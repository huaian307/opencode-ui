# AGENTS.md — opencode-ui 项目约定与关键知识

> 本文件会被 OpenCode **自动读取**（作为新会话的指令）。新对话请先读完本文件 ——
> 原 `PROGRESS.md` 的内容已**合并进本文件**；`PROGRESS.md` 从此只保留
> **历史沿革 / 详细原始数据 / 路线图**，需要考古时再读。
> 最后更新：2026-09-23（前端 `ui 260923h`）

## 这是什么

一个**自建的 OpenCode 前端**。不修改官方桌面版，独立运行、外观完全自控。
配套一个守护进程，实现「OpenCode 启动 → 拉起面板窗口」「关掉面板 → 关掉 OpenCode」的联动。

- 项目根目录：`C:\Users\TWG\Documents\opencode-ui`
- 用户语言：**中文**（界面文案、注释、文档都用中文）
- UI 主题：**《龙族》上杉绘梨衣**（白昼＝白衣绯袴，夜＝黑纹付羽织·黄金瞳）

## 当前运行态（2026-09-23，仅作快照，随时会变）

```
面板服务   8787（pid 24420）    /healthz → "ui":"260923h"
守护进程   8788（pid 24340，单实例锁）；面板心跳正常（beats 1100+）
上游       OpenCode 2.0.13，端口每次重启都变 → 永远读 service.json
面板窗口   Edge --app，独立 profile，1 个窗口（+20 余个渲染子进程）
系统音量   Core Audio 真值（实测 22；旧 winmm 会谎报 100）
频谱采集   .audio-venv 的 spectrum.py，48 段，设备 = 扬声器 (Realtek(R) Audio)
           （"跟着默认输出设备走"在工作：蓝牙切回来会自己跟上）
QQ音乐     QQMusic.exe + smtc-daemon.ps1，_music.json 每 500ms 刷新
动态壁纸   昼/夜由用户在面板里选 → _wallpapers.json（当前 昼=4K60 世界很温柔、夜=Kuroha欲语）
界面设置   背景亮度 昼100%/夜80% · 毛玻璃 18px · 壁纸速度 0.80× · 动态壁纸开 · 落樱开
           （存 localStorage，顶栏「设」按钮可改）
开机自启   已装（启动文件夹 → launch.vbs → pythonw watch.py）
联动开关   KILL_OPENCODE = True
时序常量   TICK 0.4s / POLL 2.0s / GRACE 1.0s / MINIMIZE_DELAY 1.5s / BEAT_TIMEOUT 8s
```

⚠ `KILL_OPENCODE` **只在守护进程启动时算一次**，事后放 `no-kill` 文件对它**无效**（见踩坑 #16）。

> 更细的"本轮做了什么、已知限制、下一步"见 `PROGRESS.md` 开头的**当前进度总结**。

## 环境硬约束

| 项 | 值 |
| --- | --- |
| 系统 Python | `D:\Python312\python.exe`（**仅标准库**，无 PIL、无 PDF 库） |
| 无窗口运行 | `D:\Python312\pythonw.exe` |
| **没有 Node / npm** | 前端必须是**原生 JS，无构建步骤**，不要引入打包器 |
| 有 Pillow 的解释器 | `opencode-ui\lession\.venv\Scripts\python.exe`（借用，别往里面装东西） |
| 临时 PDF 环境 | `%TEMP%\pdftool\Scripts\python.exe`（pypdf 6.19.0，隔离，可随时删） |
| 网络 | 可访问外网；**pixiv.net / i.pximg.net 被墙超时**，`pixiv.re` 反代可通 |

## 主题与素材

**两套主题**（`web/style.css`，切换状态持久化）

| 主题 | 配色 | 背景 |
| --- | --- | --- |
| `hiru`（昼，默认） | 和纸白 + 绯红 + 金线 | `hiru-bg.jpg` + 和纸浅蒙层（`.34/.44/.58`） |
| `yoru`（夜） | 夜墨 + 绯红提亮 + 黄金瞳金 | `night.jpg` + 深色渐变 + **毛玻璃面板** |

**设定依据**（查过资料，不是凭印象）：巫女服＝白衣＋绯袴；因言灵·审判不能说话、靠写字小本子交流
（→ 输入框命名为「絵梨衣の小本子」）；会给物品标「絵梨衣の××」（→ 用在标题/标签）；
暗红发＋金瞳（龙化「金边红仁」）；初见时黑纹付羽织＋黑纱遮面（→ 夜主题）；心爱小黄鸭；
题词「落尽红樱君不见，轻绘梨花泪沾衣」。

**素材**（`web/assets/`，全部本地；同人图仅自用）

| 文件 | 大小 | 用途 |
| --- | --- | --- |
| `avatar.png` | 82 KB | 助手消息圆形头像（从 39 号参考图裁头部，避开了作者水印） |
| `hiru-bg.jpg` | 312 KB | 白昼背景（896×1152，+28% 饱和 / +10% 对比 / −6% 亮度） |
| `night.jpg` | 110 KB | 夜主题背景（1000×563） |
| `hero.jpg` | 354 KB | 空会话主插画（1600×1600） |
| `badge.jpg` | 11 KB | 左上角头像 |
| `sticker-duck.jpg` / `sticker-couple.jpg` | 8/8 KB | 空状态贴纸 |

原图与备份在 `_refs/`（含 `hiru-bg-original.jpg`）、`_refs_q/`；
筛选过程留下两个画廊页 `web/refs.html`、`web/refs_q.html`（含逐张判断：推荐/存疑/不建议）。

**动态壁纸（收起侧栏时才动，素材是 Wallpaper Engine 的原片）**

- **素材**：直接播创意工坊里的 mp4 原片（**不复制、不转码、不占项目空间**）：
  - **昼 `hiru`** → 「**［绯莎］绘梨衣 sakura**」（id `3786830950`，4K，64.7 MB）
  - **夜 `yoru`** → 「**月读命绘梨衣**」（id `3568633530`，4K，55.7 MB；其 mp4 文件名是一串哈希）
- **怎么找片**：`server.py` 的 `WE_WALLPAPERS` 支持三种候选写法，按顺序取第一个命中的：
  `"title:关键字"`（扫各壁纸 `project.json` 的**标题**，最稳）/ `"id:文件夹名"` / 其余当相对 glob。
  ⚠ 不要指望按文件名匹配：那个夜主题的 mp4 就叫 `a9dc0adb…mp4`。
- **接口**（`server.py`）：`GET /live/wallpapers` → `{ok, root, hiru, yoru, detail:{主题:{title,file,size_mb,url}}}`
  （`detail` 是给人核对的：一眼看出当前选中的是哪张壁纸）；
  `GET /we/<相对 431960 的路径>` → 流式发文件，**支持 Range（206）**。
  `_send_file()` 是共用的，所以 `_static` 也顺带有了 Range。
