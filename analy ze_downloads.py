"""Diagnostic: compare download_state.json with what's actually on disk.

    python analyze_downloads.py
"""
import json
import os
from pathlib import Path
from collections import defaultdict

PROJECT_ROOT = Path(__file__).resolve().parent
STATE_PATH = PROJECT_ROOT / "download_state.json"
DOWNLOADS_DIR = PROJECT_ROOT / "downloads"

if not STATE_PATH.exists():
    print("❌ download_state.json not found")
    raise SystemExit(1)

state = json.load(open(STATE_PATH, encoding="utf-8"))["downloaded"]
print(f"📋 State entries: {len(state):,}")

# Normalize tracked paths for cross-platform comparison
def norm(p: str) -> str:
    try:
        return str(Path(p).resolve()).lower() if os.name == "nt" else str(Path(p).resolve())
    except Exception:
        return p.lower() if os.name == "nt" else p

tracked = {norm(e["path"]): (k, e) for k, e in state.items() if e.get("path")}

# Scan disk (skip .part files — those are incomplete downloads)
disk_files = [f for f in DOWNLOADS_DIR.rglob("*")
              if f.is_file() and not f.name.endswith(".part")]

print(f"💾 Files on disk: {len(disk_files):,}")

# Orphans: on disk, not in state
orphans = []
for f in disk_files:
    if norm(str(f)) not in tracked:
        orphans.append(f)

# Missing: in state, not on disk
missing = []
for p, (k, e) in tracked.items():
    if not Path(p).exists():
        missing.append((k, e["path"], e.get("size", 0)))

# .part leftovers
part_files = [f for f in DOWNLOADS_DIR.rglob("*.part") if f.is_file()]

print(f"\n🧹 Orphans (on disk, NOT in state):    {len(orphans):,}")
for o in orphans[:15]:
    print(f"   {o.relative_to(DOWNLOADS_DIR)}  ({o.stat().st_size:,} B)")
if len(orphans) > 15:
    print(f"   … and {len(orphans) - 15} more")

print(f"\n❓ Missing (in state, NOT on disk):    {len(missing):,}")
for k, p, s in missing[:15]:
    print(f"   {p}  (key={k}, expected {s:,} B)")
if len(missing) > 15:
    print(f"   … and {len(missing) - 15} more")

print(f"\n🗑️  .part leftovers (incomplete):       {len(part_files):,}")
for p in part_files[:10]:
    print(f"   {p.relative_to(DOWNLOADS_DIR)}  ({p.stat().st_size:,} B)")

# Same-size duplicates (likely same file downloaded twice)
size_map = defaultdict(list)
for f in disk_files:
    try:
        size_map[f.stat().st_size].append(f)
    except OSError:
        pass
dupes = {s: fs for s, fs in size_map.items() if len(fs) > 1 and s > 100 * 1024}

print(f"\n👯 Size-identical duplicate groups:    {len(dupes):,}")
for size, files in sorted(dupes.items(), key=lambda x: -len(x[1]))[:8]:
    print(f"   {size:,} B — {len(files)} copies:")
    for f in files[:4]:
        print(f"      {f.relative_to(DOWNLOADS_DIR)}")

# Verdict
total_on_disk = len(disk_files)
total_tracked = len(state)
if total_on_disk == total_tracked and not orphans and not missing and not part_files:
    print(f"\n✅ CLEAN — state and disk match exactly.")
else:
    print(f"\n⚠️  MISMATCH")
    print(f"   disk={total_on_disk:,}  state={total_tracked:,}  "
          f"orphans={len(orphans)}  missing={len(missing)}  parts={len(part_files)}")