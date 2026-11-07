"""Route handlers keep blocking database work off the event loop (see ``loop_checkouts``)."""
from loop_checkouts import async_session_handlers


def test_no_async_route_takes_a_request_session():
    found = sorted(async_session_handlers())
    assert not found, (
        "These async handlers take a request database session, so their queries run on the event "
        "loop and can hold a pooled connection across an await. Make the handler a plain def, or take "
        f"no session and run each database step through database.in_worker_session: {found}")
