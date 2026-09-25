# -*- coding: utf-8 -*-
"""OpenCode 引擎 —— 反代到本机 OpenCode 后台服务。

这里是从 server.py 原样搬过来的逻辑（read_service / /api/* 流式反代 /
/healthz 上游探测），搬运过程中不改任何行为，保证默认引擎下表现一致。
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request

from .base import Engine, HOP_BY_HOP

SERVICE_STATE = os.path.join(os.path.expanduser("~"), ".local", "state", "opencode", "service.json")


def read_service():
    """读取 OpenCode 后台服务的地址与密码。"""
    with open(SERVICE_STATE, "r", encoding="utf-8") as fh:
        svc = json.load(fh)
    url = str(svc["url"]).rstrip("/")
    pw = svc.get("password")
    auth = "Basic " + base64.b64encode(f"opencode:{pw}".encode()).decode() if pw else None
    return url, auth, svc.get("version")


class OpenCodeEngine(Engine):
    id = "opencode"
    label = "OpenCode"
    service_state = SERVICE_STATE

    # ---- 生命周期 ----

    def available(self) -> bool:
        return os.path.isfile(SERVICE_STATE)

    def read_service(self):
        return read_service()

    def status(self) -> dict:
        info = {"id": self.id, "label": self.label, "available": self.available()}
        try:
            url, _, version = read_service()
            info["upstream"] = url
            info["version"] = version
        except Exception as exc:  # noqa: BLE001
            info["error"] = f"{type(exc).__name__}: {exc}"
        return info

    # ---- HTTP ----

    def healthz(self):
        try:
            url, auth, version = read_service()
            req = urllib.request.Request(url + "/api/info", headers={"Authorization": auth or ""})
            with urllib.request.urlopen(req, timeout=5) as resp:
                info = json.loads(resp.read().decode("utf-8"))
            return 200, {"ok": True, "upstream": url, "version": info.get("version", version)}
        except Exception as exc:  # noqa: BLE001
            return 503, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def handle_api(self, handler, method: str) -> None:
        url, auth, _ = read_service()

        length = int(handler.headers.get("Content-Length") or 0)
        body = handler.rfile.read(length) if length else None

        headers = {}
        for key, value in handler.headers.items():
            low = key.lower()
            if low in HOP_BY_HOP or low in ("host", "authorization", "content-length", "accept-encoding"):
                continue
            headers[key] = value
        if auth:
            headers["Authorization"] = auth

        req = urllib.request.Request(url + handler.path, data=body, headers=headers, method=method)
        try:
            upstream = urllib.request.urlopen(req, timeout=None)
        except urllib.error.HTTPError as exc:
            upstream = exc
        except Exception as exc:  # noqa: BLE001
            handler._send_json(502, {"error": f"无法连接 OpenCode 服务: {type(exc).__name__}: {exc}",
                                     "upstream": url})
            return

        with upstream:
            handler.send_response(upstream.status)
            for key, value in upstream.headers.items():
                low = key.lower()
                if low in HOP_BY_HOP or low == "content-length":
                    continue
                handler.send_header(key, value)
            declared = upstream.headers.get("Content-Length")
            if declared:
                handler.send_header("Content-Length", declared)
            else:
                # 流式（含 SSE）：不声明长度，改用连接关闭界定结尾
                handler.send_header("Connection", "close")
                handler.close_connection = True
            handler.end_headers()

            # read1 会在有数据时尽快返回，适合 SSE 实时透传
            while True:
                chunk = upstream.read1(16384)
                if not chunk:
                    break
                handler.wfile.write(chunk)
                handler.wfile.flush()
