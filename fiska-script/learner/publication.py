"""Publish reviewed lessons through native skills after an owner's explicit approval."""
import hashlib
import json
import re
import urllib.error
import uuid


def initialize(queue):
    queue.db.executescript('''
        CREATE TABLE IF NOT EXISTS learning_publications (
            task_id TEXT PRIMARY KEY, digest TEXT, approval_id TEXT, reviewer_id TEXT,
            targets TEXT, skill_id TEXT, skill_version INTEGER, content_hash TEXT,
            applied TEXT NOT NULL DEFAULT '[]', state TEXT NOT NULL DEFAULT 'waiting', error TEXT);
        CREATE TABLE IF NOT EXISTS learning_notices (
            notice_key TEXT PRIMARY KEY, comment_id TEXT, attempted INTEGER NOT NULL DEFAULT 0);
    ''')


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def section(description, title):
    matches = re.findall(r'^## ' + re.escape(title) + r'\s*\n(.*?)(?=^## |\Z)',
                         description, re.M | re.S)
    if len(matches) != 1 or not matches[0].strip():
        raise ValueError('review_sections_missing_or_ambiguous')
    return matches[0].strip()


def plan(issue, job, source_project):
    candidate = json.loads(job['candidate'])
    description = issue.get('description') or ''
    lesson = section(description, 'Proposed lesson')
    scope = section(description, 'Applicability')
    validation = section(description, 'Validation limits')
    name = 'learned-' + job['task_id']
    title = issue['title'].removeprefix('Review lesson: ')
    summary = f'Only for project {source_project["title"]}: {title}'
    content = (f'---\nname: {name}\ndescription: {json.dumps(summary)}\n---\n\n'
               f'# {title}\n\n## Scope\n\n'
               f'Apply only when working on Multica project {source_project["title"]} '
               f'(`{candidate["source_project_id"]}`). If the project cannot be confirmed, '
               'do not apply this lesson. This is guidance, not permission to bypass task instructions.\n\n'
               f'{scope}\n\n## Procedure\n\n{lesson}\n\n## Validation limits\n\n{validation}\n\n'
               f'Review ticket: {issue.get("identifier", issue["id"])}. '
               f'Source task: `{job["task_id"]}`.\n')
    if len(content) > 24000:
        raise ValueError('review_content_too_large')
    digest = sha(canonical({'issue_id': issue['id'], 'title': issue['title'],
                            'description': description, 'content': content,
                            'default_agent': candidate['source_agent_id']}))
    return {'digest': digest, 'name': name, 'description': summary, 'content': content,
            'default_agent': candidate['source_agent_id']}


def notice(api, queue, issue_id, key, text, comments):
    marker = f'Learning automation receipt: {key}'
    queue.db.execute('INSERT OR IGNORE INTO learning_notices(notice_key) VALUES(?)', (key,))
    found = next((c for c in comments if marker in c.get('content', '')), None)
    if found:
        queue.db.execute('UPDATE learning_notices SET comment_id=? WHERE notice_key=?', (found['id'], key))
        return
    claimed = queue.db.execute('''UPDATE learning_notices SET attempted=1
        WHERE notice_key=? AND attempted=0 AND comment_id IS NULL''', (key,)).rowcount
    if not claimed:
        return
    try:
        comment, _ = api.request(f'/api/issues/{issue_id}/comments', {'content': text + '\n\n' + marker})
        queue.db.execute('UPDATE learning_notices SET comment_id=? WHERE notice_key=?', (comment['id'], key))
    except (OSError, ValueError):
        # Comments have no idempotency key: search for the receipt before attempting any recovery.
        return


def approval(comments, owner_id, draft):
    directives = [c for c in comments if c.get('author_type') == 'member'
                  and c.get('author_id') == owner_id and not c.get('source_task_id')
                  and not c.get('deleted_at') and c.get('content', '').strip().startswith('/learning ')]
    if not directives:
        return None
    latest = max(directives, key=lambda c: (c.get('updated_at') or c['created_at'], c['id']))
    match = re.fullmatch(r'/learning approve ([0-9a-f]{64})(?: agents=([0-9a-f,-]+))?',
                         latest['content'].strip())
    if not match or match[1] != draft['digest']:
        return None
    targets = sorted(set(match[2].split(',') if match[2] else [draft['default_agent']]))
    if not 1 <= len(targets) <= 20:
        raise ValueError('invalid_target_agents')
    for ident in targets:
        if str(uuid.UUID(ident)) != ident:
            raise ValueError('invalid_target_agents')
    return latest, targets


