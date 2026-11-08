# -*- coding: utf-8 -*-
"""『机制』页(v4.89★) —— 把"利用游戏机制"的玩法做成按钮。目前两条: 陆逊小抄、3选1。

★ 取证(v4.89★): 录一段「招募 → 战斗」这个过程里到底发生了什么(OUT/IN/PHASE 三路带秒数),
  只读 —— 一个包都不多发。用来回答"不选的话进战斗那一瞬间是谁替我选的"。

★ 3选1(v4.89★)把控制台粘贴型脚本 refs/EasySelect3.js 的能力收进了常驻桥接脚本
  (脚本里 SgsSelect3): 状态 / 自动选 / 强制完成。控制台也能直接敲 get3State() 等,
  不用再单独装那份脚本 —— 用户原话: "我确实不会去安装 EasySelect3.js, 装太多脚本很乱"。

陆逊小抄是什么
--------------
循环做三件事(游戏页 bridge/sgs_bridge.user.js 的 SgsLuXun 里写了完整依据):
  ① 使用: manager.ReqChessUseSpell(手牌第 N 张的 goodsID, [])  ← 与游戏自己用锦囊同源
  ② 同步: manager.ReqChessFlushInfo()                           ← 让服务器把手牌重发一遍
  ③ 刷新: refreshHandCardsOnly() + UI_UPDATE_HAND_CARD 事件      ← 只重画手牌区
反复"用出去 → 手牌回来 → 再用", 陆逊技能的收益就是这么一遍遍叠出来的。

为什么不是"调一下上游那个脚本"
------------------------------
上游 refs/AutoUseSpell.js 里 startLuXun / stopLuXun / toggleLuXun 【只有调用、没有定义】
(全文搜不到 `function startLuXun`), 按钮点下去就是 ReferenceError —— 那条路从来没真跑通过。
而且它"同步手牌"的兜底写的是 ReqShopRefreshChess, 那是【刷商店】会真扣虎符, 不能照抄。
所以这里是【重新写进桥接脚本】的一份实现(脚本里 SgsLuXun 那一节), 这一页只负责下发
参数和看状态。

停止条件有三条, 防止真跑成死循环: 到达最大轮次 / 手里那张已经不是锦囊 / 连着 N 轮
服务器没反应(游戏不可操作或发送失败)。任一命中都会停, 原因显示在状态栏。
"""

import os
import time
import threading
import tkinter as tk
from tkinter import messagebox

from core.config import DATA_DIR
from core.mechanism import LuXunMech, Select3Mech, TraceMech


from ui.widgets import ThemeMixin   # v4.109★ 主题色 getter 统一到 widgets.ThemeMixin


