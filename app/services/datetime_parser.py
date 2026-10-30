import calendar
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
HOUR_WORDS = {**NUMBER_WORDS, "одному": 1, "двум": 2, "трем": 3, "десяти": 10}


@dataclass(frozen=True)
class ParsedDateTime:
    date_value: date | None = None
    time_value: time | None = None
    has_explicit_date: bool = False
    has_explicit_time: bool = False
    invalid_date: bool = False
    invalid_time: bool = False


@dataclass(frozen=True)
class ParsedPeriod:
    start: date | None = None
    end: date | None = None


def parse_datetime_parts(value: str, now: datetime) -> ParsedDateTime:
    """Extract explicitly stated Russian date and time without inventing either."""
    # Keep punctuation: dots and colons distinguish dates from clock times.
    text = " ".join(value.lower().replace("ё", "е").split())
    date_value = None
    time_value = None
    invalid_date = False
    invalid_time = False
    date_span: tuple[int, int] | None = None

    relative_hours = re.search(
        r"(?:через|спустя)\s+(\d+|" + "|".join(NUMBER_WORDS) + r")\s+час", text
    )
    if relative_hours:
        raw_amount = relative_hours.group(1)
        amount = int(raw_amount) if raw_amount.isdigit() else NUMBER_WORDS[raw_amount]
        moment = now + timedelta(hours=amount)
        return ParsedDateTime(moment.date(), moment.timetz().replace(tzinfo=None), True, True)

    relative_minutes = re.search(
        r"(?:через|спустя)\s+(\d+|" + "|".join(NUMBER_WORDS) + r")\s+минут", text
    )
    if relative_minutes:
        raw_amount = relative_minutes.group(1)
        amount = int(raw_amount) if raw_amount.isdigit() else NUMBER_WORDS[raw_amount]
        moment = now + timedelta(minutes=amount)
        return ParsedDateTime(moment.date(), moment.timetz().replace(tzinfo=None), True, True)

    if re.search(r"(?:через|спустя)\s+минут[уы]?\b", text):
        moment = now + timedelta(minutes=1)
        return ParsedDateTime(moment.date(), moment.timetz().replace(tzinfo=None), True, True)

    if "до конца недели" in text:
        target = now.date() + timedelta(days=6 - now.weekday())
        return ParsedDateTime(target, None, True, False)

    if "в следующем месяце" in text or "на следующий месяц" in text:
        year = now.year + (1 if now.month == 12 else 0)
        month = 1 if now.month == 12 else now.month + 1
        return ParsedDateTime(date(year, month, 1), None, True, False)

    iso_numeric = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", text)
    if iso_numeric:
        try:
            date_value = date(
                int(iso_numeric.group(1)),
                int(iso_numeric.group(2)),
                int(iso_numeric.group(3)),
            )
        except ValueError:
            invalid_date = True
        date_span = iso_numeric.span()
    numeric = re.search(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{2}|\d{4})\b", text)
    if not iso_numeric and numeric:
        year = int(numeric.group(3))
        if year < 100:
            year += 2000
        try:
            date_value = date(year, int(numeric.group(2)), int(numeric.group(1)))
        except ValueError:
            invalid_date = True
        date_span = numeric.span()
    elif not iso_numeric:
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
        invalid_time = bool(re.search(r"\b\d{1,2}:\d{2}\b", time_text))
        hour = re.search(
            r"\bв\s+([01]?\d|2[0-3])(?:\s+час(?:а|ов)?)?\s*(утра|дня|вечера)?\b",
            time_text,
        )
        if not hour:
            hour = re.fullmatch(
                r"\s*([01]?\d|2[0-3])\s*(час(?:а|ов)?|утра|дня|вечера)\s*",
                time_text,
            )
        if hour:
            hour_value = int(hour.group(1))
            meridiem = hour.group(2) if hour.lastindex and hour.lastindex >= 2 else None
            if meridiem in {"дня", "вечера"} and hour_value < 12:
                hour_value += 12
            time_value = time(hour_value, 0)
        else:
            word_hour = re.search(
                r"\b(?:в|к)\s+(" + "|".join(HOUR_WORDS) + r")\s*(утра|дня|вечера)?\b",
                time_text,
            )
            if word_hour:
                hour_value = HOUR_WORDS[word_hour.group(1)]
                if word_hour.group(2) in {"дня", "вечера"} and hour_value < 12:
                    hour_value += 12
                time_value = time(hour_value, 0)
        if time_value is None and "полдень" in time_text:
            time_value = time(12, 0)
        elif time_value is None and "полноч" in time_text:
            time_value = time(0, 0)

    return ParsedDateTime(
        date_value=date_value,
        time_value=time_value,
        has_explicit_date=date_value is not None or invalid_date,
        has_explicit_time=time_value is not None,
        invalid_date=invalid_date,
        invalid_time=invalid_time,
    )


