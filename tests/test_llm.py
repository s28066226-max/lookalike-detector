"""Tests for the model backends (Ollama faked with a stand-in for httpx)."""

import base64

import httpx
import pytest

from detector import llm


class FakeResponse:
    def __init__(self, status_code, data=None, text=""):
        self.status_code = status_code
        self._data = data or {}
        self.text = text

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=None)


@pytest.fixture
def ollama(monkeypatch):
    monkeypatch.setenv("VISION_BACKEND", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "gemma4:e4b")
    calls = []

    def use(*responses):
        queue = list(responses)

        def fake_post(url, json, timeout):
            calls.append(dict(json))
            result = queue.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        monkeypatch.setattr(httpx, "post", fake_post)
        return calls
    return use


def test_ollama_sends_prompt_and_image(ollama):
    calls = ollama(FakeResponse(200, {"response": '{"brand": "sbi"}'}))
    assert llm.generate("which brand?", b"png-bytes") == '{"brand": "sbi"}'
    body = calls[0]
    assert body["model"] == "gemma4:e4b" and body["prompt"] == "which brand?"
    assert body["images"] == [base64.b64encode(b"png-bytes").decode()]
    assert body["stream"] is False and body["think"] is False


def test_ollama_text_only_has_no_images(ollama):
    calls = ollama(FakeResponse(200, {"response": "[]"}))
    llm.generate("explain")
    assert "images" not in calls[0]


def test_ollama_not_running(ollama):
    ollama(httpx.ConnectError("refused"))
    with pytest.raises(llm.ModelUnavailable, match="not running"):
        llm.generate("hi")


def test_ollama_model_not_downloaded(ollama):
    ollama(FakeResponse(404, text="model not found"))
    with pytest.raises(llm.ModelUnavailable, match="ollama pull gemma4:e4b"):
        llm.generate("hi")


def test_ollama_retries_without_think_on_old_versions(ollama):
    calls = ollama(FakeResponse(400, text="unknown field think"), FakeResponse(200, {"response": "ok"}))
    assert llm.generate("hi") == "ok"
    assert "think" in calls[0] and "think" not in calls[1]


def test_unknown_backend(monkeypatch):
    monkeypatch.setenv("VISION_BACKEND", "magic")
    with pytest.raises(llm.ModelUnavailable):
        llm.generate("hi")


def test_default_backend_is_gemini(monkeypatch):
    monkeypatch.delenv("VISION_BACKEND", raising=False)
    assert llm.backend_name() == "gemini"
