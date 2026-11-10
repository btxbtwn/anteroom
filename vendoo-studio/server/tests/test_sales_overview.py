from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from fastapi.testclient import TestClient

from vendoo_studio.main import app
from vendoo_studio.services.inventory_analytics import AnalyticsItem
from vendoo_studio.services.sales_overview import sales_overview

# A Friday.
NOW = datetime(2026, 10, 30, 15, tzinfo=UTC)
_ids = iter(range(1, 10_000))


def _sale(days_ago: float, **overrides) -> AnalyticsItem:
    fields = {
        "conversation_id": f"c{next(_ids)}", "title": "Tee", "status": "sold", "price": 40, "cost": 10.0,
        "brand": "", "category": "", "sold_price": 30.0, "sold_at": NOW - timedelta(days=days_ago),
        "listed_at": None, "marketplace": "ebay", "days_listed": 10, "fees": 4.0,
    }
    fields.update(overrides)
    return AnalyticsItem(**fields)


def _active(**overrides) -> AnalyticsItem:
    return _sale(0, status="active", sold_price=None, sold_at=None, days_listed=None, **overrides)


def test_figures_compare_with_the_period_of_equal_length_before():
    rows = [
        _sale(1), _sale(3, sold_price=50.0, days_listed=20),  # this period
        _sale(8),  # previous 7 days
        _sale(20),  # older than both
        _active(), _active(),
    ]
    data = sales_overview(rows, start=date(2026, 10, 24), end=date(2026, 10, 30))
    assert data["current"]["count"] == 2
    assert data["current"]["revenue"] == 80
    # 80 revenue - 8 fees - 20 cost
    assert data["current"]["profit"] == 52
    assert data["current"]["average_days"] == 15
    assert data["previous"]["count"] == 1
    assert data["previous"]["start"] == "2026-10-17"
    assert data["previous"]["end"] == "2026-10-23"
    # 2 sold ÷ (2 sold + 2 still active)
    assert data["current"]["sell_through"] == 50.0
    assert data["previous"]["sell_through"] is None


def test_figures_without_recorded_amounts_are_unknown_not_zero():
    rows = [_sale(1, sold_price=None, cost=None, fees=None, days_listed=None)]
    current = sales_overview(rows, start=date(2026, 10, 1), end=date(2026, 10, 30))["current"]
    assert current["count"] == 1
    assert current["revenue_known"] == 0
    assert current["profit"] is None
    assert current["average_days"] is None


def test_all_time_has_no_previous_period_and_counts_undated_sales():
    rows = [_sale(400), _sale(1), _sale(0, sold_at=None)]
    data = sales_overview(rows, start=None, end=date(2026, 10, 30))
    assert data["previous"] is None
    assert data["current"]["count"] == 3
    assert data["undated_sales"] == 1
    # Weeks run Monday to Sunday, the latest 52 at most.
    assert len(data["weeks"]) == 52
    assert data["weeks_truncated"] is True
    assert data["weeks"][-1]["start"] == "2026-10-26"
    assert data["weeks"][-1]["count"] == 1


def test_weeks_cover_the_period_monday_to_sunday():
    rows = [_sale(0), _sale(1), _sale(6), _sale(13)]
    data = sales_overview(rows, start=date(2026, 10, 12), end=date(2026, 10, 30))
    assert [week["start"] for week in data["weeks"]] == ["2026-10-12", "2026-10-19", "2026-10-26"]
    assert [week["count"] for week in data["weeks"]] == [1, 1, 2]
    assert data["weeks"][-1]["revenue"] == 60


def test_marketplace_filter_narrows_figures_and_breakdown():
    rows = [_sale(1), _sale(2, marketplace="poshmark", sold_price=70.0), _sale(2, marketplace="poshmark"), _active()]
    every = sales_overview(rows, start=date(2026, 10, 1), end=date(2026, 10, 30))
    assert [(row["id"], row["count"], row["revenue"]) for row in every["marketplaces"]] == [
        ("poshmark", 2, 100), ("ebay", 1, 30),
    ]
    posh = sales_overview(rows, start=date(2026, 10, 1), end=date(2026, 10, 30), marketplace="poshmark")
    assert posh["current"]["count"] == 2
    assert [row["id"] for row in posh["marketplaces"]] == ["poshmark"]
    # Active listings are not kept per marketplace, so the rate is only for all of them.
    assert posh["current"]["sell_through"] is None


def test_route_reads_the_sellers_sales():
    client = TestClient(app)
    response = client.get("/api/analytics/overview", params={"start": "2026-10-01", "end": "2026-10-30"})
    assert response.status_code == 200
    body = response.json()
    assert body["current"]["count"] == 0
    assert body["previous"]["start"] == "2026-09-01"
    bad = client.get("/api/analytics/overview", params={"start": "2026-10-30", "end": "2026-10-01"})
    assert bad.status_code == 400
    unknown = client.get("/api/analytics/overview", params={"end": "2026-10-30", "marketplace": "nowhere"})
    assert unknown.status_code == 400


def test_days_to_sell_is_the_median_both_tabs_show():
    rows = [_sale(1, days_listed=2), _sale(2, days_listed=4), _sale(3, days_listed=90)]
    current = sales_overview(rows, start=date(2026, 10, 1), end=date(2026, 10, 30))["current"]
    assert current["median_days"] == 4
    # Kept for iPhone builds that still read it; see OverviewFigures.
    assert current["average_days"] == 32
