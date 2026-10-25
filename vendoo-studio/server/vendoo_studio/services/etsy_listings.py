"""Official Etsy API listing operations, through the existing secret-holding relay.

No browser session, background publishing, or automatic write retries. Persist
each accepted ID before the next write; checks only read the saved listing.
"""
from __future__ import annotations

import asyncio
import html
import re
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from pathlib import Path

import httpx

from vendoo_studio.config import PHOTOS_DIR
from vendoo_studio.models.etsy_fields import (
    etsy_when_options, is_etsy_digital_listing, resolve_etsy_when,
)
from vendoo_studio.services import marketplace_accounts
from vendoo_studio.services.keychain import get_marketplace_accounts
from vendoo_studio.services.relay_client import RelayError


def connected_shop_id() -> str:
    account = get_marketplace_accounts().get("etsy") or {}
    shop_id = str(account.get("accountId") or "")
    if not account.get("accessToken") or not re.fullmatch(r"[1-9]\d*", shop_id):
        raise ValueError("Connect Etsy in Settings → Connections first.")
    return shop_id


def listing_id(url: str | None) -> str:
    match = re.fullmatch(r"https://www\.etsy\.com/your/shops/me/listing-editor/edit/([1-9]\d*)/?", url or "")
    if not match:
        raise ValueError("Open or paste the saved Etsy draft link before checking its result.")
    return match[1]


def edit_url(item_id: str) -> str:
    if not re.fullmatch(r"[1-9]\d*", str(item_id)):
        raise ValueError("Etsy did not return a valid draft ID.")
    return f"https://www.etsy.com/your/shops/me/listing-editor/edit/{item_id}"


async def listing_profiles() -> dict[str, list[dict]]:
    shop_id = connected_shop_id()
    shipping, processing = await asyncio.gather(
        marketplace_accounts.etsy_api("GET", f"shops/{shop_id}/shipping-profiles"),
        marketplace_accounts.etsy_api("GET", f"shops/{shop_id}/readiness-state-definitions", params={"limit": 100}),
    )
    if connected_shop_id() != shop_id:
        raise ValueError("The connected Etsy shop changed. Load its shipping profiles again.")
    return {
        "shipping": [{"id": str(p["shipping_profile_id"]), "title": str(p.get("title") or "Shipping profile")[:200],
                      "is_calculated": p.get("profile_type") == "calculated"}
                     for p in shipping.get("results", []) if re.fullmatch(r"[1-9]\d*", str(p.get("shipping_profile_id") or ""))],
        "processing": [{"id": str(p["readiness_state_id"]), "title": " · ".join(filter(None, [
            {"ready_to_ship": "Ready to ship", "made_to_order": "Made to order"}.get(p.get("readiness_state"), "Processing profile"),
            str(p.get("processing_days_display_label") or "")[:100]]))}
            for p in processing.get("results", []) if re.fullmatch(r"[1-9]\d*", str(p.get("readiness_state_id") or ""))],
    }


def _price(value) -> Decimal:
    try:
        price = Decimal(str(value))
        if price.is_finite() and price > 0:
            rounded = price.quantize(Decimal("0.01"))
            if rounded > 0:
                return rounded
    except (InvalidOperation, ValueError):
        pass
    raise ValueError("Set a positive Etsy price before listing.")


def _remote_price(remote: dict) -> Decimal:
    money = remote.get("price") or {}
    products = (remote.get("_inventory") or {}).get("products") or []
    if len(products) == 1 and len(products[0].get("offerings") or []) == 1:
        money = products[0]["offerings"][0].get("price") or {}
    return Decimal(str(money["amount"])) / Decimal(str(money["divisor"]))


def _matches(remote: dict, snapshot: dict) -> bool:
    try:
        return (html.unescape(str(remote.get("title") or "")) == snapshot.get("title")
                and html.unescape(str(remote.get("description") or "")) == str(snapshot.get("description") or "")
                and _remote_price(remote) == _price(snapshot.get("price")))
    except (KeyError, InvalidOperation, ValueError, ZeroDivisionError):
        return False


