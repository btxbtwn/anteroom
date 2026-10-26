from __future__ import annotations

import unittest

from vendoo_studio.models.ebay_fields import EBAY_CONDITION_DESCRIPTION
from vendoo_studio.services.native_form import vendoo_form

LISTING = {
    "title": "Tee",
    "description": "Soft cotton",
    "price": 18,
    "brand": "Hanes",
    "condition": "Pre-Owned - Good",
}


class NativeFormTest(unittest.TestCase):
    def test_direct_form_carries_the_defaults_vendoo_would_write(self):
        mercari = vendoo_form(LISTING, "mercari")
        self.assertIn("Ground Advantage", mercari["specifics"]["shippingLabel"])
        self.assertEqual(mercari["specifics"]["shipping"]["payerId"], 1)
        self.assertEqual(mercari["overrides"]["brand"], "Hanes")

        ebay = vendoo_form(LISTING, "ebay")
        self.assertEqual(ebay["specifics"]["conditionDescription"], EBAY_CONDITION_DESCRIPTION)
        self.assertEqual(ebay["specifics"]["pricingFormat"], "FixedPriceItem")
        self.assertTrue(ebay["specifics"]["pricingFormatDetails"]["fixedPrice"]["allowBestOffer"])

        poshmark = vendoo_form({**LISTING, "price": 20}, "poshmark")
        self.assertTrue(poshmark["specifics"]["smartSell"]["enabled"])
        self.assertTrue(poshmark["specifics"]["smartSell"]["minPrice"])


class MercariFormTest(unittest.TestCase):
    """Kids > Boys coats & jackets (1881) is a Mercari parent Vendoo's tree calls a leaf."""

    def setUp(self):
        from vendoo_studio.database import SessionLocal
        from vendoo_studio.models.catalog import CategoryTreeNode

        self.db = SessionLocal()
        self.nodes = [
            CategoryTreeNode(marketplace="mercari", category_id=cid, parent_id=parent, label=path.split(" > ")[-1],
                             path=path, is_leaf=True, has_children=children, children_loaded=True)
            for cid, parent, path, children in (
                ("1881", "3", "Kids > Boys coats & jackets", True),
                ("1931", "1881", "Kids > Boys coats & jackets > Boys (4+)", False),
            )
        ]
        # Another test may already have seeded the Mercari tree in this database: set it aside.
        ids = [node.category_id for node in self.nodes]
        existing = (
            self.db.query(CategoryTreeNode)
            .filter(CategoryTreeNode.marketplace == "mercari", CategoryTreeNode.category_id.in_(ids))
            .all()
        )
        self.saved = [
            {column.name: getattr(row, column.name) for column in CategoryTreeNode.__table__.columns}
            for row in existing
        ]
        for row in existing:
            self.db.delete(row)
        self.db.flush()
        self.db.add_all(self.nodes)
        self.db.commit()

    def tearDown(self):
        from vendoo_studio.models.catalog import CategoryTreeNode

        for node in self.nodes:
            self.db.delete(node)
        self.db.flush()
        self.db.add_all(CategoryTreeNode(**row) for row in self.saved)
        self.db.commit()
        self.db.close()

    def test_a_mercari_parent_is_never_the_leaf_the_tree_answers(self):
        from vendoo_studio.services.vendoo_create import tree_leaf

        self.assertIsNone(tree_leaf("mercari", "Kids > Boys coats & jackets"))
        self.assertEqual(tree_leaf("mercari", "Kids > Boys coats & jackets > Boys (4+)")["id"], "1931")

    def test_direct_mercari_does_not_read_vendoo_fields_or_mirrored_sizes(self):
        from unittest import mock

        from vendoo_studio.services import native_form

        listing = {**LISTING, "size": "XL (18/20)", "direct_category_ids": {"mercari": "1931"},
                   "marketplace_category_ids": {"mercari": "1881"}}
        with mock.patch.object(native_form, "vendoo_form") as vendoo, \
                mock.patch("vendoo_studio.services.user_settings.get_ships_from_zip", return_value=""):
            snapshot = native_form.attach_form(listing, "mercari")
        vendoo.assert_not_called()
        self.assertNotIn("sizes", snapshot["form"])
        self.assertEqual(snapshot["direct_category_ids"]["mercari"], "1931")
