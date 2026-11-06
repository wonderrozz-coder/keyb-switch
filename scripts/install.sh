#!/usr/bin/env bash
# One-shot installer: dependencies, virtualenv, udev rule, dictionaries, daemon.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(dirname "$here")"
cd "$root"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
fail() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

say "Проверка зависимостей"
command -v pacman >/dev/null || echo "  ! не Arch-подобная система: зависимости проверьте вручную (docs/INSTALL.md)"
missing=()
for cmd in wtype; do
  command -v "$cmd" >/dev/null || missing+=("$cmd")
done
if [[ ! -e /usr/lib/libhunspell.so && ! -e /usr/lib/libhunspell-1.7.so.0 \
      && ! -e /usr/lib/libhunspell-1.7.so ]]; then
  missing+=(hunspell)
fi
command -v wl-paste >/dev/null || missing+=(wl-clipboard)
if ((${#missing[@]})); then
  echo "  Не хватает: ${missing[*]}"
  if command -v pacman >/dev/null; then
    echo "  Ставлю: sudo pacman -S --needed ${missing[*]}"
    sudo pacman -S --needed --noconfirm "${missing[@]}" || fail "не удалось установить зависимости"
  else
    fail "установите их вручную и запустите снова"
  fi
fi
echo "  ok"

say "Виртуальное окружение Python"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
./.venv/bin/pip install --quiet --upgrade pip || true
./.venv/bin/pip install --quiet evdev || fail "не удалось установить python-evdev"
echo "  ok: $(./.venv/bin/python -c 'import evdev; print("python-evdev", evdev.__version__ if hasattr(evdev,"__version__") else "ok")')"

say "Права на чтение клавиатуры (udev)"
if [[ -e /etc/udev/rules.d/60-keyb-switch.rules ]]; then
  echo "  уже установлено"
else
  sudo ./scripts/install-udev.sh
fi

say "Словари"
if [[ -f dict/ru_RU.dic && -f dict/en_US.dic ]]; then
  echo "  уже на месте"
else
  ./bin/keyb-switch dict
fi

say "Самопроверка"
./bin/keyb-switch doctor || true

say "Запуск"
./bin/keyb-switch restart

cat <<'TXT'

Готово.

  keyb-switch status     — состояние
  keyb-switch log        — журнал исправлений
  keyb-switch doctor     — диагностика

Двойной Shift       — принудительно переложить слово/выделение
SUPER+SHIFT+ALT+P   — то же по кнопке (добавьте биндинг, см. docs/INSTALL.md)

Автозапуск: добавьте в ~/.config/hypr/autostart.lua
  o.launch_on_start("ПОЛНЫЙ_ПУТЬ/bin/keyb-switch start")

TXT
