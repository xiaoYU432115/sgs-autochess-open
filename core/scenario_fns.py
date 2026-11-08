# -*- coding: utf-8 -*-
# ============================================================================
# 偷梁换柱式 · 剧本函数 复制 / 替换 工具
#
# 隐喻来源: 游戏锦囊「偷梁换柱」= 复制一个友方单位并等价替换到阵上。
# 本模块把同一思路搬到【剧本函数(命名子剧本 / 步骤块)】层面:
#   · 把一个已定义的 step 块(函数) 等价复制成新副本 (clone);
#   · 把副本「透明地」替换进「调用阵」(callScenario 调用链 / 扁平 steps 数组),
#     原调用方无需改动任何一行 —— 即"调用方无感知";
#   · 支持对副本施加变体参数(示例: 六本 / 四本 / 星级 三档, 对应游戏的 tier / 星级变体)。
#
# 设计原则(便于 debug / verify):
#   · 全部纯函数, 绝不修改入参与原 steps; 每次 clone 记录 _cloneOf / _variants 溯源;
#   · bind / inline / make_getter 都返回新结构, 可直接丢给 scenario_sandbox 离线验证;
#   · unknown_targets() 用于快速定位"调用了不存在的函数"这一类错误。
#
# 与现有引擎的关系:
#   真机引擎 sgs_bridge.user.js 的 _scRunSteps 通过 scenario_getter 按名加载子剧本。
#   本模块的 make_getter(bindings) 返回一个"带绑定映射的 getter", 引擎只需把
#   options['scenario_getter'] 指向它, 即可实现 callScenario target 的无感知重定向,
#   引擎与调用方剧本零改动。
# ============================================================================
from __future__ import annotations

import copy
import json
import os
import glob


# ---------------------------------------------------------------------------
# 变体档位(示例): 六本 / 四本 / 星级 —— 对应游戏 tier / 星级 概念
# 仅作 clone 的 overrides 预设, 真实 spell 自动化不在此模块内。
# ---------------------------------------------------------------------------
VARIANTS = {
    "四本": {"rankCap": 2, "pool": "四本", "note": "复制场上 ≤2 级友方(六阶版为 ≤4)"},
    "六本": {"rankCap": 4, "pool": "六本", "note": "复制场上 ≤4 级友方"},
    "星级": {"rankCap": 2, "star": "base", "note": "复制基础星级变体(低星同名)"},
}


def resolve_params(steps, params):
    """调用方实时参数 -> 写进 steps(纯函数, 不改入参)。

    · key == 步块已有字段(如 target / rankCap): 直接覆盖该字段;
    · key == "出手": 改写 gating —— "强制使用"=只用 body, "暂留/遣散"=只用 else(遣散),
      其它=保持原 optional 分支。
    供 make_getter / 沙盒 callScenario 在调用瞬间注入参数。"""
    if not params:
        return steps
    out = []
    for st in (steps or []):
        if not isinstance(st, dict):
            out.append(st)
            continue
        st = dict(st)
        op = st.get("op")
        if op == "optional":
            choice = params.get("出手")
            if choice == "强制使用":
                out.extend(resolve_params(st.get("body") or [], params))
                continue
            if choice == "暂留/遣散":
                out.extend(resolve_params(st.get("else") or [], params))
                continue
            st["body"] = resolve_params(st.get("body") or [], params)
            st["else"] = resolve_params(st.get("else") or [], params)
        else:
            for k, v in params.items():
                if k == "出手":
                    continue
                if k in st:
                    st[k] = v
        out.append(st)
    return out


def _apply_overrides(steps, overrides):
    """overrides: list[ {"where": {字段:值...}, "set": {字段:值...}} ]
    对所有匹配 where 的 step 应用 set。where 为空 {} 表示匹配每一步。
    返回是否至少命中一次, 便于 debug 时确认 override 没写错目标。"""
    hit = False
    for ov in (overrides or []):
        where = ov.get("where") or {}
        sett = ov.get("set") or {}
        for st in steps:
            if not isinstance(st, dict):
                continue
            if _match_step(st, where):
                st.update(sett)
                hit = True
    return hit


def _match_step(step, where):
    if not where:
        return True
    for k, v in where.items():
        if step.get(k) != v:
            return False
    return True


