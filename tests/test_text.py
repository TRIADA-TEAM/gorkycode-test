import pytest

from app.services.text import extract_search_query, word_stem


def test_conversational_search_framing_is_removed_from_subject():
    assert (
        extract_search_query("Я когда-то ставил задачу по договору, найди ее")
        == "договору"
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Найди, пожалуйста, задачу про договор", "договор"),
        (
            "Поищи, пожалуйста, что я планировал сделать с договором",
            "договором",
        ),
        ("Покажи ту задачу, где речь шла о договоре", "договоре"),
        ("Когда запланировано согласование договора?", "согласование договора"),
    ],
)
def test_free_form_search_frames_leave_only_subject(text, expected):
    assert extract_search_query(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Найди задачу про планирование бюджета", "планирование бюджета"),
        ("Найди задачу что я хотел купить", "хотел купить"),
    ],
)
def test_meaningful_words_from_possible_frames_are_preserved(text, expected):
    assert extract_search_query(text) == expected


def test_period_prepositions_do_not_become_search_subject():
    assert extract_search_query("Когда запланировано на завтра?") == ""


@pytest.mark.parametrize(
    ("verb", "noun", "expected"),
    [
        ("согласовать", "согласование", "соглас"),
        ("организовать", "организование", "организ"),
        ("планировать", "планирование", "план"),
    ],
)
def test_action_and_derived_noun_share_search_stem(verb, noun, expected):
    assert word_stem(verb) == expected
    assert word_stem(noun) == expected
