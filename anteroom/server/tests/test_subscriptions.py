"""Hosted subscription tiers, Smart Credit enforcement, operator routes, and Postgres RLS."""

from __future__ import annotations

import asyncio
from io import BytesIO
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from PIL import Image
import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from anteroom.database import Base, load_models
from anteroom.models.conversation import Conversation, Photo
from anteroom.models.subscription import PlanUsageEvent, SellerPlan
from anteroom.routes import subscriptions as subscription_routes
from anteroom.services import (
    background_tasks,
    photo_studio,
    photoroom,
    service_context,
    subscriptions,
)
from anteroom.services.photoroom import PhotoroomError, StudioOptions, VirtualModel
from anteroom.services.seller_sessions import SellerIdentity
from test_seller_ownership import migration_engine  # noqa: F401

SELLER_A = "11111111-1111-1111-1111-111111111111"
SELLER_B = "22222222-2222-2222-2222-222222222222"
OPERATOR_ID = "99999999-9999-9999-9999-999999999999"


@pytest.fixture
def db(monkeypatch):
    load_models()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr("anteroom.database.SessionLocal", sessions)
    monkeypatch.delenv(subscriptions.UNLIMITED_EMAILS_ENV, raising=False)
    monkeypatch.delenv("ANTEROOM_OPERATOR_USER_ID", raising=False)
    session = sessions()
    yield session
    session.close()


def _ctx(seller: str, sessions, *, email: str | None = "seller@example.com"):
    return service_context.ServiceContext(
        seller_id=seller,
        session_factory=sessions,
        settings=SimpleNamespace(),
        secrets=SimpleNamespace(),
        photos=SimpleNamespace(),
        seller_email=email,
    )


def _jpeg_bytes() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (32, 32), (80, 90, 100)).save(buf, "JPEG")
    return buf.getvalue()


def test_paid_tier_catalog_has_positive_worst_case_margins():
    # Worst-case COGS is 100% credit consumption on AI listing generation ($0.11 / 5 cr = $0.022/cr)
    # plus Stripe processing (2.9% + $0.30). Every paid tier must stay well above 35% gross margin.
    for plan in subscriptions.PUBLIC_PLANS:
        if plan.monthly_price_cents == 0:
            assert plan.id == "free"
            assert plan.monthly_credits == 50
            assert plan.monthly_listed_items == 10
            continue
        assert plan.monthly_listed_items is None
        assert plan.annual_price_cents == plan.monthly_price_cents * 10
        assert plan.monthly_credits is not None
        revenue_usd = plan.monthly_price_cents / 100.0
        stripe_fee = revenue_usd * 0.029 + 0.30
        worst_case_cogs = plan.monthly_credits * 0.022
        gross_margin = (revenue_usd - stripe_fee - worst_case_cogs) / revenue_usd
        assert gross_margin >= 0.35, f"{plan.id} margin too low: {gross_margin:.2%}"


def test_photo_credit_weights_match_cutout_vs_studio():
    assert subscriptions.photo_credit_cost(StudioOptions(remove_background=True)) == (
        subscriptions.KIND_PHOTO_CUTOUT,
        1,
    )
    assert subscriptions.photo_credit_cost(StudioOptions(remove_background=True, background="#F5F5F5")) == (
        subscriptions.KIND_PHOTO_CUTOUT,
        1,
    )
    for advanced in (
        StudioOptions(remove_background=True, shadow=True),
        StudioOptions(ghost_mannequin=True),
        StudioOptions(flat_lay=True),
        StudioOptions(remove_background=True, keep="jacket"),
        StudioOptions(remove_background=True, exclude="hanger"),
        StudioOptions(virtual_model=VirtualModel()),
    ):
        assert subscriptions.photo_credit_cost(advanced) == (subscriptions.KIND_PHOTO_STUDIO, 5)


