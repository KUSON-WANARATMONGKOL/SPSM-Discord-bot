-- Reference mirror of the schema created by db.init_db() at bot startup.
-- This file is documentation only; db.py is the source of truth and creates
-- these tables automatically (CREATE TABLE IF NOT EXISTS) every time the
-- bot starts, so you never need to run this file by hand.

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    channel_id INTEGER,
    name TEXT NOT NULL,
    event_date TEXT NOT NULL,              -- 'YYYY-MM-DD'
    event_time TEXT NOT NULL,              -- 'HH:MM' (24-hour)
    description TEXT NOT NULL DEFAULT '',
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

CREATE TABLE IF NOT EXISTS suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending', -- pending | approved | rejected | considering
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
    guild_id INTEGER PRIMARY KEY,           -- one announcement channel per guild
    channel_id INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS social_posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    original_url TEXT NOT NULL,
    platform TEXT NOT NULL,                 -- 'instagram' | 'facebook'
    author_name TEXT,
    caption TEXT,
    image_url TEXT,
    likes_count INTEGER,                    -- real only when extraction_method is 'owned_*_api'; else NULL
    discord_message_id INTEGER,
    channel_id INTEGER,
    posted_timestamp TEXT NOT NULL,
    extraction_method TEXT                  -- 'owned_facebook_api' | 'owned_instagram_api' | 'meta_oembed' | 'open_graph'
);
