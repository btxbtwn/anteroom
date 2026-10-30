"""Background listing generation that survives client disconnects."""

from __future__ import annotations

import asyncio
import copy
import logging
import time

from vendoo_studio.database import SessionLocal
from vendoo_studio.repositories.queries import ConversationRepo, RevisionConflict
from vendoo_studio.services import activity, generation_timing, live_trace, model_slots, model_usage
from vendoo_studio.services.brave_search import item_fields
from vendoo_studio.services.chat_prompts import (
    listing_generation_messages,
    load_skill_rules,
)
from vendoo_studio.services.comp_research import (
    comps_search_available,
    comps_setup_note,
    research_comps_for_fields,
    research_sold_comps,
)
from vendoo_studio.services.listing_generate import (
    PhotoAnalysisError,
    analysis_with_photo_count,
    analyze_photos_with_tag_retry,
    extract_listing_json,
    latest_photo_analysis,
    listing_save_summary,
    persist_generated_listing_with_repair,
    photo_analysis_failure,
    photo_fingerprint,
    quick_identity_read,
    require_photo_analysis,
)
from vendoo_studio.services.schema_probe import (
    prepare_generation_schema,
)
from vendoo_studio.services.streaming import (
    GenerationRun,
    await_with_pulses,
    generation_discarded,
    generation_is_current,
    iter_with_keepalives,
    spawn,
    sse_data,
    sse_event,
    sse_for_stream_item,
)

log = logging.getLogger("vendoo_studio.chat")


def _brand(fields: dict[str, str]) -> str:
    return " ".join(str(fields.get("brand") or "").split()).casefold()


def _early_comps_fields(fields: dict[str, str]) -> dict[str, str] | None:
    """Identity good enough to search comps on: a brand and what the item is."""
    if _brand(fields) and (fields.get("category") or fields.get("style")):
        return fields
    return None


async def _identify_item(vision_provider, paths: list[str], notes: str) -> dict[str, str]:
    """The quick identity read, as an agent; empty (never raises) when it fails."""
    async with live_trace.agent("identity", "Identify item") as agent:
        try:
            async with model_slots.slot():
                fields = await quick_identity_read(vision_provider, paths, notes)
        except Exception as exc:  # noqa: BLE001 - comps then wait for the full read
            log.info("quick identity read failed: %s", exc)
            fields = {}
        if _early_comps_fields(fields):
            agent.result(" · ".join(v for k in ("brand", "category", "size") if (v := fields.get(k))))
        else:
            agent.result("Comps wait for the full photo read")
        return fields


