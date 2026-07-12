#!/usr/bin/env bash
# Open BIST Terminal - :8000 (backend) + :4000 (frontend) sureclerini durdur (macOS/Linux).
# Windows: stop.bat. Veri backend/bist.db dosyasinda korunur.
set -u
for port in 8000 4000; do
  pids="$(lsof -ti "tcp:$port" 2>/dev/null || true)"
  if [ -n "$pids" ]; then
    kill $pids 2>/dev/null || true
    echo "Port $port durduruldu (pid: $pids)."
  else
    echo "Port $port'ta dinleyen yok."
  fi
done
echo "Backend/frontend durduruldu. Veri (bist.db) korundu."
