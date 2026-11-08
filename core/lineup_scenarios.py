# -*- coding: utf-8 -*-
"""v4.105★ 把上游那张【四势力 → 后期思路】的表直接变成【能跑的剧本】。

表里写的是人话("多田尚衣 + 张郃/许褚"、"蜀董绾"、"多董承 + 程昱/何进"), 看完了还得
自己一步步点。这里干的事: 从那句人话里抠出【核心卡名】→ 查本地卡池换成 ID →
拼成一条标准剧本(shopfind 找卡 → deploy 一张张上阵), 存进 config.json 的 scenarios,
到『剧本』页就能直接选它跑。

为什么只做"找卡 + 上阵"两步
==========================
阵容表给的是【后期该转什么】, 不是【这一回合该点哪一下】—— 站位(7/7/三选一)、
锦囊、升人口这些得看当时局势。所以剧本只负责最机械、最容易漏的那一段:
把核心卡刷出来买到手并摆上场; 站位和锦囊留给人(日志最后一步会提示)。

用法
====
  python -m core.lineup_scenarios            # 列出能从表里认出的核心卡(自检用)
  python -m core.lineup_scenarios 魏 蜀 吴 袁  # 生成这一组势力的剧本(json 打到 stdout)
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")

# 上游表里出现过的卡名(只认这些, 免得把"蜀黄巾"里的"蜀"当成卡)
KNOWN_CARDS = [
    "田尚衣", "张郃", "许褚", "韩猛", "董绾", "董承", "何进", "程昱", "刘辩", "刘禅",
    "马超", "胡车儿", "张宝", "张宁", "杨弘", "袁绍", "沮授", "南华", "吕布", "李肃",
    "诸葛亮", "淳于琼", "陈珪", "祢衡", "赵忠", "李广", "杨玉环", "皇甫嵩", "马伶俐",
    "刘焉", "蔡文姬", "张飞", "张苞", "袁术", "张角", "张梁", "高览", "董绾",
]

# 主力卡买几张(没在表里的按 2 张)
CORE_COUNT = {"田尚衣": 3, "董承": 3, "马超": 3, "张宝": 3, "董绾": 2, "程昱": 2}


def _current_season():
    """助手现在认的赛季号(与 csv_loader / lineup_advisor 同一份 season.json)。"""
    try:
        with open(os.path.join(DATA_DIR, "season", "season.json"), "r", encoding="utf-8") as f:
            return str((json.load(f) or {}).get("season") or "").strip().lstrip("sS") or ""
    except Exception:
        return ""


def _load_pool():
    """本季卡池(赛季感知): 有本季表用本季表, 没有才回退 s10 —— 别再写死 s10。

    v4.160★ 修(实测): 以前写死 tavern_chess_s10.json, 换季后剧本按上个赛季的
    身材/势力/ID 去解卡名, 拿 S10 的 210… 前缀去对 S11 的 211… 卡, 整段剧本解不出。
    """
    season = _current_season()
    want = "tavern_chess_s%s.json" % season if season else ""
    for nm in ([want] if want else []) + ["tavern_chess_s10.json"]:
        try:
            with open(os.path.join(DATA_DIR, nm), "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and d:
                if nm != "tavern_chess_s10.json":
                    print("[阵容剧本] 卡池来源 %s (%d 条)" % (nm, len(d)), file=sys.stderr)
                return d
        except Exception as e:
            print("[阵容剧本] 读 %s 失败: %s" % (nm, e), file=sys.stderr)
    return {}


POOL = _load_pool()


def ids_of(name):
    """卡名 → 它在卡池里的所有 ID(普通/金/升级版都算, 免得只认出一半)。"""
    out = []
    for cid, v in POOL.items():
        if not isinstance(v, dict):
            continue
        nm = v.get("name") or v.get("n") or ""
        if nm == name or (len(name) >= 2 and name in nm):
            out.append(str(cid))
    return out


def extract_cores(text):
    """从"多田尚衣 + 张郃/许褚"这类文案里抠出核心卡名(按表里出现顺序)。"""
    s = str(text or "")
    out = []
    for n in KNOWN_CARDS:
        if n in s and n not in out:
            out.append(n)
    # "多田尚衣" 这种: 前缀"多"表示主力, 排到最前
    out.sort(key=lambda n: (0 if ("多" + n) in s else 1, s.index(n)))
    # 表里唯一一组"只讲处境不点名"的(魏/黄巾/西凉/袁 —— 最穷的禁势力):
    #   它的原文是"若经济倒转则南华养吕布程昱…如果对手袁喷则养李肃", 认不出就按这句兜底
    if not out and ("南华" in s or "吕布" in s or "李肃" in s):
        out = ["南华", "吕布", "程昱"]
    return out


def build_scenario(title, cores, note="", refresh_count=24, keep_gold=8):
    """核心卡名列表 → 一条剧本(dict, 与 config.json 里 scenarios 的元素同构)。"""
    cores = cores or []
    if not cores:
        return None
    pairs = [(n, CORE_COUNT.get(n, 2)) for n in cores]
    target = ",".join("%s*%d" % (n, c) for n, c in pairs)
    all_ids = []
    for n, _c in pairs:
        for i in ids_of(n):
            if i not in all_ids:
                all_ids.append(i)

    steps = [
        {"op": "log", "text": "【%s】%s" % (title, note)},
        {"op": "wait", "ms": 500},
        {"op": "shopfind", "target": target, "targetIds": all_ids,
         "buyCount": 3, "refreshCount": refresh_count, "keepGold": keep_gold},
        {"op": "wait", "ms": 400},
    ]
    for n, _c in pairs:
        steps.append({"op": "deploy", "target": n, "targetIds": ids_of(n)})
        steps.append({"op": "wait", "ms": 300})
    steps.append({"op": "log", "text": "核心卡已上场 —— 站位/锦囊/升人口按局势手动收尾"})
    return {
        "id": "lu_%d" % int(time.time() * 1000),
        "name": title,
        "hotkey": "",
        "triggerMode": "press",
        "runCount": 1,
        "speed": 1.0,
        "maxDuration": 0,
        "onStart": "", "onEnd": "",
        "enabled": False,
        "steps": steps,
    }


def build_plan_payload(factions):
    """给脚本端『按推荐转阵』(lineupPlan)步骤用的下发数据 —— 脚本端不内置任何阵容表。

    结构: {name, factions, poison:{target, ids, cores, coresIds, note}, clean:{同左}}
    """
    from core.lineup_advisor import LineupAdvisor
    res = LineupAdvisor().recommend(factions)
    if not res["exact"]:
        return None

    def _side(text):
        cores = extract_cores(text)
        if not cores:
            return None
        ids = []
        for n in cores:
            for i in ids_of(n):
                if i not in ids:
                    ids.append(i)
        return {
            "target": ",".join("%s*%d" % (n, CORE_COUNT.get(n, 2)) for n in cores),
            "ids": ids,
            "cores": cores,
            "coresIds": dict((n, ids_of(n)) for n in cores),
            "note": text,
        }

    return {
        "name": "/".join(res["exact"]["factions"]),
        "factions": res["exact"]["factions"],
        "poison": _side(res["poison"]),
        "clean": _side(res["clean"]),
    }


def build_for_factions(factions, poison=True):
    """按本局四个势力查表 → 一条剧本。势不匹配或表里没写 → None。"""
    from core.lineup_advisor import LineupAdvisor
    res = LineupAdvisor().recommend(factions)
    ex = res["exact"]
    if not ex:
        return None
    plan = (res["poison"] if poison else res["clean"]) or res["clean"] or res["poison"] or ""
    cores = extract_cores(plan)
    if not cores:
        return None
    name = "%s %s" % ("/".join(ex["factions"]),
                      ("毒/装备局" if poison else "无毒品"))
    note = "原表: " + plan
    if ex.get("_qun_fallback"):
        # 照实说清楚: 这不是原表直接写的那一组, 是从"另外几个势力"推出来的, 群没算进去
        rest = [str(x).strip() for x in (factions or []) if str(x).strip() != "群"]
        note = ("⚠ 这套是【推出来的, 不是原表直接写的】\n"
                "  原表(B站那份)35 组里【一行含『群』的都没有】, 所以它按你勾的另外几个势力\n"
                "  (%s) 找了最贴近的一组「%s」给你。\n"
                "  → 群里有什么核心卡【没算进这套方案】。群是本季卡最多的势力(60 张: 南华老仙/\n"
                "    贾诩/蔡文姬/祢衡/左慈…), 你自己看情况往里加。\n"
                "\n原表: %s" % ("/".join(rest), "/".join(ex["factions"]), plan))
    return build_scenario(name, cores, note=note)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a]
    if len(args) == 4:
        sc = build_for_factions(args, poison=True)
        print(json.dumps(sc, ensure_ascii=False, indent=2) if sc else "{}")
    else:
        from core.lineup_advisor import LineupAdvisor
        rows = LineupAdvisor().lineups
        n = 0
        for r in rows:
            cs = extract_cores(r.get("poison") or "")
            if cs:
                n += 1
                print("%-14s → %s" % (r.get("key", ""), " / ".join(cs)))
        print("共 %d/%d 组能从表里认出核心卡" % (n, len(rows)))
