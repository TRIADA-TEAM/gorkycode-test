const chat = document.querySelector("#chat");
const form = document.querySelector("#text-form");
const input = document.querySelector("#text-input");
const sendButton = document.querySelector("#send-button");
const recordButton = document.querySelector("#record-button");
const statusNode = document.querySelector("#status");
const speakToggle = document.querySelector("#speak-toggle");
const suggestionChips = document.querySelectorAll(".suggestion-chip");

const sessionId = getSessionId();
const historyKey = `assistant-history:${sessionId}`;
const historyLimit = 60;
let recorder = null;
let stream = null;
let chunks = [];
let mobileScrollFrame = null;
let mobileExpandedChromeHeight = 0;

const mobileLayout = window.matchMedia("(max-width: 560px)");
const appShell = document.querySelector(".app-shell");
const topbar = document.querySelector(".topbar");
const conversationIntro = document.querySelector(".conversation-intro");

function updateMobileChrome() {
  if (!mobileLayout.matches) {
    appShell?.classList.remove("mobile-chat-scrolled");
    mobileExpandedChromeHeight = 0;
    return;
  }

  const isCollapsed = appShell?.classList.contains("mobile-chat-scrolled") || false;
  if (!isCollapsed) {
    mobileExpandedChromeHeight =
      (topbar?.offsetHeight || 0) + (conversationIntro?.offsetHeight || 0) + 12;
  }

  // Collapsing the header makes the chat taller. With a short conversation
  // that used to remove the overflow, reset scrollTop and immediately expand
  // the header again. Only collapse when enough overflow will remain after the
  // extra space is given to the chat.
  const overflow = chat.scrollHeight - chat.clientHeight;
  const canCollapse = overflow > mobileExpandedChromeHeight + 24;
  const shouldCollapse = isCollapsed
    ? chat.scrollTop > 4
    : canCollapse && chat.scrollTop > 24;
  appShell?.classList.toggle("mobile-chat-scrolled", shouldCollapse);
}

chat.addEventListener("scroll", () => {
  if (mobileScrollFrame !== null) return;
  mobileScrollFrame = requestAnimationFrame(() => {
    mobileScrollFrame = null;
    updateMobileChrome();
  });
}, {passive: true});

mobileLayout.addEventListener?.("change", updateMobileChrome);
window.addEventListener("resize", updateMobileChrome, {passive: true});

suggestionChips.forEach((chip) => {
  chip.addEventListener("click", () => {
    if (sendButton.disabled) return;
    input.value = chip.textContent.trim();
    form.requestSubmit();
  });
});

function getSessionId() {
  let value = localStorage.getItem("assistant-session-id");
  if (!value) {
    value = createUuid();
    localStorage.setItem("assistant-session-id", value);
  }
  return value;
}

function createUuid() {
  if (globalThis.crypto?.randomUUID) {
    return globalThis.crypto.randomUUID();
  }
  if (globalThis.crypto?.getRandomValues) {
    const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0"));
    return [
      hex.slice(0, 4).join(""),
      hex.slice(4, 6).join(""),
      hex.slice(6, 8).join(""),
      hex.slice(8, 10).join(""),
      hex.slice(10, 16).join(""),
    ].join("-");
  }
  // Last-resort fallback for old browsers in an insecure local context.
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (char) => {
    const random = Math.floor(Math.random() * 16);
    const value = char === "x" ? random : (random & 0x3) | 0x8;
    return value.toString(16);
  });
}

function setBusy(busy, message = "Готов к работе") {
  input.disabled = busy;
  sendButton.disabled = busy;
  if (!recorder || recorder.state === "inactive") recordButton.disabled = busy;
  statusNode.textContent = message;
}

const MESSAGE_TIME_ZONE = "Europe/Moscow";

function loadChatHistory() {
  try {
    const value = JSON.parse(localStorage.getItem(historyKey) || "[]");
    return Array.isArray(value) ? value : [];
  } catch (_) {
    localStorage.removeItem(historyKey);
    return [];
  }
}

function saveChatMessage(role, messageText, events, timestamp) {
  const history = loadChatHistory();
  history.push({
    role,
    text: messageText,
    events,
    timestamp: timestamp.toISOString(),
  });
  try {
    localStorage.setItem(historyKey, JSON.stringify(history.slice(-historyLimit)));
  } catch (_) {
    // A full or unavailable storage must not break the assistant itself.
  }
}

function updateStoredEvent(eventId, updatedEvent) {
  const history = loadChatHistory();
  let changed = false;
  for (const message of history) {
    if (!Array.isArray(message.events)) continue;
    const nextEvents = message.events
      .map((event) => event.id === eventId ? updatedEvent : event)
      .filter(Boolean);
    if (nextEvents.length !== message.events.length
        || nextEvents.some((event, index) => event !== message.events[index])) {
      message.events = nextEvents;
      changed = true;
    }
  }
  if (changed) {
    try { localStorage.setItem(historyKey, JSON.stringify(history)); } catch (_) {}
  }
}

