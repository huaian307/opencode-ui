# -*- coding: utf-8 -*-
r"""功能体检：把 AGENTS.md 里列过的能力逐条当断言，检查 frontend/app.js 是否都还在。

背景：2026-09-22 整体重写 app.js 时丢了「消息重排」等细节，
      这类回归 jsbalance（只看括号）和 check_ids（只看 DOM id）都抓不到。
      以后大改前端后跑一次这个，能立刻看出少了什么。

用法：python tools\check_features.py
"""

from __future__ import annotations

import io
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (分组, 说明, 正则) —— 正则必须能在 app.js 里命中
CHECKS = [
    # ---- 后端接口（少一个就意味着某块功能没接上）----
    ("接口", "会话列表", r"/api/session\?limit=\d+&order=desc"),
    ("接口", "历史消息", r"/message\?limit=\d+"),
    ("接口", "发消息", r"/prompt`"),
    ("接口", "中断生成", r"/interrupt`"),
    ("接口", "新建会话", r'api\("/api/session",\s*\{\s*method:\s*"POST"'),
    ("接口", "删除会话", r'method:\s*"DELETE"'),
    ("接口", "会话改名", r'method:\s*"PATCH"'),
    ("接口", "实时事件 SSE", r'new EventSource\("/api/event"\)'),
    ("接口", "权限查询/回复", r"/permission`"),
    ("接口", "健康检查", r'api\("/healthz"\)'),
    ("接口", "心跳", r'fetch\("/heartbeat"'),
    ("接口", "关窗信号", r'"/bye"'),
    # ---- QQ音乐 ----
    ("音乐", "状态轮询", r'api\("/qq/state"\)'),
    ("音乐", "播放控制", r'api\("/qq/control"'),
    ("音乐", "音量", r'api\("/qq/volume"'),
    ("音乐", "歌词", r"/qq/lyrics\?title="),
    ("音乐", "条内搜索=弹窗搜索", r'musicSearch\(\$\("p-q"\)\.value\)'),
    ("音乐", "词按钮联动歌词", r'\$\("p-lyric-btn"\)\.addEventListener'),
    ("音乐", "进度条可拖动", r'\$\("p-seek"\)'),
    ("音乐", "已移除启动QQ客户端按钮", r"NOT:p-app"),
    ("音乐", "真频谱", r'api\("/qq/spectrum"\)'),
    ("音乐", "真频谱开关 class", r'classList\.toggle\("real"'),
    ("音乐", "纯听歌模式同步", r"syncLyricsVisibility"),
    ("音乐", "歌词只显示三行", r'line\(idx - 1, "prev"\)|class="prev"'),
    # ---- 消息渲染（重写过的高危区）----
    ("消息", "★按时间升序重排", r"\.sort\(\(a,\s*b\)\s*=>"),
    ("消息", "用户消息读顶层 text", r"typeof m\.text === \"string\""),
    ("消息", "助手消息读 content 数组", r"Array\.isArray\(m\.content\)"),
    ("消息", "工具调用渲染", r"function renderToolPart"),
    ("消息", "思考过程渲染", r"function renderReasoningPart"),
    ("消息", "附件渲染", r"function renderAttachments"),
    ("消息", "未知结构兜底展示", r"原始数据"),
    ("消息", "内容指纹（避免重建整块）", r"function signatureOf"),
    ("消息", "重渲染时保住展开状态", r"details\[data-k\]"),
    ("消息", "空会话首屏卡片", r"HERO_HTML"),
    # ---- 实时增量 ----
    ("实时", "文本增量", r"session\.text\.delta"),
    ("实时", "思考增量", r"session\.reasoning\.delta"),
    ("实时", "步骤开始(agent/model)", r"session\.step\.started"),
    ("实时", "工具调用事件", r"session\.tool\.called"),
    ("实时", "终态白名单", r"const TERMINAL ="),
    ("实时", "打字机循环", r"requestAnimationFrame"),
    ("实时", "本地乐观回显", r"state\.pending\.push"),
    # ---- 交互 ----
    ("交互", "发送（Enter）", r'key === "Enter" && !e\.shiftKey'),
    ("交互", "Esc 中断/清附件", r'key === "Escape"'),
    ("交互", "Ctrl+B 收起侧栏", r'e\.key === "b"'),
    ("交互", "跟随滚动策略", r'state\.follow = false'),
    ("交互", "输入框自动长高", r"scrollHeight"),
    ("交互", "粘贴图片", r'"paste"'),
    ("交互", "拖拽文件", r'"drop"'),
    ("交互", "附件压缩", r"function shrinkImage"),
    ("交互", "左上角名字可编辑", r"plaintext-only"),
    ("交互", "主题记忆", r"localStorage\.setItem\(THEME_KEY"),
    ("交互", "侧栏状态记忆", r"localStorage\.setItem\(SIDEBAR_KEY"),
    ("交互", "落樱+涟漪", r"petal-unit"),
    ("交互", "会话删除按钮", r'class="del" data-del='),
    ("交互", "删除前二次确认", r'confirm\(`删除会话'),
    ("交互", "删除当前会话后清空界面", r"async function deleteSession"),
    ("交互", "首问自动命名", r"async function autoNameSession"),
    ("交互", "已命名的会话不覆盖", r"function shouldAutoName"),
    ("交互", "标题截断+压平空白", r"function titleFromQuestion"),
    # ---- 弹窗（agent 会被静默卡住的那两类）----
    ("弹窗", "表单轮询", r"api\(`/api/session/\$\{sid\}/form`"),
    ("弹窗", "权限轮询", r"/permission`"),
    ("弹窗", "表单提交", r"/form/\$\{encodeURIComponent\(form\.id\)\}/reply"),
    ("弹窗", "权限回复", r"/permission/\$\{encodeURIComponent\(perm\.id\)\}/reply"),
    ("弹窗", "选项记账(防轮询清空)", r"ASK\.picks\[key\] ="),
    ("弹窗", "同项不重建 DOM", r"if \(sig === ASK\.sig"),
    ("弹窗", "必填校验", r"还差："),
    # ---- 动态壁纸（Wallpaper Engine 原片）----
    ("壁纸", "创意工坊壁纸清单", r'api\("/live/wallpapers"\)'),
    ("壁纸", "有原片才切 has-live", r'classList\.add\("has-live"\)'),
    ("壁纸", "主题/侧栏变化自动同步", r"new MutationObserver\(sync\)"),
    ("壁纸", "加载失败退回 CSS 动态层", r'addEventListener\("error"'),
    ("壁纸", "清单定期重拉(新订阅免刷新)", r"5 \* 60 \* 1000"),
    ("壁纸", "播放速度可调", r"let liveRate = "),
    ("壁纸", "倍速赋值有越界保护", r"const setRate = \(r\)"),
    # ---- 设置面板 ----
    ("设置", "顶栏设置按钮", r'\$\("btn-cfg"\)'),
    ("设置", "背景亮度按主题可调", r'--bg-dim-hiru'),
    ("设置", "背景毛玻璃可调", r'--bg-blur'),
    ("设置", "设置落盘", r'localStorage\.setItem\(SET_KEY'),
    ("设置", "动态壁纸开关", r"!SET\.live"),
    ("设置", "恢复默认", r"Object\.assign\(SET, SET_DEFAULT\)"),
    ("壁纸", "换片先藏视频防黑屏", r'classList\.remove\("ready"\)'),
    ("壁纸", "出第一帧才显示", r'classList\.add\("ready"\)'),
    ("壁纸", "展开＝暂停视频当背景", r"ensureVisibleFrame\(t\)"),
    ("壁纸", "片头黑场只量亮度(不截图)", r"const frameMean = "),
    ("壁纸", "已删掉冻结帧层", r"NOT:has-freeze"),
    ("壁纸", "已不再抓帧", r"NOT:function grabFrame"),
    ("壁纸", "已不再把画面转成图片", r"NOT:toDataURL"),
    ("壁纸", "壁纸列表接口", r'api\("/live/list"\)'),
    ("壁纸", "选择落盘保存", r'api\("/live/pick"'),
    ("壁纸", "已移除分类筛选按钮", r"NOT:wp-fb"),
    ("壁纸", "壁纸搜索框", r'\$\("wp-q"\)'),
    ("壁纸", "设置里选壁纸", r'\$\("cfg-wall"\)'),
    ("壁纸", "已移除按 kind 过滤", r"NOT:WALL\.kind"),
    ("壁纸", "已移除 scene 静帧分支", r"NOT:urls\.still"),
    ("壁纸", "已移除 scene 解包标注", r"NOT:解包·"),
    ("壁纸", "已移除 /live/we 调用", r"NOT:/live/we"),
    ("壁纸", "已移除壁纸自带音乐开关", r"NOT:btn-wemusic"),
    ("壁纸", "已移除 weMusic 设置", r"NOT:weMusic"),
    ("壁纸", "已移除 we-shot 图层", r"NOT:we-shot"),
    ("壁纸", "选壁纸后立刻换片", r"if \(liveReload\) await liveReload\(\)"),
    ("壁纸", "收起时缓慢启动", r"ramp\(liveRate, LIVE_RAMP_IN\)"),
    ("壁纸", "展开时缓慢停止", r"ramp\(LIVE_START_RATE, LIVE_RAMP_OUT\)"),
    ("壁纸", "出帧后也会确认不是黑场", r'addEventListener\("loadeddata"'),
    # ---- 音乐（面板内搜歌/放歌 · 网易云非官方接口）----
    ("音乐", "折叠箭头常驻", r'\$\("p-fold"\)'),
    ("音乐", "搜索接口", r"/music/search\?p="),
    ("音乐", "播放流代理", r"/music/stream\?p="),
    ("音乐", "登录态查询", r'api\("/music/status"\)'),
    ("音乐", "贴 Cookie 登录", r'api\("/music/cookie"'),
    ("音乐", "条内结果渲染", r"function renderBarResults"),
    ("音乐", "平台切换(网易云/QQ)", r"\.wp-pb"),
    # ---- 模型切换（顶栏「模型」按钮）----
    ("模型", "顶栏模型按钮", r'\$\("btn-model"\)'),
    ("模型", "模型清单接口", r'api\("/api/model"\)'),
    ("模型", "默认模型接口", r'api\("/api/model/default"\)'),
    ("模型", "切换写入当前会话", r'/model`,'),
    ("模型", "变体(variant)可点", r"ref\.variant = b\.dataset\.var"),
    ("模型", "新会话默认模型落盘", r"localStorage\.setItem\(MODEL_KEY"),
    ("模型", "新建会话带上模型", r"payload\.model = def"),
    ("模型", "按 provider 分组", r"mdl-group"),
    ("模型", "免费/付费标注", r"免费"),
    ("模型", "当前模型高亮", r"mdl-it\$\{on"),
    ("模型", "搜索过滤", r"\.toLowerCase\(\)\.includes\(q\)"),
    ("模型", "按钮跟随当前会话", r"updateModelBtn\(\)"),
    ("模型", "官方图标映射表", r"const MODEL_ICONS ="),
    ("模型", "图标解析顺序", r"function iconKeyFor"),
    ("模型", "本地图标目录", r'MODEL_ICON_DIR = "/assets/models/"'),
    ("模型", "顶栏用图标代替名字", r"b\.innerHTML = modelIconHtml"),
    ("模型", "无品牌标用字母徽章", r"micon-mono"),
    # ---- 微信（截图+OCR 只读识别；回复必须二次确认）----
    # ---- 龙族视觉 + 歌词翻译 ----
    ("龙族", "歌词翻译开关", r'\$\("cfg-zh"\)'),
    ("龙族", "翻译开关默认开启", r"zh: true"),
    ("龙族", "按开关决定是否翻译", r"&tr=\$\{SET\.zh \? 1 : 0\}"),
    ("龙族", "译文渲染成小字行", r'<span class="zh">'),
    ("龙族", "关翻译时隐藏译文", r"no-lyrics-zh"),
]

