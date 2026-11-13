package service

import (
	"context"
	"time"

	"github.com/jackc/pgx/v5/pgtype"
	"github.com/multica-ai/multica/server/internal/attribution"
	db "github.com/multica-ai/multica/server/pkg/db/generated"
	"github.com/multica-ai/multica/server/pkg/dbid"
	"github.com/multica-ai/multica/server/pkg/protocol"
)

// mentionTaskParams prepares overlays before callers take database locks.
func (s *TaskService) mentionTaskParams(ctx context.Context, issue db.Issue, agent db.Agent, commentID pgtype.UUID, attr attribution.Result) db.CreateAgentTaskParams {
	overlay := s.buildRuntimeMCPOverlay(ctx, attr.UserID, agent)
	source, parent, evidenceKind, evidenceRef := attributionCreateParams(attr)
	return db.CreateAgentTaskParams{
		ID: dbid.NewV7(), AgentID: agent.ID, RuntimeID: agent.RuntimeID, IssueID: issue.ID,
		Priority: priorityToInt(issue.Priority), TriggerCommentID: commentID,
		TriggerSummary:   s.buildCommentTriggerSummary(ctx, issue.WorkspaceID, commentID),
		OriginatorUserID: attr.UserID, AccountableUserID: attr.AccountableUserID,
		RuleVersionID: attr.RuleVersionID, OriginatorSource: source, DelegatedFromTaskID: parent,
		TriggerEvidenceKind: evidenceKind, TriggerEvidenceRefID: evidenceRef,
		RuntimeMcpOverlay: overlay.Overlay, RuntimeConnectedApps: overlay.ConnectedApps,
		HeadSha: headShaText(s.ResolveIssueReviewSHA(ctx, issue.ID)),
	}
}

// PreparePoolTask uses the same attribution and overlays as a leader mention.
// The comment is persisted with the task by the caller's transaction.
func (s *TaskService) PreparePoolTask(ctx context.Context, issue db.Issue, agent db.Agent, comment db.Comment) (db.CreateAgentTaskParams, error) {
	attr := s.attributionFromComment(ctx, comment, attribution.SourceDelegation)
	attr, err := s.applyAttributionFallback(ctx, attr, agent)
	if err != nil {
		return db.CreateAgentTaskParams{}, err
	}
	if err := s.guardRunProjectAccess(ctx, issue, agent, attr.UserID, attr.AccountableUserID); err != nil {
		return db.CreateAgentTaskParams{}, err
	}
	params := s.mentionTaskParams(ctx, issue, agent, comment.ID, attr)
	params.TriggerSummary = pgtype.Text{String: truncateForSummary(comment.Content, 200), Valid: true}
	params.FireAt = pgtype.Timestamptz{Time: time.Now(), Valid: true}
	return params, nil
}

func (s *TaskService) CreatePoolTask(ctx context.Context, q *db.Queries, issue db.Issue, params db.CreateAgentTaskParams) (db.AgentTaskQueue, error) {
	if err := guardIssueNotInTriage(ctx, q, issue.ID, OriginNamed); err != nil {
		return db.AgentTaskQueue{}, err
	}
	return q.CreateAgentTask(ctx, params)
}

// PublishPoolTask must be called only after the delegation transaction commits.
func (s *TaskService) PublishPoolTask(ctx context.Context, task db.AgentTaskQueue) {
	s.broadcastTaskEvent(ctx, protocol.EventTaskQueued, task)
	s.NotifyTaskEnqueued(ctx, task)
}
