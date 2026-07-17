"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import Link from "next/link";
import { apiGet, apiPut } from "@/lib/api";
import { theme } from "@/lib/theme";

const DESC: Record<string, string> = {
  factor_weights: "CANLI faktör havuzu ağırlıkları (motor bunu okur — tek doğruluk kaynağı). Kalibrasyon artık bunu SESSİZCE EZMEZ (require_oos açıkken önerileri factor_weights_suggested'a yazar). Değerler placeholder PRIOR — factor diagnostic ölçümüyle doğrulanacak. Elle değiştirirsen anında etkir.",
  factor_weights_suggested: "Kalibrasyonun önerdiği ağırlıklar (IC-temelli) — CANLI DEĞİL. Beğenirsen değerleri factor_weights'e kopyala. Haftalık job yazar; salt-okunur.",
  weights: "Kalite alt-ağırlıkları (wf=F-Score, wa=accrual, wv=değerleme).",
  thresholds: "Eşikler: base_abs_threshold (Al barı), min_move_atr_pct (hareket tabanı), sector_cap, min_fscore, min_liq_tl.",
  risk: "Risk/sizing: base_r (trade riski %), k_atr (stop çarpanı), max_heat_pct, max_name_pct, cash_floor_pct, edge_*.",
  costs: "İşlem maliyeti (POLICY-KNOB): commission_pct_per_side + spread_slippage_pct_per_side. Round-trip = 2×(komisyon+spread); net R/beklenti bundan türetilir (saklanmaz — tarife değişimi geriye dönük hesaplanır). Broker tarifene göre güncelle.",
  risk_valve: "Skor kısıcı bayraklar: rsi/aşırı-uzama (extended_ema_pct, mult_extended)/ATH-kovalama (near_high_pct) cezaları.",
  news: "KAP haber capleri: neg_cap (-20), pos_cap (+12), pos_cap_unanchored.",
  event_durations: "KAP olay süreleri (gün) tip bazında.",
  decay_halflife_days: "Haber etkisinin yarı-ömrü (gün) tip bazında.",
  account: "Hesap: starting_cash_try (bütçe), annual_cpi (reel P&L), start_date.",
  cadence: "Job sıklıkları (dk).",
  calibration: "Kalibrasyon: require_oos_for_weight_change (True=haftalık job canlı ağırlığı EZMEZ, sadece önerir → factor_weights_suggested; False=doğrudan uygular), deneme sayacı, deflate.",
  prompts: "LLM promptları (KAP / ticker yorumu / market_comment=günün durumu).",
  setups: "Setup dedektör parametreleri (snapback/squeeze/pullback/pead/accumulation eşikleri, stop çarpanı, R, zaman çıkışı). Tümü prior — deneme disiplini geçerli (§9.5).",
  context: "Sektör & makro bağlam (üst-aşağı): regime ağırlıkları (EMA50/EMA200/genişlik/volatilite + risk-on/off eşikleri), sector (görece güç/momentum/genişlik ağırlıkları + lider/geride persentili), tilt (bağlam çarpanı base/span). PRIOR — edge değil, bağlam.",
  setup_market: "Setup piyasa bağlamı (mkt_ret_5d, mkt_above_ema50, breadth) — tarama yazar; UI okur.",
  setup_evidence: "Event-study kanıt blob'u (setup başına n/isabet/fazla getiri/t/PF/verdict + by_regime dilimi) — run_event_study.py yazar; salt-okunur, elle düzenleme.",
  market_context: "Derlenmiş makro rejim + sektör görece-güç tablosu — run_scoring yazar; UI (Piyasa & Sektör paneli) okur; salt-okunur.",
  priority: "İşlem-öncelik harmanı (formül ÖN-KAYITLI, priority.py): prior_weight_k (canlı OOS'un study'yi ne hızda ezeceği), prior_r (study'siz setup net-R prior'ı), e_cap_r (öncelik ölçek tavanı), w_strength, news_div. Sıralama + AL-ADAYI/İZLE/GİRME etiketi buradan.",
  risk_profile: "Aktif risk profili (temkinli/dengeli/agresif) — Ayarlar > Risk Profili panelinden değiştir; PUT /api/risk/profile 'risk' anahtarına merge eder (base_r/heat/stop'lar). Elle düzenleme yerine paneli kullan.",
  scheduler: "Otonom mod (Europe/Istanbul): enabled, daily_refresh_time (Pzt-Cum veri+skor+tarama), kap_poll_times (KAP+Gemini saatleri, örn. '11:00,14:00,17:00'; boş=kapalı), weekly_day/time (kalibrasyon), event_study_every_weeks (kanıt yeniden ölçümü). Değişiklik backend yeniden başlatınca etkin olur.",
  scheduler_state: "Otonom job'ların son koşum kayıtları (last_run/ok/note) — scheduler yazar; salt-okunur.",
  ai_budget: "AI günlük çağrı tavanı (free-tier koruması): daily_cap + evening_reserve (gün-içi KAP yorumunun KULLANAMAYACAĞI dilim — akşam brain/tez/görüş için saklanır). Aşılınca çağrı yapılmaz, sistem deterministik devam. 0 = AI tamamen kapalı.",
  brain_brief: "AI Brain'in son üretimi (facts + AI sentezi) — generate_brief yazar; salt-okunur.",
  outlook_brief: "Serbest Görüş'ün son üretimi (metin + kaynaklar + grounded bayrağı) — generate_outlook yazar; salt-okunur.",
  goals: "Kullanıcı hedefi: target_weekly_pct (%/hafta). Sistem hedefi DEĞİŞTİRMEZ — karne ve kâğıt-portföy panelleri ölçülen kapasite/gerçekleşenle dürüstçe kıyaslar.",
  auto_paper: "Otonom Sınav (kâğıt portföy): enabled + start_cash. Sistem her al-adayını SANAL defterde kendi kurallarıyla işler; gerçek emir YOK. Tam-otonomi kararının kanıt kaynağı.",
  paper_state: "Kâğıt portföyün canlı durumu (pozisyonlar/kapananlar/equity eğrisi) — paper_trader yazar; salt-okunur. Sıfırlamak için paneldeki 'sıfırla'.",
  ai_usage: "Bugünkü AI çağrı sayacı (date/count) — budget guard yazar; gece sıfırlanır; salt-okunur.",
  exits: "Çıkış politikası parametreleri (ön-kayıtlı): trail (HWM−trail_mult×risk iz-süren stop), partial_be (first_r'de scale_frac sat + başabaş). exit_study karşılaştırır; şu an 'fixed' en iyi (değişmedi).",
  exit_study: "Çıkış-politikası karşılaştırma sonucu (setup başına fixed/trail/partial_be net PF/R) — run_exit_study yazar; salt-okunur.",
  factor_diagnostic: "Ölçülen faktör rank-IC/t/isabet (Ayarlar sekmesi gösterir) — store_factor_diagnostic yazar; salt-okunur.",
};

