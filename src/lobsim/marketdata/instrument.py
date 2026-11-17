"""Instrument specs and exact conversion of decimal strings to integer units.

Exchanges publish prices like "67000.12". The engine works in integer ticks
(and quantities in integer lots), so "67000.12" with tick 0.01 becomes
6700012. Going through float would be wrong: float("0.1") is not exactly 0.1,
so rounding errors could silently move a price to a neighbouring tick.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal


# Unsigned ASCII decimal, optional exponent: "123", "123.45", "1e-05", "6.7E4".
# Signs, spaces, underscores and non-ASCII digits are all rejected on purpose:
# prices and sizes are never negative, and anything unusual should fail loudly.
_NUMBER = re.compile(r"[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")


def to_units(text: str, decimals: int) -> int:
    """Convert a decimal string to an integer count of 10**-decimals units.

    "67000.1" with decimals=2 -> 6700010. Raises ValueError if the value has
    more precision than `decimals` allows (it is not on the tick grid), or if
    it is not a plain non-negative number.
    """
    if not text.isascii() or _NUMBER.fullmatch(text) is None:
        raise ValueError(f"{text!r} is not a plain non-negative decimal number")

    # Fast path: "123" or "123.45", scaled by moving the decimal point.
    whole, _, frac = text.partition(".")
    if whole.isdigit() and (not frac or frac.isdigit()):
        frac = frac.rstrip("0")
        if len(frac) > decimals:
            raise ValueError(f"{text!r} is not a multiple of 1e-{decimals}")
        return int(whole + frac.ljust(decimals, "0"))

    # Exponent form: exact integer arithmetic on Decimal's digits. (Decimal's
    # own arithmetic rounds to 28 significant digits, so we avoid it.)
    _sign, digits, exponent = Decimal(text).as_tuple()
    mantissa = int("".join(map(str, digits)))
    shift = exponent + decimals  # value * 10**decimals == mantissa * 10**shift
    if shift >= 0:
        return mantissa * 10**shift
    divisor = 10**-shift
    if mantissa % divisor:
        raise ValueError(f"{text!r} is not a multiple of 1e-{decimals}")
    return mantissa // divisor


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
