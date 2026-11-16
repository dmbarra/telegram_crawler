"""One-time move from the old layout to library.db + <channel_id>/<type>/ folders.

Old layout: DATA_DIR/download_state.json, file_hashes.json, channels.txt and
DOWNLOADS_DIR/<channel title>/<filter>/<caption>.ext.

Every file in an old folder ends up in the DB and in the new layout:
  - matched to its Telegram message (old record, or old name + size) → <type>/<date>_<msg_id>.ext
  - not matched → unmatched/<sha256[:16]>.ext
  - exact copy of a file already in the new layout → old copy removed

Remove this file, viewer/migrate_routes.py and viewer/templates/migrate.html
(plus the blueprint line, the home card and the "old folders" notices) once every folder is moved.
"""
import json
import mimetypes
import re
import shutil
import time
from pathlib import Path

from telethon.tl.types import InputMessagesFilterDocument, InputMessagesFilterPhotoVideo

from src.config import DATA_DIR, DB_PATH, DOWNLOADS_DIR
from src.dedup import hash_file
from src.utils import (
    folder_for_mime, get_media_key, guess_extension, media_folder, media_rel_path, safe_filename,
)

STATE_PATH = DATA_DIR / "download_state.json"
HASHES_PATH = DATA_DIR / "file_hashes.json"
CHANNELS_TXT = DATA_DIR / "channels.txt"
JUNK = {".DS_Store", "Thumbs.db", "desktop.ini"}

_MSG_CACHE_SECONDS = 15 * 60
_msg_cache = {}


class MigrationError(Exception):
    pass


def _guard(svc):
    if svc.status != "ready":
        raise MigrationError("Telegram is not connected. Wait a moment and reload the page.")
    if svc.progress.running:
        raise MigrationError("A download is running. Wait until it finishes.")


def _old_rel(path_str: str):
    """Old state stored absolute paths; the library stores them relative to DOWNLOADS_DIR."""
    p = Path(path_str)
    if not p.is_absolute():
        return p.as_posix()
    try:
        return p.relative_to(DOWNLOADS_DIR).as_posix()
    except ValueError:
        return None


# ─── step 1: create the database ────────────────────────────────
def create_database(svc) -> dict:
    """Returns {"ok", "steps": [{"level", "text"}]} — every step is reported, errors included."""
    steps = []

    def step(text, level="ok"):
        steps.append({"level": level, "text": text})
        svc.progress.log(f"Create database: {text}", level)

    _guard(svc)
    with svc._lock:
        found = [p for p in (STATE_PATH, HASHES_PATH, CHANNELS_TXT) if p.exists()]
        if found:
            backup = DATA_DIR / f"backup-{time.strftime('%Y%m%d-%H%M%S')}"
            backup.mkdir(parents=True, exist_ok=True)
            for p in found:
                shutil.copy2(p, backup / p.name)
            step(f"Backup of {', '.join(p.name for p in found)} saved in {backup}")
        else:
            step("No old state files found. Files will be matched to Telegram by name and size.", "info")

        hashes = {}
        if HASHES_PATH.exists():
            try:
                hashes = json.loads(HASHES_PATH.read_text(encoding="utf-8"))
                step(f"Read {len(hashes)} fingerprints from {HASHES_PATH.name}")
            except Exception as e:
                step(f"Could not read {HASHES_PATH.name}: {e}. Fingerprints will be computed again.", "warn")

        if STATE_PATH.exists():
            try:
                state = json.loads(STATE_PATH.read_text(encoding="utf-8")).get("downloaded", {})
            except Exception as e:
                state = {}
                step(f"Could not read {STATE_PATH.name}: {e}. Files will be matched by name and size.",
                     "error")
            added = known = 0
            outside = []
            for key, e in state.items():
                rel = _old_rel(e.get("path") or "")
                if not rel:
                    outside.append(e.get("path") or "(empty)")
                    continue
                sha = (hashes.get(rel) or [None, None, None])[2]
                if svc.library.import_legacy(key, e.get("message_id"), rel, None, e.get("size"),
                                             sha, bool(e.get("deleted"))):
                    added += 1
                else:
                    known += 1
            step(f"Imported {added} download records ({known} were already in the database)")
            if outside:
                step(f"{len(outside)} records point outside {DOWNLOADS_DIR} and were skipped. "
                     f"Example: {outside[0]}. Those files will be matched by name and size.", "warn")

    if CHANNELS_TXT.exists():
        idents = []
        for line in CHANNELS_TXT.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and line not in idents:
                idents.append(line)
        saved = svc.library.extra_idents()
        added = 0
        for ident in idents:
            if ident in saved:
                continue
            try:
                cid, err = svc.add_channel(ident)
            except Exception as e:
                cid, err = None, str(e)
            if cid is None:
                step(f"Channel {ident} not added: {err}", "error")
            elif not err:
                added += 1
        step(f"Added {added} channels from {CHANNELS_TXT.name}")

    svc.library.set_created()
    step(f"Database ready: {DB_PATH}")
    return {"ok": not any(s["level"] == "error" for s in steps), "steps": steps}