// Salt-okunur config anahtarları (motor/event-study yazar; UI'da elle düzenlenmez).
const READONLY = new Set(["setup_evidence", "setup_market", "market_context", "scheduler_state",
  "factor_weights_suggested", "ai_usage", "exit_study", "factor_diagnostic", "brain_brief",
  "outlook_brief", "paper_state", "band_coverage", "composite_ic_live", "circuit_marks"]);

function Section({ k, value, onSaved }: { k: string; value: unknown; onSaved: () => void }) {
  const [text, setText] = useState(JSON.stringify(value, null, 2));
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const readOnly = READONLY.has(k);

  async function save() {
    setErr(null);
    let parsed: unknown;
    try {
      parsed = JSON.parse(text);
    } catch (e) {
      setErr("Geçersiz JSON: " + String(e));
      return;
    }
    setBusy(true);
    try {
      await apiPut(`/api/config/${k}`, { value: parsed });
      setSaved(true);
      setTimeout(() => setSaved(false), 1500);
      onSaved();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ border: `0.5px solid ${theme.border}`, background: theme.surface, borderRadius: 3, padding: 16, marginBottom: 14 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <h3 className="mono" style={{ fontSize: 14 }}>{k}{readOnly && <span style={{ fontSize: 11, color: theme.muted, marginLeft: 8 }}>salt-okunur</span>}</h3>
        {!readOnly && (
          <button onClick={save} disabled={busy} style={{ ...btn, borderColor: saved ? theme.positive : theme.border, color: saved ? theme.positive : theme.bone }}>
            {saved ? "✓ kaydedildi" : "Kaydet"}
          </button>
        )}
      </div>
      {DESC[k] && <p style={{ fontSize: 12, color: theme.muted, margin: "4px 0 10px" }}>{DESC[k]}</p>}
      <textarea value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} readOnly={readOnly}
        style={{ width: "100%", minHeight: 120, background: theme.bg, color: readOnly ? theme.muted : theme.bone, border: `0.5px solid ${theme.border}`,
          borderRadius: 3, padding: 10, fontSize: 12, fontFamily: "var(--font-mono)", resize: "vertical" }} />
      {err && <p style={{ color: theme.negative, fontSize: 12, marginTop: 6 }}>{err}</p>}
    </div>
  );
}

const btn: React.CSSProperties = { fontSize: 12, background: "transparent", color: theme.bone, border: `0.5px solid ${theme.border}`, borderRadius: 3, padding: "5px 12px", cursor: "pointer" };

// Önemli policy-knob'lar önce
const ORDER = ["factor_weights", "thresholds", "risk", "costs", "priority", "scheduler", "risk_valve", "setups", "account", "news", "event_durations", "decay_halflife_days", "weights", "calibration", "cadence", "scheduler_state", "setup_market", "setup_evidence", "prompts"];

export default function ConfigPage() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["config-all"], queryFn: () => apiGet<Record<string, unknown>>("/api/config") });
  const keys = data ? Object.keys(data).sort((a, b) => (ORDER.indexOf(a) + 1 || 99) - (ORDER.indexOf(b) + 1 || 99)) : [];

  return (
    <main style={{ maxWidth: 820, margin: "0 auto", padding: "32px 24px" }}>
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", borderBottom: `0.5px solid ${theme.border}`, paddingBottom: 16, marginBottom: 20 }}>
        <div>
          <h1 style={{ fontSize: 18, fontWeight: 500 }}>Config / Kalibrasyon</h1>
          <p style={{ fontSize: 13, color: theme.muted, marginTop: 2 }}>Ağırlık/eşik/risk/prompt — kaydet, motor anında okur. Değişiklikten sonra <span className="mono">refresh.bat</span>.</p>
        </div>
        <Link href="/" style={{ ...btn, textDecoration: "none" }}>← Dashboard</Link>
      </header>
      {isLoading ? <p style={{ color: theme.muted }}>yükleniyor…</p>
        : keys.map((k) => <Section key={k} k={k} value={data![k]} onSaved={() => qc.invalidateQueries({ queryKey: ["config-all"] })} />)}
    </main>
  );
}
