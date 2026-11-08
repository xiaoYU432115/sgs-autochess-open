# -*- coding: utf-8 -*-
"""生成【卡 ID → 拼音候选】离线表 data/card_pinyin.json (v4.107★)

为什么是"离线表"而不是运行时现转: 手册页 / 助手运行时不该依赖 pypinyin(打包成 exe
还要多带一个包)。这里【只在重跑本脚本时】用一次 pypinyin, 把 372 张卡的拼音算好存成
JSON, 运行时(core/card_image.py)只查表。

跑法:
    pip install pypinyin          # 只这一处需要
    python tools/build_card_pinyin.py
"""
import os
import sys
import json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

DATA_DIR = os.path.join(ROOT, "data")
OUT = os.path.join(DATA_DIR, "card_pinyin.json")


def _current_season():
    """当前赛季号(与 season.json 一致), 取不到回退 10。"""
    try:
        with open(os.path.join(DATA_DIR, "season", "season.json"), "r", encoding="utf-8") as f:
            return str((json.load(f) or {}).get("season") or "").strip().lstrip("sS") or "10"
    except Exception:
        return "10"


def _load_pool(season):
    """本季卡池: 优先 tavern_chess_s<季>.json, 没有就现从官方赛季表抽。

    v4.160★ 修: 以前写死 S10, 换季后 S11 的 211… 卡一条拼音都查不到,
    卡图 HEAD 探测直接全 404(官方按拼音路径找图)。
    """
    want = "tavern_chess_s%s.json" % season
    p = os.path.join(DATA_DIR, want)
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and d:
                return d, want
        except Exception as e:
            print("[拼音] 读 %s 失败: %s" % (want, e))
    # 本季表缺失 → 从官方赛季表抽(与 build_tavern_pool 同源)
    try:
        import tools.build_tavern_pool as BP
        dst = BP.build(season, verbose=False)
        with open(dst, "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and d:
            return d, os.path.basename(dst)
    except Exception as e:
        print("[拼音] 从官方表抽本季卡池失败: %s" % e)
    # 兜底 S10
    p10 = os.path.join(DATA_DIR, "tavern_chess_s10.json")
    try:
        with open(p10, "r", encoding="utf-8") as f:
            return json.load(f), "tavern_chess_s10.json(fallback)"
    except Exception:
        return {}, "empty"


def _cands(name):
    """一个中文名 → 拼音候选(最多 4 个, 第一个是最可能的)。

    给多个是因为【多音字】: "惇" 官方可能写 dun 也可能写 zhun, "珪" 可能是 gui ——
    猜错就是 404, 所以宁可多给几个候选, 让 card_image 去 HEAD 问官方哪个在。
    """
    try:
        from pypinyin import pinyin, Style
    except ImportError:
        print("缺 pypinyin —— 先 pip install pypinyin(只有本脚本需要它)")
        return []
    # 只留汉字(像"SP关羽"那种前缀不进拼音)
    han = "".join(ch for ch in str(name or "") if "\u4e00" <= ch <= "\u9fff")
    if not han:
        return []
    base = "".join(x[0] for x in pinyin(han, style=Style.NORMAL, heteronym=False))
    outs = [base]
    # 多音字组合(每字最多 3 个读音, 总组合封顶 8, 去重后取前 4)
    per = pinyin(han, style=Style.NORMAL, heteronym=True)
    combos = [""]
    for seg in per:
        seg2 = list(dict.fromkeys([s for s in seg if s]))[:3]
        combos = [c + s for c in combos for s in seg2]
        if len(combos) > 8:
            combos = combos[:8]
            break
    for c in combos:
        if c and c not in outs:
            outs.append(c)
    return outs[:4]


def main():
    season = _current_season()
    raw, src = _load_pool(season)
    if not isinstance(raw, dict) or not raw:
        print("卡池为空, 无法生成拼音表")
        return 1
    out = {}
    for cid, v in raw.items():
        if not isinstance(v, dict):
            continue
        nm = v.get("name") or v.get("n") or ""
        cs = _cands(nm)
        if cs:
            out[str(cid)] = cs
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print("写表失败:", e)
        return 1
    print("已写 %s: %d 张卡(卡池来源 %s, 共 %d 条)" % (OUT, len(out), src, len(raw)))
    for cid in list(out)[:8]:
        print("  ", cid, out[cid])
    return 0


if __name__ == "__main__":
    sys.exit(main())
