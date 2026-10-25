"""Reviewed eBay writes. Persist hashes and receipts, never private request bodies."""
from __future__ import annotations

import asyncio
import base64
import binascii
import io
import json
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Literal
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

from pydantic import BaseModel, ConfigDict, Field
from PIL import Image
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from vendoo_studio.models.ebay_action import EbayAction
from vendoo_studio.services.ebay_api import dollars, money_cents
from vendoo_studio.services.ebay_client import EbayClient, EbayFailure, NS, assert_identity, digest, identity, segment, trading_xml
from vendoo_studio.services.ebay_workspace import listing


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Message(Input):
    conversation_id: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=2000)


class MarkRead(Input):
    conversation_id: str = Field(min_length=1, max_length=200)


class Feedback(Input):
    listing_id: str = Field(pattern=r"^\d{6,20}$")
    transaction_id: str = Field(min_length=1, max_length=200)
    line_item_id: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=80)


class Shipment(Input):
    order_id: str = Field(min_length=1, max_length=200)
    carrier: str = Field(pattern=r"^[A-Za-z0-9_-]{1,30}$")
    tracking: str = Field(min_length=1, max_length=100)


class Refund(Input):
    order_id: str = Field(min_length=1, max_length=200)
    amount: str = Field(max_length=20)
    reason: Literal["BUYER_CANCEL", "SELLER_CANCEL", "ITEM_NOT_RECEIVED", "BUYER_RETURN", "ITEM_NOT_AS_DESCRIBED", "OTHER_ADJUSTMENT", "SHIPPING_DISCOUNT"]


class ReturnAddress(Input):
    fullName: str = Field(min_length=1, max_length=100)
    addressLine1: str = Field(min_length=1, max_length=200)
    addressLine2: str = Field(default="", max_length=200)
    city: str = Field(min_length=1, max_length=100)
    stateOrProvince: str = Field(pattern=r"^[A-Z]{2}$")
    postalCode: str = Field(pattern=r"^\d{5}(-\d{4})?$")
    country: Literal["US"] = "US"
    phone: str = Field(pattern=r"^\+?[0-9 ()-]{7,30}$")


class Contest(Input):
    dispute_id: str = Field(min_length=1, max_length=200)
    note: str = Field(min_length=1, max_length=1000)
    return_address: ReturnAddress | None = None


class Evidence(Input):
    dispute_id: str = Field(min_length=1, max_length=200)
    file_id: str = Field(min_length=1, max_length=200)
    evidence_type: Literal["PROOF_OF_DELIVERY", "PROOF_OF_AUTHENTICITY", "PROOF_OF_ITEM_AS_DESCRIBED", "PROOF_OF_CREDIT_NOT_DUE", "PROOF_OF_RETURN_NOT_RECEIVED", "OPTIONAL_SUPPORTING_DOCUMENTS"]


class Promotion(Input):
    name: str = Field(min_length=1, max_length=90)
    listing_ids: list[str] = Field(min_length=1, max_length=50)
    percent: int = Field(strict=True, ge=5, le=80)
    start: datetime
    end: datetime
    quantity: int = Field(default=2, strict=True, ge=2, le=10)
    image_url: str = Field(min_length=1, max_length=2000)


class Campaign(Input):
    name: str = Field(min_length=1, max_length=80)
    bid_percent: str = Field(pattern=r"^\d{1,3}(?:\.\d)?$", max_length=5)
    start: datetime


class Ad(Input):
    campaign_id: str = Field(min_length=1, max_length=200)
    listing_id: str = Field(pattern=r"^\d{6,20}$")
    bid_percent: str = Field(pattern=r"^\d{1,3}(?:\.\d)?$", max_length=5)


class Control(Input):
    resource_id: str = Field(min_length=1, max_length=200)
    action: Literal["pause", "resume", "end"]


class Email(Input):
    subject: str = Field(min_length=1, max_length=70)
    message: str = Field(min_length=1, max_length=1000)
    audience_codes: list[str] = Field(min_length=1, max_length=20)
    listing_ids: list[str] = Field(min_length=1, max_length=10)
    schedule: datetime


class Draft(Input):
    sku: str = Field(pattern=r"^[A-Za-z0-9_-]{1,50}$")
    title: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=4000)
    category_id: str = Field(pattern=r"^\d{1,15}$")
    price: str = Field(max_length=20)
    quantity: int = Field(default=1, strict=True, ge=1, le=10000)
    condition: Literal["NEW", "NEW_OTHER", "NEW_WITH_DEFECTS", "PRE_OWNED_EXCELLENT", "USED_EXCELLENT",
                       "PRE_OWNED_FAIR", "USED_VERY_GOOD", "USED_GOOD", "USED_ACCEPTABLE"]
    image_urls: list[str] = Field(min_length=1, max_length=12)
    aspects: dict[str, list[str]] = Field(default_factory=dict)
    fulfillment_policy_id: str = Field(min_length=1, max_length=200)
    payment_policy_id: str = Field(min_length=1, max_length=200)
    return_policy_id: str = Field(min_length=1, max_length=200)
    merchant_location_key: str = Field(min_length=1, max_length=200)
    weight_oz: str = Field(default="16", pattern=r"^\d+(?:\.\d+)?$", max_length=15)


