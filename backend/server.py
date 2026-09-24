#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""opencode-ui —— 零依赖本地代理 + 静态服务器

它做三件事：
  1. 托管 frontend/ 下的静态前端；
  2. 把 /api/* 反向代理到本机 OpenCode 后台服务，自动附加 HTTP Basic 鉴权
     （浏览器无法直接带鉴权跨源访问，这一步把鉴权和同源问题一次性解决）；
  3. 以流式方式透传响应，保证 /api/event (SSE) 的实时性，不缓冲。

依赖：仅 Python 标准库。运行：
    python backend/server.py                 # 默认 http://127.0.0.1:8787
    python backend/server.py --port 9000
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import glob
import json
import mimetypes
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs, quote

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FRONTEND_DIR = os.path.join(ROOT, "frontend")
TOOLS_DIR = os.path.join(ROOT, "tools")
RESOURCES_DIR = os.path.join(ROOT, "resources")
RUNTIME_DIR = os.path.join(ROOT, "runtime")
STATE_DIR = os.path.join(RUNTIME_DIR, "state")
LOGS_DIR = os.path.join(RUNTIME_DIR, "logs")
VENVS_DIR = os.path.join(RUNTIME_DIR, "venvs")
REFS_DIR = os.path.join(RESOURCES_DIR, "references", "primary")
EXTRA_ROOTS = {
    "/refs/": REFS_DIR,
    "/refsq/": os.path.join(RESOURCES_DIR, "references", "q"),
}
SERVICE_STATE = os.path.join(os.path.expanduser("~"), ".local", "state", "opencode", "service.json")

for _directory in (RUNTIME_DIR, STATE_DIR, LOGS_DIR, VENVS_DIR):
    os.makedirs(_directory, exist_ok=True)

# 逐跳首部，不能端到端转发
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
}

UPSTREAM = {"url": None, "auth": None, "version": None}

# 面板页心跳：页面每 5 秒 POST /heartbeat 一次。窗口一关，心跳就停，
# 守护进程据此判断"面板是否还开着"（比查进程可靠得多）。
# /bye 是页面在关闭瞬间发出的"再见"，让判定从"等超时"变成"立刻"。
LAST_BEAT = {"t": 0.0, "count": 0, "bye": 0.0}
BEAT_TIMEOUT = 8.0          # 心跳断档多久算"页面没了"（正常 4 秒一次；窗口被强杀时靠它兜底）


# ============================ QQ音乐联动 ============================
# 读/控都走 Windows 媒体会话（SMTC）——QQ音乐会发布一个会话。
# 曲目信息由 tools/smtc-daemon.ps1（常驻，每 500ms 打一行 JSON）提供；
# 控制动作由 tools/smtc-control.ps1（一次性调用）执行。
# 歌词走 QQ音乐网页接口（搜索 → 歌词），只用于本地显示。

MUSIC_FILE = os.path.join(STATE_DIR, "_music.json")
SPECTRUM_FILE = os.path.join(STATE_DIR, "_spectrum.json")
AUDIO_PY = os.path.join(VENVS_DIR, "audio", "Scripts", "python.exe")
_spectrum_proc = None
_spectrum_lock = threading.Lock()
MUSIC = {"state": {}, "updated": 0.0}
_music_proc = None
_music_lock = threading.Lock()

# 网易云音乐服务（跑在隔离环境 runtime/venvs/music；非官方接口，仅供个人自用）
MUSIC_SVC_PY = os.path.join(VENVS_DIR, "music", "Scripts", "python.exe")
MUSIC_SVC_PORT = 8790
_musicsvc_proc = None
_musicsvc_lock = threading.Lock()

QQ_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36")
QQ_EXE = r"C:\Program Files (x86)\Tencent\QQMusic\QQMusic.exe"
LYRICS_CACHE: dict = {}
LYRICS_TTL = 6 * 3600

# 歌词翻译（外文 → 中文）：优先用 QQ 返回的官方 trans（实测基本为空），
# 否则走有道 demo 接口（无需 key；实测质量最好："夢ならばどれほどよかったでしょう"
# → "如果只是一场梦该有多好"）。结果落盘缓存 7 天，避免每次切歌都重翻。
# ⚠ 不要用 fanyi.youdao.com 老端点（返回 HTML）、Google gtx（被墙）、Bing ttranslatev3（要 token）。
TRANSLATE_URL = "https://aidemo.youdao.com/trans"
LYRICS_ZH_FILE = os.path.join(STATE_DIR, "_lyrics_zh.json")
LYRICS_ZH_TTL = 7 * 24 * 3600

# 面板设置：是否允许守护进程在面板打开时隐藏任务栏（false = 一直不隐藏）
TASKBAR_PREF_FILE = os.path.join(STATE_DIR, "_taskbar_enabled.json")
_ZH_CACHE = None

NO_WINDOW = 0x08000000

# ---------- 系统音量（Core Audio：IAudioEndpointVolume）----------
# ⚠ 不要用 winmm 的 waveOutGetVolume / waveOutSetVolume：Win10/11 上多数设备直接
#   返回 0xFFFFFFFF（不受支持），表现为"音量条能拖、音量纹丝不动"（本项目实测：
#   waveOutGetVolume -> 0xFFFFFFFF，而真实音量是 15，滑块却一直显示 100）。
#   这里用纯 ctypes 调 Core Audio，拿到的才是真实系统音量，也才能真的改。

class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

    def __init__(self, s: str):
        super().__init__()
        if _ole32.CLSIDFromString(s, ctypes.byref(self)) != 0:
            raise OSError("bad guid " + s)


try:
    _ole32 = ctypes.windll.ole32
    _ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    _ole32.CoInitializeEx.restype = ctypes.c_long
    _ole32.CoCreateInstance.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong,
                                        ctypes.c_void_p, ctypes.c_void_p]
    _ole32.CoCreateInstance.restype = ctypes.c_long
    _ole32.CLSIDFromString.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p]
    _ole32.CLSIDFromString.restype = ctypes.c_long
    _CLSID_MMDEVICE = _GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")      # MMDeviceEnumerator
    _IID_IMMDEVICE_ENUM = _GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
    _IID_ENDPOINT_VOLUME = _GUID("{5CDF2C82-841E-4546-9722-0CF74078229A}")
    _E_RENDER, _E_MULTIMEDIA = 0, 1
except Exception:  # noqa: BLE001
    _ole32 = None


def _com_call(this, index: int, argtypes, *args):
    """按 vtable 下标调 COM 方法（this = 接口指针）。"""
    vtbl = ctypes.cast(this, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)
    return proto(vtbl[index])(this, *args)


def _open_endpoint_volume():
    """默认输出设备的 IAudioEndpointVolume 指针（用完记得 Release，即 _com_call(p,2,[])）。"""
    _ole32.CoInitializeEx(None, 0)          # 每个请求线程都要初始化（幂等，可重复调）
    enum = ctypes.c_void_p()
    hr = _ole32.CoCreateInstance(ctypes.byref(_CLSID_MMDEVICE), None, 1,
                                 ctypes.byref(_IID_IMMDEVICE_ENUM), ctypes.byref(enum))
    if hr != 0:
        raise OSError("CoCreateInstance 0x%08X" % (hr & 0xFFFFFFFF))
    dev = ctypes.c_void_p()
    hr = _com_call(enum, 4, [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)],
                   _E_RENDER, _E_MULTIMEDIA, ctypes.byref(dev))
    if hr != 0:
        _com_call(enum, 2, [])
        raise OSError("GetDefaultAudioEndpoint 0x%08X" % (hr & 0xFFFFFFFF))
    vol = ctypes.c_void_p()
    hr = _com_call(dev, 3, [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p,
                            ctypes.POINTER(ctypes.c_void_p)],
                   ctypes.byref(_IID_ENDPOINT_VOLUME), 23, None, ctypes.byref(vol))
    _com_call(enum, 2, [])
    _com_call(dev, 2, [])
    if hr != 0:
        raise OSError("Activate 0x%08X" % (hr & 0xFFFFFFFF))
    return vol


def volume_get() -> int:
    """系统音量（0-100）；失败返回 -1。"""
    if _ole32 is None:
        return -1
    try:
        vol = _open_endpoint_volume()
        try:
            v = ctypes.c_float()
            hr = _com_call(vol, 9, [ctypes.POINTER(ctypes.c_float)], ctypes.byref(v))
            return round(v.value * 100) if hr == 0 else -1
        finally:
            _com_call(vol, 2, [])
    except Exception:  # noqa: BLE001
        return -1


def volume_set(pct) -> int:
    """把系统音量设成 0-100，返回设完后的实际值（-1 = 失败）。"""
    if _ole32 is None:
        return -1
    try:
        vol = _open_endpoint_volume()
        try:
            f = ctypes.c_float(max(0.0, min(100.0, float(pct))) / 100.0)
            hr = _com_call(vol, 7, [ctypes.c_float, ctypes.c_void_p], f, None)
            if hr != 0:
                return -1
        finally:
            _com_call(vol, 2, [])
        time.sleep(0.02)
        return volume_get()
    except Exception:  # noqa: BLE001
        return -1


def _qq_get_json(url: str, referer: str = "https://y.qq.com/") -> dict:
    req = urllib.request.Request(url, headers={
        "User-Agent": QQ_UA, "Referer": referer,
        "Accept": "application/json, text/plain, */*",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        text = resp.read().decode("utf-8", "replace")
    if not text.lstrip().startswith("{"):          # 有些接口会套一层 jsonp
        text = text[text.index("(") + 1: text.rindex(")")]
    return json.loads(text)


