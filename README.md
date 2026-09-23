# opencode-ui

一个**自建的 OpenCode 界面**：不修改官方桌面版，独立运行，外观完全自控。

> 📄 **接手/新对话请看**：
> [`AGENTS.md`](AGENTS.md) —— 项目约定、环境约束、后端 API 事实、踩坑清单（**会被 OpenCode 自动加载**）
> [`PROGRESS.md`](PROGRESS.md) —— 详细进度、功能清单、历史与路线图
> [`REPRODUCE.md`](REPRODUCE.md) —— **从零复现**：环境要求、跑起来的步骤、各功能依赖、自检清单

## 目录

```
opencode-ui/
├── server.py               零依赖本地代理 + 静态服务器（Python 标准库）
├── watch.py                守护进程：确保服务 + 联动 OpenCode 开关面板窗口
├── launch.vbs              无窗口拉起守护进程（开机自启入口）
├── start.bat               手动启动
├── stop.bat                停止面板（窗口 + 服务 + 守护进程，不动 OpenCode）
├── install-startup.ps1     安装开机自启
├── uninstall-startup.ps1   取消开机自启
├── browser-profile/        面板窗口专属浏览器 profile（244 MB）
│                           · 纯缓存可安全清空（主题/侧栏状态/名字存在 Local Storage，不受影响）
│                           · ⚠ 别用"关掉窗口"的方式清 —— 关窗会触发联动、把 OpenCode 一起关掉
├── _watch.log              守护进程日志
└── web/
    ├── index.html          页面骨架
    ├── style.css           全部主题变量集中在 :root
    ├── app.js              前端逻辑（原生 JS，无构建步骤）
    ├── assets/             同人图素材（仅本地自用）
    ├── refs.html           第一批参考图候选
    └── refs_q.html         Q 版参考图候选
```

## 运行

```bat
双击 start.bat          :: 确保服务在跑 + 打开应用窗口
```

浏览器访问 <http://127.0.0.1:8787> 也可以，但推荐用 `start.bat`
（独立窗口、无地址栏）。

要求 OpenCode 后台服务处于运行状态（状态文件 `~/.local/state/opencode/service.json`）。

## 为什么需要一个本地代理

浏览器不能直接调用 OpenCode 后台服务：① 它要求 HTTP Basic 鉴权；② 跨源会被拦。
`server.py` 一次性解决：托管静态前端、给 `/api/*` 自动附加鉴权、**流式透传**响应
（保证 `/api/event` 的 SSE 实时性）。

## 联动行为（watch.py）

```
开机 → launch.vbs → pythonw watch.py（无窗口常驻）
                       ├─ 确保 8787 面板服务活着（挂了自动拉起）
                       ├─ 检测 OpenCode 启动 → 等后端就绪 → 用【专属 profile】打开应用窗口
                       ├─ 开完面板窗口 → 把 OpenCode 自己的窗口最小化
                       ├─ 关掉应用窗口 → 1 秒后关闭 OpenCode
                       └─ 8788 端口做单实例锁，避免开两个守护进程
```

**独立浏览器 profile**：窗口用 `--user-data-dir=browser-profile` 打开，是一个
**完全独立的浏览器实例** —— 关掉它不影响你日常的浏览器，反过来也一样，
所以不需要"每次先关浏览器"。这个目录会长大（Edge 自己的缓存与组件数据，实测到过 481 MB）。
**清缓存请只删纯缓存目录**（`component_crx_cache`、`Default/Cache`、`Code Cache`、`GPUCache`、
`Service Worker/CacheStorage` 等），别删 `Default/Local Storage` 和 `Default/Preferences`
—— 主题、侧栏状态、左上角名字都存在那里。⚠ **不要用"关掉窗口"的方式清缓存**（关窗会连带关掉 OpenCode）。

**关窗口 = 关 OpenCode**：关掉应用窗口后，会一并结束 `OpenCode.exe` 与
`opencode-cli.exe`。⚠️ **这意味着正在进行的任务和会话会立即中断。**

