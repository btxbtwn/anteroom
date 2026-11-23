"""Tests for Home Command Center: Today's Summary, Goals Tracker, and Needs Attention inbox."""
from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text

from anteroom.database import SessionLocal, engine
from anteroom.main import app
from anteroom.models.automation import Automation, AutomationApproval, AutomationRelistGap
from anteroom.models.cloud_session import CloudMarketplaceSession
from anteroom.models.conversation import Conversation, Message, Photo
from anteroom.models.ebay_offer import EbayOfferAttempt, EbayOfferRule
from anteroom.models.home import AttentionDismissal, SellerGoal
from anteroom.models.job import Job
from anteroom.models.listing import Listing, ListingRevision
from anteroom.models.listing_evidence import ListingCorrection, SaleSnapshot
from anteroom.models.listing_promotion import ListingPromotion
from anteroom.models.marketplace_listing import MarketplaceListing
from anteroom.models.marketplace_sale import MarketplaceSale
from anteroom.models.promotion_automation import PromotionAutomation
from anteroom.services import home_dashboard, sales_ledger


def _add_listing(db, conv: Conversation, listing_json: dict, *, rev_id: str | None = None) -> str:
    rid = rev_id or f"rev-{uuid.uuid4().hex[:8]}"
    db.add(ListingRevision(id=rid, conversation_id=conv.id, listing_json=listing_json, source="test"))
    db.flush()
    db.add(Listing(conversation_id=conv.id, current_revision_id=rid))
    db.flush()
    return rid


@pytest.fixture(autouse=True)
def _clean_home_tables():
    with SessionLocal() as db:
        for model in (
            AttentionDismissal,
            SellerGoal,
            AutomationRelistGap,
            AutomationApproval,
            Automation,
            CloudMarketplaceSession,
            EbayOfferAttempt,
            EbayOfferRule,
            ListingPromotion,
            PromotionAutomation,
            Job,
            MarketplaceSale,
            MarketplaceListing,
            ListingCorrection,
            SaleSnapshot,
            Message,
            Photo,
            Listing,
            ListingRevision,
            Conversation,
        ):
            db.query(model).delete()
        db.commit()
    yield


def test_period_window_calendar_bounds():
    today = date(2026, 5, 13)  # Wednesday
    assert home_dashboard.period_window("weekly", today) == (date(2026, 5, 11), date(2026, 5, 17))
    assert home_dashboard.period_window("monthly", today) == (date(2026, 5, 1), date(2026, 5, 31))
    assert home_dashboard.period_window("quarterly", today) == (date(2026, 4, 1), date(2026, 6, 30))
    assert home_dashboard.period_window("yearly", today) == (date(2026, 1, 1), date(2026, 12, 31))


