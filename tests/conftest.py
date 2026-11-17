"""Run every engine test against both implementations.

Tests take a `make_book` fixture and call make_book() instead of naming an
engine class, so the Python and C++ engines share one identical suite.
"""

import pytest


def _python():
    from lobsim import OrderBook

    return OrderBook


def _cpp():
    from lobsim.fast import FastOrderBook

    return FastOrderBook


@pytest.fixture(params=["python", "cpp"])
def make_book(request):
    return _python() if request.param == "python" else _cpp()
