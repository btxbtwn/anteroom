"""Optional Vendoo draft and review actions; never publishes a listing."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from vendoo_studio.database import get_db
from vendoo_studio.services import vendoo_drafts

router = APIRouter(prefix="/api/conversations/{conv_id}/vendoo-draft", tags=["vendoo-drafts"])


class DraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision_id: str


@router.get("")
def status(conv_id: str, db: Session = Depends(get_db, scope="function")):
    return vendoo_drafts.status(db, conv_id)


@router.post("")
async def send(conv_id: str, body: DraftRequest, db: Session = Depends(get_db, scope="function")):
    return await vendoo_drafts.start(db, conv_id, body.revision_id)


@router.post("/check")
async def check(conv_id: str, db: Session = Depends(get_db, scope="function")):
    return await vendoo_drafts.check(db, conv_id)
