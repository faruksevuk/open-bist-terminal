# BIST SETUPS v0.1 — Olay-Tetikli Kısa-Vade İşlem Katmanı

## 0. Neden bu katman?

Kalibre edilmiş kesitsel faktör skoru (SCORING v0.2) **dürüst** ama düşük-vol isimleri
seçiyor — kullanıcının asıl hedefine (2-10 günlük giriş/stop/hedefli kısa-vade işlem
yakalamak) yaramıyor. Bu VERİ üzerinde yapılan faktör teşhisi:

- **roc5 IC = -0.019 (NW t = -2.76)** → 5-günün kaybedenleri geri sıçrıyor: gerçek, ölçülmüş
  bir kısa-vade **dönüş (reversal)** edge'i. Skorlama katmanı bunu hiç kullanmıyordu.

SETUPS katmanı olay-tetikli SETUP'lar ekler. **Her setup KENDİ verimizde bağımsız bir
olay-çalışması (event study) ile doğrulanır** (`app/backtest/event_study.py`). Faktör
skorunun yerini almaz — onun yanında, kısa-vade fırsat tablosu olarak çalışır.

Ayrıca ölçülen dönüş edge'i faktör skoruna da küçük bir cerrahi eklemeyle taşındı:
**rev5 = pct(-roc5)**, ağırlık 0.05 (momentum 0.10→0.05 düşürüldü, toplam yine 1.0).

---

## 1. Dedektörler (`app/engine/setups.py`)

