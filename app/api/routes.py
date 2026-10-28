import logging
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.dependencies import get_session_id
from app.config import get_settings
from app.db.session import get_db
from app.domain.commands import (
    Action,
    AssistantCommand,
    AssistantResponse,
    EventEditRequest,
    EventResponse,
    EventStatus,
    EventType,
    TextRequest,
)
from app.integrations.gigachat import GigaChatClient, GigaChatError
from app.integrations.polza import PolzaTranscriptionClient, TranscriptionError
from app.services.assistant import AssistantService
from app.services.events import EventService

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)
ALLOWED_AUDIO_TYPES = {
    "audio/webm", "audio/ogg", "audio/opus", "audio/mpeg", "audio/mp3",
    "audio/mp4", "audio/x-m4a", "audio/wav", "audio/x-wav",
}


def assistant_service(db: Session) -> AssistantService:
    settings = get_settings()
    return AssistantService(
        db,
        settings,
        GigaChatClient(settings),
        transcription_client=PolzaTranscriptionClient(settings),
    )


@router.get("/health")
async def health(db: Annotated[Session, Depends(get_db)]) -> dict[str, str]:
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@router.post("/assistant/text", response_model=AssistantResponse)
async def assistant_text(
    body: TextRequest,
    session_id: Annotated[str, Depends(get_session_id)],
    db: Annotated[Session, Depends(get_db)],
) -> AssistantResponse:
    try:
        return await assistant_service(db).handle_text(session_id, body.text)
    except GigaChatError as exc:
        raise HTTPException(status_code=503, detail=provider_error_message(str(exc))) from exc
    except Exception as exc:
        logger.exception("Unhandled text assistant error")
        raise HTTPException(status_code=500, detail="Внутренняя ошибка обработки запроса") from exc


@router.post("/assistant/audio", response_model=AssistantResponse)
async def assistant_audio(
    session_id: Annotated[str, Depends(get_session_id)],
    db: Annotated[Session, Depends(get_db)],
    audio: UploadFile = File(...),
) -> AssistantResponse:
    settings = get_settings()
    content_type = (audio.content_type or "").split(";", 1)[0]
    if content_type not in ALLOWED_AUDIO_TYPES:
        raise HTTPException(status_code=415, detail="Неподдерживаемый формат аудио")
    content = await audio.read(settings.max_audio_bytes + 1)
    if len(content) > settings.max_audio_bytes:
        raise HTTPException(status_code=413, detail="Аудиофайл слишком большой")
    if not content:
        raise HTTPException(status_code=400, detail="Аудиофайл пуст")
    try:
        return await assistant_service(db).handle_audio(
            session_id, audio.filename or "recording.webm", content, content_type
        )
    except GigaChatError as exc:
        raise HTTPException(status_code=503, detail=provider_error_message(str(exc))) from exc
    except TranscriptionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Unhandled audio assistant error")
        raise HTTPException(status_code=500, detail="Внутренняя ошибка обработки запроса") from exc


def provider_error_message(detail: str) -> str:
    if "не настроен" in detail or "авторизоваться" in detail:
        return detail
    return "Сервис временно недоступен. Попробуйте повторить запрос через несколько секунд."


@router.get("/events", response_model=list[EventResponse])
async def events(
    session_id: Annotated[str, Depends(get_session_id)],
    db: Annotated[Session, Depends(get_db)],
    event_type: EventType | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
) -> list[EventResponse]:
    items = EventService(db, get_settings().default_timezone).list_events(
        session_id, event_type=event_type, limit=limit
    )
    return [EventResponse.model_validate(item) for item in items]


def editable_event(db: Session, session_id: str, event_id: int):
    item = EventService(db, get_settings().default_timezone).get_owned(
        session_id, event_id
    )
    if item is None or item.status == EventStatus.CANCELLED.value:
        raise HTTPException(status_code=404, detail="Событие не найдено")
    return item


@router.patch("/events/{event_id}", response_model=AssistantResponse)
async def edit_event(
    event_id: int,
    body: EventEditRequest,
    session_id: Annotated[str, Depends(get_session_id)],
    db: Annotated[Session, Depends(get_db)],
) -> AssistantResponse:
    item = editable_event(db, session_id, event_id)
    parts = ["Измени событие"]
    if body.event_date:
        parts.append(body.event_date.strftime("на %d.%m.%Y"))
    if body.event_time:
        parts.append(body.event_time.strftime("в %H:%M"))
    return assistant_service(db).execute(
        session_id,
        AssistantCommand(
            action=Action.UPDATE_EVENT,
            transcript=" ".join(parts),
            event_type=EventType(item.type),
            target_event_id=event_id,
            new_title=body.title,
            clear_time=body.clear_time,
        ),
        "user_text",
    )


@router.delete("/events/{event_id}", response_model=AssistantResponse)
async def delete_event(
    event_id: int,
    session_id: Annotated[str, Depends(get_session_id)],
    db: Annotated[Session, Depends(get_db)],
) -> AssistantResponse:
    item = editable_event(db, session_id, event_id)
    return assistant_service(db).execute(
        session_id,
        AssistantCommand(
            action=Action.DELETE_EVENT,
            transcript=f"Удали событие {item.title}",
            event_type=EventType(item.type),
            target_event_id=event_id,
        ),
        "user_text",
    )


@router.post("/events/{event_id}/complete", response_model=AssistantResponse)
async def complete_event(
    event_id: int,
    session_id: Annotated[str, Depends(get_session_id)],
    db: Annotated[Session, Depends(get_db)],
) -> AssistantResponse:
    item = editable_event(db, session_id, event_id)
    return assistant_service(db).execute(
        session_id,
        AssistantCommand(
            action=Action.COMPLETE_EVENT,
            transcript=f"Отметь выполненным {item.title}",
            event_type=EventType(item.type),
            target_event_id=event_id,
        ),
        "user_text",
    )
