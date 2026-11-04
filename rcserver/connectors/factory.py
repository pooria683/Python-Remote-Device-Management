"""Create a connector for a target."""

from __future__ import annotations

from .base import Connector, ConnectorError


def create_connector(target: dict, password: str | None = None) -> Connector:
    kind = str(target.get("kind") or "")

    if password is None:
        from .. import crypto

        password = crypto.decrypt(target.get("password_enc") or "")

    if kind == "winrm":
        from .winrm import WinRMConnector

        return WinRMConnector(target, password)

    if kind == "ssh":
        from .ssh import SSHConnector

        return SSHConnector(target, password)

    if kind == "adb":
        from .adb import ADBConnector

        return ADBConnector(target)

    raise ConnectorError(f"نوع اتصال نامعتبر است: {kind}")
