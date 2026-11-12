"""Package Anteroom Bridge and submit a new version to the Chrome Web Store.

    chrome-web-store.py package --out bridge.zip
    chrome-web-store.py release --package bridge.zip

``package`` zips the tracked files of vendoo-extension/ that Chrome loads (the
same files the Mac app copies, chrome_bridge.EXTENSION_SKIP) and checks that
everything the manifest and background.js load is inside.

``release`` uploads that zip and submits it for review through the Chrome Web
Store API (v2), but only when its manifest version is newer than every version
the store has published or is reviewing. A submission still in review for an
older version is cancelled first, since the store takes one at a time. It
signs in as a service account added to the publisher's Account page:

    CHROME_WEB_STORE_SERVICE_ACCOUNT  the service account's JSON key
    CHROME_WEB_STORE_PUBLISHER_ID     the publisher ID from the developer dashboard

Standard library plus the openssl command, which signs the sign-in token.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXTENSION = "vendoo-extension"
ITEM_ID = "empinkdlhndpfoigoipbiiofaeepcheo"
API = "https://chromewebstore.googleapis.com"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/chromewebstore"
# Top-level names Chrome never loads; kept equal to chrome_bridge.EXTENSION_SKIP by a test.
SKIP = frozenset({"AGENTS.md", ".git", ".gitignore", ".playwright-mcp", "README.md", "__pycache__",
                  "node_modules", "sample-listing.json", "studio-build.js", "tests", "tools"})
# Written by the Mac app beside its own copy; background.js loads it only if present.
OPTIONAL = frozenset({"studio-build.js"})
TIMEOUT_S = 120
UPLOAD_WAIT_S = 300
# PublishItemResponse states that confirm review, staging, or publication.
SUBMITTED_STATES = frozenset({"PENDING_REVIEW", "STAGED", "PUBLISHED", "PUBLISHED_TO_TESTERS"})


class StoreError(RuntimeError):
    pass


def tracked_files(root: Path = ROOT) -> list[str]:
    """The extension's files git tracks, relative to vendoo-extension/, minus what Chrome never loads."""
    listed = subprocess.run(["git", "ls-files", "-z", "--", EXTENSION], cwd=root, check=True,
                            capture_output=True).stdout.decode().split("\0")
    files = []
    for path in filter(None, listed):
        relative = path.removeprefix(EXTENSION + "/")
        if not any(part in SKIP for part in relative.split("/")):
            files.append(relative)
    return sorted(files)


def referenced_files(source: Path) -> set[str]:
    """Every file the manifest and background.js name, which Chrome needs in the package."""
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    needed = {"manifest.json", *manifest.get("icons", {}).values()}
    worker = manifest.get("background", {}).get("service_worker")
    if worker:
        needed.add(worker)
    for script in manifest.get("content_scripts", []):
        needed.update(script.get("js", []), script.get("css", []))
    if worker:
        text = (source / worker).read_text(encoding="utf-8")
        needed.update(re.findall(r"importScripts\(\s*'([^']+)'\s*\)", text))
    return needed - OPTIONAL


def package(out: Path, root: Path = ROOT) -> str:
    """Write the store zip to ``out`` and return its manifest version."""
    source = root / EXTENSION
    files = tracked_files(root)
    missing = sorted(referenced_files(source) - set(files))
    if missing:
        raise StoreError(f"The package would lack files the Bridge loads: {', '.join(missing)}")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for relative in files:
            # A fixed date, so the same code always makes the same zip.
            info = zipfile.ZipInfo(relative, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, (source / relative).read_bytes())
    return manifest_version(source / "manifest.json")


