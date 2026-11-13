CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS worker_pool_name ON worker_pool (workspace_id, squad_id, name);
