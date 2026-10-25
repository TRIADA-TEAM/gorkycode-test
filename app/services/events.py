import json
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.db.base import DialogueSession, Event
from app.domain.commands import AssistantCommand, EventType
from app.services.text import extract_tags, normalize_text, word_stem


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


class EventService:
    def __init__(self, db: Session, timezone: str = "Europe/Moscow") -> None:
        self.db = db
        self.timezone = timezone

    def create_task(
        self,
        session_id: str,
        command: AssistantCommand,
        source: str,
        *,
        commit: bool = True,
    ) -> Event:
        event_type = command.event_type or EventType.TASK
        due_date = command.due_date or (command.due_at.date() if command.due_at else None)
        event = self._make_event(
            session_id=session_id,
            source=source,
            event_type=event_type.value,
            title=command.title or "",
            due_at=iso(command.due_at),
            due_date=due_date.isoformat() if due_date else None,
            raw_text=command.transcript,
        )
        return self._save(event, commit=commit)

    def create_meeting(
        self,
        session_id: str,
        command: AssistantCommand,
        source: str,
        *,
        commit: bool = True,
    ) -> Event:
        starts_at = command.starts_at
        ends_at = command.ends_at or (starts_at + timedelta(hours=1) if starts_at else None)
        event = self._make_event(
            session_id=session_id,
            source=source,
            event_type=EventType.MEETING.value,
            title=command.title or "",
            starts_at=iso(starts_at),
            ends_at=iso(ends_at),
            raw_text=command.transcript,
        )
        return self._save(event, commit=commit)

    def list_events(
        self,
        session_id: str,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        event_type: EventType | None = None,
        limit: int = 50,
    ) -> list[Event]:
        moment = Event.starts_at
        fallback_moment = Event.due_at
        date_only = Event.due_date
        conditions = [Event.owner_session_id == session_id]
        if event_type:
            conditions.append(Event.type == event_type.value)
        event_date = func.substr(func.coalesce(moment, fallback_moment, date_only), 1, 10)
        if date_from:
            conditions.append(event_date >= date_from.date().isoformat())
        if date_to:
            conditions.append(event_date <= date_to.date().isoformat())
        stmt = (
            select(Event)
            .where(and_(*conditions))
            .order_by(Event.starts_at, Event.due_at, Event.due_date, Event.id)
            .limit(limit)
        )
        return list(self.db.scalars(stmt))

    def search_events(
        self,
        session_id: str,
        query: str,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        event_type: EventType | None = None,
        limit: int = 20,
    ) -> list[Event]:
        normalized = normalize_text(query)
        terms = [term for term in normalized.split() if len(term) >= 2]
        base = self.list_events(session_id, date_from, date_to, event_type, limit=1000)

        def score(event: Event) -> tuple[int, int]:
            text = event.search_text
            text_stems = {word_stem(term) for term in text.split()}
            exact = 3 if normalized and normalized in text else 0
            matched = sum(
                1 for term in terms
                if term in text or word_stem(term) in text_stems
            )
            return exact + matched, -event.id

        ranked = [event for event in base if score(event)[0] > 0]
        ranked.sort(key=score, reverse=True)
        return ranked[:limit]

    def _make_event(
        self,
        session_id: str | None,
        source: str,
        event_type: str,
        title: str,
        raw_text: str | None,
        starts_at: str | None = None,
        ends_at: str | None = None,
        due_at: str | None = None,
        due_date: str | None = None,
        source_id: str | None = None,
    ) -> Event:
        tags = extract_tags(title)
        return Event(
            source_id=source_id,
            owner_session_id=session_id,
            source=source,
            type=event_type,
            title=title,
            starts_at=starts_at,
            ends_at=ends_at,
            due_at=due_at,
            due_date=due_date,
            timezone=self.timezone,
            status="pending",
            raw_user_text=raw_text,
            tags=json.dumps(tags, ensure_ascii=False),
            search_text=normalize_text(" ".join([title, *tags])),
        )

    def _save(self, event: Event, *, commit: bool = True) -> Event:
        self.db.add(event)
        if commit:
            self.db.commit()
        else:
            self.db.flush()
        self.db.refresh(event)
        return event


class DialogueService:
    def __init__(
        self,
        db: Session,
        ttl_minutes: int = 30,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.db = db
        self.ttl = timedelta(minutes=max(0, ttl_minutes))
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    def get_or_create(
        self, session_id: str, *, commit: bool = True
    ) -> DialogueSession:
        item = self.db.get(DialogueSession, session_id)
        if item is None:
            item = DialogueSession(session_id=session_id)
            self.db.add(item)
            if commit:
                self.db.commit()
            else:
                self.db.flush()
            self.db.refresh(item)
        return item

    def get_pending(self, session_id: str) -> dict | None:
        item = self.get_or_create(session_id)
        if item.pending_action and self._is_expired(item.updated_at):
            item.pending_action = None
            self.db.commit()
            return None
        return json.loads(item.pending_action) if item.pending_action else None

    def set_pending(
        self, session_id: str, command: AssistantCommand, *, commit: bool = True
    ) -> None:
        item = self.get_or_create(session_id, commit=commit)
        item.pending_action = command.model_dump_json()
        item.updated_at = self.now_provider().astimezone(timezone.utc)
        if commit:
            self.db.commit()
        else:
            self.db.flush()

    def clear_pending(self, session_id: str, *, commit: bool = True) -> None:
        item = self.get_or_create(session_id, commit=commit)
        item.pending_action = None
        item.updated_at = self.now_provider().astimezone(timezone.utc)
        if commit:
            self.db.commit()
        else:
            self.db.flush()

    def _is_expired(self, updated_at: datetime) -> bool:
        if self.ttl.total_seconds() <= 0:
            return False
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        now = self.now_provider()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return now.astimezone(timezone.utc) - updated_at.astimezone(timezone.utc) > self.ttl
