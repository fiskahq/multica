#!/usr/bin/env python3
"""Configure workspace-wide Kiro credential rotation from installed worker IDs."""
import argparse
import json
from pathlib import Path
import subprocess
import uuid

ROOT = Path(__file__).resolve().parent


def ident(value):
    return str(uuid.UUID(value))


def configure(config_path, *, status=False, quota_task=None, reset_worker=None):
    state = json.loads((ROOT / "agent-state.json").read_text())
    config = json.loads(Path(config_path).read_text())
    workspace = ident(state["workspace_id"])
    workers = [(w["name"], ident(state["agents"][w["name"]]["id"]), w.get("enabled", True))
               for w in config["workers"]]
    if not workers or len({w[1] for w in workers}) != len(workers):
        raise ValueError("Expected distinct installed Kiro worker agents")
    ids = ",".join(f"'{w[1]}'::uuid" for w in workers)
    statements = ["BEGIN;"]
    if not status:
        statements.append(f"""
DO $$ BEGIN
  IF (SELECT count(DISTINCT a.custom_env->>'KIRO_API_KEY') FROM agent a
      JOIN agent_runtime r ON r.id=a.runtime_id AND r.workspace_id=a.workspace_id
      WHERE a.workspace_id='{workspace}' AND a.id IN ({ids}) AND a.archived_at IS NULL
        AND r.provider='kiro' AND COALESCE(a.custom_env->>'KIRO_API_KEY','')<>'') <> {len(workers)}
  THEN RAISE EXCEPTION 'Every credential source must be an active workspace Kiro agent with a distinct installed API key'; END IF;
END $$;
INSERT INTO kiro_credential_pool (workspace_id) VALUES ('{workspace}') ON CONFLICT DO NOTHING;
SELECT workspace_id FROM kiro_credential_pool WHERE workspace_id='{workspace}' FOR UPDATE;
DELETE FROM kiro_credential_member WHERE workspace_id='{workspace}';
""")
        for position, (_, agent, enabled) in enumerate(workers):
            statements.append(f"INSERT INTO kiro_credential_member (workspace_id,agent_id,position,enabled) VALUES ('{workspace}','{agent}',{position},{str(enabled).lower()});")
        if quota_task:
            task = ident(quota_task)
            statements.append(f"""
INSERT INTO kiro_credential_assignment (workspace_id,task_id,source_agent_id,key_fingerprint,quota_exhausted)
SELECT '{workspace}',t.id,m.agent_id,encode(sha256(convert_to(a.custom_env->>'KIRO_API_KEY','UTF8')),'hex'),true
FROM agent_task_queue t JOIN agent a ON a.id=t.agent_id
JOIN kiro_credential_member m ON m.workspace_id=a.workspace_id
JOIN agent source ON source.id=m.agent_id AND source.custom_env->>'KIRO_API_KEY'=a.custom_env->>'KIRO_API_KEY'
WHERE t.id='{task}' AND a.workspace_id='{workspace}' AND t.failure_reason='agent_error.provider_quota_limit'
ON CONFLICT (task_id) DO UPDATE SET quota_exhausted=true;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM kiro_credential_assignment WHERE workspace_id='{workspace}' AND task_id='{task}' AND quota_exhausted)
  THEN RAISE EXCEPTION 'Quota task does not identify an installed credential'; END IF;
END $$;
""")
        if reset_worker:
            matches = [agent for name, agent, _ in workers if name == reset_worker]
            if len(matches) != 1:
                raise ValueError("Unknown worker name")
            statements.append(f"""
UPDATE kiro_credential_assignment SET quota_exhausted=false
WHERE workspace_id='{workspace}' AND key_fingerprint=(SELECT encode(sha256(convert_to(custom_env->>'KIRO_API_KEY','UTF8')),'hex') FROM agent WHERE id='{matches[0]}');
""")
    statements.append("COMMIT;")
    statements.append(f"""
SELECT json_build_object('worker',a.name,'position',m.position,'enabled',m.enabled,
  'quota_exhausted',EXISTS(SELECT 1 FROM kiro_credential_assignment x WHERE x.workspace_id=m.workspace_id AND x.quota_exhausted
    AND x.key_fingerprint=encode(sha256(convert_to(a.custom_env->>'KIRO_API_KEY','UTF8')),'hex')))
FROM kiro_credential_member m JOIN agent a ON a.id=m.agent_id
WHERE m.workspace_id='{workspace}' ORDER BY m.position;
""")
    script = '''source "$1/common.sh"
compose exec -T postgres sh -c 'exec psql -X -q -At -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
'''
    result = subprocess.run(["bash", "-c", script, "bash", str(ROOT)], input="\n".join(statements),
                            text=True, capture_output=True, check=True)
    for line in result.stdout.splitlines():
        if line.startswith("{"):
            print(line)
    if not status:
        print("Every Kiro agent in this workspace now selects an available key at task dispatch. No 1Password reads.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", nargs="?", type=Path, default=ROOT / "agents.json")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--status", action="store_true")
    action.add_argument("--quota-task", help="Import a verified quota failure from before rotation was enabled")
    action.add_argument("--reset-worker", help="Re-enable this worker's current key after its quota is restored")
    args = parser.parse_args()
    configure(args.config, status=args.status, quota_task=args.quota_task, reset_worker=args.reset_worker)


if __name__ == "__main__":
    main()
