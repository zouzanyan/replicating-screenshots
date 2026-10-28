#!/usr/bin/env python3
"""把位图的形状追踪成 SVG path，用来还原「没有对应字体/图标库」的图形。

两种输入:
  --image x.png --alpha
      带 alpha 通道的图标渲染（如 SF Symbols 用 NSImage 渲染出来的 PNG）。
      取 alpha 的 0.5 等值线。**输出坐标单位 = 源图像素**：
      viewBox 直接用源图尺寸，再给 svg 设 CSS 宽高即可等比缩放。
      例：100 px/pt 渲染的 wifi（1800x1300 px）→ viewBox="0 0 1800 1300"，
      设 width="18px" 就是 18pt 宽。

  --image shot.jpg --x X0 X1 --y Y0 Y1 --text-luma 118
      截图里的一小块。取「覆盖度」的 0.5 等值线。
      对对称模糊（JPEG、抗锯齿）来说，0.5 那条线就是真实轮廓所在，
      所以连糊掉的截图也能量出亚像素级形状。输出单位 = CSS px，相对区域左上角。

原理: marching squares 取等值线 → 端点对齐拼接成闭合环 → Douglas-Peucker 简化。

注意: 所有环的绕向一致，所以带孔的图形（如圆环图标）必须
      fill-rule="evenodd"，否则孔会被填成实心。
"""

import argparse
import sys

import numpy as np
from PIL import Image

T = 0.5
MAX_GRID = 2600  # 工作网格长边上限，超过就自动降低上采样倍数


def gauss(a, sigma):
    """可分离高斯（reflect 边界）。用 numpy 实现是为了不依赖 scipy。"""
    if sigma <= 0:
        return a
    r = int(np.ceil(sigma * 3))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    p = np.pad(a, ((0, 0), (r, r)), mode="reflect")
    out = np.apply_along_axis(lambda v: np.convolve(v, k, mode="valid"), 1, p)
    p = np.pad(out, ((r, r), (0, 0)), mode="reflect")
    return np.apply_along_axis(lambda v: np.convolve(v, k, mode="valid"), 0, p)


def field_from_alpha(path):
    im = Image.open(path).convert("RGBA")
    return np.asarray(im).astype(np.float64)[:, :, 3] / 255.0


def field_from_coverage(path, x, y, text_luma, dpr, mask=None, bg=255.0):
    g = np.asarray(Image.open(path).convert("L")).astype(np.float64)
    h, w = g.shape
    if not (0 <= x[0] < x[1] and 0 <= y[0] < y[1]):
        sys.exit(f"区域不合法：x {x[0]}-{x[1]} y {y[0]}-{y[1]}")
    if x[1] * dpr > w + 0.5 or y[1] * dpr > h + 0.5:
        sys.exit(f"区域超出图片范围：到 ({x[1]},{y[1]}) CSS px，图只有 "
                 f"{w/dpr:.2f}x{h/dpr:.2f} CSS px（dpr {dpr:g}）—— 多为 --dpr 或坐标写错")
    sub = g[int(y[0] * dpr):int(y[1] * dpr), int(x[0] * dpr):int(x[1] * dpr)]
    cov = np.clip((bg - sub) / (bg - text_luma), 0.0, 1.0)
    if mask:
        cx, cy, r = mask
        h, w = cov.shape
        X = (np.arange(w) + x[0] * dpr + 0.5) / dpr
        Y = (np.arange(h) + y[0] * dpr + 0.5) / dpr
        XX, YY = np.meshgrid(X, Y)
        cov[np.hypot(XX - cx, YY - cy) > r] = 0.0
    return cov


def _cross(v0, v1):
    """v0 → v1 之间 T 的位置参数（0..1），相等时取中点。"""
    den = v1 - v0
    safe = np.abs(den) > 1e-12
    t = np.where(safe, (T - v0) / np.where(safe, den, 1.0), 0.5)
    return np.clip(t, 0.0, 1.0)


