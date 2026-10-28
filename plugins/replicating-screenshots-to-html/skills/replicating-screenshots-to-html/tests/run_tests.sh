#!/usr/bin/env bash
# 跑全部测试：bash tests/run_tests.sh
# 改了 scripts/ 下任何东西都要先跑这个。
# 没有 bash 的环境（Windows）：三个文件分别跑 python -I tests/test_*.py
set -uo pipefail
cd "$(dirname "$0")/.."

# 解释器：PYTHON 环境变量 → python3 → python。
# Windows 的 Git Bash / MSYS 里常常只有 python 没有 python3。
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
  done
fi
if [ -z "$PY" ]; then
  echo "!! 找不到 python，用 PYTHON=/path/to/python 指定"
  exit 1
fi

fail=0
# -I 是隔离模式（-E -s -P）：忽略 PYTHONPATH 和用户 site 目录。
# 所以依赖必须装在 site-packages 或 venv 里；pip --target + PYTHONPATH 那种装法会找不到 numpy。
for t in tests/test_measure.py tests/test_trace_shape.py tests/test_render.py; do
  echo "=== ${t#tests/}"
  out=$("$PY" -I "$t" 2>&1)
  rc=$?
  echo "$out" | grep -E "^(FAIL|ERROR|Ran |OK)|AssertionError" | head -20
  if [ $rc -ne 0 ]; then
    fail=1
    echo "$out" | grep -A3 -E "^(FAIL|ERROR):" | head -40
  fi
done

if [ $fail -ne 0 ]; then
  echo "!! 有测试失败"
  exit 1
fi
echo "全部通过"
