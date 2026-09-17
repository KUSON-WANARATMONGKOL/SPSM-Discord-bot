"""Suggestion & feedback system: !suggest, !feedback, !mysuggest, !suggestion ...

Suggestions and feedback are posted to auto-created #suggestions / #feedback
channels and tracked in SQLite (see db.py). Reaction votes on suggestion
messages (✅ approve / ❌ reject / 💭 considering) are mirrored into the
database live via on_raw_reaction_add/remove.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

import discord
from discord.ext import commands

import db
from common import (
    COLOR_INFO,
    COLOR_SUCCESS,
    COLOR_WARNING,
    get_or_create_text_channel,
    log_action,
    make_embed,
    strip_quotes,
)

log = logging.getLogger("school_bot.feedback")

SUGGESTIONS_CHANNEL = "suggestions"
FEEDBACK_CHANNEL = "feedback"
EDIT_WINDOW = timedelta(minutes=5)
MIN_LEN, MAX_LEN = 5, 500

VALID_STATUSES = ("pending", "approved", "rejected", "considering")
STATUS_EMOJI = {"approved": "✅", "rejected": "❌", "considering": "💭", "pending": "💭"}
STATUS_LABEL_TH = {
    "pending": "รอพิจารณา",
    "approved": "อนุมัติแล้ว",
    "rejected": "ปฏิเสธแล้ว",
    "considering": "กำลังพิจารณา",
}


class FeedbackCog(commands.Cog, name="Feedback"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # !suggest ...
    # ------------------------------------------------------------------

    @commands.group(name="suggest", invoke_without_command=True)
    @commands.cooldown(1, 30, commands.BucketType.user)
    async def suggest_group(self, ctx: commands.Context, *, text: str) -> None:
        """Submit a suggestion for the server."""
        text = strip_quotes(text).strip()
        if not (MIN_LEN <= len(text) <= MAX_LEN):
            await ctx.send(f"❌ ข้อเสนอแนะต้องมีความยาว {MIN_LEN}-{MAX_LEN} ตัวอักษร")
            return

        channel = await get_or_create_text_channel(ctx.guild, SUGGESTIONS_CHANNEL)
        if channel is None:
            await ctx.send("❌ ไม่สามารถสร้างช่อง #suggestions ได้ โปรดตรวจสอบสิทธิ์ของบอท")
            return

        try:
            suggestion_id = db.create_suggestion(ctx.guild.id, ctx.author.id, text)
        except sqlite3.Error:
            log.exception("Failed to create suggestion")
            await ctx.send("❌ เกิดข้อผิดพลาดในการบันทึกข้อมูล โปรดลองใหม่อีกครั้ง")
            return

        embed = make_embed(title="💡 ข้อเสนอแนะใหม่", description=text, color=COLOR_INFO)
        embed.add_field(name="ผู้เสนอ", value=ctx.author.mention, inline=True)
        embed.add_field(name="สถานะ", value=STATUS_LABEL_TH["pending"], inline=True)
        embed.set_footer(text=f"Suggestion ID: {suggestion_id}")
        try:
            message = await channel.send(embed=embed)
            for emoji in ("✅", "❌", "💭"):
                await message.add_reaction(emoji)
            db.set_suggestion_message(suggestion_id, channel.id, message.id)
        except discord.Forbidden:
            log.warning("Missing permission to post suggestion in #%s", channel.name)

        await ctx.send(f"✅ ส่งข้อเสนอแนะเรียบร้อย! หมายเลขติดตาม: **#{suggestion_id}**")
        try:
            dm = make_embed(title="✅ ได้รับข้อเสนอแนะของคุณแล้ว", description=text, color=COLOR_SUCCESS)
            dm.set_footer(text=f"Suggestion ID: {suggestion_id} — ใช้ !mysuggest เพื่อติดตามสถานะ")
            await ctx.author.send(embed=dm)
        except discord.Forbidden:
            pass

        log_action(ctx.guild, f"{ctx.author} submitted suggestion #{suggestion_id}")

    @suggest_group.command(name="edit")
    async def suggest_edit(self, ctx: commands.Context, suggestion_id: int, *, new_text: str) -> None:
        """Edit your own suggestion within 5 minutes of posting it."""
        row = db.get_suggestion(suggestion_id, ctx.guild.id)
        if row is None:
            await ctx.send(f"❌ ไม่พบข้อเสนอแนะ ID {suggestion_id}")
            return
        if row["user_id"] != ctx.author.id:
            await ctx.send("❌ คุณแก้ไขได้เฉพาะข้อเสนอแนะของตัวเองเท่านั้น")
            return

        created = datetime.fromisoformat(row["timestamp"])
        if datetime.now(created.tzinfo) - created > EDIT_WINDOW:
            await ctx.send("❌ หมดเวลาแก้ไขแล้ว (แก้ไขได้ภายใน 5 นาทีหลังส่ง)")
            return

        new_text = strip_quotes(new_text).strip()
        if not (MIN_LEN <= len(new_text) <= MAX_LEN):
            await ctx.send(f"❌ ข้อเสนอแนะต้องมีความยาว {MIN_LEN}-{MAX_LEN} ตัวอักษร")
            return

        db.edit_suggestion_text(suggestion_id, new_text)
        await self._resync_suggestion_embed(ctx.guild, row, description=new_text)

        await ctx.send(f"✅ แก้ไขข้อเสนอแนะ #{suggestion_id} เรียบร้อยแล้ว")
        log_action(ctx.guild, f"{ctx.author} edited suggestion #{suggestion_id}")

    # ------------------------------------------------------------------
    # !feedback
    # ------------------------------------------------------------------

    @commands.command(name="feedback")
    @commands.cooldown(1, 30, commands.BucketType.user)
    async def feedback_cmd(self, ctx: commands.Context, *, text: str) -> None:
        """Submit feedback (praise, bug reports, etc.)."""
        text = strip_quotes(text).strip()
        if not (MIN_LEN <= len(text) <= MAX_LEN):
            await ctx.send(f"❌ ข้อความต้องมีความยาว {MIN_LEN}-{MAX_LEN} ตัวอักษร")
            return

        channel = await get_or_create_text_channel(ctx.guild, FEEDBACK_CHANNEL)
        if channel is None:
            await ctx.send("❌ ไม่สามารถสร้างช่อง #feedback ได้ โปรดตรวจสอบสิทธิ์ของบอท")
            return

        try:
            feedback_id = db.create_feedback(ctx.guild.id, ctx.author.id, text)
        except sqlite3.Error:
            log.exception("Failed to create feedback")
            await ctx.send("❌ เกิดข้อผิดพลาดในการบันทึกข้อมูล โปรดลองใหม่อีกครั้ง")
            return

        embed = make_embed(title="📝 ความคิดเห็นใหม่", description=text, color=COLOR_INFO)
        embed.add_field(name="ผู้ส่ง", value=ctx.author.mention, inline=True)
        embed.set_footer(text=f"Feedback ID: {feedback_id}")
        try:
            message = await channel.send(embed=embed)
            db.set_feedback_message(feedback_id, channel.id, message.id)
        except discord.Forbidden:
            log.warning("Missing permission to post feedback in #%s", channel.name)

        await ctx.send("✅ ส่งความคิดเห็นเรียบร้อย! ขอบคุณสำหรับความคิดเห็นของคุณ 🙏")
        log_action(ctx.guild, f"{ctx.author} submitted feedback #{feedback_id}")

    # ------------------------------------------------------------------
    # !mysuggest
    # ------------------------------------------------------------------

    @commands.command(name="mysuggest")
    async def mysuggest_cmd(self, ctx: commands.Context, member: Optional[discord.Member] = None) -> None:
        """Show your own suggestion history, or another user's (admin only)."""
        if member is not None and member.id != ctx.author.id and not ctx.author.guild_permissions.administrator:
            await ctx.send("❌ คุณไม่มีสิทธิดูข้อเสนอแนะของผู้อื่น")
            return
        target = member or ctx.author

        rows = db.list_user_suggestions(ctx.guild.id, target.id)
        if not rows:
            await ctx.send(
                embed=make_embed(title="💡 ข้อเสนอแนะ", description="ยังไม่มีข้อเสนอแนะ", color=COLOR_INFO)
            )
            return

        lines = [
            f"**#{row['id']}** [{STATUS_LABEL_TH.get(row['status'], row['status'])}] — {row['text'][:80]}"
            for row in rows[:15]
        ]
        embed = make_embed(title=f"💡 ข้อเสนอแนะของ {target.display_name}", description="\n".join(lines), color=COLOR_INFO)
        if len(rows) > 15:
            embed.set_footer(text=f"และอีก {len(rows) - 15} รายการ")
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------
    # !suggestion list / status / delete (admin)
    # ------------------------------------------------------------------

    @commands.group(name="suggestion", invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def suggestion_group(self, ctx: commands.Context) -> None:
        await ctx.send(
            "❌ ใช้ `!suggestion list [status]`, `!suggestion status <id> <status>`, หรือ `!suggestion delete <id>`"
        )

    @suggestion_group.command(name="list")
    @commands.has_permissions(administrator=True)
    async def suggestion_list(self, ctx: commands.Context, status: Optional[str] = None) -> None:
        """List all suggestions, optionally filtered by status (Admin only)."""
        if status is not None:
            status = status.lower()
            if status not in VALID_STATUSES:
                await ctx.send(f"❌ สถานะต้องเป็นหนึ่งใน: {', '.join(VALID_STATUSES)}")
                return

        rows = db.list_suggestions(ctx.guild.id, status)
        if not rows:
            await ctx.send(embed=make_embed(title="📋 รายการข้อเสนอแนะ", description="ไม่มีข้อมูล", color=COLOR_INFO))
            return

        lines = []
        for row in rows[:20]:
            member = ctx.guild.get_member(row["user_id"])
            name = member.display_name if member else f"Unknown ({row['user_id']})"
            lines.append(f"**#{row['id']}** [{STATUS_LABEL_TH.get(row['status'], row['status'])}] {name}: {row['text'][:60]}")

        title = "📋 รายการข้อเสนอแนะ" + (f" ({STATUS_LABEL_TH[status]})" if status else "")
        embed = make_embed(title=title, description="\n".join(lines), color=COLOR_INFO)
        if len(rows) > 20:
            embed.set_footer(text=f"และอีก {len(rows) - 20} รายการ")
        await ctx.send(embed=embed)

    @suggestion_group.command(name="status")
    @commands.has_permissions(administrator=True)
    async def suggestion_status(self, ctx: commands.Context, suggestion_id: int, new_status: str) -> None:
        """Update a suggestion's status and notify its author (Admin only)."""
        new_status = new_status.lower()
        if new_status not in VALID_STATUSES:
            await ctx.send(f"❌ สถานะต้องเป็นหนึ่งใน: {', '.join(VALID_STATUSES)}")
            return

        row = db.get_suggestion(suggestion_id, ctx.guild.id)
        if row is None:
            await ctx.send(f"❌ ไม่พบข้อเสนอแนะ ID {suggestion_id}")
            return

        db.update_suggestion_status(suggestion_id, new_status)
        await self._resync_suggestion_embed(ctx.guild, row, status=new_status)

        await ctx.send(f"✅ อัปเดตสถานะข้อเสนอแนะ #{suggestion_id} เป็น **{STATUS_LABEL_TH[new_status]}** แล้ว")

        author = ctx.guild.get_member(row["user_id"])
        if author:
            try:
                dm = make_embed(
                    title="🔔 สถานะข้อเสนอแนะของคุณมีการอัปเดต",
                    description=f"ข้อเสนอแนะ #{suggestion_id}: {row['text'][:200]}\n\nสถานะใหม่: **{STATUS_LABEL_TH[new_status]}**",
                    color=COLOR_INFO,
                )
                await author.send(embed=dm)
            except discord.Forbidden:
                pass

        log_action(ctx.guild, f"{ctx.author} set suggestion #{suggestion_id} status to {new_status}")

    @suggestion_group.command(name="delete")
    @commands.has_permissions(administrator=True)
    async def suggestion_delete(self, ctx: commands.Context, suggestion_id: int) -> None:
        """Delete a suggestion (Admin only)."""
        row = db.get_suggestion(suggestion_id, ctx.guild.id)
        if row is None:
            await ctx.send(f"❌ ไม่พบข้อเสนอแนะ ID {suggestion_id}")
            return

        db.delete_suggestion(suggestion_id, ctx.guild.id)

        if row["channel_id"] and row["message_id"]:
            channel = ctx.guild.get_channel(row["channel_id"])
            if channel is not None:
                try:
                    message = await channel.fetch_message(row["message_id"])
                    await message.delete()
                except (discord.NotFound, discord.Forbidden):
                    pass

        await ctx.send(f"🗑️ ลบข้อเสนอแนะ #{suggestion_id} เรียบร้อยแล้ว")
        log_action(ctx.guild, f"{ctx.author} deleted suggestion #{suggestion_id}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _resync_suggestion_embed(
        guild: discord.Guild,
        row: sqlite3.Row,
        *,
        description: Optional[str] = None,
        status: Optional[str] = None,
    ) -> None:
        """Update the posted suggestion embed/reactions after an edit or status change."""
        if not (row["channel_id"] and row["message_id"]):
            return
        channel = guild.get_channel(row["channel_id"])
        if channel is None:
            return

        try:
            message = await channel.fetch_message(row["message_id"])
        except (discord.NotFound, discord.Forbidden):
            return

        embed = message.embeds[0] if message.embeds else make_embed(title="💡 ข้อเสนอแนะ")
        if description is not None:
            embed.description = description
        if status is not None:
            for i, field in enumerate(embed.fields):
                if field.name == "สถานะ":
                    embed.set_field_at(i, name="สถานะ", value=STATUS_LABEL_TH[status], inline=True)
                    break
            else:
                embed.add_field(name="สถานะ", value=STATUS_LABEL_TH[status], inline=True)

        try:
            await message.edit(embed=embed)
            if status is not None:
                await message.clear_reactions()
                await message.add_reaction(STATUS_EMOJI[status])
        except discord.Forbidden:
            pass

    # ------------------------------------------------------------------
    # Live vote tracking
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        await self._sync_votes(payload)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        await self._sync_votes(payload)

    async def _sync_votes(self, payload: discord.RawReactionActionEvent) -> None:
        row = db.get_suggestion_by_message(payload.message_id)
        if row is None or payload.guild_id is None:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        channel = guild.get_channel(row["channel_id"])
        if channel is None:
            return

        try:
            message = await channel.fetch_message(payload.message_id)
        except (discord.NotFound, discord.Forbidden):
            return

        def count_for(emoji: str) -> int:
            reaction = discord.utils.get(message.reactions, emoji=emoji)
            return max(reaction.count - 1, 0) if reaction else 0

        db.update_suggestion_votes(row["id"], count_for("✅"), count_for("❌"), count_for("💭"))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(FeedbackCog(bot))
