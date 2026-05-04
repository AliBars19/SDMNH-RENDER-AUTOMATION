# refresh_yt_cookies.ps1
# Exports YouTube cookies from Chrome and uploads to SDMNH droplet.
# Scheduled daily at 03:00 AM (Chrome typically closed).

$ErrorActionPreference = "Stop"

$SCRIPT_DIR  = "$PSScriptRoot"
$DROPLET     = "root@138.68.186.172"
$REMOTE_PATH = "/opt/sdmnh/credentials/youtube_cookies.txt"
$TEMP_COOKIE = "$env:TEMP\youtube_cookies_fresh.txt"
$LOG         = "$SCRIPT_DIR\cookie_refresh.log"

function Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg"
    Write-Host $line
    Add-Content -Path $LOG -Value $line
}

Log "Starting cookie refresh..."

# Step 1: extract cookies via Playwright Chromium (no Chrome ABE issues)
try {
    python "$SCRIPT_DIR\get_cookies_playwright.py" $TEMP_COOKIE 2>&1 | ForEach-Object { Log $_ }
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $TEMP_COOKIE)) {
        throw "Playwright extractor returned exit code $LASTEXITCODE"
    }
    Log "Cookies extracted to $TEMP_COOKIE ($((Get-Item $TEMP_COOKIE).Length) bytes)"
} catch {
    Log "ERROR: Cookie extraction failed: $_"
    exit 1
}

# Step 2: SCP to droplet
try {
    scp -o StrictHostKeyChecking=no $TEMP_COOKIE "${DROPLET}:${REMOTE_PATH}"
    Log "Uploaded to ${DROPLET}:${REMOTE_PATH}"
} catch {
    Log "ERROR: SCP failed: $_"
    Remove-Item -Force $TEMP_COOKIE -ErrorAction SilentlyContinue
    exit 1
}

# Step 3: fix permissions on droplet
try {
    ssh -o StrictHostKeyChecking=no $DROPLET "chmod 444 $REMOTE_PATH"
    Log "Permissions set to 444"
} catch {
    Log "WARNING: chmod failed (non-fatal): $_"
}

# Cleanup
Remove-Item -Force $TEMP_COOKIE -ErrorAction SilentlyContinue
Log "Done. Cookie refresh complete."
