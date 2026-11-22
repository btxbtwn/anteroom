"""Shared desktop and hosted bookkeeping API."""
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from anteroom.database import get_db
from anteroom.models.business_expense import BusinessExpense
from anteroom.services import expense_csv, profit_and_loss, sales_ledger
from anteroom.services.business_expenses import (
    Category, Expense, ExpenseInput, ExpenseUpdate, MileageAmount, MileageInput, entries, mileage, save,
)

router = APIRouter(prefix="/api/business-expenses", tags=["business-expenses"])


@router.get("")
def list_expenses(start: date, end: date, category: Category | None = None, page: int = Query(1, ge=1, le=1_000_000),
                  db: Session = Depends(get_db, scope="function")) -> list[Expense]:
    if end < start:
        raise HTTPException(422, "End date must be on or after start date.")
    rows = db.scalars(entries(start, end, category).limit(50).offset((page - 1) * 50))
    return [Expense.model_validate(row, from_attributes=True) for row in rows]


@router.post("/mileage")
def calculate_mileage(body: MileageInput) -> MileageAmount:
    try:
        return mileage(body)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def _csv(content: str, filename: str) -> Response:
    return Response(content, media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Cache-Control": "no-store",
    })


@router.get("/template.csv")
def template() -> Response:
    return _csv(expense_csv.render(expense_csv.COLUMNS, ()), "expense-template.csv")


@router.get("/expenses.csv")
def export_expenses(start: date, end: date, category: Category | None = None,
                    db: Session = Depends(get_db, scope="function")) -> Response:
    if end < start:
        raise HTTPException(422, "End date must be on or after start date.")
    return _csv(expense_csv.export(db, start, end, category), "expenses.csv")


@router.post("/import/preview")
def preview_import(body: expense_csv.CsvInput, db: Session = Depends(get_db, scope="function")) -> expense_csv.CsvResult:
    return expense_csv.load(db, body.content, commit=False)


@router.post("/import")
def import_expenses(body: expense_csv.CsvInput, db: Session = Depends(get_db, scope="function")) -> expense_csv.CsvResult:
    return expense_csv.load(db, body.content, commit=True)


@router.get("/profit-and-loss")
def profit_report(start: date, end: date, mode: profit_and_loss.AccountingMode = "accrual",
                  db: Session = Depends(get_db, scope="function")) -> list[profit_and_loss.CurrencyReport]:
    try:
        return profit_and_loss.report(db, start, end, mode)
    except sales_ledger.LedgerError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@router.get("/profit-and-loss.csv")
def export_profit_report(start: date, end: date, mode: profit_and_loss.AccountingMode = "accrual",
                         db: Session = Depends(get_db, scope="function")) -> Response:
    rows = (
        (start, end, mode, report.currency, line.label, f"{Decimal(line.amount_cents) / 100:.2f}",
         report.incomplete_sales)
        for report in profit_report(start, end, mode, db) for line in report.lines
    )
    return _csv(expense_csv.render(("start", "end", "mode", "currency", "line", "amount", "incomplete_sales"), rows),
                "profit-and-loss.csv")


@router.post("", status_code=201)
def create_expense(body: ExpenseInput, db: Session = Depends(get_db, scope="function")) -> Expense:
    result = save(db, BusinessExpense(import_source="manual"), body)
    db.commit()
    return result


def _changed() -> HTTPException:
    return HTTPException(409, "This expense changed elsewhere. Reopen it to see the latest version.")


def _find(db: Session, expense_id: str, expected_revision: int) -> BusinessExpense:
    expense = db.get(BusinessExpense, expense_id)
    if expense is None:
        raise HTTPException(404, "Expense not found.")
    if expense.revision != expected_revision:
        raise _changed()
    return expense


@router.put("/{expense_id}")
def update_expense(expense_id: str, body: ExpenseUpdate, db: Session = Depends(get_db, scope="function")) -> Expense:
    try:
        result = save(db, _find(db, expense_id, body.expected_revision), body)
        db.commit()
        return result
    except StaleDataError as exc:
        raise _changed() from exc


@router.delete("/{expense_id}", status_code=204)
def delete_expense(expense_id: str, expected_revision: int = Query(ge=1), db: Session = Depends(get_db, scope="function")) -> Response:
    try:
        db.delete(_find(db, expense_id, expected_revision))
        db.commit()
    except StaleDataError as exc:
        raise _changed() from exc
    return Response(status_code=204)
