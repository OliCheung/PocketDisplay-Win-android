# Render the Android adaptive icon (vector XML) into PNGs, then let a small
# Python step pack them into assets/icon.ico + assets/icon.png.
#
# Uses WPF to rasterize the VectorDrawable path data (same path syntax), and
# clips the artwork to a rounded square so it looks like a normal app icon on
# Windows instead of a full-bleed square.

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName PresentationCore
Add-Type -AssemblyName PresentationFramework
Add-Type -AssemblyName WindowsBase

$res = 'd:\PocketDisplay\android\app\src\main\res\drawable'
$assets = 'd:\PocketDisplay\assets'
$androidNs = 'http://schemas.android.com/apk/res/android'
New-Item -ItemType Directory -Force -Path $assets | Out-Null

# ---- build one DrawingVisual holding background + clipped foreground -------
function Build-IconVisual {
    $dv = New-Object System.Windows.Media.DrawingVisual

    # rounded-square mask (108x108 viewport, radius ~22%)
    $rect = New-Object System.Windows.Rect(0, 0, 108, 108)
    $mask = New-Object System.Windows.Media.RectangleGeometry($rect, 24, 24)

    $dc = $dv.RenderOpen()

    # clip everything (background included) to the rounded square
    $dc.PushClip($mask)

    # background layer
    [xml]$bg = Get-Content (Join-Path $res 'ic_launcher_background.xml') -Raw
    $bgBrush = $null
    foreach ($p in $bg.vector.path) {
        $fill = $p.GetAttribute('fillColor', $androidNs)
        $data = $p.GetAttribute('pathData', $androidNs)
        if ($fill -and $data) {
            $brush = New-Object System.Windows.Media.SolidColorBrush(
                [System.Windows.Media.ColorConverter]::ConvertFromString($fill))
            $geo = [System.Windows.Media.StreamGeometry]::Parse($data)
            $dc.DrawGeometry($brush, $null, $geo)
            $bgBrush = $brush
        }
    }

    # foreground layer
    [xml]$fg = Get-Content (Join-Path $res 'ic_launcher_foreground.xml') -Raw
    foreach ($p in $fg.vector.path) {
        $fill = $p.GetAttribute('fillColor', $androidNs)
        $data = $p.GetAttribute('pathData', $androidNs)
        if (-not $fill) { continue }
        $brush = New-Object System.Windows.Media.SolidColorBrush(
            [System.Windows.Media.ColorConverter]::ConvertFromString($fill))
        $geo = [System.Windows.Media.StreamGeometry]::Parse($data)
        $dc.DrawGeometry($brush, $null, $geo)
    }
    $dc.Pop()
    $dc.Close()
    return $dv
}

$visual = Build-IconVisual

# ---- rasterize at each needed size ---------------------------------------
$sizes = @(16, 24, 32, 48, 64, 128, 256)
foreach ($s in $sizes) {
    $scale = $s / 108.0
    $visual.Transform = New-Object System.Windows.Media.ScaleTransform($scale, $scale)

    $rtb = New-Object System.Windows.Media.Imaging.RenderTargetBitmap(
        $s, $s, 96, 96, [System.Windows.Media.PixelFormats]::Pbgra32)
    $rtb.Render($visual)

    $out = Join-Path $assets ("icon_src_{0}.png" -f $s)
    $enc = New-Object System.Windows.Media.Imaging.PngBitmapEncoder
    $enc.Frames.Add([System.Windows.Media.Imaging.BitmapFrame]::Create($rtb))
    $fs = [System.IO.File]::Create($out)
    $enc.Save($fs)
    $fs.Close()
    Write-Host ("rendered {0} -> {1} ({2} bytes)" -f $s, $out, (Get-Item $out).Length)
}
$visual.Transform = $null
Write-Host 'render done'
