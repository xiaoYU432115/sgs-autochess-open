# -*- coding: utf-8 -*-
"""多开面板: 一个窗口管住所有助手实例。

    python tools/multi_panel.py
    (或双击 多开.bat → 图形启动器 → 点『多开面板』)

能干什么:
  · 一眼看到: 几个实例在跑、各自端口 / PID / 带界面还是后台 / 连没连上游戏 / 脚本版本
  · 一键: 打开装脚本页、切到那个窗口、停止、重启、后台转带界面
  · 一键: 把某个实例的名单 / 卡池数据同步给其它实例(不用一个号一个号地配)
  · 起新实例: 自动找一个没人用的端口(端口被占不再只是干报错)
"""
import os
import subprocess
import sys
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from tkinter import messagebox, simpledialog, ttk

import tkinter as tk

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from core import instances as INST          # noqa: E402
from core import instance_sync as ISYNC     # noqa: E402

BG, FG, BTN, ACT = "#1b1b24", "#e6e6f0", "#2a2a36", "#3a6ea5"
OK_C, BAD_C, WARN_C = "#4bb96b", "#e0564b", "#d9a13b"


class Panel:
    def __init__(self, root):
        self.root = root
        self.root.title("多开面板 · 自走棋助手")
        self.root.geometry("860x520")
        self.root.minsize(760, 420)
        self.root.configure(bg=BG)

        bar = tk.Frame(root, bg=BG)
        bar.pack(fill="x", padx=10, pady=(10, 6))
        self.btn_new = tk.Button(bar, text="＋ 起新实例", command=self.a_new, bg=ACT, fg="white",
                                 font=("Microsoft YaHei", 9, "bold"), padx=10, cursor="hand2")
        self.btn_new.pack(side="left", padx=(0, 6))
        for txt, cmd in (("刷新", self.refresh), ("打开装脚本页", self.a_install),
                         ("切到窗口", self.a_focus), ("改端口", self.a_change_port),
                         ("停止", self.a_stop), ("重启", self.a_restart),
                         ("后台→带界面", self.a_to_ui),
                         ("同步名单/卡池", self.a_sync), ("打开数据目录", self.a_open_dir)):
            tk.Button(bar, text=txt, command=cmd, bg=BTN, fg=FG,
                      font=("Microsoft YaHei", 9), padx=8, cursor="hand2").pack(side="left", padx=3)

        self.auto = tk.BooleanVar(value=True)
        tk.Checkbutton(bar, text="自动刷新(3秒)", variable=self.auto, bg=BG, fg=FG,
                       selectcolor="#33333f", activebackground=BG, bd=0, relief="flat",
                       font=("Microsoft YaHei", 9)).pack(side="right")

        cols = ("port", "name", "pid", "mode", "conn", "rev", "dir")
        self.tree = ttk.Treeview(root, columns=cols, show="headings", height=14)
        head = {"port": ("端口", 70), "name": ("实例", 90), "pid": ("PID", 70),
                "mode": ("类型", 70), "conn": ("连接", 130), "rev": ("脚本rev", 90),
                "dir": ("数据目录", 320)}
        for c in cols:
            t, w = head[c]
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=10, pady=4)
        self.tree.tag_configure("dead", foreground="#8a8a9a")
        self.tree.tag_configure("ok", foreground=OK_C)
        self.tree.tag_configure("warn", foreground=WARN_C)
        self.tree.bind("<Double-1>", lambda e: self.a_focus())

        self.status = tk.Label(root, text="", bg=BG, fg="#9a9aae",
                               font=("Microsoft YaHei", 9), anchor="w")
        self.status.pack(fill="x", padx=10, pady=(0, 8))

        self.rows = {}      # port -> rec
        self.refresh()
        self._tick()

    # ---------- 数据 ----------
    def _selected(self):
        sel = self.tree.selection()
        if not sel:
            return None
        try:
            return self.rows[int(self.tree.item(sel[0], "values")[0])]
        except Exception:
            return None

    def refresh(self):
        recs = INST.list_running()
        # 并行探活: 5 个实例串行等超时要 6 秒, 面板会看起来卡死
        def enrich(r):
            r["rev"] = INST.script_rev(r["port"]) if r.get("alive") else ""
            return r
        with ThreadPoolExecutor(max_workers=8) as ex:
            recs = list(ex.map(enrich, recs))

        self.rows = {int(r["port"]): r for r in recs}
        for it in self.tree.get_children():
            self.tree.delete(it)
        for r in sorted(recs, key=lambda x: x["port"]):
            if not r.get("alive"):
                conn, tag = "已退出(进程没了)", "dead"
            elif r.get("connected"):
                conn, tag = "● 已连游戏", "ok"
            else:
                ago = r.get("last_push_ago")
                conn = "○ 等浏览器推送" if ago is None else "○ 断连 %.0f 秒" % float(ago)
                tag = "warn"
            self.tree.insert("", "end", values=(
                r["port"], r.get("name") or "单实例", r.get("pid") or "",
                "带界面" if r.get("ui") else "后台",
                conn, r.get("rev") or "", r.get("dir") or "(助手根)"), tags=(tag,))
        n = len(recs)
        live = sum(1 for r in recs if r.get("alive"))
        conn = sum(1 for r in recs if r.get("connected"))
        self.status.configure(text="共 %d 个实例登记, %d 个在服务, %d 个已连上游戏"
                                   % (n, live, conn))

    def _tick(self):
        if self.auto.get():
            try:
                self.refresh()
            except Exception:
                pass
        self.root.after(3000, self._tick)

    # ---------- 操作 ----------
    def a_new(self):
        """起一个新实例: 自动挑一个没人用的端口(不再撞车干报错)。"""
        used = {r["port"] for r in INST.list_running()}
        base = 8765
        p = base
        while p in used or not INST.port_bindable(p):
            p += 20
        idx = max(1, (p - 8765) // 20 + 1)
        name = "%d号机" % idx
        try:
            subprocess.Popen([sys.executable, os.path.join(ROOT, "main.py"),
                              "--profile", name, "--port", str(p)], cwd=ROOT)
        except Exception as e:
            messagebox.showerror("起实例失败", str(e))
            return
        self.status.configure(text="正在起 %s(端口 %d)…" % (name, p))
        self.root.after(4000, self.refresh)

    def a_install(self):
        r = self._selected()
        if not r:
            return
        webbrowser.open("http://127.0.0.1:%d/install.html" % r["port"])

    def a_change_port(self):
        """给选中的实例改端口(含后台实例): 调它的 /reconfigure 让它自己重启到新端口,
        重启起来后再打开新端口的 install.html, 把油猴侧的脚本/皮肤端口一键同步。

        为什么面板也要有这功能: 改端口入口以前只在【带界面】实例的 UI 里, 5 开时
        2~5 号是后台(--no-ui), 想改它们的端口只能手改 config.json —— 没有这个按钮。
        """
        import time as _t
        import urllib.request as _ur
        r = self._selected()
        if not r:
            return
        cur = int(r["port"])
        new = simpledialog.askinteger(
            "改端口",
            "%s 当前端口 %d\n\n新端口(1024-65535):" % (r.get("name") or "实例", cur),
            initialvalue=cur, minvalue=1024, maxvalue=65535, parent=self.root)
        if not new or int(new) == cur:
            return
        if INST.ping(int(new)) or INST.port_busy(int(new)):
            messagebox.showwarning("端口被占用",
                                   "端口 %d 已被占用(多半是另一个助手实例), 换一个。" % int(new))
            return
        try:
            _ur.urlopen("http://127.0.0.1:%d/reconfigure?port=%d" % (cur, int(new)),
                        timeout=5).read(300)
        except Exception as e:
            messagebox.showerror("改端口失败", str(e))
            return
        self.status.configure(text="%s 正在重启到端口 %d…" % (r.get("name"), int(new)))
        # 等新端口起来(实例要重启 + 重新 bind)
        ok = False
        for _ in range(40):
            _t.sleep(0.5)
            if INST.ping(int(new)):
                ok = True
                break
        if not ok:
            messagebox.showwarning("没起来",
                                   "新端口 %d 没在预期时间内响应, 请手动检查该实例。" % int(new))
        # 油猴侧端口同步: 打开新端口 install.html(脚本+皮肤共用一把钥匙, 一次生效)
        try:
            os.startfile("http://127.0.0.1:%d/install.html" % int(new))
        except Exception:
            try:
                webbrowser.open("http://127.0.0.1:%d/install.html" % int(new))
            except Exception:
                pass
        self.root.after(800, self.refresh)

    def a_focus(self):
        r = self._selected()
        if not r:
            return
        if not INST.focus_instance(r["port"]):
            messagebox.showinfo("切不过去", "这个实例没有登记的窗口句柄(多半是后台实例,\n"
                                           "可以用『后台→带界面』把它变成带界面的)。")

    def a_stop(self):
        r = self._selected()
        if not r:
            return
        if not messagebox.askyesno("停止实例", "确定停掉 %s(端口 %d, pid=%s)?"
                                   % (r.get("name") or "单实例", r["port"], r.get("pid"))):
            return
        ok, msg = INST.stop_instance(r["port"])
        if not ok:
            messagebox.showerror("停止失败", msg)
        self.root.after(800, self.refresh)

    def a_restart(self):
        r = self._selected()
        if not r:
            return
        ok, _ = INST.stop_instance(r["port"])
        pid = INST.relaunch(r)
        if not pid:
            messagebox.showerror("重启失败", "没能按原参数重开(登记信息可能过期, 先刷新再试)")
        self.root.after(4000, self.refresh)

    def a_to_ui(self):
        """后台实例 → 带界面(用原参数重开, 去掉 --no-ui)。"""
        r = self._selected()
        if not r:
            return
        if r.get("ui"):
            messagebox.showinfo("已经是带界面", "这个实例本来就是带界面的。")
            return
        INST.stop_instance(r["port"])
        INST.relaunch(r, drop_no_ui=True)
        self.root.after(4000, self.refresh)

    def a_open_dir(self):
        r = self._selected()
        d = (r or {}).get("dir") or os.path.join(ROOT, "data")
        if not os.path.isdir(d):
            d = os.path.dirname(d)
        try:
            os.startfile(d)
        except Exception as e:
            messagebox.showerror("打不开", str(e))

    def a_sync(self):
        """把某个实例的名单 / 卡池同步给其它实例。"""
        r = self._selected()
        targets = ISYNC.all_targets()
        if len(targets) < 2:
            messagebox.showinfo("没得同步", "目前只有一个数据目录(助手根), 先多开几个实例再来。")
            return
        src_label = (r or {}).get("name") or "助手根(单实例)"
        want_pool = messagebox.askyesno(
            "同步名单",
            "把【%s】的名单复制给其它实例。\n\n"
            "  是 → 名单 + 卡池/赛季数据(推荐, 免得各实例各抓一遍)\n"
            "  否 → 只同步名单\n\n"
            "注意: 正在运行的实例要【重启】才会读到新名单。" % src_label)
        groups = ("lists", "pool") if want_pool else ("lists",)
        res = ISYNC.sync_to_others(src_label, groups=groups)
        if not res:
            messagebox.showerror("同步失败", "源目录没找到: %s" % src_label)
            return
        lines = ["同步完成(源: %s)" % src_label, ""]
        for name, ok, fail in res:
            lines.append("  %-14s %d 个文件OK%s" % (name, ok, (", %d 个失败" % fail) if fail else ""))
        lines.append("")
        lines.append("运行中的实例请点『重启』以读取新名单。")
        messagebox.showinfo("同步完成", "\n".join(lines))


def main():
    root = tk.Tk()
    Panel(root)
    root.mainloop()


if __name__ == "__main__":
    main()
