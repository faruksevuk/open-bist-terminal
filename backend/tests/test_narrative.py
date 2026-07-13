"""narrative engine — meta ayiklama + build_note (grounded-or-silent) testleri (DB'siz)."""

from datetime import date

from app.llm.narrative import _parse_meta, build_note


def test_parse_meta_extracts_and_cleans():
    text = ('ASELS savunma temasindan olumlu etkilenebilir.\n'
            '{"direction":"up","horizon_days":5,"tickers":["asels","otkar"],"confidence":0.6}')
    prose, meta = _parse_meta(text)
    assert "ASELS savunma" in prose and "{" not in prose  # JSON prozadan cikti
    assert meta["direction"] == "up"
    assert meta["horizon_days"] == 5
    assert meta["tickers"] == ["ASELS", "OTKAR"]           # upper + liste
    assert meta["confidence"] == 0.6


def test_parse_meta_defaults_when_no_json():
    prose, meta = _parse_meta("Belirgin gelisme yok.")
    assert prose == "Belirgin gelisme yok."
    assert meta["direction"] == "neutral" and meta["horizon_days"] == 5
    assert meta["tickers"] == [] and meta["confidence"] is None


def test_parse_meta_validates_and_clamps():
    _, meta = _parse_meta('x\n{"direction":"YUKARI","horizon_days":999,"tickers":["a"],"confidence":9}')
    assert meta["direction"] == "neutral"   # gecersiz yon -> neutral
    assert meta["horizon_days"] == 60        # 1..60 clamp
    assert meta["confidence"] == 1.0         # 0..1 clamp


def test_build_note_requires_citations():
    """grounded-or-silent: kaynak yoksa tez SAKLANMAZ (None)."""
    focus = {"scope_type": "macro", "scope": "savunma"}
    res = {"text": 'iyi\n{"direction":"up","tickers":["ASELS"]}', "citations": [], "queries": []}
    assert build_note(None, focus, res, date(2026, 7, 13)) is None


def test_build_note_with_citations(monkeypatch):
    monkeypatch.setattr("app.llm.narrative._last_close", lambda s, t: 100.0)
    focus = {"scope_type": "macro", "scope": "savunma"}
    res = {"text": ('ASELS olumlu.\n'
                    '{"direction":"up","horizon_days":5,"tickers":["ASELS"],"confidence":0.6}'),
           "citations": [{"title": "X", "uri": "http://x"}], "queries": ["asels haber"]}
    n = build_note(None, focus, res, date(2026, 7, 13))
    assert n is not None
    assert n.primary_ticker == "ASELS" and n.entry_close == 100.0
    assert n.direction == "up" and n.status == "pending"
    assert n.citations and n.tickers == ["ASELS"]


def test_build_note_ticker_scope_prepends_self(monkeypatch):
    monkeypatch.setattr("app.llm.narrative._last_close", lambda s, t: 50.0)
    focus = {"scope_type": "ticker", "scope": "THYAO"}
    res = {"text": ('THYAO baglami.\n'
                    '{"direction":"down","horizon_days":30,"tickers":["PGSUS"],"confidence":0.4}'),
           "citations": [{"title": "Y", "uri": "http://y"}], "queries": []}
    n = build_note(None, focus, res, date(2026, 7, 13))
    assert n.primary_ticker == "THYAO"      # ticker scope kendini basa ekler
    assert "THYAO" in n.tickers and n.horizon_days == 30
