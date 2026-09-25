# ACP 引擎（对话引擎抽象）

> 这份文档讲「把面板从『只能连 OpenCode』变成『可切换的对话引擎』」这套东西。
> 快速上手：想直接看效果 → `python tools/acp_demo.py`；想接真 agent → 看第 6 节。
> 最后更新：2026-09-24（前端 `ui 260924zd`）

## 1. 这是什么 / 为什么

原来的面板（`frontend/` + `backend/server.py`）是**OpenCode 专用**的：
所有 `/api/*` 都是把请求反代给 OpenCode 后台服务，前端消费的是 OpenCode 的
会话/消息/SSE 形状。

我们想让它能驱动**别的 agent**（Codex / Claude Code / DeepSeek Harness…），
而这些 agent 没有一个说 OpenCode 的 API。于是加了一层**引擎抽象**：

- `server.py` 里所有「跟哪个 agent 说话」的语义，改成先问**当前引擎**，由引擎实现；
- 新增 `acp` 引擎，用 **ACP（Agent Client Protocol）** 驱动外部 agent；
- **默认引擎仍是 `opencode`，行为与改造前完全一致**；前端几乎不用改。

```
面板前端（几乎不动，仍消费 OpenCode 形状的 /api/*）
      │
      ▼
backend/server.py  ──  engines.active_engine() 分派
      │
      ├── engines/opencode.py   → 反代到本机 OpenCode（原逻辑，原样搬移）
      └── engines/acp/          → ACP 桥：JSONL/JSON-RPC over stdio 驱动子进程 agent
```

## 2. 文件地图

| 文件 | 作用 |
| --- | --- |
| `backend/engines/__init__.py` | 引擎注册表 + `active_engine_id()` / `set_active_engine_id()` / `get_engine()`（**实例单例缓存**） |
| `backend/engines/base.py` | `Engine` 接口 + 共用 `HOP_BY_HOP` |
| `backend/engines/opencode.py` | `OpenCodeEngine`：`read_service` / `/api/*` 流式反代 / `/healthz`（从 server.py 搬来） |
| `backend/engines/acp/process.py` | `AcpProcess`：stdio 子进程 + **JSONL 分帧**（读/写，Windows 无窗口） |
| `backend/engines/acp/client.py` | `AcpClient`：JSON-RPC 2.0 请求/响应配对 + **双向请求**（agent→client 丢工作线程） |
| `backend/engines/acp/map_events.py` | ACP `session/update` → 前端事件的纯映射；`AssistantTurn`；elicitation schema→form 字段；权限 optionId 选择 |
| `backend/engines/acp/service.py` | `AcpService`：会话/消息/prompt/interrupt/事件订阅/权限与表单的等待 |
| `backend/engines/acp/engine.py` | `AcpEngine(Engine)`：把 service 接到 `/api/*`（含 `/api/event` SSE） |
| `backend/acp_mock_agent.py` | **开发用假 agent**（说 ACP v1），不需要任何外部运行时 |
| `tools/acp_demo.py` | 一键联调：独立端口起面板（不动线上 8787 / 不改全局引擎） |
| `tools/acp_engine_ui_test.py` | 无头交互测试：真在浏览器里切换引擎并验证 SSE 重连 |

## 3. 接口契约（`base.Engine`）

```python
class Engine:
    id: str
    label: str
    def available(self) -> bool          # 依赖是否就绪（OpenCode：service.json 在不在）
    def read_service(self)               # -> (url, auth, version)；不需要上游就抛 NotImplementedError
    def status(self) -> dict             # /engine/status 的自述
    def healthz(self)                    # -> (http_code, body)
    def handle_api(self, handler, method)  # 实现 /api/* 的语义（直接写响应）
```

新增引擎：写 `backend/engines/<id>.py` 实现 `Engine`，在 `registry()` 登记即可，
`server.py` 不用再改。

### 状态文件

| 文件 | 内容 | 说明 |
| --- | --- | --- |
| `runtime/state/_engine.json` | `{"engine": "opencode"}` | 当前引擎；文件缺失/非法/未注册 → 回落 `opencode` |
| `runtime/state/_acp_agents.json` | `{"active":"<id>","agents":[{id,label,command,cwd,env,note}]}` | **agent 注册表（2026-09-25）**；缺失时从 `_acp.json` bootstrap |
| `runtime/state/_acp.json` | `{"command": ["...exe","..."], "cwd": "..."}` | 旧的单 agent 配置；仍兼容（bootstrap 的来源），不再是主配置 |
| `runtime/state/_acp_sessions.json` | 落盘的 ACP 会话+消息 | 重启不丢；续聊时 `session/resume` 接回 |
| 环境变量 `OPENCODE_UI_ENGINE` | 引擎 id | **临时覆盖**当前引擎（联调隔离用；优先于状态文件） |

