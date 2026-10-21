"""Keep the business assistant from handing the seller a web address it made up.

A model asked for a box's link will sometimes build one from the title when
Studio did not give it one. Every address the assistant writes is checked, as
it streams, against the addresses Studio put in front of it this turn: the
brief, the seller's own messages and each lookup result. One that is not among
them is replaced with a note, so the seller never follows a guessed link.

The stream is released in pieces that never split an address or a markdown
link, so each piece can be checked on its own and nothing already shown has to
be taken back.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

URL = r"(?:https?://|www\.)[^\s<>\"'`)\]|]+"
_URL = re.compile(URL, re.IGNORECASE)
_LINK = re.compile(r"\[([^\[\]\n]*)\]\(\s*(" + URL + r")\s*\)", re.IGNORECASE)
_TRAILING = ".,;:!?"
# A tail that may still grow into an address or a markdown link is held back until it is complete.
_OPEN_URL = re.compile(r"(?:https?://|www\.)[^\s<>\"'`)\]|]*$", re.IGNORECASE)
_OPEN_LINK = re.compile(r"\[[^\[\]\n]{0,300}(?:\](?:\([^)\n]{0,2000})?)?$")
_SCHEMES = ("https://", "http://", "www.")
REMOVED = "(link removed: Studio did not return that address)"


def addresses(texts: Iterable[str]) -> set[str]:
    found: set[str] = set()
    for text in texts:
        for match in _URL.finditer(text or ""):
            found.add(_normal(match.group(0).rstrip(_TRAILING)))
    return found


def _normal(url: str) -> str:
    url = url.strip().lower()
    for scheme in _SCHEMES[:2]:
        if url.startswith(scheme):
            url = url[len(scheme):]
    if url.startswith("www."):
        url = url[4:]
    return url.split("#", 1)[0].rstrip("/")


def _page(url: str) -> str:
    return url.split("?", 1)[0].rstrip("/")


def message_texts(messages: list[dict]) -> list[str]:
    """What Studio and the seller wrote in a prompt; earlier assistant replies are not a source."""
    texts = []
    for message in messages:
        if message.get("role") == "assistant":
            continue
        content = message.get("content")
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            texts.extend(str(part.get("text") or "") for part in content if isinstance(part, dict))
    return texts


class LinkGuard:
    """The addresses Studio returned this turn, and a check of everything the assistant writes."""

    def __init__(self, sources: Iterable[str] = ()) -> None:
        self.allowed: set[str] = set()
        self.removed: list[str] = []
        self.allow(sources)

    def allow(self, texts: Iterable[str]) -> None:
        self.allowed |= addresses(texts)
        self._pages = {_page(url) for url in self.allowed}

    def known(self, url: str) -> bool:
        normal = _normal(url)
        # The same product page without its query (a variant id) is still the page Studio gave.
        return normal in self.allowed or _page(normal) in self._pages

    def clean(self, text: str) -> str:
        """``text`` with every address Studio did not return taken out."""

        def link(match: re.Match[str]) -> str:
            label, url = match.group(1), match.group(2)
            if self.known(url.rstrip(_TRAILING)):
                return match.group(0)
            self.removed.append(url)
            return f"{label} {REMOVED}"

        def bare(match: re.Match[str]) -> str:
            raw = match.group(0)
            url = raw.rstrip(_TRAILING)
            if self.known(url):
                return raw
            self.removed.append(url)
            return REMOVED + raw[len(url):]

        parts = []
        last = 0
        # Markdown links first, then bare addresses outside them.
        for match in _LINK.finditer(text):
            parts.append(_URL.sub(bare, text[last:match.start()]))
            parts.append(link(match))
            last = match.end()
        parts.append(_URL.sub(bare, text[last:]))
        return "".join(parts)


def safe_end(text: str, start: int = 0) -> int:
    """How far into ``text`` may be released: up to any address or markdown link still being written."""
    tail = text[start:]
    end = len(tail)
    for pattern in (_OPEN_URL, _OPEN_LINK):
        match = pattern.search(tail)
        if match:
            end = min(end, match.start())
    # A last word that may still become an address: "ht", "https:/", "ww".
    word = re.search(r"[^\s(\[]+$", tail)
    if word and any(scheme.startswith(word.group(0).lower()) for scheme in _SCHEMES):
        end = min(end, word.start())
    return start + end
