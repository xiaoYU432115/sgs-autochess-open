# -*- coding: utf-8 -*-
"""P2 功能可用性冒烟 · 运行时/交互层(接 app 上下文, 不点真实 UI)。

不依赖 pytest。用法(在 e:/三国杀 下):  python 自走棋助手/tests/smoke_runtime.py

目标: 抓"看着在、点了才崩"这最后空白层 —— 复用 smoke_app 的构造上下文
(withdrawn 真实 Tk 根 + mock BridgeServer/HM), 并【额外 mock BridgeOps】, 构造出完整的
App 之后, 直接调那四块紧耦合 LIVE/BridgeOps 的页签的公开/内部方法 + 剧本纯函数 +
自动招募引擎, 喂样本验证不崩且结果合理。

mock 策略(区分"代码崩溃" vs "没连游戏"):
  - BridgeServer/HM: 同 smoke_app, 避端口/win32 钩子。
  - BridgeOps: connected()→False(让构造期不真连), 但 lord_choose_general/psend/send_recv/
    get_all_generals_for_ui 等都是安全 no-op/空值 —— 调 send 类方法会真实走"构建 payload →
    调 ops"这条代码路径, 只是最后发包被吞, 正好暴露 payload 构建里的 bug, 又不会真连游戏。

覆盖: general(主公选将) / spell_lab(锦囊试验台) / scenario(剧本, 含 scenario_data 纯函数) /
auto_recruit(自动招募引擎)。
"""
import os
import sys
import tkinter as tk
import traceback

ROOT = r"e:/三国杀/自走棋助手"
sys.path.insert(0, ROOT)
os.chdir(ROOT)


# ===================== mock 上下文 =====================
class _DummyServer:
    def __init__(self, *a, **k):
        self.port = 0
    def start(self): pass
    def stop(self): pass
    def shutdown(self): pass


class _DummyHM:
    root = None
    log = None
    def bind(self, *a, **k): pass
    def start(self): pass
    def stop(self): pass
    def register(self, *a, **k): pass


class _DummyOps:
    """BridgeOps 替身: connected()=False(构造期不真连), 其余调用安全 no-op/空值。"""
    def __init__(self, *a, **k):
        pass
    def connected(self):
        return False
    def lord_choose_general(self, *a, **k):
        return {}
    def send_recv(self, *a, **k):
        return {}
    def psend(self, *a, **k):
        return {}
    def get_hand_chess(self):
        return []
    def get_all_generals_for_ui(self):
        return []
    def poll_scenario_log(self, *a, **k):
        return {}
    def start_scenario(self, *a, **k):
        return {}
    def stop_scenario(self, *a, **k):
        return {}
    def push_scenarios(self, *a, **k):
        return {}
    def set_net_probe(self, *a, **k):
        return None
    def __getattr__(self, name):
        return lambda *a, **k: None


def build_app():
    import core.bridge as _BR
    import core.hotkeys as _HK
    import core.remote_ops as _RO
    _BR.BridgeServer = _DummyServer
    _HK.HM = _DummyHM
    _RO.BridgeOps = _DummyOps
    import ui.app as APP
    root = tk.Tk()
    root.withdraw()
    app = APP.App(root)
    return app, root


def _case(cases, name, ok, detail=""):
    cases.append((name, "PASS" if ok else "FAIL", detail))


