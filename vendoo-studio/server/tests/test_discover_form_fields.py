from __future__ import annotations

import unittest

from vendoo_studio.services import extension_connection as extension_routes



class DiscoverAllFormFieldsTest(unittest.TestCase):
    def test_vendoo_get_timeout_allows_full_marketplace_walk(self) -> None:
        self.assertGreaterEqual(extension_routes.VENDOO_GET_TIMEOUT_SEC, 120)

if __name__ == "__main__":
    unittest.main()