想关掉这个联动：在本目录放一个名为 `no-kill` 的空文件，或在调用时加 `--no-kill`：

```bat
echo. > no-kill          :: 从此关窗口不再关 OpenCode
del no-kill              :: 恢复联动
```

> ⚠ 注意：`no-kill` / `--no-kill` **只在守护进程启动时读一次**。
> 如果守护进程已经在跑，事后放这个文件**不会生效** —— 得先重启守护进程（见 `stop.bat` / `start.bat`）。

只想停面板、不关 OpenCode：运行 `stop.bat`。

**时序常量**（都在 `watch.py` 顶部）：

| 常量 | 值 | 作用 |
| --- | --- | --- |
| `TICK_SECONDS` | 0.4 | 面板心跳轮询（很便宜）——决定"发现关窗"有多快 |
| `GRACE_SECONDS` | 1.0 | 关窗后的反悔时间（固定，无随机） |
| `POLL_SECONDS` | 2.0 | OpenCode 进程检查（较贵，降频） |
| `MINIMIZE_DELAY` | 1.5 | 开窗后隔多久最小化 OpenCode |
| `BEAT_TIMEOUT`（server.py） | 8.0 | 心跳断档多久算页面没了（窗口被强杀时兜底） |

实测（点 X 关窗）：`/bye` 0.02s → 发现 0.10s → 反悔 1.006s → **总计约 1.05s**。

## 前端已实现

- **附件：粘贴 / 拖拽 / 选择文件**（图片会显示缩略图）
  - 截图后直接在输入框 `Ctrl+V`
  - 拖拽文件到窗口任意位置（有虚线提示层）
  - 输入框右侧 `＋` 按钮选择文件
  - 大图自动压缩（>1.2MB 缩到最长边 1600px 再编码），上限 8 个 / 单个 12MB
  - 发送失败会把附件退回输入区；`Esc` 先清空附件，再按一次才中断生成
- **自动重连，不需要手动刷新**：连不上 OpenCode 时退避重试（1.5s→4s），
  顶部显示「正在连接 OpenCode…」；连上后自动加载会话并接管。OpenCode 中途重启也能自动恢复。
- **侧栏可收起**：点顶栏 `☰` 或按 `Ctrl+B`，状态会被记住
- 会话列表（搜索、排序、显示所属目录）
- 落樱 + **水面涟漪**（外圈 + 同心内圈 + 溅起的水点，与花瓣落水严格同步）
- 消息按**时间正序**从上往下排列（接口返回倒序，前端已翻转）
- **流式打字机**：回答逐字长出，带闪烁光标
- **实时思考流**：思考面板自动展开、向下生长（实测一轮可 1500+ 字）
- **实时工具调用**：`running → completed`，含入参与输出
- **本地乐观回显**：你发的问题立刻出现，服务端确认后自动对账
- **自动跟随**：默认跟到最新；手动上滚暂停跟随，回到底部恢复
- 权限确认、代码 diff、终端等尚未做（接口齐全，见 `/openapi.json`）

## 关键实现注意（踩过的坑）

1. **消息结构有两种**：助手是 `content:[{type:"text"|"reasoning"|"tool"}]`，
   用户是**顶层 `text` 字段**，没有 `content`。混用会导致用户提问整条消失。
2. **消息接口只返回"已完成"的消息**，生成中的内容只能靠 SSE 增量事件
   （`session.text.delta` / `session.reasoning.delta`，带 `assistantMessageID`、
   `ordinal`、`delta`）。**不能"每个事件都重取"**——一轮 100+ 事件会变成上百次大请求。
3. **消息接口返回倒序**（最新在索引 0），必须翻转后再渲染。
4. PowerShell 过滤进程时**不要只匹配命令行文本**，否则执行该命令的进程会匹配到自己
   （本项目已两次踩坑）。要同时限定 `Name`，或改用"谁占端口"来识别。

## 环境

- Python 3.12（`D:\Python312\python.exe`），**仅标准库，无需安装依赖**
- 不需要 Node / npm
- 调试代理：设 `OPENCODE_UI_VERBOSE=1` 打印访问日志
