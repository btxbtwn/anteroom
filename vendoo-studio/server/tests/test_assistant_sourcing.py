"""The business assistant sees every box the Sourcing tab shows, with the tab's own figures."""
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vendoo_studio.database import Base, load_models
from vendoo_studio.repositories.queries import ConversationRepo, ListingRepo
from vendoo_studio.routes.sourcing import _response
from vendoo_studio.services import box_scout, business_assistant, sourcing_boxes

NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)
PREFS = {"budget": 250.0, "min_roi": 1.0, "sell_through": 0.5, "fees": 0.2, "cost_per_piece": 2.0}


def _box(store, vid, title, price, ship, pcs, grade, per_piece):
    s = box_scout.scout()
    row = {
        "store": store, "variant_id": vid, "title": title, "url": f"https://{store}.example/{vid}",
        "price": price, "compare_at": None, "pcs": pcs, "pcs_estimated": False, "grade": grade, "lbs": 20.0,
        "lbs_estimated": False, "vip": False, "listed": "2026-09-20", "seller_resale": None, "theme": s.theme(title),
        "checkout_fee": 0.0, "usable_pcs": round(pcs * s.GRADE_YIELD[grade], 1), "demand": 1.0, "trend_hits": [],
        "ship_list": ship, "resale_low": per_piece, "resale_factor": 1.0, "score": 1.0,
    }
    row = s.price_lot(row, ship, per_piece, sell_through=PREFS["sell_through"], fees=PREFS["fees"],
                      cost_per_piece=PREFS["cost_per_piece"])
    row.update(comps_count=3 if per_piece else 0, active_median=None,
               research_at=NOW.isoformat() if per_piece else None, research_source="eBay" if per_piece else None)
    return row


def boxes():
    return [
        # Clears every check: the tab's buy list.
        _box("raghouse", 1, "Graphic Tees 60 pcs", 30.0, 20.0, 60, "good", 12.0),
        # Misses the 100% min ROI: 67% on $125 landed.
        _box("tvf", 2, "Light Jackets Mix (20 Pieces)", 100.0, 25.0, 20, "a", 30.0),
        # No sold evidence, so no resale estimate, profit or ROI.
        _box("papercrane", 3, "Mixed Apparel Lot of 40", 60.0, 15.0, 40, "standard", None),
    ]


def write_snapshot(lots, *, updated_at=NOW, **prefs):
    """A sourcing check as `box_scout._refresh` saves it, built with the skill's own buy list and picks."""
    s = box_scout.scout()
    plan_prefs = {**PREFS, **prefs}
    cfg = s.load_shipping()
    planning = {key: plan_prefs[key] for key in ("budget", "min_roi", "sell_through", "fees", "cost_per_piece")}
    saved = {**box_scout.DEFAULT_PREFS, **plan_prefs}
    state = box_scout.read_state()
    state["prefs"] = saved
    state["trend"] = {"terms": ["graphic"], "updated_at": None, "source": None}
    state["snapshot"] = {
        "format": box_scout.SNAPSHOT_FORMAT,
        "updated_at": updated_at.isoformat(),
        "destination_zip": saved["zip"],
        "preferences": {key: saved[key] for key in box_scout.PLAN_PREFS},
        "stores": {store: {"name": info["name"], "error": None, "sellout": None, "zone": 5, "origins": 1}
                   for store, info in s.STORES.items()},
        "research": False,
        "priced_themes": 2,
        "shipping": {"residential_surcharge": 0.0, "fuel_surcharge_pct": 0.0, "fuel_surcharge_as_of": "2026-09-01",
                     "factors": {store: 1.0 for store in s.STORES}, "calibration": {}},
        "vip_upside": None,
        "assumptions": {"sell_through": saved["sell_through"], "fees": saved["fees"], "fees_measured": None,
                        "sell_through_measured": None, "cost_per_piece_measured": None,
                        "selling_window_measured": None, "market_haircut": None, "grade_yield": s.GRADE_YIELD,
                        "cost_per_piece": saved["cost_per_piece"]},
        "buy_list": s.buy_list(lots, cfg, **planning),
        "store_buy_lists": {store: s.buy_list([r for r in lots if r["store"] == store], cfg, **planning)
                            for store in s.STORES},
        "store_picks": s.top_picks(lots, budget=saved["budget"], min_roi=saved["min_roi"]),
        "lots": lots,
        "calibration": {},
        "seasonality": {
            "window": {"buy_on": "2026-10-01", "start_date": "2026-10-29", "end_date": "2026-11-26",
                       "ready_in_weeks": 4, "selling_window_weeks": 4, "market": "US", "timezone": "UTC"},
            "seller_history": {"periods": [], "dated_recorded_sales": 0, "matching_window_sales": 0,
                               "excluded_incomplete_or_invalid": 0, "minimum_group_sales": 5, "groups": [],
                               "coverage": ""},
        },
    }
    box_scout._write_state(state)


