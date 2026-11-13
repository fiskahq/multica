package handler

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"regexp"
	"sort"
	"strings"

	"github.com/go-chi/chi/v5"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgtype"
	"github.com/multica-ai/multica/server/internal/service"
	"github.com/multica-ai/multica/server/internal/util"
	db "github.com/multica-ai/multica/server/pkg/db/generated"
	"github.com/multica-ai/multica/server/pkg/dbid"
	"github.com/multica-ai/multica/server/pkg/protocol"
)

var poolLabelPattern = regexp.MustCompile(`^[a-z][a-z0-9_-]{0,63}$`)

type poolMemberInput struct {
	AgentID      string   `json:"agent_id"`
	Capabilities []string `json:"capabilities"`
	Enabled      *bool    `json:"enabled,omitempty"`
}

type poolResponse struct {
	ID           string                `json:"id"`
	Name         string                `json:"name"`
	LastPosition int32                 `json:"last_position"`
	Members      []db.WorkerPoolMember `json:"members"`
}

func (h *Handler) ListWorkerPools(w http.ResponseWriter, r *http.Request) {
	squad, _, ok := h.loadSquadInWorkspace(w, r)
	if !ok {
		return
	}
	pools, err := h.Queries.ListWorkerPools(r.Context(), db.ListWorkerPoolsParams{WorkspaceID: squad.WorkspaceID, SquadID: squad.ID})
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to load pools")
		return
	}
	out := make([]poolResponse, 0, len(pools))
	for _, p := range pools {
		members, err := h.Queries.ListWorkerPoolMembers(r.Context(), p.ID)
		if err != nil {
			writeError(w, http.StatusInternalServerError, "failed to load pool members")
			return
		}
		out = append(out, poolResponse{uuidToString(p.ID), p.Name, p.LastPosition, members})
	}
	writeJSON(w, http.StatusOK, out)
}

// PutWorkerPool replaces the ordered configuration without resetting the cursor.
func (h *Handler) PutWorkerPool(w http.ResponseWriter, r *http.Request) {
	squad, ws, ok := h.loadSquadInWorkspace(w, r)
	if !ok {
		return
	}
	actorType, _ := h.resolveActor(r, requestUserID(r), ws)
	if actorType != "member" {
		writeError(w, http.StatusForbidden, "only members can configure worker pools")
		return
	}
	member, ok := h.requireWorkspaceMember(w, r, ws, "workspace not found")
	if !ok {
		return
	}
	if !canManageSquad(member, squad) {
		writeError(w, http.StatusForbidden, "insufficient permissions")
		return
	}
	name := chi.URLParam(r, "pool")
	if !poolLabelPattern.MatchString(name) {
		writeError(w, http.StatusBadRequest, "invalid pool name")
		return
	}
	var req struct {
		Members []poolMemberInput `json:"members"`
	}
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 128<<10)).Decode(&req); err != nil || req.Members == nil || len(req.Members) > 64 {
		writeError(w, http.StatusBadRequest, "invalid members (maximum 64)")
		return
	}
	ids := make([]pgtype.UUID, len(req.Members))
	seen := map[string]bool{}
	for i, m := range req.Members {
		id, ok := parseUUIDOrBadRequest(w, m.AgentID, "agent_id")
		if !ok {
			return
		}
		ids[i] = id
		if seen[uuidToString(id)] || !validPoolCapabilities(m.Capabilities) {
			writeError(w, http.StatusBadRequest, "duplicate agent or invalid capabilities")
			return
		}
		seen[uuidToString(id)] = true
	}
	tx, err := h.TxStarter.Begin(r.Context())
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to begin pool update")
		return
	}
	defer tx.Rollback(r.Context())
	q := h.Queries.WithTx(tx)
	if _, err = q.LockWorkerPoolWorkspace(r.Context(), squad.WorkspaceID); err != nil {
		writeError(w, http.StatusNotFound, "workspace not found")
		return
	}
	squad, err = q.LockSquadForUpdate(r.Context(), db.LockSquadForUpdateParams{ID: squad.ID, WorkspaceID: squad.WorkspaceID})
	if err != nil || squad.ArchivedAt.Valid {
		writeError(w, http.StatusConflict, "squad unavailable")
		return
	}
	for _, id := range ids {
		agent, err := q.GetAgent(r.Context(), id)
		if err != nil || agent.WorkspaceID != squad.WorkspaceID || agent.ArchivedAt.Valid || agent.ID == squad.LeaderID {
			writeError(w, http.StatusBadRequest, "pool workers must be active squad agents, excluding its leader")
			return
		}
		belongs, err := q.IsSquadMember(r.Context(), db.IsSquadMemberParams{SquadID: squad.ID, MemberType: "agent", MemberID: id})
		if err != nil || !belongs {
			writeError(w, http.StatusBadRequest, "worker is not a squad member")
			return
		}
		if !h.memberCanWireAgent(r.Context(), member, agent, ws) {
			writeError(w, http.StatusForbidden, "worker invocation not allowed")
			return
		}
	}
	pool, err := q.UpsertWorkerPool(r.Context(), db.UpsertWorkerPoolParams{WorkspaceID: squad.WorkspaceID, SquadID: squad.ID, Name: name})
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to save pool")
		return
	}
	if err = q.ReplaceWorkerPoolMembers(r.Context(), pool.ID); err != nil {
		writeError(w, http.StatusInternalServerError, "failed to replace pool members")
		return
	}
	for i, m := range req.Members {
		caps := m.Capabilities
		if caps == nil {
			caps = []string{}
		}
		err = q.AddWorkerPoolMember(r.Context(), db.AddWorkerPoolMemberParams{PoolID: pool.ID, AgentID: ids[i], Position: int32(i), Capabilities: caps, Enabled: m.Enabled == nil || *m.Enabled})
		if err != nil {
			writeError(w, http.StatusInternalServerError, "failed to save pool member")
			return
		}
	}
	if err = tx.Commit(r.Context()); err != nil {
		writeError(w, http.StatusInternalServerError, "failed to commit pool")
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"id": uuidToString(pool.ID), "name": pool.Name})
}

