# -*- coding: utf-8 -*-
"""自走棋局势大脑(S11) —— 回答"身材大不大、打不打得赢、要不要变阵、怎么针对"。

为什么单独开这一层
==================
auto_battle 以前的决策只有一句"命中 want 清单就买"，【没有任何关于胜负的判断】：
不知道自己这盘身材够不够、不知道对面的威胁是什么、更不会看局势换方向。
这一层把这几件事做成纯函数（输入棋子 → 输出判断），离线用真实 S11 表就能测，
接进自动对局循环后不用连游戏也能验证结论对不对。

四件事
======
1) 身材/机制建模 —— 盾、护甲、毒、经济、回手、志继这些【不写在攻防数字里】的东西，
   单独折算成"有效攻防"；只看 ATK/DEF 会把 1/1 产虎符的棋子算成废卡。
2) 对拼预测 —— 双方有效攻防 → 谁先被打光（兰彻斯特式：血量÷对面输出 = 能撑多久）。
3) 针对建议 —— 看对手主流机制，反推我这盘该补什么（对面毒多 → 要护甲/回复，等等）。
4) 变阵计划 —— 现在这盘 + 想去的方向 → 该先扔谁、优先找谁。

口径硬规矩
==========
· 所有棋子数字/技能文本来自 core.tavern_kb（官方 TavernChessConfig.json），一个不手写；
· 折算系数是本模块的【模型参数】，集中在 EFFECT 里，改就改一处，且注释写清为什么；
· 它是【估算】不是模拟器：不还原站位、不还原出手顺序，只给方向和强弱，不当精确胜负。
"""
import re
import sys

from core import tavern_kb as KB
from core import silence as SILENCE   # 兜底不静默: 吞掉的异常记一笔(见 core/silence.py)

# ============================================================ 机制 → 有效属性折算
# 每一项的量级怎么定的：
#   · 盾 = 抵挡一次伤害 ≈ 多挨一下 → 有效血量按棋子平均血量(≈8)的 8% 记，最多叠 5 层；
#   · 护甲 = 减伤 → 比盾弱一档(6%)；
#   · 毒 = 每回合额外掉血 → 直接算进【输出】，且它无视护甲，是打肉的核心手段；
#   · 连击 = 多打一次 → 有效攻击 +10%/个；
#   · 当先 = 先手，先手方的输出先落地 → 小幅加成(5%)；
#   · 志继 = 阵亡触发，层数越多收尾越强 → 攻防各 +0.5/层；
#   · 随征/召唤 = 多一个单位 → 直接加血(随征 3 / 召唤 2)；
#   · 回复/群体/成长 = 续航与滚雪球 → 小幅百分比；
#   · 经济/回手 = 不打仗，只影响【运营分】，单独记，不进有效攻防。
EFFECT = {
    "盾":     {"hp_mul": 0.08, "cap": 5},
    "护甲":   {"hp_mul": 0.06, "cap": 5},
    "毒":     {"atk_add": 1.5, "cap": 4},
    "连击":   {"atk_mul": 0.10, "cap": 4},
    "当先":   {"atk_mul": 0.05, "cap": 2},
    "回复":   {"hp_mul": 0.04, "cap": 4},
    "成长":   {"atk_add": 0.5, "cap": 6},
    "随征":   {"hp_add": 3.0, "cap": 4},
    "召唤":   {"hp_add": 2.0, "cap": 4},
    "群体":   {"atk_mul": 0.04, "cap": 4},
    "削弱":   {"atk_add": 0.4, "cap": 4},
    "增益":   {"atk_add": 0.4, "cap": 6},
}
# 只在运营层面有价值的机制(不进战斗折算)
ECON_TAGS = ("经济", "回手", "抽卡")


def _int(v, d=0):
    try:
        s = str(v or "").strip()
        return int(float(s)) if s else d
    except Exception:
        return d


# ============================================================ 归一化
def normalize_piece(x, season="s11"):
    """两种输入都认 → 统一档案。

    ① 协议里的板面: {chessID, attack, hp, ...}
    ② 决策用的棋子档案: tavern_kb.load_chess 的 value
    返回 None 表示认不出来(不是棋子)。
    """
    if not isinstance(x, dict):
        return None
    cid = str(x.get("chessID") or x.get("id") or "").strip()
    info = {}
    if cid:
        info = KB.load_chess(season).get(cid) or {}
    atk = _int(x.get("attack") or x.get("atk") or x.get("totalAttack") or info.get("atk"))
    dfn = _int(x.get("hp") or x.get("def") or x.get("totalHp") or info.get("def"))
    if not cid and not atk and not dfn:
        return None
    tags = list(x.get("tags") or info.get("tags") or [])
    desc = str(x.get("effect") or x.get("desc") or info.get("desc") or "")
    # 协议给的板面没有 tags，用描述现场打一遍标签(同一套规则，不会两套口径)
    if not tags and desc:
        tags = sorted(KB.mech_tags(desc))
    return {
        "id": cid,
        "name": str(x.get("name") or info.get("name") or cid or "?"),
        "atk": atk,
        "def": dfn,
        "rank": _int(x.get("cost") or x.get("rank") or info.get("rank"), 1) or 1,
        "faction": str(x.get("faction") or info.get("faction_name") or ""),
        "tags": tags,
        "desc": desc,
    }


