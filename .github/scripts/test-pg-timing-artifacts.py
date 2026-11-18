"""Exercise the pinned duration writer with xdist, without app fixtures or a database."""
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


class PostgresTimingArtifacts(unittest.TestCase):
    def test_workflow_uploads_only_successful_postgres_observations(self):
        workflow = (ROOT / '.github/workflows/ci.yml').read_text()
        postgres = workflow.split('  studio-backend-postgres:', 1)[1].split('  extension:', 1)[0]
        sqlite = workflow.split('  studio-backend:', 1)[1].split('  studio-backend-postgres:', 1)[0]
        self.assertNotIn('--store-durations', sqlite)
        self.assertIn('--store-durations --clean-durations', postgres)
        path = '${{ runner.temp }}/pg-durations-${{ github.run_id }}-${{ github.run_attempt }}-${{ matrix.group }}.json'
        self.assertIn(f'--durations-path "{path}"', postgres)
        self.assertIn(f'test ! -e "{path}"', postgres)
        safe_path = '${{ runner.temp }}/pg-observations-${{ github.run_id }}-${{ github.run_attempt }}-${{ matrix.group }}/durations.json'
        self.assertIn(f'--input "{path}"', postgres)
        self.assertIn(f'--output "{safe_path}"', postgres)
        steps = yaml.load(workflow, Loader=yaml.BaseLoader)['jobs']['studio-backend-postgres']['steps']
        uploads = [step for step in steps if step.get('uses', '').startswith('actions/upload-artifact@')]
        self.assertEqual(len(uploads), 1)
        inventory_path = safe_path.removesuffix('durations.json') + 'inventory.json'
        self.assert_hash_only_upload_paths(uploads[0]['with']['path'], [safe_path, inventory_path])
        self.assertIn('id: postgres-tests', postgres)
        self.assertIn("if: success() && steps.postgres-tests.outcome == 'success'", postgres)
        self.assertIn('actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9', postgres)
        self.assertIn('retention-days: 7', postgres)
        self.assertIn('--splits 2 --group ${{ matrix.group }}', postgres)

    def assert_hash_only_upload_paths(self, value, allowed):
        entries = [line.strip() for line in value.splitlines() if line.strip()]
        self.assertCountEqual(entries, allowed)

    def test_raw_paths_are_rejected_in_single_and_multiline_uploads(self):
        raw = '/tmp/pg-durations.json'
        safe = '/tmp/pg-observations/durations.json'
        inventory = '/tmp/pg-observations/inventory.json'
        for source in (f'path: {raw}\n',
                       f'path: |\n  {safe}\n  {inventory}\n  {raw}\n'):
            with self.subTest(source=source), self.assertRaises(AssertionError):
                value = yaml.load(source, Loader=yaml.BaseLoader)['path']
                self.assert_hash_only_upload_paths(value, [safe, inventory])
        value = yaml.load(f'path: |\n  {safe}\n  {inventory}\n', Loader=yaml.BaseLoader)['path']
        self.assert_hash_only_upload_paths(value, [safe, inventory])

    def test_xdist_controller_writes_exact_shard_union_and_safe_durations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'test_sample.py'
            source.write_text('import pytest\n@pytest.mark.parametrize("i", range(8), ids=["PRIVATE_PARAM_SENTINEL" + str(i) for i in range(8)])\ndef test_sample(i):\n    assert i >= 0\n')
            expected = {f'test_sample.py::test_sample[PRIVATE_PARAM_SENTINEL{i}]' for i in range(8)}
            groups = []
            for group in (1, 2):
                durations = root / f'shard-{group}.json'
                self.assertFalse(durations.exists())
                result = self.run_pytest(root, durations, group)
                self.assertEqual(result.returncode, 0, result.stdout)
                payload = json.loads(durations.read_text())
                self.assertEqual(len(payload), 4)
                self.assertTrue(all(type(value) is float and math.isfinite(value) and value >= 0 for value in payload.values()))
                safe = root / f'safe-{group}.json'
                result = self.sanitize(durations, safe)
                self.assertEqual(result.returncode, 0, result.stdout)
                published = json.loads(safe.read_text())
                self.assertEqual(published, {hashlib.sha256(key.encode()).hexdigest(): value
                                             for key, value in payload.items()})
                self.assertNotIn('PRIVATE_PARAM_SENTINEL', safe.read_text())
                groups.append(set(payload))
            self.assertEqual(groups[0] | groups[1], expected)
            self.assertFalse(groups[0] & groups[1])
            source.write_text('def test_failure():\n    assert False, "PRIVATE_EXCEPTION_SENTINEL"\n')
            failed = root / 'failed.json'
            result = self.run_pytest(root, failed)
            self.assertEqual(result.returncode, 1)
            self.assertNotIn('PRIVATE_EXCEPTION_SENTINEL', failed.read_text())
            self.assertEqual(set(json.loads(failed.read_text())), {'test_sample.py::test_failure'})
            failure_safe = root / 'failure-safe.json'
            self.assertEqual(self.sanitize(failed, failure_safe).returncode, 0)
            self.assertNotIn('PRIVATE_EXCEPTION_SENTINEL', failure_safe.read_text())
            self.assertNotIn('test_failure', failure_safe.read_text())
            source.write_text('def test_broken(:\n')
            incomplete = root / 'incomplete.json'
            result = self.run_pytest(root, incomplete)
            self.assertNotEqual(result.returncode, 0)
            if incomplete.exists():
                self.assertEqual(json.loads(incomplete.read_text()), {})
            # The writer can produce a partial/failed file: the workflow's success
            # guard is essential; existence alone must never mean a ready profile.

    def test_sanitizer_rejects_malformed_nonfinite_and_private_payloads(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw, safe = root / 'raw.json', root / 'safe.json'
            for payload in ('{PRIVATE_PARAM_SENTINEL', '[]', '{}',
                            '{"PRIVATE_PARAM_SENTINEL": NaN}', '{"PRIVATE_PARAM_SENTINEL": Infinity}',
                            '{"PRIVATE_PARAM_SENTINEL": -1}', '{"PRIVATE_PARAM_SENTINEL": true}',
                            '{"PRIVATE_PARAM_SENTINEL": "credential"}'):
                raw.write_text(payload)
                result = self.sanitize(raw, safe)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('PRIVATE_PARAM_SENTINEL', result.stdout)
                self.assertFalse(safe.exists())

    @staticmethod
    def sanitize(raw, safe):
        return subprocess.run([sys.executable, str(ROOT / '.github/scripts/hash-test-durations.py'),
                               '--input', str(raw), '--output', str(safe)], check=False,
                              text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10)

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
