"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import Link from "next/link";
import { useDashboardPrefs, type DashboardPrefs, type SectionId } from "@/lib/dashboardPrefs";
import { Icon } from "@/lib/icons";
import {
  apiGet, apiPost, apiPut, fmt, fetchSetups, fetchSetupEvidence, fetchSetupOutcomes,
  fetchContext, fetchContextAI, fetchAiBudget, fetchScheduler, fetchStrategies,
  runSchedulerJob, midasUrl, tvUrl,
  type AccountConfig, type ContextAI, type EvidenceRegimeSlice, type EvidenceSetup,
  type Health, type MarketContext,
  type Opportunities, type Portfolio, type Position, type Scores, type ScoreRow,
  type SectorRow, type SetupEvidence, type SetupOutcomes, type SetupsResponse,
  type Sparkline, type SparkResponse, SIGNAL_TR, type SetupSignal, type StrategyRow,
} from "@/lib/api";
import { scoreColor, theme } from "@/lib/theme";

const pnlColor = (v: number) => (v > 0 ? theme.positive : v < 0 ? theme.negative : theme.muted);

const FACTOR_TR: Record<string, string> = {
  low_vol: "Düşük volatilite (BAB — tek güçlü, t=4.25)",
  rev5: "Kısa-vade dönüş 5g (t=2.14)",
  momentum: "Trend gücü (bileşik EMA+RSI+ROC+52h)",
  roc20: "20g momentum (düz, zayıf)",
  pead: "PEAD (kazanç sürprizi katalisti)",
  quality: "Kalite (Piotroski F + accrual)",
  value: "Ucuzluk (PE/PB)",
  stab: "Stabilizasyon (dönüş)",
  reversal: "Oversold (reversal — negatif)",
  cause: "Sebep (sektör ko-hareketi)",
};

// Skorun hangi metriklere dayandığını açıklayan kopyalanabilir metin (#1 bias yorum)
function buildExplanation(r: ScoreRow): string {
  const fac = (r.reasoning?.factors ?? {}) as Record<string, number>;
  const w = (r.reasoning?.factor_weights ?? {}) as Record<string, number>;
  const keys = Object.keys(fac).filter((k) => fac[k] != null).sort((a, b) => (w[b] ?? 0) - (w[a] ?? 0));
  const wsum = keys.reduce((s, k) => s + (w[k] ?? 0), 0) || 1;
  const core = keys.reduce((s, k) => s + (fac[k] ?? 0) * (w[k] ?? 0), 0) / wsum;
  const lines = keys
    .filter((k) => (w[k] ?? 0) > 0)
    .map((k) => `  • ${FACTOR_TR[k] ?? k}: ${Math.round(fac[k])}/100 × ağırlık ${(w[k] ?? 0).toFixed(2)} = +${(((fac[k] ?? 0) * (w[k] ?? 0)) / wsum).toFixed(1)}`);
  const rg = r.risk_governor ?? 1;
  const np = r.news_pos ?? 0, nn = r.news_neg ?? 0;
  const thr = r.reasoning?.abs_threshold_eff;
  const gate = r.reasoning?.gate_reasons ?? [];
  return [
    `${r.ticker}${r.sector ? ` (${r.sector})` : ""} — Skor ${r.score.toFixed(1)} · ${SIGNAL_TR[r.signal] ?? r.signal}`,
    r.meets_absolute_threshold ? `✓ Mutlak eşiği geçti${thr ? ` (eşik ${thr.toFixed(0)})` : ""}` : `✗ Eşik altı / kapı kapalı${thr ? ` (eşik ${thr.toFixed(0)})` : ""}`,
    "",
    "FAKTÖR KATKILARI (değer × ağırlık, ağırlıklar kalibrasyonla öğrenildi):",
    ...lines,
    `  → çekirdek (core) = ${core.toFixed(1)}`,
    "",
    `Risk valfi: ×${rg.toFixed(2)}${rg < 1 ? " (aşırı-alım/uzama/ATH-kovalama cezası)" : ""}`,
    `Haber (KAP): ${np > 0 ? `+${np.toFixed(1)}` : ""}${nn < 0 ? ` ${nn.toFixed(1)}` : np > 0 ? "" : " yok"}`,
    `F-Score: ${r.reasoning?.f_score ?? "N/A (banka/finansal)"} · ATR%: ${r.reasoning?.atr_pct != null ? (r.reasoning.atr_pct * 100).toFixed(1) + "%" : "—"}`,
    gate.length ? `Kapı notları: ${gate.join(", ")}` : null,
    "",
    "Formül: skor = (Σ faktör×ağırlık + haber_pozitif) × risk_valfi + haber_negatif",
    "Not: low-vol kanıtlı edge; PEAD/kalite research-prior. Sistem yatırım tavsiyesi değildir.",
  ].filter((l): l is string => l !== null).join("\n");
}

function CopyBtn({ r }: { r: ScoreRow }) {
  const [done, setDone] = useState(false);
  return (
    <button
      title="Skor gerekçesini kopyala"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(buildExplanation(r));
          setDone(true);
          setTimeout(() => setDone(false), 1500);
        } catch {
          setDone(false);
        }
      }}
      style={{ ...btn, marginLeft: 6 }}
    >
      {done ? "✓" : <Icon name="copy" />}
    </button>
  );
}

function HealthBadge() {
  const { data, isError } = useQuery({ queryKey: ["health"], queryFn: () => apiGet<Health>("/health"), refetchInterval: 30_000 });
  const ok = data?.status === "ok";
  const color = isError || !data ? theme.muted : ok ? theme.positive : theme.warning;
  const label = isError ? "backend yok" : !data ? "…" : ok ? `bağlı · v${data.version}` : "degraded";
  return <span className="mono" style={{ fontSize: 12, color }}>● {label}</span>;
}

function Links({ t }: { t: string }) {
  const a: React.CSSProperties = { fontSize: 11, color: theme.muted, textDecoration: "none",
    border: `0.5px solid ${theme.border}`, borderRadius: 3, padding: "1px 6px" };
  return (
    <span style={{ display: "inline-flex", gap: 6 }}>
      <a href={tvUrl(t)} target="_blank" rel="noopener noreferrer" style={a} title="TradingView">TV</a>
      <a href={midasUrl(t)} target="_blank" rel="noopener noreferrer" style={a} title="Midas">Midas</a>
    </span>
  );
}

