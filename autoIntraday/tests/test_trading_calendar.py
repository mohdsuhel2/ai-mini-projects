from datetime import datetime

from trading_calendar import IST, is_trading_time, load_holidays


def _ist(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=IST)


HOLIDAYS = {"2026-07-13"}   # a Monday, treated as a holiday for tests


def test_trading_weekday_in_window():
    # 2026-07-10 is a Friday
    assert is_trading_time(_ist(2026, 7, 10, 11, 0), set()) is True
    assert is_trading_time(_ist(2026, 7, 10, 9, 15), set()) is True    # open edge
    assert is_trading_time(_ist(2026, 7, 10, 15, 30), set()) is True   # close edge


def test_weekend_is_not_trading():
    # 2026-07-11 Saturday, 2026-07-12 Sunday
    assert is_trading_time(_ist(2026, 7, 11, 11, 0), set()) is False
    assert is_trading_time(_ist(2026, 7, 12, 11, 0), set()) is False


def test_holiday_is_not_trading():
    assert is_trading_time(_ist(2026, 7, 13, 11, 0), HOLIDAYS) is False   # Monday holiday
    assert is_trading_time(_ist(2026, 7, 13, 11, 0), set()) is True       # same day, no holiday


def test_outside_window_is_not_trading():
    assert is_trading_time(_ist(2026, 7, 10, 9, 0), set()) is False    # before open
    assert is_trading_time(_ist(2026, 7, 10, 15, 45), set()) is False  # after close


def test_load_holidays_parses_and_ignores_comments(tmp_path):
    p = tmp_path / "h.txt"
    p.write_text("# NSE holidays\n2026-01-26\n\n2026-10-02  \n# trailing comment\n")
    assert load_holidays(str(p)) == {"2026-01-26", "2026-10-02"}


def test_load_holidays_missing_file_is_empty():
    assert load_holidays("/no/such/file.txt") == set()
