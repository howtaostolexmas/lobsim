import pytest

from lobsim import PriceLevels, Side


def test_buy_side_best_is_highest():
    lv = PriceLevels(Side.BUY)
    for p in (100, 105, 99):
        lv.set(p, p)
    assert lv.best() == 105
    assert [p for p, _ in lv.items_from_best()] == [105, 100, 99]


def test_sell_side_best_is_lowest():
    lv = PriceLevels(Side.SELL)
    for p in (100, 105, 99):
        lv.set(p, p)
    assert lv.best() == 99
    assert [p for p, _ in lv.items_from_best()] == [99, 100, 105]


def test_empty_best_is_none_and_remove_missing_raises():
    lv = PriceLevels(Side.BUY)
    assert lv.best() is None and len(lv) == 0
    with pytest.raises(KeyError):
        lv.remove(1)


def test_get_contains_and_remove():
    lv = PriceLevels(Side.SELL)
    lv.set(100, "a")
    assert lv.get(100) == "a" and 100 in lv and lv.get(101) is None
    lv.remove(100)
    assert 100 not in lv and len(lv) == 0


def test_side_opposite():
    assert Side.BUY.opposite is Side.SELL and Side.SELL.opposite is Side.BUY


def test_best_item_returns_price_and_value():
    lv = PriceLevels(Side.BUY)
    assert lv.best_item() is None
    lv.set(100, "a")
    lv.set(101, "b")
    assert lv.best_item() == (101, "b")
    assert PriceLevels(Side.SELL).best_item() is None
