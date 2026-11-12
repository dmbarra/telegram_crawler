"""Downloads one file / a batch of files. Handles dedup + collisions.

Progress is reported to a `Progress` object (see src/progress.py),
which the web UI polls.
"""
import os
import asyncio
from src.config import DOWNLOADS_DIR
from src.utils import safe_filename, guess_extension, get_media_key, resolve_unique_path
from src.storage import save_state


def _discard(path) -> None:
    try:
        if path and path.exists():
            path.unlink()
    except OSError:
        pass


async def download_file(message, folder_name, state, channel_title, progress):
    key = get_media_key(message)

    # ── dedup: already tracked AND file still on disk → skip
    if key and key in state["downloaded"]:
        path = state["downloaded"][key].get("path")
        if path and os.path.exists(path):
            progress.file_end(message.id, "skipped")
            return "skipped"

    file_size = (message.file.size if message.file else 0) or 0
    if message.text:
        label = message.text.strip().replace("\n", " ")[:60]
    else:
        label = f"msg {message.id}"
    progress.file_start(message.id, label, file_size)

    dest = None
    try:
        if message.text:
            base = safe_filename(message.text.strip(), guess_extension(message))
        else:
            base = f"{message.id}{guess_extension(message)}"

        dest = resolve_unique_path(DOWNLOADS_DIR / folder_name, base)

        result_path = await message.download_media(
            file=str(dest),
            progress_callback=lambda cur, tot: progress.file_update(message.id, cur, tot),
        )
    except asyncio.CancelledError:
        _discard(dest)
        raise
    except Exception as e:
        _discard(dest)
        progress.log(f"✗ #{message.id}: {e}", "error")
        progress.file_end(message.id, "error")
        return "error"

    if not result_path:
        _discard(dest)
        progress.file_end(message.id, "error")
        return "error"

    if key:
        state["downloaded"][key] = {
            "channel": channel_title,
            "message_id": message.id,
            "path": str(result_path),
            "size": file_size,
        }

    progress.file_end(message.id, "downloaded")
    return "downloaded"


async def download_in_batches(messages, folder_name, batch_size, state, channel_title, progress):
    downloaded = skipped = errors = 0
    progress.channel_files(len(messages))

    for start in range(0, len(messages), batch_size):
        batch = messages[start:start + batch_size]
        results = await asyncio.gather(*(
            download_file(m, folder_name, state, channel_title, progress)
            for m in batch
        ))
        downloaded += results.count("downloaded")
        skipped += results.count("skipped")
        errors += results.count("error")
        save_state(state)

    return downloaded, skipped, errors
