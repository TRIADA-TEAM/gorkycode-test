from datetime import datetime
from uuid import uuid4

from app.domain.commands import Action, AssistantCommand, EventType
from app.services.events import DialogueService, EventService


def command(**changes) -> AssistantCommand:
    values = {
        "action": Action.CREATE_TASK,
        "transcript": "Напомни подготовить договор",
        "title": "Подготовить договор",
        "event_type": EventType.TASK,
        "due_at": datetime.fromisoformat("2026-10-11T10:00:00+03:00"),
    }
    values.update(changes)
    return AssistantCommand(**values)


def test_create_and_list_task(db):
    session_id = str(uuid4())
    service = EventService(db)
    created = service.create_task(session_id, command(), "user_text")

    result = service.list_events(session_id)

    assert created.id
    assert [item.title for item in result] == ["Подготовить договор"]


def test_date_only_task_is_listed_for_its_date(db):
    session_id = str(uuid4())
    service = EventService(db)
    item = service.create_task(
        session_id,
        command(due_at=None, due_date=datetime.fromisoformat("2026-10-15T00:00:00+03:00").date()),
        "user_text",
    )

    result = service.list_events(
        session_id,
        datetime.fromisoformat("2026-10-15T00:00:00+03:00"),
        datetime.fromisoformat("2026-10-15T23:59:59+03:00"),
        EventType.TASK,
    )

    assert item.due_at is None
    assert item.due_date == "2026-10-15"
    assert [event.title for event in result] == ["Подготовить договор"]


def test_sessions_are_isolated_and_seed_is_not_user_visible(db):
    first, second = str(uuid4()), str(uuid4())
    service = EventService(db)
    service.create_task(first, command(title="Личная задача"), "user_text")
    seed = service._make_event(None, "seed", "task", "Общая задача", "Общая задача")
    service._save(seed)

    assert service.list_events(second) == []
    assert {item.title for item in service.list_events(first)} == {"Личная задача"}


def test_search_by_partial_text(db):
    session_id = str(uuid4())
    service = EventService(db)
    service.create_task(session_id, command(title="Подготовить презентацию по бюджету"), "user_text")

    result = service.search_events(session_id, "презентация бюджет")

    assert len(result) == 1
    assert result[0].title == "Подготовить презентацию по бюджету"


def test_search_uses_natural_language_subject_and_word_forms(db):
    session_id = str(uuid4())
    service = EventService(db)
    service.create_task(session_id, command(title="Отправить отчет"), "user_text")
    service.create_meeting(
        session_id,
        command(
            action=Action.CREATE_MEETING,
            title="Встреча с дизайнером",
            event_type=EventType.MEETING,
            starts_at=datetime.fromisoformat("2026-10-22T11:00:00+03:00"),
            due_at=None,
        ),
        "user_text",
    )

    assert service.search_events(session_id, "Найди задачу про отчет")[0].title == "Отправить отчет"
    assert service.search_events(session_id, "Когда у меня встреча с дизайнером?")[0].title == "Встреча с дизайнером"


def test_search_respects_date_range(db):
    session_id = str(uuid4())
    service = EventService(db)
    service.create_meeting(
        session_id,
        command(
            action=Action.CREATE_MEETING,
            title="Встреча с Иваном",
            event_type=EventType.MEETING,
            starts_at=datetime.fromisoformat("2026-10-16T11:00:00+03:00"),
            due_at=None,
        ),
        "user_text",
    )
    service.create_meeting(
        session_id,
        command(
            action=Action.CREATE_MEETING,
            title="Встреча с Иваном",
            event_type=EventType.MEETING,
            starts_at=datetime.fromisoformat("2026-10-17T11:00:00+03:00"),
            due_at=None,
        ),
        "user_text",
    )

    result = service.search_events(
        session_id,
        "Иван",
        datetime.fromisoformat("2026-10-16T00:00:00+03:00"),
        datetime.fromisoformat("2026-10-16T23:59:59+03:00"),
    )

    assert len(result) == 1
    assert result[0].starts_at == "2026-10-16T11:00:00+03:00"


def test_meeting_gets_default_duration(db):
    session_id = str(uuid4())
    service = EventService(db)
    starts = datetime.fromisoformat("2026-10-11T15:00:00+03:00")
    item = service.create_meeting(
        session_id,
        command(
            action=Action.CREATE_MEETING,
            title="Встреча с Иваном",
            event_type=EventType.MEETING,
            starts_at=starts,
            due_at=None,
        ),
        "user_text",
    )
    assert item.ends_at == "2026-10-11T16:00:00+03:00"


def test_pending_action_round_trip(db):
    session_id = str(uuid4())
    service = DialogueService(db)
    pending = command(
        action=Action.CREATE_MEETING,
        title="Встреча с Иваном",
        starts_at=None,
        due_at=None,
        missing_fields=["start_time"],
        clarification_question="На какое время?",
    )
    service.set_pending(session_id, pending)

    assert service.get_pending(session_id)["title"] == "Встреча с Иваном"
    service.clear_pending(session_id)
    assert service.get_pending(session_id) is None
