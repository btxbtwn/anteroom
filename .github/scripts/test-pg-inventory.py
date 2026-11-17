"""Small protocol and xdist probes; never import application fixtures."""
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pg_inventory_contract import validate_profile, validate_workers

SCRIPTS = Path(__file__).resolve().parent


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def record(selected):
    return {'provenance': {'commit': 'a' * 40, 'lock_sha256': 'b' * 64, 'run_id': '1', 'attempt': '1',
                           'worker_count': '2', 'python': '3.12.16', 'pytest': '9.2.0', 'pytest-xdist': '3.8.0', 'pytest-split': '0.11.0'},
            'full': [digest(str(i)) for i in range(4)], 'selected': [digest(str(i)) for i in selected]}


class InventoryTests(unittest.TestCase):
    def test_worker_agreement_missing_duplicate_unknown_and_order(self):
        good = record([0, 1])
        self.assertEqual(validate_workers([good, copy.deepcopy(good)]), good)
        for records in ([], [None], [good, record([2, 3])]):
            with self.assertRaises(ValueError):
                validate_workers(records)
        for field, value in [('full', good['full'] + good['full'][:1]),
                             ('selected', good['selected'] + good['selected'][:1]),
                             ('selected', [digest('unknown')]), ('selected', []),
                             ('selected', list(reversed(good['selected']))), ('full', ['PRIVATE_SENTINEL'])]:
            bad = copy.deepcopy(good)
            bad[field] = value
            with self.assertRaises(ValueError):
                validate_workers([bad])

    def test_cross_shard_union_and_observations_fail_closed(self):
        first, second = record([0, 1]), record([2, 3])
        profiles = [{key: .1 for key in item['selected']} for item in (first, second)]
        self.assertEqual(validate_profile([first, second], profiles, first['provenance']), 4)
        for inventories, observations in [([first], profiles), ([first, first], profiles),
                                         ([first, record([3])], profiles), ([first, second], []),
                                         ([first, second], [{}, profiles[1]]),
                                         ([first, second], [{digest('unknown'): .1}, profiles[1]])]:
            with self.assertRaises(ValueError):
                validate_profile(inventories, observations, first['provenance'])
        for value in (float('nan'), float('inf'), -1, True, 'PRIVATE_SENTINEL'):
            bad = copy.deepcopy(profiles)
            bad[0][first['selected'][0]] = value
            with self.assertRaises(ValueError):
                validate_profile([first, second], bad, first['provenance'])
        for field in first['provenance']:
            bad = copy.deepcopy(second)
            bad['provenance'][field] = 'different'
            with self.assertRaises(ValueError):
                validate_profile([first, bad], profiles, first['provenance'])

    def test_real_xdist_collection_hashes_without_changing_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'uv.lock').write_text('synthetic lock\n')
            (root / 'test_sample.py').write_text('import pytest\n@pytest.mark.parametrize("i", range(8), ids=["PRIVATE_SENTINEL" + str(i) for i in range(8)])\ndef test_sample(i):\n    assert i >= 0\n')
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            subprocess.run(['git', '-C', str(root), '-c', 'user.name=Probe', '-c', 'user.email=probe@example.com',
                            'commit', '--allow-empty', '-qm', 'probe'], check=True)
            inventories, profiles = [], []
            for group in (1, 2):
                inventory, durations = root / f'inventory-{group}.json', root / f'durations-{group}.json'
                result = self.probe(root, inventory, durations, group)
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertNotIn('PRIVATE_SENTINEL', inventory.read_text())
                item = json.loads(inventory.read_text())
                raw = json.loads(durations.read_text())
                hashed = {digest(key): value for key, value in raw.items()}
                self.assertEqual(item['selected'], [digest(f'test_sample.py::test_sample[PRIVATE_SENTINEL{i}]')
                                                   for i in range((group - 1) * 4, group * 4)])
                inventories.append(item)
                profiles.append(hashed)
            self.assertEqual(validate_profile(inventories, profiles, inventories[0]['provenance']), 8)
            zero = root / 'zero.json'
            self.assertNotEqual(self.probe(root, zero, root / 'zero-durations.json', 1, workers=0).returncode, 0)
            self.assertFalse(zero.exists())
            (root / 'test_sample.py').write_text('import os\ndef test_crash():\n    if os.environ["PYTEST_XDIST_WORKER"] == "gw0":\n        os._exit(1)\ndef test_other():\n    pass\n')
            crashed = root / 'crash.json'
            self.assertNotEqual(self.probe(root, crashed, root / 'crash-durations.json', 1).returncode, 0)
            self.assertFalse(crashed.exists())
            (root / 'test_sample.py').write_text('def test_failure():\n    assert False, "PRIVATE_SENTINEL"\n')
            output = root / 'failed-inventory.json'
            self.assertNotEqual(self.probe(root, output, root / 'failed-durations.json', 1).returncode, 0)
            self.assertFalse(output.exists())
            (root / 'test_sample.py').write_text('def test_broken(:\n')
            self.assertNotEqual(self.probe(root, output, root / 'broken-durations.json', 1).returncode, 0)
            self.assertFalse(output.exists())

    def test_cli_requires_both_exact_artifacts_and_hides_payload_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            lock = root / 'uv.lock'
            lock.write_text('synthetic lock')
            items = [record([0, 1]), record([2, 3])]
            for group, item in enumerate(items, 1):
                item['provenance']['lock_sha256'] = hashlib.sha256(lock.read_bytes()).hexdigest()
                directory = root / str(group)
                directory.mkdir()
                (directory / f'pg-inventory-1-1-{group}.json').write_text(json.dumps(item))
                (directory / f'pg-duration-hashes-1-1-{group}.json').write_text(json.dumps(dict.fromkeys(item['selected'], .1)))
            command = [sys.executable, str(SCRIPTS / 'verify-pg-inventories.py'), '--directory', str(root),
                       '--commit', 'a' * 40, '--lock', str(lock), '--run-id', '1', '--attempt', '1']
            def run():
                return subprocess.run(command, check=False, capture_output=True, text=True, timeout=10)
            self.assertEqual(run().returncode, 0)
            payload = root / '2/pg-duration-hashes-1-1-2.json'
            payload.write_text('{PRIVATE_SENTINEL')
            result = run()
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('PRIVATE_SENTINEL', result.stderr)
            self.assertNotIn('Traceback', result.stderr)
            payload.unlink()
            self.assertNotEqual(run().returncode, 0)
            self.assertNotEqual(subprocess.run(command[:-1] + ['2'], check=False, capture_output=True).returncode, 0)

    def test_workflow_keeps_gate_and_fences_current_run_downloads(self):
        text = (SCRIPTS.parent / 'workflows/ci.yml').read_text()
        aggregate = text.split('\n  ci:\n', 1)[1]
        self.assertLess(aggregate.index('Require every job'), aggregate.index('actions/download-artifact@'))
        self.assertEqual(aggregate.count('actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333'), 2)
        self.assertEqual(aggregate.count("if: needs.changes.outputs.backend == 'true'"), 4)
        for shard in (1, 2):
            self.assertIn('name: pg-duration-observations-${{ github.run_id }}-${{ github.run_attempt }}-' + str(shard), aggregate)
        self.assertNotIn('pattern:', aggregate)
        self.assertNotIn('github-token:', aggregate)
        self.assertNotIn('repository:', aggregate)
        self.assertNotIn('run-id:', aggregate.split('      - name: Verify exact')[0].replace('# No token/repository/run-id override:', ''))

    @staticmethod
    def probe(root, inventory, durations, group, workers=2):
        env = {**os.environ, 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1', 'PYTHONPATH': str(SCRIPTS),
               'PG_INVENTORY_PATH': str(inventory), 'GITHUB_RUN_ID': '1', 'GITHUB_RUN_ATTEMPT': '1'}
        return subprocess.run([sys.executable, '-m', 'pytest', '-q', '-n', str(workers), '--max-worker-restart=1', '-p', 'xdist.plugin',
                               '-p', 'pytest_split.plugin', '-p', 'pg_inventory', '--splits', '2', '--group', str(group),
                               '--store-durations', '--clean-durations', '--durations-path', str(durations)],
                              cwd=root, env=env, check=False, text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=30)


if __name__ == '__main__':
    unittest.main()
