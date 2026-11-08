# -*- coding: utf-8 -*-
"""v4.164★ 生成【装备表】 data/equip_table.json —— 剧本"按装备判定 / 选装备按类型筛"的地基。

缘起(用户原话): "能识别装备吗…如果是玉玺就不用天降神兵锦囊了…如果是五六剑, 不选武器、
  选其他的"。要答这些, 必须先有一张 equipmentID → {名字, 类型} 的表。

★ 数据来源与依据(实测, 不是猜):
  赛季配置 data/season/s11/TavernChessConfig.json 里的 Weapon 表, 每条形如
    { "WeaponID": 4113007, "WeaponName": "玉玺", "WeaponType": 2,
      "WeaponSelectRound": 11, "SeasonID": 11, ... }
  · equipmentID 就是这里的 WeaponID —— 桥接 _scEquipPicks() 拿 equipmentID 去查
    TavernChessConfiger.GetEquipByEquipID(eid), 查的就是这张表。
  · 装备大类在 WeaponType 字段, 当前赛季确认三档(见下 TYPE_CN):
      1=武器(13 件, 回合 7, 含 吴六剑 4112011)
      2=宝具(11 件, 回合 11, 含 玉玺 4113007)
      3=坐骑(12 件, 回合 3, 赤兔/的卢/绝影…)
    ⚠ 三档是【数出来】的(全赛季 126/121/124), 不是照抄旧注释; 老资料里"只有武器"的说法是错的。

★ 为什么离线生成而不是运行时问游戏:
  ① 装备 ID 是【配置表固定号】, 赛季配置里查得全, 不必等真机;
  ② 但运行时仍以游戏自己的 GetEquipByEquipID 为准(名字), 这张表主要补【类型】——
     游戏那个对象里类型字段名没个准, 而配置表里 WeaponType 是实锤。

用法: python tools/build_equip_table.py        (只写当前赛季)
"""
import io
import json
import os
import sys
import collections

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEASON_DIR = os.path.join(BASE, "data", "season")
OUT_PATH = os.path.join(BASE, "data", "equip_table.json")

# WeaponType → 中文类别。★ 依据见文件头: 按 S11 实际条目反推(类型3 全是马 = 坐骑,
#   类型2 含玉玺/锦盒/七星灯 = 宝具, 类型1 含吴六剑/古锭刀 = 武器)。
TYPE_CN = {1: "武器", 2: "宝具", 3: "坐骑"}


def latest_season():
    """取 data/season/ 下最大的赛季号(如 s11 → 11)。"""
    best = None
    try:
        for nm in os.listdir(SEASON_DIR):
            if not nm.startswith("s"):
                continue
            try:
                n = int(nm[1:])
            except ValueError:
                continue
            if os.path.exists(os.path.join(SEASON_DIR, nm, "TavernChessConfig.json")):
                if best is None or n > best:
                    best = n
    except OSError:
        pass
    return best


def collect(cfg, season):
    """递归找出所有带 WeaponID 的条目, 只留指定赛季。"""
    rows = []

    def walk(o):
        if isinstance(o, dict):
            if "WeaponID" in o:
                rows.append(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(cfg)
    return [r for r in rows if r.get("SeasonID") == season]


def main():
    season = latest_season()
    if not season:
        print("✗ 没找到任何赛季配置(data/season/s*/TavernChessConfig.json)")
        return 1
    cfg_path = os.path.join(SEASON_DIR, "s%d" % season, "TavernChessConfig.json")
    cfg = json.loads(io.open(cfg_path, encoding="utf-8", errors="replace").read())
    rows = collect(cfg, season)
    if not rows:
        print("✗ 赛季 %d 的配置里没有装备(Weapon)条目" % season)
        return 1

    items = {}
    for r in rows:
        wid = r.get("WeaponID")
        if not wid:
            continue
        items[str(wid)] = {
            "n": str(r.get("WeaponName") or ""),
            "t": r.get("WeaponType"),
            "r": r.get("WeaponSelectRound"),   # 第几回合会弹出来挑
        }

    out = {
        "season": season,
        "src": "data/season/s%d/TavernChessConfig.json" % season,
        "types": {str(k): v for k, v in TYPE_CN.items()},
        "items": items,
    }
    io.open(OUT_PATH, "w", encoding="utf-8", newline="").write(
        json.dumps(out, ensure_ascii=False, indent=1))

    cnt = collections.Counter(v["t"] for v in items.values())
    print("✓ 已写 %s" % OUT_PATH)
    print("  赛季=%d  装备 %d 件" % (season, len(items)))
    for t in sorted(cnt, key=lambda x: (x is None, x)):
        print("    类型 %s(%s): %d 件" % (t, TYPE_CN.get(t, "?"), cnt[t]))
    # 用户规则要用到的两条, 直接点名确认
    for want in ("玉玺", "吴六剑"):
        hit = [(k, v) for k, v in items.items() if v["n"] == want]
        if hit:
            k, v = hit[0]
            print("    ★ %s → ID=%s 类型=%s(%s) 回合=%s"
                  % (want, k, v["t"], TYPE_CN.get(v["t"], "?"), v["r"]))
        else:
            print("    ⚠ 本季没找到 %s" % want)
    return 0


if __name__ == "__main__":
    sys.exit(main())
