"""
TMDb + filename-parsing utilities for the auto-ingestion pipeline.

Everything here is pure-Python and unit-testable without a live Telegram
connection. The heavy lifting is done by aiohttp (which is already a project
dependency) and by a conservative filename-to-(title, year, is_tv) parser.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import aiohttp

TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_IMAGE_W342 = "https://image.tmdb.org/t/p/w342"
TMDB_IMAGE_W500 = "https://image.tmdb.org/t/p/w500"


# -----------------------------------------------------------------------------
# Filename parsing
# -----------------------------------------------------------------------------

# Common scene/release tags that pollute filenames; anything from this list at
# a word boundary marks the END of the real title.
_STOP_TAGS = re.compile(
    r"\b("
    r"480p|576p|720p|900p|1080p|1440p|2160p|4320p|"
    r"4k|8k|uhd|imax|"
    r"bluray|blu-?ray|bdrip|bd-?rip|brrip|web[-. ]?dl|webrip|web[-. ]?rip|"
    r"hdtv|hd[-. ]?tv|hdrip|dvdrip|dvd[-. ]?rip|"
    r"x264|x265|h264|h265|hevc|avc|"
    r"10bit|8bit|hdr10\+?|hdr|dv|dolby[-. ]?vision|"
    r"aac|ac3|eac3|ddp?5[.-]?1|dts|atmos|truehd|"
    r"extended|director'?s[-. ]?cut|unrated|remastered|"
    r"proper|repack|internal|limited|"
    r"nf|amzn|hulu|dsnp|atvp|pcok|max|"
    r"yts(?:\.(?:mx|ag|am|lt|gs|se))?|rarbg|eztv|"
    r"5\.1|7\.1|2\.0|mp3|flac"
    r")\b",
    re.IGNORECASE,
)

# Scene groups often suffixed at end:  -GROUP
_TRAILING_GROUP = re.compile(r"-[A-Za-z0-9_]{2,20}$")

# Year in parentheses or standalone
_YEAR = re.compile(r"[\(\[\{]?((?:19|20)\d{2})[\)\]\}]?")

# TV markers: S01E02, S01, 1x02, Season 1, etc.
_TV_MARKER = re.compile(
    r"(?:\bs\d{1,2}(?:e\d{1,3})?\b|\b\d{1,2}x\d{1,3}\b|\bseason\s*\d{1,2}\b|\bs\d{1,2}\b)",
    re.IGNORECASE,
)


@dataclass
class ParsedFilename:
    title: str
    year: Optional[int] = None
    is_tv: bool = False
    season: Optional[int] = None
    episode: Optional[int] = None
    raw: str = ""


def parse_filename(filename: str) -> ParsedFilename:
    """
    Parse a release-style filename into (title, year, is_tv, season, episode).

    Strategy:
      1. Normalize separators (dots/underscores -> spaces).
      2. Cut at first stop-tag (everything after is release junk).
      3. Detect SxxEyy, 1x02, "Season N" markers -> TV show.
      4. Extract trailing or middle year.
      5. Title is whatever remains, title-cased.
    """
    raw = filename
    # Strip extension
    name = re.sub(r"\.(mkv|mp4|avi|mov|webm|m4v|ts|wmv|flv|srt|vtt|ass)$", "", filename, flags=re.IGNORECASE)
    # Normalize separators
    name = re.sub(r"[._]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip()

    # TV episode marker?
    is_tv = False
    season = None
    episode = None
    m = re.search(r"\bs(\d{1,2})e(\d{1,3})\b", name, re.IGNORECASE)
    if m:
        is_tv = True
        season = int(m.group(1))
        episode = int(m.group(2))
        name = name[: m.start()].strip()
    else:
        m = re.search(r"\b(\d{1,2})x(\d{1,3})\b", name)
        if m:
            is_tv = True
            season = int(m.group(1))
            episode = int(m.group(2))
            name = name[: m.start()].strip()
        else:
            m = re.search(r"\bseason\s*(\d{1,2})\b", name, re.IGNORECASE)
            if m:
                is_tv = True
                season = int(m.group(1))
                name = name[: m.start()].strip()

    # TV episode marker? Also cut name at bare "Sxx" / "Season N" (the
    # episode-marker regexes above already trim; bare season flags don't).
    m_bare_s = re.search(r"\bs\d{1,2}\b", name, re.IGNORECASE)
    if m_bare_s:
        is_tv = True
        if season is None:
            try:
                season = int(m_bare_s.group(0)[1:])
            except ValueError:
                pass
        name = name[: m_bare_s.start()].strip()

    # Cut at first stop-tag
    m = _STOP_TAGS.search(name)
    if m:
        name = name[: m.start()].strip()
    # Re-cut after stop-tag in case new text appeared
    m_bare_s = re.search(r"\bs\d{1,2}\b", name, re.IGNORECASE)
    if m_bare_s:
        is_tv = True
        name = name[: m_bare_s.start()].strip()

    # Strip trailing scene group ("-EVO")
    name = _TRAILING_GROUP.sub("", name).strip()

    # Year
    year: Optional[int] = None
    m = _YEAR.search(name)
    if m:
        try:
            year = int(m.group(1))
            name = (name[: m.start()] + " " + name[m.end():]).strip()
        except ValueError:
            pass

    # Bracketed leftovers like (2023) or [US]
    name = re.sub(r"[\(\[\{][^\)\]\}]*[\)\]\}]", " ", name)
    name = re.sub(r"\s+", " ", name).strip(" -_")

    # Title-case (small connecting words stay lowercase; first word capitalized)
    _SMALL_WORDS = {"of", "the", "a", "an", "and", "or", "but", "in", "on", "at",
                    "to", "for", "with", "by", "from", "is"}

    def _norm(w: str, first: bool) -> str:
        if w.isupper() and len(w) > 1:
            return w
        if w.isdigit():
            return w
        if not first and w.lower() in _SMALL_WORDS:
            return w.lower()
        return w.capitalize()

    words = [w for w in name.split() if w]
    title = " ".join(_norm(w, i == 0) for i, w in enumerate(words))
    return ParsedFilename(title=title, year=year, is_tv=is_tv,
                          season=season, episode=episode, raw=raw)


# -----------------------------------------------------------------------------
# TMDb client
# -----------------------------------------------------------------------------


@dataclass
class TMDbHit:
    tmdb_id: int
    media_type: str           # "movie" | "tv"
    title: str
    original_title: str
    year: Optional[int]
    overview: str
    genres: List[str] = field(default_factory=list)
    rating: float = 0.0
    vote_count: int = 0
    popularity: float = 0.0
    poster_url: Optional[str] = None
    backdrop_url: Optional[str] = None
    original_language: Optional[str] = None
    cast: List[str] = field(default_factory=list)
    creators: List[str] = field(default_factory=list)
    trailer_url: Optional[str] = None

    def to_movie_doc(self, stream_url: str) -> Dict[str, Any]:
        """Project this hit into the Riyo `movies` collection schema."""
        not_years_old = True
        import datetime as _dt
        now = _dt.datetime.utcnow()
        return {
            "tmdbId": self.tmdb_id,
            "title": self.title,
            "originalTitle": self.original_title,
            "description": self.overview,
            "year": self.year,
            "genre": self.genres,
            "rating": self.rating,
            "votes": self.vote_count,
            "popularity": self.popularity,
            "posterUrl": self.poster_url,
            "backdropUrl": self.backdrop_url,
            "language": self.original_language,
            "cast": self.cast,
            "creators": self.creators,
            "contentType": "series" if self.media_type == "tv" else "movie",
            "isTvShow": self.media_type == "tv",
            "isPublished": True,
            "sourceType": "bot_ingest",
            "videoUrl": stream_url,
            "sources": [{"url": stream_url, "label": "Telegram", "quality": "auto"}],
            "subtitles": [],
            "views": 0,
            "assignedSections": ["latest"],
            "ingestedAt": now,
            "createdAt": now,
            "updatedAt": now,
        }


class TMDbClient:
    """Small async TMDb wrapper tuned for the ingestion use case."""

    def __init__(self, api_key: str, session: Optional[aiohttp.ClientSession] = None):
        self.api_key = api_key
        self._session = session
        self._semaphore = asyncio.Semaphore(8)  # stay well under 40/10s
        self._cache: Dict[tuple, Optional[TMDbHit]] = {}

    async def _get(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        params = dict(params or {})
        params["api_key"] = self.api_key
        async with self._semaphore:
            try:
                if self._session is None:
                    async with aiohttp.ClientSession() as s:
                        async with s.get(f"{TMDB_BASE}{path}", params=params, timeout=15) as r:
                            return await r.json()
                else:
                    async with self._session.get(f"{TMDB_BASE}{path}", params=params, timeout=15) as r:
                        return await r.json()
            except Exception as e:
                logging.warning(f"TMDb GET {path} failed: {e}")
                return {}

    async def lookup(
        self,
        title: str,
        year: Optional[int] = None,
        prefer_tv: Optional[bool] = None,
    ) -> Optional[TMDbHit]:
        """
        Find the best TMDb match. Uses search/multi first (movies+tv), then a
        second pass with the year filter if preferred. Caches by (title, year).
        """
        key = (title.lower(), year, prefer_tv)
        if key in self._cache:
            return self._cache[key]

        if not title:
            self._cache[key] = None
            return None

        path = "/search/multi"
        params = {"query": title, "include_adult": "false", "language": "en-US", "page": 1}
        if year:
            params["year"] = year
        data = await self._get(path, params)
        candidates = data.get("results", []) if isinstance(data, dict) else []

        # Fallback without year if the year-filtered search returned nothing
        if not candidates and year:
            params.pop("year", None)
            data = await self._get(path, params)
            candidates = data.get("results", []) if isinstance(data, dict) else []

        # Filter out people
        candidates = [c for c in candidates if c.get("media_type") in ("movie", "tv")]

        if not candidates:
            self._cache[key] = None
            return None

        # Ranking: exact-year first, then by popularity
        def score(c):
            s = 0
            if year:
                rel = c.get("release_date") or c.get("first_air_date") or ""
                if rel.startswith(str(year)):
                    s += 1000
            s += min(c.get("popularity") or 0, 500)
            if prefer_tv is True and c.get("media_type") == "tv":
                s += 200
            if prefer_tv is False and c.get("media_type") == "movie":
                s += 200
            return s

        candidates.sort(key=score, reverse=True)
        best = candidates[0]
        hit = await self._enrich(best)
        self._cache[key] = hit
        return hit

    async def _enrich(self, brief: Dict[str, Any]) -> Optional[TMDbHit]:
        """Pull details for the best candidate: genres, credits, trailer."""
        media_type = brief.get("media_type")
        tmdb_id = brief.get("id")
        if not media_type or not tmdb_id:
            return None
        # If the hit is against our confidence bar, also fetch credits+videos.
        detail = await self._get(f"/{media_type}/{tmdb_id}", {})
        credits = await self._get(f"/{media_type}/{tmdb_id}/credits", {})
        videos = await self._get(f"/{media_type}/{tmdb_id}/videos", {})

        genres = [g.get("name", "") for g in (detail.get("genres") or []) if g.get("name")]

        cast = []
        for c in (credits.get("cast") or [])[:10]:
            if c.get("name"):
                cast.append(c["name"])

        creators = []
        if media_type == "tv":
            for c in (detail.get("created_by") or []):
                if c.get("name"):
                    creators.append(c["name"])

        trailer = None
        for v in videos.get("results") or []:
            if v.get("site") == "YouTube" and v.get("type") == "Trailer":
                trailer = f"https://www.youtube.com/watch?v={v.get('key')}"
                break

        year = None
        rel = brief.get("release_date") or brief.get("first_air_date") or detail.get("release_date") or detail.get("first_air_date") or ""
        if rel and len(rel) >= 4:
            try:
                year = int(rel[:4])
            except ValueError:
                pass

        poster = brief.get("poster_path") or detail.get("poster_path")
        backdrop = brief.get("backdrop_path") or detail.get("backdrop_path")

        return TMDbHit(
            tmdb_id=tmdb_id,
            media_type=media_type,
            title=brief.get("title") or brief.get("name") or "",
            original_title=brief.get("original_title") or brief.get("original_name") or "",
            year=year,
            overview=brief.get("overview") or detail.get("overview") or "",
            genres=genres,
            rating=float(brief.get("vote_average") or 0),
            vote_count=int(brief.get("vote_count") or 0),
            popularity=float(brief.get("popularity") or 0),
            poster_url=(TMDB_IMAGE_W342 + poster) if poster else None,
            backdrop_url=(TMDB_IMAGE_W500 + backdrop) if backdrop else None,
            original_language=brief.get("original_language"),
            cast=cast,
            creators=creators,
            trailer_url=trailer,
        )
