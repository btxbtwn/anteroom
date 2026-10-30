from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from vendoo_studio.services import catalog_index as catalog


class FingerprintRecheckTest(unittest.TestCase):
    def setUp(self):
        catalog.reset_catalog_index_cache()
        self.addCleanup(catalog.reset_catalog_index_cache)
        self.fp = {"trees": {}}
        with catalog._INDEX_LOCK:
            catalog._state().index = object()
            catalog._state().fingerprint = json.dumps(self.fp, sort_keys=True)
            catalog._state().stale = False
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


if __name__ == "__main__":
    unittest.main()