def test_home_summary_today_kpis_and_goals_crud():
    client = TestClient(app)
    now = datetime.now(UTC).replace(tzinfo=None)

    with SessionLocal() as db:
        # 1) A listing published today
        c_listed = Conversation(id=f"c-{uuid.uuid4().hex[:8]}", title="Vintage Leather Jacket", status="active")
        db.add(c_listed)
        db.flush()
        rev_listed = _add_listing(db, c_listed, {"title": "Vintage Leather Jacket", "price": 120.0, "cost": 30.0})
        db.add(
            MarketplaceListing(
                conversation_id=c_listed.id,
                marketplace="ebay",
                state="published",
                listing_id="eb-1",
                revision_id=rev_listed,
                listed_price=120.0,
                listed_at=now,
            )
        )

        # 2) A listing sold today with known cost & fees
        c_sold = Conversation(id=f"c-{uuid.uuid4().hex[:8]}", title="Mid-Century Lamp", status="sold")
        db.add(c_sold)
        db.flush()
        rev_sold = _add_listing(db, c_sold, {"title": "Mid-Century Lamp", "price": 100.0, "cost": 25.0})
        db.add(
            MarketplaceListing(
                conversation_id=c_sold.id,
                marketplace="ebay",
                state="sold",
                listing_id="eb-2",
                revision_id=rev_sold,
                listed_price=100.0,
                listed_at=now - timedelta(days=2),
            )
        )
        sale = MarketplaceSale(
            conversation_id=c_sold.id,
            marketplace="ebay",
            order_id="ord-1",
            line_id="line-1",
            listing_id="eb-2",
            quantity=1,
            sold_at=now,
        )
        db.add(sale)
        db.flush()
        sale.financials = {
            "currency": "USD",
            "item_revenue": "100.00",
            "shipping_credit": "0.00",
            "refunds": "0.00",
            "fees": "13.00",
            "shipping_cost": "12.00",
            "item_cost": "25.00",
            "order_basis": sales_ledger.order_basis(sale),
            "source": "seller",
        }

        # 3) One manual share, one automated share, and one automated eBay offer
        db.add(
            ListingPromotion(
                batch_id="batch-1",
                conversation_id=c_listed.id,
                marketplace="poshmark",
                action="share",
                listing_id="pm-share-1",
                state="sent",
                sent_at=now,
                expires_at=now + timedelta(days=2),
            )
        )
        automation = PromotionAutomation(name="Auto share", marketplace="poshmark", action="share", enabled=True)
        db.add(automation)
        db.flush()
        db.add(ListingPromotion(
            batch_id="auto-batch", conversation_id=c_listed.id, marketplace="poshmark",
            action="share", listing_id="pm-share-1", state="sent", automation_id=automation.id,
            sent_at=now, expires_at=now + timedelta(days=2),
        ))
        rule = EbayOfferRule(
            account_id="acct-1",
            listing_id="eb-1",
            conversation_id=c_listed.id,
            discount_percent=10,
            minimum_price_cents=8000,
            enabled=True,
        )
        db.add(rule)
        db.flush()
        db.add(
            EbayOfferAttempt(
                rule_id=rule.id,
                current_price_cents=12000,
                offer_price_cents=10800,
                discount_percent=10,
                minimum_price_cents=8000,
                state="sent",
                sent_at=now,
                expires_at=now + timedelta(days=2),
            )
        )
        db.commit()

    res = client.get("/api/home?timezone=UTC")
    assert res.status_code == 200
    body = res.json()
    today = body["today"]
    assert today["units_listed"] >= 1
    assert today["units_sold"] == 1
    assert today["revenue"] == pytest.approx(100.0)
    # 100 - 25 cost - 13 fee - 12 shipping = 50
    assert today["profit"] == pytest.approx(50.0)
    assert today["shares_sent"] == 2
    assert today["offers_sent"] == 1
    assert today["automation_actions"] == 2

    # Create goals across all 4 metrics & periods
    for period, metric, target in (
        ("weekly", "listings", 10),
        ("monthly", "profit", 200),
        ("quarterly", "sales", 500),
        ("yearly", "sell_through_rate", 60),
    ):
        created = client.post(
            "/api/home/goals?timezone=UTC",
            json={"period": period, "metric": metric, "target": target},
        )
        assert created.status_code == 200, created.text
        assert created.json()["ok"] is True

    after_create = client.get("/api/home?timezone=UTC").json()
    assert len(after_create["goals"]) == 4
    by_metric = {g["metric"]: g for g in after_create["goals"]}
    assert by_metric["profit"]["current"] == pytest.approx(50.0)
    assert by_metric["profit"]["remaining"] == pytest.approx(150.0)
    assert by_metric["profit"]["progress_percent"] == pytest.approx(25.0)
    assert by_metric["profit"]["unit"] == "currency"
    assert by_metric["sales"]["current"] == pytest.approx(100.0)
    assert by_metric["sales"]["unit"] == "currency"
    assert by_metric["listings"]["current"] >= 1
    assert by_metric["listings"]["unit"] == "count"
    assert by_metric["sell_through_rate"]["unit"] == "percent"

    # Update a goal
    goal_id = by_metric["profit"]["id"]
    updated = client.patch(
        f"/api/home/goals/{goal_id}?timezone=UTC",
        json={"target": 40, "label": "Stretch Profit"},
    )
    assert updated.status_code == 200
    updated_profit = next(g for g in updated.json()["home"]["goals"] if g["id"] == goal_id)
    assert updated_profit["label"] == "Stretch Profit"
    assert updated_profit["pace"] == "achieved"
    assert updated_profit["remaining"] == 0.0

    # Delete a goal
    deleted = client.delete(f"/api/home/goals/{goal_id}?timezone=UTC")
    assert deleted.status_code == 200
    assert all(g["id"] != goal_id for g in deleted.json()["home"]["goals"])

    # Validation: sell-through rate > 100 rejected
    bad = client.post(
        "/api/home/goals?timezone=UTC",
        json={"period": "monthly", "metric": "sell_through_rate", "target": 150},
    )
    assert bad.status_code == 422


