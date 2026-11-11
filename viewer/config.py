"""Viewer configuration. Reads from .env with sane defaults."""
import os
from pathlib import Path

# viewer/config.py → viewer/ → project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DOWNLOADS_DIR = PROJECT_ROOT / "downloads"
# append near the other path definitions
THUMB_CACHE_DIR = PROJECT_ROOT / ".thumb_cache"
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

