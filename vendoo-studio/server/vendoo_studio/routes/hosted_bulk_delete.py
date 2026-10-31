"""Reviewed removal of selected unsent drafts. Nothing is delisted."""
from fastapi import APIRouter, Depends, HTTPException, Request

from vendoo_studio.services import bulk_delete, hosted_listing_delete
from vendoo_studio.services.background_tasks import require_idle
from vendoo_studio.services.hosted_database import get_seller_db

router = APIRouter(prefix='/api/listings/bulk-delete', tags=['inventory'])


def review(db, identity):
    row = bulk_delete.review_row(db, identity)
    if not row['skip_reason']:
        reason = hosted_listing_delete.blocker(db, identity)
        if reason:
            row.update(skip_reason=reason, review_token=None)
    return row


@router.post('/preview')
def preview(body: bulk_delete.PreviewRequest, db=Depends(get_seller_db)):
    """Review only. Another seller's item reads as missing."""
    return {'rows': [review(db, identity) for identity in dict.fromkeys(body.conversation_ids)]}


@router.post('/delete')
def delete(body: bulk_delete.DeleteRequest, request: Request, db=Depends(get_seller_db)):
    if body.confirm is not True:
        raise HTTPException(400, 'Review and confirm the items to delete first.')
    token = request.headers['authorization'].partition(' ')[2]
    deleted, skipped, seen = [], [], set()
    for reviewed in body.rows:
        identity = reviewed.conversation_id
        if identity in seen:
            continue
        seen.add(identity)
        try:
            db.expire_all()
            # Each item serializes on its own row lock, not a process-wide gate.
            require_idle(db, identity)
            current = review(db, identity)
            if current['skip_reason']:
                raise ValueError(current['skip_reason'])
            if current['review_token'] != reviewed.review_token:
                raise ValueError('This item changed since the review. Review again.')
            keys = hosted_listing_delete.remove_unsent_draft(db, identity)
            db.commit()
        except (ValueError, HTTPException) as error:
            db.rollback()
            skipped.append({'conversation_id': identity,
                            'reason': str(error.detail) if isinstance(error, HTTPException) else str(error)})
            continue
        hosted_listing_delete.release(keys, token)
        deleted.append(identity)
    return {'deleted': deleted, 'skipped': skipped}
