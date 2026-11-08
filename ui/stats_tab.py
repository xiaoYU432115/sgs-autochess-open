# -*- coding: utf-8 -*-
"""『统计』页(v4.88★) —— 一局里到底刷了几次、买了什么、商店出现过哪些卡。

数从哪来
--------
游戏页(bridge/sgs_bridge.user.js 的 SgsTavernStats)【记】, 这一页【看】:
  那边包装 manager 的 ReqShopRefreshChess / ReqShopBuyChess / ReqChessUseSpell /
  updateShopGoods / onRespShopRefreshChess, 攒成一份账本; 这页点「拉取」把账本要回来,
  算出摘要 / 每回合 / 三个排行并显示, 还能存成 json(口径与上游 TavernChessStats 一致,
  能直接喂给 visualization.py 出图)。

为什么不直接装上游那个 TavernChessStats 脚本: 它是另一份独立油猴, 自己找 manager、
  自己把日志下载成文件 —— 助手这边一个数都拿不到, 想看还得"导出 → 手动导入"。
  搬进桥接脚本之后是同一套 manager、同一条回传通道, 点一下就有数。

钩子默认【关】: 只有这里点了「开始记录」才往游戏里挂, 不点的话脚本行为与以前一致。

图表为什么不用 matplotlib: 它没进 requirements.txt, 装了才能用就不叫"点开就能看"。
  这里用 tk.Canvas 直接画横条, 零依赖。想要时间线/散点/回合趋势那 9 张 PNG, 点
  「出图(visualization.py)」—— 它认的键就是我们导出的这套, 缺依赖时按钮会明说装什么。
"""

import os
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from core import tavern_stats as TS


from ui.widgets import ThemeMixin   # v4.109★ 主题色 getter 统一到 widgets.ThemeMixin


