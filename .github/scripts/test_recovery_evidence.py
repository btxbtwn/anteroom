"""Offline recovery-consumer rejection tests; no network, application or database."""
import copy
import hashlib
import io
import json
import unittest
from dataclasses import replace
import zipfile

import recovery_evidence as recovery
import verification_evidence as verification


def archive(name, value):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr(name, json.dumps(value))
    return output.getvalue()


class RecoveryEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.pin = recovery.EvidencePin(
            repository='example/project', repository_id=10, workflow_id=20,
            target='a'*40, tree='b'*40, production_base='c'*40,
            workflow_blob='d'*40, ci_blob='e'*40, run_id=42, attempt=1,
            receipt_artifact_id=100, receipt_digest='sha256:'+'0'*64)
        self.run = {'id': 42, 'workflow_id': 20, 'head_sha': self.pin.target,
                    'head_branch': 'main', 'repository': {'id': 10, 'full_name': 'example/project'},
                    'head_repository': {'id': 10}, 'run_attempt': 1,
                    'event': 'workflow_dispatch', 'path': verification.WORKFLOW,
                    'status': 'completed', 'conclusion': 'success'}
        self.git_values = {
            ('rev-list', '--first-parent', 'origin/main'): '\n'.join(['f'*40, self.pin.target, verification.CORRECTED, verification.HISTORICAL, self.pin.production_base]),
            ('rev-parse', self.pin.target+'^{tree}'): self.pin.tree,
            ('rev-parse', self.pin.target+':'+verification.WORKFLOW): self.pin.workflow_blob,
            ('rev-parse', self.pin.target+':.github/workflows/ci.yml'): self.pin.ci_blob,
            ('rev-list', '--first-parent', '--reverse', self.pin.production_base+'..'+self.pin.target): '\n'.join([verification.HISTORICAL, verification.CORRECTED, self.pin.target]),
            ('diff', '--name-only', '--no-renames', '-z', self.pin.production_base+'..'+self.pin.target): 'anteroom-ios/example.swift\0server/migrations/new.py\0',
        }
        self.source = {
            'schema': 1, 'repository': self.pin.repository, 'target': self.pin.target,
            'tree': self.pin.tree, 'production_base': self.pin.production_base,
            'production_base_is_live_verified': False, 'historical_failure': verification.HISTORICAL,
            'correcting_merge': verification.CORRECTED,
            'span': [verification.HISTORICAL, verification.CORRECTED, self.pin.target],
            'native_changes': ['anteroom-ios/example.swift'],
            'migration_changes': ['server/migrations/new.py'], 'workflow': verification.WORKFLOW,
            'workflow_blob': self.pin.workflow_blob, 'ci_blob': self.pin.ci_blob,
            'run_id': '42', 'attempt': '1', 'event': 'workflow_dispatch', 'deployment_authorized': False}
        self.jobs = [{'id': i, 'run_id': 42, 'name': name,
                      'status': 'completed', 'conclusion': 'success'} for i, name in enumerate(
                          ['prepare', *['checks / '+n for n in sorted(verification.CHECKS)], 'evidence'], 1)]
        ids = [verification.digest(verification.REGRESSION), verification.digest('another-test')]
        self.inventories = [{**{k: self.source[k] for k in ['target', 'tree', 'run_id', 'attempt']},
                             'database': db, 'group': group, 'expected': ids,
                             'selected': [ids[group-1]], 'passed': [ids[group-1]], 'skipped': []}
                            for db in ['sqlite', 'postgres'] for group in [1, 2]]
        self.receipt = {**self.source, 'jobs': verification.validate_jobs(self.jobs),
                        'backend_inventory': verification.validate_inventory(self.inventories, self.source),
                        'scope': 'all Linux CI components; native validation remains separate',
                        'native_validation': 'not performed; separate native impact assessment and pinned receipts required'}
        self.artifacts = []
        self.downloads = {}
        self.add_artifact(100, 'verification-receipt-42-1', 'verification-receipt.json', self.receipt)
        self.add_artifact(101, 'verification-source-42-1', 'verification-source.json', self.source)
        for i, p in enumerate(self.inventories, 102):
            self.add_artifact(i, f"verification-inventory-{p['database']}-{p['group']}-42-1",
                              f"inventory-{p['database']}-{p['group']}.json", p)
        self.pin = replace(self.pin, receipt_digest=self.artifacts[0]['digest'])
        self.calls = []

    def add_artifact(self, ident, name, filename, payload):
        data = archive(filename, payload)
        self.downloads[ident] = data
        self.artifacts.append({'id': ident, 'name': name, 'expired': False, 'size_in_bytes': len(data),
                               'digest': 'sha256:'+hashlib.sha256(data).hexdigest(),
                               'workflow_run': {'id': 42, 'repository_id': 10, 'head_repository_id': 10,
                                                'head_sha': self.pin.target, 'head_branch': 'main'}})

    def git(self, *args):
        if args[0] == 'merge-base':
            return ''
        return self.git_values[args]

    def fetch(self, path):
        self.calls.append(path)
        if path in ['actions/runs/42', 'actions/runs/42/attempts/1']:
            return copy.deepcopy(self.run)
        if path.startswith('actions/runs/42/attempts/1/jobs?'):
            return {'total_count': len(self.jobs), 'jobs': copy.deepcopy(self.jobs)}
        if path.startswith('actions/runs/42/artifacts?'):
            return {'total_count': len(self.artifacts), 'artifacts': copy.deepcopy(self.artifacts)}
        raise AssertionError('Unexpected API access: '+path)

    def check(self, **kwargs):
        return recovery.verify(self.pin, kwargs.get('fetch', self.fetch),
                               kwargs.get('download', self.downloads.__getitem__), self.git)

    def rewrite(self, index, filename, payload):
        a = self.artifacts[index]
        data = archive(filename, payload)
        self.downloads[a['id']] = data
        a['size_in_bytes'] = len(data)
        a['digest'] = 'sha256:'+hashlib.sha256(data).hexdigest()
        if index == 0:
            self.pin = replace(self.pin, receipt_digest=a['digest'])

    def test_complete_pinned_evidence_is_read_only_and_keeps_native_gaps(self):
        result = self.check()
        self.assertFalse(result['deployment_authorized'])
        self.assertEqual(result['target'], self.pin.target)
        self.assertEqual(result['backend_inventory']['postgres']['passed'], 2)
        self.assertEqual(result['live_reconciliation'], 'not performed')
        self.assertEqual(result['native_changes'], ['anteroom-ios/example.swift'])
        self.assertTrue(all(p.startswith('actions/runs/42') for p in self.calls))

    def test_all_run_provenance_bindings_are_required(self):
        for key, bad in [('id', 43), ('workflow_id', 21), ('head_sha', 'f'*40),
                         ('head_branch', 'feature'), ('run_attempt', 2), ('event', 'push'),
                         ('path', '.github/workflows/ci.yml'), ('status', 'in_progress'),
                         ('conclusion', 'failure'), ('repository', {'id': 11, 'full_name': self.pin.repository}),
                         ('head_repository', {'id': 11})]:
            original = self.run[key]
            self.run[key] = bad
            with self.subTest(key=key), self.assertRaises(ValueError): self.check()
            self.run[key] = original

    def test_missing_skipped_cancelled_failed_and_extra_jobs_reject(self):
        for state in ['skipped', 'cancelled', 'failure', None]:
            self.jobs[2]['conclusion'] = state
            with self.subTest(state=state), self.assertRaises(ValueError): self.check()
        self.jobs[2]['conclusion'] = 'success'
        self.jobs.pop()
        with self.assertRaises(ValueError): self.check()

    def test_duplicate_ids_wrong_run_and_extra_job_reject(self):
        for mutation in ['duplicate', 'wrongrun', 'extra']:
            original = copy.deepcopy(self.jobs)
            if mutation == 'duplicate': self.jobs[1]['id'] = self.jobs[0]['id']
            if mutation == 'wrongrun': self.jobs[0]['run_id'] = 43
            if mutation == 'extra': self.jobs.append({**self.jobs[0], 'id': 999, 'name': 'untrusted'})
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): self.check()
            self.jobs = original

    def test_jobs_pagination_truncation_is_rejected(self):
        def fetch(path):
            result = self.fetch(path)
            if '/jobs?' in path: result['total_count'] += 1
            return result
        with self.assertRaises(ValueError): self.check(fetch=fetch)

    def test_artifact_pagination_truncation_is_rejected(self):
        def fetch(path):
            result = self.fetch(path)
            if '/artifacts?' in path: result['total_count'] += 1
            return result
        with self.assertRaises(ValueError): self.check(fetch=fetch)

    def test_receipt_id_and_digest_do_not_select_a_nearby_artifact(self):
        for pin in [replace(self.pin, receipt_artifact_id=101),
                    replace(self.pin, receipt_digest='sha256:'+'f'*64)]:
            with self.assertRaises(ValueError): recovery.verify(pin, self.fetch, self.downloads.__getitem__, self.git)

    def test_expired_wrong_run_repository_or_target_artifact_rejects(self):
        for key, value in [('expired', True), ('workflow_run', {'id': 43})]:
            original = self.artifacts[0][key]
            self.artifacts[0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): self.check()
            self.artifacts[0][key] = original

    def test_missing_duplicate_and_wrong_attempt_artifacts_reject(self):
        original = copy.deepcopy(self.artifacts)
        for items in [original[:-1], original+[original[1]],
                      [{**a, 'name': a['name'].replace('-42-1', '-42-2')} for a in original]]:
            self.artifacts = items
            with self.assertRaises(ValueError): self.check()
        self.artifacts = original

    def test_download_bytes_must_match_metadata_digest(self):
        self.downloads[100] += b'tampered'
        with self.assertRaises(ValueError): self.check()

    def test_zip_paths_extra_members_duplicate_json_and_oversize_reject(self):
        examples = [archive('../verification-receipt.json', {}), b'not a zip']
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w') as z:
            z.writestr('verification-receipt.json', '{"schema":1,"schema":2}')
        examples.append(out.getvalue())
        for raw in examples:
            self.downloads[100] = raw
            self.artifacts[0]['size_in_bytes'] = len(raw)
            self.artifacts[0]['digest'] = 'sha256:'+hashlib.sha256(raw).hexdigest()
            self.pin = replace(self.pin, receipt_digest=self.artifacts[0]['digest'])
            with self.assertRaises(ValueError): self.check()
        self.artifacts[0]['size_in_bytes'] = recovery.MAX_ARCHIVE+1
        with self.assertRaises(ValueError): self.check()

    def test_receipt_cannot_claim_different_tree_base_span_or_authorization(self):
        for key, bad in [('tree', 'f'*40), ('production_base', 'f'*40), ('span', []),
                         ('deployment_authorized', True), ('schema', True)]:
            value = {**self.receipt, key: bad}
            self.rewrite(0, 'verification-receipt.json', value)
            with self.subTest(key=key), self.assertRaises(ValueError): self.check()

    def test_source_reconstructed_from_git_not_user_json(self):
        self.rewrite(1, 'verification-source.json', {**self.source, 'migration_changes': []})
        with self.assertRaises(ValueError): self.check()

    def test_wrong_tree_or_unreviewed_workflow_and_ci_blobs_reject(self):
        for key in [('rev-parse', self.pin.target+'^{tree}'),
                    ('rev-parse', self.pin.target+':'+verification.WORKFLOW),
                    ('rev-parse', self.pin.target+':.github/workflows/ci.yml')]:
            old = self.git_values[key]
            self.git_values[key] = 'f'*40
            with self.subTest(key=key), self.assertRaises(ValueError): self.check()
            self.git_values[key] = old

    def test_docs_descendant_cannot_reuse_original_target_receipt(self):
        pin = replace(self.pin, target='f'*40)
        with self.assertRaises(ValueError): recovery.verify(pin, self.fetch, self.downloads.__getitem__, self.git)

    def test_historical_regression_cannot_be_skipped(self):
        p = self.inventories[2]
        p['skipped'], p['passed'] = p['passed'], []
        self.rewrite(4, 'inventory-postgres-1.json', p)
        with self.assertRaises(ValueError): self.check()

    def test_inventory_summary_and_job_ids_must_match_receipt(self):
        for field, value in [('backend_inventory', {}), ('jobs', [])]:
            self.rewrite(0, 'verification-receipt.json', {**self.receipt, field: value})
            with self.subTest(field=field), self.assertRaises(ValueError): self.check()

    def test_latest_attempt_is_checked_again_after_downloads(self):
        count = 0
        def fetch(path):
            nonlocal count
            result = self.fetch(path)
            if path == 'actions/runs/42':
                count += 1
                if count > 1: result['run_attempt'] = 2
            return result
        with self.assertRaises(ValueError): self.check(fetch=fetch)

    def test_no_default_comment_dispatch_or_boolean_can_enable_runtime(self):
        for authority in [None, True, {'approved': True}, {'comment': 'approved'}, {'actor': 'owner'}, self.check()]:
            with self.subTest(authority=authority), self.assertRaises(recovery.RecoveryDisabled):
                recovery.require_runtime_authority(authority)

    def test_pin_rejects_bool_ids_short_shas_missing_digest_and_unsafe_repo(self):
        for field, bad in [('run_id', True), ('attempt', 0), ('repository_id', -1),
                           ('target', 'abc'), ('tree', 'main'), ('repository', '../repo'),
                           ('receipt_digest', 'latest')]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                replace(self.pin, **{field: bad})

    def test_missing_or_reversed_first_parent_base_rejects(self):
        key = ('rev-list', '--first-parent', 'origin/main')
        for value in [self.pin.target, self.pin.production_base+'\n'+self.pin.target]:
            self.git_values[key] = value
            with self.assertRaises(ValueError): self.check()

    def test_missing_correcting_ancestry_rejects(self):
        def git(*args):
            if args[0] == 'merge-base': raise ValueError('not ancestor')
            return self.git(*args)
        with self.assertRaises(ValueError):
            recovery.verify(self.pin, self.fetch, self.downloads.__getitem__, git)

    def test_api_records_with_wrong_repository_name_reject(self):
        self.run['repository']['full_name'] = 'example/other'
        with self.assertRaises(ValueError): self.check()

    def test_missing_artifact_digest_rejects(self):
        self.artifacts[1].pop('digest')
        with self.assertRaises(ValueError): self.check()

    def test_malformed_extra_member_and_zip_bomb_header_reject(self):
        for kind in ['extra', 'oversize']:
            out = io.BytesIO()
            with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
                z.writestr('verification-receipt.json', json.dumps(self.receipt)
                           if kind == 'extra' else ' '* (recovery.MAX_JSON+1))
                if kind == 'extra': z.writestr('unexpected.json', '{}')
            raw = out.getvalue()
            self.downloads[100] = raw
            self.artifacts[0]['size_in_bytes'] = len(raw)
            self.artifacts[0]['digest'] = 'sha256:'+hashlib.sha256(raw).hexdigest()
            self.pin = replace(self.pin, receipt_digest=self.artifacts[0]['digest'])
            with self.subTest(kind=kind), self.assertRaises(ValueError): self.check()

    def test_api_failure_has_no_fallback(self):
        def fail(path): raise RuntimeError('offline')
        with self.assertRaises(RuntimeError): self.check(fetch=fail)


if __name__ == '__main__':
    unittest.main()
