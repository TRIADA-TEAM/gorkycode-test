from datetime import datetime

from app.domain.commands import Action, AssistantCommand, EventStatus, EventType
from app.services.commands import normalize_command


def test_task_moves_starts_at_to_due_at():
    moment = datetime.fromisoformat("2026-10-11T10:00:00+03:00")
    command = AssistantCommand(
        action=Action.CREATE_TASK,
        transcript="Напомни завтра в 10 позвонить Ивану",
        title="Позвонить Ивану",
        event_type=EventType.TASK,
        starts_at=moment,
    )

    result = normalize_command(command)

    assert result.event_type == EventType.REMINDER
    assert result.due_at == moment
    assert result.starts_at is None


def test_generic_list_removes_invented_event_type():
    command = AssistantCommand(
        action=Action.LIST_EVENTS,
        transcript="Что запланировано на завтра?",
        title="Список событий",
        event_type=EventType.MEETING,
        date_from=datetime.fromisoformat("2026-10-11T00:00:00+03:00"),
        date_to=datetime.fromisoformat("2026-10-11T23:59:59+03:00"),
    )

    result = normalize_command(command)

    assert result.event_type is None


def test_explicit_meeting_list_keeps_meeting_filter():
    command = AssistantCommand(
        action=Action.LIST_EVENTS,
        transcript="Какие встречи у меня завтра?",
        event_type=None,
    )
    assert normalize_command(command).event_type == EventType.MEETING


def test_nearest_weekday_is_calculated_from_application_clock():
    command = AssistantCommand(
        action=Action.CREATE_TASK,
        transcript="В пятницу нужно отправить отчет",
        title="Отправить отчет",
        event_type=EventType.TASK,
    )

    result = normalize_command(command, datetime.fromisoformat("2026-10-14T22:04:00+03:00"))

    assert result.due_date.isoformat() == "2026-10-16"
    assert result.due_at is None


def test_task_without_date_does_not_get_model_invented_deadline():
    command = AssistantCommand(
        action=Action.CREATE_TASK,
        transcript="Нужно подготовить презентацию",
        title="Подготовить презентацию",
        event_type=EventType.TASK,
        due_at=datetime.fromisoformat("2026-11-30T23:59:59+03:00"),
    )

    result = normalize_command(command, datetime.fromisoformat("2026-10-14T22:04:00+03:00"))

    assert result.due_at is None


def test_current_week_is_monday_through_sunday():
    command = AssistantCommand(
        action=Action.LIST_EVENTS,
        transcript="Что запланировано на эту неделю?",
    )

    result = normalize_command(command, datetime.fromisoformat("2026-10-14T22:04:00+03:00"))

    assert result.date_from == datetime.fromisoformat("2026-10-12T00:00:00+03:00")
    assert result.date_to == datetime.fromisoformat("2026-10-18T23:59:59+03:00")


def test_reminder_misclassified_as_meeting_is_corrected():
    command = AssistantCommand(
        action=Action.CREATE_MEETING,
        transcript="Напомни мне завтра в 10 утра позвонить Ивану",
        title="Позвонить Ивану",
        event_type=EventType.MEETING,
        starts_at=datetime.fromisoformat("2026-10-15T10:00:00+03:00"),
    )

    result = normalize_command(command, datetime.fromisoformat("2026-10-14T22:53:00+03:00"))

    assert result.action == Action.CREATE_TASK
    assert result.event_type == EventType.REMINDER
    assert result.due_at == datetime.fromisoformat("2026-10-15T10:00:00+03:00")


def test_clock_times_are_not_treated_as_missing():
    command = AssistantCommand(
        action=Action.CREATE_MEETING,
        transcript="Добавь встречу с командой завтра в 15:00",
        title="Встреча с командой",
        event_type=EventType.MEETING,
        starts_at=datetime.fromisoformat("2026-10-15T15:00:00+03:00"),
    )

    result = normalize_command(command, datetime.fromisoformat("2026-10-14T22:53:00+03:00"))

    assert result.missing_fields == []
    assert result.starts_at == datetime.fromisoformat("2026-10-15T15:00:00+03:00")