### HTTP（引擎层新增）

| 接口 | 说明 |
| --- | --- |
| `GET /engine/status` | `{ok, active, available:[id...], detail}` |
| `GET/POST /engine` | POST `{engine}` 切换（校验已注册）；返回 `{ok, active}` |
| `GET /engine/acp/agents` | ACP **agentlist**：`{ok, active, detail, agents:[{id,label,available,note,command,cwd,hasEnv}]}` |
| `POST /engine/acp/agents` | `{id}` 切换当前 agent；`{id, command|label|cwd|env|note}` 修改；改完 `shutdown()` 旧子进程 |

前端在**设置弹窗**里有「对话引擎」下拉（`#cfg-engine`）：打开设置时拉
`/engine/status` 填充；切换后清空当前会话、**重连 SSE**、重载会话列表。
老后端没有这些接口时优雅降级为「不可用」。
设置里引擎选 `acp` 时，下面会多出**「ACP 代理」**一行 → 「agentlist…」打开 `#agents` 弹窗选 agent
（自带 `codex-node` / `codex-client` / `dsh`；金点=命令可找到，红点=找不到）。

## 4. ACP 要点与映射

ACP = **JSON-RPC 2.0 + 换行分帧（JSONL）over stdio**；JSON-RPC 头字段规范，
ACP 自己的键 `camelCase`、判别值 `snake_case`，路径必须绝对、行号 1-based。
客户端启动 agent 子进程；agent 的 stderr 只当日志。

用到的方向：
- 我们 → agent：`initialize` · `session/new` · `session/prompt`(可 `session/cancel`)
- agent → 我们（通知）：`session/update`（文本/思考/工具/…）
- agent → 我们（请求，需回复）：`session/request_permission` · `elicitation/create`

### 事件映射（`session/update` → 前端 SSE）

| ACP `sessionUpdate` | 前端事件 |
| --- | --- |
| `agent_message_chunk` | `session.text.delta` |
| `agent_thought_chunk` | `session.reasoning.delta` |
| `tool_call` | `session.tool.called` |
| `tool_call_update`（completed / failed） | `session.tool.success` / `session.tool.error` |
| `user_message_chunk` | 回放用（暂不落库） |
| 其它 | 忽略 |

一轮结束由 `AcpService._run_turn` 发 `session.text.ended` / `session.step.ended` /
`session.execution.succeeded`，前端据此**重取一次权威消息**。

### 消息映射（`/api/session/{id}/message`）

助手消息拼成 `{id,type:"assistant",time:{created,completed},content:[...]}`，
`content` 里依次是 `{type:"reasoning"|"text"|"tool"}`。用户消息是顶层 `text`。
返回**新→旧**（前端自己按 `time.created` 升序重排）。

### 权限 / elicitation → 复用现有弹窗

- **权限**：`session/request_permission` 的 `toolCall.title` → `action`，
  `toolCall.locations[].path` → `resources`。前端回 `once/always/reject`，
  经 `pick_option_id()` 按 `kind` 选回 `optionId`，响应必须是
  **`{"outcome":{"outcome":"selected","optionId":...}}`**（或 `cancelled`）；
  ⚠ 找不到同向选项时只能回 `cancelled`，**绝不能把「拒绝」退化成「允许」**。
- **elicitation**：`requestedSchema`（扁平 JSON Schema）→ 前端 form 的
  `fields[]`（`enum`→选项按钮，`boolean/number/integer/string`→对应控件）；
  回复 `{"action":"accept","content":{...}}`。只声明并支持 `form` 模式
  （URL 模式回 `decline`）。

### 安全默认（`clientCapabilities`）

只声明 `{"elicitation":{"form":{}}}`；`fs/*`、`terminal/*` **一律不开**
（ACP 规定「没声明 = 不支持」）。要开由调用方以后显式传入。

## 5. 一键联调

```bat
python tools\acp_demo.py            :: 默认 http://127.0.0.1:8799
```

它会：把 `_acp.json` 指向 `backend/acp_mock_agent.py`（退出恢复）→ 设
`OPENCODE_UI_ENGINE=acp`（**只影响本进程**）→ 起独立端口的面板服务。
打开那个地址、新建会话、发一句话，就能看到：文本 / 思考 / 工具 / **权限弹窗** /
**表单弹窗**。**不碰线上 8787、不改全局 `_engine.json`。**

## 6. 接真实 agent

