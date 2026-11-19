import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest

spec = importlib.util.spec_from_file_location('claude_auth', Path(__file__).with_name('claude-auth.py'))
auth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auth)


class ClaudeAuthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.login = {'accessToken': 'test-access', 'refreshToken': 'test-refresh',
                      'expiresAt': (time.time() + 600) * 1000}

    def test_login_preserves_runtime_mcp_and_does_not_import_host_mcp(self):
        path = self.root / '.credentials.json'
        path.write_text(json.dumps({'mcpOAuth': {'runtime': 'keep'}}))
        auth.install(self.root, {'claudeAiOauth': self.login, 'mcpOAuth': {'host': 'omit'}})
        self.assertEqual(json.loads(path.read_text()),
                         {'claudeAiOauth': self.login, 'mcpOAuth': {'runtime': 'keep'}})
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_expired_login_leaves_existing_file_unchanged(self):
        path = self.root / '.credentials.json'
        path.write_text('{}')
        self.login['expiresAt'] = 1
        with self.assertRaises(ValueError):
            auth.install(self.root, {'claudeAiOauth': self.login})
        self.assertEqual(path.read_text(), '{}')

    def test_token_preserves_settings_and_credentials(self):
        path = self.root / 'settings.json'
        path.write_text(json.dumps({'env': {'KEEP': 'yes'}, 'permissions': {'deny': ['test']}}))
        credentials = self.root / '.credentials.json'
        credentials.write_text('{}')
        auth.install(self.root, {'token': 'sk-ant-oat01-test'}, token=True)
        data = json.loads(path.read_text())
        self.assertEqual(data['env'], {'KEEP': 'yes', 'CLAUDE_CODE_OAUTH_TOKEN': 'sk-ant-oat01-test'})
        self.assertEqual(data['permissions'], {'deny': ['test']})
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(credentials.read_text(), '{}')
        with self.assertRaises(ValueError):
            auth.install(self.root, {'claudeAiOauth': self.login})

    def test_escaped_or_formatted_token_does_not_replace_valid_settings(self):
        path = self.root / 'settings.json'
        path.write_text('{"env":{"KEEP":"yes"}}')
        original = path.read_bytes()
        for value in [r'sk-ant-oat01-test\_value', 'sk-ant-oat01-test.value',
                      '"sk-ant-oat01-test"', 'sk-ant-oat01-test value']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                auth.install(self.root, {'token': value}, token=True)
            self.assertEqual(path.read_bytes(), original)

    def test_bad_token_and_malformed_settings_are_preserved(self):
        path = self.root / 'settings.json'
        path.write_text('{broken')
        for value in ['', 'api-key', 'sk-ant-oat01-test\nother', 'sk-ant-oat01-test']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                auth.install(self.root, {'token': value}, token=True)
            self.assertEqual(path.read_text(), '{broken')


if __name__ == '__main__':
    unittest.main()
