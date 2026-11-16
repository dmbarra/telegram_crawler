"""Runs the Telegram client on its own event-loop thread so the web app can drive it.

Web request threads call the public methods; all Telethon work happens on
the loop thread. Only one download job runs at a time.
"""
import asyncio
import atexit
import sys
import threading
import time

from telethon import TelegramClient

from src.config import API_ID, API_HASH, DATA_DIR, DB_PATH, DOWNLOADS_DIR, SESSION_PATH, validate
from src.db import Library
from src.dedup import find_duplicates
from src.discover import Discovery
from src.progress import Progress
from src.runner import MEDIA_LABELS, build_filter, fetch_channels, normalize_entity, run_job


class TelegramService:
    def __init__(self, on_job_done=None):
        self.progress = Progress()
        self.status = "starting"        # starting | connecting | ready | error
        self.error = ""
        self.account = ""
        self.channels = []
        self.failed_extras = []
        self.library = Library(DB_PATH)
        self.discovery = Discovery(self)
        self._on_job_done = on_job_done
        self._client = None
        self._job = None
        self._lock = threading.Lock()
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._run_loop, daemon=True, name="telegram").start()

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    # ─── connection ─────────────────────────────────────────────
    def start(self) -> None:
        if not validate():
            self.status, self.error = "error", "API_ID / API_HASH missing or invalid in .env"
            return
        self._submit(self._connect())
        atexit.register(self._shutdown)

    async def _connect(self):
        self.status = "connecting"
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            # No event handlers exist; skipping updates avoids idle CPU from busy groups.
            self._client = TelegramClient(str(SESSION_PATH), API_ID, API_HASH,
                                          receive_updates=False)
            await self._client.connect()
            if not await self._client.is_user_authorized():
                if not sys.stdin.isatty():
                    raise RuntimeError("Not logged in. Run once: python main.py --login")
                # First login only: Telethon asks for phone + code in the terminal.
                await self._client.start()
            me = await self._client.get_me()
            self.account = me.username and f"@{me.username}" or me.first_name or str(me.id)
            await self._load_channels()
            self.status = "ready"
        except Exception as e:
            self.status, self.error = "error", str(e)

    async def _load_channels(self):
        self.channels, self.failed_extras = await fetch_channels(
            self._client, self.progress.log, self.library.extra_idents())

    async def _add_channel(self, ident):
        try:
            entity = await self._client.get_entity(ident)
        except Exception as e:
            return None, f"Could not find '{ident}': {e}"
        if not hasattr(entity, "title"):
            return None, f"'{ident}' is a user, not a channel or group"
        if any(c["id"] == entity.id for c in self.channels):
            return entity.id, "Already in your list"
        self.library.add_extra(entity.id, getattr(entity, "title", None),
                               getattr(entity, "username", None), ident)
        # New lists instead of in-place edits: web threads may be reading them.
        self.channels = self.channels + [normalize_entity(entity, ident, "file")]
        self.failed_extras = [f for f in self.failed_extras if f["ident"] != ident]
        return entity.id, None

    async def _remove_channel(self, ident):
        self.library.remove_extra(ident)
        kept = []
        for c in self.channels:
            if c["extra"] == ident and c["extra_source"] == "file":
                if not c["member"]:
                    continue
                c = {**c, "extra": None, "extra_source": None}
            kept.append(c)
        self.channels = kept
        self.failed_extras = [f for f in self.failed_extras if f["ident"] != ident]

    def _shutdown(self):
        # Let a running job cancel cleanly (removes half-downloaded files).
        self.stop_job()
        deadline = time.time() + 10
        while self.progress.running and time.time() < deadline:
            time.sleep(0.1)
        if self._client:
            try:
                self._submit(self._client.disconnect()).result(timeout=5)
            except Exception:
                pass

    # ─── public API (called from web threads) ───────────────────
    def refresh_channels(self):
        """Returns an error message, or None on success."""
        if self.status != "ready":
            return "Telegram is not connected"
        if self.progress.running:
            return "Wait until the download finishes"
        try:
            self._submit(self._load_channels()).result(timeout=120)
        except Exception as e:
            return f"Refresh failed: {e}"
        return None

    def add_channel(self, ident):
        """Returns (channel_id or None, error message or None)."""
        if self.status != "ready":
            return None, "Telegram is not connected"
        return self._submit(self._add_channel(ident)).result(timeout=60)

    def remove_channel(self, ident):
        """Returns an error message, or None on success."""
        if ident not in self.library.extra_idents():
            return "Only channels added on this page can be removed"
        self._submit(self._remove_channel(ident)).result(timeout=10)
        return None

    def start_job(self, channel_ids, media, batch_size, max_messages):
        """Returns an error message, or None if the job started."""
        built = build_filter(media)
        if not built:
            return "Invalid media type"
        if not 1 <= batch_size <= 20:
            return "Files at the same time must be 1 to 20"
        if max_messages < 0:
            return "Max messages must be 0 or more"
        with self._lock:
            if self.status != "ready":
                return "Telegram is not connected"
            if self.progress.running:
                return "A download is already running"
            wanted = set(channel_ids)
            selected = [c for c in self.channels if c["id"] in wanted]
            if not selected:
                return "No channels selected"
            self.progress.begin(len(selected), MEDIA_LABELS[media])
            self._job = self._submit(self._run(selected, *built, batch_size, max_messages))
        return None

    def stop_job(self) -> None:
        with self._lock:
            if self.progress.running and self._job:
                self.progress.request_stop()
                self._job.cancel()

    def remove_duplicates(self, rels):
        """Delete the given copies (only files that really are duplicates).
        Returns (summary dict, None) or (None, error message)."""
        with self._lock:
            if self.progress.running:
                return None, "Wait until the download finishes"
            keep_for = {r: d["keep"] for d in find_duplicates(self.library, DOWNLOADS_DIR)
                        for r in d["dups"]}
            removed = freed = 0
            for rel in rels:
                keep = keep_for.get(rel)
                if not keep:
                    continue
                path = DOWNLOADS_DIR / rel
                try:
                    size = path.stat().st_size
                    path.unlink()
                except OSError:
                    continue
                removed += 1
                freed += size
                # Point history at the kept copy, so the file is not downloaded again.
                self.library.repoint(rel, keep)
        if self._on_job_done:
            self._on_job_done()
        return {"removed": removed, "freed": freed}, None

    def delete_files(self, paths=(), channel_ids=()):
        """Delete files the library knows (by path, or every file of the given channels).
        The records are kept as deleted, so the files are not downloaded again.
        Returns (summary dict, None) or (None, error message)."""
        root = DOWNLOADS_DIR.resolve()
        with self._lock:
            if self.progress.running:
                return None, "Wait until the download finishes"
            wanted = set(paths) | set(self.library.channel_paths(channel_ids) if channel_ids else [])
            known = self.library.known_paths(wanted)
            removed = freed = 0
            gone, failed = [], []
            for rel in sorted(wanted):
                if rel not in known:
                    failed.append(f"{rel}: not in the database")
                    continue
                path = DOWNLOADS_DIR / rel
                try:
                    path.resolve().relative_to(root)
                except (ValueError, OSError):
                    failed.append(f"{rel}: outside the downloads folder")
                    continue
                try:
                    size = path.stat().st_size if path.is_file() else 0
                    path.unlink(missing_ok=True)
                except OSError as e:
                    failed.append(f"{rel}: {e}")
                    continue
                gone.append(rel)
                removed += 1
                freed += size
                for parent in path.parents:
                    if parent.resolve() == root:
                        break
                    try:
                        parent.rmdir()
                    except OSError:
                        break
            self.library.mark_deleted(gone)
        if self._on_job_done:
            self._on_job_done()
        return {"removed": removed, "freed": freed, "failed": failed}, None

    def snapshot(self, since: int = 0) -> dict:
        return {
            "telegram": {"status": self.status, "error": self.error, "account": self.account},
            "job": self.progress.snapshot(since),
        }

    # ─── job body (runs on the loop thread) ─────────────────────
    async def _run(self, channels, filter_type, folder_name, mime, batch_size, max_messages):
        p = self.progress
        try:
            await run_job(self._client, channels, filter_type, folder_name, mime,
                          batch_size, max_messages, self.library, p)
            p.log(f"Done. {p.downloaded} downloaded, {p.skipped} skipped, "
                  f"{p.duplicates} duplicates not kept, {p.errors} errors.", "ok")
        except asyncio.CancelledError:
            p.log("Stopped by user.", "warn")
        except Exception as e:
            p.log(f"Job failed: {e}", "error")
        finally:
            p.finish()
            if self._on_job_done:
                self._on_job_done()
