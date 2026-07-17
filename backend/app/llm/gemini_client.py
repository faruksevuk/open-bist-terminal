"""LLM istemcisi — Gemini (yerel REST) + OpenAI-uyumlu sağlayıcı (base_url+model). Anahtar-fallback.

Tek giriş noktası: `generate`/`generate_json` aktif sağlayıcıya göre yönlendirir (Ayarlar'dan
seçilir; DB config 'ai_provider'). Yalnızca NİTELİKSEL yorum için (KAP/haber, ticker). Skor/risk/
boyut ASLA LLM'e sorulmaz (v0.2 §0.1). Anahtar yoksa GeminiUnavailable → sistem skoru deterministik
üretmeye devam eder. (Modül adı tarihsel; OpenAI-uyumlu yol için bkz. openai_client.py.)
"""

from __future__ import annotations

import json
import logging
import re
import time

import requests

from app.config import get_settings

_TRANSIENT = {429, 500, 502, 503, 504}  # geçici → retry; 400/403 kalıcı → key atla

log = logging.getLogger(__name__)

# GÜVENLİK: requests HTTPError mesaji URL'yi (?key=...) icerir → hata metnine/loga anahtar
# SIZAR. README garantisi "anahtarlar loglanmaz". Her disari-cikan hata metnini bundan gecir.
_KEY_RE = re.compile(r"key=[\w.\-]+")


def _scrub(msg: object) -> str:
    return _KEY_RE.sub("key=***", str(msg))

# gemini-flash-latest: free-tier'da çalışan güncel model (2.0-flash kota-dışı, 1.5 kaldırıldı).
DEFAULT_MODEL = "gemini-flash-latest"
_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class GeminiUnavailable(Exception):
    """Key yok ya da tüm keyler başarısız."""


# Runtime key'ler — UI'dan (Ayarlar) girilenler; startup'ta DB config 'ai_keys'ten yüklenir.
# Varsa .env'i EZER (open-source akışı: clone → çalıştır → Ayarlar'dan key yapıştır, .env yok).
_runtime_keys: list[str] | None = None


def set_runtime_keys(keys: list[str] | None) -> None:
    """UI/DB'den gelen key'leri aktive et (None/boş → .env'e geri düş)."""
    global _runtime_keys
    cleaned = [k.strip() for k in (keys or []) if k and k.strip()]
    _runtime_keys = cleaned or None


def _keys() -> list[str]:
    """Aktif key listesi: runtime (UI/DB) varsa o, yoksa .env fallback."""
    return _runtime_keys if _runtime_keys else get_settings().gemini_keys


def active_keys() -> list[str]:
    """Aktif key listesinin KOPYASI — yalnız sunucu-içi (endpoint mask/merge); UI'a SIZMAZ."""
    return list(_keys())


def available() -> bool:
    return bool(_keys())


# --- Aktif saglayici: "gemini" (varsayilan) ya da "openai" (OpenAI-uyumlu) -----
_provider: dict = {"provider": "gemini", "base_url": "", "model": ""}


def set_provider(cfg: dict | None) -> None:
    """Sağlayıcı yapılandırmasını aktive et (provider/base_url/model). UI/DB'den gelir."""
    global _provider
    cfg = cfg or {}
    prov = str(cfg.get("provider") or "gemini").lower()
    _provider = {
        "provider": prov if prov in ("gemini", "openai") else "gemini",
        "base_url": str(cfg.get("base_url") or ""),
        "model": str(cfg.get("model") or ""),
    }


def active_provider() -> dict:
    """Aktif sağlayıcı yapılandırmasının kopyası (endpoint/UI için)."""
    return dict(_provider)


def generate(system: str, user: str, model: str = DEFAULT_MODEL, retries: int = 3,
             json_mode: bool = False, schema: dict | None = None) -> str:
    """Aktif sağlayıcıya göre üret (Gemini ya da OpenAI-uyumlu). Anahtar yok/tükendi → GeminiUnavailable."""
    if not _keys():
        raise GeminiUnavailable("AI API anahtarı yok — Ayarlar'dan ekle")
    if _provider.get("provider") == "openai":
        from app.llm import openai_client
        try:
            return openai_client.generate(
                system, user, keys=_keys(),
                model=_provider.get("model") or openai_client.DEFAULT_MODEL,
                base_url=_provider.get("base_url") or openai_client.DEFAULT_BASE_URL,
                retries=retries, json_mode=json_mode)
        except openai_client.LLMError as exc:
            raise GeminiUnavailable(str(exc)) from exc
    return _gemini_generate(system, user, model=model, retries=retries,
                            json_mode=json_mode, schema=schema)


