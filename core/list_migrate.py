# -*- coding: utf-8 -*-
"""v4.113★ 【名单 ID 跨季迁移】—— 换季时把招募/随征/保留/遣散名单里的旧 ID 换成本季 ID。

为什么必须迁(用户原话: "这个名不改但是里面的ID变了啊")
========================================================
官方把【赛季号编进了棋子 ID】: 同一张『曹休』S10 = 21001011、S11 = 21101011 —— 名字
一字不改, ID 整套换。而 data/lists/*.json 里存的是【ID】(不是名字), 于是换季后这些
名单全部指向【局内根本不存在】的卡:
    auto_buy        招募目标  → 找不到这张卡 → 不买
    multi_buy       多选购买  → 同上
    permanent_keep  永久保留  → 匹配不上 → 可能反被遣散(最阴的一种)
    discard_target  遣散目标  → 匹配不上 → 该遣的不遣
    followup        随征      → 随不上
表现就是"换季之后名单像全失灵了", 而且【不报错】—— 因为那些 ID 是合法的历史 ID,
只是这一季的卡池里没有。

迁移规则(两条, ①是精确的、②才是兜底)
====================================
① 【换掉赛季位】ID 第 2~3 位就是赛季号(S10 = 210 / S11 = 211), 后面 5 位是这张卡在
   本季内的编号 —— 实测 S10 → S11 的 58 个旧 ID 全部命中且名字一致(例: 21008081 陈珪
   → 21108081 陈珪)。所以 new = 本季前缀 + old[3:], 命中且【名字一致】才采用。
   这条能保住"同一张卡的不同变体"(曹休有 21101011 / 21101012 两张, 按名字找会串)。
② 【按名字补】①落空时才用: 拿 names[i] 或旧 ID 反查出来的名字, 交给
   id_map.find_ids_by_name()(已本季优先, 见 season_first) 取本季第一个 ID。
两条都落空 → 列进 unmapped, 【原样保留】: 猜错的代价比留着一条旧 ID 大得多。

用法
====
    plan_migration()   → 只算不改, 返回报告(界面上先给用户看清楚再动手)
    apply_migration()  → 先备份 data/lists/ 再落盘
"""
import json
import os
import shutil
import time

from core import id_map
from core import list_config
from core import silence as _SIL

BACKUP_ROOT = os.path.join(list_config.DATA_DIR, "lists_backup")


def season_prefix():
    """本季 ID 前缀 —— 直接用 id_map 那份(换季时 write_season_marker 会清它的缓存)。"""
    try:
        return id_map.season_prefix() or ""
    except Exception: _SIL.note("core/list_migrate.py", "season_prefix()")
    return ""


