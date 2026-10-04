"""Windows system-tray icon, implemented with ctypes only (no pystray/pywin32).

A hidden message-only window owns a Shell_NotifyIcon entry, so the app can be
collapsed to the bottom-right corner and still be restored:

  * left click / double click  -> show + restore the main window
  * right click                -> menu: 显示主窗口 / 退出

Everything runs on its own daemon thread with its own message loop, so it never
interferes with pywebview's WinForms message loop.
"""

import ctypes
import os
import threading
from ctypes import wintypes

user32 = ctypes.windll.user32
shell32 = ctypes.windll.shell32
kernel32 = ctypes.windll.kernel32

# messages
WM_DESTROY = 0x0002
WM_COMMAND = 0x0111
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_APP = 0x8000
WM_TRAY = WM_APP + 1

# Shell_NotifyIcon
NIM_ADD = 0x00000000
NIM_DELETE = 0x00000002
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004

# icons / loading
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010
LR_DEFAULTSIZE = 0x00000040

# ShowWindow
SW_HIDE = 0
SW_SHOW = 5
SW_RESTORE = 9

# window styles (these two are easy to mix up!)
GWL_STYLE = -16
WS_MAXIMIZEBOX = 0x00010000
WS_MINIMIZEBOX = 0x00020000
WS_SYSMENU = 0x00080000

# subclassing the main window so the maximize button can be repurposed
GWLP_WNDPROC = -4
WM_SYSCOMMAND = 0x0112
SC_MAXIMIZE = 0xF030
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020

# menu
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100
MF_STRING = 0x0000

CMD_SHOW = 1001
CMD_EXIT = 1002

HWND_MESSAGE = wintypes.HWND(-3)


def _is_64bit():
    return ctypes.sizeof(ctypes.c_void_p) == 8


_LRESULT = ctypes.c_longlong if _is_64bit() else ctypes.c_long
WNDPROC = ctypes.WINFUNCTYPE(_LRESULT, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASS(ctypes.Structure):
    """WNDCLASSW — ctypes 3.11 has no built-in WNDCLASS on Windows."""
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HANDLE),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class NOTIFYICONDATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoClass", wintypes.WCHAR * 64),
    ]


# ---------------------------------------------------------------------------
# Explicit Win32 signatures. Without these, ctypes truncates 64-bit handles to
# 32-bit ints and CreateWindowExW raises OverflowError on the module handle.
# ---------------------------------------------------------------------------
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASS)]
user32.RegisterClassW.restype = wintypes.WORD

user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p,
]
user32.CreateWindowExW.restype = wintypes.HWND

user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                 wintypes.LPARAM]
user32.DefWindowProcW.restype = _LRESULT

user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                               wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.TranslateMessage.restype = wintypes.BOOL
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = _LRESULT

user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL

user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                              ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.LoadImageW.restype = wintypes.HANDLE

user32.CreatePopupMenu.restype = wintypes.HMENU
user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t,
                               wintypes.LPCWSTR]
user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                  wintypes.HWND]
user32.TrackPopupMenu.restype = wintypes.UINT
user32.DestroyMenu.argtypes = [wintypes.HMENU]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                wintypes.LPARAM]

user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = wintypes.LONG
user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
user32.SetWindowLongW.restype = wintypes.LONG
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                wintypes.UINT]
user32.SetWindowPos.restype = wintypes.BOOL
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindowVisible.argtypes = [wintypes.HWND]

shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD,
                                      ctypes.POINTER(NOTIFYICONDATA)]
shell32.Shell_NotifyIconW.restype = wintypes.BOOL

kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE


class TrayIcon(object):
    """A tray icon that restores (or quits) the app through callbacks."""

    TIP = "PocketDisplay 控制客户端"

    def __init__(self, ico_path, get_main_hwnd, on_show=None, on_exit=None):
        self.ico_path = ico_path
        self.get_main_hwnd = get_main_hwnd
        self.on_show = on_show
        self.on_exit = on_exit
        self.hwnd = None
        self._nid = None
        self._wndproc = None
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._thread = None

    # ------------------------------------------------------------------ setup
    def start(self, timeout=5.0):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self._ready.wait(timeout)

    def _run(self):
        try:
            self._create_window()
            self._add_icon()
            self._ready.set()
            self._message_loop()
        except Exception:
            self._ready.set()  # never leave the caller blocked

    def _create_window(self):
        hinst = kernel32.GetModuleHandleW(None)

        def wndproc(hwnd, msg, wparam, lparam):
            if msg == WM_TRAY:
                self._on_tray_message(lparam)
                return 0
            if msg == WM_COMMAND:
                self._on_command(wparam)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        # keep a reference: the callback must outlive this function
        self._wndproc = WNDPROC(wndproc)

        wndclass = WNDCLASS()
        wndclass.lpfnWndProc = self._wndproc
        wndclass.hInstance = hinst
        wndclass.lpszClassName = "PocketDisplayTray"
        user32.RegisterClassW(ctypes.byref(wndclass))

        hwnd = user32.CreateWindowExW(
            0, "PocketDisplayTray", "PocketDisplay Tray", 0,
            0, 0, 0, 0, HWND_MESSAGE, None, hinst, None)
        if not hwnd:
            # message-only windows can be refused; fall back to a normal hidden one
            hwnd = user32.CreateWindowExW(
                0, "PocketDisplayTray", "PocketDisplay Tray", 0,
                0, 0, 0, 0, None, None, hinst, None)
        if not hwnd:
            raise ctypes.WinError()
        self.hwnd = hwnd

    def _add_icon(self):
        hicon = user32.LoadImageW(None, self.ico_path, IMAGE_ICON, 0, 0,
                                  LR_LOADFROMFILE | LR_DEFAULTSIZE)
        if not hicon:
            raise ctypes.WinError()

        nid = NOTIFYICONDATA()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATA)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = hicon
        nid.szTip = self.TIP
        if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
            raise ctypes.WinError()
        self._nid = nid

    # ------------------------------------------------------------- tray logic
    def _on_tray_message(self, lparam):
        # Legacy (non-versioned) notify data passes the mouse message as-is.
        msg = lparam & 0xFFFF
        if msg in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
            self.show_window()
        elif msg == WM_RBUTTONUP:
            self._show_menu()

    def _on_command(self, wparam):
        cmd = wparam & 0xFFFF
        if cmd == CMD_SHOW:
            self.show_window()
        elif cmd == CMD_EXIT and self.on_exit:
            self.on_exit()

    def _show_menu(self):
        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(menu, MF_STRING, CMD_SHOW, "显示主窗口")
        user32.AppendMenuW(menu, MF_STRING, CMD_EXIT, "退出")
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        # required so the menu closes properly when clicking elsewhere
        user32.SetForegroundWindow(self.hwnd)
        cmd = user32.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD,
                                    pt.x, pt.y, 0, self.hwnd, None)
        user32.PostMessageW(self.hwnd, 0, 0, 0)
        user32.DestroyMenu(menu)
        if cmd == CMD_SHOW:
            self.show_window()
        elif cmd == CMD_EXIT:
            self._exit()

    def _exit(self):
        if self.on_exit:
            self.on_exit()
        else:
            os._exit(0)

    def _message_loop(self):
        msg = wintypes.MSG()
        while True:
            r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r in (0, -1):
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    # ---------------------------------------------------------------- actions
    def show_window(self):
        hwnd = self.get_main_hwnd() if self.get_main_hwnd else None
        if hwnd:
            user32.ShowWindow(hwnd, SW_SHOW)
            user32.ShowWindow(hwnd, SW_RESTORE)
            user32.SetForegroundWindow(hwnd)
        if self.on_show:
            self.on_show()

    def hide_window(self):
        """Collapse the main window into the tray (bottom-right corner)."""
        hwnd = self.get_main_hwnd() if self.get_main_hwnd else None
        if not hwnd:
            return False
        user32.ShowWindow(hwnd, SW_HIDE)
        return True

    def remove(self):
        with self._lock:
            if self._nid is not None:
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
                self._nid = None


user32.DefWindowProcW.restype = _LRESULT


def ensure_titlebar_buttons(hwnd):
    """Make sure both minimize and maximize buttons exist and work."""
    if not hwnd:
        return False
    style = user32.GetWindowLongW(hwnd, GWL_STYLE)
    if not style:
        return False
    new_style = style | WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_SYSMENU
    if new_style != style:
        user32.SetWindowLongW(hwnd, GWL_STYLE, new_style)
        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                            SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER |
                            SWP_NOACTIVATE | SWP_FRAMECHANGED)
    return True


_LONG_PTR = ctypes.c_longlong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_long
_maximize_procs = []      # keep the ctypes callbacks alive
_maximize_installed = {}  # hwnd -> original wndproc value


def intercept_maximize(hwnd, callback):
    """Repurpose the maximize button: instead of maximizing, call `callback`.

    Works by subclassing the window and swallowing WM_SYSCOMMAND/SC_MAXIMIZE
    while forwarding everything else to the original window procedure.
    """
    if not hwnd or hwnd in _maximize_installed:
        return False
    if not hasattr(user32, "GetWindowLongPtrW"):
        return False

    get_ptr = user32.GetWindowLongPtrW
    get_ptr.argtypes = [wintypes.HWND, ctypes.c_int]
    get_ptr.restype = _LONG_PTR
    set_ptr = user32.SetWindowLongPtrW
    set_ptr.argtypes = [wintypes.HWND, ctypes.c_int, _LONG_PTR]
    set_ptr.restype = _LONG_PTR
    call_proc = user32.CallWindowProcW
    call_proc.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT,
                          wintypes.WPARAM, wintypes.LPARAM]
    call_proc.restype = _LRESULT

    orig = get_ptr(hwnd, GWLP_WNDPROC)
    if not orig:
        return False

    def proc(h, msg, wparam, lparam):
        # the command id sits in the low word, masked by 0xFFF0 in the docs
        if msg == WM_SYSCOMMAND and (wparam & 0xFFF0) == SC_MAXIMIZE:
            callback()
            return 0
        return call_proc(ctypes.c_void_p(orig), h, msg, wparam, lparam)

    wndproc = WNDPROC(proc)
    _maximize_procs.append(wndproc)
    _maximize_installed[hwnd] = orig
    set_ptr(hwnd, GWLP_WNDPROC, ctypes.cast(wndproc, ctypes.c_void_p).value)
    return True
