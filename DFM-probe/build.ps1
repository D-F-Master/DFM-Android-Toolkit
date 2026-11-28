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
# Rhino 1.8.1 class files carry MethodParameters attrs (name_index=0);
# 34.0.0 d8 NPEs on them, must use 37.0.0 d8 (see b18/b23 notes)
$d8RhinoTools = Join-Path $Sdk 'build-tools\37.0.0'
$androidJar = Join-Path $Sdk 'platforms\android-34\android.jar'
# rhino class files are Java 11+ (major 55); JDK8 javac cannot read them, use JDK17
$javac = 'D:\jdks\17\bin\javac.exe'
$jdk17 = 'D:\jdks\17'
$rhinoJar = Join-Path $root 'libs\rhino-1.8.1.jar'

$aapt2 = Join-Path $buildTools 'aapt2.exe'
$d8 = Join-Path $buildTools 'd8.bat'
$zipalign = Join-Path $buildTools 'zipalign.exe'
$apksigner = Join-Path $buildTools 'apksigner.bat'
$keytool = Join-Path $jdk17 'bin\keytool.exe'

$camhookSo = Join-Path $root 'assets\camhook.so'
$dfmJs = Join-Path $root 'assets\dfm.js'
foreach ($p in @($Sdk, $androidJar, $aapt2, $d8, $zipalign, $apksigner, $javac, $keytool, $camhookSo, $rhinoJar, $dfmJs, (Join-Path $d8RhinoTools 'd8.bat'))) {
    if (-not (Test-Path $p)) { throw "Missing build dependency: $p" }
}

$work = Join-Path $root 'dist\build'
if (Test-Path $work) { Remove-Item -Recurse -Force $work }
$flat = Join-Path $work 'flat'
$gen = Join-Path $work 'gen'
$classes = Join-Path $work 'classes'
$dex = Join-Path $work 'dex'
$dexRhino = Join-Path $work 'dex-rhino'
foreach ($d in @($flat, $gen, $classes, $dex, $dexRhino)) {
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

Write-Host '==> javac (JDK17: rhino 1.8.1 needs Java 11+)'
$sources = @(Get-ChildItem (Join-Path $root 'java') -Recurse -Filter *.java | ForEach-Object { $_.FullName })
$sources += @(Get-ChildItem $gen -Recurse -Filter R.java | ForEach-Object { $_.FullName })
& $javac -nowarn -source 8 -target 8 -encoding UTF-8 `-classpath "$androidJar;$rhinoJar" -d $classes $sources
if ($LASTEXITCODE -ne 0) { throw 'javac failed' }

Write-Host '==> d8 (probe classes)'
$env:JAVA_HOME = $jdk17
$classFiles = @(Get-ChildItem $classes -Recurse -Filter *.class | ForEach-Object { $_.FullName })
& $d8 --lib $androidJar --min-api 23 --output $dex $classFiles
if ($LASTEXITCODE -ne 0) { throw 'd8 failed' }

Write-Host '==> d8 (rhino jar -> classes2.dex, build-tools 37.0.0)'
$d8Rhino = Join-Path $d8RhinoTools 'd8.bat'
& $d8Rhino --lib $androidJar --min-api 23 --output $dexRhino $rhinoJar
if ($LASTEXITCODE -ne 0) { throw 'd8 rhino failed' }

Write-Host '==> extract rhino ResourceBundle .properties (APK root)'
$propsDir = Join-Path $work 'props'
Add-Type -AssemblyName System.IO.Compression.FileSystem
$rhinoZip = [System.IO.Compression.ZipFile]::OpenRead($rhinoJar)
try {
    $propsEntries = $rhinoZip.Entries | Where-Object { $_.FullName -like '*.properties' }
    foreach ($e in $propsEntries) {
        $outPath = Join-Path $propsDir $e.FullName
        $outParent = [System.IO.Path]::GetDirectoryName($outPath)
        if (-not (Test-Path $outParent)) { New-Item -ItemType Directory -Force -Path $outParent | Out-Null }
        [System.IO.Compression.ZipFileExtensions]::ExtractToFile($e, $outPath, $true)
    }
    Write-Host ('    extracted ' + @($propsEntries).Count + ' properties files')
} finally {
    $rhinoZip.Dispose()
}

Write-Host '==> merge classes.dex'
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::Open($baseApk, [System.IO.Compression.ZipArchiveMode]::Update)
try {
    [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
        $zip, (Join-Path $dex 'classes.dex'), 'classes.dex') | Out-Null
    # rhino 1.8.1 as separate dex (minSdk 23 supports multidex natively)
    [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
        $zip, (Join-Path $dexRhino 'classes.dex'), 'classes2.dex') | Out-Null
    # camera-replace so (0x11 extracts it from assets to /vendor/lib)
    [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
        $zip, $camhookSo, 'assets/camhook.so') | Out-Null
    # dfm.js runtime placeholder (P1: precompiled into scope by JsEngine)
    [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
        $zip, $dfmJs, 'assets/dfm.js') | Out-Null
    # rhino ResourceBundle .properties must be in APK ROOT (not dex/assets), or
    # ScriptRuntime message lookup fails: MissingResourceException Messages zh_CN
    $propsDir = Join-Path $work 'props'
    if (Test-Path $propsDir) {
        $propFiles = @(Get-ChildItem $propsDir -Recurse -Filter *.properties)
        foreach ($pf in $propFiles) {
            $rel = $pf.FullName.Substring($propsDir.Length + 1).Replace('\', '/')
            [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $pf.FullName, $rel) | Out-Null
        }
        Write-Host ('    merged ' + $propFiles.Count + ' properties files into APK root')
    }
} finally {
    $zip.Dispose()
}

Write-Host '==> zipalign'
$alignedApk = Join-Path $work 'aligned.apk'
& $zipalign -f -p 4 $baseApk $alignedApk
if ($LASTEXITCODE -ne 0) { throw 'zipalign failed' }

Write-Host '==> generate debug keystore'
# keystore fixed at project root (outside dist), reused if exists:
# regenerating changes the signature, old installed build could not upgrade
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

# Copy the APK to the service install path (get_probe_apk_path), overwrite if exists
$dataDir = Join-Path (Split-Path $PSScriptRoot -Parent) 'DFM-service\data\apk'
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
$serviceApk = Join-Path $dataDir 'DFMProbe.apk'
Copy-Item -Force -Path $OutApk -Destination $serviceApk
Write-Host "Copied to: $serviceApk"

Write-Host "Build finished: $OutApk"
