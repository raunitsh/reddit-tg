# Reddit → Telegram Bot

Polls one or more subreddits for **new posts** and forwards them to a Telegram
chat or channel — no Reddit API key required (uses the public JSON endpoint).

---

## Quick start

### 1 — Create a Telegram bot

1. Open Telegram and message **@BotFather**.
2. Send `/newbot`, follow the prompts, copy the **token**.
3. Start a chat with your new bot (so it can message you).
4. Message **@userinfobot** to get your numeric **chat ID**.

### 2 — Install dependencies

```bash
pip install -r requirements.txt
```

### 3 — Configure

```bash
cp .env.example .env
# Edit .env and fill in TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, SUBREDDITS
```

| Variable | Description | Default |
|---|---|---|
| `TELEGRAM_TOKEN` | Token from @BotFather | *required* |
| `TELEGRAM_CHAT_ID` | Your chat / channel ID | *required* |
| `SUBREDDITS` | Comma-separated list (no `r/`) | `Python,programming,worldnews` |
| `POLL_INTERVAL` | Seconds between checks | `120` |
| `POSTS_PER_CHECK` | Posts fetched per sub per check | `5` |
| `MIN_SCORE` | Minimum upvotes to forward | `0` |

### 4 — Run

```bash
# Load env and start
export $(cat .env | xargs) && python3 bot.py
```

The bot **seeds its cache on the first run** — it marks all current posts as
seen without sending them, so you only receive posts that appear *after* launch.

---

## Run as a background service (Linux systemd)

```bash
sudo cp reddit-telegram-bot.service /etc/systemd/system/
# Edit the WorkingDirectory / EnvironmentFile paths inside the .service file
sudo systemctl daemon-reload
sudo systemctl enable --now reddit-telegram-bot
sudo journalctl -fu reddit-telegram-bot   # tail logs
```

---

## Sending to a Telegram channel

1. Create the channel in Telegram.
2. Add your bot as an **Administrator** with the *Post Messages* permission.
3. Set `TELEGRAM_CHAT_ID` to `@yourchannelusername` (or the numeric channel ID).

---

## Tips

- **More subreddits**: just extend the `SUBREDDITS` list — `SUBREDDITS=Python,rust,golang,devops`
- **Filter by score**: set `MIN_SCORE=100` to only receive posts with 100+ upvotes
- **Faster alerts**: lower `POLL_INTERVAL` (minimum ~60 s to be polite to Reddit)
- `seen_posts.json` stores up to 10 000 IDs to prevent duplicates across restarts
