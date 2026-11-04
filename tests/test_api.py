"""API tests for the controller (no real network calls — connectors are faked).

Run with:  pytest tests/ -q
"""

from __future__ import annotations

import base64
import os
import sys
import tempfile
from pathlib import Path

import pytest

TMP_DIR = tempfile.mkdtemp(prefix="rc-test-")
os.environ["RC_DATA_DIR"] = TMP_DIR
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rcserver import app as app_module, crypto, store, discovery  # noqa: E402
from rcserver.app import create_app  # noqa: E402
from rcserver.connectors import adb  # noqa: E402
from rcserver.connectors.adb import parse_ls  # noqa: E402
from rcserver.connectors.base import Connector, ConnectorError, Unsupported  # noqa: E402
from rcserver.connectors.winrm import smb_split  # noqa: E402


class FakeConnector(Connector):
    kind = "fake"
    platform = "windows"

    def __init__(self, target):
        self.target = target
        self.calls = []

    def test(self):
        return {
            "hostname": "TEST-PC",
            "username": "tester",
            "os_name": "Windows 11",
            "platform": "windows",
            "smb": True,
        }

    def run(self, command, timeout=30):
        self.calls.append(("run", command))
        return {"exit_code": 0, "stdout": f"ran: {command}", "stderr": ""}

    def screenshot(self):
        self.calls.append(("screenshot",))
        return b"fake-png-bytes"

    def clipboard_get(self):
        return "clipboard text"

    def clipboard_set(self, text):
        self.calls.append(("clipset", text))

    def list_dir(self, path):
        return {
            "path": path,
            "entries": [
                {
                    "name": "a.txt",
                    "path": path + "/a.txt",
                    "is_dir": False,
                    "size": 10,
                    "mtime": 1700000000,
                }
            ],
        }

    def download(self, path):
        return b"hello", {"name": "a.txt", "path": path, "size": 5}

    def upload(self, path, data):
        self.calls.append(("upload", path, data))

    def power(self, action):
        self.calls.append(("power", action))

    def android_tap(self, x, y):
        self.calls.append(("tap", x, y))

    def android_swipe(self, x1, y1, x2, y2):
        self.calls.append(("swipe", x1, y1, x2, y2))

    def android_text(self, text):
        self.calls.append(("text", text))

    def list_apps(self):
        return "package:com.example"

    def launch_app(self, package):
        self.calls.append(("launch", package))


@pytest.fixture()
def client(monkeypatch):
    store.init_db()
    created = []

    def factory(target, password):
        connector = FakeConnector(target)
        created.append(connector)
        return connector

    monkeypatch.setattr(app_module, "create_connector", factory)
    app = create_app()
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        test_client.created = created
        yield test_client


@pytest.fixture(autouse=True)
def clean_database():
    store.init_db()
    with store.db() as conn:
        for table in ("commands", "events", "targets"):
            conn.execute(f"DELETE FROM {table}")


def create_target(client, kind="winrm", **overrides):
    body = {
        "name": "Test target",
        "kind": kind,
        "host": "192.168.1.50",
        "username": "tester",
        "password": "secret",
        "platform": "windows",
        **overrides,
    }
    response = client.post("/api/targets", json=body)
    assert response.status_code == 200, response.get_json()
    return response.get_json()["target"]


# --------------------------------------------------------------------------- #
# meta / targets
# --------------------------------------------------------------------------- #
def test_health_and_server_info(client):
    assert client.get("/api/health").get_json()["ok"] is True
    info = client.get("/api/server").get_json()
    assert "quick_commands" in info
    assert "windows" in info["quick_commands"]
    assert info["default_paths"]["windows"] == "C:\\"


def test_create_target_and_password_encrypted(client):
    target = create_target(client)
    assert target["has_password"] is True
    assert "password_enc" not in target

    row = store.get_target(target["id"])
    assert row is not None
    assert row["password_enc"]
    assert row["password_enc"] != "secret"
    assert store.get_secret(row) == "secret"

    listed = client.get("/api/targets").get_json()["targets"]
    assert len(listed) == 1
    assert "password_enc" not in listed[0]


def test_test_target_updates_status(client):
    target = create_target(client)
    response = client.post(f"/api/targets/{target['id']}/test", json={})
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["os_name"] == "Windows 11"

    listed = client.get("/api/targets").get_json()["targets"][0]
    assert listed["status"] == "online"
    assert listed["os_name"] == "Windows 11"


