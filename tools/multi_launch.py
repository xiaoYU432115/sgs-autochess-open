# -*- coding: utf-8 -*-
"""多开启动器: 一次起 N 个助手实例, 各带各的端口与数据目录。

用法(在任意目录都行, 脚本自己认路):
    python tools/multi_launch.py 5              # 起 5 个: 1 号带界面, 2~5 后台
    python tools/multi_launch.py 5 --all-ui     # 5 个都带界面(吃资源, 一般不需要)
    python tools/multi_launch.py 3 --start 2    # 只起 2/3/4 号(前面已开着的跳过)
    python tools/multi_launch.py 2 --with-edge  # 顺带开 2 个独立 Edge(各自 profile)
    python tools/multi_launch.py 5 --base-port 9000   # 端口从 9000 起

端口分配:  第 n 个实例 = 8765 + (n-1) * 20
    1号=8765  2号=8785  3号=8805  4号=8825  5号=8845  6号=8865 ...
    为什么间隔 20: 每个实例除了桥接端口, 还要留脚本服务(8766/8767...)那几个
    附属端口的档位(core/config.slot_ports 按 20 一格平移), 隔开才不会互相复用。
    1 号特意用 8765 —— 你现在浏览器里设的就是它, 单独开 1 号跟以前完全一样。

数据目录:  助手根/instances/<N>号机/  里面是各自的 config.json 与 data/
    (首次启动会从助手根 config.json 复制一份当初始配置, 之后各管各的)
"""
import argparse
import os
import subprocess
import sys
import time
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(ROOT, "main.py")
INST_DIR = os.path.join(ROOT, "instances")
sys.path.insert(0, ROOT)
from core.instances import port_bindable, find_free_port, list_running, port_busy   # noqa: E402


def _set_port_in_cfg(inst_dir, port):
    """直接改某个实例 config.json 里的 bridge.port(原子写), 不动其它键。"""
    import json as _j
    p = os.path.join(inst_dir, "config.json")
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = _j.load(f)
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    data.setdefault("bridge", {})["port"] = int(port)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        _j.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
EXT_DIR = os.path.join(ROOT, "bridge", "sgs_ext")
GAME_URL = "https://web.sanguosha.com/x/pc/index.php?lf=0"


def port_of(idx, base=8765, step=20):
    return base + (idx - 1) * step


def ping(port, timeout=1.5):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/ping" % port, timeout=timeout) as f:
            return f.read(200).decode("utf-8", "ignore")
    except Exception:
        return ""


def wait_up(port, tries=60):
    """等实例把端口真的监听起来(助手是 Laya 大包, 冷启动要几秒)。"""
    for _ in range(tries):
        if ping(port):
            return True
        time.sleep(0.5)
    return False


