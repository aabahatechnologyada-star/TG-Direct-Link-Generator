# This file is a part of TG-Direct-Link-Generator

import asyncio

try:
    asyncio.get_event_loop_policy().get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from ..vars import Var
from pyrogram import Client
from os import getcwd

StreamBot = Client(
    name="main",
    api_id=Var.API_ID,
    api_hash=Var.API_HASH,
    workdir="main",
    plugins={"root": "main/bot/plugins"},
    bot_token=Var.BOT_TOKEN,
    sleep_threshold=Var.SLEEP_THRESHOLD,
    workers=Var.WORKERS,
)

multi_clients = {}
work_loads = {}
