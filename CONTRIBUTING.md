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
- `db.py` — SQLite persistence (events + attendees, suggestions, feedback,
  social media channel + post log, event announcement channel). `schema.sql`
  is a human-readable mirror of the same tables; `db.py` is the source of
  truth and creates them automatically on startup. Adding a column to an
  existing table needs `_ensure_column(conn, table, column, ddl_type)` inside
  `init_db()` in addition to the `CREATE TABLE IF NOT EXISTS` — that
  statement is a no-op against a table that already exists (e.g. Railway's
  live database from before your change), so a bare schema edit will pass in
  local dev on a fresh `school_bot.db` and then silently not apply anywhere
  the table was already created. `_ensure_column` reads `PRAGMA table_info`
  and `ALTER TABLE ADD COLUMN`s only if missing, so it's safe to call every
  startup on both old and new databases.
- Event attendees are a proper join table (`event_attendees`), not a JSON
  array column, deliberately: two people reacting within the same instant
  would race on a read-modify-write of a JSON blob (both read the same
  array, both append, the second write clobbers the first's addition). The
  join table's `PRIMARY KEY (event_id, user_id)` makes concurrent
  joins/leaves atomic — `db.add_attendee` returns `False` on a duplicate
  instead of silently double-adding. Don't reintroduce a JSON attendee list.
- `date_utils.py` — Thai/English date & time parsing for the event system.
- `cogs/` — one file per feature area (`events_cog.py`, `fun_cog.py`,
  `feedback_cog.py`, `social_media_cog.py`), each with an `async def
  setup(bot)` that discord.py's `load_extension` calls.
- `!social` used to take a URL and scrape Instagram/Facebook for metadata
  (owned-account Graph API → Meta oEmbed → Open Graph fallback). That code
  (`utils/social_scraper.py`) was removed after confirming live that
  Instagram serves zero usable metadata to any non-browser request, even its
  own public embed endpoint — see git history for the investigation if
  you're tempted to rebuild URL-based scraping. `!social` is now a manual
  composer (button → modal), which needs no external HTTP calls at all.

## Making changes

- Admin-only commands must use `@commands.has_permissions(administrator=True)`
  — and note that a check on a `commands.group`'s own callback does **not**
  apply to its subcommands, so decorate each admin subcommand individually
  (see `event_create`/`event_delete` vs. the plain `event_group` in
  `cogs/events_cog.py`). This also applies to nested groups (`!event
  channel` inside `!event`) — every leaf command needs its own decorator.
- An announcement that's editable after posting (event embeds, suggestion
  embeds) needs its message ID stored in the DB at creation time, then a
  `_refresh_announcement`-style helper that re-fetches the row, rebuilds the
  embed from a single shared builder function (`build_event_embed` in
  `events_cog.py`), and calls `message.edit()`. Keep exactly one function
  that builds that embed — every code path (create, edit, join, leave,
  reaction listener) should call it, not duplicate its field list, or the
  views will drift out of sync with each other.
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
- A cog that needs an `aiohttp.ClientSession` (or any other resource that
  must be opened/closed around the bot's lifetime) should do it in
  `async def cog_load(self)` / `async def cog_unload(self)`, not in
  `__init__` — `__init__` can't be async and runs before the event loop
  guarantees are in place.
- `!event` and `!social` auto-delete the invoking command and every reply
  sent via `ctx.send()` a few seconds after the command finishes, via
  `common.track_replies_for_cleanup(ctx)` (called from `cog_before_invoke`)
  and `common.schedule_reply_cleanup(ctx, delay)` (called from
  `cog_after_invoke`). This only wraps `ctx.send` — a persistent
  announcement must go through `channel.send()` on the *resolved* channel
  object instead (as `event_create`/the social modal already do), never
  `ctx.send`, or it'll get deleted along with the confirmation messages.
  A command that needs to wait on a View/Modal before it can be considered
  "done" (see `SocialMediaCog.social_cmd`) must actually await that
  completion — including a hard ceiling via `asyncio.wait_for` in case the
  user opens a modal and abandons it — since `cog_after_invoke` only fires
  after the command coroutine returns, and firing early would delete the
  button/prompt message before the user could act on it.
- A Discord modal (`discord.ui.Modal`) can only contain text input fields —
  there's no file/image upload component. `!social` requires the image as an
  attachment on the triggering message instead and rejects the command
  up front if one isn't there, rather than offering an image-URL text field
  as a fallback (deliberately simplified — one input method, not two).
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
