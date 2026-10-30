"""All-listings approvals remain independent for different sellers."""
import pytest
from alembic import command
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from test_seller_ownership import migration_config, migration_engine  # noqa: F401
from vendoo_studio.models.ebay_offer import EbayOfferPolicy
from vendoo_studio.services.hosted_database import SellerDatabaseSession

ALICE = "11111111-1111-4111-8111-111111111111"
BOB = "22222222-2222-4222-8222-222222222222"


def test_policy_account_keys_and_hosted_reads_are_scoped_to_the_seller(migration_engine):  # noqa: F811
    engine = migration_engine
    command.upgrade(migration_config(engine), "head")
    table = EbayOfferPolicy.__table__
    values = {"account_id": "same-ebay-account", "discount_percent": 10, "minimum_price_cents": 1, "enabled": True}
    for seller in (ALICE, BOB):
        with engine.begin() as connection:
            connection.execute(table.insert().values(**values, user_id=seller))
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(table.insert().values(**values, user_id=ALICE))
    if engine.dialect.name == "postgresql":
        for seller in (ALICE, BOB):
            with SellerDatabaseSession(bind=engine, user_id=seller) as db:
                policies = db.scalars(select(EbayOfferPolicy)).all()
                assert len(policies) == 1
                assert policies[0].user_id == seller
