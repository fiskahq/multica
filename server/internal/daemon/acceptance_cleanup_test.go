package daemon

import (
	"context"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"testing"
	"time"

	"github.com/multica-ai/multica/server/internal/daemon/execenv"
)

func TestAcceptanceCleanup(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("shell helper fixture")
	}
	status := "acceptance"
	d := newGCTestDaemon(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprintf(w, `{"id":"issue","workspace_id":"ws","status":%q,"updated_at":"2026-11-19T00:00:00Z"}`, status)
	}))
	root := createTaskDir(t, d.cfg.WorkspacesRoot, "ws", "task-abc", &execenv.GCMeta{Kind: execenv.GCKindIssue, WorkspaceID: "ws", IssueID: "issue", TaskID: "abc", CompletedAt: time.Now()})
	artifact := filepath.Join(root, "workdir", "node_modules")
	if err := os.MkdirAll(artifact, 0700); err != nil {
		t.Fatal(err)
	}
	source := filepath.Join(root, "workdir", "source.txt")
	os.WriteFile(source, []byte("keep"), 0600)
	helper := filepath.Join(t.TempDir(), "cleanup")
	os.WriteFile(helper, []byte("#!/bin/sh\ncat >/dev/null\nexit 0\n"), 0700)
	d.cfg.AcceptanceCleanupCommand = helper
	// Activity in a different root for the same issue must prevent cleanup too.
	release := d.markActiveEnvRoot(d.acceptanceGuard("ws", "issue"))
	d.runAcceptanceCleanup(context.Background())
	if _, err := os.Stat(artifact); err != nil {
		t.Fatal("cleaned an active issue")
	}
	release()
	status = "in_progress"
	d.runAcceptanceCleanup(context.Background())
	if _, err := os.Stat(artifact); err != nil {
		t.Fatal("cleaned a reopened issue")
	}
	status = "acceptance"
	os.WriteFile(helper, []byte("#!/bin/sh\nexit 1\n"), 0700)
	d.runAcceptanceCleanup(context.Background())
	if _, err := os.Stat(filepath.Join(root, ".acceptance-cleaned")); !os.IsNotExist(err) {
		t.Fatal("failed hook acknowledged")
	}
	os.WriteFile(helper, []byte("#!/bin/sh\ncat >/dev/null\nexit 0\n"), 0700)
	d.runAcceptanceCleanup(context.Background())
	if _, err := os.Stat(artifact); !os.IsNotExist(err) {
		t.Fatal("dependencies remain")
	}
	if _, err := os.Stat(source); err != nil {
		t.Fatal("source removed")
	}
	if _, err := os.Stat(filepath.Join(root, ".acceptance-cleaned")); err != nil {
		t.Fatal("no cleanup receipt")
	}
}

func TestAcceptanceGuardBlocksNewTaskUntilCleanupReleases(t *testing.T) {
	d := newGCTestDaemon(t, http.NotFoundHandler())
	guard := d.acceptanceGuard("ws", "issue")
	release, ok := d.reserveEnvRootForGC(guard)
	if !ok {
		t.Fatal("cannot reserve idle issue")
	}
	started := make(chan func(), 1)
	go func() { started <- d.markActiveEnvRoot(guard) }()
	select {
	case done := <-started:
		done()
		t.Fatal("task entered during cleanup")
	case <-time.After(30 * time.Millisecond):
	}
	release()
	select {
	case done := <-started:
		done()
	case <-time.After(time.Second):
		t.Fatal("task did not resume")
	}
}

func TestAcceptanceCleanupRechecksStatusAndReceipt(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("shell helper fixture")
	}
	checks := 0
	reopen := true
	d := newGCTestDaemon(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		checks++
		if r.Header.Get("X-Workspace-ID") != "ws" {
			t.Error("workspace header missing")
		}
		status := "acceptance"
		if reopen && checks%2 == 0 {
			status = "in_progress"
		}
		fmt.Fprintf(w, `{"id":"issue","workspace_id":"ws","status":%q,"updated_at":"2026-11-19T00:00:00Z"}`, status)
	}))
	root := createTaskDir(t, d.cfg.WorkspacesRoot, "ws", "task-abc", &execenv.GCMeta{Kind: execenv.GCKindIssue, WorkspaceID: "ws", IssueID: "issue", TaskID: "abc", CompletedAt: time.Now()})
	artifact := filepath.Join(root, "workdir", ".venv")
	os.MkdirAll(artifact, 0700)
	helper := filepath.Join(t.TempDir(), "cleanup")
	count := filepath.Join(t.TempDir(), "runs")
	os.WriteFile(helper, []byte("#!/bin/sh\ncat >/dev/null\necho run >> '"+count+"'\n"), 0700)
	d.cfg.AcceptanceCleanupCommand = helper
	d.runAcceptanceCleanup(context.Background())
	if _, err := os.Stat(artifact); err != nil {
		t.Fatal("reopened ticket cleaned")
	}
	reopen = false
	d.runAcceptanceCleanup(context.Background())
	d.runAcceptanceCleanup(context.Background())
	content, _ := os.ReadFile(count)
	if string(content) != "run\n" {
		t.Fatalf("cleanup not idempotent: %q", content)
	}
	// A subsequent run invalidates the receipt even if the issue stayed in Acceptance.
	execenv.WriteGCMeta(root, execenv.GCMeta{Kind: execenv.GCKindIssue, WorkspaceID: "ws", IssueID: "issue", TaskID: "abc"}, d.logger)
	d.runAcceptanceCleanup(context.Background())
	content, _ = os.ReadFile(count)
	if string(content) != "run\nrun\n" {
		t.Fatalf("new run skipped: %q", content)
	}
}
