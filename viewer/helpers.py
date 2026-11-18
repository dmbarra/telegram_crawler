"""Small, side-effect-free helpers."""
from urllib.parse import quote

CHANNEL_SORTS = {
    "recent": "Newest download",
    "name":   "Name A → Z",
    "files":  "Most files",
}
DEFAULT_CHANNEL_SORT = "recent"

SORT_OPTIONS = {
    "newest":    "Newest first",
    "oldest":    "Oldest first",
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


def kind_to_type(kind: str) -> str:
    """Library kind (images/videos/audio) → tile type used by the templates."""
    return {"images": "image", "videos": "video"}.get(kind, "audio")


def card(row, old_label: str = "") -> dict:
    """One channel card (viewer index) or row (files manager) from Library.channel_cards()."""
    return {
        "id": row["channel_id"],
        "title": row["title"],
        "count": row["n"],
        "counts": {"image": row["images"], "video": row["videos"], "audio": row["audio"]},
        "size": row["size"] or 0,
        "size_str": fmt_size(row["size"] or 0),
        "mtime": row["last"] or "",
        "last": (row["last"] or "")[:10],
        "cover": _cover(row["cover"]),
    }


def _cover(value):
    if not value:
        return None
    rel, kind = value.rsplit("|", 1)
    return {"rel": rel, "type": kind_to_type(kind)}


def file_item(row) -> dict:
    name = row["path"].rsplit("/", 1)[-1]
    caption = (row["caption"] or "").strip().replace("\n", " ")
    return {
        "rel": row["path"],
        "name": name,
        "label": caption[:80] or name,
        "caption": caption,
        "kind": row["kind"],
        "type": kind_to_type(row["kind"]),
        "size": row["size"] or 0,
        "size_str": fmt_size(row["size"] or 0),
        "date": (row["at"] or "")[:16].replace("T", " "),
        "rating": row["rating"] or "",
        "info": media_info_str(row["width"], row["height"], row["duration"]),
        "ml": ml_str(row["analyzed"], row["face_count"], row["ml_error"]),
        "face_count": row["face_count"] or 0,
        "channel": row["channel_id"],
    }


def ml_str(analyzed, face_count, error) -> str:
    if not analyzed:
        return ""
    if error:
        return "Analysis failed"
    return f"Analyzed · {face_count} face{'s' if face_count != 1 else ''}"


def fmt_duration(seconds: float) -> str:
    s = round(seconds)
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def media_info_str(width, height, duration) -> str:
    parts = []
    if width and height:
        parts.append(f"{width}×{height}")
    if duration:
        parts.append(fmt_duration(duration))
    return " · ".join(parts)


def sort_channels(cards: list, sort_key: str) -> list:
    if sort_key == "name":
        return sorted(cards, key=lambda c: c["title"].lower())
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

def paginate(items: list, per: int, page: int) -> dict:
    """Slice one page out of a full list, plus what the pager template needs."""
    pages = max(1, -(-len(items) // per))
    page = min(max(page, 1), pages)
    return {"items": items[(page - 1) * per:page * per], "page": page, "pages": pages,
            "window": page_window(page, pages), "first_index": (page - 1) * per + 1}


def _face_crop(row) -> str:
    """CSS background that shows a square around the face, from the full image."""
    W, H = row["width"], row["height"]
    if not (W and H and row["w"]):
        return ""
    side = min(max(row["w"], row["h"]) * 1.6, W, H)
    left = min(max(row["x"] + row["w"] / 2 - side / 2, 0), W - side)
    top = min(max(row["y"] + row["h"] / 2 - side / 2, 0), H - side)
    px = left / (W - side) * 100 if W > side else 0
    py = top / (H - side) * 100 if H > side else 0
    return (f"background-size: {W / side * 100:.1f}% {H / side * 100:.1f}%; "
            f"background-position: {px:.1f}% {py:.1f}%;")


def group_card(row, kind: str) -> dict:
    is_video = row["cover_kind"] == "videos"
    src = ("/thumb?p=" if is_video else "/media?p=") + quote(row["cover"])
    return {
        "id": row["id"],
        "images": row["images"],
        "channels": row["channels"],
        "src": src,
        "crop": _face_crop(row) if kind == "face" else "",
    }
