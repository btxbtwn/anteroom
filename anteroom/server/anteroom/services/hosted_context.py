"""Account-scoped implementations used by the shared desktop/web services."""
from __future__ import annotations

import copy
import logging
import re
import tempfile
import threading
import uuid
from collections import OrderedDict
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError, SQLAlchemyError, TimeoutError as PoolTimeout

from anteroom.models.user_preference import UserPreference
from anteroom.services.hosted_database import SellerDatabaseSession, active_seller_session, note_changes
from anteroom.services.service_context import ServiceContext, current


_log = logging.getLogger(__name__)
# Only fixed codes are emitted; even a driver's sqlstate can be arbitrary text.
_STORAGE_SQLSTATES = frozenset({'08003', '08006', '23503', '23505', '40001', '40P01',
                               '42501', '53300', '55000', '55P03', '57014', '57P01'})


def _storage_failure(operation: str, stage: str, exc: SQLAlchemyError) -> None:
    """Report classification only: raw Vault exceptions can carry bound secrets."""
    family = next((label for kind, label in (
        (PoolTimeout, 'pool_timeout'), (IntegrityError, 'integrity'),
        (OperationalError, 'operational'), (DBAPIError, 'database'),
    ) if isinstance(exc, kind)), 'sqlalchemy')
    sqlstate = 'unknown'
    if isinstance(exc, DBAPIError):
        try:
            candidate = getattr(exc.orig, 'sqlstate', None)
            if type(candidate) is str and candidate in _STORAGE_SQLSTATES:
                sqlstate = candidate
        except Exception:  # noqa: BLE001 - diagnostic metadata must not replace the safe storage failure
            pass
    # Never attach exc_info, the exception object, its chain, or arbitrary names.
    _log.error('Credential storage failed (operation=%s; stage=%s; family=%s; sqlstate=%s)',
               operation, stage, family, sqlstate)


class CredentialStorageUnavailable(RuntimeError):
    def __init__(self):
        super().__init__('Secure credential storage is temporarily unavailable')


class HostedSettingsStore:
    """One execution's settings snapshot; updates serialize against current data."""
    def __init__(self, sessions, owner: str | None = None):
        self.sessions = sessions
        self._owner = owner
        self._snapshot = None

    def _reusable_session(self) -> SellerDatabaseSession | None:
        owner = self._owner
        if owner is None:
            try:
                context = current()
            except RuntimeError:
                context = None
            if context is None or context.settings is not self:
                return None
            owner = context.seller_id
        return active_seller_session(owner)

    def read(self) -> dict:
        snapshot = self._snapshot
        if snapshot is None:
            # Reuse the caller's open seller session when one is active: avoids
            # both an eager settings query on routes that never read settings
            # and a nested pooled-connection checkout that deadlocks under load.
            active = self._reusable_session()
            if active is not None and active.in_transaction():
                with active.no_autoflush:
                    snapshot = self.load(active)
            else:
                with self.sessions() as db:
                    snapshot = self.load(db)
        return copy.deepcopy(snapshot)

    def load(self, db) -> dict:
        """Take the snapshot through a session the caller already holds, unless it is taken."""
        if self._snapshot is None:
            self._snapshot = self._read_saved(db)
        return self._snapshot

    @staticmethod
    def _read_saved(db) -> dict:
        value = db.scalar(select(UserPreference.value).where(UserPreference.key == 'settings'))
        return value if isinstance(value, dict) else {}

    def read_fresh(self) -> dict:
        """Read current seller authorization without altering this execution's snapshot."""
        active = self._reusable_session()
        if active is not None and active.in_transaction():
            with active.no_autoflush:
                return self._read_saved(active)
        with self.sessions() as db:
            return self._read_saved(db)

    def write(self, payload):
        def replace(current):
            current.clear()
            current.update(copy.deepcopy(payload))
        self.update(replace)

    def update(self, mutator):
        with self.sessions() as db:
            # INSERT also covers simultaneous first saves. The following row
            # lock makes read/modify/write atomic across workers and devices.
            db.execute(insert(UserPreference).values(user_id=db.user_id, key='settings', value={})
                       .on_conflict_do_nothing(index_elements=['user_id', 'key']))
            row = db.scalar(select(UserPreference).where(UserPreference.key == 'settings').with_for_update())
            payload = copy.deepcopy(row.value)
            mutator(payload)
            row.value = payload
            db.commit()
            self._snapshot = copy.deepcopy(payload)
            return copy.deepcopy(payload)


