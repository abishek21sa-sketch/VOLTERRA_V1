// Command api is VOLTERRA's API gateway: serves the warehouse read-only to the frontend,
// orchestrates Julia/Python jobs over the event bus, and hosts the copilot tool-calling loop.
//
// Phase 2 status: GET /api/sites is real, reading warehouse/volterra.duckdb via the duckdb CLI
// (see internal/warehouse). Phase 6 added GET /api/demand, reading ml/'s precomputed queue-risk
// predictions from a plain JSON file, not the warehouse (see internal/mlpredictions doc comment
// for why). Phase 11 added POST /api/copilot, the Network Operations Copilot tool-calling loop —
// see internal/copilot's doc comment for its design and what has/hasn't been verified end-to-end
// (no ANTHROPIC_API_KEY is configured in this dev environment). The copilot's network_resilience
// tool queries a real, live Memgraph instance (internal/graphdb) — the real target architecture
// Phase 5 originally called for, built once Docker was confirmed working again; see
// internal/graphdb's doc comment and cmd/loadgraph. Full async job orchestration over Redpanda
// (scaffolded in ../../docker-compose.yml) remains a real future upgrade, not yet wired.
package main

import (
	"fmt"
	"log"
	"os"
	"time"

	"github.com/gofiber/fiber/v2"
	"github.com/gofiber/fiber/v2/middleware/cors"

	"github.com/abishek/volterra/backend/internal/copilot"
	"github.com/abishek/volterra/backend/internal/graphdb"
	"github.com/abishek/volterra/backend/internal/mlpredictions"
	"github.com/abishek/volterra/backend/internal/warehouse"
)

// memgraphURI is read once by both /api/copilot's setup and, if this process ever grows a second
// Memgraph-backed route, would be shared the same way — hence a package-level default rather than
// buried inside buildCopilotConfig.
func memgraphURI() string {
	uri := os.Getenv("VOLTERRA_MEMGRAPH_URI")
	if uri == "" {
		uri = "bolt://localhost:7687" // dev default -- docker-compose.yml maps this to the host
	}
	return uri
}

func main() {
	dbPath := os.Getenv("VOLTERRA_WAREHOUSE_PATH")
	if dbPath == "" {
		dbPath = "../warehouse/volterra.duckdb" // dev default: run from backend/
	}

	demandPredictionsPath := os.Getenv("VOLTERRA_QUEUE_RISK_PREDICTIONS_PATH")
	if demandPredictionsPath == "" {
		demandPredictionsPath = "../ml/data/queue_risk_predictions.json" // dev default: run from backend/
	}

	copilotCfg := buildCopilotConfig(dbPath)

	app := fiber.New()
	app.Use(func(c *fiber.Ctx) error {
		requestID := c.Get("X-Request-ID")
		if requestID == "" {
			requestID = fmt.Sprintf("volterra-%d", time.Now().UnixNano())
		}
		c.Set("X-Request-ID", requestID)
		c.Set("X-Decision-Execution", "HUMAN_GATED")
		c.Set("Cache-Control", "no-store")
		return c.Next()
	})
	// Dev-only default: the Angular dev server (localhost:4200) is cross-origin from the API
	// (localhost:4200). The origin is configurable and defaults to the local UI only.
	allowedOrigin := os.Getenv("VOLTERRA_CORS_ORIGIN")
	if allowedOrigin == "" {
		allowedOrigin = "http://localhost:4200"
	}
	app.Use(cors.New(cors.Config{AllowOrigins: allowedOrigin, AllowMethods: "GET,POST,OPTIONS", AllowHeaders: "Origin, Content-Type, Accept, X-Request-ID"}))

	app.Get("/health", func(c *fiber.Ctx) error {
		return c.JSON(fiber.Map{"status": "ok", "service": "volterra-api"})
	})

	app.Get("/api/governance/signature", func(c *fiber.Ctx) error {
		return c.JSON(fiber.Map{
			"status":               "HUMAN_GATED_REFERENCE",
			"signature_algorithm":  "GRIDWEAVE-v1",
			"implementation":       "tenx/signature_algorithm.py",
			"objective":            "minimize topology cost subject to demand coverage and N-1 criticality",
			"counterfactual":       "critical-node N-1 ablation",
			"evidence_artifact":    "artifacts/fortune50_capability_benchmark.json",
			"autonomous_execution": false,
		})
	})

	app.Get("/api/sites", func(c *fiber.Ctx) error {
		sites, err := warehouse.QuerySites(dbPath)
		if err != nil {
			log.Printf("QuerySites error: %v", err)
			return c.Status(fiber.StatusInternalServerError).JSON(fiber.Map{"error": "failed to query warehouse"})
		}
		return c.JSON(sites)
	})

	app.Get("/api/demand", func(c *fiber.Ctx) error {
		predictions, err := mlpredictions.Load(demandPredictionsPath)
		if err != nil {
			log.Printf("mlpredictions.Load error: %v", err)
			return c.Status(fiber.StatusInternalServerError).JSON(fiber.Map{"error": "failed to load queue-risk predictions"})
		}
		return c.JSON(predictions)
	})

	app.Post("/api/copilot", func(c *fiber.Ctx) error {
		var req struct {
			Tier    string `json:"tier"`
			Message string `json:"message"`
		}
		if err := c.BodyParser(&req); err != nil {
			return c.Status(fiber.StatusBadRequest).JSON(fiber.Map{"error": "invalid request body"})
		}
		if req.Message == "" {
			return c.Status(fiber.StatusBadRequest).JSON(fiber.Map{"error": "message must not be empty"})
		}
		if req.Tier == "" {
			req.Tier = copilot.TierPublic
		}
		geminiKey := os.Getenv("GEMINI_API_KEY")
		if geminiKey == "" { geminiKey = os.Getenv("GOOGLE_API_KEY") }
		if geminiKey != "" {
			answer, err := copilot.AskGemini(c.Context(), geminiKey, copilotSystemPrompt+"\nUse deterministic network evidence as authoritative and never invent site telemetry.", req.Message)
			if err == nil { return c.JSON(fiber.Map{"answer": answer, "provider": "Google Gemini", "model": copilot.GeminiModel(), "fallback": false}) }
			log.Printf("AskGemini error, using deterministic fallback: %v", err)
			return c.JSON(fiber.Map{"answer": copilot.DeterministicAnswer(req.Message), "provider": "deterministic-fallback", "model": "rule-based", "fallback": true})
		}
		if copilotCfg.Client.APIKey == "" {
			return c.JSON(fiber.Map{"answer": copilot.DeterministicAnswer(req.Message), "provider": "deterministic", "model": "rule-based", "fallback": true})
		}

		answer, err := copilotCfg.AskCopilot(c.Context(), req.Tier, req.Message)
		if err != nil {
			log.Printf("AskCopilot error: %v", err)
			return c.Status(fiber.StatusInternalServerError).JSON(fiber.Map{"error": "copilot request failed"})
		}
		return c.JSON(fiber.Map{"answer": answer})
	})
	app.Get("/api/copilot/status", func(c *fiber.Ctx) error {
		return c.JSON(fiber.Map{"provider": "Google Gemini", "model": copilot.GeminiModel(), "gemini_configured": os.Getenv("GEMINI_API_KEY") != "" || os.Getenv("GOOGLE_API_KEY") != "", "anthropic_configured": copilotCfg.Client.APIKey != "", "deterministic_fallback": true, "claim_boundary": "Network predictions are scenario screening; chat cannot authorize operations."})
	})

	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}
	log.Fatal(app.Listen(":" + port))
}