- **行为**：`#live-bg` 有原片时**一直上屏**（`has-live`）；收起时在播、展开时**暂停在同一帧**。
  - **收起 → 缓慢启动**：`playbackRate` 从 **0.3** 线性加到 0.8（0.6s）。
  - **展开 → 缓慢停止**：`playbackRate` 线性减到 0.3（1.1s）→ `pause()`。
    **停住的那一帧就是会话界面的静态背景 —— 直接暂停视频本身，不再截图**
    （`#bg-freeze` / `has-freeze` / `canvas.toDataURL` 那一套已经删掉）。
  - **换片（含昼夜切换）不黑屏**：换 `src` 时先 `classList.remove("ready")` 把视频**立刻藏起来**
    （⚠ `<video>` 没解码出帧时会**画黑**并盖住下层静态壁纸 —— 这就是"切昼夜一大块黑屏"的原因），
    此时由 `#live-bg` 自带的主题静态壁纸顶着；`loadeddata` 拿到**第一帧**才加 `.ready` 淡入。
    CSS 上**藏要立刻藏**（基础态不设 transition），只有 `.ready` 才带过渡。
  - ⚠ **片头黑场仍要处理**（踩过：会话界面切主题后一直黑）：动态壁纸片头常是淡入黑场。
    现在暂停后会量一下帧亮度（`frameMean()`，`< DARK(12)` 视为黑场），太黑就 seek 到 1.0/2.5/4.0s
    再停；**全程只量亮度、不抓帧**；实在全黑就撤掉 `.ready`，让主题静态壁纸顶着 —— 任何情况下都不会黑底。
  - ⚠ **`playbackRate` 有下限 0.0625**（1/16）：给更小会抛 `NotSupportedError`，而且那个异常会**把整个流程打断**
    （踩过：0.06 → "收起后完全没反应"）。所有赋值都走带 try/catch 的 `setRate()`。
  - 主题或侧栏一变就用 `MutationObserver` 同步；每 5 分钟重拉一次清单（新订阅免刷新）。
- **界面设置（顶栏「设」按钮 → `#cfg` 弹窗）**：目前有
  背景亮度（白昼/夜晚分开）、背景毛玻璃（模糊半径 0–60px，默认 18）、动态壁纸速度、
  收起时是否播放动态壁纸、落樱、「选壁纸…」（转开壁纸选择器）、恢复默认。
  **全部存在 `localStorage["opencode-ui.settings"]`，改完立刻生效**。
  实现：`SET`（`SET_DEFAULT` = 默认值）+ `loadSettings/saveSettings/applySettings/setSetting/renderSettings`；
  `applySettings()` 把亮度/模糊写成 `<html>` 上的**内联 CSS 变量**、把速度写进 `liveRate`、
  落樱切 `body.no-petals`；并调 `liveApply()` 让动态壁纸立刻跟随。**以后新的可调项往这里加就行。**
- **背景毛玻璃**：`--bg-blur`（默认 `18px`）。`#live-bg`（视频/暂停帧那一层）
  **只在展开（`html[data-sidebar="open"]`）时**套，`body::after`（静态兜底层）同理 ——
  收起听歌那一侧保持清晰。
  ⚠ 模糊会把图层边缘和"透明"混出一圈淡边，所以开模糊时给这两层 `inset: -3vmax` 撑出视口
  （用 `html.bg-blurred` 类控制；**收起/展开都撑**，两层几何一致，切换时不会有一点点位移）。
  实测（同一探针 A/B，Pillow 量背景区"清晰度"= 相邻像素平均绝对差）：昼 2.14 → 0.43（**−80%**）、
  夜 0.60 → 0.16（**−74%**）。
- **背景亮度**：按主题分开的两个变量 —— `--bg-dim-hiru`（默认 **1** = 原样）/ `--bg-dim-yoru`（默认 **0.8**），
  `--bg-dim` 再按主题选其中一个；`#live-bg` 与 `body::after` **两层都套**
  `filter: brightness(var(--bg-dim))`，所以"在播/暂停/静态兜底"观感一致。
  实测（同一探针 A/B，Pillow 量背景条亮度）：昼 0.85 时 −14.6%、夜 0.78 时 −21.9%；
  **白昼默认回到 1.00 后实测 229.4 == 原亮度 229.4**。
  ⚠ 弹窗打开时截图量亮度会被遮罩污染（量到 163.8），**要量背景就别开弹窗**。
- **没有动态壁纸时**（没装 WE / 没订阅 / 没抓到片子 / 系统"减少动态效果" / 设置里关掉了）：
  **两侧都用主题自带的静态壁纸**（`body::after` 那张 `hiru-bg.jpg` / `night.jpg`），不做任何动画。
  原来那套纯 CSS 动态层（推拉/视差/光斑/浮尘/光带）**已经删掉**，不再参与。
  视频加载失败（`error` 事件）也会退回这条路。
- **兜底**：找不到 Steam/创意工坊、视频加载失败（`error` 事件）→ 去掉 `has-live`，
  **退回主题自带的静态壁纸**（两侧一致，不再有 CSS 动画层）。
- **实测**：收起时速率 **0.4 → 0.8（755ms 到位）**（缓慢启动）；
  展开时 **0.64 → 0.17 → 停**（缓慢停止）—— 停住的那一帧**直接留在屏幕上**当背景
  （不再有 `#bg-freeze`；`body::after` 在 `has-live` 时让位）；
  实测展开态背景亮度 221.1 / 215.2，与在播态 220.9 / 216.0 基本一致 → **暂停的就是真实画面、不是黑场**。
  没有原片时（stub 掉 `/live/wallpapers`）收起/展开两侧都是静态壁纸（`lb_display=none`、`body::after=block`）。
  两张片子都是 **3840×2160**（播放丢帧：昼 371 帧丢 3）
  注：**夜主题那张原片自带作者水印**（右下角文字），是壁纸本身的内容，不是我们加的。
- **自己挑壁纸（面板内选择器）**：顶栏 🖥 **`壁`** 按钮 → 列出**所有 video 类型**的壁纸
  （scene 是私有格式，浏览器放不了，不列），每行有缩略图（用壁纸自带 `preview.gif`）、
  标题、大小，两个按钮「**白昼** / **夜晚**」把这张指派给对应主题，底部「恢复默认」。
  - 接口：`GET /live/list`（清单 + 当前选择 + 实际生效的 `detail`）、
    `POST /live/pick` `{theme:"hiru"|"yoru", id:"<创意工坊 id>"|null}`（`null` = 恢复默认）。
  - **选择落盘**在项目根的 `_wallpapers.json`（`{"hiru":"3786830950","yoru":"3568633530"}`），
    重启/换浏览器都还在；优先级 **用户选择 > `WE_WALLPAPERS` 默认候选**。
  - 选完立刻换片（前端 `liveReload()`），不用等 5 分钟那次定时重拉。
- ⚠ **性能纪律**：CSS 那套只动 `transform`/`opacity`（合成器层、无 JS 定时器、无 canvas）；
  视频只在收起时解码。系统开了"减少动态效果"时两者都不上，退回静态壁纸。
- 可调：
  - **播放速度** → `app.js` 的 `LIVE_RATE`（当前 **0.8** = 原速的八成；实测真实 6.00s 视频走 4.80s）。
    嫌快就往小调（0.7 / 0.6…），别低于 0.5（会显得顿）。
  - **换片** → `server.py` 的 `WE_WALLPAPERS`（`title:关键字` / `id:文件夹` / 相对 glob）。
  - **只用 CSS 动态层**（不播视频）→ 删掉 `WE_WALLPAPERS` 的候选，或不在 `boot()` 里调 `initLiveBg()`。

## 运行 / 停止 / 开关（速查）

```bat
start.bat          :: 确保服务在跑 + 打开面板窗口
stop.bat           :: 关窗口 + 停服务 + 停守护（OpenCode 毫发无损）
install-startup.ps1 / uninstall-startup.ps1   :: 装/卸开机自启
```

手工重启（按端口认人，不要按名字猜）：

```powershell
# 守护进程占 8788，面板服务占 8787；谁占端口谁就是我们的进程
foreach ($p in 8788,8787) { $q = Get-NetTCPConnection -LocalPort $p -State Listen -EA SilentlyContinue |
  Select-Object -First 1 -ExpandProperty OwningProcess; if ($q) { Stop-Process -Id $q -Force } }
Start-Process D:\Python312\pythonw.exe -ArgumentList "$((Get-Location).Path)\watch.py" -WindowStyle Hidden
```

看运行态：

