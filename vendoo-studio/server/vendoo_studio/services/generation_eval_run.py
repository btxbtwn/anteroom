"""Run live quality cases through listing prompts, repair, and local draft saves."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from vendoo_studio.services import listing_persistence
from vendoo_studio.repositories.queries import ConversationRepo
from vendoo_studio.services import model_usage
from vendoo_studio.services.chat_prompts import listing_generation_messages, load_skill_rules
from vendoo_studio.services.generation_eval import evaluate_outputs
from vendoo_studio.services.listing_generate import (
    collect_provider_text, persist_generated_listing_with_repair, photo_fingerprint, seller_item_details,
)
from vendoo_studio.services.schema_migrations import ensure_schema


def _seed_case(db: Session, case: dict) -> tuple[str, str, list[dict]]:
    """Save the case's draft and photo analysis; return its listing, seed revision and prompt."""
    repo = ConversationRepo(db)
    notes = json.dumps({"notes": case.get("seller_notes", ""), **case.get("item_details", {})})
    conv = repo.create(title=f"Evaluation: {case['id']}", notes=notes)
    seed = dict(case.get("seed_listing") or {})
    from vendoo_studio.services.native_contract import capture

    forms = case.get("native_forms") or {}
    contracts = capture(seed, forms, forms) if forms else {}
    if contracts:
        seed["_native_forms"] = contracts
    seed_revision = listing_persistence.save_revision(db, conv.id, seed, source="evaluation_seed")
    evidence = str(case["evidence"])
    if not evidence.startswith("Photo analysis:"):
        evidence = "Photo analysis:\n" + evidence
    repo.add_message(conv.id, "system", evidence, photo_fingerprint=photo_fingerprint([]))
    messages = listing_generation_messages(
        load_skill_rules(evidence, db), seller_item_details(notes), evidence, db, conv.id,
        native_forms=contracts,
    )
    return conv.id, seed_revision.id, messages


async def run_cases(provider, cases: list[dict], output_dir: Path) -> dict:
    """Only write to a new evaluation directory; never send or publish a listing."""
    from vendoo_studio import database

    output_dir.mkdir(parents=True, exist_ok=False)
    engine = create_engine(f"sqlite:///{output_dir / 'drafts.db'}", connect_args={"check_same_thread": False})
    ensure_schema(engine)
    outputs = {}
    runs = []
    gate = asyncio.Semaphore(2)

    async def one(case):
        async with gate:
            started = time.monotonic()
            with model_usage.capture() as calls:
                conv_id, seed_revision_id, messages = await database.in_worker_session(_seed_case, case)
                try:
                    reply = await collect_provider_text(provider, messages)
                    listing = await persist_generated_listing_with_repair(
                        conv_id, reply, provider, expected_revision_id=seed_revision_id,
                    )
                    if listing:
                        outputs[case["id"]] = listing
                    outcome = "saved_draft" if listing else "no_listing"
                except Exception as exc:
                    # Never export arbitrary provider error text or request data.
                    outcome = f"failed:{type(exc).__name__}"
                runs.append({"id": case["id"], "outcome": outcome, "calls": calls,
                             "total_sec": round(time.monotonic() - started, 2)})

    # Drafts go to the evaluation's own database, never the seller's: while the
    # cases run, every session the services open (database.SessionLocal) uses it.
    # The evaluation runs in a process of its own (scripts/evaluate-generation.py).
    sessions = database._local_sessions
    database._local_sessions = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    try:
        await asyncio.gather(*(one(case) for case in cases))
    finally:
        database._local_sessions = sessions
        engine.dispose()
    report = evaluate_outputs(cases, outputs)
    report["provider"] = provider.name
    report["model"] = provider.listing_model
    report["runs"] = runs
    (output_dir / "outputs.json").write_text(json.dumps(outputs, indent=2))
    (output_dir / "report.json").write_text(json.dumps(report, indent=2))
    return report
