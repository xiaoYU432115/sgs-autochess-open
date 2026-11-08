# -*- coding: utf-8 -*-
"""『即时换肤』页(v4.87★) —— 资源替换的第二套机制。

两套机制并存, 同一条资源只走一套:
  url    : 加载前改地址(老机制, 稳, 但已经画在屏幕上的那张要等下次加载才变)
  inject : 直接换已显示对象身上的纹理(当场就变, 还能挂点击播语音)

开关分三层, 从上到下:
  ① 总开关(config res_inject.on)   关 = 所有 inject 规则降级成 url, 和没这功能时一样
  ② 类别默认(config res_inject.modes)  背景走注入、棋子走 URL 这种搭配
  ③ 每条规则自己的 mode 字段        优先级最高

★ 为什么默认全 url: 存量规则是用户一条条配出来的, 新增机制不能一上来就改它们的活法。
"""

import os
import re
import json
import time
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from core import resource_rules as RR
from core import config as CFG

# 界面上能选模式的类别(顺序 = 用户关心的程度)
KINDS = ["自走棋背景", "主公立绘", "主公立绘(旧路径)",
         "棋子立绘", "OL大图", "背景", "音效", "其他"]


from ui.widgets import ThemeMixin   # v4.109★ 主题色 getter 统一到 widgets.ThemeMixin