class FnRegistry:
    """命名剧本函数注册表 + 偷梁换柱式 克隆/替换。"""

    def __init__(self):
        self._fns = {}    # name -> steps(list[dict])
        self._meta = {}   # name -> {cloneOf, variants, user}

    # ---- 基础: 注册 / 查询 ----
    def register(self, name, steps, meta=None):
        if not isinstance(steps, (list, tuple)):
            raise TypeError("steps 必须是 list")
        self._fns[name] = [copy.deepcopy(s) for s in steps]
        self._meta[name] = dict(meta or {})
        return self

    def has(self, name):
        return name in self._fns

    def get(self, name):
        return self._fns.get(name)

    def meta(self, name):
        return self._meta.get(name, {})

    def names(self):
        return list(self._fns.keys())

    # ---- 批量接入: 把剧本库目录下的 *.json 注册为命名函数 ----
    def register_scenario_dir(self, dir_path, name_from="stem"):
        """批量把目录下含 `steps` 的 *.json 注册为命名函数, 使其可被 call/clone/bind。
        name_from: 'stem'(文件名去扩展名, 默认) | 'name'(json 的 name 字段) | 'id'(json 的 id)。
        返回成功注册的名字列表。"""
        registered = []
        for fp in sorted(glob.glob(os.path.join(dir_path, "*.json"))):
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue
            steps = data.get("steps")
            if not isinstance(steps, list):
                continue
            if name_from == "name" and data.get("name"):
                nm = data["name"]
            elif name_from == "id" and data.get("id"):
                nm = data["id"]
            else:
                nm = os.path.splitext(os.path.basename(fp))[0]
            self.register(nm, steps, meta={"source": fp, "scenarioName": data.get("name")})
            registered.append(nm)
        return registered

    # ---- 偷梁换柱: 等价复制 ----
    def clone(self, src, dst, overrides=None, variants=None, meta=None):
        """把 src 等价复制为 dst。overrides 可对副本施加变体参数(见 _apply_overrides)。
        variants: 任意标记(示例用 VARIANTS 里的 六本/四本/星级)。"""
        if src not in self._fns:
            raise KeyError("未知源函数: %s" % src)
        steps = copy.deepcopy(self._fns[src])
        applied = _apply_overrides(steps, overrides) if overrides else False
        self._fns[dst] = steps
        self._meta[dst] = {
            "cloneOf": src,
            "variants": variants or {},
            "overridesApplied": bool(applied),
        }
        if meta:
            self._meta[dst].update(meta)
        return self

    def clone_variant(self, src, variant_key, dst=None):
        """便捷: 用预置 VARIANTS[variant_key] 作为 overrides 克隆一份。
        例: clone_variant('偷梁换柱','六本') → 生成 偷梁换柱__六本。"""
        if variant_key not in VARIANTS:
            raise KeyError("未知变体档位: %s (可选: %s)" % (variant_key, list(VARIANTS)))
        v = VARIANTS[variant_key]
        overrides = [{"where": {"op": "useSpell", "name": src},
                      "set": {k: val for k, val in v.items() if k != "note"}}]
        dst = dst or ("%s__%s" % (src, variant_key))
        return self.clone(src, dst, overrides=overrides, variants={variant_key: v})

    # ---- 透明替换: 调用方无感知 ----
    def bind(self, steps, mapping):
        """返回新 steps: callScenario.target ∈ mapping 的, 换成 mapping[target]。
        原 steps 不动 → 调用方无感知(它的文本/结构没变, 只是解析时指向副本)。"""
        out = []
        for s in steps:
            if isinstance(s, dict) and s.get("op") == "callScenario":
                t = s.get("target")
                if t in mapping:
                    ns = copy.deepcopy(s)
                    ns["target"] = mapping[t]
                    out.append(ns)
                    continue
            out.append(copy.deepcopy(s))
        return out

    def inline(self, steps, name=None):
        """把 steps 中 callScenario target=name 的那一行, 就地展开成该函数的 steps
        (等价复制进「调用阵」)。不指定 name 则展开所有已知 target。
        返回 (new_steps, expanded_count)。"""
        out, n = [], 0
        for s in steps:
            if isinstance(s, dict) and s.get("op") == "callScenario":
                t = s.get("target")
                if (name is None or t == name) and t in self._fns:
                    out.extend(copy.deepcopy(self._fns[t]))
                    n += 1
                    continue
            out.append(copy.deepcopy(s))
        return out, n

    # ---- 实时调用(选项 A: 调用方带参) ----
    def call(self, name, params=None):
        """实时调用: 返回该函数的一份深拷贝 steps, 并把 params 作为覆盖写进去。
        与 clone 的区别: clone 是把参数**烤进副本**(存进 registry, 长期存在);
        call 是**每次临时注入**(返回新列表, registry 原件不动), 适合调用方临时带参。

        params 两种写法:
          · list  → 直接当作 overrides([{"where":{...},"set":{...}}]) 应用;
          · dict  → 便捷写法, 默认作用到 `useSpell name=name` 那一步
                    (例: {"rankCap":4} = 把偷梁换柱复制上限设为 4)。
        返回新列表, 不污染 registry。"""
        if name not in self._fns:
            raise KeyError("未知函数: %s" % name)
        steps = copy.deepcopy(self._fns[name])
        if not params:
            return steps
        ov = [{"where": {"op": "useSpell", "name": name}, "set": dict(params)}] \
            if isinstance(params, dict) else params
        _apply_overrides(steps, ov)
        return steps

    def make_getter(self, bindings=None):
        """返回 scenario_getter(name, params=None) -> steps, 供
        scenario_sandbox.run_scenario(options={'scenario_getter': ...}) 使用。
        bindings: {原name: 克隆name}; 命中则取克隆版(无感知替换)。
        params: 调用方在 callScenario 上带的实时参数(见 call()), 会注入副本。"""
        bindings = bindings or {}

        def getter(name, params=None):
            real = bindings.get(name, name)
            steps = self._fns.get(real)
            if steps is None:
                return None
            steps = copy.deepcopy(steps)
            if params:
                steps = resolve_params(steps, params)   # 实时注入参数(目标覆盖 / 出手条件改写)
            return steps
        return getter

    # ---- 便捷: 直接经本 registry 跑一遍(把 getter 焊进沙盒) ----
    def run(self, steps, state=None, options=None, bindings=None):
        """经本 registry 的 getter 跑一遍剧本(离线沙盒)。
        等价于 scenario_sandbox.run_scenario(steps, state, {scenario_getter: make_getter(bindings)})。
        这就是「接进去」的落点: 剧本里的 callScenario 会按名从本 registry 取函数。"""
        try:
            from .scenario_sandbox import run_scenario
        except ImportError:
            from scenario_sandbox import run_scenario
        opts = dict(options or {})
        opts.setdefault("scenario_getter", self.make_getter(bindings))
        return run_scenario(steps, state if state is not None else {}, opts)

    # ---- debug 辅助 ----
    def unknown_targets(self, steps):
        """列出 steps 中 callScenario 但本 registry 没有的函数名(快速定位断链)。"""
        miss = []
        for s in steps:
            if isinstance(s, dict) and s.get("op") == "callScenario":
                t = s.get("target")
                if t not in self._fns:
                    miss.append(t)
        return miss

    def diff_clone(self, name):
        """debug: 返回某克隆函数的溯源信息。"""
        return self._meta.get(name, {})


