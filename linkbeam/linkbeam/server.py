"""Flask application: serves the web UI and implements both sides of the
transfer protocol (receiving on this node, delegating sends to other nodes).

Every LinkBeam instance is symmetric: it can be the sender or the receiver
in any given transfer, which is why this one file exposes both roles.
"""

from __future__ import annotations

import os
import platform
import secrets
import tempfile
import threading
import time
import uuid
from pathlib import Path

import requests
from flask import Flask, abort, jsonify, render_template, request, send_file

from . import client, mobile, storage
from .discovery import DiscoveryService, local_ip

WEB_DIR = Path(__file__).parent / "web"

app = Flask(
    __name__,
    template_folder=str(WEB_DIR / "templates"),
    static_folder=str(WEB_DIR / "static"),
)

# Populated by create_app()/run()
STATE = {
    "cfg": None,
    "discovery": None,
}

_lock = threading.Lock()
# transfer_id -> dict(status, sender_name, filename, size, code, tmp_path)
_incoming: dict[str, dict] = {}
# send_id -> dict(status, progress, filename, peer_name, error)
_outgoing: dict[str, dict] = {}

_PENDING_TTL = 120  # seconds a pending request stays visible before auto-expiring


def _cfg():
    return STATE["cfg"]


def _discovery() -> DiscoveryService:
    return STATE["discovery"]


def _cleanup_incoming():
    now = time.time()
    stale = [tid for tid, t in _incoming.items()
             if t["status"] == "pending" and now - t["created_at"] > _PENDING_TTL]
    for tid in stale:
        _incoming[tid]["status"] = "timeout"


# ---------------------------------------------------------------- web UI ---

@app.get("/")
def index():
    return render_template("index.html")


# --------------------------------------------------------------- identity --

@app.get("/api/me")
def api_me():
    cfg = _cfg()
    return jsonify({
        "id": cfg["device_id"],
        "name": cfg["name"],
        "os": platform.system(),
        "port": cfg["port"],
        "receive_dir": cfg["receive_dir"],
        "auto_accept": cfg["auto_accept"],
    })


@app.post("/api/me")
def api_update_me():
    cfg = _cfg()
    body = request.get_json(force=True, silent=True) or {}
    if "name" in body and body["name"].strip():
        cfg["name"] = body["name"].strip()[:64]
    if "auto_accept" in body:
        cfg["auto_accept"] = bool(body["auto_accept"])
    from . import config as config_module
    config_module.save_config(cfg)
    return jsonify({"ok": True})


@app.post("/api/me/open-folder")
def api_open_folder():
    """Best-effort: open the receive folder in the OS file explorer."""
    path = _cfg()["receive_dir"]
    system = platform.system()
    try:
        if system == "Windows":
            os.startfile(path)  # type: ignore[attr-defined]
        elif system == "Darwin":
            os.system(f'open "{path}"')
        else:
            os.system(f'xdg-open "{path}"')
        return jsonify({"ok": True})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


# ----------------------------------------------------------------- peers ---

@app.get("/api/peers")
def api_peers():
    return jsonify(_discovery().list_peers())


@app.post("/api/peers/manual")
def api_add_manual_peer():
    """Add a peer by IP:port directly — a fallback for networks that block
    mDNS/multicast (common on some corporate or guest Wi-Fi)."""
    body = request.get_json(force=True, silent=True) or {}
    address = (body.get("address") or "").strip()
    port = body.get("port")
    if not address or not port:
        return jsonify({"error": "address and port are required"}), 400
    try:
        info = requests.get(f"http://{address}:{port}/api/me", timeout=5).json()  # type: ignore[name-defined]
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"Could not reach {address}:{port} ({exc})"}), 502
    peer = {
        "id": info["id"],
        "name": info["name"],
        "address": address,
        "port": port,
        "os": info.get("os", "unknown"),
    }
    return jsonify(peer)


# --------------------------------------------------------- receiving side --

