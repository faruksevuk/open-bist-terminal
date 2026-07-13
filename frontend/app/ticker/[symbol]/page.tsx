"use client";

import { useQuery } from "@tanstack/react-query";
import { useParams } from "next/navigation";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { createChart, ColorType, type IChartApi } from "lightweight-charts";
import { apiGet, fetchSetups, fmt, midasUrl, tvUrl, type ChartData, type SetupSignal, type TickerDetail, SIGNAL_TR } from "@/lib/api";
import { scoreColor, theme } from "@/lib/theme";
import { Icon } from "@/lib/icons";

// Setup tipi → renk (dashboard ile aynı palet).
const SETUP_COLOR: Record<string, string> = {
  snapback: "#A23B43", squeeze_breakout: "#C08A3E", trend_pullback: "#5E8C6A",
  pead_drift: "#7C6FB0", quiet_accumulation: "#5C86A8",
};
const setupColor = (s: string): string => SETUP_COLOR[s] ?? theme.muted;

function SetupBanner({ s }: { s: SetupSignal }) {
  const col = setupColor(s.setup);
  const cell = (label: string, v: number | null, c?: string) => (
    <span style={{ display: "inline-flex", flexDirection: "column" }}>
      <span style={{ fontSize: 10, color: theme.muted }}>{label}</span>
      <span className="mono" style={{ fontSize: 13, color: c ?? theme.bone }}>{v != null ? `₺${fmt(v, 2)}` : "—"}</span>
    </span>
  );
  return (
    <div style={{ marginTop: 16, border: `0.5px solid ${theme.border}`, borderLeft: `2px solid ${col}`, background: theme.surface, borderRadius: 3, padding: "10px 14px", display: "flex", gap: 18, alignItems: "center", flexWrap: "wrap" }}>
      <span style={{ fontSize: 12, color: col, border: `0.5px solid ${col}`, borderRadius: 3, padding: "2px 8px", whiteSpace: "nowrap" }}><Icon name="target" size={12} /> {s.setup_label}</span>
      {cell("Giriş", s.entry_ref)}
      {cell("Stop", s.stop, theme.negative)}
      {cell("Hedef", s.target, theme.positive)}
      <span style={{ fontSize: 12, color: theme.muted }}>R-katı <span className="mono" style={{ color: theme.bone }}>{s.r_multiple != null ? `${s.r_multiple.toFixed(1)}R` : "—"}</span></span>
      {s.time_exit_days != null && <span style={{ fontSize: 12, color: theme.muted }}>⏱ <span className="mono" style={{ color: theme.bone }}>{s.time_exit_days}g</span></span>}
      {s.valid_until && <span style={{ fontSize: 12, color: theme.muted }}>geçerli → <span className="mono" style={{ color: theme.bone }}>{s.valid_until}</span></span>}
      <span style={{ fontSize: 11, color: theme.muted, marginLeft: "auto" }} title="kısa-vade olay-tetikli setup kanıtı">kanıt: {s.evidence.verdict}</span>
    </div>
  );
}

const FACTOR_TR: Record<string, string> = {
  low_vol: "Düşük volatilite", pead: "PEAD (kazanç sürprizi)", value: "Value (ucuzluk)",
  quality: "Kalite (F)", momentum: "Momentum", stab: "Stabilizasyon", reversal: "Oversold", cause: "Sebep",
};
const pct = (v: number | null | undefined, d = 1) => (v == null ? "—" : `${(v * 100).toFixed(d)}%`);
const num = (v: number | null | undefined, d = 2) => (v == null ? "—" : v.toFixed(d));

