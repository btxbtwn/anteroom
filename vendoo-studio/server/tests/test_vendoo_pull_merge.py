"""A Vendoo pull after the listing was published directly.

Reproduces conversation 22d079e8ad16: Studio's revision went to eBay and Depop
directly, Vendoo still held an older draft, and taking Vendoo's copy replaced
the listing wholesale — dropping eBay's category specifics, seller notes and
Sellwild, and writing Vendoo's bookkeeping (``type: listing``, store
categories, cost notes) into the marketplace specifics.
"""
from __future__ import annotations

import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vendoo_studio.services import listing_persistence
from vendoo_studio.database import Base
from vendoo_studio.models.fill_log import FillLogEntry  # noqa: F401
from vendoo_studio.models.registry import FieldRegistry  # noqa: F401
from vendoo_studio.repositories.queries import ConversationRepo, ListingRepo
from vendoo_studio.services.vendoo_import import listing_from_vendoo, merge_notes
from vendoo_studio.services.vendoo_watch import (
    CONTENT_FINGERPRINT,
    SYNCED_AT,
    SYNCED_REVISION,
    apply_pull,
    item_content_fingerprint,
    sync_state,
)

POSH_LEAF = "23008c10d97b4e1245005764"

STUDIO_LISTING = {
    "title": "Columbia XL (18/20) Outdoors Hooded Windbreaker Blue Regular Fit",
    "description": "Columbia windbreaker.",
    "price": 17,
    "brand": "Columbia",
    "department": "Boys",
    "seller_notes": ["Garment care is unverified and stays empty."],
    "reviewed_empty_fields": [{"marketplace": "depop", "field": "Delisting Attempt Messages"}],
    "direct_category_ids": {"depop": "kidswear_jackets"},
    "marketplace_category_ids": {"ebay": "260968", "etsy": "392", "sellwild": "183"},
    "ebay_specifics": {
        "type": "Jacket",
        "department": "Boys",
        "category_specifics": {"type": "Jacket", "size": "XL (18/20)", "outerShellMaterial": "Polyester"},
    },
    "depop_specifics": {"type": "Windbreaker", "material": ["Polyester"], "size": "Other"},
    "poshmark_specifics": {"size": "XL", "smartSellMinimumPrice": 13, "originalPrice": 0},
    "sellwild_specifics": {"categoryId": "183"},
}


def vendoo_item(stamp: int, *, title: str = "Columbia XL (18/20) Outdoors Hooded Windbreaker Blue Regular Fit") -> dict:
    return {
        "itemID": "yvg0DByYe0HAGsBZn79K",
        "dateLastModified": stamp,
        "generalDetails": {"title": title, "description": "Columbia windbreaker.", "price": "17", "brand": "Columbia"},
        "listings": {
            "ebay": {
                "type": "listing",
                "marketplaceId": "ebay",
                "listingUrl": "https://www.ebay.com/itm/377482978626",
                "overrides": {"categoryV2": {"id": "260968", "displayPath": ["Clothing", "Activewear Jackets"]}},
                "marketplaceSpecifics": {"statusItem": "Active", "conditionDescription": "SEE PHOTOS"},
                "categorySpecifics": {
                    "260968_Outer Shell Material": "Polyester",
                    "260968_StoreCategoryName": "Other:1",
                    "260968_StoreCategory2Name": "Other:1",
                },
            },
            "poshmark": {
                "type": "listing",
                "marketplaceId": "poshmark",
                "lastItemSharedAt": "2026-10-25T02:14:16.068Z",
                "overrides": {"categoryV2": {"id": POSH_LEAF, "displayPath": ["Kids", "Jackets & Coats"]}},
                "marketplaceSpecifics": {
                    "otherInfo": "Cost: $3.65 | Mat: $0.95 | Ship: $11",
                    "costPriceAmount": "1.24",
                    "styleTags": ["Sportswear"],
                },
                "categorySpecifics": {f"{POSH_LEAF}Size": "XLB", f"{POSH_LEAF}SizeScale": "boys"},
            },
            "depop": {
                "type": "listing",
                "marketplaceId": "depop",
                "lastItemSharedAt": "2026-10-25T02:11:37.116Z",
                "overrides": {"categoryV2": {"id": "kidswear_jackets"}},
                "marketplaceSpecifics": {"source": "Preloved"},
                "categorySpecifics": {
                    "kidswearJacketsAttributeJacketType": "windbreaker",
                    "kidswearJacketsAttributeMaterial": "polyester",
                },
            },
            "etsy": {
                "type": "listing",
                "overrides": {"categoryV2": {"id": "436"}},
                "marketplaceSpecifics": {"whoMade": "Another company or person"},
                "categorySpecifics": {"436_62809790395": "2022", "436_62809790395_scale": "62809790395:42"},
            },
        },
    }