# index.html 也要有：龙族标记 / 弹窗 / 控件（这些不是 app.js 里的东西）
HTML_CHECKS = [
    ("龙族", "半朽世界树校徽(SVG symbol)", r'id="mk-tree"'),
    ("龙族", "弹窗标题引用校徽", r'<use href="#mk-tree"'),
    ("龙族", "设置里的歌词翻译开关", r'id="cfg-zh"'),
    ("模型", "顶栏模型按钮", r'id="btn-model"'),
    ("模型", "模型选择弹窗", r'id="mdl"'),
    ("壁纸", "已移除分类筛选按钮", r"NOT:wp-fb"),
    ("壁纸", "已移除壁纸音乐按钮", r"NOT:btn-wemusic"),
    ("壁纸", "已移除 scene 筛选按钮", r"NOT:scene-we"),
    ("壁纸", "已移除 we-shot 图层", r"NOT:we-shot"),
    ("音乐", "音乐音频元素", r'id="mus-audio"'),
    ("音乐", "播放条折叠箭头", r'id="p-fold"'),
    ("音乐", "设置里贴Cookie", r'id="cfg-ck-qq"'),
    ("音乐", "播放条平台切换", r'data-p="qq"'),
    ("音乐", "播放条搜索框", r'id="p-q"'),
    ("音乐", "进度条元素", r'id="p-seek"'),
]

