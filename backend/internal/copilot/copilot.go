// copilot.go: the tool-calling loop, tiered model selection, and the forced-final-answer
// discipline — mirroring AirlinesApp's api/copilot.py pattern (this project's own sibling
// portfolio project, cited directly per the root CLAUDE.md's instruction to mirror it) over
// VOLTERRA's own real query functions as tools.
//
// **Tiered models**: `tier` selects `PublicModel` (cheaper/faster, for the public-facing site
// mode) or `ResearcherModel` (for the researcher-facing Decision-Center-equivalent mode) — same
// tools, same system prompt, only the model differs, matching AirlinesApp's exact convention.
//
// **Forced-final-answer discipline**: if MaxHops is reached but the model still wants to call a
// tool, the final request is made with ToolChoice={"type":"none"} — and, non-negotiably,
// Thinking={"type":"disabled"} alongside it. See anthropic.go's Thinking doc comment for the
// specific documented failure mode (silently returning zero visible text) this combination
// prevents, and the root CLAUDE.md for where this rule is stated as a cross-cutting instruction
// for this exact codebase.
//
// **What has and hasn't been verified here.** The tool-calling LOOP LOGIC — parsing tool_use
// blocks, executing the matched Go tool, appending tool_result blocks, looping until stop_reason
// isn't "tool_use", and the forced-final-answer fallback — is verified by copilot_test.go against
// an httptest server that plays back canned Anthropic API response shapes. Whether a REAL Claude
// model actually picks sensible tools for a natural-language VOLTERRA question has NOT been
// verified end-to-end: this dev environment has no ANTHROPIC_API_KEY configured, so no live call
// to the real API has been made. Set ANTHROPIC_API_KEY and try a real request against
// POST /api/copilot to close that gap — see backend/README.md.
package copilot

import (
	"context"
	"encoding/json"
	"fmt"
)

const (
	TierPublic     = "public"
	TierResearcher = "researcher"

	defaultMaxTokens = 1024
	defaultMaxHops   = 6
)

// Tool pairs an API-visible definition with the real Go function that actually executes it.
// Execute receives the raw JSON the model supplied as input and returns the text to send back as
// that tool_use block's result (or an error, reported to the model as an is_error tool_result
// rather than crashing the whole conversation — a malformed or failed tool call is normal
// real-world model behavior to recover from, not a bug in this loop).
type Tool struct {
	Definition ToolDefinition
	Execute    func(ctx context.Context, input json.RawMessage) (string, error)
}

// Config bundles what AskCopilot needs beyond the per-call tier/prompt/message — model names,
// tool set, and loop limits, so callers (main.go's route handler) build this once at startup.
type Config struct {
	Client          *Client
	PublicModel     string
	ResearcherModel string
	SystemPrompt    string
	Tools           []Tool
	MaxTokens       int // 0 -> defaultMaxTokens
	MaxHops         int // 0 -> defaultMaxHops
}

func (cfg *Config) modelForTier(tier string) (string, error) {
	switch tier {
	case TierPublic:
		return cfg.PublicModel, nil
	case TierResearcher:
		return cfg.ResearcherModel, nil
	default:
		return "", fmt.Errorf("unknown tier %q (expected %q or %q)", tier, TierPublic, TierResearcher)
	}
}

// AskCopilot runs the full tool-calling loop for one user message and returns the final visible
// text answer.
func (cfg *Config) AskCopilot(ctx context.Context, tier string, userMessage string) (string, error) {
	model, err := cfg.modelForTier(tier)
	if err != nil {
		return "", err
	}

	maxTokens := cfg.MaxTokens
	if maxTokens == 0 {
		maxTokens = defaultMaxTokens
	}
	maxHops := cfg.MaxHops
	if maxHops == 0 {
		maxHops = defaultMaxHops
	}

	toolDefs := make([]ToolDefinition, len(cfg.Tools))
	toolByName := make(map[string]Tool, len(cfg.Tools))
	for i, t := range cfg.Tools {
		toolDefs[i] = t.Definition
		toolByName[t.Definition.Name] = t
	}

	messages := []Message{{Role: "user", Content: []ContentBlock{{Type: "text", Text: userMessage}}}}

	for hop := 0; hop < maxHops; hop++ {
		resp, err := cfg.Client.createMessage(ctx, createMessageParams{
			Model: model, MaxTokens: maxTokens, System: cfg.SystemPrompt,
			Messages: messages, Tools: toolDefs,
		})
		if err != nil {
			return "", fmt.Errorf("hop %d: %w", hop, err)
		}

		messages = append(messages, Message{Role: "assistant", Content: resp.Content})

		if resp.StopReason != "tool_use" {
			return extractText(resp.Content), nil
		}

		toolResults := executeToolCalls(ctx, resp.Content, toolByName)
		messages = append(messages, Message{Role: "user", Content: toolResults})
	}

	// max_hops reached and the model still wants tools -- force a final answer. See this file's
	// doc comment and anthropic.go's Thinking doc comment for why both ToolChoice="none" and
	// Thinking="disabled" are required together here, not just ToolChoice.
	resp, err := cfg.Client.createMessage(ctx, createMessageParams{
		Model: model, MaxTokens: maxTokens, System: cfg.SystemPrompt, Messages: messages,
		ToolChoice: &ToolChoice{Type: "none"},
		Thinking:   &Thinking{Type: "disabled"},
	})
	if err != nil {
		return "", fmt.Errorf("forced final answer: %w", err)
	}
	return extractText(resp.Content), nil
}

func executeToolCalls(ctx context.Context, blocks []ContentBlock, toolByName map[string]Tool) []ContentBlock {
	var results []ContentBlock
	for _, block := range blocks {
		if block.Type != "tool_use" {
			continue
		}
		tool, ok := toolByName[block.Name]
		if !ok {
			results = append(results, ContentBlock{
				Type: "tool_result", ToolUseID: block.ID, IsError: true,
				Content: fmt.Sprintf("unknown tool: %s", block.Name),
			})
			continue
		}
		out, err := tool.Execute(ctx, block.Input)
		if err != nil {
			results = append(results, ContentBlock{
				Type: "tool_result", ToolUseID: block.ID, IsError: true, Content: err.Error(),
			})
			continue
		}
		results = append(results, ContentBlock{Type: "tool_result", ToolUseID: block.ID, Content: out})
	}
	return results
}

func extractText(blocks []ContentBlock) string {
	var text string
	for _, b := range blocks {
		if b.Type == "text" {
			text += b.Text
		}
	}
	return text
}
