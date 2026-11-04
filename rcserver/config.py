"""Server configuration (controller side only — nothing is installed on targets)."""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("RC_DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

WEB_DIR = BASE_DIR / "web"
DB_PATH = DATA_DIR / "rc.db"
KEY_PATH = DATA_DIR / "secret.key"

HOST = os.environ.get("RC_HOST", "127.0.0.1")
PORT = int(os.environ.get("RC_PORT", "8000"))
VERSION = "2.0.0"

# File transfer limits (controller side).
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_UPLOAD_BYTES = int(MAX_FILE_BYTES * 1.4) + 1024 * 1024
MAX_RESULT_CHARS = 200_000
MAX_LIST_RESULT_CHARS = 12_000

# How long a command may run before the connector gives up.
DEFAULT_COMMAND_TIMEOUT = 30
MAX_COMMAND_TIMEOUT = 300

# Target kinds.
KINDS = {"winrm", "ssh", "adb"}

DEFAULT_PORTS = {
    "winrm": 5985,
    "winrm_https": 5986,
    "ssh": 22,
}