def test_needs_attention_reads_latest_job_status_without_listing_snapshots():
    now = datetime.now(UTC).replace(tzinfo=None)
    with SessionLocal() as db:
        db.add_all([
            Conversation(id="still-failed", title="Still failed"),
            Conversation(id="recovered", title="Recovered"),
        ])
        db.flush()
        for identity in ("still-failed", "recovered"):
            db.add(Job(conversation_id=identity, approved_revision_id="rev", listing_snapshot={},
                       status="failed", last_error="Old error", created_at=now - timedelta(days=1)))
        db.add_all([
            Job(id="latest-failure", conversation_id="still-failed", approved_revision_id="rev", listing_snapshot={},
                status="failed", last_error="Current error", created_at=now, updated_at=now),
            Job(conversation_id="recovered", approved_revision_id="rev", listing_snapshot={},
                status="completed", created_at=now),
        ])
        db.commit()

    job_reads = []

    def record(_conn, _cursor, statement, _parameters, _context, _executemany):
        if "from jobs" in statement.lower():
            job_reads.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        response = TestClient(app).get("/api/home?timezone=UTC")
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert response.status_code == 200
    failures = [item for item in response.json()["needs_attention"] if item["kind"] == "failed_job"]
    assert [(item["key"], item["detail"]) for item in failures] == [
        ("failed-job:still-failed:latest-failure", "Current error"),
    ]
    assert job_reads
    assert all("listing_snapshot" not in statement.lower() for statement in job_reads)


