import asyncio
from datetime import datetime
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.config import Settings
from app.db.base import Event
from app.domain.commands import Action, AssistantCommand, EventStatus, EventType
from app.services.assistant import AssistantService, list_reply


NOW = datetime.fromisoformat("2026-10-15T12:00:00+03:00")


def test_list_reply_uses_requested_event_type():
    assert list_reply(2, EventType.TASK) == "Найдено задач: 2."
    assert list_reply(1, EventType.REMINDER) == "Найдено напоминаний: 1."
    assert list_reply(3, EventType.MEETING) == "Найдено встреч: 3."
    assert list_reply(4, None) == "Найдено событий: 4."


def test_empty_list_reply_uses_requested_event_type():
    assert list_reply(0, EventType.TASK) == "Задач не найдено."
    assert list_reply(0, EventType.MEETING) == "Встреч не найдено."
    assert list_reply(0, None) == "Событий не найдено."


def test_pending_status_filter_excludes_completed_tasks_end_to_end(db):
    session_id = str(uuid4())
    client = ScriptedClient([
        AssistantCommand(
            action=Action.LIST_EVENTS,
            transcript="Покажи невыполненные задачи на 20 октября 2026",
            event_type=EventType.TASK,
            statuses=[EventStatus.COMPLETED],
        )
    ])
    service = AssistantService(db, Settings(), client, lambda: NOW)
    completed = service.execute(
        session_id,
        AssistantCommand(
            action=Action.CREATE_TASK,
            transcript="Создай задачу Готовая на 20 октября 2026",
            title="Готовая",
            event_type=EventType.TASK,
            due_date=datetime.fromisoformat("2026-10-20").date(),
        ),
        "user_text",
    ).created_event
    service.execute(
        session_id,
        AssistantCommand(
            action=Action.CREATE_TASK,
            transcript="Создай задачу Активная на 20 октября 2026",
            title="Активная",
            event_type=EventType.TASK,
            due_date=datetime.fromisoformat("2026-10-20").date(),
        ),
        "user_text",
    )
    service.events.set_status(
        service.events.get_owned(session_id, completed.id), EventStatus.COMPLETED
    )

    result = asyncio.run(
        service.handle_text(
            session_id, "Покажи невыполненные задачи на 20 октября 2026"
        )
    )

    assert [event.title for event in result.events] == ["Активная"]


def test_ticket_search_works_through_assistant_flow(db):
    session_id = str(uuid4())
    service = AssistantService(db, Settings(), ScriptedClient([]), lambda: NOW)
    service.execute(
        session_id,
        AssistantCommand(
            action=Action.CREATE_TASK,
            transcript="Создай задачу QA100-F купить билеты на 20 октября",
            title="QA100-F купить билеты",
            event_type=EventType.TASK,
            due_date=datetime.fromisoformat("2026-10-20").date(),
        ),
        "user_text",
    )

    result = asyncio.run(service.handle_text(session_id, "Найди задачу про билеты"))

    assert [event.title for event in result.events] == ["QA100-F купить билеты"]


@pytest.mark.parametrize(
    "query",
    [
        "Я когда-то ставил задачу по договору, найди ее",
        "Найди задачу про согласование договора",
        "Найди задачу по договору",
    ],
)
def test_assignment_contract_search_phrases_find_existing_task(db, query):
    session_id = str(uuid4())
    service = AssistantService(db, Settings(), ScriptedClient([]), lambda: NOW)
    service.execute(
        session_id,
        AssistantCommand(
            action=Action.CREATE_TASK,
            transcript="Создай задачу согласовать договор послезавтра",
            title="Согласовать договор",
            event_type=EventType.TASK,
            due_date=datetime.fromisoformat("2026-10-17").date(),
        ),
        "user_text",
    )

    result = asyncio.run(service.handle_text(session_id, query))

    assert [event.title for event in result.events] == ["Согласовать договор"]


