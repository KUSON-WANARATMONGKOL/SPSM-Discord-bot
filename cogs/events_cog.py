"""Event management: !event create/list/details/edit/join/leave/delete + channel config.

Events are stored in SQLite (see db.py) so they survive bot restarts. All
dates/times are Asia/Bangkok local time. Each event is posted as a single
"announcement" embed with ✅ (join) / ❌ (leave) reactions; that embed is
edited live whenever the attendee count or description changes, so anyone
looking at it always sees current data. A background task DMs attendees
~24h before their event starts.

Un-reacting ✅ does NOT leave the event — by design there are two distinct
buttons (✅ join, ❌ leave), not one toggle, so an accidental un-react while
scrolling can't silently drop someone from the roster. `!event leave` or
clicking ❌ are the only ways to leave.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from typing import Optional

import discord
from discord.ext import commands, tasks

import db
from common import (
    COLOR_DANGER,
    COLOR_INFO,
    COLOR_SUCCESS,
    COLOR_WARNING,
    log_action,
    make_embed,
    schedule_reply_cleanup,
    strip_quotes,
    track_replies_for_cleanup,
)
from date_utils import combine, format_remaining, now_bangkok, parse_event_date, parse_event_time

log = logging.getLogger("school_bot.events")

NAME_MAX_LEN = 100
DESCRIPTION_MAX_LEN = 1000
EVENT_LIST_LIMIT = 10
JOIN_EMOJI = "✅"
LEAVE_EMOJI = "❌"
CLEANUP_DELAY_SECONDS = 8


def _event_datetime(row: sqlite3.Row) -> datetime:
    hour, minute = (int(part) for part in row["event_time"].split(":"))
    return combine(datetime.strptime(row["event_date"], "%Y-%m-%d").date(), hour, minute)


def build_event_embed(event_row: sqlite3.Row, attendee_count: int, guild: discord.Guild) -> discord.Embed:
    """The single source of truth for what an event's announcement embed looks like."""
    event_dt = _event_datetime(event_row)
    creator = guild.get_member(event_row["created_by_id"])
    creator_name = creator.display_name if creator else "ไม่ทราบ"

    embed = make_embed(
        title=f"📅 {event_row['name']}",
        description=event_row["description"] or "ไม่มีรายละเอียดเพิ่มเติม",
        color=COLOR_INFO,
    )
    if event_row["image_url"]:
        embed.set_image(url=event_row["image_url"])

    date_display = datetime.strptime(event_row["event_date"], "%Y-%m-%d").strftime("%d/%m/%Y")
    embed.add_field(name="🗓️ วันเวลา", value=f"{date_display} เวลา {event_row['event_time']} น.", inline=True)
    embed.add_field(name="⏳ เหลือเวลา", value=format_remaining(event_dt), inline=True)
    embed.add_field(name="👥 ผู้เข้าร่วม", value=f"{attendee_count} คน", inline=True)

    footer_text = f"Event ID: #{event_row['id']} | สร้างโดย {creator_name}"
    if creator and creator.display_avatar:
        embed.set_footer(text=footer_text, icon_url=creator.display_avatar.url)
    else:
        embed.set_footer(text=footer_text)
    return embed


