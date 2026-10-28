import re
from datetime import datetime, time, timedelta

from app.domain.commands import Action, AssistantCommand, EventStatus, EventType
from app.services.datetime_parser import ParsedPeriod, combine, parse_datetime_parts, parse_period
from app.services.text import extract_search_query, normalize_text


WEEKDAY_ALIASES = {
    "понедельник": 0, "понедельника": 0,
    "вторник": 1, "вторника": 1,
    "среда": 2, "среду": 2, "среды": 2,
    "четверг": 3, "четверга": 3,
    "пятница": 4, "пятницу": 4, "пятницы": 4,
    "суббота": 5, "субботу": 5, "субботы": 5,
    "воскресенье": 6, "воскресенья": 6,
}
def normalize_command(
    command: AssistantCommand,
    now: datetime | None = None,
    source_text: str | None = None,
) -> AssistantCommand:
    """Map occasionally inconsistent model fields to application semantics."""
    transcript_text = normalize_text(source_text or command.transcript)
    if (
        command.action in {Action.CREATE_TASK, Action.CREATE_MEETING}
        and " и " in f" {transcript_text} "
        and "встреч" in transcript_text
        and ("задач" in transcript_text or "напомин" in transcript_text)
    ):
        return command.model_copy(
            update={
                "action": Action.CLARIFY,
                "missing_fields": [],
                "clarification_question": "Я обрабатываю одну команду за раз. Уточните, какое действие выполнить первым.",
            }
        )
    if _is_search_request(transcript_text):
        command = command.model_copy(update={"action": Action.SEARCH_EVENTS})
    if _is_list_request(transcript_text):
        command = command.model_copy(update={"action": Action.LIST_EVENTS})
    elif (
        command.action in {Action.LIST_EVENTS, Action.SEARCH_EVENTS}
        and not _has_event_query_intent(transcript_text)
        and not _is_search_request(transcript_text)
    ):
        return command.model_copy(
            update={
                "action": Action.CLARIFY,
                "event_type": None,
                "missing_fields": [],
                "clarification_question": (
                    "Я могу создавать задачи, напоминания и встречи, а также показывать "
                    "или искать события."
                ),
            }
        )
    updates: dict = {}

    source = source_text or command.transcript
    explicit_type = detect_event_type(source)
    if command.action in {Action.CREATE_TASK, Action.CREATE_MEETING} or (
        command.action == Action.CLARIFY and explicit_type is not None
    ):
        event_type = explicit_type or command.event_type
        if event_type is None:
            event_type = (
                EventType.MEETING
                if command.action == Action.CREATE_MEETING
                else EventType.TASK
            )
        action = (
            Action.CREATE_MEETING
            if event_type == EventType.MEETING
            else Action.CREATE_TASK
        )
        updates.update({"action": action, "event_type": event_type})
        title = (command.title or "").strip()
        explicit_missing_title = any(
            marker in normalize_text(source)
            for marker in ("без названия", "без содержания", "без описания")
        )
        has_title = (
            bool(title)
            and title != DEFAULT_TITLES[event_type]
            and not explicit_missing_title
        )
        updates["title"] = title or DEFAULT_TITLES[event_type]

        if now:
            parsed = parse_datetime_parts(source, now)
            if action == Action.CREATE_MEETING:
                date_value = parsed.date_value
                time_value = parsed.time_value
                starts_at = (
                    combine(date_value, time_value, now)
                    if date_value and time_value
                    else combine(date_value, time(9), now)
                    if date_value
                    else combine(now.date(), time_value, now)
                    if time_value
                    else None
                )
                updates.update(
                    {
                        "starts_at": starts_at,
                        "ends_at": None,
                        "due_at": None,
                        "due_date": None,
                    }
                )
                missing = []
                if not has_title:
                    missing.append("title")
                if not date_value:
                    missing.append("start_date")
                if not time_value:
                    missing.append("start_time")
            else:
                date_value = parsed.date_value
                time_value = parsed.time_value
                due_at = (
                    combine(date_value, time_value, now)
                    if date_value and time_value
                    else combine(now.date(), time_value, now)
                    if time_value
                    else None
                )
                updates.update(
                    {
                        "due_date": date_value,
                        "due_at": due_at,
                        "starts_at": None,
                        "ends_at": None,
                    }
                )
                missing = []
                if not has_title:
                    missing.append("title")
                if not date_value:
                    missing.append("due_date")
                if event_type == EventType.REMINDER and not time_value:
                    missing.append("due_time")

            if parsed.invalid_date:
                missing = [field for field in missing if not field.endswith("_date")]
                missing.insert(0, "invalid_date")
            elif parsed.invalid_time:
                missing.insert(0, "invalid_time")
            elif date_value and date_value < now.date():
                # A date that has already passed is invalid regardless of whether
                # another required field (for example meeting time) is missing.
                missing = ["past_date"]
            elif not missing and _is_past(action, event_type, updates, now):
                missing = ["future_datetime"]
            updates["missing_fields"] = missing
            updates["clarification_question"] = clarification_question(event_type, missing)
        else:
            # Keep already validated model values when no application clock is supplied.
            if action == Action.CREATE_TASK and command.due_at is None and command.starts_at:
                updates.update({"due_at": command.starts_at, "starts_at": None, "ends_at": None})

    if command.action in {Action.LIST_EVENTS, Action.SEARCH_EVENTS}:
        text = normalize_text(source_text or command.transcript)
        requested_types = []
        if "задач" in text and "без встреч" in text:
            requested_types.append(EventType.TASK)
        elif "встреч" in text and "без задач" in text:
            requested_types.append(EventType.MEETING)
        elif "встреч" in text:
            requested_types.append(EventType.MEETING)
        if "задач" in text or any(word.startswith("дел") for word in text.split()):
            requested_types.append(EventType.TASK)
        if "напомин" in text:
            requested_types.append(EventType.REMINDER)
        if len(requested_types) == 1:
            updates["event_type"] = requested_types[0]
            updates["event_types"] = []
        elif len(requested_types) > 1:
            updates["event_type"] = None
            updates["event_types"] = requested_types
        else:
            updates["event_type"] = None
            updates["event_types"] = []
        if "выполнен" in text or "сделан" in text:
            updates["statuses"] = [EventStatus.COMPLETED]
        elif "невыполн" in text or "незаверш" in text:
            updates["statuses"] = [EventStatus.PENDING]
        updates["starts_at"] = None
        updates["ends_at"] = None
        updates["due_at"] = None

        if now:
            relative_period = _relative_period(source_text or command.transcript, now)
            if relative_period["date_from"] is not None:
                updates.update(relative_period)
            elif direction := _temporal_direction(text):
                if direction == "past":
                    updates.update({"date_from": None, "date_to": now})
                else:
                    updates.update({"date_from": now, "date_to": None})
            elif not parse_datetime_parts(source_text or command.transcript, now).has_explicit_date:
                # A generic list/search request means all available events. Do not
                # trust a date range invented by the provider.
                updates.update({"date_from": None, "date_to": None})

        if command.action == Action.SEARCH_EVENTS:
            updates["query"] = extract_search_query(source_text or command.transcript)

    return command.model_copy(update=updates)