def _lrc_parse(raw: str) -> list:
    """把 LRC 解析成 [{t: 秒, s: 文本}]，按时间升序。"""
    out = []
    for line in (raw or "").splitlines():
        m = re.match(r"^((?:\[\d+:\d+(?:[.:]\d+)?\])+)(.*)$", line.strip())
        if not m:
            continue
        text = m.group(2).strip()
        if not text:
            continue
        for mm, ss in re.findall(r"\[(\d+):(\d+(?:[.:]\d+)?)\]", m.group(1)):
            out.append({"t": round(int(mm) * 60 + float(ss.replace(":", ".")), 2), "s": text})
    out.sort(key=lambda x: x["t"])
    return out


def _zh_all() -> dict:
    """翻译缓存（落盘，重启不丢）：{ mid: {"_t": 时间戳, "zh": [每行中文]} }"""
    global _ZH_CACHE
    if _ZH_CACHE is None:
        try:
            with open(LYRICS_ZH_FILE, "r", encoding="utf-8") as fh:
                _ZH_CACHE = json.load(fh)
        except Exception:  # noqa: BLE001
            _ZH_CACHE = {}
    return _ZH_CACHE


def _zh_get(mid: str):
    hit = _zh_all().get(mid)
    if hit and time.time() - hit.get("_t", 0) < LYRICS_ZH_TTL:
        return hit.get("zh")
    return None


