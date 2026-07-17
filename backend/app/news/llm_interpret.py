"""KAP açıklamasını Gemini ile yapısal değerlendirmeye çevir (v0.2 §18.1).

LLM yalnızca NİTELİKSEL: yön/büyüklük/confidence/tip/mekanizma. Fiyat/skor tahmin etmez,
sayı uydurmaz. Defansif/asimetrik: negatif tam, pozitif temkinli. Key yoksa None döner.
"""

from __future__ import annotations

import logging

from app.llm import gemini_client

log = logging.getLogger(__name__)

SYSTEM = (
    "Sen bir BIST açıklama-analiz motorusun. Sana bir KAP açıklamasının başlığı+özeti ve "
    "(varsa) açıklama GÖVDESİ ile ilgili hisse verilecek. Görevin SADECE yapısal bir "
    "değerlendirme döndürmek. Gövde varsa somut bilgiye (tutar, oran, taraf, süre) dayan; "
    "magnitude'u ölçeğe göre ver. Fiyat/skor TAHMİN ETME, sayı uydurma. Yalnızca açıklamanın "
    "metnine dayan. Negatif material etkilerde tam ölçek; pozitiflerde temkinli ol. "
    "Emin değilsen confidence düşür.\n\n"
    "YALNIZCA şu JSON'u döndür (markdown/yorum yok):\n"
    '{"direction": <-1..1 float>, "magnitude": <0..1 float>, "confidence": <0..1 float>, '
    '"type": "<temettu|bedelli|bedelsiz|finansal_tablo|pay_geri_alim|yonetici_islem|'
    'onemli_sozlesme|spk|diger>", "duration_days": <int>, "mechanism": "<kısa Türkçe>"}'
)

_VALID_TYPES = {"temettu", "bedelli", "bedelsiz", "finansal_tablo", "pay_geri_alim",
                "yonetici_islem", "onemli_sozlesme", "spk", "diger"}

# Gemini responseSchema — şema-uyumlu GEÇERLİ JSON garantisi (bozuk-JSON parse hatalarını bitirir).
_SCHEMA = {
    "type": "object",
    "properties": {
        "direction": {"type": "number"},
        "magnitude": {"type": "number"},
        "confidence": {"type": "number"},
        "type": {"type": "string", "enum": sorted(_VALID_TYPES)},
        "duration_days": {"type": "integer"},
        "mechanism": {"type": "string"},
    },
    "required": ["direction", "magnitude", "confidence", "type", "duration_days", "mechanism"],
}


def _clamp(v, lo, hi, default=0.0):
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return default


def interpret(disc: dict) -> dict | None:
    """Açıklama dict'i → değerlendirme dict'i. Key yok/parse hatası → None."""
    user = (
        f"Hisse: {disc.get('ticker')}\nKAP sınıfı: {disc.get('kap_class')}\n"
        f"Başlık: {disc.get('title')}\nÖzet: {disc.get('summary') or '-'}"
    )
    if disc.get("body"):
        user += f"\nAçıklama gövdesi (kırpılmış):\n{disc['body']}"
    try:
        out = gemini_client.generate_json(SYSTEM, user, schema=_SCHEMA)
    except gemini_client.GeminiUnavailable:
        return None
    except Exception as exc:  # noqa: BLE001 — parse/JSON hatası
        log.warning("KAP yorum parse hatası (%s): %s", disc.get("ticker"), exc)
        return None

    typ = str(out.get("type", "diger")).lower()
    return {
        "direction": _clamp(out.get("direction"), -1, 1),
        "magnitude": _clamp(out.get("magnitude"), 0, 1),
        "confidence": _clamp(out.get("confidence"), 0, 1),
        "type": typ if typ in _VALID_TYPES else "diger",
        "duration_days": int(_clamp(out.get("duration_days", 7), 1, 120, 7)),
        "mechanism": str(out.get("mechanism", ""))[:300],
    }