class InjectTabBuilder(ThemeMixin):
    def __init__(self, app):
        self.app = app
        self.root = app.root
        self._kind_vars = {}      # 类别 → StringVar("url"/"inject")
        self._on_var = None       # 总开关
        self._rows = {}           # 表格 iid → 规则 dict

    # v4.109★ 主题色 getter 已统一到 widgets.ThemeMixin(本类继承它), 不再各写一份

    def log(self, msg, level="INFO"):
        if hasattr(self.app, "log"):
            self.app.log("[即时换肤] " + msg, level=level)
        else:
            print("[即时换肤] " + msg)

    def _status(self, msg):
        if hasattr(self, "status_var"):
            self.status_var.set(msg)
        self.log(msg)

    # ---------- 配置读写 ----------
    def _cfg(self):
        try:
            c = CFG.load_config() or {}
        except Exception:
            c = {}
        ri = c.get("res_inject") or {}
        return c, ri

    def _save_cfg(self, ri):
        c, _ = self._cfg()
        c["res_inject"] = ri
        try:
            CFG.save_config(c)
        except Exception as e:
            self._status("保存配置失败: %s" % e)
            return False
        return True

    # ---------- 构建 ----------
    def build(self, parent):
        mk = getattr(self.app, "_make_scrollable_tab", None)
        if mk:
            try:
                wrap = mk(parent, pady=8, hscroll=True)
            except TypeError:
                wrap = mk(parent, pady=8)
        else:
            wrap = tk.Frame(parent, bg=self._bg())
            wrap.pack(fill="both", expand=True, padx=10, pady=8)

        _c, ri = self._cfg()
        on = bool(ri.get("on"))
        modes = ri.get("modes") or {}

        tk.Label(wrap, text=("两套机制: URL 替换(加载前改地址) / 即时注入(直接换屏幕上的纹理)。\n"
                             "同一条资源只走一套 —— 走注入的, URL 替换自动让开, 不会换两次。\n"
                             "注入的好处: 当场就变、不用等下次加载, 还能点击播语音。\n"
                             "三步: ①『＋挑一个来换』选一张图(或把已有规则改成即时换肤) → "
                             "②(可选)填语音地址 → ③『一键: 配好并同步』。\n"
                             "找不到想换的? 去『资源替换』页点『扫描游戏页资源』(先在游戏里逛一圈皮肤), "
                             "清单里就有了。图集是一对的(xxx.atlas + xxx.png), 换 .png 那半才有效。"),
                 bg=self._bg(), fg=self._fg(), font=(self._fh(), 9),
                 justify="left").pack(anchor="w", padx=10, pady=(8, 6))

        # ① 总开关
        self._on_var = tk.BooleanVar(value=on)
        tk.Checkbutton(wrap, text="启用即时注入(关掉 = 全部退回 URL 替换, 和以前一模一样)",
                       variable=self._on_var, command=self._save_all,
                       bg=self._bg(), fg=self._gold(), selectcolor=self._bg(),
                       activebackground=self._bg(), activeforeground=self._gold(),
                       font=(self._fh(), 9, "bold"), anchor="w").pack(fill="x", padx=10, pady=(0, 8))

        # ② 类别默认模式
        box = tk.LabelFrame(wrap, text="按类别选走哪套(每条规则可在下面单独改)",
                            bg=self._bg(), fg=self._blue(), font=(self._fh(), 9))
        box.pack(fill="x", padx=10, pady=(0, 8))
        for k in KINDS:
            row = tk.Frame(box, bg=self._bg())
            row.pack(fill="x", anchor="w")
            v = tk.StringVar(value=(modes.get(k) or RR.MODE_URL))
            self._kind_vars[k] = v
            tk.Label(row, text=k, bg=self._bg(), fg=self._fg(),
                     font=(self._fh(), 9), width=18, anchor="w").pack(side="left")
            tk.Radiobutton(row, text="URL 替换", value=RR.MODE_URL, variable=v,
                           command=self._save_all, bg=self._bg(), fg=self._fg(),
                           selectcolor=self._bg(), activebackground=self._bg(),
                           font=(self._fh(), 9)).pack(side="left")
            tk.Radiobutton(row, text="即时注入", value=RR.MODE_INJECT, variable=v,
                           command=self._save_all, bg=self._bg(), fg=self._gold(),
                           selectcolor=self._bg(), activebackground=self._bg(),
                           font=(self._fh(), 9)).pack(side="left")

        # ③ 走注入的规则
        lst = tk.LabelFrame(wrap, text="走即时注入的规则", bg=self._bg(),
                            fg=self._blue(), font=(self._fh(), 9))
        lst.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        cols = ("name", "pattern", "voice")
        self.tree = ttk.Treeview(lst, columns=cols, show="headings", height=7)
        for c, w, t in (("name", 150, "名称"), ("pattern", 240, "匹配串"), ("voice", 220, "语音")):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=6, pady=6)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        # v4.106★ 用户原话"即时换肤也不是很友好" —— 三条一起治:
        #   (a) 找不到想换的: 这页以前只显示"已经设成注入"的规则(刚装是空的), 所以给两个入口:
        #       ① 从扫到的官方皮肤资源里挑一个直接建; ② 把已有规则改成走注入。
        #   (b) 不知道生效没: 直接问游戏页要回执(换了几张 / 总开关开没开)。
        #   (c) 步骤太多: 一键 = 设注入 + 开开关 + 同步 + 立刻查回执。
        bar0 = tk.Frame(lst, bg=self._bg())
        bar0.pack(fill="x", padx=6, pady=(0, 6))
        tk.Button(bar0, text="＋ 配一条换肤(向导)", command=self._wizard,
                  bg=self._act(), fg=self._green(), font=(self._fh(), 9, "bold"),
                  padx=8, cursor="hand2").pack(side="left")
        tk.Button(bar0, text="把已有规则改成即时换肤", command=self._convert_existing,
                  bg=self._btn(), fg=self._fg(), font=(self._fh(), 9),
                  padx=8, cursor="hand2").pack(side="left", padx=(6, 0))
        tk.Button(bar0, text="问游戏页: 换了几张?", command=self._ask_page,
                  bg=self._btn(), fg=self._blue(), font=(self._fh(), 9),
                  padx=8, cursor="hand2").pack(side="left", padx=(6, 0))
        tk.Button(bar0, text="一键: 配好并同步", command=self._one_click,
                  bg=self._act(), fg=self._gold(), font=(self._fh(), 9, "bold"),
                  padx=8, cursor="hand2").pack(side="right")

        # 语音地址
        vr = tk.Frame(wrap, bg=self._bg())
        vr.pack(fill="x", padx=10, pady=(0, 6))
        tk.Label(vr, text="语音地址(选中上面一条再填):", bg=self._bg(), fg=self._fg(),
                 font=(self._fh(), 9)).pack(side="left")
        self.voice_var = tk.StringVar(value="")
        tk.Entry(vr, textvariable=self.voice_var, width=52,
                 font=(self._fh(), 9)).pack(side="left", padx=6)
        tk.Button(vr, text="保存语音", command=self._save_voice, bg=self._btn(),
                  fg=self._green(), font=(self._fh(), 9), padx=8,
                  cursor="hand2").pack(side="left")
        tk.Button(vr, text="清空", command=lambda: (self.voice_var.set(""), self._save_voice()),
                  bg=self._btn(), fg=self._dim(), font=(self._fh(), 9),
                  padx=6, cursor="hand2").pack(side="left", padx=(4, 0))

        # 语音从哪来 —— 两个探测按钮 + 结果列表
        vf = tk.Frame(wrap, bg=self._bg())
        vf.pack(fill="x", padx=10, pady=(0, 6))
        tk.Button(vf, text="扫描游戏语音(推荐)", command=self._do_scan_voice,
                  bg=self._act(), fg=self._green(), font=(self._fh(), 9, "bold"),
                  padx=8, cursor="hand2").pack(side="left")
        tk.Button(vf, text="按标识探测", command=self._do_probe_voice, bg=self._btn(),
                  fg=self._fg(), font=(self._fh(), 9), padx=8,
                  cursor="hand2").pack(side="left", padx=(6, 0))
        tk.Button(vf, text="用选中的", command=self._use_picked_voice, bg=self._btn(),
                  fg=self._gold(), font=(self._fh(), 9), padx=8,
                  cursor="hand2").pack(side="left", padx=(6, 0))
        self.vlist = tk.Listbox(vf, height=4, bg="#1e1e1e", fg=self._fg(),
                                font=(self._fh(), 8), width=60)
        self.vlist.pack(side="left", fill="x", expand=True, padx=(6, 0))
        tk.Label(vf, text="① 游戏里点一下武将/技能(语音才会被加载)\n② 回来点『扫描游戏语音』",
                 bg=self._bg(), fg=self._dim(), font=(self._fh(), 8),
                 justify="left").pack(side="left", padx=(6, 0))

        # 操作按钮
        bar = tk.Frame(wrap, bg=self._bg())
        bar.pack(fill="x", padx=10, pady=(0, 6))
        tk.Button(bar, text="改回 URL 替换", command=self._to_url, bg=self._btn(),
                  fg=self._fg(), font=(self._fh(), 9), padx=8,
                  cursor="hand2").pack(side="left")
        tk.Button(bar, text="刷新", command=self.refresh, bg=self._btn(), fg=self._fg(),
                  font=(self._fh(), 9), padx=8, cursor="hand2").pack(side="left", padx=(6, 0))
        tk.Button(bar, text="配管宁墨韵荷香(棋盘背景)", command=self._sample_guanning,
                  bg=self._act(), fg=self._gold(), font=(self._fh(), 9, "bold"),
                  padx=8, cursor="hand2").pack(side="left", padx=(6, 0))
        tk.Button(bar, text="查换之前那张", command=self._do_find_original, bg=self._btn(),
                  fg=self._blue(), font=(self._fh(), 9), padx=8,
                  cursor="hand2").pack(side="left", padx=(6, 0))
        tk.Button(bar, text="探测动态帧(配成会动的)", command=self._do_probe_frames,
                  bg=self._act(), fg=self._gold(), font=(self._fh(), 9, "bold"),
                  padx=8, cursor="hand2").pack(side="left", padx=(6, 0))
        tk.Button(bar, text="同步到浏览器", command=self._sync, bg=self._act(),
                  fg=self._gold(), font=(self._fh(), 9, "bold"), padx=8,
                  cursor="hand2").pack(side="right")

        self.status_var = tk.StringVar(value="")
        tk.Label(wrap, textvariable=self.status_var, bg=self._bg(), fg=self._dim(),
                 font=(self._fh(), 9), anchor="w", justify="left",
                 wraplength=820).pack(fill="x", padx=10, pady=(0, 10))

        self.refresh()
        if not on:
            self._status("当前【没开总开关】—— 所有规则仍走 URL 替换, 老功能不受影响。")

    # ---------- 刷新 ----------
    def refresh(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        self._rows = {}
        _c, ri = self._cfg()
        modes = ri.get("modes") or {}
        for r in RR.load_rules():
            if str(r.get("mode") or "").strip().lower() != RR.MODE_INJECT:
                continue
            iid = self.tree.insert("", "end", values=(
                r.get("name") or "", r.get("pattern") or "", r.get("voice") or ""))
            self._rows[iid] = r
        self._status("走注入的规则 %d 条(总开关 %s)" %
                     (len(self._rows), "开" if ri.get("on") else "关"))

    def _on_select(self, _e=None):
        sel = self.tree.selection()
        if not sel:
            return
        r = self._rows.get(sel[0]) or {}
        self.voice_var.set(str(r.get("voice") or ""))

    # ---------- 保存 ----------
    def _save_all(self):
        _c, ri = self._cfg()
        ri["on"] = bool(self._on_var.get())
        modes = dict(ri.get("modes") or {})
        for k, v in self._kind_vars.items():
            modes[k] = v.get()
        ri["modes"] = modes
        self._save_cfg(ri)
        self._status("已保存(总开关 %s)。改完点『同步到浏览器』。" % ("开" if ri["on"] else "关"))

    def _save_voice(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("语音", "先在上面选中一条规则。")
            return
        r = self._rows.get(sel[0]) or {}
        pat = str(r.get("pattern") or "")
        if not pat:
            return
        v = self.voice_var.get().strip()
        rules = RR.load_rules()
        for x in rules:
            if str(x.get("pattern") or "") == pat:
                x["voice"] = v
        RR.save_rules(rules)
        self.refresh()
        self._status("语音已%s —— 点『同步到浏览器』生效。" % ("填上" if v else "清空"))

    def _to_url(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("改回 URL", "先选中一条规则。")
            return
        r = self._rows.get(sel[0]) or {}
        pat = str(r.get("pattern") or "")
        rules = RR.load_rules()
        for x in rules:
            if str(x.get("pattern") or "") == pat:
                x["mode"] = RR.MODE_URL
                x["voice"] = ""
        RR.save_rules(rules)
        self.refresh()
        self._status("已改回 URL 替换(这条以后走老机制)。")

    # ---------- 样本: 管宁「墨韵荷香」 ----------
    def _sample_guanning(self):
        url = "https://web.sanguosha.com/10/pc/res/assets/runtime/general/big/static/6195001.png"
        pat = RR.relative_pattern("tavernChess/tavernChessBg.png")
        rules = RR.load_rules()
        hit = False
        for x in rules:
            if str(x.get("pattern") or "") == pat:
                x["mode"] = RR.MODE_INJECT
                x["name"] = x.get("name") or "管宁-墨韵荷香(棋盘背景)"
                hit = True
        if not hit:
            import time
            rules.append({
                "id": "inj%d" % int(time.time() * 1000),
                "name": "管宁-墨韵荷香(棋盘背景)",
                "kind": "自走棋背景",
                "pattern": pat,
                "target": url,
                "mode": RR.MODE_INJECT,
                "voice": "",
                "enabled": True,
                "season": "",
            })
        RR.save_rules(rules)
        # 顺手把总开关和这个类别打开, 否则配了也不生效(用户最容易卡在这一步)
        _c, ri = self._cfg()
        ri["on"] = True
        modes = dict(ri.get("modes") or {})
        modes["自走棋背景"] = RR.MODE_INJECT
        ri["modes"] = modes
        self._save_cfg(ri)
        if self._on_var:
            self._on_var.set(True)
        if "自走棋背景" in self._kind_vars:
            self._kind_vars["自走棋背景"].set(RR.MODE_INJECT)
        self.refresh()
        self._status("已配: 棋盘背景 → 管宁墨韵荷香(即时注入)。语音地址在上方填, 再点同步。")

    @staticmethod
    def _wait_cmd(st, cid, timeout=10.0):
        """等脚本回传执行结果(轮询 take_result) —— 超时返回 None。"""
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                r = st.take_result(cid)
            except Exception:
                return None
            if r is not None:
                return r
            time.sleep(0.15)
        return None

    # ---------- 探测: 语音地址从哪来 ----------
    # 锦囊袋那套是硬编码的(从它代码里抠出来的正则):
    #   ^https://web.sanguosha.com/220/h5_2/res/runtime/pc/voice/spell/<英文段>/<英文段>.mp3$
    # 两段都是 [A-Za-z0-9_.-] —— 是【英文/拼音标识】, 不是资源编号(拿编号试过, 全 404)。
    # 所以这里有两条路: ① 让浏览器把自己加载过的 mp3 交回来(零猜测, 准);
    #                  ② 用户给个标识(拼音), 拼候选去问服务器在不在。
    VOICE_BASE = "https://web.sanguosha.com/220/h5_2/res/runtime/pc/voice/spell"
    VOICE_RE = "/220/h5_2/res/runtime/pc/voice/spell/"

    def _voice_candidates(self, key):
        k = re.sub(r"[^A-Za-z0-9_.-]", "", str(key or ""))
        if not k:
            return []
        out = []
        for tail in (k, "1", "0", "daiji", "beijing", k + "_1"):
            out.append("%s/%s/%s.mp3" % (self.VOICE_BASE, k, tail))
        out.append("%s/%s.mp3" % (self.VOICE_BASE, k))
        return out

    def _push_voice_hits(self, hits, what):
        self.vlist.delete(0, "end")
        for h in hits:
            self.vlist.insert("end", h)
        if not hits:
            self._status("%s: 一个都没探到 —— 换『扫描游戏语音』(在游戏里点一下武将/技能,"
                         "让浏览器自己交出地址)最准。" % what)
        else:
            self._status("%s: 探到 %d 个, 选中一行点『用选中的』。" % (what, len(hits)))

    def _do_probe_voice(self):
        from tkinter import simpledialog
        key = simpledialog.askstring(
            "探测语音",
            "输入英文标识(拼音/目录名), 例: guanning / shenmachao。\n"
            "会按官方规则 spell/<标识>/<名>.mp3 拼几个候选去问服务器在不在。",
            parent=self.root)
        if not key:
            return
        jobs = self._voice_candidates(key)
        self._status("正在探测 %d 个语音地址(HEAD, 不下载)…" % len(jobs))

        def _work():
            from concurrent.futures import ThreadPoolExecutor
            hits = []
            try:
                with ThreadPoolExecutor(max_workers=6) as ex:
                    for u, rr in zip(jobs, ex.map(RR.probe_res_url, jobs)):
                        if rr and rr.get("ok"):
                            hits.append(u)
            except Exception as e:
                self.root.after(0, self._status, "探测出错: %s" % e)
                return
            hits = [h for h in hits if h]
            self.root.after(0, lambda: self._push_voice_hits(hits, "按标识探测"))
        threading.Thread(target=_work, daemon=True).start()

    def _do_scan_voice(self):
        """让浏览器把【它真的加载过】的 mp3 交回来 —— 零猜测。

        用法: 先在游戏里点一下那个武将/皮肤/技能(语音才会被加载), 再回来点这个按钮。
        """
        try:
            from core import bridge as _BR
            st = getattr(_BR, "STATE", None)
        except Exception as e:
            messagebox.showinfo("扫描游戏语音", "读不到桥接: %s" % e)
            return
        if st is None:
            messagebox.showinfo("扫描游戏语音", "助手没在跑桥接 —— 先让助手跑起来并开着游戏页。")
            return
        code = ("(function(){try{if(!window.__sgsResSwap||!window.__sgsResSwap.dumpRes)"
                "return 'NO_SCRIPT';return JSON.stringify(window.__sgsResSwap.dumpRes());}"
                "catch(e){return 'ERR:'+e.message;}})()")
        try:
            cid = st.send_cmd("js", code=code)
        except Exception as e:
            self._status("下发扫描失败: %s" % e)
            return
        self._status("已让浏览器交出加载过的地址, 等回话…")

        def _work():
            res = self._wait_cmd(st, cid, 10.0)
            if res is None:
                self.root.after(0, self._status, "浏览器没回话(游戏页开着吗? 脚本装着吗?)")
                return
            s = str(res)
            if s.startswith("NO_SCRIPT"):
                self.root.after(0, self._status, "页面上没有换肤脚本 —— 到『资源替换』页重新装/更新脚本。")
                return
            try:
                obj = json.loads(s)
            except Exception:
                obj = []
            arr = (obj.get("urls") if isinstance(obj, dict) else obj) or []
            hits, other = [], []
            for u in arr:
                u = str(u or "")
                if not u.lower().endswith(".mp3"):
                    continue
                (hits if self.VOICE_RE in u else other).append(u)
            hits = hits + other
            self.root.after(0, lambda: self._push_voice_hits(hits, "扫描游戏语音"))
        threading.Thread(target=_work, daemon=True).start()

    def _use_picked_voice(self):
        sel = self.vlist.curselection()
        if not sel:
            messagebox.showinfo("语音", "先在列表里选中一行。")
            return
        self.voice_var.set(self.vlist.get(sel[0]))
        self._save_voice()

    # ---------- 查"换之前那张"(原地址) ----------
    def _do_find_original(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("查原地址", "先在上面选中一条规则。")
            return
        r = self._rows.get(sel[0]) or {}
        pat = str(r.get("pattern") or "")
        if not pat:
            return
        cands = []
        try:
            cands = RR.official_url_candidates(pat) or []
        except Exception as e:
            self.log("取官方候选地址失败: %s" % e)
        if not cands:
            try:
                cands = [RR.official_target_url(pat)]
            except Exception:
                cands = []
        self._status("正在问官方服务器: %d 个候选地址哪个真的存在…" % len(cands))

        def _work():
            from concurrent.futures import ThreadPoolExecutor
            found = []
            try:
                with ThreadPoolExecutor(max_workers=4) as ex:
                    for u, rr in zip(cands, ex.map(RR.probe_res_url, cands)):
                        if rr and rr.get("ok"):
                            found.append((u, rr.get("w"), rr.get("h")))
            except Exception as e:
                self.root.after(0, self._status, "探测出错: %s" % e)
                return
            self.root.after(0, lambda: self._show_original(pat, cands, found))
        threading.Thread(target=_work, daemon=True).start()

    def _show_original(self, pat, cands, found):
        if not found:
            self._status("换之前的原地址没探到(试了 %d 个: %s)。\n"
                         "这类资源多半是【游戏内生成/图集里切的】, 官方没有独立文件 —— "
                         "那就用『资源替换』页的『扫描游戏资源』, 让浏览器把真实地址交回来。"
                         % (len(cands), ", ".join(cands[:2]) or "-"))
            return
        u, w, h = found[0]
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(u)
            tail = "（已复制到剪贴板）"
        except Exception:
            tail = ""
        self._status("换之前那张 = %s (%sx%s)%s\n匹配串: %s"
                     % (u, w or "?", h or "?", tail, pat))

    # ---------- 配成"会动"的(多帧轮播) ----------
    def _do_probe_frames(self):
        """锦囊袋换完是【动态】的 —— 它换的是动态皮肤那几帧。

        这里照做: 给个大图编号, 探测 beijing/daiji/daiji_2… 哪些真存在,
        存在的几帧配成一条【多帧轮播】规则(走注入) → 当场换、而且会动。
        """
        from tkinter import simpledialog
        raw = simpledialog.askstring(
            "探测动态帧",
            "输入大图编号(例: 621401 = 神马超; 动态版 = 编号+200000)。\n"
            "会探 beijing/daiji/daiji_2 等常见帧, 存在的配成动态。",
            parent=self.root)
        if not raw:
            return
        ids = re.findall(r"\d{5,9}", str(raw))[:4]
        if not ids:
            messagebox.showinfo("探测动态帧", "没认出编号(纯数字, 例 621401)。")
            return
        jobs = []
        for i in ids:
            jobs += RR.big_id_candidates(i, with_neighbors=False)
        self._status("正在探测 %d 个地址…" % len(jobs))

        def _work():
            from concurrent.futures import ThreadPoolExecutor
            hits = []
            try:
                with ThreadPoolExecutor(max_workers=6) as ex:
                    for u, rr in zip(jobs, ex.map(RR.probe_res_url, jobs)):
                        if rr and rr.get("ok"):
                            hits.append(u)
            except Exception as e:
                self.root.after(0, self._status, "探测出错: %s" % e)
                return
            self.root.after(0, lambda: self._frames_report(ids, hits))
        threading.Thread(target=_work, daemon=True).start()

    def _frames_report(self, ids, hits):
        frames = [u for u in hits if "/dynamic/" in u]
        if len(frames) < 2:
            self._status("探到 %d 个地址, 但动态帧只有 %d 个(要 ≥2 帧才动得起来) —— "
                         "这个编号可能是静态皮肤, 换一个编号试试。" % (len(hits), len(frames)))
            return
        sel = self.tree.selection()
        if not sel:
            self._status("探到 %d 帧可用: %s\n在『资源替换』页给目标资源配规则后, 再来这里勾即时注入。"
                         % (len(frames), ", ".join(u.rsplit("/", 1)[-1] for u in frames)))
            return
        r = self._rows.get(sel[0]) or {}
        rules = RR.load_rules()
        for x in rules:
            if str(x.get("pattern") or "") == str(r.get("pattern") or ""):
                x["mode"] = RR.MODE_INJECT
                x["target"] = frames[0]
                x["animate"] = {"urls": frames, "ms": 500}
        RR.save_rules(rules)
        self.refresh()
        self._status("已把选中的规则配成【%d 帧轮播】(即时注入) —— 当场换皮且会动, 点同步生效。"
                     % len(frames))

    # ---------- v4.106★ 即时换肤"不友好"三连改 ----------
    def _pick_dialog(self, title, items, lab, on_pick, hint=""):
        """通用「搜一下 + 双击选中」小窗 —— 几百条里翻一条等于没有, 所以一律带过滤。"""
        top = tk.Toplevel(self.root)
        top.title(title)
        top.configure(bg=self._bg())
        top.geometry("760x440")
        tk.Label(top, text=("输入关键字过滤(名字/编号/地址), 双击一行就用它"),
                 bg=self._bg(), fg=self._fg(), font=(self._fh(), 9)).pack(anchor="w", padx=8, pady=(8, 2))
        if hint:
            tk.Label(top, text=hint, bg=self._bg(), fg=self._dim(),
                     font=(self._fh(), 8), justify="left").pack(anchor="w", padx=8)
        sv = tk.StringVar(value="")
        e = tk.Entry(top, textvariable=sv, font=(self._fh(), 10))
        e.pack(fill="x", padx=8, pady=(0, 4))
        lb = tk.Listbox(top, bg="#1e1e1e", fg=self._fg(), font=(self._fh(), 9))
        lb.pack(fill="both", expand=True, padx=8, pady=(0, 6))
        shown = []

        def _fill():
            q = sv.get().strip().lower()
            lb.delete(0, "end")
            shown[:] = []
            for it in items:
                line = lab(it)
                if q and q not in str(line).lower():
                    continue
                lb.insert("end", line)
                shown.append(it)
                if len(shown) >= 500:
                    break

        def _ok(_e=None):
            sel = lb.curselection()
            if not sel:
                return
            it = shown[sel[0]]
            top.destroy()
            on_pick(it)

        sv.trace_add("write", lambda *_a: _fill())
        lb.bind("<Double-1>", _ok)
        lb.bind("<Return>", _ok)
        _fill()
        tk.Button(top, text="用它", command=_ok, bg=self._act(), fg=self._gold(),
                  font=(self._fh(), 10, "bold"), padx=12, cursor="hand2").pack(pady=(0, 8))
        e.focus_set()

    def _make_inject_rule(self, url="", name="", kind="", rule=None, pattern=""):
        """把一条资源变成【走即时注入】的规则(已存在就改模式, 没有就新建), 并顺手开开关。

        为什么连开关一起开: 用户最容易卡的就是"规则配好了, 但总开关/这个类别还是 URL" ——
        那样配了也不生效, 而界面上看不出为什么。
        """
        # v4.106★ 显式的「换掉哪张」优先 —— 换肤是 A→B, 拿 B 的地址反推 A 只在"没有 A"时才对
        pat = (str(pattern or "").strip()
               or (RR.relative_pattern(str(url or "")) if url else "")
               or str((rule or {}).get("pattern") or ""))
        if not pat:
            self._status("这条没有匹配串, 配不了 —— 换个有地址的。")
            return None
        url = str(url or ((rule or {}).get("target") or ""))
        if not kind:
            try:
                kind, _note = RR.res_kind(pat)
            except Exception:
                kind = str((rule or {}).get("kind") or "")
        rules = RR.load_rules()
        tgt = None
        for x in rules:
            if str(x.get("pattern") or "") == pat:
                x["mode"] = RR.MODE_INJECT
                if url:
                    x["target"] = url
                if name:
                    x["name"] = name
                tgt = x
                break
        if tgt is None:
            import os
            import time as _t
            tgt = {"id": "inj%d" % int(_t.time() * 1000),
                   "name": name or os.path.basename(pat),
                   "kind": kind or "", "pattern": pat, "target": url,
                   "mode": RR.MODE_INJECT, "voice": "", "enabled": True, "season": ""}
            rules.append(tgt)
        RR.save_rules(rules)
        _c, ri = self._cfg()
        ri["on"] = True
        modes = dict(ri.get("modes") or {})
        if tgt.get("kind"):
            modes[tgt["kind"]] = RR.MODE_INJECT
        ri["modes"] = modes
        self._save_cfg(ri)
        try:
            self._on_var.set(True)
            if tgt.get("kind") in self._kind_vars:
                self._kind_vars[tgt["kind"]].set(RR.MODE_INJECT)
        except Exception as e:
            sys.stderr.write("[即时换肤] 同步开关到界面失败: %s\n" % e)
        self.refresh()
        return tgt

    def _wizard(self):
        """(a)【规则从哪来】—— 一条换肤 = 「换掉哪张」+「换成哪张」两件事, 一屏填完。

        用户问: "及时换肤里面没有规则? 那规则哪里来啊, 只有那一个?"
        答: 那一个是示例(管宁墨韵荷香)。真规则得自己配, 而配一条只要两样东西 ——
          ① 换成哪张(新图): 皮肤表 5300+ 套 OL 大图 / 本地图片 / 直接贴网址;
          ② 换掉哪张(游戏里现在用的那张, 叫匹配串): 从扫到的官方资源挑 / 从已有规则挑 / 手填。
        以前这页只显示"已经设成注入"的规则(新装是空的), 又没给配的地方, 所以看着像"没规则"。
        """
        top = tk.Toplevel(self.root)
        top.title("配一条换肤(向导)")
        top.configure(bg=self._bg())
        top.geometry("880x330")
        url_var = tk.StringVar(value="")
        pat_var = tk.StringVar(value="")
        nm_var = tk.StringVar(value="")

        def _tip(s, color=None):
            tip.configure(text=s, fg=color or self._dim())

        def row(title, var, btns):
            f = tk.Frame(top, bg=self._bg())
            f.pack(fill="x", padx=10, pady=(8, 0))
            tk.Label(f, text=title, bg=self._bg(), fg=self._gold(),
                     font=(self._fh(), 9, "bold"), width=6, anchor="w").pack(side="left")
            tk.Entry(f, textvariable=var, width=62, font=(self._fh(), 9)).pack(side="left", padx=6)
            for t, c in btns:
                tk.Button(f, text=t, command=c, bg=self._btn(), fg=self._fg(),
                          font=(self._fh(), 9), padx=6, cursor="hand2").pack(side="left", padx=(4, 0))

        def _dress():
            # v4.110★ 【真 bug】: 以前必须"先打开过『资源替换』页"才能用 —— 选择器是那个页
            #   实例上的方法, 那页没建过就是 None → 只能弹框把人支到另一页去点一下。
            #   用户原话: "及时换肤tab中配一条从皮肤ol挑选有bug、不行"。
            #   选择器内部只用到主题取色(ThemeMixin 的 _bg/_fg/_gold… 那几个), 而本页
            #   同样继承 ThemeMixin → 直接拿【自己】当宿主调用, 不再看那个页建没建。
            # v4.110★ 【真 bug 的根因】: 以前写的是 `from ui.resource_tab import ResourceTab`,
            #   而那个类的真名是 ResourceTabBuilder → 这一行【每次都 ImportError】
            #   ("cannot import name 'ResourceTab'"), 直接落到下面的 "皮肤表选择器加载失败",
            #   表现出来就是「从 OL 皮肤表挑」点了没反应(用户原话: "及时注入皮肤加载器失败")。
            #   现在改成按"谁带 _dress_picker"来找类 —— 以后再改名也不会炸。
            try:
                from ui import resource_tab as _RT
            except Exception as e:
                _tip("皮肤表模块加载失败: %s" % e, self._gold())
                return
            _picker = None
            for _nm in ("ResourceTabBuilder", "ResourceTab", "ResTabBuilder"):
                _cls = getattr(_RT, _nm, None)
                if _cls is not None and hasattr(_cls, "_dress_picker"):
                    _picker = _cls._dress_picker
                    break
            if _picker is None:                      # 名字全对不上就扫一遍模块里的类
                for _nm in dir(_RT):
                    _cls = getattr(_RT, _nm, None)
                    if isinstance(_cls, type) and hasattr(_cls, "_dress_picker"):
                        _picker = _cls._dress_picker
                        break
            if _picker is None:
                _tip("皮肤表选择器没找到 —— 模块里没有带 _dress_picker 的类。", self._gold())
                return
            b = getattr(self.app, "_resource_builder", None)
            if not (b and hasattr(b, "_dress_picker")):
                b = self

            def _got(it):
                u = str(it.get("url") or it.get("pattern") or "")
                if not u:
                    _tip("这套在官方服务器上一张都没探到 —— 换一套试试", self._gold())
                    return
                url_var.set(u)
                nm_var.set(str(it.get("name") or ""))
                _tip("新图已填: %s" % u)

            try:
                _picker(b, self.root, _got)
            except Exception as e:
                _tip("皮肤表打开失败: %s" % e, self._gold())

        def _local():
            from tkinter import filedialog
            p = filedialog.askopenfilename(
                parent=top, title="选一张本地图片",
                filetypes=[("图片", "*.png;*.jpg;*.jpeg;*.gif;*.webp"), ("所有", "*.*")])
            if p:
                url_var.set(p)
                _tip("新图已填(本地文件): %s" % p)

        def _scanned():
            items = [x for x in (RR.load_official_resources() or [])
                     if str(x.get("url") or x.get("pattern") or "").strip()]
            if not items:
                messagebox.showinfo("从扫到的资源挑",
                                    "扫到的官方资源是空的。\n\n做法: 游戏里逛一圈(看皮肤/打一局) → "
                                    "助手『资源替换』页点『扫描游戏页资源』 → 再回来。")
                return

            def _go(it):
                p = str(it.get("pattern") or RR.relative_pattern(str(it.get("url") or "")))
                pat_var.set(p)
                _tip("被换掉的已填: %s —— 现在填上面『换成』那一行" % p, self._gold())

            # v4.110★ 用户原话: "下面从扫的资源替换、资源不全啊, 是我们扫的就是不全、还是没拿到?"
            #   答: 不是漏拿 —— 清单只收【浏览器真的请求过】的资源, 游戏没加载过的永远进不来。
            #   把这句写在框里, 免得每次都猜"是不是助手没扫全"。
            hint = ("这是【现在用的那张】, 不是新图 —— 新图填在上面『换成』。\n"
                    "清单现在共 %d 条, 只收游戏【真加载过】的资源: 没打开过的皮肤/界面不会在"
                    "这里面(不是漏扫, 是游戏没请求过)。想换的这儿没有 → 在游戏里逛到它再回来"
                    "扫一遍, 或者改用『从皮肤表挑』(5300+ 套全表, 不靠扫描)。" % len(items))
            self._pick_dialog("换掉哪张(游戏里现在那张)", items,
                              lambda it: "%s  |  %s" % (it.get("kind") or "",
                                                        it.get("pattern") or it.get("url") or ""),
                              _go, hint=hint)

        def _existing():
            rules = list(RR.load_rules() or [])
            if not rules:
                messagebox.showinfo("从已有规则挑", "还没有规则 —— 先在『资源替换』页建一条。")
                return

            def _go(r):
                pat_var.set(str(r.get("pattern") or ""))
                nm_var.set(str(r.get("name") or ""))
                _tip("被换掉的已填: %s(拿它的匹配串)" % (r.get("pattern") or ""))

            self._pick_dialog("从已有规则挑(拿它的匹配串)", rules,
                              lambda r: "%s  |  %s" % (r.get("name") or "", r.get("pattern") or ""),
                              _go)

        row("换成", url_var, [("从皮肤表挑(OL大图)", _dress), ("选本地图片", _local)])
        row("换掉", pat_var, [("从扫到的资源挑", _scanned), ("从已有规则挑", _existing)])
        row("名字", nm_var, [])
        tip = tk.Label(top, text="", bg=self._bg(), fg=self._dim(),
                       font=(self._fh(), 8), justify="left", anchor="w", wraplength=840)
        tip.pack(fill="x", padx=10, pady=(10, 0))
        _tip("①『换成』= 想要的新图(皮肤表 5300+ 套 / 本地图片 / 贴网址); "
             "②『换掉』= 游戏里现在用的那张(不知道就点『从扫到的资源挑』)。")

        def _ok():
            u = url_var.get().strip()
            p = pat_var.get().strip()
            if not u or not p:
                _tip("两行都得填: 上面『换成』(新图) 和 下面『换掉』(现在那张)。", self._gold())
                return
            top.destroy()
            t = self._make_inject_rule(url=u, name=nm_var.get().strip(), pattern=p)
            if t:
                self._status("已配: %s → %s(即时注入)。点『一键: 配好并同步』让它当场生效。"
                             % (t.get("name") or p, str(u).rsplit("/", 1)[-1]))

        tk.Button(top, text="配好(走即时换肤)", command=_ok, bg=self._act(), fg=self._gold(),
                  font=(self._fh(), 10, "bold"), padx=14, cursor="hand2").pack(pady=14)

    def _convert_existing(self):
        """(a)② 已有规则(资源替换页建过的)改成走即时注入 —— 新装时这页是空的, 这是最快的一条路。"""
        rules = [r for r in RR.load_rules()
                 if str(r.get("mode") or "").strip().lower() != RR.MODE_INJECT]
        if not rules:
            messagebox.showinfo("改成即时换肤", "没有可转的规则 —— 先在『资源替换』页建一条。")
            return

        def _lab(r):
            return "%s  |  %s" % (r.get("name") or "", r.get("pattern") or "")

        def _go(r):
            t = self._make_inject_rule(rule=r)
            if t:
                self._status("已把「%s」改成即时换肤 —— 点『一键: 配好并同步』生效。" % (t.get("name") or ""))

        self._pick_dialog("把已有规则改成即时换肤", rules, _lab, _go,
                          hint="改成即时注入后: 当场就变(不用等下次加载), 还能挂语音")

    def _one_click(self):
        """(c) 一键: 设注入 → 开总开关 → 同步到浏览器 → 立刻问游戏页要回执。"""
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("一键应用", "先选中上面一条规则(或用『＋挑一个来换』新建一条)。")
            return
        r = self._rows.get(sel[0]) or {}
        pat = str(r.get("pattern") or "")
        rules = RR.load_rules()
        for x in rules:
            if str(x.get("pattern") or "") == pat:
                x["mode"] = RR.MODE_INJECT
        RR.save_rules(rules)
        _c, ri = self._cfg()
        ri["on"] = True
        modes = dict(ri.get("modes") or {})
        if r.get("kind"):
            modes[r["kind"]] = RR.MODE_INJECT
        ri["modes"] = modes
        self._save_cfg(ri)
        try:
            self._on_var.set(True)
            if r.get("kind") in self._kind_vars:
                self._kind_vars[r["kind"]].set(RR.MODE_INJECT)
        except Exception as e:
            sys.stderr.write("[即时换肤] 同步开关到界面失败: %s\n" % e)
        self.refresh()
        self._sync()
        self.root.after(1500, self._ask_page)

    def _ask_page(self):
        """(b) 换完到底生效没 —— 直接问游戏页要回执(换了几张)。

        拿不到回执(老脚本没这个字段)就【如实说不知道】, 并给肉眼确认法 —— 不能假装生效。
        """
        ops = getattr(self.app, "ops", None)
        if ops is None:
            self._status("没连上游戏页(看助手状态栏的连接状态)。")
            return
        js = ("(function(){try{var s=window.__sgsResSwap||{};var st=s.stats||{};"
              "return JSON.stringify({on:!!s.on,ver:String(s.ver||''),"
              "n:(s.rules?s.rules.length:0),"
              "injected:(st.injected!=null?st.injected:(s.injected!=null?s.injected:null)),"
              "hit:(st.hit!=null?st.hit:(s.hit!=null?s.hit:null)),"
              "last:String(s.lastSync||s.last||'')});}"
              "catch(e){return JSON.stringify({err:String(e)});}})()")
        try:
            raw = ops.send_recv("eval", js=js, timeout=3.0)
        except Exception as e:
            self._status("问游戏页失败: %s" % e)
            return
        val = raw
        if isinstance(raw, dict):
            for k in ("result", "data", "value", "ret"):
                if k in raw:
                    val = raw[k]
                    break
        try:
            import json
            d = val if isinstance(val, dict) else json.loads(str(val or "{}"))
        except Exception:
            d = {}
        if not isinstance(d, dict):
            d = {}
        got = d.get("injected")
        if got is None:
            got = d.get("hit")
        if d.get("err") or (not d.get("on") and got is None):
            self._status("游戏页那边没回报『换了几张』(多半是脚本版本较旧, 或总开关没开)。\n"
                         "肉眼确认: 游戏里那张图当场就变 = 生效; 没变就先看『同步到浏览器』点过没有。")
            return
        self._status("游戏页回执: 总开关%s · 注入规则 %s 条 · 已换 %s 张%s"
                     % ("开" if d.get("on") else "关", d.get("n") or 0, got or 0,
                        ("  最近同步 " + str(d.get("last"))) if d.get("last") else ""))

    # ---------- 同步 ----------
    def _sync(self):
        b = getattr(self.app, "_resource_builder", None)
        if b and hasattr(b, "_sync_browser"):
            try:
                b._sync_browser()
                self._status("已向游戏页下发(最多 15 秒自动生效)。")
                return
            except Exception as e:
                self._status("同步失败: %s" % e)
                return
        self._status("请到『资源替换』页点『立即同步到浏览器』。")
