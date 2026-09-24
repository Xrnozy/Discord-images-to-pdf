# Discord Screenshot PDF

A local Windows-friendly app that retrieves screenshot images your Discord bot has posted to a channel, organizes them by date, and generates full-resolution PDFs with one page per image at the image's native aspect ratio.

## Quick Start

1. Install [Python 3.10+](https://www.python.org/downloads/) (check **Add Python to PATH**).
2. Build your **ScreenshotToDiscord** app in `gmeet-auto-ss` (or set `DISCORD_BOT_APP` in `.env`).
3. Double-click **`start.bat`** — this starts:
   - Your **ScreenshotToDiscord** bot (posts screenshots to Discord)
   - The **PDF website** at `http://localhost:8000`
4. Enter your Discord bot token and click **Connect**.
5. Select server → category → channel (or all channels in category).
6. Click **Retrieve Screenshots**, then **Generate PDF**.

## Discord Bot Setup

### Required Permissions

Invite the bot with at least:

- **View Channel**
- **Read Message History**

The bot must be a member of the server and have access to the target channel.

### Developer Portal

In the [Discord Developer Portal](https://discord.com/developers/applications) → your app → **Bot**:

- Enable **Message Content Intent** if you need to read attachments on messages from other users. Messages sent by your bot are readable without this.

### Bot Token Security

- Store the token in `.env` as `DISCORD_BOT_TOKEN=...` or enter it in the website (saved locally to `.env`).
- The token is only used by the local backend and is never sent to external servers.
- Never commit `.env` to git.

## How It Works

```
Screenshot app → Discord bot → Discord channel → This app → PDFs
```

1. **Retrieve** — Paginates the full channel message history (not just the latest 100).
2. **Download** — Saves original attachment files from Discord CDN (not thumbnails).
3. **Date** — Uses EXIF capture date when available; otherwise Discord message timestamp (local timezone).
4. **PDF** — One page per image; page size matches image dimensions/aspect ratio. JPEG and PNG are embedded without recompression; WEBP is converted once to lossless PNG for PDF embedding.

## Output Structure

```
output/
└── my-channel-2026-09-24-generated/
    ├── screenshots_2026-09-20.pdf
    ├── screenshots_2026-09-21.pdf
    └── screenshots_2026-09-22.pdf
```

Each time you generate PDFs, one folder is created named `{channel}-{date}-generated`. The channel name comes from your last retrieval and is saved in `.env` as `RETRIEVAL_CHANNEL_NAME`. All PDFs from that run go inside it.

Downloaded originals are stored in `downloads/`. Metadata index is in `data/index.json`.

## Incremental Retrieval

The app remembers the newest processed message per channel. Subsequent runs only scan newer messages. Use **Full Rescan** to scan the entire history again (already-downloaded images are skipped by attachment ID).

## Project Structure

```
├── backend/
│   ├── main.py              # FastAPI routes
│   ├── discord_client.py    # Discord REST API
│   ├── image_processor.py   # Download, EXIF, thumbnails
│   ├── pdf_generator.py     # img2pdf with dynamic page sizes
│   ├── store.py             # JSON index
│   ├── models.py
│   └── self_check.py        # Aspect-ratio verification
├── frontend/
│   ├── index.html
│   ├── style.css
│   └── app.js
├── start.bat
├── requirements.txt
└── .env.example
```

## Self-Check

```bash
.venv\Scripts\activate
python -m backend.self_check
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Invalid bot token | Regenerate token in Developer Portal |
| Bot not in server | Invite bot with correct permissions |
| Missing permissions | Grant View Channel + Read Message History |
| Channel not accessible | Check category/channel overrides |
| Rate limited | Wait; the app retries automatically |

## Port

Default: `http://localhost:8000`. Change the port in `start.bat` if needed.
