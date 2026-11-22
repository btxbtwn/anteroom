"""Spreadsheet interchange with row-level validation and retry-safe inserts."""
import csv
import hashlib
import io
from datetime import date
from decimal import Decimal
from itertools import batched
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import Table, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from anteroom.models.business_expense import BusinessExpense
from anteroom.models.conversation import new_id
from anteroom.models.ownership import LOCAL_USER_ID
from anteroom.services.business_context import invalidate_business_context
from anteroom.services.business_expenses import Category, ExpenseInput, entries
from anteroom.services.ledger_exports import text

COLUMNS = tuple(ExpenseInput.model_fields)
TEXT_COLUMNS = {"vendor", "description", "notes", "origin_destination"}


class CsvInput(BaseModel):
    content: str = Field(max_length=1_000_000)


class CsvIssue(BaseModel):
    row: int
    detail: str


class CsvResult(BaseModel):
    valid: int
    duplicates: int
    imported: int
    issues: list[CsvIssue]
    preview: list[ExpenseInput]


def render(headers, rows) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(rows)
    return "\ufeff" + output.getvalue()


def export(db: Session, start: date, end: date, category: Category | None) -> str:
    return render(COLUMNS, (
        [text(getattr(row, key)) if key in TEXT_COLUMNS else getattr(row, key) for key in COLUMNS]
        for row in db.scalars(entries(start, end, category))
    ))


def _import_key(expense: ExpenseInput) -> str:
    return hashlib.sha256(expense.model_dump_json().encode()).hexdigest()


def load(db: Session, content: str, *, commit: bool) -> CsvResult:
    reader = csv.DictReader(io.StringIO(content.removeprefix("\ufeff")), strict=True)
    result = CsvResult(valid=0, duplicates=0, imported=0, issues=[], preview=[])
    prepared: dict[str, dict[str, Any]] = {}
    legacy_keys: dict[str, str] = {}
    mileage_dates: set[date] = set()
    try:
        headers = reader.fieldnames or []
        if (len(set(headers)) != len(headers) or set(headers) - set(COLUMNS)
                or not {"incurred_date", "category", "amount_cents"} <= set(headers)):
            raise ValueError("Use the expense CSV template headers.")
        for index, row in enumerate(reader, start=2):
            if index > 2001:
                raise ValueError("Import up to 2,000 expenses at a time.")
            if None in row or any(value is None for value in row.values()):
                result.issues.append(CsvIssue(row=index, detail="The number of cells differs from the header."))
                continue
            try:
                expense = ExpenseInput.model_validate({key: value for key, value in row.items() if value != ""})
            except ValidationError as exc:
                for error in exc.errors(include_input=False, include_context=False, include_url=False):
                    field = ".".join(map(str, error["loc"]))
                    result.issues.append(CsvIssue(row=index, detail=f"{field}: {error['msg']}" if field else error["msg"]))
                continue
            key = _import_key(expense)
            if expense.category == "mileage":
                mileage_dates.add(expense.incurred_date)
                # Before quantization the hash preserved the CSV's Decimal spelling.
                # Retain exact retries even if the imported expense has since been edited.
                legacy = _import_key(expense.model_copy(update={
                    "distance": Decimal(row["distance"]),
                    "mileage_rate_cents": Decimal(row["mileage_rate_cents"]),
                }))
                if legacy != key:
                    legacy_keys[legacy] = key
            if key in prepared:
                result.duplicates += 1
                continue
            prepared[key] = {
                **expense.model_dump(), "id": new_id(), "import_source": "csv", "import_key": key,
                "accounting_type": "inventory_purchase" if expense.category == "inventory_purchases" else "operating_expense",
            }
    except (csv.Error, ValueError) as exc:
        result.issues.append(CsvIssue(row=reader.line_num or 1, detail=str(exc)))
    owner = getattr(db, "user_id", LOCAL_USER_ID)
    existing_keys = db.scalars(select(BusinessExpense.import_key).where(
        BusinessExpense.user_id == owner, BusinessExpense.import_key.in_(set(prepared) | legacy_keys.keys()),
    ))
    existing = {legacy_keys.get(key, key) for key in existing_keys if key is not None}
    if mileage_dates:
        # Unedited mileage rows also let us recognize a retry written with a
        # different scale. Keep the original hash on disk for exact retries.
        candidates = db.scalars(select(BusinessExpense).where(
            BusinessExpense.user_id == owner,
            BusinessExpense.incurred_date.in_(mileage_dates),
            BusinessExpense.category == "mileage",
            BusinessExpense.import_source == "csv",
            BusinessExpense.import_key.is_not(None),
            BusinessExpense.revision == 1,
        ))
        for candidate in candidates:
            key = _import_key(ExpenseInput.model_validate(candidate, from_attributes=True))
            if key in prepared:
                existing.add(key)
    result.duplicates += len(existing)
    pending = {key: values for key, values in prepared.items() if key not in existing}
    result.valid = len(pending)
    if not commit:
        result.preview = [ExpenseInput.model_validate(row) for row in list(pending.values())[:5]]
    if commit and not result.issues:
        bind = db.get_bind()
        assert bind is not None
        insert = postgres_insert if bind.dialect.name == "postgresql" else sqlite_insert
        table = BusinessExpense.__table__
        assert isinstance(table, Table)
        # Lock duplicates in the same order even when simultaneous imports have reordered rows.
        for keys in batched(sorted(pending), 100):
            rows = [dict(pending[key], user_id=owner) for key in keys]
            statement = insert(table).values(rows).on_conflict_do_nothing(
                index_elements=["user_id", "import_key"],
            ).returning(table.c.id)
            result.imported += len(db.scalars(statement).all())
        result.duplicates += result.valid - result.imported
        result.valid = result.imported
        db.commit()
        if result.imported:
            invalidate_business_context(user_id=getattr(db, "user_id", None))
    return result
