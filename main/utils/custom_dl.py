# This file is a part of TG-Direct-Link-Generator

import math
import asyncio
import logging
from main import Var
from typing import Dict, Union
from main.bot import work_loads
from pyrogram import Client, utils, raw
from .file_properties import get_file_ids
from pyrogram.session import Session, Auth
from pyrogram.errors import AuthBytesInvalid
from main.server.exceptions import FIleNotFound
from pyrogram.file_id import FileId, FileType, ThumbnailSource


async def chunk_size(length):
    # 4MB chunks (was 512KB) — each chunk is one MTProto round trip, so larger
    # chunks directly reduce the round-trip count by 8× for typical video.
    return 2 ** max(min(math.ceil(math.log2(length / 1024)), 12), 2) * 1024


async def offset_fix(offset, chunksize):
    offset -= offset % chunksize
    return offset


class ByteStreamer:
    def __init__(self, client: Client):
        """A custom class that holds the cache of a specific client and class functions.
        attributes:
            client: the client that the cache is for.
            cached_file_ids: a dict of cached file IDs.
            cached_file_properties: a dict of cached file properties.
        
        functions:
            generate_file_properties: returns the properties for a media of a specific message contained in Tuple.
            generate_media_session: returns the media session for the DC that contains the media file.
            yield_file: yield a file from telegram servers for streaming.
            
        This is a modified version of the <https://github.com/eyaadh/megadlbot_oss/blob/master/mega/telegram/utils/custom_download.py>
        Thanks to Eyaadh <https://github.com/eyaadh>
        """
        self.clean_timer = 30 * 60
        self.client: Client = client
        self.cached_file_ids: Dict[int, FileId] = {}
        asyncio.create_task(self.clean_cache())

    async def get_file_properties(self, message_id: int) -> FileId:
        """
        Returns the properties of a media of a specific message in a FIleId class.
        if the properties are cached, then it'll return the cached results.
        or it'll generate the properties from the Message ID and cache them.
        """
        if message_id not in self.cached_file_ids:
            await self.generate_file_properties(message_id)
            logging.debug(f"Cached file properties for message with ID {message_id}")
        return self.cached_file_ids[message_id]
    
    async def generate_file_properties(self, message_id: int) -> FileId:
        """
        Generates the properties of a media file on a specific message.
        returns ths properties in a FIleId class.
        """
        file_id = await get_file_ids(self.client, Var.BIN_CHANNEL, message_id)
        logging.debug(f"Generated file ID and Unique ID for message with ID {message_id}")
        if not file_id:
            logging.debug(f"Message with ID {message_id} not found")
            raise FIleNotFound
        self.cached_file_ids[message_id] = file_id
        logging.debug(f"Cached media message with ID {message_id}")
        return self.cached_file_ids[message_id]

    async def generate_media_session(self, client: Client, file_id: FileId) -> Session:
        """
        Generates the media session for the DC that contains the media file.
        This is required for getting the bytes from Telegram servers.
        """

        media_session = client.media_sessions.get(file_id.dc_id, None)

        if media_session is None:
            if file_id.dc_id != await client.storage.dc_id():
                media_session = Session(
                    client,
                    file_id.dc_id,
                    await Auth(
                        client, file_id.dc_id, await client.storage.test_mode()
                    ).create(),
                    await client.storage.test_mode(),
                    is_media=True,
                )
                await media_session.start()

                for _ in range(6):
                    exported_auth = await client.invoke(
                        raw.functions.auth.ExportAuthorization(dc_id=file_id.dc_id)
                    )

                    try:
                        await media_session.send(
                            raw.functions.auth.ImportAuthorization(
                                id=exported_auth.id, bytes=exported_auth.bytes
                            )
                        )
                        break
                    except AuthBytesInvalid:
                        logging.debug(
                            f"Invalid authorization bytes for DC {file_id.dc_id}"
                        )
                        continue
                else:
                    await media_session.stop()
                    raise AuthBytesInvalid
            else:
                media_session = Session(
                    client,
                    file_id.dc_id,
                    await client.storage.auth_key(),
                    await client.storage.test_mode(),
                    is_media=True,
                )
                await media_session.start()
            logging.debug(f"Created media session for DC {file_id.dc_id}")
            client.media_sessions[file_id.dc_id] = media_session
        else:
            logging.debug(f"Using cached media session for DC {file_id.dc_id}")
        return media_session


    @staticmethod
    async def get_location(file_id: FileId) -> Union[raw.types.InputPhotoFileLocation,
                                                     raw.types.InputDocumentFileLocation,
                                                     raw.types.InputPeerPhotoFileLocation,]:
        """
        Returns the file location for the media file.
        """
        file_type = file_id.file_type

        if file_type == FileType.CHAT_PHOTO:
            if file_id.chat_id > 0:
                peer = raw.types.InputPeerUser(
                    user_id=file_id.chat_id, access_hash=file_id.chat_access_hash
                )
            else:
                if file_id.chat_access_hash == 0:
                    peer = raw.types.InputPeerChat(chat_id=-file_id.chat_id)
                else:
                    peer = raw.types.InputPeerChannel(
                        channel_id=utils.get_channel_id(file_id.chat_id),
                        access_hash=file_id.chat_access_hash,
                    )

            location = raw.types.InputPeerPhotoFileLocation(
                peer=peer,
                volume_id=file_id.volume_id,
                local_id=file_id.local_id,
                big=file_id.thumbnail_source == ThumbnailSource.CHAT_PHOTO_BIG,
            )
        elif file_type == FileType.PHOTO:
            location = raw.types.InputPhotoFileLocation(
                id=file_id.media_id,
                access_hash=file_id.access_hash,
                file_reference=file_id.file_reference,
                thumb_size=file_id.thumbnail_size,
            )
        else:
            location = raw.types.InputDocumentFileLocation(
                id=file_id.media_id,
                access_hash=file_id.access_hash,
                file_reference=file_id.file_reference,
                thumb_size=file_id.thumbnail_size,
            )
        return location

    async def yield_file(
        self,
        file_id: FileId,
        index: int,
        offset: int,
        first_part_cut: int,
        last_part_cut: int,
        part_count: int,
        chunk_size: int,
    ) -> Union[str, None]:
        """
        Pipelined file yielder. Fetches up to PARALLEL_CHUNKS chunks in flight
        per client via asyncio tasks so the effective per-request throughput
        is bounded by MTProto bandwidth, not RTT latency.
        """
        client = self.client
        work_loads[index] += 1
        media_session = await self.generate_media_session(client, file_id)
        location = await self.get_location(file_id)

        # How many chunk requests to keep in flight at once. 4 is safe for
        # Pyrogram; higher values bump against FloodWait under load.
        PARALLEL = 4

        # Every fetch is one GetFile call at a specific byte offset.
        async def fetch(off):
            r = await media_session.send(
                raw.functions.upload.GetFile(location=location, offset=off, limit=chunk_size),
            )
            if isinstance(r, raw.types.upload.File):
                return r.bytes
            return b""

        try:
            # Queue of (chunk index, byte offset) to fetch.
            chunk_queue = []
            cur_offset = offset
            for part in range(1, part_count + 1):
                chunk_queue.append((part, cur_offset))
                cur_offset += chunk_size

            # Sliding window: submit PARALLEL at a time, yield in order.
            pending = []  # list of asyncio.Task
            next_iter = iter(enumerate(chunk_queue))
            in_order_buffer = {}  # part_index (1-based) -> bytes

            async def submit_one():
                try:
                    _, (part, off) = next(next_iter)
                except StopIteration:
                    return False
                t = asyncio.create_task(fetch(off))
                pending.append((part, t))
                return True

            # Prime the window.
            for _ in range(min(PARALLEL, len(chunk_queue))):
                await submit_one()

            current_part = 1
            while pending:
                # Wait for the *next expected* task so we can yield in order.
                for (part, task) in list(pending):
                    if part == current_part and task.done():
                        pending.remove((part, task))
                        chunk = task.result()
                        if not chunk:
                            # empty chunk = EOF
                            return
                        if part_count == 1:
                            yield chunk[first_part_cut:last_part_cut]
                        elif current_part == 1:
                            yield chunk[first_part_cut:]
                        elif current_part == part_count:
                            yield chunk[:last_part_cut]
                        else:
                            yield chunk
                        current_part += 1
                        # Backfill the pipelined window.
                        if await submit_one():
                            pass
                        break
                else:
                    # Next part hasn't finished — wait briefly for it.
                    await asyncio.sleep(0.005)
        except (TimeoutError, AttributeError):
            pass
        finally:
            for (_, t) in pending:
                t.cancel()
            work_loads[index] -= 1

    
    async def clean_cache(self) -> None:
        """
        function to clean the cache to reduce memory usage
        """
        while True:
            await asyncio.sleep(self.clean_timer)
            self.cached_file_ids.clear()
            logging.debug("Cleaned the cache")


