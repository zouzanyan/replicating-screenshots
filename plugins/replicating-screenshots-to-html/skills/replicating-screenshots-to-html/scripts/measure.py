#!/usr/bin/env python3
"""截图复刻的量化对比工具。

所有指标都以「原图」为基准、以「复刻的渲染截图」为待测，单位统一换成 CSS px
（即设备像素 ÷ dpr），输出值和你在 CSS 里写的 px 是同一个刻度，可以直接改。

子命令:
  rows   逐行墨量对比 —— 判字重/字号是否整体对得上
  cols   某一行内逐字列区间 —— 判字号、字距、元素左右位置
  box    某个矩形的墨迹框与墨量 —— 判单个元素（徽标、图标、数字）
  iou    某个矩形的覆盖度 IoU / MAE —— 判形状
  elems  按清单一次量完所有元素 —— 四边偏差 + 墨量比 + IoU
  mae    全图平均绝对差 —— 判整块错位（只当汇总，不单独判像不像）

用法示例:
  python3 measure.py rows --orig 1.jpg --mine render.png
  python3 measure.py mae  --orig 1.jpg --mine render.png
  python3 measure.py elems --orig 1.jpg --mine render.png --rects rects.txt
  python3 measure.py cols --orig 1.jpg --mine render.png --y 222 240 --x 260 390
  python3 measure.py box  --orig 1.jpg --mine render.png --x 84 102 --y 120 140
  python3 measure.py iou  --orig 1.jpg --mine render.png --x 82 102 --y 118 142

为什么要这样量（详见 reference/metrics.md）:
  墨量 = Σ clip((255-gray)/255,0,1)。高斯模糊下近似守恒，
        JPEG 把笔画糊开只会让峰值变浅、边缘变灰，总和基本不变 —— 所以它
        判字重不会被压缩噪声骗。
  覆盖度 = clip((255-gray)/(255-Y文字),0,1)，二值化后算 IoU。对对称模糊，
        0.5 等值线就是真实轮廓的位置，所以量 bbox 能精确到亚像素。
"""

import argparse
import sys

import numpy as np
from PIL import Image

DARK = 200  # 「算作有墨」的默认阈值；浅灰元素要调高（见 reference/metrics.md）


def load_gray(path):
    return np.asarray(Image.open(path).convert("L")).astype(np.float64)


def ink(gray, bg=255.0):
    """墨量：对 JPEG 模糊近似守恒。

    取 |bg−gray| 而不是 bg−gray，深色主题（bg=0）下同一个公式依然成立。
    """
    return float(np.clip(np.abs(bg - gray) / 255.0, 0.0, 1.0).sum())


def coverage(gray, text_luma, bg=255.0):
    """覆盖度：文字自身亮度的归一化，二值化后 0.5 那条线≈真实轮廓。

    (bg−gray)/(bg−Y)：bg=255 时是浅底公式；bg=0 时分子分母同时变号，
    仍然是「背景 0、文字 1」，所以深色主题不用换公式。
    """
    den = bg - text_luma
    if den == 0:            # Y=bg 时区域里根本没有墨，别让它除出 nan
        return np.zeros_like(gray)
    return np.clip((bg - gray) / den, 0.0, 1.0)


def is_ink(gray, thr, bg=255.0):
    """gray < thr 的深色底推广：离背景比离阈值更远就算有墨。"""
    return np.abs(bg - gray) > abs(bg - thr)


def crop(gray, x0, y0, x1, y1, dpr):
    check_region(gray, x0, y0, x1, y1, dpr)
    return gray[int(y0 * dpr):int(y1 * dpr), int(x0 * dpr):int(x1 * dpr)]


def check_region(gray, x0, y0, x1, y1, dpr):
    """量测窗口必须完全落在图内。

    越界时 numpy 会给一个空切片，后面的统计量就变成「没有墨」这种假答案，
    而真正的错误是 dpr 或坐标写错了 —— 直接报错，别让人拿着假数据去调 CSS。
    """
    h, w = gray.shape
    if not (0 <= x0 < x1 and 0 <= y0 < y1):
        sys.exit(f"量测窗口不合法：x {x0}-{x1} y {y0}-{y1}")
    if x1 * dpr > w + 0.5 or y1 * dpr > h + 0.5:
        sys.exit(f"量测窗口超出图片范围：窗口到 ({x1},{y1}) CSS px，"
                 f"图只有 {w/dpr:.2f}x{h/dpr:.2f} CSS px（dpr {dpr:g}）"
                 f" —— 多半是 --dpr 或坐标写错了")


def row_bands(gray, dpr, thr=DARK, min_css_px=3.0, bg=255.0):
    """把图切成文字行带（连续有深色像素的行）。"""
    dark = is_ink(gray, thr, bg).sum(axis=1)
    has = dark > 3
    out, y, h = [], 0, gray.shape[0]
    while y < h:
        if has[y]:
            s = y
            while y < h and (has[y] or dark[y] > 0):
                y += 1
            if (y - s) / dpr >= min_css_px:
                out.append((s, y))
        else:
            y += 1
    return out


def ink_centroid_y(band, bg=255.0):
    """行带内墨迹的竖直质心（行号，相对带顶）。

    用它算竖向偏移，而不是用 bbox 上沿：浅灰文字贴在默认阈值上，
    抗锯齿一变 bbox 就跳 ±2px，会报出「偏了 1.7px」这种假警报。
    质心对阈值和模糊都不敏感，是墨量的一阶矩，天然守恒。
    """
    w = np.clip(np.abs(bg - band) / 255.0, 0.0, 1.0)
    rows = w.sum(axis=1)
    tot = rows.sum()
    if tot <= 0:
        return None
    return float((rows * np.arange(len(rows))).sum() / tot)


