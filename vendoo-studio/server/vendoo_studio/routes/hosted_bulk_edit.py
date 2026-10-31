"""Reviewed account field edits, using the shared inventory editor."""
from fastapi import APIRouter, Depends, HTTPException

from vendoo_studio.services import bulk_edit
from vendoo_studio.services.category_tree import categories_ready
from vendoo_studio.services.hosted_database import get_seller_db

router = APIRouter(prefix='/api/listings/bulk-edit', tags=['inventory'])


def require_supported(db, edits):
    chosen = edits.category.model_dump(exclude_none=True) if edits.category else {}
    if not chosen:
        return
    if not categories_ready(db):
        raise HTTPException(400, 'Bulk category changes are not available in this workspace yet')
    # Desktop takes eBay's pick from its own search; here it must be a synced eBay leaf.
    ebay = edits.category.ebay
    if ebay:
        leaf = bulk_edit.verified_leaf(db, 'ebay', ebay.id, 'eBay')
        if leaf['path'].split(' > ') != [part.strip() for part in ebay.path.split(' › ')]:
            raise HTTPException(400, 'Choose an eBay category from the list.')


@router.post('/preview')
def preview(body: bulk_edit.PreviewRequest, db=Depends(get_seller_db)):
    require_supported(db, body.edits)
    return bulk_edit.preview(db, body)


@router.post('/apply')
def apply(body: bulk_edit.ApplyRequest, db=Depends(get_seller_db)):
    require_supported(db, body.edits)
    return bulk_edit.apply(db, body)
