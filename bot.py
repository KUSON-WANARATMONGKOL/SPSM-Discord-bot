"""
School Discord Bot - Production Ready
A moderation, engagement, and community-management bot for a school Discord
server. See README.md for setup and CONTRIBUTING.md for development notes.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

# ============================================================================
# CONFIGURATION
# ============================================================================

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
WELCOME_CHANNEL_NAME = os.getenv("WELCOME_CHANNEL", "welcome")
PREFIX = "!"

MUTED_ROLE_NAME = "Muted"
WARN_LIMIT_ALERT = 3
CLEAR_MIN, CLEAR_MAX = 1, 100

ROLE_EMOJIS = {
    "🎓": "Grade 10",
    "📚": "Grade 11",
    "🏆": "Grade 12",
}

COLOR_SUCCESS = discord.Color.green()
COLOR_INFO = discord.Color.blue()
COLOR_WARNING = discord.Color.orange()
COLOR_DANGER = discord.Color.red()
COLOR_NEUTRAL = discord.Color.blurple()

# ============================================================================
# LOGGING
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("school_bot")

# ============================================================================
# BOT SETUP
# ============================================================================

intents = discord.Intents.all()

bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)

# In-memory storage (swap for a real database in production).
leaderboard: dict[int, dict[int, int]] = {}          # guild_id -> {user_id: points}
warnings_log: dict[int, dict[int, list[dict]]] = {}  # guild_id -> {user_id: [warning, ...]}
role_select_messages: dict[int, dict[str, str]] = {} # message_id -> {emoji: role_name}

# ============================================================================
# HELPERS
# ============================================================================


def make_embed(title: str, description: str = "", color: discord.Color = COLOR_NEUTRAL) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=color, timestamp=datetime.now(timezone.utc))
    return embed


def can_act_on(actor: discord.Member, target: discord.Member) -> bool:
    """Return True if actor is allowed to moderate target (higher role, not self, not owner)."""
    if actor.id == target.id:
        return False
    if target.id == target.guild.owner_id:
        return False
    return actor.top_role > target.top_role or actor.id == actor.guild.owner_id


def log_action(guild: discord.Guild | None, message: str) -> None:
    guild_name = guild.name if guild else "DM"
    log.info("[%s] %s", guild_name, message)


async def get_or_create_role(guild: discord.Guild, name: str, **kwargs) -> discord.Role:
    role = discord.utils.get(guild.roles, name=name)
    if role is None:
        role = await guild.create_role(name=name, reason=f"Auto-created '{name}' role", **kwargs)
        log_action(guild, f"Created missing role '{name}'")
    return role


# ============================================================================
# EVENTS
# ============================================================================


@bot.event
async def on_ready():
    log.info("Logged in as %s (ID: %s)", bot.user, bot.user.id if bot.user else "unknown")
    log.info("Connected to %d guild(s)", len(bot.guilds))
    if not daily_health_check.is_running():
        daily_health_check.start()
    await bot.change_presence(activity=discord.Game(name=f"{PREFIX}help | School Bot"))


@bot.event
async def on_member_join(member: discord.Member):
    log_action(member.guild, f"Member joined: {member} ({member.id})")

    channel = discord.utils.get(member.guild.text_channels, name=WELCOME_CHANNEL_NAME)
    if channel is None:
        log_action(member.guild, f"Welcome channel '#{WELCOME_CHANNEL_NAME}' not found, skipping welcome message")
        return

    embed = make_embed(
        title="👋 ยินดีต้อนรับ!",
        description=(
            f"ยินดีต้อนรับ {member.mention} เข้าสู่เซิร์ฟเวอร์!\n\n"
            f"คุณคือสมาชิกคนที่ **#{member.guild.member_count}**\n"
            f"กรุณาไปที่ช่องเลือกยศเพื่อเลือกระดับชั้นของคุณ"
        ),
        color=COLOR_SUCCESS,
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text="ขอให้สนุกกับการใช้งาน!")

    try:
        await channel.send(embed=embed)
    except discord.Forbidden:
        log_action(member.guild, f"Missing permission to send welcome message in #{channel.name}")


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.member is None or payload.member.bot:
        return

    mapping = role_select_messages.get(payload.message_id)
    if mapping is None:
        return

    emoji = str(payload.emoji)
    role_name = mapping.get(emoji)
    if role_name is None:
        return

    guild = bot.get_guild(payload.guild_id) if payload.guild_id else None
    if guild is None:
        return

    try:
        role = await get_or_create_role(guild, role_name)
        await payload.member.add_roles(role, reason="Self-assigned via reaction role")
        log_action(guild, f"Assigned role '{role_name}' to {payload.member} via reaction")

        try:
            dm_embed = make_embed(
                title="✅ ได้รับยศเรียบร้อย",
                description=f"คุณได้รับยศ **{role_name}** ในเซิร์ฟเวอร์ **{guild.name}** แล้ว",
                color=COLOR_SUCCESS,
            )
            await payload.member.send(embed=dm_embed)
        except discord.Forbidden:
            pass  # User has DMs disabled; not a critical failure.
    except discord.Forbidden:
        log_action(guild, f"Missing permission to assign role '{role_name}' to {payload.member}")
    except Exception as exc:
        log.exception("Error assigning reaction role: %s", exc)


@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    mapping = role_select_messages.get(payload.message_id)
    if mapping is None:
        return

    emoji = str(payload.emoji)
    role_name = mapping.get(emoji)
    if role_name is None:
        return

    guild = bot.get_guild(payload.guild_id) if payload.guild_id else None
    if guild is None:
        return

    member = guild.get_member(payload.user_id)
    if member is None or member.bot:
        return

    role = discord.utils.get(guild.roles, name=role_name)
    if role is None or role not in member.roles:
        return

    try:
        await member.remove_roles(role, reason="Un-reacted from reaction role message")
        log_action(guild, f"Removed role '{role_name}' from {member} via reaction removal")
    except discord.Forbidden:
        log_action(guild, f"Missing permission to remove role '{role_name}' from {member}")


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.CommandNotFound):
        return  # Silently ignore unknown commands.

    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ คุณไม่มีสิทธิในการใช้คำสั่งนี้")
        return

    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ ข้อมูลไม่ครบ ใช้ {PREFIX}help {ctx.command}")
        return

    if isinstance(error, (commands.BadArgument, commands.MemberNotFound, commands.RoleNotFound)):
        await ctx.send("❌ โปรดใส่ข้อมูลที่ถูกต้อง")
        return

    log.exception("Unhandled error in command '%s': %s", ctx.command, error)
    await ctx.send("❌ เกิดข้อผิดพลาด ติดต่อผู้ดูแล")


# ============================================================================
# BACKGROUND TASKS
# ============================================================================


@tasks.loop(hours=24)
async def daily_health_check():
    log.info("Daily health check OK — connected to %d guild(s), latency %.0fms", len(bot.guilds), bot.latency * 1000)


@daily_health_check.before_loop
async def before_daily_health_check():
    await bot.wait_until_ready()


# ============================================================================
# COMMANDS — BASIC
# ============================================================================


@bot.command(name="hello")
async def hello(ctx: commands.Context):
    """Say hello to the bot."""
    embed = make_embed(title="👋 สวัสดี!", description=f"สวัสดี {ctx.author.mention}!", color=COLOR_SUCCESS)
    await ctx.send(embed=embed)


@bot.command(name="ping")
async def ping(ctx: commands.Context):
    """Check the bot's latency."""
    embed = make_embed(title="🏓 Pong!", description=f"🏓 Pong! {round(bot.latency * 1000)}ms", color=COLOR_INFO)
    await ctx.send(embed=embed)


