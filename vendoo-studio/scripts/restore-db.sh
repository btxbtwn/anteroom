#!/bin/bash
# Put a snapshot back in place of the working database.
#
#   ./scripts/check-db-schema.sh                    # list what you have first
#   ./scripts/restore-db.sh <snapshot.db>           # asks before replacing
#   ./scripts/restore-db.sh <snapshot.db> --yes     # no prompt
#
# Quit Anteroom first. The current database is snapshotted before it is
# replaced, so a restore onto the wrong copy is itself reversible.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=lib.sh
source "$ROOT/scripts/lib.sh"

SNAPSHOT="${1:-}"
CONFIRM="${2:-}"

if [[ -z "$SNAPSHOT" ]]; then
  echo "Usage: $0 <snapshot.db> [--yes]" >&2
  exit 64
fi
if [[ ! -f "$SNAPSHOT" ]]; then
  echo "No such snapshot: $SNAPSHOT" >&2
  exit 66
fi

PYTHON="$(find_python)" || { echo "Python 3.12+ not found. Run ./scripts/setup.sh first." >&2; exit 1; }
DATABASE="$(PYTHONPATH="$ROOT/server" "$PYTHON" -c 'from vendoo_studio.config import DATABASE_PATH; print(DATABASE_PATH)')"

# A running Studio holds writes in the -wal beside the file it has open.
# Replacing that file underneath it mixes two databases together.
PORT="$(PYTHONPATH="$ROOT/server" "$PYTHON" -c 'from vendoo_studio.config import PORT; print(PORT)')"
if curl -fsS --max-time 2 "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
  echo "Anteroom is running on port ${PORT}. Quit it first, then run this again." >&2
  exit 69
fi

# A fresh file cannot be a leftover symlink or hardlink to the current database.
STAGED="$(mktemp "${DATABASE}.restoring.XXXXXX")"
trap 'rm -f "$STAGED"' EXIT
echo "Checking $SNAPSHOT ..."
PYTHONPATH="$ROOT/server" "$PYTHON" - "$SNAPSHOT" "$STAGED" <<'PY'
import gzip
import sqlite3
import sys
import zlib
from pathlib import Path

from vendoo_studio.services.backups import database_revision, decompress_snapshot

snapshot, staged = Path(sys.argv[1]), Path(sys.argv[2])
# Unpack before checking: a snapshot is gzipped, and a gzip of a damaged
# database unpacks perfectly well. The check has to run on the database.
try:
    decompress_snapshot(snapshot, staged)
    connection = sqlite3.connect(f"file:{staged}?mode=ro", uri=True)
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()
    finally:
        connection.close()
    if not result or result[0] != "ok":
        raise sqlite3.DatabaseError(result[0] if result else "unreadable database")
    # integrity_check passes an empty file or any other program's database.
    # Anteroom stamps every database it writes with its schema revision.
    if database_revision(staged) is None:
        raise sqlite3.DatabaseError("it is not an Anteroom database")
except (OSError, EOFError, sqlite3.DatabaseError, zlib.error) as exc:
    try:
        staged.unlink(missing_ok=True)
    except OSError:
        print("The staging file could not be removed. Check its folder permissions.", file=sys.stderr)
    damaged = isinstance(exc, (gzip.BadGzipFile, EOFError, zlib.error)) or (
        isinstance(exc, sqlite3.DatabaseError) and not isinstance(exc, sqlite3.OperationalError))
    recovery = "Choose another snapshot" if damaged else "Check file permissions, the snapshot path and free disk space, then retry"
    print(f"Cannot restore this snapshot: {exc}. {recovery}; the current database was not changed.", file=sys.stderr)
    raise SystemExit(65 if damaged else 74) from None
print("  integrity_check ok, Anteroom schema found")
PY

echo
echo "  restore : $SNAPSHOT"
echo "  over    : $DATABASE"
echo
if [[ "$CONFIRM" != "--yes" ]]; then
  read -r -p "Replace the current database? [y/N] " answer
  [[ "$answer" == "y" || "$answer" == "Y" ]] || { rm -f "$STAGED"; echo "Left alone."; exit 0; }
fi

if [[ -f "$DATABASE" ]]; then
  echo "Snapshotting the current database first ..."
  PYTHONPATH="$ROOT/server" "$PYTHON" -c "
from vendoo_studio.services.backups import take_snapshot
print('  kept as', take_snapshot('pre-restore').path)
"
fi

# The -wal and -shm belong to the database being replaced. Left behind, SQLite
# would replay them onto the restored file and mix the two together.
rm -f "${DATABASE}-wal" "${DATABASE}-shm"
mv "$STAGED" "$DATABASE"

echo "Restored. Start Anteroom and check your listings."
