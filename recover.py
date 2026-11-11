"""Recover state after the project was moved (C: → D:) + deduplicate disk.

Strategy:
  1. Heal state paths that point to the old root using basename match
  2. For still-broken entries, match by (channel, size) against disk
  3. Delete same-channel/same-size duplicate files (keep canonical copy)
  4. Report cross-channel dupes + unmatched orphans for manual review

Usage:
    python recover.py                 # dry-run — shows plan, no changes
    python recover.py --apply         # performs state heal + deletes dupes
    python recover.py --apply --aggressive   # ALSO deletes cross-channel dupes
"""
import json
import os
import sys
from pathlib import Path
from collections import defaultdict

PROJECT_ROOT = Path(__file__).resolve().parent
DOWNLOADS_DIR = PROJECT_ROOT / "downloads"
STATE_PATH = PROJECT_ROOT / "download_state.json"

APPLY = "--apply" in sys.argv
AGGRESSIVE = "--aggressive" in sys.argv

if not STATE_PATH.exists():
    print("❌ download_state.json not found")
    sys.exit(1)

if not DOWNLOADS_DIR.exists():
    print(f"❌ downloads folder not found at {DOWNLOADS_DIR}")
    sys.exit(1)

state = json.load(open(STATE_PATH, encoding="utf-8"))
entries = state["downloaded"]

print(f"📋 State entries: {len(entries):,}")
print(f"📁 Downloads dir: {DOWNLOADS_DIR}")
print(f"⚙️  Mode: {'APPLY' if APPLY else 'DRY RUN'}"
      f"{' (aggressive)' if AGGRESSIVE else ''}")


# ─── build indexes ──────────────────────────────────────────────
def walk_disk():
    """Returns list of (Path, channel, size)."""
    out = []
    for f in DOWNLOADS_DIR.rglob("*"):
        if not f.is_file() or f.name.endswith(".part"):
            continue
        try:
            rel = f.relative_to(DOWNLOADS_DIR)
            channel = rel.parts[0] if rel.parts else ""
            out.append((f, channel, f.stat().st_size))
        except OSError:
            continue
    return out


disk = walk_disk()
print(f"💾 Files on disk: {len(disk):,}")

# (channel, size) → [Path, ...]
by_cs: dict[tuple, list[Path]] = defaultdict(list)
# (channel, basename) → [Path, ...]
by_cb: dict[tuple, list[Path]] = defaultdict(list)

for p, ch, sz in disk:
    by_cs[(ch, sz)].append(p)
    by_cb[(ch, p.name)].append(p)


# ─── rank: which copy is "canonical" ────────────────────────────
def _rank(f: Path):
    """Lower = better. Prefer: no _N suffix, shorter name."""
    stem = f.stem
    has_suffix = any(stem.endswith(f"_{i}") for i in range(1, 100))
    return (has_suffix, len(f.name), f.name.lower())


# Canonical per (channel, size): pick the best one
canonical: dict[tuple, Path] = {}
dupes_by_cs: dict[tuple, list[Path]] = {}
for cs, files in by_cs.items():
    if len(files) == 1:
        canonical[cs] = files[0]
    else:
        files_sorted = sorted(files, key=_rank)
        canonical[cs] = files_sorted[0]
        dupes_by_cs[cs] = files_sorted[1:]


# ─── helper: extract channel + basename from old state path ─────
def split_old_path(old_path: str) -> tuple[str, str, Path | None]:
    """Return (channel, basename, relative_path_under_downloads or None)."""
    try:
        parts = Path(old_path).parts
    except Exception:
        return "", "", None
    try:
        idx = parts.index("downloads")
    except ValueError:
        return "", Path(old_path).name, None
    channel = parts[idx + 1] if idx + 1 < len(parts) else ""
    basename = parts[-1]
    relative = Path(*parts[idx + 1:]) if idx + 1 < len(parts) else None
    return channel, basename, relative


# ─── STEP 1: heal state paths ───────────────────────────────────
healed_simple = 0   # exact path existed under new root
healed_size = 0     # matched by (channel, size)
healed_name = 0     # matched by (channel, basename)
unmatched = []

for key, entry in entries.items():
    old_path = entry.get("path", "")
    expected_size = entry.get("size", 0) or 0
    channel, basename, relative = split_old_path(old_path)

    # 1a. Same relative structure under new root
    if relative:
        candidate = DOWNLOADS_DIR / relative
        if candidate.exists():
            entry["path"] = str(candidate)
            entry["size"] = entry.get("size") or candidate.stat().st_size
            healed_simple += 1
            continue

    # 1b. Already at a valid path?
    if old_path and os.path.exists(old_path):
        try:
            if not expected_size or os.path.getsize(old_path) == expected_size:
                continue
        except OSError:
            pass

    # 1c. Match by (channel, size) → use canonical file
    if expected_size and (channel, expected_size) in canonical:
        entry["path"] = str(canonical[(channel, expected_size)])
        healed_size += 1
        continue

    # 1d. Match by (channel, basename) — old entries with size=0
    if (channel, basename) in by_cb:
        best = sorted(by_cb[(channel, basename)], key=_rank)[0]
        entry["path"] = str(best)
        try:
            entry["size"] = entry.get("size") or best.stat().st_size
        except OSError:
            pass
        healed_name += 1
        continue

    unmatched.append((key, old_path, expected_size))


