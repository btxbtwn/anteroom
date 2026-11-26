"""The seller's per-listing choice and the General ad it manages."""
from __future__ import annotations

import asyncio
from decimal import Decimal, InvalidOperation
import re
from typing import Literal

from anteroom.services.ebay_actions import ad_rate, campaign_ads, create_listing_ad
from anteroom.services.ebay_client import EbayClient, EbayFailure, segment
from anteroom.services.listing_result import ListingProblem, missing
from anteroom.services.seller_region import seller_region

CAMPAIGNS = "/sell/marketing/v1/ad_campaign"
SECTION = "Promote your listing"
PREFIX = "ebay_specifics.promotion"
PromotionMode = Literal["confirm", "leave", "recreate"]
WARNING_CODE = "ebay_promotion_unconfirmed"
UNCONFIRMED = ("The listing is live. eBay did not confirm the promotion change. "
               "Check the campaign in Marketplaces → eBay → Marketing, then use Update on eBay to retry.")
NO_APPLIED_PROMOTION = ("The listing is live. Promotion was not applied. "
                        "Review the eBay form and confirm Update on eBay to apply it.")


def choice(snapshot: dict) -> dict:
    details = snapshot.get("ebay_specifics")
    value = details.get("promotion") if isinstance(details, dict) else None
    return value if isinstance(value, dict) else {}


def has_change(action: str, promotion: dict, previous: dict) -> bool:
    enabled = promotion.get("enabled")
    return (action in {"list", "update"} and enabled is True
            or action == "update" and enabled is not True and bool(previous))


def has_disabled_live_ad(snapshot: dict, binding: dict) -> bool:
    ad = binding.get("ebay_promotion") or {}
    return (choice(snapshot).get("enabled") is not True and bool(ad.get("listing_id"))
            and ad["listing_id"] == binding.get("listing_id"))


def validate(promotion: dict) -> tuple[str, Decimal]:
    campaign = str(promotion.get("campaignId") or "")
    if not re.fullmatch(r"[0-9]{1,64}", campaign):
        raise missing("Choose an existing eBay General campaign.", f"{PREFIX}.campaignId")
    try:
        return campaign, ad_rate(str(promotion.get("adRate") or ""))
    except (InvalidOperation, EbayFailure):
        raise missing("Enter an ad rate from 2% to 100%, with at most one decimal place.", f"{PREFIX}.adRate") from None


def selection(snapshot: dict) -> tuple[str, Decimal] | None:
    chosen = choice(snapshot)
    if chosen.get("enabled") is not True:
        return None
    from anteroom.services.marketplace_schemas import _ebay_listing_type

    if _ebay_listing_type(snapshot["ebay_specifics"].get("listingFormat")) == "AUCTION":
        raise missing("Promoted Listings requires a fixed-price eBay listing.", f"{PREFIX}.enabled")
    return validate(chosen)


def confirmed_selection(snapshot: dict, confirmed_rate: str | None) -> tuple[str, Decimal] | None:
    selected = selection(snapshot)
    if selected is None:
        return None
    try:
        rate = Decimal(confirmed_rate or "")
    except InvalidOperation:
        rate = None
    if rate is None or not rate.is_finite() or rate != selected[1]:
        raise missing(f"Confirm the saved {selected[1]}% eBay ad rate before sending this listing. "
                      "Update Anteroom if this device cannot confirm it.", f"{PREFIX}.adRate")
    return selected


def recreate_from(record: dict, old_listing_id: str | None) -> dict | None:
    applied = record.get("ebay_promotion")
    if not isinstance(applied, dict) or not applied.get("account_id") or not applied.get("applied_rate"):
        return None
    if not old_listing_id or applied.get("listing_id") != old_listing_id:
        return None
    try:
        validate({"campaignId": applied.get("campaign_id"), "adRate": applied["applied_rate"]})
    except ListingProblem:
        return None
    return applied


def _eligible(campaign: dict, marketplace: str) -> bool:
    funding = campaign.get("fundingStrategy") or {}
    return (campaign.get("marketplaceId") == marketplace
            and campaign.get("campaignStatus") == "RUNNING"
            and not campaign.get("campaignCriterion")
            and funding.get("fundingModel") == "COST_PER_SALE"
            and funding.get("adRateStrategy") in (None, "FIXED"))


