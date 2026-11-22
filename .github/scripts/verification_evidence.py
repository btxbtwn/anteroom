"""Verification evidence only. Nothing here authorizes or performs deployment."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

SHA = re.compile(r'[0-9a-f]{40}')
# d19adbff (#1717) left two Alembic heads; 6d72c5e3 (#1728) joined them.
# Push CI on d19adbff remains failure; Deploy hosted may supersede it only via
# a pinned Descendant verification receipt (see wait-for-tested-code.py).
HISTORICAL = 'd19adbff5a34609dd5c2b7ada19966ae4aeafce8'
CORRECTED = '6d72c5e3d9d1ee4256f7c754c749a2e19a01818c'
REGRESSION = 'server/tests/test_hosted_assistant.py::test_shop_snapshot_survives_calculation_longer_than_the_idle_transaction_deadline'
WORKFLOW = '.github/workflows/verify-corrected-descendant.yml'
# Names are the real expanded reusable CI jobs, not merely its aggregate result.
CHECKS = {'Changed components', 'Repo checks', 'Changelog', 'Studio frontend',
          'Studio code health', 'Studio browser tests (sandbox)', 'Studio browser tests (hosted)',
          'Studio backend (1/2)', 'Studio backend (2/2)',
          'Studio backend Postgres (1/2)', 'Studio backend Postgres (2/2)',
          'Extension', 'CI', 'Audit locked Python dependencies', 'Webhook relay'}


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def git(*args: str) -> str:
    return subprocess.run(['git', *args], check=True, capture_output=True, timeout=15).stdout.decode('utf-8', errors='surrogateescape').rstrip('\n')


def require_sha(value: str) -> str:
    if not isinstance(value, str) or SHA.fullmatch(value) is None:
        raise ValueError('Expected a full lowercase commit SHA.')
    return value


def prepare(target: str, production: str) -> dict:
    require_sha(target)
    require_sha(production)
    if os.environ['GITHUB_EVENT_NAME'] != 'workflow_dispatch':
        raise ValueError('Only the isolated manual verification event is supported.')
    if git('rev-parse', 'HEAD') != target or os.environ['GITHUB_SHA'] != target:
        raise ValueError('Input, workflow event and checked-out target must match.')
    main = git('rev-list', '--first-parent', 'origin/main').splitlines()
    if target not in main or production not in main or main.index(target) >= main.index(production):
        raise ValueError('Production base must precede target on main first-parent history.')
    for ancestor in (HISTORICAL, CORRECTED):
        git('merge-base', '--is-ancestor', ancestor, target)
    files = [p for p in git('diff', '--name-only', '--no-renames', '-z', f'{production}..{target}').split('\0') if p]
    return {'schema': 1, 'repository': os.environ['GITHUB_REPOSITORY'], 'target': target,
            'tree': git('rev-parse', 'HEAD^{tree}'), 'production_base': production,
            'production_base_is_live_verified': False,
            'historical_failure': HISTORICAL, 'correcting_merge': CORRECTED,
            'span': git('rev-list', '--first-parent', '--reverse', f'{production}..{target}').splitlines(),
            'native_changes': [p for p in files if p.startswith(('anteroom-ios/', 'contracts/'))
                               or p == 'anteroom/server/anteroom/desktop.py'],
            'migration_changes': [p for p in files if '/migrations/' in p],
            'workflow': WORKFLOW,
            'workflow_blob': git('rev-parse', f'HEAD:{WORKFLOW}'),
            'ci_blob': git('rev-parse', 'HEAD:.github/workflows/ci.yml'),
            'run_id': os.environ['GITHUB_RUN_ID'], 'attempt': os.environ['GITHUB_RUN_ATTEMPT'],
            'event': os.environ['GITHUB_EVENT_NAME'], 'deployment_authorized': False}


def all_jobs(fetch, run_id: str, attempt: str) -> list[dict]:
    """Read this exact attempt; fail on truncated, repeated or inconsistent pages."""
    if not run_id.isdecimal() or not attempt.isdecimal():
        raise ValueError('Run and attempt must be numeric.')
    jobs = []
    expected = None
    for page in range(1, 21):
        data = fetch(f'actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100&page={page}')
        if not isinstance(data, dict) or not isinstance(data.get('jobs'), list):
            raise ValueError('Invalid jobs response.')
        total = data.get('total_count')
        if type(total) is not int or total < 1 or (expected is not None and total != expected):
            raise ValueError('Inconsistent job inventory.')
        expected = total
        jobs.extend(data['jobs'])
        if len({j['id'] for j in jobs}) != len(jobs) or len(jobs) > expected:
            raise ValueError('Repeated or excess jobs.')
        if len(jobs) == expected:
            return jobs
        if len(data['jobs']) != 100:
            raise ValueError('Truncated jobs page.')
    raise ValueError('Job inventory exceeds the bounded page limit.')


def validate_jobs(jobs: list[dict]) -> list[dict]:
    checks = [j for j in jobs if j['name'].startswith('checks / ')]
    names = [j['name'].removeprefix('checks / ').removeprefix('Dependency audit / ') for j in checks]
    if set(names) != CHECKS or len(names) != len(CHECKS):
        raise ValueError('Expanded jobs do not match the complete verification contract.')
    if any(j.get('status') != 'completed' or j.get('conclusion') != 'success' for j in checks):
        raise ValueError('Every verification job must complete successfully; skips are not evidence.')
    return [{'id': j['id'], 'name': j['name'], 'conclusion': j['conclusion']} for j in checks]


def validate_inventory(payloads: list[dict], manifest: dict) -> dict:
    expected_slots = {(db, group) for db in ('sqlite', 'postgres') for group in (1, 2)}
    if any(type(p.get('group')) is not int for p in payloads):
        raise ValueError('Shard groups must be integers.')
    slots = [(p['database'], p['group']) for p in payloads]
    if len(slots) != 4 or set(slots) != expected_slots:
        raise ValueError('Both shards of each database are required exactly once.')
    summary = {}
    for database in ('sqlite', 'postgres'):
        shards = [p for p in payloads if p['database'] == database]
        for p in shards:
            if any(p.get(k) != manifest[k] for k in ('target', 'run_id', 'attempt', 'tree')):
                raise ValueError('Inventory target/tree/run/attempt mismatch.')
            for key in ('expected', 'selected', 'passed', 'skipped'):
                values = p.get(key)
                if not isinstance(values, list) or len(set(values)) != len(values) or any(
                    not isinstance(v, str) or re.fullmatch('[0-9a-f]{64}', v) is None for v in values
                ):
                    raise ValueError('Malformed or duplicate inventory identifiers.')
            selected, passed, skipped = (set(p[k]) for k in ('selected', 'passed', 'skipped'))
            if not selected or passed & skipped or passed | skipped != selected:
                raise ValueError('Selected tests must have a terminal passing or skipped outcome.')
        left, right = (set(p['selected']) for p in shards)
        expected = set(shards[0]['expected'])
        if not expected or expected != set(shards[1]['expected']) or left & right or left | right != expected:
            raise ValueError('Shard union must equal independent full collection, without overlap.')
        passed = set().union(*(set(p['passed']) for p in shards))
        if database == 'postgres' and digest(REGRESSION) not in passed:
            raise ValueError('The historical Postgres regression must actually pass, not skip.')
        summary[database] = {'collected': len(expected), 'passed': len(passed),
                             'skipped': len(expected - passed), 'inventory_sha256': digest('\n'.join(sorted(expected)))}
    return summary


def validate_run(run: dict, manifest: dict) -> None:
    if (run.get('head_sha') != manifest['target'] or run.get('event') != 'workflow_dispatch'
            or str(run.get('run_attempt')) != manifest['attempt'] or run.get('path') != WORKFLOW
            or run.get('repository', {}).get('full_name') != manifest['repository']):
        raise ValueError('Run provenance mismatch.')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    prep = sub.add_parser('prepare')
    prep.add_argument('--target', required=True)
    prep.add_argument('--production-base', required=True)
    receipt = sub.add_parser('receipt')
    receipt.add_argument('--evidence', type=Path, required=True)
    receipt.add_argument('--inventories', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'prepare':
        result = prepare(args.target, args.production_base)
    else:
        result = json.loads(args.evidence.read_text())
        if result != prepare(result['target'], result['production_base']):
            raise ValueError('Manifest no longer matches this workflow execution.')
        repo = result['repository']
        def fetch(path):
            return json.loads(subprocess.run(['gh', 'api', f'repos/{repo}/{path}'], check=True,
                                            capture_output=True, text=True, timeout=30).stdout)
        run = fetch(f"actions/runs/{result['run_id']}")
        validate_run(run, result)
        result['jobs'] = validate_jobs(all_jobs(fetch, result['run_id'], result['attempt']))
        payloads = [json.loads(p.read_text()) for p in sorted(args.inventories.glob('*.json'))]
        result['backend_inventory'] = validate_inventory(payloads, result)
        result['scope'] = 'all Linux CI components; native validation remains separate'
        result['native_validation'] = 'not performed; separate native impact assessment and pinned receipts required'
        result['deployment_authorized'] = False
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
