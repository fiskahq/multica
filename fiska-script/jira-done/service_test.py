import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("sync", Path(__file__).with_name("service.py"))
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)
CONFIG = dict(jira_site="https://jira.example", assignee_id="david-account", assignee_name="David Bernard")


def issue(ident="one", status="todo", key="MS-1"):
    return dict(id=ident, identifier="FISK-" + ident, status=status, metadata={},
                description=f"**Jira:** [{key}](https://jira.example/browse/{key})")


class API:
    def __init__(self, rows):
        self.rows = {r["id"]: copy.deepcopy(r) for r in rows}
        self.notes = []

    def issues(self):
        return copy.deepcopy(list(self.rows.values()))

    def get(self, ident):
        return copy.deepcopy(self.rows[ident])

    def note(self, ident, value):
        self.rows[ident]["metadata"][sync.NOTE_KEY] = value
        self.notes.append((ident, value))


class Jira:
    def __init__(self):
        self.fields = dict(status={"name": "Open", "statusCategory": {"key": "new"}}, resolution=None, assignee=None)
        self.transitions = [dict(id="kill", name="Kill Ticket", to={"name": "Closed / Abandonned", "statusCategory": {"key": "done"}}),
                            dict(id="complete", name="Complete Ticket", to={"name": "Resolved", "statusCategory": {"key": "done"}}, fields={
                                "resolution": {"required": True, "allowedValues": [{"id": "wont", "name": "Won't Do"}, {"id": "fixed", "name": "Done / Fixed"}]}})]
        self.calls = []
        self.lose_response = None

    def request(self, method, path, body=None, query=None):
        self.calls.append((method, path, body))
        if method == "GET":
            return copy.deepcopy({"transitions": self.transitions} if path.endswith("/transitions") else {"fields": self.fields})
        if method == "PUT" and path.endswith("/assignee"):
            self.fields["assignee"] = body
        elif method == "POST" and path.endswith("/transitions"):
            assert body["transition"]["id"] == "complete"
            assert body["fields"]["resolution"] == {"id": "fixed"}
            self.fields.update(status={"name": "Resolved", "statusCategory": {"key": "done"}}, resolution={"name": "Done / Fixed"})
        else:
            raise AssertionError("Unexpected write")
        if self.lose_response == method:
            self.lose_response = None
            raise TimeoutError("Committed but response was lost")

    def writes(self, method=None):
        return [c for c in self.calls if c[0] != "GET" and (method is None or c[0] == method)]


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = sync.connect(Path(self.temp.name) / "queue.sqlite")
        self.api, self.jira = API([issue()]), Jira()
        sync.cycle(self.api, self.jira, self.db, CONFIG)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def done(self):
        self.api.rows["one"]["status"] = "done"

    def retry(self):
        self.db.execute("UPDATE issues SET due=0")
        sync.cycle(self.api, self.jira, self.db, CONFIG)

    def state(self):
        return self.db.execute("SELECT state FROM issues WHERE id='one'").fetchone()[0]

    def test_assigns_david_then_completes_once_with_receipt(self):
        self.done()
        sync.cycle(self.api, self.jira, self.db, CONFIG)
        self.assertEqual([c[0] for c in self.jira.writes()], ["PUT", "POST"])
        self.assertEqual(self.jira.fields["assignee"]["accountId"], "david-account")
        self.assertEqual(self.state(), "synced")
        self.assertIn("David Bernard", self.api.notes[-1][1])
        sync.cycle(self.api, self.jira, self.db, CONFIG)
        self.assertEqual(len(self.jira.writes()), 2)
        self.assertEqual(len(self.api.notes), 1)

    def test_does_not_backfill_old_done(self):
        api = API([issue("old", "done")])
        fresh = sync.connect(":memory:")
        sync.cycle(api, self.jira, fresh, CONFIG)
        self.assertEqual(self.jira.calls, [])
        self.assertEqual(api.notes, [])
        api.rows["old"]["status"] = "todo"
        sync.cycle(api, self.jira, fresh, CONFIG)
        api.rows["old"]["status"] = "done"
        sync.cycle(api, self.jira, fresh, CONFIG)
        self.assertEqual(len(self.jira.writes()), 2)
        fresh.close()

    def test_waits_for_all_linked_work_and_supports_explicit_override(self):
        self.api.rows["two"] = issue("two")
        self.done()
        sync.cycle(self.api, self.jira, self.db, CONFIG)
        self.assertEqual(self.jira.calls, [])
        self.assertIn("FISK-two", self.api.notes[-1][1])
        self.db.execute("UPDATE issues SET due=0")
        sync.cycle(self.api, self.jira, self.db, dict(CONFIG, shared_close_overrides={"one": {"reason": "User requested"}}))
        self.assertEqual(self.state(), "synced")

    def test_last_linked_completion_closes_jira(self):
        self.api.rows["two"] = issue("two")
        self.done()
        sync.cycle(self.api, self.jira, self.db, CONFIG)
        self.api.rows["two"]["status"] = "done"
        sync.cycle(self.api, self.jira, self.db, CONFIG)
        self.retry()
        self.assertEqual(len(self.jira.writes("POST")), 1)
        self.assertEqual(self.state(), "synced")

    def test_lost_write_responses_are_reconciled_without_duplicate_writes(self):
        for method in ("PUT", "POST"):
            with self.subTest(method=method):
                api, jira, db = API([issue()]), Jira(), sync.connect(":memory:")
                sync.cycle(api, jira, db, CONFIG)
                api.rows["one"]["status"] = "done"
                jira.lose_response = method
                sync.cycle(api, jira, db, CONFIG)
                db.execute("UPDATE issues SET due=0")
                sync.cycle(api, jira, db, CONFIG)
                self.assertEqual(len(jira.writes("PUT")), 1)
                self.assertEqual(len(jira.writes("POST")), 1)
                self.assertEqual(db.execute("SELECT state FROM issues").fetchone()[0], "synced")
                db.close()

    def test_reopen_before_write_stops_sync(self):
        self.done()
        original = self.api.get
        self.api.get = lambda ident: dict(original(ident), status="todo")
        sync.cycle(self.api, self.jira, self.db, CONFIG)
        self.assertEqual(self.jira.calls, [])

    def test_required_unknown_field_blocks_before_any_write(self):
        self.done()
        self.jira.transitions[1]["fields"]["customfield_1"] = {"required": True}
        sync.cycle(self.api, self.jira, self.db, CONFIG)
        self.assertEqual(self.jira.writes(), [])
        self.assertIn("customfield_1", self.api.notes[-1][1])

    def test_ambiguous_or_cancel_only_transitions_never_close(self):
        for transitions in [self.jira.transitions[:1], self.jira.transitions + [self.jira.transitions[1]]]:
            with self.assertRaises(sync.Blocked):
                sync.transition_body(transitions, "david")

    def test_existing_resolved_issue_is_assigned_without_transition(self):
        self.done()
        self.jira.fields.update(status={"name": "Resolved", "statusCategory": {"key": "done"}}, resolution={"name": "Done / Fixed"})
        sync.cycle(self.api, self.jira, self.db, CONFIG)
        self.assertEqual([c[0] for c in self.jira.writes()], ["PUT"])

    def test_missing_mapping_blocks_and_recovers_when_fixed(self):
        self.done()
        self.api.rows["one"]["description"] = "Related MS-9 is still open."
        sync.cycle(self.api, self.jira, self.db, CONFIG)
        self.assertEqual(self.state(), "blocked")
        self.assertEqual(self.jira.calls, [])
        self.api.rows["one"]["metadata"]["jira_key"] = "MS-1"
        sync.cycle(self.api, self.jira, self.db, CONFIG)
        self.assertEqual(self.state(), "synced")

    def test_mapping_formats_exemptions_and_conflicts(self):
        for text in ["**Jira:** [MS-1](https://jira.example/browse/MS-1). Related MS-2.",
                     "## Jira\n\n- Primary Jira: [MS-1](https://jira.example/browse/MS-1)", "Jira: MS-1"]:
            self.assertEqual(sync.primary_key(dict(description=text), CONFIG["jira_site"]), ("MS-1", False))
        self.assertEqual(sync.primary_key(dict(description="**Jira:** pending intake. Related MS-1."), CONFIG["jira_site"]), (None, False))
        self.assertEqual(sync.primary_key(dict(description="Jira: not required (user request)."), CONFIG["jira_site"]), (None, True))
        for text in ["Jira: [MS-1](https://evil.example/browse/MS-1)",
                     "Jira: [MS-1](https://jira.example/browse/MS-2)", "Jira: MS-1\nJira: MS-2"]:
            with self.assertRaises(sync.Blocked):
                sync.primary_key(dict(description=text), CONFIG["jira_site"])

    def test_durable_restart_keeps_baseline_and_pending_event(self):
        self.done()
        sync.observe(self.db, self.api.issues(), CONFIG)
        self.db.close()
        self.db = sync.connect(Path(self.temp.name) / "queue.sqlite")
        sync.cycle(self.api, self.jira, self.db, CONFIG)
        self.assertEqual(self.state(), "synced")


