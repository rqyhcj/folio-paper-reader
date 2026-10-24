"""Reject recognizable model requests for source, not legitimate paper content."""
import pytest

from backend.translation_quality import response_issue


@pytest.mark.parametrize("reply", [
    "您提供的消息中只有字符“p”，没有可供翻译的论文片段。请粘贴需要翻译的英文原文。",
    "抱歉，您提供的输入似乎不完整或无效。请提供需要翻译的英文论文片段。",
    "请提供需要翻译的英文原文。",
    "Sorry, you have only provided a symbol. Please provide the text you want to translate.",
    "Please paste the source passage to translate.",
])
def test_source_request_is_not_a_translation(reply):
    assert response_issue(reply, "p") == "model-requested-source"


@pytest.mark.parametrize("source,translated", [
    ("The model uses a standard ODE solver.", "该模型使用标准 ODE 求解器。"),
    ("Please provide the text you want to translate.", "请提供需要翻译的英文原文。"),
    ("The request 'please provide source text' was displayed.", "系统显示了‘请提供原文’这一请求。"),
    ("We provide a symbol p.", "我们提供了符号 p。"),
    ("x = y", r"\(x=y\)"),
])
def test_actual_translation_or_quoted_request_is_retained(source, translated):
    assert response_issue(translated, source) is None
