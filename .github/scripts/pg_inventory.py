"""Observe pytest-split collection; never select, reorder or schedule tests."""
import hashlib
import json
import os
import platform
import subprocess
from importlib.metadata import version
from pathlib import Path

import pytest
from pg_inventory_contract import validate_workers


def pytest_configure(config):
    config.pluginmanager.register(Inventory(config), 'pg-collection-observer')


class Inventory:
    def __init__(self, config):
        self.config = config
        self.record = None
        self.workers = []
        self.worker_failed = False
        root = config.rootpath
        commit = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'], check=True,
                                capture_output=True, text=True).stdout.strip()
        self.provenance = {'commit': commit, 'lock_sha256': hashlib.sha256((root / 'uv.lock').read_bytes()).hexdigest(),
                           'run_id': os.environ['GITHUB_RUN_ID'], 'attempt': os.environ['GITHUB_RUN_ATTEMPT'],
                           'python': platform.python_version(),
                           'worker_count': str(getattr(config, 'workerinput', {}).get('workercount', len(config.option.tx))),
                           **{name: version(name) for name in ('pytest', 'pytest-xdist', 'pytest-split')}}

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_collection_modifyitems(self, items):
        full = [hashlib.sha256(item.nodeid.encode()).hexdigest() for item in items]
        result = yield
        self.record = {'provenance': self.provenance, 'full': full,
                       'selected': [hashlib.sha256(item.nodeid.encode()).hexdigest() for item in items]}
        return result

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodedown(self, node, error):
        self.worker_failed |= bool(error) or node.workeroutput.get('exitstatus') != 0
        self.workers.append(node.workeroutput.get('pg_inventory'))

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session, exitstatus):
        if hasattr(self.config, 'workerinput'):
            self.config.workeroutput['pg_inventory'] = self.record
            return
        if exitstatus != 0:
            return
        try:
            if self.worker_failed or not self.workers or len(self.workers) != len(self.config.option.tx):
                raise ValueError('Missing or failed workers')
            record = validate_workers(self.workers)
            with Path(os.environ['PG_INVENTORY_PATH']).open('x') as target:
                json.dump(record, target, sort_keys=True)
                target.write('\n')
        except (OSError, ValueError, TypeError, KeyError):
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
            reporter = self.config.pluginmanager.get_plugin('terminalreporter')
            reporter.write_line('Postgres collection inventory validation failed; no ready inventory.')
