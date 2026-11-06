"""Configuration handling for keyb-switch."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field

APP = "keyb-switch"
SOCKET_NAME = "keyb-switch.sock"


def home() -> str:
    return os.path.expanduser("~")


def data_dir() -> str:
    """Where dictionaries and caches live.

    Order: explicit override, the git checkout next to the package (so running
    from a clone uses its own dict/), then the usual XDG data directory.
    """
    override = os.environ.get("KEYB_SWITCH_DATA")
    if override:
        return override
    checkout = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dict")
    if os.path.isdir(checkout):
        return checkout
    xdg = os.environ.get("XDG_DATA_HOME") or os.path.join(home(), ".local", "share")
    return os.path.join(xdg, APP, "dict")


def config_path() -> str:
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home(), ".config")
    return os.path.join(xdg, APP, "config.toml")


def socket_path() -> str:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return os.path.join(runtime, SOCKET_NAME)


def dict_dir() -> str:
    return data_dir()


DEFAULT_CONFIG = """\
# keyb-switch - замена Punto Switcher для Hyprland/Wayland
#
# Автопереключение раскладки после слова (как в Punto Switcher).
# Выключить можно и на лету: keyb-switch autocorrect toggle
autocorrect = true

# Минимальная длина слова в буквах. 2 — как в Punto Switcher: короткие слова
# вроде "пщ" -> "go" тоже важны, а мусор отсекает сравнение доказательств.
min_word_length = 2

# Словари в порядке раскладок из input:kb_layout ("us,ru" -> en, ru).
dicts = ["en_US", "ru_RU"]

# Показывать уведомление после автопереключения.
notify_autocorrect = false

# Уведомления (всплывающие окошки). По умолчанию выключены: исправление
# видно прямо в тексте, окошко только мешает.
notifications = false

# Переключение «на лету»: исправлять слово до пробела, по мере набора.
live = true

# Сколько букв должно быть набрано, чтобы решение считалось надёжным.
live_min_length = 4

# Не проверять чаще, чем раз в N миллисекунд (защита от лишних вставок).
live_interval_ms = 120

# Пауза между нажатиями при вставке исправленного слова, миллисекунды.
inject_delay_ms = 6

# Двойной Shift (два быстрых нажатия подряд) принудительно исправляет
# набираемое слово и переключает раскладку. 0 отключает.
double_shift_ms = 400

# Насколько давно должен быть набран пробел, чтобы двойной Shift ещё
# исправлял последнее слово (секунды).
force_window_s = 6

# Окна, где перехват не работает: регулярные выражения по классу окна.
# Пример: blacklist = ["^steam_app_", "gamescope", "^org\\.virt-manager"]
blacklist = []

# Не автопереключать в этих приложениях (регулярные выражения по классу окна).
# Терминалы НЕ включены намеренно: в терминале тоже набирают текст в неверной
# раскладке, и это одна из главных причин держать автопереключение.
autocorrect_blacklist = [
  # менеджеры паролей: там не видно, что набрано
  "^1Password$",
  "^Bitwarden$",
  "^KeePassXC$",
  # игры
  "^steam_app_[0-9]+$",
  "^gamescope$",
]
"""


@dataclass
class Config:
    autocorrect: bool = True
    min_word_length: int = 2
    dicts: list[str] = field(default_factory=lambda: ["en_US", "ru_RU"])
    notify_autocorrect: bool = False
    notifications: bool = False
    live: bool = True
    live_min_length: int = 4
    live_interval_ms: int = 120
    inject_delay_ms: int = 6
    double_shift_ms: int = 400
    force_window_s: float = 6.0
    blacklist: list[str] = field(default_factory=list)
    autocorrect_blacklist: list[str] = field(default_factory=list)

    @classmethod
    def load(cls) -> "Config":
        cfg = cls()
        path = config_path()
        try:
            with open(path, "rb") as handle:
                raw = tomllib.load(handle)
        except FileNotFoundError:
            return cfg
        except (OSError, tomllib.TOMLDecodeError):
            return cfg
        for key, value in raw.items():
            if hasattr(cfg, key):
                setattr(cfg, key, value)
        return cfg
