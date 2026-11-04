"""Quick commands and default paths per platform (displayed in the panel)."""

from __future__ import annotations

QUICK_COMMANDS: dict[str, list[dict]] = {
    "windows": [
        {"label": "whoami", "command": "whoami"},
        {"label": "سیستم", "command": "systeminfo"},
        {"label": "شبکه", "command": "ipconfig /all"},
        {"label": "فرآیندها", "command": "tasklist"},
        {"label": "فایل‌ها", "command": "dir"},
        {"label": "مسیر فعلی", "command": "cd"},
    ],
    "linux": [
        {"label": "whoami", "command": "whoami && id"},
        {"label": "سیستم", "command": "uname -a"},
        {"label": "شبکه", "command": "ip addr || ifconfig"},
        {"label": "فرآیندها", "command": "ps aux | head -n 15"},
        {"label": "فایل‌ها", "command": "ls -la"},
        {"label": "فضای دیسک", "command": "df -h"},
    ],
    "android": [
        {"label": "مدل دستگاه", "command": "getprop ro.product.model"},
        {"label": "اندروید", "command": "getprop ro.build.version.release"},
        {"label": "باتری", "command": "dumpsys battery | grep -E 'level|temperature'"},
        {"label": "شبکه", "command": "ip addr show wlan0"},
        {"label": "اپ‌های نصب‌شده", "command": "pm list packages -3"},
        {"label": "پردازنده", "command": "cat /proc/cpuinfo | tail -n 8"},
    ],
}

DEFAULT_PATHS = {
    "windows": "C:\\",
    "linux": "~",
    "android": "/sdcard",
}


def quick_commands(platform: str | None) -> list[dict]:
    return QUICK_COMMANDS.get((platform or "windows").lower(), QUICK_COMMANDS["windows"])


def default_path(platform: str | None) -> str:
    return DEFAULT_PATHS.get((platform or "windows").lower(), "C:\\")
