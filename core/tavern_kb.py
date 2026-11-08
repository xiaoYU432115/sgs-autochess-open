# -*- coding: utf-8 -*-
"""自走棋知识库(S11) —— 棋子 / 主公 / 锦囊 / 机制标签 / 身材。

为什么要有这一层
================
项目里跟自走棋有关的数字散在好几个地方(csv_loader / lineup_advisor / refresh_advisor),
谁需要就去读一遍原始表, 各读各的 —— "这个棋子多少攻"这种最基础的问题, 在不同模块里可能
算出两个答案。这一层把它们收成【一份档案】, 上层决策(评估/变阵/针对)只认这里。

数据源(硬规矩: 一个数字都不手写)
================================
data/season/<赛季>/TavernChessConfig.json —— 官方 44 张表的合集, 由 tools/season_config.py
从客户端 Config.sgs 抓下来, 换季重抓即可。用到的表:
  ChessConf            3718  棋子主表: ID/名/费用档/攻/防/阵营/技能挂点
  ChessSkillConf       2301  技能: SkillID → SkillDesc(中文描述)/TriggerTyp(触发时机)/Buff
  ChessGeneralConf       51  主公: 血量/技能/难度/需要的随从类型
  ChessGeneralSkillConf  64  主公 ↔ 技能挂接
  ChessSpellConf        931  锦囊
  ChessSpellSkillConf   331  锦囊 ↔ 技能
  ChessBuffConf        4155  Buff 效果
  ChessBuffFollowUpConf  39  随征
  ChessClientKeywordConf 19  关键词(羁绊)的官方解释

★ 实测踩过的坑:
  ① 导出的 .csv 其实是【TSV】(tab 分隔), 用逗号读只剩一列 —— 所以这里一律读 JSON;
  ② ChessConf.ChessSkillDesc 【大量为空】, 真描述要拿 ChessSkill 里的 ID 去 ChessSkillConf 查;
  ③ 表里混着【展示用棋子】(攻防 9999 / 999) —— 不过滤的话排行榜全是它们。
"""

import os
import re
import json

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEASON_DIR = os.path.join(BASE_DIR, "data", "season")

_CACHE = {}


# ---------------------------------------------------------------- 机制标签
# 用户点名的几类: 盾 / 护甲 / 经济 / 毒 —— 它们的价值跟身材数字无关。只看 ATK/DEF
# 会把这些棋子全低估(典型: 1/1 但每回合产虎符的棋子, 身材垫底、实际是先手核心)。
# 所以按【技能描述】打标签, 评估时单独加权。匹配的是官方措辞。
MECH_RULES = [
    ("经济", r"虎符|金币|经济|利息|涨价|降价"),
    ("护甲", r"护甲|减伤|受到的伤害|免伤"),
    ("盾",   r"盾|护盾|抵挡|免疫"),
    ("毒",   r"毒|中毒|剧毒"),
    ("回手", r"回手|回到手牌|收回手牌"),
    ("随征", r"随征"),
    ("当先", r"当先"),
    ("亡语", r"死亡时|阵亡时|亡语"),
    ("连击", r"连击|额外攻击|再攻击"),
    ("成长", r"永久获得|永久提升|成长|每回合.*(?:加|\+)"),
    ("召唤", r"召唤|获得一张|加入.*营帐|放置"),
    ("群体", r"全体|所有友方|所有敌方|全部"),
    ("抽卡", r"抽牌|摸牌|获得.*手牌"),
    ("回复", r"回复|治疗|回血"),
    ("增益", r"获得.*(?:攻击|生命)|\+\d|提升.*属性"),
    ("削弱", r"降低|减少.*(?:攻击|生命)"),
    ("连携", r"连锁|连携|相邻"),
]

# 本赛季 8 大势力 —— 实测 MinionType 分组(对齐 lineup_advisor.FACTIONS):
#   1魏 2蜀 3吴 4群 5黄巾 6汉 7西凉 8袁
# ★ 踩过的坑: 早先写成 5=神 是错的 —— MinionType=5 实测是黄巾军(波才/程远志/邓茂…),
#   这个赛季没有"神"这个独立势力分组; 8 个势力是 魏蜀吴群黄巾汉西凉袁。
# ★ 还有: 阵营字段是【MinionType】, 不是 ChessType(后者只有 1/2 两种, 是别的分组, 同名卡两张)。
FACTION_CODE = {"1": "魏", "2": "蜀", "3": "吴", "4": "群", "5": "黄巾", "6": "汉", "7": "西凉", "8": "袁"}
FACTION_HINT = FACTION_CODE  # 兼容旧名

