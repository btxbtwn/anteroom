"""Read models for eBay's workspace. Private responses are never cached on disk."""
from __future__ import annotations

from datetime import date, datetime, UTC
from decimal import Decimal, InvalidOperation
from html import unescape
from html.parser import HTMLParser
import csv
import gzip
import io

from sqlalchemy.orm import Session

from vendoo_studio.models.conversation import Conversation
from vendoo_studio.services.ebay_client import EbayClient, EbayFailure, NS, segment
from vendoo_studio.services.ebay_finances import order_proceeds
from vendoo_studio.services.inventory_analytics import load_rows
from vendoo_studio.services.relay_sales import listing_id_from_url
from vendoo_studio.services.vendoo_import import parse_notes


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, _attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        if tag in ("br", "p", "div", "li"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, value):
        if not self.hidden:
            self.parts.append(value)


def plain(value: object) -> str:
    parser = _Text()
    # Some live eBay descriptions contain literal CDATA markers, including
    # an unmatched opening marker. HTMLParser otherwise hides their contents.
    source = str(value or "").replace("<![CDATA[", "").replace("]]>", "")
    parser.feed(source)
    return unescape("".join(parser.parts)).strip()


def amount(value: dict | None) -> str | None:
    if not isinstance(value, dict) or value.get("currency") != "USD":
        return None
    try:
        result = Decimal(str(value["value"]))
        return f"{result:.2f}" if result.is_finite() else None
    except (KeyError, InvalidOperation):
        return None


def row(identifier, title, *, subtitle="", status="", money=None, fields=None, children=None):
    return {"id": str(identifier), "title": plain(title), "subtitle": plain(subtitle), "status": str(status),
            "amount": amount(money), "fields": fields or {}, "children": children or []}


def page(body: dict, rows: list, offset: int, limit: int = 25) -> dict:
    total = body.get("total")
    more = bool(body.get("next")) or (isinstance(total, int) and offset + limit < total)
    return {"rows": rows, "total": total, "nextOffset": offset + limit if more else None}


def dates(start: date, end: date) -> tuple[str, str]:
    now = datetime.now(UTC)
    if end < start or (end - start).days > 90 or end > now.date():
        raise EbayFailure("Choose a date range of up to 90 days ending today or earlier.", 422)
    last = now.isoformat(timespec="milliseconds").replace("+00:00", "Z") if end == now.date() else f"{end.isoformat()}T23:59:59.999Z"
    return f"{start.isoformat()}T00:00:00.000Z", last


async def traffic(client: EbayClient, db: Session, start: date, end: date, offset: int) -> dict:
    dates(start, end)
    ids = []
    titles = {}
    for conv in db.query(Conversation).order_by(Conversation.id).all():
        urls = parse_notes(conv.notes).get("vendooListingUrls") or {}
        listing_id = listing_id_from_url("ebay", urls.get("ebay")) if isinstance(urls, dict) else None
        if listing_id and listing_id not in ids:
            ids.append(listing_id)
            titles[listing_id] = conv.title
    selected = ids[offset:offset + 25]
    if not selected:
        return page({"total": len(ids)}, [], offset)
    metrics = ["TOTAL_IMPRESSION_TOTAL", "LISTING_VIEWS_TOTAL", "CLICK_THROUGH_RATE", "SALES_CONVERSION_RATE", "TRANSACTION"]
    body = await client.request("GET", "/sell/analytics/v1/traffic_report", params={
        "dimension": "LISTING", "filter": f"listing_ids:{{{'|'.join(selected)}}},date_range:[{start:%Y%m%d}..{end:%Y%m%d}]",
        "metric": ",".join(metrics),
    })
    keys = [entry["key"] for entry in body.get("header", {}).get("metrics", [])]
    records = body.get("records", [])
    rows = []
    labels = {"TOTAL_IMPRESSION_TOTAL": "Impressions", "LISTING_VIEWS_TOTAL": "Listing views",
              "CLICK_THROUGH_RATE": "Click-through rate", "SALES_CONVERSION_RATE": "Sales conversion rate", "TRANSACTION": "Transactions"}
    for record in records:
        listing_id = str(record["dimensionValues"][0]["value"])
        values = {labels.get(key, key): (metric.get("value") if metric.get("applicable", True) else None)
                  for key, metric in zip(keys, record.get("metricValues", []), strict=False)}
        rows.append(row(listing_id, titles.get(listing_id) or f"Listing {listing_id}", fields=values))
    return page({"total": len(ids)}, rows, offset)


