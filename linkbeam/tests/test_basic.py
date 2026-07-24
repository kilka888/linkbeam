"""Lightweight smoke tests that don't require real multicast networking
(CI runners and sandboxes often block mDNS). Full peer-discovery is best
verified by hand on two machines on the same LAN — see README."""

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_imports():
    for mod in ["linkbeam.config", "linkbeam.storage", "linkbeam.discovery",
                "linkbeam.client", "linkbeam.server", "linkbeam.mobile"]:
        importlib.import_module(mod)


def test_mobile_qr_and_tokens(tmp_path):
    from linkbeam import mobile

    svg = mobile.make_qr_svg("http://192.168.1.5:47111/pickup/abc")
    assert svg.startswith("<?xml")
    assert "mm" not in svg  # fixed mm sizing stripped so CSS can scale it

    f = tmp_path / "photo.jpg"
    f.write_bytes(b"fake-jpeg-bytes")
    token = mobile.create_pickup(str(f), "photo.jpg", f.stat().st_size)
    entry = mobile.get(token)
    assert entry["kind"] == "pickup"
    assert entry["status"] == "waiting"
    mobile.mark_status(token, "downloaded")
    assert mobile.get(token)["status"] == "downloaded"

    dtoken = mobile.create_dropoff()
    dentry = mobile.get(dtoken)
    assert dentry["kind"] == "dropoff"
    mobile.mark_status(dtoken, "completed", filename="IMG.jpg")
    assert mobile.get(dtoken)["filename"] == "IMG.jpg"

    assert mobile.get("unknown-token") is None


def test_peer_to_dict():
    from linkbeam.discovery import Peer
    p = Peer(id="abc123", name="Test PC", address="192.168.1.20", port=47111, os_name="Linux")
    d = p.to_dict()
    assert d == {
        "id": "abc123",
        "name": "Test PC",
        "address": "192.168.1.20",
        "port": 47111,
        "os": "Linux",
    }


def test_history_roundtrip(tmp_path, monkeypatch):
    from linkbeam import storage
    monkeypatch.setattr(storage, "HISTORY_PATH", tmp_path / "history.json")
    storage.clear_history()
    storage.add_entry("sent", "Другой ПК", "photo.png", 1024, "completed")
    items = storage.get_history()
    assert len(items) == 1
    assert items[0]["filename"] == "photo.png"
    assert items[0]["direction"] == "sent"


def test_config_roundtrip(tmp_path, monkeypatch):
    from linkbeam import config as config_module
    monkeypatch.setattr(config_module, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_module, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(config_module, "DEFAULT_RECEIVE_DIR", tmp_path / "Received")
    cfg = config_module.load_config()
    assert "device_id" in cfg
    assert cfg["port"] == config_module.DEFAULT_PORT
    cfg["name"] = "My PC"
    config_module.save_config(cfg)
    cfg2 = config_module.load_config()
    assert cfg2["name"] == "My PC"
    assert cfg2["device_id"] == cfg["device_id"]
