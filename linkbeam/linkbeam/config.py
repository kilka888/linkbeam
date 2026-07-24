"""Device identity & user settings.

Everything is stored locally in ~/.linkbeam — no accounts, no cloud, no
telemetry. The config file just remembers a stable device id (so peers
recognize you across restarts), your display name, the UI port, and the
folder where incoming files land.
"""

from __future__ import annotations

import json
import socket
import uuid
from pathlib import Path

CONFIG_DIR = Path.home() / ".linkbeam"
CONFIG_PATH = CONFIG_DIR / "config.json"
DEFAULT_RECEIVE_DIR = Path.home() / "LinkBeam Received"
DEFAULT_PORT = 47111


def load_config() -> dict:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    cfg = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            cfg = {}

    changed = False
    if "device_id" not in cfg:
        cfg["device_id"] = uuid.uuid4().hex[:12]
        changed = True
    if "name" not in cfg:
        cfg["name"] = socket.gethostname()
        changed = True
    if "port" not in cfg:
        cfg["port"] = DEFAULT_PORT
        changed = True
    if "receive_dir" not in cfg:
        cfg["receive_dir"] = str(DEFAULT_RECEIVE_DIR)
        changed = True
    if "auto_accept" not in cfg:
        cfg["auto_accept"] = False
        changed = True

    if changed:
        save_config(cfg)

    Path(cfg["receive_dir"]).mkdir(parents=True, exist_ok=True)
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