def test_relative_hours_with_russian_number_are_calculated():
    command = AssistantCommand(
        action=Action.CREATE_MEETING,
        transcript="Запланируй встречу с клиентом через два часа",
        title="Встреча с клиентом",
        event_type=EventType.MEETING,
        missing_fields=["start_time"],
    )

    result = normalize_command(command, datetime.fromisoformat("2026-10-14T22:57:00+03:00"))

    assert result.starts_at == datetime.fromisoformat("2026-10-15T00:57:00+03:00")
    assert result.missing_fields == []


def test_absolute_date_with_minutes_is_not_cleared():
    command = AssistantCommand(
        action=Action.CREATE_MEETING,
        transcript="Добавь встречу с друзьями 20 октября в 18:30",
        title="Встреча с друзьями",
        event_type=EventType.MEETING,
        starts_at=datetime.fromisoformat("2026-10-20T18:30:00+03:00"),
    )

    result = normalize_command(command, datetime.fromisoformat("2026-10-14T22:57:00+03:00"))

    assert result.starts_at == datetime.fromisoformat("2026-10-20T18:30:00+03:00")
    assert result.missing_fields == []


def test_natural_event_list_is_not_misclassified_as_search():
    command = AssistantCommand(
        action=Action.SEARCH_EVENTS,
        transcript="Какие встречи были вчера?",
        query="вчера",
    )

    result = normalize_command(command, datetime.fromisoformat("2026-10-14T22:57:00+03:00"))

    assert result.action == Action.LIST_EVENTS
    assert result.date_from == datetime.fromisoformat("2026-10-13T00:00:00+03:00")
    assert result.date_to == datetime.fromisoformat("2026-10-13T23:59:59+03:00")


def test_generic_planned_events_is_always_a_list_request():
    command = AssistantCommand(
        action=Action.CLARIFY,
        transcript="Какие запланированы события?",
        event_type=None,
        clarification_question="Уточните запрос",
    )

    result = normalize_command(
        command, datetime.fromisoformat("2026-10-15T12:00:00+03:00")
    )

    assert result.action == Action.LIST_EVENTS
    assert result.event_type is None
    assert result.missing_fields == []


def test_text_search_for_case_is_detected_even_when_provider_returns_clarify():
    command = AssistantCommand(
        action=Action.CLARIFY,
        transcript="Не JSON",
        clarification_question="Уточните запрос",
    )

    result = normalize_command(
        command,
        datetime.fromisoformat("2026-10-15T12:00:00+03:00"),
        source_text="Найди дело по материалам",
    )

    assert result.action == Action.SEARCH_EVENTS
    assert result.event_type == EventType.TASK
    assert result.query == "материалам"


def test_generic_period_uses_original_text_and_keeps_all_event_types():
    command = AssistantCommand(
        action=Action.LIST_EVENTS,
        transcript="Какие задачи у меня на следующей неделе?",
        event_type=EventType.TASK,
    )

    result = normalize_command(
        command,
        datetime.fromisoformat("2026-10-27T12:00:00+03:00"),
        source_text="Что у меня на следующей неделе?",
    )

    assert result.event_type is None
    assert result.date_from == datetime.fromisoformat("2026-11-02T00:00:00+03:00")
    assert result.date_to == datetime.fromisoformat("2026-11-08T23:59:59+03:00")


def test_morning_period_limits_time_range():
    command = AssistantCommand(
        action=Action.LIST_EVENTS,
        transcript="Какие планы на завтра утром?",
    )

    result = normalize_command(
        command,
        datetime.fromisoformat("2026-10-28T12:00:00+03:00"),
        source_text="Какие планы на завтра утром?",
    )

    assert result.date_from == datetime.fromisoformat("2026-10-29T06:00:00+03:00")
    assert result.date_to == datetime.fromisoformat("2026-10-29T12:00:00+03:00")


