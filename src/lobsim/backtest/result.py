"""Backtest results."""

from __future__ import annotations

from dataclasses import dataclass, field

from lobsim.backtest.types import OrderFilled


@dataclass
class Result:
    fills: list[OrderFilled] = field(default_factory=list)  # as delivered to the strategy
    final_position: int = 0  # lots
    final_cash: int = 0  # tick*lot units
