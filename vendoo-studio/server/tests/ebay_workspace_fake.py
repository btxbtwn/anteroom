"""A local HTTP stand-in using eBay's documented response shapes."""
import gzip
import json
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse

ITEM_ID = "123456789012"


def order():
    return {"orderId": "order-1", "orderPaymentStatus": "PAID", "orderFulfillmentStatus": "NOT_STARTED",
            "creationDate": "2026-10-01T12:00:00Z", "buyer": {"username": "test-buyer"},
            "pricingSummary": {"total": {"value": "50.00", "currency": "USD"}}, "paymentSummary": {"refunds": []},
            "lineItems": [{"lineItemId": "line-1", "legacyItemId": ITEM_ID, "quantity": 2, "title": "Test wool coat"}]}


def offer():
    return {"offerId": "offer-1", "sku": "existing", "marketplaceId": "EBAY_US", "status": "UNPUBLISHED",
            "availableQuantity": 1, "categoryId": "123", "pricingSummary": {"price": {"value": "45.00", "currency": "USD"}},
            "listingPolicies": {"fulfillmentPolicyId": "ship", "paymentPolicyId": "pay", "returnPolicyId": "return"}}


class WorkspaceEbay(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def reply(self, code, body=None, headers=None):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode() if body is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        state = self.server.state
        path = urlparse(self.path).path
        state.reads.append(self.path)
        if path in state.overrides:
            self.reply(200, state.overrides[path])
            return
        if path in state.read_errors:
            self.reply(state.read_errors[path], {"errors": [{"errorId": 999, "message": "PRIVATE ERROR ECHO"}]})
            return
        if path == "/commerce/message/v1/conversation":
            body = {"total": 1, "conversations": [{"conversationId": "chat-1", "conversationTitle": "Question about the coat",
                    "conversationStatus": "ACTIVE", "unreadCount": 1, "latestMessage": {"messageBody": "Is it wool?"}}]}
        elif path == "/commerce/message/v1/conversation/chat-1":
            body = {"conversationTitle": "Question about the coat", "total": 1,
                    "messages": [{"messageId": "m1", "senderUsername": "test-buyer", "messageBody": "<p>Is it wool?</p>"}]}
        elif path == "/commerce/feedback/v1/awaiting_feedback":
            body = {"pagination": {"total": 1}, "lineItems": [{"orderLineItemId": "line-1", "listingId": ITEM_ID,
                    "transactionId": "tx-1", "listingTitle": "Test wool coat"}]}
        elif path == "/sell/fulfillment/v1/order":
            body = {"total": 1, "orders": [order()]}
        elif path == "/sell/fulfillment/v1/order/order-1":
            body = order()
        elif path.endswith("/shipping_fulfillment"):
            body = {"fulfillments": []}
        elif path == "/sell/fulfillment/v1/payment_dispute_summary":
            body = {"total": 1, "paymentDisputeSummaries": [{"paymentDisputeId": "dispute-1", "orderId": "order-1", "paymentDisputeStatus": "OPEN"}]}
        elif path == "/sell/fulfillment/v1/payment_dispute/dispute-1":
            body = {"revision": 1, "paymentDisputeId": "dispute-1", "orderId": "order-1", "paymentDisputeStatus": "OPEN",
                    "lineItems": [{"lineItemId": "line-1", "itemId": ITEM_ID}]}
        elif path == "/sell/analytics/v1/traffic_report":
            body = {"header": {"metrics": [{"key": "LISTING_VIEWS_TOTAL"}]}, "records": [
                {"dimensionValues": [{"value": ITEM_ID}], "metricValues": [{"value": 30, "applicable": True}]}]}
        elif path == "/sell/finances/v1/transaction":
            body = {"total": 1, "transactions": [{"transactionId": "sale-1", "transactionType": "SALE", "orderId": "order-1",
                    "bookingEntry": "CREDIT", "amount": {"value": "40.00", "currency": "USD"},
                    "totalFeeAmount": {"value": "10.00", "currency": "USD"}}]}
        elif path == "/sell/finances/v1/payout":
            body = {"total": 1, "payouts": [{"payoutId": "p1", "payoutStatus": "SUCCEEDED", "amount": {"value": "40.00", "currency": "USD"}}]}
        elif path == "/sell/marketing/v1/ad_campaign":
            body = {"total": 1, "campaigns": [{"campaignId": "campaign-1", "campaignName": "Coat campaign", "campaignStatus": "RUNNING"}]}
        elif path == "/sell/marketing/v1/ad_campaign/campaign-1":
            body = {"campaignId": "campaign-1", "campaignName": "Coat campaign", "campaignStatus": "RUNNING", "fundingStrategy": {"fundingModel": "COST_PER_SALE", "bidPercentage": "2"}}
        elif path == "/sell/marketing/v1/ad_campaign/campaign-1/ad":
            body = {"total": 1, "ads": [{"adId": "ad-1", "listingId": ITEM_ID, "bidPercentage": "2"}]}
        elif path == "/sell/marketing/v1/item_price_markdown/promo-1%40EBAY_US":
            body = {"name": "Coat sale", "promotionStatus": "RUNNING", "selectedInventoryDiscounts": [
                {"discountBenefit": {"percentageOffItem": "10"}, "inventoryCriterion": {"listingIds": [ITEM_ID]}}]}
        elif path == "/sell/marketing/v1/promotion":
            body = {"total": 1, "promotions": [{"promotionId": "promo-1", "name": "Coat sale", "promotionStatus": "RUNNING"}]}
        elif path == "/sell/marketing/v1/promotion_report":
            body = {"total": 1, "promotionReports": [{"promotionId": "promo-1", "promotionSale": {"value": "90.00", "currency": "USD"}}]}
        elif path == "/sell/marketing/v1/email_campaign":
            body = {"total": 1, "campaigns": [{"emailCampaignId": "email-1", "subject": "Coat sale", "emailCampaignStatus": "SCHEDULED"}]}
        elif path == "/sell/marketing/v1/email_campaign/audience":
            body = {"total": 1, "audiences": [{"code": "FOLLOWERS", "name": "Store followers"}]}
        elif path == "/sell/inventory/v1/inventory_item":
            body = {"total": 1, "inventoryItems": [{"sku": "existing", "condition": "USED_EXCELLENT", "product": {"title": "Test wool coat"}}]}
        elif path.startswith("/sell/inventory/v1/inventory_item/"):
            sku = path.rsplit("/", 1)[-1]
            if sku != "existing" and sku not in state.inventory:
                self.reply(404, {})
                return
            body = state.inventory.get(sku) or {"condition": "USED_EXCELLENT", "product": {"title": "Test wool coat", "imageUrls": []}}
        elif path == "/sell/inventory/v1/offer":
            body = {"total": 1, "offers": [offer()]}
        elif path.startswith("/sell/inventory/v1/offer/"):
            body = offer()
        elif path == "/sell/inventory/v1/location":
            body = {"total": 1, "locations": [{"merchantLocationKey": "warehouse", "name": "My warehouse", "merchantLocationStatus": "ENABLED"}]}
        elif path == "/sell/inventory/v1/location/warehouse":
            body = {"merchantLocationStatus": "ENABLED"}
        elif path.startswith("/sell/account/v1/"):
            kind = path.split("/")[4].split("_")[0]
            body = {f"{kind}PolicyId": kind, "marketplaceId": "EBAY_US", "name": f"My {kind} policy"}
            if path.endswith(f"/{kind}_policy"):
                body = {f"{kind}Policies": [body]}
        elif path.endswith("/get_category_suggestions"):
            body = {"categorySuggestions": [{"category": {"categoryId": "123", "categoryName": "Coats"}, "categoryTreeNodeAncestors": []}]}
        elif path.endswith("/get_item_aspects_for_category"):
            body = {"aspects": [{"localizedAspectName": "Brand", "aspectConstraint": {"aspectRequired": True, "aspectMode": "FREE_TEXT"}, "aspectValues": [{"localizedValue": "Acme"}]}]}
        elif path == "/buy/browse/v1/item_summary/search":
            body = {"total": 1, "itemSummaries": [{"itemId": "v1|123|0", "title": "Comparable coat", "price": {"value": "49.00", "currency": "USD"}, "itemWebUrl": "https://www.ebay.com/itm/123456789012"}]}
        elif path == "/sell/marketing/v1/ad_report_metadata/CAMPAIGN_PERFORMANCE_SUMMARY_REPORT":
            body = {"dimensionMetadata": [{"dimensionKey": "campaign_id"}], "metricMetadata": [{"metricKey": "impressions"}, {"metricKey": "clicks"}]}
        elif path == "/sell/marketing/v1/ad_report_task":
            body = {"total": 1, "reportTasks": [{"reportTaskId": "task-1", "reportTaskStatus": "SUCCESS", "reportId": "report-1", "reportName": "Campaign results"}]}
        elif path == "/sell/marketing/v1/ad_report_task/task-1":
            body = {"reportId": "report-1", "reportTaskStatus": "SUCCESS"}
        elif path == "/sell/marketing/v1/ad_report/report-1":
            body = gzip.compress(b"campaign_id\timpressions\tclicks\ncampaign-1\t500\t20\n")
        elif path.startswith("/commerce/media/v1_beta/image/"):
            body = {"imageUrl": "https://i.ebayimg.com/images/g/test/s-l1600.jpg"}
        else:
            self.reply(404, {})
            return
        self.reply(200, body)

    def do_POST(self):
        self.write()

    def do_PUT(self):
        self.write()

    def write(self):
        state = self.server.state
        path = urlparse(self.path).path
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if path == "/ws/api.dll" and self.headers["X-EBAY-API-CALL-NAME"] == "GetItem":
            content = f'''<GetItemResponse xmlns="urn:ebay:apis:eBLBaseComponents"><Ack>Success</Ack><Item>
            <ItemID>{ITEM_ID}</ItemID><Title>Test wool coat</Title><Description>A wool coat</Description><Site>US</Site>
            <Seller><UserID>seller</UserID></Seller><ListingType>FixedPriceItem</ListingType><Quantity>2</Quantity>
            <SellingStatus><ListingStatus>Active</ListingStatus><CurrentPrice currencyID="USD">50.00</CurrentPrice><QuantitySold>0</QuantitySold></SellingStatus>
            <PrimaryCategory><CategoryID>123</CategoryID></PrimaryCategory></Item></GetItemResponse>'''
            self.reply(200, content.encode())
            return
        if path == "/buy/browse/v1/item_summary/search_by_image":
            self.reply(200, {"total": 1, "itemSummaries": [{"itemId": "image-match", "title": "Image match", "price": {"value": "40.00", "currency": "USD"}}]})
            return
        multipart = "multipart/form-data" in self.headers.get("Content-Type", "")
        payload = {"multipart": True, "bytes": len(raw)} if multipart else None if path == "/ws/api.dll" else json.loads(raw) if raw else {}
        state.writes.append((self.command, path, payload))
        if state.write_error:
            self.reply(state.write_error, {"errors": [{"errorId": 999, "message": "PRIVATE ERROR ECHO"}]})
            return
        if path in getattr(state, "write_results", {}):
            self.reply(200, state.write_results[path])
            return
        if path == "/ws/api.dll":
            self.reply(200, b'<ReviseFixedPriceItemResponse xmlns="urn:ebay:apis:eBLBaseComponents"><Ack>Success</Ack></ReviseFixedPriceItemResponse>')
        elif path.endswith("/bulk_update_price_quantity"):
            self.reply(200, {"responses": [{"statusCode": 200}]})
        elif path.startswith("/sell/inventory/v1/inventory_item/"):
            state.inventory[path.rsplit("/", 1)[-1]] = payload
            self.reply(204)
        elif path == "/sell/inventory/v1/offer":
            self.reply(201, {"offerId": "new-offer"})
        elif path.endswith("/publish"):
            self.reply(200, {"listingId": ITEM_ID})
        elif path.endswith("/upload_evidence_file"):
            self.reply(200, {"fileId": "file-1"})
        elif path.endswith("/create_image_from_file"):
            self.reply(201, {"imageUrl": "https://i.ebayimg.com/images/g/test/s-l1600.jpg"}, {"Location": "/commerce/media/v1_beta/image/image-1"})
        else:
            self.reply(201, {}, {"Location": "https://api.ebay.com/receipt/receipt-1"})
