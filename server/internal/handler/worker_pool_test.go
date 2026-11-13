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
	"github.com/multica-ai/multica/server/internal/util"
	db "github.com/multica-ai/multica/server/pkg/db/generated"
)

type poolFixture struct {
	squadEvalFixture
	workers []string
	task    string
}

func newPoolFixture(t *testing.T) poolFixture {
	t.Helper()
	if testHandler == nil {
		t.Fatal("worker pool tests require the managed PostgreSQL environment")
	}
	fx := poolFixture{squadEvalFixture: newSquadEvalFixture(t)}
	fx.workers = []string{fx.OtherID, createHandlerTestAgent(t, "Pool second worker", nil), createHandlerTestAgent(t, "Pool third worker", nil)}
	for _, id := range fx.workers {
		dbfx.Insert(t, "squad_member", testutil.Cols{"squad_id": fx.SquadID, "member_type": "agent", "member_id": id, "role": "worker"})
		dbfx.Exec(t, `UPDATE agent SET permission_mode='private', owner_id=$2 WHERE id=$1`, id, testUserID)
		dbfx.Exec(t, `UPDATE agent_runtime SET status='online',last_seen_at=now() WHERE id=(SELECT runtime_id FROM agent WHERE id=$1)`, id)
	}
	fx.task = leaderTask(t, fx.LeaderID, fx.SquadIssueID, true, fx.SquadID)
	dbfx.Exec(t, `UPDATE agent_task_queue SET originator_user_id=$2,accountable_user_id=$2 WHERE id=$1`, fx.task, testUserID)
	req := testutil.WithURLParams(newRequest(http.MethodPut, "/pool", map[string]any{"members": []map[string]any{
		{"agent_id": fx.workers[0], "capabilities": []string{"code"}},
		{"agent_id": fx.workers[1], "capabilities": []string{"code", "review"}},
		{"agent_id": fx.workers[2], "capabilities": []string{"code"}},
	}}), "id", fx.SquadID, "pool", "kiro", "workspaceId", testWorkspaceID)
	w := httptest.NewRecorder()
	testHandler.PutWorkerPool(w, req)
	if w.Code != 200 {
		t.Fatalf("configure pool: %d %s", w.Code, w.Body.String())
	}
	t.Cleanup(func() {
		dbfx.Exec(t, `DELETE FROM worker_pool_delegation WHERE pool_id IN (SELECT id FROM worker_pool WHERE squad_id=$1)`, fx.SquadID)
		dbfx.Exec(t, `DELETE FROM worker_pool_member WHERE pool_id IN (SELECT id FROM worker_pool WHERE squad_id=$1)`, fx.SquadID)
		dbfx.Exec(t, `DELETE FROM worker_pool WHERE squad_id=$1`, fx.SquadID)
		dbfx.Exec(t, `DELETE FROM agent_task_queue WHERE issue_id=$1 AND id<>$2`, fx.SquadIssueID, fx.task)
		dbfx.Exec(t, `DELETE FROM comment WHERE issue_id=$1`, fx.SquadIssueID)
	})
	return fx
}

func (fx poolFixture) delegate(id, instructions string, caps []string) (int, poolDelegationResponse, string) {
	r := testutil.WithHeaders(testutil.WithURLParams(newRequest(http.MethodPost, "/delegate", poolDelegationRequest{Pool: "kiro", RequestID: id, Instructions: instructions, Capabilities: caps}), "id", fx.SquadIssueID, "workspaceId", testWorkspaceID), "X-Agent-ID", fx.LeaderID, "X-Task-ID", fx.task)
	w := httptest.NewRecorder()
	testHandler.DelegateWorkerPool(w, r)
	var out poolDelegationResponse
	_ = json.Unmarshal(w.Body.Bytes(), &out)
	return w.Code, out, w.Body.String()
}

func TestPoolRotationReplayAndConflict(t *testing.T) {
	fx := newPoolFixture(t)
	for i := 0; i < 7; i++ {
		status, out, body := fx.delegate(fmt.Sprint(i), "implement", nil)
		if status != 201 || out.AgentID != fx.workers[i%3] {
			t.Fatalf("turn %d: %d %s", i, status, body)
		}
		status, replay, body := fx.delegate(fmt.Sprint(i), "implement", []string{})
		if status != 200 || !replay.Replayed || replay.TaskID != out.TaskID {
			t.Fatalf("replay: %d %s", status, body)
		}
	}
	status, _, _ := fx.delegate("0", "different instructions", nil)
	if status != 409 {
		t.Fatalf("conflicting ID: %d", status)
	}
	if n := dbfx.Count(t, `SELECT count(*) FROM agent_task_queue WHERE issue_id=$1 AND agent_id<>$2`, fx.SquadIssueID, fx.LeaderID); n != 7 {
		t.Fatalf("tasks = %d", n)
	}
}

