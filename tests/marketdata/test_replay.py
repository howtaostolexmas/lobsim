import pytest
from conftest import TEST_INST

from lobsim import Side
from lobsim.marketdata.replay import BookBatch, Replayer, TradePrint, batches, events
from lobsim.marketdata.tardis import L2Row, TradeRow, read_l2, read_trades


def l2(ts, snap=False, side=Side.BUY, price=100, qty=1):
    return L2Row(ts, ts, snap, side, price, qty)


def trade(ts, tid):
    return TradeRow(ts, ts, Side.BUY, 100, 1, tid)


def test_batches_group_by_ts_local_and_flag(fixture_l2):
    bs = list(batches(read_l2(fixture_l2, TEST_INST)))
    assert [(b.ts_local, b.is_snapshot, len(b.levels)) for b in bs] == [
        (95, False, 1),
        (100, True, 4),
        (200, False, 2),
        (300, False, 2),
        (400, True, 2),
        (500, False, 1),
    ]
    assert bs[1].levels[0] == (Side.BUY, 10000, 2000)
    assert bs[3].ts_exchange == 120  # first row of the batch


def test_same_ts_but_flag_change_splits_batch():
    bs = list(batches(iter([l2(1, snap=False), l2(1, snap=True)])))
    assert [b.is_snapshot for b in bs] == [False, True]


def test_full_fixture_replay_final_book(fixture_l2):
    r = Replayer()
    for b in batches(read_l2(fixture_l2, TEST_INST)):
        r.apply(b)
    assert r.book.depth(Side.BUY, 5) == [(9800, 1000)]
    assert r.book.depth(Side.SELL, 5) == [(9850, 1000)]
    assert r.last_ts_local == 500


def test_updates_before_first_snapshot_are_skipped():
    r = Replayer()
    r.apply(BookBatch(1, 1, False, ((Side.BUY, 100, 5),)))
    assert not r.ready and r.book.best_bid() is None


def test_snapshot_after_updates_resets_book():
    r = Replayer()
    r.apply(BookBatch(1, 1, True, ((Side.BUY, 100, 5), (Side.SELL, 101, 5))))
    r.apply(BookBatch(2, 2, False, ((Side.BUY, 99, 3),)))
    r.apply(BookBatch(3, 3, True, ((Side.BUY, 98, 1),)))
    assert r.book.depth(Side.BUY, 5) == [(98, 1)] and r.book.best_ask() is None


def test_multi_batch_snapshot_clears_only_once():
    r = Replayer()
    r.apply(BookBatch(1, 1, True, ((Side.BUY, 100, 5),)))
    r.apply(BookBatch(2, 2, True, ((Side.SELL, 101, 5),)))  # same snapshot, next message
    assert r.book.best_bid() == 100 and r.book.best_ask() == 101


def test_zero_for_absent_level_is_noop():
    r = Replayer()
    r.apply(BookBatch(1, 1, True, ((Side.BUY, 100, 5),)))
    r.apply(BookBatch(2, 2, False, ((Side.SELL, 200, 0),)))
    assert r.book.depth(Side.BUY, 5) == [(100, 5)] and r.book.best_ask() is None


def test_merge_order_on_fixture(fixture_l2, fixture_trades):
    stream = events(read_l2(fixture_l2, TEST_INST), read_trades(fixture_trades, TEST_INST))
    labels = [
        f"B{e.ts_local}" if isinstance(e, BookBatch) else f"T{e.trade_id}" for e in stream
    ]
    assert labels == ["B95", "B100", "B200", "Tt1", "Tt2", "B300", "B400", "Tt3", "B500"]


def test_merge_tie_book_before_trade_and_trades_keep_file_order():
    stream = list(events(iter([l2(5)]), iter([trade(5, "a"), trade(5, "b")])))
    assert isinstance(stream[0], BookBatch)
    assert [e.trade_id for e in stream[1:]] == ["a", "b"]
    assert isinstance(stream[1], TradePrint)


def test_events_rejects_unsorted_input():
    with pytest.raises(ValueError, match="ts_local"):
        list(events(iter([l2(5), l2(3)]), iter([])))
    with pytest.raises(ValueError, match="ts_local"):
        list(events(iter([]), iter([trade(5, "a"), trade(3, "b")])))
