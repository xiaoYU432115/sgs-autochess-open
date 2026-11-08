# -*- coding: utf-8 -*-
"""S11『昭君出塞』赛季增强 —— 把官方赛季机制建模进决策层。

S11 核心：『志继』死亡联动（同势力棋子阵亡触发后续效果，蜀国大重做）+ 新主公王昭君(出塞)。
本模块只做【纯函数 + 真实数据】，可被 auto_battle / CompositionPlanner 调用，也能单测。
所有数字/技能文本来自官方公告与 data/season/s11/TavernChessConfig.json，不手写。

接入点：
  · lord_pick_advice  → 替代/增强 auto_battle._handle_choose 的主公推荐（尊重禁用势力 + 主公效果建模）
  · faction_zhiji_bonus → 喂给 CompositionPlanner._faction_power（志继联动强的势力加分）
  · pick_start_faction  → 开局没禁地时，按棋子厚度 + 志继潜力推荐优先经营的势力
"""
import re
from core import tavern_kb as KB

# S11 主公技能【效果建模】（来自官方公告，权威）。desc 仍由知识库解析，这里补"可计算"的结构。
LORD_S11_EFFECTS = {
    "王昭君": {
        "skill": "出塞", "type": "active", "cost": 0,
        # 每局限1次：选一个势力，每招募2个该势力武将 → 营帐升级费 -3。
        # 含义：强烈鼓励"单势力到底"的运营；被禁势力不能选。
        "effect": {"kind": "faction_commit_discount", "per": 2, "discount": 3},
    },
}

ZHIJI_RE = re.compile(r"志继\s*(\d+)")


def _effect_applies(eff, L):
    """LORD_S11_EFFECTS 里建模的效果, 只有在【本季官方技能文本里真能看到】时才生效。

    ★ 为什么必须校验: 官方公告写的技能跟游戏实装不一致是常事, 而决策影响的是实装。
      实测 S11 王昭君: 公告/旧建模写"出塞"(选一个势力, 每招募2个该势力 → 升级费-3),
      但官方 ChessGeneralSkillConf 里她挂的技能是【吞邪】(友方每攻击3次, 吞噬目标并
      获得其属性) —— 根本没有出塞, 也不需要选势力。
      不校验的话会凭空给她 +8 分, 推荐理由还写着"出塞:鼓励单势力" —— 理由是假的,
      用户照着选就会发现"说好的升级费打折呢"。
    """
    kind = (eff.get("effect") or {}).get("kind")
    d = str(L.get("desc") or "") + " " + " ".join(
        str(s.get("name") or "") for s in (L.get("skills") or []))
    if kind == "faction_commit_discount":
        return ("出塞" in d) or ("招募" in d and ("升级费" in d or "升级费用" in d))
    return True


# 主公技能里的【负面】效果 —— 有些主公血厚/卡池厚, 但技能本身是个代价。
#   典型: 董卓"崩坏"= 战斗失败时额外失去等同营帐等级的体力。他 50 血最厚,
#   以前靠 hp 加分排到第二, 实际是"血厚是为了承受这个代价", 属于陷阱型主公。
#   不扣的话会推荐一个其实很容易暴死的。
_LORD_PENALTY = [
    ("输局额外掉血", re.compile(r"失败时[^。；]*?失去[^。；]*?(?:体力|生命)"), -8.0),
]


def zhiji_layers(desc):
    """技能文本里"志继N"的层数 N（取最大），没有返回 0。"""
    ms = ZHIJI_RE.findall(desc or "")
    return max((int(x) for x in ms), default=0) if ms else 0


def has_zhiji_trigger(desc):
    """技能是否【获得/触发】志继（提供层数），而非仅作为阈值条件。"""
    t = desc or ""
    return ("获得志继" in t) or ("触发志继" in t) or ("志继时" in t) or ("志继效果" in t)


def eval_zhiji_board(pieces):
    """评估一盘棋子的『志继』联动强度。

    pieces: tavern_kb.load_chess 的 value 列表（dict）。
    返回 {score, zhiji_pieces, zhiji_layers, best_faction, best_faction_n}
    """
    trig = [c for c in pieces if has_zhiji_trigger(c.get("desc", ""))]
    layers = sum(zhiji_layers(c.get("desc", "")) for c in pieces)
    fac = {}
    for c in pieces:
        f = c.get("faction_name") or "?"
        fac[f] = fac.get(f, 0) + 1
    best_faction, best_n = (max(fac.items(), key=lambda kv: kv[1]) if fac else ("?", 0))
    # 联动分：触发棋子越多、且越集中同势力 → 阵亡时整体收益越高（蜀是 S11 主舞台）
    score = layers * 2.0 + len(trig) * 1.5 + (best_n if best_faction == "蜀" else best_n * 0.5)
    return {
        "score": round(score, 2), "zhiji_pieces": len(trig), "zhiji_layers": layers,
        "best_faction": best_faction, "best_faction_n": best_n,
    }


