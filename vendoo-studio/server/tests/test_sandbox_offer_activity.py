"""Native offer screenshots use paused, seller-owned sample history, never sends."""
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from vendoo_studio.database import Base
from vendoo_studio.models.listing_promotion import ListingPromotion
from vendoo_studio.models.promotion_automation import PromotionAutomation, PromotionRun
from vendoo_studio.sandbox_seed import _seed_offer_activity
from vendoo_studio.services.promotion_automations import activity


def test_offer_history_fixture_is_terminal_owned_and_keeps_rule_paused(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    owner = "11111111-1111-1111-1111-111111111111"
    with Session(engine) as db:
        db.add(PromotionAutomation(id="sample-rule", user_id=owner, name="Sample", marketplace="poshmark",
                                   action="offer", enabled=False))
        db.commit()
    monkeypatch.setenv("ANTEROOM_SANDBOX", "1")
    with patch("vendoo_studio.database.engine", engine):
        _seed_offer_activity("sample-rule")
    with Session(engine) as db:
        rule = db.get(PromotionAutomation, "sample-rule")
        assert rule is not None and not rule.enabled and rule.next_run_at is None
        rows = db.query(ListingPromotion).all()
        assert len(rows) == 2
        assert {row.confirmation for row in rows} == {"request_accepted", "legacy_unverified"}
        assert all(row.user_id == owner and row.state == "done" and row.recipient_count is None for row in rows)
        run = db.query(PromotionRun).one()
        assert run.user_id == owner and run.sent == 2 and run.skipped == run.failed == 0
        assert run.finished_at is not None
        result = activity(db, "sample-rule")
        assert len(result["sends"]) == 2
        assert result["runs"][0]["sent"] == 2


def test_offer_history_fixture_refuses_non_sandbox(monkeypatch):
    monkeypatch.delenv("ANTEROOM_SANDBOX", raising=False)
    with pytest.raises(RuntimeError, match="require sandbox mode"):
        _seed_offer_activity("never-read")
