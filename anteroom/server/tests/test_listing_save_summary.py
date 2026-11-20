"""Saved-listing instructions name the editor and marketplace actions sellers can use."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from anteroom.services.listing_generate import listing_save_summary


@pytest.mark.parametrize("missing,discovered,waiting", [
    (0, 0, False), (2, 0, False), (0, 1, False), (2, 1, False),
    (0, 0, True), (2, 1, True),
])
def test_saved_listing_instructions_use_current_editor_actions(missing, discovered, waiting):
    with (
        patch("anteroom.services.listing_generate.ConversationRepo") as conversations,
        patch("anteroom.services.validation.validate_listing", return_value=SimpleNamespace(errors=[{}] * missing)),
        patch("anteroom.services.listing_field_gaps.remaining_discovered_gap_count", return_value=discovered),
        patch("anteroom.services.marketplaces.get_selected_marketplaces", return_value=["ebay"]),
    ):
        conversations.return_value.get_photos.return_value = []
        summary = listing_save_summary(None, "fixture", {}, waiting=waiting)

    assert summary.startswith("Listing saved")
    assert "General and marketplace tabs" in summary
    assert "List or Update" in summary
    assert "Forms" not in summary
    assert "Send" not in summary
    if missing:
        assert "2 required field(s) still missing" in summary
    if discovered:
        assert "some discovered fields are still empty" in summary
    if waiting:
        assert "questions outstanding" in summary
