"""eBay's synced category tree is eBay US's. Sellers on another eBay site search their own site instead."""
from fastapi.testclient import TestClient

from anteroom.database import SessionLocal
from anteroom.main import app
from anteroom.models.catalog import CategoryNode
from anteroom.services.seller_region import set_seller_region


def _observe(marketplace: str, path: str) -> None:
    parent = path.rsplit(" > ", 1)[0]
    with SessionLocal() as db:
        db.add(CategoryNode(marketplace=marketplace, path=path, parent_path=parent, label=path.rsplit(" > ", 1)[-1]))
        db.commit()


def test_ebay_category_tree_is_offered_only_to_us_sellers():
    _observe("ebay", "Clothing > Women > Coats")
    _observe("depop", "Womenswear > Coats")
    client = TestClient(app)
    try:
        set_seller_region("US")
        us = client.get("/api/catalog/categories", params={"marketplace": "ebay"})
        assert us.status_code == 200
        assert [node["path"] for node in us.json()["nodes"]] == ["Clothing > Women > Coats"]

        set_seller_region("GB")
        gb = client.get("/api/catalog/categories", params={"marketplace": "ebay"})
        assert gb.status_code == 200
        # No US categories, and not complete: the iPhone picker then searches eBay's own site.
        assert gb.json()["nodes"] == []
        assert gb.json()["complete"] is False
        depop = client.get("/api/catalog/categories", params={"marketplace": "depop"})
        assert [node["path"] for node in depop.json()["nodes"]] == ["Womenswear > Coats"]
    finally:
        set_seller_region("US")
