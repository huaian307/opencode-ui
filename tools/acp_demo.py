# -*- coding: utf-8 -*-
"""一键联调：用假 agent 起一个独立的 ACP 面板服务。

它做三件事，且**不影响线上面板**：
  1. 在**本进程内**把 agent 注册表换成假 agent（`agents.load_registry` 猴补丁，内存里生效
     —— 不写 `runtime/state/_acp_agents.json`、也不改 `_acp.json`；
     ⚠ 以前这里是改 `_acp.json`，可引擎早已改成读注册表 `_acp_agents.json`，
     结果假 agent 根本没被用上、这个 demo 会真连你的 Codex）；
  2. 用环境变量 OPENCODE_UI_ENGINE=acp 覆盖当前引擎（不写全局 _engine.json）；
  3. 在指定端口起一个 server.Handler（不启动音乐/频谱采集进程）。

用法：
    python tools/acp_demo.py              # 默认 http://127.0.0.1:8799
    python tools/acp_demo.py --port 9001
然后浏览器打开那个地址，新建会话、发一句话，就能看到假 agent 的
文本/思考/工具/计划/工具进度/**权限弹窗（含「始终允许」）**/表单弹窗。
Ctrl+C 结束。
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import threading
import webbrowser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
MOCK = os.path.join(BACKEND, "acp_mock_agent.py")


def main() -> int:
    parser = argparse.ArgumentParser(description="用假 agent 起一个独立的 ACP 面板服务")
    parser.add_argument("--port", type=int, default=8799)
    parser.add_argument("--no-open", action="store_true", help="不要自动打开浏览器")
    args = parser.parse_args()

    if not os.path.isfile(MOCK):
        print("[!] 找不到假 agent：%s" % MOCK, file=sys.stderr)
        return 1

    os.environ["OPENCODE_UI_ENGINE"] = "acp"     # 只影响本进程
    sys.path.insert(0, BACKEND)
    import server  # noqa: E402
    from engines.acp import agents  # noqa: E402
    from engines.acp import service as svc_mod  # noqa: E402

    # ⚠ 会话/记忆**落盘也指到临时目录**：以前只换了 agent 注册表，在 demo 里建会话会写进
    #   真实的 runtime/state/_acp_sessions.json（和真实 agent 的历史混在一起，实测踩到过）。
    demo_state = tempfile.mkdtemp(prefix="acp_demo_state_")
    svc_mod.STATE_DIR = demo_state
    svc_mod._ALWAYS_FILE = os.path.join(demo_state, "_acp_always.json")

    reg = {"active": "mock",
           "baseline": {"cwd": ROOT, "env": {}, "mode": "read-only"},
           "agents": [{"id": "mock", "label": "ACP 假 agent（联调用）",
                       "command": [sys.executable, MOCK], "cwd": ROOT, "env": {},
                       "provider": "mock", "note": "由 tools/acp_demo.py 内存注入"}]}
    agents.load_registry = lambda: reg            # 内存里换掉，不落盘
    agents.save_registry = lambda r: None

    httpd = server.Server(("127.0.0.1", args.port), server.Handler)
    url = "http://127.0.0.1:%d" % args.port
    print("ACP 联调面板已启动")
    print("  地址  %s" % url)
    print("  引擎  acp（假 agent：%s）" % os.path.relpath(MOCK, ROOT))
    print("  说明  独立进程 + 内存注入，不影响线上 8787、不改任何配置文件；Ctrl+C 结束")
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
