"""PocketDisplay Windows Control Client.

A small Tkinter GUI to control the PocketDisplay server without touching the
command line or editing configuration.yaml by hand:

  * Start / Stop the server (manages the python server.py subprocess)
  * Pick which display (monitor) acts as the second screen
  * Change that display's resolution directly (via Win32 ChangeDisplaySettingsEx);
    the server auto-detects the change and restarts FFmpeg
  * Tune FPS / bitrate / preset / tune / profile / level (written to configuration.yaml,
    server restarts to apply)
  * Live status parsed from the server log: running state, phone connected,
    current capture resolution

Dependency-free: only the Python standard library, PyYAML and Tkinter (both ship
with a normal Python install; PyYAML is already used by the server).
"""

import os
import re
import sys
import time
import json
import signal
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext

import ctypes
from ctypes import wintypes

try:
    import yaml
except ImportError:
    yaml = None


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
if getattr(sys, "frozen", False):
    # Frozen build (PyInstaller): assets are bundled in the read-only _MEIPASS
    # temp dir, but the persisted config lives next to the .exe (writable).
    _EXE_DIR = os.path.dirname(os.path.abspath(sys.executable))
    _MEIPASS = getattr(sys, "_MEIPASS", _EXE_DIR)
    ROOT_DIR = _EXE_DIR
    SERVER_DIR = os.path.join(_MEIPASS, "server")
    CONFIG_PATH = os.path.join(_EXE_DIR, "configuration.yaml")
    ASSETS_DIR = os.path.join(_MEIPASS, "assets")
    ICON_ICO = os.path.join(ASSETS_DIR, "icon.ico")
    ICON_PNG = os.path.join(ASSETS_DIR, "icon.png")
else:
    ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
    SERVER_DIR = os.path.join(ROOT_DIR, "server")
    CONFIG_PATH = os.path.join(ROOT_DIR, "configuration.yaml")
    ASSETS_DIR = os.path.join(ROOT_DIR, "assets")
    ICON_ICO = os.path.join(ASSETS_DIR, "icon.ico")
    ICON_PNG = os.path.join(ASSETS_DIR, "icon.png")

# Pull the effective current settings straight from the server's config module.
sys.path.insert(0, SERVER_DIR)
import config as server_config  # noqa: E402


def recommend_resolution(modes, phone_w, phone_h):
    """Pick the Windows mode that keeps latency low while staying usable.

    Scoring rewards a small encode load (fewer pixels => lower latency) and a
    close aspect-ratio match (less stretch), and rejects modes so small that the
    phone would have to upscale them into mush.
    """
    if not modes or not phone_w or not phone_h:
        return None
    par = phone_w / float(phone_h)
    best = None
    best_score = None
    for (w, h) in modes:
        if w < 0.55 * phone_w:
            continue  # too blurry once upscaled to the phone
        ar = w / float(h)
        distortion = abs(ar - par) / par
        load = (w * h) / float(phone_w * phone_h)
        score = load * (1.0 + 2.0 * distortion)
        if best_score is None or score < best_score:
            best_score = score
            best = (w, h)
    if best is None:  # everything filtered out (very unusual phone aspect)
        best = modes[-1]
    return best


def describe_mode(w, h, phone_w, phone_h):
    """Human-readable annotation for one mode."""
    label = f"{w}x{h}"
    if not phone_w or not phone_h:
        return label
    par = phone_w / float(phone_h)
    ar = w / float(h)
    distortion = abs(ar - par) / par
    upscale = phone_w / float(w)
    load = (w * h) / float(phone_w * phone_h)
    return f"{label}  负载{load * 100:.0f}%  放大{upscale:.2f}x  失真{distortion * 100:.0f}%"


def get_phone_size():
    """Query the phone's physical screen size via adb. Returns (w, h) or None."""
    adb = getattr(server_config, "ADB_PATH", "adb")
    try:
        out = subprocess.run([adb, "shell", "wm", "size"],
                             capture_output=True, text=True, timeout=10)
        txt = (out.stdout or "") + "\n" + (out.stderr or "")
        m = re.search(r"(?:Physical|Override) size:\s*(\d+)x(\d+)", txt)
        if m:
            return int(m.group(1)), int(m.group(2))
        m = re.search(r"(\d{3,5})x(\d{3,5})", txt)
        if m:
            return int(m.group(1)), int(m.group(2))
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Win32 display helpers (ctypes, no pywin32 needed)
# ---------------------------------------------------------------------------
ENUM_CURRENT_SETTINGS = -1
DM_PELSWIDTH = 0x00080000
DM_PELSHEIGHT = 0x00100000
DISP_CHANGE_SUCCESSFUL = 0

