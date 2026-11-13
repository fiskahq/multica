CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS kiro_credential_member_agent ON kiro_credential_member (workspace_id, agent_id);
