"""Verification evidence only. Nothing here authorizes or performs deployment."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess

SHA = re.compile(r'[0-9a-f]{40}')
HEX64 = re.compile(r'[0-9a-f]{64}')
# Active pair for new Descendant verification (`prepare`) and Deploy hosted
# eligibility. Archived receipts keep their own pair on EvidencePin
# (historical_failure / correcting_merge); do not reuse these constants when
# inspecting older pins such as 99c3b700 / afc3c2db, d19adbff / 6d72c5e3, or
# fa824f9a / 5cc7fea1.
# cc22db3f (#2257) failed Studio browser tests (sandbox) on phone-webkit
# e2e/chatStreaming.e2e.ts:158; 9c5df2f6 (#2291) fixed WebKit streaming pacing
# without changing the e2e test assertion.
HISTORICAL = 'cc22db3fae820d52c3e1b61af209e19221fa9502'
CORRECTED = '9c5df2f6d655f46313ba7218036deca1a75bf0c4'
REGRESSION = 'server/tests/test_hosted_assistant.py::test_shop_snapshot_survives_calculation_longer_than_the_idle_transaction_deadline'
BROWSER_REGRESSION_PROJECT = 'phone-webkit'
BROWSER_REGRESSION_FILE = 'anteroom/e2e/chatStreaming.e2e.ts'
BROWSER_REGRESSION_TITLE = 'a reply delivered in large chunks is revealed word by word'
BROWSER_REGRESSION_KEY = f'{BROWSER_REGRESSION_PROJECT}\0{BROWSER_REGRESSION_FILE}\0{BROWSER_REGRESSION_TITLE}'
BROWSER_REGRESSION_BLOB = '0f040c3572c8c5a9c1e024c70be72dc11fcbc5f9'
BROWSER_PROJECTS = ('desktop', 'phone', 'phone-webkit')
BROWSER_OUTCOMES = ('passed', 'failed', 'skipped', 'timedOut')
WORKFLOW = '.github/workflows/verify-corrected-descendant.yml'
# Names are the real expanded reusable CI jobs, not merely its aggregate result.
CHECKS = {'Changed components', 'Repo checks', 'Changelog', 'Studio frontend',
          'Studio code health', 'Studio browser tests (sandbox)', 'Studio browser tests (hosted)',
          'Studio browser project (desktop)', 'Studio browser project (phone)',
          'Studio browser project (phone-webkit)',
          'Studio browser execution (hosted)',
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
    browser_blob = require_sha(git('rev-parse', f'HEAD:{BROWSER_REGRESSION_FILE}'))
    historical_browser_blob = require_sha(git('rev-parse', f'{HISTORICAL}:{BROWSER_REGRESSION_FILE}'))
    if browser_blob != BROWSER_REGRESSION_BLOB or historical_browser_blob != BROWSER_REGRESSION_BLOB:
        raise ValueError('Browser regression test file must remain unchanged from the historical failure.')
    files = [p for p in git('diff', '--name-only', '--no-renames', '-z', f'{production}..{target}').split('\0') if p]
    return {'schema': 2, 'repository': os.environ['GITHUB_REPOSITORY'], 'target': target,
            'tree': git('rev-parse', 'HEAD^{tree}'), 'production_base': production,
            'production_base_is_live_verified': False,
            'historical_failure': HISTORICAL, 'correcting_merge': CORRECTED,
            'browser_regression_file': BROWSER_REGRESSION_FILE,
            'browser_regression_blob': browser_blob,
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
                    not isinstance(v, str) or HEX64.fullmatch(v) is None for v in values
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


def validate_browser_inventory(payload: dict, manifest: dict) -> dict:
    if not isinstance(payload, dict) or type(payload.get('schema')) is not int or payload['schema'] != 1:
        raise ValueError('Invalid sandbox browser inventory schema.')
    if any(payload.get(k) != manifest[k] for k in ('target', 'tree', 'ci_blob', 'run_id', 'attempt')):
        raise ValueError('Sandbox browser inventory target/tree/ci_blob/run/attempt mismatch.')
    if (payload.get('regression_file') != BROWSER_REGRESSION_FILE
            or payload.get('regression_file_blob') != manifest.get('browser_regression_blob')
            or payload.get('regression_file_blob') != BROWSER_REGRESSION_BLOB
            or payload.get('regression_key_sha256') != digest(BROWSER_REGRESSION_KEY)):
        raise ValueError('Sandbox browser regression binding mismatch.')
    regression_case_id = payload.get('regression_case_id')
    if not isinstance(regression_case_id, str) or HEX64.fullmatch(regression_case_id) is None:
        raise ValueError('Invalid sandbox browser regression case identifier.')
    full = payload.get('full')
    cases = payload.get('cases')
    if not isinstance(full, list) or not full or not isinstance(cases, list) or not cases:
        raise ValueError('Sandbox browser full and executed inventories must be non-empty lists.')

    full_map: dict[str, dict] = {}
    for row in full:
        if not isinstance(row, dict):
            raise ValueError('Malformed sandbox browser collection entry.')
        cid, file_id, project, expected = row.get('id'), row.get('file'), row.get('project'), row.get('expected')
        if (not isinstance(cid, str) or HEX64.fullmatch(cid) is None
                or not isinstance(file_id, str) or HEX64.fullmatch(file_id) is None
                or project not in BROWSER_PROJECTS or expected not in ('passed', 'failed', 'skipped')
                or cid in full_map):
            raise ValueError('Invalid or duplicate sandbox browser collection entry.')
        full_map[cid] = {'project': project, 'file': file_id, 'expected': expected}

    cases_map: dict[str, dict] = {}
    for row in cases:
        if not isinstance(row, dict):
            raise ValueError('Malformed sandbox browser executed entry.')
        cid, file_id, project = row.get('id'), row.get('file'), row.get('project')
        shard, expected, attempts = row.get('shard'), row.get('expected'), row.get('attempts')
        if (not isinstance(cid, str) or HEX64.fullmatch(cid) is None
                or not isinstance(file_id, str) or HEX64.fullmatch(file_id) is None
                or project not in BROWSER_PROJECTS or type(shard) is not int or shard not in (1, 2, 3)
                or expected not in ('passed', 'failed', 'skipped')
                or not isinstance(attempts, list) or not attempts or cid in cases_map):
            raise ValueError('Invalid or duplicate sandbox browser executed entry.')
        for idx, att in enumerate(attempts):
            if not isinstance(att, dict):
                raise ValueError('Malformed sandbox browser attempt entry.')
            status, duration, retry = att.get('status'), att.get('duration'), att.get('retry')
            if (status not in BROWSER_OUTCOMES
                    or isinstance(duration, bool) or not isinstance(duration, (int, float))
                    or not math.isfinite(duration) or duration < 0
                    or type(retry) is not int or retry != idx):
                raise ValueError('Invalid sandbox browser attempt record.')
        if attempts[-1]['status'] != expected:
            raise ValueError('Sandbox browser executed case terminal outcome does not match expected status.')
        cases_map[cid] = {'project': project, 'file': file_id, 'shard': shard,
                          'expected': expected, 'attempts': attempts}

    if (any(not any(v['project'] == p for v in full_map.values()) for p in BROWSER_PROJECTS)
            or set(full_map) != set(cases_map)):
        raise ValueError('Sandbox browser shard union does not match full collection.')
    for cid, executed in cases_map.items():
        original = full_map[cid]
        if (original['project'] != executed['project']
                or original['file'] != executed['file']
                or (original['expected'] != 'passed' and original['expected'] != executed['expected'])):
            raise ValueError('Sandbox browser executed case differs from collected metadata.')

    if regression_case_id not in cases_map:
        raise ValueError(
            'The historical phone-webkit chatStreaming regression must pass on first attempt without skip or retry.'
        )
    reg_full = full_map[regression_case_id]
    reg_exec = cases_map[regression_case_id]
    if (reg_full['project'] != BROWSER_REGRESSION_PROJECT
            or reg_full['expected'] != 'passed'
            or reg_exec['project'] != BROWSER_REGRESSION_PROJECT
            or reg_exec['expected'] != 'passed'
            or len(reg_exec['attempts']) != 1
            or reg_exec['attempts'][0]['status'] != 'passed'
            or reg_exec['attempts'][0]['retry'] != 0):
        raise ValueError(
            'The historical phone-webkit chatStreaming regression must pass on first attempt without skip or retry.'
        )

    projects_summary = {}
    for project in BROWSER_PROJECTS:
        rows = [c for c in cases_map.values() if c['project'] == project]
        projects_summary[project] = {
            'collected': len(rows),
            'shard_counts': [sum(1 for c in rows if c['shard'] == s)
                             for s in sorted({c['shard'] for c in cases_map.values()})],
            'passed': sum(1 for c in rows if c['expected'] == 'passed'),
            'skipped': sum(1 for c in rows if c['expected'] == 'skipped'),
            'expected_failures': sum(1 for c in rows if c['expected'] == 'failed'),
            'retried': sum(1 for c in rows if len(c['attempts']) > 1),
        }
    return {
        'projects': projects_summary,
        'regression': {
            'project': BROWSER_REGRESSION_PROJECT,
            'file': BROWSER_REGRESSION_FILE,
            'file_blob': manifest['browser_regression_blob'],
            'key_sha256': digest(BROWSER_REGRESSION_KEY),
            'shard': reg_exec['shard'],
            'attempts': len(reg_exec['attempts']),
            'passed': True,
        },
    }


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
    receipt.add_argument('--browser', type=Path, required=True)
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
        browser_payload = json.loads(args.browser.read_text())
        result['browser_inventory'] = validate_browser_inventory(browser_payload, result)
        result['scope'] = 'all Linux CI components; native validation remains separate'
        result['native_validation'] = 'not performed; separate native impact assessment and pinned receipts required'
        result['deployment_authorized'] = False
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
