"""LinkBeam entry point.

Usage:
    python -m linkbeam                 start LinkBeam and open the UI
    python -m linkbeam --port 47500    use a specific port
    python -m linkbeam --no-browser    don't auto-open the browser
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
import webbrowser

from . import config as config_module
from . import server
from .discovery import DiscoveryService


def _banner(cfg: dict) -> str:
    url = f"http://127.0.0.1:{cfg['port']}"
    return (
        "\n"
        "  ██╗     ██╗███╗   ██╗██╗  ██╗██████╗ ███████╗ █████╗ ███╗   ███╗\n"
        "  ██║     ██║████╗  ██║██║ ██╔╝██╔══██╗██╔════╝██╔══██╗████╗ ████║\n"
        "  ██║     ██║██╔██╗ ██║█████╔╝ ██████╔╝█████╗  ███████║██╔████╔██║\n"
        "  ██║     ██║██║╚██╗██║██╔═██╗ ██╔══██╗██╔══╝  ██╔══██║██║╚██╔╝██║\n"
        "  ███████╗██║██║ ╚████║██║  ██╗██████╔╝███████╗██║  ██║██║ ╚═╝ ██║\n"
        "  ╚══════╝╚═╝╚═╝  ╚═══╝╚═╝  ╚═╝╚═════╝ ╚══════╝╚═╝  ╚═╝╚═╝     ╚═╝\n"
        "\n"
        f"  device name : {cfg['name']}\n"
        f"  device id   : {cfg['device_id']}\n"
        f"  save folder : {cfg['receive_dir']}\n"
        f"  local url   : {url}\n"
        "\n"
        "  Open the URL above (or let your browser pop up) on every PC you\n"
        "  want to beam files between. Everything stays on your LAN.\n"
        "  Press Ctrl+C to stop.\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="linkbeam", description="Zero-config LAN file sharing.")
    parser.add_argument("--port", type=int, help="port for the local web UI / transfer server")
    parser.add_argument("--name", type=str, help="display name shown to other devices")
    parser.add_argument("--no-browser", action="store_true", help="don't auto-open a browser tab")
    args = parser.parse_args()

    cfg = config_module.load_config()
    if args.port:
        cfg["port"] = args.port
    if args.name:
        cfg["name"] = args.name
    config_module.save_config(cfg)

    discovery = DiscoveryService(cfg["device_id"], cfg["name"], cfg["port"])
    discovery.start()

    server.STATE["cfg"] = cfg
    server.STATE["discovery"] = discovery

    print(_banner(cfg))

    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{cfg['port']}")).start()

    def _shutdown(*_args):
        print("\nStopping LinkBeam...")
        discovery.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    server.app.run(host="0.0.0.0", port=cfg["port"], threaded=True, debug=False)


if __name__ == "__main__":
    main()
