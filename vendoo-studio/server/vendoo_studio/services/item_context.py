"""Bounded context from durable seller edits and verbatim conversation evidence."""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from vendoo_studio.repositories.queries import ConversationRepo, ListingRepo
from vendoo_studio.services.chat_citations import expand_citations_for_provider
from vendoo_studio.services.seller_overrides import get_path, locked_paths
from vendoo_studio.services.text_retrieval import rank_texts


def seller_messages(messages: list, query: str, *, max_chars: int = 10000) -> list:
    """Keep recent seller turns plus relevant older turns, in original order.

    Stored text remains the source; model summaries never become seller facts.
    A character budget bounds text consistently across all supported providers.
    """
    rows = [message for message in messages if message.role == "user" and message.text.strip()]
    texts = [expand_citations_for_provider(message.text) for message in rows]
    selected = set()
    used = 0
    # Reserve half the budget for older evidence instead of letting long recent
    # messages evict every previous instruction.
    for index in range(len(rows) - 1, max(-1, len(rows) - 5), -1):
        if used + len(texts[index]) <= max_chars // 2:
            selected.add(index)
            used += len(texts[index])
    ranked = [index for index, _score in rank_texts(query, texts)]
    for index in [*ranked, *range(len(rows) - 1, -1, -1)]:
        if index not in selected and used + len(texts[index]) <= max_chars:
            selected.add(index)
            used += len(texts[index])
    return [rows[index] for index in sorted(selected)]


def seller_answers(repo: ConversationRepo, conv_id: str, query: str) -> str:
    rows = seller_messages(repo.get_messages(conv_id), query)
    return "\n".join(expand_citations_for_provider(row.text) for row in rows)


def item_decisions_prompt(db: Session, conv_id: str) -> str:
    """Latest seller-owned values always accompany retrieved message evidence."""
    from vendoo_studio.services.listing_generate import seller_item_details
    from vendoo_studio.services.listing_evidence import generation_evidence_prompt

    repo = ConversationRepo(db)
    conv = repo.get(conv_id)
    current = ListingRepo(db).get_current(conv_id)
    revision = ListingRepo(db).get_revision(current.current_revision_id) if current else None
    listing = revision.listing_json if revision else {}
    owned = {path: get_path(listing, path) for path in sorted(locked_paths(listing))
             if path.split(".")[0] not in {"cost", "photos", "photo_urls", "internal_notes"}}
    details = seller_item_details(conv.notes if conv else "")
    corrections = generation_evidence_prompt(db, conv_id)
    return (
        "\n\n--- Saved seller decisions ---\n"
        "These are stored item details and seller-owned values, including deliberate clears. "
        "Preserve them unless the seller's current request explicitly changes them. "
        "Earlier quoted messages are historical evidence; newer seller decisions take precedence.\n"
        + details + "\nSeller-owned fields: " + json.dumps(owned, ensure_ascii=False) + corrections
    )