async def finances(client: EbayClient, db: Session, start: date, end: date, offset: int) -> dict:
    first, last = dates(start, end)
    body = await client.request("GET", "/sell/fulfillment/v1/order", params={
        "limit": 10, "offset": offset, "filter": f"creationdate:[{first}..{last}]",
    })
    local = {item.conversation_id: item for item in load_rows(db)}
    bindings = {}
    for conv in db.query(Conversation).all():
        urls = parse_notes(conv.notes).get("vendooListingUrls") or {}
        identifier = listing_id_from_url("ebay", urls.get("ebay")) if isinstance(urls, dict) else None
        if identifier and identifier not in bindings:
            bindings[identifier] = local.get(conv.id)
        elif identifier:
            bindings[identifier] = None  # Ambiguous local matches must not invent profit.
    rows = []
    for order in body.get("orders", []):
        net = await order_proceeds(client, order["orderId"])
        costs = []
        for item in order.get("lineItems", []):
            match = bindings.get(str(item.get("legacyItemId", "")))
            value = Decimal(str(match.cost)) if match and match.cost is not None else None
            quantity = item.get("quantity")
            costs.append(value * quantity if value is not None and value.is_finite() and value >= 0
                         and isinstance(quantity, int) and not isinstance(quantity, bool) and quantity > 0 else None)
        cost = sum(costs, Decimal(0)) if costs and all(value is not None for value in costs) else None
        rows.append(row(order["orderId"], f"Order {order['orderId']}", subtitle=order.get("creationDate"),
                        money={"currency": "USD", "value": str(net)} if net is not None else None, fields={
                            "Payment": order.get("orderPaymentStatus"),
                            "Proceeds": "Recorded order credits minus debits" if net is not None else "Transaction data is incomplete",
                            "Item cost": f"{cost:.2f}" if cost is not None else None,
                            "Profit": f"{net - cost:.2f}" if net is not None and cost is not None else None,
                        }))
    return page(body, rows, offset, 10)


