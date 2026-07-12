// §14.1 tema token'ları (TS tarafı — Recharts/lightweight-charts gibi inline renk
// gereken yerler için). CSS değişkenleriyle birebir.
export const theme = {
  bg: "#0E0E10",
  surface: "#16161A",
  border: "#26262C",
  bone: "#E8E2D6",
  muted: "#9A958B",
  oxblood: "#6B1F2A",
  positive: "#5E8C6A",
  negative: "#A23B43",
  warning: "#C08A3E",
} as const;

// Skor barı gradyanı: kırmızı(0) → amber(50) → yeşil(100)
export function scoreColor(score: number): string {
  if (score >= 60) return theme.positive;
  if (score >= 45) return theme.warning;
  return theme.negative;
}
