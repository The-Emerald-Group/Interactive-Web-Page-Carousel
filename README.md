# Non-Proxy TV Carousel

This setup keeps TV display browser-native (similar to the original Web-Page-Carousel) and avoids proxy-rendering target systems.

## Services

- `chrome-kiosk`: containerized Chromium + noVNC, used only for interactive authentication.
- `page-rotator`: Selenium auth helper that opens login tabs and captures cookie/storage bundles.
- `rotator-control`: operator controls (`/control`) for capture/reset and status.
- `hybrid-carousel`: admin/auth/carousel web app on `:8084`.

## Quick Start

```bash
# edit docker-compose.yml first:
# - ADMIN_PASSWORD / SECRET_KEY
# URLs are managed in /admin (DISPLAY_URLS / LOGIN_URLS / PAGES can stay empty)
docker compose up -d --build
```

Open:
- `http://<host-ip>:8084/admin` to set login/display URLs and interval.
- `http://<host-ip>:8084/auth` from any machine to log in through embedded noVNC.
- `http://<host-ip>:8084/tv-auth` on the TV browser for local-login flow (same browser session as carousel).
- `http://<host-ip>:8084/tv` on the TV browser (single URL stream display).
- `http://<host-ip>:8084/tv-control` from any machine for helper controls.
- `http://<host-ip>:8084/carousel` (alias to `/tv-stream`).
- `http://<host-ip>:8083/control` for capture/reset/operator actions.

## GitHub-Friendly Deployment (Single File)

1. Push this folder to GitHub.
2. On the target Docker server:

```bash
git clone <your-repo-url>
cd Web-Page-Carousel-test-upload
```

3. Edit only `docker-compose.yml` and set at minimum:
   - `hybrid-carousel.environment.ADMIN_PASSWORD`
   - `hybrid-carousel.environment.SECRET_KEY`
   - Optional: fixed host ports (`8081`, `8083`, `8084`) if they conflict.
   - Manage login/display pages from `/admin` after startup.
4. Start:

```bash
docker compose up -d --build
```

## Operator Workflow

1. Configure URLs and approval flags in `/admin`.
2. Open `/auth`, click **Open Login Tabs**, and complete login for each system.
3. Click **Capture Session For Carousel**.
4. Open `/carousel` on the TV browser.
5. Put browser in fullscreen (button shown if fullscreen is not already active).

Hybrid stream mode (recommended):
1. Open `:8084/tv-control` from your PC and use TV stream controls.
2. Use **Show Login On Stream** and **Prev/Next Login** to move through login tabs and sign in.
3. From the same control page, click **Start Rotation** when ready.
4. Open `http://<host-ip>:8084/tv` on the TV; it displays the same browser session as a smooth view-only stream.

## API Contract

- `GET /api/public/state`
  - Returns:
    - `pages`: approved display URLs
    - `interval`: rotation interval in seconds
    - `version`: config version marker
    - `auth.ready`: whether captured auth bundle exists
    - `auth.captured_at`: unix timestamp of latest capture
    - `auth.origins`: captured origin list
    - `auth.entry_count`: number of captured origins
- `GET /api/admin/session-bundle` (admin session required)
  - Returns capture summary for each origin with cookie/storage counts.

## Display Behavior

- `/carousel` auto-starts rotation.
- Pages are loaded directly by URL (no proxy URL wrapping).
- If a page appears stuck, the client skips to the next page after a timeout.
- Config updates are polled automatically and applied without full restart.

## Notes and Limits

- Some sites block iframe embedding (`X-Frame-Options` / CSP `frame-ancestors`); those pages will not render in embedded mode.
- Cross-device browser auth sharing is site-dependent; if a site requires strict device-bound auth, log in directly on the TV browser as a fallback.
- Auth helper stability is improved with reconnect logic and an auth-ready marker file.
- Chromium profile is now persisted to `./chrome-data`, so login sessions can survive container restarts and host reboots (subject to your IdP/session policies).

## Auth Expiry Debug Logging

- `page-rotator` now emits auth diagnostics every `AUTH_DEBUG_PROBE_SECONDS` (default `20`).
- Logs include per-tab state (`login` vs `display`), current URL, expected display URL, and cookie health (`persistent/session` counts + earliest expiry).
- To stream those logs while reproducing logout/expiry:

```bash
docker compose logs -f --since=10m page-rotator
```
