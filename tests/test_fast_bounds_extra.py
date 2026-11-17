"""C++ edge cases found in review: level-total overflow and bad run_ops input."""

import pytest

from lobsim import Side
from lobsim._core import CoreOrderBook
from lobsim.fast import FastOrderBook


def test_level_total_overflow_raises_and_leaves_book_unchanged():
    b = FastOrderBook()
    b.add_limit(1, Side.BUY, 100, 2**62)
    b.add_limit(2, Side.BUY, 100, 2**62 - 1)  # total = 2**63 - 1, still fits
    with pytest.raises(OverflowError):
        b.add_limit(3, Side.BUY, 100, 1)  # total would exceed int64
    assert b.depth(Side.BUY, 1) == [(100, 2**63 - 1)]
    assert b.resting_qty(3) is None


@pytest.mark.parametrize("bad", [[0, 1, True, 100, 1], (0, 1), (0, "x", True, 100, 1)])
def test_run_ops_rejects_malformed_ops_with_typeerror(bad):
    with pytest.raises(TypeError):
        CoreOrderBook().run_ops([bad])


def test_run_ops_rejects_unknown_kind():
    with pytest.raises((TypeError, ValueError)):
        CoreOrderBook().run_ops([(7, 1, True, 100, 1)])
