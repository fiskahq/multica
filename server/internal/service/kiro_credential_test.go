package service

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"testing"

	"github.com/jackc/pgx/v5/pgtype"
	"github.com/multica-ai/multica/server/internal/events"
	"github.com/multica-ai/multica/server/internal/testutil"
	"github.com/multica-ai/multica/server/internal/util"
	db "github.com/multica-ai/multica/server/pkg/db/generated"
	"github.com/multica-ai/multica/server/pkg/dbid"
	"github.com/multica-ai/multica/server/pkg/taskfailure"
)

type kiroCredentialFixture struct {
	*testutil.Fixture
	workspace pgtype.UUID
	sources   []string
	runtime   string
}

func newKiroCredentialFixture(t *testing.T) kiroCredentialFixture {
	t.Helper()
	f := testutil.New(sharedTestPool(t), "", "")
	f.UserID = f.Insert(t, "user", testutil.Cols{"name": "Kiro rotation", "email": util.UUIDToString(dbid.NewV7()) + "@example.test"})
	f.WorkspaceID = f.Workspace(t, "Kiro rotation", util.UUIDToString(dbid.NewV7()))
	f.Member(t, f.WorkspaceID, f.UserID, "owner")
	x := kiroCredentialFixture{Fixture: f, workspace: util.MustParseUUID(f.WorkspaceID)}
	x.runtime = f.Runtime(t, "Kiro", testutil.Cols{"provider": "kiro"})
	f.InsertNoID(t, "kiro_credential_pool", testutil.Cols{"workspace_id": f.WorkspaceID}, "workspace_id=$1", f.WorkspaceID)
	for i := 0; i < 3; i++ {
		id := f.Agent(t, fmt.Sprintf("Source %d", i), x.runtime, testutil.Cols{"custom_env": fmt.Sprintf(`{"KIRO_API_KEY":"fake-key-%d"}`, i)})
		x.sources = append(x.sources, id)
		f.InsertNoID(t, "kiro_credential_member", testutil.Cols{"workspace_id": f.WorkspaceID, "agent_id": id, "position": i}, "workspace_id=$1 AND agent_id=$2", f.WorkspaceID, id)
	}
	t.Cleanup(func() { f.Exec(t, "DELETE FROM kiro_credential_assignment WHERE workspace_id=$1", f.WorkspaceID) })
	return x
}

func (f kiroCredentialFixture) assign(task pgtype.UUID, commit bool) (string, error) {
	ctx := context.Background()
	tx, err := f.Pool.Begin(ctx)
	if err != nil {
		return "", err
	}
	defer tx.Rollback(ctx)
	key, configured, err := AssignKiroCredential(ctx, db.New(tx), f.workspace, task)
	if err != nil {
		return "", err
	}
	if !configured {
		return "", fmt.Errorf("expected configured pool")
	}
	if commit {
		err = tx.Commit(ctx)
	}
	return key, err
}

func TestKiroCredentialRotationConcurrentReplayAndRollback(t *testing.T) {
	f := newKiroCredentialFixture(t)
	rolledBack := dbid.NewV7()
	if key, err := f.assign(rolledBack, false); err != nil || key != "fake-key-0" {
		t.Fatalf("rollback selection: %q %v", key, err)
	}
	const count = 30
	var wg sync.WaitGroup
	var mu sync.Mutex
	counts := map[string]int{}
	errs := []error{}
	for i := 0; i < count; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			key, err := f.assign(dbid.NewV7(), true)
			mu.Lock()
			defer mu.Unlock()
			if err != nil {
				errs = append(errs, err)
			} else {
				counts[key]++
			}
		}()
	}
	wg.Wait()
	if len(errs) > 0 {
		t.Fatalf("concurrent rotation: %v", errs)
	}
	for i := 0; i < 3; i++ {
		if counts[fmt.Sprintf("fake-key-%d", i)] != 10 {
			t.Fatalf("unbalanced keys: %v", counts)
		}
	}
	replay := dbid.NewV7()
	for i := 0; i < 12; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			key, err := f.assign(replay, true)
			if err != nil || key != "fake-key-0" {
				mu.Lock()
				errs = append(errs, fmt.Errorf("replay %q: %v", key, err))
				mu.Unlock()
			}
		}()
	}
	wg.Wait()
	if len(errs) > 0 {
		t.Fatal(errs)
	}
	if n := f.Count(t, "SELECT count(*) FROM kiro_credential_assignment WHERE workspace_id=$1", f.WorkspaceID); n != 31 {
		t.Fatalf("assignments=%d", n)
	}
	if key, err := f.assign(dbid.NewV7(), true); err != nil || key != "fake-key-1" {
		t.Fatalf("replay advanced cursor: %q %v", key, err)
	}
}

