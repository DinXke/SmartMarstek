#Requires -Version 5.1
$ErrorActionPreference = 'Stop'

$RootDir     = $PSScriptRoot
$BackendDir  = Join-Path $RootDir "backend"
$FrontendDir = Join-Path $RootDir "frontend"

function Write-Ok   { param($m) Write-Host "[OK]   $m" -ForegroundColor Green  }
function Write-Info { param($m) Write-Host "[INFO] $m" -ForegroundColor Yellow }
function Write-Fail { param($m) Write-Host "[ERROR] $m" -ForegroundColor Red   }

Write-Host ""
Write-Host "  ==============================================" -ForegroundColor Cyan
Write-Host "    Marstek Dashboard Installer (Windows)"       -ForegroundColor Cyan
Write-Host "  ==============================================" -ForegroundColor Cyan
Write-Host ""

# ---------- Python -----------------------------------------------------------
$python = $null
foreach ($cmd in @('python', 'python3', 'py')) {
    try {
        $v = & $cmd --version 2>&1
        if ($LASTEXITCODE -eq 0 -and "$v" -match '3\.\d+') {
            $python = $cmd
            break
        }
    } catch { }
}
if (-not $python) {
    Write-Fail "Python 3 not found."
    Write-Host "  Install from: https://www.python.org/downloads/"
    Write-Host "  Check 'Add Python to PATH' during install."
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Ok "Python $((& $python --version 2>&1).ToString().Trim()) ($python)"

# ---------- Helpers ----------------------------------------------------------
function Refresh-NodePath {
    $machine = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
    $user    = [System.Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = $machine + ";" + $user + ";C:\Program Files\nodejs;" + $env:APPDATA + "\npm"
}

function Test-Node {
    try { $null = & node --version 2>&1; return ($LASTEXITCODE -eq 0) }
    catch { return $false }
}

function Test-Npm {
    try { $null = & npm --version 2>&1; return ($LASTEXITCODE -eq 0) }
    catch { return $false }
}

# ---------- Node.js ----------------------------------------------------------
Refresh-NodePath

if (-not (Test-Node)) {
    Write-Info "Node.js not found. Installing via winget (a UAC prompt may appear)..."
    try {
        $wingetArgs = @('install', 'OpenJS.NodeJS.LTS', '--silent', '--accept-package-agreements', '--accept-source-agreements')
        & winget @wingetArgs
    } catch {
        Write-Fail "winget failed: $_"
        Write-Host "  Install Node.js 18+ manually: https://nodejs.org/"
        Read-Host "Press Enter to exit"
        exit 1
    }
    Refresh-NodePath
    if (-not (Test-Node)) {
        Write-Fail "Node.js installed but not visible yet. Open a new terminal and re-run install.bat."
        Read-Host "Press Enter to exit"
        exit 1
    }
    Write-Ok "Node.js installed successfully."
}

Write-Ok "Node.js $((& node --version 2>&1).ToString().Trim())"

if (-not (Test-Npm)) {
    Write-Fail "npm not found. Please reinstall Node.js from https://nodejs.org/"
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Ok "npm $((& npm --version 2>&1).ToString().Trim())"
Write-Host ""

# ---------- Step 1 : Python venv ---------------------------------------------
Write-Host "[1/4] Creating Python virtual environment..."
Push-Location $BackendDir
if (Test-Path "venv") {
    Write-Host "      Already exists, skipping."
} else {
    & $python -m venv venv
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "venv creation failed."
        Pop-Location; Read-Host "Press Enter to exit"; exit 1
    }
}

# ---------- Step 2 : pip install ---------------------------------------------
Write-Host "[2/4] Installing Python dependencies..."
& "venv\Scripts\pip.exe" install -r requirements.txt -q --disable-pip-version-check
if ($LASTEXITCODE -ne 0) {
    Write-Fail "pip install failed. Check your internet connection."
    Pop-Location; Read-Host "Press Enter to exit"; exit 1
}
Write-Host "      Done."
Pop-Location

# ---------- Step 3 : npm install ---------------------------------------------
Write-Host "[3/4] Installing frontend dependencies..."
Push-Location $FrontendDir
& npm install --no-fund --no-audit
if ($LASTEXITCODE -ne 0) {
    Write-Fail "npm install failed. Check your internet connection."
    Pop-Location; Read-Host "Press Enter to exit"; exit 1
}
Write-Host "      Done."

# ---------- Step 4 : npm build -----------------------------------------------
Write-Host "[4/4] Building frontend..."
& npm run build
if ($LASTEXITCODE -ne 0) {
    Write-Fail "Frontend build failed."
    Pop-Location; Read-Host "Press Enter to exit"; exit 1
}
Write-Host "      Done."
Pop-Location

# ---------- Done -------------------------------------------------------------
Write-Host ""
Write-Host "  ==============================================" -ForegroundColor Green
Write-Host "    Installation complete!" -ForegroundColor Green
Write-Host ""
Write-Host "    Run start.bat to launch the dashboard."
Write-Host "    Dashboard opens at: http://localhost:5000"
Write-Host "  ==============================================" -ForegroundColor Green
Write-Host ""

$ans = Read-Host "Launch the dashboard now? (y/n)"
if ($ans -match '^[Yy]') {
    Start-Process powershell -ArgumentList "-ExecutionPolicy Bypass -File `"$RootDir\start.ps1`""
} else {
    Read-Host "Press Enter to exit"
}
