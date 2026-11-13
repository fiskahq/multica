-- name: UpsertWorkerPool :one
INSERT INTO worker_pool (workspace_id, squad_id, name)
VALUES ($1, $2, $3)
ON CONFLICT (workspace_id, squad_id, name) DO UPDATE SET name = EXCLUDED.name
RETURNING *;

-- name: LockPoolLeaderTask :one
SELECT t.* FROM agent_task_queue t JOIN agent a ON a.id = t.agent_id
WHERE t.id = $1 AND a.workspace_id = $2 FOR SHARE OF t;

-- name: LockWorkerPoolWorkspace :one
SELECT id FROM workspace WHERE id = $1 FOR KEY SHARE;

-- name: LockWorkerPool :one
SELECT * FROM worker_pool
WHERE workspace_id = $1 AND squad_id = $2 AND name = $3
FOR UPDATE;

-- name: ListWorkerPools :many
SELECT * FROM worker_pool WHERE workspace_id = $1 AND squad_id = $2 ORDER BY name;

-- name: ReplaceWorkerPoolMembers :exec
DELETE FROM worker_pool_member WHERE pool_id = $1;

-- name: AddWorkerPoolMember :exec
INSERT INTO worker_pool_member (pool_id, agent_id, position, capabilities, enabled)
VALUES ($1, $2, $3, $4, $5);

-- name: ListWorkerPoolMembers :many
SELECT * FROM worker_pool_member WHERE pool_id = $1 ORDER BY position;

-- name: ListEligiblePoolWorkers :many
SELECT pm.* FROM worker_pool_member pm
JOIN worker_pool p ON p.id = pm.pool_id
JOIN squad s ON s.id = p.squad_id AND s.workspace_id = p.workspace_id
JOIN agent a ON a.id = pm.agent_id AND a.workspace_id = p.workspace_id
JOIN agent_runtime r ON r.id = a.runtime_id
WHERE pm.pool_id = $1 AND pm.enabled AND a.archived_at IS NULL
  AND a.id <> s.leader_id AND s.archived_at IS NULL
  AND EXISTS (SELECT 1 FROM squad_member sm WHERE sm.squad_id = s.id AND sm.member_type = 'agent' AND sm.member_id = a.id)
  AND pm.capabilities @> sqlc.arg(capabilities)::text[]
  AND r.status = 'online'
  AND COALESCE(r.last_seen_at, r.updated_at) >= now() - make_interval(secs => sqlc.arg(stale_seconds)::double precision)
ORDER BY (pm.position > sqlc.arg(last_position)::integer) DESC, pm.position;

-- name: AdvanceWorkerPool :exec
UPDATE worker_pool SET last_position = $2 WHERE id = $1;

-- name: GetWorkerPoolDelegation :one
SELECT * FROM worker_pool_delegation WHERE pool_id = $1 AND request_id = $2;

-- name: CreateWorkerPoolDelegation :one
INSERT INTO worker_pool_delegation (pool_id, request_id, request_hash, leader_task_id, issue_id, agent_id, task_id, comment_id)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING *;

-- name: RequiresWorkerPoolRouting :one
SELECT EXISTS (
    SELECT 1 FROM worker_pool p
    JOIN squad s ON s.id = p.squad_id AND s.workspace_id = p.workspace_id
    JOIN worker_pool_member pm ON pm.pool_id = p.id
    WHERE p.workspace_id = $1 AND s.leader_id = $2 AND pm.agent_id = $3
      AND s.archived_at IS NULL
);