@bot.command(name="rules")
async def rules(ctx: commands.Context):
    """Display server rules."""
    embed = make_embed(title="📜 กฎของเซิร์ฟเวอร์", color=COLOR_DANGER)
    embed.description = (
        "1️⃣ ให้เกียรติซึ่งกันและกัน ห้ามใช้คำหยาบหรือกลั่นแกล้งผู้อื่น\n"
        "2️⃣ ห้ามสแปมหรือโฆษณาที่ไม่เกี่ยวข้อง\n"
        "3️⃣ ใช้ช่องแชทให้ตรงตามวัตถุประสงค์ของช่องนั้นๆ\n"
        "4️⃣ ห้ามโพสต์เนื้อหาที่ไม่เหมาะสม (NSFW, ความรุนแรง ฯลฯ)\n"
        "5️⃣ ปฏิบัติตามคำแนะนำของผู้ดูแลระบบเสมอ"
    )
    embed.set_footer(text="⚠️ การฝ่าฝืนกฎอาจส่งผลให้ถูกตักเตือน มิวท์ หรือเตะออกจากเซิร์ฟเวอร์")
    await ctx.send(embed=embed)


@bot.command(name="server")
async def server_info(ctx: commands.Context):
    """Display server statistics."""
    guild = ctx.guild
    if guild is None:
        await ctx.send("❌ คำสั่งนี้ใช้ได้เฉพาะในเซิร์ฟเวอร์เท่านั้น")
        return

    embed = make_embed(title=f"📊 ข้อมูลเซิร์ฟเวอร์: {guild.name}", color=COLOR_INFO)
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.add_field(name="👥 สมาชิก", value=str(guild.member_count), inline=True)
    embed.add_field(name="💬 ช่องทั้งหมด", value=str(len(guild.channels)), inline=True)
    embed.add_field(name="👑 เจ้าของ", value=str(guild.owner) if guild.owner else "N/A", inline=True)
    embed.add_field(name="📅 สร้างเมื่อ", value=guild.created_at.strftime("%Y-%m-%d"), inline=True)
    embed.add_field(name="🎭 ยศทั้งหมด", value=str(len(guild.roles)), inline=True)
    await ctx.send(embed=embed)


