from helpers import BUY, SELL, snap, tr, upd

from lobsim.backtest import Liquidity, OrderAccepted, OrderCancelled, OrderFilled, OrderRejected
from lobsim.backtest.exchange import SimExchange

M = Liquidity.MAKER


def book(bids=((100, 5),), asks=((101, 5),)):
    ex = SimExchange()
    ex.on_book(snap(1, bids, asks))
    return ex


def test_rest_behind_visible_qty():
    ex = book()
    assert ex.submit_limit(10, 1, BUY, 100, 3) == [OrderAccepted(10, 1)]
    assert ex.queue_ahead(1) == 5


def test_inside_spread_rests_with_zero_ahead():
    ex = book(asks=((102, 5),))
    ex.submit_limit(10, 1, BUY, 101, 3)
    assert ex.queue_ahead(1) == 0


def test_trade_at_price_reduces_queue_then_fills_excess():
    ex = book()
    ex.submit_limit(10, 1, BUY, 100, 3)
    assert ex.on_trade(tr(20, SELL, 100, 4)) == []
    assert ex.queue_ahead(1) == 1
    assert ex.on_trade(tr(30, SELL, 100, 3)) == [OrderFilled(30, 1, BUY, 100, 2, M, 1)]
    assert ex.queue_ahead(1) == 0


def test_buy_taker_trade_does_not_touch_our_bid():
    ex = book()
    ex.submit_limit(10, 1, BUY, 100, 3)
    assert ex.on_trade(tr(20, BUY, 100, 4)) == []
    assert ex.queue_ahead(1) == 5


def test_ask_side_mirror():
    ex = book()
    ex.submit_limit(10, 1, SELL, 101, 2)
    assert ex.on_trade(tr(20, BUY, 101, 6)) == [OrderFilled(20, 1, SELL, 101, 1, M, 1)]


def test_trade_through_fills_fully_as_maker():
    ex = book()
    ex.submit_limit(10, 1, BUY, 100, 3)
    assert ex.on_trade(tr(20, SELL, 99, 1)) == [OrderFilled(20, 1, BUY, 100, 3, M, 0)]
    assert ex.queue_ahead(1) is None


def test_level_shrink_caps_queue_and_growth_does_not_raise_it():
    ex = book()
    ex.submit_limit(10, 1, BUY, 100, 3)
    ex.on_book(upd(20, (BUY, 100, 2)))
    assert ex.queue_ahead(1) == 2
    ex.on_book(upd(30, (BUY, 100, 8)))  # newcomers join behind us
    assert ex.queue_ahead(1) == 2


def test_trade_then_book_update_same_us_no_double_count():
    ex = book()
    ex.submit_limit(10, 1, BUY, 100, 3)
    assert ex.on_trade(tr(20, SELL, 100, 4)) == []  # ahead 5 -> 1
    assert ex.on_book(upd(20, (BUY, 100, 1))) == []  # same trade seen in the book: min(1, 1)
    assert ex.queue_ahead(1) == 1


def test_book_update_then_trade_same_us_no_double_count():
    ex = book()
    ex.submit_limit(10, 1, BUY, 100, 3)
    assert ex.on_book(upd(20, (BUY, 100, 1))) == []  # ahead 5 -> 1 (the trade, seen early)
    assert ex.on_trade(tr(20, SELL, 100, 4)) == []  # same µs: already counted, no phantom fill
    assert ex.queue_ahead(1) == 1
    # a further trade still works normally
    assert ex.on_trade(tr(30, SELL, 100, 2)) == [OrderFilled(30, 1, BUY, 100, 1, M, 2)]


def test_book_drop_only_absorbs_trades_in_the_same_us():
    # Known optimistic limit: a drop at one µs and a trade at a later µs count separately.
    ex = book()
    ex.submit_limit(10, 1, BUY, 100, 3)
    ex.on_book(upd(20, (BUY, 100, 1)))
    assert ex.on_trade(tr(21, SELL, 100, 4)) == [OrderFilled(21, 1, BUY, 100, 3, M, 0)]


def test_book_drop_absorbs_at_most_its_size():
    ex = book()
    ex.submit_limit(10, 1, BUY, 100, 3)
    ex.on_book(upd(20, (BUY, 100, 3)))  # ahead 5 -> 3, drop 2
    # trade 4: 2 were already counted by the drop, 2 more reduce ahead 3 -> 1
    assert ex.on_trade(tr(20, SELL, 100, 4)) == []
    assert ex.queue_ahead(1) == 1


def test_market_moves_through_resting_bid():
    ex = book()
    ex.submit_limit(10, 1, BUY, 100, 3)
    out = ex.on_book(upd(20, (SELL, 101, 0), (SELL, 100, 4)))  # best ask now at our bid
    assert out == [OrderFilled(20, 1, BUY, 100, 3, M, 0)]


def test_snapshot_reset_keeps_order_and_caps_queue():
    ex = book()
    ex.submit_limit(10, 1, BUY, 100, 3)
    ex.on_book(upd(15, (BUY, 99, 1)))
    assert ex.on_book(snap(20, bids=((100, 1),), asks=((101, 9),))) == []
    assert ex.queue_ahead(1) == 1
    assert ex.book.best_bid() == 100 and ex.book.qty_at(BUY, 99) == 0


def test_vanished_level_caps_queue_to_zero():
    ex = book(bids=((100, 5), (99, 5)))
    ex.submit_limit(10, 1, BUY, 99, 3)
    ex.on_book(upd(20, (BUY, 99, 0)))
    assert ex.queue_ahead(1) == 0


def test_snapshot_without_our_level_caps_to_zero():
    ex = book(bids=((100, 5), (99, 5)))
    ex.submit_limit(10, 1, BUY, 99, 3)
    ex.on_book(upd(15, (BUY, 100, 4)))
    ex.on_book(snap(20, bids=((100, 5),), asks=((101, 5),)))
    assert ex.queue_ahead(1) == 0


def test_unknown_side_trade_ignored():
    ex = book()
    ex.submit_limit(10, 1, BUY, 100, 3)
    assert ex.on_trade(tr(20, None, 99, 10)) == []
    assert ex.queue_ahead(1) == 5


def test_cancel_resting_and_cancel_unknown():
    ex = book()
    ex.submit_limit(10, 1, BUY, 100, 3)
    assert ex.cancel(20, 1) == [OrderCancelled(20, 1, 3)]
    assert ex.cancel(30, 1) == [OrderRejected(30, 1, "unknown_order")]
    assert ex.queue_ahead(1) is None


def test_validation_rejections():
    ex = book()
    assert ex.submit_limit(10, 1, BUY, 100, 0) == [OrderRejected(10, 1, "non_positive_qty")]
    assert ex.submit_limit(10, 1, BUY, 0, 1) == [OrderRejected(10, 1, "non_positive_price")]
    ex.submit_limit(10, 1, BUY, 100, 1)
    assert ex.submit_limit(11, 1, BUY, 100, 1) == [OrderRejected(11, 1, "duplicate_id")]
