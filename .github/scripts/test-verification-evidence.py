"""Bounded stdlib tests: fake API, fake pytest hooks, no application or database."""
import importlib.util
import os
from pathlib import Path
import sys
from types import SimpleNamespace, ModuleType
import unittest
from unittest.mock import patch

import verification_evidence as evidence

ROOT = Path(__file__).resolve().parents[2]
TARGET, BASE = 'a' * 40, 'b' * 40
MANIFEST = {'target': TARGET, 'tree': 'c' * 40, 'run_id': '42', 'attempt': '2'}


def inventories():
    ids = [evidence.digest(evidence.REGRESSION), evidence.digest('other-test')]
    return [{**MANIFEST, 'database': database, 'group': group,
             'expected': ids, 'selected': [ids[group-1]], 'passed': [ids[group-1]], 'skipped': []}
            for database in ('sqlite', 'postgres') for group in (1, 2)]


def jobs():
    return [{'id': i, 'name': 'checks / ' + name, 'status': 'completed', 'conclusion': 'success'}
            for i, name in enumerate(sorted(evidence.CHECKS), 1)]


class VerificationEvidence(unittest.TestCase):
    def test_complete_inventory_requires_every_collected_test_once_per_database(self):
        result = evidence.validate_inventory(inventories(), MANIFEST)
        self.assertEqual(result['postgres']['passed'], 2)
        self.assertEqual(result['sqlite']['collected'], 2)

    def test_missing_duplicate_or_extra_database_shards_fail(self):
        cases = [inventories()[:-1], inventories() + [inventories()[0]]]
        bad = inventories()
        bad[0]['database'] = 'untrusted'
        cases.append(bad)
        for value in cases:
            with self.assertRaises(ValueError):
                evidence.validate_inventory(value, MANIFEST)

    def test_wrong_commit_tree_run_or_attempt_fails(self):
        for key in MANIFEST:
            value = inventories()
            value[0][key] = 'wrong'
            with self.subTest(key=key), self.assertRaises(ValueError):
                evidence.validate_inventory(value, MANIFEST)

    def test_shard_overlap_omission_and_disagreement_fail(self):
        for field in ('expected', 'selected', 'passed'):
            value = inventories()
            value[0][field] = [evidence.digest('different')]
            with self.subTest(field=field), self.assertRaises(ValueError):
                evidence.validate_inventory(value, MANIFEST)
        value = inventories()
        value[1].update(selected=value[0]['selected'], passed=value[0]['passed'])
        with self.assertRaises(ValueError):
            evidence.validate_inventory(value, MANIFEST)

    def test_empty_nonhashed_and_duplicate_identifiers_fail(self):
        for key in ('expected', 'selected', 'passed', 'skipped'):
            for bad in (None, ['private node id'], ['0'*64, '0'*64]):
                value = inventories()
                value[0][key] = bad
                with self.subTest(key=key, bad=bad), self.assertRaises(ValueError):
                    evidence.validate_inventory(value, MANIFEST)

    def test_historical_postgres_regression_must_pass_not_skip(self):
        value = inventories()
        value[2].update(passed=[], skipped=value[2]['selected'])
        with self.assertRaisesRegex(ValueError, 'actually pass'):
            evidence.validate_inventory(value, MANIFEST)

    def test_normal_skips_remain_visible_without_faking_passes(self):
        value = inventories()
        value[3].update(passed=[], skipped=value[3]['selected'])
        result = evidence.validate_inventory(value, MANIFEST)
        self.assertEqual(result['postgres']['skipped'], 1)
        self.assertEqual(result['postgres']['passed'], 1)

    def test_missing_or_nonterminal_test_outcomes_fail(self):
        for passed, skipped in (([], []), (['0'*64], [])):
            value = inventories()
            value[0].update(passed=passed, skipped=skipped)
            with self.assertRaises(ValueError):
                evidence.validate_inventory(value, MANIFEST)

    def test_job_success_requires_all_expanded_jobs(self):
        self.assertEqual(len(evidence.validate_jobs(jobs())), len(evidence.CHECKS))
        for state in ('failure', 'cancelled', 'skipped', 'neutral', None):
            value = jobs()
            value[0]['conclusion'] = state
            with self.subTest(state=state), self.assertRaises(ValueError):
                evidence.validate_jobs(value)
        for value in (jobs()[:-1], jobs() + [jobs()[0]], jobs() + [
                {'id': 900, 'name': 'checks / new uncontracted job', 'conclusion': 'success'}]):
            with self.assertRaises(ValueError):
                evidence.validate_jobs(value)

    def test_reusable_dependency_job_name_is_not_a_skip_exception(self):
        value = jobs()
        for j in value:
            if j['name'].endswith('Audit locked Python dependencies'):
                j['name'] = 'checks / Dependency audit / Audit locked Python dependencies'
        evidence.validate_jobs(value)

    def test_pagination_uses_exact_attempt_and_keeps_all_jobs(self):
        first = [{'id': n} for n in range(100)]
        pages = [{'total_count': 101, 'jobs': first}, {'total_count': 101, 'jobs': [{'id': 100}]}]
        calls = []
        def fetch(path):
            calls.append(path)
            return pages.pop(0)
        self.assertEqual(len(evidence.all_jobs(fetch, '42', '2')), 101)
        self.assertTrue(all('runs/42/attempts/2/jobs?' in p for p in calls))
        self.assertTrue(calls[-1].endswith('page=2'))

    def test_truncated_repeated_inconsistent_and_excess_pages_fail(self):
        first = [{'id': n} for n in range(100)]
        cases = [[{'total_count': 101, 'jobs': first[:5]}],
                 [{'total_count': 101, 'jobs': first}, {'total_count': 101, 'jobs': first}],
                 [{'total_count': 101, 'jobs': first}, {'total_count': 102, 'jobs': [{'id': 100}]}],
                 [{'total_count': 0, 'jobs': []}], [{'jobs': []}]]
        for pages in cases:
            with self.assertRaises(ValueError):
                evidence.all_jobs(lambda path: pages.pop(0), '42', '2')

    def test_full_sha_only(self):
        for value in ('main', 'HEAD', TARGET[:8], 'A'*40, '../main', None):
            with self.assertRaises(ValueError):
                evidence.require_sha(value)

    def fake_git(self, *args):
        if args == ('rev-parse', 'HEAD'):
            return TARGET
        if args == ('rev-list', '--first-parent', 'origin/main'):
            return '\n'.join((TARGET, BASE))
        if args[0] == 'merge-base':
            return ''
        if args[:2] == ('diff', '--name-only'):
            return 'anteroom-ios/Sources/Foo.swift\nserver/migrations/versions/new.py'
        if args[:2] == ('rev-list', '--first-parent'):
            return TARGET
        return 'c'*40

    @patch.dict(os.environ, {'GITHUB_SHA': TARGET, 'GITHUB_REPOSITORY': 'owner/repo',
                            'GITHUB_RUN_ID': '42', 'GITHUB_RUN_ATTEMPT': '2',
                            'GITHUB_EVENT_NAME': 'workflow_dispatch'})
    def test_preparation_pins_source_and_discloses_unverified_live_and_native(self):
        with patch.object(evidence, 'git', self.fake_git):
            result = evidence.prepare(TARGET, BASE)
        self.assertFalse(result['deployment_authorized'])
        self.assertFalse(result['production_base_is_live_verified'])
        self.assertEqual(result['native_changes'], ['anteroom-ios/Sources/Foo.swift'])
        self.assertTrue(result['migration_changes'])

    @patch.dict(os.environ, {'GITHUB_SHA': TARGET, 'GITHUB_EVENT_NAME': 'workflow_dispatch'})
    def test_wrong_target_nonfirstparent_same_or_reversed_span_fails(self):
        with patch.object(evidence, 'git', self.fake_git):
            for target, base in ((BASE, TARGET), (TARGET, TARGET), (TARGET, 'd'*40)):
                with self.assertRaises(ValueError):
                    evidence.prepare(target, base)

    def test_foreign_repo_wrong_event_sha_workflow_and_old_attempt_fail(self):
        manifest = {**MANIFEST, 'repository': 'owner/repo'}
        good = {'head_sha': TARGET, 'event': 'workflow_dispatch', 'run_attempt': 2,
                'path': evidence.WORKFLOW, 'repository': {'full_name': 'owner/repo'}}
        evidence.validate_run(good, manifest)
        for key, value in (('head_sha', BASE), ('event', 'push'), ('event', 'pull_request'),
                           ('path', '.github/workflows/ci.yml'), ('run_attempt', 1),
                           ('repository', {'full_name': 'foreign/repo'})):
            with self.subTest(key=key), self.assertRaises(ValueError):
                evidence.validate_run({**good, key: value}, manifest)

    def test_manual_workflow_has_no_release_or_production_privileges(self):
        text = (ROOT / evidence.WORKFLOW).read_text()
        self.assertIn('name: Descendant verification', text)
        self.assertIn('  workflow_dispatch:', text)
        for forbidden in ('  push:', '  pull_request:', '  workflow_run:', 'secrets:', 'environment:',
                          'contents: write', 'actions: write', 'id-token:', 'flyctl', 'alembic', 'deploy-hosted', 'secrets: inherit'):
            self.assertNotIn(forbidden, text)
        self.assertIn('full_verification: true', text)
        self.assertEqual(text.count('ref: ${{ github.sha }}'), 2)
        self.assertIn('needs: [prepare, checks]', text)
        self.assertIn('actions: read', text)
        ci = (ROOT / '.github/workflows/ci.yml').read_text()
        self.assertIn('  workflow_call:', ci)
        self.assertIn('default: false', ci)
        self.assertIn('if [ "$FULL_VERIFICATION" = "true" ]; then base=""; fi', ci)
        self.assertEqual(ci.count('VERIFICATION_DATABASE:'), 2)
        self.assertEqual(ci.count('if: success() && inputs.full_verification'), 2)
        self.assertIn('python3 .github/scripts/test-verification-evidence.py', ci)
        self.assertIn('python ../.github/scripts/test-verification-inventory.py', ci)

    def test_receipt_cli_only_reads_api_and_cannot_consume_approvals(self):
        text = Path(evidence.__file__).read_text()
        self.assertIn("['gh', 'api', f'repos/{repo}/{path}']", text)
        for forbidden in ('--method', 'workflow_dispatch', 'deployments', 'gh pr merge'):
            if forbidden != 'workflow_dispatch':
                self.assertNotIn(forbidden, text)


