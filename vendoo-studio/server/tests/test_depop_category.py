from __future__ import annotations

import copy
import unittest

from vendoo_studio.services.depop_category import align_depop_category

TANKS = "womenswear_vests-tanks-camis"
CROP = "womenswear_crop-top"
NODES = (
    ("womenswear", "", "Women", False, True),
    ("womenswear_tops", "womenswear", "Women > Tops", False, True),
    (TANKS, "womenswear_tops", "Women > Tops > Tank tops and camis", True, False),
    (CROP, "womenswear_tops", "Women > Tops > Crop tops", True, False),
)
# The Vassarette revision before the fix: the move reached two keys, not the other two.
STALE = {
    "title": "Vassarette tube top",
    "direct_category_ids": {"poshmark": "p1", "depop": TANKS},
    "marketplace_category_ids": {"depop": CROP, "mercari": "1957"},
    "marketplace_categories": {"depop": "Women > Tops > Crop tops"},
    "marketplace_category_objects": {"depop": {
        "id": TANKS, "path": ["womenswear", "womenswear_tops", TANKS],
        "displayPath": ["Women", "Tops", "Tank tops and camis"],
        "extras": {"productType": "vests-tanks-camis", "legacyCategoryId": 97, "variantSetId": "4"},
    }},
    "depop_specifics": {"categoryId": CROP, "category_path": ["Women", "Tops", "Crop tops"]},
    "seller_fields": ["depop_specifics.categoryId", "marketplace_category_ids.depop", "marketplace_categories.depop"],
}


class DepopCategoryTest(unittest.TestCase):
    def setUp(self):
        from vendoo_studio.database import SessionLocal
        from vendoo_studio.models.catalog import CategoryTreeNode

        self.db = SessionLocal()
        ids = [row[0] for row in NODES]
        existing = (self.db.query(CategoryTreeNode)
                    .filter(CategoryTreeNode.marketplace == "depop", CategoryTreeNode.category_id.in_(ids)).all())
        self.saved = [{column.name: getattr(row, column.name) for column in CategoryTreeNode.__table__.columns}
                      for row in existing]
        for row in existing:
            self.db.delete(row)
        self.db.flush()
        self.nodes = [CategoryTreeNode(marketplace="depop", category_id=cid, parent_id=parent, path=path,
                                       label=path.split(" > ")[-1], is_leaf=leaf, has_children=children,
                                       children_loaded=True) for cid, parent, path, leaf, children in NODES]
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

    def assert_on(self, listing: dict, category: str, breadcrumb: str):
        self.assertEqual(listing["direct_category_ids"]["depop"], category)
        self.assertEqual(listing["marketplace_category_ids"]["depop"], category)
        self.assertEqual(listing["depop_specifics"]["categoryId"], category)
        self.assertEqual(listing["marketplace_category_objects"]["depop"]["id"], category)
        self.assertEqual(listing["marketplace_category_objects"]["depop"]["displayPath"], breadcrumb.split(" > "))
        self.assertNotIn("extras", listing["marketplace_category_objects"]["depop"])
        self.assertEqual(listing["marketplace_categories"]["depop"], breadcrumb)

    def test_a_job_snapshot_sends_the_category_the_seller_set(self):
        listing = copy.deepcopy(STALE)
        self.assertTrue(align_depop_category(listing))
        self.assert_on(listing, CROP, "Women > Tops > Crop tops")
        self.assertEqual(listing["direct_category_ids"]["poshmark"], "p1")
        self.assertEqual(listing["marketplace_category_ids"]["mercari"], "1957")
        self.assertFalse(align_depop_category(listing))

    def test_locked_keys_that_disagree_change_nothing(self):
        listing = copy.deepcopy(STALE)
        listing["depop_specifics"]["categoryId"] = "womenswear_jumpers"
        self.assertFalse(align_depop_category(listing))
        self.assertEqual(listing["direct_category_ids"]["depop"], TANKS)

    def test_an_unlocked_resolved_id_is_left_alone(self):
        listing = copy.deepcopy(STALE)
        listing.pop("seller_fields")
        self.assertFalse(align_depop_category(listing))
        self.assertEqual(listing["direct_category_ids"]["depop"], TANKS)

    def test_one_changed_key_on_a_save_moves_every_key_and_drops_the_old_size(self):
        previous = {
            "direct_category_ids": {"depop": TANKS}, "marketplace_category_ids": {"depop": TANKS},
            "marketplace_categories": {"depop": "Women > Tops > Tank tops and camis"},
            "marketplace_category_objects": {"depop": copy.deepcopy(STALE["marketplace_category_objects"]["depop"])},
            "depop_specifics": {"categoryId": TANKS, "nativeSizeId": 6},
        }
        listing = copy.deepcopy(previous)
        listing["marketplace_category_ids"]["depop"] = CROP
        self.assertTrue(align_depop_category(listing, previous))
        self.assert_on(listing, CROP, "Women > Tops > Crop tops")
        self.assertNotIn("nativeSizeId", listing["depop_specifics"])

    def test_a_breadcrumb_change_moves_the_ids_and_keeps_a_size_chosen_with_it(self):
        previous = copy.deepcopy(STALE)
        previous.update(marketplace_category_ids={"depop": TANKS}, marketplace_categories={"depop": "Women > Tops > Tank tops and camis"},
                        depop_specifics={"categoryId": TANKS, "nativeSizeId": 6})
        listing = copy.deepcopy(previous)
        listing["marketplace_categories"]["depop"] = "Women > Tops > Crop tops"
        listing["depop_specifics"]["nativeSizeId"] = 17
        self.assertTrue(align_depop_category(listing, previous))
        self.assert_on(listing, CROP, "Women > Tops > Crop tops")
        self.assertEqual(listing["depop_specifics"]["nativeSizeId"], 17)

    def test_an_id_the_tree_does_not_know_drops_the_stale_object(self):
        previous = copy.deepcopy(STALE)
        previous["marketplace_category_ids"]["depop"] = TANKS
        listing = copy.deepcopy(previous)
        listing["marketplace_category_ids"]["depop"] = "womenswear_unknown-type"
        self.assertTrue(align_depop_category(listing, previous))
        self.assertEqual(listing["direct_category_ids"]["depop"], "womenswear_unknown-type")
        self.assertNotIn("depop", listing["marketplace_category_objects"])

    def test_a_save_that_does_not_touch_depop_changes_nothing(self):
        listing = copy.deepcopy(STALE)
        listing.pop("seller_fields")
        self.assertFalse(align_depop_category(copy.deepcopy(listing), listing))


if __name__ == "__main__":
    unittest.main()
