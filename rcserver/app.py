"""Flask app: the local control panel + a small JSON API.

The controller connects *to* the target. Nothing runs on the target except
its built-in remote-access service (WinRM / OpenSSH / ADB).
"""

from __future__ import annotations

import base64
import json
import shutil
import time

from flask import Flask, jsonify, request, send_from_directory

from . import config, platforms, store
from .connectors import adb
from .connectors.base import Connector, ConnectorError, Unsupported
from .connectors.factory import create_connector

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = config.MAX_UPLOAD_BYTES
app.json.ensure_ascii = False


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def error(message: str, status: int = 400):
    return jsonify({"error": message}), status


@app.after_request
def _no_store(response):
    response.headers["Cache-Control"] = "no-store"
    return response


@app.errorhandler(404)
def _not_found(_):
    return error("یافت نشد", 404)


@app.errorhandler(413)
def _too_large(_):
    return error("حجم درخواست بیش از حد مجاز است (حداکثر ۸ مگابایت)", 413)


@app.errorhandler(500)
def _server_error(_):
    return error("خطای داخلی سرور", 500)


def connector_for(target_id: str) -> tuple[Connector | None, tuple | None]:
    row = store.get_target(target_id)
    if not row:
        return None, error("دستگاه یافت نشد", 404)
    try:
        connector = create_connector(dict(row), store.get_secret(row))
    except ConnectorError as exc:
        return None, error(str(exc), 400)
    return connector, None


def target_or_404(target_id: str):
    row = store.get_target(target_id)
    if not row:
        return None, error("دستگاه یافت نشد", 404)
    return row, None


# --------------------------------------------------------------------------- #
# static UI
# --------------------------------------------------------------------------- #
@app.get("/")
def index():
    return send_from_directory(config.WEB_DIR, "index.html")


@app.get("/<path:filename>")
def static_files(filename: str):
    return send_from_directory(config.WEB_DIR, filename)


# --------------------------------------------------------------------------- #
# meta
# --------------------------------------------------------------------------- #
@app.get("/api/health")
def health():
    return jsonify({"ok": True, "service": "remote-control", "version": config.VERSION})


@app.get("/api/server")
def server_info():
    return jsonify(
        {
            "version": config.VERSION,
            "adb_available": bool(shutil.which("adb")),
            "quick_commands": platforms.QUICK_COMMANDS,
            "default_paths": platforms.DEFAULT_PATHS,
        }
    )


@app.get("/api/events")
def events():
    limit = request.args.get("limit", default=100, type=int)
    return jsonify({"events": store.list_events(limit)})


@app.post("/api/discover")
def discover():
    """Scan the local network for WinRM / SSH / ADB devices (no IP needed)."""
    from . import discovery

    try:
        result = discovery.scan_network()
        return jsonify(result)
    except Exception as exc:  # noqa: BLE001
        return error(f"جستجوی شبکه ممکن نشد: {exc}", 500)


# --------------------------------------------------------------------------- #
# targets
# --------------------------------------------------------------------------- #
@app.get("/api/targets")
def list_targets():
    return jsonify({"targets": store.list_targets()})


@app.post("/api/targets")
def create_target():
    body = request.get_json(silent=True) or {}
    try:
        target = store.create_target(body)
    except ValueError as exc:
        return error(str(exc), 400)
    return jsonify({"target": target})


@app.delete("/api/targets/<target_id>")
def delete_target(target_id: str):
    if not store.delete_target(target_id):
        return error("دستگاه یافت نشد", 404)
    return jsonify({"ok": True})


@app.post("/api/targets/<target_id>/test")
def test_target(target_id: str):
    connector, err = connector_for(target_id)
    if err:
        return err
    try:
        with connector:
            info = connector.test()
        store.set_test_result(target_id, "online", info.get("os_name"), None)
        store.log_event(target_id, "info", f"connection test succeeded: {info.get('os_name')}")
        return jsonify({"ok": True, **info})
    except ConnectorError as exc:
        store.set_test_result(target_id, "offline", None, str(exc))
        store.log_event(target_id, "error", f"connection test failed: {exc}")
        return error(str(exc), 400)
    except Exception as exc:  # noqa: BLE001
        message = f"خطای غیرمنتظره: {exc}"
        store.set_test_result(target_id, "offline", None, message)
        return error(message, 500)


