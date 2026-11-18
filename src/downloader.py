"""Downloads one file / a batch of files. Handles dedup.

Progress is reported to a `Progress` object (see src/progress.py),
which the web UI polls. What was downloaded lives in the library (src/db.py).

Files go to DOWNLOADS_DIR/<channel_id>/<type>/<date>_<message_id><ext>.

Two duplicate checks:
  1. before download — same Telegram file id already downloaded (forwards, re-posts)
  2. after download  — same content (SHA-256) already in this channel (re-uploads)
"""
import asyncio
from pathlib import Path
from src.config import DOWNLOADS_DIR
from src.dedup import hash_file
from src.utils import describe_error, guess_extension, get_media_key, media_folder, media_rel_path


def _discard(path) -> None:
    try:
        if path and path.exists():
            path.unlink()
    except OSError:
        pass


async def download_file(message, channel, library, progress):
    key = get_media_key(message)

    # ── dedup: already tracked AND (file still on disk OR deleted on purpose) → skip
    if key:
        row = library.get_file(key)
        if row and (row["status"] == "deleted" or (DOWNLOADS_DIR / row["path"]).is_file()):
            progress.file_end(message.id, "skipped")
            return "skipped"

    file_size = (message.file.size if message.file else 0) or 0
    if message.text:
        label = message.text.strip().replace("\n", " ")[:60]
    else:
        label = f"msg {message.id}"
    progress.file_start(message.id, label, file_size)

    rel = media_rel_path(channel["id"], message, guess_extension(message))
    dest = DOWNLOADS_DIR / rel
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        result_path = await message.download_media(
            file=str(dest),
            progress_callback=lambda cur, tot: progress.file_update(message.id, cur, tot),
        )
    except asyncio.CancelledError:
        _discard(dest)
        raise
    except Exception as e:
        _discard(dest)
        progress.log(f"✗ #{message.id} {describe_error(e)}", "error")
        progress.file_end(message.id, "error")
        return "error"

    if not result_path:
        _discard(dest)
        progress.file_end(message.id, "error")
        return "error"

    result = "downloaded"
    rel = Path(result_path).resolve().relative_to(DOWNLOADS_DIR.resolve()).as_posix()
    sha = await asyncio.to_thread(hash_file, Path(result_path))
    # No await between find_copy() and save_file(): parallel downloads in a batch can't both miss.
    existing = next((p for p in library.find_copy(channel["id"], sha, rel)
                     if (DOWNLOADS_DIR / p).is_file()), None)
    if existing:
        _discard(Path(result_path))
        rel = existing
        result = "duplicate"
        progress.log(f"= #{message.id} is a copy of {existing} — not kept", "warn")

    if key:
        library.save_file(
            key, channel["id"], message.id, rel, media_folder(message),
            message.file.mime_type if message.file else None, file_size, sha,
            message.message or None, message.date.isoformat() if message.date else None,
        )

    progress.file_end(message.id, result)
    return result


async def download_in_batches(messages, channel, batch_size, library, progress):
    downloaded = skipped = errors = duplicates = 0
    progress.channel_files(len(messages))
    library.channel_downloaded(channel["id"], channel["title"], channel["username"])

    for start in range(0, len(messages), batch_size):
        batch = messages[start:start + batch_size]
        results = await asyncio.gather(*(
            download_file(m, channel, library, progress) for m in batch
        ))
        downloaded += results.count("downloaded")
        skipped += results.count("skipped")
        errors += results.count("error")
        duplicates += results.count("duplicate")

    return downloaded, skipped, errors, duplicates
