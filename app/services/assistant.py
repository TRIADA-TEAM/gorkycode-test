import logging
import re
from collections.abc import Callable
from datetime import datetime, time, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config import Settings
from app.domain.commands import (
    Action,
    AssistantCommand,
    AssistantResponse,
    EventResponse,
    EventStatus,
    EventType,
)
from app.integrations.polza import TranscriptionError
from app.services.commands import (
    DEFAULT_TITLES,
    _is_list_request,
    _is_past,
    _is_search_request,
    clarification_question,
    detect_event_type,
    normalize_command,
)
from app.services.datetime_parser import combine, parse_datetime_parts
from app.services.events import DialogueService, EventService
from app.services.text import normalize_text


logger = logging.getLogger(__name__)


class MeetingConflictError(ValueError):
    def __init__(self, conflicts) -> None:
        super().__init__("На это время уже запланирована другая встреча.")
        self.conflicts = conflicts


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
        if pending and self._should_cancel_pending(text, pending):
            return self.execute(
                session_id,
                AssistantCommand(action=Action.CANCEL_ACTION, transcript=text),
                "user_text",
            )
        command = await self.client.interpret(
            text=text,
            now_iso=self._now_iso(),
            pending_action=pending,
        )
        command = self._normalize_and_log(command, pending, source_text=text)
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
        if pending and self._should_cancel_pending(transcript, pending):
            return self.execute(
                session_id,
                AssistantCommand(action=Action.CANCEL_ACTION, transcript=transcript),
                "user_audio",
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
        if command.action == Action.CANCEL_ACTION:
            self.dialogue.clear_pending(session_id)
            return AssistantResponse(
                reply="Хорошо, действие отменено.",
                action=command.action.value,
                transcript=command.transcript,
            )

        if command.action in {
            Action.UPDATE_EVENT,
            Action.DELETE_EVENT,
            Action.COMPLETE_EVENT,
        }:
            return self._execute_event_management(session_id, command)

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
            starts_at = command.starts_at
            ends_at = command.ends_at or (
                starts_at + timedelta(hours=1) if starts_at else None
            )
            conflicts = (
                self.events.find_meeting_conflicts(session_id, starts_at, ends_at)
                if starts_at and ends_at and not command.conflict_confirmed
                else []
            )
            if conflicts:
                return self._conflict_response(session_id, command, conflicts)
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
                session_id,
                command.date_from,
                command.date_to,
                command.event_type,
                event_types=command.event_types,
            )
            self.dialogue.clear_pending(session_id)
            if len(events) == 1:
                self.dialogue.set_last_referenced_event_id(session_id, events[0].id)
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
                event_types=command.event_types,
            )
            self.dialogue.clear_pending(session_id)
            if len(events) == 1:
                self.dialogue.set_last_referenced_event_id(session_id, events[0].id)
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
            self.dialogue.set_last_referenced_event_id(
                session_id, event.id, commit=False
            )
            self.db.commit()
            self.db.refresh(event)
            return event
        except Exception:
            self.db.rollback()
            raise

    def _execute_event_management(
        self, session_id: str, command: AssistantCommand
    ) -> AssistantResponse:
        if not self._has_target(command):
            questions = {
                Action.UPDATE_EVENT: "Какое событие нужно изменить?",
                Action.DELETE_EVENT: "Какое событие нужно удалить?",
                Action.COMPLETE_EVENT: "Какую задачу или напоминание отметить выполненным?",
            }
            pending = command.model_copy(
                update={
                    "missing_fields": ["target_event"],
                    "clarification_question": questions[command.action],
                }
            )
            self.dialogue.set_pending(session_id, pending)
            return AssistantResponse(
                reply=pending.clarification_question,
                action=command.action.value,
                transcript=command.transcript,
                needs_clarification=True,
            )
        candidates = self._resolve_target_events(session_id, command)
        if not candidates:
            self.dialogue.clear_pending(session_id)
            return AssistantResponse(
                reply="Подходящее событие не найдено.",
                action=command.action.value,
                transcript=command.transcript,
            )
        if len(candidates) > 1:
            pending = command.model_copy(
                update={
                    "candidate_event_ids": [event.id for event in candidates],
                    "missing_fields": ["target_event"],
                    "clarification_question": "Уточните номер нужного события.",
                }
            )
            self.dialogue.set_pending(session_id, pending)
            return AssistantResponse(
                reply="Найдено несколько событий. Уточните номер нужного события.",
                action=command.action.value,
                transcript=command.transcript,
                events=[EventResponse.model_validate(event) for event in candidates],
                needs_clarification=True,
            )

        event = candidates[0]
        try:
            if command.action == Action.DELETE_EVENT:
                self.events.set_status(event, EventStatus.CANCELLED, commit=False)
                reply = f"Событие «{event.title}» удалено."
                referenced_id = None
            elif command.action == Action.COMPLETE_EVENT:
                if event.type == EventType.MEETING.value:
                    return AssistantResponse(
                        reply="Встречу нельзя отметить выполненной. Её можно удалить или перенести.",
                        action=command.action.value,
                        transcript=command.transcript,
                    )
                self.events.set_status(event, EventStatus.COMPLETED, commit=False)
                if event.type == EventType.REMINDER.value:
                    reply = f"Напоминание «{event.title}» отмечено выполненным."
                else:
                    reply = f"Задача «{event.title}» отмечена выполненной."
                referenced_id = event.id
            else:
                event = self._apply_event_update(event, command)
                reply = f"Событие «{event.title}» изменено."
                referenced_id = event.id
            self.dialogue.clear_pending(session_id, commit=False)
            self.dialogue.set_last_referenced_event_id(
                session_id, referenced_id, commit=False
            )
            self.db.commit()
            self.db.refresh(event)
        except MeetingConflictError as exc:
            self.db.rollback()
            return self._conflict_response(session_id, command, exc.conflicts)
        except ValueError as exc:
            self.db.rollback()
            missing_field = (
                "update_details"
                if str(exc).startswith("Укажите новое название")
                else "future_datetime"
            )
            pending = command.model_copy(
                update={
                    "target_event_id": event.id,
                    "missing_fields": [missing_field],
                    "clarification_question": str(exc),
                }
            )
            self.dialogue.set_pending(session_id, pending)
            return AssistantResponse(
                reply=str(exc),
                action=command.action.value,
                transcript=command.transcript,
                needs_clarification=True,
            )
        except Exception:
            self.db.rollback()
            raise

        return AssistantResponse(
            reply=reply,
            action=command.action.value,
            transcript=command.transcript,
            created_event=EventResponse.model_validate(event),
        )

    def _resolve_target_events(self, session_id: str, command: AssistantCommand):
        if command.target_event_id:
            event = self.events.get_owned(session_id, command.target_event_id)
            candidates = [event] if event and event.status != EventStatus.CANCELLED.value else []
        elif command.candidate_event_ids:
            candidates = [
                event
                for event_id in command.candidate_event_ids
                if (event := self.events.get_owned(session_id, event_id))
                and event.status != EventStatus.CANCELLED.value
            ]
            if command.action in {
                Action.UPDATE_EVENT,
                Action.DELETE_EVENT,
                Action.COMPLETE_EVENT,
            } and command.target_query:
                matching = self.events.search_events(
                    session_id,
                    command.target_query,
                    event_type=command.event_type,
                    limit=5,
                )
                matching_ids = {event.id for event in matching}
                if matching_ids:
                    candidates = [event for event in candidates if event.id in matching_ids]
        elif self._has_reference_pronoun(command.transcript):
            event_id = self.dialogue.get_last_referenced_event_id(session_id)
            event = self.events.get_owned(session_id, event_id) if event_id else None
            candidates = [event] if event and event.status != EventStatus.CANCELLED.value else []
        else:
            query = command.target_query or command.query or command.title or ""
            candidates = self.events.search_events(
                session_id, query, event_type=command.event_type, limit=5
            )
        # A date in a delete/complete request identifies the target event; do not
        # force the user to choose a number between same-named events on other dates.
        if command.action in {Action.DELETE_EVENT, Action.COMPLETE_EVENT}:
            parsed = parse_datetime_parts(command.transcript, self._now())
            if parsed.has_explicit_date and parsed.date_value:
                candidates = [
                    event
                    for event in candidates
                    if self._event_matches_datetime(event, parsed.date_value, parsed.time_value)
                ]
        return candidates

    @staticmethod
    def _event_matches_datetime(event, expected_date, expected_time=None):
        value = event.starts_at or event.due_at or event.due_date
        if not value:
            return False
        event_datetime = datetime.fromisoformat(value)
        if event_datetime.date() != expected_date:
            return False
        return expected_time is None or event_datetime.time().replace(
            second=0, microsecond=0
        ) == expected_time

    def _apply_event_update(self, event, command: AssistantCommand):
        parsed = parse_datetime_parts(command.transcript, self._now())
        title = command.new_title
        if title is None and not parsed.has_explicit_date and not parsed.has_explicit_time:
            raise ValueError("Укажите новое название, дату или время события")
        if event.type == EventType.MEETING.value:
            current_start = datetime.fromisoformat(event.starts_at)
            date_value = parsed.date_value if parsed.has_explicit_date else current_start.date()
            time_value = parsed.time_value if parsed.has_explicit_time else current_start.time()
            starts_at = combine(date_value, time_value, self._now())
            if starts_at <= self._now():
                raise ValueError("Нельзя перенести встречу в прошлое")
            duration = (
                datetime.fromisoformat(event.ends_at) - current_start
                if event.ends_at
                else None
            )
            ends_at = starts_at + (duration or timedelta(hours=1))
            if not command.conflict_confirmed:
                conflicts = self.events.find_meeting_conflicts(
                    event.owner_session_id,
                    starts_at,
                    ends_at,
                    exclude_event_id=event.id,
                )
                if conflicts:
                    raise MeetingConflictError(conflicts)
            return self.events.update_event(
                event,
                title=title,
                starts_at=starts_at,
                ends_at=ends_at,
                raw_text=command.transcript,
                commit=False,
            )

        if not parsed.has_explicit_date and not parsed.has_explicit_time:
            return self.events.update_event(
                event,
                title=title,
                raw_text=command.transcript,
                commit=False,
            )
        current_date = None
        if event.due_date:
            current_date = datetime.fromisoformat(event.due_date).date()
        elif event.due_at:
            current_date = datetime.fromisoformat(event.due_at).date()
        if current_date is None and not parsed.has_explicit_date:
            raise ValueError("Укажите дату задачи вместе со временем")
        current_time = (
            datetime.fromisoformat(event.due_at).time() if event.due_at else None
        )
        date_value = parsed.date_value if parsed.has_explicit_date else current_date
        time_value = parsed.time_value if parsed.has_explicit_time else current_time
        due_at = combine(date_value, time_value, self._now()) if time_value else None
        if (due_at and due_at <= self._now()) or (
            not due_at and date_value < self._now().date()
        ):
            raise ValueError("Нельзя перенести задачу в прошлое")
        return self.events.update_event(
            event,
            title=title,
            due_date=date_value,
            due_at=due_at,
            raw_text=command.transcript,
            commit=False,
        )

    @staticmethod
    def _is_cancel_request(value: str) -> bool:
        return normalize_text(value) in {
            "отмена", "не надо", "забудь", "начнем заново", "ничего не создавай"
        }

    @classmethod
    def _should_cancel_pending(cls, value: str, pending: dict) -> bool:
        if cls._is_cancel_request(value):
            return True
        return (
            "conflict_confirmation" in pending.get("missing_fields", [])
            and normalize_text(value) in {"нет", "не продолжай", "не добавляй"}
        )

    @staticmethod
    def _has_target(command: AssistantCommand) -> bool:
        return bool(
            command.target_event_id
            or command.candidate_event_ids
            or command.target_query
            or command.query
            or command.title
            or AssistantService._has_reference_pronoun(command.transcript)
        )

    @staticmethod
    def _has_reference_pronoun(value: str) -> bool:
        words = set(normalize_text(value).split())
        return bool(words & {"ее", "его", "это", "последнюю", "последний", "последнее"})

    @staticmethod
    def _is_affirmative(value: str) -> bool:
        return normalize_text(value) in {
            "да", "да все равно", "все равно", "подтверждаю", "добавь все равно"
        }

    def _conflict_response(self, session_id, command, conflicts) -> AssistantResponse:
        pending = command.model_copy(
            update={
                "conflict_confirmed": False,
                "missing_fields": ["conflict_confirmation"],
                "clarification_question": (
                    "На это время уже запланирована другая встреча. Всё равно продолжить?"
                ),
            }
        )
        self.dialogue.set_pending(session_id, pending)
        return AssistantResponse(
            reply=pending.clarification_question,
            action=command.action.value,
            transcript=command.transcript,
            events=[EventResponse.model_validate(event) for event in conflicts],
            needs_clarification=True,
        )

    def _now_iso(self) -> str:
        return self._now().isoformat()

    def _now(self) -> datetime:
        if self._now_provider:
            return self._now_provider()
        return datetime.now(ZoneInfo(self.settings.default_timezone))

    def _normalize_and_log(
        self,
        command: AssistantCommand,
        pending: dict | None = None,
        source_text: str | None = None,
    ) -> AssistantCommand:
        now = self._now()
        normalized = normalize_command(command, now, source_text=source_text)
        if pending:
            pending_command = AssistantCommand.model_validate(pending)
            user_text = normalize_text(source_text or command.transcript)
            is_explicit_query = normalized.action in {
                Action.LIST_EVENTS,
                Action.SEARCH_EVENTS,
            } and (_is_list_request(user_text) or _is_search_request(user_text))
            starts_new_creation = self._starts_new_creation(user_text, normalized)
            if not is_explicit_query and not starts_new_creation:
                if pending_command.action in {
                    Action.UPDATE_EVENT,
                    Action.DELETE_EVENT,
                    Action.COMPLETE_EVENT,
                }:
                    normalized = self._merge_management_pending(
                        command, pending_command
                    )
                else:
                    normalized = self._merge_pending(
                        normalized, pending_command, now, source_text=source_text
                    )
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
    def _starts_new_creation(text: str, command: AssistantCommand) -> bool:
        if command.action not in {Action.CREATE_TASK, Action.CREATE_MEETING}:
            return False
        creation_markers = (
            "добавь", "создай", "поставь", "напомни", "назначь",
            "запланируй", "добавить", "создать", "поставить",
        )
        return any(marker in text.split() for marker in creation_markers)

    @staticmethod
    def _merge_management_pending(
        current: AssistantCommand, pending: AssistantCommand
    ) -> AssistantCommand:
        target_event_id = current.target_event_id or pending.target_event_id
        number = re.fullmatch(r"\s*(\d+)\s*[.!]?\s*", current.transcript)
        if number and pending.candidate_event_ids:
            index = int(number.group(1)) - 1
            if 0 <= index < len(pending.candidate_event_ids):
                target_event_id = pending.candidate_event_ids[index]
        retain_original_request = bool(
            set(pending.missing_fields)
            & {"target_event", "conflict_confirmation"}
        )
        transcript = pending.transcript if retain_original_request else current.transcript
        return current.model_copy(
            update={
                "action": pending.action,
                "transcript": transcript,
                "event_type": pending.event_type or current.event_type,
                "target_event_id": target_event_id,
                "target_query": current.target_query or pending.target_query,
                "candidate_event_ids": (
                    [] if target_event_id else pending.candidate_event_ids
                ),
                "new_title": current.new_title or pending.new_title,
                "conflict_confirmed": (
                    pending.conflict_confirmed
                    or AssistantService._is_affirmative(current.transcript)
                ),
                "missing_fields": [],
                "clarification_question": None,
            }
        )

    @staticmethod
    def _merge_pending(
        current: AssistantCommand,
        pending: AssistantCommand,
        now: datetime,
        source_text: str | None = None,
    ) -> AssistantCommand:
        answer_text = source_text or current.transcript
        parsed = parse_datetime_parts(answer_text, now)
        explicit_type = detect_event_type(answer_text)
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
            "conflict_confirmed": (
                pending.conflict_confirmed
                or AssistantService._is_affirmative(current.transcript)
            ),
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
