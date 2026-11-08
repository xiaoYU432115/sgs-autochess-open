# -*- coding: utf-8 -*-
"""战绩记录与胜率统计（借鉴油猴"打小抄"脚本的核心玩法）"""
import csv
import json
import os
import time
from core import silence as _SIL

try:
    from .config import DATA_DIR
except ImportError:   # 直接运行本文件时
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from core.config import DATA_DIR

RECORDS_PATH = os.path.join(DATA_DIR, "records.json")


def load_records():
    if os.path.exists(RECORDS_PATH):
        try:
            with open(RECORDS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            _SIL.note("core/records.py:25", "with open(RECORDS_PATH, 'r', encoding='utf-8')")
    return []


def save_records(records):
    with open(RECORDS_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=1)


def add_record(mode, lord, generals, result, placement, duration_min, note="", place_src=""):
    """写一条战绩。

    v4.68★ 新增 place_src: 名次的【来源标记】。为什么需要它 —— 老版把协议里的 emRus
      当名次写进 placement, 而 emRus 其实是"每场战斗的结果码"(见 core/protocol.py 的源码依据),
      于是 209 条战绩的 placement 是脏的(1/2/3 = 胜/平/负)。没有来源标记就无法区分
      "第 2 名"(真名次) 和 "平局"(脏数据), 界面上就会一直报出 100% 前四率这种假数。
      现在: place_src="elim" = 由淘汰顺序推出(可信); 空 = 不知来源(界面按未知处理)。
    """
    records = load_records()
    item = {
        "time": time.strftime("%Y-%m-%d %H:%M"),
        "ts": int(time.time()),
        "mode": mode or "自走棋",
        "lord": lord or "",
        "generals": generals or [],
        "result": result,            # 胜/负
        "placement": int(placement) if placement else 0,
        "duration_min": float(duration_min) if duration_min else 0.0,
        "note": note or ""
    }
    if place_src:
        item["place_src"] = place_src     # v4.68★ 名次来源(elim=淘汰顺序)
    records.append(item)
    save_records(records)
    return records


def _is_win(r):
    """v4.107★ 口径统一 —— 自动记录写的是「胜利/前四/失败/未知」, 老的手写战绩和别的
    来源写的是「胜/负」: 以前只认"胜"一个字, 于是自动记的那 200 多条胜率全是 0%。
    现在两种都认, 另外第一名(placement==1)也算赢。
    """
    res = str(r.get("result") or "").strip()
    if res.startswith("胜"):          # 胜 / 胜利
        return True
    try:
        return int(r.get("placement") or 0) == 1
    except Exception:
        return False


def _good_placement(r):
    """这个名次能用吗? 不能用返回 0。

    老数据(209 条)的 placement 其实是【末场战斗结果码】(只有 1/2/3 三档), 直接当名次
    算平均/前四率会算出假数。可信条件: place_src=="elim"(由淘汰顺序推的), 或者名次 >3
    (脏数据里不可能出现) —— 另外自走棋就 8 个人, 超出 1~8 的一律不认(老数据里有 13/16
    这种, 拿去算平均会把"平均名次"顶到十几名, 看着就不对)。
    """
    try:
        p = int(r.get("placement") or 0)
    except Exception:
        return 0
    if not (1 <= p <= 8):
        return 0
    if str(r.get("place_src") or "") == "elim":
        return p
    return p if p > 3 else 0


def remove_record(index):
    records = load_records()
    if 0 <= index < len(records):
        records.pop(index)
        save_records(records)
    return records


def stats_by_general(records):
    """-> [(武将, 出场, 胜场, 胜率%, 平均名次), ...] 按出场排序"""
    per = {}
    for r in records:
        for g in r.get("generals", []):
            if not g:
                continue
            d = per.setdefault(g, [0, 0, 0, 0, 0.0])
            d[0] += 1
            if _is_win(r):
                d[1] += 1
            gp = _good_placement(r)
            if gp:
                d[2] += gp
                d[3] += 1
    out = []
    for g, (n, w, p, pn, _) in per.items():
        wr = round(w / n * 100, 1) if n else 0.0
        avg_p = round(p / pn, 2) if pn else 0.0
        out.append((g, n, w, wr, avg_p))
    out.sort(key=lambda x: (-x[1], -x[3]))
    return out


def stats_overall(records):
    n = len(records)
    w = sum(1 for r in records if _is_win(r))
    ps = [_good_placement(r) for r in records]
    ps = [p for p in ps if p]
    top4 = sum(1 for p in ps if p <= 4)
    return {"games": n, "wins": w, "winrate": round(w / n * 100, 1) if n else 0.0,
            "top3": sum(1 for p in ps if p <= 3), "top4": top4,
            "top4_rate": round(top4 / len(ps) * 100, 1) if ps else 0.0,
            "avg_placement": round(sum(ps) / len(ps), 2) if ps else 0.0,
            "placed": len(ps)}     # placed = 名次可信的场数(其余的不能拿来算平均)


def stats_openings(records, key_size=3):
    """开局组合(主公+前N武将)胜率，按场次排序 -> [(组合文本, 场次, 胜率%)]"""
    per = {}
    for r in records:
        lord = r.get("lord") or "-"
        gens = sorted(g for g in r.get("generals", [])[:key_size] if g)
        key = f"{lord} + {'、'.join(gens)}" if gens else f"{lord} (无将)"
        d = per.setdefault(key, [0, 0])
        d[0] += 1
        if _is_win(r):
            d[1] += 1
    out = [(k, n, round(w / n * 100, 1)) for k, (n, w) in per.items()]
    out.sort(key=lambda x: -x[1])
    return out


def export_csv(path, records):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["时间", "模式", "主公", "武将", "结果", "名次", "时长(分)", "备注"])
        for r in records:
            wr.writerow([r.get("time"), r.get("mode"), r.get("lord"),
                         "、".join(r.get("generals", [])), r.get("result"),
                         r.get("placement"), r.get("duration_min"), r.get("note")])
    return path
