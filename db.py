"""SQLite persistence layer for the SPSM Bot (events, suggestions, feedback).

Single-file SQLite database (school_bot.db) stored next to bot.py. Works
as-is on Railway's persistent volume; if the filesystem is ephemeral on your
host, mount a volume at this path or point DB_PATH at one via an env var.

See schema.sql for a human-readable mirror of the table definitions below.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from date_utils import BANGKOK_TZ

DB_PATH = Path(__file__).resolve().parent / "school_bot.db"
log = logging.getLogger("school_bot.db")


@contextmanager
def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER,
                name TEXT NOT NULL,
                event_date TEXT NOT NULL,
                event_time TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                image_url TEXT,
                announcement_channel_id INTEGER,
                announcement_message_id INTEGER,
                created_by_id INTEGER NOT NULL,
                created_timestamp TEXT NOT NULL,
                reminder_sent INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS event_attendees (
                event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                user_id INTEGER NOT NULL,
                joined_timestamp TEXT NOT NULL,
                PRIMARY KEY (event_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS event_channels (
                guild_id INTEGER PRIMARY KEY,
                channel_id INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS suggestions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                channel_id INTEGER,
                message_id INTEGER,
                votes_approve INTEGER NOT NULL DEFAULT 0,
                votes_reject INTEGER NOT NULL DEFAULT 0,
                votes_considering INTEGER NOT NULL DEFAULT 0,
                edited_timestamp TEXT
            );

            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                channel_id INTEGER,
                message_id INTEGER,
                edited_timestamp TEXT
            );

            CREATE TABLE IF NOT EXISTS social_channels (
                guild_id INTEGER PRIMARY KEY,
                channel_id INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS social_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                original_url TEXT NOT NULL,
                platform TEXT NOT NULL,
                author_name TEXT,
                caption TEXT,
                image_url TEXT,
                likes_count INTEGER,
                discord_message_id INTEGER,
                channel_id INTEGER,
                posted_timestamp TEXT NOT NULL,
                extraction_method TEXT
            );
            """
        )
        # Additive migration for databases created before these columns existed.
        # CREATE TABLE IF NOT EXISTS above is a no-op on an existing table, so
        # older deployments need ALTER TABLE to pick up new event columns.
        _ensure_column(conn, "events", "image_url", "TEXT")
        _ensure_column(conn, "events", "announcement_channel_id", "INTEGER")
        _ensure_column(conn, "events", "announcement_message_id", "INTEGER")
    log.info("Database ready at %s", DB_PATH)


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl_type: str) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")


def _now_iso() -> str:
    return datetime.now(BANGKOK_TZ).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


def create_event(
    guild_id: int,
    channel_id: int,
    name: str,
    event_date: str,
    event_time: str,
    description: str,
    created_by_id: int,
    image_url: Optional[str] = None,
) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO events
                (guild_id, channel_id, name, event_date, event_time, description, image_url,
                 created_by_id, created_timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (guild_id, channel_id, name, event_date, event_time, description, image_url, created_by_id, _now_iso()),
        )
        return cursor.lastrowid


def set_event_announcement_message(event_id: int, channel_id: int, message_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE events SET announcement_channel_id = ?, announcement_message_id = ? WHERE id = ?",
            (channel_id, message_id, event_id),
        )


def get_event_by_message_id(message_id: int) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM events WHERE announcement_message_id = ?", (message_id,)
        ).fetchone()


def update_event_description(event_id: int, guild_id: int, new_description: str) -> bool:
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE events SET description = ? WHERE id = ? AND guild_id = ?",
            (new_description, event_id, guild_id),
        )
        return cursor.rowcount > 0


def get_event(event_id: int, guild_id: int) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM events WHERE id = ? AND guild_id = ?", (event_id, guild_id)
        ).fetchone()


def list_upcoming_events(guild_id: int, max_days: int = 30) -> list[sqlite3.Row]:
    now = datetime.now(BANGKOK_TZ)
    now_str = now.strftime("%Y-%m-%d %H:%M")
    horizon_str = (now + timedelta(days=max_days)).strftime("%Y-%m-%d %H:%M")
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT * FROM events
            WHERE guild_id = ? AND (event_date || ' ' || event_time) BETWEEN ? AND ?
            ORDER BY event_date ASC, event_time ASC
            """,
            (guild_id, now_str, horizon_str),
        ).fetchall()


def delete_event(event_id: int, guild_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM events WHERE id = ? AND guild_id = ?", (event_id, guild_id))
        return cursor.rowcount > 0


def add_attendee(event_id: int, user_id: int) -> bool:
    """Register a user for an event. Returns False if already registered."""
    with get_connection() as conn:
        try:
            conn.execute(
                "INSERT INTO event_attendees (event_id, user_id, joined_timestamp) VALUES (?, ?, ?)",
                (event_id, user_id, _now_iso()),
            )
            return True
        except sqlite3.IntegrityError:
            return False


def remove_attendee(event_id: int, user_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM event_attendees WHERE event_id = ? AND user_id = ?", (event_id, user_id)
        )
        return cursor.rowcount > 0


def get_attendee_ids(event_id: int) -> list[int]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT user_id FROM event_attendees WHERE event_id = ?", (event_id,)
        ).fetchall()
    return [row["user_id"] for row in rows]


def count_attendees(event_id: int) -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM event_attendees WHERE event_id = ?", (event_id,)
        ).fetchone()
    return row["c"] if row else 0


def get_events_needing_reminder() -> list[sqlite3.Row]:
    """Events starting within the next 24h that haven't been reminded yet."""
    now = datetime.now(BANGKOK_TZ)
    now_str = now.strftime("%Y-%m-%d %H:%M")
    horizon_str = (now + timedelta(hours=24)).strftime("%Y-%m-%d %H:%M")
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT * FROM events
            WHERE reminder_sent = 0 AND (event_date || ' ' || event_time) BETWEEN ? AND ?
            """,
            (now_str, horizon_str),
        ).fetchall()


def mark_reminder_sent(event_id: int) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE events SET reminder_sent = 1 WHERE id = ?", (event_id,))


def set_event_channel(guild_id: int, channel_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO event_channels (guild_id, channel_id) VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET channel_id = excluded.channel_id
            """,
            (guild_id, channel_id),
        )


