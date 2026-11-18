"""Fail closed unless every selected CI check succeeds.

NEEDS_JSON is GitHub's complete needs context. A skipped check is acceptable
only when the successful selector explicitly marked that component false.
Keep this mapping and ci.needs in sync: missing or new jobs fail closed.
"""
from __future__ import annotations

import json
import os
import sys

REQUIRED = {'changes', 'changelog', 'repo-checks'}
COMPONENT_JOBS = {
    'studio-frontend': 'frontend',
    'studio-browser': 'browser',
    'studio-code-health': 'health',
    'dependency-audit': 'audit',
    'studio-backend': 'backend',
    'studio-backend-postgres': 'backend',
    'extension': 'extension',
    'webhook-relay': 'relay',
}


def validate(needs: object) -> list[str]:
    if not isinstance(needs, dict) or set(needs) != REQUIRED | set(COMPONENT_JOBS):
        return ['CI needs does not match the required-check contract.']
    if any(not isinstance(job, dict) for job in needs.values()):
        return ['Invalid CI job result.']
    errors = [f'{name} must succeed.' for name in sorted(REQUIRED) if needs[name].get('result') != 'success']
    outputs = needs['changes'].get('outputs')
    if not isinstance(outputs, dict) or any(outputs.get(group) not in ('true', 'false') for group in COMPONENT_JOBS.values()):
        return errors + ['Changed components must explicitly select every check group.']
    for name, group in COMPONENT_JOBS.items():
        result = needs[name].get('result')
        if result == 'success':
            continue
        if result == 'skipped' and outputs[group] == 'false':
            continue
        errors.append(f'{name} did not succeed (selected={outputs[group]}, result={result}).')
    return errors


def main() -> int:
    try:
        payload = json.loads(os.environ.get('NEEDS_JSON', ''))
    except json.JSONDecodeError:
        print('Invalid or missing CI needs JSON.', file=sys.stderr)
        return 1
    errors = validate(payload)
    for error in errors:
        print(error, file=sys.stderr)
    if not errors:
        print('Every required check succeeded or explicitly skipped an unchanged component.')
    return int(bool(errors))


if __name__ == '__main__':
    sys.exit(main())