function restoreChatHistory() {
  for (const message of loadChatHistory()) {
    if (!message || !["user", "assistant"].includes(message.role)) continue;
    const timestamp = new Date(message.timestamp);
    addMessage(
      message.role,
      String(message.text || ""),
      Array.isArray(message.events) ? message.events : [],
      Number.isNaN(timestamp.getTime()) ? new Date() : timestamp,
      false,
    );
  }
}

function addMessage(role, text, events = [], timestamp = new Date(), persist = true) {
  const article = document.createElement("article");
  article.className = `message ${role}`;
  const p = document.createElement("p");
  p.textContent = text;
  article.appendChild(p);
  if (events.length) {
    const list = document.createElement("div");
    list.className = "events";
    for (const event of events) {
      list.appendChild(createEventCard(event));
    }
    article.appendChild(list);
  }
  const time = document.createElement("time");
  time.className = "message-time";
  time.dateTime = timestamp.toISOString();
  time.textContent = formatMessageTime(timestamp);
  time.title = formatFullMessageTime(timestamp);
  article.appendChild(time);
  chat.appendChild(article);
  chat.scrollTop = chat.scrollHeight;
  updateMobileChrome();
  if (persist) saveChatMessage(role, text, events, timestamp);
}

function createEventCard(event) {
  const card = document.createElement("div");
  card.className = "event";
  renderEventCard(card, event);
  return card;
}

function renderEventCard(card, event) {
  card.replaceChildren();
  const title = document.createElement("strong");
  title.textContent = event.title;
  const meta = document.createElement("small");
  meta.textContent = formatEvent(event);
  const actions = document.createElement("div");
  actions.className = "event-actions";
  const editButton = eventButton("Изменить", "secondary");
  editButton.addEventListener("click", () => showEventEditor(card, event));
  actions.appendChild(editButton);
  if (event.type !== "meeting" && event.status === "pending") {
    const completeButton = eventButton("Выполнено", "secondary");
    completeButton.addEventListener("click", async () => {
      await runEventAction(card, `/api/events/${event.id}/complete`, {method: "POST"});
    });
    actions.appendChild(completeButton);
  }
  const deleteButton = eventButton("Удалить", "danger");
  deleteButton.addEventListener("click", async () => {
    if (!confirm(`Удалить «${event.title}»?`)) return;
    await runEventAction(card, `/api/events/${event.id}`, {method: "DELETE"}, true);
  });
  actions.appendChild(deleteButton);
  card.append(title, meta, actions);
}

function eventButton(label, variant) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `event-button ${variant}`;
  button.textContent = label;
  return button;
}

function showEventEditor(card, event) {
  const moment = event.starts_at || event.due_at || "";
  const form = document.createElement("form");
  form.className = "event-editor";
  const title = editorField("Название", "text", event.title, true);
  const date = editorField("Дата", "date", event.due_date || moment.slice(0, 10));
  const time = editorField("Время", "time", moment.includes("T") ? moment.slice(11, 16) : "");
  form.append(title.label, date.label, time.label);
  const buttons = document.createElement("div");
  buttons.className = "event-actions";
  const save = eventButton("Сохранить", "primary");
  save.type = "submit";
  const cancel = eventButton("Отмена", "secondary");
  cancel.addEventListener("click", () => renderEventCard(card, event));
  buttons.append(save, cancel);
  form.appendChild(buttons);
  form.addEventListener("submit", async (submitEvent) => {
    submitEvent.preventDefault();
    const normalizedTitle = title.input.value.trim();
    if (!normalizedTitle) {
      addMessage("assistant", "Введите название.");
      title.input.focus();
      return;
    }
    const body = {title: normalizedTitle};
    if (date.input.value) body.event_date = date.input.value;
    if (time.input.value) body.event_time = time.input.value;
    if (!time.input.value && (event.due_at || event.starts_at)) body.clear_time = true;
    await runEventAction(card, `/api/events/${event.id}`, {
      method: "PATCH",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body),
    });
  });
  card.replaceChildren(form);
  title.input.focus();
}

function editorField(text, type, value, required = false) {
  const label = document.createElement("label");
  label.textContent = text;
  const input = document.createElement("input");
  input.type = type;
  input.value = value || "";
  input.required = required;
  label.appendChild(input);
  return {label, input};
}

async function runEventAction(card, url, options, remove = false) {
  const buttons = card.querySelectorAll("button");
  buttons.forEach((button) => { button.disabled = true; });
  try {
    const response = await fetch(url, {
      ...options,
      headers: {...(options.headers || {}), "X-Session-ID": sessionId},
    });
    const data = await parseResponse(response);
    if (data.created_event) {
      const hidden = remove || data.created_event.status !== "pending";
      updateStoredEvent(data.created_event.id, hidden ? null : data.created_event);
    }
    addMessage("assistant", data.reply);
    if (remove || data.created_event?.status !== "pending") {
      card.remove();
    } else if (data.created_event) {
      renderEventCard(card, data.created_event);
    } else {
      buttons.forEach((button) => { button.disabled = false; });
    }
  } catch (error) {
    addMessage("assistant", `Ошибка: ${error.message}`);
    buttons.forEach((button) => { button.disabled = false; });
  }
}

