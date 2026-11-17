import pytest

from lobsim.marketdata.instrument import BTCUSDT, Instrument, to_units


@pytest.mark.parametrize(
    "text,dec,expected",
    [
        ("67000", 2, 6700000),
        ("67000.1", 2, 6700010),
        ("67000.10", 2, 6700010),
        ("0.00001", 5, 1),
        ("1e-05", 5, 1),
        ("0", 5, 0),
        ("12.50000", 2, 1250),
        ("0.5", 5, 50000),
        ("6.7e4", 2, 6700000),
        ("1E-5", 5, 1),
    ],
)
def test_to_units_exact(text, dec, expected):
    assert to_units(text, dec) == expected


@pytest.mark.parametrize(
    "text,dec",
    [
        ("67000.001", 2),
        ("1e-06", 5),
        ("abc", 2),
        ("", 2),
        ("-", 2),
        ("1.2.3", 2),
        ("\u0663", 2),  # Arabic-Indic digit three
        ("\uff11\uff12", 2),  # fullwidth "12"
        ("-5", 2),  # prices and sizes are never negative
        ("+5", 2),
        (" 5", 2),
        ("1_000", 2),
        ("1.0000000000000000000000000001", 2),  # beyond Decimal's default precision
        ("\u00b2", 2),  # superscript two
        ("nan", 2),
        ("inf", 2),
    ],
)
def test_to_units_rejects_off_tick_or_garbage(text, dec):
    with pytest.raises(ValueError):
        to_units(text, dec)


def test_error_message_names_the_value():
    with pytest.raises(ValueError, match="67000.001"):
        to_units("67000.001", 2)


def test_instrument_helpers():
    assert BTCUSDT.price("67000.12") == 6700012
    assert BTCUSDT.qty("0.5") == 50000
    assert Instrument("X", 0, 0).price("7") == 7
