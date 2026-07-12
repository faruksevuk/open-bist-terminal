# BIST Karar-Destek Sistemi — SCORING & RISK v0.2 (TEK YETKİLİ MODEL)

**Sürüm:** v0.2 · **Ufuk:** ~1 hafta (swing) + gün-sonu (daily) · **Hesap:** ~10.000 TL · **Veri:** 15 dk gecikmeli
**Durum:** Bu doküman, `BIST-SCORING-METHODOLOGY.md` (v0.1) ve `MASTER-BUILD-SPEC.md` §8'deki skor matematiğini **birlikte değiştirir (supersedes)**. Çelişkide **bu doküman > methodology v0.1**. Build sırası ve mimari için `MASTER-BUILD-SPEC.md` §3-§17 geçerli kalır; bu doküman yalnızca skor + risk + kalibrasyon matematiğinin tek kaynağıdır. Dürüstlük guardrail'leri (`MASTER-BUILD-SPEC.md` §2, §18.3) **ihlal edilemez** ve §13'te güncellenmiş haliyle taşınır.

> **Neden v0.2?** v0.1 iki çelişen skor modeli tanımlıyordu: methodology `T/M/N/R` (momentum + beklenen-hareket), spec `kalite/oversold/sebep/stabilizasyon` (reversal-on-quality). v0.2 **tek model** kurar: reversal-on-quality esas; `T/M/N/R` sembolleri tamamen emekliye ayrılır (crosswalk §14). Beklenen-hareket (eski M) **ödül değil, evren TABANI** olarak geri gelir.

---

## 0. Felsefe ve çerçeve

### 0.1 Değişmez ilkeler (taşındı, ihlal edilemez)
1. **LLM önerir, deterministik kod karar verir.** Skorun içindeki hiçbir sayı LLM'e bırakılmaz. LLM yalnızca (a) KAP/haber olay **tipi + niteliksel ton/yön**, (b) istek üzerine ticker yorumu üretir. Earnings-surprise dahil her sayı koddadır (§7).
2. **Nakit bir pozisyondur.** Mutlak kapıyı geçen setup yoksa "bugün setup yok, nakitte kal". Her gün "Al" üretmek yasak.
3. **Risk = boyut, seçim değil.** Volatil/lotto/junk kovalanmaz. Volatilite asla **pozitif skor bileşeni** değildir (MAX/low-vol anomalisi). Beklenen-hareket yalnızca **taban kapısı** olarak kullanılır (§2.5).
4. **Backtest beklentisini yarıla.** Hiçbir ağırlık "backtest'te en iyi" diye taşa yazılmaz; az bileşen, az deneme, walk-forward, deneme-sayan kalibrasyon (§9).
5. **Reel/USD P&L her zaman gösterilir.** Nominal TRY tek başına yanıltıcıdır.
6. **Sert aşağı-yönlü limitler kodda enforce.** Trade-başı risk, heat, günlük/haftalık drawdown; LLM gevşetemez.
7. **Hassas sonuçları abartma.** Hata → logla, sustur, bayatlığı işaretle.

### 0.2 Hedef çerçevesi (dürüst)
Hedef +%7,5/hafta bir **aspirasyon/benchmark**'tır, mandate değil. Sayısal gerçek (değişmez kayıt): +%7,5/hafta = 1.075⁵² ≈ **43×/yıl (~%4200)** — hiçbir gerçek strateji bunun bir basamak yakınına sürdürülebilir biçimde gelmemiştir. Dahası, bu sistemin **kendi risk bütçesi** onu yapısal olarak dışlar: heat bağlayıcıyken yatırılan sermaye ≈ `özsermaye × heat / stop_oranı` (varsayılanlarda ~%60) değerine sabitlenir; +%7,5 net için her hafta ~+1,25R/işlem (≈ tüm pozisyonlarda eşzamanlı >1σ yukarı) gerekir. Gerçekçi beklenti **~+%1,5/hafta**, çoğu hafta düşük-aktivite/nakit.

**Karar:** Hedef dokümanda dürüstlük notu olarak kalır; **UI'dan ve "gün başına gereken tempo" hesabından kaldırılır** (§6 ve `MASTER-BUILD-SPEC.md` §5/§14.2'ye uygulanacak değişiklik). Yerine reel-getiri benchmark'ı + risk/ruin paneli (§6).

---

## 1. Katman mimarisi (tek model)