def stem_width(band, dpr, bg=255.0, max_ratio=0.5):
    """行内笔画粗细：**水平**游程长度的中位数。

    取水平方向而不是竖直：竖直游程量到的是一条竖笔有多长（等于字高），
    不是它有多粗 —— 早期的实现就量错了，报出来的「字干」其实是 x 高度。
    超过行高一半的游程算横笔（「一」、「二」），剔除后再取中位数。
    """
    b = np.abs(bg - band) > abs(bg - 128.0)
    runs = []
    for row in b:
        n = 0
        for v in row:
            if v:
                n += 1
            elif n:
                runs.append(n)
                n = 0
        if n:
            runs.append(n)
    if not runs:
        return 0.0
    limit = b.shape[0] * max_ratio
    keep = [r for r in runs if r <= limit] or runs
    return float(np.median(keep)) / dpr


def segments(mask, axis_gap=3):
    """沿列方向把有墨的列切成连通段，返回 [(start, end_exclusive), ...]（设备像素）。"""
    idx = np.where(mask.any(axis=0))[0]
    if len(idx) == 0:
        return []
    out, s, p = [], idx[0], idx[0]
    for v in idx[1:]:
        if v - p > axis_gap:
            out.append((s, p + 1))
            s = v
        p = v
    out.append((s, p + 1))
    return out


def stem_str(band, dpr, bg=255.0):
    v = stem_width(band, dpr, bg)
    return "—" if v == 0 else f"{v:.2f}"


def cmd_rows(a):
    o, m = load_gray(a.orig), load_gray(a.mine)
    dpr = a.dpr
    warn_if_bg_is_off(o, a.bg, a.bg_given, who="整页")
    warn_if_thr_is_off(a.bg, a.thr, who="整页")
    if o.shape != m.shape:
        h, w = min(o.shape[0], m.shape[0]), min(o.shape[1], m.shape[1])
        print(f"!! 尺寸不同：原 {o.shape[1]}x{o.shape[0]} / 复 {m.shape[1]}x{m.shape[0]}，"
              f"只比公共区域 {w}x{h}（差 {abs(o.shape[0]-m.shape[0])} 设备像素）")
        o, m = o[:h, :w], m[:h, :w]
    H = o.shape[0]
    ro = row_bands(o, dpr, a.thr, bg=a.bg)
    rm = row_bands(m, dpr, a.thr, bg=a.bg)
    if len(ro) != len(rm):
        # 先说，别等表格打完再说：行数不同时后面按序号配对，全是错位的假数据
        print(f"!! 行数不一致：原 {len(ro)} 行 / 复 {len(rm)} 行 —— 先修版式，下面的数字不要信")
    print(f"{'y':>7} {'行高':>5} {'Δy':>5} | {'墨量原':>8} {'墨量复':>8} {'比值':>6} | "
          f"{'字干原':>6} {'字干复':>6} | 判定")
    worst, worst_at = 0.0, None
    worst_dy, worst_dy_at = 0.0, None
    for i in range(min(len(ro), len(rm))):
        so, sm = ro[i], rm[i]
        bo, bm = o[so[0]:so[1]], m[sm[0]:sm[1]]
        io, im = ink(bo, a.bg) / dpr / dpr, ink(bm, a.bg) / dpr / dpr
        if io <= 0:
            continue
        r = im / io
        co, cm = ink_centroid_y(bo, a.bg), ink_centroid_y(bm, a.bg)
        dy = ((sm[0] + cm) - (so[0] + co)) / dpr if None not in (co, cm) else 0.0
        edge = so[0] == 0 or so[1] >= H - 1 or sm[0] == 0 or sm[1] >= H - 1
        # 判定线和文档用同一个数：0.97–1.03。以前脚本只在 0.93/1.07 标，
        # 0.96 这种「文档说不过、脚本一声不吭」的带就这么滑过去了
        tag = "← 偏细" if r < 0.97 else ("← 偏粗" if r > 1.03 else "")
        if r < 0.93 or r > 1.07:
            tag = "← 明显偏细" if r < 1 else "← 明显偏粗"
        if edge:
            tag = "（贴边，不计）"
        elif abs(r - 1) > worst:
            worst, worst_at = abs(r - 1), so[0] / dpr
        if abs(dy) >= 1:
            tag += f"  ← 竖向偏 {dy:+.1f}px"
        if not edge and abs(dy) > abs(worst_dy):
            worst_dy, worst_dy_at = dy, so[0] / dpr
        # 行里含图形（徽标边框、按钮圆角、信号条）时，水平游程的中位数会被图形带偏，
        # 墨量却还是对的 —— 这种情况报「字干差 30%」是假警报，明确标出来
        sto, stm = stem_width(bo, dpr, a.bg), stem_width(bm, dpr, a.bg)
        if sto and stm and abs(stm / sto - 1) > 0.25 and 0.97 <= r <= 1.03:
            tag += "  （字干差但墨量正常：行内含图形，别追）"
        print(f"{so[0]/dpr:7.1f} {(so[1]-so[0])/dpr:5.1f} {dy:+5.1f} | "
              f"{io:8.1f} {im:8.1f} {r:6.3f} | "
              f"{stem_str(bo,dpr,a.bg):>6} {stem_str(bm,dpr,a.bg):>6} | {tag}")
    where = f"（最差在 y={worst_at:.1f}）" if worst_at is not None else ""
    print(f"\n最大字重偏差 {worst*100:.1f}%{where}"
          f"（≤3% 算过关；3–7% 勉强；>7% 是字号/字重/字距的结构性偏差）")
    dy_where = f"（最差在 y={worst_dy_at:.1f}）" if worst_dy_at is not None else ""
    print(f"最大 |Δy| {abs(worst_dy):.2f}px{dy_where}"
          f"（≤0.75 算过关；同一行内容重复三次实测相差 0.3，这是 JPEG 块相位噪声，"
          f"门槛卡 0.5 会把噪声当误差）")