def faction_zhiji_bonus(season="s11", banned=None):
    """每个势力"获得/触发志继"的棋子数量 → 联动加分（喂给阵营强度评估）。

    S11 里阵亡联动是胜负主轴：某个势力触发棋子越密集，越值得走单势力到底。
    """
    chess = KB.load_chess(season)
    banned = set(str(b) for b in (banned or []))
    fac = {}
    for c in chess.values():
        f = c.get("faction_name")
        if not f or f in banned:
            continue
        if has_zhiji_trigger(c.get("desc", "")):
            fac[f] = fac.get(f, 0) + 1
    # 封顶 12 个，避免某势力棋子多就无限加分；每枚触发棋子约 +1.2
    return {f: round(min(n, 12) * 1.2, 2) for f, n in fac.items()}


def _norm_banned(banned_factions):
    """禁用势力归一化成【中文名集合】—— 输入可能是 "魏" 也可能是 "2" 这种数字代码。

    为什么要归一化: UI 勾的是中文, 而 KB.lord_factions 返回的是数字代码,
    两边不统一就会出现"明明禁了魏, 魏系主公还在推荐第一位"这种看着像算错的事。
    """
    out = set()
    for b in (banned_factions or []):
        s = str(b or "").strip()
        if not s:
            continue
        if s in KB.FACTION_CODE:                      # 数字代码 → 中文
            out.add(KB.FACTION_CODE[s])
        elif s in set(KB.FACTION_CODE.values()):      # 已经是中文
            out.add(s)
    return out


def _lord_hp(L, default=40):
    """主公血量(官方 HitPoint)。拿不到/不是数字 → 回默认, 【不能当成 0】。

    为什么: hp 进分是 (hp-40)*0.6, 0 血会被算成 -24 分 = "极脆", 那是凭空造的数据
    (表上只是没填), 会无端把一批主公压到底。
    """
    try:
        v = int(L.get("hp") or 0)
    except (TypeError, ValueError):
        return default
    return v if v > 0 else default


def _lord_dep_factions(L):
    """主公依赖哪些势力(中文名集合) —— 两条路都认, 以前只认了一条。

    ① 官方字段 RequiredMinionTyp(need_type): 表上明写的依赖;
    ② KB.lord_factions: 从技能描述里认"X势力友方"这种字。
    ★ 以前只用 ① 去排除、只用 ② 去扣分(description 里扫字面), 两边各漏一半。
    """
    dep = set()
    nt = L.get("need_type")
    if nt:
        nm = KB.FACTION_CODE.get(str(nt))
        if nm:
            dep.add(nm)
    for code in KB.lord_factions(L) or ():
        nm = KB.FACTION_CODE.get(str(code))
        if nm:
            dep.add(nm)
    return dep


