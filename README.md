# SPSM Discord Bot

A Discord bot for school community management (Prasanmitr Demonstration
School): welcome messages, reaction-based role assignment, moderation tools,
announcements, a points/leaderboard system, an event system, fun/engagement
commands, a suggestion/feedback system, and Instagram/Facebook post
announcements. Built with [discord.py](https://discordpy.readthedocs.io/).

## Features

- **Welcome system** — greets new members in `#welcome` with their member number.
- **Role selection** — `!role_select` posts a reaction menu (🎓 Grade 10, 📚 Grade 11, 🏆 Grade 12).
- **Moderation** — `!kick`, `!mute`, `!warn`, `!clear`, all admin-only with hierarchy checks.
- **Announcements** — `!announce` posts a formatted embed.
- **Points & leaderboard** — `!addpoint`, `!leaderboard`.
- **Events** — `!event create/list/details/edit/join/leave/delete` + `!event channel`, with a live-updating announcement embed (image, attendee count, ✅/❌ join/leave reactions) and a 24h-before DM reminder for attendees.
- **Fun** — `!quote`, `!fact`, `!joke`, `!poll`, `!8ball`, `!dice`, `!compliment`.
- **Suggestions & feedback** — `!suggest`, `!feedback`, `!mysuggest`, `!suggestion list/status/delete` (Admin).
- **Social media announcements** — `!social`/`!embed` turns an Instagram/Facebook post link into an announcement embed, with a confirm/cancel step before posting. See [limitations](#social-media-embeds-limitations) below.
- **Utility** — `!hello`, `!ping`, `!server`, `!rules`, `!schedule`, `!help`.

Run `!help` in your server for the full, categorized command list, or
`!help <command>` (e.g. `!help event`) for usage details on one command.

## Architecture

`bot.py` wires up the bot and keeps the original single-file commands
(welcome, moderation, points, announcements, help). Larger feature sets live
in their own cogs, loaded automatically on startup:

- [common.py](common.py) — shared embed/color/logging helpers used by `bot.py` and every cog.
- [db.py](db.py) — SQLite persistence layer (see [schema.sql](schema.sql) for the table layout).
- [date_utils.py](date_utils.py) — Thai/English date & time parsing for the event system.
- [utils/social_scraper.py](utils/social_scraper.py) — Instagram/Facebook metadata extraction (oEmbed + Open Graph fallback) for the social media feature.
- [cogs/events_cog.py](cogs/events_cog.py) — `!event ...` and the 24h reminder task.
- [cogs/fun_cog.py](cogs/fun_cog.py) — `!quote`, `!fact`, `!joke`, `!poll`, `!8ball`, `!dice`, `!compliment`.
- [cogs/feedback_cog.py](cogs/feedback_cog.py) — `!suggest`, `!feedback`, `!mysuggest`, `!suggestion ...`.
- [cogs/social_media_cog.py](cogs/social_media_cog.py) — `!social_channel ...`, `!social`/`!embed`.

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

   `.env` is git-ignored — never commit your real token. `META_APP_ID` /
   `META_APP_SECRET` are optional (see
   [Social media embeds: limitations](#social-media-embeds-limitations)).

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
| `!social_channel set #channel` | Admin | Set the channel `!social` posts converted embeds to. |
| `!social_channel get` / `!social_channel reset` | Admin | View / clear the configured channel. |
| `!social <url>` (alias `!embed <url>`) | Everyone | Fetch metadata for an Instagram/Facebook post link, preview it as an embed, and post to the configured channel after you click ✅. 30s cooldown. See limitations below. |

## Social media embeds: limitations

`!social`/`!embed` does **not** log into Instagram or Facebook, does not use
unofficial scraping libraries (e.g. `instagrapi`) or Instagram's old
`?__a=1` JSON endpoint (Meta closed unauthenticated access to it years ago),
and does not use a headless browser (Playwright/Selenium) to force its way
past their anti-bot walls — all of that requires either real account
credentials (account-ban risk, ToS violation) or heavy infra that Meta's
datacenter-IP detection likely defeats anyway on a host like Railway. Instead
it tries three legitimate sources in order, each filling in only what the
previous one missed (see [utils/social_scraper.py](utils/social_scraper.py)):

1. **Owned-account Graph API** — if you've linked the school's own Facebook
   Page + Instagram Business account (see setup below), this queries that
   account directly with a real access token the school controls. This is
   the only strategy that returns **real like counts**, and it only works
   for posts *from that account*.
2. **Meta's oEmbed Read API**, if you set `META_APP_ID` + `META_APP_SECRET`
   in `.env` (from a [Meta developer app](https://developers.facebook.com/apps)).
   Meta gates this behind **App Review** for posts you don't own, so unless
   you complete that review, these calls will just fail silently. Skipped if
   strategy 1 already found the post.
3. **Open Graph tags** on the post's public page (`og:image`, `og:description`,
   `og:title`) — the same technique Discord's own link previews use. Both
   Instagram and Facebook frequently serve a login wall or a stripped page to
   non-browser requests, so this can return partial data or fail outright,
   especially for Instagram. Skipped if an earlier strategy already found
   both an image and a caption.

Practical consequences:

- **Like counts are only ever real for the school's own linked account**
  (strategy 1). For any other post, the embed shows "ไม่มีข้อมูล" (no data)
  for likes rather than a fabricated number.
- **Facebook tends to work better than Instagram** for strategies 2-3, since
  Instagram's bot-detection is more aggressive.
- **Failures are expected, not bugs** — if `!social` replies "ไม่สามารถดึงข้อมูลโพสต์ได้"
  (couldn't fetch post data), it usually means the post isn't from the linked
  account and the platform blocked the fallback request (or the post is
  private), not that something is broken.

### Social media embeds: setting up the owned-account API

Only worth doing if `!social` will mainly share the school's **own**
Instagram/Facebook posts — it doesn't help with arbitrary third-party posts.
One-time setup, done by whoever administers the school's Page/account:

1. Make sure the Instagram account is a **Business or Creator account**
   linked to a **Facebook Page** you administer (Instagram app → Settings →
   Account type, then Settings → Linked accounts → Facebook).
2. Create an app at the [Meta Developer Portal](https://developers.facebook.com/apps),
   add the "Facebook Login" product (no App Review needed for your own
   Page/account — review is only required for other people's content).
3. Use the [Graph API Explorer](https://developers.facebook.com/tools/explorer/)
   to generate a **User Access Token** with `pages_show_list`,
   `pages_read_engagement`, and `instagram_basic` permissions, logging in as
   the account that administers the Page.
4. Exchange it for a **long-lived token**: `GET /oauth/access_token?grant_type=fb_exchange_token&client_id={app-id}&client_secret={app-secret}&fb_exchange_token={short-lived-token}`.
5. Get the Page's access token from that long-lived user token: `GET /me/accounts` and copy the `access_token` for your Page — this is `META_PAGE_ACCESS_TOKEN`. Page tokens obtained this way don't expire as long as the user token behind them stays valid.
6. Note the Page's `id` (`META_PAGE_ID`) from that same `/me/accounts` response.
7. Get the linked Instagram Business Account ID: `GET /{page-id}?fields=instagram_business_account&access_token={page-access-token}` — that's `META_IG_USER_ID`.
8. Put all three in `.env` and restart the bot.

## Notes on data storage

- **Persistent (SQLite, `school_bot.db`)**: events, event attendees, suggestions, feedback, the social media channel setting, and a log of posted social media links — see [db.py](db.py) / [schema.sql](schema.sql). The file is created automatically on first run and is git-ignored.
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
- [ ] `!social <url>` before `!social_channel set` → friendly "channel not set" error, no crash.
- [ ] `!social_channel set #channel` as admin works; as non-admin is rejected.
- [ ] `!social <a real public Facebook post URL>` → preview embed with ✅/❌ buttons; ✅ posts to the configured channel, ❌ cancels, and letting it sit for 90s without clicking shows "หมดเวลา".
- [ ] `!social <a private or malformed URL>` → "ไม่สามารถดึงข้อมูลโพสต์ได้" error, no crash (Instagram failing here is expected — see limitations above).
- [ ] `!social <non-Instagram/Facebook URL>` → "URL ไม่ถูกต้อง" error.
- [ ] Posting the same URL twice shows the "เคยถูกโพสต์ไปแล้ว" warning on the second attempt.
- [ ] `!help` and `!help event` / `!help suggest` / `!help poll` / `!help social` show the new commands.
- [ ] If you've set up the owned-account API (`META_PAGE_ID`/`META_IG_USER_ID`/`META_PAGE_ACCESS_TOKEN`): `!social <a post from that account>` shows a **real** like count in the preview, and the bot log shows `extraction_method=owned_facebook_api` / `owned_instagram_api`. A post from a *different* account should skip straight to oEmbed/Open Graph instead (no likes).

## Not implemented (available on request)

The original feature spec also described: auto-refreshing like counts on a
daily timer, `!social <url> --schedule "HH:MM"` scheduled posting, posting to
multiple channels in one command, and a `--message` flag for a custom caption
above the embed. These were left out of this pass to keep the social media
feature's first version focused — ask if you want any of them added.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).