def cmd_cols(a):
    dpr = a.dpr
    warn_if_thr_is_off(a.bg, a.thr, who="这段")
    for tag, path in (("原", a.orig), ("复", a.mine)):
        g = load_gray(path)
        sub = is_ink(crop(g, a.x[0], a.y[0], a.x[1], a.y[1], dpr), a.thr, a.bg)
        segs = segments(sub, a.gap)
        txt = "  ".join(f"{a.x[0]+s/dpr:.2f}-{a.x[0]+e/dpr:.2f}" for s, e in segs)
        warn = ink_outside(g, a.x[0], a.x[1], a.y[0], a.y[1], a.thr, a.bg, dpr)
        print(f"{tag} [{len(segs):2d}段] {txt}{warn}")


def cmd_box(a):
    dpr = a.dpr
    warn_if_thr_is_off(a.bg, a.thr, who="这段")
    for tag, path in (("原", a.orig), ("复", a.mine)):
        g = load_gray(path)
        sub = crop(g, a.x[0], a.y[0], a.x[1], a.y[1], dpr)
        m = is_ink(sub, a.thr, a.bg)
        if not m.any():
            print(f"{tag} 该区域没有墨 —— {thr_note(a.bg, a.thr)}")
            continue
        ys, xs = np.where(m)
        warn = ink_outside(g, a.x[0], a.x[1], a.y[0], a.y[1], a.thr, a.bg, dpr)
        print(f"{tag} x {a.x[0]+xs.min()/dpr:7.2f}-{a.x[0]+(xs.max()+1)/dpr:7.2f} "
              f"(w {(xs.max()+1-xs.min())/dpr:5.2f})  "
              f"y {a.y[0]+ys.min()/dpr:6.2f}-{a.y[0]+(ys.max()+1)/dpr:6.2f} "
              f"(h {(ys.max()+1-ys.min())/dpr:5.2f})  墨量 {ink(sub,a.bg)/dpr/dpr:7.2f}"
              f"  文字亮度 {text_luma(sub,a.bg):.1f}{warn}")


def edge_warning(left, right, top, bottom):
    """墨迹顶到窗口边缘时的警告。

    窗口切到旁边的元素上，墨迹框会被**悄悄截短**，量出来的宽度/位置都是假的，
    而且看起来完全像一个「元素窄了 2px」的真结论 —— 实测中因此误判过两次
    （徽标右侧被切、箭头右侧被切）。顶边就提醒：要么放宽窗口，要么确认这是元素本身的边界。
    """
    sides = [n for n, hit in (("左", left), ("右", right), ("上", top), ("下", bottom)) if hit]
    return f"  ← 墨迹贴{'/'.join(sides)}边，窗口可能没框全" if sides else ""


def ink_outside(gray, x0, x1, y0, y1, thr, bg, dpr, skip=2, frac=0.25):
    """窗口外面还接着墨吗 —— 这才是「剪裁」，而不是「墨迹贴着窗口边」。

    只看「墨迹贴边」会误伤：自动生成的清单窗口就是墨迹的 bbox，四边必然贴，
    于是一份完全正确的清单报满「窗口贴边」，人就开始无视这个警告。
    真剪裁要往窗外看。紧邻的一两个设备像素可能只是 JPEG 振铃，所以跳过 `skip`
    个像素；并要求窗外那一条里 `frac` 以上是墨，单点的过冲不算。
    窗口顶到图像边界时外面没有像素，本来就没得剪 —— 不算。
    """
    H, W = gray.shape
    X0, X1, Y0, Y1 = int(x0 * dpr), int(x1 * dpr), int(y0 * dpr), int(y1 * dpr)

    def strip(ys, xs):
        if ys.stop <= ys.start or xs.stop <= xs.start:
            return False
        seg = gray[ys, xs]
        if seg.size == 0:
            return False
        d = np.abs(bg - seg)
        # 两个条件缺一不可：占比够（不是单个像素的过冲），而且够深。
        # 只看占比会被 JPEG 振铃骗：锐边外侧 16~24 灰度的雾在浅底上也能越过
        # 阈值 200（231 > 200），实测一份完全正确的浅色清单因此报 9 处「贴边」。
        # 真墨在窗外那一圈至少一半深。
        return (float((d > abs(bg - thr)).mean()) >= frac
                and float(d.mean()) >= 0.5 * abs(bg - thr))

    return edge_warning(
        strip(slice(Y0, Y1), slice(max(0, X0 - skip), X0)),
        strip(slice(Y0, Y1), slice(X1, min(W, X1 + skip))),
        strip(slice(max(0, Y0 - skip), Y0), slice(X0, X1)),
        strip(slice(Y1, min(H, Y1 + skip)), slice(X0, X1)))