def test_ai_listing_generation_charges_and_enforces_free_tier(db):
    from anteroom.database import SessionLocal

    with service_context.use(_ctx(SELLER_A, SessionLocal)):
        # Free tier gets 50 credits = 10 AI listing generations (5 credits each).
        for idx in range(10):
            conv = Conversation(id=f"conv-{idx}", user_id=SELLER_A, title=f"Item {idx}")
            db.add(conv)
            db.commit()
            task = background_tasks.enqueue(db, conv.id, "generate", {"text": ""})
            assert task.kind == "generate"

        assert subscriptions.credits_used(db, SELLER_A, subscriptions.current_period()) == 50

        # 11th AI listing generation is refused with HTTP 402 + plan_credit_limit.
        extra = Conversation(id="conv-extra", user_id=SELLER_A, title="Extra")
        db.add(extra)
        db.commit()
        with pytest.raises(subscriptions.PlanLimitError) as exc_info:
            background_tasks.enqueue(db, extra.id, "generate", {"text": ""})
        assert exc_info.value.status_code == 402
        assert exc_info.value.reason == subscriptions.REASON_CREDIT_LIMIT
        assert exc_info.value.headers == {"X-Anteroom-Reason": subscriptions.REASON_CREDIT_LIMIT}

        # Chat edits on an existing draft do not burn full listing-generation credits.
        chat_task = background_tasks.enqueue(db, extra.id, "chat", {"text": "shorten title"})
        assert chat_task.kind == "chat"


def test_photo_studio_charges_cutouts_and_refunds_on_failure(db, monkeypatch, tmp_path):
    from anteroom.database import SessionLocal

    conv = Conversation(id="conv-photo", user_id=SELLER_A, title="Jacket")
    p1 = Photo(
        id="p1",
        user_id=SELLER_A,
        conversation_id=conv.id,
        original_filename="1.jpg",
        stored_filename="p1.jpg",
        mime_type="image/jpeg",
        size_bytes=100,
        display_order=0,
    )
    p2 = Photo(
        id="p2",
        user_id=SELLER_A,
        conversation_id=conv.id,
        original_filename="2.jpg",
        stored_filename="p2.jpg",
        mime_type="image/jpeg",
        size_bytes=100,
        display_order=1,
    )
    db.add_all([conv, p1, p2])
    db.commit()

    jpeg = _jpeg_bytes()
    (tmp_path / "p1.jpg").write_bytes(jpeg)
    (tmp_path / "p2.jpg").write_bytes(jpeg)

    class FakePhotos:
        def read(self, key: str) -> bytes:
            return (tmp_path / key).read_bytes()

        def local_path(self, key: str):
            path = tmp_path / key
            if not path.exists():
                raise FileNotFoundError(key)
            return path

        def edited_key(self, source: str) -> str:
            return f"edited-{source}"

        def put(self, key: str, data: bytes) -> None:
            (tmp_path / key).write_bytes(data)

        def delete(self, key: str) -> None:
            (tmp_path / key).unlink(missing_ok=True)

    ctx = service_context.ServiceContext(
        seller_id=SELLER_A,
        session_factory=SessionLocal,
        settings=SimpleNamespace(),
        secrets=SimpleNamespace(),
        photos=FakePhotos(),
        seller_email="seller@example.com",
    )

    monkeypatch.setattr(photoroom, "available", lambda: True)

    png_buf = BytesIO()
    Image.new("RGBA", (32, 32), (255, 255, 255, 255)).save(png_buf, "PNG")
    png_bytes = png_buf.getvalue()

    async def run_db(fn, *args):
        with SessionLocal() as session:
            return fn(session, *args)

    # 1. Plain background removal costs 1 credit per photo; failure auto-refunds.
    call_count = 0

    async def fail_second(_data, _options):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise PhotoroomError("Photoroom timeout", 502)
        return png_bytes

    monkeypatch.setattr(photoroom, "edit", fail_second)
    with service_context.use(ctx):
        outcome = asyncio.run(
            photo_studio.run(run_db, conv.id, ["p1", "p2"], StudioOptions(remove_background=True))
        )
    assert len(outcome["photos"]) == 1
    assert len(outcome["errors"]) == 1
    # 2 reserved (1 + 1), 1 refunded = 1 net credit spent.
    assert subscriptions.credits_used(db, SELLER_A, subscriptions.current_period()) == 1

    # 2. Pre-flight rejects a batch that exceeds remaining credits before calling Photoroom.
    called_after_exhaust = False

    async def should_not_call(_data, _options):
        nonlocal called_after_exhaust
        called_after_exhaust = True
        return png_bytes

    monkeypatch.setattr(photoroom, "edit", should_not_call)
    # Spend 46 more credits (total 47/50); an advanced studio edit needs 5 credits -> rejected pre-flight!
    db.add(
        PlanUsageEvent(
            user_id=SELLER_A,
            period=subscriptions.current_period(),
            kind=subscriptions.KIND_AI_LISTING,
            credits=46,
            conversation_id=conv.id,
        )
    )
    db.commit()
    with service_context.use(ctx):
        with pytest.raises(subscriptions.PlanLimitError) as exc_info:
            asyncio.run(photo_studio.run(run_db, conv.id, ["p1"], StudioOptions(ghost_mannequin=True)))
    assert exc_info.value.status_code == 402
    assert exc_info.value.reason == subscriptions.REASON_CREDIT_LIMIT
    assert not called_after_exhaust


