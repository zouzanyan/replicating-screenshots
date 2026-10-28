#!/usr/bin/env python3
"""measure.py 的单元测试 + 端到端测试。

每个断言都对着**解析解**（能算出确切值的合成图），不是对着上一次的输出。
改动 measure.py 之后跑 `bash tests/run_tests.sh`，红了就是破坏了已有语义。

    python3 -I tests/test_measure.py
"""

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "scripts", "measure.py")


def load_module():
    spec = importlib.util.spec_from_file_location("measure", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


measure = load_module()


def write_gray(arr, path):
    Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "L").save(path)


def blank(size, v=255):
    return np.full((size, size), float(v))


class TestInk(unittest.TestCase):
    def test_black_is_unit_per_pixel(self):
        a = blank(10, 0)
        self.assertAlmostEqual(measure.ink(a), 100.0, places=6)

    def test_white_is_zero(self):
        self.assertAlmostEqual(measure.ink(blank(10, 255)), 0.0, places=6)

    def test_gray_is_linear(self):
        self.assertAlmostEqual(measure.ink(blank(10, 128)), 100 * 127 / 255, places=4)

    def test_dark_theme_mirrors(self):
        """深色主题下取绝对值，同一张图的墨量不随底色翻转而变。"""
        self.assertAlmostEqual(measure.ink(blank(10, 0), bg=0.0), 0.0, places=6)
        self.assertAlmostEqual(measure.ink(blank(10, 255), bg=0.0), 100.0, places=6)


class TestCoverage(unittest.TestCase):
    def test_endpoints(self):
        self.assertAlmostEqual(measure.coverage(np.array([[255.0]]), 40).item(), 0.0)
        self.assertAlmostEqual(measure.coverage(np.array([[40.0]]), 40).item(), 1.0)

    def test_midpoint_is_half(self):
        self.assertAlmostEqual(measure.coverage(np.array([[147.5]]), 40).item(), 0.5, places=6)

    def test_dark_theme_same_formula(self):
        t, bg = 240.0, 20.0
        self.assertAlmostEqual(measure.coverage(np.array([[bg]]), t, bg).item(), 0.0)
        self.assertAlmostEqual(measure.coverage(np.array([[t]]), t, bg).item(), 1.0)
        self.assertAlmostEqual(
            measure.coverage(np.array([[(bg + t) / 2]]), t, bg).item(), 0.5, places=6)


    def test_text_luma_equal_to_bg_is_all_zero_not_nan(self):
        """Y文字 == 背景（区域里没墨）时不能除出 nan。"""
        out = measure.coverage(np.array([[255.0, 200.0]]), 255.0)
        self.assertFalse(np.isnan(out).any())
        self.assertTrue((out == 0).all())


class TestIsInk(unittest.TestCase):
    def test_light_matches_plain_threshold(self):
        a = np.array([[100.0, 199.0, 200.0, 250.0]])
        self.assertEqual(measure.is_ink(a, 200).tolist(),
                         (a < 200).tolist())

    def test_dark_is_mirrored(self):
        a = np.array([[10.0, 128.0, 200.0, 255.0]])
        self.assertEqual(measure.is_ink(a, 128, bg=0.0).tolist(),
                         [[False, False, True, True]])


class TestThreshold(unittest.TestCase):
    """`is_ink` 判的是「离底色比阈值更远」，被它忽略的是一整段灰度区间。"""

    def test_dead_zone_on_white_is_the_tail(self):
        self.assertEqual(measure.dead_zone(255.0, 200.0), (200.0, 255.0))

    def test_dead_zone_on_dark_covers_mid_greys(self):
        # 深底 30 + 默认阈值 200：只剩 >200 才算墨，#6B7078（108）整个落在盲区里
        self.assertEqual(measure.dead_zone(30.0, 200.0), (0.0, 200.0))

    def test_wide_only_when_most_of_the_range_is_ignored(self):
        self.assertFalse(measure.thr_is_wide(255.0, 200.0))   # 浅底默认：忽略 22%
        self.assertFalse(measure.thr_is_wide(247.0, 226.0))   # #C6C6C6 的建议值
        self.assertFalse(measure.thr_is_wide(30.0, 69.0))     # 深底按最淡元素取
        self.assertTrue(measure.thr_is_wide(30.0, 200.0))     # 深底沿用默认

    def test_colored_buttons_are_not_page_backgrounds(self):
        """按钮内的 --bg 是中间调，两侧一半在块外 —— 那里「丢了 45%」没有意义。"""
        self.assertFalse(measure.thr_is_wide(118.0, 200.0))   # 白字蓝按钮
        self.assertFalse(measure.thr_is_wide(156.0, 80.0))    # 深字橙按钮


