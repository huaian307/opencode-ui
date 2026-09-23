# REPRODUCE.md — 从零复现 opencode-ui

> 这份文档的目标：**换一台 Windows 机器，照着做就能把本项目跑起来**，并且知道每个功能依赖什么、怎么验证。
> 阅读顺序建议：先本文件 → 再看 [`README.md`](README.md)（日常使用）。
> （开发笔记 `AGENTS.md` / `PROGRESS.md` 含本机路径与进程号等，**不随仓库发布**，只保留在本地。）
> 本文件最后更新：2026-09-23（前端 `ui 260923h`）

---

## 0. 它到底是什么

一个**自建的 OpenCode 前端**（不修改官方桌面版）：

```
┌─────────────────────────────┐
│ Edge --app 窗口（独立 profile）│   ← 界面完全自控（原生 JS，无构建步骤）
└──────────────┬──────────────┘
               │ http://127.0.0.1:8787
┌──────────────▼──────────────┐
│ server.py（Python 标准库）    │   ← 静态托管 + /api/* 反向代理（自动加 Basic 鉴权、流式透传 SSE）
│                              │      + /qq/*（QQ音乐、音量、频谱、歌词翻译）+ /live/*（壁纸）
└──────────────┬──────────────┘
               │ 读 ~/.local/state/opencode/service.json 拿 {url, password}
┌──────────────▼──────────────┐
│ OpenCode 后端（官方桌面版拉起） │
└─────────────────────────────┘

watch.py（守护进程，占 8788 单实例锁）
   ├─ 保证 8787 活着（挂了自动拉起）
   ├─ OpenCode 启动 → 等后端就绪 → 开面板窗口 → 1.5s 后最小化 OpenCode 窗口
   ├─ 关面板窗口 → 反悔 1s → 关掉 OpenCode
   ├─ 面板开/关 → 任务栏自动隐藏 / 恢复常驻
   └─ 靠页面心跳（/heartbeat、/bye）判断"面板还开着吗"
```

---

## 1. 环境要求

| 项 | 要求 | 说明 |
| --- | --- | --- |
| 操作系统 | Windows 10/11 | 大量用到 Win32 / Core Audio / UI Automation / PowerShell |
| Python | **3.12，仅标准库** | 本项目**零第三方依赖**；`python`（控制台）与 `pythonw`（无窗口）需要在 **PATH** 上 |
| Node / npm | **不需要** | 前端是原生 JS，**没有构建步骤** |
| 浏览器 | Microsoft Edge（或 Chrome） | 用 `--app=` 独立窗口；代码里按顺序找 Edge/Chrome 的常见安装路径 |
| OpenCode | **官方桌面版**（已在运行过至少一次） | 需要它生成 `%USERPROFILE%\.local\state\opencode\service.json` |
| 可选：Wallpaper Engine | 想用"动态壁纸"才需要 | 直接播创意工坊里的 mp4 原片，**不复制、不转码** |
| 可选：QQ音乐 | 想用播放控制/歌词/频谱才需要 | 通过 Windows **SMTC** 拿播放状态 |
| 可选：`.audio-venv` | 想用**真频谱**才需要 | 隔离环境（`soundcard` + `numpy`）；没有就自动退回 CSS 合成动画 |

> ⚠ **Python 依赖的边界**：`server.py` / `watch.py` / `taskbar.py` 全部只用标准库。
> 唯一需要第三方包的是**真频谱采集器** `tools/spectrum.py`（WASAPI 回环 + numpy），
> 它跑在独立 venv 里（本项目把它放在 `.audio-venv/`，**不装进系统 Python**）。

---

## 2. 从零跑起来（最小可用）

```bat
:: 1) 确认 OpenCode 桌面版已经跑起来过一次（这样才有 service.json）
::    检查是否存在： %USERPROFILE%\.local\state\opencode\service.json

:: 2) 放好项目目录（路径随意，注意不要有中文/空格更省事）
::    例如 C:\Users\<你>\Documents\opencode-ui

:: 3) 启动面板（会确保 8787 服务在跑，并用独立窗口打开界面）
start.bat

:: 4) 验证服务与上游连通
::    浏览器打开 http://127.0.0.1:8787/healthz → 应返回 {"ok":true,"ui":"...","version":"..."}
::    打开 http://127.0.0.1:8787/alive  → 应返回 {"alive":true,...}
```

装开机自启 / 卸载：

```powershell
powershell -ExecutionPolicy Bypass -File install-startup.ps1     # 装（启动文件夹 → launch.vbs → pythonw watch.py）
powershell -ExecutionPolicy Bypass -File uninstall-startup.ps1   # 卸
```

停止（**OpenCode 本体毫发无损**）：

```bat
stop.bat
```

**手工重启**（按端口认人，别按进程名猜）：

