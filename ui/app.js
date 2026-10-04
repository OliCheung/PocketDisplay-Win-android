/* PocketDisplay client UI logic (TDesign + Vue 3, talks to Python via pywebview). */

/* pywebview injects window.pywebview asynchronously, so resolve it lazily and
   wait for the `pywebviewready` event before touching the bridge. */
var api = null;

function getApi() {
  return (window.pywebview && window.pywebview.api) ? window.pywebview.api : null;
}

function opts(list) {
  return list.map(function (v) { return { label: v, value: v }; });
}

/* Encoding options: Chinese labels, English values (ffmpeg needs the tokens).
   Recommended items carry （推荐） so a safe default is obvious. */
function labeled(pairs) {
  return pairs.map(function (p) { return { label: p[0], value: p[1] }; });
}

var PRESET_OPTIONS = labeled([
  ['最快（兼容性最好）', 'ultrafast'],
  ['极快', 'superfast'],
  ['很快（推荐）', 'veryfast'],
  ['较快', 'faster'],
  ['快', 'fast'],
  ['中等（画质优先，延迟高）', 'medium']
]);

var TUNE_OPTIONS = labeled([
  ['零延迟（推荐）', 'zerolatency'],
  ['电影', 'film'],
  ['动画', 'animation'],
  ['保留颗粒', 'grain'],
  ['静态图', 'stillimage']
]);

var PROFILE_OPTIONS = labeled([
  ['基线（安卓兼容最好）', 'baseline'],
  ['主要（通用）', 'main'],
  ['高（高画质）', 'high']
]);

var LEVEL_OPTIONS = labeled([
  ['3.0 · 最高 720p', '3.0'],
  ['3.1 · 最高 1080p30（推荐）', '3.1'],
  ['4.0 · 更大码率', '4.0'],
  ['4.1 · 最高 1080p60', '4.1'],
  ['4.2 · 支持 4K', '4.2'],
  ['5.0 · 4K 高码率', '5.0']
]);

