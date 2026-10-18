from datetime import date, datetime, time

from app.services.datetime_parser import parse_datetime_parts


NOW = datetime.fromisoformat("2026-10-15T12:00:00+03:00")


def test_numeric_date_and_time_are_parsed_separately():
    result = parse_datetime_parts("На 15.10.26 16:00", NOW)

    assert result.date_value == date(2026, 10, 15)
    assert result.time_value == time(16, 0)


def test_russian_month_and_time_are_parsed():
    result = parse_datetime_parts("На 20 октября в 12:30", NOW)

    assert result.date_value == date(2026, 10, 20)
    assert result.time_value == time(12, 30)


def test_time_only_does_not_invent_date():
    result = parse_datetime_parts("12:00", NOW)

    assert result.date_value is None
    assert result.time_value == time(12, 0)


def test_time_words_are_parsed_in_clarification_replies():
    assert parse_datetime_parts("12 утра", NOW).time_value == time(12, 0)
    assert parse_datetime_parts("6 вечера", NOW).time_value == time(18, 0)
    assert parse_datetime_parts("12 часов", NOW).time_value == time(12, 0)


def test_invalid_calendar_date_is_reported():
    result = parse_datetime_parts("31.02.2026 в 12:00", NOW)

    assert result.invalid_date is True
    assert result.date_value is None
