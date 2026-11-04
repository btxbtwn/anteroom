"""One eBay listing with variations: sizes, colors and so on, each with its own stock, SKU and price.

eBay's Inventory API models it as one inventory item per variation, an
inventory item group that names them and the aspects they vary by, and one
fixed-price offer per variation (``createOrReplaceInventoryItemGroup``,
``publishByInventoryItemGroup``). Save Draft writes all of that and stops: the
offers stay unpublished, so buyers see nothing. List publishes the same group,
which eBay turns into one listing ID. Every write is a replace keyed by SKU or
group key, so repeating one after a lost reply finishes the same listing.

The seller's rows live in ``ebay_specifics.variations``::

    {"aspects": ["Size", "Color"], "photosBy": "Color",
     "rows": [{"values": {"Size": "M", "Color": "Red"}, "quantity": 2, "sku": "", "price": ""}],
     "photos": {"Red": ["<photo id>", ...]}}

A blank row SKU gets a stable one from the item's SKU and the row's values; a
blank row price uses the listing price.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Callable

from vendoo_studio.services.ebay_api import dollars, money_cents
from vendoo_studio.services.ebay_client import EbayClient, EbayFailure, segment
from vendoo_studio.services.listing_result import missing

FIELD = "ebay_specifics.variations"
MAX_SKU_LENGTH = 50
_SKU = re.compile(r"[A-Za-z0-9_-]{1,50}")


def raw(snapshot: dict) -> dict:
    details = snapshot.get("ebay_specifics")
    if not isinstance(details, dict):
        details = {}
    value = details.get("variations")
    return value if isinstance(value, dict) else {}


def has_variations(snapshot: dict) -> bool:
    rows = raw(snapshot).get("rows")
    return isinstance(rows, list) and len(rows) > 0


def _text(value) -> str:
    return " ".join(str(value if value is not None else "").split())


def _quantity(value) -> int | None:
    text = str(value if value is not None else "").strip()
    return int(text) if re.fullmatch(r"\d+", text) else None


def plan(snapshot: dict, listing_price: str) -> dict:
    """Serialize the seller's variations; eBay decides which field values it accepts."""
    data = raw(snapshot)
    names = [_text(name) for name in data.get("aspects") or [] if _text(name)]
    rows = data.get("rows")
    if not isinstance(rows, list):
        rows = []
    out_rows: list[dict] = []
    for row in rows:
        row = row if isinstance(row, dict) else {}
        given = row.get("values")
        if not isinstance(given, dict):
            given = {}
        folded = {_text(key).casefold(): _text(value) for key, value in given.items()}
        values = {name: folded.get(name.casefold(), "") for name in names}
        quantity = _quantity(row.get("quantity"))
        sku = _text(row.get("sku"))
        price_text = _text(row.get("price")).replace("$", "").replace(",", "")
        price = listing_price
        if price_text:
            try:
                price = dollars(money_cents(price_text))
            except (ValueError, ArithmeticError):
                price = price_text
        out_rows.append({"values": values, "quantity": quantity or 0, "sku": sku, "price": price})
    photos_by = _text(data.get("photosBy"))
    photos_by = next((name for name in names if name.casefold() == photos_by.casefold()), photos_by or (names[0] if names else ""))
    chosen_photos = data.get("photos")
    if not isinstance(chosen_photos, dict):
        chosen_photos = {}
    photos: dict[str, list[str]] = {}
    for value, ids in chosen_photos.items():
        ids = [str(item) for item in ids or [] if str(item or "").strip()] if isinstance(ids, list) else []
        if ids:
            photos[_text(value)] = list(dict.fromkeys(ids))
    return {"aspects": names, "photos_by": photos_by, "rows": out_rows, "photos": photos}


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", text)


def variant_sku(base: str, values: dict[str, str]) -> str:
    """A stable SKU for a row the seller left without one: the item's key and the row's values.

    Lowercased and in aspect-name order, so retyping "m" as "M" or reordering
    the aspects keeps the same variation on eBay.
    """
    ordered = [values[name].casefold() for name in sorted(values, key=str.casefold)]
    sku = f"{base}-" + "-".join(_slug(value) or "x" for value in ordered)
    if len(sku) <= MAX_SKU_LENGTH and _SKU.fullmatch(sku):
        return sku
    digest = hashlib.sha256("\x1f".join(ordered).encode()).hexdigest()[:10]
    return f"{base[:MAX_SKU_LENGTH - 11]}-{digest}"


