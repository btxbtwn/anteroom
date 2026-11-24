"""Removing a listing, and everything hanging off it.

The sidebar's delete, Regenerate's wipe, and the bulk import's sweep over items
Vendoo no longer has all take the same cascade, so it lives here rather than in
the route that happened to need it first.
"""
from __future__ import annotations

import copy
import functools
import logging
from collections.abc import Callable
from types import SimpleNamespace

from sqlalchemy import event
from sqlalchemy.orm import Session

from anteroom.models.conversation import Message, Photo as PhotoModel
from anteroom.models.diagnostics import DiagnosticRun, FieldObservation
from anteroom.models.fill_log import FillLogEntry
from anteroom.models.job import ACTIVE_JOB_STATUSES, Job, JobEvent, VendooDraftCache
from anteroom.models.listing import Listing, ListingRevision
from anteroom.repositories.queries import ConversationRepo
from anteroom.services.photos import delete_photo_files

log = logging.getLogger("anteroom.listing_delete")


def remove_files_after_commit(db: Session, removals: list[Callable[[], None]]) -> None:
    """Remove files once the session commits the rows that named them.

    Deleting them first loses a photo whenever the commit is refused or fails:
    the row stays and its file is gone. A rollback drops the removals instead.
    """
    if not removals:
        return
    pending = list(removals)

    def committed(_session) -> None:
        todo = pending[:]
        pending.clear()
        for remove in todo:
            try:
                remove()
            except Exception:  # noqa: BLE001 - an orphaned file is safe; the commit stands
                log.warning("Could not remove a deleted listing's file", exc_info=True)

    def rolled_back(_session, _previous) -> None:
        pending.clear()

    event.listen(db, "after_commit", committed)
    event.listen(db, "after_soft_rollback", rolled_back)


class ListingBusy(RuntimeError):
    """A listing cannot be deleted while automation is running on it."""


def wipe_contents(db: Session, conv_id: str, *, keep_photos: bool = False,
                  keep_marketplace_listings: bool = False, keep_message_id: str | None = None) -> tuple[int, int]:
    """Delete a listing's jobs, revisions, messages and photos, keeping the row.

    ``keep_marketplace_listings`` keeps its direct marketplace records and the
    jobs they name: their snapshots are what Check reads a result against.
    ``keep_message_id`` keeps that one message (Regenerate's photo reading).
    """
    from anteroom.repositories.marketplace_listings import MarketplaceListingRepo
    from anteroom.services.background_tasks import delete_history

    delete_history(db, conv_id)

    records = MarketplaceListingRepo(db)
    kept_jobs = records.job_ids(conv_id) if keep_marketplace_listings else set()
    if not keep_marketplace_listings:
        records.delete_for_conversation(conv_id)
    repo = ConversationRepo(db)
    photos = [] if keep_photos else repo.get_all_photos(conv_id)
    deleted_photos = len(photos)
    removals: list[Callable[[], None]] = []
    if not keep_photos:
        from anteroom.models.listing_video import ListingVideo
        from anteroom.services.listing_videos import video_path

        for video in db.query(ListingVideo).filter_by(conversation_id=conv_id).all():
            removals.append(functools.partial(video_path(video).unlink, missing_ok=True))
            db.delete(video)
    for photo in photos:
        files = SimpleNamespace(stored_filename=photo.stored_filename, source_filename=photo.source_filename,
                                edits=copy.deepcopy(photo.edits))
        removals.append(lambda files=files: delete_photo_files(files))
    remove_files_after_commit(db, removals)

    job_ids = [row[0] for row in db.query(Job.id).filter(Job.conversation_id == conv_id).all() if row[0] not in kept_jobs]
    if job_ids:
        from anteroom.services.approved_photos import KEY, release

        retained = {photo["stored_filename"]
                    for (snapshot,) in db.query(Job.listing_snapshot).filter(Job.id.in_(kept_jobs))
                    for photo in (snapshot or {}).get(KEY, [])}
        for (snapshot,) in db.query(Job.listing_snapshot).filter(Job.id.in_(job_ids)):
            release(snapshot or {}, keep=retained)
        db.query(FieldObservation).filter(
            FieldObservation.diagnostic_run_id.in_(
                db.query(DiagnosticRun.id).filter(DiagnosticRun.job_id.in_(job_ids))
            )
        ).delete(synchronize_session=False)
        db.query(DiagnosticRun).filter(DiagnosticRun.job_id.in_(job_ids)).delete(synchronize_session=False)
        db.query(FillLogEntry).filter(FillLogEntry.job_id.in_(job_ids)).delete(synchronize_session=False)
        db.query(JobEvent).filter(JobEvent.job_id.in_(job_ids)).delete(synchronize_session=False)
        # Synced Vendoo status lives here, one row per job. Leaving it makes
        # DELETE FROM jobs fail the foreign key, and Regenerate never finishes.
        db.query(VendooDraftCache).filter(VendooDraftCache.job_id.in_(job_ids)).delete(synchronize_session=False)
        db.query(Job).filter(Job.id.in_(job_ids)).delete(synchronize_session=False)

    db.query(ListingRevision).filter(ListingRevision.conversation_id == conv_id).delete(synchronize_session=False)
    db.query(Listing).filter(Listing.conversation_id == conv_id).delete(synchronize_session=False)
    messages = db.query(Message).filter(Message.conversation_id == conv_id)
    if keep_message_id is not None:
        messages = messages.filter(Message.id != keep_message_id)
    messages.delete(synchronize_session=False)
    if not keep_photos:
        db.query(PhotoModel).filter(PhotoModel.conversation_id == conv_id).delete(synchronize_session=False)
    return len(job_ids), deleted_photos


def delete_listing(db: Session, conv_id: str) -> tuple[int, int]:
    """Delete a listing outright. Returns the jobs and photos that went with it.

    Raises ``LookupError`` when there is no such listing and ``ListingBusy``
    when automation is still running on it.
    """
    repo = ConversationRepo(db)
    conv = repo.get(conv_id)
    if conv is None:
        raise LookupError("Conversation not found")

    active_jobs = db.query(Job).filter(
        Job.conversation_id == conv_id,
        Job.status.in_(ACTIVE_JOB_STATUSES),
    ).all()
    if active_jobs:
        raise ListingBusy("Cannot delete a listing with an active automation job")

    deleted_jobs, deleted_photos = wipe_contents(db, conv_id)
    from anteroom.services import activity

    activity.clear_agents(conv_id)
    db.expire_all()
    conv = repo.get(conv_id)
    if conv is None:
        raise LookupError("Conversation not found")
    from anteroom.models.scout import ScoutCheck
    # The check stays as a record of the decision; only the link goes.
    db.query(ScoutCheck).filter(ScoutCheck.conversation_id == conv_id).update({ScoutCheck.conversation_id: None})
    from anteroom.models.listing_evidence import ListingCorrection, SaleSnapshot
    for model in (ListingCorrection, SaleSnapshot):
        db.query(model).filter_by(conversation_id=conv_id).delete(synchronize_session=False)
    db.delete(conv)
    db.commit()
    return deleted_jobs, deleted_photos