var app = Vue.createApp({
  data: function () {
    return {
      status: { running: false, phone: 'unknown', capture: '', resolution_change: '' },
      monitors: [],
      monitorIndex: null,
      modes: [],
      best: null,
      resolution: '',
      phone: null,
      cfg: { fps: 30, bitrate: '4M', preset: 'ultrafast', tune: 'zerolatency',
             profile: 'baseline', level: '3.1' },
      logText: '',
      applying: false,
      busy: { start: false, stop: false, reconnect: false, apply: false,
              encode: false, phone: false },
      presetOptions: PRESET_OPTIONS,
      tuneOptions: TUNE_OPTIONS,
      profileOptions: PROFILE_OPTIONS,
      levelOptions: LEVEL_OPTIONS
    };
  },

  computed: {
    monitorOptions: function () {
      return this.monitors.map(function (m) { return { label: m.label, value: m.index }; });
    },
    resolutionOptions: function () {
      var best = this.best;
      return this.modes.map(function (m) {
        var isBest = best && m.w === best.w && m.h === best.h;
        // Keep the option text short so it never truncates; the full
        // load/scale/distortion metrics show in the tag below the select.
        return { label: (isBest ? '★推荐  ' : '') + m.w + 'x' + m.h,
                 value: m.w + 'x' + m.h };
      });
    },
    isBest: function () {
      return !!this.best && this.resolution === (this.best.w + 'x' + this.best.h);
    },
    modeInfo: function () {
      var sel = this.resolution;
      var hit = this.modes.find(function (m) { return (m.w + 'x' + m.h) === sel; });
      return hit ? hit.desc : '';
    },
    phoneText: function () {
      return this.phone
        ? '屏幕（横屏）' + this.phone.w + '×' + this.phone.h + '，宽高比 ' + this.phone.ar + ':1'
        : '';
    }
  },

  methods: {
    toast: function (theme, msg) {
      if (window.TDesign && TDesign.MessagePlugin && TDesign.MessagePlugin[theme]) {
        TDesign.MessagePlugin[theme](msg);
      }
      console.log('[' + theme + '] ' + msg);
    },

    noApi: function () {
      this.toast('error', '未连接到 Python 后端（请在客户端窗口内打开）');
    },

    refreshStatus: function () {
      if (!api) return;
      api.get_status().then(function (s) {
        this.status = s;
      }.bind(this)).catch(function () {});
    },

    refreshLog: function () {
      if (!api) return;
      api.get_log().then(function (lines) {
        this.logText = lines.join('\n');
        this.$nextTick(function () {
          var box = this.$refs.logBox;
          if (box) box.scrollTop = box.scrollHeight;
        }.bind(this));
      }.bind(this)).catch(function () {});
    },

    loadMonitors: function () {
      if (!api) return this.noApi();
      return api.get_monitors().then(function (data) {
        this.monitors = data;
        if (this.monitorIndex === null && data.length) {
          // Default to the secondary (non-primary) display — that is the one
          // meant to be streamed to the phone.
          var sec = data.find(function (m) { return !m.primary; });
          this.monitorIndex = sec ? sec.index : data[0].index;
        }
        return this.loadResolutions();
      }.bind(this));
    },

    loadResolutions: function () {
      if (!api || this.monitorIndex === null) return Promise.resolve();
      return api.get_resolutions(this.monitorIndex).then(function (r) {
        this.modes = r.modes;
        this.best = r.best;
        this.phone = r.phone;
        if (r.best && !this.resolution) {
          this.resolution = r.best.w + 'x' + r.best.h;
        }
      }.bind(this));
    },

    onMonitorChange: function () {
      var self = this;
      api.get_resolutions(this.monitorIndex).then(function (r) {
        self.modes = r.modes;
        self.best = r.best;
        if (r.best) self.resolution = r.best.w + 'x' + r.best.h;
      });
    },

    refreshPhone: function () {
      var self = this;
      this.busy.phone = true;
      api.refresh_phone().then(function (p) {
        self.phone = p;
        self.toast(p ? 'success' : 'warning',
                   p ? ('安卓设备屏幕 ' + p.w + '×' + p.h) : '未能读取（adb 不可用或未连接）');
        return self.loadResolutions();
      }).catch(function () {}).then(function () { self.busy.phone = false; });
    },

    /* Both apply actions already restart the server side automatically
       (resolution: the server's monitor thread restarts FFmpeg and drops the
       client; encoding: the controller stops/starts the server). This polls
       until the change is really in effect so the UI can confirm it. */
    waitForApply: function (targetRes, timeoutMs) {
      var self = this;
      var deadline = Date.now() + (timeoutMs || 15000);
      return new Promise(function (resolve) {
        function tick() {
          api.get_status().then(function (s) {
            self.status = s;
            var resOk = !targetRes || (s.capture || '').indexOf(targetRes) === 0;
            var devOk = s.phone === 'connected';
            if (resOk && devOk) return resolve({ ok: true, status: s });
            if (Date.now() > deadline) return resolve({ ok: false, status: s });
            setTimeout(tick, 700);
          }).catch(function () { resolve({ ok: false, status: null }); });
        }
        setTimeout(tick, 800);
      });
    },

    onApply: function () {
      if (!this.resolution) return this.toast('warning', '请先选择分辨率');
      var parts = this.resolution.split('x');
      var self = this;
      var target = this.resolution;
      this.busy.apply = true;
      this.applying = true;
      api.apply_resolution(this.monitorIndex, parseInt(parts[0]), parseInt(parts[1]))
        .then(function (r) {
          if (!r.ok) {
            self.applying = false;
            self.busy.apply = false;
            return self.toast('error', r.msg);
          }
          self.toast('info', r.msg + '…');
          return self.waitForApply(target).then(function (res) {
            self.applying = false;
            self.busy.apply = false;
            self.toast(res.ok ? 'success' : 'warning',
                       res.ok ? ('已生效 ' + target + '，安卓设备已连接')
                              : ('已下发 ' + target + '，服务器尚未确认生效，可查看下方日志'));
            return self.loadResolutions();
          });
        })
        .catch(function () { self.applying = false; self.busy.apply = false; });
    },

    onApplyRecommended: function () {
      var self = this;
      this.busy.apply = true;
      this.applying = true;
      api.apply_recommended(this.monitorIndex).then(function (r) {
        if (!r.ok) {
          self.applying = false;
          self.busy.apply = false;
          return self.toast('error', r.msg);
        }
        self.toast('info', r.msg + '…');
        return self.waitForApply(r.msg.match(/(\d+x\d+)/) ? r.msg.match(/(\d+x\d+)/)[1] : null)
          .then(function (res) {
            self.applying = false;
            self.busy.apply = false;
            self.toast(res.ok ? 'success' : 'warning',
                       res.ok ? '已应用推荐分辨率，安卓设备已连接'
                              : '已下发推荐分辨率，服务器尚未确认生效');
            return self.loadResolutions();
          });
      }).catch(function () { self.applying = false; self.busy.apply = false; });
    },

    onStart: function () {
      var self = this;
      this.busy.start = true;
      api.start_server().then(function (r) {
        self.toast(r.ok ? 'success' : 'error', r.msg);
        self.refreshStatus();
      }).catch(function () {}).then(function () { self.busy.start = false; });
    },

    onStop: function () {
      var self = this;
      this.busy.stop = true;
      api.stop_server().then(function (r) {
        self.toast(r.ok ? 'success' : 'error', r.msg);
        self.refreshStatus();
      }).catch(function () {}).then(function () { self.busy.stop = false; });
    },

    /* Ask Python to resize the window so the content fits exactly (no empty
       strip at the bottom). Sends the delta in physical px. */
    fitWindow: function () {
      if (!api || !api.fit_window) return;
      var node = document.getElementById('app');
      if (!node) return;
      var content = node.getBoundingClientRect().height;
      var delta = content - window.innerHeight;
      if (Math.abs(delta) < 2) return;
      var dpr = window.devicePixelRatio || 1;
      api.fit_window(Math.round(delta * dpr)).catch(function () {});
    },

    watchContentHeight: function () {
      var self = this;
      if (typeof ResizeObserver === 'undefined') {
        setTimeout(function () { self.fitWindow(); }, 1200);
        return;
      }
      var node = document.getElementById('app');
      if (!node) return;
      var timer = null;
      var ro = new ResizeObserver(function () {
        // debounce: layouts settle over a couple of frames after data loads
        clearTimeout(timer);
        timer = setTimeout(function () { self.fitWindow(); }, 250);
      });
      ro.observe(node);
      setTimeout(function () { self.fitWindow(); }, 600);
    },

    onMinimizeToTray: function () {
      var self = this;
      api.minimize_to_tray().then(function (r) {
        self.toast(r.ok ? 'success' : 'warning', r.msg);
      }).catch(function () {
        self.toast('warning', '托盘功能不可用');
      });
    },

    onReconnect: function () {
      var self = this;
      this.busy.reconnect = true;
      api.reconnect().then(function (r) {
        self.toast(r.ok ? 'success' : 'error', r.msg);
        self.refreshStatus();
      }).catch(function () {}).then(function () { self.busy.reconnect = false; });
    },

    onApplyEncoding: function () {
      var self = this;
      this.busy.encode = true;
      this.applying = true;
      api.save_encoding(
        this.cfg.fps, this.cfg.bitrate, this.cfg.preset,
        this.cfg.tune, this.cfg.profile, this.cfg.level
      ).then(function (r) {
        if (!r.ok) {
          self.applying = false;
          self.busy.encode = false;
          return self.toast('error', r.msg);
        }
        self.toast('info', '已保存，服务器重启中，安卓设备自动重连…');
        // save_encoding already stops/starts the server; just confirm it came back.
        return self.waitForApply(null, 20000).then(function (res) {
          self.applying = false;
          self.busy.encode = false;
          self.toast(res.ok ? 'success' : 'warning',
                     res.ok ? '参数已生效，安卓设备已连接'
                            : '服务器已重启，但安卓设备未连上，可点「一键重连」');
        });
      }).catch(function () { self.applying = false; self.busy.encode = false; });
    }
  },

  mounted: function () {
    var self = this;

    function init() {
      api = getApi();
      if (!api) { self.noApi(); return; }
      api.get_config().then(function (c) { self.cfg = c; }).catch(function () {});
      self.loadMonitors();
      self.refreshStatus();
      self.refreshLog();
      self.watchContentHeight();
      setInterval(function () { self.refreshStatus(); self.refreshLog(); }, 1000);
    }

    if (getApi()) {
      init();
    } else {
      window.addEventListener('pywebviewready', init, { once: true });
      // If the bridge never shows up (page opened outside the client window), say so.
      setTimeout(function () {
        if (!api) self.noApi();
      }, 4000);
    }
  }
});

app.use(TDesign);
app.mount('#app');