```powershell
foreach ($p in 8788,8787) {
  $q = Get-NetTCPConnection -LocalPort $p -State Listen -EA SilentlyContinue |
       Select-Object -First 1 -ExpandProperty OwningProcess
  if ($q) { Stop-Process -Id $q -Force }
}
Start-Process pythonw -ArgumentList "$((Get-Location).Path)\watch.py" -WindowStyle Hidden
```

---

## 3. 各项功能：依赖与复现方式

| 功能 | 依赖 | 怎么复现 / 验证 |
| --- | --- | --- |
| 会话列表 / 消息流式 / 附件 | 只要 OpenCode 在跑 | 发一句话即可；工具调用、思考流、打字机都应出现 |
| form / 权限弹窗 | 无 | 让 agent 触发一次提问/权限（例如让它询问你的选择）→ 面板会弹窗 |
| 昼夜主题 | 无（素材在 `web/assets/`） | 顶栏「夜/昼」按钮；状态存 `localStorage` |
| 动态壁纸（WE 原片） | Wallpaper Engine + 已订阅视频类壁纸 | 顶栏「壁」→ 选一张指派给昼/夜；`_wallpapers.json` 落盘。想在别的机器复现**必须自己订阅/放入**对应壁纸 |
| 界面设置 | 无 | 顶栏「设」：背景亮度（昼/夜）、毛玻璃、壁纸速度、动态壁纸开关、落樱、歌词翻译 |
| **模型切换** | OpenCode 提供 `/api/model` | 顶栏「模型」→ 选一个（带 variants 的可直接点变体）→ 会话的 `model` 立即改变 |
| **模型官方图标** | 一次联网下载（之后全本地） | `python tools\fetch_model_icons.py` → 图标落到 `web/assets/models/`；没有品牌标的模型自动用**字母徽章** |
| **歌词中文翻译** | 联网（有道 demo 接口） | 播一首外文歌 → 收起侧栏 → 原文下出现小字译文；缓存写在 `_lyrics_zh.json`（7 天） |
| **任务栏联动** | 无（Win32 `SHAppBarMessage`） | 开面板 → 任务栏自动隐藏（鼠标贴底边浮现）；关面板 → 恢复常驻。状态用标记文件 `_taskbar.hidden` 记账 |
| QQ音乐控制 / 歌词 / 音量 | QQ音乐客户端 + PowerShell | 启动 QQ音乐放歌 → 播放条出现；音量走 **Core Audio**（不是 winmm） |
| **真频谱** | `.audio-venv`（soundcard + numpy） | 见下方"重建 .audio-venv" |
| 动态壁纸"暂停当背景" | 无 | 收起侧栏=在播；展开=**暂停在同一帧**当静态背景（不截图） |

### 重建 `.audio-venv`（真频谱用，可选）

```bat
:: 用一个独立环境，别污染系统 Python
python -m venv .audio-venv
.audio-venv\Scripts\python.exe -m pip install soundcard numpy
:: 然后让 server.py 重新拉起采集器（杀掉落单的 spectrum.py 进程即可，keeper 会重拉）
```

验证真频谱：播放一段 **440Hz** 正弦 → `GET /qq/spectrum` 的峰值应落在**第 20 段**
（48 段对数分频，40–16000 Hz），呈**尖峰**而不是一片拉满。

---

## 4. 素材说明（**仓库里不含同人图**）

界面里用到的绘梨衣同人图（头像、背景、贴纸）**属于同人作品，仅供本地自用**，
因此**没有放进仓库**。复现时你会看到：

- 缺少 `web/assets/*.jpg|png` 时页面仍可运行（只是没有头像/背景图）
- 想完整复现观感，**请自备图片**，按下表命名放入 `web/assets/`：

| 文件 | 用途 | 建议尺寸 |
| --- | --- | --- |
| `avatar.png` | 助手消息圆形头像 | 正方形，≥256×256 |
| `hiru-bg.jpg` | 白昼背景 | 竖图更好，如 896×1152 |
| `night.jpg` | 夜主题背景 | 如 1000×563 |
| `hero.jpg` | 空会话主插画 | 1600×1600 |
| `badge.jpg` | 左上角头像 | ≥128×128 |
| `sticker-duck.jpg` / `sticker-couple.jpg` | 空状态贴纸 | 任意 |

`web/refs.html`、`web/refs_q.html` 是当时的**参考图候选画廊**，如果没放图片可忽略。

---

## 5. 复现后自检（本项目自带的验证工具）

```bat
python tools\jsbalance.py web\app.js     :: JS 括号配平 + 函数重名
python tools\check_ids.py                :: DOM id 引用是否都存在
python tools\check_features.py           :: ★功能体检：123 项能力逐条断言（app.js + index.html + style.css）
python tools\inspect_ui.py               :: ★无头渲染真实页面，打印 DOM 实际顺序
python tools\inspect_ui.py --shot _shots\panel.png   :: 再存一张截图（人/模型都能看）
python -m py_compile server.py watch.py taskbar.py   :: Python 语法
```

接口连通性：

```bat
python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8787/healthz').read())"
:: → {"ok": true, "upstream": "...", "ui": "<前端版本>", "version": "<OpenCode 版本>"}
```

