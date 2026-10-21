"""A Sourcing box's photos and video, read from its product page while the assistant answers."""
import asyncio
import base64
import json
import socket
import subprocess
from io import BytesIO
from pathlib import Path
from unittest import mock

import httpx
import pytest
from PIL import Image

from vendoo_studio.services import box_media

FIXTURES = Path(__file__).parent / "fixtures" / "box_media"
RAGHOUSE = "https://raghouse.com/products/hippie-hawaiian-dresses-39-pcs"
TVF = "https://thriftvintagefashion.com/products/mens-womens-flannel-bale?variant=50093639565543"
PAPERCRANE = ("https://www.papercranewholesale.com/shop/"
              "lot-of-23-shoes-mixed-sizing-and-mid-condition-f890158d-5280-463a-b6ed-5b22938ae5bc")


def jpeg(width=1600, height=1200) -> bytes:
    out = BytesIO()
    Image.new("RGB", (width, height), (200, 30, 30)).save(out, format="JPEG")
    return out.getvalue()


@pytest.fixture(autouse=True)
def _fresh_memo():
    box_media.clear_memo()
    yield
    box_media.clear_memo()


@pytest.fixture
def public_dns():
    answer = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 0))]
    with mock.patch("vendoo_studio.services.safe_fetch.socket.getaddrinfo", return_value=answer):
        yield


# --- Parsers, on pages saved from the stores ---------------------------------------


def test_a_shopify_box_gives_its_gallery_and_the_smallest_mp4_of_its_video():
    found = box_media.shopify_media(json.loads((FIXTURES / "raghouse.js").read_text()))
    assert found.images == [
        "https://cdn.shopify.com/s/files/1/0701/8984/2725/files/hippie-hawaiian-dresses-39-pcs-poster.jpg?v=1789172517"]
    assert len(found.videos) == 1
    assert found.videos[0]["url"].endswith(".SD-480p-1.5Mbps-92703372.mp4")
    assert "preview_images" in found.videos[0]["poster"]

    tvf = box_media.shopify_media(json.loads((FIXTURES / "tvf.js").read_text()))
    assert len(tvf.images) == 7 and not tvf.videos
    assert all(url.startswith("https://cdn.shopify.com/") for url in tvf.images)


def test_a_papercrane_lot_reads_its_own_gallery_not_the_sellers_other_lots():
    found = box_media.papercrane_media((FIXTURES / "papercrane.rsc").read_text(),
                                       "Lot of 23 shoes mixed sizing and mid condition")
    assert found.images == [
        "https://hzikojhgqzbifdofncic.supabase.co/storage/v1/object/public/listing-images/"
        "d978d5c6-8353-4d46-a91d-be735ee7bc38/1791820991329-3yck5q.jpg"]
    assert [video["url"] for video in found.videos] == [
        "https://hzikojhgqzbifdofncic.supabase.co/storage/v1/object/public/listing-images/"
        "d978d5c6-8353-4d46-a91d-be735ee7bc38/1791821012204-0-video.mp4"]


def test_any_other_page_is_read_from_og_tags_json_ld_videos_and_srcsets():
    html = """<html><head>
      <meta property="og:image" content="//cdn.example/box-main.jpg">
      <script type="application/ld+json">{"@type": "Product", "image": ["https://cdn.example/box-2.jpg",
        {"url": "https://cdn.example/box-3.jpg"}]}</script>
    </head><body>
      <img src="/static/logo.png">
      <img src="/a-small.jpg" srcset="/a-small.jpg 400w, /a-large.jpg 1600w">
      <video poster="/poster.jpg"><source src="/clip.mp4" type="video/mp4"></video>
    </body></html>"""
    found = box_media.html_media(html, "https://shop.example/products/box")
    assert found.images == ["https://cdn.example/box-main.jpg", "https://cdn.example/box-2.jpg",
                            "https://cdn.example/box-3.jpg", "https://shop.example/a-large.jpg"]
    assert found.videos == [{"url": "https://shop.example/clip.mp4", "poster": "https://shop.example/poster.jpg"}]


def test_each_store_is_read_where_its_gallery_lives():
    assert box_media.page_request(RAGHOUSE) == (f"{RAGHOUSE}.js", {"Accept": "application/json"}, "shopify")
    assert box_media.page_request(TVF)[0] == "https://thriftvintagefashion.com/products/mens-womens-flannel-bale.js"
    assert box_media.page_request(PAPERCRANE) == (PAPERCRANE, {"RSC": "1"}, "papercrane")
    assert box_media.page_request("https://other.example/item/9")[2] == "html"


