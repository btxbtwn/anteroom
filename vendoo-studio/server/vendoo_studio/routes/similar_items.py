from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from vendoo_studio.database import get_db
from vendoo_studio.services import similar_items

router = APIRouter(prefix="/api/similar-items", tags=["similar-items"])


class SimilarItem(BaseModel):
    id: str
    title: str
    status: str
    brand: str | None
    size: str | None
    price: float | None
    sku: str | None
    cover_url: str | None


class SimilarPair(BaseModel):
    first: SimilarItem
    second: SimilarItem
    reasons: list[str]
    score: float
    photo_distance: int | None
    title_score: float | None


class SimilarItems(BaseModel):
    total: int
    pairs: list[SimilarPair]


class PairRequest(BaseModel):
    first_id: str
    second_id: str


class MergeRequest(BaseModel):
    keep_id: str
    merge_id: str


class MergeResult(BaseModel):
    kept_id: str
    merged_id: str
    marketplaces: list[str]
    sales: int
    labels: list[str]


@router.get("", response_model=SimilarItems)
def list_similar_items(db: Session = Depends(get_db)):
    return similar_items.find_similar(db)


@router.post("/dismiss")
def dismiss_pair(body: PairRequest, db: Session = Depends(get_db)):
    try:
        similar_items.dismiss(db, body.first_id, body.second_id)
    except similar_items.MergeError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    return {"ok": True}


@router.post("/merge", response_model=MergeResult)
def merge_pair(body: MergeRequest, db: Session = Depends(get_db)):
    try:
        return similar_items.merge(db, body.keep_id, body.merge_id)
    except similar_items.MergeError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
