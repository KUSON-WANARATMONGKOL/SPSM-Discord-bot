# Contributing

## Local setup

1. `pip install -r requirements.txt`
2. Copy `.env.example` to `.env` and add a test bot's token (do not use the
   production token for local development).
3. `python bot.py`

## Making changes

- Keep the section headers in `bot.py` (`CONFIGURATION`, `EVENTS`, `COMMANDS`,
  `TASKS`, etc.) — they make the single-file bot navigable.
- Admin-only commands must use `@commands.has_permissions(administrator=True)`.
- Any command that acts on another member (`kick`, `mute`, `warn`) must go
  through `can_act_on()` to block self-targeting and acting on equal/higher
  roles.
- User-facing errors are in Thai and follow the fixed strings already used in
  `on_command_error` — don't leak stack traces to users; log them with
  `log.exception` instead.
- Test manually against a private test server before opening a PR: run each
  changed command as both an admin and non-admin, and check the error path
  (missing args, bad member/role, missing permissions).

## Code style

- Type hints on function signatures where it helps readability.
- No unused imports, no debug `print()` statements — use the `log` logger.
- Prefer editing the in-memory dict shapes already in use (`leaderboard`,
  `warnings_log`, `role_select_messages`) over introducing new global state.
