"""Seller-managed templates and explicit local application."""
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from vendoo_studio.database import get_db
from vendoo_studio.services import listing_templates
from vendoo_studio.services.listing_copy import CopyError, copy_result

router = APIRouter(prefix="/api/listing-templates", tags=["listing-templates"])


def _run(operation):
    try:
        return operation()
    except CopyError as exc:
        raise HTTPException(exc.status, str(exc), headers={"Cache-Control": "no-store"}) from exc


@router.get("")
def templates(response: Response, db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return {"templates": listing_templates.list_templates(db)}


@router.get("/source/{conv_id}")
def source(conv_id: str, response: Response, db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return _run(lambda: listing_templates.source_fields(db, conv_id))


@router.post("")
def create(body: listing_templates.TemplateSave, db: Session = Depends(get_db)):
    return _run(lambda: listing_templates.save(db, body))


@router.put("/{template_id}")
def update(template_id: str, body: listing_templates.TemplateSave, db: Session = Depends(get_db)):
    return _run(lambda: listing_templates.save(db, body, template_id))


@router.delete("/{template_id}")
def remove(template_id: str, expected_revision: int = Query(ge=1), db: Session = Depends(get_db)):
    return _run(lambda: listing_templates.remove(db, template_id, expected_revision))


@router.post("/{template_id}/draft")
def create_draft(template_id: str, body: listing_templates.TemplateDraft, db: Session = Depends(get_db)):
    return {"conversation_id": _run(lambda: listing_templates.create_draft(db, template_id, body))}


@router.get("/{template_id}/draft-result/{request_id}")
def draft_result(template_id: str, request_id: str, response: Response, db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return {"conversation_id": _run(lambda: copy_result(db, template_id, request_id, source_kind="template"))}


@router.post("/{template_id}/apply-preview")
def preview_apply(template_id: str, body: listing_templates.TemplateApply, db: Session = Depends(get_db)):
    return _run(lambda: listing_templates.preview_apply(db, template_id, body))


@router.post("/{template_id}/apply")
def apply(template_id: str, body: listing_templates.TemplateApproval, db: Session = Depends(get_db)):
    return _run(lambda: listing_templates.apply(db, template_id, body))
