# -*- coding: utf-8 -*-
"""对手成型阵容归档 —— 每局记下"打到最后的几家用的什么阵容", 攒起来统计高频 + 给反制思路。

为什么值得单独做: 换季之后, "现在天梯上最多的是什么阵容"是最值钱的一条信息 ——
   它直接决定你这局往哪转、优先抢什么卡、留什么克制位。但打完就忘, 最后只能凭印象。
   所以每局结束自动存一份, 攒够一定局数就能看出"流行什么"。

数据全都是现成的(不用为此新开一路采集):
   · 对手阵容: core/protocol.py 的 _sniff_any_chess() 已经把嗅探到的棋子挂在
     GameLive.others[i]['_board']; auto_recruit 还会每 10 秒主动拉一个对手的 lastLineUp。
   · 血量: others[i]['hp'](每回合 DataUpdate 都在刷)。
   · 淘汰顺序: GameLive.elim_order —— 第 1 个被淘汰的是第 8 名, 活到最后的是第 1 名
     (口径见 protocol._track_eliminations 的注释)。

存: data/archive_lineups.json(与 records.json 同级, 原子写)。

怎么用:
   · 自动: protocol._finalize_game_over() 每局结束调一次 archive_game(), 一般不用手工调;
   · 看结果: `python -m core.opponent_archive` 打一份"高频阵容 + 拿什么克制"的简报。
"""
import io
import json
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "data", "archive_lineups.json")
COUNTERS = os.path.join(ROOT, "data", "lineup_counters.json")

MAX_GAMES = 400       # 只留最近这么多局(一局一条, 够跨好几个赛季统计了)
TOP_N = 3             # 每局记前几名


def _load_json(p, default):
    try:
        with io.open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(p, d):
    """原子写: 先写临时文件再整体替换(写一半崩了不会留下半个 json)。"""
    tmp = p + ".tmp"
    d = os.path.dirname(p)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    with io.open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def _one_name(it, nm):
    """_board 里的一个元素 → 卡名。元素可能是 goodsID、也可能是 {chessID:..} 这样的 dict。"""
    if isinstance(it, dict):
        for k in ("chessID", "ChessID", "goodsID", "id", "ID"):
            if it.get(k):
                return str(nm(it[k]) or "").strip()
        return ""
    return str(nm(it) or "").strip()


def archive_game(others, my_id="", elim_order=None, mode="自走棋", lord="", my_rank=0, nm=None):
    """一局结束时把【前 TOP_N 名】的阵容记下来。

    返回写进去的条数; 0 = 一个对手阵容都没嗅探到(这种情况不写, 免得攒一堆空记录)。
    nm: ID → 卡名的函数, protocol 传自己的 _nm(带 goodsID→chessID 换算和 S10 剥离);
        不传就退回 IDMAP.name_of。
    """
    if nm is None:
        from core.id_map import IDMAP
        nm = lambda g: IDMAP.name_of(g)

    order = [str(u) for u, _hp, _ts in (elim_order or [])]
    rows = []
    for o in (others or []):
        if not isinstance(o, dict):
            continue
        uid = str(o.get("userID") or o.get("UserID") or "")
        if uid and my_id and uid == str(my_id):
            continue                      # 自己不算对手
        board = o.get("_board") or o.get("lastLineUp") or []
        names = []
        for it in board:
            s = _one_name(it, nm)
            if not s:
                continue
            if "(" in s and s.endswith(")"):        # "21002021(张星彩)" → "张星彩"
                s = s.split("(", 1)[1][:-1]
            if s and s not in names:
                names.append(s)
        if not names:
            continue                      # 没嗅探到就不记(宁可少一条, 不记一条空的)
        hp = o.get("hp")
        rows.append({
            "uid": uid,
            "who": str(o.get("nickname") or o.get("name") or ""),
            "hp": hp if isinstance(hp, (int, float)) else None,
            "dead": uid in order,
            "generals": names,
        })
    if not rows:
        return 0

    # 排序: 活着的在前(名次更好), 同档按血多的在前。
    #   为什么不用淘汰顺序直接排名次: 协议里没有"最终结算"那一下, 活着的几家谁第几
    #   只能靠血量当代理指标(自走棋里血多确实基本等于名次好)。
    rows.sort(key=lambda r: (0 if not r["dead"] else 1, -(r["hp"] or 0)))
    top = rows[:TOP_N]
    for i, r in enumerate(top, 1):
        r["rank_est"] = i

    d = _load_json(PATH, {"v": 1, "games": []}) or {"v": 1, "games": []}
    games = d.get("games") or []
    games.append({
        "ts": int(time.time()),
        "mode": mode,
        "lord": lord,
        "my_rank": my_rank or 0,
        "top3": top,
    })
    if len(games) > MAX_GAMES:
        games = games[-MAX_GAMES:]
    d["games"] = games
    _save_json(PATH, d)
    return len(top)


