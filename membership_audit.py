"""Audit how you became a member of each group/channel.

For every group/channel you're in, looks for the earliest join-related
service message and reports: how you joined, when, and (if applicable)
which user added/invited you.

NO invite links are extracted or displayed.
"""
import asyncio
from dotenv import load_dotenv

load_dotenv()

from telethon import TelegramClient                                   # noqa: E402
from telethon.tl.types import (                                       # noqa: E402
    MessageActionChatAddUser,
    MessageActionChatCreate,
    MessageActionChatJoinedByLink,
    MessageActionChatJoinedByRequest,
)
from src.config import API_ID, API_HASH, SESSION_NAME                  # noqa: E402


async def resolve_user(client, uid) -> str:
    if not uid:
        return ""
    try:
        u = await client.get_entity(uid)
        name = (getattr(u, "first_name", "") or getattr(u, "title", "") or "").strip()
        last = (getattr(u, "last_name", "") or "").strip()
        label = f"{name} {last}".strip() or f"id={uid}"
        uname = getattr(u, "username", None)
        if uname:
            label += f" (@{uname})"
        return label
    except Exception:
        return f"id={uid}"


async def audit_dialog(client, dialog, me_id):
    entity = dialog.entity
    how = "unknown"
    when = None
    by_whom = None

    try:
        async for msg in client.iter_messages(entity, reverse=True, limit=100):
            action = getattr(msg, "action", None)
            if action is None:
                continue

            if isinstance(action, MessageActionChatCreate):
                if msg.sender_id == me_id:
                    how = "created by you"
                    when = msg.date
                    break
            elif isinstance(action, MessageActionChatAddUser):
                if me_id in (action.users or []):
                    how = "added by user"
                    by_whom = msg.sender_id
                    when = msg.date
                    break
            elif isinstance(action, MessageActionChatJoinedByLink):
                if msg.sender_id == me_id:
                    how = "joined via invite link"
                    by_whom = getattr(action, "inviter_id", None)
                    when = msg.date
                    break
            elif isinstance(action, MessageActionChatJoinedByRequest):
                if msg.sender_id == me_id:
                    how = "join request approved"
                    when = msg.date
                    break
    except Exception as e:
        how = f"error: {type(e).__name__}"

    return {
        "title": dialog.title or f"Untitled {dialog.id}",
        "id": dialog.id,
        "how": how,
        "when": when,
        "by_whom": by_whom,
    }


async def main():
    async with TelegramClient(SESSION_NAME, API_ID, API_HASH) as client:
        me = await client.get_me()
        me_id = me.id
        print(f"👤 Auditing as: {me.first_name} (id={me_id})\n")

        dialogs = await client.get_dialogs()
        groups = [d for d in dialogs if d.is_channel or d.is_group]
        print(f"🔍 Scanning {len(groups)} groups/channels...\n")

        results = []
        for i, d in enumerate(groups, 1):
            print(f"  [{i}/{len(groups)}] {d.title or d.id}…", end="\r")
            results.append(await audit_dialog(client, d, me_id))
        print(" " * 60, end="\r")

        print(f"{'Group':40} {'How you joined':26} {'When':17} {'By'}")
        print("─" * 120)
        for r in results:
            when_str = r["when"].strftime("%Y-%m-%d %H:%M") if r["when"] else "?"
            by_str = await resolve_user(client, r["by_whom"]) if r["by_whom"] else ""
            print(f"{(r['title'])[:40]:40} {r['how'][:26]:26} {when_str:17} {by_str}")

        unknown = [r for r in results if r["how"] == "unknown"]
        if unknown:
            print(f"\n⚠ {len(unknown)} group(s) had no join service message in visible history.")
            print("  (Common for old joins — Telegram purges service messages after time.)")


if __name__ == "__main__":
    asyncio.run(main())