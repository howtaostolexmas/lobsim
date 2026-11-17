import pytest

from lobsim import LevelBook, Side


def test_set_update_remove_level():
    lb = LevelBook()
    lb.set_level(Side.BUY, 100, 5)
    lb.set_level(Side.BUY, 101, 2)
    assert lb.depth(Side.BUY, 5) == [(101, 2), (100, 5)]
    lb.set_level(Side.BUY, 100, 7)  # an update replaces, it does not add
    assert lb.qty_at(Side.BUY, 100) == 7
    lb.set_level(Side.BUY, 101, 0)
    assert lb.best_bid() == 100 and lb.qty_at(Side.BUY, 101) == 0


def test_removing_absent_level_is_noop():
    lb = LevelBook()
    lb.set_level(Side.SELL, 100, 0)
    assert lb.best_ask() is None


def test_sides_are_independent_and_crossing_allowed():
    lb = LevelBook()
    lb.set_level(Side.BUY, 101, 1)
    lb.set_level(Side.SELL, 100, 1)  # real feeds can briefly cross
    assert lb.best_bid() == 101 and lb.best_ask() == 100


def test_invalid_inputs_raise():
    lb = LevelBook()
    with pytest.raises(ValueError):
        lb.set_level(Side.SELL, 100, -1)
    with pytest.raises(ValueError):
        lb.set_level(Side.SELL, 0, 1)


def test_depth_limit_and_clear():
    lb = LevelBook()
    for p in (100, 101, 102):
        lb.set_level(Side.SELL, p, 1)
    assert lb.depth(Side.SELL, 2) == [(100, 1), (101, 1)]
    lb.clear()
    assert lb.best_ask() is None and lb.depth(Side.SELL, 5) == []
