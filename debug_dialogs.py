"""One-shot diagnostic — identify account + list ALL chats with flags."""
import asyncio
from dotenv import load_dotenv
load_dotenv()

from telethon import TelegramClient
from telethon.tl.functions.messages import GetDialogsRequest
from telethon.tl.types import InputPeerEmpty
from src.config import API_ID, API_HASH, SESSION_NAME, CHANNELS_SKIP


def flags(chat):
    f = []
    if getattr(chat, "broadcast", False):        f.append("broadcast")
    if getattr(chat, "megagroup", False):        f.append("megagroup")
    if getattr(chat, "left", False):             f.append("LEFT")
    if getattr(chat, "deactivated", False):      f.append("DEACTIVATED")
    if getattr(chat, "kicked", False):           f.append("KICKED")
    if getattr(chat, "creator", False):          f.append("creator")
    if getattr(chat, "admin_rights", None):      f.append("admin")
    if getattr(chat, "restricted", False):       f.append("restricted")
    if getattr(chat, "scam", False):             f.append("SCAM")
    if getattr(chat, "fake", False):             f.append("FAKE")
    if getattr(chat, "gigagroup", False):        f.append("gigagroup")
    if not getattr(chat, "username", None):      f.append("no-username")
    return ",".join(f) or "normal"


async def dump_all(c, label, folder_id=None):
    """Fetch ALL dialogs from a given folder and print each."""
    print(f"\n{'─' * 95}")
    print(f"{label}")
    print(f"{'─' * 95}")
    try:
        resp = await c(GetDialogsRequest(
            offset_date=None, offset_id=0,
            offset_peer=InputPeerEmpty(),
            limit=500,
            hash=0,
            folder_id=folder_id,
        ))
    except Exception as e:
        print(f"  ❌ error: {e}")
        return []

    chats = resp.chats
    channels_groups = [ch for ch in chats
                       if hasattr(ch, "title")]  # excludes user records

    print(f"  chats: {len(chats)}  |  channels+groups: {len(channels_groups)}")
    print()
    for ch in channels_groups:
        skip = " [CHANNELS_SKIP]" if ch.id in CHANNELS_SKIP else ""
        title = (getattr(ch, "title", None) or "?")[:42]
        print(f"    id={ch.id:>15}  {title:42}  ({flags(ch)}){skip}")

    return channels_groups


async def main():
    async with TelegramClient(SESSION_NAME, API_ID, API_HASH) as c:
        me = await c.get_me()

        print("═" * 95)
        print("  ACCOUNT IDENTITY")
        print("═" * 95)
        print(f"  First name: {me.first_name}")
        print(f"  Last name:  {me.last_name or '(none)'}")
        print(f"  Username:   {'@' + me.username if me.username else '(none)'}")
        print(f"  Phone:      {'+' + me.phone if me.phone else '(hidden by privacy)'}")
        print(f"  User ID:    {me.id}    ← compare against Telegram Web profile")
        print(f"  Bot:        {me.bot}")

        # Print any custom folders the account has
        try:
            from telethon.tl.functions.messages import GetDialogFiltersRequest
            filters = await c(GetDialogFiltersRequest())
            print(f"\n  Custom folders ({len(filters.filters)}):")
            for f in filters.filters:
                name = getattr(f, "title", "?")
                print(f"    - {name}")
        except Exception:
            print(f"\n  Custom folders: (could not fetch — older API or none)")

        # Dump each folder
        await dump_all(c, "FOLDER 0 — Main (non-archived)", folder_id=0)
        await dump_all(c, "FOLDER 1 — Archived", folder_id=1)

        # Also try Telethon's iter_dialogs which paginates automatically
        print(f"\n{'─' * 95}")
        print("Telethon iter_dialogs() — full paginated iteration")
        print(f"{'─' * 95}")
        total = 0
        groups = 0
        async for d in c.iter_dialogs():
            total += 1
            if d.is_channel or d.is_group:
                groups += 1
        print(f"  Total dialogs: {total}  |  channels+groups: {groups}")


if __name__ == "__main__":
    asyncio.run(main())