# -*- coding: utf-8 -*-
r"""opencode-ui 守护进程（无窗口运行）

职责：
  1. 确保本地面板服务（server.py）在 127.0.0.1:8787 上活着，挂了就拉起来；
  2. 检测 OpenCode 桌面版启动 → 打开面板窗口；
  3. 面板窗口用【独立浏览器 profile】打开（完全独立实例，不干扰日常浏览器）；
  4. 关掉面板窗口 → 一并关掉 OpenCode（可用 --no-kill 或 no-kill 文件关闭此行为）；
  5. 面板窗口开 → 任务栏**自动隐藏**（鼠标贴底边才浮现），关 → 恢复**常驻可见**。
     （只有常驻守护负责这件事：--once 自己马上退出，没人能把它恢复回来。）

"面板窗口还开着吗" 怎么判断：
  不靠进程句柄（会漏、会误判），而是靠**页面心跳** —— 页面每 5 秒 POST 一次
  /heartbeat，窗口一关心跳即停。刚开窗后有一段时间等页面加载，用一个 settle 窗口兜住。
  兜底：如果心跳没有但浏览器确实在跑（例如页面还是旧版、没发心跳），仍然算"开着"。

用法：
    pythonw watch.py               # 常驻（由 launch.vbs / 启动文件夹调用）
    python  watch.py --once        # 只做一次：确保服务 + 需要就开窗口，然后退出
    python  watch.py --no-kill     # 关窗口时不关 OpenCode
    python  watch.py --restore-taskbar   # 只把任务栏恢复常驻可见，然后退出（stop.bat 兜底）
"""

from __future__ import annotations

import atexit
import ctypes
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RUNTIME_DIR = os.path.join(ROOT, "runtime")
LOGS_DIR = os.path.join(RUNTIME_DIR, "logs")
HOST, PORT = "127.0.0.1", 8787
URL = f"http://{HOST}:{PORT}"
ALIVE_URL = f"{URL}/alive"
LOCK_PORT = 8788
os.makedirs(LOGS_DIR, exist_ok=True)
LOG = os.path.join(LOGS_DIR, "watch.log")

# 任务栏联动：同目录的 taskbar.py（缺失/出错都不影响守护主流程）
try:
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import taskbar as _taskbar
except Exception:  # noqa: BLE001
    _taskbar = None

TICK_SECONDS = 0.4           # 面板心跳轮询间隔（很便宜：一次本机 HTTP）—— 决定"发现关窗"有多快
POLL_SECONDS = 2.0           # OpenCode 进程检查间隔（较贵：要起一次 tasklist）
GRACE_SECONDS = 1.0          # 关窗后留给"反悔"的时间（固定 1 秒，无随机）
SETTLE_SECONDS = 40.0        # 刚开窗后，等页面加载并发第一个心跳的时间
MINIMIZE_DELAY = 1.5         # 开完面板窗口后，等它露面再把 OpenCode 最小化
OPENCODE_IMAGES = ("OpenCode.exe", "opencode-cli.exe", "opencode.exe")

PROFILE_DIR = os.path.join(RUNTIME_DIR, "browser-profile")

# 关掉面板窗口时是否一并关掉 OpenCode。
# 想关掉这个行为：命令行加 --no-kill，或在 runtime/ 放一个名为 no-kill 的空文件。
KILL_OPENCODE = ("--no-kill" not in sys.argv) and not os.path.exists(os.path.join(RUNTIME_DIR, "no-kill"))

# 可选：OpenCode 退出时是否也关掉面板窗口。默认关，避免"重启 OpenCode 时窗口闪一下"。
CLOSE_WINDOW_WHEN_OPENCODE_EXITS = False

BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Google\Chrome\Application\chrome.exe"),
]

DETACHED = 0x00000008 | 0x08000000          # DETACHED_PROCESS | CREATE_NO_WINDOW
NO_WINDOW = 0x08000000


def log(msg: str) -> None:
    try:
        ts = time.strftime("%Y-%m-%d %H:%M:%S") + f".{int(time.time() * 1000) % 1000:03d}"
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{ts}  {msg}\n")
    except Exception:
        pass


def ps(command: str) -> str:
    try:
        return subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True, text=True, timeout=25, creationflags=NO_WINDOW,
        ).stdout or ""
    except Exception as exc:  # noqa: BLE001
        log(f"PowerShell 调用失败: {exc}")
        return ""