@bot.command(name="schedule")
async def schedule(ctx: commands.Context):
    """Display the class schedule and school hours."""
    embed = make_embed(title="🗓️ ตารางเรียนและเวลาเรียน", color=COLOR_INFO)
    embed.add_field(name="🏫 เวลาเข้าเรียน", value="08:00 - 16:00 น.", inline=False)
    embed.add_field(name="🍽️ พักกลางวัน", value="12:00 - 13:00 น.", inline=False)
    embed.add_field(name="📚 คาบเรียน", value="คาบละ 50 นาที, พัก 10 นาที ระหว่างคาบ", inline=False)
    embed.set_footer(text="โปรดตรวจสอบประกาศจากโรงเรียนสำหรับวันสอบและวันสำคัญ")
    await ctx.send(embed=embed)


# ============================================================================
# COMMANDS — POINTS & LEADERBOARD
# ============================================================================


@bot.command(name="addpoint")
@commands.has_permissions(administrator=True)
async def addpoint(ctx: commands.Context, member: discord.Member, points: int):
    """Add points to a member (Admin only)."""
    if points <= 0:
        await ctx.send("❌ โปรดใส่ข้อมูลที่ถูกต้อง")
        return

    guild_board = leaderboard.setdefault(ctx.guild.id, {})
    guild_board[member.id] = guild_board.get(member.id, 0) + points

    embed = make_embed(
        title="✅ เพิ่มคะแนนสำเร็จ",
        description=f"เพิ่ม **{points}** คะแนนให้ {member.mention}\nคะแนนรวม: **{guild_board[member.id]}**",
        color=COLOR_SUCCESS,
    )
    await ctx.send(embed=embed)
    log_action(ctx.guild, f"{ctx.author} added {points} points to {member} (total: {guild_board[member.id]})")


@bot.command(name="leaderboard")
async def leaderboard_cmd(ctx: commands.Context):
    """Display the top 10 users by points."""
    guild_board = leaderboard.get(ctx.guild.id, {})
    if not guild_board:
        await ctx.send(embed=make_embed(title="🏆 กระดานผู้นำ", description="ยังไม่มีข้อมูลคะแนน", color=COLOR_INFO))
        return

    sorted_scores = sorted(guild_board.items(), key=lambda item: item[1], reverse=True)[:10]
    medals = ["🥇", "🥈", "🥉"]

    lines = []
    for i, (user_id, points) in enumerate(sorted_scores):
        member = ctx.guild.get_member(user_id)
        name = member.display_name if member else f"Unknown ({user_id})"
        prefix = medals[i] if i < len(medals) else f"{i + 1}."
        lines.append(f"{prefix} **{name}** — {points} คะแนน")

    embed = make_embed(title="🏆 กระดานผู้นำ", description="\n".join(lines), color=COLOR_WARNING)
    await ctx.send(embed=embed)


# ============================================================================
# COMMANDS — ANNOUNCEMENTS & ROLE SELECTION
# ============================================================================


@bot.command(name="announce")
@commands.has_permissions(administrator=True)
async def announce(ctx: commands.Context, *, message: str):
    """Send a formatted announcement (Admin only)."""
    embed = make_embed(title="📢 ประกาศสำคัญ", description=message, color=COLOR_DANGER)
    embed.set_footer(text=f"ประกาศโดย {ctx.author.display_name}")
    await ctx.send(embed=embed)
    log_action(ctx.guild, f"{ctx.author} sent announcement: {message[:200]}")


