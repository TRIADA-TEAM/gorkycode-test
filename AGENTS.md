# Voice Assistant Test Project

## Project goal

Build a small public prototype of a Russian-language voice assistant for tasks,
reminders, and meetings. The result will be delivered as a repository URL and a
public service URL. Reviewers will test the deployed service themselves, so the
happy path, error handling, setup instructions, and demo data must work without
manual intervention.

Public presentation copy:

- `Голосовой помощник для задач, встреч и напоминаний.`
- `Сделано командой «Триада» для GorkyCode.`

The source assignment is
`Тестовое_задание_голосовой_ассистент_для_задач_и_встреч.pdf`.
Follow `IMPLEMENTATION_PLAN.md` for the agreed architecture, API, data model,
delivery phases, and MVP completion criteria.

## MVP scope

The first version must support:

1. Create a task or reminder from free-form Russian text or recorded speech.
2. Create a meeting from free-form Russian text or recorded speech.
3. List tasks and meetings for a requested date range.
4. Search previously created tasks and meetings.
5. Ask a follow-up question when required information is missing.
6. Preserve data and an unfinished dialogue between consecutive requests.

Out of scope for the first version:

- authentication and multiple registered users;
- Google/Outlook calendar synchronization;
- recurring events;
- production notifications;
- realtime/always-on audio streaming;
- a full RAG pipeline.

## Agreed technology direction

- Backend: Python and FastAPI.
- Persistence: SQLite on the VPS filesystem, accessed through SQLAlchemy.
- Runtime schema management: Alembic only. `create_all()` is reserved for
  isolated tests. SQLite connections enable foreign keys and a 5-second busy
  timeout. Use `python -m scripts.migrate`; it validates and stamps a compatible
  legacy `create_all()` database before applying later Alembic revisions.
- Deployment: a simple VPS with persistent disk and HTTPS.
- LLM/NLU: GigaChat API.
- Voice input: record a short audio file in the browser, upload it to the
  backend, transcribe it with Polza Whisper Turbo, and pass the resulting text
  through the same GigaChat interpretation flow as typed input.
- UI: a minimal web page with a record button, text input, dialogue history,
  and visible event results.
- Desktop UI uses two columns from 900 px: explanatory copy, hints, and example
  commands on the left; a full-height chat with its composer kept visible on
  the right. Narrow screens keep the compact single-column conversation.
- On screens up to 560 px, scrolling the chat collapses the top bar and intro
  after 24 px so the dialogue gains vertical space. Returning the chat to its
  top restores them; the composer remains visible throughout. Collapse is
  enabled only when the conversation remains scrollable after the reclaimed
  header space, preventing short chats from oscillating between both layouts.
- The browser keeps up to 60 recent user/assistant messages per anonymous
  session in `localStorage` and restores them after reload. Event updates,
  completion, and deletion also update or remove stored event cards.
- The index injects a content-derived version into the main JS/CSS URLs, and
  prototype HTML/static responses use `Cache-Control: no-store`. This prevents
  stale frontend code after deployment while the prototype is under review.
- Time zone: `Europe/Moscow` until the product rules are revisited.
- No seed dataset or seed import is used. Tests create their own isolated data.

Do not depend on SaluteSpeech for the MVP. As of 2026-10-10 its API still has
documentation and supports existing customers, but since 2026-07-15 connection
for new individual customers is unavailable and Freemium renewal has stopped.

## GigaChat integration constraints

Current GigaChat API documentation confirms:

- models support user-defined functions/tool calls;
- structured output can be constrained with `response_format.type=json_schema`;
- GigaChat also provides a separate embeddings endpoint, but embeddings are not
  required for the first search implementation.

Keep both GigaChat command interpretation and Polza speech-to-text behind
application interfaces so either provider can be substituted without changing
business logic.
Always validate model-produced data with Pydantic before changing the database.
The model may select an action and propose arguments; it must not execute SQL.

Initial application actions/tools:

- `create_task`
- `create_meeting`
- `list_events`
- `search_events`
- `update_event`
- `delete_event`
- `complete_event`
- `cancel_action`

Prefer strict JSON schema for intent and argument extraction. User-visible
answers may be generated only after the application has validated and executed
the requested operation.

## Anonymous user and dialogue state

Do not identify users by IP address. IPs can change, can be shared by many
people, and introduce unnecessary personal-data concerns.

On first visit, generate a random UUID in the browser and store it in a cookie
or `localStorage`. Send it as an opaque session ID with API requests. Use that ID
to isolate reviewer-created events and dialogue state.

