"""Exercise a packaged Mac app on throwaway data, refusing live actions."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx


PORT = '4321'


def sandbox_env(directory: str, base: dict[str, str] | None = None) -> dict[str, str]:
    """Sandbox mode, a throwaway data folder and a spare port, for old and new builds alike.

    The audit runs this against the installed app, which lags ``main``. Builds
    from before the rename (0.1.1053 and earlier) read only ``VENDOO_STUDIO_DATA_DIR``
    and ``VENDOO_STUDIO_PORT``; without them such a build opens the seller's real
    data folder and port 4318. Remove the two old names once every installed
    release is 0.1.1054 or later (no later than 2026-12-31).
    """
    paths = {'DATA_DIR': directory, 'PORT': PORT}
    env = dict(os.environ if base is None else base, ANTEROOM_SANDBOX='1')
    for prefix in ('ANTEROOM', 'VENDOO_STUDIO'):
        env.update({f'{prefix}_{name}': value for name, value in paths.items()})
    return env


def main():
    executable = Path(sys.argv[1]).resolve() / 'Contents' / 'MacOS' / 'Anteroom'
    with tempfile.TemporaryDirectory(prefix='anteroom-package-smoke-') as directory:
        env = sandbox_env(directory)
        with (Path(directory) / 'app.log').open('w') as log:
            process = subprocess.Popen([str(executable)], env=env, stdout=log, stderr=log)
            try:
                with httpx.Client(base_url=f'http://127.0.0.1:{PORT}', timeout=2) as client:
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
