# -*- coding: utf-8 -*-
"""剧本 tab 2026-11-08 审计修复的针对性冒烟(B1~B5 + 桥端 eqLiuJian 链路)。

不依赖 pytest。用法(在任意目录):  python e:/三国杀/自走棋助手/tests/smoke_scenario_fixes.py

覆盖(见 AUDIT.md 十六续):
  B1  _scenario_auto_bg("dismiss") 读 dismiss 自己的复选框(不再串到"锦囊"的值)
  B2  _scenario_collect() 下发结构带 eqLiuJian/equipPick + 桥端 updateScenarios 收下并同步顶层
  B3  _scenario_dup() 复制完落盘+推送(不再只刷列表)
  B4  _scenario_import_payload() 剧本级设置(triggerMode/runCount/speed/...)跟着导入
  B5  _scenario_run_thread() finally 清"正在跑"高亮(源码断言, 线程行为仅真机可验)
"""
import io
import os
import sys
import traceback

ROOT = r"e:/三国杀/自走棋助手"
sys.path.insert(0, ROOT)
os.chdir(ROOT)


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


class _RecordingOps:
    """记录 update_auto_* / push_scenarios 调用的 mock。

    connected() 默认 False(App 构造期按"未连接"走, 不触发真连逻辑);
    App 构造完成后由测试把 _on 翻 True, 让推送/启停的门卫放行。
    """
    def __init__(self, *a, **k):
        self.calls = []
        self._on = False
    def connected(self):
        return self._on
    def update_auto_follow_up(self, on):
        self.calls.append(("follow", on))
    def update_auto_spell(self, on):
        self.calls.append(("spell", on))
    def update_auto_dismiss(self, on, policy=None):
        self.calls.append(("dismiss", on))
    def push_scenarios(self, *a, **k):
        self.calls.append(("push_scenarios", a))
        return {}
    def __getattr__(self, name):
        return lambda *a, **k: None


def _case(cases, name, ok, detail=""):
    cases.append((name, "PASS" if ok else "FAIL", detail))
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", name, ("  → %s" % detail) if detail else ""))


