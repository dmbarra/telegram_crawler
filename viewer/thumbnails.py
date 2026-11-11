"""Generate + cache video thumbnails with ffmpeg.

On first request for a video, extract a frame → save as JPEG in .thumb_cache/
→ serve it. Cache key includes mtime+size so edits/deletions auto-invalidate.
Concurrent requests for the same file block on a per-file lock; only 3 ffmpeg
processes run at once to keep CPU sane.
"""
import hashlib
import os
import shutil
import subprocess
import threading
from pathlib import Path

from viewer.config import THUMB_CACHE_DIR, FFMPEG_BIN

FFMPEG_AVAILABLE = shutil.which(FFMPEG_BIN) is not None

_gen_sem = threading.Semaphore(3)
_file_locks: dict[str, threading.Lock] = {}
_file_locks_mutex = threading.Lock()


def _thumb_path(rel: str, src: Path) -> Path:
    st = src.stat()
    key = f"{rel}|{st.st_mtime_ns}|{st.st_size}"
    h = hashlib.sha1(key.encode()).hexdigest()[:16]
    return THUMB_CACHE_DIR / f"{h}.jpg"


def _file_lock(rel: str) -> threading.Lock:
    with _file_locks_mutex:
        lock = _file_locks.get(rel)
        if lock is None:
            lock = threading.Lock()
            _file_locks[rel] = lock
        return lock


def get_thumbnail(rel: str, src: Path) -> Path | None:
    """Return the thumbnail path for `src`, generating it on first use.
    Returns None if ffmpeg is unavailable or generation fails."""
    if not FFMPEG_AVAILABLE or not src.is_file():
        return None

    thumb = _thumb_path(rel, src)
    if thumb.exists():
        return thumb

    with _file_lock(rel):
        if thumb.exists():
            return thumb
        with _gen_sem:
            return _generate(src, thumb)


def _generate(src: Path, thumb: Path) -> Path | None:
    THUMB_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # NOTE: Path.with_suffix() rejects multi-dot suffixes like ".tmp.jpg",
    #       so build the temp filename by string concatenation instead.
    tmp = thumb.parent / (thumb.stem + ".tmp.jpg")

    try:
        for seek in ("1", "0"):
            try:
                cmd = [
                    FFMPEG_BIN, "-y", "-loglevel", "error",
                    "-ss", seek, "-i", str(src),
                    "-frames:v", "1",
                    "-vf", "scale=640:640:force_original_aspect_ratio=decrease",
                    "-q:v", "4",
                    str(tmp),
                ]
                subprocess.run(cmd, capture_output=True, timeout=15)
                if tmp.exists() and tmp.stat().st_size > 0:
                    os.replace(tmp, thumb)
                    return thumb
            except (subprocess.TimeoutExpired, OSError):
                pass
        return None
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass