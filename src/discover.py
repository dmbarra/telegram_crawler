"""Discover page: find public groups, collect private invite links, join safely.

Public groups come from Telegram's own search. Private groups can't be searched,
so invite links (t.me/+… / t.me/joinchat/…) are collected from messages in groups
you choose, or pasted by you. Every join goes through JoinGuard (pause + daily cap).

All Telegram calls run on the TelegramService loop thread; public methods are
called from web threads and raise DiscoverError with a message for the user.
"""
import asyncio
import json
import os
import re
import threading
import time

from telethon import errors
from telethon.tl.functions.channels import (
    GetChannelRecommendationsRequest, GetFullChannelRequest, JoinChannelRequest,
)
from telethon.tl.functions.contacts import SearchRequest
from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest
from telethon.tl.types import (
    ChatInviteAlready, ChatInvitePeek, InputMessagesFilterPhotos, InputMessagesFilterVideo,
    MessageEntityTextUrl,
)

from src.config import INVITES_PATH, JOIN_DAILY_LIMIT, JOIN_LOG_PATH, JOIN_MIN_INTERVAL
from src.runner import normalize_chat

INVITE_RE = re.compile(
    r"(?:https?://)?(?:t\.me|telegram\.me|telegram\.dog)/(?:\+|joinchat/)([A-Za-z0-9_-]{8,64})",
    re.IGNORECASE,
)
INVITE_HASH_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
CHECK_MIN_INTERVAL = 3      # seconds between two invite previews


class DiscoverError(Exception):
    """A problem the user should see as-is."""


def find_invite_hashes(text: str) -> list:
    return list(dict.fromkeys(INVITE_RE.findall(text or "")))


def fmt_wait(seconds: float) -> str:
    seconds = max(1, int(seconds))
    if seconds < 120:
        return f"{seconds} s"
    minutes = seconds // 60
    if minutes < 120:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60} min"