@pytest.mark.parametrize(
    "query",
    [
        "Найди, пожалуйста, задачу про договор",
        "Поищи, пожалуйста, что я планировал сделать с договором",
        "Покажи ту задачу, где речь шла о договоре",
        "Когда запланировано согласование договора?",
    ],
)
def test_core_02_free_form_search_finds_only_target_task(db, query):
    session_id = str(uuid4())
    service = AssistantService(db, Settings(), ScriptedClient([]), lambda: NOW)
    for title in (
        "Согласовать договор",
        "Подписать акт",
        "Согласовать бюджет",
    ):
        service.execute(
            session_id,
            AssistantCommand(
                action=Action.CREATE_TASK,
                transcript=f"Создай задачу {title} послезавтра",
                title=title,
                event_type=EventType.TASK,
                due_date=datetime.fromisoformat("2026-10-17").date(),
            ),
            "user_text",
        )

    result = asyncio.run(service.handle_text(session_id, query))

    assert [event.title for event in result.events] == ["Согласовать договор"]


def test_targeted_missing_subject_does_not_fall_back_to_general_list(db):
    session_id = str(uuid4())
    service = AssistantService(db, Settings(), ScriptedClient([]), lambda: NOW)
    service.execute(
        session_id,
        AssistantCommand(
            action=Action.CREATE_TASK,
            transcript="Создай задачу Согласовать договор послезавтра",
            title="Согласовать договор",
            event_type=EventType.TASK,
            due_date=datetime.fromisoformat("2026-10-17").date(),
        ),
        "user_text",
    )

    result = asyncio.run(
        service.handle_text(
            session_id, "Покажи ту задачу, где речь шла о несуществующем проекте"
        )
    )

    assert result.action == Action.SEARCH_EVENTS.value
    assert result.events == []


@pytest.mark.parametrize(
    "query",
    [
        "Отыщи задание о страховом полисе",
        "Найди ту запись, где упоминался страховой полис",
        "Покажи, пожалуйста, запись, в которой говорилось о страховом полисе",
        "Посмотри, пожалуйста, что я собирался делать со страховым полисом",
    ],
)
def test_new_policy_search_wordings_find_existing_task(db, query):
    session_id = str(uuid4())
    service = AssistantService(db, Settings(), ScriptedClient([]), lambda: NOW)
    service.execute(
        session_id,
        AssistantCommand(
            action=Action.CREATE_TASK,
            transcript="Проверить страховой полис 20 октября",
            title="Проверить страховой полис",
            event_type=EventType.TASK,
            due_date=datetime.fromisoformat("2026-10-20").date(),
        ),
        "user_text",
    )

    result = asyncio.run(service.handle_text(session_id, query))

    assert [event.title for event in result.events] == ["Проверить страховой полис"]


class ScriptedClient:
    def __init__(self, commands: list[AssistantCommand]) -> None:
        self.commands = iter(commands)

    async def interpret(self, **_kwargs) -> AssistantCommand:
        return next(self.commands)


class CapturingClient(ScriptedClient):
    def __init__(self, commands: list[AssistantCommand]) -> None:
        super().__init__(commands)
        self.requests = []

    async def interpret(self, **kwargs) -> AssistantCommand:
        self.requests.append(kwargs)
        return await super().interpret(**kwargs)


class ScriptedTranscriber:
    def __init__(self, transcripts: list[str]) -> None:
        self.transcripts = iter(transcripts)
        self.requests = []

    async def transcribe(self, filename, content, content_type) -> str:
        self.requests.append((filename, content, content_type))
        return next(self.transcripts)