def get_event_channel(guild_id: int) -> Optional[int]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT channel_id FROM event_channels WHERE guild_id = ?", (guild_id,)
        ).fetchone()
    return row["channel_id"] if row else None


def clear_event_channel(guild_id: int) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM event_channels WHERE guild_id = ?", (guild_id,))


# ---------------------------------------------------------------------------
# Suggestions
# ---------------------------------------------------------------------------


def create_suggestion(guild_id: int, user_id: int, text: str) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO suggestions (guild_id, user_id, text, timestamp, status) VALUES (?, ?, ?, ?, 'pending')",
            (guild_id, user_id, text, _now_iso()),
        )
        return cursor.lastrowid


def set_suggestion_message(suggestion_id: int, channel_id: int, message_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE suggestions SET channel_id = ?, message_id = ? WHERE id = ?",
            (channel_id, message_id, suggestion_id),
        )


def get_suggestion(suggestion_id: int, guild_id: Optional[int] = None) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        if guild_id is None:
            return conn.execute("SELECT * FROM suggestions WHERE id = ?", (suggestion_id,)).fetchone()
        return conn.execute(
            "SELECT * FROM suggestions WHERE id = ? AND guild_id = ?", (suggestion_id, guild_id)
        ).fetchone()


def get_suggestion_by_message(message_id: int) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute("SELECT * FROM suggestions WHERE message_id = ?", (message_id,)).fetchone()


def list_suggestions(guild_id: int, status: Optional[str] = None) -> list[sqlite3.Row]:
    with get_connection() as conn:
        if status:
            return conn.execute(
                "SELECT * FROM suggestions WHERE guild_id = ? AND status = ? ORDER BY id DESC",
                (guild_id, status),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM suggestions WHERE guild_id = ? ORDER BY id DESC", (guild_id,)
        ).fetchall()


def list_user_suggestions(guild_id: int, user_id: int) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM suggestions WHERE guild_id = ? AND user_id = ? ORDER BY id DESC",
            (guild_id, user_id),
        ).fetchall()


def update_suggestion_status(suggestion_id: int, status: str) -> bool:
    with get_connection() as conn:
        cursor = conn.execute("UPDATE suggestions SET status = ? WHERE id = ?", (status, suggestion_id))
        return cursor.rowcount > 0


def update_suggestion_votes(suggestion_id: int, approve: int, reject: int, considering: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE suggestions SET votes_approve = ?, votes_reject = ?, votes_considering = ? WHERE id = ?",
            (approve, reject, considering, suggestion_id),
        )


def edit_suggestion_text(suggestion_id: int, new_text: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE suggestions SET text = ?, edited_timestamp = ? WHERE id = ?",
            (new_text, _now_iso(), suggestion_id),
        )


def delete_suggestion(suggestion_id: int, guild_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM suggestions WHERE id = ? AND guild_id = ?", (suggestion_id, guild_id)
        )
        return cursor.rowcount > 0


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------


def create_feedback(guild_id: int, user_id: int, text: str) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO feedback (guild_id, user_id, text, timestamp) VALUES (?, ?, ?, ?)",
            (guild_id, user_id, text, _now_iso()),
        )
        return cursor.lastrowid


def set_feedback_message(feedback_id: int, channel_id: int, message_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE feedback SET channel_id = ?, message_id = ? WHERE id = ?",
            (channel_id, message_id, feedback_id),
        )


# ---------------------------------------------------------------------------
# Social media announcements
# ---------------------------------------------------------------------------


def set_social_channel(guild_id: int, channel_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO social_channels (guild_id, channel_id) VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET channel_id = excluded.channel_id
            """,
            (guild_id, channel_id),
        )


def get_social_channel(guild_id: int) -> Optional[int]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT channel_id FROM social_channels WHERE guild_id = ?", (guild_id,)
        ).fetchone()
    return row["channel_id"] if row else None


def clear_social_channel(guild_id: int) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM social_channels WHERE guild_id = ?", (guild_id,))


def create_social_post(
    guild_id: int,
    user_id: int,
    original_url: str,
    platform: str,
    author_name: Optional[str],
    caption: Optional[str],
    image_url: Optional[str],
    likes_count: Optional[int],
    discord_message_id: int,
    channel_id: int,
    extraction_method: Optional[str] = None,
) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO social_posts
                (guild_id, user_id, original_url, platform, author_name, caption, image_url,
                 likes_count, discord_message_id, channel_id, posted_timestamp, extraction_method)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                user_id,
                original_url,
                platform,
                author_name,
                caption,
                image_url,
                likes_count,
                discord_message_id,
                channel_id,
                _now_iso(),
                extraction_method,
            ),
        )
        return cursor.lastrowid


def find_social_post_by_url(guild_id: int, url: str) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM social_posts WHERE guild_id = ? AND original_url = ? ORDER BY id DESC LIMIT 1",
            (guild_id, url),
        ).fetchone()
