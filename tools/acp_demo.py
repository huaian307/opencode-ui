# -*- coding: utf-8 -*-
"""一键联调：用假 agent 起一个独立的 ACP 面板服务。

它做三件事，且**不影响线上面板**：
  1. 把 runtime/state/_acp.json 指向 backend/acp_mock_agent.py（退出时恢复原样）；
  2. 用环境变量 OPENCODE_UI_ENGINE=acp 覆盖当前引擎（不写全局 _engine.json）；
  3. 在指定端口起一个 server.Handler（不启动音乐/频谱采集进程）。

用法：
    python tools/acp_demo.py              # 默认 http://127.0.0.1:8799
    python tools/acp_demo.py --port 9001
然后浏览器打开那个地址，新建会话、发一句话，就能看到假 agent 的
文本/思考/工具/权限弹窗/表单弹窗。Ctrl+C 结束。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import webbrowser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
STATE_DIR = os.path.join(ROOT, "runtime", "state")
CONFIG_FILE = os.path.join(STATE_DIR, "_acp.json")
MOCK = os.path.join(BACKEND, "acp_mock_agent.py")


def backup_config():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def restore_config(old):
    try:
        if old is None:
            if os.path.exists(CONFIG_FILE):
                os.remove(CONFIG_FILE)
        else:
            with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
                fh.write(old)
    except OSError:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="用假 agent 起一个独立的 ACP 面板服务")
    parser.add_argument("--port", type=int, default=8799)
    parser.add_argument("--no-open", action="store_true", help="不要自动打开浏览器")
    args = parser.parse_args()

    if not os.path.isfile(MOCK):
        print("[!] 找不到假 agent：%s" % MOCK, file=sys.stderr)
        return 1

    os.makedirs(STATE_DIR, exist_ok=True)
    old = backup_config()
    with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
        json.dump({"command": [sys.executable, MOCK], "cwd": ROOT}, fh, ensure_ascii=False, indent=2)

    os.environ["OPENCODE_UI_ENGINE"] = "acp"     # 只影响本进程
    sys.path.insert(0, BACKEND)
    import server  # noqa: E402

    httpd = server.Server(("127.0.0.1", args.port), server.Handler)
    url = "http://127.0.0.1:%d" % args.port
    print("ACP 联调面板已启动")
    print("  地址  %s" % url)
    print("  引擎  acp（假 agent：%s）" % os.path.relpath(MOCK, ROOT))
    print("  说明  这是独立进程，不影响线上 8787；Ctrl+C 结束")
    if not args.no_open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        try:
            eng = server.engines.get_engine("acp")
            shutdown = getattr(eng, "shutdown", None)
            if callable(shutdown):
                shutdown()
        except Exception:  # noqa: BLE001
            pass
        httpd.server_close()
        restore_config(old)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
