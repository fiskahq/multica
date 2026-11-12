CREATE INDEX CONCURRENTLY kiro_credential_exhausted ON kiro_credential_assignment (workspace_id, key_fingerprint) WHERE quota_exhausted;