# ─── STEP 2: determine which files are claimed by state ─────────
claimed: set[str] = set()
for e in entries.values():
    p = e.get("path")
    if p and os.path.exists(p):
        try:
            claimed.add(str(Path(p).resolve()))
        except Exception:
            pass

# ─── STEP 3: same-channel/size duplicates to delete ─────────────
to_delete_same_channel: list[Path] = []
for cs, files in dupes_by_cs.items():
    for f in files:
        try:
            if str(f.resolve()) in claimed:
                continue  # claimed by state (state won, keep it)
        except Exception:
            pass
        to_delete_same_channel.append(f)

# ─── STEP 4: cross-channel dupes (same size, different channels) ─
cross_channel_dupes: list[Path] = []
size_to_claimed: dict[int, list[Path]] = defaultdict(list)
for p in [Path(c) for c in claimed]:
    try:
        size_to_claimed[p.stat().st_size].append(p)
    except OSError:
        pass

for p, ch, sz in disk:
    if str(p.resolve()) in claimed:
        continue
    if p in to_delete_same_channel:
        continue
    if sz in size_to_claimed and size_to_claimed[sz]:
        cross_channel_dupes.append(p)

# ─── STEP 5: unreferenced orphans (no size match anywhere) ─────
known_dupes = set(to_delete_same_channel) | set(cross_channel_dupes)
orphans_only: list[Path] = []
for p, ch, sz in disk:
    if str(p.resolve()) in claimed:
        continue
    if p in known_dupes:
        continue
    orphans_only.append(p)


# ─── Report ─────────────────────────────────────────────────────
print(f"\n🔧 Path healing:")
print(f"   Simple rewrite (same relative path):   {healed_simple:,}")
print(f"   Matched by (channel, size):            {healed_size:,}")
print(f"   Matched by (channel, basename):        {healed_name:,}")
print(f"   Still unmatched (will redownload):     {len(unmatched):,}")
for k, p, s in unmatched[:8]:
    print(f"      {Path(p).name}  (size={s:,} B)")

print(f"\n👯 Same-channel/size duplicates: {len(to_delete_same_channel):,}")
for f in to_delete_same_channel[:10]:
    try:
        print(f"   {f.relative_to(DOWNLOADS_DIR)}")
    except ValueError:
        print(f"   {f}")

print(f"\n🌍 Cross-channel duplicates (same size, diff channel): "
      f"{len(cross_channel_dupes):,}")
for f in cross_channel_dupes[:8]:
    try:
        print(f"   {f.relative_to(DOWNLOADS_DIR)}")
    except ValueError:
        print(f"   {f}")

print(f"\n❔ True orphans (no size match — kept for review): {len(orphans_only):,}")
for f in orphans_only[:8]:
    try:
        print(f"   {f.relative_to(DOWNLOADS_DIR)}")
    except ValueError:
        print(f"   {f}")


# ─── Apply ──────────────────────────────────────────────────────
if not APPLY:
    print(f"\n🔍 DRY RUN — re-run with --apply to:")
    print(f"   • Save healed state "
          f"({healed_simple + healed_size + healed_name:,} paths fixed)")
    print(f"   • Delete {len(to_delete_same_channel):,} same-channel duplicates")
    if AGGRESSIVE:
        print(f"   • Delete {len(cross_channel_dupes):,} cross-channel duplicates")
    print(f"\n   Add --aggressive to also remove cross-channel dupes.")
    sys.exit(0)

# Backup state
backup = STATE_PATH.with_suffix(".json.bak")
backup.write_text(STATE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
print(f"\n💾 Backup saved: {backup.name}")

# Save healed state
with open(STATE_PATH, "w", encoding="utf-8") as fp:
    json.dump(state, fp, indent=2, ensure_ascii=False)
print(f"✅ State saved ({len(entries):,} entries)")

# Delete same-channel dupes
deleted = 0
freed = 0
for f in to_delete_same_channel:
    try:
        sz = f.stat().st_size
        f.unlink()
        deleted += 1
        freed += sz
    except OSError as e:
        print(f"   ⚠ {f}: {e}")

# Delete cross-channel dupes if aggressive
if AGGRESSIVE:
    for f in cross_channel_dupes:
        try:
            sz = f.stat().st_size
            f.unlink()
            deleted += 1
            freed += sz
        except OSError as e:
            print(f"   ⚠ {f}: {e}")

print(f"\n✅ Deleted {deleted:,} files")
print(f"✅ Freed {freed / 1024**3:.2f} GB")

if orphans_only:
    print(f"\n⚠ {len(orphans_only)} true orphans were NOT deleted.")
    print(f"   These are files on disk not referenced by state and no size match.")
    print(f"   Review manually. List saved to orphans.txt")
    with open(PROJECT_ROOT / "orphans.txt", "w", encoding="utf-8") as f:
        for p in orphans_only:
            try:
                f.write(str(p.relative_to(DOWNLOADS_DIR)) + "\n")
            except ValueError:
                f.write(str(p) + "\n")