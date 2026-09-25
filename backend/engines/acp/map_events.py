# -*- coding: utf-8 -*-
"""ACP session/update → 前端（OpenCode 形状）事件与消息。

纯逻辑，不碰进程/网络，方便单测。前端的消费点（见 frontend/app.js）：
  SSE 事件   {"type": "...", "data": {...}}
    session.step.started   {sessionID, assistantMessageID, agent, model:{id}, started}
    session.text.delta     {sessionID, assistantMessageID, ordinal, delta}
    session.reasoning.delta 同上
    session.tool.called    {sessionID, assistantMessageID, name, input, toolCallId}
    session.tool.success / .error  {sessionID, assistantMessageID, toolCallId}
  权威消息（GET /message）：
    {id, type:"assistant", agent, model:{id}, time:{created,completed?},
     content:[{type:"text",text}|{type:"reasoning",text}|{type:"tool",name,state:{status,input,output}}]}
"""
from __future__ import annotations

import time


def now_ms() -> int:
    return int(time.time() * 1000)


def content_text(content):
    """ACP 的 content 可能是块、块数组或字符串；只取其中的文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        if content.get("type") == "text":
            return content.get("text") or ""
        return ""
    if isinstance(content, list):
        return "".join(content_text(c) for c in content)
    return ""


class AssistantTurn:
    """一轮助手回答：累积增量，产出「实时事件」与「权威消息」。"""

    def __init__(self, session_id, message_id, agent="", model=""):
        self.session_id = session_id
        self.id = message_id
        self.agent = agent
        self.model = model
        self.started = now_ms()
        self.completed = None
        self.aborted = False
        self.text_parts = []
        self.reason_parts = []
        self.tools = []
        self._tool_index = {}
        self._text_seq = 0
        self._reason_seq = 0

    # ---- 增量 → 事件 ----
    def _delta(self, kind, text):
        if not text:
            return None
        if kind == "text":
            seq = self._text_seq
            self._text_seq += 1
            self.text_parts.append(text)
            etype = "session.text.delta"
        else:
            seq = self._reason_seq
            self._reason_seq += 1
            self.reason_parts.append(text)
            etype = "session.reasoning.delta"
        return {"type": etype, "data": {"sessionID": self.session_id,
                "assistantMessageID": self.id, "ordinal": seq, "delta": text}}

    def apply(self, update) -> list:
        kind = (update or {}).get("sessionUpdate")
        if kind == "agent_message_chunk":
            ev = self._delta("text", content_text(update.get("content")))
            return [ev] if ev else []
        if kind == "agent_thought_chunk":
            ev = self._delta("reasoning", content_text(update.get("content")))
            return [ev] if ev else []
        if kind == "tool_call":
            tcid = update.get("toolCallId") or ("t%d" % len(self.tools))
            name = update.get("title") or update.get("name") or update.get("kind") or "tool"
            self._tool_index[tcid] = len(self.tools)
            self.tools.append({"id": tcid, "name": name, "input": update.get("rawInput"),
                               "status": "running", "output": None})
            return [{"type": "session.tool.called", "data": {
                "sessionID": self.session_id, "assistantMessageID": self.id,
                "name": name, "input": update.get("rawInput"), "toolCallId": tcid}}]
        if kind == "tool_call_update":
            tcid = update.get("toolCallId")
            i = self._tool_index.get(tcid)
            if i is None:                       # 没见过这个 id → 当成一次新的工具调用
                merged = dict(update)
                merged["sessionUpdate"] = "tool_call"
                return self.apply(merged)
            if update.get("rawOutput") is not None:
                self.tools[i]["output"] = update.get("rawOutput")
            status = update.get("status")
            if status == "completed":
                self.tools[i]["status"] = "completed"
                return [{"type": "session.tool.success", "data": {
                    "sessionID": self.session_id, "assistantMessageID": self.id, "toolCallId": tcid}}]
            if status in ("failed", "error"):
                self.tools[i]["status"] = "error"
                return [{"type": "session.tool.error", "data": {
                    "sessionID": self.session_id, "assistantMessageID": self.id, "toolCallId": tcid}}]
            return []
        return []

    # ---- 权威消息 ----
    def message(self) -> dict:
        content = []
        if self.reason_parts:
            content.append({"type": "reasoning", "text": "".join(self.reason_parts)})
        if self.text_parts:
            content.append({"type": "text", "text": "".join(self.text_parts)})
        for t in self.tools:
            content.append({"type": "tool", "name": t["name"],
                            "state": {"status": t["status"], "input": t["input"], "output": t["output"]}})
        m = {"id": self.id, "type": "assistant",
             "model": {"id": self.model} if self.model else {},
             "time": {"created": self.started},
             "content": content}
        if self.agent:
            m["agent"] = self.agent
        if self.completed:
            m["time"]["completed"] = self.completed
        return m


# ---------------- elicitation / permission 的字段转换 ----------------

_SCHEMA_TYPES = {"string": "string", "number": "number", "integer": "integer", "boolean": "boolean"}


def schema_to_fields(schema) -> list:
    """把 elicitation 的 requestedSchema 转成前端 form 弹窗的 fields[]。"""
    schema = schema or {}
    props = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    fields = []
    for key, spec in props.items():
        spec = spec if isinstance(spec, dict) else {}
        options = None
        if isinstance(spec.get("enum"), list):
            ftype = "string"
            options = [{"value": str(v), "label": str(v)} for v in spec["enum"]]
        else:
            ftype = _SCHEMA_TYPES.get(spec.get("type"), "string")
        f = {"key": key, "title": spec.get("title") or key,
             "required": key in required, "type": ftype}
        if spec.get("description"):
            f["description"] = spec["description"]
        if options is not None:
            f["options"] = options
        if "default" in spec:
            f["default"] = spec["default"]
        fields.append(f)
    return fields


_KIND_PREF = {"once": ["allow_once"], "always": ["allow_always"],
              "reject": ["reject_once", "reject_always"]}


def pick_option_id(options, decision):
    """把前端的 once/always/reject 映射回 ACP 的 optionId。

    ⚠ 绝不跨方向兜底：用户点「拒绝」时找不到 reject 选项，只能回 None（→ 取消），
      绝不能退而选一个 allow 选项（那会把拒绝做成允许）。
    """
    options = [o for o in (options or []) if isinstance(o, dict)]
    allow = decision in ("once", "always")
    for want in _KIND_PREF.get(decision, []):
        for o in options:
            if o.get("kind") == want:
                return o.get("optionId")
    for o in options:                            # 兜底：只接受同向的 kind
        kind = str(o.get("kind", ""))
        if allow and kind.startswith("allow"):
            return o.get("optionId")
        if (not allow) and kind.startswith("reject"):
            return o.get("optionId")
    return None
