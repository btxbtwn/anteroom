"""Fill a running sandbox with sample listings through the API.

    python -m vendoo_studio.sandbox_seed http://127.0.0.1:4319

Goes through the same routes the UI uses, so a seeded sandbox looks like one a
seller built by hand. Photos are drawn on the fly; no image files are kept in
the repo. Refuses to run against anything but a sandbox.
"""

from __future__ import annotations

import io
import sys

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
            created.append(conv["id"])
        return created


def main() -> None:
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:4319"
    ids = seed(base_url)
    print(f"Seeded {len(ids)} sandbox listings at {base_url}")


if __name__ == "__main__":
    main()