def normalize_board(board, season="s11"):
    out = []
    for x in (board or []):
        p = normalize_piece(x, season)
        if p:
            out.append(p)
    return out


# ============================================================ 1. 有效攻防
def effective_stats(board, season="s11"):
    """一盘棋子 → 有效攻防 + 机制明细。

    返回的 atk/hp 是【折算过机制】的，不是表里的裸数字：
      例: 6 只 5/5 对面 6 只 4/4，裸看差不多；但我方带 2 个盾 + 1 个毒，
          折算后 hp 高一档、输出高一档，结论就从"五五开"变成"稳赢"。
    """
    ps = normalize_board(board, season)
    atk = sum(p["atk"] for p in ps)
    hp = sum(p["def"] for p in ps)

    tag_n = {}
    for p in ps:
        for t in (p.get("tags") or []):
            tag_n[t] = tag_n.get(t, 0) + 1

    atk_add = hp_add = 0.0
    atk_mul = hp_mul = 1.0
    detail = {}
    for tag, e in EFFECT.items():
        n = tag_n.get(tag, 0)
        if not n:
            continue
        n = min(n, int(e.get("cap", 99)))
        if e.get("atk_add"):
            a = e["atk_add"] * n
            atk_add += a
            detail[tag] = "+%.1f 攻" % a
        if e.get("hp_add"):
            h = e["hp_add"] * n
            hp_add += h
            detail[tag] = "+%.1f 血" % h
        if e.get("atk_mul"):
            atk_mul += e["atk_mul"] * n
            detail[tag] = "+%.0f%% 攻" % (100 * e["atk_mul"] * n)
        if e.get("hp_mul"):
            hp_mul += e["hp_mul"] * n
            detail[tag] = "+%.0f%% 血" % (100 * e["hp_mul"] * n)

    # 志继：阵亡联动是 S11 的主轴，按"总层数"折算(core.s11_enhance 那套判定)
    zhiji_layers = 0
    try:
        import core.s11_enhance as S11
        zhiji_layers = sum(S11.zhiji_layers(p.get("desc", "")) for p in ps)
        zhiji_trig = sum(1 for p in ps if S11.has_zhiji_trigger(p.get("desc", "")))
    except Exception:
        zhiji_trig = 0
    if zhiji_layers:
        atk_add += 0.5 * zhiji_layers
        hp_add += 0.5 * zhiji_layers
        detail["志继"] = "层数%d → +%.1f 攻/血" % (zhiji_layers, 0.5 * zhiji_layers)

    # 势力集中：主公/羁绊几乎都要求同势力，集中度低 = 一堆散卡
    fac = {}
    for p in ps:
        if p.get("faction"):
            fac[p["faction"]] = fac.get(p["faction"], 0) + 1
    total = max(1, len(ps))
    focus = (max(fac.values()) if fac else 0) / float(total)

    eatk = (atk + atk_add) * atk_mul
    ehp = (hp + hp_add) * hp_mul
    return {
        "n": len(ps),
        "atk": round(atk, 1), "hp": round(hp, 1),
        "e_atk": round(eatk, 1), "e_hp": round(ehp, 1),
        "raw_body": round(atk + hp, 1),
        "e_body": round(eatk + ehp, 1),
        "tags": dict(sorted(tag_n.items(), key=lambda kv: -kv[1])),
        "detail": detail,
        "zhiji_layers": zhiji_layers,
        "zhiji_trig": zhiji_trig,
        "factions": dict(sorted(fac.items(), key=lambda kv: -kv[1])),
        "focus": round(focus, 2),
        "econ_n": sum(tag_n.get(t, 0) for t in ECON_TAGS),
    }


def body_report(board, season="s11"):
    """给人看的一句话身材报告（界面/日志用）。"""
    s = effective_stats(board, season)
    if not s["n"]:
        return "空盘"
    top = "、".join("%s×%d" % (k, v) for k, v in list(s["tags"].items())[:5])
    return ("%d 只 | 裸 %d/%d → 有效 %.0f/%.0f | 势力集中度 %.0f%%%s%s"
            % (s["n"], s["atk"], s["hp"], s["e_atk"], s["e_hp"], 100 * s["focus"],
               (" | 机制: " + top) if top else "",
               (" | 志继%d层" % s["zhiji_layers"]) if s["zhiji_layers"] else ""))


