"""Check an immutable descendant receipt. Read-only; deployment authority is disabled."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import io
import json
import os
import re
import selectors
import subprocess
import time
import zipfile

import verification_evidence as evidence

MAX_ARCHIVE = 4 * 1024 * 1024
MAX_JSON = 4 * 1024 * 1024
DIGEST = re.compile(r'sha256:[0-9a-f]{64}')


class RecoveryDisabled(ValueError):
    pass


def require_runtime_authority(_authority=None):
    """No authenticated single-use control exists yet. Never return a deploy permit."""
    raise RecoveryDisabled('Recovery runtime disabled: authenticated single-use authorization and '
                           'in-attempt live image/schema reconciliation are not implemented.')


@dataclass(frozen=True)
class EvidencePin:
    repository: str
    repository_id: int
    workflow_id: int
    target: str
    tree: str
    production_base: str
    # Pair that produced this receipt; archived pins keep their own, independent
    # of verification_evidence.HISTORICAL / CORRECTED for the next recovery.
    historical_failure: str
    correcting_merge: str
    workflow_blob: str
    ci_blob: str
    run_id: int
    attempt: int
    receipt_artifact_id: int
    receipt_digest: str

    def __post_init__(self):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*', self.repository):
            raise ValueError('Expected an explicit owner/repository.')
        for name in ('repository_id', 'workflow_id', 'run_id', 'attempt', 'receipt_artifact_id'):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError('Repository, workflow, run, attempt and artifact IDs must be positive integers.')
        for name in (
            'target', 'tree', 'production_base', 'historical_failure', 'correcting_merge',
            'workflow_blob', 'ci_blob',
        ):
            evidence.require_sha(getattr(self, name))
        if not DIGEST.fullmatch(self.receipt_digest):
            raise ValueError('An explicit SHA-256 receipt archive digest is required.')


def canonical(value):
    """Compare JSON types strictly: True and 1 must not compare as equal."""
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def expected_source(pin, git):
    """Read the pinned target's Git objects, without switching checkout to main/target."""
    main = git('rev-list', '--first-parent', 'origin/main').splitlines()
    if (pin.target not in main or pin.production_base not in main
            or main.index(pin.target) >= main.index(pin.production_base)):
        raise ValueError('Target must follow the exact base on main first-parent history.')
    for ancestor in (pin.historical_failure, pin.correcting_merge):
        git('merge-base', '--is-ancestor', ancestor, pin.target)
    if (git('rev-parse', pin.target+'^{tree}') != pin.tree
            or git('rev-parse', pin.target+':'+evidence.WORKFLOW) != pin.workflow_blob
            or git('rev-parse', pin.target+':.github/workflows/ci.yml') != pin.ci_blob):
        raise ValueError('Pinned target tree or reviewed workflow/CI blobs do not match Git.')
    span = git('rev-list', '--first-parent', '--reverse', pin.production_base+'..'+pin.target).splitlines()
    files = [p for p in git('diff', '--name-only', '--no-renames', '-z',
                            pin.production_base+'..'+pin.target).split('\0') if p]
    return {'schema': 1, 'repository': pin.repository, 'target': pin.target, 'tree': pin.tree,
            'production_base': pin.production_base, 'production_base_is_live_verified': False,
            'historical_failure': pin.historical_failure, 'correcting_merge': pin.correcting_merge,
            'span': span,
            'native_changes': [p for p in files if p.startswith(('anteroom-ios/', 'contracts/'))
                               or p == 'anteroom/server/anteroom/desktop.py'],
            'migration_changes': [p for p in files if '/migrations/' in p],
            'workflow': evidence.WORKFLOW, 'workflow_blob': pin.workflow_blob, 'ci_blob': pin.ci_blob,
            'run_id': str(pin.run_id), 'attempt': str(pin.attempt), 'event': 'workflow_dispatch',
            'deployment_authorized': False}


