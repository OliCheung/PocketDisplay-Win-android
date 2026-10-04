Add-Type @"
using System;
using System.Runtime.InteropServices;
public class DispSet {
  [DllImport("user32.dll", CharSet=CharSet.Ansi)]
  public static extern int ChangeDisplaySettingsEx(string lpszDeviceName, ref DEVMODE lpDevMode, IntPtr hwnd, uint dwflags, IntPtr lParam);
  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Ansi)]
  public struct DEVMODE {
    public const int DM_SIZEOF = 124;
    public const int DM_PELSWIDTH = 0x00080000;
    public const int DM_PELSHEIGHT = 0x00100000;
    public const int DM_BITSPERPEL = 0x00040000;
    public const int DM_DISPLAYFREQUENCY = 0x00400000;
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

$dm = New-Object DispSet+DEVMODE
$dm.dmSize = [DispSet+DEVMODE]::DM_SIZEOF
$dm.dmFields = [DispSet+DEVMODE]::DM_PELSWIDTH -bor [DispSet+DEVMODE]::DM_PELSHEIGHT -bor [DispSet+DEVMODE]::DM_BITSPERPEL -bor [DispSet+DEVMODE]::DM_DISPLAYFREQUENCY
$dm.dmPelsWidth = 1520
$dm.dmPelsHeight = 720
$dm.dmBitsPerPel = 32
$dm.dmDisplayFrequency = 60

$ret = [DispSet]::ChangeDisplaySettingsEx("\\.\DISPLAY16", [ref]$dm, [IntPtr]::Zero, 0, [IntPtr]::Zero)
Write-Host "ChangeDisplaySettingsEx return code: $ret  (0 = SUCCESS)"
