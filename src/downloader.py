"""Downloads one file / a batch of files. Handles dedup + collisions.

Progress display:
  • one persistent channel-level bar (updates after each batch)
  • one short-lived bar per file (leave=False → auto-clears)
  • tqdm.write() for batch headers and errors so bars don't break
"""
import os
import asyncio
from colorama import Fore, Style
from tqdm.asyncio import tqdm
from src.config import DOWNLOADS_DIR
from src.utils import safe_filename, guess_extension, get_media_key, resolve_unique_path
from src.storage import save_state


async def download_file(message, folder_name, state, channel_title, position=1):
    key = get_media_key(message)

    # ── dedup: already tracked AND file still on disk → skip
    if key and key in state["downloaded"]:
        existing = state["downloaded"][key]
        path = existing.get("path")
        if path and os.path.exists(path):
            return "skipped"

    bar = None
    try:
        file_size = (
            message.video.size if message.video
            else message.document.size if message.document
            else 0
        )

        # Short caption-derived label for the bar
        if message.text:
            label = message.text.strip().replace("\n", " ")[:28]
        else:
            label = f"msg {message.id}"

        bar = tqdm(
            total=file_size,
            desc=f"    ↓ {label}",
            ncols=110, unit="B", unit_scale=True,
            leave=False, position=position, mininterval=0.1,
            # NOTE: %% = literal percent, %s = Fore.BLUE / Style.RESET_ALL
            bar_format=("{l_bar}%s{bar}%s| {n_fmt}/{total_fmt} {unit} "
                        "| {rate_fmt} | ETA {remaining}"
                        % (Fore.BLUE, Style.RESET_ALL)),
        )

        dest_dir = DOWNLOADS_DIR / folder_name

        if message.text:
            base = safe_filename(message.text.strip(), guess_extension(message))
        else:
            ext = guess_extension(message)
            base = f"{message.id}{ext or ''}"

        dest = resolve_unique_path(dest_dir, base)

        result_path = await message.download_media(
            file=str(dest),
            progress_callback=lambda cur, tot: bar.update(cur - bar.n) if tot else None,
        )

        bar.close()
        bar = None

        if key and result_path:
            state["downloaded"][key] = {
                "channel": channel_title,
                "message_id": message.id,
                "path": str(result_path),
                "size": file_size,
            }

        return "downloaded"

    except Exception as e:
        if bar is not None:
            try:
                bar.close()
            except Exception:
                pass
        tqdm.write(f"    {Fore.RED}✗ #{message.id}: {e}{Style.RESET_ALL}")
        return "error"


async def download_in_batches(messages, folder_name, batch_size, state, channel_title):
    total = len(messages)
    downloaded = skipped = errors = 0
    total_batches = (total + batch_size - 1) // batch_size

    # ── persistent channel-level bar (position 0, stays until channel done)
    overall_bar = tqdm(
        total=total,
        desc=f"  {channel_title[:40]:40}",
        ncols=110, unit="file",
        position=0, leave=True, mininterval=0.1,
        # NOTE: %% is a literal '%', %s pulls in the color codes
        bar_format=("{l_bar}%s{bar}%s| {n_fmt}/{total_fmt} files "
                    "| {percentage:3.0f}%% | {elapsed}/{remaining}"
                    % (Fore.GREEN, Style.RESET_ALL)),
    )

    for start in range(0, total, batch_size):
        batch = messages[start:start + batch_size]
        batch_num = start // batch_size + 1

        tqdm.write(f"  {Fore.CYAN}→ Batch {batch_num}/{total_batches} "
                   f"({len(batch)} files){Style.RESET_ALL}")

        tasks = [
            download_file(m, folder_name, state, channel_title, position=idx + 1)
            for idx, m in enumerate(batch)
        ]
        results = await asyncio.gather(*tasks)

        for r in results:
            if r == "downloaded": downloaded += 1
            elif r == "skipped":  skipped += 1
            elif r == "error":    errors += 1

        overall_bar.update(len(batch))
        save_state(state)

    overall_bar.close()
    return downloaded, skipped, errors