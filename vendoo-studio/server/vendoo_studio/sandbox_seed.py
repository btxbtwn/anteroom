"""Fill a running sandbox with sample listings through the API.

    python -m vendoo_studio.sandbox_seed http://127.0.0.1:4319

Goes through the same routes the UI uses, so a seeded sandbox looks like one a
seller built by hand. Photos are drawn on the fly; no image files are kept in
the repo. Refuses to run against anything but a sandbox.
"""

from __future__ import annotations

import io
import sys
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from PIL import Image, ImageDraw

SAMPLES = (
    {
        "title": "Sandbox blue tee",
        "color": (40, 90, 170),
        "listing": {
            "title": "Levi's M Blue Cotton Crew Neck T-Shirt",
            "description": "Blue cotton crew neck T-shirt in good used condition.",
            "price": 25,
            "brand": "Levi's",
            "size": "M",
            "condition": "Good",
            "category_path": "Clothing, Shoes & Accessories > Men > Men's Clothing > Shirts > T-Shirts",
            "primaryColor": "Blue",
            "quantity": 1,
        },
    },
    {
        "title": "Sandbox black jeans",
        "color": (30, 30, 30),
        "listing": {
            "title": "Madewell 28 Black High Rise Skinny Jeans",
            "description": "Black high rise skinny jeans. Light wear at the hem.",
            "price": 38,
            "brand": "Madewell",
            "size": "28",
            "condition": "Good",
            "category_path": "Clothing, Shoes & Accessories > Women > Women's Clothing > Jeans",
            "primaryColor": "Black",
            "quantity": 1,
        },
    },
    {
        "title": "Sandbox floral dress",
        "color": (200, 90, 120),
        "listing": {
            "title": "Free People S Pink Floral Midi Dress",
            "description": "Pink floral midi dress with smocked bodice.",
            "price": 54,
            "brand": "Free People",
            "size": "S",
            "condition": "Like New",
            "category_path": "Clothing, Shoes & Accessories > Women > Women's Clothing > Dresses",
            "primaryColor": "Pink",
            "quantity": 1,
        },
    },
    {
        "title": "Sandbox photos only",
        "color": (90, 150, 80),
        "listing": None,
    },
)

# Sample listings live on marketplaces, by sample title: (marketplace, public
# link, the price that marketplace has). The sandbox never publishes, so each is
# marked listed the way a seller links one by hand, and the price a confirmed
# List would have recorded is written beside it. None leaves the price unknown.
LIVE = {
    "Sandbox blue tee": (
        ("ebay", "https://www.ebay.com/itm/256400000001", 25),
        ("poshmark", "https://poshmark.com/listing/6adc371572e62463d992ff01", 30),
        ("mercari", "https://www.mercari.com/us/item/m10000000001/", None),
    ),
    "Sandbox black jeans": (
        ("ebay", "https://www.ebay.com/itm/256400000002", 38),
        ("depop", "https://www.depop.com/products/sandbox-black-jeans/", 34),
    ),
    "Sandbox floral dress": (
        ("poshmark", "https://poshmark.com/listing/6adc371572e62463d992ff03", 62),
        ("etsy", "https://www.etsy.com/listing/1700000003", 54),
    ),
}


def _seed_live(client: httpx.Client, conv_id: str, title: str) -> None:
    from sqlalchemy import update

    from vendoo_studio.database import engine
    from vendoo_studio.models.marketplace_listing import MarketplaceListing

    for marketplace, url, price in LIVE.get(title, ()):
        client.post(f"/api/conversations/{conv_id}/marketplace-listings/{marketplace}/mark",
                    json={"state": "listed", "url": url}).raise_for_status()
        if price is not None:
            with engine.begin() as connection:
                connection.execute(update(MarketplaceListing).where(
                    MarketplaceListing.conversation_id == conv_id, MarketplaceListing.marketplace == marketplace,
                ).values(listed_price=price))


# Sample automations, so the Automations page opens on a populated list. Saving
# one only schedules it; delists and relists still wait for approval per item.
AUTOMATIONS = (
    {
        "name": "Refresh stale listings",
        "timezone": "America/Chicago",
        "schedule": {"kind": "daily", "time": "09:00"},
        "filters": {"min_age_days": 60},
        "actions": [{"type": "price", "mode": "percent", "value": 10, "floor": 15, "repeat": "step"}, {"type": "relist"}],
        "marketplaces": ["ebay", "poshmark"],
        "activate": True,
    },
    {
        "name": "Weekend dress markdown",
        "timezone": "America/Chicago",
        "schedule": {"kind": "weekly", "time": "18:00", "weekdays": [4]},
        "filters": {"category": "Dresses"},
        "actions": [{"type": "price", "mode": "amount", "value": 5, "floor": 30, "repeat": "once"}],
        "marketplaces": ["mercari"],
        "activate": False,
    },
    {
        "name": "Refresh old drafts",
        "timezone": "America/Chicago",
        "schedule": {"kind": "weekly", "time": "08:00", "weekdays": [0]},
        "filters": {"status": "draft", "min_age_days": 30},
        "actions": [{"type": "regenerate"}],
        "marketplaces": [],
        "activate": True,
    },
)

