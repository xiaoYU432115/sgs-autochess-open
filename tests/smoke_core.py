
# -*- coding: utf-8 -*-
"""P2 功能可用性冒烟 · core 层。

不依赖 pytest：纯 import + assert，跑完打印 PASS/FAIL/ERROR 汇总。
用法(在 e:/三国杀 下):  python 自走棋助手/tests/smoke_core.py

两层冒烟:
  1) 导入冒烟: 每个 core 模块能否 import_module 成功(抓导入期就崩的死模块)。
  2) 函数冒烟: 对已知纯逻辑函数喂样本, 看是否返回合理值/抛异常。
UI 层(tab)需 Tk 显示, 不在此跑; 后续单独做 headless 导入冒烟。
"""
import importlib
import os
import sys
import traceback

ROOT = r"e:/三国杀/自走棋助手"
sys.path.insert(0, ROOT)
# 真机从 自走棋助手/ 启动, config.json 等按 CWD 找; 这里同步切过去, 否则 load_config() 找不到表
os.chdir(ROOT)

CORE_MODULES = [
    "id_map", "econ_planner", "refresh_advisor", "battle_brain", "tavern_kb",
    "config", "csv_loader", "lineup_advisor", "pivot_routes", "endgame_matcher",
    "opponent_archive", "tavern_stats", "s11_enhance", "mechanism", "pick_diag",
    "protocol", "recorder", "remote_ops", "bridge", "hotkeys", "res_server",
    "resource_rules", "ext_script", "codex_server", "card_image", "atlas",
    "dyn_skin", "matchlog", "records", "list_migrate", "list_config", "preset",
    "pool_probe", "tool_state", "utils", "silence", "page_errs", "auto_battle",
    "auto_recruit",
]


def import_smoke():
    print("=== 1) 导入冒烟 (core) ===")
    ok, fail = [], []
    for m in CORE_MODULES:
        try:
            importlib.import_module("core." + m)
            ok.append(m)
        except Exception as e:
            fail.append((m, repr(e)))
    for m in ok:
        print(f"  [OK]   core.{m}")
    for m, e in fail:
        print(f"  [FAIL] core.{m} -> {e}")
    print(f"  导入: {len(ok)} 通过 / {len(fail)} 失败\n")
    return ok, fail


