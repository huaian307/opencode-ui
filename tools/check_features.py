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
    ("音乐", "刷新后恢复歌曲状态", r"function restoreMusicState"),
    ("音乐", "状态落盘 localStorage", r"localStorage\.setItem\(MUS_KEY"),
    ("音乐", "自动连播 / 随机下一首", r"async function musicNext"),
    ("音乐", "随机播放开关", r'\$\("p-shuffle"\)'),
    ("音乐", "自动连播开关", r'\$\("p-loop"\)'),
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
    ("消息", "空会话首屏卡片", r"function heroHtml\("),
    ("外观", "★首屏主图/贴纸/文案可自定义（SET.heroL1）", r"heroL1"),
    ("外观", "自定义素材走 /appearance/img 代理", r"/appearance/img\?k="),
    ("外观", "选图走原生对话框 /pick/image", r'api\("/pick/image"'),
    ("外观", "★素材清单接口 /appearance", r'api\("/appearance"\)'),
    ("外观", "换图后重画首屏与消息区", r"function refreshAppearanceArt"),
    ("消息", "Markdown 块级渲染(表格/列表/引用)", r"function renderBlocks"),
    ("消息", "Markdown 行内渲染(链接/加粗/斜体)", r"function inlineMd"),
    ("消息", "GFM 表格识别", r"MD_SEP"),
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
    ("设置", "任务栏开关", r'\$\("cfg-taskbar"\)'),
    ("设置", "任务栏开关默认开启", r"taskbar: true"),
    ("设置", "任务栏开关同步给守护", r'fetch\("/panel/taskbar"'),
    ("设置", "两侧音频条开关", r'\$\("cfg-spec-sides"\)'),
    ("设置", "两侧音频条默认开启", r"spectrumSides: true"),
    ("设置", "两侧音频条绘制", r"function paintSides"),
    ("设置", "两侧音频条高度可调", r'\$\("cfg-spec-h"\)'),
    ("设置", "两侧音频条高度默认值", r"spectrumSidesH: 100"),
    ("设置", "两侧音频条高度写进 CSS 变量", r'"--spec-side-h"'),
    ("设置", "两侧音频条起伏可调", r'\$\("cfg-spec-gain"\)'),
    ("设置", "两侧音频条起伏默认值", r"spectrumSidesGain: 120"),
    ("设置", "两侧音频条起伏写进 CSS 变量", r'"--spec-side-gain"'),
    ("设置", "两侧音频条起伏按倍数绘制", r"\* gain"),
    ("设置", "左上角名字设置", r'\$\("cfg-brand"\)'),
    ("设置", "左上角小标签设置", r'\$\("cfg-brand-sub"\)'),
    ("设置", "名字默认值", r'BRAND_DEFAULT = "絵梨衣"'),
    ("设置", "小标签默认值", r"BRAND_SUB_DEFAULT"),
    ("设置", "会话详情展开", r"function sessionDetailHtml"),
    ("设置", "会话详情状态记账", r"detailOpen"),
    ("设置", "会话详情按钮", r'class="info"'),
    ("设置", "顶栏不再显示会话 ID", r'NOT:\$\("session-meta"\)\.textContent = state\.current\.id'),
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
    ("壁纸", "动态壁纸目录可改", r'\$\("cfg-wall-root"\)'),
    ("壁纸", "读取动态壁纸目录", r"async function loadWallpaperRoot"),
    ("壁纸", "保存动态壁纸目录", r"async function saveWallpaperRoot"),
    ("壁纸", "点按钮选择文件夹", r"async function pickWallpaperRoot"),
    ("壁纸", "目录接口 /live/root", r'api\("/live/root"'),
    ("壁纸", "目录选择接口 /live/root/pick", r'api\("/live/root/pick"'),
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
    ("音乐", "已移除音乐条歌单按钮", r"NOT:p-lib"),
    ("音乐", "歌词位置按采样时间补时", r"MUSIC\.posAt = Number\(\(MUSIC\.state \|\| \{\}\)\.ts\)"),
    ("音乐", "推荐歌单封面", r"/music/cover\?p="),
    ("音乐", "选完歌单清封面缓存", r'api\("/music/cover/clear"'),
    ("音乐", "歌曲行封面(条内)", r'coverHtml\(it, "it-cover"\)'),
    ("音乐", "歌曲行封面(主页)", r'coverHtml\(it, "s-cover"\)'),
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
    ("模型", "默认模型文案中性化", r"跟随引擎默认"),
    ("模型", "未知费用不谎报免费", r"费用未知"),
    ("设置", "首次设置向导", r"async function openSetup"),
    ("设置", "首次设置记账键", r'opencode-ui\.setup\.v1'),
    ("设置", "首次设置应用引擎/agent", r"async function setupNext"),
    # ---- 微信（截图+OCR 只读识别；回复必须二次确认）----
    # ---- 龙族视觉 + 歌词翻译 ----
    ("龙族", "歌词翻译开关", r'\$\("cfg-zh"\)'),
    ("龙族", "翻译开关默认开启", r"zh: true"),
    ("龙族", "按开关决定是否翻译", r"&tr=\$\{SET\.zh \? 1 : 0\}"),
    ("龙族", "译文渲染成小字行", r'<span class="zh">'),
    ("龙族", "关翻译时隐藏译文", r"no-lyrics-zh"),
    # ---- 对话引擎（后端 /engine；S4）----
    ("引擎", "引擎状态接口", r'api\("/engine/status"\)'),
    ("引擎", "引擎切换接口", r'api\("/engine",\s*\{\s*method:\s*"POST"'),
    ("引擎", "切换后重连 SSE(防旧引擎事件)", r'if \(typeof connectEvents === "function"\) connectEvents\(\);'),
    ("引擎", "无引擎时优雅禁用下拉", r'sel\.disabled = true'),
    # ---- 默认 agent / 模型中性化（provider 有入口、引擎能看出可用性）----
    ("中性化", "★引擎下拉标出「未就绪」", r"（未就绪）"),
    ("中性化", "不可用引擎禁选", r'value="\$\{esc\(id\)\}"\$\{ok \? "" : " disabled"\}'),
    ("中性化", "记住引擎自述（label/available）", r"ENGINE\.engines"),
    ("中性化", "向导自动落到可用引擎并说明", r"已先帮你选到可用的"),
    ("中性化", "卡片显示 provider 徽章", r'class="badge">\$\{esc\(prov\)\}'),
    ("中性化", "provider 推断提示（≈）", r"≈\$\{esc\(guess\)\}"),
    ("中性化", "★agent 编辑弹窗（含 provider）", r"function editAgentCommand"),
    ("中性化", "provider 一键自动推断", r"async function guessAgentProvider"),
    ("中性化", "保存 agent 编辑（名称/命令/目录/provider）", r"async function saveAgentEdit"),
    ("中性化", "命令解析（引号包住含空格路径）", r"function parseCmd"),
    ("中性化", "ACP 说明文案中立", r"选择要驱动的 ACP agent"),
    ("中性化", "★模型 id 的 provider 前缀也用来找图标", r'id\.split\("/"\)\[0\]'),
    ("中性化", "已删除点名 Codex/dsh 的旧文案", r"NOT:Codex / dsh / …"),
    ("可选组件", "★前端显示「音乐服务未安装」", r"async function musicComponent"),
    ("可选组件", "未装时搜索直接拦下（不撞 502）", r"MUS\.installed === false"),
    ("API Key", "★设置里能填/清模型 Key", r"async function saveApiKey"),
    ("API Key", "按 provider 猜变量名", r"KEY_NAME_BY_PROVIDER"),
    ("API Key", "打开设置就拉（只拿掩码）", r'loadApiKeyRow\("cfg"\)'),
    # ---- 智能体 / 模式切换 ----
    ("模式", "★模式清单接口 /api/agent", r'api\("/api/agent"\)'),
    ("模式", "★切换模式：POST /api/session/{id}/agent", r"/agent`,\s*\{\s*method:\s*\"POST\""),
    ("模式", "★过滤内部 agent（hidden）", r"!a\.hidden"),
    ("模式", "新会话默认模式落盘", r"MODE_KEY"),
    ("模式", "新建会话后补一次应用", r"async function ensureSessionMode"),
    ("模式", "顶栏按钮文案跟随会话", r"function renderModeButton"),
    ("模式", "跟随引擎默认（清本地默认）", r"mode-default"),
]

