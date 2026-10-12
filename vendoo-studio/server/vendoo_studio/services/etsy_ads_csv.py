"""Etsy Ads results from the stats CSV the seller downloads from Etsy.

Etsy exports one row per day (views, clicks, orders, revenue, spend, ending
budget). Each day with activity becomes a one-day Etsy ``ad_spend`` entry, so
the totals, ROAS and profit-after-ads work as they do for typed entries.
Uploading an export replaces the entries an earlier upload wrote for the days
it covers, so overlapping exports correct each other instead of double
counting. Entries the seller typed are never touched.
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from vendoo_studio.models.ad_spend import AdSpend

SOURCE = "etsy_csv"
_REQUIRED = ("date (et)", "clicks", "orders", "revenue (usd)", "spend (usd)")
_MAX_BYTES = 1_000_000


class EtsyAdsFileError(ValueError):
    """The upload is not an Etsy Ads stats export."""


def parse_export(raw: bytes) -> list[dict[str, Any]]:
    """Days with ad activity, oldest first. Idle days carry nothing to track."""
    if len(raw) > _MAX_BYTES:
        raise EtsyAdsFileError("That file is too large to be an Etsy Ads export.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise EtsyAdsFileError("That file is not a CSV export from Etsy Ads.") from exc
    reader = csv.DictReader(io.StringIO(text))
    header = {(name or "").strip().lower() for name in reader.fieldnames or []}
    missing = [name for name in _REQUIRED if name not in header]
    if missing:
        raise EtsyAdsFileError(
            "That does not look like an Etsy Ads stats export "
            f"(missing {', '.join(missing)})."
        )
    days: dict[date, dict[str, Any]] = {}
    for line, row in enumerate(reader, start=2):
        cells = {key.strip().lower(): (value or "").strip() for key, value in row.items() if key}
        if not any(cells.values()):
            continue
        try:
            day = datetime.strptime(cells["date (et)"], "%b %d, %Y").date()
            days[day] = {
                "day": day,
                "clicks": int(float(cells["clicks"] or 0)),
                "orders": int(float(cells["orders"] or 0)),
                "revenue": float(cells["revenue (usd)"] or 0),
                "spend": float(cells["spend (usd)"] or 0),
            }
        except (KeyError, ValueError) as exc:
            raise EtsyAdsFileError(f"Row {line} could not be read.") from exc
    if not days:
        raise EtsyAdsFileError("That file has no days in it.")
    return [days[day] for day in sorted(days)]


def import_export(db: Session, raw: bytes) -> dict[str, Any]:
    rows = parse_export(raw)
    first, last = rows[0]["day"], rows[-1]["day"]
    db.query(AdSpend).filter(
        AdSpend.source == SOURCE,
        AdSpend.start_date >= first,
        AdSpend.start_date <= last,
    ).delete(synchronize_session=False)
    active = [r for r in rows if r["spend"] or r["clicks"] or r["orders"] or r["revenue"]]
    for r in active:
        db.add(AdSpend(
            marketplace="etsy",
            start_date=r["day"],
            end_date=r["day"],
            spend=r["spend"],
            clicks=r["clicks"],
            orders=r["orders"],
            revenue=r["revenue"],
            notes="Etsy Ads CSV",
            source=SOURCE,
        ))
    # Typed entries stay as they are, so say when they cover the same days.
    overlapping = db.query(AdSpend).filter(
        AdSpend.marketplace == "etsy",
        AdSpend.source.is_(None),
        AdSpend.start_date <= last,
        AdSpend.end_date >= first,
    ).count()
    db.commit()
    return {
        "days": len(active),
        "start": first.isoformat(),
        "end": last.isoformat(),
        "spend": round(sum(r["spend"] for r in active), 2),
        "overlapping_entries": overlapping,
    }
