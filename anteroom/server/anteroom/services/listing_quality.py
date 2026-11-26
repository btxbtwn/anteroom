"""Measure hosted generation using counts and fixed field labels."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
from decimal import InvalidOperation

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from anteroom.models.conversation import utcnow
from anteroom.models.listing import ListingRevision
from anteroom.models.listing_quality import ListingQualityAttempt
from anteroom.models.user_preference import UserPreference
from anteroom.services.hosted_database import SellerDatabaseSession
from anteroom.services.seller_overrides import (
    CANONICAL_PRICE,
    MARKETPLACES,
    fold_key,
    get_path,
    is_price_key,
    locked_paths,
    marketplace_of,
    money,
)

CONSENT = "listing_quality_telemetry"
GENERATED = frozenset({"model", "generation_finalize", "native_fields", "native_categories", "native_schema"})
GENERAL = frozenset({
    "title", "description", "brand", "condition", "category_path", "department", "size", "sizeType",
    "material", "style", "tags", "flaws", "measurements", "price", "primaryColor", "secondaryColor",
})
MARKET_FIELDS = {
    "title": "title", "description": "description", "brand": "brand", "condition": "condition",
    "size": "size", "nativesizeid": "size", "department": "department",
    "material": "material", "style": "style",
    "primarycolor": "primary_color", "secondarycolor": "secondary_color",
}


def consent_enabled(db: SellerDatabaseSession, *, lock: bool = False) -> bool:
    query = select(UserPreference.value).where(
        UserPreference.user_id == db.user_id, UserPreference.key == CONSENT,
    )
    # Shared row locks allow independent drafts to write concurrently. Opt-out
    # waits for these transactions and removes their observations before returning.
    if lock:
        query = query.with_for_update(read=True)
    return db.scalar(query) is True


def set_consent(db: SellerDatabaseSession, enabled: bool) -> None:
    stmt = insert(UserPreference).values(user_id=db.user_id, key=CONSENT, value=enabled)
    db.execute(stmt.on_conflict_do_update(
        index_elements=[UserPreference.user_id, UserPreference.key], set_={"value": stmt.excluded.value},
    ))
    if not enabled:
        db.execute(delete(ListingQualityAttempt).where(ListingQualityAttempt.user_id == db.user_id))
    db.commit()


def _group(path: str) -> str | None:
    if path in GENERAL:
        return f"general.{path}"
    root, _, field = path.partition(".")
    for market in MARKETPLACES:
        if root == f"{market}_specifics" and field and not field.startswith("_"):
            label = "price" if is_price_key(field) else MARKET_FIELDS.get(fold_key(field), "specifics")
            return f"{market}.{label}"
        if root == "direct_category_ids" and field in {market, f"{market}_path"}:
            return f"{market}.category"
    return None


def _empty(value: object) -> bool:
    return value is None or value in ("", [], {}) or isinstance(value, str) and not value.strip()


def _change(old: object, new: object) -> str | None:
    old_empty, new_empty = _empty(old), _empty(new)
    if old == new or old_empty and new_empty:
        return None
    return "filled" if old_empty else "cleared" if new_empty else "replaced"


def _changes(before: dict, after: dict, paths: set[str]) -> dict[str, dict[str, int]]:
    if not paths:
        return {}
    counts: defaultdict[str, Counter[str]] = defaultdict(Counter)
    prior_values, saved_values = map(_form_values, (before, after))
    seen_prices: set[str] = set()
    for path in paths:
        group = _group(path)
        if group is None:
            continue
        canonical = _canonical_price(path)
        old, new = get_path(before, path), get_path(after, path)
        if canonical is not None:
            if canonical in seen_prices:
                continue
            seen_prices.add(canonical)
            old, new = prior_values.get(canonical), saved_values.get(canonical)
        change = _change(old, new)
        if change:
            counts[group][change] += 1
    return {group: dict(count) for group, count in counts.items()}


def _canonical_price(path: str) -> str | None:
    market = marketplace_of(path)
    field = path.partition(".")[2]
    return CANONICAL_PRICE[market] if market and is_price_key(field) else None


def _form_values(listing: dict, previous: dict | None = None) -> dict[str, object]:
    from anteroom.services.hosted_listing_contract import operational_key

    values = {field: listing[field] for field in GENERAL if field in listing}
    categories = listing.get("direct_category_ids")
    for market in MARKETPLACES:
        if isinstance(categories, dict) and (market in categories or f"{market}_path" in categories):
            values[f"direct_category_ids.{market}"] = categories.get(market, categories.get(f"{market}_path"))
        bucket = f"{market}_specifics"
        specifics = listing.get(bucket) or {}
        specifics = specifics if isinstance(specifics, dict) else {}
        prior = (previous or {}).get(bucket) or {}
        prior = prior if isinstance(prior, dict) else {}
        prices = sorted(key for key in specifics if is_price_key(key))
        canonical = CANONICAL_PRICE[market]
        key = canonical.partition(".")[2]
        explicitly_changed = [name for name in prices if _change(prior.get(name), specifics.get(name))]
        choices = explicitly_changed if previous is not None and explicitly_changed else prices
        if choices:
            value = specifics.get(key if key in choices else choices[0])
            try:
                amount = money(value)
            except InvalidOperation:
                amount = None
            values[canonical] = amount if amount is not None else value
        values.update({
            f"{bucket}.{key}": value for key, value in specifics.items()
            if not is_price_key(key) and not operational_key(key)
        })
    return values


def _form_corrections(
    historical: dict[str, dict[str, int]], baseline: dict, before: dict, submitted: dict,
) -> dict[str, dict[str, int]]:
    generated, previous = map(_form_values, (baseline, before))
    reviewed = _form_values(submitted, before)
    counts = {field: Counter(kinds) for field, kinds in historical.items()}
    for path in sorted(previous.keys() | reviewed.keys()):
        group = _group(path)
        old, new = previous.get(path), reviewed.get(path)
        if group is None:
            continue
        # An earlier save can remove the explicit marketplace value when it
        # follows General. A later submitted blank still replaces that choice.
        explicit_blank = (
            group in counts and not group.startswith("general.") and not group.endswith(".specifics")
            and path not in previous and path in reviewed and _empty(new)
        )
        if not _change(old, new) and not explicit_blank:
            continue
        expected = generated.get(path)
        change = _change(expected, new)
        if group.endswith(".specifics"):
            # Marketplace-defined and seller-defined labels share an anonymous
            # bucket. Counts describe the net changes in this group.
            bucket = counts.setdefault(group, Counter())
            if previous_change := _change(expected, old):
                bucket[previous_change] = max(0, bucket[previous_change] - 1)
            if change:
                bucket[change] += 1
        elif change:
            counts[group] = Counter({change: 1})
        else:
            counts.pop(group, None)
    return {field: dict(+kinds) for field, kinds in counts.items() if +kinds}


def _protected_paths(listing: dict) -> set[str]:
    return {path for path in locked_paths(listing) if _group(path) is not None}


def _attempt(db: Session, revision: ListingRevision) -> ListingQualityAttempt | None:
    return db.scalar(select(ListingQualityAttempt).where(
        ListingQualityAttempt.revision_id == revision.id,
        ListingQualityAttempt.conversation_id == revision.conversation_id,
    ).with_for_update())


def record_revision(
    db: Session, previous: ListingRevision | None, revision: ListingRevision, *,
    seller_input: dict | None = None, restore: bool = False,
) -> None:
    source = revision.source
    if not isinstance(db, SellerDatabaseSession) or previous is None and source != "model":
        return
    if not consent_enabled(db, lock=True):
        return
    before = previous.listing_json if previous is not None and isinstance(previous.listing_json, dict) else {}
    after = revision.listing_json if isinstance(revision.listing_json, dict) else {}
    if source == "model":
        paths = _protected_paths(before)
        overwrites = _changes(before, after, paths)
        db.add(ListingQualityAttempt(
            id=revision.id, revision_id=revision.id, generated_revision_id=revision.id,
            conversation_id=revision.conversation_id,
            overwrites={source: overwrites} if overwrites else {},
            overwrite_compared=bool(paths),
        ))
        return
    if previous is None:
        return
    attempt = _attempt(db, previous)
    if attempt is None:
        return
    if restore:
        attempt.corrections = {}
    elif seller_input is not None:
        baseline = previous if previous.id == attempt.generated_revision_id else db.get(
            ListingRevision, attempt.generated_revision_id,
        )
        if baseline is not None:
            attempt.corrections = _form_corrections(
                attempt.corrections, baseline.listing_json, before, seller_input,
            )
        if attempt.reviewed_at is None:
            attempt.reviewed_at = utcnow()
    elif source in GENERATED:
        paths = _protected_paths(before)
        overwrites = _changes(before, after, paths)
        attempt.overwrite_compared = attempt.overwrite_compared or bool(paths)
        if overwrites:
            prior = attempt.overwrites.get(source, {})
            counts = {
                key: dict(Counter(prior.get(key, {})) + Counter(overwrites.get(key, {})))
                for key in prior.keys() | overwrites.keys()
            }
            attempt.overwrites = {**attempt.overwrites, source: counts}
        if attempt.generated_revision_id == previous.id:
            attempt.generated_revision_id = revision.id
    elif source == "photo_removed" and attempt.generated_revision_id == previous.id:
        attempt.generated_revision_id = revision.id
    attempt.revision_id = revision.id


def record_review(db: Session, conversation_id: str) -> None:
    if not isinstance(db, SellerDatabaseSession) or not consent_enabled(db, lock=True):
        return
    from anteroom.repositories.hosted_drafts import current_fields_revision

    revision = current_fields_revision(db, conversation_id)
    attempt = _attempt(db, revision) if revision is not None else None
    if attempt is not None and attempt.reviewed_at is None:
        attempt.reviewed_at = utcnow()


def operator_report(since: datetime, until: datetime) -> dict:
    from anteroom.database import engine

    with engine.connect() as connection:
        summary = connection.execute(
            text("SELECT * FROM anteroom_private.listing_quality_report(:since, :until)"),
            {"since": since.astimezone(UTC), "until": until.astimezone(UTC)},
        ).mappings().one()
    compared = summary["overwrite_compared_attempts"]
    reviewed = summary["reviewed_attempts"]
    return {
        **summary,
        "correction_rate": summary["corrected_attempts"] / reviewed if reviewed else None,
        "overwrite_rate": summary["overwritten_attempts"] / compared if compared else None,
    }
