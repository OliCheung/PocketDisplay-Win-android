Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Disp3 {
  [DllImport("user32.dll", CharSet=CharSet.Ansi)]
  public static extern bool EnumDisplayDevices(IntPtr lpDevice, uint iDevNum, ref DISPLAY_DEVICE lpDisplayDevice, uint dwFlags);
  [DllImport("user32.dll", CharSet=CharSet.Ansi)]
  public static extern bool EnumDisplaySettings(string lpDeviceName, int iModeNum, ref DEVMODE lpDevMode);
  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Ansi)]
  public struct DISPLAY_DEVICE {
    public int cb;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst=32)] public string DeviceName;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst=128)] public string DeviceString;
    public int StateFlags;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst=128)] public string DeviceID;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst=128)] public string DeviceKey;
  }
  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Ansi)]
  public struct DEVMODE {
    public const int DM_SIZEOF = 124;
    [MarshalAs(UnmanagedType.ByValTStr,SizeConst=32)] public string dmDeviceName;
    public short dmSpecVersion; public short dmDriverVersion; public short dmSize; public short dmDriverExtra;
    public int dmFields; public int dmPositionX; public int dmPositionY; public int dmDisplayOrientation; public int dmDisplayFixedOutput;
    public short dmColor; public short dmDuplex; public short dmYResolution; public short dmTTOption; public short dmCollate;
    [MarshalAs(UnmanagedType.ByValTStr,SizeConst=32)] public string dmFormName;
    public short dmLogPixels; public int dmBitsPerPel; public int dmPelsWidth; public int dmPelsHeight;
    public int dmDisplayFlags; public int dmDisplayFrequency;
    public int dmICMMethod; public int dmICMIntent; public int dmMediaType; public int dmDroffset;
    public int dmBitmapFormat; public int dmNup; public int dmICMIntent2;
  }
}
"@

$dev = New-Object Disp3+DISPLAY_DEVICE
$dev.cb = [System.Runtime.InteropServices.Marshal]::SizeOf($dev)
$i = 0
while ([Disp3]::EnumDisplayDevices([IntPtr]::Zero, $i, [ref]$dev, 0)) {
    $i++
    $name = $dev.DeviceName
    $str = $dev.DeviceString
    if ($str -match 'Virtual Display Driver') {
        Write-Host "=== $name  ($str) ==="
        $set = @{}
        $dm = New-Object Disp3+DEVMODE
        $dm.dmSize = [Disp3+DEVMODE]::DM_SIZEOF
        $k = 0
        while ([Disp3]::EnumDisplaySettings($name, $k, [ref]$dm)) {
            $k++
            $set["$($dm.dmPelsWidth)x$($dm.dmPelsHeight)"] = $true
        }
        ($set.Keys | Sort-Object) -join ', '
        Write-Host ""
    }
    $dev = New-Object Disp3+DISPLAY_DEVICE
    $dev.cb = [System.Runtime.InteropServices.Marshal]::SizeOf($dev)
}
