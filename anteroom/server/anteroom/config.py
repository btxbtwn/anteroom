from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "Anteroom"
# The app's name before it became Anteroom, and so the name of the data folder
# every install made before the rename.
LEGACY_APP_NAME = "List This Studio"
BUNDLE_ID = "local.listthis.studio"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False) and getattr(sys, "_MEIPASS", None))


def is_packaged() -> bool:
    if is_frozen():
        return True
    return os.environ.get("ANTEROOM_PACKAGED", "") == "1"


def resource_root() -> Path:
    override = os.environ.get("ANTEROOM_RESOURCE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if is_frozen():
        meipass = Path(vars(sys)["_MEIPASS"])
        resources = Path(sys.executable).resolve().parent.parent / "Resources"
        if (meipass / "dist" / "index.html").is_file():
            return meipass
        if (resources / "dist" / "index.html").is_file():
            return resources
        return meipass
    return Path(__file__).resolve().parent.parent.parent


def user_data_root() -> Path:
    override = os.environ.get("ANTEROOM_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if is_packaged():
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return resource_root() / "data"


def skills_dir() -> Path:
    override = os.environ.get("ANTEROOM_SKILLS_DIR")
    if override:
        return Path(override).expanduser().resolve()
    bundled = resource_root() / "skills"
    if (bundled / "list-this" / "SKILL.md").exists():
        return bundled
    return resource_root().parent / "skills"


def extension_source_dir() -> Path:
    override = os.environ.get("ANTEROOM_EXTENSION_DIR")
    if override:
        return Path(override).expanduser().resolve()
    bundled = resource_root() / "anteroom-extension"
    if (bundled / "manifest.json").exists():
        return bundled
    return resource_root().parent / "anteroom-extension"


def frontend_dist_dir() -> Path:
    return resource_root() / "dist"


def log_path() -> Path:
    return user_data_root() / "logs" / f"{APP_NAME}.log"


BASE_DIR = resource_root()
DATA_DIR = str(user_data_root())
# The hosted web app and API for the seller's account. The sandbox points it
# at a local hosted sandbox so a move can be tried without the real account.
HOSTED_URL = os.environ.get("ANTEROOM_HOSTED_URL", "https://myanteroom.com").rstrip("/")
PHOTOS_DIR = str(user_data_root() / "photos")
FILL_LOGS_DIR = str(user_data_root() / "fill-logs")
LOGS_DIR = str(user_data_root() / "logs")
DATABASE_NAME = "anteroom.db"
LEGACY_DATABASE_NAME = "vendoo_studio.db"
DATABASE_PATH = str(user_data_root() / DATABASE_NAME)
# The Mac app keeps its SQLite file. The hosted API points this at Postgres.
DATABASE_URL = os.environ.get("ANTEROOM_DATABASE_URL") or f"sqlite:///{DATABASE_PATH}"
BACKUPS_DIR = str(user_data_root() / "backups")
PAIRING_FILE = str(user_data_root() / "pairing_token.txt")


def adopt_legacy_data_dir() -> None:
    """Move a pre-rename data folder to the new name, once.

    The old name is left as a link to the new folder: Chrome remembers the
    unpacked extension by its absolute path under it, and so do the agent
    workspaces, so both keep resolving after the move.
    """
    if not is_packaged() or os.environ.get("ANTEROOM_DATA_DIR"):
        return
    target = user_data_root()
    legacy = target.with_name(LEGACY_APP_NAME)
    if target.exists() or legacy.is_symlink() or not legacy.is_dir():
        return
    legacy.rename(target)
    legacy.symlink_to(APP_NAME, target_is_directory=True)


def adopt_legacy_database(data_dir: Path | None = None) -> None:
    """Rename a pre-rename SQLite file (and its WAL/SHM siblings) to anteroom.db, once.

    Safe to call again after an interruption: if the main file already moved but a
    legacy ``-wal`` / ``-shm`` / ``-journal`` remains, the next start finishes those
    renames so committed sidecar bytes are not left under the old name. Never opens
    the database; it only renames files that are present.
    """
    if os.environ.get("ANTEROOM_DATABASE_URL"):
        return
    root = data_dir or user_data_root()
    target = root / DATABASE_NAME
    legacy = root / LEGACY_DATABASE_NAME
    if legacy.is_file() and not target.exists():
        legacy.rename(target)
    if not target.exists():
        return
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = root / f"{LEGACY_DATABASE_NAME}{suffix}"
        target_sidecar = root / f"{DATABASE_NAME}{suffix}"
        if sidecar.exists() and not target_sidecar.exists():
            sidecar.rename(target_sidecar)


def ensure_user_data_dirs() -> None:
    user_data_root().mkdir(parents=True, exist_ok=True)
    Path(PHOTOS_DIR).mkdir(parents=True, exist_ok=True)
    Path(FILL_LOGS_DIR).mkdir(parents=True, exist_ok=True)
    Path(LOGS_DIR).mkdir(parents=True, exist_ok=True)
    Path(BACKUPS_DIR).mkdir(parents=True, exist_ok=True)


adopt_legacy_data_dir()
ensure_user_data_dirs()

# Anteroom's relay on Cloudflare: eBay and Etsy sale notifications and sign-in.
# Studio only ever connects out to it.
RELAY_URL = os.environ.get("ANTEROOM_RELAY_URL", "https://relay.anteroom.workers.dev").rstrip("/")

HOST = os.environ.get("ANTEROOM_HOST", "127.0.0.1")
if HOST != "127.0.0.1":
    raise ValueError("The local Anteroom app must bind to 127.0.0.1. Use private Tailscale Serve for remote access.")
PORT = int(os.environ.get("ANTEROOM_PORT", "4318"))
# The Vite dev server's port; scripts/dev.sh and vite.config.ts read the same variable.
WEB_PORT = int(os.environ.get("ANTEROOM_WEB_PORT", "5173"))

MAX_PHOTO_COUNT = 20
MAX_PHOTO_SIZE_MB = 20
ALLOWED_PHOTO_MIME = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}

CORS_ORIGINS = [
    f"http://{HOST}:{PORT}",
    f"http://{HOST}:{WEB_PORT}",
    f"http://localhost:{PORT}",
    f"http://localhost:{WEB_PORT}",
]
