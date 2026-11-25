"""Cloud schedulers survive a failed owner lookup during a timed pass."""
import asyncio
import logging
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.exc import OperationalError

from anteroom.services import cloud_sessions, hosted_promotion_scheduler, hosted_sale_checks


@pytest.mark.parametrize('scheduler,interval_owner,interval_name', [
    (hosted_sale_checks, hosted_sale_checks.chrome_sales, 'POLL_INTERVAL_S'),
    (hosted_promotion_scheduler, hosted_promotion_scheduler.promotion_automations, 'TICK_S'),
])
def test_cloud_scheduler_resumes_after_owner_lookup_fails(
    monkeypatch, caplog, scheduler, interval_owner, interval_name,
):
    failure = OperationalError('owner lookup', None, RuntimeError('database unavailable'))
    owners = Mock(side_effect=[failure, ['seller'], ['seller']])
    check = AsyncMock(side_effect=[None, asyncio.CancelledError])
    stagger = Mock(return_value=60.0)
    monkeypatch.setattr(cloud_sessions, 'SUPPORTED_CLOUD_MARKETPLACES', frozenset({'poshmark'}))
    monkeypatch.setattr(cloud_sessions, 'active_owners', owners)
    monkeypatch.setattr(interval_owner, interval_name, 0.01)
    monkeypatch.setattr(scheduler, 'check_cloud_seller', check)
    monkeypatch.setattr(hosted_promotion_scheduler.random, 'uniform', stagger)
    monkeypatch.delenv('ANTEROOM_CLOUD_LIKE_CHECK_INTERVAL_S', raising=False)

    engine = object()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(asyncio.wait_for(scheduler.serve_cloud(engine), timeout=5))

    assert owners.call_count == 3
    assert [call.args[:2] for call in check.await_args_list] == [('seller', ('poshmark',))] * 2
    errors = [record for record in caplog.records if record.name == scheduler.__name__]
    assert len(errors) == 1 and errors[0].levelno == logging.ERROR
    assert errors[0].exc_info and errors[0].exc_info[1] is failure
    if scheduler is hosted_promotion_scheduler:
        # Startup staggering runs on the first successful pass only.
        stagger.assert_called_once_with(0.0, scheduler.CLOUD_LIKE_INITIAL_STAGGER_S)
