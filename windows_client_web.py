"""PocketDisplay Windows client - TDesign web UI inside a native window.

The interface is a local HTML/Vue page styled with the real TDesign component
library (assets are vendored locally, so it works offline). Python exposes an
Api object to the page through pywebview's js_api bridge.

The display/config/server logic is reused from windows_client.py.
"""

import functools
import http.server
import os
import re
import socketserver
import sys
import threading
import time

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import webview  # noqa: E402

import tray  # noqa: E402

from windows_client import (  # noqa: E402
    list_monitors,
    list_resolutions,
    set_resolution,
    load_config,
    save_config,
    ServerController,
    recommend_resolution,
    describe_mode,
    get_phone_size,
)

ICON_ICO = os.path.join(ROOT_DIR, "assets", "icon.ico")

# Tray icon (bottom-right corner), created once the main window exists.
TRAY = None

# Initial window size. The page reports its own content height right after load
# and the window is resized to fit (see Api.fit_window), so the initial height
# just needs to be generous.
WIN_W, WIN_H = 1080, 900


def _work_area_height():
    """Usable screen height in physical px (so fit_window never runs off-screen)."""
    try:
        import ctypes
        from ctypes import wintypes
        rect = wintypes.RECT()
        # SPI_GETWORKAREA = 0x0030
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
            return int(rect.bottom - rect.top)
    except Exception:
        pass
    return 1000


def _bitrate_to_number(value):
    """'4M' -> '4', '4000K' -> '3.9' so the UI field is a plain number."""
    s = str(value or "").strip()
    m = re.match(r"^(\d+(?:\.\d+)?)\s*([MmKk]?)$", s)
    if not m:
        return s
    num = float(m.group(1))
    if m.group(2).lower() == "k":
        num = num / 1024.0
    return "%g" % round(num, 1)


def _bitrate_to_config(value, fallback="4M"):
    """'4' -> '4M' (the UI's implicit unit is Mbps); pass explicit units through."""
    s = str(value or "").strip()
    if not s:
        return fallback
    if re.match(r"^\d+(?:\.\d+)?$", s):
        return s + "M"
    return s


