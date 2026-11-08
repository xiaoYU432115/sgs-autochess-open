# -*- coding: utf-8 -*-
"""v4.161★ 实例注册表 —— 多开面板 / 端口自动避让 / "切到已开的助手" 都靠它。

为什么需要一个注册表: 多开之后,"现在有几个助手在跑、各自哪个端口、哪个 pid、
是带界面还是后台、窗口句柄是多少"这些信息【没有任何地方记录】。没有它:
  · 想关掉某个后台实例只能去任务管理器按 pid 一个个找;
  · 想"切到已开的那个窗口"做不到(不知道句柄);
  · 端口撞车时不知道占用者是"另一个助手"还是别的软件, 只能盲报错。

注册表放在【助手根】的 .running/ 下 —— 注意不是 core/config.runtime_path()
(那个是按实例隔离的, 放那儿就又变成各存各的了)。每个实例一个 <端口>.json。
"""
import json
import os
import socket
import subprocess
import time

from core.config import BASE_DIR

REG_DIR = os.path.join(BASE_DIR, ".running")


def _path(port):
    return os.path.join(REG_DIR, "%d.json" % int(port))


def _atomic_write(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        pass


def register(port, name="", pid=None, argv=None, ui=True, inst_dir="", cwd=""):
    """实例启动时登记自己。同端口重复登记 = 覆盖(重启后 pid/argv 会变)。"""
    import sys
    _pid = int(pid or os.getpid())
    # v4.161★ 先清掉【同一个 pid 在别的端口】上的旧登记。
    #   为什么必须清: os.execv 重启(改端口 / 自动避让 / /restart)后 pid 不变、
    #   端口却换了, 旧的那条永远不会被 atexit 注销(execv 不走 atexit), 也不
    #   满足"进程已死"的清理条件 —— 于是面板里留下一条永远显示"已退出"的僵尸,
    #   "停掉某个实例"也可能停错方向。
    try:
        for _fn in os.listdir(REG_DIR):
            if not _fn.endswith(".json"):
                continue
            _fp = os.path.join(REG_DIR, _fn)
            try:
                _old = json.load(open(_fp, encoding="utf-8"))
            except Exception:
                continue
            if int(_old.get("pid") or 0) == _pid and int(_old.get("port") or 0) != int(port):
                try:
                    os.remove(_fp)
                except Exception:
                    pass
    except Exception:
        pass
    rec = {
        "port": int(port),
        "pid": _pid,
        "name": name or "",
        "ui": bool(ui),
        "dir": inst_dir or "",
        "argv": list(argv or sys.argv),
        "cwd": cwd or os.getcwd(),
        "hwnd": 0,
        "started": int(time.time()),
    }
    # 保留同一端口上一次登记的 hwnd(重启后窗口要重建, 但先不丢旧值)
    try:
        old = json.load(open(_path(port), encoding="utf-8"))
        rec["hwnd"] = int(old.get("hwnd") or 0)
    except Exception:
        pass
    _atomic_write(_path(port), rec)
    return rec


def unregister(port):
    try:
        os.remove(_path(port))
    except Exception:
        pass


def set_hwnd(port, hwnd):
    """带界面的实例起来后把自己的窗口句柄补登记上(供"切到已开的助手")。"""
    try:
        rec = json.load(open(_path(port), encoding="utf-8"))
    except Exception:
        rec = {}
    if not rec:
        return
    rec["hwnd"] = int(hwnd or 0)
    _atomic_write(_path(port), rec)


def _pid_alive(pid):
    """Windows 上判断 pid 是否还在 —— os.kill(pid,0) 在 Windows 语义不一样,
    用 OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION) 最稳。"""
    if not pid:
        return False
    try:
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if h:
            ctypes.windll.kernel32.CloseHandle(h)
            return True
        return False
    except Exception:
        return False


def ping(port, timeout=1.2):
    """探这个端口上是不是一个助手(能 /ping 通)。返回 dict 或 None。"""
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/ping" % int(port),
                                    timeout=timeout) as f:
            d = json.loads(f.read(600).decode("utf-8", "ignore"))
            return d if isinstance(d, dict) else {}
    except Exception:
        return None


