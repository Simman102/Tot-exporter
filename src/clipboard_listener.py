"""Clipboard monitoring and server IP/port parsing for Tot-exporter."""

from __future__ import annotations

import ipaddress
import sys
import threading
import time
from typing import Callable


def parse_server_ip(text: str | None) -> str | None:
    """Extract a validated IPv4 server address from an ``ip:port`` string.

    Supports IPv4 (``1.2.3.4:port``) across all valid port numbers (1-65535).
    Returns the normalized IPv4 string, or ``None`` if the input is not a
    valid ``ip:port`` pair.
    """
    if not isinstance(text, str):
        return None

    cleaned = text.strip()
    if not cleaned:
        return None

    host_part, sep, port_part = cleaned.rpartition(":")
    if sep != ":" or not host_part:
        return None

    if not (port_part.isascii() and port_part.isdigit()):
        return None

    port = int(port_part)
    if not (1 <= port <= 65535):
        return None

    try:
        ip_obj = ipaddress.IPv4Address(host_part)
    except ValueError:
        return None

    return str(ip_obj)


if sys.platform == "win32":
    import ctypes
    import pywintypes
    import win32api
    import win32clipboard
    import win32con
    import win32gui

    class ClipboardListener:
        """Listen for Windows clipboard updates to detect the active server IP."""

        WM_CLIPBOARDUPDATE = 0x031D

        def __init__(self, on_change: Callable[[str | None], None] | None = None) -> None:
            self._lock = threading.Lock()
            self._server_ip: str | None = None
            self._on_change = on_change
            self._hwnd: int | None = None
            self._thread: threading.Thread | None = None
            self._stop_event = threading.Event()

        @property
        def server_ip(self) -> str | None:
            """Return the currently detected or manually set server IP."""
            with self._lock:
                return self._server_ip

        @property
        def SERVER_IP(self) -> str | None:
            """Backward-compatible alias for :attr:`server_ip`."""
            return self.server_ip

        def set_server_ip(self, ip: str | None) -> None:
            """Update the active server IP and notify any registered listener."""
            changed = False
            with self._lock:
                if self._server_ip != ip:
                    self._server_ip = ip
                    changed = True
            if changed and self._on_change is not None:
                self._on_change(ip)

        def _read_clipboard(self) -> str | None:
            """Read plain or Unicode text from the Windows clipboard safely."""
            for attempt in range(3):
                opened = False
                try:
                    win32clipboard.OpenClipboard()
                    opened = True
                    if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                        data = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
                        return data if isinstance(data, str) else None
                    if win32clipboard.IsClipboardFormatAvailable(win32con.CF_TEXT):
                        raw = win32clipboard.GetClipboardData(win32con.CF_TEXT)
                        if isinstance(raw, bytes):
                            return raw.decode("utf-8", errors="replace")
                        return raw if isinstance(raw, str) else None
                    return None
                except pywintypes.error:
                    if attempt < 2:
                        time.sleep(0.05)
                    else:
                        return None
                finally:
                    if opened:
                        try:
                            win32clipboard.CloseClipboard()
                        except pywintypes.error:
                            pass
            return None

        def _check_clipboard_once(self) -> None:
            content = self._read_clipboard()
            parsed = parse_server_ip(content)
            if parsed is not None:
                self.set_server_ip(parsed)

        def _process_message(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
            if msg == self.WM_CLIPBOARDUPDATE:
                time.sleep(0.05)
                self._check_clipboard_once()
                return 0
            if msg == win32con.WM_DESTROY:
                try:
                    ctypes.windll.user32.RemoveClipboardFormatListener(hwnd)
                except Exception:
                    pass
                win32gui.PostQuitMessage(0)
                return 0
            return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

        def _create_window(self) -> int:
            window_class = win32gui.WNDCLASS()
            window_class.lpfnWndProc = self._process_message
            window_class.lpszClassName = f"{self.__class__.__name__}_{id(self)}"
            window_class.hInstance = win32api.GetModuleHandle(None)
            class_atom = win32gui.RegisterClass(window_class)
            return win32gui.CreateWindow(
                class_atom,
                window_class.lpszClassName,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                window_class.hInstance,
                None,
            )

        def listen(self) -> None:
            """Start the background clipboard monitoring thread."""
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()

            def runner() -> None:
                self._check_clipboard_once()
                hwnd = self._create_window()
                self._hwnd = hwnd
                ctypes.windll.user32.AddClipboardFormatListener(hwnd)
                if self._stop_event.is_set():
                    win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                win32gui.PumpMessages()
                self._hwnd = None

            self._thread = threading.Thread(target=runner, name="ClipboardListener", daemon=True)
            self._thread.start()

        def stop(self) -> None:
            """Stop clipboard monitoring and close the hidden message window."""
            self._stop_event.set()
            hwnd = self._hwnd
            if hwnd is not None:
                try:
                    win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                except pywintypes.error:
                    pass
            if self._thread is not None:
                self._thread.join(timeout=2.0)

else:

    class ClipboardListener:
        """No-op fallback clipboard listener on non-Windows platforms."""

        def __init__(self, on_change: Callable[[str | None], None] | None = None) -> None:
            self._lock = threading.Lock()
            self._server_ip: str | None = None
            self._on_change = on_change

        @property
        def server_ip(self) -> str | None:
            with self._lock:
                return self._server_ip

        @property
        def SERVER_IP(self) -> str | None:
            return self.server_ip

        def set_server_ip(self, ip: str | None) -> None:
            changed = False
            with self._lock:
                if self._server_ip != ip:
                    self._server_ip = ip
                    changed = True
            if changed and self._on_change is not None:
                self._on_change(ip)

        def listen(self) -> None:
            pass

        def stop(self) -> None:
            pass
