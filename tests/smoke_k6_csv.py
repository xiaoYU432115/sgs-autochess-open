# -*- coding: utf-8 -*-
"""P2 细验 K6: 读配置表必须按赛季过滤。

不依赖 pytest。用法(在 e:/三国杀 下):  python 自走棋助手/tests/smoke_k6_csv.py

K6 背景(来自 feedback_season_filter): ChessConf.csv 跨全赛季(3700+ 条), 若按名字裸抓
会拿到【旧赛季的技能】(实测五条错四条)。csv_loader 全量读是【有意为之】(全量名表/兜底解析
需要跨赛季), 赛季过滤在【消费端】做。本测试验证:
  ① 白名单 = 当前赛季且商店可购(get_whitelist_from_csv 真的按赛季+商店过滤);
  ② chess_names_all(season) / chess_skill_map(season_filter=True) 只收该赛季;
  ③ 默认 chess_skill_map 用"当前赛季+商店"评分, 不会把丁奉(多赛季)错配成旧赛季技能
    —— 直接堵死"按名字抓错赛季"那类 bug。
"""
import os
import sys
import traceback

ROOT = r"e:/三国杀/自走棋助手"
sys.path.insert(0, ROOT)
os.chdir(ROOT)


def _case(cases, name, ok, detail=""):
    cases.append((name, "PASS" if ok else "FAIL", detail))


def k6_smoke(cases):
    try:
        from core import csv_loader as CL
    except Exception as e:
        _case(cases, "import csv_loader", False, repr(e))
        return

    # ---------- ① 全量数据非空 + 跨多赛季 ----------
    try:
        all_data = CL.load_from_csv()
        _case(cases, "csv_loader 全量读非空", len(all_data) > 1000, "n=%d" % len(all_data))
        seasons = set(c.get("season") for c in all_data)
        _case(cases, "ChessConf 跨多赛季", len(seasons) >= 2, "seasons=%s" % sorted(seasons)[:6])
    except Exception as e:
        _case(cases, "csv_loader 全量读", False, repr(e))
        return

    # ---------- ② 白名单按 赛季+商店 过滤 ----------
    try:
        wl = CL.get_whitelist_from_csv()
        _case(cases, "白名单非空(当前赛季商店武将)", bool(wl), "n=%d" % len(wl))
        bad = [c for c in wl if not (c["season"] == CL.CURRENT_SEASON and c["isShop"] == "1")]
        _case(cases, "白名单严格按赛季+商店过滤",
              not bad, "漏过滤 %d 条(应全为 s%s+IsShop=1)" % (len(bad), CL.CURRENT_SEASON))
    except Exception as e:
        _case(cases, "get_whitelist_from_csv", False, repr(e))

    # ---------- ③ chess_names_all(season) 只收该赛季 ----------
    try:
        s10_ids = {c["chessID"] for c in all_data if c["season"] == "10"}
        names10 = CL.chess_names_all(season="10")
        leak = [cid for cid in names10 if cid not in s10_ids]
        _case(cases, "chess_names_all(season=10) 不漏非s10 ID", not leak,
              "漏 %d 个非s10 ID" % len(leak) if leak else "")
    except Exception as e:
        _case(cases, "chess_names_all", False, repr(e))

    # ---------- ④ chess_skill_map(season_filter=True) 只收该赛季 ----------
    try:
        sm = CL.chess_skill_map(season="10", season_filter=True)
        leak = [cid for cid in sm["ids"] if cid not in s10_ids]
        _case(cases, "chess_skill_map(filter=True) ids 只含s10",
              not leak, "漏 %d 个非s10 ID" % len(leak) if leak else "")
        _case(cases, "chess_skill_map 返回结构完整",
              isinstance(sm.get("ids"), dict) and isinstance(sm.get("names"), dict)
              and isinstance(sm.get("picks"), list), "")
    except Exception as e:
        _case(cases, "chess_skill_map(filter)", False, repr(e))

    # ---------- ⑤ 按名字抓错赛季防护: 丁奉(多赛季) 默认应配 s10+商店技能 ----------
    try:
        df_seasons = {c["season"] for c in all_data if c["name"] == "丁奉"}
        _case(cases, "丁奉样本确实跨多赛季(测试有意义)", len(df_seasons) >= 2,
              "seasons=%s" % sorted(df_seasons))
        df_shop = [c for c in all_data
                   if c["name"] == "丁奉" and c["season"] == "10" and c["isShop"] == "1"]
        if df_shop:
            expected = df_shop[0]["skills"]
            # 强制 cur=10 + 只收s10名字(不依赖 CURRENT_SEASON 取值)
            sm10 = CL.chess_skill_map(season="10")
            got = sm10["names"].get("丁奉")
            _case(cases, "chess_skill_map 默认把丁奉配到 s10+商店技能(防旧赛季错配)",
                  got == expected, "got=%s expect=%s" % (got, expected))
        else:
            _case(cases, "chess_skill_map 丁奉 s10 技能核对", False, "CSV 里找不到 s10 丁奉商店行")
    except Exception as e:
        _case(cases, "chess_skill_map 丁奉防护", False, repr(e))


def main():
    cases = []
    print("=== P2 细验 K6: csv_loader 按赛季过滤 ===")
    k6_smoke(cases)
    npass = nfail = 0
    for name, st, det in cases:
        print("  [%s] %s  %s" % ("PASS" if st == "PASS" else "FAIL", name,
                                 ("-> " + str(det)) if det else ""))
        if st == "PASS":
            npass += 1
        else:
            nfail += 1
    print("\n  K6: %d 通过 / %d 失败" % (npass, nfail))
    os._exit(0)


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
