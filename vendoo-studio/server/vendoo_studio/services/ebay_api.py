"""Only the eBay reads and Negotiation API call used by seller offers.

Tokens stay on the Mac. Never log request headers or persist buyer responses.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation

import httpx

from vendoo_studio.services.ebay_client import TRADING_XML_DECLARATION
from vendoo_studio.services.marketplace_accounts import SignInRequired, access_token

API_URL = "https://api.ebay.com"
NS = {"e": "urn:ebay:apis:eBLBaseComponents"}


class EbayError(RuntimeError):
    """A definite read failure or an explicitly rejected write."""


class SendUnknown(RuntimeError):
    """eBay may have received a write. It must never be retried automatically."""


def money_cents(value: object) -> int:
    try:
        amount = Decimal(str(value))
        exponent = amount.as_tuple().exponent
        if not amount.is_finite() or amount <= 0 or amount > Decimal("9999999.99") or not isinstance(exponent, int) or exponent < -2:
            raise ValueError
        return int(amount * 100)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("Enter a positive price with at most two decimal places.") from exc


def dollars(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def _check_response(response: httpx.Response) -> None:
    if response.status_code in (401, 403):
        raise SignInRequired("Reconnect eBay to grant permission to manage offers.")
    if not response.is_success:
        # Error bodies may contain seller or buyer details. Only expose the code.
        raise EbayError(f"eBay rejected the request (HTTP {response.status_code}). Check Seller Hub.")


async def eligible_items() -> set[str]:
    token = await access_token("ebay")
    ids: set[str] = set()
    async with httpx.AsyncClient(timeout=30) as client:
        for offset in range(0, 100000, 200):
            response = await client.get(
                f"{API_URL}/sell/negotiation/v1/find_eligible_items",
                headers={"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": "EBAY_US"},
                params={"limit": 200, "offset": offset},
            )
            _check_response(response)
            if response.status_code == 204:
                return ids
            body = response.json()
            items = body.get("eligibleItems", [])
            ids.update(str(item["listingId"]) for item in items)
            if not body.get("next"):
                return ids
    raise EbayError("There are too many eligible listings to check safely.")


async def listing_price(listing_id: str) -> int:
    """Read the live fixed price; never calculate offers from a cached Vendoo price."""
    if not listing_id.isdigit():
        raise EbayError("Invalid eBay listing ID.")
    token = await access_token("ebay")
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{API_URL}/ws/api.dll",
            headers={
                "X-EBAY-API-IAF-TOKEN": token,
                "X-EBAY-API-CALL-NAME": "GetItem",
                "X-EBAY-API-SITEID": "0",
                "X-EBAY-API-COMPATIBILITY-LEVEL": "1487",
                "Content-Type": "text/xml",
            },
            content=(f'{TRADING_XML_DECLARATION}<GetItemRequest xmlns="{NS["e"]}">'
                     f'<ItemID>{listing_id}</ItemID><DetailLevel>ReturnAll</DetailLevel></GetItemRequest>'),
        )
    _check_response(response)
    try:
        root = ET.fromstring(response.content)
        if root.findtext("e:Ack", namespaces=NS) not in ("Success", "Warning"):
            raise EbayError("eBay could not read this listing. Check its status in Seller Hub.")
        item = root.find("e:Item", NS)
        if item is None or item.findtext("e:ItemID", namespaces=NS) != listing_id:
            raise EbayError("eBay returned a different listing. Refresh the preview.")
        if (item.findtext("e:SellingStatus/e:ListingStatus", namespaces=NS) != "Active"
                or item.findtext("e:ListingType", namespaces=NS) != "FixedPriceItem"
                or item.findtext("e:Site", namespaces=NS) != "US"
                or item.find("e:Variations", NS) is not None):
            raise EbayError("Offers currently support active US fixed-price listings without variations.")
        price = item.find("e:SellingStatus/e:CurrentPrice", NS)
        if price is None or price.get("currencyID") != "USD":
            raise EbayError("Offers currently support prices in US dollars only.")
        return money_cents(price.text)
    except (ET.ParseError, ValueError) as exc:
        raise EbayError("eBay returned an unreadable price. Try previewing again.") from exc


async def send_offer(listing_id: str, price_cents: int, *, token: str) -> int:
    # Authentication must complete before the caller revalidates approval and
    # reserves the send. Refreshing here would allow a pause to be overlooked.
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{API_URL}/sell/negotiation/v1/send_offer_to_interested_buyers",
                headers={"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": "EBAY_US"},
                json={
                    "allowCounterOffer": False,
                    # Let eBay choose its current default offer duration.
                    "offeredItems": [{"listingId": listing_id, "quantity": 1,
                                      "price": {"value": dollars(price_cents), "currency": "USD"}}],
                },
            )
    except httpx.HTTPError as exc:
        raise SendUnknown("The send result is unknown. Check offers in Seller Hub before trying again.") from exc
    if response.status_code >= 500 or 300 <= response.status_code < 400 or response.status_code == 408:
        raise SendUnknown("eBay did not confirm the send. Check offers in Seller Hub before trying again.")
    _check_response(response)
    try:
        offers = response.json()["offers"]
        if not isinstance(offers, list) or not offers or any(not offer.get("offerId") for offer in offers):
            raise ValueError
        return len(offers)
    except (KeyError, TypeError, ValueError) as exc:
        raise SendUnknown("eBay did not return a send receipt. Check offers in Seller Hub before trying again.") from exc
