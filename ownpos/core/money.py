"""Money helpers.

All money is stored as integer cents so totals never drift from floating-point rounding.
Rounding is always half-up (0.5 cent and more goes up), the rule Indiana uses for sales tax.
Quantities are floats rounded to 3 decimals."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

QTY_PLACES = 3


def to_cents(value) -> int:
    """Dollars (str, int, float or Decimal) -> integer cents. '$1,234.5' -> 123450."""
    if value is None or value == "":
        return 0
    if isinstance(value, bool):
        raise ValueError("Not an amount")
    if isinstance(value, int):
        return value * 100
    text = str(value).replace(",", "").replace("$", "").strip()
    neg = text.startswith("(") and text.endswith(")")       # accounting style (12.50)
    if neg:
        text = text[1:-1]
    try:
        d = Decimal(text or "0")
    except InvalidOperation as e:
        raise ValueError(f"'{value}' is not an amount") from e
    cents = int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return -cents if neg else cents


def to_dollars(cents: int | float | None) -> Decimal:
    if cents is None:
        return Decimal("0.00")
    return (Decimal(str(cents)) / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def round_cents(value: float | Decimal) -> int:
    """Round a fractional cent amount half-up to a whole cent."""
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def round_qty(q) -> float:
    return float(Decimal(str(q or 0)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))


def fmt_money(cents: int | float | None, symbol: str = "") -> str:
    """1234567 -> '12,345.67' (with symbol '$': '$12,345.67', negative '-$12.50')."""
    d = to_dollars(cents or 0)
    sign = "-" if d < 0 else ""
    return f"{sign}{symbol}{abs(d):,.2f}"


def fmt_qty(q: float | None) -> str:
    q = round_qty(q or 0)
    if q == int(q):
        return f"{int(q):,}" if abs(q) >= 10000 else str(int(q))
    return f"{q:.3f}".rstrip("0").rstrip(".")


def parse_money(text: str) -> int:
    return to_cents(text)


# names used by code shared with the original program
to_paisa = to_cents
round_paisa = round_cents
