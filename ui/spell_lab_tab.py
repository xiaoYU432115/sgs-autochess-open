# -*- coding: utf-8 -*-
"""『锦囊试验台』页(v4.145★) —— 在助手上直接构造锦囊包并发出去, 不用进游戏手动点。

为什么单开一页
--------------
以前想确认"某个锦囊到底要什么参数", 只能进游戏、攒到那张卡、手动点一遍 ——
一张锦囊攒半天, 换一张又得重来。用户原话: "因为现在去游戏实践拿这个锦囊太麻烦了"。

这一页把游戏客户端做的事搬到助手上:
    读手牌 → 挑一张锦囊 → 选目标(无目标 / 势力 / 自定义) → 直接发 CReqChessUseSpell
并且把【即将发出的包】按服务器看到的样子显示出来 —— 就是三个字段。

★ 依据不是猜的, 来自官方客户端源码
  (参考_sgs_forward_looking-main/source/all_tavernchess_classes.txt):
      ReqChessUseSpell(e,t){ ... SendProto(CMSG_CREQCHESSUSESPELL,
          {tableID: this.TableID, spellGoodsID: e, targets: t}) }
  而"要选势力"的锦囊(釜底抽薪)在客户端里是"弹选势力窗口 → 点确定"两步,
  点确定那一下发的就是:
      onOk(){ ReqChessUseSpell(this.chessCard.goodsID, [this.curSelectMinion]) }
  → 所以【选势力 = 把势力值放进 targets】, 不涉及新协议、新字段名。

⚠ 发错的代价: 服务器多半直接忽略(白扔一张锦囊), 不会封号, 但卡是真的没了。
"""

import json
import time
import tkinter as tk
from tkinter import ttk

from ui.widgets import ThemeMixin

# 势力数字 = MinionType, 与 ChessConf 里武将的势力字段同一套(1魏 2蜀 … 8袁)
FACTIONS = [("魏", 1), ("蜀", 2), ("吴", 3), ("群", 4),
            ("黄巾", 5), ("汉", 6), ("西凉", 7), ("袁", 8)]


