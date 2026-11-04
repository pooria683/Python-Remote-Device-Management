"""Automatic discovery of targets on the local network.

No IP needs to be typed: the controller scans the local /24 (plus the ARP
table) for open WinRM / SSH / ADB ports and asks the local adb server for
wireless-debugging devices. Credentials are still required by the target's
built-in service — discovery only removes the IP typing.
"""

from __future__ import annotations

import ipaddress
import shutil
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

COMMON_PORTS = [22, 5985, 5986, 5555]
PORT_TIMEOUT = 0.35


# --------------------------------------------------------------------------- #
# parsing helpers (testable without the network)
# --------------------------------------------------------------------------- #
def parse_arp_output(text: str) -> list[str]:
    """Extract IPv4 addresses from `arp -a` / `ip neigh` output."""
    hosts: list[str] = []
    for line in (text or "").splitlines():
        for token in line.split():
            token = token.strip("(),;:")
            try:
                ip = str(ipaddress.ip_address(token))
            except ValueError:
                continue
            if ip.count(".") == 3 and not ip.startswith("127."):
                hosts.append(ip)
    return list(dict.fromkeys(hosts))


def parse_adb_mdns(text: str) -> list[dict]:
    devices = []
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[1] in ("_adb-tls-connect._tcp", "_adb._tcp"):
            address = parts[2]
        elif len(parts) >= 2 and parts[0] == "_adb._tcp":
            address = parts[1]
        else:
            continue
        if address:
            devices.append(
                {
                    "ip": address,
                    "hostname": "Android (ADB)",
                    "ports": [],
                    "kind": "adb",
                    "source": "mdns",
                }
            )
    return devices


def parse_adb_devices(text: str) -> list[dict]:
    devices = []
    for line in (text or "").splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            serial = parts[0]
            model = ""
            for part in parts[2:]:
                if part.startswith("model:"):
                    model = part.split(":", 1)[1].replace("_", " ")
            devices.append(
                {
                    "ip": serial,
                    "hostname": model or "Android (ADB)",
                    "ports": [],
                    "kind": "adb",
                    "source": "adb",
                }
            )
    return devices


def classify_ports(ports: list[int]) -> str | None:
    if 5985 in ports or 5986 in ports:
        return "winrm"
    if 22 in ports:
        return "ssh"
    if 5555 in ports:
        return "adb"
    return None


# --------------------------------------------------------------------------- #
# network helpers
# --------------------------------------------------------------------------- #
def local_ipv4() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        pass
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return "127.0.0.1"


def subnet_of(ip: str) -> str:
    try:
        return str(ipaddress.ip_network(f"{ip}/24", strict=False))
    except ValueError:
        return ""


def subnet_hosts(ip: str) -> list[str]:
    network = subnet_of(ip)
    if not network:
        return []
    return [str(host) for host in ipaddress.ip_network(network).hosts()]


def arp_hosts() -> list[str]:
    hosts: list[str] = []
    for command in (["arp", "-a"], ["ip", "neigh"]):
        if not shutil.which(command[0]):
            continue
        try:
            result = subprocess.run(command, capture_output=True, timeout=5, text=True)
        except (OSError, subprocess.TimeoutExpired):
            continue
        for ip in parse_arp_output(result.stdout):
            if ip not in hosts:
                hosts.append(ip)
    return hosts


def scan_port(ip: str, port: int, timeout: float = PORT_TIMEOUT) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


def resolve_hostname(ip: str) -> str:
    try:
        return socket.gethostbyaddr(ip)[0]
    except OSError:
        return ip


def scan_host(ip: str) -> dict | None:
    ports = [port for port in COMMON_PORTS if scan_port(ip, port)]
    if not ports:
        return None
    return {
        "ip": ip,
        "hostname": resolve_hostname(ip),
        "ports": ports,
        "kind": classify_ports(ports),
        "source": "port",
    }


def adb_mdns_devices() -> list[dict]:
    adb = shutil.which("adb")
    if not adb:
        return []
    devices: list[dict] = []
    try:
        result = subprocess.run(
            [adb, "mdns", "services"], capture_output=True, timeout=10, text=True
        )
        devices.extend(parse_adb_mdns(result.stdout))
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        result = subprocess.run(
            [adb, "devices", "-l"], capture_output=True, timeout=10, text=True
        )
        devices.extend(parse_adb_devices(result.stdout))
    except (OSError, subprocess.TimeoutExpired):
        pass
    return list({device["ip"]: device for device in devices}.values())


def scan_network(max_hosts: int = 512) -> dict:
    local_ip = local_ipv4()

    candidates: list[str] = []
    for ip in arp_hosts():
        if ip not in candidates:
            candidates.append(ip)
    for ip in subnet_hosts(local_ip):
        if ip not in candidates:
            candidates.append(ip)
    for ip in ("127.0.0.1", local_ip):
        if ip not in candidates:
            candidates.append(ip)
    candidates = candidates[:max_hosts]

    found: list[dict] = []
    with ThreadPoolExecutor(max_workers=64) as executor:
        futures = [executor.submit(scan_host, ip) for ip in candidates]
        for future in as_completed(futures):
            try:
                result = future.result()
            except Exception:  # noqa: BLE001
                result = None
            if result:
                found.append(result)

    for device in adb_mdns_devices():
        if not any(item["ip"] == device["ip"] for item in found):
            found.append(device)

    order = {"winrm": 0, "ssh": 1, "adb": 2}
    found.sort(key=lambda item: (order.get(item.get("kind") or "", 9), item["ip"]))
    return {
        "local_ip": local_ip,
        "subnet": subnet_of(local_ip),
        "devices": found,
        "adb_available": bool(shutil.which("adb")),
    }