def _zh_put(mid: str, zh: list) -> None:
    data = _zh_all()
    data[mid] = {"_t": time.time(), "zh": zh}
    if len(data) > 200:                                # 只留最近 200 首
        for k in sorted(data, key=lambda x: data[x].get("_t", 0))[:len(data) - 200]:
            data.pop(k, None)
    try:
        with open(LYRICS_ZH_FILE, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        pass


_RE_KANA = re.compile(r"[\u3040-\u30ff]")
_RE_HANGUL = re.compile(r"[\uac00-\ud7af]")
_RE_CJK = re.compile(r"[\u4e00-\u9fff]")
_RE_LATIN = re.compile(r"[A-Za-z]")


def _needs_translation(lines: list) -> bool:
    """外文才翻：有假名/韩文 → 要；以汉字为主 → 不要；拉丁字母多 → 要。"""
    text = "".join((l.get("s") or "") for l in lines)
    if not text.strip():
        return False
    kana = len(_RE_KANA.findall(text))
    hangul = len(_RE_HANGUL.findall(text))
    cjk = len(_RE_CJK.findall(text))
    latin = len(_RE_LATIN.findall(text))
    if kana or hangul:
        return True
    if cjk >= 2 and cjk * 4 >= latin:
        return False
    return latin > 0


def _youdao(text: str) -> str:
    """有道 demo 翻译：from=auto 自动识别语种，to=zh-CHS。"""
    url = f"{TRANSLATE_URL}?from=auto&to=zh-CHS&q={quote(text)}"
    req = urllib.request.Request(url, headers={"User-Agent": QQ_UA,
                                              "Referer": "https://ai.youdao.com/"})
    with urllib.request.urlopen(req, timeout=8) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    tr = d.get("translation") or []
    return (tr[0] if tr else "").strip()


def _translate_lines(texts: list, chunk: int = 8) -> list:
    """整段翻（保留换行）；行数对不上就逐行重试。"""
    out: list = []
    for i in range(0, len(texts), chunk):
        part = texts[i:i + chunk]
        got = []
        try:
            got = _youdao("\n".join(part)).split("\n")
        except Exception:  # noqa: BLE001
            got = []
        if len(got) != len(part):
            got = []
            for t in part:
                try:
                    got.append(_youdao(t))
                except Exception:  # noqa: BLE001
                    got.append("")
        out.extend(got)
    return out


def qq_lyrics(title: str, artist: str, translate: bool = True) -> dict:
    """按 曲名+艺人 搜到 mid，再取歌词（可带中文翻译）。带缓存。"""
    key = f"{title}|{artist}".strip().lower()
    hit = LYRICS_CACHE.get(key)
    if hit and time.time() - hit.get("_t", 0) < LYRICS_TTL:
        # 中文歌词（needsTrans=False）直接命中；外文歌词要"翻好了"才命中，
        # 否则（上次翻译失败）这次重试一次。
        if not translate or hit.get("translated") or not hit.get("needsTrans"):
            return hit

    out = {"title": title, "artist": artist, "matchedTitle": "", "matchedArtist": "",
           "mid": "", "lines": [], "found": False, "translated": False}
    try:
        js = _qq_get_json("https://c.y.qq.com/soso/fcgi-bin/client_search_cp?"
                          f"w={quote((title + ' ' + artist).strip())}&format=json&n=5&p=1&new_json=1")
        songs = (js.get("data") or {}).get("song", {}).get("list") or []
        if songs:
            s0 = songs[0]
            mid = s0.get("mid") or s0.get("songmid") or ""
            out["found"] = True
            out["mid"] = mid
            out["matchedTitle"] = s0.get("title") or s0.get("songname") or title
            out["matchedArtist"] = "/".join(x.get("name", "") for x in (s0.get("singer") or []))
            if mid:
                lj = _qq_get_json("https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg?"
                                  f"songmid={mid}&format=json&nobase64=1&g_tk=5381",
                                  referer="https://y.qq.com/portal/player.html")
                out["lines"] = _lrc_parse(lj.get("lyric", ""))
                if translate:
                    _attach_translation(out, mid, lj.get("trans") or "")
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"

    out["_t"] = time.time()
    LYRICS_CACHE[key] = out
    return out


def _attach_translation(out: dict, mid: str, official: str) -> None:
    """给 out["lines"] 逐行补 "zh"：官方 trans 优先，其次机器翻译（带缓存）。"""
    lines = out["lines"]
    if not lines:
        out["needsTrans"] = False
        return
    out["needsTrans"] = _needs_translation(lines)
    zh: list = []
    if official.strip():                               # 官方翻译（实测基本为空）
        off = _lrc_parse(official)
        if len(off) == len(lines):                     # 行数对齐才用
            zh = [x.get("s", "") for x in off]
    if not zh:
        cached = _zh_get(mid)
        if cached and len(cached) == len(lines):
            zh = cached
        elif out["needsTrans"]:
            try:
                zh = _translate_lines([l.get("s", "") for l in lines])
                _zh_put(mid, zh)
            except Exception as exc:  # noqa: BLE001
                out["transError"] = f"{type(exc).__name__}: {exc}"
                zh = []
    for i, l in enumerate(lines):
        if i < len(zh) and (zh[i] or "").strip():
            l["zh"] = zh[i].strip()
    out["translated"] = any(l.get("zh") for l in lines)


def read_taskbar_pref() -> bool:
    """面板打开时是否隐藏任务栏；默认开。"""
    try:
        with open(TASKBAR_PREF_FILE, "r", encoding="utf-8") as fh:
            return bool(json.load(fh).get("enabled", True))
    except Exception:  # noqa: BLE001
        return True


def write_taskbar_pref(enabled: bool) -> bool:
    """落盘任务栏开关，供守护进程在 /alive 里读取。"""
    enabled = bool(enabled)
    try:
        tmp = TASKBAR_PREF_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"enabled": enabled}, fh, ensure_ascii=False)
        os.replace(tmp, TASKBAR_PREF_FILE)
        return True
    except Exception:  # noqa: BLE001
        return False


def _read_music_file() -> dict:
    try:
        with open(MUSIC_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return {}


def qq_search(kw: str, limit: int = 10) -> dict:
    """在 QQ音乐 曲库里搜歌。返回标题/艺人/专辑/mid/时长。"""
    out = {"ok": True, "q": kw, "items": []}
    try:
        js = _qq_get_json("https://c.y.qq.com/soso/fcgi-bin/client_search_cp?"
                          f"w={quote(kw)}&format=json&n={limit}&p=1&new_json=1")
        for s in ((js.get("data") or {}).get("song", {}).get("list") or [])[:limit]:
            album = s.get("album")
            out["items"].append({
                "title": s.get("title") or s.get("songname") or "",
                "artist": "/".join(x.get("name", "") for x in (s.get("singer") or [])),
                "album": album.get("name", "") if isinstance(album, dict) else "",
                "mid": s.get("mid") or s.get("songmid") or "",
                "interval": s.get("interval"),
            })
    except Exception as exc:  # noqa: BLE001
        out["ok"] = False
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def _qq_pids() -> set:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq QQMusic.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=15, creationflags=NO_WINDOW).stdout or ""
    except Exception:  # noqa: BLE001
        return set()
    pids = set()
    for raw in out.splitlines():
        parts = [p.strip('"') for p in raw.strip().split('","')]
        if len(parts) >= 2 and parts[0].lower() == "qqmusic.exe":
            try:
                pids.add(int(parts[1]))
            except ValueError:
                pass
    return pids


_qq_state_cache = {"t": 0.0, "running": False}


def qq_running(max_age: float = 4.0) -> bool:
    """QQ音乐客户端是否在跑（带缓存，避免每次轮询都起一次 tasklist）。"""
    now = time.time()
    if now - _qq_state_cache["t"] > max_age:
        _qq_state_cache["running"] = bool(_qq_pids())
        _qq_state_cache["t"] = now
    return _qq_state_cache["running"]


