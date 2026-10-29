"""Record and read how long each stage of listing generation takes.

Stage names, in run order (parallel stages record their own time and overlap):

- ``photo_analysis`` — full photo read (A)
- ``identity_read`` — the quick brand/item read that starts comps early
- ``categories`` / ``sold_comps`` — category selection (B) beside comps (C)
- ``categories_and_comps`` — wall time spent waiting on B and C after A
- ``listing_stream`` — the streamed listing (D)
- ``repair_and_finalize`` — JSON repair, validation and gap fill (E/F)
- ``vendoo_categories`` — Vendoo leaf ids and field schemas (H, beside G)
- ``gap_fill`` — discovered-field gap fill (G)
- ``vendoo_fields`` — category field fill (H)
- ``finish`` — wall time of the field work after the chat stream ended
"""

from __future__ import annotations

import logging
import statistics
from typing import Any

from sqlalchemy.orm import Session

from vendoo_studio.database import SessionLocal
from vendoo_studio.models.generation_timing import GenerationTiming

log = logging.getLogger("vendoo_studio.generation_timing")


def _rounded(stages: dict[str, float]) -> dict[str, float]:
    return {stage: round(float(seconds), 2) for stage, seconds in stages.items()}


def record_run(conv_id: str, stages: dict[str, float], *, total_sec: float, outcome: str, calls: list[dict] | None = None) -> str | None:
    """Save one run's stream stages; returns the row id for ``add_stages``."""
    db = SessionLocal()
    try:
        row = GenerationTiming(
            conversation_id=conv_id,
            outcome=outcome,
            stages=_rounded(stages),
            calls=list(calls or []),
            total_sec=round(total_sec, 2),
        )
        db.add(row)
        db.commit()
        return row.id
    except Exception:
        log.exception("could not save generation timing for %s", conv_id)
        db.rollback()
        return None
    finally:
        db.close()


def add_stages(timing_id: str | None, stages: dict[str, float], *, total_sec: float, outcome: str | None = None, calls: list[dict] | None = None) -> None:
    """Append the stages that finished after the chat stream ended."""
    if not timing_id or not stages:
        return
    db = SessionLocal()
    try:
        row = db.get(GenerationTiming, timing_id)
        if row is None:
            return
        row.stages = {**(row.stages or {}), **_rounded(stages)}
        if outcome is not None:
            row.outcome = outcome
        if calls is not None:
            row.calls = list(calls)
        row.total_sec = round(max(row.total_sec or 0.0, total_sec), 2)
        db.commit()
    except Exception:
        log.exception("could not update generation timing %s", timing_id)
        db.rollback()
    finally:
        db.close()


def _as_dict(row: GenerationTiming) -> dict[str, Any]:
    return {
        "id": row.id,
        "conversation_id": row.conversation_id,
        "outcome": row.outcome,
        "stages": dict(row.stages or {}),
        "calls": list(row.calls or []),
        "total_sec": row.total_sec,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def for_conversation(db: Session, conv_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
    rows = (
        db.query(GenerationTiming)
        .filter(GenerationTiming.conversation_id == conv_id)
        .order_by(GenerationTiming.created_at.desc())
        .limit(limit)
        .all()
    )
    return [_as_dict(row) for row in rows]


def recent_summary(db: Session, *, limit: int = 50) -> dict[str, Any]:
    """The latest runs and the median seconds of each stage across the finished ones."""
    rows = (
        db.query(GenerationTiming)
        .order_by(GenerationTiming.created_at.desc())
        .limit(limit)
        .all()
    )
    runs = [_as_dict(row) for row in rows]
    finished = [run for run in runs if run["outcome"] == "done"]
    by_stage: dict[str, list[float]] = {}
    for run in finished:
        for stage, seconds in run["stages"].items():
            by_stage.setdefault(stage, []).append(float(seconds))
    return {
        "runs": runs,
        "finished": len(finished),
        "median_total_sec": round(statistics.median(run["total_sec"] for run in finished), 2) if finished else None,
        "median_stage_sec": {stage: round(statistics.median(values), 2) for stage, values in by_stage.items()},
    }
