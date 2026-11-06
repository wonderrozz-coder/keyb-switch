"""Command line interface: keyb-switch <command>."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

from . import config as cfg
from . import dictionaries, hypr, ipc
from . import __version__
from .engine import notify, state_dir

USAGE = """\
keyb-switch - замена Punto Switcher для Hyprland/Wayland

Использование:
  keyb-switch start            запустить демон в фоне
  keyb-switch stop             остановить демон
  keyb-switch restart          перезапустить демон
  keyb-switch status           состояние демона, раскладок и словарей
  keyb-switch correct          исправить последнее набранное слово (хоткей)
  keyb-switch typo <текст>     перевести текст в другую раскладку
  keyb-switch autocorrect [on|off]  показать/переключить автопереключение
  keyb-switch log              показать журнал исправлений
  keyb-switch dict             установить словари для проверки орфографии
  keyb-switch doctor           проверить зависимости и права
  keyb-switch force            переложить выделение/слово (двойной Shift)
  keyb-switch version          версия программы
  keyb-switch setup            выдать доступ к клавиатуре (нужен sudo)
  keyb-switch init             создать конфиг по умолчанию
"""


def out(message: str = "") -> None:
    """Print, but never crash when the reader went away (head, less, grep -q)."""
    try:
        print(message, flush=True)
    except (BrokenPipeError, ValueError):
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except OSError:
            pass


def cmd_init() -> int:
    path = cfg.config_path()
    if os.path.exists(path):
        out("Конфиг уже есть: %s" % path)
        return 0
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(cfg.DEFAULT_CONFIG)
    out("Создан конфиг: %s" % path)
    return 0


def cmd_doctor() -> int:
    problems = 0
    out("keyb-switch: проверка окружения")
    if not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        out("  ! HYPRLAND_INSTANCE_SIGNATURE не задан (запускайте из Hyprland)")
    try:
        out("  ✓ Hyprland %s" % (hypr.request("version").split("\n")[0] or "running"))
    except hypr.HyprlandError as error:
        out("  ✗ Hyprland недоступен: %s" % error)
        problems += 1

    from .reader import access_error, keyboard_paths

    keyboards = keyboard_paths()
    out("  · клавиатур найдено: %d" % len(keyboards))
    error = access_error()
    if error:
        out("  ✗ %s" % error)
        problems += 1
    else:
        out("  ✓ чтение /dev/input разрешено")

    if shutil.which("wtype"):
        out("  ✓ wtype найден")
    else:
        out("  ✗ wtype не найден (pacman -S wtype)")
        problems += 1

    names = [dictionaries.resolve_dict_name(name) for name in (hypr.layout_names() or ["us"])]
    speller = dictionaries.Speller(cfg.dict_dir(), names)
    status = speller.status()
    speller.close()
    for name, kind in status.items():
        if kind == "missing":
            out("  ✗ словарь %s не найден" % name)
            problems += 1
        else:
            out("  ✓ словарь %s (%s)" % (name, kind))

    out("  · демон %s" % ("работает" if ipc.running() else "не запущен"))
    out("")
    out("Проблем: %d" % problems)
    return 0 if not problems else 1


def cmd_dict() -> int:
    from .dictsetup import install

    status = cmd_dict_status(silent=True)
    out("Словари в %s:" % cfg.dict_dir())
    if not status:
        out("  (пусто)")
    for name, kind in status.items():
        out("  %-8s %s" % (name, kind))
    out("")
    out("Скачиваю словари (hunspell-ru / en_US, ~4 МБ, лицензии в каталоге)...")
    ok = install()
    if not ok:
        out("Не удалось скачать словари. Установите пакеты: sudo pacman -S hunspell-ru hunspell-en_us")
        return 1
    out("Готово.")
    if ipc.running():
        ipc.call({"command": "reload"})
        out("Демон перезапустил словари.")
    return 0


def cmd_dict_status(silent: bool = False) -> dict[str, str]:
    names = [dictionaries.resolve_dict_name(name) for name in (hypr.layout_names() or ["us"])]
    speller = dictionaries.Speller(cfg.dict_dir(), names)
    status = speller.status()
    speller.close()
    return status


UDEV_RULE = "/etc/udev/rules.d/60-keyb-switch.rules"
UDEV_BODY = """\
# keyb-switch: let the user of the active seat read keyboard events.
# Remove this file to revoke the access again.
SUBSYSTEM=="input", KERNEL=="event*", TAG+="uaccess"
"""


def cmd_setup(remove: bool = False) -> int:
    if os.geteuid() != 0:
        out("Нужны права root: запустите через sudo")
        return 2
    if remove:
        if os.path.exists(UDEV_RULE):
            os.unlink(UDEV_RULE)
            out("Удалено: %s" % UDEV_RULE)
        else:
            out("Правило уже отсутствует.")
    else:
        with open(UDEV_RULE, "w", encoding="utf-8") as handle:
            handle.write(UDEV_BODY)
        out("Записано: %s" % UDEV_RULE)
    subprocess.run(["udevadm", "control", "--reload"], check=False)
    subprocess.run(
        ["udevadm", "trigger", "--subsystem-match=input", "--action=change"], check=False
    )
    subprocess.run(["udevadm", "settle"], check=False)
    out("Правила udev перезагружены.")
    return 0


def cmd_status() -> int:
    response = ipc.call({"command": "status"})
    if not response:
        out("Демон не запущен.")
        out("Запустить: keyb-switch start")
        return 1
    status = response.get("status", {})
    out("Автопереключение: %s" % ("включено" if status.get("autocorrect") else "выключено"))
    out("Раскладки:        %s" % ", ".join(status.get("layouts") or []))
    out("Группы xkb:       %s" % ", ".join(status.get("groups") or []))
    active = status.get("active_layout")
    groups = status.get("groups") or []
    if isinstance(active, int) and 0 <= active < len(groups):
        out("Активная:         %s" % groups[active])
    dicts = status.get("dicts") or {}
    out("Словари:          %s" % ", ".join("%s (%s)" % item for item in dicts.items()))
    out("Вставка текста:   %s" % status.get("injector"))
    prefixes = status.get("prefixes") or {}
    if prefixes.get("ready"):
        counts = prefixes.get("prefixes") or {}
        out("Статистика букв:  готова (%s префиксов)" % sum(counts.values()))
    else:
        out("Статистика букв:  не готова (keyb-switch reload)")
    interval = status.get("double_shift_ms") or 0
    out("Двойной Shift:    %s" % ("%d мс" % interval if interval else "выключен"))
    if status.get("live"):
        out("На лету:          да, от %s букв" % status.get("live_min_length"))
    else:
        out("На лету:          нет")
    return 0


def cmd_start(foreground: bool = False) -> int:
    if ipc.running():
        ping = ipc.call({"command": "ping"}, timeout=1)
        if ping and request_pid_alive(ping.get("pid")):
            out("Демон уже запущен.")
            return 0
        # a dead daemon left its socket behind: clear it before starting
        ipc.call({"command": "stop"}, timeout=3)
        for _ in range(30):
            if not ipc.running():
                break
            time.sleep(0.1)
    if foreground:
        from .daemon import main as daemon_main

        return daemon_main(verbose=True)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    entry = os.path.join(root, "bin", cfg.APP)
    log_file = os.path.join(state_dir(), "daemon.log")
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    with open(log_file, "ab") as handle:
        subprocess.Popen(
            [entry, "run", "--verbose"],
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=handle,
            start_new_session=True,
            close_fds=True,
        )
    for _ in range(80):
        if ipc.running():
            out("Демон запущен.")
            return 0
        time.sleep(0.1)
    out("Демон не запустился, смотрите %s" % log_file)
    return 1


def cmd_stop() -> int:
    response = ipc.call({"command": "stop"}, timeout=3)
    if not response:
        out("Демон не запущен.")
        return 0
    # wait until the socket is really gone, so a restart cannot race it
    for _ in range(60):
        if not ipc.running():
            out("Демон остановлен.")
            return 0
        time.sleep(0.1)
    pid = (ipc.call({"command": "ping"}, timeout=1) or {}).get("pid")
    if pid:
        try:
            os.kill(int(pid), 9)
        except OSError:
            pass
    out("Демон остановлен принудительно.")
    return 0


def request_pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
    except (OSError, TypeError, ValueError):
        return False
    return True


def cmd_reload() -> int:
    response = ipc.call({"command": "reload"})
    if not response:
        out("Демон не запущен.")
        return 1
    out("Конфиг перечитан.")
    return 0


def cmd_force() -> int:
    response = ipc.call({"command": "force"})
    if not response:
        out("Демон не запущен.")
        return 1
    if not response.get("ok"):
        notify("keyb-switch", str(response.get("error", "нечего исправлять")))
        return 1
    return 0


def cmd_simulate(text: str, rest: list[str]) -> int:
    layout = None
    words = [item for item in text.split() if not item.startswith("--")]
    text = " ".join(words)
    response = ipc.call({"command": "simulate", "text": text, "layout": layout})
    if not response:
        out("Демон не запущен.")
        return 1
    out(str(response))
    return 0 if response.get("ok") else 1


def cmd_correct() -> int:
    response = ipc.call({"command": "correct"})
    if not response:
        out("Демон не запущен.")
        return 1
    if not response.get("ok"):
        if response.get("error"):
            notify("keyb-switch", str(response["error"]))
        else:
            out(str(response.get("error", "не удалось исправить")))
        return 1
    return 0


def cmd_typo(text: str) -> int:
    response = ipc.call({"command": "typo", "text": text})
    if response is None:
        out("Демон не запущен.")
        return 1
    if not response.get("ok"):
        out(str(response.get("error", "ошибка")))
        return 1
    out(response["result"])
    return 0


def cmd_autocorrect(value: str | None) -> int:
    request = {"command": "autocorrect"}
    if value is not None:
        request["value"] = value.lower() in ("on", "1", "true", "yes", "вкл")
    response = ipc.call(request)
    if not response:
        out("Демон не запущен.")
        return 1
    state = "включено" if response.get("autocorrect") else "выключено"
    out("Автопереключение %s." % state)
    return 0


def cmd_log() -> int:
    from .engine import log_path

    path = log_path()
    if not os.path.exists(path):
        out("Журнал пуст.")
        return 0
    with open(path, encoding="utf-8", errors="replace") as handle:
        lines = handle.readlines()[-30:]
    for line in lines:
        out(line.rstrip("\n"))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        out(USAGE)
        return 0
    if argv[0] in ("-V", "--version", "version"):
        out("%s %s" % (cfg.APP, __version__))
        return 0
    command = argv[0]
    rest = argv[1:]

    if command == "run":
        from .daemon import main as daemon_main

        return daemon_main(
            verbose="--verbose" in rest or "-v" in rest,
            no_input="--no-input" in rest,
        )
    if command == "init":
        return cmd_init()
    if command == "doctor":
        return cmd_doctor()
    if command == "setup":
        return cmd_setup(remove="--remove" in rest)
    if command == "dict":
        return cmd_dict()
    if command == "status":
        return cmd_status()
    if command == "start":
        return cmd_start(foreground="--foreground" in rest)
    if command == "stop":
        return cmd_stop()
    if command == "restart":
        cmd_stop()
        return cmd_start()
    if command == "reload":
        return cmd_reload()
    if command == "correct":
        return cmd_correct()
    if command == "force":
        return cmd_force()
    if command == "simulate":
        return cmd_simulate(" ".join(rest), rest)
    if command == "typo":
        if not rest:
            out("Укажите текст: keyb-switch typo ghbdtn")
            return 1
        return cmd_typo(" ".join(rest))
    if command == "autocorrect":
        return cmd_autocorrect(rest[0] if rest else None)
    if command == "log":
        return cmd_log()
    out("Неизвестная команда: %s" % command)
    out(USAGE)
    return 1