async def run_listing_generation(
    run: GenerationRun,
    *,
    conv_id: str,
    provider,
    vision_provider,
    vision_name: str,
    vision_model: str,
    notes: str,
    item_details: str,
    seller_answers: str,
    paths: list[str],
    photo_count: int,
    analysis_fingerprint: str,
) -> None:
    """Analyze photos, pick categories, stream the listing, and save it."""
    calls = model_usage.bind()
    agents_run = activity.begin_agents(conv_id)

    def trace(kind: str, text: str) -> None:
        if kind == "agent":
            activity.record_agent(conv_id, agents_run, text)
        run.publish(sse_event(kind, text))

    # Photo analysis and comps research report their reasoning and searches
    # through this, so the chat can show them before their cards land.
    live_trace.bind(trace)
    model_slots.bind()
    run.publish(sse_event("status", "Analyzing photos…"))
    stream_db = SessionLocal()
    stream_repo = ConversationRepo(stream_db)
    skill_rules = load_skill_rules(item_details, stream_db)
    full_text = ""
    child_tasks: list[asyncio.Task] = []
    run_started = time.monotonic()
    stage_started = run_started
    timings: dict[str, float] = {}

    def still_current() -> bool:
        # Clear/Regenerate wipe the chat while this run is still unwinding.
        if generation_is_current(conv_id, run):
            return True
        raise asyncio.CancelledError()

    def mark(stage: str) -> None:
        """Wall time since the previous mark."""
        nonlocal stage_started
        now = time.monotonic()
        timings[stage] = round(now - stage_started, 2)
        stage_started = now

    timing_saved = False
    timing_id: str | None = None

    def save_timings(outcome: str) -> str | None:
        """Record the run once: a failure after it was saved as done is not a second run."""
        nonlocal timing_saved
        if timing_saved:
            return None
        timing_saved = True
        return generation_timing.record_run(
            conv_id, timings, total_sec=time.monotonic() - run_started, outcome=outcome, calls=calls,
        )

    async def timed(stage: str, coro):
        """A side-by-side stage's own duration."""
        started = time.monotonic()
        try:
            return await coro
        finally:
            timings[stage] = round(time.monotonic() - started, 2)

    comps_on = comps_search_available()
    comps_task: asyncio.Task | None = None
    comps_brand = ""

    def start_comps(coro, brand: str) -> None:
        nonlocal comps_task, comps_brand
        comps_task = asyncio.create_task(timed("sold_comps", coro))
        comps_brand = brand
        child_tasks.append(comps_task)

    try:
        evidence: dict = {}
        existing = latest_photo_analysis(stream_repo.get_messages(conv_id), fingerprint=analysis_fingerprint)
        listing_rules = skill_rules
        if existing:
            analysis_text = existing
        else:
            async def read_photos():
                async with model_slots.slot():
                    return await analyze_photos_with_tag_retry(
                        vision_provider,
                        paths,
                        notes=item_details,
                        listing_rules=listing_rules[:8000],
                    )

            analysis_task = asyncio.create_task(read_photos())
            child_tasks.append(analysis_task)
            # Comps only need to know what the item is. A quick read of a few
            # small photos answers that well before the full analysis does, so
            # comps can start beside it rather than after it.
            identity_task = None
            if comps_on:
                identity_task = asyncio.create_task(
                    timed("identity_read", _identify_item(vision_provider, paths, item_details))
                )
                child_tasks.append(identity_task)
            while not analysis_task.done():
                waiting = {analysis_task, *([identity_task] if identity_task and not identity_task.done() else [])}
                await asyncio.wait(waiting, timeout=3.0, return_when=asyncio.FIRST_COMPLETED)
                run.pulse()
                if identity_task is not None and identity_task.done() and comps_task is None:
                    fields = _early_comps_fields(identity_task.result() if not identity_task.cancelled() else {})
                    if fields:
                        start_comps(research_comps_for_fields(fields), _brand(fields))
                    identity_task = None
            if identity_task is not None and not identity_task.done():
                identity_task.cancel()
            try:
                result = analysis_task.result()
                evidence, analysis_text = require_photo_analysis(result)
            except PhotoAnalysisError:
                raise
            except Exception as exc:
                raise photo_analysis_failure(exc) from exc
            prompt_analysis = analysis_with_photo_count(photo_count, analysis_text)
            still_current()
            analysis_message = stream_repo.add_message(
                conv_id, "system", analysis_text, provider=vision_name, model=vision_model,
                photo_fingerprint=analysis_fingerprint,
            )
            run.publish(sse_event("posted", str(analysis_message.id)))

        if existing:
            prompt_analysis = analysis_with_photo_count(photo_count, analysis_text)

        mark("photo_analysis")
        listing_rules = load_skill_rules(
            f"{prompt_analysis}\n{item_details}",
            stream_db,
        )

        if comps_task is not None and comps_brand != _brand(item_fields(prompt_analysis, evidence)):
            # The full read named a different brand than the quick one: comps
            # searched for the wrong item, so search again on the full read.
            log.info("quick identity disagreed with photo analysis for %s; restarting comps", conv_id)
            comps_task.cancel()
            comps_task = None

        if comps_on:
            run.publish(sse_event("status", "Identifying category and looking up comps…"))
        else:
            run.publish(sse_event("status", "Choosing marketplace categories…"))

        async def choose_categories():
            # Its own session: comps and this run's stream write beside it.
            db = SessionLocal()
            try:
                async with live_trace.agent("categories", "Categories") as agent:
                    def on_status(message: str) -> None:
                        run.publish(sse_event("status", message))
                        agent.update(message)

                    seed = await prepare_generation_schema(
                        db,
                        conv_id,
                        provider,
                        prompt_analysis + "\nSeller answers:\n" + seller_answers,
                        notes,
                        on_status=on_status,
                    )
                    if isinstance(seed, dict) and seed.get("category_path"):
                        agent.result(str(seed["category_path"]).split(">")[-1].strip())
                    return seed
            finally:
                db.close()

        schema_task = asyncio.create_task(timed("categories", choose_categories()))
        child_tasks.append(schema_task)
        comps_text = ""
        if comps_on:
            if comps_task is None:
                start_comps(research_sold_comps(prompt_analysis, evidence), "")
        else:
            # Nothing connected can search. Surface that in chat so the SOLD
            # COMPS card does not silently disappear.
            comps_text = comps_setup_note()

        pending = {schema_task, *([comps_task] if comps_task else [])}
        while pending:
            _done, pending = await asyncio.wait(pending, timeout=10.0, return_when=asyncio.FIRST_COMPLETED)
            run.pulse()
        mark("categories_and_comps")
        schema_task.result()
        from vendoo_studio.services.native_field_fill import prepare_generation_forms

        native_forms = await timed("native_forms", prepare_generation_forms(
            stream_db, conv_id, provider, evidence=f"{prompt_analysis}\n{item_details}"))
        if comps_task is not None:
            comps_text = comps_task.result() or ""
        if comps_text:
            still_current()
            comps_message = stream_repo.add_message(
                conv_id, "system", comps_text, provider="web-search", model="web-search",
            )
            run.publish(sse_event("posted", str(comps_message.id)))

        messages = listing_generation_messages(
            listing_rules,
            item_details,
            prompt_analysis,
            stream_db,
            conv_id,
            comps_text,
            photo_count=photo_count,
            native_forms=native_forms,
        )
        expected_revision_id = ConversationRepo(stream_db).current_revision_id(conv_id) or ""
        run.publish(sse_event("status", "thinking"))

        from vendoo_studio.services.option_lookup import OptionCatalog, chat_with_options

        async for item in iter_with_keepalives(chat_with_options(provider, messages, OptionCatalog(native_forms))):
            if item is None:
                run.pulse()
                continue
            payload, content = sse_for_stream_item(item)
            if content:
                full_text += content
            if payload:
                run.publish(payload)

        mark("listing_stream")
        if not full_text.strip() or full_text.lstrip().lower().startswith("error:"):
            raise RuntimeError(full_text.strip() or "Listing generation returned no text")
        if not extract_listing_json(full_text):
            run.publish(sse_event("status", "Repairing listing JSON…"))
        needed_repair = not extract_listing_json(full_text)
        still_current()
        stream_db.expire_all()
        if photo_fingerprint(stream_repo.get_photos(conv_id)) != analysis_fingerprint:
            raise RuntimeError("Photos changed during generation. Generate again to read the current photos.")
        run.publish(sse_event("status", "Filling required fields…"))
        listing = await await_with_pulses(
            run,
            persist_generated_listing_with_repair(
                stream_db, conv_id, full_text, provider, final_announce=False,
                expected_revision_id=expected_revision_id,
                expected_photo_fingerprint=analysis_fingerprint,
            ),
            child_tasks,
        )
        mark("repair_and_finalize")
        log.info(
            "generation timing conv=%s total=%.2fs %s",
            conv_id,
            time.monotonic() - run_started,
            " ".join(f"{stage}={seconds}s" for stage, seconds in timings.items()),
        )
        timing_id = save_timings("preparing")
        stream_repo.update_status(conv_id, "draft")
        run.publish("data: [DONE]\n\n")
        if listing:
            evidence_text = "\n\n".join(
                part for part in (prompt_analysis, item_details, comps_text) if part
            )
            # Registered before [DONE] reaches the client, so chat stays busy until it ends.
            activity.track_task(conv_id, "Preparing marketplace fields…", spawn(_finish_generation_background(
                conv_id,
                listing,
                evidence=evidence_text,
                provider=provider,
                repaired=needed_repair,
                timing_id=timing_id,
                run_started=run_started,
                agents_run=agents_run,
                expected_revision_id=ConversationRepo(stream_db).current_revision_id(conv_id),
                expected_photo_fingerprint=analysis_fingerprint,
            )))
    except asyncio.CancelledError:
        log.warning("listing generation cancelled for %s; saving any completed text", conv_id)
        for task in child_tasks:
            if not task.done():
                task.cancel()
        save_timings("cancelled")
        if timing_id is not None:
            generation_timing.add_stages(timing_id, timings, total_sec=time.monotonic() - run_started, outcome="cancelled", calls=calls)
        # Clear/Regenerate discarded this run; saving now would write the old
        # listing into the wiped conversation.
        if (
            not generation_discarded(conv_id, run)
            and full_text.strip()
            and not full_text.lstrip().lower().startswith("error:")
        ):
            try:
                await persist_generated_listing_with_repair(
                    stream_db, conv_id, full_text, provider,
                    expected_revision_id=expected_revision_id,
                    expected_photo_fingerprint=analysis_fingerprint,
                )
            except Exception:
                log.exception("failed to persist cancelled listing for %s", conv_id)
        try:
            stream_repo.update_status(conv_id, "draft")
        except Exception:
            log.exception("failed to reset status after cancelled listing for %s", conv_id)
        raise
    except Exception as e:
        save_timings("failed")
        if timing_id is not None:
            generation_timing.add_stages(timing_id, timings, total_sec=time.monotonic() - run_started, outcome="failed", calls=calls)
        message = str(e).strip() or type(e).__name__
        if isinstance(e, TimeoutError) and not str(e).strip():
            stage = (run.last_status or "").strip()
            if stage:
                message = (
                    f"Timed out during “{stage}”. Retry generate."
                )
            else:
                message = (
                    "Timed out while generating the listing. Retry generate."
                )
        log.warning("listing generation failed for %s: %s", conv_id, message)
        try:
            run.publish(sse_data(f"Error: {message}"))
            stream_repo.add_message(conv_id, "system", message, provider="system", model="")
            run.publish("data: [DONE]\n\n")
            stream_repo.update_status(conv_id, "draft")
        except Exception:
            log.exception("failed to report listing generation error for %s", conv_id)
    finally:
        for task in child_tasks:
            if not task.done():
                task.cancel()
        stream_db.close()


