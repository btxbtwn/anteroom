"""Bounded stdlib tests: fake API, fake pytest hooks, no application or database."""
import importlib.util
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace, ModuleType
import unittest
from unittest.mock import patch

import verification_evidence as evidence
from test_recovery_evidence import RecoveryEvidenceTests  # unittest collects the offline consumer contract.
from test_wait_for_tested_code import (  # unittest collects the deploy-gate contracts.
    WaitForTestedCodePushCoverageTests,
    WaitForTestedCodeRecoveryTests,
)

ROOT = Path(__file__).resolve().parents[2]
TARGET, BASE = 'a' * 40, 'b' * 40
MANIFEST = {
    'target': TARGET,
    'tree': 'c' * 40,
    'ci_blob': 'e' * 40,
    'run_id': '42',
    'attempt': '2',
    'browser_regression_blob': evidence.BROWSER_REGRESSION_BLOB,
}


def current_jobs(value):
    text = (ROOT / '.github/workflows/ci.yml').read_text()
    raw = text.encode()
    blob = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
    return evidence.validate_jobs(value, blob, lambda *args: text)


def inventories():
    ids = [evidence.digest(evidence.REGRESSION), evidence.digest('other-test')]
    return [{**{k: MANIFEST[k] for k in ('target', 'tree', 'run_id', 'attempt')}, 'database': database, 'group': group,
             'expected': ids, 'selected': [ids[group-1]], 'passed': [ids[group-1]], 'skipped': []}
            for database in ('sqlite', 'postgres') for group in (1, 2)]


