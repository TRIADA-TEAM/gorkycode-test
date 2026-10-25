import asyncio

import httpx
import pytest

from app.config import Settings
from app.integrations.polza import PolzaTranscriptionClient, TranscriptionError


def response(status: int, payload: dict | None = None) -> httpx.Response:
    return httpx.Response(
        status,
        json=payload or {},
        request=httpx.Request("POST", "https://polza.ai/api/v1/audio/transcriptions"),
    )


def test_transcribe_sends_model_language_and_safe_filename():
    client = PolzaTranscriptionClient(
        Settings(polza_api_key="pza_test", polza_stt_max_retries=1)
    )
    captured = {}

    async def fake_post(**kwargs):
        captured.update(kwargs)
        return response(200, {"text": "  Добавь задачу завтра  "})

    client._post = fake_post
    result = asyncio.run(
        client.transcribe("../../recording.webm", b"audio", "audio/webm")
    )

    assert result == "Добавь задачу завтра"
    assert captured["data"] == {
        "model": "openai/whisper-large-v3-turbo",
        "language": "ru",
    }
    assert captured["files"]["file"] == ("recording.webm", b"audio", "audio/webm")


def test_empty_transcript_is_a_validation_error():
    client = PolzaTranscriptionClient(
        Settings(polza_api_key="pza_test", polza_stt_max_retries=1)
    )

    async def fake_post(**_kwargs):
        return response(200, {"text": "   "})

    client._post = fake_post

    with pytest.raises(TranscriptionError) as error:
        asyncio.run(client.transcribe("recording.webm", b"audio", "audio/webm"))

    assert error.value.status_code == 422
    assert "не удалось обнаружить речь" in str(error.value)


@pytest.mark.parametrize(
    ("status", "expected_status", "message"),
    [
        (400, 422, "Не удалось обработать"),
        (401, 503, "авторизоваться"),
        (402, 503, "недостаточно средств"),
        (413, 413, "слишком большой"),
        (429, 503, "перегружен"),
        (500, 503, "Не удалось распознать"),
    ],
)
def test_http_errors_are_mapped(status, expected_status, message):
    client = PolzaTranscriptionClient(
        Settings(polza_api_key="pza_test", polza_stt_max_retries=1)
    )

    async def fake_post(**_kwargs):
        return response(status)

    client._post = fake_post

    with pytest.raises(TranscriptionError) as error:
        asyncio.run(client.transcribe("recording.webm", b"audio", "audio/webm"))

    assert error.value.status_code == expected_status
    assert message in str(error.value)


def test_temporary_error_is_retried(monkeypatch):
    client = PolzaTranscriptionClient(
        Settings(polza_api_key="pza_test", polza_stt_max_retries=3)
    )
    replies = iter([response(503), response(200, {"text": "Готово"})])
    calls = 0

    async def fake_post(**_kwargs):
        nonlocal calls
        calls += 1
        return next(replies)

    async def no_sleep(_delay):
        return None

    client._post = fake_post
    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    assert asyncio.run(
        client.transcribe("recording.webm", b"audio", "audio/webm")
    ) == "Готово"
    assert calls == 2


def test_missing_key_fails_before_request():
    client = PolzaTranscriptionClient(Settings(polza_api_key=""))

    with pytest.raises(TranscriptionError, match="не настроено"):
        asyncio.run(client.transcribe("recording.webm", b"audio", "audio/webm"))