def parse_period(value: str, now: datetime) -> ParsedPeriod:
    """Resolve a list/search period without relying on provider date ranges."""
    text = " ".join(value.lower().replace("ё", "е").split())
    today = now.date()

    if re.search(r"с\s+завтрашн\w*\s+дн\w*\s+по\s+послезавтра", text):
        return ParsedPeriod(today + timedelta(days=1), today + timedelta(days=2))

    explicit_range = _parse_explicit_range(text, now)
    if explicit_range.start is not None:
        return explicit_range

    if "на следующий день" in text or "следующий день" in text:
        target = today + timedelta(days=1)
        return ParsedPeriod(target, target)
    if "сегодня" in text:
        return ParsedPeriod(today, today)
    if "вчера" in text:
        target = today - timedelta(days=1)
        return ParsedPeriod(target, target)
    if "послезавтра" in text:
        target = today + timedelta(days=2)
        return ParsedPeriod(target, target)
    if "завтра" in text:
        target = today + timedelta(days=1)
        return ParsedPeriod(target, target)

    if any(marker in text for marker in (
        "на следующей неделе", "следующую неделю", "будущую неделю", "будущей неделе",
    )):
        start = today + timedelta(days=7 - now.weekday())
        return ParsedPeriod(start, start + timedelta(days=6))
    if "в следующем месяце" in text or "на следующий месяц" in text:
        year = now.year + (1 if now.month == 12 else 0)
        month = 1 if now.month == 12 else now.month + 1
        start = date(year, month, 1)
        end = date(year, month, calendar.monthrange(year, month)[1])
        return ParsedPeriod(start, end)
    if any(marker in text for marker in (
        "на этой неделе", "эту неделю", "до конца этой недели", "до конца недели",
    )):
        start = today - timedelta(days=now.weekday())
        return ParsedPeriod(start, start + timedelta(days=6))

    month = _month_in_text(text)
    if month:
        year = now.year if month >= now.month else now.year + 1
        first = date(year, month, 1)
        last = date(year, month, calendar.monthrange(year, month)[1])
        week = re.search(r"(?:перв|втор|треть|четверт|последн)\w*\s+недел", text)
        if week:
            ordinal = week.group(0)
            if ordinal.startswith("перв"):
                number = 1
            elif ordinal.startswith("втор"):
                number = 2
            elif ordinal.startswith("треть"):
                number = 3
            elif ordinal.startswith("четверт"):
                number = 4
            else:
                number = (last.day - 1) // 7 + 1
            start_day = 1 + (number - 1) * 7
            if start_day > last.day:
                return ParsedPeriod(first, last)
            return ParsedPeriod(
                date(year, month, start_day),
                min(date(year, month, start_day) + timedelta(days=6), last),
            )
        return ParsedPeriod(first, last)

    seasons = {
        "зим": (12, 1, 2),
        "весн": (3, 4, 5),
        "лет": (6, 7, 8),
        "осен": (9, 10, 11),
    }
    words = text.split()
    for stem, months in seasons.items():
        if not any(word.startswith(stem) for word in words):
            continue
        if months[0] == 12:
            year = now.year if now.month == 12 else now.year if now.month > 12 else now.year
            if now.month <= 2:
                year = now.year - 1
            elif now.month >= 3:
                year = now.year
            start = date(year, 12, 1)
            end = date(year + 1, 2, calendar.monthrange(year + 1, 2)[1])
        else:
            start_year = now.year if now.month <= months[-1] else now.year + 1
            start = date(start_year, months[0], 1)
            end = date(start_year, months[-1], calendar.monthrange(start_year, months[-1])[1])
        return ParsedPeriod(start, end)
    return ParsedPeriod()


def _month_in_text(text: str) -> int | None:
    for stem, month in MONTHS.items():
        if re.search(rf"\b{stem}\w*\b", text):
            return month
    return None


def _parse_explicit_range(text: str, now: datetime) -> ParsedPeriod:
    month_pattern = (
        r"январ\w*|феврал\w*|март\w*|апрел\w*|ма[йя]|июн\w*|июл\w*|"
        r"август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*"
    )
    month_range = re.search(
        rf"с\s+(\d{{1,2}})\s+({month_pattern})\s+по\s+"
        rf"(\d{{1,2}})\s+({month_pattern})(?:\s+(\d{{4}}))?",
        text,
    )
    numeric_range = re.search(
        r"с\s+(\d{1,2})[./-](\d{1,2})\s+по\s+"
        r"(\d{1,2})[./-](\d{1,2})(?:[./-](\d{4}))?",
        text,
    )
    try:
        if month_range:
            start_month = next(
                number
                for stem, number in MONTHS.items()
                if month_range.group(2).startswith(stem)
            )
            end_month = next(
                number
                for stem, number in MONTHS.items()
                if month_range.group(4).startswith(stem)
            )
            year = int(month_range.group(5) or now.year)
            return ParsedPeriod(
                date(year, start_month, int(month_range.group(1))),
                date(year, end_month, int(month_range.group(3))),
            )
        if numeric_range:
            year = int(numeric_range.group(5) or now.year)
            return ParsedPeriod(
                date(year, int(numeric_range.group(2)), int(numeric_range.group(1))),
                date(year, int(numeric_range.group(4)), int(numeric_range.group(3))),
            )
    except ValueError:
        return ParsedPeriod()
    return ParsedPeriod()


def combine(date_value: date, time_value: time, now: datetime) -> datetime:
    return datetime.combine(date_value, time_value, tzinfo=now.tzinfo)
