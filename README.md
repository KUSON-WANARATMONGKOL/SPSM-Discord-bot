# School Discord Bot

A Discord bot for school community management: welcome messages, reaction-based
role assignment, moderation tools, announcements, and a points/leaderboard
system. Built with [discord.py](https://discordpy.readthedocs.io/).

## Features

- **Welcome system** — greets new members in `#welcome` with their member number.
- **Role selection** — `!role_select` posts a reaction menu (🎓 Grade 10, 📚 Grade 11, 🏆 Grade 12).
- **Moderation** — `!kick`, `!mute`, `!warn`, `!clear`, all admin-only with hierarchy checks.
- **Announcements** — `!announce` posts a formatted embed.
- **Points & leaderboard** — `!addpoint`, `!leaderboard`.
- **Utility** — `!hello`, `!ping`, `!server`, `!rules`, `!schedule`, `!help`.

Run `!help` in your server for the full, categorized command list.

## Setup

1. **Create a Discord application and bot**
   - Go to the [Discord Developer Portal](https://discord.com/developers/applications).
   - Create a New Application → Bot → Reset Token, and copy the token.
   - Under **Privileged Gateway Intents**, enable **Server Members Intent** and
     **Message Content Intent**.

2. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment variables**

   Copy `.env.example` to `.env` and fill in your bot token:

   ```bash
   cp .env.example .env
   ```

   ```
   DISCORD_TOKEN=your_bot_token_here
   WELCOME_CHANNEL=welcome
   ```

   `.env` is git-ignored — never commit your real token.

4. **Invite the bot to your server**

   In the Developer Portal, go to OAuth2 → URL Generator, select the `bot` and
   `applications.commands` scopes, and grant at minimum: Kick Members,
   Manage Roles, Manage Messages, Read/Send Messages, Add Reactions,
   Manage Channels (needed to set mute permissions per channel).

5. **Run the bot**

   ```bash
   python bot.py
   ```

   If the token is missing or invalid, the bot logs a clear error and exits
   instead of crashing.

## Notes on data storage

Points (`!addpoint`/`!leaderboard`) and warning history (`!warn`) are stored
**in memory** and reset when the bot restarts. This is intentional for a
simple first deployment — for persistent data across restarts, swap the
in-memory dictionaries in `bot.py` (`leaderboard`, `warnings_log`) for a real
database (MongoDB, PostgreSQL, SQLite, etc.).

## Deployment

Any host that can run a long-lived Python process works: Railway, a VPS, or
your own machine (`python bot.py`, keep the process alive with a process
manager like `pm2` or a systemd service). Make sure the `DISCORD_TOKEN`
environment variable is set on the host — either via a `.env` file or the
platform's own environment variable settings.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).
