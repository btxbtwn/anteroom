"""SKU, price, labels and listed marketplaces — what the sidebar filters on."""

from __future__ import annotations

import json
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vendoo_studio.services import inventory_facets
from vendoo_studio.services import listing_persistence
from vendoo_studio.database import Base
from vendoo_studio.models.fill_log import FillLogEntry  # noqa: F401
from vendoo_studio.models.marketplace_listing import MarketplaceListing  # noqa: F401
from vendoo_studio.models.registry import FieldRegistry  # noqa: F401
from vendoo_studio.repositories.queries import ConversationRepo, ListingRepo
from vendoo_studio.routes.conversations import _conv_response, _extras
from vendoo_studio.services.vendoo_import import split_vendoo_labels


class ListingFacetsTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.convs = ConversationRepo(self.db)
        self.listings = ListingRepo(self.db)

    def tearDown(self):
        self.db.close()

    def test_facets_read_sku_and_price_from_the_current_revision(self):
        conv = self.convs.create(title="Nike tee")
        listing_persistence.save_revision(self.listings.db, conv.id, {"title": "Nike tee", "sku": "NIKE-TEE-M", "price": 24.5}, "test")
        facets = inventory_facets.listing_facets(self.convs.db)
        expected = {"sku": "NIKE-TEE-M", "price": 24.5, "etsy_eligible": False}
        self.assertEqual(facets[conv.id], expected)
        self.assertEqual(inventory_facets.listing_facet(self.convs.db, conv.id), expected)

    def test_facets_are_empty_without_a_listing(self):
        conv = self.convs.create(title="Bare draft")
        self.assertNotIn(conv.id, inventory_facets.listing_facets(self.convs.db))
        # Nothing known about the item: no reason to hide Etsy.
        self.assertEqual(inventory_facets.listing_facet(self.convs.db, conv.id), {"sku": None, "price": None, "etsy_eligible": True})

    def test_facets_read_etsy_eligibility_from_the_listing(self):
        modern = self.convs.create(title="Modern tee")
        listing_persistence.save_revision(self.listings.db, modern.id, {"title": "Tee", "etsy_specifics": {
            "who_made": "Another company or person", "when_made": "2010 - 2019 (Recently)",
        }}, "test")
        vintage = self.convs.create(title="90s tee")
        listing_persistence.save_revision(self.listings.db, vintage.id, {"title": "Tee", "etsy_specifics": {
            "who_made": "someone_else", "when_made": "1990s",
        }}, "test")
        dated_by_ebay = self.convs.create(title="80s tee")
        listing_persistence.save_revision(self.listings.db, dated_by_ebay.id, {
            "title": "Tee",
            "etsy_specifics": {"who_made": "Another company or person"},
            "ebay_specifics": {"yearManufactured": "1985"},
        }, "test")
        facets = inventory_facets.listing_facets(self.convs.db)
        self.assertFalse(facets[modern.id]["etsy_eligible"])
        self.assertTrue(facets[vintage.id]["etsy_eligible"])
        self.assertTrue(facets[dated_by_ebay.id]["etsy_eligible"])
        self.assertFalse(_conv_response(modern, _extras(None, facets[modern.id])).etsy_eligible)
        self.assertTrue(_conv_response(vintage, _extras(None, facets[vintage.id])).etsy_eligible)
        self.assertTrue(_conv_response(modern).etsy_eligible)

    def test_response_carries_labels_marketplaces_sku_and_price(self):
        conv = self.convs.create(title="Nike tee")
        conv.notes = json.dumps({
            "vendooLabels": "A19, To List",
            "vendooMarketplaces": ["ebay", "etsy"],
        })
        self.db.commit()
        listing_persistence.save_revision(self.listings.db, conv.id, {"title": "Nike tee", "sku": "NIKE-TEE-M", "price": 24.5}, "test")
        response = _conv_response(conv, _extras(None, inventory_facets.listing_facet(self.convs.db, conv.id)))
        self.assertEqual(response.sku, "NIKE-TEE-M")
        self.assertEqual(response.price, 24.5)
        self.assertEqual(response.vendoo_labels, ["A19", "To List"])
        self.assertEqual(response.vendoo_marketplaces, ["ebay", "etsy"])
        self.assertEqual(response.direct_marketplaces, [])

    def test_response_names_marketplaces_anteroom_published(self):
        from vendoo_studio.repositories.marketplace_listings import MarketplaceListingRepo

        conv = self.convs.create(title="Columbia")
        conv.status = "draft"
        conv.notes = json.dumps({"vendooStatus": "draft", "vendooMarketplaces": []})
        self.db.commit()
        records = MarketplaceListingRepo(self.db)
        records.save(conv.id, "depop", {"state": "published"})
        records.save(conv.id, "etsy", {"state": "not_saved"})
        records.save(conv.id, "mercari", {"state": "pending_review"})
        live = records.live_marketplaces()
        response = _conv_response(conv, _extras(None, None, None, False, live[conv.id]))
        self.assertEqual(sorted(response.direct_marketplaces), ["depop", "mercari"])
        self.assertEqual(response.vendoo_marketplaces, [])
        self.assertEqual(response.status, "draft")

    def test_response_names_marketplaces_anteroom_delisted(self):
        from vendoo_studio.repositories.marketplace_listings import MarketplaceListingRepo

        conv = self.convs.create(title="Columbia")
        conv.status = "active"
        conv.notes = json.dumps({
            "vendooStatus": "active",
            "vendooMarketplaces": ["depop", "ebay", "mercari"],
            "vendooListingUrls": {
                "ebay": "https://www.ebay.com/itm/123456789012",
                "depop": "https://www.depop.com/products/seller-columbia-jacket/",
                "mercari": "https://www.mercari.com/us/item/m12345678901/",
            },
            "marketplaceDelists": {
                "ebay": {"state": "deleted", "listing_id": "123456789012"},
                "depop": {"state": "deleted", "listing_id": "seller-columbia-jacket"},
                # Relisted on Mercari since: a newer listing is live again.
                "mercari": {"state": "deleted", "listing_id": "m99999999999"},
            },
            "marketplaceMarks": {"poshmark": {"state": "not_listed"}},
        })
        self.db.commit()
        records = MarketplaceListingRepo(self.db)
        # Anteroom republished on Depop after the delete.
        records.save(conv.id, "depop", {"state": "published",
                                        "listing_url": "https://www.depop.com/products/seller-columbia-jacket-2/"})
        response = _conv_response(conv, _extras(None, None, None, False, ["depop"], records.listing_urls()[conv.id]))
        self.assertEqual(response.removed_marketplaces, ["ebay", "poshmark"])
        self.assertEqual(response.status, "active")





    def test_response_carries_listing_urls(self):
        conv = self.convs.create(title="Nike tee")
        conv.notes = json.dumps({"vendooListingUrls": {"ebay": "https://www.ebay.com/itm/123", "etsy": ""}})
        self.db.commit()
        response = _conv_response(conv)
        self.assertEqual(response.vendoo_listing_urls, {"ebay": "https://www.ebay.com/itm/123"})

    def test_labels_split_on_commas(self):
        self.assertEqual(split_vendoo_labels(" A19 , To List ,"), ["A19", "To List"])
        self.assertEqual(split_vendoo_labels(None), [])


if __name__ == "__main__":
    unittest.main()
