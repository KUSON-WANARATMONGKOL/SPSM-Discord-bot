"""Metadata extraction for Instagram/Facebook post URLs.

Three strategies are tried in order, each filling in only the fields the
previous one left empty:

1. **Owned-account Graph API** (most reliable, real like counts) — if the
   admin has linked the school's own Facebook Page + Instagram Business
   account (META_PAGE_ID + META_IG_USER_ID + META_PAGE_ACCESS_TOKEN env
   vars), this queries that Page/account directly with a real access token
   the school controls. Only works for posts *from that account* — Facebook
   posts are looked up directly by ID; Instagram posts are matched by
   shortcode against the account's recent media (paginated, bounded by
   MAX_IG_LOOKUP_PAGES). See README.md for the one-time setup.
2. **Meta's oEmbed Read API** (instagram_oembed / oembed_post), if the admin
   has configured a Meta developer app (META_APP_ID + META_APP_SECRET). This
   can resolve posts from *other* accounts too, but Meta gates it behind App
   Review for content you don't own, so many admins won't have it approved
   — that's expected, not a bug. Skipped once strategy 1 already found data.
3. **Open Graph meta tags** on the post's public page (the same og:image /
   og:description technique Discord's own link unfurling uses). Confirmed
   (2026-09-18, live request) that Instagram's post pages — including the
   `/embed/` URL meant for public embedding — now render entirely
   client-side and contain *zero* `og:*` tags or other static metadata in
   the server response, regardless of the post's privacy setting; this
   strategy is effectively dead for Instagram and only helps for Facebook,
   which still serves partial tags for some public posts. Skipped once an
   earlier strategy already has both an image and a caption.

Deliberately NOT implemented, and why:

- **Instagram's old `?__a=1&__w=1` JSON endpoint.** Instagram shut off
  unauthenticated access to this years ago; today it 404s, redirects to a
  login page, or returns an empty shell without a real logged-in session's
  cookies. Making it "work" would mean smuggling in real account session
  cookies, which is the same account-ban / ToS risk as instagrapi below.
- **Logging into a real Instagram account via an unofficial library** (e.g.
  instagrapi) to scrape post data. That requires storing real account
  credentials in a 24/7 bot, risks that account being banned, and is against
  Instagram's Terms of Service around automated data collection.
- **Headless-browser rendering (Playwright/Selenium)** as a scraping
  fallback. Beyond the deployment cost (a real Chromium install + extra
  system packages on Railway), Meta's anti-bot systems flag datacenter IPs
  (Railway, AWS, GCP, ...) far more aggressively than residential ones, so a
  logged-out headless browser from Railway frequently gets served the same
  "Log in to see this post" wall a plain HTTP request does — it doesn't
  reliably fix the problem it would be added to solve.

For posts outside the school's own account, `likes_count` stays None (no
legitimate source exposes it) — the UI should show "no data" rather than
inventing a number.
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

# Strategy 2: Meta oEmbed (App Review required for posts you don't own).
META_APP_ID = os.getenv("META_APP_ID")
META_APP_SECRET = os.getenv("META_APP_SECRET")
META_OEMBED_TOKEN = f"{META_APP_ID}|{META_APP_SECRET}" if META_APP_ID and META_APP_SECRET else None

# Strategy 1: owned-account Graph API (school's own Page + linked IG Business account).
META_PAGE_ID = os.getenv("META_PAGE_ID")
META_IG_USER_ID = os.getenv("META_IG_USER_ID")
META_PAGE_ACCESS_TOKEN = os.getenv("META_PAGE_ACCESS_TOKEN")
OWNED_FACEBOOK_CONFIGURED = bool(META_PAGE_ID and META_PAGE_ACCESS_TOKEN)
OWNED_INSTAGRAM_CONFIGURED = bool(META_IG_USER_ID and META_PAGE_ACCESS_TOKEN)

MAX_IG_LOOKUP_PAGES = 5  # 50 posts/page -> up to 250 recent posts checked

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

FACEBOOK_POST_ID_RE = re.compile(r"(?:/posts/|/videos/|story_fbid=)(\d+)")
INSTAGRAM_SHORTCODE_RE = re.compile(r"/(?:p|reel)/([\w-]+)")

# Deliberately not `#\w+`: Python's \w excludes Thai combining vowel/tone
# marks (Unicode category Mn, e.g. "ี" in "โรงเรียน"), which would truncate
# almost any real Thai hashtag. Match everything after '#' up to the next
# whitespace, '#', or '@' instead.
HASHTAG_RE = re.compile(r"#[^\s#@]+")
_TRAILING_PUNCTUATION = ".,!?;:\"')]}。！？"  # incl. Thai/CJK full-width punctuation


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
    extraction_method: Optional[str] = None  # for logging/debugging only

    def merge(self, other: "SocialPost") -> None:
        """Fill in only the fields this post is still missing, from `other`."""
        for attr in ("author_name", "author_avatar", "caption", "image_url", "likes_count", "post_date"):
            if getattr(self, attr) is None and getattr(other, attr) is not None:
                setattr(self, attr, getattr(other, attr))
        if not self.hashtags and other.hashtags:
            self.hashtags = other.hashtags
        if self.extraction_method is None and other.extraction_method is not None:
            self.extraction_method = other.extraction_method

    def has_any_data(self) -> bool:
        return any([self.author_name, self.caption, self.image_url])

    def is_complete_enough(self) -> bool:
        """True once further (weaker) strategies wouldn't add much value."""
        return self.image_url is not None and self.caption is not None


