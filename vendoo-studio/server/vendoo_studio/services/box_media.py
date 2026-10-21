"""A Sourcing box's photos and video, fetched while the assistant answers about it.

When the seller asks about specific boxes, or whether a box is worth buying,
the business assistant asks for them by id (``view_box_media``). This fetches
each box's product page at that moment, finds its images and videos, downloads
the images into memory, downscales them and hands them to the model as image
parts. A video is cut into a few frames with ffmpeg in a temporary directory
that is deleted straight after; without ffmpeg its poster image stands in and
the reply says the video itself was not viewed.

Nothing is written to the database or the data folder. A short in-process memo
keeps a page's result for an hour, so a follow-up in the same conversation does
not fetch the store again.

Stores: Raghouse and Thrift Vintage Fashion are Shopify shops, read through
``/products/<handle>.js`` (every image and video in the product's own gallery).
PaperCrane is a Next.js site, read through its server component payload, whose
lot gallery carries the lot's ``images`` and ``videos``. Any other page falls
back to its HTML: og:image / og:video, JSON-LD Product images, ``<video>`` tags
and the largest ``srcset`` candidate of each ``<img>``.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

import httpx
from PIL import Image

from vendoo_studio.services.safe_fetch import fetch_public, is_image_type

log = logging.getLogger("vendoo_studio.box_media")

MAX_IMAGES_PER_BOX = 8
MAX_BOXES_PER_ANSWER = 5
FRAMES_PER_VIDEO = 3
MAX_VIDEOS_PER_BOX = 2
FETCH_CONCURRENCY = 3
PAGE_TIMEOUT_SEC = 15.0
PAGE_MAX_BYTES = 4 * 1024 * 1024
IMAGE_MAX_BYTES = 15 * 1024 * 1024
VIDEO_MAX_BYTES = 60 * 1024 * 1024
VIDEO_TIMEOUT_SEC = 60.0
FFMPEG_TIMEOUT_SEC = 30.0
# Long side of an image the model sees. Fewer boxes get a little more detail, for tags.
IMAGE_MAX_SIDE = 1024
IMAGE_MAX_SIDE_MANY = 768
MEMO_SECONDS = 3600
MEMO_ENTRIES = 40
USER_AGENT = "Mozilla/5.0 (box-scout; personal sourcing research)"
# Where Homebrew puts ffmpeg; the Mac app is not started with a shell PATH.
FFMPEG_LOCATIONS = ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg")
SHOPIFY_HOSTS = {"raghouse.com", "thriftvintagefashion.com"}
PAPERCRANE_HOSTS = {"papercranewholesale.com"}
_IMAGE_SUFFIX = re.compile(r"\.(?:jpe?g|png|webp|gif|avif|heic)(?:$|\?)", re.IGNORECASE)
_SKIP_IMAGE = re.compile(r"(?:logo|icon|sprite|favicon|avatar|badge|payment|placeholder)", re.IGNORECASE)


@dataclass
class Found:
    """What a product page shows: image URLs and videos, gallery order."""

    images: list[str] = field(default_factory=list)
    # Each video: {"url": playable file or None, "poster": image URL or None}
    videos: list[dict[str, str | None]] = field(default_factory=list)

    def add_image(self, url: str | None) -> None:
        if url and url not in self.images:
            self.images.append(url)

    def add_video(self, url: str | None, poster: str | None = None) -> None:
        if not url and not poster:
            return
        if any(video["url"] == url and url for video in self.videos):
            return
        self.videos.append({"url": url, "poster": poster})


@dataclass
class BoxView:
    """One box as the model sees it this turn."""

    box_id: str
    title: str
    store: str
    url: str | None
    # (label, data URL) in the order they are attached.
    images: list[tuple[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    error: str | None = None
    photos_on_page: int = 0
    videos_on_page: int = 0

    @property
    def viewed(self) -> bool:
        return bool(self.images)


# --- Parsers ---------------------------------------------------------------------------


def shopify_media(product: dict[str, Any]) -> Found:
    """A Shopify ``/products/<handle>.js`` payload: its gallery, videos included."""
    found = Found()
    media = product.get("media")
    if isinstance(media, list) and media:
        for item in media:
            if not isinstance(item, dict):
                continue
            kind = item.get("media_type")
            preview = (item.get("preview_image") or {}).get("src") if isinstance(item.get("preview_image"), dict) else None
            if kind == "image":
                found.add_image(_absolute(item.get("src") or preview))
            elif kind == "video":
                found.add_video(_smallest_mp4(item.get("sources")), _absolute(preview))
            elif kind == "external_video":
                # A YouTube or Vimeo embed: only its thumbnail can be read here.
                found.add_video(None, _absolute(preview))
        return found
    for image in product.get("images") or []:
        found.add_image(_absolute(image if isinstance(image, str) else (image or {}).get("src")))
    return found


def _smallest_mp4(sources: Any) -> str | None:
    files = [s for s in sources or [] if isinstance(s, dict) and s.get("url")
             and (s.get("format") == "mp4" or str(s.get("mime_type", "")).startswith("video/mp4"))]
    if not files:
        return None
    return _absolute(min(files, key=lambda s: (s.get("height") or 10_000))["url"])


def papercrane_media(payload: str, title: str | None = None) -> Found:
    """A PaperCrane lot page's server component payload: the lot's own gallery.

    The page also carries cards for the seller's other lots (``moqCount``,
    ``priceCents``); only the gallery object, which has neither, is the lot's.
    """
    found = Found()
    galleries = [obj for obj in _json_objects(payload, "images")
                 if isinstance(obj.get("images"), list) and "moqCount" not in obj and "priceCents" not in obj]
    if title:
        named = [obj for obj in galleries if str(obj.get("alt") or "").strip() == title.strip()]
        galleries = named or galleries
    if not galleries:
        return found
    gallery = galleries[0]
    for url in gallery.get("images") or []:
        found.add_image(url if isinstance(url, str) else None)
    for url in gallery.get("videos") or []:
        found.add_video(url if isinstance(url, str) else None)
    return found


def _json_objects(payload: str, key: str) -> list[dict]:
    """Every JSON object in a text payload that carries ``key``, innermost first."""
    objects = []
    decoder = json.JSONDecoder()
    for match in re.finditer(f'"{re.escape(key)}":', payload):
        start, depth = match.start(), 0
        while start >= 0:
            char = payload[start]
            if char == "}":
                depth += 1
            elif char == "{":
                if depth == 0:
                    break
                depth -= 1
            start -= 1
        if start < 0:
            continue
        try:
            obj, _ = decoder.raw_decode(payload, start)
        except ValueError:
            continue
        if isinstance(obj, dict):
            objects.append(obj)
    return objects


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.og_images: list[str] = []
        self.og_videos: list[str] = []
        self.imgs: list[str] = []
        self.videos: list[tuple[str | None, str | None]] = []
        self.ld_json: list[str] = []
        self._in_ld = False
        self._ld_text: list[str] = []
        self._video: list[Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.lower(): value or "" for name, value in attrs}
        if tag == "meta":
            prop = (values.get("property") or values.get("name") or "").lower()
            if prop in {"og:image", "og:image:url", "og:image:secure_url", "twitter:image"}:
                self.og_images.append(values.get("content", ""))
            elif prop in {"og:video", "og:video:url", "og:video:secure_url"}:
                self.og_videos.append(values.get("content", ""))
        elif tag == "script" and values.get("type", "").lower() == "application/ld+json":
            self._in_ld, self._ld_text = True, []
        elif tag == "img":
            best = _largest_srcset(values.get("srcset") or values.get("data-srcset") or "")
            self.imgs.append(best or values.get("src") or values.get("data-src") or "")
        elif tag == "video":
            self._video = [values.get("src") or None, values.get("poster") or None]
            self.videos.append(tuple(self._video))
        elif tag == "source" and self._video is not None and values.get("src"):
            if "mp4" in values.get("type", "mp4") and not self._video[0]:
                self._video[0] = values["src"]
                self.videos[-1] = tuple(self._video)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in_ld:
            self._in_ld = False
            self.ld_json.append("".join(self._ld_text))
        elif tag == "video":
            self._video = None

    def handle_data(self, data: str) -> None:
        if self._in_ld:
            self._ld_text.append(data)


def _largest_srcset(srcset: str) -> str | None:
    best, best_width = None, -1.0
    for candidate in srcset.split(","):
        parts = candidate.strip().split()
        if not parts:
            continue
        width = 0.0
        if len(parts) > 1 and parts[1][:-1].replace(".", "", 1).isdigit():
            width = float(parts[1][:-1]) * (1000 if parts[1].endswith("x") else 1)
        if width > best_width:
            best, best_width = parts[0], width
    return best


def html_media(html: str, page_url: str) -> Found:
    """Any product page: og tags, JSON-LD Product images, videos, then the page's images."""
    parser = _PageParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 - a malformed page still yields what was read before the fault
        log.debug("HTML parse stopped early for %s", page_url, exc_info=True)
    found = Found()
    for url in parser.og_images:
        found.add_image(_resolve(page_url, url))
    for text in parser.ld_json:
        for image in _ld_images(text):
            found.add_image(_resolve(page_url, image))
    for url in parser.og_videos:
        found.add_video(_resolve(page_url, url))
    for src, poster in parser.videos:
        found.add_video(_resolve(page_url, src), _resolve(page_url, poster))
    for url in parser.imgs:
        resolved = _resolve(page_url, url)
        if resolved and _IMAGE_SUFFIX.search(urlparse(resolved).path + "?") and not _SKIP_IMAGE.search(resolved):
            found.add_image(resolved)
    return found


