"""Narrow checks for model replies that ask for source text instead of translating.

These are structural checks, not an assessment of translation quality. Genuine
paper passages quoting such requests are left alone when the source contains
the same request language.
"""
from __future__ import annotations

import re


_SOURCE_REQUEST = re.compile(
    r"(?:请.{0,45}(?:提供|发送|粘贴|补充).{0,55}(?:英文|原文|论文|需要翻译|待翻译)|"
    r"请.{0,25}(?:提供|发送|粘贴|补充).{0,30}(?:文本|片段|内容).{0,30}翻译|"
    r"please\s+(?:provide|paste|send|supply).{0,100}(?:text|passage|source|excerpt|translate))",
    re.I | re.S,
)
_REPLY_START = re.compile(
    r"^(?:(?:很?抱歉|对不起)[，,。!！：:\s]*)?(?:您|你)(?:当前)?(?:提供|发送|输入|提交)|"
    r"^(?:(?:很?抱歉|对不起)[，,。!！：:\s]*)?(?:没有|未)(?:提供|收到|包含)|"
    r"^(?:(?:很?抱歉|对不起)[，,。!！：:\s]*)?请(?:先)?(?:提供|发送|粘贴|补充)|"
    r"^(?:(?:I(?:'m| am)\s+sorry|Sorry)[,.!:\s]*)?(?:please\s+(?:provide|paste|send|supply)|"
    r"you\s+(?:have\s+)?(?:only\s+)?(?:provided|sent)|no\s+(?:source\s+)?(?:text|passage))",
    re.I,
)
_QUOTED_REQUEST_IN_SOURCE = re.compile(
    r"please\s+(?:provide|paste|send|supply)|you\s+(?:have\s+)?(?:only\s+)?(?:provided|sent)|"
    r"请(?:先)?(?:提供|发送|粘贴|补充)|(?:您|你)(?:提供|发送|输入|提交)", re.I,
)


def response_issue(text: str, source_text: str) -> str | None:
    """Return a reason only for a recognizable request-for-source reply."""
    reply = str(text or "").strip().strip('"“”')
    if len(reply) > 800 or _QUOTED_REQUEST_IN_SOURCE.search(str(source_text or "")):
        return None
    if _REPLY_START.search(reply) and _SOURCE_REQUEST.search(reply):
        return "model-requested-source"
    return None
