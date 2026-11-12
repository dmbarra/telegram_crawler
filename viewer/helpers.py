"""Small, side-effect-free helpers."""
import time

from viewer.config import IMAGE_EXTS, VIDEO_EXTS

CHANNEL_SORTS = {
    "recent": "Newest download",
    "name":   "Name A → Z",
    "files":  "Most files",
}
DEFAULT_CHANNEL_SORT = "recent"

SORT_OPTIONS = {
    "newest":    "Newest first (download date)",
    "oldest":    "Oldest first (download date)",
    "name_asc":  "Name A → Z",
    "name_desc": "Name Z → A",
    "size_desc": "Largest first",
    "size_asc":  "Smallest first",
}
DEFAULT_SORT = "newest"


def fmt_size(n: int) -> str:
    if n < 1024: return f"{n} B"
    if n < 1024 ** 2: return f"{n / 1024:.1f} KB"
    if n < 1024 ** 3: return f"{n / 1024 ** 2:.1f} MB"
    if n < 1024 ** 4: return f"{n / 1024 ** 3:.2f} GB"
    return f"{n / 1024 ** 4:.2f} TB"


def classify_ext(ext: str) -> str:
    ext = ext.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    return "audio"


def apply_filters(files: list, kind: str, q: str) -> list:
    if kind:
        files = [f for f in files if f["type"] == kind]
    if q:
        files = [f for f in files if q in f["name"].lower()]
    return files


def sort_files(files: list, sort_key: str) -> list:
    """Sort by download date (mtime), name, or size. Unknown keys → newest."""
    if sort_key == "oldest":
        return sorted(files, key=lambda x: x["mtime"])
    if sort_key == "name_asc":
        return sorted(files, key=lambda x: x["name"].lower())
    if sort_key == "name_desc":
        return sorted(files, key=lambda x: x["name"].lower(), reverse=True)
    if sort_key == "size_desc":
        return sorted(files, key=lambda x: x["size"], reverse=True)
    if sort_key == "size_asc":
        return sorted(files, key=lambda x: x["size"])
    # default: newest (download date, descending)
    return sorted(files, key=lambda x: x["mtime"], reverse=True)


def channel_summary(name: str, files: list) -> dict:
    """One card on the channel index: counts, size, last download, cover file."""
    counts = {"image": 0, "video": 0, "audio": 0}
    for f in files:
        counts[f["type"]] += 1
    newest = max(f["mtime"] for f in files)
    visual = [f for f in files if f["type"] in ("image", "video")]
    return {
        "name": name,
        "count": len(files),
        "counts": counts,
        "size_str": fmt_size(sum(f["size"] for f in files)),
        "mtime": newest,
        "last": time.strftime("%Y-%m-%d", time.localtime(newest)),
        "cover": max(visual, key=lambda f: f["mtime"]) if visual else None,
    }


def sort_channels(cards: list, sort_key: str) -> list:
    if sort_key == "name":
        return sorted(cards, key=lambda c: c["name"].lower())
    if sort_key == "files":
        return sorted(cards, key=lambda c: c["count"], reverse=True)
    return sorted(cards, key=lambda c: c["mtime"], reverse=True)


def page_window(page: int, pages: int, radius: int = 2) -> list:
    """Page numbers to show in the pager; None marks a gap ("…").
    Example: page 6 of 12 → [1, None, 4, 5, 6, 7, 8, None, 12]."""
    keep = {1, pages, *range(page - radius, page + radius + 1)}
    out, prev = [], 0
    for n in sorted(k for k in keep if 1 <= k <= pages):
        if n - prev > 1:
            out.append(None)
        out.append(n)
        prev = n
    return out