class Revision(Input):
    listing_id: str = Field(pattern=r"^\d{6,20}$")
    title: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=4000)
    price: str = Field(max_length=20)
    quantity: int = Field(strict=True, ge=1, le=10000)


class Offer(Input):
    offer_id: str = Field(min_length=1, max_length=200)
    marketplace: Literal["ebay"]


class InventoryRevision(Input):
    offer_id: str = Field(min_length=1, max_length=200)
    price: str = Field(max_length=20)
    quantity: int = Field(strict=True, ge=1, le=10000)


class Report(Input):
    campaign_id: str = Field(min_length=1, max_length=200)
    start: date
    end: date


class ImageUpload(Input):
    data: str = Field(min_length=1, max_length=11000000)
    mime: Literal["image/jpeg", "image/png"]
    dispute_id: str = Field(default="", max_length=200)


def image_bytes(data: dict, *, maximum: int = 8000000) -> bytes:
    try:
        raw = base64.b64decode(data["data"], validate=True)
        if not 0 < len(raw) <= maximum:
            raise ValueError
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in ("JPEG", "PNG") or (image.format == "JPEG") != (data["mime"] == "image/jpeg"):
                raise ValueError
            image.verify()
        return raw
    except (ValueError, binascii.Error, OSError) as exc:
        raise EbayFailure(f"Choose a valid JPEG or PNG image of at most {maximum // 1000} KB.", 422) from exc


MODELS = {"message": Message, "mark_read": MarkRead, "feedback": Feedback, "shipment": Shipment, "refund": Refund,
          "contest": Contest, "evidence": Evidence, "markdown": Promotion, "quantity_discount": Promotion,
          "campaign": Campaign, "ad": Ad, "campaign_control": Control, "promotion_control": Control,
          "email": Email, "draft": Draft, "revise": Revision, "publish_offer": Offer,
          "upload_image": ImageUpload, "upload_evidence": ImageUpload,
          "inventory_revision": InventoryRevision, "ad_report": Report}


def utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise EbayFailure("Choose a date and time with a time zone.", 422)
    return value.astimezone(UTC)


def iso(value: datetime) -> str:
    return utc(value).isoformat().replace("+00:00", "Z")


async def audience_codes(client: EbayClient) -> set[str]:
    codes = set()
    for offset in range(0, 10000, 200):
        body = await client.request("GET", "/sell/marketing/v1/email_campaign/audience",
                                    params={"emailCampaignType": "ITEM_SHOWCASE", "limit": 200, "offset": offset})
        items = body.get("audiences", [])
        codes.update(item["code"] for item in items)
        if not body.get("next") and offset + len(items) >= body.get("total", 0):
            return codes
    raise EbayFailure("The store has too many audiences to validate safely.", 422)


def ad_rate(value: str) -> Decimal:
    rate = Decimal(value)
    if not rate.is_finite() or not 2 <= rate <= 100 or rate * 10 != (rate * 10).to_integral_value():
        raise EbayFailure("Choose an ad rate between 2% and 100%, with at most one decimal place.", 422)
    return rate


async def campaign_ads(client: EbayClient, target: str) -> list[dict]:
    ads = []
    for offset in range(0, 10000, 200):
        body = await client.request("GET", f"/sell/marketing/v1/ad_campaign/{target}/ad", params={"limit": 200, "offset": offset})
        items = body.get("ads", [])
        ads.extend(items)
        if not body.get("next") and offset + len(items) >= body.get("total", 0):
            return sorted(ads, key=lambda ad: str(ad.get("adId", "")))
    raise EbayFailure("This campaign is too large to review here. Manage it in Seller Hub.", 422)


def refunded_cents(order: dict) -> int:
    # A line-level refund may share its refundId across multiple purchased
    # items. Sum those allocations, but count an order-level representation of
    # the same refund only once. Missing IDs cannot safely be treated as equal.
    orders, lines = {}, {}
    for index, refund in enumerate(order.get("paymentSummary", {}).get("refunds", [])):
        if refund.get("refundStatus") == "FAILED":
            continue
        if refund.get("amount", {}).get("currency") != "USD":
            raise EbayFailure("The refundable balance could not be verified in USD.", 409)
        key = refund.get("refundId") or f"order-missing-{index}"
        orders[key] = max(orders.get(key, 0), money_cents(refund["amount"]["value"]))
    for item_index, item in enumerate(order.get("lineItems", [])):
        allocations = {}
        for index, refund in enumerate(item.get("refunds", [])):
            if refund.get("refundStatus") == "FAILED":
                continue
            if refund.get("amount", {}).get("currency") != "USD":
                raise EbayFailure("The refundable balance could not be verified in USD.", 409)
            key = refund.get("refundId") or f"line-missing-{item_index}-{index}"
            allocations[key] = max(allocations.get(key, 0), money_cents(refund["amount"]["value"]))
        for key, cents in allocations.items():
            lines[key] = lines.get(key, 0) + cents
    return sum(max(orders.get(key, 0), lines.get(key, 0)) for key in orders.keys() | lines.keys())