def text_luma(gray, bg=255.0, frac=0.05):
    """区域内文字自身的亮度 `Y文字`：离背景最远的那 frac 部分像素的中位数。

    不要取最小值 —— JPEG 在锐边有 16~24 灰度的过冲，最小值是偏的。
    `iou` 不给 --text-luma 时用它自动取值；`box` 也会打出来供手工核对。

    但分位数在**墨很少**时会翻车：窗口 22×50 CSS px 里只有一条 1.4px 的浅灰箭头，
    墨只占 2.2%，95% 分位数直接落在背景上（=0），整窗都被选中，中位数就是背景本身。
    Y=bg 之后覆盖率的分母为 0，**同一张图跟自己比 IoU 也变成 0.000**
    （实测：四边全 0、墨量比 1.000，输出格式完全正常）。
    所以再用「离背景至少半个峰值距离」兜底，保证选中的一定是墨的核心。
    """
    d = np.abs(bg - gray)
    dmax = float(d.max())
    if dmax <= 0:
        return bg
    cut = max(float(np.quantile(d, 1.0 - frac)), 0.5 * dmax)
    return float(np.median(gray[d >= cut]))


def modal_luma(gray):
    """最常见的灰度和它的占比 —— 成片的底色就是它。"""
    vals, counts = np.unique(gray.astype(np.uint8), return_counts=True)
    i = int(counts.argmax())
    return float(vals[i]), float(counts[i] / counts.sum())


def warn_if_bg_is_off(gray, bg, given, who="整页"):
    """底色不是纯白、却用了默认 --bg 时提醒。

    墨量是相对 --bg 算的。页面底色 `#F7F7F9`（247）按 255 量，每个元素都会系统性
    偏重（实测 12%），读起来像「字重没调对」；第二次盲测为此整轮跑偏。
    """
    if given:
        return
    mode, _ = modal_luma(gray)
    if abs(mode - bg) >= 3:
        print(f"!! 全图最常见的灰度是 {mode:.0f}，不是 --bg {bg:g} —— 底色不是纯白时，"
              f"{who}的墨量会被系统性高估（实测 12%）。给 --bg {mode:.0f}，"
              f"或在清单里逐元素给底色")


def dead_zone(bg, thr):
    """`is_ink` 判的是「离底色比阈值更远」。返回被当成背景忽略的灰度区间 (lo, hi)。"""
    d = abs(bg - thr)
    return max(0.0, bg - d), min(255.0, bg + d)


def thr_note(bg, thr):
    lo, hi = dead_zone(bg, thr)
    return f"--thr {thr:g} 在底色 {bg:g} 上把灰度 {lo:g}~{hi:g} 当成了背景"


def thr_is_wide(bg, thr):
    """阈值把大半个亮度范围算成背景了吗 —— 只看**页面级**底色。

    彩色块（按钮、标签）上的 --bg 天然是中间调，两侧一半是块外、一半是块内，
    「丢了 45% 的亮度范围」在那里毫无信息量：`--bg 118` 的按钮配 200 是对的。
    真正会静默丢内容的只有浅底/深底，所以先把中间调排除。
    """
    if not (bg <= 96 or bg >= 191):
        return False
    room = max(255.0 - bg, bg)
    return room > 0 and abs(bg - thr) >= 0.45 * room


def warn_if_thr_is_off(bg, thr, who="整页"):
    """阈值离底色太远时提醒 —— 丢内容这件事**完全静默**，输出格式和正常时一样。

    浅底 255 上用 200，只忽略 200~255 之间的极浅雾；深底 30 上用默认 200，
    等价于「只认灰度 >200 的墨」：中灰文字（`#6B7078` = 108）、灰色细线全部消失。
    实测深色页面 `rows --bg 30` 只切出 6 条带、`--thr 80` 切出全部 18 条，
    两次的表格格式一模一样，唯一的区别就是少了 12 行。
    """
    if not thr_is_wide(bg, thr):
        return
    lo, hi = dead_zone(bg, thr)
    print(f"!! {thr_note(bg, thr)} —— {who}里落在这段的文字/细线会被静默丢掉。"
          f"阈值取 (最淡元素灰度 + bg)/2：bg 255 配 198 → 226，bg 30 配 108 → 69")


def peak_row_ratio(bo, bm, bg=255.0):
    """最亮那一行（设备行）的墨量比。

    1 设备像素的细线不能按整窗墨量判：原图被 JPEG 糊开后，整窗范围内的
    ±1 灰度雾在几千个设备像素上积起来的墨量，比线本身还多。实测一条峰值
    像素级一致的下划线报「墨量比 0.863」—— 于是一个正确的线被判成「偏细 14%」。
    峰值行只在线所在的那一行上比，振铃不参与。
    """
    ro = np.clip(np.abs(bg - bo) / 255.0, 0.0, 1.0).sum(axis=1)
    rm = np.clip(np.abs(bg - bm) / 255.0, 0.0, 1.0).sum(axis=1)
    if ro.max() <= 0:
        return None
    return float(rm.max() / ro.max())


def peak_line_ratio(bo, bm, bg, mo=None):
    """细线的峰值比：沿**短轴**取最大值 —— 横线按行，竖线按列。

    竖线在 y 方向上贯穿整个窗口，每一行的灰度都受 JPEG 8×8 块相位影响
    （实测同一条线在 43~56 之间波动）。按行取峰值等于拿两张图的**不同行**相比：
    同一条 CSS 画出来的两条竖线，一个报 0.800、一个报 1.000，差值全是块相位，
    而沿长度方向的列均值是 42.1 vs 42.9（≈1.02，正常）。
    横线的短轴本来就是行，所以这条规则同时覆盖两种朝向。
    """
    if mo is None:
        mo = is_ink(bo, DARK, bg)
    ys, xs = np.where(mo)
    if ys.size == 0:
        return None
    axis = 0 if (ys.max() - ys.min()) >= (xs.max() - xs.min()) else 1
    ro = np.clip(np.abs(bg - bo) / 255.0, 0.0, 1.0).sum(axis=axis)
    rm = np.clip(np.abs(bg - bm) / 255.0, 0.0, 1.0).sum(axis=axis)
    if ro.max() <= 0:
        return None
    return float(rm.max() / ro.max())