@bot.command(name="role_select")
@commands.has_permissions(administrator=True)
async def role_select(ctx: commands.Context):
    """Send the reaction-based role selection embed (Admin only)."""
    description_lines = [f"{emoji} — {name}" for emoji, name in ROLE_EMOJIS.items()]
    embed = make_embed(
        title="🎭 เลือกระดับชั้นของคุณ",
        description="ทำปฏิกิริยา (react) กับอิโมจิด้านล่างเพื่อรับยศที่ตรงกับระดับชั้นของคุณ\n\n"
        + "\n".join(description_lines),
        color=COLOR_INFO,
    )
    message = await ctx.send(embed=embed)

    for emoji in ROLE_EMOJIS:
        try:
            await message.add_reaction(emoji)
        except discord.Forbidden:
            log_action(ctx.guild, "Missing permission to add reactions for role_select")
            break

    # Ensure the roles exist ahead of time so first reactors don't hit delays.
    for role_name in ROLE_EMOJIS.values():
        await get_or_create_role(ctx.guild, role_name)

    role_select_messages[message.id] = dict(ROLE_EMOJIS)
    log_action(ctx.guild, f"{ctx.author} posted role selection message {message.id}")


# ============================================================================
# COMMANDS — MODERATION
# ============================================================================


@bot.command(name="kick")
@commands.has_permissions(administrator=True)
async def kick(ctx: commands.Context, member: discord.Member, *, reason: str = "ไม่ระบุเหตุผล"):
    """Kick a member from the server (Admin only)."""
    if not can_act_on(ctx.author, member):
        await ctx.send("❌ คุณไม่สามารถเตะสมาชิกคนนี้ได้ (ยศเท่ากันหรือสูงกว่า)")
        return

    try:
        await member.kick(reason=f"{reason} | By {ctx.author}")
    except discord.Forbidden:
        await ctx.send("❌ เกิดข้อผิดพลาด ติดต่อผู้ดูแล")
        return

    embed = make_embed(
        title="👢 เตะสมาชิกออกแล้ว",
        description=f"**{member}** ถูกเตะออกจากเซิร์ฟเวอร์\n**เหตุผล:** {reason}",
        color=COLOR_DANGER,
    )
    embed.set_footer(text=f"ดำเนินการโดย {ctx.author.display_name}")
    await ctx.send(embed=embed)
    log_action(ctx.guild, f"{ctx.author} kicked {member} | reason: {reason}")


@bot.command(name="mute")
@commands.has_permissions(administrator=True)
async def mute(ctx: commands.Context, member: discord.Member):
    """Mute a member so they cannot send messages (Admin only)."""
    if not can_act_on(ctx.author, member):
        await ctx.send("❌ คุณไม่สามารถมิวท์สมาชิกคนนี้ได้ (ยศเท่ากันหรือสูงกว่า)")
        return

    muted_role = await get_or_create_role(ctx.guild, MUTED_ROLE_NAME, reason="Muted role for moderation")

    for channel in ctx.guild.channels:
        try:
            await channel.set_permissions(muted_role, send_messages=False, speak=False, add_reactions=False)
        except discord.Forbidden:
            continue

    try:
        await member.add_roles(muted_role, reason=f"Muted by {ctx.author}")
    except discord.Forbidden:
        await ctx.send("❌ เกิดข้อผิดพลาด ติดต่อผู้ดูแล")
        return

    embed = make_embed(title="🔇 มิวท์สำเร็จ", description=f"{member.mention} ถูกมิวท์แล้ว", color=COLOR_WARNING)
    await ctx.send(embed=embed)
    log_action(ctx.guild, f"{ctx.author} muted {member}")


@bot.command(name="warn")
@commands.has_permissions(administrator=True)
async def warn(ctx: commands.Context, member: discord.Member, *, reason: str = "ไม่ระบุเหตุผล"):
    """Warn a member and track their warning history (Admin only)."""
    if not can_act_on(ctx.author, member):
        await ctx.send("❌ คุณไม่สามารถตักเตือนสมาชิกคนนี้ได้ (ยศเท่ากันหรือสูงกว่า)")
        return

    guild_warnings = warnings_log.setdefault(ctx.guild.id, {})
    member_warnings = guild_warnings.setdefault(member.id, [])
    member_warnings.append(
        {"reason": reason, "by": str(ctx.author), "timestamp": datetime.now(timezone.utc).isoformat()}
    )
    count = len(member_warnings)

    embed = make_embed(
        title="⚠️ ตักเตือนสมาชิก",
        description=f"{member.mention} ได้รับการตักเตือน\n**เหตุผล:** {reason}\n**จำนวนครั้ง:** {count}",
        color=COLOR_WARNING,
    )
    if count >= WARN_LIMIT_ALERT:
        embed.add_field(
            name="🚨 แจ้งเตือน",
            value=f"สมาชิกคนนี้ถูกตักเตือนครบ {count} ครั้งแล้ว โปรดพิจารณาดำเนินการเพิ่มเติม",
            inline=False,
        )
    await ctx.send(embed=embed)
    log_action(ctx.guild, f"{ctx.author} warned {member} ({count} total) | reason: {reason}")


