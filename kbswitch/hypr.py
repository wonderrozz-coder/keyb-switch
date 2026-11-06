"""Thin wrapper around the Hyprland IPC sockets."""

from __future__ import annotations

import json
import os
import socket
import subprocess


class HyprlandError(RuntimeError):
    pass


def _runtime_dir() -> str:
    return os.environ.get("XDG_RUNTIME_DIR") or "/tmp"


def _instance() -> str:
    signature = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if signature:
        return signature
    base = os.path.join(_runtime_dir(), "hypr")
    try:
        entries = sorted(os.listdir(base))
    except OSError as error:
        raise HyprlandError("Hyprland не запущен") from error
    if not entries:
        raise HyprlandError("Hyprland не запущен")
    return entries[0]


def socket_path() -> str:
    return os.path.join(_runtime_dir(), "hypr", _instance(), ".socket.sock")


def command_socket() -> str:
    return os.path.join(_runtime_dir(), "hypr", _instance(), ".socket2.sock")


def request(command: str, timeout: float = 2.0) -> str:
    path = socket_path()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(path)
        sock.sendall(command.encode())
        chunks = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    return b"".join(chunks).decode(errors="replace")


def request_json(command: str, timeout: float = 2.0):
    # the "j/" prefix makes Hyprland answer with a JSON object instead of text
    text = request("j/" + command, timeout=timeout)
    if not text.strip():
        raise HyprlandError("пустой ответ Hyprland на %r" % command)
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise HyprlandError(text.strip()[:200]) from error


def keyword(option: str, value: str) -> str:
    return request("keyword %s %s" % (option, value))


def getoption(option: str) -> dict:
    return request_json("getoption %s" % option)


def str_option(option: str) -> str:
    data = getoption(option)
    return data.get("str") or ""


def devices() -> dict:
    return request_json("devices")


def keyboards() -> list[dict]:
    return list(devices().get("keyboards") or [])


def main_keyboard() -> dict | None:
    for board in keyboards():
        if board.get("main"):
            return board
    boards = keyboards()
    return boards[0] if boards else None


def active_layout_index() -> int | None:
    board = main_keyboard()
    if not board:
        return None
    index = board.get("active_layout_index")
    return int(index) if isinstance(index, int) else None


def layout_names() -> list[str]:
    names: list[str] = []
    for board in keyboards():
        layout = board.get("layout") or ""
        if not layout:
            continue
        split = [part.strip() for part in layout.split(",") if part.strip()]
        if split and split != names:
            names = split
            break
    return names


def switch_layout(index: int) -> None:
    # "all" keeps every (sub)device in sync, including the virtual keyboard
    # used for text injection.
    request("switchxkblayout all %d" % index)


def set_prop(target: str, prop: str, value: str) -> None:
    request("setprop %s %s %s" % (target, prop, value))


def active_window() -> dict:
    try:
        data = request_json("activewindow")
    except HyprlandError:
        return {}
    return data if isinstance(data, dict) else {}


def active_window_class() -> str | None:
    klass = active_window().get("class")
    return klass or None
