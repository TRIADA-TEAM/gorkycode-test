from datetime import datetime, timedelta
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


def test_sessions_are_isolated(db):
    first, second = str(uuid4()), str(uuid4())
    service = EventService(db)
    service.create_task(first, command(title="Личная задача"), "user_text")

    assert service.list_events(second) == []
    assert {item.title for item in service.list_events(first)} == {"Личная задача"}


def test_search_by_partial_text(db):
    session_id = str(uuid4())
    service = EventService(db)
    service.create_task(session_id, command(title="Подготовить презентацию по бюджету"), "user_text")

    result = service.search_events(session_id, "презентация бюджет")

    assert len(result) == 1
    assert result[0].title == "Подготовить презентацию по бюджету"


def test_search_finds_tickets_by_keyword(db):
    session_id = str(uuid4())
    service = EventService(db)
    service.create_task(
        session_id,
        command(title="Купить билеты", transcript="Купить билеты"),
        "user_text",
    )

    result = service.search_events(session_id, "Найди задачу про билеты")

    assert len(result) == 1
    assert result[0].title == "Купить билеты"


def test_search_discards_conversational_subject_words(db):
    session_id = str(uuid4())
    service = EventService(db)
    service.create_task(
        session_id,
        command(title="Отправить письмо", transcript="Отправить письмо"),
        "user_text",
    )

    result = service.search_events(session_id, "то, что связано с письмом")

    assert len(result) == 1
    assert result[0].title == "Отправить письмо"


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


def test_search_uses_original_user_text_and_ranks_all_terms_first(db):
    session_id = str(uuid4())
    service = EventService(db)
    service.create_task(
        session_id,
        command(
            title="Подготовить материалы",
            transcript="Подготовить материалы по годовому отчету",
        ),
        "user_text",
    )
    service.create_task(
        session_id,
        command(
            title="Отправить отчет",
            transcript="Отправить отчет клиенту",
        ),
        "user_text",
    )

    result = service.search_events(session_id, "годовой отчет")

    assert [event.title for event in result] == ["Подготовить материалы"]


def test_search_prefers_results_matching_all_keywords(db):
    session_id = str(uuid4())
    service = EventService(db)
    service.create_task(
        session_id,
        command(
            title="Подготовить материалы",
            transcript="Подготовить материалы по годовому отчету",
        ),
        "user_text",
    )
    service.create_task(
        session_id,
        command(title="Отправить отчет", transcript="Отправить отчет клиенту"),
        "user_text",
    )

    result = service.search_events(session_id, "годовой отчет")

    assert [event.title for event in result] == ["Подготовить материалы"]


def test_derived_word_search_still_requires_every_meaningful_term(db):
    session_id = str(uuid4())
    service = EventService(db)
    for title in (
        "Согласовать договор",
        "Подписать договор",
        "Согласовать бюджет",
    ):
        service.create_task(
            session_id,
            command(title=title, transcript=title),
            "user_text",
        )

    result = service.search_events(
        session_id, "Найди задачу про согласование договора"
    )

    assert [event.title for event in result] == ["Согласовать договор"]


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


def test_pending_action_expires_after_ttl(db):
    session_id = str(uuid4())
    started_at = datetime.fromisoformat("2026-10-15T12:00:00+00:00")
    pending = command(
        action=Action.CREATE_MEETING,
        title="Встреча с Иваном",
        starts_at=None,
        due_at=None,
        missing_fields=["start_time"],
        clarification_question="На какое время?",
    )
    DialogueService(db, ttl_minutes=30, now_provider=lambda: started_at).set_pending(
        session_id, pending
    )

    active = DialogueService(
        db, ttl_minutes=30, now_provider=lambda: started_at + timedelta(minutes=29)
    )
    expired = DialogueService(
        db, ttl_minutes=30, now_provider=lambda: started_at + timedelta(minutes=31)
    )

    assert active.get_pending(session_id) is not None
    assert expired.get_pending(session_id) is None


def test_search_time_boundary_separates_past_and_future_today(db):
    session_id = str(uuid4())
    service = EventService(db)
    service.create_meeting(
        session_id,
        command(
            title="Кино утром",
            event_type=EventType.MEETING,
            starts_at=datetime.fromisoformat("2026-10-28T17:00:00+03:00"),
            due_at=None,
        ),
        "user_text",
    )
    service.create_meeting(
        session_id,
        command(
            title="Кино вечером",
            event_type=EventType.MEETING,
            starts_at=datetime.fromisoformat("2026-10-28T20:00:00+03:00"),
            due_at=None,
        ),
        "user_text",
    )
    now = datetime.fromisoformat("2026-10-28T18:49:00+03:00")

    past = service.search_events(session_id, "кино", date_to=now)
    future = service.search_events(session_id, "кино", date_from=now)

    assert [event.title for event in past] == ["Кино утром"]
    assert [event.title for event in future] == ["Кино вечером"]


def test_search_requires_person_name_when_it_is_in_query(db):
    session_id = str(uuid4())
    service = EventService(db)
    service.create_meeting(
        session_id,
        command(
            title="Кино с Марусей",
            event_type=EventType.MEETING,
            starts_at=datetime.fromisoformat("2026-10-30T19:00:00+03:00"),
            due_at=None,
        ),
        "user_text",
    )

    with_marusya = service.search_events(
        session_id, "встреча в кино с Марусей", event_type=EventType.MEETING
    )
    with_ivan = service.search_events(
        session_id, "встреча в кино с Иваном", event_type=EventType.MEETING
    )

    assert [event.title for event in with_marusya] == ["Кино с Марусей"]
    assert with_ivan == []


def test_mixed_event_types_are_sorted_by_one_timeline(db):
    session_id = str(uuid4())
    service = EventService(db)
    service.create_task(
        session_id,
        command(
            title="Далекое напоминание",
            event_type=EventType.REMINDER,
            due_at=datetime.fromisoformat("2027-10-26T15:00:00+03:00"),
        ),
        "user_text",
    )
    service.create_meeting(
        session_id,
        command(
            title="Ближайшая встреча",
            event_type=EventType.MEETING,
            starts_at=datetime.fromisoformat("2026-10-29T15:00:00+03:00"),
            due_at=None,
        ),
        "user_text",
    )
    service.create_task(
        session_id,
        command(
            title="Ближайшее напоминание",
            event_type=EventType.REMINDER,
            due_at=datetime.fromisoformat("2026-10-29T14:00:00+03:00"),
        ),
        "user_text",
    )

    result = service.list_events(session_id)

    assert [event.title for event in result] == [
        "Ближайшее напоминание",
        "Ближайшая встреча",
        "Далекое напоминание",
    ]


def test_mixed_type_filter_does_not_include_unrequested_type(db):
    session_id = str(uuid4())
    service = EventService(db)
    target_date = datetime.fromisoformat("2026-10-29T12:00:00+03:00")
    service.create_task(
        session_id,
        command(title="Задача", due_at=target_date, event_type=EventType.TASK),
        "user_text",
    )
    service.create_meeting(
        session_id,
        command(
            title="Встреча",
            event_type=EventType.MEETING,
            starts_at=target_date,
            due_at=None,
        ),
        "user_text",
    )
    service.create_task(
        session_id,
        command(
            title="Напоминание",
            due_at=target_date,
            event_type=EventType.REMINDER,
        ),
        "user_text",
    )

    result = service.list_events(
        session_id,
        event_types=[EventType.TASK, EventType.MEETING],
    )

    assert {event.title for event in result} == {"Задача", "Встреча"}
