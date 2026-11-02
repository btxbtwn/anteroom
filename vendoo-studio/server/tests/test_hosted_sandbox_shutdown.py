import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest


@pytest.mark.parametrize('phase', ['startup', 'running', 'cleanup'])
def test_termination_cleans_private_database_before_exiting(tmp_path, phase):
    marker = tmp_path / 'cleaned'
    root_marker = tmp_path / 'sandbox-root'
    script = '''
import asyncio
import sys
import time
from pathlib import Path
import pgserver.postgres_server as postgres_server
from fastapi import FastAPI
from vendoo_studio import database, hosted, sandbox_seed
from vendoo_studio.services import category_tree_seed

def wait_for_release():
    deadline = time.monotonic() + 60
    while not Path(sys.argv[6]).exists():
        if time.monotonic() > deadline:
            raise RuntimeError('Test did not release the resource lifecycle')
        time.sleep(.01)

class PrivatePostgres:
    def __init__(self, path, **kwargs):
        Path(sys.argv[3]).write_text(str(Path(path).parent))
        if sys.argv[4] == 'startup':
            wait_for_release()
    def get_uri(self):
        return 'sqlite://'
    def cleanup(self):
        Path(sys.argv[5]).touch()
        if sys.argv[4] == 'cleanup':
            wait_for_release()
        Path(sys.argv[2]).write_text('cleaned')

postgres_server.get_server = PrivatePostgres
database.init_db = lambda: None
category_tree_seed.import_seed = lambda engine: None
hosted.create_app = lambda *args, **kwargs: FastAPI()
sandbox_seed.seed_hosted = lambda url: None
from vendoo_studio.hosted_sandbox import serve
asyncio.run(serve(int(sys.argv[1])))
'''
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    env = dict(os.environ, ANTEROOM_SANDBOX='1', ANTEROOM_DATABASE_URL=f'sqlite:///{tmp_path / "bootstrap.db"}',
               VENDOO_STUDIO_DATA_DIR=str(tmp_path / 'data'))
    env.pop('ANTEROOM_TEST_DATABASE_URL', None)
    cleanup_started = tmp_path / 'cleanup-started'
    release = tmp_path / 'release'
    with (tmp_path / 'server.log').open('w+') as log:
        process = subprocess.Popen([sys.executable, '-c', script, str(port), str(marker), str(root_marker),
                                    phase, str(cleanup_started), str(release)],
                                   env=env, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                assert process.poll() is None, (tmp_path / 'server.log').read_text()
                if phase == 'startup':
                    if root_marker.exists():
                        break
                    time.sleep(.05)
                    continue
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health', timeout=1) as response:
                        if json.load(response)['sandbox']:
                            break
                except (urllib.error.URLError, TimeoutError):
                    time.sleep(.05)
            else:
                raise AssertionError('Sandbox did not start')
            assert root_marker.exists()
            process.terminate()
            if phase == 'startup':
                release.touch()
            if phase == 'cleanup':
                deadline = time.monotonic() + 15
                while not cleanup_started.exists() and time.monotonic() < deadline:
                    time.sleep(.05)
                assert cleanup_started.exists()
                process.terminate()
                release.touch()
            process.wait(timeout=15)
            assert marker.exists(), 'Termination skipped private Postgres cleanup'
            assert process.returncode == 0
            assert not Path(root_marker.read_text()).exists()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            if root_marker.exists():
                root = Path(root_marker.read_text())
                if root.exists():
                    assert root.name.startswith('anteroom-hosted-sandbox-')
                    shutil.rmtree(root)