@app.post("/api/receive/request")
def api_receive_request():
    body = request.get_json(force=True, silent=True) or {}
    sender_name = str(body.get("sender_name", "Unknown device"))[:64]
    filename = os.path.basename(str(body.get("filename", "file")))
    size = int(body.get("size", 0))

    _cleanup_incoming()

    transfer_id = uuid.uuid4().hex
    code = f"{secrets.randbelow(9000) + 1000}"
    entry = {
        "status": "pending",
        "sender_name": sender_name,
        "filename": filename,
        "size": size,
        "code": code,
        "created_at": time.time(),
        "tmp_path": None,
    }

    cfg = _cfg()
    if cfg["auto_accept"]:
        entry["status"] = "accepted"

    with _lock:
        _incoming[transfer_id] = entry

    return jsonify({"transfer_id": transfer_id, "code": code})


@app.get("/api/receive/status/<transfer_id>")
def api_receive_status(transfer_id):
    entry = _incoming.get(transfer_id)
    if not entry:
        return jsonify({"status": "unknown"}), 404
    return jsonify({"status": entry["status"]})


@app.post("/api/receive/upload/<transfer_id>")
def api_receive_upload(transfer_id):
    entry = _incoming.get(transfer_id)
    if not entry:
        return jsonify({"error": "unknown transfer"}), 404
    if entry["status"] != "accepted":
        return jsonify({"error": f"transfer is {entry['status']}, not accepted"}), 409

    cfg = _cfg()
    dest_dir = Path(cfg["receive_dir"])
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = _unique_path(dest_dir / entry["filename"])

    written = 0
    with open(dest_path, "wb") as fh:
        for chunk in request.stream:
            fh.write(chunk)
            written += len(chunk)

    entry["status"] = "completed"
    entry["tmp_path"] = str(dest_path)
    storage.add_entry("received", entry["sender_name"], entry["filename"], written, "completed")
    return jsonify({"ok": True, "saved_to": str(dest_path)})


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    i = 1
    while True:
        candidate = path.with_name(f"{stem} ({i}){suffix}")
        if not candidate.exists():
            return candidate
        i += 1


@app.get("/api/pending")
def api_pending():
    _cleanup_incoming()
    out = []
    for tid, t in _incoming.items():
        if t["status"] == "pending":
            out.append({
                "transfer_id": tid,
                "sender_name": t["sender_name"],
                "filename": t["filename"],
                "size": t["size"],
                "code": t["code"],
            })
    return jsonify(out)


@app.post("/api/pending/<transfer_id>/accept")
def api_pending_accept(transfer_id):
    entry = _incoming.get(transfer_id)
    if not entry:
        return jsonify({"error": "unknown transfer"}), 404
    entry["status"] = "accepted"
    return jsonify({"ok": True})


@app.post("/api/pending/<transfer_id>/reject")
def api_pending_reject(transfer_id):
    entry = _incoming.get(transfer_id)
    if not entry:
        return jsonify({"error": "unknown transfer"}), 404
    entry["status"] = "rejected"
    return jsonify({"ok": True})


# ----------------------------------------------------------------- sending -

@app.post("/api/send")
def api_send():
    peer_id = request.form.get("peer_id")
    address = request.form.get("address")
    port = request.form.get("port")
    peer_name = request.form.get("peer_name", peer_id or "peer")
    upload = request.files.get("file")

    if not upload or not address or not port:
        return jsonify({"error": "file, address and port are required"}), 400

    tmp_fd, tmp_path = tempfile.mkstemp(prefix="linkbeam_")
    os.close(tmp_fd)
    upload.save(tmp_path)

    send_id = uuid.uuid4().hex
    _outgoing[send_id] = {
        "status": "waiting_accept",
        "progress": 0,
        "filename": upload.filename,
        "peer_name": peer_name,
        "error": None,
    }

    peer = {"id": peer_id, "name": peer_name, "address": address, "port": int(port)}
    sender_name = _cfg()["name"]
    original_filename = upload.filename or "file"

    def worker():
        def progress_cb(sent, total):
            pct = int(sent * 100 / total) if total else 100
            _outgoing[send_id]["status"] = "sending"
            _outgoing[send_id]["progress"] = pct

        try:
            result = client.send_file(peer, tmp_path, sender_name,
                                       filename=original_filename, progress_cb=progress_cb)
            _outgoing[send_id]["status"] = "completed"
            _outgoing[send_id]["progress"] = 100
            storage.add_entry("sent", peer_name, result["filename"], result["size"], "completed")
        except client.TransferRejected as exc:
            _outgoing[send_id]["status"] = "rejected"
            _outgoing[send_id]["error"] = str(exc)
            storage.add_entry("sent", peer_name, upload.filename, 0, "rejected")
        except client.TransferTimeout as exc:
            _outgoing[send_id]["status"] = "timeout"
            _outgoing[send_id]["error"] = str(exc)
            storage.add_entry("sent", peer_name, upload.filename, 0, "timeout")
        except Exception as exc:  # noqa: BLE001
            _outgoing[send_id]["status"] = "error"
            _outgoing[send_id]["error"] = str(exc)
            storage.add_entry("sent", peer_name, upload.filename, 0, "error")
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"send_id": send_id})