class ImportKeepsVendooEncodingOutTest(unittest.TestCase):
    def test_vendoo_bookkeeping_and_encoded_keys_never_reach_the_specifics(self):
        listing = listing_from_vendoo(vendoo_item(1000), None)
        for name in ("ebay", "poshmark", "depop", "etsy", "mercari"):
            specifics = listing[f"{name}_specifics"]
            for key in ("marketplaceId", "lastItemSharedAt", "listingUrl", "statusItem", "otherInfo", "costPriceAmount"):
                self.assertNotIn(key, specifics, f"{name}.{key}")
            self.assertNotEqual(specifics.get("type"), "listing", name)
            for key in [*specifics, *(specifics.get("category_specifics") or {})]:
                self.assertFalse(key[:1].isdigit(), f"{name}.{key}")
                self.assertNotIn(POSH_LEAF, key)
                self.assertNotIn("Attribute", key)
        ebay = listing["ebay_specifics"]
        # A readable eBay aspect survives under Studio's own name.
        self.assertEqual(ebay["outerShellMaterial"], "Polyester")
        self.assertEqual(ebay["conditionDescription"], "SEE PHOTOS")
        self.assertEqual(listing["poshmark_specifics"]["styleTags"], "Sportswear")
        self.assertNotIn("category_specifics", listing["etsy_specifics"])

    def test_unprefixed_category_fields_still_import(self):
        listing = listing_from_vendoo(
            {"generalDetails": {"title": "Tee"}, "listings": {"etsy": {"categorySpecifics": {"sleeveLength": "Short"}}}},
            None,
        )
        self.assertEqual(listing["etsy_specifics"]["category_specifics"], {"sleeveLength": "Short"})


class PullAfterDirectListingTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.conv = ConversationRepo(self.db).create(title="Windbreaker")
        repo = ListingRepo(self.db)
        # Studio and Vendoo level at 06:14 (Update Vendoo)...
        self.synced = listing_persistence.save_revision(repo.db, self.conv.id, {**STUDIO_LISTING, "title": "Older title"}, source="user_form")
        synced_item = vendoo_item(1000, title="Older title")
        conv = ConversationRepo(self.db).get(self.conv.id)
        conv.notes = merge_notes(conv.notes, {
            "vendooItemId": "yvg0DByYe0HAGsBZn79K",
            SYNCED_AT: "1000",
            SYNCED_REVISION: self.synced.id,
            CONTENT_FINGERPRINT: item_content_fingerprint(synced_item),
        })
        self.db.commit()
        # ...then a refinement Studio published directly to eBay and Depop.
        self.listed = listing_persistence.save_revision(repo.db,
            self.conv.id, STUDIO_LISTING, source="model_refinement", parent_revision_id=self.synced.id,
        )

    def tearDown(self):
        self.db.close()

    def test_a_sync_never_replaces_the_published_revision(self):
        state = sync_state(self.db, self.conv.id, vendoo_item(2000, title="Edited in Vendoo"))
        self.assertEqual(state["action"], "conflict")

    def test_discard_merges_vendoo_over_studio_and_keeps_what_vendoo_lacks(self):
        revision_id = apply_pull(self.db, self.conv.id, vendoo_item(1000, title="Older title"), source="vendoo_pull")
        revision = ListingRepo(self.db).get_revisions(self.conv.id)[0]
        self.assertEqual(revision.id, revision_id)
        self.assertEqual(revision.parent_revision_id, self.listed.id)
        listing = revision.listing_json
        # Vendoo's form wins where it holds a value.
        self.assertEqual(listing["title"], "Older title")
        # What Vendoo does not hold stays.
        self.assertEqual(listing["department"], "Boys")
        self.assertEqual(listing["seller_notes"], STUDIO_LISTING["seller_notes"])
        self.assertEqual(listing["reviewed_empty_fields"], STUDIO_LISTING["reviewed_empty_fields"])
        self.assertEqual(listing["direct_category_ids"], {"depop": "kidswear_jackets"})
        self.assertEqual(listing["sellwild_specifics"], {"categoryId": "183"})
        self.assertEqual(listing["marketplace_category_ids"]["sellwild"], "183")
        ebay = listing["ebay_specifics"]
        self.assertEqual(ebay["type"], "Jacket")
        # Reading a listing folds eBay's category specifics into the section;
        # Studio's answers survive that, Vendoo's store categories do not appear.
        self.assertEqual(ebay["size"], "XL (18/20)")
        self.assertEqual(ebay["outerShellMaterial"], "Polyester")
        self.assertEqual(listing["depop_specifics"]["type"], "Windbreaker")
        self.assertEqual(listing["depop_specifics"]["material"], ["Polyester"])
        self.assertEqual(listing["poshmark_specifics"]["smartSellMinimumPrice"], 13)
        self.assertNotIn("otherInfo", listing["poshmark_specifics"])
        self.assertNotIn("listingUrl", ebay)
        self.assertFalse(any(key.startswith("260968_") for key in ebay))


if __name__ == "__main__":
    unittest.main()