function Spark({ pts, color, h = 60 }: { pts?: number[]; color: string; h?: number }) {
  if (!pts || pts.length < 2)
    return <div style={{ height: h, display: "flex", alignItems: "center", justifyContent: "center", color: theme.muted, fontSize: 11 }}>grafik yok</div>;
  const w = 300, min = Math.min(...pts), max = Math.max(...pts), rng = max - min || 1;
  const step = w / (pts.length - 1);
  const d = pts.map((p, i) => `${i === 0 ? "M" : "L"}${(i * step).toFixed(1)},${(h - ((p - min) / rng) * h).toFixed(1)}`).join(" ");
  return (
    <svg width="100%" height={h} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{ display: "block" }}>
      <path d={d} fill="none" stroke={color} strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

// --- Setup katmanı (kısa-vade işlem sinyalleri) -------------------------

// Her setup tipine tutarlı bir renk (koyu tema paleti; badge kenarlığı + metni).
const SETUP_COLOR: Record<string, string> = {
  snapback: "#A23B43",          // negative/oxblood ailesi — panik dönüşü
  squeeze_breakout: "#C08A3E",  // warning/amber — sıkışma kırılımı
  trend_pullback: "#5E8C6A",    // positive/yeşil — trend içi
  pead_drift: "#7C6FB0",        // mor — bilanço sürprizi
  quiet_accumulation: "#5C86A8", // mavi — sessiz toplama
};
const setupColor = (s: string): string => SETUP_COLOR[s] ?? theme.muted;

// Kanıt verdict → renk (kanıtlı=yeşil, zayıf=amber, deneysel/diğer=gri, devre dışı=oxblood).
function verdictColor(v: string): string {
  if (v === "kanıtlı") return theme.positive;
  if (v === "zayıf") return theme.warning;
  if (v === "devre dışı") return theme.oxblood;
  return theme.muted; // deneysel / deneysel (prior — PIT yok)
}

// Kanıt rozetinin hover başlığı (n, isabet, ort. 5g fazla getiri, t, PF).
function evidenceTitle(ev: SetupSignal["evidence"]): string {
  const parts: string[] = [];
  if (ev.n_events != null) parts.push(`n=${ev.n_events}`);
  if (ev.hit_rate != null) parts.push(`isabet ${(ev.hit_rate * 100).toFixed(0)}%`);
  if (ev.mean_excess_5d != null) parts.push(`ort. 5g fazla getiri ${(ev.mean_excess_5d * 100).toFixed(1)}%`);
  if (ev.t != null) parts.push(`t=${ev.t.toFixed(1)}`);
  if (ev.profit_factor != null) parts.push(`PF=${ev.profit_factor.toFixed(2)}`);
  if (ev.status) parts.push(ev.status);
  return parts.length ? parts.join(" · ") : "event-study kanıtı yok (prior)";
}

// --- işlem-öncelik katmanı (backend priority.py'nin görünür yüzü) --------

const ADVICE_META: Record<string, { label: string; color: string }> = {
  "al-adayı": { label: "AL-ADAYI", color: theme.positive },
  izle: { label: "İZLE", color: theme.muted },
  girme: { label: "GİRME", color: theme.oxblood },
};

function AdviceChip({ s }: { s: SetupSignal }) {
  const m = ADVICE_META[s.advice] ?? ADVICE_META.izle;
  return (
    <span title={s.advice_reason} style={{ fontSize: 11, fontWeight: 600, letterSpacing: 0.4,
      color: m.color, border: `0.5px solid ${m.color}`, borderRadius: 3, padding: "1px 7px",
      cursor: "help", whiteSpace: "nowrap" }}>
      {m.label}
    </span>
  );
}

const rSignColor = (v: number | null | undefined) =>
  v == null ? theme.muted : v > 0 ? theme.positive : v < 0 ? theme.negative : theme.muted;

// Rejim → tek cümle zemin yorumu (Bugün paneli; PRIOR bağlam, kanıt değil).
function regimeAdvice(regime?: string | null): string {
  if (regime === "risk_on") return "zemin kırılım/trend setup'larına uygun";
  if (regime === "risk_off") return "savunma modu — yeni pozisyonu küçült ya da bekle; nakit de pozisyondur";
  return "seçici ol — yalnız ölçülen net-beklentisi pozitif sinyaller";
}

// "Bugün Ne Yapmalı" — sistemin TÜM katmanlarını (setup + kanıt + OOS + maliyet + bağlam)
// tek karar listesine indirger. Boş durumu dürüst: sinyal yoksa "yok" der, üretmeye zorlamaz.
function TodayPanel({ resp, onTrade }: { resp?: SetupsResponse; onTrade: (s: SetupSignal) => void }) {
  const all = resp?.setups ?? [];
  const buys = all.filter((s) => s.advice === "al-adayı").slice(0, 5);
  const nWatch = all.filter((s) => s.advice === "izle").length;
  const nBlocked = all.filter((s) => s.advice === "girme").length;
  const m = resp?.market;
  const rg = m?.regime ? regimeMeta(m.regime) : null;
  const accent = buys.length ? theme.positive : theme.border;

  return (
    <div style={{ border: `0.5px solid ${theme.border}`, borderLeft: `2px solid ${accent}`,
      background: theme.surface, borderRadius: 4, padding: 16 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <h2 style={{ fontSize: 14, fontWeight: 500 }}><Icon name="clipboard" size={15} /> Bugün Ne Yapmalı</h2>
        <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
          {rg && (
            <span style={{ fontSize: 11, color: rg.color, border: `0.5px solid ${rg.color}`,
              borderRadius: 3, padding: "1px 7px" }}
              title={`rejim skoru ${m?.regime_score != null ? Math.round(m.regime_score) : "—"}/100 (PRIOR bağlam — kanıt değil)`}>
              {rg.label}
            </span>
          )}
          {rg && <span style={{ fontSize: 11, color: theme.muted }}>{regimeAdvice(m?.regime)}</span>}
        </div>
      </div>

      {buys.length === 0 ? (
        <div style={{ marginTop: 12, padding: "18px 12px", textAlign: "center" }}>
          <div style={{ fontSize: 14 }}>Bugün al-adayı sinyal yok — nakit de pozisyondur.</div>
          <div style={{ fontSize: 12, color: theme.muted, marginTop: 6 }}>
            {all.length === 0
              ? "Aktif sinyal yok; sistem sinyal üretmeye zorlanmaz."
              : `${nWatch} sinyal "izle" (ölçülen net beklenti ≤ 0 ya da kanıt yok)${nBlocked ? `, ${nBlocked} sinyal maliyet nedeniyle girilemez` : ""}.`}
          </div>
        </div>
      ) : (
        <div style={{ marginTop: 10 }}>
          {buys.map((s, i) => {
            const col = setupColor(s.setup);
            return (
              <div key={`${s.ticker}-${s.setup}`}
                style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap",
                  padding: "9px 4px", borderTop: i === 0 ? "none" : `0.5px solid ${theme.border}` }}>
                <span className="mono" style={{ fontSize: 12, color: theme.muted, width: 14 }}>{i + 1}</span>
                <Link href={`/ticker/${s.ticker}`} className="mono"
                  style={{ fontSize: 15, color: theme.bone, textDecoration: "none", minWidth: 64 }}>
                  {s.ticker}
                </Link>
                <span style={{ fontSize: 11, color: col, border: `0.5px solid ${col}`,
                  borderRadius: 3, padding: "1px 6px", whiteSpace: "nowrap" }}>{s.setup_label}</span>
                <span className="mono" style={{ fontSize: 12, color: theme.muted, whiteSpace: "nowrap" }}>
                  ₺{fmt(s.entry_ref, 2)} → <span style={{ color: theme.positive }}>₺{fmt(s.target, 2)}</span>
                  {" "}· stop <span style={{ color: theme.negative }}>₺{fmt(s.stop, 2)}</span>
                </span>
                <span className="mono" title={`Beklenen NET R/işlem — kaynak: ${s.expected_r_src}`}
                  style={{ fontSize: 12, color: rSignColor(s.expected_r_net), cursor: "help", whiteSpace: "nowrap" }}>
                  beklenti {s.expected_r_net > 0 ? "+" : ""}{s.expected_r_net.toFixed(2)}R
                </span>
                {s.plan && (
                  <span className="mono" style={{ fontSize: 11, color: theme.muted, whiteSpace: "nowrap" }}
                    title={`Hedef vurulursa net +${s.plan.net_target_r.toFixed(2)}R; stop'ta ${s.plan.net_stop_r.toFixed(2)}R; başabaş isabet %${(s.plan.breakeven_hit * 100).toFixed(0)} (maliyet dahil)`}>
                    hedefte +{s.plan.net_target_r.toFixed(1)}R
                  </span>
                )}
                <span style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8 }}>
                  <span title={`öncelik ${s.priority.toFixed(0)}/100 (beklenti × güç × bağlam × haber)`}
                    style={{ width: 52, height: 5, background: theme.border, borderRadius: 3 }}>
                    <span style={{ display: "block", width: `${Math.max(0, Math.min(100, s.priority))}%`,
                      height: "100%", background: scoreColor(s.priority), borderRadius: 3 }} />
                  </span>
                  <button onClick={() => onTrade(s)} style={{ ...btn, borderColor: theme.positive, color: theme.positive }}>Al</button>
                </span>
              </div>
            );
          })}
          <div style={{ fontSize: 11, color: theme.muted, marginTop: 10, lineHeight: 1.5 }}>
            Sıralama = ölçülen net beklenti (event-study + canlı sonuç harmanı) × sinyal gücü × bağlam × haber.
            {nWatch > 0 && ` ${nWatch} sinyal "izle" durumunda (aşağıda).`}
            {nBlocked > 0 && ` ${nBlocked} sinyal maliyet nedeniyle girilemez.`} Yatırım tavsiyesi değildir.
          </div>
        </div>
      )}
    </div>
  );
}

// --- Strateji Karnesi — kanıt × canlı × harman tek tabloda -----------------

const STATUS_META: Record<string, { color: string; hint: string }> = {
  "işlemde": { color: theme.positive, hint: "ölçülen net beklenti > 0 — sinyalleri al-adayı olur" },
  "izle": { color: theme.muted, hint: "beklenti ≤ 0 ya da yalnız prior — sinyaller izlenir, alınmaz" },
  "devre dışı": { color: theme.oxblood, hint: "kanıt negatif — sinyalleri gizlenir" },
};

