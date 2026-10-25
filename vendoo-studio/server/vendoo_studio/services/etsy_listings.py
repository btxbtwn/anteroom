"""Official Etsy API listing operations, through the existing secret-holding relay.

No browser session, background publishing, or automatic write retries. Persist
each accepted ID before the next write; checks only read the shop. A check
finds a draft whose create reply was lost by its title among the shop's drafts.
"""
from __future__ import annotations

import asyncio
import html
import re
import unicodedata
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from pathlib import Path

import httpx

from vendoo_studio.config import PHOTOS_DIR
from vendoo_studio.models.etsy_fields import (
    sanitize_etsy_tags, etsy_eligible, etsy_when_made_code, etsy_when_options, etsy_when_to_store, is_etsy_digital_listing,
)
from vendoo_studio.services import marketplace_accounts, marketplace_schemas
from vendoo_studio.services.keychain import get_marketplace_accounts
from vendoo_studio.services.listing_result import ListingProblem, failure, missing, retry_after_seconds, scrub
from vendoo_studio.services.direct_preflight import Context, gap, requirement, seeded_leaf
from vendoo_studio.services.native_form import vendoo_form
from vendoo_studio.services.relay_client import RelayError
from vendoo_studio.services.vendoo_marketplace_fields import ETSY_WHAT_CODES, ETSY_WHO_CODES
from vendoo_studio.services.vendoo_specifics import is_not_applicable


NOT_ELIGIBLE = "Etsy only allows vintage (20+ years), handmade or craft supplies. This item doesn't qualify."


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
    shipping, processing, returns = await asyncio.gather(
        marketplace_accounts.etsy_api("GET", f"shops/{shop_id}/shipping-profiles"),
        marketplace_accounts.etsy_api("GET", f"shops/{shop_id}/readiness-state-definitions", params={"limit": 100}),
        marketplace_accounts.etsy_api("GET", f"shops/{shop_id}/policies/return"),
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
        "returns": [{"id": str(p["return_policy_id"]), "title": _return_title(p)}
                    for p in returns.get("results", []) if re.fullmatch(r"[1-9]\d*", str(p.get("return_policy_id") or ""))],
    }


def _return_title(policy: dict) -> str:
    if not policy.get("accepts_returns") and not policy.get("accepts_exchanges"):
        return "No returns or exchanges"
    kinds = " and ".join(word for word, on in (("Returns", policy.get("accepts_returns")), ("exchanges", policy.get("accepts_exchanges"))) if on)
    days = policy.get("return_deadline")
    return f"{kinds.capitalize()} within {days} days" if days else kinds.capitalize()


def _price(value) -> Decimal:
    try:
        price = Decimal(str(value))
        if price.is_finite() and price > 0:
            rounded = price.quantize(Decimal("0.01"))
            if rounded > 0:
                return rounded
    except (InvalidOperation, ValueError):
        pass
    raise missing("Set a positive Etsy price before listing.", "price")


def _remote_price(remote: dict) -> Decimal:
    money = remote.get("price") or {}
    products = (remote.get("_inventory") or {}).get("products") or []
    if len(products) == 1 and len(products[0].get("offerings") or []) == 1:
        money = products[0]["offerings"][0].get("price") or {}
    return Decimal(str(money["amount"])) / Decimal(str(money["divisor"]))


def _matches(remote: dict, snapshot: dict) -> bool:
    try:
        return (html.unescape(str(remote.get("title") or "")) == etsy_title(snapshot)
                and html.unescape(str(remote.get("description") or "")) == str(snapshot.get("description") or "")
                and _remote_price(remote) == _price(snapshot.get("price")))
    except (KeyError, InvalidOperation, ValueError, ZeroDivisionError):
        return False


_ETSY_TITLE_MAX = 140
_ETSY_TITLE_SYMBOLS = frozenset("™©®")
# Etsy takes each of these at most once in a title.
_ETSY_TITLE_ONCE = "%:&+"
_ETSY_LIST_LIMITS = {"materials": (13, 45), "styles": (2, 45)}