class TestPeakLine(unittest.TestCase):
    """1 设备像素的线：横线按行取峰值，竖线必须按列。"""

    def _pair(self, vertical):
        bg = 30.0
        o, m = blank(120, bg), blank(120, bg)
        prof = np.arange(100) % 2           # 43/50 交替，模拟 8×8 块相位
        vals = np.where(prof == 0, 43.0, 50.0)
        if vertical:
            o[10:110, 30] = vals
            m[10:110, 30] = 46.5
        else:
            o[60, 10:110] = vals
            m[60, 10:110] = 46.5
        mo = np.abs(bg - o) > abs(bg - 40.0)
        return o, m, bg, mo

    def test_vertical_rule_is_judged_along_its_length(self):
        # 竖线贯穿整个窗口，按行取峰值等于拿两张图的不同行相比：
        # 实测同一条 CSS 画出来的两条线一个 0.800、一个 1.000
        o, m, bg, mo = self._pair(vertical=True)
        self.assertAlmostEqual(measure.peak_line_ratio(o, m, bg, mo), 1.0, places=6)
        self.assertLess(measure.peak_row_ratio(o, m, bg), 0.90)   # 旧判据的假警报

    def test_horizontal_rule_still_uses_rows(self):
        o, m, bg, mo = self._pair(vertical=False)
        self.assertAlmostEqual(measure.peak_line_ratio(o, m, bg, mo), 1.0, places=6)


class TestRowBands(unittest.TestCase):
    def test_finds_three_bands_at_known_positions(self):
        g = blank(90, 255)
        for y0 in (10, 40, 70):
            g[y0:y0 + 9, 10:30] = 0.0          # 每个带 3 CSS px 高 @dpr 3
        bands = measure.row_bands(g, dpr=3, min_css_px=2.0)
        self.assertEqual(bands, [(10, 19), (40, 49), (70, 79)])

    def test_ignores_thin_specks(self):
        g = blank(90, 255)
        g[10:12, 10:30] = 0.0                   # 2 设备像素 = 0.67 CSS px
        self.assertEqual(measure.row_bands(g, dpr=3, min_css_px=2.0), [])

    def test_dark_theme(self):
        g = blank(90, 0)
        g[10:19, 10:30] = 255.0
        bands = measure.row_bands(g, dpr=3, min_css_px=2.0, bg=0.0)
        self.assertEqual(bands, [(10, 19)])


class TestTextLuma(unittest.TestCase):
    def test_dense_text_takes_the_dark_core(self):
        a = blank(30, 255)
        a[5:25, 5:20] = 45.0                    # 墨占 1/3，够密
        self.assertAlmostEqual(measure.text_luma(a), 45.0, places=6)

    def test_sparse_thin_stroke_is_not_mistaken_for_background(self):
        """窗口里墨不到 5% 时，95% 分位数落在背景上（=0），整窗都被选中，
        中位数就是背景本身。Y=bg 之后覆盖率分母为 0，**同一张图跟自己比 IoU 变成
        0.000** —— 实测一条 1.4px 的浅灰箭头在 22×50 的窗口里只占 2.2%，
        四边全 0、墨量一致，却报「形状完全不对」。"""
        a = blank(60, 255)
        a[10:46, 30:34] = 200.0                 # 4 设备像素宽、36 高的浅灰线 = 0.4% 面积
        y = measure.text_luma(a)
        self.assertGreater(y, 180.0)
        self.assertLess(y, 220.0)


