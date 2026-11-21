#!/usr/bin/env python3
"""Remove owned QA Docker resources while the daemon holds the issue cleanup guard."""
import json
from pathlib import Path
import subprocess
import sys
import uuid

DOCKER = ['/usr/local/bin/docker', '--context', 'multica-qa']
ISSUE_LABEL = 'com.fiska.multica.issue'
WORKSPACE_LABEL = 'com.fiska.multica.workspace'
PROJECT_LABEL = 'com.docker.compose.project'


def docker(*args):
    result = subprocess.run([*DOCKER, *args], capture_output=True, text=True, timeout=90)
    if result.returncode:
        raise RuntimeError('Docker operation failed: ' + args[0])
    return result.stdout


def inspect(kind, ids):
    if not ids:
        return []
    # Bound argv size for long-lived installations.
    rows = []
    for i in range(0, len(ids), 50):
        rows.extend(json.loads(docker(kind, 'inspect', *ids[i:i + 50])))
    return rows


def labels(kind, resource):
    return (resource.get('Config', {}).get('Labels') if kind in ('container', 'image')
            else resource.get('Labels')) or {}


def inside(path, roots):
    if not path or not Path(path).is_absolute():
        return False
    p = Path(path).resolve()
    return any(p.is_relative_to(Path(root).resolve()) for root in roots)


def plan(resources, issue, workspace, roots):
    prefix = 'mc-' + issue.replace('-', '') + '-'
    projects = set()
    for row in resources['container']:
        tags = labels('container', row)
        project = tags.get(PROJECT_LABEL, '')
        if project.startswith(prefix):
            projects.add(project)
        elif project and inside(tags.get(PROJECT_LABEL + '.working_dir'), roots):
            # Adopt legacy Compose resources only with a proven workspace path.
            projects.add(project)
    # A legacy project used from another root is shared and cannot be reclaimed here.
    for row in resources['container']:
        tags = labels('container', row)
        project = tags.get(PROJECT_LABEL, '')
        if (project in projects and not project.startswith(prefix)
                and not inside(tags.get(PROJECT_LABEL + '.working_dir'), roots)):
            projects.discard(project)
    result = {kind: [] for kind in resources}
    for kind, rows in resources.items():
        for row in rows:
            tags = labels(kind, row)
            project = tags.get(PROJECT_LABEL, '')
            owned = (tags.get(ISSUE_LABEL) == issue and tags.get(WORKSPACE_LABEL) == workspace)
            if owned or project.startswith(prefix) or project in projects:
                result[kind].append(row['Name'] if kind == 'volume' else row['Id'])
    return result


def cleanup(payload):
    issue = str(uuid.UUID(payload['issue_id']))
    workspace = str(uuid.UUID(payload['workspace_id']))
    roots = payload['roots']
    if not isinstance(roots, list) or not roots or any(not isinstance(p, str) or not Path(p).is_absolute() for p in roots):
        raise ValueError('Invalid owned roots')
    listing = subprocess.run(['/usr/local/bin/docker', 'context', 'ls', '--format', '{{.Name}}'],
                             capture_output=True, text=True, timeout=15)
    if listing.returncode:
        raise RuntimeError('Cannot inspect Docker contexts')
    contexts = listing.stdout.splitlines()
    if 'multica-qa' not in contexts:
        return {}
    resources = {}
    for kind, command in [('container', ['ps', '-aq']), ('image', ['image', 'ls', '-aq']),
                          ('network', ['network', 'ls', '-q']), ('volume', ['volume', 'ls', '-q'])]:
        resources[kind] = inspect(kind, sorted(set(docker(*command).split())))
    selected = plan(resources, issue, workspace, roots)
    # Never delete an image, network or volume referenced by an unrelated container.
    owned = set(selected['container'])
    others = [r for r in resources['container'] if r['Id'] not in owned]
    shared_images = {r.get('Image') for r in others}
    shared_volumes = {m.get('Name') for r in others for m in r.get('Mounts', []) if m.get('Type') == 'volume'}
    shared_networks = {n.get('NetworkID') for r in others for n in r.get('NetworkSettings', {}).get('Networks', {}).values()}
    for cid in selected['container']:
        docker('container', 'rm', '--force', '--volumes', cid)
    for kind, shared in [('network', shared_networks), ('volume', shared_volumes), ('image', shared_images)]:
        for rid in selected[kind]:
            if rid in shared:
                continue
            # Docker's own in-use checks remain enabled; no force for data or images.
            docker(kind, 'rm', rid)
    return {kind: len(ids) for kind, ids in selected.items()}


if __name__ == '__main__':
    try:
        cleanup(json.load(sys.stdin))
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError):
        sys.exit('Acceptance resource cleanup incomplete; retry required. Docker details omitted.')