function StrategyPanel() {
  const { data } = useQuery({ queryKey: ["strategies"], queryFn: fetchStrategies, refetchInterval: 60_000 });
  if (!data || !data.strategies.length) return null;
  const f2 = (v: number | null | undefined, plus = true) =>
    v == null ? "—" : `${plus && v > 0 ? "+" : ""}${v.toFixed(2)}`;
  return (
    <div>
      <Wrap>
        <table style={tbl}>
          <thead><Trh cols={["Strateji", "Durum", "Beklenti (net R)", "Kanıt (n · R̄net · PF)", "Rejim ↑/↓", "Canlı (n · R̄net)", "verdict"]} /></thead>
          <tbody>
            {data.strategies.map((s: StrategyRow) => {
              const sm = STATUS_META[s.status] ?? STATUS_META["izle"];
              const col = setupColor(s.setup);
              const up = s.study.by_regime?.up, dn = s.study.by_regime?.down;
              return (
                <tr key={s.setup} style={{ borderTop: `0.5px solid ${theme.border}`, opacity: s.status === "devre dışı" ? 0.55 : 1 }}>
                  <td style={{ padding: "8px 12px" }}>
                    <span style={{ fontSize: 12, color: col, border: `0.5px solid ${col}`, borderRadius: 3, padding: "1px 6px", whiteSpace: "nowrap" }}>{s.label}</span>
                  </td>
                  <td style={{ padding: "8px 12px" }}>
                    <span title={sm.hint} style={{ fontSize: 11, color: sm.color, border: `0.5px solid ${sm.color}`, borderRadius: 3, padding: "1px 6px", cursor: "help", whiteSpace: "nowrap" }}>{s.status.toUpperCase()}</span>
                  </td>
                  <td className="mono" style={{ padding: "8px 12px", fontSize: 12 }}>
                    <span title={s.expected_r_src} style={{ color: rSignColor(s.expected_r_net), cursor: "help" }}>{f2(s.expected_r_net)}R</span>
                  </td>
                  <td className="mono" style={{ padding: "8px 12px", fontSize: 12, color: theme.muted }}>
                    {s.study.n != null
                      ? <>n={s.study.n}{s.study.n_days != null && s.study.n_days < 10 && <span title="bağımsız gün sayısı çok az — istatistik kümelenmiş, kırılgan" style={{ color: theme.warning, cursor: "help" }}> ⚠{s.study.n_days}g</span>} · <span style={{ color: rSignColor(s.study.mean_r_net) }}>{f2(s.study.mean_r_net)}</span> · {s.study.pf_net != null ? s.study.pf_net.toFixed(2) : "—"}</>
                      : "kanıt yok (prior)"}
                  </td>
                  <td className="mono" style={{ padding: "8px 12px", fontSize: 12 }}>
                    {up || dn ? (
                      <span title={`tetikte piyasa EMA50 üstü (n=${up?.n ?? 0}) / altı (n=${dn?.n ?? 0})`} style={{ cursor: "help" }}>
                        <span style={{ color: rSignColor(up?.mean_R_net) }}>{f2(up?.mean_R_net)}</span>
                        <span style={{ color: theme.muted }}> / </span>
                        <span style={{ color: rSignColor(dn?.mean_R_net) }}>{f2(dn?.mean_R_net)}</span>
                      </span>
                    ) : <span style={{ color: theme.muted }}>—</span>}
                  </td>
                  <td className="mono" style={{ padding: "8px 12px", fontSize: 12, color: theme.muted }}>
                    {s.live.n_closed > 0
                      ? <>n={s.live.n_closed} · <span style={{ color: rSignColor(s.live.mean_r_net) }}>{f2(s.live.mean_r_net)}</span></>
                      : s.live.n_pending > 0 ? `${s.live.n_pending} beklemede` : "—"}
                  </td>
                  <td style={{ padding: "8px 12px" }}>
                    <span style={{ fontSize: 11, color: verdictColor(s.verdict ?? "deneysel") }}>{s.verdict}</span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Wrap>
      <div style={{ fontSize: 11, color: theme.muted, marginTop: 6, lineHeight: 1.5 }}>{data.note}</div>
    </div>
  );
}

// AI bütçe rozeti — günlük Gemini çağrı kotası (free-tier koruması). Hover: detay.
function AiBudgetBadge() {
  const { data } = useQuery({ queryKey: ["ai-budget"], queryFn: fetchAiBudget, refetchInterval: 60_000 });
  if (!data) return null;
  const color = !data.enabled ? theme.muted : data.exhausted ? theme.negative
    : data.remaining <= 5 ? theme.warning : theme.muted;
  const label = !data.enabled ? "AI kapalı" : `AI ${data.used}/${data.cap}`;
  const title = data.enabled
    ? `Günlük AI çağrı kotası (free-tier koruması): ${data.used} kullanıldı, ${data.remaining} kaldı. `
      + `Yarın sıfırlanır. Config → ai_budget.daily_cap ile ayarla; 0 = AI kapalı.`
    : "AI kapalı (ai_budget.daily_cap = 0)";
  return <span className="mono" title={title} style={{ fontSize: 12, color, cursor: "help", whiteSpace: "nowrap" }}><Icon name="ai" /> {label}</span>;
}

// Otonom mod rozeti — scheduler durumu (başlık satırında). Hover: job detayları.
function SchedulerBadge() {
  const { data, isError } = useQuery({ queryKey: ["scheduler"], queryFn: fetchScheduler, refetchInterval: 60_000 });
  if (isError || !data) return null;
  const anyErr = data.jobs.some((j) => j.last && !j.last.ok);
  const daily = data.jobs.find((j) => j.id === "daily_refresh");
  const nextT = daily?.next_run ? daily.next_run.slice(11, 16) : null;
  const color = !data.enabled || !data.running ? theme.muted : anyErr ? theme.warning : theme.positive;
  const label = !data.enabled ? "otonom kapalı" : !data.running ? "otonom bekliyor"
    : anyErr ? "otonom · job hatası" : `otonom${nextT ? ` · ${nextT}` : ""}`;
  const title = data.jobs.map((j) =>
    `${j.name}\n  ${j.last ? `${j.last.ok ? "✓" : "✗ HATA"} ${j.last.last_run.slice(0, 16).replace("T", " ")} — ${j.last.note}` : "henüz koşmadı"}${j.next_run ? `\n  sıradaki: ${j.next_run.slice(0, 16).replace("T", " ")}` : ""}`
  ).join("\n");
  return (
    <span className="mono" title={title} style={{ fontSize: 12, color, cursor: "help", whiteSpace: "nowrap" }}>
      <Icon name="cpu" /> {label}
    </span>
  );
}

// Veri bayatlığı uyarısı + tek tıkla sunucu-taraflı tazeleme (otonom job'ı hemen koştur).
function StaleBanner({ lastBar }: { lastBar: string }) {
  const [busy, setBusy] = useState(false);
  const [started, setStarted] = useState(false);
  const days = Math.floor((Date.now() - new Date(lastBar.slice(0, 10)).getTime()) / 86_400_000);
  if (!(days > 4)) return null;
  return (
    <div style={{ marginTop: 12, border: `0.5px solid ${theme.warning}`, borderRadius: 4,
      padding: "10px 14px", display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
      <span style={{ fontSize: 12, color: theme.warning }}>
        ⚠ Veri bayat görünüyor — son bar {lastBar.slice(0, 10)} ({days} gün önce). Otonom mod backend
        açıkken her akşam 19:15'te tazeler; start.bat kapalı kaldıysa şimdi elle tetikleyebilirsin.
      </span>
      <button disabled={busy || started} style={{ ...btn, borderColor: theme.warning, color: theme.warning }}
        onClick={async () => {
          setBusy(true);
          try { await runSchedulerJob("daily_refresh"); setStarted(true); } finally { setBusy(false); }
        }}>
        {started ? "✓ başladı (birkaç dk sürer)" : busy ? "…" : "↻ veriyi şimdi tazele"}
      </button>
    </div>
  );
}

// Türkçe düz-metin açıklama (kopyala düğmesi).
function buildSetupExplanation(s: SetupSignal): string {
  const ev = s.evidence;
  const evLine: string[] = [];
  if (ev.n_events != null) evLine.push(`n=${ev.n_events}`);
  if (ev.hit_rate != null) evLine.push(`isabet ${(ev.hit_rate * 100).toFixed(0)}%`);
  if (ev.mean_excess_5d != null) evLine.push(`ort. 5g fazla getiri ${(ev.mean_excess_5d * 100).toFixed(1)}%`);
  if (ev.t != null) evLine.push(`t=${ev.t.toFixed(1)}`);
  if (ev.profit_factor != null) evLine.push(`PF=${ev.profit_factor.toFixed(2)}`);
  const am = ADVICE_META[s.advice] ?? ADVICE_META.izle;
  return [
    `${s.ticker}${s.sector ? ` (${s.sector})` : ""} — ${s.setup_label} setup'ı · ${am.label}`,
    `Tavsiye gerekçesi: ${s.advice_reason}`,
    `Güç: ${s.strength != null ? s.strength.toFixed(0) : "—"}/100 · Öncelik: ${s.priority.toFixed(0)}/100 · Kanıt: ${ev.verdict}${evLine.length ? ` (${evLine.join(", ")})` : ""}`,
    `Beklenen NET R/işlem: ${s.expected_r_net > 0 ? "+" : ""}${s.expected_r_net.toFixed(2)}R (${s.expected_r_src})`,
    "",
    `Giriş (ref): ₺${fmt(s.entry_ref, 2)}`,
    `Stop:        ₺${fmt(s.stop, 2)}`,
    `Hedef:       ₺${fmt(s.target, 2)}`,
    `R-katı: ${s.r_multiple != null ? `${s.r_multiple.toFixed(1)}R` : "—"} · Zaman çıkışı: ${s.time_exit_days ?? "—"} gün`,
    s.plan
      ? `Maliyet dahil plan: hedefte ${s.plan.net_target_r >= 0 ? "+" : ""}${s.plan.net_target_r.toFixed(2)}R · stop'ta ${s.plan.net_stop_r.toFixed(2)}R · başabaş isabet %${(s.plan.breakeven_hit * 100).toFixed(0)}${s.plan.feasible ? "" : " · GİRİLEMEZ (maliyet 1R'yi yiyor)"}`
      : null,
    `Bağlam çarpanı: ×${s.context_mult.toFixed(2)} (sektör+rejim, PRIOR)${s.news_pos > 0 ? ` · Haber: +${s.news_pos.toFixed(1)} (×${s.news_mult.toFixed(2)})` : ""}`,
    `Geçerli → ${s.valid_until ?? "—"}${s.triggered_at ? ` (tetik ${s.triggered_at})` : ""}`,
    s.score != null ? `Zemin faktör skoru: ${s.score.toFixed(1)}` : null,
    "",
    "Not: kısa-vade olay-tetikli setup; giriş/stop/hedef referanstır. Sistem yatırım tavsiyesi değildir.",
  ].filter((l): l is string => l !== null).join("\n");
}

function SetupCopyBtn({ s }: { s: SetupSignal }) {
  const [done, setDone] = useState(false);
  return (
    <button
      title="Setup açıklamasını kopyala"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(buildSetupExplanation(s));
          setDone(true);
          setTimeout(() => setDone(false), 1500);
        } catch {
          setDone(false);
        }
      }}
      style={{ ...btn, marginLeft: 6 }}
    >
      {done ? "✓" : <Icon name="copy" />}
    </button>
  );
}

// --- Sektör & Makro bağlam paneli (üst-aşağı katman) -------------------

function regimeMeta(r: string): { label: string; color: string } {
  if (r === "risk_on") return { label: "RISK-ON", color: theme.positive };
  if (r === "risk_off") return { label: "RISK-OFF", color: theme.negative };
  return { label: "NÖTR", color: theme.warning };
}
const sectorTrendColor = (t: string): string =>
  t === "lider" ? theme.positive : t === "geride" ? theme.negative : theme.muted;

function Chip({ k, v, c }: { k: string; v: string; c?: string }) {
  return (
    <span style={{ display: "inline-flex", gap: 6, alignItems: "baseline" }}>
      <span style={{ color: theme.muted }}>{k}</span>
      <span className="mono" style={{ color: c ?? theme.bone }}>{v}</span>
    </span>
  );
}

function SectorTable({ sectors }: { sectors: SectorRow[] }) {
  return (
    <Wrap maxH={300}>
      <table style={tbl}>
        <thead><Trh cols={["#", "Sektör", "Skor", "Trend", "Görece 20g", "Mom 20g", "EMA50 üstü", "n"]} /></thead>
        <tbody>
          {sectors.map((s) => {
            const tc = sectorTrendColor(s.trend);
            const rel = s.rel_strength_20d;
            return (
              <tr key={s.sector} style={{ borderTop: `0.5px solid ${theme.border}` }}>
                <td className="mono" style={{ padding: "7px 12px", fontSize: 12, color: theme.muted }}>{s.rank}</td>
                <td style={{ padding: "7px 12px", fontSize: 13 }}>{s.sector}</td>
                <td style={{ padding: "7px 12px" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <div style={{ width: 48, height: 5, background: theme.border, borderRadius: 3 }}>
                      <div style={{ width: `${Math.max(0, Math.min(100, s.score))}%`, height: "100%", background: scoreColor(s.score), borderRadius: 3 }} />
                    </div>
                    <span className="mono" style={{ fontSize: 12 }}>{s.score.toFixed(0)}</span>
                  </div>
                </td>
                <td style={{ padding: "7px 12px" }}><span style={{ fontSize: 11, color: tc }}>{s.trend}</span></td>
                <td className="mono" style={{ padding: "7px 12px", fontSize: 12, color: rel == null ? theme.muted : rel >= 0 ? theme.positive : theme.negative }}>{rel != null ? `${rel >= 0 ? "+" : ""}${(rel * 100).toFixed(1)}%` : "—"}</td>
                <td className="mono" style={{ padding: "7px 12px", fontSize: 12 }}>{s.mom_20d != null ? `${s.mom_20d >= 0 ? "+" : ""}${(s.mom_20d * 100).toFixed(1)}%` : "—"}</td>
                <td className="mono" style={{ padding: "7px 12px", fontSize: 12, color: theme.muted }}>{s.above_ema50 != null ? `%${(s.above_ema50 * 100).toFixed(0)}` : "—"}</td>
                <td className="mono" style={{ padding: "7px 12px", fontSize: 12, color: theme.muted }}>{s.n}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </Wrap>
  );
}

function MarketContextPanel({ ctx }: { ctx: MarketContext }) {
  const [aiOpen, setAiOpen] = useState(false);
  const [secOpen, setSecOpen] = useState(true);
  const ai = useQuery({ queryKey: ["context-ai"], enabled: aiOpen, staleTime: Infinity, queryFn: fetchContextAI });
  const m = ctx.macro;
  if (!m) return null;
  const rg = regimeMeta(m.regime);
  const sectors = ctx.sectors ?? [];
  const fx = m.usdtry;
  return (
    <div style={{ border: `0.5px solid ${theme.border}`, borderLeft: `2px solid ${rg.color}`, background: theme.surface, borderRadius: 4, padding: 16 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
          <span style={{ fontSize: 13, fontWeight: 600, letterSpacing: 0.5, color: rg.color, border: `0.5px solid ${rg.color}`, borderRadius: 3, padding: "2px 8px" }}>{rg.label}</span>
          <span className="mono" style={{ fontSize: 12, color: theme.muted }}>rejim {m.regime_score.toFixed(0)}/100</span>
          {ctx.as_of && <span style={{ fontSize: 11, color: theme.muted }}>{ctx.as_of}</span>}
        </div>
        <button onClick={() => setAiOpen(true)} disabled={aiOpen} style={btn}>🌐 AI: günün durumu</button>
      </div>

      <div style={{ display: "flex", gap: 18, flexWrap: "wrap", marginTop: 10, fontSize: 12 }}>
        <Chip k="Piyasa 20g" v={`${m.market_ret_20d >= 0 ? "+" : ""}${(m.market_ret_20d * 100).toFixed(1)}%`} c={m.market_ret_20d >= 0 ? theme.positive : theme.negative} />
        <Chip k="Genişlik" v={`%${(m.breadth_ema50 * 100).toFixed(0)} EMA50↑`} />
        <Chip k="Volatilite 20g" v={`%${(m.vol_20d * 100).toFixed(1)}`} />
        <Chip k="Trend" v={`EMA50 ${m.above_ema50 ? "✓" : "✗"} · EMA200 ${m.above_ema200 ? "✓" : "✗"}`} />
        {fx?.ret_20d != null && <Chip k="USDTRY 20g" v={`${fx.ret_20d >= 0 ? "+" : ""}${(fx.ret_20d * 100).toFixed(1)}%`} c={theme.muted} />}
      </div>

      {aiOpen && (
        <div style={{ marginTop: 12, borderTop: `0.5px solid ${theme.border}`, paddingTop: 10 }}>
          {ai.isLoading ? <span style={{ fontSize: 12, color: theme.muted }}>AI düşünüyor…</span>
            : ai.data?.available ? <p style={{ fontSize: 12.5, lineHeight: 1.65, whiteSpace: "pre-wrap", color: theme.bone, margin: 0 }}>{ai.data.comment}</p>
              : <span style={{ fontSize: 12, color: theme.warning }}>{ai.data?.message ?? "AI kullanılamıyor"}</span>}
        </div>
      )}

      {sectors.length > 0 && (
        <div style={{ marginTop: 14 }}>
          <button onClick={() => setSecOpen((o) => !o)} style={{ ...btn, marginBottom: secOpen ? 8 : 0 }}>{secOpen ? "sektörler ▲" : `sektörler ▼ (${sectors.length})`}</button>
          {secOpen && <SectorTable sectors={sectors} />}
        </div>
      )}

      <div style={{ fontSize: 11, color: theme.muted, marginTop: 12, lineHeight: 1.5 }}>
        Üst-aşağı bağlam eş-ağırlık evrenden derlenir (XU100 vekili). Rejim/sektör tilt'i PRIOR'dır — edge değil, bağlam; setup gücünü ±~%30 ayarlar. AI yalnız bu sayıları yorumlar (haber uydurmaz).
      </div>
    </div>
  );
}

function SetupCard({ s, onTrade }: { s: SetupSignal; onTrade: () => void }) {
  const col = setupColor(s.setup);
  const ev = s.evidence;
  const vCol = verdictColor(ev.verdict);
  const strength = s.strength ?? 0;
  const dim = s.advice !== "al-adayı";
  return (
    <div style={{ border: `0.5px solid ${theme.border}`, borderLeft: `2px solid ${col}`, background: theme.surface, borderRadius: 4, padding: 14, opacity: dim ? 0.78 : 1 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
        <span style={{ display: "inline-flex", gap: 8, alignItems: "baseline" }}>
          <Link href={`/ticker/${s.ticker}`} className="mono" style={{ fontSize: 16, color: theme.bone, textDecoration: "none" }}>{s.ticker}</Link>
          <AdviceChip s={s} />
        </span>
        <span style={{ fontSize: 11, color: col, border: `0.5px solid ${col}`, borderRadius: 3, padding: "1px 6px", whiteSpace: "nowrap" }}>{s.setup_label}</span>
      </div>
      <div style={{ fontSize: 11, color: theme.muted, marginTop: 1 }}>{s.sector ?? ""}</div>

      {/* giriş / stop / hedef */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 6, marginTop: 10 }}>
        <PriceCell label="Giriş" v={s.entry_ref} />
        <PriceCell label="Stop" v={s.stop} color={theme.negative} />
        <PriceCell label="Hedef" v={s.target} color={theme.positive} />
      </div>

      {/* beklenti satırı — kartın asıl "anlamı": maliyet dahil ne bekleyebilirim? */}
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", fontSize: 11, color: theme.muted, marginTop: 8 }}>
        <span title={`Beklenen NET R/işlem — kaynak: ${s.expected_r_src}`} style={{ cursor: "help" }}>
          beklenti <span className="mono" style={{ color: rSignColor(s.expected_r_net) }}>{s.expected_r_net > 0 ? "+" : ""}{s.expected_r_net.toFixed(2)}R</span>
        </span>
        {s.plan && (
          <span title={`Maliyet dahil: hedefte ${s.plan.net_target_r >= 0 ? "+" : ""}${s.plan.net_target_r.toFixed(2)}R, stop'ta ${s.plan.net_stop_r.toFixed(2)}R`} style={{ cursor: "help" }}>
            başabaş isabet <span className="mono" style={{ color: theme.bone }}>%{(s.plan.breakeven_hit * 100).toFixed(0)}</span>
            {ev.hit_rate != null && <span> · tarihsel <span className="mono" style={{ color: ev.hit_rate > s.plan.breakeven_hit ? theme.positive : theme.negative }}>%{(ev.hit_rate * 100).toFixed(0)}</span></span>}
          </span>
        )}
        {s.time_exit_days != null && <span title="zaman çıkışı">⏱ <span className="mono" style={{ color: theme.bone }}>{s.time_exit_days}g</span></span>}
        {s.valid_until && <span>geçerli → <span className="mono" style={{ color: theme.bone }}>{s.valid_until}</span></span>}
      </div>

      {/* güç + öncelik barı */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 10 }}>
        <span style={{ fontSize: 10, color: theme.muted }}>öncelik</span>
        <div style={{ flex: 1, height: 6, background: theme.border, borderRadius: 3 }}
          title={`öncelik ${s.priority.toFixed(0)}/100 = beklenti × güç(${strength.toFixed(0)}) × bağlam(×${s.context_mult.toFixed(2)}) × haber(×${s.news_mult.toFixed(2)})`}>
          <div style={{ width: `${Math.max(0, Math.min(100, s.priority))}%`, height: "100%", background: scoreColor(s.priority), borderRadius: 3 }} />
        </div>
        <span className="mono" style={{ fontSize: 14 }}>{s.priority.toFixed(0)}</span>
      </div>

      {/* kanıt + bağlam + zemin skoru */}
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginTop: 8 }}>
        <span title={evidenceTitle(ev)} style={{ fontSize: 11, color: vCol, border: `0.5px solid ${vCol}`, borderRadius: 3, padding: "1px 6px", cursor: "help" }}>
          kanıt: {ev.verdict}{ev.n_events != null ? ` · n=${ev.n_events}` : ""}
        </span>
        <span className="mono" title="sektör+makro bağlam çarpanı (PRIOR — kanıt değil)" style={{ fontSize: 11, color: theme.muted, cursor: "help" }}>
          bağlam ×<span style={{ color: s.context_mult > 1.02 ? theme.positive : s.context_mult < 0.98 ? theme.negative : theme.bone }}>{s.context_mult.toFixed(2)}</span>
        </span>
        {s.news_pos > 0 && <span className="mono" style={{ fontSize: 11, color: theme.positive }} title="taze pozitif KAP katalisti"><Icon name="news" size={11} /> +{s.news_pos.toFixed(1)}</span>}
        {s.score != null && <span className="mono" style={{ fontSize: 11, color: theme.muted }} title="arka plan faktör skoru">zemin: {s.score.toFixed(1)}</span>}
        <span className="mono" style={{ fontSize: 11, color: theme.muted }} title="ham sinyal gücü (dedektör)">güç: {strength.toFixed(0)}</span>
      </div>

      <div style={{ display: "flex", gap: 6, marginTop: 10, alignItems: "center" }}>
        <button onClick={onTrade} style={{ ...btn, borderColor: theme.positive, color: theme.positive }}>Al</button>
        <SetupCopyBtn s={s} />
        <span style={{ marginLeft: "auto" }}><Links t={s.ticker} /></span>
      </div>
    </div>
  );
}

function PriceCell({ label, v, color }: { label: string; v: number | null; color?: string }) {
  return (
    <div style={{ background: theme.bg, border: `0.5px solid ${theme.border}`, borderRadius: 3, padding: "5px 7px" }}>
      <div style={{ fontSize: 10, color: theme.muted }}>{label}</div>
      <div className="mono" style={{ fontSize: 13, color: color ?? theme.bone, marginTop: 1 }}>{v != null ? `₺${fmt(v, 2)}` : "—"}</div>
    </div>
  );
}

// Kanıt tablosu (collapsible) — /api/setups/evidence blob'undan.
// trade-sim PF NET (maliyet sonrası) birincil, brüt parantezde; verdict demeaned excess'e
// dayanır (maliyet kesitsel kanıtı etkilemez) → tablo yalnız friction'ı görünür kılar.
function EvidencePanel({ ev }: { ev: SetupEvidence }) {
  const setups = ev.setups ?? {};
  const names = Object.keys(setups);
  if (!names.length) {
    return <Empty text={ev.note ?? "Henüz event-study koşulmadı."} sub="Kanıt tablosu için event-study gerekiyor (run_event_study.py)." />;
  }
  const meanExc = (s: EvidenceSetup, h: string): number | null | undefined => s.excess?.[h]?.mean_excess;
  // PF: net birincil, brüt parantezde ("1.20 (brüt 1.35)"); net yoksa brüte düş.
  const pfCell = (s: EvidenceSetup): string => {
    const net = s.profit_factor_net ?? s.trade_sim?.profit_factor_net;
    const gross = s.profit_factor ?? s.trade_sim?.profit_factor;
    if (net != null && gross != null) return `${net.toFixed(2)} (brüt ${gross.toFixed(2)})`;
    if (gross != null) return gross.toFixed(2);
    return "—";
  };
  const rtc = ev.round_trip_cost_pct;
  // rejim dilimi hücresi: "↑ +0.21 / ↓ −0.05" (R̄net; hover: n'ler) — ön-kayıtlı, raporlama-only.
  const regimeCell = (s: EvidenceSetup) => {
    const up = s.by_regime?.up, dn = s.by_regime?.down;
    if (!up && !dn) return <span style={{ color: theme.muted }}>—</span>;
    const f = (r?: EvidenceRegimeSlice | null) =>
      r?.mean_R_net == null ? "—" : `${r.mean_R_net >= 0 ? "+" : ""}${r.mean_R_net.toFixed(2)}`;
    return (
      <span title={`Tetik anında piyasa EMA50 üstü (↑ n=${up?.n ?? 0}) / altı (↓ n=${dn?.n ?? 0}) — ön-kayıtlı dilim, verdict'e girmez`} style={{ cursor: "help" }}>
        <span style={{ color: rSignColor(up?.mean_R_net) }}>↑ {f(up)}</span>
        <span style={{ color: theme.muted }}> / </span>
        <span style={{ color: rSignColor(dn?.mean_R_net) }}>↓ {f(dn)}</span>
      </span>
    );
  };
  return (
    <div>
      <Wrap>
        <table style={tbl}>
          <thead><Trh cols={["Setup", "n", "isabet 5g", "fazla 1g", "fazla 3g", "fazla 5g", "fazla 10g", "t (5g)", "PF (net/brüt)", "R̄net rejim ↑/↓", "verdict"]} /></thead>
          <tbody>{names.map((name) => {
            const s = setups[name];
            const vCol = verdictColor(s.verdict ?? "deneysel");
            return (
              <tr key={name} style={{ borderTop: `0.5px solid ${theme.border}` }}>
                <td className="mono" style={{ padding: "8px 12px", fontSize: 12 }}>{name}</td>
                <td className="mono" style={{ padding: "8px 12px", fontSize: 12 }}>{s.n_events ?? "—"}</td>
                <td className="mono" style={{ padding: "8px 12px", fontSize: 12 }}>{s.hit_rate_5d != null ? `${(s.hit_rate_5d * 100).toFixed(0)}%` : "—"}</td>
                {["1", "3", "5", "10"].map((h) => {
                  const m = meanExc(s, h);
                  return <td key={h} className="mono" style={{ padding: "8px 12px", fontSize: 12, color: m == null ? theme.muted : m >= 0 ? theme.positive : theme.negative }}>{m == null ? "—" : `${m >= 0 ? "+" : ""}${(m * 100).toFixed(1)}%`}</td>;
                })}
                <td className="mono" style={{ padding: "8px 12px", fontSize: 12 }}>{s.t_newey_west_5d != null ? s.t_newey_west_5d.toFixed(2) : "—"}</td>
                <td className="mono" style={{ padding: "8px 12px", fontSize: 12 }} title="Kâr faktörü: net (maliyet sonrası) / brüt">{pfCell(s)}</td>
                <td className="mono" style={{ padding: "8px 12px", fontSize: 12 }}>{regimeCell(s)}</td>
                <td style={{ padding: "8px 12px" }}><span style={{ fontSize: 11, color: vCol }}>{s.verdict ?? "—"}</span></td>
              </tr>
            );
          })}</tbody>
        </table>
      </Wrap>
      <div style={{ fontSize: 11, color: theme.muted, marginTop: 6, lineHeight: 1.6 }}>
        Maliyet: komisyon+spread konfigüre edilebilir (config → costs){rtc != null ? ` · round-trip %${(rtc * 100).toFixed(2)}` : ""}. Verdict demeaned fazla-getiriye dayanır (maliyetten etkilenmez); PF net friction'ı gösterir. Rejim dilimi (↑/↓ = tetikte piyasa EMA50 üstü/altı) ön-kayıtlıdır ve yalnız bilgi amaçlıdır — kural değişikliği ancak canlı OOS teyidiyle yapılır.
      </div>
    </div>
  );
}

// Canlı Takip (OOS) tablosu — /api/setups/outcomes'tan. Ateşlenen sinyallerin gerçek
// sonuçları (target/stop/time_exit) birikir → dürüst canlı beklenti. Boş durumu dürüst.
function OutcomesPanel({ oc }: { oc: SetupOutcomes }) {
  const per = oc.per_setup ?? {};
  const names = Object.keys(per).filter((n) => {
    const s = per[n];
    return s.n_closed > 0 || s.n_pending > 0 || s.n_no_entry > 0;
  });
  const anyClosed = (oc.overall?.n_closed ?? 0) > 0;
  const e = oc.expectancy;

  if (!names.length) {
    return <Empty text="Henüz kapanan sinyal yok — takip birikiyor." sub="Ateşlenen her sinyalin sonucu (target/stop/time-exit) girişten sonra otomatik izlenir." />;
  }

  const rColor = (v: number | null | undefined) =>
    v == null ? theme.muted : v >= 0 ? theme.positive : theme.negative;

  // NET birincil, brüt parantezde: "−0.42R (brüt −0.38)". Net yoksa brüte düş.
  const netCell = (net: number | null, gross: number | null, dp: number, suffix = "R") => {
    if (net == null && gross == null) return <span style={{ color: theme.muted }}>—</span>;
    const primary = net ?? gross!;
    return (
      <span style={{ color: rColor(primary) }}>
        {primary >= 0 ? "+" : ""}{primary.toFixed(dp)}{suffix}
        {net != null && gross != null && (
          <span style={{ color: theme.muted }}> (brüt {gross >= 0 ? "+" : ""}{gross.toFixed(dp)})</span>
        )}
      </span>
    );
  };
  const rtc = oc.round_trip_cost_pct;

  return (
    <div>
      <Wrap>
        <table style={tbl}>
          <thead><Trh cols={["Setup", "kapalı", "beklemede", "isabet", "ort R (net/brüt)", "toplam R (net/brüt)", "ort gün"]} /></thead>
          <tbody>
            {names.map((name) => {
              const s = per[name];
              return (
                <tr key={name} style={{ borderTop: `0.5px solid ${theme.border}` }}>
                  <td className="mono" style={{ padding: "8px 12px", fontSize: 12 }} title={s.setup_label ?? name}>{name}</td>
                  <td className="mono" style={{ padding: "8px 12px", fontSize: 12 }}>{s.n_closed}</td>
                  <td className="mono" style={{ padding: "8px 12px", fontSize: 12, color: theme.muted }}>{s.n_pending}</td>
                  <td className="mono" style={{ padding: "8px 12px", fontSize: 12 }}>{s.isabet != null ? `${(s.isabet * 100).toFixed(0)}%` : "—"}</td>
                  <td className="mono" style={{ padding: "8px 12px", fontSize: 12 }}>{netCell(s.ort_r_net, s.ort_r, 2)}</td>
                  <td className="mono" style={{ padding: "8px 12px", fontSize: 12 }}>{netCell(s.toplam_r_net, s.toplam_r, 1)}</td>
                  <td className="mono" style={{ padding: "8px 12px", fontSize: 12, color: theme.muted }}>{s.ort_gun != null ? `${s.ort_gun.toFixed(1)}g` : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Wrap>
      <div style={{ fontSize: 12, color: theme.muted, marginTop: 8, lineHeight: 1.6 }}>
        {anyClosed ? (
          <>
            Beklenti (NET): ~<span className="mono" style={{ color: theme.bone }}>%{e.expected_weekly_pct_net.toFixed(2)}</span>/hafta<span style={{ color: theme.muted }}> (brüt %{e.expected_weekly_pct.toFixed(2)})</span> · Hedef %{e.target_weekly_pct.toFixed(0)}/hafta için gereken: <span className="mono" style={{ color: theme.warning }}>{e.needed_r_per_week.toFixed(0)}R</span>/hafta — mevcut NET: <span className="mono" style={{ color: rColor(e.measured_r_per_week_net) }}>{e.measured_r_per_week_net.toFixed(1)}R</span>/hafta
          </>
        ) : (
          "Henüz kapanan sinyal yok — takip birikiyor."
        )}
      </div>
      <div style={{ fontSize: 11, color: theme.muted, marginTop: 4, lineHeight: 1.6 }}>
        Maliyet: komisyon+spread konfigüre edilebilir (config → costs){rtc != null ? ` · round-trip %${(rtc * 100).toFixed(2)}` : ""}. Net türetilir (saklanmaz) — tarife değişimi geriye dönük temiz hesaplanır. Gereken {e.needed_r_per_week.toFixed(0)}R maliyeti yok sayar → gerçek açık daha büyük.
      </div>
    </div>
  );
}

function OppCard({ r, spark, onTrade }: { r: ScoreRow; spark?: Sparkline; onTrade: () => void }) {
  const factors = (r.reasoning?.factors ?? {}) as Record<string, number>;
  const w = (r.reasoning?.factor_weights ?? {}) as Record<string, number>;
  const top = Object.keys(factors).filter((k) => factors[k] != null && (w[k] ?? 0) > 0).sort((a, b) => (w[b] ?? 0) - (w[a] ?? 0)).slice(0, 3);
  const ch1y = spark?.change_1y ?? null;  // null = ~1 yıllık geçmiş yok (yeni hisse)
  const ch = ch1y ?? 0;
  return (
    <div style={{ border: `0.5px solid ${theme.border}`, background: theme.surface, borderRadius: 4, padding: 14 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <Link href={`/ticker/${r.ticker}`} className="mono" style={{ fontSize: 16, color: theme.bone, textDecoration: "none" }}>{r.ticker}</Link>
        <SignalBadge signal={r.signal} />
      </div>
      <div style={{ fontSize: 11, color: theme.muted, marginTop: 1 }}>{r.sector ?? ""}</div>
      <div style={{ marginTop: 8 }}>
        <Spark pts={spark?.points} color={ch >= 0 ? theme.positive : theme.negative} />
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: theme.muted, marginTop: 2 }}>
          <span>1 yıl</span>
          <span className="mono" style={{ color: ch1y == null ? theme.muted : ch >= 0 ? theme.positive : theme.negative }}>{ch1y == null ? "—" : `${ch >= 0 ? "+" : ""}${(ch * 100).toFixed(0)}%`}</span>
        </div>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 10 }}>
        <div style={{ flex: 1, height: 6, background: theme.border, borderRadius: 3 }}>
          <div style={{ width: `${Math.max(0, Math.min(100, r.score))}%`, height: "100%", background: scoreColor(r.score), borderRadius: 3 }} />
        </div>
        <span className="mono" style={{ fontSize: 14 }}>{r.score.toFixed(1)}</span>
        {(r.news_pos ?? 0) > 0.3 && <span title="KAP pozitif katalist" style={{ fontSize: 11, color: theme.positive }}><Icon name="news" size={11} />+{r.news_pos!.toFixed(1)}</span>}
      </div>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 8 }}>
        {top.map((k) => <span key={k} className="mono" style={{ fontSize: 11, color: theme.muted }}>{k}<span style={{ color: theme.bone }}> {Math.round(factors[k])}</span></span>)}
      </div>
      <div style={{ display: "flex", gap: 6, marginTop: 10, alignItems: "center" }}>
        <button onClick={onTrade} style={btn}>Al</button>
        <CopyBtn r={r} />
        <span style={{ marginLeft: "auto" }}><Links t={r.ticker} /></span>
      </div>
    </div>
  );
}

export default function Home() {
  const qc = useQueryClient();
  const [modal, setModal] = useState<{ ticker: string; entry?: number | null; stop?: number | null } | null>(null);
  const [acctOpen, setAcctOpen] = useState(false);
  const [q, setQ] = useState("");
  const [evOpen, setEvOpen] = useState(false);
  const prefs = useDashboardPrefs();

  const pf = useQuery({ queryKey: ["portfolio"], queryFn: () => apiGet<Portfolio>("/api/portfolio"), refetchInterval: 30_000 });
  const opp = useQuery({ queryKey: ["opportunities"], queryFn: () => apiGet<Opportunities>("/api/opportunities"), refetchInterval: 30_000 });
  const all = useQuery({ queryKey: ["scores"], queryFn: () => apiGet<Scores>("/api/scores"), refetchInterval: 30_000 });
  const setupsQ = useQuery({ queryKey: ["setups"], queryFn: fetchSetups, refetchInterval: 30_000 });
  const evidenceQ = useQuery({ queryKey: ["setups-evidence"], enabled: evOpen, queryFn: fetchSetupEvidence });
  const outcomesQ = useQuery({ queryKey: ["setups-outcomes"], enabled: evOpen, queryFn: fetchSetupOutcomes });
  const ctxQ = useQuery({ queryKey: ["context"], queryFn: fetchContext, refetchInterval: 30_000 });

  const portfolio = pf.data;
  const positions = portfolio?.positions ?? [];
  const opportunities = opp.data?.opportunities ?? [];
  const allScores = all.data?.scores ?? [];
  const setups = setupsQ.data?.setups ?? [];
  const setupsAsOf = setupsQ.data?.as_of ?? null;
  const ctx = ctxQ.data;
  const filtered = useMemo(
    () => (q ? allScores.filter((s) => s.ticker.toLowerCase().includes(q.toLowerCase())) : allScores),
    [allScores, q],
  );
  const asOf = opp.data?.as_of?.slice(0, 16)?.replace("T", " ");

  const oppTickers = opportunities.map((o) => o.ticker);
  const spark = useQuery({
    queryKey: ["spark", oppTickers.join(",")],
    enabled: oppTickers.length > 0,
    queryFn: () => apiPost<SparkResponse>("/api/sparklines", { tickers: oppTickers }),
  });
  const sparks = spark.data?.sparklines ?? {};

  const refresh = () => {
    ["portfolio", "opportunities", "scores", "setups", "setups-evidence", "setups-outcomes", "context", "context-ai", "scheduler"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
  };

  return (
    <main style={{ maxWidth: 1180, margin: "0 auto", padding: "32px 24px" }}>
      <header style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", borderBottom: `0.5px solid ${theme.border}`, paddingBottom: 16 }}>
        <div>
          <h1 style={{ fontSize: 18, fontWeight: 500 }}>Open BIST Terminal</h1>
          <p style={{ fontSize: 13, color: theme.muted, marginTop: 2 }}>
            ~1 hafta swing · {allScores.length} hisse skorlandı {asOf ? `· ${asOf}` : ""}
          </p>
        </div>
        <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
          <AiBudgetBadge />
          <SchedulerBadge />
          <Link href="/settings" style={{ fontSize: 12, color: theme.muted, textDecoration: "none", border: `0.5px solid ${theme.border}`, borderRadius: 3, padding: "4px 10px" }}>Ayarlar</Link>
          <Link href="/config" style={{ fontSize: 12, color: theme.muted, textDecoration: "none", border: `0.5px solid ${theme.border}`, borderRadius: 3, padding: "4px 10px" }}><Icon name="gear" /> Config</Link>
          <HealthBadge />
        </div>
      </header>

      {ctx?.available && ctx.as_of && <StaleBanner lastBar={ctx.as_of} />}

      {/* KPI */}
      <section style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 1, marginTop: 24, background: theme.border, border: `0.5px solid ${theme.border}` }}>
        <Kpi label="Portföy (TRY)" val={portfolio ? `₺${fmt(portfolio.total_try, 0)}` : "—"}
          sub={portfolio ? `K/Z ${portfolio.pnl_total_pct >= 0 ? "+" : ""}${portfolio.pnl_total_pct}%` : ""}
          subColor={portfolio ? pnlColor(portfolio.pnl_total_pct) : theme.muted} />
        <Kpi label="Portföy (USD)" val={portfolio?.total_usd ? `$${fmt(portfolio.total_usd, 0)}` : "—"}
          sub={portfolio ? `USDTRY ${portfolio.usdtry}` : ""} />
        <Kpi label="Reel (CPI-ayarlı)" val={portfolio ? `₺${fmt(portfolio.total_real_try, 0)}` : "—"} sub="başlangıç alım gücü" />
        <Kpi label="Nakit / Heat" val={portfolio ? `%${(portfolio.cash_pct * 100).toFixed(0)}` : "—"}
          sub={portfolio ? `heat %${(portfolio.open_heat_pct * 100).toFixed(1)} / %6` : ""}
          subColor={portfolio && portfolio.open_heat_pct > 0.06 ? theme.negative : theme.muted} />
      </section>
      <div style={{ marginTop: 8, display: "flex", gap: 12 }}>
        <button onClick={() => setAcctOpen(true)} style={btn}><Icon name="gear" /> Bütçe / hesap ayarla</button>
        <button onClick={refresh} style={btn}>↻ Tazele</button>
      </div>

      {/* Bugün Ne Yapmalı — tüm katmanların tek karar listesi (öncelik-sıralı) */}
      <section style={{ marginTop: 28 }}>
        <TodayPanel resp={setupsQ.data}
          onTrade={(s) => setModal({ ticker: s.ticker, entry: s.entry_ref, stop: s.stop })} />
      </section>

      {ctx?.available && ctx.macro && (
        <CollapsibleSection id="market" title="Piyasa & Sektör Durumu" prefs={prefs}>
          <MarketContextPanel ctx={ctx} />
        </CollapsibleSection>
      )}

      <CollapsibleSection id="strategies" title="Strateji Karnesi" prefs={prefs}>
        <StrategyPanel />
      </CollapsibleSection>

      {positions.length > 0 && (
        <Section title="Pozisyonlar">
          <Wrap>
            <table style={tbl}>
              <thead><Trh cols={["Ticker", "Adet", "Maliyet→Son", "Stop", "K/Z", "Skor", "Linkler", ""]} /></thead>
              <tbody>{positions.map((p) => <PosRow key={p.ticker} p={p} onTrade={() => setModal({ ticker: p.ticker })} />)}</tbody>
            </table>
          </Wrap>
        </Section>
      )}

      {/* olay-tetikli kısa-vade setup sinyalleri (faktör fırsatlarının ÜSTÜNDE) */}
      {!prefs.isHidden("setups") && (
      <section style={{ marginTop: 28 }}>
        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: prefs.isCollapsed("setups") ? 0 : 12, gap: 12, flexWrap: "wrap" }}>
          <div onClick={() => prefs.toggleCollapse("setups")} title={prefs.isCollapsed("setups") ? "genişlet" : "küçült"}
            style={{ display: "flex", alignItems: "baseline", gap: 8, cursor: "pointer", userSelect: "none" }}>
            <span aria-hidden style={{ color: theme.muted, fontSize: 11, width: 10 }}>{prefs.isCollapsed("setups") ? "▸" : "▾"}</span>
            <h2 style={{ fontSize: 14, fontWeight: 500 }}>
              Setup Fırsatları{setupsQ.data ? ` · ${setupsQ.data.count} sinyal` : ""}
              {setupsAsOf && <span style={{ fontSize: 12, color: theme.muted, fontWeight: 400 }}> · {setupsAsOf}</span>}
              {prefs.isCollapsed("setups") && <span style={{ fontSize: 11, color: theme.muted, fontWeight: 400 }}> · küçültüldü</span>}
            </h2>
          </div>
          {!prefs.isCollapsed("setups") && <button onClick={() => setEvOpen((o) => !o)} style={btn}>{evOpen ? "kanıtlar ▲" : "kanıtlar ▼"}</button>}
        </div>

        {!prefs.isCollapsed("setups") && <>
        {evOpen && (
          <div style={{ marginBottom: 14 }}>
            {evidenceQ.isLoading ? <Empty text="Kanıt yükleniyor…" />
              : evidenceQ.data ? <EvidencePanel ev={evidenceQ.data} />
                : <Empty text="Kanıt alınamadı." />}

            {/* Canlı Takip (OOS) — event-study prior'ının ALTINDA; gerçek out-of-sample sonuçlar */}
            <div style={{ marginTop: 16 }}>
              <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 8, color: theme.bone }}>Canlı Takip (OOS)</div>
              {outcomesQ.isLoading ? <Empty text="Sonuçlar yükleniyor…" />
                : outcomesQ.data ? <OutcomesPanel oc={outcomesQ.data} />
                  : <Empty text="Sonuç alınamadı." />}
            </div>
          </div>
        )}

        {setupsQ.isLoading ? <Empty text="Yükleniyor…" />
          : setups.length === 0
            ? <Empty
                text="Şu an tetiklenmiş setup yok — sistem sinyal üretmeye zorlanmaz."
                sub={setupsAsOf == null ? "Tarama henüz koşulmamış olabilir — otonom mod her akşam 19:15'te tarar (ya da refresh.bat)." : "Olay-tetikli kriterler bugün hiçbir isimde sağlanmadı."} />
            : <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))", gap: 14 }}>
                {setups.map((s) => <SetupCard key={`${s.ticker}-${s.setup}`} s={s}
                  onTrade={() => setModal({ ticker: s.ticker, entry: s.entry_ref, stop: s.stop })} />)}
              </div>}
        </>}
      </section>
      )}

      <CollapsibleSection id="opportunities" prefs={prefs}
        title={`Fırsatlar (zemin faktör skoru)${opp.data ? ` · ${opp.data.count} isim (sektör-cap ${opp.data.sector_cap ?? 3}/sektör, ${opp.data.total_before_cap ?? opp.data.count} aday)` : ""}`}>
        {opp.isLoading ? <Empty text="Yükleniyor…" />
          : opportunities.length === 0 ? <Empty text="Bugün setup yok — nakitte bekle" sub="Mutlak eşiği geçen aday yok." />
            : <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))", gap: 14 }}>
                {opportunities.map((r) => <OppCard key={r.ticker} r={r} spark={sparks[r.ticker]} onTrade={() => setModal({ ticker: r.ticker })} />)}
              </div>}
      </CollapsibleSection>

      {/* TÜM BIST */}
      <Section title={`Tüm BIST · ${filtered.length} hisse`}>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ticker ara… (örn. THYAO)"
          style={{ width: "100%", maxWidth: 280, marginBottom: 10, background: theme.surface, color: theme.bone,
            border: `0.5px solid ${theme.border}`, borderRadius: 3, padding: "6px 10px", fontSize: 13 }} />
        <Wrap maxH={520}>
          <ScoreTable rows={filtered} onTrade={(t) => setModal({ ticker: t })} compact />
        </Wrap>
      </Section>

      <footer style={{ marginTop: 32, fontSize: 12, color: theme.muted, lineHeight: 1.6 }}>
        Skorlar gerçek BIST verisinden; ağırlıklar kalibrasyonla öğrenildi (low-vol + PEAD + value + kalite). <Icon name="cpu" /> Otonom mod: backend açıkken veri/skor/haber/kalibrasyon kendiliğinden döner (Pzt-Cum 19:15 + KAP 30dk + Cmt kalibrasyon). Sinyal sıralaması ÖLÇÜLEN net beklentiye dayanır (event-study + canlı sonuç); "izle" = edge kanıtlanmadı, "girme" = maliyet planı öldürüyor. Edge mütevazı; sistem yatırım tavsiyesi değildir.
      </footer>

      {modal && <TradeModal ticker={modal.ticker} position={positions.find((p) => p.ticker === modal.ticker)} suggestedEntry={modal.entry} suggestedStop={modal.stop} onClose={() => setModal(null)} onDone={() => { setModal(null); refresh(); }} />}
      {acctOpen && <AccountModal onClose={() => setAcctOpen(false)} onDone={() => { setAcctOpen(false); refresh(); }} />}
    </main>
  );
}

const btn: React.CSSProperties = { fontSize: 12, background: "transparent", color: theme.bone, border: `0.5px solid ${theme.border}`, borderRadius: 3, padding: "5px 10px", cursor: "pointer" };
const tbl: React.CSSProperties = { width: "100%", borderCollapse: "collapse" };

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return <section style={{ marginTop: 28 }}><h2 style={{ fontSize: 14, fontWeight: 500, marginBottom: 12 }}>{title}</h2>{children}</section>;
}
// Küçültülebilir + gizlenebilir bölüm (görünüm tercihi localStorage'da; Ayarlar > Dashboard Bölümleri).
function CollapsibleSection({ id, title, prefs, children }: {
  id: SectionId; title: React.ReactNode; prefs: DashboardPrefs; children: React.ReactNode;
}) {
  if (prefs.isHidden(id)) return null;
  const collapsed = prefs.isCollapsed(id);
  return (
    <section style={{ marginTop: 28 }}>
      <div onClick={() => prefs.toggleCollapse(id)} title={collapsed ? "genişlet" : "küçült"}
        style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: collapsed ? 0 : 12, cursor: "pointer", userSelect: "none" }}>
        <span aria-hidden style={{ color: theme.muted, fontSize: 11, width: 10 }}>{collapsed ? "▸" : "▾"}</span>
        <h2 style={{ fontSize: 14, fontWeight: 500 }}>{title}</h2>
        {collapsed && <span style={{ fontSize: 11, color: theme.muted }}>· küçültüldü</span>}
      </div>
      {!collapsed && children}
    </section>
  );
}
function Wrap({ children, maxH }: { children: React.ReactNode; maxH?: number }) {
  return <div style={{ border: `0.5px solid ${theme.border}`, background: theme.surface, borderRadius: 2, overflow: "auto", maxHeight: maxH }}>{children}</div>;
}
function Trh({ cols }: { cols: string[] }) {
  return <tr style={{ fontSize: 11, color: theme.muted, textAlign: "left" }}>{cols.map((c) => <th key={c} style={{ padding: "8px 12px", fontWeight: 400, position: "sticky", top: 0, background: theme.surface }}>{c}</th>)}</tr>;
}
function Kpi({ label, val, sub, subColor }: { label: string; val: string; sub?: string; subColor?: string }) {
  return <div style={{ background: theme.surface, padding: "16px 18px" }}>
    <div style={{ fontSize: 12, color: theme.muted }}>{label}</div>
    <div className="mono" style={{ fontSize: 20, marginTop: 6 }}>{val}</div>
    {sub && <div className="mono" style={{ fontSize: 11, marginTop: 4, color: subColor ?? theme.muted }}>{sub}</div>}
  </div>;
}

