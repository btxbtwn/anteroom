"""Exercise the real ownership/privilege boundary with an encrypted Vault stand-in.

CI's Postgres has pgcrypto, not Supabase Vault. The stand-in has Vault's called
SQL signatures and encrypted rows, so the migration's private functions and
restricted application role are tested without storing real credentials.
"""
import concurrent.futures
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from test_seller_ownership import migration_engine  # noqa: F401
from vendoo_studio.models.user_secret import UserSecretReference
from vendoo_studio.services import schema_migrations
from vendoo_studio.services.hosted_context import CredentialStorageUnavailable, HostedSettingsStore, VaultSecretStore
from vendoo_studio.services.hosted_database import SellerDatabaseSession

ALICE, BOB = str(uuid.uuid4()), str(uuid.uuid4())


@pytest.fixture
def storage(migration_engine):  # noqa: F811
    engine = migration_engine
    if engine.dialect.name != 'postgresql':
        pytest.skip('Hosted storage requires Postgres')
    schema_migrations.ensure_schema(engine)
    def sessions(owner):
        return lambda: SellerDatabaseSession(user_id=owner, bind=engine)
    return engine, sessions


@pytest.fixture
def vault_storage(storage):
    engine, _sessions = storage
    with engine.connect() as db:
        available = db.scalar(text("SELECT 1 FROM pg_available_extensions WHERE name='pgcrypto'"))
    if not available:
        pytest.skip('Bundled local Postgres lacks pgcrypto; CI Postgres includes it')
    with engine.begin() as db:
        db.execute(text('CREATE EXTENSION IF NOT EXISTS pgcrypto'))
        db.execute(text("""
            CREATE SCHEMA vault;
            CREATE TABLE vault.secrets (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), name text UNIQUE, secret bytea);
            CREATE VIEW vault.decrypted_secrets AS
                SELECT id, name, public.pgp_sym_decrypt(secret, 'fixture-cipher-key') AS decrypted_secret FROM vault.secrets;
            CREATE FUNCTION vault.create_secret(value text, name text, description text) RETURNS uuid
                LANGUAGE sql SET search_path='' AS $$
                INSERT INTO vault.secrets(name, secret) VALUES(name, public.pgp_sym_encrypt(value, 'fixture-cipher-key')) RETURNING id $$;
            CREATE FUNCTION vault.update_secret(identity uuid, value text, new_name text, description text) RETURNS void
                LANGUAGE sql SET search_path='' AS $$
                UPDATE vault.secrets SET secret=public.pgp_sym_encrypt(value, 'fixture-cipher-key'), name=new_name WHERE id=identity $$;
        """))
    return storage


def test_vault_credentials_are_owned_encrypted_and_never_directly_readable(vault_storage):
    engine, sessions = vault_storage
    alice = VaultSecretStore(sessions(ALICE), ALICE)
    bob = VaultSecretStore(sessions(BOB), BOB)
    alice.set('ai-api-keys', 'fixture-alice-credential')
    bob.set('ai-api-keys', 'fixture-bob-credential')
    assert alice.get('ai-api-keys') == 'fixture-alice-credential'
    assert bob.get('ai-api-keys') == 'fixture-bob-credential'
    with engine.connect() as db:
        encrypted = db.execute(text('SELECT secret FROM vault.secrets')).scalars().all()
        assert len(encrypted) == 2
        assert all(b'fixture-alice-credential' not in bytes(value) for value in encrypted)
        assert all(b'fixture-bob-credential' not in bytes(value) for value in encrypted)
    for statement in ('SELECT * FROM vault.decrypted_secrets', 'SELECT * FROM vault.secrets',
                      "UPDATE user_secret_references SET account='stolen'"):
        with sessions(ALICE)() as db, pytest.raises(DBAPIError):
            db.execute(text(statement)).all()
    # Even a broad permissive policy cannot defeat the restrictive owner rule.
    with engine.begin() as db:
        db.execute(text('CREATE POLICY broad_test ON user_secret_references FOR ALL TO PUBLIC USING (true) WITH CHECK (true)'))
    with sessions(ALICE)() as db:
        assert {row.user_id for row in db.scalars(select(UserSecretReference))} == {ALICE}
    alice.delete('ai-api-keys')
    assert VaultSecretStore(sessions(ALICE), ALICE).get('ai-api-keys') is None
    assert VaultSecretStore(sessions(BOB), BOB).get('ai-api-keys') == 'fixture-bob-credential'


def test_corrupt_soft_reference_cannot_read_overwrite_or_delete_another_secret(vault_storage):
    engine, sessions = vault_storage
    VaultSecretStore(sessions(ALICE), ALICE).set('slot', 'fixture-alice')
    VaultSecretStore(sessions(BOB), BOB).set('slot', 'fixture-bob')
    with Session(engine) as db:
        alice = db.scalar(select(UserSecretReference).where(UserSecretReference.user_id == ALICE))
        bob = db.scalar(select(UserSecretReference).where(UserSecretReference.user_id == BOB))
        alice.secret_id = bob.secret_id
        db.commit()
    alice = VaultSecretStore(sessions(ALICE), ALICE)
    assert alice.get('slot') is None
    with pytest.raises(CredentialStorageUnavailable) as error:
        alice.set('slot', 'fixture-rejected-value')
    assert 'fixture-rejected-value' not in str(error.value)
    alice.delete('slot')
    assert VaultSecretStore(sessions(BOB), BOB).get('slot') == 'fixture-bob'


def test_concurrent_settings_updates_keep_both_saves_and_separate_owners(storage):
    _engine, sessions = storage
    def update(key):
        HostedSettingsStore(sessions(ALICE)).update(lambda value: value.update({key: key}))
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(update, ['theme', 'models']))
    assert HostedSettingsStore(sessions(ALICE)).read() == {'theme': 'theme', 'models': 'models'}
    assert HostedSettingsStore(sessions(BOB)).read() == {}
