"""SCORING v0.2 §10 seed config. `config` tablosuna key→value (jsonb) olarak yazılır.

POLICY-KNOB = risk iştahı (backtest-fit değil, deneme-bütçesine girmez).
PRIOR       = kalibrasyon hedefi, deneme-bütçesine sayılır (§9.5).
"""

from __future__ import annotations

# Her anahtar `config` tablosunda bir satır olur.
SEED_CONFIG: dict[str, dict] = {
    "weights": {  # PRIOR — quality alt-ağırlıkları (sub_quality içi)
        "quality": {"wf": 0.6, "wa": 0.2, "wv": 0.2},
    },
    # Çok-faktör havuzu — TEK KAYNAK: scoring._DEF_FACTOR_WEIGHTS bunu import eder (fallback
    # asla sapmaz). Bu bir PLACEHOLDER PRIOR (kullanıcının momentum/stab tercihi) — HENÜZ
    # DOĞRULANMADI; ölçüm (factor diagnostic) sonrası nihai değerler belirlenecek. Kalibrasyon
    # artık bunu SESSİZCE EZMEZ: require_oos_for_weight_change=True iken öneriler ayrı
    # 'factor_weights_suggested' anahtarına yazılır (canlı ağırlık korunur — pipeline.weekly_calibrate).
    # DEFAULT = ölçülen IC'ye göre (573 isim, 2y factor diagnostic): low_atr t=4.25 (tek güçlü),
    # rev5 t=2.14, momentum(strength) t=1.60, roc20 t=0.89. Kullanıcı dashboard → Ayarlar'dan oynar.
    "factor_weights": {  # DEFAULT (ölçüm-temelli; UI'dan ayarlanır)
        "low_vol": 0.45, "rev5": 0.30, "momentum": 0.20, "roc20": 0.05,
        "quality": 0.0, "reversal": 0.0, "stab": 0.0, "cause": 0.0,
        "pead": 0.0, "value": 0.0,
    },
    # Olay-tetikli setup katmanı (SETUPS v0.1) — TÜM parametreler literatür-temelli PRIOR.
    # OPTİMİZASYON YOK; event_study.py bunları TEK koşumda doğrular (§9.5 deneme disiplini).
    # setups.py._DEF_SETUPS ile birebir aynı — buradan config'e yazılır.
    "setups": {  # PRIOR
        "common": {"min_liq_tl": 50_000_000, "min_bars": 200, "block_news_neg": -5},
        "snapback": {"roc3_decile": 0.10, "rsi_max": 35, "ema200_floor_mult": 0.90,
                     "ema50_floor_pct": -0.08, "idio_dd": -0.04, "mkt_floor_5d": -0.05,
                     "atr_stop_mult": 0.5, "rr": 2.0, "time_exit_days": 5, "valid_days": 2},
        "squeeze_breakout": {"bbwidth_pctile_max": 20, "vol_mult": 1.5, "close_range_min": 0.70,
                             "atr_stop_mult": 0.5, "rr": 2.0, "time_exit_days": 10, "valid_days": 2},
        "trend_pullback": {"roc20_min": 10, "ema20_band": 0.02, "rsi_low": 35, "rsi_high": 55,
                           "breadth_floor": 0.45, "atr_stop_mult": 0.5, "rr": 2.0,
                           "time_exit_days": 10, "valid_days": 2},
        "pead_drift": {"sue_min": 0.5, "kap_window_days": 3, "atr_stop_mult": 0.5, "rr": 2.0,
                       "time_exit_days": 15, "valid_days": 3},
        "quiet_accumulation": {"lookback": 10, "mkt_down_thr": -0.005, "vol_mult": 1.3,
                               "min_count": 3, "last_down_thr": -0.01, "atr_stop_mult": 0.5,
                               "rr": 2.0, "time_exit_days": 10, "valid_days": 3},
        # v2 dedektörler (2026-07-07 araştırma turu) — setups.py._DEF_SETUPS ile birebir.
        # Literatür 0/21 refüte; bunlar mekanizma-temelli (htf=squeeze analojisi 'medium',
        # diğerleri prior-only). day_gain_max/max_entry_gap = refüte aileleri dışlar.
        "htf_squeeze_breakout": {"range_lookback_days": 60, "width_pctile_max": 20,
                                 "pctile_window_days": 252, "breakout_lookback_days": 60,
                                 "vol_mult": 2.0, "close_range_min": 0.70, "day_gain_max_pct": 8,
                                 "max_entry_gap_pct": 4, "stop_lookback_low_days": 10,
                                 "atr_stop_mult": 0.5, "rr": 2.0, "time_exit_days": 15,
                                 "valid_days": 2, "min_bars": 260},
        "gap_hold_continuation": {"gap_min_pct": 3, "gap_max_pct": 7, "close_range_min": 0.75,
                                  "vol_mult": 2.5, "day_gain_max_pct": 9, "ema_filter_len": 50,
                                  "max_entry_gap_pct": 4, "atr_stop_mult": 0.5, "rr": 2.0,
                                  "time_exit_days": 10, "valid_days": 1},
        "rs_shield": {"mkt_ret_lookback_days": 15, "mkt_ret_max": -0.05, "stock_ret_min": 0.0,
                      "rs_pctile_min": 90, "trigger_mkt_day_gain_min": 0.01,
                      "max_entry_gap_pct": 4, "stop_lookback_low_days": 10, "atr_stop_mult": 0.5,
                      "rr": 2.0, "time_exit_days": 12, "valid_days": 2},
    },
    "thresholds": {
        "base_abs_threshold": 60,  # POLICY-KNOB
        "abs_adapt": {"alpha": 4.0, "beta": 3.0, "floor_thr": 55, "ceil_thr": 72},  # PRIOR
        "min_cause": 55,  # PRIOR
        "min_stab": 50,  # PRIOR
        "min_fscore": 5,  # PRIOR
        "min_liq_tl": 50_000_000,
        "sector_cap": 3,  # fırsat listesinde sektör başına max isim (çeşitlilik; finansal kümelenmeyi kırar)
        # M-tabanı GEVŞETİLDİ (0.025→0.005): backtest düşük-vol'ün edge olduğunu gösterdi;
        # eski 2.5% taban tam da edge'i taşıyan isimleri eliyordu. Artık sadece ölü/illikit
        # isimleri eler. POLICY-KNOB (tez-bağımsız sisteme geçildi, +%7,5 kilidi kalktı).
        "min_move_atr_pct": 0.005,
    },
    # Çıkış politikaları (simulate_trade_detail) — ÖN-KAYITLI, parametre araması YOK (§9.5).
    # Study (exit_study.py) squeeze ailesinde AYNI girişlerle karşılaştırır; 'kanıtlı' iyileşme
    # çıkarsa canlıya bağlanır. 'fixed' varsayılan kalır (davranış değişmez).
    "exits": {  # PRIOR
        "trail": {"trail_mult": 2.0},                    # HWM − 2R iz-süren stop, sabit hedef yok
        "partial_be": {"first_r": 1.0, "scale_frac": 0.5},  # 1R'de yarı sat + başabaş stop
    },
    "costs": {  # POLICY-KNOB — işlem maliyeti (broker tarifesine göre güncelle)
        # Round-trip maliyet fraksiyonu = 2 × (commission + slippage). Muhafazakâr varsayılan.
        "commission_pct_per_side": 0.0004,
        "spread_slippage_pct_per_side": 0.0010,
        "note": "Midas tarifesine göre güncelle; slippage küçük-cap'te daha yüksek olabilir",
    },
    # Risk iştahı — app/risk/profiles.py PROFILES'tan uygulanır (PUT /api/risk/profile).
    # Profil 'risk' anahtarına merge edilir; sizing tek kaynaktan okumaya devam eder.
    "risk_profile": {"active": "dengeli"},  # POLICY
    "risk": {
        "base_r": 0.01,  # POLICY-KNOB (risk_profile uygulayınca güncellenir)
        "k_atr": 2.0,  # PRIOR
        "edge_scaling_enabled": False,
        "edge_min_live_trades": 100,
        "edge_scale": 0.5,  # PRIOR (fractional)
        "edge_factor_floor": 0.25,
        "edge_factor_cap": 0.90,  # cap<1 KORUNUR
        "max_leverage": 1.0,  # >1 AÇILMAZ
        "max_name_pct": 0.30,  # not: heat bağlayıcı, bu nadiren bağlar
        "max_heat_pct": 0.06,
        "cash_floor_pct": 0.15,
        "daily_stop_pct": 0.03,
        "weekly_dd_pct": 0.10,
    },
    "risk_valve": {  # PRIOR — kalibre + deneme-bütçesine say
        "rsi_overbought": 80,
        "today_spike_pct": 0.08,
        "extreme_atr_pct": 0.12,
        "mult_rsi": 0.6,
        "mult_spike": 0.5,
        "mult_atr": 0.6,
        "mult_binary": 0.5,
        "mult_thin_liq": 0.7,
        # aşırı-uzama / ATH-kovalama guard'ı (yukarıyı kovalamayı cezalandırır)
        "rsi_high": 72, "mult_rsi_high": 0.85,
        "extended_ema_pct": 0.18, "mult_extended": 0.7,
        "near_high_pct": -0.03, "mult_near_high": 0.8,
        "floor": 0.2,
    },
    "news": {  # PRIOR
        "neg_cap": -20,
        "pos_cap": 12,
        "pos_cap_unanchored": 4,
    },
    "event_durations": {  # PRIOR — kalibre
        "finansal_tablo": 60,
        "temettu_to_exdate": True,
        "onemli_sozlesme": 30,
        "bedelli": 20,
        "bedelsiz": 20,
        "yonetici_islem": 50,
        "pay_geri_alim": 30,
        "diger": 7,
    },
    "decay_halflife_days": {  # PRIOR — per-tip
        "finansal_tablo": 10,
        "onemli_sozlesme": 10,
        "yonetici_islem": 25,
        "diger": 2.5,
    },
    "account": {  # POLICY — hesap & reel P&L girdileri (kullanıcı günceller)
        "starting_cash_try": 10_000.0,
        "start_date": "2026-06-22",
        "annual_cpi": 0.35,  # reel getiri için (EVDS otomasyonu sonra)
    },
    # Sektör & Makro bağlam (üst-aşağı) — PRIOR (backtest edilmedi; edge değil, bağlam tilt'i).
    # sector_macro.py._DEF_CONTEXT ile birebir; buradan config'e yazılır.
    "context": {  # PRIOR
        "regime": {"w_ema50": 15.0, "w_ema200": 10.0, "w_breadth": 40.0,
                   "vol_ref": 0.02, "w_vol": 250.0, "risk_on_thr": 60.0, "risk_off_thr": 40.0},
        "sector": {"w_rel": 0.5, "w_mom": 0.3, "w_breadth": 0.2, "min_members": 3,
                   "lider_pctile": 0.67, "geride_pctile": 0.33},
        "tilt": {"base": 0.85, "span": 0.30},
    },
    # İşlem-öncelik harmanı (app/engine/priority.py ile birebir) — formül ÖN-KAYITLI,
    # parametre araması yok. E = shrinkage(event-study net R, canlı OOS); E≤0 → "izle".
    "priority": {  # POLICY/PRIOR
        "prior_weight_k": 20,     # study/prior sözde-n; canlı sonuç biriktikçe önemi düşer
        # study'siz setup prior net R (htf = squeeze analojisi; kalan v2 prior-only → 0)
        "prior_r": {"pead_drift": 0.05, "htf_squeeze_breakout": 0.05, "_default": 0.0},
        "e_cap_r": 0.30,          # E→100 taban ölçek tavanı
        "w_strength": 0.40,       # sinyal gücünün paydaki payı
        "news_div": 100.0,        # haber çarpanı bölücüsü (pos_cap=12 → ≤×1.12)
    },
    # Otonom orkestrasyon (app/scheduler.py ile birebir) — saatler Europe/Istanbul.
    "scheduler": {  # POLICY
        "enabled": True,
        "timezone": "Europe/Istanbul",
        "daily_refresh_time": "19:15",   # Pzt-Cum: 1mo bar + hedefli F/SUE + skor + tarama + sonuçlar
        "history_period": "1mo",
        "fundamentals_kap_days": 5,      # gecelik: son N günde bilanço açıklayanların F/SUE'su
        "kap_poll_minutes": 30,          # 0 = kapalı; piyasa saatlerinde (10-18) KAP+Gemini
        "rescore_after_kap": True,
        "weekly_day": "sat",
        "weekly_time": "09:00",          # faktör kalibrasyonu
        "weekly_fundamentals": True,     # Cmt: tam evren F-Score+SUE sweep'i
        "weekly_valuation": True,        # Cmt: tam evren PE/PB sweep'i (resume-safe)
        "event_study_every_weeks": 4,    # N haftada bir kanıt yeniden ölçümü (0 = kapalı)
        "narrative_enabled": True,       # gecelik: grounded analist tezleri + karne (key/kota yoksa no-op)
    },
    # AI çağrı bütçesi — free-tier'ı koruyan SERT günlük tavan (app/llm/budget.py).
    # Her Gemini çağrısı try_consume'dan geçer; aşılırsa çağrı yapılmaz, sistem deterministik
    # devam eder. 0 = AI kapalı. KAP yorumu + on-demand ticker AI + market anlatı bunu paylaşır.
    "ai_budget": {"daily_cap": 50},  # POLICY-KNOB
    "cadence": {"snapshot_min": 2, "news_min": 5, "score_min": 5},
    "calibration": {
        "trial_counter": True,
        "deflate_with_trials": True,
        "require_oos_for_weight_change": True,
    },
    "prompts": {
        "kap_interpret": "<§18.1: tip + niteliksel ton/yön + confidence + materiality. SAYI YOK.>",
        "ticker_comment": "<§18.2: verilen hesaplanmış bağlamı yorumla; yeni sayı üretme.>",
        "market_comment": "<§18.2: derlenmiş makro rejim + sektör tablosunu 'günün durumu' diye yorumla; haber/sayı uydurma.>",
    },
}