def draft_fields(snapshot: dict, *, creating: bool) -> dict:
    title, description = str(snapshot.get("title") or ""), str(snapshot.get("description") or "")
    if not title.strip() or not description.strip():
        raise ValueError("Add an Etsy title and description before listing.")
    fields = {"title": title, "description": description, "price": str(_price(snapshot.get("price")))}
    if not creating:
        return fields
    if is_etsy_digital_listing(snapshot):
        raise ValueError("This listing flow supports physical Etsy items. Prepare digital files in Etsy Shop Manager.")
    details = snapshot.get("etsy_specifics") or {}
    who = {"I did": "i_did", "A member of my shop": "collective", "Another company or person": "someone_else"}.get(details.get("who_made"))
    what = {"A finished product": False, "A supply or tool to make things": True}.get(details.get("what_is"))
    when = resolve_etsy_when(str(details.get("when_made") or ""), etsy_when_options())
    label = re.sub(r"\s*\([^)]*\)\s*$", "", when).strip()
    when = {"Made To Order": "made_to_order", "2020 - 2026": "2020_2026", "2010 - 2019": "2010_2019",
            "2007 - 2009": "2007_2009", "2000 - 2006": "2000_2006", "Before 2007": "before_2007",
            "1900 - 1909": "1900s", "Before 1700": "before_1700"}.get(label, label)
    if not who or what is None or not re.fullmatch(r"made_to_order|2020_2026|2010_2019|2007_2009|2000_2006|before_2007|19[0-9]0s|1800s|1700s|before_1700", when):
        raise ValueError("Complete Etsy's who made it, what is it, and when made details before listing.")
    category = str((snapshot.get("marketplace_category_ids") or {}).get("etsy") or "")
    if not re.fullmatch(r"[1-9]\d*", category):
        raise ValueError("Choose a verified Etsy category before listing.")
    fields.update(quantity="1", who_made=who, when_made=when, is_supply=str(what).lower(),
                  taxonomy_id=category, type="physical", should_auto_renew="false")
    # Only use explicit seller-selected IDs. Shipping and processing can also be
    # completed in the native draft; never choose a shop policy on the seller's behalf.
    for key in ("shipping_profile_id", "return_policy_id", "readiness_state_id"):
        if re.fullmatch(r"[1-9]\d*", str(details.get(key) or "")):
            fields[key] = str(details[key])
    return fields


async def _read(shop_id: str, item_id: str) -> dict:
    remote = await marketplace_accounts.etsy_api("GET", f"listings/{item_id}", params={"includes": "Images"})
    if str(remote.get("shop_id")) != shop_id or str(remote.get("listing_id")) != item_id:
        raise ValueError("Etsy returned a listing outside the connected shop. Check the draft link.")
    remote["_inventory"] = await marketplace_accounts.etsy_api("GET", f"listings/{item_id}/inventory")
    return remote


def _inventory_price(inventory: dict, price: Decimal) -> dict:
    products = inventory.get("products") or []
    if len(products) != 1 or len(products[0].get("offerings") or []) != 1:
        raise ValueError("This Etsy draft has multiple variations or offerings. Review its prices in Etsy Shop Manager first.")
    product = products[0]
    offering = product["offerings"][0]
    if "quantity" not in offering or "is_enabled" not in offering:
        raise ValueError("Etsy did not return complete inventory. Check the native draft.")
    return {"products": [{"sku": product.get("sku") or "", "property_values": [
        {key: value for key, value in prop.items() if key in {"property_id", "property_name", "scale_id", "value_ids", "values"}}
        for prop in product.get("property_values") or []],
        "offerings": [{"price": float(price), "quantity": offering["quantity"], "is_enabled": offering["is_enabled"],
                       "readiness_state_id": offering.get("readiness_state_id")}]}],
        **{key: inventory.get(key) or [] for key in ("price_on_property", "quantity_on_property", "sku_on_property", "readiness_state_on_property")}}


def _success(remote: dict) -> dict:
    item_id = str(remote["listing_id"])
    return {"ok": True, "state": "published" if remote["state"] == "active" else "draft", "draft_url": edit_url(item_id),
            **({"listing_url": f"https://www.etsy.com/listing/{item_id}"} if remote["state"] == "active" else {})}