async def _finish_generation_background(
    conv_id: str,
    listing: dict,
    *,
    evidence: str,
    provider,
    repaired: bool = False,
    timing_id: str | None = None,
    run_started: float | None = None,
    agents_run: int = 0,
    expected_revision_id: str | None = None,
    expected_photo_fingerprint: str | None = None,
) -> None:
    """Fill remaining discovered fields after the generate stream ends.

    Generation reads category/schema data and stops at a saved Studio listing.
    Labels, photos and draft writes wait for the seller to press Send/Update.

    The stream has closed by now, so agents here reach the chat through
    activity polling (``GET /activity`` → ``agents``) instead of SSE.
    """
    live_trace.bind(
        lambda kind, text: activity.record_agent(conv_id, agents_run, text) if kind == "agent" else None
    )
    model_slots.bind()
    finish_started = time.monotonic()
    outcome = "superseded"
    stages: dict[str, float] = {}
    db = SessionLocal()
    repo = ConversationRepo(db)

    async def timed(stage: str, coro):
        started = time.monotonic()
        try:
            return await coro
        finally:
            stages[stage] = round(time.monotonic() - started, 2)

    try:
        if expected_revision_id is not None and ConversationRepo(db).current_revision_id(conv_id) != expected_revision_id:
            return
        current = dict(listing) if isinstance(listing, dict) else {}
        if expected_photo_fingerprint is None:
            expected_photo_fingerprint = photo_fingerprint(repo.get_photos(conv_id))

        def check_photos() -> None:
            db.expire_all()
            if photo_fingerprint(repo.get_photos(conv_id)) != expected_photo_fingerprint:
                raise RevisionConflict("Photos changed while preparing marketplace fields")

        check_photos()

        async def fill_native():
            from vendoo_studio.services.native_field_fill import fill_native_fields

            async with live_trace.agent("native-fields", "Marketplace category fields") as agent, model_slots.slot():
                filled = await fill_native_fields(db, conv_id, provider, evidence=evidence)
                agent.result("Filled category fields" if filled else "No category fields to fill")
                return filled

        check_photos()
        native = await timed("native_fields", fill_native())
        if native is not None:
            current = native
        from vendoo_studio.services.listing_generate import _validation_blockers

        outcome = "incomplete" if _validation_blockers(copy.deepcopy(current)) else "done"
        # Posted only now: said before the fill, it reported fields as empty
        # and "Send when ready" while chat was still filling them.
        repo.add_message(
            conv_id,
            "system",
            listing_save_summary(db, conv_id, copy.deepcopy(current), repaired=repaired),
            provider="system",
            model="",
        )
    except RevisionConflict:
        log.info("discarding stale generation finish for %s", conv_id)
    except Exception:
        outcome = "failed"
        log.exception("post-generate finish failed for %s", conv_id)
        try:
            repo.add_message(
                conv_id,
                "system",
                "Listing saved, but finishing discovered fields failed. Retry generate.",
                provider="system",
                model="",
            )
        except Exception:
            log.exception("failed to report post-generate finish error for %s", conv_id)
    finally:
        stages["finish"] = round(time.monotonic() - finish_started, 2)
        if run_started is not None:
            generation_timing.add_stages(timing_id, stages, total_sec=time.monotonic() - run_started, outcome=outcome, calls=model_usage.current())
        db.close()
