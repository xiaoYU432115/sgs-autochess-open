# -*- coding: utf-8 -*-
"""v4.47★ 刷新决策器 —— "这一下该不该刷商店?"

为什么要有这个模块
==================
『剧本』里的 shopFind 是【无脑刷 N 次】(先设好次数, 到点了就刷), 『招募』是【看到就买】。
两者都不回答对局里最常问的那句: 现在手上这点金币, 到底该刷还是该存着升级?

判断依据全部来自已有数据, 没有新协议:
  · LIVE.me      —— 金币 / 商店等级 / 升级费 / 刷新费 / 买卡价 / 血量 / 回合
  · LIVE.shop    —— 当前 5 张 (用来判"目标就在脸上, 先买别刷")
  · LIVE.pool_taken —— 本局每个 chessID 你已买走几张(仅作"已见"参考, 不进概率)
  · config.json 的 star_odds(各等级各费用出现率) + _POOL_KINDS(各费用档种数, 见本文件常量)
    —— 概率按配置表【上界】口径算: 该档出现率 ÷ 该档种数(见 rough_odds 与 参考_配置表/发牌概率与卡池_s10.md)

结论是按【优先级】给的, 命中哪条就是哪条(前面几条都是"直接能看到的事实", 最后才是概率):
   读不到对局 / 现在不能刷 / 目标就在商店(先买) / 刷不起(金币不足) / 会跌破保留金币
   / 先升级再刷 / 可以刷 / 别刷

概率只当【参考】写在 reasons 里, 且明确标注"上界": 卡池 8 家共享、别人买走多少客户端拿不到
(见 core/pool_probe.py 取证), 所以它是"没人买过的池子"的上界, 实战只会更低 —— 绝不能
说成"池里还剩几张"。官方每季概率表会变, star_odds/_POOL_KINDS 是 config.json/本文件里可改的
本地表, 拿它当"精确答案"会误导人, 所以它是最后一条理由, 不是结论的唯一起点。

只读, 永不发包: 本模块不 import bridge/remote_ops, 也从不发包。
"""
import sys

# 结论里"值得刷"的最低粗估概率(低于这个数, 即使金币够也建议先攒/先升级)
ODDS_FLOOR = 0.08


def _live():
    from core.protocol import LIVE
    return LIVE


def _int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _taken_of(taken, cid):
    """LIVE.pool_taken 的键是 int(chessID), 但自检/别处可能传字符串键 —— 两种都认。"""
    if not taken:
        return 0
    for k in (str(cid), cid):
        if k in taken:
            return _int(taken.get(k))
    return 0


def _soft(fn, what, default):
    """查不动就退化成 default, 但【在 stderr 留一行】—— 静默吞掉的话, 用户看到的是
    "输入了卡名却没匹配上/目标里少了一张", 完全联想不到是查表这一步出的问题。"""
    try:
        return fn()
    except Exception as e:
        sys.stderr.write("[刷新决策器] %s 查不动(按空处理): %s: %s\n" % (what, type(e).__name__, e))
        return default


def _target_ids(target):
    """目标输入(名字/ID/逗号分隔) → ID 集合。空输入返回空集合(= 不指定目标)。"""
    from core import id_map as IDMAP
    t = str(target or "").strip()
    if not t:
        return set()
    out = set()
    for tok in [x for x in t.replace("，", ",").replace(";", ",").replace("；", ",").split(",") if x.strip()]:
        tok = tok.strip()
        if tok.isdigit():
            out.add(tok)
            # ID 可能是往季/升级链上的号, 一并带上它 1 星基础 ID
            base = _soft(lambda: IDMAP.chess_base_of(tok), "基础ID(%s)" % tok, tok)
            out.add(str(base))
        else:
            out.update(str(x) for x in
                       _soft(lambda: IDMAP.find_chess_ids_by_name(tok), "卡名(%s)" % tok, []))
    return {x for x in out if x}


