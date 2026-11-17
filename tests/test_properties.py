"""Property-based tests: random order sequences must behave identically on
the real OrderBook and the deliberately naive reference engine."""

from hypothesis import given, settings
from hypothesis import strategies as st
from reference import NaiveOrderBook

from bench.orderflow import apply

from lobsim import Accepted, Cancelled, OrderBook, Side, Trade

# Small id and price ranges make duplicates, cancels of live orders and
# crossing trades frequent. qty can be -1 or 0 so rejections get exercised.
ids = st.integers(1, 30)
sides = st.sampled_from([Side.BUY, Side.SELL])
prices = st.integers(95, 105)
qtys = st.integers(-1, 10)

op = st.one_of(
    st.tuples(st.just("limit"), ids, sides, prices, qtys),
    st.tuples(st.just("market"), ids, sides, qtys),
    st.tuples(st.just("cancel"), ids),
)


def ops():
    return st.lists(op, min_size=1, max_size=200)


@settings(max_examples=500)
@given(ops())
def test_matches_reference(ops):
    a, b = OrderBook(), NaiveOrderBook()
    for o in ops:
        assert apply(a, o) == apply(b, o)
        assert a.depth(Side.BUY, 100) == b.depth(Side.BUY, 100)
        assert a.depth(Side.SELL, 100) == b.depth(Side.SELL, 100)


@given(ops())
def test_book_never_crossed(ops):
    b = OrderBook()
    for o in ops:
        apply(b, o)
        bid, ask = b.best_bid(), b.best_ask()
        assert bid is None or ask is None or bid < ask


@given(ops())
def test_quantity_conserved(ops):
    """For every accepted order: original == filled + cancelled + resting."""
    b = OrderBook()
    life: dict[int, dict[str, int]] = {}  # current lifetime of each order id

    def check(oid):
        r = life[oid]
        resting = b.resting_qty(oid) or 0
        assert r["orig"] == r["filled"] + r["cancelled"] + resting

    for o in ops:
        events = apply(b, o)
        for e in events:
            if isinstance(e, Accepted):
                # A reused id starts a new lifetime. The previous one was
                # already checked after the op that ended it.
                qty = o[-1]
                life[e.order_id] = {"orig": qty, "filled": 0, "cancelled": 0}
            elif isinstance(e, Trade):
                life[e.maker_id]["filled"] += e.qty
                life[e.taker_id]["filled"] += e.qty
            elif isinstance(e, Cancelled):
                life[e.order_id]["cancelled"] += e.remaining_qty
        for oid in life:
            check(oid)