# ---------------- 任务栏联动 ----------------
# 面板开 → 任务栏自动隐藏（鼠标贴底边才浮现）；面板关 → 常驻可见。
# 只在"开关事件"上动作；"是不是我们藏的"由 taskbar.py 的标记文件记账，
# 所以守护重启/被强杀都不会覆盖用户自己的任务栏设置。

def taskbar_hidden(hidden: bool) -> None:
    if _taskbar is None:
        return
    try:
        if _taskbar.set_hidden(hidden):
            log(f"任务栏 → {'自动隐藏' if hidden else '常驻可见'}")
        else:
            log("任务栏联动：找不到任务栏窗口")
    except Exception as exc:  # noqa: BLE001
        log(f"任务栏联动失败: {exc}")


def restore_taskbar_if_marked() -> None:
    if _taskbar is None:
        return
    try:
        if _taskbar.restore_if_marked():
            log("任务栏 → 常驻可见（兜底恢复）")
    except Exception as exc:  # noqa: BLE001
        log(f"任务栏兜底恢复失败: {exc}")


# ---------------- 面板服务 ----------------

def port_open(host: str = HOST, port: int = PORT) -> bool:
    with socket.socket() as s:
        s.settimeout(0.6)
        return s.connect_ex((host, port)) == 0


def ensure_server() -> bool:
    if port_open():
        return False
    log("面板服务不在，启动 server.py")
    subprocess.Popen(
        [sys.executable, os.path.join(HERE, "server.py")],
        cwd=ROOT, creationflags=DETACHED,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(40):
        time.sleep(0.5)
        if port_open():
            log("面板服务已就绪")
            return True
    log("面板服务启动超时")
    return False


def panel_alive() -> bool:
    """页面心跳还在吗（= 面板窗口还开着）。"""
    try:
        with urllib.request.urlopen(ALIVE_URL, timeout=3) as r:
            return bool(json.loads(r.read().decode("utf-8")).get("alive"))
    except Exception:
        return False


def upstream_ready() -> bool:
    """OpenCode 后端是否已经可用（代理的 /healthz 会去探它）。"""
    try:
        with urllib.request.urlopen(f"{URL}/healthz", timeout=4) as r:
            return bool(json.loads(r.read().decode("utf-8")).get("ok"))
    except Exception:
        return False


def wait_upstream(timeout: float = 45.0) -> bool:
    """等 OpenCode 后端就绪再开窗，避免开出一个"连不上"的页面。
    等待期间若 OpenCode 退出了，就直接放弃开窗。"""
    if upstream_ready():
        return True
    log("OpenCode 后端尚未就绪，等它起来再开窗…")
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(1.0)
        if upstream_ready():
            log(f"后端已就绪（等待 {time.time() - t0:.0f}s）")
            return True
        if not opencode_running():
            log("等待期间 OpenCode 已退出，取消开窗")
            return False
    log(f"等待后端超时（{timeout:.0f}s），仍然开窗（页面会自动重连）")
    return False


# ---------------- OpenCode ----------------

def opencode_running() -> bool:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq OpenCode.exe", "/NH"],
            capture_output=True, text=True, timeout=15, creationflags=NO_WINDOW,
        ).stdout or ""
        return "opencode.exe" in out.lower()
    except Exception as exc:  # noqa: BLE001
        log(f"检测 OpenCode 失败: {exc}")
        return False


def kill_opencode() -> None:
    log("关闭 OpenCode（含后台服务）")
    for image in OPENCODE_IMAGES:
        subprocess.run(["taskkill", "/IM", image, "/F", "/T"],
                       capture_output=True, creationflags=NO_WINDOW)


# ---------------- 最小化 OpenCode 自己的窗口 ----------------

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
SW_MINIMIZE = 6
_user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
_user32.FindWindowW.restype = wintypes.HWND


def opencode_pids() -> set[int]:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq OpenCode.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=15, creationflags=NO_WINDOW,
        ).stdout or ""
    except Exception as exc:  # noqa: BLE001
        log(f"取 OpenCode PID 失败: {exc}")
        return set()
    pids = set()
    for raw in out.splitlines():
        line = raw.strip()
        if not line.startswith('"'):
            continue
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[0].lower() == "opencode.exe":
            try:
                pids.add(int(parts[1]))
            except ValueError:
                pass
    return pids


