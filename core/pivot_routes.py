# -*- coding: utf-8 -*-
"""变阵【路线】库 —— "从谁转到谁", 有名有姓的那一层。

为什么单独开这一层
==================
core/battle_brain.should_pivot 回答的是【势力级】的问题("转去吴"), 但打牌的人真正
想知道的是【棋子级】的: "我手上这张陆逊要不要换成魏延?"。

势力级答案是【算出来】的(打分, 官方表可推); 棋子级答案是【套路/经验】, 它不在官方
TavernChessConfig 上, 只能来自攻略沉淀。所以两个来源必须分开:
  · 官方表 → battle_brain(能算的);
  · 攻略   → 本模块(算不出来、只能抄的)。
把这两件事混在一起, 就会出现"明明数据说该转, 界面却说不清换成谁"。

数据来源
========
dev/_devfile_data/DEFAULT_DATA.json —— 第三方速查页(devfile.vip)的导出数据:
  · transforms: 20 条 from → to 变阵路线(陆逊↔魏延、马休→马伶俐/夏侯渊 …);
  · lineups:   16 套阵容(黄巾随征流、二本双雄、吴国移除流 …), 带主公/核心卡/文字步骤。

它是【攻略】不是官方表, 所以这里的规矩是:
  · 只读, 不写回任何配置, 不参与任何"必须成立"的判定;
  · 读不到 / 格式变了 → 返回空列表, 变阵逻辑退回"只给势力级答案"。
    缺它只会让建议少一句"谁换谁", 不会让判断变错。
  · 项目规矩: 兜底不静默 —— 吞掉的异常记一笔(见 core/silence.py)。
"""
import json
import os
import re
import sys

from core import silence as SILENCE

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA_PATH = os.path.join(_ROOT, "dev", "_devfile_data", "DEFAULT_DATA.json")

_CACHE = {"loaded": False, "data": None}


def _load():
    """读那份导出数据(只读一次, 之后走缓存)。读不到返回 {} —— 调用方按"没有路线"处理。"""
    if _CACHE["loaded"]:
        return _CACHE["data"] or {}
    _CACHE["loaded"] = True
    try:
        with open(_DATA_PATH, encoding="utf-8") as f:
            _CACHE["data"] = json.load(f) or {}
    except Exception as e:                                   # 缺文件/坏 JSON: 允许, 记账
        _CACHE["data"] = {}
        try:
            SILENCE.note("core/pivot_routes.py:_load", "读 %s 失败: %s" % (_DATA_PATH, e))
        except Exception as _e2:
            # 记账本身失败了也不能静默(项目预算: except: pass 不许增长) —— 落到 stderr,
            # 至少能在控制台看到, 而不是"读不到数据且一点痕迹都没有"。
            print("[pivot_routes] 记账失败: %s" % _e2, file=sys.stderr)
    return _CACHE["data"] or {}


def _split_names(s):
    """把攻略里的"名字串"拆成卡名列表。

    要处理的三种写法(都是那份数据里真实出现的):
      · "袁术 / 鲁肃"            → 并列, 任一张在场就算对上
      · "裴元绍（遗计）"          → 括号是注释, 不是名字的一部分
      · "裴元绍 → 马元义 → 白饶"  → 链条, 拆开后是"下一步可以换成谁"
    """
    if not s:
        return []
    out = []
    for part in re.split(r"[/、,，]|→|->|=>", str(s)):
        p = re.sub(r"[（(].*?[)）]", "", part)      # 去括号注释
        p = p.strip().strip("·：: ").strip()
        # 太长的多半是说明文字("按阵容方向选择"这种), 不是卡名 —— 卡名不会超过 6 个字
        if p and len(p) <= 6:
            out.append(p)
    return out


