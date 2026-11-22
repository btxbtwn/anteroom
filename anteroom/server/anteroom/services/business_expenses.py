"""Expense validation, calculation and persistence."""
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Literal, Self

from pydantic import BaseModel, Field, WithJsonSchema, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from anteroom.models.business_expense import BusinessExpense

Category = Literal[
    "inventory_purchases", "sourcing_transportation", "shipping_supplies", "shipping_labels",
    "platform_fees", "software_subscriptions", "equipment", "home_office", "advertising", "mileage", "other",
]
DecimalText = Annotated[Decimal, WithJsonSchema({"type": "string", "description": "Decimal value without a currency symbol."})]


class ExpenseInput(BaseModel):
    incurred_date: date
    amount_cents: int = Field(gt=0, le=999_999_999_999)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    category: Category
    vendor: str = Field(default="", max_length=256)
    description: str = Field(default="", max_length=512)
    notes: str = Field(default="", max_length=4096)
    distance: DecimalText | None = Field(default=None, gt=0, max_digits=12, decimal_places=3)
    distance_unit: Literal["mi", "km"] | None = None
    mileage_rate_cents: DecimalText | None = Field(default=None, gt=0, max_digits=12, decimal_places=6)
    origin_destination: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def validate_trip(self) -> Self:
        fields = (self.distance, self.distance_unit, self.mileage_rate_cents)
        if self.category != "mileage":
            if any(value is not None for value in (*fields, self.origin_destination)):
                raise ValueError("Trip details belong to mileage expenses.")
        elif self.distance is None or self.distance_unit is None or self.mileage_rate_cents is None:
            raise ValueError("Enter the trip distance, unit and rate.")
        else:
            self.distance = self.distance.quantize(Decimal("0.001"))
            self.mileage_rate_cents = self.mileage_rate_cents.quantize(Decimal("0.000001"))
            self.amount_cents = round_cents(self.distance * self.mileage_rate_cents)
            if not 0 < self.amount_cents <= 999_999_999_999:
                raise ValueError("The mileage total is outside the supported range.")
        return self


class Expense(ExpenseInput):
    id: str
    revision: int
    accounting_type: Literal["operating_expense", "inventory_purchase"]
    import_source: Literal["manual", "csv", "plaid"]


class ExpenseUpdate(ExpenseInput):
    expected_revision: int = Field(ge=1, exclude=True)


def round_cents(amount: Decimal) -> int:
    return int(amount.to_integral_value(rounding=ROUND_HALF_UP))


def entries(start: date, end: date, category: Category | None = None):
    query = select(BusinessExpense).where(BusinessExpense.incurred_date.between(start, end))
    if category is not None:
        query = query.where(BusinessExpense.category == category)
    return query.order_by(BusinessExpense.incurred_date.desc(), BusinessExpense.id.desc())


def apply_input(expense: BusinessExpense, body: ExpenseInput) -> None:
    for field, value in body.model_dump().items():
        setattr(expense, field, value)
    expense.accounting_type = "inventory_purchase" if body.category == "inventory_purchases" else "operating_expense"


def save(db: Session, expense: BusinessExpense, body: ExpenseInput) -> Expense:
    apply_input(expense, body)
    db.add(expense)
    db.flush()
    return Expense.model_validate(expense, from_attributes=True)


class MileageInput(BaseModel):
    incurred_date: date
    country: Literal["US", "CA"]
    distance: DecimalText = Field(gt=0, max_digits=9, decimal_places=3)
    distance_unit: Literal["mi", "km"]
    prior_business_km: DecimalText = Field(default=Decimal(0), ge=0, max_digits=9, decimal_places=3)
    northern_territory: bool = False
    us_rate_override_cents: DecimalText | None = Field(default=None, gt=0, max_digits=9, decimal_places=6)


class MileageAmount(BaseModel):
    currency: str
    mileage_rate_cents: DecimalText
    amount_cents: int


# IRS standard business mileage rates and CRA automobile allowance rates.
US_RATES = {2023: Decimal("65.5"), 2024: Decimal(67), 2025: Decimal(70), 2026: Decimal("72.5")}
CA_RATES = {2023: (68, 62), 2024: (70, 64), 2025: (72, 66), 2026: (73, 67)}
KM_PER_MILE = Decimal("1.609344")


def mileage(body: MileageInput) -> MileageAmount:
    year = body.incurred_date.year
    if body.country == "US" and (year in US_RATES or body.us_rate_override_cents is not None):
        if body.us_rate_override_cents is not None:
            rate = body.us_rate_override_cents
        else:
            rate = Decimal(76) if body.incurred_date >= date(2026, 7, 1) else US_RATES[year]
            if body.distance_unit == "km":
                rate /= KM_PER_MILE
        currency = "USD"
    elif body.country == "CA" and year in CA_RATES:
        km = body.distance * KM_PER_MILE if body.distance_unit == "mi" else body.distance
        first_tier = min(km, max(Decimal(0), 5000 - body.prior_business_km))
        first, extra = CA_RATES[year]
        total = first_tier * first + (km - first_tier) * extra
        if body.northern_territory:
            total += km * 4
        rate = total / body.distance
        currency = "CAD"
    else:
        raise ValueError("Mileage rates are available for 2023 through 2026.")
    rate = rate.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    return MileageAmount(currency=currency, mileage_rate_cents=rate, amount_cents=round_cents(rate * body.distance))
