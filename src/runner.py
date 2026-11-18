"""Orchestration: fetch channels, run a download job over a list of channels.

Channels are returned as normalized dicts:
    {id, title, entity, username, is_channel, is_group}

fetch_channels() uses GetDialogsRequest(hash=0) to BYPASS Telethon's
dialog cache — this is what fixes "menu shows fewer channels than app".
Includes: main folder + archived folder + extra channels (library.db + .env).
Extra channels carry `extra` (the identifier) and `extra_source` ("file" = added on the page | "env").

All messages go to a `log(msg, level)` callable; levels: info, ok, warn, error.
"""
import asyncio
from telethon.tl.functions.messages import GetDialogsRequest
from telethon.tl.types import (
    InputPeerEmpty,
    InputMessagesFilterVideo,
    InputMessagesFilterPhotos,
    InputMessagesFilterDocument,
    InputMessagesFilterPhotoVideo,
)
from src.config import EXTRA_CHANNELS
from src.downloader import download_in_batches
from src.utils import describe_error


# ─── media choice → (filter factory, folder name, mime filter) ───
FILTER_MAP = {
    "1": (InputMessagesFilterVideo,     "videos",    None),
    "2": (InputMessagesFilterPhotos,    "images",    None),
    "3": (InputMessagesFilterDocument,  "pdfs",      "application/pdf"),
    "4": (InputMessagesFilterDocument,  "zips",      "application/zip"),
    "5": (None,                         "all_media", None),
}

MEDIA_LABELS = {
    "1": "Videos only",
    "2": "Images only",
    "3": "PDFs only",
    "4": "ZIP files only",
    "5": "All media types",
}


def build_filter(choice: str):
    if choice not in FILTER_MAP:
        return None
    factory, folder, mime = FILTER_MAP[choice]
    return (factory() if factory else None), folder, mime


# ─── normalize one Chat object → channel dict ──────────────────
def normalize_chat(chat):
    """`chat` is a Telethon Channel or Chat from GetDialogsResponse.
    These ARE valid entities — client.get_messages(chat, ...) works."""
    is_channel = bool(getattr(chat, "broadcast", False))
    is_megagroup = bool(getattr(chat, "megagroup", False))
    is_group = not (is_channel or is_megagroup)

    return {
        "id": chat.id,
        "title": getattr(chat, "title", None) or f"Channel {chat.id}",
        "entity": chat,
        "username": getattr(chat, "username", None),
        "is_channel": is_channel or is_megagroup,
        "is_group": is_group,
        "member": True,
        "extra": None,
        "extra_source": None,
    }


def normalize_entity(entity, ident, source):
    is_channel = bool(
        getattr(entity, "broadcast", False) or
        (getattr(entity, "megagroup", False) is False
         and getattr(entity, "title", None) is not None)
    )
    return {
        "id": entity.id,
        "title": getattr(entity, "title", None) or f"Channel {entity.id}",
        "entity": entity,
        "username": getattr(entity, "username", None),
        "is_channel": is_channel,
        "is_group": not is_channel,
        "member": False,
        "extra": ident,
        "extra_source": source,
    }


# ─── fetch: server-side full list (bypasses cache) ─────────────
async def fetch_channels(client, log, saved_extras):
    """Fetch ALL channels/groups fresh from the server using hash=0.

    hash=0 tells Telegram: "pretend I have nothing cached, send the full list".
    This is the fix for "menu shows fewer channels than Telegram app".

    Returns (channels, failed) — failed = extras that could not be resolved:
    [{ident, source, error}].
    """
    # ── Fetch main (folder_id=0) ──
    try:
        resp = await client(GetDialogsRequest(
            offset_date=None, offset_id=0,
            offset_peer=InputPeerEmpty(),
            limit=500,            # ≥ this many channels/groups
            hash=0,               # ← CRITICAL: bypass cache
            folder_id=0,
        ))
    except Exception as e:
        log(f"✗ Failed to fetch main dialogs {describe_error(e)}", "error")
        return [], []

    # ── Fetch archived (folder_id=1) — merge, ignore errors ──
    archived_chats, archived_dialogs = [], []
    try:
        arch_resp = await client(GetDialogsRequest(
            offset_date=None, offset_id=0,
            offset_peer=InputPeerEmpty(),
            limit=500,
            hash=0,
            folder_id=1,
        ))
        archived_chats, archived_dialogs = arch_resp.chats, arch_resp.dialogs
    except Exception:
        pass  # no archived folder or API restriction — that's fine

    # `chats` also holds chats only mentioned in messages (e.g. forward sources);
    # only the ones with a dialog are really in your chat list.
    in_list = {getattr(d.peer, "channel_id", None) or getattr(d.peer, "chat_id", None)
               for d in list(resp.dialogs) + list(archived_dialogs)}

    # ── Merge, dedupe by id ──
    seen_ids = set()
    all_chats = []
    unreadable = {}
    for chat in list(resp.chats) + list(archived_chats):
        if not hasattr(chat, "title"):
            continue  # skip User records (bots, private chats)
        if chat.id in seen_ids or chat.id not in in_list:
            continue
        seen_ids.add(chat.id)
        reason = _unreadable_reason(chat)
        if reason:
            unreadable[reason] = unreadable.get(reason, 0) + 1
            continue
        all_chats.append(chat)

    normalized = [normalize_chat(chat) for chat in all_chats]

    # ── Merge extra channels ──
    by_id = {c["id"]: c for c in normalized}
    failed = []
    extras = ([(i, "file") for i in saved_extras]
              + [(i, "env") for i in EXTRA_CHANNELS])
    for ident, source in extras:
        try:
            entity = await client.get_entity(ident)
        except Exception as e:
            log(f"⚠ Could not resolve '{ident}' {describe_error(e)}", "warn")
            failed.append({"ident": ident, "source": source, "error": str(e)})
            continue
        if entity.id in by_id:
            # Already a member: just remember it is listed as extra, so it can be removed.
            if not by_id[entity.id]["extra"]:
                by_id[entity.id].update(extra=ident, extra_source=source)
            continue
        by_id[entity.id] = normalize_entity(entity, ident, source)
        normalized.append(by_id[entity.id])

    kept_ids = {c.id for c in all_chats}
    archived_count = sum(1 for c in archived_chats if c.id in kept_ids)
    log(f"✓ Fetched {len(normalized)} channels/groups fresh from server"
        f"{' (includes ' + str(archived_count) + ' archived)' if archived_count else ''}",
        "ok")
    if unreadable:
        parts = ", ".join(f"{n} {r}" for r, n in unreadable.items())
        log(f"○ Skipped {sum(unreadable.values())} chats you can't read: {parts}", "warn")

    return normalized, failed


