from helpers import BUY, SELL, snap, tr, upd

from lobsim.backtest import OrderFilled, OrderRejected, SimConfig
from lobsim.backtest.engine import Backtest, BookView, Context
from lobsim.backtest.exchange import SimExchange
from lobsim.backtest.strategy import Strategy
from lobsim.marketdata.replay import BookBatch, Replayer

CFG = SimConfig(order_latency_us=1_000, feed_latency_us=1_000, maker_fee_bps=0, taker_fee_bps=0)


class Recorder(Strategy):
    """Checks, inside every callback, that nothing from the future is visible."""

    def __init__(self, feed_latency):
        self.feed = feed_latency
        self.seen = []
        self.replica = Replayer()
        self.notices = []

    def on_book(self, ctx, batch):
        assert batch.ts_local + self.feed == ctx.now
        self.replica.apply(batch)
        self.seen.append(batch)
        self._check(ctx)

    def on_trade(self, ctx, trade):
        assert trade.ts_local + self.feed == ctx.now
        self.seen.append(trade)
        self._check(ctx)

    def on_notice(self, ctx, notice):
        assert notice.ts + self.feed == ctx.now
        self.notices.append(notice)

    def _check(self, ctx):
        assert all(e.ts_local + self.feed <= ctx.now for e in self.seen)
        for side in (BUY, SELL):
            assert ctx.book.depth(side, 50) == self.replica.book.depth(side, 50)


def market():
    return [
        snap(0, bids=((99, 5),), asks=((101, 5),)),
        upd(500, (BUY, 99, 7)),
        tr(700, SELL, 99, 1),
        upd(1500, (SELL, 101, 2)),
        tr(1800, BUY, 101, 1),
        upd(2600, (BUY, 98, 3)),
    ]


def test_strategy_never_sees_future_data():
    rec = Recorder(CFG.feed_latency_us)
    Backtest(rec, CFG).run(market())
    assert len(rec.seen) == 6  # everything is eventually delivered, in order
    assert [e.ts_local for e in rec.seen] == [0, 500, 700, 1500, 1800, 2600]


def test_context_has_no_path_to_exchange_or_market():
    seen_types = set()

    class Snoop(Strategy):
        def on_book(self, ctx, batch):
            stack, visited = [ctx], set()
            while stack:
                obj = stack.pop()
                if id(obj) in visited:
                    continue
                visited.add(id(obj))
                seen_types.add(type(obj))
                for attr in getattr(obj, "__dict__", {}).values():
                    stack.append(attr)
                for name in getattr(type(obj), "__slots__", ()):
                    if hasattr(obj, name):
                        stack.append(getattr(obj, name))
                if isinstance(obj, (list, tuple, set)):
                    stack.extend(obj)
                if isinstance(obj, dict):
                    stack.extend(obj.values())

    Backtest(Snoop(), CFG).run(market())
    assert Context in seen_types and BookView in seen_types
    assert SimExchange not in seen_types and Backtest not in seen_types


class PlaceOnce(Strategy):
    """Places one bid the first time it sees a book."""

    def __init__(self, side=BUY, price=100, qty=2):
        self.args = (side, price, qty)
        self.order_id = None
        self.notices = []
        self.cancel_at = None

    def on_book(self, ctx, batch):
        if self.order_id is None:
            self.order_id = ctx.place_limit(*self.args)
        elif self.cancel_at is not None and ctx.now >= self.cancel_at and self.order_id in ctx.open_orders:
            ctx.cancel(self.order_id)
            self.cancel_at = None

    def on_notice(self, ctx, notice):
        self.notices.append((ctx.now, notice))


def test_order_latency_order_misses_trade_before_arrival():
    # Book delivered at 1000 -> bid 100 placed at 1000 -> arrives at 2000.
    events = [snap(0, bids=((99, 5),), asks=((101, 5),)), tr(1999, SELL, 100, 1), tr(2000, SELL, 100, 1)]
    s = PlaceOnce()
    result = Backtest(s, CFG).run(events)
    assert result.fills == []  # 1999: not arrived; 2000: market is processed before arrival


def test_order_can_fill_after_arrival():
    events = [snap(0, bids=((99, 5),), asks=((101, 5),)), tr(2001, SELL, 100, 1)]
    s = PlaceOnce()
    result = Backtest(s, CFG).run(events)
    assert [(f.ts, f.qty) for f in result.fills] == [(2001, 1)]


def test_fill_notice_delayed_and_position_updates_on_delivery():
    positions = []

    class Watch(PlaceOnce):
        def on_trade(self, ctx, trade):
            positions.append((ctx.now, ctx.position))

        def on_notice(self, ctx, notice):
            positions.append((ctx.now, ctx.position))

    events = [
        snap(0, bids=((99, 5),), asks=((101, 5),)),
        tr(2001, SELL, 100, 1),  # fills us at the exchange at 2001
        tr(2500, SELL, 99, 1),  # delivered at 3500 (after our fill notice at 3001)
    ]
    Backtest(Watch(), CFG).run(events)
    # Accepted at 2000 -> seen 3000. Trade 2001 fills 1 lot: the trade print and the
    # fill notice are both delivered at 3001, print first. Trade 2500 at 99 trades
    # through our bid and fills the last lot: delivered at 3500.
    assert positions == [(3000, 0), (3001, 0), (3001, 1), (3500, 1), (3500, 2)]


def test_cancel_racing_fill_is_rejected_and_fill_reported():
    s = PlaceOnce(qty=1)
    s.cancel_at = 1500  # first book seen after 1500 triggers the cancel
    events = [
        snap(0, bids=((99, 5),), asks=((101, 5),)),
        upd(600, (BUY, 99, 6)),  # delivered 1600 -> strategy sends cancel, arrives 2600
        tr(2100, SELL, 100, 1),  # our bid (arrived 2000) fills at 2100, before the cancel lands
    ]
    result = Backtest(s, CFG).run(events)
    kinds = [type(n).__name__ for _, n in s.notices]
    assert kinds == ["OrderAccepted", "OrderFilled", "OrderRejected"]
    assert s.notices[-1][1] == OrderRejected(2600, s.order_id, "unknown_order")
    assert result.final_position == 1 and len(result.fills) == 1


def test_deterministic_two_runs_identical():
    a = Backtest(PlaceOnce(), CFG).run(market() + [tr(3000, SELL, 100, 5)])
    b = Backtest(PlaceOnce(), CFG).run(market() + [tr(3000, SELL, 100, 5)])
    assert a.fills == b.fills and a.fills


def test_market_order_through_engine_is_taker():
    class Taker(Strategy):
        done = False

        def on_book(self, ctx, batch):
            if not self.done:
                ctx.place_market(BUY, 3)
                self.done = True

    result = Backtest(Taker(), CFG).run([snap(0, bids=((99, 5),), asks=((101, 2), (102, 5)))])
    assert [(f.price, f.qty, f.liquidity.value) for f in result.fills] == [(101, 2, "taker"), (102, 1, "taker")]
    assert isinstance(result.fills[0], OrderFilled)


def test_batch_type_passed_to_on_book():
    got = []

    class T(Strategy):
        def on_book(self, ctx, batch):
            got.append(type(batch))

    Backtest(T(), CFG).run(market())
    assert set(got) == {BookBatch}
