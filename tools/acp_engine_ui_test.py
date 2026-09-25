# -*- coding: utf-8 -*-
r"""无头交互测试：真的在浏览器里切换「对话引擎」，并验证切换后重连了 SSE。

背景：S4 修过一个 bug —— 切引擎后没重新 connectEvents()，于是 REST 走新引擎、
事件流却还挂在旧引擎上。静态检查抓不到这种"时序/副作用" bug，所以这里真跑一遍：

  1. 起一个独立的、带新后端的服务（固定端口，不影响线上 8787）；
  2. 由 frontend/index.html 生成探针页：拦掉会长挂的请求（心跳/SSE/qq/form/permission），
     保留真实的 /engine/status、/engine、/api/session；
  3. 用计数器替换 window.EventSource —— 每 new 一次就 +1；
  4. 探针里开设置 → 读下拉 → setEngine("acp") → setEngine("opencode")，把结果塞进 <pre>；
  5. Edge 无头 --dump-dom 抓回来解析；
  6. 断言：下拉含两个引擎、切换后 active 正确、**每次切换 EventSource 计数都 +1**、无 JS 报错。

用法：python tools\acp_engine_ui_test.py
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import threading

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(HERE, "backend")
WEB = os.path.join(HERE, "frontend")
STATE_DIR = os.path.join(HERE, "runtime", "state")
PROBE = os.path.join(WEB, "_probe_engine.html")
MOCK = os.path.join(BACKEND, "acp_mock_agent.py")
ACF_FILE = os.path.join(STATE_DIR, "_acp.json")
ENGINE_FILE = os.path.join(STATE_DIR, "_engine.json")

PORT = 8801
URL = "http://127.0.0.1:%d/_probe_engine.html" % PORT

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

# 拦掉会长挂 / 会惊动守护进程的请求；EventSource 换成计数器（用来数"重连了几次"）
STUB = """<script>
(function () {
  var DEAD = ["/heartbeat", "/bye", "/form", "/permission", "/qq/", "/live/"];
  var raw = window.fetch;
  window.fetch = function (u, o) {
    var s = String(u);
    for (var i = 0; i < DEAD.length; i++) {
      if (s.indexOf(DEAD[i]) >= 0) return Promise.resolve(new Response("{}", { status: 200 }));
    }
    return raw.apply(this, arguments);
  };
  try { navigator.sendBeacon = function () { return true; }; } catch (e) {}
  window.__esCount = 0;
  window.EventSource = function () { window.__esCount++; this.close = function () {}; };
  window.__probeErr = [];
  window.addEventListener("error", function (e) { window.__probeErr.push(String(e.message)); });
  window.addEventListener("unhandledrejection", function (e) { window.__probeErr.push("rej: " + String(e.reason)); });
})();
</script>
"""

TEST = """<script>
setTimeout(async function () {
  var out = {};
  var note = function () { var n = document.getElementById("cfg-engine-note"); return n ? n.textContent : null; };
  try {
    var cfg = document.getElementById("btn-cfg");
    if (cfg) cfg.click();
    await new Promise(function (r) { setTimeout(r, 1200); });
    var sel = document.getElementById("cfg-engine");
    out.options = [].slice.call(sel.options).map(function (o) { return o.value; });
    out.value = sel.value;
    out.disabled = sel.disabled;
    out.note0 = note();
    var before = window.__esCount;
    await setEngine("acp");
    out.acpActive = ENGINE.active;
    out.acpEsDelta = window.__esCount - before;
    out.acpNote = note();
    var before2 = window.__esCount;
    await setEngine("opencode");
    out.openActive = ENGINE.active;
    out.openEsDelta = window.__esCount - before2;
    out.title = (document.getElementById("session-title") || {}).textContent;
    out.msgCount = (state.messages || []).length;
  } catch (e) { out.error = String(e); }
  out.jsErrors = window.__probeErr;
  var pre = document.createElement("pre");
  pre.id = "__probe__";
  pre.textContent = JSON.stringify(out);
  document.body.appendChild(pre);
}, 3500);
</script>
"""


def build_probe():
    src = io.open(os.path.join(WEB, "index.html"), encoding="utf-8").read()
    out = src.replace('<script src="/app.js', STUB + TEST + '<script src="/app.js')
    if out == src:
        raise SystemExit("在 index.html 里找不到 app.js 的 <script> 标签")
    io.open(PROBE, "w", encoding="utf-8", newline="").write(out)


def find_edge():
    for p in EDGE_CANDIDATES:
        if os.path.isfile(p):
            return p
    raise SystemExit("没找到 msedge.exe")


def render(edge):
    tmp = os.environ.get("TEMP", ".")
    dom = os.path.join(tmp, "probe_engine_dom.html")
    if os.path.exists(dom):
        os.remove(dom)
    with open(dom, "wb") as fh:
        proc = subprocess.Popen(
            [edge, "--headless=new", "--disable-gpu", "--no-first-run",
             "--no-default-browser-check",
             "--user-data-dir=" + os.path.join(tmp, "edge-acptest"),
             "--virtual-time-budget=20000", "--dump-dom", URL],
            stdout=fh, stderr=subprocess.DEVNULL)
        try:
            proc.wait(timeout=90)
        except subprocess.TimeoutExpired:
            proc.kill()
            print("  (Edge 超时，已强杀)")
    return io.open(dom, encoding="utf-8", errors="replace").read()


def main() -> int:
    edge = find_edge()

    os.environ.pop("OPENCODE_UI_ENGINE", None)          # 确保按 _engine.json 走
    sys.path.insert(0, BACKEND)
    import server  # noqa: E402

    os.makedirs(STATE_DIR, exist_ok=True)
    old_engine = None
    try:
        old_engine = io.open(ENGINE_FILE, encoding="utf-8").read()
    except OSError:
        pass
    io.open(ENGINE_FILE, "w", encoding="utf-8").write('{"engine": "opencode"}')
    io.open(ACF_FILE, "w", encoding="utf-8").write(
        json.dumps({"command": [sys.executable, MOCK], "cwd": HERE}, ensure_ascii=False))

    build_probe()
    httpd = server.Server(("127.0.0.1", PORT), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print("测试服务已起，正在无头渲染并切换引擎…")
    try:
        html = render(edge)
    finally:
        httpd.shutdown()
        if os.path.exists(PROBE):
            os.remove(PROBE)
        try:
            eng = server.engines.get_engine("acp")
            if hasattr(eng, "shutdown"):
                eng.shutdown()
        except Exception:  # noqa: BLE001
            pass
        # 还原引擎状态
        if old_engine is not None:
            io.open(ENGINE_FILE, "w", encoding="utf-8").write(old_engine)
        else:
            io.open(ENGINE_FILE, "w", encoding="utf-8").write('{"engine": "opencode"}')
        try:
            os.remove(ACF_FILE)
        except OSError:
            pass

    m = re.search(r'<pre id="__probe__">(.*?)</pre>', html, re.S)
    if not m:
        print("[BAD] 没拿到探针结果（渲染失败）。DOM 前 300 字符：")
        print(html[:300])
        return 1

    d = json.loads(m.group(1))
    print("\n=== 引擎切换（无头实测）===")
    print("  下拉选项    :", d.get("options"))
    print("  初始值/禁用 :", d.get("value"), "/", d.get("disabled"))
    print("  初始说明    :", d.get("note0"))
    print("  切到 acp    : active=%s  SSE重连=%s  说明=%s"
          % (d.get("acpActive"), d.get("acpEsDelta"), d.get("acpNote")))
    print("  切回 opencode: active=%s  SSE重连=%s" % (d.get("openActive"), d.get("openEsDelta")))
    print("  切换后标题/消息数:", d.get("title"), "/", d.get("msgCount"))
    print("  脚本异常    :", d.get("error"), " JS错误:", d.get("jsErrors"))

    opts = d.get("options") or []
    ok = (
        "acp" in opts and "opencode" in opts
        and d.get("disabled") is False
        and d.get("acpActive") == "acp" and (d.get("acpEsDelta") or 0) >= 1
        and d.get("openActive") == "opencode" and (d.get("openEsDelta") or 0) >= 1
        and not d.get("error") and not d.get("jsErrors")
    )
    print("\n  结论:", "[OK] 引擎切换与 SSE 重连都正常" if ok else "[BAD] 有问题，见上面")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
