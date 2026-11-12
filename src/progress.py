"""Download progress shared between the Telegram thread (writes) and the web UI (reads)."""
import itertools
import threading
import time
from collections import deque


class Progress:
    def __init__(self):
        self._lock = threading.Lock()
        self._log_ids = itertools.count(1)
        self._log = deque(maxlen=500)
        self._reset()

    def _reset(self):
        self.running = False
        self.stopping = False
        self.stopped = False
        self.started_at = None
        self.finished_at = None
        self.media = ""
        self.channel_index = 0
        self.channel_count = 0
        self.channel_title = ""
        self.files_done = 0
        self.files_total = 0
        self.downloaded = 0
        self.skipped = 0
        self.errors = 0
        self.active = {}

    # ─── job lifecycle ──────────────────────────────────────────
    def begin(self, channel_count: int, media: str) -> None:
        with self._lock:
            self._reset()
            self._log.clear()
            self.running = True
            self.started_at = time.time()
            self.channel_count = channel_count
            self.media = media

    def request_stop(self) -> None:
        with self._lock:
            self.stopping = True

    def finish(self) -> None:
        with self._lock:
            self.running = False
            self.stopped = self.stopping
            self.stopping = False
            self.finished_at = time.time()
            self.active = {}

    # ─── updates from the downloader ────────────────────────────
    def log(self, msg: str, level: str = "info") -> None:
        with self._lock:
            self._log.append({"id": next(self._log_ids), "t": time.time(),
                              "level": level, "msg": msg})

    def channel_start(self, index: int, title: str) -> None:
        with self._lock:
            self.channel_index = index
            self.channel_title = title
            self.files_done = 0
            self.files_total = 0
            self.active = {}

    def channel_files(self, total: int) -> None:
        with self._lock:
            self.files_total = total

    def file_start(self, key, name: str, total: int) -> None:
        with self._lock:
            self.active[key] = {"name": name, "done": 0, "total": total}

    def file_update(self, key, done: int, total: int) -> None:
        with self._lock:
            f = self.active.get(key)
            if f:
                f["done"] = done
                if total:
                    f["total"] = total

    def file_end(self, key, result: str) -> None:
        with self._lock:
            self.active.pop(key, None)
            self.files_done += 1
            if result == "downloaded":
                self.downloaded += 1
            elif result == "skipped":
                self.skipped += 1
            else:
                self.errors += 1

    # ─── read by the web UI ─────────────────────────────────────
    def snapshot(self, since: int = 0) -> dict:
        with self._lock:
            return {
                "running": self.running,
                "stopping": self.stopping,
                "stopped": self.stopped,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "media": self.media,
                "channel_index": self.channel_index,
                "channel_count": self.channel_count,
                "channel_title": self.channel_title,
                "files_done": self.files_done,
                "files_total": self.files_total,
                "downloaded": self.downloaded,
                "skipped": self.skipped,
                "errors": self.errors,
                "active": [dict(f) for f in self.active.values()],
                "log": [e for e in self._log if e["id"] > since],
            }
