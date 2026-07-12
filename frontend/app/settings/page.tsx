"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  fetchAiKeys, fetchFactors, fetchRiskProfile, measureFactors, putAiKeys, putFactorWeights, putRiskProfile,
  type AIKeys, type AIProvider, type FactorRow, type RiskProfileOverview,
} from "@/lib/api";
import { scoreColor, theme } from "@/lib/theme";
import { DASHBOARD_SECTIONS, useDashboardPrefs } from "@/lib/dashboardPrefs";

const btn: React.CSSProperties = {
  fontSize: 12, background: "transparent", color: theme.bone,
  border: `0.5px solid ${theme.border}`, borderRadius: 3, padding: "6px 12px", cursor: "pointer",
};
const inp: React.CSSProperties = {
  flex: 1, background: theme.bg, color: theme.bone, border: `0.5px solid ${theme.border}`,
  borderRadius: 3, padding: "7px 10px", fontSize: 13, fontFamily: "var(--font-mono)",
};

type KeysDraft = { vals: string[]; provider: AIProvider; baseUrl: string; model: string };

// Ölçülen kanıt rengi: güçlü (t>=2 / CI sıfırı hariç) → yeşil; negatif IC → oxblood; zayıf → muted.
function evColor(f: FactorRow): string {
  if (f.kind !== "measured") return theme.muted;
  if (f.ic != null && f.ic < 0) return theme.oxblood;
  return f.strong ? theme.positive : theme.muted;
}

function EvidenceBadge({ f }: { f: FactorRow }) {
  if (f.kind === "prior") {
    return (
      <span title="Fiyattan ölçülemez — research prior (fundamental/katalist)"
        style={{ fontSize: 11, color: theme.muted, cursor: "help" }}>
        prior {f.ic != null ? `~${f.ic.toFixed(3)}` : "—"}
      </span>
    );
  }
  const c = evColor(f);
  return (
    <span className="mono" title={`rank-IC (5g ileri) · isabet %${f.hit != null ? (f.hit * 100).toFixed(0) : "—"}`}
      style={{ fontSize: 11, color: c, cursor: "help", whiteSpace: "nowrap" }}>
      IC {f.ic != null ? `${f.ic >= 0 ? "+" : ""}${f.ic.toFixed(3)}` : "—"}
      {f.t != null && <span style={{ color: theme.muted }}> · t {f.t >= 0 ? "+" : ""}{f.t.toFixed(2)}</span>}
      {f.strong && <span style={{ color: theme.positive }}> ●</span>}
    </span>
  );
}