async def read(client: EbayClient, section: str, offset: int = 0, target: str = "", query: str = "") -> dict:
    params = {"limit": 25, "offset": offset}
    if section == "inbox":
        body = await client.request("GET", "/commerce/message/v1/conversation", params={**params, "conversation_type": "FROM_MEMBERS"})
        return page(body, [row(c["conversationId"], c.get("conversationTitle"), status=c.get("conversationStatus"),
                               subtitle=(c.get("latestMessage") or {}).get("messageBody"),
                               fields={"Unread": c.get("unreadCount", 0)}) for c in body.get("conversations", [])], offset)
    if section == "messages":
        body = await client.request("GET", f"/commerce/message/v1/conversation/{segment(target)}",
                                    params={**params, "conversation_type": "FROM_MEMBERS"})
        return page(body, [row(m["messageId"], m.get("senderUsername"), subtitle=m.get("messageBody"),
                               fields={"Date": m.get("createdDate")}) for m in body.get("messages", [])], offset)
    if section == "feedback":
        body = await client.request("GET", "/commerce/feedback/v1/awaiting_feedback", params={**params, "filter": "userRole:SELLER"})
        pagination = body.get("pagination") or {}
        return page(pagination, [row(i["orderLineItemId"], i.get("listingTitle"), fields={"listingId": i["listingId"],
                          "transactionId": i["transactionId"]}) for i in body.get("lineItems", [])], offset)
    if section == "orders":
        body = await client.request("GET", "/sell/fulfillment/v1/order", params=params)
        return page(body, [order_row(o) for o in body.get("orders", [])], offset)
    if section == "order":
        return {"rows": [order_row(await client.request("GET", f"/sell/fulfillment/v1/order/{segment(target)}"))]}
    if section == "payouts":
        body = await client.request("GET", "/sell/finances/v1/payout", params=params)
        return page(body, [row(p["payoutId"], "Payout", subtitle=p.get("payoutDate"), status=p.get("payoutStatus"),
                               money=p.get("amount")) for p in body.get("payouts", [])], offset)
    if section == "disputes":
        body = await client.request("GET", "/sell/fulfillment/v1/payment_dispute_summary", params=params)
        return page(body, [row(p["paymentDisputeId"], f"Order {p.get('orderId', '')}", status=p.get("paymentDisputeStatus"),
                               subtitle=p.get("reason"), money=p.get("amount"), fields={"Respond by": p.get("respondByDate")})
                          for p in body.get("paymentDisputeSummaries", [])], offset)
    if section == "dispute":
        p = await client.request("GET", f"/sell/fulfillment/v1/payment_dispute/{segment(target)}")
        return {"rows": [row(target, f"Order {p.get('orderId', '')}", subtitle=p.get("reason"),
                             status=p.get("paymentDisputeStatus"), fields={"revision": p.get("revision"),
                             "Respond by": p.get("respondByDate")}, children=[row(i["lineItemId"], i.get("itemId"))
                             for i in p.get("lineItems", [])])]}
    if section in ("campaigns", "promotions", "promotion-reports", "emails", "audiences"):
        mapping = {"campaigns": ("ad_campaign", "campaigns"), "promotions": ("promotion", "promotions"),
                   "promotion-reports": ("promotion_report", "promotionReports"), "emails": ("email_campaign", "campaigns"),
                   "audiences": ("email_campaign/audience", "audiences")}
        path, key = mapping[section]
        filters = {"emailCampaignType": "ITEM_SHOWCASE"} if section == "audiences" else {"q": "campaignType:ITEM_SHOWCASE"} if section == "emails" else {"marketplace_id": "EBAY_US"}
        body = await client.request("GET", f"/sell/marketing/v1/{path}", params={**params, **filters})
        items = body.get(key, [])
        return page(body, [row(i.get("campaignId") or i.get("promotionId") or i.get("emailCampaignId") or i.get("code"),
                               i.get("campaignName") or i.get("name") or i.get("subject") or i.get("description") or "Promotion report",
                               status=i.get("campaignStatus") or i.get("promotionStatus") or i.get("emailCampaignStatus") or "",
                               fields={k: amount(v) if isinstance(v, dict) else v for k, v in i.items()
                                       if k in ("startDate", "endDate", "scheduleDate", "baseSale", "promotionSale", "salesLift")})
                          for i in items], offset)
    if section == "inventory":
        body = await client.request("GET", "/sell/inventory/v1/inventory_item", params=params)
        return page(body, [row(i["sku"], i.get("product", {}).get("title"), status=i.get("condition"),
                               fields={"Quantity": (i.get("availability", {}).get("shipToLocationAvailability") or {}).get("quantity")})
                          for i in body.get("inventoryItems", [])], offset)
    if section == "policies":
        rows = []
        for kind in ("fulfillment", "payment", "return"):
            body = await client.request("GET", f"/sell/account/v1/{kind}_policy", params={"marketplace_id": "EBAY_US"})
            for policy in body.get(f"{kind}Policies", []):
                rows.append(row(policy[f"{kind}PolicyId"], policy.get("name"), status=kind))
        return {"rows": rows}
    if section == "locations":
        body = await client.request("GET", "/sell/inventory/v1/location", params=params)
        return page(body, [row(i["merchantLocationKey"], i.get("name"), status=i.get("merchantLocationStatus")) for i in body.get("locations", [])], offset)
    if section == "offers":
        body = await client.request("GET", "/sell/inventory/v1/offer", params={**params, "sku": target, "marketplace_id": "EBAY_US"})
        return page(body, [row(i["offerId"], i.get("sku"), status=i.get("status"),
                               money=i.get("pricingSummary", {}).get("price"), fields={"Category": i.get("categoryId")})
                          for i in body.get("offers", [])], offset)
    if section == "ad-reports":
        body = await client.request("GET", "/sell/marketing/v1/ad_report_task", params=params)
        return page(body, [row(i["reportTaskId"], i.get("reportName") or i.get("reportType"), status=i.get("reportTaskStatus"),
                               fields={"Report ID": i.get("reportId"), "From": i.get("dateFrom"), "To": i.get("dateTo")})
                          for i in body.get("reportTasks", [])], offset)
    if section == "ad-report":
        task = await client.request("GET", f"/sell/marketing/v1/ad_report_task/{segment(target)}")
        if not task.get("reportId"):
            return {"rows": [row(target, "Ad report", status=task.get("reportTaskStatus"))]}
        report = await client.request("GET", f"/sell/marketing/v1/ad_report/{segment(task['reportId'])}", raw=True)
        content = report["content"]
        if content.startswith(b"\x1f\x8b"):
            with gzip.GzipFile(fileobj=io.BytesIO(content)) as zipped:
                content = zipped.read(5000001)
        if len(content) > 5000000:
            raise EbayFailure("This report is too large to display. Request a smaller date range.")
        records = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig")), delimiter="\t"))
        return page({"total": len(records)}, [row(str(offset + index), f"Report row {offset + index + 1}", fields=record)
                                              for index, record in enumerate(records[offset:offset + 25])], offset)
    if section == "categories":
        body = await client.request("GET", "/commerce/taxonomy/v1/category_tree/0/get_category_suggestions", params={"q": query}, app=True)
        return {"rows": [row(c["category"]["categoryId"], c["category"]["categoryName"],
                              subtitle=" › ".join(a["categoryName"] for a in c.get("categoryTreeNodeAncestors", [])))
                         for c in body.get("categorySuggestions", [])]}
    if section == "aspects":
        from vendoo_studio.services.marketplace_schemas import ebay_aspects

        aspects = await ebay_aspects(client, segment(target))
        return {"rows": [row(a["name"], a["name"], status="Required" if a["required"] else "Optional",
                              subtitle=", ".join(v["label"] for v in a["values"][:20]),
                              fields={"Mode": "SELECTION_ONLY" if a["mode"] == "selection" else "FREE_TEXT"}) for a in aspects]}
    if section == "research":
        body = await client.request("GET", "/buy/browse/v1/item_summary/search", params={**params, "q": query,
                                    "filter": "buyingOptions:{FIXED_PRICE},deliveryCountry:US"}, app=True)
        return research_page(body, offset)
    raise EbayFailure("Unknown eBay workspace section.", 404)


