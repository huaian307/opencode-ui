# -*- coding: utf-8 -*-
"""引擎注册表 —— 把「跟哪个 agent 对话」抽象成可替换的实现。

约定：server.py 里所有跟 agent 有关的语义（/api/* 反代、/healthz 探测、
启动前置检查）都改成先问「当前引擎」，再由引擎实现。默认引擎是 opencode，
所以在没有切换之前，行为与改造前完全一致。

状态：runtime/state/_engine.json  {"engine": "opencode"}
      文件不存在 / 内容非法 / 引擎未注册 → 一律回落到 opencode。

新增引擎时：写一个 backend/engines/<id>.py，实现 base.Engine，
然后在下面的 registry() 里登记即可（server.py 不需要再改）。
"""
from __future__ import annotations

import json
import os
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(HERE)
ROOT = os.path.dirname(BACKEND_DIR)
STATE_DIR = os.path.join(ROOT, "runtime", "state")
ENGINE_STATE_FILE = os.path.join(STATE_DIR, "_engine.json")

DEFAULT_ENGINE = "opencode"
ENV_ENGINE = "OPENCODE_UI_ENGINE"      # 环境变量可临时覆盖当前引擎（联调用）


def registry() -> dict:
    """引擎 id → 类。延迟 import，避免与 server.py 形成循环依赖。

    单个引擎 import 失败不影响其它引擎 —— 免得一个坏模块把整个服务带崩。
    """
    reg = {}
    try:
        from .opencode import OpenCodeEngine
        reg[OpenCodeEngine.id] = OpenCodeEngine
    except Exception:  # noqa: BLE001
        pass
    try:
        from .acp.engine import AcpEngine
        reg[AcpEngine.id] = AcpEngine
    except Exception:  # noqa: BLE001
        pass
    return reg


def list_engines() -> list:
    """已注册的引擎 id（给 /engine/status 用）。"""
    return sorted(registry().keys())


def is_registered(engine_id: str) -> bool:
    return engine_id in registry()


def active_engine_id() -> str:
    """当前引擎 id；任何异常都回落到默认引擎。

    环境变量 OPENCODE_UI_ENGINE 优先于状态文件 —— 方便 `tools/acp_demo.py`
    起一个独立服务做联调，而不影响线上面板用的引擎。
    """
    env = os.environ.get(ENV_ENGINE, "").strip()
    if env and is_registered(env):
        return env
    try:
        with open(ENGINE_STATE_FILE, "r", encoding="utf-8") as fh:
            eid = str(json.load(fh).get("engine") or "").strip()
    except Exception:  # noqa: BLE001
        eid = ""
    return eid if is_registered(eid) else DEFAULT_ENGINE


def set_active_engine_id(engine_id: str) -> bool:
    """切换当前引擎；未注册的 id 一律拒绝。返回是否成功。"""
    engine_id = str(engine_id or "").strip()
    if not is_registered(engine_id):
        return False
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = ENGINE_STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"engine": engine_id}, fh, ensure_ascii=False)
    os.replace(tmp, ENGINE_STATE_FILE)
    return True


_instances = {}
_instances_lock = threading.Lock()


def get_engine(engine_id: str = None):
    """按 id 取引擎实例；id 为空/未注册则取当前（或默认）引擎。

    ⚠ 必须**缓存单例**：引擎（尤其 ACP）持有会话/进程等状态，每个请求新建实例
    会把这些状态丢掉。
    """
    reg = registry()
    eid = engine_id if engine_id in reg else active_engine_id()
    with _instances_lock:
        inst = _instances.get(eid)
        if inst is None:
            inst = reg[eid]()
            _instances[eid] = inst
    return inst


def active_engine():
    return get_engine()