DEFAULT_TITLES = {
    EventType.TASK: "Задача",
    EventType.REMINDER: "Напоминание",
    EventType.MEETING: "Встреча",
}

TYPE_MARKERS = {
    EventType.REMINDER: (
        "напомни", "напомнить", "напоминан", "не дай забыть", "не забыть",
    ),
    EventType.MEETING: (
        "встреч", "созвон", "совещан", "переговор", "назначь встреч",
    ),
    EventType.TASK: (
        "задач", "нужно сделать", "надо сделать", "добавь в задачи",
    ),
}


def detect_event_type(value: str) -> EventType | None:
    text = normalize_text(value)
    for event_type in (EventType.REMINDER, EventType.MEETING, EventType.TASK):
        if any(marker in text for marker in TYPE_MARKERS[event_type]):
            return event_type
    return None


def _is_list_request(text: str) -> bool:
    list_markers = (
        "какие запланированы",
        "было запланировано",
        "были запланированы",
        "какие события",
        "что запланировано",
        "что у меня",
        "покажи события",
        "покажи задачи",
        "покажи встречи",
        "есть ли события",
        "есть что-нибудь", "есть что нибудь", "есть ли что",
        "мои планы",
    )
    plural_list = bool(
        re.search(
            r"\b(?:покажи|показать|выведи|вывести|перечисли)\b.*"
            r"\b(?:события|задачи|встречи|напоминания|планы)\b",
            text,
        )
    )
    return (
        any(marker in text for marker in list_markers)
        or text.startswith("какие ")
        or plural_list
    )


def _has_event_query_intent(text: str) -> bool:
    if _is_list_request(text):
        return True
    event_words = ("событ", "задач", "дело", "дела", "напомин", "встреч", "план")
    query_words = ("найди", "найти", "поиск", "покажи", "когда", "сколько", "есть ли", "что у меня")
    return any(word in text for word in event_words) and any(
        word in text for word in query_words
    )


def _is_search_request(text: str) -> bool:
    explicit_search = text.startswith(("найди ", "найти ", "поищи ", "поиск "))
    natural_event_search = (
        ("когда" in text or "во сколько" in text)
        and any(word in text for word in ("встреч", "созвон", "совещан", "задач", "напомин"))
    )
    temporal_search = bool(
        re.search(
            r"\b(?:когда|во сколько)(?:\s+у\s+меня)?\s+"
            r"(?:был[аои]?|будет|будут|прошл[аои]?|предстоит|у меня)\b",
            text,
        )
    )
    return explicit_search or natural_event_search or temporal_search


def _temporal_direction(text: str) -> str | None:
    if re.search(
        r"\b(?:был|была|было|были|прошл\w*|прошедш\w*|предыдущ\w*|"
        r"минувш\w*|состоявш\w*|состоял\w*|завершивш\w*)\b",
        text,
    ):
        return "past"
    if re.search(
        r"\b(?:будет|будут|будущ\w*|предстоит|предстоят|предстоящ\w*|"
        r"грядущ\w*|ближайш\w*|запланирован\w*)\b",
        text,
    ):
        return "future"
    return None