def launch(idx, port, ui, base=8765, step=20, main_instance=False):
    """起第 idx 个实例。

    · 端口避让: 端口被【任何东西】占着(不一定是助手)就自动往后找一个空的 ——
      以前的表现是助手起来后桥接绑不上, 启动器这边只显示"超时"。
    · 热键归位: 只有 main_instance(第一个)接管全局热键, 其余不带界面的自不必说,
      带界面的也一律 --no-hotkeys(否则按一次 F6, 所有实例一起动)。
    """
    name = "%d号机" % idx
    d = os.path.join(INST_DIR, name)
    os.makedirs(d, exist_ok=True)
    # 端口被占 → 自动避让(顺手把该实例 config.json 里的端口也改掉,
    #   不然它起来后会按老端口去撞一次)。这里直接改文件, 不走 core.config ——
    #   启动器进程自己不该被实例的 env 污染(CONFIG_PATH 是 import 时定死的)。
    if not port_bindable(port):
        newp = find_free_port(port, step)
        print("  [避让] 端口 %d 已被占用, %s 改用 %d" % (port, name, newp))
        try:
            _set_port_in_cfg(d, newp)
        except Exception as e:
            print("  [避让] 写入 %s 的 config 失败(它仍会去撞 %d): %s" % (name, port, e))
        port = newp
    log = open(os.path.join(d, "console.log"), "a", encoding="utf-8", errors="replace")
    argv = [sys.executable, MAIN, "--profile", name, "--port", str(port),
            "--slot", str((port - base) // step)]
    if not ui:
        argv.append("--no-ui")
    elif not main_instance:
        argv.append("--no-hotkeys")     # v4.161★ 热键只留一个实例接管
    # 后台实例的输出留到 console.log, 不弹黑窗口(5 个黑窗口很吵)
    p = subprocess.Popen(argv, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    return name, d, p, port      # port 可能被避让改过, 回传给调用方显示


def launch_edge(idx, port):
    """给第 idx 个实例开一个独立 Edge(各用各的 profile, localStorage 互不干扰)。"""
    if not os.path.exists(EDGE):
        return None, "找不到 Edge: %s" % EDGE
    prof = os.path.join(INST_DIR, "edge_%d号机" % idx)
    os.makedirs(prof, exist_ok=True)
    argv = [EDGE, "--user-data-dir=" + prof,
            # v4.161★ 全新 profile 首次启动会弹 Edge 的欢迎/登录/导入引导, 覆盖在最上层 ——
            #   用户看到的就是"一打开就全屏、要登录账号、里面的东西点不动"。
            #   这几个开关把它关掉, 打开就是干净的浏览器, 直接可用。
            #   不启用 --remote-debugging-port: 它会让 Edge 顶部常驻"正由自动测试软件控制"
            #    横幅、看着像坏程序; 而助手与浏览器靠 localhost HTTP 通信, 用不到 CDP。
            "--no-first-run", "--no-default-browser-check",
            "--disable-signin-promo", "--disable-sync-promo",
            # 新 profile 默认最大化(看着像全屏)。这里给固定尺寸 + 按实例错开摆位:
            #   多个 Edge 不叠在一起, 也不占满屏挡住助手窗口。
            "--window-size=1280,820",
            "--window-position=%d,%d" % (120 + (idx - 1) * 70, 80 + (idx - 1) * 50),
            ]
    if os.path.isdir(EXT_DIR):
        argv.append("--load-extension=" + EXT_DIR)
    # 先打开本实例的 install 页: 它会把端口写进这个 profile 的 localStorage,
    # 然后自动跳去装脚本 —— 装完再开游戏页, 脚本就知道该连哪个端口。
    argv.append("http://127.0.0.1:%d/install.html" % port)
    try:
        p = subprocess.Popen(argv)
        return p, None
    except Exception as e:
        return None, str(e)


def _inst_idx_from_dir(name):
    """'3号机' / 'edge_3号机' -> 3 ; 其它 -> None"""
    import re
    m = re.search(r"(\d+)号机", name)
    return int(m.group(1)) if m else None


def cleanup_zombies(instances_dir, planned):
    """v4.161★ 启动前清理异常退出残留的目录(宁漏勿误删):
      · edge_N号机 浏览器 profile: 不在本次计划内 → 删(每次多开都重生成, 留着是无用堆积)
      · N号机 实例数据: 不在计划内 + 端口空闲 + 注册表无活记录 + 目录无 data/ → 删
        有 data/(可能含卡组/设置)的一律留着, 绝不误删。
    返回清理掉的目录名列表(供打印)。
    """
    removed = []
    try:
        names = os.listdir(instances_dir)
    except Exception:
        return removed
    for nm in names:
        dp = os.path.join(instances_dir, nm)
        if not os.path.isdir(dp):
            continue
        idx = _inst_idx_from_dir(nm)
        if idx is not None and idx in planned:
            continue  # 本次要用的, 保留(launch 会复用)
        if nm.startswith("edge_"):
            try:
                import shutil
                shutil.rmtree(dp)
                removed.append(nm)
            except Exception:
                pass
        elif nm.endswith("号机"):
            cfg_port = 0
            try:
                import json as _j
                _c = _j.load(open(os.path.join(dp, "config.json"), encoding="utf-8"))
                cfg_port = int(_c.get("bridge", {}).get("port") or 0)
            except Exception:
                pass
            if os.path.isdir(os.path.join(dp, "data")):
                continue  # 有用户数据, 绝不删
            if cfg_port and port_busy(cfg_port):
                continue
            live = False
            try:
                for _r in list_running():
                    if _r.get("dir") == dp or (cfg_port and int(_r.get("port") or 0) == cfg_port):
                        live = True
                        break
            except Exception:
                pass
            if live:
                continue
            try:
                import shutil
                shutil.rmtree(dp)
                removed.append(nm)
            except Exception:
                pass
    return removed


def recommend_base_port(step=20, default=8765):
    """v4.161★ 端口智能推荐: 没显式指定 --base-port 时, 按当前已用端口
    给下一波多开一个不撞车的起始端口(取活实例最高端口, 向上对齐 step)。
    无活实例时回落到 default(8765), 兼容"第一次开"。
    """
    max_used = default - step
    try:
        for _r in list_running():
            _p = int(_r.get("port") or 0)
            if _p > max_used:
                max_used = _p
    except Exception:
        pass
    cand = ((max_used // step) + 1) * step + (default % step)
    while cand < 1024 or not port_bindable(cand):
        cand += step
    return cand


def main(argv=None):
    ap = argparse.ArgumentParser(description="三国杀自走棋助手 · 多开启动器")
    ap.add_argument("count", nargs="?", type=int, default=1, help="起几个实例(默认 1)")
    ap.add_argument("--start", type=int, default=1, help="从第几号开始(默认 1)")
    ap.add_argument("--base-port", type=int, default=None,
                   help="起始端口(默认自动: 避开已用端口, 多波多开不撞车)")
    ap.add_argument("--step", type=int, default=20, help="端口间隔(默认 20)")
    ap.add_argument("--all-ui", action="store_true", help="全部带界面(默认只 1 号带)")
    ap.add_argument("--no-ui", action="store_true", help="全部后台(一个界面都不开)")
    ap.add_argument("--with-edge", action="store_true", help="顺带给每个实例开一个独立 Edge")
    ap.add_argument("--no-wait", action="store_true", help="不等端口起来就返回")
    a = ap.parse_args(argv)

    if a.count < 1 or a.count > 20:
        print("count 得在 1-20 之间(20 个以上先想想机器扛不扛得住)")
        return 2

    # 端口智能推荐: 没显式指定就按已用端口给下一个空闲档(多波多开不撞车)
    if a.base_port is None:
        a.base_port = recommend_base_port(a.step)
    planned = set(range(a.start, a.start + a.count))
    # 启动前清理异常退出残留的僵尸目录(宁漏勿误删, 有 data/ 的实例数据一律保留)
    removed = cleanup_zombies(INST_DIR, planned)
    if removed:
        print("  清理上次异常残留目录: " + ", ".join(removed))
    # 独立 Edge 醒目提示: 找不到就先说清楚, 别等起完才报每个都失败
    if a.with_edge and not os.path.exists(EDGE):
        print("!" * 70)
        print("  ⚠ 你选了独立 Edge, 但找不到 Edge: %s" % EDGE)
        print("  实例照样起, 但没有配套浏览器; 装好 Edge 再开, 或去掉独立 Edge 选项。")
        print("!" * 70)

    print("=" * 68)
    print(" 多开启动: %d 个实例 (%d号机 ~ %d号机)" % (a.count, a.start, a.start + a.count - 1))
    print(" 数据目录: %s" % INST_DIR)
    print("=" * 68)

    started = []
    for i in range(a.start, a.start + a.count):
        port = port_of(i, a.base_port, a.step)
        if ping(port):
            print("  [跳过] %d号机 端口 %d 已经有服务在跑了(可能是上次没关)" % (i, port))
            continue
        ui = (not a.no_ui) and (a.all_ui or i == a.start)
        name, d, p, port = launch(i, port, ui, a.base_port, a.step,
                                  main_instance=(i == a.start))
        started.append((i, name, port, d, p, ui))

    if not started:
        print("没有新起的实例(都在跑着了)")
        return 0

    if not a.no_wait:
        print("\n等待各实例就位…")
        for i, name, port, d, p, ui in started:
            up = wait_up(port)
            print("  [%s] %-5s 端口 %-5d %s  pid=%d%s"
                  % ("OK" if up else "超时", name, port,
                     "带界面" if ui else "后台  ", p.pid,
                     "" if up else "  → 看日志: %s" % os.path.join(d, "console.log")))
            if up and a.with_edge:
                ep, err = launch_edge(i, port)
                if err:
                    print("      [NG] Edge 没起来: %s" % err)
                else:
                    print("      [OK] 已开独立 Edge(profile: instances/edge_%s) → 先装脚本再进游戏" % name)

    print("\n" + "=" * 68)
    print(" 各实例地址(浏览器里装脚本/看状态用, 别串):")
    for i, name, port, d, p, ui in started:
        print("   %-5s http://127.0.0.1:%d/install.html" % (name, port))
    print("=" * 68)
    print(" 关掉: 带界面的直接关窗口; 后台实例用任务管理器结束对应 pid(上面列出了)。")
    print(" 排障: 每个实例的控制台输出在 instances/<名字>/console.log")
    return 0


if __name__ == "__main__":
    sys.exit(main())