# 展示/特殊棋子: 攻防写成 9999 或 999 的是"表演用", 不是真能拿到的卡
_MAX_SANE_STAT = 500


def _cfg(season="s11"):
    key = ("cfg", season)
    if key in _CACHE:
        return _CACHE[key]
    j = {}
    p = os.path.join(SEASON_DIR, season, "TavernChessConfig.json")
    if os.path.isfile(p):
        try:
            with open(p, encoding="utf-8") as f:
                j = json.load(f) or {}
        except Exception:
            j = {}
    _CACHE[key] = j
    return j


def _rows(season, table):
    v = _cfg(season).get(table)
    return [r for r in (v or []) if isinstance(r, dict)]


# ---------------------------------------------------------------- 速查图鉴(第三方)
# 官方表有两大空白: ① ChessSkillDesc 大量为空; ② 就算有描述, 也只写"机制原文",
# 不写【这张卡该怎么用、跟谁搭】。这份图鉴补的就是这块。
#
# ★ 定死三条边界, 别越线:
#   1. 它是【人工整理】的参考文本, 不是官方数据 —— 不能拿来覆盖 atk/def/rank 这些数字
#      (它记的是基础版身材, 金卡/升级版会更高; 实测有 30 处跟官方表对不上, 就是这个原因);
#   2. 只按【名字】关联, 不按 ID —— 图鉴是人工按名字整理的;
#   3. 读不到就当没有, 绝不能因为图鉴缺失就让 load_chess 少一条棋子。
HANDBOOK_FILE = "chess_handbook.json"

# 异体字/形近字: 同一张卡官方表和图鉴写法不一样, 不映射会白白漏掉。
# ★ 方向是【官方表名 → 图鉴名】, 别写反 —— 查的时候拿的是官方表的 ChessName。
#   (实测漏掉的: 马云騄/马云禄、陈珪/陈圭、白绕/白饶 —— 都是騄禄、珪圭、绕饶这类)
HANDBOOK_ALIAS = {
    "马云騄": "马云禄",
    "陈珪": "陈圭",
    "白绕": "白饶",
}


def _load_handbook(season="s11"):
    """第三方速查图鉴 → {武将名: {faction, stats, skill, note, core, tiers, pairings}}

    读不到/文件不存在 → 返回 {} (上层拿到的 handbook 就是空串、is_core 就是 False, 无害)。
    """
    key = ("handbook", season)
    if key in _CACHE:
        return _CACHE[key]
    out = {}
    p = os.path.join(SEASON_DIR, str(season), HANDBOOK_FILE)
    if os.path.isfile(p):
        try:
            with open(p, encoding="utf-8") as f:
                j = json.load(f) or {}
            heroes = j.get("heroes") or {}
            if isinstance(heroes, dict):
                out = {str(k).strip(): v for k, v in heroes.items()
                       if isinstance(v, dict)}
        except Exception:
            out = {}
    _CACHE[key] = out
    return out


# 图鉴标了 core 的卡("体系核心卡")在决策里值多少加分。
# ★ 量级怎么定的: 对齐 battle_brain 里"对上目标势力 +12.0" —— 意思是
#   【一张体系核心卡, 值得你为它临时改一下方向】(比如玩魏却刷到曹轶, 该拿),
#   但【不该盖过整条主线】(不能因为是核心卡就放弃自己已经成型的蜀国志继)。
#   它比"命中一个想要的机制(+4.0)"高, 跟"对上目标势力(+12.0)"同档。
CORE_BONUS = 12.0


def handbook_of(name, season="s11"):
    """按名字查图鉴条目; 查不到返回 None(走一遍别名表)。"""
    nm = str(name or "").strip()
    if not nm:
        return None
    hb = _load_handbook(season)
    if nm in hb:
        return hb[nm]
    alias = HANDBOOK_ALIAS.get(nm)
    if alias and alias in hb:
        return hb[alias]
    return None


def handbook_lord(name, season="s11"):
    """图鉴对某个主公的【参考定位】→ {"faction","bonus","hp","desc"}，查不到返回 None。

    ★ 这里的 bonus【不许进决策分】: 实测跟官方表口径对不上 ——
      王昭君图鉴写"每招募3个-2", 官方是"每2个-3"; 张角图鉴写"+25%战力",
      官方 desc 里根本没有这个数字。只用来在界面上给一句人话参考。
    """
    nm = str(name or "").strip()
    if not nm:
        return None
    v = _handbook_meta(season).get("lords")
    if isinstance(v, dict) and nm in v and isinstance(v[nm], dict):
        return v[nm]
    return None


