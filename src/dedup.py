"""Content fingerprints (SHA-256) of downloaded files, to catch exact copies per group.

A "group" is the top folder under DOWNLOADS_DIR (one per Telegram channel/group).
The index lives in DATA_DIR/file_hashes.json as {relative_path: [size, mtime, sha256]}.
Thread-safe: the downloader (loop thread), the startup scan (own thread) and the
web app all use it.
"""
import hashlib
import json
import os
import threading
from pathlib import Path


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def group_of(rel: str) -> str:
    return rel.split("/", 1)[0]


class HashIndex:
    def __init__(self, downloads_dir: Path, index_path: Path):
        self.downloads_dir = downloads_dir
        self.index_path = index_path
        self._lock = threading.Lock()
        self._entries = {}
        self.scanning = False
        self.scanned = 0
        if index_path.exists():
            try:
                self._entries = json.loads(index_path.read_text(encoding="utf-8"))
            except Exception:
                self._entries = {}

    def rel(self, path) -> str:
        return Path(path).resolve().relative_to(self.downloads_dir.resolve()).as_posix()

    # ─── lookups / updates ──────────────────────────────────────
    def find(self, group: str, sha: str, exclude: str = ""):
        """Relative path of an existing file in `group` with this hash, or None."""
        with self._lock:
            matches = [r for r, (_, _, s) in self._entries.items()
                       if s == sha and r != exclude and group_of(r) == group]
        for r in sorted(matches):
            if (self.downloads_dir / r).is_file():
                return r
        return None

    def add(self, rel: str, sha: str) -> None:
        st = (self.downloads_dir / rel).stat()
        with self._lock:
            self._entries[rel] = [st.st_size, st.st_mtime, sha]

    def forget(self, rel: str) -> None:
        with self._lock:
            self._entries.pop(rel, None)

    def save(self) -> None:
        with self._lock:
            data = json.dumps(self._entries)
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.index_path.with_suffix(".json.tmp")
        tmp.write_text(data, encoding="utf-8")
        os.replace(tmp, self.index_path)

    # ─── one-time scan of files already on disk ─────────────────
    def scan_existing(self) -> None:
        """Fingerprint files that are new or changed since the last scan."""
        self.scanning, self.scanned = True, 0
        try:
            if not self.downloads_dir.is_dir():
                return
            seen = set()
            for f in self.downloads_dir.rglob("*"):
                if not f.is_file() or f.name.startswith("."):
                    continue
                rel = self.rel(f)
                seen.add(rel)
                st = f.stat()
                with self._lock:
                    known = self._entries.get(rel)
                if known and known[0] == st.st_size and known[1] == st.st_mtime:
                    continue
                try:
                    sha = hash_file(f)
                except OSError:
                    continue
                with self._lock:
                    self._entries[rel] = [st.st_size, st.st_mtime, sha]
                self.scanned += 1
                if self.scanned % 50 == 0:
                    self.save()
            with self._lock:
                for rel in [r for r in self._entries if r not in seen]:
                    del self._entries[rel]
            self.save()
        finally:
            self.scanning = False

    # ─── duplicate report ───────────────────────────────────────
    def find_duplicates(self) -> list:
        """[{group, sha, keep, dups: [rel...], size}] — keep = oldest file (first downloaded)."""
        with self._lock:
            items = list(self._entries.items())
        by_key = {}
        for rel, (size, mtime, sha) in items:
            if (self.downloads_dir / rel).is_file():
                by_key.setdefault((group_of(rel), sha), []).append((mtime, rel, size))
        out = []
        for (group, sha), files in by_key.items():
            if len(files) < 2:
                continue
            files.sort()
            out.append({
                "group": group,
                "sha": sha,
                "keep": files[0][1],
                "dups": [r for _, r, _ in files[1:]],
                "size": files[0][2],
            })
        out.sort(key=lambda d: (d["group"].lower(), d["keep"]))
        return out
