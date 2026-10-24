"""Direct eBay access. Hosts are fixed and tokens never reach UI or logs."""
from __future__ import annotations

import hashlib
import base64
import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote

import httpx

from vendoo_studio.services.keychain import get_marketplace_accounts
from vendoo_studio.services.marketplace_accounts import SignInRequired, access_token
from vendoo_studio.services.relay_client import relay_request

API = "https://api.ebay.com"
APIZ = "https://apiz.ebay.com"
NS = {"e": "urn:ebay:apis:eBLBaseComponents"}


class EbayFailure(RuntimeError):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def segment(value: str) -> str:
    if not value or len(value) > 200 or value in (".", "..") or any(char in value for char in "/\\?#\x00"):
        raise EbayFailure("Invalid eBay identifier.", 422)
    return quote(value, safe="")


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def identity() -> tuple[str, str]:
    account = get_marketplace_accounts().get("ebay") or {}
    if not account.get("accountId") or not account.get("accessToken"):
        raise SignInRequired("Connect eBay to use this workspace.")
    # Reconnecting, including to the same seller, invalidates outstanding reviews.
    return str(account["accountId"]), str(account.get("connectedAt", ""))


def assert_identity(expected: tuple[str, str]) -> None:
    if identity() != expected:
        raise EbayFailure("The eBay connection changed. Review the action again.", 409)


class EbayClient:
    def __init__(self, token: str, seller: tuple[str, str]):
        self.token = token
        self.seller = seller

    @classmethod
    async def connect(cls):
        seller = identity()
        token = await access_token("ebay")
        assert_identity(seller)
        return cls(token, seller)

    async def request(self, method: str, path: str, *, params: dict | None = None, body: dict | None = None,
                      app: bool = False, xml: str | None = None, call: str | None = None, upload: dict | None = None,
                      raw: bool = False) -> dict:
        if not path.startswith(("/sell/", "/commerce/", "/buy/", "/ws/")) or ".." in path:
            raise EbayFailure("Invalid eBay operation.", 422)
        assert_identity(self.seller)
        token = self.token
        if app:
            operations = {
                ("GET", "/commerce/taxonomy/v1/category_tree/0/get_category_suggestions"): "categories",
                ("GET", "/commerce/taxonomy/v1/category_tree/0/get_item_aspects_for_category"): "aspects",
                ("GET", "/buy/browse/v1/item_summary/search"): "search",
                ("POST", "/buy/browse/v1/item_summary/search_by_image"): "image",
                ("GET", "/sell/marketing/v1/ad_report_metadata/CAMPAIGN_PERFORMANCE_SUMMARY_REPORT"): "ad_metadata",
            }
            operation = operations.get((method, path))
            if not operation:
                raise EbayFailure("Invalid eBay research operation.", 422)
            response = await relay_request("POST", "/studio/ebay/research", json_body={
                "operation": operation, "params": params or {}, **({"image": body["image"]} if operation == "image" else {}),
            })
            assert_identity(self.seller)
        headers = {"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
                   "Content-Language": "en-US"}
        host = APIZ if path.startswith(("/sell/finances/", "/sell/fulfillment/v1/payment_dispute")) else API
        if xml is not None:
            headers = {"X-EBAY-API-IAF-TOKEN": token, "X-EBAY-API-CALL-NAME": call,
                       "X-EBAY-API-SITEID": "0", "X-EBAY-API-COMPATIBILITY-LEVEL": "1487",
                       "Content-Type": "text/xml"}
        if not app:
            async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
                files = {upload["field"]: ("image.jpg" if upload["mime"] == "image/jpeg" else "image.png",
                                          base64.b64decode(upload["data"], validate=True), upload["mime"])} if upload else None
                response = await client.request(method, host + path, headers=headers, params=params,
                                                json=body if xml is None and not files else None, content=xml, files=files)
        # Never include response bodies: eBay errors can echo private input.
        if response.status_code in (401, 403):
            raise EbayFailure("eBay denied access. Grant permission for this feature, or check your seller eligibility.", 403)
        if not response.is_success:
            codes = []
            try:
                codes = [str(e["errorId"]) for e in response.json().get("errors", [])
                         if isinstance(e.get("errorId"), int)]
            except (ValueError, TypeError, KeyError):
                pass
            suffix = f" Error code: {', '.join(codes)}." if codes else ""
            raise EbayFailure(f"eBay returned HTTP {response.status_code}.{suffix}", response.status_code)
        try:
            assert_identity(self.seller)
        except (SignInRequired, EbayFailure) as exc:
            # A successful HTTP write may already have taken effect. A changed
            # connection after its response is uncertain, never a safe failure.
            raise EbayFailure("The eBay connection changed after the request. Check its result in eBay.") from exc
        if raw:
            if len(response.content) > 20000000:
                raise EbayFailure("This eBay report is too large to display. Request a smaller date range.")
            return {"content": response.content}
        if xml is not None:
            root = ET.fromstring(response.content)
            if root.findtext("e:Ack", namespaces=NS) not in ("Success", "Warning"):
                codes = [e.text for e in root.findall("e:Errors/e:ErrorCode", NS) if e.text and e.text.isdigit()]
                raise EbayFailure(f"eBay rejected the listing request. Error code: {', '.join(codes) or 'unknown'}.")
            return {"xml": root}
        result = response.json() if response.content else {}
        # Location is used only as a receipt ID, never followed as a URL.
        location = response.headers.get("location", "").rstrip("/").rsplit("/", 1)[-1]
        if location and re.fullmatch(r"[A-Za-z0-9._:-]{1,200}", location):
            result["receiptId"] = location
        return result

    async def trading(self, call: str, fields: dict[str, str]) -> dict:
        root = ET.Element(f"{call}Request", xmlns=NS["e"])
        for key, value in fields.items():
            ET.SubElement(root, key).text = value
        return await self.request("POST", "/ws/api.dll", xml=ET.tostring(root, encoding="unicode"), call=call)
