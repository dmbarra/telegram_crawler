"""Entry point: one web app to download from Telegram and view the media.

    python main.py      →  open http://127.0.0.1:5000

Uses Waitress (production WSGI) — NOT Flask's dev server. Waitress has
a fixed thread pool + built-in timeouts, so abandoned video streams can
never exhaust the app.

Reads .env for API_ID / API_HASH, VIEWER_HOST / VIEWER_PORT / VIEWER_PAGE_SIZE /
VIEWER_TILE_SIZE, VIEWER_USER / VIEWER_PASS, and FFMPEG_BIN.
"""
import os

# src.config must be imported first: it loads .env, which viewer.config reads.
from src.config import DOWNLOADS_DIR
from src.service import TelegramService
from viewer import config, create_app, scanner
from viewer.thumbnails import FFMPEG_AVAILABLE


def main():
    DOWNLOADS_DIR.mkdir(exist_ok=True)

    telegram = TelegramService(on_job_done=scanner.invalidate)
    telegram.start()
    app = create_app(telegram)

    auth_on = bool(os.getenv("VIEWER_USER") and os.getenv("VIEWER_PASS"))
    print(f"Serving:    {DOWNLOADS_DIR}")
    print(f"Thumbnails: {'enabled' if FFMPEG_AVAILABLE else 'DISABLED — ffmpeg not found'}")
    print(f"Auth:       {'ENABLED' if auth_on else 'DISABLED — set VIEWER_USER + VIEWER_PASS in .env'}")
    if not auth_on and config.HOST not in ("127.0.0.1", "localhost"):
        print("WARNING:    anyone on your network can start downloads. Turn on auth.")
    print("Telegram:   connecting in the background. First login asks for your phone here.")
    print(f"Open:       http://{config.HOST}:{config.PORT}")

    # • threads=16            → 16 concurrent requests served in parallel
    # • channel_timeout=90    → kill idle/half-dead sockets after 90s
    # • connection_limit=200  → hard cap on simultaneous connections
    # • asyncore_use_poll=True → better multi-connection handling on Windows
    from waitress import serve
    serve(
        app,
        host=config.HOST,
        port=config.PORT,
        threads=16,
        channel_timeout=90,
        connection_limit=200,
        asyncore_use_poll=True,
        ident="Telegram Crawler",
    )


if __name__ == "__main__":
    main()