# ============================================================ 2. 对拼预测
def predict_duel(mine, opp, season="s11"):
    """我这盘 vs 对面这盘，谁赢、赢多少。

    模型（明说是估算，不是模拟器）:
      · 不还原站位与出手顺序 —— 客户端也拿不到对面的站位顺序；
      · 用"血量 ÷ 对面输出 = 能撑几轮"这条兰彻斯特关系：
            t_me = 我有效血 / 对面有效攻     我能撑多久
            t_op = 对面有效血 / 我方有效攻    对面能撑多久
        t_me > t_op → 我先把对面打光。
      · 势力集中度高的那边额外吃 5% 加成（羁绊/主公都在奖励同势力）。
    """
    a = effective_stats(mine, season)
    b = effective_stats(opp, season)
    if not a["n"] or not b["n"]:
        return {"ok": False, "reason": "有一边是空盘(还没上阵/还没读到对面)",
                "me": a, "opp": b}

    fa = 1.0 + 0.05 * a["focus"]
    fb = 1.0 + 0.05 * b["focus"]
    atk_a = max(1.0, a["e_atk"] * fa)
    atk_b = max(1.0, b["e_atk"] * fb)
    hp_a = max(1.0, a["e_hp"])
    hp_b = max(1.0, b["e_hp"])

    t_me = hp_a / atk_b
    t_op = hp_b / atk_a
    margin = (t_me - t_op) / max(t_me, t_op)          # -1(惨败) ~ +1(碾压)
    win = margin > 0
    # 把 margin 压成 0~1 的"把握度"，方便界面显示与阈值判断
    conf = min(1.0, abs(margin) * 2.2)
    if abs(margin) < 0.06:
        verdict = "五五开"
    elif win:
        verdict = "稳赢" if conf > 0.66 else "小优"
    else:
        verdict = "打不过" if conf > 0.66 else "小劣"

    why = []
    if a["e_atk"] > b["e_atk"] * 1.15:
        why.append("我方输出更高(%.0f vs %.0f)" % (a["e_atk"], b["e_atk"]))
    elif b["e_atk"] > a["e_atk"] * 1.15:
        why.append("对面输出更高(%.0f vs %.0f)，要考虑补盾/护甲" % (b["e_atk"], a["e_atk"]))
    if a["e_hp"] > b["e_hp"] * 1.15:
        why.append("我方更肉(%.0f vs %.0f)" % (a["e_hp"], b["e_hp"]))
    elif b["e_hp"] > a["e_hp"] * 1.15:
        why.append("对面更肉(%.0f vs %.0f)，毒/成长能破" % (b["e_hp"], a["e_hp"]))
    if a["focus"] > b["focus"] + 0.2:
        why.append("我方势力更集中(羁绊/主公吃得住)")
    elif b["focus"] > a["focus"] + 0.2:
        why.append("对面势力更集中，单卡质量拼不过要走机制")
    if a["zhiji_layers"] > b["zhiji_layers"] + 2:
        why.append("我方志继层数更多(阵亡联动更强)")
    return {
        "ok": True, "win": win, "verdict": verdict,
        "margin": round(margin, 3), "conf": round(conf, 2),
        "me": a, "opp": b,
        "why": why or ["两边折算后几乎一样，看谁先手/谁站位好"],
    }


# ============================================================ 3. 针对建议
# 对面是什么路子 → 我该补什么。规则来自"哪类效果克制哪类效果"，不是拍脑袋。
COUNTER_RULES = [
    # (对面特征判定, 该补的机制, 为什么)
    (lambda s: s["tags"].get("毒", 0) >= 2, "回复", "对面毒多(毒无视护甲直掉血)，要续航顶过去"),
    (lambda s: s["tags"].get("毒", 0) >= 2, "护甲", "护甲能拖慢毒以外的伤害，给回复争取时间"),
    (lambda s: s["e_atk"] > s["e_hp"] * 1.25, "盾", "对面是高攻低血的爆发流，盾能吃掉第一波"),
    (lambda s: s["e_atk"] > s["e_hp"] * 1.25, "护甲", "减伤对高频攻击收益最大"),
    (lambda s: s["e_hp"] > s["e_atk"] * 1.6, "毒", "对面是肉盾流，纯攻砍不动，得上毒"),
    (lambda s: s["e_hp"] > s["e_atk"] * 1.6, "成长", "滚雪球才有输出越过他的血量线"),
    (lambda s: s["tags"].get("随征", 0) + s["tags"].get("召唤", 0) >= 2, "群体", "对面铺场，单点击杀没用，要AOE"),
    (lambda s: s["tags"].get("随征", 0) + s["tags"].get("召唤", 0) >= 2, "削弱", "压他全体属性，铺场流最怕这个"),
    (lambda s: s["econ_n"] >= 2, "当先", "对面在攒经济，后期会比你有钱 → 要抢先手压血"),
    (lambda s: s["econ_n"] >= 2, "连击", "单位时间输出翻倍，趁他还没成型打崩"),
    (lambda s: s["tags"].get("盾", 0) + s["tags"].get("护甲", 0) >= 3, "毒", "盾/护甲挡普通伤害，毒绕过去"),
    (lambda s: s["tags"].get("连击", 0) >= 2, "护甲", "对面出手次数多，减伤收益被放大"),
]


