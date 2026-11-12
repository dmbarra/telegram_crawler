"""Persistent state (dedup) + the channels.txt list of extra channels."""
import os
import json
from src.config import STATE_PATH, CHANNELS_TXT_PATH


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


# ─── channels.txt: one @username / link / id per line, # for comments ──
def _read_lines() -> list:
    if not CHANNELS_TXT_PATH.exists():
        return []
    return CHANNELS_TXT_PATH.read_text(encoding="utf-8").splitlines()


def _write_lines(lines: list) -> None:
    tmp = CHANNELS_TXT_PATH.with_suffix(".txt.tmp")
    tmp.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    os.replace(tmp, CHANNELS_TXT_PATH)


def load_extra_channels() -> list:
    out = []
    for line in _read_lines():
        line = line.strip()
        if line and not line.startswith("#") and line not in out:
            out.append(line)
    return out


def add_extra_channel(ident: str) -> None:
    lines = _read_lines()
    if ident not in (l.strip() for l in lines):
        _write_lines(lines + [ident])


def remove_extra_channel(ident: str) -> None:
    _write_lines([l for l in _read_lines() if l.strip() != ident])