def _ld_images(text: str) -> list[str]:
    try:
        data = json.loads(unescape(text))
    except ValueError:
        return []
    images: list[str] = []
    stack = data if isinstance(data, list) else [data]
    while stack:
        node = stack.pop(0)
        if isinstance(node, list):
            stack.extend(node)
            continue
        if not isinstance(node, dict):
            continue
        if "@graph" in node:
            stack.extend(node["@graph"] if isinstance(node["@graph"], list) else [node["@graph"]])
        kind = node.get("@type")
        kinds = kind if isinstance(kind, list) else [kind]
        if "Product" in kinds:
            image = node.get("image")
            for item in image if isinstance(image, list) else [image]:
                if isinstance(item, str):
                    images.append(item)
                elif isinstance(item, dict) and isinstance(item.get("url"), str):
                    images.append(item["url"])
    return images


def _absolute(url: Any) -> str | None:
    if not isinstance(url, str) or not url.strip():
        return None
    url = url.strip()
    return f"https:{url}" if url.startswith("//") else url


def _resolve(page_url: str, url: str | None) -> str | None:
    url = _absolute(unescape(url)) if url else None
    if not url or url.startswith("data:"):
        return None
    resolved = urljoin(page_url, url)
    return resolved if resolved.startswith(("https://", "http://")) else None