def counter_advice(opponents, mine=None, season="s11"):
    """看一圈对手 → 我这盘该补什么机制。

    opponents: 多个对手的板面列表(list of board)，也可以是 [(名字, board)]。
    返回 {need:[{tag, why, hits}], threats:[{who, profile, danger}], my_profile}
    """
    boards = []
    for o in (opponents or []):
        if isinstance(o, tuple) and len(o) == 2:
            boards.append((str(o[0]), o[1]))
        else:
            boards.append(("", o))

    profs = []
    for who, b in boards:
        s = effective_stats(b, season)
        if s["n"]:
            profs.append((who, s))
    if not profs:
        return {"ok": False, "reason": "还没读到任何对手阵容(打过一轮或点过头像才有)",
                "need": [], "threats": []}

    me = effective_stats(mine, season) if mine else None
    # 谁最危险：相对我方的胜率最高
    threats = []
    for who, s in profs:
        if me and me["n"]:
            d = predict_duel([{"id": "", "attack": s["e_atk"], "hp": s["e_hp"],
                               "faction": "", "tags": []}], mine, season)
            # 上面是粗略比，直接用折算值算更直观
            danger = (s["e_atk"] / max(1.0, me["e_hp"])) - (me["e_atk"] / max(1.0, s["e_hp"]))
        else:
            danger = (s["e_atk"] + s["e_hp"]) / 40.0
        threats.append({"who": who or "(未命名)", "profile": s, "danger": round(danger, 3)})
    threats.sort(key=lambda x: -x["danger"])

    # 需求：每条规则在几个对手身上命中 → 命中越多越该补
    need = {}
    for _who, s in profs:
        for cond, tag, why in COUNTER_RULES:
            try:
                if cond(s):
                    r = need.setdefault(tag, {"tag": tag, "hits": 0, "why": why})
                    r["hits"] += 1
            except Exception:
                continue
    # 我已经有的机制就别再喊着补
    my_tags = set((me or {}).get("tags") or {})
    out = []
    for tag, r in need.items():
        if tag in my_tags:
            continue
        out.append(r)
    out.sort(key=lambda r: -r["hits"])
    return {"ok": True, "need": out, "threats": threats[:4],
            "my_profile": me, "scanned": len(profs)}


# ============================================================ 4. 变阵计划
def transition_plan(board, want_faction=None, want_tags=None, season="s11",
                    drop_n=2, want_n=8):
    """现在这盘 + 想去的方向 → 先扔谁、优先找谁。

    排序口径: "留下价值" = 有效贡献(攻防) + 机制分 + 方向匹配度。
      方向匹配 = 势力对得上 / 机制是想要的 → 加权；都不沾的最低，先扔。
    """
    ps = normalize_board(board, season)
    want_tags = set(want_tags or [])
    pool = KB.load_chess(season)
    owned = {}
    for p in ps:
        owned.setdefault(p["name"], 0)
        owned[p["name"]] += 1

    def value(p):
        v = (p["atk"] + p["def"]) * 1.0 + 2.0 * KB.mech_score(p)
        if want_faction and p.get("faction") == want_faction:
            v += 6.0
        hit = len(set(p.get("tags") or []) & want_tags)
        v += 4.0 * hit
        return v

    ranked = sorted(ps, key=lambda p: -value(p))
    keep = ranked[:max(0, len(ranked) - drop_n)]
    drop = ranked[max(0, len(ranked) - drop_n):]

    cands = []
    for c in pool.values():
        # 目标势力优先; 不是目标势力但正好是想要的机制(跨阵营的功能核)也留着
        if want_faction and c.get("faction_name") != want_faction:
            if not (set(c.get("tags") or []) & want_tags):
                continue
        cands.append(c)

    def want_value(c):
        v = KB.power_score(c) + 4.0 * len(set(c.get("tags") or []) & want_tags)
        # ★ 目标势力必须明显优先: 不加权的话跨阵营的功能核会盖过主角,
        #   喊着"去蜀"结果推荐一堆西凉卡, 看着就像算错了。
        if want_faction and c.get("faction_name") == want_faction:
            v += 12.0
        # 速查图鉴标的"体系核心卡"(曹轶/典韦/陆逊…): 值得为它临时拐一下, 但盖不过主线
        if c.get("is_core"):
            v += KB.CORE_BONUS
        return v

    cands.sort(key=lambda c: -want_value(c))
    # ★ 先去掉已经有的, 再取前 want_n 张。以前是【先截断再过滤】, 已经拥有的卡
    #   白占名额 —— 实测手上 6 张吴时, 排前 8 的吴卡里有 6 张已经有了, 过滤完只剩
    #   2 张, "该找谁"直接少一大半(名字里那句"已经有了的就别再喊着找"是对的,
    #   但顺序反了)。
    want = [c for c in cands if owned.get(c.get("name"), 0) == 0][:want_n]

    return {
        "keep": [{"name": p["name"], "why": "%.0f 身价" % value(p)} for p in keep],
        "drop": [{"name": p["name"], "why": "方向外/贡献最低(%.0f)" % value(p)} for p in drop],
        "want": [{"id": c["id"], "name": c["name"],
                  "why": "%d费 %d/%d %s" % (c.get("rank", 0), c.get("atk", 0), c.get("def", 0),
                                            "/".join((c.get("tags") or [])[:3]) or "无机制")}
                 for c in want],
        "faction": want_faction or "", "tags": sorted(want_tags),
    }


