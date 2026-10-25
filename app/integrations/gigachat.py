import asyncio
import json
import logging
import re
import time
import uuid
from typing import Any

import httpx

from app.config import Settings
from app.domain.commands import AssistantCommand


logger = logging.getLogger(__name__)

COMMAND_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "action": {
            "type": "string",
            "enum": [
                "create_task", "create_meeting", "list_events", "search_events",
                "update_event", "delete_event", "complete_event", "cancel_action",
                "clarify",
            ],
        },
        "transcript": {"type": "string"},
        "title": {"type": ["string", "null"]},
        "event_type": {"type": ["string", "null"], "enum": ["task", "reminder", "meeting", None]},
        "starts_at": {"type": ["string", "null"], "format": "date-time"},
        "ends_at": {"type": ["string", "null"], "format": "date-time"},
        "due_at": {"type": ["string", "null"], "format": "date-time"},
        "due_date": {"type": ["string", "null"], "format": "date"},
        "date_from": {"type": ["string", "null"], "format": "date-time"},
        "date_to": {"type": ["string", "null"], "format": "date-time"},
        "query": {"type": ["string", "null"]},
        "target_event_id": {"type": ["integer", "null"]},
        "target_query": {"type": ["string", "null"]},
        "new_title": {"type": ["string", "null"]},
        "candidate_event_ids": {"type": "array", "items": {"type": "integer"}},
        "conflict_confirmed": {"type": "boolean"},
        "missing_fields": {"type": "array", "items": {"type": "string"}},
        "clarification_question": {"type": ["string", "null"]},
    },
    "required": [
        "action", "transcript", "title", "event_type", "starts_at", "ends_at",
        "due_at", "date_from", "date_to", "query", "missing_fields",
        "clarification_question", "due_date", "target_event_id", "target_query",
        "new_title", "candidate_event_ids", "conflict_confirmed",
    ],
}


class GigaChatError(RuntimeError):
    pass


class GigaChatClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._token: str | None = None
        self._token_expires_at = 0.0

    async def interpret(
        self,
        *,
        text: str,
        now_iso: str,
        pending_action: dict | None = None,
    ) -> AssistantCommand:
        system = self._system_prompt(now_iso, pending_action)
        user_message: dict[str, Any] = {"role": "user", "content": text}
        payload = {
            "model": self.settings.gigachat_model,
            "messages": [
                {"role": "system", "content": system},
                user_message,
            ],
            "temperature": 0.0,
            "response_format": {
                "type": "json_schema",
                "schema": COMMAND_SCHEMA,
                "strict": True,
            },
        }
        data = await self._request("POST", "/chat/completions", json=payload)
        try:
            content = data["choices"][0]["message"]["content"]
            command = AssistantCommand.model_validate(json.loads(content))
            logger.info(
                "gigachat_command action=%s type=%s due_at=%s starts_at=%s "
                "date_from=%s date_to=%s missing=%s",
                command.action.value,
                command.event_type.value if command.event_type else None,
                command.due_at,
                command.starts_at,
                command.date_from,
                command.date_to,
                command.missing_fields,
            )
            return command
        except (KeyError, IndexError, TypeError, json.JSONDecodeError, ValueError) as exc:
            if pending_action is not None:
                logger.warning(
                    "gigachat_invalid_pending_response error=%s",
                    type(exc).__name__,
                )
                # Preserve the unfinished operation and let the deterministic
                # application parser extract date/time from the latest answer.
                contextual = dict(pending_action)
                contextual["transcript"] = text
                return AssistantCommand.model_validate(contextual)
            if pending_action is None and not _looks_like_event_request(text):
                logger.warning(
                    "gigachat_invalid_offtopic_response error=%s",
                    type(exc).__name__,
                )
                return AssistantCommand(
                    action="clarify",
                    transcript=text,
                    clarification_question=(
                        "Я могу создавать задачи, напоминания и встречи, а также "
                        "показывать или искать события."
                    ),
                )
            raise GigaChatError("GigaChat вернул ответ в неожиданном формате") from exc

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if not self.settings.gigachat_auth_key:
            raise GigaChatError("GIGACHAT_AUTH_KEY не настроен")
        token = await self._access_token()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"
        url = f"{self.settings.gigachat_api_url.rstrip('/')}{path}"
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(
                    timeout=45.0, verify=self.settings.gigachat_verify_ssl
                ) as client:
                    response = await client.request(method, url, headers=headers, **kwargs)
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                if attempt == 0:
                    await asyncio.sleep(0.25)
        detail = self._http_error_detail(last_error)
        raise GigaChatError(f"Не удалось выполнить запрос к GigaChat{detail}") from last_error

    async def _access_token(self) -> str:
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token
        headers = {
            "Authorization": f"Basic {self.settings.gigachat_auth_key}",
            "RqUID": str(uuid.uuid4()),
            "Content-Type": "application/x-www-form-urlencoded",
        }
        try:
            async with httpx.AsyncClient(
                timeout=20.0, verify=self.settings.gigachat_verify_ssl
            ) as client:
                response = await client.post(
                    self.settings.gigachat_oauth_url,
                    headers=headers,
                    data={"scope": self.settings.gigachat_scope},
                )
            response.raise_for_status()
            payload = response.json()
            self._token = str(payload["access_token"])
            self._token_expires_at = time.monotonic() + 25 * 60
            return self._token
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            detail = self._http_error_detail(exc)
            raise GigaChatError(f"Не удалось авторизоваться в GigaChat{detail}") from exc

    @staticmethod
    def _http_error_detail(error: Exception | None) -> str:
        if not isinstance(error, httpx.HTTPStatusError):
            return ""
        response = error.response
        try:
            message = response.json().get("message")
        except (ValueError, AttributeError):
            message = None
        suffix = f": {response.status_code}"
        return f"{suffix} {message}" if message else suffix

    def _system_prompt(self, now_iso: str, pending_action: dict | None) -> str:
        pending = json.dumps(pending_action, ensure_ascii=False) if pending_action else "нет"
        return f"""
Ты преобразуешь русские запросы к календарю в одну строгую JSON-команду.
Текущее время: {now_iso}. Часовой пояс: {self.settings.default_timezone}.
Неделя начинается в понедельник. Названный день недели означает ближайшее его
наступление после текущей даты; для «на этой неделе» используй понедельник—воскресенье
текущей календарной недели. Не меняй явно названный пользователем тип события.
Для задачи обязательна дата, но время необязательно. Для напоминания и встречи
обязательны и дата, и время. Если содержательного названия нет, используй
«Задача», «Напоминание» или «Встреча».
Незавершенная команда текущего диалога: {pending}.

Разрешенные действия: create_task, create_meeting, list_events, search_events,
update_event, delete_event, complete_event, cancel_action, clarify.
Если сообщение не относится к задачам, напоминаниям, встречам, списку или поиску
событий, верни action=clarify и коротко сообщи, с чем умеет работать ассистент.
Если текущая фраза отвечает на уточнение, объедини ее с незавершенной командой.
Для встречи используй start_date/start_time в missing_fields, для задачи и
напоминания — due_date/due_time. Если текущий ответ содержит только время, не
подставляй сегодняшнюю дату: сохрани время и оставь поле даты недостающим.
Не выдумывай дату или время.
Если у задачи есть только дата, заполни due_date и оставь due_at равным null.
Не выдумывай обязательные данные: перечисли их в
missing_fields и сформулируй один короткий clarification_question. В таком
случае сохрани исходное целевое action, а не меняй его на clarify.
Для запросов списка укажи date_from/date_to. Для поиска укажи query.
Для изменения, удаления или завершения укажи target_query — краткое описание
целевого события. Для переименования заполни new_title. Не помещай новое
название в title. Фразы «перенеси», «измени», «переименуй» означают
update_event; «удали», «отмени встречу» — delete_event; «выполнено», «заверши»,
«отметь выполненной» — complete_event. Короткая отмена незавершенного действия
означает cancel_action. Местоимения «ее», «его», «это» оставляй в target_query:
backend разрешит ссылку через last_referenced_event_id.
Все даты верни в ISO-8601 с часовым поясом. transcript должен содержать точный
распознанный текст пользователя; для аудио сначала транскрибируй запись.
Верни только объект, соответствующий переданной JSON Schema.
""".strip()


def _looks_like_event_request(value: str) -> bool:
    text = value.lower().replace("ё", "е")
    markers = (
        "задач", "напом", "встреч", "событ", "созвон", "совещан", "переговор",
        "запланирован", "план", "найди", "покажи", "сегодня", "завтра",
        "послезавтра", "понедельник", "вторник", "сред", "четверг", "пятниц",
        "суббот", "воскресень",
    )
    return any(marker in text for marker in markers) or bool(
        re.search(r"\b\d{1,2}(?::\d{2}|[./-]\d{1,2})\b", text)
    )
