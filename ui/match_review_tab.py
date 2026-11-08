# -*- coding: utf-8 -*-
"""『对局复盘』页(v4.145★) —— 看一局的协议流水并导出报告。

为什么单开一页
--------------
流水记在 data\\matchlogs\\*.jsonl(每局一个), 只有命令行能看(dev\\_match_review.py),
用户问"有那个页面吗? 我怎么导出?" —— 答案当时是"没有", 这一页就是补这个:
    选一局 → 看摘要(操作成败五态 / 哪些协议最多 / 时间线) → 导出 md → 打开目录

★ 记录默认是关的: 助手启动时带环境变量 SGS_MATCHLOG=1 才会落盘(避免每局都写文件)。
   所以第一次进来如果一局都没有, 不是坏了 —— 按页面上的提示开一下再打一局。

五态是什么意思(latency 那列)
    accepted 服务器收了      rejected 服务器明确拒绝(带 errCode)
    timeout  30 秒没回包     orphan   只有回包没见请求(进局前发的)
    unknown  还没配对上(通常是刚发出, 回包还没到)
"""

import os
import subprocess
import sys
import tkinter as tk
from tkinter import ttk, messagebox

from ui.widgets import ThemeMixin
from core.matchlog import DATA_DIR, list_matches, summarize


class MatchReviewTabBuilder(ThemeMixin):
    def __init__(self, app):
        self.app = app
        self.root = app.root
        self.matches = []

    def log(self, msg, level="INFO"):
        try:
            self.app.log(str(msg), level)
        except Exception:
            pass

    # ------------------------------------------------------------------
    def build(self, parent):
        bg, fg, dim, fh = self._bg(), self._fg(), self._dim(), self._fh()
        top = tk.Frame(parent, bg=bg)
        top.pack(fill="both", expand=True, padx=8, pady=6)

        tk.Label(top, text="对局复盘 —— 一局的协议流水与操作成败",
                 bg=bg, fg=self._gold(), font=(fh, 11, "bold")).pack(anchor="w")
        tk.Label(top, text="流水目录: %s" % DATA_DIR,
                 bg=bg, fg=dim, font=(fh, 8)).pack(anchor="w")

        # ① 选一局
        box = tk.LabelFrame(top, text="① 选一局", bg=bg, fg=fg, font=(fh, 9, "bold"))
        box.pack(fill="x", pady=(6, 0))
        r = tk.Frame(box, bg=bg)
        r.pack(fill="x", padx=6, pady=6)
        self.pick = tk.StringVar(value="")
        self.cb = ttk.Combobox(r, textvariable=self.pick, width=52, state="readonly")
        self.cb.pack(side="left")
        tk.Button(r, text="刷新列表", command=self._refresh, bg=self._btn(), fg=fg,
                  font=(fh, 9), relief="flat", cursor="hand2").pack(side="left", padx=6)
        tk.Button(r, text="看这一局", command=self._show, bg=self._act(), fg=fg,
                  font=(fh, 9, "bold"), relief="flat", cursor="hand2", padx=10).pack(side="left")
        tk.Button(r, text="看战报(逐回合)", command=self._report, bg=self._btn(), fg=fg,
                  font=(fh, 9), relief="flat", cursor="hand2", padx=8).pack(side="left", padx=(6, 0))

        # ② 结果
        box2 = tk.LabelFrame(top, text="② 复盘结果", bg=bg, fg=fg, font=(fh, 9, "bold"))
        box2.pack(fill="both", expand=True, pady=(6, 0))
        self.text = tk.Text(box2, height=18, bg="#1e1e1e", fg=fg, font=("Consolas", 9),
                            relief="flat", wrap="word")
        sb = tk.Scrollbar(box2, command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.text.pack(fill="both", expand=True, padx=6, pady=6)
        self._write("点『刷新列表』看看有没有记录过的对局。\n\n"
                    "⚠ 记录默认关闭: 启动时带 SGS_MATCHLOG=1 才会落盘, 例如:\n"
                    "   set SGS_MATCHLOG=1\n"
                    "   python ui\\app.py")

        # ③ 导出 / 目录
        box3 = tk.Frame(top, bg=bg)
        box3.pack(fill="x", pady=(6, 0))
        tk.Button(box3, text="导出报告(md)", command=self._export, bg=self._btn(), fg=fg,
                  font=(fh, 9), relief="flat", cursor="hand2", padx=10).pack(side="left")
        tk.Button(box3, text="打开日志目录", command=self._open_dir, bg=self._btn(), fg=fg,
                  font=(fh, 9), relief="flat", cursor="hand2", padx=10).pack(side="left", padx=6)
        self.status = tk.Label(box3, text="", bg=bg, fg=self._green(), font=(fh, 9))
        self.status.pack(side="left", padx=8)

        self._refresh()

    # ------------------------------------------------------------------
    def _refresh(self):
        self.matches = list_matches() or []
        # v4.151★ 光看文件名分不出哪局真打过 —— 实测 39 局里 16 局 action=0,
        #   全是助手开着、游戏却停在公会/主界面时留下的空壳(文件名一模一样,
        #   只能靠内容区分)。所以数一下每局操作数标在名字后面, 空局统一挪到
        #   列表最后, 免得随手一点就中空的、还以为是战报坏了。
        info = [(p, self._count_actions(p)) for p in self.matches]
        good = [x for x in info if x[1] > 0]
        bad = [x for x in info if x[1] <= 0]
        self.matches = [p for p, _ in good] + [p for p, _ in bad]
        names = []
        labels = {}
        for p, n in good + bad:
            b = os.path.basename(p)
            lab = ("%s · %d 步" % (b, n)) if n > 0 else ("%s ·(空局·没打过)" % b)
            names.append(lab)
            labels[lab] = p
        self._labels = labels
        self.cb["values"] = names
        if names:
            self.pick.set(names[0])
            msg = "共 %d 局" % len(names)
            if bad:
                msg += "（其中 %d 局是空的, 已排到最后）" % len(bad)
            self._status(msg, self._dim())
        else:
            self.pick.set("")
            self._status("还没有记录(记录默认关, 见上面的说明)", self._warn())

    def _count_actions(self, p):
        """v4.151★ 数这局有几条操作(action)。

        只做一遍字节计数、不 json 解析 —— 39 个文件共 38MB, 这样扫完也就
        一两秒; 目的只是把"真打过的局"和"开着但没进对局的空壳"分开。
        """
        try:
            with open(p, "rb") as f:
                return f.read().count(b'"type": "action"')
        except Exception:
            return 0

    def _current(self):
        n = (self.pick.get() or "").strip()
        # v4.151★ 下拉里现在显示的是 "文件名 · N 步", 不再是纯文件名,
        #   所以先查刷新时建好的 标签→路径 表, 查不到再退回老的文件名比对。
        p = getattr(self, "_labels", {}).get(n)
        if p:
            return p
        for q in self.matches:
            if os.path.basename(q) == n:
                return q
        return None

    def _render(self, st, path):
        lines = ["文件: %s" % os.path.basename(path), ""]
        lines.append("事件总数 %d   操作(请求) %d   超时 %d   孤立回包 %d"
                     % (st.get("events", 0), st.get("actions", 0),
                        st.get("timeouts", 0), st.get("orphans", 0)))
        lines.append("")
        lines.append("操作成败(五态):")
        for k, v in sorted((st.get("by_state") or {}).items(), key=lambda x: -x[1]):
            lines.append("   %-10s %d" % (k, v))
        lines.append("")
        lines.append("按协议(前 15):")
        for k, v in sorted((st.get("by_proto") or {}).items(), key=lambda x: -x[1])[:15]:
            lines.append("   %-32s %d" % (k, v))
        notifies = st.get("notifies") or {}
        if notifies:
            lines.append("")
            lines.append("服务器推送(前 10):")
            for k, v in sorted(notifies.items(), key=lambda x: -x[1])[:10]:
                lines.append("   %-32s %d" % (k, v))
        lines.append("")
        lines.append("时间线(前 40 条):")
        for it in (st.get("timeline") or [])[:40]:
            lines.append("   %-28s %-10s %s"
                         % (str(it.get("proto") or ""), str(it.get("state") or ""),
                            ("发起=%s" % it.get("source")) if it.get("source") else ""))
        return "\n".join(lines)

    def _show(self):
        p = self._current()
        if not p:
            self._status("先选一局(列表是空的, 见上面说明)", self._warn())
            return
        try:
            st = summarize(p)
        except Exception as e:
            self._write("读不了这个文件: %s" % e)
            self._status("读取失败", self._red())
            return
        self._write(self._render(st, p))
        self._status("已载入 %s" % os.path.basename(p), self._green())

    def _report(self):
        """v4.148★ 逐回合战报 —— 调 dev/match_report.py, 结果直接显示在这一栏。

        为什么单开一个按钮: 上面『看这一局』给的是【成败五态 / 协议计数】那种体检表,
        它答不了用户真正问的"我这局上了谁、用了什么锦囊、给谁随征的、带的什么装备"。
        战报才是给人看的那一份。两者共用同一个 dev/match_report.py, 命令行敲和这里点
        出来的内容一致 —— 不会出现"两边说法不一样"。
        """
        p = self._current()
        if not p:
            self._status("先选一局", self._warn())
            return
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = os.path.join(root, "dev", "match_report.py")
        if not os.path.exists(script):
            self._status("找不到 dev/match_report.py", self._red())
            return
        # v4.151★ 先【进程内】跑, 空了再退回子进程。
        #   为什么: 子进程那条路是 subprocess.run([sys.executable, script, p])。
        #   主程序一旦是打包出来的 exe, sys.executable 就是那个 exe 自己 ——
        #   等于拿 exe 去"执行"一个 .py: 要么空输出, 要么又弹一个界面,
        #   面板上就只剩"(战报没有输出 —— 这局可能是空的)"。
        #   实机就是这样: 命令行敲 python dev\match_report.py 明明有 213 行
        #   完整战报(我方阵上/装备/遣散/上阵/挑锦囊 全在), 面板里却什么都没有。
        #   进程内 import 进来直接调 main(), 不依赖 sys.executable, python 跑和
        #   打包跑都一样。
        txt = ""
        try:
            txt = self._report_inproc(script, p) or ""
        except Exception as e:
            txt = ""
            self.log("[对局复盘] 进程内跑战报失败: %s" % e)
        if not txt.strip():
            try:
                env = dict(os.environ)
                env["PYTHONIOENCODING"] = "utf-8"
                r = subprocess.run([sys.executable, script, p],
                                   capture_output=True, timeout=120, env=env,
                                   cwd=root)
                out = (r.stdout or b"").decode("utf-8", "replace")
                err = (r.stderr or b"").decode("utf-8", "replace")
                txt = out or err
            except Exception as e:
                txt = "跑战报失败: %s" % e
        txt = txt or "(战报没有输出 —— 这局可能是空的)"
        # v4.151★ 空局要【明说】, 不能让用户对着一张空表以为战报坏了。
        #   实机踩过: 助手开了一整夜(时长 560 分钟)但游戏停在公会/主界面, 那局
        #   记录里 "回合 : 0 (开打 0 / 打完 0)"、逐回合表一行都没有 —— 看着跟
        #   战报没做出来一模一样, 其实是【这局根本没开打】。换一局才有内容。
        if "回合 : 0" in txt or "回合 : 0 " in txt:
            txt = ("※ 这局没打过(回合 0 / 开打 0) —— 不是战报坏了,\n"
                   "  是这局记录里没有对局内容(常见: 助手开着但游戏停在公会/主界面)。\n"
                   "  换一局有回合数的再看。\n\n") + txt
        self._write(txt)
        n = txt.count("\n") + 1
        # v4.151★ 顺手落一份 dev\last_report.txt: 万一面板还是显示不出来,
        #   至少有个文件能直接拿记事本打开看, 不至于完全没法查。
        try:
            fp = os.path.join(root, "dev", "last_report.txt")
            with open(fp, "w", encoding="utf-8") as f:
                f.write(txt)
            self._status("战报已生成 (%d 行) · 已存 dev\\last_report.txt" % n,
                         self._green())
        except Exception:
            self._status("战报已生成 (%d 行)" % n, self._green())
        self.log("[对局复盘] 战报 %s (%d 行)" % (os.path.basename(p), n))

    def _report_inproc(self, script, p):
        """v4.151★ 进程内跑 dev/match_report.py。

        做法: import 进来 → 把 sys.argv 摆成命令行敲的样子 → redirect_stdout
        接住它 print 出来的战报。接不住(脚本本身报错)就返回空串, 让调用方
        退回子进程那条老路。
        """
        import io as _io
        import contextlib as _cl
        import importlib.util as _ilu

        spec = _ilu.spec_from_file_location("_sgs_match_report_%d" % id(self),
                                            script)
        if not spec or not spec.loader:
            return ""
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        buf = _io.StringIO()
        old = sys.argv
        sys.argv = [script, p]
        try:
            with _cl.redirect_stdout(buf):
                mod.main()
        finally:
            sys.argv = old
        return buf.getvalue()

    def _export(self):
        p = self._current()
        if not p:
            self._status("先选一局", self._warn())
            return
        try:
            st = summarize(p)
            txt = self._render(st, p)
        except Exception as e:
            self._status("读取失败: %s" % e, self._red())
            return
        out = os.path.splitext(p)[0] + "_复盘.md"
        try:
            with open(out, "w", encoding="utf-8") as f:
                f.write("# 对局复盘\n\n```\n" + txt + "\n```\n")
        except Exception as e:
            self._status("写文件失败: %s" % e, self._red())
            return
        self._status("已导出: %s" % out, self._green())
        self.log("[对局复盘] 导出 %s" % out)

    def _open_dir(self):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
        except Exception:
            pass
        try:
            if sys.platform.startswith("win"):
                os.startfile(DATA_DIR)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", DATA_DIR])
            else:
                subprocess.Popen(["xdg-open", DATA_DIR])
            self._status("已打开目录", self._green())
        except Exception as e:
            try:
                messagebox.showinfo("日志目录", DATA_DIR)
            except Exception:
                pass
            self._status("打不开目录: %s" % e, self._red())

    def _write(self, txt):
        try:
            self.text.configure(state="normal")
            self.text.delete("1.0", "end")
            self.text.insert("1.0", txt)
            self.text.configure(state="disabled")
        except Exception:
            pass

    def _status(self, txt, color):
        try:
            self.status.config(text=txt, fg=color)
        except Exception:
            pass