def test_free_tier_monthly_listed_item_cap_allows_cross_listing_and_lister_is_unlimited(db):
    from anteroom.database import SessionLocal

    with service_context.use(_ctx(SELLER_A, SessionLocal)):
        # List 10 distinct items on eBay; cross-list item 0 to Poshmark & Mercari (counts once!).
        for idx in range(10):
            subscriptions.require_and_record_listed_item(db, f"item-{idx}", marketplace="ebay")
        db.commit()
        assert subscriptions.listed_items_used(db, SELLER_A, subscriptions.current_period()) == 10

        # Cross-listing an already-counted item to Poshmark and Mercari stays allowed.
        subscriptions.require_and_record_listed_item(db, "item-0", marketplace="poshmark")
        subscriptions.require_and_record_listed_item(db, "item-0", marketplace="mercari")
        db.commit()
        assert subscriptions.listed_items_used(db, SELLER_A, subscriptions.current_period()) == 10

        # 11th distinct item is refused on Free with 402 plan_listing_limit.
        with pytest.raises(subscriptions.PlanLimitError) as exc_info:
            subscriptions.require_and_record_listed_item(db, "item-10", marketplace="ebay")
        assert exc_info.value.status_code == 402
        assert exc_info.value.reason == subscriptions.REASON_LISTING_LIMIT

        # Upgrading to Lister unlocks unlimited distinct items at 0 credits.
        subscriptions.set_seller_plan(SELLER_A, "lister", db=db)
        subscriptions.require_and_record_listed_item(db, "item-10", marketplace="ebay")
        db.commit()
        assert subscriptions.listed_items_used(db, SELLER_A, subscriptions.current_period()) == 11
        assert subscriptions.credits_used(db, SELLER_A, subscriptions.current_period()) == 0


def test_only_approved_unlimited_emails_bypass_limits_and_env_overrides(db, monkeypatch):
    from anteroom.database import SessionLocal

    for owner_email in ("cristianarielcanizares@gmail.com", "XYZCANIZAREZ@GMAIL.COM"):
        with service_context.use(_ctx(SELLER_A, SessionLocal, email=owner_email)):
            plan, _ = subscriptions.resolve_plan(db, SELLER_A)
            assert plan.id == "unlimited"
            assert plan.monthly_credits is None
            assert plan.monthly_listed_items is None
            # Even after burning 100 credits and 15 items, unlimited accounts are never blocked.
            for idx in range(12):
                subscriptions.require_and_record_listed_item(db, f"owner-item-{owner_email}-{idx}", marketplace="ebay")
            db.commit()

    # Non-listed emails default to Free.
    with service_context.use(_ctx(SELLER_B, SessionLocal, email="other@gmail.com")):
        plan, _ = subscriptions.resolve_plan(db, SELLER_B)
        assert plan.id == "free"

    # Explicit ANTEROOM_UNLIMITED_EMAILS overrides the default list.
    monkeypatch.setenv(subscriptions.UNLIMITED_EMAILS_ENV, "vip@shop.test")
    with service_context.use(_ctx(SELLER_A, SessionLocal, email="cristianarielcanizares@gmail.com")):
        assert subscriptions.resolve_plan(db, SELLER_A)[0].id == "free"
    with service_context.use(_ctx(SELLER_B, SessionLocal, email="vip@shop.test")):
        assert subscriptions.resolve_plan(db, SELLER_B)[0].id == "unlimited"


