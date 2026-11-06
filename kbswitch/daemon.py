"""Event loop: reads keyboards, feeds the engine, serves the CLI socket."""

from __future__ import annotations

import errno
import os
import select
import signal
import sys
import threading
import time

from . import hypr, ipc
from .watcher import StaticLayouts, Watcher
from .engine import Engine, State, notify
from .reader import Reader, access_error

RESCAN_INTERVAL = 3.0


class Daemon:
    def __init__(self, verbose: bool = False, no_input: bool = False):
        self.verbose = verbose
        self.no_input = no_input
        self.state = State()
        self.engine = Engine(self.state)
        self.reader = Reader()
        self.server = ipc.Server(self.handle)
        self._stop = threading.Event()
        self._lock = threading.Lock()

    # -- logging ---------------------------------------------------------
    def log(self, message: str) -> None:
        if self.verbose:
            print(message, file=sys.stderr, flush=True)

    # -- IPC -------------------------------------------------------------
    def handle(self, request: dict) -> dict:
        command = (request or {}).get("command")
        try:
            if command == "ping":
                return {"ok": True, "pid": os.getpid()}
            if command == "status":
                return {"ok": True, "status": self.state.status()}
            if command == "reload":
                with self._lock:
                    self.state.reload()
                    self.engine.reset_buffer()
                return {"ok": True, "status": self.state.status()}
            if command == "correct":
                with self._lock:
                    return self.engine.manual_correct()
            if command == "force":
                return self.engine.force_last_word()
            if command == "autocorrect":
                value = request.get("value")
                with self._lock:
                    if value is None:
                        result = self.engine.toggle_autocorrect()
                    else:
                        self.engine.set_autocorrect(bool(value))
                        result = self.state.config.autocorrect
                return {"ok": True, "autocorrect": result}
            if command == "simulate":
                with self._lock:
                    return self.engine.simulate(
                        str(request.get("text", "")), request.get("layout")
                    )
            if command == "typo":
                return self.engine.typo(str(request.get("text", "")))
            if command == "stop":
                self._stop.set()
                return {"ok": True}
        except Exception as error:  # keep the daemon alive on any command bug
            return {"ok": False, "error": "%s: %s" % (type(error).__name__, error)}
        return {"ok": False, "error": "неизвестная команда: %s" % command}

    # -- main loop -------------------------------------------------------
    def run(self) -> int:
        problem = None if self.no_input else access_error()
        if problem:
            message = "Нет доступа к клавиатуре: %s" % problem
            print(message, file=sys.stderr)
            notify("keyb-switch", message, urgency="critical")
            return 2

        if not self.no_input:
            self.reader.refresh()
        if not self.no_input and not self.reader.filenos():
            message = "Клавиатуры не найдены"
            print(message, file=sys.stderr)
            notify("keyb-switch", message, urgency="critical")
            return 2

        watcher = Watcher()
        watcher.start()
        self.state.watcher = watcher

        self.server.start()
        signal.signal(signal.SIGTERM, lambda *_: self._stop.set())
        signal.signal(signal.SIGINT, lambda *_: self._stop.set())
        self.log("keyb-switch запущен: %d клавиатур, раскладки %s" % (
            len(self.reader.filenos()), ",".join(self.state.layout_names)))

        next_scan = time.monotonic() + RESCAN_INTERVAL
        try:
            while not self._stop.is_set():
                filenos = self.reader.filenos()
                try:
                    ready, _, _ = select.select(filenos, [], [], 0.25)
                except OSError as error:
                    if error.errno == errno.EINTR:
                        continue
                    raise
                except ValueError:
                    self.reader.refresh()
                    continue
                for fd in ready:
                    self._consume(fd)
                if time.monotonic() >= next_scan:
                    self.reader.refresh()
                    next_scan = time.monotonic() + RESCAN_INTERVAL
        finally:
            watcher.stop()
            self.reader.close()
            self.server.stop()
            self.state.speller.close()
            if self.state.layouts is not None:
                self.state.layouts.close()
        return 0

    def _consume(self, fd: int) -> None:
        events = self.reader.read(fd)
        for event in events:
            if event.type != hypr_ev_key():
                continue
            try:
                with self._lock:
                    self.engine.handle_key(event.code, event.value)
            except Exception as error:  # never die on a single bad event
                self.log("ошибка обработки: %s: %s" % (type(error).__name__, error))


def hypr_ev_key() -> int:
    from evdev import ecodes

    return ecodes.EV_KEY


def main(verbose: bool = False, no_input: bool = False) -> int:
    return Daemon(verbose=verbose, no_input=no_input).run()
