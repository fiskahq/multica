package service

import (
	"context"
	"errors"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgtype"
	db "github.com/multica-ai/multica/server/pkg/db/generated"
)

var ErrKiroCredentialsUnavailable = errors.New("no usable Kiro credentials remain in this workspace; restore quota or replace an exhausted credential, then retry")

// AssignKiroCredential runs inside the claim-delivery transaction. Replayed
// claims retain their assignment; a rolled-back delivery does not consume a turn.
func AssignKiroCredential(ctx context.Context, q *db.Queries, workspaceID, taskID pgtype.UUID) (string, bool, error) {
	pool, err := q.LockKiroCredentialPool(ctx, workspaceID)
	if errors.Is(err, pgx.ErrNoRows) {
		return "", false, nil
	}
	if err != nil {
		return "", true, err
	}
	credentials, err := q.ListKiroCredentials(ctx, db.ListKiroCredentialsParams{WorkspaceID: workspaceID, LastPosition: pool.LastPosition})
	if err != nil {
		return "", true, err
	}
	previous, err := q.GetKiroCredentialAssignment(ctx, db.GetKiroCredentialAssignmentParams{TaskID: taskID, WorkspaceID: workspaceID})
	if err == nil {
		for _, credential := range credentials {
			if credential.AgentID == previous.SourceAgentID && credential.Fingerprint == previous.KeyFingerprint {
				return credential.ApiKey, true, nil
			}
		}
		return "", true, ErrKiroCredentialsUnavailable
	}
	if !errors.Is(err, pgx.ErrNoRows) {
		return "", true, err
	}
	if len(credentials) == 0 {
		return "", true, ErrKiroCredentialsUnavailable
	}
	next := credentials[0]
	if err := q.CreateKiroCredentialAssignment(ctx, db.CreateKiroCredentialAssignmentParams{
		WorkspaceID: workspaceID, TaskID: taskID, SourceAgentID: next.AgentID, KeyFingerprint: next.Fingerprint,
	}); err != nil {
		return "", true, err
	}
	if err := q.AdvanceKiroCredentialPool(ctx, db.AdvanceKiroCredentialPoolParams{WorkspaceID: workspaceID, LastPosition: next.Position}); err != nil {
		return "", true, err
	}
	return next.ApiKey, true, nil
}

// ExhaustKiroCredential commits with the failure, before another dispatch can
// select the rejected credential. Only the fingerprint used by this task is blocked.
func ExhaustKiroCredential(ctx context.Context, q *db.Queries, taskID pgtype.UUID) error {
	workspaceID, err := q.GetKiroCredentialAssignmentWorkspace(ctx, taskID)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil
	}
	if err != nil {
		return err
	}
	if _, err := q.LockKiroCredentialPool(ctx, workspaceID); err != nil {
		return err
	}
	return q.ExhaustKiroCredentialAssignment(ctx, taskID)
}
