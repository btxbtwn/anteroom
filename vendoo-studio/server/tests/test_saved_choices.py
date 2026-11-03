"""Account choices a form shows open from the seller's saved copy, then refresh behind it."""
from __future__ import annotations

import asyncio
import time
import unittest
from unittest import mock

from test_hosted_drafts import cloud  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import jwks, signing_keys  # noqa: F401
from vendoo_studio.services import saved_choices


class SavedChoicesTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        self.folder = tempfile.TemporaryDirectory()
        self.enterContext(mock.patch.object(saved_choices, "_path", return_value=Path(self.folder.name) / "choices.json"))
        self.addCleanup(self.folder.cleanup)
        self.reads = 0

    async def read(self, value):
        self.reads += 1
        return value

    async def test_the_first_open_reads_and_later_opens_show_the_saved_copy_at_once(self):
        first = await saved_choices.kept("ebay-policies", "acct-1", lambda: self.read({"payment": ["p1"]}))
        self.assertEqual(first, {"payment": ["p1"]})

        async def slow():
            await asyncio.sleep(5)
            return {"payment": ["never"]}

        began = time.monotonic()
        again = await saved_choices.kept("ebay-policies", "acct-1", slow)
        self.assertLess(time.monotonic() - began, 0.5)
        self.assertEqual(again, {"payment": ["p1"]})
        self.assertEqual(self.reads, 1)

    async def test_an_old_copy_shows_at_once_and_is_replaced_in_the_background(self):
        await saved_choices.kept("sellwild-account", "", lambda: self.read({"ship_from": "Old St"}))
        with mock.patch.object(saved_choices, "REFRESH_AFTER_S", 0):
            shown = await saved_choices.kept("sellwild-account", "", lambda: self.read({"ship_from": "New St"}),
                                             background=saved_choices.on_this_loop)
            self.assertEqual(shown, {"ship_from": "Old St"})
            for _ in range(100):
                if self.reads == 2 and saved_choices.saved("sellwild-account", "") == {"ship_from": "New St"}:
                    break
                await asyncio.sleep(0.02)
        self.assertEqual(saved_choices.saved("sellwild-account", ""), {"ship_from": "New St"})

    async def test_a_failed_read_is_never_saved(self):
        async def fails():
            raise OSError("Chrome is closed")

        with self.assertRaises(OSError):
            await saved_choices.kept("vinted-categories", "", fails)
        self.assertIsNone(saved_choices.saved("vinted-categories", ""))
        unusable = await saved_choices.kept("sellwild-account", "", lambda: self.read({"unavailable": ["address"]}),
                                            usable=lambda value: not value["unavailable"])
        self.assertEqual(unusable, {"unavailable": ["address"]})
        self.assertIsNone(saved_choices.saved("sellwild-account", ""))

    async def test_another_account_never_sees_these_choices(self):
        await saved_choices.kept("ebay-policies", "acct-1", lambda: self.read({"payment": ["p1"]}))
        other = await saved_choices.kept("ebay-policies", "acct-2", lambda: self.read({"payment": ["p2"]}))
        self.assertEqual(other, {"payment": ["p2"]})
        self.assertIsNone(saved_choices.saved("ebay-policies", "acct-1"))


class EbayFormUsesSavedPoliciesTest(unittest.IsolatedAsyncioTestCase):
    async def test_the_ebay_form_reads_policies_through_the_saved_copy(self):
        from vendoo_studio.services import ebay_listings, native_editor

        profiles = {"payment": [{"id": "p1", "title": "Pay"}], "fulfillment": [], "return": [], "locations": []}
        kept = mock.AsyncMock(return_value=profiles)
        with mock.patch.object(saved_choices, "kept", new=kept), \
                mock.patch.object(ebay_listings, "listing_profiles", new=mock.AsyncMock(side_effect=AssertionError)), \
                mock.patch("vendoo_studio.services.marketplace_accounts.account_id", new=mock.AsyncMock(return_value="acct-1")):
            rows = await native_editor._ebay_choices({})
        self.assertEqual(kept.await_args.args[:2], ("ebay-policies", "acct-1"))
        self.assertEqual(rows[0]["option_values"], {"Pay": "p1"})


# On the web the saved copy lives in each seller's own preferences row (same table as sourcing).


def test_web_saved_choices_are_private_to_each_seller(cloud):  # noqa: F811
    from test_hosted_drafts import BOB
    from test_seller_sessions import USER_ID
    from vendoo_studio.services.hosted_context import for_seller
    from vendoo_studio.services.service_context import use

    _client, _engine = cloud
    alice_reads, bob_reads = [], []

    async def alice_value():
        alice_reads.append(1)
        return {"payment": ["alice"]}

    async def bob_value():
        bob_reads.append(1)
        return {"payment": ["bob"]}

    async def run():
        with use(for_seller(USER_ID)):
            assert await saved_choices.kept("ebay-policies", "acct", alice_value) == {"payment": ["alice"]}
            assert await saved_choices.kept("ebay-policies", "acct", alice_value) == {"payment": ["alice"]}
        with use(for_seller(BOB)):
            # Bob never sees Alice's copy; his first open still reads.
            assert await saved_choices.kept("ebay-policies", "acct", bob_value) == {"payment": ["bob"]}
            assert saved_choices.saved("ebay-policies", "acct") == {"payment": ["bob"]}
        with use(for_seller(USER_ID)):
            assert saved_choices.saved("ebay-policies", "acct") == {"payment": ["alice"]}

    asyncio.run(run())
    assert alice_reads == [1]
    assert bob_reads == [1]
