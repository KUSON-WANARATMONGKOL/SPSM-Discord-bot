"""Best-effort metadata extraction for Instagram/Facebook post URLs.

There is no reliable, ToS-clean way to get full metadata (and specifically
like counts) for an arbitrary public Instagram/Facebook post from the
outside. This module deliberately sticks to two legitimate sources and
merges whatever they return instead of pretending to have data it doesn't:

1. Meta's oEmbed Read API (instagram_oembed / oembed_post), if the admin has
   configured a Meta developer app (META_APP_ID + META_APP_SECRET env vars).
   This requires Meta App Review to work for posts you don't own, so many
   admins won't have it — that's expected, not a bug.
2. Open Graph meta tags on the post's public page (the same og:image /
   og:description technique Discord's own link unfurling uses). Instagram
   and Facebook both increasingly serve a login wall or a stripped-down page
   to non-browser requests, so this frequently returns partial data or
   nothing at all — callers must handle that gracefully.

Neither source exposes like counts for posts you don't administer, so
`likes_count` is always left as None here; the UI should show "no data"
rather than inventing a number.

Deliberately NOT implemented: logging into a real Instagram account via an
unofficial library (e.g. instagrapi) to scrape post data. That requires
storing real account credentials in a 24/7 bot, risks that account being
banned, and is against Instagram's Terms of Service around automated data
collection.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import aiohttp
from bs4 import BeautifulSoup

log = logging.getLogger("school_bot.social_scraper")

META_GRAPH_VERSION = "v19.0"
META_APP_ID = os.getenv("META_APP_ID")
META_APP_SECRET = os.getenv("META_APP_SECRET")
META_ACCESS_TOKEN = f"{META_APP_ID}|{META_APP_SECRET}" if META_APP_ID and META_APP_SECRET else None

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "th,en;q=0.9",
}

HTTP_TIMEOUT = aiohttp.ClientTimeout(total=10)

INSTAGRAM_URL_RE = re.compile(r"^https?://(www\.)?instagram\.com/(p|reel)/[\w-]+/?", re.IGNORECASE)
FACEBOOK_URL_RE = re.compile(r"^https?://(www\.|m\.)?facebook\.com/\S+", re.IGNORECASE)

# Deliberately not `#\w+`: Python's \w excludes Thai combining vowel/tone
# marks (Unicode category Mn, e.g. "ี" in "โรงเรียน"), which would truncate
# almost any real Thai hashtag. Match everything after '#' up to the next
# whitespace, '#', or '@' instead.
HASHTAG_RE = re.compile(r"#[^\s#@]+")


class ScrapingError(Exception):
    """Raised when no usable metadata could be extracted for a URL."""


@dataclass
class SocialPost:
    platform: str  # "instagram" | "facebook"
    original_url: str
    author_name: Optional[str] = None
    author_avatar: Optional[str] = None
    caption: Optional[str] = None
    image_url: Optional[str] = None
    likes_count: Optional[int] = None
    post_date: Optional[datetime] = None
    hashtags: list = field(default_factory=list)

    def merge(self, other: "SocialPost") -> None:
        """Fill in only the fields this post is still missing, from `other`."""
        for attr in ("author_name", "author_avatar", "caption", "image_url", "likes_count", "post_date"):
            if getattr(self, attr) is None and getattr(other, attr) is not None:
                setattr(self, attr, getattr(other, attr))
        if not self.hashtags and other.hashtags:
            self.hashtags = other.hashtags

    def has_any_data(self) -> bool:
        return any([self.author_name, self.caption, self.image_url])


def classify_url(url: str) -> Optional[str]:
    """Return 'instagram', 'facebook', or None if the URL isn't a supported post link."""
    url = url.strip()
    if INSTAGRAM_URL_RE.match(url):
        return "instagram"
    if FACEBOOK_URL_RE.match(url):
        return "facebook"
    return None


_TRAILING_PUNCTUATION = ".,!?;:\"')]}。！？"  # incl. Thai/CJK full-width punctuation


def extract_hashtags(text: Optional[str]) -> list:
    if not text:
        return []
    return [tag.rstrip(_TRAILING_PUNCTUATION) for tag in HASHTAG_RE.findall(text)]


async def _fetch_json(session: aiohttp.ClientSession, url: str, params: dict) -> Optional[dict]:
    try:
        async with session.get(url, params=params, timeout=HTTP_TIMEOUT) as resp:
            if resp.status != 200:
                return None
            return await resp.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return None


async def _try_instagram_oembed(session: aiohttp.ClientSession, url: str) -> Optional[SocialPost]:
    if not META_ACCESS_TOKEN:
        return None
    data = await _fetch_json(
        session,
        f"https://graph.facebook.com/{META_GRAPH_VERSION}/instagram_oembed",
        {"url": url, "access_token": META_ACCESS_TOKEN, "fields": "author_name,thumbnail_url,title"},
    )
    if not data:
        return None
    caption = data.get("title")
    return SocialPost(
        platform="instagram",
        original_url=url,
        author_name=data.get("author_name"),
        image_url=data.get("thumbnail_url"),
        caption=caption,
        hashtags=extract_hashtags(caption),
    )


async def _try_facebook_oembed(session: aiohttp.ClientSession, url: str) -> Optional[SocialPost]:
    if not META_ACCESS_TOKEN:
        return None
    data = await _fetch_json(
        session,
        f"https://graph.facebook.com/{META_GRAPH_VERSION}/oembed_post",
        {"url": url, "access_token": META_ACCESS_TOKEN},
    )
    if not data:
        return None
    return SocialPost(
        platform="facebook",
        original_url=url,
        author_name=data.get("author_name"),
    )


async def _try_open_graph(session: aiohttp.ClientSession, url: str) -> Optional[SocialPost]:
    try:
        async with session.get(url, headers=BROWSER_HEADERS, timeout=HTTP_TIMEOUT, allow_redirects=True) as resp:
            if resp.status != 200:
                return None
            html = await resp.text(errors="ignore")
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return None

    soup = BeautifulSoup(html, "html.parser")

    def meta(*props: str) -> Optional[str]:
        for prop in props:
            tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
            if tag and tag.get("content"):
                return tag["content"]
        return None

    image = meta("og:image", "og:image:secure_url")
    description = meta("og:description")
    title = meta("og:title")

    if not any([image, description, title]):
        return None

    platform = "instagram" if "instagram.com" in url else "facebook"
    return SocialPost(
        platform=platform,
        original_url=url,
        image_url=image,
        caption=description or title,
        author_name=title,
        hashtags=extract_hashtags(description),
    )


async def extract_post(session: aiohttp.ClientSession, url: str, platform: str) -> SocialPost:
    """Try oEmbed then Open Graph, merging whatever each source returns.

    Raises ScrapingError if neither source produced any usable data (e.g. the
    post is private, the URL is dead, or the platform blocked the request).
    """
    post = SocialPost(platform=platform, original_url=url)

    oembed_fetcher = _try_instagram_oembed if platform == "instagram" else _try_facebook_oembed
    try:
        oembed_data = await oembed_fetcher(session, url)
        if oembed_data:
            post.merge(oembed_data)
    except Exception:
        log.exception("oEmbed lookup failed for %s", url)

    try:
        og_data = await _try_open_graph(session, url)
        if og_data:
            post.merge(og_data)
    except Exception:
        log.exception("Open Graph lookup failed for %s", url)

    if not post.has_any_data():
        raise ScrapingError(f"Could not extract any metadata for {url}")

    return post
