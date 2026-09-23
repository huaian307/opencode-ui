# -*- coding: utf-8 -*-
r"""真·看界面：用 Edge 无头模式渲染面板页面，把 DOM 实际渲染结果打出来。

为什么需要它：这个项目没有 node，也没有连桌面浏览器，所以「界面到底渲染成什么样」
以前只能靠用户口述。其实 Edge 自己就能无头渲染 —— 这个工具把那条路固定下来：

    1. 由 web/index.html 生成一个探针页 web/_probe.html（结构一模一样）
    2. 探针页里拦掉 /heartbeat、/bye、SSE、/qq/*、/form、/permission
       —— 这样它绝不会惊动守护进程（不会触发「关窗 → 关 OpenCode」），
          也避免长连接把 --virtual-time-budget 卡死
    3. 只保留真请求：/healthz、/api/session（列表）、/api/session/{id}/message
    4. Edge --headless --dump-dom 抓下渲染完的 DOM，解析出结论
    5. 删掉探针页

用法（需要面板服务已在 8787 运行）：
    python tools\inspect_ui.py
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(HERE, "web")
PROBE = os.path.join(WEB, "_probe.html")
URL = "http://127.0.0.1:8787/_probe.html"

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

# 探针注入：拦掉与界面渲染无关、且会干扰无头渲染的请求
STUB = """<script>
(function () {
  var DEAD = ["/heartbeat", "/bye", "/form", "/permission", "/qq/"];
  var raw = window.fetch;
  window.fetch = function (u, o) {
    var s = String(u);
    for (var i = 0; i < DEAD.length; i++) {
      if (s.indexOf(DEAD[i]) >= 0) return Promise.resolve(new Response("{}", { status: 200 }));
    }
    return raw.apply(this, arguments);
  };
  try { navigator.sendBeacon = function () { return true; }; } catch (e) {}
  window.EventSource = function () { this.close = function () {}; };
})();
</script>
"""

SUMMARY = """<script>
window.__probeErr = [];
window.addEventListener("error", function (e) { window.__probeErr.push(String(e.message)); });
window.addEventListener("unhandledrejection", function (e) { window.__probeErr.push("rej: " + String(e.reason)); });
setTimeout(function () {
  var txt = function (el) { return el ? el.textContent.trim() : null; };
  var fmt = function (ms) { try { return new Date(ms).toTimeString().slice(0, 8); } catch (e) { return "?"; } };
  var arr = [], asc = null, err = null;
  try {
    arr = state.messages.map(function (m) { return { t: m.type, c: (m.time || {}).created || 0, id: m.id }; });
    asc = arr.every(function (x, i) { return i === 0 || arr[i - 1].c <= x.c; });
  } catch (e) { err = String(e); }
  var dom = [].slice.call(document.querySelectorAll("#messages .msg")).map(function (m) {
    return {
      who: txt(m.querySelector(".who strong")),
      at: txt(m.querySelector(".who .muted")),
      body: String(txt(m.querySelector(".bubble")) || "").replace(/\\s+/g, " ").slice(0, 20)
    };
  });
  var domAsc = true, prev = null;
  dom.forEach(function (x) {
    if (!x.at) return;
    var t = Date.parse(x.at.replace(/\\//g, "-"));
    if (!isNaN(t)) { if (prev !== null && t < prev) domAsc = false; prev = t; }
  });
  var out = {
    ver: txt(document.getElementById("ui-ver")),
    conn: txt(document.getElementById("conn-text")),
    title: txt(document.getElementById("session-title")),
    banner: txt(document.getElementById("banner")),
    err: err,
    arrayCount: arr.length,
    arrayAscending: asc,
    arrayHead: arr.slice(0, 4).map(function (x) { return x.t + " " + fmt(x.c); }),
    arrayTail: arr.slice(-3).map(function (x) { return x.t + " " + fmt(x.c); }),
    domCount: dom.length,
    domAscending: domAsc,
    domHead: dom.slice(0, 4),
    domTail: dom.slice(-3),
    sidebar: [].slice.call(document.querySelectorAll("#session-list .session .t")).map(function (e) {
      return e.textContent.trim().slice(0, 22);
    }),
    jsErrors: window.__probeErr
  };
  var pre = document.createElement("pre");
  pre.id = "__probe__";
  pre.textContent = JSON.stringify(out);
  document.body.appendChild(pre);
}, 2500);
</script>
"""


def build_probe() -> None:
    src = io.open(os.path.join(WEB, "index.html"), encoding="utf-8").read()
    out = src.replace('<script src="/app.js', STUB + SUMMARY + '<script src="/app.js')
    if out == src:
        raise SystemExit("在 index.html 里找不到 app.js 的 <script> 标签")
    io.open(PROBE, "w", encoding="utf-8", newline="").write(out)


def find_edge() -> str:
    for p in EDGE_CANDIDATES:
        if os.path.isfile(p):
            return p
    raise SystemExit("没找到 msedge.exe")


def render(edge: str) -> str:
    tmp = os.environ.get("TEMP", ".")
    dom = os.path.join(tmp, "probe_dom.html")
    err = os.path.join(tmp, "probe_err.txt")
    for f in (dom, err):
        if os.path.exists(f):
            os.remove(f)
    with open(dom, "wb") as fh_dom, open(err, "wb") as fh_err:
        proc = subprocess.Popen(
            [edge, "--headless=new", "--disable-gpu", "--no-first-run",
             "--no-default-browser-check",
             "--user-data-dir=" + os.path.join(tmp, "edge-inspect"),
             "--virtual-time-budget=15000", "--dump-dom", URL],
            stdout=fh_dom, stderr=fh_err)
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
            print("  （Edge 超时，已强杀）")
    return io.open(dom, encoding="utf-8", errors="replace").read()


def main() -> int:
    edge = find_edge()
    build_probe()
    print("探针页已生成，正在无头渲染…")
    try:
        html = render(edge)
    finally:
        if os.path.exists(PROBE):
            os.remove(PROBE)
            print("探针页已清理")

    m = re.search(r'<pre id="__probe__">(.*?)</pre>', html, re.S)
    if not m:
        print("没拿到探针结果（渲染可能失败）。DOM 前 500 字符：")
        print(html[:500])
        return 1

    d = json.loads(m.group(1))
    print("\n=== 面板实际渲染结果 ===")
    print(f"  前端版本    : {d['ver']}")
    print(f"  连接状态    : {d['conn']}")
    print(f"  顶部横幅    : {d['banner']}")
    print(f"  当前会话    : {d['title']}")
    print(f"  取消息报错  : {d['err']}")
    print(f"  JS 运行错误 : {d['jsErrors']}")
    print(f"\n  侧栏会话（前几项，应是最新在前）:")
    for s in d["sidebar"][:5]:
        print(f"     {s}")
    print(f"\n  state.messages : {d['arrayCount']} 条   升序? {d['arrayAscending']}")
    print(f"     最早: {d['arrayHead']}")
    print(f"     最新: {d['arrayTail']}")
    print(f"\n  DOM 渲染出的 .msg : {d['domCount']} 个   时间升序? {d['domAscending']}")
    print("     DOM 最上面 4 个（应是最旧）:")
    for x in d["domHead"]:
        print(f"        {x['who']} | {x['at']} | {x['body']}")
    print("     DOM 最下面 3 个（应是最新）:")
    for x in d["domTail"]:
        print(f"        {x['who']} | {x['at']} | {x['body']}")

    ok = (not d["err"]) and (not d["jsErrors"]) and d["arrayAscending"] and d["domAscending"]
    print("\n  结论:", "[OK] 顺序与渲染正常" if ok else "[BAD] 有问题，见上面")
    return 0 if ok else 2


def screenshot(edge: str, path: str) -> None:
    """把面板页面渲染成一张图（给人看 / 给模型看）。"""
    build_probe()
    try:
        proc = subprocess.Popen(
            [edge, "--headless=new", "--disable-gpu", "--no-first-run",
             "--no-default-browser-check", "--window-size=1400,920",
             "--user-data-dir=" + os.path.join(os.environ.get("TEMP", "."), "edge-inspect"),
             "--virtual-time-budget=9000", "--screenshot=" + path, URL],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
    finally:
        if os.path.exists(PROBE):
            os.remove(PROBE)
    print("截图:", path, os.path.getsize(path) if os.path.exists(path) else "(失败)")


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--shot":
        screenshot(find_edge(), os.path.abspath(sys.argv[2]))
    else:
        raise SystemExit(main())
