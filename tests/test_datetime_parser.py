from datetime import date, datetime, time

from app.services.datetime_parser import parse_datetime_parts, parse_period


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
    assert parse_datetime_parts("Тест 02 в пятницу", NOW).time_value is None


def test_relative_minutes_are_calculated():
    result = parse_datetime_parts("через 30 минут", NOW)

    assert result.date_value == date(2026, 10, 15)
    assert result.time_value == time(12, 30)


def test_invalid_calendar_date_is_reported():
    result = parse_datetime_parts("31.02.2026 в 12:00", NOW)

    assert result.invalid_date is True
    assert result.date_value is None


def test_next_day_period_is_one_calendar_day():
    result = parse_period("Что у меня на следующий день?", NOW)

    assert result.start == date(2026, 10, 16)
    assert result.end == date(2026, 10, 16)


def test_month_period_uses_the_requested_month():
    result = parse_period("Покажи события на декабрь", NOW)

    assert result.start == date(2026, 12, 1)
    assert result.end == date(2026, 12, 31)


def test_month_week_period_is_limited_to_seven_days():
    first = parse_period("Покажи первую неделю декабря", NOW)
    last = parse_period("Покажи последнюю неделю декабря", NOW)

    assert first.start == date(2026, 12, 1)
    assert first.end == date(2026, 12, 7)
    assert last.start == date(2026, 12, 29)
    assert last.end == date(2026, 12, 31)


def test_winter_period_crosses_year_boundary():
    result = parse_period("Что запланировано зимой", NOW)

    assert result.start == date(2026, 12, 1)
    assert result.end == date(2027, 2, 28)


def test_explicit_date_range_includes_both_boundaries():
    result = parse_period(
        "Что у меня с 29 октября по 31 октября 2026?", NOW
    )

    assert result.start == date(2026, 10, 29)
    assert result.end == date(2026, 10, 31)


def test_iso_date_is_parsed():
    result = parse_datetime_parts("2026-11-03 в 10:00", NOW)

    assert result.date_value == date(2026, 11, 3)
    assert result.time_value == time(10, 0)


def test_next_month_period_is_resolved():
    result = parse_period("Создай задачу в следующем месяце", NOW)

    assert result.start == date(2026, 11, 1)
    assert result.end == date(2026, 11, 30)


def test_next_month_creation_date_is_resolved():
    result = parse_datetime_parts("в следующем месяце", NOW)

    assert result.date_value == date(2026, 11, 1)
