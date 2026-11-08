# -*- coding: utf-8 -*-
"""P2 功能可用性冒烟 · 核心引擎功能层 (headless 喂样本)。

不依赖 pytest。用法(在 e:/三国杀 下):  python 自走棋助手/tests/smoke_engines.py

目标: 抓出"看着在、实际死 / 算错"的核心引擎逻辑 —— 喂最小样本, 验证关键函数不崩且结果合理。
与 smoke_core(导入+函数) / smoke_ui(导入) / smoke_app(构造) 互补, 这一层直击"功能是否真的能用"。

覆盖(纯逻辑、无需实时对局连接):
  - list_migrate.migrate_id    跨季 ID 迁移(也对应 K6 赛季过滤)
  - endgame_matcher.analyze    终端对局类型识别
  - battle_brain.*             局势大脑: 归一化/有效攻防/对拼预测/体系识别/针对建议/经济自由度
  - econ_planner.plan          经济决策器(蒙特卡洛模拟)
  - refresh_advisor.advise    刷新决策器
未覆盖(需实时对局 LIVE / BridgeOps, 留待接 mock 的 app 上下文再补):
  - auto_recruit(自动招募) / scenario(剧本) / general_tab(主公选将) / spell_lab(锦囊试验台)
"""
import importlib
import os
import sys
import traceback

ROOT = r"e:/三国杀/自走棋助手"
sys.path.insert(0, ROOT)
os.chdir(ROOT)  # config.json / data 按 CWD 找


def _case(cases, name, ok, detail=""):
    cases.append((name, "PASS" if ok else "FAIL", detail))


def engine_smoke(cases):
    # ---------- list_migrate: 跨季 ID 迁移 (K6) ----------
    try:
        from core import list_migrate as LM
        from core import id_map as IDMAP
        table = LM._chess_table()
        cur = LM.current_id_set(table)
        # 固定本季前缀为 211, 让测试不依赖换季标记文件
        _orig = IDMAP.season_prefix
        IDMAP.season_prefix = lambda: "211"
        try:
            new, how = LM.migrate_id("21008081", "陈珪", table, cur)
            _case(cases, "list_migrate.migrate_id(21008081→21108081)",
                  new == "21108081" and how == "season", "%s / %s" % (new, how))
            new2, how2 = LM.migrate_id("21101011", "曹休", table, cur)
            _case(cases, "list_migrate.migrate_id(本季ID keep)",
                  new2 == "21101011" and how2 == "keep", "%s / %s" % (new2, how2))
            new3, how3 = LM.migrate_id("942311", "黄巾兵", table, cur)
            _case(cases, "list_migrate.migrate_id(非棋子ID skip)",
                  new3 == "" and how3 == "skip", "%s / %s" % (new3, how3))
        finally:
            IDMAP.season_prefix = _orig
    except Exception as e:
        _case(cases, "list_migrate", False, repr(e))

    # ---------- endgame_matcher: 终端对局识别 ----------
    try:
        from core import endgame_matcher as EM
        board = [{"name": "田尚衣"}, {"name": "田尚衣"}, {"name": "庞统"}]
        r = EM.analyze(board)
        top = (r.get("top") or {}).get("key")
        _case(cases, "endgame_matcher.analyze(多田信号)",
              r.get("ok") and top == "duotian_du", "top=%s" % top)
        r2 = EM.analyze([])
        _case(cases, "endgame_matcher.analyze(空盘不崩)",
              (not r2.get("ok")) and "reason" in r2, "ok=%s" % r2.get("ok"))
    except Exception as e:
        _case(cases, "endgame_matcher", False, repr(e))

    # ---------- battle_brain: 局势大脑 ----------
    try:
        from core import battle_brain as BB
        mine = [{"chessID": "21101011"}, {"chessID": "21108081"}]
        opp = [{"chessID": "21101011"}, {"chessID": "21101011"}]
        p = BB.normalize_piece({"chessID": "21101011"})
        _case(cases, "battle_brain.normalize_piece(曹休)",
              isinstance(p, dict) and p.get("name") == "曹休", "name=%s" % (p or {}).get("name"))
        es = BB.effective_stats(mine)
        _case(cases, "battle_brain.effective_stats",
              isinstance(es, dict) and "e_atk" in es and "e_hp" in es, "e=%s/%s" % (es.get("e_atk"), es.get("e_hp")))
        dd = BB.predict_duel(mine, opp)
        _case(cases, "battle_brain.predict_duel",
              isinstance(dd, dict) and "verdict" in dd, "verdict=%s" % dd.get("verdict"))
        ds = BB.detect_system(mine, lord_faction="魏")
        _case(cases, "battle_brain.detect_system",
              isinstance(ds, dict) and "system" in ds, "system=%s" % ds.get("system"))
        ca = BB.counter_advice([opp], mine=mine)
        _case(cases, "battle_brain.counter_advice",
              isinstance(ca, dict) and "ok" in ca, "ok=%s" % ca.get("ok"))
        ef = BB.econ_freedom(coin=10, level=3)
        _case(cases, "battle_brain.econ_freedom",
              isinstance(ef, dict) and "act" in ef, "act=%s" % (ef or {}).get("act"))
    except Exception as e:
        _case(cases, "battle_brain", False, repr(e))

    # ---------- econ_planner: 经济决策器(模拟) ----------
    try:
        from core import econ_planner as EP
        from core.config import load_config
        cfg = load_config()
        d = EP.plan(targets="21101011", keep_gold=0, trials=20, cfg=cfg)
        _case(cases, "econ_planner.plan(目标21101011,20试)",
              isinstance(d, dict) and "ok" in d, "ok=%s verdict=%s" % (d.get("ok"), d.get("verdict")))
    except Exception as e:
        _case(cases, "econ_planner.plan", False, repr(e))

    # ---------- refresh_advisor: 刷新决策器 ----------
    try:
        from core import refresh_advisor as RA
        from core.config import load_config
        cfg = load_config()
        d = RA.advise(target="21101011", cfg=cfg)
        _case(cases, "refresh_advisor.advise(目标21101011)",
              isinstance(d, dict) and ("verdict" in d or "ok" in d), "verdict=%s" % d.get("verdict"))
    except Exception as e:
        _case(cases, "refresh_advisor.advise", False, repr(e))


def main():
    cases = []
    print("=== 核心引擎功能冒烟 ===")
    engine_smoke(cases)
    npass = nfail = 0
    for name, st, det in cases:
        mark = "PASS" if st == "PASS" else "FAIL"
        print("  [%s] %s  %s" % (mark, name, ("-> " + str(det)) if det else ""))
        if st == "PASS":
            npass += 1
        else:
            nfail += 1
    print("\n  引擎功能: %d 通过 / %d 失败" % (npass, nfail))
    if nfail:
        print("  (未覆盖: auto_recruit / scenario / general_tab / spell_lab 需实时 LIVE/BridgeOps)")
    return nfail


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