# ─── step 2: old folders ────────────────────────────────────────
def old_folder_names(library) -> list:
    """Top folders in DOWNLOADS_DIR that are not a channel id folder."""
    if not DOWNLOADS_DIR.is_dir():
        return []
    channel_ids = set(library.channel_titles())
    return sorted(d.name for d in DOWNLOADS_DIR.iterdir()
                  if d.is_dir() and not d.name.startswith(".") and d.name not in channel_ids)


def _files(folder: Path) -> list:
    return sorted(f for f in folder.rglob("*") if f.is_file() and f.name not in JUNK)


def old_folders(svc) -> list:
    out = []
    for name in old_folder_names(svc.library):
        files = _files(DOWNLOADS_DIR / name)
        match = next((c["id"] for c in svc.channels
                      if safe_filename(c["title"] or f"channel_{c['id']}") == name), None)
        out.append({"folder": name, "files": len(files),
                    "size": sum(f.stat().st_size for f in files), "channel_id": match})
    return out


# ─── step 3: plan one folder ────────────────────────────────────
async def _get_by_ids(client, entity, ids) -> dict:
    out = {}
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        for mid, m in zip(chunk, await client.get_messages(entity, ids=chunk)):
            if m:
                out[mid] = m
    return out


async def _all_media(client, entity) -> list:
    seen, out = set(), []
    for flt in (InputMessagesFilterPhotoVideo(), InputMessagesFilterDocument()):
        async for m in client.iter_messages(entity, filter=flt):
            if m.id not in seen:
                seen.add(m.id)
                out.append(m)
    return out


def _channel_media(svc, channel) -> list:
    hit = _msg_cache.get(channel["id"])
    if hit and time.time() - hit[0] < _MSG_CACHE_SECONDS:
        return hit[1]
    msgs = svc._submit(_all_media(svc._client, channel["entity"])).result(timeout=900)
    _msg_cache[channel["id"]] = (time.time(), msgs)
    return msgs


def _old_name(m) -> str:
    """The name the old downloader gave this message's file (before any _1, _2 suffix)."""
    ext = guess_extension(m)
    return safe_filename(m.text.strip(), ext) if m.text else f"{m.id}{ext}"


def _match_by_name(f: Path, by_name: dict, used: set):
    size = f.stat().st_size
    exact = [m for m in by_name.get(f.name, []) if m.id not in used]
    cands = exact
    if not cands:
        hit = re.fullmatch(r"(.*)_\d+", f.stem)
        if hit:
            prefix = hit.group(1)
            cands = [m for name, ms in by_name.items()
                     if Path(name).suffix == f.suffix and Path(name).stem.startswith(prefix)
                     for m in ms if m.id not in used]
    sized = [m for m in cands if m.file and m.file.size == size]
    if sized:
        return min(sized, key=lambda m: m.id)
    # Photo sizes reported by Telegram can differ a little from the saved file.
    if len(exact) == 1 and exact[0].photo:
        return exact[0]
    return None


