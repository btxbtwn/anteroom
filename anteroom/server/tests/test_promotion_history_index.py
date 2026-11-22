"""The waiting lookup can seek its time range without sorting a rule's journal."""
import asyncio
from datetime import datetime, timedelta
from itertools import product
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from test_seller_ownership import migration_config, migration_engine  # noqa: F401
from anteroom.database import Base
from anteroom.models.listing_promotion import ListingPromotion
from anteroom.models.promotion_automation import PromotionAutomation
from anteroom.services.promotion_automations import _waiting_for_likes
from anteroom.services import promotion_automations


def test_upgrade_indexes_recent_history_without_changing_waiting_results(migration_engine):  # noqa: F811
    engine = migration_engine
    config = migration_config(engine)
    command.upgrade(config, "d7be65e14c2f")
    now = datetime(2026, 11, 15, 23, 30)
    with Session(engine) as db:
        rule = PromotionAutomation(id="history-rule", name="Offers", marketplace="poshmark", action="offer", min_likes=2)
        # Seed the historical schema without issuing inserts against newer ORM columns.
        metadata = sa.MetaData()
        rules = sa.Table("promotion_automations", metadata, autoload_with=db.connection())
        history = sa.Table("listing_promotions", metadata, autoload_with=db.connection())
        db.execute(rules.insert().values(id=rule.id, name=rule.name, marketplace=rule.marketplace,
                                         action=rule.action, min_likes=rule.min_likes, enabled=False, scope="all",
                                         conversation_ids=[], excluded_ids=[], min_days_listed=0,
                                         created_at=now, updated_at=now))
        # Old rows dominate the same rule, as a seller's journal grows.
        rows = [dict(id=f"old-{i}", batch_id="batch", automation_id=rule.id, marketplace="poshmark", action="offer",
                     listing_id=f"item-{i % 100}", state="skipped", like_count=0,
                     created_at=now - timedelta(days=30), expires_at=now) for i in range(30000)]
        for listing, state, likes, age, sent in [
            ("few", "skipped", 1, 1, None),
            ("newer", "skipped", 0, 2, None),
            ("newer", "done", 3, 1, now),
            ("offered", "done", 5, 30, now - timedelta(days=30)),
            ("offered", "skipped", 5, 1, None),
            ("unknown", "unknown", 0, 1, None),
            ("null", "skipped", None, 1, None),
            ("boundary", "skipped", 0, 7, None),
            # Equal timestamps remain equal: the index must not discard ties.
            ("tie", "skipped", 0, 1, None),
            ("tie", "skipped", 1, 1, None),
        ]:
            rows.append(dict(id=f"recent-{len(rows)}", batch_id="batch", automation_id=rule.id,
                             marketplace="poshmark", action="offer", listing_id=listing, state=state,
                             like_count=likes, created_at=now - timedelta(days=age), expires_at=now, sent_at=sent))
        # Uniform keys for executemany.
        for row in rows:
            row.setdefault("sent_at", None)
        db.execute(history.insert(), rows)
        db.commit()
        assert _waiting_for_likes(db, rule, now) == {"few", "offered", "tie"}

    command.upgrade(config, "head")
    with engine.begin() as connection:
        indexes = {index["name"]: index["column_names"] for index in sa.inspect(connection).get_indexes("listing_promotions")}
        assert indexes["ix_listing_promotions_rule_recent"] == ["automation_id", "marketplace", "action", "created_at"]
        assert connection.scalar(sa.select(sa.func.count()).select_from(ListingPromotion)) == len(rows)
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
        connection.execute(sa.text("ANALYZE listing_promotions"))
        statement = sa.select(ListingPromotion.listing_id, ListingPromotion.state, ListingPromotion.like_count).where(
            ListingPromotion.automation_id == "history-rule", ListingPromotion.marketplace == "poshmark",
            ListingPromotion.action == "offer", ListingPromotion.created_at > now - timedelta(days=7),
        ).order_by(ListingPromotion.created_at.desc())
        sql = str(statement.compile(engine, compile_kwargs={"literal_binds": True}))
        prefix = "EXPLAIN QUERY PLAN " if engine.dialect.name == "sqlite" else "EXPLAIN "
        plan = str(connection.execute(sa.text(prefix + sql)).all())
        assert "ix_listing_promotions_rule_recent" in plan
        assert "Sort" not in plan and "TEMP B-TREE" not in plan
    with Session(engine) as db:
        rule = db.get(PromotionAutomation, "history-rule")
        assert _waiting_for_likes(db, rule, now) == {"few", "offered", "tie"}


def test_conflicting_timestamp_ties_never_allow_an_unconfirmed_send_to_repeat(migration_engine):  # noqa: F811
    engine = migration_engine
    command.upgrade(migration_config(engine), "head")
    index = next(index for index in ListingPromotion.__table__.indexes if index.name == "ix_listing_promotions_rule_recent")
    now = datetime(2026, 11, 15, 23, 30)
    with Session(engine) as db:
        rule = PromotionAutomation(id="tie-rule", name="Offers", marketplace="poshmark", action="offer",
                                   min_likes=2, enabled=True)
        db.add(rule)
        db.commit()
        # Exercise the real waiting lookup, blocking lookup and round. Every
        # alternative winner of the ambiguous tie must still refuse a resend.
        with patch("anteroom.database.SessionLocal", sessionmaker(bind=engine)), \
             patch.object(promotion_automations, "_now", return_value=now), \
             patch.object(promotion_automations, "_covered", return_value=[(SimpleNamespace(id="item"), "item", None)]), \
             patch.object(promotion_automations.promotions, "require_chrome"), \
             patch.object(promotion_automations.promotions, "review", new_callable=AsyncMock) as review, \
             patch.object(promotion_automations.promotions, "perform", new_callable=AsyncMock) as perform:
            for indexed in (False, True):
                if indexed:
                    index.create(engine)
                else:
                    index.drop(engine)
                for pending, other, reverse in product(("unknown", "sending"),
                                                       (("skipped", 0), ("skipped", 3), ("done", 3), ("failed", 3), ("preview", 3)),
                                                       (False, True)):
                    case = (indexed, pending, other, reverse)
                    db.query(ListingPromotion).delete()
                    states = [(pending, 0), other]
                    if reverse:
                        states.reverse()
                    for position, (state, likes) in enumerate(states):
                        db.add(ListingPromotion(id=f"tie-{position}", batch_id="tie", automation_id=rule.id,
                                                marketplace="poshmark", action="offer", listing_id="item", state=state,
                                                like_count=likes, created_at=now - timedelta(hours=1), expires_at=now,
                                                # Expired cooldown must not hide a broken unknown guard.
                                                sent_at=now - timedelta(days=2) if state == "done" else None))
                    rule.next_run_at = now
                    db.commit()
                    run = asyncio.run(promotion_automations.run_round(rule.id, expected_next_run_at=rule.next_run_at,
                                                                      sleep=AsyncMock()))
                    db.expire_all()
                    assert run is not None and run.sent == 0 and run.skipped == 1, case
                    assert db.query(ListingPromotion).count() == 2, case
                    assert db.query(ListingPromotion).filter_by(state=pending).count() == 1, case
                    review.assert_not_awaited()
                    perform.assert_not_awaited()
