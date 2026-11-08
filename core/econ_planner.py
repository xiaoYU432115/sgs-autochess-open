# -*- coding: utf-8 -*-
"""L0 经济决策器 —— 每回合回答"这回合该存钱、刷新、升级, 还是买哪张"。

为什么先做它: 自走棋里真正值钱、也最适合算的决策就是【经济决策】。它状态小(金币/等级/
   商店/回合)、动作少(存/刷/升/买), 而且真实数值本季已经落地(卡池 1费18/2费15/3费13/
   4费11/5费9/6费7, 各等级各费用概率, 刷新1/买3/卖1, 升级费 0/6/12/18/20/22)。
   ——不需要训练、不需要模拟器, 前向搜索就能给出比拍脑袋好的建议, 而且能说清理由。

怎么做: 枚举候选动作 → 每个动作蒙特卡洛 rollout(模拟刷几次出货/买到什么) →
   效用 = 期望战力增益 + 剩余金币 × 金币的未来价值 → 取最高的那个。
   它不是"最优解", 是"把账摊开给你看": 每个候选的期望收益、花费、命中概率都列出来。

⚠ 两条必须知道的偏差(别把它当成精确值):
   1) 卡池"还剩多少"【只能是上界】: 发牌在服务器, 客户端没有公共卡池, LIVE.pool_taken
      只记你自己买/卖的。别人拿走的算不进来 —— 所以真实概率比这里算的【略高】。
   2) 每回合收入【只按基础 3 算】(配置表 BaseIncrement=3, 真值)。这游戏没有利息机制,
      别的加成(连胜之类, 如果有的话)也没算进来 —— 所以"存钱"这一路的收益会被【低估】。

只读: 不发包、不改任何游戏状态。和 refresh_advisor 一样。
"""
import random
import sys
import time

from core import refresh_advisor as RA
from core import tavern_kb as KB

# 商店格数随等级(1级3格 / 2~3级4格 / 4~5级5格 / 6级6格) —— 直接复用刷新决策器那张表,
# 免得两处各写一份、改一处忘一处。
_SHOP_SLOTS = RA._SHOP_SLOTS

INCOME_BASE = 3          # 每回合基础收益(配置表 BaseIncrement=3, 真值)
                         # 注意: 这游戏没有利息, 别再往上加"存得多给得多"的项
BUY_EFFICIENCY = 0.55    # 金币→战力的转化率折扣: 不是每一金都花得出去/买得到有用的
WANT_RATE = 0.35         # 通用刷牌(没填目标卡)时, 一次刷新"碰上的正好是你用得上的"的概率
LATE_DECAY = 0.05        # 每过一个回合, 钱的边际价值掉多少(越后期越不值钱)

_tier_cache = {"ts": 0.0, "vals": {}, "n": 0}


