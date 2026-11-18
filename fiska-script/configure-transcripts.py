#!/usr/bin/env python3
"""Install private transcript review and per-action approval using native Multica Autopilots."""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + '.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def docker(deployment, *args, data=None):
    result = subprocess.run(['bash', '-c', 'source "$1/common.sh"\nshift\ncompose "$@"',
                             'bash', str(deployment), *args], input=data, text=True, capture_output=True,
                            env=dict(os.environ, TRANSCRIPT_SOURCE_DIR=str(ROOT / 'transcripts'),
                                     TRANSCRIPT_UID=str(os.getuid()), TRANSCRIPT_GID=str(os.getgid())))
    if result.returncode:
        raise RuntimeError('Transcript Docker operation failed; credentials and provider output omitted.')
    return result.stdout


def native(deployment, path, body=None):
    script = '''import json,sys,urllib.request
c=json.load(open('/home/node/.multica/config.json'));p=json.load(sys.stdin)
r=urllib.request.Request('http://backend:8080'+p['path'],data=None if p['body'] is None else json.dumps(p['body']).encode(),headers={'Authorization':'Bearer '+c['token'],'X-Workspace-ID':c['workspace_id'],'Content-Type':'application/json'})
with urllib.request.urlopen(r,timeout=30) as response: print(response.read().decode())'''
    return json.loads(docker(deployment, 'exec', '-T', 'runtime', 'python3', '-c', script,
                             data=json.dumps({'path': path, 'body': body})))


def tracked(rows, name, key, state, field='name'):
    found = [r for r in rows if r[field] == name]
    if len(found) > 1 or (found and found[0]['id'] != state.get(key)):
        raise RuntimeError('Untracked or ambiguous ' + name + '; reconcile setup state.')
    if state.get(key) and not found:
        raise RuntimeError('Tracked ' + name + ' was removed or renamed.')
    return found[0] if found else None


