#!/usr/bin/env python3
"""Grant Jira and Confluence access to the existing Internal Admin squad's agents."""
import argparse
import fcntl
import importlib.util
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('architect', ROOT / 'configure-architect.py')
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)
START = '<!-- fiska:admin-tools:start -->'
END = '<!-- fiska:admin-tools:end -->'


def configure(deployment):
    a.ROOT = deployment
    command = a.command
    squad = a.unique(command('squad', 'list'), 'Software Development Management')
    if not squad:
        raise RuntimeError('Configure the Software Development Management squad first.')
    members = command('squad', 'member', 'list', squad['id'])
    ids = {m['member_id'] for m in members if m['member_type'] == 'agent'}
    if not ids:
        raise RuntimeError('Admin squad has no agent members.')
    agents = [command('agent', 'get', ident) for ident in sorted(ids)]
    if any(x['name'] == 'background-learner' for x in agents):
        raise RuntimeError('Background learner cannot receive admin tools.')
    workspace_ids = {x['workspace_id'] for x in agents}
    if len(workspace_ids) != 1:
        raise RuntimeError('Admin roles must belong to one workspace.')
    servers = command('workspace', 'mcp', 'list', workspace_ids.pop())
    wanted = [a.unique(servers, name) for name in ('factory-jira', 'factory-jira-triage',
                                                 'factory-confluence', 'factory-confluence-write')]
    if any(x is None for x in wanted):
        raise RuntimeError('Set up the Jira and Confluence relay tools first.')
    block = START + '\n' + (ROOT / 'admin-tools-policy.md').read_text().strip() + '\n' + END
    plans = []
    for row in [*agents, command('squad', 'get', squad['id'])]:
        kind = 'agent' if row['id'] in ids else 'squad'
        before = row.get('instructions') or ''
        if START in before or END in before:
            if START not in before or END not in before or before[before.index(START):before.index(END)+len(END)] != block:
                raise RuntimeError('Admin policy changed; reconcile before reinstalling.')
            after = before
        else:
            after = before.rstrip() + '\n\n' + block
        attached = command('agent', 'mcp', 'list', row['id']) if kind == 'agent' else []
        enabled = {s['id'] for s in attached if s.get('enabled', True)}
        missing = [s for s in wanted if s['id'] not in enabled] if kind == 'agent' else []
        if after != before or missing:
            plans.append(dict(kind=kind, id=row['id'], name=row['name'], before=before, after=after,
                              attachments=attached, missing=[s['id'] for s in missing]))
    if plans:
        backup = deployment / 'backups'
        backup.mkdir(exist_ok=True)
        a.provision.write_state(Path(tempfile.mkdtemp(prefix='admin-access-', dir=backup)) / 'changes.json', plans)
    for p in plans:
        if (command(p['kind'], 'get', p['id']).get('instructions') or '') != p['before']:
            raise RuntimeError('Concurrent instruction edit; reconcile before retrying.')
        for ident in p['missing']:
            command('agent', 'mcp', 'add', p['id'], ident)
        if p['after'] != p['before']:
            command(p['kind'], 'update', p['id'], '--instructions', p['after'])
        if command(p['kind'], 'get', p['id']).get('instructions') != p['after']:
            raise RuntimeError('Admin instruction readback failed.')
    for row in agents:
        enabled = {s['id'] for s in command('agent', 'mcp', 'list', row['id']) if s.get('enabled', True)}
        if any(s['id'] not in enabled for s in wanted):
            raise RuntimeError('Admin MCP readback failed.')
    print(json.dumps({'admin_agents': [x['name'] for x in agents], 'changed': len(plans)}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deployment-dir', type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        with (args.deployment_dir / '.operation.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            configure(args.deployment_dir.resolve())
    except (ValueError, RuntimeError, OSError, KeyError) as error:
        print(f'Admin setup failed: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
