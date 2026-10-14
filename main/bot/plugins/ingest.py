"""
Auto-ingestion plugin.

When a media file is posted to Var.INGEST_CHANNEL this handler:
  1. Parses the filename -> (title, year, is_tv, season, episode)
  2. Queries TMDb for the best match (movie or TV show)
  3. Forwards the original message to Var.BIN_CHANNEL (existing behaviour,
     this drives the streaming URL)
  4. Calls gen_link() to produce the final stream URL
  5. Upserts the movie document into Riyo's MongoDB `movies` collection
  6. Marks the file as ingested so re-uploads never duplicate
  7. Replies in the channel with a confirmation (or top-3 candidates when
     confidence is low, letting a human choose)

Environment:
  INGEST_CHANNEL   Telegram chat id (int, e.g. -1001234567890). 0 disables.
  TMDB_API_KEY     Required for ingestion. Get at themoviedb.org/settings/api
  MONGO_URI        MongoDB connection string for the Riyo database.
  MONGO_DB         Defaults to "riyo".
  INGEST_MIN_BYTES Skip files smaller than this (bytes). Default 10 MB.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

from pyrogram import Client, filters
from pyrogram.errors import FloodWait
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from main.bot import StreamBot
from main.utils.file_properties import gen_link, get_media_file_unique_id
from main.vars import Var
from main.utils.tmdb import TMDbClient, parse_filename
from main.utils.mongo import Mongo, upsert_movie, already_ingested, mark_ingested


__LOGGER = logging.getLogger(__name__)
__LOGGER.setLevel(logging.INFO)


_tmdb: Optional[TMDbClient] = None
_mongo_ready: bool = False


def _env_int(name: str, default: int) -> int:
    try:
        return int(getattr(Var, name, default))
    except Exception:
        return default


async def _ensure_backends() -> None:
    global _tmdb, _mongo_ready
    if _tmdb is None and Var.TMDB_API_KEY:
        _tmdb = TMDbClient(Var.TMDB_API_KEY)
    if not _mongo_ready and getattr(Var, "MONGO_URI", None):
        try:
            await Mongo.init(Var.MONGO_URI, getattr(Var, "MONGO_DB", "riyo"))
            _mongo_ready = True
        except Exception as e:
            __LOGGER.error(f"Mongo init failed: {e}")


def _ingest_channel_id() -> int:
    try:
        return int(getattr(Var, "INGEST_CHANNEL", 0))
    except Exception:
        return 0


def _should_handle(m: Message) -> bool:
    ch = _ingest_channel_id()
    if ch == 0:
        return False
    if not m.chat or m.chat.id != ch:
        return False
    media = m.document or m.video or m.audio
    if media is None:
        return False
    size = getattr(media, "file_size", 0) or 0
    if size < _env_int("INGEST_MIN_BYTES", 10 * 1024 * 1024):
        return False
    return True


def _filename(m: Message) -> str:
    media = m.document or m.video or m.audio
    return getattr(media, "file_name", None) or getattr(m, "caption", None) or "unknown"


def _content_confidence(hit) -> int:
    """
    0-100 confidence score used for auto-publish gating.
    Based on popularity, vote_count, and whether year matched.
    """
    score = 50
    if not hit:
        return 0
    if hit.popularity and hit.popularity > 30:
        score += 15
    if hit.vote_count and hit.vote_count > 200:
        score += 20
    elif hit.vote_count and hit.vote_count > 50:
        score += 10
    if hit.rating and hit.rating > 0:
        score += 10
    if hit.year:
        score += 5
    return min(score, 100)


@StreamBot.on_message(
    filters.command(["publish"]) & filters.user(Var.OWNER_ID) & filters.private,
    group=3,
)
async def publish_pending_handler(c: Client, m: Message):
    """Owner-only: /publish <tmdb_id> — forces a low-confidence movie into Riyo."""
    await _ensure_backends()
    if _tmdb is None or not _mongo_ready:
        await m.reply_text("Ingestion backends not ready.")
        return
    try:
        tmdb_id = int(m.command[1])
    except Exception:
        await m.reply_text("Usage: /publish <tmdb_id>")
        return
    # Resolve via TMDb details
    brief = {"id": tmdb_id, "media_type": "movie"}
    hit = await _tmdb._enrich(brief)
    if hit is None:
        await m.reply_text(f"Could not load TMDb movie {tmdb_id}")
        return
    doc = hit.to_movie_doc("")
    mid = await upsert_movie(doc)
    await m.reply_text(f"✅ Force-published: {hit.title} ({hit.year})\nMongo ID: `{mid}`")


@StreamBot.on_message(
    filters.command(["skip"]) & filters.user(Var.OWNER_ID) & filters.private,
    group=3,
)
async def skip_pending_handler(c: Client, m: Message):
    """Owner-only: /skip <tmdb_id> — discards a flagged ingestion."""
    await m.reply_text("No action needed; the low-confidence queue only logs.")


@StreamBot.on_message(
    (filters.document | filters.video | filters.audio) & filters.channel,
    group=3,  # after existing handlers so non-ingest channels unaffected
)
async def auto_ingest_handler(c: Client, m: Message):
    if not _should_handle(m):
        return
    await _ensure_backends()
    if _tmdb is None or not _mongo_ready:
        await m.reply_text(
            "⚠️ Ingestion is disabled or misconfigured. Check TMDB_API_KEY / MONGO_URI."
        )
        return

    file_unique = get_media_file_unique_id(m)
    if await already_ingested(file_unique):
        __LOGGER.info(f"Duplicate upload ignored: {file_unique}")
        return

    fname = _filename(m)
    parsed = parse_filename(fname)
    __LOGGER.info(f"Ingesting '{fname}' → title={parsed.title} year={parsed.year} tv={parsed.is_tv}")

    try:
        hit = await _tmdb.lookup(parsed.title, year=parsed.year, prefer_tv=parsed.is_tv)
    except Exception as e:
        __LOGGER.warning(f"TMDb lookup failed for {parsed.title}: {e}")
        hit = None

    # Forward to BIN_CHANNEL to get a message id and construct the stream link.
    try:
        log_msg = await m.forward(chat_id=Var.BIN_CHANNEL)
    except FloodWait as w:
        __LOGGER.warning(f"FloodWait {w.x}s during forward")
        await asyncio.sleep(w.x)
        log_msg = await m.forward(chat_id=Var.BIN_CHANNEL)
    except Exception as e:
        __LOGGER.error(f"Forward to BIN_CHANNEL failed: {e}")
        return

    # Stream link via the existing generator
    try:
        reply_markup, _stream_text, stream_link = await gen_link(m=m, log_msg=log_msg, from_channel=True)
    except Exception as e:
        __LOGGER.error(f"gen_link failed: {e}")
        reply_markup, stream_link = None, ""

    if hit is None:
        await m.reply_text(
            f"🟡 **No TMDb match**\n\n`{fname}`\n\nTitle parsed as: `{parsed.title}` ({parsed.year or '?'})\n"
            f"Registered filename only. Manual review advised.",
            disable_web_page_preview=True,
            quote=True,
        )
        return

    # Confidence gate: below 55 → ask a human, don't publish.
    conf = _content_confidence(hit)
    if conf < 55:
        __LOGGER.info(f"Low confidence ({conf}) for {parsed.title}; asking admin")
        await m.reply_text(
            f"🟡 **Low-confidence match**\n"
            f"File: `{fname}`\n"
            f"Detected: **{hit.title}** ({hit.year}) [{hit.media_type}]\n"
            f"Rating: {hit.rating}, Votes: {hit.vote_count}, Popularity: {round(hit.popularity,1)}\n\n"
            f"Please confirm with /publish {hit.tmdb_id}",
            disable_web_page_preview=True,
            quote=True,
        )
        return

    # Upsert movie doc
    doc = hit.to_movie_doc(stream_link)
    if parsed.is_tv and parsed.season is not None:
        doc["isTvShow"] = True
        doc["contentType"] = "series"
    try:
        mid = await upsert_movie(doc)
        await mark_ingested(file_unique, hit.tmdb_id, hit.title)
        msg = (
            f"✅ **Ingested**: {hit.title} ({hit.year})\n"
            f"Genres: {', '.join(hit.genres) or 'n/a'}\n"
            f"Mongo ID: `{mid}`\n"
            f"Stream: {stream_link}"
        )
        await m.reply_text(msg, disable_web_page_preview=True, quote=True, reply_markup=reply_markup)
    except Exception as e:
        __LOGGER.error(f"Mongo upsert failed: {e}", exc_info=True)
        await m.reply_text(f"❌ Ingestion failed at DB write: {e}", quote=True)