def assign_skus(chosen: dict, base: str) -> dict:
    """Fill each row's SKU. A generated SKU that clashes with a typed one is refused like any duplicate."""
    rows = [{**row, "sku": row["sku"] or variant_sku(base, row["values"])} for row in chosen["rows"]]
    seen: dict[str, int] = {}
    for index, row in enumerate(rows):
        if row["sku"].casefold() in seen:
            raise missing(f"Two eBay variations would share SKU {row['sku']}. Give one of them its own SKU.",
                          f"{FIELD}.rows.{index}.sku")
        seen[row["sku"].casefold()] = index
    if any(row["sku"].casefold() == base.casefold() for row in rows):
        raise missing("A variation SKU must differ from the item's SKU.", f"{FIELD}.rows.0.sku")
    return {**chosen, "rows": rows}


async def check_category(client: EbayClient, category_id: str, chosen: dict) -> dict:
    """Use known catalog spellings without refusing values absent from our catalog."""
    from vendoo_studio.services import marketplace_schemas
    from vendoo_studio.services.ebay_listings import _aspect_key, _choice

    schema = await marketplace_schemas.ebay_aspects(client, category_id)
    by_key = {_aspect_key(row["name"]): row for row in schema}
    names = {name: (by_key.get(_aspect_key(name)) or {"name": name})["name"] for name in chosen["aspects"]}
    rows = []
    for row in chosen["rows"]:
        values = {}
        for name, value in row["values"].items():
            field = by_key.get(_aspect_key(name)) or {"name": name, "mode": "free_text"}
            labels = [entry["label"] for entry in field.get("values") or []]
            if field["mode"] == "selection":
                matched = _choice(value, labels)
                value = matched or value
            elif labels:
                value = _choice(value, labels, near=False) or value
            values[names[name]] = value
        rows.append({**row, "values": values})
    photos_by = names.get(chosen["photos_by"], chosen["photos_by"])
    photos = {}
    for value, ids in chosen["photos"].items():
        spelled = next((row["values"][photos_by] for row in rows if row["values"].get(photos_by, "").casefold() == value.casefold()
                        or _aspect_key(row["values"].get(photos_by, "")) == _aspect_key(value)), None)
        photos[spelled or value] = ids
    return {**chosen, "aspects": [names[name] for name in chosen["aspects"]], "photos_by": photos_by, "rows": rows, "photos": photos}


def common_aspects(aspects: dict[str, list[str]], chosen: dict) -> dict[str, list[str]]:
    """The listing's shared item specifics, without the aspects the variations differ by."""
    from vendoo_studio.services.ebay_listings import _aspect_key

    varied = {_aspect_key(name) for name in chosen["aspects"]}
    return {name: values for name, values in aspects.items() if _aspect_key(name) not in varied}


def with_first_values(aspects: dict[str, list[str]], chosen: dict) -> dict[str, list[str]]:
    """Aspects with each varied one answered by the first row, so a required aspect is not reported missing."""
    shared = common_aspects(aspects, chosen)
    return {**shared, **{name: [chosen["rows"][0]["values"][name]] for name in chosen["aspects"]}}


def specifications(chosen: dict) -> list[dict]:
    return [{"name": name, "values": list(dict.fromkeys(row["values"][name] for row in chosen["rows"]))}
            for name in chosen["aspects"]]


def group_body(fields: dict, chosen: dict, shared: dict[str, list[str]], image_urls: list[str]) -> dict:
    """``createOrReplaceInventoryItemGroup``: the listing's title, description, photos and shared specifics."""
    from vendoo_studio.services.ebay_listings import description_html

    return {
        "title": fields["title"],
        "description": description_html(fields["description"]),
        "imageUrls": image_urls,
        "aspects": shared,
        "variantSKUs": [row["sku"] for row in chosen["rows"]],
        "variesBy": {"aspectsImageVariesBy": [chosen["photos_by"]], "specifications": specifications(chosen)},
    }