def test_meeting_clarification_keeps_date_and_updates_time(db):
    session_id = str(uuid4())
    client = ScriptedClient([
        AssistantCommand(
            action=Action.CREATE_MEETING,
            transcript="Добавь встречу с Иваном 20 октября",
            title="Встреча с Иваном",
            event_type=EventType.MEETING,
            starts_at=datetime.fromisoformat("2026-10-20T09:00:00+03:00"),
            missing_fields=["start_time"],
            clarification_question="На какое время поставить встречу?",
        ),
        AssistantCommand(
            action=Action.CREATE_MEETING,
            transcript="В 15:30",
            title="Встреча с Иваном",
            event_type=EventType.MEETING,
            starts_at=datetime.fromisoformat("2026-10-15T15:30:00+03:00"),
        ),
    ])
    service = AssistantService(db, Settings(), client, lambda: NOW)

    first = asyncio.run(
        service.handle_text(session_id, "Добавь встречу с Иваном 20 октября")
    )
    # HTTP requests create a new service object; pending state must survive it.
    service = AssistantService(db, Settings(), client, lambda: NOW)
    second = asyncio.run(service.handle_text(session_id, "В 15:30"))

    assert first.needs_clarification is True
    assert second.needs_clarification is False
    assert second.created_event.starts_at == "2026-10-20T15:30:00+03:00"
    assert service.dialogue.get_pending(session_id) is None


def test_pending_action_survives_database_error(db, monkeypatch):
    session_id = str(uuid4())
    service = AssistantService(db, Settings(), ScriptedClient([]))
    pending = AssistantCommand(
        action=Action.CREATE_MEETING,
        transcript="Добавь встречу с Иваном 20 октября",
        title="Встреча с Иваном",
        event_type=EventType.MEETING,
        starts_at=datetime.fromisoformat("2026-10-20T09:00:00+03:00"),
        missing_fields=["start_time"],
        clarification_question="На какое время поставить встречу?",
    )
    service.dialogue.set_pending(session_id, pending)
    completed = pending.model_copy(
        update={
            "starts_at": datetime.fromisoformat("2026-10-20T15:00:00+03:00"),
            "missing_fields": [],
            "clarification_question": None,
        }
    )
    monkeypatch.setattr(
        service.events,
        "create_meeting",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("database error")),
    )

    with pytest.raises(RuntimeError, match="database error"):
        service.execute(session_id, completed, "user_text")

    assert service.dialogue.get_pending(session_id) is not None


def test_event_and_pending_clear_are_rolled_back_together(db, monkeypatch):
    session_id = str(uuid4())
    service = AssistantService(db, Settings(), ScriptedClient([]), lambda: NOW)
    pending = AssistantCommand(
        action=Action.CREATE_TASK,
        transcript="Добавь задачу подготовить отчёт",
        title="Подготовить отчёт",
        event_type=EventType.TASK,
        due_date=datetime.fromisoformat("2026-10-20").date(),
        missing_fields=["due_date"],
        clarification_question="На какую дату поставить задачу?",
    )
    service.dialogue.set_pending(session_id, pending)
    completed = pending.model_copy(
        update={"missing_fields": [], "clarification_question": None}
    )

    def fail_clear(*_args, **_kwargs):
        raise RuntimeError("clear failed")

    monkeypatch.setattr(service.dialogue, "clear_pending", fail_clear)

    with pytest.raises(RuntimeError, match="clear failed"):
        service.execute(session_id, completed, "user_text")

    assert db.scalars(select(Event).where(Event.owner_session_id == session_id)).all() == []
    assert service.dialogue.get_pending(session_id) is not None


def test_meeting_keeps_asking_until_date_and_time_are_known(db):
    session_id = str(uuid4())
    client = ScriptedClient(
        [
            AssistantCommand(
                action=Action.CREATE_MEETING,
                transcript="Добавь встречу с Иваном",
                title="Встреча с Иваном",
                event_type=EventType.MEETING,
                missing_fields=["start_date", "start_time"],
                clarification_question="На какую дату и время поставить встречу?",
            ),
            AssistantCommand(
                action=Action.CREATE_MEETING,
                transcript="В 15:00",
                title=None,
                event_type=EventType.MEETING,
                missing_fields=["start_date"],
                clarification_question="На какую дату поставить встречу?",
            ),
            AssistantCommand(
                action=Action.CREATE_MEETING,
                transcript="20 октября",
                title=None,
                event_type=EventType.MEETING,
                missing_fields=["start_time"],
                clarification_question="На какое время поставить встречу?",
            ),
        ]
    )
    clock = lambda: NOW

    first = asyncio.run(
        AssistantService(db, Settings(), client, clock).handle_text(
            session_id, "Добавь встречу с Иваном"
        )
    )
    second = asyncio.run(
        AssistantService(db, Settings(), client, clock).handle_text(session_id, "В 15:00")
    )
    third = asyncio.run(
        AssistantService(db, Settings(), client, clock).handle_text(session_id, "20 октября")
    )

    assert first.needs_clarification is True
    assert second.needs_clarification is True
    assert second.reply == "На какую дату поставить встречу?"
    assert third.needs_clarification is False
    assert third.created_event.starts_at == "2026-10-20T15:00:00+03:00"