def func_smoke():
    print("=== 2) 函数冒烟 (纯逻辑) ===")
    cases = []

    # id_map.chess_cost_of: K1 残留边界(金卡变体应返回 int 而非 None)
    try:
        from core import id_map as M
        for cid in ["21101011", "21108081", "21101012"]:
            r = M.chess_cost_of(cid)
            cases.append((f"id_map.chess_cost_of({cid})", "PASS" if isinstance(r, int) else f"返回 {r!r}(疑似 None)", r))
        # K1 回归守卫: 金卡变体 21101012 必须 == 基底卡 21101011 的 rank(1), 不能 None
        assert M.chess_cost_of("21101012") == M.chess_cost_of("21101011") == 1, "K1 金卡变体回归失败"
        cases.append(("K1 回归: chess_cost_of(21101012)==chess_cost_of(21101011)==1", "PASS", "guarded"))
    except AssertionError as ae:
        cases.append(("K1 回归守卫", "FAIL", str(ae)))
    except Exception as e:
        cases.append(("id_map.chess_cost_of", "ERROR", repr(e)))

    # econ_planner.tier_values: 应返回实算 dict, 非线性兜底
    try:
        from core import econ_planner as EP
        tv = EP.tier_values()
        good = isinstance(tv, dict) and all(isinstance(v, (int, float)) for v in tv.values())
        cases.append(("econ_planner.tier_values()", "PASS" if good else "可疑", tv))
    except Exception as e:
        cases.append(("econ_planner.tier_values()", "ERROR", repr(e)))

    # refresh_advisor.rough_odds: 基础 S11 ID 应能算出概率(非 None)。
    # 注意: 必须显式传 cfg=load_config(), 否则内部 load_config() 在独立 CWD 下取不到 star_odds 表会返回 None(环境约束, 非逻辑 bug)
    try:
        from core import refresh_advisor as RA
        from core.config import load_config
        cfg = load_config()
        od = RA.rough_odds("21101011", 5, cfg)
        cases.append(("refresh_advisor.rough_odds(21101011,5)", "PASS" if isinstance(od, dict) else f"返回 {od!r}(缺 star_odds 表)", od))
        # ---- P5 概率换地基: 锁定"上界口径 = 该档出现率 ÷ 该档种数", pool_taken 不进概率 ----
        from core import id_map as _IDMAP
        _c = "21101011"
        _r = _IDMAP.chess_cost_of(_c)
        _row = (cfg.get("star_odds") or {}).get("5") or []
        if _r and 1 <= _r <= 6 and _r - 1 < len(_row) and isinstance(od, dict):
            _exp_ps = float(_row[_r - 1]) / 100.0 / RA._POOL_KINDS[_r]
            _exp_p5 = 1 - (1 - _exp_ps) ** RA._SHOP_SLOTS[5]
            _ok = (abs(od["p_slot"] - _exp_ps) < 1e-9 and abs(od["p5"] - _exp_p5) < 1e-9
                   and od.get("kinds") == RA._POOL_KINDS[_r] and od.get("upper_bound") is True
                   and "left_me" not in od and "coarse" not in od)
            cases.append(("P5 rough_odds 上界口径=出现率÷种数", "PASS" if _ok else "FAIL",
                          "p_slot=%.5f exp=%.5f kinds=%s" % (od.get("p_slot", -1), _exp_ps, od.get("kinds"))))
        # pool_taken 不再影响概率(换地基核心语义): taken=0 与 taken=20 的 p_slot 必须相同; seen 另记
        _o0 = RA.rough_odds(_c, 5, cfg, taken={})
        _o20 = RA.rough_odds(_c, 5, cfg, taken={_c: 20})
        _same = (isinstance(_o0, dict) and isinstance(_o20, dict)
                 and abs(_o0["p_slot"] - _o20["p_slot"]) < 1e-12 and _o20.get("seen") == 20)
        cases.append(("P5 rough_odds taken 不进概率(仅记 seen=20)", "PASS" if _same else "FAIL",
                      "p0=%s p20=%s seen=%s" % (_o0 or {}, _o20 or {}, (_o20 or {}).get("seen"))))
        # 配置表权威种数(=14/24/34/42/36/34, 总 184)锁定 —— 不用会漂移的 _tier_counts(4 费 43)
        _kinds_ok = (RA._POOL_KINDS == {1: 14, 2: 24, 3: 34, 4: 42, 5: 36, 6: 34}
                     and sum(RA._POOL_KINDS.values()) == 184)
        cases.append(("P5 _POOL_KINDS 权威种数(总184, 4费=42)", "PASS" if _kinds_ok else "FAIL", str(RA._POOL_KINDS)))
        # 诚实标注: format_report 含"上界"、不含"还剩/池中剩余"(不把估算伪装成实锤)
        _fake = {"verdict": "可以刷", "reasons": ["占位"],
                 "odds": {"cid": _c, "name": "测试卡", "level": 5, "tier_pct": 20.0,
                          "kinds": 36, "p_slot": 20.0 / 36, "p5": 1 - (1 - 20.0 / 36) ** 5,
                          "seen": 3, "upper_bound": True}}
        _rep = RA.format_report(_fake)
        _honest = ("上界" in _rep) and ("还剩" not in _rep) and ("池中剩余" not in _rep)
        cases.append(("P5 format_report 标注上界、不说'还剩几张'", "PASS" if _honest else "FAIL", ""))
    except Exception as e:
        cases.append(("refresh_advisor.rough_odds", "ERROR", repr(e)))

    for name, st, val in cases:
        print(f"  [{st}] {name}  -> {val}")
    print()
    return cases


if __name__ == "__main__":
    ok, fail = import_smoke()
    func_smoke()
    print("汇总: 导入失败模块需逐个排查(可能是缺依赖或导入期 bug); 函数冒烟见上。")
