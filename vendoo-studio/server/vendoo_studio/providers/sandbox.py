"""The AI provider sandbox mode uses: the same answers every time, no network.

It reads no photos and calls no model. Photo analysis returns fixed evidence,
and chat returns a fixed listing when the prompt asks for JSON, otherwise a
short reply. That is enough to drive generation, chat and the editor end to
end; it says nothing about the quality of a real model's listing.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

LISTING = {
    "title": "Sandbox Levi's M Blue Cotton Crew Neck T-Shirt",
    "description": "Blue cotton crew neck T-shirt in good used condition. Soft jersey knit, regular fit.",
    "price": 25,
    "brand": "Levi's",
    "size": "M",
    "condition": "Good",
    "category_path": "Clothing, Shoes & Accessories > Men > Men's Clothing > Shirts > T-Shirts",
    "primaryColor": "Blue",
    "quantity": 1,
    "ebay_specifics": {
        "type": "T-Shirt",
        "department": "Men",
        "sizeType": "Regular",
        "size": "M",
        "brand": "Levi's",
        "fit": "Regular",
        "material": "Cotton",
        "pattern": "Solid",
        "neckline": "Crew Neck",
        "sleeveLength": "Short Sleeve",
    },
}

EVIDENCE = {
    "brand": {"value": "Levi's", "source": "tag"},
    "size": {"value": "M", "source": "tag"},
    "material": {"value": "Cotton", "source": "tag"},
    "color": {"value": "Blue", "source": "photo"},
    "garment": {"value": "t-shirt", "source": "photo"},
}

REPLY = "Sandbox reply: no model was called."


def _prompt_text(messages) -> str:
    parts: list[str] = []
    for message in messages or []:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            parts.extend(str(item.get("text") or "") for item in content if isinstance(item, dict))
    return "\n".join(parts)


class SandboxProvider:
    name = "sandbox"
    vision_model = "sandbox"
    listing_model = "sandbox"

    async def analyze_photos(self, photo_paths, notes="", listing_rules="", **_kwargs) -> dict:
        return {"evidence": json.loads(json.dumps(EVIDENCE))}

    async def chat(self, messages, stream=True) -> AsyncIterator[str]:
        if "json" in _prompt_text(messages).lower():
            yield "```json\n" + json.dumps(LISTING, indent=2) + "\n```"
        else:
            yield REPLY

    async def test_connection(self) -> bool:
        return True
