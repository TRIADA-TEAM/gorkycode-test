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