| Sıra | Katman | Sembol | Tür | Rejim | Ne yapar |
|---|---|---|---|---|---|
| 0 | Evren kapıları | — | Kod (gate) | — | Uygunsuz isimleri eler (likidite, limit-lock, veri, tedbir, **beklenen-hareket tabanı**, kalite) |
| 1 | Kalite | `sub_quality` | Kod 0–100 | **Mutlak** | Piotroski F + accrual + makul değerleme (junk/value-trap eler) |
| 2 | Oversold | `sub_oversold` | Kod 0–100 | **Evren-içi z-score/percentile** | Kendi trendine göre "kâr-satışıyla düşmüş" mü |
| 3 | Sebep | `sub_cause` | Kod 0–100 | **Mutlak** | Düşüş temiz mi: pozitif temizlik kanıtı + PEAD işareti + material-olumsuz yokluğu |
| 4 | Stabilizasyon | `sub_stab` | Kod 0–100 | **Evren-içi z-score/percentile** | Dönüş başladı mı (bıçağı havada yakalama) |
| 5 | Haber | `news_pos`, `news_neg` | LLM tip/ton → kod sayı | İşaretli | Aktif KAP/haber net etkisi (asimetrik, decay'li) |
| 6 | Risk valfi | `risk_governor` | Kod 0.2–1.0 | Çarpan | Riskli ismi tepe skordan kısar |

**Rejim notu (§5 kararı):** Kalite ve Sebep **mutlak** (Piotroski ve olay-temelli doğaları gereği). Oversold ve Stabilizasyon **evren-içi göreli** (z-score → percentile) — geniş satış/melt-up'ta sabit eşiğin seçiciliğini kaybetmesini önler.

---

## 2. Evren kapıları (sert — skordan önce)

Bir isim, **tüm** kapıları geçmeden skorlanmaz (aday listesine girmez):

- **2.1 Likidite:** son 20 gün ort. TL hacim ≥ `min_liq_tl` (başlangıç **50.000.000 TL/gün**). Ölü/manipüle micro-cap'leri eler.
- **2.2 Limit-lock:** an itibarıyla **tavan kilitli** (satış kotasyonu "−", fark ≈ +%10) → alınamaz, elenir. **Taban kilitli** → düşen bıçak, alım önerilmez. (Midas tablosundan doğrudan.)
- **2.3 Veri yeterliliği:** uzun indikatörler için ≥ ~200 işlem günü; yoksa "kısıtlı skor" etiketi veya eleme.
- **2.4 Tedbir/işlem kısıtı:** SPK/Borsa tedbir, brüt takas, işlem yasağı → elenir/işaretlenir.
- **2.5 Beklenen-hareket TABANI (eski M — artık ödül değil, kapı):** `ATR%(14) = ATR/fiyat ≥ min_move_atr_pct` (başlangıç **%2,5**, config). Amaç: haftada hedefe **fiziksel olarak ulaşamayacak ölü-sakin** isimleri (THYAO-tipi düşük-ATR blue-chip) elemek — "büyük marka handikabı" çözümü. **KRİTİK:** Bu bir **binary kapı**, skora pozitif katkı vermez. ATR% ne kadar yüksekse o kadar iyi DEĞİL (yüksek ATR riski risk valfinde §2.6'da ayrıca cezalanır). Yorum: ATR% ≈ %2,5 günlük → ~%5,6 haftalık 1σ; hedefe katkı verebilecek minimum hareket. `min_move_atr_pct` bir **policy-knob** (§10): yükseltmek = daha az ama daha hareketli aday.
- **2.6 Kalite kapısı:** Piotroski **F ≥ `min_fscore`** (başlangıç 5). Banka/sigorta/GYO için F-Score N/A → kalite kapısı atlanır, `sub_quality` farklı muameleyle (sektör-içi değerleme percentile) hesaplanır ve "F-Score yok" etiketi taşınır.

---

## 3. Alt-skorlar (tanım, formül, rejim)

### 3.1 `sub_quality` (0–100, MUTLAK)
Piotroski F-Score (0–9, koddan §7'deki point-in-time kuralıyla) normalize + düşük accrual + bağlama-göre makul değerleme (F/K, PD/DD sektör-içi percentile).
```
sub_quality = 100 · ( wf·(F/9) + wa·accrual_score + wv·valuation_score )
   wf, wa, wv: prior (başlangıç 0.6 / 0.2 / 0.2), toplam 1.0  — §10
   accrual_score   = 1 − minmax(accrual_ratio)   # düşük accrual iyi
   valuation_score = 1 − sektör_içi_percentile(F/K, PD/DD karması)  # ucuz iyi, junk değil
```
*Gate + bileşen; Piotroski mutlak olduğu için rejimden bağımsız tutulur.*

### 3.2 `sub_oversold` (0–100, EVREN-İÇİ GÖRELİ)
Kendi trendine/ortalamasına göre düşüklük. Ham bileşenler hesaplanır, **evren içinde z-score'lanır, sonra percentile'e (0–100) çevrilir**:
```
ham_oversold = z( −dist_50EMA ) + z( −RSI14 ) + z( drawdown_from_20d_high ) + z( dist_from_recent_peak )
sub_oversold = percentile_in_universe( ham_oversold )   # 0–100, her skor döngüsünde yeniden
```
- `dist_50EMA = (fiyat − EMA50)/EMA50` (negatif uzaklık ödüllenir → işareti ters).
- Tüm seriler **adjusted** (corporate-action; §9.2).
- **Rejim:** Göreli olduğu için geniş satışta tüm evren birden "oversold" görünüp kapıyı sele vermez.

### 3.3 `sub_cause` (0–100, MUTLAK) — EN KRİTİK, "neden düştü" motoru
v0.1'deki "son 10 günde material-olumsuz KAP yok = temiz" **yetersiz** (yokluk ≠ temizlik; kötü haber KAP'tan önce/dışında olabilir). v0.2 **pozitif temizlik kanıtı** ister:
```
sub_cause = 100 · clamp( base_clean + pead_term − idiosyncratic_penalty , 0, 1 )
```
- **`base_clean` (pozitif temizlik):** düşüş sektör/endeks geneliyle örtüşüyorsa (rotasyon/genel kâr-satışı) yüksek. `base_clean = clamp( corr_move_with_sector , 0, 1 )` — ismin son düşüşünün sektör/XU100 ile ko-hareket oranı. Sadece "KAP sessiz" yetmez; **aktif** ko-hareket kanıtı aranır.
- **`pead_term` (kazanç-sürprizi İŞARETİ, koddan §7):** son `finansal_tablo` SUE işareti pozitif/nötr → +; negatif → 0'a yakın (tuzak). PEAD yukarı-drift = temiz düşüş onayı.
- **`idiosyncratic_penalty` (yeni — kırmızı bayrak):** isim sert düştü ama sektör/endeks DÜŞMEDİYSE ve pozitif katalist yoksa → "biri bir şey biliyor" cezası. `idiosyncratic_penalty = clamp( own_drop − sector_drop , 0, 1 ) · (pozitif_katalist yoksa 1 else 0)`.
- **Material-olumsuz KAP/haber:** aktif material-negatif olay varsa `sub_cause` doğrudan ~0'a çekilir (hard override). Lookback sabit 10 gün değil; olay `effective_until`'ine bağlı (§8) — hâlâ aktif negatif `finansal_tablo` (60g penceresi) `sub_cause`'u bastırır.

### 3.4 `sub_stab` (0–100, EVREN-İÇİ GÖRELİ)
Dönüş başladı mı: düşüşte hacim kuruyor, reversal mum, RSI dönüyor, dip yapmayı bıraktı, destek tutuyor.
```
ham_stab = z(volume_dryup) + z(rsi_turning_up) + z(higher_low_formed) + z(reversal_candle_strength)
sub_stab = percentile_in_universe( ham_stab )   # 0–100
```
- **Rejim:** Göreli; melt-up'ta yapay yükselmez. Geniş satışta bile yalnızca *gerçekten dönmüş* isimler yüksek alır (oversold ile birlikte kapı = bıçak filtresi).

### 3.5 Haber (`news_pos` ∈ [0, +12], `news_neg` ∈ [−20, 0]) — asimetrik
LLM **yalnızca tip + niteliksel yön/ton + confidence + materiality** üretir (sayı uydurmaz; §0.1, §7). Kod sayıya çevirir:
```
raw_news = işaret × min( cap_yön, materiality_ağırlığı × confidence ) × decay(olay_tipi, t)
news_pos = max(0, raw_news toplamı pozitif kısım)   # cap +12
news_neg = min(0, raw_news toplamı negatif kısım)    # cap −20
```
- **Asimetri (defansif):** negatif tam (−20'ye kadar), pozitif düşük tavan (+12). BIST küçük-cap pump'ı pozitif okumaları gürültülü yapar.
- **Pozitif disclosure-kalite kapısı (review):** magnitude disclosed bir sayıya (TL değer, ciro %'si) bağlanamıyorsa pozitif katkı +12 değil **+4 ile sınırlı** + "düşük-confidence" etiketi.
- **Decay per-tip (§8):** global tek yarı-ömür YOK; PEAD yavaş, temettü ex-date'e kilitli.
- **İkinci-derece:** §8 ilişki grafiği; küçük ağırlık, freshness-kapılı.

### 3.6 `risk_governor` (0.2–1.0, çarpan)
1.0'dan başlar, her bayrak için aşağı çarpılır (taban 0.2). **Tüm çarpanlar prior — kalibre edilecek (§10).**

| Bayrak | Çarpan (prior) | Sebep |
|---|---|---|
| RSI > `rsi_overbought` (80) | ×0.6 | aşırı-alım, reversal riski |
| Bugün > +%8 / tavana çok yakın | ×0.5 | kalan alan yok, son %'yi kovalama |
| ATR% > `extreme_atr_pct` (%12) | ×0.6 | gap/manipülasyon (lotto) |
| Binary olay holding penceresinde, istenmiyor | ×0.5 | coin-flip maruziyeti |
| Likidite marjinal (kapıyı geçti ama ince) | ×0.7 | çıkış zorluğu |

Çarpanlar birikir; ~3 bayrakta zaten 0.2 tabanına çakılır (bilinçli: "çok riskli" ≈ "felaket riskli" → reddet).

---

## 4. Kompozit skor

```
core  = w_q·sub_quality + w_o·sub_oversold + w_c·sub_cause + w_s·sub_stab        # 0–100
score = clamp( (core + news_pos) · risk_governor + news_neg , 0 , 100 )
```

**Haber yerleşimi (review düzeltmesi — v0.1'den değişiklik):** Pozitif haber **çarpımın içinde** (riskli isimde risk valfi pozitif haberi de kısar); negatif haber **çarpımın dışında** (her zaman tam ısırır). v0.1'deki `core·R + N` yapısı, +12 pozitif haberin risk valfini baypas etmesine izin veriyordu — düzeltildi.

**Başlangıç ağırlıkları (config, PRIOR — taşa yazılmaz):** `w_q=0.30, w_o=0.25, w_c=0.25, w_s=0.20` (toplam 1.0). Gerçek ağırlık kalibrasyonla (§9) bulunur. Yön (reversal↔momentum) **rejim-fit edilmez** (§9.6); teori-temelli sabit prior + canlı drift monitörü.

**`reasoning` (jsonb):** her bileşenin sayısal değeri + 1 cümle (kod şablonu yazar, LLM değil). Dashboard ve AI-chat bunu kullanır.

---

## 5. Mutlak kapı + sinyal bandı

### 5.1 Uyarlanabilir mutlak eşik (review §5 kararı)
Sabit 60 yerine breadth/volatiliteye göre uyarlanır:
```
abs_threshold_eff = clamp( base_abs_threshold + α·vol_z − β·breadth_z , floor_thr, ceil_thr )
   base_abs_threshold = 60 (prior), α, β, floor_thr=55, ceil_thr=72  — hepsi prior §10
   vol_z     = z(XU100 realized vol)        # yüksek vol → eşik yüksel (daha seçici)
   breadth_z = z(yükselen/düşen oranı)       # zayıf breadth → eşik yüksel
```
**Kapı geçiş oranı** dashboard'da metrik olarak gösterilir (0% veya 100%'e sapma anında yakalanır).

### 5.2 Mutlak kapı
```
meets_absolute_threshold =
      passed_gates
  AND score   ≥ abs_threshold_eff
  AND sub_cause ≥ min_cause   (prior 55)
  AND sub_stab  ≥ min_stab    (prior 50)
```
Hiçbir aday geçmezse → **"Bugün setup yok — nakitte bekle"** (boş tablo değil, dürüst mesaj).

### 5.3 Sinyal bandı — KAPIDAN SONRA (review §4 kararı)
Band, **mutlak kapıdan sonra** hesaplanır. Kapıyı geçemeyen isim **asla** Al/Güçlü Al olamaz:
```
if not meets_absolute_threshold:
    signal = (score < 30 ? "Sat" : score < 45 ? "Azalt" : "Tut")   # Al tarafına çıkamaz
else:
    signal = (score ≥ 75 ? "Güçlü Al" : "Al")    # kapı geçildi → 60+ zaten Al
```
**"Güçlü Al ≥75" tavanı KORUNUR (kasıtlı):** `score = (core+news_pos)·rg + news_neg` ve `core ≤ 100`, `news_pos ≤ 12` olduğundan tek risk bayrağı (rg=0.6) mükemmel core (100) + tam pozitif haber (+12) ile bile skoru `(100+12)·0,6 = 67,2`'ye sınırlar → **yüksek-ATR/riskli isim tepe banttan (≥75) yapısal olarak dışlanır.** Bu bir bug değil, tasarım: volatil ismi taçlandırmıyoruz. (Not: pozitif haber çarpımın **içinde** olduğu için tavan 67,2'dir; v0.1'in `core·R + N` yerleşimi yanlışlıkla 72 verirdi — bu da haberin risk valfini baypas etmesi demekti.)

---

## 6. Risk & pozisyon boyutu — EDGE-ÖLÇEKLİ FRACTIONAL RISK

> **"Kelly" terimi kaldırıldı.** v0.1'deki `base_qty × kelly_mult` çifte-boyutlandırması (ATR riski + ayrı Kelly çarpanı) kaldırıldı — gerçekleşen riski sessizce %0,1-0,5'e düşürüyordu ve cap<1 yüzünden asla büyütemiyordu (yanlış-etiketli kesinti). v0.2 **r'nin kendisini** ölçekler.

### 6.1 Boyutlandırma
```
r_eff       = base_r · edge_factor                     # base_r prior %1
risk_amount = equity · r_eff
stop        = entry − k·ATR(14)                         # k prior 2.0
qty         = floor( risk_amount / (k·ATR) )            # tek risk-çapası: ATR
```
- **`edge_factor` (varsayılan 1.0 = saf ATR):** Edge ölçülene dek `edge_scaling_enabled=false` → `edge_factor = 1.0` → saf ATR sizing (`r_eff = base_r`).
- **Edge ölçüldüğünde** (sert kapı: `is_live=true` satırlarda `n_closed ≥ edge_min_live_trades`, başlangıç **100**; **setup-bazlı**, havuzlanmış değil):
  ```
  p, b   = canlı, STOP-FARKINDA kapanmış işlemlerden (gerçekleşen R:R, fwd_5d DEĞİL)
  f_raw  = (b·p − (1−p)) / b
  f_lcb  = bootstrap_CI_alt_sınır(f_raw)                # nokta tahmini DEĞİL
  edge_factor = clamp( edge_scale · f_lcb , edge_factor_floor , edge_factor_cap )
  ```
- **Muhafazakârlık KORUNUR:** `edge_factor_cap < 1` (başlangıç **0.90**) → ölçülen edge yalnızca `base_r`'yi **kısabilir**, asla büyütemez. `base_r` (%1) trade-başı riskin sert tavanıdır. `edge_scale` prior (örn. 0.5 = "yarım", fractional). Bu, "overbet asla" felsefesinin tek-yönlü uygulamasıdır: edge güçlü+sağlamsa ≈0.90·base_r (küçük güvenlik payı), zayıf/gürültülüyse tabana doğru de-risk.
- **Kaldıraç:** `max_leverage = 1.0`, **>1 AÇILMAZ** (config'te kilitli). Brüt maruziyet ≤ özsermaye.

### 6.2 Portföy limitleri (kodda enforce, config)
| Parametre | Değer (prior) | Not |
|---|---|---|
| Trade-başı risk `base_r` | %1 | policy-knob |
| ATR çarpanı `k` | 2.0 | swing 1.5–2.5 |
| Portföy heat (toplam açık risk) | ≤ %6 | **ASIL bağlayıcı sınır** → ~5-6 pozisyon |
| Tek isim max | ≤ %30 | **Not:** ATR sizing'de sweet-spot ATR%'de ~%16 üstü nadiren bağlar; heat bağlayıcı. Knob olarak kalır, dekoratif olduğu işaretli. |
| Nakit tabanı | ≥ %15 | sistem ~%40-60 nakitte oturur (yapısal, bug değil) |
| Günlük hesap stopu | gün-içi −%3 | yeni giriş durur |
| Haftalık drawdown | tepeden −%10 | defansif/nakit moduna geç |

**Honest not (review):** `daily_stop %3 < heat %6`. Gap/limit-down gününde korelasyonlu stop'lar tek seansta ~%6 realize edebilir (günlük stop'un 2 katı) ve ATR stop limit-down'da dolmaz. **ATR stop bir hedeftir, garanti değil; gerçekleşen kayıp %1R'yi aşabilir.** Opsiyon (policy): heat'i %3'e indir veya `daily_stop`'u yalnızca "yeni giriş durdur" rolüne sınırla; ayrıca heat'e sektör/korelasyon konsantrasyonu kat.

### 6.3 Reel/USD P&L
Her `portfolio_snapshot`: TRY, USD (USD/TRY ile), enflasyon-ayarlı reel toplam. Dashboard üçünü de gösterir.

---

## 7. Earnings-surprise motoru (KOD — Foster-tipi seasonal-naive)

**Sorun (v0.1):** §18.1 prompt LLM'den `earnings_surprise: <null|float>` istiyordu; ama §0.1 LLM'in sayı uydurmasını yasaklıyor ve KAP metni "beklenen"i içermiyor → gerçek SUE metinden hesaplanamaz. Çözüm: **koda taşı.**

```
# isyatirim/borsapy finansallarından, çeyreklik net kâr ve ciro serisi:
expectation_t    = value_{t-4}                      # mevsimsel naif (geçen yıl aynı çeyrek)
forecast_error_t = actual_t − expectation_t
SUE_t            = (actual_t − expectation_t) / std( forecast_error son ~8 çeyrek )
pead_sign        = sign(SUE_t)                      # sub_cause.pead_term'i besler
pead_magnitude   = bucket(|SUE_t|)                  # 0..1, decay penceresi finansal_tablo=60g
```
- 100% kod; §0.1'i onurlandırır. **LLM yalnızca** olay tipini (`finansal_tablo` mu) ve niteliksel tonu (`beat-dili | nötr | miss-dili | belirsiz`, düşük ağırlıklı ipucu) sınıflar — sayı asla.
- **Point-in-time:** SUE'nin girdileri o tarihteki **yayınlanmış** finansallar olmalı (§9.1).
- BIST analist konsensüsü ince olduğu için seasonal-naive doğru seçim (standart no-consensus PEAD proxy'si).

---

## 8. Olay / haber süre & decay (per-tip)

**Akış:** KAP/haber → dedupe → LLM tip+ton yorumlar → kod **olay nesnesi** kurar: `{ticker(s), type, direction, magnitude(koddan PEAD'de), confidence, mechanism, decay_profile, effective_until, second_order[], thread_id}`. Bir kez yorumlanır (`interpreted=true`), gömülerek saklanır.

**Decay per-tip (review düzeltmesi — global tek yarı-ömür KALDIRILDI):**
| Tip | duration_days (prior) | decay_profile |
|---|---|---|
| `finansal_tablo` (PEAD) | 60 | **yavaş** (yarı-ömür ~30-40g) — "altın" sinyal 1-2 haftada sönmesin |
| `temettu` | ex-date'e kadar | ex-date'e hızlı sönüm, sonra 0 |
| `onemli_sozlesme` | 30 | üstel, orta |
| `bedelli`/`bedelsiz` | 20 | takvim-bağlı |
| `yonetici_islem` (insider) | 40-60 | yavaş (insider-drift literatürü; v0.1'deki 15 çok kısaydı) — düşük-confidence |
| `pay_geri_alim` | 30 | orta |
| `diger` | 7 | hızlı |

`effective_until` buraya göre; pencere bitince katkı 0. **Tüm süreler prior — kalibre (§10).**

**Threading (review düzeltmesi):** Takip haberi mevcut `thread_id`'ye iliştirilir. **LLM'e FİYAT TEPKİSİ VERİLMEZ** (v0.1 §10 fiyatı besliyordu → döngüsel; fiyat, açıklaması gereken sinyale geri besleniyordu). Re-yorum yalnızca **yeni metin vs önceki metin-temelli değerlendirme** görür. Her revizyon **diff'le loglanır**; revizyon `|news|`'i yalnızca **düşürebilir/tersine çevirebilir**, artışlar asimetrik cap (−20/+12) ile sınırlı.

**İkinci-derece (`relations` grafiği) (review düzeltmesi):** Yalnızca otomatik-türetilebilir kenarlar: `index_co` (XU30/XU100 üyelik listelerinden, oto-yenilenir) + büyük holdingler için `ownership/subsidiary` (KCHOL, SAHOL vb., elle ama az). El-bakımı `supplier/customer/peer` kenarları **veri kaynağı olana dek kapalı.** Her kenar `last_verified` + N-ay sonrası oto-expiry; küçük ağırlık; freshness-kapılı; dashboard'da "staleness" rozeti.

---

## 9. Kalibrasyon & backtest bütünlüğü (ZORUNLU — §17 milestone-5 GERÇEK KAPI)

> **Edge çıkmazsa ağırlık değil BİLEŞEN yanlıştır → revize et, üstüne build kurma.**

### 9.1 Point-in-time fundamental (look-ahead önleme)
F-Score ve SUE girdileri **KAP `finansal_tablo.published_at`** tarihine geciktirilir (sistem bu timestamp'i zaten alıyor). Dönem-sonu damgalamak yasak (Şubat'ta açıklanan veriyi Aralık'ta "bilmek" = look-ahead). PIT mümkün değilse → **F-Score'u backtest skorundan çıkar**, yalnızca canlı kapı olarak kullan.

### 9.2 Survivorship + corporate-action
- Tüm indikatörler **adjusted** (split/temettü/bedelsiz) seride. Ham close üzerinde bedelsiz = sahte −%50 "çöküş" → sahte oversold/stab. **Birim test:** >%15 tek-gün gap corporate-action'la (kap_events bedelsiz/bedelli) eşleşmiyorsa flag.
- Backtest evrenine **delisted/suspended** isimler dahil edilmeye çalışılır; mümkün değilse "survivor-only → backtest iyimser" yazılı uyarı + standart yarılamanın ötesinde iskonto.

### 9.3 Label ↔ exit uyumu (strategy-replay)
`fwd_return_5d` (al-tut close-to-close) **sizing için kullanılmaz.** Bunun yerine **strategy-replay backtest**: gerçek `k·ATR` stop + günlük/haftalık drawdown + decay-exit kurallarını uygular, gerçekleşen R:R üretir. Kelly/edge `p,b` **yalnızca buradan** beslenir. Label `as_of`'tan **sonraki uygulanabilir bardan** başlar (15dk lookahead sızıntısını kapatır), aynı-gün close'dan değil.

### 9.4 İstatistiksel güç (decile → rank IC)
Decile-monotoniklik göz kararı **bırakılır** (≥50M kapısı sonrası ~120-200 isim → 12-20/decile, kesitsel korelasyon efektif N'i ~2-3'e düşürür, overlapping 5g pencereler N'i 5× şişirir → naif t aldatıcı). Yerine:
- **Rank Information Coefficient** (Spearman corr: skor ↔ ileri getiri),
- **Newey-West** düzeltilmiş t (overlap autocorrelation),
- **Block bootstrap** CI (tüm kesitleri resample),
- Kesitsel **market/sektör demean** (skorun idiyosenkratik edge'i, beta-loading değil).
- `/calibration` sayfası IC + **CI + güç** gösterir (pass/fail plot değil); quintile/tercile (deciles değil).

### 9.5 /config = deneme-sayan (overfit guardrail)
- `/config`+`/calibration` her değerlendirilen konfigürasyonu **sayar**; N denemeyle **deflated-Sharpe eşiği yükselir** (kullanıcı çubuğun yükseldiğini görür).
- **Çoğu knob sabit** (teori-temelli prior); yalnızca **2-3 policy-knob** açık: `base_abs_threshold`, `base_r`, `min_move_atr_pct` (risk iştahı, backtest-fit değil).
- Ağırlık değişimi **held-out OOS** onayı arkasına kapılı (kullanıcı tuning sırasında görmediği dönem).

### 9.6 Tek-rejim verisi
~2 yıl (2024-25) neredeyse tek disinflasyon rejimi → walk-forward "rejim-dışı" olamaz. Reversal↔momentum yönü **rejim-fit edilmez**; teori-temelli sabit ağırlık + canlı drift monitörü (canlı performans kalibrasyon rejiminden saparsa oto-de-risk). `calibration_log`'a rejim etiketi (enflasyon trendi, USDTRY trendi, XU100 vol durumu); edge rejime göre stratifiye, tek blended sayı raporlanmaz.

### 9.7 Canlı shadow log → edge
Her skor + gerçekleşen (strategy-replay-tutarlı) sonuç `calibration_log`'a `is_live` ayrımıyla. Periyodik edge ölçümü; **backtest edge'i yarılanır** (McLean-Pontiff). Edge `edge_min_live_trades` (100) + bootstrap-CI-alt-sınır kapısını geçene dek `edge_factor=1` (saf ATR).

---

## 10. Config şeması (seed — hepsi düzenlenebilir; policy-knob işaretli)

> **Kural (karar 9):** `// PRIOR` işaretli HER blok (weights, thresholds, abs_adapt, k_atr, edge_scale, risk_valve, news, event_durations, decay_halflife_days) bir kalibrasyon hedefidir ve **deneme-bütçesine sayılır** (§9.5 deflated-Sharpe sayacı). `// POLICY-KNOB`'lar risk iştahıdır, backtest-fit değildir; deneme-bütçesine girmez.

```jsonc
{
  "weights": {            // PRIOR — kalibre, taşa yazma
    "w_q": 0.30, "w_o": 0.25, "w_c": 0.25, "w_s": 0.20,
    "quality": { "wf": 0.6, "wa": 0.2, "wv": 0.2 }
  },
  "thresholds": {
    "base_abs_threshold": 60,   // POLICY-KNOB
    "abs_adapt": { "alpha": 4.0, "beta": 3.0, "floor_thr": 55, "ceil_thr": 72 },  // PRIOR
    "min_cause": 55, "min_stab": 50, "min_fscore": 5,   // PRIOR
    "min_liq_tl": 50000000,
    "min_move_atr_pct": 0.025   // POLICY-KNOB (eski M tabanı)
  },
  "risk": {
    "base_r": 0.01,             // POLICY-KNOB
    "k_atr": 2.0,               // PRIOR
    "edge_scaling_enabled": false,
    "edge_min_live_trades": 100,
    "edge_scale": 0.5,          // PRIOR (fractional)
    "edge_factor_floor": 0.25, "edge_factor_cap": 0.90,   // cap<1 KORUNUR
    "max_leverage": 1.0,        // >1 AÇILMAZ
    "max_name_pct": 0.30,       // not: heat bağlayıcı, bu nadiren bağlar
    "max_heat_pct": 0.06, "cash_floor_pct": 0.15,
    "daily_stop_pct": 0.03, "weekly_dd_pct": 0.10
  },
  "risk_valve": {               // PRIOR — kalibre + deneme-bütçesine say
    "rsi_overbought": 80, "today_spike_pct": 0.08, "extreme_atr_pct": 0.12,
    "mult_rsi": 0.6, "mult_spike": 0.5, "mult_atr": 0.6, "mult_binary": 0.5, "mult_thin_liq": 0.7,
    "floor": 0.2
  },
  "news": {                     // PRIOR
    "neg_cap": -20, "pos_cap": 12, "pos_cap_unanchored": 4
  },
  "event_durations": {          // PRIOR — kalibre
    "finansal_tablo": 60, "temettu_to_exdate": true, "onemli_sozlesme": 30,
    "bedelli": 20, "bedelsiz": 20, "yonetici_islem": 50, "pay_geri_alim": 30, "diger": 7
  },
  "decay_halflife_days": {      // PRIOR — per-tip
    "finansal_tablo": 35, "onemli_sozlesme": 10, "yonetici_islem": 25, "diger": 2.5
  },
  "cadence": { "snapshot_min": 2, "news_min": 5, "score_min": 5 },
  "calibration": { "trial_counter": true, "deflate_with_trials": true, "require_oos_for_weight_change": true },
  "prompts": { "kap_interpret": "<tip+ton, sayı YOK>", "ticker_comment": "<§18.2>" }
}
```

---

## 11. Sinyal bantları (özet)
`≥75 Güçlü Al · 60–74 Al · 45–59 Tut · 30–44 Azalt · <30 Sat` — **yalnızca `meets_absolute_threshold` geçenler Al/Güçlü Al** olabilir (§5.3). Geçmeyen en fazla "Tut".

---

## 12. Günlük (daily) skor
Aynı çatı, kısa-vade bileşenlerle (intraday AOF konumu, çok-kısa momentum/RSI, tavana mesafe, gün-içi range) ve daha sıkı eşik. Dashboard'da swing yanında. **Uyarı kalıcı:** 15dk gecikme + manuel execution scalp'i zorlaştırır → daily skor **bilgi amaçlı bağlam metriği**, "bayat, manuel tradable değil" etiketli (aksiyon yüzeyi swing'dir).

---

## 13. Dürüstlük guardrail checklist (her milestone'da doğrula — §18.3 güncel)
- [ ] Skor/risk/boyut yalnızca koddan; LLM sadece tip+ton+yorum. **Earnings-surprise koddan (§7).**
- [ ] Kapıyı geçen yoksa "nakitte kal"; sahte "Al" yok.
- [ ] Risk = boyut (edge-ölçekli `r_eff`); volatilite **pozitif skor bileşeni değil** (M sadece taban kapısı); MAX cezası aktif.
- [ ] Edge ölçülene dek `edge_factor=1` (saf ATR); `edge_factor_cap<1`; kaldıraç >1 yok; aşırı-bahis engelli.
- [ ] Trade-başı risk, heat, günlük/haftalık drawdown enforce; ATR-stop "garanti değil" notu görünür.
- [ ] Reel/USD P&L gösteriliyor.
- [ ] Backtest: PIT fundamental, adjusted seri + delisted, strategy-replay label, rank-IC+NW+bootstrap, deneme-sayan /config; edge yarılanıyor.
- [ ] Sosyal/söylenti skora girmiyor; whitelist resmi kaynak; LLM'e fiyat tepkisi beslenmiyor.
- [ ] Veri 15dk gecikmeli + bayatlık görünür; intraday flag "dikkat" notlu.
- [ ] **UI'da +%7,5 pace-bar YOK**; CPI+XU100 reel benchmark + risk/ruin paneli var.
- [ ] Tüm ikincil sabitler "prior — kalibre" işaretli ve deneme-bütçesine dahil.

---

## 14. Crosswalk: v0.1 (T/M/N/R) → v0.2 (TEK MODEL)
| v0.1 sembol | v0.1 ne yapıyordu | v0.2'de durumu |
|---|---|---|
| **T** (SCTR momentum percentile) | yön + güç | **EMEKLİ.** Momentum/trend bileşenleri `sub_oversold` (ters: ne kadar düştü) + `sub_stab` (dönüş) içine dağıldı. Saf trend-takip skoru yok (tez = reversal-on-quality). 200-EMA "yapısal düşüş" koruması `sub_cause`/gate notuna taşındı. |
| **M** (beklenen-hareket / sweet-spot ATR ödülü) | hareket potansiyeli ödülü | **DÖNÜŞTÜ → evren TABANI** (§2.5). Ödül değil; ölü-sakin isimleri eleyen binary kapı. Sweet-spot "ceza üstü" kısmı `risk_governor` (ATR%>12) ile zaten var. |
| **N** (haber) | yön düzelticisi | → `news_pos`/`news_neg` (§3.5). Asimetrik korunur; pozitif artık çarpımın **içinde**. |
| **R** (risk valfi) | skor kısıcı | → `risk_governor` (§3.6) aynen. |
| `w_T, w_M` | 0.60/0.40 | **KALDIRILDI.** Tek ağırlık vektörü `w_q/w_o/w_c/w_s`. |

**Dosya ağacı düzeltmesi (`MASTER-BUILD-SPEC.md` §4):** `features.py # skor bileşenleri (T,M,N,R girdileri)` → **`features.py # skor bileşenleri (kalite/oversold/sebep/stabilizasyon/haber/risk-valfi girdileri)`**. `scoring.py` ve `scores` tablosu (`sub_quality/sub_oversold/sub_cause/sub_stab/news_pos/news_neg/risk_governor`) zaten v0.2 ile uyumlu.

---

## 15. v0.1 → v0.2 değişiklik günlüğü
1. **Tek model**; T/M/N/R emekli (§1, §14).
2. **M → evren tabanı** (ödül değil), "büyük marka handikabı" çözümü (§2.5).
3. **Kelly → edge-ölçekli fractional risk**; çifte-boyutlandırma kaldırıldı; `r_eff = base_r·edge_factor`, cap<1, kaldıraç yok, canlı+n≥100+bootstrap-CI-alt-sınır (§6).
4. **Band kapıdan sonra**; ≥75 tavanı korundu (§5.3).
5. **Oversold/stab evren-içi göreli**, kalite mutlak; **uyarlanabilir abs_threshold** + kapı-geçiş metriği (§5.1, §3).
6. **UI:** pace-bar kaldırıldı; reel benchmark + risk/ruin paneli; hedef dokümanda dürüstlük notu olarak kalır (§0.2, §6).
7. **Backtest bütünlüğü:** PIT, adjusted+delisted, strategy-replay label, rank-IC+NW+bootstrap, deneme-sayan /config, rejim-fit etme (§9).
8. **Earnings-surprise koda taşındı** (Foster seasonal-naive); LLM yalnızca tip+ton (§7).
9. **İkincil sabitler "prior" işaretli** + deneme-bütçesinde (§10).
+ Review ek düzeltmeleri (vetolanabilir): haber çarpım-içi yerleşimi (§4), per-tip decay (§8), `sub_cause` pozitif-temizlik + idiyosenkratik ceza (§3.3), thread'e fiyat beslememe (§8), daily_stop/heat dürüstlük notu (§6.2).

---

*Bu doküman skor + risk + kalibrasyon matematiğinin TEK kaynağıdır. Build: `MASTER-BUILD-SPEC.md` §17 sırası; her milestone'da §13 checklist; çelişkide bu doküman > methodology v0.1; değişmez ilkeler (§0.1) korunur. Milestone-5 (backtest → rank-IC) gerçek kapı: edge yoksa bileşen revizyonu, üstüne build yok.*
