from datetime import date, datetime

from trading_calendar import IST, add_trading_days, is_trading_time, load_holidays


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


# ---- add_trading_days: calendar math for the swing ETA column ---------------------------


def test_add_weekdays_advance_simply():
    # Mon 2026-08-10 + 2 td = Wed 2026-08-12
    assert add_trading_days(date(2026, 8, 10), 2, set()) == date(2026, 8, 12)


def test_add_weekend_is_skipped():
    # Fri 2026-08-07 + 1 td = Mon 2026-08-10
    assert add_trading_days(date(2026, 8, 7), 1, set()) == date(2026, 8, 10)


def test_add_holiday_is_skipped():
    # Fri + 1 td, but Monday is a holiday -> Tuesday
    assert add_trading_days(date(2026, 8, 7), 1, {"2026-08-10"}) == date(2026, 8, 11)


def test_add_zero_and_negative_return_start():
    assert add_trading_days(date(2026, 8, 7), 0, set()) == date(2026, 8, 7)
    assert add_trading_days(date(2026, 8, 7), -3, set()) == date(2026, 8, 7)


def test_add_spans_multiple_weeks():
    # Fri 2026-08-07 + 10 td = Fri 2026-08-21
    assert add_trading_days(date(2026, 8, 7), 10, set()) == date(2026, 8, 21)
