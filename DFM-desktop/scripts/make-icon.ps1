# 生成应用图标：把 DFM-frontend 的 logo.png 重采样为 256x256，
# 供 electron-builder 转换为 Windows 安装包图标。
Add-Type -AssemblyName System.Drawing
$src = 'D:\uTools\DFM-frontend\public\logo.png'
$dstDir = 'D:\uTools\DFM-desktop\build'
New-Item -ItemType Directory -Force -Path $dstDir | Out-Null
$dst = Join-Path $dstDir 'icon.png'

$img = [System.Drawing.Image]::FromFile($src)
$bmp = New-Object System.Drawing.Bitmap(256, 256)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
$g.DrawImage($img, 0, 0, 256, 256)
$g.Dispose(); $img.Dispose()
$bmp.Save($dst, [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()
Write-Host "icon generated: $dst"
