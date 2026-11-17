import pytest

from lobsim import Accepted, Cancelled, OrderBook, Rejected, Side, Trade


def test_market_into_empty_book_cancels_all():
    assert OrderBook().add_market(1, Side.BUY, 5) == [Accepted(1), Cancelled(1, 5)]


def test_market_partial_then_cancel_remainder():
    b = OrderBook()
    b.add_limit(1, Side.SELL, 100, 2)
    assert b.add_market(2, Side.BUY, 5) == [
        Accepted(2),
        Trade(1, 2, 100, 2, Side.BUY, 0, 3),
        Cancelled(2, 3),
    ]
    assert b.best_bid() is None


def test_market_fully_filled_has_no_cancel_and_never_rests():
    b = OrderBook()
    b.add_limit(1, Side.BUY, 100, 2)
    b.add_limit(2, Side.BUY, 90, 2)
    ev = b.add_market(3, Side.SELL, 3)
    assert ev == [
        Accepted(3),
        Trade(1, 3, 100, 2, Side.SELL, 0, 1),
        Trade(2, 3, 90, 1, Side.SELL, 1, 0),
    ]
    assert b.resting_qty(3) is None and b.best_ask() is None


@pytest.mark.parametrize("qty", [0, -1])
def test_non_positive_qty_rejected(qty):
    b = OrderBook()
    assert b.add_limit(1, Side.BUY, 100, qty) == [Rejected(1, "non_positive_qty")]
    assert b.add_market(1, Side.BUY, qty) == [Rejected(1, "non_positive_qty")]
    assert b.best_bid() is None


@pytest.mark.parametrize("price", [0, -5])
def test_non_positive_price_rejected(price):
    b = OrderBook()
    assert b.add_limit(1, Side.BUY, price, 1) == [Rejected(1, "non_positive_price")]
    assert b.best_bid() is None


def test_qty_checked_before_price():
    assert OrderBook().add_limit(1, Side.BUY, 0, 0) == [Rejected(1, "non_positive_qty")]


def test_duplicate_resting_id_rejected_but_reuse_after_fill_ok():
    b = OrderBook()
    b.add_limit(1, Side.BUY, 100, 1)
    assert b.add_limit(1, Side.BUY, 99, 1) == [Rejected(1, "duplicate_id")]
    assert b.add_market(1, Side.SELL, 1) == [Rejected(1, "duplicate_id")]
    assert b.depth(Side.BUY, 5) == [(100, 1)]
    b.add_limit(2, Side.SELL, 100, 1)  # fills order 1
    assert b.add_limit(1, Side.BUY, 99, 1) == [Accepted(1)]


def test_reuse_after_cancel_ok():
    b = OrderBook()
    b.add_limit(1, Side.BUY, 100, 1)
    b.cancel(1)
    assert b.add_limit(1, Side.BUY, 100, 1) == [Accepted(1)]
