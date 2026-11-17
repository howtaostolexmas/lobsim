"""Backtest configuration and the notices the simulated exchange sends back."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from lobsim.types import Side


@dataclass(frozen=True)
class SimConfig:
    order_latency_us: int = 1_000  # strategy -> exchange
    feed_latency_us: int = 1_000  # exchange -> strategy (market data and our notices)
    maker_fee_bps: float = 10.0  # Binance spot base tier: 0.1%
    taker_fee_bps: float = 10.0  # negative values are rebates


class Liquidity(Enum):
    MAKER = "maker"  # our order was resting and got hit
    TAKER = "taker"  # our order crossed the spread


# Every notice carries `ts`: the exchange-side time it happened. The strategy
# receives it `feed_latency_us` later.


@dataclass(frozen=True, slots=True)
class OrderAccepted:
    ts: int
    order_id: int


@dataclass(frozen=True, slots=True)
class OrderFilled:
    ts: int
    order_id: int
    side: Side
    price: int
    qty: int
    liquidity: Liquidity
    remaining: int


@dataclass(frozen=True, slots=True)
class OrderCancelled:
    ts: int
    order_id: int
    remaining: int


@dataclass(frozen=True, slots=True)
class OrderRejected:
    ts: int
    order_id: int
    reason: str


Notice = OrderAccepted | OrderFilled | OrderCancelled | OrderRejected
