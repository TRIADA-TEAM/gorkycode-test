import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

MONTHS = {
    "январ": 1,
    "феврал": 2,
    "март": 3,
    "апрел": 4,
    "май": 5,
    "мая": 5,
    "июн": 6,
    "июл": 7,
    "август": 8,
    "сентябр": 9,
    "октябр": 10,
    "ноябр": 11,
    "декабр": 12,
}

WEEKDAYS = {
    "понедельник": 0,
    "вторник": 1,
    "сред": 2,
    "четверг": 3,
    "пятниц": 4,
    "суббот": 5,
    "воскресень": 6,
}

NUMBER_WORDS = {
    "один": 1,
    "два": 2,
    "три": 3,
    "четыре": 4,
    "пять": 5,
    "шесть": 6,
    "семь": 7,
    "восемь": 8,
    "девять": 9,
    "десять": 10,
}


@dataclass(frozen=True)
class ParsedDateTime:
    date_value: date | None = None
    time_value: time | None = None
    has_explicit_date: bool = False
    has_explicit_time: bool = False
    invalid_date: bool = False


def parse_datetime_parts(value: str, now: datetime) -> ParsedDateTime:
    """Extract explicitly stated Russian date and time without inventing either."""
    # Keep punctuation: dots and colons distinguish dates from clock times.
    text = " ".join(value.lower().replace("ё", "е").split())
    date_value = None
    time_value = None
    invalid_date = False
    date_span: tuple[int, int] | None = None

    relative_hours = re.search(
        r"через\s+(\d+|" + "|".join(NUMBER_WORDS) + r")\s+час", text
    )
    if relative_hours:
        raw_amount = relative_hours.group(1)
        amount = int(raw_amount) if raw_amount.isdigit() else NUMBER_WORDS[raw_amount]
        moment = now + timedelta(hours=amount)
        return ParsedDateTime(moment.date(), moment.timetz().replace(tzinfo=None), True, True)

    numeric = re.search(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{2}|\d{4})\b", text)
    if numeric:
        year = int(numeric.group(3))
        if year < 100:
            year += 2000
        try:
            date_value = date(year, int(numeric.group(2)), int(numeric.group(1)))
        except ValueError:
            invalid_date = True
        date_span = numeric.span()
    else:
        month_date = re.search(
            r"\b(\d{1,2})\s+"
            r"(январ\w*|феврал\w*|март\w*|апрел\w*|ма[йя]|июн\w*|июл\w*|"
            r"август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*)"
            r"(?:\s+(\d{2}|\d{4})(?:\s+года?)?)?\b",
            text,
        )
        if month_date:
            month_word = month_date.group(2)
            month = next(number for stem, number in MONTHS.items() if month_word.startswith(stem))
            year = int(month_date.group(3) or now.year)
            if year < 100:
                year += 2000
            try:
                date_value = date(year, month, int(month_date.group(1)))
            except ValueError:
                invalid_date = True
            date_span = month_date.span()
        elif "послезавтра" in text:
            date_value = now.date() + timedelta(days=2)
        elif "завтра" in text:
            date_value = now.date() + timedelta(days=1)
        elif "сегодня" in text:
            date_value = now.date()
        elif "вчера" in text:
            date_value = now.date() - timedelta(days=1)
        else:
            for stem, weekday in WEEKDAYS.items():
                if stem in text:
                    days_ahead = (weekday - now.weekday()) % 7 or 7
                    date_value = now.date() + timedelta(days=days_ahead)
                    break

    # Remove the numeric/month date before looking for dotted time, otherwise
    # the beginning of "15.10.26" could be mistaken for 15:10.
    time_text = text
    if date_span:
        time_text = text[: date_span[0]] + " " + text[date_span[1] :]

    clock = re.search(r"(?<!\d)([01]?\d|2[0-3])[:.]([0-5]\d)(?!\d)", time_text)
    if clock:
        time_value = time(int(clock.group(1)), int(clock.group(2)))
    else:
        hour = re.search(
            r"(?:\bв\s+|^|\s)([01]?\d|2[0-3])(?:\s+час(?:а|ов)?)?\s*(утра|дня|вечера)?\b",
            time_text,
        )
        if hour:
            hour_value = int(hour.group(1))
            if hour.group(2) in {"дня", "вечера"} and hour_value < 12:
                hour_value += 12
            time_value = time(hour_value, 0)
        elif "полдень" in time_text:
            time_value = time(12, 0)
        elif "полноч" in time_text:
            time_value = time(0, 0)

    return ParsedDateTime(
        date_value=date_value,
        time_value=time_value,
        has_explicit_date=date_value is not None or invalid_date,
        has_explicit_time=time_value is not None,
        invalid_date=invalid_date,
    )


def combine(date_value: date, time_value: time, now: datetime) -> datetime:
    return datetime.combine(date_value, time_value, tzinfo=now.tzinfo)
