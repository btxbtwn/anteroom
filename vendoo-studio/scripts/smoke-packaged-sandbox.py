"""Exercise a packaged Mac app on throwaway data, refusing live actions."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx


def main():
    executable = Path(sys.argv[1]).resolve() / 'Contents' / 'MacOS' / 'Anteroom'
    with tempfile.TemporaryDirectory(prefix='anteroom-package-smoke-') as directory:
        env = dict(os.environ, ANTEROOM_SANDBOX='1', VENDOO_STUDIO_DATA_DIR=directory, VENDOO_STUDIO_PORT='4321')
        with (Path(directory) / 'app.log').open('w') as log:
            process = subprocess.Popen([str(executable)], env=env, stdout=log, stderr=log)
            try:
                with httpx.Client(base_url='http://127.0.0.1:4321', timeout=2) as client:
                    deadline = time.monotonic() + 90
                    while time.monotonic() < deadline:
                        if process.poll() is not None:
                            raise RuntimeError('The packaged sandbox exited before startup')
                        try:
                            if client.get('/api/health').is_success:
                                break
                        except httpx.HTTPError:
                            pass
                        time.sleep(.2)
                    else:
                        raise RuntimeError('The packaged sandbox did not become healthy')
                    assert client.get('/').status_code == 200
                    assert client.get('/api/status').json()['sandbox'] is True
                    for action in ('list', 'delist'):
                        result = client.post(f'/api/conversations/probe/vendoo-api/{action}',
                                             json={'marketplaces': ['ebay'], 'confirm': True})
                        assert result.status_code == 403, result.status_code
                    print('Packaged sandbox serves the UI and refuses publishing/delisting')
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == '__main__':
    main()
