# -*- coding: utf-8 -*-
"""阵容推荐 Tab(v4.105★) —— 把上游那两张"只能看不能算"的表做成能点的东西。

为什么单独一页
==============
数据来自 B站 UP『耳目一曲』《【S10精忠报国】终端阵容解析》的配套表:
  · data/s10_lineups.json : 35 组四势力组合 → 后期该转什么(有毒一套 / 没毒一套)
  · data/tavern_odds.json : 各级营帐刷新概率、商店张数、找指定卡的期望刷新次数

这两张表以前只能人肉查, 而且有个反直觉的结论(陈珪在 4 本最好找, 5 本反而更难;
祢衡 6 本比 5 本容易), 不换算成"要刷几次 / 要多少钱"根本用不上。所以这页干两件事:
  ① 选四个势力 → 直接给转阵思路(有毒/没毒两套);
  ② 输一张卡名 → 告诉你在几本刷最省、平均几次、要准备多少金币。

纯读表, 不联网、不碰游戏 —— 表缺失时页面照常打开, 只是给出提示而不是崩。
"""
import tkinter as tk
from tkinter import messagebox

from core.lineup_advisor import LineupAdvisor, FACTIONS

DEFAULT_REFRESH_COST = 2      # 刷一次几金币(丈八/刘焉会变, 界面上可改)
DEFAULT_BUY_COST = 3          # 买到手那张卡几金币


from ui.widgets import ThemeMixin   # v4.109★ 主题色 getter 统一到 widgets.ThemeMixin


