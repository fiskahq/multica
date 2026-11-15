import concurrent.futures
import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest

spec = importlib.util.spec_from_file_location("learner", Path(__file__).with_name("service.py"))
learner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(learner)

SOURCE = {"id": "source-1", "agent_id": "builder", "issue_id": "issue-1",
          "status": "completed", "completed_at": "2026-11-13T12:00:00Z"}
CONFIG = {"agent_id": "learner", "autopilot_id": "pilot", "project_id": "private-project",
          "webhook_path": "/api/webhooks/autopilots/test", "server_version": "verified",
          "started_at": "2026-11-13T11:00:00Z"}
CANDIDATE = {"decision": "candidate", "title": "Verify the boundary", "lesson": "Test both sides",
             "rationale": "Found a regression", "evidence": [{"task_id": "source-1", "seq": 1,
             "finding": "A boundary test failed before the correction"}],
             "applicability": "Source project only", "validation": "Reviewer must reproduce"}


class FakeAPI:
    def __init__(self):
        self.calls = []
        self.lost_reply = False
        self.status = "running"
        self.result = {"output": json.dumps(CANDIDATE)}
        self.version = "verified"
        self.visibility = "private"

    def get(self, path):
        if path == "/api/config":
            return {"server_version": self.version}
        if path == "/api/projects/private-project":
            return {"visibility": self.visibility}
        if path == "/api/autopilots/pilot":
            return {"autopilot": {"project_id": "private-project", "assignee_id": "learner", "execution_mode": "run_only"}}
        if path == "/api/issues/issue-1":
            return {"id": "issue-1", "project_id": "source-project", "title": "Test"}
        if path == "/api/tasks/source-1/messages":
            return [{"seq": 1, "type": "assistant", "content": "Verified correction"}]
        if path.endswith("/comments?recent=5"):
            return []
        if path == "/api/autopilots/pilot/runs/run-1":
            return {"status": self.status, "result": self.result}
        raise AssertionError(path)

    def request(self, path, body=None, key=None):
        assert path == CONFIG["webhook_path"]
        self.calls.append((body, key))
        if self.lost_reply:
            self.lost_reply = False
            raise TimeoutError("response lost after admission")
        return {"status": "accepted", "run_id": "run-1"}, {}


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "queue.db"
        self.queue = learner.Queue(self.path, 5)

    def tearDown(self):
        self.queue.db.close()
        self.tmp.cleanup()

    def test_capacity_and_deduplication_under_concurrent_enqueues(self):
        def insert(n):
            q = learner.Queue(self.path, 5)
            try:
                return q.enqueue(dict(SOURCE, id=str(n % 10)))
            finally:
                q.db.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(insert, range(100)))
        self.assertEqual(self.queue.status(), {"pending": 5})

    def test_active_job_resumes_after_restart(self):
        self.queue.enqueue(SOURCE)
        first = self.queue.claim()
        self.queue.update(first["task_id"], run_id="persisted-run")
        q = learner.Queue(self.path, 5)
        try:
            self.assertEqual(q.claim()["run_id"], "persisted-run")
            self.assertEqual(q.status(), {"active": 1})
        finally:
            q.db.close()

    def test_overflow_recovered_on_later_scan(self):
        for i in range(5):
            self.queue.enqueue(dict(SOURCE, id=str(i)))
        self.assertFalse(self.queue.enqueue(SOURCE))
        self.queue.update("0", state="no_lesson")
        self.assertTrue(self.queue.enqueue(SOURCE))
        self.assertEqual(self.queue.status()["pending"], 5)

    def test_confirmed_failures_are_bounded_and_delayed(self):
        self.queue.enqueue(SOURCE)
        for attempt in range(1, 4):
            self.queue.update(SOURCE["id"], due=0)
            job = self.queue.claim()
            self.assertEqual(job["attempt"], attempt)
            self.queue.retry(job, "learner_failed")
            if attempt < 3:
                row = self.queue.db.execute("SELECT due FROM jobs").fetchone()
                self.assertGreater(row[0], time.time() + 59 * 2 ** (attempt - 1))
                self.assertIsNone(self.queue.claim())
        self.assertEqual(self.queue.status(), {"dead": 1})

    def test_lost_webhook_response_reuses_exact_body_and_key(self):
        self.queue.enqueue(SOURCE)
        api = FakeAPI()
        api.lost_reply = True
        with self.assertRaises(TimeoutError):
            learner.process(api, self.queue, CONFIG)
        learner.process(api, self.queue, CONFIG)
        self.assertEqual(api.calls[0], api.calls[1])
        self.assertEqual(len(api.calls), 2)
        learner.process(api, self.queue, CONFIG)
        self.assertEqual(len(api.calls), 2)

    def test_completed_review_keeps_scope_and_removes_payload(self):
        self.queue.enqueue(SOURCE)
        api = FakeAPI()
        api.status = "completed"
        learner.process(api, self.queue, CONFIG)
        row = self.queue.db.execute("SELECT * FROM jobs").fetchone()
        self.assertEqual(row["state"], "review")
        self.assertIsNone(row["request"])
        self.assertEqual(json.loads(row["candidate"])["source_project_id"], "source-project")
        self.assertTrue(all(call[1].startswith("learner:") for call in api.calls))

    def test_unknown_citation_does_not_become_a_candidate(self):
        self.queue.enqueue(SOURCE)
        api = FakeAPI()
        api.status = "completed"
        value = dict(CANDIDATE, evidence=[{"task_id": "source-1", "seq": 999, "finding": "invented"}])
        api.result = {"output": json.dumps(value)}
        learner.process(api, self.queue, CONFIG)
        self.assertEqual(self.queue.status(), {"pending": 1})

    def test_backend_rollback_and_public_review_project_block_dispatch(self):
        self.queue.enqueue(SOURCE)
        api = FakeAPI()
        api.version = "old-unprotected-backend"
        with self.assertRaises(ValueError):
            learner.process(api, self.queue, CONFIG)
        api.version = "verified"
        api.visibility = "workspace"
        with self.assertRaises(ValueError):
            learner.process(api, self.queue, CONFIG)
        self.assertEqual(api.calls, [])


