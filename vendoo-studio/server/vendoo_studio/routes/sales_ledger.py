"""Local financial editing, reporting and exports. Never calls a marketplace."""
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from vendoo_studio.database import get_db
from vendoo_studio.services import ledger_exports, sales_ledger

router = APIRouter(prefix="/api/sales-ledger", tags=["sales-ledger"])


def _run(operation):
    try:
        return operation()
    except sales_ledger.LedgerError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@router.get("")
def records(response: Response, start: date, end: date, marketplace: str | None = None, conversation_id: str | None = None,
            page: int = Query(1, ge=1), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return _run(lambda: sales_ledger.list_sales(db, start, end, marketplace, conversation_id, page))


@router.get("/export/sales.csv")
def export_sales(start: date, end: date, marketplace: str | None = None, conversation_id: str | None = None,
                 db: Session = Depends(get_db, scope="request")):
    query = _run(lambda: sales_ledger.sale_query(db, start, end, marketplace, conversation_id))
    return _download(ledger_exports.sales_csv(query), "sales")


@router.get("/export/inventory.csv")
def export_inventory(db: Session = Depends(get_db, scope="request")):
    return _download(ledger_exports.inventory_csv(db), "inventory")


def _download(chunks, kind):
    filename = f"anteroom-{kind}-{datetime.now(UTC):%Y-%m-%d}.csv"
    return StreamingResponse(chunks, media_type="text/csv; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store",
    })


@router.get("/{sale_id}")
def record(sale_id: str, response: Response, db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return _run(lambda: sales_ledger.get_sale(db, sale_id))


@router.put("/{sale_id}/financials")
def save_financials(sale_id: str, body: sales_ledger.FinancialSave, response: Response, db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return _run(lambda: sales_ledger.save_financials(db, sale_id, body))
