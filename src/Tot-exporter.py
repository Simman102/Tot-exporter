"""Tot-exporter: Windows system tray app & webhook receiver for Tot! chat logs."""

from __future__ import annotations

import argparse
import ctypes
import multiprocessing
import os
import queue
import sys
import tkinter as tk
from tkinter import messagebox

import pystray

from app_icon import create_app_icon
import autostart
from chat_logger import ChatEntry, ChatLogger, get_app_dir
from clipboard_listener import ClipboardListener
from viewer_window import ViewerWindow
from webhook_server import PortInUseError, WebhookServer

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 2137
WEBHOOK_DISPLAY_URL = f"http://localhost:{DEFAULT_PORT}/message"


def _enable_dpi_awareness() -> None:
    """Enable sharp DPI scaling for Tkinter on Windows."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


class TotExporterApp:
    """Coordinates the webhook server, clipboard listener, tray icon, and viewer UI."""

    def __init__(self, start_open: bool = False) -> None:
        self._events: queue.SimpleQueue[tuple[str, object]] = queue.SimpleQueue()
        self._shutting_down = False
        self._start_open = start_open
        self._autostart_enabled = autostart.is_autostart_enabled()

        self.clipboard_listener = ClipboardListener(
            on_change=lambda ip: self._events.put(("server_ip", ip))
        )
        self.chat_logger = ChatLogger(
            base_dir=get_app_dir(),
            on_message=lambda entry: self._events.put(("message", entry)),
        )
        self.server = WebhookServer(
            chat_logger=self.chat_logger,
            get_server_ip=lambda: self.clipboard_listener.server_ip,
            host=DEFAULT_HOST,
            port=DEFAULT_PORT,
        )
        self.tray_icon: pystray.Icon | None = None
        self.root: tk.Tk | None = None
        self.viewer: ViewerWindow | None = None

    def _tray_title(self) -> str:
        ip = self.clipboard_listener.server_ip
        suffix = f" | Server: {ip}" if ip else " | No server IP"
        return f"Tot-exporter (:{DEFAULT_PORT}){suffix}"

    def _build_tray_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem(
                "Open Viewer",
                lambda icon, item: self._events.put(("show_viewer", None)),
                default=True,
            ),
            pystray.MenuItem(
                "Copy Webhook URL",
                lambda icon, item: self._events.put(("copy_url", None)),
            ),
            pystray.MenuItem(
                "Open Logs Folder",
                lambda icon, item: self._open_logs_folder(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                lambda item: f"Server IP: {self.clipboard_listener.server_ip or 'None'}",
                None,
                enabled=False,
            ),
            pystray.MenuItem(
                "Clear Server IP",
                lambda icon, item: self.clipboard_listener.set_server_ip(None),
                enabled=lambda item: self.clipboard_listener.server_ip is not None,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Start with Windows",
                lambda icon, item: self._events.put(("set_autostart", not self._autostart_enabled)),
                checked=lambda item: self._autostart_enabled,
                enabled=autostart.is_supported(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Exit",
                lambda icon, item: self._events.put(("quit", None)),
            ),
        )

    def _open_logs_folder(self) -> None:
        if sys.platform == "win32":
            os.startfile(str(self.chat_logger.base_dir))  # type: ignore[attr-defined]

    def _refresh_tray(self) -> None:
        if self.tray_icon is None:
            return
        self.tray_icon.title = self._tray_title()
        try:
            self.tray_icon.update_menu()
        except Exception:
            pass

    def _apply_autostart(self, enabled: bool) -> None:
        try:
            self._autostart_enabled = autostart.set_autostart(enabled)
        except OSError as exc:
            self._autostart_enabled = autostart.is_autostart_enabled()
            messagebox.showerror(
                "Autostart Error",
                f"Could not update Windows startup setting:\n{exc}",
                parent=self.root,
            )
        if self.viewer is not None:
            self.viewer.set_autostart_state(self._autostart_enabled)
        self._refresh_tray()

    def _poll_events(self) -> None:
        if self._shutting_down or self.root is None or self.viewer is None:
            return

        while True:
            try:
                kind, payload = self._events.get_nowait()
            except queue.Empty:
                break

            if kind == "message" and isinstance(payload, ChatEntry):
                self.viewer.append_entry(payload)
            elif kind == "server_ip":
                ip = payload if isinstance(payload, str) or payload is None else None
                self.viewer.update_server_ip(ip)
                self._refresh_tray()
            elif kind == "show_viewer":
                self.viewer.show()
            elif kind == "copy_url":
                self.root.clipboard_clear()
                self.root.clipboard_append(WEBHOOK_DISPLAY_URL)
                self.root.update_idletasks()
            elif kind == "set_autostart":
                self._apply_autostart(bool(payload))
            elif kind == "quit":
                self.shutdown()
                return

        if not self._shutting_down:
            self.root.after(100, self._poll_events)

    def shutdown(self) -> None:
        """Stop all background services and close the GUI cleanly."""
        if self._shutting_down:
            return
        self._shutting_down = True

        if self.tray_icon is not None:
            try:
                self.tray_icon.stop()
            except Exception:
                pass

        self.server.stop()
        self.clipboard_listener.stop()

        if self.root is not None:
            try:
                self.root.quit()
                self.root.destroy()
            except tk.TclError:
                pass

    def run(self) -> int:
        """Start the server, clipboard listener, tray icon, and Tkinter mainloop."""
        _enable_dpi_awareness()
        self.root = tk.Tk()

        try:
            self.server.start()
        except PortInUseError:
            self.root.withdraw()
            messagebox.showerror(
                "Tot-exporter Already Running",
                f"Port {DEFAULT_PORT} is already in use.\n\n"
                "Check your Windows system tray — Tot-exporter may already be running.",
            )
            self.root.destroy()
            return 1
        except Exception as exc:
            self.root.withdraw()
            messagebox.showerror(
                "Tot-exporter Startup Error",
                f"Failed to start webhook server on port {DEFAULT_PORT}:\n{exc}",
            )
            self.root.destroy()
            return 1

        try:
            autostart.sync_autostart_path()
            self._autostart_enabled = autostart.is_autostart_enabled()
        except OSError:
            pass

        self.clipboard_listener.listen()

        self.viewer = ViewerWindow(
            root=self.root,
            chat_logger=self.chat_logger,
            webhook_url=WEBHOOK_DISPLAY_URL,
            initial_server_ip=self.clipboard_listener.server_ip,
            on_set_server_ip=self.clipboard_listener.set_server_ip,
            on_quit=self.shutdown,
            autostart_enabled=self._autostart_enabled,
            on_toggle_autostart=self._apply_autostart if autostart.is_supported() else None,
            start_hidden=not self._start_open,
        )

        self.tray_icon = pystray.Icon(
            name="tot-exporter",
            icon=create_app_icon(64, active=True),
            title=self._tray_title(),
            menu=self._build_tray_menu(),
        )
        self.tray_icon.run_detached()

        if not self._start_open:
            try:
                self.tray_icon.notify(
                    f"Listening on {WEBHOOK_DISPLAY_URL}. Click the tray icon to open the live viewer.",
                    "Tot-exporter is running",
                )
            except Exception:
                pass

        self.root.after(100, self._poll_events)
        try:
            self.root.mainloop()
        finally:
            self.shutdown()
        return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Tot! local chat webhook exporter")
    parser.add_argument(
        "--open",
        action="store_true",
        help="Open the live chat viewer window immediately on startup.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    args = parse_args()
    raise SystemExit(TotExporterApp(start_open=args.open).run())