def test_task_clarification_preserves_numeric_date_and_time(db):
    session_id = str(uuid4())
    client = ScriptedClient(
        [
            AssistantCommand(
                action=Action.CREATE_TASK,
                transcript="Добавь задачу сходить в магазин",
                title="Сходить в магазин",
                event_type=EventType.TASK,
                missing_fields=["due_date"],
                clarification_question="На какую дату поставить задачу?",
            ),
            AssistantCommand(
                action=Action.CREATE_TASK,
                transcript="На 20.10.26 16:00",
                title=None,
                event_type=EventType.TASK,
                missing_fields=["title"],
                clarification_question="Что нужно сделать?",
            ),
        ]
    )
    clock = lambda: NOW
    service = AssistantService(db, Settings(), client, clock)

    first = asyncio.run(service.handle_text(session_id, "Добавь задачу сходить в магазин"))
    second = asyncio.run(service.handle_text(session_id, "На 20.10.26 16:00"))

    assert first.needs_clarification is True
    assert second.needs_clarification is False
    assert second.created_event.due_date == "2026-10-20"
    assert second.created_event.due_at == "2026-10-20T16:00:00+03:00"


def test_meeting_time_clarification_keeps_exact_hour(db):
    session_id = str(uuid4())
    client = ScriptedClient(
        [
            AssistantCommand(
                action=Action.CREATE_MEETING,
                transcript="Добавь встречу 20 октября в кафе",
                title="Встреча в кафе",
                event_type=EventType.MEETING,
                starts_at=datetime.fromisoformat("2026-10-20T09:00:00+03:00"),
                missing_fields=["start_time"],
                clarification_question="На какое время поставить встречу?",
            ),
            AssistantCommand(
                action=Action.CREATE_MEETING,
                transcript="12:00",
                title=None,
                event_type=EventType.MEETING,
                missing_fields=["start_date"],
                clarification_question="На какую дату поставить встречу?",
            ),
        ]
    )
    clock = lambda: NOW
    service = AssistantService(db, Settings(), client, clock)

    first = asyncio.run(service.handle_text(session_id, "Добавь встречу 20 октября в кафе"))
    second = asyncio.run(service.handle_text(session_id, "12:00"))

    assert first.needs_clarification is True
    assert second.needs_clarification is False
    assert second.created_event.starts_at == "2026-10-20T12:00:00+03:00"


def test_pending_task_treats_clarify_response_as_context_answer(db):
    session_id = str(uuid4())
    client = ScriptedClient(
        [
            AssistantCommand(
                action=Action.CREATE_TASK,
                transcript="Нужно сходить в магазин",
                title="Сходить в магазин",
                event_type=EventType.TASK,
                missing_fields=["due_date"],
                clarification_question="На какую дату поставить задачу?",
            ),
            AssistantCommand(
                action=Action.CLARIFY,
                transcript="На сегодня",
                clarification_question="Уточните запрос.",
            ),
        ]
    )
    clock = lambda: NOW

    first = asyncio.run(
        AssistantService(db, Settings(), client, clock).handle_text(
            session_id, "Нужно сходить в магазин"
        )
    )
    second = asyncio.run(
        AssistantService(db, Settings(), client, clock).handle_text(
            session_id, "На сегодня"
        )
    )

    assert first.needs_clarification is True
    assert second.needs_clarification is False
    assert second.created_event.title == "Сходить в магазин"
    assert second.created_event.due_date == "2026-10-15"


