# Lavalink audio server for the school-discord-bot music player (cogs/music_cog.py).
# Deployed as its OWN Railway service (Root Directory = "lavalink"), separate
# from the bot service — Lavalink is a Java process, not part of the Python bot.
#
# Built on the official published image, which already contains the Lavalink
# jar, a non-root "lavalink" user, and the correct entrypoint/workdir — this
# just layers our application.yml on top.
FROM ghcr.io/lavalink-devs/lavalink:4.0.8

# Config is committed to git (see application.yml) but contains NO secrets —
# the actual password is injected at runtime via the LAVALINK_SERVER_PASSWORD
# environment variable (Spring Boot placeholder resolution), set in Railway's
# Variables dashboard, never in this file or in git.
COPY --chown=lavalink:lavalink application.yml /opt/Lavalink/application.yml

EXPOSE 2333
