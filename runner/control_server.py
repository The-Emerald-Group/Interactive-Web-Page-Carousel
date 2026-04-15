import json
import os
from pathlib import Path
from urllib import error, request as urlrequest

from flask import Flask, redirect, render_template_string, request, url_for

app = Flask(__name__)
START_FLAG = Path("/app/control/start_rotation.flag")
CAPTURE_FLAG = Path("/app/control/capture_session.flag")
SESSION_BUNDLE = Path("/app/control/session_bundle.json")
TEXT_INJECT = Path("/app/control/inject_text.txt")
RESET_LOGIN_TABS = Path("/app/control/reset_login_tabs.flag")
HYBRID_API_BASE = (os.getenv("HYBRID_API_BASE", "http://hybrid-carousel") or "").rstrip("/")

CONTROL_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Carousel Control</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 24px; }
    .card { max-width: 560px; border: 1px solid #d0d7de; border-radius: 8px; padding: 16px; }
    .ok { color: #0a7a22; }
    .muted { color: #555; }
    button { padding: 10px 14px; border: 0; border-radius: 6px; cursor: pointer; }
    .start { background: #0a7a22; color: #fff; }
    .reset { background: #a52727; color: #fff; margin-left: 8px; }
  </style>
</head>
<body>
  <div class="card">
    <h2>TV Stream Remote Control</h2>
    <p class="muted">Use this page from your PC to control what the TV stream shows.</p>
    <p><a href="/tv-carousel" target="_blank">Open TV Stream URL</a></p>
    <p><a href="/display" target="_blank">Open Chromium Viewer</a></p>
    <p>Status:
      {% if started %}
      <strong class="ok">Rotation started</strong>
      {% else %}
      <strong>Waiting for start</strong>
      {% endif %}
    </p>
    <p>Session bundle:
      {% if bundle_ready %}
      <strong class="ok">Available</strong>
      {% else %}
      <strong>Not captured yet</strong>
      {% endif %}
    </p>
    <p class="muted">Last captured at: {{ bundle_mtime or "N/A" }}</p>
    <div style="margin:10px 0 14px;">
      <form method="post" action="/tv-command/start_rotation" style="display:inline">
        <button class="start" type="submit">Start Rotation</button>
      </form>
      <form method="post" action="/tv-command/stop_rotation" style="display:inline">
        <button class="reset" type="submit">Stop Rotation</button>
      </form>
      <form method="post" action="/tv-command/prev_display" style="display:inline">
        <button class="start" type="submit" style="background:#27424a;">Prev Page</button>
      </form>
      <form method="post" action="/tv-command/next_display" style="display:inline">
        <button class="start" type="submit" style="background:#27424a;">Next Page</button>
      </form>
      <form method="post" action="/tv-command/show_display" style="display:inline">
        <button class="start" type="submit" style="background:#0b5cab;">Show Display Mode</button>
      </form>
    </div>
    <form method="post" action="/start" style="display:inline">
      <button class="start" type="submit">Start Rotation</button>
    </form>
    <form method="post" action="/reset" style="display:inline">
      <button class="reset" type="submit">Reset (Stop signal)</button>
    </form>
    <form method="post" action="/clear-session" style="display:inline">
      <button class="reset" type="submit" style="background:#666;">Clear Session Bundle</button>
    </form>
    <p class="muted">Use the buttons above for day-to-day TV stream control. Capture/reset helpers remain below for diagnostics.</p>
    <p class="muted"><a href="/session-status" target="_blank">View session bundle status JSON</a></p>
  </div>
</body>
</html>
"""

DISPLAY_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Carousel Display</title>
  <style>
    html, body { margin: 0; width: 100%; height: 100%; background: #000; overflow: hidden; }
    iframe { width: 100%; height: 100%; border: 0; }
  </style>
</head>
<body>
  <iframe id="viewer" allowfullscreen></iframe>
  <script>
    (function () {
      var host = window.location.hostname;
      var src = "http://" + host + ":8081/vnc.html?autoconnect=1&resize=scale";
      document.getElementById("viewer").src = src;
    })();
  </script>
</body>
</html>
"""

PASSTHROUGH_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Browser Pass-Through Display</title>
  <style>
    html, body { margin: 0; width: 100%; height: 100%; background: #000; overflow: hidden; }
    iframe { width: 100%; height: 100%; border: 0; }
  </style>
</head>
<body>
  <iframe id="viewer" allowfullscreen></iframe>
  <script>
    (function () {
      var host = window.location.hostname;
      // View-only noVNC session so this acts like a display feed.
      var src = "http://" + host + ":8081/vnc.html?autoconnect=1&resize=scale&view_only=1&reconnect=1&show_dot=0";
      document.getElementById("viewer").src = src;
    })();
  </script>
</body>
</html>
"""


@app.get("/")
def index():
    host = request.host.split(":")[0]
    return redirect(f"http://{host}:8084/tv-control")


@app.get("/control")
def control():
    host = request.host.split(":")[0]
    return redirect(f"http://{host}:8084/tv-control")


@app.get("/display")
def display():
    return render_template_string(DISPLAY_HTML)


@app.get("/tv-carousel")
def tv_carousel():
    host = request.host.split(":")[0]
    return redirect(f"http://{host}:8084/carousel")


@app.get("/network-auth")
def network_auth():
    host = request.host.split(":")[0]
    return redirect(f"http://{host}:8084/tv-control")


@app.get("/session-status")
def session_status():
    if not SESSION_BUNDLE.exists():
        return {"ok": True, "ready": False, "captured_at": 0, "entries": []}
    try:
        payload = __import__("json").loads(SESSION_BUNDLE.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    entries = payload.get("entries") or []
    summary = []
    for entry in entries:
        origin = (entry.get("origin") or "").strip()
        if not origin:
            continue
        summary.append(
            {
                "origin": origin,
                "cookie_count": len(entry.get("cookies") or []),
                "local_storage_count": len(entry.get("local_storage") or {}),
                "session_storage_count": len(entry.get("session_storage") or {}),
            }
        )
    return {
        "ok": True,
        "ready": len(summary) > 0,
        "captured_at": int(payload.get("captured_at") or 0),
        "entries": summary,
    }


@app.get("/auth")
def auth_alias():
    host = request.host.split(":")[0]
    return redirect(f"http://{host}:8084/tv-control")


@app.get("/admin")
def admin_alias():
    host = request.host.split(":")[0]
    return redirect(f"http://{host}:8084/admin")


def _send_tv_command(command: str) -> bool:
    url = f"{HYBRID_API_BASE}/api/tv-runtime/command"
    payload = json.dumps({"command": command}).encode("utf-8")
    req = urlrequest.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlrequest.urlopen(req, timeout=4) as resp:
            return 200 <= getattr(resp, "status", 500) < 300
    except (error.URLError, TimeoutError):
        return False


@app.post("/tv-command/<command>")
def tv_command(command: str):
    _send_tv_command(command)
    return redirect(url_for("control"))


@app.post("/start")
def start():
    START_FLAG.parent.mkdir(parents=True, exist_ok=True)
    START_FLAG.write_text("start\n", encoding="utf-8")
    return redirect(url_for("control"))


@app.post("/reset")
def reset():
    if START_FLAG.exists():
        START_FLAG.unlink()
    return redirect(url_for("control"))


@app.post("/capture-session")
def capture_session():
    CAPTURE_FLAG.parent.mkdir(parents=True, exist_ok=True)
    CAPTURE_FLAG.write_text("capture\n", encoding="utf-8")
    return redirect(url_for("control"))


@app.post("/type-text")
def type_text():
    text = (request.form.get("text") or "").strip()
    TEXT_INJECT.parent.mkdir(parents=True, exist_ok=True)
    TEXT_INJECT.write_text(text, encoding="utf-8")
    return redirect(url_for("control"))


@app.post("/reset-login-tabs")
def reset_login_tabs():
    RESET_LOGIN_TABS.parent.mkdir(parents=True, exist_ok=True)
    RESET_LOGIN_TABS.write_text("reset\n", encoding="utf-8")
    return redirect(url_for("control"))


@app.post("/clear-session")
def clear_session():
    if SESSION_BUNDLE.exists():
        SESSION_BUNDLE.unlink()
    return redirect(url_for("control"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8082, debug=False)