class LineupTabBuilder(ThemeMixin):
    """『阵容』页 —— 与 SeasonUpdateTabBuilder 同一套构建模式(app 传进来, 不反向依赖)。"""

    def __init__(self, app):
        self.app = app
        self.root = app.root
        self.advisor = LineupAdvisor()
        self._faction_vars = {}
        self._slot_vars = []          # v4.107★ 站位那 7 格(build 里造好)
        # v4.106★ 换季后: 表还是上赛季的就要写在脸上(阵容结论会随卡池变, 不能拿旧思路硬套)
        self.stale = bool(getattr(self.advisor, "fallback", False)) and bool(
            getattr(self.advisor, "season", ""))

    # v4.109★ 主题色 getter 已统一到 widgets.ThemeMixin(本类继承它), 不再各写一份
    #   注: 原来这里的兜底色(BG #1e1e1e / FG #dddddd / BTN #2c2c2c / GREEN #7fd48f)
    #   与 app.py:315-325 的真实值并不一致, 现已统一到 app 那套。

    def _log(self, text):
        try:
            if hasattr(self.app, "log"):
                self.app.log(str(text))
        except Exception as e:
            print("[阵容] %s" % e)

    # ---------------------------------------------------------------- build
    def build(self, parent):
        bg, fg, gold = self._bg(), self._fg(), self._gold()

        top = tk.Frame(parent, bg=bg)
        top.pack(fill="x", padx=10, pady=(8, 0))
        tk.Button(top, text="在浏览器里打开本地手册（查卡 / 摆盘）", command=self._open_codex,
                  bg=self._btn(), fg=fg, font=(self._fh(), 10, "bold"), padx=10,
                  cursor="hand2").pack(side="left")
        tk.Label(top, text="（core/codex_server.py 起的一个纯本地 http 服务，不联网、不用装 Django）",
                 bg=bg, fg=self._dim(), font=(self._fh(), 8)).pack(side="left", padx=8)

        if self.stale:
            tk.Label(parent,
                     text="⚠ 本季是 S%s，但没找到它的阵容表 —— 现在用的是【S10 那张】，结论可能已过时。\n"
                          "   换季：拿到新赛季的优选表 → python tools/build_lineup_data.py --season %s"
                          % (self.advisor.season, self.advisor.season),
                     bg=bg, fg=self._warn(), font=(self._fh(), 9), justify="left"
                     ).pack(padx=10, pady=(2, 0), anchor="w")

        if not self.advisor.lineups:
            tk.Label(parent, text="⚠ 没读到阵容表(data/*lineups*.json) —— 先跑一次 "
                                  "python tools/build_lineup_data.py 生成它",
                     bg=bg, fg=self._warn(), font=(self._fh(), 10)).pack(padx=10, pady=10, anchor="w")

        # ---------- ① 势力 → 转阵思路 ----------
        box = tk.LabelFrame(parent, text=" 后期转什么 (选本局的四个势力) ",
                            bg=bg, fg=gold, bd=1, relief="groove", font=(self._fh(), 10, "bold"))
        box.pack(fill="x", padx=10, pady=(8, 4))

        row = tk.Frame(box, bg=bg)
        row.pack(fill="x", padx=8, pady=(6, 2))
        for f in FACTIONS:
            v = tk.BooleanVar(value=False)
            self._faction_vars[f] = v
            tk.Checkbutton(row, text=f, variable=v, command=self._on_pick,
                           bg=bg, fg=fg, selectcolor="#33333f", activebackground=bg,
                           font=(self._fh(), 10), bd=0, relief="flat").pack(side="left", padx=6)

        btns = tk.Frame(box, bg=bg)
        btns.pack(fill="x", padx=8, pady=(2, 4))
        tk.Button(btns, text="清空", command=self._clear, bg=self._btn(), fg=fg,
                  font=(self._fh(), 9), padx=8, cursor="hand2").pack(side="left")
        self.poison_var = tk.BooleanVar(value=True)
        tk.Checkbutton(btns, text="有毒 / 有关键装备", variable=self.poison_var,
                       command=self._on_pick, bg=bg, fg=fg, selectcolor="#33333f",
                       activebackground=bg, font=(self._fh(), 9), bd=0,
                       relief="flat").pack(side="left", padx=(10, 0))
        tk.Button(btns, text="按推荐生成剧本（写进『剧本』页）", command=self._gen_scenario,
                  bg=self._btn(), fg=self._gold(), font=(self._fh(), 9, "bold"), padx=10,
                  cursor="hand2").pack(side="left", padx=(12, 0))

        self.result = tk.Text(box, height=8, bg="#141414", fg=fg, insertbackground=fg,
                              font=(self._fh(), 10), wrap="word", relief="flat")
        self.result.pack(fill="x", padx=8, pady=(2, 8))
        self.result.insert("1.0", "勾上本局的四个势力 → 这里直接给「有毒」和「没毒」两套后期思路。")
        self.result.configure(state="disabled")

        # ---------- ② 找卡算账 ----------
        fb = tk.LabelFrame(parent, text=" 这张卡在几本刷最省 (期望次数 → 金币) ",
                           bg=bg, fg=gold, bd=1, relief="groove", font=(self._fh(), 10, "bold"))
        fb.pack(fill="x", padx=10, pady=(4, 4))

        r1 = tk.Frame(fb, bg=bg)
        r1.pack(fill="x", padx=8, pady=(6, 2))
        tk.Label(r1, text="卡名:", bg=bg, fg=fg, font=(self._fh(), 9), width=8, anchor="w").pack(side="left")
        self.card_var = tk.StringVar(value="陈珪")
        tk.Entry(r1, textvariable=self.card_var, width=12, bg="#1e1e1e", fg=fg,
                 insertbackground=fg, font=(self._fh(), 9)).pack(side="left", padx=4)
        tk.Label(r1, text="刷新费:", bg=bg, fg=fg, font=(self._fh(), 9)).pack(side="left", padx=(10, 0))
        self.rf_var = tk.StringVar(value=str(DEFAULT_REFRESH_COST))
        tk.Entry(r1, textvariable=self.rf_var, width=4, bg="#1e1e1e", fg=fg,
                 insertbackground=fg, font=(self._fh(), 9)).pack(side="left", padx=4)
        tk.Label(r1, text="买价:", bg=bg, fg=fg, font=(self._fh(), 9)).pack(side="left", padx=(10, 0))
        self.buy_var = tk.StringVar(value=str(DEFAULT_BUY_COST))
        tk.Entry(r1, textvariable=self.buy_var, width=4, bg="#1e1e1e", fg=fg,
                 insertbackground=fg, font=(self._fh(), 9)).pack(side="left", padx=4)
        tk.Label(r1, text="当前金币:", bg=bg, fg=fg, font=(self._fh(), 9)).pack(side="left", padx=(10, 0))
        self.gold_var = tk.StringVar(value="")
        tk.Entry(r1, textvariable=self.gold_var, width=6, bg="#1e1e1e", fg=fg,
                 insertbackground=fg, font=(self._fh(), 9)).pack(side="left", padx=4)
        tk.Button(r1, text="算一下", command=self._calc, bg=self._green(), fg=fg,
                  font=(self._fh(), 9, "bold"), padx=10, cursor="hand2").pack(side="left", padx=(12, 0))

        self.calc_out = tk.Text(fb, height=6, bg="#141414", fg=fg, insertbackground=fg,
                                font=(self._fh(), 10), wrap="word", relief="flat")
        self.calc_out.pack(fill="x", padx=8, pady=(2, 8))
        self.calc_out.configure(state="disabled")

        # ---------- ②b 站位 (v4.107★) ----------
        pb = tk.LabelFrame(parent, text=" 站位 (第1格=最前排, 先挨打) ",
                           bg=bg, fg=gold, bd=1, relief="groove", font=(self._fh(), 10, "bold"))
        pb.pack(fill="x", padx=10, pady=(4, 4))
        pr = tk.Frame(pb, bg=bg)
        pr.pack(fill="x", padx=8, pady=(6, 2))
        self._slot_vars = []
        for i in range(7):
            f = tk.Frame(pr, bg=bg)
            f.pack(side="left", padx=2)
            tk.Label(f, text=str(i + 1), bg=bg, fg=self._dim(), font=(self._fh(), 8)).pack()
            v = tk.StringVar(value="")
            self._slot_vars.append(v)
            tk.Entry(f, textvariable=v, width=7, bg="#1e1e1e", fg=fg, insertbackground=fg,
                     font=(self._fh(), 9)).pack()
        pb2 = tk.Frame(pb, bg=bg)
        pb2.pack(fill="x", padx=8, pady=(4, 2))
        tk.Button(pb2, text="按当前推荐排", command=self._pos_fill, bg=self._btn(), fg=self._gold(),
                  font=(self._fh(), 9), padx=8, cursor="hand2").pack(side="left")
        tk.Button(pb2, text="保存这组站位", command=self._pos_save, bg=self._btn(), fg=fg,
                  font=(self._fh(), 9), padx=8, cursor="hand2").pack(side="left", padx=(6, 0))
        tk.Button(pb2, text="生成站位步骤(写进剧本页)", command=self._pos_to_scenario,
                  bg=self._btn(), fg=self._green(), font=(self._fh(), 9), padx=8,
                  cursor="hand2").pack(side="left", padx=(6, 0))
        self.pos_note = tk.StringVar(
            value="勾齐四个势力 → 点『按当前推荐排』(按卡池攻防给默认盘); 也可以直接手填 7 格再保存。")
        tk.Label(pb, textvariable=self.pos_note, bg=bg, fg=self._dim(), font=(self._fh(), 8),
                 anchor="w", justify="left", wraplength=860).pack(fill="x", padx=8, pady=(0, 6))

        # ---------- ②c 谁克谁 (v4.107★) ----------
        cb = tk.LabelFrame(parent, text=" 谁克谁 (原表只有一句话, 这里能自己记) ",
                           bg=bg, fg=gold, bd=1, relief="groove", font=(self._fh(), 10, "bold"))
        cb.pack(fill="x", padx=10, pady=(4, 4))
        self.counter_list = tk.Listbox(cb, height=4, bg="#141414", fg=fg, font=(self._fh(), 9))
        self.counter_list.pack(fill="x", padx=8, pady=(6, 2))
        cr = tk.Frame(cb, bg=bg)
        cr.pack(fill="x", padx=8, pady=(0, 6))
        self.ct_a = tk.StringVar(value="")
        self.ct_b = tk.StringVar(value="")
        self.ct_win = tk.StringVar(value="a")
        tk.Entry(cr, textvariable=self.ct_a, width=14, bg="#1e1e1e", fg=fg, insertbackground=fg,
                 font=(self._fh(), 9)).pack(side="left")
        tk.Label(cr, text="vs", bg=bg, fg=fg, font=(self._fh(), 9)).pack(side="left", padx=3)
        tk.Entry(cr, textvariable=self.ct_b, width=14, bg="#1e1e1e", fg=fg, insertbackground=fg,
                 font=(self._fh(), 9)).pack(side="left")
        for t, val in (("前者克后者", "a"), ("后者克前者", "b"), ("五五开", "even")):
            tk.Radiobutton(cr, text=t, value=val, variable=self.ct_win, bg=bg, fg=fg,
                           selectcolor="#33333f", activebackground=bg, font=(self._fh(), 9)
                           ).pack(side="left", padx=(6, 0))
        tk.Button(cr, text="加一条", command=self._counter_add, bg=self._btn(), fg=self._green(),
                  font=(self._fh(), 9), padx=8, cursor="hand2").pack(side="left", padx=(8, 0))
        self._counters_refresh()

        # ---------- ②d 本局记录 (v4.107★) ----------
        rb = tk.LabelFrame(parent, text=" 战绩回看 (打完一局自动记一条) ",
                           bg=bg, fg=gold, bd=1, relief="groove", font=(self._fh(), 10, "bold"))
        rb.pack(fill="x", padx=10, pady=(4, 4))
        rr = tk.Frame(rb, bg=bg)
        rr.pack(fill="x", padx=8, pady=(6, 2))
        tk.Button(rr, text="刷新", command=self._records_refresh, bg=self._btn(), fg=fg,
                  font=(self._fh(), 9), padx=8, cursor="hand2").pack(side="left")
        tk.Button(rr, text="导出 CSV", command=self._records_export, bg=self._btn(), fg=fg,
                  font=(self._fh(), 9), padx=8, cursor="hand2").pack(side="left", padx=(6, 0))
        self.rec_out = tk.Text(rb, height=8, bg="#141414", fg=fg, insertbackground=fg,
                               font=(self._fh(), 9), wrap="word", relief="flat")
        self.rec_out.pack(fill="x", padx=8, pady=(2, 8))
        self.rec_out.configure(state="disabled")
        self._records_refresh()

        # ---------- ③ 表里的关键提示 ----------
        tb = tk.LabelFrame(parent, text=" 原表的关键结论 ",
                           bg=bg, fg=gold, bd=1, relief="groove", font=(self._fh(), 10, "bold"))
        tb.pack(fill="both", expand=True, padx=10, pady=(4, 10))
        tips = tk.Text(tb, bg="#141414", fg=self._dim(), font=(self._fh(), 9),
                       wrap="word", relief="flat", height=8)
        tips.pack(fill="both", expand=True, padx=8, pady=6)
        for t in (self.advisor.odds.get("tips") or []):
            tips.insert("end", "· " + t + "\n")
        tips.configure(state="disabled")

    # ---------------------------------------------------------------- 交互
    def _set(self, widget, text):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def _picked(self):
        return [f for f in FACTIONS if self._faction_vars.get(f) and self._faction_vars[f].get()]

    def _clear(self):
        for f in FACTIONS:
            if self._faction_vars.get(f):
                self._faction_vars[f].set(False)
        self._set(self.result, "勾上本局的四个势力 → 这里直接给「有毒」和「没毒」两套后期思路。")

    def _on_pick(self):
        picked = self._picked()
        if len(picked) > 4:
            # 只允许四个: 多勾的那个这次不生效, 免得整页逻辑变复杂
            self._set(self.result, "选了 %d 个势力(本局只有四个): %s —— 请去掉一个。"
                      % (len(picked), "/".join(picked)))
            return
        res = self.advisor.recommend(picked)
        if not picked:
            self._set(self.result, "勾上本局的四个势力 → 这里直接给「有毒」和「没毒」两套后期思路。")
            return
        lines = []
        if res["exact"]:
            lines.append("✔ 本局势力: " + "/".join(res["exact"]["factions"]))
            lines.append("")
            lines.append("【有毒 / 有关键装备】→ " + (res["poison"] or "(原表没写)"))
            lines.append("【没毒 / 没关键装备】→ " + (res["clean"] or "(原表没写)"))
        else:
            lines.append("还没选满四个(%d/4: %s) —— 下面是【包含这些势力】的组合:"
                         % (len(picked), "/".join(picked)))
            lines.append("")
            for r in res["candidates"]:
                lines.append("· " + r.get("key", "") + " → " + (r.get("poison") or ""))
            if not res["candidates"]:
                lines.append("(表里没有包含这些势力的组合)")
        self._set(self.result, "\n".join(lines))
        self._counters_refresh()      # v4.107★ 换推荐 = 换对手池, 克制那栏跟着变

    def _calc(self):
        def _num(var, default):
            try:
                return int(float(str(var.get()).strip()))
            except Exception:
                return default

        name = self.card_var.get().strip()
        rf = _num(self.rf_var, DEFAULT_REFRESH_COST)
        bc = _num(self.buy_var, DEFAULT_BUY_COST)
        gold = None
        gs = self.gold_var.get().strip()
        if gs:
            try:
                gold = int(float(gs))
            except Exception:
                gold = None
        self._set(self.calc_out, self.advisor.find_text(name, rf, bc, gold))
        self._log("[阵容] 查「%s」: %d本最省" % (name, (self.advisor.find_plan(name, rf, bc, gold)
                                                   .get("best") or {}).get("level", 0)))

    # ---------------- 站位 (v4.107★) ----------------
    def _pos_key(self):
        picked = self._picked()
        return "/".join(picked) if len(picked) == 4 else ""

    def _core_names(self):
        """当前推荐的核心卡(复用『生成剧本』那条链, 拿不到就返回空 —— 不瞎编)。"""
        picked = self._picked()
        if len(picked) != 4:
            return []
        try:
            from core import lineup_scenarios as LS
            sc = LS.build_for_factions(picked, poison=bool(self.poison_var.get()))
            names = []
            for s in ((sc or {}).get("steps") or []):
                if s.get("op") == "deploy" and s.get("target"):
                    for t in (s["target"] if isinstance(s["target"], list) else [s["target"]]):
                        if t and t not in names:
                            names.append(t)
            return names
        except Exception as e:
            self._log("[阵容] 取核心卡失败: %s" % e)
            return []

    def _pos_fill(self):
        names = self._core_names()
        if not names:
            self.pos_note.set("先勾齐四个势力(用推荐里的核心卡); 也可以直接在上面 7 个格子里手填。")
            return
        plan = self.advisor.position_plan(names, key=self._pos_key())
        slots = list(plan.get("slots") or []) + [""] * 7
        for i, v in enumerate(self._slot_vars):
            v.set(str(slots[i] or ""))
        self.pos_note.set("%s · 核心卡: %s" % (plan.get("note") or "", "、".join(names)))

    def _pos_save(self):
        key = self._pos_key()
        if not key:
            messagebox.showinfo("保存站位", "先把四个势力勾齐 —— 站位是按这组势力存的。")
            return
        slots = [v.get().strip() for v in self._slot_vars]
        if not any(slots):
            messagebox.showinfo("保存站位", "7 格都是空的, 没什么可存。")
            return
        if self.advisor.save_position(key, slots):
            self.pos_note.set("已存「%s」: %s" % (key, " / ".join(s or "-" for s in slots)))
            self._log("[阵容] 站位已存: %s" % key)
        else:
            messagebox.showerror("保存站位", "写 data/lineup_positions.json 失败(详情见日志)。")

    def _pos_to_scenario(self):
        """把上面 7 格变成【移动】步骤写进『剧本』页 —— 站位不用自己一步步挪。"""
        slots = [(i + 1, v.get().strip()) for i, v in enumerate(self._slot_vars) if v.get().strip()]
        if not slots:
            messagebox.showinfo("生成站位步骤", "先排好(或手填)至少一格。")
            return
        steps = [{"op": "log", "text": "按站位摆盘(第1格=最前排)"}]
        for i, nm in slots:
            steps.append({"op": "moveCard", "target": [nm], "pos": str(i), "optional": True})
            steps.append({"op": "wait", "ms": 300})
        steps.append({"op": "log", "text": "站位摆完(对手阵容变了记得回来改)"})
        sc = {"name": "站位·%s" % (self._pos_key() or "自定义"), "steps": steps}
        try:
            cfg = getattr(self.app, "cfg", None)
            if cfg is None:
                raise RuntimeError("拿不到 config 对象")
            cfg.setdefault("scenarios", []).append(sc)
            try:
                from core.config import save_config
            except Exception:
                save_config = getattr(self.app, "save_config", None)
            if save_config:
                save_config(cfg)
            # v4.110 修: 刷新方法在 ScenarioTabBuilder 上, 【不在 app 上】—— 以前
            #   hasattr(self.app, "_scenario_refresh_list") 恒为 False, 刷新从来没发生过:
            #   剧本确实写进了 config.json, 但『剧本』页列表不刷, 表现就是"点了没反应"
            #   (要重启助手才看得见)。
            b = getattr(self.app, "_scenario_builder", None)
            fn = (getattr(b, "_scenario_refresh_list", None)
                  or getattr(self.app, "_scenario_refresh_list", None))
            if fn:
                fn()
            else:
                self._log("[阵容] 剧本页列表刷新不了(找不到刷新方法) —— 剧本已写入 config, "
                          "重启助手后可见")
        except Exception as e:
            self._log("[阵容] 写站位剧本失败: %s" % e)
            messagebox.showerror("生成站位步骤", "写进 config.json 失败：%s" % e)
            return
        self._log("[阵容] 已生成站位剧本: %d 格" % len(slots))
        messagebox.showinfo("生成站位步骤", "已写入剧本「%s」(%d 格) —— 到『剧本』页选中就能跑。"
                                            % (sc["name"], len(slots)))

    # ---------------- 谁克谁 (v4.107★) ----------------
    def _counters_refresh(self):
        if not hasattr(self, "counter_list"):
            return
        self.counter_list.delete(0, "end")
        key = ""
        try:
            res = self.advisor.recommend(self._picked())
            key = "%s %s" % (res.get("poison") or "", res.get("clean") or "")
        except Exception:
            key = ""
        for x in self.advisor.counters(key):
            self.counter_list.insert("end", self.advisor.counter_text(x))
        if self.counter_list.size() == 0:
            self.counter_list.insert("end", "(还没记过 —— 在下面填一对, 点『加一条』)")

    def _counter_add(self):
        a, b = self.ct_a.get().strip(), self.ct_b.get().strip()
        if not (a and b):
            messagebox.showinfo("加克制", "两边都填一下(写阵容名或核心卡都行, 能匹配上就行)。")
            return
        if self.advisor.add_counter(a, b, self.ct_win.get()):
            self.ct_a.set("")
            self.ct_b.set("")
            self._counters_refresh()
            self._log("[阵容] 已记克制: %s / %s" % (a, b))
        else:
            messagebox.showerror("加克制", "写 data/lineup_counters.json 失败(详情见日志)。")

    # ---------------- 战绩回看 (v4.107★) ----------------
    def _records_refresh(self):
        if not hasattr(self, "rec_out"):
            return
        try:
            from core import records as RC
            recs = RC.load_records()
            st = RC.stats_overall(recs)
            lines = ["共 %d 局 · 胜率 %.1f%% (%d 胜)" % (st["games"], st["winrate"], st["wins"])]
            if st.get("placed"):
                lines.append("前四率 %.1f%% · 平均名次 %.2f (按 %d 局名次可信的算)"
                             % (st["top4_rate"], st["avg_placement"], st["placed"]))
            else:
                lines.append("(名次还不可信 —— 老战绩里那个数字是末场结果码; 新局会按淘汰顺序记)")
            lines.append("")
            recent = list(reversed(recs))[:8]
            if not recent:
                lines.append("还没有战绩: 打完一局会自动记一条(协议里的 GameOver)。")
            for r in recent:
                p = int(r.get("placement") or 0)
                gens = r.get("generals") or []
                lines.append("%s  %s  %s%s" % (
                    r.get("time") or "?",
                    ("第%d名" % p) if p else "名次?",
                    r.get("result") or "",
                    ("  " + "、".join(gens)) if gens else "  (这局没记下武将)"))
            self._set(self.rec_out, "\n".join(lines))
        except Exception as e:
            self._set(self.rec_out, "读战绩失败: %s" % e)

    def _records_export(self):
        try:
            from tkinter import filedialog
            from core import records as RC
            p = filedialog.asksaveasfilename(parent=self.root, title="导出战绩",
                                             defaultextension=".csv",
                                             filetypes=[("CSV", "*.csv"), ("所有", "*.*")])
            if not p:
                return
            RC.export_csv(p, RC.load_records())
            self._log("[阵容] 战绩已导出: %s" % p)
        except Exception as e:
            messagebox.showerror("导出战绩", str(e))

    def _open_codex(self):
        """起本地手册服务并打开浏览器。已经起着就直接复用(不再开第二个端口)。"""
        try:
            from core import codex_server
            url = codex_server.get_server().start(open_browser=True)
        except Exception as e:
            url = None
            self._log("[阵容] 本地手册启动失败: %s" % e)
        if url:
            self._log("[阵容] 本地手册已打开: " + url)
        else:
            messagebox.showerror("本地手册", "起不来本地服务（端口可能被占），详情见日志。")

    def _gen_scenario(self):
        """「按推荐转阵」: 勾齐四个势力 → 查表 → 把核心卡拼成一条剧本, 写进 config.json。

        只生成【找卡 + 上阵】两步 —— 站位/锦囊/升人口要看当时局势, 剧本里不做(最后一步
        日志会提醒)。原表里没写出可识别核心卡的组合会明确告诉你, 不会瞎编一个剧本。
        """
        picked = self._picked()
        if len(picked) != 4:
            messagebox.showinfo("生成剧本", "先把本局的四个势力勾齐（当前 %d 个）。" % len(picked))
            return
        try:
            from core import lineup_scenarios as LS
            sc = LS.build_for_factions(picked, poison=bool(self.poison_var.get()))
        except Exception as e:
            sc = None
            self._log("[阵容] 生成剧本失败: %s" % e)
        if not sc:
            messagebox.showinfo("生成剧本",
                                "这组势力在原表里没写出可识别的核心卡。\n\n"
                                "请看上方结果框里的原表原文，自己定核心卡后到『剧本』页手写。")
            return
        try:
            cfg = getattr(self.app, "cfg", None)
            if cfg is None:
                raise RuntimeError("拿不到 config 对象")
            cfg.setdefault("scenarios", []).append(sc)
            try:
                from core.config import save_config
            except Exception:
                save_config = getattr(self.app, "save_config", None)
            if save_config:
                save_config(cfg)
            # v4.110 修: 刷新方法在 ScenarioTabBuilder 上, 【不在 app 上】—— 以前
            #   hasattr(self.app, "_scenario_refresh_list") 恒为 False, 刷新从来没发生过:
            #   剧本确实写进了 config.json, 但『剧本』页列表不刷, 表现就是"点了没反应"
            #   (要重启助手才看得见)。
            b = getattr(self.app, "_scenario_builder", None)
            fn = (getattr(b, "_scenario_refresh_list", None)
                  or getattr(self.app, "_scenario_refresh_list", None))
            if fn:
                fn()
            else:
                self._log("[阵容] 剧本页列表刷新不了(找不到刷新方法) —— 剧本已写入 config, "
                          "重启助手后可见")
        except Exception as e:
            self._log("[阵容] 写剧本失败: %s" % e)
            messagebox.showerror("生成剧本", "写进 config.json 失败：%s" % e)
            return
        # v4.106★ 顺手把阵容表推到游戏页 —— 剧本里就能用「按推荐转阵」(lineupPlan)步骤了,
        #   不用先生成剧本再跑: 那条步骤直接照这份数据找卡 + 上阵。
        try:
            from core import lineup_scenarios as LS
            plan = LS.build_plan_payload(picked)
            ops = getattr(self.app, "ops", None)
            if plan and ops and hasattr(ops, "update_lineup_plan"):
                ops.update_lineup_plan(plan)
        except Exception as e:
            self._log("[阵容] 阵容表推送失败(不影响已生成的剧本): %s" % e)

        cores = [s.get("target") for s in sc["steps"] if s.get("op") == "deploy"]
        self._log("[阵容] 已生成剧本「%s」核心卡: %s" % (sc["name"], "/".join(cores)))
        messagebox.showinfo("生成剧本",
                            "已写入剧本「%s」\n\n核心卡：%s\n\n到『剧本』页选中它就能跑；\n"
                            "站位/锦囊/升人口剧本不做，最后一步日志会提醒。"
                            % (sc["name"], "、".join(cores)))
