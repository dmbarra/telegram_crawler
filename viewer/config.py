"""Viewer configuration. Reads from .env with sane defaults."""
import os

from src.config import DOWNLOADS_DIR, THUMB_CACHE_DIR  # noqa: F401  (also loads .env)

FFMPEG_BIN = os.getenv("FFMPEG_BIN", "ffmpeg")

# Server
HOST = os.getenv("VIEWER_HOST", "127.0.0.1")
PORT = int(os.getenv("VIEWER_PORT", "5000"))

# UI
PAGE_SIZE = int(os.getenv("VIEWER_PAGE_SIZE", "20"))
TILE_SIZE = int(os.getenv("VIEWER_TILE_SIZE", "480"))

# Extensions
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"}
AUDIO_EXTS = {".mp3", ".m4a", ".ogg", ".wav", ".flac"}
ALL_EXTS = IMAGE_EXTS | VIDEO_EXTS | AUDIO_EXTS