def main():
    import tkinter as tk
    import core.bridge as _BR
    import core.hotkeys as _HK
    import core.remote_ops as _RO
    _BR.BridgeServer = _DummyServer
    _HK.HM = _DummyHM
    _RO.BridgeOps = _RecordingOps
    import ui.app as APP
    root = tk.Tk()
    root.withdraw()
    app = APP.App(root)
    app.ops._on = True          # 构造完再"连上", 让推送/启停门卫放行
    cases = []

    try:
        scb = getattr(app, "_scenario_builder", None)
        assert scb is not None, "拿不到 ScenarioTabBuilder(app._scenario_builder)"

        # ---------- B1: dismiss 读自己的复选框 ----------
        try:
            ops = app.ops
            # 三个框摆成互相不同的值: follow=True / spell=False / dismiss=True
            scb.auto_follow_bg_var.set(True)
            scb.auto_spell_bg_var.set(False)
            scb.auto_dismiss_bg_var.set(True)
            scb._scenario_auto_bg("dismiss")
            hit = [c for c in ops.calls if c[0] == "dismiss"]
            _case(cases, "B1 dismiss 读自己的框(dismiss=True)",
                  hit == [("dismiss", True)], "calls=%s" % hit)
            # 反向: dismiss 框=False 而锦囊框=True —— 旧 bug 会在这里推 True
            ops.calls.clear()
            scb.auto_dismiss_bg_var.set(False)
            scb.auto_spell_bg_var.set(True)
            scb._scenario_auto_bg("dismiss")
            hit = [c for c in ops.calls if c[0] == "dismiss"]
            _case(cases, "B1 dismiss=False 时不被锦囊框带偏",
                  hit == [("dismiss", False)], "calls=%s" % hit)
            ops.calls.clear()
            scb.auto_follow_bg_var.set(True)
            scb._scenario_auto_bg("follow")
            hit = [c for c in ops.calls if c[0] == "follow"]
            _case(cases, "B1 follow 路不受影响",
                  hit == [("follow", True)], "calls=%s" % hit)
        except Exception as e:
            _case(cases, "B1 _scenario_auto_bg", False, repr(e))

        # ---------- B2-①: collect 下发 eqLiuJian/equipPick ----------
        try:
            scb.scenario_scripts = [
                {"id": "t1", "name": "勾了五六剑", "steps": [], "eqLiuJian": True},
                {"id": "t2", "name": "没勾", "steps": []},
                {"id": "t3", "name": "带equipPick", "steps": [],
                 "equipPick": {"rejectTypes": [1], "preferNames": "玉玺"}},
            ]
            out = scb._scenario_collect()
            _case(cases, "B2 collect 带 eqLiuJian(True)",
                  out[0].get("eqLiuJian") is True, "=%s" % out[0].get("eqLiuJian"))
            _case(cases, "B2 collect eqLiuJian 默认 False",
                  out[1].get("eqLiuJian") is False, "=%s" % out[1].get("eqLiuJian"))
            _case(cases, "B2 collect 透传 equipPick",
                  out[2].get("equipPick") == {"rejectTypes": [1], "preferNames": "玉玺"},
                  "=%s" % out[2].get("equipPick"))
            _case(cases, "B2 collect 无 equipPick 的剧本不带该键",
                  "equipPick" not in out[0] and "equipPick" not in out[1], "")
        except Exception as e:
            _case(cases, "B2 _scenario_collect", False, repr(e))

        # ---------- B3: dup 落盘+推送 ----------
        try:
            ops = app.ops
            ops.calls.clear()
            scb.scenario_scripts = [
                {"id": "d1", "name": "原件", "steps": [{"op": "wait", "ms": 100}]},
            ]
            scb.scenario_cur_idx = 0
            before = len(scb.scenario_scripts)
            scb._scenario_dup()
            import time as _time
            _time.sleep(0.6)     # push 走 _run_bg 后台线程, 等它记上账
            pushed = any(c[0] == "push_scenarios" for c in ops.calls)
            saved = app.cfg.get("scenarios")
            _case(cases, "B3 dup 后剧本数 +1",
                  len(scb.scenario_scripts) == before + 1,
                  "%d→%d" % (before, len(scb.scenario_scripts)))
            _case(cases, "B3 dup 触发推送(push_scenarios)", pushed, "calls=%s" % ops.calls)
            _case(cases, "B3 dup 落盘进 cfg(副本在)",
                  any(str(s.get("name") or "").endswith("副本") for s in (saved or [])),
                  "cfg剧本=%s" % [s.get("name") for s in (saved or [])])
        except Exception as e:
            _case(cases, "B3 _scenario_dup", False, repr(e))

        # ---------- B4: 导入带剧本级设置 ----------
        try:
            import ui.scenario_tab as _ST
            _ask, _info, _warn = _ST.messagebox.askyesno, _ST.messagebox.showinfo, _ST.messagebox.showwarning
            _ST.messagebox.askyesno = lambda *a, **k: True
            _ST.messagebox.showinfo = lambda *a, **k: None
            _ST.messagebox.showwarning = lambda *a, **k: None
            try:
                scb.scenario_scripts = []
                payload = {"scenarios": [{
                    "id": "imp1", "name": "导入的循环刷牌", "hotkey": "ctrl+alt+9",
                    "triggerMode": "toggle", "runCount": 0, "speed": 2.5,
                    "maxDuration": 300, "onStart": "", "onEnd": "收尾剧本",
                    "enabled": False, "eqLiuJian": True,
                    "steps": [{"op": "wait", "ms": 100}],
                }]}
                scb._scenario_import_payload(payload, "测试")
                got = (scb.scenario_scripts or [{}])[0]
                _case(cases, "B4 导入保留 triggerMode=toggle",
                      got.get("triggerMode") == "toggle", "=%s" % got.get("triggerMode"))
                _case(cases, "B4 导入保留 runCount=0(一直跑)",
                      got.get("runCount") == 0, "=%s" % got.get("runCount"))
                _case(cases, "B4 导入保留 speed=2.5",
                      got.get("speed") == 2.5, "=%s" % got.get("speed"))
                _case(cases, "B4 导入保留 maxDuration=300",
                      got.get("maxDuration") == 300, "=%s" % got.get("maxDuration"))
                _case(cases, "B4 导入保留 onEnd",
                      got.get("onEnd") == "收尾剧本", "=%s" % got.get("onEnd"))
                _case(cases, "B4 导入保留 enabled=False",
                      got.get("enabled") is False, "=%s" % got.get("enabled"))
                _case(cases, "B4 导入保留 eqLiuJian=True",
                      got.get("eqLiuJian") is True, "=%s" % got.get("eqLiuJian"))
            finally:
                _ST.messagebox.askyesno, _ST.messagebox.showinfo, _ST.messagebox.showwarning = _ask, _info, _warn
        except Exception as e:
            _case(cases, "B4 _scenario_import_payload", False, repr(e))
            traceback.print_exc()

        # ---------- B5: run_thread finally 清高亮(源码断言) ----------
        try:
            import inspect
            src = inspect.getsource(type(scb)._scenario_run_thread)
            has_clear = "_scenario_hl_apply(-1, False)" in src
            _case(cases, "B5 run_thread finally 清运行高亮(源码)",
                  has_clear, "(线程内实际清屏仅真机可验)")
        except Exception as e:
            _case(cases, "B5 源码断言", False, repr(e))

        # ---------- B2-②/③: 桥端 updateScenarios 收下 + 同步顶层(文本断言) ----------
        try:
            js = io.open(os.path.join(ROOT, "bridge", "sgs_bridge.user.js"), encoding="utf-8").read()
            _case(cases, "B2桥 updateScenarios map 收 eqLiuJian",
                  "eqLiuJian: sc.eqLiuJian === true," in js, "")
            _case(cases, "B2桥 updateScenarios map 收 equipPick",
                  "equipPick: sc.equipPick || null," in js, "")
            _case(cases, "B2桥 同步顶层 S.eqLiuJian",
                  "S.eqLiuJian = _ljTop;" in js, "")
            _case(cases, "B2桥 同步顶层 S.equipPick",
                  "S.equipPick = _epTop;" in js, "")
            _case(cases, "B2桥 装备过滤器读顶层(未动)",
                  "window.sgsScenario.eqLiuJian ? { rejectTypes: [1], preferNames: \"玉玺,七星灯\" } : null" in js,
                  "")
            # bundle 同步对齐
            bd = io.open(os.path.join(ROOT, "bridge", "sgs_ext", "lib", "bridge.bundle.js"), encoding="utf-8").read()
            _case(cases, "B2桥 离线 bundle 已含同款修复",
                  "S.eqLiuJian = _ljTop;" in bd, "bundle 与源同步")
        except Exception as e:
            _case(cases, "B2桥 文本断言", False, repr(e))

    finally:
        try:
            root.destroy()
        except Exception:
            pass

    fails = [c for c in cases if c[1] != "PASS"]
    print("\n==== 剧本修复冒烟: %d/%d PASS ====" % (len(cases) - len(fails), len(cases)))
    if fails:
        for n, s, d in fails:
            print("FAIL: %s  %s" % (n, d))
        sys.exit(1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
