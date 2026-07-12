# BIST CONTEXT v0.1 — Sektör & Makro Bağlam Katmanı (üst-aşağı)

## 0. Neden bu katman?

Faktör skoru (SCORING v0.2, kesitsel) ve setups (SETUPS v0.1, olay-tetikli) **aşağı-yukarı**
ve **bağlamsız**dır — "piyasa risk-on mu, hangi sektör lider" bilgisini taşımazlar. Bu katman
eldeki veriden (**DIŞ BAĞIMLILIK YOK**) bir **üst-aşağı bağlam** derler ve setup gücüne
modest bir **tilt** uygular.

**DÜRÜSTLÜK (guardrail):** bu katman **EDGE ÜRETMEZ**. Rejim/sektör tilt'i literatür-temelli
bir **PRIOR**'dır — backtest EDİLMEDİ. Kararı odaklar, bağlamı görünür kılar; kanıt iddia etmez.
(SCORING v0.2 §18.3 dürüstlük ilkesiyle tutarlı.)

---

## 1. Derleme (`app/engine/sector_macro.py`)

Skorlama matematiği **saf fonksiyonlar** (test edilebilir); `compute_market_context` DB'den
adjusted kapanışları toplayıp bu fonksiyonları çağırır. Eş-ağırlık evren = **XU100 vekili**
(tutarlı, PIT). Sonuç `config['market_context']`'e yazılır (batch: `run_scoring.py`).

### Makro rejim (0-100, yüksek = risk-on)
`regime_score(above_ema50, above_ema200, breadth_ema50, vol_20d)`:
50 tabanı ± piyasa trendi (EMA50 ±15, EMA200 ±10) ± genişlik (isimlerin EMA50-üstü oranı, ±20)
− volatilite cezası (20g günlük std, vol_ref=%2 üstü). Etiket: ≥60 **risk_on**, ≤40 **risk_off**,
arası **neutral**. Şeffaf, prior-temelli heuristik.

> Not: USDTRY trendi **bilgilendirici** olarak gösterilir ama rejim skorunu **sürüklemez** —
> BIST TL-nominal olduğundan lira zayıflığının yönü belirsiz (nominal endeksi itebilir).

### Sektör görece-gücü (0-100)
Her sektör (Security.sector ile) için: `rel_strength_20d` (sektör 20g mom − piyasa 20g),
`mom_20d`, `above_ema50` (üye genişliği). Ağırlıklı blend → **mid-rank kesitsel persentil**
(az sektörde bile eşikler temiz oturur). Etiket persentil eşiğiyle: **lider / nötr / geride**.

### Bağlam tilt'i — "kendi skorum" (üst × alt)
`context_tilt(strength, sector_score, regime_score)`:
```
context = strength × (base + span·sektör/100) × (base + span·rejim/100)
        base=0.85, span=0.30 → çarpan ∈ [0.72, 1.32]  (bounded, ±~%30)
```
Lider sektör + risk-on setup gücünü yükseltir; geride sektör + risk-off kısar. Sektör/rejim
yoksa 50 (nötr) kabul edilir. **PRIOR — kanıt değil.**

---

## 2. Kalıcılık + API

- **Config** `market_context` (salt-okunur; batch yazar): `{as_of, macro{...}, sectors[...],
  sector_score{sektör→skor}, tilt_cfg}`.
- **Hook**: `run_scoring.py`, setup taramasından sonra `store_market_context` çağırır
  (refresh.bat hep taze tutar).
- **Config knob** `context` (`seed_config.py`): regime/sector/tilt ağırlıkları (PRIOR, tunable).
- **API** (`app/api/routes/context.py`):
  - `GET /api/context` → derlenmiş makro rejim + sektör tablosu.
  - `GET /api/context/ai` → **ON-DEMAND**: Gemini derlenmiş **SAYILARI** "günün durumu" diye
    yorumlar — **haber/olay UYDURMAZ**, yalnız verilen hesaplanmış bağlamı yorumlar (§18.2).
    Key yoksa `available:false` (sistem çalışır).

---

## 3. Frontend

- **Piyasa & Sektör paneli** (`page.tsx::MarketContextPanel`): rejim rozeti (RISK-ON/NÖTR/
  RISK-OFF) + skor + makro chip'ler (piyasa 20g, genişlik, volatilite, trend, USDTRY) + sektör
  tablosu (skor barı, trend, görece güç) + **on-demand AI "günün durumu"** butonu.
- **Setup kartı bağlam rozeti**: her setup'ta `bağlam: N ▲/▼` — sektör+makro tilt'i uygulanmış
  güç (backend `context_tilt` ile birebir; frontend `contextTilt` yalnız gösterir, hesap yeniden).

---

## 4. Doğrulama

- **Saf fonksiyonlar** birim-test: rejim skoru monotonik/bounded, sektör sıralama+etiket,
  tilt bounded/monotonik/None-nötr (`test_sector_macro.py`).
- **Derleme** smoke-test: `load_daily`/Security/`usdtry_trend` patch'lenerek sentetik 2-sektör
  panelde `compute_market_context` uçtan uca koştu — güçlü sektör geride kalanın üstünde sıralandı.
- 91/91 test geçti; frontend tsc + build temiz. (Canlı DB koşumu SQLite'a geçişten sonra
  `refresh.bat` ile doğrulandı — Docker/Postgres YOK.)