def _title_char(ch: str) -> str:
    if ch in _ETSY_TITLE_SYMBOLS:
        return ch
    category = unicodedata.category(ch)
    if category[0] in "LP" or category in {"Nd", "Sm", "Zs"}:
        return ch
    return " " if ch.isspace() else ""


def etsy_title(snapshot: dict) -> str:
    """The title as Etsy takes it: its characters only, %:&+ once each, a letter or digit first, 140 at most.

    A longer title is cut at the last whole word; the pre-flight says so.
    """
    text = "".join(_title_char(ch) for ch in unicodedata.normalize("NFC", str(snapshot.get("title") or "")))
    for symbol in _ETSY_TITLE_ONCE:
        first = text.find(symbol)
        if first >= 0:
            text = text[:first + 1] + text[first + 1:].replace(symbol, " ")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^[\W_]+", "", text)
    if len(text) > _ETSY_TITLE_MAX:
        cut = text[:_ETSY_TITLE_MAX + 1]
        text = (cut.rsplit(" ", 1)[0] if " " in cut else text[:_ETSY_TITLE_MAX]).rstrip(" -,|/:&+")
    return text


def _words(values, limit: int, max_length: int) -> list[str]:
    """Etsy materials and styles: letters, numbers and spaces only, unique, capped."""
    if isinstance(values, str):
        values = values.split(",")
    out: list[str] = []
    for value in values if isinstance(values, list) else []:
        text = re.sub(r"\s+", " ", "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in str(value or ""))).strip()
        if text and len(text) <= max_length and text.casefold() not in {item.casefold() for item in out}:
            out.append(text)
    return out[:limit]


def _form(snapshot: dict) -> dict:
    return snapshot.get("form") if isinstance(snapshot.get("form"), dict) else vendoo_form(snapshot, "etsy")


def list_fields(snapshot: dict) -> dict[str, list[str]]:
    """Tags, materials and styles, cleaned the way Etsy takes them (the Vendoo path's tags)."""
    details = snapshot.get("etsy_specifics") if isinstance(snapshot.get("etsy_specifics"), dict) else {}
    specifics = _form(snapshot).get("specifics") or {}
    tags = sanitize_etsy_tags(specifics.get("tags") or details.get("tags") or snapshot.get("tags"))
    materials = _words(details.get("materials") or snapshot.get("material"), *_ETSY_LIST_LIMITS["materials"])
    styles = _words(details.get("styles") or details.get("style") or snapshot.get("style"), *_ETSY_LIST_LIMITS["styles"])
    return {key: value for key, value in (("tags", tags), ("materials", materials), ("styles", styles)) if value}


def _code(raw, codes: dict[str, str]) -> str | None:
    """Etsy's code for a who-made or what-is answer, given as Etsy's code or Studio's label."""
    if isinstance(raw, bool):
        raw = "1" if raw else "0"
    text = str(raw or "").strip()
    if not text:
        return None
    if text in codes.values():
        return text
    return codes.get(re.sub(r"\s+", " ", text.lower()))


_WHO = dict(ETSY_WHO_CODES, **{"i_did": "i_did", "collective": "collective", "someone_else": "someone_else"})
# Studio's label, Vendoo's code ("0" finished, "1" supply) or Etsy's own is_supply.
_WHAT = dict(ETSY_WHAT_CODES, **{"false": "0", "true": "1", "finished product": "0", "supply": "1"})


def etsy_quantity(snapshot: dict) -> int:
    try:
        quantity = int(str(snapshot.get("quantity") or 1).strip())
    except ValueError:
        quantity = 0
    if not 1 <= quantity <= 999:
        raise missing("Enter an Etsy quantity from 1 to 999.", "quantity")
    return quantity


def draft_fields(snapshot: dict, *, creating: bool) -> dict:
    title, description = etsy_title(snapshot), str(snapshot.get("description") or "")
    if not title.strip() or not description.strip():
        raise missing("Add an Etsy title and description before listing.",
                      *[name for name, value in (("title", title), ("description", description)) if not value.strip()])
    fields = {"title": title, "description": description, "price": str(_price(snapshot.get("price")))}
    # Etsy takes list fields as comma-separated values.
    fields.update({key: ",".join(values) for key, values in list_fields(snapshot).items()})
    if not creating:
        return fields
    if is_etsy_digital_listing(snapshot):
        raise ValueError("This listing flow supports physical Etsy items. Prepare digital files in Etsy Shop Manager.")
    details = snapshot.get("etsy_specifics") or {}
    who = _code(details.get("who_made") or details.get("whoMade"), _WHO)
    what = _code(details.get("what_is") or details.get("whatIsIt"), _WHAT)
    when = etsy_when_to_store(str(details.get("when_made") or ""), snapshot, etsy_when_options())
    when = etsy_when_made_code(when)
    if not who or what is None or not when:
        raise missing("Complete Etsy's who made it, what is it, and when made details before listing.",
                      *[f"etsy_specifics.{name}" for name, value in (("who_made", who), ("what_is", what), ("when_made", when))
                        if not value])
    from vendoo_studio.services.direct_categories import direct_category_id

    category = direct_category_id(snapshot, "etsy") or str((snapshot.get("marketplace_category_ids") or {}).get("etsy") or "")
    if not re.fullmatch(r"[1-9]\d*", category):
        raise missing("Choose a verified Etsy category before listing.", "marketplace_category_ids.etsy")
    # Like the Vendoo path: is_supply is Etsy's "What is it?" answer, a supply or tool.
    fields.update(quantity=str(etsy_quantity(snapshot)), who_made=who, when_made=when, is_supply="true" if what == "1" else "false",
                  taxonomy_id=category, type="physical", should_auto_renew="false")
    # Only use explicit seller-selected IDs. Preparation renames these to
    # Vendoo's camelCase; the listing may still have the snake_case spelling.
    # Shipping and processing can also be completed in the native draft; never
    # choose a shop policy on the seller's behalf.
    for key, aliases in (
        ("shipping_profile_id", ("shipping_profile_id", "shippingProfileId")),
        ("return_policy_id", ("return_policy_id", "returnPolicyId")),
        ("readiness_state_id", ("readiness_state_id", "readinessStateId")),
    ):
        chosen = next((str(details.get(name) or "").strip() for name in aliases
                       if re.fullmatch(r"[1-9]\d*", str(details.get(name) or "").strip())), "")
        if chosen:
            fields[key] = chosen
    return fields


@requirement("etsy")
def requirements(snapshot: dict, context: Context) -> list[dict]:
    """What Etsy would refuse, read from the listing alone. Shop profiles are chosen in Settings."""
    found = []
    try:
        fields = draft_fields(snapshot, creating=True)
    except ListingProblem as exc:
        return [gap(item["field"], item["message"]) for item in exc.fields or []] or [gap("listing", str(exc))]
    except ValueError as exc:
        return [gap("listing", str(exc))]
    if fields["title"] != str(snapshot.get("title") or "").strip():
        reason = (f"Etsy titles are at most {_ETSY_TITLE_MAX} characters" if len(str(snapshot.get("title") or "")) > _ETSY_TITLE_MAX
                  else "Etsy allows %, :, & and + once each and no emoji or special symbols")
        found.append(gap("title", f"{reason}, so Etsy gets: {fields['title']}", blocking=False))
    if seeded_leaf(context.db, "etsy", fields["taxonomy_id"]) is False:
        found.append(gap("marketplace_category_ids.etsy", "That Etsy category has subcategories. Choose the most specific one."))
    found.extend(_cached_property_gaps(snapshot))
    return found


def _cached_property_gaps(snapshot: dict) -> list[dict]:
    """Required Etsy category properties from the cached live schema. No cache means List still checks them."""
    from vendoo_studio.services.category_fields import load_rows
    from vendoo_studio.services.direct_categories import direct_category_id
    from vendoo_studio.services.marketplace_schemas import ETSY_PROPERTIES

    category = direct_category_id(snapshot, "etsy") or str((snapshot.get("marketplace_category_ids") or {}).get("etsy") or "")
    if not re.fullmatch(r"[1-9]\d*", category):
        return []
    try:
        rows = load_rows(ETSY_PROPERTIES, category)
    except Exception:  # noqa: BLE001 - a closed database only means the live check at List remains
        return []
    if not rows:
        return []
    try:
        property_values(snapshot, rows)
    except ListingProblem as exc:
        return [gap(item["field"], item["message"]) for item in exc.fields or []]
    return []


# --- category properties ----------------------------------------------------------

def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


# Listing keys that are never category properties.
_NOT_PROPERTIES = frozenset(_key(key) for key in (
    "who_made", "what_is", "when_made", "section", "materials", "tags", "styles", "style", "category_specifics",
    "shipping_profile_id", "return_policy_id", "readiness_state_id", "listing_type", "renewal_option"))


def _answers(snapshot: dict) -> dict[str, list[str]]:
    """What the listing says, keyed the way Etsy names properties ("Sleeve length" → sleevelength)."""
    details = snapshot.get("etsy_specifics") if isinstance(snapshot.get("etsy_specifics"), dict) else {}
    nested = details.get("category_specifics") if isinstance(details.get("category_specifics"), dict) else {}
    answers: dict[str, list[str]] = {}

    def put(name: str, value) -> None:
        key = _key(name)
        values = value if isinstance(value, list) else str(value or "").split(",")
        values = [str(part).strip() for part in values if str(part or "").strip() and not is_not_applicable(part)]
        if key and values and key not in answers and key not in _NOT_PROPERTIES:
            answers[key] = values

    for name, value in nested.items():
        put(name, value)
    for name, value in details.items():
        if not isinstance(value, dict):
            put(name, value)
    put("Primary color", snapshot.get("primaryColor") or snapshot.get("color"))
    put("Secondary color", snapshot.get("secondaryColor"))
    return answers


def _match(value: str, choices: list[dict]) -> dict | None:
    """Etsy's value for the listing's word: exact, then singular, then the one choice that contains it."""
    wanted = _key(value)
    if not wanted:
        return None
    for compare in (lambda label: label == wanted, lambda label: label.rstrip("s") == wanted.rstrip("s")):
        hit = next((choice for choice in choices if compare(_key(choice["label"]))), None)
        if hit:
            return hit
    near = [choice for choice in choices if wanted in _key(choice["label"]) or _key(choice["label"]) in wanted]
    return near[0] if len(near) == 1 else None


def property_values(snapshot: dict, properties: list[dict]) -> list[dict]:
    """The category properties to write, matched against Etsy's own (``marketplace_schemas``).

    A required one with no answer is refused before anything is written.
    """
    answers = _answers(snapshot)
    assignments: list[dict] = []
    problems: list[dict] = []
    for prop in properties:
        name = prop["name"]
        values = answers.get(_key(name)) or answers.get(_key(prop.get("alias") or "")) or []
        hits: list[dict] = []
        for value in values:
            hit = _match(value, prop["values"]) if prop["values"] else {"id": None, "label": value}
            if hit and hit not in hits:
                hits.append(hit)
        limit = int(prop.get("max_values") or (len(hits) if prop.get("multi") else 1) or 1)
        hits = hits[:limit]
        if not hits:
            if prop.get("required"):
                message = (f"Choose one of Etsy's {name} values." if values else f"Etsy needs {name} in this category.")
                problems.append({"field": f"etsy_specifics.category_specifics.{name}", "message": message})
            continue
        row = {"property_id": prop["id"], "name": name, "value_ids": [hit["id"] for hit in hits if hit["id"] is not None],
               "values": [hit["label"].replace("(", "").replace(")", "") for hit in hits]}
        scale = next((hit.get("scale_id") for hit in hits if hit.get("scale_id")), None)
        if scale:
            row["scale_id"] = scale
        assignments.append(row)
    if problems:
        raise ListingProblem(" ".join(item["message"] for item in problems), "missing_fields", fields=problems)
    return assignments


async def _write_properties(write, shop_id: str, item_id: str, assignments: list[dict]) -> None:
    for row in assignments:
        data = {"value_ids": ",".join(str(value) for value in row["value_ids"]), "values": ",".join(row["values"])}
        if row.get("scale_id"):
            data["scale_id"] = str(row["scale_id"])
        await write("PUT", f"shops/{shop_id}/listings/{item_id}/properties/{row['property_id']}", "properties", data=data)


async def unkept_properties(item_id: str, assignments: list[dict]) -> list[str]:
    """The written properties Etsy did not keep, read back from the listing."""
    if not assignments:
        return []
    body = await marketplace_accounts.etsy_api("GET", f"listings/{item_id}/properties")
    kept = {str(row.get("property_id")): ({str(value) for value in row.get("value_ids") or []},
                                          {str(value).casefold() for value in row.get("values") or []})
            for row in body.get("results") or []}
    lost = []
    for row in assignments:
        ids, names = kept.get(str(row["property_id"]), (set(), set()))
        wanted_ids = {str(value) for value in row["value_ids"]}
        if (wanted_ids and not wanted_ids <= ids) or (not wanted_ids and not {v.casefold() for v in row["values"]} <= names):
            lost.append(row["name"])
    return lost


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


# Etsy's request field names, and where each lives on the listing.
_FIELD_PATHS = {"shipping_profile_id": "etsy_specifics.shipping_profile_id", "readiness_state_id": "etsy_specifics.readiness_state_id",
                "return_policy_id": "etsy_specifics.return_policy_id", "taxonomy_id": "marketplace_category_ids.etsy",
                "who_made": "etsy_specifics.who_made", "when_made": "etsy_specifics.when_made", "is_supply": "etsy_specifics.what_is",
                "price": "price", "quantity": "quantity", "title": "title", "description": "description", "image": "photos"}


def _etsy_messages(exc: httpx.HTTPStatusError) -> str:
    try:
        body = exc.response.json()
    except (ValueError, AttributeError):
        return ""

    def messages_for(value):
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [message for item in value for message in messages_for(item)]
        if isinstance(value, dict):
            return [message for key in ("error", "message", "error_msg", "error_message", "error_description", "errors", "detail")
                    for message in messages_for(value.get(key))]
        return []

    return "; ".join(str(value) for value in messages_for(body))


def marketplace_message(exc: Exception) -> str | None:
    """What Etsy itself said, scrubbed. None when Etsy did not answer."""
    if not isinstance(exc, httpx.HTTPStatusError):
        return None
    said = _etsy_messages(exc)
    if not said:
        heading = re.search(r"<(?:title|h1)[^>]*>([^<]+)</(?:title|h1)>", exc.response.text or "", re.I)
        said = heading[1] if heading else f"HTTP {exc.response.status_code} {exc.response.reason_phrase or ''}"
    return scrub(said)


def _named_fields(message: str) -> list[str]:
    return [name for name in _FIELD_PATHS if re.search(rf"\b{re.escape(name)}\b", message)]


def _error_text(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        # Our own words only: Etsy's text goes beside them as marketplace_message.
        status = exc.response.status_code
        if status == 429:
            return "Etsy is limiting requests right now. Wait a moment, then List again."
        message = _etsy_messages(exc)
        fields = _named_fields(message)
        detail = f"Check Etsy fields: {', '.join(fields)}." if fields else "Review the Etsy draft fields and connection."
        if not fields and status == 400:
            detail = "Etsy refused these listing details. Its reason is below."
        return f"Etsy rejected the request (HTTP {status}). {detail}"
    if isinstance(exc, httpx.HTTPError):
        return "Etsy's API did not confirm the request. Check the result before trying again."
    if isinstance(exc, (ValueError, marketplace_accounts.SignInRequired, RelayError)):
        return str(exc)
    return "Could not confirm the Etsy API action. Check its result before trying again."


def _failure(exc: Exception, *, safe: bool, **extra) -> dict:
    """The contract result for an exception. Not safe means a write may have landed."""
    said = marketplace_message(exc) or getattr(exc, "marketplace_message", None)
    text = _error_text(exc)
    if not safe:
        return failure(text, "uncertain", safe=False, marketplace_message=said, **extra)
    if isinstance(exc, ListingProblem):
        return failure(text, exc.code, safe=True, fields=exc.fields, marketplace_message=said, **extra)
    if isinstance(exc, marketplace_accounts.SignInRequired):
        return failure(text, "signed_out", safe=True, **extra)
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 429:
            return failure(text, "rate_limited", safe=True, marketplace_message=said,
                           retry_after_s=retry_after_seconds(exc.response.headers.get("retry-after")), **extra)
        fields = _named_fields(_etsy_messages(exc))
        if status == 400 and fields:
            return failure(text, "missing_fields", safe=True, marketplace_message=said,
                           fields=[{"field": _FIELD_PATHS[name], "message": f"Etsy refused {name}."} for name in fields], **extra)
        return failure(text, "rejected", safe=True, marketplace_message=said, **extra)
    if isinstance(exc, (RelayError, httpx.TransportError)):
        return failure(text, "network", safe=True, **extra)
    return failure(text, "rejected", safe=True, **extra)


async def find_draft(shop_id: str, snapshot: dict) -> str | None:
    """The edit link of the shop's one draft with this listing's title, when a create reply was lost."""
    found = await marketplace_accounts.etsy_api("GET", f"shops/{shop_id}/listings", params={
        "state": "draft", "limit": 100, "sort_on": "created", "sort_order": "desc"})
    title = etsy_title(snapshot)
    matches = [str(item.get("listing_id")) for item in found.get("results") or []
               if html.unescape(str(item.get("title") or "")) == title and str(item.get("shop_id")) == shop_id]
    if len(matches) > 1:
        raise ValueError(f"Etsy has {len(matches)} drafts titled like this listing. Delete the extras in Etsy, then use “I checked it myself”.")
    return edit_url(matches[0]) if matches else None


async def _sync_photos(write, shop_id: str, item_id: str, remote: dict, photos: list, binding: dict,
                       record: Callable[[dict], None]) -> None:
    """Make the Etsy listing's photos the item's photos: upload new ones, remove dropped ones.

    Every image ID is saved as Etsy confirms it, so a retry never uploads a photo twice.
    """
    wanted = [photo.id for photo in photos]
    if binding.get("etsy_photo_ids") is None and remote.get("images"):
        # Photos Anteroom did not upload (a pasted listing): leave them alone.
        return
    if binding.get("etsy_photos_complete") and binding.get("etsy_photo_ids") == wanted:
        return
    images = {str(key): str(value) for key, value in (binding.get("etsy_image_ids") or {}).items()}
    observed = {str(image.get("listing_image_id")) for image in remote.get("images") or []}
    if observed != set(images.values()):
        raise ValueError("Etsy has draft photos Anteroom has not confirmed. Check the listing in Etsy before another upload.")
    record({"etsy_photo_ids": wanted, "etsy_photos_complete": False})
    for photo_id in [key for key in images if key not in wanted]:
        await write("DELETE", f"shops/{shop_id}/listings/{item_id}/images/{images[photo_id]}", "photo")
        images.pop(photo_id)
        record({"etsy_image_ids": dict(images), "etsy_pending_write": None})
    for rank, photo in enumerate(photos, start=1):
        if photo.id in images:
            continue
        path = (Path(PHOTOS_DIR) / photo.stored_filename).resolve()
        if not path.is_relative_to(Path(PHOTOS_DIR).resolve()) or not path.is_file():
            raise missing("An Etsy photo is missing from Anteroom. Restore it before continuing.", "photos")
        with path.open("rb") as image:
            uploaded = await write("POST", f"shops/{shop_id}/listings/{item_id}/images", "photo",
                                   data={"rank": str(rank)}, files={"image": (path.name, image, photo.mime_type)}, timeout=60)
        image_id = str(uploaded.get("listing_image_id") or "")
        if not re.fullmatch(r"[1-9]\d*", image_id):
            raise ValueError("Etsy did not confirm a photo ID. Check the draft before another upload.")
        images[photo.id] = image_id
        record({"etsy_image_ids": dict(images), "etsy_pending_write": None})
    record({"etsy_photos_complete": True})


async def execute(action: str, snapshot: dict, photos: list, binding: dict,
                  record: Callable[[dict], None], active: Callable[[], bool], *, confirm: bool = False,
                  shipping_profile_id: str | None = None, readiness_state_id: str | None = None,
                  return_policy_id: str | None = None, package: dict | None = None) -> dict:
    attempted = False
    rejected = False
    try:
        shop_id = connected_shop_id()
        if binding.get("etsy_shop_id") and binding["etsy_shop_id"] != shop_id:
            raise ValueError("This Etsy draft belongs to a different connected shop.")
        record({"etsy_shop_id": shop_id})

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

        if action == "update":
            if confirm is not True:
                raise ValueError("Confirm updating the live Etsy listing.")
            item_id = str(binding.get("listing_id") or "")
            if not re.fullmatch(r"[1-9]\d*", item_id):
                item_id = listing_id(binding.get("draft_url"))
            remote = await _read(shop_id, item_id)
            if remote.get("state") != "active":
                raise ValueError("This Etsy listing is not live. Check it in Etsy before updating it.")
            fields = draft_fields(snapshot, creating=False)
            inventory = remote.get("_inventory") or {}
            if _remote_price(remote) != _price(snapshot["price"]):
                await write("PUT", f"listings/{item_id}/inventory", "price", json_body=_inventory_price(inventory, _price(snapshot["price"])))
                record({"etsy_pending_write": None})
            await write("PATCH", f"shops/{shop_id}/listings/{item_id}", "update", data={key: value for key, value in fields.items() if key != "price"})
            record({"etsy_pending_write": None})
            await _sync_photos(write, shop_id, item_id, remote, photos, binding, record)
            remote = await _read(shop_id, item_id)
            if remote.get("state") != "active" or not _matches(remote, snapshot):
                raise ValueError("Etsy has not confirmed the update on the live listing. Check it before trying again.")
            return _success(remote)

        if action == "publish":
            if confirm is not True:
                raise ValueError("Confirm listing on Etsy.")
            item_id = listing_id(binding.get("draft_url"))
            remote = await _read(shop_id, item_id)
            if remote.get("state") == "active" and _matches(remote, snapshot):
                # An earlier activation finished after its reply was lost.
                record({"etsy_pending_write": None})
                return _success(remote)
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
        if not etsy_eligible(snapshot):
            # Etsy refuses modern resale outright; nothing is sent.
            raise ValueError(NOT_ELIGIBLE)
        creating = not binding.get("draft_url")
        fields = draft_fields(snapshot, creating=True)
        taxonomy = fields["taxonomy_id"]
        if taxonomy not in await marketplace_schemas.etsy_leaves():
            raise missing("Etsy no longer offers that category, or it has subcategories. Choose a current Etsy category.",
                          "marketplace_category_ids.etsy")
        # Every required category property is answered before anything is written.
        assignments = property_values(snapshot, await marketplace_schemas.etsy_properties(taxonomy))
        if not creating:
            fields = draft_fields(snapshot, creating=False)
        if creating:
            selected = shipping_profile_id or fields.get("shipping_profile_id")
            processing = readiness_state_id or fields.get("readiness_state_id")
            returns = return_policy_id or fields.get("return_policy_id")
            if not selected:
                raise ValueError("Choose an Etsy shipping profile in Settings before listing.")
            if not processing:
                raise ValueError("Choose an Etsy processing profile in Settings before listing.")
            if not returns:
                raise ValueError("Choose an Etsy return policy in Settings before listing.")
            profiles = await listing_profiles()
            if selected not in {profile["id"] for profile in profiles["shipping"]}:
                raise ValueError("Choose a shipping profile from the connected Etsy shop.")
            if processing not in {profile["id"] for profile in profiles["processing"]}:
                raise ValueError("Choose a processing profile from the connected Etsy shop.")
            if returns not in {profile["id"] for profile in profiles["returns"]}:
                raise ValueError("Choose a return policy from the connected Etsy shop.")
            shipping = next(profile for profile in profiles["shipping"] if profile["id"] == selected)
            if shipping["is_calculated"] and not package:
                raise ValueError("Enter packed weight and dimensions for Etsy calculated shipping.")
            fields["shipping_profile_id"] = selected
            fields["readiness_state_id"] = processing
            fields["return_policy_id"] = returns
            if package:
                fields.update({key: str(value) for key, value in package.items()})
            if not 1 <= len(photos) <= 20:
                raise missing("Choose between one and twenty Etsy photos.", "photos")
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
        # Each property write replaces that property, so writing it again is safe.
        await _write_properties(write, shop_id, item_id, assignments)
        record({"etsy_pending_write": None})
        lost = await unkept_properties(item_id, assignments)
        if lost:
            rejected = True
            raise ListingProblem(f"Etsy did not keep these category details: {', '.join(lost)}. Check them in the Etsy draft.",
                                 "rejected", fields=[{"field": f"etsy_specifics.category_specifics.{name}", "message": "Etsy did not keep it."}
                                                     for name in lost])
        await _sync_photos(write, shop_id, item_id, remote, photos, binding, record)
        remote = await _read(shop_id, item_id)
        if remote.get("state") != "draft" or not _matches(remote, snapshot) or not remote.get("images"):
            raise ValueError("Etsy did not retain the private draft fields and photos. Check its result.")
        if binding.get("etsy_photo_ids") and len(remote["images"]) != len(binding["etsy_photo_ids"]):
            raise ValueError("Etsy did not retain every draft photo. Check its result.")
        return _success(remote)
    except Exception as exc:
        return _failure(exc, safe=not attempted or rejected,
                        **({"draft_url": binding["draft_url"]} if binding.get("draft_url") else {}))


async def check(snapshot: dict, binding: dict, candidate: str | None) -> dict:
    """Read the shop for this action's result. Never writes."""
    try:
        shop_id = connected_shop_id()
        if binding.get("etsy_shop_id") and binding["etsy_shop_id"] != shop_id:
            raise ValueError("This Etsy draft belongs to a different connected shop.")
        found = not candidate
        if found:
            candidate = await find_draft(shop_id, snapshot)
            if not candidate:
                # The draft is created first; with no draft, nothing reached Etsy.
                return {"ok": False, "safe_to_retry": True, "error": "Etsy has no draft from this listing. List again."}
        remote = await _read(shop_id, listing_id(candidate))
        if binding["action"] in {"publish", "update"}:
            if remote.get("state") == "active" and _matches(remote, snapshot):
                return _success(remote)
            if binding["action"] == "publish" and remote.get("state") == "draft":
                # Activation is one state change: still a draft means it did not happen.
                return {"ok": False, "safe_to_retry": True, "draft_url": candidate,
                        "error": "Etsy kept this listing as a draft. List again to publish it."}
            raise ValueError("Etsy has not confirmed the update. Check the live listing; do not repeat it.")
        if remote.get("state") != "draft":
            raise ValueError("The Etsy item is no longer a private draft. Check the native listing.")
        expected = binding.get("etsy_photo_ids")
        complete = bool(remote.get("images")) and (not expected or len(remote["images"]) == len(expected))
        if _matches(remote, snapshot) and complete:
            return _success(remote)
        if not binding.get("etsy_pending_write") or found:
            # Found by title after a lost create: nothing after the create can have run.
            return {"ok": False, "safe_to_retry": True, "draft_url": candidate,
                    "error": "This Etsy listing is incomplete. List again to finish the same one."}
        raise ValueError("Etsy's draft write is unconfirmed. Review its fields and photos before another attempt.")
    except Exception as exc:
        return _failure(exc, safe=False, **({"draft_url": candidate} if candidate else {}))
