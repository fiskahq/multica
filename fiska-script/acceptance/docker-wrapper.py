#!/usr/bin/env python3
"""Assign task-created Docker resources to their Multica issue."""
import hashlib
import os
from pathlib import Path
import re
import sys

REAL_DOCKER = '/usr/local/bin/docker'
ISSUE_LABEL = 'com.fiska.multica.issue'
WORKSPACE_LABEL = 'com.fiska.multica.workspace'
UUID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z')


def owned_args(args, issue, workspace, cwd, environ):
    if not issue:
        return args, dict(environ)
    if not UUID.fullmatch(issue) or not UUID.fullmatch(workspace):
        raise ValueError('Invalid task identity')
    env = dict(environ)
    # Respect global context/TLS options; the wrapper only changes resource ownership.
    pos = 0
    with_value = {'--context', '-c', '--host', '-H', '--config', '--log-level', '-l',
                  '--tlscacert', '--tlscert', '--tlskey'}
    while pos < len(args) and args[pos].startswith('-'):
        pos += 2 if args[pos] in with_value else 1
    if pos >= len(args):
        return args, env
    command = args[pos]
    labels = ['--label', ISSUE_LABEL + '=' + issue, '--label', WORKSPACE_LABEL + '=' + workspace]
    offset = pos + 1
    if command == 'compose':
        tail = args[offset:]
        project = env.get('COMPOSE_PROJECT_NAME', '')
        clean = []
        i = 0
        # Compose options occur before its subcommand. Preserve subcommand flags.
        options_with_value = {'-f', '--file', '--env-file', '--profile', '--project-directory',
                              '--parallel', '--progress', '--ansi'}
        while i < len(tail) and tail[i].startswith('-'):
            arg = tail[i]
            if arg in ('-p', '--project-name'):
                if i + 1 >= len(tail):
                    raise ValueError('Missing Compose project name')
                project = tail[i + 1]; i += 2
            elif arg.startswith('--project-name='):
                project = arg.split('=', 1)[1]; i += 1
            elif arg.startswith('-p') and not arg.startswith('--'):
                project = arg[2:]; i += 1
            else:
                count = 2 if arg in options_with_value else 1
                clean.extend(tail[i:i + count]); i += count
        prefix = 'mc-' + issue.replace('-', '') + '-'
        # Nested calls may inherit the already-scoped Compose project.
        if not project.startswith(prefix):
            suffix = hashlib.sha256((project or str(Path(cwd).resolve())).encode()).hexdigest()[:12]
            project = prefix + suffix
        env['COMPOSE_PROJECT_NAME'] = project
        return args[:offset] + ['--project-name', project] + clean + tail[i:], env
    if command in ('run', 'create', 'build'):
        return args[:offset] + labels + args[offset:], env
    if command in ('container', 'image', 'network', 'volume', 'buildx') and offset < len(args):
        subcommand = args[offset]
        if ((command == 'container' and subcommand in ('run', 'create'))
                or (command in ('network', 'volume') and subcommand == 'create')
                or (command in ('image', 'buildx') and subcommand == 'build')):
            offset += 1
            return args[:offset] + labels + args[offset:], env
    return args, env


def main():
    args, env = owned_args(sys.argv[1:], os.environ.get('MULTICA_ISSUE_ID', ''),
                           os.environ.get('MULTICA_WORKSPACE_ID', ''), os.getcwd(), os.environ)
    os.execve(REAL_DOCKER, [REAL_DOCKER, *args], env)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError):
        sys.exit('Docker ownership setup failed; command was not run.')