func TestPoolConcurrentDelegations(t *testing.T) {
	fx := newPoolFixture(t)
	const count = 30
	var wg sync.WaitGroup
	var mu sync.Mutex
	counts := map[string]int{}
	failures := []string{}
	for i := 0; i < count; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			status, out, body := fx.delegate(fmt.Sprint(i), "parallel work", nil)
			mu.Lock()
			defer mu.Unlock()
			if status != 201 {
				failures = append(failures, body)
			} else {
				counts[out.AgentID]++
			}
		}(i)
	}
	wg.Wait()
	if len(failures) > 0 {
		t.Fatalf("dispatch failures: %v", failures)
	}
	for _, id := range fx.workers {
		if counts[id] != count/3 {
			t.Fatalf("uneven rotation: %v", counts)
		}
	}
	var task string
	for i := 0; i < 12; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			status, out, body := fx.delegate("retry", "same work", nil)
			mu.Lock()
			defer mu.Unlock()
			if status != 200 && status != 201 {
				failures = append(failures, body)
			}
			if task != "" && task != out.TaskID {
				failures = append(failures, "duplicate task")
			}
			task = out.TaskID
		}()
	}
	wg.Wait()
	if len(failures) > 0 {
		t.Fatalf("retry failures: %v", failures)
	}
	if n := dbfx.Count(t, `SELECT count(*) FROM agent_task_queue WHERE issue_id=$1 AND agent_id<>$2`, fx.SquadIssueID, fx.LeaderID); n != count+1 {
		t.Fatalf("tasks = %d", n)
	}
}

func TestPoolEligibilityAndEnforcement(t *testing.T) {
	fx := newPoolFixture(t)
	status, out, body := fx.delegate("review", "review changes", []string{"review"})
	if status != 201 || out.AgentID != fx.workers[1] {
		t.Fatalf("capability: %d %s", status, body)
	}
	status, _, _ = fx.delegate("missing", "work", []string{"missing"})
	if status != 409 {
		t.Fatalf("no eligible worker: %d", status)
	}
	status, out, body = fx.delegate("next", "work", nil)
	if status != 201 || out.AgentID != fx.workers[2] {
		t.Fatalf("failed request consumed turn: %d %s", status, body)
	}
	ctx := context.Background()
	a, err := testHandler.Queries.GetAgent(ctx, util.MustParseUUID(fx.workers[0]))
	if err != nil {
		t.Fatal(err)
	}
	if testHandler.canInvokeAgent(ctx, a, "agent", fx.LeaderID, testUserID, testWorkspaceID) {
		t.Fatal("leader bypassed pool routing")
	}
	if !testHandler.canInvokeAgent(ctx, a, "member", testUserID, testUserID, testWorkspaceID) {
		t.Fatal("human invocation was blocked")
	}
	dbfx.Exec(t, `UPDATE worker_pool_member SET enabled=false WHERE agent_id=$1`, fx.workers[0])
	status, out, body = fx.delegate("disabled", "work", nil)
	if status != 201 || out.AgentID != fx.workers[1] {
		t.Fatalf("disabled worker: %d %s", status, body)
	}
	dbfx.Exec(t, `UPDATE worker_pool_member SET enabled=false WHERE pool_id IN (SELECT id FROM worker_pool WHERE squad_id=$1)`, fx.SquadID)
	status, _, _ = fx.delegate("none", "work", nil)
	if status != 409 {
		t.Fatalf("disabled pool: %d", status)
	}
}

func TestPoolTransactionRollback(t *testing.T) {
	fx := newPoolFixture(t)
	var poolID string
	dbfx.QueryRow(t, `SELECT id FROM worker_pool WHERE squad_id=$1`, fx.SquadID).Scan(&poolID)
	constraint := "test_pool_rollback_" + strings.ReplaceAll(poolID, "-", "")
	dbfx.Exec(t, `ALTER TABLE worker_pool_delegation ADD CONSTRAINT `+constraint+` CHECK (pool_id <> '`+poolID+`'::uuid) NOT VALID`)
	t.Cleanup(func() { dbfx.Exec(t, `ALTER TABLE worker_pool_delegation DROP CONSTRAINT IF EXISTS `+constraint) })
	status, _, _ := fx.delegate("rollback", "work", nil)
	if status != 500 {
		t.Fatalf("injected write failure: %d", status)
	}
	if n := dbfx.Count(t, `SELECT count(*) FROM comment WHERE issue_id=$1`, fx.SquadIssueID); n != 0 {
		t.Fatal("rollback left a comment")
	}
	if n := dbfx.Count(t, `SELECT count(*) FROM agent_task_queue WHERE issue_id=$1 AND agent_id<>$2`, fx.SquadIssueID, fx.LeaderID); n != 0 {
		t.Fatal("rollback left a task")
	}
	dbfx.Exec(t, `ALTER TABLE worker_pool_delegation DROP CONSTRAINT `+constraint)
	status, out, body := fx.delegate("rollback", "work", nil)
	if status != 201 || out.AgentID != fx.workers[0] {
		t.Fatalf("rollback consumed turn: %d %s", status, body)
	}
	// A fresh handler reads the persisted cursor instead of process-local state.
	q := db.New(testPool)
	pools, err := q.ListWorkerPools(context.Background(), db.ListWorkerPoolsParams{WorkspaceID: util.MustParseUUID(testWorkspaceID), SquadID: util.MustParseUUID(fx.SquadID)})
	if err != nil || len(pools) != 1 || pools[0].LastPosition != 0 {
		t.Fatalf("durable cursor: %v %v", pools, err)
	}
}

