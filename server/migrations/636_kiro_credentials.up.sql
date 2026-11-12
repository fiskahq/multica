CREATE TABLE kiro_credential_pool (
    workspace_id uuid NOT NULL,
    last_position integer NOT NULL DEFAULT -1
);
CREATE TABLE kiro_credential_member (
    workspace_id uuid NOT NULL,
    agent_id uuid NOT NULL,
    position integer NOT NULL,
    enabled boolean NOT NULL DEFAULT true
);
CREATE TABLE kiro_credential_assignment (
    workspace_id uuid NOT NULL,
    task_id uuid NOT NULL,
    source_agent_id uuid NOT NULL,
    key_fingerprint text NOT NULL,
    quota_exhausted boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now()
);
