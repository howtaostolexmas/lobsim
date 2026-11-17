from lobsim import Accepted, Cancelled, Rejected, Side, Trade


def test_non_crossing_limit_rests(make_book):
    b = make_book()
    assert b.add_limit(1, Side.BUY, 100, 5) == [Accepted(1)]
    assert b.best_bid() == 100 and b.best_ask() is None
    assert b.depth(Side.BUY, 5) == [(100, 5)]
    assert b.resting_qty(1) == 5


def test_crossing_limit_trades_at_maker_price(make_book):
    b = make_book()
    b.add_limit(1, Side.SELL, 101, 5)
    ev = b.add_limit(2, Side.BUY, 103, 3)
    assert ev == [Accepted(2), Trade(1, 2, 101, 3, Side.BUY, 2, 0)]
    assert b.depth(Side.SELL, 5) == [(101, 2)] and b.best_bid() is None


def test_equal_price_crosses_and_fifo_within_level(make_book):
    b = make_book()
    b.add_limit(1, Side.SELL, 100, 2)
    b.add_limit(2, Side.SELL, 100, 2)
    ev = b.add_limit(3, Side.BUY, 100, 3)
    assert ev == [
        Accepted(3),
        Trade(1, 3, 100, 2, Side.BUY, 0, 1),
        Trade(2, 3, 100, 1, Side.BUY, 1, 0),
    ]


def test_sell_taker_hits_highest_bid_first(make_book):
    b = make_book()
    b.add_limit(1, Side.BUY, 99, 1)
    b.add_limit(2, Side.BUY, 100, 1)
    ev = b.add_limit(3, Side.SELL, 99, 2)
    assert [(e.maker_id, e.price) for e in ev if isinstance(e, Trade)] == [(2, 100), (1, 99)]


def test_sweeps_multiple_levels_then_rests_remainder(make_book):
    b = make_book()
    b.add_limit(1, Side.SELL, 100, 1)
    b.add_limit(2, Side.SELL, 101, 1)
    ev = b.add_limit(3, Side.BUY, 101, 5)
    assert [e.price for e in ev if isinstance(e, Trade)] == [100, 101]
    assert b.depth(Side.BUY, 5) == [(101, 3)] and b.best_ask() is None


def test_limit_does_not_trade_through_its_price(make_book):
    b = make_book()
    b.add_limit(1, Side.SELL, 102, 1)
    assert b.add_limit(2, Side.BUY, 101, 1) == [Accepted(2)]
    assert b.best_bid() == 101 and b.best_ask() == 102


def test_depth_limits_levels_and_sums_qty(make_book):
    b = make_book()
    b.add_limit(1, Side.BUY, 100, 2)
    b.add_limit(2, Side.BUY, 100, 3)
    b.add_limit(3, Side.BUY, 99, 1)
    assert b.depth(Side.BUY, 1) == [(100, 5)]
    assert b.depth(Side.BUY, 5) == [(100, 5), (99, 1)]


def test_cancel_resting_removes_level(make_book):
    b = make_book()
    b.add_limit(1, Side.BUY, 100, 5)
    assert b.cancel(1) == [Cancelled(1, 5)]
    assert b.best_bid() is None and b.depth(Side.BUY, 5) == []


def test_cancel_keeps_queue_order_of_others(make_book):
    b = make_book()
    for oid in (1, 2, 3):
        b.add_limit(oid, Side.SELL, 100, 1)
    b.cancel(2)
    ev = b.add_limit(4, Side.BUY, 100, 2)
    assert [e.maker_id for e in ev if isinstance(e, Trade)] == [1, 3]


def test_cancel_partially_filled_reports_remaining(make_book):
    b = make_book()
    b.add_limit(1, Side.SELL, 100, 5)
    b.add_limit(2, Side.BUY, 100, 2)
    assert b.cancel(1) == [Cancelled(1, 3)]
    assert b.best_ask() is None and b.resting_qty(1) is None


def test_cancel_fully_filled_is_unknown(make_book):
    b = make_book()
    b.add_limit(1, Side.SELL, 100, 2)
    b.add_limit(2, Side.BUY, 100, 2)
    assert b.cancel(1) == [Rejected(1, "unknown_id")]


def test_depth_total_after_partial_fill_and_middle_cancel(make_book):
    b = make_book()
    for oid in (1, 2, 3):
        b.add_limit(oid, Side.SELL, 100, 10)
    b.add_market(9, Side.BUY, 4)  # order 1 -> 6 left
    b.cancel(2)  # remove from the middle of the level
    assert b.depth(Side.SELL, 1) == [(100, 16)]


def test_level_empties_and_reappears_with_fresh_total(make_book):
    b = make_book()
    b.add_limit(1, Side.SELL, 100, 5)
    b.cancel(1)
    b.add_limit(2, Side.SELL, 100, 3)
    assert b.depth(Side.SELL, 1) == [(100, 3)]