class EvidenceTests(unittest.TestCase):
    def test_redaction_preserves_valid_json(self):
        sample = {"password": "foo", "nested": [{"api_key": "bar"}],
                  "content": 'Authorization: Bearer abc.xyz\n{"secret":"a\\b"}\npassword=guess'}
        cleaned = json.loads(learner.redact(sample))
        self.assertEqual(cleaned["password"], "[REDACTED]")
        self.assertNotIn("abc.xyz", cleaned["content"])
        self.assertNotIn("guess", cleaned["content"])

    def test_candidate_requires_evidence_and_correct_task(self):
        for value in (dict(CANDIDATE, evidence=[]), dict(CANDIDATE, applicability=""),
                      dict(CANDIDATE, evidence=[{"task_id": "other", "seq": 1, "finding": "x"}])):
            with self.assertRaises(ValueError):
                learner.parse_candidate(value, SOURCE["id"])

    def test_collect_paginates_excludes_self_chats_and_old_completions(self):
        class HistoryAPI:
            def get(self, path):
                return [{"id": "learner", "name": "background-learner"}, {"id": "builder", "name": "builder"}]

            def request(self, path):
                if "before=" not in path:
                    return [dict(SOURCE, id="chat", issue_id=None), dict(SOURCE, id="new")], {"X-Agent-Tasks-Next-Cursor": "page2"}
                return [dict(SOURCE, id="old-start-new-finish"), dict(SOURCE, id="old", completed_at="2026-11-12T00:00:00Z")], {}
        with tempfile.TemporaryDirectory() as directory:
            q = learner.Queue(Path(directory) / "test.db")
            try:
                self.assertEqual(learner.collect(HistoryAPI(), q, CONFIG), "ok")
                self.assertEqual({r[0] for r in q.db.execute("SELECT task_id FROM jobs")}, {"new", "old-start-new-finish"})
                learner.collect(HistoryAPI(), q, CONFIG)
                self.assertEqual(q.status(), {"pending": 2})
            finally:
                q.db.close()