def test_test_new_target_without_saving(client):
    response = client.post(
        "/api/targets/test",
        json={
            "kind": "ssh",
            "host": "192.168.1.51",
            "username": "tester",
            "password": "secret",
            "platform": "linux",
        },
    )
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["hostname"] == "TEST-PC"
    assert client.get("/api/targets").get_json()["targets"] == []


def test_invalid_target_kind(client):
    response = client.post(
        "/api/targets",
        json={"kind": "evil", "host": "1.2.3.4", "username": "x", "password": "y"},
    )
    assert response.status_code == 400


def test_missing_host(client):
    response = client.post("/api/targets", json={"kind": "winrm", "username": "x", "password": "y"})
    assert response.status_code == 400


def test_delete_target(client):
    target = create_target(client)
    assert client.delete(f"/api/targets/{target['id']}").status_code == 200
    assert client.get("/api/targets").get_json()["targets"] == []
    assert client.delete(f"/api/targets/{target['id']}").status_code == 404


def test_missing_target_returns_404(client):
    assert client.get("/api/targets/nope/commands").status_code == 404
    assert client.post("/api/targets/nope/exec", json={"command": "whoami"}).status_code == 404


# --------------------------------------------------------------------------- #
# operations
# --------------------------------------------------------------------------- #
def test_exec_and_history(client):
    target = create_target(client)
    response = client.post(
        f"/api/targets/{target['id']}/exec", json={"command": "whoami", "timeout": 30}
    )
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["stdout"] == "ran: whoami"

    commands = client.get(f"/api/targets/{target['id']}/commands").get_json()
    assert len(commands["commands"]) == 1
    command = commands["commands"][0]
    assert command["kind"] == "shell"
    assert command["input"] == "whoami"
    assert "ran: whoami" in command["output"]
    assert command["exit_code"] == 0
    assert commands["stats"]["ok"] == 1


def test_empty_command_rejected(client):
    target = create_target(client)
    assert client.post(f"/api/targets/{target['id']}/exec", json={"command": "  "}).status_code == 400


def test_screenshot(client):
    target = create_target(client)
    response = client.post(f"/api/targets/{target['id']}/screenshot", json={})
    assert response.status_code == 200, response.get_json()
    assert base64.b64decode(response.get_json()["image_base64"]) == b"fake-png-bytes"


def test_clipboard(client):
    target = create_target(client)
    response = client.post(
        f"/api/targets/{target['id']}/clipboard", json={"action": "get"}
    )
    assert response.status_code == 200
    assert response.get_json()["text"] == "clipboard text"

    response = client.post(
        f"/api/targets/{target['id']}/clipboard", json={"action": "set", "text": "hello"}
    )
    assert response.status_code == 200
    assert client.created[-1].calls[-1] == ("clipset", "hello")


def test_files(client):
    target = create_target(client)
    listing = client.post(
        f"/api/targets/{target['id']}/files", json={"action": "list", "path": "C:\\Users"}
    )
    assert listing.status_code == 200
    assert listing.get_json()["entries"][0]["name"] == "a.txt"

    download = client.post(
        f"/api/targets/{target['id']}/files", json={"action": "download", "path": "C:\\Users\\a.txt"}
    )
    assert download.status_code == 200
    assert base64.b64decode(download.get_json()["content_base64"]) == b"hello"

    upload = client.post(
        f"/api/targets/{target['id']}/files",
        json={
            "action": "upload",
            "path": "C:\\Users\\b.txt",
            "content_b64": base64.b64encode(b"uploaded").decode(),
        },
    )
    assert upload.status_code == 200
    assert client.created[-1].calls[-1] == ("upload", "C:\\Users\\b.txt", b"uploaded")


def test_power(client):
    target = create_target(client)
    response = client.post(f"/api/targets/{target['id']}/power", json={"action": "lock"})
    assert response.status_code == 200
    assert client.created[-1].calls[-1] == ("power", "lock")


def test_android_actions(client):
    target = create_target(client, kind="adb", host="192.168.1.50:40123", platform="android")
    response = client.post(
        f"/api/targets/{target['id']}/android", json={"action": "tap", "x": 10, "y": 20}
    )
    assert response.status_code == 200
    assert client.created[-1].calls[-1] == ("tap", 10, 20)

    response = client.post(
        f"/api/targets/{target['id']}/android", json={"action": "apps"}
    )
    assert response.status_code == 200
    assert response.get_json()["output"] == "package:com.example"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def test_crypto_roundtrip():
    token = crypto.encrypt("my secret")
    assert token
    assert token != "my secret"
    assert crypto.decrypt(token) == "my secret"
    assert crypto.decrypt("not-a-valid-token") == ""