# style.css 里的背景层（纯 CSS 的改动 app.js/index.html 都断言不了，这里补上）
CSS_CHECKS = [
    ("壁纸", "展开时视频层也套毛玻璃", r'html\[data-sidebar="open"\] #live-bg'),
    ("壁纸", "有动态层时静态壁纸让位", r"html\.has-live #static-bg \{ display: none; \}"),
    ("壁纸", "静态背景两层交叉淡入", r"#static-bg \.sb\.on"),
    ("壁纸", "静态背景按主题互斥", r'#static-bg \.sb'),
    ("壁纸", "模糊时图层撑出视口(切换不跳)", r"html\.bg-blurred #live-bg \{ inset: -3vmax; \}"),
    ("壁纸", "已删掉冻结帧样式", r"NOT:bg-freeze"),
    ("壁纸", "已删掉 has-freeze 规则", r"NOT:has-freeze"),
    ("壁纸", "已移除 scene 图层样式", r"NOT:lb-we"),
    ("壁纸", "已移除壁纸音乐样式", r"NOT:btn-wemusic"),
    ("音乐", "音频控件样式", r"\.mus-audio"),
]


def _hit(pattern: str, text: str) -> bool:
    """以 `NOT:` 开头 = 必须**不存在**（用来断言"旧的实现确实删干净了"）。"""
    if pattern.startswith("NOT:"):
        return re.search(pattern[4:], text) is None
    return re.search(pattern, text) is not None


