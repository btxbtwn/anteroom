"""Account-scoped implementations used by the shared desktop/web services."""
from __future__ import annotations

import copy
import re
import tempfile
import threading
import uuid
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from vendoo_studio.models.user_preference import UserPreference
from vendoo_studio.models.user_secret import UserSecretReference
from vendoo_studio.services.hosted_database import SellerDatabaseSession, note_changes
from vendoo_studio.services.service_context import ServiceContext


class CredentialStorageUnavailable(RuntimeError):
    def __init__(self):
        super().__init__('Secure credential storage is temporarily unavailable')


class HostedSettingsStore:
    """One execution's settings snapshot; updates serialize against current data."""
    def __init__(self, sessions):
        self.sessions = sessions
        self._snapshot = None

    def read(self):
        if self._snapshot is None:
            with self.sessions() as db:
                value = db.scalar(select(UserPreference.value).where(UserPreference.key == 'settings'))
                self._snapshot = value if isinstance(value, dict) else {}
        return copy.deepcopy(self._snapshot)

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
        exists = db.scalar(select(UserSecretReference.id).where(UserSecretReference.account == account))
        return db.scalar(text('SELECT anteroom_private.secret_read(:slot)'), {'slot': account}) if exists else None

    def get(self, account):
        if account not in self._cache:
            try:
                with self.sessions() as db:
                    self._cache[account] = self._read(db, account)
            except SQLAlchemyError:
                raise CredentialStorageUnavailable() from None
        return self._cache[account]

    def set(self, account, value):
        self.update(account, lambda _previous: value)
        return True

    def delete(self, account):
        self.update(account, lambda _previous: None)

    def update(self, account, mutator):
        try:
            with self.sessions() as db:
                db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))'),
                           {'identity': f'anteroom-secret:{self.owner}:{account}'})
                value = mutator(self._read(db, account))
                db.execute(text('SELECT anteroom_private.secret_write(:slot, :value)'), {'slot': account, 'value': value})
                # The function writes outside the ORM; announce it like any settings change.
                note_changes(db, 'user_secret_references')
                db.commit()
                self._cache[account] = value
        except SQLAlchemyError:
            # SQLAlchemy exceptions may contain bound secret parameters. They
            # must never reach an API response, SSE error, or exception logger.
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
        from vendoo_studio.services.hosted_storage import checked_photo_key
        checked_photo_key(key, self.owner)
        parts = key.split('/')
        return self._root() / parts[1] / parts[2]

    def _call(self, method, key, data=None):
        if self._token is not None:
            from vendoo_studio.services.hosted_photos import storage_call
            return storage_call(method, key, self._token, data)
        from vendoo_studio.services.hosted_storage import photo_call
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

    def local_path(self, key):
        with self._lock:
            path = self._path(key)
            if not path.is_file():
                data = self._call('GET', key)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
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
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

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


_CATALOG_STATES: dict = {}
_CATALOG_STATES_LOCK = threading.Lock()


def catalog_state(owner):
    """The seller's catalog index state, kept across requests so the index is built once."""
    from vendoo_studio.services.catalog_index import CatalogIndexState
    with _CATALOG_STATES_LOCK:
        return _CATALOG_STATES.setdefault(owner, CatalogIndexState())


def for_seller(user_id, token=None, *, task_lease=None):
    from vendoo_studio import database
    owner = str(uuid.UUID(user_id))
    def sessions():
        if database.engine.dialect.name != 'postgresql':
            raise HTTPException(503, 'The cloud database is not configured')
        session = SellerDatabaseSession(user_id=owner, bind=database.engine)
        if task_lease is not None:
            session.info['background_lease'] = task_lease
        return session
    from vendoo_studio.services.hosted_provider import provider_for
    return ServiceContext(owner, sessions, HostedSettingsStore(sessions),
                          VaultSecretStore(sessions, owner), HostedPhotoStore(owner, token),
                          catalog=catalog_state(owner), provider_factory=provider_for)