# index.html 也要有：龙族标记 / 弹窗 / 控件（这些不是 app.js 里的东西）
HTML_CHECKS = [
    ("龙族", "半朽世界树校徽(SVG symbol)", r'id="mk-tree"'),
    ("龙族", "弹窗标题引用校徽", r'<use href="#mk-tree"'),
    ("龙族", "设置里的歌词翻译开关", r'id="cfg-zh"'),
    ("设置", "设置里的任务栏开关", r'id="cfg-taskbar"'),
    ("设置", "设置里的首次设置按钮", r'id="cfg-setup"'),
    ("设置", "首次设置弹窗", r'id="setup"'),
    ("设置", "首次设置引擎下拉", r'id="setup-engine"'),
    ("中性化", "agent 编辑弹窗", r'id="agent-edit"'),
    ("中性化", "编辑弹窗里的 provider 输入", r'id="ae-provider"'),
    ("API Key", "设置里的 Key 输入框", r'id="cfg-key-value"'),
    ("模式", "★顶栏模式按钮", r'id="btn-agent"'),
    ("模式", "模式弹窗", r'id="mode-list"'),
    ("模式", "弹窗标题（智能体/模式）", r'id="mode-title"'),
    ("API Key", "向导里的 Key 行", r'id="setup-key-row"'),
    ("API Key", "★变量名改成只读标签（不再两个输入框）", r'id="cfg-key-var"'),
    ("API Key", "向导里的变量名只读标签", r'id="setup-key-var"'),
    ("外观", "头像/素材选择行（data-img）", r'data-img="brand"'),
    ("外观", "空会话主图可换", r'data-img="hero"'),
    ("外观", "空会话贴纸可换", r'data-img="sticker2"'),
    ("外观", "空会话文案可改", r'id="cfg-hero-l1"'),
    ("外观", "空会话署名可改", r'id="cfg-hero-credit"'),
    ("中性化", "provider 自动推断按钮", r'id="ae-guess"'),
    ("中性化", "编辑按钮文案（不再只改命令）", r'编辑当前 agent…'),
    ("设置", "首次设置 agent 下拉", r'id="setup-agent"'),
    ("设置", "首次设置模型下拉", r'id="setup-model"'),
    ("设置", "设置里的左上角名字输入框", r'id="cfg-brand"'),
    ("设置", "设置里的小标签输入框", r'id="cfg-brand-sub"'),
    ("设置", "侧栏小标签元素", r'id="brand-sub"'),
    ("设置", "设置里的两侧音频条开关", r'id="cfg-spec-sides"'),
    ("设置", "两侧音频条高度调节行", r'id="cfg-spec-sides-h"'),
    ("设置", "两侧音频条高度滑块", r'id="cfg-spec-h"'),
    ("设置", "两侧音频条起伏调节行", r'id="cfg-spec-sides-gain"'),
    ("设置", "两侧音频条起伏滑块", r'id="cfg-spec-gain"'),
    ("设置", "会话区左侧音频条", r'id="spec-left"'),
    ("设置", "会话区右侧音频条", r'id="spec-right"'),
    ("设置", "两侧音频条主体外壳", r'id="body-stage"'),
    ("模型", "顶栏模型按钮", r'id="btn-model"'),
    ("模型", "模型选择弹窗", r'id="mdl"'),
    ("壁纸", "已移除分类筛选按钮", r"NOT:wp-fb"),
    ("壁纸", "已移除壁纸音乐按钮", r"NOT:btn-wemusic"),
    ("壁纸", "已移除 scene 筛选按钮", r"NOT:scene-we"),
    ("壁纸", "已移除 we-shot 图层", r"NOT:we-shot"),
    ("壁纸", "设置里动态壁纸目录输入框", r'id="cfg-wall-root"'),
    ("壁纸", "选择壁纸目录按钮", r'id="cfg-wall-root-pick"'),
    ("壁纸", "壁纸目录恢复自动按钮", r'id="cfg-wall-root-auto"'),
    ("音乐", "音乐音频元素", r'id="mus-audio"'),
    ("音乐", "播放条折叠箭头", r'id="p-fold"'),
    ("音乐", "设置里贴Cookie", r'id="cfg-ck-qq"'),
    ("音乐", "播放条平台切换", r'data-p="qq"'),
    ("音乐", "播放条随机按钮", r'id="p-shuffle"'),
    ("音乐", "播放条连播按钮", r'id="p-loop"'),
    ("音乐", "播放条搜索框", r'id="p-q"'),
    ("音乐", "进度条元素", r'id="p-seek"'),
    ("音乐", "已移除音乐条歌单按钮", r"NOT:p-lib"),
    ("引擎", "设置里的引擎下拉", r'id="cfg-engine"'),
    ("引擎", "引擎状态说明文字", r'id="cfg-engine-note"'),
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
    ("音乐", "歌单封面样式", r"\.mus-pl \.pl-cover"),
    ("音乐", "条内歌曲封面样式", r"\.p-results \.it \.it-cover"),
    ("音乐", "主页歌曲封面样式", r"\.mus-song \.s-cover"),
    ("壁纸", "目录用等宽字体(反斜杠不显成￥)", r"#cfg-wall-root \{ font-family: var\(--mono\)"),
    ("引擎", "引擎下拉样式", r"#cfg-engine \{"),
    ("主题", "白昼面板也用毛玻璃", r'(?s)\[data-theme="hiru"\] #sidebar.*?backdrop-filter: blur\(10px\)'),
    ("主题", "数学符号字体兜底", r"Cambria Math"),
    ("设置", "★宽行布局（防止 label 被挤成竖排）", r"\.cfg-row\.wide\s*\{"),
    ("设置", "密码框也有样式（不是白框）", r'input\[type="password"\]'),
    ("模式", "★顶栏模式按钮放开 34px 宽度（踩坑 #24）", r"#topbar \.actions #btn-agent"),
]

