"""ADB connector — controls an Android device over wireless debugging.

Nothing is installed on the phone: enable Developer options → Wireless
debugging, pair once with `adb pair`, then `adb connect`. The adb binary
runs on the controller (download Google's Platform Tools).
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import tempfile
from datetime import datetime

from .base import Connector, ConnectorError, Unsupported


def _decode(data: bytes) -> str:
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def adb_path() -> str | None:
    return shutil.which("adb")


def adb_pair(address: str, code: str) -> str:
    adb = adb_path()
    if not adb:
        raise ConnectorError(
            "adb پیدا نشد. Android Platform Tools را نصب کنید و در PATH قرار دهید."
        )
    result = subprocess.run(
        [adb, "pair", address, code], capture_output=True, timeout=30
    )
    output = (_decode(result.stdout) + _decode(result.stderr)).strip()
    if result.returncode != 0:
        raise ConnectorError(output or "جفت‌سازی adb ممکن نشد")
    return output


def adb_connect(address: str) -> str:
    adb = adb_path()
    if not adb:
        raise ConnectorError(
            "adb پیدا نشد. Android Platform Tools را نصب کنید و در PATH قرار دهید."
        )
    result = subprocess.run([adb, "connect", address], capture_output=True, timeout=30)
    output = (_decode(result.stdout) + _decode(result.stderr)).strip()
    if result.returncode != 0:
        raise ConnectorError(output or "اتصال adb ممکن نشد")
    return output


def parse_ls(output: str, path: str) -> dict:
    entries = []
    base = (path or "/sdcard").rstrip("/") or "/"
    for line in (output or "").splitlines():
        line = line.rstrip()
        if not line or line.startswith("total "):
            continue
        parts = line.split(None, 8)
        if len(parts) < 5:
            continue
        permissions = parts[0]
        if not permissions or permissions[0] not in "-dlbcps":
            continue
        if len(parts) >= 9:
            name, date_parts = parts[8], parts[5:8]
        elif len(parts) >= 8:
            name, date_parts = parts[7], parts[5:7]
        else:
            continue
        if not name or name in (".", ".."):
            continue
        try:
            size = int(parts[4])
        except ValueError:
            size = 0
        mtime = None
        try:
            if len(date_parts) == 3:
                if ":" in date_parts[2]:
                    stamp = datetime.strptime(
                        f"2000 {date_parts[0]} {date_parts[1]} {date_parts[2]}",
                        "%Y %b %d %H:%M",
                    ).replace(year=datetime.now().year)
                else:
                    stamp = datetime.strptime(
                        f"{date_parts[0]} {date_parts[1]} {date_parts[2]}", "%b %d %Y"
                    )
            else:
                stamp = datetime.strptime(
                    f"{date_parts[0]} {date_parts[1]}", "%Y-%m-%d %H:%M"
                )
            mtime = stamp.timestamp()
        except ValueError:
            mtime = None
        entries.append(
            {
                "name": name,
                "path": base + "/" + name,
                "is_dir": permissions.startswith("d"),
                "size": size,
                "mtime": mtime,
            }
        )
    entries.sort(key=lambda entry: (not entry["is_dir"], entry["name"].lower()))
    return {"path": base, "entries": entries}


class ADBConnector(Connector):
    kind = "adb"
    platform = "android"

    def __init__(self, target: dict):
        self.adb = adb_path()
        if not self.adb:
            raise ConnectorError(
                "adb پیدا نشد. Android Platform Tools را نصب کنید و در PATH قرار دهید."
            )
        self.serial = str(target.get("host") or "").strip()
        if not self.serial:
            raise ConnectorError("آدرس دستگاه (IP:port) لازم است")

    def _run(self, args: list[str], timeout: int = 30, input_bytes: bytes | None = None) -> dict:
        try:
            result = subprocess.run(
                [self.adb, "-s", self.serial, *args],
                capture_output=True,
                timeout=timeout,
                input=input_bytes,
            )
        except FileNotFoundError as exc:
            raise ConnectorError("اجرای adb ممکن نشد") from exc
        except subprocess.TimeoutExpired as exc:
            raise ConnectorError(f"زمان اتصال به دستگاه تمام شد: {exc}") from exc
        return {
            "exit_code": result.returncode,
            "stdout": _decode(result.stdout),
            "stderr": _decode(result.stderr),
        }

    def _raw(self, args: list[str], timeout: int = 60) -> bytes:
        try:
            result = subprocess.run(
                [self.adb, "-s", self.serial, *args],
                capture_output=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise ConnectorError(f"زمان اتصال به دستگاه تمام شد: {exc}") from exc
        if result.returncode != 0:
            raise ConnectorError(_decode(result.stderr) or "دستور adb ممکن نشد")
        return result.stdout

    def test(self) -> dict:
        state = self._run(["get-state"], timeout=15)
        value = state["stdout"].strip()
        if value == "unauthorized":
            raise ConnectorError(
                "دستگاه اجازه‌ی اتصال نداد. روی گوشی پیام اجازه را تأیید کنید."
            )
        if value != "device":
            raise ConnectorError(
                "دستگاه در دسترس نیست. ابتدا Wireless debugging را فعال و سپس adb connect را اجرا کنید."
            )
        model = self._run(["shell", "getprop", "ro.product.model"], timeout=15)["stdout"].strip()
        release = self._run(["shell", "getprop", "ro.build.version.release"], timeout=15)["stdout"].strip()
        abi = self._run(["shell", "getprop", "ro.product.cpu.abi"], timeout=15)["stdout"].strip()
        return {
            "hostname": model or "Android device",
            "username": "",
            "os_name": f"Android {release} ({abi})".strip(),
            "platform": "android",
        }

    def run(self, command: str, timeout: int = 30) -> dict:
        return self._run(["shell", command], timeout=timeout)

    def screenshot(self) -> bytes:
        data = self._raw(["exec-out", "screencap", "-p"], timeout=60)
        if not data:
            raise ConnectorError("تصویر صفحه خالی بود")
        return data

    def list_dir(self, path: str) -> dict:
        result = self._run(["shell", "ls", "-la", path or "/sdcard"], timeout=30)
        if result["exit_code"] != 0 and not result["stdout"]:
            raise ConnectorError(result["stderr"] or "فهرست کردن پوشه ممکن نشد")
        return parse_ls(result["stdout"], path or "/sdcard")

    def download(self, path: str) -> tuple[bytes, dict]:
        handle, temp_path = tempfile.mkstemp()
        os.close(handle)
        try:
            result = self._run(["pull", path, temp_path], timeout=120)
            if result["exit_code"] != 0:
                raise ConnectorError(result["stderr"] or "دانلود فایل ممکن نشد")
            size = os.path.getsize(temp_path)
            if size > 8 * 1024 * 1024:
                raise ConnectorError("حجم فایل بیش از ۸ مگابایت است")
            with open(temp_path, "rb") as file:
                data = file.read()
        finally:
            try:
                os.remove(temp_path)
            except OSError:
                pass
        return data, {
            "name": os.path.basename(path.rstrip("/")),
            "path": path,
            "size": size,
        }

    def upload(self, path: str, data: bytes) -> None:
        if len(data) > 8 * 1024 * 1024:
            raise ConnectorError("حجم فایل بیش از ۸ مگابایت است")
        handle, temp_path = tempfile.mkstemp()
        try:
            with os.fdopen(handle, "wb") as file:
                file.write(data)
            result = self._run(["push", temp_path, path], timeout=120)
            if result["exit_code"] != 0:
                raise ConnectorError(result["stderr"] or "آپلود فایل ممکن نشد")
        finally:
            try:
                os.remove(temp_path)
            except OSError:
                pass

    def power(self, action: str) -> None:
        commands = {
            "lock": "input keyevent 26",
            "sleep": "input keyevent 223",
            "restart": "reboot",
            "shutdown": "svc power shutdown || reboot -p",
        }
        command = commands.get(action)
        if not command:
            raise ConnectorError("عملیات قدرت نامعتبر است")
        try:
            self._run(["shell", command], timeout=20)
        except Exception:
            pass

    def android_tap(self, x: float, y: float) -> None:
        self._run(["shell", "input", "tap", str(int(x)), str(int(y))], timeout=15)

    def android_swipe(self, x1: float, y1: float, x2: float, y2: float) -> None:
        self._run(
            ["shell", "input", "swipe", str(int(x1)), str(int(y1)), str(int(x2)), str(int(y2))],
            timeout=15,
        )

    def android_text(self, text: str) -> None:
        self._run(["shell", "input", "text", text], timeout=15)

    def list_apps(self) -> str:
        result = self._run(["shell", "pm", "list", "packages", "-3"], timeout=30)
        return result["stdout"]

    def launch_app(self, package: str) -> None:
        self._run(
            ["shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"],
            timeout=30,
        )

    def clipboard_get(self) -> str:
        raise Unsupported("خواندن کلیپ‌بورد اندروید بدون اپلیکیشن کمکی ممکن نیست")

    def clipboard_set(self, text: str) -> None:
        raise Unsupported("نوشتن کلیپ‌بورد اندروید بدون اپلیکیشن کمکی ممکن نیست")