```powershell
Invoke-WebRequest http://127.0.0.1:8787/healthz -UseBasicParsing | Select -Expand Content
Invoke-WebRequest http://127.0.0.1:8787/alive   -UseBasicParsing | Select -Expand Content
Get-Content 'C:\Users\TWG\Documents\opencode-ui\_watch.log' -Encoding UTF8 | Select -Last 20
```

⚠ 上面 `Get-Content` 用了 `-Encoding UTF8` 才敢读**日志文本**；`web/app.js`、`web/index.html`
这类文件**一律不许**用 `Get-Content`/`Set-Content` 管道（见踩坑 #11）。

⚠️ **`watch.py` 会在面板窗口关闭时杀掉 OpenCode，连带杀掉正在跑的会话。**
要安全地做关窗类实验，先放开关文件：

```bat
echo. > no-kill     :: 关窗不再关 OpenCode（做完实验记得 del no-kill）
```

⚠ **这个保险只对「之后启动的」守护进程有效**：`watch.py` 的 `KILL_OPENCODE` 在进程启动时算一次，
对已经在跑的守护进程放 `no-kill` 完全没用（见踩坑 #16）。
所以想用它，必须**先放文件、再按端口重启守护进程**（命令见上）。

## 架构与数据流

```
面板窗口(Edge --app, 独立 profile  browser-profile/)
      │  http://127.0.0.1:8787
      ▼
server.py  ← 静态前端(web/) + /api/* 反向代理(自动加 Basic 鉴权、流式透传 SSE)
      │  http://127.0.0.1:49374（端口会变，永远从 service.json 读）
      ▼
opencode-cli.exe（OpenCode 自己的后端服务，被桌面版拉起）

watch.py（守护进程，8788 单实例锁）
   ├─ 确保 8787 活着           ├─ OpenCode 启动 → 等后端就绪 → 开面板窗口 → 1.5s 后最小化 OpenCode 窗口
   ├─ 关窗 → 反悔 1s → 关 OpenCode   └─ 靠页面心跳(/heartbeat) + /bye 判断"面板是否还开着"
```

**联动状态机（`watch.py`）**

```
开机 → launch.vbs → pythonw watch.py（无窗口常驻，占 8788 做单实例锁）
        ├─ ensure_server()：8787 不通就拉起 server.py
        ├─ 面板"是否还开着" = 页面心跳（/heartbeat 每 4s）
        │    ├─ 关窗时页面发 navigator.sendBeacon("/bye") → 立刻判定关闭
        │    ├─ 心跳断档 → 兜底看浏览器进程（有句柄用句柄，无句柄用廉价 PID 存活检查）
        │    └─ 刚开窗后有 40s"等待页面加载"窗口
        ├─ OpenCode 启动 → wait_upstream() → open_window() → 1.5s → minimize_opencode()
        ├─ 关窗 → 反悔 1s → kill_opencode()
        └─ 用户主动关窗后 skip_open=True，等 OpenCode 重新启动才再开窗
```

**实测时序**（用 `WM_CLOSE` 模拟点 X，全程挂 `no-kill`）：

| 事件 | 耗时 |
| --- | --- |
| `/alive` 变 false | 0.02s（页面 `/bye` 即时） |
| 守护进程发现关窗 | 0.10s（TICK 0.4s） |
| 反悔期 | 1.006s（设定 1.00s） |
| **从关窗到联动总计** | **1.05s** |

强杀窗口（崩溃 / `Stop-Process -Force`）时页面来不及发 `/bye`，只能等心跳超时 → 约 **8s**。

## 任务栏联动（面板开 = 隐藏，面板关 = 常驻可见）

唤起/开着面板时任务栏**自动隐藏**（鼠标贴底边才浮现），面板关掉后恢复**常驻可见**。
实现：`taskbar.py`（`SHAppBarMessage(ABM_SETSTATE)`，**实测即时生效、不用重启 explorer**）；
`watch.py` 只在「开关事件」上调用它，`stop.bat` 收尾兜底。

- **触发点**：`open_window()`（唤起即隐藏）· 面板由关→开（含外部手动打开的）·
  反悔期结束确认关闭（恢复可见）· 守护启动时按当时面板状态对齐一次。
- **记账**：只有"**我们**藏过"才写标记文件 `_taskbar.hidden`；恢复时据此判断，
  **不会覆盖用户自己**的任务栏设置。守护被强杀（跑不到 `atexit`）时，
  下次守护启动 / `stop.bat`（`watch.py --restore-taskbar`）会自愈。
- ⚠ `ABM_GETSTATE = 0x4`（**`0x2` 是 `ABM_QUERYPOS`**）——用错会读回垃圾值，
  本项目曾据此把"关"误判成"开"（见踩坑 #23）。
- 命令行：`python taskbar.py status|hide|show|restore`（输出纯 ASCII，避开 GBK 控制台）。

## 模型切换（顶栏「模型」按钮）

顶栏 **模型** 按钮 → 弹窗（复用 `.wp` 外壳）：按 provider 分组列出全部模型，带搜索框。

- **模型是「会话属性」**：选中即 `POST /api/session/{id}/model`（**204**），顶栏按钮随即显示该模型 id。
- **没选会话时** → 存成「新会话默认模型」`localStorage["opencode-ui.model"]`，
  下次 `POST /api/session` 会带上（`payload.model`）。
- **variants（思考强度）** 做成一排小按钮：`deepseek-flash` → `none/low/high/max`、
  `muse-spark-*` → `minimal/low/medium/high/xhigh`；点变体即带 `Model.Ref.variant`。
- 每行显示 `id · 免费/付费(in/out) · 工具`；当前模型高亮；「用默认模型」= `GET /api/model/default`。
- **官方图标代替名字**：顶栏按钮里放**模型品牌标**（名字进 tooltip），列表每行也是「图标 + 名称」。
  - 图标是**本地素材** `web/assets/models/*.svg`（8.6 KB），由 `tools/fetch_model_icons.py` 下载；
    ⚠ **OpenCode 的 `/api/model`、`/api/provider` 都没有图标字段**（查证过），
    但 **`models.dev` 就是 OpenCode 的数据源**，所以用 `https://models.dev/logos/<providerID>.svg` 最正统。
  - 解析顺序 `app.js` 的 `MODEL_ICONS`：**模型 id → family → family(去 `-free`) → providerID**；
    本机映射：deepseek→DeepSeek、mimo→小米、ling→蚂蚁、nemotron→NVIDIA；
    **muse-spark / big-pickle 没有公开品牌标 → 青铜字母徽章兜底**（M / B）。
  - `modelFull()` 会拿会话里的轻量 ref 去 `MODEL.list` 里补 `name/family`；所以 boot 里预取一次清单。
- ⚠ 三个踩坑：**`GET /api/session/{id}` 空会话不返回 `model`**（列表才始终带）·
  注入脚本**别在全局声明与 app.js 同名的变量**（我用 `var sleep` 撞了 app.js 的 `const sleep`，
  导致整个 app.js 语法错误、界面全废——无头探针里才发现的）· 新 UI 属性名用 `data-prov/data-id/data-var`，
  **别用 `data-theme`**（会命中主题变量规则，见踩坑 #21）。

## 龙族视觉层（顶栏铭牌 / 设置刻印 / 校徽）

《龙族》（江南）风格的界面语汇，集中在 `style.css` 末尾「龙族视觉层」一节 + `index.html` 的
内联 SVG `#mk-tree`。**设计依据全部来自原著设定**（查过公开资料，非自创）：