面板存活与关窗联动（**想测关窗必须先放 `no-kill` 并重启守护进程**，否则会连 OpenCode 一起关掉）：

```bat
echo. > no-kill          :: 关窗不再关 OpenCode
:: …按上面"手工重启"重启守护进程，再关窗口，观察 _watch.log 里的毫秒差…
del no-kill              :: 测完删掉
```

---

## 6. 平台/环境硬约束（复现时最容易卡住的点）

1. **没有 Node**：前端必须保持"原生 JS + 无构建"；不要引入打包器。
2. **系统 Python 只有标准库**：要装第三方包就**另建 venv**（`.audio-venv` 就是这么来的）。
3. **`service.json` 里的端口每次重启 OpenCode 都可能变**：所有后端访问都必须**每次读文件**。
4. **鉴权是 HTTP Basic**：用户名固定 `opencode`，密码在 `service.json`；`server.py` 自动附加。
5. **消息接口只返回"已完成"的消息**，实时内容只能靠 SSE 增量事件（`session.text.delta` 等）。
6. **消息接口返回顺序是「新 → 旧」**，渲染前必须按 `time.created` 升序重排。
7. **浏览器窗口必须真正独立**：`--app=<url> --user-data-dir=<专属目录> --disable-background-mode`。
8. **强杀浏览器会跳过 `pagehide`**：`/bye` 发不出去，只能等心跳超时（约 8s）；正常点 X 约 1s。
9. **`no-kill` 只在守护进程启动时读一次**：对已在运行的守护进程放文件无效。
10. **任务栏自动隐藏用的是 `ABM_SETSTATE`**：`ABM_GETSTATE = 0x4`（**不是 `0x2`**）；注册表 `StuckRects3` 是注销时才写的，别当实时状态。
11. **系统音量别用 winmm**（`waveOutGetVolume` 在多数设备返回 `0xFFFFFFFF`）→ 用 Core Audio `IAudioEndpointVolume`。
12. **无头浏览器别打开"真面板页"**做实验：`pagehide → /bye` 会**真的**把 OpenCode 关掉（当前会话也会被杀）。用探针页。
13. **无头 `--virtual-time-budget` 不适合测视频画面**：虚拟时间跑得比解码快，`drawImage` 会全黑 → 画面类结论要看截图 + Pillow。

---

## 7. 目录结构（仓库里有什么 / 没有什么）

```
opencode-ui/
├── server.py / watch.py / taskbar.py   ← 后端与守护进程（纯标准库）
├── start.bat / stop.bat / launch.vbs   ← 启动 / 停止 / 无窗口拉起
├── install-startup.ps1 / uninstall-startup.ps1
├── web/                                 ← 前端（index.html / style.css / app.js）
│   └── assets/models/                   ← 模型品牌标（可用 tools/fetch_model_icons.py 重新下载）
├── tools/                               ← 验证与采集工具（见 §5）
├── README.md / REPRODUCE.md / .gitignore
└── 运行时生成（不入库）：_wallpapers.json · _lyrics_zh.json · _music.json
    · _spectrum.json · _taskbar.hidden · _watch.log

＜以下内容不在仓库里＞
├── AGENTS.md / PROGRESS.md   ← 开发笔记（含本机路径、进程号、开发日志）
├── web/refs*.html            ← 当时的参考图筛选页
├── web/assets/*.jpg|png      ← 界面用同人图（版权原因，需自备；见 §4）
└── 与本项目无关的个人目录 · 浏览器 profile · .audio-venv · 诊断截图 · 参考图原图
```

---

## 8. 常见问题

| 现象 | 原因 / 处理 |
| --- | --- |
| 页面显示「正在连接 OpenCode…」不消失 | OpenCode 没在跑，或 `service.json` 不可读 → 先启动官方桌面版 |
| 8787 起不来 / 报端口占用 | 用 §2 的"按端口认人"命令找出占用者 |
| 改了前端没生效 | 面板只在页面加载时读一次 JS → 按 **Ctrl+Shift+R**；侧栏底部 `ui xxxx` 是实际加载到的版本 |
| 面板一关，OpenCode 也跟着关了 | 这是设计行为；想改：放 `no-kill` **并重启守护进程** |
| 音频条不跳 | 优先看 `/qq/spectrum` 的 `device` 字段是否是你当前的输出设备（蓝牙切换要能自己跟上） |
| 好几个人同时改前端，功能"凭空消失" | 改完**必须**跑 §5 的 `check_features.py`（它把能力逐条当断言） |

---

## 9. 许可与素材

- 本项目代码：可自行取用/修改（若你要公开发布，建议补一个 LICENSE，例如 MIT）。
- **同人图素材不在仓库内**，请勿把我提供的界面截图里的角色图当作可自由再分发的素材。
- 第三方品牌标（`web/assets/models/*.svg`）来自 models.dev / Simple Icons / LobeHub，商标归各自所有者。