def marching_squares(f):
    """向量化 marching squares，返回 [(p0, p1), ...]，坐标为网格坐标。"""
    a, b = f[:-1, :-1], f[:-1, 1:]
    c, d = f[1:, 1:], f[1:, :-1]

    k = ((a > T).astype(np.uint8)
         | ((b > T).astype(np.uint8) << 1)
         | ((c > T).astype(np.uint8) << 2)
         | ((d > T).astype(np.uint8) << 3))

    ji, ii = np.nonzero(k)          # 只算有等值线穿过的格子
    kk = k[ji, ii]
    a, b, c, d = a[ji, ii], b[ji, ii], c[ji, ii], d[ji, ii]

    top = np.stack([ii + _cross(a, b), ji.astype(np.float64)], axis=1)
    right = np.stack([(ii + 1).astype(np.float64), ji + _cross(b, c)], axis=1)
    bottom = np.stack([ii + _cross(d, c), (ji + 1).astype(np.float64)], axis=1)
    left = np.stack([ii.astype(np.float64), ji + _cross(a, d)], axis=1)

    pairs = {1: [(left, top)], 14: [(left, top)],
             2: [(top, right)], 13: [(top, right)],
             3: [(left, right)], 12: [(left, right)],
             4: [(right, bottom)], 11: [(right, bottom)],
             6: [(top, bottom)], 9: [(top, bottom)],
             7: [(left, bottom)], 8: [(left, bottom)],
             5: [(left, top), (right, bottom)],
             10: [(top, right), (left, bottom)]}

    segs = []
    for case, plist in pairs.items():
        m = kk == case
        if not m.any():
            continue
        for p0, p1 in plist:
            segs.append((p0[m], p1[m]))
    if not segs:
        return []
    p0 = np.concatenate([s[0] for s in segs])
    p1 = np.concatenate([s[1] for s in segs])
    return [(tuple(x0), tuple(x1)) for x0, x1 in zip(p0.tolist(), p1.tolist())]


def stitch(segs):
    def key(p):
        return (round(p[0], 6), round(p[1], 6))

    adj = {}
    for a, b in segs:
        ka, kb = key(a), key(b)
        adj.setdefault(ka, []).append(kb)
        adj.setdefault(kb, []).append(ka)
    used, loops = set(), []
    for start in adj:
        for nb in adj[start]:
            if (start, nb) in used:
                continue
            loop, cur = [start], nb
            used.add((start, nb)); used.add((nb, start))
            while cur != start:
                loop.append(cur)
                nxt = [n for n in adj[cur] if (cur, n) not in used]
                if not nxt:
                    break
                n = nxt[0]
                used.add((cur, n)); used.add((n, cur))
                cur = n
            if len(loop) > 3:
                loops.append(loop)
    return loops