# ---- ACP 后端（backend/engines/acp/）----
# 以前这个体检只看前端，后端改坏了没有任何网；ACP 那条线尤其需要
# （事件映射 / 记忆 / 导入都是"看不见"的行为）。
# Inno 安装脚本（注释是 `;`，单独扫）
ISS_CHECKS = [
    ("安装包", "★组件 payload 用 DirExists 判断（FileExists 对目录为假！）", r"DirExists\("),
    ("安装包", "音乐/频谱组件声明", r"Name: \"music\""),
    ("安装包", "随包 agent 组件声明（node/codex/claude）", r"Name: \"agent_node\""),
    ("安装包", "Claude 适配器组件", r"Name: \"agent_claude\""),
    ("安装包", "勾了适配器必带 node（Inno 代码守卫）", r"WizardSelectComponents\('agent_node'\)"),
    ("安装包", "per-user 安装（不弹 UAC）", r"PrivilegesRequired=lowest"),
    ("安装包", "卸载清 codex-home（运行时 state）", r'"\{app\}\\agents\\codex\\codex-home"'),
    ("安装包", "卸载先按端口停进程（含安装版端口）", r"17888,17887,17990"),
    ("安装包", "装完跑 init_state", r'init_state\.py"" --app-dir'),
]

PY_CHECKS = [
    ("ACP事件", "★工具输出转字符串（不显示 [object Object]）", r"def normalize_output"),
    ("ACP事件", "工具运行中输出 → session.tool.progress", r"session\.tool\.progress"),
    ("ACP事件", "Codex 增量输出(_meta.terminal_output_delta)", r"def meta_progress"),
    ("ACP事件", "计划 → session.plan.update", r"session\.plan\.update"),
    ("ACP事件", "上下文占用 → session.usage.update", r"session\.usage\.update"),
    ("ACP事件", "上下文压缩摘要进思考", r"compaction_summary_chunk"),
    ("ACP事件", "★tool_call 自带 completed 要发 success", r'st == "completed"'),
    ("ACP事件", "客户端宣告 terminal_output_delta", r'"terminal_output_delta": True'),
    ("ACP事件", "计划绝不塞进 content（前端只会当原始 JSON）", r'NOT:"type": "plan"'),
    ("ACP权限", "★「始终允许」记忆落盘", r'_acp_always\.json'),
    ("ACP权限", "记住规则的字段", r"def _remember_always"),
    ("ACP权限", "命中规则自动放行", r"def _match_always"),
    ("ACP权限", "自动放行有事件可观测", r'"permission\.auto"'),
    ("ACP权限", "清记忆接口", r"def forget_always"),
    ("ACP会话", "★agent 侧会话列表 session/list", r'"session/list"'),
    ("ACP会话", "导入已有会话", r"def import_remote"),
    ("ACP会话", "空对象宣告(list: {})也算支持", r'"list" in caps'),
    ("ACP会话", "agent 起的真名可自动改名", r"def _auto_title_ok"),
    ("ACP会话", "用户改过的标题不被顶掉", r"def _on_session_info"),
    ("ACP会话", "模式被 agent 改时同步", r"current_mode_update"),
    ("ACP会话", "配置项权威回执", r"config_option_update"),
    ("ACP会话", "★prompt 结果的 usage 累加进 tokens", r"cachedReadTokens"),
    ("ACP会话", "stopReason → 结果", r"max_turn_requests"),
    ("ACP接口", "拉 agent 侧会话的路由", r'"/api/session/remote"'),
    ("ACP接口", "导入路由", r'"/api/session/import"'),
    ("ACP接口", "记忆查询/清除路由", r'"/api/permission/always"'),
    ("ACP接口", "模式清单（与 OpenCode 对齐）", r'"/api/agent"'),
    ("ACP接口", "消息翻页 cursor", r"cursor"),
    ("ACP进程", "★子进程强制 UTF-8（GBK 会把中文变乱码）", r"PYTHONIOENCODING"),
    ("ACP中性", "★provider 可推断（env/命令/config.toml）", r"def guess_provider"),
    ("ACP中性", "空值的 env 不算判断依据", r"if str\(v or \"\"\)\.strip\(\)"),
    ("ACP中性", "读 Codex config.toml 猜 provider", r"_provider_from_codex_home"),
    ("ACP中性", "bootstrap 不预设默认 agent", r'reg = \{"active": "", "agents": agents\}'),
    ("ACP中性", "bootstrap 只挑「真的可用」当 active", r"if status_of\(a, det\)\[\"available\"\]"),
    ("ACP中性", "add_agent 自动补 provider", r'a\["provider"\] = guess_provider\(a\)'),
    ("ACP中性", "autofill 只补空的 provider", r"filledProviders"),
    ("ACP中性", "public 给出 providerGuess 建议", r"providerGuess"),
    ("ACP中性", "Codex 默认项不再塞 OPENAI_API_KEY", r'NOT:"env": \{"OPENAI_API_KEY"'),
    ("ACP中性", "引擎可用性自述 describe()", r"def describe"),
    ("ACP中性", "/engine/status 带上引擎自述", r'"engines": engines\.describe\(\)'),
    ("ACP中性", "编辑弹窗的 provider 推断接口", r'"guess-provider"'),
    ("接口", "★版本号锚定 app.js（别取到 style.css 的 ?v=）", r"app\\\.js\\\?v="),
    # ---- 脱离 OpenCode（面板独立运行）----
    ("脱离OC", "★守护进程按引擎判定是否依赖 OpenCode", r"def opencode_required"),
    ("脱离OC", "★开窗决策抽成纯函数（可单测）", r"def should_open_now"),
    ("脱离OC", "ACP 模式关窗不杀任何东西", r"def should_kill_on_close"),
    ("脱离OC", "ACP 模式不最小化 OpenCode 窗口", r"def should_minimize_opencode"),
    ("脱离OC", "主循环用 should_open_now（不再只看 running）", r"should_open_now\(required, running, fresh, skip_open, closed_at\)"),
    ("脱离OC", "引擎中途可切换（每轮重算 required）", r"required = opencode_required\(\)\s+#"),
    ("脱离OC", "★默认引擎不再写死（第一个可用的）", r"def first_available_engine"),
    ("脱离OC", "没有状态文件时用 first_available_engine", r"return first_available_engine\(\) or DEFAULT_ENGINE"),
    ("脱离OC", "★启动器按引擎决定要不要起 OpenCode", r"def plan"),
    ("脱离OC", "启动器 acp 模式跳过自愈", r'"heal": not panel_only'),
    # ---- 子命令式 ACP（OpenCode 自带 `acp` 子命令）----
    ("子命令ACP", "★识别「CLI 的 acp 子命令」这种形态", r"def find_subcommand_acp"),
    ("子命令ACP", "已登记的规格含 opencode-cli", r'"opencode-cli\.exe"'),
    ("子命令ACP", "OpenCode ACP 的信息（能力/能力说明）", r"OpenCode · ACP（子命令）"),
    ("子命令ACP", "★扫描时按真实路径去重（junction 不会扫出两条）", r"os\.path\.realpath\(p\)"),
    ("子命令ACP", "push 用真实路径做去重键", r"norm\.append\(os\.path\.realpath\(sx\)\.lower\(\)\)"),
    ("子命令ACP", "选文件夹也能认出子命令式", r"hit = find_subcommand_acp\(\[d\]\)"),
    ("子命令ACP", "候选复用规格里的名字", r"_spec_for_command\(cmd\)"),
    ("子命令ACP", "扫到候选里也推送子命令式", r'push\(spec\["label"\], cmd,'),
    ("子命令ACP", "★基线 mode 该 agent 没有就跳过（不报 -32602）", r"这个 agent 没有（可用："),
    ("子命令ACP", "★每个 agent 自己的 mode 覆盖共享基线", r'b2\["mode"\] = a\["mode"\]'),
    ("子命令ACP", "改模型以 agent 回执为准（不许盲信）", r"没被 agent 接受"),
    ("子命令ACP", "public 回 mode 字段", r'"mode": str\(a\.get\("mode"\) or ""\)'),
    ("子命令ACP", "patch 白名单含 mode", r'"provider", "mode"'),
    # ---- 可选组件（安装包：音乐服务 / 音频频谱）----
    ("可选组件", "★按组件找解释器（venv 或 site-packages）", r"def _component_python"),
    ("可选组件", "安装包形态：自带解释器 + PYTHONPATH", r"def _component_env"),
    ("可选组件", "音乐服务没装就静默跳过", r"if not MUSIC_SVC_PY:"),
    ("可选组件", "频谱没装就静默跳过", r"if not AUDIO_PY:"),
    ("可选组件", "★缺音乐服务时给人话（503 + reason）", r'"error": music_component\(\)\["reason"\]'),
    ("可选组件", "组件自述接口 /music/component", r'"/music/component"'),
    # ---- 安装包（packaging/）----
    # ---- 安装包（packaging/ + tools/selftest.py，见 PY 扫描列表）----
    ("安装包", "★payload 收集（核心/组件/agent）", r"def stage_agent"),
    ("安装包", "可选组件按 site-packages 打包", r"def stage_components"),
    ("安装包", "★安装时修 ._pth（嵌入式解释器隔离模式）", r"def fix_pth"),
    ("安装包", "定位用户目录不依赖 LOCALAPPDATA", r"def env_path"),
    ("安装包", "★组件目录也写进 ._pth（._pth 会忽略 PYTHONPATH）", r'\.\.\\\\runtime\\\\site-packages'),
    ("安装包", "★安装版端口与开发版隔离（_panel.json）", r"def read_ports"),
    ("安装包", "watch 支持 --port/--lock-port", r"--lock-port"),
    ("安装包", "ensure_server 把端口传给 server.py", r'"--music-port", str\(MUSIC_PORT\)'),
    ("安装包", "server.py 支持 --music-port", r'"--music-port"'),
    # ---- Claude Code（与 codex 同构）----
    ("Claude", "★与 codex 同构的两个内置档案", r"def _claude_defaults"),
    ("Claude", "claude-node（node 适配器）", r'"claude-node"'),
    ("Claude", "claude-client（本机客户端）", r'"claude-client"'),
    ("Claude", "★检测 claude / claude-code-acp / 本地适配器", r'"claudeAdapter"'),
    ("Claude", "适配器入口查找（通用 _adapter_entry）", r"def _adapter_entry"),
    ("Claude", "★依赖判定：无登录态时提示（适配器自带 CLI，不误报缺 CLI）", r"Claude Code 登录（或 ANTHROPIC_API_KEY）"),
    ("Claude", "claude-client 才要求 CLI/适配器", r"claude-code-acp / Claude Code CLI"),
    ("Claude", "登录态探测（~/.claude 或 ANTHROPIC_API_KEY）", r"def _has_claude_auth"),
    ("Claude", "★包内 agents 目录也算探测位置（安装版状态才正确）", r'os\.path\.join\(ROOT_DIR, "agents", "node"'),
    ("Claude", "★provider 关键词整词命中（路径名不误导）", r"def _kw_hit"),
    ("Claude", "命令只看文件名（绝对路径的目录名不算线索）", r"os\.path\.basename\(sx\)"),
    ("安装包", "★save_registry 留一份 .bak（误写可捞）", r'AGENTS_FILE \+ "\.bak"'),
    ("安装包", "★autofill 支持 save=False（探针别写盘）", r"def autofill\(reg: dict = None, save: bool = True\)"),
    ("Claude", "autofill 把 npx 升级成本地适配器", r'"@zed-industries/claude-code-acp"'),
    ("Claude", "候选扫描含 Claude Code 两类", r"Claude Code · 本机客户端"),
    ("Claude", "★便携 node 定点查找（大目录会吃光 _walk_find 上限）", r"def _glob_node_exe"),
    ("Claude", "按要求不探测 VS Code 缓存", r"NOT:agent-host"),
    ("Claude", "provider 由推断而来（claude → anthropic 线索）", r'"claude", "ANTHROPIC_API_KEY"'),
    ("安装包", "★初始化不写任何密钥", r"API Key 不随包"),
    ("安装包", "随包 agent 用 app 相对路径", r'"agents", "node", "node\.exe"'),
    ("安装包", "自检脚本（装完能跑）", r"opencode-ui 自检"),
    ("安装包", "自检也检查 Claude 适配器 + 登录态", r"Claude 登录态"),
    ("安装包", "★自检把「ACP server 超时」当正常（它会等 stdin）", r"except subprocess\.TimeoutExpired"),
    # ---- 模型 API Key（装完怎么填）----
    ("API Key", "★基线环境变量写入（密钥只落本机）", r"def update_baseline_env"),
    ("API Key", "★回给界面的只有掩码", r"def mask_secret"),
    ("API Key", "改 Key 后重启 ACP 子进程", r"def set_baseline_env"),
    ("API Key", "基线视图（掩码 + 变量名）", r"def baseline_view"),
    ("API Key", "接口 /engine/acp/baseline", r'"/engine/acp/baseline"'),
    # ---- 自定义外观（头像 / 空会话素材）----
    ("外观", "★素材状态文件（只记本机路径）", r"APPEARANCE_FILE"),
    ("外观", "允许的图片扩展名白名单", r"IMAGE_EXTS"),
    ("外观", "★接口 /appearance（读/写/清除）", r'path == "/appearance"'),
    ("外观", "素材走 /appearance/img 代理（file:// 会被浏览器拦）", r'path == "/appearance/img"'),
    ("外观", "原生选图框脚本", r"pick_image\.ps1"),
    ("外观", "选图走共用 _pick_path（BOM 要剥掉）", r'lstrip\("\\ufeff"\)'),
    # ---- 引擎：opencode 回归（安装版曾把 opencode 判成未就绪）----
    ("引擎", "★多候选找 service.json（缺 USERPROFILE 也能找到）", r"def service_states"),
    ("引擎", "用户目录多线索推导", r"def _home_dirs"),
    ("引擎", "★装了 CLI 也算可用（不把用户挡在门外）", r"def cli_path"),
    ("引擎", "NOT:写死 expanduser 当唯一来源",
     r"NOT:SERVICE_STATE = os\.path\.join\(os\.path\.expanduser"),
    ("引擎", "★自愈：注册表缺 opencode-acp 就自动补", r"def ensure_opencode_agent"),
    ("引擎", "★server.py 每次启动都调一次自愈", r"acp_agents\.ensure_opencode_agent\(\)"),
    ("引擎", "自愈加进来的 agent 标成内置（不给删）", r'a\["builtin"\] = True'),
    ("安装包", "★init_state 用后端探测器找 opencode-acp", r"ag\.find_subcommand_acp\(\)"),
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

    # ACP 后端也纳入体检（以前只看前端，后端改坏了没有任何网）
    py = []
    py_files = [os.path.join("backend", "engines", "acp", n)
                for n in ("map_events.py", "service.py", "engine.py", "process.py",
                          "client.py", "agents.py")]
    py_files += [os.path.join("backend", "engines", "__init__.py"),
                 os.path.join("backend", "engines", "opencode.py"),
                 os.path.join("backend", "server.py"),
                 os.path.join("backend", "watch.py"),
                 os.path.join("backend", "panel_port.py"),
                 os.path.join("launchers", "launch_opencode.py"),
                 os.path.join("packaging", "build.py"),
                 os.path.join("packaging", "init_state.py"),
                 os.path.join("tools", "selftest.py")]
    for rel in py_files:
        p = os.path.join(HERE, rel)
        src = io.open(p, encoding="utf-8").read()
        src = re.sub(r'"""(?:.|\n)*?"""', " ", src)       # 去掉文档字符串
        src = re.sub(r"(?m)^\s*#.*$", " ", src)           # 去掉整行注释
        py.append("/* %s */\n%s" % (rel.replace("\\", "/"), src))
    py = "\n".join(py)
    # Inno 脚本单独扫（它的注释是 `;`，用 # 会误删 #define 行）
    iss = io.open(os.path.join(HERE, "packaging", "opencode-ui.iss"), encoding="utf-8").read()
    iss = re.sub(r"(?m)^\s*;.*$", " ", iss)

    fails: list = []
    total = 0
    for suffix, checks, text in (("", CHECKS, js),
                                (" · index.html", HTML_CHECKS, html),
                                (" · style.css", CSS_CHECKS, css),
                                 (" · acp", PY_CHECKS, py),
                                 (" · iss", ISS_CHECKS, iss)):
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
