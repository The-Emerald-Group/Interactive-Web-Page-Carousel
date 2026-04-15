import json
import os
import time
from urllib import error, request as urlrequest
from urllib.parse import urlencode, urlparse

from flask import Flask, Response, jsonify, redirect, request, send_file, session

app = Flask(__name__, static_folder=None)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-key")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("SESSION_COOKIE_SECURE", "0") == "1"

_config_path = os.environ.get("CONFIG_PATH", "/app/data/config.json")
_session_bundle_path = os.environ.get("SESSION_BUNDLE_PATH", "/app/control/session_bundle.json")
_tv_runtime_path = os.environ.get("TV_RUNTIME_PATH", "/app/data/tv_runtime.json")
_admin_password = os.environ.get("ADMIN_PASSWORD", "admin123")
_state_cache = None
_session_bundle_cache = {}
_session_bundle_mtime = None


def _load_session_bundle():
    global _session_bundle_cache, _session_bundle_mtime
    try:
        mtime = os.path.getmtime(_session_bundle_path)
    except OSError:
        _session_bundle_cache = {}
        _session_bundle_mtime = None
        return {}
    if _session_bundle_mtime == mtime and _session_bundle_cache:
        return _session_bundle_cache
    try:
        with open(_session_bundle_path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except Exception:
        payload = {}
    payload["entries"] = payload.get("entries") or []
    payload["captured_at"] = int(payload.get("captured_at") or 0)
    _session_bundle_cache = payload
    _session_bundle_mtime = mtime
    return _session_bundle_cache

def _safe_int(value, fallback):
    try:
        parsed = int(value)
        if parsed < 1:
            return fallback
        return parsed
    except Exception:
        return fallback

def _normalize_state(state):
    pages = state.get("pages", [])
    normalized_pages = []
    for item in pages:
        if isinstance(item, str):
            display_url = item.strip()
            login_url = display_url
            approved = True
        elif isinstance(item, dict):
            display_url = str(item.get("display_url") or item.get("url") or "").strip()
            login_url = str(item.get("login_url") or display_url).strip()
            approved = bool(item.get("approved", False))
        else:
            continue
        if not display_url:
            continue
        normalized_pages.append({
            "display_url": display_url,
            "login_url": login_url or display_url,
            "approved": approved,
        })
    interval = _safe_int(state.get("interval", 30), 30)
    version = _safe_int(state.get("version", int(time.time())), int(time.time()))
    return {"pages": normalized_pages, "interval": interval, "version": version}

def _default_state():
    pages = os.environ.get("PAGES", "https://example.com")
    interval = _safe_int(os.environ.get("INTERVAL", "30"), 30)
    page_list = [p.strip() for p in pages.split(",") if p.strip()]
    return {
        "pages": [{"display_url": p, "login_url": p, "approved": True} for p in page_list],
        "interval": interval,
        "version": int(time.time()),
    }

def load_state():
    global _state_cache
    if _state_cache is not None:
        return _state_cache
    try:
        with open(_config_path, "r", encoding="utf-8") as fh:
            _state_cache = _normalize_state(json.load(fh))
    except Exception:
        _state_cache = _default_state()
        save_state(_state_cache)
    return _state_cache

def save_state(state):
    global _state_cache
    normalized = _normalize_state(state)
    normalized["version"] = int(time.time() * 1000)
    os.makedirs(os.path.dirname(_config_path), exist_ok=True)
    with open(_config_path, "w", encoding="utf-8") as fh:
        json.dump(normalized, fh, indent=2)
    _state_cache = normalized
    return _state_cache

def require_admin():
    return session.get("is_admin") is True


@app.route('/')
def root():
    return redirect("/carousel")


@app.route('/carousel')
def index():
    return redirect("/tv-stream")


@app.route('/display')
def display_alias():
    return redirect('/carousel')


@app.route('/tv-stream')
def tv_stream_page():
    return Response(
        """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>TV Stream Display</title>
  <style>
    html, body { margin: 0; width: 100%; height: 100%; background: #000; overflow: hidden; }
    iframe { width: 100%; height: 100%; border: 0; }
    #badge {
      position: fixed; bottom: 10px; right: 10px; z-index: 9999;
      background: rgba(0,0,0,0.5); color: #b8dfff; font-family: monospace;
      border: 1px solid rgba(120,180,255,0.35); border-radius: 6px; padding: 6px 8px; font-size: 11px;
    }
  </style>
</head>
<body>
  <iframe id="stream" allowfullscreen></iframe>
  <div id="badge">TV stream mode</div>
  <script>
    (function () {
      var host = window.location.hostname;
      var src = "http://" + host + ":8081/vnc.html?autoconnect=1&resize=scale&clip=0&view_only=1&reconnect=1&show_dot=0&quality=9&compression=0";
      document.getElementById("stream").src = src;
    })();
  </script>
</body>
</html>""",
        mimetype="text/html",
    )


@app.route('/tv-auth')
def tv_auth_page():
    return Response(
        """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>TV Local Auth</title>
  <style>
    body { margin: 0; font-family: Arial, sans-serif; background: #0b1013; color: #e8f7ff; }
    .top { padding: 14px; border-bottom: 1px solid #20323c; background: #10181d; }
    .muted { color: #9fc1ce; font-size: 13px; }
    .controls { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; }
    button, a.btn { background: #0b5cab; color: #fff; border: 0; border-radius: 6px; padding: 8px 12px; cursor: pointer; text-decoration: none; }
    button.secondary, a.btn.secondary { background: #2b404c; }
    #status { margin-top: 8px; font-size: 13px; color: #8fd2e8; }
    #hint { margin-top: 6px; font-size: 12px; color: #ffd28c; }
    .frame { width: 100vw; height: calc(100vh - 150px); border: 0; background: #000; }
  </style>
</head>
<body>
  <div class="top">
    <strong>TV Local Authentication</strong>
    <div class="muted">Use this on the TV browser so login and carousel share the same browser session.</div>
    <div class="controls">
      <button id="prev-btn" class="secondary" type="button">Previous Login</button>
      <button id="next-btn" type="button">Next Login</button>
      <button id="fullpage-btn" type="button">Open Current Login Full Page</button>
      <button id="open-tab-btn" class="secondary" type="button">Open Login In New Tab</button>
      <a class="btn" href="/tv-control">Open TV Rotation Control</a>
      <a class="btn secondary" href="/admin" target="_blank">Admin</a>
    </div>
    <div id="status">Loading login pages...</div>
    <div id="hint">If Microsoft/SSO login fails in embedded view, use "Open Current Login Full Page" or use TV Rotation Control.</div>
  </div>
  <iframe id="auth-frame" class="frame" allowfullscreen></iframe>
  <script>
    (function () {
      const frame = document.getElementById("auth-frame");
      const status = document.getElementById("status");
      const nextBtn = document.getElementById("next-btn");
      const prevBtn = document.getElementById("prev-btn");
      const openTabBtn = document.getElementById("open-tab-btn");
      const fullpageBtn = document.getElementById("fullpage-btn");
      let pages = [];
      let idx = 0;

      function render() {
        if (!pages.length) {
          status.textContent = "No approved login pages configured.";
          frame.removeAttribute("src");
          return;
        }
        const item = pages[idx];
        frame.src = item.login_url;
        status.textContent = (idx + 1) + " / " + pages.length + " Login: " + item.login_url + " | Display: " + item.display_url;
      }

      nextBtn.addEventListener("click", function () {
        if (!pages.length) return;
        idx = (idx + 1) % pages.length;
        render();
      });

      prevBtn.addEventListener("click", function () {
        if (!pages.length) return;
        idx = (idx - 1 + pages.length) % pages.length;
        render();
      });

      openTabBtn.addEventListener("click", function () {
        if (!pages.length) return;
        window.open(pages[idx].login_url, "_blank");
      });

      fullpageBtn.addEventListener("click", function () {
        if (!pages.length) return;
        window.location.href = pages[idx].login_url;
      });

      fetch("/api/public/tv-auth-config", { cache: "no-store" })
        .then((res) => res.json())
        .then((cfg) => {
          pages = Array.isArray(cfg.pages) ? cfg.pages : [];
          idx = 0;
          render();
        })
        .catch(() => {
          status.textContent = "Could not load TV auth config.";
        });
    })();
  </script>
</body>
</html>""",
        mimetype="text/html",
    )


@app.route('/tv-control')
def tv_control_page():
    return Response(
        """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>TV Remote Control</title>
  <style>
    :root {
      --bg: #0b1013;
      --panel: #111920;
      --panel-2: #0f161c;
      --line: #22333f;
      --text: #e8f7ff;
      --muted: #9fc1ce;
      --primary: #0b5cab;
      --secondary: #2b404c;
      --warn: #9c3c3c;
      --ok: #1f7a49;
    }
    * { box-sizing: border-box; }
    body { margin: 0; font-family: Arial, sans-serif; background: var(--bg); color: var(--text); }
    .wrap {
      width: 100%;
      max-width: none;
      margin: 0;
      padding: clamp(10px, 1.2vw, 18px);
    }
    .topbar { display: flex; gap: 10px; align-items: center; justify-content: space-between; flex-wrap: wrap; margin-bottom: 12px; }
    details.panel summary { cursor: pointer; list-style: none; font-weight: 700; color: #bfe9ff; }
    details.panel summary::-webkit-details-marker { display: none; }
    .progress-wrap { margin-top: 8px; }
    .progress-track { width: 100%; height: 12px; border-radius: 999px; background: #1a2831; border: 1px solid var(--line); overflow: hidden; }
    .progress-fill { height: 100%; width: 0%; background: linear-gradient(90deg, #0f7ae5, #36c0ff); transition: width 0.3s ease; }
    .status-line { font-size: 14px; color: #d8effb; }
    .mono-small { font-family: monospace; font-size: 12px; color: #c6e7f8; }
    .muted { color: var(--muted); font-size: 13px; margin: 0; }
    .grid {
      display: grid;
      grid-template-columns: minmax(300px, 440px) minmax(0, 1fr);
      gap: clamp(8px, 1vw, 14px);
      align-items: start;
    }
    .panel {
      border: 1px solid var(--line);
      border-radius: 10px;
      background: var(--panel);
      padding: clamp(10px, 0.9vw, 14px);
      min-width: 0;
    }
    .panel h3 { margin: 0 0 10px; font-size: 15px; color: #bfe9ff; }
    .panel p { margin: 0 0 8px; }
    .stack { display: flex; gap: 8px; flex-wrap: wrap; }
    .stack-col { display: flex; gap: 8px; flex-direction: column; }
    .btn-row { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 8px; min-width: 0; }
    button, a.btn {
      background: var(--primary);
      color: #fff;
      border: 0;
      border-radius: 8px;
      padding: 9px 12px;
      cursor: pointer;
      text-decoration: none;
      text-align: center;
      font-size: 13px;
    }
    button.secondary, a.btn.secondary { background: var(--secondary); }
    button.warn, a.btn.warn { background: var(--warn); }
    button.ok, a.btn.ok { background: var(--ok); }
    button:disabled { opacity: 0.6; cursor: default; }
    .meta-list { margin: 0; padding: 0; list-style: none; display: grid; gap: 8px; min-width: 0; }
    .meta-item { padding: 8px; border-radius: 8px; border: 1px solid var(--line); background: var(--panel-2); min-width: 0; }
    .meta-item b { display: inline-block; width: 135px; color: #b9dbec; font-weight: 600; }
    .meta-item code {
      color: #dbf5ff;
      display: inline-block;
      max-width: 100%;
      white-space: normal;
      overflow-wrap: anywhere;
      word-break: break-word;
      vertical-align: top;
    }
    .inline-controls { display: flex; gap: 8px; align-items: center; margin-top: 8px; flex-wrap: wrap; }
    .inline-controls input {
      width: 100px;
      background: #0d141a;
      color: #e8f7ff;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 8px;
    }
    #status {
      margin-top: 10px;
      min-height: 18px;
      color: #8fd2e8;
      font-size: 13px;
    }
    .viewer-wrap { border: 1px solid var(--line); border-radius: 10px; overflow: hidden; background: #000; min-width: 0; }
    .viewer-wrap iframe {
      width: 100%;
      height: clamp(460px, 72vh, 900px);
      border: 0;
      display: block;
    }
    .quick-links { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 8px; }
    .badge { display: inline-block; padding: 3px 8px; border-radius: 999px; font-size: 12px; border: 1px solid var(--line); background: var(--panel-2); color: #c7e8f9; }
    @media (max-width: 1180px) {
      .grid { grid-template-columns: 1fr; }
      .viewer-wrap iframe { height: clamp(420px, 62vh, 760px); }
    }
    @media (max-width: 760px) {
      .btn-row { grid-template-columns: 1fr; }
      .meta-item b { width: auto; display: block; margin-bottom: 4px; }
      .quick-links { flex-direction: column; }
      .quick-links .btn, .quick-links button { width: 100%; }
      .inline-controls { align-items: stretch; }
      .inline-controls input { width: 100%; }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="topbar">
      <div>
        <h2 style="margin:0;">TV Control Center</h2>
        <p class="muted">Use this page to handle login, rotation, and stream controls for operators.</p>
      </div>
      <span class="badge">TV should open <code>/tv</code></span>
    </div>

    <div class="grid">
      <div class="stack-col">
        <div class="panel">
          <h3>Rotation Controls</h3>
          <div class="btn-row">
            <button id="start-rotation-btn" class="ok" type="button">Start Rotation</button>
            <button id="stop-rotation-btn" class="warn" type="button">Stop Rotation</button>
            <button id="prev-page-btn" class="secondary" type="button">Previous Page</button>
            <button id="next-page-btn" class="secondary" type="button">Next Page</button>
            <button id="open-display-btn" class="secondary" type="button">Show Current Page on TV</button>
            <button id="fullscreen-toggle-btn" class="secondary" type="button">Fullscreen Lock: ON</button>
          </div>
          <div class="inline-controls">
            <strong>Interval (sec)</strong>
            <input id="interval-input" type="number" min="1" value="30">
            <button id="apply-interval-btn" class="secondary" type="button">Apply</button>
          </div>
        </div>

        <details class="panel">
          <summary>One-Time Login Setup</summary>
          <p class="muted" style="margin:8px 0 10px;">Use this only when signing in or re-authenticating accounts.</p>
          <h3>Login Navigation</h3>
          <div class="btn-row">
            <button id="show-login-btn" class="secondary" type="button">Show Login on Stream</button>
            <button id="prev-login-btn" class="secondary" type="button">Previous Login</button>
            <button id="next-login-btn" class="secondary" type="button">Next Login</button>
            <a id="open-vnc-btn" class="btn secondary" href="#" target="_blank">Open Full Browser Control</a>
          </div>
          <div class="quick-links">
            <a class="btn secondary" href="/admin" target="_blank">Admin</a>
            <a class="btn secondary" href="/tv-agent" target="_blank">TV Runtime Page</a>
          </div>
        </details>

        <div class="panel">
          <h3>Live Status</h3>
          <p class="status-line"><b>System:</b> <span id="system-state">Loading...</span></p>
          <div class="progress-wrap">
            <div class="progress-track"><div id="rotation-progress" class="progress-fill"></div></div>
            <div id="rotation-progress-label" class="mono-small" style="margin-top:6px;">Rotation countdown: -</div>
          </div>
          <ul class="meta-list">
            <li class="meta-item"><b>Current page:</b> <code id="display-target">-</code></li>
            <li class="meta-item"><b>View mode:</b> <code id="rotation-state">-</code></li>
            <li class="meta-item"><b>Fullscreen:</b> <code id="fullscreen-lock">-</code></li>
            <li class="meta-item"><b>Next switch:</b> <code id="next-rotation">-</code></li>
            <li class="meta-item"><b>TV stream URL:</b> <code id="tv-stream-url">-</code></li>
          </ul>
          <div class="quick-links">
            <button id="copy-stream-url-btn" class="secondary" type="button">Copy TV URL</button>
            <button id="paste-clipboard-btn" class="secondary" type="button">Paste</button>
            <a id="open-stream-url-btn" class="btn secondary" href="#" target="_blank">Open TV URL</a>
          </div>
          <div id="status">Loading config...</div>
        </div>
      </div>

      <div class="panel">
        <h3>Live Browser Session (Interactive)</h3>
        <p class="muted" style="margin-bottom:10px;">Sign in here directly when needed. This controls the same Chromium instance used for TV streaming.</p>
        <div class="viewer-wrap">
          <iframe id="embedded-vnc" allowfullscreen></iframe>
        </div>
      </div>
    </div>
  </div>
  <script>
    (function () {
      const statusEl = document.getElementById("status");
      const displayTargetEl = document.getElementById("display-target");
      const rotationStateEl = document.getElementById("rotation-state");
      const fullscreenLockEl = document.getElementById("fullscreen-lock");
      const nextRotationEl = document.getElementById("next-rotation");
      const systemStateEl = document.getElementById("system-state");
      const rotationProgressEl = document.getElementById("rotation-progress");
      const rotationProgressLabelEl = document.getElementById("rotation-progress-label");
      const streamUrlEl = document.getElementById("tv-stream-url");
      const intervalInputEl = document.getElementById("interval-input");
      const applyIntervalBtn = document.getElementById("apply-interval-btn");
      const embeddedVnc = document.getElementById("embedded-vnc");
      const openVncBtn = document.getElementById("open-vnc-btn");
      const openDisplayBtn = document.getElementById("open-display-btn");
      const showLoginBtn = document.getElementById("show-login-btn");
      const nextLoginBtn = document.getElementById("next-login-btn");
      const prevLoginBtn = document.getElementById("prev-login-btn");
      const startRotationBtn = document.getElementById("start-rotation-btn");
      const stopRotationBtn = document.getElementById("stop-rotation-btn");
      const nextPageBtn = document.getElementById("next-page-btn");
      const prevPageBtn = document.getElementById("prev-page-btn");
      const fullscreenToggleBtn = document.getElementById("fullscreen-toggle-btn");
      const copyStreamUrlBtn = document.getElementById("copy-stream-url-btn");
      const pasteClipboardBtn = document.getElementById("paste-clipboard-btn");
      const openStreamUrlBtn = document.getElementById("open-stream-url-btn");
      let loginRows = [];
      let displayPages = [];
      let loginIdx = 0;
      let displayIdx = 0;
      let rotationEnabled = false;
      let runtimeMode = "login";
      let fullscreenLock = true;
      let intervalSeconds = 30;
      let nextRotationAtMs = 0;
      let runtimeServerOffsetMs = 0;

      function setStatus(msg) {
        statusEl.textContent = msg;
      }

      function updateTargets() {
        const display = displayPages[displayIdx] || "";
        displayTargetEl.textContent = display || "-";
        const modeLabel = runtimeMode === "display" ? "display" : "login";
        const rotationLabel = rotationEnabled ? "running" : "stopped";
        rotationStateEl.textContent = modeLabel === "display" ? "Showing live pages" : "Showing login page";
        fullscreenLockEl.textContent = fullscreenLock ? "On" : "Off";
        fullscreenToggleBtn.textContent = fullscreenLock ? "Fullscreen: On" : "Fullscreen: Off";
        systemStateEl.textContent = rotationEnabled ? "Rotating pages automatically" : "Manual control mode";
        if (intervalInputEl && !document.activeElement?.isSameNode(intervalInputEl)) {
          intervalInputEl.value = String(intervalSeconds || 30);
        }
      }

      function renderCountdown() {
        if (!(rotationEnabled && runtimeMode === "display" && nextRotationAtMs > 0)) {
          nextRotationEl.textContent = "-";
          rotationProgressEl.style.width = "0%";
          rotationProgressLabelEl.textContent = "Rotation countdown: paused";
          return;
        }

        const nowMs = Date.now() + runtimeServerOffsetMs;
        const remainingMs = Math.max(0, nextRotationAtMs - nowMs);
        const maxMs = Math.max(1000, (intervalSeconds || 30) * 1000);
        const elapsedPct = Math.max(0, Math.min(100, ((maxMs - remainingMs) / maxMs) * 100));
        const remainingSecPrecise = remainingMs / 1000;
        const remainingSecWhole = Math.ceil(remainingSecPrecise);

        nextRotationEl.textContent = remainingSecWhole + "s";
        rotationProgressEl.style.width = String(elapsedPct) + "%";
        rotationProgressLabelEl.textContent = "Rotation countdown: " + remainingSecWhole + "s remaining";
      }

      async function sendCommand(command) {
        const res = await fetch("/api/tv-runtime/command", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ command: command })
        });
        if (!res.ok) throw new Error("command failed");
        return res.json();
      }

      async function refreshRuntime() {
        try {
          const runtime = await fetch("/api/tv-runtime", { cache: "no-store" }).then((r) => r.json());
          const localNowMs = Date.now();
          const serverNowMs = Number(runtime.server_now_ms || 0);
          runtimeServerOffsetMs = serverNowMs > 0 ? (serverNowMs - localNowMs) : 0;
          loginIdx = runtime.login_index || 0;
          displayIdx = Number.isInteger(runtime.active_display_index) ? runtime.active_display_index : (runtime.display_index || 0);
          rotationEnabled = !!runtime.rotation_enabled;
          runtimeMode = runtime.mode === "display" ? "display" : "login";
          fullscreenLock = runtime.fullscreen_lock !== false;
          intervalSeconds = runtime.interval_seconds || intervalSeconds || 30;
          nextRotationAtMs = Number(runtime.next_rotation_at || 0);
          renderCountdown();
          updateTargets();
        } catch (e) {
          setStatus("Could not load runtime state.");
        }
      }

      async function runCommand(command, okMsg) {
        try {
          await sendCommand(command);
          await refreshRuntime();
          setStatus(okMsg);
        } catch (e) {
          setStatus("Command failed.");
        }
      }

      async function applyInterval() {
        const parsed = parseInt(intervalInputEl.value, 10);
        const interval = Number.isFinite(parsed) && parsed > 0 ? parsed : 30;
        try {
          const res = await fetch("/api/tv-runtime/interval", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ interval: interval })
          });
          if (!res.ok) throw new Error("interval update failed");
          const payload = await res.json();
          intervalSeconds = payload.interval || interval;
          await refreshRuntime();
          setStatus("Rotation interval updated to " + intervalSeconds + "s.");
        } catch (e) {
          setStatus("Failed to update interval.");
        }
      }

      async function copyStreamUrl() {
        try {
          await navigator.clipboard.writeText(streamUrlEl.textContent || "");
          setStatus("TV URL copied to clipboard.");
        } catch (e) {
          setStatus("Could not copy TV URL.");
        }
      }

      async function pasteClipboardToBrowser() {
        try {
          const text = await navigator.clipboard.readText();
          if (!text) {
            setStatus("Clipboard is empty.");
            return;
          }
          const res = await fetch("/api/tv-runtime/paste", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text: text })
          });
          if (!res.ok) throw new Error("paste relay failed");
          setStatus("Pasted clipboard text to active browser field.");
        } catch (e) {
          setStatus("Could not paste. Click into a field in browser view and allow clipboard access.");
        }
      }

      showLoginBtn.addEventListener("click", function () { runCommand("show_login", "Requested login view on TV."); });
      nextLoginBtn.addEventListener("click", function () { runCommand("next_login", "Moved to next login page."); });
      prevLoginBtn.addEventListener("click", function () { runCommand("prev_login", "Moved to previous login page."); });
      openDisplayBtn.addEventListener("click", function () { runCommand("show_display", "Requested current display on TV."); });
      startRotationBtn.addEventListener("click", function () { runCommand("start_rotation", "Rotation started."); });
      stopRotationBtn.addEventListener("click", function () { runCommand("stop_rotation", "Rotation stopped."); });
      nextPageBtn.addEventListener("click", function () { runCommand("next_display", "Moved to next display page."); });
      prevPageBtn.addEventListener("click", function () { runCommand("prev_display", "Moved to previous display page."); });
      fullscreenToggleBtn.addEventListener("click", function () { runCommand("toggle_fullscreen_lock", "Fullscreen setting updated."); });
      applyIntervalBtn.addEventListener("click", applyInterval);
      copyStreamUrlBtn.addEventListener("click", copyStreamUrl);
      pasteClipboardBtn.addEventListener("click", pasteClipboardToBrowser);

      fetch("/api/public/tv-auth-config", { cache: "no-store" })
        .then((res) => res.json())
        .then((cfg) => {
          loginRows = Array.isArray(cfg.pages) ? cfg.pages : [];
          displayPages = loginRows.map((row) => row.display_url).filter(Boolean);
          refreshRuntime();
          if (!loginRows.length) {
            setStatus("No approved rows configured in admin.");
          } else {
            setStatus("Ready. Ensure TV is on /tv.");
          }
        })
        .catch(() => {
          setStatus("Could not load config.");
        });

      setInterval(refreshRuntime, 3000);
      setInterval(renderCountdown, 200);

      var host = window.location.hostname;
      streamUrlEl.textContent = "http://" + host + ":8084/tv";
      openStreamUrlBtn.href = streamUrlEl.textContent;
      embeddedVnc.src = "http://" + host + ":8081/vnc.html?autoconnect=1&resize=scale&clip=0&reconnect=1&view_only=0&show_dot=0&quality=9&compression=0";
      openVncBtn.href = "http://" + host + ":8081/vnc.html?autoconnect=1&resize=scale&clip=0&reconnect=1&view_only=0&show_dot=0&quality=9&compression=0";
    })();
  </script>
</body>
</html>""",
        mimetype="text/html",
    )


@app.route('/tv-agent')
def tv_agent_page():
    return Response(
        """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>TV Agent</title>
  <style>
    html, body { margin: 0; width: 100%; height: 100%; background: #000; overflow: hidden; }
    #stage { width: 100%; height: 100%; border: 0; background: #000; }
  </style>
</head>
<body>
  <iframe id="stage" allowfullscreen></iframe>
  <script>
    (function () {
      const stage = document.getElementById("stage");
      let displayWindow = null;
      let rows = [];
      let intervalSec = 30;
      let lastNonce = 0;
      let lastUrl = "";

      function setStatus(msg) {}
      function loginUrl(idx) {
        if (!rows.length) return "";
        return rows[((idx % rows.length) + rows.length) % rows.length].login_url || "";
      }

      function displayUrl(idx) {
        if (!rows.length) return "";
        return rows[((idx % rows.length) + rows.length) % rows.length].display_url || "";
      }

      function go(url) {
        if (!url) return;
        if (url === lastUrl) return;
        stage.src = url;
        lastUrl = url;
        setStatus("Showing: " + url);
      }

      function refreshConfig() {
        return fetch("/api/public/tv-auth-config", { cache: "no-store" })
          .then((r) => r.json())
          .then((cfg) => {
            rows = Array.isArray(cfg.pages) ? cfg.pages : [];
            intervalSec = parseInt(cfg.interval, 10) || 30;
          });
      }

      function poll() {
        fetch("/api/tv-runtime", { cache: "no-store" })
          .then((r) => r.json())
          .then((runtime) => {
            if (!rows.length) return;
            const nonce = runtime.command_nonce || 0;
            if (nonce !== lastNonce) {
              lastNonce = nonce;
              if (runtime.mode === "login") {
                go(loginUrl(runtime.login_index || 0));
              } else {
                go(displayUrl(runtime.display_index || 0));
              }
            }
            if (runtime.rotation_enabled && runtime.mode === "display") {
              const idx = Number.isInteger(runtime.active_display_index)
                ? runtime.active_display_index
                : (runtime.display_index || 0);
              go(displayUrl(idx));
            }
          })
          .catch(() => {});
      }

      refreshConfig().then(function () {
        setInterval(function () {
          refreshConfig().then(poll);
        }, 4000);
        poll();
      });
    })();
  </script>
</body>
</html>""",
        mimetype="text/html",
    )


@app.route('/tv')
def tv_single_url():
    return redirect("/tv-stream")


@app.route('/config.js')
def config_legacy():
    public_state = get_public_config()
    pages_json = "[" + ",".join(f'"{p}"' for p in public_state["pages"]) + "]"
    js = f'window.CAROUSEL_CONFIG = {{ pages: {pages_json}, interval: {public_state["interval"]}, version: {public_state["version"]} }};'
    return Response(js, mimetype='application/javascript', headers={'Cache-Control': 'no-cache'})

def get_public_config():
    state = load_state()
    approved_pages = [p["display_url"] for p in state["pages"] if p.get("approved")]
    return {"pages": approved_pages, "interval": state["interval"], "version": state["version"]}


def get_tv_auth_config():
    state = load_state()
    rows = []
    for p in state.get("pages") or []:
        if not p.get("approved"):
            continue
        display_url = (p.get("display_url") or "").strip()
        login_url = (p.get("login_url") or display_url).strip()
        if not display_url:
            continue
        rows.append({"login_url": login_url, "display_url": display_url})
    return {"pages": rows, "interval": state["interval"], "version": state["version"]}


def _load_tv_runtime():
    defaults = {
        "mode": "login",
        "login_index": 0,
        "display_index": 0,
        "fullscreen_lock": True,
        "rotation_enabled": False,
        "rotation_started_at": 0,
        "command_nonce": 0,
        "updated_at": int(time.time() * 1000),
    }
    try:
        with open(_tv_runtime_path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except Exception:
        payload = {}
    merged = defaults.copy()
    merged.update({k: payload.get(k, defaults[k]) for k in defaults})
    return merged


def _save_tv_runtime(state):
    os.makedirs(os.path.dirname(_tv_runtime_path), exist_ok=True)
    state["updated_at"] = int(time.time() * 1000)
    with open(_tv_runtime_path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
    return state


def _update_tv_runtime(command: str):
    state = _load_tv_runtime()
    cfg = get_tv_auth_config()
    rows = cfg.get("pages") or []
    display_count = len(rows)

    def bump_nonce():
        state["command_nonce"] = int(state.get("command_nonce") or 0) + 1

    if command == "show_display":
        state["mode"] = "display"
        state["rotation_enabled"] = False
        bump_nonce()
    elif command == "show_login":
        state["mode"] = "login"
        state["rotation_enabled"] = False
        bump_nonce()
    elif command == "next_display" and display_count:
        state["mode"] = "display"
        state["rotation_enabled"] = False
        state["display_index"] = (int(state.get("display_index") or 0) + 1) % display_count
        bump_nonce()
    elif command == "prev_display" and display_count:
        state["mode"] = "display"
        state["rotation_enabled"] = False
        state["display_index"] = (int(state.get("display_index") or 0) - 1 + display_count) % display_count
        bump_nonce()
    elif command == "start_rotation":
        state["mode"] = "display"
        state["rotation_enabled"] = True
        state["rotation_started_at"] = int(time.time() * 1000)
        bump_nonce()
    elif command == "next_login" and display_count:
        state["mode"] = "login"
        state["rotation_enabled"] = False
        state["login_index"] = (int(state.get("login_index") or 0) + 1) % display_count
        bump_nonce()
    elif command == "prev_login" and display_count:
        state["mode"] = "login"
        state["rotation_enabled"] = False
        state["login_index"] = (int(state.get("login_index") or 0) - 1 + display_count) % display_count
        bump_nonce()
    elif command == "stop_rotation":
        state["rotation_enabled"] = False
        bump_nonce()
    elif command == "toggle_fullscreen_lock":
        state["fullscreen_lock"] = not bool(state.get("fullscreen_lock", True))
        bump_nonce()
    elif command == "enable_fullscreen_lock":
        state["fullscreen_lock"] = True
        bump_nonce()
    elif command == "disable_fullscreen_lock":
        state["fullscreen_lock"] = False
        bump_nonce()

    return _save_tv_runtime(state)


def _runtime_with_meta():
    state = _load_tv_runtime()
    cfg = get_tv_auth_config()
    rows = cfg.get("pages") or []
    count = len(rows)
    interval_seconds = _safe_int(cfg.get("interval", 30), 30)
    now_ms = int(time.time() * 1000)

    runtime = dict(state)
    runtime["server_now_ms"] = now_ms
    runtime["interval_seconds"] = interval_seconds

    base_display_idx = int(runtime.get("display_index") or 0)
    if count > 0:
        base_display_idx %= count
    runtime["active_display_index"] = base_display_idx
    runtime["seconds_until_next"] = None
    runtime["next_rotation_at"] = 0

    if (
        count > 0
        and runtime.get("mode") == "display"
        and bool(runtime.get("rotation_enabled"))
    ):
        started_at = int(runtime.get("rotation_started_at") or now_ms)
        elapsed_ms = max(0, now_ms - started_at)
        step_ms = max(1, interval_seconds) * 1000
        steps = elapsed_ms // step_ms
        runtime["active_display_index"] = (base_display_idx + steps) % count
        next_at = started_at + ((steps + 1) * step_ms)
        runtime["next_rotation_at"] = int(next_at)
        runtime["seconds_until_next"] = int(max(0, (next_at - now_ms + 999) // 1000))

    return runtime

@app.route("/carousel-api/public-config")
def public_config():
    return jsonify(get_public_config())


@app.route("/api/public/state")
def public_state():
    return jsonify(get_public_state())


@app.route("/api/public/tv-auth-config")
def public_tv_auth_config():
    return jsonify(get_tv_auth_config())


@app.route("/api/tv-runtime")
def tv_runtime():
    return jsonify(_runtime_with_meta())


@app.route("/api/tv-runtime/command", methods=["POST"])
def tv_runtime_command():
    body = request.get_json(silent=True) or {}
    command = str(body.get("command") or "").strip()
    allowed = {
        "show_display",
        "show_login",
        "next_login",
        "prev_login",
        "next_display",
        "prev_display",
        "start_rotation",
        "stop_rotation",
        "toggle_fullscreen_lock",
        "enable_fullscreen_lock",
        "disable_fullscreen_lock",
    }
    if command not in allowed:
        return jsonify({"ok": False, "error": "Invalid command"}), 400
    _update_tv_runtime(command)
    return jsonify({"ok": True, "state": _runtime_with_meta()})


def get_public_state():
    cfg = get_public_config()
    bundle = _load_session_bundle()
    origins = []
    for entry in bundle.get("entries") or []:
        origin = (entry.get("origin") or "").strip()
        if origin:
            origins.append(origin)
    return {
        "pages": cfg.get("pages") or [],
        "interval": cfg.get("interval") or 30,
        "version": cfg.get("version") or int(time.time()),
        "auth": {
            "ready": len(origins) > 0,
            "captured_at": bundle.get("captured_at") or 0,
            "origins": origins,
            "entry_count": len(origins),
        },
    }

@app.route("/api/admin/login", methods=["POST"])
def admin_login():
    body = request.get_json(silent=True) or {}
    password = str(body.get("password", ""))
    if password != _admin_password:
        return jsonify({"ok": False, "error": "Invalid password"}), 401
    session["is_admin"] = True
    return jsonify({"ok": True})

@app.route("/api/admin/logout", methods=["POST"])
def admin_logout():
    session.clear()
    return jsonify({"ok": True})

@app.route("/api/admin/config")
def admin_config():
    if not require_admin():
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    return jsonify(load_state())

@app.route("/api/admin/config", methods=["POST"])
def admin_config_update():
    if not require_admin():
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    body = request.get_json(silent=True) or {}
    state = {
        "interval": body.get("interval", 30),
        "pages": body.get("pages", []),
        "version": body.get("version", int(time.time())),
    }
    saved = save_state(state)
    return jsonify({"ok": True, "state": saved})


@app.route("/api/tv-runtime/interval", methods=["POST"])
def tv_runtime_interval_update():
    body = request.get_json(silent=True) or {}
    interval = _safe_int(body.get("interval"), 30)
    current = load_state()
    updated = {
        "pages": current.get("pages") or [],
        "interval": interval,
        "version": current.get("version", int(time.time())),
    }
    saved = save_state(updated)
    return jsonify({"ok": True, "interval": saved.get("interval", interval), "version": saved.get("version")})


@app.route("/api/admin/apply-browser-config", methods=["POST"])
def admin_apply_browser_config():
    if not require_admin():
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    req = urlrequest.Request("http://rotator-control:8082/reset-login-tabs", method="POST")
    try:
        with urlrequest.urlopen(req, timeout=4) as resp:
            ok = 200 <= getattr(resp, "status", 500) < 400
    except (error.URLError, TimeoutError):
        ok = False
    if not ok:
        return jsonify({"ok": False, "error": "Could not apply config to browser"}), 502
    return jsonify({"ok": True})


@app.route("/api/tv-runtime/paste", methods=["POST"])
def tv_runtime_paste():
    body = request.get_json(silent=True) or {}
    text = str(body.get("text") or "")
    target = "http://rotator-control:8082/type-text"
    payload = urlencode({"text": text}).encode("utf-8")
    req = urlrequest.Request(
        target,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlrequest.urlopen(req, timeout=4) as resp:
            ok = 200 <= getattr(resp, "status", 500) < 400
    except (error.URLError, TimeoutError):
        ok = False
    if not ok:
        return jsonify({"ok": False, "error": "Could not send paste text"}), 502
    return jsonify({"ok": True})


@app.route("/api/admin/session-bundle")
def admin_session_bundle():
    if not require_admin():
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    bundle = _load_session_bundle()
    entries = []
    for entry in bundle.get("entries") or []:
        origin = (entry.get("origin") or "").strip()
        if not origin:
            continue
        entries.append(
            {
                "origin": origin,
                "cookie_count": len(entry.get("cookies") or []),
                "local_storage_count": len(entry.get("local_storage") or {}),
                "session_storage_count": len(entry.get("session_storage") or {}),
            }
        )
    return jsonify(
        {
            "ok": True,
            "captured_at": bundle.get("captured_at") or 0,
            "entries": entries,
        }
    )

@app.route("/admin")
def admin_page():
    return Response("""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Carousel Admin</title>
  <style>
    body { font-family: monospace; background: #111; color: #d5f7ff; max-width: 900px; margin: 0 auto; padding: 24px; }
    h1 { margin-bottom: 18px; }
    .panel { border: 1px solid #3e6f7a; border-radius: 10px; padding: 18px; margin-bottom: 16px; background: #151a1d; }
    button { background: #0d9ab8; border: none; color: #041012; padding: 8px 12px; font-weight: bold; border-radius: 6px; cursor: pointer; }
    button.secondary { background: #27424a; color: #d5f7ff; }
    button.warn { background: #d07f27; color: #130b04; }
    input, textarea { width: 100%; background: #0f1417; color: #e9faff; border: 1px solid #36535b; border-radius: 6px; padding: 8px; margin-top: 8px; }
    table { width: 100%; border-collapse: collapse; margin-top: 10px; }
    th, td { border-bottom: 1px solid #2b4349; padding: 8px; vertical-align: top; }
    th { text-align: left; color: #9cefff; font-size: 12px; }
    .url-input { margin-top: 0; }
    .small { opacity: 0.8; font-size: 12px; }
    .hidden { display: none; }
  </style>
</head>
<body>
  <h1>Carousel Admin Panel</h1>
  <div class="panel" id="login-panel">
    <p>Log in to manage and approve carousel URLs.</p>
    <input id="password" type="password" placeholder="Admin password">
    <button id="login-btn">Login</button>
    <p id="login-error" class="small"></p>
  </div>
  <div class="panel hidden" id="admin-panel">
    <label>Interval (seconds)</label>
    <input id="interval" type="number" min="1" value="30">
    <p class="small">Set a login URL and a display URL per site. Use Login Helper to sign in first, then only approved display URLs rotate.</p>
    <table>
      <thead>
        <tr>
          <th>Login URL</th>
          <th>Display URL (after login)</th>
          <th>Approved</th>
          <th>Remove</th>
        </tr>
      </thead>
      <tbody id="rows"></tbody>
    </table>
    <div style="display:flex; gap:8px; margin-top:10px;">
      <button id="add-row-btn" class="secondary">Add Row</button>
      <button id="save-btn">Save Changes</button>
      <button id="apply-browser-btn" class="secondary">Apply To Browser Now</button>
      <button id="reload-btn" class="secondary">Reload</button>
      <button id="logout-btn" class="warn">Logout</button>
    </div>
    <p id="status" class="small"></p>
  </div>
  <script>
    const loginPanel = document.getElementById('login-panel');
    const adminPanel = document.getElementById('admin-panel');
    const rowsEl = document.getElementById('rows');
    const statusEl = document.getElementById('status');

    function setStatus(msg) { statusEl.textContent = msg || ''; }
    function rowHtml(row = {login_url:'', display_url:'', approved:true}) {
      const login = (row.login_url || '').replaceAll('"', '&quot;');
      const display = (row.display_url || '').replaceAll('"', '&quot;');
      return `
        <tr>
          <td><input class="url-input login-url" type="text" value="${login}" placeholder="https://.../login"></td>
          <td><input class="url-input display-url" type="text" value="${display}" placeholder="https://.../target-page"></td>
          <td style="text-align:center;"><input class="approved" type="checkbox" ${row.approved ? 'checked' : ''}></td>
          <td style="text-align:center;"><button type="button" class="warn remove-row-btn">X</button></td>
        </tr>
      `;
    }

    function renderRows(pages) {
      rowsEl.innerHTML = (pages || []).map((p) => rowHtml(p)).join('');
      bindRowEvents();
      if (!rowsEl.children.length) addRow();
    }

    function addRow() {
      rowsEl.insertAdjacentHTML('beforeend', rowHtml());
      bindRowEvents();
    }

    function bindRowEvents() {
      rowsEl.querySelectorAll('.remove-row-btn').forEach((btn) => {
        btn.onclick = () => {
          btn.closest('tr').remove();
          if (!rowsEl.children.length) addRow();
        };
      });
    }

    function readRows() {
      return Array.from(rowsEl.querySelectorAll('tr')).map((tr) => {
        const loginUrl = tr.querySelector('.login-url')?.value?.trim() || '';
        const displayUrl = tr.querySelector('.display-url')?.value?.trim() || '';
        const approved = !!tr.querySelector('.approved')?.checked;
        return { login_url: loginUrl || displayUrl, display_url: displayUrl, approved };
      }).filter((r) => !!r.display_url);
    }

    async function loadAdminConfig() {
      const res = await fetch('/api/admin/config');
      if (res.status === 401) {
        loginPanel.classList.remove('hidden');
        adminPanel.classList.add('hidden');
        return null;
      }
      const state = await res.json();
      loginPanel.classList.add('hidden');
      adminPanel.classList.remove('hidden');
      document.getElementById('interval').value = state.interval || 30;
      renderRows(state.pages || []);
      setStatus('Loaded.');
      return state;
    }
    document.getElementById('add-row-btn').addEventListener('click', addRow);

    document.getElementById('login-btn').addEventListener('click', async () => {
      const password = document.getElementById('password').value;
      const res = await fetch('/api/admin/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password }) });
      if (!res.ok) {
        document.getElementById('login-error').textContent = 'Login failed.';
        return;
      }
      document.getElementById('login-error').textContent = '';
      await loadAdminConfig();
    });

    document.getElementById('save-btn').addEventListener('click', async () => {
      const interval = parseInt(document.getElementById('interval').value, 10) || 30;
      const payload = {
        interval,
        pages: readRows()
      };
      const res = await fetch('/api/admin/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      if (!res.ok) {
        setStatus('Save failed.');
        return;
      }
      setStatus('Saved. Use Auth Portal to authenticate each site.');
      await loadAdminConfig();
    });

    document.getElementById('reload-btn').addEventListener('click', loadAdminConfig);
    document.getElementById('apply-browser-btn').addEventListener('click', async () => {
      setStatus('Applying config to browser tabs...');
      const res = await fetch('/api/admin/apply-browser-config', { method: 'POST' });
      if (!res.ok) {
        setStatus('Apply failed. Try again.');
        return;
      }
      setStatus('Applied. Browser tabs were reset to current approved URLs.');
    });
    document.getElementById('logout-btn').addEventListener('click', async () => {
      await fetch('/api/admin/logout', { method: 'POST' });
      loginPanel.classList.remove('hidden');
      adminPanel.classList.add('hidden');
      setStatus('');
    });

    loadAdminConfig();
  </script>
</body>
</html>""", mimetype="text/html")

@app.route('/auth')
def auth_portal():
    state = load_state()
    page_list = state.get("pages", [])
    rows = ''.join(
        f'<tr>'
        f'<td>{(p.get("login_url") or p.get("display_url") or "")}</td>'
        f'<td>{(p.get("display_url") or "")}</td>'
        f'</tr>'
        for p in page_list
        if (p.get("login_url") or p.get("display_url"))
    )
    html = f'''<!DOCTYPE html>
<html><head><title>Network Auth Portal</title>
<style>
body{{font-family:Arial,sans-serif;padding:24px;background:#101417;color:#e6f6ff}}
h2{{margin:0 0 10px}}
a{{color:#6fd3ff}}
table{{border-collapse:collapse;width:100%;max-width:1100px;background:#151b1f}}
th,td{{border:1px solid #2a3942;padding:10px;text-align:left;vertical-align:top}}
.muted{{color:#9fb6c2}}
.controls{{margin:12px 0 14px;display:flex;gap:10px;flex-wrap:wrap}}
button{{background:#0b5cab;border:none;color:#fff;padding:10px 14px;border-radius:6px;cursor:pointer}}
.viewer-wrap{{width:100%;max-width:1280px;height:720px;border:1px solid #2a3942;border-radius:8px;overflow:hidden;background:#000}}
.viewer-wrap iframe{{width:100%;height:100%;border:0}}
</style>
</head><body>
<h2>Network Authentication Portal</h2>
<p class="muted">Use the embedded browser below to sign in. This is the live Chromium auth-helper session from the container.</p>
<p><a href="/carousel" target="_blank">Open TV Carousel Display</a> | <a href="/tv-auth" target="_blank">Open TV Local Auth</a> | <a href="/admin" target="_blank">Open Admin</a></p>
<div class="controls">
  <form id="capture-form" method="post" action="#" target="capture-target">
    <button type="submit">Capture Session For Carousel</button>
  </form>
  <form id="reset-tabs-form" method="post" action="#" target="capture-target">
    <button type="submit">Open Login Tabs</button>
  </form>
  <form id="type-form" method="post" action="#" target="capture-target">
    <input id="paste-text" name="text" type="text" placeholder="Paste text here, then click Send to active field" style="min-width:380px;padding:8px;border-radius:6px;border:1px solid #2a3942;background:#0f1417;color:#e6f6ff;">
    <button type="submit">Send To Active Field</button>
  </form>
  <a id="control-link" href="#" target="_blank">Open Control Page</a>
</div>
<iframe name="capture-target" style="display:none;"></iframe>
<div class="viewer-wrap">
  <iframe id="vnc-viewer" allowfullscreen></iframe>
</div>
<p class="muted">When login is complete, click <strong>Capture Session For Carousel</strong>, then open the TV Carousel Display. If TV still shows login, use <strong>TV Local Auth</strong> on the TV browser.</p>
<table>
<thead><tr><th>Login URL</th><th>Display URL</th></tr></thead>
<tbody>{rows}</tbody>
</table>
<p class="muted">Note: TV display uses direct page URLs (no proxy rendering).</p>
<script>
  (function () {{
    var host = window.location.hostname;
    var src = "http://" + host + ":8081/vnc.html?autoconnect=1&resize=scale&clip=0&reconnect=1&quality=9&compression=0";
    document.getElementById("vnc-viewer").src = src;
    document.getElementById("capture-form").action = "http://" + host + ":8083/capture-session";
    document.getElementById("reset-tabs-form").action = "http://" + host + ":8083/reset-login-tabs";
    document.getElementById("type-form").action = "http://" + host + ":8083/type-text";
    document.getElementById("control-link").href = "http://" + host + ":8083/control";
    // Operator can explicitly click "Open Login Tabs" if browser state is stale.
  }})();
</script>
</body></html>'''
    return Response(html, mimetype='text/html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)