def test_pending_reminder_uses_original_time_answer(db):
    session_id = str(uuid4())
    client = ScriptedClient(
        [
            AssistantCommand(
                action=Action.CREATE_TASK,
                transcript="Напомни завтра подготовить документы",
                title="Подготовить документы",
                event_type=EventType.REMINDER,
                due_date=datetime.fromisoformat("2026-10-16").date(),
                missing_fields=["due_time"],
                clarification_question="На какое время поставить напоминание?",
            ),
            AssistantCommand(
                action=Action.CLARIFY,
                transcript="Уточните время",
                clarification_question="На какое время поставить напоминание?",
            ),
        ]
    )
    clock = lambda: NOW

    first = asyncio.run(
        AssistantService(db, Settings(), client, clock).handle_text(
            session_id, "Напомни завтра подготовить документы"
        )
    )
    second = asyncio.run(
        AssistantService(db, Settings(), client, clock).handle_text(session_id, "12 утра")
    )

    assert first.needs_clarification is True
    assert second.needs_clarification is False
    assert second.created_event.type == EventType.REMINDER.value
    assert second.created_event.due_at == "2026-10-16T12:00:00+03:00"


def test_greeting_clears_unfinished_creation(db):
    session_id = str(uuid4())
    client = ScriptedClient([
        AssistantCommand(
            action=Action.CLARIFY,
            transcript="Привет",
            clarification_question="Я помогаю с задачами и встречами.",
        )
    ])
    assistant = AssistantService(db, Settings(), client, lambda: NOW)
    pending = AssistantCommand(
        action=Action.CREATE_TASK,
        transcript="Создай задачу",
        title="Задача",
        event_type=EventType.TASK,
        missing_fields=["title", "due_date"],
        clarification_question="Что нужно сделать?",
    )
    assistant.dialogue.set_pending(session_id, pending)

    result = asyncio.run(assistant.handle_text(session_id, "Привет"))

    assert result.action == Action.CLARIFY.value
    assert assistant.dialogue.get_pending(session_id) is None


def test_day_schedule_is_built_without_language_model(db):
    session_id = str(uuid4())
    service = AssistantService(db, Settings(), ScriptedClient([]), lambda: NOW)
    service.execute(
        session_id,
        AssistantCommand(
            action=Action.CREATE_MEETING,
            transcript="Встреча завтра в 15:00",
            title="Встреча с командой",
            event_type=EventType.MEETING,
            starts_at=datetime.fromisoformat("2026-10-16T15:00:00+03:00"),
        ),
        "user_text",
    )
    service.execute(
        session_id,
        AssistantCommand(
            action=Action.CREATE_TASK,
            transcript="Задача на завтра",
            title="Подготовить презентацию",
            event_type=EventType.TASK,
            due_date=datetime.fromisoformat("2026-10-16").date(),
        ),
        "user_text",
    )

    result = asyncio.run(
        service.handle_text(session_id, "Покажи расписание на завтра")
    )

    assert result.view_mode == "day_schedule"
    assert result.schedule_date.isoformat() == "2026-10-16"
    assert [event.title for event in result.events] == [
        "Подготовить презентацию",
        "Встреча с командой",
    ]


def test_empty_day_schedule_keeps_schedule_view(db):
    service = AssistantService(db, Settings(), ScriptedClient([]), lambda: NOW)

    result = asyncio.run(
        service.handle_text(str(uuid4()), "Покажи расписание на завтра")
    )

    assert result.reply == "На этот день ничего не запланировано."
    assert result.view_mode == "day_schedule"
    assert result.events == []


def test_schedule_card_without_date_asks_for_day(db):
    service = AssistantService(db, Settings(), ScriptedClient([]), lambda: NOW)

    result = asyncio.run(service.handle_text(str(uuid4()), "Покажи карточку"))

    assert result.needs_clarification is True
    assert result.reply == "На какой день показать карточку расписания?"


