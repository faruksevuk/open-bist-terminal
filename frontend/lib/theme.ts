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
  // METİN için kırmızı: #A23B43 koyu zeminde 2.8:1, #6B1F2A 1.6:1 (AA=4.5:1 altı) —
  // GİRME rozeti fiilen okunmuyordu. Küçük punto kırmızı metin BUNU kullanır (5.2:1);
  // dolgu/kenarlık/bar gibi dekoratif kullanımlarda negative/oxblood kalır.
  negativeText: "#D06A74",
  warning: "#C08A3E",
} as const;

// Skor barı gradyanı: kırmızı(0) → amber(50) → yeşil(100)
export function scoreColor(score: number): string {
  if (score >= 60) return theme.positive;
  if (score >= 45) return theme.warning;
  return theme.negative;
}