function moscowDateKey(value) {
  return new Intl.DateTimeFormat("en-CA", {
    year: "numeric", month: "2-digit", day: "2-digit", timeZone: MESSAGE_TIME_ZONE,
  }).format(value);
}

function formatMessageTime(value) {
  const today = moscowDateKey(new Date());
  const messageDate = moscowDateKey(value);
  const time = new Intl.DateTimeFormat("ru-RU", {
    hour: "2-digit", minute: "2-digit", timeZone: MESSAGE_TIME_ZONE,
  }).format(value);
  if (messageDate === today) return time;

  const dayDifference = (Date.parse(`${today}T00:00:00Z`) - Date.parse(`${messageDate}T00:00:00Z`)) / 86400000;
  if (dayDifference === 1) return `Вчера, ${time}`;

  const year = new Intl.DateTimeFormat("ru-RU", {
    year: "numeric", timeZone: MESSAGE_TIME_ZONE,
  }).format(value);
  const currentYear = new Intl.DateTimeFormat("ru-RU", {
    year: "numeric", timeZone: MESSAGE_TIME_ZONE,
  }).format(new Date());
  const date = new Intl.DateTimeFormat("ru-RU", {
    day: "numeric", month: "long", timeZone: MESSAGE_TIME_ZONE,
  }).format(value);
  return year === currentYear ? `${date}, ${time}` : `${date} ${year}, ${time}`;
}

function formatFullMessageTime(value) {
  return new Intl.DateTimeFormat("ru-RU", {
    dateStyle: "full", timeStyle: "short", timeZone: MESSAGE_TIME_ZONE,
  }).format(value);
}

function formatEvent(event) {
  const moment = event.starts_at || event.due_at;
  if (!moment && event.due_date) return `${event.type} · ${formatDateOnly(event.due_date)}`;
  if (!moment) return event.type;
  const date = new Date(moment);
  return `${event.type} · ${date.toLocaleString("ru-RU", {timeZone: MESSAGE_TIME_ZONE})}`;
}

function formatDateOnly(value) {
  return new Date(`${value}T00:00:00`).toLocaleDateString("ru-RU");
}

async function parseResponse(response) {
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data.detail;
    let message = "Не удалось обработать запрос";
    if (typeof detail === "string") {
      message = detail;
    } else if (Array.isArray(detail) && detail.length) {
      message = String(detail[0]?.msg || message).replace(/^Value error,\s*/i, "");
    }
    throw new Error(message);
  }
  return data;
}

function showAssistantResult(data) {
  const events = data.created_event ? [data.created_event] : data.events;
  addMessage("assistant", data.reply, events || []);
  if (speakToggle.checked && "speechSynthesis" in window) {
    speechSynthesis.cancel();
    speechSynthesis.speak(new SpeechSynthesisUtterance(data.reply));
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  addMessage("user", text);
  input.value = "";
  setBusy(true, "Обрабатываю запрос…");
  try {
    const response = await fetch("/api/assistant/text", {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-Session-ID": sessionId},
      body: JSON.stringify({text}),
    });
    showAssistantResult(await parseResponse(response));
  } catch (error) {
    addMessage("assistant", `Ошибка: ${error.message}`);
  } finally {
    setBusy(false);
    input.focus();
  }
});

recordButton.addEventListener("click", async () => {
  if (recorder && recorder.state === "recording") {
    recorder.stop();
    return;
  }
  try {
    stream = await navigator.mediaDevices.getUserMedia({audio: true});
    chunks = [];
    recorder = new MediaRecorder(stream, {mimeType: "audio/webm;codecs=opus"});
    recorder.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
    recorder.onstop = uploadRecording;
    recorder.start();
    recordButton.classList.add("recording");
    statusNode.textContent = "Идет запись… Нажмите ещё раз, чтобы остановить";
  } catch (error) {
    addMessage("assistant", "Не удалось получить доступ к микрофону.");
  }
});

async function uploadRecording() {
  recordButton.classList.remove("recording");
  stream?.getTracks().forEach((track) => track.stop());
  setBusy(true, "Распознаю запись…");
  try {
    const blob = new Blob(chunks, {type: "audio/webm"});
    const body = new FormData();
    body.append("audio", blob, "recording.webm");
    const response = await fetch("/api/assistant/audio", {
      method: "POST", headers: {"X-Session-ID": sessionId}, body,
    });
    const data = await parseResponse(response);
    addMessage("user", data.transcript || "Голосовое сообщение");
    showAssistantResult(data);
  } catch (error) {
    addMessage("assistant", `Ошибка: ${error.message}`);
  } finally {
    setBusy(false);
  }
}

restoreChatHistory();
