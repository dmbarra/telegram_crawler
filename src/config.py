"""Loads credentials & paths. Never destroys your .env.

Important: PROJECT_ROOT is the parent of src/ so .env, downloads/,
download_state.json, channels.json all live at the top level.
"""
import os
from pathlib import Path
from dotenv import load_dotenv
from colorama import Fore, init

init(autoreset=True)

# src/config.py → src/ → project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent

ENV_PATH = PROJECT_ROOT / ".env"
STATE_PATH = PROJECT_ROOT / "download_state.json"
CHANNELS_JSON_PATH = PROJECT_ROOT / "channels.json"
DOWNLOADS_DIR = PROJECT_ROOT / "downloads"
CHANNELS_TXT_PATH = PROJECT_ROOT / "channels.txt"

# Auto-create .env if missing so first run has something to read
if not ENV_PATH.exists():
    ENV_PATH.write_text(
        "API_ID=your_api_id\n"
        "API_HASH=your_api_hash\n"
        "SESSION_NAME=default_session\n"
        "BATCH_SIZE=5\n"
        "MAX_MESSAGES=1000\n"
        "CHANNELS_SKIP=\n"
        "EXTRA_CHANNELS=\n",
        encoding="utf-8",
    )

load_dotenv(ENV_PATH)

API_ID = int(os.getenv("API_ID", "0") or 0)
API_HASH = os.getenv("API_HASH", "") or ""
SESSION_NAME = os.getenv("SESSION_NAME", "default_session")
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "5"))
MAX_MESSAGES = int(os.getenv("MAX_MESSAGES", "1000"))

# CHANNELS_SKIP="-100123,-100456" → {-100123, -100456}
CHANNELS_SKIP = {
    int(x.strip())
    for x in (os.getenv("CHANNELS_SKIP", "") or "").split(",")
    if x.strip().lstrip("-").isdigit()
}

# EXTRA_CHANNELS: comma-separated @username / -100123 / https://t.me/foo
EXTRA_CHANNELS = [
    x.strip()
    for x in (os.getenv("EXTRA_CHANNELS", "") or "").split(",")
    if x.strip()
]

# Also append entries from channels.txt (if present) — one per line, # for comments
if CHANNELS_TXT_PATH.exists():
    for line in CHANNELS_TXT_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            EXTRA_CHANNELS.append(line)


def validate() -> bool:
    if not API_ID or not API_HASH:
        print(f"{Fore.RED}API_ID / API_HASH missing in {ENV_PATH}")
        print(f"{Fore.YELLOW}Get them at https://my.telegram.org/apps and paste into .env")
        return False
    return True