Persist short dialogue state in SQLite. A session should contain at least:

- `session_id`;
- `pending_action` as validated JSON;
- `last_referenced_event_id`, when applicable;
- `created_at` and `updated_at`.

Example clarification flow:

1. User: `Добавь встречу с Иваном завтра`.
2. Model returns `create_meeting` with a missing start time.
3. Backend saves a pending action and asks for the time.
4. User: `В 15:00`.
5. Backend combines the answer with the pending action, validates it, creates
   the meeting, clears pending state, and confirms the result.

Do not send an unbounded conversation transcript to the model. Store messages
for UI/history if useful, but pass only the current request, compact session
state, and the few most recent turns needed for disambiguation.

`pending_action` expires after 30 minutes by default. Configure this through
`DIALOGUE_TTL_MINUTES`. Reading an expired pending action clears it and starts a
new command. Creating an event and clearing its pending action must happen in
one database transaction so a partial failure cannot create duplicates.

Creation, a list/search result containing exactly one event, and a successful
update or completion set `last_referenced_event_id`. This lets a following
command use references such as `перенеси её` or `удали его`. If a description
matches several events, return the candidates and persist their IDs; the user
can select one by its displayed number. Never resolve an event ID outside the
current anonymous session.

## Data model direction

Use one `events` table for tasks, reminders, and meetings. It should include at
least:

- `id`;
- `owner_session_id`;
- `source` (`user_text` or `user_audio`);
- `type` (`task`, `reminder`, or `meeting`);
- `title` and optional `description`;
- `starts_at`, `ends_at`, and/or `due_at`;
- `timezone`;
- `status`;
- `raw_user_text`;
- `created_at` and `updated_at`.

Store normalized datetimes as ISO-8601 values and perform parsing and comparison
through Python datetime types. Keep the original user phrase for debugging.

Current provisional time rules:

- week starts on Monday;
- default timezone is `Europe/Moscow`;
- a task may have a date without an exact time;
- a meeting requires a start time;
- default meeting duration is 60 minutes;
- ambiguous or contradictory input triggers a clarification instead of a write;
- overlapping pending meetings trigger a warning and require explicit
  confirmation; meetings whose boundaries merely touch are not overlapping;
- rejecting a conflict confirmation or saying `не надо` clears the pending
  operation without calling the language model;
- explicit search phrases (`найди`, `поищи`, `когда было`, `когда будет`) take
  precedence over a provider-proposed creation action. Past/future search is
  filtered against the current timestamp, not only the calendar date;
- an exact date in a search request (for example `20 октября` or `02.11.2026`)
  selects that one day and must not be expanded to the entire named month;
- generic list wording with `запланировано` or `предстоит` returns events from
  the current timestamp forward. `Было запланировано` returns past events;
  `покажи события` remains unbounded. An explicit day or period takes priority
  and returns that complete calendar range;
- past synonyms include `прошлые`, `прошедшие`, `предыдущие`, `минувшие`,
  `состоявшиеся`, and `завершившиеся`; future synonyms include `будущие`,
  `предстоящие`, `ближайшие`, and `грядущие`. Temporal qualifiers are filters,
  not search subject terms;
- season names are recognized only as separate words with the corresponding
  stem. A stem inside an unrelated word (for example `лет` in `билеты`) must
  not introduce a hidden summer date range;
- list/search commands may contain multiple explicit event types. Preserve the
  exact requested set through `event_types`; do not collapse `задачи и встречи`
  to only one type or expand it to include reminders;
- negative status forms (`невыполненные`, `незавершённые`, `несделанные`) are
  resolved before positive stems such as `выполнен`; they select only pending
  tasks or reminders. Positive completion wording selects completed records;
- an explicit new creation command (`добавь`, `создай`, `поставь`, `напомни`,
  `назначь`, `запланируй`) replaces an unfinished creation instead of inheriting
  its date or time. The new event must independently provide all required data;
- relative-date behavior will be refined later and must be covered by tests with
  a fixed application clock;
- an unqualified weekday means the next occurrence strictly after today. On a
  Friday, `в пятницу` resolves to the following Friday; use `сегодня` for the
  current day;

## Event management rules

- Tasks, reminders, and meetings can be renamed or moved through the same text
  and voice assistant endpoints used for creation.
- Updating only the date preserves an existing time; updating only the time
  preserves an existing date. Meeting duration is preserved when moved.