user32 = ctypes.windll.user32


def _set_dpi_awareness():
    """Report physical resolutions.

    A DPI-unaware process sees scaled (logical) sizes, which would not match the
    sizes mss reports to the server. Make us DPI aware before enumerating.
    """
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


_set_dpi_awareness()


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class DEVMODE(ctypes.Structure):
    _fields_ = [
        ("dmDeviceName", ctypes.c_wchar * 32),
        ("dmSpecVersion", ctypes.c_ushort),
        ("dmDriverVersion", ctypes.c_ushort),
        ("dmSize", ctypes.c_ushort),
        ("dmDriverExtra", ctypes.c_ushort),
        ("dmFields", ctypes.c_ulong),
        # union: its largest member is the printer struct (8 shorts = 16 bytes),
        # so the union occupies 16 bytes — this is what makes sizeof(DEVMODE) == 220.
        ("dmOrientation", ctypes.c_short),
        ("dmPaperSize", ctypes.c_short),
        ("dmPaperLength", ctypes.c_short),
        ("dmPaperWidth", ctypes.c_short),
        ("dmScale", ctypes.c_short),
        ("dmCopies", ctypes.c_short),
        ("dmDefaultSource", ctypes.c_short),
        ("dmPrintQuality", ctypes.c_short),
        ("dmColor", ctypes.c_ushort),
        ("dmDuplex", ctypes.c_ushort),
        ("dmYResolution", ctypes.c_ushort),
        ("dmTTOption", ctypes.c_ushort),
        ("dmCollate", ctypes.c_ushort),
        ("dmFormName", ctypes.c_wchar * 32),
        ("dmLogPixels", ctypes.c_ushort),
        ("dmBitsPerPel", ctypes.c_ulong),
        ("dmPelsWidth", ctypes.c_ulong),
        ("dmPelsHeight", ctypes.c_ulong),
        ("dmDisplayFlags", ctypes.c_ulong),
        ("dmDisplayFrequency", ctypes.c_ulong),
        ("dmICMMethod", ctypes.c_ulong),
        ("dmICMIntent", ctypes.c_ulong),
        ("dmMediaType", ctypes.c_ulong),
        ("dmDitherType", ctypes.c_ulong),
        ("dmReserved1", ctypes.c_ulong),
        ("dmReserved2", ctypes.c_ulong),
        ("dmPanningWidth", ctypes.c_ulong),
        ("dmPanningHeight", ctypes.c_ulong),
    ]


class MONITORINFOEX(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", ctypes.c_ulong),
        ("szDevice", ctypes.c_wchar * 32),
    ]


_MONITORENUMPROC = ctypes.WINFUNCTYPE(
    ctypes.c_bool, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(RECT), ctypes.c_void_p
)

user32.EnumDisplayMonitors.argtypes = [
    wintypes.HDC, ctypes.POINTER(RECT), _MONITORENUMPROC, ctypes.c_void_p
]
user32.EnumDisplayMonitors.restype = ctypes.c_bool
user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFOEX)]
user32.GetMonitorInfoW.restype = ctypes.c_bool
user32.EnumDisplaySettingsExW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(DEVMODE), wintypes.DWORD
]
user32.EnumDisplaySettingsExW.restype = ctypes.c_bool
user32.ChangeDisplaySettingsExW.argtypes = [
    wintypes.LPCWSTR, ctypes.POINTER(DEVMODE), wintypes.HWND, wintypes.DWORD, ctypes.c_void_p
]
user32.ChangeDisplaySettingsExW.restype = ctypes.c_int


def list_monitors():
    """Return displays in the same order mss numbers them.

    Each entry: {"device", "left", "top", "width", "height", "primary", "index"}.
    `index` matches the server's MONITOR_INDEX (mss position among individual monitors).
    """
    monitors = []

    def _enum(hmonitor, hdc, lprect, lparam):
        info = MONITORINFOEX()
        info.cbSize = ctypes.sizeof(MONITORINFOEX)
        if user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            r = info.rcMonitor
            monitors.append({
                "device": info.szDevice,
                "left": r.left,
                "top": r.top,
                "width": r.right - r.left,
                "height": r.bottom - r.top,
                "primary": bool(info.dwFlags & 0x1),
            })
        return True

    cb = _MONITORENUMPROC(_enum)
    user32.EnumDisplayMonitors(0, None, cb, None)
    for i, m in enumerate(monitors):
        m["index"] = i
    return monitors


