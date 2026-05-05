#!/usr/bin/env python3
"""
Reddit → Telegram Bot
Polls subreddits for new posts and forwards them to a Telegram chat.
"""

import os
import json
import time
import logging
import requests
from datetime import datetime
from pathlib import Path

# ── Configuration ────────────────────────────────────────────────────────────
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN", "YOUR_BOT_TOKEN_HERE")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID_HERE")

# Subreddits to watch (without r/)
SUBREDDITS = os.getenv("SUBREDDITS", "Python,programming,worldnews").split(",")

# How often to check (seconds). Reddit recommends >= 60 s to stay under rate limits.
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "120"))

# How many posts to fetch per subreddit per check
POSTS_PER_CHECK = int(os.getenv("POSTS_PER_CHECK", "5"))

# Minimum score a post needs before being sent (0 = all posts)
MIN_SCORE = int(os.getenv("MIN_SCORE", "0"))

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
    # Keep only the last 10 000 IDs to prevent unbounded growth
    trimmed = list(seen)[-10_000:]
    SEEN_FILE.write_text(json.dumps(trimmed))


# ── Reddit helpers ────────────────────────────────────────────────────────────
REDDIT_HEADERS = {"User-Agent": "reddit-telegram-bot/1.0 (by /u/telegrambot)"}


def fetch_new_posts(subreddit: str, limit: int = 10) -> list[dict]:
    """Return the latest posts from a subreddit via the public JSON API."""
    url = f"https://www.reddit.com/r/{subreddit}/new.json?limit={limit}"
    try:
        r = requests.get(url, headers=REDDIT_HEADERS, timeout=15)
        r.raise_for_status()
        posts = r.json()["data"]["children"]
        return [p["data"] for p in posts]
    except Exception as exc:
        log.warning("Failed to fetch r/%s: %s", subreddit, exc)
        return []


# ── Telegram helpers ──────────────────────────────────────────────────────────
def send_telegram(text: str) -> bool:
    """Send a message to the configured Telegram chat."""
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
    """Format a Reddit post into a Telegram-ready HTML message."""
    title     = post.get("title", "No title")
    author    = post.get("author", "unknown")
    subreddit = post.get("subreddit", "")
    score     = post.get("score", 0)
    comments  = post.get("num_comments", 0)
    url       = post.get("url", "")
    permalink = "https://reddit.com" + post.get("permalink", "")
    flair     = post.get("link_flair_text") or ""
    is_self   = post.get("is_self", False)

    flair_str = f" • <i>{flair}</i>" if flair else ""
    link_line = (
        f'<a href="{permalink}">💬 Comments ({comments})</a>'
        if is_self
        else f'<a href="{url}">🔗 Link</a>  |  <a href="{permalink}">💬 Comments ({comments})</a>'
    )

    return (
        f"<b>r/{subreddit}</b>{flair_str}\n"
        f"<b>{title}</b>\n"
        f"👤 u/{author}  •  ⬆️ {score:,}\n"
        f"{link_line}"
    )


# ── Main loop ─────────────────────────────────────────────────────────────────
def main() -> None:
    log.info("Starting Reddit → Telegram bot")
    log.info("Watching subreddits: %s", ", ".join(f"r/{s}" for s in SUBREDDITS))
    log.info("Poll interval: %d s | Min score: %d", POLL_INTERVAL, MIN_SCORE)

    seen = load_seen()

    # On the very first run, mark existing posts as seen without sending them
    # so you only get posts that appear *after* the bot starts.
    first_run = len(seen) == 0
    if first_run:
        log.info("First run — seeding seen-posts cache (no messages will be sent yet).")
        for sub in SUBREDDITS:
            for post in fetch_new_posts(sub, limit=25):
                seen.add(post["id"])
        save_seen(seen)
        log.info("Seeded %d post IDs. Waiting for new posts…", len(seen))

    while True:
        new_count = 0
        for sub in SUBREDDITS:
            posts = fetch_new_posts(sub, limit=POSTS_PER_CHECK)
            # Process oldest-first so Telegram messages arrive in chronological order
            for post in reversed(posts):
                pid = post["id"]
                if pid in seen:
                    continue
                seen.add(pid)

                if post.get("score", 0) < MIN_SCORE:
                    log.debug("Skipping low-score post %s (%d)", pid, post.get("score", 0))
                    continue

                msg = format_post(post)
                if send_telegram(msg):
                    log.info("Sent: [r/%s] %s", sub, post.get("title", "")[:60])
                    new_count += 1
                time.sleep(0.5)  # gentle rate-limit between messages

        save_seen(seen)
        if new_count:
            log.info("Sent %d new post(s). Sleeping %d s…", new_count, POLL_INTERVAL)
        else:
            log.debug("No new posts. Sleeping %d s…", POLL_INTERVAL)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