def minimize_opencode() -> int:
    """把 OpenCode 桌面版的主窗口最小化（按 PID 归属找窗口，避免误伤别的窗口）。"""
    pids = opencode_pids()
    if not pids:
        log("没有 OpenCode.exe 进程，跳过最小化")
        return 0
    found: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _lparam):
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if (pid.value in pids
                and _user32.IsWindowVisible(hwnd)
                and _user32.GetWindowTextLengthW(hwnd) > 0):
            found.append(hwnd)
        return True

    _user32.EnumWindows(_cb, 0)
    for hwnd in found:
        _user32.ShowWindow(hwnd, SW_MINIMIZE)
    log(f"已最小化 OpenCode 窗口 {len(found)} 个")
    return len(found)


# ---------------- 面板窗口 ----------------

_profile_cache = {"t": 0.0, "val": False}


def profile_in_use(max_age: float = 5.0) -> bool:
    """专属 profile 是否已有浏览器在跑。

    这是"兜底"判据（比心跳慢、比心跳贵），只在心跳不可信时才需要：
      · 页面还是旧版、不发心跳；
      · 浏览器最小化时 Chromium 会节流定时器，心跳可能断档。
    加 5 秒缓存，避免每次轮询都起一个 PowerShell。
    注意必须限定进程名，否则执行这段 PowerShell 的自己会因命令行含该路径而被误数。
    """
    now = time.time()
    if now - _profile_cache["t"] < max_age:
        return _profile_cache["val"]
    out = ps("(Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'msedge|chrome' "
             "-and $_.CommandLine -like '*" + PROFILE_DIR + "*' } | Measure-Object).Count")
    out = out.strip()
    val = out.isdigit() and int(out) > 0
    _profile_cache["t"] = now
    _profile_cache["val"] = val
    return val


def pid_alive(pid: int) -> bool:
    """便宜的进程存活检查（tasklist，约 60ms），用于"采纳的外部窗口"这种没有句柄的情况。"""
    if not pid:
        return False
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                             capture_output=True, text=True, timeout=10,
                             creationflags=NO_WINDOW).stdout or ""
        return str(pid) in out
    except Exception:
        return False


def panel_main_pid() -> int:
    """面板专属 profile 的浏览器主进程 PID（取创建最早的那个），用于廉价存活检查。"""
    out = ps("Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'msedge|chrome' "
             "-and $_.CommandLine -like '*" + PROFILE_DIR + "*' } "
             "| Sort-Object CreationDate | Select-Object -First 1 -ExpandProperty ProcessId")
    out = out.strip()
    return int(out) if out.isdigit() else 0


def close_window() -> None:
    ps("Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'msedge|chrome' "
       "-and $_.CommandLine -like '*" + PROFILE_DIR + "*' } "
       "| ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")


def open_window():
    wait_upstream()                          # ★ 先等后端可用，避免开出连不上的页面
    os.makedirs(PROFILE_DIR, exist_ok=True)
    for exe in BROWSERS:
        if os.path.isfile(exe):
            taskbar_hidden(True)             # 唤起面板 → 任务栏自动隐藏
            log(f"打开面板窗口: {exe}")
            proc = subprocess.Popen(
                [exe, f"--app={URL}", f"--user-data-dir={PROFILE_DIR}",
                 "--start-maximized", "--no-first-run", "--no-default-browser-check",
                 "--disable-background-mode", "--disable-sync",
                 "--disable-features=Translate,MediaRouter"],
                creationflags=NO_WINDOW,
            )
            # 等面板窗口先露面，再把 OpenCode 自己的窗口最小化
            time.sleep(MINIMIZE_DELAY)
            try:
                minimize_opencode()
            except Exception as exc:  # noqa: BLE001
                log(f"最小化 OpenCode 失败: {exc}")
            return proc
    log("没找到 Edge/Chrome，退回默认浏览器打开")
    taskbar_hidden(True)                     # 唤起面板 → 任务栏自动隐藏
    os.startfile(URL)  # noqa: S606
    time.sleep(MINIMIZE_DELAY)
    try:
        minimize_opencode()
    except Exception as exc:  # noqa: BLE001
        log(f"最小化 OpenCode 失败: {exc}")
    return None


# ---------------- 主流程 ----------------

