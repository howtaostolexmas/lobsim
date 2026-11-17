from helpers import BUY, SELL, snap, tr, upd

from lobsim.backtest import SimConfig
from lobsim.backtest.engine import Backtest
from lobsim.backtest.strategy import NaiveMarketMaker

CFG = SimConfig(order_latency_us=10, feed_latency_us=10, maker_fee_bps=0, taker_fee_bps=0)


class Spy(NaiveMarketMaker):
    def on_end(self, ctx):
        self.final_quotes = sorted((o.side.value, o.price) for o in ctx.open_orders.values())


def test_market_maker_quotes_both_sides_at_best():
    mm = Spy(size_lots=1, max_position_lots=5)
    r = Backtest(mm, CFG).run([snap(0, bids=((99, 5),), asks=((101, 5),))])
    assert mm.final_quotes == [("buy", 99), ("sell", 101)]
    assert r.orders_placed == 2


def test_market_maker_requotes_on_best_change():
    mm = Spy(size_lots=1, max_position_lots=5)
    events = [
        snap(0, bids=((99, 5),), asks=((101, 5),)),
        upd(100, (BUY, 100, 3)),  # new best bid
        upd(200, (SELL, 101, 6)),  # just another update, lets the requote happen
    ]
    Backtest(mm, CFG).run(events)
    assert mm.final_quotes == [("buy", 100), ("sell", 101)]


def test_market_maker_respects_position_limit():
    mm = Spy(size_lots=1, max_position_lots=1)
    events = [
        snap(0, bids=((99, 5),), asks=((101, 5),)),
        tr(100, SELL, 98, 1),  # trades through our bid at 99 -> we buy 1, position 1 = limit
        upd(200, (BUY, 99, 4)),
        upd(300, (BUY, 99, 3)),
    ]
    r = Backtest(mm, CFG).run(events)
    assert r.final_position == 1
    assert mm.final_quotes == [("sell", 101)]  # no new bid at the limit
