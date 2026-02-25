# SDMNH Automation - Startup folder setup (no admin required)
# Run this once in a normal PowerShell window:
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#   .\setup_startup.ps1
#
# To remove later:
#   Remove-Item "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\run_sdmnh.vbs"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$VbsSource  = Join-Path $ProjectDir "run_sdmnh.vbs"
$StartupDir = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup"
$VbsDest    = Join-Path $StartupDir "run_sdmnh.vbs"

Write-Host ""
Write-Host "SDMNH Automation - Startup Setup" -ForegroundColor Cyan
Write-Host "-----------------------------------"

if (-not (Test-Path $VbsSource)) {
    Write-Error "run_sdmnh.vbs not found at: $VbsSource"
    exit 1
}

if (-not (Test-Path "$ProjectDir\automation.py")) {
    Write-Error "automation.py not found in: $ProjectDir"
    exit 1
}

Copy-Item -Path $VbsSource -Destination $VbsDest -Force

Write-Host ""
Write-Host "  Installed to Startup folder:" -ForegroundColor Green
Write-Host "  $VbsDest"
Write-Host ""
Write-Host "  The VBS fires silently 2 minutes after every login." -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  1. First-time YouTube auth - run this once manually:"
Write-Host "       python automation.py --setup"
Write-Host ""
Write-Host "  2. Test the full pipeline:"
Write-Host "       python automation.py --force"
Write-Host ""
Write-Host "  3. Done - log in tomorrow and a video will compile and upload automatically."
Write-Host ""
Write-Host "  Log file location:"
Write-Host "  $ProjectDir\data\automation.log"
Write-Host ""
