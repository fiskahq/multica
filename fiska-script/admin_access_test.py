import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('admin_access', ROOT / 'configure-admin-access.py')
admin = importlib.util.module_from_spec(spec); spec.loader.exec_module(admin)


class AdminAccessTests(unittest.TestCase):
    def test_only_actual_squad_members_receive_scoped_access_and_rerun_preserves_it(self):
        agents = {'a': {'id':'a','name':'admin-worker','workspace_id':'w','instructions':'Keep privacy rules.'},
                  'outside': {'id':'outside','name':'unrelated','workspace_id':'w','instructions':'Untouched.'}}
        squad = {'id':'s','name':'Software Development Management','instructions':'Leader rules.'}
        servers = [{'id': n, 'name': n} for n in ('factory-jira','factory-jira-triage','factory-confluence','factory-confluence-write','factory-jira-release')]
        bindings = {'a': [], 'outside': []}
        writes = []
        def command(kind, operation, *args, **kw):
            if kind == 'workspace': return copy.deepcopy(servers)
            if kind == 'squad' and operation == 'member': return [{'member_id':'a','member_type':'agent'}]
            if operation == 'list': return [copy.deepcopy(squad)]
            if operation == 'mcp':
                if args[0] == 'add':
                    bindings[args[1]].append({'id': args[2], 'enabled': True}); writes.append(args)
                return copy.deepcopy(bindings[args[1]])
            row = squad if kind == 'squad' else agents[args[0]]
            if operation == 'update': row['instructions'] = args[2]; writes.append(args)
            return copy.deepcopy(row)
        with tempfile.TemporaryDirectory() as tmp, patch.object(admin.a, 'command', side_effect=command):
            admin.configure(Path(tmp))
            self.assertEqual(len(bindings['a']),4)
            self.assertEqual(bindings['outside'],[])
            self.assertNotIn('factory-jira-release',[b['id'] for b in bindings['a']])
            self.assertIn('Keep privacy rules.',agents['a']['instructions'])
            count=len(writes)
            admin.configure(Path(tmp))
            self.assertEqual(len(writes),count)


if __name__ == '__main__': unittest.main()
