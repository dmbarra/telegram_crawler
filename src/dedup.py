"""Content fingerprints (SHA-256) and the duplicates report, from the library."""
import hashlib
from pathlib import Path


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def find_duplicates(library, downloads_dir: Path) -> list:
    """[{group, sha, keep, dups: [rel...], size}] — keep = oldest file (first downloaded).
    A group is one channel (its id, as text)."""
    by_key = {}
    seen = set()
    for r in library.kept_files():
        rel = r["path"]
        if not r["sha256"] or r["channel_id"] is None or rel in seen \
                or not (downloads_dir / rel).is_file():
            continue
        seen.add(rel)
        by_key.setdefault((str(r["channel_id"]), r["sha256"]), []).append((r["downloaded_at"], rel, r["size"] or 0))
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