async def owned_listing(client: EbayClient, listing_id: str) -> dict:
    info = await listing(client, listing_id)
    from vendoo_studio.services.keychain import get_marketplace_accounts
    account = get_marketplace_accounts().get("ebay") or {}
    if not account.get("label") or info["seller"].casefold() != str(account["label"]).casefold():
        raise EbayFailure("This listing does not belong to the connected seller.", 409)
    if info["status"] != "Active" or info["site"] != "US" or not info["price"] or info["variations"]:
        raise EbayFailure("Choose an active US listing in USD without variations.", 409)
    return info


async def prepare(client: EbayClient, kind: str, payload: dict) -> dict:
    if kind not in MODELS:
        raise EbayFailure("Unknown eBay action.", 422)
    data = MODELS[kind].model_validate(payload).model_dump(mode="json")
    plan = {"kind": kind, "target": "", "summary": [], "operations": [], "context": {}, "grant": client.seller}

    def operation(method, path, body=None, **extra):
        plan["operations"].append({"method": method, "path": path, "body": body, **extra})

    if kind == "ad_report":
        start, end = date.fromisoformat(data["start"]), date.fromisoformat(data["end"])
        if start > end or (end - start).days > 90 or end >= datetime.now(UTC).date():
            raise EbayFailure("Choose up to 90 completed days, ending yesterday or earlier.", 422)
        campaign = await client.request("GET", f"/sell/marketing/v1/ad_campaign/{segment(data['campaign_id'])}")
        metadata = await client.request("GET", "/sell/marketing/v1/ad_report_metadata/CAMPAIGN_PERFORMANCE_SUMMARY_REPORT", app=True)
        dimensions = metadata.get("dimensionMetadata", [])
        metrics = [m["metricKey"] for m in metadata.get("metricMetadata", [])
                   if any(key in m["metricKey"] for key in ("impression", "click", "sale", "fee", "roas"))]
        if not dimensions or not metrics:
            raise EbayFailure("eBay did not return the metadata needed to generate this report.")
        maximum = metadata.get("maxNumberOfMetricsToRequest") or len(metrics)
        operation("POST", "/sell/marketing/v1/ad_report_task", {"marketplaceId": "EBAY_US", "campaignIds": [data["campaign_id"]],
                  "reportType": "CAMPAIGN_PERFORMANCE_SUMMARY_REPORT", "reportFormat": "TSV_GZIP",
                  "dateFrom": f"{start}T00:00:00.000Z", "dateTo": f"{end}T23:59:59.999Z",
                  "dimensions": [{"dimensionKey": dimensions[0]["dimensionKey"]}], "metricKeys": metrics[:maximum]})
        plan["target"] = data["campaign_id"]
        plan["summary"] = [f"Generate an ad report for {campaign.get('campaignName', data['campaign_id'])}", f"{start} to {end}"]
    elif kind == "inventory_revision":
        offer = await client.request("GET", f"/sell/inventory/v1/offer/{segment(data['offer_id'])}")
        if offer.get("marketplaceId") != "EBAY_US":
            raise EbayFailure("Choose an eBay US offer.", 422)
        cents = money_cents(data["price"])
        operation("POST", "/sell/inventory/v1/bulk_update_price_quantity", {"requests": [{"sku": offer["sku"],
                  "shipToLocationAvailability": {"quantity": data["quantity"]},
                  "offers": [{"offerId": data["offer_id"], "availableQuantity": data["quantity"],
                              "price": {"value": dollars(cents), "currency": "USD"}}]}]})
        plan["context"] = offer
        plan["target"] = data["offer_id"]
        plan["summary"] = [f"Update inventory SKU {offer['sku']}", f"Price ${dollars(cents)} · available quantity {data['quantity']}"]
    elif kind in ("upload_image", "upload_evidence"):
        raw = image_bytes(data, maximum=1500000 if kind == "upload_evidence" else 8000000)
        if kind == "upload_evidence":
            target = segment(data["dispute_id"])
            await client.request("GET", f"/sell/fulfillment/v1/payment_dispute/{target}")
            path = f"/sell/fulfillment/v1/payment_dispute/{target}/upload_evidence_file"
            field = "file"
        else:
            path, field = "/commerce/media/v1_beta/image/create_image_from_file", "image"
        operation("POST", path, upload={"field": field, "data": data["data"], "mime": data["mime"]})
        plan["target"] = data["dispute_id"] or "image"
        plan["summary"] = [f"Upload this {len(raw) // 1000} KB image to eBay.",
                           "The image will be available for your listing." if kind == "upload_image" else "Attach the returned file ID to the dispute's evidence separately."]
    elif kind in ("message", "mark_read"):
        target = segment(data["conversation_id"])
        conversation = await client.request("GET", f"/commerce/message/v1/conversation/{target}",
                                            params={"conversation_type": "FROM_MEMBERS", "limit": 1})
        plan["context"] = conversation
        plan["target"] = data["conversation_id"]
        plan["summary"] = [f"Conversation: {conversation.get('conversationTitle', target)}"]
        if kind == "message":
            operation("POST", "/commerce/message/v1/send_message", {"conversationId": data["conversation_id"], "messageText": data["text"]})
            plan["summary"].append(data["text"])
        else:
            operation("POST", "/commerce/message/v1/update_conversation", {"conversationId": data["conversation_id"],
                      "conversationType": "FROM_MEMBERS", "read": True})
            plan["summary"].append("Mark this conversation as read.")
    elif kind == "feedback":
        body = await client.request("GET", "/commerce/feedback/v1/awaiting_feedback", params={"filter": f"listingId:{data['listing_id']},userRole:SELLER", "limit": 100})
        match = next((i for i in body.get("lineItems", []) if i["orderLineItemId"] == data["line_item_id"]
                      and i["transactionId"] == data["transaction_id"]), None)
        if not match:
            raise EbayFailure("This sale is no longer awaiting your feedback.", 409)
        plan["target"] = data["line_item_id"]
        plan["summary"] = [f"Positive feedback for {match.get('listingTitle', data['listing_id'])}", data["text"]]
        operation("POST", "/commerce/feedback/v1/feedback", {"commentText": data["text"], "commentType": "POSITIVE",
                  "listingId": data["listing_id"], "transactionId": data["transaction_id"],
                  "orderLineItemId": data["line_item_id"], "sellerRatings": []})
    elif kind in ("shipment", "refund"):
        target = segment(data["order_id"])
        order = await client.request("GET", f"/sell/fulfillment/v1/order/{target}")
        if order.get("orderPaymentStatus") not in ("PAID", "PARTIALLY_REFUNDED"):
            raise EbayFailure("Only paid orders can be shipped or refunded here.", 409)
        plan["target"] = data["order_id"]
        plan["context"] = {"payment": order.get("paymentSummary"), "status": order.get("orderFulfillmentStatus")}
        if kind == "shipment":
            fulfilled = await client.request("GET", f"/sell/fulfillment/v1/order/{target}/shipping_fulfillment")
            quantities = {}
            for shipment in fulfilled.get("fulfillments", []):
                for item in shipment.get("lineItems", []):
                    quantities[item["lineItemId"]] = quantities.get(item["lineItemId"], 0) + int(item.get("quantity", 0))
            items = [{"lineItemId": i["lineItemId"], "quantity": int(i["quantity"]) - quantities.get(i["lineItemId"], 0)}
                     for i in order.get("lineItems", []) if int(i["quantity"]) > quantities.get(i["lineItemId"], 0)]
            if not items:
                raise EbayFailure("This order has no unshipped items.", 409)
            operation("POST", f"/sell/fulfillment/v1/order/{target}/shipping_fulfillment", {
                "lineItems": items, "shippingCarrierCode": data["carrier"], "trackingNumber": data["tracking"],
            })
            plan["summary"] = [f"Ship all remaining items in order {target}", f"{data['carrier']} · {data['tracking']}"]
        else:
            total = order.get("pricingSummary", {}).get("total", {})
            if total.get("currency") != "USD":
                raise EbayFailure("Refunds here support USD orders only.", 422)
            refunded = refunded_cents(order)
            plan["context"]["refundedCents"] = refunded
            cents = money_cents(data["amount"])
            if cents > money_cents(total["value"]) - refunded:
                raise EbayFailure("The refund exceeds the order's remaining total.", 409)
            operation("POST", f"/sell/fulfillment/v1/order/{target}/issue_refund", {"reasonForRefund": data["reason"],
                      "orderLevelRefundAmount": {"value": dollars(cents), "currency": "USD"}})
            plan["summary"] = [f"Refund ${dollars(cents)} to the buyer for order {target}", data["reason"]]
    elif kind in ("contest", "evidence"):
        target = segment(data["dispute_id"])
        dispute = await client.request("GET", f"/sell/fulfillment/v1/payment_dispute/{target}")
        plan["target"] = data["dispute_id"]
        plan["context"] = {"revision": dispute.get("revision"), "status": dispute.get("paymentDisputeStatus"), "reason": dispute.get("reason")}
        if kind == "contest":
            address = data["return_address"]
            if dispute.get("reason") == "SIGNIFICANTLY_NOT_AS_DESCRIBED" and not address:
                raise EbayFailure("This dispute requires a return address. Enter your US return address and phone before reviewing.", 422)
            if address:
                phone = re.sub(r"\D", "", address["phone"])
                if len(phone) not in (10, 11) or (len(phone) == 11 and not phone.startswith("1")):
                    raise EbayFailure("Enter a US return phone number with area code.", 422)
                address = {**address, "primaryPhone": {"countryCode": "US", "number": phone}}
                del address["phone"]
            operation("POST", f"/sell/fulfillment/v1/payment_dispute/{target}/contest", {"revision": dispute["revision"], "note": data["note"],
                      **({"returnAddress": address} if address else {})})
            plan["summary"] = [f"Contest payment dispute {target}", data["note"]]
            if address:
                plan["summary"].append("Return address: " + json.dumps(address))
        else:
            operation("POST", f"/sell/fulfillment/v1/payment_dispute/{target}/add_evidence", {
                "evidenceType": data["evidence_type"], "files": [{"fileId": data["file_id"]}],
                "lineItems": [{"itemId": i["itemId"], "lineItemId": i["lineItemId"]} for i in dispute.get("lineItems", [])],
            })
            plan["summary"] = [f"Attach evidence file {data['file_id']} to dispute {target}", data["evidence_type"]]
    elif kind in ("markdown", "quantity_discount", "email", "ad"):
        ids = data.get("listing_ids", [data.get("listing_id")])
        live = [await owned_listing(client, identifier) for identifier in dict.fromkeys(ids)]
        plan["context"] = live
        plan["target"] = ",".join(item["id"] for item in live)
        if kind in ("markdown", "quantity_discount"):
            image = urlparse(data["image_url"])
            if image.scheme != "https" or not image.hostname or image.username:
                raise EbayFailure("Provide an HTTPS promotion image: JPEG or PNG, at least 500×500 pixels, at most 12 MB.", 422)
            plan["images"] = [data["image_url"]]
            start, end = datetime.fromisoformat(data["start"]), datetime.fromisoformat(data["end"])
            if utc(start) <= datetime.now(UTC) or utc(end) <= utc(start) or utc(end) - utc(start) > timedelta(days=90):
                raise EbayFailure("Schedule a future start and an end within 90 days.", 422)
            if kind == "markdown" and not timedelta(days=1) <= utc(end) - utc(start) <= timedelta(days=45):
                raise EbayFailure("US markdown sales must last between 24 hours and 45 days.", 422)
            common = {"name": data["name"], "description": data["name"][:50], "marketplaceId": "EBAY_US", "promotionImageUrl": data["image_url"],
                      "startDate": iso(start), "endDate": iso(end), "promotionStatus": "SCHEDULED"}
            criterion = {"inventoryCriterionType": "INVENTORY_BY_VALUE", "listingIds": ids}
            if kind == "markdown":
                operation("POST", "/sell/marketing/v1/item_price_markdown", {**common, "autoSelectFutureInventory": False,
                          "selectedInventoryDiscounts": [{"discountBenefit": {"percentageOffItem": str(data["percent"])},
                                                         "inventoryCriterion": criterion}]})
            else:
                operation("POST", "/sell/marketing/v1/item_promotion", {**common, "promotionType": "ORDER_DISCOUNT",
                          "inventoryCriterion": criterion, "discountRules": [{"discountBenefit": {"percentageOffOrder": str(data["percent"])},
                          "discountSpecification": {"minQuantity": data["quantity"]}, "ruleOrder": 1}]})
            plan["summary"] = [f"{data['percent']}% off {len(ids)} selected listings", f"{iso(start)} to {iso(end)}", f"Promotion image: {data['image_url']}"]
            if kind == "quantity_discount":
                plan["summary"].append(f"Buyer must purchase at least {data['quantity']} items.")
        elif kind == "ad":
            rate = ad_rate(data["bid_percent"])
            campaign = await client.request("GET", f"/sell/marketing/v1/ad_campaign/{segment(data['campaign_id'])}")
            if campaign.get("fundingStrategy", {}).get("fundingModel") != "COST_PER_SALE":
                raise EbayFailure("Choose a general campaign charged per sale.", 409)
            plan["context"] = {"listings": live, "campaign": campaign}
            operation("POST", f"/sell/marketing/v1/ad_campaign/{segment(data['campaign_id'])}/ad", {"listingId": data["listing_id"], "bidPercentage": str(rate)})
            plan["summary"] = [f"Advertise {live[0]['title']} in {campaign.get('campaignName', data['campaign_id'])}", f"Ad rate: {rate}% of attributed sales"]
        else:
            schedule = datetime.fromisoformat(data["schedule"])
            if utc(schedule) <= datetime.now(UTC) + timedelta(minutes=5):
                raise EbayFailure("Schedule the email at least five minutes in the future.", 422)
            codes = await audience_codes(client)
            if not set(data["audience_codes"]).issubset(codes):
                raise EbayFailure("Choose an audience available to your store.", 422)
            operation("POST", "/sell/marketing/v1/email_campaign", {"audienceCodes": data["audience_codes"],
                      "emailCampaignType": "ITEM_SHOWCASE", "itemSelectMode": "MANUAL", "itemIds": ids,
                      "personalizedMessage": data["message"], "subject": data["subject"], "scheduleDate": iso(schedule)})
            plan["summary"] = [f"Email your selected store audiences at {iso(schedule)}", data["subject"], data["message"]]
    elif kind == "campaign":
        rate = ad_rate(data["bid_percent"])
        start = datetime.fromisoformat(data["start"])
        if utc(start) <= datetime.now(UTC):
            raise EbayFailure("Choose a future campaign start time.", 422)
        plan["target"] = "new-campaign"
        operation("POST", "/sell/marketing/v1/ad_campaign", {"campaignName": data["name"], "marketplaceId": "EBAY_US",
                  "startDate": iso(start),
                  "fundingStrategy": {"fundingModel": "COST_PER_SALE", "bidPercentage": str(rate)}, "campaignTargetingType": "MANUAL"})
        plan["summary"] = [f"Create general ad campaign {data['name']}", f"Default ad rate: {rate}%. Add listings separately."]
    elif kind in ("campaign_control", "promotion_control"):
        target = segment(data["resource_id"])
        resource = "ad_campaign" if kind == "campaign_control" else "promotion"
        if resource == "promotion" and data["action"] == "end":
            raise EbayFailure("Use pause to stop a discount.", 422)
        if resource == "ad_campaign":
            current = await client.request("GET", f"/sell/marketing/v1/{resource}/{target}")
            if current.get("fundingStrategy", {}).get("fundingModel") != "COST_PER_SALE":
                raise EbayFailure("Only general campaigns charged per sale can be controlled here.", 422)
            details = {"Funding": current.get("fundingStrategy"), "Status": current.get("campaignStatus"),
                       "Starts": current.get("startDate"), "Ends": current.get("endDate"),
                       "Inventory selection rules": current.get("campaignCriterion")}
            if (current.get("campaignCriterion") or {}).get("autoSelectFutureInventory"):
                details["Ongoing scope"] = "Future matching listings will automatically be advertised and incur fees at the campaign's rates. Resuming also re-enables this continuing selection."
            ads = await campaign_ads(client, target)
            current = {**current, "ads": ads}
            details["Advertised listings and effective rates"] = [{
                "listing": ad.get("listingId") or ad.get("inventoryReferenceId") or ad.get("adId"),
                "rate (%)": ad.get("bidPercentage") or current["fundingStrategy"].get("bidPercentage"),
                "status": ad.get("adStatus"),
            } for ad in ads]
        else:
            if "@" in data["resource_id"] and not data["resource_id"].endswith("@EBAY_US"):
                raise EbayFailure("Choose an eBay US promotion.", 422)
            target = segment(data["resource_id"] if "@" in data["resource_id"] else data["resource_id"] + "@EBAY_US")
            try:
                current = await client.request("GET", f"/sell/marketing/v1/item_price_markdown/{target}")
            except EbayFailure as exc:
                if exc.status != 404:
                    raise
                current = await client.request("GET", f"/sell/marketing/v1/item_promotion/{target}")
            details = {key: current.get(key) for key in ("promotionStatus", "promotionType", "startDate", "endDate",
                       "budget", "discountRules", "inventoryCriterion", "selectedInventoryDiscounts", "autoSelectFutureInventory")}
        plan["context"] = current
        operation("POST", f"/sell/marketing/v1/{resource}/{target}/{data['action']}", {})
        plan["target"] = data["resource_id"]
        plan["summary"] = [f"{data['action'].capitalize()} {current.get('campaignName') or current.get('name') or target}",
                           *[f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in details.items() if value is not None]]
    elif kind == "draft":
        for url in data["image_urls"]:
            parsed = urlparse(url)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or len(url) > 2000:
                raise EbayFailure("Provide HTTPS image URLs, or upload an image first.", 422)
        cents = money_cents(data["price"])
        weight = Decimal(data["weight_oz"])
        if not weight.is_finite() or not 0 < weight <= 2400:
            raise EbayFailure("Enter a packaged weight above zero and no more than 2400 ounces.", 422)
        for policy in ("fulfillment", "payment", "return"):
            selected = await client.request("GET", f"/sell/account/v1/{policy}_policy/{segment(data[f'{policy}_policy_id'])}")
            if selected.get("marketplaceId") != "EBAY_US":
                raise EbayFailure("Choose US eBay business policies.", 422)
        location = await client.request("GET", f"/sell/inventory/v1/location/{segment(data['merchant_location_key'])}")
        if location.get("merchantLocationStatus") != "ENABLED":
            raise EbayFailure("Choose an enabled eBay inventory location.", 422)
        try:
            await client.request("GET", f"/sell/inventory/v1/inventory_item/{segment(data['sku'])}")
        except EbayFailure as exc:
            if exc.status != 404:
                raise
        else:
            raise EbayFailure("This SKU already exists on eBay. Choose a new SKU to avoid overwriting it.", 409)
        aspects = await client.request("GET", "/commerce/taxonomy/v1/category_tree/0/get_item_aspects_for_category",
                                       params={"category_id": data["category_id"]}, app=True)
        for aspect in aspects.get("aspects", []):
            values = data["aspects"].get(aspect["localizedAspectName"], [])
            constraint = aspect.get("aspectConstraint", {})
            if constraint.get("aspectRequired") and not values:
                raise EbayFailure(f"Add required item specific: {aspect['localizedAspectName']}.", 422)
            if constraint.get("aspectMode") == "SELECTION_ONLY" and any(v not in {a["localizedValue"] for a in aspect.get("aspectValues", [])} for v in values):
                raise EbayFailure(f"Choose a valid value for {aspect['localizedAspectName']}.", 422)
        from vendoo_studio.services.user_settings import get_package_dimensions
        package = get_package_dimensions()
        operation("PUT", f"/sell/inventory/v1/inventory_item/{segment(data['sku'])}", {
            "condition": data["condition"], "availability": {"shipToLocationAvailability": {"quantity": data["quantity"]}},
            "product": {"title": data["title"], "description": data["description"], "imageUrls": data["image_urls"], "aspects": data["aspects"]},
            "packageWeightAndSize": {"weight": {"value": str(weight), "unit": "OUNCE"}, "dimensions": {**package, "unit": "INCH"}},
        })
        operation("POST", "/sell/inventory/v1/offer", {"sku": data["sku"], "marketplaceId": "EBAY_US", "format": "FIXED_PRICE",
                  "includeCatalogProductDetails": False, "categoryId": data["category_id"], "availableQuantity": data["quantity"],
                  "pricingSummary": {"price": {"value": dollars(cents), "currency": "USD"}}, "listingDescription": data["description"],
                  "merchantLocationKey": data["merchant_location_key"],
                  "listingPolicies": {"fulfillmentPolicyId": data["fulfillment_policy_id"], "paymentPolicyId": data["payment_policy_id"],
                                      "returnPolicyId": data["return_policy_id"]}})
        plan["target"] = data["sku"]
        plan["summary"] = [f"Save unpublished eBay offer: {data['title']}", f"SKU {data['sku']} · ${dollars(cents)} · quantity {data['quantity']}"]
    elif kind == "revise":
        before = await owned_listing(client, data["listing_id"])
        if before["type"] != "FixedPriceItem":
            raise EbayFailure("Only fixed-price listings can be revised here.", 422)
        if data["quantity"] < int(before["soldQuantity"] or 0):
            raise EbayFailure("Quantity cannot be lower than the quantity already sold.", 422)
        root = ET.Element("ReviseFixedPriceItemRequest", xmlns=NS["e"])
        item = ET.SubElement(root, "Item")
        for name, value in {"ItemID": data["listing_id"], "Title": data["title"], "Description": data["description"],
                            "StartPrice": dollars(money_cents(data["price"])), "Quantity": str(data["quantity"])}.items():
            ET.SubElement(item, name).text = value
        operation("POST", "/ws/api.dll", xml=trading_xml(root), call="ReviseFixedPriceItem")
        plan["context"] = before
        plan["target"] = data["listing_id"]
        plan["summary"] = [f"Update live listing {before['title']}", f"New title: {data['title']}",
                           f"${data['price']} · total quantity {data['quantity']}", data["description"]]
    elif kind == "publish_offer":
        offer = await client.request("GET", f"/sell/inventory/v1/offer/{segment(data['offer_id'])}")
        if offer.get("marketplaceId") != "EBAY_US" or offer.get("status") != "UNPUBLISHED":
            raise EbayFailure("Choose an unpublished US eBay offer.", 409)
        plan["context"] = offer
        inventory = await client.request("GET", f"/sell/inventory/v1/inventory_item/{segment(offer['sku'])}")
        plan["context"] = {"offer": offer, "inventory": inventory}
        plan["target"] = data["offer_id"]
        price = offer.get("pricingSummary", {}).get("price", {})
        plan["summary"] = [f"Publish {inventory.get('product', {}).get('title', offer['sku'])} on eBay US now.",
                           f"Price: {price.get('value')} {price.get('currency')} · quantity {offer.get('availableQuantity')}",
                           f"Category {offer.get('categoryId')} · condition {inventory.get('condition')}",
                           offer.get("listingDescription") or inventory.get("product", {}).get("description", ""),
                           f"Shipping policy: {offer.get('listingPolicies', {}).get('fulfillmentPolicyId', 'missing')}",
                           "This makes the listing public."]
        plan["images"] = inventory.get("product", {}).get("imageUrls", [])
        operation("POST", f"/sell/inventory/v1/offer/{segment(data['offer_id'])}/publish", {})
    return plan


