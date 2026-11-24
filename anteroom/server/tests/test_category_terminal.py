"""The General category must be a final one: a category with no subcategories, at any depth (#2075)."""
from __future__ import annotations

from pathlib import Path

import pytest

from anteroom.services.validation import validate_listing

MESSAGE = "Choose a category with no subcategories"


def _category_issues(path: str) -> list[dict]:
    result = validate_listing({"title": "Item", "category_path": path, "price": 10}, 1, require_photos=False)
    return [issue for issue in result.errors if issue["field"] == "category_path"]


@pytest.mark.parametrize("path", [
    "Jewelry & Watches > Fashion Jewelry > Necklaces & Pendants",
    "Jewelry & Watches > Fashion Jewelry > Rings",
    "Clothing, Shoes & Accessories > Women > Women's Clothing > Tops",
])
def test_a_final_category_passes_whatever_its_depth(path):
    assert _category_issues(path) == []


@pytest.mark.parametrize("path", [
    "Jewelry & Watches > Fashion Jewelry",
    "Clothing, Shoes & Accessories > Men > Men's Clothing > Shirts",
])
def test_a_category_with_subcategories_is_rejected(path):
    assert [issue["message"] for issue in _category_issues(path)] == [MESSAGE]


def test_the_hosted_image_carries_the_category_tree_seed():
    root = Path(__file__).resolve().parents[3]
    dockerfile = (root / "deploy/fly/Dockerfile").read_text(encoding="utf-8")
    dockerignore = (root / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert "COPY anteroom/data/category-trees-seed.json.gz /app/anteroom/data/category-trees-seed.json.gz" in dockerfile
    assert "!anteroom/data/category-trees-seed.json.gz" in dockerignore
    assert "anteroom/data/*" in dockerignore
