# -*- coding: utf-8 -*-
# ============================================================================
# 统一条件引擎（想法 C）—— 把 if/loop(跳出)/waitUntil/L5.verify/L3计数/B沙盒
# 的条件求值收敛到【同一份语义】上, 根除"JS 一份、Python 一份"的漂移。
#
# 语义严格对齐桥 sgs_bridge.user.js 的 _scCond / _scCondCard / _scCondNum /
# _scCondKeys / _scCmpNum(见 12295~17994 行)。改这里 = 改全部消费者。
#
# 条件两种写法(都支持, 向后兼容):
#   ① 旧式单条件: {check, has, mode, where, gold, kind, cmp, target/count, negate, ...}
#   ② 新式条件组(L2 且/或):
#        {"conds": [ {单条件A}, {"join":"and", 单条件B}, {"join":"or", 单条件C} ]}
#      或嵌套组 {"op":"and", "items":[ 单条件或组, ...]}  (深度上限 GROUP_MAX_DEPTH)
# ============================================================================
import re

GROUP_MAX_DEPTH = 3

# 条件 check 值 → 中文(日志/预览用)
COND_CN = {"card": "手牌/场上有卡", "gold": "金币", "turn": "回合", "level": "商店等级",
           "hp": "血量", "stage": "阶段", "var": "变量", "elapsed": "已运行秒数",
           "stat": "数值变化", "alive": "存活家数", "foehp": "对手最高血量",
           "equip": "身上装备"}

# 阶段同义词组(游戏枚举英文 vs 用户填中文)
_STAGE_SYN = {
    "招募": ["recruit", "prepare", "prep", "buy", "shop", "准备", "招募"],
    "战斗": ["battle", "fight", "combat", "war", "战斗"],
    "结算": ["settle", "result", "结算"],
}


# ---------------------------------------------------------------------------
# 状态读取助手
# ---------------------------------------------------------------------------
def _as_num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _gold_tail(card):
    """金卡判据: 尾数 2 = 金, 其余一律【普通】(对齐桥 _scOpTallyGen 的 id%10===2 才是金)。
    返回 'normal'/'gold'。旧的「尾数 1=普通」太窄(很多卡 ID 尾数既不是1也不是2),
    会导致非金卡被当成""、tallyGen 键变成 'cnt:孙策:'、条件里 gold='normal' 也判不中 —— 现已修正。"""
    g = int(card.get("goodsID") or card.get("chessID") or 0)
    if g % 10 == 2:
        return "gold"
    return "normal"


def _card_kind(card):
    """spellID 存在=锦囊; equipmentID 存在=武器/装备; 否则=武将/随征卡。对齐桥 kindOk。"""
    if card.get("spellID"):
        return "spell"
    if card.get("equipmentID"):
        return "equip"
    return "chess"


def _sc_hit(card, key):
    """卡名包含 key(子串) 或 ID 相等即命中(对齐桥 _scHit 的"名字 or ID")。"""
    if card is None:
        return False
    k = str(key or "").strip().lower()
    if not k:
        return False
    name = str(card.get("name") or "").lower()
    if k in name:
        return True
    try:
        cid = int(k)
        if cid and (int(card.get("chessID") or 0) == cid or int(card.get("goodsID") or 0) == cid):
            return True
    except ValueError:
        pass
    return False


def _sc_keys(v):
    """把 "手牌:孙策, 阵上:!丁奉, 格3:孙策" 解析成 [{k,neg,where,slot}]。
    对齐桥 _scCondKeys(位置前缀 / 否定前缀 / 站位前缀)。"""
    if v is None:
        return []
    parts = re.split(r"[,，;；]", str(v))
    out = []
    for raw in parts:
        t = str(raw).strip()
        if not t:
            continue
        neg = False
        where = ""
        mw = re.match(r"^(手牌|手里|手上|hand|阵上|场上|field|both|任意|any|全场|全桌|all)\s*[:：]\s*(.*)$", t, re.I)
        if mw:
            a = mw[1].lower()
            where = ("hand" if a in ("手牌", "手里", "手上", "hand") else
                     "field" if a in ("阵上", "场上", "field") else
                     "all" if a in ("全场", "全桌", "all") else "both")
            t = mw[2].strip()
        if t and t[0] in "!-！":
            neg = True
            t = t[1:].strip()
        mz = re.match(r"^(没有|不带|不在|排除)\s*(.+)$", t)
        if mz:
            neg = True
            t = mz[2].strip()
        slot = 0
        ms = re.match(r"^(?:格|第|slot\s*[:=]?)?\s*([1-7])\s*格?\s*[:：]\s*(.*)$", t, re.I)
        if ms:
            slot = int(ms[1])
            t = ms[2].strip()
        if not t:
            continue
        out.append({"k": t, "neg": neg, "where": where, "slot": slot})
    return out


