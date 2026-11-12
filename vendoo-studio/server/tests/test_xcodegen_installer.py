"""The CI installer retries transport failures but never skips integrity checks."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

INSTALLER = Path(__file__).resolve().parents[3] / "anteroom-ios/tools/install-xcodegen.sh"
SHA256 = "4d9e34b62172d645eed6457cac13fc222569974098ef4ee9c3368bedf0196806"


def test_cloud_setup_handles_failures_and_release_settings():
    subprocess.run([sys.executable, str(INSTALLER.parent / "test_cloud_setup.py")], check=True)


def install(tmp_path, *, fail_download=False, fail_checksum=False, github_token: str | None = None):
    """Stub tools, not the installer: no network, unpacked code or system changes."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.jsonl"
    for tool in ("curl", "shasum", "unzip"):
        script = bin_dir / tool
        script.write_text(f"#!{sys.executable}\n" + r'''
import json, os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
body = sys.stdin.read() if name == 'shasum' else None
with open(os.environ['INSTALLER_TEST_LOG'], 'a') as log:
    log.write(json.dumps({'tool': name, 'args': args, 'body': body}) + '\n')
if name == 'curl':
    if os.environ['FAIL_DOWNLOAD'] == '1':
        sys.exit(56)
    pathlib.Path(args[args.index('-o') + 1]).write_text('test archive')
elif name == 'shasum':
    if os.environ['FAIL_CHECKSUM'] == '1':
        sys.exit(1)
elif name == 'unzip':
    binary = pathlib.Path(args[args.index('-d') + 1]) / 'xcodegen/bin/xcodegen'
    binary.parent.mkdir(parents=True)
    binary.write_text('#!/bin/sh\nprintf "version checked\\n"\n')
    binary.chmod(0o755)
''')
        script.chmod(0o755)
    destination = tmp_path / "download"
    path_file = tmp_path / "github-path"
    env = {
        **os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "INSTALLER_TEST_LOG": str(log), "GITHUB_PATH": str(path_file),
        "FAIL_DOWNLOAD": str(int(fail_download)), "FAIL_CHECKSUM": str(int(fail_checksum)),
    }
    env.pop("GITHUB_TOKEN", None)
    if github_token is not None:
        env["GITHUB_TOKEN"] = github_token
    result = subprocess.run(["bash", str(INSTALLER), str(destination)], env=env,
                            capture_output=True, text=True, timeout=10)
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    return result, calls, destination, path_file


def _headers(args: list[str]) -> list[str]:
    return [args[i + 1] for i, flag in enumerate(args) if flag == "-H"]


def test_xcodegen_download_retries_transport_errors_with_deadlines(tmp_path):
    result, calls, destination, path_file = install(tmp_path)
    assert result.returncode == 0, result.stderr
    assert [call["tool"] for call in calls] == ["curl", "shasum", "unzip"]
    args = calls[0]["args"]
    assert "--retry-all-errors" in args  # curl exit 56 is not retried by --retry alone.
    for flag, value in (("--retry", "3"), ("--connect-timeout", "15"), ("--max-time", "120"), ("--retry-max-time", "600")):
        assert args[args.index(flag) + 1] == value
    assert _headers(args) == ["Accept: application/octet-stream"]
    assert args[-1] == "https://api.github.com/repos/yonaskolb/XcodeGen/releases/assets/478866069"
    assert calls[1]["args"] == ["-a", "256", "-c", "-"]
    assert calls[1]["body"] == f"{SHA256}  {destination}/xcodegen.zip\n"
    assert "version checked" in result.stdout
    assert path_file.read_text() == f"{destination}/xcodegen/bin\n"


def test_xcodegen_download_uses_job_token_when_present(tmp_path):
    result, calls, _, _ = install(tmp_path, github_token="test-token")
    assert result.returncode == 0, result.stderr
    assert _headers(calls[0]["args"]) == [
        "Accept: application/octet-stream",
        "Authorization: Bearer test-token",
    ]


@pytest.mark.parametrize("failure,expected", [("download", ["curl"]), ("checksum", ["curl", "shasum"])])
def test_xcodegen_installer_never_unpacks_or_adds_failed_download_to_path(tmp_path, failure, expected):
    result, calls, destination, path_file = install(tmp_path, fail_download=failure == "download", fail_checksum=failure == "checksum")
    assert result.returncode != 0
    assert [call["tool"] for call in calls] == expected
    assert not (destination / "xcodegen/bin/xcodegen").exists()
    assert not path_file.exists()
