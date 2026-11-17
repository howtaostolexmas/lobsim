import json
from pathlib import Path

from lobsim.backtest import __main__ as bt_cli
from lobsim.marketdata import __main__ as md_cli
from lobsim.marketdata.instrument import Instrument
from lobsim.marketdata.normalize import normalize_l2, normalize_trades

DATA = Path(__file__).resolve().parent.parent / "data"
TEST_INST = Instrument("TEST", price_decimals=2, qty_decimals=3)


def test_cli_runs_on_fixtures(tmp_path, monkeypatch, capsys):
    monkeypatch.setitem(md_cli.INSTRUMENTS, "TEST", TEST_INST)
    norm = tmp_path / "normalized"
    normalize_l2(DATA / "l2_sample.csv", norm / "l2_2026-11-01_TEST.parquet", TEST_INST)
    normalize_trades(DATA / "trades_sample.csv", norm / "trades_2026-11-01_TEST.parquet", TEST_INST)
    out_json = tmp_path / "r.json"
    code = bt_cli.main(
        ["--day", "2026-11-01", "--symbol", "TEST", "--data-dir", str(tmp_path),
         "--size-lots", "1", "--order-latency-us", "5", "--feed-latency-us", "5", "--json", str(out_json)]
    )
    assert code == 0
    summary = json.loads(out_json.read_text())
    assert {"pnl_quote", "fees_quote", "fill_ratio", "config"} <= set(summary)
    assert summary["config"]["order_latency_us"] == 5
    assert "pnl" in capsys.readouterr().out
