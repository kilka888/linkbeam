"""Sender-side logic: ask a peer for permission, then stream the file.

Protocol (plain HTTP, LAN only):

  1. POST /api/receive/request   {sender_name, filename, size} -> {transfer_id}
  2. GET  /api/receive/status/<transfer_id>   (poll until accepted/rejected)
  3. POST /api/receive/upload/<transfer_id>   raw chunked body -> saved to disk

Every transfer requires an explicit accept on the receiving end unless the
receiver has turned on auto-accept for trusted networks.
"""

from __future__ import annotations

import os
import time
from typing import Callable, Optional

import requests

CHUNK_SIZE = 256 * 1024


class TransferRejected(Exception):
    pass


class TransferTimeout(Exception):
    pass


def send_file(peer: dict, filepath: str, sender_name: str,
              filename: Optional[str] = None,
              progress_cb: Optional[Callable[[int, int], None]] = None,
              accept_timeout: int = 60) -> dict:
    """filepath is where the bytes actually live on disk (often a temp file);
    filename is the name shown to the recipient and used to save the file,
    which may differ from the temp file's own name on disk."""
    filename = filename or os.path.basename(filepath)
    size = os.path.getsize(filepath)
    base = f"http://{peer['address']}:{peer['port']}"

    resp = requests.post(
        f"{base}/api/receive/request",
        json={"sender_name": sender_name, "filename": filename, "size": size},
        timeout=10,
    )
    resp.raise_for_status()
    transfer_id = resp.json()["transfer_id"]

    deadline = time.time() + accept_timeout
    status = "pending"
    while time.time() < deadline:
        s = requests.get(f"{base}/api/receive/status/{transfer_id}", timeout=10).json()
        status = s.get("status", "pending")
        if status != "pending":
            break
        time.sleep(1)

    if status == "rejected":
        raise TransferRejected(f"{peer.get('name', peer['id'])} declined the transfer")
    if status != "accepted":
        raise TransferTimeout("No response from the recipient in time")

    sent = 0

    def _stream():
        nonlocal sent
        with open(filepath, "rb") as fh:
            while True:
                chunk = fh.read(CHUNK_SIZE)
                if not chunk:
                    break
                sent += len(chunk)
                if progress_cb:
                    progress_cb(sent, size)
                yield chunk

    headers = {"Content-Type": "application/octet-stream", "X-Filename": filename}
    up = requests.post(f"{base}/api/receive/upload/{transfer_id}", data=_stream(),
                        headers=headers, timeout=None)
    up.raise_for_status()
    return {"status": "completed", "filename": filename, "size": size}