def _chess_table():
    """{id: name} 全量表(含往季) —— 既用来反查名字, 也用来取本季 ID 集合。

    容错两种形态: chess.json 的值历史上是纯字符串, 后来变成 {"n": name, "src": ...}。
    """
    try:
        with open(os.path.join(list_config.DATA_DIR, "chess.json"), "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return {}
    out = {}
    for k, v in (d or {}).items():
        out[str(k)] = str(v.get("n") or "") if isinstance(v, dict) else str(v or "")
    return out


def current_id_set(table=None):
    """本季 ID 集合(8 位 + 本季前缀)。"""
    t = table if table is not None else _chess_table()
    p = season_prefix()
    if not p:
        return set()
    return set(k for k in t if k.isdigit() and len(k) == 8 and k.startswith(p))


def _name_by_id(table, cid):
    return str(table.get(str(cid)) or "")


def migrate_id(cid, name, table, cur):
    """单个 ID 怎么迁 → (新ID, 方式) ; 迁不动返回 ("", 原因)。

    方式: "season"(换了赛季位) / "byname"(按名字补) / "keep"(本季的, 不用动)
          / "skip"(不是 8 位棋子 ID, 比如随征锦囊) / 原因字符串
    """
    s = str(cid or "").strip()
    if not s.isdigit() or len(s) != 8:
        return ("", "skip")          # 不是棋子 ID(随征里混着锦囊 ID), 不动
    p = season_prefix()
    if not p:
        return ("", "no-prefix")
    if s.startswith(p):
        return (s, "keep")
    # ① 换掉赛季位
    cand = p + s[3:]
    if cand in cur:
        old_name = name or _name_by_id(table, s)
        new_name = _name_by_id(table, cand)
        if (not old_name) or (not new_name) or old_name == new_name:
            return (cand, "season")
    # ② 按名字补
    nm = name or _name_by_id(table, s)
    if nm:
        try:
            ids = id_map.find_ids_by_name(nm) or []
        except Exception:
            ids = []
        for x in ids:
            x = str(x)
            if x.isdigit() and len(x) == 8 and x.startswith(p):
                return (x, "byname")
    return ("", "本季没有这张卡(%s)" % (name or s))


def plan_migration():
    """只算不改 → 报告。给界面先展示"哪些会变、哪些迁不动"。"""
    table = _chess_table()
    cur = current_id_set(table)
    p = season_prefix()
    rep = {"season_prefix": p, "files": {}, "total_changed": 0, "total_unmapped": 0}
    for name_key in list_config.LIST_FILES:
        raw = list_config.load_list(name_key)
        ids = [str(x) for x in (raw.get("ids") or [])]
        nms = list(raw.get("names") or [])
        items, unmap, keep = [], [], 0
        for i, old in enumerate(ids):
            nm = nms[i] if i < len(nms) else ""
            new, how = migrate_id(old, nm, table, cur)
            if how == "keep":
                keep += 1
            elif how == "skip":
                items.append({"old": old, "new": old, "name": nm, "how": "skip"})
            elif new:
                items.append({"old": old, "new": new, "name": nm, "how": how})
            else:
                unmap.append({"old": old, "name": nm, "why": how})
        rep["files"][name_key] = {
            "changed": [x for x in items if x["how"] in ("season", "byname")],
            "skipped": [x for x in items if x["how"] == "skip"],
            "unmapped": unmap,
            "already": keep,
            "total": len(ids),
        }
        rep["total_changed"] += len(rep["files"][name_key]["changed"])
        rep["total_unmapped"] += len(unmap)
    return rep


def apply_migration():
    """备份 → 落盘 → (备份目录, 报告)。

    备份在 data/lists_backup/<时间戳>/, 迁错了能整份拷回去(名单就这几个文件, 全量备份
    比"记住旧值再回滚"可靠)。报告用的是【改完之后】重新算的一份, 所以 unmapped 是
    "仍迁不动"的那些。
    """
    stamp = time.strftime("%Y%m%d_%H%M%S")
    bdir = os.path.join(BACKUP_ROOT, stamp)
    try:
        os.makedirs(bdir, exist_ok=True)
        for fname in list_config.LIST_FILES.values():
            src = os.path.join(list_config.LISTS_DIR, fname)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(bdir, fname))
    except Exception: _SIL.note("core/list_migrate.py", "备份 data/lists/")

    table = _chess_table()
    cur = current_id_set(table)
    for name_key in list_config.LIST_FILES:
        raw = list_config.load_list(name_key)
        ids = [str(x) for x in (raw.get("ids") or [])]
        nms = list(raw.get("names") or [])
        if not ids:
            continue
        new_ids, new_nms = [], []
        for i, old in enumerate(ids):
            nm = nms[i] if i < len(nms) else ""
            new, how = migrate_id(old, nm, table, cur)
            if new:
                new_ids.append(new)
                new_nms.append(nm or _name_by_id(table, new))
            else:                      # 迁不动的【原样留下】, 不猜
                new_ids.append(old)
                new_nms.append(nm)
        if new_ids != ids or new_nms != nms:
            list_config.save_list(name_key, new_ids, new_nms)
    return (bdir, plan_migration())


if __name__ == "__main__":
    import sys
    r = plan_migration()
    print("本季前缀:", r["season_prefix"] or "(取不到)")
    for k, v in r["files"].items():
        if not v["total"]:
            continue
        print("  %-16s 共%3d  本季%3d  将迁%3d  迁不动%2d"
              % (k, v["total"], v["already"], len(v["changed"]), len(v["unmapped"])))
        for x in v["changed"][:3]:
            print("        %s → %s  [%s] %s" % (x["old"], x["new"], x["how"], x["name"]))
        for x in v["unmapped"][:3]:
            print("        ✗ %s (%s) %s" % (x["old"], x["name"], x["why"]))
    print("合计: 将迁 %d, 迁不动 %d" % (r["total_changed"], r["total_unmapped"]))
    if "--apply" in sys.argv:
        b, r2 = apply_migration()
        print("已落盘, 备份:", b)