# Sharing and offers to likers are always saved off; only the seller turns them on.
PROMOTION_AUTOMATIONS = (
    {"marketplace": "poshmark", "action": "share", "name": "Evening closet share", "scope": "all",
     "times": ["09:00", "20:00"], "timezone": "America/Chicago", "daily_cap": 300},
    {"marketplace": "poshmark", "action": "offer", "name": "Likers get 10% off", "scope": "all",
     "discount_percent": 10, "min_likes": 1},
)


def _seed_automations(client: httpx.Client) -> None:
    for sample in AUTOMATIONS:
        client.post("/api/automations", json=sample).raise_for_status()
    for sample in PROMOTION_AUTOMATIONS:
        saved = client.post("/api/promotion-automations", json=sample).raise_for_status().json()
        from vendoo_studio import sandbox
        if sample["action"] == "offer" and sandbox.enabled():
            _seed_offer_activity(saved["id"])


def _seed_offer_activity(automation_id: str) -> None:
    """Sample terminal history only; never enable a rule or contact a marketplace."""
    from sqlalchemy.orm import Session

    from vendoo_studio import sandbox
    from vendoo_studio.database import engine
    from vendoo_studio.models.listing_promotion import ListingPromotion
    from vendoo_studio.models.promotion_automation import PromotionAutomation, PromotionRun

    if not sandbox.enabled():
        raise RuntimeError("Offer activity fixtures require sandbox mode")
    with Session(engine) as db:
        rule = db.get(PromotionAutomation, automation_id)
        if rule is None or rule.enabled or rule.marketplace != "poshmark" or rule.action != "offer":
            raise RuntimeError("Offer activity fixtures require a paused Poshmark offer rule")
        now = datetime.now(UTC).replace(tzinfo=None)
        batch_id = str(uuid.uuid4())
        for index, confirmation in enumerate(("request_accepted", "legacy_unverified")):
            at = now - timedelta(minutes=index + 1)
            db.add(ListingPromotion(
                user_id=rule.user_id, automation_id=rule.id, batch_id=batch_id,
                marketplace="poshmark", action="offer", listing_id=f"sandbox-offer-{index + 1}",
                title=f"Sample offer {index + 1}", state="done", confirmation=confirmation,
                recipient_count=None, offer_price_cents=2700, created_at=at, sent_at=at,
                finished_at=at, expires_at=at + timedelta(days=1),
                note="Sample history for layout review. No offer was sent.",
            ))
        db.add(PromotionRun(user_id=rule.user_id, automation_id=rule.id, started_at=now,
                            finished_at=now, sent=2, skipped=0, failed=0,
                            note="Sample completed actions; buyer delivery is unverified."))
        db.commit()


# Items that already sold, so Analytics has figures and charts to show:
# (title, color, marketplace, days ago, sale price, fees, cost of goods).
SOLD = (
    ("Patagonia L Gray Fleece Jacket", (110, 110, 120), "ebay", 2, "68", "8.84", "12"),
    ("Coach Brown Leather Crossbody Bag", (120, 80, 50), "poshmark", 4, "95", "19", "20"),
    ("Nike 10 White Air Force 1 Sneakers", (220, 220, 220), "mercari", 9, "72", "7.2", "25"),
    ("J.Crew M Navy Wool Sweater", (30, 40, 90), "ebay", 12, "34", "4.42", "6"),
    ("Lululemon 6 Black Align Leggings", (20, 20, 20), "poshmark", 16, "48", "9.6", "10"),
    ("Carhartt L Brown Canvas Work Jacket", (140, 100, 40), "depop", 19, "85", "8.5", "18"),
    ("Anthropologie S Green Linen Dress", (70, 120, 80), "poshmark", 23, "42", "8.4", "8"),
    ("Ray-Ban Black Wayfarer Sunglasses", (40, 40, 40), "ebay", 27, "58", "7.54", None),
    ("Levi's 32 Blue 501 Jeans", (50, 80, 140), "mercari", 33, "39", "3.9", "7"),
    ("Madewell M Cream Cable Knit Cardigan", (230, 220, 200), "ebay", 38, "44", "5.72", "9"),
    ("Free People S Blue Peasant Top", (90, 130, 190), "depop", 45, "29", "2.9", "5"),
    ("Columbia XL Red Rain Jacket", (170, 40, 40), "ebay", 52, "36", "4.68", "8"),
)


