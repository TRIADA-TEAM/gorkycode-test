from datetime import datetime
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api import routes
from app.config import Settings
from app.db.session import get_db
from app.domain.commands import Action, AssistantCommand, EventType
from app.main import app
from app.integrations.polza import TranscriptionError
from app.services.assistant import AssistantService


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
