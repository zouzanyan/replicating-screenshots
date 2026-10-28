#!/usr/bin/env bash
# 兼容入口：实现在 render.py 里（跨平台，Windows 上也能直接跑）。
#
#   bash scripts/render.sh <in.html> <out.png> [宽=390] [高=844] [dpr=3]
#
# 等价于：python3 scripts/render.py <in.html> <out.png> [宽] [高] [dpr]
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

# 有的环境只有 python 没有 python3（Windows 的 Git Bash 就是这样）
PY=${PYTHON:-}
if [ -z "$PY" ]; then
  for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1; then PY=$c; break; fi
  done
fi
[ -n "$PY" ] || { echo "找不到 python3：装一个，或用 PYTHON=/path/to/python 指定" >&2; exit 1; }

exec "$PY" "$here/render.py" "$@"
