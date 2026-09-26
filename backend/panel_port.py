# -*- coding: utf-8 -*-
r"""面板端口配置 —— 让「安装出来的副本」和「开发目录里的副本」能同时跑。

默认（开发机、没有配置文件时）：
    8787 面板服务 · 8788 守护进程单实例锁 · 8790 本地音乐服务

⚠ 为什么需要这个文件：端口是**硬编码在代码里**的，两个副本同时在跑时，
  后来者的守护进程一 bind 8788 就失败 → 日志「已有守护进程在运行，退出」→ **连窗口都不开**
  （实测踩到：用户双击桌面上安装版的快捷方式，什么也没发生）。
  安装包由 `init_state.py` 写 `runtime/state/_panel.json`（例如 17887/17888/17990），
  于是安装版和开发版各用各的，互不抢。

配置优先级：命令行参数 > `runtime/state/_panel.json` > 上面的默认值。
（`runtime/state` 是按文件位置算的，所以两个副本天然各读各的。）
"""
from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STATE_DIR = os.path.join(ROOT, "runtime", "state")
PANEL_FILE = os.path.join(STATE_DIR, "_panel.json")

DEFAULTS = {"port": 8787, "lock": 8788, "music": 8790}


def read_ports(defaults: dict = None) -> dict:
    """读 `_panel.json`（安装包写的），坏的/缺的键一律回默认值。"""
    out = dict(defaults or DEFAULTS)
    try:
        with open(PANEL_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh) or {}
        for key in list(out.keys()):
            val = data.get(key)
            if isinstance(val, int) and 1 <= val <= 65535:
                out[key] = val
    except Exception:  # noqa: BLE001  文件不存在/坏掉都算"用默认"
        pass
    return out


def write_ports(ports: dict) -> bool:
    """写 `_panel.json`；**已存在就不动**（重装/用户手改过都不覆盖）。

    返回是否真的写了。
    """
    if os.path.exists(PANEL_FILE):
        return False
    os.makedirs(STATE_DIR, exist_ok=True)
    data = {}
    for key, dev in DEFAULTS.items():
        val = ports.get(key, dev)
        data[key] = int(val) if isinstance(val, int) and 1 <= val <= 65535 else dev
    tmp = PANEL_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh)
    os.replace(tmp, PANEL_FILE)
    return True
