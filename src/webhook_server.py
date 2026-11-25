"""FastAPI webhook server wrapper for Tot-exporter."""

from __future__ import annotations

import socket
import threading
import time
from typing import Callable

from fastapi import FastAPI, Response
import uvicorn

from chat_logger import ChatLogger


class PortInUseError(OSError):
    """Raised when the configured webhook port cannot be bound."""


def ensure_port_available(host: str, port: int) -> None:
    """Verify ``(host, port)`` can be bound exclusively before starting Uvicorn."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            sock.bind((host, port))
        except OSError as exc:
            raise PortInUseError(
                f"Port {port} on {host} is already in use or cannot be bound."
            ) from exc


def create_app(
    chat_logger: ChatLogger,
    get_server_ip: Callable[[], str | None],
) -> FastAPI:
    """Create the FastAPI application handling Tot! chat webhooks."""
    app = FastAPI(title="Tot-exporter", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/message")
    def save_log(sender: str, message: str) -> Response:
        chat_logger.log_message(
            sender=sender,
            message=message,
            server_ip=get_server_ip(),
        )
        return Response(status_code=200)

    @app.get("/health")
    def health() -> dict[str, str | None]:
        return {"status": "ok", "server_ip": get_server_ip()}

    return app


class WebhookServer:
    """Background Uvicorn server without raw console HTTP/ANSI logging."""

    def __init__(
        self,
        chat_logger: ChatLogger,
        get_server_ip: Callable[[], str | None],
        host: str = "0.0.0.0",
        port: int = 2137,
    ) -> None:
        self.host = host
        self.port = port
        self.app = create_app(chat_logger=chat_logger, get_server_ip=get_server_ip)
        self._config = uvicorn.Config(
            self.app,
            host=self.host,
            port=self.port,
            log_config=None,
            access_log=False,
            loop="asyncio",
            http="h11",
            ws="none",
            workers=1,
        )
        self._server = uvicorn.Server(self._config)
        self._thread: threading.Thread | None = None
        self._error: BaseException | None = None

    def start(self, startup_timeout: float = 5.0) -> None:
        """Start the webhook server in a daemon thread and wait until ready."""
        if self._thread is not None and self._thread.is_alive():
            return

        ensure_port_available(self.host, self.port)
        self._error = None

        def runner() -> None:
            try:
                self._server.run()
            except BaseException as exc:  # pragma: no cover - defensive capture
                self._error = exc

        self._thread = threading.Thread(target=runner, name="WebhookServer", daemon=True)
        self._thread.start()

        deadline = time.monotonic() + startup_timeout
        while time.monotonic() < deadline:
            if self._error is not None:
                raise self._error
            if self._server.started:
                return
            if not self._thread.is_alive():
                raise RuntimeError("Webhook server exited unexpectedly during startup.")
            time.sleep(0.05)

        raise TimeoutError(f"Webhook server did not start within {startup_timeout:.1f}s.")

    def stop(self, timeout: float = 3.0) -> None:
        """Signal Uvicorn to exit and wait for the server thread to finish."""
        self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=timeout)