def _load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save_json(path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


# ─── join safety limits (saved, so restarts don't reset them) ──────
class JoinGuard:
    def __init__(self, path=JOIN_LOG_PATH, daily=JOIN_DAILY_LIMIT, interval=JOIN_MIN_INTERVAL):
        self.path, self.daily, self.interval = path, daily, interval
        data = _load_json(path, {})
        self.joins = data.get("joins", [])
        self.blocked_until = data.get("blocked_until", 0)
        self.last_attempt = data.get("last_attempt", 0)

    def _recent(self, now):
        return [j for j in self.joins if now - j["t"] < 86400]

    def wait_seconds(self, now=None) -> float:
        now = now or time.time()
        waits = [self.blocked_until - now, self.last_attempt + self.interval - now]
        recent = self._recent(now)
        if len(recent) >= self.daily:
            waits.append(min(j["t"] for j in recent) + 86400 - now)
        return max(0, *waits)

    def check(self, now=None):
        """None if a join is allowed now, else the reason."""
        now = now or time.time()
        if self.blocked_until > now:
            return f"Telegram asked to wait. Try again in {fmt_wait(self.blocked_until - now)}."
        recent = self._recent(now)
        if len(recent) >= self.daily:
            nxt = min(j["t"] for j in recent) + 86400
            return f"Daily limit reached ({self.daily}). Next join in {fmt_wait(nxt - now)}."
        wait = self.last_attempt + self.interval - now
        if wait > 0:
            return f"Safety pause between joins: wait {fmt_wait(wait)}."
        return None

    def attempted(self, title: str, result: str) -> None:
        now = time.time()
        self.last_attempt = now
        if result in ("joined", "requested"):
            self.joins.append({"t": now, "title": title, "result": result})
        self._save()

    def block(self, seconds: int) -> None:
        self.blocked_until = time.time() + seconds
        self._save()

    def _save(self):
        self.joins = self._recent(time.time())
        _save_json(self.path, {"joins": self.joins, "blocked_until": self.blocked_until,
                               "last_attempt": self.last_attempt})

    def snapshot(self) -> dict:
        now = time.time()
        return {"joins_today": len(self._recent(now)), "limit": self.daily,
                "interval": self.interval, "wait": self.wait_seconds(now),
                "reason": self.check(now)}


# ─── invite links found so far (saved) ─────────────────────────────
class InviteStore:
    def __init__(self, path=INVITES_PATH):
        self.path = path
        self._lock = threading.Lock()
        self._items = _load_json(path, {})

    def found(self, h: str, source: str, context: str, when: float) -> bool:
        """Record a sighting. Returns True if the link is new."""
        with self._lock:
            item = self._items.get(h)
            new = item is None
            if new:
                item = self._items[h] = {
                    "hash": h, "link": f"https://t.me/+{h}", "sources": [],
                    "context": (context or "").strip()[:300], "first_seen": when,
                    "status": "new", "ignored": False, "preview": None, "checked_at": None,
                }
            if source and source not in item["sources"]:
                item["sources"] = item["sources"] + [source]
            return new

    def get(self, h: str):
        with self._lock:
            item = self._items.get(h)
            return dict(item) if item else None

    def update(self, h: str, **fields) -> None:
        with self._lock:
            if h in self._items:
                self._items[h] = {**self._items[h], **fields}
        self.save()

    def list(self) -> list:
        with self._lock:
            items = [dict(i) for i in self._items.values()]
        return sorted(items, key=lambda i: i["first_seen"] or 0, reverse=True)

    def save(self) -> None:
        with self._lock:
            data = dict(self._items)
        _save_json(self.path, data)


# ─── the Discover feature ──────────────────────────────────────────
class Discovery:
    def __init__(self, service):
        self.svc = service
        self.guard = JoinGuard()
        self.invites = InviteStore()
        self._entities = {}
        self._join_lock = threading.Lock()
        self._check_lock = threading.Lock()
        self._last_check = 0.0
        self._check_blocked_until = 0.0
        self._scan_job = None
        self.scan = self._scan_state(running=False)

    # ─── helpers ────────────────────────────────────────────────
    @staticmethod
    def _scan_state(**kw):
        return {"running": False, "stopped": False, "done": 0, "total": 0, "seen": 0,
                "new": 0, "current": "", "errors": [], "finished_at": None, **kw}

    def _run(self, coro, timeout=90):
        if self.svc.status != "ready":
            coro.close()
            raise DiscoverError("Telegram is not connected")
        return self.svc._submit(coro).result(timeout=timeout)

    @property
    def _client(self):
        return self.svc._client

    def _entity(self, chat_id: int):
        if chat_id in self._entities:
            return self._entities[chat_id]
        for c in self.svc.channels:
            if c["id"] == chat_id:
                return c["entity"]
        raise DiscoverError("Unknown group — search again first.")

    def _result(self, chat) -> dict:
        known = {c["id"]: c for c in self.svc.channels}.get(chat.id)
        return {
            "id": chat.id,
            "title": getattr(chat, "title", None) or f"#{chat.id}",
            "username": getattr(chat, "username", None),
            "kind": "channel" if getattr(chat, "broadcast", False) else "group",
            "members": getattr(chat, "participants_count", None),
            "is_member": not getattr(chat, "left", True) or bool(known and known["member"]),
            "in_list": known is not None,
            "needs_approval": bool(getattr(chat, "join_request", False)),
            "scam": bool(getattr(chat, "scam", False) or getattr(chat, "fake", False)),
        }

    def _results(self, chats) -> list:
        chats = [c for c in chats if hasattr(c, "title")]
        for c in chats:
            self._entities[c.id] = c
        return [self._result(c) for c in chats]

    def _add_joined(self, chat) -> None:
        # Runs on the loop thread; new list so web threads never see a half-updated one.
        if not any(c["id"] == chat.id for c in self.svc.channels):
            self.svc.channels = self.svc.channels + [normalize_chat(chat)]

    # ─── public search ──────────────────────────────────────────
    def search(self, q: str) -> list:
        async def go():
            return (await self._client(SearchRequest(q=q, limit=50))).chats
        return self._results(self._run(go()))

    def similar(self, chat_id: int) -> list:
        entity = self._entity(chat_id)

        async def go():
            try:
                return (await self._client(GetChannelRecommendationsRequest(channel=entity))).chats
            except (TypeError, ValueError):
                raise DiscoverError("Suggestions only work for channels and big groups.")
        return self._results(self._run(go()))

    def details(self, chat_id: int) -> dict:
        entity = self._entity(chat_id)

        async def go():
            try:
                full = (await self._client(GetFullChannelRequest(entity))).full_chat
                last = await self._client.get_messages(entity, limit=1)
                photos = await self._client.get_messages(entity, limit=0, filter=InputMessagesFilterPhotos())
                videos = await self._client.get_messages(entity, limit=0, filter=InputMessagesFilterVideo())
            except errors.ChannelPrivateError:
                raise DiscoverError("This group is private — details need membership.")
            return {
                "about": full.about or "",
                "members": full.participants_count,
                "last_post": last[0].date.timestamp() if last else None,
                "photos": photos.total,
                "videos": videos.total,
            }
        return self._run(go())

    def add_public(self, chat_id: int) -> dict:
        entity = self._entity(chat_id)
        username = getattr(entity, "username", None)
        if not username:
            raise DiscoverError("This group has no public @username — join it instead.")
        cid, note = self.svc.add_channel("@" + username)
        if cid is None:
            raise DiscoverError(note)
        return {"id": cid, "note": note}

    # ─── joining ────────────────────────────────────────────────
    def _join(self, request, title: str) -> str:
        if not self._join_lock.acquire(blocking=False):
            raise DiscoverError("Another join is in progress.")
        try:
            reason = self.guard.check()
            if reason:
                raise DiscoverError(reason)

            async def go():
                try:
                    upd = await self._client(request)
                except errors.UserAlreadyParticipantError:
                    return "member"
                except errors.InviteRequestSentError:
                    return "requested"
                except (errors.InviteHashExpiredError, errors.InviteHashInvalidError):
                    return "expired"
                except errors.ChannelsTooMuchError:
                    raise DiscoverError("Telegram limit: you are in too many channels and groups "
                                        "(about 500). Leave some first.")
                except errors.FloodWaitError as e:
                    self.guard.block(e.seconds)
                    raise DiscoverError(f"Telegram asked to wait {fmt_wait(e.seconds)} before joining again.")
                for chat in getattr(upd, "chats", []):
                    if hasattr(chat, "title"):
                        self._add_joined(chat)
                        break
                return "joined"

            status = self._run(go())
            self.guard.attempted(title, status)
            return status
        finally:
            self._join_lock.release()

    def join_public(self, chat_id: int) -> dict:
        entity = self._entity(chat_id)
        status = self._join(JoinChannelRequest(entity), getattr(entity, "title", str(chat_id)))
        return {"status": status}

    # ─── invite links ───────────────────────────────────────────
    def add_links(self, text: str) -> dict:
        hashes = find_invite_hashes(text)
        if not hashes and INVITE_HASH_RE.match(text.strip()):
            hashes = [text.strip()]
        if not hashes:
            raise DiscoverError("No invite link found. It looks like t.me/+AbCd… or t.me/joinchat/AbCd…")
        new = sum(self.invites.found(h, "pasted by you", "", time.time()) for h in hashes)
        self.invites.save()
        return {"hashes": hashes, "new": new}

    def preview_invite(self, h: str) -> dict:
        if not self.invites.get(h):
            raise DiscoverError("Unknown invite link.")
        with self._check_lock:
            now = time.time()
            if self._check_blocked_until > now:
                raise DiscoverError(f"Telegram asked to wait {fmt_wait(self._check_blocked_until - now)} "
                                    "before more previews.")
            if now - self._last_check < CHECK_MIN_INTERVAL:
                raise DiscoverError("Wait a few seconds between previews.")
            self._last_check = now

        async def go():
            try:
                r = await self._client(CheckChatInviteRequest(hash=h))
            except (errors.InviteHashExpiredError, errors.InviteHashInvalidError):
                return "expired", None
            except errors.FloodWaitError as e:
                self._check_blocked_until = time.time() + e.seconds
                raise DiscoverError(f"Telegram asked to wait {fmt_wait(e.seconds)} before more previews.")
            if isinstance(r, (ChatInviteAlready, ChatInvitePeek)):
                chat = r.chat
                preview = {
                    "title": chat.title, "members": getattr(chat, "participants_count", None),
                    "kind": "channel" if getattr(chat, "broadcast", False) else "group",
                    "about": "", "needs_approval": False,
                    "scam": bool(getattr(chat, "scam", False) or getattr(chat, "fake", False)),
                }
                if isinstance(r, ChatInviteAlready):
                    self._add_joined(chat)
                    return "member", preview
                return None, preview
            return None, {
                "title": r.title, "members": r.participants_count,
                "kind": "channel" if r.broadcast else "group", "about": r.about or "",
                "needs_approval": bool(r.request_needed), "scam": bool(r.scam or r.fake),
            }

        status, preview = self._run(go())
        fields = {"checked_at": time.time()}
        if preview:
            fields["preview"] = preview
        if status:
            fields["status"] = status
        self.invites.update(h, **fields)
        return self.invites.get(h)

    def join_invite(self, h: str) -> dict:
        item = self.invites.get(h)
        if not item:
            raise DiscoverError("Unknown invite link.")
        title = (item["preview"] or {}).get("title") or item["link"]
        status = self._join(ImportChatInviteRequest(hash=h), title)
        self.invites.update(h, status=status)
        return self.invites.get(h)

    def set_ignored(self, h: str, ignored: bool) -> dict:
        if not self.invites.get(h):
            raise DiscoverError("Unknown invite link.")
        self.invites.update(h, ignored=ignored)
        return self.invites.get(h)

    # ─── scan your groups for invite links ──────────────────────
    def start_scan(self, channel_ids, limit: int, keywords) -> None:
        if self.scan["running"]:
            raise DiscoverError("A scan is already running.")
        if self.svc.status != "ready":
            raise DiscoverError("Telegram is not connected")
        if not 1 <= limit <= 5000:
            raise DiscoverError("Messages per group must be 1 to 5000.")
        wanted = set(channel_ids)
        chats = [c for c in self.svc.channels if c["id"] in wanted]
        if not chats:
            raise DiscoverError("Choose at least one group to scan.")
        kws = [k.strip().lower() for k in keywords if k.strip()]
        self.scan = self._scan_state(running=True, total=len(chats))
        self._scan_job = self.svc._submit(self._scan(chats, limit, kws))

    def stop_scan(self) -> None:
        if self.scan["running"] and self._scan_job:
            self._scan_job.cancel()

    async def _scan(self, chats, limit, kws):
        s = self.scan
        try:
            for ch in chats:
                s["current"] = ch["title"]
                try:
                    async for m in self._client.iter_messages(ch["entity"], limit=limit):
                        text = m.message or ""
                        urls = [e.url for e in (m.entities or []) if isinstance(e, MessageEntityTextUrl)]
                        for row in getattr(getattr(m, "reply_markup", None), "rows", None) or []:
                            urls += [b.url for b in row.buttons if getattr(b, "url", None)]
                        hashes = find_invite_hashes(" ".join([text, *urls]))
                        if not hashes or (kws and not any(k in text.lower() for k in kws)):
                            continue
                        for h in hashes:
                            s["seen"] += 1
                            if self.invites.found(h, ch["title"], text, m.date.timestamp()):
                                s["new"] += 1
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    s["errors"].append(f"{ch['title']}: {e}")
                s["done"] += 1
        except asyncio.CancelledError:
            s["stopped"] = True
        finally:
            s["running"] = False
            s["current"] = ""
            s["finished_at"] = time.time()
            self.invites.save()

    # ─── for the page ───────────────────────────────────────────
    def snapshot(self) -> dict:
        return {
            "scan": {**self.scan, "errors": list(self.scan["errors"])},
            "joins": self.guard.snapshot(),
            "invites": self.invites.list(),
        }
