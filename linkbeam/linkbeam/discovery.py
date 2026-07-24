"""LAN peer discovery over mDNS/Zeroconf.

Every LinkBeam instance announces itself as `_linkbeam._tcp.local.` and
browses for others doing the same. No server, no registry, no accounts —
peers simply find each other the moment they join the same network,
exactly the way printers and Chromecasts do.
"""

from __future__ import annotations

import platform
import socket
import threading
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from zeroconf import IPVersion, ServiceInfo, ServiceListener, ServiceBrowser, Zeroconf

SERVICE_TYPE = "_linkbeam._tcp.local."


@dataclass
class Peer:
    id: str
    name: str
    address: str
    port: int
    os_name: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "address": self.address,
            "port": self.port,
            "os": self.os_name,
        }


def _local_ip() -> str:
    """Best-effort LAN IP without actually sending traffic anywhere."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class _Listener(ServiceListener):
    def __init__(self, self_id: str, on_change: Callable[[], None]):
        self._self_id = self_id
        self._on_change = on_change
        self._peers: Dict[str, Peer] = {}
        self._lock = threading.Lock()

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self._refresh(zc, type_, name)

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self._refresh(zc, type_, name)

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        peer_id = name.split(".")[0]
        with self._lock:
            self._peers.pop(peer_id, None)
        self._on_change()

    def _refresh(self, zc: Zeroconf, type_: str, name: str) -> None:
        info = zc.get_service_info(type_, name, timeout=2000)
        if not info:
            return
        peer_id = name.split(".")[0]
        if peer_id == self._self_id:
            return
        addresses = info.parsed_addresses()
        if not addresses:
            return
        props = {
            k.decode() if isinstance(k, bytes) else k: (v.decode() if isinstance(v, bytes) else v)
            for k, v in (info.properties or {}).items()
        }
        peer = Peer(
            id=peer_id,
            name=props.get("name", peer_id),
            address=addresses[0],
            port=info.port,
            os_name=props.get("os", "unknown"),
        )
        with self._lock:
            self._peers[peer_id] = peer
        self._on_change()

    def list_peers(self) -> List[Peer]:
        with self._lock:
            return list(self._peers.values())


class DiscoveryService:
    def __init__(self, device_id: str, display_name: str, port: int,
                 on_change: Optional[Callable[[], None]] = None):
        self.device_id = device_id
        self.display_name = display_name
        self.port = port
        self._on_change = on_change or (lambda: None)
        self._zc: Optional[Zeroconf] = None
        self._browser: Optional[ServiceBrowser] = None
        self._info: Optional[ServiceInfo] = None
        self._listener: Optional[_Listener] = None

    def start(self) -> None:
        self._zc = Zeroconf(ip_version=IPVersion.V4Only)
        ip = _local_ip()
        self._info = ServiceInfo(
            SERVICE_TYPE,
            f"{self.device_id}.{SERVICE_TYPE}",
            addresses=[socket.inet_aton(ip)],
            port=self.port,
            properties={"name": self.display_name, "os": platform.system()},
        )
        self._zc.register_service(self._info)
        self._listener = _Listener(self.device_id, self._on_change)
        self._browser = ServiceBrowser(self._zc, SERVICE_TYPE, self._listener)

    def stop(self) -> None:
        if self._zc:
            if self._info:
                try:
                    self._zc.unregister_service(self._info)
                except Exception:
                    pass
            self._zc.close()

    def list_peers(self) -> List[dict]:
        if not self._listener:
            return []
        return [p.to_dict() for p in self._listener.list_peers()]
