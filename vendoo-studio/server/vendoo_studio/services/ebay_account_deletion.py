"""Delete what Anteroom holds about an eBay account eBay reports deleted.

eBay's Marketplace Account Deletion policy asks every app to delete the data
it holds about an eBay user once eBay says the account is closed. The relay
routes that notification only to the seller whose connected eBay account it
names, and Anteroom by design keeps no buyer details from eBay (no names,
addresses or messages: see the ``marketplace_sale``, ``ebay_action`` and
``ebay_offer`` models). What it does hold about the seller's eBay account, and
deletes here:

- the eBay sign-in: tokens, the eBay user ID and username (Keychain or Vault);
- the eBay business policies and item locations kept for the listing form;
- each item's eBay listing record (eBay listing, offer and SKU IDs, eBay photo
  URLs, eBay's messages);
- the eBay action receipts and the eBay offer rules, approvals and sends kept
  under that account's ID.

The seller's own items, photos and listing text stay: they are the seller's
work, not data from eBay. So does the sales ledger (order and line numbers,
dates, amounts), the seller's record of their own income, which tax rules
require them to keep; it holds no buyer details.

Runs in a worker thread: the reads and deletes are Keychain or Vault calls and
database writes. Deleting the sign-in comes last, so a failure part way leaves
the account connected and the relay's retry runs the whole purge again. Once
the sign-in is gone, a replayed notification finds no matching account and
does nothing.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import cast

from sqlalchemy import CursorResult, delete, select
from sqlalchemy.orm import Session

from vendoo_studio.models.ebay_action import EbayAction
from vendoo_studio.models.ebay_offer import EbayOfferAttempt, EbayOfferPolicy, EbayOfferRule
from vendoo_studio.models.marketplace_listing import MarketplaceListing
from vendoo_studio.services import saved_choices
from vendoo_studio.services.keychain import delete_marketplace_account, get_marketplace_accounts

EBAY_CHOICES = "ebay-policies"
log = logging.getLogger(__name__)


def purge(user_id: str | None, session_factory: Callable[[], Session]) -> bool:
    """Delete this eBay account's data if it is the connected one. Returns whether it was."""
    account = get_marketplace_accounts().get("ebay") or {}
    if not user_id or str(account.get("accountId") or "") != user_id:
        log.info("eBay reported an account closed that is not the connected one; nothing to delete")
        return False
    with session_factory() as db:
        def deleted(statement) -> int:
            return cast(CursorResult, db.execute(statement)).rowcount

        rules = select(EbayOfferRule.id).where(EbayOfferRule.account_id == user_id)
        counts = {
            "offer sends": deleted(delete(EbayOfferAttempt).where(EbayOfferAttempt.rule_id.in_(rules))),
            "offer rules": deleted(delete(EbayOfferRule).where(EbayOfferRule.account_id == user_id)),
            "offer approvals": deleted(delete(EbayOfferPolicy).where(EbayOfferPolicy.account_id == user_id)),
            "action receipts": deleted(delete(EbayAction).where(EbayAction.account_id == user_id)),
            "listing records": deleted(delete(MarketplaceListing).where(MarketplaceListing.marketplace == "ebay")),
        }
        db.commit()
    if saved_choices.saved(EBAY_CHOICES, user_id) is not None:
        saved_choices.forget(EBAY_CHOICES)
    delete_marketplace_account("ebay")
    log.warning("eBay reported the connected account closed; deleted its sign-in, saved eBay policies and %s",
                ", ".join(f"{count} {name}" for name, count in counts.items()))
    return True
