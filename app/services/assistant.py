import logging
from collections.abc import Callable
from datetime import datetime, time
from typing import Protocol
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config import Settings
from app.domain.commands import (
    Action,
    AssistantCommand,
    AssistantResponse,
    EventResponse,
    EventType,
)
from app.integrations.polza import TranscriptionError
from app.services.commands import (
    DEFAULT_TITLES,
    _has_event_query_intent,
    _is_past,
    clarification_question,
    detect_event_type,
    normalize_command,
)
from app.services.datetime_parser import combine, parse_datetime_parts
from app.services.events import DialogueService, EventService
from app.services.text import normalize_text


logger = logging.getLogger(__name__)


class CommandClient(Protocol):
    async def interpret(
        self, *, text: str, now_iso: str, pending_action: dict | None = None
    ) -> AssistantCommand: ...


class SpeechToTextClient(Protocol):
    async def transcribe(
        self, filename: str, content: bytes, content_type: str
    ) -> str: ...


class AssistantService:
    def __init__(
        self,
        db: Session,
        settings: Settings,
        client: CommandClient,
        now_provider: Callable[[], datetime] | None = None,
        transcription_client: SpeechToTextClient | None = None,
    ) -> None:
        self.settings = settings
        self.client = client
        self.db = db
        self._now_provider = now_provider
        self.events = EventService(db, settings.default_timezone)
        self.dialogue = DialogueService(
            db,
            settings.dialogue_ttl_minutes,
            now_provider=lambda: self._now(),
        )
        self.transcription_client = transcription_client

    async def handle_text(self, session_id: str, text: str) -> AssistantResponse:
        pending = self.dialogue.get_pending(session_id)
        command = await self.client.interpret(
            text=text,
            now_iso=self._now_iso(),
            pending_action=pending,
        )
        command = self._normalize_and_log(command, pending)
        return self.execute(session_id, command, "user_text")

    async def handle_audio(
        self, session_id: str, filename: str, content: bytes, content_type: str
    ) -> AssistantResponse:
        if self.transcription_client is None:
            raise TranscriptionError("Распознавание речи не настроено")
        pending = self.dialogue.get_pending(session_id)
        transcript = await self.transcription_client.transcribe(
            filename, content, content_type
        )
        command = await self.client.interpret(
            text=transcript,
            now_iso=self._now_iso(),
            pending_action=pending,
        )
        # The user must see the actual STT result even if the command model
        # paraphrases it in its structured response.
        command = command.model_copy(update={"transcript": transcript})
        command = self._normalize_and_log(command, pending)
        return self.execute(session_id, command, "user_audio")

    def execute(
        self, session_id: str, command: AssistantCommand, source: str
    ) -> AssistantResponse:
        if command.missing_fields or command.action == Action.CLARIFY:
            if command.action != Action.CLARIFY:
                self.dialogue.set_pending(session_id, command)
            question = command.clarification_question or "Уточните, пожалуйста, детали."
            return AssistantResponse(
                reply=question,
                action=command.action.value,
                transcript=command.transcript,
                needs_clarification=True,
            )

        if command.action == Action.CREATE_TASK:
            event = self._create_event_atomically(
                session_id, command, source, meeting=False
            )
            if event.type == EventType.REMINDER.value:
                reply = f"Напоминание «{event.title}» добавлено."
            elif event.due_date or event.due_at:
                reply = f"Задача «{event.title}» добавлена."
            else:
                reply = f"Задача «{event.title}» добавлена без срока."
            return AssistantResponse(
                reply=reply,
                action=command.action.value,
                transcript=command.transcript,
                created_event=EventResponse.model_validate(event),
            )
        if command.action == Action.CREATE_MEETING:
            event = self._create_event_atomically(
                session_id, command, source, meeting=True
            )
            return AssistantResponse(
                reply=f"Встреча «{event.title}» добавлена на {event.starts_at}.",
                action=command.action.value,
                transcript=command.transcript,
                created_event=EventResponse.model_validate(event),
            )
        if command.action == Action.LIST_EVENTS:
            events = self.events.list_events(
                session_id, command.date_from, command.date_to, command.event_type
            )
            self.dialogue.clear_pending(session_id)
            reply = list_reply(len(events), command.event_type)
            return AssistantResponse(
                reply=reply,
                action=command.action.value,
                transcript=command.transcript,
                events=[EventResponse.model_validate(event) for event in events],
            )
        if command.action == Action.SEARCH_EVENTS:
            events = self.events.search_events(
                session_id,
                command.query or "",
                command.date_from,
                command.date_to,
                command.event_type,
            )
            self.dialogue.clear_pending(session_id)
            reply = f"Найдено совпадений: {len(events)}." if events else "Совпадений не найдено."
            return AssistantResponse(
                reply=reply,
                action=command.action.value,
                transcript=command.transcript,
                events=[EventResponse.model_validate(event) for event in events],
            )
        raise ValueError(f"Unsupported action: {command.action}")

    def _create_event_atomically(
        self,
        session_id: str,
        command: AssistantCommand,
        source: str,
        *,
        meeting: bool,
    ):
        try:
            if meeting:
                event = self.events.create_meeting(
                    session_id, command, source, commit=False
                )
            else:
                event = self.events.create_task(
                    session_id, command, source, commit=False
                )
            self.dialogue.clear_pending(session_id, commit=False)
            self.db.commit()
            self.db.refresh(event)
            return event
        except Exception:
            self.db.rollback()
            raise

    def _now_iso(self) -> str:
        return self._now().isoformat()

    def _now(self) -> datetime:
        if self._now_provider:
            return self._now_provider()
        return datetime.now(ZoneInfo(self.settings.default_timezone))

    def _normalize_and_log(
        self, command: AssistantCommand, pending: dict | None = None
    ) -> AssistantCommand:
        now = self._now()
        normalized = normalize_command(command, now)
        if pending:
            pending_command = AssistantCommand.model_validate(pending)
            is_explicit_query = normalized.action in {
                Action.LIST_EVENTS,
                Action.SEARCH_EVENTS,
            } and _has_event_query_intent(normalize_text(command.transcript))
            if not is_explicit_query:
                normalized = self._merge_pending(normalized, pending_command, now)
        logger.info(
            "assistant_command action=%s type=%s title=%r due_at=%s starts_at=%s "
            "date_from=%s date_to=%s missing=%s",
            normalized.action.value,
            normalized.event_type.value if normalized.event_type else None,
            normalized.title,
            normalized.due_at,
            normalized.starts_at,
            normalized.date_from,
            normalized.date_to,
            normalized.missing_fields,
        )
        return normalized

    @staticmethod
    def _merge_pending(
        current: AssistantCommand, pending: AssistantCommand, now: datetime
    ) -> AssistantCommand:
        parsed = parse_datetime_parts(current.transcript, now)
        explicit_type = detect_event_type(current.transcript)
        event_type = explicit_type or pending.event_type or current.event_type
        action = (
            Action.CREATE_MEETING
            if event_type == EventType.MEETING
            else Action.CREATE_TASK
        )
        current_default = DEFAULT_TITLES.get(current.event_type)
        title = (
            current.title
            if current.title and current.title != current_default
            else pending.title or DEFAULT_TITLES[event_type]
        )

        updates = {
            "action": action,
            "event_type": event_type,
            "title": title,
            "ends_at": None,
        }
        if action == Action.CREATE_MEETING:
            known_date = (
                pending.starts_at.date()
                if pending.starts_at and "start_date" not in pending.missing_fields
                else None
            )
            known_time = (
                pending.starts_at.timetz().replace(tzinfo=None)
                if pending.starts_at and "start_time" not in pending.missing_fields
                else None
            )
            date_value = parsed.date_value if parsed.has_explicit_date else known_date
            time_value = parsed.time_value if parsed.has_explicit_time else known_time
            starts_at = (
                combine(date_value, time_value, now)
                if date_value and time_value
                else combine(date_value, time(9), now)
                if date_value
                else combine(now.date(), time_value, now)
                if time_value
                else None
            )
            updates.update({"starts_at": starts_at, "due_at": None, "due_date": None})
            missing = []
            if not date_value:
                missing.append("start_date")
            if not time_value:
                missing.append("start_time")
        else:
            known_date = (
                pending.due_date
                if pending.due_date and "due_date" not in pending.missing_fields
                else None
            )
            known_time = (
                pending.due_at.timetz().replace(tzinfo=None)
                if pending.due_at and "due_time" not in pending.missing_fields
                else None
            )
            date_value = parsed.date_value if parsed.has_explicit_date else known_date
            time_value = parsed.time_value if parsed.has_explicit_time else known_time
            due_at = (
                combine(date_value, time_value, now)
                if date_value and time_value
                else combine(now.date(), time_value, now)
                if time_value
                else None
            )
            updates.update(
                {"due_date": date_value, "due_at": due_at, "starts_at": None}
            )
            missing = []
            if not date_value:
                missing.append("due_date")
            if event_type == EventType.REMINDER and not time_value:
                missing.append("due_time")

        if parsed.invalid_date:
            missing = [field for field in missing if not field.endswith("_date")]
            missing.insert(0, "invalid_date")
        elif date_value and date_value < now.date():
            missing = ["past_date"]
        elif not missing and _is_past(action, event_type, updates, now):
            missing = ["future_datetime"]
        updates.update(
            {
                "missing_fields": missing,
                "clarification_question": clarification_question(event_type, missing),
            }
        )
        return current.model_copy(update=updates)


def list_reply(count: int, event_type) -> str:
    labels = {
        "task": ("задач", "Задач"),
        "reminder": ("напоминаний", "Напоминаний"),
        "meeting": ("встреч", "Встреч"),
    }
    label = labels.get(event_type.value if event_type else "", ("событий", "Событий"))
    return f"Найдено {label[0]}: {count}." if count else f"{label[1]} не найдено."
