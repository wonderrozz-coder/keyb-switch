#!/usr/bin/env bash
# Remove the udev rule installed by install-udev.sh.
set -euo pipefail

target=/etc/udev/rules.d/60-keyb-switch.rules

if [[ $EUID -ne 0 ]]; then
  echo "Нужны права root. Запустите: sudo $0" >&2
  exit 1
fi

rm -f "$target"
udevadm control --reload
udevadm trigger --subsystem-match=input --action=change
udevadm settle
echo "Удалено: $target"