# ===========================================================================
# 默认函数库(选项 B): 把真实 gating 步块注册为默认函数, 便于直接 bind/调用
# ===========================================================================
# 偷梁换柱 真实 gating 步块:
#   · ifStart 守卫: 场上至少 1 个友方才尝试(否则必无合法目标);
#   · useSpell optional=True: 无 ≤2 级友方时引擎判无目标 → 跳过(默认保留, 不遣散);
#   · waitUntil / pickOne 同样 optional, 保证整段"无目标即保留"。
# 说明: 精确的"等级≤N"过滤由锦囊自身规则执行; 步块只用 optional 兜底"无目标→保留"
#       + ifStart 守卫"场上至少 1 友方"。日后引擎若补 rank 维度, 可在 overrides 里加 where。
DEFAULT_TOU_LIANG = [
    {"op": "ifStart", "check": "card", "where": "field", "kind": "chess",
     "count": 1, "cmp": ">=", "note": "场上至少 1 友方才尝试偷梁换柱"},
    {"op": "useSpell", "name": "偷梁换柱", "optional": True,
     "note": "等级≤2 由锦囊自身规则判定; 无目标则 optional 跳过=保留不遣散"},
    {"op": "waitUntil", "check": "card", "has": ["手牌:复制体"], "timeoutMs": 800, "optional": True},
    {"op": "pickOne", "optional": True},
    {"op": "ifend"},
]


