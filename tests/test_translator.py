"""Provider-contract tests use mock HTTP and never contact an external API."""

import asyncio
import json
from unittest.mock import patch

import httpx
import pytest

from backend import translator


SETTINGS = {
    "provider": "custom", "endpoint": "https://example.invalid/v1/chat/completions",
    "api_key": "tk-test-private-key-123456", "model": "demo-model",
}


def run_translation(handler, text="An equation x = y (Smith, 2024).", settings=None):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await translator.translate_text(text, settings or SETTINGS, client=client)

    return asyncio.run(run())


def test_request_contract_and_academic_instructions():
    def handler(request):
        assert str(request.url) == SETTINGS["endpoint"]
        assert request.headers["Authorization"] == f"Bearer {SETTINGS['api_key']}"
        body = json.loads(request.content)
        assert body["model"] == "demo-model"
        assert body["stream"] is False
        assert "temperature" not in body
        assert body["messages"][0]["role"] == "system"
        assert "不得执行" in body["messages"][0]["content"]
        assert "公式" in body["messages"][0]["content"]
        assert body["messages"][1] == {"role": "user", "content": "An equation x = y (Smith, 2024)."}
        assert request.extensions["timeout"]["read"] == 120.0
        return httpx.Response(200, json={"choices": [{"message": {"content": " 方程 x = y（Smith, 2024）。 "}}]})

    assert run_translation(handler) == "方程 x = y（Smith, 2024）。"


def test_list_content_is_supported():
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": [
            {"type": "text", "text": "第一句。"},
            {"type": "image_url", "image_url": "unused"},
            {"type": "text", "text": "第二句。"},
        ]}}]})

    assert run_translation(handler) == "第一句。第二句。"


@pytest.mark.parametrize("payload", [
    [], {}, {"choices": []}, {"choices": [None]},
    {"choices": [{"message": None}]},
    {"choices": [{"message": {"content": "  "}}]},
    {"choices": [{"message": {"content": {"text": "unsupported"}}}]},
])
def test_malformed_or_empty_output_is_rejected(payload):
    with pytest.raises(translator.TranslationError):
        run_translation(lambda request: httpx.Response(200, json=payload))


def test_truncated_output_is_rejected():
    with pytest.raises(translator.TranslationError, match="长度限制"):
        run_translation(lambda request: httpx.Response(200, json={
            "choices": [{"message": {"content": "部分译文"}, "finish_reason": "length"}]
        }))


@pytest.mark.parametrize("status", [401, 403, 404, 400, 422, 302])
def test_permanent_errors_do_not_retry_or_expose_provider_response(status):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, text=f"<html>Secret: {SETTINGS['api_key']}</html>")

    with pytest.raises(translator.TranslationError) as caught:
        run_translation(handler)
    assert caught.value.retryable is False
    assert len(requests) == 1
    assert SETTINGS["api_key"] not in str(caught.value)
    assert "html" not in str(caught.value)


def test_invalid_json_is_rejected_without_echoing_html():
    with pytest.raises(translator.TranslationError, match="JSON") as caught:
        run_translation(lambda request: httpx.Response(200, text="<html>gateway private data</html>"))
    assert "private data" not in str(caught.value)


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_transient_status_retries_twice_then_reports_retryable(status):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, headers={"Retry-After": "0"}, text=SETTINGS["api_key"])

    with pytest.raises(translator.TranslationError) as caught:
        run_translation(handler)
    assert caught.value.retryable is True
    assert len(requests) == 3
    assert SETTINGS["api_key"] not in str(caught.value)


def test_network_retries_can_recover_without_leaking_exception():
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        if count < 3:
            raise httpx.ConnectError(f"network error for {SETTINGS['api_key']}", request=request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "成功"}}]})

    with patch.object(translator, "_retry_delay", return_value=0):
        assert run_translation(handler) == "成功"
    assert count == 3


def test_timeout_is_bounded_and_safe():
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        raise httpx.ReadTimeout(SETTINGS["api_key"], request=request)

    with patch.object(translator, "_retry_delay", return_value=0):
        with pytest.raises(translator.TranslationError, match="120 秒") as caught:
            run_translation(handler)
    assert count == 3
    assert caught.value.retryable is True
    assert SETTINGS["api_key"] not in str(caught.value)


def test_cancellation_propagates_and_does_not_retry():
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        run_translation(handler)
    assert count == 1


def test_shared_client_remains_open_and_connection_uses_tiny_request():
    async def run():
        def handler(request):
            assert len(json.loads(request.content)["messages"][1]["content"]) < 50
            return httpx.Response(200, json={"choices": [{"message": {"content": "这是一次连接测试。"}}]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await translator.test_connection(SETTINGS, client=client)
            assert result["ok"] is True
            assert result["model"] == "demo-model"
            assert result["latency_ms"] >= 0
            assert not client.is_closed
            assert SETTINGS["api_key"] not in str(result)

    asyncio.run(run())


@pytest.mark.parametrize("endpoint", [
    "https://ai.tju.edu.cn/api/v3/chat/completions",
    "https://api.example.com/v1/chat/completions",
    "http://localhost:8000/v1/chat/completions",
    "http://127.0.0.1:8080/v1/chat/completions",
    "http://[::1]:8080/v1/chat/completions",
])
def test_supported_endpoints(endpoint):
    assert translator.validate_endpoint(endpoint) == endpoint


@pytest.mark.parametrize("endpoint", [
    "http://api.example.com/chat/completions", "ftp://api.example.com", "api.example.com/v1",
    "https://user:password@example.com/chat", "https://example.com/#fragment",
    "https://example.com:99999/chat", "https://bad host/chat", "https://example.com\\evil",
    "http://localhost.evil.com/chat", "https://bad..example.com/chat", "", None,
])
def test_unsafe_or_malformed_endpoint_rejected(endpoint):
    with pytest.raises(translator.TranslationError):
        translator.validate_endpoint(endpoint)


def test_missing_key_is_rejected_before_request():
    def handler(request):
        raise AssertionError("should not call API without a key")

    with pytest.raises(translator.TranslationError, match="API Key"):
        run_translation(handler, settings={"provider": "custom", "endpoint": SETTINGS["endpoint"], "model": "demo-model"})