def browser_inventory():
    reg_id = evidence.digest('pw-chat-streaming')
    rows = [
        {'id': evidence.digest('d-1'), 'project': 'desktop', 'file': evidence.digest('f-1'), 'expected': 'passed'},
        {'id': evidence.digest('p-1'), 'project': 'phone', 'file': evidence.digest('f-1'), 'expected': 'passed'},
        {'id': reg_id, 'project': 'phone-webkit', 'file': evidence.digest('f-1'), 'expected': 'passed'},
    ]
    return {
        'schema': 1,
        'target': MANIFEST['target'],
        'tree': MANIFEST['tree'],
        'ci_blob': MANIFEST['ci_blob'],
        'run_id': MANIFEST['run_id'],
        'attempt': MANIFEST['attempt'],
        'regression_file': evidence.BROWSER_REGRESSION_FILE,
        'regression_file_blob': evidence.BROWSER_REGRESSION_BLOB,
        'regression_key_sha256': evidence.digest(evidence.BROWSER_REGRESSION_KEY),
        'regression_case_id': reg_id,
        'full': rows,
        'cases': [
            {**rows[0], 'shard': 1, 'attempts': [{'status': 'passed', 'duration': 10, 'retry': 0}]},
            {**rows[1], 'shard': 1, 'expected': 'skipped', 'attempts': [{'status': 'skipped', 'duration': 0, 'retry': 0}]},
            {**rows[2], 'shard': 2, 'attempts': [{'status': 'passed', 'duration': 25, 'retry': 0}]},
        ],
    }


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
        for key in ('target', 'tree', 'run_id', 'attempt'):
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

    def test_browser_inventory_requires_three_projects_and_passing_phone_webkit_regression(self):
        result = evidence.validate_browser_inventory(browser_inventory(), MANIFEST)
        self.assertTrue(result['regression']['passed'])
        self.assertEqual(result['regression']['attempts'], 1)
        self.assertEqual(result['regression']['project'], 'phone-webkit')
        self.assertEqual(result['regression']['shard'], 2)
        self.assertEqual(result['projects']['phone']['skipped'], 1)
        self.assertEqual(result['projects']['phone-webkit']['passed'], 1)

    def test_browser_inventory_retains_three_project_partitions(self):
        payload = browser_inventory()
        for index, row in enumerate(payload['cases'], 1):
            row['shard'] = index
        result = evidence.validate_browser_inventory(payload, MANIFEST)
        self.assertEqual(result['regression']['shard'], 3)
        self.assertEqual(result['projects']['phone-webkit']['shard_counts'], [0, 0, 1])

    def test_browser_inventory_rejects_skip_retry_wrong_provenance_altered_blob_and_parity_breaks(self):
        # Retried pass (failed retry=0 then passed retry=1) must fail closed.
        retried = browser_inventory()
        retried['cases'][2]['attempts'] = [
            {'status': 'failed', 'duration': 10, 'retry': 0},
            {'status': 'passed', 'duration': 15, 'retry': 1},
        ]
        with self.assertRaisesRegex(ValueError, 'first attempt'):
            evidence.validate_browser_inventory(retried, MANIFEST)

        # Skipped phone-webkit chatStreaming regression must fail closed.
        skipped = browser_inventory()
        skipped['cases'][2]['expected'] = 'skipped'
        skipped['cases'][2]['attempts'] = [{'status': 'skipped', 'duration': 0, 'retry': 0}]
        with self.assertRaisesRegex(ValueError, 'first attempt'):
            evidence.validate_browser_inventory(skipped, MANIFEST)

        # Wrong target/tree/ci_blob/run_id/attempt or altered test file blob must fail closed.
        for key in ('target', 'tree', 'ci_blob', 'run_id', 'attempt', 'regression_file_blob', 'regression_key_sha256'):
            bad = browser_inventory()
            bad[key] = 'f' * len(str(bad[key]))
            with self.subTest(key=key), self.assertRaises(ValueError):
                evidence.validate_browser_inventory(bad, MANIFEST)

        # Missing case or wrong project for regression case must fail closed.
        missing = browser_inventory()
        missing['cases'].pop()
        with self.assertRaises(ValueError):
            evidence.validate_browser_inventory(missing, MANIFEST)

        wrong_project = browser_inventory()
        wrong_project['regression_case_id'] = wrong_project['cases'][0]['id']
        with self.assertRaisesRegex(ValueError, 'first attempt'):
            evidence.validate_browser_inventory(wrong_project, MANIFEST)

        # Bool duration or non-int shard must fail closed.
        bool_dur = browser_inventory()
        bool_dur['cases'][0]['attempts'][0]['duration'] = True
        with self.assertRaises(ValueError):
            evidence.validate_browser_inventory(bool_dur, MANIFEST)

    def test_job_success_requires_all_expanded_jobs(self):
        self.assertEqual(len(current_jobs(jobs())), len(evidence.CHECKS))
        for state in ('failure', 'cancelled', 'skipped', 'neutral', None):
            value = jobs()
            value[0]['conclusion'] = state
            with self.subTest(state=state), self.assertRaises(ValueError):
                current_jobs(value)
        for value in (jobs()[:-1], jobs() + [jobs()[0]], jobs() + [{'id': 901, 'name': 'unknown job'}], jobs() + [
                {'id': 900, 'name': 'checks / new uncontracted job', 'conclusion': 'success'}]):
            with self.assertRaises(ValueError):
                current_jobs(value)

    def test_reusable_dependency_job_name_is_not_a_skip_exception(self):
        value = jobs()
        for j in value:
            if j['name'].endswith('Audit locked Python dependencies'):
                j['name'] = 'checks / Dependency audit / Audit locked Python dependencies'
        current_jobs(value)

    def test_pinned_historical_receipt_keeps_its_exact_job_contract(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures/recovery-receipt-46093755351.json').read_text())
        receipt = fixture['receipt']
        self.assertEqual(receipt['run_id'], '46093755351')
        self.assertEqual(receipt['attempt'], '1')
        self.assertEqual(receipt['ci_blob'], evidence.LEGACY_CI_BLOB)
        self.assertEqual(fixture['artifact_sha256'], '2a98eb5e6a9efd67a9c1ca20a5925b134c84390f0bce1415109d4d19762a095d')
        actual = evidence.validate_jobs(fixture['jobs'], receipt['ci_blob'])
        self.assertEqual(sorted(actual, key=lambda j: j['id']), sorted(receipt['jobs'], key=lambda j: j['id']))
        with self.assertRaises(ValueError):
            current_jobs(fixture['jobs'])
        for value in (fixture['jobs'][:-2], fixture['jobs'] + [jobs()[0]]):
            with self.assertRaises(ValueError):
                evidence.validate_jobs(value, receipt['ci_blob'])
        failed = copy.deepcopy(fixture['jobs'])
        next(j for j in failed if j['name'].startswith('checks / '))['conclusion'] = 'skipped'
        with self.assertRaises(ValueError):
            evidence.validate_jobs(failed, receipt['ci_blob'])

    def test_unknown_or_missing_workflow_contract_rejects_both_layouts(self):
        for text in ('name: CI', '# verification-job-contract: unknown',
                     '# verification-job-contract: browser-projects-v1\n# verification-job-contract: browser-projects-v1'):
            for value in (jobs(), [j for j in jobs() if j['name'].removeprefix('checks / ') not in evidence.PROJECT_CHECKS]):
                with self.subTest(text=text), self.assertRaises(ValueError):
                    evidence.validate_jobs(value, 'f' * 40, lambda *args: text)

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
        if args in (
            ('rev-parse', f'HEAD:{evidence.BROWSER_REGRESSION_FILE}'),
            ('rev-parse', f'{evidence.HISTORICAL}:{evidence.BROWSER_REGRESSION_FILE}'),
        ):
            return evidence.BROWSER_REGRESSION_BLOB
        if args[:2] == ('diff', '--name-only'):
            return 'anteroom-ios/Sources/Foo.swift\0server/migrations/versions/new.py\0'
        if args[:2] == ('rev-list', '--first-parent'):
            return TARGET
        return 'c'*40

    @patch.dict(os.environ, {'GITHUB_SHA': TARGET, 'GITHUB_REPOSITORY': 'owner/repo',
                            'GITHUB_RUN_ID': '42', 'GITHUB_RUN_ATTEMPT': '2',
                            'GITHUB_EVENT_NAME': 'workflow_dispatch'})
    def test_preparation_pins_source_and_discloses_unverified_live_and_native(self):
        with patch.object(evidence, 'git', self.fake_git):
            result = evidence.prepare(TARGET, BASE)
        self.assertEqual(result['schema'], 2)
        self.assertEqual(result['browser_regression_blob'], evidence.BROWSER_REGRESSION_BLOB)
        self.assertFalse(result['deployment_authorized'])
        self.assertFalse(result['production_base_is_live_verified'])
        self.assertEqual(result['native_changes'], ['anteroom-ios/Sources/Foo.swift'])
        self.assertTrue(result['migration_changes'])

    @patch.dict(os.environ, {'GITHUB_SHA': TARGET, 'GITHUB_REPOSITORY': 'owner/repo',
                            'GITHUB_RUN_ID': '42', 'GITHUB_RUN_ATTEMPT': '2',
                            'GITHUB_EVENT_NAME': 'workflow_dispatch'})
    def test_preparation_rejects_modified_browser_regression_test(self):
        def modified_git(*args):
            if args == ('rev-parse', f'HEAD:{evidence.BROWSER_REGRESSION_FILE}'):
                return 'f' * 40
            return self.fake_git(*args)
        with patch.object(evidence, 'git', modified_git), self.assertRaisesRegex(ValueError, 'remain unchanged'):
            evidence.prepare(TARGET, BASE)

    @patch.dict(os.environ, {'GITHUB_SHA': TARGET, 'GITHUB_REPOSITORY': 'owner/repo',
                            'GITHUB_RUN_ID': '42', 'GITHUB_RUN_ATTEMPT': '2',
                            'GITHUB_EVENT_NAME': 'workflow_dispatch'})
    def test_impact_paths_preserve_unicode_whitespace_and_renamed_sources(self):
        with tempfile.TemporaryDirectory(prefix='verification-paths-') as directory:
            root = Path(directory)
            production_git = evidence.git
            empty_hooks = root / 'empty-hooks'
            empty_hooks.mkdir()
            def git(*args):
                # Filter at call time so injected overrides exercise isolation.
                git_env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
                with patch.dict(os.environ, git_env, clear=True):
                    return production_git('-c', f'core.hooksPath={empty_hooks}', '-C', str(root), *args)
            redirected = root / 'must-not-be-a-repository'
            with patch.dict(os.environ, {'GIT_DIR': str(redirected), 'GIT_WORK_TREE': str(redirected),
                                        'GIT_INDEX_FILE': str(redirected / 'index')}):
                git('init', '-q')
            self.assertTrue((root / '.git').is_dir())
            self.assertFalse(redirected.exists())
            rejecting_hooks = root / '.git/rejecting-hooks'
            rejecting_hooks.mkdir()
            hook = rejecting_hooks / 'pre-commit'
            hook.write_text('#!/bin/sh\nexit 1\n')
            hook.chmod(0o700)
            # Repository config outranks global config; the command override
            # must suppress even this stronger rejecting hook configuration.
            git('config', 'core.hooksPath', str(rejecting_hooks))
            source = root / 'anteroom-ios/Sources/Foo.swift'
            source.parent.mkdir(parents=True)
            source.write_text('synthetic native source')
            git('add', '.')
            git('-c', 'user.name=Verification', '-c', 'user.email=verification@example.invalid',
                '-c', 'commit.gpgsign=false', 'commit', '-qm', 'Base fixture')
            base = git('rev-parse', 'HEAD').strip()
            (root / 'archive').mkdir()
            source.rename(root / 'archive/Foo.swift')
            unusual = 'anteroom-ios/Sources/Café\nTail .swift '
            (root / unusual).write_text('another synthetic source')
            expected_native = {'anteroom-ios/Sources/Foo.swift', unusual}
            if sys.platform.startswith('linux'):
                raw_name = os.fsdecode(b'anteroom-ios/Sources/byte-\xff.swift')
                (root / raw_name).write_text('synthetic raw-byte filename')
                expected_native.add(raw_name)
            git('add', '.')
            git('-c', 'user.name=Verification', '-c', 'user.email=verification@example.invalid',
                '-c', 'commit.gpgsign=false', 'commit', '-qm', 'Rename and add unusual path')
            # Pin rename/quoting settings so the old bug is demonstrated on all hosts.
            git('config', 'diff.renames', 'true')
            git('config', 'core.quotePath', 'true')
            legacy = git('diff', '--name-only', f'{base}..HEAD').splitlines()
            self.assertNotIn(unusual, legacy)
            self.assertNotIn('anteroom-ios/Sources/Foo.swift', legacy)
            def mixed_git(*args):
                if args[0] == 'diff':
                    self.assertEqual(args[1:4], ('--name-only', '--no-renames', '-z'))
                    return git(*args[:-1], f'{base}..HEAD')
                return self.fake_git(*args)
            with patch.object(evidence, 'git', mixed_git):
                result = evidence.prepare(TARGET, BASE)
            self.assertEqual(set(result['native_changes']), expected_native)

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
        self.assertIn('name: verification-browser-${{ github.run_id }}-${{ github.run_attempt }}', text)
        self.assertIn('--browser browser-evidence/verification-browser.json', text)
        ci = (ROOT / '.github/workflows/ci.yml').read_text()
        self.assertIn('  workflow_call:', ci)
        self.assertIn('default: false', ci)
        self.assertIn('if [ "$FULL_VERIFICATION" = "true" ]; then base=""; fi', ci)
        self.assertEqual(ci.count('VERIFICATION_DATABASE:'), 2)
        for job in ('studio-browser', 'studio-backend', 'studio-backend-postgres'):
            block = ci.split('  ' + job + ':\n', 1)[1].split('    strategy:', 1)[0]
            self.assertNotIn('runner.temp', block)
        self.assertEqual(ci.count('if: success() && inputs.full_verification'), 3)
        self.assertIn('VERIFICATION_BROWSER_INVENTORY_PATH:', ci)
        self.assertIn('name: verification-browser-${{ github.run_id }}-${{ github.run_attempt }}', ci)
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

    def test_teardown_skip_replaces_pass_without_conflicting_outcomes(self):
        p = self.plugin
        p.pytest_runtest_logreport(SimpleNamespace(nodeid='one', when='call', passed=True, skipped=False))
        p.pytest_runtest_logreport(SimpleNamespace(nodeid='one', when='teardown', passed=False, skipped=True))
        self.assertFalse(p.state.passed)
        self.assertEqual(p.state.skipped, {evidence.digest('one')})

    def test_successful_teardown_keeps_call_pass(self):
        p = self.plugin
        p.pytest_runtest_logreport(SimpleNamespace(nodeid='one', when='call', passed=True, skipped=False))
        p.pytest_runtest_logreport(SimpleNamespace(nodeid='one', when='teardown', passed=True, skipped=False))
        self.assertEqual(p.state.passed, {evidence.digest('one')})
        self.assertFalse(p.state.skipped)

    def test_normal_worker_without_payload_invalidates_inventory(self):
        self.plugin.pytest_testnodedown(SimpleNamespace(workeroutput={}), None)
        self.assertTrue(self.plugin.state.invalid)
        self.assertFalse(self.plugin.state.workers)

    def test_worker_loss_prevents_inventory(self):
        p = self.plugin
        p.pytest_testnodedown(SimpleNamespace(), 'worker died')
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
