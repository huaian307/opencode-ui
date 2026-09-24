# REPRODUCE.md — 从零复现 opencode-ui

> 目标：在 Windows 10/11 上把本项目跑起来，并知道每项功能的依赖、验证方法与边界。
> 本仓库是**脱敏后的公开版本**：不包含本机运行数据、开发笔记和同人图素材。
>
> 下文用 `%PROJECT%` 表示项目目录（例如 `D:\opencode-ui`，路径不要有中文和空格最省事）。

## 0. 它是什么

一个**自建的 OpenCode 前端**（不修改官方桌面版）：界面完全自控，配套守护进程实现
「OpenCode 启动 → 拉起面板窗口」「关闭面板窗口 → 关闭 OpenCode」的联动。

```text
Edge --app（独立 profile）
          │ http://127.0.0.1:8787
          ▼
backend/server.py（Python 标准库）
  ├─ frontend/ 静态前端
  ├─ /api/* → OpenCode 后端（自动 Basic 鉴权、SSE 流式透传）
  ├─ /qq/* → QQ 音乐、Core Audio 音量、频谱、歌词
  ├─ /music/* → runtime/venvs/music 中的本地音乐服务
  └─ /live/* → Wallpaper Engine 视频原片

backend/watch.py
  ├─ 保活面板服务
  ├─ 拉起/识别独立面板窗口
  ├─ 联动 OpenCode 开关
  └─ 联动任务栏显示状态
```

## 1. 环境要求

| 项目 | 要求 |
| --- | --- |
| 系统 | Windows 10/11 |
| Python | 3.12；核心代码仅标准库；`python` / `pythonw` 需在 PATH 上 |
| 浏览器 | Microsoft Edge 或 Chrome |
| OpenCode | 官方桌面版至少启动过一次 |
| Node/npm | 不需要 |
| 可选 | Wallpaper Engine、QQ音乐、网易云/QQ 音乐 Cookie |

OpenCode 启动后必须存在：

```text
%USERPROFILE%\.local\state\opencode\service.json
```

其中 `url` 的端口每次重启都可能变化，所以所有访问都必须实时读取该文件，不能写死端口。

## 2. 从零跑起来

```bat
:: 1) 取得项目（目录位置随意）
git clone https://github.com/huaian307/opencode-ui.git
cd /d %PROJECT%

:: 2) 确认 OpenCode 桌面版跑起来过一次（生成 service.json）

:: 3) 启动面板：确保 8787 服务在跑，并打开独立应用窗口
scripts\start.bat
```

验证：

```text
http://127.0.0.1:8787/healthz
http://127.0.0.1:8787/alive
```

推荐用法：建桌面快捷方式 **「OpenCode（带面板）」**，目标指向：

```text
pythonw.exe "%PROJECT%\launchers\launch_opencode.py"
```

双击它就会先启动 OpenCode，再自动带出面板；**不改动**原有的 OpenCode 图标。

默认不装开机自启。需要时：

```powershell
powershell -ExecutionPolicy Bypass -File "%PROJECT%\scripts\install-startup.ps1"
```

停止面板，但**不影响 OpenCode 本体**：

```bat
"%PROJECT%\scripts\stop.bat"
```

## 3. 目录结构

```text
%PROJECT%/
├── backend/       server.py / watch.py / taskbar.py
├── frontend/      index.html / style.css / app.js / assets/
├── launchers/     launch_opencode.py / launch.vbs
├── scripts/       start.bat / stop.bat / install/uninstall-startup.ps1
├── tools/         验证、频谱、SMTC、音乐服务
├── docs/          REPRODUCE.md（开发笔记 PROGRESS.md 不随仓库发布）
├── resources/     references/ + screenshots/（本机素材，不随仓库发布）
└── runtime/       browser-profile/ + venvs/ + state/ + logs/（运行时生成，不随仓库发布）
```

说明：

- 代码目录不保存本机运行产物。Cookie、频谱、歌词缓存、壁纸选择与日志都在 `runtime/`。
- `runtime/`、`resources/references/`、`resources/screenshots/`、`AGENTS.md`、
  `docs/PROGRESS.md`、同人图素材和 `frontend/refs*.html` 都已列入 `.gitignore`，不会进入仓库。

## 4. 可选环境

### 4.1 真频谱

目录：`runtime/venvs/audio`

