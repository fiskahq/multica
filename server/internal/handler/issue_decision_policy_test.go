package handler

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"github.com/multica-ai/multica/server/internal/testutil"
	db "github.com/multica-ai/multica/server/pkg/db/generated"
)

func TestParseDecisionCommand(t *testing.T) {
	for _, tc := range []struct {
		text, mode, target string
		bad                bool
	}{
		{"/decisions human", "human", "", false},
		{"  /DECISIONS auto\n", "auto", "", false},
		{"/decisions @Architect", "agent", "Architect", false},
		{"/decisions @Senior Engineer", "agent", "Senior Engineer", false},
		{"/decisions [@Architect](mention://agent/030b99b4-c4b3-4484-84f2-f71d461f0fea)", "agent", "030b99b4-c4b3-4484-84f2-f71d461f0fea", false},
		{"Please use /decisions auto", "", "", false},
		{"> /decisions auto", "", "", false},
		{"```\n/decisions auto\n```", "", "", false},
		{"/decisions", "", "", true}, {"/decisions @", "", "", true},
		{"/decisions auto and deploy", "", "", true},
		{"/decisions @Architect\nextra instructions", "", "", true},
	} {
		t.Run(tc.text, func(t *testing.T) {
			mode, target, err := parseDecisionCommand(tc.text)
			if (err != nil) != tc.bad || mode != tc.mode || target != tc.target {
				t.Fatalf("got %q %q %v", mode, target, err)
			}
		})
	}
}

func decisionFixture(t *testing.T) string {
	t.Helper()
	if testPool == nil {
		t.Skip("database not available")
	}
	id := dbfx.Issue(t, "Decision policy "+t.Name())
	dbfx.Cleanup(t, "DELETE FROM comment WHERE issue_id = $1", id)
	return id
}

func readDecisionPolicy(t *testing.T, id string) DecisionPolicy {
	t.Helper()
	var response IssueResponse
	req := withURLParam(newRequest("GET", "/api/issues/"+id, nil), "id", id)
	testutil.Call(t, testHandler.GetIssue, req).Want(http.StatusOK).JSON(&response)
	if response.DecisionPolicy == nil {
		t.Fatal("decision policy missing")
	}
	return *response.DecisionPolicy
}

func decisionCommand(t *testing.T, id, content string, status int, agentID string) CommentResponse {
	t.Helper()
	req := withURLParam(newRequest("POST", "/api/issues/"+id+"/comments", map[string]any{"content": content}), "id", id)
	if agentID != "" {
		req.Header.Set("X-Actor-Source", "task_token")
		req.Header.Set("X-Agent-ID", agentID)
	}
	var response CommentResponse
	call := testutil.Call(t, testHandler.CreateComment, req).Want(status)
	if status == http.StatusCreated {
		call.JSON(&response)
	}
	return response
}

func TestDecisionPolicyInheritanceAndRevocation(t *testing.T) {
	root := decisionFixture(t)
	child := dbfx.Issue(t, "Decision policy child", testutil.Cols{"parent_issue_id": root})
	if got := readDecisionPolicy(t, child); got.Mode != "human" || got.RootIssueID != root {
		t.Fatalf("default: %+v", got)
	}
	command := decisionCommand(t, root, "/decisions auto", http.StatusCreated, "")
	got := readDecisionPolicy(t, child)
	if got.Mode != "auto" || got.CommentID != command.ID || got.ConfiguredBy != testUserID {
		t.Fatalf("inherited: %+v", got)
	}
	decisionCommand(t, child, "/decisions auto", http.StatusConflict, "")
	decisionCommand(t, root, "/decisions @missing-agent", http.StatusBadRequest, "")
	if got := readDecisionPolicy(t, child); got.CommentID != command.ID {
		t.Fatal("invalid command changed policy")
	}
	decisionCommand(t, root, "/decisions human", http.StatusCreated, "")
	if got := readDecisionPolicy(t, child); got.Mode != "human" {
		t.Fatalf("not revoked: %+v", got)
	}
	// The claim path must inherit even when the child has no named workflow.
	issue, err := testHandler.Queries.GetIssue(context.Background(), parseUUID(child))
	if err != nil {
		t.Fatal(err)
	}
	brief := testHandler.claimProjectWorkflow(context.Background(), issue, nil, false)
	if brief == nil || !strings.Contains(brief.Brief, "Decision mode: human") || !strings.Contains(brief.Brief, root) {
		t.Fatalf("missing inherited brief: %+v", brief)
	}
}

