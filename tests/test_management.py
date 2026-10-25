import asyncio
from datetime import datetime
from uuid import uuid4

from app.config import Settings
from app.domain.commands import Action, AssistantCommand, EventStatus, EventType
from app.services.assistant import AssistantService


NOW = datetime.fromisoformat("2026-10-15T12:00:00+03:00")


class ScriptedClient:
    def __init__(self, commands: list[AssistantCommand]) -> None:
        self.commands = iter(commands)
        self.calls = 0

    async def interpret(self, **_kwargs) -> AssistantCommand:
        self.calls += 1
        return next(self.commands)


def service(db, commands=None) -> AssistantService:
    return AssistantService(
        db,
        Settings(),
        ScriptedClient(commands or []),
        lambda: NOW,
    )


def meeting_command(title="Встреча с Иваном", day=20, hour=15):
    return AssistantCommand(
        action=Action.CREATE_MEETING,
        transcript=f"Добавь встречу {day} октября в {hour}:00",
        title=title,
        event_type=EventType.MEETING,
        starts_at=datetime.fromisoformat(f"2026-10-{day:02d}T{hour:02d}:00:00+03:00"),
    )


def task_command(title="Подготовить отчёт", day=20):
    return AssistantCommand(
        action=Action.CREATE_TASK,
        transcript=f"Добавь задачу на {day} октября",
        title=title,
        event_type=EventType.TASK,
        due_date=datetime.fromisoformat(f"2026-10-{day:02d}").date(),
    )


def test_created_event_becomes_last_referenced(db):
    session_id = str(uuid4())
    assistant = service(db)

    result = assistant.execute(session_id, task_command(), "user_text")

    assert assistant.dialogue.get_last_referenced_event_id(session_id) == result.created_event.id


def test_update_meeting_date_preserves_time_and_duration(db):
    session_id = str(uuid4())
    assistant = service(db)
    created = assistant.execute(session_id, meeting_command(), "user_text").created_event
    update = AssistantCommand(
        action=Action.UPDATE_EVENT,
        transcript="Перенеси встречу с Иваном на 22 октября",
        target_query="встреча с Иваном",
        event_type=EventType.MEETING,
    )

    result = assistant.execute(session_id, update, "user_text")

    assert result.created_event.id == created.id
    assert result.created_event.starts_at == "2026-10-22T15:00:00+03:00"
    assert result.created_event.ends_at == "2026-10-22T16:00:00+03:00"


def test_last_reference_supports_pronoun_update(db):
    session_id = str(uuid4())
    assistant = service(db)
    created = assistant.execute(session_id, meeting_command(), "user_text").created_event
    update = AssistantCommand(
        action=Action.UPDATE_EVENT,
        transcript="Перенеси её на 23 октября в 12:30",
    )

    result = assistant.execute(session_id, update, "user_text")

    assert result.created_event.id == created.id
    assert result.created_event.starts_at == "2026-10-23T12:30:00+03:00"


def test_soft_delete_hides_event_from_lists_and_search(db):
    session_id = str(uuid4())
    assistant = service(db)
    created = assistant.execute(session_id, task_command(), "user_text").created_event
    delete = AssistantCommand(
        action=Action.DELETE_EVENT,
        transcript="Удали задачу про отчёт",
        target_query="отчёт",
        event_type=EventType.TASK,
    )

    result = assistant.execute(session_id, delete, "user_text")

    assert result.created_event.id == created.id
    assert result.created_event.status == EventStatus.CANCELLED.value
    assert assistant.events.list_events(session_id) == []
    assert assistant.events.search_events(session_id, "отчёт") == []
    assert assistant.dialogue.get_last_referenced_event_id(session_id) is None


def test_complete_task_hides_it_from_upcoming_but_keeps_it_searchable(db):
    session_id = str(uuid4())
    assistant = service(db)
    created = assistant.execute(session_id, task_command(), "user_text").created_event
    complete = AssistantCommand(
        action=Action.COMPLETE_EVENT,
        transcript="Отметь задачу про отчёт выполненной",
        target_query="отчёт",
        event_type=EventType.TASK,
    )

    result = assistant.execute(session_id, complete, "user_text")

    assert result.created_event.id == created.id
    assert result.created_event.status == EventStatus.COMPLETED.value
    assert assistant.events.list_events(session_id) == []
    assert [item.id for item in assistant.events.search_events(session_id, "отчёт")] == [
        created.id
    ]