const copilotSystemPrompt = `You are the VOLTERRA Network Operations Copilot, answering questions about a real
EV charging network and grid decision-intelligence platform built over real Tesla Supercharger
data plus authoritative public datasets (NREL, FHWA, EIA, NOAA, EPA).

Always use your tools to answer questions about specific real sites, network structure, or
queue-risk predictions rather than guessing or relying on general knowledge -- you have no memory
of this network's actual data without calling a tool. When a tool's output is explicitly labeled
"modeled" or comes from a surrogate/predictive model rather than a direct real measurement, say so
plainly in your answer -- never present a modeled or predicted figure as if it were an observed
fact. If a question needs a real site name, resolve it via a tool rather than assuming spelling or
formatting. Report metrics separately when a tool returns several (e.g. degree, betweenness, and
criticality) -- never invent a single blended score.`

func buildCopilotConfig(dbPath string) *copilot.Config {
	apiKey := os.Getenv("ANTHROPIC_API_KEY")
	publicModel := os.Getenv("VOLTERRA_CLAUDE_MODEL_PUBLIC")
	if publicModel == "" {
		publicModel = "claude-haiku-4-5"
	}
	researcherModel := os.Getenv("VOLTERRA_CLAUDE_MODEL_RESEARCHER")
	if researcherModel == "" {
		researcherModel = "claude-sonnet-4-5"
	}

	pythonExecutable := os.Getenv("VOLTERRA_ML_PYTHON")
	if pythonExecutable == "" {
		pythonExecutable = "../ml/.venv/Scripts/python.exe"
	}
	predictOneScript := os.Getenv("VOLTERRA_PREDICT_ONE_SCRIPT")
	if predictOneScript == "" {
		predictOneScript = "../ml/examples/predict_one.py"
	}

	// graphdb.NewClient doesn't itself connect (the Bolt driver connects lazily on first real
	// query) -- constructing it here always succeeds even if Memgraph isn't actually running yet;
	// a query against it will surface a clear connection error at call time instead. See
	// internal/graphdb's doc comment and cmd/loadgraph for loading the real graph into Memgraph.
	graphClient, err := graphdb.NewClient(memgraphURI())
	if err != nil {
		log.Fatalf("constructing Memgraph client: %v", err)
	}

	tools := copilot.NewTools(copilot.ToolsConfig{
		WarehousePath:    dbPath,
		GraphDBClient:    graphClient,
		PythonExecutable: pythonExecutable,
		PredictOneScript: predictOneScript,
	})

	return &copilot.Config{
		Client:          copilot.NewClient(apiKey),
		PublicModel:     publicModel,
		ResearcherModel: researcherModel,
		SystemPrompt:    copilotSystemPrompt,
		Tools:           tools,
	}
}
