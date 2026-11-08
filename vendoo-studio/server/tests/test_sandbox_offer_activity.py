"""Native offer screenshots use paused, seller-owned sample history, never sends."""
import os
import subprocess
import sys
from pathlib import Path
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


def test_offer_history_fixture_loads_models_in_fresh_process(tmp_path):
    # The API creates the rule in another process before the CLI seed reads it.
    # Do not let pytest's app imports register models inside that seed process.
    url = f"sqlite:///{tmp_path / 'fixture.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(PromotionAutomation(id="fresh-rule", name="Sample", marketplace="poshmark",
                                   action="offer", enabled=False))
        db.commit()
    env = dict(os.environ, ANTEROOM_SANDBOX="1", ANTEROOM_DATABASE_URL=url,
               VENDOO_STUDIO_DATA_DIR=str(tmp_path / "data"),
               PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    env.pop("ANTEROOM_TEST_DATABASE_URL", None)
    result = subprocess.run([sys.executable, "-c", """
import sys
from vendoo_studio.sandbox_seed import _seed_offer_activity
assert 'vendoo_studio.main' not in sys.modules
assert 'vendoo_studio.models.listing_video' not in sys.modules
_seed_offer_activity('fresh-rule')
assert 'vendoo_studio.main' not in sys.modules
"""], env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    with Session(engine) as db:
        rule = db.get(PromotionAutomation, "fresh-rule")
        assert rule is not None and not rule.enabled and rule.next_run_at is None
        rows = db.query(ListingPromotion).all()
        assert len(rows) == 2 and all(row.state == "done" for row in rows)
        assert db.query(PromotionRun).one().sent == 2
