"""SQLAlchemy modelleri — MASTER-BUILD-SPEC §6, SCORING v0.2 ile hizalı.

NOT (v0.2): `scores` tablosu T/M/N/R DEĞİL; kalite/oversold/sebep/stabilizasyon/
haber/risk-valfi kolonlarını tutar. `calibration_log` is_live + stop-farkında R
kolonları içerir; `relations` last_verified + expires_at; `kap_events` ve
`fundamentals` point-in-time için published_at taşır.
"""

from __future__ import annotations

import enum
from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# BigInteger PK: SQLite yalnız "INTEGER PRIMARY KEY"i rowid alias yapıp auto-increment eder
# ("BIGINT PRIMARY KEY" etmez). with_variant SQLite'ta INTEGER, Postgres'te BIGINT üretir.
BigIntPK = BigInteger().with_variant(Integer, "sqlite")


class UtcDateTime(TypeDecorator):
    """Tz-güvenli datetime: her zaman UTC-naive SAKLA, UTC-aware DÖNDÜR.

    SQLite `DateTime` naive döndürür → `datetime.now(timezone.utc)` ile Python
    karşılaştırmasında TypeError. Bu decorator o hata sınıfını tek yerde kapatır; kod
    zaten her yerde UTC yazıyor (datetime.now(timezone.utc)), dolayısıyla normalize güvenli.
    Postgres'te de tutarlı (UTC sakla/oku).
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value


# --- Enums --------------------------------------------------------------

class LockState(str, enum.Enum):
    none = "none"
    up = "up"
    down = "down"


class Horizon(str, enum.Enum):
    swing = "swing"
    daily = "daily"


class Signal(str, enum.Enum):
    strong_buy = "strong_buy"
    buy = "buy"
    hold = "hold"
    reduce = "reduce"
    sell = "sell"


class KapType(str, enum.Enum):
    temettu = "temettu"
    bedelli = "bedelli"
    bedelsiz = "bedelsiz"
    finansal_tablo = "finansal_tablo"
    pay_geri_alim = "pay_geri_alim"
    yonetici_islem = "yonetici_islem"
    onemli_sozlesme = "onemli_sozlesme"
    spk = "spk"
    diger = "diger"


class Side(str, enum.Enum):
    buy = "buy"
    sell = "sell"


class TradeSource(str, enum.Enum):
    telegram = "telegram"
    dashboard = "dashboard"
    manual = "manual"


class RelationType(str, enum.Enum):
    subsidiary = "subsidiary"
    supplier = "supplier"
    customer = "customer"
    peer = "peer"
    index_co = "index_co"
    ownership = "ownership"


class DecayProfile(str, enum.Enum):
    slow = "slow"
    medium = "medium"
    fast = "fast"
    exdate = "exdate"


# --- Tablolar -----------------------------------------------------------

class Security(Base):
    __tablename__ = "securities"

    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(128))
    sector: Mapped[str | None] = mapped_column(String(64))
    is_participation: Mapped[bool] = mapped_column(Boolean, default=False)  # katılım/faizsiz
    lot_size: Mapped[int] = mapped_column(Integer, default=1)
    listed_date: Mapped[date | None] = mapped_column(Date)
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)  # tedbir/işlem yasağı
    meta: Mapped[dict | None] = mapped_column(JSON)


class Snapshot(Base):
    """15dk gecikmeli akış; aynı zamanda intraday tape kaynağı."""

    __tablename__ = "snapshots"
    __table_args__ = (UniqueConstraint("ticker", "as_of", name="uq_snapshot_ticker_asof"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    as_of: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    last: Mapped[float | None] = mapped_column(Float)
    bid: Mapped[float | None] = mapped_column(Float)
    ask: Mapped[float | None] = mapped_column(Float)
    change_pct: Mapped[float | None] = mapped_column(Float)
    day_low: Mapped[float | None] = mapped_column(Float)
    day_high: Mapped[float | None] = mapped_column(Float)
    vwap: Mapped[float | None] = mapped_column(Float)
    vol_tl: Mapped[float | None] = mapped_column(Float)
    vol_lot: Mapped[float | None] = mapped_column(Float)
    limit_up: Mapped[float | None] = mapped_column(Float)
    limit_down: Mapped[float | None] = mapped_column(Float)
    locked: Mapped[LockState] = mapped_column(Enum(LockState, name="lock_state"), default=LockState.none)


class DailyBar(Base):
    __tablename__ = "daily_bars"
    __table_args__ = (UniqueConstraint("ticker", "date", name="uq_dailybar_ticker_date"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    adj_close: Mapped[float | None] = mapped_column(Float)  # v0.2 §9.2: indikatörler ADJUSTED seride


class Fundamental(Base):
    __tablename__ = "fundamentals"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    as_of: Mapped[datetime] = mapped_column(UtcDateTime)
    published_at: Mapped[datetime | None] = mapped_column(UtcDateTime)  # v0.2 §9.1 PIT
    pe: Mapped[float | None] = mapped_column(Float)
    pb: Mapped[float | None] = mapped_column(Float)
    mcap: Mapped[float | None] = mapped_column(Float)
    net_profit: Mapped[float | None] = mapped_column(Float)
    free_float_pct: Mapped[float | None] = mapped_column(Float)
    foreign_pct: Mapped[float | None] = mapped_column(Float)
    volatility: Mapped[float | None] = mapped_column(Float)
    piotroski_f: Mapped[int | None] = mapped_column(Integer)  # 0-9, koddan (PIT)
    accrual_ratio: Mapped[float | None] = mapped_column(Float)
    raw: Mapped[dict | None] = mapped_column(JSON)


class KapEvent(Base):
    __tablename__ = "kap_events"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    tickers: Mapped[list[str]] = mapped_column(JSON)
    published_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    type: Mapped[KapType] = mapped_column(Enum(KapType, name="kap_type"))
    title: Mapped[str | None] = mapped_column(Text)
    raw_url: Mapped[str | None] = mapped_column(Text)
    interpreted: Mapped[bool] = mapped_column(Boolean, default=False)
    direction: Mapped[float | None] = mapped_column(Float)  # -1..1 (LLM ton/yön)
    magnitude: Mapped[float | None] = mapped_column(Float)  # 0..1
    confidence: Mapped[float | None] = mapped_column(Float)  # 0..1
    mechanism: Mapped[str | None] = mapped_column(Text)
    duration_days: Mapped[int | None] = mapped_column(Integer)
    decay_profile: Mapped[DecayProfile | None] = mapped_column(Enum(DecayProfile, name="decay_profile"))
    effective_until: Mapped[datetime | None] = mapped_column(UtcDateTime)
    # v0.2 §7: earnings_surprise KODDAN hesaplanan SUE (LLM emit etmez)
    earnings_surprise: Mapped[float | None] = mapped_column(Float)
    second_order: Mapped[list | None] = mapped_column(JSON)
    thread_id: Mapped[str | None] = mapped_column(String(64), index=True)


class KapOutcome(Base):
    """KAP yorum KARNESİ — AI'ın direction çağrısını gerçekleşen getiriyle notlar.

    DENETİM BULGUSU: direction×magnitude×confidence skora ±20 puana kadar giriyordu ama
    isabeti hiç ölçülmüyordu (21/21-refüte dürüstlük çizgisine aykırı kör nokta).
    kap_grade.evaluate_kap_outcomes doldurur; thesis_grade ile aynı notlama (adjusted
    çift, ±%1 nötr band). Bir olaya en fazla BİR sonuç (event_id UNIQUE — upsert).
    NOT: yeni TABLO (mevcut modele kolon eklenmez — create_all ALTER yapmaz).
    """

    __tablename__ = "kap_outcomes"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_kap_outcome_event"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(BigInteger, index=True)   # kap_events.id (mantıksal fk)
    ticker: Mapped[str] = mapped_column(String(16), index=True)     # notlanan isim (tickers[0])
    type: Mapped[str | None] = mapped_column(String(32), index=True)
    direction: Mapped[float | None] = mapped_column(Float)          # olayın AI yönü (-1..1)
    horizon_days: Mapped[int] = mapped_column(Integer, default=5)
    entry_close: Mapped[float | None] = mapped_column(Float)        # yayın günü adjusted kapanış
    outcome_ret: Mapped[float | None] = mapped_column(Float)        # +N bar adjusted getiri
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # hit|miss|neutral|pending|no_data
    graded_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class NewsItem(Base):
    __tablename__ = "news_items"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    source: Mapped[str | None] = mapped_column(String(64))
    url: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    tickers: Mapped[list[str]] = mapped_column(JSON)
    interpreted: Mapped[bool] = mapped_column(Boolean, default=False)
    direction: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    mechanism: Mapped[str | None] = mapped_column(Text)
    thread_id: Mapped[str | None] = mapped_column(String(64), index=True)
    effective_until: Mapped[datetime | None] = mapped_column(UtcDateTime)


class Score(Base):
    """v0.2 tek model: kalite/oversold/sebep/stabilizasyon/haber/risk-valfi."""

    __tablename__ = "scores"
    __table_args__ = (
        UniqueConstraint("ticker", "as_of", "horizon", name="uq_score_ticker_asof_horizon"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    as_of: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    horizon: Mapped[Horizon] = mapped_column(Enum(Horizon, name="horizon"))
    score: Mapped[float | None] = mapped_column(Float)  # 0-100
    passed_gates: Mapped[bool] = mapped_column(Boolean, default=False)
    signal: Mapped[Signal | None] = mapped_column(Enum(Signal, name="signal"))
    # Alt-skorlar (v0.2 §3)
    sub_quality: Mapped[float | None] = mapped_column(Float)      # mutlak 0-100
    sub_oversold: Mapped[float | None] = mapped_column(Float)     # evren-içi göreli 0-100
    sub_cause: Mapped[float | None] = mapped_column(Float)        # mutlak 0-100 (EN KRİTİK)
    sub_stab: Mapped[float | None] = mapped_column(Float)         # evren-içi göreli 0-100
    news_pos: Mapped[float | None] = mapped_column(Float)         # 0..+12 (çarpım içinde)
    news_neg: Mapped[float | None] = mapped_column(Float)         # -20..0 (çarpım dışında)
    risk_governor: Mapped[float | None] = mapped_column(Float)    # 0.2..1.0
    meets_absolute_threshold: Mapped[bool] = mapped_column(Boolean, default=False)
    reasoning: Mapped[dict | None] = mapped_column(JSON)         # kod şablonu yazar, LLM değil


class SetupSignal(Base):
    """Olay-tetikli setup sinyali (SETUPS v0.1). Kısa-vade işlem katmanı.

    Her (ticker, setup, triggered_at) benzersiz — aynı gün aynı setup tek satır.
    Canlı tarama (setup_scan.scan_universe) upsert eder; valid_until geçmişte kalınca
    active=False yapılır. Kanıt (verdict) config 'setup_evidence'den okunur (burada değil).
    """

    __tablename__ = "setup_signals"
    __table_args__ = (
        UniqueConstraint("ticker", "setup", "triggered_at", name="uq_setup_ticker_setup_date"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)  # securities'e mantıksal fk (Score gibi)
    setup: Mapped[str] = mapped_column(String(32), index=True)
    triggered_at: Mapped[date] = mapped_column(Date, index=True)  # sinyal barının tarihi
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, server_default=func.now())
    strength: Mapped[float | None] = mapped_column(Float)  # 0-100
    entry_ref: Mapped[float | None] = mapped_column(Float)  # son close (referans giriş)
    stop: Mapped[float | None] = mapped_column(Float)
    target: Mapped[float | None] = mapped_column(Float)
    time_exit_days: Mapped[int | None] = mapped_column(Integer)
    valid_until: Mapped[date | None] = mapped_column(Date, index=True)
    context: Mapped[dict | None] = mapped_column(JSON)  # tetikleyen indikatör değerleri
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class SetupOutcome(Base):
    """Canlı sinyal sonuç-takibi (OOS) — her SetupSignal'a en fazla BİR sonuç.

    scan_universe bir sinyal ürettikten SONRA, evaluate_outcomes (setup_outcomes.py) o
    sinyalin tetik-sonrası barlarını event_study ile AYNI stop-önce konvansiyonuyla yürür:
    giriş = tetik barından SONRAKİ ilk barın OPEN'ı; stop-önce; zaman-çıkışı entry+time_exit
    barının close'u. Böylece canlı (out-of-sample) dürüst beklenti/isabet birikir.

    signal_id UNIQUE (upsert) — bir sinyale bir sonuç satırı. status kapanana dek 'pending';
    kapanınca 'target'/'stop'/'time_exit' (giriş barı hiç oluşmadıysa 'no_entry').
    NOT: yeni TABLO (mevcut modele kolon eklenmez — create_all ALTER yapmaz).
    """

    __tablename__ = "setup_outcomes"
    __table_args__ = (
        UniqueConstraint("signal_id", name="uq_setup_outcome_signal"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(BigInteger, index=True)  # setup_signals.id (mantıksal fk)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    setup: Mapped[str] = mapped_column(String(32), index=True)
    triggered_at: Mapped[date] = mapped_column(Date, index=True)  # sinyal barının tarihi
    entry_date: Mapped[date | None] = mapped_column(Date)      # tetik-sonrası ilk bar (giriş)
    entry_price: Mapped[float | None] = mapped_column(Float)   # o barın OPEN'ı
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    exit_date: Mapped[date | None] = mapped_column(Date)
    exit_price: Mapped[float | None] = mapped_column(Float)
    realized_r: Mapped[float | None] = mapped_column(Float)    # R-katı (planlı stop farkına göre)
    realized_pct: Mapped[float | None] = mapped_column(Float)  # (exit-entry)/entry
    days_held: Mapped[int | None] = mapped_column(Integer)
    evaluated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, server_default=func.now(), onupdate=func.now()
    )


class Trade(Base):
    """LEDGER — append-only. Manuel Midas işlemlerinin tek doğruluk kaynağı."""

    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    side: Mapped[Side] = mapped_column(Enum(Side, name="side"))
    qty: Mapped[int] = mapped_column(Integer)
    price: Mapped[float] = mapped_column(Float)
    executed_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    fees: Mapped[float] = mapped_column(Float, default=0.0)
    note: Mapped[str | None] = mapped_column(Text)
    decision_snapshot: Mapped[dict | None] = mapped_column(JSON)  # skor/reasoning/portföy/ATR-stop
    source: Mapped[TradeSource] = mapped_column(Enum(TradeSource, name="trade_source"))
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    fx_try_usd_at_exec: Mapped[float | None] = mapped_column(Float)


class Position(Base):
    """Türetilir, ledger'dan reconcile edilir."""

    __tablename__ = "positions"

    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    qty: Mapped[int] = mapped_column(Integer, default=0)
    avg_cost: Mapped[float | None] = mapped_column(Float)
    stop: Mapped[float | None] = mapped_column(Float)
    opened_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    last_reconciled_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    as_of: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    cash: Mapped[float | None] = mapped_column(Float)
    invested: Mapped[float | None] = mapped_column(Float)
    total_try: Mapped[float | None] = mapped_column(Float)
    total_usd: Mapped[float | None] = mapped_column(Float)       # v0.2 §6.3
    total_real: Mapped[float | None] = mapped_column(Float)      # enflasyon-ayarlı
    day_pct: Mapped[float | None] = mapped_column(Float)
    week_pct: Mapped[float | None] = mapped_column(Float)
    month_pct: Mapped[float | None] = mapped_column(Float)
    open_heat_pct: Mapped[float | None] = mapped_column(Float)
    peak_equity: Mapped[float | None] = mapped_column(Float)
    drawdown_pct: Mapped[float | None] = mapped_column(Float)