@app.get("/api/send/status/<send_id>")
def api_send_status(send_id):
    entry = _outgoing.get(send_id)
    if not entry:
        return jsonify({"error": "unknown send"}), 404
    return jsonify(entry)


# ---------------------------------------------------------------- history --

@app.get("/api/history")
def api_history():
    return jsonify(storage.get_history())


@app.post("/api/history/clear")
def api_history_clear():
    storage.clear_history()
    return jsonify({"ok": True})


# ----------------------------------------------------------- mobile / QR ---
# No app, no discovery needed on the other end — just a phone's camera and
# browser. See linkbeam/mobile.py for the token model.

def _phone_url(path: str) -> str:
    cfg = _cfg()
    return f"http://{local_ip()}:{cfg['port']}{path}"


@app.post("/api/mobile/send-init")
def api_mobile_send_init():
    """PC has a file, wants to beam it to a phone: stash it and hand back a
    QR code pointing at the download link."""
    upload = request.files.get("file")
    if not upload:
        return jsonify({"error": "file is required"}), 400

    tmp_fd, tmp_path = tempfile.mkstemp(prefix="linkbeam_pickup_")
    os.close(tmp_fd)
    upload.save(tmp_path)
    size = os.path.getsize(tmp_path)

    token = mobile.create_pickup(tmp_path, upload.filename or "file", size)
    url = _phone_url(f"/pickup/{token}")
    return jsonify({
        "token": token,
        "url": url,
        "qr_svg": mobile.make_qr_svg(url),
        "filename": upload.filename,
        "size": size,
    })


@app.get("/pickup/<token>")
def pickup(token):
    entry = mobile.get(token)
    if not entry or entry["kind"] != "pickup":
        abort(404, "Эта ссылка недействительна или уже истекла.")
    mobile.mark_status(token, "downloaded")
    storage.add_entry("sent", "📱 Телефон (QR)", entry["filename"], entry["size"], "completed")
    return send_file(entry["path"], as_attachment=True, download_name=entry["filename"])


@app.post("/api/mobile/receive-init")
def api_mobile_receive_init():
    """PC wants a file from a phone: hand back a QR code pointing at a tiny
    upload page, no file yet."""
    token = mobile.create_dropoff()
    url = _phone_url(f"/dropoff/{token}")
    return jsonify({"token": token, "url": url, "qr_svg": mobile.make_qr_svg(url)})


@app.get("/dropoff/<token>")
def dropoff_page(token):
    entry = mobile.get(token)
    if not entry or entry["kind"] != "dropoff":
        return render_template(
            "mobile_dropoff.html", token=token, device_name=_cfg()["name"]
        ), 404
    return render_template("mobile_dropoff.html", token=token, device_name=_cfg()["name"])


@app.post("/dropoff/<token>/upload")
def dropoff_upload(token):
    entry = mobile.get(token)
    if not entry or entry["kind"] != "dropoff":
        return jsonify({"error": "link expired"}), 410

    upload = request.files.get("file")
    if not upload:
        return jsonify({"error": "file is required"}), 400

    cfg = _cfg()
    dest_dir = Path(cfg["receive_dir"])
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = _unique_path(dest_dir / (upload.filename or "phone-file"))
    upload.save(dest_path)
    size = dest_path.stat().st_size

    mobile.mark_status(token, "completed", filename=upload.filename)
    storage.add_entry("received", "📱 Телефон (QR)", upload.filename or "file", size, "completed")
    return jsonify({"ok": True})


@app.get("/api/mobile/status/<token>")
def api_mobile_status(token):
    entry = mobile.get(token)
    if not entry:
        return jsonify({"status": "expired"})
    return jsonify({"status": entry["status"], "filename": entry.get("filename")})

