# -*- coding: utf-8 -*-
"""持久化记录各工具/自检的最后执行状态, 让 UI 能显示"上次体检是哪天"。

只保存轻量元信息(时间/退出码/摘要), 不保存完整输出 —— 日志仍然走各自的文本/日志框。
文件: data/tool_state.json
"""
import json
import os
import time

from core.config import DATA_DIR
from core import silence as _SIL

STATE_FILE = os.path.join(DATA_DIR, "tool_state.json")


def _ensure_dir():
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)


def load():
    """返回整个状态字典, 不存在则返回空。"""
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save(state):
    """原子写回状态文件。"""
    _ensure_dir()
    tmp = STATE_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATE_FILE)
    except Exception:
        try:
            os.remove(tmp)
        except Exception:
            _SIL.note("core/tool_state.py:42", "os.remove(tmp)")


def mark(tool_key, exit_code=0, summary="", extra=None):
    """记录某工具最近一次执行结果。"""
    state = load()
    state[tool_key] = {
        "ts": int(time.time()),
        "exit": int(exit_code),
        "summary": str(summary or ""),
        "extra": extra or {},
    }
    # 限制只保留最近 100 条 key, 避免无限增长
    if len(state) > 100:
        for k in sorted(state, key=lambda x: state[x].get("ts", 0))[:len(state) - 100]:
            del state[k]
    save(state)


def get(tool_key):
    """取单个工具状态。"""
    return load().get(tool_key)


def fmt(ts):
    """时间戳 → 可读字符串。"""
    if not ts:
        return "从未"
    try:
        return time.strftime("%m-%d %H:%M", time.localtime(ts))
    except Exception:
        return str(ts)