def publish(api, queue, config, job, issue, draft, accepted):
    comment, targets = accepted
    ident = job['task_id']
    # The first approval freezes the publication. Repeated or competing approvals cannot widen it.
    queue.db.execute('''UPDATE learning_publications SET digest=?,approval_id=?,reviewer_id=?,targets=?,
        state='publishing',error=NULL WHERE task_id=? AND state='waiting' ''',
        (draft['digest'], comment['id'], comment['author_id'], canonical(targets), ident))
    receipt = queue.db.execute('SELECT * FROM learning_publications WHERE task_id=?', (ident,)).fetchone()
    if (receipt['digest'] != draft['digest'] or receipt['approval_id'] != comment['id']
            or json.loads(receipt['targets']) != targets):
        raise ValueError('approval_changed_after_publication_started')
    for target in targets:
        agent = api.get(f'/api/agents/{target}')
        if (agent.get('workspace_id') != config['workspace_id'] or agent.get('archived_at')
                or target == config['agent_id']):
            raise ValueError('target_agent_unavailable')
    metadata = {'source_task_id': ident, 'review_issue_id': issue['id'],
                'approval_comment_id': comment['id'], 'reviewer_id': comment['author_id'],
                'approval_digest': draft['digest'], 'target_agent_ids': targets}
    skills = [s for s in api.get('/api/skills') if s['name'] == draft['name']]
    if len(skills) > 1:
        raise ValueError('ambiguous_published_skill')
    if not skills:
        if receipt['skill_id']:
            raise ValueError('published_skill_removed')
        try:
            skill, _ = api.request('/api/skills', {k: draft[k] for k in ('name', 'description', 'content')}
                                   | {'config': {'learning': metadata}})
        except urllib.error.HTTPError as error:
            if error.code != 409:
                raise
            skills = [s for s in api.get('/api/skills') if s['name'] == draft['name']]
            if len(skills) != 1:
                raise ValueError('published_skill_conflict')
            skill = skills[0]
    else:
        skill = skills[0]
    skill = api.get(f'/api/skills/{skill["id"]}')
    if (skill.get('content') != draft['content'] or skill.get('description') != draft['description']
            or (skill.get('config') or {}).get('learning') != metadata
            or (receipt['skill_id'] and receipt['skill_id'] != skill['id'])):
        raise ValueError('published_skill_changed_or_name_conflict')
    versions = api.get(f'/api/skills/{skill["id"]}/versions')
    if not versions:
        raise ValueError('published_skill_version_unavailable')
    version = max(v['version'] for v in versions)
    queue.db.execute('UPDATE learning_publications SET skill_id=?,skill_version=?,content_hash=? WHERE task_id=?',
                     (skill['id'], version, sha(draft['content']), ident))
    applied = json.loads(receipt['applied'])
    for target in targets:
        attached = api.get(f'/api/agents/{target}/skills')
        existing = next((s for s in attached if s['id'] == skill['id']), None)
        if existing and existing.get('enabled') is False:
            raise ValueError('published_skill_disabled_by_operator')
        if not existing:
            if target in applied:
                raise ValueError('published_skill_detached_by_operator')
            api.request(f'/api/agents/{target}/skills/add', {'skill_ids': [skill['id']]})
        attached = api.get(f'/api/agents/{target}/skills')
        if not any(s['id'] == skill['id'] and s.get('enabled', True) for s in attached):
            raise ValueError('skill_attachment_not_verified')
        # The native add operation is atomic and additive; no other skills are replaced.
        with queue.transaction():
            current = json.loads(queue.db.execute('SELECT applied FROM learning_publications WHERE task_id=?',
                                                  (ident,)).fetchone()[0])
            queue.db.execute('UPDATE learning_publications SET applied=? WHERE task_id=?',
                             (canonical(sorted(set(current + [target]))), ident))
    queue.db.execute("UPDATE learning_publications SET state='applied',error=NULL WHERE task_id=?", (ident,))


