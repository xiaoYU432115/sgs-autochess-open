# -*- coding: utf-8 -*-
# ============================================================================
# 剧本结构层（想法 A，flat-native 实现）
#
# 设计要点（深度思考后的更优解，区别于"另存树模型"那种方案）：
#   引擎 sgs_bridge.user.js 的 _scRunSteps 本身消费的就是【扁平 steps + 配对标记】
#   （ifstart/ifend/else/loopstart/loopend/break/continue），_scMatch/_scElseOf
#   用【深度计数】找配对。所以：
#     ① 存储 / 下发给页面的，仍是扁平 steps —— 引擎零改动，老剧本 100% 兼容；
#     ② 编辑期只做"配对感知"的运算（缩进、着色、配对高亮、缩进/凸排、整块删复制），
#        结构在编辑期物理上不可能存错（移动/缩进只在合法块边界生效）；
#     ③ 树（flat_to_tree）只作为【只读派生视图】给离线沙盒(B)用，不另存，
#        从而消除"树 ↔ flat 双源漂移"这一隐性风险。
#
#   结构化 op 规范严格对齐桥 _scOpNorm：
#     if→ifstart, loop→loopstart；其余小写。结构 op 集合：
#     ifstart / ifend / else / loopstart / loopend / break / continue
#   注：引擎只认 else、不认 elseif；elseif 用"else 里套 if"无损模拟（见 tree_to_flat）。
# ============================================================================

# ---- 结构 op 集合 ----
SCENARIO_OPEN = ("ifstart", "loopstart")
SCENARIO_CLOSE = ("ifend", "loopend")
SCENARIO_BLOCK_KIND = {"ifstart": "if", "loopstart": "loop"}
SCENARIO_STRUCT = {
    "ifstart": "open", "ifend": "close", "else": "branch",
    "loopstart": "open", "loopend": "close",
    "break": "ctrl", "continue": "ctrl",
}
# 块类型 → 其配对 close op
BLOCK_CLOSE = {"if": "ifend", "loop": "loopend"}
# 某 op 允许出现的容器（用于校验）
CTRL_CONTAINER = {"break": "loop", "continue": "loop"}
BRANCH_CONTAINER = {"else": "if"}

MAX_DEPTH = 8  # 本地 ≤3 + callScenario ≤5；超了仅告警不阻断


def scenario_op_norm(op):
    """对齐桥 _scOpNorm：if/loop 别名归一，其余小写。"""
    o = str(op or "").lower()
    if o == "if":
        return "ifstart"
    if o == "loop":
        return "loopstart"
    return o


def _norm_op_of(step):
    if not isinstance(step, dict):
        return ""
    return scenario_op_norm(step.get("op"))


def scenario_match_close(steps, idx):
    """对齐桥 _scMatch：从 idx(必须是 open) 起按嵌套深度找配对的 close 下标；-1=未找到。"""
    if not isinstance(steps, (list, tuple)) or idx < 0 or idx >= len(steps):
        return -1
    op = _norm_op_of(steps[idx])
    if op not in SCENARIO_OPEN:
        return -1
    kind = SCENARIO_BLOCK_KIND[op]
    close_op = BLOCK_CLOSE[kind]
    open_op = "ifstart" if kind == "if" else "loopstart"
    depth = 0
    for i in range(idx, len(steps)):
        o = _norm_op_of(steps[i])
        if o == open_op:
            depth += 1
        elif o == close_op:
            depth -= 1
            if depth == 0:
                return i
    return -1


