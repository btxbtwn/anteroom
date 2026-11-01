"""Move this Mac's listings, photos, settings and keys into the seller's account.

Runs on the Mac, which holds the database, the photo files and the Keychain.
It signs in to the account with the seller's email and password, sends the
records table by table to the hosted import routes, then the photos, settings
and keys. Every step can run again: the account keeps what it already has, so
an interrupted move resumes instead of duplicating anything. The local data is
left in place as a backup; only the Mac's automations pause, so price drops
and scheduled edits run once, from the account.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from vendoo_studio.config import DATA_DIR, HOSTED_URL

BATCH_ROWS = 200
BATCH_BYTES = 2 * 1024 * 1024
MARKER = Path(DATA_DIR) / "account-move.json"

_lock = threading.Lock()
_progress: dict = {"state": "idle"}


class MoveError(Exception):
    """A failure the seller can act on; the message is shown as is."""


SIGN_IN_ERRORS = {
    "invalid_credentials": "That email and password don’t match. Try again, or reset your password on the web.",
    "email_not_confirmed": "Confirm your email first, using the link we sent you.",
    "over_request_rate_limit": "Too many attempts. Wait a minute and try again.",
}


def _json_value(value):
    if isinstance(value, datetime):
        return (value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def serialize(row) -> dict:
    return {name: _json_value(value) for name, value in row._mapping.items()}


def batches(connection, table):
    """Rows of one table as JSON-ready dicts, in batches bounded by count and size."""
    order = list(table.primary_key.columns)
    batch, size = [], 0
    for row in connection.execute(select(table).order_by(*order)):
        record = serialize(row)
        weight = len(json.dumps(record, default=str))
        if batch and (len(batch) >= BATCH_ROWS or size + weight > BATCH_BYTES):
            yield batch
            batch, size = [], 0
        batch.append(record)
        size += weight
    if batch:
        yield batch


class Account:
    """An authenticated client for the hosted API, refreshing its session as needed."""

    def __init__(self, base_url: str, client: httpx.Client | None = None):
        self.base_url = base_url
        self.http = client or httpx.Client(timeout=60, follow_redirects=False)
        self.config: dict = {}
        self.session: dict = {}

    def sign_in(self, email: str, password: str) -> dict:
        try:
            response = self.http.get(f"{self.base_url}/api/auth-config")
        except Exception:  # noqa: BLE001 - offline, DNS, or the sandbox's outbound block
            raise MoveError("Could not reach your account. Check your connection and try again.") from None
        if response.status_code != 200:
            raise MoveError("Could not reach your account. Check your connection and try again.")
        self.config = response.json()
        if not self.config.get("supabase_url") or not self.config.get("publishable_key"):
            raise MoveError("Your account is not ready for sign-in yet. Try again later.")
        self._token({"email": email, "password": password}, "password")
        return self.session["user"]

    def _token(self, body: dict, grant: str) -> None:
        try:
            response = self.http.post(f"{self.config['supabase_url'].rstrip('/')}/auth/v1/token",
                                      params={"grant_type": grant}, json=body,
                                      headers={"apikey": self.config["publishable_key"]})
        except Exception:  # noqa: BLE001 - offline, DNS, or the sandbox's outbound block
            raise MoveError("Could not reach sign-in. Check your connection and try again.") from None
        if response.status_code != 200:
            try:
                code = response.json().get("error_code") or response.json().get("code")
            except ValueError:
                code = None
            raise MoveError(SIGN_IN_ERRORS.get(str(code), "Could not sign in. Please try again."))
        payload = response.json()
        self.session = {"access_token": payload["access_token"], "refresh_token": payload["refresh_token"],
                        "expires_at": time.time() + int(payload.get("expires_in", 3600)),
                        "user": {"id": payload["user"]["id"], "email": payload["user"].get("email")}}

    def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        if self.session["expires_at"] - time.time() < 120:
            self._token({"refresh_token": self.session["refresh_token"]}, "refresh_token")
        headers = {"Authorization": f"Bearer {self.session['access_token']}", **kwargs.pop("headers", {})}
        for attempt in range(3):
            try:
                response = self.http.request(method, f"{self.base_url}{path}", headers=headers, **kwargs)
            except httpx.HTTPError:
                if attempt == 2:
                    raise MoveError("Lost the connection to your account. Run the move again to pick up where it stopped.") from None
                time.sleep(2 ** attempt)
                continue
            if response.status_code in {502, 503, 504} and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            if response.status_code >= 400:
                try:
                    detail = response.json().get("detail")
                except ValueError:
                    detail = None
                raise MoveError(detail if isinstance(detail, str) else "Your account could not take this step. Run the move again to retry.")
            return response
        raise AssertionError("unreachable")


def _source():
    from vendoo_studio.database import engine
    return engine


def local_summary() -> dict:
    from vendoo_studio.models.conversation import Conversation, Photo
    with Session(_source()) as db:
        return {"listings": db.scalar(select(func.count()).select_from(Conversation)) or 0,
                "photos": db.scalar(select(func.count()).select_from(Photo)) or 0}


def moved() -> dict | None:
    try:
        marker = json.loads(MARKER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return marker if isinstance(marker, dict) else None


def status() -> dict:
    with _lock:
        progress = dict(_progress)
    return {"hosted_url": HOSTED_URL, "moved": moved(), **local_summary(), "progress": progress}


def _set(**values) -> None:
    with _lock:
        _progress.update(values)


def start(email: str, password: str, account: Account | None = None) -> bool:
    """Begin a move in the background; False when one is already running."""
    with _lock:
        if _progress.get("state") == "running":
            return False
        _progress.clear()
        _progress.update({"state": "running", "step": "Signing in…", "done": 0, "total": 0})
    worker = threading.Thread(target=_run, args=(email, password, account), daemon=True, name="account-move")
    worker.start()
    return True


def _run(email: str, password: str, account: Account | None) -> None:
    try:
        result = move(email, password, account or Account(HOSTED_URL))
    except MoveError as error:
        _set(state="failed", error=str(error))
    except Exception as error:  # noqa: BLE001 - shown as a retry prompt, logged for diagnosis
        print(f"account move failed: {error!r}", flush=True)
        _set(state="failed", error="The move stopped unexpectedly. Run it again to pick up where it stopped.")
    else:
        _set(state="done", step="Done", result=result)


def move(email: str, password: str, account: Account, source=None, photos=None) -> dict:
    from vendoo_studio.database import Base, load_models
    from vendoo_studio.services.account_import import SECRET_SLOTS
    from vendoo_studio.services.user_settings import read_settings

    user = account.sign_in(email, password)
    tables = account.request("GET", "/api/account-import").json()["tables"]
    load_models()
    local = {table.name: table for table in Base.metadata.sorted_tables if "user_id" in table.c}
    names = [name for name in tables if name in local]
    source = source or _source()
    with source.connect() as connection:
        total = sum(connection.scalar(select(func.count()).select_from(local[name])) or 0 for name in names)
        _set(step="Copying listings and history…", done=0, total=total)
        copied = 0
        for name in names:
            for batch in batches(connection, local[name]):
                account.request("POST", "/api/account-import/rows", json={"table": name, "rows": batch})
                copied += len(batch)
                _set(done=copied)
    from vendoo_studio.services.photo_store import photo_store
    photos = _move_photos(account, source, photos or photo_store())
    _set(step="Copying settings and keys…")
    settings = account.request("PUT", "/api/account-import/settings", json=read_settings()).json()["added"]
    keys = local_secrets(SECRET_SLOTS)
    saved = account.request("PUT", "/api/account-import/secrets", json=keys).json()["saved"] if keys else []
    paused = _pause_local_automations(source)
    result = {"records": copied, "photos": photos["copied"], "missing_photos": photos["missing"],
              "settings": len(settings), "keys": saved, "paused_automations": paused}
    MARKER.parent.mkdir(parents=True, exist_ok=True)
    MARKER.write_text(json.dumps({"user_id": user["id"], "email": user.get("email"),
                                  "moved_at": datetime.now(UTC).isoformat(), **result}), encoding="utf-8")
    return result


def local_secrets(slots) -> dict:
    """This Mac's provider keys and sign-ins, read from its Keychain."""
    from vendoo_studio.services import keychain
    return {slot: value for slot in sorted(slots) if (value := keychain.read_slot(slot))}


