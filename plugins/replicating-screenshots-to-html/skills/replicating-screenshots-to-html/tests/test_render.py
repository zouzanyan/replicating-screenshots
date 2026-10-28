#!/usr/bin/env python3
"""render.py 的端到端测试：视口宽度、dpr、裁剪位置。

用一张「顶部长条铺满视口宽度」的探针页：长条的实际像素宽度就等于
iframe 的 CSS 视口宽度 × dpr。这正好抓住了历史上那个 `--window-size`
被最小窗口宽夹到 500 的 bug —— 那时候长条会画成 500 宽再被裁成 390。

没有装 Chrome 的机器上这 4 条会 skip（不是失败）：量测和追踪的 77 条
不依赖浏览器，照样跑。`render.sh` 是这个脚本的 bash 包装，两条命令等价。

    python3 -I tests/test_render.py
"""

import os
import subprocess
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
RENDER = os.path.join(HERE, "..", "scripts", "render.py")


def _have_chrome():
    try:
        r = subprocess.run([sys.executable, RENDER, "--check"],
                           capture_output=True, text=True)
    except OSError:
        return False
    return r.returncode == 0


HAVE_CHROME = _have_chrome()

PROBE = """<!doctype html><meta charset="utf-8">
<style>html,body{margin:0;padding:0;background:#fff}
#bar{width:100%;height:50px;background:#000}
#edge{position:absolute;left:0;top:0;width:1px;height:10px;background:#f00}</style>
<div id="bar"></div><div id="edge"></div>
"""


@unittest.skipUnless(HAVE_CHROME, "没有 Chrome，跳过渲染测试（其余 77 条不受影响）")
class TestRender(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.html = os.path.join(self.dir, "probe.html")
        with open(self.html, "w") as f:
            f.write(PROBE)

    def render(self, w, h, dpr):
        out = os.path.join(self.dir, f"o_{w}_{dpr}.png")
        r = subprocess.run([sys.executable, RENDER, self.html, out,
                            str(w), str(h), str(dpr)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        with Image.open(out) as im:
            return np.asarray(im.convert("L")).astype(float), out

    def test_viewport_is_the_requested_width_not_clamped(self):
        """320/390/414 都低于 Chrome 的最小窗口宽 500，必须仍然是各自的宽度。"""
        for w in (320, 390, 414):
            img, out = self.render(w, 844, 3)
            with Image.open(out) as im:
                self.assertEqual(im.size, (w * 3, 844 * 3))
            row = img[60]                      # 长条内部（50px 高，取 y=20 CSS）
            black = np.where(row < 40)[0]
            self.assertTrue(len(black) > 0, "探针长条没画出来")
            width_css = (black[-1] + 1) / 3.0
            self.assertAlmostEqual(width_css, w, delta=1.0,
                                   msg=f"视口宽度 {width_css:.1f} != {w}")

    def test_origin_is_top_left_exactly(self):
        """左上角必须是 (0,0)：红色 1px 标记应落在第 0 列。"""
        img, _ = self.render(390, 844, 3)
        self.assertLess(img[3, 0], 200, "左上角不是页面原点，说明裁剪/偏移有问题")

    def test_dpr(self):
        for dpr in (2, 3):
            img, out = self.render(390, 844, dpr)
            with Image.open(out) as im:
                self.assertEqual(im.size, (390 * dpr, 844 * dpr))
            black = np.where(img[int(20 * dpr)] < 40)[0]
            self.assertAlmostEqual((black[-1] + 1) / dpr, 390, delta=1.0)

    def test_page_taller_than_viewport_is_clipped(self):
        img, _ = self.render(390, 300, 3)
        self.assertEqual(img.shape, (900, 1170))
        self.assertLess(img[50, 600], 40, "减裁后顶部应仍是长条")


if __name__ == "__main__":
    unittest.main(verbosity=2)
