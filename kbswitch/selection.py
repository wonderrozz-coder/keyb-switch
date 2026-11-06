"""Selected text, read and replaced through the Wayland primary selection.

Wayland forbids reading another window's contents, but the *primary selection*
is published by the application itself, so a selected fragment can be read with
``wl-paste --primary`` and written back with ``wl-copy --primary`` (apps like
GTK and Qt insert the new selection on middle click, and most toolkits treat a
primary-selection change as a replace request when text is selected).

This is a best-effort path: if the toolkit ignores the update, the caller falls
back to the word buffer.
"""

from __future__ import annotations

import shutil
import subprocess


class Unavailable(RuntimeError):
    pass


class Selection:
    def __init__(self) -> None:
        self.paste = shutil.which("wl-paste")
        self.copy = shutil.which("wl-copy")
        if not self.paste or not self.copy:
            raise Unavailable("нужен пакет wl-clipboard")

    def read(self, timeout: float = 1.5) -> str | None:
        """Text currently selected, or None when the selection is empty."""
        try:
            result = subprocess.run(
                [self.paste, "--primary", "--no-newline"],
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0:
            return None
        text = result.stdout.decode("utf-8", "replace")
        if not text.strip():
            return None
        return text

    def write(self, text: str, timeout: float = 1.5) -> bool:
        try:
            result = subprocess.run(
                [self.copy, "--primary", "--type", "text/plain;charset=utf-8"],
                input=text.encode(),
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0

    def clear(self) -> None:
        try:
            subprocess.run(
                [self.copy, "--primary", "--clear"], timeout=1.0, check=False,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError):
            pass
