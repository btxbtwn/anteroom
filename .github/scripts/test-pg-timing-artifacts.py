"""Exercise the pinned duration writer with xdist, without app fixtures or a database."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class PostgresTimingArtifacts(unittest.TestCase):
    def test_workflow_uploads_only_successful_postgres_observations(self):
        workflow = (ROOT / '.github/workflows/ci.yml').read_text()
        postgres = workflow.split('  studio-backend-postgres:', 1)[1].split('  extension:', 1)[0]
        sqlite = workflow.split('  studio-backend:', 1)[1].split('  studio-backend-postgres:', 1)[0]
        self.assertNotIn('--store-durations', sqlite)
        self.assertIn('--store-durations --clean-durations', postgres)
        self.assertIn('id: postgres-tests', postgres)
        self.assertIn("if: success() && steps.postgres-tests.outcome == 'success'", postgres)
        self.assertIn('actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9', postgres)
        self.assertIn('retention-days: 7', postgres)
        self.assertIn('--splits 2 --group ${{ matrix.group }}', postgres)

    def test_xdist_controller_writes_exact_shard_union_and_safe_durations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'test_sample.py'
            source.write_text('import pytest\n@pytest.mark.parametrize("i", range(8))\ndef test_sample(i):\n    assert i >= 0\n')
            expected = {f'test_sample.py::test_sample[{i}]' for i in range(8)}
            groups = []
            for group in (1, 2):
                durations = root / f'shard-{group}.json'
                result = self.run_pytest(root, durations, group)
                self.assertEqual(result.returncode, 0, result.stdout)
                payload = json.loads(durations.read_text())
                self.assertEqual(len(payload), 4)
                self.assertTrue(all(type(value) is float and value >= 0 for value in payload.values()))
                groups.append(set(payload))
            self.assertEqual(groups[0] | groups[1], expected)
            self.assertFalse(groups[0] & groups[1])
            source.write_text('def test_failure():\n    assert False, "PRIVATE_EXCEPTION_SENTINEL"\n')
            failed = root / 'failed.json'
            result = self.run_pytest(root, failed)
            self.assertEqual(result.returncode, 1)
            self.assertNotIn('PRIVATE_EXCEPTION_SENTINEL', failed.read_text())
            self.assertEqual(set(json.loads(failed.read_text())), {'test_sample.py::test_failure'})
            source.write_text('def test_broken(:\n')
            incomplete = root / 'incomplete.json'
            result = self.run_pytest(root, incomplete)
            self.assertNotEqual(result.returncode, 0)
            if incomplete.exists():
                self.assertEqual(json.loads(incomplete.read_text()), {})
            # The writer can produce a partial/failed file: the workflow's success
            # guard is essential; existence alone must never mean a ready profile.

    @staticmethod
    def run_pytest(root, durations, group=None):
        command = [sys.executable, '-m', 'pytest', '-p', 'xdist.plugin', '-p', 'pytest_split.plugin',
                   '-q', '-n', '2', '--store-durations', '--clean-durations', '--durations-path', str(durations)]
        if group is not None:
            command += ['--splits', '2', '--group', str(group)]
        environment = {**os.environ, 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'}
        return subprocess.run(command, check=False, cwd=root, env=environment, text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=30)


if __name__ == '__main__':
    unittest.main()
