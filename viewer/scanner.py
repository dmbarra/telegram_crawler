"""Walk downloads/ once, cache the result, rescan only on demand."""
from viewer.config import DOWNLOADS_DIR, ALL_EXTS
from viewer.helpers import fmt_size, classify_ext

_cache = {"data": None}


def scan_media(force: bool = False) -> dict:
    """Returns {channel_name: [ {rel, name, type, size, size_str, mtime}, ... ]}.

    Files are stored unsorted — the route layer decides the order.
    Cache lives until force=True.
    """
    if not force and _cache["data"] is not None:
        return _cache["data"]

    result: dict[str, list] = {}
    if DOWNLOADS_DIR.is_dir():
        for channel_dir in sorted(DOWNLOADS_DIR.iterdir()):
            if not channel_dir.is_dir():
                continue

            files = []
            for f in channel_dir.rglob("*"):
                if not f.is_file() or f.suffix.lower() not in ALL_EXTS:
                    continue
                try:
                    stat = f.stat()
                except OSError:
                    continue
                files.append({
                    "rel": f.relative_to(DOWNLOADS_DIR).as_posix(),
                    "name": f.name,
                    "type": classify_ext(f.suffix),
                    "size": stat.st_size,
                    "size_str": fmt_size(stat.st_size),
                    "mtime": stat.st_mtime,
                })

            if files:
                result[channel_dir.name] = files

    _cache["data"] = result
    return result


def invalidate() -> None:
    _cache["data"] = None