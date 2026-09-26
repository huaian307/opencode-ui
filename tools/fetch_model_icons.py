# -*- coding: utf-8 -*-
r"""下载模型的官方品牌图标 → frontend/assets/models/（一次性，之后前端只用本地文件）

为什么这么做：
  · OpenCode 的 `/api/model`、`/api/provider` **都没有图标字段**（已查证）；
  · 图标源里 **models.dev 就是 OpenCode 的数据源**（`https://models.dev/logos/<providerID>.svg`），
    所以 DeepSeek 用它最正统；其余品牌用 LobeHub / Simple Icons 的官方标。
  · 项目原则是"素材全部本地"（不依赖运行时联网），所以下载一次即可。

映射（与 app.js 的 MODEL_ICONS 对应）：
  deepseek        ← models.dev/logos/deepseek.svg
  xiaomi (mimo)   ← cdn.simpleicons.org/xiaomi
  nvidia (nemotron) ← cdn.simpleicons.org/nvidia
  antgroup (ling) ← unpkg @lobehub/icons-static-svg/icons/antgroup-color.svg
  （muse-spark / big-pickle 没有公开品牌标 → 前端用字母徽章兜底）

用法：python tools\fetch_model_icons.py
"""
from __future__ import annotations

import json
import os
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "frontend", "assets", "models")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

ICONS = {
    "deepseek": "https://models.dev/logos/deepseek.svg",
    "anthropic": "https://models.dev/logos/anthropic.svg",
    "xiaomi": "https://cdn.simpleicons.org/xiaomi",
    "nvidia": "https://cdn.simpleicons.org/nvidia",
    "antgroup": "https://unpkg.com/@lobehub/icons-static-svg/icons/antgroup-color.svg",
}

# 有的源 SVG 不带颜色（currentColor / 默认黑），放进 <img> 会渲染成黑色，夜里看不见。
# 这里按品牌色补上（DeepSeek 蓝 #4D6BFE、Anthropic 陶土色 #D97757、蚂蚁蓝 #1677FF；
# 小米/英伟达自带品牌色）。
BRAND_FILL = {
    "deepseek": "#4D6BFE",
    "anthropic": "#D97757",
    "antgroup": "#1677FF",
}

MANIFEST = os.path.join(OUT, "sources.json")


def paint(name: str, text: str) -> str:
    """把 currentColor 换成品牌色；根 <svg> 没有 fill 的补一个（子路径继承）。"""
    color = BRAND_FILL.get(name)
    if not color:
        return text
    out = text.replace("currentColor", color)
    if 'fill="' not in out.split(">", 1)[0]:
        out = out.replace("<svg", '<svg fill="%s"' % color, 1)
    return out


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    src = {}
    bad = 0
    for name, url in ICONS.items():
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=25) as r:
                data = r.read()
            if b"<svg" not in data[:400].lower():
                print("[BAD] %s: 拿到的不是 SVG" % name)
                bad += 1
                continue
            path = os.path.join(OUT, name + ".svg")
            text = paint(name, data.decode("utf-8", "replace"))
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
            src[name] = {"file": name + ".svg", "source": url, "bytes": len(text),
                         "fill": BRAND_FILL.get(name, "自带")}
            print("[OK] %-9s %6d B  fill=%-8s <- %s" % (name, len(text),
                                                        BRAND_FILL.get(name, "自带"), url))
        except Exception as exc:  # noqa: BLE001
            print("[BAD] %-9s %s" % (name, exc))
            bad += 1

    with open(MANIFEST, "w", encoding="utf-8") as fh:
        json.dump(src, fh, ensure_ascii=False, indent=2)
    print("\n已写入 %s（%d 个图标）" % (OUT, len(src)))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