def test_event_from_another_session_cannot_be_deleted_by_id(db):
    owner = str(uuid4())
    stranger = str(uuid4())
    assistant = service(db)
    created = assistant.execute(owner, task_command(), "user_text").created_event

    result = assistant.execute(
        stranger,
        AssistantCommand(
            action=Action.DELETE_EVENT,
            transcript="Удали событие",
            target_event_id=created.id,
        ),
        "user_text",
    )

    assert result.reply == "Подходящее событие не найдено."
    assert assistant.events.get_owned(owner, created.id).status == EventStatus.PENDING.value


def test_ambiguous_delete_can_be_resolved_by_number(db):
    session_id = str(uuid4())
    followup = AssistantCommand(
        action=Action.CLARIFY,
        transcript="2",
        clarification_question="Уточните событие.",
    )
    assistant = service(db, [followup])
    first = assistant.execute(
        session_id, meeting_command("Встреча с Иваном", 20, 15), "user_text"
    ).created_event
    second = assistant.execute(
        session_id, meeting_command("Встреча с Иваном", 21, 16), "user_text"
    ).created_event

    ambiguous = assistant.execute(
        session_id,
        AssistantCommand(
            action=Action.DELETE_EVENT,
            transcript="Удали встречу с Иваном",
            target_query="встреча с Иваном",
            event_type=EventType.MEETING,
        ),
        "user_text",
    )
    selected = asyncio.run(assistant.handle_text(session_id, "2"))

    assert ambiguous.needs_clarification is True
    assert [event.id for event in ambiguous.events] == [first.id, second.id]
    assert selected.created_event.id == second.id
    assert selected.created_event.status == EventStatus.CANCELLED.value
    assert assistant.events.get_owned(session_id, first.id).status == EventStatus.PENDING.value


def test_cancel_phrase_clears_pending_without_calling_model(db):
    session_id = str(uuid4())
    model = ScriptedClient([])
    assistant = AssistantService(db, Settings(), model, lambda: NOW)
    pending = task_command().model_copy(
        update={
            "due_date": None,
            "missing_fields": ["due_date"],
            "clarification_question": "На какую дату поставить задачу?",
        }
    )
    assistant.dialogue.set_pending(session_id, pending)

    result = asyncio.run(assistant.handle_text(session_id, "Не надо"))

    assert result.action == Action.CANCEL_ACTION.value
    assert assistant.dialogue.get_pending(session_id) is None
    assert model.calls == 0


def test_past_update_is_kept_as_pending_clarification(db):
    session_id = str(uuid4())
    assistant = service(db)
    created = assistant.execute(session_id, task_command(), "user_text").created_event

    result = assistant.execute(
        session_id,
        AssistantCommand(
            action=Action.UPDATE_EVENT,
            transcript="Перенеси задачу про отчёт на 10 октября",
            target_event_id=created.id,
        ),
        "user_text",
    )

    assert result.needs_clarification is True
    assert "прошлое" in result.reply
    assert assistant.events.get_owned(session_id, created.id).due_date == "2026-10-20"
    assert assistant.dialogue.get_pending(session_id) is not None


def test_conflicting_meeting_requires_confirmation(db):
    session_id = str(uuid4())
    confirmation = AssistantCommand(
        action=Action.CLARIFY,
        transcript="Да",
        clarification_question="Подтвердите действие.",
    )
    assistant = service(db, [confirmation])
    existing = assistant.execute(
        session_id, meeting_command("Созвон с командой", 20, 15), "user_text"
    ).created_event

    conflict = assistant.execute(
        session_id, meeting_command("Встреча с Иваном", 20, 15), "user_text"
    )
    confirmed = asyncio.run(assistant.handle_text(session_id, "Да"))

    assert conflict.needs_clarification is True
    assert [event.id for event in conflict.events] == [existing.id]
    assert confirmed.needs_clarification is False
    assert confirmed.created_event.title == "Встреча с Иваном"


def test_adjacent_meetings_do_not_conflict(db):
    session_id = str(uuid4())
    assistant = service(db)
    assistant.execute(
        session_id, meeting_command("Первая встреча", 20, 15), "user_text"
    )

    result = assistant.execute(
        session_id, meeting_command("Вторая встреча", 20, 16), "user_text"
    )

    assert result.needs_clarification is False
    assert result.created_event.starts_at == "2026-10-20T16:00:00+03:00"


