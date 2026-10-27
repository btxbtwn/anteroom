"""Seller settings: the stale-listing threshold and label colors."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from vendoo_studio.services import user_settings


@pytest.fixture(autouse=True)
def settings_file(tmp_path):
    with patch.object(user_settings, "settings_path", return_value=tmp_path / "settings.json"):
        yield


def test_defaults_and_saved_values():
    prefs = user_settings.get_ui_prefs()
    assert prefs["stale_listing_days"] == 60 and prefs["label_colors"] == {}
    saved = user_settings.set_ui_prefs(stale_listing_days=30, label_colors={"Winter": "blue", " Bin  A ": "red"})
    assert saved["stale_listing_days"] == 30 and user_settings.get_stale_listing_days() == 30
    assert saved["label_colors"] == {"Winter": "blue", "Bin A": "red"}
    assert user_settings.set_ui_prefs(label_colors={})["label_colors"] == {}


@pytest.mark.parametrize("change", [{"stale_listing_days": 14}, {"stale_listing_days": 121}, {"stale_listing_days": True},
    {"label_colors": {"Winter": "neon"}}, {"label_colors": ["Winter"]}])
def test_invalid_values_are_refused(change):
    with pytest.raises(ValueError):
        user_settings.set_ui_prefs(**change)
    assert user_settings.get_ui_prefs()["stale_listing_days"] == 60


def test_analytics_stale_list_follows_the_threshold():
    from vendoo_studio.services import inventory_analytics
    from vendoo_studio.services.inventory_analytics import AnalyticsItem

    now = datetime(2026, 10, 26, tzinfo=UTC)
    item = AnalyticsItem(conversation_id="a", title="Coat", status="active", price=40.0, cost=None, brand="", category="",
                         sold_price=None, sold_at=None, listed_at=now - timedelta(days=40), marketplace="", days_listed=None)
    assert inventory_analytics._stale([item], now) == []
    user_settings.set_ui_prefs(stale_listing_days=30)
    assert [row["days_listed"] for row in inventory_analytics._stale([item], now)] == [40]
