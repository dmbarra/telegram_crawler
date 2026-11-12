"""Entry point: one web app to download from Telegram and view the media.

    python main.py           →  start the app, open http://127.0.0.1:5000
    python main.py --login   →  log in to Telegram once (phone + code), then exit

Uses Waitress (production WSGI) — NOT Flask's dev server. Waitress has
a fixed thread pool + built-in timeouts, so abandoned video streams can
never exhaust the app.

All settings come from .env — see .env.example for the full list.
"""
import asyncio
import os
import signal
import sys

# src.config must be imported first: it loads .env, which viewer.config reads.
from src.config import (
    API_HASH, API_ID, DATA_DIR, DOWNLOADS_DIR, SESSION_PATH, validate,
)
from src.service import TelegramService
from viewer import config, create_app, scanner
from viewer.thumbnails import FFMPEG_AVAILABLE


def login() -> int:
    if not validate():
        return 1
    from telethon import TelegramClient

    async def _run():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        async with TelegramClient(str(SESSION_PATH), API_ID, API_HASH) as client:
            me = await client.get_me()
            print(f"Logged in as {me.first_name} (@{me.username or '-'}). "
                  f"Session saved in {SESSION_PATH}.session")

    asyncio.run(_run())
    return 0


def main():
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    telegram = TelegramService(on_job_done=scanner.invalidate)
    telegram.start()
    app = create_app(telegram)

    # docker stop sends SIGTERM; exiting normally runs the atexit cleanup in TelegramService.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    auth_on = bool(os.getenv("VIEWER_USER") and os.getenv("VIEWER_PASS"))
    print(f"Downloads:  {DOWNLOADS_DIR}")
    print(f"App data:   {DATA_DIR}")
    print(f"Thumbnails: {'enabled' if FFMPEG_AVAILABLE else 'DISABLED — ffmpeg not found'}")
    print(f"Auth:       {'ENABLED' if auth_on else 'DISABLED — set VIEWER_USER + VIEWER_PASS in .env'}")
    # PUBLISHED_IP / OPEN_URL are set by docker-compose.yml (the container itself listens on 0.0.0.0).
    reachable_on = os.getenv("PUBLISHED_IP") or config.HOST
    if not auth_on and reachable_on not in ("127.0.0.1", "localhost"):
        print(f"WARNING:    reachable on {reachable_on} without auth — anyone on your network "
              "can start downloads. Set VIEWER_USER + VIEWER_PASS.")
    print(f"Open:       {os.getenv('OPEN_URL') or f'http://{config.HOST}:{config.PORT}'}")

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
    if "--login" in sys.argv[1:]:
        sys.exit(login())
    main()