def list_resolutions(device):
    """Return the list of (width, height) modes supported by `device`, sorted."""
    modes = set()
    i = 0
    dm = DEVMODE()
    dm.dmSize = ctypes.sizeof(DEVMODE)
    while user32.EnumDisplaySettingsExW(device, i, ctypes.byref(dm), 0):
        modes.add((dm.dmPelsWidth, dm.dmPelsHeight))
        i += 1
    return sorted(modes)


def set_resolution(device, width, height):
    """Apply a new resolution to `device`. Returns True on success."""
    dm = DEVMODE()
    dm.dmSize = ctypes.sizeof(DEVMODE)
    dm.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT
    dm.dmPelsWidth = width
    dm.dmPelsHeight = height
    ret = user32.ChangeDisplaySettingsExW(device, ctypes.byref(dm), None, 0, None)
    return ret == DISP_CHANGE_SUCCESSFUL


# ---------------------------------------------------------------------------
# Configuration file (configuration.yaml)
# ---------------------------------------------------------------------------
DEFAULT_CONFIG = {
    "display": {
        "monitor_index": server_config.MONITOR_INDEX,
        "capture_width": server_config.CAPTURE_WIDTH,
        "capture_height": server_config.CAPTURE_HEIGHT,
        "capture_fps": server_config.CAPTURE_FPS,
    },
    "encoding": {
        "preset": server_config.H264_PRESET,
        "tune": server_config.H264_TUNE,
        "bitrate": server_config.H264_BITRATE,
        "keyframe_interval": server_config.KEYFRAME_INTERVAL,
        "profile": server_config.H264_PROFILE,
        "level": server_config.H264_LEVEL,
        "slices": server_config.H264_SLICES,
        "threads": server_config.H264_THREADS,
    },
    "network": {
        "video_port": server_config.VIDEO_PORT,
        "control_port": server_config.CONTROL_PORT,
        "host": server_config.HOST,
    },
    "android": {
        "default_server": server_config.ANDROID_DEFAULT_SERVER,
    },
}


def load_config():
    if yaml is None or not os.path.exists(CONFIG_PATH):
        return {k: dict(v) for k, v in DEFAULT_CONFIG.items()}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        merged = {k: dict(v) for k, v in DEFAULT_CONFIG.items()}
        for section, vals in data.items():
            if section in merged and isinstance(vals, dict):
                merged[section].update(vals)
        return merged
    except Exception:
        return {k: dict(v) for k, v in DEFAULT_CONFIG.items()}


def save_config(cfg):
    if yaml is None:
        raise RuntimeError("PyYAML is not installed; cannot write configuration.yaml")
    with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
        yaml.safe_dump(cfg, fh, sort_keys=False, allow_unicode=True)