def _cmp_num(have, cmp_, want):
    """对齐桥 _scCmpNum。"""
    c = str(cmp_ or "").strip()
    if c in (">=", "≥"):
        return have >= want
    if c in ("<=", "≤"):
        return have <= want
    if c in (">", "大于"):
        return have > want
    if c in ("<", "小于"):
        return have < want
    if c in ("!=", "≠", "<>"):
        return have != want
    return have == want


def _var_items(v):
    """变量值 → 列表(文本按 ,，;；| 切; 列表原样; 其他包成单元素)。"""
    if isinstance(v, (list, tuple)):
        return list(v)
    s = str(v if v is not None else "")
    if not s:
        return []
    return [x.strip() for x in re.split(r"[,，;；|]", s) if x.strip()]


# ---------------------------------------------------------------------------
# 单条件求值(旧式字段)
# ---------------------------------------------------------------------------
def _cond_card(step, st):
    items = _sc_keys(step.get("has") or step.get("target"))
    mode = str(step.get("mode") or "all").lower()
    where = str(step.get("where") or "hand").lower()
    gold_want = str(step.get("gold") or "").lower()
    kind_raw = str(step.get("kind") or "").lower()
    kind_wl = ("spell" if (kind_raw == "spell" or "锦囊" in kind_raw) else
               "chess" if (kind_raw in ("chess", "武将", "棋子")) else
               "equip" if (kind_raw in ("equip", "武器", "装备")) else "")

    hand = [c for c in st.get("hand", []) if c]
    field = [c for c in st.get("field", []) if c]
    all_seen = st.get("allSeen") or (hand + field)

    def gold_ok(c):
        if not gold_want:
            return True
        return _gold_tail(c) == gold_want

    def kind_ok(c):
        if not kind_wl:
            return True
        return _card_kind(c) == kind_wl

    def list_of(w):
        if w == "hand":
            return hand
        if w == "field":
            return field
        if w == "all":
            return all_seen
        return hand + field

    def kind_count_at(w):
        if not kind_wl:
            return len(list_of(w))
        return sum(1 for c in list_of(w) if kind_ok(c))

    def hit_at(k, w, slot_n):
        ww = w or where
        hit = lambda c: _sc_hit(c, k) and gold_ok(c) and kind_ok(c)
        if 1 <= slot_n <= 7:
            for c in field:
                sj = int(c.get("slot") or 0) or 0
                if sj == slot_n:
                    return hit(c)
            return False
        if ww == "hand":
            return any(hit(c) for c in hand)
        if ww == "field":
            return any(hit(c) for c in field)
        if ww == "all":
            return any(hit(c) for c in all_seen)
        return any(hit(c) for c in hand) or any(hit(c) for c in field)

    CN = {"hand": "手牌", "field": "阵上", "both": "任意位", "all": "全场已见"}
    pos_total = pos_hit = neg_bad = 0
    for it in items:
        h = hit_at(it["k"], it["where"], it["slot"])
        if it["neg"]:
            if h:
                neg_bad += 1
        else:
            pos_total += 1
            if h:
                pos_hit += 1
    kcn = {"spell": "锦囊", "chess": "武将", "equip": "装备/武器"}
    if pos_total:
        if mode == "any":
            pos_ok = pos_hit > 0
        elif mode == "none":
            pos_ok = pos_hit == 0
        else:
            pos_ok = pos_hit == pos_total
    elif kind_wl:
        c1 = kind_count_at(where)
        has = c1 > 0
        pos_ok = (not has) if mode == "none" else has
    else:
        # 全是否定项(如 "!丁奉"): 逐一确认"都不在"
        neg_items = [it for it in items if it["neg"]]
        if neg_items:
            neg_ok = sum(1 for it in neg_items if not hit_at(it["k"], it["where"], it["slot"]))
            pos_ok = (neg_ok == len(neg_items))
        else:
            pos_ok = (mode == "none")

    ok = bool(pos_ok) and (neg_bad == 0)

    # 数量形态
    cnt_want = int(step.get("count") or 0)
    if cnt_want > 0:
        if not items:
            n = kind_count_at(where) if kind_wl else (len(list_of(where)))
        else:
            n = 0
            for it in items:
                lst = (hand if it["where"] == "hand" else
                       field if it["where"] == "field" else
                       all_seen if it["where"] == "all" else
                       hand + field)
                n += sum(1 for c in lst if _sc_hit(c, it["k"]) and gold_ok(c) and kind_ok(c))
        c_ok = _cmp_num(n, step.get("cmp") or ">=", cnt_want)
        res = (not c_ok) if step.get("negate") else c_ok
        return res, "数量 %d%s%s [%d%s%d] → %s" % (
            n, CN.get(where, where), (kcn.get(kind_wl, kind_wl) if kind_wl else ""),
            n, step.get("cmp") or ">=", cnt_want, "成立" if res else "不成立")
    return ok, "条件%s" % ("成立" if ok else "不成立")