def clock():
    return datetime.now(UTC).replace(tzinfo=None)


async def review(db: Session, kind: str, payload: dict) -> dict:
    client = await EbayClient.connect()
    plan = await prepare(client, kind, payload)
    fingerprint = digest(plan)
    if db.query(EbayAction.id).filter_by(account_id=client.seller[0], kind=kind, resource_id=plan["target"]).filter(
        EbayAction.state.in_(("sending", "unknown")),
    ).first():
        raise EbayFailure("An earlier action for this item has an uncertain result. Check eBay and mark it checked in Activity first.", 409)
    if db.query(EbayAction.id).filter_by(account_id=client.seller[0], fingerprint=fingerprint).filter(
        EbayAction.state.in_(("sending", "unknown") if kind in ("campaign_control", "promotion_control", "mark_read") else ("sending", "unknown", "sent")),
    ).first():
        raise EbayFailure("This action was already sent or has an uncertain result. Check eBay before repeating it.", 409)
    action = EbayAction(account_id=client.seller[0], kind=kind, resource_id=plan["target"], fingerprint=fingerprint,
                        expires_at=clock() + timedelta(minutes=10), state="preview")
    db.add(action)
    db.commit()
    return {"id": action.id, "summary": plan["summary"], "images": plan.get("images", []), "expiresAt": action.expires_at.isoformat() + "Z"}


