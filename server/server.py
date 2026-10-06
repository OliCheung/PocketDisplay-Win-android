"""Main entry point for the PocketDisplay server."""

import signal
import subprocess
import sys
import threading
import time
from config import (
    MONITOR_INDEX, VIDEO_PORT, CONTROL_PORT, HOST,
    CAPTURE_WIDTH, CAPTURE_HEIGHT, CAPTURE_FPS,
    FFMPEG_PATH, H264_ENCODER, H264_PRESET, H264_TUNE, H264_BITRATE,
    KEYFRAME_INTERVAL, H264_PROFILE, H264_LEVEL,
    H264_SLICES, H264_THREADS
)
from stream_server import StreamServer
from touch_receiver import TouchReceiver
from adb_setup import check_device, setup_reverse_ports


def _hidden_kwargs():
    """Popen kwargs that suppress the console window of a console-application
    child (ffmpeg, adb, taskkill, cmd...). No-op on non-Windows."""
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def _list_ffmpeg_encoders(ffmpeg_path):
    """Return the set of video encoders ffmpeg was compiled with.

    Used to confirm a hardware encoder (nvenc/qsv/amf) is actually present
    before we try to use it, since a static build may lack them."""
    try:
        proc = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=20,
            **_hidden_kwargs(),
        )
        blob = (proc.stderr or "") + (proc.stdout or "")
    except Exception:
        return set()
    encoders = set()
    for line in blob.splitlines():
        # ffmpeg lists encoders like: " V..... libx264  ..."
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith("V") and not parts[0].startswith("VF"):
            encoders.add(parts[1])
    return encoders


def _detect_gpu_vendor():
    """Best-effort detection of the primary GPU vendor on Windows.

    Returns one of 'nvidia', 'intel', 'amd', or 'unknown'."""
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_VideoController).Name"],
            capture_output=True, text=True, timeout=20,
            **_hidden_kwargs(),
        )
        text = (proc.stdout or "").lower()
    except Exception:
        text = ""
    if "nvidia" in text:
        return "nvidia"
    if "intel" in text:
        return "intel"
    if "amd" in text or "radeon" in text or "advanced micro" in text:
        return "amd"
    return "unknown"


def _select_encoder():
    """Pick the H.264 encoder to use.

    Honors an explicit ``encoding.encoder`` value from configuration.yaml
    (e.g. ``libx264``, ``h264_nvenc``, ``h264_qsv``, ``h264_amf``).
    When set to ``auto`` (default) it detects the GPU vendor and falls back
    to whatever hardware encoder ffmpeg actually supports; if none is found
    it safely returns to the CPU ``libx264`` encoder.
    """
    preferred = (H264_ENCODER or "auto").strip().lower()
    explicit = preferred in {
        "libx264", "h264_nvenc", "h264_qsv", "h264_amf",
    }
    if explicit:
        return preferred

    vendor = _detect_gpu_vendor()
    encoders = _list_ffmpeg_encoders(FFMPEG_PATH)

    # Prefer the hardware encoder matching the detected GPU, but only if
    # ffmpeg was built with it. Otherwise fall through to the generic checks
    # below and ultimately to libx264.
    if vendor == "nvidia" and "h264_nvenc" in encoders:
        return "h264_nvenc"
    if vendor == "intel" and "h264_qsv" in encoders:
        return "h264_qsv"
    if vendor == "amd" and "h264_amf" in encoders:
        return "h264_amf"

    # GPU not recognised but a hardware encoder is available anyway
    # (e.g. headless detection miss). Use it opportunistically.
    for hw in ("h264_nvenc", "h264_qsv", "h264_amf"):
        if hw in encoders:
            return hw

    return "libx264"


SELECTED_ENCODER = _select_encoder()


