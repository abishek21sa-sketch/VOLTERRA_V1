package copilot

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

// newTestClient points a Client at an httptest server that plays back the given sequence of
// responses in order (one per call to createMessage) — lets these tests verify the tool-calling
// LOOP LOGIC precisely without needing a real ANTHROPIC_API_KEY or a live model. See copilot.go's
// doc comment for what this does and doesn't verify.
func newTestClient(t *testing.T, responses []CreateMessageResponse) (*Client, *[]createMessageRequest) {
	t.Helper()
	var received []createMessageRequest
	call := 0

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var req createMessageRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			t.Fatalf("decoding request body: %v", err)
		}
		received = append(received, req)

		if call >= len(responses) {
			t.Fatalf("test server received more calls (%d) than responses provided (%d)", call+1, len(responses))
		}
		resp := responses[call]
		call++

		w.Header().Set("Content-Type", "application/json")
		if err := json.NewEncoder(w).Encode(resp); err != nil {
			t.Fatalf("encoding response: %v", err)
		}
	}))
	t.Cleanup(server.Close)

	return &Client{APIKey: "test-key", BaseURL: server.URL, HTTPClient: server.Client()}, &received
}

func textResponse(stopReason, text string) CreateMessageResponse {
	return CreateMessageResponse{StopReason: stopReason, Content: []ContentBlock{{Type: "text", Text: text}}}
}

func toolUseResponse(toolUseID, toolName string, input json.RawMessage) CreateMessageResponse {
	return CreateMessageResponse{
		StopReason: "tool_use",
		Content:    []ContentBlock{{Type: "tool_use", ID: toolUseID, Name: toolName, Input: input}},
	}
}

func echoTool(name string) Tool {
	return Tool{
		Definition: ToolDefinition{Name: name, Description: "echoes its input", InputSchema: json.RawMessage(`{"type":"object"}`)},
		Execute: func(_ context.Context, input json.RawMessage) (string, error) {
			return string(input), nil
		},
	}
}

func TestAskCopilot_NoToolUse_ReturnsTextDirectly(t *testing.T) {
	client, _ := newTestClient(t, []CreateMessageResponse{
		textResponse("end_turn", "the answer is 42"),
	})
	cfg := &Config{Client: client, PublicModel: "test-model", ResearcherModel: "test-model-r", Tools: []Tool{echoTool("noop")}}

	answer, err := cfg.AskCopilot(context.Background(), TierPublic, "what is the answer?")
	if err != nil {
		t.Fatalf("AskCopilot returned error: %v", err)
	}
	if answer != "the answer is 42" {
		t.Errorf("got answer %q, want %q", answer, "the answer is 42")
	}
}

func TestAskCopilot_UnknownTier_ReturnsError(t *testing.T) {
	client, _ := newTestClient(t, nil)
	cfg := &Config{Client: client, PublicModel: "test-model", ResearcherModel: "test-model-r"}

	_, err := cfg.AskCopilot(context.Background(), "not-a-real-tier", "hello")
	if err == nil {
		t.Fatal("expected an error for an unknown tier, got nil")
	}
}

func TestAskCopilot_ExecutesToolAndReturnsFollowUpAnswer(t *testing.T) {
	toolInput := json.RawMessage(`{"query":"Las Vegas"}`)
	client, received := newTestClient(t, []CreateMessageResponse{
		toolUseResponse("tu_1", "lookup_site", toolInput),
		textResponse("end_turn", "Las Vegas has 8 real stalls."),
	})
	cfg := &Config{Client: client, PublicModel: "test-model", ResearcherModel: "test-model-r", Tools: []Tool{echoTool("lookup_site")}}

	answer, err := cfg.AskCopilot(context.Background(), TierPublic, "how many stalls does Las Vegas have?")
	if err != nil {
		t.Fatalf("AskCopilot returned error: %v", err)
	}
	if answer != "Las Vegas has 8 real stalls." {
		t.Errorf("got answer %q, want the follow-up text", answer)
	}

	// The second request must include a tool_result block carrying the tool's real output back.
	if len(*received) != 2 {
		t.Fatalf("expected 2 requests to the API, got %d", len(*received))
	}
	secondReqMessages := (*received)[1].Messages
	lastMsg := secondReqMessages[len(secondReqMessages)-1]
	if lastMsg.Role != "user" {
		t.Fatalf("expected the tool_result message to have role=user, got %q", lastMsg.Role)
	}
	found := false
	for _, block := range lastMsg.Content {
		if block.Type == "tool_result" && block.ToolUseID == "tu_1" && block.Content == string(toolInput) {
			found = true
		}
	}
	if !found {
		t.Errorf("expected a tool_result block echoing the tool's real output, got %+v", lastMsg.Content)
	}
}