def test_parse_ls():
    sample = """total 24
drwxr-xr-x 2 root root 4096 2024-01-01 12:00 .
-rw-r--r-- 1 root root 123 2024-01-01 12:00 file.txt
drwxr-xr-x 2 root root 4096 Jan  1 12:00 dir
"""
    result = parse_ls(sample, "/sdcard")
    names = [entry["name"] for entry in result["entries"]]
    assert "file.txt" in names
    assert "dir" in names
    assert "." not in names and ".." not in names
    assert result["path"] == "/sdcard"
    file_entry = next(entry for entry in result["entries"] if entry["name"] == "file.txt")
    assert file_entry["is_dir"] is False
    assert file_entry["size"] == 123
    dir_entry = next(entry for entry in result["entries"] if entry["name"] == "dir")
    assert dir_entry["is_dir"] is True


def test_smb_split():
    assert smb_split("C:\\Users\\foo") == ("C$", "Users\\foo")
    assert smb_split("C:/") == ("C$", "")
    assert smb_split("\\\\host\\share\\dir") == ("share", "dir")
    with pytest.raises(ConnectorError):
        smb_split("relative/path")


def test_adb_pair_without_adb(monkeypatch):
    monkeypatch.setattr(adb, "adb_path", lambda: None)
    with pytest.raises(ConnectorError):
        adb.adb_pair("192.168.1.50:37123", "123456")


def test_base_connector_unsupported():
    connector = Connector()
    with pytest.raises(Unsupported):
        connector.screenshot()
    with pytest.raises(Unsupported):
        connector.clipboard_get()
    with pytest.raises(Unsupported):
        connector.android_tap(1, 2)


# --------------------------------------------------------------------------- #
# discovery
# --------------------------------------------------------------------------- #
def test_parse_arp_output():
    sample = """? (192.168.1.1) at 00:11:22:33:44:55 on en0 ifscope [ethernet]
? (192.168.1.50) at 00:aa:bb:cc:dd:ee on en0 ifscope [ethernet]
"""
    hosts = discovery.parse_arp_output(sample)
    assert "192.168.1.1" in hosts
    assert "192.168.1.50" in hosts


def test_parse_adb_mdns():
    sample = "_adb._tcp 192.168.1.50:40123\n_other._tcp 192.168.1.51:1234\n"
    devices = discovery.parse_adb_mdns(sample)
    assert len(devices) == 1
    assert devices[0]["ip"] == "192.168.1.50:40123"
    assert devices[0]["kind"] == "adb"


def test_parse_adb_devices():
    sample = """List of devices attached
192.168.1.50:40123 device product:sdk_gphone64 model:Pixel_7 device_id:abc transport_id:1
192.168.1.51:40123 offline
"""
    devices = discovery.parse_adb_devices(sample)
    assert len(devices) == 1
    assert devices[0]["ip"] == "192.168.1.50:40123"
    assert devices[0]["hostname"] == "Pixel 7"


def test_classify_ports():
    assert discovery.classify_ports([5985]) == "winrm"
    assert discovery.classify_ports([5986]) == "winrm"
    assert discovery.classify_ports([22]) == "ssh"
    assert discovery.classify_ports([5555]) == "adb"
    assert discovery.classify_ports([80, 443]) is None


def test_subnet_hosts():
    hosts = discovery.subnet_hosts("192.168.1.10")
    assert len(hosts) == 254
    assert hosts[0] == "192.168.1.1"
    assert hosts[-1] == "192.168.1.254"


def test_scan_port():
    import socket
    import threading

    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    def accept_one():
        conn, _ = server.accept()
        conn.close()

    thread = threading.Thread(target=accept_one, daemon=True)
    thread.start()
    try:
        assert discovery.scan_port("127.0.0.1", port, timeout=1) is True
    finally:
        thread.join(timeout=2)
        server.close()
    assert discovery.scan_port("127.0.0.1", port, timeout=0.2) is False


def test_discover_endpoint(client, monkeypatch):
    sample = {
        "local_ip": "192.168.1.10",
        "subnet": "192.168.1.0/24",
        "devices": [
            {
                "ip": "192.168.1.50",
                "hostname": "TEST-PC",
                "ports": [5985],
                "kind": "winrm",
                "source": "port",
            }
        ],
        "adb_available": False,
    }
    monkeypatch.setattr(discovery, "scan_network", lambda: sample)
    response = client.post("/api/discover")
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert data["local_ip"] == "192.168.1.10"
    assert len(data["devices"]) == 1
    assert data["devices"][0]["kind"] == "winrm"