func validPoolCapabilities(caps []string) bool {
	if len(caps) > 32 {
		return false
	}
	seen := map[string]bool{}
	for _, c := range caps {
		if !poolLabelPattern.MatchString(c) || seen[c] {
			return false
		}
		seen[c] = true
	}
	return true
}

type poolDelegationRequest struct {
	Pool         string   `json:"pool"`
	RequestID    string   `json:"request_id"`
	Instructions string   `json:"instructions"`
	Capabilities []string `json:"capabilities"`
}

type poolDelegationResponse struct {
	PoolID    string `json:"pool_id"`
	RequestID string `json:"request_id"`
	AgentID   string `json:"agent_id"`
	TaskID    string `json:"task_id"`
	CommentID string `json:"comment_id"`
	Replayed  bool   `json:"replayed"`
}

func delegationResponse(d db.WorkerPoolDelegation, replayed bool) poolDelegationResponse {
	return poolDelegationResponse{uuidToString(d.PoolID), d.RequestID, uuidToString(d.AgentID), uuidToString(d.TaskID), uuidToString(d.CommentID), replayed}
}

// DelegateWorkerPool commits the cursor, comment and native task together.
func (h *Handler) DelegateWorkerPool(w http.ResponseWriter, r *http.Request) {
	issue, ok := h.loadIssueForUser(w, r, chi.URLParam(r, "id"))
	if !ok {
		return
	}
	var req poolDelegationRequest
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 128<<10)).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	if !poolLabelPattern.MatchString(req.Pool) || len(req.RequestID) < 1 || len(req.RequestID) > 128 || strings.TrimSpace(req.Instructions) == "" || len(req.Instructions) > 32000 || strings.ContainsRune(req.Instructions, 0) || !validPoolCapabilities(req.Capabilities) {
		writeError(w, http.StatusBadRequest, "pool, request_id, instructions and valid capabilities are required")
		return
	}
	if req.Capabilities == nil {
		req.Capabilities = []string{}
	}
	sort.Strings(req.Capabilities)
	actorType, actorID := h.resolveActor(r, requestUserID(r), uuidToString(issue.WorkspaceID))
	taskID, ok := parseUUIDOrBadRequest(w, r.Header.Get("X-Task-ID"), "task id")
	if !ok {
		return
	}
	leader, err := h.Queries.GetAgentTaskInWorkspace(r.Context(), db.GetAgentTaskInWorkspaceParams{ID: taskID, WorkspaceID: issue.WorkspaceID})
	if err != nil || actorType != "agent" || uuidToString(leader.AgentID) != actorID || leader.IssueID != issue.ID || !leader.IsLeaderTask || !leader.SquadID.Valid {
		writeError(w, http.StatusForbidden, "only this issue's active squad leader task can delegate")
		return
	}
	encoded, _ := json.Marshal(struct {
		Request poolDelegationRequest
		Issue   string
		Leader  string
	}{req, uuidToString(issue.ID), actorID})
	digest := sha256.Sum256(encoded)
	requestHash := hex.EncodeToString(digest[:])
	// Overlay preparation can use network I/O. Cache it outside the locked transaction.
	prepared := map[string]db.CreateAgentTaskParams{}
	commentID := dbid.NewV7()
	for attempt := 0; attempt < 128; attempt++ {
		done := h.tryPoolDelegation(w, r, issue, leader, req, requestHash, commentID, prepared)
		if done {
			return
		}
	}
	writeError(w, http.StatusConflict, "pool changed repeatedly; retry with the same request_id")
}