1. 在 `runtime/state/_acp.json` 写启动命令，例如：
   ```json
   { "command": ["<适配器可执行文件>"], "cwd": "D:\\你的项目" }
   ```
2. 重启后端（或在设置里把引擎切成 `acp`）。
3. 新建会话。

各家适配器（2026 现状，**需自行安装**）：
- Codex：`@zed-industries/codex-acp`（有按平台预编译产物）
- Claude Code：`@zed-industries/claude-code-acp`（npm 包）
- DeepSeek Harness：`dsh-acp` / `openma-ai/deepseek-harness-acp`

⚠ **本机没有 Node/npm**：`claude-code-acp` / `dsh` 是 Node/TS 生态；Codex 的适配器
看是否有免 Node 的原生产物。ACP 客户端侧（我们）是**纯标准库 Python**，不需要 Node。

## 7. 测试

原本的假 agent 与测试脚本在临时目录；仓库里现在有：

```bat
python tools\acp_engine_ui_test.py   :: 无头交互：切换引擎 + 验证 SSE 重连
python tools\check_features.py       :: 功能体检（含 7 条引擎断言，共 213 项）
```

另有开发期的回归脚本（`%TEMP%\opencode\`，可重建）：S1 opencode 回归、S2 ACP
客户端、S2b 大消息完整性（3×2MB）、S3 端到端 HTTP+SSE、修复定向测试。

## 8. 已知限制

- **模型清单/切换已接**（2026-09-24）：从 `session/new` 返回的 `configOptions`（`category == "model"`）取清单，
  `/api/model` 返回、`/api/model/default` 返回当前值、切换走 `session/set_config_option`；
  **思考强度（`effort`）做成 variants**（off/low/high/max）。用量/费用仍未接（`usage_update`）。
  ⚠ 模型清单来自**会话**：没建过会话前 `/api/model` 会是空的（先新建一个会话即可）。
- 会话**已持久化**：会话+消息落盘在 `runtime/state/_acp_sessions.json`，**重启后仍能列出**；
  第一次续聊时会用 `session/resume`（失败退 `session/load`）把会话在 agent 侧接回来。
  ⚠ 本修复**之前**丢掉的会话无法找回（当时是纯内存）。
- 附件只发 `text` + `resource_link`；`data:` URI 对真实 agent 多半无效，图片未发。
- `fs/*`、`terminal/*` 未实现。
- 切到**未配置**的引擎会看到 `500 未配置 ACP agent`（`/engine/status` 只报当前引擎可用性）。
- 同一会话**串行发问**：上一轮没结束再发会回 `409`（我们的 turn 映射不支持并发）。

## 9. 本轮踩过的坑（都已修 + 进体检）

1. **`AcpClient` 默认 60s 超时**会掐断真实 agent 的长回答 → 一轮用 `TURN_TIMEOUT`(24h)。
2. **`bufsize=0` 的 stdin 会短写** → 必须用 `memoryview` 循环写满，否则大消息破坏 JSONL。
3. **拒绝被做成允许**：`pick_option_id` 的盲兜底 `options[0]` 会跨方向乱选 → 只接受同向 kind。
4. **切引擎后没重连 SSE**：`/api/event` 在连接时就绑定了当时的引擎 → 切换后必须 `connectEvents()`。
5. **SSE 漏 `end_headers()`**：状态行/响应头从没刷出去，客户端把正文当状态行（用裸 socket 才定位到）。
6. **引擎实例每请求新建** → 会话状态丢失；改成单例缓存。
7. **dsh 在 Windows 上跑不了 bash**（2026-09-24）：默认 `workspace-write` 沙箱**在 Windows 没有可用后端**
   （`no sandbox backend is usable on this host`）→ 必须 `_acp.json` 的 `env.DSH_PERMISSION_MODE = "danger-full-access"`。
   另外它 `spawn bash` 需要 **Git 的 bash 在 PATH**：本机 Git 装在 `D:\Git`，而 PATH 只有 `D:\Git\cmd`。
   **已改成自动探测**：`process.py` 的 `_git_bin_dirs()`（按 PATH 里的 git.exe 反推 + 常见安装位置）
   + `_inject_git_path()` 在起子进程时自动把 `D:\Git\bin`、`usr\bin`、`mingw64\bin` 等补进 PATH —— **不用手配**。
   少了权限模式那项，agent 会**连错几十次 bash**、最后 dsh 内部 `turn/start N while turn N-1 is still open`
   崩掉（面板表现：满屏 `error bash`）。
   ⚠ `danger-full-access` = **无沙箱**，仅适合本机自用；`env` 是**叠加**到进程环境的（见 process.py）。