def close_qqmusic() -> int:
    """优先优雅关闭（给主窗口发 WM_CLOSE），没有可见窗口时才强杀。返回处理的窗口数。"""
    pids = _qq_pids()
    if not pids:
        return 0
    u = ctypes.WinDLL("user32", use_last_error=True)
    u.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p),
                              ctypes.c_void_p]
    u.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
    u.IsWindowVisible.argtypes = [ctypes.c_void_p]
    u.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
    u.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _cb(hwnd, _lp):
        pid = ctypes.c_uint()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and u.IsWindowVisible(hwnd) and u.GetWindowTextLengthW(hwnd) > 0:
            found.append(hwnd)
        return True

    u.EnumWindows(_cb, None)
    for hwnd in found:
        u.PostMessageW(hwnd, 0x0010, None, None)          # WM_CLOSE
    # QQ音乐 收到 WM_CLOSE 往往只是缩到托盘（甚至弹确认框），并不会真的退出，
    # 所以等一会儿再检查；还活着就强杀，否则"关闭"按钮形同虚设。
    for _ in range(6):
        time.sleep(0.5)
        if not _qq_pids():
            return len(found)
    subprocess.run(["taskkill", "/IM", "QQMusic.exe", "/F"],
                   capture_output=True, creationflags=NO_WINDOW)
    return len(found)


def start_qqmusic() -> tuple:
    if not os.path.isfile(QQ_EXE):
        return False, f"找不到 {QQ_EXE}"
    try:
        subprocess.Popen([QQ_EXE], cwd=os.path.dirname(QQ_EXE), creationflags=NO_WINDOW)
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def start_music_daemon(force: bool = False) -> None:
    """拉起常驻的 SMTC 采集进程（幂等）。它把状态写进 _music.json。

    两重去重：
      ① 状态文件还新鲜（< 10s）说明已经有采集进程在写 → 不再拉新的；
         （服务重启不会带走已脱离的采集进程，这一步避免每次重启都堆一个）
      ② 采集脚本内部用命名互斥量做单实例 → 万一并发，第二个会立刻退出。
    """
    global _music_proc
    if not force:
        try:
            if (time.time() - os.path.getmtime(MUSIC_FILE)) < 10:
                return
        except OSError:
            pass
    with _music_lock:
        if _music_proc is not None and _music_proc.poll() is None:
            return
        script = os.path.join(TOOLS_DIR, "smtc-daemon.ps1")
        if not os.path.isfile(script):
            return
        try:
            _music_proc = subprocess.Popen(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", script, "-OutFile", MUSIC_FILE],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                creationflags=NO_WINDOW,
            )
        except Exception:  # noqa: BLE001
            _music_proc = None


def ui_version() -> str:
    """读 frontend/index.html 里的 ?v=，作为"服务端现在的前端版本号"。
    前端拿它和自己加载到的 ?v= 比对，不一致就自动刷新（避免面板一直跑旧代码）。"""
    try:
        with open(os.path.join(FRONTEND_DIR, "index.html"), "r", encoding="utf-8") as fh:
            m = re.search(r"\?v=([\w.\-]+)", fh.read())
        return m.group(1) if m else ""
    except Exception:  # noqa: BLE001
        return ""


# ============================ 动态壁纸（Wallpaper Engine 原片） ============================
# 收起侧栏时播放创意工坊里的 mp4 原片（展开立刻暂停 → 不再解码）。
# 用 glob 匹配，避免把带书名号/破折号的文件名硬编码进来；优先「不在 files/ 子目录里」的那个。

WE_APPID = "431960"
# 每个主题一串候选，按顺序取第一个找到的。三种写法：
#   "title:关键字" —— 扫各壁纸的 project.json 标题，包含关键字就用它（改名/换文件名都不怕）
#   "id:文件夹名"  —— 直接指定创意工坊 id
#   其余当作相对 431960 的 glob（例如 "*/sakura*.mp4"）
WE_WALLPAPERS = {
    # 白昼：［绯莎］绘梨衣 sakura —— 夜：月读命绘梨衣
    "hiru": ["title:绯莎", "3786830950", "*/sakura*.mp4"],
    "yoru": ["title:月读", "3568633530"],
}
_we_root_cache = {"path": None, "t": 0.0}
MEDIA_EXT = (".mp4", ".webm", ".mkv")
WALLPAPER_PICK_FILE = os.path.join(STATE_DIR, "_wallpapers.json")   # 用户在面板里选的壁纸（主题 → 创意工坊 id）


def _steam_roots() -> list:
    """Steam 根目录候选：注册表 + 各库的 libraryfolders.vdf。"""
    roots = []
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            p = winreg.QueryValueEx(k, "SteamPath")[0]
        if p:
            roots.append(os.path.normpath(p))
    except Exception:  # noqa: BLE001
        pass
    for r in list(roots):                      # 别的库（D:\SteamLibrary 之类）
        vdf = os.path.join(r, "steamapps", "libraryfolders.vdf")
        try:
            with open(vdf, "r", encoding="utf-8", errors="replace") as fh:
                for m in re.finditer(r'"path"\s+"([^"]+)"', fh.read()):
                    roots.append(os.path.normpath(m.group(1).replace("\\\\", "\\")))
        except Exception:  # noqa: BLE001
            pass
    return roots


def we_root(max_age: float = 60.0):
    """创意工坊 431960（壁纸）目录；找不到返回 None。带缓存，别每次请求都扫盘。"""
    now = time.time()
    if _we_root_cache["path"] is not None and now - _we_root_cache["t"] < max_age:
        return _we_root_cache["path"] or None
    found = None
    for r in _steam_roots():
        cand = os.path.join(r, "steamapps", "workshop", "content", WE_APPID)
        if os.path.isdir(cand):
            found = cand
            break
    _we_root_cache["path"] = found or ""
    _we_root_cache["t"] = now
    return found


def _we_titles(root: str) -> dict:
    """{文件夹名: project.json 里的标题}"""
    out = {}
    for d in os.listdir(root):
        p = os.path.join(root, d)
        if not os.path.isdir(p):
            continue
        try:
            with open(os.path.join(p, "project.json"), "r", encoding="utf-8-sig") as fh:
                out[d] = str(json.load(fh).get("title") or "")
        except Exception:  # noqa: BLE001
            out[d] = ""
    return out