def manifest_version(path: Path) -> str:
    return str(json.loads(path.read_text(encoding="utf-8"))["version"])


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def plan(version: str, status: dict) -> str:
    """What to do with ``version`` given the store's fetchStatus: skip, cancel_and_submit or submit."""
    submitted = status.get("submittedItemRevisionStatus") or {}
    known = [channel.get("crxVersion") for revision in (status.get("publishedItemRevisionStatus"), submitted)
             for channel in (revision or {}).get("distributionChannels") or [] if channel.get("crxVersion")]
    if any(version_key(other) >= version_key(version) for other in known):
        return "skip"
    return "cancel_and_submit" if submitted.get("state") == "PENDING_REVIEW" else "submit"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def access_token(key_json: str) -> str:
    """An OAuth access token for the service account, by the JWT bearer grant."""
    key = json.loads(key_json)
    now = int(time.time())
    head = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    body = _b64(json.dumps({"iss": key["client_email"], "scope": SCOPE, "aud": TOKEN_URL,
                            "iat": now, "exp": now + 600}).encode())
    with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=True) as pem:
        os.chmod(pem.name, 0o600)
        pem.write(key["private_key"])
        pem.flush()
        signature = subprocess.run(["openssl", "dgst", "-sha256", "-sign", pem.name],
                                   input=f"{head}.{body}".encode(), check=True, capture_output=True).stdout
    form = urllib.parse.urlencode({"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                   "assertion": f"{head}.{body}.{_b64(signature)}"}).encode()
    return _call("POST", TOKEN_URL, data=form, headers={"Content-Type": "application/x-www-form-urlencoded"})[
        "access_token"]


def _call(method: str, url: str, *, token: str | None = None, data: bytes | None = None,
          headers: dict | None = None) -> dict:
    request = urllib.request.Request(url, data=data, method=method, headers={
        **({"Authorization": f"Bearer {token}"} if token else {}), **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            text = response.read().decode()
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")[:2000]
        raise StoreError(f"{method} {url.split('?')[0]} failed with {error.code}: {detail}") from None
    return json.loads(text) if text.strip() else {}


def release(zip_path: Path, version: str, *, token: str, publisher: str) -> str:
    item = f"publishers/{publisher}/items/{ITEM_ID}"
    status = _call("GET", f"{API}/v2/{item}:fetchStatus", token=token)
    action = plan(version, status)
    print(f"Store status: {json.dumps(status)}")
    if action == "skip":
        print(f"The store already has Anteroom Bridge {version} or newer; nothing to submit.")
        return "skip"
    if action == "cancel_and_submit":
        print("Cancelling the older submission still in review, so this version replaces it.")
        _call("POST", f"{API}/v2/{item}:cancelSubmission", token=token, data=b"{}",
              headers={"Content-Type": "application/json"})
    upload = _call("POST", f"{API}/upload/v2/{item}:upload", token=token, data=zip_path.read_bytes(),
                   headers={"Content-Type": "application/zip"})
    state = upload.get("uploadState")
    deadline = time.monotonic() + UPLOAD_WAIT_S
    while state == "IN_PROGRESS" and time.monotonic() < deadline:
        time.sleep(10)
        state = _call("GET", f"{API}/v2/{item}:fetchStatus", token=token).get("lastAsyncUploadState")
    if state != "SUCCEEDED":
        raise StoreError(f"The store did not accept the package (upload state {state}): {json.dumps(upload)}")
    published = _call("POST", f"{API}/v2/{item}:publish", token=token, data=b"{}",
                      headers={"Content-Type": "application/json"})
    state = published.get("state")
    if state not in SUBMITTED_STATES:
        raise StoreError(f"The store did not confirm submission (state {state}): {json.dumps(published)}")
    print(f"Submitted Anteroom Bridge {version}: {json.dumps(published)}")
    return state


def verify_ci(repo: str, sha: str) -> None:
    """Require a successful push CI run for this exact commit on main."""
    subprocess.run(["git", "merge-base", "--is-ancestor", sha, "origin/main"], check=True)
    result = subprocess.run(
        ["gh", "api", f"repos/{repo}/actions/workflows/ci.yml/runs",
         "--method", "GET", "-f", f"head_sha={sha}", "-f", "branch=main",
         "-f", "event=push", "-f", "per_page=100"],
        check=True, capture_output=True, text=True,
    )
    runs = json.loads(result.stdout).get("workflow_runs", [])
    matching = [run for run in runs if run.get("head_sha") == sha
                and run.get("head_branch") == "main" and run.get("event") == "push"
                and run.get("path") == ".github/workflows/ci.yml"]
    latest = max(matching, key=lambda run: run["id"], default={})
    if latest.get("status") != "completed" or latest.get("conclusion") != "success":
        raise StoreError(f"No successful completed main push CI for exact commit {sha}.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("package").add_argument("--out", type=Path, required=True)
    commands.add_parser("release").add_argument("--package", type=Path, required=True)
    gate = commands.add_parser("verify-ci")
    gate.add_argument("--repo", required=True)
    gate.add_argument("--sha", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "verify-ci":
            verify_ci(args.repo, args.sha)
            return 0
        if args.command == "package":
            print(package(args.out))
            return 0
        with zipfile.ZipFile(args.package) as archive:
            version = str(json.loads(archive.read("manifest.json"))["version"])
        key, publisher = (os.environ.get("CHROME_WEB_STORE_SERVICE_ACCOUNT", ""),
                          os.environ.get("CHROME_WEB_STORE_PUBLISHER_ID", "").strip())
        if not key or not publisher:
            raise StoreError("Set CHROME_WEB_STORE_SERVICE_ACCOUNT and CHROME_WEB_STORE_PUBLISHER_ID.")
        release(args.package, version, token=access_token(key), publisher=publisher)
        return 0
    except StoreError as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
