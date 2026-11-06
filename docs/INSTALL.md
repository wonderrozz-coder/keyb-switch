# Установка

## Быстрая установка (рекомендуется)

```bash
git clone https://github.com/<ваш-логин>/keyb-switch.git
cd keyb-switch
./scripts/install.sh
```

Скрипт:

1. проверит зависимости;
2. создаст локальный virtualenv и поставит `python-evdev`;
3. попросит пароль sudo и установит udev-правило (доступ к `/dev/input`);
4. подготовит словари (если их нет);
5. запустит демон.

## Установка вручную, по шагам

### 1. Зависимости

```bash
sudo pacman -S --needed python wtype hunspell wl-clipboard
```

| Пакет | Зачем |
| --- | --- |
| `python` | язык программы |
| `wtype` | вставка исправленного текста (протокол виртуальной клавиатуры Wayland) |
| `hunspell` | проверка орфографии (используется библиотека `libhunspell`) |
| `wl-clipboard` | исправление **выделенного** текста по двойному Shift |

`python-evdev` ставится в локальный virtualenv автоматически (или
`sudo pacman -S python-evdev`, если хотите системный).

### 2. Права на чтение клавиатуры

Без этого шага демон не увидит нажатий:

```bash
sudo ./scripts/install-udev.sh
```

Создаётся `/etc/udev/rules.d/60-keyb-switch.rules`:

```
SUBSYSTEM=="input", KERNEL=="event*", TAG+="uaccess"
```

`uaccess` даёт доступ **только пользователю активной сессии**, а не всем.
Снять: `sudo ./scripts/uninstall-udev.sh`.

### 3. Словари

```bash
./bin/keyb-switch dict
```

Скачивает словари в `dict/` (~4 МБ). Если в репозитории они уже есть — шаг
не нужен. Альтернатива: `sudo pacman -S hunspell-ru hunspell-en_us`, тогда
берутся системные.

### 4. Запуск

```bash
./bin/keyb-switch start
./bin/keyb-switch status
```

## Автозапуск и горячая клавиша (Hyprland / Omarchy)

В `~/.config/hypr/autostart.lua`:

```lua
o.launch_on_start("/полный/путь/к/keyb-switch/bin/keyb-switch start")
```

В `~/.config/hypr/bindings.lua` (необязательно, есть двойной Shift):

```lua
o.bind("SUPER + SHIFT + ALT + P", "Fix keyboard layout", "/полный/путь/к/keyb-switch/bin/keyb-switch force")
```

После правки: `hyprctl reload && hyprctl configerrors`.

## Другие дистрибутивы

Программа не зависит от Omarchy: нужны только Hyprland (или другой
wlroots-композит **с включённым протоколом virtual-keyboard**), `wtype`
и `libhunspell`.

## Удаление

```bash
./bin/keyb-switch stop
sudo ./scripts/uninstall-udev.sh
rm -rf ~/.config/keyb-switch ~/.local/state/keyb-switch
# и удалить строки автозапуска/хоткея из конфигов Hyprland
```