class SpellLabTabBuilder(ThemeMixin):
    def __init__(self, app):
        self.app = app
        self.root = app.root
        self.ops = getattr(app, "ops", None)
        self.spells = []            # 手牌里的锦囊: [{name, spellID, goodsID}]
        self._probe_on = False
        self._probe_btn = None
        self._refreshing = False
        self._refresh_btn = None

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

        tk.Label(top, text="锦囊试验台 —— 不进游戏也能把包直接发出去",
                 bg=bg, fg=self._gold(), font=(fh, 11, "bold")).pack(anchor="w")
        tk.Label(top, text="读手牌 → 挑一张锦囊 → 选目标 → 发送。包结构和游戏客户端发的完全一致"
                          "(三个字段: tableID / spellGoodsID / targets)。",
                 bg=bg, fg=dim, font=(fh, 9), wraplength=900, justify="left").pack(anchor="w", pady=(2, 6))

        # ① 手上的锦囊
        box = tk.LabelFrame(top, text="① 手上的锦囊", bg=bg, fg=fg, font=(fh, 9, "bold"))
        box.pack(fill="both", expand=True)
        row = tk.Frame(box, bg=bg)
        row.pack(fill="x", padx=6, pady=4)
        self._refresh_btn = tk.Button(row, text="刷新手牌", command=self._refresh,
                                      bg=self._btn(), fg=fg, font=(fh, 9),
                                      relief="flat", cursor="hand2")
        self._refresh_btn.pack(side="left")
        self._probe_btn = tk.Button(row, text="协议探测: 关", command=self._toggle_probe,
                                    bg=self._btn(), fg=fg, font=(fh, 9), relief="flat",
                                    cursor="hand2")
        self._probe_btn.pack(side="left", padx=6)
        tk.Label(row, text="(探测开着 = 所有包存完整结构, 取证用, 用完记得关)",
                 bg=bg, fg=dim, font=(fh, 8)).pack(side="left")

        self.listbox = tk.Listbox(box, height=7, bg="#1e1e1e", fg=fg,
                                  font=("Consolas", 9), relief="flat",
                                  selectbackground=self._act())
        self.listbox.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        self.listbox.insert("end", "(正在后台读取游戏手牌…)")
        self.listbox.bind("<<ListboxSelect>>", lambda e: self._preview())

        # ② 目标
        box2 = tk.LabelFrame(top, text="② 目标(targets 里装什么)", bg=bg, fg=fg, font=(fh, 9, "bold"))
        box2.pack(fill="x", pady=(6, 0))
        self.mode = tk.StringVar(value="faction")
        r = tk.Frame(box2, bg=bg)
        r.pack(fill="x", padx=6, pady=4)
        for txt, val in [("无目标 []", "none"), ("势力(釜底抽薪这类)", "faction"), ("自定义", "custom")]:
            tk.Radiobutton(r, text=txt, variable=self.mode, value=val, bg=bg, fg=fg,
                           selectcolor=self._act(), activebackground=bg, activeforeground=fg,
                           font=(fh, 9), command=self._preview).pack(side="left", padx=(0, 10))
        r2 = tk.Frame(box2, bg=bg)
        r2.pack(fill="x", padx=6, pady=(0, 6))
        tk.Label(r2, text="势力:", bg=bg, fg=fg, font=(fh, 9)).pack(side="left")
        self.faction = tk.StringVar(value="魏")
        cb = ttk.Combobox(r2, textvariable=self.faction, width=8, state="readonly",
                          values=[n for n, _ in FACTIONS])
        cb.pack(side="left", padx=4)
        cb.bind("<<ComboboxSelected>>", lambda e: self._preview())
        self.cb_faction = cb
        self.faction_tip = tk.Label(r2, text="", bg=bg, fg=self._dim(), font=(fh, 8))
        self.faction_tip.pack(side="left", padx=(6, 0))
        tk.Label(r2, text="自定义 targets(逗号分隔, 如 1 或 1733,1734):",
                 bg=bg, fg=dim, font=(fh, 9)).pack(side="left", padx=(12, 2))
        self.custom = tk.StringVar(value="")
        ent = tk.Entry(r2, textvariable=self.custom, width=22, bg="#1e1e1e", fg=fg,
                       insertbackground=fg, relief="flat", font=("Consolas", 9))
        ent.pack(side="left")
        ent.bind("<KeyRelease>", lambda ev: self._preview())

        # ③ 包预览 + 发送
        box3 = tk.LabelFrame(top, text="③ 将发出的包", bg=bg, fg=fg, font=(fh, 9, "bold"))
        box3.pack(fill="x", pady=(6, 0))
        self.preview = tk.Text(box3, height=3, bg="#1e1e1e", fg=self._blue(),
                               font=("Consolas", 9), relief="flat")
        self.preview.pack(fill="x", padx=6, pady=4)
        self.preview.insert("1.0", "(先点『刷新手牌』, 再选一张锦囊)")
        self.preview.configure(state="disabled")

        b = tk.Frame(box3, bg=bg)
        b.pack(fill="x", padx=6, pady=(0, 6))
        tk.Button(b, text="发送", command=self._send, bg=self._act(), fg=fg,
                  font=(fh, 10, "bold"), relief="flat", cursor="hand2", padx=14).pack(side="left")
        # v4.147★ 直发开关: 走协议直发就不经过游戏的 ReqChessUseSpell,
        # 也就不会被 CanOperate / 阶段门禁静默吞掉(那两个门禁命中时是无声无息的)。
        self.direct = tk.BooleanVar(value=False)
        tk.Checkbutton(b, text="绕过门禁直发(psend)", variable=self.direct,
                       bg=bg, fg=fg, selectcolor=self._act(), activebackground=bg,
                       activeforeground=fg, font=(fh, 9), cursor="hand2").pack(side="left", padx=6)
        self.result = tk.Label(b, text="", bg=bg, fg=self._green(), font=(fh, 9),
                               wraplength=760, justify="left")
        self.result.pack(side="left", padx=8)

        # v4.148★ 凭空发送(实验): 不选手牌, 手工填一个 spellGoodsID 照样发出去。
        #   用途是【取证】—— 回答"没有这张锦囊时, 发个一模一样的包服务器收不收"。
        #   包结构和客户端 onOk() 发出去的完全一致(就三个字段), 唯一区别是这个
        #   spellGoodsID 不是从手牌里取的。
        #   ⚠ 只用来探协议。服务器要是不校验, 那是它的漏洞, 不是本工具该依赖的东西 ——
        #     探完看 err_code 就知道了, 别拿它当"凭空变锦囊"用。
        b2 = tk.Frame(box3, bg=bg)
        b2.pack(fill="x", padx=6, pady=(0, 6))
        self.manual_on = tk.BooleanVar(value=False)
        tk.Checkbutton(b2, text="凭空发送(实验): 手工填 spellGoodsID, 不看手牌",
                       variable=self.manual_on, command=self._preview,
                       bg=bg, fg=fg, selectcolor=self._act(), activebackground=bg,
                       activeforeground=fg, font=(fh, 9), cursor="hand2").pack(side="left")
        self.manual_gid = tk.StringVar(value="")
        ent2 = tk.Entry(b2, textvariable=self.manual_gid, width=14, bg="#1e1e1e", fg=fg,
                        insertbackground=fg, relief="flat", font=("Consolas", 9))
        ent2.pack(side="left", padx=4)
        ent2.bind("<KeyRelease>", lambda ev: self._preview())

        # v4.149★ 装备三选一试验台 —— 单独开一格, 因为"候选"和"选中"是两件事,
        #   之前正是被这俩混在一起绕晕了:
        #     候选 = CNotifyEquipmentChooseList(服务器给了你哪三个) → 抓进来是空的
        #     选中 = CReqChessChooseEquipment(你挑了哪一个)        → 这里有 equipmentID
        #   所以"能看到"的是后者。这一格把两件事分开, 并且能手工直发试服务器收不收。
        box4 = tk.LabelFrame(top, text="④ 装备三选一试验台", bg=bg, fg=fg,
                             font=(fh, 9, "bold"))
        box4.pack(fill="x", padx=0, pady=(8, 0))
        tk.Label(box4, text="① 读候选 = 看服务器到底发没发那三个选项(要在三选一弹出后点)"
                           "    ② 直发 = 手工填 equipmentID 硬发, 看 errCode 判断服务器收不收",
                 bg=bg, fg=dim, font=(fh, 8), wraplength=900,
                 justify="left").pack(anchor="w", padx=6, pady=(4, 2))
        r4 = tk.Frame(box4, bg=bg)
        r4.pack(fill="x", padx=6, pady=2)
        tk.Button(r4, text="① 读候选", command=self._eq_read_cand,
                  bg=self._btn(), fg=fg, font=(fh, 9), relief="flat",
                  cursor="hand2").pack(side="left")
        tk.Label(r4, text="equipmentID:", bg=bg, fg=fg,
                 font=(fh, 9)).pack(side="left", padx=(12, 2))
        self.eq_id = tk.StringVar(value="")
        tk.Entry(r4, textvariable=self.eq_id, width=14, bg="#1e1e1e", fg=fg,
                 insertbackground=fg, relief="flat",
                 font=("Consolas", 9)).pack(side="left")
        tk.Button(r4, text="② 直发选中", command=self._eq_send,
                  bg=self._btn(), fg=fg, font=(fh, 9), relief="flat",
                  cursor="hand2").pack(side="left", padx=6)
        self.eq_result = tk.Label(box4, text="", bg=bg, fg=self._green(),
                                  font=(fh, 9), wraplength=880, justify="left")
        self.eq_result.pack(anchor="w", padx=6, pady=(0, 6))

        # v4.151★ 实时快照 —— 商店 / 手牌 / 阵上 / 3选1 一眼看完。
        #   以前要看这些只能进游戏 F12 粘别人的监听脚本(cy-*.js 那两个), 而监听脚本是
        #   【另一套独立代码】: 它能读出名字, 不代表【我们】的执行路径也能(前几轮
        #   "3选1 读不到名字"整整绕的就是这个圈)。这一格刻意走我们自己的取数口子
        #   (getShopGoods / _scHandCards / _scFieldCards / _scPendingPicks),
        #   看到的就正是剧本实际会拿到的东西 —— 对不上就能立刻发现, 不用等复盘。
        box5 = tk.LabelFrame(top, text="⑤ 实时快照(商店 / 手牌 / 阵上 / 3选1)",
                             bg=bg, fg=fg, font=(fh, 9, "bold"))
        box5.pack(fill="x", padx=0, pady=(8, 0))
        r5 = tk.Frame(box5, bg=bg)
        r5.pack(fill="x", padx=6, pady=(4, 2))
        tk.Button(r5, text="读快照", command=self._snap_read,
                  bg=self._btn(), fg=fg, font=(fh, 9), relief="flat",
                  cursor="hand2").pack(side="left")
        self.snap_auto = tk.BooleanVar(value=False)
        tk.Checkbutton(r5, text="每 2 秒自动刷新", variable=self.snap_auto,
                       command=self._snap_toggle,
                       bg=bg, fg=fg, selectcolor=self._act(), activebackground=bg,
                       activeforeground=fg, font=(fh, 9),
                       cursor="hand2").pack(side="left", padx=8)
        self.snap_age = tk.Label(r5, text="", bg=bg, fg=self._dim(), font=(fh, 8))
        self.snap_age.pack(side="left", padx=6)
        self.snap = tk.Text(box5, height=14, bg="#1e1e1e", fg=fg,
                            font=("Consolas", 9), relief="flat")
        self.snap.pack(fill="x", padx=6, pady=(0, 6))
        self.snap.insert("1.0", "(点『读快照』—— 要游戏已开、且助手连上了)")
        self.snap.configure(state="disabled")
        self._snap_job = None

        parent.after_idle(self._refresh)

    # ------------------------------------------------------------------
    def _refresh(self):
        """读手牌, 只留下带 spellID 的(那就是锦囊)。

        ★v4.147 换数据源。原来走 get_hand_chess() → AutoBuyAndSell.getHandChess()
          → **mgr.HandChess 单一来源**。而真正在正常工作的那几条路(剧本「用锦囊」、
          Shift+2、遣散锦囊)用的是 _scHandCards() → getHandArray(mgr), 它会
          多级回退(mgr.HandChess → selfInfo.newLyHandChess)并做原生字段兜底。
          两边不是一个源 —— 手牌在 HandChess 为空/还没重建完的那段时间里,
          试验台就"一张锦囊都没有", 而剧本那边照样认得出。表现就是"获取不到锦囊"。
          现在改走 collectHandSpells(与剧本/快捷键【同一套】识别层), 拿不到才回退。
        """
        if not self.ops:
            self._set_result("助手还没连上游戏页", self._red())
            return
        if self._refreshing:
            return
        self._refreshing = True
        self._refresh_btn.configure(state="disabled")
        if not self.app._run_bg("spell_lab_refresh", self._read_snapshot,
                                self._refresh_done):
            self._refreshing = False
            self._refresh_btn.configure(state="normal")

    def _read_snapshot(self):
        rows = []
        src = "?"
        blocked = []
        js = ("(function(){try{"
              "var AB=window.AutoBuyAndSell||null;"
              "if(!AB||typeof AB.collectHandSpells!=='function')return '';"
              "var g=AB.collectHandSpells('')||{};"
              "return JSON.stringify({list:(g.all||g.list||[]),blocked:(g.blocked||[]),src:(g.src||'?')});"
              "}catch(e){return '';}})()")
        try:
            res = self.ops.send_recv("eval", js=js, timeout=3.0)
            if res:
                s = str(res).strip().strip('"')
                if s.startswith("{"):
                    d = json.loads(s)
                    rows = d.get("list") or []
                    src = d.get("src") or "?"
                    blocked = d.get("blocked") or []
        except Exception:
            rows = []
        if not rows:
            # 回退: 识别层不可用(旧脚本)时走原来的单一来源, 至少不会一片空白
            try:
                rows = self.ops.get_hand_chess() or []
            except Exception as e:
                return {"error": "读手牌失败: %s" % e}
            src = "HandChess(回退)"

        out = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            sid = r.get("spellID") or r.get("SpellID") or 0
            if not sid:
                continue                      # 没有 spellID = 武将/随征卡, 不是锦囊
            gid = r.get("goodsID") or r.get("GoodsID") or 0
            nm = r.get("name") or r.get("Name") or ("锦囊%s" % sid)
            out.append({"name": nm, "spellID": sid, "goodsID": gid})
        # ★v4.148 黑名单的卡【也列出来】(标上 [黑名单])。
        #   黑名单的本意是【不自动用】, 不是【不让你用】—— 面板上那一栏「势力(釜底抽薪
        #   这类)」本来就是给釜底抽薪手动发包装的。以前只列"能自动用"的, 于是手上
        #   只剩釜底抽薪这类时列表一片空白, 看着就像"试验台识别不了手牌"。
        _n_blocked = 0
        for r in (blocked or []):
            if not isinstance(r, dict):
                continue
            sid = r.get("spellID") or r.get("SpellID") or 0
            if not sid:
                continue
            gid = r.get("goodsID") or r.get("GoodsID") or 0
            nm = r.get("name") or r.get("Name") or ("锦囊%s" % sid)
            out.append({"name": nm, "spellID": sid, "goodsID": gid,
                        "blocked": True})
            _n_blocked += 1
        names, tip = self._fetch_factions()
        return {
            "spells": out,
            "source": src,
            "blocked_count": _n_blocked,
            "factions": names,
            "faction_tip": tip,
        }

    def _refresh_done(self, result, err=""):
        self._refreshing = False
        try:
            self._refresh_btn.configure(state="normal")
        except Exception as e:
            self.log("[锦囊试验台] 刷新按钮状态更新失败: %s" % e, level="WARN")
        if err:
            self._set_result("刷新失败: %s" % err, self._red())
            self.log("[锦囊试验台] 刷新手牌失败: %s" % err, level="ERROR")
            return
        if not isinstance(result, dict):
            self._set_result("刷新失败: 后台没有返回有效数据", self._red())
            self.log("[锦囊试验台] 刷新手牌失败: 后台没有返回有效数据", level="ERROR")
            return
        if result.get("error"):
            self._set_result(result["error"], self._red())
            self.log("[锦囊试验台] " + result["error"], level="ERROR")
            return

        self.spells = result["spells"]
        self.log("[锦囊试验台] 数据源=%s, 认到 %d 张, 黑名单里另有 %d 张"
                 % (result["source"], len(self.spells) - result["blocked_count"],
                    result["blocked_count"]))
        self.listbox.delete(0, "end")
        for it in self.spells:
            self.listbox.insert("end", "%s%s  (spellID=%s, goodsID=%s)"
                                % ("[黑名单] " if it.get("blocked") else "",
                                   it["name"], it["spellID"], it["goodsID"]))
        if not self.spells:
            self.listbox.insert("end", "(手上没有锦囊 —— 先在游戏里拿一张, 再点刷新)")
        self.log("[锦囊试验台] 刷新手牌: %d 张锦囊" % len(self.spells))
        self._apply_factions(result["factions"], result["faction_tip"])
        self._preview()

    def _fetch_factions(self):
        """向游戏的 CurSeasonConf 要【本季真实可选势力】, 拿不到才用全表兜底。

        为什么不写死一张 1~8 的表: 势力池逐季在变 —— S1 的池子里没有西凉(7)和袁(8),
        到 S5 才加进来。照着老赛季写死, 遇上换季就会把本季根本没有的势力发出去,
        服务器多半直接忽略(锦囊还白扔一张)。所以宁可运行时问一次。
        """
        nums = []
        try:
            if self.ops:
                js = ("(function(){try{return JSON.stringify("
                      "(typeof window.__sgsSeasonFactions==='function')"
                      "?window.__sgsSeasonFactions():[]);}catch(e){return '[]';}})()")
                res = self.ops.send_recv("eval", js=js, timeout=2.0)
                if res:
                    nums = [int(x) for x in json.loads(str(res))
                            if str(x).strip().isdigit()]
        except Exception:
            nums = []
        if nums:
            names = [n for n, v in sorted(FACTIONS, key=lambda kv: kv[1]) if v in nums]
            tip = "本季候选 %s (来自游戏配置)" % " ".join(names)
        else:
            names = [n for n, _ in FACTIONS]
            tip = "未连上游戏, 按全表兜底(可能含本季没有的势力)"
        return names, tip

    def _apply_factions(self, names, tip):
        try:
            self.cb_faction["values"] = names
            if self.faction.get() not in names:
                self.faction.set(names[0])
            self.faction_tip.config(text=tip)
        except Exception:
            pass

    def _sel(self):
        try:
            i = self.listbox.curselection()[0]
            if 0 <= i < len(self.spells):
                return self.spells[i]
        except Exception:
            pass
        return None

    def _targets(self):
        """按当前选项算出 targets 数组。"""
        m = self.mode.get()
        if m == "none":
            return []
        if m == "custom":
            raw = (self.custom.get() or "").strip()
            if not raw:
                return []
            out = []
            for p in raw.replace("，", ",").split(","):
                p = p.strip()
                if p:
                    try:
                        out.append(int(p))
                    except ValueError:
                        pass
            return out
        # 势力
        name = (self.faction.get() or "").strip()
        for n, v in FACTIONS:
            if name == n or n in name:
                return [v]
        return []

    def _gid(self):
        """本次要发的 spellGoodsID: 开了『凭空发送』就用手填的那个。"""
        if getattr(self, "manual_on", None) and self.manual_on.get():
            try:
                return int(str(self.manual_gid.get()).strip() or 0)
            except ValueError:
                return 0
        it = self._sel()
        return int(it["goodsID"] or 0) if it else 0

    def _preview(self):
        it = self._sel()
        try:
            self.preview.configure(state="normal")
            self.preview.delete("1.0", "end")
            gid = self._gid()
            if not gid:
                self.preview.insert("1.0", "(选一张锦囊, 或开『凭空发送』填一个 spellGoodsID)")
            else:
                nm = (it["name"] if it else "凭空(手牌里没有这张)")
                body = {"tableID": "<当前牌桌>", "spellGoodsID": gid,
                        "targets": self._targets()}
                self.preview.insert("1.0", "CReqChessUseSpell  %s\n" % nm +
                                    json.dumps(body, ensure_ascii=False, indent=2))
            self.preview.configure(state="disabled")
        except Exception:
            pass

    def _send(self):
        it = self._sel()
        manual = bool(getattr(self, "manual_on", None) and self.manual_on.get())
        if not manual and not it:
            self._set_result("先选一张锦囊(或开『凭空发送』填 ID)", self._warn())
            return
        if not self.ops:
            self._set_result("助手还没连上游戏页", self._red())
            return
        tg = self._targets()
        gid = self._gid()
        if not gid:
            self._set_result("没有可用的 spellGoodsID", self._red())
            return
        nm = (it["name"] if it else "凭空#%d" % gid)

        # ★v4.147 两条路, 由【绕过门禁直发】勾选决定
        #   走游戏方法  = 与客户端 onOk() 同源, 但过 CanOperate / 阶段门禁
        #                 (门禁不满足时【一个字节都不发】, 还什么都不报 —— 第 17/21 批那条)
        #   协议直发    = psend 直接把 CReqChessUseSpell 打到网络上, 不经游戏方法,
        #                 所以门禁挡不住; 服务器收不收得下, 看 verified。
        if getattr(self, "direct", None) and self.direct.get():
            try:
                tid = self._table_id()
            except Exception:
                tid = 0
            body = {"spellGoodsID": gid, "targets": tg}
            if tid:
                body["tableID"] = tid        # v4.16 教训: 缺 tableID 服务器直接忽略
            try:
                res = self.ops.psend("cmsg.CReqChessUseSpell", body)
            except Exception as e:
                self._set_result("直发失败: %s" % e, self._red())
                return
            res = str(res)
            ok = ("verified" in res and "true" in res.lower()) or res.strip().startswith("{")
            self.log("[锦囊试验台·直发] CReqChessUseSpell %s → %s" % (body, res))
            self._set_result(("已直发 %s targets=%s → %s" % (nm, tg, res[:120])),
                             self._green() if ok else self._warn())
            return

        js = ("(function(){try{var m=(typeof getManager==='function')?getManager():null;"
              "if(!m||typeof m.ReqChessUseSpell!=='function')return 'err:拿不到 manager(不在对局里?)';"
              "m.ReqChessUseSpell(%d,%s);return 'ok';}catch(e){return 'err:'+(e&&e.message);}})()"
              % (gid, json.dumps(tg)))
        try:
            res = self.ops.send_recv("eval", js=js, timeout=3.0)
        except Exception as e:
            self._set_result("发送失败: %s" % e, self._red())
            return
        res = str(res)
        ok = res.strip().strip('"').startswith("ok")
        self._set_result(("已发: %s targets=%s" % (nm, tg)) if ok else ("失败: " + res),
                         self._green() if ok else self._red())
        self.log("[锦囊试验台] 发送 %s(goodsID=%s, %s) targets=%s → %s"
                 % (nm, gid, "凭空" if manual else "手牌", tg, res))

    def _table_id(self):
        """取当前牌桌 ID(协议直发必须带, 缺了服务器直接忽略 —— v4.16 的教训)。"""
        try:
            js = "(function(){try{return String(window.__sgsTableID||0);}catch(e){return '0';}})()"
            res = self.ops.send_recv("eval", js=js, timeout=2.0)
            return int(str(res).strip().strip('"') or 0)
        except Exception:
            return 0

    def _toggle_probe(self):
        self._probe_on = not self._probe_on
        ok = False
        try:
            ok = self.ops.set_net_probe(self._probe_on)
        except Exception as e:
            self._set_result("探测开关失败: %s" % e, self._red())
            self._probe_on = not self._probe_on
            return
        if self._probe_btn:
            self._probe_btn.config(text=("协议探测: 开" if self._probe_on else "协议探测: 关"))
        self._set_result("协议探测已开启 —— 现在去游戏里做一次操作, 取完关掉"
                         if self._probe_on else "协议探测已关闭(恢复常态)",
                         self._warn() if self._probe_on else self._dim())

    # ------------------------------------------------------------------
    # v4.149★ 装备三选一
    # ------------------------------------------------------------------
    def _set_eq(self, text, color=None):
        try:
            self.eq_result.configure(text=text, fg=color or self._green())
        except Exception:
            pass

    def _eq_read_cand(self):
        """读页面最近一次 CNotifyEquipmentChooseList 的【深抠】结果。

        为什么要深抠: 这条协议抓进来是 payload:{} keys:[], 跟"字段挂在原型上的类实例"
        一个签名(bridge 6916 行记过这个坑)。所以很可能是 Object.keys() 抠不到,
        而不是服务器没发。__sgsEquipProbe 里存的是连原型链一起抠的版本。
        """
        if not self.ops:
            self._set_eq("助手还没连上游戏页", self._red())
            return
        try:
            res = self.ops.send_recv(
                "eval", timeout=6.0,
                code="return JSON.stringify(window.__sgsEquipProbe || null)")
        except Exception as e:
            self._set_eq("读取失败: %s" % e, self._red())
            return
        if not res:
            self._set_eq("没拿到东西 —— 要么这一局还没弹出过三选一, "
                         "要么页面脚本还是旧的(刷新游戏页再来)", self._warn())
            return
        try:
            d = json.loads(res) if isinstance(res, str) else res
        except Exception:
            d = None
        if not d:
            self._set_eq("页面里是 null —— 本局还没弹出过三选一", self._warn())
            return
        age = time.time() - float(d.get("ts") or 0) / 1000.0
        dump = str(d.get("dump") or "")
        self.log("[装备试验台·候选] 距今%.0fs: %s" % (age, dump[:300]))
        self._set_eq("距今 %.0f 秒 → %s" % (age, dump[:420]),
                     self._green() if dump else self._warn())

    def _eq_send(self):
        """手工填 equipmentID 直发 CReqChessChooseEquipment。

        ⚠ 现在是【盲发】: 因为候选列表读不到(见 _eq_read_cand), 我们不知道服务器
          给的三个是哪个。所以填的 ID 可能根本不在候选里 —— 这正是要试的:
          看 errCode 判断服务器到底校不校验。
        """
        if not self.ops:
            self._set_eq("助手还没连上游戏页", self._red())
            return
        raw = (self.eq_id.get() or "").strip()
        try:
            eid = int(raw)
        except Exception:
            self._set_eq("equipmentID 得是数字(日志里见过的形如 4111008 / 4112011)",
                         self._red())
            return
        try:
            tid = self._table_id()
        except Exception:
            tid = 0
        body = {"equipmentID": eid}
        if tid:
            body["tableID"] = tid          # v4.16 教训: 缺 tableID 服务器直接忽略
        try:
            res = self.ops.psend("cmsg.CReqChessChooseEquipment", body)
        except Exception as e:
            self._set_eq("直发失败: %s" % e, self._red())
            return
        res = str(res)
        ok = ("verified" in res and "true" in res.lower())
        self.log("[装备试验台·直发] CReqChessChooseEquipment %s → %s" % (body, res))
        self._set_eq("已发 equipmentID=%s → %s" % (eid, res[:220]),
                     self._green() if ok else self._warn())

    # ------------------------------------------------------------------
    # v4.151★ 实时快照
    # ------------------------------------------------------------------
    # ⚠ 卡对象/候选对象的字段【挂在原型链上】, Object.keys() 抠不到、直接 JSON 化会被
    #   抠成 {} —— 这也是"包抓到了, 候选一个都读不出来"的老坑(见 bridge 4301 行)。
    #   所以这里【在页面里就地摊平】成 {i,id,name} 的纯对象再回传, 不碰原型链。
    #   名字一律过 getChessName/getSpellName; 拿不到就留空, 由 _snap_render 标出来,
    #   绝不拿裸号冒充名字。
    _SNAP_JS = (
        "(function(){"
        "var E=window.AutoBuyAndSell||null,S=window.__SGS_Standalone||null;"
        "function g(o,ks){if(!o)return 0;for(var i=0;i<ks.length;i++){"
        "try{var v=o[ks[i]];if(v)return v;}catch(e){}}return 0;}"
        "function nm(x){try{"
        "var c=g(x,['chessID','ChessID']),s=g(x,['spellID','SpellID']);"
        "if(c&&S&&S.getChessName){var n=S.getChessName(c);"
        "if(n&&!/^\\d+$/.test(String(n)))return String(n);}"
        "if(s&&S&&S.getSpellName){var m=S.getSpellName(s);"
        "if(m&&!/^\\d+$/.test(String(m)))return String(m);}}catch(e){}"
        "var t=String((x&&x.name)||'');"
        "return (t&&!/^\\d+$/.test(t))?t:'';}"
        "function row(x,i){return {i:i,"
        "id:(g(x,['goodsID','GoodsID'])||g(x,['chessID','ChessID'])||g(x,['spellID','SpellID'])),"
        "name:nm(x)};}"
        "var o={ts:Date.now()};"
        "if(!E){o.err='AutoBuyAndSell 不在(脚本没注入?刷新游戏页再来)';return JSON.stringify(o);}"
        "try{o.shop=(E.getShopGoods()||[]).map(row);}catch(e){o.shopErr=String(e);}"
        "try{o.hand=(E._scHandCards()||[]).map(row);}catch(e){o.handErr=String(e);}"
        "try{o.field=(E._scFieldCards()||[]).map(row);}catch(e){o.fieldErr=String(e);}"
        "try{o.eq=window.__sgsEquipCand||null;}catch(e){}"
        "try{var p=E._scPendingPicks()||{};"
        "o.pick={chess:(p.chess||[]),spell:(p.spell||[]),idx:p.idx,open:!!p.windowOpen};}"
        "catch(e){o.pickErr=String(e);}"
        "return JSON.stringify(o);})()"
    )

    def _set_snap(self, text, color=None):
        try:
            self.snap.configure(state="normal")
            self.snap.delete("1.0", "end")
            self.snap.insert("1.0", text)
            self.snap.configure(state="disabled")
            if color is not None:
                self.snap.configure(fg=color)
        except Exception:
            pass

    def _snap_render(self, d):
        """摊成一张能扫的表。

        名字没解析出来的必须【明着标出来】, 不能默默显示裸号 —— 那正是前几轮
        "名单看着有内容、其实全是空的"骗人的地方(?(s=310052) 那种)。
        """
        L = []

        def sec(title, rows, err):
            L.append("【%s】" % title)
            if err:
                L.append("  读取失败: %s" % err)
                return
            if not rows:
                L.append("  (空)")
                return
            for r in rows:
                nm = str(r.get("name") or "")
                rid = r.get("id")
                if nm:
                    L.append("  %2s  %-16s id=%s" % (r.get("i"), nm, rid))
                else:
                    L.append("  %2s  %-16s id=%s   ← 名字没解析出来(裸号)"
                             % (r.get("i"), "#" + str(rid), rid))

        sec("商店", d.get("shop"), d.get("shopErr"))
        L.append("")
        sec("手牌", d.get("hand"), d.get("handErr"))
        L.append("")
        sec("阵上", d.get("field"), d.get("fieldErr"))
        L.append("")
        L.append("【装备三选一候选】")
        lst = ((d.get("eq") or {}).get("list") or [])
        if lst:
            for i, e in enumerate(lst):
                L.append("  %d  %-16s equipmentID=%s"
                         % (i + 1, e.get("name") or ("#" + str(e.get("equipmentID"))),
                            e.get("equipmentID")))
        else:
            L.append("  (本局还没弹出过装备三选一)")
        L.append("")
        p = d.get("pick") or {}
        L.append("【锦囊/武将三选一】%s"
                 % ("窗口开着, 正在等你挑" if p.get("open") else "现在没在等挑"))
        for kind, key, idk in (("武将", "chess", "chessID"), ("锦囊", "spell", "spellID")):
            rows = p.get(key) or []
            if not rows:
                L.append("  %s: (无)" % kind)
                continue
            for i, r in enumerate(rows):
                nm = str(r.get("name") or "")
                rid = r.get(idk)
                if nm:
                    L.append("  %s%d  %-16s id=%s" % (kind, i + 1, nm, rid))
                else:
                    L.append("  %s%d  %-16s id=%s   ← 名字没解析出来(裸号)"
                             % (kind, i + 1, "#" + str(rid), rid))
        if d.get("pickErr"):
            L.append("  读取失败: %s" % d.get("pickErr"))
        return "\n".join(L)

    def _snap_read(self):
        if not self.ops:
            self._set_snap("助手还没连上游戏页", self._red())
            return
        try:
            res = self.ops.send_recv("eval", js=self._SNAP_JS, timeout=6.0)
        except Exception as e:
            self._set_snap("读取失败: %s" % e, self._red())
            return
        s = str(res or "").strip()
        if s.startswith('"'):
            try:
                s = json.loads(s)
            except Exception:
                pass
        if not s or not s.startswith("{"):
            self._set_snap("没拿到东西 —— 游戏页脚本可能是旧的(刷新游戏页再来)",
                           self._warn())
            return
        try:
            d = json.loads(s)
        except Exception as e:
            self._set_snap("解析失败: %s\n%s" % (e, s[:300]), self._red())
            return
        if d.get("err"):
            self._set_snap(d["err"], self._red())
            return
        self._set_snap(self._snap_render(d), self._green())
        try:
            age = max(0.0, time.time() - float(d.get("ts") or 0) / 1000.0)
            self.snap_age.configure(text="(页面数据距今 %.1f 秒)" % age)
        except Exception:
            pass
        self.log("[实时快照] 商店%d 手牌%d 阵上%d 三选一%s"
                 % (len(d.get("shop") or []), len(d.get("hand") or []),
                    len(d.get("field") or []),
                    "开着" if (d.get("pick") or {}).get("open") else "没开"))

    def _snap_toggle(self):
        """自动刷新开关 —— 关掉时【必须】把挂着的 after 取消, 否则页签切走了还在跑。"""
        if self._snap_job is not None:
            try:
                self.root.after_cancel(self._snap_job)
            except Exception:
                pass
            self._snap_job = None
        if self.snap_auto.get():
            self._snap_tick()

    def _snap_tick(self):
        if not self.snap_auto.get():
            self._snap_job = None
            return
        try:
            self._snap_read()
        except Exception:
            pass
        try:
            self._snap_job = self.root.after(2000, self._snap_tick)
        except Exception:
            self._snap_job = None

    def _set_result(self, text, color):
        try:
            self.result.config(text=text, fg=color)
        except Exception:
            pass