class Api:
    """Methods exposed to the page as window.pywebview.api.<name>()."""

    # window size bookkeeping used by fit_window()
    _win_h = WIN_H
    _max_h = 20000

    def __init__(self):
        self.controller = ServerController()
        self.cfg = load_config()
        self.monitors = list_monitors()
        self.phone_w = None
        self.phone_h = None
        self.refresh_phone()

    # ---- phone -----------------------------------------------------------
    def refresh_phone(self):
        """adb reports the native portrait size; the app runs in landscape."""
        size = get_phone_size()
        if size:
            w, h = size
            self.phone_w, self.phone_h = max(w, h), min(w, h)
        else:
            self.phone_w = self.phone_h = None
        return self._phone()

    def _phone(self):
        if self.phone_w and self.phone_h:
            return {
                "w": self.phone_w,
                "h": self.phone_h,
                "ar": round(self.phone_w / float(self.phone_h), 2),
            }
        return None

    # ---- displays --------------------------------------------------------
    def _monitor(self, index):
        for m in self.monitors:
            if m["index"] == index:
                return m
        return None

    def get_monitors(self):
        self.monitors = list_monitors()
        return [{
            "index": m["index"],
            "device": m["device"],
            "label": "{} {} {}x{}".format(
                "主屏" if m["primary"] else "副屏", m["device"], m["width"], m["height"]),
            "width": m["width"],
            "height": m["height"],
            "primary": m["primary"],
        } for m in self.monitors]

    def get_resolutions(self, index):
        self.monitors = list_monitors()
        mon = self._monitor(index)
        if not mon:
            return {"modes": [], "best": None, "phone": self._phone(), "current": ""}
        modes = list_resolutions(mon["device"])
        best = recommend_resolution(modes, self.phone_w, self.phone_h)
        out = []
        for (w, h) in modes:
            desc = describe_mode(w, h, self.phone_w, self.phone_h)
            if best and (w, h) == best:
                desc = "★推荐  " + desc
            out.append({"w": w, "h": h, "desc": desc})
        return {
            "modes": out,
            "best": {"w": best[0], "h": best[1]} if best else None,
            "phone": self._phone(),
            "current": "{}x{}".format(mon["width"], mon["height"]),
        }

    def apply_resolution(self, index, width, height):
        mon = self._monitor(index)
        if not mon:
            return {"ok": False, "msg": "未找到该显示器"}
        w, h = int(width), int(height)
        self.cfg["display"]["monitor_index"] = mon["index"]
        self.cfg["display"]["capture_width"] = w
        self.cfg["display"]["capture_height"] = h
        try:
            save_config(self.cfg)
        except Exception as exc:
            return {"ok": False, "msg": "写配置失败: {}".format(exc)}
        if set_resolution(mon["device"], w, h):
            return {"ok": True,
                    "msg": "已切换 {} -> {}x{}，服务器正在自动重启捕获，安卓设备自动重连"
                           .format(mon["device"], w, h)}
        return {"ok": False, "msg": "切换失败，驱动可能不支持 {}x{}".format(w, h)}

    def apply_recommended(self, index):
        mon = self._monitor(index)
        if not mon:
            return {"ok": False, "msg": "未找到该显示器"}
        modes = list_resolutions(mon["device"])
        best = recommend_resolution(modes, self.phone_w, self.phone_h)
        if not best:
            return {"ok": False, "msg": "无法推荐，请先点『读取手机尺寸』"}
        return self.apply_resolution(index, best[0], best[1])

    # ---- encoding --------------------------------------------------------
    def get_config(self):
        disp = self.cfg["display"]
        enc = self.cfg["encoding"]
        return {
            "fps": disp["capture_fps"],
            "bitrate": _bitrate_to_number(enc["bitrate"]),
            "preset": enc["preset"],
            "tune": enc["tune"],
            "profile": enc["profile"],
            "level": enc["level"],
        }

    def save_encoding(self, fps, bitrate, preset, tune, profile, level):
        try:
            fps = int(fps)
        except (TypeError, ValueError):
            return {"ok": False, "msg": "FPS 必须是整数"}
        self.cfg["display"]["capture_fps"] = fps
        self.cfg["encoding"]["bitrate"] = _bitrate_to_config(bitrate,
                                                           self.cfg["encoding"].get("bitrate"))
        self.cfg["encoding"]["preset"] = preset
        self.cfg["encoding"]["tune"] = tune
        self.cfg["encoding"]["profile"] = profile
        self.cfg["encoding"]["level"] = level
        try:
            save_config(self.cfg)
        except Exception as exc:
            return {"ok": False, "msg": "写配置失败: {}".format(exc)}
        self.controller.stop()
        time.sleep(0.5)
        self.controller.start()
        return {"ok": True, "msg": "已保存并重启服务器"}

    # ---- server ----------------------------------------------------------
    def start_server(self):
        try:
            self.controller.start()
            return {"ok": True, "msg": "服务器已启动"}
        except Exception as exc:
            return {"ok": False, "msg": "启动失败: {}".format(exc)}

    def stop_server(self):
        self.controller.stop()
        return {"ok": True, "msg": "服务器已停止"}

    def fit_window(self, delta):
        """Resize the window by `delta` physical px so the content fits exactly.

        The page measures (content height - viewport height) in CSS px and sends
        it scaled by devicePixelRatio; adjusting by a delta keeps this correct
        regardless of DPI scaling or how pywebview counts the window frame.
        """
        try:
            delta = int(delta)
        except (TypeError, ValueError):
            return {"ok": False}
        if abs(delta) < 2:
            return {"ok": True, "msg": "already fits"}
        win = webview.windows[0]
        new_h = int(self._win_h + delta)
        new_h = max(420, min(new_h, self._max_h))
        if new_h == self._win_h:
            return {"ok": True, "msg": "clamped"}
        try:
            win.resize(int(win.width), new_h)
            self._win_h = new_h
            return {"ok": True}
        except Exception:
            return {"ok": False}

    def minimize_to_tray(self):
        """Collapse the window into the bottom-right tray."""
        okay = TRAY.hide_window() if TRAY else False
        return {"ok": bool(okay),
                "msg": "已缩到右下角托盘，点击/双击托盘图标可恢复" if okay
                       else "托盘尚未就绪，请稍等后再试"}

    def reconnect(self):
        self.controller.stop()
        time.sleep(0.5)
        self.controller.start()
        return {"ok": True, "msg": "已重启服务器，手机约 2 秒后自动重连"}

    def get_status(self):
        st = self.controller.get_status()
        running = bool(self.controller.proc and self.controller.proc.poll() is None)
        return {
            "running": running,
            "phone": st.get("phone", "unknown"),
            "capture": st.get("capture", ""),
            "resolution_change": st.get("resolution_change", ""),
        }

    def get_log(self):
        return self.controller.get_log()[-300:]


# ---------------------------------------------------------------------------
# local static server for the UI
# ---------------------------------------------------------------------------
class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT_DIR, **kwargs)

    def log_message(self, fmt, *args):
        pass

    def end_headers(self):
        # Never cache the UI: WebView2 would otherwise keep serving an old
        # index.html/app.js after an edit, making UI changes look "not applied".
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()


