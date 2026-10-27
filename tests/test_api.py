from datetime import datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.config import Settings
from app.db.session import get_db
from app.domain.commands import Action, AssistantCommand, EventType
from app.main import app
from app.integrations.polza import TranscriptionError
from app.services.assistant import AssistantService
from app.services.events import EventService


class ScriptedClient:
    def __init__(self, commands: list[AssistantCommand]) -> None:
        self.commands = iter(commands)

    async def interpret(self, **_kwargs) -> AssistantCommand:
        return next(self.commands)


class StaticTranscriber:
    async def transcribe(self, _filename, _content, _content_type) -> str:
        return "Добавь задачу купить молоко завтра"


def test_text_endpoint_preserves_clarification_between_requests(db, monkeypatch):
    session_id = str(uuid4())
    model = ScriptedClient(
        [
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
                transcript="В 15:00",
                title=None,
                event_type=EventType.MEETING,
                starts_at=datetime.fromisoformat("2026-10-15T15:00:00+03:00"),
                missing_fields=["title"],
                clarification_question="Что за встреча?",
            ),
        ]
    )

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(
        routes,
        "assistant_service",
        lambda request_db: AssistantService(
            request_db,
            Settings(),
            model,
            lambda: datetime.fromisoformat("2026-10-15T12:00:00+03:00"),
        ),
    )
    try:
        with TestClient(app) as client:
            first = client.post(
                "/api/assistant/text",
                headers={"X-Session-ID": session_id},
                json={"text": "Добавь встречу с Иваном 20 октября"},
            )
            second = client.post(
                "/api/assistant/text",
                headers={"X-Session-ID": session_id},
                json={"text": "В 15:00"},
            )
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == 200
    assert first.json()["needs_clarification"] is True
    assert second.status_code == 200
    assert second.json()["needs_clarification"] is False
    assert second.json()["created_event"]["title"] == "Встреча с Иваном"
    assert second.json()["created_event"]["starts_at"] == "2026-10-20T15:00:00+03:00"


def test_text_endpoint_rejects_invalid_session_id():
    with TestClient(app) as client:
        response = client.post(
            "/api/assistant/text",
            headers={"X-Session-ID": "not-a-uuid"},
            json={"text": "Что запланировано завтра?"},
        )

    assert response.status_code == 400


def test_audio_endpoint_transcribes_and_creates_event(db, monkeypatch):
    session_id = str(uuid4())
    model = ScriptedClient(
        [
            AssistantCommand(
                action=Action.CREATE_TASK,
                transcript="Добавь задачу купить молоко завтра",
                title="Купить молоко",
                event_type=EventType.TASK,
            )
        ]
    )

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(
        routes,
        "assistant_service",
        lambda request_db: AssistantService(
            request_db,
            Settings(),
            model,
            lambda: datetime.fromisoformat("2026-10-15T12:00:00+03:00"),
            transcription_client=StaticTranscriber(),
        ),
    )
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/assistant/audio",
                headers={"X-Session-ID": session_id},
                files={"audio": ("recording.webm", b"audio", "audio/webm")},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["transcript"] == "Добавь задачу купить молоко завтра"
    assert response.json()["created_event"]["title"] == "Купить молоко"


def test_audio_endpoint_returns_transcription_error(monkeypatch):
    class FailingService:
        async def handle_audio(self, *_args):
            raise TranscriptionError("В записи не удалось обнаружить речь", 422)

    monkeypatch.setattr(routes, "assistant_service", lambda _db: FailingService())

    with TestClient(app) as client:
        response = client.post(
            "/api/assistant/audio",
            headers={"X-Session-ID": str(uuid4())},
            files={"audio": ("recording.webm", b"audio", "audio/webm")},
        )

    assert response.status_code == 422
    assert response.json()["detail"] == "В записи не удалось обнаружить речь"


