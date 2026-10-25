"""SKU, price, labels and listed marketplaces — what the sidebar filters on."""

from __future__ import annotations

import json
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vendoo_studio.database import Base
from vendoo_studio.models.fill_log import FillLogEntry  # noqa: F401
from vendoo_studio.models.registry import FieldRegistry  # noqa: F401
from vendoo_studio.repositories.queries import ConversationRepo, ListingRepo
from vendoo_studio.routes.conversations import _conv_response, _extras
from vendoo_studio.services.vendoo_import import (
    split_vendoo_labels,
    vendoo_listed_marketplaces,
    vendoo_listing_urls,
)


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
        self.listings.save_revision(conv.id, {"title": "Nike tee", "sku": "NIKE-TEE-M", "price": 24.5}, "test")
        facets = self.convs.listing_facets()
        expected = {"sku": "NIKE-TEE-M", "price": 24.5, "etsy_eligible": False}
        self.assertEqual(facets[conv.id], expected)
        self.assertEqual(self.convs.listing_facet(conv.id), expected)

    def test_facets_are_empty_without_a_listing(self):
        conv = self.convs.create(title="Bare draft")
        self.assertNotIn(conv.id, self.convs.listing_facets())
        # Nothing known about the item: no reason to hide Etsy.
        self.assertEqual(self.convs.listing_facet(conv.id), {"sku": None, "price": None, "etsy_eligible": True})

    def test_facets_read_etsy_eligibility_from_the_listing(self):
        modern = self.convs.create(title="Modern tee")
        self.listings.save_revision(modern.id, {"title": "Tee", "etsy_specifics": {
            "who_made": "Another company or person", "when_made": "2010 - 2019 (Recently)",
        }}, "test")
        vintage = self.convs.create(title="90s tee")
        self.listings.save_revision(vintage.id, {"title": "Tee", "etsy_specifics": {
            "who_made": "someone_else", "when_made": "1990s",
        }}, "test")
        dated_by_ebay = self.convs.create(title="80s tee")
        self.listings.save_revision(dated_by_ebay.id, {
            "title": "Tee",
            "etsy_specifics": {"who_made": "Another company or person"},
            "ebay_specifics": {"yearManufactured": "1985"},
        }, "test")
        facets = self.convs.listing_facets()
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
        self.listings.save_revision(conv.id, {"title": "Nike tee", "sku": "NIKE-TEE-M", "price": 24.5}, "test")
        response = _conv_response(conv, _extras(None, self.convs.listing_facet(conv.id)))
        self.assertEqual(response.sku, "NIKE-TEE-M")
        self.assertEqual(response.price, 24.5)
        self.assertEqual(response.vendoo_labels, ["A19", "To List"])
        self.assertEqual(response.vendoo_marketplaces, ["ebay", "etsy"])

    def test_listed_marketplaces_follow_vendoo_status_flags(self):
        item = {
            "listings": {
                "ebay": {"status": {"listed": True}},
                "poshmark": {"status": {"listed": False}},
                "etsy": {"status": {"sold": True}},
                "depop": {"status": {"shipped": True}},
                "vestiaireApi": {"status": {"listed": True}},
                "validate": {"status": {"listed": True}},
            }
        }
        self.assertEqual(
            vendoo_listed_marketplaces(item, None),
            ["depop", "ebay", "etsy", "vestiaire"],
        )

    def test_direct_listings_count_as_live_on_that_marketplace(self):
        item = {
            "listings": {"ebay": {"status": {"notListed": True}}},
            "externalListings": {
                "poshmark": {
                    "status": {"listed": True},
                    "listingURL": "https://poshmark.com/listing/windbreaker",
                },
                "mercari": {
                    "status": {"notListed": True},
                    "listingURL": "https://www.mercari.com/us/item/gone/",
                },
            },
        }
        self.assertEqual(vendoo_listed_marketplaces(item, None), ["poshmark"])
        self.assertEqual(
            vendoo_listing_urls(item, None),
            {"poshmark": "https://poshmark.com/listing/windbreaker"},
        )

    def test_listed_marketplaces_are_empty_without_listings(self):
        self.assertEqual(vendoo_listed_marketplaces(None, None), [])
        self.assertEqual(vendoo_listed_marketplaces({"listings": "nope"}, None), [])

    def test_listing_urls_come_from_live_and_sold_listings(self):
        item = {
            "listings": {
                "ebay": {"status": {"listed": True}, "listingURL": "https://www.ebay.com/itm/123"},
                "poshmark": {
                    "status": {"sold": True},
                    "listingURL": "https://poshmark.com/listing/abc",
                },
                # Delisted: the page is gone even if a stale URL lingers.
                "mercari": {"status": {"notListed": True}, "listingURL": "https://www.mercari.com/us/item/m1/"},
                "etsy": {"status": {"listed": True}, "listingURL": ""},
                "depop": {"status": {"listed": True}, "listingURL": "javascript:alert(1)"},
                "vestiaireApi": {"status": {"listed": True}, "listingURL": "https://vestiairecollective.com/x"},
            }
        }
        self.assertEqual(vendoo_listing_urls(item, None), {
            "ebay": "https://www.ebay.com/itm/123",
            "poshmark": "https://poshmark.com/listing/abc",
            "vestiaire": "https://vestiairecollective.com/x",
        })
        self.assertEqual(vendoo_listing_urls(None, None), {})

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