# ---------------------------------------------------------------------------
# Server process controller
# ---------------------------------------------------------------------------
class ServerController:
    def __init__(self):
        self.proc = None
        self._reader = None
        self.log_lines = []
        self.status = {
            "running": False,
            "phone": "unknown",
            "capture": "",
            "resolution_change": "",
            "ffmpeg_error": "",
        }
        self._lock = threading.Lock()

    def start(self):
        if self.proc and self.proc.poll() is None:
            return
        self.stop()  # ensure clean
        if getattr(sys, "frozen", False):
            # Frozen build: the server is a standalone .exe sitting next to us.
            # (Running `server.py` as a subprocess would re-launch THIS .exe.)
            server_exe = os.path.join(os.path.dirname(sys.executable),
                                     "PocketDisplayServer.exe")
            self.proc = subprocess.Popen(
                [server_exe],
                cwd=os.path.dirname(sys.executable),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            # Launch the server in its own directory so `import config` resolves.
            self.proc = subprocess.Popen(
                # -u: unbuffered, so the log shows up in real time (stdout is a pipe).
                [sys.executable, "-u", "server.py"],
                cwd=SERVER_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def stop(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except Exception:
                pass
            try:
                self.proc.wait(timeout=3)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        # Clean up any orphaned FFmpeg the server spawned.
        try:
            subprocess.run(["taskkill", "/F", "/IM", "ffmpeg.exe"],
                           capture_output=True, timeout=5)
        except Exception:
            pass
        self.proc = None
        with self._lock:
            self.status["running"] = False

    def _read(self):
        try:
            for line in self.proc.stdout:
                self._ingest(line.rstrip("\n"))
        except Exception:
            pass
        with self._lock:
            self.status["running"] = False

    def _ingest(self, line):
        with self._lock:
            self.log_lines.append(line)
            if len(self.log_lines) > 2000:
                self.log_lines = self.log_lines[-2000:]
            low = line.lower()
            if "video client connected" in low:
                self.status["phone"] = "connected"
            elif "client disconnected" in low:
                self.status["phone"] = "disconnected"
            m = re.search(r"display resolution changed:\s*(\d+x\d+)\s*->\s*(\d+x\d+)", low)
            if m:
                self.status["resolution_change"] = f"{m.group(1)} -> {m.group(2)}"
                self.status["capture"] = m.group(2)
            m = re.search(r"resolution:\s*(\d+x\d+)", low)
            if m and not self.status["capture"]:
                self.status["capture"] = m.group(1)
            if "ffmpeg:" in low and ("error" in low or "fatal" in low):
                self.status["ffmpeg_error"] = line.strip()
        # Notify the GUI if a callback is registered.
        if self.on_log:
            self.on_log(line)

    on_log = None  # set by the GUI

    def get_log(self):
        with self._lock:
            return list(self.log_lines)

    def get_status(self):
        with self._lock:
            return dict(self.status)


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
PRESETS = ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium"]
TUNES = ["zerolatency", "film", "animation", "grain", "stillimage"]
PROFILES = ["baseline", "main", "high"]
LEVELS = ["3.0", "3.1", "4.0", "4.1", "4.2", "5.0"]


class ControlClientApp:
    def __init__(self, root):
        self.root = root
        self.controller = ServerController()
        self.controller.on_log = self._on_log
        self.cfg = load_config()
        self.monitors = list_monitors()
        self.modes = []
        self.best_mode = None
        self.phone_w = None
        self.phone_h = None

        root.title("PocketDisplay — Windows 控制客户端")
        root.geometry("680x760")
        root.minsize(620, 680)

        base_font = ("Microsoft YaHei UI", 10)
        self._bold_font = ("Microsoft YaHei UI", 10, "bold")
        root.option_add("*Font", base_font)
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except Exception:
            pass
        style.configure(".", font=base_font)
        style.configure("TLabelframe.Label", font=self._bold_font)
        style.configure("TButton", padding=(10, 3))

        # App icon (title bar + taskbar); prefer the multi-size .ico.
        self._icon_img = None
        try:
            if os.path.exists(ICON_ICO):
                root.iconbitmap(ICON_ICO)
            elif os.path.exists(ICON_PNG):
                self._icon_img = tk.PhotoImage(file=ICON_PNG)
                root.iconphoto(True, self._icon_img)
        except Exception:
            pass

        self._build_control_frame()
        self._build_display_frame()
        self._build_encoding_frame()
        self._build_log_frame()

        # After the log widget exists (it is used by _log_line).
        self._refresh_phone_size()
        self._refresh_status()
        self._poll()

    # ---- frames ----------------------------------------------------------
    def _build_control_frame(self):
        f = ttk.LabelFrame(self.root, text=" 服务器 ")
        f.pack(fill="x", padx=10, pady=(10, 5))

        self.btn_start = ttk.Button(f, text="启动", command=self._on_start, width=8)
        self.btn_stop = ttk.Button(f, text="停止", command=self._on_stop, width=8)
        self.btn_reconnect = ttk.Button(f, text="一键重连", command=self._on_reconnect, width=10)
        self.btn_start.grid(row=0, column=0, padx=(8, 4), pady=8, sticky="w")
        self.btn_stop.grid(row=0, column=1, padx=4, pady=8, sticky="w")
        self.btn_reconnect.grid(row=0, column=2, padx=4, pady=8, sticky="w")

        self.lbl_state = ttk.Label(f, text="状态: 未知", font=self._bold_font)
        self.lbl_state.grid(row=0, column=3, padx=(16, 8), pady=8, sticky="e")
        f.columnconfigure(3, weight=1)

        self.lbl_phone = ttk.Label(f, text="手机: 未知")
        self.lbl_phone.grid(row=1, column=0, columnspan=2, padx=8, pady=(0, 8), sticky="w")
        self.lbl_cap = ttk.Label(f, text="捕获分辨率: -")
        self.lbl_cap.grid(row=1, column=2, padx=4, pady=(0, 8), sticky="w")
        self.lbl_reschg = ttk.Label(f, text="")
        self.lbl_reschg.grid(row=1, column=3, padx=8, pady=(0, 8), sticky="w")

    def _build_display_frame(self):
        f = ttk.LabelFrame(self.root, text=" 显示器与分辨率 ")
        f.pack(fill="x", padx=10, pady=5)

        ttk.Label(f, text="副屏显示器").grid(row=0, column=0, sticky="e", padx=(8, 10), pady=4)
        self.mon_var = tk.StringVar()
        self.mon_cb = ttk.Combobox(f, textvariable=self.mon_var, state="readonly", width=30)
        self.mon_cb.grid(row=0, column=1, columnspan=2, sticky="w", padx=2, pady=4)

        ttk.Label(f, text="分辨率").grid(row=1, column=0, sticky="e", padx=(8, 10), pady=4)
        self.res_var = tk.StringVar()
        self.res_cb = ttk.Combobox(f, textvariable=self.res_var, state="readonly", width=14)
        self.res_cb.grid(row=1, column=1, sticky="w", padx=2, pady=4)
        ttk.Button(f, text="读取手机尺寸", command=self._refresh_phone_size).grid(
            row=1, column=2, sticky="w", padx=(16, 2), pady=4)

        # Own full-width row so the annotation is never clipped.
        self.lbl_mode_info = ttk.Label(f, text="", foreground="#555555")
        self.lbl_mode_info.grid(row=2, column=0, columnspan=3, sticky="w", padx=10, pady=(0, 2))

        ttk.Button(f, text="应用分辨率", command=self._on_apply_resolution).grid(
            row=3, column=0, sticky="w", padx=(8, 4), pady=6)
        ttk.Button(f, text="应用推荐 (低延迟)", command=self._on_apply_recommended).grid(
            row=3, column=1, sticky="w", padx=4, pady=6)
        self.lbl_res_status = ttk.Label(f, text="")
        self.lbl_res_status.grid(row=3, column=2, sticky="w", padx=8, pady=6)

        self.lbl_phone_size = ttk.Label(f, text="手机屏幕: 未知", foreground="#555555")
        self.lbl_phone_size.grid(row=4, column=0, columnspan=3, sticky="w", padx=10, pady=(0, 8))

        f.columnconfigure(2, weight=1)

        self.mon_cb.bind("<<ComboboxSelected>>", lambda e: self._fill_resolutions())
        self.res_cb.bind("<<ComboboxSelected>>", lambda e: self._update_mode_info())
        self._fill_monitors()
        self._fill_resolutions()

    def _build_encoding_frame(self):
        f = ttk.LabelFrame(self.root, text=" 编码参数（应用后重启服务器） ")
        f.pack(fill="x", padx=10, pady=5)

        ttk.Label(f, text="FPS").grid(row=0, column=0, sticky="e", padx=(8, 10), pady=4)
        self.fps_var = tk.StringVar(value=str(self.cfg["display"]["capture_fps"]))
        ttk.Entry(f, textvariable=self.fps_var, width=7).grid(row=0, column=1, sticky="w", pady=4)

        ttk.Label(f, text="码率").grid(row=0, column=2, sticky="e", padx=(18, 10), pady=4)
        self.bit_var = tk.StringVar(value=str(self.cfg["encoding"]["bitrate"]))
        ttk.Entry(f, textvariable=self.bit_var, width=9).grid(row=0, column=3, sticky="w", pady=4)

        ttk.Label(f, text="Preset").grid(row=1, column=0, sticky="e", padx=(8, 10), pady=4)
        self.preset_var = tk.StringVar(value=self.cfg["encoding"]["preset"])
        ttk.Combobox(f, textvariable=self.preset_var, values=PRESETS, state="readonly",
                     width=12).grid(row=1, column=1, sticky="w", pady=4)

        ttk.Label(f, text="Tune").grid(row=1, column=2, sticky="e", padx=(18, 10), pady=4)
        self.tune_var = tk.StringVar(value=self.cfg["encoding"]["tune"])
        ttk.Combobox(f, textvariable=self.tune_var, values=TUNES, state="readonly",
                     width=12).grid(row=1, column=3, sticky="w", pady=4)

        ttk.Label(f, text="Profile").grid(row=2, column=0, sticky="e", padx=(8, 10), pady=4)
        self.profile_var = tk.StringVar(value=self.cfg["encoding"]["profile"])
        ttk.Combobox(f, textvariable=self.profile_var, values=PROFILES, state="readonly",
                     width=12).grid(row=2, column=1, sticky="w", pady=4)

        ttk.Label(f, text="Level").grid(row=2, column=2, sticky="e", padx=(18, 10), pady=4)
        self.level_var = tk.StringVar(value=self.cfg["encoding"]["level"])
        ttk.Combobox(f, textvariable=self.level_var, values=LEVELS, state="readonly",
                     width=12).grid(row=2, column=3, sticky="w", pady=4)

        ttk.Button(f, text="应用并重启", command=self._on_apply_encoding).grid(
            row=3, column=0, sticky="w", padx=8, pady=(4, 8))
        self.lbl_enc_status = ttk.Label(f, text="")
        self.lbl_enc_status.grid(row=3, column=1, columnspan=3, sticky="w", padx=4, pady=(4, 8))

    def _build_log_frame(self):
        f = ttk.LabelFrame(self.root, text=" 服务器日志 ")
        f.pack(fill="both", expand=True, padx=10, pady=(5, 10))
        self.log = scrolledtext.ScrolledText(f, height=12, state="disabled",
                                             font=("Microsoft YaHei UI", 9),
                                             relief="flat", background="#fafafa")
        self.log.pack(fill="both", expand=True, padx=6, pady=6)

    # ---- helpers ---------------------------------------------------------
    def _fill_monitors(self):
        labels = []
        for m in self.monitors:
            tag = "主屏" if m["primary"] else "副屏"
            labels.append(f"[{m['index']}] {tag} {m['device']} {m['width']}x{m['height']}")
        self.mon_cb["values"] = labels
        cur = self.cfg["display"]["monitor_index"]
        for i, m in enumerate(self.monitors):
            if m["index"] == cur:
                self.mon_cb.current(i)
                break
        else:
            if labels:
                self.mon_cb.current(0)

    def _selected_monitor(self):
        idx = self.mon_cb.current()
        if 0 <= idx < len(self.monitors):
            return self.monitors[idx]
        return None

    def _fill_resolutions(self):
        m = self._selected_monitor()
        if not m:
            self.res_cb["values"] = []
            self.modes = []
            return
        try:
            modes = list_resolutions(m["device"])
        except Exception as e:
            self.res_cb["values"] = []
            self.modes = []
            self.lbl_res_status.config(text=f"读取失败: {e}")
            return
        self.modes = modes
        # Keep values as plain WxH (parsed elsewhere); annotations go in a label.
        self.best_mode = recommend_resolution(modes, self.phone_w, self.phone_h)
        labels = [f"{w}x{h}" for (w, h) in modes]
        self.res_cb["values"] = labels
        if self.best_mode:
            cur = f"{self.best_mode[0]}x{self.best_mode[1]}"
            if cur in labels:
                self.res_cb.set(cur)
        elif labels:
            self.res_cb.current(0)
        self._update_mode_info()

    def _update_mode_info(self):
        m = re.match(r"(\d+)x(\d+)", self.res_var.get().strip())
        if not m:
            self.lbl_mode_info.config(text="")
            return
        w, h = int(m.group(1)), int(m.group(2))
        txt = describe_mode(w, h, self.phone_w, self.phone_h)
        if self.best_mode and (w, h) == self.best_mode:
            txt = "★推荐(低延迟)  " + txt
        self.lbl_mode_info.config(text=txt)

    def _on_apply_recommended(self):
        if not self.best_mode:
            self.lbl_res_status.config(text="无法推荐：请先点『读取手机尺寸』")
            return
        w, h = self.best_mode
        self.res_var.set(f"{w}x{h}")
        self._update_mode_info()
        self._on_apply_resolution()

    # ---- actions ---------------------------------------------------------
    def _on_start(self):
        try:
            self.controller.start()
            self._log_line(">>> 启动服务器...")
        except Exception as e:
            self._log_line(f">>> 启动失败: {e}")

    def _on_stop(self):
        self.controller.stop()
        self._log_line(">>> 停止服务器")

    def _on_reconnect(self):
        """Force the phone to reconnect: restart the server so the video client
        is dropped and the phone's auto-reconnect (+ decoder reset) kicks in."""
        self._log_line(">>> 一键重连：重启服务器，手机约 2 秒后自动重连")
        self.controller.stop()
        time.sleep(0.5)
        self.controller.start()

    def _refresh_phone_size(self):
        sz = get_phone_size()
        if sz:
            w, h = sz
            # adb reports the native portrait size; the app runs in landscape.
            self.phone_w, self.phone_h = max(w, h), min(w, h)
            ar = (self.phone_w / self.phone_h) if self.phone_h else 0.0
            self.lbl_phone_size.config(
                text=f"手机屏幕(横屏): {self.phone_w}x{self.phone_h}  宽高比 {ar:.2f}:1")
            self._log_line(f">>> 手机屏幕 {self.phone_w}x{self.phone_h} 宽高比 {ar:.2f}:1")
        else:
            self.phone_w = self.phone_h = None
            self.lbl_phone_size.config(text="手机屏幕: 未知 (adb 不可用或未连接)")
        self._fill_resolutions()

    def _on_apply_resolution(self):
        m = self._selected_monitor()
        val = self.res_var.get().strip()
        if not m or "x" not in val:
            self.lbl_res_status.config(text="请选择显示器与分辨率")
            return
        w, h = val.split("x")
        try:
            w, h = int(w), int(h)
        except ValueError:
            self.lbl_res_status.config(text="分辨率格式错误")
            return
        # Update monitor_index in config so the server captures this display.
        self.cfg["display"]["monitor_index"] = m["index"]
        self.cfg["display"]["capture_width"] = w
        self.cfg["display"]["capture_height"] = h
        try:
            save_config(self.cfg)
        except Exception as e:
            self.lbl_res_status.config(text=f"写配置失败: {e}")
            return
        ok = set_resolution(m["device"], w, h)
        if ok:
            self.lbl_res_status.config(text=f"已切换 {m['device']} -> {w}x{h} (服务器约3秒后生效)")
            self._log_line(f">>> 设置分辨率 {w}x{h} 于 {m['device']}")
        else:
            self.lbl_res_status.config(text=f"切换失败 (驱动可能不支持 {w}x{h})")

    def _on_apply_encoding(self):
        try:
            fps = int(self.fps_var.get())
        except ValueError:
            self.lbl_enc_status.config(text="FPS 必须是整数")
            return
        self.cfg["display"]["capture_fps"] = fps
        self.cfg["encoding"]["bitrate"] = self.bit_var.get().strip()
        self.cfg["encoding"]["preset"] = self.preset_var.get()
        self.cfg["encoding"]["tune"] = self.tune_var.get()
        self.cfg["encoding"]["profile"] = self.profile_var.get()
        self.cfg["encoding"]["level"] = self.level_var.get()
        try:
            save_config(self.cfg)
        except Exception as e:
            self.lbl_enc_status.config(text=f"写配置失败: {e}")
            return
        self.lbl_enc_status.config(text="已保存，正在重启服务器...")
        self.controller.stop()
        time.sleep(0.5)
        self.controller.start()
        self._log_line(">>> 编码参数已应用，服务器重启")

    # ---- logging / polling ----------------------------------------------
    def _on_log(self, line):
        # Called from the reader thread; marshal to the GUI thread.
        self.root.after(0, self._log_line, line)

    def _log_line(self, line):
        self.log.configure(state="normal")
        self.log.insert("end", line + "\n")
        self.log.configure(state="disabled")
        self.log.see("end")

    def _refresh_status(self):
        st = self.controller.get_status()
        running = bool(self.controller.proc and self.controller.proc.poll() is None)
        self.lbl_state.config(text=f"状态: {'运行中' if running else '已停止'}")
        self.lbl_phone.config(text=f"手机: {st['phone']}")
        cap = st["capture"] or "-"
        self.lbl_cap.config(text=f"捕获分辨率: {cap}")
        self.lbl_reschg.config(text=f"分辨率变化: {st['resolution_change']}" if st["resolution_change"] else "")

    def _poll(self):
        self._refresh_status()
        self.root.after(500, self._poll)


def main():
    root = tk.Tk()
    ControlClientApp(root)
    # Make sure the window actually shows on top (pythonw has no console to
    # steal focus, and on some setups the first frame can paint behind others).
    root.update_idletasks()
    root.lift()
    root.attributes("-topmost", True)
    root.after(800, lambda: root.attributes("-topmost", False))
    root.mainloop()


if __name__ == "__main__":
    main()
