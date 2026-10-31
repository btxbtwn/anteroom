from __future__ import annotations

import hashlib
import json
import math
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

import httpx

from vendoo_studio.config import APP_NAME, is_frozen, resource_root, user_data_root
from vendoo_studio.version import app_version

GITHUB_API = os.environ.get("VENDOO_STUDIO_GITHUB_API", "https://api.github.com")
GITHUB_DOWNLOAD = os.environ.get("VENDOO_STUDIO_GITHUB_DOWNLOAD", "https://github.com")
GITHUB_REPO = os.environ.get("VENDOO_STUDIO_GITHUB_REPO", "btxbtwn/anteroom")
RELEASE_TAG = os.environ.get("VENDOO_STUDIO_RELEASE_TAG", "studio-macos")
# Every build is also published under its own tag, "studio-macos-v<version>-<sha>",
# so a build that turns out broken can be swapped for an earlier one.
VERSIONED_TAG_PREFIX = f"{RELEASE_TAG}-v"
ZIP_NAME = "Anteroom-macos.zip"
INFO_NAME = "build_info.json"
APP_BUNDLE_NAME = f"{APP_NAME}.app"
USER_AGENT = f"Anteroom/{app_version()}"
# Download fills this band; verify / extract / codesign take the rest so the
# meter keeps moving after the last network byte (codesign is often longer).
DOWNLOAD_PROGRESS_FLOOR = 5.0
DOWNLOAD_PROGRESS_CEILING = 90.0
DOWNLOAD_CHUNK_SIZE = 64 * 1024
DOWNLOAD_ATTEMPTS = 5
# Half-life for unknown Content-Length downloads (~25 MiB → ~63% of the band).
UNKNOWN_SIZE_HALF_BYTES = 25 * 1024 * 1024

_prepared_lock = threading.Lock()
_prepared_update: dict | None = None


class PackagedUpdateError(RuntimeError):
    pass


def build_info_path() -> Path:
    override = os.environ.get("VENDOO_STUDIO_BUILD_INFO")
    if override:
        return Path(override).expanduser().resolve()
    return resource_root() / INFO_NAME


def local_build_info() -> dict:
    path = build_info_path()
    fallback = app_version()
    if not path.is_file():
        return {"version": fallback, "sha": None, "short_sha": None, "ref": None}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": fallback, "sha": None, "short_sha": None, "ref": None}
    sha = payload.get("sha")
    return {
        "version": payload.get("version") or fallback,
        "sha": sha,
        "short_sha": payload.get("short_sha") or (sha[:7] if sha else None),
        "ref": payload.get("ref"),
    }


def installed_app_path() -> Path:
    override = os.environ.get("VENDOO_STUDIO_APP_PATH")
    if override:
        return Path(override).expanduser().resolve()
    if is_frozen():
        return Path(sys.executable).resolve().parents[2]
    return Path.home() / "Applications" / APP_BUNDLE_NAME


def release_asset_url(name: str, tag: str = RELEASE_TAG) -> str:
    """Stable public download URL that does not consume the GitHub REST rate limit."""
    return f"{GITHUB_DOWNLOAD.rstrip('/')}/{GITHUB_REPO}/releases/download/{tag}/{name}"


def release_page_url() -> str:
    return f"{GITHUB_DOWNLOAD.rstrip('/')}/{GITHUB_REPO}/releases/tag/{RELEASE_TAG}"