def test_original_reminder_intent_wins_over_provider_task_type():
    command = AssistantCommand(
        action=Action.CREATE_TASK,
        transcript="Перефразированный ответ модели",
        title="Размяться",
        event_type=EventType.TASK,
    )

    result = normalize_command(
        command,
        datetime.fromisoformat("2026-10-15T12:00:00+03:00"),
        source_text="Напомни размяться через два часа",
    )

    assert result.action == Action.CREATE_TASK
    assert result.event_type == EventType.REMINDER
    assert result.due_at == datetime.fromisoformat("2026-10-15T14:00:00+03:00")
    assert result.missing_fields == []


def test_completed_task_list_requests_completed_status():
    command = AssistantCommand(
        action=Action.LIST_EVENTS,
        transcript="Покажи выполненные задачи",
    )

    result = normalize_command(command, datetime.fromisoformat("2026-10-15T12:00:00+03:00"))

    assert result.statuses == [EventStatus.COMPLETED]


def test_invalid_clock_time_requires_correction():
    command = AssistantCommand(
        action=Action.CREATE_TASK,
        transcript="Создай задачу проверить принтер завтра в 25:61",
        title="Проверить принтер",
        event_type=EventType.TASK,
    )

    result = normalize_command(command, datetime.fromisoformat("2026-10-15T12:00:00+03:00"))

    assert result.missing_fields == ["invalid_time"]
    assert "некорректно" in result.clarification_question


def test_task_without_title_asks_for_content_before_date():
    command = AssistantCommand(
        action=Action.CREATE_TASK,
        transcript="Создай задачу без названия",
        title=None,
        event_type=EventType.TASK,
    )

    result = normalize_command(command, datetime.fromisoformat("2026-10-15T12:00:00+03:00"))

    assert result.missing_fields == ["title", "due_date"]
    assert result.clarification_question == "Что нужно сделать?"


def test_explicit_without_title_overrides_model_title():
    command = AssistantCommand(
        action=Action.CREATE_TASK,
        transcript="Ответ модели",
        title="RETEST-04",
        event_type=EventType.TASK,
    )

    result = normalize_command(
        command,
        datetime.fromisoformat("2026-10-15T12:00:00+03:00"),
        source_text="Создай задачу RETEST-04 без названия",
    )

    assert result.missing_fields == ["title", "due_date"]
    assert result.clarification_question == "Что нужно сделать?"


def test_past_meeting_question_is_detected_as_search():
    command = AssistantCommand(
        action=Action.CLARIFY,
        transcript="Перефразированный ответ модели",
        clarification_question="Уточните запрос",
    )

    result = normalize_command(
        command,
        datetime.fromisoformat("2026-10-28T12:00:00+03:00"),
        source_text="Когда у меня была встреча с дизайнером?",
    )

    assert result.action == Action.SEARCH_EVENTS
    assert result.event_type == EventType.MEETING
    assert result.query == "дизайнером"


def test_after_lunch_period_has_time_bounds():
    command = AssistantCommand(action=Action.LIST_EVENTS, transcript="Какие планы после обеда?")

    result = normalize_command(
        command,
        datetime.fromisoformat("2026-10-15T12:00:00+03:00"),
        source_text="Какие планы после обеда?",
    )

    assert result.date_from == datetime.fromisoformat("2026-10-15T14:00:00+03:00")
    assert result.date_to == datetime.fromisoformat("2026-10-15T23:59:59+03:00")


def test_meetings_without_tasks_keeps_only_meeting_type():
    command = AssistantCommand(
        action=Action.LIST_EVENTS,
        transcript="Покажи встречи без задач",
    )

    result = normalize_command(
        command,
        datetime.fromisoformat("2026-10-15T12:00:00+03:00"),
        source_text="Покажи встречи без задач",
    )

    assert result.event_type == EventType.MEETING
    assert result.event_types == []


