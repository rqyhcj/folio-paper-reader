"""Provider configuration, credential isolation, and mocked request contracts."""

import asyncio
import importlib
import json
import os
import time

from fastapi.testclient import TestClient
import httpx
import pytest

from backend import storage as db, translator
from backend.providers import requires_key


api = importlib.import_module("backend.app")
ENDPOINT = "https://example.invalid/v1/chat/completions"
TEST_KEY = "test-only-key-for-mocked-requests"


@pytest.fixture(autouse=True)
def prohibit_external_requests(monkeypatch):
    async def denied(*args, **kwargs):
        raise AssertionError("Provider tests must use MockTransport, never a real API")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", denied)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA", tmp_path / "provider-data")
    # Credential transition tests do not depend on a particular OS keystore.
    # The native Windows encryption round trip is covered in test_api.py.
    if os.name != "nt":
        monkeypatch.setattr(db, "_protect", lambda data, decrypt=False: data[::-1])
    api.jobs.clear()
    with TestClient(api.app) as session:
        yield session
    assert not any(not task.done() for task in api.jobs.values())
    api.jobs.clear()


def save_connection(client, **overrides):
    payload = {"provider": "custom", "endpoint": ENDPOINT, "model": "demo-model", "api_key": TEST_KEY}
    payload.update(overrides)
    response = client.put("/api/settings", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_fresh_install_has_no_selected_model_endpoint_or_key(client):
    response = client.get("/api/settings")
    assert response.status_code == 200
    settings = response.json()
    assert settings["provider"] == "custom"
    assert settings["endpoint"] == ""
    assert settings["model"] == ""
    assert settings["has_key"] is False
    assert settings["key_hint"] == ""
    assert settings["requires_key"] is True
    assert "api_key" not in settings
    assert db.settings_private()["api_key"] == ""
    assert not (db.DATA / "settings.json").exists()


def test_provider_catalog_is_public_suggestions_without_credentials(client):
    save_connection(client)
    response = client.get("/api/providers")
    assert response.status_code == 200
    catalog = response.json()
    assert {provider["id"] for provider in catalog} == {"custom", "deepseek", "openai", "dashscope", "tju", "ollama"}
    for provider in catalog:
        assert set(provider) == {"id", "name", "endpoint", "models", "requires_key", "note"}
        assert isinstance(provider["models"], list)
        assert all(isinstance(model, str) for model in provider["models"])
    custom = next(provider for provider in catalog if provider["id"] == "custom")
    assert custom["endpoint"] == "" and custom["models"] == []
    assert TEST_KEY not in response.text
    assert "api_key" not in response.text
    assert "protected_key" not in response.text


@pytest.mark.parametrize("provider", ["deepseek", "custom"])
def test_openai_compatible_providers_use_selected_endpoint_model_and_bearer_header(provider):
    calls = []

    def handler(request):
        calls.append(request)
        assert str(request.url) == ENDPOINT
        assert request.headers["Authorization"] == f"Bearer {TEST_KEY}"
        payload = json.loads(request.content)
        assert payload["model"] == "demo-model"
        assert payload["stream"] is False
        assert "temperature" not in payload
        assert TEST_KEY not in request.content.decode()
        assert payload["messages"][1] == {"role": "user", "content": "Preserve citations [1]."}
        return httpx.Response(200, json={"choices": [{"message": {"content": "保留引用 [1]。"}}]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as mock_client:
            return await translator.translate_text("Preserve citations [1].", {
                "provider": provider, "endpoint": ENDPOINT, "model": "demo-model", "api_key": TEST_KEY,
            }, client=mock_client)

    assert asyncio.run(run()) == "保留引用 [1]。"
    assert len(calls) == 1


@pytest.mark.parametrize("endpoint", [
    "http://localhost:11434/v1/chat/completions",
    "http://127.0.0.1:11434/v1/chat/completions",
    "http://[::1]:11434/v1/chat/completions",
])
def test_ollama_loopback_translates_without_a_user_key(endpoint):
    settings = {"provider": "ollama", "endpoint": endpoint, "model": "demo-model", "api_key": ""}
    assert requires_key(settings) is False
    calls = []

    def handler(request):
        calls.append(request)
        assert str(request.url) == translator.validate_endpoint(endpoint)
        assert json.loads(request.content)["model"] == "demo-model"
        assert TEST_KEY not in str(request.headers)
        return httpx.Response(200, json={"choices": [{"message": {"content": "本机译文"}}]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as mock_client:
            return await translator.translate_text("A local paragraph.", settings, client=mock_client)

    assert asyncio.run(run()) == "本机译文"
    assert len(calls) == 1


@pytest.mark.parametrize("provider,endpoint", [
    ("ollama", "https://remote.example.invalid/v1/chat/completions"),
    ("ollama", "https://localhost.evil.invalid/v1/chat/completions"),
    ("custom", "http://localhost:11434/v1/chat/completions"),
    ("deepseek", ENDPOINT),
])
def test_remote_ollama_and_other_services_require_a_user_key_before_request(provider, endpoint):
    settings = {"provider": provider, "endpoint": endpoint, "model": "demo-model", "api_key": ""}
    assert requires_key(settings) is True

    def handler(request):
        raise AssertionError("An unconfigured key must be rejected before HTTP")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as mock_client:
            return await translator.translate_text("A paragraph.", settings, client=mock_client)

    with pytest.raises(translator.TranslationError, match="API Key"):
        asyncio.run(run())


@pytest.mark.parametrize("change", [
    {"endpoint": "https://other.example.invalid/v1/chat/completions"},
    {"provider": "deepseek"},
])
def test_changing_connection_context_without_a_new_key_clears_the_previous_key(client, change):
    save_connection(client)
    response = client.put("/api/settings", json=change)
    assert response.status_code == 200, response.text
    assert response.json()["has_key"] is False
    assert db.settings_private()["api_key"] == ""
    assert not json.loads((db.DATA / "settings.json").read_text(encoding="utf-8"))["protected_key"]


def test_model_only_change_keeps_key_but_explicit_replacement_changes_it(client):
    save_connection(client)
    changed = client.put("/api/settings", json={"model": "another-demo-model"})
    assert changed.status_code == 200
    assert db.settings_private()["api_key"] == TEST_KEY
    replacement = "another-test-only-key"
    changed = client.put("/api/settings", json={"provider": "deepseek", "api_key": replacement})
    assert changed.status_code == 200
    assert db.settings_private()["api_key"] == replacement
    assert replacement not in changed.text


def test_testing_a_different_endpoint_does_not_reuse_or_delete_the_saved_key(client, monkeypatch):
    save_connection(client)

    async def never_called(settings):
        raise AssertionError("The old key must not be used with a new endpoint")

    monkeypatch.setattr(api, "test_connection", never_called)
    response = client.post("/api/settings/test", json={"endpoint": "https://other.example.invalid/chat/completions"})
    assert response.status_code == 400
    assert "API Key" in response.json()["detail"]
    assert db.settings_private()["endpoint"] == ENDPOINT
    assert db.settings_private()["api_key"] == TEST_KEY


def test_ollama_without_key_can_start_translation_but_remote_endpoint_cannot(client, monkeypatch):
    saved = save_connection(client, provider="ollama", endpoint="http://localhost:11434/v1/chat/completions", api_key="")
    assert saved["has_key"] is False and saved["requires_key"] is False
    paper = db.refresh_progress({
        "id": "local-paper", "title": "Local Paper", "filename": "local.pdf", "page_count": 1,
        "created_at": "2026-01-01T00:00:00+00:00", "status": "idle", "outline": [], "is_demo": False,
        "paragraphs": [{"id": "p-1", "page": 1, "text": "A test paragraph.", "translation": None, "error": None}],
    })
    db.save_paper(paper)
    calls = []

    async def fake_translate(text, settings):
        calls.append(dict(settings))
        return "本机译文"

    monkeypatch.setattr(api, "translate_text", fake_translate)
    response = client.post("/api/papers/local-paper/translate", json={})
    assert response.status_code == 200, response.text
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        loaded = client.get("/api/papers/local-paper").json()
        if loaded["status"] == "completed":
            break
        time.sleep(.01)
    assert loaded["status"] == "completed"
    assert loaded["paragraphs"][0]["translation"] == "本机译文"
    assert calls[0]["api_key"] == ""
    save_connection(client, provider="ollama", endpoint=ENDPOINT, api_key="")
    paper["id"] = "remote-paper"
    db.save_paper(paper)
    denied = client.post("/api/papers/remote-paper/translate", json={})
    assert denied.status_code == 400
    assert "密钥" in denied.json()["detail"]
    assert len(calls) == 1


@pytest.mark.parametrize("partial", [
    {"provider": "deepseek"},
    {"endpoint": ENDPOINT},
    {"model": "demo-model"},
])
def test_provider_choice_does_not_silently_fill_required_model_or_endpoint(client, partial):
    response = client.put("/api/settings", json=partial)
    assert response.status_code == 400
    assert not (db.DATA / "settings.json").exists()