def classify_url(url: str) -> Optional[str]:
    """Return 'instagram', 'facebook', or None if the URL isn't a supported post link."""
    url = url.strip()
    if INSTAGRAM_URL_RE.match(url):
        return "instagram"
    if FACEBOOK_URL_RE.match(url):
        return "facebook"
    return None


def extract_hashtags(text: Optional[str]) -> list:
    if not text:
        return []
    return [tag.rstrip(_TRAILING_PUNCTUATION) for tag in HASHTAG_RE.findall(text)]


def _parse_graph_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return None


async def _fetch_json(session: aiohttp.ClientSession, url: str, params: Optional[dict]) -> Optional[dict]:
    try:
        async with session.get(url, params=params, timeout=HTTP_TIMEOUT) as resp:
            try:
                data = await resp.json(content_type=None)
            except (ValueError, aiohttp.ContentTypeError):
                return None
            if resp.status != 200 or (isinstance(data, dict) and "error" in data):
                log.info("Graph API call to %s returned status=%s body=%s", url, resp.status, data)
                return None
            return data
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return None


# ---------------------------------------------------------------------------
# Strategy 1: owned-account Graph API
# ---------------------------------------------------------------------------


async def _fetch_fb_page_avatar(session: aiohttp.ClientSession, page_id: str) -> Optional[str]:
    data = await _fetch_json(
        session,
        f"https://graph.facebook.com/{META_GRAPH_VERSION}/{page_id}/picture",
        {"redirect": "false", "access_token": META_PAGE_ACCESS_TOKEN},
    )
    return (data or {}).get("data", {}).get("url")


async def _try_owned_facebook_post(session: aiohttp.ClientSession, url: str) -> Optional[SocialPost]:
    """Look up a post directly by ID from the school's own configured Page."""
    if not OWNED_FACEBOOK_CONFIGURED:
        return None
    match = FACEBOOK_POST_ID_RE.search(url)
    if not match:
        return None
    story_id = match.group(1)
    graph_id = f"{META_PAGE_ID}_{story_id}"

    data = await _fetch_json(
        session,
        f"https://graph.facebook.com/{META_GRAPH_VERSION}/{graph_id}",
        {
            "fields": "message,full_picture,created_time,permalink_url,from,likes.summary(true)",
            "access_token": META_PAGE_ACCESS_TOKEN,
        },
    )
    if not data:
        return None

    caption = data.get("message")
    likes = (data.get("likes") or {}).get("summary", {}).get("total_count")
    avatar = await _fetch_fb_page_avatar(session, META_PAGE_ID)

    return SocialPost(
        platform="facebook",
        original_url=url,
        author_name=(data.get("from") or {}).get("name"),
        author_avatar=avatar,
        caption=caption,
        image_url=data.get("full_picture"),
        likes_count=likes,
        post_date=_parse_graph_datetime(data.get("created_time")),
        hashtags=extract_hashtags(caption),
        extraction_method="owned_facebook_api",
    )


def _social_post_from_ig_media(item: dict, original_url: str) -> SocialPost:
    caption = item.get("caption")
    return SocialPost(
        platform="instagram",
        original_url=original_url,
        author_name=item.get("username"),
        caption=caption,
        image_url=item.get("media_url") or item.get("thumbnail_url"),
        likes_count=item.get("like_count"),
        post_date=_parse_graph_datetime(item.get("timestamp")),
        hashtags=extract_hashtags(caption),
        extraction_method="owned_instagram_api",
    )


