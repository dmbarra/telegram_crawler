"""Launcher for the media viewer.

    python viewer.py

Uses Waitress (production WSGI) — NOT Flask's dev server. Waitress has
a fixed thread pool + built-in timeouts, so abandoned video streams can
never exhaust the app.

Reads .env for VIEWER_HOST / VIEWER_PORT / VIEWER_PAGE_SIZE / VIEWER_TILE_SIZE,
VIEWER_USER / VIEWER_PASS, and FFMPEG_BIN.
"""
import os
from dotenv import load_dotenv

load_dotenv()

from viewer import create_app                              # noqa: E402
from viewer import config                                  # noqa: E402
from viewer.thumbnails import FFMPEG_AVAILABLE             # noqa: E402


if __name__ == "__main__":
    if not config.DOWNLOADS_DIR.exists():
        print(f"⚠️  No downloads folder at {config.DOWNLOADS_DIR}")
        print("   Run `python main.py` first to download some media.")
        raise SystemExit(1)

    print(f"📁 Serving:   {config.DOWNLOADS_DIR}")
    print(f"🎬 Page size: {config.PAGE_SIZE} tiles per channel")
    print(f"🖼️  Tile size: {config.TILE_SIZE}px")

    if FFMPEG_AVAILABLE:
        print(f"🎞️  Thumbnails: enabled (ffmpeg found)")
    else:
        print(f"🎞️  Thumbnails: DISABLED — ffmpeg not found")
        print(f"   Install from https://ffmpeg.org/download.html")

    user = os.getenv("VIEWER_USER", "")
    passwd = os.getenv("VIEWER_PASS", "")
    if user and passwd:
        print(f"🔐 Auth:      ENABLED (user '{user}')")
    else:
        print(f"🔓 Auth:      DISABLED — set VIEWER_USER + VIEWER_PASS in .env")

    print(f"🌐 Open:      http://{config.HOST}:{config.PORT}")

    app = create_app()

    # ─── Waitress: production WSGI server ────────────────────────
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
        ident="Media Viewer",
    )