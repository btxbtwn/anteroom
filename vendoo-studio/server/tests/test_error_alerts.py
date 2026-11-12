"""Operator error emails: structured, grouped, scrubbed and never blocking."""
import logging
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from vendoo_studio.services import error_alerts
from vendoo_studio.services.error_alerts import AlertHandler, build_report, render_email, send_email


FAKE_KEY = "-".join(("fake", "value", "123456789"))


@pytest.fixture(autouse=True)
def email_store(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from vendoo_studio.repositories import error_email

    engine = create_engine(f"sqlite:///{tmp_path / 'email.db'}")
    store = error_email.EmailStore(engine)
    store.table.create(engine)
    with engine.begin() as connection:
        connection.execute(store.table.insert().values(id=1, payload={}))
    monkeypatch.setattr(error_email, 'EmailStore', lambda _engine: store)
    monkeypatch.setenv('ANTEROOM_ERROR_EMAIL_DAILY_LIMIT', '100')
    yield store
    engine.dispose()


def record_with_exception(message="Draft save failed", error=None):
    try:
        raise error or RuntimeError(f"could not save listing api_key={FAKE_KEY}")
    except Exception:
        return logging.LogRecord("vendoo_studio.jobs", logging.ERROR, __file__, 12, message, (), sys.exc_info())


def test_report_has_type_message_stack_location_and_release():
    report = build_report(record_with_exception())
    assert report["exception"]["type"] == "RuntimeError"
    assert "could not save listing" in report["exception"]["message"]
    assert "record_with_exception" in report["stack"]
    assert report["location"].startswith("test_error_alerts.py:")
    assert report["environment"]["release"]
    assert report["title"].startswith("RuntimeError: could not save listing")


def test_secrets_and_sql_parameters_are_removed():
    report = build_report(record_with_exception())
    assert FAKE_KEY not in str(report)
    sql = RuntimeError("(psycopg.errors.X) boom\n[SQL: UPDATE listings SET title=%(t)s]\n"
                       "[parameters: {'t': 'Seller private title'}]\n(Background on this error at: x)")
    report = build_report(record_with_exception(error=sql))
    assert "Seller private title" not in str(report)
    assert "UPDATE listings" in report["exception"]["message"]


def test_same_failure_with_different_messages_shares_a_fingerprint():
    first = build_report(record_with_exception(error=ValueError("listing 1")))
    second = build_report(record_with_exception(error=ValueError("listing 2")))
    assert first["fingerprint"] == second["fingerprint"]


def test_request_context_names_route_request_and_seller():
    scope = {"method": "POST", "route": SimpleNamespace(path="/api/drafts/{draft_id}"),
             "state": {"seller": SimpleNamespace(user_id="seller-1")}}
    token = error_alerts.request_context.set({"scope": scope, "request_id": "req-9"})
    try:
        report = build_report(record_with_exception())
    finally:
        error_alerts.request_context.reset(token)
    assert report["request"] == {"method": "POST", "route": "/api/drafts/{draft_id}",
                                 "request_id": "req-9", "seller_id": "seller-1"}
    subject, body = render_email(report, 0, None)
    assert "POST /api/drafts/{draft_id}" in body and "seller-1" in body


def test_distinct_handled_errors_at_one_call_site_are_not_grouped_together():
    def failure(message):
        return build_report(logging.LogRecord("vendoo_studio.jobs", logging.ERROR, __file__, 1, message, (), None))

    assert failure("Job 123: network unavailable")["fingerprint"] != failure("Job 456: marketplace refused")["fingerprint"]
    assert failure("Job 123: network unavailable")["fingerprint"] == failure("Job 456: network unavailable")["fingerprint"]


def test_anteroom_ids_in_a_handled_error_do_not_split_its_group():
    def failure(*args):
        return build_report(logging.LogRecord("vendoo_studio.jobs", logging.ERROR, __file__, 1,
                                              "Job %s failed at %s (seller %s): %s", args, None))

    first = failure("e9b3c7c2227b", "fill", "4f1a9c0d2b7e4c5d8e6f0a1b2c3d4e5f", "network unavailable")
    second = failure("80678405bba2", "fill", "0a1b2c3d4e5f4a5b8c7d9e0f1a2b3c4d", "network unavailable")
    assert first["fingerprint"] == second["fingerprint"]
    assert first["fingerprint"] != failure("80678405bba2", "fill", "x", "marketplace refused")["fingerprint"]


def test_browser_reports_carry_page_and_release():
    record = logging.LogRecord("vendoo_studio.browser", logging.ERROR, __file__, 1, "Browser crash", (), None)
    record.browser = {"kind": "crash", "name": "TypeError", "message": "x is undefined at row 42",
                      "stack": "at Row (app.js:1:2)", "path": "/inventory", "user_agent": "Safari", "release": "0.1.840"}
    report = build_report(record)
    assert report["exception"] == {"type": "TypeError", "message": "x is undefined at row 42"}
    assert report["location"] == "/inventory"
    subject, body = render_email(report, 0, None)
    assert subject.startswith("[Anteroom local] ERROR TypeError: x is undefined")
    assert "web release 0.1.840" in body


def test_background_reports_include_the_seller_context():
    with patch("vendoo_studio.services.service_context.current", return_value=SimpleNamespace(seller_id="seller-background")):
        report = build_report(record_with_exception())
    assert report["request"]["seller_id"] == "seller-background"


def test_handled_http_refusal_is_logged_without_private_url_or_body():
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient
    from vendoo_studio.hosted import AccessLogMiddleware

    app = FastAPI()

    @app.get("/api/items/{item_id}")
    def refused(item_id: str):
        return JSONResponse({"detail": "private listing detail"}, status_code=409)

    app.add_middleware(AccessLogMiddleware)
    with patch.object(logging.getLogger("vendoo_studio.http"), "warning") as warning:
        assert TestClient(app).get("/api/items/private-item?token=secret").status_code == 409
    message, *args = warning.call_args.args
    assert message % tuple(args) == "GET /api/items/{item_id} answered 409"


def test_email_goes_to_operator_over_tls():
    with patch("smtplib.SMTP") as factory:
        send_email("subject", "body")
    factory.assert_called_once_with("smtp-relay.gmail.com", 587, local_hostname="myanteroom.com", timeout=20)
    smtp = factory.return_value.__enter__.return_value
    smtp.starttls.assert_called_once()
    message = smtp.send_message.call_args.args[0]
    assert message["To"] == "ccanizares@myanteroom.com"


def test_handled_browser_failure_reaches_email_worker():
    import threading
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from vendoo_studio.routes.error_reports import router

    delivered = threading.Event()
    messages = []

    def send(subject, body):
        messages.append((subject, body))
        delivered.set()

    h = AlertHandler(send)
    logger = logging.getLogger("vendoo_studio.browser")
    logger.addHandler(h)
    app = FastAPI()
    app.include_router(router)
    try:
        response = TestClient(app).post("/api/error-reports", json={
            "kind": "mutation", "name": "Error", "message": "Offer refused token=private-value", "path": "/automations",
        })
        assert response.status_code == 202
        assert delivered.wait(3)
        assert "Offer refused" in messages[0][1]
        assert "private-value" not in messages[0][1]
        assert "mutation on /automations" in messages[0][1]
    finally:
        logger.removeHandler(h)
        h.close()


@pytest.mark.parametrize('seller,operator,visible', [
    ('seller', 'operator', False), ('operator', '', False), ('operator', 'operator', True),
])
def test_delivery_status_is_only_visible_to_the_configured_operator(monkeypatch, seller, operator, visible):
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from vendoo_studio.routes.error_reports import router

    monkeypatch.setenv('ANTEROOM_OPERATOR_USER_ID', operator)
    monkeypatch.setattr(error_alerts, '_handler', SimpleNamespace(
        delivery=SimpleNamespace(status=lambda: {'state': 'quota_paused', 'pending': 3}), dropped=0))
    app = FastAPI()

    @app.middleware('http')
    async def identify(request: Request, call_next):
        request.state.seller = SimpleNamespace(user_id=seller)
        return await call_next(request)

    app.include_router(router)
    response = TestClient(app).get('/api/error-reports/delivery')
    assert response.status_code == 200
    data = response.json()
    assert data['visible'] == visible
    assert ('pending' in data) == visible


def handler(send, clock=lambda: 0.0):
    return AlertHandler(send, clock=clock)


def test_repeats_are_grouped_then_counted_in_the_next_email():
    sent, now = [], [0.0]
    h = handler(lambda s, b: sent.append((s, b)), clock=lambda: now[0])
    for _ in range(5):
        h.delivery.add(build_report(record_with_exception()))
        h.delivery.tick()
    assert len(sent) == 1
    now[0] = 901
    h.delivery.add(build_report(record_with_exception()))
    h.delivery.tick()
    assert len(sent) == 2 and "(+4 more)" in sent[1][0]
    assert "Occurrences  5 since" in sent[1][1]
    h.close()


def test_shutdown_never_bypasses_budget_or_grouping():
    sent = []
    h = handler(lambda s, b: sent.append(s))
    for _ in range(3):
        h.delivery.add(build_report(record_with_exception()))
        h.delivery.tick()
    h.close()
    assert len(sent) == 1
    assert h.delivery.status()['pending'] == 2


def test_failed_delivery_is_retained_for_later_retry():
    attempts = []

    def flaky(subject, body):
        attempts.append(subject)
        if len(attempts) < 3:
            raise OSError("relay down")

    h = handler(flaky)
    h.delivery.add(build_report(record_with_exception()))
    h.delivery.tick()
    assert len(attempts) == 1
    assert h.delivery.status()['pending'] == 1
    assert h.delivery.status()['state'] == 'delivery_failed'
    h.close()


def test_hourly_cap_stops_a_flood_and_says_so():
    sent, now = [], [0.0]
    h = handler(lambda s, b: sent.append(b), clock=lambda: now[0])
    for i in range(65):
        record = logging.LogRecord("vendoo_studio.x", logging.ERROR, f"/f{i}.py", i, "m", (), None)
        h.delivery.add(build_report(record))
        h.delivery.tick()
    assert len(sent) == 60
    now[0] = 3601
    h.delivery.tick()
    assert '5 occurrences' in sent[-1]
    h.close()


def test_reporter_ignores_its_own_logs_and_uvicorns_duplicate():
    h = handler(lambda s, b: None)
    own = logging.LogRecord("vendoo_studio.services.error_alerts", logging.ERROR, __file__, 1, "x", (), None)
    dup = logging.LogRecord("uvicorn.error", logging.ERROR, __file__, 1, "Exception in ASGI application\n", (), None)
    other = logging.LogRecord("uvicorn.error", logging.ERROR, __file__, 1, "lifespan failed", (), None)
    assert not h.filter(own) and not h.filter(dup) and h.filter(other)
    h.close()


def test_warnings_are_emailed():
    h = handler(lambda s, b: None)
    assert h.level == logging.WARNING
    h.close()


@pytest.mark.parametrize("env", [{}, {"ANTEROOM_ERROR_EMAIL": "1", "ANTEROOM_SANDBOX": "1"}])
def test_disabled_or_sandbox_never_sends(monkeypatch, env):
    monkeypatch.delenv("ANTEROOM_ERROR_EMAIL", raising=False)
    monkeypatch.delenv("ANTEROOM_SANDBOX", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    with patch.object(error_alerts, "AlertHandler") as factory:
        assert error_alerts.install_alerts() is None
    factory.assert_not_called()


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_enabled_install_reports_errors_and_uncaught_thread_exceptions(monkeypatch):
    import threading

    monkeypatch.setenv("ANTEROOM_ERROR_EMAIL", "1")
    monkeypatch.delenv("ANTEROOM_SANDBOX", raising=False)
    queued = []
    monkeypatch.setattr(AlertHandler, "emit", lambda self, record: queued.append(record))
    hooks = sys.excepthook, threading.excepthook
    installed = error_alerts.install_alerts()
    try:
        assert installed is not None
        logging.getLogger("vendoo_studio.jobs").error("boom")
        logging.getLogger("vendoo_studio.jobs").warning("handled failure")
        worker = threading.Thread(target=lambda: 1 / 0, name="broken")
        worker.start()
        worker.join()
    finally:
        monkeypatch.setattr(installed.delivery, "send", lambda s, b: None)
        error_alerts.uninstall_alerts()
    assert (sys.excepthook, threading.excepthook) == hooks
    assert [r.getMessage() for r in queued] == ["boom", "handled failure", "Uncaught exception in thread broken"]


def test_browser_route_logs_structured_report():
    from pydantic import ValidationError
    from vendoo_studio.routes.error_reports import BrowserFailure, report

    with pytest.raises(ValidationError):
        BrowserFailure.model_validate({"kind": "crash", "unexpected": "x"})
    with patch.object(logging.getLogger("vendoo_studio.browser"), "error") as error:
        report(BrowserFailure(kind="render", name="TypeError", message="bad", path="/inventory"))
    assert error.call_args.kwargs["extra"]["browser"]["path"] == "/inventory"


def test_route_crash_is_reported_once_with_request_context():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from vendoo_studio.hosted import AccessLogMiddleware

    app = FastAPI()

    @app.get("/api/items/{item_id}")
    def broken(item_id: str):
        raise KeyError("missing")

    @app.get("/api/unavailable")
    def unavailable():
        from fastapi.responses import JSONResponse
        return JSONResponse({}, status_code=503)

    app.add_middleware(AccessLogMiddleware)
    reports = []
    handler = logging.Handler(logging.ERROR)
    handler.emit = lambda record: reports.append(build_report(record))
    logging.getLogger("vendoo_studio.http").addHandler(handler)
    try:
        client = TestClient(app, raise_server_exceptions=False)
        assert client.get("/api/items/abc?code=secret", headers={"fly-request-id": "r-1"}).status_code == 500
        assert client.get("/api/unavailable").status_code == 503
    finally:
        logging.getLogger("vendoo_studio.http").removeHandler(handler)
    crash, unavailable_report = reports
    assert crash["exception"]["type"] == "KeyError"
    assert crash["request"] == {"method": "GET", "route": "/api/items/{item_id}", "request_id": "r-1", "seller_id": None}
    assert "secret" not in str(crash)
    assert unavailable_report["message"] == "GET /api/unavailable answered 503"


def test_unavailable_answer_reports_the_failure_behind_it():
    """A 503 from a pool timeout names the timeout, not only the status."""
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.exc import TimeoutError as PoolTimeout
    from vendoo_studio.hosted import AccessLogMiddleware
    from vendoo_studio.services import hosted_database
    from vendoo_studio.services.seller_sessions import SellerIdentity

    app = FastAPI()

    @app.middleware("http")
    async def seller(request, call_next):
        request.scope.setdefault("state", {})["seller"] = SellerIdentity("aaa1abaf-ee00-4d3a-9d73-c20fb0a6f267")
        return await call_next(request)

    @app.get("/api/settings/ui")
    def ui(db=Depends(hosted_database.get_seller_db, scope="function")):
        return {}

    app.add_middleware(AccessLogMiddleware)

    def exhausted(**_):
        raise PoolTimeout("QueuePool limit of size 8 overflow 4 reached, connection timed out, timeout 3.00")

    reports = []
    handler = logging.Handler(logging.ERROR)
    handler.emit = lambda record: reports.append(build_report(record))
    logging.getLogger("vendoo_studio.http").addHandler(handler)
    postgres = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    try:
        with patch("vendoo_studio.database.engine", postgres), \
                patch.object(hosted_database, "SellerDatabaseSession", exhausted):
            response = TestClient(app, raise_server_exceptions=False).get("/api/settings/ui")
    finally:
        logging.getLogger("vendoo_studio.http").removeHandler(handler)
    assert response.status_code == 503
    assert response.json() == {"detail": "Cloud data is temporarily unavailable"}
    [report] = reports
    assert report["message"] == "GET /api/settings/ui answered 503"
    assert report["exception"]["type"] == "sqlalchemy.exc.TimeoutError"
    assert "QueuePool limit" in report["exception"]["message"]
    assert report["request"]["route"] == "/api/settings/ui"
