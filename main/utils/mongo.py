"""
Async Mongo client for the ingestion pipeline.

Uses Motor (async PyMongo). Connects to the same MongoDB as Riyo's backend
so documents are directly consumable by the mobile app without any scraper.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

try:
    from motor.motor_asyncio import AsyncIOMotorClient
    _MOTOR_AVAILABLE = True
except ImportError:
    AsyncIOMotorClient = None  # type: ignore
    _MOTOR_AVAILABLE = False


class Mongo:
    """Singleton Motor client wrapper; safe to call get_db() repeatedly."""

    _client: Optional[Any] = None
    _db_name: str = "riyo"

    @classmethod
    async def init(cls, uri: str, db_name: str = "riyo") -> None:
        if not _MOTOR_AVAILABLE:
            raise RuntimeError("motor is not installed. Add 'motor' to requirements.txt.")
        if cls._client is not None:
            return
        cls._db_name = db_name
        cls._client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=10000)
        # Force connection check
        await cls._client.admin.command("ping")
        logging.info("✅ MongoDB (ingest) connected")

    @classmethod
    def db(cls):
        if cls._client is None:
            raise RuntimeError("Mongo not initialised. Call Mongo.init() first.")
        return cls._client[cls._db_name]

    @classmethod
    async def close(cls) -> None:
        if cls._client is not None:
            cls._client.close()
            cls._client = None


async def upsert_movie(doc: Dict[str, Any]) -> str:
    """
    Idempotent upsert of a movie document into the Riyo 'movies' collection.
    Matches on (tmdbId, year) to avoid double-ingestion when the same title
    arrives as multiple qualities.
    """
    movies = Mongo.db()["movies"]
    key = {"tmdbId": doc.get("tmdbId")}
    if doc.get("year"):
        key["year"] = doc["year"]
    res = await movies.update_one(key, {"$set": doc}, upsert=True)
    if res.upserted_id:
        return str(res.upserted_id)
    existing = await movies.find_one(key, {"_id": 1})
    return str(existing["_id"]) if existing else ""


async def already_ingested(file_unique_id: str) -> bool:
    coll = Mongo.db()["ingested_files"]
    existing = await coll.find_one({"file_unique_id": file_unique_id})
    return existing is not None


async def mark_ingested(file_unique_id: str, tmdb_id: int, title: str) -> None:
    coll = Mongo.db()["ingested_files"]
    import datetime as _dt
    await coll.update_one(
        {"file_unique_id": file_unique_id},
        {"$set": {
            "file_unique_id": file_unique_id,
            "tmdbId": tmdb_id,
            "title": title,
            "at": _dt.datetime.utcnow(),
        }},
        upsert=True,
    )