def _pick_media(folder: str):
    """在一个壁纸目录里挑主视频：优先 project.json 的 file，其次目录里最大的视频文件。"""
    try:
        with open(os.path.join(folder, "project.json"), "r", encoding="utf-8-sig") as fh:
            f = str(json.load(fh).get("file") or "")
        if f and f.lower().endswith(MEDIA_EXT):
            p = os.path.join(folder, f)
            if os.path.isfile(p):
                return p
    except Exception:  # noqa: BLE001
        pass
    best = None
    try:
        for name in os.listdir(folder):
            p = os.path.join(folder, name)
            if os.path.isfile(p) and name.lower().endswith(MEDIA_EXT):
                if best is None or os.path.getsize(p) > os.path.getsize(best):
                    best = p
    except OSError:
        pass
    return best


def live_wallpaper_files() -> dict:
    """按主题解析出可用的原片：{主题: (相对 431960 的路径, 壁纸标题)}。
    优先用**用户在面板里选过的**，没选过才落到 WE_WALLPAPERS 里的默认候选。"""
    root = we_root()
    out = {}
    if not root:
        return out
    titles = _we_titles(root)
    pick = _read_pick()
    for theme, specs in WE_WALLPAPERS.items():
        wid = str(pick.get(theme) or "")
        if wid and wid in titles:                    # ① 用户选的
            folder = os.path.join(root, wid)
            if we_project_kind(folder) != "video":   # 只支持 video 类壁纸
                continue
            p = _pick_media(folder)
            if p:
                out[theme] = (os.path.relpath(p, root).replace("\\", "/"), titles.get(wid, ""))
                continue
        for spec in specs:                           # ② 默认候选
            pick_path, label = None, ""
            if spec.startswith("title:"):
                key = spec[len("title:"):]
                for d, t in titles.items():
                    if key and key in t:
                        pick_path = _pick_media(os.path.join(root, d))
                        label = t
                        if pick_path:
                            break
            elif spec.startswith("id:"):
                d = spec[len("id:"):]
                if d in titles:
                    pick_path = _pick_media(os.path.join(root, d))
                    label = titles.get(d, "")
            else:
                hits = [p for p in glob.glob(os.path.join(root, spec)) if os.path.isfile(p)]
                if hits:
                    hits.sort(key=lambda p: -os.path.getsize(p))
                    pick_path = hits[0]
            if pick_path:
                out[theme] = (os.path.relpath(pick_path, root).replace("\\", "/"), label)
                break
    return out


