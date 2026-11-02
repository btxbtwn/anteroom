"""Upgrading retires old Sends without dispatching or cancelling direct actions."""
from importlib import import_module

from vendoo_studio.database import SessionLocal
from vendoo_studio.models.conversation import Conversation
from vendoo_studio.models.job import Job


def test_upgrade_cancels_only_unfinished_vendoo_jobs(monkeypatch):
    migration = import_module("vendoo_studio.migrations.versions.e5b1d7a3c920_cancel_leftover_vendoo_sends")
    with SessionLocal() as db:
        conversations = [Conversation(title="Retired send", status="listing"),
                         Conversation(title="Direct draft", status="listing")]
        db.add_all(conversations)
        db.flush()
        old = [Job(conversation_id=conversations[0].id, approved_revision_id="historical",
                   listing_snapshot={"title": "Stored history"}, status=status, current_step=step)
               for status, step in [("queued", None), ("dispatched", "vendoo_api_save"),
                                    ("awaiting_extension", "filling_general")]]
        direct = Job(conversation_id=conversations[1].id, approved_revision_id="direct",
                     listing_snapshot={}, status="dispatched", current_step="native_poshmark_save_draft")
        completed = Job(conversation_id=conversations[0].id, approved_revision_id="historical",
                        listing_snapshot={}, status="completed", current_step="vendoo_api_created")
        db.add_all([*old, direct, completed])
        db.flush()
        monkeypatch.setattr(migration.op, "get_bind", db.connection)
        migration.upgrade()
        db.expire_all()
        assert all(job.status == "cancelled" for job in old)
        assert old[0].listing_snapshot == {"title": "Stored history"}
        assert direct.status == "dispatched"
        assert completed.status == "completed"
        assert conversations[0].status == "draft"
        assert conversations[1].status == "listing"
        db.rollback()
