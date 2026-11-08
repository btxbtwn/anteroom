"""The Linux CI tool download keeps its pinned checksum when retrying."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import textwrap

import pytest

WORKFLOW = Path(__file__).resolve().parents[3] / ".github/workflows/ci.yml"


@pytest.mark.parametrize("download_fails", [False, True])
def test_gitleaks_download_has_bounded_retries_and_never_extracts_unverified_bytes(tmp_path, download_fails):
    if not shutil.which("sha256sum"):
        pytest.skip("The Linux repo-check workflow uses sha256sum.")
    step = WORKFLOW.read_text().split("      - name: Install gitleaks\n", 1)[1].split("      - name:", 1)[0]
    version_match = re.search(r'GITLEAKS_VERSION: "([^"]+)"', step)
    asset_match = re.search(r'GITLEAKS_ASSET_ID: "([0-9]+)"', step)
    checksum_match = re.search(r"GITLEAKS_SHA256: ([a-f0-9]+)", step)
    assert version_match and checksum_match and asset_match
    version, checksum = version_match.group(1), checksum_match.group(1)
    assert version == "8.30.1"
    asset = asset_match.group(1)
    assert asset == "378332058"
    assert checksum == "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb"
    script = textwrap.dedent(step.split("        run: |\n", 1)[1])
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text(f"#!{sys.executable}\n" + r'''
import json, os, pathlib, sys
pathlib.Path(os.environ['RUNNER_TEMP'], 'curl.json').write_text(json.dumps(sys.argv[1:]))
if os.environ['FAIL_DOWNLOAD'] == '1':
    sys.exit(56)
pathlib.Path(sys.argv[sys.argv.index('-o') + 1]).write_bytes(b'unverified archive')
''')
    curl.chmod(0o755)
    tar = bin_dir / "tar"
    tar.write_text('#!/bin/sh\ntouch "$RUNNER_TEMP/extracted"\n')
    tar.chmod(0o755)
    result = subprocess.run(["bash", "-e", "-c", script], env={
        **os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "RUNNER_TEMP": str(tmp_path),
        "GITLEAKS_VERSION": version, "GITLEAKS_ASSET_ID": asset, "GITLEAKS_SHA256": checksum, "FAIL_DOWNLOAD": str(int(download_fails)),
    }, capture_output=True, text=True, timeout=10)
    args = json.loads((tmp_path / "curl.json").read_text())
    assert "--retry-all-errors" in args
    for flag, value in (("--retry", "3"), ("--connect-timeout", "15"), ("--max-time", "120"), ("--retry-max-time", "600")):
        assert args[args.index(flag) + 1] == value
    assert args[args.index("-H") + 1] == "Accept: application/octet-stream"
    assert args[-1] == f"https://api.github.com/repos/gitleaks/gitleaks/releases/assets/{asset}"
    assert result.returncode == (56 if download_fails else 1)
    if not download_fails:
        assert "FAILED" in result.stdout  # The real checksum tool rejected the stubbed archive.
    assert not (tmp_path / "extracted").exists()