class CollectorContract(unittest.TestCase):
    def setUp(self):
        # Load hooks with a fake decorator, without importing pytest or its plugins.
        fake = ModuleType('pytest')
        fake.hookimpl = lambda **kwargs: (lambda fn: fn)
        spec = importlib.util.spec_from_file_location('inventory_under_test', Path(__file__).with_name('verification_inventory.py'))
        self.plugin = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'pytest': fake}):
            spec.loader.exec_module(self.plugin)

    def test_collection_records_full_before_split_and_selected_after(self):
        items = [SimpleNamespace(nodeid='one'), SimpleNamespace(nodeid='two')]
        hook = self.plugin.pytest_collection_modifyitems(items)
        next(hook)
        items.pop()
        with self.assertRaises(StopIteration):
            next(hook)
        self.assertEqual(len(self.plugin.state.expected), 2)
        self.assertEqual(len(self.plugin.state.selected), 1)

    def test_subtest_skip_cannot_disguise_parent_outcome(self):
        p = self.plugin
        p.pytest_runtest_logreport(SimpleNamespace(nodeid='one', when='call', passed=False, skipped=True, context='subtest'))
        p.pytest_runtest_logreport(SimpleNamespace(nodeid='one', when='call', passed=True, skipped=False))
        self.assertEqual(p.state.passed, {evidence.digest('one')})
        self.assertFalse(p.state.skipped)

    def test_worker_loss_prevents_inventory(self):
        p = self.plugin
        p.pytest_testnodedown(SimpleNamespace(workeroutput={}), 'worker died')
        with patch.object(p, 'snapshot', side_effect=AssertionError('must not publish')):
            p.pytest_sessionfinish(SimpleNamespace(config=SimpleNamespace()), 0)
        self.assertTrue(p.state.invalid)

    def test_failed_session_never_writes_inventory(self):
        with patch.object(self.plugin, 'snapshot', side_effect=AssertionError('must not publish')):
            self.plugin.pytest_sessionfinish(SimpleNamespace(config=SimpleNamespace()), 1)

    def test_worker_inventory_is_transferred_not_written_by_worker(self):
        config = SimpleNamespace(workerinput={}, workeroutput={})
        self.plugin.pytest_sessionfinish(SimpleNamespace(config=config), 0)
        self.assertIn('verification_inventory', config.workeroutput)

    def test_disagreeing_workers_fail(self):
        p = self.plugin
        p.state.workers = [{'expected': ['a'], 'selected': ['a']}, {'expected': ['b'], 'selected': ['a']}]
        with self.assertRaises(RuntimeError):
            p.pytest_sessionfinish(SimpleNamespace(config=SimpleNamespace()), 0)


if __name__ == '__main__':
    unittest.main()
