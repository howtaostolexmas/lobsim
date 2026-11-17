"""Tiny builders for hand-written market event sequences."""

from lobsim import Side
from lobsim.marketdata.replay import BookBatch, TradePrint

BUY, SELL = Side.BUY, Side.SELL


def snap(ts, bids=(), asks=()):
    levels = tuple((BUY, p, q) for p, q in bids) + tuple((SELL, p, q) for p, q in asks)
    return BookBatch(ts, ts, True, levels)


def upd(ts, *levels):
    """upd(ts, (side, price, qty), ...)"""
    return BookBatch(ts, ts, False, tuple(levels))


def tr(ts, taker, price, qty, tid="t"):
    return TradePrint(ts, ts, taker, price, qty, tid)
