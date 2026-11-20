"""The last marketplace statuses Vendoo reported for an imported item.

Nothing writes this cache any more; the sidebar chips and hover popup still
read it for items imported from Vendoo.
"""

from __future__ import annotations


def draft_has_status_signal(payload: object) -> bool:
    """True when a cached draft can say anything about a marketplace."""
    if not isinstance(payload, dict):
        return False
    return bool(payload.get("item") or payload.get("form") or payload.get("statuses"))
