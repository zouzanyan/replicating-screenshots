#!/usr/bin/env python3
"""用 Chrome headless 按「固定 CSS 视口 + 指定设备像素比」截图。

    python3 scripts/render.py <in.html> <out.png> [宽=390] [高=844] [dpr=3]

关键点：dpr 必须和原图一致，否则抗锯齿、hinting、亚像素定位全不一样，
逐像素比较就没有意义了。iPhone 截图一般是 @3x。

为什么要套一层 iframe：
  Chrome 的 --window-size 会被窗口最小宽度夹住（macOS 上实测 390 → 500），
  但截图仍然按请求的尺寸裁，于是页面按 500 CSS px 排版、只截到左边 390，
  所有居中元素整体右移 55px，x 坐标全废。用 iframe 固定成 宽×高，
  页面拿到的就是真正的 390×844 视口，窗口多大都无所谓，截完再裁。
  别指望 --window-size 单独就能定视口 —— 那正是这个脚本踩过的坑。

用 Python 而不是 bash 写的理由：Windows 上没有 bash，而这里的每一步
（写 wrapper、调 Chrome、按 dpr 裁图）本来都跟平台无关。`render.sh` 现在
只是这个脚本的包装，macOS / Linux 上两条命令等价。
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

USAGE = "用法: render.py <in.html> <out.png> [宽] [高] [dpr]"

# Chrome 卡住（页面在等外部资源、或死循环）时别把 agent 一起挂死。
TIMEOUT = int(os.environ.get("RENDER_TIMEOUT", "120"))

# 探测顺序：CHROME 环境变量 → macOS → Linux → Windows。
# expandvars 在非 Windows 上不会展开 %VAR%，那几条自然就不存在，不用分支。
WINDOWS_CANDIDATES = [
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
]


def find_chrome():
    """返回可执行的 Chrome 路径；找不到就带着说明抛 SystemExit。"""
    env = os.environ.get("CHROME")
    if env and not os.access(env, os.X_OK):
        print(f"!! CHROME={env} 不可执行，改为自动探测", file=sys.stderr)
        env = None
    if env:
        return env

    cands = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("chrome"),
    ]
    cands += [os.path.expandvars(p) for p in WINDOWS_CANDIDATES]
    for c in cands:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    raise SystemExit("找不到 Chrome：装一个，或用 CHROME=/path/to/chrome 指定")


def render(inp, out, w, h, dpr):
    chrome = find_chrome()
    inp = Path(inp).resolve()
    if not inp.is_file():
        raise SystemExit(f"!! 找不到输入文件：{inp}")

    tmp = Path(tempfile.mkdtemp())
    try:
        wrap = tmp / "wrap.html"
        wrap.write_text(
            "<!doctype html><meta charset=\"utf-8\">\n"
            "<style>\n"
            "  html,body{margin:0;padding:0;background:#fff;overflow:hidden}\n"
            f"  iframe{{display:block;border:0;width:{w}px;height:{h}px}}\n"
            "</style>\n"
            f"<iframe src=\"{inp.as_uri()}\"></iframe>\n",
            encoding="utf-8",
        )
        raw = tmp / "raw.png"
        cmd = [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--allow-file-access-from-files",
            f"--force-device-scale-factor={dpr:g}",
            f"--window-size={max(w, 520)},{h + 200}",
            f"--screenshot={raw}",
            wrap.as_uri(),
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               errors="replace", timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            print(f"!! Chrome 超过 {TIMEOUT}s 没返回：{chrome}", file=sys.stderr)
            print("   多半是页面在等外部资源或死循环；用 RENDER_TIMEOUT=秒数 放宽上限",
                  file=sys.stderr)
            raise SystemExit(1)
        if r.returncode != 0:
            print(f"!! Chrome 退出非零：{chrome}", file=sys.stderr)
            print("\n".join(r.stderr.strip().splitlines()[-10:]), file=sys.stderr)
            raise SystemExit(1)
        if not raw.is_file() or raw.stat().st_size == 0:
            print(f"!! Chrome 没有产出截图：{chrome}", file=sys.stderr)
            print("\n".join(r.stderr.strip().splitlines()[-10:]), file=sys.stderr)
            raise SystemExit(1)

        need = (int(round(w * dpr)), int(round(h * dpr)))
        with Image.open(raw) as im:
            if im.width < need[0] or im.height < need[1]:
                raise SystemExit(f"!! 截图 {im.size} 小于需要的 {need}，窗口太小或页面溢出")
            im.crop((0, 0, *need)).save(out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"{out}  {need[0]}x{need[1]}  ({w} x {h} CSS px @{dpr:g}x)  视口由 iframe 固定  OK")


def main(argv):
    if len(argv) >= 2 and argv[1] == "--check":
        print(find_chrome())
        return 0
    if len(argv) < 3:
        raise SystemExit(USAGE)
    inp, out = argv[1], argv[2]
    try:
        w = int(argv[3]) if len(argv) > 3 else 390
        h = int(argv[4]) if len(argv) > 4 else 844
        dpr = float(argv[5]) if len(argv) > 5 else 3.0
    except ValueError:
        raise SystemExit(USAGE)
    if w <= 0 or h <= 0 or dpr <= 0:
        raise SystemExit(USAGE)
    render(inp, out, w, h, dpr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
