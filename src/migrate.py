"""One-time move from the old layout to library.db + <channel_id>/<type>/ folders.

Old layout: DATA_DIR/download_state.json, file_hashes.json, channels.txt and
DOWNLOADS_DIR/<channel title>/<filter>/<caption>.ext.

Remove this file, viewer/migrate_routes.py and viewer/templates/migrate.html
(plus the blueprint line and the home card) once every folder is moved.
"""
import json
import shutil
import time
from pathlib import Path

from src.config import DATA_DIR, DOWNLOADS_DIR
from src.dedup import hash_file
from src.utils import get_media_key, media_folder, media_rel_path, safe_filename

STATE_PATH = DATA_DIR / "download_state.json"
HASHES_PATH = DATA_DIR / "file_hashes.json"
CHANNELS_TXT = DATA_DIR / "channels.txt"


class MigrationError(Exception):
    pass


def _guard(svc):
    if svc.status != "ready":
        raise MigrationError("Telegram is not connected")
    if svc.progress.running:
        raise MigrationError("Wait until the download finishes")


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _old_rel(path_str: str):
    """Old state stored absolute paths; the library stores them relative to DOWNLOADS_DIR."""
    p = Path(path_str)
    if not p.is_absolute():
        return p.as_posix()
    try:
        return p.relative_to(DOWNLOADS_DIR).as_posix()
    except ValueError:
        return None


# ─── step 1: backup + import ────────────────────────────────────
def backup_and_import(svc) -> dict:
    with svc._lock:
        _guard(svc)
        found = [p for p in (STATE_PATH, HASHES_PATH, CHANNELS_TXT) if p.exists()]
        if not found:
            raise MigrationError(f"No old files found in {DATA_DIR}")
        backup = DATA_DIR / f"backup-{time.strftime('%Y%m%d-%H%M%S')}"
        backup.mkdir(parents=True, exist_ok=True)
        for p in found:
            shutil.copy2(p, backup / p.name)

        hashes = _load_json(HASHES_PATH) or {}
        state = (_load_json(STATE_PATH) or {}).get("downloaded", {})
        added = known = outside = 0
        for key, e in state.items():
            rel = _old_rel(e.get("path") or "")
            if not rel:
                outside += 1
                continue
            sha = (hashes.get(rel) or [None, None, None])[2]
            if svc.library.import_legacy(key, e.get("message_id"), rel, None, e.get("size"),
                                         sha, bool(e.get("deleted"))):
                added += 1
            else:
                known += 1

    idents = []
    if CHANNELS_TXT.exists():
        for line in CHANNELS_TXT.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and line not in idents:
                idents.append(line)
    saved = svc.library.extra_idents()
    channels_added, channels_failed = 0, []
    for ident in idents:
        if ident in saved:
            continue
        cid, err = svc.add_channel(ident)
        if cid is None:
            channels_failed.append(f"{ident}: {err}")
        elif not err:
            channels_added += 1

    log = svc.progress.log
    log(f"Migration import: backup in {backup.name}, {added} records added, "
        f"{known} already known, {outside} outside the downloads folder, "
        f"{channels_added} channels added", "ok")
    for f in channels_failed:
        log(f"Migration import: channel not added — {f}", "warn")
    return {"backup": backup.name, "added": added, "known": known, "outside": outside,
            "channels_added": channels_added, "channels_failed": channels_failed}


# ─── step 2: old folders report ─────────────────────────────────
def _files(folder: Path) -> list:
    return sorted(f for f in folder.rglob("*") if f.is_file() and not f.name.startswith("."))


def _legacy_by_path(library) -> dict:
    out = {}
    for r in library.legacy_files():
        if r["status"] == "downloaded":
            out.setdefault(r["path"], []).append(r)
    return out