def _github_token() -> str:
    for key in ("VENDOO_STUDIO_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
        token = (os.environ.get(key) or "").strip()
        if token:
            return token
    return ""


def _headers(*, api: bool = False) -> dict[str, str]:
    headers = {"User-Agent": USER_AGENT}
    if api:
        headers["Accept"] = "application/vnd.github+json"
        token = _github_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
    else:
        headers["Accept"] = "application/octet-stream"
    return headers


def _rate_limit_message(response: httpx.Response) -> str | None:
    if response.status_code != 403:
        return None
    body = (response.text or "").lower()
    if "rate limit" not in body and "api rate limit" not in body:
        return None
    return (
        "GitHub API rate limit exceeded. Update checks use release downloads and "
        "should not need the API; retry later or set GITHUB_TOKEN for API fallbacks."
    )


def fetch_release(client: httpx.Client | None = None) -> dict:
    """API fallback for releases whose build_info.json has no digest for ZIP_NAME."""
    url = f"{GITHUB_API.rstrip('/')}/repos/{GITHUB_REPO}/releases/tags/{RELEASE_TAG}"
    own_client = client is None
    http = client or httpx.Client(timeout=30.0, headers=_headers(api=True), follow_redirects=True)
    try:
        response = http.get(url)
        if response.status_code == 404:
            raise PackagedUpdateError("No macOS release has been published yet.")
        limited = _rate_limit_message(response)
        if limited:
            raise PackagedUpdateError(limited)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as exc:
        raise PackagedUpdateError(f"Could not reach GitHub releases: {exc}") from exc
    finally:
        if own_client:
            http.close()


def _normalize_build_info(payload: dict) -> dict:
    sha = payload.get("sha")
    # Digests are keyed by asset name. The top-level zip_sha256 belongs to the
    # zip that builds from before the rename download, not to ZIP_NAME.
    zips = payload.get("zips")
    if not isinstance(zips, dict):
        zips = {}
    zip_sha = str(zips.get(ZIP_NAME) or "").strip().lower()
    if zip_sha.startswith("sha256:"):
        zip_sha = zip_sha.split(":", 1)[1].strip().lower()
    title = str(payload.get("title") or payload.get("summary") or "").strip()
    pull_requests = []
    for item in payload.get("pull_requests") or []:
        if not isinstance(item, dict):
            continue
        raw_number = item.get("number")
        if raw_number is None:
            continue
        try:
            number = int(raw_number)
        except (TypeError, ValueError):
            continue
        pull_title = str(item.get("title") or "").strip()
        if number > 0 and pull_title:
            pull_requests.append({"number": number, "title": pull_title})
    commits = [
        f"#{item['number']} — {item['title']}"
        for item in pull_requests
    ]
    if not commits:
        commits = [
            str(item).strip()
            for item in payload.get("commits") or []
            if str(item).strip()
        ]
    return {
        "version": payload.get("version") or app_version(),
        "sha": sha,
        "short_sha": payload.get("short_sha") or (sha[:7] if isinstance(sha, str) and sha else None),
        "ref": payload.get("ref"),
        "zip_sha256": zip_sha or None,
        "schema_revision": str(payload.get("schema_revision") or "").strip() or None,
        "title": title,
        "pull_requests": pull_requests,
        "commits": commits,
    }


def fetch_remote_build_info(client: httpx.Client | None = None, tag: str = RELEASE_TAG) -> dict:
    """Load the published build stamp from the release asset CDN (no REST quota)."""
    url = release_asset_url(INFO_NAME, tag)
    own_client = client is None
    http = client or httpx.Client(timeout=30.0, headers=_headers(), follow_redirects=True)
    try:
        response = http.get(url)
        if response.status_code == 404:
            raise PackagedUpdateError("No macOS release has been published yet.")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise PackagedUpdateError("Published build_info.json is invalid.")
        return _normalize_build_info(payload)
    except httpx.HTTPError as exc:
        raise PackagedUpdateError(f"Could not reach GitHub releases: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise PackagedUpdateError("Published build_info.json is invalid.") from exc
    finally:
        if own_client:
            http.close()


def _asset_map(release: dict) -> dict[str, dict]:
    return {asset.get("name"): asset for asset in release.get("assets") or [] if asset.get("name")}


def _asset_digest(asset: dict | None) -> str | None:
    if not isinstance(asset, dict):
        return None
    digest = str(asset.get("digest") or "").strip()
    if digest.lower().startswith("sha256:"):
        return digest.split(":", 1)[1].strip().lower()
    sha = str(asset.get("sha256") or "").strip().lower()
    return sha or None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_archive_digest(archive: Path, expected: str | None) -> None:
    if not expected:
        raise PackagedUpdateError("The release does not include a SHA-256 digest for the Mac zip.")
    actual = _sha256_file(archive)
    if actual.lower() != expected.lower():
        raise PackagedUpdateError("Downloaded update did not match the signed digest.")


def _verify_app_signature(app_path: Path) -> None:
    """Require an intact signature from a signing certificate.

    Keychain lets an update keep the saved sign-ins only when it is signed by
    the same certificate as the installed app. An ad-hoc or broken signature
    would lose that access, so such a download is refused rather than
    installed. The zip's SHA-256 digest from the release is checked separately.
    """
    if os.environ.get("VENDOO_STUDIO_SKIP_CODESIGN") == "1":
        return
    display = subprocess.run(
        ["/usr/bin/codesign", "-dv", str(app_path)],
        capture_output=True,
        text=True,
    )
    detail = (display.stderr or display.stdout or "").strip()
    if display.returncode != 0 or "code object is not signed at all" in detail:
        raise PackagedUpdateError("The update is not signed by a trusted identity.")
    if "Signature=adhoc" in detail:
        raise PackagedUpdateError("The update is not signed with Anteroom's certificate.")
    installed = subprocess.run(
        ["/usr/bin/codesign", "-dr", "-", str(installed_app_path())],
        capture_output=True,
        text=True,
    )
    # codesign writes the requirement to stdout and only the executable path to
    # stderr, so both streams are read.
    requirement = next((line.split("designated =>", 1)[1].strip()
                        for line in (raw.lstrip("# ") for raw in
                                     (installed.stdout + "\n" + installed.stderr).splitlines())
                        if line.startswith("designated =>")), "")
    if installed.returncode != 0 or not requirement:
        raise PackagedUpdateError("The installed app's signing identity could not be verified. Reinstall a trusted signed copy.")
    # The requirement is passed as its own argument with -R=. codesign does not
    # take a separate value here: "-R <requirement> <app>" is read as a
    # requirement naming a file, so it fails with "No such file or directory /
    # invalid requirement specification" and every update is refused.
    verify = subprocess.run(
        ["/usr/bin/codesign", "--verify", "--deep", "--strict",
         f"-R={requirement}", str(app_path)],
        capture_output=True,
        text=True,
    )
    if verify.returncode != 0:
        reason = (verify.stderr or verify.stdout or "").strip().splitlines()
        raise PackagedUpdateError(
            "The update's code signature is damaged or does not match Anteroom's signing identity"
            + (f": {reason[-1]}" if reason else ".")
        )


def _path_inside(root: Path, candidate: Path) -> bool:
    root = root.resolve()
    try:
        candidate = candidate.resolve(strict=False)
    except RuntimeError as exc:
        raise PackagedUpdateError("Archive contains an unsafe path.") from exc
    return candidate == root or str(candidate).startswith(str(root) + os.sep)


def _validate_symlink_target(destination: Path, member_name: str, link_target: str) -> None:
    """Reject absolute links and relative links that escape the extract root."""
    destination = destination.resolve()
    if not link_target or link_target.startswith("/") or link_target.startswith("~"):
        raise PackagedUpdateError("Archive contains an unsafe symbolic link.")
    member_parent = (destination / member_name).parent
    # Pure lexical join so missing parents do not affect the safety check.
    joined = Path(os.path.normpath(member_parent / link_target))
    if not _path_inside(destination, joined):
        raise PackagedUpdateError("Archive contains an unsafe symbolic link.")


def _validate_zip_members(archive: Path, destination: Path) -> None:
    destination = destination.resolve()
    with zipfile.ZipFile(archive) as bundle:
        for info in bundle.infolist():
            target = (destination / info.filename).resolve()
            if not _path_inside(destination, target):
                raise PackagedUpdateError("Archive contains an unsafe path.")
            mode = (info.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                link_target = bundle.read(info).decode("utf-8", errors="surrogateescape")
                _validate_symlink_target(destination, info.filename, link_target)


def _safe_extract_zip(archive: Path, destination: Path) -> None:
    _validate_zip_members(archive, destination)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(destination)


def map_download_progress(raw_percent: float) -> float:
    """Map 0–100 download completion into the prepare-update progress band."""
    clamped = min(100.0, max(0.0, float(raw_percent)))
    span = DOWNLOAD_PROGRESS_CEILING - DOWNLOAD_PROGRESS_FLOOR
    return DOWNLOAD_PROGRESS_FLOOR + (clamped / 100.0) * span


def unknown_size_download_progress(downloaded: int) -> float:
    """Asymptotic 0–100% when the response has no Content-Length."""
    if downloaded <= 0:
        return 0.0
    return 100.0 * (1.0 - math.exp(-downloaded / UNKNOWN_SIZE_HALF_BYTES))


def _download(
    client: httpx.Client,
    url: str,
    destination: Path,
    progress_callback=None,
) -> None:
    # Replacing the rolling release deletes its old asset before uploading the
    # new one. Allow that short 404 window without retrying other HTTP errors.
    for attempt in range(DOWNLOAD_ATTEMPTS):
        try:
            _download_once(client, url, destination, progress_callback)
            return
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
            if attempt == DOWNLOAD_ATTEMPTS - 1:
                raise PackagedUpdateError(
                    "The Mac update download is temporarily unavailable. "
                    "Wait a minute, then click Download Update again."
                ) from exc
            time.sleep(2 ** (attempt + 1))


def _download_once(
    client: httpx.Client,
    url: str,
    destination: Path,
    progress_callback=None,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with client.stream("GET", url) as response:
        response.raise_for_status()
        total = int(response.headers.get("content-length") or 0)
        downloaded = 0
        last_reported = -1.0

        def report(raw_percent: float) -> None:
            nonlocal last_reported
            if not progress_callback:
                return
            mapped = map_download_progress(raw_percent)
            # Emit at least every half percent so the sidebar meter moves, and
            # always emit the band ceiling when the body is fully written.
            if mapped < DOWNLOAD_PROGRESS_CEILING and mapped - last_reported < 0.5:
                return
            last_reported = mapped
            progress_callback(mapped)

        with destination.open("wb") as handle:
            for chunk in response.iter_bytes(chunk_size=DOWNLOAD_CHUNK_SIZE):
                handle.write(chunk)
                downloaded += len(chunk)
                if total > 0:
                    report(downloaded / total * 100.0)
                else:
                    report(unknown_size_download_progress(downloaded))
        if progress_callback:
            progress_callback(DOWNLOAD_PROGRESS_CEILING)


def _resolve_zip_digest(remote: dict, client: httpx.Client | None = None) -> str | None:
    digest = remote.get("zip_sha256")
    if digest:
        return str(digest)
    # Older releases only expose the digest on the GitHub API asset record.
    release = fetch_release(client=client)
    return _asset_digest(_asset_map(release).get(ZIP_NAME))


def check_for_packaged_update() -> dict:
    local = local_build_info()
    try:
        remote = fetch_remote_build_info()
    except PackagedUpdateError as exc:
        return {
            "available": False,
            "packaged": True,
            "local_sha": local.get("sha"),
            "error": str(exc),
        }
    remote_sha = remote.get("sha")
    local_sha = local.get("sha")
    available = bool(remote_sha) and remote_sha != local_sha
    commits = list(remote.get("commits") or [])
    summary = str((commits[0] if commits else remote.get("title")) or "").strip()
    return {
        "available": available,
        "packaged": True,
        "behind": (len(commits) or 1) if available else 0,
        "ahead": 0,
        "branch": remote.get("ref") or "main",
        "local_sha": local_sha,
        "remote_sha": remote_sha,
        "remote_ref": f"github:{GITHUB_REPO}:{RELEASE_TAG}",
        "summary": summary if available else "",
        "commits": (commits or ([summary] if summary else [])) if available else [],
        "dirty": [],
        "error": None,
        "short_sha": remote.get("short_sha"),
        "download_url": release_asset_url(ZIP_NAME),
        "sha256": remote.get("zip_sha256"),
        "release_url": release_page_url(),
    }


def _prepare_app_bundle(app_path: Path, *, clear_quarantine: bool = False) -> None:
    """Restore execute bits. Quarantine is cleared only after signature verification."""
    macos = app_path / "Contents" / "MacOS"
    if macos.is_dir():
        for path in macos.iterdir():
            if path.is_file():
                path.chmod(path.stat().st_mode | 0o111)
    if not clear_quarantine:
        return
    try:
        subprocess.run(
            ["/usr/bin/xattr", "-cr", str(app_path)],
            check=False,
            capture_output=True,
        )
    except FileNotFoundError:
        pass


def _extract_app(archive: Path, destination: Path) -> Path:
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    _validate_zip_members(archive, destination)
    try:
        extracted = subprocess.run(
            ["/usr/bin/ditto", "-xk", str(archive), str(destination)],
            capture_output=True,
            text=True,
        )
        ditto_ok = extracted.returncode == 0
    except FileNotFoundError:
        ditto_ok = False
    if not ditto_ok:
        _safe_extract_zip(archive, destination)
    dest = destination.resolve()
    for path in dest.rglob("*"):
        if not _path_inside(dest, path):
            raise PackagedUpdateError("Archive contains an unsafe path.")
    matches = [path for path in destination.rglob(APP_BUNDLE_NAME) if path.is_dir()]
    if not matches:
        raise PackagedUpdateError(f"The downloaded zip did not contain {APP_BUNDLE_NAME}.")
    app = min(matches, key=lambda path: len(path.parts))
    _prepare_app_bundle(app, clear_quarantine=False)
    return app


def _write_replacer(
    app_path: Path,
    new_app: Path,
    pid: int,
    *,
    restore_database: tuple[Path, Path] | None = None,
) -> Path:
    script = user_data_root() / "updates" / "replace-app.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    # Install under the bundle's own name. An app installed before the rename
    # sits at "List This Studio.app"; this moves it to "Anteroom.app".
    target = app_path.with_name(new_app.name)
    # Copy beside the installed app and swap it in whole. Copying over the
    # installed bundle merges the two: files the new build dropped stay behind,
    # the code seal no longer matches, and Keychain stops recognising the app,
    # asking for the login password on every launch.
    staged = target.with_name(f"{target.name}.updating")
    retired = app_path.with_name(f"{app_path.name}.previous")
    app, target_q, staged_q, retired_q = (shlex.quote(str(p)) for p in (app_path, target, staged, retired))
    # Going back to a build with an older database layout puts a snapshot from
    # that layout in place. It has to happen after this process exits, or the
    # -wal it still holds would be replayed onto the restored file.
    database_lines: list[str] = []
    if restore_database:
        staged_db, live_db = restore_database
        live_q = shlex.quote(str(live_db))
        database_lines = [
            f"/bin/rm -f {live_q}-wal {live_q}-shm",
            f"/bin/mv {shlex.quote(str(staged_db))} {live_q}",
        ]
    script.write_text(
        "\n".join(
            [
                "#!/bin/bash",
                "set -euo pipefail",
                f"while /bin/kill -0 {pid} 2>/dev/null; do sleep 0.2; done",
                "sleep 0.4",
                f"/bin/rm -rf {staged_q} {retired_q}",
                f"/usr/bin/ditto {shlex.quote(str(new_app))} {staged_q}",
                f"/usr/bin/xattr -cr {staged_q} || true",
                f"/bin/chmod -R u+x {staged_q}/Contents/MacOS || true",
                f"/bin/mv {app} {retired_q}",
                *([f"/bin/rm -rf {target_q}"] if target != app_path else []),
                f"/bin/mv {staged_q} {target_q}",
                f"/bin/rm -rf {retired_q}",
                *database_lines,
                f"/usr/bin/open {target_q}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def _stage_app(download_url: str, expected_digest: str | None, report, progress_callback=None) -> tuple[Path, Path]:
    """Download a release zip, check its digest and signature, and unpack it."""
    staging = Path(tempfile.mkdtemp(prefix="anteroom-update-"))
    archive = staging / ZIP_NAME
    report(DOWNLOAD_PROGRESS_FLOOR)
    with httpx.Client(timeout=120.0, headers=_headers(), follow_redirects=True) as client:
        _download(client, download_url, archive, progress_callback)
    report(91.0)
    _verify_archive_digest(archive, expected_digest)
    report(94.0)
    new_app = _extract_app(archive, staging / "unpacked")
    report(97.0)
    _verify_app_signature(new_app)
    _prepare_app_bundle(new_app, clear_quarantine=True)
    return staging, new_app


def prepare_packaged_update(*, force: bool = False, progress_callback=None) -> dict:
    """Download, verify, and unpack an update without restarting the app."""
    global _prepared_update

    def report(percent: float) -> None:
        if progress_callback:
            progress_callback(min(100.0, max(0.0, float(percent))))

    report(1.0)
    status = check_for_packaged_update()
    download_url = status.get("download_url")
    if not force and not status.get("available"):
        if status.get("error"):
            raise PackagedUpdateError(status["error"])
        return {"ok": True, "updated": False, "sha": status.get("local_sha"), "packaged": True}
    if force and status.get("error") and not download_url:
        raise PackagedUpdateError(status["error"])
    if not download_url:
        raise PackagedUpdateError("The GitHub release does not include a Mac zip.")
    app_path = installed_app_path()
    if not app_path.exists():
        raise PackagedUpdateError(f"Cannot replace {app_path} because that app is missing.")

    expected_digest = status.get("sha256")
    if not expected_digest:
        expected_digest = _resolve_zip_digest({"zip_sha256": None})
    staging, new_app = _stage_app(download_url, expected_digest, report, progress_callback)
    prepared = {
        "app_path": app_path,
        "new_app": new_app,
        "staging": staging,
        "sha": status.get("remote_sha"),
        "packaged": True,
        "reinstalled": force,
    }
    with _prepared_lock:
        previous = _prepared_update
        _prepared_update = prepared
    if previous:
        previous_staging = previous.get("staging")
        if isinstance(previous_staging, Path) and previous_staging != staging:
            shutil.rmtree(previous_staging, ignore_errors=True)
    report(100.0)
    return {
        "ok": True,
        "updated": True,
        "prepared": True,
        "sha": status.get("remote_sha"),
        "packaged": True,
        "reinstalled": force,
    }


def install_prepared_packaged_update() -> dict:
    """Arm the staged app replacement. The caller is responsible for exiting."""
    global _prepared_update

    with _prepared_lock:
        prepared = _prepared_update
    if not prepared:
        raise PackagedUpdateError("Download the update before restarting to install it.")
    app_path = prepared["app_path"]
    new_app = prepared["new_app"]
    if not app_path.exists() or not new_app.exists():
        raise PackagedUpdateError("The downloaded update is no longer available. Download it again.")
    _verify_app_signature(new_app)
    script = _write_replacer(app_path, new_app, os.getpid())
    subprocess.Popen(
        [str(script)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    with _prepared_lock:
        _prepared_update = None
    return {
        "ok": True,
        "updated": True,
        "sha": prepared.get("sha"),
        "packaged": True,
        "relaunch": True,
        "reinstalled": bool(prepared.get("reinstalled")),
    }


def apply_packaged_update(*, force: bool = False) -> dict:
    prepared = prepare_packaged_update(force=force)
    if not prepared.get("updated"):
        return prepared
    return install_prepared_packaged_update()


def reinstall_packaged_app() -> dict:
    """Download the published Mac zip and replace the installed app, even if already current."""
    return apply_packaged_update(force=True)


def _parse_versioned_tag(tag: str) -> tuple[str, str] | None:
    """"studio-macos-v0.1.594-abc1234" -> ("0.1.594", "abc1234")."""
    if not tag.startswith(VERSIONED_TAG_PREFIX):
        return None
    version, _, short_sha = tag[len(VERSIONED_TAG_PREFIX):].rpartition("-")
    if not version or not short_sha:
        return None
    return version, short_sha


def list_published_versions(client: httpx.Client | None = None) -> list[dict]:
    """Every build still published under its own tag, newest first."""
    url = f"{GITHUB_API.rstrip('/')}/repos/{GITHUB_REPO}/releases"
    own_client = client is None
    http = client or httpx.Client(timeout=30.0, headers=_headers(api=True), follow_redirects=True)
    try:
        response = http.get(url, params={"per_page": 100})
        limited = _rate_limit_message(response)
        if limited:
            raise PackagedUpdateError(limited)
        response.raise_for_status()
        releases = response.json()
    except httpx.HTTPError as exc:
        raise PackagedUpdateError(f"Could not reach GitHub releases: {exc}") from exc
    finally:
        if own_client:
            http.close()

    local_sha = str(local_build_info().get("sha") or "")
    versions = []
    for release in releases if isinstance(releases, list) else []:
        tag = str(release.get("tag_name") or "")
        parsed = _parse_versioned_tag(tag)
        if not parsed or release.get("draft"):
            continue
        version, short_sha = parsed
        versions.append({
            "tag": tag,
            "version": version,
            "short_sha": short_sha,
            "title": str(release.get("name") or "").strip(),
            "published_at": release.get("published_at") or release.get("created_at"),
            "url": release.get("html_url"),
            "installed": bool(local_sha) and local_sha.startswith(short_sha),
        })
    versions.sort(key=lambda item: item["published_at"] or "", reverse=True)
    return versions


def fetch_version_build_info(tag: str) -> dict:
    if not _parse_versioned_tag(tag):
        raise PackagedUpdateError(f"{tag} is not a published Anteroom build.")
    return fetch_remote_build_info(tag=tag)


def install_packaged_version(
    tag: str,
    remote: dict,
    *,
    restore_database: tuple[Path, Path] | None = None,
    progress_callback=None,
) -> dict:
    """Download the build published under ``tag`` and arm the swap to it."""

    def report(percent: float) -> None:
        if progress_callback:
            progress_callback(min(100.0, max(0.0, float(percent))))

    app_path = installed_app_path()
    if not app_path.exists():
        raise PackagedUpdateError(f"Cannot replace {app_path} because that app is missing.")
    if not remote.get("zip_sha256"):
        raise PackagedUpdateError("That build does not include a SHA-256 digest for the Mac zip.")
    _staging, new_app = _stage_app(
        release_asset_url(ZIP_NAME, tag), remote["zip_sha256"], report, progress_callback
    )
    script = _write_replacer(app_path, new_app, os.getpid(), restore_database=restore_database)
    subprocess.Popen(
        [str(script)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    report(100.0)
    return {
        "ok": True,
        "updated": True,
        "sha": remote.get("sha"),
        "version": remote.get("version"),
        "packaged": True,
        "relaunch": True,
        "database_restored": restore_database is not None,
    }