async def execute(db: Session, action_id: str, kind: str, payload: dict) -> dict:
    client = await EbayClient.connect()
    action = db.get(EbayAction, action_id)
    if not action or action.account_id != client.seller[0] or action.kind != kind or action.state != "preview" or action.expires_at < clock():
        raise EbayFailure("This review expired or was already used. Review the action again.", 409)
    plan = await prepare(client, kind, payload)
    if digest(plan) != action.fingerprint:
        raise EbayFailure("The input, listing, order, or account changed. Review the action again.", 409)
    assert_identity(client.seller)
    if db.query(EbayAction.id).filter(EbayAction.account_id == action.account_id, EbayAction.kind == kind,
                                     EbayAction.resource_id == plan["target"], EbayAction.state.in_(("sending", "unknown")),
                                     EbayAction.id != action.id).first():
        raise EbayFailure("An earlier action for this item needs a result check in Activity.", 409)
    if db.query(EbayAction.id).filter(EbayAction.account_id == action.account_id, EbayAction.fingerprint == action.fingerprint,
                                     EbayAction.state.in_(("sending", "unknown") if kind in ("campaign_control", "promotion_control", "mark_read") else ("sending", "unknown", "sent")), EbayAction.id != action.id).first():
        raise EbayFailure("This action was already sent or has an uncertain result.", 409)
    try:
        claimed = db.execute(update(EbayAction).where(EbayAction.id == action_id, EbayAction.state == "preview").values(state="sending"))
        if claimed.rowcount != 1:
            raise EbayFailure("This action already started.", 409)
        db.execute(update(EbayAction).where(EbayAction.account_id == action.account_id, EbayAction.fingerprint == action.fingerprint,
                                           EbayAction.state == "preview", EbayAction.id != action.id).values(state="superseded"))
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise EbayFailure("Another eBay action is running. Wait for its result.", 409) from exc
    completed = 0
    result = {}
    try:
        for operation in plan["operations"]:
            result = await client.request(**operation)
            if kind == "refund" and result.get("refundStatus") == "FAILED":
                raise EbayFailure("eBay rejected this refund. No refund was issued.", 422)
            # Bulk Inventory calls can return HTTP 200 with individual failures.
            if any(r.get("statusCode", 200) >= 400 or r.get("errors") for r in result.get("responses", [])):
                raise EbayFailure("eBay rejected one or more inventory updates. Check the live listing before trying again.")
            completed += 1
    except BaseException as exc:
        definite = isinstance(exc, EbayFailure) and exc.status in (400, 401, 403, 404, 409, 422, 429) and completed == 0
        action.state = "failed" if definite else "unknown"
        db.commit()
        if isinstance(exc, asyncio.CancelledError):
            raise
        if definite:
            raise
        return {"state": "unknown", "id": action.id, "message": "The result is uncertain or only part finished. Check eBay before repeating. Nothing will retry automatically."}
    action.state = "sent"
    action.receipt_id = str(result.get("receiptId") or result.get("offerId") or result.get("listingId") or result.get("emailCampaignId") or result.get("refundId") or result.get("fileId") or "")[:200]
    db.commit()
    image_url = result.get("imageUrl")
    if kind == "upload_image" and action.receipt_id and not image_url:
        image_url = (await client.request("GET", f"/commerce/media/v1_beta/image/{segment(action.receipt_id)}")).get("imageUrl")
    return {"state": "sent", "id": action.id, "receiptId": action.receipt_id, "imageUrl": image_url,
            "message": "eBay accepted the refund; payment is pending. Check the order for its final status." if kind == "refund" and result.get("refundStatus") == "PENDING" else "eBay confirmed the action."}


def activity(db: Session) -> dict:
    seller = identity()[0]
    rows = db.query(EbayAction).filter_by(account_id=seller).filter(EbayAction.state != "preview").order_by(EbayAction.created_at.desc()).limit(100).all()
    return {"rows": [{"id": a.id, "title": a.kind.replace("_", " "), "subtitle": a.resource_id,
                      "status": a.state, "amount": None, "fields": {"Receipt": a.receipt_id, "Date": a.created_at.isoformat() + "Z"}, "children": []} for a in rows]}


def recover(db: Session) -> None:
    db.query(EbayAction).filter_by(state="sending").update({"state": "unknown"})
    db.commit()
