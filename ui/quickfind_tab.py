# -*- coding: utf-8 -*-
"""快速找牌 tab —— v4.200★

独立的「刷新找牌」页: 刷新期间拦截商店绘制, 仍接收并检查每次服务端回包;
命中后自动购买, 停止时只把最后一份完整货架数据交回原绘制函数重绘一次。

桥侧入口: window.AutoBuyAndSell.quickFind(params)
  params = { targets:[名字...], maxRefreshes:1000, speed:10, collectAll:false, buyOnMatch:true }
  window.__sgsQuickFindStatus = {running,sent,found,bought,coins,lastMsg,done,why}  (Python 轮询)
  window.__sgsQuickFindStop = true  (手动停)
"""

import json
import threading

import tkinter as tk
from tkinter import ttk, messagebox, Toplevel

from ui.widgets import ThemeMixin
from ui.app import (BG, FG, DIM, RED, GOLD, BLUE, BTN, ACT,
                    SEL_BG, SEL_FG, FH)
from core import id_map as IDMAP
from core import tavern_kb as _KB
from core import csv_loader as _CL
from core import silence as _SIL
from ui.scenario_data import _scenario_spell_names

# v4.184★ 本季武将「技能类型」索引(当先/亡语/召唤…), 供挑选框按类型筛。只算一次并缓存。
_HERO_SKILL_INDEX = None
_HERO_TAG_CURATED = ["当先", "亡语", "召唤", "群体", "护甲", "盾", "毒", "回手", "成长", "经济"]


def _hero_skill_index():
    global _HERO_SKILL_INDEX
    if _HERO_SKILL_INDEX is not None:
        return _HERO_SKILL_INDEX
    name_to_tags, tag_to_names = {}, {}
    try:
        _raw = str(getattr(_CL, "CURRENT_SEASON", "11") or "11").strip().lstrip("sS")
        _season = "s%d" % int(_raw or 11)
    except Exception:
        _season = "s11"
    try:
        chess = _KB.load_chess(_season, shop_only=True, cur_season_only=True)
        for _c in (chess or {}).values():
            _nm = (_c or {}).get("name") or ""
            if not _nm:
                continue
            for _t in (_c.get("tags") or []):
                _t = str(_t).strip()
                if not _t:
                    continue
                name_to_tags.setdefault(_nm, set()).add(_t)
                tag_to_names.setdefault(_t, set()).add(_nm)
    except Exception as _e:
        _SIL.note("ui/quickfind_tab.py:_hero_skill_index", str(_e))
    _HERO_SKILL_INDEX = (name_to_tags, tag_to_names)
    return _HERO_SKILL_INDEX


def _hero_tag_choices():
    """挑选框「技能类型」下拉: {类型: [名字...]}, 只保留本季确实存在的代表性类型。"""
    _, tag_to_names = _hero_skill_index()
    out = {}
    for _t in _HERO_TAG_CURATED:
        _ns = tag_to_names.get(_t)
        if _ns:
            out[_t] = sorted(_ns)
    return out


