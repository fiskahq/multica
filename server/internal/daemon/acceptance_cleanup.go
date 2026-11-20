package daemon

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/multica-ai/multica/server/internal/daemon/execenv"
	"github.com/multica-ai/multica/server/internal/daemon/processtree"
)

// Issue guards also cover new roots created while cleanup is in progress.
func (d *Daemon) acceptanceGuard(workspace, issue string) string {
	return filepath.Join(d.cfg.WorkspacesRoot, ".acceptance-locks", workspace, issue)
}

func (d *Daemon) acceptanceCleanupLoop(ctx context.Context) {
	if d.cfg.AcceptanceCleanupCommand == "" {
		return
	}
	ticker := time.NewTicker(time.Minute)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			d.runAcceptanceCleanup(ctx)
		}
	}
}

type acceptanceIssue struct {
	ID          string    `json:"id"`
	WorkspaceID string    `json:"workspace_id"`
	Status      string    `json:"status"`
	UpdatedAt   time.Time `json:"updated_at"`
}

func (d *Daemon) acceptanceIssue(ctx context.Context, workspace, issue string) (*acceptanceIssue, error) {
	var result acceptanceIssue
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, d.client.baseURL+"/api/issues/"+issue, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+d.client.token)
	req.Header.Set("X-Workspace-ID", workspace)
	d.client.setIdentityHeaders(req)
	response, err := d.client.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("issue lookup returned HTTP %d", response.StatusCode)
	}
	if err = json.NewDecoder(response.Body).Decode(&result); err != nil {
		return nil, err
	}
	if result.ID != issue || result.WorkspaceID != workspace {
		return nil, fmt.Errorf("issue identity mismatch")
	}
	return &result, nil
}

func (d *Daemon) runAcceptanceCleanup(ctx context.Context) {
	workspaces, err := os.ReadDir(d.cfg.WorkspacesRoot)
	if err != nil {
		return
	}
	groups := map[string][]issueGCCandidate{}
	for _, ws := range workspaces {
		if !ws.IsDir() || strings.HasPrefix(ws.Name(), ".") {
			continue
		}
		base := filepath.Join(d.cfg.WorkspacesRoot, ws.Name())
		roots, err := os.ReadDir(base)
		if err != nil {
			continue
		}
		for _, root := range roots {
			if ctx.Err() != nil {
				return
			}
			if !root.IsDir() {
				continue
			}
			path := filepath.Join(base, root.Name())
			meta, err := execenv.ReadGCMeta(path)
			if err != nil || meta.Kind != execenv.GCKindIssue || meta.IssueID == "" || meta.WorkspaceID == "" || meta.CompletedAt.IsZero() {
				continue
			}
			if _, err := d.gcTaskDirOwner(path); err != nil {
				continue
			}
			key := meta.WorkspaceID + "/" + meta.IssueID
			groups[key] = append(groups[key], issueGCCandidate{taskDir: path, meta: meta})
		}
	}
	for _, candidates := range groups {
		if ctx.Err() != nil {
			return
		}
		meta := candidates[0].meta
		issue, err := d.acceptanceIssue(ctx, meta.WorkspaceID, meta.IssueID)
		if err != nil || issue.Status != "acceptance" {
			continue
		}
		release, ok := d.reserveEnvRootForGC(d.acceptanceGuard(meta.WorkspaceID, meta.IssueID))
		if !ok {
			continue
		}
		d.cleanAcceptanceIssue(ctx, issue, candidates)
		release()
	}
}

func (d *Daemon) cleanAcceptanceIssue(ctx context.Context, issue *acceptanceIssue, candidates []issueGCCandidate) {
	releases := []func(){}
	defer func() {
		for _, release := range releases {
			release()
		}
	}()
	for _, candidate := range candidates {
		if d.environments.servesFrom(candidate.taskDir) {
			return
		}
		release, ok := d.reserveEnvRootForGC(candidate.taskDir)
		if !ok {
			return
		}
		releases = append(releases, release)
	}
	// A user may have reopened the issue while the guard was being acquired.
	current, err := d.acceptanceIssue(ctx, issue.WorkspaceID, issue.ID)
	if err != nil || current.Status != "acceptance" {
		return
	}
	pending := []issueGCCandidate{}
	for _, candidate := range candidates {
		meta, err := execenv.ReadGCMeta(candidate.taskDir)
		if err != nil || meta.IssueID != issue.ID || meta.WorkspaceID != issue.WorkspaceID {
			return
		}
		if _, err = d.gcTaskDirOwner(candidate.taskDir); err != nil {
			return
		}
		candidate.meta = meta
		signature := current.UpdatedAt.UTC().Format(time.RFC3339Nano) + "\n" + meta.CompletedAt.UTC().Format(time.RFC3339Nano)
		previous, _ := os.ReadFile(filepath.Join(candidate.taskDir, ".acceptance-cleaned"))
		if string(previous) != signature {
			pending = append(pending, candidate)
		}
	}
	if len(pending) == 0 {
		return
	}
	payload := struct {
		IssueID     string   `json:"issue_id"`
		WorkspaceID string   `json:"workspace_id"`
		Roots       []string `json:"roots"`
	}{IssueID: issue.ID, WorkspaceID: issue.WorkspaceID}
	for _, candidate := range candidates {
		payload.Roots = append(payload.Roots, candidate.taskDir)
	}
	body, _ := json.Marshal(payload)
	hookCtx, cancel := context.WithTimeout(ctx, 5*time.Minute)
	defer cancel()
	cmd := exec.Command(d.cfg.AcceptanceCleanupCommand)
	cmd.Stdin = strings.NewReader(string(body))
	// The helper must not log Docker environment values or container configuration.
	if err := processtree.Run(hookCtx, cmd, 5*time.Second); err != nil {
		d.logger.Warn("acceptance cleanup: resource cleanup failed; will retry", "issue", issue.ID, "error", err)
		return
	}
	for _, candidate := range pending {
		removed, bytes, _, complete := d.sweepTaskArtifacts(ctx, candidate.taskDir, newArtifactMatcher([]string{"node_modules", ".next", ".turbo", ".venv", "__pycache__", ".pytest_cache"}, execenv.ManagedReclaimableArtifactSubpaths()))
		if !complete {
			continue
		}
		signature := current.UpdatedAt.UTC().Format(time.RFC3339Nano) + "\n" + candidate.meta.CompletedAt.UTC().Format(time.RFC3339Nano)
		// Only acknowledge a completed pass. Cancellation leaves it eligible for retry.
		if ctx.Err() != nil {
			return
		}
		if err := writeAcceptanceReceipt(candidate.taskDir, signature); err != nil {
			return
		}
		d.logger.Info("acceptance cleanup: completed", "issue", issue.ID, "root", candidate.taskDir, "artifact_dirs", removed, "bytes_reclaimed", bytes)
	}
}

func writeAcceptanceReceipt(root, signature string) error {
	f, err := os.CreateTemp(root, ".acceptance-cleaned-")
	if err != nil {
		return err
	}
	defer os.Remove(f.Name())
	if _, err = f.WriteString(signature); err != nil {
		f.Close()
		return err
	}
	if err = f.Close(); err != nil {
		return err
	}
	return os.Rename(f.Name(), filepath.Join(root, ".acceptance-cleaned"))
}