class HTTPTest(unittest.TestCase):
    def test_real_http_clients_reconcile_committed_write_after_relay_error(self):
        api, jira = API([issue()]), Jira()
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def dispatch(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)))) if self.headers.get("Content-Length") else None
                requests.append((self.command, self.path))
                code = 200
                if self.path.startswith("/api/issues?"):
                    result = {"issues": api.issues(), "total": len(api.rows)}
                elif self.path.endswith("/metadata/jira_done_sync"):
                    api.note("one", body["value"])
                    result = {"metadata": api.rows["one"]["metadata"]}
                elif self.path == "/api/issues/one":
                    result = api.get("one")
                elif self.path == "/mcp":
                    if body["method"] == "initialize":
                        value = {"protocolVersion": "2025-03-26"}
                    else:
                        try:
                            args = body["params"]["arguments"]
                            value = jira.request(args["method"], args["path"], args.get("body"), args.get("query"))
                            value = {"content": [{"type": "text", "text": json.dumps({"data": value})}]}
                        except TimeoutError:
                            value = {"isError": True, "content": [{"type": "text", "text": "Response lost"}]}
                    result = {"jsonrpc": "2.0", "id": body["id"], "result": value}
                else:
                    code, result = 501, {"error": "Not modeled"}
                raw = json.dumps(result).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            do_GET = do_POST = do_PUT = dispatch
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            origin = "http://127.0.0.1:" + str(server.server_port)
            config = dict(CONFIG, server_url=origin, relay_url=origin + "/mcp", token="fake", relay_token="fake", workspace_id="fixture")
            client, relay, db = sync.Multica(config), sync.Jira(config), sync.connect(":memory:")
            sync.cycle(client, relay, db, config)
            api.rows["one"]["status"] = "done"
            jira.lose_response = "POST"
            sync.cycle(client, relay, db, config)
            self.assertEqual(db.execute("SELECT state FROM issues").fetchone()[0], "retry")
            db.execute("UPDATE issues SET due=0")
            sync.cycle(client, relay, db, config)
            self.assertEqual(db.execute("SELECT state FROM issues").fetchone()[0], "synced")
            self.assertEqual(len(jira.writes("POST")), 1)
            self.assertEqual(jira.fields["assignee"]["accountId"], "david-account")
            self.assertIn(("PUT", "/api/issues/one/metadata/jira_done_sync"), requests)
            db.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


