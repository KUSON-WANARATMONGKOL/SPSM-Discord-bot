# Contributing

## Local setup

1. `pip install -r requirements.txt`
2. Copy `.env.example` to `.env` and add a test bot's token (do not use the
   production token for local development).
3. `python bot.py`

## Project layout

- `bot.py` — bot setup, the original single-file commands (welcome,
  moderation, points, announcements, help), and cog loading via
  `bot.setup_hook`.
- `common.py` — shared embed/color/logging helpers used by `bot.py` and every
  cog. Import from here instead of duplicating `make_embed`/`log_action`/etc.
  Do **not** `import bot` from a cog or `common.py` — running `bot.py`
  directly makes it the `__main__` module, so a plain `import bot` elsewhere
  would re-execute the whole file as a second module and create a second
  `Bot` instance.
- `db.py` — SQLite persistence (events, suggestions, feedback, social media
  channel + post log). `schema.sql` is a human-readable mirror of the same
  tables; `db.py` is the source of truth and creates them automatically on
  startup.
- `date_utils.py` — Thai/English date & time parsing for the event system.
- `utils/social_scraper.py` — Instagram/Facebook metadata extraction: owned-
  account Graph API → Meta oEmbed → Open Graph fallback, in that order, for
  the social media feature. See the module docstring before changing the
  data sources — it explains why login-based scraping (e.g. `instagrapi`),
  Instagram's old `?__a=1` JSON endpoint, and headless-browser rendering are
  all deliberately not used, and why like counts are only ever real for
  posts from the school's own linked account (owned-account strategy).
- `cogs/` — one file per feature area (`events_cog.py`, `fun_cog.py`,
  `feedback_cog.py`, `social_media_cog.py`), each with an `async def
  setup(bot)` that discord.py's `load_extension` calls.

## Making changes

- Admin-only commands must use `@commands.has_permissions(administrator=True)`
  — and note that a check on a `commands.group`'s own callback does **not**
  apply to its subcommands, so decorate each admin subcommand individually
  (see `event_create`/`event_delete` vs. the plain `event_group` in
  `cogs/events_cog.py`).
- Any command that acts on another member (`kick`, `mute`, `warn`) must go
  through `can_act_on()` to block self-targeting and acting on equal/higher
  roles.
- User-facing errors are in Thai and follow the fixed strings already used in
  `on_command_error` — don't leak stack traces to users; log them with
  `log.exception` instead.
- Command parameters that discord.py resolves at runtime (converters like
  `Optional[discord.Member]`) must use `typing.Optional[X]`, not the `X | None`
  syntax — the project targets Python 3.8+, and `X | None` needs 3.10+ to
  evaluate even under `from __future__ import annotations`.
- New persistent state goes in SQLite via `db.py` (add a table + helper
  functions there, mirror it in `schema.sql`), not a new in-memory dict.
- A cog that needs an `aiohttp.ClientSession` should open it in
  `async def cog_load(self)` and close it in `async def cog_unload(self)`
  (see `SocialMediaCog`), not in `__init__` — `__init__` can't be async and
  runs before the event loop guarantees are in place.
- Test manually against a private test server before opening a PR: run each
  changed command as both an admin and non-admin, and check the error path
  (missing args, bad member/role, missing permissions, bad date/time for
  `!event create`).

## Code style

- Type hints on function signatures where it helps readability.
- No unused imports, no debug `print()` statements — use the module's `log`
  logger (`logging.getLogger("school_bot.<area>")` in cogs).
- Prefer editing the in-memory dict shapes already in use in `bot.py`
  (`leaderboard`, `warnings_log`, `role_select_messages`) over introducing new
  global state; for anything that should survive a restart, use `db.py`
  instead.
