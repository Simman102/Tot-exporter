"""Chat message file logging and recent history buffer for Tot-exporter."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sys
import threading
from typing import Callable


def get_app_dir() -> Path:
    """Return the directory where the executable or script resides."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


@dataclass(frozen=True)
class ChatEntry:
    """A decoded chat message received by the webhook."""

    timestamp: datetime
    sender: str
    message: str
    server_ip: str | None
    log_path: Path

    def format_line(self) -> str:
        """Format the entry as stored in daily ``.txt`` log files."""
        return f"[{self.timestamp.strftime('%H:%M:%S')}] {self.sender}: {self.message}"


class ChatLogger:
    """Thread-safe daily UTC chat log writer with in-memory history."""

    def __init__(
        self,
        base_dir: Path | None = None,
        max_history: int = 500,
        on_message: Callable[[ChatEntry], None] | None = None,
    ) -> None:
        self.base_dir = Path(base_dir) if base_dir is not None else get_app_dir()
        self._lock = threading.Lock()
        self._history: deque[ChatEntry] = deque(maxlen=max_history)
        self._on_message = on_message
        self._total_logged = 0

    @property
    def total_logged(self) -> int:
        """Return total number of messages logged in this session."""
        with self._lock:
            return self._total_logged

    def get_log_path(
        self,
        server_ip: str | None = None,
        when: datetime | None = None,
    ) -> Path:
        """Compute the UTC daily log file path for an optional server IP."""
        current = when or datetime.now(timezone.utc)
        suffix = f"-{server_ip}" if server_ip else ""
        return self.base_dir / f"{current.date().isoformat()}{suffix}.txt"

    def log_message(
        self,
        sender: str,
        message: str,
        server_ip: str | None = None,
        now: datetime | None = None,
    ) -> ChatEntry:
        """Append a decoded chat message to today's log file and notify listeners."""
        timestamp = now or datetime.now(timezone.utc)
        log_path = self.get_log_path(server_ip=server_ip, when=timestamp)
        entry = ChatEntry(
            timestamp=timestamp,
            sender=sender,
            message=message,
            server_ip=server_ip,
            log_path=log_path,
        )

        with self._lock:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as file:
                file.write(f"{entry.format_line()}\n")
            self._history.append(entry)
            self._total_logged += 1

        if self._on_message is not None:
            try:
                self._on_message(entry)
            except Exception:
                pass

        return entry

    def get_history(self) -> list[ChatEntry]:
        """Return a snapshot of recent session chat entries."""
        with self._lock:
            return list(self._history)

    @staticmethod
    def load_file_tail(log_path: Path, limit: int = 200) -> list[str]:
        """Read up to the last ``limit`` lines from ``log_path`` if it exists."""
        if not log_path.exists():
            return []
        try:
            with log_path.open("r", encoding="utf-8", errors="replace") as file:
                return list(deque((line.rstrip("\r\n") for line in file), maxlen=limit))
        except OSError:
            return []
