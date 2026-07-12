#!/usr/bin/env bash
# Open BIST Terminal - tek komut baslatici (macOS/Linux).
# Kullanim:  bash start.sh   (Windows: start.bat)
# SQLite tabanli, sifir servis. Backend :8000 (arka plan), frontend :4000 (on plan).
# Ctrl-C ikisini de kapatir. Tekrar-calistirilabilir (once :8000/:4000 surecleri durdurulur).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"; FRONTEND="$ROOT/frontend"
VENV="$BACKEND/.venv"; PY="$VENV/bin/python"

say() { printf '\033[36m%s\033[0m\n' "$1"; }

command -v python3 >/dev/null 2>&1 || { echo "HATA: python3 gerekli (>=3.12)."; exit 1; }
command -v npm     >/dev/null 2>&1 || { echo "HATA: npm gerekli (Node 20+)."; exit 1; }

say "Open BIST Terminal baslatiliyor..."

# 0) Port cakismasini onle
for port in 8000 4000; do
  pids="$(lsof -ti "tcp:$port" 2>/dev/null || true)"
  if [ -n "$pids" ]; then kill $pids 2>/dev/null || true; say "  Port $port bosaltildi."; fi
done

# 1) venv + backend bagimliliklari (ilk sefer)
if [ ! -x "$PY" ]; then
  say "[1/3] Python venv olusturuluyor (ilk calistirma)..."
  python3 -m venv "$VENV"
  say "  Bagimliliklar kuruluyor (birkac dakika)..."
  "$PY" -m pip install -q --upgrade pip
  ( cd "$BACKEND" && "$PY" -m pip install -q -e ".[dev]" )
else
  say "[1/3] Backend venv mevcut."
fi

# 2) DB tablolari + seed
say "[2/3] DB tablolari + seed config..."
( cd "$BACKEND" && "$PY" scripts/setup_db.py )

# frontend bagimliliklari (ilk sefer)
if [ ! -d "$FRONTEND/node_modules" ]; then
  say "  Frontend bagimliliklari kuruluyor (ilk calistirma)..."
  ( cd "$FRONTEND" && npm install )
fi

# 3) Backend arka planda (:8000)
say "[3/3] Backend baslatiliyor (http://localhost:8000)..."
( cd "$BACKEND" && exec "$PY" -m uvicorn app.main:app --reload ) &
BACKEND_PID=$!
cleanup() { echo; say "Kapatiliyor..."; kill "$BACKEND_PID" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

say ""
say "HAZIR.  Dashboard: http://localhost:4000  |  API: http://localhost:8000/docs"
say "AI istersen: Dashboard > Ayarlar > AI API Anahtarlari (istege bagli)."
say "Durdurmak: Ctrl-C (ya da baska pencerede: bash stop.sh)"
say ""

# Frontend on planda (:4000) - kapaninca trap backend'i de durdurur
( cd "$FRONTEND" && npm run dev )
