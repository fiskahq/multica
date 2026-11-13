CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS worker_pool_member_agent ON worker_pool_member (pool_id, agent_id);
