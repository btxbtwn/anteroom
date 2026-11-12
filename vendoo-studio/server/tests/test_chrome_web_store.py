""".github/scripts/chrome-web-store.py: what goes into the store package and when a version is submitted."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import zipfile
from pathlib import Path

import pytest

from vendoo_studio.services.chrome_bridge import EXTENSION_SKIP

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("chrome_web_store", ROOT / ".github" / "scripts" / "chrome-web-store.py")
assert SPEC and SPEC.loader
store = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(store)


def status(published: str | None = None, submitted: str | None = None, state: str = "PENDING_REVIEW") -> dict:
    result: dict = {}
    if published:
        result["publishedItemRevisionStatus"] = {"state": "PUBLISHED", "distributionChannels": [{"crxVersion": published}]}
    if submitted:
        result["submittedItemRevisionStatus"] = {"state": state, "distributionChannels": [{"crxVersion": submitted}]}
    return result


def test_store_package_skips_what_the_mac_app_skips():
    assert store.SKIP == EXTENSION_SKIP


def test_package_holds_every_file_the_bridge_loads_and_no_developer_files(tmp_path):
    out = tmp_path / "bridge.zip"
    version = store.package(out)
    manifest = json.loads((ROOT / "vendoo-extension" / "manifest.json").read_text(encoding="utf-8"))
    assert version == manifest["version"]
    names = set(zipfile.ZipFile(out).namelist())
    assert store.referenced_files(ROOT / "vendoo-extension") <= names
    assert "content-scripts/cloud-connection.js" in names
    assert not any(name.split("/")[0] in EXTENSION_SKIP for name in names)
    assert json.loads(zipfile.ZipFile(out).read("manifest.json"))["version"] == version


def test_package_refuses_when_a_loaded_file_is_not_tracked(tmp_path):
    repo = tmp_path / "repo"
    source = repo / "vendoo-extension"
    source.mkdir(parents=True)
    (source / "manifest.json").write_text(json.dumps({"version": "1.0.0", "background": {"service_worker": "background.js"}}))
    (source / "background.js").write_text("try { importScripts('studio-build.js'); } catch {}\nimportScripts('background/gone.js');\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    with pytest.raises(store.StoreError, match="background/gone.js"):
        store.package(tmp_path / "bridge.zip", repo)


@pytest.mark.parametrize(("current", "expected"), [
    (status(), "submit"),
    (status(published="0.2.70"), "submit"),
    (status(published="0.2.71"), "skip"),
    (status(published="0.2.72"), "skip"),
    (status(published="0.2.69", submitted="0.2.71"), "skip"),
    (status(published="0.2.69", submitted="0.2.70"), "cancel_and_submit"),
    (status(published="0.2.69", submitted="0.2.70", state="REJECTED"), "submit"),
    (status(published="0.2.9"), "submit"),
])
def test_only_a_version_newer_than_the_store_is_submitted(current, expected):
    assert store.plan("0.2.71", current) == expected


def test_release_cancels_an_older_review_then_uploads_and_submits(tmp_path, monkeypatch):
    calls = []
    replies = {":fetchStatus": status(published="0.2.69", submitted="0.2.70"), ":cancelSubmission": {},
               ":upload": {"uploadState": "SUCCEEDED", "crxVersion": "0.2.71"}, ":publish": {"state": "PENDING_REVIEW"}}

    def fake(method, url, **kwargs):
        calls.append((method, url.rsplit("/", 1)[1], kwargs.get("headers", {}).get("Content-Type")))
        return replies[url[url.rindex(":"):]]

    monkeypatch.setattr(store, "_call", fake)
    zip_path = tmp_path / "bridge.zip"
    zip_path.write_bytes(b"zip")
    assert store.release(zip_path, "0.2.71", token="t", publisher="pub") == "PENDING_REVIEW"
    item = store.ITEM_ID
    assert calls == [("GET", f"{item}:fetchStatus", None), ("POST", f"{item}:cancelSubmission", "application/json"),
                     ("POST", f"{item}:upload", "application/zip"), ("POST", f"{item}:publish", "application/json")]


def test_release_stops_when_the_store_already_has_the_version(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(store, "_call", lambda method, url, **_: calls.append(url) or status(published="0.2.71"))
    assert store.release(tmp_path / "unused.zip", "0.2.71", token="t", publisher="pub") == "skip"
    assert len(calls) == 1


def test_release_fails_when_the_upload_is_refused(tmp_path, monkeypatch):
    replies = {":fetchStatus": status(published="0.2.69"), ":upload": {"uploadState": "FAILED"}}
    monkeypatch.setattr(store, "_call", lambda method, url, **_: replies[url[url.rindex(":"):]])
    zip_path = tmp_path / "bridge.zip"
    zip_path.write_bytes(b"zip")
    with pytest.raises(store.StoreError, match="FAILED"):
        store.release(zip_path, "0.2.71", token="t", publisher="pub")


def test_sign_in_sends_a_service_account_assertion_google_can_verify(monkeypatch):
    import base64
    import urllib.parse

    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
    sent = {}

    def fake(method, url, **kwargs):
        sent.update(method=method, url=url, form=urllib.parse.parse_qs(kwargs["data"].decode()))
        return {"access_token": "token"}

    monkeypatch.setattr(store, "_call", fake)
    key = json.dumps({"client_email": "bridge@example.iam.gserviceaccount.com", "private_key": pem})
    assert store.access_token(key) == "token"
    assert (sent["method"], sent["url"]) == ("POST", store.TOKEN_URL)
    assert sent["form"]["grant_type"] == ["urn:ietf:params:oauth:grant-type:jwt-bearer"]
    head, body, signature = sent["form"]["assertion"][0].split(".")

    def decode(part: str) -> bytes:
        return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))

    private.public_key().verify(decode(signature), f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
    claims = json.loads(decode(body))
    assert json.loads(decode(head))["alg"] == "RS256"
    assert claims["iss"] == "bridge@example.iam.gserviceaccount.com"
    assert (claims["scope"], claims["aud"]) == (store.SCOPE, store.TOKEN_URL)
    assert 0 < claims["exp"] - claims["iat"] <= 3600


@pytest.mark.parametrize("state", [None, "REJECTED", "CANCELLED", "ITEM_STATE_UNSPECIFIED", "unknown"])
def test_release_fails_closed_without_confirmed_submission(tmp_path, monkeypatch, state):
    replies = {":fetchStatus": status(), ":upload": {"uploadState": "SUCCEEDED"},
               ":publish": {} if state is None else {"state": state}}
    monkeypatch.setattr(store, "_call", lambda method, url, **_: replies[url[url.rindex(":"):]])
    zip_path = tmp_path / "bridge.zip"
    zip_path.write_bytes(b"zip")
    with pytest.raises(store.StoreError, match="did not confirm submission"):
        store.release(zip_path, "0.2.71", token="t", publisher="pub")


@pytest.mark.parametrize("state", ["PENDING_REVIEW", "STAGED", "PUBLISHED", "PUBLISHED_TO_TESTERS"])
def test_release_accepts_only_documented_success_states(tmp_path, monkeypatch, state):
    replies = {":fetchStatus": status(), ":upload": {"uploadState": "SUCCEEDED"},
               ":publish": {"state": state}}
    monkeypatch.setattr(store, "_call", lambda method, url, **_: replies[url[url.rindex(":"):]])
    zip_path = tmp_path / "bridge.zip"
    zip_path.write_bytes(b"zip")
    assert store.release(zip_path, "0.2.71", token="t", publisher="pub") == state


def ci_run(**changes):
    return {"id": 1, "head_sha": "abc", "head_branch": "main", "event": "push",
            "path": ".github/workflows/ci.yml", "status": "completed", "conclusion": "success", **changes}


@pytest.mark.parametrize("runs", [
    [], [ci_run(head_sha="other")], [ci_run(head_branch="feature")],
    [ci_run(event="pull_request")], [ci_run(path=".github/workflows/other.yml")],
    [ci_run(status="in_progress")], [ci_run(conclusion="failure")],
    [ci_run(), ci_run(id=2, conclusion="failure")],
])
def test_ci_gate_rejects_unverified_commit(monkeypatch, runs):
    monkeypatch.setattr(store.subprocess, "run", lambda *_args, **_kwargs:
                        subprocess.CompletedProcess([], 0, stdout=json.dumps({"workflow_runs": runs})))
    with pytest.raises(store.StoreError, match="exact commit"):
        store.verify_ci("owner/repo", "abc")


def test_ci_gate_accepts_exact_main_success_and_checks_ancestry(monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"workflow_runs": [ci_run()]}))

    monkeypatch.setattr(store.subprocess, "run", run)
    store.verify_ci("owner/repo", "abc")
    assert calls[0] == ["git", "merge-base", "--is-ancestor", "abc", "origin/main"]
    assert calls[1][:3] == ["gh", "api", "repos/owner/repo/actions/workflows/ci.yml/runs"]


def test_ci_gate_rejects_commit_outside_main_before_api(monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(store.subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        store.verify_ci("owner/repo", "abc")
    assert len(calls) == 1


def test_missing_credentials_fail_before_any_network_request(tmp_path, monkeypatch):
    archive = tmp_path / "bridge.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("manifest.json", json.dumps({"version": "1.0.0"}))
    monkeypatch.delenv("CHROME_WEB_STORE_SERVICE_ACCOUNT", raising=False)
    monkeypatch.delenv("CHROME_WEB_STORE_PUBLISHER_ID", raising=False)
    monkeypatch.setattr(store, "_call", lambda *_args, **_kwargs: pytest.fail("must not access network"))
    assert store.main(["release", "--package", str(archive)]) == 1


def test_workflow_checks_out_and_gates_the_same_commit_before_credentials():
    workflow = (ROOT / ".github/workflows/chrome-web-store.yml").read_text()
    assert "github.ref == 'refs/heads/main'" in workflow
    assert "github.event.workflow_run.event == 'push'" in workflow
    assert "github.event.workflow_run.head_repository.full_name == github.repository" in workflow
    assert "ref: ${{ github.event.workflow_run.head_sha || github.sha }}" in workflow
    assert "TARGET_SHA: ${{ github.event.workflow_run.head_sha || github.sha }}" in workflow
    assert workflow.index("verify-ci --repo") < workflow.index("CHROME_WEB_STORE_SERVICE_ACCOUNT:")
