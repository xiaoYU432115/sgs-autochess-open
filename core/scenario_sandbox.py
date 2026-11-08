# -*- coding: utf-8 -*-
# ============================================================================
# 剧本离线沙盒（想法 B）—— 不动真机即可定位"哪步没执行 / 效果没实现 / 逻辑写错"。
#
# 消费与引擎【完全相同】的扁平 steps(想法A的序列化物), 用统一的 _scMatch/_scElseOf
# 配对语义(对齐 sgs_bridge.user.js)做控制流, 跑出每步 ✅/⏭/❌ + 前后 diff。
#
# 诚实边界(对齐设计文档 B.1):
#   · 能验: 分支进没进、循环终不终止、条件在给定快照下是否成立、setVar/addVar/tallyGen
#           的变量演化、callScenario 静态展开、不可达/死循环/变量未定义。
#   · 仅真机可验: deploy/buy/combine 等真实局面改动 —— 这里标 effect_pending, 并只做静态前置检查。
# ============================================================================
from .cond_engine import eval_cond, cond_of_step, _gold_tail, _card_kind, _var_items

# 改变沙盒变量/计数(可真验)的 op —— 全小写(与 _norm 输出一致)
VAR_OPS = {"setvar", "addvar", "tallygen"}
# 控制流 op —— 全小写
CONTROL_OPS = {"ifstart", "ifend", "else", "loopstart", "loopend",
               "break", "continue", "mark"}
# 游戏动作 op(仅真机可验, 标 effect_pending)
ACTION_WORDS = ("上阵", "购买", "合成", "刷新", "遣散", "用锦囊", "选", "随征", "挑战", "升级", "施法")


def _norm(s):
    o = str(s or "").lower()
    if o == "if":
        return "ifstart"
    if o == "loop":
        return "loopstart"
    return o


def _match(steps, start, open_op, close_op):
    depth = 0
    for i in range(start, len(steps)):
        op = _norm(steps[i].get("op") if isinstance(steps[i], dict) else None)
        if op == open_op:
            depth += 1
        elif op == close_op:
            depth -= 1
            if depth == 0:
                return i
    return -1


def _else_of(steps, start):
    depth = 0
    for i in range(start, len(steps)):
        op = _norm(steps[i].get("op") if isinstance(steps[i], dict) else None)
        if op == "ifstart":
            depth += 1
        elif op == "ifend":
            depth -= 1
            if depth == 0:
                return -1
        elif op == "else" and depth == 1:
            return i
    return -1


def _find_ifstart(steps, at_else):
    depth = 0
    for i in range(at_else, -1, -1):
        op = _norm(steps[i].get("op") if isinstance(steps[i], dict) else None)
        if op == "ifend":
            depth += 1
        elif op == "ifstart":
            if depth == 0:
                return i
            depth -= 1
    return -1


def _resolve_value(raw, st):
    """把「设置变量」的值解析成 数/文本。数→float; gold/turn/level/hp/elapsed→取 state;
    变量名→取 vars; 否则→原文本。对齐桥 _scVarNum 的宽松口径。"""
    s = str(raw if raw is not None else "").strip()
    if s == "":
        return 0
    if _as_num(s) is not None:
        return _as_num(s)
    k = s.lower()
    if k in ("gold", "金币", "金钱"):
        return st.get("gold") or 0
    if k in ("turn", "round", "回合"):
        return st.get("round") or 0
    if k in ("level", "等级"):
        return st.get("level") or 0
    if k in ("hp", "血量", "生命"):
        return st.get("hp") or 0
    if k in ("elapsed", "已运行秒数"):
        return st.get("elapsed") or 0
    if k in st.get("vars", {}):
        return st["vars"][k]
    return s  # 文本原样