function PriceChart({ data }: { data: ChartData }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart: IChartApi = createChart(ref.current, {
      width: ref.current.clientWidth, height: 360,
      layout: { background: { type: ColorType.Solid, color: theme.surface }, textColor: theme.muted, fontFamily: "var(--font-mono)" },
      grid: { vertLines: { color: theme.border }, horzLines: { color: theme.border } },
      rightPriceScale: { borderColor: theme.border },
      timeScale: { borderColor: theme.border, timeVisible: false },
    });
    const candle = chart.addCandlestickSeries({ upColor: theme.positive, downColor: theme.negative, borderVisible: false, wickUpColor: theme.positive, wickDownColor: theme.negative });
    candle.setData(data.candles as never);
    const mkLine = (color: string, w: 1 | 2 = 1) => chart.addLineSeries({ color, lineWidth: w, priceLineVisible: false, lastValueVisible: false });
    mkLine("#9A958B").setData(data.ema20 as never);
    mkLine("#C08A3E").setData(data.ema50 as never);
    mkLine("#6B1F2A", 2).setData(data.ema200 as never);
    const vol = chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "vol" });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });
    vol.setData(data.volume as never);
    chart.timeScale().fitContent();
    const onResize = () => ref.current && chart.applyOptions({ width: ref.current.clientWidth });
    window.addEventListener("resize", onResize);
    return () => { window.removeEventListener("resize", onResize); chart.remove(); };
  }, [data]);
  return (
    <div>
      <div ref={ref} />
      <div style={{ display: "flex", gap: 14, fontSize: 11, color: theme.muted, marginTop: 4 }}>
        <span style={{ color: "#9A958B" }}>— EMA20</span>
        <span style={{ color: "#C08A3E" }}>— EMA50</span>
        <span style={{ color: "#6B1F2A" }}>— EMA200</span>
      </div>
    </div>
  );
}

function RsiChart({ data }: { data: ChartData }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart = createChart(ref.current, {
      width: ref.current.clientWidth, height: 110,
      layout: { background: { type: ColorType.Solid, color: theme.surface }, textColor: theme.muted, fontFamily: "var(--font-mono)" },
      grid: { vertLines: { color: theme.border }, horzLines: { color: theme.border } },
      rightPriceScale: { borderColor: theme.border },
      timeScale: { borderColor: theme.border, visible: false },
    });
    const rsi = chart.addLineSeries({ color: theme.bone, lineWidth: 1, priceLineVisible: false });
    rsi.setData(data.rsi as never);
    [70, 30].forEach((lvl) => rsi.createPriceLine({ price: lvl, color: theme.border, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: String(lvl) }));
    chart.timeScale().fitContent();
    const onResize = () => ref.current && chart.applyOptions({ width: ref.current.clientWidth });
    window.addEventListener("resize", onResize);
    return () => { window.removeEventListener("resize", onResize); chart.remove(); };
  }, [data]);
  return <div><div style={{ fontSize: 11, color: theme.muted, margin: "8px 0 2px" }}>RSI(14)</div><div ref={ref} /></div>;
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ border: `0.5px solid ${theme.border}`, background: theme.surface, borderRadius: 3, padding: 16 }}>
      <h3 style={{ fontSize: 13, fontWeight: 500, color: theme.muted, marginBottom: 10 }}>{title}</h3>
      {children}
    </div>
  );
}
function Stat({ k, v, c }: { k: string; v: string; c?: string }) {
  return <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13, marginBottom: 6 }}><span style={{ color: theme.muted }}>{k}</span><span className="mono" style={{ color: c ?? theme.bone }}>{v}</span></div>;
}
const pnlC = (v: number | null | undefined) => ((v ?? 0) > 0 ? theme.positive : (v ?? 0) < 0 ? theme.negative : theme.muted);

