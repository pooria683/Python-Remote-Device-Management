"""WinRM connector — uses the built-in Windows Remote Management service.

Nothing is installed on the target. It only needs:
    Enable-PSRemoting -Force
(once, in an elevated PowerShell). File transfer uses SMB (built-in file
sharing) when available, with a small WinRM fallback.
"""

from __future__ import annotations

import base64
import json
import os
import tempfile

import winrm

from .base import Connector, ConnectorError, Unsupported

SMB_FALLBACK_LIMIT = 16 * 1024


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _decode(data: bytes) -> str:
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def smb_split(path: str) -> tuple[str, str]:
    """Split a Windows path into (SMB share, path within the share)."""
    path = (path or "").strip().replace("/", "\\")
    if len(path) >= 2 and path[1] == ":":
        return f"{path[0].upper()}$", path[2:].lstrip("\\")
    if path.startswith("\\\\"):
        parts = path[2:].split("\\", 2)
        if len(parts) >= 2:
            return parts[1], parts[2]
    raise ConnectorError(f"مسیر SMB پشتیبانی نمی‌شود: {path}")


class WinRMConnector(Connector):
    kind = "winrm"
    platform = "windows"

    def __init__(self, target: dict, password: str):
        self.target = target
        self.password = password
        self.host = str(target.get("host") or "").strip()
        if not self.host:
            raise ConnectorError("آدرس دستگاه لازم است")

        use_https = bool(target.get("use_https"))
        port = int(target.get("port") or (5986 if use_https else 5985))
        endpoint = f"{'https' if use_https else 'http'}://{self.host}:{port}"

        username = str(target.get("username") or "").strip()
        domain = str(target.get("domain") or "").strip()
        if not username:
            raise ConnectorError("نام کاربری لازم است")
        if not domain and "\\" in username:
            domain, username = username.split("\\", 1)

        try:
            self.session = winrm.Session(
                endpoint,
                auth=(username, password),
                server_cert_validation="ignore" if (use_https and target.get("insecure_tls")) else "validate",
                operation_timeout_sec=60,
                read_timeout_sec=120,
            )
        except Exception as exc:  # noqa: BLE001
            raise ConnectorError(f"ساخت نشست WinRM ممکن نشد: {exc}") from exc

        self._smb = None
        self._smb_error = ""

    # ------------------------------------------------------------------ #
    # low level
    # ------------------------------------------------------------------ #
    def _ps(self, script: str) -> dict:
        try:
            result = self.session.run_ps(script)
        except Exception as exc:  # noqa: BLE001
            raise ConnectorError(
                f"اجرای دستور WinRM ممکن نشد: {exc}\n"
                "مطمئن شوید سرویس WinRM فعال است (Enable-PSRemoting -Force) و پورت 5985/5986 باز است."
            ) from exc
        return {
            "exit_code": result.status_code,
            "stdout": _decode(result.std_out or b""),
            "stderr": _decode(result.std_err or b""),
        }

    def _lines(self, output: str) -> list[str]:
        return [line.strip() for line in (output or "").splitlines() if line.strip()]

    # ------------------------------------------------------------------ #
    # SMB (file transfer)
    # ------------------------------------------------------------------ #
    def _smb_connection(self):
        if self._smb is not None:
            return self._smb
        try:
            from smb.SMBConnection import SMBConnection
        except ImportError as exc:
            self._smb_error = "کتابخانه pysmb نصب نیست"
            raise ConnectorError("برای انتقال فایل، pysmb لازم است (pip install pysmb)") from exc

        username = str(self.target.get("username") or "")
        domain = str(self.target.get("domain") or "")
        if not domain and "\\" in username:
            domain, username = username.split("\\", 1)

        connection = SMBConnection(
            username,
            self.password,
            "rc-controller",
            self.host,
            domain=domain,
            use_ntlm_v2=True,
            is_direct_tcp=True,
        )
        try:
            connection.connect(self.host, 445, timeout=15)
        except Exception:
            connection = SMBConnection(
                username,
                self.password,
                "rc-controller",
                self.host,
                domain=domain,
                use_ntlm_v2=True,
                is_direct_tcp=False,
            )
            connection.connect(self.host, 139, timeout=15)
        self._smb = connection
        self._smb_error = ""
        return connection

    def _smb_split(self, path: str) -> tuple[str, str]:
        return smb_split(path)

    def _smb_join(self, share: str, rest: str, name: str) -> str:
        if share.endswith("$") and len(share) == 2:
            base = f"{share[0]}:"
            if rest:
                base += "\\" + rest
            return base + "\\" + name
        base = f"\\\\{self.host}\\{share}"
        if rest:
            base += "\\" + rest
        return base + "\\" + name

    # ------------------------------------------------------------------ #
    # public API
    # ------------------------------------------------------------------ #
    def test(self) -> dict:
        info = self._ps(
            "hostname; whoami; [System.Environment]::OSVersion.VersionString; "
            "$PSVersionTable.PSVersion.ToString()"
        )
        lines = self._lines(info["stdout"])
        if not lines:
            raise ConnectorError(
                "پاسخی از WinRM دریافت نشد. مطمئن شوید سرویس WinRM فعال است: "
                "Enable-PSRemoting -Force"
            )
        smb_ok = False
        try:
            self._smb_connection()
            smb_ok = True
        except Exception as exc:  # noqa: BLE001
            self._smb_error = str(exc)
        return {
            "hostname": lines[0],
            "username": lines[1] if len(lines) > 1 else "",
            "os_name": lines[2] if len(lines) > 2 else "Windows",
            "platform": "windows",
            "smb": smb_ok,
            "smb_error": "" if smb_ok else self._smb_error,
        }

    def run(self, command: str, timeout: int = 30) -> dict:
        return self._ps(command)

    def screenshot(self) -> bytes:
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
        result = self._ps(script)
        encoded = "".join(self._lines(result["stdout"]))
        if not encoded:
            raise ConnectorError(
                "اسکرین‌شات ممکن نشد. مطمئن شوید در یک نشست دسکتاپ لاگین هستید."
            )
        try:
            return base64.b64decode(encoded)
        except Exception as exc:  # noqa: BLE001
            raise ConnectorError(f"تصویر دریافتی معتبر نیست: {exc}") from exc

    def clipboard_get(self) -> str:
        result = self._ps("Get-Clipboard -Raw")
        return result["stdout"].replace("\r\n", "\n").rstrip("\n")

    def clipboard_set(self, text: str) -> None:
        script = (
            "$t = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"
            + _b64(text)
            + "')); Set-Clipboard -Value $t"
        )
        result = self._ps(script)
        if result["exit_code"] != 0:
            raise ConnectorError(result["stderr"] or "نوشتن در کلیپ‌بورد ممکن نشد")

    def list_dir(self, path: str) -> dict:
        target = path or "C:\\"
        try:
            connection = self._smb_connection()
            share, rest = self._smb_split(target)
            entries = []
            for item in connection.listPath(share, rest):
                if item.filename in (".", ".."):
                    continue
                entries.append(
                    {
                        "name": item.filename,
                        "path": self._smb_join(share, rest, item.filename),
                        "is_dir": bool(item.isDirectory),
                        "size": item.file_size,
                        "mtime": item.last_write_time,
                    }
                )
            entries.sort(key=lambda entry: (not entry["is_dir"], entry["name"].lower()))
            return {"path": target, "entries": entries}
        except ConnectorError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._smb_error = str(exc)
            # WinRM fallback for listing (output is small).
            script = (
                "$p = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"
                + _b64(target)
                + "'))"
                "if (!(Test-Path -LiteralPath $p)) { throw 'not found' }"
                "@(Get-ChildItem -LiteralPath $p | ForEach-Object {"
                "[pscustomobject]@{ name=$_.Name; path=$_.FullName; is_dir=$_.PSIsContainer;"
                "size=$_.Length; mtime=$_.LastWriteTimeUtc.ToString('o') } })"
                "| ConvertTo-Json -Compress"
            )
            result = self._ps(script)
            output = result["stdout"].strip()
            if not output:
                return {"path": target, "entries": []}
            try:
                data = json.loads(output)
            except ValueError as exc:
                raise ConnectorError(f"پاسخ فهرست نامعتبر است: {exc}") from exc
            if isinstance(data, dict):
                data = [data]
            return {"path": target, "entries": data}

    def download(self, path: str) -> tuple[bytes, dict]:
        try:
            connection = self._smb_connection()
            share, rest = self._smb_split(path)
            handle, temp_path = tempfile.mkstemp()
            os.close(handle)
            try:
                connection.retrieveFile(share, rest, temp_path)
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
                "name": os.path.basename(path.replace("\\", "/")),
                "path": path,
                "size": size,
            }
        except ConnectorError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._smb_error = str(exc)
            raise ConnectorError(
                f"دانلود از طریق SMB ممکن نشد ({exc}). "
                "فایل‌اشتراک‌گذاری ویندوز را فعال کنید یا از SSH استفاده کنید."
            ) from exc

    def upload(self, path: str, data: bytes) -> None:
        if len(data) > 8 * 1024 * 1024:
            raise ConnectorError("حجم فایل بیش از ۸ مگابایت است")
        try:
            connection = self._smb_connection()
            share, rest = self._smb_split(path)
            parent = rest.rsplit("\\", 1)[0] if "\\" in rest else ""
            if parent:
                try:
                    connection.createDirectory(share, parent)
                except Exception:
                    pass
            handle, temp_path = tempfile.mkstemp()
            os.close(handle)
            try:
                with open(temp_path, "wb") as file:
                    file.write(data)
                with open(temp_path, "rb") as file:
                    connection.storeFile(share, rest, file)
            finally:
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
        except ConnectorError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._smb_error = str(exc)
            if len(data) <= SMB_FALLBACK_LIMIT:
                script = (
                    "$p = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"
                    + _b64(path)
                    + "'))"
                    "$b = [Convert]::FromBase64String('"
                    + base64.b64encode(data).decode("ascii")
                    + "')"
                    "$dir = Split-Path -Parent $p"
                    "if ($dir -and !(Test-Path -LiteralPath $dir)) {"
                    "New-Item -ItemType Directory -Force -Path $dir | Out-Null }"
                    "[IO.File]::WriteAllBytes($p, $b)"
                )
                result = self._ps(script)
                if result["exit_code"] == 0:
                    return
                raise ConnectorError(result["stderr"] or "آپلود ممکن نشد")
            raise ConnectorError(
                f"آپلود از طریق SMB ممکن نشد ({exc}). "
                "فایل‌اشتراک‌گذاری ویندوز را فعال کنید یا از SSH استفاده کنید."
            ) from exc

    def power(self, action: str) -> None:
        commands = {
            "lock": "rundll32.exe user32.dll,LockWorkStation",
            "sleep": "rundll32.exe powrprof.dll,SetSuspendState 0,1,0",
            "restart": "shutdown /r /t 0",
            "shutdown": "shutdown /s /t 0",
        }
        command = commands.get(action)
        if not command:
            raise ConnectorError("عملیات قدرت نامعتبر است")
        try:
            self._ps(command)
        except Exception:
            # The connection usually drops on shutdown/restart — that is expected.
            pass

    def close(self) -> None:
        if self._smb is not None:
            try:
                self._smb.close()
            except Exception:
                pass
            self._smb = None