func TestDecisionPolicyAgentAndMemberAuthorization(t *testing.T) {
	root := decisionFixture(t)
	runtime := dbfx.Runtime(t, "Decision runtime")
	agent := dbfx.Agent(t, "Decision Architect", runtime, testutil.Cols{"owner_id": testUserID})
	decisionCommand(t, root, "/decisions auto", http.StatusForbidden, agent)
	stranger := dbfx.User(t, "Decision stranger", "decision-stranger-"+root+"@example.test")
	dbfx.Member(t, testWorkspaceID, stranger, "member")
	req := withURLParam(newRequest("POST", "/api/issues/"+root+"/comments", map[string]any{"content": "/decisions auto"}), "id", root)
	req.Header.Set("X-User-ID", stranger)
	testutil.Call(t, testHandler.CreateComment, req).Want(http.StatusForbidden)
	if got := readDecisionPolicy(t, root); got.Mode != "human" || got.CommentID != "" {
		t.Fatalf("unauthorized policy: %+v", got)
	}
	decisionCommand(t, root, "/decisions @Decision Architect", http.StatusCreated, "")
	got := readDecisionPolicy(t, root)
	if got.Mode != "agent" || got.AgentID != agent || !got.Available {
		t.Fatalf("agent selection: %+v", got)
	}
	if n := dbfx.Count(t, "SELECT count(*) FROM agent_task_queue WHERE issue_id = $1", root); n != 0 {
		t.Fatal("policy command invoked agent")
	}
	dbfx.Agent(t, "Private decision agent", runtime, testutil.Cols{"owner_id": stranger})
	decisionCommand(t, root, "/decisions @Private decision agent", http.StatusBadRequest, "")
	if updated := readDecisionPolicy(t, root); updated.CommentID != got.CommentID {
		t.Fatal("unauthorized agent changed policy")
	}
	dbfx.Exec(t, "UPDATE agent SET archived_at = now() WHERE id = $1", agent)
	if got := readDecisionPolicy(t, root); got.Mode != "agent" || got.Available {
		t.Fatalf("unavailable agent silently changed mode: %+v", got)
	}
}

func TestDecisionPolicyCommandAuditAndEditBypass(t *testing.T) {
	root := decisionFixture(t)
	command := decisionCommand(t, root, "/decisions auto", http.StatusCreated, "")
	req := withURLParam(newRequest("PATCH", "/api/comments/"+command.ID, map[string]any{"content": "ordinary comment"}), "commentId", command.ID)
	testutil.Call(t, testHandler.UpdateComment, req).Want(http.StatusConflict)
	req = withURLParam(newRequest("DELETE", "/api/comments/"+command.ID, nil), "commentId", command.ID)
	testutil.Call(t, testHandler.DeleteComment, req).Want(http.StatusConflict)
	plain := dbfx.Comment(t, root, "ordinary comment")
	req = withURLParam(newRequest("PATCH", "/api/comments/"+plain, map[string]any{"content": "/decisions human"}), "commentId", plain)
	testutil.Call(t, testHandler.UpdateComment, req).Want(http.StatusConflict)
	if got := readDecisionPolicy(t, root); got.CommentID != command.ID {
		t.Fatal("edit/delete changed policy")
	}
}

