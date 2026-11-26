"""The seller's per-listing choice and the General ad it manages."""
from __future__ import annotations

import asyncio
from decimal import Decimal, InvalidOperation
import re

from anteroom.services.ebay_actions import ad_rate, campaign_ads, create_listing_ad
from anteroom.services.ebay_client import EbayClient, EbayFailure, segment
from anteroom.services.listing_result import missing
from anteroom.services.seller_region import seller_region

CAMPAIGNS = "/sell/marketing/v1/ad_campaign"
SECTION = "Promote your listing"
PREFIX = "ebay_specifics.promotion"
WARNING_CODE = "ebay_promotion_unconfirmed"
UNCONFIRMED = ("The listing is live. eBay did not confirm the promotion change. "
               "Check the campaign in Marketplaces → eBay → Marketing, then use Update on eBay to retry.")


def choice(snapshot: dict) -> dict:
    details = snapshot.get("ebay_specifics")
    value = details.get("promotion") if isinstance(details, dict) else None
    return value if isinstance(value, dict) else {}


def has_change(action: str, promotion: dict, previous: dict) -> bool:
    enabled = promotion.get("enabled")
    return (action in {"list", "update"} and enabled is True
            or action == "update" and enabled is False and bool(previous))


def validate(promotion: dict) -> tuple[str, Decimal]:
    campaign = str(promotion.get("campaignId") or "")
    if not re.fullmatch(r"[0-9]{1,64}", campaign):
        raise missing("Choose an existing eBay General campaign.", f"{PREFIX}.campaignId")
    try:
        return campaign, ad_rate(str(promotion.get("adRate") or ""))
    except (InvalidOperation, EbayFailure):
        raise missing("Enter an ad rate from 2% to 100%, with at most one decimal place.", f"{PREFIX}.adRate") from None


def _eligible(campaign: dict, marketplace: str) -> bool:
    funding = campaign.get("fundingStrategy") or {}
    return (campaign.get("marketplaceId") == marketplace
            and campaign.get("campaignStatus") == "RUNNING"
            and not campaign.get("campaignCriterion")
            and funding.get("fundingModel") == "COST_PER_SALE"
            and funding.get("adRateStrategy") in (None, "FIXED"))


async def campaigns() -> list[dict]:
    options: list[dict] = []
    async with asyncio.timeout(25):
        client = await EbayClient.connect()
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
    async with asyncio.timeout(15):
        client = await EbayClient.connect()
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
    await remember({"ebay_promotion": {"listing_id": listing_id, "campaign_id": selected[0],
                                       "account_id": client.seller[0]}})
    if not ad:
        await create_listing_ad(client, path, listing_id, rate)
    elif Decimal(str(ad.get("bidPercentage") or "0")) != rate:
        await client.request("POST", f"{path}/ad/{segment(ad['adId'])}/update_bid", body={"bidPercentage": str(rate)})
