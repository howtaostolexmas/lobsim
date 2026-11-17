"""Shared fixtures for market data tests.

The CSV files in tests/data are hand-written synthetic rows in Tardis format
(never copied from real downloads, which may not be redistributed).
"""

from pathlib import Path

import pytest

from lobsim.marketdata.instrument import Instrument

DATA = Path(__file__).resolve().parent.parent / "data"
TEST_INST = Instrument("TEST", price_decimals=2, qty_decimals=3)


@pytest.fixture
def fixture_l2() -> Path:
    return DATA / "l2_sample.csv"


@pytest.fixture
def fixture_trades() -> Path:
    return DATA / "trades_sample.csv"


@pytest.fixture
def fixture_snap() -> Path:
    return DATA / "snap25_sample.csv"