def order_row(order: dict) -> dict:
    return row(order["orderId"], f"Order {order['orderId']}", subtitle=order.get("creationDate"),
               status=order.get("orderFulfillmentStatus"), money=order.get("pricingSummary", {}).get("total"),
               fields={"Payment": order.get("orderPaymentStatus"), "Buyer": order.get("buyer", {}).get("username")},
               children=[row(i["lineItemId"], i.get("title"), fields={"Quantity": i.get("quantity"),
                       "listingId": i.get("legacyItemId"), "Shipped": i.get("lineItemFulfillmentStatus")})
                         for i in order.get("lineItems", [])])


def research_page(body: dict, offset: int) -> dict:
    return page(body, [row(i["itemId"], i.get("title"), subtitle=i.get("condition"), money=i.get("price"),
                           fields={"url": i.get("itemWebUrl"), "Shipping": amount((i.get("shippingOptions") or [{}])[0].get("shippingCost"))})
                      for i in body.get("itemSummaries", [])], offset)


async def listing(client: EbayClient, listing_id: str) -> dict:
    if not listing_id.isdigit():
        raise EbayFailure("Enter an eBay listing ID.", 422)
    result = await client.trading("GetItem", {"ItemID": listing_id, "DetailLevel": "ReturnAll"})
    item = result["xml"].find("e:Item", NS)
    if item is None:
        raise EbayFailure("eBay did not return the listing.")
    def text(path):
        return item.findtext(path, namespaces=NS) or ""
    price = item.find("e:SellingStatus/e:CurrentPrice", NS)
    specifics = {}
    for aspect in item.findall("e:ItemSpecifics/e:NameValueList", NS):
        name = aspect.findtext("e:Name", namespaces=NS)
        values = [node.text for node in aspect.findall("e:Value", NS) if node.text]
        if name and values:
            specifics[name] = values
    return {"id": text("e:ItemID"), "title": text("e:Title"), "description": plain(text("e:Description")),
            "price": price.text if price is not None and price.get("currencyID") == "USD" else None,
            "quantity": text("e:Quantity"), "soldQuantity": text("e:SellingStatus/e:QuantitySold"),
            "status": text("e:SellingStatus/e:ListingStatus"), "type": text("e:ListingType"),
            "seller": text("e:Seller/e:UserID"), "sku": text("e:SKU"), "site": text("e:Site"),
            "categoryId": text("e:PrimaryCategory/e:CategoryID"), "variations": item.find("e:Variations", NS) is not None,
            "categoryName": text("e:PrimaryCategory/e:CategoryName"),
            "condition": text("e:ConditionDisplayName"), "conditionDescription": text("e:ConditionDescription"),
            "specifics": specifics,
            "images": [node.text for node in item.findall("e:PictureDetails/e:PictureURL", NS) if node.text]}
