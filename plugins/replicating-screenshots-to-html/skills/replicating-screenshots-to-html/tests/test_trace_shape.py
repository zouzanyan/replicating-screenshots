#!/usr/bin/env python3
"""trace_shape.py 的单元测试 + 端到端测试。

合成图的覆盖度是解析算出来的，所以 0.5 等值线的**真实位置已知**，
追踪结果的面积/bbox 可以直接对着解析解校验。

所有几何参数都用 CSS px 表述，内部再乘 dpr 换算成设备像素 ——
测试里同时出现两套单位是最容易写错的地方。

    python3 -I tests/test_trace_shape.py
"""

import os
import re
import subprocess
import sys
import tempfile
import time
import unittest

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "scripts", "trace_shape.py")


def parse(out):
    """从 CLI 输出里取出 loops（CSS px）与 viewBox。"""
    vb = re.search(r'viewBox="0 0 ([-\d.]+) ([-\d.]+)"', out)
    d = re.search(r'd="([^"]+)"', out)
    loops = []
    if d:
        for m in re.finditer(r"M([-\d.]+),([-\d.]+)((?:L[-\d.]+,[-\d.]+)*)Z", d.group(1)):
            pts = [(float(m.group(1)), float(m.group(2)))]
            pts += [(float(a), float(b)) for a, b in re.findall(r"L([-\d.]+),([-\d.]+)", m.group(3))]
            loops.append(np.array(pts))
    return loops, (float(vb.group(1)), float(vb.group(2))) if vb else None


def poly_area(p):
    x, y = p[:, 0], p[:, 1]
    return 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def inside(pt, p):
    x, y = pt
    c = False
    for i in range(len(p)):
        x1, y1 = p[i]
        x2, y2 = p[(i + 1) % len(p)]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            c = not c
    return c


def evenodd_area(loops):
    """even-odd 填充面积：外环为正，被更大的环包住的环为负。

    只拿**面积更大**的环做包含判断：圆环的内环质心同时也是外环的中心，
    若不分大小，内环会被算成「包含在外环里」而外环也被算成「包含在内环里」。
    """
    order = sorted(loops, key=poly_area, reverse=True)
    tot = 0.0
    for i, p in enumerate(order):
        depth = sum(1 for q in order[:i] if inside(p.mean(axis=0), q))
        tot += (-1) ** depth * poly_area(p)
    return tot


def grid(size_css, dpr):
    """返回每个设备像素中心的 CSS 坐标。"""
    n = int(round(size_css * dpr))
    ys, xs = np.mgrid[0:n, 0:n]
    return (xs + 0.5) / dpr, (ys + 0.5) / dpr


def circle_cov(size_css, dpr, cx, cy, r):
    X, Y = grid(size_css, dpr)
    d = np.hypot(X - cx, Y - cy) * dpr          # 设备像素距离
    return np.clip(0.5 + (r * dpr - d), 0, 1)


def ring_cov(size_css, dpr, cx, cy, r_out, r_in):
    X, Y = grid(size_css, dpr)
    d = np.hypot(X - cx, Y - cy) * dpr
    return (np.clip(0.5 + (r_out * dpr - d), 0, 1)
            * np.clip(0.5 + (d - r_in * dpr), 0, 1))


def save_gray(cov, bg, text, path):
    Image.fromarray(np.clip(bg + (text - bg) * cov, 0, 255).astype(np.uint8), "L").save(path)
    return path


def save_alpha(a, path):
    rgba = np.zeros((*a.shape, 4), np.uint8)
    rgba[:, :, 3] = np.clip(a * 255, 0, 255).astype(np.uint8)
    Image.fromarray(rgba, "RGBA").save(path)
    return path


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def trace(self, img, *args):
        return subprocess.run([sys.executable, "-I", SCRIPT, "--image", img, *args],
                              capture_output=True, text=True)


