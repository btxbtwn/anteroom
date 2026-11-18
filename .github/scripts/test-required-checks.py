"""Regression tests for the required CI aggregator; standard library only."""
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name('required-checks.py')
GROUPS = ('frontend', 'backend', 'browser', 'health', 'audit', 'extension', 'relay')
JOBS = {
    'studio-frontend': 'frontend', 'studio-browser': 'browser',
    'studio-code-health': 'health', 'dependency-audit': 'audit',
    'studio-backend': 'backend', 'studio-backend-postgres': 'backend',
    'extension': 'extension', 'webhook-relay': 'relay',
}


def needs(selected=True):
    result = {name: {'result': 'success'} for name in ('changes', 'changelog', 'repo-checks')}
    result['changes']['outputs'] = {name: str(selected).lower() for name in GROUPS}
    result.update({name: {'result': 'success' if selected else 'skipped'} for name in JOBS})
    return result


class RequiredChecksTests(unittest.TestCase):
    def check(self, value, expected):
        payload = json.dumps(value) if not isinstance(value, str) else value
        proc = subprocess.run([sys.executable, str(SCRIPT)], env={**os.environ, 'NEEDS_JSON': payload},
                              text=True, capture_output=True, check=False)
        self.assertEqual(proc.returncode, expected, proc.stdout + proc.stderr)
        self.assertNotIn('Traceback', proc.stderr)

    def test_all_selected_checks_pass(self):
        self.check(needs(), 0)

    def test_unchanged_components_can_skip(self):
        self.check(needs(False), 0)

    def test_selected_checks_cannot_skip(self):
        for name in JOBS:
            with self.subTest(name=name):
                value = needs()
                value[name]['result'] = 'skipped'
                self.check(value, 1)

    def test_required_unconditional_checks_cannot_skip(self):
        for name in ('changes', 'changelog', 'repo-checks'):
            value = needs(False)
            value[name]['result'] = 'skipped'
            self.check(value, 1)

    def test_failures_and_cancellations_never_pass_even_if_unselected(self):
        for state in ('failure', 'cancelled', '', 'pending', 'neutral'):
            for selected in (True, False):
                value = needs(selected)
                value['studio-backend']['result'] = state
                self.check(value, 1)

    def test_missing_or_unknown_contract_entries_fail(self):
        for name in needs():
            value = needs()
            del value[name]
            self.check(value, 1)
        value = needs()
        value['new-unmapped-job'] = {'result': 'success'}
        self.check(value, 1)
        for flag in (None, True, '', 'TRUE', 'unknown'):
            value = needs()
            value['changes']['outputs']['backend'] = flag
            self.check(value, 1)
        value = needs()
        del value['changes']['outputs']['backend']
        self.check(value, 1)

    def test_invalid_payload_fails_without_traceback(self):
        for value in ('', '{', '[]', 'null', '{"changes":null}'):
            self.check(value, 1)

    def test_workflow_calls_validator_with_complete_needs(self):
        workflow = SCRIPT.parents[1] / 'workflows' / 'ci.yml'
        text = workflow.read_text()
        self.assertIn('NEEDS_JSON: ${{ toJSON(needs) }}', text)
        self.assertIn('python3 .github/scripts/required-checks.py', text)
        self.assertIn('python3 .github/scripts/test-required-checks.py', text)
        aggregate = text.split('\n  ci:\n', 1)[1]
        block = aggregate.split('    needs:\n', 1)[1].split('    runs-on:', 1)[0]
        actual = set(re.findall(r'^      - ([a-z-]+)$', block, re.MULTILINE))
        self.assertEqual(actual, set(needs()))


if __name__ == '__main__':
    unittest.main()
