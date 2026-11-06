"""Reading physical keyboards through evdev.

Only read access is needed (``input`` group).  Injected text goes through
wtype's own Wayland virtual keyboard, so it never comes back to us and no grab
or self-filtering is necessary.
"""

from __future__ import annotations

import glob
import os

import evdev
from evdev import ecodes

EVENT_GLOB = "/dev/input/event*"

#: devices created by input-injection tools; reading them would feed our own
#: corrections (or an IME's output) back into the word buffer
VIRTUAL_NAME_HINTS = (
    "uinput",
    "virtual",
    "wtype",
    "ydotool",
    "keyb-switch",
    "dotool",
    "xdotool",
    "osu!",
)

KEY_A = ecodes.KEY_A
KEY_Z = ecodes.KEY_Z


def event_paths() -> list[str]:
    return sorted(glob.glob(EVENT_GLOB))


def open_device(path: str) -> tuple[evdev.InputDevice | None, str | None]:
    try:
        device = evdev.InputDevice(path)
    except PermissionError:
        return None, "permission"
    except OSError as error:
        return None, str(error)
    return device, None


def is_virtual(name: str) -> bool:
    lowered = (name or "").lower()
    return any(hint in lowered for hint in VIRTUAL_NAME_HINTS)


def keyboard_paths() -> list[str]:
    """Paths of devices that look like keyboards (readable ones only)."""
    paths: list[str] = []
    for path in event_paths():
        device, _error = open_device(path)
        if device is None:
            continue
        try:
            keys = device.capabilities(verbose=False).get(ecodes.EV_KEY) or []
        finally:
            device.close()
        if KEY_A in keys and KEY_Z in keys and not is_virtual(device.name):
            paths.append(path)
    return paths


def access_error() -> str | None:
    """Human readable description of a missing /dev/input permission."""
    paths = event_paths()
    if not paths:
        return "устройства /dev/input/event* не найдены"
    readable = 0
    unreadable = 0
    for path in paths:
        try:
            handle = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except PermissionError:
            unreadable += 1
            continue
        except OSError:
            continue
        os.close(handle)
        readable += 1
    if readable:
        return None
    if unreadable:
        return (
            "нет доступа к /dev/input (%d устройств): добавьте пользователя в группу "
            "input командой: sudo usermod -aG input $USER" % unreadable
        )
    return None


class Reader:
    """Multiplexes all physical keyboards into one non-blocking event stream."""

    def __init__(self) -> None:
        self._devices: dict[int, evdev.InputDevice] = {}
        self._paths: dict[int, str] = {}
        self.skipped: dict[str, str] = {}

    def refresh(self) -> None:
        self._prune()
        known = set(self._paths.values())
        for path in event_paths():
            if path in known:
                continue
            device, error = open_device(path)
            if device is None:
                if error:
                    self.skipped[path] = error
                continue
            try:
                keys = device.capabilities(verbose=False).get(ecodes.EV_KEY) or []
            except OSError:
                device.close()
                continue
            name = device.name
            if KEY_A not in keys or KEY_Z not in keys or is_virtual(name):
                device.close()
                continue
            self.skipped.pop(path, None)
            self._devices[device.fd] = device
            self._paths[device.fd] = path

    def _prune(self) -> None:
        for fd in list(self._devices):
            path = self._paths.get(fd, "")
            if path and os.path.exists(path):
                continue
            self._drop(fd)

    def _drop(self, fd: int) -> None:
        device = self._devices.pop(fd, None)
        self._paths.pop(fd, None)
        if device is not None:
            try:
                device.close()
            except OSError:
                pass

    def filenos(self) -> list[int]:
        self._prune()
        return list(self._devices)

    def read(self, fd: int) -> list[evdev.InputEvent]:
        device = self._devices.get(fd)
        if device is None:
            return []
        try:
            return list(device.read())
        except OSError:
            self._drop(fd)
            return []

    def close(self) -> None:
        for fd in list(self._devices):
            self._drop(fd)