// Volatilite konisi görseli — 2σ tam genişlik, 1σ gölge, spot işareti (merkez).
function BandBar({ b, spot }: { b: import("@/lib/api").BandRow; spot: number }) {
  const lo = b.low2, rng = b.high2 - b.low2 || 1;
  const clamp = (x: number) => Math.max(0, Math.min(100, x));
  const p1lo = clamp(((b.low1 - lo) / rng) * 100);
  const p1w = clamp(((b.high1 - b.low1) / rng) * 100);
  const sp = clamp(((spot - lo) / rng) * 100);
  return (
    <div style={{ position: "relative", height: 10, background: theme.border, borderRadius: 3 }}>
      <div style={{ position: "absolute", left: `${p1lo}%`, width: `${p1w}%`, top: 0, bottom: 0, background: `${theme.warning}33`, borderRadius: 2 }} />
      <div title={`spot ₺${spot.toFixed(2)}`} style={{ position: "absolute", left: `${sp}%`, top: -2, bottom: -2, width: 2, background: theme.bone, transform: "translateX(-1px)" }} />
    </div>
  );
}
function HorizonBand({ label, b, spot }: { label: string; b: import("@/lib/api").BandRow; spot: number }) {
  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, marginBottom: 3 }}>
        <span style={{ color: theme.muted }}>{label} <span style={{ opacity: 0.7 }}>±%{b.pct1.toFixed(1)}</span></span>
        <span className="mono" style={{ color: theme.bone }}>₺{b.low1.toFixed(2)} – ₺{b.high1.toFixed(2)}</span>
      </div>
      <BandBar b={b} spot={spot} />
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: theme.muted, marginTop: 2 }}>
        <span className="mono">₺{b.low2.toFixed(2)}</span>
        <span style={{ opacity: 0.7 }}>~%95 (2σ)</span>
        <span className="mono">₺{b.high2.toFixed(2)}</span>
      </div>
    </div>
  );
}