def test_schedule_card_rejects_multi_day_period(db):
    service = AssistantService(db, Settings(), ScriptedClient([]), lambda: NOW)

    result = asyncio.run(
        service.handle_text(str(uuid4()), "Покажи расписание на следующую неделю")
    )

    assert result.needs_clarification is True
    assert result.reply == "Для карточки расписания укажите один конкретный день."


def test_audio_schedule_uses_same_deterministic_view(db):
    service = AssistantService(
        db,
        Settings(),
        ScriptedClient([]),
        lambda: NOW,
        transcription_client=ScriptedTranscriber(["Покажи расписание на завтра"]),
    )

    result = asyncio.run(
        service.handle_audio(str(uuid4()), "schedule.webm", b"audio", "audio/webm")
    )

    assert result.view_mode == "day_schedule"
    assert result.schedule_date.isoformat() == "2026-10-16"


def test_search_after_invalid_creation_does_not_execute_stale_pending(db):
    session_id = str(uuid4())
    client = ScriptedClient([
        AssistantCommand(
            action=Action.CREATE_TASK,
            transcript="Добавь задачу сдать справку завтра в 24:75",
            title="Сдать справку",
            event_type=EventType.TASK,
        )
    ])
    first_service = AssistantService(db, Settings(), client, lambda: NOW)

    first = asyncio.run(
        first_service.handle_text(
            session_id, "Добавь задачу сдать справку завтра в 24:75"
        )
    )
    second_service = AssistantService(db, Settings(), ScriptedClient([]), lambda: NOW)
    second = asyncio.run(
        second_service.handle_text(session_id, "Покажи задание про итоговый бюджет")
    )

    assert first.needs_clarification is True
    assert first.reply.startswith("Время указано некорректно")
    assert second.action == Action.SEARCH_EVENTS.value
    assert second.created_event is None
    assert second_service.dialogue.get_pending(session_id) is None
    assert db.scalars(select(Event).where(Event.owner_session_id == session_id)).all() == []


def test_explicit_creation_replaces_unfinished_creation_without_mixing_fields(db):
    session_id = str(uuid4())
    client = ScriptedClient([
        AssistantCommand(
            action=Action.CREATE_TASK,
            transcript="Напомни завтра набрать Петра",
            title="Набрать Петра",
            event_type=EventType.REMINDER,
        ),
        AssistantCommand(
            action=Action.CREATE_MEETING,
            transcript="Запланируй на завтра на 14:00 встречу с маркетологом",
            title="Встреча с маркетологом",
            event_type=EventType.MEETING,
        ),
    ])
    service = AssistantService(db, Settings(), client, lambda: NOW)

    first = asyncio.run(
        service.handle_text(session_id, "Напомни завтра набрать Петра")
    )
    second = asyncio.run(
        service.handle_text(
            session_id, "Запланируй на завтра на 14:00 встречу с маркетологом"
        )
    )

    assert first.needs_clarification is True
    assert second.needs_clarification is False
    assert second.created_event.type == EventType.MEETING.value
    assert second.created_event.title == "Встреча с маркетологом"
    assert second.created_event.starts_at == "2026-10-16T14:00:00+03:00"


def test_audio_is_transcribed_before_command_interpretation(db):
    session_id = str(uuid4())
    transcript = "Добавь задачу сходить в магазин завтра"
    model = CapturingClient(
        [
            AssistantCommand(
                action=Action.CREATE_TASK,
                transcript="Перефразированный текст",
                title="Сходить в магазин",
                event_type=EventType.TASK,
                due_date=datetime.fromisoformat("2026-10-16").date(),
            )
        ]
    )
    transcriber = ScriptedTranscriber([transcript])
    service = AssistantService(
        db,
        Settings(),
        model,
        lambda: NOW,
        transcription_client=transcriber,
    )

    result = asyncio.run(
        service.handle_audio(
            session_id, "recording.webm", b"webm-audio", "audio/webm"
        )
    )

    assert transcriber.requests == [
        ("recording.webm", b"webm-audio", "audio/webm")
    ]
    assert model.requests[0]["text"] == transcript
    assert "attachment_id" not in model.requests[0]
    assert result.transcript == transcript
    assert result.created_event.title == "Сходить в магазин"


