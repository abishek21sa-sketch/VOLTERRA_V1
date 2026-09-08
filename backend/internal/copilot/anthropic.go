// Package copilot implements the Network Operations Copilot (Phase 11): a tool-calling loop
// against Claude, wired to VOLTERRA's own real query functions as tools, mirroring AirlinesApp's
// api/copilot.py pattern (tiered models, forced-final-answer discipline) — see copilot.go's doc
// comment for the full design and the honest caveat about what has and hasn't been verified
// end-to-end in this environment.
//
// anthropic.go is a raw net/http client against the Messages API — no third-party SDK. That's a
// deliberate choice, not an oversight: this backend has exactly one external dependency so far
// (Fiber), and the Messages API surface this copilot actually needs (messages, tools, tool
// results, the thinking/tool_choice combination the forced-final-answer path depends on) is small
// and stable enough that hand-rolling it keeps the whole request/response shape auditable in one
// file, with no supply-chain trust question about a third-party module this session can't fully
// vet.
package copilot

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

const anthropicAPIURL = "https://api.anthropic.com/v1/messages"
const anthropicVersion = "2023-06-01"

// ContentBlock covers every block shape this copilot sends or receives: assistant text and
// tool_use blocks in responses, and user text and tool_result blocks in requests. Fields not
// relevant to a given Type are simply left zero-valued and omitted from the JSON (`omitempty`) —
// one struct is simpler and just as correct as a tagged union for this small, fixed set of shapes.
type ContentBlock struct {
	Type string `json:"type"`

	// type=text (request and response)
	Text string `json:"text,omitempty"`

	// type=tool_use (response only)
	ID    string          `json:"id,omitempty"`
	Name  string          `json:"name,omitempty"`
	Input json.RawMessage `json:"input,omitempty"`

	// type=tool_result (request only)
	ToolUseID string `json:"tool_use_id,omitempty"`
	Content   string `json:"content,omitempty"`
	IsError   bool   `json:"is_error,omitempty"`
}

// Message is one turn in the conversation — always the content-block-array form (never the plain
// string shorthand the API also accepts), so this client only has one shape to marshal.
type Message struct {
	Role    string         `json:"role"` // "user" or "assistant"
	Content []ContentBlock `json:"content"`
}

// ToolDefinition is what the API needs to know about one callable tool — see Tool in copilot.go
// for the paired Go-side executable.
type ToolDefinition struct {
	Name        string          `json:"name"`
	Description string          `json:"description"`
	InputSchema json.RawMessage `json:"input_schema"`
}

// ToolChoice, when set to {"type": "none"}, forbids the model from calling any tool on that turn
// — used only on the forced-final-answer path (see copilot.go).
type ToolChoice struct {
	Type string `json:"type"`
}

// Thinking controls extended thinking. **Must be {"type": "disabled"} whenever ToolChoice is set
// to "none"** — confirmed in a sibling project's production copilot (AirlinesApp's api/copilot.py)
// that leaving thinking enabled on a forced-final-answer turn can let the model end its turn
// (stop_reason=end_turn, not truncated) having produced only a thinking block and zero visible
// text, silently returning nothing. This is state-dependent, not per-call-random — identical
// retries reproduce the identical empty result — so CreateMessage always sets both together on
// that path rather than leaving Thinking unset and hoping the API defaults cooperate.
type Thinking struct {
	Type string `json:"type"`
}

type createMessageRequest struct {
	Model      string           `json:"model"`
	MaxTokens  int              `json:"max_tokens"`
	System     string           `json:"system,omitempty"`
	Messages   []Message        `json:"messages"`
	Tools      []ToolDefinition `json:"tools,omitempty"`
	ToolChoice *ToolChoice      `json:"tool_choice,omitempty"`
	Thinking   *Thinking        `json:"thinking,omitempty"`
}

// CreateMessageResponse is the subset of the Messages API response this copilot uses.
type CreateMessageResponse struct {
	ID         string         `json:"id"`
	StopReason string         `json:"stop_reason"`
	Content    []ContentBlock `json:"content"`
}

type anthropicErrorResponse struct {
	Error struct {
		Type    string `json:"type"`
		Message string `json:"message"`
	} `json:"error"`
}

// Client is a minimal Anthropic Messages API client. BaseURL and HTTPClient are overridable so
// tests can point this at an httptest server instead of the real API — see copilot_test.go.
type Client struct {
	APIKey     string
	BaseURL    string
	HTTPClient *http.Client
}

func NewClient(apiKey string) *Client {
	return &Client{
		APIKey:     apiKey,
		BaseURL:    anthropicAPIURL,
		HTTPClient: &http.Client{Timeout: 60 * time.Second},
	}
}

type createMessageParams struct {
	Model      string
	MaxTokens  int
	System     string
	Messages   []Message
	Tools      []ToolDefinition
	ToolChoice *ToolChoice
	Thinking   *Thinking
}

func (c *Client) createMessage(ctx context.Context, p createMessageParams) (*CreateMessageResponse, error) {
	reqBody, err := json.Marshal(createMessageRequest{
		Model: p.Model, MaxTokens: p.MaxTokens, System: p.System, Messages: p.Messages,
		Tools: p.Tools, ToolChoice: p.ToolChoice, Thinking: p.Thinking,
	})
	if err != nil {
		return nil, fmt.Errorf("marshaling request: %w", err)
	}

	baseURL := c.BaseURL
	if baseURL == "" {
		baseURL = anthropicAPIURL
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, baseURL, bytes.NewReader(reqBody))
	if err != nil {
		return nil, fmt.Errorf("building request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("x-api-key", c.APIKey)
	req.Header.Set("anthropic-version", anthropicVersion)

	httpClient := c.HTTPClient
	if httpClient == nil {
		httpClient = http.DefaultClient
	}
	resp, err := httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("calling Anthropic API: %w", err)
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("reading response body: %w", err)
	}

	if resp.StatusCode != http.StatusOK {
		var apiErr anthropicErrorResponse
		if json.Unmarshal(respBody, &apiErr) == nil && apiErr.Error.Message != "" {
			return nil, fmt.Errorf("anthropic API error (%s): %s", apiErr.Error.Type, apiErr.Error.Message)
		}
		return nil, fmt.Errorf("anthropic API returned status %d: %s", resp.StatusCode, respBody)
	}

	var result CreateMessageResponse
	if err := json.Unmarshal(respBody, &result); err != nil {
		return nil, fmt.Errorf("parsing response: %w", err)
	}
	return &result, nil
}