def dp(pts, eps):
    """Douglas-Peucker，距离按**线段**算而不是按无限长直线算。

    按直线算的时候，落在弦延长线上的点会被误判成「贴着弦」而丢掉 ——
    刀切的直角拐角正好是这种情况：拐角在弦的延长方向上，垂直距离却接近 0，
    于是每一版追踪出来的方角都被悄悄抹圆了（实测面积少 1%）。
    """
    if len(pts) < 3:
        return pts
    keep = np.zeros(len(pts), bool)
    keep[0] = keep[-1] = True
    stack, P = [(0, len(pts) - 1)], np.array(pts, float)
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        a, ab = P[i], P[j] - P[i]
        L2 = float(ab @ ab)
        seg = P[i + 1:j] - a
        if L2 < 1e-12:
            d = np.hypot(seg[:, 0], seg[:, 1])
        else:
            t = np.clip((seg @ ab) / L2, 0.0, 1.0)
            d = np.hypot(*(seg - t[:, None] * ab).T)
        m = int(np.argmax(d))
        if d[m] > eps:
            k = i + 1 + m
            keep[k] = True
            stack.append((i, k))
            stack.append((k, j))
    return [pts[i] for i in range(len(pts)) if keep[i]]


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--image", required=True)
    p.add_argument("--alpha", action="store_true", help="用 alpha 通道（图标 PNG）")
    p.add_argument("--x", type=float, nargs=2, help="CSS px [x0 x1]")
    p.add_argument("--y", type=float, nargs=2, help="CSS px [y0 y1]")
    p.add_argument("--dpr", type=float, default=3.0)
    p.add_argument("--text-luma", type=float, default=118.0,
                   help="文字自身亮度；先量内部最暗像素再填这里")
    p.add_argument("--bg", type=float, default=255.0,
                   help="背景亮度。深色主题必须给（浅色文字压深色底时它 > --text-luma）")
    p.add_argument("--mask-circle", type=float, nargs=3, metavar=("CX", "CY", "R"),
                   help="只保留圆内的墨（去掉附近的圆环等干扰）")
    p.add_argument("--blur", type=float, default=0.55,
                   help="追踪前的高斯 sigma（设备像素），压掉 JPEG 振铃；0 = 不模糊")
    p.add_argument("--up", type=int, default=8,
                   help="上采样倍数；会按工作网格 %d 自动封顶" % MAX_GRID)
    p.add_argument("--eps", type=float, default=None,
                   help="Douglas-Peucker 容差（网格像素），越大越简洁。"
                        "默认 alpha 模式 0.8（干净的抗锯齿边缘，尖角保得住），"
                        "覆盖度模式 1.6（JPEG 有振铃，放宽一点免得把噪声当成形状）")
    a = p.parse_args()
    if a.eps is None:
        a.eps = 0.8 if a.alpha else 1.6

    if a.alpha:
        f = field_from_alpha(a.image)
        unit, x0, y0 = 1.0, 0.0, 0.0
        f = gauss(f, a.blur)
    else:
        if not a.x or not a.y:
            p.error("--x/--y 必填（除非用 --alpha）")
        if abs(a.bg - a.text_luma) < 1.0:
            p.error("--bg 与 --text-luma 太接近，覆盖度无意义（深色主题记得给 --bg）")
        f = field_from_coverage(a.image, a.x, a.y, a.text_luma, a.dpr,
                                a.mask_circle, a.bg)
        unit, x0, y0 = a.dpr, a.x[0], a.y[0]
        f = gauss(f, a.blur)

    up = max(1, min(a.up, MAX_GRID // max(f.shape)))
    if up != a.up:
        print(f"# 源图 {f.shape[1]}x{f.shape[0]} 已经够大，上采样 {a.up}→{up}", file=sys.stderr)

    if (f > T).sum() == 0:
        sys.exit("区域里没有墨（没有任何像素达到 0.5 覆盖度）："
                 "检查 --x/--y 是否框错、--text-luma 和 --bg 是否填反")

    if up > 1:
        im = Image.fromarray(f.astype(np.float32), mode="F")
        f = np.asarray(im.resize((f.shape[1] * up, f.shape[0] * up),
                                 Image.BICUBIC)).astype(np.float64)
    k = unit * up

    loops = stitch(marching_squares(f))
    loops.sort(key=len, reverse=True)
    W, H = f.shape[1] / k, f.shape[0] / k

    def conv(pt):
        # +0.5：marching squares 的网格点是像素中心，连续坐标要补半格
        return ((pt[0] + 0.5) / k, (pt[1] + 0.5) / k)

    parts, npts = [], 0
    for loop in loops:
        pl = dp(loop, a.eps)
        npts += len(pl)
        if len(pl) < 3:
            continue
        pts = [conv(q) for q in pl]
        d = f"M{pts[0][0]:.3f},{pts[0][1]:.3f}"
        d += "".join(f"L{x:.3f},{y:.3f}" for x, y in pts[1:]) + "Z"
        parts.append(d)

    ys, xs = np.where(f > T)
    ix0, iy0 = conv((xs.min(), ys.min()))
    ix1, iy1 = conv((xs.max() + 1, ys.max() + 1))
    print(f"<!-- 源区域原点 ({x0:.2f},{y0:.2f})，1 单位 = {unit:.2f} 源像素；"
          f"墨迹 x {x0+ix0:.2f}-{x0+ix1:.2f} y {y0+iy0:.2f}-{y0+iy1:.2f}，"
          f"环数 {len(parts)}，点数 {npts} -->", file=sys.stderr)
    print(f'viewBox="0 0 {W:.3f} {H:.3f}"')
    print('<path fill-rule="evenodd" d="' + " ".join(parts) + '"/>')


if __name__ == "__main__":
    sys.exit(main())