def _tier_counts():
    """本季商店池按费用分组: {rank: [chessID, ...]}。

    用 tavern_chess.json(本季商店表)算"同费用段还有多少种卡" —— 概率式里的分母。
    读不到就返回空(概率那条理由会自动省略, 不给假数字)。
    """
    from core import id_map as IDMAP
    groups = {}
    try:
        pool = IDMAP.get_tavern_chess_data() or {}
    except Exception:
        return groups
    if not isinstance(pool, dict):
        return groups
    for cid in pool:
        r = IDMAP.chess_cost_of(cid)
        if r:
            groups.setdefault(int(r), []).append(str(cid))
    return groups


# v4.110★ 商店格数【随等级变】: 1级3格 / 2~3级4格 / 4~5级5格 / 6级6格
#   (来源: 参考_配置表/发牌概率与卡池_s10.md 第一节, 出自 ChessShopConf)。
#   rough_odds 里原来写死 5 格 —— 1 级实际只有 3 格, 会把"这一刷能看到"的概率算高;
#   6 级有 6 格, 又算低。
_SHOP_SLOTS = {1: 3, 2: 4, 3: 4, 4: 5, 5: 5, 6: 6}

# (P5 概率换地基) 本季商店池【每种费用档有多少种卡】—— 概率分母, 取自
#   参考_配置表/发牌概率与卡池_s10.md 第二节(权威配置表口径, 总数自检 184 种)。
#   与 _SHOP_SLOTS 同源、换季一起更新。
#   ⚠ 不用 _tier_counts(): 它走 tavern_chess.json, 现数 4 费为 43, 比配置表权威值 42 多 1 张
#   (总数 185 vs 184), 已漂移 —— 概率分母以配置表为准, 见 rough_odds() 的"上界口径"说明。
_POOL_KINDS = {1: 14, 2: 24, 3: 34, 4: 42, 5: 36, 6: 34}


def _sfx(cfg):
    """星/费用概率表的两个来源, 归一化成 (odds, pool_totals)。"""
    cfg = cfg or {}
    odds = cfg.get("star_odds") or {}
    pool = cfg.get("pool_totals") or {}
    return odds, pool


def _cfg_costs():
    """配置表基准价 (刷新费, 买卡费) —— ChessGlobalConf: ShopRefreshCost=1, ChessPrice=3。

    v4.68★ 为什么要有它: 协议里读不到时, 原来刷新费写死兜底 2、买卡费写死 0——
        · 刷新 2 是错的(实机基础 1): 没减费牌时它让人"少刷", 有丈八蛇矛时又正好等于 2;
        · 买卡 0 更糟: 直接让"这笔钱够不够买一张"这类判断消失。
    装备/主公会【改活体价】(丈八蛇矛 买3→2/刷1→2、刘焉各 -1), 所以这里只放表里的基准值,
    实时判断永远优先用 LIVE 推上来的活体值。
    """
    try:
        from core import csv_loader
        c = csv_loader.shop_costs() or {}
    except Exception as e:
        sys.stderr.write("[刷新决策器] 配置表花费读不到(用经验值): %s: %s\n" % (type(e).__name__, e))
        c = {}
    return _int(c.get("ShopRefreshCost")) or 1, _int(c.get("ShopBuyCost")) or 3


def rough_odds(cid, level, cfg=None, taken=None):
    """上界估算"下一次刷新在格子里看到这张卡"的概率(0~1)。数据不全返回 None。

    口径(配置表换地基, 见 参考_配置表/发牌概率与卡池_s10.md 第三节):
        单格 = 该费用档出现率 / 该档种数
        整刷 = 1 - (1 - 单格)^格数(格数随等级, 见 _SHOP_SLOTS)
    为什么分母用"种数"不用张数: 同档每种张数相同(ChessRankConf), "从池里摸"≈"均匀随机"。

    ⚠ 这是【上界】, 不是"池里还剩几张": 卡池 8 家共享, 别人买走多少客户端拿不到
      (发牌在服务器, 回包只给自己那几格 —— 见 core/pool_probe.py 取证)。所以这里按
      "没人买过的池子"算, 实战只会更低。也【绝不】拿 pool_taken 去减概率: 它只统计
      自己买走的, 减了只会得到一个"比上界假精确一点"的数, 正是要避免的误导。
      taken 仅用于附带报告"本局你已购入/已见几张"(可见的下界信号), 不进概率。
    """
    from core import id_map as IDMAP
    r = IDMAP.chess_cost_of(cid)
    if not r:
        return None
    r = int(r)
    odds, _pool = _sfx(cfg)
    slots = _SHOP_SLOTS.get(_int(level), 5)   # v4.110★ 格数随等级, 不再写死 5 格
    n_tier = _POOL_KINDS.get(r)               # 该档种数(配置表权威值, 不用会漂移的 _tier_counts)
    if not n_tier:
        return None
    row = odds.get(str(level)) or odds.get(level)
    try:
        p_tier = float(row[r - 1]) / 100.0 if row and r - 1 < len(row) else None
    except (TypeError, ValueError, IndexError):
        p_tier = None
    if p_tier is None:
        return None
    p_slot = p_tier / float(n_tier)
    seen = _taken_of(taken or {}, cid)
    return {"p_slot": p_slot, "p5": 1 - (1 - p_slot) ** slots,
            "tier_pct": p_tier * 100, "kinds": n_tier, "seen": seen,
            "upper_bound": True}


