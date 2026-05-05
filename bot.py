#!/usr/bin/env python3
"""
Reddit → Telegram Bot (RSS edition)
Polls subreddit RSS feeds for new posts — no API key, no OAuth, no 403s.
"""

import os
import json
import time
import logging
import requests
import xml.etree.ElementTree as ET
from pathlib import Path

# ── Configuration ────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "YOUR_BOT_TOKEN_HERE")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID_HERE")

# Subreddits to watch (without r/)
SUBREDDITS = os.getenv("SUBREDDITS", "Python,programming,worldnews").split(",")

# How often to check (seconds)
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "120"))

# How many posts to fetch per subreddit per check (max 25 for RSS)
POSTS_PER_CHECK = int(os.getenv("POSTS_PER_CHECK", "10"))

# File that stores already-seen post IDs so duplicates are never sent
SEEN_FILE = Path(os.getenv("SEEN_FILE", "seen_posts.json"))

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Seen-posts store ──────────────────────────────────────────────────────────
def load_seen() -> set:
    if SEEN_FILE.exists():
        try:
            return set(json.loads(SEEN_FILE.read_text()))
        except Exception:
            return set()
    return set()


def save_seen(seen: set) -> None:
    trimmed = list(seen)[-10_000:]
    SEEN_FILE.write_text(json.dumps(trimmed))


# ── RSS helpers ───────────────────────────────────────────────────────────────
RSS_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "media": "http://search.yahoo.com/mrss/",
}

_session = requests.Session()
_session.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; rss-reader/1.0)",
    "Accept": "application/rss+xml, application/xml, text/xml",
})


def fetch_new_posts(subreddit: str, limit: int = 10) -> list[dict]:
    """Fetch latest posts from a subreddit RSS feed."""
    url = f"https://www.reddit.com/r/{subreddit}/new/.rss?limit={limit}"
    try:
        r = _session.get(url, timeout=15)
        r.raise_for_status()
        return parse_rss(r.text, subreddit)
    except Exception as exc:
        log.warning("Failed to fetch r/%s RSS: %s", subreddit, exc)
        return []


def parse_rss(xml_text: str, subreddit: str) -> list[dict]:
    """Parse Reddit Atom RSS feed into a list of post dicts."""
    posts = []
    try:
        root = ET.fromstring(xml_text)
        entries = root.findall("atom:entry", RSS_NS)
        for entry in entries:
            raw_id  = entry.findtext("atom:id", default="", namespaces=RSS_NS)
            post_id = raw_id.split("_")[-1] if "_" in raw_id else raw_id
            title   = entry.findtext("atom:title", default="No title", namespaces=RSS_NS)
            link    = entry.find("atom:link", RSS_NS)
            url     = link.get("href", "") if link is not None else ""
            author  = entry.findtext("atom:author/atom:name", default="unknown", namespaces=RSS_NS)

            posts.append({
                "id":        post_id,
                "title":     title,
                "url":       url,
                "author":    author,
                "subreddit": subreddit,
            })
    except ET.ParseError as exc:
        log.warning("RSS parse error for r/%s: %s", subreddit, exc)
    return posts


# ── Telegram helpers ──────────────────────────────────────────────────────────
def send_telegram(text: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    try:
        r = requests.post(url, json=payload, timeout=15)
        r.raise_for_status()
        return True
    except Exception as exc:
        log.error("Telegram send failed: %s", exc)
        return False


def format_post(post: dict) -> str:
    title     = post["title"]
    author    = post["author"]
    subreddit = post["subreddit"]
    url       = post["url"]

    is_comments = "reddit.com/r/" in url and "/comments/" in url
    link_line = (
        f'<a href="{url}">💬 View post</a>'
        if is_comments
        else f'<a href="{url}">🔗 Link</a>'
    )

    return (
        f"<b>r/{subreddit}</b>\n"
        f"<b>{title}</b>\n"
        f"👤 {author}\n"
        f"{link_line}"
    )


# ── Main loop ─────────────────────────────────────────────────────────────────
def main() -> None:
    log.info("Starting Reddit → Telegram bot (RSS mode)")
    log.info("Watching: %s", ", ".join(f"r/{s}" for s in SUBREDDITS))
    log.info("Poll interval: %d s", POLL_INTERVAL)

    seen = load_seen()

    if not seen:
        log.info("First run — seeding seen-posts cache (no messages sent yet).")
        for sub in SUBREDDITS:
            for post in fetch_new_posts(sub, limit=25):
                seen.add(post["id"])
        save_seen(seen)
        log.info("Seeded %d post IDs. Waiting for new posts…", len(seen))

    while True:
        new_count = 0
        for sub in SUBREDDITS:
            posts = fetch_new_posts(sub, limit=POSTS_PER_CHECK)
            for post in reversed(posts):
                pid = post["id"]
                if pid in seen:
                    continue
                seen.add(pid)
                msg = format_post(post)
                if send_telegram(msg):
                    log.info("Sent: [r/%s] %s", sub, post["title"][:60])
                    new_count += 1
                time.sleep(0.5)

        save_seen(seen)
        if new_count:
            log.info("Sent %d new post(s). Sleeping %d s…", new_count, POLL_INTERVAL)
        else:
            log.debug("No new posts. Sleeping %d s…", POLL_INTERVAL)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