class Config(Base):
    """Tüm ağırlık/parametre/prompt — SCORING v0.2 §10 seed."""

    __tablename__ = "config"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, server_default=func.now(), onupdate=func.now()
    )


class CalibrationLog(Base):
    """Backtest + canlı shadow log. v0.2: is_live ayrımı + stop-farkında R."""

    __tablename__ = "calibration_log"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    score: Mapped[float | None] = mapped_column(Float)
    horizon: Mapped[Horizon] = mapped_column(Enum(Horizon, name="horizon"))
    as_of: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    fwd_return_5d: Mapped[float | None] = mapped_column(Float)   # RANKING için (sizing için değil)
    replay_r: Mapped[float | None] = mapped_column(Float)        # v0.2 §9.3 stop-farkında R-multiple
    setup_tag: Mapped[str | None] = mapped_column(String(32))    # v0.2 §6.1 setup-bazlı p,b
    is_live: Mapped[bool] = mapped_column(Boolean, default=False, index=True)  # v0.2 §9.7
    realized: Mapped[bool] = mapped_column(Boolean, default=False)


class Relation(Base):
    """İkinci-derece grafik. v0.2 §8: last_verified + expires_at."""

    __tablename__ = "relations"
    __table_args__ = (
        UniqueConstraint("ticker_a", "ticker_b", "relation", name="uq_relation_pair"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    ticker_a: Mapped[str] = mapped_column(String(16), index=True)
    ticker_b: Mapped[str] = mapped_column(String(16), index=True)
    relation: Mapped[RelationType] = mapped_column(Enum(RelationType, name="relation_type"))
    weight: Mapped[float] = mapped_column(Float, default=0.0)
    mechanism: Mapped[str | None] = mapped_column(Text)
    last_verified: Mapped[datetime | None] = mapped_column(UtcDateTime)
    expires_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class Macro(Base):
    __tablename__ = "macro"

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    usdtry: Mapped[float | None] = mapped_column(Float)
    cpi_yoy: Mapped[float | None] = mapped_column(Float)
    policy_rate: Mapped[float | None] = mapped_column(Float)
    next_cbrt_meeting: Mapped[date | None] = mapped_column(Date)
    next_cpi_date: Mapped[date | None] = mapped_column(Date)


class AnalystNote(Base):
    """AI "Trader-Brain" analist tezi — grounded (Google Search + KAP) NİTELİKSEL yorum.

    narrative engine (app/llm/narrative.py) üretir: digest'ten konu/hisse seçer, GERÇEK güncel
    olayları (grounded) okur, analist tezi yazar. DÜRÜSTLÜK: her tez kaynaklı (citations); metin
    olgu/yorum ayrımı taşır; sayı (skor/boyut) BURADAN GELMEZ. Çağrılar sonra notlanır
    (thesis_grade.evaluate_theses) → dürüst karne. YATIRIM TAVSİYESİ DEĞİL.
    """

    __tablename__ = "analyst_notes"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, server_default=func.now(), index=True)
    as_of: Mapped[date] = mapped_column(Date, index=True)          # digest günü (tezin dayandığı gün)
    scope_type: Mapped[str] = mapped_column(String(8))             # "macro" | "ticker"
    scope: Mapped[str] = mapped_column(String(48), index=True)     # tema adı ya da ticker
    tickers: Mapped[list | None] = mapped_column(JSON)             # etkilenen hisseler [...]
    direction: Mapped[str | None] = mapped_column(String(8))       # up|down|neutral|mixed
    horizon_days: Mapped[int | None] = mapped_column(Integer)      # tezin beyan ettiği vade (5/30)
    confidence: Mapped[float | None] = mapped_column(Float)        # AI'ın beyan ettiği güven 0..1
    text: Mapped[str | None] = mapped_column(Text)                 # analist prozası (olgu/yorum ayrı)
    citations: Mapped[list | None] = mapped_column(JSON)           # [{title, uri}] — kaynak ŞART
    queries: Mapped[list | None] = mapped_column(JSON)             # AI'ın web arama sorguları
    # --- karne (thesis_grade sonradan doldurur; grounded değilken satır hiç yazılmaz) ---
    primary_ticker: Mapped[str | None] = mapped_column(String(16), index=True)  # notlanan isim (tickers[0])
    entry_close: Mapped[float | None] = mapped_column(Float)       # tez anındaki referans kapanış
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)  # pending|hit|miss|neutral|no_data
    outcome_ret: Mapped[float | None] = mapped_column(Float)       # vade sonu gerçekleşen getiri
    graded_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