Saf fonksiyonlar; per-ticker indikatör DataFrame'i (`compute_indicators`) + kesitsel
bağlam (`MarketContext`) alır, BELİRLİ bir bar indeksinde (`i`) çalışır. Canlı tarama son
barı; olay-çalışması geçmişteki HER barı **point-in-time** (yalnız t'ye kadarki veri)
değerlendirir.

**TÜM parametreler literatür-temelli PRIOR** — optimizasyon YOK. `config['setups']`
anahtarından okunur, yoksa `_DEF_SETUPS`.

### Piyasa rejimi bağlamı (tarama başına bir kez; eş-ağırlık evren)
- `mkt_ret_5d` — eş-ağırlık evren 5-gün getirisi
- `mkt_above_ema50` — eş-ağırlık endeks kendi EMA50'sinin üstünde mi (bool)
- `breadth` — son barda yükselen isimlerin oranı

### 1) snapback — "Panik Dönüşü" (ölçülen edge ile hizalı)
Tetik (i barında):
- `roc3` (3-gün % değişim) evren **alt-desilinde** (bugün), VE
- `rsi14 < 35`, VE
- `close > 0.90 × ema200` (ölüm sarmalı DEĞİL), VE
- idiyosenkratik düşüş: `(hisse 5g ret − mkt_ret_5d) ≤ -0.04`, VE
- `mkt_ret_5d > -0.05` (piyasa çökmüyor).

`stop = min(son 3 barın low) − 0.5×atr14`; `target = entry + 2R`; `time_exit=5`; `valid=2`.
`strength = 100·(0.6·roc3_derinliği + 0.4·rsi_derinliği)` — roc3 alt-desil içinde ne kadar
dipte + rsi 35→10 aralığında ne kadar düşük.

### 2) squeeze_breakout — "Sıkışma Kırılımı"
`bb_width = (bb_upper − bb_lower)/bb_mid`. Tetik:
- DÜNKÜ (`i-1`) bb_width'in kendi **252g** tarihindeki persentili ≤ 20, VE
- `close > max(prior 20 bar close)`, VE
- `vol_tl ≥ 1.5 × avg_tl_vol_20`, VE
- `(close−low)/(high−low) ≥ 0.70` (günün üst %30'unda kapanış).

`stop = kırılım barının low − 0.5×atr14`; `target = 2R`; `time_exit=10`; `valid=2`.

### 3) trend_pullback — "Trend İçi Düzeltme"
Tetik:
- `close > ema50 > ema200`, VE `roc20 ≥ 10%`, VE
- `|close/ema20 − 1| ≤ 0.02`, VE `35 ≤ rsi14 ≤ 55`, VE
- (yükseliş barı: `close>open`, VEYA prior 5 bara göre higher_low), VE
- `mkt_above_ema50 OR breadth > 0.45` (rejim destekli).

`stop = min(son 5 barın low) − 0.5×atr14`; `target = 2R`; `time_exit=10`; `valid=2`.

### 4) pead_drift — "Bilanço Sürprizi (PEAD)" — CANLI-ONLY
**Dürüst event-study EDİLEMEZ**: SUE latest-snapshot; PIT açıklama tarihi yok. Tetik:
- `fundamentals raw.sue ≥ 0.5`, VE
- ticker için son 3 işlem günü içinde bir `finansal_tablo` KAP olayı (yoksa TETİKLEME), VE
- son bar `close ≥ open`.

`stop = son barın low − 0.5×atr14`; `time_exit=15`; `valid=3`.
Kanıt statüsü sabit: **"deneysel (prior — PIT yok)"**.

### 5) quiet_accumulation — "Sessiz Toplama"
Son 10 barda, `piyasa eş-ağırlık ret < -0.005` iken `hisse ret ≥ 0` ve
`vol_tl ≥ 1.3 × avg_tl_vol_20` olan gün sayısı ≥ 3 VE son bar > -%1 ise tetiklenir.
`stop = min(son 10 barın low) − 0.5×atr14`; `target = 2R`; `time_exit=10`; `valid=3`.
Başlangıç statüsü **"deneysel"**.

### Ortak kapılar (tüm dedektörler)
`min_liq_tl = 50M` likidite, `min_bars = 200` geçmiş, `excluded=False` (tedbir yok),
taze negatif haber bloğu (`news_neg ≤ -5` → setup bloke). Aynı gün (ticker,setup) mükerrer
kaydı yapıca imkânsız (unique constraint).

---

## 2. Olay-çalışması metodolojisi (`app/backtest/event_study.py`)

Setup 1,2,3,5 için (pead_drift HARİÇ — PIT yok). 220+ barı olan tüm tickerlarda, ısınma
210 sonrası her bar t'de dedektör YALNIZ t'ye kadarki veriyle koşulur.

- **Giriş** = t+1 barının OPEN'ı (adjusted).
- **Forward fazla getiri (excess)** h∈{1,3,5,10} bar için:
  `excess = hisse fwd getiri (giriş open → t+1+h close) − evren MEDYAN fwd getiri (aynı pencere)`.
  Kesitsel **demeaning** rejim sürüklenmesini (disinflasyon boğası) öldürür.
- **İşlem simülasyonu**: setup'ın stop/target/time-exit'i günlük barlarda. **Muhafazakâr
  stop-önce**: bir bar `low ≤ stop AND high ≥ target` ise ÖNCE STOP sayılır (worst-case).
  R-multiple üretir.
- **İstatistiksel dürüstlük**: olaylar zamanda kümelenir → önce AYNI GÜNÜN olayları günlük
  ortalamaya toplanır; `t = newey_west_tstat(DAILY ortalama seri, lags=horizon)`; ayrıca
  `bootstrap_ci`. Ticker başına AYNI setup'ın **örtüşen** tetikleri atlanır (önceki olaydan
  `horizon(10)` bar geçmeden yeni olay yok).

### Verdict (setup başına, 5g excess'e göre)
| verdict | koşul |
|---|---|
| `kanıtlı` | n_events ≥ 30 AND mean_excess(5g) > 0 AND NW t ≥ 1.5 |
| `zayıf` | n ≥ 30, pozitif ama t < 1.5 |
| `deneysel` | n < 30 |
| `devre dışı` | mean_excess(5g) ≤ 0 (negatif-edge; API'de gizli, `include_all=true` ile taranabilir) |

### Deneme disiplini (§9.5, BIST-SCORING-v0.2.md)
**PARAMETRE ARAMASI YOK.** Prior parametreler TAM olarak BİR kez koşulur. Bu bir
DOĞRULAMA'dır, optimizasyon değil — deneme sayacına 1 giriş.

---

## 3. Kalıcılık + API

- **Model** `SetupSignal` (`setup_signals` tablosu): unique (ticker, setup, triggered_at),
  active bool, valid_until date, context JSONB.
- **Tarama** `app/engine/setup_scan.py::scan_universe`: son barda dedektörleri koştur,
  kapı+haber uygula, upsert (on_conflict_do_nothing), süresi geçenleri deaktive et.
  Güncel piyasa bağlamını `config['setup_market']`e yazar.
- **Hook**: `run_scoring.py` skorlamadan sonra `scan_universe` da çağırır (refresh.bat
  tetikler); ayrıca bağımsız `scan_setups.py`.
- **API** (`app/api/routes/setups.py`):
  - `GET /api/setups[?include_all=true]` → market + aktif/süresiz sinyaller (verdict ≠
    "devre dışı"); verdict rank → strength desc sıralı; her sinyalde evidence + score.
  - `GET /api/setups/evidence` → tam setup_evidence blob'u.

---

## 4. rev5 faktör-katmanı eklentisi (cerrahi)

Ölçülen kısa-vade dönüş edge'i swing skoruna da taşındı:
- `features.py`: snapshot'a `roc5` eklendi.
- `scoring.py`: faktör havuzuna `rev5 = pct(-roc5)`; `_DEF_FACTOR_WEIGHTS`'e rev5=0.05
  (momentum 0.10→0.05); `persist_scores` reasoning.factors'a rev5.
- `runner.py`: diagnostic'e `rev5 = pct(-roc5)`.
- `calibration.py`: `_MAP`'e `rev5: rev5`.
- `seed_config.py`: factor_weights'e rev5=0.05 (toplam 1.0).

Kalibrasyon rev5'i IC'sine göre (deflate 0.5) yeniden ağırlıklar.

---

## 5. ACTUAL RESULTS (olay-çalışması çıktısı)

**Not (v0.1 tarihsel — güncellendi):** Bu bölüm ilk yazıldığında canlı koşum Docker/Postgres
engeline takılmıştı. O engel **artık geçersiz** — proje **SQLite**'a taşındı (Docker YOK) ve
event-study `event-study.bat` ile doğrudan gerçek `daily_bars` üzerinde koşuyor. Ölçülen özet:
Sıkışma Kırılımı net PF ~1.42, Uzun Sıkışma ~1.56 (ikisi de "zayıf": t<1.5, tek rejim). Tam
kırılım için `event-study.bat` çıktısı / config `setup_evidence`.

### Offline doğrulama (DB olmadan yapılanlar)
- **61/61 test geçti** (39 mevcut + 22 yeni: `test_setups.py` 13, `test_event_study.py` 9).
- **Uçtan uca boru hattı doğrulandı**: `run_event_study` sentetik 3-ticker/320-bar panelde
  (DB bağımlılıkları patch'lenerek) tam koştu — olay toplama (5 snapback olayı),
  PIT excess/verdict hesabı, `setup_evidence` yazımı çalıştı. Mekanik doğru.
- **Point-in-time garantisi test edildi**: gelecek barları değiştirmek aynı i'deki tetiği
  etkilemiyor (`test_pit_no_lookahead`).
- **Stop-önce simülasyon test edildi**: aynı barda stop+target → -1R (muhafazakâr).
- **Örtüşme baskılama + günlük kümeleme + verdict eşikleri** birim testlerle doğrulandı.

### Canlı koşum talimatı (kullanıcı için)
```
cd backend && .venv\Scripts\python.exe scripts\setup_db.py    # SQLite bist.db + seed (--overwrite YOK)
.\event-study.bat                                              # setup_evidence yazar
.venv\Scripts\python.exe -c "from app.db.base import SessionLocal; from app.backtest.calibration import calibrate_factor_weights;\
  s=SessionLocal(); print(calibrate_factor_weights(s))"        # rev5'i alır
.venv\Scripts\python.exe scripts\run_scoring.py                # skorlar + setup taraması
```

<!-- RESULTS_PLACEHOLDER: event-study.bat çıktı tablosunu buraya yapıştır -->
_Canlı sonuç tablosu (n_events, hit_rate, excess@1/3/5/10, NW t, bootstrap CI, PF, verdict)
`event-study.bat` çalıştırıldıktan sonra buraya eklenecek._

---

## 6. Canlı Sonuç Takibi (OOS) — `app/engine/setup_outcomes.py`

Event-study (§2) bir **prior doğrulama**dır: aynı geçmiş veride ölçülen edge. Gerçek
kanıt ancak **canlı, out-of-sample (OOS)** sonuçlarla birikir — ateşlenen her sinyalin
sonrasında ne OLDUĞUNU izleyerek. Bu katman "zayıf/deneysel" verdict'lerinden "kanıtlı"ya
giden **tek dürüst yol**dur.

### Model — `SetupOutcome` (`setup_outcomes` tablosu)
Her `SetupSignal`'a **en fazla bir** sonuç (`signal_id` UNIQUE → upsert). Alanlar:
`ticker, setup, triggered_at, entry_date, entry_price, status, exit_date, exit_price,
realized_r, realized_pct, days_held, evaluated_at`.
(Yeni TABLO — mevcut `setup_signals`'e kolon EKLENMEZ; `create_all` ALTER yapmaz.)

**`realized_r`/`realized_pct` GROSS saklanır** (maliyet öncesi). NET karşılıkları
SAKLANMAZ — `outcome_summary`/API'de saklı gross + o anki `config['costs']`'tan **türetilir**
(bkz. §8 İşlem Maliyetleri). Bu, geriye dönük tarife değişiminin (broker güncellemesi) tüm
geçmişi temiz yeniden-hesaplamasını sağlar — kolon eklemeye gerek yok, hesap her zaman taze.

### Konvansiyonlar (event-study `_simulate_trade` ile **BİREBİR** — mantık çatallanmaz)
Tek doğruluk kaynağı: `event_study.simulate_trade_detail`. Hem PIT event-study hem canlı
takip bunu çağırır (R sayısını `_simulate_trade` bundan türetir).
- **Giriş** = tetik (`triggered_at`) barından SONRAKİ ilk barın **OPEN**'ı (adjusted seri).
- **Muhafazakâr STOP-ÖNCE**: bir bar `low ≤ stop AND high ≥ target` ise ÖNCE STOP (worst-case).
- **Zaman-çıkışı** = giriş barı + `time_exit_days` barının **CLOSE**'u (o bara dek stop/target yoksa).
- `realized_r = (exit − entry) / (entry − planlı_stop)`; `realized_pct = exit/entry − 1`.

### Statüler
| status | anlam |
|---|---|
| `pending` | giriş barı henüz oluşmadı, VEYA giriş oldu ama zaman-çıkışına kadarki barlar tam gelmedi ve stop/target vurulmadı (kısmi; sonraki koşumda tekrar değerlendirilir) |
| `target` | hedef vuruldu → `realized_r > 0` |
| `stop` | stop vuruldu → `realized_r ≈ −1` |
| `time_exit` | ne stop ne target → zaman-çıkışı close'u ile kapandı |
| `no_entry` | giriş barının open'ı ≤ stop (geçersiz risk) → hiç girilmedi |

`pending` HARİÇ hepsi **final** — bir daha değerlendirilmez. `pending` her koşumda tekrar
denenir (yeni bar geldikçe olgunlaşır).

### Beklenti (expectancy) formülü — DÜRÜST
`risk_per_trade = config risk.base_r` (varsayılan 0.01):
```
ölçülen_R/hafta       = Σ realized_r (kapalı) / hafta_aralığı(triggered_at min→max)
beklenen_%/hafta      = ölçülen_R/hafta × base_r × 100
gereken_R/hafta       = 0.10 / base_r        # %10/hafta için → base_r=0.01'de 10R/hafta
açık (gap)            = gereken_R/hafta − ölçülen_R/hafta
```
`gap` sayısal olarak yazılır: hedefin mevcut edge'in ne kadar üstünde olduğunu **rakamla**
gösterir. Kapalı işlem yoksa dürüst boş durum: "Henüz kapanan sinyal yok — takip birikiyor."

### Wiring
- `scripts/run_scoring.py`: `scan_universe`'den SONRA `evaluate_outcomes` çağrılır
  (`refresh.bat` sonuçları hep taze tutar: yeni sinyallere entry fill denenir, eskiler
  kapanana dek güncellenir).
- Bağımsız: `scripts/evaluate_outcomes.py` (Türkçe özet tablo + beklenti bloğu basar).
- **API** `GET /api/setups/outcomes` → `{as_of, per_setup, overall, expectancy, outcomes[son 50 kapalı]}`.
- **Frontend**: kanıtlar (EvidencePanel) collapsible'ı içinde "📡 Canlı Takip (OOS)"
  kompakt tablosu + tek satır beklenti çizgisi (boş durumu dürüst).

### Neden OOS biriktirme "zayıf→kanıtlı"nın TEK yolu?
Event-study aynı veride prior parametreleri ölçer (deneme disiplini §9.5: tek koşum, arama
yok). Bu **in-sample** bir doğrulama; edge'i teyit etmez, yalnızca prior'ın çöp olmadığını
gösterir. Gerçek dünyada — gap'ler, likidite, kayma, rejim değişimi altında — setup'ın
para kazanıp kazanmadığı ancak **ileriye dönük, görülmemiş** sinyallerde ölçülebilir. Her
canlı sinyal bir bağımsız örnektir; yeterince birikince (event-study eşiğiyle uyumlu n≥30)
canlı isabet/ort-R verdict'i in-sample prior'dan bağımsız olarak "kanıtlı"ya taşıyabilir —
ya da edge'in yaşamadığını dürüstçe gösterir.

### İlk canlı koşum sonucu (2026-07-03, bar tazeliği max=2026-07-02)
`fetch_history --period 5d` (567/568 ticker) + `run_scoring.py` (skor+tarama+sonuç) koşuldu.
BIST 2026-07-03 barı HENÜZ oluşmadı → 2026-07-02 sinyalleri **pending** (giriş beklemede) —
bu **dürüst** durumdur, sistem sinyal fill'i uydurmaz.

Buna rağmen daha eski sinyaller (2026-06-22 tetikli) çoktan kapandı → **9 kapalı işlem**
gerçek OOS kanıtı:

| setup | kapalı | beklemede | isabet | ort R | toplam R | ort gün |
|---|---|---|---|---|---|---|
| Trend İçi Düzeltme | 5 | 7 | 20% | −0.44 | −2.19 | 2.0 |
| Sıkışma Kırılımı | 2 | 2 | 50% | +0.07 | +0.13 | 1.5 |
| Panik Dönüşü | 2 | 1 | 0% | −0.66 | −1.32 | 2.5 |
| Sessiz Toplama | 0 | 6 | — | — | — | — |
| **GENEL** | **9** | **16** | **22%** | **−0.38** | **−3.38** | — |

**Dürüst beklenti:** ölçülen −3.38R/hafta → ~%−3.38/hafta. Hedef %10/hafta için 10R/hafta
gerekli — **açık 13.4R/hafta**. Bu fark **yapısal**: hedef mevcut edge'in çok üstünde. (n=9
henüz istatistiksel karar için küçük — deneysel; kanıt ancak birikimle olgunlaşır.)

---

## 8. İşlem Maliyetleri (komisyon + spread) — NET muhasebe

Raporlanan tüm edge/beklenti sayıları **GROSS** (maliyet öncesi) idi; friction küçük edge'i
daha da küçültür. Bu katman **net-of-cost** muhasebeyi her yere ekler — bu bir **friction
realizmi**dir, parametre optimizasyonu DEĞİL (tek re-run, ayar yok).

### Config knob — `config['costs']` (POLICY-KNOB)
```json
{
  "commission_pct_per_side": 0.0004,
  "spread_slippage_pct_per_side": 0.0010,
  "note": "Midas tarifesine göre güncelle; slippage küçük-cap'te daha yüksek olabilir"
}
```
Kullanıcı broker tarifesine göre günceller. `get_config` fallback deseniyle okunur.

### Formül
```
round_trip_cost_pct = 2 × (commission_pct_per_side + spread_slippage_pct_per_side)   # varsayılan 0.0028 = %0.28
net_pct             = gross_pct − round_trip_cost_pct                                 # fiyat-getiri uzayı
risk_frac           = (entry − stop) / entry                                          # R denominatörü / entry
cost_R              = round_trip_cost_pct / risk_frac  ( = cost_pct × entry/(entry−stop) )
net_R               = gross_R − cost_R
```
`round_trip_cost_pct == 0` → net == gross (varsayılan davranış korunur; eski alan adları
GROSS anlamını taşır, tüketiciler sessizce değişmez).

**Maliyet-fizibilite guard'ı (ayar YOK):** `risk_frac ≤ round_trip_cost_pct` ise işlem
maliyeti tüm 1R risk bütçesini yer → ekonomik olarak **girilemez**; net_R anlamsızdır
(risk→0'da R-normalizasyon patlar). Böyle işlemler net-R toplamasından **düşer** (`net_R=None`);
GROSS ve `net_pct` dokunulmaz. Eşik maliyetin kendisidir. (Canlı sizing'de bu isimler zaten
ATR-tabanı `min_move_atr_pct` ile elenir; net-R'de de dürüstçe dışlanır.)

### Türetilir, saklanmaz
Canlı sonuçlarda (`SetupOutcome`) `realized_r`/`realized_pct` **GROSS** saklanır; NET
`outcome_summary`/API'de saklı gross + o anki config maliyetinden **türetilir** (risk_frac =
`realized_pct/realized_r`; event-study R matematiğiyle birebir). Şema değişikliği YOK —
geriye dönük tarife değişimi tüm geçmişi temiz yeniden-hesaplar (özellik, kusur değil).

### Verdict maliyetten ETKİLENMEZ
Setup verdict'i (kanıtlı/zayıf/deneysel/devre dışı) **demeaned kesitsel fazla-getiri**
istatistiğine (§2) dayanır — maliyet kesitsel excess kanıtını değiştirmez, bu yüzden verdict
mantığı **değişmedi**. Net trade-sim yalnızca kullanıcıya friction'ı görünür kılar.

Ayrıca `needed_r_per_week` (10R/hafta = %10/hafta @ %1 risk) maliyeti **yok sayar** →
gerçek açık raporlanandan daha büyüktür (net beklenti daha da düşük).

### Re-run NET sonuçları (event-study, tek koşum — 545 ticker, round-trip %0.28)
Aynı prior parametreler, tek koşum. trade-sim GROSS → NET (verdict değişmez):

| Setup | n | mean_R gross → **net** | PF gross → **net** | verdict |
|---|---|---|---|---|
| Panik Dönüşü (snapback) | 435 | 0.074 → **−0.007** | 1.13 → **0.99** | zayıf |
| Sıkışma Kırılımı (squeeze) | 1216 | 0.232 → **0.192** | 1.59 → **1.46** | zayıf |
| Trend İçi Düzeltme (pullback) | 540 | 0.178 → **0.044** | 1.34 → **1.08** | zayıf |
| Sessiz Toplama (accumulation) | 269 | 0.032 → **0.017** | 1.12 → **1.06** | devre dışı |

**Dürüst okuma:** friction Panik Dönüşü'nü pozitiften **başabaşın altına** (PF < 1.0) itiyor;
her setup'ın PF'i 1.0'a yaklaşıyor. Gross rakamlar edge'i olduğundan iyimser gösteriyordu —
net, gerçek işlem koşullarında marjın ne kadar ince olduğunu ortaya koyuyor.

### Wiring
- `simulate_trade_detail(..., round_trip_cost_pct=…)`: TEK doğruluk kaynağı; GROSS + NET
  (`r_multiple_net`, `pct_net`) döner. `_simulate_trade` → `(gross_R, net_R)`.
- Event-study `_aggregate`: `mean_R_net`, `profit_factor_net`, `hit_rate_R_net`.
- Canlı `outcome_summary`: per-setup + genel `ort_r_net`/`toplam_r_net`/`ort_pct_net` +
  beklenti bloğu `measured_r_per_week_net`, `expected_weekly_pct_net`; `gap_note` NET'e dayanır.
- **API**: `/api/setups/evidence` (net trade-sim) + `/api/setups/outcomes`
  (`round_trip_cost_pct`, net beklenti, `needed_ignores_cost`).
- **Frontend**: EvidencePanel "PF (net/brüt)"; OutcomesPanel "ort/toplam R (net/brüt)" —
  net birincil, brüt parantezde; tek satır dipnot: "Maliyet: komisyon+spread konfigüre
  edilebilir (config → costs)". Config sayfası `costs` knob açıklaması.

---

# EK C — v0.2: İşlem-Öncelik Katmanı + Rejim Dilimi + Otonom Mod (2026-07-07)

## C.1 İşlem-öncelik katmanı (`app/engine/priority.py`)

**Problem:** `/api/setups` verdict+strength ile sıralanıyordu; elimizdeki ÜÇ ölçülmüş
bilgi karara girmiyordu: (1) event-study NET işlem beklentisi, (2) canlı OOS sonuçları,
(3) bağlam tilt'i (sector_macro'da hesaplanıp hiçbir yerde kullanılmıyordu).

**Çözüm — formül ÖN-KAYITLI (parametre araması yok, §9.5):**

```
E (beklenen net R/işlem) = (n_oos·oos_r + k·study_r) / (n_oos + k)      k=20 (POLICY)
  study_r = event-study trade-sim mean_R_net
            (yoksa gross mean_R "BRÜT" etiketiyle; o da yoksa config prior_r —
             pead_drift 0.05R research-prior, _default 0)
plan ekonomisi (maliyet DAHİL):
  risk_frac = (giriş−stop)/giriş;  cost_r = round_trip/risk_frac
  net_target_r = rr − cost_r;  net_stop_r = −1 − cost_r
  breakeven_hit = (1+cost_r)/(rr+1)
  risk_frac ≤ round_trip → GİRİLEMEZ (simulate_trade_detail guard'ı ile AYNI eşik)
priority (0-100) = 100·clip(E,0,0.30)/0.30 × (0.6+0.4·güç/100) × bağlam_çarpanı × haber_çarpanı
  bağlam_çarpanı = (0.85+0.30·sektör/100)(0.85+0.30·rejim/100)  [context_tilt, PRIOR]
  haber_çarpanı  = 1 + news_pos/100  (≤ ×1.12; negatif haber zaten taramada bloke)
```

**Tavsiye etiketi:** `girme` (plan girilemez — maliyet 1R'yi yiyor) > `al-adayı` (E>0) >
`izle` (E≤0 ya da yalnız prior). E≤0 → priority 0: **ölçülen edge'i olmayan sinyal
sıralamada yukarı ÇIKAMAZ** — snapback (net PF 0.99) veriyle kendiliğinden "izle"ye düşer,
elle kural yazılmadı.

Config: `priority` (prior_weight_k / prior_r / e_cap_r / w_strength / news_div).
Canlı OOS n büyüdükçe study'nin ağırlığı otomatik erir (shrinkage) — sistem kendi canlı
sonuçlarından ÖĞRENİR, kural değişikliği gerekmez.

## C.2 Ön-kayıtlı rejim dilimi (event-study `by_regime`)

Her olay tetik anındaki piyasa rejimiyle etiketlenir (eş-ağırlık endeks kendi EMA50
üstü/altı — zaten PIT hesaplanıyordu). `_aggregate` `by_regime = {up, down}` üretir:
n / mean_excess_5d / hit_rate_5d / mean_R_net / profit_factor_net.

**SADECE raporlama:** verdict'e girmez, priority kullanmaz (koşullu kural değiştirme =
snooping). Kural değişikliği ancak canlı OOS teyidiyle. UI: kanıt tablosunda
"R̄net rejim ↑/↓" kolonu.

## C.3 Otonom mod (`app/scheduler.py` + `app/pipeline.py`)

`app/pipeline.py`: script'ler VE scheduler'ın çağırdığı TEK orkestrasyon
(refresh_data / refresh_scores / poll_news / weekly_calibrate / refresh_event_study).
`run_scoring.py` ve `poll_kap.py` artık buraya delege eder — mantık çatallanmaz.

APScheduler (BackgroundScheduler) FastAPI lifespan'da başlar; config `scheduler`:

| Job | Zamanlama (Europe/Istanbul) | İş |
|---|---|---|
| daily_refresh | Pzt-Cum 19:15 | 1mo bar upsert + hedefli F/SUE (son 5 günde bilanço açıklayanlar — PEAD tazeliği) + skor + tarama + bağlam + sonuç-takibi |
| kap_poll | Pzt-Cum 10-18, her 30dk | KAP + Gemini yorum; yeni olay → yeniden skorla |
| weekly_maintenance | Cmt 09:00 | tam evren F/SUE + PE/PB sweep'i → kalibrasyon → (4 haftada bir) event-study → yeniden skor |

- Her job kendi session'ını açar, hatada scheduler yaşar; sonuç `scheduler_state`
  config'ine yazılır (last_run/ok/note).
- API: `GET /api/scheduler` (durum+sıradaki koşum), `POST /api/scheduler/run/{job}`
  (manuel tetik — UI'daki "veriyi şimdi tazele" bunu çağırır).
- Otomatik event-study bir **parametre araması DEĞİLDİR**: aynı prior parametrelerle
  örneklem büyüdükçe yeniden ölçüm (aylık tekrar planın kendisiydi).
- start.bat değişmedi: backend ayakta kaldıkça sistem otonom döner; .bat'lar manuel
  alternatif olarak kalır.

## C.4 UI anlamlandırma (dashboard)

- **"Bugün Ne Yapmalı" paneli** (en üstte): yalnız `al-adayı` sinyaller, öncelik-sıralı,
  satır başına giriş→hedef/stop + beklenen net R + hedefte net R + öncelik barı + Al.
  Boş durum dürüst: "Bugün al-adayı sinyal yok — nakit de pozisyondur."
- **Setup kartları**: AL-ADAYI/İZLE/GİRME rozeti (hover=gerekçe), beklenti satırı
  (beklenen net R + başabaş isabet vs tarihsel isabet), öncelik barı (bileşenler
  hover'da), bağlam çarpanı ×0.xx, 📰 haber bonusu. "izle/girme" kartlar soluk.
- **🤖 otonom rozeti** (başlık): job durumları + sıradaki koşum (hover'da ayrıntı).
- **Bayat veri bandı**: son bar >4 gün eskiyse uyarı + "↻ veriyi şimdi tazele"
  (sunucu-taraflı daily_refresh tetikler).

---

# EK D — v2 Strateji Turu + Risk Profilleri (2026-07-07)

## D.1 Araştırma bulgusu: literatür 0/21 refüte

Çok-ajanlı derin araştırma (6 paralel track → 21 strateji-tanımlayıcı iddia → adversarial
refütasyon) çalıştırıldı. **21 iddianın 21'i de refüte edildi**: limit-devam (Çin/Tayvan
magnet+continuation), 52-hafta-zirve yakınlığı (George-Hwang), hacim primi (Gervais-Kaniel-
Mingelgrin), haftalık kısa-vade reversal, BIST contrarian (Bildik-Gülay, Demirer), rejim-
koşullu momentum (Cooper-Gutierrez-Hameed). Refütasyon gerekçeleri tutarlı: (a) maliyet
sonrası kaybolan edge, (b) BIST'e/emerging-market'e transfer edilemezlik, (c) örneklem-
dönemi madenciliği, (d) BIST'in zaten "dar bant" rejiminde olması (arbitraj yok).

**Sonuç dürüst ve önemli:** literatürde 0.28% maliyeti geçen bedava öğle yemeği YOK. Tek
kanıtlı edge sistem-içi `squeeze_breakout` (net PF 1.44) olarak kalıyor. Yeni dedektörler
literatür-gücü İDDİA ETMEZ; mekanizma-temellidir.

## D.2 Üç yeni dedektör (mekanizma-temelli; §9.5 tek event-study)

| Dedektör | Tez | Kanıt statüsü | Event-study NET sonucu |
|---|---|---|---|
| **Uzun Sıkışma Kırılımı** (htf_squeeze_breakout) | Kanıtlı squeeze edge'inin 60g-ufuk analojisi | medium (analoji) | **n=325, net PF 1.51, mean_R net 0.15 — İKİNCİ EDGE** ✓ |
| **Boşluk Tutunması** (gap_hold_continuation) | %3-7 tutunan hacimli gap = bilgili akış | prior-only | n=25, 5g excess −0.2% → **devre dışı** (çok nadir + düz) |
| **Düşüşte Dirençli Lider** (rs_shield) | Piyasa düşüşünde dirençli + stabilizasyon tetiği | prior-only | n=97 PF net 1.91 AMA yalnız 3 bağımsız gün (t=0) → **zayıf, temkinli** |

**En önemli kazanım:** Uzun Sıkışma Kırılımı, kanıtlı squeeze mekanizmasını daha uzun ufka
taşıdı ve orijinalden bile yüksek net PF (1.51 vs 1.44) ölçtü — tradeable fırsat kümesini
gerçek, ölçülmüş bir edge'le genişletti.

**Dürüst uyarı (rs_shield):** per-olay istatistikleri parlak (PF 1.9, %95 CI sıfırı hariç)
ama 97 olay yalnız 3 piyasa-dip-günü etrafında kümeleniyor → günlük-kümelenmiş t=0. Yani
gerçekte ~3 bağımsız gözlem. Sistem bunu doğru okudu: "kanıtlı" DEMEDİ, "zayıf" dedi.
Kayıplar bağımsız değil (piyasa 2. bacak → hepsi birlikte stoplanır); risk katmanı
korelasyon-kovası olarak ele alır.

**Refüte aileler bilinçli dışlandı:** her dedektörde `day_gain_max_pct` (tavan kovalama yok)
ve `max_entry_gap_pct=4` (ertesi açılış +%4 üstü gap'liyse iptal — kovalamama) kuralları var.
gap_hold'da pead-aktif çifte-sayım engeli (kazanç gap'i PEAD'e bırakılır).

## D.3 Gap-through (taban kilidi) simülasyon realizmi

Araştırmanın yüksek-güven bulgusu: **stop fill garanti değildir.** simulate_trade_detail
artık gap-through modeller: bar stop'un ALTINDA açılırsa fill = açılış (stop değil) → kayıp
−1R'den kötü olabilir (gap-down/limit). Simetrik: target üstünde açılırsa fill = açılış.
Bu tüm event-study + canlı OOS R'lerini daha gerçekçi (biraz daha kötü) yapar — dürüstlük.

## D.4 Risk profilleri (temkinli/dengeli/agresif)

`app/risk/profiles.py` — "ne kadar risk" tek anahtarla. Kelly-çapraz-sağlamalı (isabet ~%45,
K/L ~1.3 → tam Kelly ≈ %2.7): temkinli %0.5 (çeyrek-Kelly), dengeli %1 (varsayılan, eski
seed'le birebir), agresif %1.5 (yarım-Kelly tavanı — %2+ overbet bölgesi, MacLean/Thorp/
Ziemba). Profil `risk` config'ine merge edilir (base_r/heat/daily_stop/weekly_dd); sizing tek
kaynaktan okur. **Profil edge üretmez** — pozisyonu ölçekler; agresif kazancı VE kaybı büyütür.

Seçmeden önce dürüst matematik gösterilir (kesin DP, yaklaşıklık değil): N işlemde en az bir
kez K ardışık kayıp olasılığı + o serinin bileşik drawdown'u. Örn. %45 isabet, 50 işlem:
6'lı seri olasılığı ~%40, agresif profilde 8'li seri hesabı −%11 çukur. Agresif seçimi
onay-diyaloğu ister. API: `GET/PUT /api/risk/profile`.
