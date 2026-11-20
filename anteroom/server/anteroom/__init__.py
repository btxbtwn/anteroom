from pathlib import Path

import truststore
from pillow_heif import register_heif_opener

# The packaged app's Python has no CA bundle of its own, so stdlib HTTPS (the
# box-scout crawl, for one) failed certificate checks. Verify against the
# system trust store (the macOS Keychain) instead. This has to run before the
# first urlopen: urllib builds its shared opener, HTTPS context included, on
# that first call and keeps it, and the desktop launcher's health check makes
# that call before the server is imported.
truststore.inject_into_ssl()

# Photos dragged from Finder or the Files app arrive as the iPhone's HEIC.
# Pillow cannot open those by itself, so thumbnails fell back to the original
# and the vision encoder sent HEIC bytes labelled as JPEG, which no model reads.
register_heif_opener()

_PACKAGE_ROOT = f"{Path(__file__).resolve().parent.as_posix().rstrip('/')}/"
_NON_PACKAGE_PREFIXES = (".", "server/", "tests/", "scripts/", "skills/", "venv/", "node_modules/")


def app_module_path(filename: str) -> str | None:
    """Return a frame's path relative to the anteroom Python package, or None.

    The repo and project directories are also named ``anteroom``, so checking
    for ``/anteroom/`` alone mistakes ``anteroom/.venv/.../site-packages/...``
    and ``anteroom/server/tests/...`` for package code.
    """
    normalized = filename.replace("\\", "/")
    if normalized.startswith(_PACKAGE_ROOT):
        return normalized[len(_PACKAGE_ROOT):]
    if "/anteroom/" not in normalized:
        return None
    relative = normalized.rsplit("/anteroom/", 1)[1]
    if not relative or relative.startswith(_NON_PACKAGE_PREFIXES):
        return None
    if "/site-packages/" in relative or "/dist-packages/" in relative or "/lib/python" in relative:
        return None
    return relative
