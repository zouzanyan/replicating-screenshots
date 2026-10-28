# Claude Code 扩展

一个 Claude Code marketplace。

## 添加这个市场

```
/plugin marketplace add zouzanyan/replicating-screenshots
```

## 里面的插件

### replicating-screenshots-to-html

把一张截图（App 页面、网页、设计稿导出图）复刻成 HTML/CSS，用**数字**收敛到像素级，
而不是靠肉眼说「差不多」。

跟「让模型看一眼觉得像」的做法不同，这里把差异拆成可解释的量：逐元素墨量比、四边 Δ（设备像素）、
覆盖率 IoU、局部 MAE、逐字列区间。配三个脚本 + 81 条测试。

```
/plugin install replicating-screenshots-to-html --marketplace zouzanyan/replicating-screenshots
```

详细说明见 [plugins/replicating-screenshots-to-html/README.md](plugins/replicating-screenshots-to-html/README.md)。

## 结构

```
.claude-plugin/marketplace.json                 市场清单
plugins/replicating-screenshots-to-html/
├── .claude-plugin/plugin.json                  插件清单（name/version 必须和市场清单一致）
├── .claude-plugin/icon.png                     列表图标，方图 512–2048px
├── README.md                                   市场页渲染的是这一份
├── LICENSE
└── skills/replicating-screenshots-to-html/     插件本体
```

## 许可

MIT，© 2026 zouzanyan。