def _encoder_args(encoder):
    """Build the ffmpeg video-encoder argument list for the given encoder.

    Common rate-control (CBR with forced keyframes) is shared so the client
    always sees a clean, seekable Annex-B H.264 stream. Hardware encoders use
    their low-latency presets; libx264 uses the configured preset/tune.
    """
    args = [
        "-c:v", encoder,
        "-b:v", H264_BITRATE,
        "-maxrate", H264_BITRATE,
        "-bufsize", H264_BITRATE,
        "-g", str(KEYFRAME_INTERVAL),
        "-keyint_min", str(KEYFRAME_INTERVAL),
    ]
    if encoder == "libx264":
        args += [
            "-profile:v", H264_PROFILE,
            "-level", H264_LEVEL,
            "-preset", H264_PRESET,
            "-tune", H264_TUNE,
            "-threads", str(H264_THREADS),
            "-x264-params",
            f"repeat-headers=1:slices={H264_SLICES}:threads={H264_THREADS}",
        ]
    elif encoder == "h264_nvenc":
        args += [
            "-profile:v", H264_PROFILE,
            "-level", H264_LEVEL,
            "-preset", "p1",
            "-tune", "ll",
            "-rc", "cbr",
            "-delay", "0",
        ]
    elif encoder == "h264_qsv":
        args += [
            "-profile:v", H264_PROFILE,
            "-level", H264_LEVEL,
            "-preset", "veryfast",
            "-tune", "zerolatency",
            "-rc", "cbr",
            "-look_ahead", "0",
        ]
    elif encoder == "h264_amf":
        # AMF rejects "-profile:v baseline" (and is picky about level), so we
        # omit profile/level entirely and let it use its defaults; Android's
        # MediaCodec decodes main/high fine.
        args += [
            "-usage", "lowlatency",
            "-quality", "balanced",
            "-preset", "speed",
            "-rc", "cbr",
        ]
    return args


def _print_local_ips():
    """Print the local IP addresses of this machine."""
    import socket as _sock
    try:
        hostname = _sock.gethostname()
        addrs = _sock.getaddrinfo(hostname, None, _sock.AF_INET)
        ips = sorted(set(addr[4][0] for addr in addrs if not addr[4][0].startswith("127.")))
        if not ips:
            s = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ips = [s.getsockname()[0]]
            s.close()
        for ip in ips:
            print(f"    >>> {ip} <<<")
    except Exception:
        print("    (Could not determine local IP — check ipconfig)")


def _detect_resolution():
    """Detect resolution of the target monitor using mss."""
    try:
        import mss
        with mss.MSS() as sct:
            monitors = sct.monitors
            mss_idx = MONITOR_INDEX + 1
            if mss_idx < len(monitors):
                m = monitors[mss_idx]
            elif len(monitors) > 1:
                m = monitors[1]
            else:
                return CAPTURE_WIDTH, CAPTURE_HEIGHT
            return m['width'], m['height']
    except Exception:
        return CAPTURE_WIDTH, CAPTURE_HEIGHT


def _get_monitor_offset():
    """Get the top-left offset of the target monitor for gdigrab."""
    try:
        import mss
        with mss.MSS() as sct:
            monitors = sct.monitors
            mss_idx = MONITOR_INDEX + 1
            if mss_idx < len(monitors):
                m = monitors[mss_idx]
                return m['left'], m['top']
    except Exception:
        pass
    return 0, 0


def _kill_existing_servers():
    """Kill any existing PocketDisplay server processes (port conflicts, stale FFmpeg)."""
    import socket as _sock

    # Check if VIDEO_PORT is already in use
    killed = False
    try:
        test_sock = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
        test_sock.settimeout(1)
        result = test_sock.connect_ex(('127.0.0.1', VIDEO_PORT))
        test_sock.close()
        if result == 0:
            print(f"  Port {VIDEO_PORT} is in use — killing existing server...")
            # Find and kill the process holding the port
            try:
                output = subprocess.check_output(
                    f'netstat -ano | findstr ":{VIDEO_PORT} " | findstr "LISTENING"',
                    shell=True, text=True, **_hidden_kwargs()
                )
                pids = set()
                for line in output.strip().splitlines():
                    parts = line.split()
                    if parts:
                        pid = parts[-1]
                        if pid.isdigit() and int(pid) > 0:
                            pids.add(int(pid))
                for pid in pids:
                    try:
                        subprocess.run(
                            ["taskkill", "/F", "/PID", str(pid)],
                            capture_output=True, timeout=5, **_hidden_kwargs()
                        )
                        killed = True
                        print(f"  Killed process PID {pid}")
                    except Exception:
                        pass
            except subprocess.CalledProcessError:
                pass
    except Exception:
        pass

    # Also kill any orphaned ffmpeg processes capturing the desktop
    try:
        output = subprocess.check_output(
            'wmic process where "name=\'ffmpeg.exe\'" get commandline,processid /format:csv',
            shell=True, text=True, stderr=subprocess.DEVNULL, **_hidden_kwargs()
        )
        for line in output.strip().splitlines():
            if 'gdigrab' in line:
                parts = line.strip().split(',')
                pid = parts[-1].strip()
                if pid.isdigit() and int(pid) > 0:
                    try:
                        subprocess.run(
                            ["taskkill", "/F", "/PID", pid],
                            capture_output=True, timeout=5, **_hidden_kwargs()
                        )
                        killed = True
                        print(f"  Killed orphaned FFmpeg PID {pid}")
                    except Exception:
                        pass
    except Exception:
        pass

    if killed:
        time.sleep(2)
        print("  Cleanup complete.")
    else:
        print("  No existing server found.")


