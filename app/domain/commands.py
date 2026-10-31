from datetime import date, datetime, time
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Action(StrEnum):
    CREATE_TASK = "create_task"
    CREATE_MEETING = "create_meeting"
    LIST_EVENTS = "list_events"
    SEARCH_EVENTS = "search_events"
    UPDATE_EVENT = "update_event"
    DELETE_EVENT = "delete_event"
    COMPLETE_EVENT = "complete_event"
    CANCEL_ACTION = "cancel_action"
    CLARIFY = "clarify"


class EventType(StrEnum):
    TASK = "task"
    REMINDER = "reminder"
    MEETING = "meeting"


class EventStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class AssistantCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Action
    transcript: str = Field(min_length=1, max_length=5000)
    title: str | None = Field(default=None, max_length=500)
    event_type: EventType | None = None
    event_types: list[EventType] = Field(default_factory=list)
    statuses: list[EventStatus] = Field(default_factory=list)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    due_at: datetime | None = None
    due_date: date | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    query: str | None = Field(default=None, max_length=500)
    target_event_id: int | None = None
    target_query: str | None = Field(default=None, max_length=500)
    new_title: str | None = Field(default=None, max_length=500)
    candidate_event_ids: list[int] = Field(default_factory=list)
    conflict_confirmed: bool = False
    clear_time: bool = False
    missing_fields: list[str] = Field(default_factory=list)
    clarification_question: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_command(self) -> "AssistantCommand":
        if self.date_from and self.date_to and self.date_to < self.date_from:
            raise ValueError("date_to must not be earlier than date_from")
        # Creation requirements are checked after deterministic normalization.
        # Provider output may omit a title (the application supplies a default)
        # or forget to declare a missing date/time.
        if self.action == Action.SEARCH_EVENTS and not self.query:
            raise ValueError("query is required to search events")
        if self.action == Action.CLARIFY and not self.clarification_question:
            raise ValueError("clarification_question is required")
        return self


class TextRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000)


class EventEditRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    event_date: date | None = None
    event_time: time | None = None
    clear_time: bool = False

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Введите название")
        return value

    @model_validator(mode="after")
    def require_changes(self) -> "EventEditRequest":
        if self.title is None and self.event_date is None and self.event_time is None:
            raise ValueError("at least one event field is required")
        return self


class EventResponse(BaseModel):
    id: int
    type: str
    title: str
    starts_at: str | None
    ends_at: str | None
    due_at: str | None
    due_date: str | None
    status: str
    source: str

    model_config = ConfigDict(from_attributes=True)


class AssistantResponse(BaseModel):
    reply: str
    action: str
    transcript: str
    events: list[EventResponse] = Field(default_factory=list)
    created_event: EventResponse | None = None
    needs_clarification: bool = False
    view_mode: Literal["default", "day_schedule"] = "default"
    schedule_date: date | None = None
