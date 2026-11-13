CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS worker_pool_member_position ON worker_pool_member (pool_id, position);