@app.post("/api/targets/test")
def test_new_target():
    """Test a configuration before saving it (no credentials are stored)."""
    body = request.get_json(silent=True) or {}
    kind = str(body.get("kind") or "")

    if kind == "adb":
        address = str(body.get("host") or "").strip()
        if not address:
            return error("آدرس دستگاه (IP:port) لازم است")
        try:
            if body.get("pair_address") and body.get("pair_code"):
                adb.adb_pair(str(body["pair_address"]).strip(), str(body["pair_code"]).strip())
            adb.adb_connect(address)
            connector = adb.ADBConnector({"kind": "adb", "host": address})
        except ConnectorError as exc:
            return error(str(exc), 400)
    else:
        target = {
            "kind": kind,
            "host": body.get("host"),
            "port": body.get("port"),
            "username": body.get("username"),
            "domain": body.get("domain"),
            "key_path": body.get("key_path"),
            "platform": body.get("platform"),
            "use_https": body.get("use_https"),
            "insecure_tls": body.get("insecure_tls"),
        }
        try:
            connector = create_connector(target, str(body.get("password") or ""))
        except ConnectorError as exc:
            return error(str(exc), 400)

    try:
        with connector:
            info = connector.test()
        return jsonify({"ok": True, **info})
    except ConnectorError as exc:
        return error(str(exc), 400)
    except Exception as exc:  # noqa: BLE001
        return error(f"خطای غیرمنتظره: {exc}", 500)


@app.post("/api/adb/pair")
def pair_adb():
    body = request.get_json(silent=True) or {}
    address = str(body.get("address") or "").strip()
    code = str(body.get("code") or "").strip()
    if not address or not code:
        return error("آدرس جفت‌سازی و کد لازم است")
    try:
        output = adb.adb_pair(address, code)
        return jsonify({"ok": True, "output": output})
    except ConnectorError as exc:
        return error(str(exc), 400)


# --------------------------------------------------------------------------- #
# operations
# --------------------------------------------------------------------------- #
@app.get("/api/targets/<target_id>/commands")
def target_commands(target_id: str):
    row, err = target_or_404(target_id)
    if err:
        return err
    limit = request.args.get("limit", default=30, type=int)
    return jsonify(
        {
            "commands": store.list_commands(target_id, limit),
            "stats": store.command_stats(target_id),
        }
    )


@app.post("/api/targets/<target_id>/exec")
def exec_command(target_id: str):
    connector, err = connector_for(target_id)
    if err:
        return err
    body = request.get_json(silent=True) or {}
    command = str(body.get("command") or "").strip()
    if not command:
        return error("دستور خالی است")
    timeout = min(
        int(body.get("timeout") or config.DEFAULT_COMMAND_TIMEOUT), config.MAX_COMMAND_TIMEOUT
    )
    started = time.time()
    try:
        with connector:
            result = connector.run(command, timeout)
        duration = int((time.time() - started) * 1000)
        output = result.get("stdout") or ""
        stderr = result.get("stderr") or ""
        if stderr:
            output += ("\n" if output and not output.endswith("\n") else "") + "[stderr]\n" + stderr
        store.log_command(
            target_id, "shell", command, output, result.get("exit_code"), None, duration
        )
        return jsonify(
            {
                "exit_code": result.get("exit_code"),
                "stdout": result.get("stdout") or "",
                "stderr": stderr,
                "duration_ms": duration,
            }
        )
    except ConnectorError as exc:
        store.log_command(
            target_id, "shell", command, "", -1, str(exc), int((time.time() - started) * 1000)
        )
        return error(str(exc), 400)
    except Exception as exc:  # noqa: BLE001
        store.log_command(
            target_id, "shell", command, "", -1, str(exc), int((time.time() - started) * 1000)
        )
        return error(f"خطای غیرمنتظره: {exc}", 500)


@app.post("/api/targets/<target_id>/screenshot")
def screenshot(target_id: str):
    connector, err = connector_for(target_id)
    if err:
        return err
    started = time.time()
    try:
        with connector:
            data = connector.screenshot()
        encoded = base64.b64encode(data).decode("ascii")
        store.log_command(
            target_id,
            "screenshot",
            "screenshot",
            f"captured {len(data):,} bytes",
            0,
            None,
            int((time.time() - started) * 1000),
        )
        return jsonify({"image_base64": encoded})
    except Unsupported as exc:
        store.log_command(target_id, "screenshot", "screenshot", "", -1, str(exc), 0)
        return error(str(exc), 400)
    except ConnectorError as exc:
        store.log_command(target_id, "screenshot", "screenshot", "", -1, str(exc), 0)
        return error(str(exc), 400)


