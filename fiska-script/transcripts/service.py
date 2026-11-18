"""Bounded transcript intake and content-bound human approval before action dispatch."""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
import urllib.parse
import urllib.request
import uuid

from docx_text import DocumentError, MAX_DOCX, extract as extract_docx

MAX_FILE = 128 * 1024
KINDS = {'jira', 'confluence', 'email', 'other'}
FIELDS = {'title', 'kind', 'proposal', 'source_quote', 'blocker', 'owner', 'resume'}


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class API:
    def __init__(self, config):
        self.config = config
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, path, body=None, key=None, method=None):
        if not path.startswith('/api/') or '\n' in path:
            raise ValueError('invalid_api_path')
        headers = {'Authorization': 'Bearer ' + self.config['token'],
                   'X-Workspace-ID': self.config['workspace_id'], 'Content-Type': 'application/json'}
        if key:
            headers['Idempotency-Key'] = key
        req = urllib.request.Request(self.config['server_url'] + path,
                                     data=None if body is None else canonical(body).encode(),
                                     headers=headers, method=method)
        with self.opener.open(req, timeout=30) as response:
            raw = response.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise ValueError('response_too_large')
            return json.loads(raw) if raw else {}

    def get(self, path):
        return self.request(path)


class Store:
    def __init__(self, path, capacity=100):
        if not 1 <= capacity <= 500:
            raise ValueError('invalid_capacity')
        self.capacity = capacity
        self.db = sqlite3.connect(path, timeout=30, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
          PRAGMA journal_mode=WAL;
          PRAGMA synchronous=FULL;
          CREATE TABLE IF NOT EXISTS files (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, text TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'pending', request TEXT, run_id TEXT, error TEXT);
          CREATE TABLE IF NOT EXISTS actions (
            id TEXT PRIMARY KEY, source TEXT NOT NULL, draft TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'review', issue_id TEXT, issue_attempted INTEGER DEFAULT 0,
            request TEXT, run_id TEXT, result TEXT, error TEXT);
          CREATE TABLE IF NOT EXISTS notices (id TEXT PRIMARY KEY, attempted INTEGER DEFAULT 0);
          CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
          CREATE UNIQUE INDEX IF NOT EXISTS one_extraction ON files((1)) WHERE state='extracting';
          CREATE UNIQUE INDEX IF NOT EXISTS one_execution ON actions((1)) WHERE state='executing';
        ''')

    def enqueue(self, name, text):
        ident = digest(text)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            if self.db.execute('SELECT 1 FROM files WHERE id=?', (ident,)).fetchone():
                return ident
            count = self.db.execute("SELECT count(*) FROM files WHERE state IN ('pending','extracting')").fetchone()[0]
            if count >= self.capacity:
                return None
            # Also bound unresolved action reviews; files stay on disk while backpressured.
            if self.db.execute("SELECT count(*) FROM actions WHERE state NOT IN ('done','cancelled','superseded')").fetchone()[0] >= self.capacity * 20:
                return None
            self.db.execute('INSERT INTO files(id,name,text) VALUES(?,?,?)', (ident, name, text))
            return ident
        finally:
            self.db.execute('COMMIT')


def scan(store, inbox, now=None):
    now = time.time() if now is None else now
    errors = []
    for path in sorted(Path(inbox).iterdir()):
        if path.suffix.lower() not in {'.txt', '.md', '.vtt', '.srt', '.docx'} or path.name.startswith(('.', '~$')):
            continue
        fd = None
        limit = MAX_DOCX if path.suffix.lower() == '.docx' else MAX_FILE
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or now - info.st_mtime < 10:
                continue
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                raise ValueError('file_too_large_or_not_regular')
            with os.fdopen(fd, 'rb', closefd=False) as source:
                raw = source.read(limit + 1)
            after = os.fstat(fd)
            if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
                continue
            if len(raw) != after.st_size or len(raw) > limit:
                raise ValueError('file_changed_or_too_large')
            text = extract_docx(raw, MAX_FILE) if path.suffix.lower() == '.docx' else raw.decode('utf-8-sig')
            if not text.strip() or '\x00' in text:
                raise ValueError('empty_or_binary_file')
            if store.enqueue(path.name, text) is None:
                break
        except (ValueError, OSError, UnicodeError) as error:
            errors.append({'file': path.name, 'error': str(error) if isinstance(error, DocumentError) else type(error).__name__})
        finally:
            if fd is not None:
                os.close(fd)
    return errors


def output(value):
    if isinstance(value, dict):
        value = value.get('output', value.get('summary', value))
    if isinstance(value, str):
        value = value.strip()
        if value.startswith('```json') and value.endswith('```'):
            value = value[7:-3].strip()
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError('invalid_agent_output')
    return value


def validate_action(value):
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ValueError('invalid_action_fields')
    if any(not isinstance(v, str) for v in value.values()) or len(canonical(value)) > 24000:
        raise ValueError('invalid_action_size')
    if value['kind'] not in KINDS or any(not value[k].strip() for k in ('title', 'proposal', 'source_quote', 'owner', 'resume')):
        raise ValueError('missing_action_details')
    if len(value['title']) > 180:
        raise ValueError('action_title_too_long')
    return value


def guard(api, config):
    if api.get('/api/config').get('server_version') != config['server_version']:
        raise ValueError('backend_changed_rerun_setup')
    project = api.get('/api/projects/' + config['project_id'])
    if project.get('visibility') != 'private':
        raise ValueError('review_project_must_be_private')
    workflows = api.get('/api/issue-workflows')['workflows']
    workflow = next((w for w in workflows if w['id'] == project.get('workflow_id')), None)
    if not workflow or any(s.get('handler', {}).get('type', 'none') != 'none' for s in workflow['steps']):
        raise ValueError('review_workflow_must_have_no_handlers')
    for role in ('intake', 'executor'):
        pilot = api.get('/api/autopilots/' + config[role]['id'])['autopilot']
        if (pilot.get('project_id') != config['project_id'] or pilot.get('execution_mode') != 'run_only'
                or pilot.get('assignee_id') != config[role]['agent_id']
                or digest(pilot.get('description', '')) != config[role]['instructions_digest']):
            raise ValueError('autopilot_scope_changed')


def extraction(api, store, config):
    row = store.db.execute("SELECT * FROM files WHERE state='extracting'").fetchone()
    if not row:
        row = store.db.execute("SELECT * FROM files WHERE state='pending' ORDER BY rowid LIMIT 1").fetchone()
        if not row:
            return
        store.db.execute("UPDATE files SET state='extracting',request=? WHERE id=? AND state='pending'",
                         (canonical({'event_type': 'transcript.intake', 'event_payload': {'source_id': row['id'],
                            'filename': row['name'], 'transcript': row['text']}}), row['id']))
        row = store.db.execute('SELECT * FROM files WHERE id=?', (row['id'],)).fetchone()
    if not row['run_id']:
        reply = api.request(config['intake']['webhook_path'], json.loads(row['request']), 'transcript:' + row['id'])
        if not reply.get('run_id'):
            raise ValueError('intake_dispatch_pending')
        store.db.execute('UPDATE files SET run_id=? WHERE id=?', (reply['run_id'], row['id']))
        return
    run = api.get(f'/api/autopilots/{config["intake"]["id"]}/runs/{row["run_id"]}')
    if run['status'] in ('failed', 'cancelled', 'skipped'):
        store.db.execute("UPDATE files SET state='blocked',error='intake_run_failed' WHERE id=?", (row['id'],))
    elif run['status'] == 'completed':
        try:
            values = output(run.get('result'))['actions']
            if not isinstance(values, list) or len(values) > 20:
                raise ValueError('too_many_actions')
            for value in values:
                validate_action(value)
                if value['source_quote'] not in row['text']:
                    raise ValueError('unsupported_source_quote')
        except (ValueError, KeyError, TypeError):
            store.db.execute("UPDATE files SET state='blocked',error='invalid_extraction_output' WHERE id=?", (row['id'],))
            return
        store.db.execute('BEGIN IMMEDIATE')
        try:
            unresolved = store.db.execute("SELECT count(*) FROM actions WHERE state NOT IN ('done','cancelled','superseded')").fetchone()[0]
            if unresolved + len(values) > store.capacity * 20:
                store.db.execute('COMMIT')
                return
            for index, value in enumerate(values):
                ident = digest(row['id'] + ':' + str(index))
                store.db.execute('INSERT OR IGNORE INTO actions(id,source,draft) VALUES(?,?,?)',
                                 (ident, row['id'], canonical(value)))
            store.db.execute("UPDATE files SET state='reviewed',error=NULL WHERE id=?", (row['id'],))
            store.db.execute('COMMIT')
        except BaseException:
            store.db.execute('ROLLBACK')
            raise


def parse_review(issue, row, config):
    if issue.get('project_id') != config['project_id']:
        raise ValueError('review_moved_project')
    description = issue.get('description') or ''
    matches = re.findall(r'```json\s*\n(.*?)\n```', description, re.S)
    if len(matches) != 1 or f'Transcript action: {row["id"]}' not in description:
        raise ValueError('review_format_changed')
    draft = validate_action(json.loads(matches[0]))
    fingerprint = digest(canonical({'issue_id': issue['id'], 'title': issue['title'],
                                    'description': description, 'source': row['source']}))
    return draft, fingerprint


def accepted(comments, owner_id, fingerprint):
    commands = [c for c in comments if c.get('author_type') == 'member' and c.get('author_id') == owner_id
                and not c.get('source_task_id') and not c.get('deleted_at')
                and c.get('content', '').strip().startswith('/action ')]
    if not commands:
        return None
    latest = max(commands, key=lambda c: (c.get('updated_at') or c['created_at'], c['id']))
    return latest if latest['content'].strip() == '/action approve ' + fingerprint else None


def notice(api, store, issue_id, key, text):
    marker = 'Transcript automation receipt: ' + key
    store.db.execute('INSERT OR IGNORE INTO notices(id) VALUES(?)', (key,))
    comments = api.get('/api/issues/' + issue_id + '/comments')
    if any(marker in c.get('content', '') for c in comments):
        return
    if not store.db.execute('UPDATE notices SET attempted=1 WHERE id=? AND attempted=0', (key,)).rowcount:
        return
    # Uncertain comment creation is never blindly repeated.
    api.request('/api/issues/' + issue_id + '/comments', {'content': text + '\n\n' + marker})


def tickets(api, store, config):
    rows = store.db.execute('SELECT * FROM actions WHERE issue_id IS NULL ORDER BY rowid LIMIT 20').fetchall()
    for row in rows:
        marker = 'Transcript action: ' + row['id']
        query = urllib.parse.urlencode({'q': row['id'], 'project_id': config['project_id'], 'include_closed': 'true', 'limit': 50})
        matches = [i for i in api.get('/api/issues/search?' + query)['issues']
                   if i.get('project_id') == config['project_id'] and marker in (i.get('description') or '')]
        if len(matches) == 1:
            store.db.execute('UPDATE actions SET issue_id=?,error=NULL WHERE id=?', (matches[0]['id'], row['id']))
            continue
        if matches or row['issue_attempted']:
            store.db.execute("UPDATE actions SET error='review_creation_needs_reconciliation' WHERE id=?", (row['id'],))
            continue
        draft = json.loads(row['draft'])
        if draft['kind'] in ('email', 'other') and not draft['blocker']:
            draft['blocker'] = 'No execution connector for this action. Preserve the draft and resolve access first.'
        description = (f'{marker}\nSource transcript: `{row["source"]}`\n\n'
                       'Review the exact proposal below. Edit the JSON to resolve missing details. '
                       'The service posts an approval command for ready actions. Internal review is not '
                       'approval to create a Jira ticket or begin development.\n\n```json\n' +
                       json.dumps(draft, ensure_ascii=False, indent=2) + '\n```')
        if not store.db.execute('UPDATE actions SET issue_attempted=1 WHERE id=? AND issue_attempted=0', (row['id'],)).rowcount:
            continue
        issue = api.request('/api/issues', {'title': draft['title'], 'description': description,
                            'project_id': config['project_id'], 'status': 'blocked' if draft['blocker'] else 'awaiting_approval',
                            'priority': 'none', 'allow_duplicate': True})
        store.db.execute('UPDATE actions SET issue_id=? WHERE id=?', (issue['id'], row['id']))


def set_status(api, issue, status):
    if issue.get('status') != status:
        api.request('/api/issues/' + issue['id'], {'status': status}, method='PATCH')


def reviews(api, store, config):
    for row in store.db.execute("SELECT * FROM actions WHERE issue_id IS NOT NULL AND state='review' ORDER BY rowid").fetchall():
        issue = api.get('/api/issues/' + row['issue_id'])
        if issue.get('project_id') != config['project_id']:
            store.db.execute("UPDATE actions SET error='review_moved_project' WHERE id=?", (row['id'],))
            continue
        if issue.get('status') in ('done', 'cancelled'):
            store.db.execute("UPDATE actions SET state='cancelled' WHERE id=? AND state='review'", (row['id'],))
            continue  # Closing a proposal is never execution or approval.
        try:
            draft, fingerprint = parse_review(issue, row, config)
        except (ValueError, TypeError):
            set_status(api, issue, 'blocked')
            notice(api, store, issue['id'], row['id'] + ':invalid',
                   'Blocked: proposal JSON is invalid or the review moved project. Owner: installer. Restore its format and project; no action was dispatched.')
            continue
        reason = draft['blocker']
        if draft['kind'] in ('email', 'other'):
            reason = reason or 'No execution connector is configured for this action kind.'
        if reason:
            set_status(api, issue, 'blocked')
            notice(api, store, issue['id'], fingerprint + ':blocked',
                   f'Blocked: {reason}\nOwner: {draft["owner"]}\nResume: {draft["resume"]}\n'
                   'Resolve the prerequisite and edit the proposal JSON. A new approval is then required.')
            continue
        set_status(api, issue, 'awaiting_approval')
        notice(api, store, issue['id'], fingerprint + ':approval',
               'Ready, awaiting your approval. Review the destination and full content above, then comment exactly:\n\n'
               f'`/action approve {fingerprint}`\n\nOnly the installation owner can approve. '
               'Edits invalidate this command. Done status, transcript text and agent comments do not approve. '
               'To withdraw a pending approval, comment `/action reject`. Dispatch freezes the approved snapshot.')
        comments = api.get('/api/issues/' + issue['id'] + '/comments')
        approval = accepted(comments, config['owner_id'], fingerprint)
        if not approval:
            continue
        # Freeze the observed, approved snapshot; do not dispatch changed content with old consent.
        fresh = api.get('/api/issues/' + issue['id'])
        if fresh.get('status') in ('done', 'cancelled') or parse_review(fresh, row, config)[1] != fingerprint:
            continue
        if not accepted(api.get('/api/issues/' + issue['id'] + '/comments'), config['owner_id'], fingerprint):
            continue
        payload = {'event_type': 'transcript.approved_action', 'event_payload': {
            'action_id': row['id'], 'review_issue_id': issue['id'], 'approval_digest': fingerprint,
            'approval_comment_id': approval['id'], 'approved_by': approval['author_id'], 'action': draft}}
        try:
            store.db.execute("UPDATE actions SET state='executing',request=? WHERE id=? AND state='review'",
                             (canonical(payload), row['id']))
        except sqlite3.IntegrityError:
            return  # Another approved action is still executing.
        set_status(api, fresh, 'in_progress')
        return


def execution(api, store, config):
    row = store.db.execute("SELECT * FROM actions WHERE state='executing'").fetchone()
    if not row:
        return
    if not row['run_id'] and not row['result']:
        reply = api.request(config['executor']['webhook_path'], json.loads(row['request']), 'transcript-action:' + row['id'])
        if not reply.get('run_id'):
            raise ValueError('execution_dispatch_pending')
        store.db.execute('UPDATE actions SET run_id=? WHERE id=?', (reply['run_id'], row['id']))
        return
    if row['result']:
        result = json.loads(row['result'])
    else:
        run = api.get(f'/api/autopilots/{config["executor"]["id"]}/runs/{row["run_id"]}')
        if run['status'] not in ('completed', 'failed', 'cancelled', 'skipped'):
            return
        try:
            result = output(run.get('result'))
            if result.get('outcome') not in ('done', 'blocked') or not isinstance(result.get('summary'), str):
                raise ValueError('invalid_execution_result')
            receipts = result.get('receipts')
            if not isinstance(receipts, list) or any(not isinstance(x, str) or not x.startswith('https://') for x in receipts):
                raise ValueError('invalid_receipts')
            if result['outcome'] == 'done' and not receipts:
                raise ValueError('missing_receipt')
            if result['outcome'] == 'blocked' and any(not result.get(k) for k in ('blocker', 'owner', 'resume')):
                raise ValueError('missing_blocker_details')
        except (ValueError, TypeError):
            result = {'outcome': 'blocked', 'summary': 'Execution failed or returned no valid receipt. A write may have completed.',
                      'receipts': [], 'blocker': 'Outcome needs reconciliation; automatic rerun is disabled.',
                      'owner': 'David', 'resume': 'Inspect the run and external records before any new action.'}
        store.db.execute('UPDATE actions SET result=? WHERE id=?', (canonical(result), row['id']))
    issue = api.get('/api/issues/' + row['issue_id'])
    if issue.get('project_id') != config['project_id']:
        raise ValueError('execution_review_moved_project')
    notice(api, store, issue['id'], row['id'] + ':result',
           'Approved action result:\n\n```json\n' + json.dumps(result, indent=2) + '\n```\n\n'
           f'Execution run: `{row["run_id"]}`. This action will not automatically execute again.')
    set_status(api, issue, result['outcome'])
    store.db.execute('UPDATE actions SET state=? WHERE id=?', (result['outcome'], row['id']))


def replan(api, store, config, ident, reason):
    if not reason or not reason.strip():
        raise ValueError('reconciliation_reason_required')
    row = store.db.execute('SELECT * FROM actions WHERE id=?', (ident,)).fetchone()
    if not row or row['state'] != 'blocked' or not row['issue_id']:
        raise ValueError('replan_requires_blocked_execution')
    issue = api.get('/api/issues/' + row['issue_id'])
    draft, _ = parse_review(issue, row, config)
    draft['blocker'] = ''
    draft['resume'] = 'Operator reconciliation: ' + reason + '\n' + draft['resume']
    new_id = uuid.uuid4().hex
    store.db.execute('BEGIN IMMEDIATE')
    try:
        if not store.db.execute("UPDATE actions SET state='superseded' WHERE id=? AND state='blocked'", (ident,)).rowcount:
            raise ValueError('action_already_replanned')
        store.db.execute('INSERT INTO actions(id,source,draft) VALUES(?,?,?)', (new_id, row['source'], canonical(draft)))
        store.db.execute('COMMIT')
    except BaseException:
        store.db.execute('ROLLBACK')
        raise
    # The new ticket ID and approval hash ensure the old approval cannot authorize another run.
    return {'action_id': new_id, 'state': 'review', 'previous_action_id': ident}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['serve', 'status', 'replan'])
    parser.add_argument('action_id', nargs='?')
    parser.add_argument('--reason')
    parser.add_argument('--config', default='/run/transcripts/config.json')
    parser.add_argument('--data', default='/data')
    parser.add_argument('--inbox', default='/inbox')
    args = parser.parse_args()
    os.umask(0o077)
    config = json.loads(Path(args.config).read_text())
    store = Store(Path(args.data) / 'queue.sqlite3', config.get('capacity', 100))
    if args.action == 'replan':
        api = API(config)
        guard(api, config)
        print(canonical(replan(api, store, config, args.action_id, args.reason)))
        return
    if args.action == 'status':
        print(json.dumps({'files': [dict(r) for r in store.db.execute('SELECT id,name,state,error FROM files')],
                          'actions': [dict(r) for r in store.db.execute('SELECT id,state,issue_id,run_id,error FROM actions')],
                          'heartbeat': [dict(r) for r in store.db.execute('SELECT * FROM settings')]}, indent=2))
        return
    with (Path(args.data) / 'worker.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        api = API(config)
        while True:
            report = {'at': datetime.now(timezone.utc).isoformat()}
            try:
                guard(api, config)
                report['files'] = scan(store, args.inbox)
                extraction(api, store, config)
                tickets(api, store, config)
                execution(api, store, config)
                reviews(api, store, config)
                report['ok'] = True
            except Exception as error:
                report.update(ok=False, error=type(error).__name__)
            store.db.execute("INSERT INTO settings VALUES('heartbeat',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (canonical(report),))
            print(canonical(report), flush=True)
            time.sleep(15)


if __name__ == '__main__':
    main()