def old_folders(svc) -> list:
    if not DOWNLOADS_DIR.is_dir():
        return []
    new_layout = set(svc.library.channel_titles())
    legacy = _legacy_by_path(svc.library)
    out = []
    for d in sorted(DOWNLOADS_DIR.iterdir()):
        if not d.is_dir() or d.name.startswith(".") or d.name in new_layout:
            continue
        files = _files(d)
        tracked = sum(1 for f in files if f.relative_to(DOWNLOADS_DIR).as_posix() in legacy)
        match = next((c["id"] for c in svc.channels
                      if safe_filename(c["title"] or f"channel_{c['id']}") == d.name), None)
        out.append({
            "folder": d.name,
            "files": len(files),
            "size": sum(f.stat().st_size for f in files),
            "tracked": tracked,
            "untracked": len(files) - tracked,
            "channel_id": match,
        })
    return out


# ─── step 3: preview / run one folder ───────────────────────────
async def _fetch_messages(client, entity, ids) -> dict:
    out = {}
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        for mid, m in zip(chunk, await client.get_messages(entity, ids=chunk)):
            if m:
                out[mid] = m
    return out


def _plan(svc, folder: str, channel_id: int):
    d = DOWNLOADS_DIR / folder
    if "/" in folder or "\\" in folder or folder.startswith(".") or not d.is_dir():
        raise MigrationError(f"Unknown folder: {folder}")
    channel = next((c for c in svc.channels if c["id"] == channel_id), None)
    if not channel:
        raise MigrationError("Pick a channel from the list")

    legacy = _legacy_by_path(svc.library)
    files = _files(d)
    ids = sorted({r["message_id"] for f in files
                  for r in legacy.get(f.relative_to(DOWNLOADS_DIR).as_posix(), []) if r["message_id"]})
    msgs = svc._submit(_fetch_messages(svc._client, channel["entity"], ids)).result(
        timeout=60 + len(ids) // 10)

    actions, taken = [], set()
    for f in files:
        rel = f.relative_to(DOWNLOADS_DIR).as_posix()
        rows = legacy.get(rel)
        if not rows:
            actions.append({"old": rel, "new": None, "why": "no record in the old state"})
            continue
        hit = next(((r, msgs[r["message_id"]]) for r in rows
                    if r["message_id"] in msgs and get_media_key(msgs[r["message_id"]]) == r["media_key"]),
                   None)
        if not hit:
            gone = all(r["message_id"] not in msgs for r in rows)
            actions.append({"old": rel, "new": None,
                            "why": "message is gone from Telegram" if gone else "message media changed"})
            continue
        row, msg = hit
        new = media_rel_path(channel_id, msg, f.suffix)
        if new in taken or (DOWNLOADS_DIR / new).exists():
            actions.append({"old": rel, "new": None, "why": f"{new} already exists"})
            continue
        taken.add(new)
        actions.append({"old": rel, "new": new, "why": "", "_msg": msg, "_sha": row["sha256"]})
    return channel, actions


def _public(actions) -> list:
    return [{k: v for k, v in a.items() if not k.startswith("_")} for a in actions]


def preview(svc, folder: str, channel_id: int) -> dict:
    with svc._lock:
        _guard(svc)
        _, actions = _plan(svc, folder, channel_id)
    return {"actions": _public(actions)}


def run(svc, folder: str, channel_id: int) -> dict:
    log = svc.progress.log
    with svc._lock:
        _guard(svc)
        channel, actions = _plan(svc, folder, channel_id)
        lib = svc.library
        lib.save_channel(channel["id"], channel["title"], channel["username"])
        moved = 0
        for a in actions:
            if not a["new"]:
                continue
            src, dst, msg = DOWNLOADS_DIR / a["old"], DOWNLOADS_DIR / a["new"], a["_msg"]
            try:
                sha = a["_sha"] or hash_file(src)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(src, dst)
            except OSError as e:
                a.update(new=None, why=f"move failed: {e}")
                continue
            lib.move_file(a["old"], a["new"], channel_id, media_folder(msg), sha,
                          msg.message or None, msg.date.isoformat() if msg.date else None)
            moved += 1
        lib.claim_deleted(folder, channel_id)

        root = DOWNLOADS_DIR / folder
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

    stayed = len(actions) - moved
    log(f"Migration: {folder} → {channel_id}: {moved} moved, {stayed} stayed", "ok" if not stayed else "warn")
    return {"moved": moved, "stayed": stayed, "actions": _public(actions)}