async def prewarm_media_sessions():
    """
    Forge media sessions to all known Telegram DCs at startup so the first
    incoming request to a "cold" DC doesn't pay the ExportAuthorization +
    ImportAuthorization handshake (which can add 300–800 ms per first fetch).

    DCs 1..5 cover all current production regions. Failures are non-fatal —
    the session will be created lazily on first use anyway.
    """
    from main.bot import StreamBot
    if not getattr(Var, "PREWARM_DCS", False):
        return
    logging.info("Pre-warming DC media sessions (1..5)")
    from pyrogram.session import Session, Auth
    from pyrogram import raw
    for dc in range(1, 6):
        try:
            if dc == await StreamBot.storage.dc_id():
                continue
            sess = Session(
                StreamBot,
                dc,
                await Auth(StreamBot, dc, await StreamBot.storage.test_mode()).create(),
                await StreamBot.storage.test_mode(),
                is_media=True,
            )
            await sess.start()
            for _ in range(6):
                exported = await StreamBot.invoke(raw.functions.auth.ExportAuthorization(dc_id=dc))
                try:
                    await sess.send(raw.functions.auth.ImportAuthorization(id=exported.id, bytes=exported.bytes))
                    break
                except Exception as e:
                    logging.debug(f"Pre-warm auth for DC {dc} attempt failed: {e}")
            StreamBot.media_sessions[dc] = sess
            logging.info(f"✅ DC {dc} media session ready")
        except Exception as e:
            logging.warning(f"⚠️ Failed to pre-warm DC {dc}: {e}")