# -*- coding: utf-8 -*-
"""三国杀自走棋助手入口：Python大窗口(8页签) + 游戏内注入小抄悬浮窗(全部功能注入实现)

v4.160★ 多开支持(命令行):
    python main.py                          # 老样子: 单实例, 端口 8765, 助手根的 config/data
    python main.py --port 9876              # 只换端口(共用旧的数据目录)
    python main.py --profile 2号机 --port 8785   # 独立实例: 自己的 config.json + data/
    python main.py --profile 3号机 --port 8805 --no-ui   # 后台跑, 不弹窗口(5 开推荐)
    python 多开.bat 5                        # 一次起 5 个实例(见 多开说明.md)

  端口与实例的关系: 一个端口 = 一个助手实例 = 一个浏览器(profile)。
    · 不能两个浏览器共用一个端口: 桥接状态是单例, 两边会互相覆盖状态、互抢指令。
    · 每个实例自己的 config.json / data/ 互不干扰(落在 助手根/instances/<名字>/)。
"""
import os
import sys

# v4.24★ 路径自举 + 消除隐式相对导入警告(basedpyright reportImplicitRelativeImport)
#   ① 之前直接 `from ui.app import main`: 只在本文件所在目录恰好是 working dir 时才能跑,
#      从别处 `python 自走棋助手\main.py` 或双击运行会 ModuleNotFoundError。
#      现在自己把所在目录放进 sys.path, 与 core/ocr_ops.py 等模块的做法一致。
#   ② `ui` 是正规包(ui/__init__.py 存在), 这里就是绝对导入, 显式声明后静态检查不会误判。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _parse_args(argv):
    """解析命令行。

    为什么不用 argparse 的 required/互斥那些: 这些参数全是"有就用, 没有就老行为",
    而且必须在 import core 之前算完(它们是通过环境变量传给 core.config 的)。
    """
    import argparse
    ap = argparse.ArgumentParser(
        prog="main.py",
        description="三国杀自走棋助手(可多开)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="多开示例:  python main.py --profile 2号机 --port 8785 --no-ui")
    ap.add_argument("--port", type=int, default=None,
                    help="桥接端口(默认读 config.json 的 bridge.port, 通常 8765)。多开时每个实例必须不同")
    ap.add_argument("--profile", "--name", dest="profile", default=None,
                    help="实例名 → 数据目录 助手根/instances/<名字>/ (config.json 与 data/ 都在里面)")
    ap.add_argument("--instance-dir", default=None,
                    help="直接指定实例目录(优先于 --profile)")
    ap.add_argument("--slot", type=int, default=None,
                    help="附属服务(脚本服务/图片服务)的端口档位。不填则按端口自动推算")
    ap.add_argument("--no-ui", action="store_true",
                    help="不弹窗口, 只跑桥接服务(多开时给不需要操作的实例用)")
    ap.add_argument("--no-hotkeys", action="store_true",
                    help="本实例不接管全局热键(F5/F6/F8/Ctrl+Alt+B…)。多开时只让一个实例接管, "
                         "否则按一次键所有实例会一起动")
    return ap.parse_args(argv)


def main(argv=None):
    args = _parse_args(sys.argv[1:] if argv is None else list(argv))

    root = os.path.dirname(os.path.abspath(__file__))

    # ---- 实例目录(必须在 import 任何 core 模块之前设好环境变量) ----
    #   core/config.py 在【导入时】就按这些环境变量定死 DATA_DIR / CONFIG_PATH,
    #   晚一步设置就等于没设 —— 这是多开隔离能不能生效的关键。
    inst_dir = args.instance_dir
    if not inst_dir and args.profile:
        inst_dir = os.path.join(root, "instances", str(args.profile))
    if inst_dir:
        os.environ["SGS_INSTANCE_DIR"] = os.path.abspath(inst_dir)
        os.environ["SGS_INSTANCE_NAME"] = args.profile or os.path.basename(
            os.path.abspath(inst_dir))

    # ---- 端口 ----
    port = args.port
    if port is not None:
        if not (1024 <= port <= 65535):
            print("[启动] --port 必须在 1024-65535 之间, 收到 %d" % port)
            return 2
        os.environ["SGS_BRIDGE_PORT"] = str(port)
        # v4.160★ 命令行给的端口【就是这个实例的端口】: ① 落盘 ② 从 argv 摘掉。
        #   ① 落盘: 界面上看到的端口才是真的, 重启后也不会跳回 config 里的旧值。
        #   ② 摘掉: 不摘的话, 在界面里改端口 → os.execv 重启 → 命令行那个旧 --port
        #      又把新端口盖回去 —— 表现就是"改了端口没生效"。摘掉后重启一律读
        #      config.json(而 config 刚被写成了新端口), 两边一致。
        try:
            from core.config import set_bridge_port
            set_bridge_port(port)
        except Exception as e:
            print("[启动] 端口写入 config 失败(本次仍生效): %s" % e)
        # ③ 还要把环境变量【删掉】: os.execv 重启会继承环境, 带着它就等于把端口
        #    钉死在命令行那个值上 —— 界面里改端口 → 重启 → 又被这个旧值盖回去,
        #    表现是"改了端口, 重启后还是老样子"(实测就是这样, 别再踩)。
        #    端口已经落盘了, 子进程从 config 读即可。
        os.environ.pop("SGS_BRIDGE_PORT", None)
        try:
            av = sys.argv[:]
            while "--port" in av:
                i = av.index("--port")
                del av[i:i + 2]
            sys.argv[:] = av
        except Exception:
            pass

    # ---- 附属服务档位: 没显式给就按端口推算(间隔 20 一格), 免得各实例抢同一批端口 ----
    slot = args.slot
    if slot is None:
        slot = 0
        if port is not None and port >= 8765:
            slot = (port - 8765) // 20
    os.environ["SGS_INSTANCE_SLOT"] = str(max(0, int(slot)))

    # 控制台编码兜底(Windows 上中文提示/端口告警会用到)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    # v4.161★ 本实例是否接管全局热键。多开时只让"主实例"(第一个)接管,
    #   否则按一次 F6/Ctrl+Alt+B, 所有带界面的实例会一起执行(各自操作自己的号)。
    if args.no_hotkeys:
        os.environ["SGS_NO_HOTKEYS"] = "1"

    from core.config import (CONFIG, INSTANCE_DIR, INSTANCE_NAME, INSTANCE_SLOT,
                             is_multi_instance, set_bridge_port)
    from core import instances as _INST

    port = int(CONFIG.get("bridge", {}).get("port") or 8765)

    # ---- v4.161★ 端口撞车: 自动避让 / (单实例时) 弹窗让你挑 ----
    if _INST.port_busy(port):
        other = _INST.ping(port)
        if other:
            # 是【另一个助手】在跑
            if is_multi_instance():
                # 多开模式不打断: 自动往后找一个空端口(别人占着的那个保持不动)
                newp = _INST.find_free_port(port + 20)
                print("[多开] 端口 %d 上已经有一个助手(pid=%s), 本实例改用 %d"
                      % (port, other.get("pid"), newp))
                set_bridge_port(newp)
                port = newp
            else:
                # 单实例模式(多半是双击 main.py 又点了一次): 让你挑, 别闷头开第二个
                return _ask_when_already_running(port, other, root)
        else:
            # 被别的软件占了 → 直接避让, 不然桥接绑不上(以前的表现是"静默失联")
            newp = _INST.find_free_port(port + 20)
            print("[启动] 端口 %d 被别的程序占着(不是助手), 本实例改用 %d" % (port, newp))
            set_bridge_port(newp)
            port = newp

    # ---- 登记自己(多开面板 / 停止 / 切窗口 都要用) ----
    _INST.register(port, name=INSTANCE_NAME, ui=not args.no_ui,
                   inst_dir=INSTANCE_DIR, argv=sys.argv)
    import atexit
    atexit.register(_INST.unregister, port)

    if args.no_ui:
        # 不 import ui.app —— 免得把 tkinter 拉起来
        print("[多开] 实例目录: %s" % INSTANCE_DIR)
        print("[多开] 实例名:   %s   端口档位: %d" % (INSTANCE_NAME or "(单实例)", INSTANCE_SLOT))
        from core import headless
        return headless.run(port, log=print)

    from ui.app import main as ui_main  # pyright: ignore[reportImplicitRelativeImport]
    return ui_main()


def _ask_when_already_running(port, other, root):
    """v4.161★ 单实例模式下端口上已经有一个助手: 弹窗让你挑, 而不是闷头开第二个。

    以前的后果: 第二个实例绑不上端口 → 静默退回 → 浏览器推的数据全落到第一个
    实例上, 表现为"新开的窗口一直连不上 / 读到的是别人的对局"。
    """
    try:
        import tkinter as _tk
        from tkinter import messagebox as _mb
        r = _tk.Tk()
        r.withdraw()
        who = other.get("pid")
        ans = _mb.askyesnocancel(
            "助手已经在运行",
            "端口 %d 上已经有一个助手在跑了(pid=%s)。\n\n"
            "  是   → 再开【第二个】(自动用空闲端口 + 独立数据目录)\n"
            "  否   → 切到已经开着那个窗口\n"
            "  取消 → 什么都不做\n\n"
            "(多开请用 多开.bat, 一次起好几个更省事)" % (port, who))
        try:
            r.destroy()
        except Exception:
            pass
    except Exception:
        ans = None

    if ans is True:          # 开第二个
        from core import instances as _INST
        import subprocess as _sp
        newp = _INST.find_free_port(port + 20)
        idx = max(2, (newp - 8765) // 20 + 1)
        _sp.Popen([sys.executable, os.path.join(root, "main.py"),
                   "--profile", "%d号机" % idx, "--port", str(newp)],
                  cwd=root)
        return 0
    if ans is False:         # 切到已开的
        from core import instances as _INST
        if not _INST.focus_instance(port):
            print("[启动] 没能把已开的窗口提到前台(它可能没在带界面模式跑)")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
