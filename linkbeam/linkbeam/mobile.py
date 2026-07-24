"""QR-code handoff for phones and other devices with no LinkBeam of their
own — no app, no discovery, just a browser.

Two kinds of short-lived tokens:

  pickup   PC has a file, wants to beam it to a phone.
           The phone scans a QR code -> browser opens a URL -> file downloads
           immediately (it's a plain GET with Content-Disposition: attachment).

  dropoff  PC wants a file *from* a phone (a photo, say).
           The phone scans a QR code -> browser opens a tiny upload page ->
           picks/captures a file -> it lands straight in the receive folder.

Both are deliberately simple: a token is just an entry in memory with a TTL,
no accounts, nothing written anywhere but the transferred file itself.
"""

from __future__ import annotations

import io
import re
import secrets
import time
from pathlib import Path
from threading import Lock
from typing import Optional

import qrcode
import qrcode.image.svg

TTL_SECONDS = 10 * 60  # links self-expire after 10 minutes

_lock = Lock()
_links: dict[str, dict] = {}

_SVG_SIZE_ATTRS = re.compile(r'\s(width|height)="[^"]*mm"')


def _new_token() -> str:
    return secrets.token_urlsafe(16)


def make_qr_svg(data: str) -> str:
    """Render a QR code for `data` as an inline, responsively-sized SVG string."""
    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    svg = buf.getvalue().decode("utf-8")
    # Strip the fixed mm width/height so CSS can size it to fit its container.
    return _SVG_SIZE_ATTRS.sub("", svg)


def create_pickup(tmp_path: str, filename: str, size: int) -> str:
    """Register a file on this PC as available for a phone to download."""
    token = _new_token()
    with _lock:
        _links[token] = {
            "kind": "pickup",
            "path": tmp_path,
            "filename": filename,
            "size": size,
            "created_at": time.time(),
            "status": "waiting",
        }
    return token


def create_dropoff() -> str:
    """Register a slot for a phone to upload a file into."""
    token = _new_token()
    with _lock:
        _links[token] = {
            "kind": "dropoff",
            "created_at": time.time(),
            "status": "waiting",
            "filename": None,
        }
    return token


def get(token: str) -> Optional[dict]:
    _cleanup()
    return _links.get(token)


def mark_status(token: str, status: str, **extra) -> None:
    entry = _links.get(token)
    if entry:
        entry["status"] = status
        entry.update(extra)


def _cleanup() -> None:
    now = time.time()
    with _lock:
        expired = [t for t, e in _links.items() if now - e["created_at"] > TTL_SECONDS]
        for t in expired:
            entry = _links.pop(t)
            if entry["kind"] == "pickup":
                try:
                    Path(entry["path"]).unlink(missing_ok=True)
                except OSError:
                    pass