class VaultSecretStore:
    """The restricted runtime never receives grants on Vault tables or views."""
    def __init__(self, sessions, owner):
        self.sessions, self.owner = sessions, owner
        self._cache = {}

    @staticmethod
    def _read(db, account):
        # One round trip. Vault is reached only for a slot that has a secret,
        # so a database without Vault still works while none is saved.
        return db.scalar(text(
            'SELECT CASE WHEN EXISTS (SELECT 1 FROM user_secret_references WHERE account = :slot)'
            ' THEN anteroom_private.secret_read(:slot) END'), {'slot': account})

    def get(self, account):
        if account not in self._cache:
            return self.get_fresh(account)
        return self._cache[account]

    def get_fresh(self, account):
        """Read directly from Vault, bypassing the per-request cache."""
        stage = 'session'
        try:
            with self.sessions() as db:
                stage = 'read'
                value = self._read(db, account)
                stage = 'close'
        except SQLAlchemyError as exc:
            _storage_failure('get', stage, exc)
            raise CredentialStorageUnavailable() from None
        self._cache[account] = value
        return value

    def set(self, account, value):
        self.update(account, lambda _previous: value)
        return True

    def delete(self, account):
        self.update(account, lambda _previous: None)

    def write_in_session(self, db: SellerDatabaseSession, account: str, value: str | None) -> None:
        """Stage a Vault write in the same transaction as related seller records."""
        stage = 'write'
        try:
            db.execute(text('SELECT anteroom_private.secret_write(:slot, :value)'), {'slot': account, 'value': value})
            stage = 'change'
            note_changes(db, 'user_secret_references')
        except SQLAlchemyError as exc:
            _storage_failure('update', stage, exc)
            raise CredentialStorageUnavailable() from None
        self._cache.pop(account, None)

    def update(self, account, mutator):
        stage = 'session'
        try:
            with self.sessions() as db:
                stage = 'lock'
                db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))'),
                           {'identity': f'anteroom-secret:{self.owner}:{account}'})
                stage = 'read'
                previous = self._read(db, account)
                stage = 'mutate'
                value = mutator(previous)
                if value == previous:
                    # Nothing changed: no write, and no change for other devices to re-read.
                    self._cache[account] = value
                    stage = 'close'
                    return
                stage = 'write'
                self.write_in_session(db, account, value)
                stage = 'commit'
                db.commit()
                self._cache[account] = value
                stage = 'close'
        except SQLAlchemyError as exc:
            # SQLAlchemy exceptions may contain bound secret parameters. They
            # must never reach an API response, SSE error, or exception logger.
            _storage_failure('update', stage, exc)
            raise CredentialStorageUnavailable() from None


class HostedPhotoStore:
    """Private Storage objects, materialized in an execution's private temp dir."""
    def __init__(self, owner, token):
        self.owner, self._token = owner, token
        self._temporary = None
        self._lock = threading.RLock()

    def _root(self):
        if self._temporary is None:
            self._temporary = tempfile.TemporaryDirectory(prefix='anteroom-photos-')
        return Path(self._temporary.name)

    def _path(self, key):
        from anteroom.services.hosted_storage import checked_photo_key
        checked_photo_key(key, self.owner)
        parts = key.split('/')
        return self._root() / parts[1] / parts[2]

    def _call(self, method, key, data=None):
        if self._token is not None:
            from anteroom.services.hosted_photos import storage_call
            return storage_call(method, key, self._token, data)
        from anteroom.services.hosted_storage import photo_call
        return photo_call(method, key, data)

    def edited_key(self, source):
        self._path(source)
        return source.rsplit('/', 1)[0] + '/' + uuid.uuid4().hex + '.jpg'

    def new_key(self, collection_id, suffix):
        if suffix != '.jpg':
            raise ValueError('Hosted photos must be JPEG images')
        key = f'{self.owner}/{collection_id}/{uuid.uuid4().hex}.jpg'
        self._path(key)
        return key

    def _write(self, path, data):
        # Network calls stay outside the lock, so photos load in parallel; each file appears whole.
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            part = path.with_name(f'{path.name}.{uuid.uuid4().hex}.part')
            part.write_bytes(data)
            part.replace(path)

    def local_path(self, key):
        with self._lock:
            path = self._path(key)
            if path.is_file():
                return path
        self._write(path, self._call('GET', key))
        return path

    def local_paths(self, keys):
        # A missing/foreign object fails the operation; it cannot quietly make
        # a vision reading from a different set of photographs.
        return [self.local_path(key) for key in keys]

    def read(self, key):
        return self.local_path(key).read_bytes()

    def exists(self, key):
        try:
            self.local_path(key)
            return True
        except HTTPException as error:
            if error.status_code == 404:
                return False
            raise

    def put(self, key, data):
        with self._lock:
            path = self._path(key)
        self._call('POST', key, data)
        self._write(path, data)

    def delete(self, key):
        with self._lock:
            path = self._path(key)
            self._call('DELETE', key)
            path.unlink(missing_ok=True)

    def copy(self, source, target):
        self.put(target, self.read(source))

    def cache_dir(self, name):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', name):
            raise ValueError('Invalid derived photo cache name')
        with self._lock:
            path = self._root() / 'derived' / name
            path.mkdir(parents=True, exist_ok=True)
            return path


# Recently active sellers' catalog indexes, least recent first. A dropped
# seller's index is rebuilt from their files on next use; a request already
# holding the old state keeps using it.
CATALOG_STATES_KEPT = 256
_CATALOG_STATES: OrderedDict = OrderedDict()
_CATALOG_STATES_LOCK = threading.Lock()


def catalog_state(owner):
    """The seller's catalog index state, kept across requests so the index is built once."""
    from anteroom.services.catalog_index import CatalogIndexState
    with _CATALOG_STATES_LOCK:
        state = _CATALOG_STATES.pop(owner, None)
        if state is None:
            state = CatalogIndexState()
        _CATALOG_STATES[owner] = state
        while len(_CATALOG_STATES) > CATALOG_STATES_KEPT:
            _CATALOG_STATES.popitem(last=False)
        return state


def forget_catalog_state(owner):
    """Drop a deleted account's index state from this process."""
    with _CATALOG_STATES_LOCK:
        _CATALOG_STATES.pop(owner, None)


def for_seller(user_id, token=None, *, task_lease=None):
    from anteroom import database
    owner = str(uuid.UUID(user_id))
    def sessions():
        if database.engine.dialect.name != 'postgresql':
            raise HTTPException(503, 'The cloud database is not configured')
        session = SellerDatabaseSession(user_id=owner, bind=database.engine)
        if task_lease is not None:
            session.info['background_lease'] = task_lease
        return session
    from anteroom.services.hosted_provider import provider_for
    return ServiceContext(owner, sessions, HostedSettingsStore(sessions, owner),
                          VaultSecretStore(sessions, owner), HostedPhotoStore(owner, token),
                          catalog=catalog_state(owner), provider_factory=provider_for)