def test_needs_attention_categories_dismiss_and_seven_day_expiry():
    client = TestClient(app)
    now = datetime.now(UTC).replace(tzinfo=None)

    with SessionLocal() as db:
        # 1) Sold elsewhere, still live on another marketplace
        c_still = Conversation(id="conv-still", title="Cashmere Cardigan", status="sold")
        db.add(c_still)
        db.flush()
        _add_listing(db, c_still, {"title": "Cashmere Cardigan", "price": 80.0, "quantity": 1})
        db.add_all([
            MarketplaceListing(
                conversation_id=c_still.id,
                marketplace="ebay",
                state="sold",
                listing_id="123456789012",
                listing_url="https://www.ebay.com/itm/123456789012",
                listed_at=now - timedelta(days=3),
            ),
            MarketplaceListing(
                conversation_id=c_still.id,
                marketplace="poshmark",
                state="published",
                listing_id="65a1b2c3d4e5f6a7b8c9d0e1",
                listing_url="https://poshmark.com/listing/Cashmere-Cardigan-65a1b2c3d4e5f6a7b8c9d0e1",
                listed_at=now - timedelta(days=3),
            ),
            MarketplaceSale(
                conversation_id=c_still.id,
                marketplace="ebay",
                order_id="ord-still",
                line_id="line-still",
                listing_id="123456789012",
                quantity=1,
                order_status="paid",
                source="marketplace",
                state="new",
                sold_at=now,
            ),
        ])

        # 2) Expired cloud marketplace session
        db.add(
            CloudMarketplaceSession(
                marketplace="poshmark",
                state="expired",
                last_error="Session cookie expired",
            )
        )

        # 3) Failed job / publish error
        c_fail = Conversation(id="conv-fail", title="Brass Candlesticks", status="draft")
        db.add(c_fail)
        db.flush()
        _add_listing(db, c_fail, {"title": "Brass Candlesticks", "price": 45.0})
        db.add(
            Job(
                conversation_id=c_fail.id,
                approved_revision_id="rev-1",
                listing_snapshot={"title": "Brass Candlesticks"},
                status="failed",
                last_error="Missing shipping profile",
            )
        )

        # 4) Listing with unsent local edits (revision_id differs from Listing.current_revision_id)
        c_unsent = Conversation(id="conv-unsent", title="Denim Trucker Jacket", status="active")
        db.add(c_unsent)
        db.flush()
        old_rev = "rev-old-sent"
        new_rev = "rev-new-unsent"
        db.add_all([
            ListingRevision(
                id=old_rev,
                conversation_id=c_unsent.id,
                listing_json={"title": "Old Jacket Title", "price": 65.0},
                source="test",
            ),
            ListingRevision(
                id=new_rev,
                conversation_id=c_unsent.id,
                listing_json={"title": "Denim Trucker Jacket", "price": 75.0},
                source="test",
            ),
        ])
        db.flush()
        db.add(Listing(conversation_id=c_unsent.id, current_revision_id=new_rev))
        db.add(
            MarketplaceListing(
                conversation_id=c_unsent.id,
                marketplace="ebay",
                state="published",
                listing_id="eb-unsent",
                revision_id=old_rev,
                listed_price=65.0,
                listed_at=now - timedelta(hours=2),
            )
        )

        # 5) Pending automation approval + 6) Relist gap
        auto = Automation(
            name="Weekend Delist/Relist",
            status="active",
            schedule={"kind": "interval"},
            filters={},
            actions=[{"kind": "relist"}],
            marketplaces=["poshmark"],
        )
        db.add(auto)
        db.flush()
        db.add(
            AutomationApproval(
                automation_id=auto.id,
                conversation_id=c_unsent.id,
                title="Denim Trucker Jacket",
                marketplaces=["poshmark"],
                status="pending",
                review_basis="Price drop candidate",
            )
        )
        db.add(
            AutomationRelistGap(
                automation_id=auto.id,
                conversation_id=c_unsent.id,
                title="Denim Trucker Jacket",
                marketplace="poshmark",
                state="down, needs relist",
                error="Browser disconnected after delist",
            )
        )

        # 7) Potential duplicate pair (same title & SKU)
        c_dup_a = Conversation(id="conv-dup-a", title="Patagonia Fleece Pullover", status="draft", notes='{"sku": "DUP-42"}')
        c_dup_b = Conversation(id="conv-dup-b", title="Patagonia Fleece Pullover", status="draft", notes='{"sku": "DUP-42"}')
        db.add_all([c_dup_a, c_dup_b])
        db.flush()
        _add_listing(db, c_dup_a, {"title": "Patagonia Fleece Pullover", "brand": "Patagonia", "sku": "DUP-42", "price": 55.0})
        _add_listing(db, c_dup_b, {"title": "Patagonia Fleece Pullover", "brand": "Patagonia", "sku": "DUP-42", "price": 55.0})
        db.commit()

    body = client.get("/api/home?timezone=UTC").json()
    kinds = {item["kind"] for item in body["needs_attention"]}
    assert "still_listed" in kinds
    assert "expired_session" in kinds
    assert "failed_job" in kinds
    assert "unsent_edits" in kinds
    assert "pending_approval" in kinds
    assert "relist_gap" in kinds
    assert "duplicate_listing" in kinds

    # Dismiss one item -> moves from needs_attention to dismissed
    target_item = next(i for i in body["needs_attention"] if i["kind"] == "failed_job")
    dismissed_resp = client.post(
        "/api/home/attention/dismiss?timezone=UTC",
        json={"item_key": target_item["key"], "kind": target_item["kind"]},
    )
    assert dismissed_resp.status_code == 200
    after_dismiss = dismissed_resp.json()["home"]
    assert all(i["key"] != target_item["key"] for i in after_dismiss["needs_attention"])
    assert any(i["key"] == target_item["key"] for i in after_dismiss["dismissed"])

    # Simulate 8 days passing -> expired dismissal reappears in needs_attention
    with SessionLocal() as db:
        row = db.query(AttentionDismissal).filter(AttentionDismissal.item_key == target_item["key"]).one()
        row.expires_at = now - timedelta(days=1)
        db.commit()

    reappeared = client.get("/api/home?timezone=UTC").json()
    assert any(i["key"] == target_item["key"] for i in reappeared["needs_attention"])
    assert all(i["key"] != target_item["key"] for i in reappeared["dismissed"])

    # Dismiss again, then explicitly restore
    client.post(
        "/api/home/attention/dismiss?timezone=UTC",
        json={"item_key": target_item["key"], "kind": target_item["kind"]},
    )
    restored = client.post(
        "/api/home/attention/restore?timezone=UTC",
        json={"item_key": target_item["key"]},
    )
    assert restored.status_code == 200
    assert any(i["key"] == target_item["key"] for i in restored.json()["home"]["needs_attention"])


