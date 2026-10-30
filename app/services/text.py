import re


STOP_WORDS = {
    "в", "во", "на", "и", "или", "с", "со", "по", "к", "у", "о", "об",
    "от", "до", "за", "для", "про", "мне", "меня", "мой", "мои", "это",
}

SEARCH_WORDS = {
    "найди", "найти", "поищи", "поиск", "айди", "когда", "была", "был", "было",
    "были", "будет", "будут", "предстоит", "предстоят", "все", "всех",
    "весь", "у", "меня", "мою", "моей", "мой", "мое", "во", "сколько", "то", "что", "связано",
    "задачу", "задача", "задачи", "дело", "дела", "встречу", "встреча", "встречи", "событие",
    "сегодня", "вчера", "завтра", "послезавтра", "понедельник", "вторник", "среда",
    "четверг", "пятница", "суббота", "воскресенье",
    "расскажи", "за", "что",
    "события", "напоминание", "напоминания", "покажи", "покажите", "про", "по", "с",
    # Conversational framing describes the act of creating/searching, not the
    # subject itself: "Я когда-то ставил задачу по договору, найди ее".
    "я", "мы", "ее", "его", "их", "ставил", "ставила", "ставили",
    "поставил", "поставила", "поставили", "создавал", "создавала",
    "создавали", "добавлял", "добавляла", "добавляли",
}

TEMPORAL_SEARCH_PREFIXES = (
    "прошл", "прошедш", "предыдущ", "минувш", "состоявш", "состоял",
    "завершивш",
    "будущ", "предстоящ", "грядущ", "ближайш", "запланирован",
)


def normalize_text(value: str) -> str:
    value = value.lower().replace("ё", "е")
    value = re.sub(r"[^a-zа-я0-9]+", " ", value)
    return " ".join(value.split())


def extract_tags(value: str) -> list[str]:
    words = normalize_text(value).split()
    return sorted({word for word in words if len(word) >= 3 and word not in STOP_WORDS})


def extract_search_query(value: str) -> str:
    """Keep the subject of a natural-language search request."""
    words = normalize_text(value).split()
    terms = [
        word
        for word in words
        if word not in SEARCH_WORDS
        and not word.startswith(TEMPORAL_SEARCH_PREFIXES)
        and len(word) >= 2
    ]
    return " ".join(terms)


def extract_target_query(value: str) -> str:
    """Keep meaningful target words for update/delete commands."""
    identifiers = [item.lower() for item in re.findall(r"[a-zа-я]+-\d+", value, re.IGNORECASE)]
    words = normalize_text(value).split()
    management_words = {
        "измени", "изменить", "перенеси", "перенести", "поставь", "поставить",
        "удали", "удалить", "отмени", "отменить", "отметь", "отметить", "заверши", "завершить",
        "событие", "события", "задачу", "задача", "встречу", "встреча",
        "напоминание", "напоминания", "выполнить", "выполненной", "выполнена",
        "завершить", "на", "в", "до", "послезавтра",
        "завтра", "сегодня", "вчера",
    }
    identifier_parts = {
        part for identifier in identifiers for part in identifier.split("-")
    }
    terms = [
        word for word in words
        if word not in management_words
        and word not in identifier_parts
        and not re.fullmatch(r"\d{1,2}(:\d{2})?", word)
        and len(word) >= 2
    ]
    return " ".join([*identifiers, *terms])


def word_stem(value: str) -> str:
    """Apply a small suffix normalizer for Russian search terms."""
    for suffix in (
        "ирование", "ование", "евание", "ывание", "ивание",
        "ировать", "иться", "овать", "евать", "ывать", "ивать",
        "ками", "ку", "ки", "ка", "ке", "ами", "ями", "ого", "ему",
        "ому", "ам", "ям", "ом", "ем", "ой", "ей", "ить", "ать",
        "ять", "ый", "ий", "ая", "ое", "ые", "ов", "ев", "ок", "ы",
        "и", "а", "я", "у", "е", "о", "ю", "ь",
    ):
        if len(value) - len(suffix) >= 3 and value.endswith(suffix):
            return value[:-len(suffix)]
    return value
