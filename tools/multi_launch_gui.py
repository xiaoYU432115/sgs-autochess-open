# -*- coding: utf-8 -*-
"""多开启动器 · 图形界面版(双击 `多开.bat` 不带参数就会开这个)。

给不想敲命令行的人用: 填几个数 → 点启动 → 各实例自己起来, 端口/数据目录全自动。

用法:
    双击 多开.bat                        → 弹出本窗口
    python tools/multi_launch_gui.py     → 同上(想看控制台输出就用这个)

说明: 命令行版 tools/multi_launch.py 仍然保留(多开.bat 3 这种带参数时走它)。
"""
import io
import os
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
sys.path.insert(0, ROOT)
sys.path.insert(0, TOOLS)

# pythonw 启动(双击)时 sys.stdout 是 None —— 底下任何 print 都会炸。先摆平,
# 免得启动器自己还没起来就崩在一个无关紧要的日志上。
if sys.stdout is None:
    sys.stdout = io.StringIO()
if sys.stderr is None:
    sys.stderr = io.StringIO()

import multi_launch as ML  # noqa: E402  (必须在 sys.path 摆好之后)

BG, FG, BTN, ACT, GOLD = "#1b1b24", "#e6e6f0", "#2a2a36", "#3a6ea5", "#ffd479"


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("自走棋助手 · 多开启动器")
        self.root.geometry("640x520")
        self.root.minsize(560, 470)
        self.root.configure(bg=BG)
        self.running = False

        frm = tk.Frame(root, bg=BG)
        frm.pack(fill="x", padx=14, pady=(12, 6))

        r = 0
        tk.Label(frm, text="开几个实例:", bg=BG, fg=FG, font=("Microsoft YaHei", 10)).grid(
            row=r, column=0, sticky="w", pady=4)
        self.v_count = tk.StringVar(value="2")
        tk.Spinbox(frm, from_=1, to=20, width=6, textvariable=self.v_count,
                   font=("Microsoft YaHei", 10)).grid(row=r, column=1, sticky="w")
        tk.Label(frm, text="(2/3/5 都行, 上限 20)", bg=BG, fg="#9a9aae",
                 font=("Microsoft YaHei", 9)).grid(row=r, column=2, sticky="w", padx=6)

        r += 1
        tk.Label(frm, text="从第几号开始:", bg=BG, fg=FG, font=("Microsoft YaHei", 10)).grid(
            row=r, column=0, sticky="w", pady=4)
        self.v_start = tk.StringVar(value="1")
        tk.Spinbox(frm, from_=1, to=20, width=6, textvariable=self.v_start,
                   font=("Microsoft YaHei", 10)).grid(row=r, column=1, sticky="w")
        tk.Label(frm, text="(1号=8765, 前面已开着的会自动跳过)", bg=BG, fg="#9a9aae",
                 font=("Microsoft YaHei", 9)).grid(row=r, column=2, sticky="w", padx=6)

        r += 1
        tk.Label(frm, text="起始端口:", bg=BG, fg=FG, font=("Microsoft YaHei", 10)).grid(
            row=r, column=0, sticky="w", pady=4)
        # 起始端口默认用"智能推荐"(避开当前已用端口, 多波多开不撞车); 失败则回落 8765
        try:
            _rec = ML.recommend_base_port()
        except Exception:
            _rec = 8765
        self.v_port = tk.StringVar(value=str(_rec))
        tk.Entry(frm, width=8, textvariable=self.v_port,
                 font=("Microsoft YaHei", 10)).grid(row=r, column=1, sticky="w")
        tk.Label(frm, text="(每个实例间隔 20: 8765 / 8785 / 8805 …)", bg=BG, fg="#9a9aae",
                 font=("Microsoft YaHei", 9)).grid(row=r, column=2, sticky="w", padx=6)

        r += 1
        tk.Label(frm, text="界面:", bg=BG, fg=FG, font=("Microsoft YaHei", 10)).grid(
            row=r, column=0, sticky="nw", pady=4)
        self.v_ui = tk.StringVar(value="first")
        box = tk.Frame(frm, bg=BG)
        box.grid(row=r, column=1, columnspan=2, sticky="w")
        for val, txt in (("first", "只第 1 个带界面(推荐, 省资源)"),
                         ("all", "全部带界面"),
                         ("none", "全部后台(不弹窗口)")):
            tk.Radiobutton(box, text=txt, value=val, variable=self.v_ui, bg=BG, fg=FG,
                           selectcolor="#33333f", activebackground=BG,
                           font=("Microsoft YaHei", 9), bd=0, relief="flat").pack(anchor="w")

        r += 1
        # 多开默认开独立 Edge: 每个实例一个独立浏览器 profile, localStorage(端口钥匙)互不干扰。
        # 不勾的话所有实例共用你原本的浏览器, 多开皮肤的端口必然对不上(结构性无解)。
        self.v_edge = tk.BooleanVar(value=True)
        tk.Checkbutton(frm, text="顺便为每个实例开一个独立 Edge(自动打开装脚本页)",
                       variable=self.v_edge, bg=BG, fg=FG, selectcolor="#33333f",
                       activebackground=BG, font=("Microsoft YaHei", 9),
                       bd=0, relief="flat").grid(row=r, column=0, columnspan=3, sticky="w", pady=(8, 2))

        bar = tk.Frame(root, bg=BG)
        bar.pack(fill="x", padx=14, pady=6)
        self.btn = tk.Button(bar, text="启动", command=self.start, bg=ACT, fg="white",
                             font=("Microsoft YaHei", 10, "bold"), padx=18, cursor="hand2")
        self.btn.pack(side="left")
        tk.Button(bar, text="关闭", command=root.destroy, bg=BTN, fg=FG,
                  font=("Microsoft YaHei", 10), padx=14, cursor="hand2").pack(side="left", padx=8)
        tk.Button(bar, text="多开面板", command=self.open_panel, bg=BTN, fg=GOLD,
                  font=("Microsoft YaHei", 10), padx=14, cursor="hand2").pack(side="right")

        self.txt = tk.Text(root, height=14, bg="#101018", fg="#c8c8d8", insertbackground=FG,
                           font=("Consolas", 9), relief="flat")
        self.txt.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        self.say("填好点『启动』。\n"
                 "· 每个实例有自己的端口和数据目录(instances/N号机/), 互不干扰\n"
                 "· 一个实例 = 一个浏览器(独立 profile); 浏览器里装脚本/扩展时填对应端口\n"
                 "· 想改某个实例的端口: 在那个助手窗口的工具栏点『端口 xxxx』")

    def open_panel(self):
        """打开多开面板(看/停/重启/同步名单都在那里面)。"""
        try:
            import subprocess as _sp
            _sp.Popen([sys.executable, os.path.join(TOOLS, "multi_panel.py")], cwd=ROOT)
            self.say("已打开『多开面板』。")
        except Exception as e:
            messagebox.showerror("打不开", str(e))

    def say(self, s):
        self.txt.insert("end", s + "\n")
        self.txt.see("end")
        self.txt.update_idletasks()

    def start(self):
        if self.running:
            return
        try:
            n = int(self.v_count.get())
            st = int(self.v_start.get())
            base = int(self.v_port.get())
        except ValueError:
            messagebox.showerror("填错了", "数量 / 起始号 / 端口 都得是数字")
            return
        if not (1 <= n <= 20 and 1 <= st <= 20 and 1024 <= base <= 65000):
            messagebox.showerror("填错了", "数量 1-20、起始号 1-20、端口 1024-65000")
            return

        self.running = True
        self.btn.configure(state="disabled", text="启动中…")
        ui_mode, with_edge = self.v_ui.get(), self.v_edge.get()
        # 独立 Edge 醒目提示: 勾了却找不到 Edge, 先问清楚, 别闷头每个实例都失败
        if with_edge and not os.path.exists(ML.EDGE):
            if not messagebox.askyesno("找不到 Edge",
                                       "你勾了『独立 Edge』, 但系统里没找到 Edge:\n%s\n\n"
                                       "仍要启动(只是本次不开配套浏览器)?\n"
                                       "(装好 Edge 后重开本窗口即可自动带上)" % ML.EDGE):
                self.btn.configure(state="normal", text="启动")
                self.running = False
                return
            with_edge = False
            self.say("  [提示] 找不到 Edge, 本次不开独立浏览器(实例照常起)")

        def work():
            started = []
            # 启动前清理异常退出残留的僵尸目录(宁漏勿误删, 有 data/ 的实例数据保留)
            try:
                _rem = ML.cleanup_zombies(ML.INST_DIR, set(range(st, st + n)))
                if _rem:
                    self.say("  清理上次异常残留目录: " + ", ".join(_rem))
            except Exception as _e:
                self.say("  [清理] 略: %s" % _e)
            for i in range(st, st + n):
                port = ML.port_of(i, base, 20)
                if ML.ping(port):
                    self.say("  [跳过] %d号机 端口 %d 已经有服务在跑了" % (i, port))
                    continue
                ui = (ui_mode == "all") or (ui_mode == "first" and i == st)
                name, d, p, port = ML.launch(i, port, ui, base, 20,
                                             main_instance=(i == st))
                self.say("  启动 %s 端口 %d pid=%d (%s)" % (name, port, p.pid,
                                                           "带界面" if ui else "后台"))
                ok = ML.wait_up(port)
                self.say("    [%s] %s http://127.0.0.1:%d/install.html%s"
                         % ("OK" if ok else "超时", name, port,
                            "" if ok else "  → 日志: " + os.path.join(d, "console.log")))
                if ok and with_edge:
                    ep, err = ML.launch_edge(i, port)
                    self.say("    [%s] 已开独立 Edge%s" % ("OK" if ep else "NG",
                                                          "" if ep else ": " + str(err)))
                started.append((name, port, ui))

            if not started:
                self.say("\n没有新起的实例(这些端口都已经在跑了)")
            else:
                self.say("\n" + "=" * 58)
                self.say(" 各实例装脚本地址(浏览器里打开对应那个):")
                for name, port, ui in started:
                    self.say("   %-5s http://127.0.0.1:%d/install.html%s"
                             % (name, port, "   ← 这个带界面" if ui else ""))
                self.say("=" * 58)
                self.say(" 关掉: 带界面的直接关窗口; 后台实例用任务管理器结束对应 pid。")
            self.btn.configure(state="normal", text="启动")
            self.running = False

        threading.Thread(target=work, daemon=True).start()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