class MechanismTabBuilder(ThemeMixin):
    def __init__(self, app):
        self.app = app
        self.root = app.root
        self.mech = LuXunMech(getattr(app, "ops", None), log=self.log)
        self.mech3 = Select3Mech(getattr(app, "ops", None), log=self.log)
        self.mech_tr = TraceMech(getattr(app, "ops", None), log=self.log)
        self._job = None
        self._last_running = False
        self._tracing = False
        self._trace_lines = []
        # v4.101★ 后台任务簿: {名字: 是否还在跑}。用来做【单飞】—— 同一个动作上一轮还没
        #   回来就不发第二轮, 否则断线时(每次都要等满超时)点几下就叠好几条链。
        self._busy = {}
        self._alive = True

    # ---------- 主题 ----------
    # v4.109★ 主题色 getter 已统一到 widgets.ThemeMixin(本类继承它), 不再各写一份

    def log(self, msg, level="INFO"):
        if hasattr(self.app, "log"):
            self.app.log("[机制] " + str(msg), level=level)
        else:
            print("[机制] " + str(msg))

    def _status(self, msg):
        if hasattr(self, "status_var"):
            self.status_var.set(str(msg))
        # 为什么去重复: 状态栏那句话每 2 秒就会被轮询重设一次(比如"已停止: xxx"),
        #   照原样写日志 = 每 2 秒一行一模一样的话, 把真正有用的行冲掉。只在【内容变了】时记。
        if getattr(self, "_last_status_logged", None) != str(msg):
            self._last_status_logged = str(msg)
            self.log(msg)

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

        # ---------- 陆逊小抄 ----------
        box = tk.LabelFrame(wrap, text=" 陆逊小抄 · 循环「用锦囊 → 同步手牌 → 刷新手牌区」 ",
                            bg=self._bg(), fg=self._gold(), font=(self._fh(), 10, "bold"),
                            padx=8, pady=8)
        box.pack(fill="x", padx=10, pady=(8, 10))

        tk.Label(box, text=(
            "每一轮: 把手牌第 N 张当作锦囊用出去(ReqChessUseSpell) → 让服务器把手牌重发一遍\n"
            "(ReqChessFlushInfo) → 重画手牌区。用出去又回来, 陆逊技能就一遍遍触发。\n"
            "为什么必须是【锦囊】: 陆逊技能原文『你失去最后的手牌时…』(data/tavern_lords.json);\n"
            "  而武将卡带 chessID、没有 spellID, ReqChessUseSpell 根本不接受 —— 所以不是「武将换锦囊」,\n"
            "  而是要把【手牌清到只剩那一张锦囊】, 用掉它手牌清空才会触发。\n"
            "⚠ 只会用【带 spellID 的卡】: 手牌第 N 张是武将/随征卡时会直接停下, 不会乱用。\n"
            "⚠ 同步只走 ReqChessFlushInfo —— 绝不用「刷商店」凑(那会真扣虎符)。\n"
            "⚠ 官方已把这个漏洞修掉了(见 功能开发分析.md「陆逊相关漏洞已被修复」、参考资料_上游\n"
            "  JS脚本说明.md「陆逊小抄已禁用」): 现在指令照发、服务器可能【静默忽略】—— 所以加了\n"
            "  「手牌没变化就停」, 免得计数器一路空转你却看不出到底生没生效。"),
                 bg=self._bg(), fg=self._fg(), font=(self._fh(), 9),
                 justify="left").pack(anchor="w", pady=(0, 8))

        # 参数
        pf = tk.Frame(box, bg=self._bg())
        pf.pack(fill="x", pady=(0, 8))
        self.v_index = tk.StringVar(value="0")
        self.v_gap = tk.StringVar(value="300")
        self.v_max = tk.StringVar(value="200")
        self.v_idle = tk.StringVar(value="30")
        self.v_same = tk.StringVar(value="6")
        for label, var, width, hint in (
                ("手牌第几张", self.v_index, 5, "0 = 最左边那张"),
                ("每轮间隔(ms)", self.v_gap, 7, "太快服务器会漏, 300 实测可用"),
                ("最多跑几轮", self.v_max, 7, "保险: 到了就停"),
                ("连续无响应上限", self.v_idle, 5, "连着这么多轮没反应就停"),
                ("手牌不变上限", self.v_same, 5, "连着这么多轮手牌没变=服务器没吃, 停"),
        ):
            row = tk.Frame(pf, bg=self._bg())
            row.pack(side="left", padx=(0, 14))
            tk.Label(row, text=label, bg=self._bg(), fg=self._fg(),
                     font=(self._fh(), 9)).pack(anchor="w")
            e = tk.Entry(row, textvariable=var, width=width, bg="#232323", fg=self._fg(),
                         insertbackground=self._fg(), font=(self._fh(), 9))
            e.pack(anchor="w")
            tk.Label(row, text=hint, bg=self._bg(), fg=self._dim(),
                     font=(self._fh(), 8)).pack(anchor="w")

        # 按钮
        bf = tk.Frame(box, bg=self._bg())
        bf.pack(fill="x", pady=(0, 8))
        tk.Button(bf, text="▶ 启动陆逊小抄", command=self._on_start, bg=self._act(),
                  fg=self._fg(), font=(self._fh(), 10, "bold"), relief="flat",
                  padx=10, pady=4, cursor="hand2").pack(side="left", padx=(0, 8))
        tk.Button(bf, text="■ 停止", command=self._on_stop, bg=self._btn(), fg=self._fg(),
                  font=(self._fh(), 10), relief="flat", padx=10, pady=4,
                  cursor="hand2").pack(side="left", padx=(0, 8))
        tk.Button(bf, text="⟳ 刷新状态", command=self._poll, bg=self._btn(), fg=self._fg(),
                  font=(self._fh(), 10), relief="flat", padx=10, pady=4,
                  cursor="hand2").pack(side="left")
        # 为什么要能关: 这个判据同样会拦下"用完又原样回来"的真漏洞 —— 万一以后又有了, 能关掉。
        self.v_stop_same = tk.BooleanVar(value=True)
        tk.Checkbutton(bf, text="手牌没变化就停(防服务器静默忽略时空转)",
                       variable=self.v_stop_same, bg=self._bg(), fg=self._fg(),
                       selectcolor="#232323", activebackground=self._bg(),
                       activeforeground=self._fg(), font=(self._fh(), 9),
                       cursor="hand2").pack(side="left", padx=(14, 0))

        self.status_var = tk.StringVar(value="未启动")
        tk.Label(box, textvariable=self.status_var, bg=self._bg(), fg=self._blue(),
                 font=(self._fh(), 9), anchor="w").pack(fill="x", pady=(0, 6))

        self.stat_var = tk.StringVar(value="轮次 0 · 已发 0 · 同步 0 · 刷新 0 · 失败 0")
        tk.Label(box, textvariable=self.stat_var, bg=self._bg(), fg=self._gold(),
                 font=(self._fh(), 10, "bold"), anchor="w").pack(fill="x", pady=(0, 6))

        tk.Label(box, text="当前手牌(前 12 张, 带 ✦ 的是锦囊):", bg=self._bg(),
                 fg=self._dim(), font=(self._fh(), 9), anchor="w").pack(fill="x")
        self.hand_var = tk.StringVar(value="(还没读)")
        tk.Label(box, textvariable=self.hand_var, bg=self._bg(), fg=self._fg(),
                 font=(self._fh(), 9), anchor="w", justify="left",
                 wraplength=760).pack(fill="x")

        # ---------- 3选1 (v4.89★) ----------
        box3 = tk.LabelFrame(wrap, text=" 3选1 · 看状态 / 自动选 / 强制完成 ",
                             bg=self._bg(), fg=self._gold(), font=(self._fh(), 10, "bold"),
                             padx=8, pady=8)
        box3.pack(fill="x", padx=10, pady=(0, 10))

        tk.Label(box3, text=(
            "挑武将 / 挑锦囊 / 挑对手武将那个窗。以前要靠控制台粘贴型脚本 refs/EasySelect3.js\n"
            "—— 它是「最后一行的 window.get3State = …」挂全局的, 不整段粘进控制台跑一次就\n"
            "没有这些函数, 直接敲必然 ReferenceError。现在收进常驻桥接脚本了(脚本 SgsSelect3):\n"
            "  · 自动选  : 只挑【游戏这一档窗口里本来就有的第一张】, 走游戏自己的挑选指令,\n"
            "              和手动点一张同源, 服务器认。\n"
            "  · 连选到底: 有些技能一次要连挑【好几档】(实测诸葛亮就是: 武将 → 锦囊 → 锦囊,\n"
            "              档号 0→1→2), 每挑完一档服务器立刻挂下一档 —— 用这个一次挑完,\n"
            "              不用反复点「自动选」+「强制完成」。(v4.95★)\n"
            "  · 强制完成: 先点窗口的确认按钮; 都不行才「本地关窗」—— ⚠ 本地关窗服务器一个包\n"
            "              都没收到, 窗口没了但那一档还挂在服务器上等你, 别当成选完了。\n"
            "  · 判据    : 挑牌成没成看【服务器收没收下】(脚本 _scInstallPickTrace 抓的回包 errCode),\n"
            "              不能拿 WaitSelectCards 还在不在当结论 —— 答完之后它本来就不会立刻变。"),
                 bg=self._bg(), fg=self._fg(), font=(self._fh(), 9),
                 justify="left").pack(anchor="w", pady=(0, 8))

        bf3 = tk.Frame(box3, bg=self._bg())
        bf3.pack(fill="x", pady=(0, 6))
        tk.Button(bf3, text="⟳ 刷新状态", command=self._on_s3_refresh, bg=self._btn(),
                  fg=self._fg(), font=(self._fh(), 10), relief="flat", padx=10, pady=4,
                  cursor="hand2").pack(side="left", padx=(0, 8))
        tk.Button(bf3, text="✔ 自动选(挑第一张)", command=self._on_s3_auto, bg=self._act(),
                  fg=self._fg(), font=(self._fh(), 10, "bold"), relief="flat", padx=10, pady=4,
                  cursor="hand2").pack(side="left", padx=(0, 8))
        # v4.95★ 连选到底: 有些技能(实测诸葛亮就是)一次要连挑好几档 —— 取证 24:00 那份是
        #   档号 0→1→2 = 武将 → 锦囊 → 锦囊, 每挑完一档服务器立刻挂下一档。原来只能一档
        #   一档手动点, 现在挑完自动接下一档(最多 6 档)。
        tk.Button(bf3, text="⏩ 连选到底(最多6档)", command=self._on_s3_chain, bg=self._act(),
                  fg=self._fg(), font=(self._fh(), 10, "bold"), relief="flat", padx=10, pady=4,
                  cursor="hand2").pack(side="left", padx=(0, 8))
        tk.Button(bf3, text="⚑ 强制完成", command=self._on_s3_force, bg=self._btn(),
                  fg=self._fg(), font=(self._fh(), 10), relief="flat", padx=10, pady=4,
                  cursor="hand2").pack(side="left")

        self.s3_var = tk.StringVar(value="(还没读 —— 切到这一页会自动读)")
        tk.Label(box3, textvariable=self.s3_var, bg=self._bg(), fg=self._blue(),
                 font=(self._fh(), 9), anchor="w", justify="left",
                 wraplength=760).pack(fill="x")

        # ---------- 取证: 录一段「招募 → 战斗」(v4.89★) ----------
        boxt = tk.LabelFrame(wrap, text=" 取证 · 录一段「招募 → 战斗」到底发生了什么 ",
                             bg=self._bg(), fg=self._gold(), font=(self._fh(), 10, "bold"),
                             padx=8, pady=8)
        boxt.pack(fill="x", padx=10, pady=(0, 10))

        tk.Label(boxt, text=(
            "只录不改: 一点【开始录制】→ 游戏里照常打(进战斗前后别切页面) → 点【取回】看时间轴。\n"
            "三路都带【相对起点的秒数】和【当时哪个阶段】: ①OUT 发出去的包 ②IN 服务器推来的包\n"
            "(带 errCode, 非 0 = 服务器把这一包退回来了) ③PHASE 阶段/档号/待选/可操作(变了才记一行)。\n"
            "名字带【(推)】= 这个协议号游戏页面上查不到, 是拿【紧跟着的回包名字】反推出来的请求名\n"
            "(仅供你看, 不参与发包; 同一个号被推成两种名字时会主动判成歧义、不显示)。\n"
            "用来回答: 「不选的话, 进战斗那一瞬间是服务器替我选了, 还是客户端自己把窗关了」。"),
                 bg=self._bg(), fg=self._fg(), font=(self._fh(), 9),
                 justify="left").pack(anchor="w", pady=(0, 8))

        r1 = tk.Frame(boxt, bg=self._bg())
        r1.pack(fill="x", pady=(0, 6))
        tk.Label(r1, text="最长录多久(秒)", bg=self._bg(), fg=self._fg(),
                 font=(self._fh(), 9)).pack(side="left")
        self.v_trace_ms = tk.StringVar(value="180")
        tk.Entry(r1, textvariable=self.v_trace_ms, width=6, bg="#232323", fg=self._fg(),
                 insertbackground=self._fg(), font=(self._fh(), 9)).pack(side="left", padx=(4, 12))
        tk.Button(r1, text="● 开始录制", command=self._on_trace_start, bg=self._act(),
                  fg=self._fg(), font=(self._fh(), 10, "bold"), relief="flat", padx=10, pady=3,
                  cursor="hand2").pack(side="left", padx=(0, 8))
        tk.Button(r1, text="■ 停止", command=self._on_trace_stop, bg=self._btn(),
                  fg=self._fg(), font=(self._fh(), 10), relief="flat", padx=10, pady=3,
                  cursor="hand2").pack(side="left", padx=(0, 8))
        tk.Button(r1, text="⟳ 取回", command=self._on_trace_dump, bg=self._btn(),
                  fg=self._fg(), font=(self._fh(), 10), relief="flat", padx=10, pady=3,
                  cursor="hand2").pack(side="left", padx=(0, 12))
        self.v_pick_only = tk.BooleanVar(value=False)
        tk.Checkbutton(r1, text="只看挑选相关(名字带 SELECT / 档号变化)",
                       variable=self.v_pick_only, bg=self._bg(), fg=self._fg(),
                       selectcolor="#232323", activebackground=self._bg(),
                       activeforeground=self._fg(), font=(self._fh(), 9),
                       cursor="hand2", command=self._render_trace_again).pack(side="left")

        self.trace_var = tk.StringVar(value="(还没录)")
        tk.Label(boxt, textvariable=self.trace_var, bg=self._bg(), fg=self._blue(),
                 font=(self._fh(), 9), anchor="w").pack(fill="x", pady=(0, 4))

        tf = tk.Frame(boxt, bg=self._bg())
        tf.pack(fill="x")
        self.trace_txt = tk.Text(tf, height=14, wrap="none", bg="#1c1c1c", fg=self._fg(),
                                 insertbackground=self._fg(), font=("Consolas", 9),
                                 relief="flat")
        sb = tk.Scrollbar(tf, orient="vertical", command=self.trace_txt.yview)
        self.trace_txt.configure(yscrollcommand=sb.set)
        self.trace_txt.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        try:
            self.trace_txt.insert("1.0", "(开始录制后, 这里会出现时间轴)")
            self.trace_txt.configure(state="disabled")
        except Exception as e:
            self.log("取证文本框初始化失败: %s" % e, level="WARN")

        # 注意: 这里【不】立刻拉一次状态 —— 助手启动时所有页都会 build, 那时还没连上游戏页,
        #   立刻拉只会白扔一条失败日志。等用户真的切到这一页(或已经在跑)再开始问。
        self._start_polling()

    # ---------- 动作 ----------
    # v4.101★ 【为什么要有这个 helper】—— 「机制页特别卡」的根因就在这里:
    #   这一页每一个按钮/每一次轮询, 以前都是在 Tk 主线程里【同步】等游戏页回话
    #   (core/remote_ops.py 的 send_recv 就是 time.sleep + 轮询, 会把调用它的线程整个冻住)。
    #   · mech.state()  最坏等 6 秒
    #   · mech3.state() 最坏等 6 秒
    #   · trace_dump()  最坏等 20 秒
    #   而 _poll() 一轮串行跑前两条, 录制时再加第三条 —— 界面一次被冻 12~32 秒, 期间点什么
    #   都没反应、切页也切不动。现在所有会等的调用一律丢到工作线程, 回来用 after(0) 回主线程
    #   更新界面; 同一个动作【单飞】(上一轮没回来就不发新的), 免得断线时越点越卡。
    def _run(self, tag, fn, on_done=None, wait=None):
        if not self._alive:
            return False
        if self._busy.get(tag):
            return False
        self._busy[tag] = True
        if wait:
            try:
                self._status(wait)
            except Exception as e:
                # 别静默吞: 状态栏写不进去说明界面已经关了/还没建好, 值得记一笔
                self.log("状态栏更新失败(%s): %s" % (tag, e), level="WARN")

        def _work():
            r, err = None, ""
            try:
                r = fn()
            except Exception as e:
                err = str(e)

            def _done():
                self._busy[tag] = False
                if not self._alive:
                    return
                try:
                    if on_done:
                        on_done(r, err)
                except Exception as e:
                    self.log("「%s」更新界面失败: %s" % (tag, e), level="WARN")
            try:
                self.root.after(0, _done)
            except Exception as e:
                self._busy[tag] = False
                self.log("「%s」回主线程失败(界面可能已关): %s" % (tag, e), level="WARN")

        try:
            threading.Thread(target=_work, daemon=True).start()
            return True
        except Exception as e:
            self._busy[tag] = False
            self.log("「%s」起线程失败: %s" % (tag, e), level="WARN")
            return False

    def _int(self, var, default, lo=0, hi=100000):
        try:
            v = int(str(var.get()).strip())
        except Exception:
            return default
        return max(lo, min(hi, v))

    def _on_start(self):
        # 为什么非要拦一下: ReqChessUseSpell 就是游戏自己"用锦囊"那条指令, 服务器【真扣牌】,
        #   没有任何"试一下不消耗"的入口。当年能循环是因为陆逊技能把用掉的手牌补回来;
        #   官方修掉之后就只剩"用一张少一张"。不先说清楚, 点一下就是白扔锦囊。
        try:
            ok = messagebox.askyesno(
                "陆逊小抄: 锦囊会真的被用掉",
                "手牌第 %s 张的锦囊会被【真的用出去】(服务器真扣, 没有试用的入口)。\n\n"
                "· 当年这个循环能滚起来, 靠的是陆逊技能把用掉的手牌补回来;\n"
                "· 官方已把这一点修掉了 —— 现在通常只会【用一张少一张】;\n"
                "· 真要继续, 建议把「最多跑几轮」调小(比如 3~5), 先看手牌有没有变化。\n\n"
                "确定要启动吗?" % (str(self.v_index.get()).strip() or "0"),
                icon="warning")
        except Exception:
            ok = True
        if not ok:
            self._status("已取消(没有真的用出去)")
            return
        args = dict(
            index=self._int(self.v_index, 0, 0, 20),
            gap_ms=self._int(self.v_gap, 300, 50, 10000),
            max_times=self._int(self.v_max, 200, 1, 100000),
            max_idle=self._int(self.v_idle, 30, 3, 10000),
            max_same=self._int(self.v_same, 6, 2, 10000),
            stop_same=bool(self.v_stop_same.get()),
        )
        # 参数在【主线程】里先读好, 线程里只管发 —— 界面变量不是线程安全的。
        self._run("luxun_start", (lambda: self.mech.start(**args)),
                  self._on_start_done, wait="正在启动…")

    def _on_start_done(self, r, err):
        if err:
            self._status("启动异常: %s" % err)
            return
        r = r or {}
        if not r.get("ok"):
            self._status("启动失败: %s" % (r.get("reason") or "?"))
            return
        if r.get("reason"):
            self._status("注意: %s" % r["reason"])
        else:
            o = r.get("opt") or {}
            self._status("已启动: 第 %s 张 · 间隔 %sms · 最多 %s 轮"
                         % (o.get("index"), o.get("gapMs"), o.get("maxTimes")))
        self._poll()

    def _on_stop(self):
        self._run("luxun_stop", (lambda: self.mech.stop("助手端点了停止")),
                  self._on_stop_done, wait="正在停止…")

    def _on_stop_done(self, r, err):
        if err:
            self._status("停止异常: %s" % err)
            return
        r = r or {}
        self._status("已停止" if r.get("ok") else ("停止失败: %s" % (r.get("reason") or "?")))
        self._poll()

    # ---------- 3选1 (v4.89★) ----------
    def _s3_line(self, r):
        """把一次 3选1 的结果/状态显示成一行。"""
        if not r.get("ok"):
            return "读不到: %s" % (r.get("reason") or "?")
        if "how" in r:
            how = r.get("how") or "?"
            if r.get("local"):
                return "走了兜底: %s ⚠ 本地关窗, 服务器没收到%s" % (
                    how, (" —— " + r["note"]) if r.get("note") else "")
            card = r.get("card")
            # v4.93★ 标出走的是【挑武将】还是【挑锦囊】那一路(两条路协议/字段不同,
            #   实机只验过其中一条, 不标出来就不知道这次验的是哪条)。
            kt = r.get("kindText") or {"chess": "武将", "spell": "锦囊"}.get(r.get("kind") or "", "")
            txt = "已发: %s%s" % (how, (" · " + card) if card else "")
            if kt:
                txt += " 【第%s档·挑%s】" % (r.get("idx") if r.get("idx") is not None else "?", kt)
            return txt
        st = []
        st.append("阶段 %s" % (r.get("phase") if r.get("phase") is not None else "?"))
        st.append("服务器在等的档号 %s" % r.get("curIdx"))
        st.append("待选 %s 张" % r.get("waitCards"))
        st.append("待选装备 %s" % r.get("waitEquips"))
        if r.get("canOperate") is False:
            st.append("⚠ 游戏当前不可操作")
        # v4.101★ 上一次挑选的【回包】—— 以前点完"自动选"只有一句"已发: …", 而真正的确认
        #   /补包/接下一档都在页面的定时器里做, 成功失败都回不到界面(异常被空 catch 吞掉)。
        #   现在这一行是"服务器到底收下没有"的证据。
        ack = r.get("lastAck")
        if ack:
            age = int((ack.get("ageMs") or 0) / 1000)
            if ack.get("errCode") == 0 and ack.get("hasEntity"):
                st.append("✔ 上次已落定(服务器收下了, %s 秒前)" % age)
            elif ack.get("errCode") == 0:
                st.append("上次回包 errCode=0 但没带回实体(%s 秒前)" % age)
            else:
                st.append("⚠ 上次被退回 errCode=%s(%s 秒前)" % (ack.get("errCode"), age))
        cards = r.get("cards") or []
        if cards:
            parts = []
            for c in cards[:8]:
                nm = c.get("name") or ("#%s" % (c.get("goodsID") or 0))
                parts.append("%d.%s(%s)" % (int(c.get("idx", 0)) + 1, nm, c.get("type") or "?"))
            st.append("候选: " + "  ".join(parts))
        else:
            st.append("这一刻没有待选窗口")
        # v4.150★ 装备档: 以前只报"待选装备 N", 看不见是哪几件 —— 现在把名字也列出来。
        #   装备在页面端是【另一个字段】WaitSelectEquiments(不在 cards 里), 所以走单独一支。
        eqs = r.get("equips") or []
        if eqs:
            parts = []
            for c in eqs[:8]:
                nm = c.get("name") or ("#%s" % (c.get("weaponID") or 0))
                parts.append("%d.%s(%s)" % (int(c.get("idx", 0)) + 1, nm, c.get("type") or "?"))
            st.append("装备候选: " + "  ".join(parts))
        elif (r.get("waitEquips") or 0) > 0:
            st.append("装备候选 %s 件(页面没解析出明细)" % r.get("waitEquips"))
        return " · ".join(str(x) for x in st)

    def _apply_s3(self, r, err, tag=""):
        """把一次 3选1 的返回铺到界面上(主线程)。"""
        if err:
            self.s3_var.set("出错: %s" % err)
            self._status("3选1 %s异常: %s" % (tag, err))
            return
        r = r or {}
        line = self._s3_line(r)
        self.s3_var.set(line)
        if not r.get("ok"):
            # v4.101★ 以前 ok=false 时【什么都不显示】, 界面停在上一轮的旧值, 看着像"点了没反应"
            self._status("3选1 %s没成: %s" % (tag, r.get("reason") or "?"))
            return
        if tag == "自动选":
            # v4.91★ 预选档一次点击要发【两包】(预选 + 确认), 只发一包等于点了卡没点确认
            self._status("3选1 自动选: %s%s" % (
                r.get("how") or "已发送",
                (" —— " + r.get("second")) if r.get("second") else ""))
        elif tag == "连选到底":
            self._status("3选1 连选到底: %s —— 已开挑(最多 6 档, 每档挑完自动接下一档, 看脚本日志看进度)"
                         % (r.get("how") or "已发送"))
        elif tag == "刷新":
            self._status("3选1: %s" % line[:80])

    def _on_s3_refresh(self):
        self._run("s3_state", (lambda: self.mech3.state()),
                  lambda r, err: self._apply_s3(r, err, "刷新"), wait="读取 3选1 状态…")

    def _on_s3_auto(self):
        self._run("s3_auto", (lambda: self.mech3.auto()),
                  lambda r, err: self._apply_s3(r, err, "自动选"), wait="正在自动选…")

    def _on_s3_chain(self):
        # v4.95★ 连挑多档(诸葛亮这类技能一次要连挑好几档), 挑完一档自动接下一档
        self._run("s3_auto", (lambda: self.mech3.auto(times=6)),
                  lambda r, err: self._apply_s3(r, err, "连选到底"), wait="正在连选…")

    def _on_s3_force(self):
        self._run("s3_force", (lambda: self.mech3.force()), self._on_s3_force_done,
                  wait="正在强制完成…")

    def _on_s3_force_done(self, r, err):
        if err:
            self._status("3选1 强制完成异常: %s" % err)
            return
        r = r or {}
        self.s3_var.set(self._s3_line(r))
        if r.get("ok"):
            # v4.97★ 关键: 先说【服务器收下没有】—— 已落定时关窗无害(这就是手动"先选再
            #   强制关闭"能成的原因); 没落定时关窗会把那一档永远留在服务器上。
            ack = r.get("ack") or ""
            if r.get("local"):
                self._status("3选1 强制完成: ⚠ 只关了本地窗口 —— %s" % (ack or "那一档还在服务器上等你"))
            else:
                self._status("3选1 强制完成: %s —— %s" % (r.get("how") or "已触发", ack))
        else:
            # 找不到窗口时把"试过哪些名字 / WindowManager 上有什么"一并带出来, 便于定位真名
            we = r.get("winErr") or ""
            self._status("3选1 强制完成没成: %s%s" % (
                r.get("reason") or "?", (" | 窗口: " + we[:90]) if we else ""))

    # ---------- 取证 (v4.89★) ----------
    def _trace_pick_only(self, line):
        """只看挑选相关: 名字带 SELECT, 或 PHASE 行里档号/待选有变化。"""
        nm = str(line.get("name") or "")
        if "select" in nm.lower():
            return True
        if str(line.get("src") or "") == "PHASE":
            d = str(line.get("detail") or "")
            return ("档号=" in d) and (not d.endswith("档号=-"))
        return False

    def _fmt_trace(self, lines):
        out = []
        for ln in lines:
            tag = str(ln.get("src") or "?")
            nm = str(ln.get("name") or "")
            detail = str(ln.get("detail") or "")
            ph = str(ln.get("phase") or "")
            pad = {"OUT": "OUT  ", "IN": "IN   ", "PHASE": "PHASE"}.get(tag, tag)
            out.append("[+%ss] %s %s%s%s" % (
                ln.get("t", 0), pad, nm,
                ("  " + detail) if detail else "",
                ("  【阶段 " + ph + "】") if ph else ""))
        return "\n".join(out)

    def _save_trace(self, lines):
        """落盘一份(用户原话"录制完了你看吧" —— 只在界面上看的话我读不到)。"""
        try:
            if not os.path.isdir(DATA_DIR):
                os.makedirs(DATA_DIR)
            path = os.path.join(DATA_DIR, "trace_%s.txt" % time.strftime("%Y%m%d_%H%M%S"))
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._fmt_trace(lines) or "(空)")
            self.log("取证已存盘: %s (%s 行)" % (path, len(lines or [])))
            return path
        except Exception as e:
            self.log("取证存盘失败: %s" % e, level="WARN")
            return ""

    def _render_trace(self, lines, save=False):
        self._trace_lines = list(lines or [])
        if save:
            self._save_trace(self._trace_lines)
        # v4.90★ 存盘存【全段】, 但 Text 里只塞最后 300 行 —— 上千行直接 insert 会把界面卡住,
        #   而"只看挑选相关 / 再渲染一次"用的是内存里的 _trace_lines(全段), 不受这个限制。
        show = self._trace_lines
        if len(show) > 300:
            show = show[-300:]
        try:
            self.trace_txt.configure(state="normal")
            self.trace_txt.delete("1.0", "end")
            self.trace_txt.insert("1.0", self._fmt_trace(show) or "(还没有任何记录)")
            self.trace_txt.configure(state="disabled")
        except Exception as e:
            self.log("取证渲染失败: %s" % e, level="WARN")

    def _render_trace_again(self):
        """勾选框变化时按当前筛选重画(不重新下发指令)。"""
        if not self._trace_lines:
            return
        if self.v_pick_only.get():
            self._render_trace([x for x in self._trace_lines if self._trace_pick_only(x)])
        else:
            self._render_trace(self._trace_lines)

    def _on_trace_start(self):
        sec = self._int(self.v_trace_ms, 180, 5, 3600)
        self._run("trace_start", (lambda: self.mech_tr.start(max_ms=sec * 1000, tag="招募 → 战斗")),
                  self._on_trace_start_done, wait="正在开始录制…")

    def _on_trace_start_done(self, r, err):
        if err:
            self._status("取证启动异常: %s" % err)
            return
        r = r or {}
        if not r.get("ok"):
            self._status("取证启动失败: %s" % (r.get("reason") or "?"))
            return
        sec = self._int(self.v_trace_ms, 180, 5, 3600)
        self._tracing = True
        self.trace_var.set("录制中…(最多 %s 秒, 到点自动停) —— 现在去游戏里打, 进战斗后回来点【取回】" % sec)
        self._status("取证: 已开始录制(最多 %s 秒)" % sec)

    def _on_trace_stop(self):
        self._run("trace_stop", (lambda: self.mech_tr.stop("助手端点了停止")),
                  self._on_trace_stop_done, wait="正在停止并取回…")

    def _on_trace_stop_done(self, r, err):
        # v4.101★ 以前这里【完全不看 r["ok"]】: 超时/断线时 r 是个 {ok:false}, 界面照样写
        #   "已停止 · 共 0 行(已存盘)" 并按空数组存盘 —— 明明什么都没拿回来, 却报成功。
        if err:
            self._status("取证停止异常: %s" % err)
            return
        r = r or {}
        if not r.get("ok"):
            self._status("取证停止失败: %s —— 页面那段还没取回来, 再点一次【停止并取回】试试"
                         % (r.get("reason") or "?"))
            return
        self._tracing = False
        self.trace_var.set("已停止 · 共 %s 行(已存盘)" % (r.get("total") or 0))
        self._render_trace(r.get("lines") or [], save=True)

    def _on_trace_dump(self):
        # v4.90★ 存盘要【全段】: 以前固定取最后 200 行, 结果文件里只剩战斗尾巴,
        #   招募/挑选那一段根本没进去(脚本侧缓冲区也被噪音挤满, 已同步改成 4000 行 + 先丢噪音)。
        self._run("trace_dump", (lambda: self.mech_tr.dump(5000)),
                  self._on_trace_dump_done, wait="正在取回(内容多, 可能要几秒)…")

    def _on_trace_dump_done(self, r, err):
        if err:
            self._status("取回异常: %s" % err)
            return
        r = r or {}
        if not r.get("ok"):
            self._status("取回失败: %s" % (r.get("reason") or "?"))
            return
        total = r.get("total") or 0
        got = len(r.get("lines") or [])
        self.trace_var.set("%s · 共 %s 行(存盘 %s 行, 下面只显示最后 300 行)"
                           % ("录制中…" if r.get("on") else "已停止", total, got))
        self._render_trace(r.get("lines") or [], save=True)

    def _apply_trace_stat(self, r, err):
        """v4.101★ 录制中的轮询【只问行数】—— 以前每 2 秒 dump(200) 把 200 行搬回来
        重画一遍 Text(页面序列化 + 传输 + 助手解析 + 渲染), 只为看一眼"还在录吗/多少行"。
        完整内容交给「取回」按钮。"""
        if err or not r or not r.get("ok"):
            return
        if not r.get("on"):
            self._tracing = False
        self.trace_var.set("%s · 共 %s 行(已录 %.0f 秒)"
                           % ("录制中…" if r.get("on") else "已停止",
                              r.get("total") or 0, (r.get("ms") or 0) / 1000.0))

    # ---------- 轮询 ----------
    # 为什么要挑时机: 每秒无脑问一次状态 = 每秒一条指令往游戏页发, 而绝大多数时间
    #   这一页根本没被打开、小抄也没在跑 —— 纯浪费传输(用户为这个删过『复盘』页)。
    #   所以只在【这一页正被看着】或【小抄正在跑/刚跑完】时才真发指令, 平时 2 秒空转一次。
    def _tab_visible(self):
        nb = getattr(self.app, "notebook", None)
        if nb is None:
            return True
        try:
            return nb.tab(nb.select(), "text") == "机制"
        except Exception as e:
            self.log("判断当前页失败(按可见处理): %s" % e, level="WARN")
            return True

    def _start_polling(self):
        self._job = self.root.after(2000, self._tick_poll)

    def _tick_poll(self):
        try:
            if self._tab_visible() or self._last_running or self._tracing:
                self._poll()
        except Exception as e:
            self.log("轮询状态异常: %s" % e, level="WARN")
        # v4.101★ 这一页没被看着、也没东西在跑时把间隔拉长(2s → 5s): 那种情况下一次网络
        #   请求都不发, 没必要 2 秒醒一次。真在跑(小抄/录制)或正看着这页时仍是 2 秒。
        try:
            gap = 2000 if (self._tab_visible() or self._last_running or self._tracing) else 5000
        except Exception:
            gap = 2000
        self._job = self.root.after(gap, self._tick_poll)

    def _apply_s3_line(self, r):
        """轮询里的 3选1 更新: 只刷那一行, 【不动状态栏】—— 否则每 2 秒会把你刚点按钮
        得到的提示(比如"已发: …")冲掉, 看着像"点了没反应"。"""
        if r and r.get("ok"):
            try:
                self.s3_var.set(self._s3_line(r))
            except Exception as e:
                self.log("3选1 状态行更新失败: %s" % e, level="WARN")

    def _poll(self):
        # v4.101★ 一轮 = 一个工作线程里串行跑完三件事(陆逊状态 + 3选1状态 + 取证行数),
        #   回来一次性刷界面。以前这三条是在 Tk 主线程里同步等: 6s + 6s + 录制时再加 20s
        #   = 界面一次被冻 12~32 秒 —— 这就是用户说的"机制页特别卡"。
        tracing = bool(self._tracing)

        def _work():
            out = {"luxun": self.mech.state(), "s3": self.mech3.state()}
            if tracing:
                try:
                    out["trace"] = self.mech_tr.stat()
                except Exception:
                    out["trace"] = None
            return out

        self._run("poll", _work, self._poll_done)

    def _poll_done(self, r, err):
        if err:
            self._status("读状态异常: %s" % err)
            return
        r = r or {}
        self._apply_s3_line(r.get("s3"))
        if r.get("trace"):
            self._apply_trace_stat(r.get("trace"), "")
        r = r.get("luxun") or {}
        if not r.get("ok"):
            self._status("读状态失败: %s" % (r.get("reason") or "?"))
            return
        st = r.get("stat") or {}
        running = bool(r.get("running"))
        s = st.get("stopReason") or ""
        if running:
            self._status("运行中…")
        elif s:
            self._status("已停止: %s" % s)
        elif st.get("stopAt"):
            self._status("已停止")
        # 手牌"没变化"轮次一并显示: 这个数在涨 = 服务器压根没吃这条指令, 白跑
        self.stat_var.set("轮次 %s · 已发 %s · 同步 %s · 刷新 %s · 失败 %s · 手牌无变化 %s%s" % (
            st.get("rounds", 0), st.get("used", 0), st.get("synced", 0),
            st.get("refreshed", 0), st.get("fails", 0), st.get("same", 0),
            (" · 最后一张: " + st["lastCard"]) if st.get("lastCard") else ""))
        hand = r.get("hand") or []
        if not hand:
            self.hand_var.set("(手牌读到 0 张 —— 没进对局, 或这个版本读不到手牌)")
        else:
            parts = []
            for h in hand:
                nm = h.get("name") or ("#" + str(h.get("goodsID") or 0))
                parts.append("%d. %s%s" % (int(h.get("idx", 0)) + 1, nm,
                                           " ✦" if h.get("spellID") else ""))
            self.hand_var.set("  ".join(parts))
        if self._last_running and not running and s:
            self.log("陆逊小抄停止: %s" % s)
        self._last_running = running
