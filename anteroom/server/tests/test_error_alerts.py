"""Operator error emails: structured, grouped, scrubbed and never blocking."""
import logging
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from anteroom.services import error_alerts
from anteroom.services.error_alerts import AlertHandler, build_report, render_email, send_email


FAKE_KEY = "-".join(("fake", "value", "123456789"))


@pytest.fixture(autouse=True)
def email_store(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from anteroom.repositories import error_email

    engine = create_engine(f"sqlite:///{tmp_path / 'email.db'}")
    store = error_email.EmailStore(engine)
    store.table.create(engine)
    store.groups.create(engine)
    with engine.begin() as connection:
        connection.execute(store.table.insert().values(id=1, payload={}))
    monkeypatch.setattr(error_email, 'EmailStore', lambda _engine: store)
    monkeypatch.setenv('ANTEROOM_ERROR_EMAIL_DAILY_LIMIT', '100')
    monkeypatch.setenv('ANTEROOM_ERROR_EMAIL_LIFECYCLE', '1')
    yield store
    engine.dispose()


def record_with_exception(message="Draft save failed", error=None):
    try:
        raise error or RuntimeError(f"could not save listing api_key={FAKE_KEY}")
    except Exception:
        return logging.LogRecord("anteroom.jobs", logging.ERROR, __file__, 12, message, (), sys.exc_info())


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
        return build_report(logging.LogRecord("anteroom.jobs", logging.ERROR, __file__, 1, message, (), None))

    assert failure("Job 123: network unavailable")["fingerprint"] != failure("Job 456: marketplace refused")["fingerprint"]
    assert failure("Job 123: network unavailable")["fingerprint"] == failure("Job 456: network unavailable")["fingerprint"]


def test_anteroom_ids_in_a_handled_error_do_not_split_its_group():
    def failure(*args):
        return build_report(logging.LogRecord("anteroom.jobs", logging.ERROR, __file__, 1,
                                              "Job %s failed at %s (seller %s): %s", args, None))

    first = failure("e9b3c7c2227b", "fill", "4f1a9c0d2b7e4c5d8e6f0a1b2c3d4e5f", "network unavailable")
    second = failure("80678405bba2", "fill", "0a1b2c3d4e5f4a5b8c7d9e0f1a2b3c4d", "network unavailable")
    assert first["fingerprint"] == second["fingerprint"]
    assert first["fingerprint"] != failure("80678405bba2", "fill", "x", "marketplace refused")["fingerprint"]


def test_browser_reports_carry_page_and_release():
    record = logging.LogRecord("anteroom.browser", logging.ERROR, __file__, 1, "Browser crash", (), None)
    record.browser = {"kind": "crash", "name": "TypeError", "message": "x is undefined at row 42",
                      "stack": "at Row (app.js:1:2)", "path": "/inventory", "user_agent": "Safari", "release": "0.1.840"}
    report = build_report(record)
    assert report["exception"] == {"type": "TypeError", "message": "x is undefined at row 42"}
    assert report["location"] == "/inventory"
    subject, body = render_email(report, 0, None)
    assert subject.startswith("[Anteroom local] ERROR TypeError: x is undefined")
    assert "web release 0.1.840" in body


def test_background_reports_include_the_seller_context():
    with patch("anteroom.services.service_context.current", return_value=SimpleNamespace(seller_id="seller-background")):
        report = build_report(record_with_exception())
    assert report["request"]["seller_id"] == "seller-background"


def test_handled_http_refusal_is_logged_without_private_url_or_body():
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient
    from anteroom.hosted import AccessLogMiddleware

    app = FastAPI()

    @app.get("/api/items/{item_id}")
    def refused(item_id: str):
        return JSONResponse({"detail": "private listing detail"}, status_code=409)

    app.add_middleware(AccessLogMiddleware)
    with patch.object(logging.getLogger("anteroom.http"), "warning") as warning:
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
    from anteroom.routes.error_reports import router

    delivered = threading.Event()
    messages = []

    def send(subject, body):
        messages.append((subject, body))
        delivered.set()

    h = AlertHandler(send)
    logger = logging.getLogger("anteroom.browser")
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
    from anteroom.routes.error_reports import router

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


@pytest.fixture
def groups_client(monkeypatch, email_store):
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from anteroom.routes import error_reports

    monkeypatch.setattr(error_reports, 'EmailStore', lambda _engine: email_store)
    monkeypatch.setenv('ANTEROOM_OPERATOR_USER_ID', 'operator')
    app = FastAPI()

    @app.middleware('http')
    async def identify(request: Request, call_next):
        if seller := request.headers.get('x-test-seller'):
            request.state.seller = SimpleNamespace(user_id=seller)
        return await call_next(request)

    app.include_router(error_reports.router)
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize('seller', [None, 'another-seller'])
def test_group_endpoints_require_operator(groups_client, seller):
    headers = {'x-test-seller': seller} if seller else {}
    path = '/api/error-reports/groups'
    assert groups_client.get(path, headers=headers).status_code == 403
    for action in ('resolve', 'mute', 'reopen'):
        assert groups_client.post(f'{path}/ERROR/abcdef/{action}', headers=headers).status_code == 403


def test_operator_triage_is_unavailable_until_senders_activate_it(groups_client):
    headers = {'x-test-seller': 'operator'}
    path = '/api/error-reports/groups'
    assert groups_client.get(path, headers=headers).status_code == 503
    for action in ('resolve', 'mute', 'reopen'):
        assert groups_client.post(f'{path}/ERROR/abcdef/{action}', headers=headers).status_code == 503


def test_operator_can_list_and_triage_groups(groups_client, email_store, monkeypatch):
    from anteroom.services.error_email_delivery import EmailDelivery

    monkeypatch.delenv('ANTEROOM_RELEASE', raising=False)
    monkeypatch.setenv('FLY_IMAGE_REF', 'registry.fly.io/anteroom:v1.2.9-deadbeef')
    delivery = EmailDelivery(email_store, lambda *_: None, daily_limit=0)
    item = build_report(record_with_exception())
    delivery.add(item)
    delivery.add(item)
    warning = build_report(logging.LogRecord('anteroom.jobs', logging.WARNING, '/jobs.py', 6, 'warning', (), None))
    delivery.add(warning)

    path = '/api/error-reports/groups'
    target = f"{path}/ERROR/{item['fingerprint']}"
    headers = {'x-test-seller': 'operator'}
    rows = groups_client.get(path, headers=headers, params={'sort': 'count'}).json()
    assert [row['severity'] for row in rows] == ['ERROR', 'WARNING']
    assert rows[0]['count'] == 2 and rows[0]['headline'] == item['title']
    assert rows[0]['first_seen'].endswith(('Z', '+00:00'))
    assert rows[0]['last_seen'] == rows[0]['first_seen']
    assert rows[0]['last_release'] == item['environment']['release']
    assert groups_client.get(path, headers=headers, params={'severity': 'WARNING'}).json() == rows[1:]
    assert groups_client.post(f'{target}/resolve', headers=headers).json() == {'status': 'resolved'}
    resolved = groups_client.get(path, headers=headers, params={'status': 'resolved'}).json()[0]
    assert resolved['resolved_release'] == 'registry.fly.io/anteroom:v1.2.9-deadbeef'
    assert groups_client.post(f'{target}/mute', headers=headers, json={'duration_hours': 0}).status_code == 422
    assert groups_client.post(f'{target}/mute', headers=headers,
                              json={'duration_hours': 168, 'note': 'Known outage'}).json() == {'status': 'muted'}
    row = groups_client.get(path, headers=headers, params={'status': 'muted'}).json()[0]
    assert row['muted_until'] and row['note'] == 'Known outage'
    assert groups_client.post(f'{target}/reopen', headers=headers).json() == {'status': 'open'}
    reopened = groups_client.get(path, headers=headers, params={'severity': 'ERROR'}).json()[0]
    assert reopened['muted_until'] is None and reopened['resolved_release'] is None
    assert groups_client.post(f'{path}/ERROR/missing/reopen', headers=headers).status_code == 404


def handler(send, clock=lambda: 0.0):
    return AlertHandler(send, clock=clock)


def test_overdue_timestamps_and_ids_keep_one_group_per_marketplace():
    def overdue(marketplace, identity, due, last):
        return build_report(logging.LogRecord('anteroom.promotion_automations', logging.ERROR,
            '/promotion_automations.py', 497,
            '%s offer automation %s is overdue (seller %s, due %s, last run %s). Check the scheduler and Chrome connection.',
            (marketplace, identity, identity, due, last), None))

    first = overdue('poshmark', 'a9b19dd3-7616-473a-b68f-123456789012',
                    '2026-11-15 02:20:44.123456', '2026-11-14 01:12:30.654321')
    later = overdue('poshmark', 'b8c28ee4-8727-484b-c79a-234567890123',
                    '2026-11-16 22:44:34.932766', '2026-11-15 03:45:03.012345')
    distinct = overdue('depop', 'b8c28ee4-8727-484b-c79a-234567890123',
                       '2026-11-16 22:44:34.932766', '2026-11-15 03:45:03.012345')
    assert first['fingerprint'] == later['fingerprint']
    assert first['fingerprint'] != distinct['fingerprint']
    assert first['message'] != later['message']  # Full latest diagnostics remain visible.


def test_repeats_are_grouped_then_counted_in_the_next_email():
    sent, now = [], [0.0]
    h = handler(lambda s, b: sent.append((s, b)), clock=lambda: now[0])
    for _ in range(5):
        h.delivery.add(build_report(record_with_exception()))
        h.delivery.tick()
    assert len(sent) == 1
    now[0] = 3601
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
        record = logging.LogRecord("anteroom.x", logging.ERROR, f"/f{i}.py", i, "m", (), None)
        h.delivery.add(build_report(record))
        h.delivery.tick()
    assert len(sent) == 60
    now[0] = 3601
    h.delivery.tick()
    assert '5 occurrences' in sent[-1]
    h.close()


def test_reporter_ignores_its_own_logs_and_uvicorns_duplicate():
    h = handler(lambda s, b: None)
    own = logging.LogRecord("anteroom.services.error_alerts", logging.ERROR, __file__, 1, "x", (), None)
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
        logging.getLogger("anteroom.jobs").error("boom")
        logging.getLogger("anteroom.jobs").warning("handled failure")
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
    from anteroom.routes.error_reports import BrowserFailure, report

    with pytest.raises(ValidationError):
        BrowserFailure.model_validate({"kind": "crash", "unexpected": "x"})
    with patch.object(logging.getLogger("anteroom.browser"), "error") as error:
        report(BrowserFailure(kind="render", name="TypeError", message="bad", path="/inventory"))
    assert error.call_args.kwargs["extra"]["browser"]["path"] == "/inventory"


def test_route_crash_is_reported_once_with_request_context():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from anteroom.hosted import AccessLogMiddleware

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
    logging.getLogger("anteroom.http").addHandler(handler)
    try:
        client = TestClient(app, raise_server_exceptions=False)
        assert client.get("/api/items/abc?code=secret", headers={"fly-request-id": "r-1"}).status_code == 500
        assert client.get("/api/unavailable").status_code == 503
    finally:
        logging.getLogger("anteroom.http").removeHandler(handler)
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
    from anteroom.hosted import AccessLogMiddleware
    from anteroom.services import hosted_database
    from anteroom.services.seller_sessions import SellerIdentity

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
    logging.getLogger("anteroom.http").addHandler(handler)
    postgres = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    try:
        with patch("anteroom.database.engine", postgres), \
                patch.object(hosted_database, "SellerDatabaseSession", exhausted):
            response = TestClient(app, raise_server_exceptions=False).get("/api/settings/ui")
    finally:
        logging.getLogger("anteroom.http").removeHandler(handler)
    assert response.status_code == 503
    assert response.json() == {"detail": "Cloud data is temporarily unavailable"}
    [report] = reports
    assert report["message"] == "GET /api/settings/ui answered 503"
    assert report["exception"]["type"] == "sqlalchemy.exc.TimeoutError"
    assert "QueuePool limit" in report["exception"]["message"]
    assert report["request"]["route"] == "/api/settings/ui"


def test_promotion_preview_refusals_group_across_listings_but_keep_the_failure(email_store):
    from anteroom.services.error_email_delivery import EmailDelivery

    def preview(listing, automation, reason='Depop refused preview', marketplace='depop',
                explanation='marketplace explanation', logger='anteroom.listing_promotions', action='offer'):
        record = logging.LogRecord(logger, logging.ERROR, 'listing_promotions.py', 265,
                                  '%s %s preview rejected for listing %s, automation %s: %s; %s',
                                  (marketplace, action, listing, automation, reason, explanation), None)
        return build_report(record)

    first = preview('seller-vintage-shirt-abcd', 'aabb12345678')
    second = preview('seller-cotton-trousers-efab', 'ccdd87654321')
    assert first['fingerprint'] == second['fingerprint']
    assert 'seller-vintage-shirt-abcd' in first['message']
    assert 'seller-cotton-trousers-efab' in second['message']
    assert first['fingerprint'] != preview('seller-vintage-shirt-abcd', 'aabb12345678', 'Account disconnected')['fingerprint']
    assert first['fingerprint'] != preview('seller-vintage-shirt-abcd', 'aabb12345678', marketplace='mercari')['fingerprint']
    assert first['fingerprint'] != preview('seller-vintage-shirt-abcd', 'aabb12345678', action='discount')['fingerprint']
    assert first['fingerprint'] != preview('seller-vintage-shirt-abcd', 'aabb12345678', explanation='Not authorized')['fingerprint']
    assert preview('one-shirt', 'aabb12345678', logger='anteroom.other')['fingerprint'] != preview(
        'two-shirt', 'aabb12345678', logger='anteroom.other')['fingerprint']
    sent = []
    delivery = EmailDelivery(email_store, lambda subject, body: sent.append((subject, body)), daily_limit=10)
    delivery.add(first)
    delivery.tick()
    restarted = EmailDelivery(email_store, lambda subject, body: sent.append((subject, body)), daily_limit=10)
    restarted.add(second)
    restarted.tick()
    assert len(sent) == 1
    assert restarted.status()['pending'] == 1
    [group] = email_store.read()['groups'].values()
    assert group['report']['message'] == second['message']


@pytest.mark.parametrize('hours', [46, 47])
def test_mercari_cooldown_review_is_info_at_both_layers_and_does_not_queue_email(email_store, monkeypatch, caplog, hours):
    import asyncio
    from unittest.mock import AsyncMock, Mock
    from anteroom.models.listing_promotion import ListingPromotion
    from anteroom.services import browser_bridge, listing_promotions

    result = {'ok': False, 'error': 'Mercari refused the request.', 'error_code': 'rejected', 'safe_to_retry': True,
              'marketplace_message': f'You can make an offer again in {hours} hours. (once every 3 days)'}
    request = AsyncMock(return_value=result)
    monkeypatch.setattr(browser_bridge, '_request', request)
    row = ListingPromotion(marketplace='mercari', action='offer', batch_id='fixture', discount_percent=12, floor_cents=1)
    send = Mock()
    alerts = AlertHandler(send=send, store=email_store)
    queued = []
    monkeypatch.setattr(alerts, 'emit', lambda record: queued.append(build_report(record)))
    logger = logging.getLogger('anteroom')
    logger.addHandler(alerts)
    try:
        with caplog.at_level(logging.INFO, logger='anteroom'), pytest.raises(listing_promotions.PromotionError) as raised:
            asyncio.run(listing_promotions._review_remote(row, 'fixture-listing'))
        assert raised.value.code == 'eligibility_cooldown'
        records = [record for record in caplog.records if record.name in
                   ('anteroom.marketplaces', 'anteroom.listing_promotions')]
        assert [(record.name, record.levelname) for record in records] == [
            ('anteroom.marketplaces', 'INFO'), ('anteroom.listing_promotions', 'INFO')]
        assert all(result['marketplace_message'] in record.getMessage() for record in records)
        assert queued == []  # The real AlertHandler's severity threshold must filter both records.
        send.assert_not_called()
        assert row.marketplace_message == result['marketplace_message']
        assert row.sent_at is None and row.offer_price_cents is None and row.confirmation is None
        request.assert_awaited_once()
    finally:
        logger.removeHandler(alerts)
        alerts.close()



def _driver_failure(route_file, route_name, parameter, depth=10):
    """A driver error raised deep inside library code, called from one Anteroom site.

    Code is compiled under realistic file names so the traceback carries the
    same paths a production stack does, without importing the real libraries.
    """
    class QueryCanceled(Exception):
        pass

    QueryCanceled.__module__, QueryCanceled.__qualname__ = 'psycopg.errors', 'QueryCanceled'
    library = {'QueryCanceled': QueryCanceled}
    source = "def call_0(value):\n    raise QueryCanceled('canceling statement due to statement timeout: ' + value)\n"
    for level in range(1, depth):
        source += f"def call_{level}(value):\n    return call_{level - 1}(value)\n"
    exec(compile(source, '/app/.venv/lib/python3.12/site-packages/sqlalchemy/engine/base.py', 'exec'), library)
    route = {'execute': library[f'call_{depth - 1}']}
    exec(compile(f"def {route_name}(value):\n    return execute(value)\n",
                 f'/app/anteroom/server/anteroom/{route_file}', 'exec'), route)
    try:
        route[route_name](parameter)
    except QueryCanceled:
        record = logging.LogRecord('anteroom.http', logging.ERROR, '/hosted.py', 1,
                                   'Unhandled exception in GET %s', ('/api/x',), sys.exc_info())
    return build_report(record)


def test_driver_errors_from_different_app_operations_get_separate_groups():
    # With more than eight library frames, the kept frames are all library
    # code, so every route that hit the same driver error shared one group.
    catalog = _driver_failure('routes/catalog.py', 'categories', "marketplace = 'ebay'")
    inventory = _driver_failure('routes/hosted_workspace.py', 'inventory_response', "marketplace = 'ebay'")
    assert catalog['exception']['type'] == inventory['exception']['type'] == 'psycopg.errors.QueryCanceled'
    assert catalog['fingerprint'] != inventory['fingerprint']


def test_repeats_from_one_app_operation_share_a_group_whatever_the_sql_values():
    first = _driver_failure('routes/catalog.py', 'categories', "marketplace = 'ebay'")
    again = _driver_failure('routes/catalog.py', 'categories', "marketplace = 'depop', seller 'a9b19dd3'")
    assert first['fingerprint'] == again['fingerprint']


def test_groups_that_already_show_an_app_frame_keep_their_fingerprint():
    # A shallow library stack already keeps the Anteroom frame among its last
    # eight. Its fingerprint must be the pre-fix one, so only collided groups split.
    import hashlib

    report = _driver_failure('routes/catalog.py', 'categories', "marketplace = 'ebay'", depth=3)
    frames = [line.strip() for line in report['stack'].splitlines() if line.strip().startswith('File "')]
    named = tuple((line.split('"')[1].rsplit('/', 1)[-1], line.rsplit(' in ', 1)[1]) for line in frames)
    expected = hashlib.sha256(repr(('psycopg.errors.QueryCanceled', named[-8:])).encode()).hexdigest()[:12]
    assert report['fingerprint'] == expected


def test_pool_timeouts_through_a_session_keep_one_group_whatever_the_call_site():
    # A pool timeout reached through a session sits under a dozen library
    # frames. It must keep the pre-fix fingerprint, one group per incident,
    # not split per Anteroom function that happened to wait for a connection.
    import hashlib

    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import TimeoutError as PoolTimeout
    from sqlalchemy.orm import Session

    from anteroom.database_pool import DiagnosticQueuePool

    engine = create_engine('sqlite://', poolclass=DiagnosticQueuePool, pool_size=1, max_overflow=0, pool_timeout=0.001)

    def timeout_from(route_file, route_name):
        route = {'Session': Session, 'engine': engine, 'text': text}
        exec(compile(f"def {route_name}():\n    with Session(engine) as db:\n        return db.execute(text('select 1'))\n",
                     f'/app/anteroom/server/anteroom/{route_file}', 'exec'), route)
        with engine.connect():
            try:
                route[route_name]()
            except PoolTimeout:
                record = logging.LogRecord('anteroom.http', logging.ERROR, '/hosted.py', 1,
                                           'Unhandled exception in GET %s', ('/api/x',), sys.exc_info())
        return build_report(record)

    try:
        catalog = timeout_from('routes/catalog.py', 'categories')
        inventory = timeout_from('routes/hosted_workspace.py', 'inventory_response')
    finally:
        engine.dispose()
    assert catalog['fingerprint'] == inventory['fingerprint']
    from anteroom import app_module_path

    frames = [line.strip() for line in catalog['stack'].splitlines() if line.strip().startswith('File "')]
    kept = [line for line in frames if app_module_path(line.split('"')[1]) != 'database_pool.py']
    named = tuple((line.split('"')[1].rsplit('/', 1)[-1], line.rsplit(' in ', 1)[1]) for line in kept)
    assert not any(app_module_path(line.split('"')[1]) for line in kept[-8:]), 'the stack must be deep enough to matter'
    expected = hashlib.sha256(repr(('sqlalchemy.exc.TimeoutError', named[-8:])).encode()).hexdigest()[:12]
    assert catalog['fingerprint'] == expected


def test_expected_client_4xx_and_fly_replay_do_not_warn():
    from fastapi import FastAPI, Response
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient
    from anteroom.hosted import AccessLogMiddleware

    app = FastAPI()

    @app.get("/")
    def home():
        return {"ok": True}

    @app.post("/api/browser-relay/replies")
    def replay():
        return Response(status_code=409, headers={"fly-replay": "instance=machine-2"})

    @app.get("/api/marketplaces/sessions")
    def sessions():
        return JSONResponse({"detail": "Connect Chrome, then check marketplace sign-in."}, status_code=409)

    @app.get("/api/items/{item_id}")
    def refused(item_id: str):
        return JSONResponse({"detail": "conflict"}, status_code=409)

    class RejectUnauth:
        def __init__(self, inner):
            self.inner = inner

        async def __call__(self, scope, receive, send):
            if scope["type"] == "http" and scope["path"] == "/wp-login.php":
                await JSONResponse({"detail": "A valid seller session is required"}, status_code=401)(scope, receive, send)
                return
            await self.inner(scope, receive, send)

    app.add_middleware(RejectUnauth)
    app.add_middleware(AccessLogMiddleware)

    client = TestClient(app)
    with patch.object(logging.getLogger("anteroom.http"), "warning") as warning:
        assert client.post("/api/browser-relay/replies").status_code == 409
        assert client.get("/wp-login.php").status_code == 401
        assert client.get("/unknown-scanner-path").status_code == 404
        assert client.head("/").status_code == 405
        assert client.get("/api/marketplaces/sessions").status_code == 409
        warning.assert_not_called()

        # Matched-route refusals still log a warning.
        assert client.get("/api/items/123").status_code == 409
        warning.assert_called_once()