def add_sale(db, title, category, price, cost, listed, sold_at):
    conv = ConversationRepo(db).create(title=title, notes=json.dumps({
        "vendooStatus": "sold",
        "vendooSale": {"price": price, "marketplace": "ebay", "soldAt": sold_at, "fees": round(price * 0.13, 2)},
        "vendooDates": {"listed": listed, "sold": sold_at},
    }))
    ListingRepo(db).save_revision(conv.id, {"title": title, "price": price, "cost": cost,
                                            "category_path": f"Men > {category}"}, source="user")


@pytest.fixture
def db(monkeypatch, tmp_path):
    load_models()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(box_scout, "state_path", lambda: tmp_path / "sourcing.json")
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


def lookup(shop, sql):
    rows = shop.lookups.execute(sql)
    names = [column[0] for column in rows.description]
    return [dict(zip(names, row, strict=True)) for row in rows.fetchall()]


def sourcing_section(brief):
    return json.loads(brief.split("--- Sourcing buy list ---\n")[1].split("\n\n")[0])


def test_every_box_on_the_tab_reaches_the_assistant_with_its_figures(db):
    write_snapshot(boxes())
    tab = _response().snapshot
    shop = business_assistant.read_shop(db, now=NOW)

    seen = {row["id"]: row for row in lookup(shop, "SELECT * FROM sourcing_boxes")}
    assert set(seen) == {f"{lot.store}:{lot.variant_id}" for lot in tab.lots}
    for lot in tab.lots:
        row = seen[f"{lot.store}:{lot.variant_id}"]
        assert (row["title"], row["url"], row["price"], row["pieces"], row["contents"]) == (
            lot.title, lot.url, lot.price, lot.pcs, lot.theme)
        assert row["checked_at"] == tab.updated_at and row["store_listed_on"] == "2026-09-20"
    tees = seen["raghouse:1"]
    assert (tees["store"], tees["shipping"], tees["landed"], tees["resale_per_piece"]) == ("Raghouse", 20.0, 50.0, 12.0)
    assert (tees["projected_profit"], tees["roi_percent"], tees["passes_min_roi"]) == (155.2, 310.0, 1)
    assert (tees["buy_list"], tees["on_its_own"], tees["top_pick"]) == ("recommended", "qualifies", 1)
    # Nothing priced it, so its figures are unknown rather than zero.
    mixed = seen["papercrane:3"]
    assert mixed["resale_per_piece"] is None and mixed["projected_profit"] is None and mixed["roi_percent"] is None
    assert mixed["passes_min_roi"] is None and mixed["roi_gap_points"] is None
    assert (mixed["buy_list"], mixed["on_its_own"]) == ("needs_research", "needs_research")
    # The brief has a line for each box too, named by store and title.
    table = shop.brief.split("--- Boxes on the Sourcing tab (3) ---")[1]
    for title in ("Graphic Tees 60 pcs", "Light Jackets Mix (20 Pieces)", "Mixed Apparel Lot of 40"):
        assert title in table


def test_a_box_that_misses_the_min_roi_says_by_how_much(db):
    write_snapshot(boxes())
    jackets = next(row for row in sourcing_boxes.box_rows(box_scout.read_state(), [], NOW) if row["id"] == "tvf:2")

    assert (jackets["landed"], jackets["projected_profit"], jackets["roi_percent"]) == (125.0, 84.0, 67.0)
    assert jackets["passes_min_roi"] is False
    # 100% of $125 is $125 profit; it projects $84.
    assert (jackets["roi_gap_points"], jackets["profit_shortfall"]) == (33.0, 41.0)
    assert jackets["buy_list"] == "return_target"


def test_recommended_lots_are_the_tabs_buy_list(db):
    write_snapshot(boxes())
    plan = _response().snapshot.buy_list
    summary = sourcing_section(business_assistant.read_shop(db, now=NOW).brief)

    assert [lot["id"] for lot in summary["recommended_lots"]] == [
        f"{lot.store}:{lot.variant_id}" for cart in plan.carts for lot in cart.lots]
    assert summary["recommended_lots"][0]["title"] == "Graphic Tees 60 pcs"
    assert (summary["total"], summary["expected_profit"]) == (plan.total, plan.expected_profit)
    assert "empty_reason" not in summary and "freshness" not in summary
    assert (summary["boxes_on_tab"], summary["boxes_priced"], summary["boxes_reaching_min_roi"]) == (3, 2, 1)


@pytest.mark.parametrize(("lots", "prefs", "reason"), [
    (None, {"min_roi": 5.0}, "0 of 3 boxes reach the 500% min ROI (best is 310% (Graphic Tees 60 pcs, Raghouse)), "
                             "within the $250 budget: 2 are below the min ROI, 1 need recent sold evidence."),
    (None, {"budget": 40.0}, "1 of 3 boxes reach the 100% min ROI (best is 310% (Graphic Tees 60 pcs, Raghouse)), but "
                             "none of them passes every other check, within the $40 budget:"),
    ([], {}, "No boxes found in the last check."),
])
def test_an_empty_buy_list_says_why(db, lots, prefs, reason):
    write_snapshot(boxes() if lots is None else lots, **prefs)
    summary = sourcing_section(business_assistant.read_shop(db, now=NOW).brief)

    assert summary["recommended_lots"] == []
    assert summary["empty_reason"].startswith(reason)