def parse_rects(path):
    """读元素清单。每行 `名字,x0,x1,y0,y1[,阈值][,底色][,类型][,Y文字]`，逗号或空格分隔，`#` 注释。

    留空表示用默认值（阈值 200 / 全局 --bg / 类型 text / Y文字自动取），例如
    `按钮,30,360,500,540,,118,icon,255`。
    为什么要有这个文件：方法是一行一个元素地量，工具却只能一次框一个 ——
    25 个元素就是 50 次调用，还得人眼比对四行数字。清单一次全出。
    类型（text / icon / line）只影响判定：文字 IoU 在 iOS 原图上底噪就有 0.5–0.85
    （见 metrics.md 坑 22），拿 0.80 卡它会让整页每一行都报「形状差」；
    1 设备像素的细线要按峰值行判墨量（见 peak_row_ratio）。
    """
    out = []
    try:
        f = open(path)
    except OSError as e:
        sys.exit(f"读不到元素清单 {path}：{e}")
    with f:
        for ln, line in enumerate(f, 1):
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            parts = [p.strip() for p in (s.split(",") if "," in s else s.split())]
            if len(parts) < 5:
                sys.exit(f"{path}:{ln} 需要至少 5 列（名字,x0,x1,y0,y1），这行是：{s}")
            try:
                x0, x1, y0, y1 = (float(v) for v in parts[1:5])
            except ValueError:
                sys.exit(f"{path}:{ln} 的坐标不是数字：{s}")
            thr = float(parts[5]) if len(parts) > 5 and parts[5] else DARK
            bg = float(parts[6]) if len(parts) > 6 and parts[6] else None
            kind = parts[7].lower() if len(parts) > 7 and parts[7] else "text"
            if kind not in ("text", "icon", "line"):
                sys.exit(f"{path}:{ln} 的类型只能是 text / icon / line，这行是：{s}")
            try:
                ytext = float(parts[8]) if len(parts) > 8 and parts[8] else None
            except ValueError:
                sys.exit(f"{path}:{ln} 的 Y文字不是数字：{s}")
            out.append((parts[0], x0, x1, y0, y1, thr, bg, kind, ytext))
    if not out:
        sys.exit(f"{path} 里没有任何元素行")
    return out


def subpixel_width(prof, s, e):
    """一维列墨剖面里某一段的亚像素宽度：取 0.5 峰值交叉点。

    对称模糊下 0.5 等值线正落在真实边缘上，所以 JPEG 光晕不改变它。
    按二值掩膜数「有墨的列数」则会被光晕和整像素舍入带偏 —— 实测同一段
    文字按掩膜差 6.2%、按 0.5 交叉点差 0.1%，前者会伪装成整档字号错。
    """
    seg = prof[s:e]
    pk = float(seg.max())
    if pk <= 0:
        return 0.0
    half = pk * 0.5
    i = int(np.argmax(seg >= half))
    if i == 0:
        left = float(s)
    else:
        left = s + i - 1 + (half - seg[i - 1]) / (seg[i] - seg[i - 1])
    j = len(seg) - 1 - int(np.argmax(seg[::-1] >= half))
    if j == len(seg) - 1:
        right = float(e)
    else:
        right = s + j + (half - seg[j]) / (seg[j + 1] - seg[j])
    return right - left


def column_verdict(co, cm, gap=3):
    """逐字列区间：整串长度变了时，说是字距还是字号。

    这是 `cols` 的结论版。放进 `elems` 是因为「宽度差补字距」和「字号差改字号」
    的分辨依据全在差落在哪几个字上（坑 37），而需要另跑一条命令的检查，
    实际就是不会跑。

    `co`/`cm` 是**覆盖率**（0~1）数组，不是 0/1 墨掩膜：0.5 等值线是位置的定义，
    掩膜的边界会被 JPEG 光晕撑宽。

    只在整串首尾字心跨度差 ≥3% 时开口 —— 那才是「长度不对」的场景。
    段宽只用来归因：跟着一起变就是字号/缩放，没变就是字距。
    段数不同直接不说：阈值和字体的贴合方式会让相邻字偶尔并段，
    拿它当结论就是拟合噪声（实测一页好图上有 3 个元素两边段数不同）。
    """
    so, sm = segments(co > 0.5, gap), segments(cm > 0.5, gap)
    if len(so) < 2 or len(so) != len(sm):
        return None
    cc = [(s + e) / 2 for s, e in so]
    cd = [(s + e) / 2 for s, e in sm]
    if cc[-1] <= cc[0]:
        return None
    sr = (cd[-1] - cd[0]) / (cc[-1] - cc[0])
    if abs(sr - 1) < 0.03:
        return None
    po, pm = co.sum(axis=0), cm.sum(axis=0)
    wo = [subpixel_width(po, s, e) for s, e in so]
    wm = [subpixel_width(pm, s, e) for s, e in sm]
    ratios = [b / a for a, b in zip(wo, wm) if a > 1.5]
    if not ratios:
        return f"跨度比 {sr:.3f}（段太窄，字宽没量出来）"
    wr = float(np.median(ratios))
    if abs(wr - 1) >= 0.5 * abs(sr - 1):
        return f"跨度比 {sr:.3f}、字宽比 {wr:.3f}（字宽整体变了 → 字号/缩放，不是字距）"
    return f"跨度比 {sr:.3f}、字宽比 {wr:.3f}（字宽没变 → 字距）"