class QuickFindTabBuilder(ThemeMixin):
    """快速找牌页。app.py 那边只留薄壳(创建 tab 帧 + 调 _build)。"""

    def __init__(self, app):
        self.app = app
        self.root = app.root
        self.targets = []          # 选中的目标卡名字
        self._qf_thread = None
        self._qf_stop = False
        self._qf_running = False

    # ------------------------------------------------------------------ 构建
    def _build(self):
        frame = self.app._make_scrollable_tab(self.app.tab_quickfind, padx=(10, 0), pady=8)

        tk.Label(frame, text="快速找牌", bg=BG, fg=GOLD, font=(FH, 14, "bold")).pack(anchor="w")
        tk.Label(frame,
                 text="找牌期间只隐藏商店区域并暂缓货架重绘；游戏仍接收回包并用新货架识别目标。"
                      "命中后购买，停止时再恢复并重绘最后一份货架。每次刷新必须等到商店更新回包，超时会停止。",
                 bg=BG, fg=DIM, font=(FH, 9), wraplength=980, justify="left").pack(anchor="w", pady=(2, 8))

        # ===== 目标卡 =====
        tgt = tk.LabelFrame(frame, text="目标卡（可多选；出现任意一张就买下并停，也可只选一张）",
                           bg=BG, fg=GOLD, bd=1, relief="solid", font=(FH, 9))
        tgt.pack(fill="x", pady=(0, 8))
        trow = tk.Frame(tgt, bg=BG)
        trow.pack(fill="x", padx=8, pady=6)
        tk.Button(trow, text="挑选", command=self._open_picker, bg=BTN, fg=FG,
                  activebackground=BTN, activeforeground=FG, font=(FH, 9),
                  relief="flat", padx=10, cursor="hand2").pack(side="left")
        tk.Button(trow, text="清空", command=self._clear_targets, bg=BTN, fg=FG,
                  activebackground=BTN, activeforeground=FG, font=(FH, 9),
                  relief="flat", padx=10, cursor="hand2").pack(side="left", padx=(6, 0))
        self.target_count_var = tk.StringVar(value="已选 0 张")
        tk.Label(trow, textvariable=self.target_count_var, bg=BG, fg=DIM, font=(FH, 9)).pack(side="left", padx=(8, 0))

        lst = tk.Frame(tgt, bg=BG)
        lst.pack(fill="x", padx=8, pady=(0, 6))
        self.target_listbox = tk.Listbox(lst, bg=BTN, fg=FG, selectbackground=SEL_BG,
                                         selectforeground=SEL_FG, font=(FH, 9), height=4)
        self.target_listbox.pack(side="left", fill="x", expand=True)
        rmb = tk.Button(lst, text="移除选中", command=self._remove_selected, bg=BTN, fg=FG,
                        activebackground=BTN, activeforeground=FG, font=(FH, 9),
                        relief="flat", padx=8, cursor="hand2")
        rmb.pack(side="left", padx=(6, 0), fill="y")

        # ===== 选项 =====
        opt = tk.LabelFrame(frame, text="选项", bg=BG, fg=GOLD, bd=1, relief="solid", font=(FH, 9))
        opt.pack(fill="x", pady=(0, 8))

        # 上限
        r1 = tk.Frame(opt, bg=BG)
        r1.pack(fill="x", padx=8, pady=(6, 0))
        tk.Label(r1, text="刷新上限(次):", bg=BG, fg=FG, font=(FH, 9)).pack(side="left")
        self.cap_var = tk.StringVar(value="1000")
        tk.Entry(r1, textvariable=self.cap_var, bg=BTN, fg=FG, insertbackground=FG,
                 font=(FH, 9), width=8).pack(side="left", padx=(6, 0))
        tk.Label(r1, text="（到上限或虎符归零自动停）", bg=BG, fg=DIM, font=(FH, 8)).pack(side="left", padx=(6, 0))

        # 倍速
        r2 = tk.Frame(opt, bg=BG)
        r2.pack(fill="x", padx=8, pady=(6, 0))
        tk.Label(r2, text="倍速:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left")
        self.speed_var = tk.IntVar(value=10)
        self.speed_label = tk.StringVar(value="纯连发")
        sc = ttk.Scale(r2, from_=1, to=10, orient="horizontal", variable=self.speed_var,
                       length=200, command=self._on_speed)
        sc.pack(side="left", padx=(6, 0))
        tk.Label(r2, textvariable=self.speed_label, bg=BG, fg=GOLD, font=(FH, 9)).pack(side="left", padx=(6, 0))

        # 勾选项
        r3 = tk.Frame(opt, bg=BG)
        r3.pack(fill="x", padx=8, pady=(6, 8))
        self.collect_var = tk.BooleanVar(value=False)
        tk.Checkbutton(r3, text="集齐所有目标才停（否则出现任意一张就买下并停）", variable=self.collect_var,
                       bg=BG, fg=FG, selectcolor="#1e1e1e", activebackground=BG, activeforeground=FG,
                       font=(FH, 9)).pack(side="left")
        self.buy_var = tk.BooleanVar(value=True)
        tk.Checkbutton(r3, text="命中后自动买", variable=self.buy_var,
                       bg=BG, fg=FG, selectcolor="#1e1e1e", activebackground=BG, activeforeground=FG,
                       font=(FH, 9)).pack(side="left", padx=(12, 0))

        # ===== 操作 =====
        btn = tk.Frame(frame, bg=BG)
        btn.pack(fill="x", pady=(0, 8))
        self.start_btn = tk.Button(btn, text="开始找牌", command=self._start, bg=ACT, fg=FG,
                                   activebackground=ACT, activeforeground=FG, font=(FH, 10, "bold"),
                                   relief="flat", padx=14, cursor="hand2")
        self.start_btn.pack(side="left")
        self.stop_btn = tk.Button(btn, text="停止", command=self._stop, bg=BTN, fg=FG,
                                  activebackground=BTN, activeforeground=FG, font=(FH, 10),
                                  relief="flat", padx=14, cursor="hand2", state="disabled")
        self.stop_btn.pack(side="left", padx=(8, 0))
        self.combine_btn = tk.Button(btn, text="合指定牌", command=self._combine_specific, bg=BTN, fg=FG,
                                     activebackground=BTN, activeforeground=FG, font=(FH, 10),
                                     relief="flat", padx=14, cursor="hand2")
        self.combine_btn.pack(side="left", padx=(8, 0))

        # ===== 状态 =====
        stf = tk.LabelFrame(frame, text="状态", bg=BG, fg=GOLD, bd=1, relief="solid", font=(FH, 9))
        stf.pack(fill="x")
        self.st_var = tk.StringVar(value="待命")
        self.st_sent = tk.StringVar(value="已发: 0")
        self.st_found = tk.StringVar(value="找到: 0")
        self.st_bought = tk.StringVar(value="买下: 0")
        self.st_coins = tk.StringVar(value="虎符: -")
        self.st_shop = tk.StringVar(value="最近商店: -")
        grid = tk.Frame(stf, bg=BG)
        grid.pack(fill="x", padx=8, pady=6)
        tk.Label(grid, textvariable=self.st_var, bg=BG, fg=FG, font=(FH, 9), width=46, anchor="w").grid(row=0, column=0, columnspan=4, sticky="w")
        tk.Label(grid, textvariable=self.st_sent, bg=BG, fg=DIM, font=(FH, 9)).grid(row=1, column=0, sticky="w")
        tk.Label(grid, textvariable=self.st_found, bg=BG, fg=DIM, font=(FH, 9)).grid(row=1, column=1, sticky="w")
        tk.Label(grid, textvariable=self.st_bought, bg=BG, fg=DIM, font=(FH, 9)).grid(row=1, column=2, sticky="w")
        tk.Label(grid, textvariable=self.st_coins, bg=BG, fg=DIM, font=(FH, 9)).grid(row=1, column=3, sticky="w")
        tk.Label(grid, textvariable=self.st_shop, bg=BG, fg=DIM, font=(FH, 8), width=130, anchor="w").grid(row=2, column=0, columnspan=4, sticky="w")

        self._refresh_target_count()

    # ------------------------------------------------------------------ 目标管理
    def _refresh_target_count(self):
        try:
            self.target_count_var.set("已选 %d 张" % len(self.targets))
        except Exception:
            pass

    def _clear_targets(self):
        self.targets = []
        try:
            self.target_listbox.delete(0, "end")
            self._refresh_target_count()
        except Exception:
            pass

    def _remove_selected(self):
        try:
            for i in reversed(self.target_listbox.curselection()):
                self.target_listbox.delete(i)
                del self.targets[i]
            self._refresh_target_count()
        except Exception:
            pass

    def _add_targets(self, names):
        for n in names:
            if n and n not in self.targets:
                self.targets.append(n)
                self.target_listbox.insert("end", n)
        self._refresh_target_count()

    def _open_picker(self):
        """通用名字挑选框: 武将(本季+技能类型筛选) / 锦囊, 多选追加到目标。"""
        if not self.targets == self.targets:  # noqa: 占位, 保持结构
            pass
        dlg = Toplevel(self.root)
        dlg.title("挑选目标卡")
        dlg.configure(bg=BG)
        dlg.transient(self.root)

        lib_var = tk.StringVar(value="武将")
        tag_var = tk.StringVar(value="全部")
        search_var = tk.StringVar(value="")
        tkvar = tk.StringVar(value="")

        top = tk.Frame(dlg, bg=BG)
        top.pack(fill="x", padx=10, pady=(8, 0))
        tk.Label(top, text="库:", bg=BG, fg=DIM, font=(FH, 9)).pack(side="left")
        lib_cb = ttk.Combobox(top, textvariable=lib_var, state="readonly",
                              values=["武将", "锦囊"], font=(FH, 9), width=8)
        lib_cb.pack(side="left", padx=(4, 0))
        tag_cb = ttk.Combobox(top, textvariable=tag_var, state="readonly",
                             values=["全部"], font=(FH, 9), width=12)
        tag_cb.pack(side="left", padx=(6, 0))

        fnd = tk.Frame(dlg, bg=BG)
        fnd.pack(fill="x", padx=10, pady=(6, 0))
        tk.Label(fnd, text="搜索:", bg=BG, fg=DIM, font=(FH, 9)).pack(side="left")
        ent = tk.Entry(fnd, textvariable=search_var, bg=BTN, fg=FG, insertbackground=FG, font=(FH, 9), width=22)
        ent.pack(side="left", padx=(4, 0))
        ent.bind("<KeyRelease>", lambda *_: _fill())

        lb = tk.Listbox(dlg, bg=BTN, fg=FG, selectbackground=SEL_BG, selectforeground=SEL_FG,
                        font=(FH, 9), height=14, selectmode="extended")
        lb.pack(fill="both", expand=True, padx=10, pady=(6, 0))
        lb.bind("<Double-Button-1>", lambda *_: _add_sel())

        def _all_names():
            if lib_var.get() == "锦囊":
                return _scenario_spell_names()
            return IDMAP.current_season_hero_names()

        def _refresh_tags():
            if lib_var.get() == "锦囊":
                tag_cb.configure(values=["全部"], state="disabled")
                tag_var.set("全部")
            else:
                _tg = _hero_tag_choices()
                tag_cb.configure(values=["全部"] + sorted(_tg.keys()), state="readonly")
                tag_var.set("全部")

        def _fill():
            kw = search_var.get().strip()
            names = _all_names()
            if lib_var.get() == "武将" and tag_var.get() != "全部":
                _tg = _hero_tag_choices().get(tag_var.get()) or []
                names = [n for n in names if n in set(_tg)]
            lb.delete(0, "end")
            for n in names:
                if not kw or kw in n:
                    lb.insert("end", n)

        def _add_sel():
            for i in lb.curselection():
                self._add_targets([lb.get(i)])
            dlg.destroy()

        tk.Button(dlg, text="添加选中到目标", command=_add_sel, bg=ACT, fg=FG,
                  activebackground=ACT, activeforeground=FG, font=(FH, 9),
                  relief="flat", padx=12, cursor="hand2").pack(side="left", padx=10, pady=(4, 8))
        tk.Button(dlg, text="取消", command=dlg.destroy, bg=BTN, fg=FG,
                  activebackground=BTN, activeforeground=FG, font=(FH, 9),
                  relief="flat", padx=12, cursor="hand2").pack(side="left", pady=(4, 8))

        lib_cb.bind("<<ComboboxSelected>>", lambda *_: (_refresh_tags(), _fill()))
        tag_cb.bind("<<ComboboxSelected>>", lambda *_: _fill())
        _refresh_tags()
        _fill()

    # ------------------------------------------------------------------ 倍速显示
    def _on_speed(self, *_a):
        v = self.speed_var.get()
        if v >= 10:
            self.speed_label.set("纯连发")
        else:
            self.speed_label.set("%d（越慢越稳）" % v)

    # ------------------------------------------------------------------ 运行
    def _start(self):
        if self._qf_running:
            return
        if not self.targets:
            messagebox.showinfo("提示", "先挑选至少一个目标卡")
            return
        if not (self.app.ops and self.app.ops.connected()):
            messagebox.showinfo("提示", "先连接游戏页（助手连上游戏后再点开始）")
            return
        try:
            cap = max(1, int(str(self.cap_var.get()).strip() or "1000"))
        except Exception:
            cap = 1000
        # v4.184★ 把目标名反查成稳定 ID(chessID/spellID), 桥里按 ID 匹配,
        #   绕开"武将名映射为空时 name 退化成数字串、按名对不上"的坑。
        target_ids, unresolved = [], []
        for nm in self.targets:
            try:
                cids = IDMAP.find_ids_by_name(nm) or []
            except Exception:
                cids = []
            try:
                sids = IDMAP.find_spell_ids_by_name(nm) or []
            except Exception:
                sids = []
            ids = []
            for x in list(cids) + list(sids):
                try:
                    n = int(str(x).strip())
                    if n:
                        ids.append(n)
                except Exception:
                    pass
            if ids:
                target_ids.extend(ids)
            else:
                unresolved.append(nm)
        target_ids = sorted(set(target_ids))
        if unresolved:
            try:
                self.app.log("快速找牌: 以下目标名无法解析为 ID, 退回按名字匹配(可能不准): %s"
                             % ", ".join(unresolved), "WARN")
            except Exception:
                pass
        params = {
            "targets": list(self.targets),
            "targetIDs": target_ids,
            "maxRefreshes": cap,
            "speed": self.speed_var.get(),
            "collectAll": bool(self.collect_var.get()),
            "buyOnMatch": bool(self.buy_var.get()),
        }
        js = "window.AutoBuyAndSell.quickFind(%s)" % json.dumps(params)
        try:
            self.app.ops.send("eval", js=js)
        except Exception as e:
            messagebox.showerror("发送失败", str(e))
            return
        self._qf_running = True
        self._qf_stop = False
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.st_var.set("运行中…")
        self._qf_thread = threading.Thread(target=self._poll, daemon=True)
        self._qf_thread.start()

    def _stop(self):
        try:
            self.app.ops.send("eval", js="window.__sgsQuickFindStop=true")
        except Exception:
            pass
        self.st_var.set("正在停止并恢复商店…")

    def _combine_specific(self):
        """合指定牌: 用当前选中的目标(名字可重复, 凑 3 张)跑 combinespecific 步骤,
        绕过全局三连开关直接合金(这就是"指定三连"的可运行入口, 也用于验证该功能)。"""
        if not self.targets:
            messagebox.showinfo("提示", "先挑选要合的卡(同名可重复选, 凑 3 张)\n合指定牌会绕过全局三连开关直接合金。")
            return
        if not (self.app.ops and self.app.ops.connected()):
            messagebox.showinfo("提示", "先连接游戏页（助手连上游戏后再点）")
            return
        import time
        names = ",".join(self.targets)
        sid = "qf_combine_" + str(int(time.time() * 1000))
        sc = {
            "id": sid,
            "name": "快速找牌-合指定牌",
            "steps": [{"op": "combinespecific", "names": names, "optional": True}],
        }
        try:
            self.app.ops.push_scenarios([sc])
            r = self.app.ops.start_scenario(sid)
        except Exception as e:
            messagebox.showerror("发起失败", str(e))
            return
        try:
            self.app.log("合指定牌 已发起: %s -> %s" % (names, r), "INFO")
        except Exception:
            pass
        self.st_var.set("已发起合指定牌: " + names)
        threading.Thread(target=self._poll_combine, args=(sid,), daemon=True).start()

    def _poll_combine(self, sid):
        import time
        seq = 0
        last = ""
        for _ in range(60):
            try:
                res = self.app.ops.send_recv(
                    "eval",
                    js="JSON.stringify(window.AutoBuyAndSell.getScenarioLog(%d))" % seq,
                    timeout=2.0)
                if res:
                    d = json.loads(res)
                    for e in (d.get("lines") or []):
                        s = e.get("s") or e.get("msg") or ""
                        if s:
                            last = s
                            try:
                                seq = max(seq, int(e.get("seq", seq)) + 1)
                            except Exception:
                                pass
                            self.root.after(0, self.st_var.set, "合指定牌: " + s[:80])
                    if not d.get("running"):
                        break
            except Exception:
                pass
            if self._qf_stop:
                break
            time.sleep(0.3)
        if last:
            self.root.after(0, self.st_var.set, "合指定牌: " + last[:80])

    def _poll(self):
        try:
            while not self._qf_stop:
                try:
                    res = self.app.ops.send_recv(
                        "eval",
                        js="JSON.stringify(window.__sgsQuickFindStatus||{running:false})",
                        timeout=2.0)
                except Exception:
                    res = None
                status = None
                if res:
                    try:
                        status = json.loads(res)
                    except Exception:
                        status = None
                if status:
                    self.root.after(0, self._update_status, status)
                    if status.get("done"):
                        break
                if self._qf_stop:
                    break
                import time
                time.sleep(1.0)
        finally:
            self.root.after(0, self._finish)

    def _update_status(self, s):
        try:
            self.st_sent.set("已发: %s" % s.get("sent", 0))
            self.st_found.set("找到: %s" % s.get("found", 0))
            self.st_bought.set("买下: %s" % s.get("bought", 0))
            coins = s.get("coins")
            self.st_coins.set("虎符: %s" % ("-" if coins is None else coins))
            try:
                ls = s.get("lastShop")
                if isinstance(ls, list):
                    txt = " / ".join("%s%s" % (x.get("name") or "?", ("#" + str(x.get("id")) if x.get("id") else ""))
                                     for x in ls[:8])
                    self.st_shop.set("最近商店: " + (txt or "-"))
            except Exception:
                pass
            if s.get("lastMsg"):
                self.st_var.set(s.get("lastMsg"))
        except Exception:
            pass

    def _finish(self):
        self._qf_running = False
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        if self.st_var.get() in ("运行中…", "正在停止…", "正在停止并恢复商店…"):
            self.st_var.set("已结束")