def _as_num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def run_scenario(steps, state, options=None):
    """跑一遍剧本(扁平 steps), 返回 {ok, trace, summary, error}。
    state: 可变 dict(会就地改 vars 等); options: {loopCap, callDepthCap, scenario_getter}。"""
    opts = options or {}
    loop_cap = int(opts.get("loopCap", 10000))
    call_cap = int(opts.get("callDepthCap", 5))
    getter = opts.get("scenario_getter")
    st = state
    trace = []
    errors = []
    marks = {}
    action_failed = [False]   # 给 _run_action 回写"本步模拟失败"(onFailJump 用)
    loops = []          # {at, end, times, done, kind, check, while_check}
    at = 0
    n = len(steps)
    # 预扫描所有 mark → 建立 name→index 映射(支持"失败跳转"前向引用, 与真机一致:
    # 真机在 run 前会先扫一遍步骤建锚点表)。
    for _i, _s in enumerate(steps):
        if isinstance(_s, dict) and _norm(_s.get("op")) == "mark" and _s.get("name"):
            marks[str(_s["name"])] = _i

    def log(idx, op, status, note, branch="", iter_=None, vars_after=None):
        rec = {"idx": idx, "op": op, "status": status, "note": note,
               "branch": branch, "iter": iter_}
        if vars_after is not None:
            rec["varsAfter"] = dict(vars_after)
        trace.append(rec)
        return rec

    def record_var(name, val):
        st.setdefault("vars", {})[name] = val

    step_count = 0
    while at < n:
        step = steps[at]
        if not isinstance(step, dict):
            at += 1
            continue
        op = _norm(step.get("op"))
        cur = at
        # ---- 控制流 ----
        if op in ("ifstart",):
            c = cond_of_step(step)
            ok, txt = eval_cond(c, st)
            if ok:
                log(at, op, "executed", txt, branch="then")
                at += 1
            else:
                e = _else_of(steps, at)
                if e >= 0:
                    log(at, op, "skipped", txt + " → 走 否则", branch="else")
                    at = e + 1
                else:
                    end = _match(steps, at, "ifstart", "ifend")
                    log(at, op, "skipped", txt + " → 整块跳过", branch="none")
                    at = end + 1 if end >= 0 else n
        elif op == "else":
            end = _match(steps, at, "ifstart", "ifend")
            log(at, op, "branch", "否则分支结束")
            at = end + 1 if end >= 0 else n
        elif op == "ifend":
            log(at, op, "close", "")
            at += 1
        elif op in ("loopstart",):
            times = int(step.get("times") or 0)
            while_mode = str(step.get("mode") or "").lower() == "while"
            # 条件字段(check/has/...)在【次数模式】下作"提前跳出条件",
            # 在【while 模式】下作"循环继续条件"(开始不成立则一轮都不跑)。
            # 与 sgs_bridge.user.js 的 loopstart(brk) 同口径: 同一组字段, 由 mode 决定语义。
            cond = cond_of_step(step) if step.get("check") else None
            while_check = cond if while_mode else None
            break_check = cond if (not while_mode and step.get("check")) else None
            end = _match(steps, at, "loopstart", "loopend")
            if end < 0:
                errors.append("循环未闭合(缺 loopend): 第%d步" % (at + 1))
                break
            loops.append({"at": at, "end": end, "times": times, "done": 0,
                          "kind": "while" if while_mode else "count",
                          "check": break_check, "while_check": while_check})
            # 首轮判定
            if while_mode:
                wc_ok = eval_cond(while_check, st)[0] if while_check else True
                if not wc_ok:
                    log(at, op, "loop_skip", "while 条件不成立, 跳过", iter_=0)
                    at = end + 1
                    continue
            else:
                if times <= 0:
                    log(at, op, "loop_skip", "次数<=0, 跳过", iter_=0)
                    at = end + 1
                    continue
            log(at, op, "loop_start", "循环开始(times=%s%s)" % (
                times, ", while" if while_mode else ""), iter_=1)
            at += 1
        elif op == "loopend":
            if loops:
                L = loops[-1]            # 留在栈上, 直到 done 才弹出(对齐引擎 _scLoop)
                L["done"] += 1
                finished = False
                if L["kind"] == "while":
                    wc_ok = eval_cond(L["while_check"], st)[0] if L["while_check"] else False
                    if wc_ok and L["done"] < loop_cap:
                        log(at, op, "loop_iter", "while 继续", iter_=L["done"] + 1)
                        at = L["at"] + 1
                    else:
                        if L["done"] >= loop_cap:
                            errors.append("while 循环超过 %d 次上限(疑似死循环): 第%d步" % (loop_cap, L["at"] + 1))
                        finished = True
                else:
                    # 次数模式: 提前跳出条件(check)成立 → 立即结束本轮循环
                    if L["check"] and eval_cond(L["check"], st)[0]:
                        finished = True
                        log(at, op, "loop_break", "跳出条件成立, 提前结束循环(%d轮)" % L["done"], iter_=L["done"])
                    elif L["done"] < L["times"] and L["done"] < loop_cap:
                        log(at, op, "loop_iter", "第%d/%d轮" % (L["done"] + 1, L["times"]), iter_=L["done"] + 1)
                        at = L["at"] + 1
                    else:
                        if L["done"] >= loop_cap:
                            errors.append("循环超过 %d 次上限(疑似死循环): 第%d步" % (loop_cap, L["at"] + 1))
                        finished = True
                if finished:
                    loops.pop()
                    log(at, op, "loop_end", "循环结束(%d轮)" % L["done"], iter_=L["done"])
                    at += 1
            else:
                log(at, op, "close", "")
                at += 1
        elif op in ("break", "continue"):
            if not loops:
                log(at, op, "warn", "break/continue 不在循环内(引擎会忽略)", )
                at += 1
                continue
            end = loops[-1]["end"]
            if op == "break":
                loops.pop()              # 跳出: 弹出当前循环并越过 loopend
                log(at, op, "break", "跳出循环", )
                at = end + 1
            else:
                log(at, op, "continue", "跳过本次", )
                at = end                 # 回到 loopend 重新判 done/条件
        elif op == "mark":
            name = str(step.get("name") or step.get("text") or ("m%d" % at))
            marks[name] = at
            log(at, op, "mark", "锚点 %s" % name)
            at += 1
        # ---- 变量 / 计数(可真验) ----
        elif op in VAR_OPS:
            _run_var_step(op, step, st, record_var, log, at)
        # ---- 调用子剧本(静态展开) ----
        elif op == "callscenario":
            _run_call(op, step, st, log, at, getter, call_cap, run_scenario, opts)
        # ---- 游戏动作(仅真机可验) ----
        else:
            action_failed[0] = False
            _run_action(op, step, st, log, at, errors, action_failed)

        # L5 verify: 步执行后立刻复检(对齐设计文档"效果断言")
        if op not in CONTROL_OPS and isinstance(step, dict) and step.get("verify"):
            ok, txt = eval_cond(cond_of_step(step["verify"]), st)
            if not ok:
                errors.append("第%d步 效果断言未达成: %s" % (cur + 1, txt))
                # 标记最近一条该步 trace 为 error
                for r in reversed(trace):
                    if r["idx"] == cur:
                        r["status"] = "error"
                        r["note"] = (r["note"] + " | verify未达成: " + txt)
                        break
                # 让 verify 失败也能被 onFailJump 接住(与真机一致: 断言失败=本步失败)
                action_failed[0] = True
        # L4 onFailJump: 动作步真机失败 → 跳到锚点而非中断(沙盒用 simFail 模拟失败;
        #   真机由 sgs_bridge.user.js 在 _scRunSteps 里读 onFailJump 实现同样动作)
        if (op not in CONTROL_OPS and isinstance(step, dict) and step.get("onFailJump")
                and action_failed[0]):
            mname = str(step["onFailJump"])
            action_failed[0] = False
            if mname in marks:
                log(cur, op, "jump", "失败 → 跳到锚点 %s" % mname)
                at = marks[mname]
                continue
            else:
                errors.append("第%d步 的失败跳转锚点「%s」不存在" % (cur + 1, mname))
        if at == cur:       # 控制流分支已自行跳转, 其余步统一前进
            at += 1
        step_count += 1
        if step_count > loop_cap * 5:
            errors.append("步数超过安全上限, 疑似失控")
            break

    summary = _summarize(trace)
    return {"ok": len(errors) == 0, "trace": trace, "summary": summary, "errors": errors}


