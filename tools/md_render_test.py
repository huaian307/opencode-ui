# -*- coding: utf-8 -*-
r"""无头测试：面板的 Markdown 渲染（renderText / renderBlocks / inlineMd）。

用一个固定的 Markdown 样例，在无头 Edge 里跑真实前端代码，把渲染结果抓回来断言：
表格 / 列表 / 标题 / 引用 / 分割线 / 链接 / 加粗 / 斜体 / 删除线 / 代码块都要出标签，
而 `javascript:` 链接必须被拦掉（只留文字）。

前置：面板服务在 8787 运行（打开 frontend/ 即可）。
用法：python tools\md_render_test.py
"""

from __future__ import annotations

import io
import json
import html as htmlmod
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(HERE, "frontend")
PROBE = os.path.join(WEB, "_probe_md.html")
URL = "http://127.0.0.1:8787/_probe_md.html"

sys.path.insert(0, os.path.join(HERE, "tools"))
from inspect_ui import find_edge  # noqa: E402  复用 Edge 定位

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
  window.EventSource = function () { this.close = function () {}; };
  window.__probeErr = [];
  window.addEventListener("error", function (e) { window.__probeErr.push(String(e.message)); });
})();
</script>
"""

SAMPLE = ("# 标题一\\n\\n段落 **加粗** *斜体* `代码` ~~删~~ "
          "[好](https://example.com) [坏](javascript:alert(1))\\n\\n"
          "- 甲\\n- 乙\\n\\n1. 一\\n2. 二\\n\\n> 引用一行\\n\\n"
          "| 名称 | 值 |\\n| --- | --- |\\n| a | 1 |\\n| b | 2 |\\n\\n---\\n\\n"
          "```py\\nprint(\\\"hi\\\")\\n```\\n")

SCRIPT = """<script>
setTimeout(function () {
  var out = {};
  try { out.html = renderText(SAMPLE_MD); }
  catch (e) { out.error = String(e); }
  out.jsErrors = window.__probeErr;
  var pre = document.createElement("pre");
  pre.id = "__probe__";
  pre.textContent = JSON.stringify(out);
  document.body.appendChild(pre);
}, 2500);
</script>
""".replace("SAMPLE_MD", '"' + SAMPLE + '"')


def build_probe():
    src = io.open(os.path.join(WEB, "index.html"), encoding="utf-8").read()
    out = src.replace('<script src="/app.js', STUB + SCRIPT + '<script src="/app.js')
    if out == src:
        raise SystemExit("在 index.html 里找不到 app.js 的 <script> 标签")
    io.open(PROBE, "w", encoding="utf-8", newline="").write(out)


def render(edge):
    tmp = os.environ.get("TEMP", ".")
    dom = os.path.join(tmp, "probe_md_dom.html")
    if os.path.exists(dom):
        os.remove(dom)
    with open(dom, "wb") as fh:
        proc = subprocess.Popen(
            [edge, "--headless=new", "--disable-gpu", "--no-first-run",
             "--no-default-browser-check",
             "--user-data-dir=" + os.path.join(tmp, "edge-mdtest"),
             "--virtual-time-budget=12000", "--dump-dom", URL],
            stdout=fh, stderr=subprocess.DEVNULL)
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
            print("  (Edge 超时，已强杀)")
    return io.open(dom, encoding="utf-8", errors="replace").read()


def main() -> int:
    edge = find_edge()
    build_probe()
    try:
        html = render(edge)
    finally:
        if os.path.exists(PROBE):
            os.remove(PROBE)

    m = re.search(r'<pre id="__probe__">(.*?)</pre>', html, re.S)
    if not m:
        print("[BAD] 没拿到探针结果。DOM 前 300 字符：")
        print(html[:300])
        return 1
    d = json.loads(m.group(1))
    got = htmlmod.unescape(d.get("html") or "")
    print("\n=== Markdown 渲染实测 ===")
    print("  渲染片段:", got[:220].encode("ascii", "replace").decode())

    must = ["<h1>", "<strong>加粗</strong>", "<em>斜体</em>", "<code>代码</code>",
            "<del>删</del>", '<a href="https://example.com"', "<ul>", "<ol>",
            "<blockquote>", "<table>", "<th>名称</th>", "<td>1</td>", "<hr>", "<pre>"]
    missing = [x for x in must if x not in got]
    bad = [x for x in ["javascript:", "| --- |"] if x in got]
    err = d.get("error") or (d.get("jsErrors") or [])

    print("  缺失标签:", missing)
    print("  不该出现:", bad)
    print("  脚本异常:", d.get("error"), " JS错误:", d.get("jsErrors"))
    ok = (not missing) and (not bad) and not err
    print("\n  结论:", "[OK] Markdown 渲染正确（含 XSS 拦截）" if ok else "[BAD] 见上")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
