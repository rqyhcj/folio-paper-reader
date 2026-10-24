"""Keep protected source formulas out of model-generated mathematical text."""
from collections import Counter
import re

from .translation_quality import response_issue
from .translator import TranslationError


TOKEN = re.compile(r"⟦FOLIO_MATH_\d+⟧")


def paragraph_input(paragraph: dict) -> str:
    protected = paragraph.get("translation_text")
    return protected if isinstance(protected, str) and protected.strip() else paragraph["text"]


def validate_translation(paragraph: dict, translated: str) -> str:
    if not isinstance(translated, str) or not translated.strip():
        raise TranslationError("模型未返回可用译文，请重试此段。")
    source = paragraph_input(paragraph)
    expected = Counter(TOKEN.findall(source))
    if Counter(TOKEN.findall(translated)) != expected or TOKEN.findall(translated) != TOKEN.findall(source):
        raise TranslationError("模型未完整保留原式标记，请重试此段；公式不会交给模型改写。")
    if response_issue(translated, source):
        raise TranslationError("模型返回了索要原文的说明，未生成译文，请重试此段。")
    return translated.strip()
