"""A marketplace form that could not be loaded is unknown, not proof every saved answer is invalid.

Listing b2d0b9f27f87 (revision fe774df67f31): while generation re-read the
forms, Mercari's could not be loaded. The fields check then treated its empty
form as one with no fields, deleted every saved Mercari answer, and left only
empty ``smartPricing`` and ``package`` sections.
"""

from __future__ import annotations

import copy
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy.orm import sessionmaker

from worker_sessions import memory_engine, worker_sessions

from vendoo_studio.database import Base
from vendoo_studio.repositories.queries import ConversationRepo, ListingRepo
from vendoo_studio.services import listing_persistence, native_contract, native_field_fill


def row(path, label, options=(), **extra):
    return {"key": path, "path": path, "label": label, "value": "", "required": False, "multi": False,
            "selection_only": bool(options), "options": list(options),
            "option_values": {option: option for option in options}, **extra}


MERCARI_FORM = {"marketplace": "mercari", "native": True, "known": True, "category_id": "1934", "page_labels": True,
                "fields": [
                    row("mercari_specifics.brand", "Brand", ["Gear for Sports"]),
                    row("mercari_specifics.condition", "Condition", ["3"]),
                    row("mercari_specifics.nativeSizeId", "Size", ["341"]),
                    row("mercari_specifics.shipsFrom", "Ships from"),
                    row("mercari_specifics.smartPricing.enabled", "Smart Pricing"),
                    row("mercari_specifics.smartPricing.minimumPrice", "Minimum price"),
                ]}

SAVED_MERCARI = {"brand": "Gear for Sports", "condition": "3", "nativeSizeId": "341", "shipsFrom": "70115",
                 "smartPricing": {"enabled": True, "minimumPrice": 17}}


class ConstrainGeneratedTest(unittest.TestCase):
    def test_an_unavailable_or_empty_form_keeps_every_saved_answer(self):
        for contract in ({"unavailable": True}, {"fields": []}, {}):
            listing = {"mercari_specifics": copy.deepcopy(SAVED_MERCARI), native_contract.KEY: {"mercari": contract}}
            native_contract.constrain_generated(listing, copy.deepcopy(listing), freeze_categories=False)
            self.assertEqual(listing["mercari_specifics"], SAVED_MERCARI, contract)
            # The form stays unknown, so List still asks for the fields to be reloaded.
            self.assertEqual(listing[native_contract.KEY]["mercari"], contract)

    def test_seller_locks_still_win_on_an_unavailable_form(self):
        seed = {"mercari_specifics": {"condition": "3"}, native_contract.KEY: {"mercari": {"unavailable": True}},
                "seller_fields": ["mercari_specifics.condition", "mercari_specifics.brand"]}
        listing = {**copy.deepcopy(seed), "mercari_specifics": {"condition": "1", "brand": "Generated", "shipsFrom": "70115"}}
        native_contract.constrain_generated(listing, seed, freeze_categories=False)
        self.assertEqual(listing["mercari_specifics"]["condition"], "3")
        # A locked path the seed holds no value for is not filled by generation.
        self.assertIsNone(listing["mercari_specifics"]["brand"])
        self.assertEqual(listing["mercari_specifics"]["shipsFrom"], "70115")

    def test_unavailable_contract_still_blocks_listing(self):
        listing = {"mercari_specifics": copy.deepcopy(SAVED_MERCARI), native_contract.KEY: {"mercari": {"unavailable": True}}}
        native_contract.constrain_generated(listing, copy.deepcopy(listing), freeze_categories=False)
        found = native_contract.gaps(listing, "mercari")
        self.assertTrue(found and found[0]["blocking"])
        self.assertIn("could not be loaded", found[0]["message"])

    def test_a_loaded_form_still_strips_invented_and_invalid_answers(self):
        contracts = native_contract.capture({}, {"mercari": MERCARI_FORM}, ["mercari"])
        listing = {native_contract.KEY: contracts, "mercari_specifics": {
            **copy.deepcopy(SAVED_MERCARI), "invented": "x", "condition": "not a Mercari condition"}}
        native_contract.constrain_generated(listing, {native_contract.KEY: contracts}, freeze_categories=False)
        self.assertNotIn("invented", listing["mercari_specifics"])
        self.assertNotIn("condition", listing["mercari_specifics"])
        self.assertEqual(listing["mercari_specifics"]["brand"], "Gear for Sports")
        self.assertEqual(listing["mercari_specifics"]["smartPricing"], {"enabled": True, "minimumPrice": 17})


class RecordFormsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = memory_engine()
        Base.metadata.create_all(self.engine)
        self.enterContext(worker_sessions(self.engine))
        self.db = sessionmaker(bind=self.engine)()
        self.conv = ConversationRepo(self.db).create(title="Gear for Sports tee")
        self.repo = ListingRepo(self.db)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _current(self):
        self.db.expire_all()
        saved = self.repo.get_current(self.conv.id)
        return self.repo.get_revision(saved.current_revision_id).listing_json

    async def test_a_form_that_cannot_be_read_keeps_that_marketplaces_answers(self):
        listing = {"title": "Gear for Sports tee", "brand": "Gear for Sports", "condition": "Pre-Owned - Good",
                   "direct_category_ids": {"mercari": "1934"}, "mercari_specifics": copy.deepcopy(SAVED_MERCARI)}
        listing[native_contract.KEY] = native_contract.capture(listing, {"mercari": MERCARI_FORM}, ["mercari"])
        listing_persistence.save_revision(self.db, self.conv.id, listing, "native_schema")
        unavailable = RuntimeError("Cloud data is temporarily unavailable.")
        with patch("vendoo_studio.services.native_editor.editor_form", AsyncMock(side_effect=unavailable)):
            await native_field_fill.record_forms(self.conv.id, {"mercari"})
        saved = self._current()
        self.assertEqual(saved["mercari_specifics"], SAVED_MERCARI)
        self.assertEqual(saved[native_contract.KEY]["mercari"], {"unavailable": True})


if __name__ == "__main__":
    unittest.main()