def test_moving_meeting_into_conflict_requires_confirmation(db):
    session_id = str(uuid4())
    assistant = service(db)
    assistant.execute(
        session_id, meeting_command("Первая встреча", 20, 15), "user_text"
    )
    moving = assistant.execute(
        session_id, meeting_command("Вторая встреча", 21, 12), "user_text"
    ).created_event

    result = assistant.execute(
        session_id,
        AssistantCommand(
            action=Action.UPDATE_EVENT,
            transcript="Перенеси вторую встречу на 20 октября в 15:30",
            target_event_id=moving.id,
        ),
        "user_text",
    )

    assert result.needs_clarification is True
    assert result.events[0].title == "Первая встреча"
    assert assistant.events.get_owned(session_id, moving.id).starts_at == "2026-10-21T12:00:00+03:00"


def test_confirmed_conflicting_move_keeps_requested_datetime(db):
    session_id = str(uuid4())
    confirmation = AssistantCommand(
        action=Action.CLARIFY,
        transcript="Да",
        clarification_question="Подтвердите действие.",
    )
    assistant = service(db, [confirmation])
    assistant.execute(
        session_id, meeting_command("Первая встреча", 20, 15), "user_text"
    )
    moving = assistant.execute(
        session_id, meeting_command("Вторая встреча", 21, 12), "user_text"
    ).created_event
    assistant.execute(
        session_id,
        AssistantCommand(
            action=Action.UPDATE_EVENT,
            transcript="Перенеси вторую встречу на 20 октября в 15:30",
            target_event_id=moving.id,
        ),
        "user_text",
    )

    result = asyncio.run(assistant.handle_text(session_id, "Да"))

    assert result.created_event.starts_at == "2026-10-20T15:30:00+03:00"


def test_rejecting_conflict_cancels_pending_action_without_model_call(db):
    session_id = str(uuid4())
    model = ScriptedClient([])
    assistant = AssistantService(db, Settings(), model, lambda: NOW)
    assistant.execute(
        session_id, meeting_command("Первая встреча", 20, 15), "user_text"
    )
    assistant.execute(
        session_id, meeting_command("Вторая встреча", 20, 15), "user_text"
    )

    result = asyncio.run(assistant.handle_text(session_id, "Нет"))

    assert result.action == Action.CANCEL_ACTION.value
    assert assistant.dialogue.get_pending(session_id) is None
    assert model.calls == 0
    assert len(assistant.events.list_events(session_id)) == 1


def test_renaming_task_without_due_date_does_not_require_a_date(db):
    session_id = str(uuid4())
    assistant = service(db)
    created = assistant.execute(
        session_id,
        AssistantCommand(
            action=Action.CREATE_TASK,
            transcript="Добавь задачу разобрать почту",
            title="Разобрать почту",
            event_type=EventType.TASK,
        ),
        "user_text",
    ).created_event

    result = assistant.execute(
        session_id,
        AssistantCommand(
            action=Action.UPDATE_EVENT,
            transcript="Переименуй её в Проверить почту",
            target_event_id=created.id,
            new_title="Проверить почту",
        ),
        "user_text",
    )

    assert result.created_event.title == "Проверить почту"
    assert result.created_event.due_date is None


def test_management_without_target_asks_which_event(db):
    session_id = str(uuid4())
    assistant = service(db)
    assistant.execute(session_id, task_command(), "user_text")

    result = assistant.execute(
        session_id,
        AssistantCommand(
            action=Action.DELETE_EVENT,
            transcript="Удали",
        ),
        "user_text",
    )

    assert result.needs_clarification is True
    assert result.reply == "Какое событие нужно удалить?"
    assert len(assistant.events.list_events(session_id)) == 1


def test_update_without_new_values_asks_what_to_change(db):
    session_id = str(uuid4())
    assistant = service(db)
    created = assistant.execute(session_id, task_command(), "user_text").created_event

    result = assistant.execute(
        session_id,
        AssistantCommand(
            action=Action.UPDATE_EVENT,
            transcript="Измени задачу про отчёт",
            target_event_id=created.id,
        ),
        "user_text",
    )

    assert result.needs_clarification is True
    assert result.reply == "Укажите новое название, дату или время события"
    assert assistant.dialogue.get_pending(session_id)["missing_fields"] == [
        "update_details"
    ]
