"""Interactive channel skip-list manager.

Run:   python manage_channels.py
Toggles channels between "active" and "skip" by editing CHANNELS_SKIP
in .env. Your CHANNELS_LIST (base64) is never touched.
"""
import os
import json
import base64
from colorama import Fore, Style, init
from dotenv import load_dotenv

init(autoreset=True)
load_dotenv()

ENV_PATH = ".env"

raw = os.getenv("CHANNELS_LIST", "")
if not raw:
    print(f"{Fore.RED}No CHANNELS_LIST in .env. Run main.py once first.{Style.RESET_ALL}")
    raise SystemExit(1)

channels = json.loads(base64.b64decode(raw).decode("utf-8"))

skip_raw = os.getenv("CHANNELS_SKIP", "") or ""
skip_set = {
    int(x.strip())
    for x in skip_raw.split(",")
    if x.strip().lstrip("-").isdigit()
}

# ── show current state
print(f"\n{Fore.CYAN}Current channels:{Style.RESET_ALL}")
for i, ch in enumerate(channels, 1):
    mark = (f"{Fore.RED}✗ SKIP  {Style.RESET_ALL}"
            if ch["id"] in skip_set
            else f"{Fore.GREEN}✓ ACTIVE{Style.RESET_ALL}")
    print(f"  {i:3}. {mark}  {ch['title'][:45]:45}  (ID: {ch['id']})")

active = len(channels) - len(skip_set)
print(f"\n{Fore.CYAN}{active} active, {len(skip_set)} skipped.{Style.RESET_ALL}")

# ── prompt
print(f"\n{Fore.CYAN}Toggle channels — enter numbers like '1 3 5-8' or ENTER to cancel:{Style.RESET_ALL}")
sel = input("> ").strip()

if not sel:
    print("No changes.")
    raise SystemExit(0)

selected = set()
for part in sel.split():
    if "-" in part:
        try:
            a, b = part.split("-", 1)
            selected.update(range(int(a), int(b) + 1))
        except ValueError:
            print(f"{Fore.RED}Bad range: {part}{Style.RESET_ALL}")
    else:
        try:
            selected.add(int(part))
        except ValueError:
            print(f"{Fore.RED}Bad number: {part}{Style.RESET_ALL}")

for n in selected:
    if 1 <= n <= len(channels):
        cid = channels[n - 1]["id"]
        skip_set.symmetric_difference_update({cid})  # toggle

# ── persist back to .env (safe)
with open(ENV_PATH, "r", encoding="utf-8") as f:
    lines = f.readlines()

lines = [l for l in lines if not l.startswith("CHANNELS_SKIP=")]
if lines and not lines[-1].endswith("\n"):
    lines[-1] += "\n"
new_val = ",".join(str(x) for x in sorted(skip_set))
lines.append(f"CHANNELS_SKIP={new_val}\n")

tmp = ENV_PATH + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    f.writelines(lines)
os.replace(tmp, ENV_PATH)

print(f"\n{Fore.GREEN}✓ Saved. {len(skip_set)} channel(s) now skipped, "
      f"{len(channels) - len(skip_set)} active.{Style.RESET_ALL}")
print(f"{Fore.YELLOW}Re-run: python main.py{Style.RESET_ALL}")