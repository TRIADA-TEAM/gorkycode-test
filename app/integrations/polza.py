import asyncio
import logging
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings


logger = logging.getLogger(__name__)


class TranscriptionError(RuntimeError):
    def __init__(self, message: str, status_code: int = 503) -> None:
        super().__init__(message)
        self.status_code = status_code


class PolzaTranscriptionClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def transcribe(
        self, filename: str, content: bytes, content_type: str
    ) -> str:
        if not self.settings.polza_api_key:
            raise TranscriptionError("Распознавание речи не настроено")

        safe_filename = Path(filename).name or "recording.webm"
        response: httpx.Response | None = None
        attempts = max(1, self.settings.polza_stt_max_retries)
        for attempt in range(attempts):
            try:
                response = await self._post(
                    files={"file": (safe_filename, content, content_type)},
                    data={
                        "model": self.settings.polza_stt_model,
                        "language": self.settings.polza_stt_language,
                    },
                )
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt + 1 < attempts:
                        await asyncio.sleep(0.25 * (2**attempt))
                        continue
                response.raise_for_status()
                return self._parse_transcript(response)
            except httpx.TimeoutException as exc:
                if attempt + 1 < attempts:
                    await asyncio.sleep(0.25 * (2**attempt))
                    continue
                raise TranscriptionError(
                    "Сервис распознавания не ответил вовремя. Попробуйте ещё раз"
                ) from exc
            except httpx.HTTPStatusError as exc:
                raise self._status_error(exc.response.status_code) from exc
            except httpx.HTTPError as exc:
                if attempt + 1 < attempts:
                    await asyncio.sleep(0.25 * (2**attempt))
                    continue
                raise TranscriptionError(
                    "Не удалось распознать речь. Попробуйте ещё раз"
                ) from exc

        status = response.status_code if response is not None else 503
        raise self._status_error(status)

    async def _post(self, **kwargs: Any) -> httpx.Response:
        url = f"{self.settings.polza_api_url.rstrip('/')}/audio/transcriptions"
        headers = {"Authorization": f"Bearer {self.settings.polza_api_key}"}
        async with httpx.AsyncClient(timeout=self.settings.polza_stt_timeout) as client:
            return await client.post(url, headers=headers, **kwargs)

    @staticmethod
    def _parse_transcript(response: httpx.Response) -> str:
        try:
            text = response.json()["text"].strip()
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise TranscriptionError(
                "Сервис распознавания вернул некорректный ответ"
            ) from exc
        if not text:
            raise TranscriptionError(
                "В записи не удалось обнаружить речь", status_code=422
            )
        return text

    @staticmethod
    def _status_error(status_code: int) -> TranscriptionError:
        if status_code == 400:
            return TranscriptionError(
                "Не удалось обработать аудиозапись", status_code=422
            )
        if status_code == 401:
            return TranscriptionError(
                "Не удалось авторизоваться в сервисе распознавания речи"
            )
        if status_code == 402:
            return TranscriptionError(
                "На балансе сервиса распознавания недостаточно средств"
            )
        if status_code == 413:
            return TranscriptionError("Аудиофайл слишком большой", status_code=413)
        if status_code == 429:
            return TranscriptionError("Сервис распознавания временно перегружен")
        return TranscriptionError("Не удалось распознать речь. Попробуйте ещё раз")
