"""C++-specific edges: Python ints are unbounded, the C++ engine uses int64."""

import pytest

from lobsim import Accepted, Cancelled, Side
from lobsim.fast import FastOrderBook

INT64_MAX = 2**63 - 1


def test_int64_max_roundtrip():
    b = FastOrderBook()
    assert b.add_limit(INT64_MAX, Side.BUY, INT64_MAX, 1) == [Accepted(INT64_MAX)]
    assert b.best_bid() == INT64_MAX
    assert b.cancel(INT64_MAX) == [Cancelled(INT64_MAX, 1)]


@pytest.mark.parametrize("field", ["id", "price", "qty"])
def test_out_of_int64_range_raises_typeerror(field):
    args = {"id": 1, "price": 100, "qty": 1}
    args[field] = 2**63
    with pytest.raises(TypeError):
        FastOrderBook().add_limit(args["id"], Side.BUY, args["price"], args["qty"])


def test_run_ops_counts_events_and_reports_time():
    from bench.orderflow import generate
    from lobsim._core import CoreOrderBook

    from lobsim.fast import encode_ops

    events, elapsed_ns = CoreOrderBook().run_ops(encode_ops(generate(1_000, seed=1)))
    assert events > 1_000 and elapsed_ns > 0
