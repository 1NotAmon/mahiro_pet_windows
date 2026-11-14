<#
.SYNOPSIS
    真尋桌寵 / Desktop Pet - Windows 一鍵安裝 + 啟動
.DESCRIPTION
    建立 .venv 虛擬環境、安裝相依套件，然後啟動桌寵。
    已安裝過的話，這個腳本會直接啟動（不會重複安裝）。
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File setup.ps1
    powershell -ExecutionPolicy Bypass -File setup.ps1 -NoLaunch
    powershell -ExecutionPolicy Bypass -File setup.ps1 -ForceReinstall
#>
[CmdletBinding()]
param(
    [switch]$NoLaunch,          # 只安裝，不啟動
    [switch]$WithConsole,       # 啟動時保留終端機（看 log）
    [switch]$ForceReinstall,    # 刪除並重建 .venv
    [string]$Python = ""        # 自行指定 python 執行檔路徑
)

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$Root  = Split-Path -Parent $MyInvocation.MyCommand.Definition
$Venv  = Join-Path $Root ".venv"
$VenvPy   = Join-Path $Venv "Scripts\python.exe"
$VenvPyw  = Join-Path $Venv "Scripts\pythonw.exe"
$PyArgs   = @()                # py -3 之類的額外參數

Write-Host ""
Write-Host "== 真尋桌寵 安裝 ==" -ForegroundColor Green

# ── 1) 找可用的 64 位元 Python 3.10+（PyQt6 只有 64-bit wheel）──
function Invoke-PythonProbe([string]$Exe, [array]$Pre) {
    try {
        $code = "import sys,struct;" +
                "assert sys.version_info>=(3,10);" +
                "assert struct.calcsize('P')*8==64;" +
                "print(sys.version.split()[0])"
        $out = & $Exe @Pre -c $code 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) { return "$out".Trim() }
    } catch {}
    return $null
}

if ($Python) {
    $Pre = @()
    $ver = Invoke-PythonProbe $Python $Pre
    if (-not $ver) { throw "指定的 Python 不可用（需要 64 位元、3.10 以上）：$Python" }
    Write-Host "使用指定的 Python $ver（$Python）" -ForegroundColor DarkGray
} else {
    foreach ($cand in @("py", "python", "python3")) {
        if (-not (Get-Command $cand -ErrorAction SilentlyContinue)) { continue }
        $Pre = if ($cand -eq "py") { @("-3", "-64") } else { @() }
        $ver = Invoke-PythonProbe $cand $Pre
        if (-not $ver -and $cand -eq "py") {          # 有些環境不支援 -64
            $ver = Invoke-PythonProbe $cand @("-3")
            $Pre = @("-3")
        }
        if ($ver) {
            $Python = $cand
            $PyArgs = $Pre
            Write-Host "找到 Python $ver（$cand $($Pre -join ' ')）" -ForegroundColor DarkGray
            break
        }
    }
}

if (-not $Python) {
    Write-Host ""
    Write-Host "找不到 64 位元的 Python 3.10+。" -ForegroundColor Red
    Write-Host "請先安裝（安裝時勾選 'Add python.exe to PATH'）：" -ForegroundColor Yellow
    Write-Host "  winget install Python.Python.3.12" -ForegroundColor Cyan
    Write-Host "  或到 https://www.python.org/downloads/windows/ 下載" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "也可以事後直接指定路徑：" -ForegroundColor Yellow
    Write-Host '  powershell -ExecutionPolicy Bypass -File setup.ps1 -Python "C:\Python312\python.exe"' -ForegroundColor Cyan
    exit 1
}

# ── 2) 建立虛擬環境 ─────────────────────────────────────────
if ($ForceReinstall -and (Test-Path $Venv)) {
    Write-Host "移除舊的 .venv ..." -ForegroundColor DarkGray
    Remove-Item -Recurse -Force $Venv
}

if (-not (Test-Path $VenvPy)) {
    Write-Host "建立虛擬環境 .venv ..." -ForegroundColor Green
    & $Python @PyArgs -m venv $Venv
    if (-not (Test-Path $VenvPy)) { throw "虛擬環境建立失敗" }
} else {
    Write-Host ".venv 已存在" -ForegroundColor DarkGray
}

# ── 3) 安裝相依套件（已裝好就跳過）─────────────────────────
& $VenvPy -c "import PyQt6, psutil" 2>$null | Out-Null
$needInstall = ($LASTEXITCODE -ne 0) -or $ForceReinstall
if ($needInstall) {
    Write-Host "安裝相依套件（PyQt6 / psutil / Pillow）... 第一次會比較久" -ForegroundColor Green
    & $VenvPy -m pip install --upgrade pip --quiet
    & $VenvPy -m pip install -r (Join-Path $Root "requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "pip install 失敗，請檢查網路或 proxy 設定" }
} else {
    Write-Host "相依套件已安裝，跳過" -ForegroundColor DarkGray
}

# ── 4) 確認 assets 資料夾齊全 ───────────────────────────────
foreach ($d in @("idle","working","pat","grabbed","poke","notify","confirm")) {
    $p = Join-Path $Root "assets\$d"
    if (-not (Test-Path $p)) { New-Item -ItemType Directory -Path $p | Out-Null }
}

Write-Host ""
Write-Host "安裝完成" -ForegroundColor Green
Write-Host "之後要啟動，直接雙擊「啟動桌寵.bat」（不會出現黑色終端機視窗）。"

# ── 5) 啟動 ────────────────────────────────────────────────
if (-not $NoLaunch) {
    Write-Host ""
    if ($WithConsole) {
        Write-Host "啟動桌寵（終端機模式，可看 log）..." -ForegroundColor Green
        & $VenvPy main.py
    } else {
        Write-Host "啟動桌寵（背景模式）..." -ForegroundColor Green
        if (Test-Path $VenvPyw) {
            Start-Process -FilePath $VenvPyw -ArgumentList "main.py" -WorkingDirectory $Root
        } else {
            Start-Process -FilePath $VenvPy -ArgumentList "main.py" -WorkingDirectory $Root
        }
    }
}