def cmd_elems(a):
    """一次量完整张清单：每个元素的四边偏差、墨量比、形状 IoU、局部 MAE。"""
    dpr = a.dpr
    o, m = load_gray(a.orig), load_gray(a.mine)
    rects = parse_rects(a.rects)
    print(f"{'元素':<12}{'Δ左':>6}{'Δ上':>6}{'Δ右':>6}{'Δ下':>6} | "
          f"{'墨量比':>6} | {'IoU':>6} | {'MAE局部':>7} | 判定（Δ 单位：设备像素）")
    worst_ink = (-1.0, None)      # 用负数起步：完全一致的 0.0 也要被记下来
    worst_line = (-1.0, None)
    worst_icon = (2.0, None)
    worst_text = (2.0, None)
    for name, x0, x1, y0, y1, thr, bg, kind, ytext in rects:
        bg = a.bg if bg is None else bg
        try:
            bo = crop(o, x0, y0, x1, y1, dpr)
            bm = crop(m, x0, y0, x1, y1, dpr)
        except SystemExit as e:
            # 清单里一行写错不该把另外 20 个元素的结果一起吞掉；逐行报错继续
            print(f"{name:<12}{'':>6}{'':>6}{'':>6}{'':>6} | {'':>6} | {'':>6} | {'':>7} | !! {e}")
            continue
        mo, mm = is_ink(bo, thr, bg), is_ink(bm, thr, bg)
        # 阈值把大半个亮度范围当背景时，两个「没有墨」的分支都得说是阈值的问题 ——
        # 「坐标不对」和「窗口开大了」都会把人往错的方向推
        hint = f"（{thr_note(bg, thr)}）" if thr_is_wide(bg, thr) else ""
        if not mo.any() or not mm.any():
            side = "原" if not mo.any() else "复"
            print(f"{name:<12}{'':>6}{'':>6}{'':>6}{'':>6} | {'':>6} | {'':>6} | {'':>7} | "
                  f"{side}图该区域没有墨 —— 坐标或阈值不对{hint}")
            continue
        yo, xo = np.where(mo)
        ym, xm = np.where(mm)
        d = (xm.min() - xo.min(), ym.min() - yo.min(),
             (xm.max() + 1) - (xo.max() + 1), (ym.max() + 1) - (yo.max() + 1))
        io = ink(bo, bg)
        r = ink(bm, bg) / io if io > 0 else 0.0
        # 1 设备像素的线按峰值行判：整窗墨量里混着 JPEG 振铃，会把正确的线判成「偏细 14%」
        peak = peak_line_ratio(bo, bm, bg, mo) if kind == "line" else None
        r_report = r if peak is None else peak
        y = ytext if ytext is not None else text_luma(bo, bg)
        covf_o, covf_m = coverage(bo, y, bg), coverage(bm, y, bg)
        cov_o, cov_m = covf_o > 0.5, covf_m > 0.5
        u = (cov_o | cov_m).sum()
        if not u:
            print(f"{name:<12}{'':>6}{'':>6}{'':>6}{'':>6} | {'':>6} | {'':>6} | {'':>7} | "
                  f"窗口里没有可测的墨（Y文字 ≈ 背景）—— 窗口开大了？{hint}")
            continue
        iou = float((cov_o & cov_m).sum() / u)
        # 局部 MAE：低对比文字上比 IoU 稳（IoU 被 JPEG 噪声压到 0.5 时它还有区分度）。
        # 只比自己改动前后 —— 窗口大小不同，元素之间不可比
        mae_local = float(np.abs(bo - bm).mean())

        flags = []
        # 逐元素核对底色：窗口里最常见的灰度就是这块元素的底。给错了（比如色块上的
        # 图标还按 255 算），墨量比和 IoU 都会偏，而且数字看着完全正常。
        # 只有在这个灰度占窗口 ≥25%、且**不是墨自己**（实心按钮的窗口整个被墨占满，
        # 最常见灰度就是它的填充色）时才认
        mode, frac = modal_luma(bo)
        if abs(mode - bg) >= 3 and frac >= 0.25 and abs(mode - y) >= 3:
            # 只警告不给数，人还是会去用上面那个按错的底色算出来的比值 ——
            # 那个数看着完全正常。顺手按窗口自己的底色再算一遍，两个数并排放
            io_alt = ink(bo, mode)
            r_alt = ink(bm, mode) / io_alt if io_alt > 0 else 0.0
            flags.append(f"底色可能不对（窗口最常见 {mode:.0f}，按它算墨量比 {r_alt:.3f}）")
        if kind == "text":
            v = column_verdict(covf_o, covf_m)
            if v:
                flags.append(v)
        if kind == "line":
            # 细线单独统计：它的噪声底是 2~7%（见下），混进「最大墨量偏差」会把
            # 一页正常的东西顶成 7% 的假头条
            if abs(r_report - 1) > worst_line[0]:
                worst_line = (abs(r_report - 1), name)
        elif abs(r_report - 1) > worst_ink[0]:
            worst_ink = (abs(r_report - 1), name)
        if kind == "icon":
            if abs(bg - y) >= 100 and iou < worst_icon[0]:
                worst_icon = (iou, name)
        else:
            if iou < worst_text[0]:
                worst_text = (iou, name)
        if not 0.97 <= r_report <= 1.03:
            # 1 设备像素的线对比度本来就低（深底上 47 vs 30，只有 17 个灰度），
            # 源图 JPEG 的块相位能让**一模一样**的两条线差 1.9%，单条能差到 7%。
            # 拿 3% 卡它等于拟合压缩噪声：真实错误（alpha .08 vs .12）是 30% 以上
            if kind == "line" and 0.90 <= r_report <= 1.10:
                pass
            else:
                flags.append("墨量偏" + ("粗" if r_report > 1 else "细"))
        if peak is not None and abs(r / peak - 1) > 0.03:
            flags.append(f"细线（整窗比 {r:.3f} 含背景振铃，看峰值）")
        # 只对**强对比的图标**按绝对线判形状。两类东西的 IoU 底噪很大：
        # 文字（iOS 原图 0.5–0.85，见 metrics.md 坑 22），以及细描边 + 浅灰的图标
        # （实测 1 设备像素的 #C6C6C6 箭头：四边都在 1 设备像素内，IoU 只有 0.54）。
        # 拿 0.80 一律卡，人会开始拟合 JPEG。
        if kind == "icon" and iou < 0.80 and abs(bg - y) >= 100:
            flags.append("形状差")
            # 顺手做一次 ±2 设备像素的对齐：IoU 低到底是因为位置差还是形状差，
            # 这一步能直接分开。分开之后人就不会去改本来是对的路径
            best = max(((shifted_iou(cov_o, cov_m, dy, dx), dy, dx)
                        for dy in range(-2, 3) for dx in range(-2, 3)))
            if best[0] >= 0.80 and best[1:] != (0, 0):
                flags[-1] = (f"位置差（对齐后 {best[0]:.3f}，"
                             f"复刻需移 dy={best[1]:+d} dx={best[2]:+d} 设备像素）")
        # 全出血元素（横贯整幅的分隔线、卡片顶边）墨迹必然贴窗口边，窗口本身已经
        # 顶到图像边界时那不是「窗口没框全」。真正的剪裁看窗外还接不接着墨
        clip = ink_outside(o, x0, x1, y0, y1, thr, bg, dpr)
        if clip:
            flags.append("窗口贴边")
        print(f"{name:<12}{d[0]:>+6.1f}{d[1]:>+6.1f}{d[2]:>+6.1f}{d[3]:>+6.1f} | "
              f"{r_report:6.3f} | {iou:6.3f} | {mae_local:7.2f} | {'；'.join(flags)}")
    parts = []
    if worst_ink[1]:
        parts.append(f"最大墨量偏差 {worst_ink[0]*100:.1f}%（{worst_ink[1]}）")
    if worst_line[1]:
        # 单独一条：细线的噪声底是 2~7%，混进上面那条会把正常的线顶成整页头条
        parts.append(f"细线最大偏差 {worst_line[0]*100:.1f}%（{worst_line[1]}，噪声底 2~7%）")
    if worst_icon[1]:
        parts.append(f"最低图标 IoU {worst_icon[0]:.3f}（{worst_icon[1]}）")
    if worst_text[1]:
        parts.append(f"最低文字 IoU {worst_text[0]:.3f}（{worst_text[1]}，仅参考）")
    if parts:
        print("\n" + "；".join(parts))
    print("四边 Δ 都 ≤1 设备像素且墨量 0.97–1.03 才算位置对；图标 IoU <0.80 再拆形状，"
          "先跑 `iou --shift 4` 确认不是位置差。文字 IoU 只比改动前后，不设绝对线。"
          "MAE局部 同理，只在同一窗口的改动前后之间比")