# ---------------------------------------------------------------------------
def _run_var_step(op, step, st, record_var, log, at):
    if op == "setvar":
        name = str(step.get("name") or "").strip()
        if not name:
            log(at, op, "warn", "没写变量名")
            return
        val = _resolve_value(step.get("value"), st)
        record_var(name, val)
        log(at, op, "executed", "%s = %s" % (name, val), vars_after=st.get("vars"))
    elif op == "addvar":
        name = str(step.get("name") or "").strip()
        if not name:
            log(at, op, "warn", "没写变量名")
            return
        cur = st.get("vars", {}).get(name, 0)
        cur = cur if isinstance(cur, (int, float)) else _as_num(cur) or 0
        delta = _resolve_value(step.get("value"), st)
        delta = delta if isinstance(delta, (int, float)) else _as_num(delta) or 0
        record_var(name, cur + delta)
        log(at, op, "executed", "%s += %s → %s" % (name, delta, cur + delta), vars_after=st.get("vars"))
    elif op == "tallygen":
        scope = str(step.get("scope") or "both").lower()
        prefix = str(step.get("out") or "cnt")
        sep = bool(step.get("goldSeparate", True))
        names = [x.strip() for x in str(step.get("names") or "").split(",") if x.strip()]
        cards = []
        if scope in ("hand", "both"):
            cards += st.get("hand", [])
        if scope in ("field", "both"):
            cards += st.get("field", [])
        for c in cards:
            if not c:
                continue
            nm = str(c.get("name") or "")
            if names and nm not in names:
                continue
            key = "%s:%s" % (prefix, nm)
            if sep:
                key += ":" + _gold_tail(c)
            st.setdefault("vars", {})[key] = st.get("vars", {}).get(key, 0) + 1
        log(at, op, "executed", "统计%s → %s" % (scope, prefix), vars_after=st.get("vars"))