def port_busy(port):
    """端口是不是被【任何东西】占着(不一定是助手)。"""
    s = socket.socket()
    s.settimeout(0.4)
    try:
        return s.connect_ex(("127.0.0.1", int(port))) == 0
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def port_bindable(port):
    """这个端口现在能不能被我们 bind(没人占 = True)。"""
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", int(port)))
        return True
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def find_free_port(base, step=20, tries=50):
    """从 base 起找一个能 bind 的端口(按 step 递增)。给"端口被占自动避让"用。"""
    for i in range(tries):
        p = int(base) + i * int(step)
        if 1024 <= p <= 65535 and port_bindable(p):
            return p
    return int(base)


def list_running(include_dead=False):
    """列出登记过的实例, 顺带探活。死掉的记录会被清掉(进程被强杀时来不及注销)。"""
    out = []
    try:
        files = sorted(os.listdir(REG_DIR))
    except Exception:
        files = []
    for fn in files:
        if not fn.endswith(".json"):
            continue
        try:
            rec = json.load(open(os.path.join(REG_DIR, fn), encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(rec, dict) or "port" not in rec:
            continue
        p = int(rec["port"])
        live = ping(p)
        pid_ok = _pid_alive(rec.get("pid"))
        rec["alive"] = bool(live)
        rec["pid_alive"] = pid_ok
        rec["connected"] = bool(live and live.get("connected"))
        rec["last_push_ago"] = (live or {}).get("last_push_ago_s")
        if not live and not pid_ok:
            # 进程没了 → 清掉, 免得面板里越积越多僵尸
            try:
                os.remove(os.path.join(REG_DIR, fn))
            except Exception:
                pass
            if not include_dead:
                continue
        out.append(rec)
    out.sort(key=lambda r: r.get("port", 0))
    return out


def stop_instance(port, force=True):
    """停掉登记在该端口上的实例(用 pid 精确杀, 不按端口扫)。"""
    try:
        rec = json.load(open(_path(port), encoding="utf-8"))
    except Exception:
        return False, "没有这个实例的登记: %s" % port
    pid = int(rec.get("pid") or 0)
    if not pid or not _pid_alive(pid):
        unregister(port)
        return True, "进程已经不在了"
    try:
        cmd = ["taskkill", "/PID", str(pid), "/T"]
        if force:
            cmd.append("/F")
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=10)
        ok = (r.returncode == 0)
    except Exception as e:
        return False, str(e)
    if ok:
        unregister(port)
    return ok, ("" if ok else "taskkill 失败")


def relaunch(rec, drop_no_ui=True):
    """按登记时的 argv 重开一个实例(后台实例可以借此"转成带界面")。"""
    argv = list(rec.get("argv") or [])
    if not argv:
        return None
    if drop_no_ui:
        argv = [a for a in argv if a != "--no-ui"]
    cwd = rec.get("cwd") or BASE_DIR
    try:
        p = subprocess.Popen(argv, cwd=cwd)
        return p.pid
    except Exception:
        return None


def focus_instance(port):
    """把已开着的那个助手窗口提到前台(供"切到已开的助手"用)。"""
    hwnd = 0
    try:
        rec = json.load(open(_path(port), encoding="utf-8"))
        hwnd = int(rec.get("hwnd") or 0)
        pid = int(rec.get("pid") or 0)
    except Exception:
        return False
    if not hwnd and pid:
        hwnd = _find_main_hwnd(pid)
    if not hwnd:
        return False
    try:
        import ctypes
        u = ctypes.windll.user32
        if u.IsIconic(hwnd):
            u.ShowWindow(hwnd, 9)      # SW_RESTORE
        u.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


def _find_main_hwnd(pid):
    """按 pid 找它的主窗口句柄(注册表里没记 hwnd 时的兜底)。"""
    try:
        import ctypes
        u = ctypes.windll.user32
        found = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def _cb(hwnd, _lp):
            if not u.IsWindowVisible(hwnd):
                return True
            p = ctypes.c_ulong()
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
            if p.value == int(pid):
                found.append(hwnd)
                return False
            return True

        u.EnumWindows(_cb, 0)
        return int(found[0]) if found else 0
    except Exception:
        return 0


def script_rev(port):
    """取这个实例当前下发的脚本版本(/ext/bridge.meta 的版本卡, 很轻)。"""
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/ext/bridge.meta" % int(port),
                                    timeout=1.5) as f:
            d = json.loads(f.read(3000).decode("utf-8", "ignore"))
            return str(d.get("rev") or d.get("version") or "")
    except Exception:
        return ""
