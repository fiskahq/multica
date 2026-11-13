CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS worker_pool_request ON worker_pool_delegation (pool_id, request_id);
