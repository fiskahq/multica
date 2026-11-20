import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


cleanup = load('cleanup')
wrapper = load('docker-wrapper')
ISSUE = '01a1f048-2cc0-7db5-ac4c-8a2d0e7ec3f8'
OTHER = '01a1f048-2cc0-7db5-ac4c-8a2d0e7ec3f9'
WS = '8f361cba-1bc3-4bec-83a8-04b25c9a025b'


class OwnershipTest(unittest.TestCase):
    def test_compose_scope_is_stable_and_issue_specific(self):
        args = ['--context', 'multica-qa', 'compose', '-f', 'stack.yml', '-p', 'app', 'up', '-d']
        first, env = wrapper.owned_args(args, ISSUE, WS, '/work/a', {})
        second, _ = wrapper.owned_args(args, ISSUE, WS, '/work/b', {})
        other, _ = wrapper.owned_args(args, OTHER, WS, '/work/a', {})
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)
        again, _ = wrapper.owned_args(['compose', 'down'], ISSUE, WS, '/work/a', env)
        self.assertEqual(first[4], again[2])
        self.assertEqual(first[-2:], ['up', '-d'])

    def test_plain_docker_resources_get_labels_and_non_task_calls_stay_unchanged(self):
        for args in [['run', 'alpine'], ['build', '.'], ['volume', 'create', 'data'],
                     ['network', 'create', 'net'], ['buildx', 'build', '.']]:
            owned, _ = wrapper.owned_args(args, ISSUE, WS, '/work', {})
            self.assertIn(cleanup.ISSUE_LABEL + '=' + ISSUE, owned)
            self.assertIn(cleanup.WORKSPACE_LABEL + '=' + WS, owned)
            plain, _ = wrapper.owned_args(args, '', '', '/work', {})
            self.assertEqual(plain, args)

    def test_plan_preserves_unrelated_resources_and_adopts_only_proven_legacy_projects(self):
        def container(cid, project, directory):
            return {'Id': cid, 'Config': {'Labels': {cleanup.PROJECT_LABEL: project,
                    cleanup.PROJECT_LABEL + '.working_dir': directory}}}
        resources = {'container': [container('own', 'legacy-own', '/work/ticket/repo'),
                     container('mixed1', 'shared', '/work/ticket/repo'),
                     container('mixed2', 'shared', '/work/other/repo'),
                     container('foreign', 'foreign', '/work/ticket-sibling/repo')],
                     'image': [{'Id':'image1','Config':{'Labels':{cleanup.PROJECT_LABEL:'legacy-own'}}},
                               {'Id':'shared','Config':{'Labels':{cleanup.PROJECT_LABEL:'shared'}}}],
                     'network': [], 'volume': []}
        result = cleanup.plan(resources, ISSUE, WS, ['/work/ticket'])
        self.assertEqual(result['container'], ['own'])
        self.assertEqual(result['image'], ['image1'])

    def test_cleanup_preserves_resources_used_by_other_containers(self):
        tags = {cleanup.ISSUE_LABEL: ISSUE, cleanup.WORKSPACE_LABEL: WS}
        resources = {
            'container': [{'Id':'ours','Config':{'Labels':tags}},
                          {'Id':'other','Config':{'Labels':{}},'Image':'shared-image',
                           'Mounts':[{'Type':'volume','Name':'shared-volume'}],
                           'NetworkSettings':{'Networks':{'n':{'NetworkID':'shared-network'}}}}],
            'image': [{'Id':'own-image','Config':{'Labels':tags}},
                      {'Id':'shared-image','Config':{'Labels':tags}}],
            'volume': [{'Name':'own-volume','Labels':tags}, {'Name':'shared-volume','Labels':tags}],
            'network': [{'Id':'own-network','Labels':tags}, {'Id':'shared-network','Labels':tags}],
        }
        calls=[]
        def docker(*args):
            calls.append(args)
            return 'id' if 'rm' not in args else ''
        with patch.object(cleanup, 'docker', side_effect=docker), \
             patch.object(cleanup, 'inspect', side_effect=lambda kind,ids: resources[kind]), \
             patch.object(cleanup.subprocess, 'run', return_value=SimpleNamespace(returncode=0,stdout='multica-qa\n')):
            cleanup.cleanup({'issue_id':ISSUE,'workspace_id':WS,'roots':['/work/ticket']})
        deletions=[args for args in calls if 'rm' in args]
        self.assertEqual(deletions, [('container','rm','--force','--volumes','ours'),
                                    ('network','rm','own-network'),('volume','rm','own-volume'),
                                    ('image','rm','own-image')])

    def test_labels_require_matching_workspace_and_symlinks_cannot_prove_legacy_ownership(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/'root'; root.mkdir()
            other=Path(td)/'other'; other.mkdir()
            (root/'escape').symlink_to(other, target_is_directory=True)
            self.assertFalse(cleanup.inside(str(root/'escape'), [str(root)]))
        resources = {'container': [], 'image': [], 'network': [], 'volume': [
            {'Name':'own','Labels':{cleanup.ISSUE_LABEL:ISSUE,cleanup.WORKSPACE_LABEL:WS}},
            {'Name':'foreign','Labels':{cleanup.ISSUE_LABEL:ISSUE,cleanup.WORKSPACE_LABEL:OTHER}}]}
        self.assertEqual(cleanup.plan(resources, ISSUE, WS, ['/work/ticket'])['volume'], ['own'])


if __name__ == '__main__':
    unittest.main()