| 原著元素 | 用在哪儿 |
| --- | --- |
| 卡塞尔学院校徽「**半朽的世界树**」（半枯半荣） | 弹窗标题旁的内联 SVG（左半枯枝、右半生叶） |
| **青铜**巨门 / 炼金术 / 七宗罪 | 所有按钮＝青铜边＋纸/墨底＋顶面高光；顶栏按钮四角**刻线** |
| 「**黄金瞳**」（龙化·金边红仁） | 滑块圆点（金色径向渐变）、按钮悬停的金色微光 |
| 「**言灵周期表**」的序列观念 | 设置项左侧罗马数字序列（CSS 计数器 I…VIII） |
| **绯袴**（项目原有主色） | 主按钮＝绯红渐变＋金线 |
| 题字 | 设置弹窗「装备部」、模型弹窗「言灵周期表」（`--mk-cap` 金色小字） |

⚠ 一个真实的尺寸坑：顶栏按钮统一是 `width: 34px` 的方图标钮，**更宽的按钮会被截断**
（「模型」曾被截成 `deep`）→ 用 `#topbar .actions #btn-model { width: auto }` 按 id 放开。

## 歌词翻译（外文 → 中文）

`server.py` 的 `qq_lyrics()` 会给每行补 `zh`；前端在歌词浮层的原文下面加一行小字译文。

- **翻译源**：`https://aidemo.youdao.com/trans`（有道 demo，无需 key，实测质量最好：
  `夢ならばどれほどよかったでしょう` → "如果只是一场梦该有多好"）。整段翻（按 8 行一批，保留换行），
  行数对不上就逐行重试。
  **实测不可用的**：QQ 官方 `trans` 字段（6 首热门日文歌全为空）· `fanyi.youdao.com` 老端点（返回 HTML）
  · Google `translate_a/single`（**被墙超时**）· Bing `ttranslatev3`（要 token）。
- **缓存**：内存 6h（`LYRICS_CACHE`）＋**磁盘 7 天** `_lyrics_zh.json`（按 songmid）。第二次起 ~0.5s。
- **中文歌自动跳过**：`_needs_translation()` 看假名/韩文/拉丁/汉字占比，中文歌词不翻、不请求。
- **接口**：`GET /qq/lyrics?title=&artist=&tr=1`（`tr=0` 不翻译，省时间）。
- **前端开关**：设置面板第 7 行「歌词翻译」（默认开，存 `localStorage`，关掉加 `body.no-lyrics-zh`）；
  开关一变会**重新取一次歌词**（`tr=0/1` 不同），不是先翻好再藏。
- ⚠ 有道 demo 是公开接口，可能限流；译文为**机器翻译**，仅作参考。

## OpenCode 后端 API：已探明的事实（别重复踩）

**鉴权**：HTTP Basic，用户名 `opencode`，密码在
`C:\Users\TWG\.local\state\opencode\service.json` → `{ "url", "password", "version", "pid" }`。
**`url` 里的端口每次重启都可能变，必须每次读文件**（`server.py` 就是这么做的）。

**常用接口**

| 用途 | 接口 |
| --- | --- |
| 会话列表 | `GET /api/session?limit=50&order=desc` → `{data:[...], cursor}` |
| 单个会话 | `GET /api/session/{id}` |
| 历史消息 | `GET /api/session/{id}/message?limit=80` |
| 发消息 | `POST /api/session/{id}/prompt` `{text(必填), files?, ...}` |
| 中断 | `POST /api/session/{id}/interrupt`（返回 204） |
| 新建 | `POST /api/session` `{}` |
| 删除 | `DELETE /api/session/{id}`（204）—— **前端会话项右侧的 × 就是调它**（含二次确认；删的是当前会话会清空界面并自动选剩下最新一条） |
| **改名** | `PATCH /api/session/{id}` body `{"title":"..."}`（**204**）—— 用**第一句提问**自动命名（见下）。请求体还接受 `permissions`，我们只传 title |
| 实时事件 | `GET /api/event`（SSE） |
| 完整清单 | `GET /openapi.json`（113 个接口） |
| **模型清单** | `GET /api/model` → `{location,data:[{id,modelID,providerID,name,variants[],cost[],capabilities{}}]}`（本机 **9** 个：7 免费 / 2 付费，4 个带 `variants`） |
| 默认模型 | `GET /api/model/default` → `{location,data:{...}}` |
| **切换模型** | `POST /api/session/{id}/model` body `{"model":{"id","providerID","variant"?}}` → **204**（`Model.Ref`：`id`+`providerID` 必填；不给 `variant` 时服务端填 `"default"`） |
| 切换智能体 | `POST /api/session/{id}/agent` body `{"agent":"build"}`（**还没接前端**） |

⚠ **`GET /api/session/{id}` 只在"会话已经有模型/agent"时才返回 `model`/`agent`**——空会话是 `null`；
   **列表接口（`GET /api/session`）才始终带 `model`**。前端判断"当前用什么模型"要以列表/会话详情里能拿到的为准，别拿 null 当"没模型"去重设。

**消息结构有两种形态（最大的坑）**

```jsonc
// 助手：内容在 content 数组里
{ "type":"assistant", "agent":"build", "model":{"id":"...","providerID":"..."},
  "time":{"created":..,"streamed":..,"completed":..},
  "content":[ {"type":"text","text":"..."},
              {"type":"reasoning","text":"..."},
              {"type":"tool","name":"shell","state":{"status":"completed","input":{...},"output":"..."}} ] }

// 用户：正文在**顶层 text**，没有 content 数组！
{ "type":"user", "text":"...", "files":[...],
  "metadata":{"displayText":"...","attachments":[],"agent":"...","model":{...}} }

// 其他类型：synthetic(自动续写) / system / location-switched / idle —— 都可能有顶层 text
```

**消息列表返回顺序是「新 → 旧」（最新在索引 0）**，渲染前必须按 `time.created` 升序重排。

**会话自动命名（`app.js`）**：新建会话标题先叫「新会话」；用户在这个会话里**发出第一句提问**时，
前端用 `titleFromQuestion()` 压平空白并按 30 字截断（超出加 `…`），再 `PATCH` 改名。
两个条件同时满足才改名：① 标题还是默认值（`新会话`/`(无标题)`/空…）② 会话里还没有用户消息；
另外 `state.named[id]` 记账，**每个会话只自动命名一次**（不依赖消息是否已回传，避免连发两句被改两次）。
用户手动改过名的会话**绝不覆盖**。

**生成中的内容拿不到**：`/message` 只返回**已完成**的消息。实时性只能靠 SSE 增量：

```jsonc
{"type":"session.text.delta",      "data":{"sessionID","assistantMessageID","ordinal","delta"}}
{"type":"session.reasoning.delta", "data":{"sessionID","assistantMessageID","ordinal","delta"}}
{"type":"session.step.started",    "data":{"sessionID","assistantMessageID","agent","model","started"}}
{"type":"session.tool.called",     "data":{"sessionID","assistantMessageID","id","input",...}}
{"type":"session.tool.success",    "data":{...}}
// 终态（这些之后才值得重取一次权威数据）：
// session.text.ended / session.step.ended / session.tool.success / session.execution.succeeded
// session.inbox.enqueued / session.inbox.delivered / session.created / session.renamed
```

一轮回答的事件量级：`reasoning.delta` 约 70 条、`text.delta` 约 32 条 →
**绝不能"每个事件都重取消息"**，那会变成上百次几百 KB 的请求。

**附件**：`prompt` 的 `files[]` 只要求 `uri`（可选 `name`/`description`）。
**`data:` URL 可以直接传**，服务端会规范化成：

```jsonc
{ "data":"<base64 去掉前缀>", "mime":"image/png", "source":{"type":"inline"}, "name":"x.png" }
```

## QQ音乐联动（已接入）

后端在 `server.py`，前端在 `app.js` 的 `initMusic()` 一带。

