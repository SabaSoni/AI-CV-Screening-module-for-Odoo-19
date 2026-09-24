# Installs Ollama (the free local AI runtime) and downloads the models this module uses.
# Windows 10/11. Run in PowerShell:   powershell -ExecutionPolicy Bypass -File setup_ollama.ps1
# Optional: -ChatModel gemma3:12b   for better Georgian text (slower, ~8 GB download).
param(
    [string]$ChatModel = "gemma3:4b",
    [string]$EmbedModel = "bge-m3"
)

$ErrorActionPreference = "Stop"
$exe = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"

if (-not (Test-Path $exe) -and -not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Write-Host "Installing Ollama with winget..."
    winget install --id Ollama.Ollama -e --silent --accept-source-agreements --accept-package-agreements
}
if (-not (Test-Path $exe)) { $exe = (Get-Command ollama).Source }

# The installer starts the background service; wait until it answers.
$up = $false
foreach ($i in 1..30) {
    try { Invoke-WebRequest -Uri "http://127.0.0.1:11434/api/version" -UseBasicParsing -TimeoutSec 3 | Out-Null; $up = $true; break }
    catch { Start-Sleep -Seconds 2 }
}
if (-not $up) {
    Write-Host "Starting the Ollama service..."
    Start-Process -FilePath $exe -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep -Seconds 5
}

foreach ($model in @($ChatModel, $EmbedModel)) {
    # Downloads resume, so a dropped connection only costs a retry.
    foreach ($attempt in 1..20) {
        if ((& $exe list) -match [regex]::Escape($model)) { break }
        Write-Host "Downloading $model (attempt $attempt)..."
        & $exe pull $model
        if ($LASTEXITCODE -ne 0) { Start-Sleep -Seconds 20 }
    }
}

Write-Host ""
& $exe list
Write-Host ""
Write-Host "Done. In Odoo: Recruitment > Configuration > Settings > AI CV Screening > Test connection."
Write-Host "Laptops: keep the charger plugged in, otherwise the GPU may switch off and analysis gets ~4x slower."
