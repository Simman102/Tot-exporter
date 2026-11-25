"""Windows per-user autostart management via the HKCU Run registry key."""

from __future__ import annotations

from pathlib import Path
import shutil
import sys

APP_NAME = "Tot-exporter"
RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
APPROVED_KEY_PATH = (
    r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
)

if sys.platform == "win32":
    import winreg
else:  # pragma: no cover - non-Windows fallback
    winreg = None  # type: ignore[assignment]


def is_supported() -> bool:
    """Return True when Windows registry autostart is available."""
    return sys.platform == "win32" and winreg is not None


def get_autostart_command(script_path: Path | None = None) -> str:
    """Build the command string Windows should execute at user logon."""
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}"'

    target_script = (
        script_path or (Path(__file__).resolve().parent / "Tot-exporter.py")
    ).resolve()

    exe_path = Path(sys.executable).resolve()
    pythonw_candidate = exe_path.with_name("pythonw.exe")
    if pythonw_candidate.exists():
        launcher = str(pythonw_candidate)
    else:
        launcher = shutil.which("pyw") or str(exe_path)

    return f'"{launcher}" "{target_script}"'


def _read_run_value(app_name: str = APP_NAME) -> str | None:
    if not is_supported():
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_READ) as key:
            value, _reg_type = winreg.QueryValueEx(key, app_name)
            return value if isinstance(value, str) and value.strip() else None
    except FileNotFoundError:
        return None


def _is_startup_approved(app_name: str = APP_NAME) -> bool:
    """Return False if the user disabled this startup entry in Task Manager."""
    if not is_supported():
        return False
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, APPROVED_KEY_PATH, 0, winreg.KEY_READ
        ) as key:
            data, _reg_type = winreg.QueryValueEx(key, app_name)
            if isinstance(data, bytes) and len(data) >= 1:
                # Even first byte (e.g. 0x02, 0x06) = enabled; odd (e.g. 0x03) = disabled
                return (data[0] & 1) == 0
    except FileNotFoundError:
        return True
    except OSError:
        return True
    return True


def _clear_disabled_approval(app_name: str = APP_NAME) -> None:
    if not is_supported():
        return
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, APPROVED_KEY_PATH, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, app_name)
    except FileNotFoundError:
        pass
    except OSError:
        pass


def is_autostart_enabled(app_name: str = APP_NAME) -> bool:
    """Return True if Tot-exporter is registered and approved to start with Windows."""
    if not is_supported():
        return False
    return _read_run_value(app_name) is not None and _is_startup_approved(app_name)


def set_autostart(
    enabled: bool,
    app_name: str = APP_NAME,
    command: str | None = None,
) -> bool:
    """Enable or disable per-user Windows startup for Tot-exporter."""
    if not is_supported():
        return False

    if enabled:
        launch_cmd = command or get_autostart_command()
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, launch_cmd)
        _clear_disabled_approval(app_name)
        return True

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, app_name)
    except FileNotFoundError:
        pass
    return False


def sync_autostart_path(app_name: str = APP_NAME) -> bool:
    """Refresh the stored autostart path if enabled and the app was moved.

    Returns True if the registry value was updated, False otherwise.
    """
    if not is_autostart_enabled(app_name):
        return False
    current_value = _read_run_value(app_name)
    expected_value = get_autostart_command()
    if current_value == expected_value:
        return False
    set_autostart(True, app_name=app_name, command=expected_value)
    return True