function SignalBadge({ signal }: { signal: ScoreRow["signal"] }) {
  const buy = signal === "buy" || signal === "strong_buy", sell = signal === "sell" || signal === "reduce";
  const color = buy ? theme.positive : sell ? theme.negative : theme.muted;
  return <span style={{ fontSize: 11, color, border: `0.5px solid ${color}`, borderRadius: 3, padding: "1px 6px", whiteSpace: "nowrap" }}>{SIGNAL_TR[signal] ?? signal}</span>;
}

function PosRow({ p, onTrade }: { p: Position; onTrade: () => void }) {
  return <tr style={{ borderTop: `0.5px solid ${theme.border}` }}>
    <td className="mono" style={{ padding: "10px 12px", fontSize: 14 }}>{p.ticker}</td>
    <td className="mono" style={{ padding: "10px 12px" }}>{p.qty}</td>
    <td className="mono" style={{ padding: "10px 12px", fontSize: 13 }}>{p.avg_cost?.toFixed(2)} → {p.last?.toFixed(2)}</td>
    <td className="mono" style={{ padding: "10px 12px", fontSize: 13, color: theme.muted }}>{p.stop ? p.stop.toFixed(2) : "—"}</td>
    <td className="mono" style={{ padding: "10px 12px", color: pnlColor(p.pnl_pct) }}>{p.pnl_pct >= 0 ? "+" : ""}{p.pnl_pct}%</td>
    <td style={{ padding: "10px 12px" }}>
      {p.score != null ? <span className="mono" style={{ fontSize: 13, color: scoreColor(p.score) }}>{p.score.toFixed(1)}{p.signal ? ` ${SIGNAL_TR[p.signal] ?? ""}` : ""}</span> : <span style={{ color: theme.muted, fontSize: 12 }}>—</span>}
    </td>
    <td style={{ padding: "10px 12px" }}><Links t={p.ticker} /></td>
    <td style={{ padding: "10px 12px" }}><button onClick={onTrade} style={btn}>İşlem</button></td>
  </tr>;
}

