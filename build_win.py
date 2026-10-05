"""Build PocketDisplay Windows artifacts with PyInstaller.

Produces (in a clean dist/ folder):
  * PocketDisplayServer.exe  - the streaming server (standalone)
  * PocketDisplay.exe        - the polished TDesign/pywebview client (primary)
  * PocketDisplayClassic.exe - the dependency-light Tkinter client (fallback)
  * configuration.yaml       - shared, editable config next to the .exes

The frozen client expects a sibling server executable named
PocketDisplayServer.exe (see windows_client.ServerController.start).

Run (clear PYTHONPATH so CodeBuddy's sitecustomize shim does not hijack
os.remove):  $env:PYTHONPATH=""; py -3 build_win.py
"""

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(HERE, "dist")
# Unique workpath per run so PyInstaller never reuses a cached analysis from a
# previous build (a stale cache would freeze outdated source, e.g. an older
# config.py). Old build_* dirs are gitignored.
BUILD = os.path.join(HERE, "build_" + str(os.getpid()))
ICON = os.path.join(HERE, "assets", "icon.ico")
SERVER_DIR = os.path.join(HERE, "server")


def _pick_dist():
    """Return a dist path guaranteed to be empty so PyInstaller never has to
    delete a locked (Defender-scanned) stale .exe via os.remove."""
    base = DIST
    try:
        if os.path.exists(base):
            shutil.rmtree(base)
    except Exception as exc:
        print(f"note: could not remove existing {base}: {exc}", flush=True)
    if os.path.exists(base):
        base = f"{DIST}_{os.getpid()}"
        print(f"note: falling back to {base}", flush=True)
    os.makedirs(base, exist_ok=True)
    return base


def _run(args, dist):
    # Use `python -m PyInstaller` so it works inside a venv / CI where the
    # `pyinstaller` console script may not be on PATH.
    cmd = [sys.executable, "-m", "PyInstaller"] + args[1:]
    print("\n>>> " + " ".join(cmd) + "\n", flush=True)
    subprocess.run(cmd, check=True, cwd=HERE)


def build_server(dist):
    _run([
        "pyinstaller",
        os.path.join("server", "server.py"),
        "--name", "PocketDisplayServer",
        "--onefile", "--windowed",
        "--icon", ICON,
        "--distpath", dist, "--workpath", BUILD, "--noconfirm",
        "--paths", SERVER_DIR,
        "--hidden-import", "mss",
        "--hidden-import", "yaml",
    ], dist)


def build_web(dist):
    _run([
        "pyinstaller",
        "windows_client_web.py",
        "--name", "PocketDisplay",
        "--onefile", "--windowed",
        "--icon", ICON,
        "--distpath", dist, "--workpath", BUILD, "--noconfirm",
        "--paths", SERVER_DIR,
        "--hidden-import", "webview",
        "--hidden-import", "yaml",
        "--hidden-import", "mss",
        "--add-data", f"ui{os.pathsep}ui",
        "--add-data", f"assets{os.pathsep}assets",
        "--add-data", f"server{os.pathsep}server",
        "--add-data", f"configuration.yaml{os.pathsep}.",
    ], dist)


def build_classic(dist):
    _run([
        "pyinstaller",
        "windows_client.py",
        "--name", "PocketDisplayClassic",
        "--onefile", "--windowed",
        "--icon", ICON,
        "--distpath", dist, "--workpath", BUILD, "--noconfirm",
        "--paths", SERVER_DIR,
        "--hidden-import", "yaml",
        "--add-data", f"server{os.pathsep}server",
        "--add-data", f"assets{os.pathsep}assets",
    ], dist)


def _bundle_bin(dist):
    """Copy ffmpeg (and adb if present) next to the .exes so the app is
    self-contained and does not require them on PATH."""
    bin_dir = os.path.join(dist, "bin")
    os.makedirs(bin_dir, exist_ok=True)
    for name in ("ffmpeg", "adb"):
        exe = shutil.which(name)
        if not exe:
            print(f"note: {name} not found on PATH - not bundled "
                  f"(user must provide it for PATH)", flush=True)
            continue
        exe = os.path.realpath(exe)
        try:
            shutil.copyfile(exe, os.path.join(bin_dir, name + ".exe"))
            print(f"bundled {name} -> bin/{name}.exe", flush=True)
        except Exception as exc:
            print(f"note: failed to bundle {name}: {exc}", flush=True)
        # adb needs its side-by-side DLLs to run.
        if name == "adb":
            for dll in ("AdbWinApi.dll", "AdbWinUsbApi.dll"):
                src = os.path.join(os.path.dirname(exe), dll)
                if os.path.isfile(src):
                    shutil.copyfile(src, os.path.join(bin_dir, dll))


def post(dist):
    _bundle_bin(dist)
    dst = os.path.join(dist, "configuration.yaml")
    if not os.path.exists(dst):
        shutil.copyfile(os.path.join(HERE, "configuration.yaml"), dst)
    print("\nBuild complete. Artifacts in:", dist, flush=True)
    for name in ("PocketDisplay.exe", "PocketDisplayServer.exe",
                 "PocketDisplayClassic.exe", "configuration.yaml"):
        p = os.path.join(dist, name)
        if os.path.exists(p):
            print(f"  {name:28s} {os.path.getsize(p)/1024/1024:7.1f} MB", flush=True)


if __name__ == "__main__":
    dist = _pick_dist()
    try:
        build_server(dist)
        build_web(dist)
        build_classic(dist)
        post(dist)
    except subprocess.CalledProcessError as exc:
        print(f"\nBUILD FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
