from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from vendoo_studio.database import get_db
from vendoo_studio.services.inventory_analytics import inventory_analytics

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


class AnalyticsInventory(BaseModel):
    active: int
    draft: int
    sold: int
    failed: int
    working: int
    asking_value: float
    cost_value: float
    cost_known: int
    stale_count: int
    stale_value: float
    undated_count: int


class AnalyticsSales(BaseModel):
    count: int
    revenue: float
    profit: float | None
    profit_known: int
    fees_known: int
    revenue_known: int
    days_known: int
    average_price: float | None
    median_days: int | None
    margin: float | None


class AnalyticsPrevious(BaseModel):
    start: str
    end: str
    sales: AnalyticsSales


class AnalyticsPeriod(AnalyticsSales):
    label: str


class AnalyticsGroup(AnalyticsSales):
    id: str
    label: str


class AnalyticsLabelGroup(AnalyticsGroup):
    color: str | None


class AnalyticsActiveListing(BaseModel):
    conversation_id: str
    title: str
    price: float
    days_listed: int | None


class AnalyticsAging(BaseModel):
    label: str
    count: int
    asking_value: float
    listings: list[AnalyticsActiveListing]


class AnalyticsStaleListing(BaseModel):
    conversation_id: str
    title: str
    days_listed: int
    price: float
    cost: float | None
    lowest_price: int | None
    discount_percent: int | None
    sale_price: float | None


class AnalyticsSale(BaseModel):
    conversation_id: str
    title: str
    price: float | None
    marketplace: str
    sold_at: str | None
    days_listed: int | None
    event: str | None
    profit: float | None


class AnalyticsOldListing(BaseModel):
    conversation_id: str
    title: str
    price: float
    days_listed: int


class AnalyticsIncompleteSale(BaseModel):
    id: str
    conversation_id: str | None
    title: str
    missing: list[str]


class AnalyticsAdMarketplace(BaseModel):
    id: str
    entries: int
    spend: float
    clicks: int | None
    orders: int | None
    revenue: float | None
    roas: float | None
    cost_per_click: float | None
    sales_revenue: float
    spend_percent: float | None
    profit: float | None
    profit_after_ads: float | None


class AnalyticsAds(BaseModel):
    spend: float
    profit: float | None
    profit_after_ads: float | None
    marketplaces: list[AnalyticsAdMarketplace]


class AnalyticsResponse(BaseModel):
    range: str
    incomplete_sales: list[AnalyticsIncompleteSale]
    undated_sales: int
    periods_truncated: bool
    inventory: AnalyticsInventory
    sell_through_rate: float | None
    sales: AnalyticsSales
    previous: AnalyticsPrevious | None
    periods: list[AnalyticsPeriod]
    marketplaces: list[AnalyticsGroup]
    categories: list[AnalyticsGroup]
    brands: list[AnalyticsGroup]
    labels: list[AnalyticsLabelGroup]
    price_tiers: list[AnalyticsGroup]
    aging: list[AnalyticsAging]
    stale: list[AnalyticsStaleListing]
    recent: list[AnalyticsSale]
    oldest: list[AnalyticsOldListing]
    ads: AnalyticsAds


@router.get("", response_model=AnalyticsResponse)
def get_analytics(
    span: str = Query("12m", alias="range"),
    start: date | None = None,
    end: date | None = None,
    db: Session = Depends(get_db),
):
    try:
        payload = inventory_analytics(db, range_id=span, start=start, end=end)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Unknown analytics range, or a custom range without a start on or before its end.") from exc
    return AnalyticsResponse.model_validate(payload)


class OverviewFigures(BaseModel):
    count: int
    revenue: float
    revenue_known: int
    profit: float | None
    profit_known: int
    fees_known: int
    average_days: float | None
    days_known: int
    sell_through: float | None


class OverviewPrevious(OverviewFigures):
    start: str
    end: str


class OverviewWeek(BaseModel):
    start: str
    count: int
    revenue: float
    revenue_known: int


class OverviewMarketplace(BaseModel):
    id: str
    count: int
    revenue: float
    revenue_known: int


class OverviewResponse(BaseModel):
    start: str | None
    end: str
    undated_sales: int
    current: OverviewFigures
    previous: OverviewPrevious | None
    weeks: list[OverviewWeek]
    weeks_truncated: bool
    marketplaces: list[OverviewMarketplace]


@router.get("/overview", response_model=OverviewResponse)
def get_overview(
    end: date,
    response: Response,
    start: date | None = None,
    marketplace: str | None = None,
    db: Session = Depends(get_db),
):
    """Sales figures for whole UTC days from ``start`` (all time when absent) through ``end``."""
    from vendoo_studio.services.inventory_analytics import load_rows
    from vendoo_studio.services.sales_ledger import SALE_MARKETPLACES
    from vendoo_studio.services.sales_overview import sales_overview

    if marketplace is not None and marketplace not in SALE_MARKETPLACES:
        raise HTTPException(status_code=400, detail="Choose a supported marketplace.")
    if start is not None and start > end:
        raise HTTPException(status_code=400, detail="Choose a start on or before the end.")
    response.headers["Cache-Control"] = "no-store"
    return OverviewResponse.model_validate(sales_overview(load_rows(db), start=start, end=end, marketplace=marketplace))


@router.get("/generation-timings")
def get_generation_timings(limit: int = Query(50, ge=1, le=500), db: Session = Depends(get_db)):
    """Recent listing generations: seconds per stage, and the median of each stage."""
    from vendoo_studio.services.generation_timing import recent_summary

    return recent_summary(db, limit=limit)
