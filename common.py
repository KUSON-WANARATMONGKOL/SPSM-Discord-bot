"""Shared constants and helpers used by bot.py and the cogs.

Kept separate from bot.py so cogs can import these without re-importing
(and re-running) the bot.py module itself.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import discord

PREFIX = "!"

COLOR_SUCCESS = discord.Color.green()
COLOR_INFO = discord.Color.blue()
COLOR_WARNING = discord.Color.orange()
COLOR_DANGER = discord.Color.red()
COLOR_NEUTRAL = discord.Color.blurple()

log = logging.getLogger("school_bot")


def make_embed(title: str, description: str = "", color: discord.Color = COLOR_NEUTRAL) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=color, timestamp=datetime.now(timezone.utc))


def log_action(guild: Optional[discord.Guild], message: str) -> None:
    guild_name = guild.name if guild else "DM"
    log.info("[%s] %s", guild_name, message)


def can_act_on(actor: discord.Member, target: discord.Member) -> bool:
    """Return True if actor is allowed to moderate target (higher role, not self, not owner)."""
    if actor.id == target.id:
        return False
    if target.id == target.guild.owner_id:
        return False
    return actor.top_role > target.top_role or actor.id == actor.guild.owner_id


async def get_or_create_role(guild: discord.Guild, name: str, **kwargs) -> discord.Role:
    role = discord.utils.get(guild.roles, name=name)
    if role is None:
        role = await guild.create_role(name=name, reason=f"Auto-created '{name}' role", **kwargs)
        log_action(guild, f"Created missing role '{name}'")
    return role


async def get_or_create_text_channel(guild: discord.Guild, name: str, **kwargs) -> Optional[discord.TextChannel]:
    channel = discord.utils.get(guild.text_channels, name=name)
    if channel is None:
        try:
            channel = await guild.create_text_channel(name, reason=f"Auto-created '#{name}' channel", **kwargs)
            log_action(guild, f"Created missing channel '#{name}'")
        except discord.Forbidden:
            return None
    return channel


def strip_quotes(text: str) -> str:
    """Strip a single matching pair of surrounding double quotes, if present."""
    text = text.strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        return text[1:-1]
    return text
