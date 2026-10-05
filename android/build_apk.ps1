<#
.SYNOPSIS
    Build the PocketDisplay Android receiver APK.

.PARAMETER BuildType
    "release" (default) or "debug".

.DESCRIPTION
    For a release build, signing is applied automatically when these
    environment variables are present:
        KEYSTORE_FILE      - absolute path to the .jks keystore
        KEYSTORE_PASSWORD  - keystore password
        KEY_ALIAS          - key alias
        KEY_PASSWORD       - key password
    Without them a plain (unsigned) release APK is produced, which is fine for
    sideloading / GitHub release distribution but cannot be uploaded to Google Play.

    The produced APK is copied to ..\dist\PocketDisplay.apk for easy upload.
#>
param(
    [ValidateSet("release", "debug")]
    [string]$BuildType = "release"
)

$ErrorActionPreference = 'Stop'

# --- Java / Android SDK ------------------------------------------------
if (-not $env:JAVA_HOME) {
    $studioJbr = 'C:\Program Files\Android\Android Studio\jbr'
    if (Test-Path "$studioJbr\bin\java.exe") {
        $env:JAVA_HOME = $studioJbr
        Write-Host "Auto-detected JAVA_HOME: $studioJbr" -ForegroundColor Green
    }
}

Set-Location $PSScriptRoot

# AGP refuses to build a release APK without a signing config. When no keystore
# is configured we fall back to a debug APK, which installs fine for sideloading.
if ($BuildType -eq 'release' -and -not $env:KEYSTORE_FILE) {
    Write-Warning "No KEYSTORE_FILE set - building a DEBUG APK instead (unsigned)."
    Write-Warning "Set KEYSTORE_FILE / KEYSTORE_PASSWORD / KEY_ALIAS / KEY_PASSWORD for a signed release."
    $task = 'assembleDebug'
}
else {
    $task = if ($BuildType -eq 'release') { 'assembleRelease' } else { 'assembleDebug' }
}

Write-Host "==> Building Android APK ($BuildType) ..." -ForegroundColor Cyan
& .\gradlew.bat $task --no-daemon --init-script init.gradle 2>&1 | Tee-Object -Variable out | Select-Object -Last 40

if ($LASTEXITCODE -ne 0) {
    Write-Host "BUILD_FAILED_EXIT_$LASTEXITCODE" -ForegroundColor Red
    exit $LASTEXITCODE
}
Write-Host "BUILD_DONE_OK" -ForegroundColor Green

# --- locate + copy the APK ---------------------------------------------
$apkDir = Join-Path $PSScriptRoot "app\build\outputs\apk"
$apk = Get-ChildItem -Path $apkDir -Recurse -Filter '*.apk' | Select-Object -First 1
if (-not $apk) {
    Write-Host "Could not find built APK under $apkDir" -ForegroundColor Red
    exit 1
}

$distDir = Join-Path $PSScriptRoot '..\dist'
New-Item -ItemType Directory -Force -Path $distDir | Out-Null
$dest = Join-Path $distDir 'PocketDisplay.apk'
Copy-Item -Force $apk.FullName $dest
Write-Host "APK ready: $dest" -ForegroundColor Green
