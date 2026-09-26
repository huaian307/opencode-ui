# -*- coding: utf-8 -*-
"""AcpService —— 用 ACP 支撑前端所需的会话/消息/事件语义。

它把 ACP 的「一个连接 + 多个会话」包装成前端认识的形状：
  - 会话：create / list / get / rename / delete / set_model
  - 对话：prompt（异步跑一轮）/ interrupt
  - 事件：订阅队列（给 /api/event SSE 用）
  - 交互：request_permission / elicitation 阻塞等工作线程 → 复用前端的弹窗
所有状态在内存里（服务重启即清空）——S3 先这样，持久化留到后面。
"""
from __future__ import annotations

import itertools
import json
import os
import queue
import re
import threading

from .. import STATE_DIR
from .client import AcpClient, AcpClosed
from .map_events import AssistantTurn, content_text, now_ms, pick_option_id, schema_to_fields

# 只声明「form 模式的 elicitation」；ACP 规定没声明=不支持，所以 agent 不会乱调别的。
# fs / terminal 一律不开（安全默认），要开由调用方以后显式传入。
# `_meta.terminal_output_delta = true`：告诉 Codex「我按增量收命令输出」——
#   它据此用 `_meta.terminal_output_delta` 逐段发 stdout/stderr（否则只有终态一大坨）。
CLIENT_CAPABILITIES = {"elicitation": {"form": {}},
                       "_meta": {"terminal_output_delta": True}}
# Codex 在自定义 provider 下会把这段告警当正文/想法输出；弹窗"背景"里没必要带它
_MODEL_WARN_RE = re.compile(r"^\s*Warning: Model metadata for `[^`]*` not found\.[^\n]*\n+", re.I)
ANSWER_TIMEOUT = 1800.0      # 等用户答复的上限（秒）；超时按拒绝处理
TURN_TIMEOUT = 24 * 3600.0   # 等 agent 跑完一轮的上限（秒）。模型一轮可能很久，
#                            ⚠ 绝不能用 AcpClient 的默认 60s：真实 agent 会被超时掐断，
#                            而 agent 还在继续跑 → 状态错乱（delta 找不到 turn 被丢弃）。
# 多个 agent 的 AcpService 会同时存在（进程缓存），它们共用 _acp_sessions.json。
# 这个锁保证"只改自己的会话、别人的原样保留"，避免各存各的时互相覆盖。
_SESSIONS_IO_LOCK = threading.RLock()
# 「始终允许」的记忆落盘在这里（多个 agent 共用一条规则表，按 agent 字段区分）
_ALWAYS_FILE = os.path.join(STATE_DIR, "_acp_always.json")
_ALWAYS_LOCK = threading.RLock()
# 前端 titleFromQuestion 的常量（app.js TITLE_MAX）；用来判断「这个标题是自动命名还是用户改的」
_TITLE_MAX = 30


def _load_always_rules() -> list:
    try:
        with open(_ALWAYS_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:  # noqa: BLE001
        return []
    return [r for r in ((data or {}).get("rules") or []) if isinstance(r, dict)]


def _save_always_rules(rules: list) -> None:
    try:
        with _ALWAYS_LOCK:
            os.makedirs(os.path.dirname(_ALWAYS_FILE), exist_ok=True)
            tmp = _ALWAYS_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"rules": rules}, fh, ensure_ascii=False)
            os.replace(tmp, _ALWAYS_FILE)
    except Exception:  # noqa: BLE001
        pass


def _norm_path(p) -> str:
    """路径归一（只用于「始终允许」的范围比较；Windows 不区分大小写）。"""
    s = str(p or "").strip().replace("\\", "/").rstrip("/")
    return s.lower()


def _perm_detail(tc: dict) -> str:
    """给用户看「这个权限用来干什么」——把工具 kind / 将执行的命令 / 目标 / 说明摊开。"""
    tc = tc or {}
    parts = []
    if tc.get("kind"):
        parts.append("类型：%s" % tc.get("kind"))
    ri = tc.get("rawInput")
    if isinstance(ri, dict):
        c = ri.get("command") or ri.get("cmd")
        if c:
            parts.append("将要执行：%s" % (c if isinstance(c, str) else " ".join(str(x) for x in c)))
        if ri.get("path"):
            parts.append("目标：%s" % ri.get("path"))
        if not (c or ri.get("path")):
            parts.append("参数：%s" % json.dumps(ri, ensure_ascii=False)[:400])
    elif ri:
        parts.append("参数：%s" % str(ri)[:400])
    t = content_text(tc.get("content"))
    if t:
        parts.append("说明：%s" % t[:300])
    return "\n".join(parts)


class BusyError(Exception):
    """同一会话上一轮还在生成。"""


