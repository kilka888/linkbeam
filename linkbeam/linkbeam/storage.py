"""Small JSON-backed transfer history log."""

from __future__ import annotations

import json
import time
from pathlib import Path
from threading import Lock

HISTORY_PATH = Path.home() / ".linkbeam" / "history.json"
_lock = Lock()
_MAX_ENTRIES = 300


def _ensure() -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not HISTORY_PATH.exists():
        HISTORY_PATH.write_text("[]", encoding="utf-8")


def add_entry(direction: str, peer_name: str, filename: str, size: int, status: str = "completed") -> None:
    """direction: 'sent' or 'received'."""
    _ensure()
    with _lock:
        try:
            data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = []
        data.insert(0, {
            "direction": direction,
            "peer": peer_name,
            "filename": filename,
            "size": size,
            "status": status,
            "timestamp": time.time(),
        })
        data = data[:_MAX_ENTRIES]
        HISTORY_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_history() -> list:
    _ensure()
    with _lock:
        try:
            return json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []


def clear_history() -> None:
    with _lock:
        HISTORY_PATH.write_text("[]", encoding="utf-8")
