"""Pozisyon boyutlandırma — EDGE-ÖLÇEKLİ FRACTIONAL RISK (SCORING v0.2 §6).

Tek risk-çapası ATR; r'nin kendisi edge ile ölçeklenir (Kelly-on-shares çifte-
boyutlandırması YOK). edge_factor varsayılan 1.0 = saf ATR (edge ~100 canlı işleme
kadar ölçülmez). edge_factor_cap<1 → ölçülen edge yalnızca KISAR; kaldıraç >1 yok.
"""

from __future__ import annotations

import math


def position_size(
    equity: float,
    price: float,
    atr: float,
    risk_cfg: dict,
    open_heat_pct: float = 0.0,
    edge_factor: float = 1.0,
    plan_stop: float | None = None,
) -> dict:
    """plan_stop verilirse per-share risk SİNYALİN kendi stop'undan hesaplanır (denetim:
    sizing daima 2×ATR varsayıyordu → dar-stoplu setup'ta base_r'nin çok altında risk).
    plan_stop=None → eski davranış (k_atr×ATR)."""
    base_r = risk_cfg.get("base_r", 0.01)
    k = risk_cfg.get("k_atr", 2.0)
    cap = risk_cfg.get("edge_factor_cap", 0.90)
    max_name = risk_cfg.get("max_name_pct", 0.30)
    max_heat = risk_cfg.get("max_heat_pct", 0.06)

    if price <= 0 or equity <= 0:
        return {"qty": 0, "valid": False, "reason": "geçersiz fiyat/özsermaye"}
    if plan_stop is None and atr <= 0:
        return {"qty": 0, "valid": False, "reason": "geçersiz ATR"}

    # edge_factor yalnızca [floor, cap]; cap<1 → base_r'yi aşamaz (overbet yok)
    eff_factor = min(edge_factor, cap) if edge_factor != 1.0 else 1.0
    r_eff = base_r * eff_factor
    risk_amount = equity * r_eff
    if plan_stop is not None:
        per_share_risk = price - float(plan_stop)
        if per_share_risk <= 0:
            return {"qty": 0, "valid": False, "reason": "plan stop girişin üstünde"}
        stop = float(plan_stop)
    else:
        per_share_risk = k * atr
        stop = price - per_share_risk

    qty = math.floor(risk_amount / per_share_risk)
    capped_by = None
    # tek-isim notional tavanı
    max_notional = equity * max_name
    if qty * price > max_notional:
        qty = math.floor(max_notional / price)
        capped_by = "max_name"

    # TOPLAM ISI tavanı: kalan heat bütçesi qty'yi GERÇEKTEN kırpar (yalnız bayrak değil).
    # base_r (≈%1) << max_heat (≈%6) olduğundan açık-risk yokken bu tipik olarak bağlamaz.
    remaining_heat = max(0.0, max_heat - open_heat_pct)
    heat_qty = math.floor((equity * remaining_heat) / per_share_risk)
    if heat_qty < qty:
        qty = heat_qty
        capped_by = "heat"

    notional = qty * price
    realized_risk = qty * per_share_risk
    heat_contribution = realized_risk / equity if equity else 0.0
    fits_heat = (open_heat_pct + heat_contribution) <= max_heat + 1e-9

    return {
        "valid": qty > 0,
        "qty": int(qty),
        "entry": round(price, 4),
        "stop": round(stop, 4),
        "per_share_risk": round(per_share_risk, 4),
        "risk_amount": round(realized_risk, 2),
        "risk_pct": round(heat_contribution, 4),  # bu trade'in özsermayeye risk %'si
        "notional": round(notional, 2),
        "notional_pct": round(notional / equity, 4) if equity else 0.0,
        "heat_after": round(open_heat_pct + heat_contribution, 4),
        "fits_heat": bool(fits_heat),
        "capped_by": capped_by,
        "r_eff": round(r_eff, 5),
        "edge_active": edge_factor != 1.0,
    }
