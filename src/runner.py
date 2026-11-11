"""Orchestration: fetch channels, single-channel run, all-channels run.

Channels are returned as normalized dicts:
    {id, title, entity, username, is_channel, is_group}

fetch_channels() uses GetDialogsRequest(hash=0) to BYPASS Telethon's
dialog cache — this is what fixes "menu shows fewer channels than app".
Includes: main folder + archived folder + EXTRA_CHANNELS, minus CHANNELS_SKIP.
"""
import asyncio
from colorama import Fore
from telethon.tl.functions.messages import GetDialogsRequest
from telethon.tl.types import (
    InputPeerEmpty,
    InputMessagesFilterVideo,
    InputMessagesFilterPhotos,
    InputMessagesFilterDocument,
    InputMessagesFilterPhotoVideo,
)
from src.config import (
    MAX_MESSAGES, BATCH_SIZE, CHANNELS_SKIP, EXTRA_CHANNELS,
)
from src.utils import safe_filename
from src.downloader import download_in_batches
from src.storage import save_state


# ─── media choice → (filter factory, folder name, mime filter) ───
FILTER_MAP = {
    "1": (InputMessagesFilterVideo,     "videos",    None),
    "2": (InputMessagesFilterPhotos,    "images",    None),
    "3": (InputMessagesFilterDocument,  "pdfs",      "application/pdf"),
    "4": (InputMessagesFilterDocument,  "zips",      "application/zip"),
    "5": (None,                         "all_media", None),
}


def build_filter(choice: str):
    if choice not in FILTER_MAP:
        return None
    factory, folder, mime = FILTER_MAP[choice]
    return (factory() if factory else None), folder, mime


# ─── normalize one Chat object → channel dict ──────────────────
def _normalize_chat(chat):
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
    }


def _normalize_entity(entity):
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
    }


