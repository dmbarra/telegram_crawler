"""Loads settings from .env (see .env.example for the full list).

Relative folder paths are resolved from the project root, not the current
directory, so `python main.py` works from anywhere.
"""
import os
from pathlib import Path
from dotenv import load_dotenv
from colorama import Fore, init

init(autoreset=True)

# src/config.py → src/ → project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"

# Real environment variables (e.g. from docker compose) win over .env.
load_dotenv(ENV_PATH)


def _folder(name: str, default: str) -> Path:
    path = Path(os.getenv(name) or default).expanduser()
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


# Where downloaded media is saved
DOWNLOADS_DIR = _folder("DOWNLOADS_DIR", "downloads")
# Where the app keeps its own files: session, dedup state, channels.txt, thumbnails
DATA_DIR = _folder("DATA_DIR", ".")

STATE_PATH = DATA_DIR / "download_state.json"
CHANNELS_TXT_PATH = DATA_DIR / "channels.txt"
THUMB_CACHE_DIR = DATA_DIR / ".thumb_cache"
HASH_INDEX_PATH = DATA_DIR / "file_hashes.json"
JOIN_LOG_PATH = DATA_DIR / "join_log.json"
INVITES_PATH = DATA_DIR / "invites.json"

_raw_api_id = (os.getenv("API_ID") or "").strip()
API_ID = int(_raw_api_id) if _raw_api_id.isdigit() else 0
API_HASH = (os.getenv("API_HASH") or "").strip()
SESSION_NAME = os.getenv("SESSION_NAME") or "default_session"
SESSION_PATH = DATA_DIR / SESSION_NAME

# Defaults for the Download page options
BATCH_SIZE = int(os.getenv("BATCH_SIZE") or "5")
MAX_MESSAGES = int(os.getenv("MAX_MESSAGES") or "1000")

# Discover page: safety limits for joining groups (Telegram restricts fast joiners)
JOIN_DAILY_LIMIT = int(os.getenv("JOIN_DAILY_LIMIT") or "20")
JOIN_MIN_INTERVAL = int(os.getenv("JOIN_MIN_INTERVAL") or "60")

# Read-only extras from .env: comma-separated @username / -100123 / https://t.me/foo.
# Channels added in the web page go to channels.txt instead (see src/storage.py).
EXTRA_CHANNELS = [
    x.strip()
    for x in (os.getenv("EXTRA_CHANNELS", "") or "").split(",")
    if x.strip()
]


def validate() -> bool:
    if not API_ID or not API_HASH:
        if _raw_api_id and not API_ID:
            print(f"{Fore.RED}API_ID must be a number (got '{_raw_api_id}')")
        else:
            print(f"{Fore.RED}API_ID / API_HASH missing — copy .env.example to .env and fill them in")
        print(f"{Fore.YELLOW}Get them at https://my.telegram.org/apps")
        return False
    return True