def cmd_mae(a):
    """全图平均绝对差：只当汇总看，不能单独拿它判「像不像」。

    它被大片背景稀释，字重错 20% 也能低到 3/255；但反过来，
    一个整块错位（列表整体下移 2px）会让它立刻跳上去，比逐行表更早报警。
    """
    o, m = load_gray(a.orig), load_gray(a.mine)
    warn_if_bg_is_off(o, a.bg, a.bg_given, who="整页")
    if o.shape != m.shape:
        h, w = min(o.shape[0], m.shape[0]), min(o.shape[1], m.shape[1])
        print(f"!! 尺寸不同：原 {o.shape[1]}x{o.shape[0]} / 复 {m.shape[1]}x{m.shape[0]}，"
              f"只比公共区域 {w}x{h}")
        o, m = o[:h, :w], m[:h, :w]
    d = np.abs(o - m)
    print(f"MAE {d.mean():.3f}/255（<7 肉眼看不出；突然上升通常是整块错位，去跑 rows）")


def shifted_iou(bo, bm, dy, dx):
    """把 bm 平移 (dy,dx) 设备像素后与 bo 的 IoU（只比重叠区域，不绕回）。"""
    h, w = bo.shape
    y0, y1 = max(0, dy), min(h, h + dy)
    x0, x1 = max(0, dx), min(w, w + dx)
    a = bo[y0:y1, x0:x1]
    b = bm[y0 - dy:y1 - dy, x0 - dx:x1 - dx]
    u = (a | b).sum()
    return float((a & b).sum() / u) if u else 0.0