def test_conversational_meeting_question_is_search():
    command = AssistantCommand(
        action=Action.CLARIFY,
        transcript="Ответ модели",
        clarification_question="Уточните запрос",
    )

    result = normalize_command(
        command,
        datetime.fromisoformat("2026-10-15T12:00:00+03:00"),
        source_text="Что за встреча с Олей?",
    )

    assert result.action == Action.SEARCH_EVENTS
    assert result.event_type == EventType.MEETING


def test_short_update_extracts_target_from_original_text():
    command = AssistantCommand(
        action=Action.UPDATE_EVENT,
        transcript="Ответ модели",
        target_query=None,
    )

    result = normalize_command(
        command,
        datetime.fromisoformat("2026-10-15T12:00:00+03:00"),
        source_text="Измени CHECK-01 на 12:00",
    )

    assert result.target_query == "check-01"


def test_management_action_is_detected_from_original_text():
    command = AssistantCommand(
        action=Action.CLARIFY,
        transcript="Ответ модели",
        clarification_question="Уточните запрос",
    )

    result = normalize_command(
        command,
        datetime.fromisoformat("2026-10-15T12:00:00+03:00"),
        source_text="Отметь задачу FINAL-01 проверить договор выполненной",
    )

    assert result.action == Action.COMPLETE_EVENT
    assert result.target_query == "final-01 проверить договор"


def test_between_hours_period_has_time_bounds():
    command = AssistantCommand(action=Action.LIST_EVENTS, transcript="Что у меня между 10 и 12?")

    result = normalize_command(
        command,
        datetime.fromisoformat("2026-10-15T12:00:00+03:00"),
        source_text="Что у меня между 10 и 12?",
    )

    assert result.date_from == datetime.fromisoformat("2026-10-15T10:00:00+03:00")
    assert result.date_to == datetime.fromisoformat("2026-10-15T12:00:00+03:00")


def test_generic_planned_events_start_from_current_moment():
    command = AssistantCommand(
        action=Action.LIST_EVENTS,
        transcript="Какие запланированы события?",
        date_from=datetime.fromisoformat("2026-10-15T00:00:00+03:00"),
        date_to=datetime.fromisoformat("2026-10-15T23:59:59+03:00"),
    )

    now = datetime.fromisoformat("2026-10-15T12:00:00+03:00")
    result = normalize_command(command, now)

    assert result.action == Action.LIST_EVENTS
    assert result.date_from == now
    assert result.date_to is None


def test_unrelated_message_is_not_allowed_to_become_event_list():
    command = AssistantCommand(
        action=Action.LIST_EVENTS,
        transcript="Расскажи анекдот",
        date_from=datetime.fromisoformat("2026-10-15T00:00:00+03:00"),
        date_to=datetime.fromisoformat("2026-10-15T23:59:59+03:00"),
    )

    result = normalize_command(
        command, datetime.fromisoformat("2026-10-15T12:00:00+03:00")
    )

    assert result.action == Action.CLARIFY
    assert "создавать задачи" in result.clarification_question


def test_unrelated_clarification_stays_outside_event_creation():
    command = AssistantCommand(
        action=Action.CLARIFY,
        transcript="Привет, как дела?",
        clarification_question="Я помогаю работать с задачами и встречами.",
    )

    result = normalize_command(
        command, datetime.fromisoformat("2026-10-15T12:00:00+03:00")
    )

    assert result.action == Action.CLARIFY
    assert result.event_type is None


def test_search_keeps_relative_date_filter():
    command = AssistantCommand(
        action=Action.SEARCH_EVENTS,
        transcript="Найди встречи с Иваном завтра",
        query="Иван",
        event_type=EventType.MEETING,
    )

    result = normalize_command(
        command, datetime.fromisoformat("2026-10-15T12:00:00+03:00")
    )

    assert result.date_from == datetime.fromisoformat("2026-10-16T00:00:00+03:00")
    assert result.date_to == datetime.fromisoformat("2026-10-16T23:59:59+03:00")


