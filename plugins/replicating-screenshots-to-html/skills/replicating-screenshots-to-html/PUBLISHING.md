# 发布到 skills 市场

这个包按 skill 的形态组织（`SKILL.md` 在根）。要发到 Claude Code 的 plugin 市场，
需要在外面套一层 plugin 目录 —— 两边的差别只有 `SKILL.md` 的深度和两个清单文件。

## 1. 摊成 marketplace 仓库

```
replicating-screenshots/                      ← git 仓库根
├── .claude-plugin/
│   └── marketplace.json
└── plugins/
    └── replicating-screenshots-to-html/
        ├── .claude-plugin/
        │   ├── plugin.json
        │   └── icon.png                     ← 从 assets/icon.png 拷过来，必须在插件根
        ├── README.md                        ← 从 skill 根拷一份（市场页渲染的是它）
        ├── LICENSE
        └── skills/
            └── replicating-screenshots-to-html/
                └── （本包的全部内容）
```

**图标只能放插件根**（`.claude-plugin/icon.png` 或 `assets/icon.png`），
放进 `skills/<name>/` 里校验器找不到。本包用 1024×1024 方图；尺寸/体积的官方规则
没在现行文档里写死，见第 5 节。

**插件根的 README 不是原样拷。** 市场页渲染它，相对链接以插件根为基准，而 skill 里的
`assets/cover.png`、`examples/stats-card/` 都在下一层，直接拷过去会 404。拷完要改三处：

- 首图 `assets/cover.png` → `skills/<name>/assets/cover.png`
- 例子链接 `examples/stats-card/` → `skills/<name>/examples/stats-card/`
- 「目录」代码块改成插件根视角，或者加一句「以下路径都在 `skills/<name>/` 下」

## 2. 两份清单

`.claude-plugin/marketplace.json`：

```json
{
  "$schema": "https://anthropic.com/claude-code/marketplace.schema.json",
  "name": "zouzanyan",
  "description": "Claude Code 扩展",
  "owner": { "name": "zouzanyan" },
  "plugins": [
    {
      "name": "replicating-screenshots-to-html",
      "description": "把截图复刻成像素级 HTML/CSS：逐元素量墨量、位置与字形，用数字收敛而不是靠肉眼。",
      "author": { "name": "zouzanyan" },
      "source": "./plugins/replicating-screenshots-to-html",
      "category": "development",
      "homepage": "https://github.com/zouzanyan/replicating-screenshots"
    }
  ]
}
```

`plugins/…/.claude-plugin/plugin.json` 的 `name` / `version` 必须和 marketplace 条目一致，
`claude plugin tag` 会校验这一点。

## 3. 提交前跑一遍

**两个目录要用不同的命令验**，一条命令验不了全部：

```bash
# 市场清单（仓库根）
claude plugin validate .

# 插件本体（插件根）：目录收录时的检查子集——缺图标、缺 README、缺安装行都会拦
claude plugin validate plugins/replicating-screenshots-to-html --profile directory --strict
```

`--profile directory` 要的是一个**插件**目录（有 `.claude-plugin/plugin.json`，或
`skills/<name>/SKILL.md`）。拿它去验仓库根会报
「`--profile directory` needs a plugin folder」。`--strict` 把警告也当错误。
本包当前两条都是 **✔ Validation passed**。

它会拦的三件事：**缺图标、缺 README、README 里没有一行可复制的安装命令**。所以插件根的
`README.md` 里必须留着：

```
/plugin install replicating-screenshots-to-html --marketplace zouzanyan/replicating-screenshots
```

## 4. 两条上架路线，别混

| | **自建市场** | **Anthropic 目录** |
|---|---|---|
| 怎么进 | push 一个带 `marketplace.json` 的仓库 | 提审：`claude.ai/directory/manage` |
| 审核 | 无，push 完就算发布 | 有，人工 |
| 门槛 | 无 | 付费 claude.ai 计划（Pro/Max/Team/Enterprise）+ 公开 GitHub 仓库 |
| 用户怎么装 | `/plugin marketplace add zouzanyan/replicating-screenshots` | 账号同步，显示成 `<name>@synced` |
| 版本 | 自己维护 | 每次发版必须 bump |

