# -*- coding: utf-8 -*-
"""ACP agent 注册表（给面板的 agentlist 用）。

注册表：runtime/state/_acp_agents.json
    {"active": "<id>", "agents": [{"id","label","command","cwd","env","note"}, ...]}

兼容旧的单 agent 配置 runtime/state/_acp.json（{"command","cwd","env"}）：
没有注册表文件时，用它 bootstrap 出唯一一个 agent（id="dsh"），
并顺带补上 Codex 的两个候选：
  - codex-node   ：Node 适配器（用便携 Node 跑 npx @agentclientprotocol/codex-acp）
  - codex-client ：本机可执行文件（GitHub Releases 的 codex-acp.exe 绝对路径）

只做配置读写，不碰子进程。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time

from .. import STATE_DIR

AGENTS_FILE = os.path.join(STATE_DIR, "_acp_agents.json")
LEGACY_FILE = os.path.join(STATE_DIR, "_acp.json")


def _read_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _node_from(legacy: dict) -> str:
    """尽量拿到一个可用的 node.exe（优先旧命令里那个便携 Node）。"""
    cmd = legacy.get("command") or []
    if cmd and str(cmd[0]).lower().endswith("node.exe") and os.path.isfile(str(cmd[0])):
        return str(cmd[0])
    return shutil.which("node") or ""


def _codex_defaults(legacy: dict) -> list:
    node = _node_from(legacy)
    npx = ""
    if node:
        cand = os.path.join(os.path.dirname(node), "node_modules", "npm", "bin", "npx-cli.js")
        if os.path.isfile(cand):
            npx = cand
    node_cmd = ([node, npx, "-y", "@agentclientprotocol/codex-acp"]
                if (node and npx) else ["npx", "-y", "@agentclientprotocol/codex-acp"])
    cwd = legacy.get("cwd") or ""
    return [
        {"id": "codex-node", "label": "Codex · Node 适配器", "builtin": True,
         "command": node_cmd, "cwd": cwd, "env": {"OPENAI_API_KEY": ""},
         "note": "npm 适配器 @agentclientprotocol/codex-acp；需联网 + 已登录 Codex（或用 OPENAI_API_KEY）"},
        {"id": "codex-client", "label": "Codex · 本机客户端", "builtin": True,
         "command": ["codex-acp"], "cwd": cwd, "env": {},
         "note": "指向本机 codex-acp 可执行文件（GitHub Releases 下载后填绝对路径），或你的 Codex 客户端命令"},
    ]


def bootstrap() -> dict:
    """没有注册表时，从 _acp.json 生成一份（含 Codex 候选）并落盘。"""
    legacy = _read_json(LEGACY_FILE)
    agents = []
    if legacy.get("command"):
        agents.append({
            "id": "dsh", "label": "DeepSeek Harness（dsh-acp）", "builtin": True,
            "command": legacy.get("command"), "cwd": legacy.get("cwd") or "",
            "env": legacy.get("env") or {}, "note": "来自旧的 _acp.json 配置",
        })
    agents.extend(_codex_defaults(legacy))
    reg = {"active": agents[0]["id"] if agents else "", "agents": agents}
    save_registry(reg)
    try:
        reg = autofill(reg)["registry"]     # 首次就自动检测本机的 node / codex / dsh 位置
    except Exception:  # noqa: BLE001
        pass
    return reg


def load_registry() -> dict:
    reg = _read_json(AGENTS_FILE)
    if not (isinstance(reg.get("agents"), list) and reg.get("agents")):
        reg = bootstrap()
    return reg


def save_registry(reg: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = AGENTS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(reg, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, AGENTS_FILE)


def find_agent(reg: dict, aid: str) -> dict:
    for a in (reg.get("agents") or []):
        if str(a.get("id")) == str(aid):
            return a
    return {}


def active_agent(reg: dict = None) -> dict:
    reg = reg or load_registry()
    a = find_agent(reg, reg.get("active"))
    if a:
        return a
    agents = reg.get("agents") or []
    return agents[0] if agents else {}


def baseline() -> dict:
    """所有 agent 共享的基线（cwd / env / mode）——避免每个 agent 各配一份。"""
    b = load_registry().get("baseline")
    return b if isinstance(b, dict) else {}


def _slug(label: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(label or "").lower()).strip("-")
    return (s[:24] or "agent")


def _node_exe() -> str:
    """找一个可用的 node.exe（优先注册表里便携 Node 那条）。"""
    for a in (load_registry().get("agents") or []):
        cmd = a.get("command") or []
        if cmd and str(cmd[0]).lower().endswith("node.exe") and os.path.isfile(str(cmd[0])):
            return str(cmd[0])
    return shutil.which("node") or ""


def infer_from_dir(d: str) -> tuple:
    """从一个目录推断怎么启动其中的 ACP agent。返回 (command|None, note)。

    认得两种常见形态：
      - node 包：`node_modules/.../package.json` 的 bin 里有含 "acp" 的入口（如 @agentclientprotocol/codex-acp）
      - 可执行文件：目录（或其一层子目录）里的 `*acp*.exe`
    """
    d = os.path.normpath(str(d or ""))
    if not d or not os.path.isdir(d):
        return None, ""
    node = _node_exe()
    nm = os.path.join(d, "node_modules")
    if os.path.isdir(nm):
        for scope in sorted(os.listdir(nm)):
            sp = os.path.join(nm, scope)
            if not os.path.isdir(sp):
                continue
            subs = ([os.path.join(scope, x) for x in sorted(os.listdir(sp))]
                    if scope.startswith("@") else [scope])
            for rel in subs:
                pj = os.path.join(nm, rel, "package.json")
                if not os.path.isfile(pj):
                    continue
                try:
                    meta = json.load(open(pj, encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    continue
                name = str(meta.get("name") or "")
                bins = meta.get("bin") or {}
                if isinstance(bins, str):
                    bins = {name.split("/")[-1] or "bin": bins}
                for bname, brel in bins.items():
                    blob = ("%s %s" % (bname, brel)).lower()
                    if "acp" not in blob and "acp" not in name.lower():
                        continue
                    entry = os.path.normpath(os.path.join(nm, rel, str(brel)))
                    if not os.path.isfile(entry):
                        continue
                    label = name or bname
                    if node:
                        return [node, entry], "node 包：%s" % label
                    return [entry], "node 包（未找到 node，可能要靠 PATH）：%s" % label
    for base in [d] + [os.path.join(d, x) for x in sorted(os.listdir(d))
                       if os.path.isdir(os.path.join(d, x))]:
        try:
            for f in sorted(os.listdir(base)):
                if f.lower().endswith(".exe") and "acp" in f.lower():
                    return [os.path.join(base, f)], "可执行文件：%s" % f
        except OSError:
            continue
    return None, ""


def scan_candidates(limit: int = 12) -> list:
    """扫描本机可能装有 ACP agent 的地方，列出**还没登记**的候选（给「新增 agent」选）。"""
    reg = load_registry()
    have_ids = {str(a.get("id")) for a in (reg.get("agents") or [])}
    have_cmds = {" ".join(str(x) for x in (a.get("command") or [])).lower().strip()
                 for a in (reg.get("agents") or []) if a.get("command")}
    out = []
    seen = set()

    def push(label, cmd, where, note):
        if not cmd:
            return
        key = " ".join(str(x) for x in cmd).lower().strip()
        if key in have_cmds or key in seen:
            return
        seen.add(key)
        cid = _slug(label) or "agent"
        n = 2
        while cid in have_ids or any(x["id"] == cid for x in out):
            cid = "%s-%d" % (_slug(label), n)
            n += 1
        st = status_of({"id": cid, "command": cmd})
        out.append({"id": cid, "label": label, "command": cmd, "cwd": "",
                    "dir": where, "note": note, "available": st["available"],
                    "missing": st["missing"]})

    # ① PATH 上常见的 ACP 适配器
    for name in ("codex-acp", "claude-code-acp", "gemini", "goose", "crush", "amp-acp", "iflow"):
        w = shutil.which(name)
        if w:
            push(name, [w], os.path.dirname(w), "PATH 上找到")
    # ② 同盘 agentlist\* 目录（我们放 agent 的地方）
    base = os.path.join(local_root(), "agentlist")
    if os.path.isdir(base):
        for d in sorted(os.listdir(base)):
            p = os.path.join(base, d)
            if not os.path.isdir(p):
                continue
            cmd, note = infer_from_dir(p)
            if cmd:
                push(d, cmd, p, note)
    # ③ 全局 npm bin 里的 *acp* 命令（npx/npm -g 装的适配器）
    for nmdir in (os.path.join(os.environ.get("APPDATA", ""), "npm"),
                  os.path.join(os.path.expanduser("~"), ".local", "bin")):
        if not os.path.isdir(nmdir):
            continue
        for f in sorted(os.listdir(nmdir)):
            low = f.lower()
            if "acp" in low and low.endswith((".exe", ".cmd")) and not low.endswith(".ps1"):
                push(f, [os.path.join(nmdir, f)], nmdir, "全局命令")
    return out[:limit]
    s = re.sub(r"[^a-z0-9]+", "-", str(label or "").lower()).strip("-")
    return (s[:24] or "agent")


def add_agent(label: str, command, cwd: str = "", env: dict = None, note: str = "") -> dict:
    """用户自己添加一个 agent（label + command 必填）。id 由 label 生成、重名自动加序号。"""
    cmd = [str(x) for x in (command or []) if str(x).strip()]
    if not cmd:
        return {}
    reg = load_registry()
    key = " ".join(cmd).lower().strip()
    for a in (reg.get("agents") or []):          # 已有同样命令的 agent → 直接复用，别加重复项
        if " ".join(str(x) for x in (a.get("command") or [])).lower().strip() == key:
            return a
    ids = {str(a.get("id")) for a in (reg.get("agents") or [])}
    aid = _slug(label)
    n = 2
    while aid in ids:
        aid = "%s-%d" % (_slug(label), n)
        n += 1
    a = {"id": aid, "label": str(label or aid), "command": cmd,
         "cwd": cwd or "", "env": env or {}, "note": note or "用户添加"}
    reg.setdefault("agents", []).append(a)
    save_registry(reg)
    return a


def remove_agent(aid: str) -> bool:
    """删掉一个 agent；**当前使用中的不许删**（否则引擎会指向不存在的配置）。"""
    reg = load_registry()
    agents = reg.get("agents") or []
    if str(reg.get("active")) == str(aid):
        return False
    left = [a for a in agents if str(a.get("id")) != str(aid)]
    if len(left) == len(agents) or not left:
        return False
    reg["agents"] = left
    save_registry(reg)
    return True


def set_active(aid: str) -> bool:
    reg = load_registry()
    if not find_agent(reg, aid):
        return False
    reg["active"] = str(aid)
    save_registry(reg)
    return True


def update_agent(aid: str, patch: dict) -> dict:
    reg = load_registry()
    a = find_agent(reg, aid)
    if not a:
        return {}
    for k in ("label", "command", "cwd", "env", "note"):
        if k in patch and patch[k] is not None:
            a[k] = patch[k]
    save_registry(reg)
    return a


def cmd_available(command) -> bool:
    """命令的第一个词是否真的存在（绝对路径看文件，否则查 PATH）。"""
    if not command:
        return False
    exe = str(command[0])
    if os.path.isabs(exe) or ("\\" in exe) or ("/" in exe):
        return os.path.isfile(exe)
    return shutil.which(exe) is not None


# ---------------- 自动检测（不用手填路径）----------------

_CODEX_NAMES = ("codex.exe", "codex.cmd", "codex.bat", "codex")
_CODEX_ACP_NAMES = ("codex-acp.exe", "codex-acp.cmd", "codex-acp")
_CODEX_ACP_EXE = ("codex-acp.exe",)


def _search_dirs() -> list:
    home = os.path.expanduser("~")
    la = os.environ.get("LOCALAPPDATA", "")
    ad = os.environ.get("APPDATA", "")
    cands = [
        os.path.join(ad, "npm"),
        os.path.join(home, ".codex", "bin"),
        os.path.join(home, ".codex"),
        os.path.join(home, ".local", "bin"),
        os.path.join(la, "Programs"),
        os.path.join(la, "Microsoft", "WindowsApps"),
        os.path.join(local_root(), "agentlist"),
    ]
    return [d for d in cands if d]


def local_root() -> str:
    """本项目所在的盘根（例如 D:\\）——用来找同盘的 agentlist 等。"""
    root = os.path.splitdrive(os.path.abspath(STATE_DIR))[0] + os.sep
    return root


def _walk_find(dirs, names, max_depth=3, limit=4000) -> str:
    """在若干目录里（限定深度）找匹配某个文件名的可执行文件。"""
    want = {n.lower() for n in names}
    seen = 0
    for base in dirs:
        if not base or not os.path.isdir(base):
            continue
        d0 = base.rstrip("\\/").count(os.sep)
        for root, subdirs, files in os.walk(base):
            if root.count(os.sep) - d0 >= max_depth:
                subdirs[:] = []
            for f in files:
                seen += 1
                if seen > limit:
                    return ""
                if f.lower() in want:
                    return os.path.join(root, f)
    return ""


def _find_dsh_bin() -> str:
    legacy = _read_json(LEGACY_FILE)
    for a in (legacy.get("command") or []):
        if "dsh-acp" in str(a) and os.path.isfile(str(a)):
            return str(a)
    for base in (_search_dirs() + [os.path.join(os.environ.get("APPDATA", ""), "npm")]):
        if not base or not os.path.isdir(base):
            continue
        d0 = base.rstrip("\\/").count(os.sep)
        for root, subdirs, files in os.walk(base):
            if root.count(os.sep) - d0 > 6:
                subdirs[:] = []
                continue
            if root.replace("/", "\\").lower().endswith("node_modules\\dsh-acp\\lib") and "bin.js" in files:
                return os.path.join(root, "bin.js")
    return ""


def detect_all() -> dict:
    """扫描本机，找出 node / npx / codex / codex-acp / dsh-acp / codex-acp 适配器的位置。"""
    node = shutil.which("node") or ""
    if not node:
        node = _walk_find([os.path.join(local_root(), "agentlist"),
                           os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")],
                          ("node.exe",), max_depth=4)
    npx = ""
    if node:
        cand = os.path.join(os.path.dirname(node), "node_modules", "npm", "bin", "npx-cli.js")
        if os.path.isfile(cand):
            npx = cand
    dirs = _search_dirs()
    return {
        "node": node, "npx": npx,
        "codex": shutil.which("codex") or _walk_find(dirs, _CODEX_NAMES),
        "codexAcp": shutil.which("codex-acp") or _walk_find(dirs, _CODEX_ACP_NAMES),
        "codexAcpExe": _walk_find(dirs, _CODEX_ACP_EXE),      # 只认真 exe（.cmd 需要 node 在 PATH）
        "codexAdapter": _find_codex_adapter(),                 # 本地 npm 安装的适配器入口
        "dsh": _find_dsh_bin(),
    }


def _find_codex_adapter() -> str:
    """找本地 npm 安装的 @agentclientprotocol/codex-acp 入口（dist/index.js）。"""
    for base in _search_dirs():
        if not base or not os.path.isdir(base):
            continue
        d0 = base.rstrip("\\/").count(os.sep)
        for root, subdirs, files in os.walk(base):
            if root.count(os.sep) - d0 > 6:
                subdirs[:] = []
                continue
            low = root.replace("/", "\\").lower()
            if low.endswith("node_modules\\@agentclientprotocol\\codex-acp\\dist") and "index.js" in files:
                return os.path.join(root, "index.js")
    return ""


def autofill(reg: dict = None) -> dict:
    """补全「命令不可用」的 agent；另外把 codex-node 从 npx 形态升级成本地适配器。

    可用的一律不动（尊重用户手填），只有两种情况会改：
      ① 命令的第一个词找不到；② codex-node 还在用 npx 形态但本地已经装了适配器。
    """
    reg = reg or load_registry()
    det = detect_all()
    changed = []
    for a in (reg.get("agents") or []):
        aid = str(a.get("id"))
        cur = a.get("command") or []
        if aid == "codex-node" and det.get("node") and det.get("codexAdapter"):
            if (not cmd_available(cur)) or any("npx-cli.js" in str(x) for x in cur):
                a["command"] = [det["node"], det["codexAdapter"]]
                changed.append(aid)
                continue
        if cmd_available(cur):
            continue
        cmd = None
        if aid == "codex-node" and det["node"] and det["npx"]:
            cmd = [det["node"], det["npx"], "-y", "@agentclientprotocol/codex-acp"]
        elif aid == "codex-client" and det.get("codexAcpExe"):
            cmd = [det["codexAcpExe"]]
        elif aid == "dsh" and det["node"] and det["dsh"]:
            cmd = [det["node"], det["dsh"]]
        if cmd:
            a["command"] = cmd
            changed.append(aid)
    if changed:
        save_registry(reg)
    return {"registry": reg, "detected": det, "changed": changed}


# ---------------- 「可用」到底怎么算（按类型看真依赖）----------------

_det_cache = {"t": 0.0, "val": None}


def detect_all_cached(max_age: float = 15.0) -> dict:
    """detect_all 会扫目录，做个 15s 缓存，别每次轮询都扫。"""
    now = time.time()
    if _det_cache["val"] and (now - _det_cache["t"]) < max_age:
        return _det_cache["val"]
    val = detect_all()
    _det_cache["t"] = now
    _det_cache["val"] = val
    return val


def _kind(a: dict) -> str:
    aid = str(a.get("id") or "")
    if aid.startswith("codex-node"):
        return "codex-node"
    if aid.startswith("codex-client"):
        return "codex-client"
    if aid.startswith("dsh"):
        return "dsh"
    return "custom"


def status_of(a: dict, det: dict = None) -> dict:
    """按 agent 类型判断是否真的能用；返回 {available, missing:[原因]}。

    ⚠ 只看 command[0] 存在是不够的：codex-node 的 command[0] 只是 node.exe，
    真正的依赖是 npm 适配器 + Codex CLI；codex-client 依赖 codex-acp / codex 可执行文件。
    """
    det = det or detect_all_cached()
    cmd = a.get("command") or []
    missing = []
    if not cmd:
        missing.append("命令未配置")
    elif not cmd_available(cmd):
        missing.append("命令找不到")
    kind = _kind(a)
    if kind == "codex-node":
        if not det.get("node"):
            missing.append("Node")
        if not det.get("codex"):
            missing.append("Codex CLI")
        if not (det.get("codexAdapter") or det.get("npx")):
            missing.append("适配器")
    elif kind == "codex-client":
        if not (det.get("codexAcp") or det.get("codex")):
            missing.append("codex-acp / Codex CLI")
    elif kind == "dsh":
        if not det.get("dsh"):
            missing.append("dsh-acp")
    uniq = []
    for m in missing:
        if m not in uniq:
            uniq.append(m)
    return {"available": not uniq, "missing": uniq}


def public(a: dict, det: dict = None) -> dict:
    cmd = a.get("command") or []
    st = status_of(a, det)
    return {
        "id": a.get("id"), "label": a.get("label") or a.get("id"),
        "available": st["available"], "missing": st["missing"],
        "builtin": bool(a.get("builtin")),
        "note": a.get("note") or "", "command": cmd,
        "cwd": a.get("cwd") or "", "hasEnv": bool(a.get("env")),
    }
