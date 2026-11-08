# -*- coding: utf-8 -*-
"""P2 功能可用性冒烟 · 构造期 (App 全量 Tab 构建)。

不依赖 pytest。用法(在 e:/三国杀 下):  python 自走棋助手/tests/smoke_app.py

杠杆: App.__init__ 会构建全部 Tab(builder.build())。构造一次 = 跑遍所有页签的构建逻辑,
正好补上"导入 OK 但构造就崩"这一空白层。

环境隔离:
  - mock core.bridge.BridgeServer: 避免 start() 绑端口(端口占用会污染 Tab 覆盖, 且留进程)。
  - mock core.hotkeys.HM: 避免 win32 全局钩子副作用(构造期不需要测热键)。
两者都在 import ui.app 之前 patch, 因 app.py 在模块顶部 `from core.bridge import ...` /
`from core.hotkeys import HM` 已经把引用拷走了。
"""
import os
import sys
import tkinter as tk
import traceback

ROOT = r"e:/三国杀/自走棋助手"
sys.path.insert(0, ROOT)
os.chdir(ROOT)


class _DummyServer:
    def __init__(self, *a, **k):
        self.port = 0
    def start(self):
        pass
    def stop(self):
        pass
    def shutdown(self):
        pass


class _DummyHM:
    root = None
    log = None
    def bind(self, *a, **k):
        pass
    def start(self):
        pass
    def stop(self):
        pass
    def register(self, *a, **k):
        pass


def main():
    # 在 import ui.app 之前 patch(否则拿不到我们替换的引用)
    import core.bridge as _BR
    import core.hotkeys as _HK
    _BR.BridgeServer = _DummyServer
    _HK.HM = _DummyHM()

    import ui.app as APP

    root = tk.Tk()
    root.withdraw()
    app = None
    try:
        app = APP.App(root)
        # 构造成功即代表全部 Tab 的 build() 没在 __init__ 崩
        built = [a for a in dir(app) if a.startswith("_") and a.endswith("_builder")]
        print("  [PASS] App() 构造成功, 已构建 builder 数: %d" % len(built))
        print("         builders: %s" % ", ".join(sorted(built)))
        return 0
    except Exception as e:
        tb = traceback.format_exc()
        # 从 traceback 里挑出 app.py 里具体是哪段(_build_*_tab / builder.build)崩的
        loc = ""
        for ln in tb.splitlines():
            if "app.py" in ln and "_build" in ln:
                loc = ln.strip()
                break
        print("  [FAIL] App() 构造抛异常: %s: %s" % (type(e).__name__, e))
        if loc:
            print("        崩在: %s" % loc)
        print("  ----- traceback 末 12 行 -----")
        for ln in tb.strip().splitlines()[-12:]:
            print("      " + ln)
        return 1
    finally:
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
        # 强行退出: 防止任何非 daemon 线程(钩子/日志泵)挂住脚本
        os._exit(0)


if __name__ == "__main__":
    sys.exit(main())
