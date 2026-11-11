"""Audit: for each group/channel you're in, who added/invited you.

Groups the result by inviter so you can see patterns:
  "Maria L. added me to 4 groups"      ← normal
  "id=999999 added me to 7 groups"     ← suspicious if you don't know them

No invite links extracted. No message content read.
"""
import asyncio
from collections import defaultdict
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
        return "(unknown)"
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


async def find_join_event(client, entity, me_id):
    """Returns (how, when, inviter_id) for the earliest join event."""
    try:
        async for msg in client.iter_messages(entity, reverse=True, limit=100):
            action = getattr(msg, "action", None)
            if action is None:
                continue

            if isinstance(action, MessageActionChatCreate):
                if msg.sender_id == me_id:
                    return ("created by you", msg.date, None)

            elif isinstance(action, MessageActionChatAddUser):
                if me_id in (action.users or []):
                    return ("added by user", msg.date, msg.sender_id)

            elif isinstance(action, MessageActionChatJoinedByLink):
                if msg.sender_id == me_id:
                    return ("joined via invite link", msg.date,
                            getattr(action, "inviter_id", None))

            elif isinstance(action, MessageActionChatJoinedByRequest):
                if msg.sender_id == me_id:
                    return ("join request approved", msg.date, None)
    except Exception as e:
        return (f"error: {type(e).__name__}", None, None)

    return ("unknown (old join)", None, None)


async def main():
    async with TelegramClient(SESSION_NAME, API_ID, API_HASH) as client:
        me = await client.get_me()
        print(f"👤 Auditing as: {me.first_name} (id={me.id})\n")

        dialogs = await client.get_dialogs()
        groups = [d for d in dialogs if d.is_channel or d.is_group]
        print(f"🔍 Checking {len(groups)} groups/channels...\n")

        # Collect
        records = []
        for i, d in enumerate(groups, 1):
            print(f"  [{i}/{len(groups)}] {(d.title or str(d.id))[:50]}…", end="\r")
            how, when, inviter = await find_join_event(client, d.entity, me.id)
            records.append({
                "title": d.title or f"Untitled {d.id}",
                "id": d.id,
                "private": not bool(getattr(d.entity, "username", None)),
                "how": how,
                "when": when,
                "inviter_id": inviter,
            })
        print(" " * 70, end="\r")

        # Resolve names
        for r in records:
            r["inviter_name"] = (await resolve_user(client, r["inviter_id"])
                                 if r["inviter_id"] else None)

        # ─── Per-inviter summary ────────────────────────────────
        by_inviter = defaultdict(list)
        for r in records:
            if r["how"] == "added by user" and r["inviter_id"]:
                by_inviter[r["inviter_name"]].append(r)

        if by_inviter:
            print(f"\n{'═' * 78}")
            print(f"  WHO ADDED YOU TO GROUPS")
            print(f"{'═' * 78}\n")
            for name, items in sorted(by_inviter.items(),
                                      key=lambda kv: -len(kv[1])):
                print(f"  {name}  →  {len(items)} group{'s' if len(items) > 1 else ''}")
                for it in items:
                    when = it["when"].strftime("%Y-%m-%d") if it["when"] else "?"
                    priv = "🔒" if it["private"] else "🌐"
                    print(f"     {priv} {when}  {it['title']}")
                print()

        # ─── Link-joins with known inviter ─────────────────────
        link_joins = [r for r in records
                      if r["how"] == "joined via invite link" and r["inviter_id"]]
        if link_joins:
            print(f"\n{'═' * 78}")
            print(f"  JOINED VIA LINK — link was created by:")
            print(f"{'═' * 78}\n")
            for r in link_joins:
                when = r["when"].strftime("%Y-%m-%d") if r["when"] else "?"
                priv = "🔒" if r["private"] else "🌐"
                print(f"  {priv} {when}  {r['title']}")
                print(f"     link created by: {r['inviter_name']}")
                print()

        # ─── Unknown origin (worth reviewing) ──────────────────
        unknown = [r for r in records if r["how"] in
                   ("unknown (old join)", "join request approved")]
        if unknown:
            print(f"\n{'═' * 78}")
            print(f"  NO VISIBLE INVITER — old join, service message purged")
            print(f"{'═' * 78}\n")
            for r in unknown:
                priv = "🔒" if r["private"] else "🌐"
                print(f"  {priv}  {r['title']}  (id={r['id']})")

        # ─── Suspicious: inviters with many adds, especially unknown ─
        print(f"\n{'═' * 78}")
        print(f"  🚨 RED FLAGS TO REVIEW")
        print(f"{'═' * 78}\n")
        flags = 0

        for name, items in by_inviter.items():
            if len(items) >= 3:
                print(f"  ⚠ {name} added you to {len(items)} groups — verify you know them")
                flags += 1

        for r in records:
            if r["how"] == "added by user" and r["inviter_name"] == "(unknown)":
                print(f"  ⚠ Added to '{r['title']}' by an account that no longer exists")
                flags += 1

        if not flags:
            print("  ✅ No obvious red flags found.")


if __name__ == "__main__":
    asyncio.run(main())