| 接口 | 作用 |
| --- | --- |
| `GET /qq/state` | 曲目/播放状态/进度/系统音量（`music` 来自 `_music.json`） |
| `POST /qq/control` `{action}` | `playpause` / `play` / `pause` / `next` / `prev` / `stop` |
| `GET /qq/volume` · `POST {value}` | 系统音量 0-100（Core Audio `IAudioEndpointVolume`，**纯 ctypes**） |
| `GET /qq/lyrics?title=&artist=&tr=` | 搜 QQ音乐 → 取歌词 → 返回 `[{t,s,zh?}]`（`tr=0` 不翻译）；6h 内存 + 7 天磁盘缓存 |
| `GET /qq/search?q=` | 搜曲库，返回 `items[{title,artist,album,mid}]` |
| `POST /qq/app` `{action}` | `start` / `stop` / `status` 启动关闭 QQ音乐客户端 |
| `GET /qq/spectrum` | **真频谱**：**48** 个频段值（0~1），来自 WASAPI 回环采集 + FFT |

机制：QQ音乐会发布 **Windows 媒体会话（SMTC）**。
`tools\smtc-daemon.ps1`（常驻，每 500ms）把状态写进 `_music.json`，`server.py` 读文件；
控制动作由 `tools\smtc-control.ps1` 一次性执行。
前端：**侧栏收起 = 纯听歌模式**（`html[data-sidebar="closed"]`）——侧栏与消息区都隐藏，
歌词**居中浮层**显示（只显示 上一句 / 当前句 / 下一句），**播放条被隐藏**，
改为在**输入框正上方**显示一条**真频谱音频条**（`#vis-bar`，**48 根柱子 ×16px，整条 1000×64px**，
数据同 `/qq/spectrum`），音频条 + 输入框一起贴底；有歌有词才显示歌词，没有时给一行提示免得一片空白。

**`#vis-bar`（收起时的音频条）可调项**

| 想改什么 | 位置 |
| --- | --- |
| 长 / 密 | `style.css` 的 `.vis-bar`：`width: min(100%, 1000px)` 决定整条长度，`max-width`（柱宽）+ `gap` 决定密度 |
| 高 | 同处 `height`（当前 64px） |
| 透明度 / 颜色 | 同处 `opacity`（当前 .5）与柱子的上下渐变 |
| **起伏 / 灵敏度** | `app.js` 的 `VIS_TOP`（88 封顶）、`VIS_GAMMA`（1.7，>1 把小声压更矮）、`VIS_GATE`（0.08，低于它当静音让"谷"落到底） |
| 柱子总数 | `spectrum.py` 的 `BANDS`（48）+ `app.js` 的 `VIS_WEIGHT` + `style.css` 的 `max-width`，三者要配套 |
| 采样频率 | 收起时也持续采；**没在放歌时自动降到约 1.7 次/秒**，避免 70ms 空转打接口 |
| 没放歌时 | 柱子静止成一条矮基线（不是空白，也不是满格）；拿不到真频谱就退回 CSS 合成动画 |

**两条必须知道的坑**

1. **PowerShell 输出不要走管道**：stdout 是管道时 `Write-Output` 会被缓冲
   （实测 8 秒 0 行），写文件则立刻落盘 → 所以采集进程写 `_music.json`，不往管道吐。
2. **`[double]::Round` 不存在**，要用 `[math]::Round`。

另：服务重启不会带走已脱离的采集进程，靠"`_music.json` 是否新鲜（<10s）"避免重复拉起。

**做不到 / 已确认的边界**

- **在页面里直接播放 QQ音乐音频**：不行（DRM + vkey）。
- **让客户端播放指定歌曲**：**`qqmusic://` 协议没注册**（注册表里只有 `.mflac/.qmc0/.tkm` 等文件关联），
  所以页面无法代它开播；只能搜到结果后复制名字手动粘。想要自动输入只能做键盘模拟（脆弱）。
- **关闭 QQ音乐**：给它发 `WM_CLOSE` **它只会缩到托盘**，不会退出 → `close_qqmusic()` 必须在
  3 秒后 `taskkill /F` 兜底。
- **音频条**：优先走**真频谱**（`tools/spectrum.py` 用 WASAPI 回环采系统输出 → FFT → **48** 个对数频段，
  快起慢落平滑 → 写 `_spectrum.json` → `GET /qq/spectrum`）。采集进程跑在**隔离环境** `opencode-ui\.audio-venv`
  （soundcard + numpy）；该环境不存在时会自动退回 CSS 合成动画，所以不会"什么都没有"。
  实测：播 440Hz 时是**尖峰**（`#20=0.91 #19=0.85 #21=0.27 #18=0.14`），播放结束平滑衰减。
  响应形态 `{ok, age, device, bars[48], error}`；`bars` 是 0~1；`device` = 采集器当前盯着的输出设备
  （**蓝牙切走/采错设备时一眼就能看出来**）。
  采集进程常驻约 **22% 单核**（48 段；未播放时也不停 → 是一个可优化点）。
  前端只在「播放条可见且正在播放」时才轮询频谱（70ms 一帧），收起时也采但**没放歌时降到约 1.7 次/秒**。
  **自愈**：每 2s 比对默认输出设备名，变了就重开回环；连续静音 30s 也重开一次（蓝牙耳机省电会把流放死）；
  `record()` 报错则退避重试并把采样率降到 44100/16000（蓝牙常见不支持 48k）。

**`spectrum.py` 的两个关键参数（决定"起伏"）**

| 参数 | 值 | 作用 |
| --- | --- | --- |
| `BANDS` | 48 | 段数。越多越密、相邻段差异越大；改完**必须杀掉采集进程**，`server.py` 的 `spectrum_keeper()` 会在 ~13s 内用新参数重启 |
| 归一化 | `vals[i] = spec[m].max()` | **取频段峰值**（不是均值）。用均值的话段越窄读得越高、容易顶到 1.0 → 满屏"拉满"、毫无起伏 |
| `DB_FLOOR` / `REF` | 50 / `BLOCK/4` | `REF` = 满幅正弦经 Hann 窗的峰值；`-50dB..0dB → 0..1`，留出动态余量 |

## 需要你选择：form / permission 弹窗（已接入）

**这两类交互必须自己弹窗** —— 它们由客户端负责，自建界面不实现的话 agent 会被**静默卡住**，
而本项目还会**最小化官方窗口**，用户根本看不到。前端在 `pollAsks()` / `renderAsk()`。

| 用途 | 接口 |
| --- | --- |
| 待处理提问 | `GET /api/session/{sid}/form` → `{data:[{id,title,fields}]}` |
| 回答问题 | `POST /api/session/{sid}/form/{frm_id}/reply` `{answer:{key:value}}`（204） |
| 待处理权限 | `GET /api/session/{sid}/permission` → `{data:[{id,action,resources,message}]}` |
| 权限回复 | `POST /api/session/{sid}/permission/{per_id}/reply` `{decision:"once"｜"always"｜"reject"}` |

`Form.Field` 公共属性：`key/title/description/required/hidden/when/type`；
`type` ∈ `string｜number｜integer｜boolean｜multiselect｜external`；
**`string` 也可以带 `options[]`**（选择题就是这么来的；选项是 `{value,label,description}`）。
`Form.Answer` 的 key 是字段的 `key`，值是 `string｜number｜boolean｜string[]`。

SSE 有 `form.created` / `form.replied` / `permission.*` 可即时触发弹窗，`pollAsks()` 每 1.2s 兜底轮询。
对**已结算**的表单再回复 → **409 `FormAlreadySettledError`**（这是判断"用户已答过"的可靠信号）。
权限回复的取值是 `once｜always｜reject`，**不是** `allow`/`deny`。

**前端两个关键修复（别再退回去）**：
① 同一条待处理项**不重建 DOM** —— 原来每 1.2s 重写 `innerHTML`，用户刚点的选择会被清掉，
   表现成"能看见弹窗但选不中"；
② 选中态用**独立记账** `ASK.picks`，即使 DOM 重建也不丢。
实测第一个问题成功回收答案。

