from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

from vendoo_studio.services import extension_connection as extension_routes
from extension_sources import background_source

EXTENSION_DIR = Path(__file__).resolve().parents[3] / "vendoo-extension"


class DiscoverAllFormFieldsTest(unittest.TestCase):
    def test_vendoo_get_timeout_allows_full_marketplace_walk(self) -> None:
        self.assertGreaterEqual(extension_routes.VENDOO_GET_TIMEOUT_SEC, 120)

if __name__ == "__main__":
    unittest.main()