# ─── internal helpers ───────────────────────────────────────────
def _unreadable_reason(chat):
    """Dialogs that still list a chat whose messages can't be searched."""
    kind = type(chat).__name__
    if kind == "ChannelForbidden":
        return "banned or private"
    if kind == "ChatForbidden":
        return "left or removed"
    if getattr(chat, "migrated_to", None) or getattr(chat, "deactivated", False):
        return "upgraded to supergroup"
    return None


async def _probe_has_media(client, entity, filter_type, mime, log):
    """True / False = has media or not; None = the probe itself failed (already logged)."""
    if filter_type is None or mime:
        return True
    try:
        probe = await client.get_messages(entity, filter=filter_type, limit=1)
        return len(probe) > 0
    except Exception as e:
        log(f"  ✗ Probe failed {describe_error(e)}", "error")
        return None


async def _fetch_all_media(client, entity, limit, log):
    seen, combined = set(), []

    pv = await client.get_messages(entity, filter=InputMessagesFilterPhotoVideo(), limit=limit)
    for m in pv:
        if m.id not in seen:
            seen.add(m.id)
            combined.append(m)
    log(f"  → {len(pv)} photos/videos")

    docs = await client.get_messages(entity, filter=InputMessagesFilterDocument(), limit=limit)
    for m in docs:
        if m.id not in seen:
            seen.add(m.id)
            combined.append(m)
    log(f"  → {len(docs)} documents")

    combined.sort(key=lambda m: m.id, reverse=True)
    return combined


async def _fetch_messages(client, entity, filter_type, mime, max_messages, log):
    limit = max_messages if max_messages > 0 else None
    if filter_type is None and mime is None:
        return await _fetch_all_media(client, entity, limit, log)
    msgs = await client.get_messages(entity, filter=filter_type, limit=limit)
    if mime:
        msgs = [m for m in msgs if m.document and m.document.mime_type == mime]
    return msgs


# ─── run: one or more channels ──────────────────────────────────
async def run_job(client, channels, filter_type, folder_name, mime,
                  batch_size, max_messages, library, progress):
    log = progress.log
    for i, ch in enumerate(channels, 1):
        title = ch["title"]
        entity = ch["entity"]
        progress.channel_start(i, title)
        log(f"[{i}/{len(channels)}] {title}")

        try:
            ok = await asyncio.wait_for(
                _probe_has_media(client, entity, filter_type, mime, log), timeout=30
            )
        except asyncio.TimeoutError:
            log("  ✗ Probe failed [network] timed out after 30 s", "error")
            continue
        if ok is None:
            continue
        if not ok:
            log(f"  ○ No {folder_name}, skipping", "warn")
            continue

        try:
            msgs = await asyncio.wait_for(
                _fetch_messages(client, entity, filter_type, mime, max_messages, log),
                timeout=180
            )
        except asyncio.TimeoutError:
            log("  ✗ Fetch failed [network] timed out after 180 s", "error")
            continue
        except Exception as e:
            log(f"  ✗ Fetch failed {describe_error(e)}", "error")
            continue

        if not msgs:
            log("  ○ No matching media, skipping", "warn")
            continue

        log(f"  → downloading {len(msgs)} items")
        d, s, e, dup = await download_in_batches(msgs, ch, batch_size, library, progress)
        log(f"  Downloaded {d} | Skipped {s} | Duplicates {dup} | Errors {e}", "ok")