def _sold_sale(marketplace: str, days_ago: int, price: str, fees: str, cost: str | None) -> dict:
    return {
        "marketplace": marketplace, "currency": "USD", "item_revenue": price, "fees": fees,
        "shipping_credit": None, "shipping_cost": None, "item_cost": cost,
        "sold_on": (datetime.now(UTC).date() - timedelta(days=days_ago)).isoformat(),
        "request_id": uuid.uuid4().hex,
    }


def _photo(label: str, color: tuple[int, int, int], index: int) -> bytes:
    image = Image.new("RGB", (800, 1000), color)
    draw = ImageDraw.Draw(image)
    draw.rectangle((60, 60, 740, 940), outline=(255, 255, 255), width=8)
    draw.text((100, 100), f"{label}\nphoto {index}", fill=(255, 255, 255))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=80)
    return buffer.getvalue()


def seed(base_url: str) -> list[str]:
    with httpx.Client(base_url=base_url, timeout=30) as client:
        status = client.get("/api/status").raise_for_status().json()
        if not status.get("sandbox"):
            raise SystemExit(f"{base_url} is not a sandbox (ANTEROOM_SANDBOX=1); refusing to seed it.")
        created = []
        for sample in SAMPLES:
            conv = client.post("/api/conversations", json={"title": sample["title"]}).raise_for_status().json()
            files = [
                ("files", (f"photo-{i}.jpg", _photo(sample["title"], sample["color"], i), "image/jpeg"))
                for i in (1, 2)
            ]
            client.post(f"/api/conversations/{conv['id']}/photos", files=files).raise_for_status()
            if sample["listing"]:
                client.put(
                    f"/api/conversations/{conv['id']}/listing",
                    json={"listing": sample["listing"], "expected_revision_id": ""},
                ).raise_for_status()
            _seed_live(client, conv["id"], sample["title"])
            created.append(conv["id"])
        for title, color, marketplace, days_ago, price, fees, cost in SOLD:
            conv = client.post("/api/conversations", json={"title": title}).raise_for_status().json()
            client.post(f"/api/conversations/{conv['id']}/photos",
                        files=[("files", ("photo-1.jpg", _photo(title, color, 1), "image/jpeg"))]).raise_for_status()
            client.put(f"/api/conversations/{conv['id']}/listing", json={
                "listing": {"title": title, "price": float(price), "quantity": 1}, "expected_revision_id": "",
            }).raise_for_status()
            client.post(f"/api/conversations/{conv['id']}/marketplace-listings/sold",
                        json=_sold_sale(marketplace, days_ago, price, fees, cost)).raise_for_status()
            created.append(conv["id"])
        _seed_automations(client)
        return created


def seed_hosted(base_url: str) -> list[str]:
    """Build sample drafts through the same hosted routes as the web/iPhone."""
    with httpx.Client(base_url=base_url, timeout=30) as client:
        if client.get('/api/health').raise_for_status().json().get('sandbox') is not True:
            raise RuntimeError('Refusing to seed a server that is not a local sandbox')
        session = client.post('/local/auth/v1/token?grant_type=password', json={
            'email': 'alice@sandbox.test', 'password': 'sandbox',
        }).raise_for_status().json()
        client.headers['Authorization'] = 'Bearer ' + session['access_token']
        created = []
        for sample in SAMPLES:
            listing = sample['listing'] or {}
            draft = client.post('/api/drafts', json={
                'title': listing.get('title') or sample['title'],
            }).raise_for_status().json()
            client.post(f"/api/drafts/{draft['id']}/photos", content=_photo(sample['title'], sample['color'], 1),
                        headers={'Content-Type': 'image/jpeg'}).raise_for_status()
            if listing:
                client.patch(f"/api/drafts/{draft['id']}", json={
                    'title': listing['title'], 'notes': '', 'expected_updated_at': draft['updated_at'],
                    'fields': {key: value for key, value in listing.items() if key != 'title'},
                }).raise_for_status()
            _seed_live(client, draft['id'], sample['title'])
            created.append(draft['id'])
        for title, color, marketplace, days_ago, price, fees, cost in SOLD:
            draft = client.post('/api/drafts', json={'title': title}).raise_for_status().json()
            client.post(f"/api/drafts/{draft['id']}/photos", content=_photo(title, color, 1),
                        headers={'Content-Type': 'image/jpeg'}).raise_for_status()
            client.post(f"/api/conversations/{draft['id']}/marketplace-listings/sold",
                        json=_sold_sale(marketplace, days_ago, price, fees, cost)).raise_for_status()
            created.append(draft['id'])
        _seed_automations(client)
        return created


def main() -> None:
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:4319"
    ids = seed(base_url)
    print(f"Seeded {len(ids)} sandbox listings at {base_url}")


if __name__ == "__main__":
    main()