func TestAskCopilot_UnknownToolName_SurfacesAsErrorResultNotCrash(t *testing.T) {
	client, received := newTestClient(t, []CreateMessageResponse{
		toolUseResponse("tu_1", "does_not_exist", json.RawMessage(`{}`)),
		textResponse("end_turn", "recovered"),
	})
	cfg := &Config{Client: client, PublicModel: "test-model", ResearcherModel: "test-model-r", Tools: []Tool{echoTool("some_real_tool")}}

	answer, err := cfg.AskCopilot(context.Background(), TierPublic, "call a tool that doesn't exist")
	if err != nil {
		t.Fatalf("AskCopilot returned error: %v", err)
	}
	if answer != "recovered" {
		t.Errorf("got answer %q, want %q", answer, "recovered")
	}

	lastMsg := (*received)[1].Messages[len((*received)[1].Messages)-1]
	block := lastMsg.Content[0]
	if !block.IsError {
		t.Errorf("expected the tool_result for an unknown tool name to be marked IsError, got %+v", block)
	}
}

func TestAskCopilot_ToolExecutionError_SurfacesAsErrorResultNotCrash(t *testing.T) {
	failingTool := Tool{
		Definition: ToolDefinition{Name: "flaky", Description: "always fails", InputSchema: json.RawMessage(`{"type":"object"}`)},
		Execute: func(_ context.Context, _ json.RawMessage) (string, error) {
			return "", errFlaky
		},
	}
	client, received := newTestClient(t, []CreateMessageResponse{
		toolUseResponse("tu_1", "flaky", json.RawMessage(`{}`)),
		textResponse("end_turn", "handled the failure"),
	})
	cfg := &Config{Client: client, PublicModel: "test-model", ResearcherModel: "test-model-r", Tools: []Tool{failingTool}}

	answer, err := cfg.AskCopilot(context.Background(), TierPublic, "trigger the flaky tool")
	if err != nil {
		t.Fatalf("AskCopilot returned error: %v", err)
	}
	if answer != "handled the failure" {
		t.Errorf("got answer %q, want %q", answer, "handled the failure")
	}

	lastMsg := (*received)[1].Messages[len((*received)[1].Messages)-1]
	if !lastMsg.Content[0].IsError {
		t.Errorf("expected a failing tool's result to be marked IsError, got %+v", lastMsg.Content[0])
	}
}

func TestAskCopilot_MaxHopsReached_ForcesFinalAnswerWithToolsDisabled(t *testing.T) {
	// The model keeps wanting to call a tool forever -- after MaxHops, the loop must force a
	// final answer with tool_choice=none AND thinking=disabled together (see copilot.go's doc
	// comment for why both, not just tool_choice).
	responses := []CreateMessageResponse{
		toolUseResponse("tu_1", "echo", json.RawMessage(`{}`)),
		toolUseResponse("tu_2", "echo", json.RawMessage(`{}`)),
		textResponse("end_turn", "finally, a real answer"),
	}
	client, received := newTestClient(t, responses)
	cfg := &Config{
		Client: client, PublicModel: "test-model", ResearcherModel: "test-model-r",
		Tools: []Tool{echoTool("echo")}, MaxHops: 2,
	}

	answer, err := cfg.AskCopilot(context.Background(), TierPublic, "keep calling tools")
	if err != nil {
		t.Fatalf("AskCopilot returned error: %v", err)
	}
	if answer != "finally, a real answer" {
		t.Errorf("got answer %q, want the forced final answer", answer)
	}

	if len(*received) != 3 {
		t.Fatalf("expected 3 requests (2 hops + 1 forced final), got %d", len(*received))
	}
	finalReq := (*received)[2]
	if finalReq.ToolChoice == nil || finalReq.ToolChoice.Type != "none" {
		t.Errorf("expected the forced-final request to set tool_choice={type:none}, got %+v", finalReq.ToolChoice)
	}
	if finalReq.Thinking == nil || finalReq.Thinking.Type != "disabled" {
		t.Errorf("expected the forced-final request to set thinking={type:disabled}, got %+v", finalReq.Thinking)
	}
}

func TestAskCopilot_TieredModelSelection(t *testing.T) {
	client, received := newTestClient(t, []CreateMessageResponse{
		textResponse("end_turn", "ok"),
		textResponse("end_turn", "ok"),
	})
	cfg := &Config{Client: client, PublicModel: "cheap-model", ResearcherModel: "expensive-model"}

	if _, err := cfg.AskCopilot(context.Background(), TierPublic, "hi"); err != nil {
		t.Fatalf("public tier: %v", err)
	}
	if _, err := cfg.AskCopilot(context.Background(), TierResearcher, "hi"); err != nil {
		t.Fatalf("researcher tier: %v", err)
	}

	if (*received)[0].Model != "cheap-model" {
		t.Errorf("public tier used model %q, want %q", (*received)[0].Model, "cheap-model")
	}
	if (*received)[1].Model != "expensive-model" {
		t.Errorf("researcher tier used model %q, want %q", (*received)[1].Model, "expensive-model")
	}
}

type flakyErr struct{}

func (flakyErr) Error() string { return "simulated tool failure" }

var errFlaky = flakyErr{}
