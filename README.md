# opencode-ui

一个**自建的 OpenCode 界面**：不修改官方桌面版，独立运行，外观完全自控。

- 项目目录可放在任意位置，下文用 `%PROJECT%` 表示（例如 `D:\opencode-ui`）
- 配套守护进程实现「启动 OpenCode → 拉起面板」「关闭面板 → 关闭 OpenCode」
- 前端是原生 HTML/CSS/JS，无 Node、无 npm、无构建步骤

> 从零复现见 [`docs/REPRODUCE.md`](docs/REPRODUCE.md)。
> 开发笔记 `AGENTS.md` 与 `docs/PROGRESS.md` 属于本机文件，**不随仓库发布**。

## 目录结构

```text
D:\opencode-ui\
├── backend\                 后端与守护代码
│   ├── server.py            静态服务 + OpenCode API/SSE 代理 + 音乐/壁纸接口
│   ├── watch.py             面板服务保活、窗口与 OpenCode 联动
│   └── taskbar.py           任务栏自动隐藏/恢复
├── frontend\                原生前端
│   ├── index.html
│   ├── style.css
│   ├── app.js
│   └── assets\              界面素材与本地模型品牌标
├── launchers\               启动入口
│   ├── launch_opencode.py   桌面「OpenCode（带面板）」快捷方式使用
│   └── launch.vbs           纯 ASCII 备用入口
├── scripts\                 启停与自启脚本
│   ├── start.bat
│   ├── stop.bat
│   ├── install-startup.ps1
│   └── uninstall-startup.ps1
├── tools\                   验证、频谱、SMTC 与音乐服务工具
├── docs\                    复现说明与历史档案
├── resources\
│   ├── references\          参考图与筛选素材
│   └── screenshots\         inspect_ui 截图
├── runtime\                 本机运行数据，不进 Git
│   ├── browser-profile\     面板专属 Edge profile
│   ├── venvs\audio\         真频谱隔离环境
│   ├── venvs\music\         网易云/QQ 音乐服务隔离环境
│   ├── state\               Cookie、频谱、歌词、壁纸选择等
│   └── logs\watch.log       守护进程日志
├── .git\                    Git 仓库
├── AGENTS.md                开发规范
└── README.md
```

## 运行

### 推荐方式

桌面建立快捷方式 **「OpenCode（带面板）」**，目标指向：

```text
D:\Python312\pythonw.exe D:\opencode-ui\launchers\launch_opencode.py
```

双击后会先启动 OpenCode，再由 `backend/watch.py` 拉起本地面板。

### 手动启动

```bat
D:\opencode-ui\scripts\start.bat
```

浏览器也可访问 <http://127.0.0.1:8787>，但推荐脚本启动的独立应用窗口。

停止面板、面板服务与守护进程，但不关闭 OpenCode 本体：

```bat
D:\opencode-ui\scripts\stop.bat
```

本项目默认不安装开机自启；需要时可运行：

```powershell
powershell -ExecutionPolicy Bypass -File D:\opencode-ui\scripts\install-startup.ps1
```

## 架构

```text
面板窗口（Edge --app，独立 profile）
          │ http://127.0.0.1:8787
          ▼
backend/server.py
  ├─ frontend/ 静态文件
  ├─ /api/* 自动 Basic 鉴权并流式代理到 OpenCode
  ├─ /qq/* QQ 音乐状态、歌词、系统音量、真频谱
  ├─ /music/* 代理本地音乐服务（网易云 / QQ）
  └─ /live/* Wallpaper Engine 视频原片

backend/watch.py
  ├─ 保活 8787 面板服务
  ├─ OpenCode 启动后打开独立面板窗口
  ├─ 面板关闭后延迟 1 秒关闭 OpenCode
  └─ 面板开/关时同步任务栏自动隐藏状态
```

## 关键数据位置

运行数据统一放在 `runtime/`：

| 数据 | 路径 |
| --- | --- |
| QQ/SMTC 状态 | `runtime/state/_music.json` |
| 真频谱 | `runtime/state/_spectrum.json` |
| 歌词翻译缓存 | `runtime/state/_lyrics_zh.json` |
| 音乐平台 Cookie | `runtime/state/_music_cookie.json` |
| 壁纸选择 | `runtime/state/_wallpapers.json` |
| 守护日志 | `runtime/logs/watch.log` |
| 浏览器状态 | `runtime/browser-profile/` |
| 临时关闭联动 | `runtime/no-kill` |

`runtime/no-kill` **只在守护进程启动时读取一次**。要让保险生效，必须先创建文件，再重启守护进程。

## 验证

```bat
cd /d D:\opencode-ui
python tools\jsbalance.py frontend\app.js
python tools\check_ids.py
python tools\check_features.py
python tools\inspect_ui.py --shot resources\screenshots\panel.png
python -m py_compile backend\server.py backend\watch.py backend\taskbar.py
```

`inspect_ui.py` 使用安全探针页，不会向真实页面发送 `/bye`，因此不会误触发“关面板连带关 OpenCode”。

## 环境约束

- Windows 10/11
- Python 3.12；`server.py`、`watch.py`、`taskbar.py` 仅用标准库
- 不需要 Node / npm
- OpenCode 必须至少启动过一次，以生成 `%USERPROFILE%\.local\state\opencode\service.json`
- 真频谱和面板内音乐服务分别使用 `runtime/venvs/` 下的隔离环境，不污染系统 Python

## 已实现功能

会话与流式消息、附件、思考/工具调用、昼夜主题、动态壁纸、模型与思考强度切换、form/权限弹窗、QQ 音乐联动、歌词翻译、真频谱、面板内网易云/QQ 搜歌与播放、任务栏联动（面板最小化会自动恢复，设置里可关闭），以及面板关闭后自动关闭 OpenCode。

## 边界

- 面板内音乐接口均为非官方接口，可能随平台调整失效；会员/付费歌曲受账号权限限制。
- 只支持 Wallpaper Engine 的 **video** 类壁纸；scene/application 已移除。
- form/权限弹窗当前只轮询当前会话。
- 历史消息目前读取最近 80 条，尚未实现向上翻页。
