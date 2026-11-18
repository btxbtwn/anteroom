"""Opt-in pytest/xdist collector for verification-only shard completeness evidence."""
import json
import os
from pathlib import Path

import pytest

from verification_evidence import digest, git


class Inventory:
    def __init__(self):
        self.expected = None
        self.selected = None
        self.passed = set()
        self.skipped = set()
        self.invalid = False
        self.workers = []


state = Inventory()


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_collection_modifyitems(items):
    # Capture the full collection BEFORE pytest-split removes the other shard.
    state.expected = sorted(digest(item.nodeid) for item in items)
    yield
    state.selected = sorted(digest(item.nodeid) for item in items)


def pytest_runtest_logreport(report):
    if hasattr(report, 'context'):
        return
    key = digest(report.nodeid)
    # Subtest reports share a node ID with their parent; the parent still must
    # finish. Failed subtests cause a nonzero session exit and no artifact.
    if report.when == 'call' and report.passed:
        state.passed.add(key)
    elif report.skipped:
        # A fixture can skip during teardown after the test body passed.
        state.passed.discard(key)
        state.skipped.add(key)


def snapshot():
    return {'expected': state.expected, 'selected': state.selected,
            'passed': sorted(state.passed), 'skipped': sorted(state.skipped)}


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node, error):
    # A crashed worker never sends workerfinished and may have no workeroutput.
    if error:
        state.invalid = True
        return
    payload = getattr(node, 'workeroutput', {}).get('verification_inventory')
    if payload is None:
        state.invalid = True
    else:
        state.workers.append(payload)


def pytest_sessionfinish(session, exitstatus):
    if hasattr(session.config, 'workerinput'):
        session.config.workeroutput['verification_inventory'] = snapshot()
        return
    if exitstatus or state.invalid:
        return
    if state.workers:
        if any((w['expected'], w['selected']) != (state.workers[0]['expected'], state.workers[0]['selected'])
               for w in state.workers):
            raise RuntimeError('Workers disagree about collection/shard selection.')
        payload = {key: state.workers[0][key] for key in ('expected', 'selected')}
        for key in ('passed', 'skipped'):
            payload[key] = sorted(set().union(*(set(w[key]) for w in state.workers)))
    else:
        payload = snapshot()
    payload.update(database=os.environ['VERIFICATION_DATABASE'], group=int(os.environ['VERIFICATION_GROUP']),
                   target=git('rev-parse', 'HEAD'), tree=git('rev-parse', 'HEAD^{tree}'),
                   run_id=os.environ['GITHUB_RUN_ID'], attempt=os.environ['GITHUB_RUN_ATTEMPT'])
    path = Path(os.environ['VERIFICATION_INVENTORY_PATH'])
    if path.exists():
        raise RuntimeError('Refusing to reuse a previous inventory.')
    path.write_text(json.dumps(payload) + '\n')