def _run_call(op, step, st, log, at, getter, call_cap, run_fn, opts):
    sid = str(step.get("scenario") or step.get("id") or step.get("target") or "").strip()
    if not getter:
        log(at, op, "warn", "未提供子剧本获取器, 跳过(仅真机可验)")
        return
    # 调用方实时参数(选项 A): 把 params 透传给 getter, 由它注入到子剧本 steps。
    # 旧式 getter 只收 1 参(name), 用 try/except 保持向后兼容。
    params = step.get("params")
    try:
        sub = getter(sid, params)
    except TypeError:
        sub = getter(sid)
    if not sub:
        log(at, op, "error", "找不到子剧本 %s" % sid)
        return
    # 传参映射 bind: {子变量: 本变量}
    bind = step.get("bind") or {}
    if bind:
        saved = {}
        for sub_v, my_v in bind.items():
            saved[sub_v] = st.get("vars", {}).get(my_v)
            st.setdefault("vars", {})[sub_v] = st.get("vars", {}).get(my_v)
    try:
        res = run_fn(sub, st, dict(opts, callDepthCap=call_cap - 1))
    finally:
        if bind:
            for sub_v in bind:
                if sub_v in saved and saved[sub_v] is not None:
                    st.setdefault("vars", {})[sub_v] = saved[sub_v]
                else:
                    st.get("vars", {}).pop(sub_v, None)
    log(at, op, "executed" if res["ok"] else "error",
        "调用 %s(深度%d): %s" % (sid, call_cap, "; ".join(res["errors"]) or "ok"))


def _run_action(op, step, st, log, at, errors, failed_flag=None):
    label = str(step.get("op") or "")
    # 静态前置检查(仅真机可验, 这里只做"能不能跑"的廉价判断)
    note = "真实效果仅真机可验(effect_pending)"
    if "target" in step or step.get("has"):
        note = "目标=%s | 真机效果待验" % (step.get("target") or step.get("has"))
    on_fail = step.get("onFailJump")
    if on_fail:
        # 沙盒里游戏动作无法真正失败, 但记录跳转意图
        note += " | 失败跳 %s" % on_fail
    # 离线测试钩子: simFail 让本步"模拟真机失败", 用于验证 onFailJump 跳转逻辑
    if step.get("simFail"):
        note += " | 模拟失败(simFail)"
        if failed_flag is not None:
            failed_flag[0] = True
        log(at, label, "error", note)
        return
    log(at, label, "effect_pending", note)


def _summarize(trace):
    from collections import Counter
    c = Counter(r["status"] for r in trace)
    return dict(c)
