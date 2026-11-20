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
# Durable move-plan marker written before any rename. Presence of anteroom.db alone
# is not provenance; only a parseable plan proves an in-progress transfer.
LEGACY_DATABASE_ADOPTION_MARKER = "anteroom.db.adopting-from-vendoo_studio"
LEGACY_DATABASE_ADOPTION_MARKER_VERSION = "anteroom-legacy-db-adoption/2"
_LEGACY_DATABASE_SIDECARS = ("-wal", "-shm", "-journal")
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


class _LegacyDbMove:
    __slots__ = ("source_name", "dest_name", "size", "mtime_ns")

    def __init__(self, source_name: str, dest_name: str, size: int, mtime_ns: int) -> None:
        self.source_name = source_name
        self.dest_name = dest_name
        self.size = size
        self.mtime_ns = mtime_ns


def _legacy_database_paths(root: Path) -> tuple[Path, Path, Path]:
    return (
        root / DATABASE_NAME,
        root / LEGACY_DATABASE_NAME,
        root / LEGACY_DATABASE_ADOPTION_MARKER,
    )


def _legacy_database_file_identity(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


def _serialize_legacy_database_move_plan(plan: list[_LegacyDbMove]) -> bytes:
    lines = [LEGACY_DATABASE_ADOPTION_MARKER_VERSION]
    for step in plan:
        lines.append(
            f"move:{step.source_name}>{step.dest_name}:{step.size}:{step.mtime_ns}"
        )
    return ("\n".join(lines) + "\n").encode("utf-8")


def _parse_legacy_database_move_plan(data: bytes) -> list[_LegacyDbMove] | None:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    lines = text.splitlines()
    if not lines or lines[0] != LEGACY_DATABASE_ADOPTION_MARKER_VERSION:
        return None
    plan: list[_LegacyDbMove] = []
    for line in lines[1:]:
        if not line.startswith("move:"):
            return None
        rest = line[len("move:") :]
        try:
            path_part, size_text, mtime_text = rest.rsplit(":", 2)
            source_name, dest_name = path_part.split(">", 1)
        except ValueError:
            return None
        if not source_name or not dest_name or "/" in source_name or "/" in dest_name:
            return None
        try:
            plan.append(
                _LegacyDbMove(source_name, dest_name, int(size_text), int(mtime_text))
            )
        except ValueError:
            return None
    return plan or None


def _read_legacy_database_move_plan(marker: Path) -> list[_LegacyDbMove] | None:
    try:
        return _parse_legacy_database_move_plan(marker.read_bytes())
    except OSError:
        return None


def _fsync_directory(directory: Path) -> None:
    """Make recent directory entries durable (create/rename/unlink)."""
    dir_fd = os.open(str(directory), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def _write_legacy_database_adoption_marker(marker: Path, plan: list[_LegacyDbMove]) -> None:
    """Persist the move plan before any rename (file + directory fsync)."""
    data = _serialize_legacy_database_move_plan(plan)
    with marker.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    _fsync_directory(marker.parent)


def _legacy_database_names_present(root: Path) -> bool:
    if (root / LEGACY_DATABASE_NAME).exists():
        return True
    return any((root / f"{LEGACY_DATABASE_NAME}{suffix}").exists() for suffix in _LEGACY_DATABASE_SIDECARS)


def _abandon_legacy_database_adoption(marker: Path) -> None:
    """Stop claiming an in-progress transfer. Database files are left untouched."""
    marker.unlink(missing_ok=True)
    _fsync_directory(marker.parent)


def _build_fresh_legacy_database_move_plan(
    root: Path, legacy: Path, target: Path
) -> list[_LegacyDbMove] | None:
    """Plan a fresh adoption, or None when any target sidecar/primary already exists."""
    if target.exists() or not legacy.is_file():
        return None
    for suffix in _LEGACY_DATABASE_SIDECARS:
        if (root / f"{DATABASE_NAME}{suffix}").exists():
            # Preexisting new-name sidecar must not be paired with a renamed primary.
            return None
        source = root / f"{LEGACY_DATABASE_NAME}{suffix}"
        destination = root / f"{DATABASE_NAME}{suffix}"
        if source.exists() and destination.exists():
            return None
    plan = [
        _LegacyDbMove(
            LEGACY_DATABASE_NAME,
            DATABASE_NAME,
            *_legacy_database_file_identity(legacy),
        )
    ]
    for suffix in _LEGACY_DATABASE_SIDECARS:
        source = root / f"{LEGACY_DATABASE_NAME}{suffix}"
        if source.is_file():
            plan.append(
                _LegacyDbMove(
                    f"{LEGACY_DATABASE_NAME}{suffix}",
                    f"{DATABASE_NAME}{suffix}",
                    *_legacy_database_file_identity(source),
                )
            )
    return plan


def _preflight_legacy_database_move_plan(root: Path, plan: list[_LegacyDbMove]) -> bool:
    """True only when every planned step is already done or still a safe rename."""
    for step in plan:
        source = root / step.source_name
        destination = root / step.dest_name
        source_exists = source.exists()
        destination_exists = destination.exists()
        if source_exists and destination_exists:
            return False
        if source_exists:
            try:
                size, mtime_ns = _legacy_database_file_identity(source)
            except OSError:
                return False
            if size != step.size or mtime_ns != step.mtime_ns:
                return False
            continue
        if not destination_exists:
            # Source gone and destination missing: incomplete or tampered.
            return False
    return True


def _execute_legacy_database_move_plan(
    root: Path, plan: list[_LegacyDbMove], marker: Path
) -> None:
    """Run only after a full-plan preflight. Fail closed without further renames."""
    if not _preflight_legacy_database_move_plan(root, plan):
        _abandon_legacy_database_adoption(marker)
        return
    for step in plan:
        source = root / step.source_name
        destination = root / step.dest_name
        if not source.exists():
            continue
        if destination.exists():
            _abandon_legacy_database_adoption(marker)
            return
        try:
            size, mtime_ns = _legacy_database_file_identity(source)
        except OSError:
            _abandon_legacy_database_adoption(marker)
            return
        if size != step.size or mtime_ns != step.mtime_ns:
            _abandon_legacy_database_adoption(marker)
            return
        source.rename(destination)
    # Renames must be durable before the marker can go; keep it if sync fails.
    try:
        _fsync_directory(root)
    except OSError:
        return
    if not _legacy_database_names_present(root):
        _abandon_legacy_database_adoption(marker)


def adopt_legacy_database(data_dir: Path | None = None) -> None:
    """Rename a pre-rename SQLite file (and its WAL/SHM siblings) to anteroom.db, once.

    Fresh adoption preflights the whole set first: any preexisting ``anteroom.db``
    sidecar rejects the transfer so a renamed primary is never paired with a foreign
    WAL/journal. A durable move plan (source identities + destinations) is fsynced
    before any rename; resume re-validates the whole plan and fails closed without
    further moves on ambiguity.

    Only a parseable ``anteroom-legacy-db-adoption/2`` plan is a recovery claim.
    Pre-v2 constant-text markers and other unparseable marker bytes are not: they
    carry no source identities, so they are dropped and adoption continues with the
    fresh-plan path when that path is still eligible. Refusal never blocks opening
    the database; this function only renames files. Historical unmarked interruptions
    cannot be identified safely and are not guessed.
    """
    if os.environ.get("ANTEROOM_DATABASE_URL"):
        return
    root = data_dir or user_data_root()
    target, legacy, marker = _legacy_database_paths(root)

    if marker.is_file():
        plan = _read_legacy_database_move_plan(marker)
        if plan is not None:
            _execute_legacy_database_move_plan(root, plan, marker)
            return
        # Not a v2 plan: clear and fall through (startup is never blocked).
        _abandon_legacy_database_adoption(marker)

    plan = _build_fresh_legacy_database_move_plan(root, legacy, target)
    if plan is None:
        return

    _write_legacy_database_adoption_marker(marker, plan)
    _execute_legacy_database_move_plan(root, plan, marker)


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
