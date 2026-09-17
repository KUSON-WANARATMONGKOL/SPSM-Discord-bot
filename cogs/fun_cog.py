"""Fun/engagement commands: !quote, !fact, !joke, !poll, !8ball, !dice, !compliment."""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Optional

import discord
from discord.ext import commands

from common import COLOR_INFO, COLOR_SUCCESS, make_embed, strip_quotes

log = logging.getLogger("school_bot.fun")

QUOTES = [
    ("ความพยายามอยู่ที่ไหน ความสำเร็จอยู่ที่นั่น", "สุภาษิตไทย"),
    ("จงเป็นการเปลี่ยนแปลงที่คุณอยากเห็นในโลกใบนี้", "มหาตมะ คานธี"),
    ("ล้มเจ็ดครั้ง ลุกแปดครั้ง", "สุภาษิตญี่ปุ่น"),
    ("การศึกษาคืออาวุธที่ทรงพลังที่สุดที่คุณสามารถใช้เปลี่ยนแปลงโลกได้", "เนลสัน แมนเดลา"),
    ("ความรู้คือพลัง", "ฟรานซิส เบคอน"),
    ("อย่ากลัวที่จะเริ่มต้นใหม่ มันคือโอกาสในการสร้างสิ่งที่ดีกว่าเดิม", "ไม่ทราบผู้แต่ง"),
    ("ทำวันนี้ให้ดีที่สุด แล้วพรุ่งนี้จะดีกว่าเดิม", "ไม่ทราบผู้แต่ง"),
    ("ไม่มีใครแก่เกินกว่าจะเรียนรู้สิ่งใหม่", "ไม่ทราบผู้แต่ง"),
]

FACTS = [
    "โรงเรียนสาธิตมหาวิทยาลัยศรีนครินทรวิโรฒ ประสานมิตร ก่อตั้งขึ้นเพื่อเป็นแหล่งฝึกปฏิบัติการสอนของนิสิตครู",
    "สมองมนุษย์ใช้พลังงานประมาณ 20% ของพลังงานทั้งหมดในร่างกาย ทั้งที่มีน้ำหนักเพียงราว 2% ของร่างกาย",
    "ผึ้งสามารถจดจำใบหน้ามนุษย์ได้",
    "หัวใจของกุ้งอยู่ที่บริเวณหัว ไม่ใช่ลำตัว",
    "แสงจากดวงอาทิตย์ใช้เวลาประมาณ 8 นาที 20 วินาที จึงจะมาถึงโลก",
    "ภาษาไทยมีพยัญชนะทั้งหมด 44 ตัว แต่ใช้แทนเสียงพยัญชนะเพียง 21 เสียง",
    "น้ำผึ้งไม่มีวันเสียถ้าเก็บรักษาอย่างถูกวิธี",
    "การจดโน้ตด้วยมือช่วยให้จดจำเนื้อหาได้ดีกว่าการพิมพ์",
]

JOKES = [
    "ครู: ทำไมนักเรียนถึงมาสาย?\nนักเรียน: เพราะป้ายบอกทางเขียนว่า 'ให้ชะลอความเร็ว' ครับ",
    "ทำไมหนังสือคณิตศาสตร์ถึงดูเศร้า? เพราะมันมีปัญหาเยอะมาก",
    "ทำไมคอมพิวเตอร์ถึงหนาว? เพราะมันลืมปิดวินโดว์!",
    "นักเรียน: ครูครับ ผมทำการบ้านไม่ทันเพราะไฟดับ\nครู: แล้วทำไมไม่ใช้ดินสอล่ะ?",
    "ทำไมผึ้งถึงเรียนเก่ง? เพราะมันอยู่ในระบบรังผึ้งตลอดเวลา!",
    "ทำไมโครงกระดูกไม่กล้าพูดหน้าห้อง? เพราะมันไม่มีความกล้า (guts)",
]

EIGHT_BALL_ANSWERS = [
    "ใช่แน่นอน ✅",
    "เป็นไปได้สูง 👍",
    "ไม่แน่ใจ ลองถามใหม่อีกครั้ง 🤔",
    "ไม่น่าจะใช่ ❌",
    "แน่นอนที่สุด 💯",
    "ตอนนี้ยังบอกไม่ได้ ⏳",
    "อย่าหวังมากไปเลย 😅",
    "สัญญาณดีมาก ✨",
    "ไม่ ❌",
    "ใช่ ✅",
]

COMPLIMENTS = [
    "คุณเก่งมากและมีความพยายามที่น่าชื่นชม! 🌟",
    "รอยยิ้มของคุณทำให้วันนี้สดใสขึ้นเยอะเลย 😊",
    "คุณเป็นเพื่อนที่ดีและน่ารักมาก 💖",
    "ความตั้งใจของคุณสร้างแรงบันดาลใจให้คนรอบข้างเสมอ 🔥",
    "คุณมีความคิดสร้างสรรค์ที่ยอดเยี่ยมมาก 🎨",
    "ขอบคุณที่เป็นส่วนหนึ่งที่ทำให้เซิร์ฟเวอร์นี้น่าอยู่ขึ้น 🏫",
]