def _move_photos(account: Account, source, store) -> dict:
    from vendoo_studio.models.conversation import Photo
    pending = account.request("GET", "/api/account-import/photos").json()
    work = [(item["id"], field) for item in pending for field in item["fields"]]
    _set(step="Copying photos…", done=0, total=len(work))
    copied = missing = 0
    with Session(source) as db:
        for done, (photo_id, field) in enumerate(work, start=1):
            photo = db.get(Photo, str(photo_id))
            key = None if photo is None else (photo.stored_filename if field == "stored" else photo.source_filename)
            if not key or not store.exists(key):
                missing += 1
            else:
                account.request("POST", f"/api/account-import/photos/{photo_id}/{field}", content=store.read(key),
                                headers={"Content-Type": "application/octet-stream"})
                copied += 1
            _set(done=done)
    return {"copied": copied, "missing": missing}


def _pause_local_automations(source) -> int:
    """The account runs them now; running them here too would apply each change twice."""
    from vendoo_studio.models.automation import Automation
    from vendoo_studio.models.promotion_automation import PromotionAutomation
    with Session(source) as db:
        active = db.scalars(select(Automation).where(Automation.status == "active")).all()
        promotions = db.scalars(select(PromotionAutomation).where(PromotionAutomation.enabled.is_(True))).all()
        for automation in active:
            automation.status = "paused"
        for promotion in promotions:
            promotion.enabled = False
        db.commit()
        return len(active) + len(promotions)