def scenario_step_depths(steps):
    """派生每步的结构信息（O(n)），供缩进/连线/配对高亮用。
    返回 (depth, parent_open, close_of, is_last, ancestors)：
      depth[i]      : 嵌套层级（0=顶层）
      parent_open[i]: 包含 i 的最内层块 open 下标（-1=顶层）
      close_of[i]   : 若 i 是 open，其配对 close 下标（-1=未闭合）
      is_last[i]    : 在其父块内是否末位子（画 └─ 还是 ├─）
      ancestors[i]  : 从根到包含 i 的块的 open 下标链（列表）
    """
    n = len(steps) if isinstance(steps, (list, tuple)) else 0
    depth = [0] * n
    parent_open = [-1] * n
    close_of = [-1] * n
    is_last = [False] * n
    ancestors = [[] for _ in range(n)]
    stack = []  # 当前尚未闭合的 open 下标；按类型寻找匹配项
    for i in range(n):
        s = steps[i] if i < n else None
        op = _norm_op_of(s)
        if op in SCENARIO_OPEN:
            ancestors[i] = list(stack)
            depth[i] = len(stack)
            parent_open[i] = stack[-1] if stack else -1
            stack.append(i)
        elif op in SCENARIO_CLOSE:
            open_op = "ifstart" if op == "ifend" else "loopstart"
            match_pos = next(
                (pos for pos in range(len(stack) - 1, -1, -1)
                 if _norm_op_of(steps[stack[pos]]) == open_op),
                -1,
            )
            if match_pos >= 0:
                oi = stack[match_pos]
                ancestors[i] = list(stack[:match_pos])
                depth[i] = match_pos
                close_of[oi] = i
                parent_open[i] = oi
                del stack[match_pos]
                continue
            ancestors[i] = list(stack)
            depth[i] = len(stack)
            parent_open[i] = -1
        elif op == "else":
            match_pos = next(
                (pos for pos in range(len(stack) - 1, -1, -1)
                 if _norm_op_of(steps[stack[pos]]) == "ifstart"),
                -1,
            )
            if match_pos >= 0:
                ancestors[i] = list(stack[:match_pos + 1])
                depth[i] = match_pos + 1
                parent_open[i] = stack[match_pos]
            else:
                ancestors[i] = list(stack)
                depth[i] = len(stack)
                parent_open[i] = -1
        else:
            ancestors[i] = list(stack)
            depth[i] = len(stack)
            parent_open[i] = stack[-1] if stack else -1
    # 计算 is_last：按 parent_open 分组，组内最后一个即末位
    kids = {}
    for i in range(n):
        po = parent_open[i]
        if po >= 0:
            kids.setdefault(po, []).append(i)
    for po, lst in kids.items():
        for k in lst:
            is_last[k] = (k == lst[-1])
    return depth, parent_open, close_of, is_last, ancestors


def scenario_prefix(depth, is_last_flag, ancestors_chain, is_last_arr):
    """生成纯缩进前缀，避免树枝线字符挤占步骤名称空间。"""
    return "  " * max(0, int(depth))


def scenario_block_span(steps, idx):
    """返回包含 idx 的【最内层块】区间 [lo, hi]（含 open/close）。
    若 idx 本身是 open → 返回它自己的块；若 idx 不在任何块内 → 返回 [idx, idx]。"""
    if not isinstance(steps, (list, tuple)) or idx < 0 or idx >= len(steps):
        return (idx, idx)
    depth, parent_open, close_of, _, _ = scenario_step_depths(steps)
    op = _norm_op_of(steps[idx])
    if op in SCENARIO_OPEN:
        c = scenario_match_close(steps, idx)
        return (idx, c if c >= 0 else idx)
    po = parent_open[idx]
    if po >= 0 and close_of[po] >= 0:
        return (po, close_of[po])
    return (idx, idx)