async def campaigns() -> list[dict]:
    options: list[dict] = []
    from anteroom.services.marketplace_schemas import shared_ebay_client

    async with asyncio.timeout(25):
        client = await shared_ebay_client()
        marketplace = (await asyncio.to_thread(seller_region)).ebay_marketplace
        for offset in range(0, 10000, 100):
            page = await client.request("GET", CAMPAIGNS, params={
                "funding_strategy": "COST_PER_SALE", "campaign_status": "RUNNING", "limit": 100, "offset": offset,
            })
            options.extend(campaign for campaign in page.get("campaigns", []) if _eligible(campaign, marketplace))
            if not page.get("next"):
                return options
    raise EbayFailure("eBay has too many campaigns to load here.")


async def suggested_rate(listing_id: str) -> str:
    if not re.fullmatch(r"[0-9]{6,20}", listing_id):
        return ""
    from anteroom.services.marketplace_schemas import shared_ebay_client

    async with asyncio.timeout(15):
        client = await shared_ebay_client()
        page = await client.request("POST", "/sell/recommendation/v1/find",
                                    params={"filter": "recommendationTypes:{AD}"},
                                    body={"listingIds": [listing_id]})
    for recommendation in page.get("listingRecommendations", []):
        if recommendation.get("listingId") != listing_id:
            continue
        for rate in recommendation.get("marketing", {}).get("ad", {}).get("bidPercentages", []):
            if rate.get("basis") == "TRENDING":
                return str(ad_rate(str(rate.get("value"))))
    return ""


async def _ad(client: EbayClient, campaign_id: str, listing_id: str) -> dict:
    ads = await campaign_ads(client, segment(campaign_id), listing_id=listing_id)
    matches = [ad for ad in ads if str(ad.get("listingId")) == listing_id and ad.get("adId")]
    if len(matches) > 1:
        raise EbayFailure("eBay returned more than one ad for this listing and campaign.")
    return matches[0] if matches else {}


async def reconcile(client: EbayClient, listing_id: str, selected: tuple[str, Decimal] | None,
                    previous: dict, remember, active) -> None:
    """Read before changing ads, including after a lost write reply."""
    prior = previous.get("campaign_id") if previous.get("listing_id") == listing_id else None
    if prior and previous.get("account_id") != client.seller[0]:
        raise EbayFailure("This promotion belongs to a different eBay account.")
    campaign_id = selected[0] if selected else None
    path = f"{CAMPAIGNS}/{segment(campaign_id)}" if campaign_id else ""
    if campaign_id:
        marketplace = (await asyncio.to_thread(seller_region)).ebay_marketplace
        if not _eligible(await client.request("GET", path), marketplace):
            raise EbayFailure("Choose a running General campaign with manually selected listings and a fixed ad rate.")
    if prior and prior != campaign_id:
        ad = await _ad(client, prior, listing_id)
        if ad and await active():
            await client.request("DELETE", f"{CAMPAIGNS}/{segment(prior)}/ad/{segment(ad['adId'])}")
        elif ad:
            raise EbayFailure("The eBay promotion step was cancelled.")
        await remember({"ebay_promotion": None})
    if not selected:
        return
    rate = selected[1]
    ad = await _ad(client, selected[0], listing_id)
    if not await active():
        raise EbayFailure("The eBay promotion step was cancelled.")
    target = {"listing_id": listing_id, "campaign_id": selected[0], "account_id": client.seller[0]}
    if prior != campaign_id:
        await remember({"ebay_promotion": target})
    if not await active():
        raise EbayFailure("The eBay promotion step was cancelled.")
    if not ad:
        await create_listing_ad(client, path, listing_id, rate)
    elif Decimal(str(ad.get("bidPercentage") or "0")) != rate:
        await client.request("POST", f"{path}/ad/{segment(ad['adId'])}/update_bid", body={"bidPercentage": str(rate)})
    await remember({"ebay_promotion": {**target, "applied_rate": str(rate)}})
