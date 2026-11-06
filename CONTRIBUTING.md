# Как участвовать

Спасибо, что заглянули. Правил мало, они простые.

## Сообщить о проблеме

Лучше всего через issue: что набрали, что получили, класс окна, `keyb-switch log`
и `keyb-switch doctor`. Шаблон подставится сам.

Понимаю, что для «переключило не туда» нужен пример: одно слово и раскладка
уже очень помогают.

## Изменить код

```bash
git clone https://github.com/wonderrozz-coder/keyb-switch.git
cd keyb-switch
python3 -m venv .venv
./.venv/bin/pip install evdev
./.venv/bin/python -m unittest discover -s tests -v
```

Перед pull request:

```bash
./.venv/bin/python -m unittest discover -s tests -v   # тесты должны проходить
./.venv/bin/python scripts/benchmark.py 500           # точность не должна падать
```

Просьба придерживаться стиля: комментарии объясняют **почему**, а не что;
имена по-английски, сообщения пользователю — по-русски.

### Где что менять

| Задача | Файл |
| --- | --- |
| политика решения «в какой раскладке слово» | `kbswitch/engine.py`, `_judge` |
| пороги и веса | `kbswitch/frequency.py` |
| другая раскладка/язык | `kbswitch/dictionaries.py`, `kbswitch/prefixes.py` |
| другой композитор | `kbswitch/hypr.py`, `kbswitch/watcher.py` |
| способ вставки текста | `kbswitch/inject.py` |
| чтение клавиатур | `kbswitch/reader.py` |

Описание архитектуры: [docs/DESIGN.md](docs/DESIGN.md).

## Лицензия вклада

Отправляя pull request, вы соглашаетесь, что ваш код распространяется под MIT
(см. [LICENSE](LICENSE)).