def _plan(svc, folder: str, channel_id: int):
    if not svc.library.is_created():
        raise MigrationError("Create the database first")
    root = DOWNLOADS_DIR / folder
    if "/" in folder or "\\" in folder or folder.startswith(".") or not root.is_dir():
        raise MigrationError(f"Folder not found: {folder}")
    if folder in svc.library.channel_titles():
        raise MigrationError(f"{folder} is already a new-layout folder")
    channel = next((c for c in svc.channels if c["id"] == channel_id), None)
    if not channel:
        raise MigrationError("Pick the channel this folder belongs to")

    lib = svc.library
    legacy = {}
    for r in lib.legacy_files():
        if r["status"] == "downloaded":
            legacy.setdefault(r["path"], []).append(r)

    files = _files(root)
    ids = sorted({r["message_id"] for f in files
                  for r in legacy.get(f.relative_to(DOWNLOADS_DIR).as_posix(), []) if r["message_id"]})
    by_id = svc._submit(_get_by_ids(svc._client, channel["entity"], ids)).result(
        timeout=60 + len(ids) // 10) if ids else {}

    by_name = None
    used, taken, actions = set(), {}, []
    for f in files:
        rel = f.relative_to(DOWNLOADS_DIR).as_posix()
        msg, how = None, ""
        for r in legacy.get(rel, []):
            m = by_id.get(r["message_id"])
            if m and m.id not in used and get_media_key(m) == r["media_key"]:
                msg, how = m, "matched by old record"
                break
        if not msg:
            if by_name is None:
                by_name = {}
                for m in _channel_media(svc, channel):
                    by_name.setdefault(_old_name(m), []).append(m)
            msg = _match_by_name(f, by_name, used)
            how = "matched by name and size" if msg else ""

        a = {"old": rel, "new": None, "result": "", "note": how, "_msg": msg, "_sha": None}
        if msg:
            used.add(msg.id)
            new = media_rel_path(channel_id, msg, f.suffix)
            row = lib.get_file(get_media_key(msg))
            if row and row["channel_id"] is not None and row["path"] != rel \
                    and (DOWNLOADS_DIR / row["path"]).is_file():
                a["_sha"] = hash_file(f)
                kept_sha = row["sha256"] or hash_file(DOWNLOADS_DIR / row["path"])
                if a["_sha"] == kept_sha:
                    a.update(new=row["path"], result="copy",
                             note="already downloaded in the new layout — old copy removed")
                    actions.append(a)
                    continue
                msg = None
                a["note"] = "message already has a different file in the new layout"
            elif new in taken or (DOWNLOADS_DIR / new).exists():
                msg = None
                a["note"] = f"new name {new} is already used"
            else:
                a.update(new=new, result="rename")
                taken[new] = None
                actions.append(a)
                continue

        a["_msg"] = None
        a["_sha"] = a["_sha"] or hash_file(f)
        new = f"{channel_id}/unmatched/{a['_sha'][:16]}{f.suffix}"
        a["note"] = a["note"] or "no matching Telegram message"
        if new in taken or (DOWNLOADS_DIR / new).is_file():
            a.update(new=new, result="copy", note="same content as another file — old copy removed")
        else:
            a.update(new=new, result="unmatched")
            taken[new] = None
        actions.append(a)
    return channel, actions


def _public(actions) -> list:
    return [{k: v for k, v in a.items() if not k.startswith("_")} for a in actions]


def _summary(actions) -> dict:
    count = lambda r: sum(1 for a in actions if a["result"] == r)  # noqa: E731
    return {"renamed": count("rename"), "unmatched": count("unmatched"),
            "copies": count("copy"), "errors": count("error"), "total": len(actions)}


def preview(svc, folder: str, channel_id: int) -> dict:
    _guard(svc)
    with svc._lock:
        _, actions = _plan(svc, folder, channel_id)
    return {"summary": _summary(actions), "actions": _public(actions)}


# ─── step 4: run + verify one folder ────────────────────────────
def _apply(lib, channel_id, a) -> None:
    src, dst = DOWNLOADS_DIR / a["old"], DOWNLOADS_DIR / a["new"]
    if a["result"] == "copy":
        src.unlink()
        lib.repoint_legacy(a["old"], a["new"], channel_id)
        return
    sha = a["_sha"] or hash_file(src)
    size = src.stat().st_size
    if dst.exists():
        raise OSError(f"{a['new']} already exists")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(src, dst)
    a["_sha"], a["_size"] = sha, size
    msg = a["_msg"]
    if msg:
        a["_key"] = get_media_key(msg)
        lib.save_file(a["_key"], channel_id, msg.id, a["new"], media_folder(msg),
                      msg.file.mime_type if msg.file else None, size, sha,
                      msg.message or None, msg.date.isoformat() if msg.date else None)
    else:
        mime = mimetypes.guess_type(dst.name)[0] or ""
        a["_key"] = f"file:{sha}"
        lib.save_file(a["_key"], channel_id, None, a["new"], folder_for_mime(mime),
                      mime or None, size, sha, None, None)
    lib.repoint_legacy(a["old"], a["new"], channel_id)


def _verify(lib, channel_id, a) -> str:
    """Empty string when the file is where the DB says, else what is wrong."""
    if a["result"] == "error":
        return a["note"]
    if (DOWNLOADS_DIR / a["old"]).exists():
        return "old file still there"
    dst = DOWNLOADS_DIR / a["new"]
    if not dst.is_file():
        return "new file missing"
    if a["result"] == "copy":
        return ""
    if dst.stat().st_size != a["_size"]:
        return "size changed during the move"
    row = lib.get_file(a["_key"])
    if not row or row["path"] != a["new"] or row["channel_id"] != channel_id \
            or row["status"] != "downloaded":
        return "database record does not point at the new file"
    return ""


def run(svc, folder: str, channel_id: int) -> dict:
    _guard(svc)
    lib = svc.library
    with svc._lock:
        channel, actions = _plan(svc, folder, channel_id)
        lib.save_channel(channel["id"], channel["title"], channel["username"])
        for a in actions:
            try:
                _apply(lib, channel_id, a)
            except Exception as e:
                a.update(result="error", note=f"{a['note']} — failed: {e}")

        root = DOWNLOADS_DIR / folder
        lib.claim_deleted(folder, channel_id)
        stale = [r["media_key"] for r in lib.legacy_files()
                 if r["status"] == "downloaded" and r["path"].startswith(folder + "/")
                 and not (DOWNLOADS_DIR / r["path"]).exists()]
        lib.forget(stale)
        for junk in root.rglob("*"):
            if junk.is_file() and junk.name in JUNK:
                junk.unlink()
        for p in sorted(root.rglob("*"), reverse=True):
            if p.is_dir():
                try:
                    p.rmdir()
                except OSError:
                    pass
        try:
            root.rmdir()
        except OSError:
            pass

        problems = []
        for a in actions:
            a["check"] = _verify(lib, channel_id, a)
            if a["check"]:
                problems.append(a)
        folder_gone = not root.exists()
        summary = _summary(actions)
        summary.update(checked=len(actions), check_failed=len(problems), folder_removed=folder_gone,
                       forgotten=len(stale))
        verified = not problems and folder_gone
        report = {"folder": folder, "channel_id": channel_id, "channel": channel["title"],
                  "verified": verified, "summary": summary, "actions": _public(actions)}
        lib.save_migration(folder, channel_id, summary["renamed"], summary["unmatched"],
                           summary["copies"], summary["errors"], verified, json.dumps(report))

    svc.progress.log(
        f"Migration {folder} → {channel_id}: {summary['renamed']} renamed, "
        f"{summary['unmatched']} unmatched, {summary['copies']} copies removed, "
        f"{summary['errors']} errors — {'verified OK' if verified else 'CHECK FAILED'}",
        "ok" if verified else "error")
    return report