def _read_pick() -> dict:
    try:
        with open(WALLPAPER_PICK_FILE, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _write_pick(theme: str, wid) -> dict:
    d = _read_pick()
    if wid:
        d[theme] = str(wid)
    else:
        d.pop(theme, None)
    try:
        with open(WALLPAPER_PICK_FILE, "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False, indent=2)
    except Exception:  # noqa: BLE001
        pass
    return d


def we_project_kind(folder: str) -> str:
    """video / scene / application / unsupported（application 一律避开）"""
    try:
        names = os.listdir(folder)
    except OSError:
        return "unsupported"
    low = [n.lower() for n in names]
    if any(n.endswith((".exe", ".dll", ".bat", ".cmd", ".ps1", ".vbs")) for n in low):
        return "application"
    typ = ""
    try:
        with open(os.path.join(folder, "project.json"), "r", encoding="utf-8-sig") as fh:
            typ = str(json.load(fh).get("type") or "").lower()
    except Exception:  # noqa: BLE001
        pass
    if typ in ("application", "program"):
        return "application"
    if typ == "scene" or any(n.endswith(".pkg") for n in low):
        return "scene"
    if typ == "video" or any(n.endswith(MEDIA_EXT) for n in low):
        return "video"
    return "unsupported"


def list_video_wallpapers() -> list:
    """创意工坊里的 **video** 壁纸（可在浏览器里直接播）。
    scene / application / 含 exe·dll 的项目一律不列、不解析、不执行。"""
    root = we_root()
    if not root:
        return []
    out = []
    for d, t in _we_titles(root).items():
        folder = os.path.join(root, d)
        if we_project_kind(folder) != "video":
            continue
        p = _pick_media(folder)
        if not p:
            continue
        preview = ""
        for name in ("preview.gif", "preview.jpg", "preview.png"):
            if os.path.isfile(os.path.join(folder, name)):
                preview = "/we/" + quote(d) + "/" + quote(name)
                break
        rel = os.path.relpath(p, root).replace("\\", "/")
        try:
            size = os.path.getsize(p)
        except OSError:
            size = 0
        out.append({
            "id": d, "title": t, "kind": "video",
            "file": os.path.basename(rel), "size_mb": round(size / 1048576.0, 1),
            "url": "/we/" + "/".join(quote(seg) for seg in rel.split("/")),
            "preview": preview,
        })
    out.sort(key=lambda x: -x["size_mb"])
    return out


def _read_spectrum() -> dict:
    try:
        with open(SPECTRUM_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return {}


def spectrum_age():
    try:
        return max(0.0, time.time() - os.path.getmtime(SPECTRUM_FILE))
    except OSError:
        return None


def start_spectrum(force: bool = False) -> None:
    """拉起频谱采集进程（用 runtime/venvs/audio 里的 python；没有那个环境就安静跳过）。"""
    global _spectrum_proc
    if not os.path.isfile(AUDIO_PY):
        return
    if not force:
        age = spectrum_age()
        if age is not None and age < 5:
            return
    with _spectrum_lock:
        if _spectrum_proc is not None and _spectrum_proc.poll() is None:
            return
        script = os.path.join(TOOLS_DIR, "spectrum.py")
        if not os.path.isfile(script):
            return
        try:
            _spectrum_proc = subprocess.Popen(
                [AUDIO_PY, script, "--out", SPECTRUM_FILE],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                creationflags=NO_WINDOW)
        except Exception:  # noqa: BLE001
            _spectrum_proc = None


def spectrum_keeper() -> None:
    """频谱文件超过 8 秒没更新就重新拉起采集进程。"""
    while True:
        time.sleep(5)
        try:
            age = spectrum_age()
            if age is None or age > 8:
                start_spectrum()
        except Exception:  # noqa: BLE001
            pass


def stop_spectrum() -> None:
    global _spectrum_proc
    with _spectrum_lock:
        if _spectrum_proc is not None and _spectrum_proc.poll() is None:
            try:
                _spectrum_proc.kill()
            except Exception:  # noqa: BLE001
                pass
        _spectrum_proc = None


def music_svc_alive() -> bool:
    """本地网易云音乐服务是否在监听。"""
    try:
        with socket.create_connection(("127.0.0.1", MUSIC_SVC_PORT), 0.3):
            return True
    except OSError:
        return False


def start_music_service() -> None:
    """拉起网易云音乐服务（用 runtime/venvs/music；没有那个环境就安静跳过）。"""
    global _musicsvc_proc
    if not os.path.isfile(MUSIC_SVC_PY):
        return
    with _musicsvc_lock:
        if _musicsvc_proc is not None and _musicsvc_proc.poll() is None:
            return
        script = os.path.join(TOOLS_DIR, "music_service.py")
        if not os.path.isfile(script):
            return
        try:
            _musicsvc_proc = subprocess.Popen(
                [MUSIC_SVC_PY, script, "--port", str(MUSIC_SVC_PORT)],
                cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
        except Exception:  # noqa: BLE001
            _musicsvc_proc = None


def music_service_keeper() -> None:
    """音乐服务没在监听就重新拉起。"""
    while True:
        time.sleep(5)
        try:
            if not music_svc_alive():
                start_music_service()
        except Exception:  # noqa: BLE001
            pass


def music_keeper() -> None:
    """只看状态文件新不新鲜：超过 10 秒没更新就重新拉起采集进程。
    采集进程自带命名互斥量做单实例，所以这里多拉几次也不会堆进程。"""
    while True:
        time.sleep(5)
        try:
            try:
                stale = (time.time() - os.path.getmtime(MUSIC_FILE)) > 10
            except OSError:
                stale = True
            if stale:
                start_music_daemon()
        except Exception:  # noqa: BLE001
            pass


def stop_music_daemon() -> None:
    global _music_proc
    with _music_lock:
        if _music_proc is not None and _music_proc.poll() is None:
            try:
                _music_proc.kill()
            except Exception:  # noqa: BLE001
                pass
        _music_proc = None



def read_service() -> tuple[str, str | None, str | None]:
    """读取 OpenCode 后台服务的地址与密码。"""
    with open(SERVICE_STATE, "r", encoding="utf-8") as fh:
        svc = json.load(fh)
    url = str(svc["url"]).rstrip("/")
    pw = svc.get("password")
    auth = "Basic " + base64.b64encode(f"opencode:{pw}".encode()).decode() if pw else None
    return url, auth, svc.get("version")


class Handler(BaseHTTPRequestHandler):
    server_version = "opencode-ui"
    protocol_version = "HTTP/1.1"

    # ---------- 基础工具 ----------

    def log_message(self, fmt, *args):
        # 静默常规请求，避免控制台被 SSE 刷屏
        if os.environ.get("OPENCODE_UI_VERBOSE"):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send_json(self, code: int, obj: dict):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---------- 路由 ----------

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_PUT(self):
        self._route("PUT")

    def do_PATCH(self):
        self._route("PATCH")

    def do_DELETE(self):
        self._route("DELETE")

    def _route(self, method: str):
        path = urlsplit(self.path).path
        try:
            if path == "/healthz":
                self._healthz()
            elif path == "/heartbeat":
                self._heartbeat()
            elif path == "/bye":
                self._bye()
            elif path == "/alive":
                self._alive()
            elif path == "/panel/taskbar":
                self._panel_taskbar(method)
            elif path == "/qq/state":
                self._qq_state()
            elif path == "/qq/control":
                self._qq_control(method)
            elif path == "/qq/volume":
                self._qq_volume(method)
            elif path == "/qq/lyrics":
                self._qq_lyrics()
            elif path == "/qq/search":
                self._qq_search()
            elif path == "/qq/spectrum":
                self._qq_spectrum()
            elif path == "/qq/app":
                self._qq_app(method)
            elif path == "/live/wallpapers":
                self._live_wallpapers()
            elif path == "/live/list":
                self._live_list()
            elif path == "/live/pick":
                self._live_pick(method)
            elif path.startswith("/we/"):
                self._we(path)
            elif path.startswith("/music/"):
                self._music_proxy(method, path)
            elif path.startswith("/api/"):
                self._proxy(method)
            else:
                self._static(path)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception as exc:  # noqa: BLE001 - 兜底，保证服务不崩
            try:
                self._send_json(500, {"error": f"{type(exc).__name__}: {exc}"})
            except Exception:  # noqa: BLE001
                self.close_connection = True

    # ---------- 健康检查 ----------

    def _healthz(self):
        try:
            url, auth, version = read_service()
            req = urllib.request.Request(url + "/api/info", headers={"Authorization": auth or ""})
            with urllib.request.urlopen(req, timeout=5) as resp:
                info = json.loads(resp.read().decode("utf-8"))
            self._send_json(200, {"ok": True, "upstream": url, "ui": ui_version(),
                                  "version": info.get("version", version)})
        except Exception as exc:  # noqa: BLE001
            self._send_json(503, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    # ---------- 面板心跳 ----------

    def _heartbeat(self):
        LAST_BEAT["t"] = time.time()
        LAST_BEAT["count"] += 1
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _bye(self):
        """页面关闭瞬间的信号：比心跳超时判定快得多。"""
        LAST_BEAT["bye"] = time.time()
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _alive(self):
        now = time.time()
        t = LAST_BEAT["t"]
        age = (now - t) if t else None
        fresh = age is not None and age < BEAT_TIMEOUT
        closed = LAST_BEAT["bye"] > t          # 收到过比最后一次心跳更晚的"再见"
        self._send_json(200, {
            "alive": bool(fresh and not closed),
            "age_seconds": round(age, 1) if age is not None else None,
            "closed": bool(closed),
            "beats": LAST_BEAT["count"],
            "taskbar": read_taskbar_pref(),   # 守护进程据此决定要不要隐藏任务栏
        })

    def _panel_taskbar(self, method: str):
        """面板设置：任务栏是否在面板打开时自动隐藏。"""
        if method == "GET":
            self._send_json(200, {"enabled": read_taskbar_pref()})
            return
        if method != "POST":
            self._send_json(405, {"error": "GET or POST only"})
            return
        body = self._read_json_body() or {}
        enabled = bool(body.get("enabled", True))
        ok = write_taskbar_pref(enabled)
        self._send_json(200 if ok else 500, {"ok": ok, "enabled": enabled})

    # ---------- QQ音乐 ----------

    def _read_json_body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8", "replace") or "{}")
        except Exception:  # noqa: BLE001
            return {}

    def _qq_state(self):
        state = _read_music_file()
        age = None
        try:
            age = max(0.0, time.time() - os.path.getmtime(MUSIC_FILE))
        except OSError:
            age = None
        self._send_json(200, {
            "ok": True,
            "age": round(age, 1) if age is not None else None,
            "stale": age is None or age > 3.0,
            "daemon": bool(_music_proc is not None and _music_proc.poll() is None),
            "qqRunning": qq_running(),
            "spectrum": (lambda a: bool(a is not None and a < 3.0))(spectrum_age()),
            "volume": volume_get(),
            "music": state,
        })

    def _qq_control(self, method: str):
        if method != "POST":
            self._send_json(405, {"error": "POST only"})
            return
        action = str((self._read_json_body() or {}).get("action", ""))
        if action not in ("playpause", "play", "pause", "next", "prev", "stop"):
            self._send_json(400, {"error": "bad action", "action": action})
            return
        script = os.path.join(TOOLS_DIR, "smtc-control.ps1")
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", script, "-Action", action],
                capture_output=True, text=True, timeout=25, creationflags=NO_WINDOW,
            ).stdout.strip()
            ok = '"ok":true' in out.replace(" ", "")
        except Exception as exc:  # noqa: BLE001
            self._send_json(502, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})
            return
        self._send_json(200, {"ok": ok, "action": action, "raw": out[:200]})

    def _qq_volume(self, method: str):
        if method == "POST":
            body = self._read_json_body() or {}
            v = volume_set(body.get("value", 100))
        else:
            v = volume_get()
        self._send_json(200, {"ok": v >= 0, "volume": v})

    def _qq_lyrics(self):
        q = parse_qs(urlsplit(self.path).query)
        title = (q.get("title", [""])[0] or "").strip()
        artist = (q.get("artist", [""])[0] or "").strip()
        if not title:
            self._send_json(400, {"error": "title required"})
            return
        data = qq_lyrics(title, artist, translate=(q.get("tr", ["1"])[0] != "0"))
        data.pop("_t", None)
        self._send_json(200, data)

    def _qq_search(self):
        q = parse_qs(urlsplit(self.path).query)
        kw = (q.get("q", [""])[0] or "").strip()
        if not kw:
            self._send_json(400, {"error": "q required"})
            return
        self._send_json(200, qq_search(kw))

    def _qq_spectrum(self):
        data = _read_spectrum()
        age = spectrum_age()
        bars = data.get("bars") or []
        self._send_json(200, {
            "ok": bool(bars) and age is not None and age < 3.0,
            "age": round(age, 2) if age is not None else None,
            "device": data.get("device"),          # 采集器当前盯着的输出设备（蓝牙切走时一眼能看出来）
            "bars": bars,
            "error": data.get("error"),
        })

    def _qq_app(self, method: str):
        """启动 / 关闭 QQ音乐 客户端。"""
        if method != "POST":
            self._send_json(405, {"error": "POST only"})
            return
        action = str((self._read_json_body() or {}).get("action", ""))
        if action == "start":
            ok, err = start_qqmusic()
            self._send_json(200 if ok else 500, {"ok": ok, "action": "start", "error": err})
        elif action == "stop":
            self._send_json(200, {"ok": True, "action": "stop", "windows": close_qqmusic()})
        elif action == "status":
            self._send_json(200, {"ok": True, "running": bool(_qq_pids())})
        else:
            self._send_json(400, {"ok": False, "error": "bad action"})

    # ---------- 动态壁纸（创意工坊 mp4）----------

    def _live_wallpapers(self):
        """给前端：当前各主题生效的原片（只支持 video 类壁纸）。"""
        root = we_root()
        files = live_wallpaper_files() if root else {}
        out = {"ok": bool(files), "root": root, "hiru": None, "yoru": None, "detail": {}}
        for theme, (rel, label) in files.items():
            url = "/we/" + "/".join(quote(seg) for seg in rel.split("/"))
            out[theme] = url
            try:
                size = os.path.getsize(os.path.join(root, rel))
            except OSError:
                size = 0
            out["detail"][theme] = {"title": label, "file": os.path.basename(rel),
                                    "size_mb": round(size / 1048576.0, 1), "url": url,
                                    "kind": "video"}
        self._send_json(200, out)

    def _live_list(self):
        """所有能用浏览器播的壁纸（含用户当前选择），给面板里的选择器用。"""
        root = we_root()
        items = list_video_wallpapers() if root else []
        files = live_wallpaper_files() if root else {}
        self._send_json(200, {
            "ok": bool(items), "root": root, "items": items, "pick": _read_pick(),
            "detail": {t: {"title": v[1]} for t, v in files.items()},
        })

    def _live_pick(self, method: str):
        """用户在面板里选壁纸：{theme: "hiru"|"yoru", id: "<创意工坊 id>"|null}（null = 恢复默认）"""
        if method != "POST":
            self._send_json(405, {"error": "POST only"})
            return
        body = self._read_json_body() or {}
        theme = str(body.get("theme") or "")
        if theme not in ("hiru", "yoru"):
            self._send_json(400, {"error": "theme 必须是 hiru / yoru"})
            return
        root = we_root()
        wid = body.get("id")
        wid = str(wid) if wid else None
        if wid and (not root or wid not in _we_titles(root)):
            self._send_json(400, {"error": "找不到这个壁纸 id", "id": wid})
            return
        _write_pick(theme, wid)
        files = live_wallpaper_files() if root else {}
        self._send_json(200, {"ok": True, "pick": _read_pick(), "theme": theme, "id": wid,
                              "detail": {t: {"title": v[1]} for t, v in files.items()}})

    def _we(self, path: str):
        """/we/<相对 431960 的路径> —— 流式发创意工坊里的文件（给 <video> 用）。"""
        root = we_root()
        if not root:
            self._send_json(404, {"error": "没找到 Wallpaper Engine 创意工坊目录"})
            return
        rel = urllib.parse.unquote(path[len("/we/"):])
        target = os.path.normpath(os.path.join(root, rel))
        if not target.startswith(root) or not os.path.isfile(target):
            self._send_json(404, {"error": "not found", "path": path})
            return
        self._send_file(target)

    # ---------- 静态文件 ----------

    def _ctype_for(self, target: str) -> str:
        ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        return ctype

    def _send_file(self, target: str, base: int = 0, total: int = None, ctype: str = None):
        """发文件，**支持 Range**。
        没有 Range 的话 <video> 得把整份下完才能播（我们的壁纸原片是 300~400MB）——
        所以这里必须实现 206 / Content-Range。"""
        try:
            file_size = os.path.getsize(target)
        except OSError:
            self._send_json(404, {"error": "not found"})
            return
        size = int(total) if total is not None else max(0, file_size - base)
        start, end, status = 0, size - 1, 200
        rng = (self.headers.get("Range") or "").strip()
        m = re.match(r"bytes=(\d*)-(\d*)$", rng)
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                if m.group(2):
                    end = min(int(m.group(2)), size - 1)
            else:                                  # bytes=-N：最后 N 字节
                start = max(0, size - int(m.group(2)))
                end = size - 1
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", "bytes */%d" % size)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            status = 206
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", ctype or self._ctype_for(target))
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with open(target, "rb") as fh:
            fh.seek(base + start)
            left = length
            while left > 0:
                chunk = fh.read(min(256 * 1024, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    def _static(self, path: str):
        if path in ("/", ""):
            path = "/index.html"
        root, rel = FRONTEND_DIR, path.lstrip("/")
        for prefix, extra in EXTRA_ROOTS.items():
            if path.startswith(prefix):
                root, rel = extra, path[len(prefix):]
                break
        target = os.path.normpath(os.path.join(root, rel))
        if not target.startswith(root) or not os.path.isfile(target):
            self._send_json(404, {"error": "not found", "path": path})
            return
        self._send_file(target)

    # ---------- 反向代理 ----------

    def _proxy(self, method: str):
        url, auth, _ = read_service()

        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None

        headers = {}
        for key, value in self.headers.items():
            low = key.lower()
            if low in HOP_BY_HOP or low in ("host", "authorization", "content-length", "accept-encoding"):
                continue
            headers[key] = value
        if auth:
            headers["Authorization"] = auth

        req = urllib.request.Request(url + self.path, data=body, headers=headers, method=method)
        try:
            upstream = urllib.request.urlopen(req, timeout=None)
        except urllib.error.HTTPError as exc:
            upstream = exc
        except Exception as exc:  # noqa: BLE001
            self._send_json(502, {"error": f"无法连接 OpenCode 服务: {type(exc).__name__}: {exc}", "upstream": url})
            return

        with upstream:
            self.send_response(upstream.status)
            for key, value in upstream.headers.items():
                low = key.lower()
                if low in HOP_BY_HOP or low == "content-length":
                    continue
                self.send_header(key, value)
            declared = upstream.headers.get("Content-Length")
            if declared:
                self.send_header("Content-Length", declared)
            else:
                # 流式（含 SSE）：不声明长度，改用连接关闭界定结尾
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()

            # read1 会在有数据时尽快返回，适合 SSE 实时透传
            while True:
                chunk = upstream.read1(16384)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()

    def _music_proxy(self, method: str, path: str):
        """把 /music/* 代理到本地网易云服务（127.0.0.1:MUSIC_SVC_PORT）。"""
        if not music_svc_alive():
            start_music_service()
        query = urlsplit(self.path).query
        target = "http://127.0.0.1:%d%s" % (MUSIC_SVC_PORT, path[len("/music"):])
        if query:
            target += "?" + query
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        headers = {}
        for key, value in self.headers.items():
            if key.lower() in ("host", "content-length", "accept-encoding", "connection"):
                continue
            headers[key] = value
        req = urllib.request.Request(target, data=body, headers=headers, method=method)
        try:
            upstream = urllib.request.urlopen(req, timeout=60)
        except urllib.error.HTTPError as exc:
            upstream = exc
        except Exception as exc:  # noqa: BLE001
            self._send_json(502, {"ok": False, "error": "音乐服务未就绪: %s: %s"
                                  % (type(exc).__name__, exc)})
            return
        with upstream:
            self.send_response(upstream.status)
            declared = upstream.headers.get("Content-Length")
            for key, value in upstream.headers.items():
                low = key.lower()
                if low in HOP_BY_HOP or low in ("content-length", "transfer-encoding"):
                    continue
                self.send_header(key, value)
            if declared:
                self.send_header("Content-Length", declared)
            else:
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            while True:
                chunk = upstream.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        super().server_bind()


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenCode 自建界面的本地代理")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()

    if not os.path.exists(SERVICE_STATE):
        print(f"[!] 找不到 OpenCode 服务状态文件：{SERVICE_STATE}", file=sys.stderr)
        print("    请先启动 OpenCode（桌面版或 `opencode serve`）。", file=sys.stderr)
        return 1

    try:
        url, _, version = read_service()
    except Exception as exc:  # noqa: BLE001
        print(f"[!] 读取服务状态失败：{exc}", file=sys.stderr)
        return 1

    httpd = None
    try:
        httpd = Server((args.host, args.port), Handler)
    except OSError as exc:
        # 端口已被占用通常意味着"已经在跑了"，对自启动来说属于正常情况
        print(f"[i] 端口 {args.host}:{args.port} 已在监听，视为已在运行（{exc}）")
        return 0

    start_music_daemon()                      # 拉起 SMTC 采集进程（QQ音乐联动）
    threading.Thread(target=music_keeper, daemon=True).start()
    start_spectrum()                          # 拉起频谱采集进程（真·音频条）
    threading.Thread(target=spectrum_keeper, daemon=True).start()
    start_music_service()                     # 拉起网易云音乐服务（面板内搜歌/放歌）
    threading.Thread(target=music_service_keeper, daemon=True).start()

    print("opencode-ui 已启动")
    print(f"  界面    http://{args.host}:{args.port}")
    print(f"  上游    {url}  (OpenCode {version})")
    print("  停止    Ctrl+C")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        stop_music_daemon()
        stop_spectrum()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
