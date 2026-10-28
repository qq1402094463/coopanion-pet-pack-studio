#!/usr/bin/env bash
# Pet Pack Studio for Coopanion —— macOS / Linux 启动脚本
set -e
cd "$(dirname "$0")"

PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done

if [ -z "$PY" ]; then
  echo "[!] 没找到 python3。先装 Python 3.10+：https://www.python.org/downloads/"
  exit 1
fi

if ! "$PY" -c "import PIL" >/dev/null 2>&1; then
  echo "第一次运行，正在装 Pillow ..."
  "$PY" -m pip install --quiet --upgrade pip || true
  "$PY" -m pip install --quiet pillow
fi

exec "$PY" studio/server.py "$@"