def test_audio_clarification_uses_pending_context(db):
    session_id = str(uuid4())
    model = CapturingClient(
        [
            AssistantCommand(
                action=Action.CREATE_TASK,
                transcript="На сегодня",
                title=None,
                event_type=EventType.TASK,
                missing_fields=["title"],
                clarification_question="Что нужно сделать?",
            )
        ]
    )
    transcriber = ScriptedTranscriber(["На сегодня"])
    service = AssistantService(
        db,
        Settings(),
        model,
        lambda: NOW,
        transcription_client=transcriber,
    )
    pending = AssistantCommand(
        action=Action.CREATE_TASK,
        transcript="Нужно сходить в магазин",
        title="Сходить в магазин",
        event_type=EventType.TASK,
        missing_fields=["due_date"],
        clarification_question="На какую дату поставить задачу?",
    )
    service.dialogue.set_pending(session_id, pending)

    result = asyncio.run(
        service.handle_audio(session_id, "answer.webm", b"audio", "audio/webm")
    )

    assert model.requests[0]["pending_action"]["title"] == "Сходить в магазин"
    assert result.needs_clarification is False
    assert result.created_event.due_date == "2026-10-15"


def test_explicit_new_event_replaces_pending_creation(db):
    session_id = str(uuid4())
    client = ScriptedClient([
        AssistantCommand(
            action=Action.CREATE_TASK,
            transcript="Напомни сегодня в 10 оплатить интернет",
            title="Оплатить интернет",
            event_type=EventType.REMINDER,
        ),
        AssistantCommand(
            action=Action.CREATE_MEETING,
            transcript="Добавь встречу с Марусей",
            title="Встреча с Марусей",
            event_type=EventType.MEETING,
        ),
    ])
    now = datetime.fromisoformat("2026-10-28T18:49:00+03:00")
    service = AssistantService(db, Settings(), client, lambda: now)

    first = asyncio.run(
        service.handle_text(session_id, "Напомни сегодня в 10 оплатить интернет")
    )
    second = asyncio.run(service.handle_text(session_id, "Добавь встречу с Марусей"))

    assert first.needs_clarification is True
    assert "прошли" in first.reply
    assert second.action == Action.CREATE_MEETING.value
    assert second.needs_clarification is True
    assert second.reply == "На какую дату и время поставить встречу?"
    pending = service.dialogue.get_pending(session_id)
    assert pending["title"] == "Встреча с Марусей"
    assert pending["starts_at"] is None


def test_explicit_search_does_not_continue_pending_creation(db):
    session_id = str(uuid4())
    client = ScriptedClient([
        AssistantCommand(
            action=Action.CREATE_MEETING,
            transcript="Найди встречу в кино с Марусей",
            title="Встреча в кино с Марусей",
            event_type=EventType.MEETING,
        )
    ])
    service = AssistantService(db, Settings(), client, lambda: NOW)
    service.events.create_meeting(
        session_id,
        AssistantCommand(
            action=Action.CREATE_MEETING,
            transcript="Добавь встречу в кино с Марусей 20 октября в 19:00",
            title="Кино с Марусей",
            event_type=EventType.MEETING,
            starts_at=datetime.fromisoformat("2026-10-20T19:00:00+03:00"),
        ),
        "user_text",
    )
    service.dialogue.set_pending(
        session_id,
        AssistantCommand(
            action=Action.CREATE_TASK,
            transcript="Добавь задачу купить билеты",
            title="Купить билеты",
            event_type=EventType.TASK,
            missing_fields=["due_date"],
            clarification_question="На какую дату поставить задачу?",
        ),
    )

    result = asyncio.run(
        service.handle_text(session_id, "Найди встречу в кино с Марусей")
    )

    assert result.action == Action.SEARCH_EVENTS.value
    assert [event.title for event in result.events] == ["Кино с Марусей"]
    assert result.created_event is None
    assert service.dialogue.get_pending(session_id) is None
