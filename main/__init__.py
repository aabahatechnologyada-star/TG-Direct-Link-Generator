# This file is a part of TG-Direct-Link-Generator

import time

# ============================================================
# MONKEY PATCH: Fix Pyrogram peer ID resolution for -100 channels
# ============================================================
from pyrogram import utils

def get_peer_type_new(peer_id: int) -> str:
    peer_id_str = str(peer_id)
    if not peer_id_str.startswith("-"):
        return "user"
    elif peer_id_str.startswith("-100"):
        return "channel"
    else:
        return "chat"

utils.get_peer_type = get_peer_type_new
# ============================================================

__version__ = 2.2
StartTime = time.time()

from .vars import Var
from main.bot.clients import StreamBot