class TestCoverageMode(Base):
    DPR = 3

    def build(self, cov, bg=255, text=40, name="s.png"):
        return save_gray(cov, bg, text, os.path.join(self.dir, name))

    def test_circle_area_and_bbox(self):
        """半径 6 的圆，0.5 等值线就是 r=6，面积应为 113.097。"""
        R, C = 6.0, 20.0
        p = self.build(circle_cov(40, self.DPR, C, C, R))
        r = self.trace(p, "--x", "10", "30", "--y", "10", "30", "--dpr", str(self.DPR),
                       "--text-luma", "40", "--blur", "0")
        self.assertEqual(r.returncode, 0, r.stderr)
        loops, vb = parse(r.stdout)
        self.assertEqual(len(loops), 1)
        self.assertEqual(vb, (20.0, 20.0))
        area = evenodd_area(loops)
        self.assertLess(abs(area / (np.pi * R * R) - 1), 0.015, f"面积 {area:.3f}")
        # 输出坐标相对区域原点，所以圆心 (20,20) 减去窗口原点 (10,10) 得 (10,10)
        xs = np.concatenate([l[:, 0] for l in loops])
        self.assertAlmostEqual(xs.min(), C - R - 10, delta=0.1)
        self.assertAlmostEqual(xs.max(), C + R - 10, delta=0.1)

    def test_dark_background_gives_identical_geometry(self):
        R, C = 6.0, 20.0
        cov = circle_cov(40, self.DPR, C, C, R)
        pl = self.build(cov, 255, 40, "l.png")
        pd = self.build(cov, 20, 240, "d.png")
        args = ("--x", "10", "30", "--y", "10", "30", "--dpr", str(self.DPR), "--blur", "0")
        a = parse(self.trace(pl, *args, "--text-luma", "40").stdout)[0]
        b = parse(self.trace(pd, *args, "--bg", "20", "--text-luma", "240").stdout)[0]
        self.assertEqual(len(a), len(b))
        self.assertLess(abs(evenodd_area(a) / evenodd_area(b) - 1), 0.01)

    def test_ring_has_hole_and_evenodd_area(self):
        """圆环：外 r=8、内 r=4，必须有 2 个环，面积 = π(64-16)。"""
        RO, RI, C = 8.0, 4.0, 20.0
        p = self.build(ring_cov(40, self.DPR, C, C, RO, RI))
        r = self.trace(p, "--x", "10", "30", "--y", "10", "30", "--dpr", str(self.DPR),
                       "--text-luma", "40", "--blur", "0")
        self.assertEqual(r.returncode, 0, r.stderr)
        loops, _ = parse(r.stdout)
        self.assertEqual(len(loops), 2, "圆环必须追出内外两个环（配 evenodd 才是空的）")
        area = evenodd_area(loops)
        self.assertLess(abs(area / (np.pi * (RO ** 2 - RI ** 2)) - 1), 0.02, f"面积 {area:.3f}")

    def test_bg_equal_to_text_luma_is_rejected(self):
        p = self.build(circle_cov(20, self.DPR, 10, 10, 3))
        r = self.trace(p, "--x", "2", "18", "--y", "2", "18", "--bg", "40", "--text-luma", "40")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("太接近", r.stderr)

    def test_region_outside_image_is_an_error(self):
        p = self.build(circle_cov(20, self.DPR, 10, 10, 3))
        r = self.trace(p, "--x", "100", "120", "--y", "0", "10")
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("超出图片范围", r.stderr)

    def test_empty_region_is_an_error(self):
        p = self.build(circle_cov(20, self.DPR, 10, 10, 3))
        r = self.trace(p, "--x", "1", "5", "--y", "1", "5")
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("没有墨", r.stderr)


class TestAlphaMode(Base):
    def emit(self, alpha, *args):
        p = save_alpha(alpha, os.path.join(self.dir, "ico.png"))
        return self.trace(p, "--alpha", "--blur", "0", *args)

    def test_square_area(self):
        a = np.zeros((40, 40))
        a[8:24, 10:26] = 1.0
        r = self.emit(a)
        self.assertEqual(r.returncode, 0, r.stderr)
        loops, vb = parse(r.stdout)
        self.assertEqual(len(loops), 1)
        self.assertEqual(vb, (40.0, 40.0), "alpha 模式 viewBox 必须是源图像素")
        self.assertLess(abs(evenodd_area(loops) - 256.0), 1.0)

    def test_donut_has_two_loops(self):
        n = 60
        ys, xs = np.mgrid[0:n, 0:n]
        d = np.hypot(xs + 0.5 - 30, ys + 0.5 - 30)
        a = ((d < 24) & (d > 12)).astype(float)
        r = self.emit(a)
        self.assertEqual(r.returncode, 0, r.stderr)
        loops, _ = parse(r.stdout)
        self.assertEqual(len(loops), 2)
        want = np.pi * (24 ** 2 - 12 ** 2)
        self.assertLess(abs(evenodd_area(loops) - want) / want, 0.02)

    def test_big_source_is_capped_not_slow(self):
        """1800px 级的图标必须在几秒内出结果（历史 bug：8 倍上采样跑 40 秒）。"""
        a = np.zeros((1300, 1800))
        a[100:1200, 100:1700] = 1.0
        t0 = time.time()
        r = self.emit(a)
        dt = time.time() - t0
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertLess(dt, 8.0, f"耗时 {dt:.1f}s")
        self.assertEqual(len(parse(r.stdout)[0]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
