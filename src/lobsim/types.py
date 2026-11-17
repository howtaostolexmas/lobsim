"""Core value types: order sides and the events the engine emits."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Side(Enum):
    BUY = "buy"
    SELL = "sell"

    @property
    def opposite(self) -> Side:
        return Side.SELL if self is Side.BUY else Side.BUY


# Events are immutable so tests can compare whole event lists with ==.


@dataclass(frozen=True, slots=True)
class Accepted:
    order_id: int


@dataclass(frozen=True, slots=True)
class Rejected:
    order_id: int
    reason: str


@dataclass(frozen=True, slots=True)
class Trade:
    """One fill between a resting order (maker) and an incoming order (taker).

    A partial fill is simply a Trade whose remaining quantity is above zero,
    so there is no separate "partial fill" event.
    """

    maker_id: int
    taker_id: int
    price: int  # always the maker's price
    qty: int
    aggressor: Side  # the taker's side
    maker_remaining: int
    taker_remaining: int


@dataclass(frozen=True, slots=True)
class Cancelled:
    order_id: int
    remaining_qty: int


Event = Accepted | Rejected | Trade | Cancelled
