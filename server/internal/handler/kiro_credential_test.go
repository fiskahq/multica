package handler

import (
	"encoding/json"
	"fmt"
	"net/http"
	"testing"

	"github.com/multica-ai/multica/server/internal/testutil"
	"github.com/multica-ai/multica/server/internal/util"
	"github.com/multica-ai/multica/server/pkg/dbid"
)

func TestKiroCredentialBatchClaimsPreserveRoles(t *testing.T) {
	if testHandler == nil {
		t.Fatal("managed PostgreSQL is required")
	}
	f := testutil.New(testPool, "", testUserID)
	f.WorkspaceID = f.Workspace(t, "Kiro claims", util.UUIDToString(dbid.NewV7()))
	f.Member(t, f.WorkspaceID, testUserID, "owner")
	runtime := f.Runtime(t, "Kiro", testutil.Cols{"provider": "kiro", "daemon_id": batchClaimTestDaemonID})
	claudeRuntime := f.Runtime(t, "Claude", testutil.Cols{"provider": "claude", "daemon_id": batchClaimTestDaemonID})
	f.InsertNoID(t, "kiro_credential_pool", testutil.Cols{"workspace_id": f.WorkspaceID}, "workspace_id=$1", f.WorkspaceID)
	for i := 0; i < 3; i++ {
		source := f.Agent(t, fmt.Sprintf("Credential %d", i), runtime, testutil.Cols{"custom_env": fmt.Sprintf(`{"KIRO_API_KEY":"fake-%d"}`, i)})
		f.InsertNoID(t, "kiro_credential_member", testutil.Cols{"workspace_id": f.WorkspaceID, "agent_id": source, "position": i}, "workspace_id=$1 AND agent_id=$2", f.WorkspaceID, source)
	}
	roles := map[string]string{}
	for _, name := range []string{"development-qa", "development-builder", "development-release", "Guy"} {
		rt := runtime
		if name == "Guy" {
			rt = claudeRuntime
		}
		role := f.Agent(t, name, rt, testutil.Cols{"instructions": "Preserve role: " + name, "custom_env": `{"KEEP":"yes","KIRO_API_KEY":"old-fixed-key"}`})
		roles[role] = name
		issue := f.Issue(t, name, testutil.Cols{"status": "in_progress"})
		f.Task(t, role, testutil.Cols{"runtime_id": rt, "issue_id": issue, "originator_user_id": testUserID, "accountable_user_id": testUserID, "originator_source": "human"})
	}
	t.Cleanup(func() {
		f.Exec(t, "DELETE FROM kiro_credential_assignment WHERE workspace_id=$1", f.WorkspaceID)
		f.Exec(t, "DELETE FROM task_token WHERE workspace_id=$1", f.WorkspaceID)
	})
	req := newOwnerDaemonRequest(http.MethodPost, "/api/daemon/tasks/claim", map[string]any{"daemon_id": batchClaimTestDaemonID, "runtime_ids": []string{runtime, claudeRuntime}, "max_tasks": 4})
	var response struct {
		Tasks []AgentTaskResponse `json:"tasks"`
	}
	testutil.Call(t, testHandler.ClaimTasksByRuntime, req).Want(http.StatusOK).JSON(&response)
	if len(response.Tasks) != 4 {
		b, _ := json.Marshal(response)
		t.Fatalf("expected four claims: %s", b)
	}
	keys := map[string]bool{}
	for _, task := range response.Tasks {
		name := roles[task.AgentID]
		if task.Agent == nil || task.Agent.Name != name || task.Agent.Instructions != "Preserve role: "+name || task.Agent.CustomEnv["KEEP"] != "yes" {
			t.Fatalf("role context changed: %#v", task.Agent)
		}
		key := task.Agent.CustomEnv["KIRO_API_KEY"]
		if name == "Guy" {
			if key != "old-fixed-key" {
				t.Fatal("Claude credentials changed")
			}
		} else {
			keys[key] = true
		}
	}
	for i := 0; i < 3; i++ {
		if !keys[fmt.Sprintf("fake-%d", i)] {
			t.Fatalf("role agents did not rotate: %v", keys)
		}
	}
}
