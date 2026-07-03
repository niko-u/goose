from labvault.normalize import (
    compute_flag,
    parse_date,
    parse_reference_range,
    parse_value,
)


def test_parse_plain_number():
    pv = parse_value("105")
    assert pv.value_num == 105.0
    assert pv.comparator is None


def test_parse_comparator():
    pv = parse_value("<0.01")
    assert pv.comparator == "<"
    assert pv.value_num == 0.01
    assert pv.value_text == "<0.01"


def test_parse_greater_than():
    pv = parse_value(">200")
    assert pv.comparator == ">"
    assert pv.value_num == 200.0


def test_parse_qualitative():
    pv = parse_value("Negative")
    assert pv.value_num is None
    assert pv.value_text == "Negative"


def test_parse_with_flag():
    pv = parse_value("212 H")
    assert pv.value_num == 212.0
    assert pv.flag == "H"


def test_parse_thousands_separator():
    pv = parse_value("1,234")
    assert pv.value_num == 1234.0


def test_parse_range():
    pr = parse_reference_range("65-99")
    assert pr.low == 65.0
    assert pr.high == 99.0


def test_parse_range_upper_only():
    pr = parse_reference_range(">40")
    assert pr.low == 40.0
    assert pr.high is None
    pr2 = parse_reference_range("<200")
    assert pr2.high == 200.0
    assert pr2.low is None


def test_compute_flag_from_range():
    assert compute_flag(105, 65, 99, None) == "H"
    assert compute_flag(38, 40, None, None) == "L"
    assert compute_flag(80, 65, 99, None) is None


def test_compute_flag_prefers_explicit():
    assert compute_flag(80, 65, 99, "A") == "A"


def test_parse_date_formats():
    assert parse_date("01/15/2024") == "2024-01-15"
    assert parse_date("2024-01-15") == "2024-01-15"
    assert parse_date("January 15, 2024") == "2024-01-15"
    assert parse_date("15-Jan-2024") == "2024-01-15"
    assert parse_date("garbage") is None