def snapshot():
    """把 LIVE 里跟"要不要刷新"有关的东西抄一份出来(短锁, 不长期持锁)。"""
    L = _live()
    with L.lock:
        me = dict(L.me or {})
        shop = [dict(g) for g in (L.shop or []) if isinstance(g, dict)]
        board = [c for c in (me.get("_board") or []) if isinstance(c, dict)]
        line = [c for c in (me.get("_lineUp") or []) if c]
        rf_cfg, buy_cfg = _cfg_costs()
        return {"in_game": bool(L.in_game or me), "nickname": me.get("nickname"),
                "coin": _int(me.get("coin"), -1), "hp": me.get("hp"),
                "hp_limit": me.get("hpLimit"), "level": _int(me.get("shopLevel")),
                # 活体价优先(装备/主公技能会改), 读不到才用配置表基准(刷新1/买卡3)。
                #   两个都标出来, 用户能一眼看出"现在这个价是不是被改过"。
                "refresh_cost": _int(me.get("shopRefreshCost")) or rf_cfg,
                "refresh_cost_cfg": rf_cfg,
                "buy_cost": _int(me.get("shopBuyCost")) or buy_cfg,
                "buy_cost_cfg": buy_cfg,
                "level_up_cost": _int(me.get("shopLevelUpCost")),
                "turn": _int(L.turn), "phase": str(L.phase or ""),
                "shop": shop, "board_n": len([c for c in board if not L.is_spell_card(c)]),
                "line_n": len(line), "others_n": len(L.others or []),
                "taken": dict(L.pool_taken or {})}


