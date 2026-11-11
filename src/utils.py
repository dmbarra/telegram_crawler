"""Pure helpers — no I/O, no globals."""
import os
import re
import mimetypes
from pathlib import Path


# Windows reserved filenames (cannot be used, even with extension)
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}

# Leave room for collision suffix (_1, _2, …) and Windows MAX_PATH
_MAX_STEM_LEN = 120


def safe_filename(name: str, default_ext: str = "") -> str:
    """Make any string safe as a Windows/macOS/Linux filename.

    Handles: newlines, tabs, control chars, invalid chars, reserved names,
    trailing dots/spaces, and length truncation (Windows MAX_PATH).
    """
    if not name:
        return "unnamed"

    # 1) Collapse newlines / tabs / carriage returns into a single space
    name = re.sub(r"[\r\n\t]+", " ", name)

    # 2) Strip control chars (0x00-0x1F, 0x7F) — these are what caused Errno 22
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)

    # 3) Replace Windows-invalid chars
    name = re.sub(r'[\\/*?:"<>|]', "_", name)

    # 4) Collapse whitespace, trim
    name = re.sub(r"\s+", " ", name).strip()

    # 5) Strip trailing dots and spaces (Windows silently rejects these)
    name = name.rstrip(" .")

    # 6) Append extension if requested
    if default_ext and not name.lower().endswith(default_ext.lower()):
        name += default_ext

    # 7) Truncate the stem (keep extension intact)
    stem, ext = os.path.splitext(name)
    if len(stem) > _MAX_STEM_LEN:
        stem = stem[:_MAX_STEM_LEN].rstrip(" .")
        name = stem + ext

    # 8) Avoid reserved names
    root = name.split(".", 1)[0].upper()
    if root in _RESERVED:
        name = "_" + name

    return name or "unnamed"


def guess_extension(message) -> str:
    if message.document and message.document.mime_type:
        ext = mimetypes.guess_extension(message.document.mime_type)
        if ext:
            return ext
    if message.video:
        return ".mp4"
    if message.photo:
        return ".jpg"
    return ""


def get_media_key(message):
    """Stable ID — same uploaded file → same key, even if forwarded."""
    if message.document:
        return f"doc:{message.document.id}"
    if message.photo:
        return f"photo:{message.photo.id}"
    return None


def resolve_unique_path(directory: Path, filename: str) -> Path:
    """Return a non-colliding path inside directory.

    Also re-truncates on collision so `_1`, `_2` suffixes never push the
    filename past the OS limit.
    """
    directory.mkdir(parents=True, exist_ok=True)
    stem, ext = os.path.splitext(filename)
    candidate = filename
    counter = 1
    while (directory / candidate).exists():
        # Keep the stem within limits even after adding "_N"
        suffix = f"_{counter}"
        trimmed = stem[: _MAX_STEM_LEN - len(suffix)]
        candidate = f"{trimmed}{suffix}{ext}"
        counter += 1
    return directory / candidate