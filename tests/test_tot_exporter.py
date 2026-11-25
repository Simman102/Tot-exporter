"""Unit and integration tests for Tot-exporter."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import socket
import sys
import tempfile
import tkinter as tk
import unittest
from unittest import mock
import urllib.request

from fastapi.testclient import TestClient

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from app_icon import create_app_icon, save_ico  # noqa: E402
import autostart  # noqa: E402
from chat_logger import ChatEntry, ChatLogger  # noqa: E402
from clipboard_listener import ClipboardListener, parse_server_ip  # noqa: E402
from viewer_window import ViewerWindow  # noqa: E402
from webhook_server import PortInUseError, WebhookServer, create_app  # noqa: E402


class ParseServerIpTests(unittest.TestCase):
    def test_accepts_all_valid_port_lengths(self) -> None:
        cases = {
            "45.143.197.83:1": "45.143.197.83",
            "45.143.197.83:80": "45.143.197.83",
            "45.143.197.83:443": "45.143.197.83",
            "45.143.197.83:7777": "45.143.197.83",
            "188.64.33.126:27015": "188.64.33.126",
            "127.0.0.1:65535": "127.0.0.1",
            "  192.168.1.10:28015 \n": "192.168.1.10",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(parse_server_ip(raw), expected)

    def test_rejects_invalid_ips_and_ports(self) -> None:
        invalid = [
            None,
            "",
            "   ",
            "45.143.197.83",
            "45.143.197.83:",
            "45.143.197.83:0",
            "45.143.197.83:65536",
            "45.143.197.83:99999",
            "45.143.197.83:abc",
            "45.143.197.83:77²7",
            "999.143.197.83:7777",
            "010.0.0.1:7777",
            "example.com:7777",
            "[::1]:7777",
            "http://45.143.197.83:7777",
        ]
        for raw in invalid:
            with self.subTest(raw=raw):
                self.assertIsNone(parse_server_ip(raw))


class ChatLoggerTests(unittest.TestCase):
    def test_logs_utf8_with_and_without_server_ip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            received: list[ChatEntry] = []
            logger = ChatLogger(base_dir=Path(tmp), on_message=received.append)
            ts = datetime(2026, 11, 24, 18, 5, 9, tzinfo=timezone.utc)

            e1 = logger.log_message("Grey Robes", "Hello world!", server_ip=None, now=ts)
            e2 = logger.log_message(
                "Stacy", "Casts Mirror Image", server_ip="45.143.197.83", now=ts
            )

            self.assertEqual(e1.log_path.name, "2026-11-24.txt")
            self.assertEqual(e2.log_path.name, "2026-11-24-45.143.197.83.txt")
            self.assertEqual(
                e1.log_path.read_text(encoding="utf-8"),
                "[18:05:09] Grey Robes: Hello world!\n",
            )
            self.assertEqual(
                e2.log_path.read_text(encoding="utf-8"),
                "[18:05:09] Stacy: Casts Mirror Image\n",
            )
            self.assertEqual(len(received), 2)
            self.assertEqual(logger.total_logged, 2)
            self.assertEqual(
                ChatLogger.load_file_tail(e2.log_path),
                ["[18:05:09] Stacy: Casts Mirror Image"],
            )


class WebhookIntegrationTests(unittest.TestCase):
    def test_fastapi_decodes_url_encoded_query_params(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            entries: list[ChatEntry] = []
            logger = ChatLogger(base_dir=Path(tmp), on_message=entries.append)
            listener = ClipboardListener()
            listener.set_server_ip("188.64.33.126")
            app = create_app(logger, lambda: listener.server_ip)

            client = TestClient(app)
            resp = client.get("/message?sender=Grey%20Robes&message=Hi%20there%21%20100%25")
            self.assertEqual(resp.status_code, 200)

            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].sender, "Grey Robes")
            self.assertEqual(entries[0].message, "Hi there! 100%")
            self.assertNotIn("%20", entries[0].format_line())

            health = client.get("/health")
            self.assertEqual(health.status_code, 200)
            self.assertEqual(health.json(), {"status": "ok", "server_ip": "188.64.33.126"})

    def test_webhook_server_lifecycle_and_port_conflict(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]

        with tempfile.TemporaryDirectory() as tmp:
            logger = ChatLogger(base_dir=Path(tmp))
            server = WebhookServer(
                chat_logger=logger,
                get_server_ip=lambda: "45.143.197.83",
                host="127.0.0.1",
                port=port,
            )
            server.start()
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/message?sender=Grey%20Robes&message=Hi%21"
                ) as resp:
                    self.assertEqual(resp.status, 200)

                conflict = WebhookServer(
                    chat_logger=logger,
                    get_server_ip=lambda: None,
                    host="127.0.0.1",
                    port=port,
                )
                with self.assertRaises(PortInUseError):
                    conflict.start()
            finally:
                server.stop()

            history = logger.get_history()
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0].sender, "Grey Robes")
            self.assertEqual(history[0].message, "Hi!")


class ViewerAndIconTests(unittest.TestCase):
    def test_icon_generation_and_viewer_renders_decoded_messages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ico_path = save_ico(Path(tmp) / "icon.ico")
            self.assertTrue(ico_path.exists())
            self.assertEqual(create_app_icon(64).size, (64, 64))

            logger = ChatLogger(base_dir=Path(tmp))
            root = tk.Tk()
            try:
                viewer = ViewerWindow(
                    root=root,
                    chat_logger=logger,
                    webhook_url="http://localhost:2137/message",
                    initial_server_ip=None,
                    on_set_server_ip=lambda _ip: None,
                    on_quit=root.destroy,
                    start_hidden=True,
                )
                ts = datetime(2026, 11, 24, 20, 1, 2, tzinfo=timezone.utc)
                entry = logger.log_message("Grey Robes", "Hello from Tot!", "45.143.197.83", now=ts)
                viewer.update_server_ip("45.143.197.83")
                viewer.append_entry(entry)

                content = viewer.text.get("1.0", tk.END)
                self.assertIn("[20:01:02] Grey Robes: Hello from Tot!", content)
                self.assertIn("Server IP set to 45.143.197.83", content)
                self.assertNotIn("%20", content)
            finally:
                root.destroy()

    def test_clipboard_listener_manual_override_triggers_callback(self) -> None:
        changes: list[str | None] = []
        listener = ClipboardListener(on_change=changes.append)
        listener.set_server_ip("45.143.197.83")
        listener.set_server_ip("45.143.197.83")  # duplicate should not re-notify
        listener.set_server_ip(None)

        self.assertEqual(changes, ["45.143.197.83", None])
        self.assertIsNone(listener.server_ip)


class _FakeRegKey:
    def __init__(self, store: dict[str, tuple[object, int]]) -> None:
        self.store = store

    def __enter__(self) -> "_FakeRegKey":
        return self

    def __exit__(self, *args: object) -> None:
        return None


class _FakeWinreg:
    HKEY_CURRENT_USER = object()
    KEY_READ = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1
    REG_BINARY = 3

    def __init__(self) -> None:
        self.keys: dict[str, dict[str, tuple[object, int]]] = {}

    def OpenKey(self, root: object, sub_key: str, reserved: int, access: int) -> _FakeRegKey:
        if sub_key not in self.keys:
            raise FileNotFoundError(sub_key)
        return _FakeRegKey(self.keys[sub_key])

    def CreateKeyEx(self, root: object, sub_key: str, reserved: int, access: int) -> _FakeRegKey:
        return _FakeRegKey(self.keys.setdefault(sub_key, {}))

    def QueryValueEx(self, key: _FakeRegKey, name: str) -> tuple[object, int]:
        if name not in key.store:
            raise FileNotFoundError(name)
        return key.store[name]

    def SetValueEx(
        self, key: _FakeRegKey, name: str, reserved: int, reg_type: int, value: object
    ) -> None:
        key.store[name] = (value, reg_type)

    def DeleteValue(self, key: _FakeRegKey, name: str) -> None:
        if name not in key.store:
            raise FileNotFoundError(name)
        del key.store[name]


class AutostartTests(unittest.TestCase):
    def test_command_generation_for_frozen_and_script(self) -> None:
        expected_exe = Path(r"C:\Apps\Tot-exporter.exe").resolve()
        with mock.patch.object(sys, "frozen", True, create=True), mock.patch.object(
            sys, "executable", str(expected_exe)
        ):
            cmd = autostart.get_autostart_command()
            self.assertEqual(cmd, f'"{expected_exe}"')

        with mock.patch.object(sys, "frozen", False, create=True):
            cmd = autostart.get_autostart_command(Path("src/Tot-exporter.py"))
            self.assertIn("Tot-exporter.py", cmd)
            self.assertTrue(
                "pythonw.exe" in cmd.lower() or "pyw" in cmd.lower() or "python.exe" in cmd.lower()
            )

    def test_enable_disable_task_manager_approval_and_sync(self) -> None:
        fake = _FakeWinreg()
        with mock.patch.object(autostart, "winreg", fake), mock.patch.object(
            autostart, "is_supported", return_value=True
        ), mock.patch.object(
            autostart, "get_autostart_command", return_value=r'"C:\New\Tot-exporter.exe"'
        ):
            self.assertFalse(autostart.is_autostart_enabled())

            # Enable autostart
            self.assertTrue(autostart.set_autostart(True, command=r'"C:\Old\Tot-exporter.exe"'))
            self.assertTrue(autostart.is_autostart_enabled())

            # Simulate moving the executable -> sync updates stored path
            self.assertTrue(autostart.sync_autostart_path())
            self.assertEqual(
                fake.keys[autostart.RUN_KEY_PATH][autostart.APP_NAME][0],
                r'"C:\New\Tot-exporter.exe"',
            )
            self.assertFalse(autostart.sync_autostart_path())

            # Simulate user disabling entry in Task Manager (0x03 byte)
            fake.keys[autostart.APPROVED_KEY_PATH] = {
                autostart.APP_NAME: (b"\x03" + b"\x00" * 11, _FakeWinreg.REG_BINARY)
            }
            self.assertFalse(autostart.is_autostart_enabled())

            # Re-enabling from the app clears the disabled Task Manager approval
            self.assertTrue(autostart.set_autostart(True))
            self.assertTrue(autostart.is_autostart_enabled())

            # Disabling removes the Run value
            self.assertFalse(autostart.set_autostart(False))
            self.assertFalse(autostart.is_autostart_enabled())


if __name__ == "__main__":
    unittest.main()
