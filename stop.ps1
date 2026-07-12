# Open BIST Terminal - backend (:8000) + frontend (:4000) sureclerini durdur (Windows).
# Veri backend\bist.db dosyasinda korunur. SAF ASCII (PS 5.1 BOM'suz -> cp1252).
$ErrorActionPreference = "SilentlyContinue"

foreach ($port in @(8000, 4000)) {
    $pids = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique
    if ($pids) {
        foreach ($procId in $pids) {
            try { Stop-Process -Id $procId -Force; Write-Host "Port $port durduruldu (pid $procId)." -ForegroundColor Yellow } catch {}
        }
    } else {
        Write-Host "Port $port'ta dinleyen yok." -ForegroundColor DarkGray
    }
}
Write-Host "Backend/frontend durduruldu. Veri (bist.db) korundu." -ForegroundColor Green
