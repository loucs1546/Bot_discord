# utils/logging.py
import asyncio
from typing import Optional, Union
import discord
import core_config as config

async def _send_to_channel(bot: discord.Client, channel_id: int, content: Union[str, discord.Embed], *, mention: Optional[str]=None, view: Optional[discord.ui.View]=None) -> bool:
    try:
        channel = bot.get_channel(channel_id)
        if channel is None:
            channel = await bot.fetch_channel(channel_id)
        if isinstance(content, discord.Embed):
            await channel.send(content=mention or None, embed=content, view=view)
        else:
            text = f"{mention or ''}{content}"
            await channel.send(text, view=view)
        return True
    except Exception:
        return False

async def send_log_to(bot: discord.Client, log_type: str, content: Union[str, discord.Embed], *, mention: Optional[str]=None, view: Optional[discord.ui.View]=None) -> bool:
    channel_id = config.CONFIG.get("logs", {}).get(log_type)
    if not channel_id:
        return False
    return await _send_to_channel(bot, channel_id, content, mention=mention, view=view)

def send_log(bot: discord.Client, log_type: str, content: Union[str, discord.Embed], *, mention: Optional[str]=None):
    return asyncio.create_task(send_log_to(bot, log_type, content, mention=mention))