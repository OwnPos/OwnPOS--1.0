"""Sales tax for one receipt (Indiana: 7%, IC 6-2.5-2-2).

Prices are before tax. The tax is worked out on the INVOICE BASIS, which the statute allows
(IC 6-2.5-2-2(c)): for each tax rate, add up the taxable amounts on the receipt (after discounts),
multiply by the rate and round half-up to the cent (IC 6-2.5-2-2(b): a third decimal above four
rounds up). That tax is then split over the lines of that rate in proportion to their amounts
(largest remainder), so the line taxes always add up to exactly the receipt tax. Returns use
the line taxes, so a customer gets back exactly what they were charged."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from .money import round_cents


@dataclass
class LineAmounts:
    gross: int       # qty x price, before discount
    discount: int
    net: int         # gross - discount (the taxable base of the line)
    tax: int
    total: int       # net + tax
    taxable: bool


def tax_on(amount_cents: int, rate_pct: float) -> int:
    """round-half-up(amount x rate) to the cent."""
    if not rate_pct or not amount_cents:
        return 0
    value = Decimal(int(amount_cents)) * Decimal(str(rate_pct)) / Decimal(100)
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def split_by_weight(total: int, weights: list[int]) -> list[int]:
    """Split an integer total over weights so the parts add up exactly (largest remainder; ties to the
    earlier line). Works for negative totals too."""
    if not weights:
        return []
    wsum = sum(weights)
    if wsum == 0:
        out = [0] * len(weights)
        out[0] = total
        return out
    sign = -1 if total < 0 else 1
    t = abs(total)
    raw = [Decimal(t) * Decimal(w) / Decimal(wsum) for w in weights]
    parts = [int(x) for x in raw]               # floor (weights are >= 0)
    left = t - sum(parts)
    order = sorted(range(len(weights)), key=lambda i: (-(raw[i] - parts[i]), i))
    for i in order[:left]:
        parts[i] += 1
    return [sign * p for p in parts]


def calc_receipt(lines: list[tuple[float, int, int, float]], tax_exempt: bool = False) -> tuple[list[LineAmounts], dict]:
    """lines: [(qty, unit_price_cents, discount_cents, tax_rate_pct), ...].

    Returns (per-line amounts, totals dict with subtotal, discount, taxable, nontaxable, tax, total)."""
    out: list[LineAmounts] = []
    for qty, price, discount, rate in lines:
        gross = round_cents(qty * price)
        disc = int(discount or 0)
        net = gross - disc
        out.append(LineAmounts(gross, disc, net, 0, net, bool(rate) and not tax_exempt))
    # one rounding per tax rate (invoice basis)
    rates: dict[float, list[int]] = {}
    for i, (ln, (_q, _p, _d, rate)) in enumerate(zip(out, lines)):
        if ln.taxable:
            rates.setdefault(float(rate), []).append(i)
    for rate, idx in rates.items():
        base = sum(out[i].net for i in idx)
        tax = tax_on(base, rate)
        for i, part in zip(idx, split_by_weight(tax, [max(out[i].net, 0) for i in idx])):
            out[i].tax = part
            out[i].total = out[i].net + part
    totals = {
        "subtotal": sum(x.gross for x in out),
        "discount": sum(x.discount for x in out),
        "taxable": sum(x.net for x in out if x.taxable),
        "nontaxable": sum(x.net for x in out if not x.taxable),
        "tax": sum(x.tax for x in out),
    }
    totals["total"] = totals["taxable"] + totals["nontaxable"] + totals["tax"]
    return out, totals