func TestPoolWorkerReplyWakesLeader(t *testing.T) {
	fx := newPoolFixture(t)
	status, delegated, body := fx.delegate("first", "implement", nil)
	if status != http.StatusCreated {
		t.Fatalf("delegate: %d %s", status, body)
	}
	dbfx.Exec(t, `UPDATE agent_task_queue SET status='completed',completed_at=now() WHERE id=$1`, fx.task)
	dbfx.Exec(t, `UPDATE agent_task_queue SET status='running',started_at=now() WHERE id=$1`, delegated.TaskID)
	r := testutil.WithHeaders(testutil.WithURLParams(newRequest(http.MethodPost, "/comments", map[string]any{"content": "Implementation is ready for review.", "parent_id": delegated.CommentID}), "id", fx.SquadIssueID, "workspaceId", testWorkspaceID), "X-Agent-ID", delegated.AgentID, "X-Task-ID", delegated.TaskID)
	w := httptest.NewRecorder()
	testHandler.CreateComment(w, r)
	if w.Code != http.StatusCreated {
		t.Fatalf("worker reply: %d %s", w.Code, w.Body.String())
	}
	if n := dbfx.Count(t, `SELECT count(*) FROM agent_task_queue WHERE issue_id=$1 AND agent_id=$2 AND status='queued' AND is_leader_task=true AND squad_id=$3`, fx.SquadIssueID, fx.LeaderID, fx.SquadID); n != 1 {
		t.Fatalf("leader wake count = %d", n)
	}
}

func TestPoolRejectsWrongActorAndInactiveLeader(t *testing.T) {
	fx := newPoolFixture(t)
	r := testutil.WithHeaders(testutil.WithURLParams(newRequest(http.MethodPost, "/delegate", poolDelegationRequest{Pool: "kiro", RequestID: "wrong", Instructions: "work"}), "id", fx.SquadIssueID, "workspaceId", testWorkspaceID), "X-Agent-ID", fx.workers[0], "X-Task-ID", fx.task)
	w := httptest.NewRecorder()
	testHandler.DelegateWorkerPool(w, r)
	if w.Code != http.StatusForbidden {
		t.Fatalf("wrong actor: %d", w.Code)
	}
	dbfx.Exec(t, `UPDATE agent_task_queue SET status='completed',completed_at=now() WHERE id=$1`, fx.task)
	status, _, _ := fx.delegate("completed", "work", nil)
	if status != http.StatusConflict {
		t.Fatalf("inactive leader: %d", status)
	}
}

func TestPoolSkipsOfflineWorkers(t *testing.T) {
	fx := newPoolFixture(t)
	// All fixture workers share a runtime. A stale heartbeat excludes the entire pool.
	var runtimeID string
	dbfx.QueryRow(t, `SELECT runtime_id FROM agent WHERE id=$1`, fx.workers[0]).Scan(&runtimeID)
	dbfx.Exec(t, `UPDATE agent_runtime SET last_seen_at=now()-interval '10 minutes' WHERE id=$1`, runtimeID)
	t.Cleanup(func() {
		dbfx.Exec(t, `UPDATE agent_runtime SET status='online',last_seen_at=now() WHERE id=$1`, runtimeID)
	})
	status, _, _ := fx.delegate("offline", "work", nil)
	if status != http.StatusConflict {
		t.Fatalf("stale runtime: %d", status)
	}
	dbfx.Exec(t, `UPDATE agent_runtime SET last_seen_at=now() WHERE id=$1`, runtimeID)
	status, out, body := fx.delegate("offline", "work", nil)
	if status != http.StatusCreated || out.AgentID != fx.workers[0] {
		t.Fatalf("recovered runtime: %d %s", status, body)
	}
}
