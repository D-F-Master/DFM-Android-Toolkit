#Requires -Version 5.1
<#
    DFM Probe APK build script (no Gradle; uses Android SDK build-tools directly).
    Usage: powershell -ExecutionPolicy Bypass -File build.ps1
    Output: DFMProbe.apk (zipalign + debug signed)
#>
param(
    [string]$Sdk = $env:ANDROID_HOME,
    [string]$OutApk = (Join-Path $PSScriptRoot 'DFMProbe.apk')
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

$buildTools = Join-Path $Sdk 'build-tools\34.0.0'
$androidJar = Join-Path $Sdk 'platforms\android-34\android.jar'
$javac = 'D:\jdks\8\bin\javac.exe'
$jdk17 = 'D:\jdks\17'

$aapt2 = Join-Path $buildTools 'aapt2.exe'
$d8 = Join-Path $buildTools 'd8.bat'
$zipalign = Join-Path $buildTools 'zipalign.exe'
$apksigner = Join-Path $buildTools 'apksigner.bat'
$keytool = Join-Path $jdk17 'bin\keytool.exe'

foreach ($p in @($Sdk, $androidJar, $aapt2, $d8, $zipalign, $apksigner, $javac, $keytool)) {
    if (-not (Test-Path $p)) { throw "Missing build dependency: $p" }
}

$work = Join-Path $root 'dist\build'
if (Test-Path $work) { Remove-Item -Recurse -Force $work }
$flat = Join-Path $work 'flat'
$gen = Join-Path $work 'gen'
$classes = Join-Path $work 'classes'
$dex = Join-Path $work 'dex'
foreach ($d in @($flat, $gen, $classes, $dex)) {
    New-Item -ItemType Directory -Force -Path $d | Out-Null
}

Write-Host '==> aapt2 compile'
& $aapt2 compile --dir (Join-Path $root 'res') -o $flat
if ($LASTEXITCODE -ne 0) { throw 'aapt2 compile failed' }

Write-Host '==> aapt2 link'
$baseApk = Join-Path $work 'base.apk'
$linkArgs = @(
    'link', '-o', $baseApk, '-I', $androidJar,
    '--manifest', (Join-Path $root 'AndroidManifest.xml'),
    '--java', $gen,
    '--min-sdk-version', '23', '--target-sdk-version', '30'
)
foreach ($f in (Get-ChildItem $flat -Filter *.flat)) {
    $linkArgs += $f.FullName
}
& $aapt2 @linkArgs
if ($LASTEXITCODE -ne 0) { throw 'aapt2 link failed' }

Write-Host '==> javac'
$sources = @(Get-ChildItem (Join-Path $root 'java') -Recurse -Filter *.java | ForEach-Object { $_.FullName })
$sources += @(Get-ChildItem $gen -Recurse -Filter R.java | ForEach-Object { $_.FullName })
& $javac -source 1.8 -target 1.8 -encoding UTF-8 -bootclasspath $androidJar -d $classes $sources
if ($LASTEXITCODE -ne 0) { throw 'javac failed' }

Write-Host '==> d8'
$env:JAVA_HOME = $jdk17
$classFiles = @(Get-ChildItem $classes -Recurse -Filter *.class | ForEach-Object { $_.FullName })
& $d8 --lib $androidJar --min-api 23 --output $dex $classFiles
if ($LASTEXITCODE -ne 0) { throw 'd8 failed' }

Write-Host '==> merge classes.dex'
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::Open($baseApk, [System.IO.Compression.ZipArchiveMode]::Update)
try {
    [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
        $zip, (Join-Path $dex 'classes.dex'), 'classes.dex') | Out-Null
} finally {
    $zip.Dispose()
}

Write-Host '==> zipalign'
$alignedApk = Join-Path $work 'aligned.apk'
& $zipalign -f -p 4 $baseApk $alignedApk
if ($LASTEXITCODE -ne 0) { throw 'zipalign failed' }

Write-Host '==> generate debug keystore'
# keystore 固定在工程根目录（dist 外），存在则复用：
# 每次重新生成会导致签名变化，设备上已装的旧版本将无法覆盖安装
$keystore = Join-Path $PSScriptRoot 'debug.keystore'
if (-not (Test-Path $keystore)) {
    & $keytool -genkeypair -alias dfmprobe -keystore $keystore -storetype JKS `
        -storepass android -keypass android -keyalg RSA -keysize 2048 -validity 10000 `
        -dname 'CN=DFM Probe,OU=Dev,O=DFM,C=CN'
    if ($LASTEXITCODE -ne 0) { throw 'keytool failed' }
}

Write-Host '==> apksigner'
& $apksigner sign --ks $keystore --ks-pass pass:android --key-pass pass:android `
    --out $OutApk $alignedApk
if ($LASTEXITCODE -ne 0) { throw 'apksigner failed' }
& $apksigner verify --verbose $OutApk
if ($LASTEXITCODE -ne 0) { throw 'apksigner verify failed' }

Write-Host "Build finished: $OutApk"
