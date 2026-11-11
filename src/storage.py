"""Persistent state + SAFE .env / channels.json writing."""
import os
import json
import base64
from colorama import Fore
from src.config import ENV_PATH, STATE_PATH, CHANNELS_JSON_PATH


# ─── dedup state ────────────────────────────────────────────────
def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            data.setdefault("downloaded", {})
            return data
        except Exception:
            pass
    return {"downloaded": {}}


def save_state(state: dict) -> None:
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, STATE_PATH)


# ─── channels list → .env (safe) + channels.json (redundant) ────
def save_channels_to_env(channels: list) -> None:
    """Updates ONLY the CHANNELS_LIST line. Backs up first. Verifies
    API_ID / API_HASH survive. Atomic write. Never destroys your .env."""
    if not channels:
        return

    # Always write channels.json first (independent backup)
    try:
        CHANNELS_JSON_PATH.write_text(
            json.dumps(channels, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as e:
        print(f"{Fore.YELLOW}[storage] channels.json write failed: {e}")

    if not ENV_PATH.exists():
        print(f"{Fore.YELLOW}[storage] {ENV_PATH} not found; see channels.json")
        return

    encoded = base64.b64encode(
        json.dumps(channels, ensure_ascii=False).encode("utf-8")
    ).decode("ascii")

    try:
        original = ENV_PATH.read_text(encoding="utf-8")

        # Backup BEFORE touching anything
        (ENV_PATH.parent / (ENV_PATH.name + ".bak")).write_text(original, encoding="utf-8")

        # Rebuild content (preserve all other lines)
        lines = original.splitlines(keepends=True)
        kept = [l for l in lines if not l.startswith("CHANNELS_LIST=")]
        if kept and not kept[-1].endswith(("\n", "\r")):
            kept[-1] += "\n"
        kept.append(f'CHANNELS_LIST="{encoded}"\n')
        new_content = "".join(kept)

        # Refuse to commit if we lost a critical key
        for required in ("API_ID=", "API_HASH="):
            if required not in new_content:
                print(f"{Fore.RED}[storage] ABORT: {required} would be lost. .env untouched.")
                return

        # Atomic write
        tmp = ENV_PATH.with_suffix(".env.tmp")
        tmp.write_text(new_content, encoding="utf-8")
        os.replace(tmp, ENV_PATH)

        os.environ["CHANNELS_LIST"] = encoded
        print(f"{Fore.GREEN}[storage] ✓ CHANNELS_LIST saved to .env "
              f"({len(channels)} entries). Backup: .env.bak")

    except Exception as e:
        print(f"{Fore.RED}[storage] .env update failed: {e}. Original intact.")


def load_channels_from_env() -> list:
    raw = os.getenv("CHANNELS_LIST")
    if not raw:
        if CHANNELS_JSON_PATH.exists():
            try:
                return json.loads(CHANNELS_JSON_PATH.read_text(encoding="utf-8"))
            except Exception:
                return []
        return []
    try:
        return json.loads(base64.b64decode(raw).decode("utf-8"))
    except Exception:
        try:
            return json.loads(raw)
        except Exception:
            return []