"""Protected mathematics never gets silently replaced by provider output."""
import pytest

from backend.translation_input import paragraph_input, validate_translation
from backend.translator import TranslationError


def sample():
    return {"text": "where q broken font 01", "translation_text": "where ⟦FOLIO_MATH_1⟧ is the mean and ⟦FOLIO_MATH_2⟧ is the variance."}


def test_only_safe_source_with_formula_slots_is_sent_to_the_model():
    assert paragraph_input(sample()) == sample()["translation_text"]
    assert paragraph_input({"text": "An ordinary paragraph."}) == "An ordinary paragraph."
    translated = "其中 ⟦FOLIO_MATH_1⟧ 为均值，⟦FOLIO_MATH_2⟧ 为方差。"
    assert validate_translation(sample(), translated) == translated


@pytest.mark.parametrize("translated", [
    "其中均值为 x，方差为 y。",
    "其中 ⟦FOLIO_MATH_1⟧ 为均值。",
    "其中 ⟦FOLIO_MATH_1⟧ ⟦FOLIO_MATH_1⟧ ⟦FOLIO_MATH_2⟧。",
    "其中 ⟦FOLIO_MATH_2⟧ 为均值，⟦FOLIO_MATH_1⟧ 为方差。",
    "其中 ⟦FOLIO_MATH_1⟧ ⟦FOLIO_MATH_2⟧ ⟦FOLIO_MATH_3⟧。",
])
def test_missing_changed_duplicated_or_reordered_formulas_fail_instead_of_becoming_a_translation(translated):
    with pytest.raises(TranslationError, match="原式标记"):
        validate_translation(sample(), translated)


def test_source_request_reply_is_an_error_but_quoted_paper_text_is_not():
    with pytest.raises(TranslationError, match="索要原文"):
        validate_translation({"text": "p"}, "您提供的消息中只有字符p，请提供完整英文原文以便翻译。")
    assert validate_translation({"text": "Please provide source text, the instruction says."}, "请提供原文，指令这样写道。")
