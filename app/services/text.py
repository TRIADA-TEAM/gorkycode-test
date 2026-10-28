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
    "события", "напоминание", "напоминания", "покажи", "покажите", "про", "по", "с",
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


def word_stem(value: str) -> str:
    """Apply a small suffix normalizer for Russian search terms."""
    for suffix in ("ировать", "иться", "овать", "евать", "ывать", "ивать", "ами", "ями", "ого", "ему", "ому", "ами", "ями", "ам", "ям", "ом", "ем", "ой", "ей", "ить", "ать", "ять", "ый", "ий", "ая", "ое", "ые", "ов", "ев", "ок", "ы", "и", "а", "я", "у", "е", "о", "ю", "ь"):
        if len(value) - len(suffix) >= 3 and value.endswith(suffix):
            return value[:-len(suffix)]
    return value
