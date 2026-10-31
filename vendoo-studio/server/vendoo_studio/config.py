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
    return os.environ.get("VENDOO_STUDIO_PACKAGED", "") == "1"


def resource_root() -> Path:
    override = os.environ.get("VENDOO_STUDIO_RESOURCE_DIR")
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
    override = os.environ.get("VENDOO_STUDIO_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if is_packaged():
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return resource_root() / "data"


def skills_dir() -> Path:
    override = os.environ.get("VENDOO_STUDIO_SKILLS_DIR")
    if override:
        return Path(override).expanduser().resolve()
    bundled = resource_root() / "skills"
    if (bundled / "list-this" / "SKILL.md").exists():
        return bundled
    return resource_root().parent / "skills"


def extension_source_dir() -> Path:
    override = os.environ.get("VENDOO_STUDIO_EXTENSION_DIR")
    if override:
        return Path(override).expanduser().resolve()
    bundled = resource_root() / "vendoo-extension"
    if (bundled / "manifest.json").exists():
        return bundled
    return resource_root().parent / "vendoo-extension"


def frontend_dist_dir() -> Path:
    return resource_root() / "dist"


def log_path() -> Path:
    return user_data_root() / "logs" / f"{APP_NAME}.log"


BASE_DIR = resource_root()
DATA_DIR = str(user_data_root())
PHOTOS_DIR = str(user_data_root() / "photos")
FILL_LOGS_DIR = str(user_data_root() / "fill-logs")
LOGS_DIR = str(user_data_root() / "logs")
DATABASE_PATH = str(user_data_root() / "vendoo_studio.db")
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
    if not is_packaged() or os.environ.get("VENDOO_STUDIO_DATA_DIR"):
        return
    target = user_data_root()
    legacy = target.with_name(LEGACY_APP_NAME)
    if target.exists() or legacy.is_symlink() or not legacy.is_dir():
        return
    legacy.rename(target)
    legacy.symlink_to(APP_NAME, target_is_directory=True)


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
RELAY_URL = os.environ.get("VENDOO_STUDIO_RELAY_URL", "https://relay.anteroom.workers.dev").rstrip("/")

HOST = os.environ.get("VENDOO_STUDIO_HOST", "127.0.0.1")
if HOST != "127.0.0.1":
    raise ValueError("The local Anteroom app must bind to 127.0.0.1. Use private Tailscale Serve for remote access.")
PORT = int(os.environ.get("VENDOO_STUDIO_PORT", "4318"))
# The Vite dev server's port; scripts/dev.sh and vite.config.ts read the same variable.
WEB_PORT = int(os.environ.get("VENDOO_STUDIO_WEB_PORT", "5173"))

MAX_PHOTO_COUNT = 20
MAX_PHOTO_SIZE_MB = 20
ALLOWED_PHOTO_MIME = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}

CORS_ORIGINS = [
    f"http://{HOST}:{PORT}",
    f"http://{HOST}:{WEB_PORT}",
    f"http://localhost:{PORT}",
    f"http://localhost:{WEB_PORT}",
]