**`claude-plugins-official` 没有公开提交通道。** 官方文档写的是：这个市场不通过目录门户收稿，
要做官方收录得走 Anthropic 合作伙伴联系人。公开的自助入口是 **Anthropic 目录**，不是官方市场 ——
两者是不同的东西。`claude-plugins-community` 是同一套审核流水线的只读镜像，往它的仓库开 PR 会被自动关闭。

自建市场**没有**图标、README、分类、许可证的强制要求（schema 里 `category`/`tags` 都是自由字符串，
`license` 只是展示字段）。下节的硬性要求只对 Anthropic 目录生效。

## 5. Anthropic 目录收录的硬性要求

来自官方 pre-submission checklist，逐条对过本包：

| 要求 | 本包 |
|---|---|
| 公开 GitHub 仓库 | ⬜ 待建 |
| 插件目录里的 README ≥ 40 词（代码块不算） | ✅ |
| `LICENSE` 文件或 `plugin.json` 里有 `license` | ✅ 两者都有 |
| 名字全小写字母/数字/连字符，≤64 字符，首尾字母数字 | ✅ `replicating-screenshots-to-html` |
| ≤512 个文件 | ✅ 32 |
| 单个非图片/字体文件 ≤256 KiB | ✅ 最大 39 KB（`measure.py`） |
| 每次发版 bump `version` | ⬜ 发布流程 |
| 图标：方图、512–2048px、<2MB | ✅ 1024×1024 —— 但见下方说明 |

**图标规格是我没在现行文档里找到的一条。** 文档只定义了 `plugin.json` 的 `icon` 字段（图片路径），
没有写尺寸和体积上限；这条要么在门户的 Listing details 步骤里卡，要么已经废了。
本地 `claude plugin validate --profile directory` 确实会检查图标存在，所以图先按 1024×1024 方图准备着不会错。
另外**图标第一次提交就锁定**，之后换图不更新列表里的图标，所以定稿再提。

`plugin.json` 里还有五个只被目录读取、Claude Code 加载时完全忽略的字段：
`icon`、`documentationUrl`、`supportUrl`、`privacyPolicyUrl`、`termsOfServiceUrl`——
它们只能写在 `plugin.json`，写进 marketplace 条目会被 validate 报 unknown。

## 6. 发版：用户什么时候能拿到新版

看**计算出来的版本号**，优先级是：

1. `plugin.json` 的 `version` ← 本包设了 `1.0.0`，所以走这条
2. marketplace 条目里的 `version`
3. 两处都没有时按源码派生：git 托管市场里的相对路径插件 = **commit SHA**

**所以本包每次发版都要改 `plugin.json` 的 `version` 再 push。** 只 push 不改号，
`claude plugin update` 会认为没变化，用户拿不到。

想让每次 push 都生效，就把两处 `version` 都删掉，退回第 3 种（commit SHA 追踪）。

**git tag 不是必需的** —— 它只在两种情况下有用：别的插件用 semver `dependencies` 声明了对本包的
依赖范围（要按 `<name>--v<version>` 标签解析），或者用户用 `#<ref>` 钉住了市场源。要打的话：

```bash
# 在市场仓库根跑，要带上插件路径——根目录没有 plugin.json，不带会报 No plugin manifest found
claude plugin tag plugins/replicating-screenshots-to-html --push
```

它会往前走目录找 `marketplace.json`，比对版本号；工作区脏、标签已存在、两处版本不一致都会退出 1。
先 `--dry-run` 看它打算打什么标签。
改版本号要同时改 `plugin.json` 和 `SKILL.md` 的 frontmatter（后者只是给人看的）。

## 7. 打 skill 分发包（不走 plugin 市场的用户）

```bash
cd .. && zip -r replicating-screenshots-to-html-1.0.0.zip replicating-screenshots-to-html \
  -x '*/__pycache__/*' -x '*.pyc' -x '*.DS_Store' -x '*/examples/*/render.png'
```

`__pycache__` 必须排掉：跑过一次测试就会生成，`.gitignore` 拦得住 git，拦不住 zip。
`examples/*/render.png` 是渲染产物，让用户自己跑第 1 步生成，别进包。

**排除模式必须带前导 `*/`** —— zip 是拿整条路径去匹配的，
写 `examples/*/render.png` 一条都排不掉（实际路径是 `<包名>/examples/stats-card/render.png`）。