def advise(target="", keep_gold=0, want_level=0, cfg=None):
    """给出"该不该刷新"的结论。

    target    : 你在找的卡(名字或 ID, 逗号分隔可多个)。留空 = 只做通用建议。
    keep_gold : 保留金币(刷新后金币不得低于它, 例如留着下回合升级/买卡)。
    want_level: 计划升到几级(> 当前等级才有意义); 填了会额外算"先升级会不会好很多"。
    cfg       : 助手配置(取 star_odds / pool_totals); 不传就自己 load。

    返回 {"ok", "verdict", "reasons": [...], "odds": {...}, "snap": {...}, "targets": [...]}
    只读, 不发包, 不改任何状态。
    """
    if cfg is None:
        try:
            from core.config import load_config
            cfg = load_config()
        except Exception:
            cfg = {}
    snap = snapshot()
    out = {"ok": True, "verdict": "", "reasons": [], "odds": {}, "snap": snap, "targets": []}
    r = out["reasons"]

    if not snap["in_game"]:
        out["ok"] = False
        out["verdict"] = "读不到对局"
        r.append("LIVE 里还没有对局数据 —— 先进入一局自走棋(并在游戏页确认助手已连上)。")
        return out

    coin = snap["coin"]
    lv = snap["level"]
    rcost = snap["refresh_cost"]
    keep_gold = max(0, _int(keep_gold))
    want_level = _int(want_level)

    r.append("当前: %s  金币 %d  商店Lv%d  血量 %s/%s  第%d回合(%s)"
             % (snap.get("nickname") or "?", coin, lv, snap.get("hp"), snap.get("hp_limit"),
                snap["turn"], snap["phase"] or "-"))
    # v4.68★ 费用这行要把【活体价】和【配置表基准】都摆出来: 用户问过
    #   "买武将 3 虎符、刷新 1 虎符, 拿了丈八蛇矛会变成 2/2, 刘焉和主公技能还会再减 1" ——
    #   当活体价 != 基准价时, 必须让他一眼看出"现在的价是被牌改过的", 否则他会以为助手算错了。
    _bcost = _int(snap.get("buy_cost"))
    _bcfg = _int(snap.get("buy_cost_cfg")) or 3
    _rfcfg = _int(snap.get("refresh_cost_cfg")) or 1
    r.append("费用: 刷新 %d 金%s / 升级还差 %d 金 / 买卡 %d 金%s"
             % (rcost,
                ("(基准 %d, 已被装备/主公技能改)" % _rfcfg) if rcost != _rfcfg else "",
                max(0, snap["level_up_cost"] - coin),
                _bcost or _bcfg,
                ("(基准 %d, 已被装备/主公技能改)" % _bcfg) if _bcost and _bcost != _bcfg else ""))

    # ---- 目标卡: 在不在商店里 / 池子还剩多少 / 粗估概率 ----
    want = _target_ids(target)
    out["targets"] = sorted(want)
    on_shop = []
    if want:
        from core import id_map as IDMAP
        for g in snap["shop"]:
            cid = str(g.get("chessID") or "")
            base = str(IDMAP.chess_base_of(cid)) if cid else ""
            if cid in want or base in want:
                on_shop.append((g.get("goodsID"), cid,
                                IDMAP.name_of(cid, chess_hint=True) or cid))
        if on_shop:
            names = ", ".join("%s(#%s)" % (n, g) for g, _c, n in on_shop[:3])
            r.append("★ 目标就在商店里: %s —— 直接买, 刷掉就没了。" % names)
        else:
            r.append("目标不在当前 5 张里。")

    # 概率: 只对"第一个目标"给(多目标时逐个列会把结论淹掉), 且只有拿得到概率表才算
    if want:
        from core import id_map as IDMAP
        tid = sorted(want, key=lambda x: (IDMAP.chess_cost_of(x) or 9))[0]
        nm = IDMAP.name_of(tid, chess_hint=True) or tid
        od = rough_odds(tid, lv, cfg, snap["taken"])
        if od:
            out["odds"] = {"cid": tid, "name": nm, "level": lv,
                           "p5": od["p5"], "p_slot": od["p_slot"],
                           "tier_pct": od["tier_pct"], "kinds": od["kinds"],
                           "seen": od["seen"], "upper_bound": True}
            r.append("%s 下一刷见到它 ≈ %.1f%%(上界: 配置表口径=该档出现率÷该档种数, "
                     "按'没人买过的池子'算, 实战只会更低; Lv%d 该档 %.1f%% / 该档 %d 种)%s"
                     % (nm, od["p5"] * 100, lv, od["tier_pct"], od["kinds"],
                        ("; 本局你已购入/已见 %d 张" % od["seen"]) if od["seen"] else ""))
            if want_level and want_level > lv:
                od2 = rough_odds(tid, want_level, cfg, snap["taken"])
                if od2:
                    out["odds"]["p5_next_level"] = od2["p5"]
                    r.append("升到 Lv%d 后同一张的上界 ≈ %.1f%% (该档出现率 %.1f%%)"
                             % (want_level, od2["p5"] * 100, od2["tier_pct"]))

    # ---- 结论: 从上到下命中哪条算哪条 ----
    if snap["phase"] and snap["phase"] != "recruit":
        out["verdict"] = "现在不能刷"
        r.append("阶段是『%s』不是招募阶段 —— 商店刷新只在招募阶段有意义。" % snap["phase"])
        return out
    if on_shop:
        out["verdict"] = "先买, 别刷"
        return out
    if coin < 0:
        out["verdict"] = "读不到金币"
        r.append("LIVE 里没读到 coin(可能刚进对局还没刷新) —— 等下次状态更新再看。")
        return out
    if coin < rcost:
        out["verdict"] = "刷不了(金币不足)"
        r.append("金币 %d < 刷新费 %d, 这一下刷不动。" % (coin, rcost))
        return out
    if coin - rcost < keep_gold:
        out["verdict"] = "别刷(会跌破保留金币)"
        r.append("刷完只剩 %d 金, 低于你设的保留线 %d。" % (coin - rcost, keep_gold))
        return out

    times = (coin - keep_gold) // rcost if rcost else 0
    r.append("按保留 %d 金算, 这一回合最多能刷 %d 次。" % (keep_gold, times))
    # v4.68★ 顺手把"这笔钱按买卡价还能买几张"算出来 —— 用户问的就是这个问题
    #   (买武将 3 虎符)。刷新和买卡花的是同一笔钱, 只报刷新次数等于只答了一半。
    _bcost2 = _int(snap.get("buy_cost")) or _int(snap.get("buy_cost_cfg")) or 3
    if _bcost2 > 0:
        _nb = (coin - keep_gold) // _bcost2
        _nr = times
        r.append("同一笔钱: 买卡价 %d 金 → 最多再买 %d 张; 刷新价 %d 金 → 最多刷 %d 次"
                 " (两者花的是同一笔钱, 别把两边的数都当成'都能做')" % (_bcost2, _nb, rcost, _nr))
        out["buy_afford"] = {"buy_cost": _bcost2, "can_buy": _nb,
                             "refresh_cost": rcost, "can_refresh": _nr}

    # 先升级再刷: 目标是 3 费以上且在下一级明显更容易出时, 攒着升级比现在硬刷划算
    if want and want_level and want_level > lv:
        p_now = (out["odds"] or {}).get("p5")
        p_next = (out["odds"] or {}).get("p5_next_level")
        gap = max(0, snap["level_up_cost"] - coin)
        if p_now is not None and p_next is not None and p_next > p_now * 1.5 and gap > 0:
            out["verdict"] = "先升级再刷(攒 %d 金)" % gap
            r.append("升级后见到它的概率从 %.1f%% 涨到 %.1f%%, 而现在离升级还差 %d 金 —— "
                     "把这波金币花在升级上更划算。" % (p_now * 100, p_next * 100, gap))
            return out

    p5 = (out["odds"] or {}).get("p5")
    if p5 is not None and want:
        if p5 < ODDS_FLOOR and times <= 1:
            out["verdict"] = "别刷(概率太低且刷不动几次)"
            r.append("粗估只有 %.1f%%, 而这一回合最多刷 %d 次 —— 大概率是白花 %d 金; "
                     "建议先把金币用在升级/买卡上。" % (p5 * 100, times, rcost))
            return out
        if p5 > 0:
            r.append("平均约每 %.1f 次刷新能见到一次(粗估)。" % (1.0 / p5))
    out["verdict"] = "可以刷"
    if not want:
        r.append("没指定目标卡 —— 只按金币/费用给的通用结论: 金币够刷 %d 次, 且不会跌破保留线。" % times)
    return out


