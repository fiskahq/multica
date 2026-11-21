-- name: SetIssueDecisionPolicy :one
-- CreateComment holds this issue's row lock until the policy also commits.
UPDATE issue SET decision_policy = @policy::jsonb
WHERE id = @issue_id AND workspace_id = @workspace_id AND parent_issue_id IS NULL
RETURNING id;
