"""Text injection through the wtype virtual-keyboard client.

wtype talks the Wayland virtual-keyboard protocol and sends the requested
characters verbatim: it is not affected by the compositor's active xkb group.
That makes injection independent from layout switching, and its keystrokes
never reach our evdev readers, so a correction cannot retrigger itself.
"""

from __future__ import annotations

import os
import shutil
import subprocess


class Unavailable(RuntimeError):
    pass


class Injector:
    def __init__(self, delay_ms: int = 6):
        self.delay_ms = max(0, int(delay_ms))
        self.binary = shutil.which("wtype")
        if not self.binary:
            raise Unavailable("wtype не найден (пакет wtype)")

    def available(self) -> bool:
        return bool(self.binary and os.environ.get("WAYLAND_DISPLAY"))

    def replace(self, text: str, delete: int) -> None:
        """Erase `delete` characters and type `text` in one round trip."""
        if delete <= 0 and not text:
            return
        command = [self.binary]
        for _ in range(max(0, delete)):
            command += ["-k", "BackSpace"]
        if text:
            if self.delay_ms:
                command += ["-d", str(self.delay_ms)]
            command += ["--", text]
        self._run(command)

    def type_text(self, text: str) -> None:
        self.replace(text, 0)

    def _run(self, command: list[str]) -> None:
        try:
            result = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise Unavailable(str(error)) from error
        if result.returncode != 0:
            raise Unavailable("wtype завершился с кодом %d" % result.returncode)