def install(deployment):
    architect, learner = load('configure-architect'), load('configure-learner')
    architect.ROOT = deployment
    command = architect.command
    save = architect.provision.write_state
    path = deployment / 'transcript-state.json'
    state = json.loads(path.read_text()) if path.exists() else {}
    version = native(deployment, '/api/config').get('server_version', '')
    learner.verify_backend(version)
    owner = native(deployment, '/api/me')['id']
    if state.get('owner_id', owner) != owner:
        raise RuntimeError('Approval owner changed; reconcile ownership before reinstalling.')
    manager = architect.unique(command('agent', 'list'), 'sdm-manager')
    if not manager:
        raise RuntimeError('Configure the admin squad first.')
    if state.get('workspace_id', manager['workspace_id']) != manager['workspace_id']:
        raise RuntimeError('Transcript setup belongs to another workspace.')
    state.update(workspace_id=manager['workspace_id'], owner_id=owner, server_version=version)
    save(path, state)
    workflows = command('workflow', 'list')
    workflow = tracked(workflows, 'Transcript Actions', 'workflow_id', state)
    if not workflow:
        statuses = native(deployment, '/api/issue-statuses')['statuses']
        approval = next((s for s in statuses if s['key'] == 'awaiting_approval' and not s.get('archived_at')), None)
        steps = [{'status_key': key} for key in ('backlog', 'todo', 'in_progress', 'in_review', 'blocked', 'done', 'cancelled')]
        steps.insert(2, {'status_key': 'awaiting_approval'} if approval else
                     {'new_status': {'name': 'Awaiting approval', 'category': 'unstarted'}})
        workflow = native(deployment, '/api/issue-workflows', {'name': 'Transcript Actions',
                          'description': 'Ready proposals wait for per-action approval; missing prerequisites are Blocked.', 'steps': steps})
        state['workflow_id'] = workflow['id']; save(path, state)
    project = tracked(command('project', 'list'), 'Transcript Actions', 'project_id', state, 'title')
    if not project:
        project = native(deployment, '/api/projects', {'title': 'Transcript Actions', 'visibility': 'private',
                         'description': 'Private transcript action proposals. External actions require explicit content-bound approval.'})
        state['project_id'] = project['id']; save(path, state)
    project = command('project', 'get', state['project_id'])
    if project.get('visibility') != 'private':
        raise RuntimeError('Transcript Actions must be private.')
    if project.get('workflow_id') != state['workflow_id']:
        if project.get('workflow_id'):
            raise RuntimeError('Transcript workflow changed; reconcile before setup.')
        native(deployment, '/api/projects/' + project['id'] + '/workflow', {'workflow_id': state['workflow_id']})
    # Internal Admin gets the same visible statuses without automatic handlers or status remapping.
    admin = next((p for p in command('project', 'list') if p['title'] == 'Internal Admin'), None)
    if admin:
        admin = command('project', 'get', admin['id'])
        if admin.get('workflow_id') not in (None, state['workflow_id']):
            raise RuntimeError('Internal Admin has a custom workflow; reconcile its approval stage first.')
        native(deployment, '/api/projects/' + admin['id'] + '/workflow', {'workflow_id': state['workflow_id']})
    runtime_ids = {r['id'] for r in command('runtime', 'list') if r.get('provider') == 'kiro' and r.get('status') == 'online' and not r.get('profile_id')}
    if len(runtime_ids) != 1:
        raise RuntimeError('Expected one online shared Kiro runtime.')
    agent = tracked(command('agent', 'list'), 'transcript-intake', 'agent_id', state)
    if not agent:
        agent = command('agent', 'create', '--name', 'transcript-intake', '--runtime-id', runtime_ids.pop(),
                        '--model', 'claude-opus-5.5', '--thinking-level', 'high', '--custom-args', '["--agent-engine","v3"]',
                        '--max-concurrent-tasks', '1', '--permission-mode', 'private',
                        '--instructions', (ROOT / 'transcripts/intake.md').read_text())
        state['agent_id'] = agent['id']; save(path, state)
    if command('agent', 'get', agent['id']).get('instructions') != (ROOT / 'transcripts/intake.md').read_text():
        raise RuntimeError('Transcript extraction instructions changed; reconcile before setup.')
    if command('agent', 'mcp', 'list', agent['id']):
        raise RuntimeError('Transcript extraction agent must have no MCP attachments.')
    state['intake'] = dict(state.get('intake', {}), agent_id=agent['id'])
    state['executor'] = dict(state.get('executor', {}), agent_id=manager['id'])
    for role, title, description in (
        ('intake', 'Transcript extraction', (ROOT / 'transcripts/intake.md').read_text()),
        ('executor', 'Approved transcript action', (ROOT / 'transcripts/execution.md').read_text())):
        key = role + '_pilot_id'
        pilot = tracked(command('autopilot', 'list')['autopilots'], title, key, state, 'title')
        if not pilot:
            pilot = command('autopilot', 'create', '--title', title, '--agent', state[role]['agent_id'],
                            '--mode', 'run_only', '--project', state['project_id'], '--description', description)
            pilot = pilot.get('autopilot', pilot)
            state[key] = pilot['id']; save(path, state)
        detail = command('autopilot', 'get', state[key], '--show-secrets')
        triggers = detail.get('triggers', [])
        if not state.get(role + '_trigger_id'):
            if triggers:
                raise RuntimeError('Untracked transcript trigger; reconcile setup.')
            trigger = command('autopilot', 'trigger-add', state[key], '--kind', 'webhook', '--label', title)
            state[role + '_trigger_id'] = trigger['id']; save(path, state)
            detail = command('autopilot', 'get', state[key], '--show-secrets')
            triggers = detail['triggers']
        if len(triggers) != 1 or triggers[0]['id'] != state[role + '_trigger_id'] or triggers[0]['kind'] != 'webhook':
            raise RuntimeError('Unexpected transcript trigger configuration.')
        trigger = triggers[0]
        if detail['autopilot'].get('description') != description:
            raise RuntimeError('Managed transcript Autopilot instructions changed; reconcile before reinstalling.')
        state[role].update(id=state[key], instructions_digest=hashlib.sha256(description.encode()).hexdigest(), webhook_path=trigger.get('webhook_path') or '/api/webhooks/autopilots/' + trigger['webhook_token'])
        save(path, state)
    credentials = json.loads(docker(deployment, 'exec', '-T', 'runtime', 'cat', '/home/node/.multica/config.json'))
    if credentials['workspace_id'] != state['workspace_id']:
        raise RuntimeError('Workspace credentials differ.')
    for relative in ('transcript-private', 'transcript-private/data', 'transcript-inbox'):
        (deployment / relative).mkdir(mode=0o700, parents=True, exist_ok=True)
    config = dict(state, server_url='http://backend:8080', token=credentials['token'], capacity=100)
    save(deployment / 'transcript-private/config.json', config)
    docker(deployment, '-f', str(ROOT / 'compose.transcripts.yml'), '--profile', 'transcripts',
           'up', '-d', '--no-deps', 'transcript-intake')
    print(json.dumps({'inbox': str(deployment / 'transcript-inbox'), 'project_id': state['project_id'],
                      'approval': 'one owner-approved content-bound comment per external action', 'runtime_restarted': False}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', default='install', choices=('install', 'status', 'stop', 'start', 'replan'))
    parser.add_argument('action_id', nargs='?')
    parser.add_argument('--reason')
    parser.add_argument('--deployment-dir', type=Path, default=ROOT)
    args = parser.parse_args(); deployment = args.deployment_dir.resolve()
    os.umask(0o077)
    try:
        if args.action == 'install':
            admin = load('configure-admin-access')
            with (deployment / '.operation.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                admin.configure(deployment)
                install(deployment)
        else:
            base = ('-f', str(ROOT / 'compose.transcripts.yml'), '--profile', 'transcripts')
            if args.action in ('stop', 'start'):
                print(docker(deployment, *base, args.action, 'transcript-intake'))
            elif args.action == 'replan':
                if not args.action_id or not args.reason:
                    raise ValueError('replan requires an action ID and --reason describing reconciliation')
                print(docker(deployment, *base, 'exec', '-T', 'transcript-intake', 'python3', '-B', '/app/service.py', 'replan', args.action_id, '--reason', args.reason))
            else:
                print(docker(deployment, *base, 'exec', '-T', 'transcript-intake', 'python3', '-B', '/app/service.py', 'status'))
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        print('Transcript setup failed: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