# ============================================================ 4b. 体系识别 / 转阵(v4.142★)
# 为什么补这一节: transition_plan 只回答"给定方向怎么转", 而"我现在在玩什么"和
#   "该不该转"这两件事项目里【从来没有过】—— effective_stats 只吐瞬时统计(势力计数/
#   集中度), 没有"体系"概念, 于是引擎只能开局定死阵容、全程不改。
#   用户要的"看刷新出来考虑玩什么、怎么变阵"核心就是这三条链:
#     detect_system(现在在玩什么) → pivot_options(能转去哪) → should_pivot(该不该转)
#   → transition_plan(怎么转) → 引擎执行(改 _plan + 遣散)。

# 转阵的【死线】: 过了这个回合就不再转。
#   ★ 这是估计值, 不是官方数据: 自走棋一局大约十几回合, 后段转阵手续费+掉身材
#     回不来。真跑出对局样本后应该按实测淘汰回合重调(见 dev/_verify_pivot.py)。
PIVOT_DEADLINE_TURN = 9
# 收益门槛: 新方向要比现状高出这么多才值得动。低于它就是"换个方向也差不多",
#   动了只会白扔手续费 —— 而这种抖动界面上只会看到"助手一直在卖卡买卡"。
PIVOT_GAIN_MIN = 6.0


def faction_cores(season, faction):
    """某势力的【体系核心卡】档案列表(速查图鉴标的 core)。

    项目里没有独立的"体系核心卡清单", 只有逐棋子的 is_core 布尔 + 势力,
    s11_enhance.lord_pick_advice 也是这么用的 —— 口径统一, 不另造一套。
    """
    if not faction:
        return []
    out = []
    for c in (KB.load_chess(season) or {}).values():
        if c.get("faction_name") == faction and c.get("is_core"):
            out.append(c)
    out.sort(key=lambda c: -KB.power_score(c))
    return out


def detect_system(board, season="s11", lord_faction=None):
    """现在这盘在玩什么 → 势力 + 体系名 + 已有/还缺哪些核心卡 + 置信度。

    返回 {faction, faction_n, focus, system, sys_tag, cores_have, cores_missing,
          confidence, why}
    """
    ps = normalize_board(board, season)
    if not ps:
        return {"faction": "", "faction_n": 0, "focus": 0.0, "system": "空盘",
                "sys_tag": "", "cores_have": [], "cores_missing": [],
                "confidence": 0.0, "why": "手上还没有牌"}

    st = effective_stats(board, season)
    fac = st.get("factions") or {}
    main_f, main_n = (max(fac.items(), key=lambda kv: kv[1]) if fac else ("", 0))
    focus = float(st.get("focus") or 0.0)

    # 主导机制: 志继是 S11 主轴(s11_enhance 全套判定都围着它), 单列;
    #   否则取张数最多的机制标签, 至少 2 张才算"在打这个机制"。
    sys_tag = ""
    if int(st.get("zhiji_layers") or 0) >= 2 or int(st.get("zhiji_trig") or 0) >= 2:
        sys_tag = "志继"
    else:
        for t, n in (st.get("tags") or {}).items():
            if n >= 2:
                sys_tag = t
                break

    have = {}
    for p in ps:
        have[p["name"]] = have.get(p["name"], 0) + 1
    cores = faction_cores(season, main_f)
    cores_have = [c["name"] for c in cores if c["name"] in have]
    cores_missing = [c["name"] for c in cores if c["name"] not in have]

    # 置信度: 势力集中度为主, 拿到核心卡加分, 牌太少打折。
    #   它决定"要不要听这个体系的话" —— 置信度低时硬转就是瞎动。
    conf = focus * 0.7
    if cores:
        conf += 0.3 * (len(cores_have) / float(min(3, len(cores))))
    if len(ps) < 4:
        conf *= 0.6
    conf = max(0.0, min(1.0, conf))

    system = main_f or "杂"
    if sys_tag:
        system = "%s·%s" % (main_f or "杂", sys_tag)
    return {
        "faction": main_f, "faction_n": main_n, "focus": round(focus, 2),
        "system": system, "sys_tag": sys_tag,
        "cores_have": cores_have, "cores_missing": cores_missing[:4],
        "confidence": round(conf, 2),
        "why": "%s%d只(集中度%.0f%%), 核心%d/%d%s" % (
            main_f, main_n, 100 * focus, len(cores_have), len(cores),
            (", 志继%d层" % st["zhiji_layers"]) if st.get("zhiji_layers") else ""),
    }