def main() -> int:
    app = io.open(os.path.join(HERE, "frontend", "app.js"), encoding="utf-8").read()
    # 去掉注释，避免"注释里写了但代码没了"的假通过
    js = re.sub(r"/\*.*?\*/", " ", app, flags=re.S)
    js = re.sub(r"(?<!:)//[^\n]*", " ", js)
    html = io.open(os.path.join(HERE, "frontend", "index.html"), encoding="utf-8").read()
    css = re.sub(r"/\*.*?\*/", " ",
                 io.open(os.path.join(HERE, "frontend", "style.css"), encoding="utf-8").read(), flags=re.S)

    fails: list = []
    total = 0
    for suffix, checks, text in (("", CHECKS, js),
                                (" · index.html", HTML_CHECKS, html),
                                (" · style.css", CSS_CHECKS, css)):
        group = None
        for g, name, pattern in checks:
            if g != group:
                print(f"\n[{g}{suffix}]")
                group = g
            total += 1
            ok = _hit(pattern, text)
            print(f"  {'OK  ' if ok else 'MISS'} {name}")
            if not ok:
                fails.append(f"{g}/{name}")

    print(f"\n共 {total} 项，缺失 {len(fails)} 项")
    if fails:
        print("缺失清单：")
        for f in fails:
            print("   -", f)
        return 1
    print("result: PASS")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
