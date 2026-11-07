"""Route handlers keep blocking database work off the event loop (see ``loop_checkouts``)."""
from loop_checkouts import BASELINE, async_session_handlers, recorded


def test_no_new_async_route_takes_a_request_session():
    new = sorted(async_session_handlers() - recorded())
    assert not new, (
        "These async handlers take a request database session, so their queries run on the event "
        "loop and can hold a pooled connection across an await. Make the handler a plain def, or take "
        f"no session and run each database step through database.in_worker_session: {new}")


def test_the_record_only_lists_handlers_that_still_need_fixing():
    fixed = sorted(recorded() - async_session_handlers())
    assert not fixed, f"Remove these fixed handlers from {BASELINE.name}: {fixed}"