def handbook_guide(season="s11"):
    """图鉴里的【新手教程】→ [{"title","items"}] —— 讲的是"这游戏该怎么玩"。

    官方配置表只有数字和技能原文, 从来不写"前期先铺满格子""三回合掉到 30 血就卡二本"
    这种【打法】。自动对局要做出像人一样的决策, 缺的就是这一层。
    """
    return _handbook_meta(season).get("guide") or {"label": "", "sections": []}


def handbook_combos(season="s11"):
    """图鉴里【各本(1~6星)的打法建议】→ {"1": {"label","items"}, ...}

    用法: 升到 N 本之后, 查 combos[str(N)] 拿到"这一档该凑哪几张、体系怎么起"。
    """
    return _handbook_meta(season).get("combos") or {}


def _handbook_meta(season="s11"):
    """handbook 文件里除 heroes 之外的部分(主公/教程/打法), 带缓存。"""
    key = ("handbook_meta", season)
    if key in _CACHE:
        return _CACHE[key]
    out = {}
    p = os.path.join(SEASON_DIR, str(season), HANDBOOK_FILE)
    if os.path.isfile(p):
        try:
            with open(p, encoding="utf-8") as f:
                j = json.load(f) or {}
            out = {"lords": j.get("lords") or {},
                   "guide": j.get("guide") or {},
                   "combos": j.get("combos") or {}}
        except Exception:
            out = {}
    _CACHE[key] = out
    return out


def handbook_text(e):
    """图鉴条目 → 一句给人看的参考文本(技能白话 + 用法备注 + 搭配建议)。

    拼成纯字符串是为了好喂: battle_brain / codex 页面 / 日志都能直接吃, 不用各解析一遍。
    """
    if not e or not isinstance(e, dict):
        return ""
    parts = []
    if e.get("skill"):
        parts.append("技能：" + str(e["skill"]).strip())
    if e.get("note"):
        parts.append("用法：" + str(e["note"]).strip())
    ps = [str(x).strip() for x in (e.get("pairings") or []) if str(x).strip()]
    if ps:
        parts.append("搭配：" + "；".join(ps[:3]))
    return " ｜ ".join(parts)


def _int(v, default=0):
    try:
        s = str(v or "").strip()
        return int(float(s)) if s else default
    except Exception:
        return default


def _flag(v, default=True):
    """表里那些 0/1 开关 → bool。

    ★ 坑: 不能直接写 `r.get("IsShop") or "1"` —— 值为 0(int) 时 0 是 falsy,
      会【被默认值顶掉】, 变成 1, 于是 shop=0 的棋子全混进来(榜首全是它们)。
      这里显式判断, 0/false/空 才算关。
    """
    if v is None:
        return default
    s = str(v).strip()
    if s == "":
        return default
    return s not in ("0", "false", "False", "None", "null")


def mech_tags(text):
    t = str(text or "")
    if not t:
        return set()
    out = set()
    for name, pat in MECH_RULES:
        try:
            if re.search(pat, t):
                out.add(name)
        except Exception:
            continue
    return out


def _parse_ids(v):
    """'[2011011]' / '2011011' / '1,2' → ['2011011']"""
    s = str(v or "").strip().strip("[]")
    if not s:
        return []
    return [x.strip() for x in re.split(r"[,\s]+", s) if x.strip()]


# ---------------------------------------------------------------- 技能
def load_skills(season="s11"):
    """{skillID: {id,name,desc,trigger,buffs,tags}}"""
    key = ("skill", season)
    if key in _CACHE:
        return _CACHE[key]
    out = {}
    for r in _rows(season, "ChessSkillConf"):
        sid = str(r.get("SkillID") or "").strip()
        if not sid:
            continue
        desc = str(r.get("SkillDesc") or "")
        out[sid] = {
            "id": sid,
            "name": str(r.get("SkillName") or "").strip(),
            "desc": desc,
            "trigger": _int(r.get("TriggerTyp")),
            "buff": _parse_ids(r.get("SkillBuff1")),
            "tags": sorted(mech_tags(desc + " " + str(r.get("ChessBuffTips") or ""))),
        }
    _CACHE[key] = out
    return out