class EventsCog(commands.Cog, name="Events"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.reminder_check.start()

    def cog_unload(self) -> None:
        self.reminder_check.cancel()

    async def cog_before_invoke(self, ctx: commands.Context) -> None:
        track_replies_for_cleanup(ctx)

    async def cog_after_invoke(self, ctx: commands.Context) -> None:
        # Keeps the channel tidy: the !event command and every reply it sent
        # here vanish shortly after. The announcement embed itself is posted
        # via channel.send() to the configured event channel, not ctx.send(),
        # so it's never part of this cleanup regardless of which channel that is.
        await schedule_reply_cleanup(ctx, CLEANUP_DELAY_SECONDS)

    # ------------------------------------------------------------------
    # Shared helpers (used by both text commands and reactions)
    # ------------------------------------------------------------------

    async def _refresh_announcement(self, guild: discord.Guild, event_id: int) -> None:
        """Re-fetch the event + attendee count and edit the live announcement embed."""
        row = db.get_event(event_id, guild.id)
        if row is None or not row["announcement_channel_id"] or not row["announcement_message_id"]:
            return
        channel = guild.get_channel(row["announcement_channel_id"])
        if channel is None:
            return
        try:
            message = await channel.fetch_message(row["announcement_message_id"])
        except (discord.NotFound, discord.Forbidden):
            return
        embed = build_event_embed(row, db.count_attendees(event_id), guild)
        try:
            await message.edit(embed=embed)
        except discord.Forbidden:
            pass

    async def _join_event(self, guild: discord.Guild, event_row: sqlite3.Row, member: discord.Member) -> bool:
        """Register `member` for the event. Returns False if already joined."""
        try:
            added = db.add_attendee(event_row["id"], member.id)
        except sqlite3.Error:
            log.exception("Failed to add attendee")
            return False
        if not added:
            return False

        await self._refresh_announcement(guild, event_row["id"])

        try:
            dm = make_embed(
                title="✅ ยืนยันการลงทะเบียน",
                description=f"คุณได้ลงทะเบียนเข้าร่วมกิจกรรม **{event_row['name']}** เรียบร้อยแล้ว",
                color=COLOR_SUCCESS,
            )
            dm.set_footer(text=f"Event ID: #{event_row['id']}")
            await member.send(embed=dm)
        except discord.Forbidden:
            pass

        creator = guild.get_member(event_row["created_by_id"])
        if creator and creator.id != member.id:
            try:
                notify = make_embed(
                    title="👥 มีผู้เข้าร่วมกิจกรรมใหม่",
                    description=f"{member.mention} ได้ลงทะเบียนเข้าร่วม **{event_row['name']}**",
                    color=COLOR_INFO,
                )
                notify.set_footer(text=f"Event ID: #{event_row['id']}")
                await creator.send(embed=notify)
            except discord.Forbidden:
                pass

        log_action(guild, f"{member} joined event #{event_row['id']}")
        return True

    async def _leave_event(self, guild: discord.Guild, event_row: sqlite3.Row, member: discord.Member) -> bool:
        """Remove `member` from the event. Returns False if they weren't registered."""
        removed = db.remove_attendee(event_row["id"], member.id)
        if not removed:
            return False

        await self._refresh_announcement(guild, event_row["id"])

        try:
            dm = make_embed(
                title="✅ ยกเลิกการลงทะเบียนแล้ว",
                description=f"คุณได้ยกเลิกการเข้าร่วม **{event_row['name']}** แล้ว",
                color=COLOR_WARNING,
            )
            await member.send(embed=dm)
        except discord.Forbidden:
            pass

        log_action(guild, f"{member} left event #{event_row['id']}")
        return True

    # ------------------------------------------------------------------
    # !event ...
    # ------------------------------------------------------------------

    @commands.group(name="event", invoke_without_command=True)
    async def event_group(self, ctx: commands.Context) -> None:
        await ctx.send(
            "❌ ใช้ `!help event` เพื่อดูคำสั่งย่อยทั้งหมด (create, list, details, edit, join, leave, delete, channel)"
        )

    @event_group.command(name="create")
    @commands.has_permissions(administrator=True)
    async def event_create(
        self,
        ctx: commands.Context,
        name: str,
        date_raw: str,
        time_raw: str,
        description: str,
        image_url: Optional[str] = None,
    ) -> None:
        """Create a new event and post its announcement embed (Admin only)."""
        name = strip_quotes(name).strip()
        description = strip_quotes(description).strip()
        if image_url:
            image_url = strip_quotes(image_url).strip()
            if not image_url.startswith(("http://", "https://")):
                await ctx.send("❌ URL รูปภาพไม่ถูกต้อง โปรดใช้ URL ที่ขึ้นต้นด้วย http:// หรือ https://")
                return

        if not (1 <= len(name) <= NAME_MAX_LEN):
            await ctx.send(f"❌ ชื่อกิจกรรมต้องมีความยาว 1-{NAME_MAX_LEN} ตัวอักษร")
            return
        if len(description) > DESCRIPTION_MAX_LEN:
            await ctx.send(f"❌ รายละเอียดต้องไม่เกิน {DESCRIPTION_MAX_LEN} ตัวอักษร")
            return

        event_date = parse_event_date(date_raw)
        if event_date is None:
            await ctx.send("❌ รูปแบบวันที่ไม่ถูกต้อง ใช้ `DD/MM/YYYY`, `วันนี้`, `พรุ่งนี้`, หรือ `จันทร์หน้า`")
            return

        parsed_time = parse_event_time(time_raw)
        if parsed_time is None:
            await ctx.send("❌ รูปแบบเวลาไม่ถูกต้อง ใช้ `HH:MM` แบบ 24 ชั่วโมง เช่น 14:30")
            return
        hour, minute = parsed_time

        event_dt = combine(event_date, hour, minute)
        if event_dt < now_bangkok():
            await ctx.send("❌ วันที่ของกิจกรรมต้องเป็นอนาคต")
            return

        channel_id = db.get_event_channel(ctx.guild.id)
        if channel_id is None:
            await ctx.send("❌ ยังไม่ได้ตั้งค่าช่องประกาศกิจกรรม\nใช้: `!event channel set #channel_name`")
            return
        channel = ctx.guild.get_channel(channel_id)
        if channel is None:
            await ctx.send("❌ ไม่พบช่องที่ตั้งค่าไว้ โปรดตั้งค่าใหม่ด้วย `!event channel set #channel_name`")
            return

        if not image_url and ctx.message.attachments:
            first = ctx.message.attachments[0]
            if (first.content_type or "").startswith("image/"):
                image_url = first.url

        try:
            event_id = db.create_event(
                guild_id=ctx.guild.id,
                channel_id=channel.id,
                name=name,
                event_date=event_date.isoformat(),
                event_time=f"{hour:02d}:{minute:02d}",
                description=description,
                created_by_id=ctx.author.id,
                image_url=image_url,
            )
        except sqlite3.Error:
            log.exception("Failed to create event")
            await ctx.send("❌ เกิดข้อผิดพลาดในการบันทึกข้อมูล โปรดลองใหม่อีกครั้ง")
            return

        row = db.get_event(event_id, ctx.guild.id)
        embed = build_event_embed(row, 0, ctx.guild)

        try:
            message = await channel.send(embed=embed)
            for emoji in (JOIN_EMOJI, LEAVE_EMOJI):
                await message.add_reaction(emoji)
            db.set_event_announcement_message(event_id, channel.id, message.id)
        except discord.Forbidden:
            await ctx.send("⚠️ สร้างกิจกรรมในฐานข้อมูลแล้ว แต่บอทไม่มีสิทธิ์โพสต์หรือทำปฏิกิริยาในช่องนั้น")
            return

        await ctx.send(
            embed=make_embed(
                title="✅ สร้างกิจกรรมสำเร็จ",
                description=f"ประกาศกิจกรรม **{name}** ไปยัง {channel.mention} แล้ว",
                color=COLOR_SUCCESS,
            )
        )
        log_action(ctx.guild, f"{ctx.author} created event #{event_id}: {name}")

    @event_group.command(name="list")
    async def event_list(self, ctx: commands.Context) -> None:
        """List the next 10 upcoming events."""
        rows = db.list_upcoming_events(ctx.guild.id, max_days=30)
        if not rows:
            await ctx.send(
                embed=make_embed(
                    title="🗓️ กิจกรรมที่กำลังจะมาถึง",
                    description="ไม่มีกิจกรรมในช่วง 30 วันข้างหน้า",
                    color=COLOR_INFO,
                )
            )
            return

        embed = make_embed(title="🗓️ กิจกรรมที่กำลังจะมาถึง", color=COLOR_INFO)
        for row in rows[:EVENT_LIST_LIMIT]:
            attendee_count = db.count_attendees(row["id"])
            date_display = datetime.strptime(row["event_date"], "%Y-%m-%d").strftime("%d/%m/%Y")
            embed.add_field(
                name=f"#{row['id']} — {row['name']}",
                value=f"📅 {date_display} ⏰ {row['event_time']} น. | 👥 {attendee_count} คนเข้าร่วม",
                inline=False,
            )
        if len(rows) > EVENT_LIST_LIMIT:
            embed.set_footer(text=f"และอีก {len(rows) - EVENT_LIST_LIMIT} กิจกรรม — ใช้ !event details <id> เพื่อดูรายละเอียด")
        await ctx.send(embed=embed)

    @event_group.command(name="details")
    async def event_details(self, ctx: commands.Context, event_id: int) -> None:
        """Show full details for one event, including the attendee list."""
        row = db.get_event(event_id, ctx.guild.id)
        if row is None:
            await ctx.send(f"❌ ไม่พบกิจกรรม ID {event_id}")
            return

        attendee_ids = db.get_attendee_ids(event_id)
        embed = build_event_embed(row, len(attendee_ids), ctx.guild)
        if attendee_ids:
            shown_names = []
            for uid in attendee_ids[:20]:
                member = ctx.guild.get_member(uid)
                shown_names.append(member.display_name if member else f"Unknown ({uid})")
            shown = ", ".join(shown_names)
            if len(attendee_ids) > 20:
                shown += f" และอีก {len(attendee_ids) - 20} คน"
            embed.add_field(name="รายชื่อผู้เข้าร่วม", value=shown, inline=False)
        await ctx.send(embed=embed)

    @event_group.command(name="edit")
    @commands.has_permissions(administrator=True)
    async def event_edit(self, ctx: commands.Context, event_id: int, *, new_description: str) -> None:
        """Edit an event's description and update the live announcement embed (Admin only)."""
        new_description = strip_quotes(new_description).strip()
        if len(new_description) > DESCRIPTION_MAX_LEN:
            await ctx.send(f"❌ รายละเอียดต้องไม่เกิน {DESCRIPTION_MAX_LEN} ตัวอักษร")
            return

        row = db.get_event(event_id, ctx.guild.id)
        if row is None:
            await ctx.send(f"❌ ไม่พบกิจกรรม ID {event_id}")
            return

        db.update_event_description(event_id, ctx.guild.id, new_description)
        await self._refresh_announcement(ctx.guild, event_id)

        await ctx.send(f"✅ แก้ไขรายละเอียดกิจกรรม #{event_id} เรียบร้อยแล้ว")
        log_action(ctx.guild, f"{ctx.author} edited event #{event_id}")

    @event_group.command(name="join")
    async def event_join(self, ctx: commands.Context, event_id: int) -> None:
        """Register for an event."""
        row = db.get_event(event_id, ctx.guild.id)
        if row is None:
            await ctx.send(f"❌ ไม่พบกิจกรรม ID {event_id}")
            return

        joined = await self._join_event(ctx.guild, row, ctx.author)
        if not joined:
            await ctx.send(f"⚠️ คุณลงทะเบียนกิจกรรม **{row['name']}** ไปแล้ว")
            return

        await ctx.send(
            embed=make_embed(
                title="✅ ลงทะเบียนสำเร็จ",
                description=f"คุณได้ลงทะเบียนเข้าร่วม **{row['name']}** แล้ว",
                color=COLOR_SUCCESS,
            )
        )

    @event_group.command(name="leave")
    async def event_leave(self, ctx: commands.Context, event_id: int) -> None:
        """Cancel your registration for an event."""
        row = db.get_event(event_id, ctx.guild.id)
        if row is None:
            await ctx.send(f"❌ ไม่พบกิจกรรม ID {event_id}")
            return

        left = await self._leave_event(ctx.guild, row, ctx.author)
        if not left:
            await ctx.send(f"⚠️ คุณไม่ได้ลงทะเบียนกิจกรรม **{row['name']}**")
            return

        await ctx.send(
            embed=make_embed(
                title="✅ ยกเลิกการลงทะเบียนแล้ว",
                description=f"คุณได้ยกเลิกการเข้าร่วม **{row['name']}** แล้ว",
                color=COLOR_WARNING,
            )
        )

    @event_group.command(name="delete")
    @commands.has_permissions(administrator=True)
    async def event_delete(self, ctx: commands.Context, event_id: int) -> None:
        """Delete an event and remove its announcement embed from Discord (Admin only)."""
        row = db.get_event(event_id, ctx.guild.id)
        if row is None:
            await ctx.send(f"❌ ไม่พบกิจกรรม ID {event_id}")
            return

        db.delete_event(event_id, ctx.guild.id)

        if row["announcement_channel_id"] and row["announcement_message_id"]:
            channel = ctx.guild.get_channel(row["announcement_channel_id"])
            if channel is not None:
                try:
                    message = await channel.fetch_message(row["announcement_message_id"])
                    await message.delete()
                except (discord.NotFound, discord.Forbidden):
                    pass

        await ctx.send(
            embed=make_embed(
                title="🗑️ ลบกิจกรรมแล้ว",
                description=f"ลบกิจกรรม **{row['name']}** (ID: {event_id}) เรียบร้อยแล้ว",
                color=COLOR_DANGER,
            )
        )
        log_action(ctx.guild, f"{ctx.author} deleted event #{event_id}: {row['name']}")

    # ------------------------------------------------------------------
    # !event channel ...
    # ------------------------------------------------------------------

    @event_group.group(name="channel", invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def event_channel_group(self, ctx: commands.Context) -> None:
        await ctx.send("❌ ใช้ `!event channel set #channel`, `!event channel get`, หรือ `!event channel reset`")

    @event_channel_group.command(name="set")
    @commands.has_permissions(administrator=True)
    async def event_channel_set(self, ctx: commands.Context, channel: discord.TextChannel) -> None:
        """Set the channel event announcements are posted to (Admin only)."""
        db.set_event_channel(ctx.guild.id, channel.id)
        await ctx.send(f"✅ ตั้งค่าช่องประกาศกิจกรรมเป็น {channel.mention} แล้ว")
        log_action(ctx.guild, f"{ctx.author} set event channel to #{channel.name}")

    @event_channel_group.command(name="get")
    @commands.has_permissions(administrator=True)
    async def event_channel_get(self, ctx: commands.Context) -> None:
        """Show the currently configured event announcement channel (Admin only)."""
        channel_id = db.get_event_channel(ctx.guild.id)
        if channel_id is None:
            await ctx.send("❌ ยังไม่ได้ตั้งค่าช่องประกาศกิจกรรม ใช้ `!event channel set #channel`")
            return
        channel = ctx.guild.get_channel(channel_id)
        if channel is None:
            await ctx.send(f"⚠️ ตั้งค่าไว้ที่ช่อง ID `{channel_id}` แต่ไม่พบช่องนี้แล้ว (อาจถูกลบไปแล้ว)")
            return
        await ctx.send(f"📌 ช่องประกาศกิจกรรมปัจจุบัน: {channel.mention} (ID: {channel_id})")

    @event_channel_group.command(name="reset")
    @commands.has_permissions(administrator=True)
    async def event_channel_reset(self, ctx: commands.Context) -> None:
        """Clear the event announcement channel setting (Admin only)."""
        db.clear_event_channel(ctx.guild.id)
        await ctx.send("✅ ล้างค่าช่องประกาศกิจกรรมแล้ว")
        log_action(ctx.guild, f"{ctx.author} reset the event channel")

    # ------------------------------------------------------------------
    # Reaction-based join/leave
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.member is None or payload.member.bot:
            return
        emoji = str(payload.emoji)
        if emoji not in (JOIN_EMOJI, LEAVE_EMOJI):
            return

        row = db.get_event_by_message_id(payload.message_id)
        if row is None:
            return
        guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
        if guild is None:
            return

        if emoji == JOIN_EMOJI:
            await self._join_event(guild, row, payload.member)
            opposite = LEAVE_EMOJI
        else:
            await self._leave_event(guild, row, payload.member)
            opposite = JOIN_EMOJI

        # Keep the visual state tidy: drop the user's reaction on the other button, if any.
        channel = guild.get_channel(row["announcement_channel_id"])
        if channel is not None:
            try:
                message = await channel.fetch_message(payload.message_id)
                await message.remove_reaction(opposite, payload.member)
            except (discord.NotFound, discord.Forbidden):
                pass

    # ------------------------------------------------------------------
    # Reminders
    # ------------------------------------------------------------------

    @tasks.loop(minutes=30)
    async def reminder_check(self) -> None:
        for row in db.get_events_needing_reminder():
            guild = self.bot.get_guild(row["guild_id"])
            if guild is None:
                continue

            event_dt = _event_datetime(row)
            for uid in db.get_attendee_ids(row["id"]):
                member = guild.get_member(uid)
                if member is None:
                    continue
                try:
                    embed = make_embed(
                        title="⏰ แจ้งเตือนกิจกรรมใกล้เริ่ม!",
                        description=f"กิจกรรม **{row['name']}** จะเริ่มเร็วๆ นี้",
                        color=COLOR_WARNING,
                    )
                    embed.add_field(
                        name="📅 วันที่",
                        value=datetime.strptime(row["event_date"], "%Y-%m-%d").strftime("%d/%m/%Y"),
                        inline=True,
                    )
                    embed.add_field(name="⏰ เวลา", value=f"{row['event_time']} น.", inline=True)
                    embed.add_field(name="⏳ เหลือเวลา", value=format_remaining(event_dt), inline=True)
                    embed.set_footer(text=f"Event ID: #{row['id']}")
                    await member.send(embed=embed)
                except discord.Forbidden:
                    continue

            db.mark_reminder_sent(row["id"])
            log_action(guild, f"Sent reminder for event #{row['id']}: {row['name']}")

    @reminder_check.before_loop
    async def before_reminder_check(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EventsCog(bot))