def pivot_options(board, season="s11", banned=None, lord_faction=None,
                  current_faction=None, top=3):
    """能转去哪 → 每个方向打分, 返回 top 个 [{faction, score, why}]。

    ★ 每一项加分的依据都写进 why, 界面上要能原样看到(用户要能判断它说得对不对):
      · 手上已有该势力的张数 × 2.0 —— 沉没成本: 转过去这些卡立刻能用, 留着就是废牌
      · 该势力还剩多少张可买 min(n,25) × 0.3 —— 转过去之后还买得到东西
      · 该势力核心卡数 min(n,6) × 1.5 —— 有核心卡才成体系, 一堆散卡转过去也没用
      · 该势力志继棋子数 × 1.0 —— S11 主轴
      · 主公依赖 +10 —— 主公技能绑这个势力, 转过去才吃得到加成
      · 现状惯性 +3 —— 已经在走的体系。★ 不加这条会在两个差不多的方向之间反复横跳
    """
    banned = set(str(b) for b in (banned or []) if str(b))
    ps = normalize_board(board, season)
    pool = KB.load_chess(season) or {}

    held = {}
    for p in ps:
        f = p.get("faction")
        if f:
            held[f] = held.get(f, 0) + 1

    # 每个势力: 还剩多少张可买 / 几张核心 / 几张志继
    pool_n, core_n, zhiji_n = {}, {}, {}
    try:
        import core.s11_enhance as S11
    except Exception as e:
        # S11 拿不到就只按卡池算(志继是加分项, 缺了不至于算错方向), 但不能静默
        SILENCE.note("battle_brain.pivot_options", "S11 不可用, 本次不算志继维度: %s" % e)
        S11 = None
    for c in pool.values():
        f = c.get("faction_name")
        if not f or f in banned:
            continue
        pool_n[f] = pool_n.get(f, 0) + 1
        if c.get("is_core"):
            core_n[f] = core_n.get(f, 0) + 1
        if S11 is not None:
            d = str(c.get("desc") or "")
            try:
                if S11.zhiji_layers(d) or S11.has_zhiji_trigger(d):
                    zhiji_n[f] = zhiji_n.get(f, 0) + 1
            except Exception as e:
                SILENCE.note("battle_brain.pivot_options.zhiji(%s)" % f,
                             "志继判定失败: %s" % e)

    total_held = sum(held.values())
    out = []
    for f in set(list(pool_n.keys()) + list(held.keys())):
        if f in banned:
            continue
        score = 0.0
        bits = []
        h = held.get(f, 0)
        if h:
            score += 2.0 * h
            bits.append("手上%d只" % h)
        # ★ 要扔掉的卡(方向外的) = 转过去的成本。这条【不加就永远不转】:
        #   光比"哪个势力客观更强"的话, 志继多/核心多的强势力的静态优势会一直压过
        #   手上持有什么 —— 实测手上蜀2吴5时, 蜀靠"志继11张"拿了 11 分静态优势,
        #   结果 best 永远是蜀, should_pivot 从头到尾返回"现在走的就是最优方向",
        #   变阵功能等于没生效。
        drop_n = total_held - h
        if drop_n:
            score -= 1.5 * drop_n
            bits.append("要扔%d" % drop_n)
        if pool_n.get(f):
            score += min(pool_n[f], 25) * 0.3
            bits.append("可买%d" % pool_n[f])
        if core_n.get(f):
            score += min(core_n[f], 6) * 1.5
            bits.append("核心%d" % core_n[f])
        if zhiji_n.get(f):
            # cap 6: 志继张数是"这个势力有多少张带志继", 是卡池属性不是我的战斗力,
            #   不封顶的话(蜀实测 11 张)它会一家独大, 把其他维度全盖掉。
            score += min(zhiji_n[f], 6) * 1.0
            bits.append("志继%d" % zhiji_n[f])
        if lord_faction and f == lord_faction:
            score += 10.0
            bits.append("主公绑它")
        # ★ 这里【不给现状加惯性分】: 以前给 current_faction +3 想防"两个方向间横跳",
        #   结果防抖做了两遍(这里 +3, should_pivot 里又有 PIVOT_GAIN_MIN 门槛),
        #   而且惯性会污染客观打分 —— 实测手上蜀1吴6这种明显该转的情况, 蜀靠惯性+
        #   志继只差 5.5 分, 正好卡在门槛下, 该转的转成不转。
        #   防抖统一交给 should_pivot 的门槛 + 触发条件, 这里只回答"哪个方向客观更好"。
        out.append({"faction": f, "score": round(score, 1), "why": "/".join(bits) or "无"})
    out.sort(key=lambda x: (-x["score"], x["faction"]))
    return out[:top]