def _int(v, d=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return d


def _card_value(row):
    """单卡战力价值 —— 口径跟 id_map.board_strength 保持一致: 费用*2 + 血 + 攻*1.5。

    为什么要一致: 这样"买这张涨多少战力"能直接跟阵容强度评分对齐, 不会出现两个模块
    各说各的数。

    ★ 再加一项: 速查图鉴标的"体系核心卡"按 KB.CORE_BONUS 加分。
      纯身材口径会低估那些【身材平平但缺了它体系就转不起来】的卡(典型: 曹轶 4/5,
      数字很普通, 实际是魏国加属性的发动机)。不加分的话蒙特卡洛会倾向"买身材大的",
      而自走棋里身材从来不是全部 —— 盾/护甲/经济/毒都不在数字里。
    """
    cost = _int(row.get("rank") or row.get("cost"))
    hp = _int(row.get("def") or row.get("hp") or row.get("totalHp"))
    atk = _int(row.get("atk") or row.get("totalAttack"))
    v = cost * 2.0 + hp + atk * 1.5
    # 核心卡加分。KB 是模块级导入的(不在函数里 import —— 项目有"静默 except: pass
    # 只许减不许增"的棘轮检查, 用 try/except 兜 import 会把它顶上去)。
    if row.get("is_core"):
        v += float(getattr(KB, "CORE_BONUS", 0.0) or 0.0)
    return v


def tier_values(ttl=300):
    """各费用档的【平均单卡价值】(从本季卡池实算, 带 5 分钟缓存 —— 卡池 300KB 别每次读)。"""
    now = time.time()
    if _tier_cache["vals"] and now - _tier_cache["ts"] < ttl:
        return _tier_cache["vals"]
    acc = {}
    try:
        from core.lineup_advisor import LineupAdvisor
        pool = LineupAdvisor().card_pool() or {}
        for _cid, v in pool.items():
            if not isinstance(v, dict):
                continue
            r = _int(v.get("rank") or v.get("cost"))
            if r <= 0:
                continue
            a = acc.setdefault(r, [0.0, 0])
            a[0] += _card_value(v)
            a[1] += 1
    except Exception as e:
        sys.stderr.write("[经济决策器] 卡池读不到(按费用档粗估): %s: %s\n" % (type(e).__name__, e))
    vals = {r: (s / n) for r, (s, n) in acc.items()}
    if vals:
        _tier_cache.update({"ts": now, "vals": vals, "n": sum(n for _s, n in acc.values())})
    return vals


def _fallback_tier_values(vals):
    """卡池读不到时的兜底: 价值随费用档线性涨(只影响排序, 不影响正确性)。"""
    out = dict(vals or {})
    for r in range(1, 7):
        out.setdefault(r, r * 6.0)
    return out


def income():
    """下回合收入 = 基础 3。

    【这游戏没有利息】。之前按"每 10 金 +1、封顶 5"估过, 那是照搬别的自走棋, 错的:
    它会凭空造出"存钱越多、下回合越赚"的收益, 把存钱这一路高估好几金(实测在 12~30 金
    区间足够把结论从刷新翻成存钱)。现在只算表里的真值 BaseIncrement=3。
    要是实测还有别的加成(连胜之类), 在这儿加个参数即可。
    """
    return INCOME_BASE


def _avg_tier_value(level, snap, vals):
    """当前等级下, 商店里一张卡的【平均价值】(按费用档概率加权)。"""
    odds, _pool = RA._sfx(snap.get("_cfg"))
    row = odds.get(str(level)) or odds.get(level) or []
    tot = 0.0
    wsum = 0.0
    for i, p in enumerate(row):
        try:
            w = float(p)
        except (TypeError, ValueError):
            continue
        if w <= 0:
            continue
        tot += w * vals.get(i + 1, 0.0)
        wsum += w
    return (tot / wsum) if wsum else (sum(vals.values()) / max(1, len(vals)))


def gold_value(turn, snap, vals):
    """1 金在【未来】值多少战力 —— 用来把"省下来的钱"折算成效用, 跟眼前买卡的收益可比。"""
    buy = max(1, snap.get("buy_cost") or 3)
    lv = _int(snap.get("level")) or 1
    avg = _avg_tier_value(lv, snap, vals)
    eff = (avg / buy) * BUY_EFFICIENCY
    decay = max(0.35, 1.0 - max(0, _int(turn) - 5) * LATE_DECAY)
    return eff * decay


def _target_ids(targets):
    """用户填的目标卡(名字或 ID, 逗号分隔) → [(卡ID, 显示名)]。"""
    out = []
    for t in str(targets or "").replace("，", ",").split(","):
        t = t.strip()
        if not t:
            continue
        cid = None
        try:
            from core import id_map as IDMAP
            if t.isdigit():
                cid = int(t)
            else:
                # 反查用 find_ids_by_name: 它带【字形容错】(实测『马云禄』在表里写作
                # 『马云騄』), 自己按名字硬比对会查空、目标卡静默失效。
                ids = IDMAP.find_ids_by_name(t) or []
                cid = int(ids[0]) if ids else None
        except Exception as e:
            sys.stderr.write("[经济决策器] 目标卡「%s」查不到(按通用刷牌): %s\n" % (t, e))
            cid = None
        if cid:
            out.append((int(cid), t))
        else:
            sys.stderr.write("[经济决策器] 目标卡「%s」没查到 ID —— 按通用刷牌算\n" % t)
    return out


def _sim_shop_best(level, snap, tgt_odds, tgt_val, vals, rng):
    """模拟一次刷新的商店, 返回"这一刷能买到的最好一张值多少" (还没扣买卡钱)。

    抽 slots 格, 每格按本等级的费用档概率决定费用 → 用该档平均价值;
    指定了目标卡就额外判一次"这一刷看不看得见它"(用 rough_odds 的 p5)。
    """
    slots = _SHOP_SLOTS.get(_int(level), 5)
    odds, _pool = RA._sfx(snap.get("_cfg"))
    row = odds.get(str(level)) or odds.get(level) or []
    best = 0.0
    if row:
        cum = []
        s = 0.0
        for p in row:
            try:
                s += float(p)
            except (TypeError, ValueError):
                s += 0.0
            cum.append(s)
        if s > 0:
            for _ in range(slots):
                x = rng.random() * s
                r = 1
                for i, c in enumerate(cum):
                    if x <= c:
                        r = i + 1
                        break
                v = vals.get(r, 0.0)
                if v > best:
                    best = v
    if tgt_odds and tgt_val and rng.random() < float(tgt_odds.get("p5") or 0):
        if tgt_val > best:
            best = tgt_val
    return best


def _target_odds(ids, row, level, snap):
    """目标卡"这一刷看得到它"的概率 —— 【自己算】, 不用 RA.rough_odds。

    为什么不用现成的那个: rough_odds 内部靠 chess_cost_of(cid) 求费用档, 而
      find_ids_by_name 返回的 ID(实测张梁 = 206109 / 20204081…)跟卡池的 key
      (21001011 这种)【不是同一套】—— chess_cost_of 一律返回 None, rough_odds 就直接
      返回 None 了。结果是: 用户明明填了目标卡, 却跟没填一样(静默降级成通用刷牌)。
    这里改用【卡池按名字查出来的 rank】当费用档, 绕开 ID 那套不通的路。
    """
    r = _int((row or {}).get("rank") or (row or {}).get("cost"))
    if not r:
        return None
    odds, pool = RA._sfx(snap.get("_cfg"))
    orow = odds.get(str(level)) or odds.get(level) or []
    try:
        p_tier = float(orow[r - 1]) / 100.0
    except (TypeError, ValueError, IndexError):
        return None
    slots = _SHOP_SLOTS.get(_int(level), 5)
    base = _int(pool.get(str(r)) or pool.get(r)) or 6
    taken = snap.get("taken") or {}
    mine = 0
    for i in (ids or []):
        mine += _int(taken.get(str(i)) or 0)
    left_me = max(0, base - mine)
    left_tier = 0
    for other in ((RA._tier_counts() or {}).get(r) or []):
        left_tier += max(0, base - _int(taken.get(str(other)) or 0))
    if not left_tier or left_me <= 0:
        return {"p5": 0.0, "p_slot": 0.0, "coarse": True}
    p_slot = p_tier * (left_me / float(left_tier))
    return {"p5": 1 - (1 - p_slot) ** slots, "p_slot": p_slot, "coarse": False}


def _sim(act, snap, vals, tgt_odds, tgt_val, rng, keep, need=0.0):
    """一次 rollout: 执行这个动作 → 走到下回合开始。返回 (战力增益, 剩余金币)。

    need: "值得买"的价值门槛。通用刷牌(没填目标卡)时它不为 0 —— 否则模型会认为
          【每刷必买】, 出货率恒为 100%、刷新几次收益都一样, 这明显不符合实际手感。
    """
    coin = _int(snap.get("coin"))
    level = _int(snap.get("level")) or 1
    buy = max(1, snap.get("buy_cost") or 3)
    rf = max(1, snap.get("refresh_cost") or 1)
    gain = 0.0
    k = act.get("kind")
    if k == "buy":
        if coin - keep >= buy:
            coin -= buy
            gain += float(act.get("value") or 0.0)
    elif k == "level":
        c = _int(act.get("cost"))
        if c and coin - keep >= c:
            coin -= c
            level = min(6, level + 1)
    elif k in ("refresh", "level_refresh"):
        if k == "level_refresh":
            c = _int(act.get("cost"))
            if c and coin - keep >= c:
                coin -= c
                level = min(6, level + 1)
            else:
                return gain, coin                      # 升不起 → 这一轮白走
        for _ in range(_int(act.get("n") or 1)):
            if coin - keep < rf:
                break
            coin -= rf
            if tgt_odds:
                # 指定了目标卡: 【只有这一刷真出现它】才算买到, 没出现就是白刷一次 ——
                #   这才是"为了找某张卡而刷"的语义。否则会变成"顺手买张别的", 于是
                #   p5=0 的卡(比如 3 级刷 6 费的张梁)也算 100% 出货, 结论整个反过来。
                if rng.random() < float(tgt_odds.get("p5") or 0) and coin - keep >= buy:
                    coin -= buy
                    gain += tgt_val
                    break
                continue
            v = _sim_shop_best(level, snap, None, 0, vals, rng)
            # 通用刷牌三条: ① 好过门槛 ② 碰上的正好是你用得上的 ③ 比把钱留着划算
            if v > 0 and v >= need and rng.random() < WANT_RATE and coin - keep >= buy \
                    and v >= (buy * gold_value(snap.get("turn"), snap, vals)):
                coin -= buy
                gain += v
                break                                  # 买到就收手(保守: 不假设无限买)
    coin += income()
    return gain, coin


def _evaluate(act, snap, vals, tgt_odds, tgt_val, trials, keep, rng, need=0.0):
    """蒙特卡洛: trials 次 rollout, 取平均效用。"""
    gv = gold_value(snap.get("turn"), snap, vals)
    tot = 0.0
    hit = 0
    n = 0
    for _ in range(trials):
        g, left = _sim(act, snap, vals, tgt_odds, tgt_val, rng, keep, need)
        if g > 0:
            hit += 1
        tot += g + left * gv
        n += 1
    if not n:
        return None
    act["ev"] = tot / n
    act["hit_rate"] = hit / float(n)
    return act


def plan(targets="", keep_gold=0, trials=300, max_refresh=4, cfg=None):
    """给出"这回合该干什么"。返回 {ok, best, alts, snap, notes}。只读。

    targets   : 你在找的卡(名字或 ID, 逗号分隔); 留空 = 通用刷牌(按费用档期望价值)
    keep_gold : 保留金币(花完不得低于它, 例如留着下回合升级)
    trials    : 每个候选的蒙特卡洛次数(默认 300, 够稳且快)
    """
    snap = RA.snapshot()
    snap["_cfg"] = cfg or _load_cfg()
    if not snap.get("in_game"):
        return {"ok": False, "reason": "还没进对局(读不到你的金币/商店)", "snap": snap, "alts": []}
    if str(snap.get("phase") or "") != "recruit":
        return {"ok": False, "reason": "现在不是招募阶段(phase=%s), 招募阶段才需要决策"
                % (snap.get("phase") or "?"), "snap": snap, "alts": []}
    coin = _int(snap.get("coin"))
    if coin < 0:
        return {"ok": False, "reason": "金币还没推上来(coin=%s)" % coin, "snap": snap, "alts": []}

    vals = _fallback_tier_values(tier_values())
    tgt_odds = None
    tgt_val = 0.0
    tgt_nm = ""
    tids = _target_ids(targets)
    # 一个卡名往往对应【十几个赛季变体 ID】(实测『孙策』有 22 个), 其中只有一部分能被
    #   chess_cost_of 认出费用档 —— 只试第一个的话, 目标卡会白白算不出概率、静默降级成
    #   通用刷牌(用户填了目标却像没填一样)。所以挨个试, 取第一个算得出来的。
    byname = {}
    for cid, nm in tids:
        byname.setdefault(nm, []).append(cid)
    for nm, ids in byname.items():
        row = None
        try:
            from core.lineup_advisor import LineupAdvisor
            row = LineupAdvisor().card_info(nm)
        except Exception as e:
            sys.stderr.write("[经济决策器] 卡池查不到「%s」: %s\n" % (nm, e))
        o = _target_odds(ids, row, snap.get("level"), snap)
        if not o:
            continue
        tgt_odds, tgt_nm = o, nm
        tgt_val = _card_value(row) if row else vals.get(_int(_cost_of(ids[0])), 0.0)
        break
    # 没填目标卡时的"值得买"门槛: 得明显好过当前等级的平均水平。
    #   不然等于"刷到什么买什么", 出货率恒 100%、刷几次都一样 —— 不是真实手感。
    need = 0.0 if tgt_odds else _avg_tier_value(snap.get("level"), snap, vals) * 1.15

    # ---- 候选动作 ----
    cands = [{"kind": "hold", "name": "存钱(什么都不做)", "cost": 0}]
    up = _int(snap.get("level_up_cost"))
    lv = _int(snap.get("level")) or 1
    if up and lv < 6:
        cands.append({"kind": "level", "name": "升到 %d 级" % (lv + 1), "cost": up})
    for g in (snap.get("shop") or []):
        if not isinstance(g, dict):
            continue
        nm = str(g.get("name") or g.get("chessID") or "?")
        cid = g.get("chessID") or g.get("cid")
        row = None
        try:
            from core.lineup_advisor import LineupAdvisor
            row = LineupAdvisor().card_info(nm)
        except Exception:
            row = None
        v = _card_value(row) if row else _card_value(g)
        if v <= 0:
            continue
        cands.append({"kind": "buy", "name": "买 %s" % nm, "cost": snap.get("buy_cost") or 3,
                      "value": v})
    for n in range(1, max_refresh + 1):
        cands.append({"kind": "refresh", "name": "刷新 %d 次" % n, "cost": n * (snap.get("refresh_cost") or 1),
                      "n": n})
    if up and lv < 6:
        cands.append({"kind": "level_refresh", "name": "升到 %d 级再刷 1 次" % (lv + 1),
                      "cost": up + (snap.get("refresh_cost") or 1), "n": 1})

    # 买不起的候选别装作"花得起": 不标的话它账面上跟存钱一样(什么都没发生),
    #   却显示"花 12 金", 看起来像"升级不划算", 其实是【根本没升】。
    for c in cands:
        cost = _int(c.get("cost"))
        if c.get("kind") in ("level", "level_refresh", "buy") and cost and cost > coin - _int(keep_gold):
            c["afford"] = False
            c["short"] = cost - (coin - _int(keep_gold))
            c["name"] = "%s(还差 %d 金)" % (c.get("name"), c["short"])

    rng = random.Random(12345)     # 固定种子: 同一局面重复问, 结论不会来回跳
    keep = _int(keep_gold)
    evs = []
    for c in cands:
        r = _evaluate(c, snap, vals, tgt_odds, tgt_val, trials, keep, rng, need)
        if r is not None:
            evs.append(r)
    if not evs:
        return {"ok": False, "reason": "一个候选都算不出来", "snap": snap, "alts": []}
    evs.sort(key=lambda x: -x["ev"])
    best = evs[0]
    alts = evs[1:6]

    notes = []
    if tgt_odds:
        notes.append("目标卡「%s」: 这一刷看到它的概率约 %.0f%%(按你买/卖的记录估, 别人拿走的"
                     "算不进来 → 真实概率【略高于】这个数)" % (tgt_nm, 100.0 * float(tgt_odds.get("p5") or 0)))
    elif str(targets or "").strip():
        notes.append("目标卡「%s」这次没算出概率(名字没查到 / 不在当前等级卡池里) → 按通用刷牌算"
                     % str(targets).strip()[:24])
    notes.append("每回合收入只按基础 3 算(表里的真值; 没有利息机制, 别的加成也没算 → "
                 "存钱这一路的收益略被低估)")
    notes.append("金币折算: 现在 1 金 ≈ %.2f 战力(越到后期越不值钱)" % gold_value(snap.get("turn"), snap, vals))
    return {"ok": True, "best": best, "alts": alts, "snap": snap, "notes": notes,
            "target": str(targets or ""), "trials": trials}


def _cost_of(cid):
    try:
        from core import id_map as IDMAP
        return IDMAP.chess_cost_of(cid)
    except Exception:
        return 0


def _load_cfg():
    try:
        from core.config import load_config
        return load_config() or {}
    except Exception as e:
        sys.stderr.write("[经济决策器] 配置读不到(概率表降级): %s\n" % e)
        return {}


def format_report(d):
    """给 UI / 命令行用的一段人话。"""
    if not d.get("ok"):
        return "现在给不了建议: %s" % d.get("reason", "?")
    s = d.get("snap") or {}
    b = d.get("best") or {}
    lines = ["金币 %s · %d 级 · 第 %s 回合" % (s.get("coin"), _int(s.get("level")), s.get("turn")),
             "",
             "建议: %s" % b.get("name"),
             "  期望效用 %.1f(战力增益 + 剩余金币折算)" % float(b.get("ev") or 0),
             "  预计花费 %s 金" % b.get("cost")]
    if b.get("kind") in ("refresh", "level_refresh"):
        lines.append("  出货率 %.0f%%(rollout %d 次)" % (100.0 * float(b.get("hit_rate") or 0), d.get("trials") or 0))
    lines.append("")
    lines.append("其他选项(按期望效用排):")
    for a in (d.get("alts") or []):
        lines.append("  %-22s 效用 %.1f   花 %s 金" % (a.get("name"), float(a.get("ev") or 0), a.get("cost")))
    if d.get("notes"):
        lines.append("")
        for n in d.get("notes"):
            lines.append("· %s" % n)
    return "\n".join(lines)


if __name__ == "__main__":
    print(format_report(plan()))