def item_body(base: dict, row: dict, shared: dict[str, list[str]], image_urls: list[str]) -> dict:
    """One variation's inventory item. Title and description stay on the group, as eBay asks."""
    product = {key: value for key, value in base["product"].items() if key not in {"title", "description", "imageUrls"}}
    product["aspects"] = {**shared, **{name: [value] for name, value in row["values"].items()}}
    if image_urls:
        product["imageUrls"] = image_urls
    return {**base, "product": product, "availability": {"shipToLocationAvailability": {"quantity": row["quantity"]}}}


def offer_body(row: dict, fields: dict, location: str | None, policies: dict) -> dict:
    """One variation's fixed-price offer. No description (the group's is the listing's) and no Best Offer."""
    from vendoo_studio.services.ebay_listings import _offer_body

    body = _offer_body(row["sku"], {**fields, "price": row["price"], "quantity": row["quantity"]}, location,
                       {key: value for key, value in policies.items() if key != "bestOfferTerms"})
    body.pop("listingDescription", None)
    return body


def row_fields(fields: dict, row: dict) -> dict:
    return {**fields, "price": row["price"], "quantity": row["quantity"]}


def group_differences(saved: dict, fields: dict, chosen: dict) -> list[str]:
    """What eBay's saved group holds differently from the rows sent."""
    def specs(entries) -> list:
        return sorted((str(entry.get("name")), sorted(str(value) for value in entry.get("values") or []))
                      for entry in entries or [] if isinstance(entry, dict))

    varies = saved.get("variesBy")
    if not isinstance(varies, dict):
        varies = {}
    checks = {
        "variations": sorted(saved.get("variantSKUs") or []) == sorted(row["sku"] for row in chosen["rows"]),
        "variation aspects": specs(varies.get("specifications")) == specs(specifications(chosen)),
        "title": saved.get("title") in (None, fields["title"]),
    }
    return [name for name, same in checks.items() if not same]


def _photo_urls(chosen: dict, photo_urls: dict[str, str], row: dict) -> list[str]:
    ids = chosen["photos"].get(row["values"].get(chosen["photos_by"], ""), [])
    return [photo_urls[photo_id] for photo_id in ids if photo_id in photo_urls]


def check_photo_ids(chosen: dict, photos: list) -> None:
    known = {photo.id for photo in photos}
    for value, ids in chosen["photos"].items():
        if any(photo_id not in known for photo_id in ids):
            raise missing(f"A photo chosen for {value} is not among the eBay form's photos. Tick it there or choose its photos again.",
                          f"{FIELD}.photos")


async def _offers(client: EbayClient, chosen: dict, binding: dict) -> dict[str, dict]:
    """eBay's offer for each row's SKU, where there is one: the saved ID, or looked up by SKU."""
    from vendoo_studio.services.ebay_listings import FIXED_PRICE, _offer_for

    known = binding.get("ebay_variant_offers")
    if not isinstance(known, dict):
        known = {}
    found = {}
    for row in chosen["rows"]:
        offer = await _offer_for(client, row["sku"], {"ebay_offer_id": known.get(row["sku"]),
                                                       "ebay_inventory_written": binding.get("ebay_inventory_written")}, FIXED_PRICE)
        if offer and offer.get("offerId"):
            found[row["sku"]] = offer
    return found


def _live_listing_id(offers: dict[str, dict]) -> str:
    for offer in offers.values():
        listing_id = str((offer.get("listing") or {}).get("listingId") or "")
        if offer.get("status") == "PUBLISHED" and listing_id:
            return listing_id
    return ""


async def _not_ours(client: EbayClient, chosen: dict, group_key: str, generated: bool) -> None:
    """Before this item first writes to eBay: a SKU or group key eBay already has belongs to something else."""
    from vendoo_studio.services.ebay_listings import _not_found

    if not generated and await _not_found(client.request("GET", f"/sell/inventory/v1/inventory_item_group/{segment(group_key)}")) is not None:
        raise missing(f"eBay already has a variation group named {group_key}. Give this item its own SKU, then try again.", "sku")
    for index, row in enumerate(chosen["rows"]):
        if await _not_found(client.request("GET", f"/sell/inventory/v1/inventory_item/{segment(row['sku'])}")) is not None:
            raise missing(f"eBay already has an item with SKU {row['sku']}. Give this variation its own SKU, then try again.",
                          f"{FIELD}.rows.{index}.sku")


