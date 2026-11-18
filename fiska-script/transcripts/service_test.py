import copy
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from service import Store, accepted, canonical, digest, execution, extraction, parse_review, replan, reviews, scan, tickets

DRAFT = dict(title='Document the decision', kind='confluence', proposal='Create page Decision in space TEAM: Use the documented option.',
             source_quote='Please document the decision.', blocker='', owner='David', resume='Publish the approved draft.')
CONFIG = dict(project_id='private', owner_id='owner', intake={'id': 'intake', 'webhook_path': '/api/hooks/intake'},
              executor={'id': 'executor', 'webhook_path': '/api/hooks/executor'})


class FakeAPI:
    def __init__(self):
        self.issues = {}
        self.comments = {}
        self.runs = {}
        self.dispatches = []
        self.calls = []
        self.lose_create = False
        self.lose_dispatch = False
        self.webhooks = {}

    def get(self, path):
        if path.startswith('/api/issues/search?'):
            return {'issues': list(copy.deepcopy(self.issues).values())}
        if '/runs/' in path:
            return copy.deepcopy(self.runs[path.rsplit('/', 1)[1]])
        ident = path.split('/')[3]
        if path.endswith('/comments'):
            return copy.deepcopy(self.comments[ident])
        return copy.deepcopy(self.issues[ident])

    def request(self, path, body=None, key=None, method=None):
        self.calls.append((path, copy.deepcopy(body), key, method))
        if path.startswith('/api/hooks/'):
            if key not in self.webhooks:
                run = 'run' + str(len(self.webhooks))
                self.webhooks[key] = run
                self.runs[run] = {'status': 'running'}
                self.dispatches.append((path, copy.deepcopy(body), key))
            if self.lose_dispatch:
                self.lose_dispatch = False
                raise OSError('lost response')
            return {'run_id': self.webhooks[key]}
        if path == '/api/issues':
            ident = str(len(self.issues) + 1)
            self.issues[ident] = dict(body, id=ident)
            self.comments[ident] = []
            if self.lose_create:
                self.lose_create = False
                raise OSError('lost response')
            return copy.deepcopy(self.issues[ident])
        ident = path.split('/')[3]
        if path.endswith('/comments'):
            comment = dict(body, id=str(len(self.comments[ident])), author_type='member', author_id='owner', created_at='1')
            self.comments[ident].append(comment)
            return comment
        self.issues[ident].update(body)
        return copy.deepcopy(self.issues[ident])


class TranscriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'queue.sqlite3', 2)
        self.addCleanup(self.store.db.close)
        self.api = FakeAPI()

    def action(self, **changes):
        draft = dict(DRAFT, **changes)
        self.store.db.execute('INSERT INTO actions(id,source,draft) VALUES(?,?,?)', ('a', 's', canonical(draft)))
        tickets(self.api, self.store, CONFIG)
        return self.store.db.execute('SELECT * FROM actions').fetchone()

    def approve(self, row, **changes):
        _, fingerprint = parse_review(self.api.issues[row['issue_id']], row, CONFIG)
        comment = dict(id='approval', author_type='member', author_id='owner', created_at='9', content='/action approve ' + fingerprint)
        comment.update(changes)
        self.api.comments[row['issue_id']].append(comment)
        return fingerprint

    def test_no_dispatch_without_exact_owner_approval(self):
        row = self.action()
        reviews(self.api, self.store, CONFIG)
        execution(self.api, self.store, CONFIG)
        self.assertEqual(self.api.dispatches, [])
        self.assertEqual(self.api.issues['1']['status'], 'awaiting_approval')
        for wrong in ({'author_type': 'agent'}, {'author_id': 'another'}, {'source_task_id': 'agent-task'}, {'content': '/action approve stale'}):
            self.api.comments['1'] = []
            self.approve(row, **wrong)
            reviews(self.api, self.store, CONFIG)
            execution(self.api, self.store, CONFIG)
            self.assertEqual(self.api.dispatches, [])

    def test_changed_content_and_done_status_are_not_approval(self):
        row = self.action()
        self.approve(row)
        self.api.issues['1']['description'] += '\nA changed destination.'
        reviews(self.api, self.store, CONFIG)
        execution(self.api, self.store, CONFIG)
        self.assertEqual(self.api.dispatches, [])
        self.approve(row)
        self.api.issues['1']['status'] = 'done'
        reviews(self.api, self.store, CONFIG)
        execution(self.api, self.store, CONFIG)
        self.assertEqual(self.api.dispatches, [])

    def test_latest_rejection_cancels_pending_approval(self):
        row = self.action()
        fingerprint = self.approve(row)
        self.api.comments['1'].append(dict(id='reject', author_type='member', author_id='owner', created_at='99', content='/action reject'))
        self.assertIsNone(accepted(self.api.comments['1'], 'owner', fingerprint))

    def test_blocked_and_unconnected_email_never_dispatch(self):
        row = self.action(kind='email')
        self.approve(row)
        reviews(self.api, self.store, CONFIG)
        execution(self.api, self.store, CONFIG)
        self.assertEqual(self.api.dispatches, [])
        self.assertEqual(self.api.issues['1']['status'], 'blocked')

    def test_approved_action_runs_once_and_records_completion(self):
        row = self.action()
        self.approve(row)
        reviews(self.api, self.store, CONFIG)
        self.api.lose_dispatch = True
        with self.assertRaises(OSError):
            execution(self.api, self.store, CONFIG)
        execution(self.api, self.store, CONFIG)
        self.assertEqual(len(self.api.dispatches), 1)
        self.api.runs['run0'] = dict(status='completed', result={'output': canonical(dict(outcome='done', summary='Created and read back page', receipts=['https://example.org/wiki/1']))})
        execution(self.api, self.store, CONFIG)
        execution(self.api, self.store, CONFIG)
        reviews(self.api, self.store, CONFIG)
        self.assertEqual(self.api.issues['1']['status'], 'done')
        self.assertEqual(len(self.api.dispatches), 1)

    def test_uncertain_execution_is_blocked_and_never_retried(self):
        row = self.action()
        self.approve(row)
        reviews(self.api, self.store, CONFIG)
        execution(self.api, self.store, CONFIG)
        self.api.runs['run0'] = {'status': 'failed'}
        execution(self.api, self.store, CONFIG)
        reviews(self.api, self.store, CONFIG)
        execution(self.api, self.store, CONFIG)
        self.assertEqual(self.api.issues['1']['status'], 'blocked')
        self.assertEqual(len(self.api.dispatches), 1)

    def test_lost_ticket_reply_reconciles_without_duplicate(self):
        self.api.lose_create = True
        with self.assertRaises(OSError):
            self.action()
        tickets(self.api, self.store, CONFIG)
        self.assertEqual(len(self.api.issues), 1)
        self.assertEqual(self.store.db.execute('SELECT issue_id FROM actions').fetchone()[0], '1')

    def test_bounded_queue_and_content_deduplication(self):
        first = self.store.enqueue('first.txt', 'same text')
        self.assertEqual(first, self.store.enqueue('renamed.txt', 'same text'))
        self.store.enqueue('second.txt', 'second')
        self.assertIsNone(self.store.enqueue('third.txt', 'third'))
        other = Store(self.root / 'queue.sqlite3', 2)
        try:
            self.assertIsNone(other.enqueue('fourth.txt', 'fourth'))
        finally:
            other.db.close()

    def test_scan_ignores_symlinks_and_partial_files(self):
        inbox = self.root / 'inbox'; inbox.mkdir()
        (inbox / 'valid.txt').write_text('Please document the decision.')
        (inbox / 'link.txt').symlink_to(inbox / 'valid.txt')
        (inbox / 'partial.txt').write_text('not complete')
        (inbox / 'audio.wav').write_bytes(b'data')
        os.utime(inbox / 'valid.txt', (0, 0))
        scan(self.store, inbox)
        self.assertEqual(self.store.db.execute('SELECT name FROM files').fetchall()[0][0], 'valid.txt')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM files').fetchone()[0], 1)

    def test_extraction_requires_real_source_quote(self):
        self.store.enqueue('call.txt', DRAFT['source_quote'])
        extraction(self.api, self.store, CONFIG)
        self.api.runs['run0'] = dict(status='completed', result={'output': canonical({'actions': [dict(DRAFT, source_quote='invented')]})})
        extraction(self.api, self.store, CONFIG)
        self.assertEqual(self.store.db.execute('SELECT state FROM files').fetchone()[0], 'blocked')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM actions').fetchone()[0], 0)

    def test_end_to_end_extract_review_no_external_action_before_approval(self):
        self.store.enqueue('call.txt', DRAFT['source_quote'])
        extraction(self.api, self.store, CONFIG)
        self.api.runs['run0'] = dict(status='completed', result={'output': canonical({'actions': [DRAFT]})})
        extraction(self.api, self.store, CONFIG)
        tickets(self.api, self.store, CONFIG)
        reviews(self.api, self.store, CONFIG)
        execution(self.api, self.store, CONFIG)
        self.assertEqual(len(self.api.dispatches), 1)
        self.assertEqual(self.api.dispatches[0][0], '/api/hooks/intake')
        self.assertEqual(self.api.issues['1']['status'], 'awaiting_approval')

    def test_execution_receipt_is_not_published_to_a_moved_review(self):
        row = self.action()
        self.approve(row)
        reviews(self.api, self.store, CONFIG)
        execution(self.api, self.store, CONFIG)
        self.api.runs['run0'] = dict(status='completed', result={'output': canonical(dict(outcome='done', summary='Private result', receipts=['https://example.org/private']))})
        self.api.issues['1']['project_id'] = 'public'
        count = len(self.api.comments['1'])
        with self.assertRaisesRegex(ValueError, 'moved_project'):
            execution(self.api, self.store, CONFIG)
        self.assertEqual(len(self.api.comments['1']), count)

    def test_replan_preserves_old_run_and_requires_a_new_approval(self):
        row = self.action()
        self.approve(row)
        reviews(self.api, self.store, CONFIG)
        execution(self.api, self.store, CONFIG)
        self.api.runs['run0'] = {'status': 'failed'}
        execution(self.api, self.store, CONFIG)
        result = replan(self.api, self.store, CONFIG, 'a', 'Verified no page was created; access restored.')
        tickets(self.api, self.store, CONFIG)
        reviews(self.api, self.store, CONFIG)
        execution(self.api, self.store, CONFIG)
        self.assertNotEqual(result['action_id'], 'a')
        self.assertEqual(len(self.api.issues), 2)
        self.assertEqual(len(self.api.dispatches), 1)
        old = self.store.db.execute("SELECT state,run_id FROM actions WHERE id='a'").fetchone()
        self.assertEqual(tuple(old), ('superseded', 'run0'))

    def test_database_enforces_one_execution_even_across_connections(self):
        other = Store(self.root / 'queue.sqlite3', 2)
        try:
            self.store.db.execute("INSERT INTO actions(id,source,draft,state) VALUES('a','s','{}','executing')")
            with self.assertRaises(sqlite3.IntegrityError):
                other.db.execute("INSERT INTO actions(id,source,draft,state) VALUES('b','s','{}','executing')")
        finally:
            other.db.close()


if __name__ == '__main__':
    unittest.main()