// --- AI API anahtarları + sağlayıcı (kontrollü — kaydet global bardan) ---
function ApiKeysPanel({ data, draft, onChange }: {
  data?: AIKeys; draft: KeysDraft; onChange: (d: KeysDraft) => void;
}) {
  const savedProv = (data?.provider as AIProvider) || "gemini";
  const switching = draft.provider !== savedProv;
  const enabled = data?.enabled;
  const srcLabel = data?.source === "env" ? ".env" : data?.source === "db" ? "Ayarlar" : "yok";
  const keyPh = draft.provider === "openai" ? "sk-… (boş = yok)" : "AQ.… (boş = yok)";
  return (
    <div style={{ border: `0.5px solid ${theme.border}`, background: theme.surface, borderRadius: 4, padding: 16 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <h2 style={{ fontSize: 14, fontWeight: 500 }}>AI API Anahtarları</h2>
        <span style={{ fontSize: 12, color: enabled ? theme.positive : theme.muted }}>
          {enabled ? `● AI aktif · ${data?.count} anahtar · ${srcLabel}` : "○ AI kapalı — anahtar ekle"}
        </span>
      </div>
      <p style={{ fontSize: 12, color: theme.muted, lineHeight: 1.6, margin: "8px 0 12px" }}>
        İsteğe bağlı — AI olmadan da sistem çalışır (skor/sinyal deterministiktir). Anahtarlar
        yerel <span className="mono">bist.db</span>'de saklanır (git'e girmez), maskeli gösterilir.
      </p>

      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: draft.provider === "openai" ? 10 : 4 }}>
        <span style={{ fontSize: 11, color: theme.muted, width: 58 }}>Sağlayıcı</span>
        <select value={draft.provider} onChange={(e) => onChange({ ...draft, provider: e.target.value as AIProvider })}
          style={{ background: theme.bg, color: theme.bone, border: `0.5px solid ${theme.border}`,
            borderRadius: 3, padding: "6px 10px", fontSize: 13 }}>
          <option value="gemini">Gemini (Google · ücretsiz katman)</option>
          <option value="openai">OpenAI-uyumlu (OpenAI · DeepSeek · yerel)</option>
        </select>
      </div>

      {draft.provider === "openai" && (
        <div style={{ display: "grid", gap: 8, marginBottom: 4 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span style={{ fontSize: 11, color: theme.muted, width: 58 }}>Base URL</span>
            <input value={draft.baseUrl} onChange={(e) => onChange({ ...draft, baseUrl: e.target.value })}
              placeholder="https://api.openai.com/v1  ·  DeepSeek: https://api.deepseek.com" style={inp} />
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span style={{ fontSize: 11, color: theme.muted, width: 58 }}>Model</span>
            <input value={draft.model} onChange={(e) => onChange({ ...draft, model: e.target.value })}
              placeholder="gpt-4o-mini  ·  deepseek-chat  ·  llama3.1" style={inp} />
          </div>
        </div>
      )}

      <div style={{ display: "grid", gap: 8, marginTop: 8 }}>
        {[0, 1, 2, 3].map((i) => {
          const existing = !switching ? data?.keys?.[i] : undefined;
          return (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <span style={{ fontSize: 11, color: theme.muted, width: 58 }}>Anahtar {i + 1}</span>
              <input type="password" autoComplete="off" value={draft.vals[i]}
                placeholder={existing ? `${existing} (kayıtlı — değiştirmek için gir)` : keyPh}
                onChange={(e) => onChange({ ...draft, vals: draft.vals.map((v, j) => (j === i ? e.target.value : v)) })}
                style={inp} />
            </div>
          );
        })}
      </div>
      <div style={{ fontSize: 11, color: theme.muted, marginTop: 10 }}>
        {switching ? "Sağlayıcı değişti — yeni anahtar(lar) gir." : "Boş bıraktığın slotlar korunur."}
      </div>
    </div>
  );
}

// --- Risk profili — seçim TASLAK olur (kaydet global bardan); agresifte dürüst uyarı ---
function RiskPanel({ data, selected, onSelect }: {
  data?: RiskProfileOverview; selected: string; onSelect: (name: string) => void;
}) {
  if (!data) return null;
  const names = ["temkinli", "dengeli", "agresif"];
  const ar = data.applied_risk;

  function pick(name: string) {
    if (name === selected) return;
    const p = data!.profiles[name];
    if (name === "agresif") {
      const s8 = p.math.streaks["8"];
      const ok = confirm(
        `Agresif profil: işlem başına %${(p.base_r * 100).toFixed(1)} risk.\n\n` +
        `Dürüst matematik (${p.math.n_trades_window} işlemde, isabet ~%${(p.math.assumed_hit_rate * 100).toFixed(0)}):\n` +
        `8'li kayıp serisi olasılığı %${(s8.p_streak * 100).toFixed(0)} → hesap −%${(s8.drawdown * 100).toFixed(1)} çukur.\n\n` +
        `Kazanç VE kayıp aynı oranda büyür; edge değişmez. Devam?`);
      if (!ok) return;
    }
    onSelect(name);
  }

  return (
    <div style={{ border: `0.5px solid ${theme.border}`, background: theme.surface, borderRadius: 4, padding: 16 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <h2 style={{ fontSize: 14, fontWeight: 500 }}>Risk Profili</h2>
        <span style={{ fontSize: 11, color: theme.muted }}>isabet kaynağı: {data.hit_rate_source}</span>
      </div>
      <p style={{ fontSize: 12, color: theme.muted, lineHeight: 1.6, margin: "8px 0 10px" }}>
        Tek seçimle risk iştahı — pozisyon büyüklüğünü ölçekler (kaydedince sizing uygular; edge değişmez).
        Seçmeden önce dürüst kayıp-serisi matematiği aşağıda.
      </p>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 10 }}>
        {names.map((name) => {
          const p = data.profiles[name];
          if (!p) return null;
          const isActive = data.active === name;
          const isSel = selected === name;
          const accent = name === "agresif" ? theme.oxblood : name === "temkinli" ? "#5C86A8" : theme.positive;
          const boxColor = isSel ? accent : theme.border;
          const s6 = p.math.streaks["6"], s8 = p.math.streaks["8"];
          return (
            <button key={name} onClick={() => pick(name)}
              style={{ textAlign: "left", cursor: isSel ? "default" : "pointer", background: theme.bg,
                borderTop: `0.5px solid ${boxColor}`, borderRight: `0.5px solid ${boxColor}`,
                borderBottom: `0.5px solid ${boxColor}`, borderLeft: `2px solid ${accent}`,
                borderRadius: 4, padding: "10px 12px", opacity: isSel ? 1 : 0.72, color: theme.bone }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                <span style={{ fontSize: 13, fontWeight: 600, color: accent }}>{p.label}</span>
                {isActive
                  ? <span style={{ fontSize: 10, color: accent, border: `0.5px solid ${accent}`, borderRadius: 3, padding: "0 5px" }}>AKTİF</span>
                  : isSel && <span style={{ fontSize: 10, color: theme.warning, border: `0.5px solid ${theme.warning}`, borderRadius: 3, padding: "0 5px" }}>SEÇİLDİ</span>}
              </div>
              <div className="mono" style={{ fontSize: 12, marginTop: 6 }}>
                işlem riski <span style={{ color: theme.bone }}>%{(p.base_r * 100).toFixed(1)}</span>
                {" · "}heat ≤ <span style={{ color: theme.bone }}>%{(p.max_heat_pct * 100).toFixed(0)}</span>
              </div>
              <div className="mono" style={{ fontSize: 11, color: theme.muted, marginTop: 4 }}
                title={`${p.math.n_trades_window} işlemlik pencerede en az bir kez 6/8 ardışık kayıp görme olasılığı ve o serinin hesapta açtığı çukur`}>
                6'lı seri %{(s6.p_streak * 100).toFixed(0)} → −%{(s6.drawdown * 100).toFixed(1)}
                {" · "}8'li %{(s8.p_streak * 100).toFixed(0)} → −%{(s8.drawdown * 100).toFixed(1)}
              </div>
              <div style={{ fontSize: 11, color: theme.muted, marginTop: 4 }}>{p.desc}</div>
            </button>
          );
        })}
      </div>
      <div style={{ fontSize: 11, color: theme.muted, marginTop: 10, lineHeight: 1.5 }}>{data.note}</div>
      {ar && ar.base_r != null && (
        <div style={{ fontSize: 11, color: theme.muted, marginTop: 8, paddingTop: 8, borderTop: `0.5px solid ${theme.border}` }}>
          Uygulanan (sizing bunu kullanır): işlem riski <span className="mono" style={{ color: theme.bone }}>%{(ar.base_r * 100).toFixed(1)}</span>
          {ar.max_heat_pct != null && <> · heat ≤ <span className="mono" style={{ color: theme.bone }}>%{(ar.max_heat_pct * 100).toFixed(0)}</span></>}
          {ar.daily_stop_pct != null && <> · günlük stop <span className="mono" style={{ color: theme.bone }}>%{(ar.daily_stop_pct * 100).toFixed(1)}</span></>}
        </div>
      )}
    </div>
  );
}

// --- Dashboard bölümleri (görünüm tercihi — anında, tarayıcıda saklanır; save-bar'a girmez) ---
function SectionsPanel() {
  const prefs = useDashboardPrefs();
  return (
    <div style={{ border: `0.5px solid ${theme.border}`, background: theme.surface, borderRadius: 4, padding: 16 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <h2 style={{ fontSize: 14, fontWeight: 500 }}>Dashboard Bölümleri</h2>
        <span style={{ fontSize: 11, color: theme.muted }}>anında uygulanır · tarayıcıda saklanır</span>
      </div>
      <p style={{ fontSize: 12, color: theme.muted, lineHeight: 1.6, margin: "8px 0 12px" }}>
        Panelde hangi bölümler görünsün. Kapatılan dashboard'dan kalkar (başlıktaki ▸ ile küçültebilirsin de).
      </p>
      <div style={{ display: "grid", gap: 6 }}>
        {DASHBOARD_SECTIONS.map((s) => {
          const on = !prefs.isHidden(s.id);
          return (
            <label key={s.id} style={{ display: "flex", alignItems: "center", gap: 10, cursor: "pointer",
              padding: "7px 10px", border: `0.5px solid ${theme.border}`, borderRadius: 3, background: theme.bg }}>
              <input type="checkbox" checked={on} onChange={(e) => prefs.setHidden(s.id, !e.target.checked)}
                style={{ accentColor: theme.positive }} />
              <span style={{ fontSize: 13, color: on ? theme.bone : theme.muted }}>{s.label}</span>
              <span style={{ marginLeft: "auto", fontSize: 11, color: on ? theme.positive : theme.muted }}>
                {on ? "görünür" : "gizli"}
              </span>
            </label>
          );
        })}
      </div>
    </div>
  );
}

export default function SettingsPage() {
  const qc = useQueryClient();
  const router = useRouter();
  const keysQ = useQuery({ queryKey: ["ai-keys"], queryFn: fetchAiKeys });
  const riskQ = useQuery({ queryKey: ["risk-profile"], queryFn: fetchRiskProfile });
  const factorsQ = useQuery({ queryKey: ["factors"], queryFn: fetchFactors });
  const keysData = keysQ.data, riskData = riskQ.data, factorsData = factorsQ.data;

  const [keysDraft, setKeysDraft] = useState<KeysDraft>({ vals: ["", "", "", ""], provider: "gemini", baseUrl: "", model: "" });
  const [riskSel, setRiskSel] = useState<string | null>(null);
  const [w, setW] = useState<Record<string, number>>({});
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [measuring, setMeasuring] = useState(false);
  const [leaveOpen, setLeaveOpen] = useState(false);
  const hydrated = useRef({ keys: false, risk: false, w: false });

  // Sunucu durumundan bir kez hidratla (arka plan refetch taslağı ezmesin)
  useEffect(() => {
    if (keysData && !hydrated.current.keys) {
      setKeysDraft({ vals: ["", "", "", ""], provider: (keysData.provider as AIProvider) || "gemini",
        baseUrl: keysData.base_url || "", model: keysData.model || "" });
      hydrated.current.keys = true;
    }
  }, [keysData]);
  useEffect(() => {
    if (riskData && !hydrated.current.risk) { setRiskSel(riskData.active); hydrated.current.risk = true; }
  }, [riskData]);
  useEffect(() => {
    if (factorsData && !hydrated.current.w) {
      setW(Object.fromEntries(factorsData.factors.map((f) => [f.key, f.weight])));
      hydrated.current.w = true;
    }
  }, [factorsData]);

  const factors = factorsData?.factors ?? [];
  const sum = useMemo(() => Object.values(w).reduce((s, v) => s + v, 0), [w]);

  const savedProv = (keysData?.provider as AIProvider) || "gemini";
  const keysDirty = !!keysData && (
    keysDraft.vals.some((v) => v.trim()) || keysDraft.provider !== savedProv ||
    (keysDraft.provider === "openai" && (keysDraft.baseUrl !== (keysData.base_url || "") || keysDraft.model !== (keysData.model || "")))
  );
  const riskDirty = !!riskData && riskSel != null && riskSel !== riskData.active;
  const wDirty = !!factorsData && factorsData.factors.some((f) => Math.abs((w[f.key] ?? 0) - f.weight) > 1e-9);
  const anyDirty = keysDirty || riskDirty || wDirty;

  const dirtyBits = [keysDirty && "anahtar", riskDirty && "risk", wDirty && "ağırlıklar"].filter(Boolean).join(", ");

  // Sekme/pencere kapatma (kaydetmeden) → tarayıcı uyarısı
  useEffect(() => {
    if (!anyDirty) return;
    const h = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", h);
    return () => window.removeEventListener("beforeunload", h);
  }, [anyDirty]);

  async function saveAll() {
    setBusy(true);
    try {
      if (keysDirty) {
        await putAiKeys(keysDraft.vals, keysDraft.provider, keysDraft.baseUrl, keysDraft.model);
        setKeysDraft({ ...keysDraft, vals: ["", "", "", ""] });
        qc.invalidateQueries({ queryKey: ["ai-keys"] });
        qc.invalidateQueries({ queryKey: ["ai-budget"] });
      }
      if (riskDirty && riskSel) {
        await putRiskProfile(riskSel);
        qc.invalidateQueries({ queryKey: ["risk-profile"] });
        qc.invalidateQueries({ queryKey: ["portfolio"] });
      }
      if (wDirty) {
        await putFactorWeights(w);
        ["factors", "scores", "opportunities"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
      }
      setSaved(true);
      setTimeout(() => setSaved(false), 1600);
    } finally { setBusy(false); }
  }

  function resetAll() {
    if (keysData) setKeysDraft({ vals: ["", "", "", ""], provider: (keysData.provider as AIProvider) || "gemini",
      baseUrl: keysData.base_url || "", model: keysData.model || "" });
    if (riskData) setRiskSel(riskData.active);
    if (factorsData) setW(Object.fromEntries(factorsData.factors.map((f) => [f.key, f.weight])));
  }

  // "ölçüme göre öner": ağırlık ∝ max(0, IC), normalize (kalibrasyonun ham hâli — priorlar dahil).
  function suggest() {
    const raw = Object.fromEntries(factors.map((f) => [f.key, Math.max(0, f.ic ?? 0)]));
    const tot = Object.values(raw).reduce((s, v) => s + v, 0) || 1;
    setW(Object.fromEntries(Object.entries(raw).map(([k, v]) => [k, Math.round((v / tot) * 100) / 100])));
  }
  async function remeasure() {
    setMeasuring(true);
    try { await measureFactors(); qc.invalidateQueries({ queryKey: ["factors"] }); }
    finally { setMeasuring(false); }
  }

  function goBack() {
    if (anyDirty) setLeaveOpen(true);
    else router.push("/");
  }

  return (
    <main style={{ maxWidth: 820, margin: "0 auto", padding: "32px 24px", paddingBottom: anyDirty ? 96 : 32 }}>
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline",
        borderBottom: `0.5px solid ${theme.border}`, paddingBottom: 16, marginBottom: 6 }}>
        <div>
          <h1 style={{ fontSize: 18, fontWeight: 500 }}>Ayarlar</h1>
          <p style={{ fontSize: 13, color: theme.muted, marginTop: 2 }}>
            API anahtarları + risk profili + strateji ağırlıkları.
          </p>
        </div>
        <button onClick={goBack} style={{ ...btn, color: theme.muted }}>← Panel</button>
      </header>

      <section style={{ marginTop: 20 }}>
        <ApiKeysPanel data={keysData} draft={keysDraft} onChange={setKeysDraft} />
      </section>

      <section style={{ marginTop: 28 }}>
        <RiskPanel data={riskData} selected={riskSel ?? riskData?.active ?? "dengeli"} onSelect={setRiskSel} />
      </section>

      <section style={{ marginTop: 28 }}>
        <SectionsPanel />
      </section>

      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginTop: 28, marginBottom: 4, gap: 10, flexWrap: "wrap" }}>
        <h2 style={{ fontSize: 14, fontWeight: 500 }}>Strateji Ağırlıkları</h2>
        <span style={{ fontSize: 12, color: theme.muted }}>
          Skor = Σ(faktör × ağırlık){factorsData?.diagnostic_as_of ? ` · ölçüm ${factorsData.diagnostic_as_of.slice(0, 10)}${factorsData.diagnostic_params?.n_tickers ? ` (${factorsData.diagnostic_params.n_tickers} isim)` : ""}` : ""}
        </span>
      </div>

      <p style={{ fontSize: 12, color: theme.muted, lineHeight: 1.6, margin: "6px 0 14px" }}>
        Yeşil <span style={{ color: theme.positive }}>●</span> = istatistiksel güçlü (t≥2 ya da %95 CI sıfırı
        hariç). Gri = zayıf/anlamsız. <span style={{ color: theme.oxblood }}>Oxblood</span> = negatif (kaybettirir).
        <span style={{ color: theme.muted }}> prior</span> = fiyattan ölçülemez (fundamental/katalist), research
        tahmini. <b style={{ color: theme.bone }}>Dürüst uyarı:</b> ölçüm tek rejim (2 yıl disinflasyon) — kanıt
        değil, işaret; low-vol tek güçlü faktör.
      </p>

      {factorsQ.isLoading ? (
        <div style={{ padding: 40, textAlign: "center", color: theme.muted }}>yükleniyor…</div>
      ) : (
        <>
          <div style={{ border: `0.5px solid ${theme.border}`, background: theme.surface, borderRadius: 4 }}>
            {factors.map((f, i) => {
              const raw = w[f.key] ?? 0;
              const norm = sum > 0 ? raw / sum : 0;   // motorun gerçekte kullanacağı pay
              return (
                <div key={f.key} style={{ display: "grid", gridTemplateColumns: "1fr 260px",
                  gap: 16, alignItems: "center", padding: "12px 16px",
                  borderTop: i === 0 ? "none" : `0.5px solid ${theme.border}`,
                  opacity: f.kind === "prior" ? 0.82 : 1 }}>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontSize: 13, color: theme.bone }}>{f.label}</div>
                    <div style={{ marginTop: 3 }}><EvidenceBadge f={f} /></div>
                  </div>
                  <div>
                    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                      <input type="range" min={0} max={0.7} step={0.01} value={raw}
                        onChange={(e) => setW({ ...w, [f.key]: Number(e.target.value) })}
                        style={{ flex: 1, accentColor: f.strong ? theme.positive : theme.muted }} />
                      <span className="mono" style={{ fontSize: 13, width: 42, textAlign: "right",
                        color: raw > 0 ? theme.bone : theme.muted }}>
                        {Math.round(norm * 100)}%
                      </span>
                    </div>
                    <div style={{ height: 4, background: theme.border, borderRadius: 2, marginTop: 6 }}>
                      <div style={{ width: `${Math.min(100, norm * 100)}%`, height: "100%",
                        background: scoreColor(f.strong ? 80 : 50), borderRadius: 2 }} />
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap", marginTop: 16 }}>
            <span style={{ fontSize: 12, color: theme.muted }}>
              Ham toplam <span className="mono" style={{ color: sum > 0 ? theme.bone : theme.negative }}>
                {Math.round(sum * 100)}%</span> (motor normalize eder — mutlak değer değil, oran önemli)
            </span>
            <span style={{ marginLeft: "auto", display: "flex", gap: 8, flexWrap: "wrap" }}>
              <button onClick={suggest} style={btn} title="Ağırlık ∝ ölçülen IC (kalibrasyonun ham hâli; priorlar dahil)">
                Ölçüme göre öner
              </button>
              <button onClick={remeasure} disabled={measuring} style={btn}
                title="Factor diagnostic'i yeniden koştur (uzun — evren geneli rank-IC)">
                {measuring ? "ölçülüyor…" : "Ölçümü yenile"}
              </button>
            </span>
          </div>

          <p style={{ fontSize: 11, color: theme.muted, marginTop: 14, lineHeight: 1.6 }}>
            Kaydedince motor anında yeni ağırlıklarla skorlar (bir sonraki refresh). Haftalık kalibrasyon
            bunu artık SESSİZCE EZMEZ — önerileri ayrı tutar (config → factor_weights_suggested).
            "momentum" = bileşik trend-gücü (EMA-mesafesi+RSI+ROC+52h); "20g momentum" = düz ROC20.
            Hatırlatma: skor kısa-vade TAHMİN değil, low-vol kalite/bağlam filtresidir — asıl kısa-vade
            edge setup katmanındadır.
          </p>
        </>
      )}

      {/* Global kaydet-barı — herhangi bir değişiklikte belirir */}
      {anyDirty && (
        <div style={{ position: "fixed", left: 0, right: 0, bottom: 0, background: theme.surface,
          borderTop: `0.5px solid ${theme.border}`, boxShadow: "0 -4px 20px rgba(0,0,0,0.35)", zIndex: 50 }}>
          <div style={{ maxWidth: 820, margin: "0 auto", padding: "12px 24px", display: "flex",
            alignItems: "center", gap: 12, flexWrap: "wrap" }}>
            <span style={{ fontSize: 12, color: saved ? theme.positive : theme.warning }}>
              {saved ? "✓ kaydedildi" : `● Kaydedilmemiş değişiklik${dirtyBits ? ` — ${dirtyBits}` : ""}`}
            </span>
            <span style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
              <button onClick={resetAll} disabled={busy} style={btn}>Sıfırla</button>
              <button onClick={saveAll} disabled={busy}
                style={{ ...btn, borderColor: theme.positive, color: theme.positive, padding: "6px 18px" }}>
                {busy ? "…" : "Kaydet"}
              </button>
            </span>
          </div>
        </div>
      )}

      {/* Kaydetmeden çıkış onayı */}
      {leaveOpen && (
        <div onClick={() => setLeaveOpen(false)}
          style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", display: "flex",
            alignItems: "center", justifyContent: "center", zIndex: 60, padding: 20 }}>
          <div onClick={(e) => e.stopPropagation()}
            style={{ background: theme.surface, border: `0.5px solid ${theme.border}`, borderRadius: 6, padding: 20, maxWidth: 400 }}>
            <div style={{ fontSize: 14, color: theme.bone }}>Kaydedilmemiş değişiklikler var</div>
            <p style={{ fontSize: 12, color: theme.muted, margin: "8px 0 16px", lineHeight: 1.5 }}>
              Değiştirdiğin {dirtyBits || "ayarlar"} henüz kaydedilmedi. Panele dönmeden ne yapayım?
            </p>
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", flexWrap: "wrap" }}>
              <button onClick={() => setLeaveOpen(false)} style={btn}>İptal</button>
              <button onClick={() => { setLeaveOpen(false); router.push("/"); }}
                style={{ ...btn, borderColor: theme.oxblood, color: theme.oxblood }}>Kaydetmeden çık</button>
              <button onClick={async () => { await saveAll(); setLeaveOpen(false); router.push("/"); }}
                style={{ ...btn, borderColor: theme.positive, color: theme.positive }}>Kaydet ve çık</button>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}