def format_report(d):
    """把 advise() 的结果排成给人看的文本(UI 那个只读文本框直接用)。"""
    d = d if isinstance(d, dict) else {}
    lines = ["结论: %s" % (d.get("verdict") or "?"), ""]
    for i, x in enumerate(d.get("reasons") or [], 1):
        lines.append("  %d) %s" % (i, x))
    od = d.get("odds") or {}
    if od:
        lines.append("")
        lines.append("概率明细(上界: 配置表口径 = 该档出现率 ÷ 该档种数; 卡池 8 家共享, 实战只会更低):")
        lines.append("  卡: %s(%s)  Lv%d  该档出现率 %.1f%%  该档 %d 种  "
                     "单格 %.2f%%  整刷 ≈ %.1f%%"
                     % (od.get("name"), od.get("cid"), od.get("level"), od.get("tier_pct") or 0,
                        od.get("kinds") or 0,
                        (od.get("p_slot") or 0) * 100, (od.get("p5") or 0) * 100))
        if od.get("seen"):
            lines.append("  本局你已购入/已见 %d 张(仅你可见部分; 别人买走的看不到)" % od["seen"])
        if od.get("p5_next_level") is not None:
            lines.append("  升到目标等级后整刷 ≈ %.1f%%" % (od["p5_next_level"] * 100))
    return "\n".join(lines)