def should_pivot(board, season="s11", banned=None, lord_faction=None,
                 current_faction=None, turn=0, hp=30, lose_streak=0,
                 rounds_left=None):
    """该不该转 → {pivot, to, gain, drop_n, why, now, options}。

    ★ 三条【不转】的死线(满足任一直接回 False, 都写在 why 里):
      ① 回合过了死线(turn >= PIVOT_DEADLINE_TURN): 后段转阵手续费+掉身材回不来。
         (这个 9 是估计值不是官方数字, 见上面 PIVOT_DEADLINE_TURN 的注释)
      ② 手上不到 4 张牌: 这时候是"还没成型", 谈不上"转", 该怎么拿怎么拿
      ③ 转过去的收益 < PIVOT_GAIN_MIN: 换个方向也差不多, 动了只会白扔手续费 ——
         界面上表现为"助手一直在卖卡买卡", 看着像抽风。
    触发(任一即可, 但还要过上面三条才真转):
      a) 连败 >= 2 且 当前体系核心卡不足
      b) 当前体系缺核心卡 >= 2(核心凑不齐, 体系起不来)
      c) 手上别的势力张数 > 当前势力(商店一直在喂别的势力, 顺势转更省)
    """
    ps = normalize_board(board, season)
    now = detect_system(board, season, lord_faction=lord_faction)
    options = pivot_options(board, season, banned=banned,
                            lord_faction=lord_faction,
                            current_faction=current_faction or now.get("faction"))
    cur_f = current_faction or now.get("faction") or ""
    cur_score = 0.0
    best = None
    for o in options:
        if o["faction"] == cur_f:
            cur_score = o["score"]
        if best is None or o["score"] > best["score"]:
            best = o

    def _no(why):
        return {"pivot": False, "to": cur_f, "gain": 0.0, "drop_n": 0,
                "why": why, "now": now, "options": options}

    if not ps or len(ps) < 4:
        return _no("手上才 %d 张, 还没成型, 谈不上转" % len(ps))
    if turn and turn >= PIVOT_DEADLINE_TURN:
        return _no("已经第 %d 回合(死线 %d), 这时候转回不了本"
                   % (turn, PIVOT_DEADLINE_TURN))
    if best is None or not options:
        return _no("没有可转的方向(可能势力全被禁了)")
    gain = round(best["score"] - cur_score, 1)
    if best["faction"] == cur_f:
        return _no("现在走的 %s 就是最优方向(领先 %.1f)" % (cur_f, -gain))
    if gain < PIVOT_GAIN_MIN:
        return _no("转到 %s 只多 %.1f 分(门槛 %.1f), 不值得动"
                   % (best["faction"], gain, PIVOT_GAIN_MIN))

    # ---- 触发条件: 得有【理由】才转, 不能因为"那边分高一点"就动 ----
    reasons = []
    miss = len(now.get("cores_missing") or [])
    held = {}
    for p in ps:
        f = p.get("faction")
        if f:
            held[f] = held.get(f, 0) + 1
    cur_n = held.get(cur_f, 0)
    other_top = max((v for f, v in held.items() if f != cur_f), default=0)

    if lose_streak >= 2 and miss >= 1:
        reasons.append("连败%d场且%s还缺%s" % (lose_streak, cur_f,
                                              "/".join(now["cores_missing"][:2]) or "核心"))
    if miss >= 2:
        reasons.append("%s核心卡还缺%d张(%s), 体系起不来"
                       % (cur_f, miss, "/".join(now["cores_missing"][:3])))
    if other_top > cur_n:
        reasons.append("手上别的势力(%d只)已经比%s(%d只)多, 商店在喂别的"
                       % (other_top, cur_f, cur_n))
    if not reasons:
        return _no("没有触发条件: 没连败/%s核心不缺/也没被喂偏(转到%s多%.1f分也不动)"
                   % (cur_f, best["faction"], gain))

    # ---- 成本: 要扔几张(方向外的卡) ----
    drop_n = sum(v for f, v in held.items() if f != best["faction"] and f != "")
    # ---- v4.134★ 棋子级路线: "转去吴"之外再给一句"谁换谁" ----
    #   势力级答案是算出来的(上面 best), 但"到底把哪张换成哪张"是套路, 来自攻略库。
    #   它只是【附加说明】: 读不到就是空列表, 不影响 pivot/gain 这些真判断。
    routes = []
    try:
        from core import pivot_routes
        routes = pivot_routes.find_routes([p.get("name") for p in ps], limit=2)
    except Exception as e:
        try:
            SILENCE.note("core/battle_brain.py:should_pivot", "变阵路线库: %s" % e)
        except Exception as _e2:
            print("[battle_brain] 记账失败: %s" % _e2, file=sys.stderr)
        routes = []
    return {"pivot": True, "to": best["faction"], "gain": gain, "drop_n": drop_n,
            "why": "；".join(reasons) + " → 转%s(多%.1f分)" % (best["faction"], gain),
            "routes": routes, "now": now, "options": options}