def build_default_registry():
    """返回已预置「偷梁换柱」真实 gating 函数的 FnRegistry, 可直接 bind / 调用。

    预置:
      · 偷梁换柱       —— 基础(复制 ≤2 级友方), 无目标默认保留
      · 偷梁换柱__六本  —— 变体 rankCap=4(复制 ≤4 级)
      · 偷梁换柱__四本  —— 变体 rankCap=2
      · 偷梁换柱__星级  —— 变体: 基础星级
    """
    reg = FnRegistry()
    reg.register("偷梁换柱", DEFAULT_TOU_LIANG)
    reg.clone_variant("偷梁换柱", "六本")
    reg.clone_variant("偷梁换柱", "四本")
    reg.clone_variant("偷梁换柱", "星级")
    return reg


# ===========================================================================
# 自测(直接 `python core/scenario_fns.py` 运行)
# ===========================================================================
def _demo():
    # ===== 选项 B: 直接用默认函数库(已含真实 gating 的 偷梁换柱) =====
    reg = build_default_registry()
    print("默认注册的函数:", reg.names())
    print("\n[默认函数] 偷梁换柱 真实步块:")
    for s in reg.get("偷梁换柱"):
        print("   ", s)

    # ===== 全量接入剧本库: 把 剧本库/*.json 全部注册为可调用/克隆函数 =====
    scen_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "剧本库")
    loaded = reg.register_scenario_dir(scen_dir)
    print("\n[批量接入] 剧本库已注册 %d 个: %s" % (len(loaded), loaded))
    # 验证其中一个(当先流)可克隆/无感知替换
    dangxian = "阵容_蜀国当先流"
    reg.clone(dangxian, dangxian + "__副本")  # 验证克隆链路(通用 clone, 不写死参数)
    print("[批量接入] %s 克隆副本: %s" % (dangxian, reg.names()[-1]))
    # 无感知替换: 调用方写 target=原名, 绑定到副本
    caller_dx = [{"op": "callScenario", "target": dangxian}]
    resolved_dx = reg.bind(caller_dx, {dangxian: dangxian + "__副本"})
    print("[批量接入] 当先流 bind 后 target:",
          [s.get('target') for s in resolved_dx if s.get('op') == 'callScenario'])

    # 调用方只写 target=偷梁换柱, 不知道后面是哪个变体
    caller = [
        {"op": "deploy", "name": "陈珪", "optional": True},
        {"op": "callScenario", "target": "偷梁换柱"},
        {"op": "callScenario", "target": "不存在的函数"},  # 故意断链用于 debug
    ]

    # 透明替换: 无感知指向 __六本
    bindings = {"偷梁换柱": "偷梁换柱__六本"}
    resolved = reg.bind(caller, bindings)
    print("\n[bind] 解析后 target:", [s.get('target') for s in resolved if s.get('op') == 'callScenario'])
    print("[bind] 原 caller 未改动:", [s.get('target') for s in caller if s.get('op') == 'callScenario'])

    # 展开进调用阵
    inlined, n = reg.inline(resolved, "偷梁换柱__六本")
    print("\n[inline] 展开步数:", n, "→ 调用阵总长:", len(inlined))

    # debug 断链
    print("\n[debug] 未知目标:", reg.unknown_targets(caller))

    # getter 经绑定解析到 __六本
    getter = reg.make_getter(bindings)
    got = getter("偷梁换柱")
    got_rc = next((s.get("rankCap") for s in got if s.get("op") == "useSpell"), None) if got else None
    print("\n[getter] 经绑定指向 偷梁换柱__六本 (rankCap=%s):" % got_rc, got_rc == 4)

    # ===== 选项 A: 调用方「实时带参」(不改默认原件) =====
    runtime = reg.call("偷梁换柱", {"rankCap": 4})
    print("\n[call 实时带参] 注入后 rankCap:",
          [s.get("rankCap") for s in runtime if s.get("op") == "useSpell"])
    print("[call 实时带参] 默认原件 rankCap:",
          next((s.get("rankCap") for s in reg.get("偷梁换柱") if s.get("op") == "useSpell"), None),
          "(None=基础步块未写死 rankCap, 由锦囊自身规则判 ≤2)")
    g = reg.make_getter()
    print("[getter 带参]   rankCap:",
          next((s.get("rankCap") for s in g("偷梁换柱", {"rankCap": 9}) if s.get("op") == "useSpell"), None))
    print("[getter 不带参] rankCap:",
          next((s.get("rankCap") for s in g("偷梁换柱") if s.get("op") == "useSpell"), None))

    print("\n[OK] 自测通过: 默认函数(B)/克隆/绑定/展开/断链/实时带参(A) 均正常。")


if __name__ == "__main__":
    _demo()