async def _verify_draft(client: EbayClient, group_key: str, fields: dict, chosen: dict, offer_ids: dict[str, str]) -> None:
    saved = await client.request("GET", f"/sell/inventory/v1/inventory_item_group/{segment(group_key)}")
    differences = group_differences(saved, fields, chosen)
    from vendoo_studio.services.ebay_listings import offer_differences

    for row in chosen["rows"]:
        offer = await client.request("GET", f"/sell/inventory/v1/offer/{segment(offer_ids[row['sku']])}")
        if offer.get("status") == "PUBLISHED":
            raise ValueError("An offer in this eBay draft is live. Check eBay before saving again.")
        differences += [f"{name} ({', '.join(row['values'].values())})" for name in offer_differences(offer, row["sku"], row_fields(fields, row))]
    if differences:
        raise ValueError(f"eBay saved different values for: {', '.join(dict.fromkeys(differences))}. Check the item before listing.")


async def execute(action: str, client: EbayClient, snapshot: dict, form: dict, fields: dict, photos: list, binding: dict,
                  record: Callable[[dict], None], active: Callable[[], bool], conversation_id: str,
                  pending: dict, aspects: dict[str, list[str]]) -> dict:
    """Save Draft or List for a listing with variations. ``pending`` names the unrepeatable write in flight."""
    from vendoo_studio.services import ebay_listings as single

    chosen = fields["variations"]
    check_photo_ids(chosen, photos)
    base = single.ebay_sku(snapshot, binding, conversation_id)
    group_key = str(binding.get("ebay_group_key") or base)
    choices = await single.listing_choices(client, snapshot)
    if not binding.get("ebay_inventory_written"):
        await _not_ours(client, chosen, group_key, base == single.item_key(conversation_id))
    previous = dict(binding.get("ebay_variant_offers") or {})
    offers = await _offers(client, chosen, binding)
    known = {sku: str(offer["offerId"]) for sku, offer in offers.items()}
    if known:
        record({"ebay_sku": base, "ebay_group_key": group_key, "ebay_variant_offers": known})
    live = _live_listing_id(offers)
    if live:
        record({"ebay_listing_id": live})
        return {"ok": True, "state": "published", "listing_url": single._listing_url(live)}
    image_urls = await single._image_urls(client, photos, binding, record, active)
    by_photo = dict(zip([photo.id for photo in photos], image_urls, strict=True))
    main = image_urls[:single.MAX_PHOTOS]
    shared = common_aspects(aspects, chosen)
    record({"ebay_sku": base, "ebay_group_key": group_key, "ebay_inventory_written": True})
    template = single._inventory_body({**fields, "quantity": 0}, {}, [], snapshot, form)
    for row in chosen["rows"]:
        if not active():
            raise ValueError("The eBay listing stopped before it finished. Try again to continue.")
        await client.request("PUT", f"/sell/inventory/v1/inventory_item/{segment(row['sku'])}",
                             body=item_body(template, row, shared, _photo_urls(chosen, by_photo, row)))
    await client.request("PUT", f"/sell/inventory/v1/inventory_item_group/{segment(group_key)}",
                         body=group_body(fields, chosen, shared, main))
    policies = {"fulfillmentPolicyId": choices["fulfillmentPolicyId"], "paymentPolicyId": choices["paymentPolicyId"],
                "returnPolicyId": choices["returnPolicyId"]}
    for row in chosen["rows"]:
        body = offer_body(row, fields, choices["merchantLocationKey"], policies)
        if row["sku"] in known:
            await client.request("PUT", f"/sell/inventory/v1/offer/{segment(known[row['sku']])}", body=body)
            continue
        pending["write"] = "offer"
        created = await client.request("POST", "/sell/inventory/v1/offer", body=body)
        offer_id = str(created.get("offerId") or "")
        if not offer_id:
            raise EbayFailure("eBay did not confirm a variation's offer. Check eBay before trying again.")
        known[row["sku"]] = offer_id
        record({"ebay_variant_offers": dict(known)})
        pending["write"] = None
    await _drop_removed(client, previous, chosen, known, record)
    if action == "save_draft":
        await _verify_draft(client, group_key, fields, chosen, known)
        return {"ok": True, "state": "draft"}
    if not active():
        raise ValueError("The eBay listing stopped before it was published. List again to publish it.")
    pending["write"] = "publish"
    published = await client.request("POST", "/sell/inventory/v1/offer/publish_by_inventory_item_group",
                                     body={"inventoryItemGroupKey": group_key, "marketplaceId": "EBAY_US"})
    listing_id = str(published.get("listingId") or "")
    url = single._listing_url(listing_id)
    record({"ebay_listing_id": listing_id})
    return {"ok": True, "state": "published", "listing_url": url}