def test_event_can_be_edited_without_model_interpretation(db, monkeypatch):
    session_id = str(uuid4())
    created = EventService(db).create_meeting(
        session_id,
        AssistantCommand(
            action=Action.CREATE_MEETING,
            transcript="Добавь встречу с Иваном 5 ноября в 15:00",
            title="Встреча с Иваном",
            event_type=EventType.MEETING,
            starts_at=datetime.fromisoformat("2026-11-05T15:00:00+03:00"),
        ),
        "user_text",
    )

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(
        routes,
        "assistant_service",
        lambda request_db: AssistantService(
            request_db,
            Settings(),
            ScriptedClient([]),
            lambda: datetime.fromisoformat("2026-10-28T12:00:00+03:00"),
        ),
    )
    try:
        with TestClient(app) as client:
            response = client.patch(
                f"/api/events/{created.id}",
                headers={"X-Session-ID": session_id},
                json={
                    "title": "Кино с Иваном",
                    "event_date": "2026-11-06",
                    "event_time": "19:30",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["created_event"]["title"] == "Кино с Иваном"
    assert response.json()["created_event"]["starts_at"] == "2026-11-06T19:30:00+03:00"


@pytest.mark.parametrize(
    ("method", "suffix", "json_body"),
    [
        ("PATCH", "", {"title": "Чужое название"}),
        ("DELETE", "", None),
        ("POST", "/complete", None),
    ],
)
def test_event_controls_cannot_modify_another_session(
    db, method, suffix, json_body
):
    owner = str(uuid4())
    created = EventService(db).create_task(
        owner,
        AssistantCommand(
            action=Action.CREATE_TASK,
            transcript="Добавь задачу",
            title="Личная задача",
            event_type=EventType.TASK,
            due_date=datetime.fromisoformat("2026-11-05").date(),
        ),
        "user_text",
    )

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            response = client.request(
                method,
                f"/api/events/{created.id}{suffix}",
                headers={"X-Session-ID": str(uuid4())},
                json=json_body,
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert EventService(db).get_owned(owner, created.id).status == "pending"


def test_event_controls_update_complete_and_delete_without_model(db, monkeypatch):
    session_id = str(uuid4())
    events = EventService(db)
    task = events.create_task(
        session_id,
        AssistantCommand(
            action=Action.CREATE_TASK,
            transcript="Добавь задачу подготовить отчет 5 ноября",
            title="Подготовить отчет",
            event_type=EventType.TASK,
            due_date=datetime.fromisoformat("2026-11-05").date(),
        ),
        "user_text",
    )
    meeting = events.create_meeting(
        session_id,
        AssistantCommand(
            action=Action.CREATE_MEETING,
            transcript="Добавь встречу 6 ноября в 15:00",
            title="Встреча с командой",
            event_type=EventType.MEETING,
            starts_at=datetime.fromisoformat("2026-11-06T15:00:00+03:00"),
        ),
        "user_text",
    )

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(
        routes,
        "assistant_service",
        lambda request_db: AssistantService(
            request_db,
            Settings(),
            ScriptedClient([]),
            lambda: datetime.fromisoformat("2026-10-28T12:00:00+03:00"),
        ),
    )
    try:
        with TestClient(app) as client:
            renamed = client.patch(
                f"/api/events/{task.id}",
                headers={"X-Session-ID": session_id},
                json={"title": "Подготовить итоговый отчет"},
            )
            completed = client.post(
                f"/api/events/{task.id}/complete",
                headers={"X-Session-ID": session_id},
            )
            deleted = client.delete(
                f"/api/events/{meeting.id}",
                headers={"X-Session-ID": session_id},
            )
            visible = client.get(
                "/api/events", headers={"X-Session-ID": session_id}
            )
    finally:
        app.dependency_overrides.clear()

    assert renamed.status_code == 200
    assert renamed.json()["created_event"]["title"] == "Подготовить итоговый отчет"
    assert completed.json()["created_event"]["status"] == "completed"
    assert deleted.json()["created_event"]["status"] == "cancelled"
    assert visible.json() == []


def test_event_edit_rejects_empty_body():
    with TestClient(app) as client:
        response = client.patch(
            "/api/events/1",
            headers={"X-Session-ID": str(uuid4())},
            json={},
        )

    assert response.status_code == 422