# --- Caps -----------------------------------------------------------------------------


def test_a_box_gets_at_most_eight_images_with_room_kept_for_video_frames():
    found = box_media.Found(images=[f"https://cdn.example/{n}.jpg" for n in range(20)],
                            videos=[{"url": f"https://cdn.example/{n}.mp4", "poster": None} for n in range(4)])
    photos, videos = box_media.plan_media(found)
    assert len(videos) == box_media.MAX_VIDEOS_PER_BOX
    assert len(photos) == box_media.MAX_IMAGES_PER_BOX - box_media.FRAMES_PER_VIDEO * len(videos)
    photos, videos = box_media.plan_media(box_media.Found(images=[f"https://c.example/{n}.jpg" for n in range(20)]))
    assert len(photos) == 8 and videos == []


def shop_transport(pages: dict[str, tuple[str, bytes]], hits: list[str] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        url = f"https://{request.headers['host']}{request.url.raw_path.decode()}"
        if hits is not None:
            hits.append(url)
        if url not in pages:
            return httpx.Response(404)
        content_type, body = pages[url]
        return httpx.Response(200, headers={"content-type": content_type}, content=body)
    return httpx.MockTransport(handler)


def gallery_pages(count: int) -> dict[str, tuple[str, bytes]]:
    product = {"media": [{"media_type": "image", "src": f"https://cdn.example/{n}.jpg"} for n in range(count)]}
    pages = {"https://raghouse.com/products/big-box.js": ("application/json", json.dumps(product).encode())}
    pages.update({f"https://cdn.example/{n}.jpg": ("image/jpeg", jpeg()) for n in range(count)})
    return pages


def test_twelve_photos_reach_the_model_as_eight_downscaled_images(public_dns):
    async def run():
        async with httpx.AsyncClient(transport=shop_transport(gallery_pages(12))) as client:
            return await box_media.view_box(client, {"id": "raghouse:1", "title": "Big box", "store": "Raghouse",
                                                      "url": "https://raghouse.com/products/big-box"})
    view = asyncio.run(run())
    assert view.viewed and len(view.images) == 8 and view.photos_on_page == 12
    assert view.images[0][0] == "photo 1 of 12"
    assert any("4 more photos on the page were not viewed" in note for note in view.notes)
    with Image.open(BytesIO(base64.b64decode(view.images[0][1].split(",", 1)[1]))) as image:
        assert max(image.size) == box_media.IMAGE_MAX_SIDE


def test_an_unchanged_page_reuses_its_images_within_the_hour(public_dns):
    hits: list[str] = []
    box = {"id": "raghouse:1", "title": "Big box", "store": "Raghouse", "url": "https://raghouse.com/products/big-box"}

    async def run():
        async with httpx.AsyncClient(transport=shop_transport(gallery_pages(2), hits)) as client:
            first = await box_media.view_box(client, box)
            again = await box_media.view_box(client, {**box, "id": "raghouse:1b"})
            return first, again
    first, again = asyncio.run(run())
    # The page is read twice; its two photos only once.
    assert hits.count("https://raghouse.com/products/big-box.js") == 2
    assert sum(1 for url in hits if url.startswith("https://cdn.example/")) == 2
    assert again.images == first.images and again.box_id == "raghouse:1b"


# --- Video ------------------------------------------------------------------------------


def fake_ffmpeg(seen_dirs: list[Path]):
    def run(argv, **_kwargs):
        if "-frames:v" not in argv:
            return subprocess.CompletedProcess(argv, 1, "", "  Duration: 00:00:12.00, start: 0.000000")
        target = Path(argv[-1])
        seen_dirs.append(target.parent)
        target.write_bytes(jpeg(640, 480))
        return subprocess.CompletedProcess(argv, 0, b"", b"")
    return run


def video_pages():
    product = {"media": [{"media_type": "video", "preview_image": {"src": "https://cdn.example/poster.jpg"},
                          "sources": [{"url": "https://cdn.example/clip.mp4", "format": "mp4", "height": 480}]}]}
    return {
        "https://raghouse.com/products/vid.js": ("application/json", json.dumps(product).encode()),
        "https://cdn.example/clip.mp4": ("video/mp4", b"\x00\x00\x00\x18ftypmp42"),
        "https://cdn.example/poster.jpg": ("image/jpeg", jpeg()),
    }


VIDEO_BOX = {"id": "raghouse:2", "title": "Video box", "store": "Raghouse", "url": "https://raghouse.com/products/vid"}


def test_a_video_is_cut_into_evenly_spaced_frames_in_a_temp_dir_that_is_then_deleted(public_dns):
    seen_dirs: list[Path] = []
    calls = []

    def recording(argv, **kwargs):
        calls.append(argv)
        return fake_ffmpeg(seen_dirs)(argv, **kwargs)

    async def run():
        async with httpx.AsyncClient(transport=shop_transport(video_pages())) as client:
            return await box_media.view_box(client, VIDEO_BOX)
    with mock.patch.object(box_media, "ffmpeg_path", return_value="/fake/ffmpeg"), \
            mock.patch.object(box_media.subprocess, "run", side_effect=recording):
        view = asyncio.run(run())

    assert [label for label, _ in view.images] == [f"video 1 of 1, frame {n} of 3" for n in (1, 2, 3)]
    assert "video 1 of 1: viewed as 3 frames" in view.notes
    # Frames at a quarter, half and three quarters of the 12 seconds.
    assert [argv[argv.index("-ss") + 1] for argv in calls if "-ss" in argv] == ["3.00", "6.00", "9.00"]
    assert seen_dirs and not any(folder.exists() for folder in seen_dirs)


def test_without_ffmpeg_the_poster_stands_in_and_the_video_is_not_viewed(public_dns):
    async def run():
        async with httpx.AsyncClient(transport=shop_transport(video_pages())) as client:
            return await box_media.view_box(client, VIDEO_BOX)
    with mock.patch.object(box_media, "ffmpeg_path", return_value=None):
        view = asyncio.run(run())
    assert [label for label, _ in view.images] == ["video 1 of 1, poster image only"]
    assert view.notes == ["video 1 of 1: NOT viewed (ffmpeg is not installed on this Mac); only its poster image was"]


def test_failed_frames_still_delete_the_temp_dir(public_dns):
    seen_dirs: list[Path] = []

    def broken(argv, **_kwargs):
        seen_dirs.append(Path(argv[argv.index("-i") + 1]).parent)
        raise subprocess.TimeoutExpired(argv, 30)

    async def run():
        async with httpx.AsyncClient(transport=shop_transport(video_pages())) as client:
            return await box_media.video_frames(client, "https://cdn.example/clip.mp4")
    with mock.patch.object(box_media, "ffmpeg_path", return_value="/fake/ffmpeg"), \
            mock.patch.object(box_media.subprocess, "run", side_effect=broken):
        frames, why = asyncio.run(run())
    assert frames == [] and why.startswith("ffmpeg could not read the video")
    assert seen_dirs and not seen_dirs[0].exists()


# --- What the model is told ---------------------------------------------------------


def test_boxes_that_could_not_be_seen_are_labelled_not_viewed(public_dns):
    pages = gallery_pages(2)

    async def run():
        async with httpx.AsyncClient(transport=shop_transport(pages)) as client:
            return await box_media.view_boxes([
                {"id": "raghouse:1", "title": "Big box", "store": "Raghouse",
                 "url": "https://raghouse.com/products/big-box"},
                {"id": "raghouse:9", "title": "Gone box", "store": "Raghouse",
                 "url": "https://raghouse.com/products/gone"},
                {"id": "tvf:3", "title": "No page", "store": "TVF", "url": None},
            ], client=client)
    views = asyncio.run(run())
    text, parts = box_media.describe(views, skipped=["x: NOT VIEWED — no box with that id is on the Sourcing tab."])
    assert len(parts) == 2 and all(part["type"] == "image_url" for part in parts)
    assert "Box raghouse:1 (Big box, Raghouse): VIEWED — images 1-2: #1 photo 1 of 2; #2 photo 2 of 2" in text
    assert "Box raghouse:9 (Gone box, Raghouse): NOT VIEWED — the product page could not be read (HTTP 404)" in text
    assert "Box tvf:3 (No page, TVF): NOT VIEWED — Studio has no product page for this box" in text
    assert "x: NOT VIEWED" in text


def test_only_five_boxes_are_fetched_at_once():
    seen = []

    async def fake_view(_client, box, **_kwargs):
        seen.append(box["id"])
        return box_media.BoxView(box_id=box["id"], title="", store="", url=None)
    with mock.patch.object(box_media, "view_box", side_effect=fake_view):
        views = asyncio.run(box_media.view_boxes([{"id": str(n)} for n in range(9)], client=mock.Mock()))
    assert len(views) == 5 and seen == ["0", "1", "2", "3", "4"]
