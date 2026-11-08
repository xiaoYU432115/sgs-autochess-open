# -*- coding: utf-8 -*-
"""P2 功能可用性冒烟 · UI 层导入冒烟 (headless)。

不依赖 pytest。用法(在 e:/三国杀 下):  python 自走棋助手/tests/smoke_ui.py

目标: 抓出 "UI 页签看着在、import 就崩" 的死模块 —— pyflakes 抓不到这类(运行时/依赖)问题。
UI 模块常需 Tk 显示; 本机 Windows 有捆绑的 Tcl/Tk, import tkinter 不建窗口, 但若模块在
import 期就 Tk()/mainloop 会失败。首轮先裸导入看真实报错, 再按需 mock。
"""
import glob
import importlib
import os
import sys
import traceback

ROOT = r"e:/三国杀/自走棋助手"
sys.path.insert(0, ROOT)
os.chdir(ROOT)


def main():
    ui_files = sorted(glob.glob(os.path.join(ROOT, "ui", "*.py")))
    mods = []
    for fp in ui_files:
        base = os.path.basename(fp)[:-3]
        if base == "__init__":
            continue
        mods.append(base)

    print("=== UI 层导入冒烟 (%d 个模块) ===" % len(mods))
    ok, fail = [], []
    for m in mods:
        try:
            importlib.import_module("ui." + m)
            ok.append(m)
        except Exception as e:
            tb = traceback.format_exc(limit=2)
            fail.append((m, type(e).__name__, str(e).splitlines()[0] if str(e) else "", tb))

    for m in ok:
        print("  [OK]   ui.%s" % m)
    for m, et, em, _ in fail:
        print("  [FAIL] ui.%s  ->  %s: %s" % (m, et, em))
    print("\n  导入: %d 通过 / %d 失败" % (len(ok), len(fail)))

    if fail:
        print("\n--- 失败详情(前 3 个, 取末 6 行 traceback) ---")
        for m, et, em, tb in fail[:3]:
            lines = tb.strip().splitlines()[-6:]
            print("  [%s] %s: %s" % (m, et, em))
            for ln in lines:
                print("      " + ln)
    return len(fail)


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
