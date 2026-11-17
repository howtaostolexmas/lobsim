import gzip
import urllib.error
from datetime import date

import pytest
from conftest import TEST_INST

from lobsim.marketdata import __main__ as cli
from lobsim.marketdata.stats import replay_stats
from lobsim.marketdata.tardis import download, read_l2, read_trades, tardis_url

DAY = date(2026, 11, 1)


def test_tardis_url():
    assert (
        tardis_url("binance", "trades", DAY, "BTCUSDT")
        == "https://datasets.tardis.dev/v1/binance/trades/2026/11/01/BTCUSDT.csv.gz"
    )


def test_download_skips_existing_and_uses_part_file(tmp_path):
    calls = []

    def fake_fetch(url, out):
        calls.append(url)
        out.write(gzip.compress(b"a,b\n"))

    p1 = download("binance", "trades", DAY, "X", tmp_path, fetch=fake_fetch)
    p2 = download("binance", "trades", DAY, "X", tmp_path, fetch=fake_fetch)
    assert p1 == p2 == tmp_path / "binance_trades_2026-11-01_X.csv.gz"
    assert p1.exists() and len(calls) == 1 and not list(tmp_path.glob("*.part"))


def test_download_rejects_non_gzip(tmp_path):
    with pytest.raises(ValueError, match="gzip"):
        download("binance", "trades", DAY, "X", tmp_path, fetch=lambda u, out: out.write(b"<html>"))
    assert not list(tmp_path.iterdir())


def test_download_401_explains_free_days(tmp_path):
    def deny(url, out):
        raise urllib.error.HTTPError(url, 401, "Unauthorized", None, None)

    with pytest.raises(RuntimeError, match="day 1"):
        download("binance", "trades", date(2026, 11, 2), "X", tmp_path, fetch=deny)
    assert not list(tmp_path.iterdir())


def test_replay_stats_on_fixture(fixture_l2, fixture_trades):
    s = replay_stats(read_l2(fixture_l2, TEST_INST), read_trades(fixture_trades, TEST_INST))
    assert (s.l2_rows, s.batches, s.trades) == (12, 6, 3)
    assert s.spread_time_us == {50: 200, 100: 200}
    assert s.crossed_batches == 0


@pytest.fixture
def data_dir(tmp_path, fixture_l2, fixture_trades, fixture_snap, monkeypatch):
    raw = tmp_path / "raw"
    raw.mkdir()
    for dtype, src in [
        ("incremental_book_L2", fixture_l2),
        ("trades", fixture_trades),
        ("book_snapshot_25", fixture_snap),
    ]:
        (raw / f"binance_{dtype}_2026-11-01_TEST.csv.gz").write_bytes(gzip.compress(src.read_bytes()))
    monkeypatch.setitem(cli.INSTRUMENTS, "TEST", TEST_INST)
    return tmp_path


def test_cli_normalize_validate_stats(data_dir, capsys):
    common = ["--day", "2026-11-01", "--symbol", "TEST", "--data-dir", str(data_dir)]
    assert cli.main(["normalize", *common]) == 0
    assert (data_dir / "normalized" / "l2_2026-11-01_TEST.parquet").exists()
    assert cli.main(["validate", *common]) == 1  # the fixture has one deliberate mismatch
    out = capsys.readouterr().out
    assert "checked 4" in out and "matched 3" in out
    assert cli.main(["stats", *common]) == 0
    assert "batches" in capsys.readouterr().out


def test_buckets_survive_one_sided_gap_spanning_buckets():
    from lobsim import Side
    from lobsim.marketdata.tardis import L2Row

    rows = [
        L2Row(0, 0, True, Side.BUY, 100, 1),
        L2Row(0, 0, True, Side.SELL, 101, 1),  # spread 1, mid 100.5
        L2Row(50, 50, False, Side.SELL, 101, 0),  # one-sided from 50 to 420
        L2Row(420, 420, False, Side.SELL, 102, 1),  # spread 2, mid 101
        L2Row(480, 480, False, Side.BUY, 100, 2),  # same spread; replay ends at 480
    ]
    s = replay_stats(iter(rows), iter([]), bucket_us=100)
    assert s.spread_time_us == {1: 50, 2: 60}
    assert s.buckets == [(0, 100.5, 1.0), (400, 101.0, 2.0)]