def _cond_num(step, st):
    check = str(step.get("check") or "").strip().lower()
    cmp_ = str(step.get("cmp") or "").strip()
    want = str(step.get("target") if step.get("target") is not None else "").strip()
    neg = bool(step.get("negate"))

    # 装备
    if check in ("equip", "装备", "身上装备"):
        eq_list = st.get("equip", [])
        names = [x.strip() for x in re.split(r"[,，;；]", str(step.get("has") or "")) if x.strip()]
        type_want = int(step.get("eqType") or 0)
        if not names and not type_want:
            return False, "装备条件既没写名也没选大类 → 不成立"
        ok = False
        for e in eq_list:
            if type_want and e.get("type") == type_want:
                ok = True
                break
            en = str(e.get("name") or "")
            eid = str(e.get("id") or "")
            if any((w in en) or (eid == w) for w in names):
                ok = True
                break
        return (not ok) if neg else ok, "装备[%s] %s%s → %s" % (
            "待选" if str(step.get("eqScope") or "own").lower() == "cand" else "已装",
            "不含 " if neg else "含 ", ",".join(names) or ("大类%d" % type_want),
            "成立" if ((not ok) if neg else ok) else "不成立")

    # 阶段
    if check in ("phase", "阶段", "stage"):
        pn = str(st.get("phaseName") or "")
        words = _STAGE_SYN.get(want.replace("阶段", ""), [want] if want else [])
        hay = (pn + " " + str(st.get("phase") or "")).lower()
        ok = any(w and w.lower() in hay for w in words)
        return (not ok) if neg else ok, "阶段=%s %s %s → %s" % (
            pn or st.get("phase") or "读不到", "不是 " if neg else "是 ", want,
            "成立" if ((not ok) if neg else ok) else "不成立")

    # 数值变化(stat)
    if check in ("stat", "数值", "数值变化"):
        keys = [x["k"] for x in _sc_keys(step.get("has")) if not x["neg"]]
        which = "hp" if str(step.get("stat") or "atk").lower() in ("hp", "血量", "生命") else "atk"
        if not keys:
            return False, "数值变化没写要看哪张卡 → 不成立"
        snap = st.get("statSnap") or {}
        w0 = _as_num(want)
        if w0 is None:
            return False, "数值变化要比个数字 → 不成立"
        f = st.get("field", [])
        any_ok = False
        for c in f:
            if not c or not c.get("goodsID") or not _sc_hit(c, keys):
                continue
            rec = snap.get(str(c.get("goodsID")))
            now = c.get("_stat") or {}
            nowv = now.get(which)
            if rec is None or rec.get(which) is None or nowv is None:
                continue
            d = nowv - rec.get(which)
            if _cmp_num(d, cmp_, w0):
                any_ok = True
        return (not any_ok) if neg else any_ok, "数值变化[%s] → %s" % (
            "/".join(keys), "成立" if ((not any_ok) if neg else any_ok) else "不成立")

    # 通用数值源
    have = None
    what = ""
    if check in ("gold", "金币", "金钱"):
        have, what = st.get("gold"), "金币"
    elif check in ("level", "等级"):
        have, what = st.get("level"), "等级"
    elif check in ("turn", "round", "回合"):
        have, what = st.get("round"), "回合"
    elif check in ("hp", "血量", "生命"):
        have, what = st.get("hp"), "血量"
    elif check in ("alive", "存活家数", "存活人数"):
        have = st.get("alive")
        what = "存活家数"
    elif check in ("foehp", "对手最高血量", "对手血量"):
        have = st.get("foehp")
        what = "对手最高血量"
    elif check in ("elapsed", "已运行秒数", "时间"):
        have = st.get("elapsed")
        what = "已运行秒数"
    elif check in ("var", "变量"):
        vn = str(step.get("varName") or step.get("name") or "").strip()
        what = "变量" + (vn or "?")
        as_ = str(step.get("varAs") or step.get("varMode") or step.get("as") or "num").lower()
        raw = st.get("vars", {}).get(vn) if vn else None
        has1 = vn in st.get("vars", {})
        if as_ in ("count", "有几项", "项数"):
            if not has1:
                return False, what + "还没值 → 不成立"
            items = _var_items(raw)
            w1 = _as_num(want)
            if w1 is None:
                return False, what + "要比项数但数值非数字 → 不成立"
            ok = _cmp_num(len(items), cmp_, int(w1))
            return (not ok) if neg else ok, what + "共%d项 %s%d → %s" % (
                len(items), cmp_ or "=", int(w1), "成立" if ((not ok) if neg else ok) else "不成立")
        if as_ in ("has", "包含", "里面有没有某个词"):
            needle = str(step.get("varText") if step.get("varText") not in (None, "") else step.get("target") or "").strip()
            if not has1:
                return False, what + "还没值 → 不成立"
            if not needle:
                return False, what + "要查的词为空 → 不成立"
            items = _var_items(raw)
            hit = any(needle in str(x) for x in items)
            return (not hit) if neg else hit, what + " 里有%s「%s」 → %s" % (
                "" if hit else "没有", needle, "成立" if ((not hit) if neg else hit) else "不成立")
        if has1:
            have = raw if isinstance(raw, (int, float)) else _as_num(raw)

    if not what:
        return False, "不认识的判定项「%s」→ 不成立" % check
    if have is None:
        return False, what + "读不到 → 不成立"
    w = _as_num(want)
    if w is None:
        return False, what + "要跟「%s」比但目标非数字 → 不成立" % want
    ok = _cmp_num(have, cmp_, w)
    return (not ok) if neg else ok, what + "=%s%s%s → %s" % (
        have, cmp_ or "=", w, "成立" if ((not ok) if neg else ok) else "不成立")


