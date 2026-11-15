import concurrent.futures
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


service = load('service')
publication = load('publication')
AGENT = '11111111-1111-4111-8111-111111111111'
OTHER = '22222222-2222-4222-8222-222222222222'
SOURCE = {'id': '33333333-3333-4333-8333-333333333333', 'agent_id': AGENT,
          'agent_name': 'QA', 'issue_id': 'source-issue'}
CANDIDATE = {'title': 'Use the correct test image', 'lesson': 'Use Node with the SDK.',
             'rationale': 'Build dependency', 'applicability': 'QA only', 'validation': 'Check the image',
             'source_project_id': 'source-project', 'source_agent_id': AGENT, 'evidence': []}
CONFIG = {'workspace_id': 'workspace', 'project_id': 'review-project', 'agent_id': 'learner',
          'server_version': 'verified'}


class API:
    def __init__(self, issue):
        self.issue = issue
        self.comments = []
        self.skills = []
        self.attached = {AGENT: [{'id': 'existing-skill', 'enabled': True}], OTHER: []}
        self.skill_creates = 0
        self.adds = 0
        self.lose_create = False
        self.fail_target = None
        self.visibility = 'private'
        self.lock = threading.Lock()

    def get(self, path):
        if path == '/api/config':
            return {'server_version': 'verified'}
        if path == '/api/projects/review-project':
            return {'visibility': self.visibility}
        if path == '/api/projects/source-project':
            return {'id': 'source-project', 'title': 'Merchant Services'}
        if path == '/api/me':
            return {'id': 'owner'}
        if path == '/api/issues/review-issue':
            return copy.deepcopy(self.issue)
        if path == '/api/issues/review-issue/comments':
            return copy.deepcopy(self.comments)
        if path == '/api/skills':
            return copy.deepcopy(self.skills)
        if path == '/api/skills/skill-1':
            return copy.deepcopy(self.skills[0])
        if path == '/api/skills/skill-1/versions':
            return [{'version': 1}]
        for target in (AGENT, OTHER):
            if path == f'/api/agents/{target}':
                return {'id': target, 'name': 'QA', 'workspace_id': 'workspace'}
            if path == f'/api/agents/{target}/skills':
                return copy.deepcopy(self.attached[target])
        raise AssertionError(path)

    def request(self, path, body=None):
        with self.lock:
            if path == '/api/issues/review-issue/comments':
                comment = dict(body, id=f'notice-{len(self.comments)}', author_type='member', author_id='owner',
                               created_at='2026-11-15T10:00:00Z', updated_at='2026-11-15T10:00:00Z')
                self.comments.append(comment)
                return comment, {}
            if path == '/api/skills':
                if self.skills:
                    raise urllib.error.HTTPError(path, 409, 'exists', {}, None)
                self.skill_creates += 1
                skill = dict(body, id='skill-1')
                self.skills.append(skill)
                if self.lose_create:
                    self.lose_create = False
                    raise TimeoutError('reply lost')
                return skill, {}
            for target in (AGENT, OTHER):
                if path == f'/api/agents/{target}/skills/add':
                    if target == self.fail_target:
                        raise TimeoutError('temporary failure')
                    self.adds += 1
                    if not any(s['id'] == 'skill-1' for s in self.attached[target]):
                        self.attached[target].append({'id': 'skill-1', 'enabled': True})
                    return self.attached[target], {}
        raise AssertionError(path)


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'queue.db'
        self.queue = service.Queue(self.path)
        self.queue.enqueue(SOURCE)
        self.queue.update(SOURCE['id'], state='review', candidate=json.dumps(CANDIDATE), run_id='run-1')
        job = dict(self.queue.db.execute('SELECT * FROM jobs').fetchone())
        self.issue = service.review_body(job, CONFIG) | {'id': 'review-issue', 'identifier': 'TEST-1'}
        self.queue.db.execute('INSERT INTO review_tickets(task_id,body,issue_id) VALUES(?,?,?)',
                              (SOURCE['id'], json.dumps(self.issue), 'review-issue'))
        self.api = API(self.issue)
        publication.initialize(self.queue)

    def tearDown(self):
        self.queue.db.close()
        self.tmp.cleanup()

    def approve(self, suffix='', **overrides):
        job = dict(self.queue.db.execute('SELECT * FROM jobs').fetchone())
        draft = publication.plan(self.issue, job, {'title': 'Merchant Services'})
        comment = {'id': 'approval-1', 'author_type': 'member', 'author_id': 'owner',
                   'created_at': '2026-11-15T11:00:00Z', 'updated_at': '2026-11-15T11:00:00Z',
                   'content': '/learning approve ' + draft['digest'] + suffix}
        comment.update(overrides)
        self.api.comments.append(comment)
        return comment

    def sync(self):
        publication.sync(self.api, self.queue, CONFIG)

    def receipt(self):
        return self.queue.db.execute('SELECT * FROM learning_publications').fetchone()

    def test_preview_and_done_never_publish_without_approval(self):
        self.issue['status'] = 'done'
        self.sync()
        self.sync()
        self.assertEqual(self.api.skill_creates, 0)
        self.assertEqual(len(self.api.comments), 1)
        self.assertIn('/learning approve ', self.api.comments[0]['content'])
        self.assertIn('workspace-visible', self.api.comments[0]['content'])

    def test_owner_approval_publishes_scoped_skill_and_preserves_existing_skills(self):
        self.approve()
        self.sync()
        self.sync()
        self.assertEqual(self.receipt()['state'], 'applied')
        self.assertEqual(self.receipt()['skill_version'], 1)
        self.assertEqual(self.api.skill_creates, 1)
        self.assertEqual(self.api.adds, 1)
        self.assertEqual({s['id'] for s in self.api.attached[AGENT]}, {'existing-skill', 'skill-1'})
        self.assertIn('Apply only when working on Multica project Merchant Services', self.api.skills[0]['content'])
        self.assertIn('Use Node with the SDK.', self.api.skills[0]['content'])
        self.assertTrue(any('Applied as skill' in c['content'] for c in self.api.comments))

    def test_agent_nonowner_and_task_backed_comments_cannot_approve(self):
        for override in ({'author_type': 'agent'}, {'author_id': 'someone-else'}, {'source_task_id': 'run'},
                         {'deleted_at': '2026-11-15T12:00:00Z'}):
            self.api.comments.clear()
            self.approve(**override)
            self.sync()
            self.assertEqual(self.api.skill_creates, 0)

    def test_edit_invalidates_approval_and_new_approval_applies_reviewed_text(self):
        self.approve()
        self.issue['description'] = self.issue['description'].replace('Use Node with the SDK.', 'Use reviewed image.')
        self.sync()
        self.assertEqual(self.api.skill_creates, 0)
        self.approve(id='approval-2', created_at='2026-11-15T12:00:00Z', updated_at='2026-11-15T12:00:00Z')
        self.sync()
        self.assertIn('Use reviewed image.', self.api.skills[0]['content'])

    def test_latest_hold_prevents_application(self):
        self.approve()
        self.approve(id='hold', content='/learning hold', updated_at='2026-11-15T12:00:00Z')
        self.sync()
        self.assertEqual(self.api.skill_creates, 0)

    def test_lost_skill_create_reply_recovers_after_restart(self):
        self.approve()
        self.api.lose_create = True
        self.sync()
        self.queue.db.close()
        self.queue = service.Queue(self.path)
        self.sync()
        self.assertEqual(self.api.skill_creates, 1)
        self.assertEqual(self.receipt()['state'], 'applied')

    def test_partial_attachment_resumes_only_remaining_targets(self):
        self.approve(suffix=f' agents={AGENT},{OTHER}')
        self.api.fail_target = OTHER
        self.sync()
        self.assertEqual(json.loads(self.receipt()['applied']), [AGENT])
        self.api.fail_target = None
        self.sync()
        self.assertEqual(self.api.adds, 2)
        self.assertEqual(self.receipt()['state'], 'applied')

    def test_operator_detach_or_edit_is_not_overwritten(self):
        self.approve(suffix=f' agents={AGENT},{OTHER}')
        self.api.fail_target = OTHER
        self.sync()
        self.api.attached[AGENT] = []
        self.api.fail_target = None
        self.sync()
        self.assertEqual(self.receipt()['error'], 'published_skill_detached_by_operator')
        self.assertEqual(self.api.adds, 1)
        self.api.skills[0]['content'] = 'Human edit'
        self.sync()
        self.assertEqual(self.receipt()['error'], 'published_skill_changed_or_name_conflict')
        self.assertEqual(self.api.skills[0]['content'], 'Human edit')

    def test_applied_receipt_does_not_republish_after_later_changes(self):
        self.approve()
        self.sync()
        self.api.attached[AGENT] = []
        self.issue['description'] = 'Edited later'
        self.sync()
        self.assertEqual(self.api.adds, 1)
        self.assertEqual(self.api.skill_creates, 1)

    def test_private_project_guard(self):
        self.approve()
        self.api.visibility = 'workspace'
        with self.assertRaises(ValueError):
            self.sync()
        self.assertEqual(self.api.skill_creates, 0)

    def test_concurrent_publication_uses_one_unique_skill(self):
        self.approve()
        def run(_):
            q = service.Queue(self.path)
            try:
                publication.sync(self.api, q, CONFIG)
            finally:
                q.db.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(run, range(8)))
        self.assertEqual(self.api.skill_creates, 1)
        self.assertEqual(self.receipt()['state'], 'applied')
        self.assertEqual(sum(s['id'] == 'skill-1' for s in self.api.attached[AGENT]), 1)


if __name__ == '__main__':
    unittest.main()
