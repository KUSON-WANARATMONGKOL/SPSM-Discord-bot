"""Social media announcements: !social_channel set/get/reset, !social/!embed.

!social no longer takes a URL or scrapes Instagram/Facebook — that approach
was dropped after confirming Instagram serves zero usable metadata to any
non-browser request (see git history / prior conversation for the evidence).
Instead it's a manual composer: the command REQUIRES an image attached to
the !social message itself (Discord modals can't have a file-upload field,
so this is the only way to attach a picture — there's no "paste a link"
fallback), then a button opens a Discord modal (title + description text
fields), and submitting it posts the resulting embed to the configured
channel using that attached image.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from typing import Optional

import discord
from discord.ext import commands

import db
from common import (
    COLOR_INFO,
    log_action,
    make_embed,
    schedule_reply_cleanup,
    track_replies_for_cleanup,
)

log = logging.getLogger("school_bot.social")

TITLE_MAX_LEN = 100
DESCRIPTION_MAX_LEN = 2000
BUTTON_TIMEOUT_SECONDS = 120
OVERALL_WAIT_CEILING_SECONDS = 300  # safety net if the user opens the modal and abandons it
CLEANUP_DELAY_SECONDS = 8


def build_social_announcement_embed(
    title: str, description: str, image_url: Optional[str], author: discord.abc.User
) -> discord.Embed:
    embed = make_embed(title=f"📢 {title}", description=description, color=COLOR_INFO)
    if image_url:
        embed.set_image(url=image_url)
    avatar_url = author.display_avatar.url if getattr(author, "display_avatar", None) else None
    if avatar_url:
        embed.set_footer(text=f"ประกาศโดย {author.display_name}", icon_url=avatar_url)
    else:
        embed.set_footer(text=f"ประกาศโดย {author.display_name}")
    return embed


class SocialAnnouncementModal(discord.ui.Modal):
    def __init__(self, channel: discord.TextChannel, attached_image_url: str, done: asyncio.Event) -> None:
        super().__init__(title="สร้างประกาศโซเชียลมีเดีย")
        self.channel = channel
        self.attached_image_url = attached_image_url
        self.done = done

        self.title_input = discord.ui.TextInput(
            label="หัวข้อประกาศ",
            style=discord.TextStyle.short,
            max_length=TITLE_MAX_LEN,
            required=True,
            placeholder="เช่น กิจกรรมวันกีฬาสี",
        )
        self.add_item(self.title_input)

        self.description_input = discord.ui.TextInput(
            label="รายละเอียด",
            style=discord.TextStyle.paragraph,
            max_length=DESCRIPTION_MAX_LEN,
            required=True,
            placeholder="พิมพ์เนื้อหาประกาศที่นี่...",
        )
        self.add_item(self.description_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            title = self.title_input.value.strip()
            description = self.description_input.value.strip()

            embed = build_social_announcement_embed(title, description, self.attached_image_url, interaction.user)

            try:
                message = await self.channel.send(embed=embed)
            except discord.Forbidden:
                await interaction.response.send_message("❌ บอทไม่มีสิทธิ์โพสต์ในช่องที่ตั้งค่าไว้", ephemeral=True)
                return

            try:
                db.create_social_announcement(
                    guild_id=interaction.guild_id,
                    user_id=interaction.user.id,
                    title=title,
                    description=description,
                    image_url=self.attached_image_url,
                    discord_message_id=message.id,
                    channel_id=self.channel.id,
                )
            except sqlite3.Error:
                log.exception("Failed to record social announcement")

            await interaction.response.send_message(f"✅ โพสต์ประกาศไปยัง {self.channel.mention} แล้ว", ephemeral=True)
            log_action(interaction.guild, f"{interaction.user} posted a social announcement to #{self.channel.name}: {title}")
        finally:
            self.done.set()

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception("Error in social announcement modal: %s", error)
        try:
            await interaction.response.send_message("❌ เกิดข้อผิดพลาด โปรดลองใหม่อีกครั้ง", ephemeral=True)
        except discord.InteractionResponded:
            pass
        self.done.set()


class SocialAnnouncementStartView(discord.ui.View):
    def __init__(self, author_id: int, channel: discord.TextChannel, image_url: str) -> None:
        super().__init__(timeout=BUTTON_TIMEOUT_SECONDS)
        self.author_id = author_id
        self.channel = channel
        self.image_url = image_url
        self.done = asyncio.Event()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ เฉพาะผู้ใช้คำสั่งเท่านั้นที่กรอกแบบฟอร์มนี้ได้", ephemeral=True)
            return False
        return True

    async def on_timeout(self) -> None:
        self.done.set()

    @discord.ui.button(label="กรอกรายละเอียดประกาศ", style=discord.ButtonStyle.primary, emoji="📝")
    async def open_form(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(SocialAnnouncementModal(self.channel, self.image_url, self.done))
        self.stop()
        try:
            await interaction.message.edit(view=None)
        except (discord.NotFound, discord.Forbidden):
            pass


class SocialMediaCog(commands.Cog, name="SocialMedia"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_before_invoke(self, ctx: commands.Context) -> None:
        track_replies_for_cleanup(ctx)

    async def cog_after_invoke(self, ctx: commands.Context) -> None:
        await schedule_reply_cleanup(ctx, CLEANUP_DELAY_SECONDS)

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
        """Set the channel where !social posts announcements (Admin only)."""
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
    async def social_cmd(self, ctx: commands.Context) -> None:
        """Attach an image to this command to open a form and post an announcement."""
        channel_id = db.get_social_channel(ctx.guild.id)
        if channel_id is None:
            await ctx.send("❌ ยังไม่ได้ตั้งค่าช่องประกาศ\nใช้: `!social_channel set #channel_name`")
            return
        channel = ctx.guild.get_channel(channel_id)
        if channel is None:
            await ctx.send("❌ ไม่พบช่องที่ตั้งค่าไว้ โปรดตั้งค่าใหม่ด้วย `!social_channel set #channel_name`")
            return

        image_url = None
        if ctx.message.attachments:
            first = ctx.message.attachments[0]
            if (first.content_type or "").startswith("image/"):
                image_url = first.url

        if image_url is None:
            await ctx.send("❌ กรุณาแนบรูปภาพมากับข้อความนี้ด้วย เช่น พิมพ์ `!social` แล้วแนบไฟล์รูปภาพ")
            return

        view = SocialAnnouncementStartView(ctx.author.id, channel, image_url)
        await ctx.send("แนบรูปภาพแล้ว ✅ กดปุ่มด้านล่างเพื่อกรอกหัวข้อและรายละเอียด", view=view)

        try:
            await asyncio.wait_for(view.done.wait(), timeout=OVERALL_WAIT_CEILING_SECONDS)
        except asyncio.TimeoutError:
            pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SocialMediaCog(bot))
