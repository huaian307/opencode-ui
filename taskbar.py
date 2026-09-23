# -*- coding: utf-8 -*-
r"""任务栏自动隐藏控制（面板开 = 隐藏，面板关 = 常驻可见）

机制：Shell 的 AppBar 接口 `SHAppBarMessage(ABM_SETSTATE)`。
实测（2026-09-23 本机 Win11）：**即时生效，不需要重启 explorer**。

⚠ 常量坑：`ABM_GETSTATE = 0x4`
   （**0x2 是 `ABM_QUERYPOS`**，拿它当 GETSTATE 用会读回垃圾值 —— 本项目为此把"关"误判成"开"了一次）

标记文件 `_taskbar.hidden`：只有"**我们**把它藏起来"时才存在。
守护 / 停止脚本靠它判断"要不要恢复"，从而**不会覆盖用户自己**的任务栏设置。

命令行（输出一律纯 ASCII，避开 GBK 控制台）：
    python taskbar.py status      # 只读：打印当前状态
    python taskbar.py hide        # 自动隐藏（写标记）
    python taskbar.py show        # 常驻可见（删标记）
    python taskbar.py restore     # 只有标记存在时才恢复常驻可见
"""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
MARKER = os.path.join(HERE, "_taskbar.hidden")

ABM_GETSTATE = 0x00000004
ABM_SETSTATE = 0x0000000A
ABS_AUTOHIDE = 0x00000001
ABS_ALWAYSONTOP = 0x00000002


class APPBARDATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uCallbackMessage", wintypes.UINT),
        ("uEdge", wintypes.UINT),
        ("rc", wintypes.RECT),
        ("lParam", wintypes.LPARAM),
    ]


_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
_user32.FindWindowW.restype = wintypes.HWND
_shell32 = ctypes.WinDLL("shell32", use_last_error=True)
_shell32.SHAppBarMessage.argtypes = [wintypes.UINT, ctypes.POINTER(APPBARDATA)]
_shell32.SHAppBarMessage.restype = wintypes.UINT


def _taskbar_hwnd():
    hwnd = _user32.FindWindowW("Shell_TrayWnd", None)
    if not hwnd:                       # 个别系统主任务栏类名是 System_TrayWnd
        hwnd = _user32.FindWindowW("System_TrayWnd", None)
    return hwnd


def get_hidden() -> bool:
    """当前任务栏是否处于自动隐藏（只读）。"""
    abd = APPBARDATA()
    abd.cbSize = ctypes.sizeof(APPBARDATA)
    abd.hWnd = _taskbar_hwnd()
    return bool(_shell32.SHAppBarMessage(ABM_GETSTATE, ctypes.byref(abd)) & ABS_AUTOHIDE)


def _mark(hidden: bool) -> None:
    try:
        if hidden:
            with open(MARKER, "w", encoding="utf-8") as fh:
                fh.write("hidden\n")
        elif os.path.exists(MARKER):
            os.remove(MARKER)
    except OSError:
        pass


def set_hidden(hidden: bool) -> bool:
    """设任务栏自动隐藏（True）/ 常驻可见（False），并同步标记文件。"""
    hwnd = _taskbar_hwnd()
    if not hwnd:
        return False
    abd = APPBARDATA()
    abd.cbSize = ctypes.sizeof(APPBARDATA)
    abd.hWnd = hwnd
    abd.lParam = ABS_AUTOHIDE if hidden else ABS_ALWAYSONTOP
    _shell32.SHAppBarMessage(ABM_SETSTATE, ctypes.byref(abd))
    _mark(hidden)
    return True


def restore_if_marked() -> bool:
    """只有"我们藏过"（标记存在）才恢复常驻可见；返回是否真的恢复过。"""
    if os.path.exists(MARKER):
        set_hidden(False)
        return True
    return False


def _main(argv) -> int:
    cmd = (argv[1] if len(argv) > 1 else "status").strip().lower()
    if cmd == "hide":
        ok = set_hidden(True)
        print("[OK] taskbar hidden" if ok else "[BAD] taskbar window not found")
        return 0 if ok else 1
    if cmd == "show":
        ok = set_hidden(False)
        print("[OK] taskbar visible" if ok else "[BAD] taskbar window not found")
        return 0 if ok else 1
    if cmd == "restore":
        if restore_if_marked():
            print("[OK] restored (marker found)")
        else:
            print("[SKIP] no marker -> left user setting untouched")
        return 0
    print("hidden=%s marked=%s hwnd=%s" % (
        get_hidden(), os.path.exists(MARKER), bool(_taskbar_hwnd())))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
