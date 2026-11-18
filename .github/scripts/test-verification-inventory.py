"""Real collector integration on tiny synthetic tests. No app fixtures or database.

Run only where locked dev dependencies and a test-capacity grant are available.
Ordinary PR CI runs this so the opt-in verifier cannot hide an untested plugin.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from verification_evidence import digest

SCRIPTS = Path(__file__).resolve().parent


class RealCollection(unittest.TestCase):
    def git(self, root, *args):
        git_env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
        empty_hooks = root / 'empty-hooks'
        empty_hooks.mkdir(exist_ok=True)
        return subprocess.run(['git', '-c', f'core.hooksPath={empty_hooks}', '-C', str(root), *args], check=True, capture_output=True,
                              text=True, timeout=10, env=git_env).stdout.strip()

    def fixture(self, root, source):
        (root / 'test_sample.py').write_text(source)
        self.git(root, 'init', '-q')
        rejecting_hooks = root / '.git/rejecting-hooks'
        rejecting_hooks.mkdir()
        hook = rejecting_hooks / 'pre-commit'
        hook.write_text('#!/bin/sh\nexit 1\n')
        hook.chmod(0o700)
        self.git(root, 'config', 'core.hooksPath', str(rejecting_hooks))
        self.git(root, 'add', 'test_sample.py')
        self.git(root, '-c', 'user.name=Verification', '-c', 'user.email=verification@example.invalid',
                 '-c', 'commit.gpgsign=false', 'commit', '-qm', 'Synthetic fixture')

    def run_fixture(self, root, output, group=None):
        env = {**{key: value for key, value in os.environ.items() if not key.startswith('GIT_')},
               'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1',
               'PYTEST_PLUGINS': 'verification_inventory', 'PYTHONPATH': str(SCRIPTS),
               'VERIFICATION_DATABASE': 'sqlite', 'VERIFICATION_GROUP': str(group or 1),
               'VERIFICATION_INVENTORY_PATH': str(output), 'GITHUB_RUN_ID': '42',
               'GITHUB_RUN_ATTEMPT': '2'}
        command = [sys.executable, '-m', 'pytest', '-p', 'xdist.plugin', '-p', 'pytest_split.plugin',
                   '-q', '-n', '2', '--max-worker-restart=0']
        if group:
            command += ['--splits', '2', '--group', str(group)]
        return subprocess.run(command, cwd=root, env=env, text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=30, check=False)

    def test_real_shard_union_skips_xfails_and_subtests(self):
        with tempfile.TemporaryDirectory(prefix='verification-synthetic-') as directory:
            root = Path(directory)
            self.fixture(root, '''import pytest

def test_pass():
    assert True

@pytest.mark.skip(reason="synthetic skip")
def test_skip():
    assert False

@pytest.mark.xfail(strict=True, reason="synthetic expected failure")
def test_xfail():
    assert False

@pytest.fixture
def teardown_skip():
    yield
    pytest.skip("synthetic teardown skip")

def test_teardown_skip(teardown_skip):
    assert True

def test_subtests(subtests):
    with subtests.test(case="pass"):
        assert True
    with subtests.test(case="skip"):
        pytest.skip("synthetic subtest")
''')
            payloads = []
            for group in (1, 2):
                path = root / f'inventory-{group}.json'
                result = self.run_fixture(root, path, group)
                self.assertEqual(result.returncode, 0, result.stdout)
                payloads.append(json.loads(path.read_text()))
            expected = {digest('test_sample.py::' + name) for name in
                        ('test_pass', 'test_skip', 'test_xfail', 'test_subtests', 'test_teardown_skip')}
            left, right = (set(p['selected']) for p in payloads)
            self.assertEqual(left | right, expected)
            self.assertFalse(left & right)
            self.assertTrue(all(set(p['expected']) == expected for p in payloads))
            passed = set().union(*(set(p['passed']) for p in payloads))
            skipped = set().union(*(set(p['skipped']) for p in payloads))
            self.assertEqual(passed, {digest('test_sample.py::test_pass'), digest('test_sample.py::test_subtests')})
            self.assertEqual(skipped, {digest('test_sample.py::test_skip'), digest('test_sample.py::test_xfail'),
                                       digest('test_sample.py::test_teardown_skip')})
            self.assertTrue(all(p['target'] == self.git(root, 'rev-parse', 'HEAD') for p in payloads))
            self.assertTrue(all(p['tree'] == self.git(root, 'rev-parse', 'HEAD^{tree}') for p in payloads))
            self.assertTrue(all(p['run_id'] == '42' and p['attempt'] == '2' for p in payloads))

    def test_failed_collection_test_subtest_or_worker_never_publishes(self):
        cases = {'failed-test': 'def test_failure():\n    assert False\n',
                 'collection-error': 'def test_broken(:\n',
                 'failed-subtest': 'def test_subtests(subtests):\n    with subtests.test(case="fail"):\n        assert False\n',
                 'worker-loss': 'import os\ndef test_crash():\n    os._exit(7)\n'}
        for case, source in cases.items():
            with self.subTest(case=case), tempfile.TemporaryDirectory(prefix='verification-synthetic-') as directory:
                root = Path(directory)
                self.fixture(root, source)
                path = root / 'must-not-exist.json'
                result = self.run_fixture(root, path)
                # Reject collector errors: a nonzero exit alone cannot prove that
                # the intended pytest/xdist failure path was actually exercised.
                if case == 'collection-error':
                    self.assertIn(result.returncode, (1, 2), result.stdout)
                    self.assertIn('ERROR collecting test_sample.py', result.stdout)
                    self.assertIn('SyntaxError', result.stdout)
                else:
                    self.assertEqual(result.returncode, 1, result.stdout)
                    if case == 'worker-loss':
                        self.assertIn('crashed while running', result.stdout)
                        self.assertIn('test_sample.py::test_crash', result.stdout)
                    else:
                        self.assertIn('AssertionError', result.stdout)
                        self.assertIn('test_failure' if case == 'failed-test' else 'test_subtests', result.stdout)
                self.assertNotIn('INTERNALERROR', result.stdout)
                self.assertNotIn('AttributeError', result.stdout)
                self.assertFalse(path.exists(), result.stdout)


if __name__ == '__main__':
    unittest.main()
