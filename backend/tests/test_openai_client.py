"""OpenAI-uyumlu istemci + saglayici router birim testleri (mock requests — ag yok)."""

from __future__ import annotations

import pytest
import requests

from app.llm import gemini_client, openai_client


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            err = requests.HTTPError(f"HTTP {self.status_code}")
            err.response = self
            raise err


def test_generate_happy(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured.update(url=url, auth=headers.get("Authorization"), body=json)
        return _Resp({"choices": [{"message": {"content": '{"ok": true}'}}]})

    monkeypatch.setattr(requests, "post", fake_post)
    out = openai_client.generate("sys", "usr", keys=["sk-abc"], model="m",
                                 base_url="https://api.example.com/v1", json_mode=True)
    assert out == '{"ok": true}'
    assert captured["url"] == "https://api.example.com/v1/chat/completions"
    assert captured["auth"] == "Bearer sk-abc"
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert captured["body"]["model"] == "m"


def test_generate_no_keys():
    with pytest.raises(openai_client.LLMError):
        openai_client.generate("s", "u", keys=[])


def test_generate_fallback_second_key(monkeypatch):
    calls = {"n": 0}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls["n"] += 1
        if headers["Authorization"] == "Bearer bad":
            return _Resp({}, status=401)  # kalici → siradaki anahtar
        return _Resp({"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(requests, "post", fake_post)
    out = openai_client.generate("s", "u", keys=["bad", "good"])
    assert out == "ok"
    assert calls["n"] == 2


def test_router_dispatches_to_openai(monkeypatch):
    """gemini_client.generate aktif saglayici 'openai' iken openai_client'a yonlendirir."""
    monkeypatch.setattr(gemini_client, "_runtime_keys", ["k1"], raising=False)
    monkeypatch.setattr(openai_client, "generate", lambda *a, **k: "routed-openai")
    gemini_client.set_provider({"provider": "openai", "base_url": "https://x/v1", "model": "m"})
    try:
        assert gemini_client.generate("s", "u") == "routed-openai"
        assert gemini_client.active_provider()["provider"] == "openai"
    finally:
        gemini_client.set_provider({"provider": "gemini"})  # global state reset


def test_router_default_is_gemini():
    gemini_client.set_provider(None)
    assert gemini_client.active_provider()["provider"] == "gemini"