# ---------------------------------------------------------------- 棋子
def season_no(season="s11"):
    """'s11' → 11"""
    try:
        return int(str(season).lower().lstrip("s"))
    except Exception:
        return 0


def load_chess(season="s11", shop_only=True, drop_showpiece=True, cur_season_only=True):
    """棋子档案 → {chessID: {...}}

    ★ 这张表是【历赛季合表】: 同一个武将(如吕布)每个赛季都有一份记录, SeasonID 不同。
      不按 SeasonID 过滤的话, 会拿到 1~11 季的十几个"吕布", 还有赛季活动里那些
      50/50、8/16 的特殊棋子(它们 IsShop=0), 排行榜全是这些假数据。
    cur_season_only: 只留本赛季(SeasonID == 赛季号)的
    shop_only:       只留商店能刷到的(IsShop=1)
    drop_showpiece:  丢掉攻防 >= 500 的展示用棋子
    """
    key = ("chess", season, shop_only, drop_showpiece, cur_season_only)
    if key in _CACHE:
        return _CACHE[key]

    sk = load_skills(season)
    want_season = season_no(season)
    out = {}
    hb_all = _load_handbook(season)
    for r in _rows(season, "ChessConf"):
        cid = str(r.get("ChessID") or "").strip()
        if not cid:
            continue
        if not _flag(r.get("IsAvailable"), True):
            continue
        if cur_season_only and want_season and _int(r.get("SeasonID"), 0) != want_season:
            continue
        if shop_only and not _flag(r.get("IsShop"), True):
            continue
        if shop_only and not _flag(r.get("IsInRandomPool"), True):
            continue
        atk = _int(r.get("DefaultATK"))
        dfn = _int(r.get("DefaultDEF"))
        if drop_showpiece and (atk >= _MAX_SANE_STAT or dfn >= _MAX_SANE_STAT):
            continue

        # ★ 描述: ChessSkillDesc 经常是空的 —— 真描述在 ChessSkill 挂的技能上
        own = str(r.get("ChessSkillDesc") or "")
        sids = _parse_ids(r.get("ChessSkill"))
        linked = " ".join(str(sk.get(s, {}).get("desc") or "") for s in sids)
        full = (own + " " + linked).strip()
        tags = mech_tags(full)
        for s in sids:
            tags |= set(sk.get(s, {}).get("tags") or [])

        # 图鉴(第三方, 只补文本不补数字): 按名字关联, 查不到就是空 —— 绝不影响这条棋子本身
        name = str(r.get("ChessName") or "").strip()
        hb = handbook_of(name, season) if hb_all else None

        out[cid] = {
            "id": cid,
            "name": name,
            "rank": _int(r.get("ChessRank")),
            "atk": atk,
            "def": dfn,
            "faction": str(r.get("MinionType") or "").strip(),
            "faction_name": FACTION_CODE.get(str(r.get("MinionType") or "").strip(), ""),
            "skill_ids": sids,
            "skill_desc": own,          # 表上自带的(可能空)
            "desc": full,               # 补全后的完整描述
            "tags": sorted(tags),
            "is_shop": _flag(r.get("IsShop"), True),
            "in_pool": _flag(r.get("IsInRandomPool"), True),
            "upgrade_id": str(r.get("RelateUpgradeChessID") or "").strip(),
            "chess_score": _int(r.get("ChessScore")),
            "special": str(r.get("ChessSpecialType") or "").strip(),
            "season": str(r.get("SeasonID") or "").strip(),
            # 第三方图鉴: 参考文本 + 是否体系核心卡
            "handbook": handbook_text(hb),
            "is_core": bool(hb and hb.get("core")),
        }
    _CACHE[key] = out
    return out