# ============================================================ 5. 经济自由度
def econ_freedom(coin, level=1, buy_cost=3, refresh_cost=1, rounds_left=6):
    """钱够到什么程度 → 该存还是该花。

    为什么要算这个: "经济自由"不是钱越多越好 —— 钱的价值【随剩余回合数衰减】
    (econ_planner 里的 LATE_DECAY 就是这件事)。留着 30 金币结束 = 全浪费。

    阈值口径:
      · 保命线 = 还能买 2 张 + 刷 2 次(遇到能翻盘的卡还有得操作);
      · 自由线 = 保命线 × 2 —— 过了这条线, 每回合都可以"刷到满意为止",
        这时候的正确动作是【提质量】(升级商店/换高费核心), 而不是继续攒。
    """
    coin = _int(coin)
    base = buy_cost * 2 + refresh_cost * 2
    free = base * 2
    late = rounds_left <= 3
    if coin < base:
        act, why = "继续攒", "低于保命线 %d，一点余量都没有，刷了就没钱买了" % base
    elif coin < free:
        act, why = "正常运营", "%d~%d 之间：够买不够随便刷，优先买命中方向的卡" % (base, free)
    else:
        act, why = "经济自由 → 提质量", "超过 %d：该升级商店/换高费核心，钱捂到最后就是废纸" % free
    if late:
        act = "最后几回合 → 全花掉"
        why += "；只剩 %d 回合，留钱没有任何意义" % rounds_left
    return {"coin": coin, "keep_line": base, "free_line": free,
            "act": act, "why": why, "late": late, "level": _int(level, 1)}


# ============================================================ 自测
def _self_test():
    import json
    chess = KB.load_chess("s11")

    # 造两盘真实 S11 棋子：一盘蜀志继，一盘群肉盾
    shu = [c for c in chess.values() if c.get("faction_name") == "蜀"][:7]
    qun = [c for c in chess.values() if c.get("faction_name") == "群"][:7]

    print("== 身材报告(蜀) ==")
    print("  " + body_report(shu))
    print("== 身材报告(群) ==")
    print("  " + body_report(qun))

    print("== 对拼预测 ==")
    d = predict_duel(shu, qun)
    print("  ", d.get("verdict"), "margin=", d.get("margin"), "conf=", d.get("conf"))
    for w in d.get("why", []):
        print("   -", w)

    print("== 针对建议(对面是群那盘) ==")
    ca = counter_advice([("对手A", qun)], mine=shu)
    for r in ca.get("need", []):
        print("   补 %s(命中%d个对手): %s" % (r["tag"], r["hits"], r["why"]))
    if not ca.get("need"):
        print("   (对面没有明显流派特征 → 不用特意针对，按自己方向走)")

    # 造一盘极端肉盾 + 铺场，验证"克制规则"真会被触发(上面那两盘特征太弱，一条都没命中)
    tanky = [{"id": "", "name": "肉盾%d" % i, "attack": 1, "hp": 14,
              "faction": "群", "tags": ["随征"], "desc": "随征"} for i in range(6)]
    ca2 = counter_advice([("肉盾流", tanky)], mine=shu)
    print("== 针对建议(对面是肉盾+铺场) ==")
    for r in ca2.get("need", []):
        print("   补 %s(命中%d个对手): %s" % (r["tag"], r["hits"], r["why"]))

    print("== 变阵计划(想去蜀 + 志继/成长) ==")
    tp = transition_plan(qun, want_faction="蜀", want_tags=["成长", "志继"], drop_n=2)
    print("   扔:", "、".join(x["name"] for x in tp["drop"]))
    print("   找:", "、".join(x["name"] for x in tp["want"][:6]))

    print("== 经济自由度 ==")
    for c in (3, 8, 20):
        e = econ_freedom(c, level=4, rounds_left=6)
        print("   %d 金 → %s(%s)" % (c, e["act"], e["why"]))

    print("== 协议板面(chessID/attack/hp)也能吃 ==")
    proto = [{"chessID": c["id"], "attack": c["atk"], "hp": c["def"]} for c in shu[:4]]
    print("  " + body_report(proto))
    print("SELF_TEST_OK")


if __name__ == "__main__":
    _self_test()
