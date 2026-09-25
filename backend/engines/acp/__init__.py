# -*- coding: utf-8 -*-
"""ACP 引擎 —— 用 Agent Client Protocol 驱动外部 agent（Codex / Claude / dsh…）。

分层：
    process.py  —— 只管 stdio 子进程 + JSONL 分帧（把一行 JSON 变成 dict 交回调）
    client.py   —— 在 process 之上做 JSON-RPC 2.0 的请求/响应配对与双向请求

本包只依赖 Python 标准库，不需要 Node / npm。
"""