def _start_http_server():
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", 0), _QuietHandler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def _apply_icon_once():
    """Set the window icon via raw WM_SETICON (pure ctypes — no pythonnet,
    whose `import clr` can deadlock when called from a worker thread while
    the WinForms message loop runs on the GUI thread)."""
    import ctypes
    try:
        user32 = ctypes.windll.user32
        handle = webview.windows[0].native.Handle
        try:
            hwnd = int(handle.ToInt64())
        except AttributeError:
            hwnd = int(handle)
        if not hwnd:
            return False
        hicon = user32.LoadImageW(None, ICON_ICO, 1, 32, 32, 0x10)  # IMAGE_ICON, LR_LOADFROMFILE
        if not hicon:
            return False
        user32.SendMessageW(hwnd, 0x80, 1, hicon)  # WM_SETICON, ICON_BIG
        user32.SendMessageW(hwnd, 0x80, 0, hicon)  # WM_SETICON, ICON_SMALL
        # SWP_FRAMECHANGED makes the taskbar button pick the new icon up.
        SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_FRAMECHANGED = 0x01, 0x02, 0x04, 0x20
        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                            SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_FRAMECHANGED)
        return True
    except Exception:
        return False


def _main_hwnd():
    """Native HWND of the pywebview window, or None before it exists."""
    try:
        handle = webview.windows[0].native.Handle
        try:
            return int(handle.ToInt64())
        except AttributeError:
            return int(handle)
    except Exception:
        return None


def _init_tray():
    """Create the tray icon and repurpose the maximize button.

    The maximize button (and double-clicking the title bar) now collapses the
    window into the bottom-right tray instead of maximizing; clicking the tray
    icon brings it back.
    """
    global TRAY
    if TRAY is not None or not os.path.exists(ICON_ICO):
        return

    def on_exit():
        try:
            webview.windows[0].destroy()
        except Exception:
            os._exit(0)

    icon = tray.TrayIcon(ICON_ICO, get_main_hwnd=_main_hwnd, on_exit=on_exit)
    if icon.start(timeout=5.0) and icon.hwnd:
        TRAY = icon
        print("tray icon ready", flush=True)
    else:
        print("tray icon failed", flush=True)

    hwnd = _main_hwnd()
    if hwnd:
        # keep both title-bar buttons usable, then take over maximize
        tray.ensure_titlebar_buttons(hwnd)
        if tray.intercept_maximize(hwnd, lambda: icon.hide_window()):
            print("maximize button -> minimize to tray", flush=True)


def _set_window_icon():
    """Set the title-bar/taskbar icon and keep re-applying it.

    WebView2's async init clears a window icon that was set too early
    (observed: icon present at t+3s, gone by t+6s), so a low-frequency
    keeper thread re-applies it; trivial cost and guarantees the icon.
    """
    ok = False
    for _ in range(50):  # wait for the native window to exist
        ok = _apply_icon_once()
        if ok:
            break
        time.sleep(0.2)
    if not ok:
        print("could not set window icon", flush=True)
        return
    print("window icon set", flush=True)

    try:
        _init_tray()
    except Exception as exc:
        print("tray init error: {}".format(exc), flush=True)

    # The VBS launcher starts us with SW_HIDE in the child's STARTUPINFO, and
    # WinForms' first Show can inherit it, leaving the window hidden. Force it
    # visible (and foreground) during startup; keep the icon only afterwards
    # so we never fight the user minimizing the window.
    import ctypes
    user32 = ctypes.windll.user32
    for _ in range(10):
        _apply_icon_once()  # also covers the WebView2-init icon wipe
        try:
            handle = webview.windows[0].native.Handle
            try:
                hwnd = int(handle.ToInt64())
            except AttributeError:
                hwnd = int(handle)
            if hwnd:
                user32.ShowWindow(hwnd, 5)  # SW_SHOW
                user32.SetForegroundWindow(hwnd)
        except Exception:
            pass
        time.sleep(2.5)

    while True:  # daemon keeper
        time.sleep(5)
        _apply_icon_once()


def _run():
    api = Api()
    httpd = _start_http_server()
    port = httpd.server_address[1]
    url = "http://127.0.0.1:{}/ui/index.html".format(port)
    print("UI URL: {}".format(url), flush=True)

    Api._max_h = _work_area_height()
    Api._win_h = WIN_H

    webview.create_window(
        "PocketDisplay 控制客户端",
        url=url,
        js_api=api,
        width=WIN_W,
        height=WIN_H,
        min_size=(820, 420),
    )

    # Start the icon keeper *before* webview.start() so the icon is already set
    # when the window is first shown — that is what makes the taskbar button
    # use it instead of the pythonw.exe icon.
    threading.Thread(target=_set_window_icon, daemon=True).start()

    try:
        webview.start()
    finally:
        if TRAY is not None:
            TRAY.remove()
        api.controller.stop()
        try:
            httpd.shutdown()
        except Exception:
            pass


def main():
    """Entry point: log any startup failure, since pythonw has no console."""
    try:
        _run()
    except Exception:
        import traceback
        log_path = os.path.join(ROOT_DIR, "client_error.log")
        try:
            with open(log_path, "w", encoding="utf-8") as fh:
                traceback.print_exc(file=fh)
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
