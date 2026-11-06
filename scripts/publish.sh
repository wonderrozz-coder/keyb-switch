#!/usr/bin/env bash
# Publish this repository to GitHub.
#
# Needs only `gh auth login` once (see the message at the end). No SSH keys, no
# manual token handling: gh stores the credential in its own keyring.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(dirname "$here")"
cd "$root"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
die() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

REPO_NAME="${REPO_NAME:-keyb-switch}"

say "Проверка инструментов"
command -v git >/dev/null || die "git не установлен"
command -v gh  >/dev/null || die "GitHub CLI не установлен: sudo pacman -S github-cli"

if ! gh auth status >/dev/null 2>&1; then
  cat <<'TXT'
Вы не вошли в GitHub. Выполните один раз (сами, в терминале):

    gh auth login

Выберите:
  ? What account do you want to log into?      GitHub.com
  ? Preferred protocol for Git operations?     HTTPS
  ? Authenticate Git with your GitHub creds?   Yes
  ? How would you like to authenticate?        Login with a web browser

Скопируйте код из терминала, вставьте в браузере, разрешите доступ.
После этого запустите этот скрипт снова.

Токен и пароль вводить мне в чат не нужно и не следует.
TXT
  exit 1
fi

LOGIN="$(gh api user -q .login)"
say "Вы вошли как: $LOGIN"

say "Подстановка логина в файлы"
for file in README.md pyproject.toml CONTRIBUTING.md docs/INSTALL.md; do
  [[ -f "$file" ]] || continue
  if grep -q "<логин>" "$file"; then
    sed -i "s|<логин>|$LOGIN|g" "$file"
    echo "  обновлён: $file"
  fi
done

say "Проверка перед публикацией"
problems=0
if grep -rqiE "api[_-]?key *=|token *= *['\"]|BEGIN .*PRIVATE KEY" \
      --include="*.py" --include="*.sh" --include="*.toml" kbswitch scripts 2>/dev/null; then
  echo "  ! похоже на секрет в коде" >&2
  problems=$((problems + 1))
fi
if [[ -x .venv/bin/python ]]; then
  .venv/bin/python -m unittest discover -s tests >/dev/null 2>&1 \
    && echo "  ok: тесты проходят" \
    || { echo "  ! тесты не проходят — публикация остановлена" >&2; die "исправьте тесты"; }
else
  echo "  ! нет .venv — тесты не проверены (python3 -m venv .venv && .venv/bin/pip install evdev)"
fi
echo "  ok: файлов в индексе: $(git ls-files | wc -l)"
echo "  размер: $(du -sh --exclude=.venv --exclude=.git . | cut -f1)"

say "Коммит изменений"
if [[ -n "$(git status --porcelain)" ]]; then
  git add -A
  git commit -q -m "Подстановка логина GitHub в ссылки" || true
  echo "  создан коммит"
else
  echo "  нечего коммитить"
fi

say "Репозиторий на GitHub"
if git remote get-url origin >/dev/null 2>&1; then
  echo "  origin уже настроен: $(git remote get-url origin)"
else
  echo "  Создать репозиторий '$REPO_NAME'?"
  read -rp "  Публичный? [Y/n] " answer
  case "$answer" in
    [Nn]*) visibility=--private ;;
    *)     visibility=--public ;;
  esac
  gh repo create "$REPO_NAME" "$visibility" --source=. --remote=origin \
    --description "Автоматическое исправление раскладки для Hyprland/Wayland — замена Punto Switcher"
  echo "  создан"
fi

say "Отправка"
git push -u origin main

URL="$(gh repo view --json url -q .url 2>/dev/null || echo "https://github.com/$LOGIN/$REPO_NAME")"
cat <<TXT

\033[32mГотово: $URL\033[0m

Что можно сделать на странице репозитория:
  • добавить описание и темы (Topics): keyboard, wayland, hyprland,
    punto-switcher, layout, hunspell, russian
  • включить Issues (для шаблонов из .github/ они уже готовы)
  • нажать Star себе — пусть будет
TXT
