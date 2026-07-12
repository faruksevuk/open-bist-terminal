"use client";

import { useCallback, useEffect, useState } from "react";

// Dashboard'da kullanıcının küçültebildiği / gizleyebildiği bölümler (görünüm tercihi).
export type SectionId = "market" | "strategies" | "setups" | "opportunities";

export const DASHBOARD_SECTIONS: { id: SectionId; label: string }[] = [
  { id: "market", label: "Piyasa & Sektör Durumu" },
  { id: "strategies", label: "Strateji Karnesi" },
  { id: "setups", label: "Setup Fırsatları" },
  { id: "opportunities", label: "Fırsatlar (faktör skoru)" },
];

type Prefs = { collapsed: Record<string, boolean>; hidden: Record<string, boolean> };
const KEY = "obt.dashboard.prefs.v1";
const EMPTY: Prefs = { collapsed: {}, hidden: {} };

function load(): Prefs {
  if (typeof window === "undefined") return EMPTY;
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return EMPTY;
    const p = JSON.parse(raw);
    return { collapsed: p.collapsed ?? {}, hidden: p.hidden ?? {} };
  } catch { return EMPTY; }
}

export type DashboardPrefs = {
  ready: boolean;
  isCollapsed: (id: SectionId) => boolean;
  isHidden: (id: SectionId) => boolean;
  toggleCollapse: (id: SectionId) => void;
  setHidden: (id: SectionId, v: boolean) => void;
};

// localStorage-tabanlı görünüm tercihleri (dashboard + Ayarlar aynı anahtarı paylaşır).
// SSR'de localStorage yok → mount sonrası yüklenir (ready flag'i ile).
export function useDashboardPrefs(): DashboardPrefs {
  const [prefs, setPrefs] = useState<Prefs>(EMPTY);
  const [ready, setReady] = useState(false);

  useEffect(() => { setPrefs(load()); setReady(true); }, []);

  const persist = useCallback((p: Prefs) => {
    setPrefs(p);
    try { window.localStorage.setItem(KEY, JSON.stringify(p)); } catch { /* kota/erişim yok — yoksay */ }
  }, []);

  return {
    ready,
    isCollapsed: (id) => !!prefs.collapsed[id],
    isHidden: (id) => !!prefs.hidden[id],
    toggleCollapse: (id) =>
      persist({ ...prefs, collapsed: { ...prefs.collapsed, [id]: !prefs.collapsed[id] } }),
    setHidden: (id, v) => persist({ ...prefs, hidden: { ...prefs.hidden, [id]: v } }),
  };
}