func TestDecisionPolicyConcurrentCommands(t *testing.T) {
	root := decisionFixture(t)
	var wg sync.WaitGroup
	results := make(chan CommentResponse, 8)
	failures := make(chan string, 8)
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			mode := "auto"
			if i%2 == 0 {
				mode = "human"
			}
			req := withURLParam(newRequest("POST", "/api/issues/"+root+"/comments", map[string]any{"content": "/decisions " + mode}), "id", root)
			w := httptest.NewRecorder()
			testHandler.CreateComment(w, req)
			if w.Code != http.StatusCreated {
				failures <- fmt.Sprintf("%d %s", w.Code, w.Body.String())
				return
			}
			var response CommentResponse
			if err := json.Unmarshal(w.Body.Bytes(), &response); err != nil {
				failures <- err.Error()
				return
			}
			results <- response
		}(i)
	}
	wg.Wait()
	close(results)
	close(failures)
	for failure := range failures {
		t.Error(failure)
	}
	var latest CommentResponse
	for response := range results {
		if response.IssueRevision > latest.IssueRevision {
			latest = response
		}
	}
	if got := readDecisionPolicy(t, root); got.CommentID != latest.ID {
		t.Fatalf("policy %s differs from last serialized comment %s", got.CommentID, latest.ID)
	}
}

func TestDecisionPolicyAtomicRollback(t *testing.T) {
	root := decisionFixture(t)
	parent := dbfx.Issue(t, "Parent after stale root load")
	dbfx.Exec(t, "UPDATE issue SET parent_issue_id = $1 WHERE id = $2", parent, root)
	req := newRequest("POST", "/", nil)
	_, err := wakeupWrite(testHandler, req, func(q *db.Queries) (db.CreateCommentRow, error) {
		comment, err := q.CreateComment(context.Background(), db.CreateCommentParams{ID: parseUUID("1c1c223b-a1ce-4fa0-b94c-123412341234"), IssueID: parseUUID(root), WorkspaceID: parseUUID(testWorkspaceID), AuthorType: "member", AuthorID: parseUUID(testUserID), Content: "/decisions auto", Type: "comment"})
		if err != nil {
			return comment, err
		}
		_, err = q.SetIssueDecisionPolicy(context.Background(), db.SetIssueDecisionPolicyParams{IssueID: parseUUID(root), WorkspaceID: parseUUID(testWorkspaceID), Policy: []byte(`{"mode":"auto"}`)})
		return comment, err
	})
	if err == nil {
		t.Fatal("policy write to child should fail")
	}
	if n := dbfx.Count(t, "SELECT count(*) FROM comment WHERE issue_id = $1", root); n != 0 {
		t.Fatal("failed command left a comment")
	}
}

func TestDecisionPolicyPrivateRootDoesNotHidePublicChild(t *testing.T) {
	f := newPrivateProjectFixture(t)
	decisionCommand(t, f.secretIssue, "/decisions auto", http.StatusCreated, "")
	dbfx.Exec(t, "UPDATE issue SET parent_issue_id = $1 WHERE id = $2", f.secretIssue, f.publicIssue)
	var out IssueResponse
	getIssueAs(t, f.outsider, f.publicIssue).Want(http.StatusOK).JSON(&out)
	if out.DecisionPolicy != nil {
		t.Fatal("private root policy leaked through public child")
	}
	getIssueAs(t, f.insider, f.publicIssue).Want(http.StatusOK).JSON(&out)
	if out.DecisionPolicy == nil || out.DecisionPolicy.Mode != "auto" {
		t.Fatal("authorized reader lost inherited policy")
	}
	runtime := dbfx.Runtime(t, "Private decision policy runtime")
	agent := dbfx.Agent(t, "Private root policy observer", runtime, testutil.Cols{"owner_id": f.outsider})
	taskID := dbfx.Task(t, agent, testutil.Cols{"issue_id": f.publicIssue, "runtime_id": runtime, "originator_user_id": f.outsider, "accountable_user_id": f.outsider})
	task, err := testHandler.Queries.GetAgentTask(context.Background(), parseUUID(taskID))
	if err != nil {
		t.Fatal(err)
	}
	issue, err := testHandler.Queries.GetIssue(context.Background(), parseUUID(f.publicIssue))
	if err != nil {
		t.Fatal(err)
	}
	brief := testHandler.decisionPolicyBrief(context.Background(), issue, &task)
	if !strings.Contains(brief, "could not be loaded") || strings.Contains(brief, f.secretIssue) || strings.Contains(brief, "Decision mode: auto") {
		t.Fatalf("private policy leaked through claim: %s", brief)
	}
}