async def _fetch_ig_avatar(session: aiohttp.ClientSession, ig_user_id: str) -> Optional[str]:
    data = await _fetch_json(
        session,
        f"https://graph.facebook.com/{META_GRAPH_VERSION}/{ig_user_id}",
        {"fields": "profile_picture_url", "access_token": META_PAGE_ACCESS_TOKEN},
    )
    return (data or {}).get("profile_picture_url")


async def _try_owned_instagram_post(session: aiohttp.ClientSession, url: str) -> Optional[SocialPost]:
    """Match a post by shortcode against the school's own IG Business account's recent media."""
    if not OWNED_INSTAGRAM_CONFIGURED:
        return None
    match = INSTAGRAM_SHORTCODE_RE.search(url)
    if not match:
        return None
    shortcode = match.group(1)

    fields = "id,caption,media_url,thumbnail_url,permalink,timestamp,like_count,username"
    next_url = f"https://graph.facebook.com/{META_GRAPH_VERSION}/{META_IG_USER_ID}/media"
    params: Optional[dict] = {"fields": fields, "access_token": META_PAGE_ACCESS_TOKEN, "limit": 50}

    for _ in range(MAX_IG_LOOKUP_PAGES):
        data = await _fetch_json(session, next_url, params)
        if not data or "data" not in data:
            return None

        for item in data["data"]:
            if shortcode in (item.get("permalink") or ""):
                post = _social_post_from_ig_media(item, url)
                post.author_avatar = await _fetch_ig_avatar(session, META_IG_USER_ID)
                return post

        cursor_url = (data.get("paging") or {}).get("next")
        if not cursor_url:
            return None
        next_url, params = cursor_url, None  # cursor URL already carries the query string

    return None


# ---------------------------------------------------------------------------
# Strategy 2: Meta oEmbed
# ---------------------------------------------------------------------------


async def _try_instagram_oembed(session: aiohttp.ClientSession, url: str) -> Optional[SocialPost]:
    if not META_OEMBED_TOKEN:
        return None
    data = await _fetch_json(
        session,
        f"https://graph.facebook.com/{META_GRAPH_VERSION}/instagram_oembed",
        {"url": url, "access_token": META_OEMBED_TOKEN, "fields": "author_name,thumbnail_url,title"},
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
        extraction_method="meta_oembed",
    )


async def _try_facebook_oembed(session: aiohttp.ClientSession, url: str) -> Optional[SocialPost]:
    if not META_OEMBED_TOKEN:
        return None
    data = await _fetch_json(
        session,
        f"https://graph.facebook.com/{META_GRAPH_VERSION}/oembed_post",
        {"url": url, "access_token": META_OEMBED_TOKEN},
    )
    if not data:
        return None
    return SocialPost(
        platform="facebook",
        original_url=url,
        author_name=data.get("author_name"),
        extraction_method="meta_oembed",
    )


# ---------------------------------------------------------------------------
# Strategy 3: Open Graph tags
# ---------------------------------------------------------------------------


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
        extraction_method="open_graph",
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def extract_post(session: aiohttp.ClientSession, url: str, platform: str) -> SocialPost:
    """Try the owned-account API, then oEmbed, then Open Graph, merging results.

    Each later strategy only fills in fields the earlier ones left empty, and
    is skipped entirely once the post already has enough data to be worth
    posting. Raises ScrapingError if nothing produced any usable data (e.g.
    the post is private, the URL is dead, or the platform blocked every
    request).
    """
    post = SocialPost(platform=platform, original_url=url)

    owned_fetcher = _try_owned_instagram_post if platform == "instagram" else _try_owned_facebook_post
    try:
        owned_data = await owned_fetcher(session, url)
        if owned_data:
            post.merge(owned_data)
    except Exception:
        log.exception("Owned-account Graph API lookup failed for %s", url)

    if not post.is_complete_enough():
        oembed_fetcher = _try_instagram_oembed if platform == "instagram" else _try_facebook_oembed
        try:
            oembed_data = await oembed_fetcher(session, url)
            if oembed_data:
                post.merge(oembed_data)
        except Exception:
            log.exception("oEmbed lookup failed for %s", url)

    if not post.is_complete_enough():
        try:
            og_data = await _try_open_graph(session, url)
            if og_data:
                post.merge(og_data)
        except Exception:
            log.exception("Open Graph lookup failed for %s", url)

    if not post.has_any_data():
        raise ScrapingError(f"Could not extract any metadata for {url}")

    log.info("Extracted %s post %s via %s", platform, url, post.extraction_method)
    return post