- Moving an event must never rename it from a provider paraphrase. `new_title`
  from text or voice is accepted only when the user explicitly asks to rename
  the event (for example with `переименуй` or `измени название`); direct event
  card edits remain deterministic and may update the title explicitly.
- A task without a deadline can be renamed without inventing a date. Supplying
  only a time for such a task requires clarification of the date.
- Tasks and reminders can be marked completed. Meetings cannot be completed;
  they can be moved or deleted.
- Deletion is soft deletion: set `status=cancelled`, do not remove the database
  row. Cancelled events are hidden from normal lists and search.
- Completed events are hidden from upcoming lists but remain searchable so the
  user can still address or inspect them.
- Past dates and times are rejected before a write and the pending update is
  preserved so the user can provide a corrected value.
- If an update, delete, or completion command does not identify a target, ask
  which event is meant. Never silently select the only stored event.
- Event cards expose deterministic `Изменить`, `Удалить`, and, for tasks and
  reminders, `Выполнено` controls. These controls call session-isolated event
  endpoints directly and do not ask GigaChat to infer the intended operation.
- The direct event API is `PATCH /api/events/{id}`, `DELETE /api/events/{id}`,
  and `POST /api/events/{id}/complete`. Editing accepts `title`, `event_date`,
  and `event_time`; the same business validation for past dates and meeting
  conflicts applies.

## Search plan

Use deterministic search, not RAG:

1. Assign normalized tags when importing or creating an event.
2. Filter by event type, date range, and session ownership.
3. Search tags and normalized event text in the current anonymous session.
4. Multi-word queries require every meaningful term to match. Do not return
   `кино с Марусей` for `кино с Иваном` merely because `кино` matches.
5. Return a small ranked result set.

Conversational framing such as `я когда-то ставил задачу ... найди её` is not
part of the search subject and must be removed before matching. Deterministic
stemming should map common action/derived-noun pairs to one root, for example
`согласовать` and `согласование`, while the all-meaningful-terms rule remains
in force. Remove polite or narrative framing only inside search-query
extraction and only as complete recognized phrases; never remove words such as
`планировал` or `хотел` globally from creation text, titles, or tags.

A singular thematic request such as `покажи ту задачу, где речь шла о договоре`
or `когда запланировано согласование договора` is a search and takes precedence
over generic list wording. Without a meaningful subject, a type/period question
remains a list. In generic plural period questions, `дела` means all event
types; singular `дело` in creation or targeted search continues to mean a task.

SQLite FTS5, embeddings, a vector database, and RAG are not required for this
test project. Reconsider them only if deterministic search proves insufficient.

## Secrets and repository safety

Runtime API credentials may be stored in a VPS `.env` file, but `.env` must
never be committed. Maintain a committed `.env.example` containing variable
names and safe placeholders. Ensure `.gitignore` covers `.env`, SQLite runtime
files, uploaded audio, logs, and other generated artifacts.

The browser must never receive GigaChat credentials. All provider calls go
through the backend. Delete temporary local audio and remote uploaded files
after processing whenever the API flow permits it.

## Quality and delivery requirements

- Provide a health endpoint suitable for VPS monitoring.
- Use Alembic as the only runtime schema initialization mechanism. Application
  startup must not call `Base.metadata.create_all()`; tests may use it for an
  isolated in-memory database.
- Include Docker-based setup and production launch instructions.
- Provide graceful messages for microphone denial, malformed audio, provider
  timeouts, invalid model output, and database errors.
- Test relative dates with a frozen clock and test the four required actions.
- Keep audio/provider, LLM interpretation, business logic, persistence, and UI
  as separate modules.
- README must explain architectural decisions, limitations, environment setup,
  service URL, and several example phrases reviewers can try.

## Deferred features

Do not implement these until the core acceptance scenarios are stable:

- FTS5, embeddings, and RAG;
- additional calendar integrations;
- recurring events and production notifications;
- restoring cancelled events and a dedicated completed/cancelled history UI;
- broad cross-browser audio conversion.

## Open decisions

Resolve these through small technical spikes before locking implementation:

- exact GigaChat model available under the project credentials and whether its
  quota supports JSON schema reliably;
- Polza Whisper Turbo compatibility with the browser's WebM/Opus recordings;
- browser audio format and any FFmpeg conversion needed on the VPS;
- whether voice output is required for the first deployed version and, if so,
  which TTS provider or browser API to use;
- final tagging vocabulary and ranking rules;
- final rules for relative dates beyond the currently tested phrases;
- whether meeting conflict detection should eventually include configurable
  buffer time or only strict interval overlap.