def transform_routes():
    """20 条变阵路线(原始结构, 名字/起点/终点/说明/步骤文字)。"""
    try:
        return list((_load().get("transforms") or {}).get("groups") or [])
    except Exception as e:
        try:
            SILENCE.note("core/pivot_routes.py:transform_routes", str(e))
        except Exception as _e2:
            # 记账本身失败了也不能静默(项目预算: except: pass 不许增长) —— 落到 stderr,
            # 至少能在控制台看到, 而不是"读不到数据且一点痕迹都没有"。
            print("[pivot_routes] 记账失败: %s" % _e2, file=sys.stderr)
        return []


def find_routes(held_names, limit=3):
    """手上有这些卡 → 能走哪些变阵路线。

    held_names: 卡名列表(手牌 + 阵上都算, 调用方给全)。
    匹配口径: 路线的【起点】(from)里任何一张在你手上, 这条路线就用得上 ——
      起点是"你现在该有的那张", 终点是"它可以换成谁 / 它启动了哪条链"。
    返回按"对上的张数"排序(对上多的排前面), 最多 limit 条。
    """
    held = set()
    for n in (held_names or []):
        n = str(n or "").strip()
        if n:
            held.add(n)
    if not held:
        return []
    out = []
    for g in transform_routes():
        if not isinstance(g, dict):
            continue
        frms = _split_names(g.get("from"))
        hit = [f for f in frms if f in held]
        if not hit:
            continue
        out.append({
            "name": str(g.get("name") or ""),
            "from": frms,
            "to": _split_names(g.get("to")),
            "toText": str(g.get("to") or ""),
            "desc": str(g.get("desc") or ""),
            "items": [str(x) for x in (g.get("items") or [])],
            "hit": hit,                      # 你手上哪几张对上了这条路线
        })
    out.sort(key=lambda r: (-len(r["hit"]), r["name"]))
    return out[:max(1, int(limit))]


def route_hint(held_names, limit=2):
    """给界面/日志用的一句话: "手上有陆逊 → 可走『陆逊 ↔ 魏延』(二本双雄…)"。

    没有匹配就返回 "" —— 调用方直接拼在说明后面即可, 不必判空。
    """
    rs = find_routes(held_names, limit=limit)
    if not rs:
        return ""
    parts = []
    for r in rs:
        why = r["desc"] or (r["toText"] or "")
        parts.append("手上有%s → 可走『%s』(%s)" % ("/".join(r["hit"][:2]), r["name"], why))
    return "；".join(parts)


def lineups():
    """16 套阵容(名称/主公/核心卡/文字步骤) —— 给"阵容库"展示与一键填目标用。"""
    try:
        return list((_load().get("lineups") or {}).get("groups") or [])
    except Exception as e:
        try:
            SILENCE.note("core/pivot_routes.py:lineups", str(e))
        except Exception as _e2:
            # 记账本身失败了也不能静默(项目预算: except: pass 不许增长) —— 落到 stderr,
            # 至少能在控制台看到, 而不是"读不到数据且一点痕迹都没有"。
            print("[pivot_routes] 记账失败: %s" % _e2, file=sys.stderr)
        return []


def lineup_cores(name=None):
    """取一套阵容的【核心卡】名单, 用于一键填进剧本目标 / 白名单。

    name 为空时返回全部 16 套的 {名称: [核心卡]}。
    核心卡字段是 "陶升 · 严政 · 刘辟" 这种点分串, 这里拆成列表。
    """
    out = {}
    for g in lineups():
        if not isinstance(g, dict):
            continue
        nm = str(g.get("name") or "")
        core = g.get("core")
        if isinstance(core, list):
            names = [str(x).strip() for x in core if str(x or "").strip()]
        else:
            names = [x.strip() for x in re.split(r"[·、,，]", str(core or "")) if x.strip()]
        out[nm] = names
    if name:
        return out.get(name) or []
    return out


def lineup_steps(name):
    """一套阵容的【文字步骤】(攻略原话), 用于界面展示 —— 是给人看的, 不是可执行剧本。"""
    for g in lineups():
        if str(g.get("name") or "") == name:
            return [str(x) for x in (g.get("items") or [])]
    return []
