from conftest import TEST_INST

from lobsim import LevelBook, Side
from lobsim.marketdata.tardis import read_l2
from lobsim.marketdata.validate import Snapshot, read_snapshots, top_uncrossed, validate


def test_read_snapshots_parses_levels_and_stops_at_empty(fixture_snap):
    snaps = list(read_snapshots(fixture_snap, TEST_INST))
    assert len(snaps) == 4
    assert snaps[0] == Snapshot(100, [(10000, 2000), (9950, 1500)], [(10050, 3000), (10100, 1000)])
    assert snaps[1].asks == [(10100, 1000)]


def test_validate_counts_matches_and_reports_mismatch(fixture_l2, fixture_snap):
    report = validate(read_l2(fixture_l2, TEST_INST), read_snapshots(fixture_snap, TEST_INST), n=2)
    assert report.checked == 4 and report.matched == 3
    assert report.first_mismatches[0]["ts_local"] == 400
    assert report.first_mismatches[0]["expected_asks"] == [(9850, 9999)]
    assert report.first_mismatches[0]["actual_asks"] == [(9850, 2000)]


def test_top_uncrossed_plain_book():
    lb = LevelBook()
    for p in (99, 98, 97):
        lb.set_level(Side.BUY, p, 1)
    lb.set_level(Side.SELL, 100, 2)
    assert top_uncrossed(lb, 2) == ([(99, 1), (98, 1)], [(100, 2)])


def test_crossed_book_is_compared_after_uncrossing():
    lb = LevelBook()
    lb.set_level(Side.BUY, 101, 1)  # crosses the ask at 100
    lb.set_level(Side.BUY, 99, 1)
    lb.set_level(Side.SELL, 100, 1)
    lb.set_level(Side.SELL, 102, 1)
    bids, asks = top_uncrossed(lb, 5)
    assert bids[0][0] < asks[0][0]  # never returns a crossed pair
    assert (101, 1) not in bids and (100, 1) not in asks


def test_validate_counts_crossed_moments():
    from lobsim.marketdata.replay import BookBatch  # noqa: F401  (documents the input type)
    from lobsim.marketdata.tardis import L2Row

    rows = [
        L2Row(1, 1, True, Side.BUY, 99, 1),
        L2Row(1, 1, True, Side.SELL, 100, 1),
        L2Row(2, 2, False, Side.BUY, 100, 1),  # bid 100 now crosses ask 100
    ]
    snaps = [Snapshot(2, [(99, 1)], [])]
    report = validate(iter(rows), iter(snaps), n=5)
    assert report.crossed_moments == 1 and report.matched == 1