def main():
    width, height = _detect_resolution()

    print("=" * 60)
    print("  PocketDisplay Server")
    print("=" * 60)
    print(f"  Monitor Index: {MONITOR_INDEX}")
    print(f"  Resolution: {width}x{height} @ {CAPTURE_FPS}fps")
    print(f"  Video Port: {VIDEO_PORT}")
    print(f"  Control Port: {CONTROL_PORT}")
    print("=" * 60)
    print()

    stream_server = None
    touch_receiver = None
    ffmpeg_proc = None
    shutdown_event = threading.Event()
    frame_count = [0]
    start_time = [time.time()]

    def signal_handler(sig, frame):
        print("\nShutting down...")
        shutdown_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        print("[0/4] Checking for existing server instances...")
        _kill_existing_servers()
        print()

        print("[1/4] Setting up ADB reverse port forwarding...")
        adb_ok = False
        if check_device():
            if setup_reverse_ports():
                adb_ok = True
            else:
                print("WARNING: ADB port forwarding failed.")

        if not adb_ok:
            print()
            print("  ADB not available — using WiFi mode instead.")
            print("  On your phone app, enter this PC's IP address:")
            _print_local_ips()
            print()
            print("  The server will listen on all interfaces (0.0.0.0).")
            print("  Make sure your phone is on the same WiFi network.")
        print()

        print("[2/4] Starting video stream server with touch input...")
        offset_x, offset_y = _get_monitor_offset()
        touch_receiver = TouchReceiver(host=HOST, port=CONTROL_PORT,
                                       display_width=width, display_height=height,
                                       monitor_index=MONITOR_INDEX,
                                       monitor_offset_x=offset_x,
                                       monitor_offset_y=offset_y)
        stream_server = StreamServer(host=HOST, port=VIDEO_PORT,
                                     on_touch_event=touch_receiver.handle_touch_event)
        stream_server.start()
        print()

        print("[3/4] Touch input processing ready (via video connection)")
        print()

        print("[4/4] Starting FFmpeg capture + encode pipeline...")
        # Mutable holder for the running FFmpeg process + current capture geometry
        ffmpeg_proc_holder = [None]
        current = {
            "w": width, "h": height,
            "ox": offset_x, "oy": offset_y,
        }
        offset_x, offset_y = _get_monitor_offset()
        print(f"  Capture offset: ({offset_x}, {offset_y})")
        print(f"  Resolution: {width}x{height}")
        print(f"  Using FFmpeg at: {FFMPEG_PATH}")
        print(f"  Video encoder: {SELECTED_ENCODER}"
              + (" (auto-detected)" if (H264_ENCODER or "auto").strip().lower() == "auto"
                 else " (from configuration.yaml)"))

        def build_cmd(w, h, ox, oy):
            return [
                FFMPEG_PATH,
                "-f", "gdigrab",
                "-framerate", str(CAPTURE_FPS),
                "-offset_x", str(ox),
                "-offset_y", str(oy),
                "-video_size", f"{w}x{h}",
                "-i", "desktop",
            ] + _encoder_args(SELECTED_ENCODER) + [
                "-pix_fmt", "yuv420p",
                "-f", "h264",
                "-an",
                "-flush_packets", "1",
                "pipe:1"
            ]

        print(f"  Command: {' '.join(build_cmd(width, height, offset_x, offset_y)[:6])}...")

        def launch_capture(w, h, ox, oy):
            cmd = build_cmd(w, h, ox, oy)
            print(f"  Launching FFmpeg capture {w}x{h} @ offset ({ox},{oy})")
            print(f"  Command: {' '.join(cmd[:6])} ...")
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                # ffmpeg is a console app: without this it keeps a black console
                # window open for as long as the capture runs.
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )

            def read_output():
                try:
                    while not shutdown_event.is_set():
                        data = proc.stdout.read(65536)
                        if not data:
                            break
                        frame_count[0] += 1
                        stream_server.send_data(data)
                except Exception as e:
                    if not shutdown_event.is_set():
                        print(f"Output read error: {e}")

            def read_stderr():
                try:
                    for line in proc.stderr:
                        line = line.decode("utf-8", errors="replace").strip()
                        if line and not shutdown_event.is_set():
                            if any(k in line.lower() for k in ["error", "warning", "fatal"]):
                                print(f"FFmpeg: {line}")
                except Exception:
                    pass

            threading.Thread(target=read_output, daemon=True).start()
            threading.Thread(target=read_stderr, daemon=True).start()
            return proc

        def stop_capture(proc):
            if proc is None:
                return
            try:
                proc.kill()
                proc.wait(timeout=3)
            except Exception:
                pass

        def restart_capture(new_w, new_h, new_ox, new_oy):
            print(f"  [Capture restart] {current['w']}x{current['h']} -> {new_w}x{new_h} "
                  f"(offset {current['ox']},{current['oy']} -> {new_ox},{new_oy})")
            stop_capture(ffmpeg_proc_holder[0])
            # Drop cached SPS/PPS/IDR so the new stream's headers get cached for clients
            try:
                stream_server._header_cache = bytearray()
                stream_server._parse_buf = bytearray()
            except Exception:
                pass
            # Force the phone to reconnect so its decoder resets to the new resolution
            try:
                stream_server.disconnect_client()
            except Exception:
                pass
            # Update touch mapping to the new geometry
            touch_receiver.display_width = new_w
            touch_receiver.display_height = new_h
            touch_receiver.monitor_offset_x = new_ox
            touch_receiver.monitor_offset_y = new_oy
            ffmpeg_proc_holder[0] = launch_capture(new_w, new_h, new_ox, new_oy)
            current["w"] = new_w
            current["h"] = new_h
            current["ox"] = new_ox
            current["oy"] = new_oy

        # Initial launch
        ffmpeg_proc = launch_capture(width, height, offset_x, offset_y)
        ffmpeg_proc_holder[0] = ffmpeg_proc

        def monitor_resolution():
            """Poll the target monitor; auto-restart FFmpeg when its resolution
            or position changes (e.g. user changes display settings in Windows)."""
            while not shutdown_event.is_set():
                shutdown_event.wait(timeout=3.0)
                if shutdown_event.is_set():
                    break
                try:
                    nw, nh = _detect_resolution()
                    nox, noy = _get_monitor_offset()
                except Exception:
                    continue
                if nw <= 0 or nh <= 0:
                    continue
                if nw != current["w"] or nh != current["h"] or nox != current["ox"] or noy != current["oy"]:
                    if nw != current["w"] or nh != current["h"]:
                        print(f"  Display resolution changed: {current['w']}x{current['h']} -> {nw}x{nh}")
                    restart_capture(nw, nh, nox, noy)

        monitor_thread = threading.Thread(target=monitor_resolution, daemon=True)
        monitor_thread.start()

        start_time[0] = time.time()
        print()
        print("=" * 60)
        print("  Server running! Press Ctrl+C to stop.")
        print("=" * 60)
        print()

        try:
            while not shutdown_event.is_set():
                shutdown_event.wait(timeout=5.0)
                if not shutdown_event.is_set():
                    elapsed = time.time() - start_time[0]
                    fps = frame_count[0] / elapsed if elapsed > 0 else 0
                    chunks = frame_count[0]
                    sent_mb = stream_server.bytes_sent / (1024 * 1024)
                    client = "Yes" if stream_server.has_client else "No"
                    print(
                        f"  [Status] Chunks: {chunks} | "
                        f"Sent: {sent_mb:.1f}MB | "
                        f"Drops: {stream_server._chunks_dropped} | "
                        f"Q: {len(stream_server._send_queue)} | Client: {client}"
                    )
        except KeyboardInterrupt:
            shutdown_event.set()
    finally:
        print("\nStopping components...")
        shutdown_event.set()
        if ffmpeg_proc_holder[0]:
            try:
                ffmpeg_proc_holder[0].kill()
                ffmpeg_proc_holder[0].wait(timeout=3)
            except Exception:
                pass
            print("FFmpeg stopped.")
        if stream_server:
            stream_server.stop()
        print("\nServer stopped. Goodbye!")


if __name__ == "__main__":
    main()
