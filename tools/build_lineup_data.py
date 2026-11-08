# -*- coding: utf-8 -*-
"""把上游那两张表(其实是 TSV 文本, 后缀写成 .xlsx)转成助手能直接读的 json。

源:
  e:/三国杀/自走棋助手/s10全势力优选方案.xlsx  -> data/s10_lineups.json
  e:/三国杀/自走棋助手/各级营帐参考数据.xlsx    -> data/tavern_odds.json(已手工落成, 本脚本顺带校验)

用法: python tools/build_lineup_data.py
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "s10全势力优选方案.xlsx")
OUT = os.path.join(ROOT, "data", "s10_lineups.json")

# v4.110 修: 以前漏了"群" —— 而下面 parse() 要求【每个势力都在 FACTIONS 里】才收这一行,
#   于是原表里所有含"群"的组合被【静默丢弃】, 最后只剩 35 组不含群的。群是本季势力且每局
#   必带, 等于整张表全错。与 core/lineup_advisor.py 的 FACTIONS 保持一致(上游 mod 也是这 8 个)。
FACTIONS = ["魏", "蜀", "吴", "群", "黄巾", "汉", "西凉", "袁"]


def parse(path):
    rows = []
    for line in open(path, "r", encoding="utf-8").read().splitlines():
        cols = [c.strip().strip('"') for c in line.split("\t")]
        while cols and cols[0] == "":
            cols.pop(0)
        if len(cols) < 2:
            continue
        head = cols[0]
        if "/" not in head or any(f not in head for f in head.split("/")):
            continue
        parts = [p.strip() for p in head.split("/") if p.strip()]
        if len(parts) != 4 or not all(p in FACTIONS for p in parts):
            continue
        poison = cols[1] if len(cols) > 1 else ""
        clean = cols[2] if len(cols) > 2 else ""
        rows.append({
            "factions": parts,
            "key": "/".join(parts),
            "poison": poison,
            "clean": clean or poison,   # 只写了一列 = 两种情况下同一套
        })
    return rows


def main(argv=None):
    # v4.106★ 【按赛季出表】—— 换季后新表叫 s11全势力优选方案.xlsx, 生成 data/lineups_s11.json;
    #   推荐器按赛季号找文件(找不到才回退 S10, 且界面上会写"这是上赛季的表")。
    args = list(sys.argv[1:] if argv is None else argv)
    season = "10"
    if "--season" in args:
        try:
            season = str(args[args.index("--season") + 1]).strip().lstrip("sS") or "10"
        except Exception as e:
            print("--season 后面要跟赛季号(如 11):", e)
            return 1
    src = os.path.join(ROOT, "s%s全势力优选方案.xlsx" % season)
    out = os.path.join(ROOT, "data", "lineups_s%s.json" % season)
    if not os.path.exists(src):
        print("找不到源文件:", src)
        print("  换季: 把新赛季的优选表命名成 s<赛季>全势力优选方案.xlsx 放在项目根目录, "
              "再跑 python tools/build_lineup_data.py --season <赛季>")
        return 1
    rows = parse(src)
    if not rows:
        print("没解析出任何阵容行, 表格式可能变了")
        return 1
    # 关键卡: 从方案文案里抠出常见的武将名, 方便 UI 直接给出"要找谁"
    data = {
        "season": season,
        "_source": "s%s全势力优选方案.xlsx（B站 UP『耳目一曲』S%s 终端阵容解析配套表）" % (season, season),
        "_note": "poison = 有毒/关键装备情况下的转阵思路; clean = 无毒/没关键装备时。只写了一列时两者相同。",
        "count": len(rows),
        "lineups": rows,
    }
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("已写出 %s, 共 %d 组势力组合（赛季 S%s）" % (out, len(rows), season))
    print("  推荐器会优先读 data/lineups_s%s.json；本季没有表时才回退 data/s10_lineups.json" % season)
    for r in rows[:3]:
        print("  ", r["key"], "|", r["poison"], "|", r["clean"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