# ─── fetch: server-side full list (bypasses cache) ─────────────
async def fetch_channels(client):
    """Fetch ALL channels/groups fresh from the server using hash=0.

    hash=0 tells Telegram: "pretend I have nothing cached, send the full list".
    This is the fix for "menu shows fewer channels than Telegram app".
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
        print(f"{Fore.RED}✗ Failed to fetch main dialogs: {e}{Fore.RESET}")
        return []

    # ── Fetch archived (folder_id=1) — merge, ignore errors ──
    archived_chats = []
    try:
        arch_resp = await client(GetDialogsRequest(
            offset_date=None, offset_id=0,
            offset_peer=InputPeerEmpty(),
            limit=500,
            hash=0,
            folder_id=1,
        ))
        archived_chats = arch_resp.chats
    except Exception:
        pass  # no archived folder or API restriction — that's fine

    # ── Merge, dedupe by id ──
    seen_ids = set()
    all_chats = []
    for chat in list(resp.chats) + list(archived_chats):
        if not hasattr(chat, "title"):
            continue  # skip User records (bots, private chats)
        if chat.id in seen_ids:
            continue
        seen_ids.add(chat.id)
        all_chats.append(chat)

    # ── Apply skip + normalize ──
    normalized = []
    excluded = 0
    for chat in all_chats:
        if chat.id in CHANNELS_SKIP:
            excluded += 1
            continue
        normalized.append(_normalize_chat(chat))

    # ── Merge EXTRA_CHANNELS ──
    added = 0
    current_ids = {c["id"] for c in normalized}
    for ident in EXTRA_CHANNELS:
        try:
            entity = await client.get_entity(ident)
        except Exception as e:
            print(f"{Fore.YELLOW}⚠ Could not resolve '{ident}': {e}{Fore.RESET}")
            continue
        if entity.id in current_ids or entity.id in CHANNELS_SKIP:
            continue
        normalized.append(_normalize_entity(entity))
        current_ids.add(entity.id)
        added += 1

    archived_count = sum(1 for c in archived_chats
                        if hasattr(c, "title") and c.id not in CHANNELS_SKIP)
    print(f"{Fore.GREEN}✓ Fetched {len(normalized)} channels/groups fresh from server"
          f"{' (includes ' + str(archived_count) + ' archived)' if archived_count else ''}"
          f"{Fore.RESET}")
    if excluded:
        print(f"{Fore.YELLOW}Excluded {excluded} channel(s) via CHANNELS_SKIP{Fore.RESET}")
    if added:
        print(f"{Fore.GREEN}+ Added {added} channel(s) via EXTRA_CHANNELS{Fore.RESET}")

    return normalized


def print_channels(channels) -> None:
    print(f"\n{Fore.CYAN}Your Channels / Groups:{Fore.RESET}")
    for i, c in enumerate(channels, 1):
        uname = f"@{c['username']}" if c["username"] else "(private)"
        kind = "channel" if c["is_channel"] else "group"
        title = (c["title"] or "Untitled")[:35]
        print(f"  {i:3}. [{kind:7}] {title:35}  {uname:20}  (ID: {c['id']})")


# ─── internal helpers ───────────────────────────────────────────
async def _probe_has_media(client, entity, filter_type, mime) -> bool:
    if filter_type is None or mime:
        return True
    try:
        probe = await client.get_messages(entity, filter=filter_type, limit=1)
        return len(probe) > 0
    except Exception:
        return False


async def _fetch_all_media(client, entity, limit):
    seen, combined = set(), []

    print(f"    {Fore.YELLOW}→ fetching photos + videos...{Fore.RESET}")
    pv = await client.get_messages(entity, filter=InputMessagesFilterPhotoVideo(), limit=limit)
    for m in pv:
        if m.id not in seen:
            seen.add(m.id)
            combined.append(m)
    print(f"    {Fore.YELLOW}→ {len(pv)} photos/videos{Fore.RESET}")

    print(f"    {Fore.YELLOW}→ fetching documents...{Fore.RESET}")
    docs = await client.get_messages(entity, filter=InputMessagesFilterDocument(), limit=limit)
    for m in docs:
        if m.id not in seen:
            seen.add(m.id)
            combined.append(m)
    print(f"    {Fore.YELLOW}→ {len(docs)} documents{Fore.RESET}")

    combined.sort(key=lambda m: m.id, reverse=True)
    return combined


async def _fetch_messages(client, entity, filter_type, mime):
    limit = MAX_MESSAGES if MAX_MESSAGES > 0 else None
    if filter_type is None and mime is None:
        return await _fetch_all_media(client, entity, limit)
    msgs = await client.get_messages(entity, filter=filter_type, limit=limit)
    if mime:
        msgs = [m for m in msgs if m.document and m.document.mime_type == mime]
    return msgs


# ─── run: single channel ────────────────────────────────────────
async def run_single(client, channels, filter_type, folder_name, mime, state):
    try:
        idx = int(input(f"\n{Fore.CYAN}Enter channel number: {Fore.RESET}")) - 1
        if not (0 <= idx < len(channels)):
            raise ValueError
    except ValueError:
        print(f"{Fore.RED}Invalid number.{Fore.RESET}")
        return

    selected = channels[idx]
    ch_folder = safe_filename(selected["title"] or f"channel_{selected['id']}")
    full_folder = f"{ch_folder}/{folder_name}"

    print(f"{Fore.YELLOW}Fetching messages from {selected['title']}...{Fore.RESET}")
    try:
        msgs = await asyncio.wait_for(
            _fetch_messages(client, selected["entity"], filter_type, mime), timeout=180
        )
    except asyncio.TimeoutError:
        print(f"{Fore.RED}✗ fetch timed out after 180s{Fore.RESET}")
        return
    print(f"{Fore.GREEN}Found {len(msgs)} matching messages.{Fore.RESET}")

    if not msgs:
        print(f"{Fore.RED}No media found.{Fore.RESET}")
        return

    d, s, e = await download_in_batches(msgs, full_folder, BATCH_SIZE, state, selected["title"])
    print(f"\n{Fore.GREEN}Done. {d} downloaded, {s} skipped, {e} errors.{Fore.RESET}")


# ─── run: all channels ──────────────────────────────────────────
async def run_all(client, channels, filter_type, folder_name, mime, state):
    tot_d = tot_s = tot_e = proc = 0
    print(f"\n{Fore.YELLOW}Scanning {len(channels)} channels for {folder_name}...{Fore.RESET}\n")

    for i, ch in enumerate(channels, 1):
        title = ch["title"]
        print(f"{Fore.CYAN}[{i}/{len(channels)}] {title}{Fore.RESET}")

        entity = ch["entity"]   # ← use entity directly, no re-fetch needed
        if entity is None:
            print(f"  {Fore.RED}✗ No entity for channel{Fore.RESET}")
            continue

        try:
            ok = await asyncio.wait_for(
                _probe_has_media(client, entity, filter_type, mime), timeout=30
            )
        except asyncio.TimeoutError:
            print(f"  {Fore.RED}✗ probe timed out{Fore.RESET}")
            continue
        if not ok:
            print(f"  {Fore.YELLOW}○ No {folder_name}, skipping{Fore.RESET}")
            continue

        try:
            msgs = await asyncio.wait_for(
                _fetch_messages(client, entity, filter_type, mime), timeout=180
            )
        except asyncio.TimeoutError:
            print(f"  {Fore.RED}✗ fetch timed out after 180s — skipping{Fore.RESET}")
            continue
        except Exception as e:
            print(f"  {Fore.RED}✗ Fetch failed: {e}{Fore.RESET}")
            continue

        if not msgs:
            print(f"  {Fore.YELLOW}○ No matching media, skipping{Fore.RESET}")
            continue

        proc += 1
        ch_folder = safe_filename(title or f"channel_{ch['id']}")
        full_folder = f"{ch_folder}/{folder_name}"
        print(f"  {Fore.GREEN}→ downloading {len(msgs)} items{Fore.RESET}")

        d, s, e = await download_in_batches(msgs, full_folder, BATCH_SIZE, state, title)
        tot_d += d; tot_s += s; tot_e += e
        print(f"  Downloaded: {Fore.GREEN}{d}{Fore.RESET} | "
              f"Skipped: {Fore.YELLOW}{s}{Fore.RESET} | "
              f"Errors: {Fore.RED}{e}{Fore.RESET}\n")
        save_state(state)

    print(f"\n{Fore.GREEN}═══ SUMMARY ═══{Fore.RESET}")
    print(f"  Channels scanned   : {len(channels)}")
    print(f"  Channels with media: {proc}")
    print(f"  New downloads      : {Fore.GREEN}{tot_d}{Fore.RESET}")
    print(f"  Duplicates skipped : {Fore.YELLOW}{tot_s}{Fore.RESET}")
    print(f"  Errors             : {Fore.RED}{tot_e}{Fore.RESET}")