@app.post("/api/targets/<target_id>/clipboard")
def clipboard(target_id: str):
    connector, err = connector_for(target_id)
    if err:
        return err
    body = request.get_json(silent=True) or {}
    action = str(body.get("action") or "get")
    started = time.time()
    try:
        with connector:
            if action == "set":
                text = str(body.get("text") or "")
                connector.clipboard_set(text)
                store.log_command(
                    target_id, "clipboard_set", text[:200], "ok", 0, None,
                    int((time.time() - started) * 1000),
                )
                return jsonify({"ok": True})
            text = connector.clipboard_get()
            store.log_command(
                target_id, "clipboard_get", "", text, 0, None,
                int((time.time() - started) * 1000),
            )
            return jsonify({"text": text})
    except Unsupported as exc:
        store.log_command(target_id, f"clipboard_{action}", "", "", -1, str(exc), 0)
        return error(str(exc), 400)
    except ConnectorError as exc:
        store.log_command(target_id, f"clipboard_{action}", "", "", -1, str(exc), 0)
        return error(str(exc), 400)


@app.post("/api/targets/<target_id>/files")
def files(target_id: str):
    connector, err = connector_for(target_id)
    if err:
        return err
    body = request.get_json(silent=True) or {}
    action = str(body.get("action") or "list")
    path = str(body.get("path") or "")
    started = time.time()
    try:
        with connector:
            if action == "download":
                data, info = connector.download(path)
                encoded = base64.b64encode(data).decode("ascii")
                store.log_command(
                    target_id, "download", path, json.dumps(info, ensure_ascii=False), 0, None,
                    int((time.time() - started) * 1000),
                )
                return jsonify({"content_base64": encoded, **info})
            if action == "upload":
                content = str(body.get("content_b64") or "")
                if len(content) > config.MAX_FILE_BYTES * 1.4:
                    return error("حجم فایل بیش از ۸ مگابایت است", 413)
                data = base64.b64decode(content, validate=True)
                connector.upload(path, data)
                store.log_command(
                    target_id, "upload", path, f"uploaded {len(data):,} bytes", 0, None,
                    int((time.time() - started) * 1000),
                )
                return jsonify({"ok": True, "path": path})
            result = connector.list_dir(path)
            store.log_command(
                target_id, "list_dir", path, json.dumps(result, ensure_ascii=False), 0, None,
                int((time.time() - started) * 1000),
            )
            return jsonify(result)
    except Unsupported as exc:
        store.log_command(target_id, action, path, "", -1, str(exc), 0)
        return error(str(exc), 400)
    except ConnectorError as exc:
        store.log_command(target_id, action, path, "", -1, str(exc), 0)
        return error(str(exc), 400)


@app.post("/api/targets/<target_id>/power")
def power(target_id: str):
    connector, err = connector_for(target_id)
    if err:
        return err
    body = request.get_json(silent=True) or {}
    action = str(body.get("action") or "")
    started = time.time()
    try:
        with connector:
            connector.power(action)
        store.log_command(
            target_id, "power", action, "sent", 0, None, int((time.time() - started) * 1000)
        )
        return jsonify({"ok": True})
    except Unsupported as exc:
        store.log_command(target_id, "power", action, "", -1, str(exc), 0)
        return error(str(exc), 400)
    except ConnectorError as exc:
        store.log_command(target_id, "power", action, "", -1, str(exc), 0)
        return error(str(exc), 400)


@app.post("/api/targets/<target_id>/android")
def android(target_id: str):
    connector, err = connector_for(target_id)
    if err:
        return err
    body = request.get_json(silent=True) or {}
    action = str(body.get("action") or "")
    started = time.time()
    try:
        with connector:
            if action == "tap":
                connector.android_tap(float(body.get("x") or 0), float(body.get("y") or 0))
                output = "tap"
            elif action == "swipe":
                connector.android_swipe(
                    float(body.get("x1") or 0),
                    float(body.get("y1") or 0),
                    float(body.get("x2") or 0),
                    float(body.get("y2") or 0),
                )
                output = "swipe"
            elif action == "text":
                text = str(body.get("text") or "")
                connector.android_text(text)
                output = f"typed {len(text):,} chars"
            elif action == "apps":
                output = connector.list_apps()
            elif action == "launch":
                package = str(body.get("package") or "").strip()
                if not package:
                    return error("نام پکیج لازم است")
                connector.launch_app(package)
                output = f"launched {package}"
            else:
                return error("عملیات اندروید نامعتبر است")
        store.log_command(
            target_id, f"android_{action}", str(body)[:200], output, 0, None,
            int((time.time() - started) * 1000),
        )
        return jsonify({"ok": True, "output": output})
    except Unsupported as exc:
        store.log_command(target_id, f"android_{action}", "", "", -1, str(exc), 0)
        return error(str(exc), 400)
    except ConnectorError as exc:
        store.log_command(target_id, f"android_{action}", "", "", -1, str(exc), 0)
        return error(str(exc), 400)


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #
def create_app() -> Flask:
    store.init_db()
    return app


if __name__ == "__main__":
    create_app().run(host=config.HOST, port=config.PORT, threaded=True, use_reloader=False)
