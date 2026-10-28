# 最小例子：一张统计卡

一个能跑通的完整闭环，30 秒。裁自一张 iPhone 截图（390×96 CSS px，@3x），
故意只保留一块：三个大数字、三行浅灰说明、两条 0.3333px 的分隔线 ——
正好覆盖「深色数字 / 浅灰小字 / 亚像素细线」三种判据。

```
source.png   原图裁切（1170×288，dpr 3）
work.html    复刻稿，按 CSS px 写
rects.txt    元素清单，8 个元素
render.png   渲染结果 ← 由 render.py 生成
expected.txt 下面第 3 步应该看到的输出
```

## 跑一遍

```bash
cd examples/stats-card

# 1. 渲染：尺寸和 dpr 必须和原图一致（1170÷390 = 3）
python3 ../../scripts/render.py work.html render.png 390 96 3

# 2. 先在原图上自比：四边必须全 0、墨量比 1.000、IoU 1.000
python3 ../../scripts/measure.py elems --orig source.png --mine source.png --rects rects.txt

# 3. 再和复刻比
python3 ../../scripts/measure.py elems --orig source.png --mine render.png --rects rects.txt
```

第 2 步是零成本的。它要是不干净，问题在你的窗口坐标或阈值，不在 CSS ——
先修清单，别急着改稿子。

（Windows 上把三条命令里的 `python3` 换成 `python`，其余不变。）

## 什么样的输出算过关

```
数字1     +1.0  +0.0  +0.0  +0.0 | 1.002 | 0.954
分隔线1   +0.0  +0.0  +0.0  -1.0 | 1.008 | 1.000
说明1     +1.0  +0.0  +0.0  +0.0 | 1.000 | 0.741
```

- **四边 Δ ≤ 1 设备像素**，多数是 0。1 设备像素 = 0.333 CSS px，是 Chrome
  布局量子化的刻度，再抠下去经常逐字节相同。
- **墨量比 0.97–1.03**。这里最差 0.7%。
- **文字 IoU 不当绝对线**：`说明1` 只有 0.741，但它的四边和墨量都对 ——
  浅灰小字在 iOS 和 Chrome 上的栅格化本来就不同。IoU 只看改动前后有没有涨。
- **细线看峰值**：两条 0.3333px 的分隔线整窗比是 0.911 / 0.946，看着像偏细 10%，
  按峰值行算是 0.8% —— 整窗比混进了 JPEG 振铃，那是判据之外的噪声。

## 换自己的图

`rects.txt` 每行是 `名字,x0,x1,y0,y1[,阈值][,底色][,类型][,Y文字]`，
坐标全部用 **CSS px**（设备像素 ÷ dpr）。完整格式见
[`reference/elements.example.txt`](../../reference/elements.example.txt)。

**别把窗口框成一大片同色**。这张卡如果整张框进去，窗口里 90% 是白卡片，
底色会被认成 255 而不是页面的 247，墨量比和 Δ 都会失真 —— 工具会警告
「底色可能不对」，看到这句话先把窗口收小，或者补上第 8 列底色。
