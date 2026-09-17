# SPSM Discord Bot

A Discord bot for school community management (Prasanmitr Demonstration
School): welcome messages, reaction-based role assignment, moderation tools,
announcements, a points/leaderboard system, an event system, fun/engagement
commands, a suggestion/feedback system, and a form-based social media
announcement composer. Built with [discord.py](https://discordpy.readthedocs.io/).

## Features

- **Welcome system** — greets new members in `#welcome` with their member number.
- **Role selection** — `!role_select` posts a reaction menu (🎓 Grade 10, 📚 Grade 11, 🏆 Grade 12).
- **Moderation** — `!kick`, `!mute`, `!warn`, `!clear`, all admin-only with hierarchy checks.
- **Announcements** — `!announce` posts a formatted embed.
- **Points & leaderboard** — `!addpoint`, `!leaderboard`.
- **Events** — `!event create/list/details/edit/join/leave/delete` + `!event channel`, with a live-updating announcement embed (image, attendee count, ✅/❌ join/leave reactions) and a 24h-before DM reminder for attendees.
- **Fun** — `!quote`, `!fact`, `!joke`, `!poll`, `!8ball`, `!dice`, `!compliment`.
- **Suggestions & feedback** — `!suggest`, `!feedback`, `!mysuggest`, `!suggestion list/status/delete` (Admin).
- **Social media announcements** — `!social`/`!embed` opens a form (button → Discord modal) to compose a title + description, attach an image, and post the result to the configured channel.
- **Utility** — `!hello`, `!ping`, `!server`, `!rules`, `!schedule`, `!help`.
- **Self-cleaning `!event`/`!social` commands** — a few seconds after either finishes, the user's command message and every reply the bot sent back to that channel are auto-deleted, keeping the channel tidy. The actual event/social announcement embed (posted to the configured channel) is never touched by this.

Run `!help` in your server for the full, categorized command list, or
`!help <command>` (e.g. `!help event`) for usage details on one command.

## Architecture

`bot.py` wires up the bot and keeps the original single-file commands
(welcome, moderation, points, announcements, help). Larger feature sets live
in their own cogs, loaded automatically on startup:

- [common.py](common.py) — shared embed/color/logging helpers used by `bot.py` and every cog.
- [db.py](db.py) — SQLite persistence layer (see [schema.sql](schema.sql) for the table layout).
- [date_utils.py](date_utils.py) — Thai/English date & time parsing for the event system.
- [cogs/events_cog.py](cogs/events_cog.py) — `!event ...` and the 24h reminder task.
- [cogs/fun_cog.py](cogs/fun_cog.py) — `!quote`, `!fact`, `!joke`, `!poll`, `!8ball`, `!dice`, `!compliment`.
- [cogs/feedback_cog.py](cogs/feedback_cog.py) — `!suggest`, `!feedback`, `!mysuggest`, `!suggestion ...`.
- [cogs/social_media_cog.py](cogs/social_media_cog.py) — `!social_channel ...`, `!social`/`!embed` (button + modal announcement composer).

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
   Manage Roles, Manage Messages, Read/Send Messages, Add Reactions, Embed
   Links, Manage Channels (needed to set mute permissions per channel, and to
   auto-create `#suggestions`/`#feedback` if they don't already exist).

5. **Run the bot**

   ```bash
   python bot.py
   ```

   If the token is missing or invalid, the bot logs a clear error and exits
   instead of crashing.

## New commands (events, fun, feedback, social media)

| Command | Who | Description |
|---|---|---|
| `!event channel set #channel` / `get` / `reset` | Admin | Configure where event announcements are posted. Required before `!event create` will work. |
| `!event create "name" "date" "time" "description" ["image url"]` | Admin | Create an event and post its announcement embed. Date: `DD/MM/YYYY`, `today`/`tomorrow` (or Thai `วันนี้`/`พรุ่งนี้`), or `next <weekday>` (or Thai `<weekday>หน้า`). Time: 24h `HH:MM`. Always quote each argument. An image can be a URL argument or a file attached to the same message. |
| `!event list` | Everyone | Next 10 upcoming events, soonest first. |
| `!event details <id>` | Everyone | Full details: description, attendees, time remaining. |
| `!event edit <id> "new description"` | Admin | Update an event's description; live-edits the posted announcement embed. |
| `!event join <id>` / `!event leave <id>`, or ✅/❌ on the announcement | Everyone | Register / cancel registration. Both the command and the reactions do the same thing, live-update the announcement's attendee count, and DM a confirmation. Un-reacting ✅ does **not** leave — only ❌ or `!event leave` do, so an accidental un-react can't silently drop someone. |
| `!event delete <id>` | Admin | Delete an event and remove its announcement message from Discord. |
| `!quote` / `!fact` / `!joke` | Everyone | Random Thai quote / fact / joke. |
| `!poll "question" "opt1" "opt2" ["opt3"] ["opt4"]` | Everyone | Reaction poll (2-4 options), tallies after 60 seconds. |
| `!8ball "question"` | Everyone | Magic 8-ball answer. |
| `!dice [sides]` | Everyone | Roll a dice (default 6-sided). |
| `!compliment [@user]` | Everyone | Send a random compliment. |
| `!suggest "text"` | Everyone | Submit a suggestion; posts to `#suggestions` (auto-created) with ✅/❌/💭 reaction voting. 30s cooldown. |
| `!suggest edit <id> "new text"` | Author | Edit your own suggestion within 5 minutes of posting. |
| `!feedback "text"` | Everyone | Submit feedback; posts to `#feedback` (auto-created). 30s cooldown. |
| `!mysuggest [@user]` | Everyone (others: Admin) | List your own suggestions and their status. |
| `!suggestion list [status]` | Admin | List all suggestions, optionally filtered (`pending`/`approved`/`rejected`/`considering`). |
| `!suggestion status <id> <status>` | Admin | Update a suggestion's status; DMs the author and updates the posted embed/reaction. |
| `!suggestion delete <id>` | Admin | Delete a suggestion and its posted message. |
| `!social_channel set #channel` | Admin | Set the channel `!social` posts announcements to. |
| `!social_channel get` / `!social_channel reset` | Admin | View / clear the configured channel. |
| `!social` (alias `!embed`) — optionally attach an image to this message | Everyone | Posts a button; clicking it opens a Discord form (title + description, and an image URL field if you didn't attach one) and submits directly to the configured channel. 30s cooldown. |

## Social media announcements: how it works

`!social` does **not** take a URL and does **not** fetch anything from
Instagram/Facebook — an earlier version tried scraping post metadata, but
testing confirmed Instagram serves zero usable data to any non-browser
request (not even its own public embed endpoint), and there's no reliable,
ToS-clean way around that short of logging into a real account (ban risk) or
running a headless browser that datacenter IPs like Railway's get blocked
from anyway. Rather than ship something unreliable, `!social` is a manual
composer instead:

1. Type `!social`, optionally with an image attached to that same message.
2. The bot replies with a **📝 กรอกรายละเอียดประกาศ** button (only you can click it).
3. Clicking it opens a Discord **modal** (popup form) with a Title field, a
   Description field, and — only if you didn't attach an image in step 1 —
   an optional Image URL field. (Discord modals can only contain text
   fields; there's no way to upload a file through one, which is why the
   attachment has to happen on the original message instead.)
4. Submitting the form immediately posts the resulting embed to the
   configured channel and confirms to you privately (ephemeral reply).

The button times out after 2 minutes if unclicked; if you click it but then
close the form without submitting, the command gives up after 5 minutes so
it doesn't hang forever.

## Notes on data storage

- **Persistent (SQLite, `school_bot.db`)**: events, event attendees, suggestions, feedback, the social media channel setting, and a log of posted social media announcements — see [db.py](db.py) / [schema.sql](schema.sql). The file is created automatically on first run and is git-ignored.
- **In-memory (resets on restart)**: points (`!addpoint`/`!leaderboard`) and warning history (`!warn`), stored in `bot.py`'s `leaderboard`/`warnings_log` dicts. This is intentional for now — swap them for SQLite tables (following the same pattern as `db.py`) if you need them to survive restarts too.

## Deployment

Any host that can run a long-lived Python process works: Railway, a VPS, or
your own machine (`python bot.py`, keep the process alive with a process
manager like `pm2` or a systemd service). Make sure the `DISCORD_TOKEN`
environment variable is set on the host — either via a `.env` file or the
platform's own environment variable settings.

On Railway specifically: attach a persistent volume mounted at the project
directory (or set `DB_PATH` in `db.py` to a path on that volume) so
`school_bot.db` survives redeploys — Railway's default filesystem is
ephemeral and a plain redeploy without a volume will reset events,
suggestions, feedback, and the social media channel setting.

## Testing checklist

Manual smoke test after changes (see also [CONTRIBUTING.md](CONTRIBUTING.md)):

- [ ] `!event create "Test" "tomorrow" "10:00" "desc"` before `!event channel set` → "channel not set" error, no crash.
- [ ] `!event channel set #channel` as admin works; as non-admin is rejected.
- [ ] `!event create "Test" "tomorrow" "10:00" "desc"` as admin → posts an announcement embed with ✅/❌ reactions in the configured channel; as non-admin → permission error.
- [ ] `!event create ... "desc" "https://.../image.jpg"` and separately `!event create ...` with an image attached to the same message → both show the image in the embed.
- [ ] `!event create` with a bad date (`"foo"`), bad time (`"99:99"`), a past date, or a malformed image URL → friendly Thai error, no crash.
- [ ] Clicking ✅ on the announcement joins (embed's attendee count updates live, DM confirmation sent); clicking ❌ leaves (count updates back down). Un-reacting ✅ does **not** change the count.
- [ ] `!event join <id>` / `!event leave <id>` do the same thing as the reactions and also update the live embed.
- [ ] `!event list` / `!event details <id>` show the created event; `!event edit <id> "new desc"` updates both the DB and the live announcement embed.
- [ ] `!event delete <id>` as admin removes the DB row **and** the announcement message from Discord; as non-admin is rejected.
- [ ] Two people clicking ✅ within the same second both end up correctly counted (no lost join from a race).
- [ ] Restart the bot and confirm the event/suggestion/feedback data in `school_bot.db` is still there.
- [ ] `!poll "q" "a" "b"` reacts with 1️⃣2️⃣, tallies votes after 60s.
- [ ] `!suggest "text"` creates `#suggestions` if missing, posts with reactions, DMs the author.
- [ ] `!suggest edit <id> "new"` within 5 minutes works; after 5 minutes is rejected.
- [ ] `!suggestion status <id> approved` as admin DMs the author and updates the embed/reaction.
- [ ] Spamming `!suggest`/`!feedback` twice within 30s triggers the cooldown message, not a crash.
- [ ] `!social` before `!social_channel set` → friendly "channel not set" error, no crash.
- [ ] `!social_channel set #channel` as admin works; as non-admin is rejected.
- [ ] `!social` with no attachment → button appears → clicking it opens a modal with 3 fields (title, description, image URL); submitting with a valid `https://...` image URL posts the embed with that image to the configured channel.
- [ ] `!social` with an image attached to that message → button → modal has only 2 fields (title, description, no image URL field); submitting posts the embed with the attached image.
- [ ] Submitting the modal's image URL field with something that doesn't start with `http://`/`https://` → ephemeral Thai error, no crash.
- [ ] Someone other than the command's author clicking the button gets an ephemeral "only the command user can" message and nothing opens for them.
- [ ] Leaving the button unclicked for 2 minutes, and separately opening the modal and closing it without submitting, both eventually let the command finish (no permanent hang).
- [ ] `!help` and `!help event` / `!help suggest` / `!help poll` / `!help social` show the new commands.
- [ ] For both `!event` and `!social` commands: a few seconds after the command finishes, the user's typed command and the bot's reply/prompt message in that channel disappear on their own — but the actual event/social announcement embed (posted to the configured channel) stays.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).
