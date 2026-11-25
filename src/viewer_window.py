"""On-demand Tkinter live chat viewer window for Tot-exporter."""

from __future__ import annotations

from datetime import datetime, timezone
import ipaddress
import os
from pathlib import Path
import re
import sys
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from typing import Callable

from PIL import ImageTk

from app_icon import create_app_icon
from chat_logger import ChatEntry, ChatLogger
from clipboard_listener import parse_server_ip

_LOG_LINE_RE = re.compile(r"^\[(\d{2}:\d{2}:\d{2})\] ([^:]+): (.*)$")


class ViewerWindow:
    """Dark-themed desktop window displaying decoded Tot! chat messages."""

    BG_DARK = "#131722"
    BG_PANEL = "#1b2130"
    BG_INPUT = "#0f131c"
    BORDER = "#2b3448"
    FG_PRIMARY = "#e6edf3"
    FG_MUTED = "#8b949e"
    ACCENT_TEAL = "#48bbb2"
    ACCENT_GOLD = "#f2c14e"
    ACCENT_BLUE = "#58a6ff"

    def __init__(
        self,
        root: tk.Tk,
        chat_logger: ChatLogger,
        webhook_url: str,
        initial_server_ip: str | None,
        on_set_server_ip: Callable[[str | None], None],
        on_quit: Callable[[], None],
        autostart_enabled: bool = False,
        on_toggle_autostart: Callable[[bool], None] | None = None,
        start_hidden: bool = True,
    ) -> None:
        self.root = root
        self.chat_logger = chat_logger
        self.webhook_url = webhook_url
        self._server_ip = initial_server_ip
        self._on_set_server_ip = on_set_server_ip
        self._on_quit = on_quit
        self._on_toggle_autostart = on_toggle_autostart

        self.auto_scroll_var = tk.BooleanVar(value=True)
        self.autostart_var = tk.BooleanVar(value=autostart_enabled)
        self.server_ip_var = tk.StringVar()
        self.log_file_var = tk.StringVar()
        self.session_count_var = tk.StringVar(value="Session messages: 0")
        self._copy_after_id: str | None = None

        self._configure_window()
        self._build_styles()
        self._build_ui()
        self._refresh_status_labels()
        self._load_initial_history()

        if start_hidden:
            self.root.withdraw()
        else:
            self.show()

    def _configure_window(self) -> None:
        self.root.title("Tot-exporter — Live Chat Viewer")
        self.root.geometry("860x560")
        self.root.minsize(640, 400)
        self.root.configure(bg=self.BG_DARK)
        self.root.protocol("WM_DELETE_WINDOW", self.hide)

        try:
            self._icon_photo = ImageTk.PhotoImage(create_app_icon(64, active=True))
            self.root.iconphoto(True, self._icon_photo)
        except Exception:
            self._icon_photo = None

    def _build_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("App.TFrame", background=self.BG_DARK)
        style.configure("Panel.TFrame", background=self.BG_PANEL)
        style.configure(
            "App.TCheckbutton",
            background=self.BG_DARK,
            foreground=self.FG_PRIMARY,
            focuscolor=self.BG_DARK,
        )
        style.map(
            "App.TCheckbutton",
            background=[("active", self.BG_DARK)],
            foreground=[("active", self.FG_PRIMARY)],
        )

    def _make_button(
        self,
        parent: tk.Widget,
        text: str,
        command: Callable[[], None],
        accent: bool = False,
    ) -> tk.Button:
        bg = "#253047" if not accent else "#1f5a57"
        active_bg = "#31405e" if not accent else "#287570"
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=bg,
            fg=self.FG_PRIMARY,
            activebackground=active_bg,
            activeforeground=self.FG_PRIMARY,
            relief=tk.FLAT,
            bd=0,
            padx=10,
            pady=4,
            cursor="hand2",
            font=("Segoe UI", 9),
        )

    def _build_ui(self) -> None:
        container = tk.Frame(self.root, bg=self.BG_DARK, padx=14, pady=12)
        container.pack(fill=tk.BOTH, expand=True)

        # Header status panel
        header = tk.Frame(
            container,
            bg=self.BG_PANEL,
            highlightbackground=self.BORDER,
            highlightthickness=1,
            padx=12,
            pady=10,
        )
        header.pack(fill=tk.X, pady=(0, 10))

        # Row 1: Webhook status + Copy URL
        row1 = tk.Frame(header, bg=self.BG_PANEL)
        row1.pack(fill=tk.X, pady=(0, 6))

        tk.Label(
            row1,
            text="● Listening",
            bg=self.BG_PANEL,
            fg=self.ACCENT_TEAL,
            font=("Segoe UI Semibold", 10),
        ).pack(side=tk.LEFT)

        tk.Label(
            row1,
            text=f"  {self.webhook_url}",
            bg=self.BG_PANEL,
            fg=self.FG_PRIMARY,
            font=("Consolas", 10),
        ).pack(side=tk.LEFT)

        self.copy_btn = self._make_button(row1, "Copy URL", self._copy_webhook_url)
        self.copy_btn.pack(side=tk.RIGHT)

        # Row 2: Server IP + controls
        row2 = tk.Frame(header, bg=self.BG_PANEL)
        row2.pack(fill=tk.X, pady=(0, 6))

        tk.Label(
            row2,
            text="Server IP:",
            bg=self.BG_PANEL,
            fg=self.FG_MUTED,
            font=("Segoe UI", 9),
        ).pack(side=tk.LEFT)

        tk.Label(
            row2,
            textvariable=self.server_ip_var,
            bg=self.BG_PANEL,
            fg=self.ACCENT_GOLD,
            font=("Consolas", 10, "bold"),
        ).pack(side=tk.LEFT, padx=(6, 0))

        self._make_button(row2, "Clear IP", lambda: self._on_set_server_ip(None)).pack(
            side=tk.RIGHT
        )
        self._make_button(row2, "Set IP...", self._prompt_server_ip).pack(
            side=tk.RIGHT, padx=(0, 6)
        )

        # Row 3: Active log file + folder actions
        row3 = tk.Frame(header, bg=self.BG_PANEL)
        row3.pack(fill=tk.X)

        tk.Label(
            row3,
            text="Active log:",
            bg=self.BG_PANEL,
            fg=self.FG_MUTED,
            font=("Segoe UI", 9),
        ).pack(side=tk.LEFT)

        tk.Label(
            row3,
            textvariable=self.log_file_var,
            bg=self.BG_PANEL,
            fg=self.ACCENT_BLUE,
            font=("Consolas", 9),
        ).pack(side=tk.LEFT, padx=(6, 0))

        self._make_button(row3, "Open Logs Folder", self._open_logs_folder).pack(
            side=tk.RIGHT
        )
        self._make_button(row3, "Open Today's Log", self._open_current_log_file).pack(
            side=tk.RIGHT, padx=(0, 6)
        )

        # Main log viewer
        feed_frame = tk.Frame(
            container,
            bg=self.BG_INPUT,
            highlightbackground=self.BORDER,
            highlightthickness=1,
        )
        feed_frame.pack(fill=tk.BOTH, expand=True)

        scrollbar = tk.Scrollbar(feed_frame, orient=tk.VERTICAL)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.text = tk.Text(
            feed_frame,
            bg=self.BG_INPUT,
            fg=self.FG_PRIMARY,
            insertbackground=self.FG_PRIMARY,
            selectbackground="#2c436b",
            relief=tk.FLAT,
            bd=0,
            padx=12,
            pady=10,
            wrap=tk.WORD,
            font=("Consolas", 10),
            yscrollcommand=scrollbar.set,
            state=tk.DISABLED,
        )
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.text.yview)

        self.text.tag_configure("time", foreground=self.ACCENT_TEAL)
        self.text.tag_configure("sender", foreground=self.ACCENT_GOLD, font=("Consolas", 10, "bold"))
        self.text.tag_configure("message", foreground=self.FG_PRIMARY)
        self.text.tag_configure("history", foreground=self.FG_MUTED)
        self.text.tag_configure("system", foreground=self.ACCENT_BLUE, font=("Segoe UI", 9, "italic"))

        # Footer controls
        footer = tk.Frame(container, bg=self.BG_DARK)
        footer.pack(fill=tk.X, pady=(10, 0))

        tk.Label(
            footer,
            textvariable=self.session_count_var,
            bg=self.BG_DARK,
            fg=self.FG_MUTED,
            font=("Segoe UI", 9),
        ).pack(side=tk.LEFT)

        ttk.Checkbutton(
            footer,
            text="Auto-scroll",
            variable=self.auto_scroll_var,
            style="App.TCheckbutton",
        ).pack(side=tk.LEFT, padx=(16, 0))

        if self._on_toggle_autostart is not None:
            ttk.Checkbutton(
                footer,
                text="Start with Windows",
                variable=self.autostart_var,
                command=lambda: self._on_toggle_autostart(bool(self.autostart_var.get())),
                style="App.TCheckbutton",
            ).pack(side=tk.LEFT, padx=(14, 0))

        self._make_button(footer, "Quit App", self._on_quit).pack(side=tk.RIGHT)
        self._make_button(footer, "Hide to Tray", self.hide, accent=True).pack(
            side=tk.RIGHT, padx=(0, 6)
        )
        self._make_button(footer, "Clear View", self._clear_view).pack(
            side=tk.RIGHT, padx=(0, 6)
        )

    def _refresh_status_labels(self) -> None:
        if self._server_ip:
            self.server_ip_var.set(self._server_ip)
        else:
            self.server_ip_var.set("Not set (waiting for clipboard IP:port)")
        active_path = self.chat_logger.get_log_path(server_ip=self._server_ip)
        self.log_file_var.set(active_path.name)

    def _load_initial_history(self) -> None:
        active_path = self.chat_logger.get_log_path(server_ip=self._server_ip)
        tail = self.chat_logger.load_file_tail(active_path, limit=150)
        if tail:
            self._append_system_line(f"Loaded recent entries from {active_path.name}:")
            for line in tail:
                self._append_raw_line(line, is_history=True)
            self._append_system_line("─── Live session started (UTC) ───")
        else:
            self._append_system_line(
                f"Ready. Point Tot! webhook to {self.webhook_url} — copy a server IP:port anytime to tag filenames."
            )

    def _append_raw_line(self, line: str, is_history: bool = False) -> None:
        self.text.configure(state=tk.NORMAL)
        match = _LOG_LINE_RE.match(line)
        if match:
            hhmmss, sender, body = match.groups()
            if is_history:
                self.text.insert(tk.END, f"[{hhmmss}] {sender}: {body}\n", "history")
            else:
                self.text.insert(tk.END, f"[{hhmmss}] ", "time")
                self.text.insert(tk.END, f"{sender}", "sender")
                self.text.insert(tk.END, f": {body}\n", "message")
        else:
            tag = "history" if is_history else "message"
            self.text.insert(tk.END, f"{line}\n", tag)
        self.text.configure(state=tk.DISABLED)
        if self.auto_scroll_var.get():
            self.text.see(tk.END)

    def _append_system_line(self, text: str) -> None:
        now_str = datetime.now(timezone.utc).strftime("%H:%M:%S")
        self.text.configure(state=tk.NORMAL)
        self.text.insert(tk.END, f"[{now_str}] {text}\n", "system")
        self.text.configure(state=tk.DISABLED)
        if self.auto_scroll_var.get():
            self.text.see(tk.END)

    def append_entry(self, entry: ChatEntry) -> None:
        """Render a decoded :class:`ChatEntry` into the live chat feed."""
        self.text.configure(state=tk.NORMAL)
        self.text.insert(tk.END, f"[{entry.timestamp.strftime('%H:%M:%S')}] ", "time")
        self.text.insert(tk.END, f"{entry.sender}", "sender")
        self.text.insert(tk.END, f": {entry.message}\n", "message")
        self.text.configure(state=tk.DISABLED)
        self.session_count_var.set(f"Session messages: {self.chat_logger.total_logged}")
        self._refresh_status_labels()
        if self.auto_scroll_var.get():
            self.text.see(tk.END)

    def update_server_ip(self, ip: str | None) -> None:
        """Update displayed server IP and active log file name."""
        if ip == self._server_ip:
            return
        self._server_ip = ip
        self._refresh_status_labels()
        if ip:
            self._append_system_line(f"Server IP set to {ip} (logging to {self.log_file_var.get()})")
        else:
            self._append_system_line(f"Server IP cleared (logging to {self.log_file_var.get()})")

    def set_autostart_state(self, enabled: bool) -> None:
        """Synchronize the 'Start with Windows' checkbox state."""
        self.autostart_var.set(enabled)

    def show(self) -> None:
        """Show and raise the viewer window."""
        self._refresh_status_labels()
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        self.root.focus_force()
        if self.auto_scroll_var.get():
            self.text.see(tk.END)

    def hide(self) -> None:
        """Hide the viewer window back to the system tray."""
        self.root.withdraw()

    def _clear_view(self) -> None:
        self.text.configure(state=tk.NORMAL)
        self.text.delete("1.0", tk.END)
        self.text.configure(state=tk.DISABLED)

    def _copy_webhook_url(self) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(self.webhook_url)
        self.root.update_idletasks()
        self.copy_btn.configure(text="Copied!")
        if self._copy_after_id is not None:
            self.root.after_cancel(self._copy_after_id)
        self._copy_after_id = self.root.after(1500, lambda: self.copy_btn.configure(text="Copy URL"))

    def _prompt_server_ip(self) -> None:
        raw = simpledialog.askstring(
            "Set Server IP",
            "Enter a server IP (e.g. 45.143.197.83 or 45.143.197.83:27015):",
            initialvalue=self._server_ip or "",
            parent=self.root,
        )
        if raw is None:
            return
        cleaned = raw.strip()
        if not cleaned:
            self._on_set_server_ip(None)
            return

        parsed = parse_server_ip(cleaned)
        if parsed is None:
            try:
                parsed = str(ipaddress.IPv4Address(cleaned))
            except ValueError:
                messagebox.showerror(
                    "Invalid Server IP",
                    "Please enter a valid IPv4 address (or IPv4:port with port 1-65535).",
                    parent=self.root,
                )
                return
        self._on_set_server_ip(parsed)

    def _open_path(self, target: Path) -> None:
        if not target.exists():
            messagebox.showinfo("Not Found", f"File does not exist yet:\n{target}", parent=self.root)
            return
        if sys.platform == "win32":
            os.startfile(str(target))  # type: ignore[attr-defined]

    def _open_logs_folder(self) -> None:
        self._open_path(self.chat_logger.base_dir)

    def _open_current_log_file(self) -> None:
        self._open_path(self.chat_logger.get_log_path(server_ip=self._server_ip))