def validate_run(run, pin):
    expected = {'id': pin.run_id, 'workflow_id': pin.workflow_id, 'head_sha': pin.target,
                'head_branch': 'main', 'run_attempt': pin.attempt, 'event': 'workflow_dispatch',
                'path': evidence.WORKFLOW, 'status': 'completed', 'conclusion': 'success'}
    if (any(canonical(run.get(k)) != canonical(v) for k, v in expected.items())
            or run.get('repository', {}).get('id') != pin.repository_id
            or run.get('repository', {}).get('full_name') != pin.repository
            or run.get('head_repository', {}).get('id') != pin.repository_id):
        raise ValueError('Verification run provenance or final success mismatch.')


def all_artifacts(fetch, run_id):
    artifacts, expected = [], None
    for page in range(1, 21):
        data = fetch(f'actions/runs/{run_id}/artifacts?per_page=100&page={page}')
        total, values = data.get('total_count'), data.get('artifacts')
        if (type(total) is not int or total < 1 or not isinstance(values, list)
                or (expected is not None and expected != total)):
            raise ValueError('Invalid or inconsistent artifact pagination.')
        expected = total
        artifacts.extend(values)
        ids = [a.get('id') for a in artifacts]
        if (any(type(i) is not int or i < 1 for i in ids)
                or len(set(ids)) != len(ids) or len(ids) > total):
            raise ValueError('Duplicate, excess or invalid artifact IDs.')
        if len(ids) == total:
            return artifacts
        if len(values) != 100:
            raise ValueError('Truncated artifact pagination.')
    raise ValueError('Artifact page limit exceeded.')


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate JSON keys.')
        value[key] = item
    return value


