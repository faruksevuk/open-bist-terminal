// Backend (FastAPI) REST istemcisi. Hiçbir hesap frontend'de yapılmaz (§14.3).
const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API ${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`API ${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

export async function apiPut<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`API ${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

// Hisse yönlendiricileri (kullanıcı doğruladı).
export const tvUrl = (t: string) => `https://www.tradingview.com/symbols/BIST-${t.toUpperCase()}/`;
export const midasUrl = (t: string) =>
  `https://www.getmidas.com/canli-borsa/${t.toLowerCase()}-hisse/`;

export type AccountConfig = { starting_cash_try: number; start_date: string; annual_cpi: number };

export type AIBudget = {
  date: string;
  used: number;
  cap: number;
  remaining: number;
  exhausted: boolean;
  enabled: boolean;
};

export type AIComment = {
  available: boolean;
  comment?: string;
  message?: string;
  context?: Record<string, unknown>;
  budget?: AIBudget;
};

export const fetchAiBudget = () => apiGet<AIBudget>("/api/ai/budget");

export type KapEvent = {
  title: string | null;
  type: string | null;
  direction: number | null;
  magnitude: number | null;
  confidence: number | null;
  mechanism: string | null;
  published_at: string | null;
  active: boolean;
  url: string | null;
};
export type KapResponse = { ticker: string; events: KapEvent[] };

export type Candle = { time: string; open: number; high: number; low: number; close: number };
export type ChartPoint = { time: string; value: number };
export type ChartData = {
  symbol: string;
  candles: Candle[];
  volume: { time: string; value: number; color: string }[];
  ema20: ChartPoint[];
  ema50: ChartPoint[];
  ema200: ChartPoint[];
  rsi: ChartPoint[];
};

export type TickerDetail = {
  ticker: string;
  price: number | null;
  change_pct: number | null;
  score: number | null;
  signal: string | null;
  meets_absolute_threshold: boolean | null;
  factors?: Record<string, number | null> | null;
  factor_weights?: Record<string, number> | null;
  news_pos: number | null;
  news_neg: number | null;
  risk_governor: number | null;
  indicators: Record<string, number | null>;
  returns: Record<string, number | null>;
  range_position_52w: number | null;
  fundamentals: Record<string, number | null>;
  valuation: Record<string, number | null>;
  target_bands: TargetBands | null;
  sizing: SizePreview;
  position: { qty: number; avg_cost: number | null; stop: number | null } | null;
  kap: { title: string | null; direction: number | null; mechanism: string | null; active: boolean; url: string | null }[];
};

// Hedef fiyat bandı — volatilite konisi (deterministik; yön tahmini DEĞİL, belirsizlik aralığı).
export type BandRow = {
  low1: number; high1: number;   // ~%68 (1σ)
  low2: number; high2: number;   // ~%95 (2σ)
  sigma_h: number; pct1: number; pct2: number;
};
export type TargetBands = {
  spot: number;
  sigma_daily: number;                    // günlük oynaklık (0.021 = %2.1)
  horizons: Record<string, BandRow>;      // "5" | "30" → bant
};

export const fmt = (n: number | null | undefined, d = 0): string =>
  n == null ? "—" : n.toLocaleString("tr-TR", { minimumFractionDigits: d, maximumFractionDigits: d });

export type Health = {
  status: "ok" | "degraded";
  db: boolean;
  cache?: string;   // "memory" (in-process; Redis kaldırıldı)
  version: string;
};

export type ScoreRow = {
  ticker: string;
  sector?: string | null;
  score: number;
  signal: "strong_buy" | "buy" | "hold" | "reduce" | "sell";
  passed_gates: boolean;
  meets_absolute_threshold: boolean;
  sub_quality: number;
  sub_oversold: number;
  sub_cause: number;
  sub_stab: number;
  risk_governor: number;
  news_pos?: number;
  news_neg?: number;
  reasoning?: {
    f_score?: number | null;
    atr_pct?: number | null;
    gate_reasons?: string[];
    factors?: Record<string, number | null>;
    factor_weights?: Record<string, number>;
    abs_threshold_eff?: number | null;
  };
};

export type Opportunities = {
  as_of: string | null;
  count: number;
  total_before_cap?: number;
  sector_cap?: number;
  opportunities: ScoreRow[];
};
export type Scores = { as_of: string | null; scores: ScoreRow[] };

export const SIGNAL_TR: Record<string, string> = {
  strong_buy: "Güçlü Al",
  buy: "Al",
  hold: "Tut",
  reduce: "Azalt",
  sell: "Sat",
};

export type Position = {
  ticker: string;
  qty: number;
  avg_cost: number;
  last: number;
  stop: number | null;
  pnl_try: number;
  pnl_pct: number;
  score?: number | null;
  signal?: string | null;
};

export type Sparkline = { points: number[]; change_1y: number | null; last: number };
export type SparkResponse = { sparklines: Record<string, Sparkline> };

export type Portfolio = {
  cash_try: number;
  invested_try: number;
  total_try: number;
  total_usd: number | null;
  total_real_try: number;
  usdtry: number;
  usdtry_stale: boolean;
  open_heat_pct: number;
  cash_pct: number;
  pnl_total_try: number;
  pnl_total_pct: number;
  positions: Position[];
  as_of: string;
};

// --- Setup sinyalleri (kısa-vade işlem katmanı) -------------------------
export type SetupVerdict =
  | "kanıtlı"
  | "zayıf"
  | "deneysel"
  | "deneysel (prior — PIT yok)"
  | "devre dışı"
  | string;

export type SetupEvidenceSummary = {
  verdict: SetupVerdict;
  n_events: number | null;
  hit_rate: number | null;
  mean_excess_5d: number | null;
  t: number | null;
  profit_factor: number | null;
  ci95_5d?: (number | null)[];
  status?: string | null;
};

export type SetupType =
  | "snapback"
  | "squeeze_breakout"
  | "trend_pullback"
  | "pead_drift"
  | "quiet_accumulation"
  | string;

// İşlem-planı ekonomisi (maliyet DAHİL) — backend priority.plan_economics çıktısı.
export type SetupPlan = {
  risk_frac: number;        // (giriş−stop)/giriş
  rr: number;               // plan R-katı (hedefe)
  cost_r: number;           // round-trip maliyetin R cinsinden bedeli
  net_target_r: number;     // hedef vurulursa NET kazanç (R)
  net_stop_r: number;       // stop vurulursa NET kayıp (R)
  breakeven_hit: number;    // başabaş isabet oranı
  feasible: boolean;        // false → maliyet 1R bütçesini yiyor, girilemez
};

export type SetupAdvice = "al-adayı" | "izle" | "girme" | string;

export type SetupSignal = {
  ticker: string;
  sector: string | null;
  setup: SetupType;
  setup_label: string;
  strength: number | null;
  entry_ref: number | null;
  stop: number | null;
  target: number | null;
  r_multiple: number | null;
  time_exit_days: number | null;
  triggered_at: string | null;
  valid_until: string | null;
  evidence: SetupEvidenceSummary;
  score: number | null;
  context: Record<string, unknown> | null;
  // --- işlem-öncelik katmanı (backend priority.py; formül ön-kayıtlı) ---
  expected_r_net: number;         // beklenen NET R/işlem (event-study + canlı OOS harmanı)
  expected_r_src: string;         // kaynağı ("event-study net n=1181 + canlı n=9 …")
  plan: SetupPlan | null;
  context_mult: number;           // sektör+rejim tilt çarpanı (~0.72–1.32, PRIOR)
  news_mult: number;
  news_pos: number;
  priority: number;               // 0-100 (sıralama bunu izler)
  advice: SetupAdvice;
  advice_reason: string;
};

export type SetupMarket = {
  mkt_ret_5d: number | null;
  mkt_above_ema50: boolean | null;
  breadth: number | null;
  regime?: MacroRegime | null;        // üst-aşağı rejim (market_context'ten)
  regime_score?: number | null;
};

export type SetupsResponse = {
  as_of: string | null;
  market: SetupMarket | null;
  round_trip_cost_pct?: number | null;
  count: number;
  setups: SetupSignal[];
};

// /api/setups/evidence — tam setup_evidence config blob'u (event-study çıktısı).
// Koşulmadıysa yalnız {note} döner; alanların hepsi opsiyonel ele alınmalı.
export type EvidenceHorizon = {
  mean_excess?: number | null;
  median_excess?: number | null;
  hit_rate?: number | null;
  n?: number | null;
  t_newey_west?: number | null;
  ci95_low?: number | null;
  ci95_high?: number | null;
};

export type EvidenceTradeSim = {
  n_trades?: number | null;
  mean_R?: number | null;
  avg_win?: number | null;
  avg_loss?: number | null;
  profit_factor?: number | null;
  hit_rate_R?: number | null;
  // NET (round-trip komisyon+spread düşülmüş) — friction realizmi
  mean_R_net?: number | null;
  profit_factor_net?: number | null;
  hit_rate_R_net?: number | null;
};

// Ön-kayıtlı rejim dilimi (tetikte piyasa EMA50 üstü/altı) — SADECE raporlama.
export type EvidenceRegimeSlice = {
  n: number;
  mean_excess_5d?: number | null;
  hit_rate_5d?: number | null;
  mean_R_net?: number | null;
  profit_factor_net?: number | null;
};

export type EvidenceSetup = {
  setup?: string;
  n_events?: number | null;
  n_days?: number | null;
  hit_rate_5d?: number | null;
  excess?: Record<string, EvidenceHorizon> | null;
  mean_excess_5d?: number | null;
  t_newey_west_5d?: number | null;
  trade_sim?: EvidenceTradeSim | null;
  profit_factor?: number | null;
  profit_factor_net?: number | null;   // NET PF (maliyet sonrası)
  mean_R_net?: number | null;           // NET ort R (maliyet sonrası)
  by_regime?: Record<string, EvidenceRegimeSlice> | null;  // "up"/"down" — raporlama-only
  verdict?: SetupVerdict;
  note?: string;
};

export type SetupEvidence = {
  as_of?: string | null;
  n_tickers?: number | null;
  horizons?: number[];
  methodology?: string;
  round_trip_cost_pct?: number | null;  // net trade-sim'de kullanılan round-trip maliyet
  pead_drift?: { verdict?: string; status?: string; note?: string };
  setups?: Record<string, EvidenceSetup>;
  note?: string;
};

export const fetchSetups = () => apiGet<SetupsResponse>("/api/setups");
export const fetchSetupEvidence = () => apiGet<SetupEvidence>("/api/setups/evidence");

// --- Canlı sonuç-takibi (OOS) — /api/setups/outcomes -------------------
// Ateşlenen her sinyalin gerçekte ne olduğu (target/stop/time_exit/no_entry) canlı olarak
// birikir; "zayıf/deneysel" verdict'lerinden "kanıtlı"ya giden tek dürüst yol.
export type OutcomePerSetup = {
  setup?: string;
  setup_label?: string;
  n_closed: number;
  n_pending: number;
  n_no_entry: number;
  n_target: number;
  n_stop: number;
  n_time_exit: number;
  isabet: number | null;      // R>0 oranı (kapalılar)
  target_rate: number | null;
  stop_rate: number | null;
  ort_r: number | null;
  medyan_r: number | null;
  toplam_r: number | null;
  ort_pct: number | null;
  ort_gun: number | null;
  // NET (round-trip komisyon+spread düşülmüş; TÜRETİLMİŞ, saklanmaz)
  ort_r_net: number | null;
  medyan_r_net: number | null;
  toplam_r_net: number | null;
  ort_pct_net: number | null;
  isabet_net: number | null;
};

export type OutcomeExpectancy = {
  risk_per_trade: number;
  measured_r_per_week: number;
  measured_r_per_week_net: number;      // NET (maliyet sonrası)
  expected_weekly_pct: number;
  expected_weekly_pct_net: number;      // NET beklenen haftalık %
  target_weekly_pct: number;   // 10.0
  needed_r_per_week: number;
  needed_ignores_cost?: boolean;        // gereken rakam maliyeti YOK SAYAR
  gap_note: string;
};

export type OutcomeRow = {
  ticker: string;
  setup: string;
  setup_label: string;
  status: "target" | "stop" | "time_exit" | string;
  realized_r: number | null;
  realized_pct: number | null;
  days_held: number | null;
  triggered_at: string | null;
  entry_date: string | null;
  exit_date: string | null;
};

export type SetupOutcomes = {
  as_of: string | null;
  cost_note?: string | null;             // "Maliyet TÜRETİLİR (saklanmaz)…"
  round_trip_cost_pct?: number | null;   // net'te kullanılan round-trip maliyet
  per_setup: Record<string, OutcomePerSetup>;
  overall: OutcomePerSetup;
  expectancy: OutcomeExpectancy;
  outcomes: OutcomeRow[];
};

export const fetchSetupOutcomes = () => apiGet<SetupOutcomes>("/api/setups/outcomes");

// --- Sektör & Makro bağlam (üst-aşağı katman) — PRIOR tilt, edge DEĞİL ---
export type MacroRegime = "risk_on" | "neutral" | "risk_off" | string;

export type UsdTry = {
  rate: number | null;
  ret_20d: number | null;
  trend: "up" | "down" | null;
  stale: boolean;
};

export type MacroContext = {
  regime: MacroRegime;
  regime_score: number;
  above_ema50: boolean;
  above_ema200: boolean;
  market_ret_5d: number;
  market_ret_20d: number;
  breadth_today: number;
  breadth_ema50: number;
  vol_20d: number;
  usdtry: UsdTry;
  n_names: number;
  notes: string[];
};

export type SectorRow = {
  sector: string;
  n: number;
  score: number;
  rank: number;
  trend: "lider" | "nötr" | "geride" | string;
  mom_20d: number | null;
  mom_60d: number | null;
  rel_strength_20d: number | null;
  above_ema50: number | null;
};

export type MarketContext = {
  available: boolean;
  message?: string;
  as_of?: string;
  macro?: MacroContext;
  sectors?: SectorRow[];
  sector_score?: Record<string, number>;
  tilt_cfg?: { base: number; span: number };
};

export type ContextAI = { available: boolean; comment?: string; message?: string; as_of?: string };

export const fetchContext = () => apiGet<MarketContext>("/api/context");
export const fetchContextAI = () => apiGet<ContextAI>("/api/context/ai");

// --- Otonom scheduler (arka planda veri/skor/haber/kalibrasyon) ----------
export type SchedulerJob = {
  id: string;
  name: string;
  scheduled: boolean;
  next_run: string | null;
  last: { last_run: string; ok: boolean; note: string } | null;
};

export type SchedulerStatus = {
  enabled: boolean;
  running: boolean;
  timezone: string | null;
  jobs: SchedulerJob[];
  event_study_state?: { last_run: string; ok: boolean; note: string } | null;
};

export const fetchScheduler = () => apiGet<SchedulerStatus>("/api/scheduler");
export const runSchedulerJob = (job: string) =>
  apiPost<{ started: boolean }>(`/api/scheduler/run/${job}`, {});

// --- Risk profili (temkinli/dengeli/agresif) ------------------------------
export type RiskStreak = { p_streak: number; drawdown: number };
export type RiskProfileMath = {
  assumed_hit_rate: number;
  n_trades_window: number;
  risk_per_trade: number;
  streaks: Record<string, RiskStreak>;   // "4" | "6" | "8"
};
export type RiskProfile = {
  label: string;
  desc: string;
  base_r: number;
  max_heat_pct: number;
  daily_stop_pct: number;
  weekly_dd_pct: number;
  math: RiskProfileMath;
};
export type RiskProfileOverview = {
  active: string;
  hit_rate_source: string;
  profiles: Record<string, RiskProfile>;
  note: string;
  applied_risk?: Record<string, number | null>;
};
export const fetchRiskProfile = () => apiGet<RiskProfileOverview>("/api/risk/profile");
export const putRiskProfile = (profile: string) =>
  apiPut<{ ok: boolean; active: string }>("/api/risk/profile", { profile });

// --- Faktör ağırlıkları + ölçülen kanıt (Ayarlar sekmesi) -----------------
export type FactorRow = {
  key: string;
  label: string;
  kind: "measured" | "prior" | string;
  weight: number;            // 0..1 (ham; motor wsum ile normalize eder)
  ic: number | null;         // ölçülen rank-IC (measured) ya da prior IC eşdeğeri
  t: number | null;          // Newey-West t (yalnız measured)
  hit: number | null;        // IC>0 oranı (yalnız measured)
  strong: boolean;           // t>=2 ya da CI sıfırı hariç → istatistiksel güçlü
};
export type FactorsResponse = {
  diagnostic_as_of: string | null;
  diagnostic_params?: { n_tickers?: number; horizon?: number } | null;
  weight_sum: number;
  factors: FactorRow[];
  note: string;
};
export const fetchFactors = () => apiGet<FactorsResponse>("/api/factors");
export const putFactorWeights = (weights: Record<string, number>) =>
  apiPut<{ ok: boolean; weights: Record<string, number> }>("/api/factors/weights", { weights });
export const measureFactors = () =>
  apiPost<{ ok: boolean; as_of: string }>("/api/factors/measure", {});

// --- Gemini API anahtarları (Ayarlar) — tam key ASLA dönmez, maskeli --------
export type AIProvider = "gemini" | "openai";
export type AIKeys = {
  count: number;
  keys: string[];              // maskeli ("AQ.••••1234")
  source: "db" | "env" | "none" | string;
  enabled: boolean;
  provider?: AIProvider;
  base_url?: string;           // OpenAI-uyumlu için
  model?: string;              // OpenAI-uyumlu için
  providers?: string[];
};
export const fetchAiKeys = () => apiGet<AIKeys>("/api/ai/keys");
export const putAiKeys = (
  keys: string[], provider?: AIProvider, base_url?: string, model?: string,
) => apiPut<AIKeys & { ok: boolean }>("/api/ai/keys", { keys, provider, base_url, model });

// --- Strateji karnesi (kanıt × canlı × harman; sunucu hesaplar) -----------
export type StrategyRow = {
  setup: string;
  label: string;
  verdict: SetupVerdict;
  status: "işlemde" | "izle" | "devre dışı" | string;
  expected_r_net: number;
  expected_r_src: string;
  study: {
    n: number | null;
    n_days?: number | null;   // bağımsız gün sayısı — az ise kümelenme/kırılganlık uyarısı
    mean_r_net: number | null;
    pf_net: number | null;
    hit_rate: number | null;
    by_regime?: Record<string, EvidenceRegimeSlice> | null;
  };
  live: { n_closed: number; n_pending: number; mean_r_net: number | null; hit_rate: number | null };
};
export type StrategiesResponse = {
  as_of: string | null;
  round_trip_cost_pct: number | null;
  count: number;
  strategies: StrategyRow[];
  note: string;
};
export const fetchStrategies = () => apiGet<StrategiesResponse>("/api/strategies");

// Bağlam tilt'i — backend sector_macro.context_tilt ile BİREBİR (PRIOR; backtest edilmedi).
// context = strength × (base+span·sektör/100) × (base+span·rejim/100); sektör/rejim yok → 50 (nötr).
export function contextTilt(
  strength: number,
  sectorScore: number | null | undefined,
  regimeScore: number | null | undefined,
  cfg?: { base: number; span: number },
): number {
  const base = cfg?.base ?? 0.85;
  const span = cfg?.span ?? 0.3;
  const sec = sectorScore == null ? 50 : sectorScore;
  const reg = regimeScore == null ? 50 : regimeScore;
  const factor = (base + (span * sec) / 100) * (base + (span * reg) / 100);
  return Math.max(0, Math.min(100, strength * factor));
}

// --- Trader-Brain: "dikkat çekenler" digest + analist tezleri + karne ----------
// digest = deterministik (KAP+setup+fiyat/hacim → materiality); narrative = grounded AI tezleri.
export type DigestItem = {
  ticker: string;
  materiality: number;
  reasons: string[];
  corporate: { type: string | null; direction: number | null; title: string | null }[];
  technical: { setup: string | null; strength: number | null }[];
  move: { ret: number; vol_z: number; close: number } | null;
  stance: { score: number | null; signal: string | null; gates: boolean; meets: boolean } | null;
};
export type DigestResponse = { as_of: string | null; count: number; items: DigestItem[] };
export const fetchDigest = () => apiGet<DigestResponse>("/api/digest");

export type ThesisDirection = "up" | "down" | "neutral" | "mixed";
export type ThesisStatus = "pending" | "hit" | "miss" | "neutral" | "no_data" | string;
export type AnalystNote = {
  id: number;
  created_at: string | null;
  as_of: string | null;
  scope_type: "macro" | "ticker" | string;
  scope: string;
  tickers: string[];
  direction: ThesisDirection | null;
  horizon_days: number | null;
  confidence: number | null;    // AI'ın beyan ettiği güven 0..1
  text: string | null;          // analist prozası (olgu/yorum ayrı)
  citations: { title: string; uri: string }[];   // kaynak linkleri (grounded ŞART)
  queries: string[];
  primary_ticker: string | null;
  status: ThesisStatus;         // karne: pending → hit/miss/neutral
  outcome_ret: number | null;
  graded_at: string | null;
};
export type ScoreBucket = { directional: number; hits: number; hit_rate: number | null };
export type ThesisScorecard = {
  total_notes: number;
  pending: number;
  graded: number;
  directional: number;
  hits: number;
  hit_rate: number | null;
  avg_ret_hit: number | null;
  avg_ret_miss: number | null;
  by_scope: { macro: ScoreBucket; ticker: ScoreBucket };
  note: string;
};
export type NarrativeResponse = {
  count: number;
  notes: AnalystNote[];
  scorecard: ThesisScorecard;
  disclaimer: string;
};
export const fetchNarrative = () => apiGet<NarrativeResponse>("/api/narrative");

// --- AI Brain: portföy-farkında değerlendirme (sistemin kendi sinyallerinin AI sentezi) -----
export type BrainStance = "koru" | "azalt" | "cik" | "izle";
export type BrainHolding = {
  ticker: string; qty: number; pnl_pct: number; score: number | null;
  signal: string | null; setup: string | null; stance: BrainStance;
};
export type BrainCandidate = {
  ticker: string; score: number; signal: string | null; sector: string | null; setup: string | null;
};
export type BrainFacts = {
  as_of: string | null; cash_try: number | null; cash_pct: number | null;
  open_heat_pct: number | null; total_try: number | null; pnl_total_pct: number | null;
  regime: { regime: string | null; regime_score: number | null; breadth_ema50: number | null };
  holdings: BrainHolding[]; candidates: BrainCandidate[];
};
export type BrainAI = {
  summary: string; cash_note: string;
  holdings: { ticker: string; stance: BrainStance; note: string }[];
  buys: { ticker: string; note: string }[];
};
export type BrainBrief = {
  generated_at: string | null;   // null = henüz AI üretilmedi (deterministik defter gösterilir)
  facts: BrainFacts;
  ai: BrainAI | null;            // null = kota/anahtar yok → yalnız deterministik duruş
  ai_stale?: boolean;           // true = AI yorumu önceki koşumdan (son tazeleme başarısız, korundu)
  disclaimer: string;
  note?: string;
};
export const fetchBrain = () => apiGet<BrainBrief>("/api/brain");
export const refreshBrain = () => apiPost<BrainBrief>("/api/brain/refresh", {});
export const BRAIN_STANCE_TR: Record<string, string> = {
  koru: "KORU", azalt: "AZALT", cik: "ÇIK", izle: "İZLE",
};

export const DIRECTION_TR: Record<string, string> = {
  up: "Yukarı", down: "Aşağı", neutral: "Nötr", mixed: "Karışık",
};
export const THESIS_STATUS_TR: Record<string, string> = {
  pending: "bekliyor", hit: "isabet", miss: "ıska", neutral: "nötr", no_data: "veri yok",
};

export type SizePreview = {
  ticker: string;
  equity: number;
  price: number;
  daily_close?: number;
  atr14?: number;
  atr_pct?: number;
  valid: boolean;
  reason?: string;
  qty: number;
  entry?: number;
  stop?: number;
  risk_amount?: number;
  risk_pct?: number;
  notional?: number;
  notional_pct?: number;
  heat_after?: number;
  fits_heat?: boolean;
  capped_by?: string | null;
};
