import os
import time
import json
import re
from pathlib import Path
from urllib.parse import urlparse
from urllib import error as urlerror, request as urlrequest
from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options


def log(message: str) -> None:
    print(message, flush=True)


def parse_urls(raw: str) -> list[str]:
    return [u.strip() for u in (raw or "").split(",") if u.strip()]


def env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


def env_bool(name: str, default: bool) -> bool:
    raw = (os.getenv(name, "1" if default else "0") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def env_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


def env_nonnegative_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
        return value if value >= 0 else default
    except (TypeError, ValueError):
        return default


def build_driver(selenium_url: str, launch_app_url: str = "") -> webdriver.Remote:
    options = Options()
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-sandbox")
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--disable-gpu")
    options.add_argument("--start-maximized")
    options.add_argument("--kiosk")
    options.add_argument("--disable-infobars")
    options.add_argument("--user-data-dir=/opt/selenium/assets/chrome-data")
    options.add_argument("--window-size=1920,1080")
    if launch_app_url:
        options.add_argument(f"--app={launch_app_url}")
    driver = webdriver.Remote(command_executor=selenium_url, options=options)
    try:
        # Inject before any page script runs to block scripted refresh loops.
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": """
(() => {
  try {
    const stripRefreshMeta = () => {
      try {
        document.querySelectorAll('meta[http-equiv="refresh" i]').forEach((el) => el.remove());
      } catch (e) {}
    };
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", stripRefreshMeta, { once: true });
    } else {
      stripRefreshMeta();
    }

    const looksLikeRefresh = (src) => /location\\.(reload|href|assign|replace)|window\\.location|history\\.go\\(0\\)/i.test(String(src || ""));
    const originalSetTimeout = window.setTimeout.bind(window);
    const originalSetInterval = window.setInterval.bind(window);
    window.setTimeout = function (handler, timeout, ...args) {
      if (looksLikeRefresh(handler)) return 0;
      return originalSetTimeout(handler, timeout, ...args);
    };
    window.setInterval = function (handler, timeout, ...args) {
      if (looksLikeRefresh(handler)) return 0;
      return originalSetInterval(handler, timeout, ...args);
    };

    try {
      const originalAssign = window.location.assign.bind(window.location);
      const originalReplace = window.location.replace.bind(window.location);
      const currentHref = () => String(window.location.href || "");
      window.location.reload = function () { return undefined; };
      window.location.assign = function (url) {
        if (String(url || "") === currentHref()) return undefined;
        return originalAssign(url);
      };
      window.location.replace = function (url) {
        if (String(url || "") === currentHref()) return undefined;
        return originalReplace(url);
      };
    } catch (e) {}
  } catch (e) {}
})();
                """
            },
        )
    except Exception:
        pass
    try:
        driver.set_page_load_timeout(30)
    except Exception:
        pass
    return driver


def ensure_fullscreen(driver: webdriver.Remote) -> None:
    # Use deterministic Chrome window-state fullscreen first, then fallbacks.
    try:
        window_info = driver.execute_cdp_cmd("Browser.getWindowForTarget", {})
        window_id = window_info.get("windowId")
        if window_id is not None:
            driver.execute_cdp_cmd(
                "Browser.setWindowBounds",
                {"windowId": window_id, "bounds": {"windowState": "fullscreen"}},
            )
            return
    except Exception:
        pass
    try:
        driver.maximize_window()
    except Exception:
        pass
    try:
        driver.set_window_size(1920, 1080)
    except Exception:
        pass


def looks_fullscreen(driver: webdriver.Remote) -> bool:
    try:
        return bool(
            driver.execute_script(
                """
                try {
                  const h = Math.abs((window.outerHeight || 0) - (screen.height || 0));
                  const w = Math.abs((window.outerWidth || 0) - (screen.width || 0));
                  return h <= 4 && w <= 4;
                } catch (e) {
                  return false;
                }
                """
            )
        )
    except Exception:
        return False


def suppress_auto_refresh(driver: webdriver.Remote) -> None:
    """Disabled: script injection caused navigation-race instability."""
    return


def install_login_redirect_guard(driver: webdriver.Remote, display_urls: list[str]) -> None:
    """For managed domains, block/bounce unexpected /login navigations."""
    try:
        redirect_map = {}
        for url in display_urls:
            origin = origin_for(url)
            if origin and origin not in redirect_map:
                redirect_map[origin] = url
        if not redirect_map:
            return
        script = f"""
