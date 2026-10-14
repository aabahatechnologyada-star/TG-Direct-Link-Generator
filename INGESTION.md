# Auto-Ingestion Pipeline

This bot now automatically catalogs every movie/TV file posted to a designated
Telegram channel. For each file it will: identify the title via TMDb, fetch
metadata, construct a streaming URL via the existing pipeline, write the movie
into your Riyo backend's MongoDB, and mark the file as ingested so re-uploads
never duplicate.

## Architecture

```
Telegram INGEST_CHANNEL  ──►  parse_filename()   (title, year, SxxEyy)
                                  │
                                  ▼
                            TMDbClient.lookup()
                                  │
                                  ▼
                            Confidence scoring
                                  │
                  ┌───────────────┴────────────────┐
                  │                                 │
             conf ≥ 55                         conf < 55
                  │                                 │
                  ▼                                 ▼
       Mongo upsert + reply ok          Ask owner: /publish <id> | skip
                  │
                  ▼
            mark_ingested()
```

## Setup

Required variables are added to `vars.py` and read from env:

| Variable | Purpose |
|---|---|
| `INGEST_CHANNEL` | Telegram channel ID (`-1001234567890`) where you drop files. Set to `0` to disable the pipeline. |
| `TMDB_API_KEY` | From https://www.themoviedb.org/settings/api (free) |
| `MONGO_URI` | Your Riyo MongoDB connection string |
| `MONGO_DB` | Database name, default `riyo` |
| `INGEST_MIN_BYTES` | Files smaller than this are skipped (default 10 MB) |
| `PARALLEL_CHUNKS` | In-flight MTProto chunkrequests per stream (default 4) |
| `PREWARM_DCS` | Set `0` to disable DC session pre-warming |

## Behaviour specifics

- **Title parsing** understands scene naming: `Show.Name.S01E02.1080p.WEB-DL.mkv`
  → `Show Name` (S01E02, is_tv=True). Movie files with year brackets are
  handled: `Inception (2010) 1080p` → `Inception` (2010, movie).
- **TMDb lookup** uses `/search/multi` (movies + TV simultaneously) with a
  per-instance cache keyed on (title, year, prefer_tv). Fallback retries
  without the year if the first query is empty.
- **`_enrich()`** pulls `/movie/{id}`, `/tv/{id}` details, credits, and
  videos — giving genres, cast (up to 10), creators (TV), rating, votes,
  popularity, poster/backdrop URLs, and a trailer link when present.
- **Confidence scoring** weights popularity (>30 → +15), votes (>200 → +20,
  >50 → +10), rating presence (+10), and year extracted (+5). Below 55 the
  pipeline pauses and asks a human — replying `/publish <tmdb_id>` to the bot
  in owner DM force-publishes.
- **Idempotent writes** key on `(tmdbId, year)` — uploading the same film in
  1080p and 2160p updates the existing Riyo movie document, never duplicates.
- Every ingested file is also marked by `file_unique_id` in
  `ingested_files` so a Telegram re-forward doesn't re-trigger the TMDb hit.

## What it writes into MongoDB `movies`

All fields Riyo's mobile app already reads:
`tmdbId, title, originalTitle, description, year, genre[], rating, votes,
popularity, posterUrl, backdropUrl, language, cast[], creators[], contentType
(movie|series), isTvShow, isPublished=true, sourceType="bot_ingest", videoUrl,
sources=[{url, label:"Telegram", quality:"auto"}], assignedSections=["latest"],
ingestedAt, createdAt, updatedAt`

Because the app's category endpoint matches the `genre` array using
case-insensitive aliases, ingested content flows straight into
`/categories/movies/action/movies`, `/categories/movies/comedy/movies`, etc.

## Limits

- TMDb keys are IP-limited to 40 requests per 10 seconds. The pipeline holds a
  Semaphore(8) which is safe for sequential drops (~10 movies/burst). For bulk
  backfill, space the posts a minute apart.
- Filename parsing is heuristic. Worst case a wrong title is suggested; the
  confidence gate stops it from auto-publishing, and the bot asks you first.
- Backfill the library by posting batches of files to INGEST_CHANNEL.
- The bot needs **access to post** in `INGEST_CHANNEL` and in `BIN_CHANNEL`.
