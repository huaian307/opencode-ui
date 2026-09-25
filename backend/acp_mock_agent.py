# -*- coding: utf-8 -*-
"""ACP 假 agent —— 开发/联调用的最小 ACP v1 实现（不是产品运行时）。

用途：在没有任何外部 agent（Codex / Claude / dsh）的情况下，验证我们的 ACP
桥路（分帧、双向请求、事件映射、弹窗）。配合 tools/acp_demo.py 一键起面板。

它按 ACP v1 说 JSONL/JSON-RPC，覆盖一轮完整的 prompt：
  1) agent_message_chunk("你好，")
  2) agent_thought_chunk("思考中…")
  3) tool_call(tc1「运行测试」)
  4) **反向请求** session/request_permission  → 等客户端回复
  5) 回复后：tool_call_update(tc1 completed) + elicitation/create(form)
  6) 表单回复后：agent_message_chunk("完成。") → 结束本轮
对未知的带 id 请求**故意不回**（用于验证客户端超时）。
"""
import json
import sys

STATE = {"sid": "", "prompt_id": None}


def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def respond(rid, result):
    send({"jsonrpc": "2.0", "id": rid, "result": result})


def notify(update):
    send({"jsonrpc": "2.0", "method": "session/update",
          "params": {"sessionId": STATE["sid"], "update": update}})


def chunk(text, kind="agent_message_chunk"):
    notify({"sessionUpdate": kind, "content": {"type": "text", "text": text}})


while True:
    line = sys.stdin.readline()
    if not line:
        break
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except Exception:
        continue

    method = msg.get("method")
    rid = msg.get("id")

    if method == "initialize":
        respond(rid, {"protocolVersion": 1,
                      "agentCapabilities": {"loadSession": True,
                                            "promptCapabilities": {"image": False}},
                      "agentInfo": {"name": "acp-mock", "title": "ACP Mock", "version": "0.0.2"},
                      "authMethods": []})
    elif method == "session/new":
        STATE["sid"] = "sess_mock_1"
        respond(rid, {"sessionId": "sess_mock_1"})
    elif method == "session/prompt":
        STATE["prompt_id"] = rid
        chunk("你好，")
        chunk("思考中…", kind="agent_thought_chunk")
        notify({"sessionUpdate": "tool_call", "toolCallId": "tc1", "name": "run_tests",
                "title": "运行测试", "kind": "execute", "status": "pending",
                "rawInput": {"cmd": "pytest"}})
        send({"jsonrpc": "2.0", "id": 1000, "method": "session/request_permission",
              "params": {"sessionId": STATE["sid"],
                         "toolCall": {"toolCallId": "tc1", "title": "运行测试"},
                         "options": [{"optionId": "allow-once", "name": "允许一次", "kind": "allow_once"},
                                     {"optionId": "reject-once", "name": "拒绝", "kind": "reject_once"}]}})
    elif method == "session/cancel":
        pass
    elif method == "boom":
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": "boom"}})
    elif rid is not None and ("result" in msg or "error" in msg):
        if rid == 1000:                      # 权限答复
            notify({"sessionUpdate": "tool_call_update", "toolCallId": "tc1",
                    "status": "completed", "rawOutput": "3 passed"})
            send({"jsonrpc": "2.0", "id": 2000, "method": "elicitation/create",
                  "params": {"sessionId": STATE["sid"], "mode": "form",
                             "message": "选择重构策略",
                             "requestedSchema": {"type": "object",
                                                 "properties": {"strategy": {
                                                     "type": "string",
                                                     "enum": ["conservative", "balanced", "aggressive"],
                                                     "title": "策略"}},
                                                 "required": ["strategy"]}}})
        elif rid == 2000:                    # 表单答复
            chunk("完成。")
            respond(STATE["prompt_id"], {"stopReason": "end_turn"})
            STATE["prompt_id"] = None
    # 其它带 id 的请求：故意不回
