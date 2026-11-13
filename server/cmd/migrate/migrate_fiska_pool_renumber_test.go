package main

import (
	"context"
	"fmt"
	"math/rand/v2"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
)

func TestFiskaPoolRenumberPreservesExistingData(t *testing.T) {
	versions := []string{
		"657_worker_pool",
		"658_worker_pool_id",
		"659_worker_pool_name",
		"660_worker_pool_member_agent",
		"661_worker_pool_member_position",
		"662_worker_pool_request",
		"663_kiro_credentials",
		"664_kiro_credential_pool_workspace",
		"665_kiro_credential_member_agent",
		"666_kiro_credential_member_position",
		"667_kiro_credential_assignment_task",
		"668_kiro_credential_exhausted",
	}
	for _, existing := range []bool{false, true} {
		t.Run(fmt.Sprintf("existing_%t", existing), func(t *testing.T) {
			admin := openTestPool(t)
			ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
			defer cancel()
			schema := fmt.Sprintf("migrate_fiska_%d_%d", time.Now().UnixNano(), rand.Uint32())
			ident := pgx.Identifier{schema}.Sanitize()
			if _, err := admin.Exec(ctx, "CREATE SCHEMA "+ident); err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() {
				ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
				defer cancel()
				if _, err := admin.Exec(ctx, "DROP SCHEMA "+ident+" CASCADE"); err != nil {
					t.Error(err)
				}
			})
			pool := openTestPoolWithSearchPath(t, schema)
			files := realMigrationFiles(t, versions, "up")
			if _, err := pool.Exec(ctx, "CREATE TABLE schema_migrations (version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ DEFAULT now())"); err != nil {
				t.Fatal(err)
			}
			var originalOID uint32
			if existing {
				for i, file := range files {
					sql, err := os.ReadFile(file)
					if err != nil {
						t.Fatal(err)
					}
					// Reproduce the original, non-idempotent 630-641 migrations.
					if _, err := pool.Exec(ctx, strings.ReplaceAll(string(sql), "IF NOT EXISTS ", "")); err != nil {
						t.Fatal(err)
					}
					old := fmt.Sprintf("%d%s", 630+i, versions[i][3:])
					if _, err := pool.Exec(ctx, "INSERT INTO schema_migrations (version) VALUES ($1)", old); err != nil {
						t.Fatal(err)
					}
				}
				if _, err := pool.Exec(ctx, `
     INSERT INTO worker_pool (workspace_id, squad_id, name, last_position) VALUES (gen_random_uuid(), gen_random_uuid(), 'kiro', 2);
     INSERT INTO kiro_credential_assignment (workspace_id, task_id, source_agent_id, key_fingerprint, quota_exhausted) VALUES (gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), 'test-fingerprint', true)`); err != nil {
					t.Fatal(err)
				}
				if err := pool.QueryRow(ctx, "SELECT 'kiro_credential_exhausted'::regclass::oid").Scan(&originalOID); err != nil {
					t.Fatal(err)
				}
			}
			options := runOptions{Direction: "up", Files: files, SchemaMigrationsTable: schema + ".schema_migrations", AdvisoryLockKey: int64(rand.Uint64()&0x7fffffffffffffff) | 1, Hooks: hooksForDirection("up")}
			for range 2 {
				if err := runMigrations(ctx, pool, options); err != nil {
					t.Fatal(err)
				}
			}
			for _, version := range versions {
				var recorded bool
				if err := pool.QueryRow(ctx, "SELECT EXISTS (SELECT 1 FROM schema_migrations WHERE version=$1)", version).Scan(&recorded); err != nil || !recorded {
					t.Fatalf("migration %s recorded=%t: %v", version, recorded, err)
				}
			}
			assertIndexValidity(t, pool, schema, "kiro_credential_exhausted", true)
			if existing {
				var position int
				if err := pool.QueryRow(ctx, "SELECT last_position FROM worker_pool WHERE name='kiro'").Scan(&position); err != nil || position != 2 {
					t.Fatalf("rotation position=%d: %v", position, err)
				}
				var exhausted bool
				if err := pool.QueryRow(ctx, "SELECT quota_exhausted FROM kiro_credential_assignment WHERE key_fingerprint='test-fingerprint'").Scan(&exhausted); err != nil || !exhausted {
					t.Fatalf("quota state=%t: %v", exhausted, err)
				}
				var currentOID uint32
				if err := pool.QueryRow(ctx, "SELECT 'kiro_credential_exhausted'::regclass::oid").Scan(&currentOID); err != nil || currentOID != originalOID {
					t.Fatalf("existing index replaced: %v", err)
				}
				var legacyCount int
				if err := pool.QueryRow(ctx, "SELECT count(*) FROM schema_migrations WHERE version < '657'").Scan(&legacyCount); err != nil || legacyCount != 12 {
					t.Fatalf("legacy migration count=%d: %v", legacyCount, err)
				}
			}
		})
	}
}
