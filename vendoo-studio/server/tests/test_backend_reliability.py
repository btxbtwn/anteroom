"""Background steps fail visibly and closed, and prompts follow the seller's own formulas."""
from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest

from vendoo_studio.services import automations, catalog_index, listing_generate, skill_formulas


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *_args):
        return self

    def all(self):
        return self.rows


def test_a_sale_whose_stock_cannot_be_read_counts_as_sold(caplog):
    db = SimpleNamespace(query=lambda _model: _Rows([SimpleNamespace(marketplace="ebay", listing_id="1")]))
    with patch("vendoo_studio.services.sale_inventory.stock_after_sale", side_effect=RuntimeError("bad row")), \
            caplog.at_level(logging.ERROR, logger="vendoo_studio.automations"):
        assert automations._sold_marketplaces(db, "conv-1") == {"ebay"}
    assert "conv-1" in caplog.text


def test_the_hosted_catalog_build_has_a_deadline(tmp_path, monkeypatch):
    monkeypatch.setenv("VENDOO_STUDIO_DATA_DIR", str(tmp_path))
    seen = {}

    def run(*args, **kwargs):
        seen.update(kwargs)
        raise catalog_index.subprocess.TimeoutExpired(args[0], kwargs.get("timeout") or 0)

    monkeypatch.setattr(catalog_index.subprocess, "run", run)
    from vendoo_studio.services.hosted_context import for_seller
    from vendoo_studio.services.service_context import use

    with use(for_seller(str(uuid4()))):
        with pytest.raises(catalog_index.subprocess.TimeoutExpired):
            catalog_index.build_catalog_index(catalog_index.CatalogSnapshot({"trees": {}}, [], []))
    assert seen["timeout"] == catalog_index.HOSTED_BUILD_TIMEOUT_SEC


def test_formula_rules_need_the_list_this_skill(tmp_path, monkeypatch):
    monkeypatch.setenv("VENDOO_STUDIO_SKILLS_DIR", str(tmp_path))
    skill_formulas._base_formula_rules.cache_clear()
    try:
        with pytest.raises(FileNotFoundError, match="SKILL.md"):
            skill_formulas.listing_formula_rules()
    finally:
        skill_formulas._base_formula_rules.cache_clear()


def test_json_repair_follows_the_sellers_title_formula():
    seen = []

    class Provider:
        async def quick_chat(self, messages):
            seen.extend(messages)
            yield '```json\n{"title": "Fixed"}\n```'

    with patch("vendoo_studio.services.skill_formulas.get_listing_formulas", return_value={"title": "{ITEM} by {BRAND}"}):
        asyncio.run(listing_generate.repair_listing_json(Provider(), '```json\n{"title": "Broken",,\n```'))
    system = seen[0]["content"]
    assert "{ITEM} by {BRAND}" in system
    assert "Brand Size Vibe Item Color Fit" not in system