class TestStemWidth(unittest.TestCase):
    def test_measures_bar_width(self):
        band = blank(20, 255)
        band[:, 5:11] = 0.0                     # 6 设备像素
        self.assertAlmostEqual(measure.stem_width(band, dpr=3), 2.0, places=6)


class TestSegments(unittest.TestCase):
    def test_splits_on_gap(self):
        m = np.zeros((5, 30), bool)
        m[:, 0:5] = True
        m[:, 10:20] = True                      # 间隔 5 > gap 3
        self.assertEqual(measure.segments(m, axis_gap=3), [(0, 5), (10, 20)])

    def test_merges_small_gap(self):
        m = np.zeros((5, 30), bool)
        m[:, 0:5] = True
        m[:, 7:10] = True                       # 间隔 2 <= gap 3
        self.assertEqual(measure.segments(m, axis_gap=3), [(0, 10)])


class TestSubpixelWidth(unittest.TestCase):
    """0.5 交叉点必须还原真实边缘 —— 这是「掩膜数宽度会被 JPEG 撑宽」的解药。"""

    def test_blurred_bar_keeps_its_true_width(self):
        x = np.arange(40.0)
        prof = ((x >= 10) & (x < 30)).astype(float)
        k = np.exp(-0.5 * (np.arange(-4, 5) / 1.2) ** 2)
        prof = np.convolve(prof, k / k.sum(), mode="same")
        self.assertAlmostEqual(measure.subpixel_width(prof, 0, 40), 20.0, delta=0.2)

    def test_empty_profile_has_no_width(self):
        self.assertEqual(measure.subpixel_width(np.zeros(20), 0, 20), 0.0)


class TestColumnVerdict(unittest.TestCase):
    """列区间的结论要能分开「字距错」和「字号错」—— 两者的改法相反。"""

    @staticmethod
    def glyphs(*spans, rows=10, width=40):
        m = np.zeros((rows, width), float)
        for s, e in spans:
            m[:, s:e] = 1.0
        return m

    def test_identical_masks_say_nothing(self):
        m = self.glyphs((0, 6), (10, 16), (20, 26))
        self.assertIsNone(measure.column_verdict(m, m))

    def test_a_small_offset_alone_is_not_a_verdict(self):
        """整串平移不改变跨度；位置由 Δ 列判，不该在这里再说一遍。"""
        a = self.glyphs((0, 6), (10, 16), (20, 26))
        b = self.glyphs((3, 9), (13, 19), (23, 29))
        self.assertIsNone(measure.column_verdict(a, b))

    def test_equal_widths_with_wider_pitch_is_tracking(self):
        a = self.glyphs((0, 6), (10, 16), (20, 26))
        b = self.glyphs((0, 6), (12, 18), (24, 30))      # 字宽全一样，字间拉开
        self.assertEqual(measure.column_verdict(a, b),
                         "跨度比 1.200、字宽比 1.000（字宽没变 → 字距）")

    def test_scaled_widths_along_the_span_are_size(self):
        a = self.glyphs((0, 6), (10, 16), (20, 26))
        b = self.glyphs((0, 7), (11, 18), (22, 29))      # 字宽和步进一起放大
        self.assertIn("字号/缩放，不是字距", measure.column_verdict(a, b))

    def test_mismatched_segment_counts_stay_silent(self):
        """相邻字阈值下并段/断段很常见，段数不同不能当结论。"""
        a = self.glyphs((0, 6), (10, 16), (20, 26))
        b = self.glyphs((0, 6), (10, 26))
        self.assertIsNone(measure.column_verdict(a, b))

    def test_a_single_segment_carries_no_information(self):
        a = self.glyphs((0, 20))
        b = self.glyphs((0, 24))
        self.assertIsNone(measure.column_verdict(a, b))


