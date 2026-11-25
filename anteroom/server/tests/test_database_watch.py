"""The operator hears once when the hosted database stays unreachable, and once when it is back."""
import logging
import threading

from anteroom.services import database_watch
from anteroom.services.database_watch import DatabaseWatch


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make_watch(statuses, *, budget=lambda: None, send=None, sent=None):
    clock = Clock()
    sent = [] if sent is None else sent
    answers = iter(statuses)
    watch = DatabaseWatch(
        lambda: next(answers), send or (lambda subject, body: sent.append((subject, body))), budget,
        clock=clock, wall=lambda: 1_795_000_000.0,
    )
    return watch, clock, sent


def run(watch, clock, rounds, step=30):
    for _ in range(rounds):
        watch.tick()
        clock.now += step


def test_two_minutes_of_failure_sends_one_outage_email_then_one_recovery():
    watch, clock, sent = make_watch(["unavailable"] * 20 + ["ok"] * 5, budget=lambda: True)
    run(watch, clock, 4)  # 0, 30, 60, 90 seconds: not yet two minutes.
    assert sent == []
    run(watch, clock, 16)
    [(subject, body)] = sent
    assert subject == "[Anteroom hosted] Hosted database unreachable"
    assert "GET /api/ready has failed" in body and "Postgres is not accepting connections" in body
    run(watch, clock, 5)
    assert [s for s, _ in sent] == [
        "[Anteroom hosted] Hosted database unreachable", "[Anteroom hosted] Hosted database reachable again"]
    assert "about 10 minutes" in sent[1][1]


def test_a_short_blip_sends_nothing():
    watch, clock, sent = make_watch(["unavailable"] * 3 + ["ok"] * 3 + ["unavailable"] * 3 + ["ok"])
    run(watch, clock, 10)
    assert sent == []


def test_outage_email_goes_without_the_unreadable_budget_and_recovery_waits_for_it():
    allowed = {"value": None}
    watch, clock, sent = make_watch(["unavailable"] * 6 + ["ok"] * 30, budget=lambda: allowed["value"])
    run(watch, clock, 6)
    assert len(sent) == 1  # Budget unreadable during the outage: sent anyway, once.
    allowed["value"] = False  # Database back, budget refuses for now.
    run(watch, clock, 12)
    assert len(sent) == 1
    allowed["value"] = True
    run(watch, clock, 12)
    assert [s.endswith("reachable again") for s, _ in sent] == [False, True]


def test_a_failed_send_is_tried_again_later_not_every_check():
    attempts = []

    def flaky(subject, body):
        attempts.append(subject)
        if len(attempts) == 1:
            raise OSError("relay unreachable")

    watch, clock, _ = make_watch(["unavailable"] * 40, send=flaky)
    run(watch, clock, 10)  # Fails at 120 s; the next try waits five minutes.
    assert len(attempts) == 1
    run(watch, clock, 10)
    assert len(attempts) == 2
    run(watch, clock, 20)
    assert len(attempts) == 2  # Delivered: never repeated for the same outage.


def test_failing_again_before_the_recovery_email_keeps_one_outage():
    allowed = {"value": None}
    statuses = ["unavailable"] * 5 + ["ok"] + ["unavailable"] * 10 + ["ok"]
    watch, clock, sent = make_watch(statuses, budget=lambda: allowed["value"])
    run(watch, clock, 5)
    assert len(sent) == 1
    allowed["value"] = False
    run(watch, clock, 1)  # Recovery waits for the budget...
    allowed["value"] = None
    run(watch, clock, 10)  # ...and the database goes down again: no second outage email.
    assert len(sent) == 1
    allowed["value"] = True
    run(watch, clock, 1)
    assert [s.endswith("reachable again") for s, _ in sent] == [False, True]


def test_a_check_that_hangs_counts_as_a_failure_within_its_deadline():
    release = threading.Event()

    def hangs():
        release.wait(5)
        return "ok"

    watch = DatabaseWatch(hangs, lambda *_: None, lambda: None, check_deadline=0.05)
    try:
        assert watch._check() == "unavailable"
        assert watch._check() == "unavailable"  # Still hung: no second connect attempt piles up.
    finally:
        release.set()


def test_a_budget_read_that_hangs_still_sends_the_outage_email_once():
    release = threading.Event()
    reads = []

    def hangs():
        reads.append(1)
        release.wait(5)
        return False

    clock, sent = Clock(), []
    watch = DatabaseWatch(
        lambda: "unavailable", lambda subject, body: sent.append(subject), hangs,
        clock=clock, wall=lambda: 1_795_000_000.0, check_deadline=0.05,
    )
    try:
        run(watch, clock, 10)
        assert sent == ["[Anteroom hosted] Hosted database unreachable"]
        assert len(reads) == 1  # The hung read is never stacked by later rounds.
    finally:
        release.set()


def test_schema_mismatch_is_named_in_the_alert():
    watch, clock, sent = make_watch(["schema_mismatch"] * 6)
    run(watch, clock, 6)
    [(_, body)] = sent
    assert "schema revision" in body


def test_watch_logs_never_become_error_emails():
    from anteroom.services.error_alerts import AlertHandler

    record = logging.LogRecord(database_watch.log.name, logging.WARNING, __file__, 1, "x", (), None)
    assert AlertHandler.filter(object.__new__(AlertHandler), record) is False


def test_operator_alert_may_use_the_budget_reserved_for_errors(tmp_path):
    from sqlalchemy import create_engine
    from anteroom.repositories.error_email import EmailStore
    from anteroom.services.error_email_delivery import reserve_direct_send

    engine = create_engine(f"sqlite:///{tmp_path / 'email.db'}")
    store = EmailStore(engine)
    store.table.create(engine)
    store.groups.create(engine)
    with engine.begin() as connection:
        connection.execute(store.table.insert().values(id=1, payload={"attempts": [1000.0] * 3}))
    assert reserve_direct_send(store, 4, clock=lambda: 1001.0) is False
    assert reserve_direct_send(store, 4, clock=lambda: 1001.0, keep_error_reserve=False) is True
    engine.dispose()


def test_unavailable_answer_names_the_reason_for_the_apps():
    import asyncio
    from sqlalchemy.exc import OperationalError, SQLAlchemyError
    from anteroom.hosted import create_app

    handler = create_app().exception_handlers[SQLAlchemyError]
    response = asyncio.run(handler(None, OperationalError("SELECT 1", {}, Exception("connection refused"))))
    assert response.status_code == 503
    assert response.headers["X-Anteroom-Reason"] == "database_unavailable"