def _call_one(key: str, model: str, system: str, user: str, timeout: int = 30,
              json_mode: bool = False, schema: dict | None = None) -> str:
    gen: dict = {"temperature": 0.2 if json_mode else 0.4, "maxOutputTokens": 2048}
    if json_mode:
        gen["responseMimeType"] = "application/json"  # geçerli JSON garantisi
        if schema:
            gen["responseSchema"] = schema  # yapılandırılmış çıktı → şema-uyumlu JSON GARANTİ
    body = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": gen,
    }
    r = requests.post(
        _URL.format(model=model), params={"key": key}, json=body, timeout=timeout
    )
    r.raise_for_status()
    data = r.json()
    parts = data["candidates"][0]["content"].get("parts", [])
    # düşünme-modeli birden çok part dönebilir → 'text' içerenleri birleştir
    text = "".join(p["text"] for p in parts if isinstance(p, dict) and "text" in p)
    if not text:
        raise GeminiUnavailable("Gemini boş yanıt")
    return text


# Model zinciri: birincil (flash) free-tier kotası (429) bitince lite'a düş — lite'ın AYRI
# kotası var, bu yüzden flash tükendiğinde bile AI çalışmaya devam eder (2026-07 ölçüldü).
_MODEL_CHAIN = ["gemini-flash-latest", "gemini-flash-lite-latest"]


def _run_model(model: str, keys: list[str], system: str, user: str, retries: int,
               json_mode: bool, schema: dict | None) -> str:
    """Tek model: N-anahtar fallback. 5xx → backoff-retry; 429 (kota) → beklemeden bu modeli bırak.

    429 günün kotası tükendi demek — 1.5s backoff'la düzelmez; hemen fail edip çağıran sıradaki
    modele (lite) düşsün (aksi halde her istekte ~4.5s + boşa 429 çağrıları harcanır).
    """
    last: Exception | None = None
    for attempt in range(retries):
        saw_transient = False  # yalnız 5xx/ağ → backoff-retry anlamlı
        for i, key in enumerate(keys, 1):
            try:
                return _call_one(key, model, system, user, json_mode=json_mode, schema=schema)
            except requests.HTTPError as exc:
                code = exc.response.status_code if exc.response is not None else None
                last = exc
                if code == 429:
                    continue  # kota — sıradaki key'i dene; hepsi 429 ise modeli bırak (backoff yok)
                if code in _TRANSIENT:
                    saw_transient = True
                else:  # kalıcı (geçersiz key/istek) → key atla, retry etme
                    log.warning("Gemini %s key#%d kalıcı hata %s", model, i, code)
            except Exception as exc:  # noqa: BLE001
                last = exc
                saw_transient = True  # ağ/timeout vb. → retry anlamlı
        if not saw_transient:
            break  # 429/kalıcı → backoff boşa; çağıran sıradaki modele düşsün
        if attempt < retries - 1:
            time.sleep(1.5 * (attempt + 1))  # backoff yalnız 5xx/ağ: 1.5s, 3s
    raise GeminiUnavailable(f"{model}: {len(keys)} key başarısız: {_scrub(last)}")


def _gemini_generate(system: str, user: str, model: str = DEFAULT_MODEL, retries: int = 3,
                     json_mode: bool = False, schema: dict | None = None) -> str:
    """Gemini REST — MODEL zinciri (flash→lite, quota bölünür) × N-anahtar × geçici-hata retry."""
    keys = _keys()
    if not keys:
        raise GeminiUnavailable("Gemini API key yok — Ayarlar'dan ekle ya da .env GEMINI_API_KEY_1..4")
    models = [model] + [m for m in _MODEL_CHAIN if m != model]
    last: Exception | None = None
    for mdl in models:
        try:
            return _run_model(mdl, keys, system, user, retries, json_mode, schema)
        except GeminiUnavailable as exc:
            last = exc
            log.info("Gemini model %s kullanılamadı → sıradaki modele düşülüyor", mdl)
    raise last or GeminiUnavailable("tüm modeller başarısız")


