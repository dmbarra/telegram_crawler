"""Small helpers — no globals."""
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


def media_folder(message) -> str:
    """Type sub-folder for a message's media: videos, images, audio, pdf, zip or other."""
    if message.photo:
        return "images"
    if message.video or message.gif:
        return "videos"
    if message.audio or message.voice:
        return "audio"
    return folder_for_mime((message.document.mime_type or "") if message.document else "")


def folder_for_mime(mime: str) -> str:
    if mime.startswith("image/"):
        return "images"
    if mime.startswith("video/"):
        return "videos"
    if mime.startswith("audio/"):
        return "audio"
    if mime == "application/pdf":
        return "pdf"
    if mime == "application/zip":
        return "zip"
    return "other"


_RPC_REASONS = {
    "PEER_ID_INVALID": ("invalid chat", "chat was upgraded, left, or deleted"),
    "CHANNEL_INVALID": ("invalid chat", "chat was upgraded, left, or deleted"),
    "CHAT_ID_INVALID": ("invalid chat", "chat was upgraded, left, or deleted"),
    "CHANNEL_PRIVATE": ("no access", "you were removed, or it is private"),
    "CHAT_FORBIDDEN": ("no access", "you were removed, or it is private"),
    "USER_BANNED_IN_CHANNEL": ("banned", "account is banned in this chat"),
    "CHAT_ADMIN_REQUIRED": ("admin only", "only admins can read this"),
    "FILE_REFERENCE_EXPIRED": ("file expired", "run the job again"),
}


def describe_error(e: Exception) -> str:
    """One short line: '[category] reason'. Works on Telethon RPC errors without importing Telethon."""
    seconds = getattr(e, "seconds", None)
    if seconds is not None:
        return f"[rate limit] Telegram asks to wait {seconds} s"
    code = getattr(e, "message", None)
    if isinstance(code, str) and code in _RPC_REASONS:
        category, hint = _RPC_REASONS[code]
        return f"[{category}] {hint}"
    if isinstance(e, (TimeoutError, ConnectionError)):
        return f"[network] {type(e).__name__}, check the connection"
    if isinstance(e, OSError):
        return f"[disk] {e.strerror or e}"
    if isinstance(code, str) and getattr(e, "code", None) is not None:
        return f"[telegram {e.code}] {code}"
    return f"[{type(e).__name__}] {e}"


def media_rel_path(channel_id: int, message, ext: str) -> str:
    """<channel_id>/<type>/<date>_<message_id><ext> — no titles or captions, so nothing leaks."""
    date = message.date.strftime("%Y-%m-%d") if message.date else "0000-00-00"
    return f"{channel_id}/{media_folder(message)}/{date}_{message.id}{ext}"
