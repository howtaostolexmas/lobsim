import pytest
from helpers import BUY, SELL, snap

from lobsim.backtest import Liquidity, OrderAccepted, OrderCancelled, OrderFilled, SimConfig
from lobsim.backtest.exchange import SimExchange, fee_quote
from lobsim.marketdata.instrument import BTCUSDT

T = Liquidity.TAKER


def deep_book():
    ex = SimExchange()
    ex.on_book(snap(1, bids=((100, 4), (99, 6)), asks=((101, 2), (102, 3), (103, 5))))
    return ex


def test_marketable_limit_walks_visible_levels_up_to_limit():
    ex = deep_book()
    assert ex.submit_limit(10, 1, BUY, 102, 10) == [
        OrderAccepted(10, 1),
        OrderFilled(10, 1, BUY, 101, 2, T, 8),
        OrderFilled(10, 1, BUY, 102, 3, T, 5),
        OrderCancelled(10, 1, 5),
    ]


def test_marketable_limit_fully_filled_has_no_cancel():
    ex = deep_book()
    assert ex.submit_limit(10, 1, SELL, 100, 3) == [
        OrderAccepted(10, 1),
        OrderFilled(10, 1, SELL, 100, 3, T, 0),
    ]


def test_market_order_walks_all_levels_and_cancels_rest():
    ex = deep_book()
    out = ex.submit_market(10, 1, SELL, 12)
    assert out == [
        OrderAccepted(10, 1),
        OrderFilled(10, 1, SELL, 100, 4, T, 8),
        OrderFilled(10, 1, SELL, 99, 6, T, 2),
        OrderCancelled(10, 1, 2),
    ]


def test_market_order_into_empty_side():
    ex = SimExchange()
    ex.on_book(snap(1, bids=((100, 4),)))
    assert ex.submit_market(10, 1, BUY, 3) == [OrderAccepted(10, 1), OrderCancelled(10, 1, 3)]


def test_aggressive_does_not_modify_replayed_book():
    ex = deep_book()
    ex.submit_limit(10, 1, BUY, 103, 7)
    assert ex.book.depth(SELL, 5) == [(101, 2), (102, 3), (103, 5)]


def test_aggressive_never_rests():
    ex = deep_book()
    ex.submit_limit(10, 1, BUY, 101, 5)  # 2 fill, 3 cancelled
    assert ex.queue_ahead(1) is None


def test_market_validation():
    ex = deep_book()
    assert ex.submit_market(10, 1, BUY, 0)[0].reason == "non_positive_qty"


def test_fee_quote_maker_taker_and_rebate():
    cfg = SimConfig(maker_fee_bps=-1.0, taker_fee_bps=10.0)
    # 77,582.03 USDT x 1 BTC at 10 bps = 77.58203 USDT
    assert fee_quote(7_758_203, 100_000, Liquidity.TAKER, BTCUSDT, cfg) == pytest.approx(77.58203)
    assert fee_quote(7_758_203, 100_000, Liquidity.MAKER, BTCUSDT, cfg) == pytest.approx(-7.758203)
