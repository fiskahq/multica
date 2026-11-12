-- name: LockKiroCredentialPool :one
SELECT * FROM kiro_credential_pool WHERE workspace_id = $1 FOR UPDATE;

-- name: ListKiroCredentials :many
SELECT m.agent_id, m.position,
       (a.custom_env->>'KIRO_API_KEY')::text AS api_key,
       encode(sha256(convert_to(a.custom_env->>'KIRO_API_KEY', 'UTF8')), 'hex')::text AS fingerprint
FROM kiro_credential_member m
JOIN agent a ON a.id = m.agent_id AND a.workspace_id = m.workspace_id
JOIN agent_runtime r ON r.id = a.runtime_id AND r.workspace_id = m.workspace_id
WHERE m.workspace_id = $1 AND m.enabled AND a.archived_at IS NULL AND r.provider = 'kiro'
  AND COALESCE(a.custom_env->>'KIRO_API_KEY', '') <> ''
  AND NOT EXISTS (
    SELECT 1 FROM kiro_credential_assignment x
    WHERE x.workspace_id = m.workspace_id AND x.quota_exhausted
      AND x.key_fingerprint = encode(sha256(convert_to(a.custom_env->>'KIRO_API_KEY', 'UTF8')), 'hex')
  )
ORDER BY (m.position > sqlc.arg(last_position)::integer) DESC, m.position;

-- name: GetKiroCredentialAssignment :one
SELECT * FROM kiro_credential_assignment WHERE task_id = $1 AND workspace_id = $2;

-- name: CreateKiroCredentialAssignment :exec
INSERT INTO kiro_credential_assignment (workspace_id, task_id, source_agent_id, key_fingerprint)
VALUES ($1, $2, $3, $4);

-- name: AdvanceKiroCredentialPool :exec
UPDATE kiro_credential_pool SET last_position = $2 WHERE workspace_id = $1;

-- name: ExhaustKiroCredentialAssignment :exec
UPDATE kiro_credential_assignment SET quota_exhausted = true WHERE task_id = $1;

-- name: GetKiroCredentialAssignmentWorkspace :one
SELECT workspace_id FROM kiro_credential_assignment WHERE task_id = $1;
