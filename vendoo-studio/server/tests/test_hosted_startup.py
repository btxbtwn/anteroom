"""A fresh hosted process cannot rely on migrations/tests loading its models."""
import os
import subprocess
import sys
from pathlib import Path


def test_hosted_cold_start_registers_all_relationships_without_desktop_services(tmp_path):
    result = subprocess.run([sys.executable, "-c", """
import sys
from fastapi.testclient import TestClient
from sqlalchemy.orm import configure_mappers
from vendoo_studio.hosted import create_app
with TestClient(create_app('https://startup-test.supabase.co')) as client:
    configure_mappers()
    assert client.get('/api/health').status_code == 200
    assert client.get('/api/conversations').status_code == 401
    assert 'vendoo_studio.main' not in sys.modules
"""], env={**os.environ, 'VENDOO_STUDIO_DATA_DIR': str(tmp_path), 'ANTEROOM_HOSTED': '1',
             'PYTHONPATH': str(Path(__file__).resolve().parents[1])},
        capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
