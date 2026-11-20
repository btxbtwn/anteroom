"""Exercise check.sh's actual private-PG wrapper without starting a database."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys

import pytest

CHECK = Path(__file__).resolve().parents[3] / "check.sh"


@pytest.mark.parametrize(
    "outcome,expected", [("0", 0), ("7", 7), ("TERM", 143), ("INT", -signal.SIGINT)]
)
def test_private_postgres_cleanup_precedes_directory_removal(
    tmp_path, outcome, expected
):
    # Execute the production heredoc, replacing only PG and pytest with tiny
    # process doubles. Cleanup records whether its containing directory exists.
    code = CHECK.read_text().split("<<'PY'\n", 1)[1].split('\nPY"', 1)[0]
    package = tmp_path / "pgserver"
    package.mkdir()
    (package / "__init__.py").touch()
    (package / "postgres_server.py").write_text("""
import atexit, json, os
from pathlib import Path
class Server:
    def __init__(self, path):
        self.path = path
        path.mkdir()
        self.cleaned = False
        Path(os.environ['ROOT_RECORD']).write_text(str(path.parent))
        atexit.register(self.cleanup)
    def get_uri(self):
        return 'postgresql://unused-local-double'
    def cleanup(self):
        if not self.cleaned:
            Path(os.environ['CLEANUP_RECORD']).write_text(json.dumps(self.path.parent.exists()))
            self.cleaned = True
            atexit.unregister(self.cleanup)
def get_server(path, cleanup_mode):
    assert cleanup_mode == 'delete'
    return Server(path)
""")
    (tmp_path / "pytest.py").write_text("""
import os, signal, time
from pathlib import Path
Path(os.environ['CHILD_RECORD']).write_text(str(os.getpid()))
outcome = os.environ['OUTCOME']
if outcome in ('TERM', 'INT'):
    os.kill(os.getppid(), getattr(signal, 'SIG' + outcome))
    time.sleep(10)
else:
    raise SystemExit(int(outcome))
""")
    cleanup = tmp_path / "cleanup.json"
    root = tmp_path / "root"
    child = tmp_path / "child"
    result = subprocess.run(
        [sys.executable, "-c", code],
        env={
            **os.environ,
            "PYTHONPATH": str(tmp_path),
            "OUTCOME": outcome,
            "CLEANUP_RECORD": str(cleanup),
            "ROOT_RECORD": str(root),
            "CHILD_RECORD": str(child),
        },
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == expected, result.stderr
    assert cleanup.exists(), result.stderr
    assert json.loads(cleanup.read_text()) is True, (
        "server stopped after its directory was removed"
    )
    assert not Path(root.read_text()).exists()
    with pytest.raises(ProcessLookupError):
        os.kill(int(child.read_text()), 0)
