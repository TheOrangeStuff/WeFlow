"""Start at login (Settings). Only meaningful for the installed app."""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path

LABEL = "com.weflow.app"


def _command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "WeFlow"]


def set_start_at_login(enabled: bool) -> bool:
    """Turn start-at-login on or off. Returns False when it could not be changed."""
    try:
        if sys.platform == "darwin":
            plist = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
            if not enabled:
                with contextlib.suppress(FileNotFoundError):
                    plist.unlink()
                return True
            args = "".join(f"<string>{part}</string>" for part in _command())
            plist.parent.mkdir(parents=True, exist_ok=True)
            plist.write_text(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                f'<plist version="1.0"><dict><key>Label</key><string>{LABEL}</string>'
                f"<key>ProgramArguments</key><array>{args}</array>"
                "<key>RunAtLoad</key><true/></dict></plist>\n",
                encoding="utf-8",
            )
            return True
        if sys.platform == "win32":
            import winreg

            path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
                if enabled:
                    command = " ".join(f'"{part}"' for part in _command())
                    winreg.SetValueEx(key, "WeFlow", 0, winreg.REG_SZ, command)
                else:
                    with contextlib.suppress(FileNotFoundError):
                        winreg.DeleteValue(key, "WeFlow")
            return True
    except OSError:
        return False
    return False
