"""Connector interface shared by WinRM, SSH and ADB."""

from __future__ import annotations


class ConnectorError(Exception):
    """Connection or execution failure."""


class Unsupported(ConnectorError):
    """The target platform does not support this operation."""


class Connector:
    kind = "base"
    platform = "unknown"

    def test(self) -> dict:
        raise NotImplementedError

    def run(self, command: str, timeout: int = 30) -> dict:
        raise NotImplementedError

    def screenshot(self) -> bytes:
        raise Unsupported("این دستگاه از اسکرین‌شات پشتیبانی نمی‌کند")

    def clipboard_get(self) -> str:
        raise Unsupported("این دستگاه از کلیپ‌بورد پشتیبانی نمی‌کند")

    def clipboard_set(self, text: str) -> None:
        raise Unsupported("این دستگاه از کلیپ‌بورد پشتیبانی نمی‌کند")

    def list_dir(self, path: str) -> dict:
        raise Unsupported("این دستگاه از مرور فایل پشتیبانی نمی‌کند")

    def download(self, path: str) -> tuple[bytes, dict]:
        raise Unsupported("این دستگاه از دانلود فایل پشتیبانی نمی‌کند")

    def upload(self, path: str, data: bytes) -> None:
        raise Unsupported("این دستگاه از آپلود فایل پشتیبانی نمی‌کند")

    def power(self, action: str) -> None:
        raise Unsupported("این دستگاه از عملیات قدرت پشتیبانی نمی‌کند")

    def android_tap(self, x: float, y: float) -> None:
        raise Unsupported("این عملیات فقط برای اندروید است")

    def android_swipe(self, x1: float, y1: float, x2: float, y2: float) -> None:
        raise Unsupported("این عملیات فقط برای اندروید است")

    def android_text(self, text: str) -> None:
        raise Unsupported("این عملیات فقط برای اندروید است")

    def list_apps(self) -> str:
        raise Unsupported("این عملیات فقط برای اندروید است")

    def launch_app(self, package: str) -> None:
        raise Unsupported("این عملیات فقط برای اندروید است")

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