def test_desktop_execution_without_service_context_is_never_limited(db):
    # Outside service_context.use(...), current() is None (desktop app).
    assert subscriptions.charge_ai_listing(db, "desktop-conv") == 0
    subscriptions.require_and_record_listed_item(db, "desktop-conv", marketplace="ebay")
    assert db.scalars(select(PlanUsageEvent)).all() == []


def test_seller_and_operator_http_routes(db, monkeypatch):
    from anteroom.database import SessionLocal

    app = FastAPI()
    active_seller: dict[str, SellerIdentity] = {
        "seller": SellerIdentity(user_id=SELLER_A, email="seller@example.com"),
    }

    @app.middleware("http")
    async def attach_seller(request: Request, call_next):
        request.state.seller = active_seller["seller"]
        return await call_next(request)

    def fake_seller_db():
        with SessionLocal() as session:
            session.user_id = active_seller["seller"].user_id  # type: ignore[attr-defined]
            yield session

    app.dependency_overrides[subscription_routes.get_seller_db] = fake_seller_db
    monkeypatch.setattr(subscription_routes, "_operator_db", lambda: db)
    app.include_router(subscription_routes.router)
    app.include_router(subscription_routes.operator_router)

    client = TestClient(app)

    # 1. Seller sees Free tier summary by default.
    resp = client.get("/api/settings/subscription")
    assert resp.status_code == 200
    body = resp.json()
    assert body["plan"]["id"] == "free"
    assert body["credits"]["limit"] == 50
    assert body["listed_items"]["limit"] == 10
    assert [p["id"] for p in body["plans"]] == ["free", "lister", "starter", "pro", "business"]

    # 2. Non-operator cannot access operator plan endpoints.
    monkeypatch.setenv("ANTEROOM_OPERATOR_USER_ID", OPERATOR_ID)
    assert client.get("/api/operator/plans").status_code == 403
    assert client.put(f"/api/operator/plans/{SELLER_A}", json={"plan_id": "pro"}).status_code == 403

    # 3. Operator assigns Seller A to Pro (annual) and sees them in GET /api/operator/plans.
    active_seller["seller"] = SellerIdentity(user_id=OPERATOR_ID, email="ops@example.com")
    invalid = client.put(f"/api/operator/plans/{SELLER_A}", json={"plan_id": "unknown"})
    assert invalid.status_code == 422

    assigned = client.put(
        f"/api/operator/plans/{SELLER_A}",
        json={"plan_id": "pro", "billing_interval": "annual", "note": "Paid beta"},
    )
    assert assigned.status_code == 200
    assert assigned.json()["plan_id"] == "pro"
    assert assigned.json()["billing_interval"] == "annual"

    listed = client.get("/api/operator/plans")
    assert listed.status_code == 200
    rows = listed.json()["sellers"]
    assert len(rows) == 1
    assert rows[0]["user_id"] == SELLER_A
    assert rows[0]["plan_id"] == "pro"

    # 4. Seller A now sees Pro (annual) on /api/settings/subscription.
    active_seller["seller"] = SellerIdentity(user_id=SELLER_A, email="seller@example.com")
    updated = client.get("/api/settings/subscription")
    assert updated.status_code == 200
    assert updated.json()["plan"]["id"] == "pro"
    assert updated.json()["plan"]["billing_interval"] == "annual"
    assert updated.json()["credits"]["limit"] == 1200
    assert updated.json()["listed_items"]["limit"] is None