async def _drop_removed(client: EbayClient, previous: dict, chosen: dict, current: dict[str, str],
                        record: Callable[[dict], None]) -> None:
    """Delete the unpublished offers of rows the seller removed, so a later List cannot publish them."""
    from vendoo_studio.services.ebay_listings import _not_found

    keep = {row["sku"] for row in chosen["rows"]}
    for sku, offer_id in previous.items():
        if sku in keep:
            continue
        offer = await _not_found(client.request("GET", f"/sell/inventory/v1/offer/{segment(str(offer_id))}"))
        if offer is not None and offer.get("status") != "PUBLISHED":
            await client.request("DELETE", f"/sell/inventory/v1/offer/{segment(str(offer_id))}")
    record({"ebay_variant_offers": dict(current)})


async def update_live(client: EbayClient, snapshot: dict, form: dict, fields: dict, photos: list, binding: dict,
                      record: Callable[[dict], None], active: Callable[[], bool], aspects: dict[str, list[str]]) -> dict:
    """Replace the live listing's group, variations and offers. Never publishes and never adds a listing."""
    from vendoo_studio.services import ebay_listings as single

    listing_id = str(binding.get("listing_id") or binding.get("ebay_listing_id") or "")
    group_key = str(binding.get("ebay_group_key") or "")
    if not group_key or not re.fullmatch(r"\d{6,20}", listing_id):
        raise ValueError("This eBay listing has no variations Anteroom made. End it on eBay and list it again to add variations.")
    chosen = fields["variations"]
    check_photo_ids(chosen, photos)
    offers = await _offers(client, chosen, binding)
    for index, row in enumerate(chosen["rows"]):
        offer = offers.get(row["sku"])
        if not offer:
            raise missing("Adding a variation to a live eBay listing is not supported yet. Remove the new row, "
                          "or end the listing and list it again.", f"{FIELD}.rows.{index}")
        if offer.get("status") != "PUBLISHED" or str((offer.get("listing") or {}).get("listingId") or "") != listing_id:
            raise ValueError("A variation's eBay offer is not on the live listing. Check eBay before updating it.")
    image_urls = await single._image_urls(client, photos, binding, record, active)
    by_photo = dict(zip([photo.id for photo in photos], image_urls, strict=True))
    shared = common_aspects(aspects, chosen)
    template = single._inventory_body({**fields, "quantity": 0}, {}, [], snapshot, form)
    for row in chosen["rows"]:
        if not active():
            raise ValueError("The eBay update stopped. Check its result before trying again.")
        await client.request("PUT", f"/sell/inventory/v1/inventory_item/{segment(row['sku'])}",
                             body=item_body(template, row, shared, _photo_urls(chosen, by_photo, row)))
    # Replacing the group updates the live listing; a row left out is removed from it (eBay refuses that after a sale).
    await client.request("PUT", f"/sell/inventory/v1/inventory_item_group/{segment(group_key)}",
                         body=group_body(fields, chosen, shared, image_urls[:single.MAX_PHOTOS]))
    for row in chosen["rows"]:
        offer = offers[row["sku"]]
        policies = offer.get("listingPolicies")
        if not isinstance(policies, dict):
            policies = {}
        own = single.own_listing_choices(snapshot)
        policies = {**policies, **{key: value for key, value in own.items() if key != "merchantLocationKey"}}
        body = offer_body(row, fields, own.get("merchantLocationKey") or offer.get("merchantLocationKey"), policies)
        body.pop("includeCatalogProductDetails", None)
        await client.request("PUT", f"/sell/inventory/v1/offer/{segment(str(offer['offerId']))}", body=body)
    record({"ebay_variant_offers": {sku: str(offer["offerId"]) for sku, offer in offers.items()}})
    await _confirm_live(client, chosen, offers, listing_id, fields)
    return {"ok": True, "state": "published", "listing_url": single._listing_url(listing_id)}