def sync(api, queue, config):
    initialize(queue)
    jobs = queue.db.execute('''SELECT j.*,r.issue_id FROM jobs j JOIN review_tickets r USING(task_id)
        WHERE j.state='review' AND r.issue_id IS NOT NULL ORDER BY j.created''').fetchall()
    if not jobs:
        return
    if api.get('/api/config').get('server_version') != config['server_version']:
        raise ValueError('backend_version_changed')
    if api.get(f'/api/projects/{config["project_id"]}').get('visibility') != 'private':
        raise ValueError('review_project_not_private')
    owner_id = api.get('/api/me')['id']
    for job in jobs:
        ident = job['task_id']
        queue.db.execute('INSERT OR IGNORE INTO learning_publications(task_id) VALUES(?)', (ident,))
        can_notify = False
        try:
            issue = api.get(f'/api/issues/{job["issue_id"]}')
            if issue.get('project_id') != config['project_id']:
                raise ValueError('review_ticket_moved')
            comments = api.get(f'/api/issues/{job["issue_id"]}/comments')
            can_notify = True
            receipt = queue.db.execute('SELECT * FROM learning_publications WHERE task_id=?', (ident,)).fetchone()
            if receipt['state'] == 'applied':
                notice(api, queue, issue['id'], f'applied:{ident}',
                       f'Applied as skill **{receipt["skill_id"]}**, version **{receipt["skill_version"]}**.\n\n'
                       f'Agents: {", ".join(json.loads(receipt["targets"]))}.\n\n'
                       f'Content SHA-256: `{receipt["content_hash"]}`.\n\n'
                       'Available on subsequent runs. Existing runs were not restarted. '
                       'To undo application, remove this skill from those agents in their Skills settings. '
                       'Later ticket edits or status changes do not republish or undo this receipt.', comments)
                continue
            candidate = json.loads(job['candidate'])
            source_project = api.get(f'/api/projects/{candidate["source_project_id"]}')
            draft = plan(issue, job, source_project)
            agent = api.get(f'/api/agents/{draft["default_agent"]}')
            notice(api, queue, issue['id'], f'preview:{ident}:{draft["digest"]}',
                   'Automatic application is available after review. Publish the **Proposed lesson**, '
                   '**Applicability**, and **Validation limits** as a separate workspace-visible skill. '
                   f'The skill instructs agents to apply it only to project **{source_project["title"]}**; '
                   'this is an instruction, not a runtime access restriction.\n\n'
                   f'Default target: **{agent["name"]}** (`{agent["id"]}`). '
                   'The installation owner can approve this exact ticket content by posting this as a new comment:\n\n'
                   f'```\n/learning approve {draft["digest"]}\n```\n\n'
                   'To choose different targets, append ` agents=UUID,UUID` to that command. '
                   'Edit the ticket before approving; an edit produces a new approval command. '
                   'Use `/learning hold` to prevent a pending approval from being processed. '
                   'Marking Done alone does not approve publication.', comments)
            accepted = approval(comments, owner_id, draft)
            if accepted:
                publish(api, queue, config, job, issue, draft, accepted)
            elif receipt['state'] == 'publishing':
                raise ValueError('approval_missing_or_changed_during_publication')
            else:
                queue.db.execute('UPDATE learning_publications SET error=NULL WHERE task_id=?', (ident,))
        except Exception as error:
            # Only our fixed error codes are safe to persist; remote errors may carry credentials.
            code = str(error) if type(error) is ValueError and re.fullmatch('[a-z_]+', str(error)) else type(error).__name__
            queue.db.execute('UPDATE learning_publications SET error=? WHERE task_id=?', (code, ident))
            if can_notify:
                receipt = queue.db.execute('SELECT applied FROM learning_publications WHERE task_id=?', (ident,)).fetchone()
                notice(api, queue, job['issue_id'], f'blocked:{ident}:{code}',
                       f'Learning application needs attention: `{code}`.\n\n'
                       f'Attachments already verified: {receipt["applied"]}. '
                       'No existing skill content was overwritten. Inspect the publication receipt '
                       'with setup-learner.sh status before resuming.', comments)
