# -*- coding: utf-8 -*-
"""生成 data/tavern_chess_s<季>.json —— 各决策模块共用的"本季卡池"表。

为什么要有它
============
core/lineup_advisor.card_pool() 找的是 data/tavern_chess_s<季>.json；S11 一直缺这张表，
于是它【静默回退】到 tavern_chess_s10.json —— 后果是 econ_planner.tier_values() 拿
S10 的身材算"这回合该不该刷/升", 刷新顾问、阵容推荐、锦囊图鉴跟着一起用错赛季的数据。
界面上没有任何提示, 属于"看着在干活、其实算的是上赛季"。

修法是【补表】而不是【改回退逻辑】:
  · 补出来 → 所有下游(lineup_advisor / econ_planner / lineup_scenarios / codex_server /
    build_card_pinyin)不用改一行就吃到本季数据;
  · 只改回退逻辑 → 每个模块各改一遍, 迟早漏一处。

数据源: data/season/s<季>/TavernChessConfig.json 的 ChessConf / ChessSkillConf
        (官方表, 由 tools/season_config.py 抓下来)。一个数字都不手写。

字段口径跟既有 tavern_chess_s10.json 完全对齐(下游按这套字段名在读):
  n/name 武将名   atk 攻   def 血   rank/cost 费用档   faction 势力中文
  skill 技能ID数组   season 赛季号   in_shop 商店能刷到(1/0)
  effect 技能描述   upgrade_id 升级后的 ChessID   type 固定"武将"

用法:
  python tools/build_tavern_pool.py              # 生成当前赛季(data/season/season.json)
  python tools/build_tavern_pool.py s11 s10      # 生成指定赛季(可多个)
"""
import os
import sys
import json

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
SEASON_DIR = os.path.join(DATA_DIR, "season")

FACTION_CODE = {"1": "魏", "2": "蜀", "3": "吴", "4": "群",
                "5": "黄巾", "6": "汉", "7": "西凉", "8": "袁"}


def _int(v, d=0):
    try:
        s = str(v or "").strip()
        return int(float(s)) if s else d
    except Exception:
        return d


def _flag(v, default=True):
    """0/1 开关 → bool。★ 不能写 `v or "1"`: 值为 0 时会被默认值顶掉。"""
    if v is None:
        return default
    s = str(v).strip()
    if s == "":
        return default
    return s not in ("0", "false", "False", "None", "null")


def _ids(v):
    s = str(v or "").strip().strip("[]")
    if not s:
        return []
    out = []
    for x in s.replace(",", " ").split():
        x = x.strip()
        if x:
            out.append(_int(x))
    return out


def current_season():
    p = os.path.join(SEASON_DIR, "season.json")
    try:
        with open(p, encoding="utf-8") as f:
            return str((json.load(f) or {}).get("season") or "").strip().lstrip("sS")
    except Exception:
        return ""


def build(season_no, verbose=True):
    """season_no: '11' 这种纯数字字符串(与 s10 表的 season 字段口径一致)。"""
    src = os.path.join(SEASON_DIR, "s%s" % season_no, "TavernChessConfig.json")
    if not os.path.isfile(src):
        raise SystemExit("[卡池表] 找不到官方赛季表: %s" % src)
    with open(src, encoding="utf-8") as f:
        cfg = json.load(f) or {}

    # 技能ID → 描述: ChessConf.ChessSkillDesc 大面积为空, 真描述在 ChessSkillConf
    skill_desc = {}
    for r in (cfg.get("ChessSkillConf") or []):
        if not isinstance(r, dict):
            continue
        sid = str(r.get("SkillID") or "").strip()
        if sid:
            skill_desc[sid] = str(r.get("SkillDesc") or "").strip()

    out = {}
    want = int(season_no)
    for r in (cfg.get("ChessConf") or []):
        if not isinstance(r, dict):
            continue
        cid = str(r.get("ChessID") or "").strip()
        if not cid or not cid.isdigit():
            continue
        if not _flag(r.get("IsAvailable"), True):
            continue
        if _int(r.get("SeasonID"), 0) != want:
            continue

        name = str(r.get("ChessName") or "").strip()
        if not name:
            continue

        sids = _ids(r.get("ChessSkill"))
        eff = str(r.get("ChessSkillDesc") or "").strip()
        if not eff:
            eff = " ".join(skill_desc.get(str(s), "") for s in sids).strip()
        rank = _int(r.get("ChessRank"), 1) or 1
        in_shop = 1 if (_flag(r.get("IsShop"), True) and _flag(r.get("IsInRandomPool"), True)) else 0

        out[cid] = {
            "n": name,
            "name": name,
            "atk": _int(r.get("DefaultATK")),
            "def": _int(r.get("DefaultDEF")),
            "faction": FACTION_CODE.get(str(r.get("MinionType") or "").strip(), ""),
            "rank": rank,
            "cost": rank,
            "skill": sids,
            "season": want,
            "in_shop": in_shop,
            "effect": eff,
            "upgrade_id": _int(r.get("RelateUpgradeChessID")),
            "type": "武将",
        }

    if not out:
        raise SystemExit("[卡池表] 赛季 %s 解析出 0 条 —— 赛季号对不对? (%s)" % (season_no, src))

    dst = os.path.join(DATA_DIR, "tavern_chess_s%s.json" % season_no)
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    if verbose:
        shop = sum(1 for v in out.values() if v["in_shop"])
        print("[卡池表] %s → %d 条(商店可刷 %d 条)  来源: %s"
              % (os.path.basename(dst), len(out), shop, os.path.relpath(src, BASE_DIR)))
    return dst


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    args = [a.strip().lstrip("sS") for a in sys.argv[1:] if a.strip()]
    # ★ 只认纯数字的赛季号: 早先没校验, 把 `--help` 当赛季号去 build, 顺带把
    #   tavern_chess_s10.json 也重写了一遍(372 → 371 条)。参数随手传不能变成改数据。
    bad = [a for a in args if not a.isdigit()]
    if bad:
        raise SystemExit("[卡池表] 赛季号只认数字(11 / s11), 收到: %s" % " ".join(bad))
    if not args:
        cur = current_season()
        if not cur or not cur.isdigit():
            raise SystemExit("[卡池表] 读不到当前赛季号(data/season/season.json), 请手动指定: python tools/build_tavern_pool.py s11")
        args = [cur]
    for a in args:
        build(a)
