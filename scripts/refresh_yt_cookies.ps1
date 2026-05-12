# refresh_yt_cookies.ps1
# Exports YouTube cookies from Chrome via CDP and uploads to SDMNH droplet.
# Scheduled daily at 03:00 AM. Works regardless of whether Chrome is running.
# Chrome ABE (App-Bound Encryption) is bypassed by using Chrome's own process.

$ErrorActionPreference = "Stop"

$SCRIPT_DIR   = $PSScriptRoot
$DROPLET      = "root@138.68.186.172"
$REMOTE_PATH  = "/opt/sdmnh/credentials/youtube_cookies.txt"
$TEMP_COOKIE  = "$env:TEMP\youtube_cookies_fresh.txt"
$LOG          = "$SCRIPT_DIR\cookie_refresh.log"
$CHROME       = "C:\Program Files\Google\Chrome\Application\chrome.exe"
$DEBUG_PORT   = 9222
$CHROME_STARTED = $false

function Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg"
    Write-Host $line
    Add-Content -Path $LOG -Value $line -Encoding UTF8
}

Log "Starting cookie refresh..."

# Step 1: Ensure Chrome is running with remote debugging
$chromeProc = Get-Process -Name "chrome" -ErrorAction SilentlyContinue
if (-not $chromeProc) {
    Log "Chrome not running — starting with debug port $DEBUG_PORT..."
    Start-Process $CHROME -ArgumentList "--remote-debugging-port=$DEBUG_PORT", "--remote-allow-origins=*", "--user-data-dir=$env:LOCALAPPDATA\Google\Chrome\User Data"
    Start-Sleep -Seconds 5
    $CHROME_STARTED = $true
    Log "Chrome started."
} else {
    Log "Chrome already running — attaching debug port $DEBUG_PORT..."
    # Re-launch with debug flag if not already listening
    try {
        $null = Invoke-WebRequest "http://localhost:$DEBUG_PORT/json" -UseBasicParsing -TimeoutSec 3
        Log "Debug port already open."
    } catch {
        # Kill and restart with debug flag
        Stop-Process -Name "chrome" -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
        Start-Process $CHROME -ArgumentList "--remote-debugging-port=$DEBUG_PORT", "--remote-allow-origins=*", "--user-data-dir=$env:LOCALAPPDATA\Google\Chrome\User Data"
        Start-Sleep -Seconds 5
        $CHROME_STARTED = $true
        Log "Chrome restarted with debug port."
    }
}

# Step 2: Extract cookies via CDP
try {
    $result = python "$SCRIPT_DIR\extract_cookies_cdp.py" $TEMP_COOKIE 2>&1
    $result | ForEach-Object { Log $_ }
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $TEMP_COOKIE)) {
        throw "CDP extractor failed (exit $LASTEXITCODE)"
    }
    $size = (Get-Item $TEMP_COOKIE).Length
    Log "Cookies extracted: $size bytes"
} catch {
    Log "ERROR: Cookie extraction failed: $_"
    if ($CHROME_STARTED) { Stop-Process -Name "chrome" -Force -ErrorAction SilentlyContinue }
    exit 1
}

# Step 3: SCP to droplet
try {
    scp -o StrictHostKeyChecking=no $TEMP_COOKIE "${DROPLET}:${REMOTE_PATH}"
    Log "Uploaded to ${DROPLET}:${REMOTE_PATH}"
} catch {
    Log "ERROR: SCP failed: $_"
    Remove-Item -Force $TEMP_COOKIE -ErrorAction SilentlyContinue
    if ($CHROME_STARTED) { Stop-Process -Name "chrome" -Force -ErrorAction SilentlyContinue }
    exit 1
}

# Step 4: fix permissions on droplet
try {
    ssh -o StrictHostKeyChecking=no $DROPLET "chmod 444 $REMOTE_PATH"
    Log "Permissions set to 444"
} catch {
    Log "WARNING: chmod failed (non-fatal): $_"
}

# Cleanup
Remove-Item -Force $TEMP_COOKIE -ErrorAction SilentlyContinue
if ($CHROME_STARTED) {
    Stop-Process -Name "chrome" -Force -ErrorAction SilentlyContinue
    Log "Chrome closed."
}
Log "Cookie refresh complete."