def test_duplicate_limit_keeps_all_unresolved_dismissals():
    from anteroom.services import similar_items

    with SessionLocal() as db:
        # Two groups of six identical SKUs give 30 ranked pairs.
        for group in ("A", "B"):
            for index in range(6):
                conv = Conversation(
                    id=f"{group}-{index}", title=f"unique{group}{index}", status="draft",
                    notes=json.dumps({"sku": f"shared-{group}"}),
                )
                db.add(conv)
                db.flush()
                _add_listing(db, conv, {"title": conv.title, "sku": f"shared-{group}"})
        db.commit()
        pairs = similar_items.find_similar(db, compute_fingerprints=False)["pairs"]
        assert len(pairs) == 30
        keys = [f"duplicate:{pair['first']['id']}:{pair['second']['id']}" for pair in pairs]
        for key in [*keys[:25], keys[-1]]:
            home_dashboard.dismiss_attention_item(db, item_key=key, kind="duplicate_listing")
        summary = home_dashboard.home_summary(db)
    assert {i["key"] for i in summary["needs_attention"] if i["kind"] == "duplicate_listing"} == set(keys[25:-1])
    assert {i["key"] for i in summary["dismissed"]} == {*keys[:25], keys[-1]}


def test_optional_query_failure_leaves_home_transaction_usable(monkeypatch):
    from anteroom.services import sale_delist_followup

    def failed_read(db):
        db.execute(text("SELECT 1 FROM missing_optional_dashboard_table"))

    with SessionLocal() as db:
        db.add(CloudMarketplaceSession(marketplace="poshmark", state="expired"))
        db.commit()
        monkeypatch.setattr(sale_delist_followup, "still_listed", failed_read)
        result = home_dashboard.home_summary(db)
        assert any(i["key"] == "session:poshmark" for i in result["needs_attention"])


def test_saved_delete_succeeds_if_dashboard_refresh_fails(monkeypatch):
    client = TestClient(app)
    with SessionLocal() as db:
        goal_id = home_dashboard.upsert_goal(db, period="weekly", metric="sales", target=100)

    def failed_refresh(db, **_kwargs):
        db.execute(text("SELECT 1 FROM missing_dashboard_refresh_table"))

    monkeypatch.setattr(home_dashboard, "home_summary", failed_refresh)
    result = client.delete(f"/api/home/goals/{goal_id}")
    assert result.status_code == 200
    assert result.json() == {"ok": True}
    with SessionLocal() as db:
        assert db.get(SellerGoal, goal_id) is None


def test_repeated_goal_and_dismissal_saves_replace_existing_values():
    now = datetime.now(UTC)
    with SessionLocal() as db:
        goal_id = home_dashboard.upsert_goal(db, period="weekly", metric="sales", target=50)
        assert home_dashboard.upsert_goal(db, period="weekly", metric="sales", target=200) == goal_id
        with pytest.raises(ValueError, match="cannot exceed"):
            home_dashboard.update_goal(db, goal_id, metric="sell_through_rate")
        db.rollback()
        assert db.get(SellerGoal, goal_id).target == 200
        db.rollback()
        home_dashboard.dismiss_attention_item(db, item_key="refresh-me", kind="old", now=now)
        latest = home_dashboard.dismiss_attention_item(
            db, item_key="refresh-me", kind="new", now=now + timedelta(days=1),
        )
        row = db.query(AttentionDismissal).one()
        assert row.kind == "new"
        assert row.expires_at.replace(tzinfo=UTC).isoformat() == latest["expires_at"]
