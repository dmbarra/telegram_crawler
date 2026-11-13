"""Runs the Telegram client on its own event-loop thread so the web app can drive it.

Web request threads call the public methods; all Telethon work happens on
the loop thread. Only one download job runs at a time.
"""
import asyncio
import atexit
import shutil
import sys
import threading
import time

from telethon import TelegramClient

from src.config import (
    API_ID, API_HASH, DATA_DIR, DOWNLOADS_DIR, HASH_INDEX_PATH, SESSION_PATH, validate,
)
from src.dedup import HashIndex
from src.discover import Discovery
from src.progress import Progress
from src.runner import MEDIA_LABELS, build_filter, fetch_channels, normalize_entity, run_job
from src.storage import (
    add_extra_channel, load_extra_channels, load_state, remove_extra_channel, save_state,
)


class TelegramService:
    def __init__(self, on_job_done=None):
        self.progress = Progress()
        self.status = "starting"        # starting | connecting | ready | error
        self.error = ""
        self.account = ""
        self.channels = []
        self.failed_extras = []
        self._state = load_state()
        self.hashes = HashIndex(DOWNLOADS_DIR, HASH_INDEX_PATH)
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
        # Fingerprint files already on disk (first start, or files added by hand).
        threading.Thread(target=self.hashes.scan_existing, daemon=True, name="hash-scan").start()
        if not validate():
            self.status, self.error = "error", "API_ID / API_HASH missing or invalid in .env"
            return
        self._submit(self._connect())
        atexit.register(self._shutdown)

    async def _connect(self):
        self.status = "connecting"
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            self._client = TelegramClient(str(SESSION_PATH), API_ID, API_HASH)
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
        self.channels, self.failed_extras = await fetch_channels(self._client, self.progress.log)

    async def _add_channel(self, ident):
        try:
            entity = await self._client.get_entity(ident)
        except Exception as e:
            return None, f"Could not find '{ident}': {e}"
        if not hasattr(entity, "title"):
            return None, f"'{ident}' is a user, not a channel or group"
        if any(c["id"] == entity.id for c in self.channels):
            return entity.id, "Already in your list"
        add_extra_channel(ident)
        # New lists instead of in-place edits: web threads may be reading them.
        self.channels = self.channels + [normalize_entity(entity, ident, "file")]
        self.failed_extras = [f for f in self.failed_extras if f["ident"] != ident]
        return entity.id, None

    async def _remove_channel(self, ident):
        remove_extra_channel(ident)
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
        # Let a running job cancel cleanly (removes half-downloaded files, saves state).
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
        if ident not in load_extra_channels():
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
            if self.hashes.scanning:
                return None, "Still checking your files — try again in a moment"
            keep_for = {r: d["keep"] for d in self.hashes.find_duplicates() for r in d["dups"]}
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
                self.hashes.forget(rel)
                removed += 1
                freed += size
                # Point history at the kept copy, so the file is not downloaded again.
                old, new = str(path), str(DOWNLOADS_DIR / keep)
                for entry in self._state["downloaded"].values():
                    if entry.get("path") == old:
                        entry["path"] = new
            save_state(self._state)
            self.hashes.save()
        if self._on_job_done:
            self._on_job_done()
        return {"removed": removed, "freed": freed}, None

    def delete_paths(self, rels):
        """Delete files/folders under DOWNLOADS_DIR. History marks them deleted so they are not downloaded again.
        Returns (summary dict, None) or (None, error message)."""
        root = DOWNLOADS_DIR.resolve()
        targets = []
        for rel in rels:
            path = DOWNLOADS_DIR / rel
            try:
                resolved = path.resolve()
                resolved.relative_to(root)
            except (ValueError, OSError):
                return None, f"Not allowed: {rel}"
            if resolved == root:
                return None, "Cannot delete the downloads folder itself"
            targets.append(path)

        with self._lock:
            if self.progress.running:
                return None, "Wait until the download finishes"
            if self.hashes.scanning:
                return None, "Still checking your files — try again in a moment"
            removed = freed = 0
            gone = set()
            for path in targets:
                files = [f for f in path.rglob("*") if f.is_file()] if path.is_dir() else [path]
                try:
                    sizes = sum(f.stat().st_size for f in files)
                    shutil.rmtree(path) if path.is_dir() else path.unlink()
                except OSError:
                    continue
                removed += len(files)
                freed += sizes
                for f in files:
                    gone.add(str(f))
                    self.hashes.forget(f.relative_to(DOWNLOADS_DIR).as_posix())
            for entry in self._state["downloaded"].values():
                if entry.get("path") in gone:
                    entry["deleted"] = True
            save_state(self._state)
            self.hashes.save()
        if self._on_job_done:
            self._on_job_done()
        return {"removed": removed, "freed": freed}, None

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
                          batch_size, max_messages, self._state, p, self.hashes)
            p.log(f"Done. {p.downloaded} downloaded, {p.skipped} skipped, "
                  f"{p.duplicates} duplicates not kept, {p.errors} errors.", "ok")
        except asyncio.CancelledError:
            p.log("Stopped by user.", "warn")
        except Exception as e:
            p.log(f"Job failed: {e}", "error")
        finally:
            save_state(self._state)
            p.finish()
            if self._on_job_done:
                self._on_job_done()
