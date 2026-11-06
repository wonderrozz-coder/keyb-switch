---
name: Не сработало / ошибка
about: Сообщить о проблеме с автопереключением
title: ''
labels: bug
assignees: ''
---

**Что набрали** (например `ghbdtn`):

**Что получили** (например `привет` или ничего):

**Где** (приложение/класс окна):

```
hyprctl -j activewindow | jq -r .class
```

**Диагностика**

```
keyb-switch doctor
keyb-switch log
```

<details>
<summary>Конфиг</summary>

```toml
cat ~/.config/keyb-switch/config.toml
```
</details>

**Версия**

```
keyb-switch --version 2>/dev/null || git -C . log -1 --oneline
python3 --version
hyprctl version | head -1
```