**已知限制**：只轮询**当前会话**，别的会话里的提问不会弹。

## 踩过的坑（必须遵守）

1. **用户消息的结构**与助手不同（顶层 `text`）——只读 `content` 会让**用户的提问整条消失**。
2. **消息列表是倒序**——不重排就会出现"最新在顶、最旧在底"。
3. **实时性必须走 SSE 增量**，且按事件类型分流；不要无脑重取。
4. **PowerShell 过滤进程时必须限定 `Name`**。只匹配命令行文本的话，**执行这条命令的进程会匹配到自己**（本项目踩了两次：一次自杀、一次让判断永远为真）。更稳的做法是"谁占端口谁就是目标"。
5. **JS 没有运行时**（无 node），所以改完 `app.js` **必须**用
   `python tools\jsbalance.py web\app.js` 检查括号配平，并检查**函数是否重名**
   —— 重名的 `function` 会静默覆盖，最后定义的那个生效（已踩过一次）。
6. **不要把"循环开头的时间戳"用作计时基准**：一轮循环可能花掉上百毫秒到 1 秒，
   会让"反悔 1 秒"瞬间超时（实测只剩 4 毫秒）。要用**事件发生那一刻**的 `time.time()`。
7. **浏览器窗口要真正独立、关窗即退**：`--app=URL --user-data-dir=<专属目录>
   --disable-background-mode`。共用日常 profile 会互相干扰。
8. **强杀浏览器会跳过 `pagehide`**，`/bye` 发不出去 → 只能等心跳超时（8s）。
   正常点 X 是 **约 1 秒**。
9. `.ps1` 文件**不要写中文**（Windows PowerShell 按 ANSI 读无 BOM 文件，会把字符串截断）。`AGENTS.md`/`.md`/`.bat` 里的中文没问题。
10. 控制台是 GBK：脚本里**别 print emoji**（`✅` 会直接抛 `UnicodeEncodeError`）；需要看中文就 `PYTHONIOENCODING=utf-8` 并重定向到文件再读。
11. ### ⚠ 绝对不要用 PowerShell 的 `Get-Content`/`Set-Content` 改写 UTF-8 文件 ###
    PowerShell 5.1 的 `Get-Content -Raw` **不带 `-Encoding` 时按 ANSI(GBK) 解码**，会把中文变成乱码；
    更狠的是 GBK 是**双字节**编码，字节错位还会**吞掉换行**，导致括号全不配平。
    本项目用这个手法一次性毁掉了 `web/app.js` 与 `web/index.html`（809 个字符不可逆丢失，只能整体重写）。
    **要改这些文件，只用编辑工具（write/edit），或 `python -c` 读写；确要用 PowerShell 必须两端都写 `-Encoding UTF8`。**
12. 改前端后**只需改 `web/index.html` 里的 `?v=`**（当前 `260923h`）——版本号现在是自动的：
    `web/app.js` 用 `document.currentScript` 读出自己加载时带的 `?v=`，`server.py` 从 index.html 读同一个值
    并通过 `/healthz` 的 `ui` 字段告诉前端；**两处不一致时页面会自己刷新一次**（见 #13），
    所以不用再手改 `#ui-ver`，它显示的就是实际加载到的版本。
13. **面板窗口只在页面加载时读一次 JS**：改了前端而窗口没刷新，它就一直跑旧代码，
    表现就是"改了没生效/顺序不对"。现在有自动刷新兜底（只对上线该功能之后的版本有效）；
    万一窗口跑的是更老的版本，仍需按一次 Ctrl+Shift+R，**并以侧栏底部 `ui xxxx` 为准**再讨论现象。
    ⚠ 自动刷新期间必须**跳过 `/bye`**，否则守护进程会误判"面板关了"而杀掉 OpenCode。
14. 亲眼看界面（本项目**能**看，别再说"我看不到"）：`python tools\inspect_ui.py`
    用 Edge 无头渲染真实页面并打出 DOM 实际顺序；加 `--shot 路径.png` 还能截图（图像可直接读）。
15. **整体重写文件会"静默丢功能"，而现有校验器查不出来**：重写 `app.js` 时把「消息按 `time.created`
    升序重排」弄丢了 → 用户又看到"对话从下到上"。`jsbalance.py` 只看括号、`check_ids.py` 只看 DOM id，
    **都抓不到这类回归** → 所以有 `tools\check_features.py`：把本文件列过的能力**逐条当断言**（123 项，
    覆盖 `app.js` + `index.html` + `style.css`；`NOT:` 前缀 = 断言"必须不存在"，用来锁住"旧实现已删干净"），
    大改前端后**必跑**。
16. **`no-kill` 对已经在跑的守护进程无效**：`watch.py` 的 `KILL_OPENCODE` 是**进程启动时**算一次的常量，
    事后放文件完全没用。所以要放**探针页**而不是直接开面板页做实验 —— 见 #17。
    （用无头浏览器打开**真面板页**会触发 `pagehide → /bye` → 真的会杀掉 OpenCode 和当前会话。）
17. **Headless Edge 的 `--virtual-time-budget` 会被"永不结束的请求"卡死**：面板页有 SSE 长连接
    + 若干个不停歇的轮询（音乐 1s / 频谱 70ms / 弹窗 1.2s）→ 虚拟时间停在"等待网络"，
    `--dump-dom` 永远不返回（实测 45 秒超时被杀）。
    → `inspect_ui.py` 用**探针页**：由 index.html 生成、结构一致，但把 `/heartbeat`、`/bye`、SSE、
    音乐与频谱轮询**全部拦掉**，只留真正要测的那几条（healthz / 会话列表 / 消息）。
18. **别按"进程数"判断采集器跑了几份**：`.audio-venv\Scripts\python.exe` 是 `python -m venv` 生成的
    **转发壳**（`pyvenv.cfg` 里写着 `home = D:\Python312`），它会拿**同样的命令行**再拉起基础解释器 →
    任务管理器里永远看到**两个** `spectrum.py` 进程。判定真假要看 **CPU 时间**：
    壳 0ms/3s，真解释器约 670ms/3s。**不是两份在抢写 `_spectrum.json`**，别去"修"。（2026-09-23 实测）
19. **⚠ 别用 `waveOutGetVolume/SetVolume` 调系统音量**（踩过：滑块能拖、音量纹丝不动）。
    本机实测 `waveOutGetVolume(NULL)` 返回 **0xFFFFFFFF（不受支持）**，而真实音量是 **15**，
    旧代码却因此永远报 `100`。要真控制音量必须用 Core Audio 的 **`IAudioEndpointVolume`**
    （`SetMasterVolumeLevelScalar` / `GetMasterVolumeLevelScalar`），`server.py` 里是纯 ctypes 实现。
20. **⚠ 蓝牙耳机让"按名字取回环"失效**（踩过：音频条一直贴在基线不跳）。
    蓝牙耳机的**播放端点和麦克风端点同名**（本机都叫 `耳机 (YSX6)`），
    `sc.get_microphone(名字, include_loopback=True)` 会抓到**麦克风**端点 →
    采到一片静音（实测峰值 **0.00003**；按 id 精确挑回环是 **0.29492**）。
    以前用 Realtek 没出事只是运气好（它的回环叫「扬声器 (…)、麦克风叫「麦克风阵列 (…)，名字不同）。
    **正确做法：`all_microphones(include_loopback=True)` 里挑 `isloopback` 且 `id == 默认扬声器.id` 的那个。**
    配套：采集器还必须**跟着默认设备走**（设备一换就重开回环），否则切到蓝牙后它会一直采旧设备的静音，
    而且它**还在不停写文件** → 只看"文件新不新鲜"的 `spectrum_keeper()` 永远不重拉它。