def runtime_smoke(cases):
    # ---------- 构造 App(全量 Tab 构建 + mock ops) ----------
    try:
        app, root = build_app()
    except Exception as e:
        tb = traceback.format_exc()
        _case(cases, "App() 构造(含 mock BridgeOps)", False, repr(e))
        for ln in tb.strip().splitlines()[-8:]:
            print("      " + ln)
        return None, None

    # ---------- general(主公选将) ----------
    try:
        from ui.general_tab import GeneralTabBuilder as GTB
        gens = [{"id": "21101011", "name": "曹休", "reqMinionTyp": 0},
                {"id": "21108081", "name": "陈珪", "reqMinionTyp": 0}]
        gid, mt = GTB._name_to_gid(gens, "曹休")        # 类调用(无 self)
        _case(cases, "general._name_to_gid(曹休)", gid == 21101011 and mt == 0, "%s/%s" % (gid, mt))
        gid0, _ = GTB._name_to_gid(gens, "不存在的人")
        _case(cases, "general._name_to_gid(未找到→0)", gid0 == 0, "%s" % gid0)
        gb = getattr(app, "_general_builder", None)
        if gb is not None:
            r = gb._do_send(21101011, False)            # 走 lord_choose_general(被 mock 吞)
            _case(cases, "general._do_send(走ops)", isinstance(r, dict), "=%s" % r)
        else:
            _case(cases, "general._do_send(走ops)", False, "builder 不存在")
    except Exception as e:
        _case(cases, "general", False, repr(e))

    # ---------- spell_lab(锦囊试验台) ----------
    try:
        sb = getattr(app, "_spell_lab_builder", None)
        if sb is not None:
            sb._preview()                              # 纯构造 payload(不发包)
            _case(cases, "spell_lab._preview()", True, "no-crash")
            t = sb._targets()                          # 按选项算 targets 数组
            _case(cases, "spell_lab._targets()", isinstance(t, list), "=%s" % t)
        else:
            _case(cases, "spell_lab", False, "builder 不存在")
    except Exception as e:
        _case(cases, "spell_lab", False, repr(e))

    # ---------- scenario(剧本): builder 纯方法 ----------
    try:
        scb = getattr(app, "_scenario_builder", None)
        if scb is not None:
            d = scb._scenario_step_desc({"op": "recycle", "names": ["曹休"], "retry": 2})
            _case(cases, "scenario._scenario_step_desc", isinstance(d, str) and d, "len=%d" % len(d or ""))
            m = scb._scenario_missing_names({"op": "recycle", "names": ["曹休"]})
            _case(cases, "scenario._scenario_missing_names(曹休可解析→[])", isinstance(m, list) and not m, "=%s" % m)
            vf = scb._scenario_visible_fields("recycle", {})
            _case(cases, "scenario._scenario_visible_fields", isinstance(vf, list), "n=%d" % len(vf))
            sv = scb._scenario_step_from_values("recycle", {"names": "曹休"})
            _case(cases, "scenario._scenario_step_from_values", isinstance(sv, dict), "op=%s" % sv.get("op"))
        else:
            _case(cases, "scenario(builder)", False, "builder 不存在")
    except Exception as e:
        _case(cases, "scenario(builder)", False, repr(e))

    # ---------- scenario: scenario_data 模块级纯函数 ----------
    try:
        from ui import scenario_data as SD
        ids = SD._scenario_ids_for_names(["曹休"])
        _case(cases, "scenario_data._scenario_ids_for_names(曹休)",
              isinstance(ids, list) and "21101011" in ids, "=%s" % ids[:3])
        ex = SD._expand_list_ids([{"id": "21101011", "name": "曹休"}])
        _case(cases, "scenario_data._expand_list_ids(曹休→全赛季ID)", isinstance(ex, list) and len(ex) > 1, "n=%d" % len(ex))
        step = {"op": "recycle", "exclude": ["曹休"]}
        SD._scenario_fill_target_ids(step)
        _case(cases, "scenario_data._scenario_fill_target_ids(补excludeIds)",
              "excludeIds" in step and step["excludeIds"], "=%s" % step.get("excludeIds"))
        spl = SD._scenario_split_names("曹休, 周瑜")
        _case(cases, "scenario_data._scenario_split_names", spl == ["曹休", "周瑜"], "=%s" % spl)
        _case(cases, "scenario_data._scenario_safe_int('5')", SD._scenario_safe_int("5") == 5, "")
        _case(cases, "scenario_data._scenario_safe_int('x',0)", SD._scenario_safe_int("x", 0) == 0, "")
    except Exception as e:
        _case(cases, "scenario_data", False, repr(e))

    # ---------- auto_recruit(自动招募引擎) ----------
    try:
        from core.auto_recruit import AutoRecruiter
        ar = AutoRecruiter(_DummyOps(), log_cb=None)
        ar.cfg = {"targets": "曹休,21101011", "strict_whitelist": False}
        ar.whitelist = ["21101011"]
        ar.blacklist = []
        hit = ar._in_whitelist({"goodsID": "21101011", "chessID": "21101011", "name": "曹休"})
        _case(cases, "auto_recruit._in_whitelist(白名单命中)", hit is True, "%s" % hit)
        buy, why = ar._should_buy({"goodsID": "21101011", "chessID": "21101011", "name": "曹休"})
        _case(cases, "auto_recruit._should_buy(白名单→True)", buy is True, "%s/%s" % (buy, why))
        ar.blacklist = ["21101011"]
        buy2, why2 = ar._should_buy({"goodsID": "21101011", "chessID": "21101011", "name": "曹休"})
        _case(cases, "auto_recruit._should_buy(黑名单→False)", buy2 is False, "%s/%s" % (buy2, why2))
        # P3-A 节流判定(纯函数): interval 默认 5s, <=0 为逃生钩子(每轮扫)
        due = AutoRecruiter._rescan_due
        _case(cases, "auto_recruit._rescan_due(翻转强制→True)", due(0.0, 5.0, 0.0, True) is True, "")
        _case(cases, "auto_recruit._rescan_due(5s内→False)", due(2.0, 5.0, 0.0, False) is False, "")
        _case(cases, "auto_recruit._rescan_due(超5s→True)", due(6.0, 5.0, 0.0, False) is True, "")
        _case(cases, "auto_recruit._rescan_due(钩子<=0→始终True)",
              due(0.1, 0.0, 0.0, False) is True and due(99.0, -1.0, 0.0, False) is True, "")
    except Exception as e:
        _case(cases, "auto_recruit", False, repr(e))

    return app, root


def main():
    cases = []
    print("=== 运行时/交互层冒烟(接 app 上下文) ===")
    app, root = runtime_smoke(cases)
    npass = nfail = 0
    for name, st, det in cases:
        mark = "PASS" if st == "PASS" else "FAIL"
        print("  [%s] %s  %s" % (mark, name, ("-> " + str(det)) if det else ""))
        if st == "PASS":
            npass += 1
        else:
            nfail += 1
    print("\n  运行时层: %d 通过 / %d 失败" % (npass, nfail))
    try:
        if app and getattr(app, "hotkeys_manager", None):
            app.hotkeys_manager.stop()
    except Exception:
        pass
    try:
        if app and getattr(app, "server", None):
            app.server.stop()
    except Exception:
        pass
    try:
        root.destroy()
    except Exception:
        pass
    os._exit(0)


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