def top_lineups(limit=10, min_n=1):
    """高频阵容排行: 卡名集合一模一样算同一种, 返回 [{cards, n, avg_rank}]。"""
    d = _load_json(PATH, {"v": 1, "games": []}) or {"v": 1, "games": []}
    freq = {}
    for g in (d.get("games") or []):
        for t in (g.get("top3") or []):
            names = [x for x in (t.get("generals") or []) if x]
            if not names:
                continue
            key = ",".join(sorted(names))
            e = freq.setdefault(key, {"cards": sorted(names), "n": 0, "_r": []})
            e["n"] += 1
            e["_r"].append(t.get("rank_est") or 0)
    out = []
    for e in freq.values():
        rk = e.pop("_r")
        e["avg_rank"] = round(sum(rk) / float(len(rk)), 2) if rk else 0
        out.append(e)
    out.sort(key=lambda e: (-e["n"], e["avg_rank"]))
    return [e for e in out if e["n"] >= min_n][:limit]


def _hit(kw, text, names):
    """克制关键词能不能对上这份阵容 —— 关键词允许【连写多张卡】(如 '董承何进')。"""
    kw = (kw or "").strip()
    if not kw:
        return False
    if kw in text:
        return True
    # 连写的多卡名: 按 2 字一组拆开, 拆出来的都在阵容里才算对上
    parts = [kw[i:i + 2] for i in range(0, len(kw), 2)]
    if len(parts) >= 2:
        return all(p in names for p in parts)
    return False


def counter_advice(cards, limit=4):
    """这份阵容该拿什么克制 —— 查 data/lineup_counters.json 里手工维护的克制关系。

    返回 [{use, beats, note}]: use = 建议你用的那个, beats = 它能压住对方阵容里的什么。
    查不到就是没维护过这条(去阵容页加), 不瞎编。
    """
    d = _load_json(COUNTERS, {"items": []}) or {"items": []}
    cards = [c for c in (cards or []) if c]
    text = "".join(cards)
    names = set(cards)
    out = []
    for it in (d.get("items") or []):
        a, b = str(it.get("a") or ""), str(it.get("b") or "")
        win = str(it.get("win") or "")
        if _hit(a, text, names) and win == "b":
            out.append({"use": b, "beats": a, "note": it.get("note") or ""})
        elif _hit(b, text, names) and win == "a":
            out.append({"use": a, "beats": b, "note": it.get("note") or ""})
    return out[:limit]


def brief(limit=10):
    """给命令行/日志用的一份简报: 高频阵容 + 各自的反制思路。"""
    d = _load_json(PATH, {"v": 1, "games": []}) or {"v": 1, "games": []}
    n = len(d.get("games") or [])
    lines = ["对手成型阵容归档: 共 %d 局" % n]
    if not n:
        lines.append("  (还没有 —— 打完一局就会自动记一条)")
        return "\n".join(lines)
    for i, e in enumerate(top_lineups(limit=limit), 1):
        cards = e["cards"]
        lines.append("  %d. x%d  平均名次 %s  %s"
                     % (i, e["n"], e["avg_rank"], " ".join(cards)))
        for c in counter_advice(cards):
            lines.append("       可用 %s 压 %s%s"
                         % (c["use"], c["beats"], (" (%s)" % c["note"] if c["note"] else "")))
    return "\n".join(lines)


if __name__ == "__main__":
    print(brief())
