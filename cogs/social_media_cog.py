"""Social media -> Discord embed announcements.

!social_channel set/get/reset (Admin) configures where converted posts go.
!social <url> (alias: !embed) fetches best-effort metadata for a public
Instagram/Facebook post and, after the author confirms a preview, posts it
as an embed to that channel.

See utils/social_scraper.py for the data-source limitations (no like counts,
Instagram frequently blocks extraction entirely) — this cog just renders
whatever comes back and degrades gracefully when it's incomplete.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Optional

import aiohttp
import discord
from discord.ext import commands

import db
from common import COLOR_DANGER, COLOR_INFO, COLOR_SUCCESS, log_action, make_embed
from utils.social_scraper import ScrapingError, SocialPost, classify_url, extract_post

log = logging.getLogger("school_bot.social")

PLATFORM_META = {
    "instagram": {"label": "📷 Instagram", "color": discord.Color.from_rgb(255, 102, 178)},
    "facebook": {"label": "👍 Facebook", "color": discord.Color.from_rgb(24, 119, 242)},
}

CONFIRM_TIMEOUT_SECONDS = 90


def build_social_embed(post: SocialPost) -> discord.Embed:
    meta = PLATFORM_META.get(post.platform, {"label": post.platform, "color": discord.Color.blurple()})
    author_display = post.author_name or "ไม่ทราบผู้เขียน"

    embed = discord.Embed(
        title=f"{meta['label']} Post",
        description=f"โพสต์โดย **{author_display}**",
        color=meta["color"],
        timestamp=post.post_date or datetime.now(timezone.utc),
    )
    if post.author_avatar:
        embed.set_thumbnail(url=post.author_avatar)
    if post.image_url:
        embed.set_image(url=post.image_url)

    caption = (post.caption or "ไม่มีคำบรรยาย")[:1024]
    embed.add_field(name="📝 คำบรรยาย", value=caption, inline=False)

    if post.hashtags:
        embed.add_field(name="🏷️ แฮชแท็ก", value=" ".join(post.hashtags)[:1024], inline=False)

    likes_display = f"{post.likes_count:,}" if post.likes_count is not None else "ไม่มีข้อมูล"
    embed.add_field(name="❤️ ยอดไลก์", value=likes_display, inline=True)
    embed.add_field(name="🔗 โพสต์ต้นฉบับ", value=f"[ดูโพสต์เดิม]({post.original_url})", inline=True)

    if post.author_avatar:
        embed.set_footer(text=f"{meta['label']} | {author_display}", icon_url=post.author_avatar)
    else:
        embed.set_footer(text=f"{meta['label']} | {author_display}")
    return embed


class SocialConfirmView(discord.ui.View):
    def __init__(self, author_id: int) -> None:
        super().__init__(timeout=CONFIRM_TIMEOUT_SECONDS)
        self.author_id = author_id
        self.value: Optional[bool] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ เฉพาะผู้ใช้คำสั่งเท่านั้นที่กดยืนยันได้", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="โพสต์", style=discord.ButtonStyle.success, emoji="✅")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.value = True
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="ยกเลิก", style=discord.ButtonStyle.danger, emoji="❌")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.value = False
        await interaction.response.defer()
        self.stop()


class SocialMediaCog(commands.Cog, name="SocialMedia"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.session: Optional[aiohttp.ClientSession] = None

    async def cog_load(self) -> None:
        self.session = aiohttp.ClientSession()

    async def cog_unload(self) -> None:
        if self.session is not None:
            await self.session.close()

    # ------------------------------------------------------------------
    # !social_channel ...
    # ------------------------------------------------------------------

    @commands.group(name="social_channel", invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def social_channel_group(self, ctx: commands.Context) -> None:
        await ctx.send("❌ ใช้ `!social_channel set #channel`, `!social_channel get`, หรือ `!social_channel reset`")

    @social_channel_group.command(name="set")
    @commands.has_permissions(administrator=True)
    async def social_channel_set(self, ctx: commands.Context, channel: discord.TextChannel) -> None:
        """Set the channel where !social posts are published (Admin only)."""
        db.set_social_channel(ctx.guild.id, channel.id)
        await ctx.send(f"✅ ตั้งค่าช่องโซเชียลมีเดียเป็น {channel.mention} แล้ว")
        log_action(ctx.guild, f"{ctx.author} set social media channel to #{channel.name}")

    @social_channel_group.command(name="get")
    @commands.has_permissions(administrator=True)
    async def social_channel_get(self, ctx: commands.Context) -> None:
        """Show the currently configured social media channel (Admin only)."""
        channel_id = db.get_social_channel(ctx.guild.id)
        if channel_id is None:
            await ctx.send("❌ ยังไม่ได้ตั้งค่าช่องประกาศโซเชียลมีเดีย ใช้ `!social_channel set #channel`")
            return
        channel = ctx.guild.get_channel(channel_id)
        if channel is None:
            await ctx.send(f"⚠️ ตั้งค่าไว้ที่ช่อง ID `{channel_id}` แต่ไม่พบช่องนี้แล้ว (อาจถูกลบไปแล้ว)")
            return
        await ctx.send(f"📌 ช่องโซเชียลมีเดียปัจจุบัน: {channel.mention} (ID: {channel_id})")

    @social_channel_group.command(name="reset")
    @commands.has_permissions(administrator=True)
    async def social_channel_reset(self, ctx: commands.Context) -> None:
        """Clear the social media channel setting (Admin only)."""
        db.clear_social_channel(ctx.guild.id)
        await ctx.send("✅ ล้างค่าช่องโซเชียลมีเดียแล้ว")
        log_action(ctx.guild, f"{ctx.author} reset the social media channel")

    # ------------------------------------------------------------------
    # !social / !embed
    # ------------------------------------------------------------------

    @commands.command(name="social", aliases=["embed"])
    @commands.cooldown(1, 30, commands.BucketType.user)
    async def social_cmd(self, ctx: commands.Context, url: str) -> None:
        """Convert an Instagram/Facebook post link into an announcement embed."""
        platform = classify_url(url)
        if platform is None:
            await ctx.send("❌ URL ไม่ถูกต้อง รองรับเฉพาะลิงก์โพสต์ Instagram หรือ Facebook เท่านั้น")
            return

        channel_id = db.get_social_channel(ctx.guild.id)
        if channel_id is None:
            await ctx.send("❌ ยังไม่ได้ตั้งค่าช่องประกาศ\nใช้: `!social_channel set #channel_name`")
            return
        channel = ctx.guild.get_channel(channel_id)
        if channel is None:
            await ctx.send("❌ ไม่พบช่องที่ตั้งค่าไว้ โปรดตั้งค่าใหม่ด้วย `!social_channel set #channel_name`")
            return

        previous = db.find_social_post_by_url(ctx.guild.id, url)

        async with ctx.typing():
            try:
                post = await extract_post(self.session, url, platform)
            except ScrapingError:
                await ctx.send("❌ ไม่สามารถดึงข้อมูลโพสต์ได้ โปรดตรวจสอบว่า URL ถูกต้องและโพสต์เป็นสาธารณะ")
                return
            except Exception:
                log.exception("Unexpected error extracting social post from %s", url)
                await ctx.send("❌ เกิดข้อผิดพลาดในระบบ โปรดลองใหม่อีกครั้ง")
                return

        embed = build_social_embed(post)
        view = SocialConfirmView(author_id=ctx.author.id)
        preview_note = "ตรวจสอบตัวอย่างด้านล่าง แล้วกดยืนยันเพื่อโพสต์"
        if previous is not None:
            preview_note = f"⚠️ ลิงก์นี้เคยถูกโพสต์ไปแล้ว (#{previous['id']})\n{preview_note}"

        preview_message = await ctx.send(content=preview_note, embed=embed, view=view)

        await view.wait()

        if view.value is None:
            await preview_message.edit(content="⏳ หมดเวลา ไม่ได้โพสต์", embed=embed, view=None)
            return
        if not view.value:
            await preview_message.edit(content="❌ ยกเลิกแล้ว", embed=embed, view=None)
            return

        try:
            posted_message = await channel.send(embed=embed)
        except discord.Forbidden:
            await preview_message.edit(content="❌ บอทไม่มีสิทธิ์โพสต์ในช่องนั้น", embed=embed, view=None)
            return

        try:
            db.create_social_post(
                guild_id=ctx.guild.id,
                user_id=ctx.author.id,
                original_url=url,
                platform=platform,
                author_name=post.author_name,
                caption=post.caption,
                image_url=post.image_url,
                likes_count=post.likes_count,
                discord_message_id=posted_message.id,
                channel_id=channel.id,
                extraction_method=post.extraction_method,
            )
        except sqlite3.Error:
            log.exception("Failed to record social post in the database")

        await preview_message.edit(content=f"✅ โพสต์ไปยัง {channel.mention} แล้ว", embed=embed, view=None)
        log_action(ctx.guild, f"{ctx.author} posted a {platform} link to #{channel.name}: {url}")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SocialMediaCog(bot))
