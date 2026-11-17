"""Media info scan: size, duration, audio and a perceptual hash, read with ffprobe/ffmpeg.

Runs on demand on its own thread. Only files without a media_info row are scanned.
"""
import json
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from src.config import DOWNLOADS_DIR

FFPROBE_BIN = os.getenv("FFPROBE_BIN", "ffprobe")
FFMPEG_BIN = os.getenv("FFMPEG_BIN", "ffmpeg")
WORKERS = 4


def probe(path) -> dict:
    out = subprocess.run(
        [FFPROBE_BIN, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, timeout=30, check=True,
    ).stdout
    data = json.loads(out or b"{}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    duration = data.get("format", {}).get("duration")
    return {
        "width": video.get("width"),
        "height": video.get("height"),
        "duration": float(duration) if duration not in (None, "N/A") else None,
        "has_audio": int(any(s.get("codec_type") == "audio" for s in streams)),
        "codec": video.get("codec_name") or next((s.get("codec_name") for s in streams), None),
    }


def dhash(path) -> str:
    """64-bit difference hash as 16 hex chars: close hashes = similar pictures."""
    raw = subprocess.run(
        [FFMPEG_BIN, "-v", "error", "-i", str(path), "-frames:v", "1",
         "-vf", "scale=9:8:flags=area,format=gray", "-f", "rawvideo", "-"],
        capture_output=True, timeout=30, check=True,
    ).stdout
    if len(raw) != 72:
        raise ValueError("could not read pixels")
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (raw[row * 9 + col] > raw[row * 9 + col + 1])
    return f"{bits:016x}"


class Scanner:
    def __init__(self, library):
        self.library = library
        self._lock = threading.Lock()
        self.running = False
        self.done = self.total = self.errors = 0
        self.started_at = self.finished_at = None

    def snapshot(self) -> dict:
        with self._lock:
            state = {"running": self.running, "done": self.done, "total": self.total,
                     "errors": self.errors, "started_at": self.started_at,
                     "finished_at": self.finished_at}
        return {**state, **self.library.media_info_summary()}

    def start(self):
        """Returns an error message, or None if the scan started."""
        with self._lock:
            if self.running:
                return "A scan is already running"
            todo = self.library.unscanned()
            if not todo:
                return "Nothing to scan: every file already has media info"
            self.running = True
            self.done, self.total, self.errors = 0, len(todo), 0
            self.started_at, self.finished_at = time.time(), None
        threading.Thread(target=self._run, args=(todo,), daemon=True, name="mediainfo").start()
        return None

    def _run(self, todo):
        try:
            with ThreadPoolExecutor(WORKERS) as pool:
                for ok in pool.map(self._scan_one, todo):
                    with self._lock:
                        self.done += 1
                        self.errors += not ok
        finally:
            with self._lock:
                self.running = False
                self.finished_at = time.time()

    def _scan_one(self, row) -> bool:
        path = DOWNLOADS_DIR / row["path"]
        if not path.is_file():
            return False
        info = {"width": None, "height": None, "duration": None, "has_audio": None,
                "codec": None, "phash": None, "error": None}
        try:
            info.update(probe(path))
            if row["kind"] == "images":
                info["phash"] = dhash(path)
        except (subprocess.SubprocessError, OSError, ValueError) as e:
            info["error"] = str(e)[:300] or type(e).__name__
        self.library.save_media_info(row["path"], **info)
        return info["error"] is None
