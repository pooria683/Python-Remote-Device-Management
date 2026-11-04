"""Small networking helpers (LAN address discovery)."""

from __future__ import annotations

import socket


def lan_ips() -> list[str]:
    """Return the IPv4 addresses other devices on the Wi‑Fi can reach us on."""
    ips: list[str] = []

    # This does not send any traffic; it just asks the kernel which
    # interface would be used to reach the internet.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
        if ip and not ip.startswith("127.") and ip not in ips:
            ips.append(ip)
    except OSError:
        pass

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except OSError:
        pass

    return ips or ["127.0.0.1"]
