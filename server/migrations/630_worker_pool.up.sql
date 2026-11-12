CREATE TABLE worker_pool (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL,
    squad_id uuid NOT NULL,
    name text NOT NULL,
    last_position integer NOT NULL DEFAULT -1
);
CREATE TABLE worker_pool_member (
    pool_id uuid NOT NULL,
    agent_id uuid NOT NULL,
    position integer NOT NULL,
    capabilities text[] NOT NULL DEFAULT '{}',
    enabled boolean NOT NULL DEFAULT true
);
CREATE TABLE worker_pool_delegation (
    pool_id uuid NOT NULL,
    request_id text NOT NULL,
    request_hash text NOT NULL,
    leader_task_id uuid NOT NULL,
    issue_id uuid NOT NULL,
    agent_id uuid NOT NULL,
    task_id uuid NOT NULL,
    comment_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
