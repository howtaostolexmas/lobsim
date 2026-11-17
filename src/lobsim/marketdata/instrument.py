"""Instrument specs and exact conversion of decimal strings to integer units.

Exchanges publish prices like "67000.12". The engine works in integer ticks
(and quantities in integer lots), so "67000.12" with tick 0.01 becomes
6700012. Going through float would be wrong: float("0.1") is not exactly 0.1,
so rounding errors could silently move a price to a neighbouring tick.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


def to_units(text: str, decimals: int) -> int:
    """Convert a decimal string to an integer count of 10**-decimals units.

    "67000.1" with decimals=2 -> 6700010. Raises ValueError if the value has
    more precision than `decimals` allows (it is not on the tick grid), or if
    it is not a number at all.
    """
    # Fast path: plain "123" or "123.45" strings, scaled by moving the point.
    whole, dot, frac = text.partition(".")
    if whole.isdigit() and (not dot or frac.isdigit()):
        frac = frac.rstrip("0")
        if len(frac) > decimals:
            raise ValueError(f"{text!r} is not a multiple of 1e-{decimals}")
        return int(whole + frac.ljust(decimals, "0"))

    # Slow path (exponents like "1e-05", signs): exact Decimal arithmetic.
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise ValueError(f"{text!r} is not a number") from None
    if not value.is_finite():
        raise ValueError(f"{text!r} is not a finite number")
    scaled = value.scaleb(decimals)
    if scaled != scaled.to_integral_value():
        raise ValueError(f"{text!r} is not a multiple of 1e-{decimals}")
    return int(scaled)


@dataclass(frozen=True)
class Instrument:
    symbol: str
    price_decimals: int  # tick size = 10**-price_decimals
    qty_decimals: int  # lot size = 10**-qty_decimals

    def price(self, text: str) -> int:
        return to_units(text, self.price_decimals)

    def qty(self, text: str) -> int:
        return to_units(text, self.qty_decimals)


# Binance spot BTCUSDT: tick 0.01 USDT, lot 0.00001 BTC.
BTCUSDT = Instrument("BTCUSDT", price_decimals=2, qty_decimals=5)