def read_artifact(meta, filename, pin, download):
    expected = {'id': pin.run_id, 'repository_id': pin.repository_id,
                'head_repository_id': pin.repository_id, 'head_sha': pin.target, 'head_branch': 'main'}
    wr = meta.get('workflow_run', {})
    size = meta.get('size_in_bytes')
    digest = meta.get('digest')
    if (meta.get('expired') is not False
            or type(size) is not int or not 0 < size <= MAX_ARCHIVE
            or not isinstance(digest, str) or not DIGEST.fullmatch(digest)
            or any(canonical(wr.get(k)) != canonical(v) for k, v in expected.items())):
        raise ValueError('Expired, oversized or mismatched artifact provenance.')
    raw = download(meta['id'])
    if (not isinstance(raw, bytes) or len(raw) != size or len(raw) > MAX_ARCHIVE
            or 'sha256:'+hashlib.sha256(raw).hexdigest() != digest):
        raise ValueError('Artifact bytes do not match the immutable archive digest.')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if (len(entries) != 1 or entries[0].filename != filename
                    or entries[0].flag_bits & 1 or entries[0].is_dir()
                    or entries[0].compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                    or entries[0].file_size > MAX_JSON):
                raise ValueError('Unexpected or oversized artifact archive members.')
            payload = archive.read(entries[0])
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError) as error:
        raise ValueError('Invalid artifact archive.') from error
    value = json.loads(payload, object_pairs_hook=unique_object,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-finite JSON.')))
    if not isinstance(value, dict):
        raise ValueError('Artifact JSON must be an object.')
    return value


def verify(pin, fetch, download, git):
    """Validate authenticated read-only API observations and Git objects; authorize nothing itself.

    Callbacks are trusted transport adapters, never dispatch-supplied JSON. The CLI
    supplies bounded GET-only gh calls. Callers (such as wait-for-tested-code.py)
    are responsible for live base checks, ancestry/CI gating, and serialization.
    """
    validate_run(fetch(f'actions/runs/{pin.run_id}'), pin)
    validate_run(fetch(f'actions/runs/{pin.run_id}/attempts/{pin.attempt}'), pin)
    source = expected_source(pin, git)
    jobs = evidence.all_jobs(fetch, str(pin.run_id), str(pin.attempt))
    ids = [j.get('id') for j in jobs]
    if (any(type(i) is not int or i < 1 for i in ids) or len(set(ids)) != len(ids)
            or any(type(j.get('run_id')) is not int or j['run_id'] != pin.run_id for j in jobs)
            or any(j.get('status') != 'completed' or j.get('conclusion') != 'success' for j in jobs)
            or sorted(j['name'] for j in jobs if not j['name'].startswith('checks / ')) != ['evidence', 'prepare']):
        raise ValueError('Final job inventory is incomplete or contains untrusted jobs.')
    checks = evidence.validate_jobs(jobs)
    suffix = f'{pin.run_id}-{pin.attempt}'
    names = {f'verification-receipt-{suffix}': 'verification-receipt.json',
             f'verification-source-{suffix}': 'verification-source.json'}
    for db in ('sqlite', 'postgres'):
        for group in (1, 2):
            names[f'verification-inventory-{db}-{group}-{suffix}'] = f'inventory-{db}-{group}.json'
    selected = [a for a in all_artifacts(fetch, pin.run_id) if a.get('name', '').startswith(
        ('verification-receipt-', 'verification-source-', 'verification-inventory-'))]
    if len(selected) != len(names) or {a.get('name') for a in selected} != set(names):
        raise ValueError('Exactly one receipt/source and all four exact-attempt inventory artifacts are required.')
    receipt_meta = next(a for a in selected if a['name'] == f'verification-receipt-{suffix}')
    if receipt_meta['id'] != pin.receipt_artifact_id or receipt_meta.get('digest') != pin.receipt_digest:
        raise ValueError('Unique pinned receipt artifact ID/digest mismatch.')
    payloads = {a['name']: read_artifact(a, names[a['name']], pin, download) for a in selected}
    if canonical(payloads[f'verification-source-{suffix}']) != canonical(source):
        raise ValueError('Source artifact does not match independently reconstructed target/base Git objects.')
    inventories = [payloads[name] for name in names if name.startswith('verification-inventory-')]
    summary = evidence.validate_inventory(inventories, source)
    receipt = dict(payloads[f'verification-receipt-{suffix}'])
    reported_jobs = receipt.pop('jobs', [])
    if (not isinstance(reported_jobs, list)
            or canonical(sorted(reported_jobs, key=lambda j: j['id'])) != canonical(sorted(checks, key=lambda j: j['id']))):
        raise ValueError('Receipt job identities/results do not match the GitHub attempt.')
    expected = {**source, 'backend_inventory': summary,
                'scope': 'all Linux CI components; native validation remains separate',
                'native_validation': 'not performed; separate native impact assessment and pinned receipts required'}
    if canonical(receipt) != canonical(expected):
        raise ValueError('Receipt target/base, inventories or verification scope mismatch.')
    # A newer attempt started while archives were downloaded must invalidate this check.
    validate_run(fetch(f'actions/runs/{pin.run_id}'), pin)
    return {**asdict(pin), 'backend_inventory': summary,
            'native_changes': source['native_changes'], 'migration_changes': source['migration_changes'],
            'deployment_authorized': False, 'runtime_authority': 'disabled',
            'native_validation': 'not performed', 'live_reconciliation': 'not performed'}


def read_command(command, limit=MAX_ARCHIVE):
    """Bound both time and output without logging process stderr or credentials."""
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    output = bytearray()
    deadline = time.monotonic()+30
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline-time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise ValueError('Read-only command timed out.')
                block = os.read(process.stdout.fileno(), 65536)
                if not block:
                    break
                output.extend(block)
                if len(output) > limit:
                    raise ValueError('Read-only command output exceeded its bound.')
        if process.wait(timeout=max(0.01, deadline-time.monotonic())):
            raise ValueError('Read-only command failed; no fallback or retry.')
        return bytes(output)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in EvidencePin.__dataclass_fields__:
        parser.add_argument('--'+field.replace('_', '-'), required=True,
                            type=int if field.endswith('_id') or field == 'attempt' else str)
    pin = EvidencePin(**vars(parser.parse_args()))
    def fetch(path):
        return json.loads(read_command(['gh', 'api', '--method', 'GET', f'repos/{pin.repository}/{path}']))
    def download(artifact_id):
        return read_command(['gh', 'api', '--method', 'GET',
                             f'repos/{pin.repository}/actions/artifacts/{artifact_id}/zip'])
    def git(*args):
        return read_command(['git', *args]).decode('utf-8', errors='surrogateescape').rstrip('\n')
    print(json.dumps(verify(pin, fetch, download, git), indent=2))


if __name__ == '__main__':
    main()
