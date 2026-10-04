$env:JAVA_HOME = "C:\Program Files\Android\Android Studio\jbr"
$env:ANDROID_HOME = "$env:LOCALAPPDATA\Android\Sdk"
Set-Location "d:\PocketDisplay\android"
$gradleBin = "C:\Users\OLI\.gradle\wrapper\dists\gradle-8.14.3-all\10utluxaxniiv4wxiphsi49nj\gradle-8.14.3\bin\gradle.bat"
& $gradleBin assembleDebug --no-daemon --init-script init.gradle 2>&1 | Tee-Object -Variable out | Select-Object -Last 40
if ($LASTEXITCODE -ne 0) { Write-Host "BUILD_FAILED_EXIT_$LASTEXITCODE" } else { Write-Host "BUILD_DONE_OK" }
