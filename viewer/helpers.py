"""Small, side-effect-free helpers."""
from viewer.config import IMAGE_EXTS, VIDEO_EXTS

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