# ---------------------------------------------------------------- 主公
def load_lords(season="s11"):
    """{generalID: {id,name,hp,skill_desc,tags,difficulty,feature,need}}"""
    key = ("lord", season)
    if key in _CACHE:
        return _CACHE[key]

    # ★ 主公技能的挂法容易搞错, 实测两种 ID 都叫 SkillID:
    #   ChessGeneralConf.GeneralSkill = 1063   —— 这是【技能效果挂点】, 拿它去 ChessSkillConf
    #                                            查只会查到"马休/张横"这些武将(1060311…), 张冠李戴;
    #   ChessGeneralSkillConf.SkillID = 1001     —— 这个【就是主公 ID】, 里面直接带着
    #                                            SkillName(崩坏) / SkillDesc / CostType。
    #   所以主公技能一律从 ChessGeneralSkillConf 按【主公 ID】取。
    gsk = {}
    for r in _rows(season, "ChessGeneralSkillConf"):
        gid = str(r.get("SkillID") or "").strip()
        if not gid:
            continue
        desc = str(r.get("SkillDesc") or "")
        gsk.setdefault(gid, []).append({
            "name": str(r.get("SkillName") or "").strip(),
            "desc": desc,
            "cost_type": _int(r.get("CostType")),
            "cost_para": _int(r.get("CostPara")),
            "gtype": _int(r.get("GeneralSkillType")),   # 1=主动 2=被动
            "tags": sorted(mech_tags(desc + " " + str(r.get("ChessBuffTips") or ""))),
        })

    out = {}
    for r in _rows(season, "ChessGeneralConf"):
        gid = str(r.get("GeneralID") or "").strip()
        if not gid:
            continue
        if not _flag(r.get("IsAvailable"), True):
            continue
        sks = gsk.get(gid) or []
        desc = " ".join(s["desc"] for s in sks).strip()
        tags = set()
        for s in sks:
            tags |= set(s["tags"] or [])
        out[gid] = {
            "id": gid,
            "name": str(r.get("GeneralName") or "").strip(),
            "hp": _int(r.get("HitPoint")),
            "skills": sks,
            "desc": desc,
            "tags": sorted(tags),
            "difficulty": _int(r.get("Difficulty")),
            "feature": _parse_ids(r.get("GenaralFeature")),
            "need_type": _int(r.get("RequiredMinionTyp")),
            "order": _int(r.get("GeneralOrder")),
        }
    _CACHE[key] = out
    return out


# ---------------------------------------------------------------- 锦囊
def load_spells(season="s11"):
    """{spellID: {id,name,rank,desc,tags}}"""
    key = ("spell", season)
    if key in _CACHE:
        return _CACHE[key]
    out = {}
    for r in _rows(season, "ChessSpellConf"):
        sid = str(r.get("SpellID") or "").strip()
        if not sid:
            continue
        if not _flag(r.get("IsAvailable"), True):
            continue
        desc = str(r.get("SpellSkillDesc") or r.get("SpellName") or "")
        out[sid] = {
            "id": sid,
            "name": str(r.get("SpellName") or "").strip(),
            "rank": _int(r.get("SpellRank")),
            "desc": desc,
            "tags": sorted(mech_tags(desc)),
        }
    _CACHE[key] = out
    return out


# ---------------------------------------------------------------- 关键词(羁绊)
def load_keywords(season="s11"):
    """官方关键词解释 → [{name,desc,type,color}]

    ★ 实测踩坑(2026-10-05): 这张表的字段就叫 `Keyword` / `KeywordDesc`,
      原代码猜的是 KeywordName/Name —— 一个都不存在 → nm 恒为空 →
      【19 条关键词被静默跳过, 函数永远返回空列表】。模块注释明明写着
      "ChessClientKeywordConf 19 关键词", 实际一条都拿不到, 属于读了等于没读。
      现在把两种写法都兜住(换季改名也不至于再空一次)。
    """
    key = ("kw", season)
    if key in _CACHE:
        return _CACHE[key]
    out = []
    for r in _rows(season, "ChessClientKeywordConf"):
        nm = str(r.get("Keyword") or r.get("KeywordName") or r.get("Name") or "").strip()
        ds = str(r.get("KeywordDesc") or r.get("Desc") or "").strip()
        if not nm:
            continue
        out.append({
            "name": nm,
            "desc": ds,
            "type": _int(r.get("ChessSkillType"), 0),
            "color": str(r.get("KeywordColor") or "").strip(),
        })
    _CACHE[key] = out
    return out


# ---------------------------------------------------------------- 打分
def body_score(c):
    return (c.get("atk") or 0) + (c.get("def") or 0)


def body_per_cost(c):
    return ((c.get("atk") or 0) + (c.get("def") or 0)) / float(max(1, int(c.get("rank") or 1)))


# 机制权重 —— 这些效果不写在身材里, 对胜负的影响往往比身材大
MECH_WEIGHT = {
    "经济": 6.0, "随征": 4.0, "成长": 3.5, "召唤": 3.0, "群体": 3.0,
    "护甲": 2.5, "盾": 2.5, "连击": 2.5, "连携": 2.0, "毒": 2.0,
    "当先": 2.0, "亡语": 2.0, "抽卡": 2.0, "增益": 2.0, "回复": 1.5, "削弱": 1.5,
    "回手": 2.5,
}