NUMBER_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"]
POLL_DURATION_SECONDS = 60


class FunCog(commands.Cog, name="Fun"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command(name="quote")
    async def quote_cmd(self, ctx: commands.Context) -> None:
        """Show a random motivational quote."""
        text, author = random.choice(QUOTES)
        embed = make_embed(title="💬 คำคมประจำวัน", description=f'"{text}"\n— {author}', color=COLOR_INFO)
        await ctx.send(embed=embed)

    @commands.command(name="fact")
    async def fact_cmd(self, ctx: commands.Context) -> None:
        """Show a random fun fact."""
        embed = make_embed(title="🧠 รู้หรือไม่?", description=random.choice(FACTS), color=COLOR_INFO)
        await ctx.send(embed=embed)

    @commands.command(name="joke")
    async def joke_cmd(self, ctx: commands.Context) -> None:
        """Show a random school-appropriate joke."""
        embed = make_embed(title="😂 มุกฮา", description=random.choice(JOKES), color=COLOR_SUCCESS)
        await ctx.send(embed=embed)

    @commands.command(name="8ball")
    async def eight_ball_cmd(self, ctx: commands.Context, *, question: str) -> None:
        """Ask the magic 8-ball a question."""
        question = strip_quotes(question).strip()
        embed = make_embed(title="🎱 Magic 8-Ball", color=COLOR_INFO)
        embed.add_field(name="❓ คำถาม", value=question, inline=False)
        embed.add_field(name="🔮 คำตอบ", value=random.choice(EIGHT_BALL_ANSWERS), inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="dice")
    async def dice_cmd(self, ctx: commands.Context, sides: int = 6) -> None:
        """Roll an N-sided dice (default 6)."""
        if not (2 <= sides <= 1000):
            await ctx.send("❌ โปรดใส่จำนวนหน้าลูกเต๋าระหว่าง 2-1000")
            return
        result = random.randint(1, sides)
        embed = make_embed(
            title="🎲 ทอยลูกเต๋า",
            description=f"🎲 {ctx.author.display_name} ทอยได้: **{result}** (จาก 1-{sides})",
            color=COLOR_INFO,
        )
        await ctx.send(embed=embed)

    @commands.command(name="compliment")
    async def compliment_cmd(self, ctx: commands.Context, member: Optional[discord.Member] = None) -> None:
        """Send a random compliment to yourself or a mentioned user."""
        target = member or ctx.author
        compliment = random.choice(COMPLIMENTS)
        description = compliment if target.id == ctx.author.id else f"{target.mention} {compliment}"
        embed = make_embed(title="💝 คำชม", description=description, color=COLOR_SUCCESS)
        await ctx.send(embed=embed)

    @commands.command(name="poll")
    async def poll_cmd(self, ctx: commands.Context, question: str, *options: str) -> None:
        """Create a 60-second reaction poll with 2-4 options."""
        question = strip_quotes(question).strip()
        options = [strip_quotes(opt).strip() for opt in options]

        if not (2 <= len(options) <= 4):
            await ctx.send('❌ โปรดใส่ตัวเลือก 2-4 ตัวเลือก เช่น !poll "คำถาม" "ตัวเลือก1" "ตัวเลือก2"')
            return

        lines = [f"{NUMBER_EMOJIS[i]} {opt}" for i, opt in enumerate(options)]
        embed = make_embed(title=f"📊 {question}", description="\n".join(lines), color=COLOR_INFO)
        embed.set_footer(text=f"โหวตโดยกดอิโมจิ — ผลโหวตจะประกาศใน {POLL_DURATION_SECONDS} วินาที")
        message = await ctx.send(embed=embed)

        for i in range(len(options)):
            try:
                await message.add_reaction(NUMBER_EMOJIS[i])
            except discord.Forbidden:
                log.warning("Missing permission to add reactions for poll")
                break

        await asyncio.sleep(POLL_DURATION_SECONDS)

        try:
            message = await ctx.channel.fetch_message(message.id)
        except (discord.NotFound, discord.Forbidden):
            return

        counts = []
        for i in range(len(options)):
            reaction = discord.utils.get(message.reactions, emoji=NUMBER_EMOJIS[i])
            counts.append(max(reaction.count - 1, 0) if reaction else 0)

        result_lines = [f"{NUMBER_EMOJIS[i]} {options[i]} — **{counts[i]}** โหวต" for i in range(len(options))]
        if any(counts):
            winner_idx = max(range(len(options)), key=lambda i: counts[i])
            tail = f"\n\n🏆 ตัวเลือกที่ชนะ: **{options[winner_idx]}**"
        else:
            tail = "\n\nยังไม่มีใครโหวตเลย"

        result_embed = make_embed(
            title=f"📊 ผลโหวต: {question}",
            description="\n".join(result_lines) + tail,
            color=COLOR_SUCCESS,
        )
        await ctx.send(embed=result_embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(FunCog(bot))