21. **⚠ `data-theme` 是主题开关，不能借来当别的标记**（踩过：壁纸选择器里"夜晚"按钮变黑）。
    给按钮写 `data-theme="yoru"` 本意是"这张指派给夜主题"，但 `style.css` 里
    `[data-theme="yoru"] { --paper: #141110; … }` 会把**夜主题的全部变量套到那个按钮身上** →
    `background: var(--paper)` 直接变黑。**换个属性名**（现在叫 `data-slot`）就没事。
    同理：任何元素都不要随手加 `data-theme`。
22. **⚠ `video.playbackRate` 有下限 0.0625，越界会抛异常并打断整个流程**（踩过：收起侧栏后"完全没反应"）。
    想做"缓慢启动"我把起步速率写成 0.06 → `NotSupportedError: not in the supported playback range`
    → 那一行之后的 `opacity`、`play()` 全都没执行，表现就是**什么都不发生**（而且 `jsbalance` /
    `check_features` 这类静态检查**抓不到**，只有真跑一遍并监听 `window.onerror` 才看得见）。
    → 起步取 **0.1**，并且所有赋值都走带 try/catch 的 `setRate()`。
    **教训：这类"静默中断"只能靠"抓未捕获异常 + 打时间线"定位。**
23. **⚠ `ABM_GETSTATE` 是 `0x4`，不是 `0x2`**（`0x2` 是 `ABM_QUERYPOS`；读任务栏状态要用 `0x4`）。
    踩过：读"任务栏是否自动隐藏"时传了 `0x2`，拿回垃圾值 `0x1`，于是把**关**误判成**开**，
    还照错误结论去"关"了一遍（幸好 `ABM_SETSTATE` 的写入本身是对的，没造成实际后果）。
    另外 `StuckRects3` 的字节是 **explorer 注销时才写**的 → **注册表可能陈旧**，别拿它当实时状态。
    **教训：Win32 常量逐个核对；拿不准就用行为学验证兜底** ——
    把鼠标在屏幕中央 vs 贴底边各停一会儿，看任务栏矩形是否滑出滑入（实测：开→top 1065、关→top 1019）。

24. **⚠ 顶栏按钮是固定 `width: 34px` 的方图标钮**：任何放进 `.actions` 的**更宽**按钮都会被截断
    （「模型」按钮曾被截成 `deep`，因为 `#topbar .actions button { width: 34px }` 赢了）。要放宽就用
    **id 选择器**（`#topbar .actions #btn-model { width: auto }`）。验证办法：无头截图里看按钮文字。
25. **⚠ 探针页注入脚本的两条铁律**（都踩过，第二条很危险）：
    ① 不要在全局声明与 `app.js` **同名**的变量 —— 写入 `var sleep` 撞上 `app.js` 的 `const sleep` →
       **整个 app.js 语法错误、界面全废**（表现是"点了没反应、列表空"）。
    ② 不要用 `str.replace("占位符", ...)` 做注入替换 —— 占位符若同时出现在**属性名**里
       （`window.FAKE_LYRICS = FAKE_LYRICS`）会被一起替换坏 → 注入脚本语法错误 → **拦截失效**：
       真实 `/qq/spectrum` 轮询把 `--virtual-time-budget` 卡死（见 #17），
       而且探针页关窗时可能**真的发 `/bye` 把 OpenCode 杀掉**。改法：直接把 JSON 拼进模板，别事后 replace。

26. **⚠ `<img>` 里的 SVG 不继承页面颜色**：模型品牌标里有 `fill="currentColor"`（models.dev 的 DeepSeek）
    或干脆不写 fill（LobeHub 的 AntGroup）→ 放进 `<img>` 会按**默认黑**渲染，**夜主题下直接看不见**。
    改法：下载后用品牌色**写死**（`tools/fetch_model_icons.py` 的 `BRAND_FILL`：DeepSeek `#4D6BFE`、蚂蚁 `#1677FF`）。
    另外：**OpenCode 的模型/提供方接口都没有图标字段**，但 `models.dev/logos/<providerID>.svg` 可用（它就是其数据源）。

27. **⚠ 无头测"视频画面"别用 `--virtual-time-budget`**：虚拟时间跑得比真实解码快，
    取到的 `drawImage` 结果会是**纯黑**（`mean=0`）、`currentTime` 卡住不动、`.ready` 看着却是 true ——
    很容易误判成"渲染坏了"。**画面类结论要看截图**（真实时间渲染）再用 Pillow 量；
    纯 DOM/状态类结论（class、paused、display）不受影响。排查时先量 `videoWidth/readyState/error`：
    能解码就该是 `videoWidth=3840 / readyState=4`。

## 验证工具

```bat
python tools\jsbalance.py web\app.js     :: JS 括号配平（会正确跳过字符串/模板串/注释/正则字面量）+ 查函数重名
python tools\check_ids.py                :: DOM id 引用是否都存在
python tools\check_features.py           :: ★功能体检：123 项能力逐条断言（app.js + index.html + style.css）
python tools\fetch_model_icons.py        :: 下载模型品牌标到 web/assets/models/（一次性，改了模型清单才需要跑）
python tools\inspect_ui.py               :: ★真的看界面：Edge 无头渲染面板页，打出 DOM 实际顺序
python tools\inspect_ui.py --shot _shots\panel.png   :: 再存一张截图（人/模型都能看）
python -m py_compile server.py watch.py  :: Python 语法
python -c "import json,urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8787/healthz').read())"
                                          :: 面板代理与上游连通性
```

**按对象选手段**

| 对象 | 手段 |
| --- | --- |
| 编码体检 | 读字节：`BOM` 是否为真 + `U+FFFD` 计数 + 中文抽样是否命中 |
| 前端可达 | `GET /`、`/style.css`、`/app.js`、`/assets/*` 全部 200 且长度合理 |
| 上游连通 | `GET /healthz` → `{ok:true, ui, version}`（`ui` = 当前前端版本，供页面自检） |
| 面板存活 | `GET /alive` → `{alive, age_seconds, closed, beats}` |
| **真频谱链路** | 播 440Hz → 轮询 `/qq/spectrum` → 峰值应落在第 **20** 段（48 段对数分频，40~16000Hz），是尖峰不是一片 |
| 接口行为 | 用临时会话真调（新建 → 发消息 → 取消息 → 删除），验证字段与归一化结果 |
| 弹窗回复 | `POST .../form/{fid}/reply` body `{"answer":{key:value}}` → **204**；已结算 → **409** |
| 关窗联动 | 发 `WM_CLOSE`（等价点 X，会触发 `pagehide`→`/bye`）+ 读日志毫秒差 + **全程 `no-kill`** |

> ⚠ 验证关窗联动前**必须先放 `no-kill` 并重启守护进程**，否则会杀掉 OpenCode、
> 连带杀掉正在跑的这个会话（`opencode-cli.exe` 就是会话后端）。

`tools\jsbalance.py` **是自测过的**：故意写坏的文件能抓到（FAIL），正则/字符串/除法不会误报（PASS）。

## 文件地图