def scenario_validate(steps):
    """编辑期结构校验。返回问题列表：{at, level, kind, msg}。level=error/warn。
    对齐桥 _scRunSteps 的真实容错(按类型分别计深度配对)：
      · 未闭合的 open(桥 _scMatch 返回 -1，条件不成立时中断) → 唯一致命(error)；
      · 悬空 close / 孤儿 else / 不在循环里的 break·continue / 过深 → 桥一律忽略(warn)，不崩。"""
    issues = []
    if not isinstance(steps, (list, tuple)):
        return issues
    n = len(steps)
    # 1) 未闭合的 open —— 唯一致命(桥会中断)
    for i in range(n):
        op = _norm_op_of(steps[i])
        if op in SCENARIO_OPEN and scenario_match_close(steps, i) < 0:
            issues.append({"at": i, "level": "error", "kind": "unclosed",
                           "msg": "第%d步 %s 缺少配对的 %s（桥会中断，必须修）" % (i + 1, op, BLOCK_CLOSE[SCENARIO_BLOCK_KIND[op]])})
    # 2) 悬空 close(无对应 open) —— 桥忽略, 仅告警
    for i in range(n):
        op = _norm_op_of(steps[i])
        if op in SCENARIO_CLOSE:
            kind = SCENARIO_BLOCK_KIND.get(op, "if")
            open_op = "ifstart" if kind == "if" else "loopstart"
            depth = 0; found = False
            for j in range(i - 1, -1, -1):
                o = _norm_op_of(steps[j])
                if o == op:
                    depth += 1
                elif o == open_op:
                    if depth == 0:
                        found = True; break
                    depth -= 1
            if not found:
                issues.append({"at": i, "level": "warn", "kind": "dangling_close",
                               "msg": "第%d步 %s 无对应开头（桥会忽略）" % (i + 1, op)})
    # 3) else 不在任何 if 内 —— 桥忽略
    for i in range(n):
        op = _norm_op_of(steps[i])
        if op == "else":
            depth = 0; found = False
            for j in range(i - 1, -1, -1):
                o = _norm_op_of(steps[j])
                if o == "ifend":
                    depth += 1
                elif o == "ifstart":
                    if depth == 0:
                        found = True; break
                    depth -= 1
            if not found:
                issues.append({"at": i, "level": "warn", "kind": "else_scope",
                               "msg": "第%d步 else 不在任何 if 块内（桥会忽略）" % (i + 1)})
    # 4) break / continue 不在任何循环内 —— 桥忽略
    for i in range(n):
        op = _norm_op_of(steps[i])
        if op in ("break", "continue"):
            depth = 0; found = False
            for j in range(i - 1, -1, -1):
                o = _norm_op_of(steps[j])
                if o == "loopend":
                    depth += 1
                elif o == "loopstart":
                    if depth == 0:
                        found = True; break
                    depth -= 1
            if not found:
                issues.append({"at": i, "level": "warn", "kind": "ctrl_scope",
                               "msg": "第%d步 %s 不在任何循环内（桥会忽略）" % (i + 1, op)})
    # 5) 深度超限(软告警)
    depth, _, _, _, _ = scenario_step_depths(steps)
    for i in range(n):
        if depth[i] > MAX_DEPTH:
            issues.append({"at": i, "level": "warn", "kind": "depth",
                           "msg": "第%d步嵌套深度 %d 超过建议上限 %d" % (i + 1, depth[i], MAX_DEPTH)})
    return issues


def scenario_repair(steps):
    """修复无法配对的结束标记，并按原始嵌套顺序补上缺失的结束标记。

    对齐桥 _scRunSteps 的真实容错(按类型分别计深度配对)：
      · 缺 ifEnd / loopEnd(未闭合 open) → 条件不成立时 _scMatch 返回 -1 → 下标跳回 0 → 中断。
        这是【唯一致命】情形。修复=给每个未闭合的 open 在脚本末尾补一个对应 close
        （保留开括号顺序，按逆序补齐，避免修复后改变块的嵌套关系）。
      · 悬空 close（无对应 open）→ 桥忽略，这里丢弃并告警（避免它错误地配对后面的 open）；
      · 孤儿 else / 不在循环的 break·continue → 桥忽略，原样保留（删了反而可能改坏
        "循环里套 if 再 break"等合法写法，或丢失用户步骤）。
    返回 (repaired_steps, warnings)。"""
    warnings = []
    if not isinstance(steps, (list, tuple)):
        return (list(steps) if isinstance(steps, list) else []), warnings
    out = []
    stack = []       # (block kind, 1-based step number)
    for i, s in enumerate(steps):
        op = _norm_op_of(s)
        if op == "ifstart":
            out.append(s); stack.append(("if", i + 1))
        elif op == "ifend":
            match_pos = next((pos for pos in range(len(stack) - 1, -1, -1)
                              if stack[pos][0] == "if"), -1)
            if match_pos >= 0:
                del stack[match_pos]; out.append(s)
            else:
                warnings.append("第%d步多余的 ifend 已忽略（无对应 ifstart）" % (i + 1))
        elif op == "loopstart":
            out.append(s); stack.append(("loop", i + 1))
        elif op == "loopend":
            match_pos = next((pos for pos in range(len(stack) - 1, -1, -1)
                              if stack[pos][0] == "loop"), -1)
            if match_pos >= 0:
                del stack[match_pos]; out.append(s)
            else:
                warnings.append("第%d步多余的 loopend 已忽略（无对应 loopstart）" % (i + 1))
        else:
            # else / break / continue / 普通步骤: 原样保留(桥会忽略非法的, 合法的一律照跑)
            out.append(s)
    # 脚本末尾补缺失的 close(只补, 不删其它)
    while stack:
        kind, st = stack.pop()
        if kind == "loop":
            out.append({"op": "loopend"})
            warnings.append("第%d步 loopstart 未闭合，已在脚本末尾补 loopend" % st)
        else:
            out.append({"op": "ifend"})
            warnings.append("第%d步 ifstart 未闭合，已在脚本末尾补 ifend" % st)
    return out, warnings