async def _confirm_live(client: EbayClient, chosen: dict, offers: dict[str, dict], listing_id: str, fields: dict) -> None:
    from vendoo_studio.services.ebay_listings import _same_amount

    for row in chosen["rows"]:
        saved = await client.request("GET", f"/sell/inventory/v1/offer/{segment(str(offers[row['sku']]['offerId']))}")
        same = (saved.get("status") == "PUBLISHED" and str((saved.get("listing") or {}).get("listingId") or "") == listing_id
                and _same_amount((saved.get("pricingSummary") or {}).get("price"), row["price"])
                and str(saved.get("availableQuantity", "")) == str(row["quantity"]))
        if not same:
            raise ValueError("eBay has not confirmed the update on the same listing. Check eBay before trying again.")


async def check(client: EbayClient, binding: dict, snapshot: dict | None, record: Callable[[dict], None]) -> dict:
    """Read what eBay has for this group. Never writes."""
    from vendoo_studio.services import ebay_listings as single
    from vendoo_studio.services.native_form import vendoo_form

    known = binding.get("ebay_variant_offers")
    if not isinstance(known, dict):
        known = {}
    chosen = None
    fields = None
    if snapshot and has_variations(snapshot):
        form = snapshot.get("form")
        if not isinstance(form, dict):
            form = vendoo_form(snapshot, "ebay")
        fields = single._listing_fields(snapshot, form)
        chosen = assign_skus(fields["variations"], str(binding.get("ebay_sku") or ""))
        chosen = await check_category(client, fields["category_id"], chosen)
    rows = chosen["rows"] if chosen else [{"sku": sku} for sku in known]
    offers = await _offers(client, {"rows": rows}, binding) if rows else {}
    if offers:
        record({"ebay_variant_offers": {sku: str(offer["offerId"]) for sku, offer in offers.items()}})
    live = _live_listing_id(offers)
    if binding.get("action") == "update" and chosen and fields:
        listing_id = str(binding.get("listing_id") or binding.get("ebay_listing_id") or "")
        if any(row["sku"] not in offers for row in chosen["rows"]):
            raise ValueError("eBay has not confirmed every variation. Check the live listing before trying again.")
        await _confirm_live(client, chosen, offers, listing_id, fields)
        return {"ok": True, "state": "published", "listing_url": single._listing_url(listing_id)}
    if live:
        record({"ebay_listing_id": live})
        return {"ok": True, "state": "published", "listing_url": single._listing_url(live)}
    if binding.get("action") == "save_draft" and chosen and fields and len(offers) == len(chosen["rows"]):
        await _verify_draft(client, str(binding.get("ebay_group_key") or ""), fields, chosen,
                            {sku: str(offer["offerId"]) for sku, offer in offers.items()})
        return {"ok": True, "state": "draft"}
    if offers:
        return single.resumable("eBay has this listing's variations saved but not live. List again to publish it.")
    return single.resumable("eBay has no listing for this item. List again.")


async def end_group(client: EbayClient, group_key: str, offer_ids: list[str]) -> dict:
    """End the whole listing (``withdrawOfferByInventoryItemGroup``). Its offers stay, unpublished."""
    live = False
    for offer_id in offer_ids:
        offer = await client.request("GET", f"/sell/inventory/v1/offer/{segment(offer_id)}")
        live = live or offer.get("status") == "PUBLISHED"
    if offer_ids and not live:
        return {"ok": True, "state": "deleted", "already": True}
    await client.request("POST", "/sell/inventory/v1/offer/withdraw_by_inventory_item_group",
                         body={"inventoryItemGroupKey": group_key, "marketplaceId": "EBAY_US"})
    return {"ok": True, "state": "deleted"}


def total_quantity(snapshot: dict) -> int | None:
    """Units across the listing's eBay variations, or None when it has none."""
    if not has_variations(snapshot):
        return None
    return sum(_quantity(row.get("quantity")) or 0 for row in raw(snapshot)["rows"] if isinstance(row, dict))