function ScoreTable({ rows, onTrade, compact }: { rows: ScoreRow[]; onTrade: (t: string) => void; compact?: boolean }) {
  return (
    <table style={tbl}>
      <thead><Trh cols={["Ticker", "Skor", "Sinyal", "Faktörler", "Linkler", ""]} /></thead>
      <tbody>{rows.map((r) => (
        <tr key={r.ticker} style={{ borderTop: `0.5px solid ${theme.border}`, opacity: r.meets_absolute_threshold ? 1 : 0.7 }}>
          <td style={{ padding: "9px 12px" }}>
            <Link href={`/ticker/${r.ticker}`} className="mono" style={{ fontSize: 14, color: theme.bone, textDecoration: "none", borderBottom: `1px dotted ${theme.muted}` }}>{r.ticker}</Link>
            {r.sector && <div style={{ fontSize: 10, color: theme.muted }}>{r.sector}</div>}
          </td>
          <td style={{ padding: "9px 12px" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <div style={{ width: 56, height: 5, background: theme.border, borderRadius: 3 }}>
                <div style={{ width: `${Math.max(0, Math.min(100, r.score))}%`, height: "100%", background: scoreColor(r.score), borderRadius: 3 }} />
              </div>
              <span className="mono" style={{ fontSize: 13 }}>{r.score.toFixed(1)}</span>
            </div>
          </td>
          <td style={{ padding: "9px 12px" }}>
            <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
              <SignalBadge signal={r.signal} />
              {(r.news_pos ?? 0) > 0.3 && <span title="KAP pozitif katalist" style={{ fontSize: 11, color: theme.positive }}><Icon name="news" size={11} />+{r.news_pos!.toFixed(1)}</span>}
              {(r.news_neg ?? 0) < -0.3 && <span title="KAP negatif" style={{ fontSize: 11, color: theme.negative }}><Icon name="news" size={11} />{r.news_neg!.toFixed(1)}</span>}
            </div>
          </td>
          <td style={{ padding: "9px 12px" }}>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              {r.reasoning?.factors && Object.entries(r.reasoning.factors as Record<string, number>)
                .filter(([, v]) => v != null).sort((a, b) => b[1] - a[1]).slice(0, compact ? 2 : 3)
                .map(([k, v]) => <span key={k} className="mono" style={{ fontSize: 11, color: theme.muted }}>{k}<span style={{ color: theme.bone }}> {Math.round(v)}</span></span>)}
            </div>
          </td>
          <td style={{ padding: "9px 12px" }}><Links t={r.ticker} /></td>
          <td style={{ padding: "9px 12px", whiteSpace: "nowrap" }}>
            <button onClick={() => onTrade(r.ticker)} style={btn}>Al</button>
            <CopyBtn r={r} />
          </td>
        </tr>
      ))}</tbody>
    </table>
  );
}

function Empty({ text, sub }: { text: string; sub?: string }) {
  return <div style={{ border: `0.5px solid ${theme.border}`, background: theme.surface, borderRadius: 2, padding: "36px 24px", textAlign: "center" }}>
    <div style={{ fontSize: 15 }}>{text}</div>{sub && <div style={{ fontSize: 13, color: theme.muted, marginTop: 8 }}>{sub}</div>}
  </div>;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label style={{ display: "block", marginTop: 12 }}>
    <span style={{ fontSize: 12, color: theme.muted }}>{label}</span>
    <div style={{ marginTop: 4 }}>{children}</div>
  </label>;
}
const inp: React.CSSProperties = { width: "100%", background: theme.bg, color: theme.bone, border: `0.5px solid ${theme.border}`, borderRadius: 3, padding: "7px 10px", fontSize: 14 };

function TradeModal({ ticker, position, suggestedEntry, suggestedStop, onClose, onDone }: { ticker: string; position?: Position; suggestedEntry?: number | null; suggestedStop?: number | null; onClose: () => void; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [qty, setQty] = useState<number | "">("");
  // Setup kartından gelindiyse fiyat kutusunu önerilen giriş referansıyla ön-doldur.
  const [priceInput, setPriceInput] = useState<number | "">(suggestedEntry ?? "");
  const sz = useQuery({ queryKey: ["size", ticker], queryFn: () => apiGet<import("@/lib/api").SizePreview>(`/api/size/${ticker}`) });
  const s = sz.data;
  const curPrice = s?.price ?? s?.daily_close ?? 0;
  const effPrice = priceInput === "" ? curPrice : Number(priceInput);
  const hasStop = s?.entry != null && s?.stop != null;
  const perShareRisk = hasStop ? (s!.entry! - s!.stop!) : 0;
  const effQty = qty === "" ? (s?.valid ? s.qty : 0) : Number(qty);
  const amount = effQty * effPrice;

  async function submit(side: "buy" | "sell") {
    setBusy(true); setErr(null);
    try {
      const useQty = side === "sell" ? (position?.qty ?? 0) : effQty;
      const usePrice = side === "sell" ? (priceInput === "" ? (position?.last ?? curPrice) : Number(priceInput)) : effPrice;
      if (useQty <= 0) throw new Error("adet 0 olamaz");
      if (usePrice <= 0) throw new Error("fiyat gir");
      await apiPost("/api/trades", { ticker, side, qty: useQty, price: usePrice, stop: hasStop ? s!.stop : null, source: "dashboard" });
      onDone();
    } catch (e) { setErr(String(e)); } finally { setBusy(false); }
  }

  return (
    <Overlay onClose={onClose}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <h3 className="mono" style={{ fontSize: 16 }}>{ticker} · işlem</h3>
        <Links t={ticker} />
      </div>
      {(suggestedEntry != null || suggestedStop != null) && (
        <div style={{ marginTop: 12, fontSize: 12, color: theme.muted, border: `0.5px solid ${theme.border}`, borderRadius: 3, padding: "8px 10px", display: "flex", gap: 14, flexWrap: "wrap" }}>
          <span><Icon name="target" /> Setup önerisi:</span>
          {suggestedEntry != null && <span>giriş <span className="mono" style={{ color: theme.bone }}>₺{suggestedEntry.toFixed(2)}</span></span>}
          {suggestedStop != null && <span>stop <span className="mono" style={{ color: theme.negative }}>₺{suggestedStop.toFixed(2)}</span></span>}
        </div>
      )}
      {sz.isLoading ? <p style={{ color: theme.muted, fontSize: 13, marginTop: 16 }}>fiyat/boyut alınıyor…</p>
        : !s ? <p style={{ color: theme.warning, fontSize: 13, marginTop: 16 }}>Bu hisse için veri yok — adet ve fiyatı elle gir.</p>
          : <>
            <div style={{ marginTop: 14, fontSize: 13, lineHeight: 1.9 }}>
              <Row k="Güncel fiyat" v={curPrice ? `₺${curPrice.toFixed(2)}` : "—"} />
              {s.atr14 != null && <Row k="ATR(14)" v={`${s.atr14.toFixed(2)}${s.atr_pct != null ? ` (%${(s.atr_pct * 100).toFixed(1)})` : ""}`} />}
              {s.valid && hasStop
                ? <Row k="Önerilen (ATR)" v={`${s.qty} lot · stop ₺${s.stop!.toFixed(2)}`} />
                : <Row k="ATR önerisi" v={`yok${s.reason ? ` (${s.reason})` : ""} — elle gir`} color={theme.warning} />}
            </div>
            <Field label="Adet">
              <input type="number" min={0} value={qty} placeholder={s.valid ? String(s.qty) : "adet"} onChange={(e) => setQty(e.target.value === "" ? "" : Math.max(0, Math.floor(Number(e.target.value))))} style={inp} />
            </Field>
            <Field label="Fiyat (₺) — kendi maliyetini girebilirsin (boş=güncel)">
              <input type="number" min={0} step="0.01" value={priceInput} placeholder={curPrice ? curPrice.toFixed(2) : "fiyat"} onChange={(e) => setPriceInput(e.target.value === "" ? "" : Math.max(0, Number(e.target.value)))} style={inp} />
            </Field>
            <Field label="veya Tutar (₺) — adede çevrilir">
              <input type="number" min={0} value={amount ? Math.round(amount) : ""} onChange={(e) => setQty(effPrice ? Math.floor(Number(e.target.value) / effPrice) : 0)} style={inp} />
            </Field>
            <div style={{ marginTop: 14, fontSize: 13, lineHeight: 1.9, borderTop: `0.5px solid ${theme.border}`, paddingTop: 10 }}>
              <Row k="İşlem" v={`${effQty} lot × ₺${effPrice.toFixed(2)} = ₺${fmt(amount, 0)}`} bold />
              {hasStop && <Row k="Risk (stop'a)" v={`₺${fmt(effQty * perShareRisk, 0)}`} color={theme.muted} />}
              {s.capped_by && qty === "" && <Row k="ATR önerisi sınırı" v={s.capped_by === "max_name" ? "tek-isim %30" : "heat %6"} color={theme.warning} />}
            </div>
          </>}
      {err && <p style={{ color: theme.negative, fontSize: 12, marginTop: 10 }}>{err}</p>}
      <div style={{ display: "flex", gap: 8, marginTop: 18 }}>
        <button disabled={busy || effQty <= 0} onClick={() => submit("buy")} style={{ ...btn, flex: 1, borderColor: theme.positive, color: theme.positive, padding: "8px" }}>AL {effQty || ""}</button>
        {position && <button disabled={busy} onClick={() => submit("sell")} style={{ ...btn, flex: 1, borderColor: theme.negative, color: theme.negative, padding: "8px" }}>SAT {position.qty}</button>}
      </div>
      <p style={{ fontSize: 11, color: theme.muted, marginTop: 12 }}>Sistem emir göndermez — Midas'ta yaptığın işlemi (ya da mevcut pozisyonunu) ledger'a kaydeder. Mevcut holdingi eklemek için adet + kendi maliyet fiyatını gir, AL.</p>
      <KapEvents ticker={ticker} />
      <AiComment ticker={ticker} />
    </Overlay>
  );
}

function KapEvents({ ticker }: { ticker: string }) {
  const kap = useQuery({
    queryKey: ["kap", ticker],
    queryFn: () => apiGet<import("@/lib/api").KapResponse>(`/api/kap/${ticker}`),
  });
  const events = kap.data?.events ?? [];
  if (!events.length) return null;
  const dirColor = (d: number | null) => ((d ?? 0) > 0.1 ? theme.positive : (d ?? 0) < -0.1 ? theme.negative : theme.muted);
  return (
    <div style={{ marginTop: 14, borderTop: `0.5px solid ${theme.border}`, paddingTop: 12 }}>
      <div style={{ fontSize: 12, color: theme.muted, marginBottom: 6 }}><Icon name="news" /> KAP açıklamaları</div>
      {events.slice(0, 5).map((e, i) => (
        <div key={i} style={{ fontSize: 12, marginBottom: 6, opacity: e.active ? 1 : 0.55 }}>
          <span style={{ color: dirColor(e.direction) }}>●</span>{" "}
          {e.url ? <a href={e.url} target="_blank" rel="noopener noreferrer" style={{ color: theme.bone, textDecoration: "none" }}>{e.title || e.type}</a> : (e.title || e.type)}
          {e.mechanism && <div style={{ color: theme.muted, fontSize: 11, marginLeft: 14 }}>{e.mechanism}</div>}
        </div>
      ))}
    </div>
  );
}

function AiComment({ ticker }: { ticker: string }) {
  const [open, setOpen] = useState(false);
  const ai = useQuery({
    queryKey: ["ai", ticker], enabled: open,
    queryFn: () => apiGet<import("@/lib/api").AIComment>(`/api/ai/ticker/${ticker}`),
  });
  return (
    <div style={{ marginTop: 14, borderTop: `0.5px solid ${theme.border}`, paddingTop: 12 }}>
      {!open ? (
        <button onClick={() => setOpen(true)} style={btn}><Icon name="ai" /> AI yorum</button>
      ) : ai.isLoading ? (
        <p style={{ fontSize: 13, color: theme.muted }}>AI düşünüyor…</p>
      ) : ai.data?.available ? (
        <p style={{ fontSize: 13, lineHeight: 1.6, whiteSpace: "pre-wrap", color: theme.bone }}>{ai.data.comment}</p>
      ) : (
        <p style={{ fontSize: 12, lineHeight: 1.6, color: theme.warning }}>{ai.data?.message ?? "AI kullanılamıyor"}</p>
      )}
    </div>
  );
}

function AccountModal({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const cfg = useQuery({ queryKey: ["cfg-account"], queryFn: () => apiGet<{ value: AccountConfig }>("/api/config/account") });
  const [cash, setCash] = useState<string>("");
  const [cpi, setCpi] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const v = cfg.data?.value;

  async function save() {
    if (!v) return;  // cfg yüklenmeden/hata varken mevcut start_date'i sabit tarihle EZME
    setBusy(true);
    try {
      const value: AccountConfig = {
        starting_cash_try: cash === "" ? v.starting_cash_try : Number(cash),
        start_date: v.start_date,
        annual_cpi: cpi === "" ? v.annual_cpi : Number(cpi) / 100,
      };
      await apiPut("/api/config/account", { value });
      onDone();
    } finally { setBusy(false); }
  }

  return (
    <Overlay onClose={onClose}>
      <h3 style={{ fontSize: 16 }}>Bütçe / hesap ayarı</h3>
      {cfg.isLoading ? <p style={{ color: theme.muted, marginTop: 12 }}>yükleniyor…</p> : <>
        <Field label={`Başlangıç bütçesi (₺) — şu an ₺${fmt(v?.starting_cash_try ?? 0, 0)}`}>
          <input type="number" value={cash} placeholder={String(v?.starting_cash_try ?? 10000)} onChange={(e) => setCash(e.target.value)} style={inp} />
        </Field>
        <Field label={`Yıllık enflasyon % (reel P&L için) — şu an %${((v?.annual_cpi ?? 0.35) * 100).toFixed(0)}`}>
          <input type="number" value={cpi} placeholder={String(((v?.annual_cpi ?? 0.35) * 100).toFixed(0))} onChange={(e) => setCpi(e.target.value)} style={inp} />
        </Field>
        <p style={{ fontSize: 11, color: theme.muted, marginTop: 10 }}>Bütçe = portföyün nakit tabanı; boyutlandırma ve heat buna göre hesaplanır.</p>
        <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
          <button disabled={busy || !v} onClick={save} style={{ ...btn, flex: 1, borderColor: theme.positive, color: theme.positive, padding: "8px" }}>Kaydet</button>
          <button onClick={onClose} style={{ ...btn, padding: "8px 16px" }}>İptal</button>
        </div>
        <div style={{ marginTop: 18, borderTop: `0.5px solid ${theme.border}`, paddingTop: 12 }}>
          <p style={{ fontSize: 11, color: theme.muted, marginBottom: 8 }}>Tüm işlemleri/pozisyonları siler — kendi portföyünü sıfırdan girmek için.</p>
          <button disabled={busy} onClick={async () => {
            if (!confirm("Tüm işlemler ve pozisyonlar silinecek. Emin misin?")) return;
            setBusy(true);
            try { await apiPost("/api/portfolio/reset", {}); onDone(); } finally { setBusy(false); }
          }} style={{ ...btn, borderColor: theme.negative, color: theme.negative, padding: "8px 14px" }}>⚠ Portföyü sıfırla</button>
        </div>
      </>}
    </Overlay>
  );
}

function Overlay({ children, onClose }: { children: React.ReactNode; onClose: () => void }) {
  return <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 50 }}>
    <div onClick={(e) => e.stopPropagation()} style={{ background: theme.surface, border: `0.5px solid ${theme.border}`, borderRadius: 4, padding: 24, width: 400, maxHeight: "90vh", overflow: "auto" }}>{children}</div>
  </div>;
}

function Row({ k, v, bold, color }: { k: string; v: string; bold?: boolean; color?: string }) {
  return <div style={{ display: "flex", justifyContent: "space-between" }}>
    <span style={{ color: theme.muted }}>{k}</span>
    <span className="mono" style={{ color: color ?? theme.bone, fontWeight: bold ? 500 : 400 }}>{v}</span>
  </div>;
}