def _eval_single(step, st):
    """旧式单条件(带 check 字段)求值。返回 (ok, text)。"""
    if not isinstance(step, dict):
        return False, "空条件"
    check = str(step.get("check") or "").strip().lower()
    if not check or check in ("card", "has", "手牌"):
        return _cond_card(step, st)
    return _cond_num(step, st)


# ---------------------------------------------------------------------------
# 条件组(L2 且/或) + 对外入口
# ---------------------------------------------------------------------------
def eval_cond(cond, st):
    """统一入口。cond 可以是:
       · None / {}           → (False, "")
       · 旧式单条件(dict有check) → 走 _eval_single
       · {"conds":[...]}     → 条件组(and/or 链)
       · {"op":..,"items":..}→ 嵌套组
    返回 (ok, text)。"""
    if not cond:
        return False, ""
    if "op" in cond and "items" in cond:
        return _eval_group(cond, st, 0)
    if "all" in cond and isinstance(cond["all"], (list, tuple)):
        return _eval_group({"op": "and", "items": cond["all"]}, st, 0)
    if "any" in cond and isinstance(cond["any"], (list, tuple)):
        return _eval_group({"op": "or", "items": cond["any"]}, st, 0)
    if "conds" in cond and isinstance(cond["conds"], (list, tuple)):
        return _eval_chain(cond["conds"], st)
    return _eval_single(cond, st)


def _eval_chain(conds, st):
    """conds=[A, {join:'and',B}, {join:'or',C}] → A (and B) (or C)。"""
    if not conds:
        return False, ""
    ok, txt = eval_cond(conds[0], st)
    texts = [txt]
    for item in conds[1:]:
        join = str(item.get("join") or "and").lower()
        ok2, t2 = eval_cond(item, st)
        texts.append(t2)
        if join == "or":
            ok = ok or ok2
        else:
            ok = ok and ok2
    return ok, " | ".join(texts)


def _eval_group(g, st, depth):
    if depth > GROUP_MAX_DEPTH:
        return False, "条件嵌套过深(上限%d) → 不成立" % GROUP_MAX_DEPTH
    op = str(g.get("op") or "and").lower()
    items = g.get("items") or []
    if not items:
        return False, ""
    if op == "or":
        for it in items:
            ok, t = eval_cond(it, st)
            if ok:
                return True, t
        # 全假: 取最后一条说明
        _, t = eval_cond(items[-1], st)
        return False, t
    # and(默认)
    last = ""
    for it in items:
        ok, t = eval_cond(it, st)
        last = t
        if not ok:
            return False, t
    return True, last


# ---------------------------------------------------------------------------
# 便捷: 从"步骤"里抽条件(兼容 step.check 旧字段 与 step.conds 新字段)
# ---------------------------------------------------------------------------
def cond_of_step(step):
    """给定剧本步骤, 返回应参与求值的条件对象(conds 优先, 否则回退旧字段)。"""
    if not isinstance(step, dict):
        return {}
    c = step.get("conds")
    if c:
        if isinstance(c, (list, tuple)):
            return {"conds": list(c)}   # 链格式统一包一层, 交给 _eval_chain
        return c                        # dict 形式(all/any/op/conds)原样返回
    # 直接给的就是组/单条件(没有 conds 包裹, 如 verify 字段写 {all:[...]} / 单条件 {check:..}) —— 原样返回
    if ("all" in step) or ("any" in step) or ("op" in step and "items" in step):
        return step
    if "check" in step:
        return step
    return {}
