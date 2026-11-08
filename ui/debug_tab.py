# -*- coding: utf-8 -*-
"""Debug 诊断 tab —— v4.200★

独立的「Debug/决策审计」页, 从桥拉取 window.SgsDebug.getSnapshot() 并展示:
  · 状态区(enabled / tableID / phase / round / 装备pending / 未确认 / 最近决策)
  · 最近决策列表(为什么选X)
  · 最近日志(debug 级)
  · 完整快照(JSON)
并可与页交互: 开启/关闭 Debug、应用装备目标、切换「跳过合成动画」开关。

桥侧新增指令(见 bridge/sgs_bridge.user.js): debug_snapshot / set_debug_enabled /
set_shenbing_targets / set_skip_composite / set_auto_advance / debug_decisions。
"""
import json
import threading

import tkinter as tk
from tkinter import ttk, messagebox

from ui.widgets import ThemeMixin
from ui.app import (BG, FG, DIM, RED, GOLD, BLUE, BTN, ACT,
                    SEL_BG, SEL_FG, FH)


class DebugTabBuilder(ThemeMixin):
    """Debug 诊断页。app.py 只留薄壳(创建 tab 帧 + 调 _build)。"""

    def __init__(self, app):
        self.app = app
        self.root = app.root
        self._th = None
        self._stop = False
        self._running = False

    # ------------------------------------------------------------------ 构建
    def _build(self):
        frame = self.app._make_scrollable_tab(self.app.tab_debug, padx=(10, 0), pady=8)

        tk.Label(frame, text="Debug 诊断 / 决策审计", bg=BG, fg=GOLD, font=(FH, 14, "bold")).pack(anchor="w")
        tk.Label(frame,
                 text="实时拉取页内 window.SgsDebug 快照(决策/日志/装备pending)。需先连接游戏页。",
                 bg=BG, fg=DIM, font=(FH, 9), wraplength=980, justify="left").pack(anchor="w", pady=(2, 8))

        # ===== 控制行 =====
        ctrl = tk.Frame(frame, bg=BG)
        ctrl.pack(fill="x", pady=(0, 8))
        self.auto_var = tk.BooleanVar(value=True)
        tk.Checkbutton(ctrl, text="自动刷新(2s)", variable=self.auto_var,
                       bg=BG, fg=FG, selectcolor="#1e1e1e", activebackground=BG, activeforeground=FG,
                       font=(FH, 9)).pack(side="left")
        tk.Button(ctrl, text="立即刷新", command=self._manual_refresh, bg=BTN, fg=FG,
                  activebackground=BTN, activeforeground=FG, font=(FH, 9),
                  relief="flat", padx=10, cursor="hand2").pack(side="left", padx=(6, 0))
        tk.Button(ctrl, text="开启Debug", command=lambda: self._set_debug(True), bg=ACT, fg=FG,
                  activebackground=ACT, activeforeground=FG, font=(FH, 9),
                  relief="flat", padx=10, cursor="hand2").pack(side="left", padx=(6, 0))
        tk.Button(ctrl, text="关闭Debug", command=lambda: self._set_debug(False), bg=BTN, fg=FG,
                  activebackground=BTN, activeforeground=FG, font=(FH, 9),
                  relief="flat", padx=10, cursor="hand2").pack(side="left", padx=(6, 0))
        self.skip_var = tk.BooleanVar(value=False)
        tk.Checkbutton(ctrl, text="跳过合成动画(§5.3, 真机慎用)", variable=self.skip_var,
                       bg=BG, fg=FG, selectcolor="#1e1e1e", activebackground=BG, activeforeground=FG,
                       font=(FH, 9)).pack(side="left", padx=(8, 0))
        self.skip_var.trace_add("write", lambda *_a: self._set_skip(self.skip_var.get()))

        # ===== 装备目标编辑 =====
        eq = tk.LabelFrame(frame, text="装备目标(防具/武器 装备ID, 逗号分隔)", bg=BG, fg=GOLD, bd=1, relief="solid", font=(FH, 9))
        eq.pack(fill="x", pady=(0, 8))
        r1 = tk.Frame(eq, bg=BG)
        r1.pack(fill="x", padx=8, pady=6)
        tk.Label(r1, text="防具:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left")
        self.armor_var = tk.StringVar(value="4113001")
        tk.Entry(r1, textvariable=self.armor_var, bg=BTN, fg=FG, insertbackground=FG, font=(FH, 9), width=40).pack(side="left", padx=(6, 0))
        tk.Label(r1, text="武器:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left", padx=(12, 0))
        self.weapon_var = tk.StringVar(value="4112011")
        tk.Entry(r1, textvariable=self.weapon_var, bg=BTN, fg=FG, insertbackground=FG, font=(FH, 9), width=40).pack(side="left", padx=(6, 0))
        tk.Button(eq, text="应用装备目标", command=self._apply_targets, bg=ACT, fg=FG,
                  activebackground=ACT, activeforeground=FG, font=(FH, 9),
                  relief="flat", padx=12, cursor="hand2").pack(anchor="w", padx=8, pady=(0, 6))

        # ===== 状态区 =====
        stf = tk.LabelFrame(frame, text="状态", bg=BG, fg=GOLD, bd=1, relief="solid", font=(FH, 9))
        stf.pack(fill="x", pady=(0, 8))
        self.st_var = tk.StringVar(value="待命（未连接或无快照）")
        tk.Label(stf, textvariable=self.st_var, bg=BG, fg=FG, font=(FH, 9), width=120, anchor="w", justify="left").pack(fill="x", padx=8, pady=6)

        # ===== 最近决策 =====
        df = tk.LabelFrame(frame, text="最近决策（为什么选 X）", bg=BG, fg=GOLD, bd=1, relief="solid", font=(FH, 9))
        df.pack(fill="x", pady=(0, 8))
        self.dec_text = tk.Text(df, bg=BTN, fg=FG, font=(FH, 9), height=8, wrap="word")
        self.dec_text.pack(fill="x", padx=8, pady=6)

        # ===== 最近日志(debug 级) =====
        lf = tk.LabelFrame(frame, text="最近日志", bg=BG, fg=GOLD, bd=1, relief="solid", font=(FH, 9))
        lf.pack(fill="x", pady=(0, 8))
        self.log_text = tk.Text(lf, bg=BTN, fg=FG, font=(FH, 9), height=8, wrap="word")
        self.log_text.pack(fill="x", padx=8, pady=6)

        # ===== 完整快照 =====
        sf = tk.LabelFrame(frame, text="完整快照(JSON)", bg=BG, fg=GOLD, bd=1, relief="solid", font=(FH, 9))
        sf.pack(fill="both", expand=True, pady=(0, 8))
        self.snap_text = tk.Text(sf, bg=BTN, fg=FG, font=(FH, 9), height=10, wrap="none")
        self.snap_text.pack(fill="both", expand=True, padx=8, pady=6)

        self._start_poll()

    # ------------------------------------------------------------------ 交互
    def _connected(self):
        try:
            return bool(self.app.ops and self.app.ops.connected())
        except Exception:
            return False

    def _manual_refresh(self):
        if not self._connected():
            self.st_var.set("未连接游戏页（先连接再刷新）")
            return
        self._do_refresh()

    def _set_debug(self, on):
        if not self._connected():
            messagebox.showinfo("提示", "先连接游戏页")
            return
        try:
            self.app.ops.send("set_debug_enabled", on=bool(on))
            self.st_var.set("已发送 %s Debug" % ("开启" if on else "关闭"))
        except Exception as e:
            messagebox.showerror("发送失败", str(e))

    def _set_skip(self, on):
        if not self._connected():
            return
        try:
            self.app.ops.send("set_skip_composite", on=bool(on))
        except Exception:
            pass

    def _apply_targets(self):
        if not self._connected():
            messagebox.showinfo("提示", "先连接游戏页")
            return
        armor = self._parse_ids(self.armor_var.get())
        weapon = self._parse_ids(self.weapon_var.get())
        try:
            self.app.ops.send("set_shenbing_targets", armor=armor, weapon=weapon)
            self.st_var.set("已应用装备目标: 防具 %s / 武器 %s" % (armor, weapon))
        except Exception as e:
            messagebox.showerror("发送失败", str(e))

    @staticmethod
    def _parse_ids(s):
        out = []
        for p in (s or "").split(","):
            p = p.strip()
            if not p:
                continue
            try:
                n = int(p)
                if n:
                    out.append(n)
            except Exception:
                pass
        return out

    # ------------------------------------------------------------------ 轮询
    def _start_poll(self):
        if self._running:
            return
        self._running = True
        self._stop = False
        self._th = threading.Thread(target=self._poll, daemon=True)
        self._th.start()

    def _poll(self):
        import time
        while not self._stop:
            try:
                if self.auto_var.get() and self._connected():
                    self._do_refresh()
            except Exception:
                pass
            for _ in range(20):  # 2s, 可中断
                if self._stop:
                    break
                time.sleep(0.1)

    def _do_refresh(self):
        try:
            res = self.app.ops.send_recv("debug_snapshot", timeout=3.0)
        except Exception as e:
            self.root.after(0, self.st_var.set, "拉取失败: %s" % str(e)[:80])
            return
        if not res:
            self.root.after(0, self.st_var.set, "无返回（页未就绪或 SgsDebug 缺失）")
            return
        try:
            d = json.loads(res)
        except Exception:
            self.root.after(0, self.st_var.set, "返回非 JSON")
            return
        snap = (d or {}).get("snapshot") or {}
        self.root.after(0, self._render, snap)

    def _render(self, snap):
        try:
            mgr = snap.get("manager") or {}
            dbg = snap.get("debug") or {}
            eq = snap.get("equipment") or {}
            pend = eq.get("pending")
            if isinstance(pend, dict):
                pend_state = pend.get("state", "?")
                unconf = ("是(" + str(pend.get("equipmentID")) + ")") if pend_state == "unconfirmed" else "无"
            else:
                pend_state, unconf = "无", "无"
            lines = []
            lines.append("Debug: %s   会话: %s" % (dbg.get("enabled"), dbg.get("sessionId", "")[:16]))
            lines.append("对局: %s   表ID: %s" % (mgr.get("found"), mgr.get("tableID")))
            lines.append("阶段: %s   回合: %s" % (mgr.get("phase"), mgr.get("round")))
            lines.append("装备pending: %s   未确认: %s" % (pend_state, unconf))
            self.st_var.set("\n".join(lines))

            decisions = snap.get("decisions") or []
            dtxt = ""
            for x in decisions[-12:]:
                dtxt += "· %s #%s %s\n" % (x.get("kind", "?"), x.get("id", "?"), x.get("rule", ""))
            if not dtxt:
                dtxt = "（暂无决策）"
            self._set_text(self.dec_text, dtxt)

            logs = snap.get("recentLogs") or []
            ltxt = ""
            for x in logs[-30:]:
                ltxt += "[%s] %s %s %s\n" % (x.get("level"), x.get("module"), x.get("event"), json.dumps(x.get("context")) if x.get("context") is not None else "")
            if not ltxt:
                ltxt = "（无日志）"
            self._set_text(self.log_text, ltxt)

            self._set_text(self.snap_text, json.dumps(snap, ensure_ascii=False, indent=2))
        except Exception as e:
            try:
                self.st_var.set("渲染失败: %s" % str(e)[:80])
            except Exception:
                pass

    @staticmethod
    def _set_text(w, txt):
        try:
            w.configure(state="normal")
            w.delete("1.0", "end")
            w.insert("1.0", txt)
            w.configure(state="disabled")
        except Exception:
            pass

    def destroy(self):
        self._stop = True