class TestCli(unittest.TestCase):
    """端到端：跑真正的命令行，避免函数对但接口错。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.img = os.path.join(self.dir, "a.png")
        g = blank(60, 255)
        g[30:45, 33:54] = 0.0                   # CSS px: x 11-18, y 10-15 @dpr 3
        write_gray(g, self.img)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-I", SCRIPT, *args],
                              capture_output=True, text=True)

    def test_box_reports_exact_bbox(self):
        r = self.run_cli("box", "--orig", self.img, "--mine", self.img,
                         "--x", "0", "20", "--y", "0", "20")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("x   11.00-  18.00 (w  7.00)", r.stdout)
        self.assertIn("y  10.00- 15.00 (h  5.00)", r.stdout)

    def test_iou_with_itself_is_one(self):
        r = self.run_cli("iou", "--orig", self.img, "--mine", self.img,
                         "--x", "0", "20", "--y", "0", "20", "--text-luma", "0")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("IoU 1.0000", r.stdout)

    def test_rows_runs_and_reports_deviation(self):
        r = self.run_cli("rows", "--orig", self.img, "--mine", self.img)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("最大字重偏差 0.0%", r.stdout)
        self.assertIn("最大 |Δy| 0.00px", r.stdout)

    def test_empty_region_is_an_error_not_a_traceback(self):
        """框到空白区域时，必须给一句人话，不能抛 ValueError。"""
        r = self.run_cli("box", "--orig", self.img, "--mine", self.img,
                         "--x", "1", "6", "--y", "0", "5")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("没有墨", r.stdout)

    def test_region_outside_image_is_an_error(self):
        r = self.run_cli("box", "--orig", self.img, "--mine", self.img,
                         "--x", "100", "110", "--y", "0", "5")
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("超出图片范围", r.stderr)

    def test_iou_derives_text_luma_when_not_given(self):
        """不给 --text-luma 时自动取 Y文字：手填一个错的（如 118 去量灰度 45 的字）
        会把 IoU 压得毫无意义，且看不出是填错了。"""
        r = self.run_cli("iou", "--orig", self.img, "--mine", self.img,
                         "--x", "0", "20", "--y", "0", "20")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("（自动）", r.stdout)
        self.assertIn("IoU 1.0000", r.stdout)

    def test_box_reports_text_luma(self):
        r = self.run_cli("box", "--orig", self.img, "--mine", self.img,
                         "--x", "0", "20", "--y", "0", "20")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("文字亮度 0.0", r.stdout)

    def test_mae_of_identical_images_is_zero(self):
        r = self.run_cli("mae", "--orig", self.img, "--mine", self.img)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("MAE 0.000/255", r.stdout)

    def test_mae_sees_a_shift(self):
        """整块错位是 MAE 的强项：逐行表按序号配对会把上下两行平均掉。"""
        g = blank(60, 255)
        g[33:48, 33:54] = 0.0
        other = os.path.join(self.dir, "shifted.png")
        write_gray(g, other)
        r = self.run_cli("mae", "--orig", self.img, "--mine", other)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("MAE 0.000", r.stdout)

    def test_rows_warns_before_the_table_when_counts_differ(self):
        """行数不同时，按序号配对的每一行都是错位的假数据 —— 警告必须排在表头前面。"""
        g = blank(60, 255)
        g[2:13, 33:54] = 0.0                    # 11 设备像素 > 3 CSS px 的行带上限
        g[40:55, 33:54] = 0.0
        other = os.path.join(self.dir, "two_rows.png")
        write_gray(g, other)
        r = self.run_cli("rows", "--orig", self.img, "--mine", other)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("行数不一致", r.stdout)
        self.assertLess(r.stdout.index("行数不一致"), r.stdout.index("墨量原"))

    def test_iou_shift_separates_offset_from_shape(self):
        """位置差 2 设备像素时，原地 IoU < 1；--shift 要能找到平移并给出 1.0。"""
        g = blank(60, 255)
        g[32:47, 34:55] = 0.0                   # 比原图右下各偏 2 / 1 设备像素
        moved = os.path.join(self.dir, "moved.png")
        write_gray(g, moved)
        r0 = self.run_cli("iou", "--orig", self.img, "--mine", moved,
                          "--x", "0", "20", "--y", "0", "20")
        self.assertEqual(r0.returncode, 0, r0.stderr)
        self.assertNotIn("IoU 1.0000", r0.stdout)

        r = self.run_cli("iou", "--orig", self.img, "--mine", moved,
                         "--x", "0", "20", "--y", "0", "20", "--shift", "3")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("最佳对齐 IoU 1.0000", r.stdout)
        # 顺序必须写死：只写 -2,-1 时，人会把 y 当成 x（实测因此横着移了箭头）
        self.assertIn("dy=-2 dx=-1", r.stdout)

    def test_iou_warns_when_most_of_the_window_is_ink(self):
        """白字在彩色底上忘了给 --bg 时，覆盖率会算成底色本身，数值看着很正常。"""
        g = blank(60, 0)                        # 整窗全黑：>50% 被算成墨
        dark = os.path.join(self.dir, "dark.png")
        write_gray(g, dark)
        r = self.run_cli("iou", "--orig", dark, "--mine", dark,
                         "--x", "0", "20", "--y", "0", "20")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("一半以上被算成墨", r.stdout)

    def test_sparse_light_ink_still_has_a_usable_iou(self):
        """窗口 66x150 设备像素里只有一条 4x36 的浅灰线（1.5% 面积）：
        自动 Y 若退化成 255，同一张图跟自己比也会报 IoU 0.000。"""
        g = blank(200, 255)
        g[60:96, 90:94] = 200.0
        sparse = os.path.join(self.dir, "sparse.png")
        write_gray(g, sparse)
        r = self.run_cli("iou", "--orig", sparse, "--mine", sparse,
                         "--x", "26", "48", "--y", "16", "66")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("IoU 1.0000", r.stdout)

    def test_elems_does_not_print_iou_zero_for_sparse_icon(self):
        """同一条墨在 elems 里就是一行「IoU 0.000」，格式完全正常 —— 最危险的那种错。"""
        g = blank(200, 255)
        g[60:96, 90:94] = 200.0
        sparse = os.path.join(self.dir, "sparse2.png")
        write_gray(g, sparse)
        rects = self.write_rects("chev,26,48,16,66,235,,icon\n")
        r = self.run_cli("elems", "--orig", sparse, "--mine", sparse, "--rects", rects)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("chev", r.stdout)
        self.assertNotIn("|  0.000 |", r.stdout)

    def test_elems_reports_local_mae_per_element(self):
        """局部 MAE 是低对比文字上唯一稳的绝对信号（IoU 被 JPEG 噪声压到 0.5 时
        它还能区分 12.90 和 6.09）。"""
        r = self.run_cli("elems", "--orig", self.img, "--mine", self.img,
                         "--rects", self.write_rects("block,10,20,8,18\n"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("MAE局部", r.stdout)
        self.assertIn("|    0.00 |", r.stdout)

    def test_line_kind_judges_the_peak_row_not_the_jpeg_haze(self):
        """1 设备像素的线：原图的 ±1 灰度雾在整窗几千个设备像素上积起来的墨量
        比线本身还多，整窗比会把一条像素级正确的线判成「偏细 14%」。"""
        o = blank(60, 255)
        o[30, :] = 190.0                        # 线
        o[29, :] = 252.0                        # JPEG 振铃
        o[31, :] = 252.0
        m = blank(60, 255)
        m[30, :] = 190.0
        a = os.path.join(self.dir, "line_o.png")
        b = os.path.join(self.dir, "line_m.png")
        write_gray(o, a)
        write_gray(m, b)
        r = self.run_cli("elems", "--orig", a, "--mine", b,
                         "--rects", self.write_rects("hair,0,20,8,12,200,,line\n"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("细线（整窗比 0.91", r.stdout)
        self.assertNotIn("墨量偏细", r.stdout)

    def test_full_bleed_window_is_not_reported_as_clipped(self):
        """横贯整幅的分隔线墨迹必然贴窗口边；窗口本身顶到图像边界时不是「没框全」。"""
        o = blank(60, 255)
        o[30, :] = 200.0
        p = os.path.join(self.dir, "bleed.png")
        write_gray(o, p)
        r = self.run_cli("elems", "--orig", p, "--mine", p,
                         "--rects", self.write_rects("full,0,20,8,12,200,,line\n"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("窗口贴边", r.stdout)

    def test_rects_can_pin_text_luma_per_element(self):
        """彩色底 + 旁边有更亮的块时，自动 Y 可能取到别的界面；清单要能钉死它。"""
        o = blank(60, 255)
        o[30:45, 33:54] = 45.0
        m = blank(60, 255)
        m[30:45, 33:54] = 150.0
        a = os.path.join(self.dir, "pin_o.png")
        b = os.path.join(self.dir, "pin_m.png")
        write_gray(o, a)
        write_gray(m, b)
        auto = self.run_cli("elems", "--orig", a, "--mine", b,
                            "--rects", self.write_rects("pin,0,20,8,18\n"))
        self.assertIn("|  0.000 |", auto.stdout)     # 自动 Y=45 时复刻正好落在 0.5 线上
        pinned = self.run_cli("elems", "--orig", a, "--mine", b,
                              "--rects", self.write_rects("pin,0,20,8,18,200,,text,150\n"))
        self.assertEqual(pinned.returncode, 0, pinned.stderr)
        self.assertIn("|  1.000 |", pinned.stdout)

    def test_warns_when_the_page_background_is_not_white(self):
        """底色 #F7F7F9（247）按 255 量，每个元素都系统性偏重 12%，
        读起来像「字重没调对」—— 第二次盲测为此整轮跑偏。"""
        g = blank(60, 247)
        g[30:45, 33:54] = 0.0
        p = os.path.join(self.dir, "tint.png")
        write_gray(g, p)
        r = self.run_cli("rows", "--orig", p, "--mine", p)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("最常见的灰度是 247", r.stdout)
        r2 = self.run_cli("rows", "--orig", p, "--mine", p, "--bg", "247")
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertNotIn("最常见的灰度", r2.stdout)

    def test_dark_page_with_the_default_threshold_says_what_it_drops(self):
        """深底（30）沿用默认阈值 200，等价于「只认灰度 >200 的墨」。
        中灰文字整段消失，而行表照样报「最大字重偏差 0.0%」——
        实测深色页面就是这样从 18 条带缩成 6 条，输出格式没有任何变化。"""
        g = blank(60, 30)
        g[10:20, 10:50] = 255.0        # 白字
        g[35:45, 10:50] = 108.0        # #6B7078 中灰
        p = os.path.join(self.dir, "dark.png")
        write_gray(g, p)
        r = self.run_cli("rows", "--orig", p, "--mine", p, "--bg", "30")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("当成了背景", r.stdout)
        self.assertIn("最大字重偏差 0.0%", r.stdout)      # 数字好看，内容少了半页
        r2 = self.run_cli("rows", "--orig", p, "--mine", p, "--bg", "30", "--thr", "80")
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertNotIn("当成了背景", r2.stdout)

    def test_light_page_with_the_default_threshold_does_not_warn(self):
        r = self.run_cli("rows", "--orig", self.img, "--mine", self.img)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("当成了背景", r.stdout)

    def test_box_explains_an_empty_region_the_threshold_swallowed(self):
        """框到中灰内容却说「没有墨」时，别让人以为是坐标写错了。"""
        g = blank(60, 30)
        g[30:45, 33:54] = 108.0
        p = os.path.join(self.dir, "gray.png")
        write_gray(g, p)
        r = self.run_cli("box", "--orig", p, "--mine", p,
                         "--x", "0", "20", "--y", "0", "20", "--bg", "30")
        self.assertIn("该区域没有墨", r.stdout)
        self.assertIn("把灰度 0~200 当成了背景", r.stdout)
        r2 = self.run_cli("box", "--orig", p, "--mine", p,
                          "--x", "0", "20", "--y", "0", "20", "--bg", "30", "--thr", "80")
        self.assertNotIn("当成了背景", r2.stdout)
        self.assertIn("x   11.00-  18.00", r2.stdout)

    def test_elems_says_the_threshold_is_why_it_found_no_ink(self):
        g = blank(60, 30)
        g[30:45, 33:54] = 108.0
        p = os.path.join(self.dir, "gray.png")
        write_gray(g, p)
        rects = os.path.join(self.dir, "rects.txt")
        with open(rects, "w") as f:
            f.write("灰字,11,18,10,15,,30\n")
        r = self.run_cli("elems", "--orig", p, "--mine", p, "--rects", rects)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("原图该区域没有墨", r.stdout)
        self.assertIn("当成了背景", r.stdout)

    def test_a_filled_pill_window_is_not_reported_as_clipped(self):
        """窗口开在实心按钮内部时墨迹四边都贴 —— 那是元素填满了窗口，不是剪裁。"""
        g = blank(90, 255)
        g[30:70, 30:70] = 0.0                       # CSS: x/y 10–23.33 @dpr 3
        p = os.path.join(self.dir, "pill.png")
        write_gray(g, p)
        rects = os.path.join(self.dir, "pill.txt")
        with open(rects, "w") as f:
            f.write("实心块,10,23.34,10,23.34,200,255\n")
        r = self.run_cli("elems", "--orig", p, "--mine", p, "--rects", rects)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("窗口贴边", r.stdout)
        self.assertIn("1.000 |  1.000", r.stdout)

    def test_elems_flags_a_wrong_background_per_element(self):
        """图标坐在 240 的色块上、清单里却跟着用了 --bg 255：
        墨量和 IoU 都会偏，而数字看着完全正常。窗口最常见的灰度就是这块的底。"""
        g = blank(60, 240)
        g[30:45, 33:54] = 65.0
        p = os.path.join(self.dir, "tint2.png")
        write_gray(g, p)
        wrong = self.run_cli("elems", "--orig", p, "--mine", p,
                             "--rects", self.write_rects("ico,0,20,8,18,200,,icon\n"))
        self.assertIn("底色可能不对（窗口最常见 240，按它算墨量比 1.000）", wrong.stdout)
        right = self.run_cli("elems", "--orig", p, "--mine", p,
                             "--rects", self.write_rects("ico,0,20,8,18,200,240,icon\n"))
        self.assertNotIn("底色可能不对", right.stdout)

    def test_elems_separates_tracking_from_size(self):
        """字宽一样、第二个字被推开：判定列要直接说「字距」，别让人再去跑 cols。"""
        o = blank(90, 255)
        o[30:45, 30:36] = 40.0
        o[30:45, 48:54] = 40.0
        m = blank(90, 255)
        m[30:45, 30:36] = 40.0
        m[30:45, 51:57] = 40.0                  # 第二字右移 3 设备像素
        po, pm = os.path.join(self.dir, "po.png"), os.path.join(self.dir, "pm.png")
        write_gray(o, po)
        write_gray(m, pm)
        r = self.run_cli("elems", "--orig", po, "--mine", pm,
                         "--rects", self.write_rects("t,0,30,8,18\n"))
        self.assertIn("跨度比 1.167、字宽比 1.000（字宽没变 → 字距）", r.stdout)

    def write_rects(self, text):
        path = os.path.join(self.dir, "rects.txt")
        with open(path, "w") as f:
            f.write(text)
        return path

    def test_elems_measures_the_whole_list_at_once(self):
        rects = self.write_rects("# 名字,x0,x1,y0,y1\n"
                                 "block,10,20,8,18\n"
                                 "block2 10 20 8 18\n")
        r = self.run_cli("elems", "--orig", self.img, "--mine", self.img,
                         "--rects", rects)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("block", r.stdout)
        self.assertIn("block2", r.stdout)          # 空格分隔也接受
        self.assertIn("最大墨量偏差 0.0%", r.stdout)
        self.assertIn("最低文字 IoU 1.000", r.stdout)

    def test_elems_only_flags_shape_for_icons(self):
        """文字 IoU 没有绝对门槛（iOS 原图底噪 0.5–0.85），不能一律标「形状差」。
        图标数据不达标时，工具还要顺手说清楚是位置差还是形状差。"""
        g = blank(60, 255)
        g[32:47, 34:55] = 0.0
        moved = os.path.join(self.dir, "moved2.png")
        write_gray(g, moved)
        rects = self.write_rects("words,10,20,8,18\nicon,10,20,8,18,,,icon\n")
        r = self.run_cli("elems", "--orig", self.img, "--mine", moved,
                         "--rects", rects)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("位置差（对齐后 1.000", r.stdout)
        self.assertNotIn("形状差", r.stdout)     # 2 设备像素的错位应被识别成位置差
        self.assertIn("最低图标 IoU", r.stdout)
        self.assertIn("仅参考", r.stdout)

    def test_elems_flags_a_clipped_window(self):
        rects = self.write_rects("clip,10,15,8,18\n")
        r = self.run_cli("elems", "--orig", self.img, "--mine", self.img,
                         "--rects", rects)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("窗口贴边", r.stdout)

    def test_elems_keeps_going_when_one_rect_is_outside(self):
        """一行坐标写错，不该让其余元素的结果一起消失。"""
        rects = self.write_rects("good,10,20,8,18\nbad,100,110,0,5\n")
        r = self.run_cli("elems", "--orig", self.img, "--mine", self.img,
                         "--rects", rects)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("good", r.stdout)
        self.assertIn("超出图片范围", r.stdout)
        self.assertIn("bad", r.stdout)

    def test_elems_does_not_flag_low_contrast_icons_by_absolute_iou(self):
        """1 设备像素的浅灰描边（#C6C6C6 在 255 上）IoU 底噪就有 0.54，
        四边全对也会被 0.80 误判成形状错。"""
        a = blank(60, 255)
        a[30:45, 33:54] = 200.0
        img_a = os.path.join(self.dir, "g1.png")
        write_gray(a, img_a)
        b = blank(60, 255)
        b[32:47, 34:55] = 200.0
        img_b = os.path.join(self.dir, "g2.png")
        write_gray(b, img_b)
        rects = self.write_rects("chev,10,20,8,18,,,icon\n")
        r = self.run_cli("elems", "--orig", img_a, "--mine", img_b, "--rects", rects)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("形状差", r.stdout)

    def test_elems_rejects_a_malformed_line_without_a_traceback(self):
        rects = self.write_rects("oops,1,2\n")
        r = self.run_cli("elems", "--orig", self.img, "--mine", self.img,
                         "--rects", rects)
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("至少 5 列", r.stderr)

    def test_dpr_is_accepted_after_the_subcommand(self):
        r = self.run_cli("rows", "--orig", self.img, "--mine", self.img, "--dpr", "3")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("最大字重偏差", r.stdout)

    def test_box_warns_when_window_clips_the_ink(self):
        """窗口切过元素时墨迹框会被悄悄截短 —— 必须出声。

        没有这句话的时候，「徽标窄了 2px」这种结论其实是窗口把右边切掉了，
        而输出格式完全正常，看不出问题。
        """
        r = self.run_cli("box", "--orig", self.img, "--mine", self.img,
                         "--x", "0", "15", "--y", "0", "20")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("贴右边", r.stdout)

    def test_box_is_silent_when_window_contains_the_ink(self):
        r = self.run_cli("box", "--orig", self.img, "--mine", self.img,
                         "--x", "0", "20", "--y", "0", "20")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("贴边", r.stdout)

    def test_cols_warns_when_window_clips_a_segment(self):
        r = self.run_cli("cols", "--orig", self.img, "--mine", self.img,
                         "--x", "0", "15", "--y", "8", "18")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("贴右边", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
