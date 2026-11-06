#!/usr/bin/env bash
# Grant the active-seat user read access to keyboard events (needed by the daemon).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(dirname "$here")"
target=/etc/udev/rules.d/60-keyb-switch.rules

if [[ $EUID -ne 0 ]]; then
  echo "Нужны права root. Запустите: sudo $0" >&2
  exit 1
fi

install -Dm644 "$root/packaging/udev.rules" "$target"
udevadm control --reload
udevadm trigger --subsystem-match=input --action=change
udevadm settle

echo "Установлено: $target"
echo "Проверьте:   $root/bin/keyb-switch doctor"