def test_postgres_rls_isolates_seller_plans_and_blocks_self_upgrade(migration_engine, monkeypatch):  # noqa: F811
    if migration_engine.dialect.name != "postgresql":
        pytest.skip("Postgres RLS isolation test requires PostgreSQL")

    from anteroom.services import schema_migrations
    from anteroom.services.hosted_database import SellerDatabaseSession

    schema_migrations.ensure_schema(migration_engine)
    monkeypatch.setattr("anteroom.database.engine", migration_engine)

    # Operator assigns Seller A to Pro via anteroom_private.set_seller_plan.
    assigned = subscriptions.set_seller_plan(SELLER_A, "pro", billing_interval="monthly", note="Verified")
    assert assigned["plan_id"] == "pro"

    # Seller A reads their own plan, inserts usage, and CANNOT self-upgrade seller_plans.
    with SellerDatabaseSession(user_id=SELLER_A, bind=migration_engine) as db_a:
        plan_a, _ = subscriptions.resolve_plan(db_a, SELLER_A, email="a@example.com")
        assert plan_a.id == "pro"
        db_a.add(
            PlanUsageEvent(
                user_id=SELLER_A,
                period="2026-11",
                kind=subscriptions.KIND_AI_LISTING,
                credits=5,
                conversation_id="conv-a",
            )
        )
        db_a.commit()

        # Attempt to self-upgrade to unlimited must fail under Postgres grants (no UPDATE/INSERT on seller_plans).
        with pytest.raises(ProgrammingError):
            db_a.execute(
                sa.text("UPDATE public.seller_plans SET plan_id = 'unlimited' WHERE user_id = :uid"),
                {"uid": SELLER_A},
            )
        db_a.rollback()

    # Seller B cannot see Seller A's plan or usage events.
    with SellerDatabaseSession(user_id=SELLER_B, bind=migration_engine) as db_b:
        assert db_b.scalars(select(SellerPlan)).all() == []
        assert db_b.scalars(select(PlanUsageEvent)).all() == []
        plan_b, _ = subscriptions.resolve_plan(db_b, SELLER_B, email="b@example.com")
        assert plan_b.id == "free"

    # Operator listing function returns Seller A's plan + usage across RLS.
    report = subscriptions.list_seller_plans(period="2026-11")
    seller_a_row = next(row for row in report["sellers"] if row["user_id"] == SELLER_A)
    assert seller_a_row["plan_id"] == "pro"
    assert seller_a_row["credits_used"] == 5


def test_account_import_rejects_subscription_and_credit_tables(db):
    from fastapi import HTTPException

    from anteroom.services import account_import

    exposed = {table.name for table in account_import.imported_tables()}
    assert "seller_plans" not in exposed
    assert "plan_usage_events" not in exposed

    for forbidden_table in ("seller_plans", "plan_usage_events"):
        with pytest.raises(HTTPException) as exc_info:
            account_import.import_rows(db, SELLER_A, forbidden_table, [])
        assert exc_info.value.status_code == 422


def test_photo_refund_stays_in_reserved_period_across_month_boundary(db):
    from datetime import UTC, datetime

    from anteroom.database import SessionLocal

    nov_end = datetime(2026, 11, 30, 23, 59, 59, tzinfo=UTC)
    dec_start = datetime(2026, 12, 1, 0, 0, 5, tzinfo=UTC)
    with service_context.use(_ctx(SELLER_A, SessionLocal)):
        cost, period = subscriptions.reserve_photo_credit(
            db, "conv-boundary", "photo-1", StudioOptions(ghost_mannequin=True), now=nov_end,
        )
        assert cost == 5
        assert period == "2026-11"
        assert subscriptions.credits_used(db, SELLER_A, "2026-11") == 5

        subscriptions.refund_photo_credit(
            db, "conv-boundary", "photo-1", cost, period=period, now=dec_start,
        )
        assert subscriptions.credits_used(db, SELLER_A, "2026-11") == 0
        # December must remain completely clean (no negative credit spillover).
        dec_summary = subscriptions.seller_subscription_summary(db, SELLER_A, now=dec_start)
        assert dec_summary["credits"]["used"] == 0
        assert dec_summary["credits"]["breakdown"]["photo_refund"]["count"] == 0


def test_hosted_enforcement_fails_closed_without_service_context(db, monkeypatch):
    monkeypatch.setenv("ANTEROOM_HOSTED", "1")
    with pytest.raises(RuntimeError):
        subscriptions.charge_ai_listing(db, "conv-no-ctx")