def cmd_iou(a):
    dpr = a.dpr
    o = crop(load_gray(a.orig), a.x[0], a.y[0], a.x[1], a.y[1], dpr)
    m = crop(load_gray(a.mine), a.x[0], a.y[0], a.x[1], a.y[1], dpr)
    # 不给 --text-luma 就从原图自动取：手填错一个数（比如拿正文的 118 去量
    # 灰度 45 的数字）会把 IoU 压得毫无意义，而且看不出是自己填错了
    auto = a.text_luma is None
    y = text_luma(o, a.bg) if auto else a.text_luma
    if auto and abs(a.bg - y) < 4:
        # 自动取到的「文字亮度」贴着背景 = 窗口里只有 JPEG 噪声，没有真墨。
        # 这时候硬算覆盖率得到的是噪声的形状，数字看着正常但毫无意义
        print("该区域没有可测的墨（自动 Y文字 几乎等于背景）—— 窗口是不是开大了，或坐标不对？")
        return
    co, cm = coverage(o, y, a.bg), coverage(m, y, a.bg)
    h, w = min(co.shape[0], cm.shape[0]), min(co.shape[1], cm.shape[1])
    co, cm = co[:h, :w], cm[:h, :w]
    bo, bm = co > 0.5, cm > 0.5
    union = (bo | bm).sum()
    if union == 0:
        print("该区域两边都没有墨 —— 坐标或 --text-luma 有问题")
        return
    # 白字在彩色底上时，背景色不是 --bg，覆盖率会算成整块底色的形状：
    # 实测同一个按钮报 0.9373（假）vs 真值 0.8072，而且输出一切正常
    if bo.mean() > 0.5 or bm.mean() > 0.5:
        print("!! 窗口内一半以上被算成墨 —— 背景色可能不是 --bg。"
              "白字/浅字在彩色底上，要同时给 --bg（底色）和 --text-luma（字色）")
    print(f"Y文字 {y:5.1f}{'（自动）' if auto else ''}   "
          f"IoU {(bo & bm).sum()/union:.4f}   MAE {np.abs(co-cm).mean():.4f}   "
          f"(>0.80 好, 0.65~0.80 形状对但边缘有差, <0.5 基本是另外一个形状)")
    if a.shift > 0:
        best = max(((shifted_iou(bo, bm, dy, dx), dy, dx)
                    for dy in range(-a.shift, a.shift + 1)
                    for dx in range(-a.shift, a.shift + 1)))
        iou0 = shifted_iou(bo, bm, 0, 0)
        if best[1:] != (0, 0):
            print(f"最佳对齐 IoU {best[0]:.4f}"
                  f"（复刻需移 dy={best[1]:+d} dx={best[2]:+d} 设备像素）"
                  f"，原地 {iou0:.4f} —— 差值就是「位置错」而不是「形状错」，先修位置")
        else:
            print(f"±{a.shift} 设备像素内已是原位最优 —— 剩下的差是形状，不是位置")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dpr", type=float, default=3.0, help="截图的设备像素比，默认 3")
    p.add_argument("--bg", type=float, default=None,
                   help="背景灰度。先量页面底色再给：纯白 255，iOS 常见的 #F7F7F9 是 247，"
                        "深色主题 0~40；不给则按 255 并在全图最常见灰度不是它时警告")
    sub = p.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--orig", required=True)
    common.add_argument("--mine", required=True)
    # 允许 --dpr / --bg 写在子命令后面。以前只能写在前面，写在后面 argparse 直接报
    # "unrecognized arguments"，而报错信息完全没提示该挪位置（实测因此浪费过一轮）
    common.add_argument("--dpr", type=float, default=None, dest="dpr_sub",
                        help="同全局 --dpr，写在子命令前后都可以")
    common.add_argument("--bg", type=float, default=None, dest="bg_sub",
                        help="同全局 --bg，写在子命令前后都可以")

    z = sub.add_parser("mae", parents=[common],
                       help="全图平均绝对差（只看趋势，不单独判像不像）")
    z.set_defaults(func=cmd_mae)

    e = sub.add_parser("elems", parents=[common],
                       help="按清单一次量完所有元素（推荐主用）")
    e.add_argument("--rects", required=True,
                   help="元素清单：每行 `名字,x0,x1,y0,y1[,阈值][,底色][,类型]`，"
                        "类型 text（默认）/icon，`#` 注释")
    e.set_defaults(func=cmd_elems)

    r = sub.add_parser("rows", parents=[common])
    r.add_argument("--thr", type=int, default=DARK, help="算作有墨的灰度阈值，默认 200")
    r.set_defaults(func=cmd_rows)

    c = sub.add_parser("cols", parents=[common])
    c.add_argument("--x", type=float, nargs=2, required=True)
    c.add_argument("--y", type=float, nargs=2, required=True)
    c.add_argument("--gap", type=int, default=3, help="列间隔小于该值算同一个字（设备像素）")
    c.add_argument("--thr", type=int, default=DARK, help="算作有墨的灰度阈值，默认 200")
    c.set_defaults(func=cmd_cols)

    b = sub.add_parser("box", parents=[common])
    b.add_argument("--x", type=float, nargs=2, required=True)
    b.add_argument("--y", type=float, nargs=2, required=True)
    b.add_argument("--thr", type=int, default=DARK, help="算作有墨的灰度阈值，默认 200")
    b.set_defaults(func=cmd_box)

    i = sub.add_parser("iou", parents=[common])
    i.add_argument("--x", type=float, nargs=2, required=True)
    i.add_argument("--y", type=float, nargs=2, required=True)
    i.add_argument("--text-luma", type=float, default=None,
                   help="区域内文字自身的亮度；不给就从原图自动取"
                        "（离背景最远的 5%% 像素的中位数）")
    i.add_argument("--shift", type=int, default=0,
                   help="再搜 ±N 设备像素的最佳对齐，把「位置错」和「形状错」分开；"
                        "输出顺序 dy,dx（y 在前）；0 = 只比原地")
    i.set_defaults(func=cmd_iou)

    a = p.parse_args()
    a.dpr = a.dpr_sub if a.dpr_sub is not None else a.dpr
    a.bg = a.bg_sub if a.bg_sub is not None else a.bg
    # 记下 --bg 是不是用户显式给的：没给时按 255 算，但底色不是纯白就要出声，
    # 否则每个元素的墨量都系统性偏重，看起来像「字重没调对」
    a.bg_given = a.bg is not None
    if a.bg is None:
        a.bg = 255.0
    a.func(a)


if __name__ == "__main__":
    sys.exit(main())