def main() -> int:
    if "--restore-taskbar" in sys.argv:
        # stop.bat 兜底：守护是被强制结束的，跑不到退出钩子，这里补一刀
        restore_taskbar_if_marked()
        return 0

    once = "--once" in sys.argv
    log(f"守护进程启动（关窗联动关 OpenCode: {KILL_OPENCODE}）")

    if not once:
        lock = socket.socket()
        try:
            lock.bind((HOST, LOCK_PORT))
            lock.listen(1)
        except OSError:
            log("已有守护进程在运行，退出")
            return 0
        atexit.register(restore_taskbar_if_marked)   # 守护退出（正常路径）时兜底恢复

    ensure_server()

    # 判断窗口是否开着：心跳优先；没心跳时用浏览器进程兜底（兼容旧页面/最小化被节流）
    alive = panel_alive()
    proc = None                                  # 我们自己拉起的窗口进程句柄
    adopted_pid = 0                              # 采纳的外部窗口：主进程 PID（廉价存活判据）
    if not alive and profile_in_use():
        alive = True
        adopted_pid = panel_main_pid()
        log(f"启动时检测到已有面板窗口（外部打开，主进程 PID={adopted_pid}）")
    opened_at = 0.0
    log(f"启动时面板窗口: {'开着' if alive else '没开'}")
    if alive:
        taskbar_hidden(True)                 # 接管已开着的面板 → 任务栏跟着隐藏
    else:
        restore_taskbar_if_marked()          # 上次留下"隐藏"标记而面板已关 → 自愈恢复

    running = opencode_running()

    if once:
        if not alive:
            open_window()
        return 0

    if running and not alive:
        proc = open_window()
        opened_at = time.time()
        alive = True

    closed_at = None
    skip_open = False                            # 用户主动关窗后，别自动重开
    was_open = alive
    last_oc = time.time()

    while True:
        # 反悔期内把 tick 收窄到"刚好够判定"，让联动准点执行（误差 < 50ms）
        nap = TICK_SECONDS
        if closed_at is not None:
            nap = max(0.05, min(TICK_SECONDS, GRACE_SECONDS - (time.time() - closed_at)))
        time.sleep(nap)
        now = time.time()
        if not port_open():
            ensure_server()

        # ---- ① 面板窗口还开着吗 ----
        if opened_at and now - opened_at < SETTLE_SECONDS:
            fresh = True                         # 刚开窗，等页面加载
        else:
            fresh = panel_alive()
            if not fresh and proc is not None and proc.poll() is None:
                fresh = True                     # 我们拉起的浏览器进程还活着（最准）
            elif not fresh and proc is None and adopted_pid and pid_alive(adopted_pid):
                fresh = True                     # 采纳的外部窗口：用便宜的 PID 存活检查兜底
        if proc is not None and proc.poll() is not None:
            proc = None                          # 句柄已失效

        # ---- ②′ 由关 → 开（也可能是外部手动打开的）→ 任务栏跟着隐藏 ----
        if fresh and not was_open:
            taskbar_hidden(True)

        # ---- ② 由开 → 关：进入反悔期 ----
        if was_open and not fresh:
            # 必须用"发现关闭的那一刻"，不能用本轮循环开头的时间戳：
            # 本轮可能已经花了上百毫秒到 1 秒（进程兜底要起 PowerShell），
            # 用旧时间戳会让反悔期瞬间"超时"，等于没有反悔期。
            closed_at = time.time()
            log(f"面板窗口已关闭 → 反悔 {GRACE_SECONDS:.1f} 秒")

        # ---- ③ 反悔期（固定时长，无随机；用当下时间判定，保证实际不短于设定值）----
        if closed_at is not None:
            if fresh:
                log("窗口又开回来了 → 取消关闭")
                closed_at = None
            elif time.time() - closed_at >= GRACE_SECONDS:
                taskbar_hidden(False)            # 面板确实关了 → 任务栏恢复常驻可见
                if KILL_OPENCODE:
                    kill_opencode()
                closed_at = None
                skip_open = True                 # 这是"我要退出"的意图
                log("反悔期结束 → 已执行联动")

        # ---- ④ OpenCode 状态（较贵，降频检查）----
        if now - last_oc >= POLL_SECONDS:
            running = opencode_running()
            last_oc = now
            if not running:
                skip_open = False                # OpenCode 已退出 → 解除抑制

        # ---- ⑤ OpenCode 在跑但窗口没开 → 开窗 ----
        # 关键：反悔期内（closed_at 未清）绝不能抢着重开，否则会"关掉就被重开"、
        # 反悔期被新窗口的心跳取消，kill 永远执行不到 —— 必须等反悔期走完。
        if running and not fresh and not skip_open and closed_at is None:
            log("检测到 OpenCode 在运行且窗口未开 → 打开面板窗口")
            proc = open_window()
            opened_at = time.time()
            fresh = True

        # ---- ⑥ 可选：OpenCode 退出时也关掉窗口（默认关，见模块顶部常量）----
        if CLOSE_WINDOW_WHEN_OPENCODE_EXITS and was_open and not running:
            close_window()
            fresh = False

        was_open = fresh


if __name__ == "__main__":
    raise SystemExit(main())