func (h *Handler) tryPoolDelegation(w http.ResponseWriter, r *http.Request, issue db.Issue, leader db.AgentTaskQueue, req poolDelegationRequest, requestHash string, commentID pgtype.UUID, prepared map[string]db.CreateAgentTaskParams) bool {
	ctx := r.Context()
	tx, err := h.TxStarter.Begin(ctx)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to begin delegation")
		return true
	}
	defer tx.Rollback(ctx)
	q := h.Queries.WithTx(tx)
	if _, err = q.LockWorkerPoolWorkspace(ctx, issue.WorkspaceID); err != nil {
		writeError(w, http.StatusNotFound, "workspace not found")
		return true
	}
	squad, err := q.LockSquadForAutopilotAssignment(ctx, db.LockSquadForAutopilotAssignmentParams{ID: leader.SquadID, WorkspaceID: issue.WorkspaceID})
	if err != nil || squad.ArchivedAt.Valid || squad.LeaderID != leader.AgentID {
		writeError(w, http.StatusForbidden, "caller is no longer the squad leader")
		return true
	}
	pool, err := q.LockWorkerPool(ctx, db.LockWorkerPoolParams{WorkspaceID: issue.WorkspaceID, SquadID: squad.ID, Name: req.Pool})
	if errors.Is(err, pgx.ErrNoRows) {
		writeError(w, http.StatusNotFound, "worker pool not found")
		return true
	}
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to lock worker pool")
		return true
	}
	prior, err := q.GetWorkerPoolDelegation(ctx, db.GetWorkerPoolDelegationParams{PoolID: pool.ID, RequestID: req.RequestID})
	if err == nil {
		if prior.RequestHash != requestHash {
			writeError(w, http.StatusConflict, "request_id already used with different input")
			return true
		}
		writeJSON(w, http.StatusOK, delegationResponse(prior, true))
		return true
	}
	if !errors.Is(err, pgx.ErrNoRows) {
		writeError(w, http.StatusInternalServerError, "failed to read delegation")
		return true
	}
	leader, err = q.LockPoolLeaderTask(ctx, db.LockPoolLeaderTaskParams{ID: leader.ID, WorkspaceID: issue.WorkspaceID})
	if err != nil || leader.Status != "running" {
		writeError(w, http.StatusConflict, "leader task is not running")
		return true
	}
	noAction, err := service.HasSquadLeaderNoActionEvaluationForTask(ctx, q, leader)
	if err != nil || noAction {
		writeError(w, http.StatusConflict, "leader task cannot delegate after no_action")
		return true
	}
	candidates, err := q.ListEligiblePoolWorkers(ctx, db.ListEligiblePoolWorkersParams{PoolID: pool.ID, Capabilities: req.Capabilities, StaleSeconds: service.RuntimeClaimFreshnessSeconds, LastPosition: pool.LastPosition})
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to select pool worker")
		return true
	}
	var selected db.WorkerPoolMember
	var agent db.Agent
	found := false
	for _, candidate := range candidates {
		a, e := q.LockAgentForAutopilotAssignment(ctx, db.LockAgentForAutopilotAssignmentParams{ID: candidate.AgentID, WorkspaceID: issue.WorkspaceID})
		if e != nil {
			writeError(w, http.StatusInternalServerError, "failed to load pool worker")
			return true
		}
		if a.ArchivedAt.Valid || !a.RuntimeID.Valid {
			continue
		}
		// Pool selection bypasses only the routing restriction, never invocation permissions.
		if h.invokeAgentDecision(ctx, a, "agent", uuidToString(leader.AgentID), uuidToString(leader.OriginatorUserID), uuidToString(issue.WorkspaceID)) {
			selected = candidate
			agent = a
			found = true
			break
		}
	}
	if !found {
		writeError(w, http.StatusConflict, "no eligible online worker; cursor unchanged")
		return true
	}
	key := fmt.Sprintf("%s/%s/%v", uuidToString(agent.ID), uuidToString(agent.RuntimeID), agent.UpdatedAt)
	content := fmt.Sprintf("Delegated through pool `%s` to %s.\n\n%s", pool.Name, agent.Name, req.Instructions)
	params, ready := prepared[key]
	if !ready {
		if err = tx.Rollback(ctx); err != nil {
			writeError(w, http.StatusInternalServerError, "failed to release pool lock")
			return true
		}
		params, err = h.TaskService.PreparePoolTask(ctx, issue, agent, db.Comment{ID: commentID, WorkspaceID: issue.WorkspaceID, IssueID: issue.ID, AuthorType: "agent", AuthorID: leader.AgentID, SourceTaskID: leader.ID, Content: content})
		if err != nil {
			writeError(w, http.StatusInternalServerError, "failed to prepare worker task")
			return true
		}
		prepared[key] = params
		return false
	}
	comment, err := q.CreateComment(ctx, db.CreateCommentParams{ID: commentID, IssueID: issue.ID, WorkspaceID: issue.WorkspaceID, AuthorType: "agent", AuthorID: leader.AgentID, Content: content, Type: "comment", SourceTaskID: leader.ID, ParentID: leader.TriggerCommentID})
	if err != nil {
		slog.ErrorContext(ctx, "pool comment insert failed", "error", err)
		writeError(w, http.StatusInternalServerError, "failed to save delegation comment")
		return true
	}
	if leader.TriggerCommentID.Valid {
		if _, err = q.UnresolveComment(ctx, leader.TriggerCommentID); err != nil {
			writeError(w, http.StatusInternalServerError, "failed to reopen delegation thread")
			return true
		}
	}
	task, err := h.TaskService.CreatePoolTask(ctx, q, issue, params)
	if err != nil {
		writeError(w, http.StatusConflict, "worker task could not be queued; cursor unchanged")
		return true
	}
	delegation, err := q.CreateWorkerPoolDelegation(ctx, db.CreateWorkerPoolDelegationParams{PoolID: pool.ID, RequestID: req.RequestID, RequestHash: requestHash, LeaderTaskID: leader.ID, IssueID: issue.ID, AgentID: agent.ID, TaskID: task.ID, CommentID: comment.ID})
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to save delegation")
		return true
	}
	if err = q.AdvanceWorkerPool(ctx, db.AdvanceWorkerPoolParams{ID: pool.ID, LastPosition: selected.Position}); err != nil {
		writeError(w, http.StatusInternalServerError, "failed to advance pool")
		return true
	}
	if err = tx.Commit(ctx); err != nil {
		writeError(w, http.StatusInternalServerError, "failed to commit delegation; retry with the same request_id")
		return true
	}
	h.TaskService.PublishPoolTask(ctx, task)
	response := commentToResponse(comment.Comment(), nil, nil)
	response.IssueRevision = comment.IssueRevision
	h.publish(protocol.EventCommentCreated, uuidToString(issue.WorkspaceID), "agent", uuidToString(leader.AgentID), map[string]any{
		"comment": response, "issue_title": issue.Title, "issue_status": issue.Status,
		"issue_assignee_type": textToPtr(issue.AssigneeType), "issue_assignee_id": uuidToPtr(issue.AssigneeID),
		"issue_revision": comment.IssueRevision,
	})
	writeJSON(w, http.StatusCreated, delegationResponse(delegation, false))
	return true
}

func (h *Handler) poolRoutingRequired(ctx context.Context, agent db.Agent, actorType, actorID, ws string) bool {
	if actorType != "agent" || actorID == "" {
		return false
	}
	actor, err := util.ParseUUID(actorID)
	if err != nil {
		return true
	}
	workspace, err := util.ParseUUID(ws)
	if err != nil {
		return true
	}
	required, err := h.Queries.RequiresWorkerPoolRouting(ctx, db.RequiresWorkerPoolRoutingParams{WorkspaceID: workspace, LeaderID: actor, AgentID: agent.ID})
	return err != nil || required
}