# ===================== 只读树视图（供离线沙盒 B） =====================
def scenario_flat_to_tree(steps):
    """把扁平 steps 解析成嵌套树（只读视图）。节点：
      {"t":"if",   "head":step, "then":[...], "else":[...]}
      {"t":"loop", "head":step, "body":[...]}
      {"t":"leaf", "step":step}
    elseIf 在引擎里是"else 里的 if"，这里不特意解开（沙盒按嵌套 if 求值时语义一致）。"""
    root = []
    stack = [{"t": "_root", "children": root}]
    for s in steps:
        op = _norm_op_of(s)
        top = stack[-1]
        if op in SCENARIO_OPEN:
            kind = SCENARIO_BLOCK_KIND[op]
            if kind == "if":
                node = {"t": "if", "head": s, "then": [], "else": []}
            else:
                node = {"t": "loop", "head": s, "body": []}
            # 落点选择: 跟叶子同一套规则(根/if.else/if.then/loop.body)
            if top.get("t") == "_root":
                target = top["children"]
            elif top.get("t") == "if" and top.get("_cur") == "else":
                target = top["else"]
            elif top.get("t") == "if":
                target = top["then"]
            elif top.get("t") == "loop":
                target = top["body"]
            else:
                target = top.get("children", [])
            target.append(node)
            stack.append(node)
        elif op == "ifend":
            if len(stack) > 1:
                stack.pop()
        elif op == "loopend":
            if len(stack) > 1:
                stack.pop()
        elif op == "else":
            # else 是控制标记, 不计入树节点; 仅把当前 if 的后续落点切到 else 分支
            if stack[-1].get("t") == "if":
                stack[-1]["_cur"] = "else"
            continue
        else:
            if stack[-1].get("t") == "if" and stack[-1].get("_cur") == "else":
                stack[-1]["else"].append({"t": "leaf", "step": s})
            else:
                target = stack[-1]["then"] if stack[-1].get("t") == "if" else \
                    (stack[-1]["body"] if stack[-1].get("t") == "loop" else stack[-1]["children"])
                target.append({"t": "leaf", "step": s})
    return root


def scenario_tree_to_flat(root):
    """树 → 扁平 steps（含 elseIf 的"else 里套 if"模拟）。
    if 节点支持 elifs:[{"head":step,"body":[...]}] 与 else_body:[...] 以表达否则如果/否则。"""
    out = []

    def emit_if(node):
        out.append(node["head"])
        for c in node.get("then", []):
            _emit(c)
        elifs = node.get("elifs") or []
        else_body = node.get("else_body") or []
        if elifs or else_body:
            out.append({"op": "else"})
            for k, e in enumerate(elifs):
                out.append(e["head"])
                for c in e.get("body", []):
                    _emit(c)
                if k < len(elifs) - 1 or else_body:
                    out.append({"op": "else"})
            for c in else_body:
                _emit(c)
            for _ in range(len(elifs)):
                out.append({"op": "ifend"})
            out.append({"op": "ifend"})

    def _emit(node):
        t = node.get("t")
        if t == "if":
            emit_if(node)
        elif t == "loop":
            out.append(node["head"])
            for c in node.get("body", []):
                _emit(c)
            out.append({"op": "loopend"})
        else:
            out.append(node["step"])

    for node in root:
        _emit(node)
    return out


# ===================== 渲染行构造（供 Treeview） =====================
def scenario_outline_rows(steps):
    """返回渲染用行列表：每行 (idx, prefix, op_norm, is_open, is_close, depth)。"""
    depth, parent_open, close_of, is_last, ancestors = scenario_step_depths(steps)
    rows = []
    for i in range(len(steps)):
        op = _norm_op_of(steps[i])
        is_close = op in SCENARIO_CLOSE
        pre = scenario_prefix(depth[i], is_last[i], ancestors[i], is_last)
        rows.append({
            "idx": i, "prefix": pre, "op": op,
            "is_open": op in SCENARIO_OPEN, "is_close": is_close,
            "depth": depth[i], "close_of": close_of[i], "parent_open": parent_open[i],
        })
    return rows
