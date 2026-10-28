from datetime import datetime

import pytest

from app.domain.commands import Action, AssistantCommand, EventType
from app.services.commands import normalize_command


NOW = datetime.fromisoformat("2026-10-28T18:49:00+03:00")


@pytest.mark.parametrize(
    ("phrase", "expected_query", "direction"),
    [
        ("Найди встречу в кино с Марусей", "кино марусей", None),
        ("Найти задачу про подарки", "подарки", None),
        ("Поищи напоминание оплатить интернет", "оплатить интернет", None),
        ("Поиск встречи с дизайнером", "дизайнером", None),
        ("Когда было кино", "кино", "past"),
        ("Когда была встреча с Иваном", "иваном", "past"),
        ("Когда будет созвон с командой", "созвон командой", "future"),
        ("Когда будут подарки", "подарки", "future"),
    ],
)
def test_search_phrase_matrix_overrides_wrong_provider_action(
    phrase, expected_query, direction
):
    provider_command = AssistantCommand(
        action=Action.CREATE_MEETING,
        transcript=phrase,
        title="Ошибочно созданная встреча",
        event_type=EventType.MEETING,
        starts_at=datetime.fromisoformat("2026-10-30T19:00:00+03:00"),
    )

    result = normalize_command(provider_command, NOW)

    assert result.action == Action.SEARCH_EVENTS
    assert result.query == expected_query
    assert result.starts_at is None
    if direction == "past":
        assert result.date_from is None
        assert result.date_to == NOW
    elif direction == "future":
        assert result.date_from == NOW
        assert result.date_to is None


@pytest.mark.parametrize(
    "phrase",
    [
        "Какие встречи были вчера?",
        "Что у меня на следующей неделе?",
        "Покажи события в ноябре",
        "Какие задачи запланированы на сегодня?",
    ],
)
def test_list_phrases_remain_lists(phrase):
    command = AssistantCommand(
        action=Action.SEARCH_EVENTS,
        transcript=phrase,
        query="ошибочный запрос",
    )

    result = normalize_command(command, NOW)

    assert result.action == Action.LIST_EVENTS


@pytest.mark.parametrize(
    ("phrase", "expected_date"),
    [
        ("Найди встречу 20 октября", "2026-10-20"),
        ("Поищи задачу на 02.11.2026", "2026-11-02"),
        ("Когда была встреча 15 октября", "2026-10-15"),
    ],
)
def test_exact_search_date_is_not_expanded_to_whole_month(phrase, expected_date):
    command = AssistantCommand(
        action=Action.SEARCH_EVENTS,
        transcript=phrase,
        query="встреча",
    )

    result = normalize_command(command, NOW)

    assert result.date_from.date().isoformat() == expected_date
    assert result.date_to.date().isoformat() == expected_date
    assert result.date_from.time().isoformat() == "00:00:00"
    assert result.date_to.time().isoformat() == "23:59:59"


@pytest.mark.parametrize(
    ("phrase", "expected_from", "expected_to"),
    [
        ("Покажи запланированные события", NOW, None),
        ("Какие встречи предстоят?", NOW, None),
        ("Что у меня запланировано?", NOW, None),
        ("Что было запланировано?", None, NOW),
        ("Покажи события", None, None),
    ],
)
def test_list_temporal_direction_matrix(phrase, expected_from, expected_to):
    command = AssistantCommand(
        action=Action.LIST_EVENTS,
        transcript=phrase,
    )

    result = normalize_command(command, NOW)

    assert result.action == Action.LIST_EVENTS
    assert result.date_from == expected_from
    assert result.date_to == expected_to


def test_explicit_day_has_priority_over_planned_future_direction():
    command = AssistantCommand(
        action=Action.LIST_EVENTS,
        transcript="Что было запланировано сегодня?",
    )

    result = normalize_command(command, NOW)

    assert result.date_from == datetime.fromisoformat("2026-10-28T00:00:00+03:00")
    assert result.date_to == datetime.fromisoformat("2026-10-28T23:59:59+03:00")


@pytest.mark.parametrize(
    "phrase",
    [
        "Покажи все прошедшие встречи",
        "Выведи прошлые встречи",
        "Перечисли предыдущие встречи",
        "Покажи минувшие встречи",
        "Какие встречи уже состоялись?",
        "Покажи завершившиеся встречи",
    ],
)
def test_past_list_keyword_matrix(phrase):
    command = AssistantCommand(action=Action.CREATE_MEETING, transcript=phrase)

    result = normalize_command(command, NOW)

    assert result.action == Action.LIST_EVENTS
    assert result.event_type == EventType.MEETING
    assert result.date_from is None
    assert result.date_to == NOW


@pytest.mark.parametrize(
    "phrase",
    [
        "Покажи будущие встречи",
        "Выведи предстоящие встречи",
        "Перечисли ближайшие встречи",
        "Покажи грядущие встречи",
    ],
)
def test_future_list_keyword_matrix(phrase):
    command = AssistantCommand(action=Action.CLARIFY, transcript=phrase,
                               clarification_question="Уточните запрос")

    result = normalize_command(command, NOW)

    assert result.action == Action.LIST_EVENTS
    assert result.event_type == EventType.MEETING
    assert result.date_from == NOW
    assert result.date_to is None


def test_temporal_words_are_removed_from_search_subject():
    command = AssistantCommand(
        action=Action.SEARCH_EVENTS,
        transcript="Найди все прошлые встречи с Иваном",
        query="ошибка модели",
    )

    result = normalize_command(command, NOW)

    assert result.query == "иваном"
    assert result.date_to == NOW


@pytest.mark.parametrize(
    ("phrase", "expected_types"),
    [
        ("Какие задачи и встречи у меня на завтра?", {"task", "meeting"}),
        ("Покажи задачи и напоминания", {"task", "reminder"}),
        (
            "Покажи задачи, встречи и напоминания",
            {"task", "meeting", "reminder"},
        ),
    ],
)
def test_mixed_event_type_matrix(phrase, expected_types):
    command = AssistantCommand(action=Action.LIST_EVENTS, transcript=phrase)

    result = normalize_command(command, NOW)

    assert result.action == Action.LIST_EVENTS
    assert result.event_type is None
    assert {item.value for item in result.event_types} == expected_types
