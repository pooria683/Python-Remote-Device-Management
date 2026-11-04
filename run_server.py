#!/usr/bin/env python3
"""Start the Remote Control server (controller).

    python run_server.py
    python run_server.py --port 8080

Open http://127.0.0.1:8000 and add a target. The target does NOT need to
reach this server — the controller connects to the target's built-in
WinRM / OpenSSH / ADB service.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rcserver import config, net, store  # noqa: E402
from rcserver.app import create_app  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Remote Control server")
    parser.add_argument("--host", default=config.HOST, help="bind address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=config.PORT, help="port (default: 8000)")
    args = parser.parse_args()

    store.init_db()
    app = create_app()

    print("=" * 62)
    print("  Remote Control — local control panel (no agent on targets)")
    print("=" * 62)
    print(f"  Local:   http://127.0.0.1:{args.port}")
    for ip in net.lan_ips():
        print(f"  LAN:     http://{ip}:{args.port}")
    print(f"  Database: {config.DB_PATH}")
    print(f"  adb:      {'found' if shutil.which('adb') else 'NOT found (needed for Android targets)'}")
    print("  Press Ctrl+C to stop.")
    print("=" * 62)

    app.run(host=args.host, port=args.port, threaded=True, use_reloader=False, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
