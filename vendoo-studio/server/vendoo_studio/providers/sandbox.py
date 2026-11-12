"""The AI provider sandbox mode uses: the same answers every time, no network.

It reads no photos and calls no model. Photo analysis returns fixed evidence,
and chat returns a fixed listing when the prompt asks for JSON, otherwise a
short reply. That is enough to drive generation, chat and the editor end to
end; it says nothing about the quality of a real model's listing.

A call whose photo notes or prompt contain ``STALL_MARKER`` never answers, the
way a provider that stopped responding behaves. Tests and agents use it to
check that every long action ends in a named error instead of spinning.

``ANTEROOM_SANDBOX_MODEL_SECONDS`` makes every photo analysis and chat call
wait that many seconds before answering, so a load test
(``scripts/hosted-load-test.py``) holds generations open about as long as a
real model would. It is unset, and calls answer at once, everywhere else.
"""

from __future__ import annotations

import asyncio
import json
import os
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
STALL_MARKER = "[sandbox:stall]"
# Streams a long markdown reply a word at a time, so browser tests can watch a
# reply arrive and stop it part way through.
SLOW_MARKER = "[sandbox:slow]"
SLOW_REPLY = (
    "**Sandbox** is streaming this reply slowly, one word at a time, so a test can watch it arrive. "
    + "Each sentence adds a few more words to the paragraph being written. " * 12
    + "\n\n- A list item arrives\n- Then another one\n\nThe end."
)
SLOW_WORD_SECONDS = 0.05
MODEL_SECONDS_ENV = "ANTEROOM_SANDBOX_MODEL_SECONDS"


async def _model_wait() -> None:
    """Stand in for a model's response time when a load test asks for one."""
    seconds = float(os.environ.get(MODEL_SECONDS_ENV) or 0)
    if seconds > 0:
        await asyncio.sleep(seconds)


async def _stall() -> None:
    """Wait forever, like a provider that stopped answering."""
    await asyncio.Event().wait()


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
        if STALL_MARKER in (notes or ""):
            await _stall()
        await _model_wait()
        return {"evidence": json.loads(json.dumps(EVIDENCE))}

    async def chat(self, messages, stream=True) -> AsyncIterator[str]:
        prompt = _prompt_text(messages)
        if STALL_MARKER in prompt:
            await _stall()
        await _model_wait()
        if SLOW_MARKER in prompt:
            for word in SLOW_REPLY.split(" "):
                await asyncio.sleep(SLOW_WORD_SECONDS)
                yield word + " "
            return
        if "json" in prompt.lower():
            yield "```json\n" + json.dumps(LISTING, indent=2) + "\n```"
        else:
            yield REPLY

    async def test_connection(self) -> bool:
        return True
