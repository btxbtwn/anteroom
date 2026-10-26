"""Price drops measured from the original asking price.

A recurring "10% every 60 days" sets the price to 90% of the original, and
the next time it is still 90% of that same original. It does not take 10%
off whatever the price is now. The floor is a minimum dollar price (or a
percent of the original) and the result never goes under it, and never up.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP


class PriceError(ValueError):
    pass


def money(value: float | Decimal | str) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def drop_price(
    original: float,
    current: float,
    *,
    mode: str,
    value: float,
    floor: float | None = None,
    floor_percent: float | None = None,
) -> tuple[float, float]:
    """Return ``(original, target)`` in dollars.

    The target is the original minus the drop, then raised to the floor when
    the drop would pass it, then capped at the current price so a second pass
    cannot push it lower and a pass never raises the price.
    """
    basis = money(original)
    now = money(current)
    if basis <= 0 or now <= 0:
        raise PriceError("The listing needs a price above zero.")
    if mode == "percent":
        if value <= 0 or value >= 100:
            raise PriceError("A percent drop is between 0 and 100.")
        target = (basis * (Decimal("100") - Decimal(str(value))) / Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    elif mode == "amount":
        if value <= 0:
            raise PriceError("A price drop needs an amount above zero.")
        target = basis - money(value)
    else:
        raise PriceError("A price change is an amount or a percent.")
    minimum = _floor(basis, floor, floor_percent)
    if minimum is not None and target < minimum:
        target = minimum
    if target < Decimal("0.01"):
        target = Decimal("0.01")
    if target > now:
        target = now
    return float(basis), float(target)


def _floor(original: Decimal, floor: float | None, floor_percent: float | None) -> Decimal | None:
    bounds = []
    if floor is not None:
        if floor < 0:
            raise PriceError("A price floor cannot be negative.")
        bounds.append(money(floor))
    if floor_percent is not None:
        if floor_percent <= 0 or floor_percent > 100:
            raise PriceError("A percent floor is between 0 and 100.")
        bounds.append((original * Decimal(str(floor_percent)) / Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        ))
    if not bounds:
        return None
    return max(bounds)
