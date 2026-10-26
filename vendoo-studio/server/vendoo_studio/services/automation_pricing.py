"""Price drops measured from the original asking price.

A one-time 10% sets the price to 90% of the original and leaves it there.
A recurring drop steps down that same original: 90%, then 80%, then 70%,
and stops at the floor. It never takes the percent off the price it just
set. The result never goes under the floor, and never up.
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
    repeat: str = "once",
) -> tuple[float, float]:
    """Return ``(original, target)`` in dollars.

    ``once`` subtracts the drop from the original a single time. ``step``
    walks one more rung down the original each cycle (10% is 90, then 80,
    then 70) until the floor. Either way the target is capped at the current
    price, so a pass never raises it.
    """
    basis = money(original)
    now = money(current)
    if basis <= 0 or now <= 0:
        raise PriceError("The listing needs a price above zero.")
    step = _step(basis, mode, value)
    minimum = _floor(basis, floor, floor_percent)
    if repeat == "step":
        target = _next_rung(basis, now, step, minimum)
    elif repeat == "once":
        target = basis - step
        if minimum is not None and target < minimum:
            target = minimum
        if target < Decimal("0.01"):
            target = Decimal("0.01")
    else:
        raise PriceError("A price drop happens once, or it steps down each cycle.")
    if target > now:
        target = now
    return float(basis), float(target)


def _step(original: Decimal, mode: str, value: float) -> Decimal:
    if mode == "percent":
        if value <= 0 or value >= 100:
            raise PriceError("A percent drop is between 0 and 100.")
        return (original * Decimal(str(value)) / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if mode == "amount":
        if value <= 0:
            raise PriceError("A price drop needs an amount above zero.")
        return money(value)
    raise PriceError("A price change is an amount or a percent.")


def _next_rung(original: Decimal, current: Decimal, step: Decimal, minimum: Decimal | None) -> Decimal:
    """The next price strictly below ``current``, one drop further from the original."""
    floor = minimum if minimum is not None else Decimal("0.01")
    if floor < Decimal("0.01"):
        floor = Decimal("0.01")
    for count in range(1, 1001):
        target = (original - step * count).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if target < floor:
            target = floor
        if target < current:
            return target
        if target == floor:
            return current if current <= floor else floor
    return current


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