```bat
cd /d %PROJECT%
python -m venv runtime\venvs\audio
runtime\venvs\audio\Scripts\python.exe -m pip install soundcard numpy
```

验证：播放一段 **440Hz** 正弦波，轮询 `GET /qq/spectrum`。48 段对数频谱的峰值应集中在
第 **20** 段附近，呈**尖峰**而不是一片拉满。

### 4.2 面板内搜歌服务

目录：`runtime/venvs/music`

```bat
cd /d %PROJECT%
python -m venv runtime\venvs\music
runtime\venvs\music\Scripts\python.exe -m pip install NeteaseCloudMusic py-mini-racer "setuptools<81" diskcache requests
```

服务由 `backend/server.py` 保活，监听 `127.0.0.1:8790`，面板通过 `/music/*` 访问。

⚠ 旧版 `py-mini-racer` 的 V8 会把 `encodeURIComponent` 按 UTF-16 编码，导致中文搜索乱码。
修复已经写进 `tools/music_service.py` 的 `SHIM`，不要删除。

## 5. 素材说明

界面同人图仅供本地自用，**不随仓库发布**。缺少 `frontend/assets/*.jpg|png|ico` 时页面仍可运行，
只是没有头像、背景和贴纸。想完整复现观感，请自备图片并按下表命名：

| 文件 | 用途 | 建议尺寸 |
| --- | --- | --- |
| `avatar.png` | 助手消息圆形头像 | 正方形，≥256×256 |
| `hiru-bg.jpg` | 白昼背景 | 竖图更好，如 896×1152 |
| `night.jpg` | 夜主题背景 | 如 1000×563 |
| `hero.jpg` | 空会话主插画 | 1600×1600 |
| `badge.jpg` | 左上角头像 | ≥128×128 |
| `sticker-duck.jpg` / `sticker-couple.jpg` | 空状态贴纸 | 任意 |
| `opencode-ui.ico` | 快捷方式图标 | 多尺寸最佳 |

模型品牌标位于 `frontend/assets/models/`，属于仓库内容。需要重新下载时：

```bat
python tools\fetch_model_icons.py
```

## 6. 自检

```bat
cd /d %PROJECT%
python tools\jsbalance.py frontend\app.js
python tools\check_ids.py
python tools\check_features.py
python tools\inspect_ui.py --shot resources\screenshots\panel.png
python -m py_compile backend\server.py backend\watch.py backend\taskbar.py
```

HTTP 检查：

```bat
python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8787/healthz').read())"
python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8787/alive').read())"
```

`inspect_ui.py` 使用安全探针页，不会向真实页面发送 `/bye`，因此不会误触发「关面板连带关 OpenCode」。

## 7. 关键边界与坑

1. 助手消息正文在 `content[]`，用户正文在顶层 `text`。
2. 消息接口返回「新 → 旧」，渲染前必须按 `time.created` 升序重排。
3. 生成中内容只能靠 SSE 增量，不能每个事件都重取消息。
4. `service.json` 的 URL 端口会变，不能写死。
5. 系统音量必须用 Core Audio；不要改回 `waveOutGetVolume`。
6. 浏览器必须使用 `runtime/browser-profile` 独立 profile，否则会干扰日常浏览器。
7. `runtime/no-kill` 只在守护启动时读取；对已运行守护事后创建无效。
8. 不要用无头浏览器直接打开真实面板页；用 `tools/inspect_ui.py` 的安全探针页。
9. `.ps1` / `.vbs` 必须保持纯 ASCII，避免 Windows ANSI 解码破坏脚本。
10. 不要使用系统 Python 安装第三方包；依赖只放 `runtime/venvs/`。
11. 公开仓库只保留代码与文档，不要把本机运行数据、开发笔记和同人图带进提交。

## 8. 联动测试安全规则

关闭面板会关闭 OpenCode，也会中断当前会话。测试前必须：

1. 创建 `runtime/no-kill`；
2. **重启守护进程**（保险只在启动时读取）；
3. 再做关窗实验；
4. 测完删除 `runtime/no-kill` 并恢复正常启动。

仅停面板、不关 OpenCode，可直接运行 `scripts/stop.bat`。

## 9. 提交前请核对

```bat
git status --short
git check-ignore -v AGENTS.md docs/PROGRESS.md runtime/state/_music_cookie.json resources/references/primary
```

以上只应看到「被忽略」的结果，不应出现在 `git status` 的待提交列表中。
