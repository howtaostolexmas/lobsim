import pytest
from helpers import BUY, SELL, snap, upd

from lobsim.backtest import Liquidity, SimConfig
from lobsim.backtest.engine import Backtest
from lobsim.backtest.exchange import fee_quote
from lobsim.backtest.strategy import Strategy
from lobsim.marketdata.instrument import Instrument

INST = Instrument("TEST", price_decimals=2, qty_decimals=3)
CFG = SimConfig(order_latency_us=10, feed_latency_us=10, maker_fee_bps=-1.0, taker_fee_bps=5.0)


class Script(Strategy):
    """Sends a list of (on_nth_book, method, args) requests."""

    def __init__(self, steps):
        self.steps = steps
        self.n = 0

    def on_book(self, ctx, batch):
        for when, method, args in self.steps:
            if when == self.n:
                getattr(ctx, method)(*args)
        self.n += 1


def market():
    return [
        snap(0, bids=((99, 5),), asks=((101, 2), (102, 5))),
        upd(100, (BUY, 99, 6)),
        upd(200, (BUY, 98, 1)),
    ]


def test_cash_and_position_exact_from_fills():
    steps = [(0, "place_market", (BUY, 3)), (1, "place_market", (SELL, 1))]
    r = Backtest(Script(steps), CFG, INST).run(market())
    assert [(f.side, f.price, f.qty) for f in r.fills] == [(BUY, 101, 2), (BUY, 102, 1), (SELL, 99, 1)]
    assert r.final_position == 2
    assert r.final_cash == -(2 * 101 + 102) + 99  # exact integer tick*lot units


def test_fees_total_equals_sum_of_fill_fees():
    steps = [(0, "place_market", (BUY, 3)), (1, "place_market", (SELL, 1))]
    r = Backtest(Script(steps), CFG, INST).run(market())
    expected = sum(fee_quote(f.price, f.qty, f.liquidity, INST, CFG) for f in r.fills)
    assert r.fees_quote == pytest.approx(expected)
    assert all(f.liquidity is Liquidity.TAKER for f in r.fills)


def test_pnl_mark_to_price():
    steps = [(0, "place_market", (BUY, 3))]
    r = Backtest(Script(steps), CFG, INST).run(market())
    unit = 10**-2 * 10**-3  # one tick times one lot, in quote currency
    gross = (r.final_cash + r.final_position * 100) * unit
    assert r.pnl_quote(100) == pytest.approx(gross - r.fees_quote)


def test_fill_ratio_and_counts():
    steps = [(0, "place_market", (BUY, 10)), (1, "place_limit", (BUY, 90, 4))]
    r = Backtest(Script(steps), CFG, INST).run(market())
    assert r.orders_placed == 2 and r.qty_placed == 14
    assert r.qty_filled == 7 and r.taker_qty == 7 and r.maker_qty == 0  # 2 + 5 visible asks
    assert r.fill_ratio == pytest.approx(7 / 14)


def test_summary_has_expected_keys():
    r = Backtest(Script([(0, "place_market", (BUY, 1))]), CFG, INST).run(market())
    s = r.summary(INST)
    for key in ("pnl_quote", "gross_pnl_quote", "fees_quote", "fills", "fill_ratio",
                "maker_qty", "taker_qty", "final_position", "max_abs_position", "orders_placed"):
        assert key in s


def test_equity_sampled_per_bucket():
    events = [snap(0, bids=((99, 5),), asks=((101, 5),))] + [
        upd(t, (BUY, 99, 5 + i)) for i, t in enumerate(range(50, 1000, 50))
    ]
    r = Backtest(Strategy(), CFG, INST, sample_every_us=300).run(events)
    assert [ts for ts, _pos, _pnl in r.equity] == [300, 600, 900]
