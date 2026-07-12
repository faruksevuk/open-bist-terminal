"""OpenAI-uyumlu LLM istemcisi — OpenAI / DeepSeek / Groq / yerel (Ollama, LM Studio) vb.

Chat Completions API (base_url + model + anahtar). Yalnızca NİTELİKSEL yorum için; skor/risk/
boyut ASLA LLM'e sorulmaz. json_mode'da response_format=json_object (yaygın uyumlu) kullanılır;
şema prompt ile zorlanır, çağıran taraf extract_json ile güvenli parse eder.
"""

from __future__ import annotations

import logging
import time

import requests

_TRANSIENT = {429, 500, 502, 503, 504}  # geçici → retry; 4xx (kota/geçersiz) → anahtar atla

log = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_BASE_URL = "https://api.openai.com/v1"  # DeepSeek: https://api.deepseek.com ; yerel: http://localhost:11434/v1


class LLMError(Exception):
    """OpenAI-uyumlu çağrı başarısız (anahtar yok ya da tüm anahtarlar başarısız)."""


def _call_one(key: str, base_url: str, model: str, system: str, user: str,
              timeout: int = 30, json_mode: bool = False) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    body: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2 if json_mode else 0.4,
        "max_tokens": 2048,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}  # geçerli JSON (yaygın uyumlu)
    r = requests.post(url, headers={"Authorization": f"Bearer {key}"}, json=body, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    text = (data["choices"][0]["message"].get("content") or "").strip()
    if not text:
        raise LLMError("OpenAI-uyumlu boş yanıt")
    return text


def generate(system: str, user: str, keys: list[str], model: str = DEFAULT_MODEL,
             base_url: str = DEFAULT_BASE_URL, retries: int = 3, json_mode: bool = False) -> str:
    """Anahtar-zinciri fallback + geçici hata (429/5xx) retry+backoff. Tükenirse LLMError."""
    keys = [k for k in (keys or []) if k]
    if not keys:
        raise LLMError("OpenAI-uyumlu API anahtarı yok")
    model = model or DEFAULT_MODEL
    base_url = base_url or DEFAULT_BASE_URL
    last: Exception | None = None
    for attempt in range(retries):
        saw_transient = False
        for i, key in enumerate(keys, 1):
            try:
                return _call_one(key, base_url, model, system, user, json_mode=json_mode)
            except requests.HTTPError as exc:
                code = exc.response.status_code if exc.response is not None else None
                last = exc
                if code in _TRANSIENT:
                    saw_transient = True
                else:  # kalıcı (geçersiz anahtar/model) → anahtar atla, retry etme
                    log.warning("OpenAI-uyumlu anahtar#%d kalıcı hata %s", i, code)
            except Exception as exc:  # noqa: BLE001 — ağ/timeout vb. → retry anlamlı
                last = exc
                saw_transient = True
        if not saw_transient:
            break
        if attempt < retries - 1:
            time.sleep(1.5 * (attempt + 1))
            log.info("OpenAI-uyumlu geçici hata, retry %d/%d", attempt + 2, retries)
    raise LLMError(f"tüm {len(keys)} anahtar başarısız ({retries} deneme): {last}")
