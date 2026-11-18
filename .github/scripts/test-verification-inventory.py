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
        return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True,
                              text=True, timeout=10).stdout.strip()

    def fixture(self, root, source):
        (root / 'test_sample.py').write_text(source)
        self.git(root, 'init', '-q')
        self.git(root, 'add', 'test_sample.py')
        self.git(root, '-c', 'user.name=Verification', '-c', 'user.email=verification@example.invalid',
                 '-c', 'commit.gpgsign=false', 'commit', '-qm', 'Synthetic fixture')

    def run_fixture(self, root, output, group=None):
        env = {**os.environ, 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1',
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
                        ('test_pass', 'test_skip', 'test_xfail', 'test_subtests')}
            left, right = (set(p['selected']) for p in payloads)
            self.assertEqual(left | right, expected)
            self.assertFalse(left & right)
            self.assertTrue(all(set(p['expected']) == expected for p in payloads))
            passed = set().union(*(set(p['passed']) for p in payloads))
            skipped = set().union(*(set(p['skipped']) for p in payloads))
            self.assertEqual(passed, {digest('test_sample.py::test_pass'), digest('test_sample.py::test_subtests')})
            self.assertEqual(skipped, {digest('test_sample.py::test_skip'), digest('test_sample.py::test_xfail')})
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
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertFalse(path.exists(), result.stdout)


if __name__ == '__main__':
    unittest.main()
