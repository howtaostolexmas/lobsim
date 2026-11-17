import gzip

import pytest
from conftest import TEST_INST

from lobsim import Side
from lobsim.marketdata.normalize import (
    normalize_l2,
    normalize_trades,
    read_l2_parquet,
    read_trades_parquet,
)
from lobsim.marketdata.tardis import L2Row, TradeRow, read_l2, read_trades


def test_read_l2_parses_types(fixture_l2):
    rows = list(read_l2(fixture_l2, TEST_INST))
    assert len(rows) == 12
    assert rows[0] == L2Row(90, 95, False, Side.BUY, 9900, 1000)
    assert rows[1] == L2Row(100, 100, True, Side.BUY, 10000, 2000)
    assert rows[6] == L2Row(110, 200, False, Side.SELL, 10050, 0)
    assert all(isinstance(r.price, int) and isinstance(r.qty, int) for r in rows)


def test_read_trades(fixture_trades):
    rows = list(read_trades(fixture_trades, TEST_INST))
    assert rows[0] == TradeRow(150, 200, Side.BUY, 10050, 500, "t1")
    assert rows[1].side is Side.SELL
    assert rows[2].side is None  # "unknown"


def test_parquet_roundtrip_identical(tmp_path, fixture_l2, fixture_trades):
    n = normalize_l2(fixture_l2, tmp_path / "l2.parquet", TEST_INST)
    assert n == 12
    assert list(read_l2_parquet(tmp_path / "l2.parquet")) == list(read_l2(fixture_l2, TEST_INST))
    m = normalize_trades(fixture_trades, tmp_path / "t.parquet", TEST_INST)
    assert m == 3
    assert list(read_trades_parquet(tmp_path / "t.parquet")) == list(
        read_trades(fixture_trades, TEST_INST)
    )


def test_parquet_writes_in_batches(tmp_path, fixture_l2):
    normalize_l2(fixture_l2, tmp_path / "l2.parquet", TEST_INST, batch_rows=5)
    assert list(read_l2_parquet(tmp_path / "l2.parquet")) == list(read_l2(fixture_l2, TEST_INST))


def test_gz_and_plain_csv_read_the_same(tmp_path, fixture_l2):
    gz = tmp_path / "l2.csv.gz"
    gz.write_bytes(gzip.compress(fixture_l2.read_bytes()))
    assert list(read_l2(gz, TEST_INST)) == list(read_l2(fixture_l2, TEST_INST))


def test_off_tick_price_in_file_raises_with_context(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text(
        "exchange,symbol,timestamp,local_timestamp,is_snapshot,side,price,amount\n"
        "test,TEST,1,1,true,bid,100.00,1\n"
        "test,TEST,2,2,false,bid,100.001,1\n"
    )
    with pytest.raises(ValueError, match=r"100\.001.*line 3|line 3.*100\.001"):
        list(read_l2(bad, TEST_INST))


def test_unknown_book_side_raises(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text(
        "exchange,symbol,timestamp,local_timestamp,is_snapshot,side,price,amount\n"
        "test,TEST,1,1,true,middle,100.00,1\n"
    )
    with pytest.raises(ValueError, match="middle"):
        list(read_l2(bad, TEST_INST))


def test_unknown_snapshot_flag_raises(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text(
        "exchange,symbol,timestamp,local_timestamp,is_snapshot,side,price,amount\n"
        "test,TEST,1,1,True,bid,100.00,1\n"
    )
    with pytest.raises(ValueError, match="is_snapshot"):
        list(read_l2(bad, TEST_INST))
