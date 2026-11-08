# -*- coding: utf-8 -*-
"""克隆/替换函数注册表 —— 应用级单例(偷梁换柱式剧本函数库)。

构建一次, 全局复用: ui/app.py 启动时与 ui/scenario_tab.py 都通过 get_registry()
取同一份实例, 避免重复加载 剧本库。返回的 FnRegistry 已预置:
  · 偷梁换柱(基础) + 变体 __六本/__四本/__星级(见 core/scenario_fns.build_default_registry);
  · 剧本库/*.json 全部注册为可调用/克隆的命名函数。

设计要点(稳定性): 任何一步失败都不抛异常到调用方 —— 返回尽量可用的注册表。
"""
from __future__ import annotations

import os

from .scenario_fns import build_default_registry

_REGISTRY = None


def get_registry():
    """返回应用级单例 FnRegistry; 首次调用时构建并缓存。"""
    global _REGISTRY
    if _REGISTRY is not None:
        return _REGISTRY
    reg = build_default_registry()
    # 批量接入剧本库(19 个阵容剧本). 失败不致命: 至少保留预置的 偷梁换柱 等默认函数。
    _dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "剧本库")
    try:
        loaded = reg.register_scenario_dir(_dir)
        reg._fn_loaded_scenarios = loaded
    except Exception:
        reg._fn_loaded_scenarios = []
    # 批量接入 49 锦囊 函数库(实际注册 48 个, 偷梁换柱 已由默认注册表预置). 失败不致命。
    try:
        from .spell_lib import build_spell_functions
        reg._fn_loaded_spells = build_spell_functions(reg)
    except Exception:
        reg._fn_loaded_spells = []
    _REGISTRY = reg
    return _REGISTRY