# --- Where to read a box's media ---------------------------------------------------


def _host(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def page_request(url: str) -> tuple[str, dict[str, str], str]:
    """The URL to fetch for a box page, its headers, and which parser reads it."""
    parsed = urlparse(url)
    host = _host(url)
    handle = re.match(r"^/products/([^/?#]+)/?$", parsed.path or "")
    if host in SHOPIFY_HOSTS and handle:
        js = urlunparse(parsed._replace(path=f"/products/{handle.group(1)}.js", query="", fragment=""))
        return js, {"Accept": "application/json"}, "shopify"
    if host in PAPERCRANE_HOSTS:
        return url, {"RSC": "1"}, "papercrane"
    return url, {"Accept": "text/html,application/xhtml+xml"}, "html"


def parse_page(kind: str, body: str, url: str, title: str | None = None) -> Found:
    if kind == "shopify":
        return shopify_media(json.loads(body))
    if kind == "papercrane":
        found = papercrane_media(body, title)
        return found if found.images or found.videos else html_media(body, url)
    return html_media(body, url)


# --- Images and frames ----------------------------------------------------------------


def downscale(contents: bytes, max_side: int = IMAGE_MAX_SIDE) -> str:
    """An image as a JPEG data URL no longer than ``max_side`` on its long side."""
    with Image.open(BytesIO(contents)) as image:
        image.load()
        image = image.convert("RGB")
        image.thumbnail((max_side, max_side))
        out = BytesIO()
        image.save(out, format="JPEG", quality=82, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode("ascii")


def ffmpeg_path() -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return found
    for location in FFMPEG_LOCATIONS:
        if os.path.isfile(location) and os.access(location, os.X_OK):
            return location
    return None


_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def _duration(ffmpeg: str, path: Path) -> float | None:
    probe = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [ffmpeg, "-hide_banner", "-nostdin", "-i", str(path)],
        capture_output=True, text=True, timeout=FFMPEG_TIMEOUT_SEC, check=False,
    )
    match = _DURATION.search(probe.stderr or "")
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def extract_frames(ffmpeg: str, video: Path, count: int, out_dir: Path) -> list[bytes]:
    """``count`` evenly spaced frames of ``video`` as JPEG bytes, cut into ``out_dir``."""
    duration = _duration(ffmpeg, video)
    points = [duration * (index + 1) / (count + 1) for index in range(count)] if duration else [0.0]
    frames = []
    for index, at in enumerate(points):
        target = out_dir / f"frame-{index}.jpg"
        subprocess.run(  # noqa: S603 - fixed argv, no shell
            [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-ss", f"{at:.2f}", "-i", str(video),
             "-frames:v", "1", "-q:v", "3", str(target)],
            capture_output=True, timeout=FFMPEG_TIMEOUT_SEC, check=False,
        )
        if target.is_file() and target.stat().st_size:
            frames.append(target.read_bytes())
    return frames


async def video_frames(client: httpx.AsyncClient, url: str, count: int = FRAMES_PER_VIDEO) -> tuple[list[bytes], str | None]:
    """Frames of one video, or why there are none. The download lives in a temp dir only while it is cut."""
    ffmpeg = ffmpeg_path()
    if ffmpeg is None:
        return [], "ffmpeg is not installed on this Mac"
    try:
        contents, _, _ = await asyncio.wait_for(
            fetch_public(client, url, accept=_is_video_type, max_bytes=VIDEO_MAX_BYTES,
                         headers={"User-Agent": USER_AGENT}),
            VIDEO_TIMEOUT_SEC,
        )
    except TimeoutError:
        return [], f"the video took longer than {VIDEO_TIMEOUT_SEC:.0f}s to download"
    except Exception as exc:  # noqa: BLE001 - every failure is reported to the model as text
        return [], f"the video could not be downloaded ({_reason(exc)})"
    with tempfile.TemporaryDirectory(prefix="box-video-") as tmp:
        folder = Path(tmp)
        video = folder / "video"
        video.write_bytes(contents)
        try:
            frames = await asyncio.to_thread(extract_frames, ffmpeg, video, count, folder)
        except Exception as exc:  # noqa: BLE001
            return [], f"ffmpeg could not read the video ({_reason(exc)})"
    if not frames:
        return [], "ffmpeg found no frames in the video"
    return frames, None


def _is_video_type(content_type: str) -> bool:
    return not content_type or content_type.startswith("video/") or content_type in {
        "application/octet-stream", "binary/octet-stream",
    }


def _reason(exc: BaseException) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return str(exc).strip() or type(exc).__name__


# --- Viewing boxes --------------------------------------------------------------------

# url -> (stored at, page hash, the view without its box id)
_memo: dict[str, tuple[float, str, BoxView]] = {}


def clear_memo() -> None:
    _memo.clear()


def _remember(url: str, digest: str, view: BoxView) -> None:
    _memo[url] = (time.monotonic(), digest, view)
    while len(_memo) > MEMO_ENTRIES:
        _memo.pop(min(_memo, key=lambda key: _memo[key][0]))


def _recalled(url: str, digest: str) -> BoxView | None:
    """The view kept for this page, while it is under an hour old and the page is unchanged."""
    entry = _memo.get(url)
    if entry is None:
        return None
    if time.monotonic() - entry[0] > MEMO_SECONDS or entry[1] != digest:
        _memo.pop(url, None)
        return None
    return entry[2]


def plan_media(found: Found, *, limit: int = MAX_IMAGES_PER_BOX) -> tuple[list[str], list[dict[str, str | None]]]:
    """Which photos and videos fit one box's image budget: frames for up to two videos, photos for the rest."""
    videos = found.videos[:MAX_VIDEOS_PER_BOX]
    reserved = min(limit, FRAMES_PER_VIDEO * sum(1 for video in videos if video["url"]))
    return found.images[: max(0, limit - reserved)], videos


async def view_box(
    client: httpx.AsyncClient, box: dict[str, Any], *, max_side: int = IMAGE_MAX_SIDE,
) -> BoxView:
    """Fetch one box's page now and return what the model can see of it."""
    view = BoxView(box_id=str(box.get("id") or ""), title=str(box.get("title") or ""),
                   store=str(box.get("store") or ""), url=box.get("url") or None)
    if not view.url:
        view.error = "Studio has no product page for this box"
        return view
    fetch_url, headers, kind = page_request(view.url)
    try:
        body, _, _ = await asyncio.wait_for(
            fetch_public(client, fetch_url, max_bytes=PAGE_MAX_BYTES, headers={"User-Agent": USER_AGENT, **headers}),
            PAGE_TIMEOUT_SEC,
        )
        # The page is read every time; its photos and frames are not, while it is unchanged.
        digest = hashlib.sha256(body).hexdigest()
        remembered = _recalled(view.url, digest)
        if remembered is not None:
            return BoxView(**{**remembered.__dict__, "box_id": view.box_id})
        found = parse_page(kind, body.decode("utf-8", "replace"), view.url, view.title)
    except TimeoutError:
        view.error = f"the product page did not answer within {PAGE_TIMEOUT_SEC:.0f}s"
        return view
    except Exception as exc:  # noqa: BLE001 - reported to the model as text
        view.error = f"the product page could not be read ({_reason(exc)})"
        return view
    view.photos_on_page, view.videos_on_page = len(found.images), len(found.videos)
    photos, videos = plan_media(found)
    labelled: list[tuple[str, str]] = []
    for index, url in enumerate(photos, start=1):
        labelled.append((f"photo {index} of {len(found.images)}", url))
    images: list[tuple[str, str]] = []
    for label, url in labelled:
        try:
            contents, _, _ = await asyncio.wait_for(
                fetch_public(client, url, accept=is_image_type, max_bytes=IMAGE_MAX_BYTES,
                             headers={"User-Agent": USER_AGENT}),
                PAGE_TIMEOUT_SEC,
            )
            images.append((label, await asyncio.to_thread(downscale, contents, max_side)))
        except Exception as exc:  # noqa: BLE001
            view.notes.append(f"{label} could not be loaded ({_reason(exc)})")
    for number, video in enumerate(videos, start=1):
        label = f"video {number} of {len(found.videos)}"
        frames, why = ([], "the store gives no playable file for it") if not video["url"] else (
            await video_frames(client, video["url"]))
        if frames:
            for index, frame in enumerate(frames, start=1):
                images.append((f"{label}, frame {index} of {len(frames)}",
                               await asyncio.to_thread(downscale, frame, max_side)))
            view.notes.append(f"{label}: viewed as {len(frames)} frames")
            continue
        poster = video.get("poster")
        if poster and len(images) < MAX_IMAGES_PER_BOX:
            try:
                contents, _, _ = await fetch_public(client, poster, accept=is_image_type, max_bytes=IMAGE_MAX_BYTES,
                                                    headers={"User-Agent": USER_AGENT})
                images.append((f"{label}, poster image only", await asyncio.to_thread(downscale, contents, max_side)))
                view.notes.append(f"{label}: NOT viewed ({why}); only its poster image was")
                continue
            except Exception as exc:  # noqa: BLE001
                why = f"{why}; its poster image failed too ({_reason(exc)})"
        view.notes.append(f"{label}: NOT viewed ({why})")
    if len(found.videos) > len(videos):
        view.notes.append(f"{len(found.videos) - len(videos)} more videos were not viewed (cap)")
    if len(found.images) > len(photos):
        view.notes.append(f"{len(found.images) - len(photos)} more photos on the page were not viewed (cap of "
                          f"{MAX_IMAGES_PER_BOX} images per box)")
    view.images = images[:MAX_IMAGES_PER_BOX]
    if not found.images and not found.videos:
        view.error = "the product page shows no photos or videos"
    elif not view.images:
        view.error = "none of the page's photos or video frames could be loaded"
    if view.viewed:
        _remember(view.url, digest, view)
    return view


async def view_boxes(boxes: list[dict[str, Any]], *, client: httpx.AsyncClient | None = None) -> list[BoxView]:
    """Fetch up to ``MAX_BOXES_PER_ANSWER`` boxes, a few at a time, in the order given."""
    boxes = boxes[:MAX_BOXES_PER_ANSWER]
    max_side = IMAGE_MAX_SIDE if len(boxes) <= 2 else IMAGE_MAX_SIDE_MANY
    limit = asyncio.Semaphore(FETCH_CONCURRENCY)

    async def one(box: dict[str, Any], shared: httpx.AsyncClient) -> BoxView:
        async with limit:
            try:
                return await view_box(shared, box, max_side=max_side)
            except Exception as exc:  # noqa: BLE001 - one box failing never ends the lookup silently
                log.warning("box media failed for %s", box.get("url"), exc_info=True)
                return BoxView(box_id=str(box.get("id") or ""), title=str(box.get("title") or ""),
                               store=str(box.get("store") or ""), url=box.get("url"), error=_reason(exc))

    if client is not None:
        return list(await asyncio.gather(*(one(box, client) for box in boxes)))
    async with httpx.AsyncClient(follow_redirects=False, timeout=PAGE_TIMEOUT_SEC, trust_env=False) as shared:
        return list(await asyncio.gather(*(one(box, shared) for box in boxes)))


def describe(views: list[BoxView], *, skipped: list[str] | None = None) -> tuple[str, list[dict[str, Any]]]:
    """The lookup result: a text that says what each attached image is, and the image parts in that order."""
    lines = ["Lookup view_box_media result. Images below are attached in this order, after any images the seller "
             "attached to the question:"]
    parts: list[dict[str, Any]] = []
    number = 0
    for view in views:
        head = f"- Box {view.box_id} ({view.title}, {view.store})"
        if not view.viewed:
            lines.append(f"{head}: NOT VIEWED — {view.error or 'no images could be loaded'}.")
            continue
        first = number + 1
        number += len(view.images)
        lines.append(f"{head}: VIEWED — images {first}-{number}: "
                     + "; ".join(f"#{first + index} {label}" for index, (label, _) in enumerate(view.images))
                     + f". The page shows {view.photos_on_page} photos and {view.videos_on_page} videos.")
        for note in view.notes:
            lines.append(f"  - {note}")
        parts.extend({"type": "image_url", "image_url": {"url": data}} for _, data in view.images)
    for reason in skipped or []:
        lines.append(f"- {reason}")
    return "\n".join(lines), parts
