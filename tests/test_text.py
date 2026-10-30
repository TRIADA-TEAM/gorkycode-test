import pytest

from app.services.text import extract_search_query, word_stem


def test_conversational_search_framing_is_removed_from_subject():
    assert (
        extract_search_query("Я когда-то ставил задачу по договору, найди ее")
        == "договору"
    )


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