def mech_score(c):
    return sum(MECH_WEIGHT.get(t, 1.0) for t in (c.get("tags") or []))


def power_score(c):
    """综合强度 = 每费身材×3 + 机制分。第一版粗分, 后面用真实对局数据校准。"""
    return body_per_cost(c) * 3.0 + mech_score(c)


def top_chess(season="s11", n=15, rank=None, tag=None, dedup_name=True):
    it = list(load_chess(season).values())
    if rank:
        it = [c for c in it if c.get("rank") == int(rank)]
    if tag:
        it = [c for c in it if tag in (c.get("tags") or [])]
    it.sort(key=power_score, reverse=True)
    if dedup_name:
        seen, out = set(), []
        for c in it:
            nm = c.get("name") or ""
            if nm in seen:
                continue
            seen.add(nm)
            out.append(c)
        it = out
    return it[:n]


# ---------------------------------------------------------------- 选主公
# 主公技能里直接写了"X势力友方"的, 就依赖那个势力 —— 该势力被禁时这个主公要降权。
_FACTION_WORDS = {"魏": "1", "蜀": "2", "吴": "3", "群": "4", "黄巾": "5", "汉": "6", "西凉": "7", "袁": "8"}


def lord_factions(g):
    """主公依赖哪些势力(从技能描述里认字)。"""
    d = str(g.get("desc") or "")
    out = set()
    for w, code in _FACTION_WORDS.items():
        if (w + "势力") in d or (w + "势力友方") in d:
            out.add(code)
    return out


def recommend_lords(season="s11", banned=None, prefer_tags=None, topn=8):
    """选主公。

    banned:      被禁的势力(数字代码或 "魏"/"蜀" 这种字都行)
    prefer_tags: 想要的机制(如 ["经济"] —— 想打经济流就挑经济类主公)
    排序: 依赖被禁势力的直接压到最后; 其余按 标签匹配 + 血量 - 难度 排。
    """
    lords = load_lords(season)
    ban = set()
    for b in (banned or []):
        s = str(b).strip()
        ban.add(_FACTION_WORDS.get(s, s))
    want = set(prefer_tags or [])

    scored = []
    for g in lords.values():
        dep = lord_factions(g)
        hit_ban = bool(dep & ban)
        tag_hit = len(set(g.get("tags") or []) & want)
        s = tag_hit * 6.0 + g.get("hp", 0) * 0.25 - g.get("difficulty", 0) * 0.8
        if hit_ban:
            s -= 30.0
        scored.append((s, g, hit_ban, sorted(dep), tag_hit))

    scored.sort(key=lambda x: -x[0])
    out = []
    for s, g, hb, dep, th in scored[:topn]:
        why = []
        if hb:
            why.append("依赖被禁势力%s → 不推荐" % dep)
        if th:
            why.append("命中想要的机制%d条" % th)
        if not why:
            why.append("血量%d 难度%d" % (g.get("hp", 0), g.get("difficulty", 0)))
        out.append({"id": g["id"], "name": g["name"], "hp": g.get("hp"),
                    "difficulty": g.get("difficulty"), "score": round(s, 1),
                    "tags": g.get("tags"), "desc": g.get("desc") or "",
                    "why": "；".join(why)})
    return out


def stats(season="s11"):
    cs = load_chess(season)
    sp = load_spells(season)
    ld = load_lords(season)
    tag_cnt, no_desc = {}, 0
    for c in cs.values():
        if not c.get("desc"):
            no_desc += 1
        for t in (c.get("tags") or []):
            tag_cnt[t] = tag_cnt.get(t, 0) + 1
    return {
        "season": season, "chess": len(cs), "spells": len(sp), "lords": len(ld),
        "no_desc": no_desc,
        "tags": dict(sorted(tag_cnt.items(), key=lambda x: -x[1])),
    }


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    s = stats("s11")
    print("赛季:", s["season"])
    print("棋子:", s["chess"], " 主公:", s["lords"], " 锦囊:", s["spells"],
          " 无描述:", s["no_desc"])
    print("")
    print("机制标签分布(命中棋子数):")
    for k, v in s["tags"].items():
        print("   %-6s %d" % (k, v))
    print("")
    print("综合强度 Top12:")
    for c in top_chess("s11", 12):
        print("   %-7s %d费 %d/%d  %.1f  %-14s %s"
              % (c["name"], c["rank"], c["atk"], c["def"], power_score(c),
                 ",".join(c["tags"]) or "-", (c["desc"] or "")[:40]))