```
opencode-ui/
├── AGENTS.md                ← 本文件（自动加载，已含原 PROGRESS 的内容）
├── PROGRESS.md              ← 只留历史沿革 / 详细原始数据 / 路线图
├── README.md                ← 面向使用者的说明
├── server.py                ← 静态托管 + /api 反向代理 + /qq/* + /heartbeat /alive /bye /healthz
├── watch.py                 ← 守护进程：服务保活、开窗、最小化、关窗联动关 OpenCode、任务栏联动
├── taskbar.py               ← 任务栏联动：面板开=自动隐藏、关=常驻可见（ABM_SETSTATE）
├── launch.vbs / start.bat / stop.bat / install-startup.ps1 / uninstall-startup.ps1
├── tools/
│   ├── jsbalance.py         ← JS 括号配平 + 函数重名检测
│   ├── check_ids.py         ← DOM id 引用校验
│   ├── check_features.py    ← ★功能体检（123 项断言：app.js + index.html + style.css）
│   ├── fetch_model_icons.py ← 下载模型官方图标 → `web/assets/models/`（一次性，品牌色已写死）
│   ├── inspect_ui.py        ← ★无头渲染看界面（--shot 出图）
│   ├── spectrum.py          ← WASAPI 回环 + FFT → `_spectrum.json`
│   ├── smtc-daemon.ps1      ← 常驻，每 500ms 采集 SMTC → `_music.json`
│   ├── smtc-control.ps1     ← 一次性播放控制
├── web/
│   ├── index.html  style.css  app.js      ← 前端三件套（原生 JS，无构建）
│   ├── assets/     ← 主题素材（头像/背景/贴纸）＋ `models/` 模型品牌标（本地 SVG）
│   └── refs.html  refs_q.html             ← 参考图候选画廊
├── .audio-venv/             ← 频谱采集的隔离环境（soundcard + numpy，65 MB；删了就退回 CSS 合成动画）
├── browser-profile/         ← 面板窗口的专属浏览器 profile（244 MB；纯缓存可安全清，见下）
├── _music.json              ← SMTC 采集产物（<10s 视为新鲜）
├── _spectrum.json           ← 真频谱数据（bar 值）
├── _wallpapers.json         ← 用户在面板里选的动态壁纸（`{主题: 创意工坊 id}`，可删=恢复默认）
├── _lyrics_zh.json          ← 歌词翻译缓存（按 songmid，7 天；可删=重新翻译）
├── _refs/ _refs_q/          ← 参考图原图与备份（32 MB，用户要求保留）
├── _shots/                  ← `inspect_ui.py --shot` 的截图（可删）
├── _watch.log               ← 守护进程日志（带毫秒）
└── lession/                 ← 用户自己的课程项目（**不要动**，内含 `_codex_archive/`）
```

> **清 `browser-profile` 缓存的正确姿势**（面板窗口开着时也能做，**不会**惊动守护进程）：
> 只删纯缓存目录 —— `component_crx_cache`、`Default/Cache`、`Default/Code Cache`、`Default/GPUCache`、
> `Default/Dawn*Cache`、`Default/Service Worker/{CacheStorage,ScriptCache}`、`GrShaderCache`、`ShaderCache`、`BrowserMetrics*`。
> **`Default/Local Storage` 与 `Default/Preferences` 一律不动**（主题/侧栏状态/左上角名字都存在那里）。
> 被占用的文件会因共享冲突删不掉，跳过即可。⚠ 千万别关窗去清 —— 那会触发守护进程杀掉 OpenCode。

## 规模与体积

| 位置 | 规模 |
| --- | --- |
| `web/app.js` | **2198 行 / 86.6 KB / 100+ 个函数** |
| `web/style.css` | 1238 行 / 53.1 KB |
| `web/index.html` | 10.9 KB |
| `server.py` | 48.6 KB · `watch.py` 19.0 KB · `taskbar.py` 4.1 KB |
| `web/assets/models/` | 4 个品牌标 + `sources.json`（8.6 KB，本地，可删=重新下载） |
| `tools/` | jsbalance 5.5 · check_features 10.9 · fetch_model_icons 3.6 · inspect_ui 8 · check_ids 1.4 · spectrum 8.2 · smtc-daemon 3.7 · smtc-control 2.1 KB |
| `.audio-venv/` | 2319 文件 / 65 MB（删了自动退回 CSS 合成动画） |
| `browser-profile/` | 244 MB（已从 481 MB 清出 **237.7 MB** 纯缓存；剩 168 MB 是 Edge 的 AI 视觉模型 `ProvenanceData/*.ort`，**不是缓存，未动**） |
| `_refs/` + `_refs_q/` | 86 文件 / 32 MB（用户要求保留） |
| `lession/` | 8160 文件 / 658 MB（**不要动**） |

## 可能的下一步（等用户发话）

- 真频谱的 CPU 优化：不播放时把采集降频或暂停（现在常驻约 14% 单核）
- 代码变更 / diff 面板（`/api/vcs/status`、`/api/vcs/diff`；本轮问过用户，被排到频谱之后）
- 弹窗改成**轮询所有会话**（现在只看当前会话）
- 历史消息向上翻页（现在只有最新 80 条，接口支持 `cursor`）
- 工具输出实时流（消费 `session.tool.progress`）
- 顶栏「壁纸开关」（现在 ❀ 只控制落樱）
- 多会话标签页、**智能体**切换（`/api/agent`、`POST /api/session/{id}/agent`；模型切换已做，见上）
- 终端（`/api/pty`）与文件浏览（`/api/fs/list`、`/api/fs/read/*`）
- 那 168 MB 的 Edge AI 视觉模型（删了 Edge 以后会重新下载）

### ⛔ 明确排除（**不要再重复提议**）

| 想法 | 结论 |
| --- | --- |
| 在页面里直接播放 QQ音乐音频 | **不可能**（DRM + vkey） |
| 让 QQ音乐 播放指定歌曲 | `qqmusic://` **协议未注册**，只能键盘模拟（脆弱） |
| 关闭 QQ音乐 | 发 `WM_CLOSE` 只会缩托盘 → 必须 3 秒后 `taskkill /F` 兜底 |
| 用无头浏览器打开**真面板页**做实验 | **危险**：`pagehide → /bye` 会杀掉 OpenCode（`no-kill` 对已运行的守护无效）→ 必须用探针页 |
| 改官方 `app.asar` 注入 | 升级即失效、破坏签名；若用户坚持要做须先明确告知 |

## 与用户工作区的关系

- 用户自己的课程项目 `lession/` **被用户自己移到了本项目目录下**（原在 `Documents\ChatGPT\lession`）。
  核实过：文件完整（`.venv`、`CourseData`、`AGENTS.md`、早先备份的 `_codex_archive` 都在），
  但 `.git` 缺失。**这是用户的操作，不要擅自移动或修改它。**
- `_codex_archive/`（在 `lession/` 内）是早先帮用户备份的 3 份 Codex 会话记录 + 摘要。
- 用户此前让我彻底卸载了 Codex，代理由 `~/.cc-switch` 管理（该软件未动）。

## 工作规范

- 改前端后必跑四件套：`tools\jsbalance.py` → `tools\check_ids.py` → `tools\check_features.py`
  → `tools\inspect_ui.py --shot`；确认页面能通过 HTTP 取到；然后让用户 **Ctrl+Shift+R**。
- **改 `web/app.js` / `web/index.html` / 任何 UTF-8 文件，只用编辑工具（write/edit）或 `python -c` 读写**，
  绝不用 PowerShell `Get-Content`/`Set-Content` 管道；改完做编码体检（BOM + `U+FFFD` 计数 + 中文抽样）。
- 不要在用户系统 Python 里装包；要装就建隔离 venv（临时目录里，如 `.audio-venv` 就是这么来的）。
- 破坏性 / 影响运行态的操作（关窗、改自启、动 `lession/`）**先确认**；关窗类实验要先放 `no-kill`
  **并重启守护进程**（文件对已在跑的无效）。
- 视觉类改动**能自证**：先用 `tools\inspect_ui.py`（可加 `--shot` 截图）自己看，再让用户确认。
  能用程序验证的部分（括号、id、功能清单、HTTP 200、DOM 实际顺序、素材存在、接口返回）要自动化验证并贴证据。
- 新写的 Python 脚本**不要 `print` emoji**，一律用 `[OK]` / `[BAD]` / `PASS` 这类纯 ASCII。
- 用户偏好：**先查证再动手**（实测优先于猜测）、**说清限制**、**给可调参数**。
