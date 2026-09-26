# -*- coding: utf-8 -*-
r"""守护进程「要不要依赖 OpenCode」的决策表单测（纯函数，不碰运行中的守护进程）。

背景：`watch.py` 原来只在 **OpenCode 正在运行** 时才开面板窗口 —— 这就是"脱离 OpenCode"
的最后一道门。现在引擎是 `acp` 时面板独立跑（不看/不等/不碰 OpenCode）。
这类"条件组合 + 时序"的逻辑静态检查抓不到，所以把判定抽成纯函数，在这里逐条断言。

用法：python tools\watch_mode_test.py    （输出纯 ASCII，Windows 控制台是 GBK）
"""
from __future__ import annotations

import io
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "backend"))

import watch  # noqa: E402

FAILS: list = []
PASSES = 0


def check(name: str, ok: bool, extra: str = "") -> None:
    global PASSES
    if ok:
        PASSES += 1
        print("  OK   %s" % name)
    else:
        FAILS.append(name)
        safe = str(extra)[:200].encode("ascii", "backslashreplace").decode("ascii")
        print("  FAIL %s  %s" % (name, safe))


def main() -> int:
    print("[1] opencode_required(): engine decides")
    check("engine=acp -> False (panel must not need OpenCode)",
          watch.opencode_required({"active": "acp"}) is False)
    check("engine=acp + opencode uninstalled -> still False",
          watch.opencode_required({"active": "acp",
                                   "engines": [{"id": "opencode", "available": False}]}) is False)
    check("engine=opencode + available -> True",
          watch.opencode_required({"active": "opencode",
                                   "engines": [{"id": "opencode", "available": True}]}) is True)
    check("engine=opencode but NOT installed -> False (don't wait forever)",
          watch.opencode_required({"active": "opencode",
                                   "engines": [{"id": "opencode", "available": False}]}) is False)
    check("no engines field (old backend) + engine=opencode -> True",
          watch.opencode_required({"active": "opencode"}) is True)
    # 探测失败（服务没起来）：回落到"OpenCode 在跑就当它需要"，保持老行为
    orig = watch.opencode_running
    try:
        watch.opencode_running = lambda: True
        check("no engine info + OpenCode running -> True (old behaviour)",
              watch.opencode_required({}) is True)
        watch.opencode_running = lambda: False
        check("no engine info + OpenCode not running -> False",
              watch.opencode_required({}) is False)
    finally:
        watch.opencode_running = orig

    print("[2] should_open_now(): the open-the-window decision table")
    # (required, running, alive, skip_open, closed_at) -> expected
    cases = [
        ((True, True, False, False, None), True, "opencode mode, running, closed -> open"),
        ((True, False, False, False, None), False, "opencode mode, NOT running -> stay closed"),
        ((False, False, False, False, None), True, "ACP mode, no OpenCode -> OPEN (the point)"),
        ((False, True, False, False, None), True, "ACP mode, unrelated OpenCode running -> open"),
        ((False, False, True, False, None), False, "window already open -> don't reopen"),
        ((False, False, False, True, None), False, "user closed it (skip_open) -> don't reopen"),
        ((False, False, False, False, 123.0), False, "inside grace period -> don't reopen"),
        ((True, True, True, False, None), False, "opencode mode + already open -> don't reopen"),
    ]
    for args, want, why in cases:
        got = watch.should_open_now(*args)
        check("open? %s" % why, got is want, "args=%r got=%r want=%r" % (args, got, want))

    print("[3] should_kill_on_close() / should_minimize_opencode()")
    orig_kill = watch.KILL_OPENCODE
    try:
        watch.KILL_OPENCODE = True
        check("opencode mode + kill pref -> kill OpenCode on close",
              watch.should_kill_on_close(True) is True)
        check("ACP mode -> never kill on close", watch.should_kill_on_close(False) is False)
        check("★browser still alive (page throttled) -> DO NOT kill",
              watch.should_kill_on_close(True, browser_alive=True) is False)
        check("browser really gone -> kill",
              watch.should_kill_on_close(True, browser_alive=False) is True)
        watch.KILL_OPENCODE = False
        check("kill pref off -> no kill", watch.should_kill_on_close(True) is False)
    finally:
        watch.KILL_OPENCODE = orig_kill
    check("ACP mode -> never minimize OpenCode window",
          watch.should_minimize_opencode(False) is False)
    check("opencode mode -> minimize it", watch.should_minimize_opencode(True) is True)
    check("panel_browser_alive() exists (heartbeat fallback)", callable(watch.panel_browser_alive))

    print("[4] wait_upstream() signature stays compatible + is engine-based")
    import inspect                                     # noqa: PLC0415
    sig = list(inspect.signature(watch.wait_upstream).parameters)
    check("wait_upstream(timeout, required)", sig[:2] == ["timeout", "required"], str(sig))
    src = io.open(os.path.join(HERE, "backend", "watch.py"), encoding="utf-8").read()
    check("upstream check asks OUR /healthz (engine-aware, not service.json)",
          "/healthz" in src and "service.json" not in src)
    check("main loop uses should_open_now()",
          "should_open_now(required, running, fresh, skip_open, closed_at)" in src)
    check("grace-end kill is gated",
          "if should_kill_on_close(required, browser_alive=False):" in src)
    check("★grace-end double-checks the browser (heartbeat alone is not enough)",
          "panel_browser_alive(proc, adopted_pid)" in src)

    print("[5] launcher plan(): same shortcut, engine-aware")
    import importlib.util                              # noqa: PLC0415
    spec = importlib.util.spec_from_file_location(
        "launch_panel_mod", os.path.join(HERE, "launchers", "launch_opencode.py"))
    lm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lm)                        # 只 import，不会执行 main()
    orig_eng = lm.active_engine
    orig_run = lm.opencode_running
    try:
        lm.opencode_running = lambda: False             # "OpenCode 没在跑"
        lm.active_engine = lambda: "acp"
        p = lm.plan()
        check("engine=acp -> panel_only, no OpenCode, no heal",
              p["panel_only"] is True and p["start_opencode"] is False and p["heal"] is False,
              str(p))
        lm.active_engine = lambda: "opencode"
        p2 = lm.plan()
        check("engine=opencode -> heal + start OpenCode (old behaviour)",
              p2["panel_only"] is False and p2["heal"] is True, str(p2))
        check("engine=opencode -> start_opencode reflects whether it is running",
              p2["start_opencode"] is bool(p2["exe"]), str(p2))
        lm.opencode_running = lambda: True
        check("engine=opencode + already running -> don't start it again",
              lm.plan()["start_opencode"] is False)
    finally:
        lm.active_engine = orig_eng
        lm.opencode_running = orig_run

    print("[6] 端口配置（安装版避开开发版：17887/17888/17990）")
    import importlib                          # noqa: PLC0415
    import panel_port                         # noqa: PLC0415
    import tempfile                           # noqa: PLC0415
    tmpdir = tempfile.mkdtemp(prefix="ports_")
    orig_file = panel_port.PANEL_FILE
    try:
        panel_port.PANEL_FILE = os.path.join(tmpdir, "_panel.json")
        check("默认（没有 _panel.json）走 8787/8788/8790",
              tuple(panel_port.read_ports()[k] for k in ("port", "lock", "music")) == (8787, 8788, 8790),
              str(panel_port.read_ports()))
        wrote = panel_port.write_ports({"port": 17887, "lock": 17888, "music": 17990})
        got = panel_port.read_ports()
        check("写了之后读回安装版端口",
              wrote and tuple(got[k] for k in ("port", "lock", "music")) == (17887, 17888, 17990), str(got))
        check("已存在就不覆盖（重装/手改都保得住）",
              panel_port.write_ports({"port": 9999}) is False and panel_port.read_ports()["port"] == 17887)
        # 坏文件 → 回默认
        with io.open(panel_port.PANEL_FILE, "w", encoding="utf-8") as fh:
            fh.write("{ not json")
        check("坏文件回默认", panel_port.read_ports()["port"] == 8787, str(panel_port.read_ports()))
    finally:
        panel_port.PANEL_FILE = orig_file
    # watch.py 在 import 时读端口/命令行；用 reload 验证优先级
    argv_saved = list(sys.argv)
    try:
        sys.argv = [sys.argv[0]]
        w2 = importlib.reload(watch)
        check("watch 默认端口 = 8787/8788", (w2.PORT, w2.LOCK_PORT) == (8787, 8788),
              "%s/%s" % (w2.PORT, w2.LOCK_PORT))
        sys.argv = [sys.argv[0], "--port", "17887", "--lock-port", "17888", "--music-port", "17990"]
        w3 = importlib.reload(watch)
        check("watch 支持 --port/--lock-port/--music-port",
              (w3.PORT, w3.LOCK_PORT, w3.MUSIC_PORT) == (17887, 17888, 17990),
              "%s/%s/%s" % (w3.PORT, w3.LOCK_PORT, w3.MUSIC_PORT))
        check("URL 跟着端口走", w3.URL == "http://127.0.0.1:17887", w3.URL)
        src_w = io.open(os.path.join(HERE, "backend", "watch.py"), encoding="utf-8").read()
        check("ensure_server 把端口传给 server.py",
              '"--port", str(PORT), "--music-port", str(MUSIC_PORT)' in src_w)
    finally:
        sys.argv = argv_saved
        importlib.reload(watch)

    print("\ntotal %d, failed %d" % (PASSES + len(FAILS), len(FAILS)))
    if FAILS:
        print("failed list:")
        for f in FAILS:
            print("   -", f)
        return 1
    print("result: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
