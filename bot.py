#!/usr/bin/env python3
"""
Reddit → Telegram Bot (RSS edition)
Polls subreddit RSS feeds for new posts — no API key, no OAuth, no 403s.
Uses MongoDB to persist seen post IDs across restarts.
Includes a minimal HTTP health-check server to prevent Render spindown.
"""

import os
import time
import logging
import threading
import requests
import xml.etree.ElementTree as ET
from http.server import HTTPServer, BaseHTTPRequestHandler
from pymongo import MongoClient
from pymongo.errors import PyMongoError

# ── Configuration ────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "YOUR_BOT_TOKEN_HERE")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID_HERE")
MONGO_URI        = os.getenv("MONGO_URI", "YOUR_MONGO_URI_HERE")
SUBREDDITS       = os.getenv("SUBREDDITS", "Python,programming,worldnews").split(",")
POLL_INTERVAL    = int(os.getenv("POLL_INTERVAL", "120"))
POSTS_PER_CHECK  = int(os.getenv("POSTS_PER_CHECK", "10"))
PORT             = int(os.getenv("PORT", "8080"))

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── MongoDB ───────────────────────────────────────────────────────────────────
_client = MongoClient(MONGO_URI)
_db     = _client["reddit_bot"]
_seen   = _db["seen_posts"]
_seen.create_index("post_id", unique=True)  # fast lookups + no duplicates


def is_seen(post_id: str) -> bool:
    return _seen.count_documents({"post_id": post_id}, limit=1) > 0


def mark_seen(post_id: str) -> None:
    try:
        _seen.insert_one({"post_id": post_id})
    except PyMongoError:
        pass  # duplicate key = already seen, that's fine


def seed_seen(post_ids: list[str]) -> None:
    """Bulk-insert IDs on first run, ignoring duplicates."""
    if not post_ids:
        return
    try:
        _seen.insert_many(
            [{"post_id": pid} for pid in post_ids],
            ordered=False
        )
    except PyMongoError:
        pass


def is_first_run() -> bool:
    return _seen.count_documents({}) == 0


# ── Health-check server (prevents Render spindown) ────────────────────────────
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, *args):
        pass


def start_health_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    log.info("Health-check server listening on port %d", PORT)
    server.serve_forever()


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
if proxy := os.getenv("PROXY_URL"):
    _session.proxies = {"http": proxy, "https": proxy}
    log.info("Using proxy: %s", proxy)


def fetch_new_posts(subreddit: str, limit: int = 10) -> list[dict]:
    url = f"http://www.reddit.com/r/{subreddit}/new/.rss?limit={limit}"
    try:
        r = _session.get(url, timeout=15)
        r.raise_for_status()
        return parse_rss(r.text, subreddit)
    except Exception as exc:
        log.warning("Failed to fetch r/%s RSS: %s", subreddit, exc)
        return []


def parse_rss(xml_text: str, subreddit: str) -> list[dict]:
    posts = []
    try:
        root    = ET.fromstring(xml_text)
        entries = root.findall("atom:entry", RSS_NS)
        for entry in entries:
            raw_id  = entry.findtext("atom:id", default="", namespaces=RSS_NS)
            post_id = raw_id.split("_")[-1] if "_" in raw_id else raw_id
            title   = entry.findtext("atom:title", default="No title", namespaces=RSS_NS)
            link    = entry.find("atom:link", RSS_NS)
            url     = link.get("href", "") if link is not None else ""
            author  = entry.findtext("atom:author/atom:name", default="unknown", namespaces=RSS_NS)
            posts.append({"id": post_id, "title": title, "url": url, "author": author, "subreddit": subreddit})
    except ET.ParseError as exc:
        log.warning("RSS parse error for r/%s: %s", subreddit, exc)
    return posts


# ── Telegram helpers ──────────────────────────────────────────────────────────
def send_telegram(text: str) -> bool:
    url     = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": False}
    try:
        r = requests.post(url, json=payload, timeout=15)
        r.raise_for_status()
        return True
    except Exception as exc:
        log.error("Telegram send failed: %s", exc)
        return False


def format_post(post: dict) -> str:
    url         = post["url"]
    is_comments = "reddit.com/r/" in url and "/comments/" in url
    link_line   = f'<a href="{url}">💬 View post</a>' if is_comments else f'<a href="{url}">🔗 Link</a>'
    return f"<b>r/{post['subreddit']}</b>\n<b>{post['title']}</b>\n👤 {post['author']}\n{link_line}"


# ── Main loop ─────────────────────────────────────────────────────────────────
def main() -> None:
    threading.Thread(target=start_health_server, daemon=True).start()

    log.info("Starting Reddit → Telegram bot (RSS + MongoDB mode)")
    log.info("Watching: %s", ", ".join(f"r/{s}" for s in SUBREDDITS))
    log.info("Poll interval: %d s", POLL_INTERVAL)

    if is_first_run():
        log.info("First run — seeding seen-posts cache (no messages sent yet).")
        for sub in SUBREDDITS:
            posts = fetch_new_posts(sub, limit=10)
            seed_seen([p["id"] for p in posts])
            log.info("Seeded %d posts from r/%s", len(posts), sub)
            time.sleep(3)
        log.info("Seeding complete. Waiting for new posts…")

    while True:
        new_count = 0
        for sub in SUBREDDITS:
            posts = fetch_new_posts(sub, limit=POSTS_PER_CHECK)
            time.sleep(2)
            for post in reversed(posts):
                if is_seen(post["id"]):
                    continue
                mark_seen(post["id"])
                if send_telegram(format_post(post)):
                    log.info("Sent: [r/%s] %s", sub, post["title"][:60])
                    new_count += 1
                time.sleep(0.5)

        if new_count:
            log.info("Sent %d new post(s). Sleeping %d s…", new_count, POLL_INTERVAL)
        else:
            log.debug("No new posts. Sleeping %d s…", POLL_INTERVAL)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