(() => {{
  const map = {json.dumps(redirect_map)};
  try {{
    const origin = String(window.location.origin || "");
    const href = String(window.location.href || "");
    const target = map[origin];
    if (!target) return;

    const isLoginUrl = (value) => /\\/login(\\b|\\/|\\?|#|$)/i.test(String(value || ""));
    const forceTarget = () => {{
      try {{
        if (String(window.location.href || "") !== target) {{
          window.location.replace(target);
        }}
      }} catch (e) {{}}
    }};

    // If we ever land on /login for a managed origin, bounce immediately.
    if (isLoginUrl(href) && href !== target) {{
      forceTarget();
      return;
    }}

    // Block scripted redirects to /login before they execute.
    try {{
      const originalAssign = window.location.assign.bind(window.location);
      const originalReplace = window.location.replace.bind(window.location);
      window.location.assign = function (url) {{
        if (isLoginUrl(url)) return undefined;
        return originalAssign(url);
      }};
      window.location.replace = function (url) {{
        if (isLoginUrl(url)) return undefined;
        return originalReplace(url);
      }};
    }} catch (e) {{}}
  }} catch (e) {{}}
}})();
"""
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": script})
    except Exception:
        pass


def origin_for(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def read_storage(driver: webdriver.Remote, storage_name: str) -> dict[str, str]:
    try:
        return driver.execute_script(
            """
            const s = window[arguments[0]];
            const out = {};
            if (!s) return out;
            for (let i = 0; i < s.length; i++) {
              const k = s.key(i);
              out[k] = s.getItem(k);
            }
            return out;
            """,
            storage_name,
        ) or {}
    except Exception:
        return {}


def write_storage(driver: webdriver.Remote, storage_name: str, values: dict[str, str]) -> None:
    driver.execute_script(
        """
        const s = window[arguments[0]];
        const values = arguments[1] || {};
        if (!s) return;
        for (const [k, v] of Object.entries(values)) {
          s.setItem(k, String(v));
        }
        """,
        storage_name,
        values or {},
    )


def capture_session_bundle(driver: webdriver.Remote, bundle_path: Path, target_urls: list[str] | None = None) -> None:
    entries = []
    seen = set()
    by_origin = {}
    handles = list(driver.window_handles)
    current_handle = driver.current_window_handle
    for handle in handles:
        try:
            driver.switch_to.window(handle)
            url = driver.current_url or ""
            origin = origin_for(url)
            if not origin or origin in seen:
                continue
            seen.add(origin)
            cookies = driver.get_cookies() or []
            local_storage = read_storage(driver, "localStorage")
            session_storage = read_storage(driver, "sessionStorage")
            entries.append(
                {
                    "origin": origin,
                    "url": url,
                    "cookies": cookies,
                    "local_storage": local_storage,
                    "session_storage": session_storage,
                }
            )
            by_origin[origin] = entries[-1]
        except Exception as exc:
            log(f"[rotator] capture skipped one tab: {exc}")

    # Ensure we export configured domains even if their tab was closed/not focused.
    for target_url in (target_urls or []):
        origin = origin_for(target_url)
        if not origin or origin in seen:
            continue
        try:
            driver.switch_to.window(current_handle)
            driver.get(origin)
            cookies = driver.get_cookies() or []
            local_storage = read_storage(driver, "localStorage")
            session_storage = read_storage(driver, "sessionStorage")
            entry = {
                "origin": origin,
                "url": driver.current_url or origin,
                "cookies": cookies,
                "local_storage": local_storage,
                "session_storage": session_storage,
            }
            entries.append(entry)
            by_origin[origin] = entry
            seen.add(origin)
        except Exception as exc:
            log(f"[rotator] capture skipped target origin {origin}: {exc}")

    try:
        driver.switch_to.window(current_handle)
    except Exception:
        pass

    payload = {"captured_at": int(time.time()), "entries": entries}
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    bundle_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    log(f"[rotator] session bundle saved to {bundle_path} ({len(entries)} origin(s))")


def load_session_bundle(bundle_path: Path) -> dict[str, dict]:
    if not bundle_path.exists():
        return {}
    try:
        payload = json.loads(bundle_path.read_text(encoding="utf-8"))
        entries = payload.get("entries") or []
        by_origin = {}
        for entry in entries:
            origin = (entry.get("origin") or "").strip()
            if origin:
                by_origin[origin] = entry
        if by_origin:
            log(f"[rotator] loaded session bundle with {len(by_origin)} origin(s)")
        return by_origin
    except Exception as exc:
        log(f"[rotator] could not read session bundle: {exc}")
        return {}


def apply_session_for_url(driver: webdriver.Remote, target_url: str, bundle_by_origin: dict[str, dict]) -> None:
    # Prefer the persistent Chromium profile as source of truth.
    # Storage/cookie replay is intentionally skipped to avoid navigation-race crashes.
    try:
        driver.get(target_url)
    except Exception as exc:
        log(f"[rotator] direct open failed for {target_url}: {exc}")


def choose_startup_url(login_url: str, display_url: str, bundle_by_origin: dict[str, dict]) -> str:
    """Prefer display URL when we already have session data for its origin."""
    display_origin = origin_for(display_url)
    entry = bundle_by_origin.get(display_origin) if display_origin else None
    if entry and ((entry.get("cookies") or []) or (entry.get("local_storage") or {}) or (entry.get("session_storage") or {})):
        return display_url
    return login_url


def maybe_capture(
    driver: webdriver.Remote,
    capture_flag_path: Path,
    bundle_path: Path,
    target_urls: list[str] | None = None,
) -> None:
    if not capture_flag_path.exists():
        return
    try:
        capture_session_bundle(driver, bundle_path, target_urls=target_urls)
    finally:
        try:
            capture_flag_path.unlink()
        except Exception:
            pass
    try:
        driver.fullscreen_window()
    except Exception:
        pass


def maybe_inject_text(driver: webdriver.Remote, text_flag_path: Path) -> None:
    if not text_flag_path.exists():
        return
    try:
        text = text_flag_path.read_text(encoding="utf-8")
    except Exception:
        text = ""
    try:
        driver.execute_script(
            """
            const text = arguments[0] || "";
            const el = document.activeElement;
            if (!el) return false;
            const tag = (el.tagName || "").toLowerCase();
            if (tag === "input" || tag === "textarea") {
              el.value = text;
              el.dispatchEvent(new Event('input', { bubbles: true }));
              el.dispatchEvent(new Event('change', { bubbles: true }));
              return true;
            }
            if (el.isContentEditable) {
              el.textContent = text;
              el.dispatchEvent(new Event('input', { bubbles: true }));
              return true;
            }
            return false;
            """,
            text,
        )
        log("[rotator] injected text into active field")
    except Exception as exc:
        log(f"[rotator] text inject failed: {exc}")
    finally:
        try:
            text_flag_path.unlink()
        except Exception:
            pass


def maybe_reset_login_tabs(
    driver: webdriver.Remote,
    reset_flag_path: Path,
    managed_urls: list[str],
    bundle_by_origin: dict[str, dict],
) -> None:
    if not reset_flag_path.exists():
        return
    try:
        # Close all extra tabs and rebuild expected login tabs.
        handles = list(driver.window_handles)
        keep = handles[0] if handles else None
        for h in handles[1:]:
            try:
                driver.switch_to.window(h)
                driver.close()
            except Exception:
                pass
        if keep:
            driver.switch_to.window(keep)
        if not managed_urls:
            return
        apply_session_for_url(driver, managed_urls[0], bundle_by_origin)
        for managed_url in managed_urls[1:]:
            driver.switch_to.new_window("tab")
            apply_session_for_url(driver, managed_url, bundle_by_origin)
        driver.switch_to.window(driver.window_handles[0])
        log("[rotator] reset browser to managed tabs")
    finally:
        try:
            reset_flag_path.unlink()
        except Exception:
            pass


def fetch_runtime(runtime_base: str) -> dict:
    if not runtime_base:
        return {}
    runtime_url = runtime_base.rstrip("/") + "/api/tv-runtime"
    try:
        with urlrequest.urlopen(runtime_url, timeout=2) as resp:
            data = resp.read().decode("utf-8", errors="replace")
        return json.loads(data)
    except (urlerror.URLError, TimeoutError, json.JSONDecodeError):
        return {}


def fetch_tv_auth_config(runtime_base: str) -> dict:
    if not runtime_base:
        return {}
    cfg_url = runtime_base.rstrip("/") + "/api/public/tv-auth-config"
    try:
        with urlrequest.urlopen(cfg_url, timeout=2) as resp:
            data = resp.read().decode("utf-8", errors="replace")
        payload = json.loads(data)
        rows = payload.get("pages") or []
        display_urls = []
        login_urls = []
        for row in rows:
            display_url = str((row or {}).get("display_url") or "").strip()
            login_url = str((row or {}).get("login_url") or display_url).strip()
            if not display_url:
                continue
            display_urls.append(display_url)
            login_urls.append(login_url or display_url)
        return {
            "display_urls": display_urls,
            "login_urls": login_urls or display_urls,
            "interval": int(payload.get("interval") or 0),
        }
    except (urlerror.URLError, TimeoutError, json.JSONDecodeError, ValueError, TypeError):
        return {}


def clear_stale_selenium_sessions(selenium_url: str) -> None:
    """Best effort: free occupied Selenium slots before creating our session."""
    if not selenium_url:
        return
    base = selenium_url.replace("/wd/hub", "").rstrip("/")
    status_url = base + "/status"
    try:
        with urlrequest.urlopen(status_url, timeout=2) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return
    try:
        nodes = ((payload or {}).get("value") or {}).get("nodes") or []
    except Exception:
        nodes = []
    session_ids: list[str] = []
    for node in nodes:
        for slot in (node.get("slots") or []):
            sess = slot.get("session") or {}
            sid = (sess.get("sessionId") or "").strip()
            if sid:
                session_ids.append(sid)
    for sid in session_ids:
        try:
            req = urlrequest.Request(base + f"/session/{sid}", method="DELETE")
            with urlrequest.urlopen(req, timeout=2):
                pass
            log(f"[rotator] cleared stale Selenium session: {sid}")
        except Exception:
            continue


def reset_to_single_tab(driver: webdriver.Remote) -> None:
    handles = list(driver.window_handles)
    if not handles:
        return
    keep = handles[0]
    for handle in handles[1:]:
        try:
            driver.switch_to.window(handle)
            driver.close()
        except Exception:
            continue
    try:
        driver.switch_to.window(keep)
    except Exception:
        pass


def normalize_url(value: str) -> str:
    return (value or "").strip().rstrip("/")


def url_is_login(value: str) -> bool:
    return bool(re.search(r"/login(\b|/|\?|#|$)", value or "", re.IGNORECASE))


def summarize_cookie_health(cookies: list[dict], now_ts: float) -> str:
    if not cookies:
        return "cookies=0"
    persistent_expiries = []
    session_count = 0
    for cookie in cookies:
        expiry = cookie.get("expiry")
        if isinstance(expiry, (int, float)):
            persistent_expiries.append(float(expiry))
        else:
            session_count += 1
    persistent_count = len(persistent_expiries)
    if persistent_expiries:
        earliest_exp = min(persistent_expiries)
        mins_left = int((earliest_exp - now_ts) / 60)
        if mins_left < 0:
            expiry_note = f"earliest_expired={abs(mins_left)}m_ago"
        else:
            expiry_note = f"earliest_expiry_in={mins_left}m"
    else:
        expiry_note = "no_persistent_expiry"
    return (
        f"cookies={len(cookies)} "
        f"persistent={persistent_count} "
        f"session={session_count} "
        f"{expiry_note}"
    )


def probe_auth_state(driver: webdriver.Remote, display_urls: list[str], display_handles: list[str]) -> None:
    expected = [normalize_url(u) for u in display_urls if u]
    now_ts = time.time()
    handles = list(display_handles) if display_handles else list(driver.window_handles)
    for idx, handle in enumerate(handles):
        if handle not in driver.window_handles:
            continue
        try:
            driver.switch_to.window(handle)
            current_url = (driver.current_url or "").strip()
            current_norm = normalize_url(current_url)
            expected_url = expected[idx] if idx < len(expected) else ""
            expected_norm = normalize_url(expected_url)
            cookies = driver.get_cookies() or []
            cookie_health = summarize_cookie_health(cookies, now_ts)
            auth_label = "login" if url_is_login(current_url) else "display"
            if expected_norm and current_norm and current_norm != expected_norm:
                log(
                    f"[rotator][auth] tab={idx} state={auth_label} "
                    f"url={current_url} expected={expected_url} {cookie_health}"
                )
            else:
                log(
                    f"[rotator][auth] tab={idx} state={auth_label} "
                    f"url={current_url} {cookie_health}"
                )
        except Exception as exc:
            log(f"[rotator][auth] tab={idx} probe_error={exc}")


def probe_auth_state_active_only(
    driver: webdriver.Remote,
    display_urls: list[str],
    display_handles: list[str],
) -> None:
    """Non-disruptive auth probe that only inspects the active tab."""
    expected = [normalize_url(u) for u in display_urls if u]
    now_ts = time.time()
    try:
        active_handle = driver.current_window_handle
        current_handles = list(driver.window_handles)
        if active_handle not in current_handles:
            return
        idx = current_handles.index(active_handle)
        current_url = (driver.current_url or "").strip()
        current_norm = normalize_url(current_url)
        expected_url = expected[idx] if idx < len(expected) else ""
        expected_norm = normalize_url(expected_url)
        cookies = driver.get_cookies() or []
        cookie_health = summarize_cookie_health(cookies, now_ts)
        auth_label = "login" if url_is_login(current_url) else "display"
        if expected_norm and current_norm and current_norm != expected_norm:
            log(
                f"[rotator][auth] tab={idx} state={auth_label} "
                f"url={current_url} expected={expected_url} {cookie_health}"
            )
        else:
            log(f"[rotator][auth] tab={idx} state={auth_label} url={current_url} {cookie_health}")
    except Exception as exc:
        log(f"[rotator][auth] active probe_error={exc}")


def ensure_expected_display_url(driver: webdriver.Remote, expected_url: str, tab_index: int) -> str:
    expected_norm = normalize_url(expected_url)
    try:
        current_url = (driver.current_url or "").strip()
    except Exception:
        current_url = ""
    current_norm = normalize_url(current_url)
    if expected_norm and (not current_norm or current_norm != expected_norm):
        reason = "login drift" if url_is_login(current_url) else "url drift"
        try:
            driver.get(expected_url)
            current_url = (driver.current_url or "").strip()
            log(
                f"[rotator] corrected tab {tab_index} from {reason}: "
                f"expected={expected_url} now={current_url}"
            )
            return current_url
        except Exception as exc:
            log(f"[rotator] could not correct tab {tab_index} to expected URL: {exc}")
    return current_url


def reconcile_display_handles(driver: webdriver.Remote, expected_count: int) -> list[str]:
    """Best effort: map managed tabs back to the first N open handles."""
    try:
        handles = list(driver.window_handles)
    except Exception:
        return []
    if expected_count <= 0 or not handles:
        return []
    return handles[:expected_count]


def rebuild_managed_tabs(
    driver: webdriver.Remote, managed_urls: list[str], bundle_by_origin: dict[str, dict]
) -> list[str]:
    """Recreate managed tabs when Chrome unexpectedly closes one."""
    handles: list[str] = []
    reset_to_single_tab(driver)
    if not managed_urls:
        return handles
    for idx, url in enumerate(managed_urls):
        if idx == 0:
            apply_session_for_url(driver, url, bundle_by_origin)
        else:
            driver.switch_to.new_window("tab")
            apply_session_for_url(driver, url, bundle_by_origin)
        suppress_auto_refresh(driver)
        handles.append(driver.current_window_handle)
    if handles:
        driver.switch_to.window(handles[0])
    return handles


def ensure_tv_agent_tab(
    driver: webdriver.Remote,
    tv_agent_url: str,
    known_handle: str | None,
) -> str | None:
    if not tv_agent_url:
        return known_handle

    handles = list(driver.window_handles)
    if not handles:
        return known_handle

    if known_handle and known_handle in handles:
        return known_handle

    current_handle = None
    try:
        current_handle = driver.current_window_handle
    except Exception:
        current_handle = None

    found_handle = None
    for h in handles:
        try:
            driver.switch_to.window(h)
            current = (driver.current_url or "").strip()
            if current.startswith(tv_agent_url):
                found_handle = h
                break
        except Exception:
            continue

    if current_handle and current_handle in handles:
        try:
            driver.switch_to.window(current_handle)
        except Exception:
            pass

    if found_handle:
        return found_handle

    try:
        if current_handle and current_handle in handles:
            driver.switch_to.window(current_handle)
            driver.switch_to.new_window("tab")
            driver.get(tv_agent_url)
            new_handle = driver.current_window_handle
            try:
                driver.switch_to.window(current_handle)
            except Exception:
                pass
            return new_handle
        driver.switch_to.window(handles[0])
        driver.get(tv_agent_url)
        return driver.current_window_handle
    except Exception as exc:
        log(f"[rotator] could not restore tv-agent tab: {exc}")
        return known_handle


def main() -> None:
    log("[rotator] build: tab-stability-v2")
    display_urls = parse_urls(os.getenv("DISPLAY_URLS", ""))
    login_urls = parse_urls(os.getenv("LOGIN_URLS", "")) or display_urls
    interval_seconds = env_int("INTERVAL_SECONDS", 30)
    selenium_url = (os.getenv("SELENIUM_URL", "http://chrome-kiosk:4444/wd/hub") or "").strip()
    start_flag_path = Path(os.getenv("START_FLAG_PATH", "/app/control/start_rotation.flag"))
    capture_flag_path = Path(os.getenv("CAPTURE_FLAG_PATH", "/app/control/capture_session.flag"))
    session_bundle_path = Path(os.getenv("SESSION_BUNDLE_PATH", "/app/control/session_bundle.json"))
    text_inject_path = Path(os.getenv("TEXT_INJECT_PATH", "/app/control/inject_text.txt"))
    reset_tabs_path = Path(os.getenv("RESET_LOGIN_TABS_PATH", "/app/control/reset_login_tabs.flag"))
    ready_flag_path = Path(os.getenv("READY_FLAG_PATH", "/app/control/auth_ready.flag"))
    tv_agent_url = (os.getenv("TV_AGENT_URL", "") or "").strip()
    runtime_api_base = (os.getenv("RUNTIME_API_BASE", "http://hybrid-carousel") or "").strip()
    auto_capture_seconds = env_float("AUTO_CAPTURE_SECONDS", 0.0)
    tab_keepalive_seconds = env_nonnegative_float("TAB_KEEPALIVE_REFRESH_SECONDS", 1200.0)
    enable_rotation = env_bool("ENABLE_SELENIUM_ROTATION", False)
    force_fullscreen = env_bool("FORCE_SELENIUM_FULLSCREEN", False)
    open_all_login_tabs_on_start = env_bool("OPEN_ALL_LOGIN_TABS_ON_START", False)
    auth_probe_seconds = env_nonnegative_float("AUTH_DEBUG_PROBE_SECONDS", 90.0)
    auth_probe_all_tabs = env_bool("AUTH_DEBUG_PROBE_ALL_TABS", False)

    # Prefer app-configured URLs over static env URLs so rotator tabs match admin state.
    runtime_cfg = fetch_tv_auth_config(runtime_api_base)
    if runtime_cfg.get("display_urls"):
        display_urls = runtime_cfg.get("display_urls") or display_urls
        login_urls = runtime_cfg.get("login_urls") or display_urls
        runtime_interval = int(runtime_cfg.get("interval") or 0)
        if runtime_interval > 0:
            interval_seconds = runtime_interval
        log(f"[rotator] using runtime config urls ({len(display_urls)} page(s))")
    elif not display_urls:
        log("[rotator] no URLs configured yet; waiting for admin-configured approved pages")
    config_signature = json.dumps(
        {"display_urls": display_urls, "login_urls": login_urls, "interval": interval_seconds},
        sort_keys=True,
    )

    while True:
        log(f"[rotator] connecting to Selenium at {selenium_url}")
        clear_stale_selenium_sessions(selenium_url)
        driver = None
        launch_app_url = ""
        while driver is None:
            try:
                driver = build_driver(selenium_url, launch_app_url=launch_app_url)
                install_login_redirect_guard(driver, display_urls)
            except Exception as exc:
                log(f"[rotator] Selenium not ready yet: {exc}. Retrying in 3s...")
                time.sleep(3)

        try:
            display_handles: list[str] = []
            last_runtime_nonce = -1
            last_fullscreen_at = 0.0
            if force_fullscreen:
                ensure_fullscreen(driver)
                last_fullscreen_at = time.time()
            reset_to_single_tab(driver)
            start_flag_path.parent.mkdir(parents=True, exist_ok=True)
            if start_flag_path.exists():
                start_flag_path.unlink()
            if capture_flag_path.exists():
                capture_flag_path.unlink()
            if ready_flag_path.exists():
                ready_flag_path.unlink()
            session_bundle = load_session_bundle(session_bundle_path)

            if not enable_rotation:
                if not display_urls:
                    log("[rotator] waiting for approved URLs from admin config...")
                    while not display_urls:
                        live_cfg = fetch_tv_auth_config(runtime_api_base)
                        if live_cfg.get("display_urls"):
                            display_urls = live_cfg.get("display_urls") or []
                            login_urls = live_cfg.get("login_urls") or display_urls
                            runtime_interval = int(live_cfg.get("interval") or 0)
                            if runtime_interval > 0:
                                interval_seconds = runtime_interval
                            config_signature = json.dumps(
                                {"display_urls": display_urls, "login_urls": login_urls, "interval": interval_seconds},
                                sort_keys=True,
                            )
                            install_login_redirect_guard(driver, display_urls)
                            log(f"[rotator] picked up admin runtime config ({len(display_urls)} page(s))")
                            break
                        time.sleep(2)
                for index, login_url in enumerate(login_urls):
                    display_url = display_urls[index] if index < len(display_urls) else login_url
                    startup_url = choose_startup_url(login_url, display_url, session_bundle)
                    if index == 0:
                        apply_session_for_url(driver, startup_url, session_bundle)
                        suppress_auto_refresh(driver)
                    else:
                        driver.switch_to.new_window("tab")
                        apply_session_for_url(driver, startup_url, session_bundle)
                        suppress_auto_refresh(driver)
                    display_handles.append(driver.current_window_handle)
                    log(f"[rotator] prepared managed tab {index + 1}/{len(login_urls)}: {startup_url}")
            else:
                startup_login_urls = login_urls
                if not enable_rotation and not open_all_login_tabs_on_start and login_urls:
                    startup_login_urls = [login_urls[0]]

                for index, login_url in enumerate(startup_login_urls):
                    if index == 0:
                        apply_session_for_url(driver, login_url, session_bundle)
                    else:
                        driver.switch_to.new_window("tab")
                        apply_session_for_url(driver, login_url, session_bundle)
                    log(f"[rotator] opened login tab {index + 1}/{len(startup_login_urls)}: {login_url}")

            driver.switch_to.window(driver.window_handles[0])
            ready_flag_path.write_text(str(int(time.time())), encoding="utf-8")
            log("[rotator] auth browser ready")
            if not enable_rotation:
                log("[rotator] auth-only mode enabled; waiting for capture requests")
                last_auto_capture = 0.0
                last_keepalive = 0.0
                last_applied_display_idx = -1
                runtime_fullscreen_lock = True
                last_config_poll_at = 0.0
                last_tab_keepalive_at = 0.0
                last_auth_probe_at = 0.0
                while True:
                    now = time.time()
                    if (now - last_config_poll_at) >= 5:
                        live_cfg = fetch_tv_auth_config(runtime_api_base)
                        last_config_poll_at = now
                        if live_cfg.get("display_urls"):
                            new_display_urls = live_cfg.get("display_urls") or []
                            new_login_urls = live_cfg.get("login_urls") or new_display_urls
                            new_interval = int(live_cfg.get("interval") or interval_seconds)
                            new_signature = json.dumps(
                                {
                                    "display_urls": new_display_urls,
                                    "login_urls": new_login_urls,
                                    "interval": new_interval,
                                },
                                sort_keys=True,
                            )
                            if new_signature != config_signature:
                                display_urls = new_display_urls
                                login_urls = new_login_urls
                                interval_seconds = new_interval if new_interval > 0 else interval_seconds
                                config_signature = new_signature
                                install_login_redirect_guard(driver, display_urls)
                                rebuilt = rebuild_managed_tabs(driver, display_urls, session_bundle)
                                if rebuilt:
                                    display_handles = rebuilt
                                last_applied_display_idx = -1
                                last_runtime_nonce = -1
                                log(f"[rotator] applied updated runtime config ({len(display_urls)} page(s))")

                    runtime = fetch_runtime(runtime_api_base)
                    runtime_nonce = int(runtime.get("command_nonce") or 0) if runtime else 0
                    runtime_mode = (runtime.get("mode") or "login") if runtime else "login"
                    runtime_login_idx = int(runtime.get("login_index") or 0) if runtime else 0
                    runtime_display_idx = int(runtime.get("display_index") or 0) if runtime else 0
                    if runtime and (runtime.get("active_display_index") is not None):
                        runtime_active_display_idx = int(runtime.get("active_display_index"))
                    else:
                        runtime_active_display_idx = runtime_display_idx
                    runtime_rotation_enabled = bool(runtime.get("rotation_enabled")) if runtime else False
                    runtime_fullscreen_lock = bool(runtime.get("fullscreen_lock", True)) if runtime else True

                    # If Chrome dropped a tab, rebuild managed tabs to restore deterministic switching.
                    try:
                        current_handles = list(driver.window_handles)
                    except Exception:
                        current_handles = []
                    if len(current_handles) < len(display_urls):
                        rebuilt = rebuild_managed_tabs(driver, display_urls, session_bundle)
                        if rebuilt:
                            display_handles = rebuilt
                            log("[rotator] rebuilt managed tabs after tab loss")

                    if display_handles:
                        if runtime_nonce != last_runtime_nonce:
                            try:
                                if runtime_mode == "display":
                                    target_idx = runtime_display_idx % len(display_handles)
                                    target_handle = display_handles[target_idx]
                                    if target_handle not in driver.window_handles:
                                        recovered = reconcile_display_handles(driver, len(display_handles))
                                        if recovered:
                                            display_handles = recovered
                                            target_handle = display_handles[target_idx]
                                            log("[rotator] recovered stale display handles")
                                    if target_handle in driver.window_handles:
                                        driver.switch_to.window(target_handle)
                                        suppress_auto_refresh(driver)
                                        expected_url = display_urls[target_idx] if target_idx < len(display_urls) else ""
                                        current_after_switch = ensure_expected_display_url(driver, expected_url, target_idx)
                                        if runtime_fullscreen_lock or force_fullscreen:
                                            ensure_fullscreen(driver)
                                        log(
                                            f"[rotator] applied display command: index={runtime_display_idx}, url={current_after_switch}"
                                        )
                                        last_applied_display_idx = target_idx
                                else:
                                    target_idx = runtime_login_idx % len(display_handles)
                                    target_handle = display_handles[target_idx]
                                    if target_handle in driver.window_handles:
                                        driver.switch_to.window(target_handle)
                                        suppress_auto_refresh(driver)
                                        if runtime_fullscreen_lock or force_fullscreen:
                                            ensure_fullscreen(driver)
                                        log(f"[rotator] applied login command: index={runtime_login_idx}")
                                last_runtime_nonce = runtime_nonce
                            except Exception as exc:
                                log(f"[rotator] transient command apply issue: {exc}")

                        # Keep following server-side rotation index while in display+rotation mode.
                        if runtime_mode == "display" and runtime_rotation_enabled:
                            try:
                                target_idx = runtime_active_display_idx % len(display_handles)
                                if target_idx != last_applied_display_idx:
                                    target_handle = display_handles[target_idx]
                                    if target_handle in driver.window_handles:
                                        driver.switch_to.window(target_handle)
                                        suppress_auto_refresh(driver)
                                        expected_url = display_urls[target_idx] if target_idx < len(display_urls) else ""
                                        current_after_switch = ensure_expected_display_url(driver, expected_url, target_idx)
                                        if runtime_fullscreen_lock or force_fullscreen:
                                            ensure_fullscreen(driver)
                                        log(
                                            f"[rotator] applied rotation step: index={target_idx}, url={current_after_switch}"
                                        )
                                        last_applied_display_idx = target_idx
                            except Exception as exc:
                                log(f"[rotator] transient rotation step issue: {exc}")

                        # Periodically touch each managed page to reduce idle/session expiry risk.
                        if tab_keepalive_seconds > 0 and (now - last_tab_keepalive_at) >= tab_keepalive_seconds:
                            try:
                                active_handle = driver.current_window_handle
                            except Exception:
                                active_handle = None
                            for idx, target_handle in enumerate(display_handles):
                                target_url = display_urls[idx] if idx < len(display_urls) else ""
                                if not target_url or target_handle not in driver.window_handles:
                                    continue
                                try:
                                    driver.switch_to.window(target_handle)
                                    current_url = (driver.current_url or "").strip()
                                    expected_norm = normalize_url(target_url)
                                    current_norm = normalize_url(current_url)
                                    should_reopen = False
                                    reason = ""
                                    if url_is_login(current_url):
                                        should_reopen = True
                                        reason = "login page"
                                    elif expected_norm and current_norm and current_norm != expected_norm:
                                        should_reopen = True
                                        reason = "url drift"

                                    if should_reopen:
                                        log(
                                            f"[rotator] keepalive correcting tab {idx} ({reason}); "
                                            f"reopening expected display URL"
                                        )
                                        driver.get(target_url)
                                except Exception as exc:
                                    log(f"[rotator] keepalive refresh issue on tab {idx}: {exc}")
                            if active_handle and active_handle in driver.window_handles:
                                try:
                                    driver.switch_to.window(active_handle)
                                except Exception:
                                    pass
                            last_tab_keepalive_at = now

                    if (runtime_fullscreen_lock or force_fullscreen) and (time.time() - last_fullscreen_at) >= 3:
                        if not looks_fullscreen(driver):
                            ensure_fullscreen(driver)
                        last_fullscreen_at = time.time()
                    if auth_probe_seconds > 0 and (now - last_auth_probe_at) >= auth_probe_seconds:
                        if auth_probe_all_tabs:
                            try:
                                active_before_probe = driver.current_window_handle
                            except Exception:
                                active_before_probe = None
                            probe_auth_state(driver, display_urls, display_handles)
                            if active_before_probe and active_before_probe in driver.window_handles:
                                try:
                                    driver.switch_to.window(active_before_probe)
                                except Exception:
                                    pass
                        else:
                            probe_auth_state_active_only(driver, display_urls, display_handles)
                        last_auth_probe_at = now
                    suppress_auto_refresh(driver)
                    maybe_capture(driver, capture_flag_path, session_bundle_path, target_urls=(login_urls + display_urls))
                    maybe_inject_text(driver, text_inject_path)
                    maybe_reset_login_tabs(driver, reset_tabs_path, display_urls, session_bundle)
                    if now - last_keepalive >= 30:
                        try:
                            _ = driver.current_url
                        except Exception as exc:
                            log(f"[rotator] transient keepalive issue: {exc}")
                        last_keepalive = now
                    if auto_capture_seconds > 0 and (now - last_auto_capture >= auto_capture_seconds):
                        capture_session_bundle(driver, session_bundle_path, target_urls=(login_urls + display_urls))
                        last_auto_capture = now
                    time.sleep(1)

            log("[rotator] waiting for start flag (set via control page)")
            while not start_flag_path.exists():
                maybe_capture(driver, capture_flag_path, session_bundle_path, target_urls=(login_urls + display_urls))
                maybe_inject_text(driver, text_inject_path)
                maybe_reset_login_tabs(driver, reset_tabs_path, display_urls, session_bundle)
                time.sleep(1)

            idx = 0
            while True:
                target = display_urls[idx]
                log(f"[rotator] showing ({idx + 1}/{len(display_urls)}): {target}")
                driver.switch_to.window(driver.window_handles[0])
                driver.get(target)
                if force_fullscreen:
                    ensure_fullscreen(driver)
                maybe_capture(driver, capture_flag_path, session_bundle_path, target_urls=(login_urls + display_urls))
                maybe_inject_text(driver, text_inject_path)
                maybe_reset_login_tabs(driver, reset_tabs_path, display_urls, session_bundle)
                time.sleep(interval_seconds)
                idx = (idx + 1) % len(display_urls)
        except Exception as exc:
            log(f"[rotator] session dropped, restarting browser session: {exc}")
            try:
                if ready_flag_path.exists():
                    ready_flag_path.unlink()
            except Exception:
                pass
            time.sleep(2)
        finally:
            try:
                driver.quit()
            except Exception:
                pass


if __name__ == "__main__":
    main()
