"""Gemini istemci testleri (offline) — key-yok zarif davranışı + JSON parse."""

from __future__ import annotations

import pytest

from app.llm import gemini_client as g


def test_no_keys_raises(monkeypatch):
    class S:
        gemini_keys: list[str] = []

    monkeypatch.setattr(g, "get_settings", lambda: S())
    assert g.available() is False
    with pytest.raises(g.GeminiUnavailable):
        g.generate("sistem", "kullanıcı")


def test_json_fence_strip(monkeypatch):
    monkeypatch.setattr(g, "generate", lambda *a, **k: '```json\n{"direction": 1}\n```')
    assert g.generate_json("s", "u") == {"direction": 1}


def test_json_regex_fallback(monkeypatch):
    # JSON öncesi/sonrası gürültü olsa bile ilk {...} bloğu yakalanır
    monkeypatch.setattr(g, "generate", lambda *a, **k: 'Düşünce: ... {"direction": -1, "magnitude": 0.5} son')
    assert g.generate_json("s", "u")["direction"] == -1


def test_available_true_with_keys(monkeypatch):
    class S:
        gemini_keys = ["AIzaTEST"]

    monkeypatch.setattr(g, "get_settings", lambda: S())
    assert g.available() is True


# --- extract_json (LLM JSON ayrıştırma sağlamlığı) — saf, ağ yok --------

def test_extract_json_plain():
    from app.llm.gemini_client import extract_json
    assert extract_json('{"a": 1, "b": 2}') == {"a": 1, "b": 2}


def test_extract_json_fenced():
    from app.llm.gemini_client import extract_json
    assert extract_json('```json\n{"x": 5}\n```') == {"x": 5}


def test_extract_json_extra_data_after():
    """Thinking-model JSON'dan SONRA fazladan veri/metin döndürünce ilk nesne alınmalı
    (eski greedy regex burada patlıyordu — 'Extra data' hatası)."""
    from app.llm.gemini_client import extract_json
    txt = '{"direction": 0.5, "type": "temettu"}\n\nAçıklama: bu bir temettü bildirimidir.'
    assert extract_json(txt) == {"direction": 0.5, "type": "temettu"}


def test_extract_json_double_object():
    """İki nesne art arda → İLKİ alınır (fazlası yok sayılır)."""
    from app.llm.gemini_client import extract_json
    assert extract_json('{"a": 1}\n{"a": 2}') == {"a": 1}


def test_extract_json_leading_text():
    from app.llm.gemini_client import extract_json
    assert extract_json('İşte sonuç:\n{"ok": true}') == {"ok": True}


def test_extract_json_none_raises():
    import json
    import pytest
    from app.llm.gemini_client import extract_json
    with pytest.raises(json.JSONDecodeError):
        extract_json("hiç json yok burada")
