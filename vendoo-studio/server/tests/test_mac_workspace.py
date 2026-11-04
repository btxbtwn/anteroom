"""While the Mac's window shows the seller's account, this Mac's copy starts nothing on its own.

The account checks sales, delists after a sale and runs automations, promotions,
eBay offers and sourcing. Running them here as well handled one sale twice, so
they wait until the window shows This Mac's Listings again.
"""
from __future__ import annotations

import asyncio
from unittest import mock

import pytest

from vendoo_studio.services import (
    automation_scheduler,
    box_scout,
    chrome_sales,
    ebay_offers,
    mac_workspace,
    marketplace_accounts,
    promotion_automations,
)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(mac_workspace, "WORKSPACE_FILE", tmp_path / "desktop-workspace.json")
    return mac_workspace


def test_a_mac_that_never_showed_the_account_runs_everything(workspace):
    assert workspace.account_shown() is False


def test_the_window_choice_decides(workspace):
    workspace.save("account")
    assert workspace.account_shown() is True
    workspace.save("mac")
    assert workspace.account_shown() is False


def test_an_unreadable_choice_counts_as_this_macs_listings(workspace):
    workspace.WORKSPACE_FILE.write_text("{", encoding="utf-8")
    assert workspace.account_shown() is False


def test_the_hosted_api_never_waits_on_a_mac_window(workspace):
    workspace.save("account")
    with mock.patch("vendoo_studio.services.service_context.current", return_value=object()):
        assert workspace.account_shown() is False


def _one_round(module, coroutine, *, sleeps_first: bool):
    """Run a timer loop for one round: its sleeps return, then the next one stops it."""
    effects = ([None] if sleeps_first else []) + [asyncio.CancelledError()]
    with mock.patch.object(module.asyncio, "sleep", side_effect=effects):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(coroutine())


@pytest.mark.parametrize("shown", [True, False])
def test_sale_checks_wait_while_the_account_is_shown(workspace, shown):
    workspace.save("account" if shown else "mac")
    chrome_sales._next.clear()
    with mock.patch.object(chrome_sales, "sale_detection_enabled", return_value=True), \
            mock.patch.object(chrome_sales, "check_marketplace", new=mock.AsyncMock(return_value=True)) as check:
        asyncio.run(chrome_sales.check_due())
    assert check.called is not shown
    chrome_sales._next.clear()


@pytest.mark.parametrize("shown", [True, False])
def test_autodelist_waits_while_the_account_is_shown(workspace, shown):
    workspace.save("account" if shown else "mac")
    session_factory = mock.MagicMock(side_effect=RuntimeError("delisting started"))
    with mock.patch("vendoo_studio.services.user_settings.autodelist_enabled", return_value=True):
        if shown:
            assert asyncio.run(marketplace_accounts.autodelist_for_sales(["sale"], session_factory=session_factory)) == {}
        else:
            with pytest.raises(RuntimeError, match="delisting started"):
                asyncio.run(marketplace_accounts.autodelist_for_sales(["sale"], session_factory=session_factory))


@pytest.mark.parametrize("shown", [True, False])
def test_automations_wait_while_the_account_is_shown(workspace, shown):
    workspace.save("account" if shown else "mac")
    with mock.patch.object(automation_scheduler, "tick", new=mock.AsyncMock(return_value=0)) as tick, \
            mock.patch.object(automation_scheduler, "SessionLocal"):
        _one_round(automation_scheduler, automation_scheduler._loop, sleeps_first=False)
    assert tick.called is not shown


@pytest.mark.parametrize("shown", [True, False])
def test_promotions_wait_while_the_account_is_shown(workspace, shown):
    workspace.save("account" if shown else "mac")
    with mock.patch.object(promotion_automations, "tick", new=mock.AsyncMock(return_value=0)) as tick, \
            mock.patch.object(promotion_automations, "check_new_likes", new=mock.AsyncMock()) as likes, \
            mock.patch.object(promotion_automations, "SessionLocal"):
        _one_round(promotion_automations, promotion_automations._timer, sleeps_first=True)
    assert tick.called is not shown
    assert likes.called is not shown


@pytest.mark.parametrize("shown", [True, False])
def test_ebay_offers_wait_while_the_account_is_shown(workspace, shown):
    workspace.save("account" if shown else "mac")
    with mock.patch.object(ebay_offers, "run_automation", new=mock.AsyncMock()) as run:
        _one_round(ebay_offers, ebay_offers._timer, sleeps_first=True)
    assert run.called is not shown


@pytest.mark.parametrize("shown", [True, False])
def test_sourcing_waits_while_the_account_is_shown(workspace, shown):
    workspace.save("account" if shown else "mac")
    with mock.patch.object(box_scout, "refresh_is_due", return_value=True), \
            mock.patch.object(box_scout, "refresh") as refresh:
        box_scout.timed_refresh()
    assert refresh.called is not shown