def _call_one_grounded(key: str, model: str, system: str, user: str, timeout: int = 45) -> dict:
    """Google Search grounding'li TEK cagri → {text, citations, queries, rendered_suggestions}.

    Grounding = `tools:[{google_search:{}}]` (native v1beta, 2.x flash). JSON mode ile BIRLIKTE
    KULLANILMAZ (mutually exclusive) → daima serbest metin; yapiyi sonra ayri cikaririz.
    Parse SAVUNMACI: groundingMetadata alanlari degisirse citation bos doner ama metin akar.
    """
    body = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "tools": [{"google_search": {}}],  # <-- gercek-dunya temellendirme (NATO/Fed vb.)
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 2048},
    }
    r = requests.post(_URL.format(model=model), params={"key": key}, json=body, timeout=timeout)
    r.raise_for_status()
    cand = r.json()["candidates"][0]
    parts = cand["content"].get("parts", [])
    text = "".join(p["text"] for p in parts if isinstance(p, dict) and "text" in p)
    if not text:
        raise GeminiUnavailable("Gemini bos yanit (grounded)")
    gm = cand.get("groundingMetadata") or {}
    citations: list[dict] = []
    for c in gm.get("groundingChunks") or []:
        w = (c or {}).get("web") or {}
        if w.get("uri"):
            citations.append({"title": w.get("title") or w["uri"], "uri": w["uri"]})
    return {
        "text": text,
        "citations": citations,  # [{title, uri}] — kaynak linkleri (dogrulama icin sart)
        "queries": gm.get("webSearchQueries") or [],  # AI'in web'de aradigi sorgular
        "rendered_suggestions": (gm.get("searchEntryPoint") or {}).get("renderedContent") or "",
    }


def _run_grounded_model(model: str, keys: list[str], system: str, user: str,
                        retries: int) -> dict:
    """Tek model grounded: N-anahtar fallback; 429 → beklemeden modeli bırak (kota mantığı
    _run_model ile aynı — 429 backoff'la düzelmez, çağıran sıradaki modele düşsün)."""
    last: Exception | None = None
    for attempt in range(retries):
        saw_transient = False
        for i, key in enumerate(keys, 1):
            try:
                return _call_one_grounded(key, model, system, user)
            except requests.HTTPError as exc:
                code = exc.response.status_code if exc.response is not None else None
                last = exc
                if code == 429:
                    continue  # kota — sıradaki key; hepsi 429 ise modeli bırak
                if code in _TRANSIENT:
                    saw_transient = True
                else:
                    log.warning("Gemini grounded %s key#%d kalıcı hata %s", model, i, code)
            except Exception as exc:  # noqa: BLE001
                last = exc
                saw_transient = True
        if not saw_transient:
            break
        if attempt < retries - 1:
            time.sleep(1.5 * (attempt + 1))
    raise GeminiUnavailable(f"grounded {model}: {len(keys)} key başarısız: {_scrub(last)}")


def grounded_generate(system: str, user: str, model: str = DEFAULT_MODEL, retries: int = 2) -> dict:
    """Gerçek-dünya temellendirmeli üret (Google Search). SADECE Gemini sağlayıcıda.

    {text, citations, queries, rendered_suggestions} döndürür. MODEL ZİNCİRİ uygulanır
    (flash→lite; 2026-07-17 canlı ölçüm: flash 429 iken lite grounding'i AYRI kotayla
    taşıyabiliyor). Anahtar/kota tükendi → GeminiUnavailable (çağıran zarifçe atlar —
    uydurma YOK, kaynaksız iddia YOK).
    """
    keys = _keys()
    if not keys:
        raise GeminiUnavailable("AI API anahtarı yok — Ayarlar'dan ekle")
    models = [model] + [m for m in _MODEL_CHAIN if m != model]
    last: Exception | None = None
    for mdl in models:
        try:
            return _run_grounded_model(mdl, keys, system, user, retries)
        except GeminiUnavailable as exc:
            last = exc
            log.info("Gemini grounded %s kullanılamadı → sıradaki modele düşülüyor", mdl)
    raise last or GeminiUnavailable("grounded: tüm modeller başarısız")


def extract_json(txt: str) -> dict:
    """LLM metninden ilk JSON NESNESİni sağlamca çıkar (saf; test edilebilir).

    Toleranslar: markdown fence (```json), önde açıklama metni, ve JSON'dan SONRA fazladan
    veri (thinking-model bazen nesneyi iki kez ya da arkasına metin döndürür — eski greedy
    regex `\\{.*\\}` bunu tek geçersiz bloğa birleştiriyordu). raw_decode ilk geçerli nesneyi
    alır, kalanı yok sayar. Nesne bulunamazsa JSONDecodeError.
    """
    txt = (txt or "").strip()
    if txt.startswith("```"):
        txt = txt.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        pass
    start = txt.find("{")
    if start >= 0:
        obj, _end = json.JSONDecoder().raw_decode(txt[start:])  # ilk nesne; fazlayı yok say
        if isinstance(obj, dict):
            return obj
    raise json.JSONDecodeError("JSON nesnesi bulunamadı", txt, 0)


def generate_json(system: str, user: str, model: str = DEFAULT_MODEL,
                  schema: dict | None = None) -> dict:
    """JSON döndüren çağrı. schema verilirse responseSchema ile şema-uyumlu JSON garanti."""
    return extract_json(generate(system, user, model, json_mode=True, schema=schema))
