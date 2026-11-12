CREATE UNIQUE INDEX CONCURRENTLY worker_pool_request ON worker_pool_delegation (pool_id, request_id);
