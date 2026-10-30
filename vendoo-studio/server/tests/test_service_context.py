"""Shared services must keep the caller's storage across async/thread boundaries."""
import asyncio
import copy
from unittest.mock import patch

import pytest

from vendoo_studio.database import SessionLocal
from vendoo_studio.services import keychain, photo_store, service_context, user_settings


class MemorySettings:
    def __init__(self): self.value = {}
    def read(self): return copy.deepcopy(self.value)
    def write(self, value): self.value = copy.deepcopy(value)
    def update(self, mutator):
        value = self.read()
        mutator(value)
        self.write(value)
        return value


class MemorySecrets:
    def __init__(self): self.value = {}
    def get(self, account): return self.value.get(account)
    def set(self, account, value):
        self.value[account] = value
        return True
    def delete(self, account): self.value.pop(account, None)
    def update(self, account, mutator):
        value = mutator(self.value.get(account))
        if value is None:
            self.delete(account)
        else:
            self.set(account, value)


def runtime(owner):
    from vendoo_studio.services.catalog_index import CatalogIndexState
    return service_context.ServiceContext(owner, lambda: owner, MemorySettings(), MemorySecrets(), object(),
                                          catalog=CatalogIndexState())


def test_storage_boundaries_remain_separate_across_tasks_threads_and_nested_execution():
    alice, bob = runtime('alice'), runtime('bob')
    async def read_owner(context):
        with service_context.use(context):
            keychain.set_api_key(context.seller_id)
            user_settings.set_package_dimensions(length=10, width=5, height=2)
            assert photo_store.photo_store() is context.photos
            async def child():
                await asyncio.sleep(0)
                return SessionLocal(), keychain.get_api_key()
            assert await asyncio.create_task(child()) == (context.seller_id, context.seller_id)
            assert await asyncio.to_thread(keychain.get_api_key) == context.seller_id
            with service_context.use(bob if context is alice else alice):
                assert service_context.current() is not context
            assert service_context.current() is context
    async def together():
        await asyncio.gather(read_owner(alice), read_owner(bob))
    with patch.object(keychain, '_keyring', side_effect=AssertionError('Never touch the Mac Keychain')), patch.object(
            user_settings, 'settings_path', side_effect=AssertionError('Never touch shared settings')):
        asyncio.run(together())
    assert service_context.current() is None
    assert alice.secrets.value != bob.secrets.value


def test_context_is_restored_after_a_failure():
    alice = runtime('alice')
    with pytest.raises(ValueError), service_context.use(alice):
        raise ValueError('cancelled')
    assert service_context.current() is None


@pytest.mark.parametrize('operation', [SessionLocal, user_settings.read_settings, keychain.get_api_key, photo_store.photo_store])
def test_hosted_execution_without_owner_fails_before_local_storage(monkeypatch, operation):
    monkeypatch.setenv('ANTEROOM_HOSTED', '1')
    with pytest.raises(RuntimeError, match='verified seller'):
        operation()


def test_catalog_state_files_and_rebuild_threads_keep_their_execution_owner(monkeypatch, tmp_path):
    from contextlib import nullcontext
    from dataclasses import replace
    from threading import Event
    from vendoo_studio.services import catalog_index as catalog

    alice = runtime('a7a5ee16-9850-49ba-b5bd-83b8be54df46')
    bob = runtime('b7a5ee16-9850-49ba-b5bd-83b8be54df46')
    monkeypatch.setattr(catalog, 'user_data_root', lambda: tmp_path)
    finished, rebuilt = Event(), []

    def rebuild(session):
        rebuilt.append((session, service_context.current().seller_id, catalog.catalog_docs_dir()))
        catalog._state().index = 'alice-observed-schema'
        finished.set()

    monkeypatch.setattr(catalog, 'rebuild_catalog_index', rebuild)
    with service_context.use(replace(alice, session_factory=lambda: nullcontext('alice-session'))):
        alice_path = catalog.catalog_docs_dir()
        catalog._kick_rebuild()
        assert finished.wait(2)
        assert catalog._state().index == 'alice-observed-schema'
    with service_context.use(bob):
        assert catalog._state().index is None
        assert catalog.catalog_docs_dir() != alice_path
        assert bob.seller_id in str(catalog.catalog_docs_dir())
    assert rebuilt == [('alice-session', alice.seller_id, alice_path)]
