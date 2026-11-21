from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy.orm import Session

from anteroom.database import Base
from anteroom.models.catalog import CategoryTree, CategoryTreeNode
from anteroom.services import catalog_index as catalog
from anteroom.services.category_tree import MARKETPLACES
from worker_sessions import memory_engine, worker_sessions


class FingerprintRecheckTest(unittest.TestCase):
    def setUp(self):
        catalog.reset_catalog_index_cache()
        self.addCleanup(catalog.reset_catalog_index_cache)
        self.fp = {"trees": {}}
        with catalog._INDEX_LOCK:
            catalog._state().index = object()
            catalog._state().fingerprint = json.dumps(self.fp, sort_keys=True)
            catalog._state().stale = False
            catalog._SHARED_CATEGORIES.root = str(catalog.category_index_dir())
            catalog._SHARED_CATEGORIES.trees_fp = json.dumps(self.fp["trees"], sort_keys=True)
        meta = MagicMock()
        meta.is_file.return_value = True
        meta.read_text.return_value = catalog._state().fingerprint
        patcher = patch.object(catalog, "catalog_meta_path", return_value=meta)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_match_is_trusted_until_the_catalog_is_marked_stale(self):
        with patch.object(catalog, "fingerprint", return_value=self.fp) as fingerprint:
            catalog.ensure_catalog_index(MagicMock())
            catalog.ensure_catalog_index(MagicMock())
            self.assertEqual(fingerprint.call_count, 1)

            catalog.mark_catalog_index_stale()
            with patch.object(catalog, "rebuild_catalog_index", return_value={"ok": True}) as rebuild:
                catalog.ensure_catalog_index(MagicMock())
            self.assertEqual(fingerprint.call_count, 2)
            rebuild.assert_called_once()

    def test_a_match_is_checked_again_after_a_while(self):
        with (
            patch.object(catalog, "fingerprint", return_value=self.fp) as fingerprint,
            patch.object(catalog, "FINGERPRINT_RECHECK_SEC", 0.0),
        ):
            catalog.ensure_catalog_index(MagicMock())
            catalog.ensure_catalog_index(MagicMock())
        self.assertEqual(fingerprint.call_count, 2)

    def test_stale_shared_trees_trigger_rebuild_even_when_seller_fingerprint_matches(self):
        with catalog._INDEX_LOCK:
            catalog._SHARED_CATEGORIES.trees_fp = '{"ebay": {"content_hash": "old"}}'
        with (
            patch.object(catalog, "fingerprint", return_value=self.fp),
            patch.object(catalog, "rebuild_catalog_index", return_value={"ok": True}) as rebuild,
        ):
            catalog.ensure_catalog_index(MagicMock())
        rebuild.assert_called_once()


class FingerprintQueryTest(unittest.TestCase):
    """Bulk regenerate calls fingerprint on every draft. Counting leaves scans
    tens of thousands of shared rows and cancels under the hosted 15s timeout.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._data = os.environ.get("ANTEROOM_DATA_DIR")
        os.environ["ANTEROOM_DATA_DIR"] = self.tmp.name
        catalog.reset_catalog_index_cache()
        self.engine = memory_engine()
        Base.metadata.create_all(self.engine)
        self.enterContext(worker_sessions(self.engine))
        self.db = Session(self.engine)
        for marketplace in MARKETPLACES:
            self.db.add(CategoryTree(
                marketplace=marketplace,
                status="complete",
                roots_loaded=True,
                content_hash=f"hash-{marketplace}",
            ))
            # Enough nodes that a leaf-count fingerprint would be expensive on
            # hosted Postgres; the fingerprint must ignore them entirely.
            for index in range(40):
                self.db.add(CategoryTreeNode(
                    marketplace=marketplace,
                    category_id=f"{marketplace}-{index}",
                    parent_id="__root",
                    label=f"Leaf {index}",
                    path=f"Root > Leaf {index}",
                    is_leaf=True,
                    has_children=False,
                    children_loaded=True,
                ))
        self.db.commit()

    def tearDown(self):
        catalog.reset_catalog_index_cache()
        self.db.close()
        self.engine.dispose()
        self.tmp.cleanup()
        if self._data is None:
            os.environ.pop("ANTEROOM_DATA_DIR", None)
        else:
            os.environ["ANTEROOM_DATA_DIR"] = self._data

    def test_fingerprint_reads_tree_content_hash_without_counting_leaves(self):
        with patch.object(
            catalog, "_leaf_query", side_effect=AssertionError("must not count category leaves"),
        ):
            fp = catalog.fingerprint(self.db)
        self.assertEqual(
            {marketplace: info["content_hash"] for marketplace, info in fp["trees"].items()},
            {marketplace: f"hash-{marketplace}" for marketplace in MARKETPLACES},
        )
        for info in fp["trees"].values():
            self.assertNotIn("leaves", info)


if __name__ == "__main__":
    unittest.main()
