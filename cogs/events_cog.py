"""Event management: !event create/list/details/join/leave/delete.

Events are stored in SQLite (see db.py) so they survive bot restarts. All
dates/times are Asia/Bangkok local time. A background task DMs attendees
~24h before their event starts.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime

import discord
from discord.ext import commands, tasks

import db
from common import COLOR_DANGER, COLOR_INFO, COLOR_SUCCESS, COLOR_WARNING, log_action, make_embed, strip_quotes
from date_utils import BANGKOK_TZ, combine, format_remaining, now_bangkok, parse_event_date, parse_event_time

log = logging.getLogger("school_bot.events")

NAME_MAX_LEN = 100
DESCRIPTION_MAX_LEN = 1000


def _event_datetime(row: sqlite3.Row) -> datetime:
    hour, minute = (int(part) for part in row["event_time"].split(":"))
    return combine(datetime.strptime(row["event_date"], "%Y-%m-%d").date(), hour, minute)


class EventsCog(commands.Cog, name="Events"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.reminder_check.start()

    def cog_unload(self) -> None:
        self.reminder_check.cancel()

    # ------------------------------------------------------------------
    # !event ...
    # ------------------------------------------------------------------

    @commands.group(name="event", invoke_without_command=True)
    async def event_group(self, ctx: commands.Context) -> None:
        await ctx.send("❌ ใช้ `!help event` เพื่อดูคำสั่งย่อยทั้งหมด (create, list, details, join, leave, delete)")

    @event_group.command(name="create")
    @commands.has_permissions(administrator=True)
    async def event_create(
        self,
        ctx: commands.Context,
        name: str,
        date_raw: str,
        time_raw: str,
        *,
        description: str = "",
    ) -> None:
        """Create a new event (Admin only)."""
        name = strip_quotes(name).strip()
        description = strip_quotes(description).strip()

        if not (1 <= len(name) <= NAME_MAX_LEN):
            await ctx.send(f"❌ ชื่อกิจกรรมต้องมีความยาว 1-{NAME_MAX_LEN} ตัวอักษร")
            return
        if len(description) > DESCRIPTION_MAX_LEN:
            await ctx.send(f"❌ รายละเอียดต้องไม่เกิน {DESCRIPTION_MAX_LEN} ตัวอักษร")
            return

        event_date = parse_event_date(date_raw)
        if event_date is None:
            await ctx.send("❌ รูปแบบวันที่ไม่ถูกต้อง ใช้ `DD/MM/YYYY`, `tomorrow`/`พรุ่งนี้`, หรือ `next Monday`")
            return

        parsed_time = parse_event_time(time_raw)
        if parsed_time is None:
            await ctx.send("❌ รูปแบบเวลาไม่ถูกต้อง ใช้ `HH:MM` แบบ 24 ชั่วโมง เช่น 14:30")
            return
        hour, minute = parsed_time

        event_dt = combine(event_date, hour, minute)
        if event_dt < now_bangkok():
            await ctx.send("❌ ไม่สามารถสร้างกิจกรรมที่เป็นวัน-เวลาในอดีตได้")
            return

        try:
            event_id = db.create_event(
                guild_id=ctx.guild.id,
                channel_id=ctx.channel.id,
                name=name,
                event_date=event_date.isoformat(),
                event_time=f"{hour:02d}:{minute:02d}",
                description=description,
                created_by_id=ctx.author.id,
            )
        except sqlite3.Error:
            log.exception("Failed to create event")
            await ctx.send("❌ เกิดข้อผิดพลาดในการบันทึกข้อมูล โปรดลองใหม่อีกครั้ง")
            return

        embed = make_embed(
            title=f"🎉 สร้างกิจกรรมสำเร็จ: {name}",
            description=description or "ไม่มีรายละเอียดเพิ่มเติม",
            color=COLOR_SUCCESS,
        )
        embed.add_field(name="📅 วันที่", value=event_date.strftime("%d/%m/%Y"), inline=True)
        embed.add_field(name="⏰ เวลา", value=f"{hour:02d}:{minute:02d} น.", inline=True)
        embed.add_field(name="⏳ เหลือเวลา", value=format_remaining(event_dt), inline=True)
        embed.set_footer(text=f"Event ID: {event_id}")
        await ctx.send(embed=embed)
        log_action(ctx.guild, f"{ctx.author} created event #{event_id}: {name}")

    @event_group.command(name="list")
    async def event_list(self, ctx: commands.Context) -> None:
        """List upcoming events (next 30 days)."""
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

        embed = make_embed(title="🗓️ กิจกรรมที่กำลังจะมาถึง (30 วันข้างหน้า)", color=COLOR_INFO)
        for row in rows[:15]:
            attendee_count = db.count_attendees(row["id"])
            date_display = datetime.strptime(row["event_date"], "%Y-%m-%d").strftime("%d/%m/%Y")
            embed.add_field(
                name=f"#{row['id']} — {row['name']}",
                value=f"📅 {date_display} ⏰ {row['event_time']} น. | 👥 {attendee_count} คนเข้าร่วม",
                inline=False,
            )
        if len(rows) > 15:
            embed.set_footer(text=f"และอีก {len(rows) - 15} กิจกรรม — ใช้ !event details <id> เพื่อดูรายละเอียด")
        await ctx.send(embed=embed)

    @event_group.command(name="details")
    async def event_details(self, ctx: commands.Context, event_id: int) -> None:
        """Show full details for one event."""
        row = db.get_event(event_id, ctx.guild.id)
        if row is None:
            await ctx.send(f"❌ ไม่พบกิจกรรม ID {event_id}")
            return

        event_dt = _event_datetime(row)
        attendee_ids = db.get_attendee_ids(event_id)
        shown_names = []
        for uid in attendee_ids[:20]:
            member = ctx.guild.get_member(uid)
            shown_names.append(member.display_name if member else f"Unknown ({uid})")

        creator = ctx.guild.get_member(row["created_by_id"])

        embed = make_embed(
            title=f"🎉 {row['name']}",
            description=row["description"] or "ไม่มีรายละเอียดเพิ่มเติม",
            color=COLOR_INFO,
        )
        embed.add_field(name="📅 วันที่", value=datetime.strptime(row["event_date"], "%Y-%m-%d").strftime("%d/%m/%Y"), inline=True)
        embed.add_field(name="⏰ เวลา", value=f"{row['event_time']} น.", inline=True)
        embed.add_field(name="⏳ เหลือเวลา", value=format_remaining(event_dt), inline=True)
        embed.add_field(name="👤 ผู้สร้าง", value=creator.mention if creator else "ไม่ทราบ", inline=True)
        embed.add_field(name="👥 ผู้เข้าร่วม", value=str(len(attendee_ids)), inline=True)
        if shown_names:
            shown = ", ".join(shown_names)
            if len(attendee_ids) > 20:
                shown += f" และอีก {len(attendee_ids) - 20} คน"
            embed.add_field(name="รายชื่อผู้เข้าร่วม", value=shown, inline=False)
        embed.set_footer(text=f"Event ID: {event_id}")
        await ctx.send(embed=embed)

    @event_group.command(name="join")
    async def event_join(self, ctx: commands.Context, event_id: int) -> None:
        """Register for an event."""
        row = db.get_event(event_id, ctx.guild.id)
        if row is None:
            await ctx.send(f"❌ ไม่พบกิจกรรม ID {event_id}")
            return

        try:
            added = db.add_attendee(event_id, ctx.author.id)
        except sqlite3.Error:
            log.exception("Failed to add attendee")
            await ctx.send("❌ เกิดข้อผิดพลาดในการบันทึกข้อมูล โปรดลองใหม่อีกครั้ง")
            return

        if not added:
            await ctx.send("⚠️ คุณลงทะเบียนกิจกรรมนี้ไปแล้ว")
            return

        await ctx.send(
            embed=make_embed(
                title="✅ ลงทะเบียนสำเร็จ",
                description=f"คุณได้ลงทะเบียนเข้าร่วม **{row['name']}** แล้ว",
                color=COLOR_SUCCESS,
            )
        )

        try:
            dm = make_embed(
                title="✅ ยืนยันการลงทะเบียน",
                description=f"คุณได้ลงทะเบียนเข้าร่วมกิจกรรม **{row['name']}** เรียบร้อยแล้ว",
                color=COLOR_SUCCESS,
            )
            dm.set_footer(text=f"Event ID: {event_id}")
            await ctx.author.send(embed=dm)
        except discord.Forbidden:
            pass

        creator = ctx.guild.get_member(row["created_by_id"])
        if creator and creator.id != ctx.author.id:
            try:
                notify = make_embed(
                    title="👥 มีผู้เข้าร่วมกิจกรรมใหม่",
                    description=f"{ctx.author.mention} ได้ลงทะเบียนเข้าร่วม **{row['name']}**",
                    color=COLOR_INFO,
                )
                notify.set_footer(text=f"Event ID: {event_id}")
                await creator.send(embed=notify)
            except discord.Forbidden:
                pass

        log_action(ctx.guild, f"{ctx.author} joined event #{event_id}")

    @event_group.command(name="leave")
    async def event_leave(self, ctx: commands.Context, event_id: int) -> None:
        """Cancel your registration for an event."""
        row = db.get_event(event_id, ctx.guild.id)
        if row is None:
            await ctx.send(f"❌ ไม่พบกิจกรรม ID {event_id}")
            return

        removed = db.remove_attendee(event_id, ctx.author.id)
        if not removed:
            await ctx.send("⚠️ คุณยังไม่ได้ลงทะเบียนกิจกรรมนี้")
            return

        await ctx.send(
            embed=make_embed(
                title="✅ ยกเลิกการลงทะเบียนแล้ว",
                description=f"คุณได้ยกเลิกการเข้าร่วม **{row['name']}** แล้ว",
                color=COLOR_WARNING,
            )
        )
        log_action(ctx.guild, f"{ctx.author} left event #{event_id}")

    @event_group.command(name="delete")
    @commands.has_permissions(administrator=True)
    async def event_delete(self, ctx: commands.Context, event_id: int) -> None:
        """Delete an event (Admin only)."""
        row = db.get_event(event_id, ctx.guild.id)
        if row is None:
            await ctx.send(f"❌ ไม่พบกิจกรรม ID {event_id}")
            return

        db.delete_event(event_id, ctx.guild.id)
        await ctx.send(
            embed=make_embed(
                title="🗑️ ลบกิจกรรมแล้ว",
                description=f"ลบกิจกรรม **{row['name']}** (ID: {event_id}) เรียบร้อยแล้ว",
                color=COLOR_DANGER,
            )
        )
        log_action(ctx.guild, f"{ctx.author} deleted event #{event_id}: {row['name']}")

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
                    embed.set_footer(text=f"Event ID: {row['id']}")
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