export default function TickerPage() {
  const params = useParams();
  const symbol = String(params.symbol || "").toUpperCase();
  const [aiOpen, setAiOpen] = useState(false);
  // detay canlı (30sn); grafik statik (global polling kapalı → zoom/pan sıfırlanmaz); AI on-demand
  const d = useQuery({ queryKey: ["ticker", symbol], queryFn: () => apiGet<TickerDetail>(`/api/ticker/${symbol}`), refetchInterval: 30_000 });
  const ch = useQuery({ queryKey: ["chart", symbol], queryFn: () => apiGet<ChartData>(`/api/ticker/${symbol}/chart`), staleTime: 5 * 60_000 });
  const setupsQ = useQuery({ queryKey: ["setups"], queryFn: fetchSetups, refetchInterval: 30_000 });
  const setup = setupsQ.data?.setups.find((s) => s.ticker === symbol) ?? null;
  const ai = useQuery({ queryKey: ["ai", symbol], enabled: aiOpen, staleTime: Infinity, queryFn: () => apiGet<import("@/lib/api").AIComment>(`/api/ai/ticker/${symbol}`) });
  const t = d.data;
  const buy = t?.signal === "buy" || t?.signal === "strong_buy";

  return (
    <main style={{ maxWidth: 1100, margin: "0 auto", padding: "28px 24px" }}>
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", borderBottom: `0.5px solid ${theme.border}`, paddingBottom: 14 }}>
        <div style={{ display: "flex", gap: 16, alignItems: "baseline" }}>
          <h1 className="mono" style={{ fontSize: 22 }}>{symbol}</h1>
          {t?.price != null && <span className="mono" style={{ fontSize: 18 }}>₺{num(t.price)}</span>}
          {t?.change_pct != null && <span className="mono" style={{ fontSize: 14, color: pnlC(t.change_pct) }}>{t.change_pct > 0 ? "+" : ""}{num(t.change_pct, 2)}%</span>}
          {t?.signal && <span style={{ fontSize: 12, color: buy ? theme.positive : theme.muted, border: `0.5px solid ${buy ? theme.positive : theme.border}`, borderRadius: 3, padding: "2px 8px" }}>{SIGNAL_TR[t.signal] ?? t.signal} · {num(t.score, 0)}</span>}
        </div>
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <a href={tvUrl(symbol)} target="_blank" rel="noopener noreferrer" style={linkBtn}>TradingView</a>
          <a href={midasUrl(symbol)} target="_blank" rel="noopener noreferrer" style={linkBtn}>Midas</a>
          <Link href="/" style={linkBtn}>← Dashboard</Link>
        </div>
      </header>

      {setup && <SetupBanner s={setup} />}

      <section style={{ marginTop: 16 }}>
        {ch.isLoading ? <p style={{ color: theme.muted }}>grafik yükleniyor…</p>
          : ch.data ? <><PriceChart data={ch.data} /><RsiChart data={ch.data} /></>
          : <p style={{ color: theme.warning }}>grafik verisi yok</p>}
      </section>

      <section style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 14, marginTop: 18 }}>
        <Panel title="Faktör-bias (skor kırılımı)">
          {t?.factors && t.factor_weights ? Object.keys(t.factors).filter((k) => (t.factor_weights![k] ?? 0) > 0).sort((a, b) => (t.factor_weights![b] ?? 0) - (t.factor_weights![a] ?? 0)).map((k) => (
            <div key={k} style={{ marginBottom: 8 }}>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
                <span style={{ color: theme.muted }}>{FACTOR_TR[k] ?? k} <span style={{ opacity: 0.6 }}>×{(t.factor_weights![k] ?? 0).toFixed(2)}</span></span>
                <span className="mono">{num(t.factors![k], 0)}</span>
              </div>
              <div style={{ height: 4, background: theme.border, borderRadius: 2, marginTop: 2 }}>
                <div style={{ width: `${Math.max(0, Math.min(100, t.factors![k] ?? 0))}%`, height: "100%", background: scoreColor(t.factors![k] ?? 50), borderRadius: 2 }} />
              </div>
            </div>
          )) : <p style={{ color: theme.muted, fontSize: 12 }}>skor yok</p>}
          {t && <div style={{ marginTop: 8, borderTop: `0.5px solid ${theme.border}`, paddingTop: 8 }}>
            <Stat k="Risk valfi" v={`×${num(t.risk_governor, 2)}`} c={(t.risk_governor ?? 1) < 1 ? theme.warning : theme.bone} />
            <Stat k="Haber (KAP)" v={`${(t.news_pos ?? 0) > 0 ? "+" + num(t.news_pos, 1) : ""}${(t.news_neg ?? 0) < 0 ? " " + num(t.news_neg, 1) : (t.news_pos ?? 0) > 0 ? "" : "yok"}`} c={(t.news_pos ?? 0) > 0 ? theme.positive : theme.muted} />
          </div>}
        </Panel>

        <Panel title="Fundamental (Bloomberg-vari)">
          <Stat k="F/K (PE)" v={num(t?.fundamentals.pe)} />
          <Stat k="PD/DD (PB)" v={num(t?.fundamentals.pb)} />
          <Stat k="Piotroski F" v={t?.fundamentals.f_score != null ? `${t.fundamentals.f_score}/9` : "N/A (banka)"} />
          <Stat k="ROA" v={pct(t?.fundamentals.roa)} />
          <Stat k="Piyasa değeri" v={t?.valuation.mcap != null ? `₺${fmt((t.valuation.mcap ?? 0) / 1e9, 1)} mlr` : "—"} />
          <Stat k="Yabancı oranı" v={pct(t?.valuation.foreign_ratio != null ? (t.valuation.foreign_ratio ?? 0) / 100 : null)} />
          <Stat k="52h konumu" v={t?.range_position_52w != null ? `${(t.range_position_52w * 100).toFixed(0)}%` : "—"} c={(t?.range_position_52w ?? 0) > 0.9 ? theme.warning : theme.bone} />
        </Panel>

        <Panel title="Teknik + getiri">
          <Stat k="RSI(14)" v={num(t?.indicators.rsi14, 0)} c={(t?.indicators.rsi14 ?? 0) > 72 ? theme.warning : theme.bone} />
          <Stat k="ATR%" v={pct(t?.indicators.atr_pct)} />
          <Stat k="ADX" v={num(t?.indicators.adx14, 0)} />
          <Stat k="EMA50 uzaklık" v={pct(t?.indicators.dist_ema50)} c={(t?.indicators.dist_ema50 ?? 0) > 0.18 ? theme.warning : theme.bone} />
          <Stat k="Getiri 1h / 1a / 3a" v={`${pct(t?.returns["1w"])} · ${pct(t?.returns["1m"])} · ${pct(t?.returns["3m"])}`} />
        </Panel>

        {t?.target_bands && (t.target_bands.horizons["5"] || t.target_bands.horizons["30"]) && (
          <Panel title="Hedef bandı (volatilite konisi)">
            {t.target_bands.horizons["5"] && <HorizonBand label="5 gün" b={t.target_bands.horizons["5"]} spot={t.target_bands.spot} />}
            {t.target_bands.horizons["30"] && <HorizonBand label="30 gün" b={t.target_bands.horizons["30"]} spot={t.target_bands.spot} />}
            <p style={{ fontSize: 10.5, color: theme.muted, lineHeight: 1.5, marginTop: 4 }}>
              Günlük oynaklık %{(t.target_bands.sigma_daily * 100).toFixed(1)}. Bant = <b style={{ color: theme.bone, fontWeight: 500 }}>belirsizlik aralığı</b>, yön tahmini değil — merkez bugünkü fiyat. Gölge ~%68 (1σ). Oynaklıktan hesaplanır; AI değil.
            </p>
          </Panel>
        )}

        <Panel title="Risk / pozisyon boyutu">
          {t?.sizing?.valid ? <>
            <Stat k="Önerilen adet" v={`${t.sizing.qty} lot`} />
            <Stat k="Stop (2·ATR)" v={t.sizing.stop != null ? `₺${num(t.sizing.stop)}` : "—"} />
            <Stat k="Risk" v={t.sizing.risk_amount != null ? `₺${fmt(t.sizing.risk_amount, 0)} (${pct(t.sizing.risk_pct)})` : "—"} />
            <Stat k="Notional" v={t.sizing.notional != null ? `₺${fmt(t.sizing.notional, 0)} (${pct(t.sizing.notional_pct, 0)})` : "—"} />
          </> : <p style={{ color: theme.muted, fontSize: 12 }}>ATR boyutu hesaplanamadı{t?.sizing?.reason ? ` (${t.sizing.reason})` : ""}</p>}
          {t?.position && <div style={{ marginTop: 8, borderTop: `0.5px solid ${theme.border}`, paddingTop: 8 }}>
            <Stat k="Pozisyonun" v={`${t.position.qty} lot @ ₺${num(t.position.avg_cost)}`} c={theme.positive} />
          </div>}
        </Panel>

        <Panel title="KAP açıklamaları">
          {t?.kap?.length ? t.kap.slice(0, 6).map((e, i) => (
            <div key={i} style={{ fontSize: 12, marginBottom: 6, opacity: e.active ? 1 : 0.55 }}>
              <span style={{ color: (e.direction ?? 0) > 0.1 ? theme.positive : (e.direction ?? 0) < -0.1 ? theme.negative : theme.muted }}>●</span>{" "}
              {e.url ? <a href={e.url} target="_blank" rel="noopener noreferrer" style={{ color: theme.bone, textDecoration: "none" }}>{e.title}</a> : e.title}
            </div>
          )) : <p style={{ color: theme.muted, fontSize: 12 }}>aktif KAP olayı yok</p>}
        </Panel>

        <Panel title="AI yorum">
          {!aiOpen ? <button onClick={() => setAiOpen(true)} style={{ ...linkBtn, cursor: "pointer", background: "transparent" }}>AI yorumu iste (Gemini)</button>
            : ai.isLoading ? <p style={{ color: theme.muted, fontSize: 12 }}>düşünüyor…</p>
            : ai.data?.available ? <p style={{ fontSize: 12.5, lineHeight: 1.6, whiteSpace: "pre-wrap" }}>{ai.data.comment}</p>
            : <p style={{ fontSize: 12, color: theme.warning }}>{ai.data?.message ?? "AI yok"}</p>}
        </Panel>
      </section>

      <p style={{ marginTop: 20, fontSize: 11, color: theme.muted, lineHeight: 1.6 }}>
        Free + 15dk gecikmeli veri. Yatırım tavsiyesi değildir; nihai karar senin. Gerçek-zamanlı tick/derinlik yoktur.
      </p>
    </main>
  );
}

const linkBtn: React.CSSProperties = { fontSize: 12, color: theme.muted, textDecoration: "none", border: `0.5px solid ${theme.border}`, borderRadius: 3, padding: "4px 10px" };