def test_yesterday_is_preserved_when_creating_meeting():
    command = AssistantCommand(
        action=Action.CREATE_MEETING,
        transcript="Добавь встречу с Жекой вчера",
        title="Встреча с Жекой",
        event_type=EventType.MEETING,
        missing_fields=["start_time"],
    )

    result = normalize_command(command, datetime.fromisoformat("2026-10-14T22:57:00+03:00"))

    assert result.starts_at == datetime.fromisoformat("2026-10-13T09:00:00+03:00")
    assert result.missing_fields == ["past_date"]
    assert "прошла" in result.clarification_question


def test_task_without_date_clears_model_invented_due_date():
    command = AssistantCommand(
        action=Action.CREATE_TASK,
        transcript="Добавь задачу сделать английский",
        title="Сделать английский",
        event_type=EventType.TASK,
        due_at=datetime.fromisoformat("2026-10-31T23:59:59+03:00"),
        due_date=datetime.fromisoformat("2026-10-31").date(),
    )

    result = normalize_command(command, datetime.fromisoformat("2026-10-14T22:57:00+03:00"))

    assert result.due_at is None
    assert result.due_date is None
    assert result.missing_fields == ["due_date"]
    assert result.clarification_question == "На какую дату поставить задачу?"


def test_explicit_task_marker_overrides_wrong_meeting_classification():
    command = AssistantCommand(
        action=Action.CREATE_MEETING,
        transcript="Поставь задачу сходить погулять",
        title="Сходить погулять",
        event_type=EventType.MEETING,
        starts_at=datetime.fromisoformat("2026-10-15T15:00:00+03:00"),
    )

    result = normalize_command(
        command, datetime.fromisoformat("2026-10-15T12:00:00+03:00")
    )

    assert result.action == Action.CREATE_TASK
    assert result.event_type == EventType.TASK
    assert result.title == "Сходить погулять"
    assert result.missing_fields == ["due_date"]


def test_explicit_task_marker_overrides_clarification_and_meeting_type():
    command = AssistantCommand(
        action=Action.CLARIFY,
        transcript="Поставь задачу сходить погулять",
        title="Сходить погулять",
        event_type=EventType.MEETING,
        missing_fields=["start_date"],
        clarification_question="На какую дату поставить встречу?",
    )

    result = normalize_command(
        command, datetime.fromisoformat("2026-10-15T12:00:00+03:00")
    )

    assert result.action == Action.CREATE_TASK
    assert result.event_type == EventType.TASK
    assert result.missing_fields == ["due_date"]
    assert result.clarification_question == "На какую дату поставить задачу?"


def test_meeting_without_date_and_time_asks_for_both():
    command = AssistantCommand(
        action=Action.CREATE_MEETING,
        transcript="Добавь встречу в кафе",
        title="Встреча в кафе",
        event_type=EventType.MEETING,
    )

    result = normalize_command(
        command, datetime.fromisoformat("2026-10-15T12:00:00+03:00")
    )

    assert result.missing_fields == ["start_date", "start_time"]
    assert result.clarification_question == "На какую дату и время поставить встречу?"


def test_reminder_requires_both_date_and_time():
    command = AssistantCommand(
        action=Action.CREATE_TASK,
        transcript="Напомни позвонить Ивану завтра",
        title="Позвонить Ивану",
        event_type=EventType.REMINDER,
        missing_fields=["due_time"],
        clarification_question="На какое время поставить напоминание?",
    )

    result = normalize_command(
        command, datetime.fromisoformat("2026-10-15T12:00:00+03:00")
    )

    assert result.due_date.isoformat() == "2026-10-16"
    assert result.due_at is None
    assert result.missing_fields == ["due_time"]


