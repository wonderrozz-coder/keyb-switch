"""Unix-socket server used by the CLI to talk to the running daemon."""

from __future__ import annotations

import json
import os
import socket
import threading

from . import config as cfg


class Server:
    def __init__(self, handler):
        self.path = cfg.socket_path()
        self.handler = handler
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(self.path)
        server.listen(8)
        server.settimeout(0.5)
        self._socket = server
        self._thread = threading.Thread(target=self._serve, name="ipc", daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        assert self._socket is not None
        while not self._stop.is_set():
            try:
                connection, _ = self._socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with connection:
                connection.settimeout(10)
                try:
                    request = connection.makefile("rwb")
                    line = request.readline()
                    if not line:
                        continue
                    response = self.handler(json.loads(line.decode()))
                    request.write((json.dumps(response) + "\n").encode())
                    request.flush()
                except (OSError, ValueError):
                    continue

    def stop(self) -> None:
        self._stop.set()
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass


def call(request: dict, timeout: float = 10.0) -> dict | None:
    path = cfg.socket_path()
    if not os.path.exists(path):
        return None
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        try:
            client.connect(path)
            client.sendall((json.dumps(request) + "\n").encode())
            response = client.makefile("rb").readline()
        except OSError:
            return None
    if not response:
        return None
    try:
        return json.loads(response.decode())
    except json.JSONDecodeError:
        return None


def running() -> bool:
    return call({"command": "ping"}, timeout=2.0) is not None
