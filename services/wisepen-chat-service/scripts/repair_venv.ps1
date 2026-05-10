# repair_venv.ps1
# 激进修复 venv：删除 uv.lock 和 .venv，全量重装
# 用法: cd services/wisepen-chat-service ; powershell -ExecutionPolicy Bypass -File scripts/repair_venv.ps1

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ServiceDir = Resolve-Path "$ScriptDir\.."
$WorkspaceDir = Resolve-Path "$ServiceDir\..\.."
$VenvDir   = "$WorkspaceDir\.venv"
$LockFile  = "$WorkspaceDir\uv.lock"

Write-Host "=== 激进修复 venv ==="
Write-Host "工作区: $WorkspaceDir"
Write-Host ""

# 1. 删除 uv.lock
if (Test-Path $LockFile) {
    Write-Host "[1/4] 删除 uv.lock ..."
    Remove-Item -Force $LockFile
    Write-Host "  已删除"
} else {
    Write-Host "[1/4] uv.lock 不存在，跳过"
}

# 2. 删除 .venv
if (Test-Path $VenvDir) {
    Write-Host "[2/4] 删除 .venv ..."
    Remove-Item -Recurse -Force $VenvDir -ErrorAction SilentlyContinue
    if (Test-Path $VenvDir) {
        Write-Host "  文件被占用，重试 ..."
        Start-Sleep -Seconds 3
        Remove-Item -Recurse -Force $VenvDir -ErrorAction Stop
    }
    Write-Host "  已删除"
} else {
    Write-Host "[2/4] .venv 不存在，跳过"
}

# 3. uv sync (base + doc + ocr)
Write-Host "[3/4] uv sync --group doc --group ocr (全量安装) ..."
Push-Location $ServiceDir
try {
    uv sync --group doc --group ocr
    if ($LASTEXITCODE -ne 0) {
        throw "uv sync 失败 (exit code $LASTEXITCODE)"
    }
} finally {
    Pop-Location
}
Write-Host "  完成"

# 4. 验证
Write-Host "[4/4] 验证 ..."
Push-Location $ServiceDir
try {
    uv run python -c "from docling.document_converter import DocumentConverter; print('docling import ok')"
    uv run python -c "from chat.main import app; print(app.title)"
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "=== 修复完成 ==="