def test_missing_title_uses_type_default():
    command = AssistantCommand(
        action=Action.CREATE_MEETING,
        transcript="Добавь встречу 20 октября в 16:00",
        title=None,
        event_type=EventType.MEETING,
        missing_fields=["title"],
        clarification_question="Что за встреча?",
    )

    result = normalize_command(
        command, datetime.fromisoformat("2026-10-15T12:00:00+03:00")
    )

    assert result.title == "Встреча"
    assert result.missing_fields == ["title"]


def test_past_meeting_is_not_ready_to_save():
    command = AssistantCommand(
        action=Action.CREATE_MEETING,
        transcript="Добавь встречу 10 октября в кафе в 12:00",
        title="Встреча в кафе",
        event_type=EventType.MEETING,
        starts_at=datetime.fromisoformat("2026-10-10T12:00:00+03:00"),
    )

    result = normalize_command(
        command, datetime.fromisoformat("2026-10-15T12:00:00+03:00")
    )

    assert result.missing_fields == ["past_date"]
    assert "прошла" in result.clarification_question


def test_past_meeting_date_is_reported_before_missing_time():
    command = AssistantCommand(
        action=Action.CREATE_MEETING,
        transcript="Добавь встречу на 10 октября в кафе",
        title="Встреча в кафе",
        event_type=EventType.MEETING,
        missing_fields=["start_time"],
        clarification_question="На какое время поставить встречу?",
    )

    result = normalize_command(
        command, datetime.fromisoformat("2026-10-15T12:00:00+03:00")
    )

    assert result.missing_fields == ["past_date"]
    assert result.clarification_question == "Указанная дата уже прошла. Укажите будущую дату."


def test_past_task_date_is_rejected():
    command = AssistantCommand(
        action=Action.CREATE_TASK,
        transcript="Поставь задачу на 10 октября",
        title="Сделать отчет",
        event_type=EventType.TASK,
        due_date=datetime.fromisoformat("2026-10-10").date(),
    )

    result = normalize_command(
        command, datetime.fromisoformat("2026-10-15T12:00:00+03:00")
    )

    assert result.missing_fields == ["past_date"]
    assert result.due_date == datetime.fromisoformat("2026-10-10").date()
    assert result.clarification_question == "Указанная дата уже прошла. Укажите будущую дату."


def test_explicit_search_overrides_wrong_creation_action():
    command = AssistantCommand(
        action=Action.CREATE_MEETING,
        transcript="Найди встречу в кино с Марусей",
        title="Встреча в кино с Марусей",
        event_type=EventType.MEETING,
        starts_at=datetime.fromisoformat("2026-10-29T18:00:00+03:00"),
    )

    result = normalize_command(
        command, datetime.fromisoformat("2026-10-28T12:00:00+03:00")
    )

    assert result.action == Action.SEARCH_EVENTS
    assert result.event_type == EventType.MEETING
    assert result.query == "кино марусей"
    assert result.starts_at is None


def test_past_search_is_limited_to_events_before_current_time():
    command = AssistantCommand(
        action=Action.CREATE_MEETING,
        transcript="Когда было кино?",
        title="Кино",
        event_type=EventType.MEETING,
    )
    now = datetime.fromisoformat("2026-10-28T18:49:00+03:00")

    result = normalize_command(command, now)

    assert result.action == Action.SEARCH_EVENTS
    assert result.query == "кино"
    assert result.date_from is None
    assert result.date_to == now


def test_future_search_is_limited_to_events_after_current_time():
    command = AssistantCommand(
        action=Action.CLARIFY,
        transcript="Когда будет кино?",
        clarification_question="Уточните запрос",
    )
    now = datetime.fromisoformat("2026-10-28T18:49:00+03:00")

    result = normalize_command(command, now)

    assert result.action == Action.SEARCH_EVENTS
    assert result.query == "кино"
    assert result.date_from == now
    assert result.date_to is None
