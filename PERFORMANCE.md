# Performance & Hardening — TG-Direct-Link-Generator

This document explains the changes in this fork relative to upstream, why they
exist, and what they buy you.

## What was slow and why

Upstream's streaming handler serviced one Telegram chunk request per RTT
(`upload.GetFile`), with chunks capped at ~512 KB. Real-world effect:

- A 3 MB HLS segment → 6 sequential MTProto calls → ~1.5–3 s start-up latency.
- Single bot token → every user shares one TCP+auth pipeline per DC.
- First request to a cold DC paid the ExportAuthorization/ImportAuthorization
  handshake — up to 800 ms of nothing.
- No HTTP cache headers → players re-fetched segments, doubling bandwidth.

## Changes

### 1. Chunk size 4 MB (`custom_dl.py:chunk_size`)

Cap raised from `min(log2(...), 10)` to `min(log2(...), 12)` → 4 MB per chunk.
Each chunk is **one** MTProto request, so this is the single-largest win for
typical video (8× fewer round trips for 4 MB media).

### 2. Pipelined chunk fetcher (`custom_dl.py:ByteStreamer.yield_file`)

Replaces the sequential `while current_part <= part_count` loop with an
asyncio sliding-window pipeline:

- `PARALLEL = 4` chunk requests in flight at once (env-configurable as
  `PARALLEL_CHUNKS`).
- In-order drain: yields when the *next expected* chunk completes; concurrent
  prefetches of later chunks hide MTProto RTTs behind download time.
- On disconnect/error, pending tasks are cancelled cleanly so no `work_loads`
  drift.

Effective per-request throughput is bounded by MTProto bandwidth, not RTT.

### 3. DC session pre-warming (`custom_dl.py:prewarm_media_sessions`)

On startup, constructs media sessions for DCs 1–5 (excluding the bot's home
DC) and runs the auth handshake in the background (`asyncio.create_task`, so
it never blocks service start).

First request to a cold DC now goes straight to `upload.GetFile` instead of
ExportAuthorization → ImportAuthorization → GetFile.

Configured via `PREWARM_DCS=1` (default on), set to `0` to disable.

### 4. HTTP caching + ETag (`stream_routes.py:media_streamer`)

Responses now carry:

```
Cache-Control: public, max-age=3600
ETag: "<message_id>-<file_size>-<hash>"
```

HLS/HTML5 players that re-request a segment (e.g. after a transient buffer
stall) hit the browser cache instead of paying Telegram again.

`Accept-Ranges: bytes` + 206 handling is unchanged — Range requests work
exactly as before.

### 5. Health endpoint (`stream_routes.py:/health`)

`GET /health` returns JSON: `{status, clients, sessions_cached}`. Use as the
Render/Railway liveness probe instead of scraping `/`.

## Configuration

All knobs optional; defaults are safe.

| Env var | Default | Meaning |
|---|---|---|
| `PARALLEL_CHUNKS` | `4` | In-flight MTProto chunk requests per stream |
| `PREWARM_DCS` | `1` | Pre-open DC 1–5 media sessions at startup |
| `WORKERS` | `6` | Pyrogram handler concurrency |
| `SLEEP_THRESHOLD` | `60` | FloodWait pause ceiling |
| `MULTI_TOKEN1..N` | — | Extra bot tokens for multi-client fan-out |

## How the NTP fix interacts

This fork already contains the Telegram-time-sync fix in `__main__.py`:
`PYROGRAM_IGNORE_TIME=1`, forced TZ, NTP offset measured against
pool.ntp.org / Google / Cloudflare, and the offset applied via
`session.time_offset` on `BadMsgNotification`. This made the sign-in succeed
from hosts with drifted clocks.

The performance work above doesn't touch that code path; we just don't need
NTP round-trips on every request anymore.

## Known limits that remain

- One host = one network. Client-side parallelism helps but can't exceed the
  NIC or the ratelimit of the bot token. For hot files, mirror to a real CDN
  after first play and serve from there.
- Telegram's per-session auth exports are still subject to FloodWait. The
  retry loop (6 tries, log-warn) tolerates this gracefully.
- No authorization model other than the URL hash — anyone with the link can
  fetch. That is by design (the link is the bearer token).