func TestKiroCredentialQuotaFailureAndReplacement(t *testing.T) {
	f := newKiroCredentialFixture(t)
	ctx := context.Background()
	role := f.Agent(t, "development-qa", f.runtime)
	issue := f.Issue(t, "QA")
	taskID := f.Task(t, role, testutil.Cols{"runtime_id": f.runtime, "issue_id": issue, "status": "running"})
	task := util.MustParseUUID(taskID)
	if key, err := f.assign(task, true); err != nil || key != "fake-key-0" {
		t.Fatalf("first: %q %v", key, err)
	}
	svc := NewTaskService(db.New(f.Pool), f.Pool, nil, events.New())
	_, transitioned, err := svc.FailTaskWithTransition(ctx, task, "monthly usage limit", "", "", "", taskfailure.ReasonAgentProviderQuotaLimit.String(), false, "", "")
	if err != nil || !transitioned {
		t.Fatalf("quota transition: %v %v", transitioned, err)
	}
	for i := 0; i < 6; i++ {
		want := fmt.Sprintf("fake-key-%d", 1+i%2)
		if key, err := f.assign(dbid.NewV7(), true); err != nil || key != want {
			t.Fatalf("blocked quota: %q want %q: %v", key, want, err)
		}
	}
	f.Exec(t, `UPDATE agent SET custom_env='{"KIRO_API_KEY":"replacement"}'::jsonb WHERE id=$1`, f.sources[0])
	if key, err := f.assign(dbid.NewV7(), true); err != nil || key != "replacement" {
		t.Fatalf("replacement remained blocked: %q %v", key, err)
	}
}

func TestKiroCredentialNoFallbackAndWorkspaceIsolation(t *testing.T) {
	f := newKiroCredentialFixture(t)
	f.Exec(t, "UPDATE kiro_credential_member SET enabled=false WHERE workspace_id=$1", f.WorkspaceID)
	if _, err := f.assign(dbid.NewV7(), true); !errors.Is(err, ErrKiroCredentialsUnavailable) {
		t.Fatalf("missing credentials: %v", err)
	}
	if n := f.Count(t, "SELECT count(*) FROM kiro_credential_assignment WHERE workspace_id=$1", f.WorkspaceID); n != 0 {
		t.Fatalf("unavailable pool consumed assignment: %d", n)
	}
	other := newKiroCredentialFixture(t)
	f.Exec(t, "UPDATE kiro_credential_member SET enabled=true,agent_id=$2 WHERE workspace_id=$1 AND position=0", f.WorkspaceID, other.sources[0])
	if _, err := f.assign(dbid.NewV7(), true); !errors.Is(err, ErrKiroCredentialsUnavailable) {
		t.Fatalf("cross-workspace credential accepted: %v", err)
	}
	key, configured, err := AssignKiroCredential(context.Background(), db.New(f.Pool), dbid.NewV7(), dbid.NewV7())
	if err != nil || configured || key != "" {
		t.Fatalf("unconfigured workspace changed: %q %v %v", key, configured, err)
	}
}
