"""Reviewed account field edits, using the shared inventory editor."""
from fastapi import APIRouter, Depends, HTTPException

from vendoo_studio.services import bulk_edit
from vendoo_studio.services.hosted_database import get_seller_db

router = APIRouter(prefix='/api/listings/bulk-edit', tags=['inventory'])


def require_supported(edits):
    # Bulk categories need the browser-backed picker and its verified catalog.
    if edits.category and edits.category.model_dump(exclude_none=True):
        raise HTTPException(400, 'Bulk category changes are not available in this workspace yet')


@router.post('/preview')
def preview(body: bulk_edit.PreviewRequest, db=Depends(get_seller_db)):
    require_supported(body.edits)
    return bulk_edit.preview(db, body)


@router.post('/apply')
def apply(body: bulk_edit.ApplyRequest, db=Depends(get_seller_db)):
    require_supported(body.edits)
    return bulk_edit.apply(db, body)
