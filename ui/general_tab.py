# -*- coding: utf-8 -*-
"""『主公』tab (v4.152★) —— 列 40 个可选主公, 点哪个就发 ReqChessChooseGeneral(ID)。

为什么要有这一页
----------------
以前只能在游戏自己的选将界面里点。那个界面是游戏打包闭包里的窗口, 脚本点不到,
于是: 想指定主公 = 只能手动点。而底层其实很简单 —— 选将最终就是一条包::

    manager.ReqChessChooseGeneral(generalID, isPreselection)

实机包印证(data/matchlogs/m-1791965313-29348.jsonl)::

    {"op":"ReqChessChooseGeneral","args":[1019,true]}
    {"proto":"CReqChessChooseGeneral","request":{"tableID":3080,"isPreselection":true},"state":"sent"}
    {"proto":"CReqChessChooseGeneral","err_code":0,"state":"accepted"}   ← 服务器接受

所以直接调 manager 那个方法就行(与 bridge 里主公技能 lordSkillUse 同一条路)。

【候选主公为什么不从服务器拿】
    CNotifyChessGeneralList 的顶层键实测只有
    chessMinionTypList / isHomeOwner / readyEndTime / stageEndTime / tableID /
    teamInfo / userList / userNum —— **根本没有候选列表**。
    候选是客户端按配置表 + 赛季规则本地算的, 所以这里直接列配置表里那 40 个
    可选主公(data/generals.json, IsAvailable=1)就是正确做法, 不用等任何取证。

数据来源
--------
    data/generals.json     40 个可选主公: id / name / skill / hp / skin / search
    data/lord_skills.json  主公技能 ID -> {n: 名字, desc: 描述}
"""

import io
import json
import os

import tkinter as tk
from tkinter import ttk

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
BG = "#2a2a2a"

# 势力编码 <-> 中文(自动选将规则1 即填数字也填中文)
_FACTION_CN = {"魏": 1, "蜀": 2, "吴": 3, "群": 4, "黄巾": 5,
               "汉": 6, "西凉": 7, "董卓": 7, "袁绍": 8, "神": 11}
_FACTION_NUM = {v: k for k, v in _FACTION_CN.items()}
# v4.163★ 规则链界面那 8 个势力格子 —— 本局势力只有这 8 个(见 ChessConf.MinionType),
#   所以直接摆成一排让人点, 不用手敲中文/数字。
_FACTION_ORDER = [1, 2, 3, 4, 5, 6, 7, 8]
_FACTION_SHORT = {1: "魏", 2: "蜀", 3: "吴", 4: "群",
                  5: "黄巾", 6: "汉", 7: "西凉", 8: "袁"}
# 格子的三种状态: 不管 / 必须有 / 必须没有
_FSTYLE = {"none": (BG, "#8a8a8a"), "need": ("#2e7d32", "#ffffff"),
           "no": ("#8b2b2b", "#ffffff")}


def _load_json(rel):
    p = os.path.join(ROOT, rel)
    try:
        with io.open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


