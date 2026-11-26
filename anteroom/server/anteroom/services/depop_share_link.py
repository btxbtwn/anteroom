"""Read the public product URL advertised on a Depop app share page."""
from __future__ import annotations

import asyncio
import re
from html.parser import HTMLParser

import httpx

from anteroom.services.marketplace_identity import listing_reference


class _ProductLink(HTMLParser):
    url = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "meta" and attributes.get("property") == "og:url":
            self.url = attributes.get("content") or ""


async def resolve(url: str | None) -> str | None:
    if not url or not re.fullmatch(r"https://depop\.app\.link/[a-zA-Z0-9_-]+", url.strip()):
        return url
    parser = _ProductLink()
    try:
        async with asyncio.timeout(8), httpx.AsyncClient(follow_redirects=False) as client:
            async with client.stream("GET", url.strip()) as response:
                if response.is_redirect:
                    target = response.headers.get("location")
                else:
                    response.raise_for_status()
                    length = 0
                    async for chunk in response.aiter_text():
                        length += len(chunk)
                        if length > 128 * 1024:
                            break
                        parser.feed(chunk)
                        if parser.url:
                            break
                    target = parser.url
    except (httpx.HTTPError, TimeoutError) as exc:
        raise ValueError("Depop could not open this share link. Try again or paste the product page link.") from exc
    reference = listing_reference("depop", target)
    if not reference:
        raise ValueError("This Depop share link did not identify a product. Paste its product page link.")
    return reference[1]
