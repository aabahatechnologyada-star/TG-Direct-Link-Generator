# TG Direct Link Generator - Deployment & Setup Guide

This guide details everything required to deploy, configure, and maintain your TG Direct Link Generator bot across cloud hosting providers (Render, Railway, Heroku, VPS).

---

## 🚀 Quick Start & Fixes Overview

### Runtime Event Loop Fix
On modern Python runtimes (Python 3.10+, 3.12, 3.14), Pyrogram requires an active asyncio event loop during module import (`pyrogram.sync`). The entry points `main/__main__.py` and `main/bot/__init__.py` have been updated to initialize and register an asyncio event loop prior to importing `pyrogram`, resolving errors such as:
`RuntimeError: There is no current event loop in thread 'MainThread'.`

---

## ⚙️ Environment Variables Reference

When deploying to Render, Heroku, Railway, or VPS, set the following environment variables:

### Mandatory Variables

| Variable | Type | Description |
|---|---|---|
| `API_ID` | Integer | Telegram API ID from [my.telegram.org](https://my.telegram.org) |
| `API_HASH` | String | Telegram API Hash from [my.telegram.org](https://my.telegram.org) |
| `BOT_TOKEN` | String | Bot token obtained from Telegram [@BotFather](https://t.me/BotFather) |
| `BIN_CHANNEL` | Integer | ID of your storage channel (e.g., `-1001234567890`). Add your bot as an admin here! |
| `OWNER_ID` | Integer | Your Telegram numerical user ID (get from [@userinfobot](https://t.me/userinfobot)) |

### Optional & Tuning Variables

| Variable | Type | Default | Description |
|---|---|---|---|
| `PORT` | Integer | `8080` | Web server port (Render/Heroku assign `PORT` automatically) |
| `WEB_SERVER_BIND_ADDRESS` | String | `0.0.0.0` | Bind address for web server |
| `FQDN` | String | `WEB_SERVER_BIND_ADDRESS` | Domain name or public server IP (e.g., `myapp.onrender.com`) |
| `HAS_SSL` | Boolean | `False` | Set `True` if using SSL/HTTPS |
| `NO_PORT` | Boolean | `False` | Set `True` to hide port in generated streaming URLs (if port 80/443 used) |
| `WORKERS` | Integer | `6` | Number of concurrent Pyrogram update workers |
| `SLEEP_THRESHOLD` | Integer | `60` | FloodWait sleep ceiling in seconds |
| `PARALLEL_CHUNKS` | Integer | `4` | Number of in-flight parallel MTProto chunk prefetches |
| `PREWARM_DCS` | Boolean/Int | `1` | Pre-warm Telegram DC media connections at startup |
| `PING_INTERVAL` | Integer | `1200` | Keep-alive ping interval in seconds |

### Multi-Client Fan-out Variables (Optional)

To scale bandwidth across multiple bots/sessions:
- `MULTI_TOKEN1`: Additional bot token or session string
- `MULTI_TOKEN2`: Additional bot token or session string
- `MULTI_TOKEN3` ... `MULTI_TOKENn`

*Note: All multi-client bots must be added as admins in `BIN_CHANNEL`.*

### Auto-Ingestion & TMDb Integration (Optional)

| Variable | Type | Default | Description |
|---|---|---|---|
| `INGEST_CHANNEL` | Integer | `0` | Channel ID to monitor for incoming media (`0` disables ingestion) |
| `TMDB_API_KEY` | String | `""` | TMDb v3 API key for movie/series metadata lookup |
| `MONGO_URI` | String | `""` | MongoDB connection string |
| `MONGO_DB` | String | `"riyo"` | Database name |
| `INGEST_MIN_BYTES` | Integer | `10485760` (10MB) | Minimum file size in bytes to trigger ingestion |

---

## 🛠 Deploying on Render

1. **New Web Service**:
   - Create a new **Web Service** on [Render](https://render.com).
   - Connect your GitHub repository.

2. **Build & Start Commands**:
   - **Environment**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python3 -m main`

3. **Environment Variables**:
   - Go to **Environment** tab in Render dashboard.
   - Add all required variables (`API_ID`, `API_HASH`, `BOT_TOKEN`, `BIN_CHANNEL`, `OWNER_ID`).
   - Add `FQDN` set to your Render service URL (e.g. `your-app.onrender.com`).
   - Set `HAS_SSL` to `True`.

4. **Health Check Endpoint**:
   - Set health check path to `/health`.

---

## 🚂 Deploying on Railway

1. Create a new project on [Railway](https://railway.app).
2. Connect your GitHub repository.
3. Railway automatically detects `Railway.json` or `Procfile`.
4. Add all environment variables in Railway's **Variables** tab.

---

## 🐳 Deploying via Docker / VPS

Run directly on a Linux VPS using systemd or screen:

```bash
git clone <your-repo-url>
cd TG-Direct-Link-Generator
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Create .env file
cat << 'EOF' > .env
API_ID=123456
API_HASH=your_api_hash
BOT_TOKEN=123456789:ABCdef...
BIN_CHANNEL=-1001234567890
OWNER_ID=987654321
PORT=8080
EOF

python3 -m main
```

---

## 🔍 Troubleshooting Checklist

1. **`RuntimeError: There is no current event loop in thread 'MainThread'`**:
   - Ensure you are running the updated code where event loop initialization occurs before Pyrogram imports.

2. **`[400 API_ID_INVALID]`**:
   - Verify `API_ID` (integer) and `API_HASH` (string) from [my.telegram.org](https://my.telegram.org).

3. **`[400 BOT_TOKEN_INVALID]`**:
   - Check `BOT_TOKEN` for typos or regenerate via [@BotFather](https://t.me/BotFather).

4. **Bot not streaming / HTTP 400 on media links**:
   - Verify the bot (and any multi-client bots) are promoted to **Admin** in the `BIN_CHANNEL`.
   - Verify `BIN_CHANNEL` starts with `-100`.

5. **`BadMsgNotification` or system clock drift**:
   - The application automatically attempts NTP time offset synchronization on startup. Ensure outbound UDP port 123 is not blocked by host firewall rules.
