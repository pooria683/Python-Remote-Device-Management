"""SSH connector — works with Windows OpenSSH Server, Linux and macOS.

Nothing is installed on the target beyond the built-in OpenSSH Server
(Windows: Settings → Optional features → OpenSSH Server).
"""

from __future__ import annotations

import base64
import os
import shlex
import stat

import paramiko

from .base import Connector, ConnectorError


def _decode(data: bytes) -> str:
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


class SSHConnector(Connector):
    kind = "ssh"

    def __init__(self, target: dict, password: str):
        self.target = target
        self.password = password or ""
        self.host = str(target.get("host") or "").strip()
        if not self.host:
            raise ConnectorError("آدرس دستگاه لازم است")
        self.port = int(target.get("port") or 22)
        self.username = str(target.get("username") or "").strip()
        if not self.username:
            raise ConnectorError("نام کاربری لازم است")
        self.key_path = str(target.get("key_path") or "").strip()
        self.platform = str(target.get("platform") or "auto").lower()

        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs = {
            "hostname": self.host,
            "port": self.port,
            "username": self.username,
            "timeout": 15,
            "banner_timeout": 30,
            "auth_timeout": 30,
            "allow_agent": False,
            "look_for_keys": False,
        }
        if self.key_path and os.path.isfile(self.key_path):
            kwargs["key_filename"] = self.key_path
        if self.password:
            kwargs["password"] = self.password
        if "key_filename" not in kwargs and "password" not in kwargs:
            raise ConnectorError("برای SSH رمز عبور یا فایل کلید لازم است")

        try:
            self.client.connect(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise ConnectorError(f"اتصال SSH ممکن نشد: {exc}") from exc

        self.sftp = None
        if self.platform == "auto":
            self.platform = self._detect_platform()

    # ------------------------------------------------------------------ #
    # low level
    # ------------------------------------------------------------------ #
    def _detect_platform(self) -> str:
        try:
            result = self._exec("uname -a 2>/dev/null || ver", timeout=15)
            return "linux" if "Linux" in result["stdout"] else "windows"
        except Exception:
            return "linux"

    def _wrap(self, command: str) -> str:
        if self.platform == "windows":
            encoded = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
            return f"powershell -NoProfile -NonInteractive -EncodedCommand {encoded}"
        return command

    def _exec(self, command: str, timeout: int = 30) -> dict:
        try:
            _stdin, stdout, stderr = self.client.exec_command(command, timeout=timeout)
            output = _decode(stdout.read())
            error = _decode(stderr.read())
            code = stdout.channel.recv_exit_status()
            return {"exit_code": code, "stdout": output, "stderr": error}
        except Exception as exc:  # noqa: BLE001
            raise ConnectorError(f"اجرای دستور SSH ممکن نشد: {exc}") from exc

    def _get_sftp(self):
        if self.sftp is None:
            try:
                self.sftp = self.client.open_sftp()
            except Exception as exc:  # noqa: BLE001
                raise ConnectorError(f"سرویس SFTP در دسترس نیست: {exc}") from exc
        return self.sftp

    def _remote_path(self, path: str) -> str:
        path = (path or ".").strip()
        sftp = self._get_sftp()
        if path == "~":
            return sftp.normalize(".")
        if path.startswith("~/"):
            return sftp.normalize(path[2:])
        if self.platform == "windows":
            return path.replace("\\", "/")
        return path

    # ------------------------------------------------------------------ #
    # public API
    # ------------------------------------------------------------------ #
    def test(self) -> dict:
        if self.platform == "windows":
            result = self._exec(self._wrap("hostname; whoami; [System.Environment]::OSVersion.VersionString"))
        else:
            result = self._exec("hostname; whoami; uname -a")
        lines = [line.strip() for line in result["stdout"].splitlines() if line.strip()]
        if not lines:
            raise ConnectorError("پاسخی از SSH دریافت نشد")
        return {
            "hostname": lines[0],
            "username": lines[1] if len(lines) > 1 else self.username,
            "os_name": lines[2] if len(lines) > 2 else ("Windows" if self.platform == "windows" else "Linux"),
            "platform": self.platform,
        }

    def run(self, command: str, timeout: int = 30) -> dict:
        return self._exec(self._wrap(command), timeout)

    def screenshot(self) -> bytes:
        if self.platform == "windows":
            script = """
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$p = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$bmp = New-Object System.Drawing.Bitmap $p.Width, $p.Height
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($p.Location, [System.Drawing.Point]::Empty, $p.Size)
$ms = New-Object System.IO.MemoryStream
$bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
[Convert]::ToBase64String($ms.ToArray())
$g.Dispose(); $bmp.Dispose(); $ms.Dispose()
"""
            result = self._exec(self._wrap(script), timeout=60)
        else:
            command = (
                'f=$(mktemp /tmp/rc_shot.XXXXXX.png); '
                'gnome-screenshot -f "$f" 2>/dev/null || scrot "$f" 2>/dev/null || import -window root "$f" 2>/dev/null; '
                'base64 "$f" 2>/dev/null | tr -d "\\n"; rm -f "$f"'
            )
            result = self._exec(command, timeout=60)
        encoded = "".join(result["stdout"].split())
        if not encoded:
            raise ConnectorError(
                "اسکرین‌شات ممکن نشد. روی لینوکس scrot یا imagemagick نصب کنید و در نشست گرافیکی اجرا کنید."
            )
        try:
            return base64.b64decode(encoded)
        except Exception as exc:  # noqa: BLE001
            raise ConnectorError(f"تصویر دریافتی معتبر نیست: {exc}") from exc

    def clipboard_get(self) -> str:
        if self.platform == "windows":
            result = self._exec(self._wrap("Get-Clipboard -Raw"))
            return result["stdout"].replace("\r\n", "\n").rstrip("\n")
        for command in ("xclip -selection clipboard -o", "xsel --clipboard --output"):
            try:
                result = self._exec(command, timeout=15)
                if result["exit_code"] == 0:
                    return result["stdout"].rstrip("\n")
            except ConnectorError:
                continue
        raise ConnectorError("xclip یا xsel روی دستگاه پیدا نشد")

    def clipboard_set(self, text: str) -> None:
        if self.platform == "windows":
            script = (
                "$t = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"
                + base64.b64encode(text.encode("utf-8")).decode("ascii")
                + "')); Set-Clipboard -Value $t"
            )
            result = self._exec(self._wrap(script), timeout=15)
            if result["exit_code"] != 0:
                raise ConnectorError(result["stderr"] or "نوشتن در کلیپ‌بورد ممکن نشد")
            return
        try:
            _stdin, stdout, stderr = self.client.exec_command("xclip -selection clipboard -i", timeout=15)
            _stdin.write(text.encode("utf-8"))
            _stdin.flush()
            _stdin.channel.shutdown_write()
            code = stdout.channel.recv_exit_status()
            if code != 0:
                raise ConnectorError(_decode(stderr.read()) or "xclip ممکن نشد")
        except Exception as exc:  # noqa: BLE001
            raise ConnectorError(f"نوشتن در کلیپ‌بورد ممکن نشد: {exc}") from exc

    def list_dir(self, path: str) -> dict:
        sftp = self._get_sftp()
        remote = self._remote_path(path)
        try:
            attributes = sftp.listdir_attr(remote)
        except FileNotFoundError as exc:
            raise ConnectorError(f"پوشه پیدا نشد: {remote}") from exc
        except Exception as exc:  # noqa: BLE001
            raise ConnectorError(f"فهرست کردن پوشه ممکن نشد: {exc}") from exc

        entries = []
        for attribute in attributes:
            if attribute.filename in (".", ".."):
                continue
            entries.append(
                {
                    "name": attribute.filename,
                    "path": remote.rstrip("/") + "/" + attribute.filename,
                    "is_dir": stat.S_ISDIR(attribute.st_mode),
                    "size": attribute.st_size,
                    "mtime": attribute.st_mtime,
                }
            )
        entries.sort(key=lambda entry: (not entry["is_dir"], entry["name"].lower()))
        return {"path": remote, "entries": entries}

    def download(self, path: str) -> tuple[bytes, dict]:
        sftp = self._get_sftp()
        remote = self._remote_path(path)
        try:
            size = sftp.stat(remote).st_size
        except Exception as exc:  # noqa: BLE001
            raise ConnectorError(f"فایل پیدا نشد: {remote}") from exc
        if size > 8 * 1024 * 1024:
            raise ConnectorError("حجم فایل بیش از ۸ مگابایت است")
        try:
            with sftp.open(remote, "rb") as file:
                data = file.read()
        except Exception as exc:  # noqa: BLE001
            raise ConnectorError(f"خواندن فایل ممکن نشد: {exc}") from exc
        return data, {
            "name": os.path.basename(remote),
            "path": remote,
            "size": size,
        }

    def upload(self, path: str, data: bytes) -> None:
        if len(data) > 8 * 1024 * 1024:
            raise ConnectorError("حجم فایل بیش از ۸ مگابایت است")
        sftp = self._get_sftp()
        remote = self._remote_path(path)
        parent = remote.rsplit("/", 1)[0] if "/" in remote else ""
        if parent:
            if self.platform == "windows":
                script = (
                    f"$p = '{parent}'; if (!(Test-Path -LiteralPath $p)) "
                    f"{{ New-Item -ItemType Directory -Force -Path $p | Out-Null }}"
                )
                self._exec(self._wrap(script), timeout=15)
            else:
                self._exec(f"mkdir -p {shlex.quote(parent)}", timeout=15)
        try:
            with sftp.open(remote, "wb") as file:
                file.write(data)
        except Exception as exc:  # noqa: BLE001
            raise ConnectorError(f"نوشتن فایل ممکن نشد: {exc}") from exc

    def power(self, action: str) -> None:
        if self.platform == "windows":
            commands = {
                "lock": "rundll32.exe user32.dll,LockWorkStation",
                "sleep": "rundll32.exe powrprof.dll,SetSuspendState 0,1,0",
                "restart": "shutdown /r /t 0",
                "shutdown": "shutdown /s /t 0",
            }
        else:
            commands = {
                "lock": "loginctl lock-session || xdg-screensaver lock",
                "sleep": "systemctl suspend",
                "restart": "systemctl reboot",
                "shutdown": "systemctl poweroff",
            }
        command = commands.get(action)
        if not command:
            raise ConnectorError("عملیات قدرت نامعتبر است")
        try:
            self._exec(self._wrap(command), timeout=20)
        except Exception:
            # The connection usually drops on reboot/shutdown — expected.
            pass

    def close(self) -> None:
        try:
            if self.sftp is not None:
                self.sftp.close()
        except Exception:
            pass
        try:
            self.client.close()
        except Exception:
            pass