class GeneralTabBuilder(object):
    """『主公』tab。用法与 ui/match_review_tab.py 一致: 由 app 里 new 出来再 build(frame)。"""

    def __init__(self, app):
        self.app = app
        self.root = getattr(app, "root", None)
        self.generals = []
        self.skills = {}
        self.candidates = []          # v4.152★ 本局真正给你的候选(从选将窗口读)
        self.cand_var = tk.StringVar(value="")
        self.only_cand_var = tk.BooleanVar(value=True)
        self._busy = False
        self._last_logged_fire_ts = 0     # v4.152★ 自动选将日志去重(见 _on_auto)

    # ---------- 构建 ----------
    def build(self, frame):
        fg = "#dddddd"
        self.generals = _load_json(os.path.join("data", "generals.json")) or []
        self.skills = _load_json(os.path.join("data", "lord_skills.json")) or {}
        # v4.152★ 整个主公页套上 Canvas+Scrollbar(竖+横), 否则内容多/窗口窄时看不全
        mk = getattr(self.app, "_make_scrollable_tab", None)
        if callable(mk):
            frame = mk(frame, hscroll=True)

        top = tk.Frame(frame, bg=BG)
        top.pack(fill="x", padx=8, pady=(8, 2))
        tk.Label(top, text="选将(主公) —— 点一个主公就把选将包发到当前游戏页",
                 bg=BG, fg=fg, anchor="w").pack(fill="x")
        tk.Label(top,
                 text="候选不从服务器拿(CNotifyChessGeneralList 里没有候选列表), "
                      "直接列配置表全部可选主公。要在游戏【选将界面】时点 —— "
                      "『选将中』的判据是\"90 秒内收到过选将包\", 不是 phase 数值"
                      "(TavernChessPhaseType 的数值在解密产物里查不到, 不能靠猜)。",
                 bg=BG, fg="#888888", anchor="w",
                 wraplength=760, justify="left").pack(fill="x")

        # ---- 状态行 ----
        st = tk.Frame(frame, bg=BG)
        st.pack(fill="x", padx=8, pady=(6, 2))
        ttk.Button(st, text="刷新状态", command=self._refresh_state).pack(side="left")
        # v4.152★ 探针: 实机"窗口开着却发不出去"时, 一键把三个候选发包入口全摸一遍,
        #   结果打到日志并弹窗 —— 排查不靠猜。
        ttk.Button(st, text="探针", command=self._probe).pack(side="left", padx=6)
        self.state_var = tk.StringVar(value="(未读取)")
        tk.Label(st, textvariable=self.state_var, bg=BG, fg="#88ccff",
                 anchor="w").pack(side="left", padx=8)

        # v4.152★ 候选行: 选将窗口开着时能读到【本局真正给你的那几个】
        cn = tk.Frame(frame, bg=BG)
        cn.pack(fill="x", padx=8, pady=(2, 2))
        ttk.Checkbutton(cn, text="只看候选", variable=self.only_cand_var,
                        command=self._fill).pack(side="left")
        tk.Label(cn, textvariable=self.cand_var, bg=BG, fg="#ffcc66",
                 anchor="w", wraplength=700,
                 justify="left").pack(side="left", padx=6)

        # ---- 搜索 ----
        se = tk.Frame(frame, bg=BG)
        se.pack(fill="x", padx=8, pady=(4, 2))
        tk.Label(se, text="找:", bg=BG, fg=fg).pack(side="left")
        self.kw = tk.StringVar()
        e = ttk.Entry(se, textvariable=self.kw, width=18)
        e.pack(side="left", padx=4)
        e.bind("<KeyRelease>", lambda ev: self._fill())
        ttk.Button(se, text="清空", command=self._clear_kw).pack(side="left")
        self.n_var = tk.StringVar(value="")
        tk.Label(se, textvariable=self.n_var, bg=BG, fg="#888888").pack(side="left", padx=8)

        # ---- 列表 ----
        lst = tk.Frame(frame, bg=BG)
        lst.pack(fill="both", expand=True, padx=8, pady=4)
        cols = ("id", "name", "skill", "hp", "desc")
        self.tv = ttk.Treeview(lst, columns=cols, show="headings", height=16)
        for c, w, t in (("id", 60, "ID"), ("name", 90, "主公"),
                        ("skill", 110, "技能"), ("hp", 50, "血量"),
                        ("desc", 430, "技能描述")):
            self.tv.heading(c, text=t)
            self.tv.column(c, width=w, anchor="w")
        sb = ttk.Scrollbar(lst, orient="vertical", command=self.tv.yview)
        self.tv.configure(yscrollcommand=sb.set)
        self.tv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tv.bind("<Double-1>", lambda ev: self._send_both())

        # ---- 操作 ----
        op = tk.Frame(frame, bg=BG)
        op.pack(fill="x", padx=8, pady=(2, 8))
        ttk.Button(op, text="一键选定", command=self._send_both).pack(side="left")
        ttk.Button(op, text="只预选(True)", command=lambda: self._send(True)).pack(side="left", padx=6)
        ttk.Button(op, text="只确认(False)", command=lambda: self._send(False)).pack(side="left")
        tk.Label(op, text="双击一行 = 一键选定。游戏真实流程是两步: 预选(true) → "
                         "等\"选定\"按钮 → 确认(false), 学自 cy-自动选择主公-dev.js。",
                 bg=BG, fg="#888888").pack(side="left", padx=8)

        # ---- 自动选将预设 (v4.163★ 规则链) ----
        #   用户要"提前选好, 一进选将就自动选, 不用守着页面"; 而且要的不止"有X选Y"一条 ——
        #   真实需求是【一串带优先级的条件】: 有吴→李广 / 没吴有汉→岳飞 / 没吴有蜀→李广 /
        #   没吴没汉→狄青。所以这里做成【不限条数的规则链】, 每条可写"有"(必须全有)
        #   和"没有"(必须全没有), 从上往下取【第一条满足】的; 两个条件都留空 = 兜底。
        #   看门狗本体在脚本里(__sgsGenAuto, 120ms 一跳), 这里只负责配规则 + 下发。
        self._names = [(g.get("name") or "") for g in self.generals if g.get("name")]
        au = tk.LabelFrame(frame, text="自动选将(提前设好, 一进选将就自动选, 不用守页面)",
                           bg=BG, fg=fg)
        au.pack(fill="x", padx=8, pady=(2, 8))
        self.auto_var = tk.BooleanVar(value=False)
        self.auto_mode = tk.StringVar(value="confirm")
        self.auto_gap = tk.StringVar(value="120")
        self.auto_state = tk.StringVar(value="")
        self.cur_factions = []     # 本局势力(状态里的 minionTypList), 用来实时标出当前会命中哪条
        self.rules = []            # [{"need":StringVar, "no":StringVar, "general":StringVar, "hit":StringVar}]

        r1 = tk.Frame(au, bg=BG)
        r1.pack(fill="x", padx=6, pady=(4, 2))
        ttk.Checkbutton(r1, text="启用", variable=self.auto_var).pack(side="left")
        ttk.Radiobutton(r1, text="直接确认", variable=self.auto_mode,
                        value="confirm").pack(side="left", padx=8)
        ttk.Radiobutton(r1, text="只预选", variable=self.auto_mode,
                        value="preselect").pack(side="left")
        tk.Label(r1, text="两步间隔ms:", bg=BG, fg=fg).pack(side="left", padx=(12, 0))
        ttk.Entry(r1, textvariable=self.auto_gap, width=6).pack(side="left")

        rh = tk.Frame(au, bg=BG)
        rh.pack(fill="x", padx=6, pady=(6, 2))
        tk.Label(rh, text="规则链(从上往下, 取【第一条满足】的):", bg=BG, fg=fg).pack(side="left")
        ttk.Button(rh, text="+ 加一条", command=self._rule_add).pack(side="left", padx=8)
        tk.Label(rh, text="势力格子点一下=必须有(绿) · 再点=必须没有(红) · 第三下=不管",
                 bg=BG, fg="#888888").pack(side="left")

        self.rules_box = tk.Frame(au, bg=BG)
        self.rules_box.pack(fill="x", padx=6, pady=2)
        self.rules_sum = tk.StringVar(value="")
        self._rules_default()
        self._rules_render()
        tk.Label(au, textvariable=self.rules_sum, bg=BG, fg="#ffcc66", anchor="w",
                 wraplength=760, justify="left").pack(fill="x", padx=6, pady=(2, 0))

        r3 = tk.Frame(au, bg=BG)
        r3.pack(fill="x", padx=6, pady=(4, 2))
        ttk.Button(r3, text="下发到脚本", command=self._auto_push).pack(side="left")
        ttk.Button(r3, text="读脚本状态", command=self._auto_pull).pack(side="left", padx=8)
        ttk.Button(r3, text="停用", command=self._auto_stop).pack(side="left", padx=8)

        tk.Label(au, textvariable=self.auto_state, bg=BG, fg="#88ccff", anchor="w",
                 wraplength=760, justify="left").pack(fill="x", padx=6, pady=(4, 0))
        tk.Label(au, text="『有』= 本局势力里必须有(点绿的那些要全有); 『没有』= 必须没有"
                         "(点红的那些要全没有); 一条里两种都没点 = 兜底(一定命中), 习惯上放最后。"
                         " 右边『当前』列按本局势力实时标出会命中哪一条。",
                 bg=BG, fg="#888888", anchor="w", wraplength=760,
                 justify="left").pack(fill="x", padx=6, pady=(2, 4))

        self._auto_load()
        if self.root is not None:
            try:
                # 进来就静默补发一次(脚本侧现在是 localStorage 持久化, 这是双保险),
                # 再开一个 3 秒一次的诊断回读 —— 让"为什么没自动发"一直看得见。
                if self.auto_var.get():
                    self.root.after(1500, self._auto_push)
                self.root.after(2500, self._auto_loop)
            except Exception:
                pass

        self._fill()
        self._refresh_state()

    # ---------- 列表填充 ----------
    def _clear_kw(self):
        self.kw.set("")
        self._fill()

    def _fill(self):
        if not hasattr(self, "tv"):
            return
        for i in self.tv.get_children():
            self.tv.delete(i)
        kw = (self.kw.get() or "").strip().lower()
        # v4.152★ 候选模式: 只列本局真正给你的那几个, 并按它们在窗口里的顺序排。
        cand_ids = {}
        for c in (self.candidates or []):
            cand_ids[int(c.get("generalID") or 0)] = int(c.get("idx") or 0)
        only = bool(self.only_cand_var.get()) and bool(cand_ids)
        seq = self.generals
        if only:
            seq = sorted([g for g in self.generals
                          if int(g.get("id") or 0) in cand_ids],
                         key=lambda g: cand_ids[int(g.get("id") or 0)])
        n = 0
        for g in seq:
            name = g.get("name") or ""
            gid = int(g.get("id") or 0)
            sid = str(g.get("skill") or "")
            sk = self.skills.get(sid) or {}
            skname = sk.get("n") or ("技能" + sid if sid else "")
            if kw:
                hay = (name + " " + skname + " " + (g.get("search") or "") + " " + sid).lower()
                if kw not in hay:
                    continue
            self.tv.insert("", "end", iid=str(gid), values=(
                gid, ("★ " + name) if gid in cand_ids else name,
                ("%s(%s)" % (skname, sid)) if skname else sid,
                g.get("hp"), sk.get("desc") or ""))
            n += 1
        self.n_var.set("显示 %d / %d%s" % (n, len(self.generals),
                                          " (只看本局候选)" if only else ""))

    # ---------- 探针 (v4.152★) ----------
    def _probe(self):
        """一键摸清"窗口开着却发不出去"到底卡在哪 —— 结果打日志 + 弹窗。"""
        ops = getattr(self.app, "ops", None)
        if ops is None or not hasattr(ops, "lord_general_probe"):
            self.state_var.set("ops 未就绪 / 缺 lord_general_probe")
            return
        self.state_var.set("探针运行中 ...")
        import threading
        import json as _json

        def work():
            try:
                d = ops.lord_general_probe()
            except Exception as e:
                d = {"ok": False, "reason": "异常: %s" % e}
            txt = _json.dumps(d, ensure_ascii=False, indent=1)
            try:
                self._log("[选将探针]\n" + txt)
            except Exception:
                pass
            try:
                self.root.after(0, lambda: self._probe_done(d, txt))
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _probe_done(self, d, txt):
        self.state_var.set("探针完成 —— 见日志")
        # 只读最关键的三行, 一眼看出该用哪个入口
        try:
            sm = d.get("sceneManager") or {}
            wm = d.get("winManager") or {}
            cm = d.get("gameContextManager") or {}
            tip = ("入口① getScene().manager: %s / Req=%s\n"
                   "入口② win.Manager: %s / Req=%s\n"
                   "入口③ GameContext.Manager: %s / Req=%s\n"
                   "同一个对象: %s · 窗口找到: %s · 候选数: %s"
                   % (sm.get("cls"), sm.get("hasReqChooseGeneral"),
                      wm.get("cls"), wm.get("hasReqChooseGeneral"),
                      cm.get("cls") if cm else "(不在 window 上)", cm.get("hasReqChooseGeneral") if cm else "-",
                      d.get("winManagerSameAsScene"), d.get("winFound"), d.get("candidateCount")))
        except Exception:
            tip = txt[:1500]
        try:
            import tkinter.messagebox as mb
            mb.showinfo("选将探针", tip + "\n\n(完整结果已写入日志)  \n" + txt[:2500])
        except Exception:
            pass

    # ---------- 状态 ----------
    def _refresh_state(self):
        if self._busy:
            return
        self._busy = True
        self.state_var.set("读取中 ...")
        self._run(self._do_state, self._on_state)
        # v4.152★ 看门狗: 实机卡在"读取中"不动 —— send_recv 等不到回包时后台线程一直
        #   挂着, 界面就永远停在"读取中", 而且 _busy 不复位于是再点也没反应(用户看到
        #   的就是"读取中？？拿不到？")。这里 8 秒后强制收尾并给出可能原因。
        if self.root is not None:
            try:
                self.root.after(8000, self._state_timeout)
            except Exception:
                pass

    def _state_timeout(self):
        if not self._busy:
            return
        self._busy = False
        self.state_var.set("读取超时(8s) —— 脚本没回包。常见: 游戏页没开 / "
                           "油猴脚本没刷新(缺 lord_general_state 指令) / 通道没连上")

    def _do_state(self):
        ops = getattr(self.app, "ops", None)
        if ops is None or not hasattr(ops, "lord_general_state"):
            return {"ok": False, "reason": "ops 未就绪"}
        try:
            return ops.lord_general_state()
        except Exception as e:
            return {"ok": False, "reason": "异常: %s" % e}

    def _on_state(self, d):
        self._busy = False
        d = d or {}
        if not d.get("ok"):
            self.state_var.set("读不到: %s" % (d.get("reason") or "?"))
            return
        gid = d.get("myGeneralID") or 0
        nm = ""
        if gid:
            for g in self.generals:
                if int(g.get("id") or 0) == int(gid):
                    nm = " " + (g.get("name") or "")
                    break
        ago = d.get("chooseAgoMs")
        ago_s = ("%.0fs前收到选将包" % (ago / 1000.0)) if ago is not None else "从没收到过选将包"
        pre = d.get("myPreGeneralID") or 0
        # v4.152★ 已确认(selfGeneralID) 与 已预选(selfPreGeneralID) 分开显示 ——
        #   游戏源码: 回包 isPreselection ? selfPreGeneralID=i : selfGeneralID=i
        pre_s = (" · 预选=%d" % pre) if (pre and pre != gid) else ""
        # v4.152★ 四道闸原样体检 —— 用户报"点了没反应 / 游戏提示不在招募阶段"时,
        #   光看 phase 查不出是谁拦的, 所以把脚本吐回来的 gates 直接打出来。
        g = d.get("gates") or {}
        g_s = ""
        if g:
            # v4.163★ 本局势力存下来 —— 规则链那几行的『当前命中』要靠它实时标
            ml = g.get("minionTypList")
            if ml:
                try:
                    self.cur_factions = [int(x) for x in ml if str(x).strip()]
                    if getattr(self, "rules", None):
                        self._rules_hint()
                except Exception:
                    pass
            g_s = (" | 闸: selfGeneralID=%s 预选ID=%s 组队=%s 本局兵种=%s 选将包=%ss前"
                   % (g.get("selfGeneralID"), g.get("selfPreGeneralID"), g.get("friendUID"),
                      g.get("minionTypList"), g.get("chooseWinAgeSec")))
            ssl = g.get("serverSelectInfoList")
            if ssl:
                try:
                    g_s += " 别人已定=%s" % [x.get("generalID") for x in ssl
                                            if isinstance(x, dict) and x.get("isPreselection") in (False, 0)]
                except Exception:
                    pass
        # v4.152★ 选将阶段剩余秒数(manager.endChooseGeneralStageTime - 现在)。
        #   这是服务器给的 deadline, 比"窗口开着""协议到达时间"都硬 —— 也是唯一
        #   能解释"为什么这时候发得出去 / 那时候发不出去"的量。
        left = d.get("stageLeftSec")
        if left is not None:
            g_s += (" · 选将剩余=%ds%s" % (left, "【已结束】" if left <= 0 else ""))
        self.state_var.set(
            "phase=%s · 窗口开着=%s · 可操作=%s · 选将中=%s(%s) · 已确认主公=%s%s%s%s"
            % (d.get("phase"), d.get("winOpen"), d.get("canSelectOpt"),
               d.get("inChoose"), ago_s, gid or "(未选)", nm, pre_s, g_s))

        # v4.152★ 本局真正的候选(从 TavernChessSelectGeneralWindow.panel.DataProvider 读的,
        #   学自 cy-自动选择主公-dev.js)。拿到就在列表里标出来, 并可只看候选。
        cands = d.get("candidates") or []
        self.candidates = cands
        if cands:
            names = " ".join((c.get("name") or str(c.get("generalID"))) for c in cands)
            self.cand_var.set("本局候选(%d): %s" % (len(cands), names))
            if self.only_cand_var.get():
                self._fill()
        else:
            diag = d.get("diag") or ""
            if d.get("winFound"):
                self.cand_var.set("窗口找到了, 但没读到候选(候选数=%s) %s"
                                  % (d.get("dpCount"), diag))
            elif d.get("inChoose"):
                self.cand_var.set("(不在这局选将阶段 —— 进选将界面再点刷新) %s" % diag)
            else:
                self.cand_var.set("(没找到选将窗口: %s) —— 以下按配置表列全部 40 个" % (diag or "无诊断"))

    # ---------- 发选将包 ----------
    def _send(self, is_pre):
        sel = self.tv.selection()
        if not sel:
            self._log("先选一个主公")
            return
        try:
            gid = int(sel[0])
        except Exception:
            self._log("选中行拿不到 ID")
            return
        name = ""
        for g in self.generals:
            if int(g.get("id") or 0) == gid:
                name = g.get("name") or ""
                break
        self._log("选将: %s(%d) isPreselection=%s" % (name, gid, is_pre))
        self._run(lambda: self._do_send(gid, is_pre), self._on_send)

    def _send_both(self):
        """v4.152★ 一键选定: 预选(true) → 等 500ms → 确认(false)。

        游戏的真实流程是【两步】—— 先选中、界面上出现"选定"按钮, 再点那个按钮才确认
        (学自 cy-自动选择主公-dev.js: onOptImgClick 选中 → onSelectBtnClick 确认,
         而 onSelectBtnClick 的兜底实现就是 mgr.ReqChessChooseGeneral(generalID, false))。
        所以只发 false 有时不生效(界面还没"选中"), 两步都发才稳。
        """
        sel = self.tv.selection()
        if not sel:
            self._log("先选一个主公")
            return
        try:
            gid = int(sel[0])
        except Exception:
            self._log("选中行拿不到 ID")
            return
        self._log("一键选定: %d (预选 → 等 500ms → 确认)" % gid)
        self._run(lambda: self._do_both(gid), self._on_send)

    def _do_both(self, gid):
        # v4.152★ 提速: 不再"预选一次往返 + sleep + 确认一次往返"。
        #   实机一轮 lord_choose_general 要 2~3 秒(助手↔脚本是跨进程轮询, CMD_MS=500ms),
        #   两步就是 5 秒起步 —— 用户反馈"能选了但是慢"。
        #   现在一次性下发, 由脚本内部连着发两步(中间只隔 120ms), 只占一轮。
        ops = getattr(self.app, "ops", None)
        if ops is None or not hasattr(ops, "lord_choose_general"):
            return {"ok": False, "reason": "ops 未就绪(或 remote_ops 没更新)"}
        return ops.lord_choose_general(gid, is_preselection=True, both=True, gap=120)

    def _do_send(self, gid, is_pre):
        ops = getattr(self.app, "ops", None)
        if ops is None or not hasattr(ops, "lord_choose_general"):
            return {"ok": False, "reason": "ops 未就绪(或 remote_ops 没更新)"}
        try:
            return ops.lord_choose_general(gid, is_preselection=is_pre)
        except Exception as e:
            return {"ok": False, "reason": "异常: %s" % e}

    def _on_send(self, r):
        r = r or {}
        if r.get("ok"):
            self._log("已调用游戏方法 sent=%s" % (r.get("sent"),))
            if r.get("note"):
                self._log(r["note"])
        else:
            self._log("选将失败: %s" % (r.get("reason") or "未知"))
        # 无论成败都回读一次: ok 只代表调到了方法, 包有没有真发出去要看
        # selfPreGeneralID / selfGeneralID 有没有变(游戏内部还有几道闸门会静默短路)。
        self._refresh_state()

    # ---------- 自动选将预设 (v4.163★ 规则链) ----------
    _AUTO_REL = os.path.join("data", "general_auto.json")

    def _auto_path(self):
        return os.path.join(ROOT, self._AUTO_REL)

    @staticmethod
    def _name_to_gid(generals, name):
        for g in generals:
            if (g.get("name") or "") == name:
                return int(g.get("id") or 0), int(g.get("reqMinionTyp") or 0)
        return 0, 0

    # ---- 规则链: 增删改序 ----
    @staticmethod
    def _parse_factions(text):
        """中文/数字混填 -> 势力编码列表。空串 -> []。"""
        out = []
        for p in (text or "").replace("，", ",").replace("、", ",").split(","):
            p = p.strip()
            if not p:
                continue
            if p.isdigit():
                out.append(int(p))
                continue
            # 支持中文势力名: 吴 / 蜀 / 袁绍 ... 也兼容 "吴国" 这种带国字
            key = p
            if key in _FACTION_CN:
                out.append(_FACTION_CN[key])
                continue
            if key.endswith("国") and key[:-1] in _FACTION_CN:
                out.append(_FACTION_CN[key[:-1]])
                continue
            try:
                out.append(int(p))
            except Exception:
                pass
        return out

    @staticmethod
    def _faction_names(codes):
        return "、".join(_FACTION_NUM.get(int(x), str(x)) for x in codes)

    @staticmethod
    def _rule_new(need="", no="", general=""):
        return {"need": tk.StringVar(value=need),
                "no": tk.StringVar(value=no),
                "general": tk.StringVar(value=general),
                "hit": tk.StringVar(value="")}

    def _rules_default(self):
        """默认链 = 用户实际要的那四条(没装过配置时给一套能直接用的)。"""
        self.rules = [self._rule_new("吴", "", "李广"),
                      self._rule_new("汉", "吴", "岳飞"),
                      self._rule_new("蜀", "吴", "李广"),
                      self._rule_new("", "吴,汉", "狄青")]

    def _rule_add(self):
        self.rules.append(self._rule_new("", "", (self._names[0] if self._names else "")))
        self._rules_render()

    def _rule_del(self, i):
        if 0 <= i < len(self.rules):
            self.rules.pop(i)
            self._rules_render()

    def _rule_move(self, i, d):
        j = i + d
        if 0 <= j < len(self.rules):
            self.rules[i], self.rules[j] = self.rules[j], self.rules[i]
            self._rules_render()

    def _rule_text(self, r):
        """一条规则的人话(界面概览 + 下发 label 共用, 免得两处写法不一样)。"""
        need = self._parse_factions(r["need"].get())
        ex = self._parse_factions(r["no"].get())
        parts = []
        if need:
            parts.append("有" + self._faction_names(need))
        if ex:
            parts.append("没" + self._faction_names(ex))
        return "%s → %s" % (" ".join(parts) if parts else "兜底",
                            (r["general"].get() or "(未选主公)"))

    def _rule_toggle(self, r, code):
        """势力格子三态循环: 不管 → 必须有(绿) → 必须没有(红) → 不管。"""
        need = self._parse_factions(r["need"].get())
        ex = self._parse_factions(r["no"].get())
        if code in need:            # 有 -> 没有
            need.remove(code)
            ex.append(code)
        elif code in ex:            # 没有 -> 不管
            ex.remove(code)
        else:                       # 不管 -> 有
            need.append(code)
        r["need"].set(",".join(str(x) for x in need))
        r["no"].set(",".join(str(x) for x in ex))
        self._rule_paint(r)
        self._rules_hint()

    def _rule_paint(self, r):
        need = self._parse_factions(r["need"].get())
        ex = self._parse_factions(r["no"].get())
        for code, btn in (r.get("btns") or {}).items():
            if code in need:
                bg, fgc, t = _FSTYLE["need"][0], _FSTYLE["need"][1], "有" + _FACTION_SHORT[code]
            elif code in ex:
                bg, fgc, t = _FSTYLE["no"][0], _FSTYLE["no"][1], "没" + _FACTION_SHORT[code]
            else:
                bg, fgc, t = _FSTYLE["none"][0], _FSTYLE["none"][1], _FACTION_SHORT[code]
            try:
                btn.configure(text=t, bg=bg, fg=fgc,
                              activebackground=bg, activeforeground=fgc)
            except Exception:
                pass

    def _rules_render(self):
        if not hasattr(self, "rules_box"):
            return
        for w in self.rules_box.winfo_children():
            w.destroy()
        # 表头: 一行势力名, 下面每条规则点自己那排格子
        hd = tk.Frame(self.rules_box, bg=BG)
        hd.pack(fill="x", padx=2, pady=(0, 2))
        tk.Label(hd, text="", width=3, bg=BG).pack(side="left")
        for c in _FACTION_ORDER:
            tk.Label(hd, text=_FACTION_SHORT[c], width=4, bg=BG,
                     fg="#888888").pack(side="left")
        tk.Label(hd, text="→ 选主公", bg=BG, fg="#888888").pack(side="left", padx=(12, 0))
        for i, r in enumerate(self.rules):
            row = tk.Frame(self.rules_box, bg=BG)
            row.pack(fill="x", padx=2, pady=1)
            tk.Label(row, text="%d." % (i + 1), bg=BG, fg="#dddddd",
                     width=3, anchor="w").pack(side="left")
            btns = {}
            for c in _FACTION_ORDER:
                b = tk.Button(row, text=_FACTION_SHORT[c], width=4, bd=1, relief="flat",
                              bg=_FSTYLE["none"][0], fg=_FSTYLE["none"][1],
                              command=lambda rr=r, cc=c: self._rule_toggle(rr, cc))
                b.pack(side="left")
                btns[c] = b
            r["btns"] = btns
            cb = ttk.Combobox(row, textvariable=r["general"], width=12,
                              values=self._names, state="readonly")
            cb.pack(side="left", padx=(12, 0))
            cb.bind("<<ComboboxSelected>>", lambda ev: self._rules_hint())
            ttk.Button(row, text="↑", width=2,
                       command=lambda i=i: self._rule_move(i, -1)).pack(side="left", padx=(10, 1))
            ttk.Button(row, text="↓", width=2,
                       command=lambda i=i: self._rule_move(i, 1)).pack(side="left")
            ttk.Button(row, text="删", width=3,
                       command=lambda i=i: self._rule_del(i)).pack(side="left", padx=(1, 8))
            tk.Label(row, textvariable=r["hit"], bg=BG, fg="#88ccff",
                     anchor="w").pack(side="left")
            self._rule_paint(r)
        self._rules_hint()

    def _rules_hint(self):
        """按【当前本局势力】标出会命中哪一条 —— 配规则时不用靠脑补。"""
        cur = [int(x) for x in (self.cur_factions or []) if str(x).strip()]
        hit_idx = None
        if cur:
            for i, r in enumerate(self.rules):
                need = self._parse_factions(r["need"].get())
                ex = self._parse_factions(r["no"].get())
                if not need and not ex:
                    continue          # 兜底条: 只在前面都没命中时才算, 下面统一处理
                if all(x in cur for x in need) and not any(x in cur for x in ex):
                    hit_idx = i
                    break
        if hit_idx is None and cur:
            for i, r in enumerate(self.rules):
                if not self._parse_factions(r["need"].get()) and not self._parse_factions(r["no"].get()):
                    hit_idx = i
                    break
        for i, r in enumerate(self.rules):
            if not cur:
                r["hit"].set("")
            elif hit_idx is None:
                r["hit"].set("本局[%s] 无命中" % self._faction_names(cur))
            elif i == hit_idx:
                r["hit"].set("◀ 本局[%s] 命中这条" % self._faction_names(cur))
            else:
                r["hit"].set("")
        # 概览: 把整条链写成一行人话, 不用逐行去看点了哪些格子
        try:
            self.rules_sum.set("当前链: " + " | ".join(
                "%d.%s" % (i + 1, self._rule_text(r)) for i, r in enumerate(self.rules)))
        except Exception:
            pass

    def _auto_cfg(self):
        rules = []
        for r in (self.rules or []):
            nm = (r["general"].get() or "").strip()
            gid, req = self._name_to_gid(self.generals, nm)
            if not gid:
                continue          # 没选主公的条直接跳过, 别把 generalID=0 发给脚本
            need = self._parse_factions(r["need"].get())
            ex = self._parse_factions(r["no"].get())
            rules.append({"require": need, "exclude": ex, "generalID": gid, "req": req,
                          "label": self._rule_text(r).replace(" → ", " -> ")})
        try:
            gap = int(self.auto_gap.get() or 120)
        except Exception:
            gap = 120
        return {"enabled": bool(self.auto_var.get()),
                "mode": self.auto_mode.get() or "confirm",
                "gap": gap, "rules": rules}

    def _auto_load(self):
        try:
            with io.open(self._auto_path(), encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            return
        try:
            self.auto_var.set(bool(d.get("enabled")))
            self.auto_mode.set(d.get("mode") or "confirm")
            self.auto_gap.set(str(d.get("gap") or 120))
            rl = d.get("rules")
            if isinstance(rl, list) and rl:
                # v4.163★ 新格式: 规则链
                self.rules = [self._rule_new(str(x.get("need") or ""),
                                             str(x.get("no") or ""),
                                             str(x.get("general") or ""))
                              for x in rl if isinstance(x, dict)]
            elif rl is None:
                # 老格式(单条件 + 兜底)自动迁移, 别让用户配过的东西凭空消失。
                # 例外: 若那套还停在出厂值(有吴→李广 / 兜底→狄青), 说明从没手动改过 ——
                #   那就直接给新的四规则链(是它的超集), 省得用户自己再敲一遍。
                _old = (str(d.get("need") or ""), str(d.get("main") or ""),
                        str(d.get("fallback") or ""))
                if _old == ("3", "李广", "狄青"):
                    self._rules_default()
                else:
                    self.rules = [self._rule_new(_old[0], "", _old[1]),
                                  self._rule_new("", "", _old[2])]
            else:
                self._rules_default()
            self._rules_render()
        except Exception:
            pass

    def _auto_save(self):
        try:
            d = {"enabled": bool(self.auto_var.get()), "mode": self.auto_mode.get(),
                 "gap": self.auto_gap.get(),
                 "rules": [{"need": r["need"].get(), "no": r["no"].get(),
                            "general": r["general"].get()} for r in (self.rules or [])]}
            with io.open(self._auto_path(), "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=1)
        except Exception:
            pass

    def _auto_push(self):
        ops = getattr(self.app, "ops", None)
        if ops is None or not hasattr(ops, "lord_general_auto"):
            self.auto_state.set("ops 未就绪 / remote_ops 没更新")
            return
        cfg = self._auto_cfg()
        self._auto_save()
        self._run(lambda: ops.lord_general_auto(cfg=cfg, reset_fired=True), self._on_auto)

    def _auto_pull(self):
        ops = getattr(self.app, "ops", None)
        if ops is None or not hasattr(ops, "lord_general_auto"):
            self.auto_state.set("ops 未就绪 / remote_ops 没更新")
            return
        self._run(lambda: ops.lord_general_auto(), self._on_auto)

    def _auto_stop(self):
        """v4.152★ 一键停用: 关掉自动选将, 并把配置写进 localStorage(刷新页面也保持关)。

        用户担心自动选将影响正常游戏(选锦囊卡住)。代码上已有两道闸不碰选将以外的阶段,
        这里再给一个明确的'关'出口, 不必去取消复选框再点下发。
        """
        ops = getattr(self.app, "ops", None)
        if ops is None or not hasattr(ops, "lord_general_auto"):
            self.auto_state.set("ops 未就绪 / remote_ops 没更新")
            return
        self.auto_var.set(False)
        self._auto_save()   # data/general_auto.json 里 enabled 也置否, 重启助手仍关
        self._run(lambda: ops.lord_general_auto(cfg={"enabled": False, "mode": "confirm",
                                                      "gap": 120, "rules": []}), self._on_auto)
        self._log("已停用自动选将(配置已留空, 刷新页面也不会再自动选)")

    def _auto_loop(self):
        """v4.152★ 每 3 秒回读一次脚本侧诊断, 让『为什么没自动发』一直可见。

        之前只有一个静态的结果串, 用户看到"不生效"但完全不知道卡在哪一步;
        现在脚本每一跳都写 why, 这里定期拉回来显示。
        """
        try:
            ops = getattr(self.app, "ops", None)
            if ops is not None and hasattr(ops, "lord_general_auto"):
                self._run(lambda: ops.lord_general_auto(), self._on_auto)
        except Exception:
            pass
        try:
            self.root.after(3000, self._auto_loop)
        except Exception:
            pass

    def _on_auto(self, r):
        r = r or {}
        if not r.get("ok"):
            self.auto_state.set("下发失败: %s" % (r.get("reason") or "未知"))
            return
        if r.get("cfg") is None:
            self.auto_state.set("脚本当前没有配置 —— 勾『启用』再点『下发到脚本』")
            return
        c = r.get("cfg") or {}
        d = r.get("diag") or {}
        # v4.163★ 3 秒一轮的回读里也带着本局势力 —— 拿它刷新规则链的『当前命中』,
        #   这样不用专门去点『刷新状态』, 进选将那一刻就能看见会走哪条。
        if d.get("list"):
            try:
                self.cur_factions = [int(x) for x in d["list"] if str(x).strip()]
                if getattr(self, "rules", None):
                    self._rules_hint()
            except Exception:
                pass
        # v4.152★ 把脚本每一跳的"为什么没发"原样摊开 —— 盯着一个 ok:true 是查不出东西的。
        self.auto_state.set(
            "脚本: enabled=%s mode=%s 规则=%d条 · 已跳%d次/已发%d次 · 配置已存localStorage=%s\n"
            "这一跳为什么没发: %s\n"
            "实机取值: tableID=%s · selfGeneralID=%s · endChooseGeneralStageTime=%s · "
            "选将剩余=%ss · 本局势力=%s · 选将窗口可见=%s"
            % (c.get("enabled"), c.get("mode"), len(c.get("rules") or []),
               r.get("ticks") or 0, r.get("firedCount") or 0, r.get("ls"),
               r.get("why") or "(无)",
               d.get("tid"), d.get("sid"), d.get("endT"),
               d.get("stageLeftSec"), d.get("list"), d.get("win")))
        # v4.152★ 只在【新的一次自动发】时记一行 —— lastLog 是脚本里的持久字段,
        #   而这个 _on_auto 每 3 秒被轮询调一次, 不加这个判断日志会被同一条刷满
        #   (实机: 77 行全是同一条"已发", 看着像发了 77 次, 其实只发了一次)。
        fire_ts = r.get("lastFireTs") or 0
        if r.get("lastLog") and fire_ts and fire_ts != self._last_logged_fire_ts:
            self._last_logged_fire_ts = fire_ts
            self._log("自动选将 %s" % r["lastLog"])
        v = r.get("verify")
        if v:
            self.auto_state.set(
                self.auto_state.get()
                + "\n发后验证: %s —— 目标ID=%s selfGeneralID=%s selfPreGeneralID=%s 已重试%d次%s"
                % ("✅ 接上了" if v.get("ok") else "❌ 还没接上",
                   v.get("gid"), v.get("selfGeneralID"), v.get("selfPreGeneralID"),
                   v.get("tries") or 0,
                   "" if v.get("ok") else ("(自动重发中)" if not v.get("done") else "(已停止重试)")))

    # ---------- 通用 ----------
    def _log(self, msg):
        fn = getattr(self.app, "log", None)
        if callable(fn):
            try:
                fn("[主公] " + msg)
            except Exception:
                pass

    def _run(self, fn, on_done=None):
        """后台跑 fn, 回来在主线程调 on_done。没有 root 就退化成同步跑。"""
        if self.root is None:
            try:
                d = fn()
            except Exception as e:
                d = {"ok": False, "reason": "异常: %s" % e}
            cb = on_done or (self._on_state if "state" in getattr(fn, "__name__", "") else None)
            if callable(cb):
                cb(d)
            return

        def _task():
            try:
                d = fn()
            except Exception as e:
                d = {"ok": False, "reason": "异常: %s" % e}
            # v4.152★ 防御: 启动早期 mainloop 尚未起来时, root.after 会抛
            #   "main thread is not in main loop"。重试调度直到 mainloop 就绪,
            #   避免后台线程结果丢失 + 控制台刷 RuntimeError。
            import time
            cb = on_done or self._on_state
            for _ in range(120):
                try:
                    self.root.after(0, lambda: cb(d))
                    return
                except RuntimeError:
                    try:
                        time.sleep(0.05)
                    except Exception:
                        return

        import threading
        threading.Thread(target=_task, daemon=True).start()
