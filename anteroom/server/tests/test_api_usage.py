"""Per-seller managed OpenRouter and Photoroom usage ledger for beta invoicing."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from anteroom.database import Base, load_models
from anteroom.models.api_usage import ApiUsageEvent
from anteroom.services import api_usage, photoroom, service_context
from anteroom.services.photoroom import StudioOptions


SELLER_A = "11111111-1111-1111-1111-111111111111"
SELLER_B = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def db(tmp_path, monkeypatch):
    load_models()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr("anteroom.database.SessionLocal", sessions)
    monkeypatch.delenv("ANTEROOM_SANDBOX", raising=False)
    monkeypatch.delenv("ANTEROOM_MANAGED_OPENROUTER_KEY", raising=False)
    monkeypatch.delenv(photoroom.MANAGED_KEY_ENV, raising=False)
    monkeypatch.delenv(api_usage.PHOTOROOM_COST_ENV, raising=False)
    session = sessions()
    yield session
    session.close()


def _context(seller: str, sessions):
    return service_context.ServiceContext(
        seller_id=seller,
        session_factory=sessions,
        settings=SimpleNamespace(),
        secrets=SimpleNamespace(),
        photos=SimpleNamespace(),
    )


def test_managed_openrouter_cost_is_recorded_for_the_current_seller(db, monkeypatch):
    from anteroom.database import SessionLocal

    monkeypatch.setenv("ANTEROOM_MANAGED_OPENROUTER_KEY", "sk-or-managed")
    with service_context.use(_context(SELLER_A, SessionLocal)):
        api_usage.record_openrouter(
            cost_usd=0.042,
            model="google/gemini-2.5-flash",
            kind="chat",
            provider_request_id="gen-abc",
            input_tokens=100,
            output_tokens=20,
        )
    rows = db.scalars(select(ApiUsageEvent)).all()
    assert len(rows) == 1
    assert rows[0].user_id == SELLER_A
    assert rows[0].provider == "openrouter"
    assert rows[0].cost_usd == pytest.approx(0.042)
    assert rows[0].cost_source == "provider_reported"
    assert rows[0].model_or_edit == "google/gemini-2.5-flash"
    assert rows[0].provider_request_id == "gen-abc"


def test_openrouter_is_not_recorded_without_managed_key(db, monkeypatch):
    from anteroom.database import SessionLocal

    monkeypatch.delenv("ANTEROOM_MANAGED_OPENROUTER_KEY", raising=False)
    with service_context.use(_context(SELLER_A, SessionLocal)):
        api_usage.record_openrouter(cost_usd=0.01, model="x", kind="chat")
    assert db.scalars(select(ApiUsageEvent)).all() == []


def test_managed_photoroom_success_records_rate_card_cost(db, monkeypatch):
    from anteroom.database import SessionLocal

    monkeypatch.setenv(photoroom.MANAGED_KEY_ENV, "sk_pr_managed")
    with service_context.use(_context(SELLER_B, SessionLocal)):
        api_usage.record_photoroom(edit_label="Ghost mannequin, shadow", basic=False)
    rows = db.scalars(select(ApiUsageEvent)).all()
    assert len(rows) == 1
    assert rows[0].user_id == SELLER_B
    assert rows[0].provider == "photoroom"
    assert rows[0].cost_usd == pytest.approx(0.10)
    assert rows[0].cost_source == "rate_card"
    assert rows[0].model_or_edit == "Ghost mannequin, shadow"
    assert rows[0].units == 1


def test_photoroom_cost_env_override(db, monkeypatch):
    from anteroom.database import SessionLocal

    monkeypatch.setenv(photoroom.MANAGED_KEY_ENV, "sk_pr_managed")
    monkeypatch.setenv(api_usage.PHOTOROOM_COST_ENV, "0.08")
    with service_context.use(_context(SELLER_A, SessionLocal)):
        api_usage.record_photoroom(edit_label="Background removed", basic=True)
        api_usage.record_photoroom(edit_label="Ghost mannequin", basic=False)
    by_edit = {row.model_or_edit: row.cost_usd for row in db.scalars(select(ApiUsageEvent)).all()}
    assert by_edit == {"Background removed": pytest.approx(0.016), "Ghost mannequin": pytest.approx(0.08)}


def test_seller_photoroom_key_is_not_metered_as_anteroom_cost(db, monkeypatch):
    from anteroom.database import SessionLocal

    monkeypatch.delenv(photoroom.MANAGED_KEY_ENV, raising=False)
    with service_context.use(_context(SELLER_A, SessionLocal)):
        api_usage.record_photoroom(edit_label="Background removed", basic=True)
    assert db.scalars(select(ApiUsageEvent)).all() == []


def _png() -> bytes:
    from io import BytesIO
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGBA", (40, 40), (10, 20, 30, 255)).save(buffer, "PNG")
    return buffer.getvalue()


@pytest.mark.parametrize(("keep", "cost"), [("", 0.02), ("shirt", 0.10)])
def test_photoroom_edit_records_usage_only_on_managed_success(monkeypatch, db, keep, cost):
    import asyncio
    from anteroom.database import SessionLocal

    monkeypatch.setenv(photoroom.MANAGED_KEY_ENV, "sk_pr_managed")
    photo = _png()

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return httpx.Response(200, content=photo)

    monkeypatch.setattr(photoroom, "_client", lambda timeout: httpx.AsyncClient(transport=Transport(), timeout=timeout))
    with service_context.use(_context(SELLER_A, SessionLocal)):
        result = asyncio.run(photoroom.edit(photo, StudioOptions(remove_background=True, keep=keep)))
    assert result.startswith(b"\x89PNG")
    rows = db.scalars(select(ApiUsageEvent)).all()
    assert len(rows) == 1
    assert rows[0].provider == "photoroom"
    assert rows[0].cost_usd == pytest.approx(cost)


def test_openrouter_provider_persists_reported_cost_when_managed(monkeypatch, db):
    import asyncio
    from anteroom.database import SessionLocal
    from anteroom.providers.chat_completions import ChatCompletionsProvider
    from anteroom.services.ai_key_catalog import KEY_PROVIDERS

    monkeypatch.setenv("ANTEROOM_MANAGED_OPENROUTER_KEY", "sk-or-managed")

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return httpx.Response(200, json={
                "id": "gen-xyz",
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4, "cost": 0.0014},
            })

    monkeypatch.setattr(
        "anteroom.providers.chat_completions.http_client",
        lambda: httpx.AsyncClient(transport=Transport()),
    )
    provider = ChatCompletionsProvider(KEY_PROVIDERS["openrouter"], "sk-or-managed", "openrouter/auto", "openrouter/auto")
    with service_context.use(_context(SELLER_A, SessionLocal)):
        text = asyncio.run(provider._complete([{"role": "user", "content": "hi"}], "openrouter/auto"))
    assert text == "ok"
    row = db.scalars(select(ApiUsageEvent)).one()
    assert row.cost_usd == pytest.approx(0.0014)
    assert row.provider_request_id == "gen-xyz"
    assert row.provider == "openrouter"


def test_operator_report_totals_per_seller(db, monkeypatch):
    from anteroom.database import SessionLocal

    monkeypatch.setenv("ANTEROOM_MANAGED_OPENROUTER_KEY", "sk-or-managed")
    monkeypatch.setenv(photoroom.MANAGED_KEY_ENV, "sk_pr_managed")
    with service_context.use(_context(SELLER_A, SessionLocal)):
        api_usage.record_openrouter(cost_usd=0.02, model="m", kind="chat")
        api_usage.record_photoroom(edit_label="Virtual model", basic=False)
    with service_context.use(_context(SELLER_B, SessionLocal)):
        api_usage.record_openrouter(cost_usd=0.05, model="m", kind="chat")

    report = api_usage.operator_report(db, since=datetime.now(UTC) - timedelta(hours=1), until=datetime.now(UTC) + timedelta(hours=1))
    by_seller = {row["seller_id"]: row for row in report["sellers"]}
    assert by_seller[SELLER_A]["openrouter_usd"] == pytest.approx(0.02)
    assert by_seller[SELLER_A]["photoroom_usd"] == pytest.approx(0.10)
    assert by_seller[SELLER_A]["total_usd"] == pytest.approx(0.12)
    assert by_seller[SELLER_A]["openrouter_calls"] == 1
    assert by_seller[SELLER_A]["photoroom_calls"] == 1
    assert by_seller[SELLER_B]["total_usd"] == pytest.approx(0.05)
    assert report["total_usd"] == pytest.approx(0.17)


def test_operator_usage_route_requires_operator(db, monkeypatch):
    from anteroom.routes import operator_usage

    monkeypatch.setenv("ANTEROOM_OPERATOR_USER_ID", "operator")
    monkeypatch.setattr(operator_usage, "_report_db", lambda: db)
    app = FastAPI()

    @app.middleware("http")
    async def identify(request: Request, call_next):
        if seller := request.headers.get("x-test-seller"):
            request.state.seller = SimpleNamespace(user_id=seller)
        return await call_next(request)

    app.include_router(operator_usage.router)
    client = TestClient(app)
    assert client.get("/api/operator/usage").status_code == 403
    assert client.get("/api/operator/usage", headers={"x-test-seller": "other"}).status_code == 403

    db.add(ApiUsageEvent(
        user_id=SELLER_A, provider="openrouter", kind="chat", model_or_edit="m",
        cost_usd=0.03, cost_source="provider_reported", units=None,
    ))
    db.commit()
    response = client.get("/api/operator/usage", headers={"x-test-seller": "operator"})
    assert response.status_code == 200
    body = response.json()
    assert body["sellers"][0]["seller_id"] == SELLER_A
    assert body["sellers"][0]["total_usd"] == pytest.approx(0.03)

    csv = client.get("/api/operator/usage", headers={"x-test-seller": "operator"}, params={"format": "csv"})
    assert csv.status_code == 200
    assert "text/csv" in csv.headers["content-type"]
    assert SELLER_A in csv.text
    assert "0.03" in csv.text


def test_operator_usage_route_is_served_by_the_hosted_app(db, monkeypatch):
    from anteroom.hosted import create_app
    from anteroom.routes import operator_usage
    from anteroom.services.seller_sessions import SellerIdentity

    monkeypatch.delenv("ANTEROOM_BACKGROUND_WORKER", raising=False)
    monkeypatch.setenv("ANTEROOM_OPERATOR_USER_ID", SELLER_B)
    monkeypatch.setattr(operator_usage, "_report_db", lambda: db)
    verifier = SimpleNamespace(verify=lambda token: SellerIdentity(user_id=token))
    with TestClient(create_app("https://example.supabase.co", session_verifier=verifier)) as client:
        assert client.get("/api/operator/usage").status_code == 401
        assert client.get("/api/operator/usage", headers={"Authorization": f"Bearer {SELLER_A}"}).status_code == 403
        response = client.get("/api/operator/usage", headers={"Authorization": f"Bearer {SELLER_B}"})
    assert response.status_code == 200
    assert response.json()["sellers"] == []