def _error_text(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        # Never include request headers, URLs with credentials, or raw response bodies.
        body = None
        try:
            body = exc.response.json()
            def messages_for(value):
                if isinstance(value, str):
                    return [value]
                if isinstance(value, list):
                    return [message for item in value for message in messages_for(item)]
                if isinstance(value, dict):
                    return [message for key in ("error", "message", "error_msg", "error_message", "error_description", "errors", "detail")
                            for message in messages_for(value.get(key))]
                return []
            messages = messages_for(body)
            message = "; ".join(str(value) for value in messages)
        except (ValueError, AttributeError):
            message = ""
        if not message and exc.response.status_code == 400:
            heading = re.search(r"<(?:title|h1)[^>]*>([^<]+)</(?:title|h1)>", exc.response.text, re.I)
            message = heading[1] if heading else "Etsy rejected these draft details without a validation message. Review the title and required fields."
        fields = [name for name in ("shipping_profile_id", "readiness_state_id", "return_policy_id", "taxonomy_id",
                                   "who_made", "when_made", "is_supply", "price", "quantity", "title", "description", "image")
                  if re.search(rf"\b{re.escape(name)}\b", message)]
        detail = f"Check Etsy fields: {', '.join(fields)}." if fields else "Review the Etsy draft fields and connection."
        if not fields and exc.response.status_code == 400 and message:
            # Etsy's validation error is intended for the seller. Keep only that
            # string, strip URLs and token-shaped text, and bound its length.
            safe = re.sub(r"https?://\S+", "[URL]", message)
            safe = re.sub(r"[A-Za-z0-9._~+/=-]{24,}", "[redacted]", safe)
            detail = " ".join(safe.split())[:240]
        return f"Etsy rejected the request (HTTP {exc.response.status_code}). {detail}"
    if isinstance(exc, httpx.HTTPError):
        return "Etsy's API did not confirm the request. Check the result before trying again."
    if isinstance(exc, (ValueError, marketplace_accounts.SignInRequired, RelayError)):
        return str(exc)
    return "Could not confirm the Etsy API action. Check its result before trying again."


async def execute(action: str, snapshot: dict, photos: list, binding: dict,
                  record: Callable[[dict], None], active: Callable[[], bool], *, confirm: bool = False,
                  shipping_profile_id: str | None = None, readiness_state_id: str | None = None,
                  package: dict | None = None) -> dict:
    attempted = False
    rejected = False
    try:
        shop_id = connected_shop_id()
        if binding.get("etsy_shop_id") and binding["etsy_shop_id"] != shop_id:
            raise ValueError("This Etsy draft belongs to a different connected shop.")
        record({"transport": "etsy_api", "etsy_shop_id": shop_id})

        async def write(method: str, path: str, kind: str, **kwargs):
            nonlocal attempted, rejected
            if not active() or connected_shop_id() != shop_id:
                raise ValueError("The Etsy action stopped or the connected shop changed. Check its result.")
            record({"etsy_pending_write": kind})
            attempted = True
            try:
                response = await marketplace_accounts.etsy_api(method, path, **kwargs)
            except marketplace_accounts.SignInRequired:
                # Refresh failure or a definite 401 cannot have committed this write.
                rejected = True
                record({"etsy_pending_write": None})
                raise
            except httpx.HTTPStatusError as exc:
                if 400 <= exc.response.status_code < 500 and exc.response.status_code not in {408, 409}:
                    rejected = True
                    record({"etsy_pending_write": None})
                raise
            return response

        if action == "publish":
            if confirm is not True:
                raise ValueError("Confirm listing on Etsy.")
            item_id = listing_id(binding.get("draft_url"))
            remote = await _read(shop_id, item_id)
            if remote.get("state") != "draft" or not _matches(remote, snapshot) or not remote.get("images"):
                raise ValueError("Etsy did not accept this listing's fields or photos.")
            remote = await write("PATCH", f"shops/{shop_id}/listings/{item_id}", "publish", data={"state": "active"})
            # A successful response is a receipt; a later read failure must not
            # discard it and invite a second paid activation.
            if str(remote.get("listing_id")) == item_id and str(remote.get("shop_id")) == shop_id and remote.get("state") == "active":
                record({"etsy_pending_write": None})
                return _success(remote)
            raise ValueError("Etsy has not confirmed activation. Check the result; publishing will not be retried.")

        if action != "save_draft":
            raise ValueError("Unsupported Etsy API action.")
        creating = not binding.get("draft_url")
        fields = draft_fields(snapshot, creating=creating)
        if creating:
            selected = shipping_profile_id or fields.get("shipping_profile_id")
            processing = readiness_state_id or fields.get("readiness_state_id")
            if not selected:
                raise ValueError("Choose an Etsy shipping profile in Settings before listing.")
            if not processing:
                raise ValueError("Choose an Etsy processing profile in Settings before listing.")
            profiles = await listing_profiles()
            if selected not in {profile["id"] for profile in profiles["shipping"]}:
                raise ValueError("Choose a shipping profile from the connected Etsy shop.")
            if processing not in {profile["id"] for profile in profiles["processing"]}:
                raise ValueError("Choose a processing profile from the connected Etsy shop.")
            shipping = next(profile for profile in profiles["shipping"] if profile["id"] == selected)
            if shipping["is_calculated"] and not package:
                raise ValueError("Enter packed weight and dimensions for Etsy calculated shipping.")
            fields["shipping_profile_id"] = selected
            fields["readiness_state_id"] = processing
            if package:
                fields.update({key: str(value) for key, value in package.items()})
            if not 1 <= len(photos) <= 20:
                raise ValueError("Choose between one and twenty Etsy photos.")
            remote = await write("POST", f"shops/{shop_id}/listings", "create", data=fields)
            item_id = str(remote.get("listing_id") or "")
            if str(remote.get("shop_id")) != shop_id or remote.get("state") != "draft":
                raise ValueError("Etsy did not confirm a private draft. Check its result before trying again.")
            record({"draft_url": edit_url(item_id), "etsy_pending_write": None,
                    "etsy_photo_ids": [p.id for p in photos], "etsy_image_ids": {}, "etsy_photos_complete": False,
                    "etsy_shipping_profile_id": selected, "etsy_readiness_state_id": processing})
        else:
            item_id = listing_id(binding["draft_url"])
            remote = await _read(shop_id, item_id)
            if remote.get("state") != "draft":
                raise ValueError("This Etsy listing is no longer a private draft.")
            if binding.get("etsy_photo_ids") and not binding.get("etsy_photos_complete"):
                if binding["etsy_photo_ids"] != [p.id for p in photos]:
                    raise ValueError("Finish the original Etsy draft photos before changing this draft's photo selection.")
                known = {str(value) for value in (binding.get("etsy_image_ids") or {}).values()}
                observed = {str(image.get("listing_image_id")) for image in remote.get("images") or []}
                if observed != known:
                    raise ValueError("Etsy has unconfirmed draft photos. Check the native draft before another upload.")
            # updateListing has no price parameter. Price is an inventory write;
            # preserve quantity, SKU, properties and processing for this product.
            inventory = remote.get("_inventory") or {}
            price_changed = _remote_price(remote) != _price(snapshot["price"])
            if price_changed:
                inventory_body = _inventory_price(inventory, _price(snapshot["price"]))
                await write("PUT", f"listings/{item_id}/inventory", "price", json_body=inventory_body)
                record({"etsy_pending_write": None})
            await write("PATCH", f"shops/{shop_id}/listings/{item_id}", "update", data={key: value for key, value in fields.items() if key != "price"})
            record({"etsy_pending_write": None})
        if binding.get("etsy_photo_ids") and not binding.get("etsy_photos_complete"):
            for rank, photo in enumerate(photos, start=1):
                if photo.id in binding.get("etsy_image_ids", {}):
                    continue
                path = (Path(PHOTOS_DIR) / photo.stored_filename).resolve()
                if not path.is_relative_to(Path(PHOTOS_DIR).resolve()) or not path.is_file():
                    raise ValueError("An Etsy photo is missing from Anteroom. Restore it before continuing.")
                with path.open("rb") as image:
                    uploaded = await write("POST", f"shops/{shop_id}/listings/{item_id}/images", "photo",
                                           data={"rank": str(rank)}, files={"image": (path.name, image, photo.mime_type)}, timeout=60)
                image_id = str(uploaded.get("listing_image_id") or "")
                if not re.fullmatch(r"[1-9]\d*", image_id):
                    raise ValueError("Etsy did not confirm a photo ID. Check the draft before another upload.")
                record({"etsy_image_ids": {**binding.get("etsy_image_ids", {}), photo.id: image_id}, "etsy_pending_write": None})
            record({"etsy_photos_complete": True})
        remote = await _read(shop_id, item_id)
        if remote.get("state") != "draft" or not _matches(remote, snapshot) or not remote.get("images"):
            raise ValueError("Etsy did not retain the private draft fields and photos. Check its result.")
        if binding.get("etsy_photo_ids") and len(remote["images"]) != len(binding["etsy_photo_ids"]):
            raise ValueError("Etsy did not retain every draft photo. Check its result.")
        return _success(remote)
    except Exception as exc:
        return {"ok": False, "safe_to_retry": not attempted or rejected, "error": _error_text(exc),
                **({"draft_url": binding["draft_url"]} if binding.get("draft_url") else {})}


async def check(snapshot: dict, binding: dict, candidate: str | None) -> dict:
    try:
        shop_id = connected_shop_id()
        if binding.get("etsy_shop_id") and binding["etsy_shop_id"] != shop_id:
            raise ValueError("This Etsy draft belongs to a different connected shop.")
        remote = await _read(shop_id, listing_id(candidate))
        if binding["action"] == "publish":
            if remote.get("state") == "active" and _matches(remote, snapshot):
                return _success(remote)
            # A draft read is not proof that a timed-out activation cannot finish.
            raise ValueError("Etsy has not confirmed activation. Check the native account; do not repeat publishing.")
        if remote.get("state") != "draft":
            raise ValueError("The Etsy item is no longer a private draft. Check the native listing.")
        expected = binding.get("etsy_photo_ids")
        complete = bool(remote.get("images")) and (not expected or len(remote["images"]) == len(expected))
        if _matches(remote, snapshot) and complete:
            return _success(remote)
        if not binding.get("etsy_pending_write"):
            return {"ok": False, "safe_to_retry": True, "draft_url": candidate,
                    "error": "This Etsy listing is incomplete. List again to finish the same one."}
        raise ValueError("Etsy's draft write is unconfirmed. Review its fields and photos before another attempt.")
    except Exception as exc:
        return {"ok": False, "error": _error_text(exc)}
