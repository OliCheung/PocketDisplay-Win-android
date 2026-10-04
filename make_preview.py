"""Build ui/_preview.html: the real index.html plus a mock pywebview bridge.

Lets the layout be screenshotted in a plain browser (no Python backend), which
is handy for reviewing UI changes without launching the real client.
"""

import os

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "ui", "index.html")
DST = os.path.join(ROOT, "ui", "_preview.html")

MOCK = r"""
<script>
// ---- mock pywebview bridge (preview only) --------------------------------
(function () {
  var DEV1 = '\\\\.\\DISPLAY1';
  var DEV16 = '\\\\.\\DISPLAY16';
  var MODES = [
    { w: 1140, h: 540, desc: '★推荐  1140x540  负载27% 放大1.68x 失真3%' },
    { w: 1280, h: 720, desc: '1280x720  负载37% 放大1.50x 失真11%' },
    { w: 1366, h: 768, desc: '1366x768  负载46% 放大1.41x 失真11%' },
    { w: 1920, h: 1080, desc: '1920x1080  负载90% 放大1.00x 失真11%' },
    { w: 2560, h: 1440, desc: '2560x1440  负载160% 放大0.75x 失真11%' }
  ];
  function res() {
    return {
      modes: MODES,
      best: { w: 1140, h: 540 },
      phone: { w: 1920, h: 1200, ar: 1.6 },
      current: '1366x768'
    };
  }
  var api = {
    get_config: function () {
      return Promise.resolve({ fps: 30, bitrate: '4', preset: 'veryfast',
        tune: 'zerolatency', profile: 'baseline', level: '3.1' });
    },
    get_monitors: function () {
      return Promise.resolve([
        { index: 0, device: DEV1, label: '主屏 ' + DEV1 + ' 1920x1080',
          width: 1920, height: 1080, primary: true },
        { index: 1, device: DEV16, label: '副屏 ' + DEV16 + ' 1366x768',
          width: 1366, height: 768, primary: false }
      ]);
    },
    get_resolutions: function () { return Promise.resolve(res()); },
    refresh_phone: function () { return Promise.resolve(res().phone); },
    get_status: function () {
      return Promise.resolve({ running: true, phone: 'connected',
        capture: '1366x768', resolution_change: '' });
    },
    get_log: function () {
      return Promise.resolve([
        '[1/4] Detecting displays...',
        '  Monitor 0: ' + DEV1 + '  1920x1080  (primary)',
        '  Monitor 1: ' + DEV16 + ' 1366x768   (target)',
        '[2/4] Target: ' + DEV16 + ' 1366x768 @ offset (1920, 0)',
        '[3/4] Video port 5000 listening on 0.0.0.0',
        '[4/4] Starting FFmpeg capture + encode pipeline...',
        '  Launching FFmpeg capture 1366x768 @ offset (1920,0)',
        '  H.264 profile: baseline level 3.1',
        '  Video client connected from 127.0.0.1',
        '  Frames: 1420  FPS: 30.0  Bitrate: 3.9 Mbps'
      ]);
    },
    apply_resolution: function () { return Promise.resolve({ ok: true, msg: '预览模式' }); },
    apply_recommended: function () { return Promise.resolve({ ok: true, msg: '预览模式' }); },
    save_encoding: function () { return Promise.resolve({ ok: true, msg: '预览模式' }); },
    start_server: function () { return Promise.resolve({ ok: true, msg: '预览模式' }); },
    stop_server: function () { return Promise.resolve({ ok: true, msg: '预览模式' }); },
    reconnect: function () { return Promise.resolve({ ok: true, msg: '预览模式' }); },
    minimize_to_tray: function () { return Promise.resolve({ ok: true, msg: '预览模式' }); }
  };
  window.pywebview = { api: api };
  window.addEventListener('DOMContentLoaded', function () {
    setTimeout(function () {
      window.dispatchEvent(new Event('pywebviewready'));
    }, 50);
  });
})();
</script>
"""


def main():
    with open(SRC, "r", encoding="utf-8") as fh:
        html = fh.read()

    tag = '<script src="/assets/vendor/vue.global.prod.js"></script>'
    if tag not in html:
        raise SystemExit("anchor script tag not found in index.html")
    html = html.replace(tag, MOCK + "\n" + tag, 1)

    with open(DST, "w", encoding="utf-8") as fh:
        fh.write(html)
    print("wrote", DST, os.path.getsize(DST), "bytes")


if __name__ == "__main__":
    main()
