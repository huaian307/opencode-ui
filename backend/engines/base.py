# -*- coding: utf-8 -*-
"""引擎接口与通用常量。"""
from __future__ import annotations

# 逐跳首部，不能端到端转发（原来定义在 server.py；现在 server 与引擎共用这一份）
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
}


class Engine:
    """一个可替换的 agent 后端。

    实现者直接往 HTTP handler 上写响应，语义必须和改造前的 OpenCode 反代一致
    —— 这样前端完全不需要知道背后换了谁。
    """

    id = "base"
    label = "Base"

    # ---- 生命周期 ----

    def available(self) -> bool:
        """引擎依赖是否就绪（例如 OpenCode 的状态文件是否存在）。"""
        return False

    def read_service(self):
        """返回 (url, auth_header_or_None, version)。未实现则抛异常。"""
        raise NotImplementedError

    # ---- HTTP ----

    def status(self) -> dict:
        """给 GET /engine/status 用的自述。"""
        return {"id": self.id, "label": self.label, "available": self.available()}

    def healthz(self):
        """返回 (http_code, body_dict)。server.py 会再补上 ui 版本号。"""
        raise NotImplementedError

    def handle_api(self, handler, method: str) -> None:
        """实现 /api/* 的语义（handler 是 BaseHTTPRequestHandler）。"""
        raise NotImplementedError
