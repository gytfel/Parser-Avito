#!/usr/bin/env bash
# Запуск на macOS / Linux: ./start.sh  (меню)  или  ./start.sh scan -c phones -q "iphone 13"
set -euo pipefail
cd "$(dirname "$0")"

PY=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 \
        && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
        PY="$candidate"
        break
    fi
done
if [ -z "$PY" ]; then
    echo "Не найден Python 3.10 или новее. Установите его: https://www.python.org/downloads/"
    echo "(macOS: brew install python, Ubuntu/Debian: sudo apt install python3 python3-venv)"
    exit 1
fi

if [ ! -x .venv/bin/python ]; then
    echo "Первый запуск: создаю окружение в папке .venv ..."
    if ! "$PY" -m venv .venv; then
        echo "Не удалось создать окружение. На Ubuntu/Debian установите: sudo apt install python3-venv"
        rm -rf .venv
        exit 1
    fi
fi

# Зависимости ставятся при первом запуске и после изменения requirements.txt
if ! cmp -s requirements.txt .venv/requirements.installed; then
    echo "Устанавливаю зависимости, это займёт пару минут ..."
    .venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt
    cp requirements.txt .venv/requirements.installed
fi

export AVITO_FLIP_CMD=./start.sh
exec .venv/bin/python -m avito_flip "$@"