def test_no_check_yet_and_old_or_outdated_data_are_said_plainly(db):
    assert "No sourcing check has finished yet" in sourcing_section(
        business_assistant.read_shop(db, now=NOW).brief)["empty_reason"]

    write_snapshot(boxes(), updated_at=NOW - timedelta(days=3))
    assert "The box data is 3 days old: refresh the buy list" in sourcing_section(
        business_assistant.read_shop(db, now=NOW).brief)["freshness"]

    write_snapshot(boxes())
    box_scout.set_prefs(min_roi=0.5)
    assert "changed min roi since this check" in sourcing_section(
        business_assistant.read_shop(db, now=NOW).brief)["freshness"]


def test_a_refreshed_buy_list_reaches_the_next_question(db):
    write_snapshot(boxes())
    before = business_assistant.read_shop(db, now=NOW)
    assert "Graphic Tees 60 pcs" in before.brief

    # A refresh saves a new check; the next question reads it, never the old one.
    write_snapshot([_box("raghouse", 9, "Flannel Shirts 40 pcs", 40.0, 20.0, 40, "good", 14.0)],
                   updated_at=NOW + timedelta(hours=6))
    after = business_assistant.read_shop(db, now=NOW + timedelta(hours=6))
    assert "Flannel Shirts 40 pcs" in after.brief and "Graphic Tees 60 pcs" not in after.brief
    assert [row["id"] for row in lookup(after, "SELECT id FROM sourcing_boxes")] == ["raghouse:9"]
    assert sourcing_section(after.brief)["checked_at"] == (NOW + timedelta(hours=6)).isoformat()


def test_a_refreshed_buy_list_and_new_prefs_reach_the_next_question_through_the_kept_copy(db):
    # Without a clock, the assistant reuses the copy it read for the last question.
    write_snapshot(boxes())
    before = business_assistant.read_shop(db)
    assert "Graphic Tees 60 pcs" in before.brief

    write_snapshot([_box("raghouse", 9, "Flannel Shirts 40 pcs", 40.0, 20.0, 40, "good", 14.0)])
    after = business_assistant.read_shop(db)
    assert "Flannel Shirts 40 pcs" in after.brief and "Graphic Tees 60 pcs" not in after.brief

    box_scout.set_prefs(min_roi=0.5)
    assert "changed min roi since this check" in sourcing_section(business_assistant.read_shop(db).brief)["freshness"]
    for shop in (before, after):
        shop.lookups.close()


def test_recent_sales_by_category_and_by_kind_of_box(db):
    add_sale(db, "Vintage Graphic Tee Harley", "T-Shirts", 30, 3, "2026-09-01T00:00:00+00:00", "2026-09-11T00:00:00+00:00")
    add_sale(db, "Graphic Band Tee", "T-Shirts", 20, 3, "2026-09-01T00:00:00+00:00", "2026-09-21T00:00:00+00:00")
    add_sale(db, "Graphic Tee Nike", "T-Shirts", 25, None, "2026-09-01T00:00:00+00:00", "2026-09-05T00:00:00+00:00")
    add_sale(db, "Rain Jacket", "Coats", 50, 10, "2026-06-01T00:00:00+00:00", "2026-08-30T00:00:00+00:00")
    write_snapshot(boxes())
    shop = business_assistant.read_shop(db, now=NOW)

    tees = lookup(shop, "SELECT * FROM sales_by_category WHERE period = '30d' AND category = 'T-Shirts'")[0]
    assert (tees["count"], tees["average_price"], tees["median_days"]) == (3, 25.0, 10)
    # Margin rests on the two sales with a cost: (30 + 20 - 6.5 fees - 6 cost) / 50.
    assert (tees["profit_known"], tees["margin"]) == (2, 75.0)
    assert lookup(shop, "SELECT count FROM sales_by_category WHERE period = '30d' AND category = 'Coats'") == []
    assert lookup(shop, "SELECT count FROM sales_by_category WHERE period = '90d' AND category = 'Coats'") == [
        {"count": 1}]

    kinds = {row["id"]: row for row in lookup(shop, "SELECT * FROM sourcing_boxes")}
    assert (kinds["raghouse:1"]["kind_sold_12m"], kinds["raghouse:1"]["kind_average_price"],
            kinds["raghouse:1"]["kind_median_days"]) == (3, 25.0, 10)
    assert (kinds["tvf:2"]["kind_sold_12m"], kinds["tvf:2"]["kind_average_price"]) == (1, 50.0)
    assert (kinds["papercrane:3"]["kind_sold_12m"], kinds["papercrane:3"]["kind_average_price"]) == (0, None)