class ReviewAPI(FakeAPI):
    def __init__(self):
        super().__init__()
        self.issues = []
        self.posts = 0
        self.handler = 'none'
        self.reject_before_create = False

    def get(self, path):
        if path == '/api/issue-workflows':
            return {'workflows': [{'id': 'default', 'is_default': True,
                    'steps': [{'status_key': 'todo', 'handler': {'type': self.handler}}]}]}
        if path.startswith('/api/issues/search?'):
            return {'issues': self.issues}
        return super().get(path)

    def request(self, path, body=None, key=None):
        assert path == '/api/issues'
        self.posts += 1
        if self.reject_before_create:
            raise TimeoutError('outcome unknown')
        issue = dict(body, id='review-1')
        self.issues.append(issue)
        if self.lost_reply:
            self.lost_reply = False
            raise TimeoutError('reply lost after commit')
        return issue, {}


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'queue.db'
        self.queue = learner.Queue(self.path)
        self.queue.enqueue(SOURCE)
        self.queue.update(SOURCE['id'], state='review', candidate=json.dumps(CANDIDATE), run_id='run-1')
        self.api = ReviewAPI()

    def tearDown(self):
        self.queue.db.close()
        self.tmp.cleanup()

    def test_backfills_once_and_preserves_human_edits(self):
        learner.sync_reviews(self.api, self.queue, CONFIG)
        issue = self.api.issues[0]
        self.assertEqual(issue['project_id'], 'private-project')
        self.assertEqual(issue['status'], 'todo')
        self.assertNotIn('assignee_id', issue)
        self.assertIn(CANDIDATE['lesson'], issue['description'])
        self.assertIn('Learning source task: source-1', issue['description'])
        issue['title'], issue['status'] = 'Edited by reviewer', 'done'
        learner.sync_reviews(self.api, self.queue, CONFIG)
        self.assertEqual(self.api.posts, 1)
        self.assertEqual(issue['title'], 'Edited by reviewer')

    def test_lost_reply_recovers_closed_ticket_after_restart(self):
        self.api.lost_reply = True
        learner.sync_reviews(self.api, self.queue, CONFIG)
        self.api.issues[0]['status'] = 'done'
        self.queue.db.close()
        self.queue = learner.Queue(self.path)
        learner.sync_reviews(self.api, self.queue, CONFIG)
        self.assertEqual(self.api.posts, 1)
        receipt = self.queue.db.execute('SELECT * FROM review_tickets').fetchone()
        self.assertEqual(receipt['issue_id'], 'review-1')
        self.assertIsNone(receipt['error'])

    def test_unknown_creation_is_not_blindly_repeated(self):
        self.api.reject_before_create = True
        learner.sync_reviews(self.api, self.queue, CONFIG)
        learner.sync_reviews(self.api, self.queue, CONFIG)
        self.assertEqual(self.api.posts, 1)
        receipt = self.queue.db.execute('SELECT * FROM review_tickets').fetchone()
        self.assertEqual(receipt['error'], 'creation_outcome_unknown')

    def test_private_project_and_manual_workflow_required(self):
        for attribute, value in [('visibility', 'workspace'), ('handler', 'agent'), ('version', 'old')]:
            api = ReviewAPI()
            setattr(api, attribute, value)
            with self.assertRaises(ValueError):
                learner.sync_reviews(api, self.queue, CONFIG)
            self.assertEqual(api.posts, 0)

    def test_no_lesson_and_invalid_results_never_create_tickets(self):
        for state in ('no_lesson', 'dead', 'pending', 'active'):
            self.queue.update(SOURCE['id'], state=state)
            learner.sync_reviews(self.api, self.queue, CONFIG)
        self.assertEqual(self.api.posts, 0)

    def test_concurrent_syncs_claim_only_one_create(self):
        def sync(_):
            q = learner.Queue(self.path)
            try:
                learner.sync_reviews(self.api, q, CONFIG)
            finally:
                q.db.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(sync, range(12)))
        self.assertEqual(self.api.posts, 1)


if __name__ == "__main__":
    unittest.main()