@bot.command(name="clear")
@commands.has_permissions(administrator=True)
async def clear(ctx: commands.Context, count: int):
    """Bulk delete messages, 1-100 at a time (Admin only)."""
    if not (CLEAR_MIN <= count <= CLEAR_MAX):
        await ctx.send(f"❌ โปรดใส่จำนวนระหว่าง {CLEAR_MIN}-{CLEAR_MAX}")
        return

    deleted = await ctx.channel.purge(limit=count + 1)  # +1 to include the command message itself.
    notice = await ctx.send(embed=make_embed(title="🧹 ลบข้อความแล้ว", description=f"ลบ {len(deleted) - 1} ข้อความ", color=COLOR_SUCCESS))
    await notice.delete(delay=5)
    log_action(ctx.guild, f"{ctx.author} cleared {len(deleted) - 1} messages in #{ctx.channel}")


# ============================================================================
# COMMANDS — HELP
# ============================================================================

HELP_CATEGORIES = {
    "basic": {
        "title": "📘 คำสั่งพื้นฐาน",
        "commands": {
            "hello": "ทักทายบอท — ใช้: !hello",
            "ping": "เช็คความหน่วงของบอท — ใช้: !ping",
            "server": "ดูข้อมูลเซิร์ฟเวอร์ — ใช้: !server",
            "rules": "แสดงกฎเซิร์ฟเวอร์ — ใช้: !rules",
        },
    },
    "school": {
        "title": "🏫 คำสั่งเกี่ยวกับโรงเรียน",
        "commands": {
            "schedule": "ดูตารางเรียนและเวลาเรียน — ใช้: !schedule",
        },
    },
    "points": {
        "title": "🏆 คะแนนและกระดานผู้นำ",
        "commands": {
            "leaderboard": "ดูอันดับคะแนนสูงสุด — ใช้: !leaderboard",
            "addpoint": "เพิ่มคะแนนให้สมาชิก (Admin) — ใช้: !addpoint @user [points]",
        },
    },
    "moderation": {
        "title": "🛡️ คำสั่งผู้ดูแล",
        "commands": {
            "announce": "ส่งประกาศ (Admin) — ใช้: !announce [message]",
            "role_select": "ส่งเมนูเลือกยศ (Admin) — ใช้: !role_select",
            "kick": "เตะสมาชิก (Admin) — ใช้: !kick @user [reason]",
            "mute": "มิวท์สมาชิก (Admin) — ใช้: !mute @user",
            "warn": "ตักเตือนสมาชิก (Admin) — ใช้: !warn @user [reason]",
            "clear": "ลบข้อความ (Admin) — ใช้: !clear [count]",
        },
    },
}


@bot.command(name="help")
async def custom_help(ctx: commands.Context, command_name: str = None):
    """Show all commands, or details for a specific command."""
    if command_name is None:
        embed = make_embed(title="📖 รายการคำสั่งทั้งหมด", description=f"ใช้ `{PREFIX}help [command]` เพื่อดูรายละเอียดคำสั่ง", color=COLOR_INFO)
        for category in HELP_CATEGORIES.values():
            lines = [f"`{PREFIX}{name}` — {desc}" for name, desc in category["commands"].items()]
            embed.add_field(name=category["title"], value="\n".join(lines), inline=False)
        await ctx.send(embed=embed)
        return

    command_name = command_name.lstrip(PREFIX).lower()
    for category in HELP_CATEGORIES.values():
        if command_name in category["commands"]:
            embed = make_embed(title=f"📖 คำสั่ง: {PREFIX}{command_name}", description=category["commands"][command_name], color=COLOR_INFO)
            await ctx.send(embed=embed)
            return

    await ctx.send(f"❌ ไม่พบคำสั่ง `{command_name}` ใช้ `{PREFIX}help` เพื่อดูรายการทั้งหมด")


# ============================================================================
# ENTRY POINT
# ============================================================================


def main():
    if not TOKEN:
        log.error("DISCORD_TOKEN not found. Create a .env file (see .env.example) with your bot token.")
        raise SystemExit(1)

    try:
        bot.run(TOKEN)
    except discord.LoginFailure:
        log.error("Login failed: the provided DISCORD_TOKEN is invalid.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