def lord_pick_advice(season="s11", banned_factions=None, prefer_tags=None, top=5):
    """S11 选主公建议（尊重禁用势力 + 主公效果建模）。

    返回 [{id,name,score,hp,why,dep,pool,core,tip}] 按分降序。

    ★ 每一项加分的依据都写在这里, 改数值前先读一遍:
      · 基线 50
      · 血量 hp: (hp-40)*0.6 —— 图鉴教程原话"血量是硬通货: 第十回合还能维持20血以上
        就有前四希望"。官方 hp 实测 35~60, 这一项落在 -3 ~ +12。
        (v4.141 修: 以前 hp 只塞进返回值, 完全不进分 —— 等于没考虑。)
      · 机制标签: MECH_WEIGHT × 0.5(沿用)
      · prefer_tags 命中: 每个 +5.0 —— (v4.141 修: 这个参数以前是死的,
        界面上勾了"想打经济"结果推荐里一点没变。)
      · 依赖势力的【可用卡池】: min(张数,25) × 0.4(最多+10) —— 禁完之后还能拿到几张
      · 依赖势力的【核心卡数】: min(张数,8) × 1.0(最多+8) —— 速查图鉴标的体系核心卡。
        没有核心卡的势力凑不出体系, 光有数量没用(一堆散卡打不过成体系的)。
      · 不依赖任何势力: +len(banned) × 1.5 —— 禁得越多, 通用主公越稳(它不会被禁废)。
        这条是"根据禁用判断选谁"的另一半: 不只排除被禁的, 还要在禁得多时偏通用。
      · 王昭君出塞: +8(沿用, 官方建模 per=2/discount=3)
      · 依赖势力全被禁: 直接排除(技能核心没了, 留着只是干扰)
      · 依赖势力部分被禁: -30
    """
    banned = _norm_banned(banned_factions)
    lords = KB.load_lords(season)
    chess = KB.load_chess(season)

    # 禁完之后, 每个势力还剩多少张卡 / 其中多少张是体系核心卡
    fac_count, fac_core = {}, {}
    for c in chess.values():
        f = c.get("faction_name")
        if not f or f in banned:
            continue
        fac_count[f] = fac_count.get(f, 0) + 1
        if c.get("is_core"):
            fac_core[f] = fac_core.get(f, 0) + 1

    want = set(str(t) for t in (prefer_tags or []) if str(t).strip())
    recs = []
    for gid, L in lords.items():
        name = L.get("name") or ""
        dep = _lord_dep_factions(L)
        alive = dep - banned

        # 依赖的势力全被禁 → 这个主公的技能核心没了, 直接排除
        if dep and not alive:
            continue

        score = 50.0
        why = []

        hp = _lord_hp(L)
        if hp != 40:
            score += (hp - 40) * 0.6
            why.append("血%d" % hp)

        for t in (L.get("tags") or []):
            if t in KB.MECH_WEIGHT:
                score += KB.MECH_WEIGHT[t] * 0.5
                why.append("机制:" + t)

        hit = sorted(want & set(L.get("tags") or []))
        if hit:
            score += 5.0 * len(hit)
            why.append("想要:%s" % "/".join(hit))

        pool = sum(fac_count.get(f, 0) for f in alive)
        core = sum(fac_core.get(f, 0) for f in alive)
        if dep:
            if pool:
                score += min(pool, 25) * 0.4
            if core:
                score += min(core, 8) * 1.0
            why.append("%s可用%d张(核心%d)" % ("/".join(sorted(alive)), pool, core))
        else:
            # 不绑势力: 禁得越多它越稳
            if banned:
                score += len(banned) * 1.5
                why.append("不绑势力,禁%d个也不受影响" % len(banned))

        if dep and len(alive) < len(dep):
            dead = sorted(dep - alive)
            score -= 30.0
            why.append("依赖被禁%s" % "/".join(dead))

        eff = LORD_S11_EFFECTS.get(name)
        if eff and _effect_applies(eff, L):
            if eff["effect"]["kind"] == "faction_commit_discount":
                best_fac, best_n = (max(fac_count.items(), key=lambda kv: kv[1])
                                    if fac_count else ("?", 0))
                score += 8.0
                why.append("出塞:鼓励单势力(%s可用%d个)" % (best_fac, best_n))

        # 负面技能: 血厚/卡池厚但技能是代价的, 扣回来
        d_all = str(L.get("desc") or "")
        for label, pat, pen in _LORD_PENALTY:
            if pat.search(d_all):
                score += pen
                why.append("%s%s" % (label, int(pen)))

        # 图鉴参考定位: 只给人看, 【不进分】(口径跟官方对不上, 见 KB.handbook_lord 注释)
        tip = ""
        hb = KB.handbook_lord(name, season)
        if hb and hb.get("desc"):
            tip = str(hb["desc"])

        recs.append({
            "id": gid, "name": name, "score": round(score, 1), "hp": hp,
            "why": "；".join(why) or "常规",
            "dep": sorted(dep), "pool": pool, "core": core, "tip": tip,
        })
    recs.sort(key=lambda r: (-r["score"], -r["hp"]))
    return recs[:top]


def pick_start_faction(season="s11", banned_factions=None, top=8):
    """开局没禁地时，按势力棋子厚度 + 志继联动潜力，推荐优先经营的势力。"""
    banned = set(str(b) for b in (banned_factions or []))
    chess = KB.load_chess(season)
    fac = {}
    for c in chess.values():
        f = c.get("faction_name")
        if not f or f in banned:
            continue
        fac.setdefault(f, []).append(c)
    out = []
    for f, items in fac.items():
        n = len(items)
        zj = sum(1 for c in items if has_zhiji_trigger(c.get("desc", "")))
        body = sum((c.get("atk", 0) + c.get("def", 0)) for c in items) / float(max(1, n))
        score = n * 1.0 + zj * 3.0 + body * 0.3
        out.append({"faction": f, "count": n, "zhiji": zj, "body": round(body, 1),
                    "score": round(score, 1)})
    out.sort(key=lambda r: r["score"], reverse=True)
    return out[:top]


def _self_test():
    import json
    print("== S11 选主公建议(无禁地) ==")
    for r in lord_pick_advice(top=5):
        print("  %s 评分%.1f hp%s 理由:%s" % (r["name"], r["score"], r["hp"], r["why"]))
    print("== S11 选主公建议(禁蜀/禁吴) ==")
    for r in lord_pick_advice(banned_factions=["蜀", "吴"], top=3):
        print("  %s 评分%.1f 理由:%s" % (r["name"], r["score"], r["why"]))
    print("== 开局势力推荐 ==")
    for f in pick_start_faction()[:8]:
        print("  %s 棋子%d 志继%d 均身材%.1f 分%.1f" % (f["faction"], f["count"], f["zhiji"], f["body"], f["score"]))
    print("== 志继阵营加分 ==")
    print("  " + json.dumps(faction_zhiji_bonus(), ensure_ascii=False))
    # 一只虚构蜀国志继板：验证 eval_zhiji_board
    fake = [
        {"desc": "友方获得志继时额外获得1层", "faction_name": "蜀"},
        {"desc": "志继1：全体友方永久+1/+1", "faction_name": "蜀"},
        {"desc": "攻击时，友方获得1层志继", "faction_name": "蜀"},
        {"desc": "普通攻击", "faction_name": "吴"},
    ]
    print("== 虚构板志继评估 ==")
    print("  " + json.dumps(eval_zhiji_board(fake), ensure_ascii=False))


if __name__ == "__main__":
    _self_test()
