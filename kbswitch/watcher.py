"""Live view of the compositor state through the Hyprland event socket.

Layout changes are pushed by Hyprland, so the event handlers can stay fast and
never block on a socket round trip while the user is typing.
"""

from __future__ import annotations

import os
import socket
import threading
import time

from . import hypr


class Watcher:
    def __init__(self, reconnect_delay: float = 2.0):
        self._lock = threading.Lock()
        self._layout_index: int | None = None
        self._layout_name: str | None = None
        self._window_class: str | None = None
        self._reconnect_delay = reconnect_delay
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._connected = threading.Event()

    # -- lifecycle -------------------------------------------------------
    def start(self) -> None:
        self._prime()
        self._thread = threading.Thread(target=self._loop, name="hypr-events", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _prime(self) -> None:
        try:
            index = hypr.active_layout_index()
        except hypr.HyprlandError:
            index = None
        with self._lock:
            self._layout_index = index
        window = hypr.active_window()
        with self._lock:
            self._window_class = window.get("class") or None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                path = hypr.command_socket()
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                    sock.settimeout(1.0)
                    sock.connect(path)
                    self._connected.set()
                    reader = sock.makefile("r", encoding="utf-8", errors="replace")
                    for line in reader:
                        if self._stop.is_set():
                            break
                        self._handle(line.strip())
            except (OSError, hypr.HyprlandError):
                pass
            finally:
                self._connected.clear()
            if self._stop.is_set():
                break
            time.sleep(self._reconnect_delay)
            self._prime()

    def _handle(self, line: str) -> None:
        if not line or ">>" not in line:
            return
        event, payload = line.split(">>", 1)
        if event == "activelayout":
            index = self._index_from_devices()
            with self._lock:
                if index is not None:
                    self._layout_index = index
                _, _, name = payload.partition(",")
                self._layout_name = name or None
        elif event in ("activewindow", "activewindowv2", "openwindow", "closewindow"):
            window = hypr.active_window()
            with self._lock:
                self._window_class = window.get("class") or None
        elif event in ("configreloaded", "monitoradded", "monitorremoved"):
            self._prime()

    @staticmethod
    def _index_from_devices() -> int | None:
        try:
            return hypr.active_layout_index()
        except hypr.HyprlandError:
            return None

    # -- accessors -------------------------------------------------------
    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    def layout_index(self) -> int:
        with self._lock:
            cached = self._layout_index
        if cached is not None and self._connected.is_set():
            return cached
        # not connected, or nothing received yet: ask directly
        index = self._index_from_devices()
        if index is None:
            return cached if cached is not None else 0
        with self._lock:
            self._layout_index = index
        return index

    def set_layout_index(self, index: int) -> None:
        with self._lock:
            self._layout_index = index

    def window_class(self) -> str | None:
        with self._lock:
            return self._window_class

    def refresh_window(self) -> str | None:
        window = hypr.active_window()
        klass = window.get("class") or None
        with self._lock:
            self._window_class = klass
        return klass


class StaticLayouts:
    """Fallback layout source when the event socket is unavailable."""

    def __init__(self) -> None:
        self._index: int | None = None

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    @property
    def connected(self) -> bool:
        return False

    def layout_index(self) -> int:
        # Always ask: this fallback exists precisely because the event socket is
        # unavailable, so a cached value would go stale the moment the user
        # presses their own layout switch.
        try:
            index = hypr.active_layout_index()
        except hypr.HyprlandError:
            index = None
        if index is not None:
            self._index = index
        return self._index if self._index is not None else 0

    def set_layout_index(self, index: int) -> None:
        self._index = index

    def window_class(self) -> str | None:
        return hypr.active_window_class()

    def refresh_window(self) -> str | None:
        return hypr.active_window_class()
