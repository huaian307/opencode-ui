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
CLIENT_CAPABILITIES = {"elicitation": {"form": {}}}
# Codex 在自定义 provider 下会把这段告警当正文/想法输出；弹窗"背景"里没必要带它
_MODEL_WARN_RE = re.compile(r"^\s*Warning: Model metadata for `[^`]*` not found\.[^\n]*\n+", re.I)
ANSWER_TIMEOUT = 1800.0      # 等用户答复的上限（秒）；超时按拒绝处理TURN_TIMEOUT = 24 * 3600.0   # 等 agent 跑完一轮的上限（秒）。模型一轮可能很久，
#                            ⚠ 绝不能用 AcpClient 的默认 60s：真实 agent 会被超时掐断，
#                            而 agent 还在继续跑 → 状态错乱（delta 找不到 turn 被丢弃）。


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
                 agent_id="", baseline=None):
        self.command = list(command)
        self.cwd = cwd
        self.env = env
        self.agent_id = agent_id or ""
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
        self._config = {}          # 最近一次 session/new 的 configOptions（模型 / 思考强度）
        self._model = ""
        self._model_from_state = False   # True = 模型清单来自旧式 `models`（切换用 session/set_model）
        self._effort = ""
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

    # ---------------- 持久化 / 会话接回 ----------------

    def _load(self):
        try:
            with open(self._sessions_file, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:  # noqa: BLE001
            return
        for s in (data.get("sessions") or []):
            if not isinstance(s, dict) or not s.get("id") or s["id"] in self._sessions:
                continue
            s.setdefault("title", "新会话")
            s.setdefault("location", {"directory": self._cwd})
            s.setdefault("model", {"id": self._model or "deepseek-v4-pro",
                                   "providerID": "deepseek", "variant": "default"})
            s.setdefault("time", {})
            s.setdefault("tokens", {})
            s.setdefault("cost", None)
            s.setdefault("outcome", None)
            s.setdefault("messages", [])
            s["turn"] = None
            s["permissions"] = {}
            s["forms"] = {}
            self._sessions[s["id"]] = s
            self._order.append(s["id"])
        if self._order:
            self._log("[acp] 已载入 %d 个历史会话" % len(self._order))

    def _save(self):
        try:
            with self._lock:
                data = {"sessions": [
                    {k: s.get(k) for k in ("id", "agent", "title", "location", "model", "time",
                                           "tokens", "cost", "outcome", "messages")}
                    for i in self._order if (s := self._sessions.get(i))]}
            os.makedirs(os.path.dirname(self._sessions_file), exist_ok=True)
            tmp = self._sessions_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False)
            os.replace(tmp, self._sessions_file)
        except Exception:  # noqa: BLE001
            pass

    def _ensure_bound(self, sid):
        """确保会话在「当前 agent 进程」里是活的（重启后第一次续聊要 resume/load 接回）。"""
        with self._lock:
            if sid in self._bound:
                return
            cwd = ((self._sessions.get(sid) or {}).get("location") or {}).get("directory") or self._cwd
        client = self._client_or_none()
        if client is None:
            return
        for method in ("session/resume", "session/load"):
            try:
                res = client.request(method, {"sessionId": sid, "cwd": cwd, "mcpServers": []},
                                     timeout=120) or {}
                with self._lock:
                    self._bound.add(sid)
                # ⚠ resume/load 也会返回 configOptions/models：必须收下，
                # 否则重启后旧会话的模型清单永远是空的。
                self._capture_session_config(res)
                self._apply_baseline(client, sid, res.get("configOptions"))
                self._log("[acp] 已用 %s 接回会话 %s" % (method, sid))
                return
            except Exception as exc:  # noqa: BLE001
                self._log("[acp] %s(%s) 失败：%s" % (method, sid, exc))

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
        for sid in sids[:5]:
            self._ensure_bound(sid)
            if self._config.get("model"):
                return True
        return False

    # ---------------- ACP 回调 ----------------

    def _on_notification(self, method, params):
        if method != "session/update":
            self._log("[acp] 忽略通知：%s" % method)
            return
        sid = params.get("sessionId")
        update = params.get("update") or {}
        with self._lock:
            s = self._sessions.get(sid)
            if s is None:
                return
            if update.get("sessionUpdate") == "user_message_chunk":
                return                                   # 回放用；S3 不落库
            turn = s.get("turn")
            events = turn.apply(update) if turn is not None else []
        self._emit_all(events)

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
                "action": title,
                "resources": locations,
                "message": reason,
                "detail": detail,
                "toolCallId": tc.get("toolCallId"), "options": options}
        waiter = {"event": threading.Event(), "value": None}
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
        oid = pick_option_id(options, decision)
        if oid is None:                       # 没有可选项 / 没匹配上 → 只能按取消回
            return {"outcome": {"outcome": "cancelled"}}
        return {"outcome": {"outcome": "selected", "optionId": oid}}

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

    def _capture_session_config(self, res):
        """从 session/new、session/resume、session/load 的结果里统一收配置。

        ⚠ 这三条都可能返回模型清单；以前只认 session/new，导致服务重启 / 切 agent 后
        旧会话的模型清单一直为空（只能新会话才有）。
        """
        if not isinstance(res, dict):
            return
        self._remember_config(res.get("configOptions"))
        self._remember_model_state(res.get("models"))

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
        mine = None
        for o in (options or []):
            if isinstance(o, dict) and str(o.get("id")) in ("mode", "approval", "approval_mode"):
                mine = o
                break
        if not mine or str(mine.get("currentValue") or "") == want:
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
            out.append({"id": o["value"], "modelID": o["value"], "providerID": "deepseek",
                        "family": "deepseek", "name": o.get("name") or o["value"],
                        "variants": self._effort_variants(),
                        "cost": [{"input": 0, "output": 0, "cache": {"read": 0, "write": 0}}],
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
        return {"id": mid, "providerID": "deepseek", "name": name,
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
        return {"id": mid, "providerID": (ref or {}).get("providerID") or "deepseek",
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
        return {k: s[k] for k in ("id", "title", "location", "model", "time",
                                  "tokens", "cost", "outcome")}

    def create_session(self, title=None, cwd=None, model=None) -> dict:
        client = self.ensure_started()
        cwd = cwd or self._cwd
        res = client.session_new(cwd=cwd) or {}
        self._capture_session_config(res)
        sid = res.get("sessionId") or ("acp_%d" % next(self._id))
        self._apply_baseline(client, sid, res.get("configOptions"))
        now = now_ms()
        s = {"id": sid, "title": title or "新会话", "location": {"directory": cwd},
             "agent": self.agent_id,
             "model": self._effective_model(model), "time": {"created": now, "updated": now},
             "tokens": {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0}},
             "cost": None, "outcome": None,
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
        client = self._client_or_none()
        if client is None:
            return False
        try:
            if ref and ref.get("id"):
                if self._model_from_state:
                    # 旧式 models 清单：用 session/set_model（没有 configOptions 的适配器）
                    client.request("session/set_model",
                                   {"sessionId": sid, "modelId": ref["id"]},
                                   timeout=TURN_TIMEOUT)
                else:
                    client.request("session/set_config_option",
                                   {"sessionId": sid, "configId": "model", "value": ref["id"]},
                                   timeout=TURN_TIMEOUT)
                self._model = str(ref["id"])
            if ref and ref.get("variant") and not self._model_from_state:
                client.request("session/set_config_option",
                               {"sessionId": sid, "configId": "effort", "value": ref["variant"]},
                               timeout=TURN_TIMEOUT)
                self._effort = str(ref["variant"])
        except Exception as exc:  # noqa: BLE001
            self._log("[acp] 切换模型失败：%s" % exc)
            return False
        with self._lock:
            s["model"] = {"id": self._model or "deepseek-v4-pro",
                          "providerID": "deepseek", "variant": self._effort or "default"}
            s["time"]["updated"] = now_ms()
        self._save()
        return True

    def messages(self, sid, limit=80) -> list:
        with self._lock:
            s = self._sessions.get(sid)
            msgs = list(s["messages"]) if (s and self._owns(s)) else []
        if limit:
            msgs = msgs[-limit:]
        return list(reversed(msgs))        # 新 → 旧（跟 OpenCode 一致；前端会自己排序）

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

    def _run_turn(self, sid, turn, blocks):
        outcome = "completed"
        try:
            client = self._client_or_none()
            if client is None:
                raise AcpClosed("客户端未启动")
            client.request("session/prompt", {"sessionId": sid, "prompt": blocks}, timeout=TURN_TIMEOUT)
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
        client.notify("session/cancel", {"sessionId": sid})
        return True
