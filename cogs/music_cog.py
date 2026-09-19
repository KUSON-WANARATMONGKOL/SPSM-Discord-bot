"""Music playback: !play/pause/resume/stop/skip/queue/shuffle/clearqueue/volume/loop/now.

Built on wavelink 3.x, which talks to a separately-hosted Lavalink v4 server
over HTTP — this cog does not embed or manage a Lavalink process itself.
Point it at one via the LAVALINK_URI / LAVALINK_PASSWORD env vars (see
.env.example). If no node is reachable, every music command replies with a
"service unavailable" message instead of raising, so a missing/misconfigured
Lavalink server never takes the rest of the bot down.

Queue advancement (including loop modes) is handled in one place —
on_wavelink_track_end — regardless of whether a track ended naturally or was
skipped, so there's a single code path instead of duplicated "play next"
logic in both !skip and the natural end-of-track handler.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

import aiohttp
import discord
import wavelink
from discord.ext import commands

from common import COLOR_DANGER, COLOR_SUCCESS, make_embed

log = logging.getLogger("school_bot.music")

LAVALINK_URI = os.getenv("LAVALINK_URI", "http://localhost:2333")
LAVALINK_PASSWORD = os.getenv("LAVALINK_PASSWORD", "youshallnotpass")
LAVALINK_IDENTIFIER = os.getenv("LAVALINK_IDENTIFIER", "MAIN")
LAVALINK_RETRY_SECONDS = 15
LAVALINK_PROBE_TIMEOUT_SECONDS = 8

QUEUE_PAGE_SIZE = 10
PROGRESS_BAR_LENGTH = 20

_LOOP_MODES = {
    "off": wavelink.QueueMode.normal,
    "one": wavelink.QueueMode.loop,
    "all": wavelink.QueueMode.loop_all,
}
_LOOP_LABELS = {
    wavelink.QueueMode.normal: "ปิด / Off",
    wavelink.QueueMode.loop: "เพลงเดียว / One",
    wavelink.QueueMode.loop_all: "ทั้งคิว / All",
}


def format_time(milliseconds: int) -> str:
    seconds = milliseconds // 1000
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes}:{seconds:02d}"


def create_progress_bar(position_ms: int, duration_ms: int, length: int = PROGRESS_BAR_LENGTH) -> str:
    if duration_ms <= 0:
        return "`[" + "▬" * length + "]`"
    percentage = min(max(position_ms / duration_ms, 0.0), 1.0)
    filled = int(length * percentage)
    bar = "▓" * filled + "░" * (length - filled)
    return f"`[{bar}]` {percentage * 100:.0f}%"


class MusicCog(commands.Cog, name="Music"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._last_connect_error: Optional[str] = None

    async def cog_load(self) -> None:
        # Fire-and-forget: awaiting wait_until_ready() here directly would
        # deadlock, since cog_load runs inside setup_hook, which the gateway
        # connection (and therefore READY) waits on.
        self.bot.loop.create_task(self._connect_lavalink())

    async def _probe_lavalink(self, session: aiohttp.ClientSession) -> Optional[str]:
        """Cheap, bounded-timeout HTTP preflight, run before ever calling
        wavelink.Pool.connect(). This matters because wavelink's websocket
        layer retries a plain connection failure (e.g. "connection refused"
        because nothing is listening yet) internally, forever, with its own
        backoff, and never raises — so handing a bad/unreachable LAVALINK_URI
        straight to Pool.connect() looks IDENTICAL to "Lavalink is still
        booting, give it a moment" from the caller's side: both just hang
        with no error. This probe fails fast instead, and says specifically
        *why* — wrong password vs. unreachable host vs. an unexpected
        response — instead of leaving every failure mode looking the same.
        """
        url = f"{LAVALINK_URI.rstrip('/')}/version"
        try:
            async with session.get(
                url,
                headers={"Authorization": LAVALINK_PASSWORD},
                timeout=aiohttp.ClientTimeout(total=LAVALINK_PROBE_TIMEOUT_SECONDS),
            ) as resp:
                if resp.status == 200:
                    return None
                if resp.status in (401, 403):
                    return f"HTTP {resp.status} from {url} — LAVALINK_PASSWORD doesn't match the server's password"
                return f"HTTP {resp.status} from {url} — unexpected response"
        except asyncio.TimeoutError:
            return (
                f"timed out after {LAVALINK_PROBE_TIMEOUT_SECONDS}s reaching {url} "
                "(host unreachable/still starting up, or LAVALINK_URI is wrong)"
            )
        except aiohttp.ClientConnectorError as exc:
            return f"connection failed to {url}: {exc}"
        except aiohttp.ClientError as exc:
            return f"{type(exc).__name__} reaching {url}: {exc}"

    async def _connect_lavalink(self) -> None:
        await self.bot.wait_until_ready()
        # One aiohttp session is created up front and reused across retries —
        # wavelink.Node() otherwise opens a new one every attempt, which would
        # leak a session per failed retry for as long as Lavalink stays down.
        session = aiohttp.ClientSession()
        while not wavelink.Pool.nodes:
            probe_error = await self._probe_lavalink(session)
            if probe_error is not None:
                self._last_connect_error = probe_error
                log.warning("Lavalink not usable yet (%s) — retrying in %ss", probe_error, LAVALINK_RETRY_SECONDS)
                await asyncio.sleep(LAVALINK_RETRY_SECONDS)
                continue

            node = wavelink.Node(
                uri=LAVALINK_URI, password=LAVALINK_PASSWORD, identifier=LAVALINK_IDENTIFIER, session=session
            )
            try:
                await wavelink.Pool.connect(nodes=[node], client=self.bot)
            except Exception as exc:
                self._last_connect_error = f"wavelink raised {exc!r} while connecting"
                log.exception("Unexpected error connecting to Lavalink at %s", LAVALINK_URI)

            if wavelink.Pool.nodes:
                self._last_connect_error = None
                log.info("Connected to Lavalink at %s", LAVALINK_URI)
                return

            if self._last_connect_error is None:
                self._last_connect_error = (
                    "HTTP preflight succeeded but wavelink's websocket handshake did not register a node "
                    "(check the Lavalink logs for a rejected connection)"
                )
            log.warning("%s — retrying in %ss", self._last_connect_error, LAVALINK_RETRY_SECONDS)
            await asyncio.sleep(LAVALINK_RETRY_SECONDS)

    @commands.Cog.listener()
    async def on_wavelink_node_ready(self, payload: wavelink.NodeReadyEventPayload) -> None:
        log.info("Lavalink node ready: %s", payload.node.identifier)

    @commands.command(name="musicstatus")
    @commands.has_permissions(administrator=True)
    async def music_status(self, ctx: commands.Context) -> None:
        """Show Lavalink connection diagnostics (Admin only)."""
        connected = bool(wavelink.Pool.nodes)
        embed = make_embed(
            title="🔧 Music service status",
            color=COLOR_SUCCESS if connected else COLOR_DANGER,
        )
        embed.add_field(name="Status", value="✅ Connected" if connected else "❌ Not connected", inline=False)
        embed.add_field(name="LAVALINK_URI", value=f"`{LAVALINK_URI}`", inline=False)
        if connected:
            for identifier, node in wavelink.Pool.nodes.items():
                embed.add_field(name=f"Node: {identifier}", value=f"Players: {len(node.players)}", inline=False)
        else:
            embed.add_field(
                name="Last error",
                value=self._last_connect_error or "No connection attempt has completed yet",
                inline=False,
            )
            embed.add_field(name="Retrying every", value=f"{LAVALINK_RETRY_SECONDS}s", inline=True)
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------
    # Voice/player helpers
    # ------------------------------------------------------------------

    async def _connect_player(self, ctx: commands.Context) -> Optional[wavelink.Player]:
        """Used by !play: connects a new player if needed, or reuses an existing one."""
        if not wavelink.Pool.nodes:
            await ctx.send("❌ Music service unavailable, ลองใหม่ภายหลัง\n❌ Music service unavailable, try again later")
            return None
        if ctx.author.voice is None or ctx.author.voice.channel is None:
            await ctx.send("❌ คุณต้องอยู่ในรูม voice ก่อน\n❌ You must be in a voice channel first")
            return None

        player = ctx.voice_client
        if isinstance(player, wavelink.Player):
            if player.channel.id != ctx.author.voice.channel.id:
                await ctx.send("❌ คุณต้องอยู่ในห้องเสียงเดียวกับบอท\n❌ You must be in the same voice channel as the bot")
                return None
            return player

        voice_channel = ctx.author.voice.channel
        perms = voice_channel.permissions_for(ctx.guild.me)
        if not (perms.connect and perms.speak):
            await ctx.send(
                "❌ บอทไม่มีสิทธิ์ Connect/Speak ในห้องเสียงนี้\n"
                "❌ Bot needs 'Connect' and 'Speak' permissions in that voice channel"
            )
            return None

        try:
            player = await voice_channel.connect(cls=wavelink.Player)
        except (discord.ClientException, wavelink.ChannelTimeoutException):
            await ctx.send("❌ เข้าร่วมห้องเสียงไม่สำเร็จ\n❌ Could not join the voice channel")
            return None

        player.autoplay = wavelink.AutoPlayMode.disabled
        player.text_channel = ctx.channel
        return player

    def _get_player(self, ctx: commands.Context) -> Optional[wavelink.Player]:
        player = ctx.voice_client
        return player if isinstance(player, wavelink.Player) else None

    async def _get_controllable_player(self, ctx: commands.Context) -> Optional[wavelink.Player]:
        """Used by playback-control commands: requires an existing player AND the
        caller to be in the same voice channel, so control can't be hijacked from
        elsewhere in the server."""
        player = self._get_player(ctx)
        if player is None:
            await ctx.send("❌ ไม่มี player ในรูม voice นี้\n❌ No player in this voice channel")
            return None
        if ctx.author.voice is None or ctx.author.voice.channel.id != player.channel.id:
            await ctx.send(
                "❌ คุณต้องอยู่ในห้องเสียงเดียวกับบอทเพื่อควบคุมเพลง\n"
                "❌ You must be in the same voice channel to control playback"
            )
            return None
        return player

    # ------------------------------------------------------------------
    # Embeds
    # ------------------------------------------------------------------

    def _now_playing_embed(self, player: wavelink.Player, track: wavelink.Playable) -> discord.Embed:
        embed = make_embed(title="🎵 Now Playing", description=f"**{track.title}**")
        embed.add_field(name="🎤 ศิลปิน / Artist", value=track.author or "ไม่ทราบ / Unknown", inline=True)
        embed.add_field(name="⏱️ ความยาว / Duration", value=format_time(track.length), inline=True)
        embed.add_field(name="📍 แหล่งที่มา / Source", value=getattr(track, "source", None) or "ไม่ทราบ / Unknown", inline=True)
        embed.add_field(
            name="📊 ความคืบหน้า / Progress",
            value=create_progress_bar(player.position, track.length),
            inline=False,
        )
        embed.add_field(
            name="⏲️ เวลา / Time",
            value=f"{format_time(player.position)} / {format_time(track.length)}",
            inline=False,
        )
        embed.add_field(name="📋 คิว / Queue", value=f"{player.queue.count} เพลง / songs", inline=True)
        embed.add_field(name="🔊 ระดับเสียง / Volume", value=f"{player.volume}%", inline=True)
        embed.add_field(name="🔁 ลูป / Loop", value=_LOOP_LABELS.get(player.queue.mode, "ปิด / Off"), inline=True)
        return embed

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    @commands.command(name="play")
    async def play(self, ctx: commands.Context, *, query: str) -> None:
        """Play a song, or queue it if something is already playing.

        Usage: !play <song name or URL>
        """
        player = await self._connect_player(ctx)
        if player is None:
            return

        is_url = query.startswith(("http://", "https://"))
        try:
            results = await wavelink.Playable.search(query if is_url else query, source=None if is_url else wavelink.TrackSource.YouTube)
        except wavelink.LavalinkLoadException as exc:
            # Lavalink itself rejected the query (age-restricted, region-locked,
            # source plugin error, etc.) — surface its actual reason instead of
            # a generic "search failed", since the fix differs per cause.
            log.warning("Lavalink rejected query %r: %s", query, exc.error)
            await ctx.send(f"❌ โหลดเพลงไม่สำเร็จ: {exc.error}\n❌ Failed to load: {exc.error}")
            return
        except Exception:
            log.exception("Track search failed for query: %s", query)
            await ctx.send(f"❌ ค้นหาเพลงล้มเหลว: {query}\n❌ Search failed: {query}")
            return

        if not results:
            await ctx.send(f"❌ ไม่พบเพลง: {query}\n❌ No tracks found: {query}")
            return

        if isinstance(results, wavelink.Playlist):
            await player.queue.put_wait(results)
            embed = make_embed(
                title="🎵 เพิ่มเพลย์ลิสต์เข้า queue / Added playlist to queue",
                description=f"**{results.name}** — {len(results.tracks)} เพลง / songs",
            )
            await ctx.send(embed=embed)
            if player.current is None:
                await player.play(player.queue.get())
            return

        track = results[0]
        if player.current is not None:
            player.queue.put(track)
            embed = make_embed(
                title="🎵 เพิ่มเข้า queue / Added to queue",
                description=f"**{track.title}**\nโดย / by {track.author}",
            )
            embed.add_field(name="⏱️ ความยาว / Duration", value=format_time(track.length), inline=True)
            embed.add_field(name="📍 ตำแหน่งใน queue / Position", value=str(player.queue.count), inline=True)
            await ctx.send(embed=embed)
        else:
            await player.play(track)
            await ctx.send(embed=self._now_playing_embed(player, track))

    @commands.command(name="pause")
    async def pause(self, ctx: commands.Context) -> None:
        """Pause the current song."""
        player = await self._get_controllable_player(ctx)
        if player is None:
            return
        if player.current is None:
            await ctx.send("❌ ไม่มีเพลงเล่นอยู่ขณะนี้\n❌ Nothing is playing")
            return
        if player.paused:
            await ctx.send("❌ เพลงถูกหยุดชั่วคราวอยู่แล้ว\n❌ Already paused")
            return
        await player.pause(True)
        await ctx.send(f"⏸️ หยุดเพลงชั่วคราว: **{player.current.title}**\n⏸️ Paused: **{player.current.title}**")

    @commands.command(name="resume")
    async def resume(self, ctx: commands.Context) -> None:
        """Resume the paused song."""
        player = await self._get_controllable_player(ctx)
        if player is None:
            return
        if player.current is None:
            await ctx.send("❌ ไม่มีเพลงเล่นอยู่ขณะนี้\n❌ Nothing is playing")
            return
        if not player.paused:
            await ctx.send("❌ เพลงกำลังเล่นอยู่\n❌ Already playing")
            return
        await player.pause(False)
        await ctx.send(f"▶️ เล่นต่อ: **{player.current.title}**\n▶️ Resumed: **{player.current.title}**")

    @commands.command(name="stop")
    async def stop(self, ctx: commands.Context) -> None:
        """Stop playback and clear the queue."""
        player = await self._get_controllable_player(ctx)
        if player is None:
            return
        player.queue.clear()
        await player.stop()
        await ctx.send("⏹️ หยุดเพลงและล้าง queue แล้ว\n⏹️ Stopped and cleared the queue")

    @commands.command(name="skip")
    async def skip(self, ctx: commands.Context) -> None:
        """Skip to the next song."""
        player = await self._get_controllable_player(ctx)
        if player is None:
            return
        if player.current is None:
            await ctx.send("❌ ไม่มีเพลงเล่นอยู่ขณะนี้\n❌ Nothing is playing")
            return

        skipped = player.current
        embed = make_embed(title="⏭️ ข้ามเพลง / Skipped", description=f"**{skipped.title}**")
        if not player.queue.is_empty:
            upcoming = player.queue.peek(0)
            embed.add_field(name="🎵 เพลงถัดไป / Next song", value=f"**{upcoming.title}**", inline=False)
        await player.skip()
        await ctx.send(embed=embed)

    @commands.command(name="queue", aliases=["q"])
    async def queue_cmd(self, ctx: commands.Context, page: int = 1) -> None:
        """View the song queue.

        Usage: !queue [page]
        """
        player = self._get_player(ctx)
        if player is None:
            await ctx.send("❌ ไม่มี player ในรูม voice นี้\n❌ No player in this voice channel")
            return
        if player.queue.is_empty:
            await ctx.send("❌ Queue ว่างเปล่า\n❌ Queue is empty")
            return

        total_pages = (player.queue.count + QUEUE_PAGE_SIZE - 1) // QUEUE_PAGE_SIZE
        if page < 1 or page > total_pages:
            await ctx.send(f"❌ ไม่มีหน้า {page}\n❌ Page {page} doesn't exist")
            return

        start = (page - 1) * QUEUE_PAGE_SIZE
        end = min(start + QUEUE_PAGE_SIZE, player.queue.count)
        lines = [f"{i + 1}. **{player.queue.peek(i).title}** ({format_time(player.queue.peek(i).length)})" for i in range(start, end)]

        embed = make_embed(title="🎵 Queue", description="\n".join(lines))
        embed.set_footer(text=f"Page {page}/{total_pages} • Total: {player.queue.count} songs")
        await ctx.send(embed=embed)

    @commands.command(name="shuffle")
    async def shuffle(self, ctx: commands.Context) -> None:
        """Shuffle the queue."""
        player = await self._get_controllable_player(ctx)
        if player is None:
            return
        if player.queue.is_empty:
            await ctx.send("❌ Queue ว่างเปล่า\n❌ Queue is empty")
            return
        player.queue.shuffle()
        await ctx.send(f"🎲 สับเปลี่ยน queue แล้ว ({player.queue.count} เพลง)\n🎲 Shuffled queue ({player.queue.count} songs)")

    @commands.command(name="clearqueue")
    async def clearqueue(self, ctx: commands.Context) -> None:
        """Clear the entire queue (does not stop the current song)."""
        player = await self._get_controllable_player(ctx)
        if player is None:
            return
        if player.queue.is_empty:
            await ctx.send("❌ Queue ว่างเปล่าอยู่แล้ว\n❌ Queue is already empty")
            return
        count = player.queue.count
        player.queue.clear()
        await ctx.send(f"🗑️ ลบ queue แล้ว ({count} เพลง)\n🗑️ Cleared queue ({count} songs)")

    @commands.command(name="volume")
    async def volume(self, ctx: commands.Context, level: int) -> None:
        """Set the player volume (0-100).

        Usage: !volume <0-100>
        """
        if not (0 <= level <= 100):
            await ctx.send("❌ ระดับเสียงต้องเป็น 0-100\n❌ Volume must be 0-100")
            return
        player = await self._get_controllable_player(ctx)
        if player is None:
            return
        await player.set_volume(level)
        await ctx.send(f"🔊 ระดับเสียง: **{level}%**\n🔊 Volume: **{level}%**")

    @commands.command(name="loop")
    async def loop(self, ctx: commands.Context, mode: str = "off") -> None:
        """Set loop mode.

        Usage: !loop <off|one|all>
        """
        mode = mode.lower()
        if mode not in _LOOP_MODES:
            await ctx.send("❌ Mode ต้องเป็น: off, one, all\n❌ Mode must be: off, one, all")
            return
        player = await self._get_controllable_player(ctx)
        if player is None:
            return
        player.queue.mode = _LOOP_MODES[mode]
        await ctx.send(f"🔁 Loop: **{_LOOP_LABELS[_LOOP_MODES[mode]]}**")

    @commands.command(name="now", aliases=["np"])
    async def now(self, ctx: commands.Context) -> None:
        """Show the currently playing song with a progress bar."""
        player = self._get_player(ctx)
        if player is None or player.current is None:
            await ctx.send("❌ ไม่มีเพลงเล่นอยู่ขณะนี้\n❌ Nothing is playing")
            return
        await ctx.send(embed=self._now_playing_embed(player, player.current))

    # ------------------------------------------------------------------
    # Automatic queue advancement / idle handling
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload) -> None:
        player = payload.player
        if player is None:
            return

        # In loop/loop_all mode, queue.get() still returns the repeating track
        # even when the upcoming queue is otherwise empty — only skip advancing
        # in normal mode once the queue actually runs out.
        if player.queue.mode == wavelink.QueueMode.normal and player.queue.is_empty:
            return

        next_track = player.queue.get()
        await player.play(next_track)

        channel = getattr(player, "text_channel", None)
        if channel is not None:
            try:
                await channel.send(embed=self._now_playing_embed(player, next_track))
            except discord.Forbidden:
                pass

    @commands.Cog.listener()
    async def on_wavelink_inactive_player(self, player: wavelink.Player) -> None:
        channel = getattr(player, "text_channel", None)
        if channel is not None:
            try:
                await channel.send("👋 ไม่มีการเล่นเพลงนานเกินไป บอทออกจากห้องเสียงแล้ว\n👋 Left the voice channel due to inactivity")
            except discord.Forbidden:
                pass
        await player.disconnect()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MusicCog(bot))