class StatsTabBuilder(ThemeMixin):
    def __init__(self, app):
        self.app = app
        self.root = app.root
        self.stats = TS.TavernStats(getattr(app, "ops", None), log=self.log)
        self._data = None          # 最近一次拉到的账本(totals/rounds/... 那一整份)
        self._auto_var = None      # 自动刷新开关
        self._auto_job = None
        self._charts = []          # (canvas, items, color) —— 窗口缩放时重画

    # ---------- 主题 ----------
    # v4.109★ 主题色 getter 已统一到 widgets.ThemeMixin(本类继承它), 不再各写一份

    def log(self, msg, level="INFO"):
        if hasattr(self.app, "log"):
            self.app.log("[统计] " + str(msg), level=level)
        else:
            print("[统计] " + str(msg))

    def _status(self, msg):
        if hasattr(self, "status_var"):
            self.status_var.set(str(msg))
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

        tk.Label(wrap, text=(
            "记一局里: 刷新了几次(花多少虎符、几次是自动刷新)、买了几次(买了什么)、\n"
            "商店每次刷出来哪些卡(每张累计出现过几次)、锦囊用了几次(哪一张)。\n"
            "统计钩子默认关闭 —— 点「开始记录」才往游戏页挂, 不点就与以前完全一样。"),
                 bg=self._bg(), fg=self._fg(), font=(self._fh(), 9),
                 justify="left").pack(anchor="w", padx=10, pady=(8, 6))

        # ---------- 控制条 ----------
        bar = tk.Frame(wrap, bg=self._bg())
        bar.pack(fill="x", padx=10, pady=(0, 6))
        for text, cmd, col in (
                ("▶ 开始记录", self._on_start, self._act()),
                ("■ 停止记录", self._on_stop, self._btn()),
                ("⟳ 拉取数据", self._on_pull, self._btn()),
                ("清空重记", self._on_reset, self._btn()),
                ("保存日志", self._on_save, self._btn()),
                ("载入历史", self._on_load, self._btn()),
                ("出图(visualization.py)", self._on_plot, self._btn()),
        ):
            tk.Button(bar, text=text, command=cmd, bg=col, fg=self._fg(),
                      font=(self._fh(), 10), relief="flat", padx=8, pady=3,
                      cursor="hand2").pack(side="left", padx=(0, 6))

        self._auto_var = tk.BooleanVar(value=False)
        tk.Checkbutton(bar, text="每 3 秒自动拉取", variable=self._auto_var,
                       command=self._on_auto_toggle, bg=self._bg(), fg=self._fg(),
                       selectcolor=self._btn(), activebackground=self._bg(),
                       activeforeground=self._fg(), font=(self._fh(), 9)
                       ).pack(side="left", padx=(10, 0))

        self.status_var = tk.StringVar(value="未开始记录")
        tk.Label(wrap, textvariable=self.status_var, bg=self._bg(), fg=self._blue(),
                 font=(self._fh(), 9), anchor="w").pack(fill="x", padx=10, pady=(0, 8))

        # ---------- 摘要 + 排行 ----------
        mid = tk.Frame(wrap, bg=self._bg())
        mid.pack(fill="x", padx=10, pady=(0, 8))

        left = tk.Frame(mid, bg=self._bg())
        left.pack(side="left", anchor="n", padx=(0, 14))
        tk.Label(left, text="整局摘要", bg=self._bg(), fg=self._gold(),
                 font=(self._fh(), 10, "bold")).pack(anchor="w")
        self.sum_tree = ttk.Treeview(left, columns=("k", "v"), show="headings",
                                     height=11, selectmode="browse")
        self.sum_tree.heading("k", text="统计项")
        self.sum_tree.heading("v", text="数值")
        self.sum_tree.column("k", width=150, anchor="w")
        self.sum_tree.column("v", width=80, anchor="e")
        self.sum_tree.pack(anchor="w")

        right = tk.Frame(mid, bg=self._bg())
        right.pack(side="left", anchor="n", fill="x", expand=True)
        tk.Label(right, text="卡牌出现次数 (商店里出现过几次)", bg=self._bg(),
                 fg=self._gold(), font=(self._fh(), 10, "bold")).pack(anchor="w")
        self.card_cv = tk.Canvas(right, bg="#232323", height=180, highlightthickness=0)
        self.card_cv.pack(fill="x", anchor="w", pady=(0, 8))
        self._charts.append([self.card_cv, [], self._green()])
        self.card_cv.bind("<Configure>", lambda e: self._redraw_charts())

        tk.Label(right, text="购买排行 (买了几次)", bg=self._bg(), fg=self._gold(),
                 font=(self._fh(), 10, "bold")).pack(anchor="w")
        self.buy_cv = tk.Canvas(right, bg="#232323", height=120, highlightthickness=0)
        self.buy_cv.pack(fill="x", anchor="w", pady=(0, 8))
        self._charts.append([self.buy_cv, [], self._blue()])
        self.buy_cv.bind("<Configure>", lambda e: self._redraw_charts())

        tk.Label(right, text="锦囊使用次数", bg=self._bg(), fg=self._gold(),
                 font=(self._fh(), 10, "bold")).pack(anchor="w")
        self.spell_cv = tk.Canvas(right, bg="#232323", height=110, highlightthickness=0)
        self.spell_cv.pack(fill="x", anchor="w")
        self._charts.append([self.spell_cv, [], "#d98cd9"])
        self.spell_cv.bind("<Configure>", lambda e: self._redraw_charts())

        # ---------- 每回合 ----------
        tk.Label(wrap, text="每回合明细", bg=self._bg(), fg=self._gold(),
                 font=(self._fh(), 10, "bold")).pack(anchor="w", padx=10, pady=(6, 2))
        self.round_tree = ttk.Treeview(wrap, columns=("r", "rf", "by", "cost", "snap"),
                                       show="headings", height=8, selectmode="browse")
        for col, head, w in (("r", "回合", 60), ("rf", "刷新", 60), ("by", "购买", 60),
                             ("cost", "虎符", 70), ("snap", "商店快照", 80)):
            self.round_tree.heading(col, text=head)
            self.round_tree.column(col, width=w, anchor="center")
        self.round_tree.pack(fill="x", padx=10, pady=(0, 10))

        self._render(None)

    # ---------- v4.101★ 后台执行(这一页以前是「卡」的重灾区) ----------
    #   统计页每一个动作(开始/停止/拉取/清空)都要跟游戏页来回一次, 而 TavernStats._cmd 的
    #   超时是【6 秒】(core/tavern_stats.py:65), send_recv 是 sleep + 轮询的阻塞等待 ——
    #   以前这些调用全在 Tk 主线程里做, 尤其「自动刷新」还是【每 3 秒】调一次 _on_pull:
    #   游戏页不回话时界面就是"冻 6 秒 → 松 3 秒 → 再冻 6 秒"的持续假死。
    #   现在一律丢工作线程, 回来用 after(0) 刷界面; 同一个动作【单飞】(上轮没回来不发新的)。
    def _run_bg(self, tag, fn, on_done=None, wait=None):
        if getattr(self, "_busy", None) is None:
            self._busy = {}
        if self._busy.get(tag):
            return False
        self._busy[tag] = True
        if wait:
            try:
                self._status(wait)
            except Exception as e:
                self.log("状态栏更新失败: %s" % e, level="WARN")

        def _work():
            r, err = None, ""
            try:
                r = fn()
            except Exception as e:
                err = str(e)

            def _done():
                self._busy[tag] = False
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
            threading.Thread(target=_work, name="stats-" + str(tag), daemon=True).start()
            return True
        except Exception as e:
            self._busy[tag] = False
            self.log("「%s」起线程失败: %s" % (tag, e), level="WARN")
            return False

    # ---------- 按钮动作 ----------
    def _on_start(self):
        self._run_bg("start", (lambda: self.stats.start(reset=True)),
                     self._on_start_done, wait="正在开启…")

    def _on_start_done(self, r, err):
        if err:
            self._status("开始异常: %s" % err)
            return
        r = r or {}
        if not r.get("ok"):
            self._status("开始失败: %s" % r.get("reason", "?"))
            return
        if r.get("reason"):
            self._status("已开启(等待中): %s" % r["reason"])
        else:
            self._status("已开始记录, 钩子: %s" % (", ".join(r.get("hooked") or []) or "(没挂上)"))

    def _on_stop(self):
        self._run_bg("stop", (lambda: self.stats.stop()), self._on_stop_done, wait="正在停止…")

    def _on_stop_done(self, r, err):
        if err:
            self._status("停止异常: %s" % err)
            return
        r = r or {}
        if not r.get("ok"):
            self._status("停止失败: %s" % r.get("reason", "?"))
            return
        self._status("已停止记录")
        self._on_pull()

    def _on_pull(self):
        self._run_bg("pull", (lambda: self.stats.dump()), self._on_pull_done, wait="正在拉取…")

    def _on_pull_done(self, r, err):
        if err:
            self._status("拉取异常: %s" % err)
            return
        r = r or {}
        if not r.get("ok"):
            self._status("拉取失败: %s" % r.get("reason", "?"))
            return
        if not r.get("stats"):
            self._status("游戏页还没有账本 —— 先点「开始记录」")
            return
        self._data = r["stats"]
        self._render(self._data)
        tag = "记录中" if r.get("on") else "已停止"
        if r.get("pending"):
            tag = "等待进对局"
        self._status("%s · 钩子:%s · 已拉取" % (tag, ", ".join(r.get("hooked") or []) or "无"))

    def _on_reset(self):
        self._run_bg("reset", (lambda: self.stats.reset()), self._on_reset_done, wait="正在清空…")

    def _on_reset_done(self, r, err):
        if err:
            self._status("清空异常: %s" % err)
            return
        r = r or {}
        self._data = None
        self._render(None)
        self._status("账本已清空" if r.get("ok") else ("清空失败: %s" % r.get("reason", "?")))

    def _on_save(self):
        if not self._data:
            messagebox.showinfo("统计", "还没有数据 —— 先「拉取」一次")
            return
        try:
            p = self.stats.save({"stats": self._data})
        except Exception as e:
            self._status("保存失败: %s" % e)
            return
        self._status("已保存: %s" % p)

    def _on_load(self):
        files = self.stats.list_saved(limit=40)
        if not files:
            messagebox.showinfo("统计", "还没有存过日志(先「保存日志」)")
            return
        win = tk.Toplevel(self.root)
        win.title("载入历史统计")
        win.configure(bg=self._bg())
        win.geometry("620x360")
        lb = tk.Listbox(win, bg="#232323", fg=self._fg(), font=(self._fh(), 9),
                        selectbackground="#0055aa", selectforeground="#ffffff")
        lb.pack(fill="both", expand=True, padx=8, pady=8)
        for p in files:
            lb.insert("end", os.path.basename(p))

        def _do(_e=None):
            sel = lb.curselection()
            if not sel:
                return
            try:
                self._data = self.stats.load(files[sel[0]])
            except Exception as e:
                self._status("载入失败: %s" % e)
                win.destroy()
                return
            self._render(self._data)
            self._status("已载入: %s" % os.path.basename(files[sel[0]]))
            win.destroy()

        lb.bind("<Double-Button-1>", _do)
        tk.Button(win, text="载入选中", command=_do, bg=self._btn(), fg=self._fg(),
                  font=(self._fh(), 10), relief="flat", padx=10, pady=4,
                  cursor="hand2").pack(pady=(0, 8))

    def _on_plot(self):
        """把当前账本交给 visualization.py 画成 9 张 PNG(时间线/散点/回合趋势那些)。

        页面里的横条图是 tk.Canvas 画的, 画不出时间线 —— 那份 visualization.py 本来就是
        上游 TavernChessStats 的配套, 认的键就是我们导出的这套, 所以直接调它, 不重复造。
        画图要几秒, 放线程里跑, 跑完只往状态栏写一行(不弹窗打断)。
        """
        if not self._data:
            messagebox.showinfo("统计", "还没有数据 —— 先「拉取」一次")
            return
        missing = TS.vis_missing_deps()
        if missing:
            messagebox.showinfo(
                "出图",
                "缺画图依赖: %s\n\n装一下就能用:\n    pip install %s\n\n"
                "(页面里的横条图不依赖它, 一直能看)"
                % ("、".join(missing), " ".join(missing)))
            return
        try:
            json_path = self.stats.save({"stats": self._data})
        except Exception as e:
            self._status("出图前保存失败: %s" % e)
            return

        def _work():
            ok, msg = TS.run_visualization(json_path)
            def _done():
                self._status(msg)
                if ok and hasattr(self.app, "log"):
                    self.app.log("[统计] " + msg)
            try:
                self.root.after(0, _done)
            except Exception:
                self._status(msg)

        threading.Thread(target=_work, name="stats-plot", daemon=True).start()
        self._status("正在出图(几秒)… 图会存进 data/tavern_stats/charts-*/")

    # ---------- 自动刷新 ----------
    def _tab_visible(self):
        """现在是不是停在「统计」这一页。"""
        try:
            nb = getattr(self.app, "notebook", None) or getattr(self.app, "nb", None)
            if nb is None:
                return True
            return nb.tab(nb.select(), "text") == "统计"
        except Exception as e:
            self.log("判断当前页失败(按可见处理): %s" % e, level="WARN")
            return True

    def _on_auto_toggle(self):
        if self._auto_var.get():
            # v4.101★ 起链前先 cancel: 以前反复勾选会【叠出好几条 3 秒链】
            #   (每条链每 3 秒拉一次), 越点越卡, 而且取消勾选只 cancel 最后一条。
            if self._auto_job:
                try:
                    self.root.after_cancel(self._auto_job)
                except Exception as e:
                    self.log("取消自动拉取失败(忽略): %s" % e, level="WARN")
                self._auto_job = None
            self._auto_loop()
        else:
            if self._auto_job:
                try:
                    self.root.after_cancel(self._auto_job)
                except Exception as e:
                    self.log("取消自动拉取失败(忽略): %s" % e, level="WARN")
                self._auto_job = None

    def _auto_loop(self):
        if not (self._auto_var and self._auto_var.get()):
            return
        # v4.182★ 性能页「暂停统计/录制重绘」: 勾了就不再每 3 秒自动拉刷(省的就是"每 3 秒
        #   重画一遍三个树 + 三张图"); 手动点「拉取」仍然照常出画面, 不会出现"点了没反应"。
        #   定时器照旧续上, 取消勾选后自动就回来了。
        if getattr(self.app, "_render_paused", None) and self.app._render_paused("stats"):
            self._auto_job = self.root.after(3000, self._auto_loop)
            return
        # v4.101★ 不在这一页时【不发请求】, 只把定时器续上: 以前切到别的页它照样每 3 秒
        #   拉一次(而且每次都在主线程等满超时), 用户的感觉是"我干别的它也卡"。
        if self._tab_visible():
            self._on_pull()
        self._auto_job = self.root.after(3000, self._auto_loop)

    # ---------- 渲染 ----------
    def _render(self, data):
        a = TS.analyze(data)
        for t in (self.sum_tree, self.round_tree):
            for iid in t.get_children():
                t.delete(iid)
        for k, v in a["summary"]:
            self.sum_tree.insert("", "end", values=(k, v))
        for r in a["rounds"]:
            self.round_tree.insert("", "end", values=(
                r["round"], r["refresh"], r["buy"], r["cost"], r["snapshots"]))
        self._charts[0][1] = a["top_cards"]
        self._charts[1][1] = a["bought"]
        self._charts[2][1] = a["top_spells"]
        self._redraw_charts()

    def _redraw_charts(self):
        for item in self._charts:
            cv, items, color = item[0], item[1], item[2]
            try:
                self._draw_bars(cv, items, color)
            except Exception as e:
                self.log("画条形图失败: %s" % e, level="WARN")

    def _draw_bars(self, cv, items, color):
        cv.delete("all")
        w = cv.winfo_width() or 420
        if not items:
            cv.configure(height=40, scrollregion=(0, 0, w, 40))
            cv.create_text(8, 20, anchor="w", text="(暂无数据)", fill=self._dim(),
                           font=(self._fh(), 9))
            return
        row_h = 19
        h = 8 + row_h * len(items)
        cv.configure(height=h, scrollregion=(0, 0, w, h))
        maxv = max(v for _, v in items) or 1
        name_w = min(150, max(80, int(w * 0.36)))
        bar_max = max(60, w - name_w - 60)
        for i, (name, v) in enumerate(items):
            y = 5 + i * row_h
            cv.create_text(4, y + 8, anchor="w", text=str(name)[:12], fill=self._fg(),
                           font=(self._fh(), 9))
            bw = int(bar_max * float(v) / float(maxv))
            cv.create_rectangle(name_w, y + 2, name_w + max(2, bw), y + 14,
                                fill=color, outline="")
            cv.create_text(name_w + max(2, bw) + 6, y + 8, anchor="w", text=str(v),
                           fill=self._gold(), font=(self._fh(), 9))
