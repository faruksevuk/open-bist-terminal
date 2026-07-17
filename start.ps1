# Open BIST Terminal - tek komut baslatici (Windows).
# Calistir: cift-tikla start.bat  |  powershell -ExecutionPolicy Bypass -File start.ps1
# Gelistirici modu (hot-reload): start.ps1 -Dev  (ya da dev.bat)
# SAF ASCII olmali (PowerShell 5.1 BOM'suz dosyayi cp1252 okur; TR harf/tire bozar -> parse error).
# SQLite tabanli, sifir servis. Tekrar-calistirilabilir (once eski surecleri durdurur).
# VARSAYILAN ARTIK PROD: next build + next start ve uvicorn --reload'suz. Eski davranis
# (herkese dev sunucusu) performans sikayetinin ana nedeniydi.
param([switch]$Dev)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"
$py = Join-Path $backend ".venv\Scripts\python.exe"

function Say($m, $c = "Cyan") { Write-Host $m -ForegroundColor $c }

Say "Open BIST Terminal baslatiliyor..." "Green"

# --- 0) Port cakismasini onle: :8000 / :4000 uzerindeki eski surecleri durdur ---
foreach ($port in @(8000, 4000)) {
    $pids = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($procId in $pids) {
        try { Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue; Say "  Port $port bosaltildi (onceki surec durduruldu)." "DarkGray" } catch {}
    }
}

# --- 1) Backend venv + bagimliliklar (ilk calistirma) ---
if (-not (Test-Path $py)) {
    Say "[1/3] Python venv olusturuluyor (ilk calistirma)..."
    python -m venv (Join-Path $backend ".venv")
    Say "  Bagimliliklar kuruluyor (birkac dakika)..."
    Push-Location $backend
    & $py -m pip install -q --upgrade pip
    & $py -m pip install -q -e ".[dev]"
    Pop-Location
} else {
    Say "[1/3] Backend venv mevcut."
}

# --- 2) DB tablolari + seed config (SQLite: backend\bist.db) ---
Say "[2/3] DB tablolari + seed config..."
Push-Location $backend
& $py "scripts\setup_db.py"
Pop-Location

# --- 3) Backend (yeni pencere, :8000) ---
Say "[3/3] Backend baslatiliyor (yeni pencere, http://localhost:8000)..."
$uvicornArgs = "-m uvicorn app.main:app --port 8000"
if ($Dev) { $uvicornArgs = "$uvicornArgs --reload" }
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "cd '$backend'; & '$py' $uvicornArgs"
)

# --- Frontend bagimliliklari (ilk sefer) + baslat (yeni pencere, :4000) ---
if (-not (Test-Path (Join-Path $frontend "node_modules"))) {
    Say "  Frontend bagimliliklari kuruluyor (ilk calistirma, birkac dakika)..."
    Push-Location $frontend; npm install; Pop-Location
}

if ($Dev) {
    Say "Frontend DEV modda baslatiliyor (hot-reload, http://localhost:4000)..."
    Start-Process powershell -ArgumentList @(
        "-NoExit", "-Command",
        "cd '$frontend'; npm run dev"
    )
} else {
    # PROD: build gerekliyse (ilk sefer ya da kod build'den yeniyse) once derle, sonra start.
    $buildId = Join-Path $frontend ".next\BUILD_ID"
    $needBuild = -not (Test-Path $buildId)
    if (-not $needBuild) {
        $builtAt = (Get-Item $buildId).LastWriteTime
        $src = Get-ChildItem -Recurse -File (Join-Path $frontend "app"), (Join-Path $frontend "lib") -ErrorAction SilentlyContinue |
               Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if ($src -and $src.LastWriteTime -gt $builtAt) { $needBuild = $true }
    }
    if ($needBuild) {
        Say "  Frontend derleniyor (production build, 1-2 dk; sonraki acilislar hizli)..."
        Push-Location $frontend
        npm run build
        if ($LASTEXITCODE -ne 0) { Pop-Location; throw "Frontend build basarisiz - cikti yukarida." }
        Pop-Location
    } else {
        Say "  Frontend build guncel."
    }
    Say "Frontend baslatiliyor (production, http://localhost:4000)..."
    Start-Process powershell -ArgumentList @(
        "-NoExit", "-Command",
        "cd '$frontend'; npm run start"
    )
}

Say ""
Say "HAZIR." "Green"
Say "  Dashboard : http://localhost:4000" "Green"
Say "  API/docs  : http://localhost:8000/docs" "Green"
Say ""
Say "AI istersen: Dashboard > Ayarlar > AI API Anahtarlari (istege bagli)." "DarkGray"
Say "Veri bos ise ILK doldurma: backend\.venv\Scripts\python.exe backend\scripts\fetch_history.py" "Yellow"
Say "  (sonra skorlama: refresh.bat - refresh.bat tek basina bar CEKMEZ)" "Yellow"
Say "Gelistirici modu (hot-reload): dev.bat" "DarkGray"
Say "Durdurmak icin: stop.bat" "Yellow"