class SetupTest(unittest.TestCase):
    def test_reinstall_keeps_state_and_exception_and_refreshes_private_credentials(self):
        spec = importlib.util.spec_from_file_location("setup", Path(__file__).parents[1] / "configure-jira-done.py")
        setup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(setup)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relay = root / "mcp-relay"
            (relay / ".secrets").mkdir(parents=True)
            endpoints = []
            for name in ("jira", "jira_triage"):
                endpoints.append(dict(name=name, path="/mcp/" + name, token={"file": "/run/secrets/" + name}))
                (relay / ".secrets" / name).write_text("fake")
            (relay / "config.json").write_text(json.dumps(dict(http={"endpoints": endpoints}, jira={"site": "https://jira.example"})))
            calls = []
            def docker(deployment, *args):
                calls.append(args)
                if args[0] == "port":
                    return "127.0.0.1:8090"
                if args[0] == "exec":
                    return json.dumps(dict(workspace_id="workspace", token="new-token"))
                return ""
            def rpc(url, token, method, params, ident):
                if method == "initialize":
                    return {}
                return {"content": [{"text": json.dumps([dict(active=True, displayName="David Bernard", accountId="david-account")])}]}
            with patch.object(setup, "docker", side_effect=docker), patch.object(setup, "relay_call", side_effect=rpc):
                setup.configure(root, "David Bernard")
                path = root / "jira-done-private/config.json"
                saved = json.loads(path.read_text())
                saved["shared_close_overrides"] = {"one": {"reason": "Explicit user instruction"}}
                path.write_text(json.dumps(saved))
                marker = root / "jira-done-private/data/preserved"
                marker.write_text("durable queue")
                setup.configure(root, "David Bernard")
                self.assertEqual(json.loads(path.read_text())["shared_close_overrides"], saved["shared_close_overrides"])
                self.assertEqual(marker.read_text(), "durable queue")
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                saved["workspace_id"] = "different-workspace"
                path.write_text(json.dumps(saved))
                prior_calls = len([c for c in calls if c[0] == "up"])
                with self.assertRaisesRegex(RuntimeError, "another workspace"):
                    setup.configure(root, "David Bernard")
                self.assertEqual(len([c for c in calls if c[0] == "up"]), prior_calls)


if __name__ == "__main__":
    unittest.main()
