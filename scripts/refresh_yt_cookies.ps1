# refresh_yt_cookies.ps1
# Extracts YouTube cookies via a persistent Playwright Chromium profile
# (no Chrome ABE issues — Playwright manages its own Chromium build).
# One-time setup: run setup_playwright_profile.py once to log in to YouTube.
# After that this script runs fully unattended.

$ErrorActionPreference = "Stop"

$SCRIPT_DIR  = $PSScriptRoot
$DROPLET     = "root@138.68.186.172"
$REMOTE_PATH = "/opt/sdmnh/credentials/youtube_cookies.txt"
$TEMP_COOKIE = "$env:TEMP\youtube_cookies_fresh.txt"
$LOG         = "$SCRIPT_DIR\cookie_refresh.log"

function Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg"
    Write-Host $line
    Add-Content -Path $LOG -Value $line -Encoding UTF8
}

Log "Starting cookie refresh (Playwright)..."

# Extract cookies using the persistent Playwright profile
try {
    $result = python "$SCRIPT_DIR\extract_cookies_playwright.py" $TEMP_COOKIE 2>&1
    $result | ForEach-Object { Log $_ }
    $extractExit = $LASTEXITCODE
} catch {
    Log "ERROR: Failed to run extract_cookies_playwright.py: $_"
    exit 1
}

if ($extractExit -ne 0 -or -not (Test-Path $TEMP_COOKIE)) {
    Log "ERROR: Cookie extraction failed (exit $extractExit). Re-run setup_playwright_profile.py to re-login."
    exit 1
}

$size = (Get-Item $TEMP_COOKIE).Length
Log "Cookies extracted: $size bytes"

# SCP to droplet
try {
    scp -o StrictHostKeyChecking=no $TEMP_COOKIE "${DROPLET}:${REMOTE_PATH}"
    Log "Uploaded to ${DROPLET}:${REMOTE_PATH}"
} catch {
    Log "ERROR: SCP failed: $_"
    Remove-Item -Force $TEMP_COOKIE -ErrorAction SilentlyContinue
    exit 1
}

# Fix permissions on droplet
try {
    ssh -o StrictHostKeyChecking=no $DROPLET "chmod 600 $REMOTE_PATH"
    Log "Permissions set."
} catch {
    Log "WARNING: chmod failed (non-fatal): $_"
}

Remove-Item -Force $TEMP_COOKIE -ErrorAction SilentlyContinue
Log "Cookie refresh complete."
