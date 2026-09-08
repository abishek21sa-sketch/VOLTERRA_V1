package copilot

// Gemini support is deliberately a small narration boundary. The deterministic answer remains
// available without credentials, while the existing Anthropic path continues to provide the
// full real-tool loop when ANTHROPIC_API_KEY is configured.
import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"
)

const defaultGeminiModel = "gemini-2.5-flash"

type geminiRequest struct {
	SystemInstruction map[string]any `json:"system_instruction"`
	Contents          []map[string]any `json:"contents"`
	GenerationConfig  map[string]any `json:"generationConfig"`
}

type geminiResponse struct {
	Candidates []struct {
		Content struct { Parts []struct { Text string `json:"text"` } `json:"parts"` } `json:"content"`
	} `json:"candidates"`
}

func GeminiModel() string {
	if model := strings.TrimSpace(os.Getenv("GEMINI_MODEL")); model != "" { return model }
	return defaultGeminiModel
}

func AskGemini(ctx context.Context, apiKey, system, message string) (string, error) {
	if strings.TrimSpace(apiKey) == "" { return "", fmt.Errorf("GEMINI_API_KEY is not configured") }
	body, err := json.Marshal(geminiRequest{
		SystemInstruction: map[string]any{"parts": []map[string]string{{"text": system}}},
		Contents: []map[string]any{{"role": "user", "parts": []map[string]string{{"text": message}}}},
		GenerationConfig: map[string]any{"temperature": 0, "seed": 42, "maxOutputTokens": 700},
	})
	if err != nil { return "", fmt.Errorf("marshal Gemini request: %w", err) }
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, "https://generativelanguage.googleapis.com/v1beta/models/"+GeminiModel()+":generateContent", bytes.NewReader(body))
	if err != nil { return "", fmt.Errorf("build Gemini request: %w", err) }
	req.Header.Set("Content-Type", "application/json"); req.Header.Set("x-goog-api-key", apiKey)
	client := &http.Client{Timeout: 25 * time.Second}
	resp, err := client.Do(req)
	if err != nil { return "", fmt.Errorf("call Gemini: %w", err) }
	defer resp.Body.Close()
	raw, err := io.ReadAll(resp.Body)
	if err != nil { return "", fmt.Errorf("read Gemini response: %w", err) }
	if resp.StatusCode < 200 || resp.StatusCode >= 300 { return "", fmt.Errorf("Gemini returned HTTP %d", resp.StatusCode) }
	var parsed geminiResponse
	if err := json.Unmarshal(raw, &parsed); err != nil { return "", fmt.Errorf("decode Gemini response: %w", err) }
	var out strings.Builder
	if len(parsed.Candidates) > 0 { for _, part := range parsed.Candidates[0].Content.Parts { out.WriteString(part.Text) } }
	if strings.TrimSpace(out.String()) == "" { return "", fmt.Errorf("Gemini returned no visible text") }
	return strings.TrimSpace(out.String()), nil
}

func DeterministicAnswer(message string) string {
	q := strings.ToLower(message); next := "Start with the network map, inspect the modeled queue-risk layer, and keep any operating or capital action human-gated."
	if strings.Contains(q, "queue") || strings.Contains(q, "wait") { next = "Choose a utilization scenario, inspect queue-risk predictions, then verify the site and weather provenance before escalation." }
	if strings.Contains(q, "site") || strings.Contains(q, "station") { next = "Open the site dossier and separate observed public station attributes from modeled queue-risk predictions." }
	if strings.Contains(q, "resilien") || strings.Contains(q, "network") { next = "Review network topology and resilience evidence, then treat any modeled prediction as scenario screening rather than observed demand." }
	return fmt.Sprintf("Deterministic VOLTERRA network readout\n\nQuestion: %s\n\nRecommended path: %s\nBoundary: this is review-only decision support; no charger, grid, or capital action is authorized by chat.", strings.TrimSpace(message), next)
}
