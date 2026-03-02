# 📺 Web Page Carousel

A Docker-based webpage carousel for displays. Cycles through a list of URLs at a configurable interval and keeps the screen awake.

---

## 🚀 Quick Start

```bash
docker compose up -d
```

Open **http://localhost:8080** on your browser.

---

## ⚙️ Configuration

Edit `docker-compose.yml` to customise:

```yaml
environment:
  # Comma-separated URLs to cycle through
  PAGES: >-
    https://example.com,
    https://wikipedia.org,
    https://news.ycombinator.com

  # Seconds per page
  INTERVAL: 30
```

| Variable   | Default                | Description                              |
|------------|------------------------|------------------------------------------|
| `PAGES`    | `https://example.com`  | Comma-separated list of URLs to display  |
| `INTERVAL` | `30`                   | Seconds each page is displayed           |

---

## 🛡️ Keep-Awake

The app uses two strategies to prevent TV sleep:

1. **Screen Wake Lock API** — requests a browser wake lock when supported
2. **Canvas pixel flicker** — periodically mutates a 1×1 invisible canvas to signal activity



---

## 📦 Run Without Compose

```bash
docker run -d \
  -p 8080:80 \
  -e PAGES="https://example.com,https://wikipedia.org" \
  -e INTERVAL=45 \
  --restart unless-stopped \
  samuelstreets/web-page-carousel:latest
```