def clarification_question(event_type: EventType, missing: list[str]) -> str | None:
    if not missing:
        return None
    if "invalid_date" in missing:
        return "Не удалось распознать дату. Укажите её, например: «15 октября» или «15.10.2026»."
    if "invalid_time" in missing:
        return "Время указано некорректно. Используйте формат вроде «15:30»."
    if "past_date" in missing:
        return "Указанная дата уже прошла. Укажите будущую дату."
    if "future_datetime" in missing:
        return "Указанные дата или время уже прошли. Укажите будущую дату или время."
    if "title" in missing:
        if event_type == EventType.MEETING:
            return "С кем или о чем будет встреча?"
        if event_type == EventType.REMINDER:
            return "Что нужно напомнить?"
        return "Что нужно сделать?"
    date_field = "start_date" if event_type == EventType.MEETING else "due_date"
    time_field = "start_time" if event_type == EventType.MEETING else "due_time"
    label = {
        EventType.TASK: "задачу",
        EventType.REMINDER: "напоминание",
        EventType.MEETING: "встречу",
    }[event_type]
    if date_field in missing and time_field in missing:
        return f"На какую дату и время поставить {label}?"
    if date_field in missing:
        return f"На какую дату поставить {label}?"
    if time_field in missing:
        return f"На какое время поставить {label}?"
    return "Уточните, пожалуйста, дату и время."


def _is_past(
    action: Action, event_type: EventType, values: dict, now: datetime
) -> bool:
    if action == Action.CREATE_MEETING:
        return bool(values.get("starts_at") and values["starts_at"] <= now)
    due_at = values.get("due_at")
    if due_at:
        return due_at <= now
    due_date = values.get("due_date")
    return bool(due_date and due_date < now.date())


def _relative_period(text_value: str, now: datetime) -> dict:
    text = normalize_text(text_value)
    time_range = re.search(
        r"(?:между\s+|с\s+)(\d{1,2})(?::(\d{2}))?\s+(?:и|до)\s+"
        r"(\d{1,2})(?::(\d{2}))?",
        text,
    )
    after = re.search(r"(?:после|позже)\s+(\d{1,2})(?::(\d{2}))?", text)
    before = re.search(r"(?:до|раньше)\s+(\d{1,2})(?::(\d{2}))?", text)
    if time_range or after or before or "после обеда" in text:
        exact_for_day = parse_datetime_parts(text_value, now)
        day = exact_for_day.date_value or now.date()
        if time_range:
            start = time(int(time_range.group(1)), int(time_range.group(2) or 0))
            end = time(int(time_range.group(3)), int(time_range.group(4) or 0))
        elif after:
            start = time(int(after.group(1)), int(after.group(2) or 0))
            end = time(23, 59, 59)
        elif before:
            start = time.min
            end = time(int(before.group(1)), int(before.group(2) or 0))
        else:
            start, end = time(14), time(23, 59, 59)
        return {
            "date_from": datetime.combine(day, start, tzinfo=now.tzinfo),
            "date_to": datetime.combine(day, end, tzinfo=now.tzinfo),
        }
    exact = parse_datetime_parts(text_value, now)
    if (
        exact.date_value
        and not exact.invalid_date
        and not re.search(r"\bс\b.*\bпо\b", normalize_text(text_value))
    ):
        tz = now.tzinfo
        return {
            "date_from": _period_bound(exact.date_value, text_value, now, lower=True),
            "date_to": _period_bound(exact.date_value, text_value, now, lower=False),
        }
    parsed = parse_period(text_value, now)
    if parsed.start is None:
        text = normalize_text(text_value)
        for name, weekday in WEEKDAY_ALIASES.items():
            if name in text:
                start = now.date() + timedelta(days=(weekday - now.weekday()) % 7 or 7)
                parsed = ParsedPeriod(start, start)
                break
    if parsed.start is None or parsed.end is None:
        return {"date_from": None, "date_to": None}
    tz = now.tzinfo
    return {
        "date_from": _period_bound(parsed.start, text_value, now, lower=True),
        "date_to": _period_bound(parsed.end, text_value, now, lower=False),
    }


def _period_bound(day, text_value: str, now: datetime, *, lower: bool) -> datetime:
    text = normalize_text(text_value)
    ranges = {
        "утр": (time(6), time(12)),
        "днем": (time(12), time(18)),
        "вечер": (time(18), time(23, 59, 59)),
        "ночью": (time.min, time(6)),
    }
    words = text.split()
    selected = next(
        (value for marker, value in ranges.items() if any(word.startswith(marker) for word in words)),
        None,
    )
    if not selected:
        return datetime.combine(day, time.min if lower else time(23, 59, 59), tzinfo=now.tzinfo)
    return datetime.combine(day, selected[0 if lower else 1], tzinfo=now.tzinfo)
