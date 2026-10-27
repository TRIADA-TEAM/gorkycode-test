import asyncio
from datetime import datetime
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.config import Settings
from app.db.base import Event
from app.domain.commands import Action, AssistantCommand, EventType
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