class AcpService:
    def __init__(self, command, cwd=None, env=None, log=None, default_model="acp",
                 agent_id="", provider_id="", baseline=None):
        self.command = list(command)
        self.env = env
        self.agent_id = agent_id or ""
        # 模型的「provider」不再写死 deepseek：由注册表里当前 agent 的 provider 决定；
        # 没配就用中性的 "acp"（前端图标会回落到模型 id / 字母徽章）。
        self.provider_id = str(provider_id or "").strip() or "acp"
        self.baseline = baseline or {}
        self._log = log or (lambda s: None)
        self._lock = threading.RLock()
        self._start_lock = threading.Lock()
        self._client = None
        self._sessions = {}
        self._order = []
        self._subs = set()
        self._waiters = {}
        self._id = itertools.count(1)
        self._default_model = default_model
        self._cwd = cwd or os.getcwd()
        self._config = {}          # 最近一次 session/new 的 configOptions（模型 / 思考强度 / 模式）
        self._modes = {}           # 旧式 SessionModeState（codex 会同时给 modes，dsh 只有 configOptions）
        self._model = ""
        self._model_from_state = False   # True = 模型清单来自旧式 `models`（切换用 session/set_model）
        self._effort = ""
        self._commands = []        # available_commands_update（斜杠命令清单，先记着）
        self._bound = set()        # 已与「当前 client」绑定的会话（重启后要 resume 才能续聊）
        self._sessions_file = os.path.join(STATE_DIR, "_acp_sessions.json")
        self._load()               # 重启后把上次的会话/消息读回来

    # ---------------- 事件订阅 ----------------

    def subscribe(self):
        q = queue.Queue()
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            self._subs.discard(q)

    def _emit(self, ev):
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(ev)
            except Exception:  # noqa: BLE001
                pass

    def _emit_all(self, events):
        for e in events:
            self._emit(e)

    # ---------------- 生命周期 ----------------

    def running(self) -> bool:
        with self._lock:
            return bool(self._client and self._client.is_alive())

    def ensure_started(self):
        with self._start_lock:
            with self._lock:
                if self._client is not None and self._client.is_alive():
                    return self._client
                client = AcpClient(self.command, cwd=self.cwd, env=self.env,
                                   on_notification=self._on_notification,
                                   on_request=self._on_request, on_log=self._log)
            client.start()
            client.initialize(client_capabilities=CLIENT_CAPABILITIES)
            with self._lock:
                self._client = client
                self._bound.clear()        # 新进程里旧绑定都失效；续聊时用 _ensure_bound 接回
            self._log("[acp] 已连接 agent：%s" % " ".join(self.command))
            return client

    def close(self):
        with self._start_lock:
            with self._lock:
                client = self._client
                self._client = None
            if client is not None:
                try:
                    client.close()
                except Exception:  # noqa: BLE001
                    pass

    def _client_or_none(self):
        with self._lock:
            return self._client

    @property
    def cwd(self) -> str:
        return self._cwd

    # ---------------- 持久化 / 会话接回 ----------------

    def _load(self):
        with _SESSIONS_IO_LOCK:
            try:
                with open(self._sessions_file, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except Exception:  # noqa: BLE001
                return
        for s in (data.get("sessions") or []):
            if not isinstance(s, dict) or not s.get("id") or s["id"] in self._sessions:
                continue
            # 每个 agent 只装自己的会话：多个 AcpService 同时保活时，保存才不会互相覆盖。
            if not self._owns(s):
                continue
            s.setdefault("title", "新会话")
            s.setdefault("location", {"directory": self._cwd})
            s.setdefault("model", {"id": self._model or "",
                                   "providerID": self.provider_id, "variant": "default"})
            s.setdefault("time", {})
            s.setdefault("tokens", {})
            s.setdefault("cost", None)
            s.setdefault("outcome", None)
            s.setdefault("context", None)
            s.setdefault("plan", None)
            s.setdefault("imported", False)
            s.setdefault("messages", [])
            s.setdefault("agentSessionId", "")
            s["turn"] = None
            s["permissions"] = {}
            s["forms"] = {}
            self._sessions[s["id"]] = s
            self._order.append(s["id"])
        if self._order:
            self._log("[acp] 已载入 %d 个历史会话" % len(self._order))

    def _save(self):
        """只写属于当前 agent 的会话；别人的原样保留（多 agent 保活时互不覆盖）。"""
        try:
            keys = ("id", "agent", "agentSessionId", "title", "location", "model", "time",
                    "tokens", "cost", "outcome", "context", "plan", "imported", "messages")
            with self._lock:
                mine = [{k: s.get(k) for k in keys}
                        for i in self._order if (s := self._sessions.get(i))]
            with _SESSIONS_IO_LOCK:
                existing = []
                try:
                    with open(self._sessions_file, "r", encoding="utf-8") as fh:
                        existing = (json.load(fh) or {}).get("sessions") or []
                except Exception:  # noqa: BLE001
                    existing = []
                others = [s for s in existing
                          if isinstance(s, dict) and s.get("id") and not self._owns(s)]
                os.makedirs(os.path.dirname(self._sessions_file), exist_ok=True)
                tmp = self._sessions_file + ".tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump({"sessions": others + mine}, fh, ensure_ascii=False)
                os.replace(tmp, self._sessions_file)
        except Exception:  # noqa: BLE001
            pass

    def _agent_sid(self, sid):
        """本地会话 id → agent 侧实际 sessionId（resume 失败降级时会新建）。"""
        with self._lock:
            s = self._sessions.get(sid) or {}
            return s.get("agentSessionId") or sid

    def _ensure_bound(self, sid, allow_new: bool = True):
        """确保会话在「当前 agent 进程」里是活的。

        - 优先 session/resume / session/load 接回旧会话；
        - ⚠ 两个都失败（agent 侧早就没这条会话）且 allow_new=True 时，
          **给本地会话新建一个 agent 会话**；以后 prompt / set_model / cancel 都用
          `agentSessionId`，本地消息历史保持权威。
        - `ensure_model_config()` 走 allow_new=False：只是拉模型清单，不该凭空建会话。
        """
        with self._lock:
            if sid in self._bound:
                return
            s = self._sessions.get(sid) or {}
            cwd = (s.get("location") or {}).get("directory") or self._cwd
            target = s.get("agentSessionId") or sid
        client = self._client_or_none()
        if client is None:
            return
        for method in ("session/resume", "session/load"):
            try:
                res = client.request(method, {"sessionId": target, "cwd": cwd, "mcpServers": []},
                                     timeout=120) or {}
                with self._lock:
                    self._bound.add(sid)
                    if self._sessions.get(sid) is not None:
                        self._sessions[sid]["agentSessionId"] = target
                # ⚠ resume/load 也会返回 configOptions/models：必须收下，
                # 否则重启后旧会话的模型清单永远是空的。
                self._capture_session_config(res)
                self._apply_baseline(client, target, res.get("configOptions"))
                self._log("[acp] 已用 %s 接回会话 %s" % (method, sid))
                return
            except Exception as exc:  # noqa: BLE001
                self._log("[acp] %s(%s) 失败：%s" % (method, sid, exc))

        if not allow_new:
            return
        # 降级：本地消息保留，agent 侧开一条新会话
        try:
            res = client.session_new(cwd=cwd) or {}
            new_sid = str(res.get("sessionId") or "")
            if not new_sid:
                return
            with self._lock:
                if self._sessions.get(sid) is not None:
                    self._sessions[sid]["agentSessionId"] = new_sid
                self._bound.add(sid)
            self._capture_session_config(res)
            self._apply_baseline(client, new_sid, res.get("configOptions"))
            self._log("[acp] resume/load 都失败，已为本地会话 %s 新建 agent 会话 %s" % (sid, new_sid))
            self._save()
        except Exception as exc:  # noqa: BLE001
            self._log("[acp] 降级 session/new 也失败：%s" % exc)

    def ensure_model_config(self) -> bool:
        """补全模型清单：若当前还没记住配置（服务重启 / 刚切 agent），
        用属于当前 agent 的最近一个会话 resume 一次，把 configOptions 收回来。

        ⚠ 模型清单来自会话：没有任何会话时本来就没有可拉的，直接回 False。
        """
        if self._config.get("model"):
            return True
        with self._lock:
            sids = [i for i in reversed(self._order)
                    if self._owns(self._sessions.get(i) or {})]
        if not sids:
            return False
        try:
            self.ensure_started()
        except Exception as exc:  # noqa: BLE001
            self._log("[acp] 补全模型清单失败：%s" % exc)
            return False
        # 有些历史会话在 agent 侧已不存在（resume/load 会失败）→ 逐个试最近的几个。
        # ⚠ 这里 allow_new=False：拉模型清单不该顺手建新会话。
        for sid in sids[:5]:
            self._ensure_bound(sid, allow_new=False)
            if self._config.get("model"):
                return True
        return False

    # ---------------- ACP 回调 ----------------

    # 这些是**会话/配置级**更新，不该塞进某一轮 turn：
    #   session_info_update     agent 给会话起了真名（Codex 的 thread name）→ 自动改名
    #   current_mode_update     审批模式被改（用户在 agent 那边切的）→ 让 /api/agent 显示真值
    #   config_option_update    配置项的权威回执（模型/思考强度实际生效的值）
    #   notice                  agent 的提示（重试、配额…）→ 事件 + 日志
    #   available_commands_update 斜杠命令清单（先记着，前端还没入口）
    _SESSION_LEVEL = ("session_info_update", "current_mode_update", "config_option_update",
                      "notice", "available_commands_update")

    def _on_notification(self, method, params):
        if method != "session/update":
            self._log("[acp] 忽略通知：%s" % method)
            return
        sid = params.get("sessionId")
        update = params.get("update") or {}
        kind = update.get("sessionUpdate")
        save = False
        with self._lock:
            s = self._sessions.get(sid)
            if s is None:
                return
            if kind == "user_message_chunk":
                return                                   # 回放用；S3 不落库
            turn = s.get("turn")
            if kind in self._SESSION_LEVEL:
                events = self._session_update(sid, s, update, kind)
                save = any(e.get("type") == "session.renamed" for e in events)
            elif turn is not None:
                events = turn.apply(update)
            else:
                # 没有 turn（比如导入的会话、或 turn 已收尾）也别把更新丢掉：
                # 计划/上下文占用记到会话上，事件照发。
                events = self._detached_update(sid, s, update, kind)
        self._emit_all(events)
        if save:
            self._save()

    def _session_update(self, sid, s, update, kind) -> list:
        """会话/配置级 session/update。调用方已持锁。"""
        if kind == "session_info_update":
            return self._on_session_info(s, update)
        if kind == "current_mode_update":
            cur = str(update.get("currentModeId") or "")
            if cur:
                if isinstance(self._modes, dict):
                    self._modes["currentModeId"] = cur
                opt = self._mode_option()
                if opt is not None:
                    opt["currentValue"] = cur
                self._log("[acp] 审批模式被 agent 改成：%s" % cur)
            return [{"type": "session.mode.update",
                     "data": {"sessionID": sid, "currentModeId": cur}}]
        if kind == "config_option_update":
            # set_config_option 的权威回执：agent 真正生效的值（可能规范化过）
            self._remember_config(update.get("configOptions"))
            mid = self._model
            if mid and s.get("model") and s["model"].get("id") != mid:
                s["model"] = {"id": mid, "providerID": self.provider_id,
                              "variant": s["model"].get("variant") or "default"}
            return [{"type": "session.model.update",
                     "data": {"sessionID": sid, "model": s.get("model")}}]
        if kind == "notice":
            title = str(update.get("title") or "")
            severity = str(update.get("severity") or "info")
            desc = str(update.get("description") or "")
            self._log("[acp] notice(%s)：%s %s" % (severity, title, desc))
            return [{"type": "session.notice",
                     "data": {"sessionID": sid, "severity": severity,
                              "title": title, "description": desc}}]
        if kind == "available_commands_update":
            cmds = [c.get("name") for c in (update.get("availableCommands") or [])
                    if isinstance(c, dict) and c.get("name")]
            self._commands = cmds
            return []
        return []

    def _detached_update(self, sid, s, update, kind) -> list:
        """没有 turn 时的兜底：把仍有长期价值的信息记到会话上。"""
        if kind in ("plan", "plan_update", "plan_removed"):
            turn = AssistantTurn(sid, "adhoc", agent=self.agent_id, model=self._model)
            events = turn.apply(update)
            s["plan"] = turn.plan
            return events
        if kind == "usage_update":
            used = int(update.get("used") or 0)
            size = int(update.get("size") or 0)
            s["context"] = {"used": used, "size": size}
            return [{"type": "session.usage.update",
                     "data": {"sessionID": sid, "used": used, "size": size,
                              "ratio": (round(used / size, 4) if size else None)}}]
        return []

    def _auto_title_ok(self, s, new_title) -> bool:
        """这个标题能不能被 agent 的 `session_info_update` 顶掉。

        只在「还没人认真起名」时替：标题是默认值，或者它就是前端用**第一句提问**
        自动截出来的那个（`titleFromQuestion`，TITLE_MAX=30）。用户手动改过的绝不动。
        """
        cur = str(s.get("title") or "").strip()
        if not cur or cur in ("新会话", "(无标题)", "无标题", "未命名", "新会话 "):
            return True
        first = ""
        for m in (s.get("messages") or []):
            if isinstance(m, dict) and m.get("type") == "user" and str(m.get("text") or "").strip():
                first = str(m["text"])
                break
        if first:
            t = re.sub(r"\s+", " ", first).strip()
            t = re.sub(r"^[#>\-*・\s]+", "", t)
            if len(t) > _TITLE_MAX:
                t = t[:_TITLE_MAX].rstrip() + "…"
            if cur in (t, t.rstrip("…")):
                return True
        return False

    def _on_session_info(self, s, update) -> list:
        title = str(update.get("title") or "").strip()
        if title and title != str(s.get("title") or "") and self._auto_title_ok(s, title):
            s["title"] = title[:120]
            s["time"]["updated"] = now_ms()
            self._log("[acp] agent 给会话命名：%s" % s["title"])
            return [{"type": "session.renamed",
                     "data": {"sessionID": s["id"], "title": s["title"]}}]
        return []

    def _on_request(self, method, params):
        if method == "session/request_permission":
            return self._ask_permission(params)
        if method == "elicitation/create":
            return self._ask_form(params)
        raise RuntimeError("客户端不支持该请求：%s" % method)

    def _ask_permission(self, params):
        sid = params.get("sessionId")
        tc = params.get("toolCall") or {}
        options = params.get("options") or []
        pid = "perm_%d" % next(self._id)
        locations = [loc.get("path") for loc in (tc.get("locations") or [])
                     if isinstance(loc, dict) and loc.get("path")]
        # Codex 会在 toolCall._meta.permission 里给出「人话」的标题和原因 —— 这是最理想的"为什么"。
        meta = ((tc.get("_meta") or {}).get("permission") or {})
        reason = str(meta.get("description") or "").strip()
        title = str(meta.get("title") or "").strip() or tc.get("title") or tc.get("name") or "工具调用"
        kind = str(tc.get("kind") or "")

        # ① 先看「始终允许」的记忆：命中就直接放行，不再弹窗打断用户。
        #    ⚠ 只在**同 agent + 同会话 + 同 kind**（或规则写 "*"）时命中；
        #      有路径范围时还要求路径落在范围内 —— 宁可多问一次，不要乱放行。
        rule = self._match_always(sid, kind, locations)
        if rule is not None:
            oid = pick_option_id(options, "always") or pick_option_id(options, "once")
            if oid is not None:
                self._hit_rule(rule)
                self._log("[acp] 按「始终允许」自动放行：%s（%s）" % (title, rule.get("id")))
                self._emit({"type": "permission.auto", "data": {
                    "sessionID": sid, "action": title, "rule": dict(rule),
                    "toolCallId": tc.get("toolCallId")}})
                return {"outcome": {"outcome": "selected", "optionId": oid}}

        detail = _perm_detail(tc)
        with self._lock:
            s = self._sessions.get(sid)
            ctx = ""
            if s is not None:
                t = s.get("turn")
                if t is not None:
                    ctx = ("".join(t.reason_parts) or "".join(t.text_parts))[-320:].strip()
                    ctx = _MODEL_WARN_RE.sub("", ctx).strip()      # 去掉 Codex 的噪音告警行
        # 没有现成原因（dsh / 部分 agent 不给 _meta.permission）→ 自己合成一段，
        # 并把「模型刚才的想法节选」作为背景 —— 让用户知道这次权限是干什么用的。
        if not reason:
            seg = ["agent 请求权限：%s" % title]
            if locations:
                seg.append("涉及：" + "、".join(str(x) for x in locations[:4]))
            reason = "；".join(seg) + "。"
        if ctx:
            detail = ((detail + "\n") if detail else "") + "背景（模型刚才的想法节选）：" + ctx
        perm = {"id": pid, "sessionID": sid,
                "action": title, "kind": kind,
                "resources": locations,
                "message": reason,
                "detail": detail,
                "toolCallId": tc.get("toolCallId"), "options": options}
        waiter = {"event": threading.Event(), "value": None, "perm": perm}
        with self._lock:
            self._waiters[pid] = waiter
            s = self._sessions.get(sid)
            if s is not None:
                s.setdefault("permissions", {})[pid] = perm
        self._log("[acp] 权限请求 %s：%s" % (pid, perm["action"]))
        self._emit({"type": "permission.updated", "data": {"sessionID": sid, "id": pid}})
        if waiter["event"].wait(ANSWER_TIMEOUT):
            decision = waiter["value"] or "reject"
        else:
            decision = "reject"
        with self._lock:
            self._waiters.pop(pid, None)
            if s is not None:
                s.get("permissions", {}).pop(pid, None)
        if decision == "always":
            # 记住：以后同类请求（同 agent + 同会话 + 同 kind，路径范围相同）直接放行
            self._remember_always(sid, kind, locations, title)
        oid = pick_option_id(options, decision)
        if oid is None:                       # 没有可选项 / 没匹配上 → 只能按取消回
            return {"outcome": {"outcome": "cancelled"}}
        return {"outcome": {"outcome": "selected", "optionId": oid}}

    # ---------------- 权限「始终允许」记忆 ----------------

    def _rule_id(self, sid, kind, path) -> str:
        return "ar_%s_%s_%s" % (self.agent_id or "acp", kind or "*", _norm_path(path) or "*")

    def _remember_always(self, sid, kind, locations, action="") -> dict:
        path = str((locations or [""])[0]) if locations else ""
        rid = self._rule_id(sid, kind, path)
        with _ALWAYS_LOCK:
            rules = _load_always_rules()
            hit = next((r for r in rules if r.get("id") == rid), None)
            if hit is None:
                hit = {"id": rid, "agent": self.agent_id or "acp", "session": sid,
                       "kind": kind or "*", "path": path, "hits": 0, "ts": now_ms()}
                rules.append(hit)
            hit["ts"] = now_ms()
            if action:
                hit["action"] = str(action)[:120]
            _save_always_rules(rules)
        self._log("[acp] 已记住「始终允许」：%s / %s%s"
                  % (kind or "*", path or "(不限路径)", "（本会话）"))
        return hit

    def _match_always(self, sid, kind, locations):
        """找出该放行的记忆规则；没有就回 None。"""
        rules = [r for r in _load_always_rules()
                 if (r.get("agent") in (self.agent_id or "acp", "*"))
                 and (r.get("session") in (sid, "*"))
                 and (r.get("kind") in (kind or "*", "*"))]
        if not rules:
            return None
        want = [_norm_path(p) for p in (locations or []) if p]
        for r in rules:                     # 有路径范围的规则要求命中路径；不限路径的 (*"") 放行一切
            rp = _norm_path(r.get("path"))
            if not rp:
                return r
            if any(p == rp or p.startswith(rp + "/") for p in want):
                return r
        return None

    def _hit_rule(self, rule) -> None:
        rid = rule.get("id")
        with _ALWAYS_LOCK:
            rules = _load_always_rules()
            for r in rules:
                if r.get("id") == rid:
                    r["hits"] = int(r.get("hits") or 0) + 1
                    r["ts"] = now_ms()
                    break
            _save_always_rules(rules)

    def always_rules(self, sid=None) -> list:
        """当前 agent 的「始终允许」规则（sid 给定则只看那条会话）。"""
        out = [r for r in _load_always_rules() if r.get("agent") in (self.agent_id or "acp", "*")]
        if sid:
            out = [r for r in out if r.get("session") in (sid, "*")]
        return out

    def forget_always(self, sid=None, kind=None, rule_id=None) -> int:
        """清记忆。sid/kind/rule_id 都不给 = 全清（返回清掉几条）。"""
        with _ALWAYS_LOCK:
            rules = _load_always_rules()
            keep = []
            for r in rules:
                if r.get("agent") not in (self.agent_id or "acp", "*"):
                    keep.append(r)
                    continue
                if rule_id and r.get("id") != rule_id:
                    keep.append(r)
                    continue
                if sid and r.get("session") not in (sid, "*"):
                    keep.append(r)
                    continue
                if kind and r.get("kind") not in (kind, "*"):
                    keep.append(r)
                    continue
                if not (rule_id or sid or kind):
                    continue           # 全清
            _save_always_rules(keep)
            return len(rules) - len(keep)

    def _ask_form(self, params):
        sid = params.get("sessionId")
        fid = "form_%d" % next(self._id)
        if params.get("mode") != "form":             # URL 模式暂不支持
            return {"action": "decline"}
        form = {"id": fid, "sessionID": sid,
                "title": params.get("message") or "需要你选择",
                "fields": schema_to_fields(params.get("requestedSchema"))}
        waiter = {"event": threading.Event(), "value": None}
        with self._lock:
            self._waiters[fid] = waiter
            s = self._sessions.get(sid)
            if s is not None:
                s.setdefault("forms", {})[fid] = form
        self._log("[acp] 表单请求 %s：%s" % (fid, form["title"]))
        self._emit({"type": "form.updated", "data": {"sessionID": sid, "id": fid}})
        if waiter["event"].wait(ANSWER_TIMEOUT):
            answer = waiter["value"]
            action = "accept" if answer is not None else "decline"
        else:
            answer, action = None, "cancel"
        with self._lock:
            self._waiters.pop(fid, None)
            if s is not None:
                s.get("forms", {}).pop(fid, None)
        if action == "accept":
            return {"action": "accept", "content": answer or {}}
        return {"action": action}

    # ---------------- 交互答复（前端调） ----------------

    def reply_permission(self, sid, pid, decision) -> bool:
        with self._lock:
            w = self._waiters.get(pid)
        if not w:
            return False
        w["value"] = decision
        w["event"].set()
        return True

    def reply_form(self, sid, fid, answer) -> bool:
        with self._lock:
            w = self._waiters.get(fid)
        if not w:
            return False
        w["value"] = answer
        w["event"].set()
        return True

    def list_permissions(self, sid) -> list:
        with self._lock:
            s = self._sessions.get(sid) or {}
            return list((s.get("permissions") or {}).values())

    def list_forms(self, sid) -> list:
        with self._lock:
            s = self._sessions.get(sid) or {}
            return list((s.get("forms") or {}).values())

    # ---------------- 会话 ----------------

    def _remember_config(self, options):
        """记住 session/new 返回的 configOptions（ACP 的模型/思考强度就在这里）。"""
        if not isinstance(options, list):
            return
        for opt in options:
            if isinstance(opt, dict) and opt.get("id"):
                self._config[opt["id"]] = opt
        m = self._config.get("model") or {}
        if m.get("currentValue"):
            self._model = str(m["currentValue"])
        if m:
            self._model_from_state = False
        e = self._config.get("effort") or {}
        if e.get("currentValue"):
            self._effort = str(e["currentValue"])

    def _remember_model_state(self, models):
        """兼容只返回 `models`（ACP SessionModelState）、不返回 configOptions 的适配器。

        configOptions 里有 model 时以它为准（它带思考强度等更多信息）；这里只兜底。
        """
        if self._config.get("model") or not isinstance(models, dict):
            return
        opts = []
        for m in (models.get("availableModels") or []):
            if not isinstance(m, dict):
                continue
            mid = m.get("modelId") or m.get("id")
            if not mid:
                continue
            opts.append({"value": str(mid), "name": m.get("name") or str(mid),
                         "description": m.get("description")})
        if not opts:
            return
        cur = str(models.get("currentModelId") or "")
        self._config["model"] = {"id": "model", "name": "Model", "category": "model",
                                 "type": "select", "currentValue": cur, "options": opts}
        self._model_from_state = True
        if cur:
            self._model = cur

    def _remember_modes_state(self, modes):
        """记住旧式 SessionModeState（codex 的 modes.availableModes / currentModeId）。"""
        if not isinstance(modes, dict):
            return
        arr = modes.get("availableModes") or []
        if not arr:
            return
        self._modes = {"currentModeId": str(modes.get("currentModeId") or ""),
                       "availableModes": [m for m in arr if isinstance(m, dict)]}

    def _mode_option(self):
        """找到「审批模式」configOption（不同 adapter 叫 mode / approval / approval_mode）。"""
        for key in ("mode", "approval", "approval_mode"):
            opt = self._config.get(key)
            if isinstance(opt, dict) and opt.get("options"):
                return opt
        for opt in self._config.values():
            if isinstance(opt, dict) and str(opt.get("category") or "") == "mode" and opt.get("options"):
                return opt
        return None

    def agents(self) -> list:
        """把 ACP 的「模式」暴露成和 OpenCode `/api/agent` 相同的形状。

        codex 走 `modes.availableModes`；dsh 等只有 configOptions 的走 `_mode_option()`。
        """
        out = []
        cur = str((self._modes or {}).get("currentModeId") or "")
        for m in (self._modes.get("availableModes") or []):
            mid = m.get("id") or m.get("modeId")
            if not mid:
                continue
            out.append({"id": str(mid), "name": m.get("name") or str(mid),
                        "description": m.get("description") or "",
                        "mode": "primary", "hidden": False,
                        "current": str(mid) == cur})
        if out:
            return out
        opt = self._mode_option()
        if not opt:
            return []
        cur = str(opt.get("currentValue") or "")
        for o in (opt.get("options") or []):
            if not isinstance(o, dict) or not o.get("value"):
                continue
            out.append({"id": str(o["value"]), "name": o.get("name") or str(o["value"]),
                        "description": o.get("description") or "",
                        "mode": "primary", "hidden": False,
                        "current": str(o["value"]) == cur})
        return out

    def set_agent(self, sid, aid) -> bool:
        """切换 ACP 会话的模式：优先 `session/set_mode`，退回 `session/set_config_option`。"""
        aid = str(aid or "").strip()
        if not aid:
            return False
        try:
            client = self.ensure_started()
            self._ensure_bound(sid, allow_new=False)
            asid = self._agent_sid(sid)
        except Exception as exc:  # noqa: BLE001
            self._log("[acp] 切换模式前接回会话失败：%s" % exc)
            return False
        # 1) 有 SessionModeState 的 adapter：标准 session/set_mode
        if (self._modes or {}).get("availableModes"):
            try:
                client.request("session/set_mode", {"sessionId": asid, "modeId": aid},
                               timeout=TURN_TIMEOUT)
                self._modes["currentModeId"] = aid
                self._log("[acp] 模式切换：%s" % aid)
                return True
            except Exception as exc:  # noqa: BLE001
                self._log("[acp] session/set_mode 失败，改用 config option：%s" % exc)
        # 2) configOptions 形态
        opt = self._mode_option()
        if not opt:
            return False
        try:
            res = client.request("session/set_config_option",
                                 {"sessionId": asid, "configId": opt["id"], "value": aid},
                                 timeout=TURN_TIMEOUT)
            self._remember_config((res or {}).get("configOptions"))
            opt["currentValue"] = aid
            self._log("[acp] 模式切换：%s" % aid)
            return True
        except Exception as exc:  # noqa: BLE001
            self._log("[acp] 切换模式失败：%s" % exc)
            return False

    def close_agent_session(self, sid) -> bool:
        """删除本地会话时，best-effort 告诉 agent 侧 close/delete（不支持就忽略）。"""
        client = self._client_or_none()
        if client is None:
            return False
        asid = self._agent_sid(sid)
        for method in ("session/close", "session/delete"):
            try:
                client.request(method, {"sessionId": asid}, timeout=60)
                self._log("[acp] 已在 agent 侧 %s 会话 %s" % (method, sid))
                return True
            except Exception:  # noqa: BLE001
                continue
        return False

    # ---------------- agent 侧已有会话（session/list → 导入）----------------

    def supports_session_list(self) -> bool:
        """agent 是否宣告支持 `session/list`（Codex 宣告了；dsh 之类没有）。

        ⚠ 宣告值常是**空对象** `{}`（Codex 就是 `list: {}`），所以只能判"键在不在"，
          不能 `bool(value)` —— `bool({})` 是 False，会把支持的 agent 误判成不支持。
        """
        client = self._client_or_none()
        if client is None:
            return False
        caps = (client.agent_capabilities or {}).get("sessionCapabilities") or {}
        if not isinstance(caps, dict):
            return False
        return "list" in caps and caps.get("list") is not False

    def list_remote(self, cwd=None, cursor="") -> dict:
        """列出 **agent 侧**已有的会话（Codex 客户端里聊过的那些），供面板「导入」。

        ACP 形状：`session/list {cwd?, cursor?}` → `{sessions:[{sessionId,cwd,title?,updatedAt?}], nextCursor?}`
        旧适配器不支持时回 `{"supported": False}`（前端可优雅降级）。
        """
        try:
            client = self.ensure_started()
        except Exception as exc:  # noqa: BLE001
            return {"supported": False, "sessions": [], "error": str(exc)}
        if not self.supports_session_list():
            return {"supported": False, "sessions": [],
                    "error": "当前 agent 不支持 session/list（没有 agentlist 能力）"}
        params = {}
        if cwd:
            params["cwd"] = str(cwd)
        if cursor:
            params["cursor"] = str(cursor)
        try:
            res = client.request("session/list", params, timeout=120) or {}
        except Exception as exc:  # noqa: BLE001
            self._log("[acp] session/list 失败：%s" % exc)
            return {"supported": True, "sessions": [], "error": str(exc)}
        with self._lock:
            owned = {}
            for i in self._order:
                s = self._sessions.get(i) or {}
                if self._owns(s) and s.get("agentSessionId"):
                    owned.setdefault(str(s["agentSessionId"]), i)
        out = []
        for it in (res.get("sessions") or []):
            if not isinstance(it, dict):
                continue
            rid = str(it.get("sessionId") or "")
            if not rid:
                continue
            out.append({"id": rid, "title": str(it.get("title") or ""),
                        "cwd": str(it.get("cwd") or ""),
                        "updatedAt": str(it.get("updatedAt") or ""),
                        "imported": rid in owned, "localID": owned.get(rid) or ""})
        self._log("[acp] agent 侧有 %d 条会话（已导入 %d）" % (len(out), sum(1 for x in out if x["imported"])))
        return {"supported": True, "sessions": out, "cursor": res.get("nextCursor") or None}

    def import_remote(self, remote_id, title=None) -> dict:
        """把 agent 侧已有的一条会话「领」到面板里（当初设计的"读歌单式"导入）。

        只在本地建一条**指向**该 agent 会话的记录，不复制历史 ——
        ACP 没有「读别人消息」的接口，历史留在 agent 那边；发第一条消息时
        `_ensure_bound()` 会 `session/resume` 接回去，agent 就能接着聊。
        """
        rid = str(remote_id or "").strip()
        if not rid:
            raise ValueError("缺少 agent 会话 id")
        with self._lock:
            for i in self._order:
                s = self._sessions.get(i) or {}
                if self._owns(s) and str(s.get("agentSessionId") or "") == rid:
                    return self._public(s)              # 已经导入过，幂等
        cwd = self._cwd
        title = str(title or "").strip()
        if not title:
            try:
                remote = self.list_remote()
                hit = next((x for x in (remote.get("sessions") or []) if x.get("id") == rid), None)
                if hit:
                    cwd = hit.get("cwd") or cwd
                    title = str(hit.get("title") or "")
            except Exception:  # noqa: BLE001
                pass
        now = now_ms()
        sid = "acp_r%d" % next(self._id)
        s = {"id": sid, "title": title or "导入的会话", "location": {"directory": cwd},
             "agent": self.agent_id, "agentSessionId": rid,
             "model": self._effective_model(None), "time": {"created": now, "updated": now},
             "tokens": {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0}},
             "cost": None, "outcome": None, "context": None, "plan": None, "imported": True,
             "messages": [], "turn": None, "permissions": {}, "forms": {}}
        with self._lock:
            self._sessions[sid] = s
            self._order.append(sid)
            pub = self._public(s)
        # 接回一次只为把「模型 / 审批模式」这些配置收回来（失败不阻断导入）
        try:
            self._ensure_bound(sid, allow_new=False)
        except Exception as exc:  # noqa: BLE001
            self._log("[acp] 导入后接回失败（不影响导入）：%s" % exc)
        with self._lock:
            if self._sessions.get(sid) is not None:
                self._sessions[sid]["model"] = self._effective_model(None)
                pub = self._public(self._sessions[sid])
        self._save()
        self._emit({"type": "session.created", "data": {"sessionID": sid, "session": pub}})
        return pub

    def _capture_session_config(self, res):
        """从 session/new、session/resume、session/load 的结果里统一收配置。

        ⚠ 这三条都可能返回模型清单；以前只认 session/new，导致服务重启 / 切 agent 后
        旧会话的模型清单一直为空（只能新会话才有）。
        """
        if not isinstance(res, dict):
            return
        self._remember_config(res.get("configOptions"))
        self._remember_model_state(res.get("models"))
        self._remember_modes_state(res.get("modes"))

    def _apply_baseline(self, client, sid, options):
        """把「共享基线」里的 mode 应用到新会话。

        ⚠ 为什么需要：Codex 的 "Approve for me"(mode=agent / auto_review) 会用**内建模型**
        `codex-auto-review` 审每条命令，而自定义 provider（DeepSeek）不认识这个模型名 →
        每条命令都被 "Guardian Review" 拒掉（实测）。基线默认设 `read-only`：
        让 agent 通过 ACP `session/request_permission` **问我们**（面板弹窗，带用途说明）。
        """
        want = str((self.baseline or {}).get("mode") or "").strip()
        if not want:
            return
        # 1) 有 SessionModeState 的 adapter（Codex）：标准 session/set_mode 才真的生效。
        #    configOptions 里那个 mode 对它只是只读展示，set_config_option 常常不起作用。
        modes = self._modes or {}
        avail = [str(m.get("id")) for m in (modes.get("availableModes") or [])
                 if isinstance(m, dict) and m.get("id")]
        if want in avail:
            if str(modes.get("currentModeId") or "") == want:
                return
            try:
                client.request("session/set_mode", {"sessionId": sid, "modeId": want}, timeout=30)
                modes["currentModeId"] = want
                self._log("[acp] 基线：mode=%s（set_mode）" % want)
                return
            except Exception as exc:  # noqa: BLE001
                self._log("[acp] 基线 set_mode=%s 失败，尝试 config option：%s" % (want, exc))
        # 2) 只有 configOptions 的 adapter：退回 session/set_config_option
        mine = None
        for o in (options or []):
            if isinstance(o, dict) and str(o.get("id")) in ("mode", "approval", "approval_mode"):
                mine = o
                break
        if not mine or str(mine.get("currentValue") or "") == want:
            return
        # ⚠ 只在**这个 agent 真的有这个值**时才发（不同 agent 的模式名不一样：
        #   Codex 是 read-only/agent/agent-full-access，OpenCode 是 build/plan）。
        #   不加这道判断的话每次建会话都会收到 `-32602: mode not found: read-only`（实测）。
        vals = [str(x.get("value")) for x in (mine.get("options") or [])
                if isinstance(x, dict) and x.get("value")]
        if vals and want not in vals:
            self._log("[acp] 基线的 mode=%s 这个 agent 没有（可用：%s）→ 跳过；"
                      "想给它指定就用注册表里该 agent 的 `mode` 字段（agent 覆盖基线）"
                      % (want, " / ".join(vals)))
            return
        try:
            client.request("session/set_config_option",
                           {"sessionId": sid, "configId": mine.get("id"), "value": want}, timeout=30)
            mine["currentValue"] = want
            self._log("[acp] 基线：mode=%s" % want)
        except Exception as exc:  # noqa: BLE001
            self._log("[acp] 设置 mode=%s 失败：%s" % (want, exc))

    def _effort_variants(self):
        e = self._config.get("effort") or {}
        return [{"id": o.get("value"), "name": o.get("name")}
                for o in (e.get("options") or []) if isinstance(o, dict) and o.get("value")]

    def models(self) -> list:
        """把 ACP 的 model configOption 转成前端要的模型清单形状。"""
        out = []
        for o in ((self._config.get("model") or {}).get("options") or []):
            if not isinstance(o, dict) or not o.get("value"):
                continue
            out.append({"id": o["value"], "modelID": o["value"], "providerID": self.provider_id,
                        "family": self.provider_id, "name": o.get("name") or o["value"],
                        "variants": self._effort_variants(),
                        # ACP 不给官方价格；null = 前端显示「费用未知」，不要谎报免费
                        "cost": None,
                        "capabilities": {"tools": True}})
        return out

    def model_default(self):
        """当前模型。⚠ 不能返回 AcpService 的占位 default_model（"acp"）：
        还没有会话时配置里没有模型，此时应回 None，让前端别把 "acp" 当模型写进新会话。"""
        mid = self._model or str((self._config.get("model") or {}).get("currentValue") or "")
        if not mid:
            return None
        name = mid
        for o in ((self._config.get("model") or {}).get("options") or []):
            if isinstance(o, dict) and o.get("value") == mid:
                name = o.get("name") or mid
        return {"id": mid, "providerID": (self.provider_id or ""), "name": name,
                "variant": self._effort or "default"}

    def _effective_model(self, ref=None):
        """算出会话实际用的模型；**本 agent 不认识的模型名一律忽略**，回落到当前值。"""
        known = [o.get("value") for o in ((self._config.get("model") or {}).get("options") or [])
                 if isinstance(o, dict) and o.get("value")]
        mid = str((ref or {}).get("id") or "")
        if mid and known and mid not in known:
            mid = ""
        if not mid:
            mid = self._model or (known[0] if known else "")
        if not mid:
            return {}
        return {"id": mid, "providerID": (ref or {}).get("providerID") or self.provider_id,
                "variant": (ref or {}).get("variant") or self._effort or "default"}

    def _owns(self, s) -> bool:
        """这个会话能不能用「当前 agent」操作。

        ACP 的 sessionId 只有**创建它的 agent**认识 —— 切了 agent 还拿旧 id 去 prompt，
        对方会回 `-32603 Internal error (Session SID not found)`。所以按 agent 隔离。
        旧数据没有 `agent` 字段 → 视为 `dsh`（加这个功能之前只有 dsh）。
        """
        if not self.agent_id:
            return True
        return (s.get("agent") or "dsh") == self.agent_id

    def _public(self, s) -> dict:
        return {k: s.get(k) for k in ("id", "title", "location", "model", "time",
                                      "tokens", "cost", "outcome", "context",
                                      "plan", "imported")}

    def create_session(self, title=None, cwd=None, model=None) -> dict:
        client = self.ensure_started()
        cwd = cwd or self._cwd
        res = client.session_new(cwd=cwd) or {}
        self._capture_session_config(res)
        sid = res.get("sessionId") or ("acp_%d" % next(self._id))
        self._apply_baseline(client, sid, res.get("configOptions"))
        now = now_ms()
        s = {"id": sid, "title": title or "新会话", "location": {"directory": cwd},
             "agent": self.agent_id, "agentSessionId": sid,
             "model": self._effective_model(model), "time": {"created": now, "updated": now},
             "tokens": {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0}},
             "cost": None, "outcome": None, "context": None, "plan": None, "imported": False,
             "messages": [], "turn": None, "permissions": {}, "forms": {}}
        with self._lock:
            self._sessions[sid] = s
            self._order.append(sid)
            self._bound.add(sid)
            pub = self._public(s)
        self._save()
        self._emit({"type": "session.created", "data": {"sessionID": sid, "session": pub}})
        return pub

    def list_sessions(self) -> list:
        with self._lock:
            return [self._public(self._sessions[i]) for i in reversed(self._order)
                    if i in self._sessions and self._owns(self._sessions[i])]

    def get_session(self, sid):
        with self._lock:
            s = self._sessions.get(sid)
            return self._public(s) if (s and self._owns(s)) else None

    def delete_session(self, sid) -> bool:
        with self._lock:
            existed = self._sessions.pop(sid, None) is not None
            if sid in self._order:
                self._order.remove(sid)
            self._bound.discard(sid)
        if existed:
            self._save()
            self._emit({"type": "session.deleted", "data": {"sessionID": sid}})
        return existed

    def rename_session(self, sid, title) -> bool:
        with self._lock:
            s = self._sessions.get(sid)
            if not s:
                return False
            s["title"] = title
            s["time"]["updated"] = now_ms()
        self._save()
        self._emit({"type": "session.renamed", "data": {"sessionID": sid, "title": title}})
        return True

    def set_model(self, sid, ref) -> bool:
        with self._lock:
            s = self._sessions.get(sid)
            if not s:
                return False
        try:
            client = self.ensure_started()
            self._ensure_bound(sid, allow_new=False)   # 接回旧会话（失败也不在改模型时建新会话）
            asid = self._agent_sid(sid)
        except Exception as exc:  # noqa: BLE001
            self._log("[acp] 切换模型前接回会话失败：%s" % exc)
            return False
        try:
            if ref and ref.get("id"):
                if self._model_from_state:
                    # 旧式 models 清单：用 session/set_model（没有 configOptions 的适配器）
                    client.request("session/set_model",
                                   {"sessionId": asid, "modelId": ref["id"]},
                                   timeout=TURN_TIMEOUT)
                    self._model = str(ref["id"])
                else:
                    res = client.request("session/set_config_option",
                                         {"sessionId": asid, "configId": "model", "value": ref["id"]},
                                         timeout=TURN_TIMEOUT)
                    # ⚠ 以 **agent 的回执**为准：它可能规范化我们给的值、或直接不接受。
                    #   （实测 OpenCode 能改、回执里 currentValue 就是新模型；Codex 也会回 configOptions）
                    self._remember_config((res or {}).get("configOptions"))
                    got = str((self._config.get("model") or {}).get("currentValue") or "")
                    if got and got != str(ref["id"]):
                        self._log("[acp] 模型没被 agent 接受：请求 %s，实际仍是 %s" % (ref["id"], got))
            if ref and ref.get("variant") and not self._model_from_state:
                res = client.request("session/set_config_option",
                                     {"sessionId": asid, "configId": "effort", "value": ref["variant"]},
                                     timeout=TURN_TIMEOUT)
                self._remember_config((res or {}).get("configOptions"))
                self._effort = str(ref["variant"])
        except Exception as exc:  # noqa: BLE001
            self._log("[acp] 切换模型失败：%s" % exc)
            return False
        with self._lock:
            s["model"] = {"id": self._model or "",
                          "providerID": (ref or {}).get("providerID") or self.provider_id,
                          "variant": self._effort or "default"}
            s["time"]["updated"] = now_ms()
        self._save()
        return True

    def messages(self, sid, limit=80, cursor="") -> tuple:
        """返回 `(新→旧消息列表, 下一页 cursor)`，与 OpenCode `/message` 形状对齐。

        `cursor` 是上一页返回的**最旧一条消息 id**；传回来就只取比它更旧的消息。
        """
        with self._lock:
            s = self._sessions.get(sid)
            asc = list(s["messages"]) if (s and self._owns(s)) else []   # 旧 → 新
        if cursor:
            idx = next((i for i, m in enumerate(asc) if str(m.get("id")) == str(cursor)), -1)
            if idx >= 0:
                asc = asc[:idx]
        start = max(0, len(asc) - int(limit)) if limit else 0
        page = asc[start:]
        next_cursor = page[0].get("id") if (page and start > 0) else None
        return list(reversed(page)), next_cursor

    # ---------------- 对话 ----------------

    def _prompt_blocks(self, text, files):
        blocks = []
        if text:
            blocks.append({"type": "text", "text": text})
        for f in (files or []):
            uri = (f or {}).get("uri") or (f or {}).get("dataUrl") or ""
            if uri:
                blocks.append({"type": "resource_link", "uri": uri,
                               "name": (f or {}).get("name") or ""})
        return blocks

    def is_busy(self, sid) -> bool:
        with self._lock:
            s = self._sessions.get(sid)
            return bool(s and s.get("turn") is not None)

    def prompt(self, sid, text, files=None) -> dict:
        with self._lock:
            s = self._sessions.get(sid)
            if s is None or not self._owns(s):
                raise KeyError("会话不存在：%s" % sid)
            if s.get("turn") is not None:
                # 我们的映射是「每个会话同一时刻只有一个 turn」；并发发问会串轮
                # （delta 找不到自己的 turn → 静默丢弃）。宁可明确拒绝。
                raise BusyError("上一轮还在生成，请等它结束或先中断")
        client = self.ensure_started()
        self._ensure_bound(sid)        # 重启后第一次续聊：先 resume 把会话在 agent 侧接回来
        now = now_ms()
        um = {"id": "u_%d" % next(self._id), "type": "user", "text": text,
              "time": {"created": now}}      # ⚠ 必须有 time.created，前端按它升序排；否则会排到最顶
        if files:
            um["files"] = files
        with self._lock:
            s["messages"].append(um)
            turn = AssistantTurn(sid, "a_%d" % next(self._id), agent=(self.agent_id or "acp"),
                                 model=(s["model"] or {}).get("id") or self._model or "")
            turn.started = max(turn.started, now + 1)   # 助手必须严格晚于用户，否则同毫秒排序会乱
            s["turn"] = turn
            s["time"]["updated"] = now_ms()
        self._emit({"type": "session.step.started", "data": {
            "sessionID": sid, "assistantMessageID": turn.id, "agent": turn.agent,
            "model": {"id": turn.model}, "started": turn.started}})
        blocks = self._prompt_blocks(text, files)
        threading.Thread(target=self._run_turn, args=(sid, turn, blocks), daemon=True).start()
        return {"ok": True}

    def _record_usage(self, s, usage, stop):
        """把一轮的 token 用量累加到会话上，并把 stopReason 记成「结果」。

        ⚠ ACP 的 `session/prompt` 结果带 `usage`（zUsage: totalTokens/inputTokens/
          outputTokens/thoughtTokens/cachedReadTokens/cachedWriteTokens）——
          这是**账单口径**的 token，和 `usage_update`（上下文窗口占用）不是一回事。
        ⚠ ACP 不给官方单价，所以 `cost` 仍然是 None（面板显示「费用未知」，不谎报免费）。
        """
        if isinstance(usage, dict):
            def num(*keys):
                for k in keys:
                    v = usage.get(k)
                    if isinstance(v, (int, float)):
                        return int(v)
                return 0
            with self._lock:
                tok = s.setdefault("tokens", {}) or {}
                if not isinstance(tok, dict):
                    tok = {}
                    s["tokens"] = tok
                cache = tok.get("cache") if isinstance(tok.get("cache"), dict) else {}
                tok["input"] = int(tok.get("input") or 0) + num("inputTokens", "input_tokens")
                tok["output"] = int(tok.get("output") or 0) + num("outputTokens", "output_tokens")
                tok["reasoning"] = int(tok.get("reasoning") or 0) + num("thoughtTokens", "reasoningTokens")
                cache["read"] = int(cache.get("read") or 0) + num("cachedReadTokens")
                cache["write"] = int(cache.get("write") or 0) + num("cachedWriteTokens")
                tok["cache"] = cache
                cost = usage.get("cost")
                if isinstance(cost, dict) and isinstance(cost.get("amount"), (int, float)):
                    s["cost"] = float(cost["amount"])
                elif isinstance(cost, (int, float)):
                    s["cost"] = float(cost)
        if stop:
            s["outcome"] = {"end_turn": "completed", "max_tokens": "max_tokens",
                            "max_turn_requests": "max_turn_requests", "cancelled": "cancelled",
                            "refusal": "refusal"}.get(str(stop), str(stop))

    def _run_turn(self, sid, turn, blocks):
        outcome = "completed"
        usage = None
        stop = None
        try:
            client = self._client_or_none()
            if client is None:
                raise AcpClosed("客户端未启动")
            res = client.request("session/prompt", {"sessionId": self._agent_sid(sid), "prompt": blocks},
                                 timeout=TURN_TIMEOUT)
            if isinstance(res, dict):
                usage = res.get("usage")
                stop = res.get("stopReason")
        except Exception as exc:  # noqa: BLE001
            outcome = "error"
            turn.aborted = True
            if not (turn.text_parts or turn.reason_parts or turn.tools):
                turn.text_parts.append("（本轮未产生输出：%s: %s）" % (type(exc).__name__, exc))
            self._emit({"type": "session.error", "data": {
                "sessionID": sid, "assistantMessageID": turn.id,
                "error": "%s: %s" % (type(exc).__name__, exc)}})
        turn.completed = now_ms()
        with self._lock:
            s = self._sessions.get(sid)
            if s is not None:
                s["messages"].append(turn.message())
                if s.get("turn") is turn:
                    s["turn"] = None
                s["time"]["updated"] = now_ms()
                s["outcome"] = outcome
                if turn.plan is not None:
                    s["plan"] = turn.plan
                if turn.context:
                    s["context"] = turn.context
        if s is not None:
            self._record_usage(s, usage, stop)
        self._save()
        # 终态事件 → 前端会去重取一次权威消息
        self._emit({"type": "session.text.ended",
                    "data": {"sessionID": sid, "assistantMessageID": turn.id}})
        self._emit({"type": "session.step.ended",
                    "data": {"sessionID": sid, "assistantMessageID": turn.id}})
        self._emit({"type": "session.execution.succeeded", "data": {"sessionID": sid}})

    def interrupt(self, sid) -> bool:
        client = self._client_or_none()
        if client is None:
            return False
        client.notify("session/cancel", {"sessionId": self._agent_sid(sid)})
        return True
