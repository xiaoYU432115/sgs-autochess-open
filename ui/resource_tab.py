# -*- coding: utf-8 -*-
"""资源替换 Tab(v4.70★) —— 管理背景、棋子立绘、主公立绘等本地替换规则。

设计约束
========
· 不直接操作游戏: 只生成 Redirector / GoRes / 皮肤替换脚本可吃的规则。
· 自定义规则存在 data/resource_rules.json, 与赛季默认规则合并后生成规则文件。
· 所有耗时的文件/配置表读取都放子线程, 回主线程刷新界面。

v4.70★ 本版按用户这几个问题改的
================================
1) "那个换肤有js了吗? 或者写进那里面sgs里面?"
   → 有, 而且现在【就写在桥接脚本里】(bridge/sgs_bridge.user.js 的「资源替换」段):
     不用再装第二个油猴脚本、更不用 Redirector/GoRes。原理见下面第 3 条。
     bridge/sgs_skin_swap.user.js 仍然保留 —— 那是"只想要换肤、连助手都不开"时的独立版,
     它检测到桥接脚本在场就自动让位, 不会两边抢着改同一个 URL。
2) "换皮肤那, 武将改动是已经要把三国杀服务器的加载好了对应的武将名字皮肤可以切换;
   然后还可以自定义"
   → 「目标类型」多了第三种: 「官方武将立绘」+ 挑选框。
     列的是配置表里确有其名的官方立绘(棋子立绘 / 武将牌面 / 主公立绘, 共 581 条),
     按武将名排, 并且标出哪套是【本赛季在用的】—— 19 位武将有两套(如 陆逊 luxun / luxun_02),
     可以直接来回切换。自定义照旧: 本地图片 / 任意网络地址。
3) "我不想再加一个扩展插件: gores了, 你把原理学下来, 实现我们的功能"
   → GoRes/Redirector 的原理就一句话: "看到 URL 含 A, 就把请求换成 B"。
     浏览器扩展是拿 webRequest 权限做的; 油猴脚本没那个权限, 但可以**在页面里改请求**:
       · Laya 加载贴图走 new Image().src / setAttribute('src')  → 改写这两个
       · 配置/图集走 XMLHttpRequest.open()                        → 改写 URL
       · 少数走 fetch() / style.backgroundImage                   → 一并改写
     命中规则就换成目标地址。所以功能一样, 且不需要装扩展。
4) "这个图片是另起一个服务端口吗? 在那个端口整图片? 还是就是本地的?"
   → v4.70 起【不再是另一个端口】: /rules 和 /img/xxx.png 都挂在助手主端口上
     (core/bridge.py 上的路由, 实现复用 core/res_server.py)。
     上一版要手点「启动图片服务」才会起 8767 —— 忘了点, 规则目标就指向空端口,
     现象是"图配好了却没生效"还看不出是谁的锅。现在助手在跑, 地址就是活的。
5) 顺带修一个会让"棋子不显示"的坑: 旧版把 360+ 条规则(绝大多数没放图)全写进 txt,
   照着一导入, 那 350 多个资源全部 404。现在默认【只导出已放图的】, 可以用勾选框改回去。
"""
import json
import os
import re
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from core import resource_rules as RR

try:
    from core import res_server as RS
except Exception:      # 缺这个模块不影响规则编辑, 只是不能一键起独立图片服务
    RS = None


def _short_path(p):
    """绝对路径 → "data/season/s10/TavernChessConfig.json" 这种给人看的短路径(状态栏用)。"""
    t = str(p or "").replace("\\", "/")
    for anchor in ("/data/", "/bridge/", "/core/"):
        i = t.find(anchor)
        if i >= 0:
            return t[i + 1:]
    return t


from ui.widgets import ThemeMixin   # v4.109★ 主题色 getter 统一到 widgets.ThemeMixin


class ResourceTabBuilder(ThemeMixin):
    def __init__(self, app):
        self.app = app
        self.root = app.root
        self._all_rules = []          # 未过滤的全量(搜索用)
        self._custom = []
        self._skins = None            # 官方立绘清单(第一次打开挑选框时现读, 之后缓存)
        # v4.71★ 「核对官方图」的结果 {匹配串: 查不到的原因} —— 在线核对过"服务器上没有
        #   这张"的资源在这里留个记号, 表格『这是什么』那列会红字标出来, 免得用户
        #   挑了半天才发现那套皮肤根本不存在。
        self._probe = {}
        # v4.72★ 「量尺寸」的结果 {匹配串: rule_size_info(...)} —— 用户原话:
        #   "还有那个替换前的尺寸大小, 分辨率, 像素多少? 要不换进去, 好乱都变形了"。
        #   存下来有两个用处: 表格那一列直接显示"原图 W×H → 你的 W×H"; 预览时顺手带上
        #   (预览【不自己去量】—— 量 360 条要发几百个请求, 预览必须秒开)。
        self._sizes = {}
        # 「尺寸会变形」的警告每条只弹一次 —— 否则连着双击十行会被弹十次, 用户只能去点确定
        self._size_warned = set()
        # v4.74★ 候选表 iid → 候选 dict(以前只存一行 URL 文本, 于是"建规则"时只能拿整条 URL
        #   当匹配串 —— 带域名的匹配串只有一条线路能命中, 是"配了不生效"的老来源之一)
        self._cand_map = {}
        # v4.74★ 官方大图/皮肤资源清单缓存(见 data/official_resources.json)
        self._official_items = []

    # v4.109★ 主题色 getter 已统一到 widgets.ThemeMixin(本类继承它), 不再各写一份

    # ---------- 日志 ----------
    def log(self, msg, level="INFO"):
        if hasattr(self.app, "log"):
            self.app.log("[资源] " + msg, level=level)
        else:
            print("[资源] " + msg)
        # v4.110★ 本页日志框也留一份 —— 照『赛季更新』页: 这一页做过什么, 抬头就看见,
        #   不用切到主日志去翻。状态栏只留最后一行, 一闪就过去了(用户原话"不知道成没成")。
        self._log_local(msg, level)

    def _log_local(self, msg, level="INFO"):
        """往本页那个日志框追加一行(带时间; WARN/ERROR 会带个标记)。"""
        w = getattr(self, "_log_text", None)
        if w is None:                      # 页面还没建好时 log 就可能被调用
            return
        try:
            tag = "" if str(level).upper() == "INFO" else "[%s] " % level
            w.configure(state="normal")
            w.insert("end", "%s %s%s\n" % (time.strftime("%H:%M:%S"), tag, msg))
            w.see("end")
            # 只留最近 400 行 —— 扫一次资源能刷出上百行, 不限量会越堆越卡
            n = int(w.index("end-1c").split(".")[0])
            if n > 400:
                w.delete("1.0", "%d.0" % (n - 400))
            w.configure(state="disabled")
        except Exception as e:
            # 写不进去也不能让功能挂掉(例如页面已经销毁、还在往里写)
            print("[资源] 写本页日志框失败: %s" % e)

    def _clear_log(self):
        w = getattr(self, "_log_text", None)
        if w is None:
            return
        try:
            w.configure(state="normal")
            w.delete("1.0", "end")
            w.configure(state="disabled")
        except Exception as e:
            print("[资源] 清空日志框失败: %s" % e)

    def _status(self, msg):
        if hasattr(self, "status_var"):
            self.status_var.set(msg)
        self.log(msg)

    # ---------- 构建 Tab ----------
    def build(self, parent):
        # v4.68★ 内容较多, 套上 Canvas+Scrollbar 防截断(复用 app 的通用方法)。
        # v4.74★ hscroll=True: 再加一条横向滚动条 —— 表格/说明行长, 窗口窄时左右滑着看。
        mk = getattr(self.app, "_make_scrollable_tab", None)
        if mk:
            try:
                wrap = mk(parent, pady=8, hscroll=True)
            except TypeError:
                wrap = mk(parent, pady=8)   # 旧版 app 没有 hscroll 参数
        else:
            wrap = tk.Frame(parent, bg=self._bg())
            wrap.pack(fill="both", expand=True, padx=10, pady=8)

        # ⓪ 状态 —— 照『赛季更新』页那个"当前状态"段的写法: 一进来先看见"现在是什么情况",
        #   不用自己盯着表数。用户原话: "改的没改的、是不是最新、查到没查到的分不清"。
        box0 = tk.LabelFrame(wrap, text=" ⓪ 现在什么情况 ",
                             bg=self._bg(), fg=self._gold(),
                             font=(self._fh(), 10, "bold"), bd=1, relief="groove")
        box0.pack(fill="x", pady=(0, 6))
        self.state_var = tk.StringVar(value="读取中…")
        tk.Label(box0, textvariable=self.state_var, bg=self._bg(), fg=self._fg(),
                 font=(self._fh(), 9), justify="left", anchor="w").pack(fill="x", padx=8, pady=(6, 4))
        r0 = tk.Frame(box0, bg=self._bg())
        r0.pack(fill="x", padx=6, pady=(0, 6))
        tk.Button(r0, text="立刻同步到浏览器", command=self._sync_browser,
                  bg=self._act(), fg=self._green(), font=(self._fh(), 9, "bold"),
                  padx=8, cursor="hand2").pack(side="left", padx=(0, 6))
        tk.Button(r0, text="查看全部数据(网页图库)", command=self._open_gallery,
                  bg=self._act(), fg=self._gold(), font=(self._fh(), 9, "bold"),
                  padx=8, cursor="hand2").pack(side="left", padx=(0, 6))
        tk.Button(r0, text="缺图体检", command=self._open_health,
                  bg=self._act(), fg=self._blue(), font=(self._fh(), 9, "bold"),
                  padx=8, cursor="hand2").pack(side="left", padx=(0, 6))
        # v4.110★ 用户原话"获取及时换肤的注入规则" —— 光看助手这边的表不算数, 浏览器里
        #   __sgsResSwap.state 才是真相: 它现在生效几条、命中过几次、规则什么版本/来源。
        tk.Button(r0, text="浏览器里现在生效的", command=self._view_injected,
                  bg=self._act(), fg=self._blue(), font=(self._fh(), 9, "bold"),
                  padx=8, cursor="hand2").pack(side="left", padx=(0, 6))
        tk.Button(r0, text="刷新", command=self._load_rules,
                  bg=self._btn(), fg=self._fg(), font=(self._fh(), 9),
                  padx=8, cursor="hand2").pack(side="left", padx=(0, 6))
        tk.Label(r0, text="改完规则要点一次『立刻同步』, 状态才会变绿(脚本自己也会 15 秒拉一次)",
                 bg=self._bg(), fg=self._dim(), font=(self._fh(), 8)).pack(side="left", padx=(4, 0))

        # ① 规则从哪来(赛季 / 读默认 / 添加 / 导入)
        bar1box = tk.LabelFrame(wrap, text=" ① 规则从哪来 ",
                                bg=self._bg(), fg=self._gold(),
                                font=(self._fh(), 10, "bold"), bd=1, relief="groove")
        bar1box.pack(fill="x", pady=(0, 6))
        bar = tk.Frame(bar1box, bg=self._bg())
        bar.pack(fill="x", padx=6, pady=6)

        tk.Label(bar, text="赛季:", bg=self._bg(), fg=self._fg(),
                 font=(self._fh(), 9)).pack(side="left")
        # v4.74★ 赛季: 从"光杆输入框"改成【下拉 + 选完立刻重读 + 状态栏说清楚】
        #   用户原话"左上角的赛季也没有效果啊"。旧版这个 Entry: ① 没绑任何事件, 改完必须
        #   再手动点一次「读取默认规则」才生效; ② 里面一直是空的(没有任何地方给它设过初值),
        #   用户根本不知道该填什么; ③ 填一个 data/season 下没有的赛季名时静默回退到别的
        #   赛季, 界面上零反馈。现在: 下拉列出 data/season 下真实存在的赛季(★ = 有配置表,
        #   能读), 选中/回车/失焦都立刻重读, 并把"实际读的是哪张表"写进状态栏。
        self.season_var = tk.StringVar(value="")
        self.season_cb = ttk.Combobox(bar, textvariable=self.season_var, width=9,
                                      values=RR.season_choices(), state="normal")
        self.season_cb.pack(side="left", padx=(2, 8))
        self.season_cb.bind("<<ComboboxSelected>>", lambda _e: self._on_season_change())
        self.season_cb.bind("<Return>", lambda _e: self._on_season_change())
        self.season_cb.bind("<FocusOut>", lambda _e: self._on_season_change())

        tk.Button(bar, text="读取默认规则", command=self._load_defaults,
                  bg=self._act(), fg=self._gold(), font=(self._fh(), 9, "bold"),
                  padx=8, cursor="hand2").pack(side="left", padx=(0, 6))
        tk.Button(bar, text="添加自定义规则 …", command=self._add_rule,
                  bg=self._act(), fg=self._fg(), font=(self._fh(), 9, "bold"),
                  padx=8, cursor="hand2").pack(side="left", padx=(0, 6))
        tk.Button(bar, text="从 tsv 导入", command=self._import_tsv,
                  bg=self._btn(), fg=self._blue(), font=(self._fh(), 9, "bold"),
                  padx=8, cursor="hand2").pack(side="left", padx=(0, 6))

        # ② 动作行: 搜索 + 类别 + 服务 + 采集 + 生成(照赛季页的写法给它一个带编号的框 ——
        #   以前这十几颗按钮是裸的一行, 跟上面工具栏、下面表格糊在一起, 分不清哪颗管什么)
        bar2box = tk.LabelFrame(wrap, text=" ② 动作(搜规则 · 开服务 · 采集资源 · 生成规则) ",
                                bg=self._bg(), fg=self._gold(),
                                font=(self._fh(), 10, "bold"), bd=1, relief="groove")
        bar2box.pack(fill="x", pady=(0, 6))
        bar2 = tk.Frame(bar2box, bg=self._bg())
        bar2.pack(fill="x", padx=6, pady=6)

        # v4.69★ 搜索框 —— 直接过滤下面的规则树(名称/匹配串/目标/类别 任一包含即命中)
        tk.Label(bar2, text="搜索:", bg=self._bg(), fg=self._fg(),
                 font=(self._fh(), 9)).pack(side="left")
        self.search_var = tk.StringVar(value="")
        ent = tk.Entry(bar2, textvariable=self.search_var, width=22, bg=self._btn(), fg=self._fg(),
                       insertbackground=self._fg(), font=(self._fh(), 9))
        ent.pack(side="left", padx=(2, 6))
        ent.bind("<KeyRelease>", lambda _e: self._apply_filter())
        tk.Button(bar2, text="×", command=lambda: (self.search_var.set(""), self._apply_filter()),
                  bg=self._btn(), fg=self._dim(), font=(self._fh(), 9), padx=5,
                  cursor="hand2").pack(side="left", padx=(0, 6))

        tk.Label(bar2, text="只看:", bg=self._bg(), fg=self._fg(),
                 font=(self._fh(), 9)).pack(side="left")
        self.kind_var = tk.StringVar(value="全部")
        kind_cb = ttk.Combobox(bar2, textvariable=self.kind_var, state="readonly", width=12,
                               values=["全部", "棋子立绘", "武将牌面", "主公立绘", "主公立绘(旧路径)",
                                       "背景", "音效", "其他", "自定义"])
        kind_cb.pack(side="left", padx=(2, 8))
        kind_cb.bind("<<ComboboxSelected>>", lambda _e: self._apply_filter())

        # v4.70★ 资源服务 = 助手主端口(不是第二个端口)。助手在跑它就在, 不用点任何东西。
        #   剩下那个"独立图片服务"只是"不想开助手时"的备选, 所以按钮名里写明"可选"。
        self.srv_var = tk.StringVar(value="资源服务: 助手主端口")
        tk.Button(bar2, text="资源服务页", command=self._open_res_page,
                  bg=self._act(), fg=self._blue(), font=(self._fh(), 9, "bold"),
                  padx=8, cursor="hand2").pack(side="left", padx=(0, 6))
        # v4.74★ 资源图库(/gallery) —— 预览页的可操作版: 点卡片选图/编辑/启停/导入导出,
        #   保存后 rev 变 → 脚本/扩展 ≤20 秒自动重拉 → 游戏里换图(不用重启助手)。
        tk.Button(bar2, text="资源图库(选图/编辑)", command=self._open_gallery,
                  bg=self._act(), fg=self._gold(), font=(self._fh(), 9, "bold"),
                  padx=8, cursor="hand2").pack(side="left", padx=(0, 6))
        # v4.87★ 体检 —— 用户原话"我现在改的有点乱; 还有不全": 规则配错【不报错】,
        #   所以给个入口把"配了但不会生效"的(缺图/重复/没填目标)逐条点名。
        tk.Button(bar2, text="缺图体检", command=self._open_health,
                  bg=self._act(), fg=self._green(), font=(self._fh(), 9, "bold"),
                  padx=8, cursor="hand2").pack(side="left", padx=(0, 6))
        # v4.106★ 用户原话: "资源替换的原始资源, 怎么查找呢? 加个按钮? 跟赛季更新那样?"
        #   扫描一直都有(_scan_official_res: 让浏览器把"真的加载过哪些 res/assets URL"交回来),
        #   但它埋在下面「官方大图 / 皮肤资源」那个框里 —— 最常用的动作提到顶栏, 一眼就看到:
        #   在游戏里逛一圈(看皮肤/打一局) → 回助手点它 → 清单自己长出来 → 双击一行即配成规则。
        tk.Button(bar2, text="扫描游戏页资源(抓原始图/皮肤)", command=self._scan_official_res,
                  bg=self._act(), fg=self._gold(), font=(self._fh(), 9, "bold"),
                  padx=8, cursor="hand2").pack(side="left", padx=(0, 6))
        # v4.87★ 用户原话"也没有按钮啊?" —— 最常用的两件事直接给按钮, 不用先开网页:
        #   打开图片目录(放图) / 服务状态与日志(查"到底生效没有")。
        tk.Button(bar2, text="打开我的图文件夹", command=self._open_img_dir,
                  bg=self._act(), fg=self._gold(), font=(self._fh(), 9, "bold"),
                  padx=8, cursor="hand2").pack(side="left", padx=(0, 6))
        tk.Button(bar2, text="服务状态/日志", command=self._open_status,
                  bg=self._act(), fg=self._green(), font=(self._fh(), 9, "bold"),
                  padx=8, cursor="hand2").pack(side="left", padx=(0, 6))
        tk.Button(bar2, text="独立图片服务(可选)", command=self._start_server,
                  bg=self._btn(), fg=self._dim(), font=(self._fh(), 9),
                  padx=8, cursor="hand2").pack(side="left", padx=(0, 6))
        tk.Button(bar2, text="换肤脚本安装", command=self._open_swap_script,
                  bg=self._btn(), fg=self._gold(), font=(self._fh(), 9),
                  padx=8, cursor="hand2").pack(side="left", padx=(0, 6))
        # v4.70★ MV3 浏览器扩展(可选): 走 declarativeNetRequest 在网络层重定向,
        #   覆盖面比脚本那四条 hook 宽(图集 .atlas / 语音 .mp3 这类不走路由的也能换)。
        #   只是**下载** zip, 装不装在浏览器那边(chrome://extensions 加载已解压的扩展程序)。
        tk.Button(bar2, text="浏览器扩展(zip, 可选)", command=self._open_ext_zip,
                  bg=self._btn(), fg=self._gold(), font=(self._fh(), 9),
                  padx=8, cursor="hand2").pack(side="left", padx=(0, 6))
        # v4.81★ 全能扩展【不再内置】主脚本 —— 它每次从助手 /ext/bridge.main.js 取。
        #   所以"本项目里改完脚本 → 让扩展换新的"就是这一个按钮: 推一下发布时间戳,
        #   rev 变 → 扩展(≤20 秒一次)自动取走新的一版。不用重跑打包、不用重载扩展。
        tk.Button(bar2, text="更新全能扩展脚本", command=self._update_ext_script,
                  bg=self._act(), fg=self._gold(), font=(self._fh(), 9, "bold"),
                  padx=8, cursor="hand2").pack(side="left", padx=(0, 6))
        tk.Label(bar2, textvariable=self.srv_var, bg=self._bg(), fg=self._dim(),
                 font=(self._fh(), 8)).pack(side="left", padx=(0, 8))

        self.only_existing_var = tk.BooleanVar(value=True)
        tk.Checkbutton(bar2, text="只导出已放图的规则(防 404)", variable=self.only_existing_var,
                       bg=self._bg(), fg=self._fg(), selectcolor="#33333f",
                       activebackground=self._bg(), font=(self._fh(), 9)).pack(side="left", padx=(0, 8))

        tk.Button(bar2, text="生成扩展规则", command=self._generate,
                  bg=self._green(), fg=self._bg(), font=(self._fh(), 10, "bold"),
                  padx=10, cursor="hand2").pack(side="right", padx=(0, 6))
        # (v4.110★ 这里原本还有一颗「打开图片目录」, 跟上面那颗「打开我的图文件夹」
        #   是同一个动作 —— 一行里出现两次, 删掉右面这个, 免得以为是两个功能)

        # ③ 规则列表
        listbox = tk.LabelFrame(wrap, text=" ③ 当前规则(自定义优先覆盖默认) ",
                                bg=self._bg(), fg=self._gold(),
                                font=(self._fh(), 10, "bold"), bd=1, relief="groove")
        listbox.pack(fill="both", expand=True, pady=(0, 6))

        cols = ("sync", "kind", "name", "pattern", "target", "exists", "enabled", "note", "size")
        self.tree = ttk.Treeview(listbox, columns=cols, show="headings", height=10)
        # v4.110★ 状态列放最前面: 用户原话"改的没改的、是不是最新、查到没查到全分不清"。
        #   以前只能靠「已就绪 / 启用」两列自己推, 推不出"这条到底推出去没有"。
        self.tree.heading("sync", text="状态")
        # 配色: 绿=已生效 黄=改过还没推 灰=没推过/停用 警示色=缺图(这条根本不会生效)
        self.tree.tag_configure("s_ok", foreground=self._green())
        self.tree.tag_configure("s_dirty", foreground=self._gold())
        self.tree.tag_configure("s_never", foreground=self._dim())
        self.tree.tag_configure("s_miss", foreground=self._warn())
        self.tree.tag_configure("s_off", foreground=self._dim())
        self.tree.heading("kind", text="类型")
        self.tree.heading("name", text="名称")
        self.tree.heading("pattern", text="匹配串")
        self.tree.heading("target", text="目标(本地文件 / 网络地址)")
        self.tree.heading("exists", text="已就绪")
        self.tree.heading("enabled", text="启用")
        # v4.71★ 用户原话: "也备注好这都是什么" —— 每一行都把"这是什么资源"写清楚,
        #   否则 360 条默认规则里那三列(piece/card/general)只有作者自己分得清。
        self.tree.heading("note", text="这是什么")
        # v4.72★ 用户原话: "那个替换前的尺寸大小, 分辨率, 像素多少? 换个图进去好乱都变形了"。
        #   为什么这列是"能不能换成功"的关键: 游戏是按写死的矩形画贴图的(见 core/resource_rules.py
        #   顶部那段说明), 宽高比和原图不一致就一定是拉伸 —— 那么"原图多大"就是唯一的正确依据。
        self.tree.heading("size", text="尺寸 原图 → 你的图")
        self.tree.column("sync", width=74, anchor="center")
        self.tree.column("kind", width=118, anchor="w")
        self.tree.column("name", width=100, anchor="w")
        self.tree.column("pattern", width=230, anchor="w")
        self.tree.column("target", width=185, anchor="w")
        self.tree.column("exists", width=62, anchor="center")
        self.tree.column("enabled", width=44, anchor="center")
        self.tree.column("note", width=215, anchor="w")
        self.tree.column("size", width=175, anchor="w")
        vs = tk.Scrollbar(listbox, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.pack(side="left", fill="both", expand=True, padx=6, pady=6)
        vs.pack(side="right", fill="y", pady=6)

        self.tree.bind("<<TreeviewSelect>>", lambda _e: None)
        # v4.71★ 双击改成「预览」(原来是「编辑」)。
        #   理由: 用户问"有预览吗" —— 看这张资源现在长什么样、我要换成什么, 是比改匹配串
        #   高频得多的动作; 编辑还在按钮栏里, 没被拿走。
        self.tree.bind("<Double-1>", lambda _e: self._preview_selected())

        btn_bar = tk.Frame(listbox, bg=self._bg())
        btn_bar.pack(fill="x", padx=6, pady=(0, 6))
        tk.Button(btn_bar, text="编辑", command=self._edit_selected,
                  bg=self._btn(), fg=self._fg(), font=(self._fh(), 9)).pack(side="left", padx=(0, 6))
        # v4.69★ 默认规则也能"拿来改" —— 复制成一条自定义规则(匹配串原样带过来)
        tk.Button(btn_bar, text="转成自定义(可编辑)", command=self._clone_to_custom,
                  bg=self._act(), fg=self._gold(), font=(self._fh(), 9, "bold")).pack(side="left", padx=(0, 6))
        tk.Button(btn_bar, text="给这条换图 …", command=self._set_target_for_selected,
                  bg=self._btn(), fg=self._blue(), font=(self._fh(), 9)).pack(side="left", padx=(0, 6))
        tk.Button(btn_bar, text="删除", command=self._delete_selected,
                  bg=self._btn(), fg=self._red(), font=(self._fh(), 9)).pack(side="left", padx=(0, 6))
        tk.Button(btn_bar, text="切换启用", command=self._toggle_selected,
                  bg=self._btn(), fg=self._fg(), font=(self._fh(), 9)).pack(side="left", padx=(0, 6))
        # v4.71★ 预览三件套 —— 用户原话: "有预览吗? 全部显示出来能够替换的"。
        #   ① 预览选中: 双击一行也行(看"这张资源现在长什么样、我要换成什么");
        #   ② 预览全部: 生成一个网页图墙(360+ 条并排看, tkinter 里塞 700 张图会卡死);
        #   ③ 核对官方图: HEAD 问一次服务器"这张在不在" —— 光拼 URL 不算查到,
        #      服务器上没有那个文件时规则一配就是 404(棋子变透明), 必须问过才敢说"能换"。
        tk.Button(btn_bar, text="预览选中", command=self._preview_selected,
                  bg=self._act(), fg=self._green(), font=(self._fh(), 9, "bold")
                  ).pack(side="left", padx=(0, 6))
        tk.Button(btn_bar, text="预览全部(网页图墙)", command=self._preview_all,
                  bg=self._btn(), fg=self._blue(), font=(self._fh(), 9)).pack(side="left", padx=(0, 6))
        tk.Button(btn_bar, text="核对官方图", command=self._probe_official,
                  bg=self._btn(), fg=self._blue(), font=(self._fh(), 9)).pack(side="left", padx=(0, 6))
        # v4.72★ 用户原话: "那个替换前的尺寸大小, 分辨率, 像素多少? 要不换进去, 好乱都变形了"。
        #   为什么不能自动量: 官方图有几百张, 每张要发一个请求 —— 打开页面就自动发几百个请求
        #   是不礼貌的(而且慢)。所以做成按钮: 选中就量选中的, 没选中就量全部已就绪的。
        #   量到的尺寸有缓存(data/image_sizes.json), 第二次点几乎是瞬时的。
        tk.Button(btn_bar, text="量尺寸(会变形吗)", command=self._measure_sizes,
                  bg=self._act(), fg=self._green(), font=(self._fh(), 9, "bold")
                  ).pack(side="left", padx=(0, 6))
        # v4.71★ 用户原话: "我换完, 点 python 端就能同步更新的操作?" —— 就是这个按钮。
        #   脚本自己每 15 秒拉一次 /rules(见 sgs_bridge.user.js 的 SWAP_POLL_MS), 所以
        #   "换完等 15 秒"本来也会生效; 但用户要的是"点一下就同步", 所以这里直接给页面
        #   发一条 js 指令让它【立刻】重拉, 并回读当前生效条数确认(不然又是"点了不知道成没成")。
        tk.Button(btn_bar, text="立即同步到浏览器", command=self._sync_browser,
                  bg=self._act(), fg=self._gold(), font=(self._fh(), 9, "bold")
                  ).pack(side="left", padx=(0, 6))
        # v4.70★ 批量自检: 把"匹配串本赛季命中不了"的规则一次摊开(并支持一键修正)。
        #   为什么值得一个按钮 —— 匹配串写错【不会报错、没有日志、界面全显示"已就绪"】,
        #   唯一的现象就是"游戏里没变化", 用户只能一条条猜(见用户原话"我用这俩换咋都不行")。
        tk.Button(btn_bar, text="自检自定义规则", command=self._check_custom_rules,
                  bg=self._act(), fg=self._blue(), font=(self._fh(), 9, "bold")
                  ).pack(side="right", padx=(0, 6))

        # v4.71★ 用法说明 —— 用户原话"看不懂怎么用"。
        #   这张表上 4 个列的含义(App 里没人给他讲): 匹配串/目标/已就绪/启用, 以及最关键的
        #   "默认规则那 360 条为什么全显示'待放图'"(因为我的图/ 里还没有对应的图)。
        tk.Label(listbox, text=(
            "怎么用: ① 把图按资源名放进 我的图/(例: luxun_02.png, 名字取『名称』列那个); "
            "或选中一行点「给这条换图 …」选本地图; 或用「挑选官方立绘」直接用服务器上那张(不用下载)。"
            "  ② 点「生成扩展规则」。  ③ 点「立即同步到浏览器」(不点也行, 脚本 15 秒内自己会拉)。\n"
            "『已就绪』= 目标图准备好了; 默认规则显示『待放图』就是 我的图/ 里还缺这张图(缺图导出会 404, "
            "所以默认只导出已就绪的)。『启用』= 这条规则生效吗 —— 默认规则也能停用(会存成一条同名自定义规则压住它)。\n"
            "『尺寸』那列 = 原图多大 → 你这张多大。为什么要看它: 游戏是把图【塞进一个写死的矩形】画的, "
            "比例和原图不一样就一定会被拉伸(这就是【换进去好乱】的原因), 所以按原图尺寸裁最稳; "
            "比例不一致的会标 ⚠变形。点「量尺寸(会变形吗)」量官方原图(只取前 4KB, 不下载整张)。\n"
            "动态图/视频: 【不行】—— 背景和皮肤在游戏里是画布贴图(gif/webp 动图只会显示第一帧), "
            "mp4 更不会被当图片解码, 那一格会直接变空白(要播视频得往页面注入播放器, 会盖住整张棋盘)。"),
            bg=self._bg(), fg=self._dim(), font=(self._fh(), 8), wraplength=1180,
            justify="left", anchor="w").pack(fill="x", padx=6, pady=(0, 4))

        # ④ v4.74★ 官方大图 / 皮肤资源清单
        #   用户原话: "现在本项目中的资源替换里面没有这样的图片啊; 这个是刘备的皮肤(500103/500104)…
        #    这个是神马超(621401)… 这个是动态神马超(821401)… 这些能直接也加到可以替换的资源里面;
        #    这个资源可以获取吧? 新建一个放这个"
        #   为什么必须单开一块: 下面 ④ 那个候选区的来源是诊断包里 atlas 的【帧名】, 拼出来的地址
        #   是死的(游戏加载的是整张图集, 从来不会单独请求帧名); 而 OL 大图/动态皮肤是【一个个独立
        #   的 png】, 只能靠"页面真的加载过"或"HEAD 问一下官方在不在"拿到。清单落在
        #   data/official_resources.json, 双击一行就用它建规则。
        big_box = tk.LabelFrame(
            wrap, text=" ④ 官方大图 / 皮肤资源(刘备 500103 · 神马超 621401 · 动态 821401 这类)"
                       " —— 双击一行 = 把它配成可替换资源 ",
            bg=self._bg(), fg=self._gold(), font=(self._fh(), 10, "bold"), bd=1, relief="groove")
        big_box.pack(fill="x", pady=(0, 6))

        big_cols = ("kind", "pattern", "size", "src")
        self.big_tree = ttk.Treeview(big_box, columns=big_cols, show="headings", height=6)
        for cid, txt, cw in (("kind", "类型", 90), ("pattern", "资源(匹配串)", 460),
                             ("size", "尺寸/大小", 150), ("src", "来源", 140)):
            self.big_tree.heading(cid, text=txt)
            self.big_tree.column(cid, width=cw, anchor="w")
        bvs = tk.Scrollbar(big_box, orient="vertical", command=self.big_tree.yview)
        self.big_tree.configure(yscrollcommand=bvs.set)
        self.big_tree.pack(side="left", fill="x", expand=True, padx=6, pady=6)
        bvs.pack(side="right", fill="y", pady=6)
        self.big_tree.bind("<Double-1>", lambda _e: self._use_official_res())

        bbtns = tk.Frame(big_box, bg=self._bg())
        bbtns.pack(side="right", fill="y", padx=6, pady=6)
        # v4.86★ 新加的三个"一键套装"按钮原先混在七个按钮里, 用户一眼扫过去当成了老按钮
        #   (原话: "没看到?") —— 单独给个金色小标题把它们拎出来。
        tk.Label(bbtns, text="▼ 一键套装(v4.86)", bg=self._bg(), fg=self._gold(),
                 font=(self._fh(), 9, "bold"), anchor="w").pack(fill="x", pady=(0, 4))
        tk.Button(bbtns, text="扫描游戏已加载资源", command=self._scan_official_res,
                  bg=self._act(), fg=self._gold(), font=(self._fh(), 9, "bold"),
                  padx=8, cursor="hand2").pack(fill="x", pady=(0, 4))
        # v4.86★ 用户原话: "扫描需要我都点一遍吗? 你不能去获取吗?" —— 能: 皮肤表里有大图编号,
        #   按编号去问官方服务器就行, 不用在游戏里逛任何界面。
        tk.Button(bbtns, text="联网批量探测(不用逛) …", command=self._probe_dress_batch,
                  bg=self._act(), fg=self._blue(), font=(self._fh(), 9, "bold"),
                  padx=8, cursor="hand2").pack(fill="x", pady=(0, 4))
        tk.Button(bbtns, text="按编号探测 …", command=self._probe_big_id,
                  bg=self._act(), fg=self._blue(), font=(self._fh(), 9, "bold"),
                  padx=8, cursor="hand2").pack(fill="x", pady=(0, 4))
        tk.Button(bbtns, text="自走棋背景全套 …", command=self._bg_bundle,
                  bg=self._act(), fg=self._gold(), font=(self._fh(), 9, "bold"),
                  padx=8, cursor="hand2").pack(fill="x", pady=(0, 4))
        tk.Button(bbtns, text="OL大图/动态皮肤 …", command=self._dress_bundle,
                  bg=self._act(), fg=self._blue(), font=(self._fh(), 9, "bold"),
                  padx=8, cursor="hand2").pack(fill="x", pady=(0, 4))
        # v4.111★ 动态皮整套替换【搬到『动态皮肤』页】了 —— 用户原话: "我感觉渲染和 png
        #   替换不一样; 这俩放一起会乱"。这里只留一个指路按钮, 免得老入口找不着。
        tk.Button(bbtns, text="动态皮(skel+atlas+png)→ 『动态皮肤』页",
                  command=self._goto_dynskin,
                  bg=self._act(), fg=self._gold(), font=(self._fh(), 9, "bold"),
                  padx=8, cursor="hand2").pack(fill="x", pady=(0, 4))
        tk.Button(bbtns, text="让背景动起来(轮播) …", command=self._anim_bg,
                  bg=self._act(), fg=self._green(), font=(self._fh(), 9, "bold"),
                  padx=8, cursor="hand2").pack(fill="x", pady=(0, 4))
        # v4.87★ 用户原话: "自走棋图集, 好像是一个剪辑图, 里面有很多元素, 取一部分就是一个图"
        #   —— 官方界面资源很多是【图集】(一整张 png + .atlas 清单), 换它 = 整张一起换。
        #   这个窗口就是用来"从整张里取一块 / 把我的图贴回某一块"的。
        tk.Button(bbtns, text="图集取图/贴图 …", command=self._atlas_tool,
                  bg=self._act(), fg=self._gold(), font=(self._fh(), 9, "bold"),
                  padx=8, cursor="hand2").pack(fill="x", pady=(0, 4))
        tk.Button(bbtns, text="预览(浏览器打开)", command=self._preview_official,
                  bg=self._btn(), fg=self._blue(), font=(self._fh(), 9),
                  padx=8, cursor="hand2").pack(fill="x", pady=(0, 4))
        tk.Button(bbtns, text="替换成我的图 …", command=self._use_official_res,
                  bg=self._btn(), fg=self._green(), font=(self._fh(), 9),
                  padx=8, cursor="hand2").pack(fill="x", pady=(0, 4))
        tk.Button(bbtns, text="当替换目标(皮肤切换)", command=self._set_official_target,
                  bg=self._btn(), fg=self._fg(), font=(self._fh(), 9),
                  padx=8, cursor="hand2").pack(fill="x", pady=(0, 4))
        tk.Button(bbtns, text="删除选中", command=self._del_official_res,
                  bg=self._btn(), fg=self._red(), font=(self._fh(), 9),
                  padx=8, cursor="hand2").pack(fill="x", pady=(0, 4))
        tk.Label(bbtns, text="扫描: 在游戏里把皮肤/武将详情点开逛一圈(那些大图才会被加载), 再回来点它",
                 bg=self._bg(), fg=self._dim(), font=(self._fh(), 8), wraplength=150,
                 justify="left").pack(fill="x")

        # ⑤ 网络包里的候选背景 URL
        cand_box = tk.LabelFrame(wrap, text=" ⑤ 从诊断/游戏页真实加载里抓到的候选资源 URL(双击或选中后点『用作背景』) ",
                                 bg=self._bg(), fg=self._gold(),
                                 font=(self._fh(), 10, "bold"), bd=1, relief="groove")
        cand_box.pack(fill="x", pady=(0, 6))

        cand_cols = ("tags", "url")
        self.cand_tree = ttk.Treeview(cand_box, columns=cand_cols, show="headings", height=5)
        self.cand_tree.heading("tags", text="标签")
        self.cand_tree.heading("url", text="URL")
        self.cand_tree.column("tags", width=100, anchor="w")
        self.cand_tree.column("url", width=780, anchor="w")
        cvs = tk.Scrollbar(cand_box, orient="vertical", command=self.cand_tree.yview)
        self.cand_tree.configure(yscrollcommand=cvs.set)
        self.cand_tree.pack(side="left", fill="x", expand=True, padx=6, pady=6)
        cvs.pack(side="right", fill="y", pady=6)
        self.cand_tree.bind("<Double-1>", lambda _e: self._use_candidate())
        tk.Button(cand_box, text="刷新候选", command=self._load_candidates,
                  bg=self._btn(), fg=self._blue(), font=(self._fh(), 9)).pack(side="right", padx=6, pady=(0, 6))
        tk.Button(cand_box, text="预览(浏览器打开)", command=self._preview_candidate,
                  bg=self._btn(), fg=self._fg(), font=(self._fh(), 9)).pack(side="right", padx=6, pady=(0, 6))
        tk.Button(cand_box, text="用作背景", command=self._use_candidate,
                  bg=self._act(), fg=self._gold(), font=(self._fh(), 9, "bold")).pack(side="right", padx=6, pady=(0, 6))

        # ⑤ 操作日志 —— 照『赛季更新』页: 这一页做过什么全留在这, 能往回看。
        #   状态栏只有最后一行, 一闪就过去(用户原话"点了不知道成没成"); 日志框把过程留住。
        log_box = tk.LabelFrame(wrap, text=" ⑥ 操作日志(这一页做过什么, 最新的在最下面) ",
                                bg=self._bg(), fg=self._gold(),
                                font=(self._fh(), 10, "bold"), bd=1, relief="groove")
        log_box.pack(fill="x", pady=(0, 6))
        lf = tk.Frame(log_box, bg=self._bg())
        lf.pack(fill="both", expand=True, padx=6, pady=(6, 0))
        self._log_text = tk.Text(lf, height=7, bg="#141414", fg="#cccccc", wrap="word",
                                 font=("Consolas", 9), state="disabled", relief="flat")
        lvs = tk.Scrollbar(lf, orient="vertical", command=self._log_text.yview)
        self._log_text.configure(yscrollcommand=lvs.set)
        lvs.pack(side="right", fill="y")
        self._log_text.pack(fill="both", expand=True)
        tk.Button(log_box, text="清空日志", command=self._clear_log,
                  bg=self._btn(), fg=self._dim(), font=(self._fh(), 8),
                  padx=6).pack(anchor="e", padx=6, pady=(2, 6))

        # ⑦ 状态栏
        self.status_var = tk.StringVar(value="就绪")
        tk.Label(wrap, textvariable=self.status_var, bg=self._bg(), fg=self._dim(),
                 font=(self._fh(), 9), anchor="w").pack(fill="x")

        # v4.74★ 赛季框先填上"现在是哪个赛季" —— 旧版这个框一直是空的(全文件没有一处 set 过它),
        #   用户看着一个空框根本不知道该填什么, 这也是"赛季没有效果"的一半来源。
        try:
            self.season_var.set(RR.season_info(None).get("season") or "")
        except Exception as e:
            # 填不上也不能让整个页签挂掉, 但必须留痕(静态自检 S4: 不许静默 pass)
            self.log("初始赛季填不上: %s" % e, level="WARN")
        # 延迟加载(不卡启动)
        self.root.after(200, self._load_rules)
        self.root.after(300, self._load_candidates)
        self.root.after(420, self._load_official_res)   # v4.74★ 官方大图/皮肤资源清单

    # ---------- 数据操作 ----------
    def _season(self):
        # v4.69★ 归一化交给 RR.norm_season("s10"/"10" 都写成 s10) —— 以前这里 lstrip("sS")
        #   会把 s10 变成 "10", 于是同一个赛季生出两个文件名, 用户不知道导入哪个。
        # v4.74★ 下拉项带 ★(标"这个赛季有配置表"), 取名字前先摘掉, 否则会被当成赛季名的一部分。
        s = (self.season_var.get() or "").replace("★", "").strip()
        return RR.norm_season(s) or None

    def _on_season_change(self):
        """赛季改了 → 立刻按新赛季重读规则, 并把"读的哪张表"说清楚。

        用户原话"左上角的赛季也没有效果啊": 以前改完赛季必须再手动点「读取默认规则」,
        而且读到了什么、有没有读到, 界面上没有任何反馈。现在这三件事一次做完。
        """
        self._skins = None            # 立绘清单跟着赛季走, 缓存作废
        self._probe.clear()
        self._sizes.clear()
        try:
            info = RR.season_info(self._season())
        except Exception as e:
            info = {"season": self._season(), "has": False, "path": "", "err": str(e)}
        self._load_rules()
        # 赛季名写回(把 "10" 规范成 "s10" 显示, 免得用户以为自己填错了)
        if info.get("season"):
            self.season_var.set(info["season"])
        try:
            self.season_cb.configure(values=RR.season_choices())
        except Exception as e:
            self.log("刷新赛季下拉列表失败: %s" % e, level="WARN")
        if info.get("has"):
            self._status("赛季 %s: 配置表 %s(立绘/默认规则已按它重读)"
                         % (info.get("season"), _short_path(info.get("path"))))
        else:
            self._status("赛季 %s 找不到配置表(%s) —— 会退到 data/season 下已有的赛季; "
                         "想让这个赛季生效, 把 TavernChessConfig.json 放进该赛季目录"
                         % (info.get("season"), _short_path(info.get("path"))))

    def _load_rules(self):
        """后台读取自定义规则 + 默认规则并合并显示。"""
        def _work():
            try:
                custom = RR.load_rules()
                season = self._season()
                merged = RR.build_rules(season, custom)
                # v4.74★ 停用的自定义规则必须补回表格 —— 否则"停用=看不见=没法再启用"。
                #   用户原话: "停用去哪了? 选全部也没有; 点不上那个怎么点切换启用呢?
                #   我现在看不到那一条"。build_rules 会把停用条目压掉(游戏侧 /rules 本来
                #   就不该拿到它们, 这是对的), 但界面直接拿合并表填表格, 停用行就凭空消失。
                #   这里只补【显示】, 不动 build_rules —— /rules 的输出不受影响。
                dead = [dict(r) for r in (custom or [])
                        if isinstance(r, dict) and not r.get("enabled", True)
                        and str(r.get("pattern") or "").strip()]
                if dead:
                    merged = dead + merged      # 自定义本来就排在默认规则前面
                self.root.after(0, self._fill_tree, merged, custom)
            except Exception as e:
                self.root.after(0, self._status, "读取规则失败: %s" % e)
        threading.Thread(target=_work, daemon=True).start()

    def _apply_filter(self):
        """搜索框/类别变化 → 只重画表格, 不重新读配置表。"""
        self._fill_tree(self._all_rules, self._custom)

    def _fill_tree(self, rules, custom):
        self._all_rules = list(rules or [])
        self._custom = list(custom or [])
        kw = self.search_var.get() if hasattr(self, "search_var") else ""
        kind = self.kind_var.get() if hasattr(self, "kind_var") else "全部"
        kinds = None
        if kind and kind != "全部":
            kinds = {"自定义"} if kind == "自定义" else {kind}
        shown = RR.search_rules(self._all_rules, kw, kinds)
        if kind == "自定义":
            ids = {r.get("id") for r in self._custom}
            shown = [r for r in shown if r.get("id") in ids]

        self.tree.delete(*self.tree.get_children())
        # v4.110★ 行号 → 规则 的对照表(见 _selected_rule): 以前选中一行要靠"某一列的值去
        #   反查这是哪条规则", 列顺序一变或有同名多条就查不到 —— 表现就是【明明选了,
        #   却提示"请先在列表里选一条规则"】, 编辑/预览/删除全都用不了。
        self._row_map = {}
        custom_ids = {r.get("id") for r in self._custom}
        for r in shown:
            is_custom = r.get("id") in custom_ids
            exists = "是" if RR.target_exists(r.get("target")) else "否"
            enabled = "是" if r.get("enabled", True) else "否"
            rkind = r.get("kind", "其他")
            if is_custom:
                rkind = "【自定义】" + rkind
            target = str(r.get("target") or "")
            if RR.is_remote_target(target):
                target = "URL " + target
            else:
                # v4.71★ 本地目标只显示文件名。用户原话"默认规则, 带网址了, 但你写的没有,
                #   看不懂怎么用" —— 默认规则的目标是 我的图/<名字>.png 这种一长串本地路径,
                #   既看不出"要去哪放图", 也看不出"这条到底准备好没有"; 只留文件名 + 『已就绪』
                #   那一列, 配合下面的说明就自解释了(完整路径仍然能在「编辑」里看到)。
                target = "我的图/%s" % os.path.basename(target) if target else ""
            note = RR.kind_note(rkind.replace("【自定义】", ""))
            # v4.72★ 尺寸那一列: 官方原图多大 → 你这张多大(比例不一致直接标 ⚠变形)
            sinfo = self._sizes.get(str(r.get("pattern") or ""))
            if sinfo is None:
                # "你的图"这一半不用点按钮、也不用联网 —— 本地文件读一下文件头就知道,
                #   所以一打开表就有(见 local_image_size 的说明)。要发请求的是官方那一半。
                tgt = str(r.get("target") or "")
                if tgt and not RR.is_remote_target(tgt) and os.path.isfile(tgt):
                    _tsz, _ = RR.local_image_size(tgt)
                    _fok, _fnote = RR.check_target_format(tgt)
                    sinfo = {"official": (0, 0, ""), "mine": _tsz, "ok": None,
                             "fmt_ok": _fok, "fmt_note": _fnote}
            size_txt = RR.size_cell_text(sinfo)
            if r.get("kind") not in RR.OFFICIAL_KINDS:
                note += "(匹配串自己写)"
            bad = self._probe.get(str(r.get("pattern") or ""))
            if bad:
                note += " ✘官方查不到: " + bad
            _skey, _stxt = RR.sync_state(r)
            _iid = r.get("id") or ("d_%d" % id(r))
            # v4.119★ iid 撞号会让 tree.insert 直接抛 TclError("Item xxx already exists"),
            #   而这里是定时刷新的回调 —— 一抛整个刷新就断了, 界面从此不再更新(用户看到的是
            #   "助手卡死")。两条规则 id 相同的情况确实存在(导入/新增时生成碰撞), 所以这里
            #   兜底去重: 撞了就加序号, 保证 iid 唯一。
            if _iid in self._row_map:
                _n = 1
                while ("%s_%d" % (_iid, _n)) in self._row_map:
                    _n += 1
                _iid = "%s_%d" % (_iid, _n)
            self.tree.insert("", "end", iid=_iid,
                             tags=("s_" + _skey,), values=(
                _stxt,
                rkind,
                r.get("name", ""),
                r.get("pattern", ""),
                target,
                exists,
                enabled,
                note,
                size_txt,
            ))
            self._row_map[_iid] = (dict(r), is_custom)
        # 统计口径与 .tsv 一致: 已就绪=目标图准备好了 / 待放图=还缺图 / 停用=规则不生效
        ready = sum(1 for r in self._all_rules if RR.target_exists(r.get("target")))
        off = sum(1 for r in self._all_rules if not r.get("enabled", True))
        tail = ""
        if kw or kinds:
            tail = " (筛选 %d / 共 %d)" % (len(shown), len(self._all_rules))
        # v4.72★ 量过尺寸的, 在状态栏把"有几条会变形"直接说出来 —— 用户真正关心的就是这个数
        if self._sizes:
            n_ok = sum(1 for v in self._sizes.values() if v.get("ok") is True)
            n_bad = sum(1 for v in self._sizes.values() if v.get("ok") is False)
            n_fmt = sum(1 for v in self._sizes.values() if v.get("fmt_ok") is False)
            tail += "; 尺寸已量 %d 条(比例一致 %d, 会变形 %d%s)" % (
                len(self._sizes), n_ok, n_bad, ", 格式不对 %d" % n_fmt if n_fmt else "")
        # v4.110★ 同步口径也进状态栏: "生效了几条 / 改过但还没推几条"。
        #   以前这行只有"已就绪/待放图/停用", 看不出推没推出去 —— 关掉助手重开更是一抹黑。
        _ns = {}
        for r in self._all_rules:
            _k, _t = RR.sync_state(r)
            _ns[_k] = _ns.get(_k, 0) + 1
        tail += "; 已生效 %d / 改过没推 %d / 没推过 %d" % (
            _ns.get("ok", 0), _ns.get("dirty", 0), _ns.get("never", 0))
        self._status("已加载 %d 条规则: 已就绪 %d / 待放图 %d / 停用 %d (含 %d 条自定义)%s"
                     % (len(self._all_rules), ready, len(self._all_rules) - ready, off,
                        len(self._custom), tail))
        if hasattr(self, "state_var"):
            self._refresh_state()

    def _refresh_state(self):
        """v4.110★ 顶部『现在什么情况』那块字 —— 一进来先看见, 不用盯着表自己数。

        写清楚四件事: ① 认的是哪个赛季 ② 一共几条、各状态几条 ③ 最后一次推出去是什么
        时候 ④ 规则文件本身改没改过。以前这四件全靠猜(或者压根没地方看)。
        """
        rules = list(getattr(self, "_all_rules", None) or [])
        ns = {}
        for r in rules:
            k, _t = RR.sync_state(r)
            ns[k] = ns.get(k, 0) + 1
        sy = [int(r.get("synced_at") or 0) for r in rules]
        last = max(sy) if sy else 0
        last_txt = (time.strftime("%m-%d %H:%M:%S", time.localtime(last / 1000.0))
                    if last else "还没推过")
        try:
            fts = time.strftime("%m-%d %H:%M", time.localtime(os.path.getmtime(RR.RULES_PATH)))
        except Exception:
            fts = "?"
        season = ""
        try:
            season = self.season_var.get()
        except Exception:
            season = ""
        self.state_var.set(
            "赛季 %s ｜ 规则共 %d 条 → 已生效 %d、改过没推 %d、没推过 %d、缺图 %d、停用 %d\n"
            "最后一次推出去: %s ｜ 规则文件改动于: %s"
            % (season or "?", len(rules), ns.get("ok", 0), ns.get("dirty", 0),
               ns.get("never", 0), ns.get("miss", 0), ns.get("off", 0), last_txt, fts))

    def _view_injected(self):
        """v4.110★ 【及时换肤的注入规则】—— 问浏览器: 你现在实际生效的是哪一套?

        为什么要有这个: 用户原话"是不是最新不知道 / 改的没改的分不清"。助手这边的表
        只代表"我打算让你生效什么", 浏览器里 __sgsResSwap.state 才是"真的生效了什么" ——
        它有几条、命中过几次(hits)、规则版本 rev、来源 source、页面报错 lastErr。
        """
        try:
            from core import bridge as _BR
            st = getattr(_BR, "STATE", None)
        except Exception as e:
            messagebox.showinfo("浏览器里的换肤规则", "读不到桥接模块(core/bridge.py): %s" % e)
            return
        if st is None:
            messagebox.showinfo("浏览器里的换肤规则", "助手没在跑桥接服务, 没法问浏览器。")
            return
        code = ("(function(){try{var s=window.__sgsResSwap&&window.__sgsResSwap.state;"
                "if(!s)return 'NO_SCRIPT';"
                "return JSON.stringify({on:!!s.enabled,n:(s.rules||[]).length,rev:s.rev||'',"
                "hits:s.hits||0,src:s.source||'',err:s.lastErr||''});}"
                "catch(e){return 'ERR:'+e.message;}})()")
        try:
            cid = st.send_cmd("js", code=code)
        except Exception as e:
            self._status("问浏览器失败: %s" % e)
            return

        def _work():
            res = self._wait_cmd(st, cid, 6.0)
            self.root.after(0, self._view_injected_report, ("" if res is None else str(res)))
        threading.Thread(target=_work, daemon=True).start()

    def _view_injected_report(self, res):
        mine = len(list(getattr(self, "_all_rules", None) or []))
        if not res or res.startswith("NO_SCRIPT"):
            messagebox.showinfo("浏览器里的换肤规则",
                                "游戏页面里没有换肤模块(window.__sgsResSwap 不存在)。\n\n"
                                "先点「换肤脚本安装」把主脚本装上或更新, 再点这个按钮。")
            return
        if res.startswith("ERR:"):
            self._status("页面执行出错: %s" % res)
            return
        try:
            d = json.loads(res)
        except Exception:
            self._status("浏览器回了看不懂的东西: %s" % str(res)[:120])
            return
        n = int(d.get("n") or 0)
        hits = int(d.get("hits") or 0)
        same = (n == mine)
        txt = ("助手这边 %d 条 ｜ 浏览器里生效 %d 条 → %s\n"
               "换肤开关: %s ｜ 已命中 %d 次 ｜ rev=%s ｜ 来源 %s%s"
               % (mine, n, "一样, 是最新的" if same else "【对不上】",
                  "开" if d.get("on") else "关", hits, d.get("rev") or "-",
                  d.get("src") or "-", ("\n页面报错: " + str(d.get("err"))) if d.get("err") else ""))
        if not same:
            txt += ("\n\n对不上通常是这三个原因: ① 改完没点「立刻同步」(脚本最多 15 秒自己"
                    "拉一次); ② 缺图和停用的规则不会被下发, 所以浏览器那边本来就少几条; "
                    "③ 游戏页刚打开还没拉到。")
        if hits:
            txt += "\n\n命中 %d 次 = 换肤真的在起作用(不是装上没生效)。" % hits
        self._status(txt.replace("\n", " "))
        messagebox.showinfo("浏览器里的换肤规则", txt)

    def _load_candidates(self):
        def _work():
            try:
                cands = RR.diagnostics_candidates()
                self.root.after(0, self._fill_candidates, cands)
            except Exception as e:
                self.root.after(0, self._status, "读取候选 URL 失败: %s" % e)
        threading.Thread(target=_work, daemon=True).start()

    def _fill_candidates(self, cands):
        self._cand_map = {}
        self.cand_tree.delete(*self.cand_tree.get_children())
        for i, c in enumerate(cands[:200]):
            tags = ",".join(c.get("tags") or [])
            iid = "c%d" % i
            self._cand_map[iid] = c
            self.cand_tree.insert("", "end", iid=iid, values=(tags, c.get("url", "")))
        self._status("候选背景/皮肤 URL: %d 条(双击一行 = 选一张本地图把它换掉)" % len(cands))

    def _selected_rule(self):
        """选中的这一行 → (rule dict, 是不是自定义)。

        v4.69★ 以前这里只返回自定义规则, 选中默认规则一律返回 None —— 于是「编辑」按钮
        对默认规则只会弹一句"默认规则不能直接改", 用户想做的就是"拿这条换个图"却无从下手。
        现在默认规则也返回(带 default 标记), 由调用方决定: 编辑/删除走「转成自定义」。
        """
        sel = self.tree.selection()
        if not sel:
            return None, False
        iid = sel[0]
        # v4.110★ 先查建表时记下的对照表: 这一行是哪条规则本来就知道, 不用再拿某列的值去猜。
        #   (下面那两截保留, 只是兼容老行为的兜底)
        _hit = (getattr(self, "_row_map", None) or {}).get(iid)
        if _hit:
            return dict(_hit[0]), bool(_hit[1])
        for r in RR.load_rules():
            if r.get("id") == iid:
                return r, True
        # 默认规则: 树里的 iid 是 "d_<内存编号>", 得按当前显示行反查
        item = self.tree.item(iid)
        vals = item.get("values") or []
        if len(vals) >= 4:
            # v4.110★ 第 4 列才是匹配串(状态/类型/名称/匹配串…) —— 以前取的是第 3 列【名称】,
            #   拿名称去比匹配串, 自然一条都命中不了。
            pat = str(vals[3])
            for r in self._all_rules:
                if str(r.get("pattern") or "") == pat and r.get("id") not in {x.get("id") for x in self._custom}:
                    return dict(r), False
        return None, False

    def _selected_candidate(self):
        """选中的候选 → 候选 dict(含 url/pattern/kind); 没有映射时用行里的 URL 现拼一个。"""
        sel = self.cand_tree.selection()
        if not sel:
            return None
        c = (self._cand_map or {}).get(sel[0])
        if c:
            return c
        vals = self.cand_tree.item(sel[0]).get("values") or []
        u = str(vals[1]) if len(vals) > 1 else ""
        if not u:
            return None
        _line, rel = RR.res_ref(u)
        return {"url": u, "pattern": rel or u, "kind": RR.res_kind(rel)[0], "tags": []}

    # ---------- 资源服务(助手主端口) ----------
    def _main_base(self):
        """助手主端口的地址 —— /rules 与 /img/xxx.png 就挂在它上面。

        v4.70★ 取不到(旧版 app 没有 server 属性 / 端口未知)返回空串, 调用方自己兜底。
        这里**不写死端口**: 用户可以把 bridge.port 改掉, 写死就会指向一个没人监听的端口,
        而且不报错 —— 正是 dev/_verify_static.py 的 S5 那条检查要防的事。
        """
        srv = getattr(self.app, "server", None)
        p = getattr(srv, "port", None) if srv is not None else None
        try:
            p = int(p)
        except Exception:
            return ""
        return "http://127.0.0.1:%d" % p if p else ""

    def _open_res_page(self):
        """打开资源服务页(/res) —— 当前地址、规则条数、脚本安装入口都在那一页上。"""
        base = self._main_base()
        if not base:
            messagebox.showinfo("资源服务", "拿不到助手端口(助手没在跑?)。")
            return
        url = base + "/res"
        try:
            import webbrowser
            webbrowser.open(url)
            self._status("资源服务页: %s" % url)
        except Exception as e:
            self._status("打不开浏览器: %s —— 手动访问 %s" % (e, url))

    def _open_gallery(self):
        """打开资源图库(/gallery) —— 可操作的管理页: 点卡片选图/编辑/启停/导入导出。

        为什么值得独立一页: preview_html 那张是【只读的】(存成文件给人看), 而用户实际
        要的是"看着这页直接换图、保存、游戏里立刻变" —— 交互页必须由本机服务现挂,
        数据现拉 /resapi/state, 保存走 /resapi(实现在 core/res_server.py)。
        """
        base = self._main_base()
        if not base:
            messagebox.showinfo("资源图库", "拿不到助手端口(助手没在跑?)。")
            return
        url = base + "/gallery"
        try:
            import webbrowser
            webbrowser.open(url)
            self._status("资源图库: %s" % url)
        except Exception as e:
            self._status("打不开浏览器: %s —— 手动访问 %s" % (e, url))

    def _open_ext_zip(self):
        """下载【全能版】MV3 浏览器扩展的 zip(/ext.zip) —— 装不装在浏览器那边完成。

        这里只负责把包丢给浏览器; 装法(开发者模式 → 加载已解压的扩展程序)写在
        bridge/sgs_ext/README.md, 资源服务页和这个提示框里都会再说一遍。
        """
        base = self._main_base()
        if not base:
            messagebox.showinfo(
                "全能浏览器扩展",
                "拿不到助手端口(助手没在跑?)。\n扩展是打包成 zip 下载的, 需要助手端口。")
            return
        url = base + "/ext.zip"
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception as e:
            messagebox.showerror("全能浏览器扩展", "打不开浏览器: %s\n手动访问 %s" % (e, url))
            return
        messagebox.showinfo(
            "全能浏览器扩展(可选, 推荐)",
            "zip 已开始下载(sgs_ext.zip)。\n\n"
            "这一个扩展里装了三样: 油猴脚本本体(不用再装 Tampermonkey)\n"
            "+ GoRes 同款网络层重定向 + 管理页(看规则/手写规则/挑官方立绘)。\n\n"
            "装法: 解压到一个固定目录 → 扩展管理页\n"
            "(Chrome: chrome://extensions / Edge: edge://extensions)\n"
            "→ 开「开发者模式」→ 加载已解压的扩展程序\n→ 选中解压出的文件夹。\n\n"
            "装好后点扩展图标 → 「管理页」→ 确认端口 → 「立即同步」。\n"
            "(油猴脚本如果也装着, 它会自动退到一边让扩展接管, 不会重复改写。)")

    def _update_ext_script(self):
        """v4.81★ 把【当前】bridge/sgs_bridge.user.js 发布给全能扩展。

        全能扩展 v1.3 起【不内置】主脚本: 它常问 /ext/bridge.meta 比 rev, 变了才拉
        /ext/bridge.main.js。所以"本项目里改完脚本 → 扩展换新的"只需要这里推一下
        发布时间戳(内容没变也照样生效 = 用户点了就得去更新), 后面全是自动的:
            扩展 ≤20 秒取走 → 存进自己的 storage → 用 chrome.userScripts 重新注册。
        不用重跑 dev/_pack_ext.py, 也不用去扩展管理页(chrome://extensions /
        edge://extensions) 点"重新加载扩展"。
        """
        try:
            from core import ext_script as ES
        except Exception as e:
            messagebox.showerror("更新全能扩展脚本", "缺少 core/ext_script.py: %s" % e)
            return
        try:
            m = ES.publish()
        except Exception as e:
            messagebox.showerror("更新全能扩展脚本",
                                 "发布失败(源脚本里 @version 与 SCRIPT_VER 不一致?):\n%s" % e)
            return
        if not m.get("ok"):
            messagebox.showerror("更新全能扩展脚本", m.get("error", "读不到主脚本"))
            return

        ver = m.get("ver") or "?"
        kb = int(m.get("size", 0) or 0) // 1024
        base = self._main_base()
        self._status("主脚本 v%s (%d KB) 已发布 —— 全能扩展 ≤20 秒自动取走; 已打开的游戏页刷新一次才是新的"
                     % (ver, kb))
        messagebox.showinfo(
            "更新全能扩展脚本",
            "已发布: %s v%s（%d KB）\n"
            "下发地址: %s/ext/bridge.main.js\n\n"
            "接下来不用你动手: 扩展自己会取(打开游戏页 / 每分钟 / 页面每 20 秒问一次)。\n"
            "取到后【刷新一次游戏页】才是新的 —— 浏览器不会给已经加载完的页面重新注入。\n\n"
            "看不到变化? 扩展图标 → 管理页 → 「脚本」那一行写着现在跑的是哪一版、\n"
            "什么时候取的、注册上了没有; 也可以在那儿点「立即检查更新」。"
            % (m.get("source", "sgs_bridge.user.js"), ver, kb, base or "(助手端口)"))

    def _open_health(self):
        """打开资源服务页并【自动跑一次体检】(页面认 #health)。

        用助手主端口(不是独立服务) —— 助手在跑它就在, 不会因为"忘了点启动"而打不开。
        """
        base = self._main_base()
        if not base:
            messagebox.showinfo("缺图体检", "拿不到助手主端口 —— 先让助手跑起来再点。")
            return
        try:
            import webbrowser
            webbrowser.open(base + "/res#health")
            self._status("体检页已打开(助手主端口 %s) —— 页面上会列出『缺图 / 同一匹配串重复 / "
                         "目标没填』这三类【配了但不会生效】的规则。" % base)
        except Exception as e:
            self._status("打不开浏览器: %s —— 手动访问 %s/res#health" % (e, base))

    def _open_status(self):
        """打开服务状态页 —— 看脚本有没有来拉 /rules、有没有 /img 404(排障第一现场)。"""
        base = self._main_base()
        if not base:
            messagebox.showinfo("服务状态", "拿不到助手主端口 —— 先让助手跑起来再点。")
            return
        try:
            import webbrowser
            webbrowser.open(base + "/res")
            self._status("状态页已打开(助手主端口 %s) —— 页面上「服务状态」看 rev 和最后一次拉取时间, "
                         "「请求日志」看脚本有没有来拉 /rules、哪些图 404。" % base)
        except Exception as e:
            self._status("打不开浏览器: %s —— 手动访问 %s/res" % (e, base))

    def _start_server(self):
        """(可选)起【独立】图片服务 —— 只有"不想开助手、只想单跑图片服务"时才需要。

        v4.70★ 为什么改成"可选": 助手主端口已经带 /rules 和 /img 了(见 core/bridge.py),
        规则目标只要指向主端口就一定活着。上一版必须手点这个按钮才会起 8767, 忘了点 →
        目标指向空端口 → "图配好了却没生效"。所以主链路不再依赖它。
        """
        if RS is None:
            messagebox.showinfo("图片服务", "缺少 core/res_server.py, 无法启动独立图片服务。")
            return

        def _work():
            try:
                # v4.160★ 多开: 候选端口按实例序号平移, 免得复用别的实例的服务
                from core.config import slot_ports
                port, notes = RS.ensure(ports=slot_ports(RS.PORT))
            except Exception as e:
                self.root.after(0, self._status, "图片服务启动失败: %s" % e)
                return
            if not port:
                self.root.after(0, self._status, "图片服务起不来: " + "; ".join(notes or []))
                return
            self.root.after(0, self._srv_started, port, notes)
        threading.Thread(target=_work, daemon=True).start()

    def _srv_started(self, port, notes):
        self.srv_var.set("独立图片服务: 端口 %d(可选)" % port)
        self._status("独立图片服务已启动(端口 %d) —— 一般来说用助手主端口那个就够了; %s"
                     % (port, "; ".join(notes or [])))
        # v4.71★ 起完【自动把它的页面打开】。用户原话: "独立服务有啥用, 没看懂, 也看不到页面啊" ——
        #   上一版确实只改了旁边那行小字, 页面要用户自己去地址栏敲 127.0.0.1:8767,
        #   等于"启动了但什么也没发生"。既然点了按钮, 就把那一页直接给他看:
        #   那一页上写着这个服务的用途(图片/规则文件的落点)、当前规则条数、脚本安装入口。
        try:
            url = RS.base_url(port) + "/res"
        except Exception:
            url = "http://127.0.0.1:%d/res" % port
        try:
            import webbrowser
            webbrowser.open(url)
            self._status("独立图片服务的页面已打开: %s —— 它和助手主端口是【同一套】图片/规则, "
                         "只是【不想开助手】时还能单独跑; 页面里能看到当前规则条数和安装入口。" % url)
        except Exception as e:
            self._status("独立图片服务已启动(端口 %d), 但打不开浏览器: %s —— 手动访问 %s"
                         % (port, e, url))

    def _open_swap_script(self):
        """打开【换肤脚本】的安装/说明入口(Tampermonkey 会自动弹安装页)。

        v4.70★ 变化: 换肤逻辑【已经写进 bridge/sgs_bridge.user.js】了 —— 用户原话
          "那个换肤有js了吗? 或者写进那里面sgs里面?" + "我不想再加一个扩展插件: gores了"。
          所以这里优先给"主脚本的安装/更新页"(你本来就有它, 更新一下就有换肤);
          sgs_skin_swap.user.js 是独立版(不开助手也能用), 也留了个入口。
        """
        base = self._main_base()
        if not base:
            messagebox.showinfo("换肤脚本", "拿不到助手端口(助手没在跑?)。\n"
                                          "也可以手动把 bridge/sgs_bridge.user.js 拖进浏览器安装。")
            return
        url = base + "/sgs_bridge.user.js"
        try:
            import webbrowser
            webbrowser.open(url)
            self._status("换肤已内置在主脚本里: %s —— Tampermonkey 会弹安装/更新, 更新一下就有"
                         "(独立版见 %s/sgs_skin_swap.user.js)" % (url, base))
        except Exception as e:
            self._status("打不开浏览器: %s —— 手动访问 %s" % (e, url))

    def _base_url(self):
        """拼规则目标用的地址: 优先助手主端口(一定活着), 其次独立图片服务, 都没有就留空。

        留空时 generate_redirect_files 会退回"本地文件路径"形态 —— 那是给命令行/离线用的,
        游戏页(https)跳 file:// 多半被浏览器拒, 所以界面上会提示"没拿到服务地址"。
        """
        base = self._main_base()
        if base:
            return base
        if RS is None:
            return ""
        p = getattr(RS, "_active_port", None)
        if not p:
            return ""
        try:
            if not RS.probe(p, timeout=0.3):
                return ""
        except Exception:
            return ""
        return RS.base_url(p)

    # ---------- 对话框 ----------
    def _rule_dialog(self, rule=None):
        """添加/编辑自定义规则的对话框。"""
        title = "编辑自定义规则" if rule else "添加自定义规则"
        top = tk.Toplevel(self.root)
        top.title(title)
        top.configure(bg=self._bg())
        top.transient(self.root)
        top.grab_set()

        is_edit = bool(rule)
        r = dict(rule) if rule else {
            "id": "r%d" % int(__import__("time").time() * 1000),
            "name": "",
            "kind": "背景",
            "pattern": "",
            "target": "",
            "enabled": True,
            "season": self._season() or "",
        }

        # v4.74★ 弹窗结构改法(用户原话: "确实没有显示完整啊; 并且没有保存按钮"):
        #   旧版 `top.geometry("820x430")` 把高度写死, 而这 9 行 grid 内容实际约 470px,
        #   于是最后一行(保存/取消)落到可视区之外 —— 用户根本看不到保存按钮。
        #   现在分两截: 按钮行 pack(side="bottom") 钉在窗口底部(内容再长也在),
        #   内容放进 Canvas+Scrollbar(超高可滚), 窗口高度按内容自适应且不超过屏幕 85%。
        bar_host = tk.Frame(top, bg=self._bg())
        bar_host.pack(side="bottom", fill="x")
        cv = tk.Canvas(top, bg=self._bg(), highlightthickness=0, bd=0)
        vsb = tk.Scrollbar(top, orient="vertical", command=cv.yview)
        cv.configure(yscrollcommand=vsb.set)
        cv.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        host = tk.Frame(cv, bg=self._bg())
        _win = cv.create_window((0, 0), window=host, anchor="nw")
        host.bind("<Configure>", lambda _e: cv.configure(scrollregion=cv.bbox("all")))
        cv.bind("<Configure>", lambda _e: cv.itemconfigure(_win, width=cv.winfo_width()))

        def _dlg_wheel(e):
            """弹窗自己的滚轮 —— 不往主窗口的页签冒泡(否则滚这个框会带着后面的表一起动)。"""
            try:
                cv.yview_scroll(int(-1 * (e.delta / 120)), "units")
            except Exception as _we:
                self.log("弹窗滚轮滚动失败: %s" % _we, level="WARN")
            return "break"

        for _w in (cv, host):
            _w.bind("<MouseWheel>", _dlg_wheel)

        pad = {"padx": 8, "pady": 6}
        tk.Label(host, text="名称:", bg=self._bg(), fg=self._fg(),
                 font=(self._fh(), 9)).grid(row=0, column=0, sticky="e", **pad)
        name_var = tk.StringVar(value=r.get("name", ""))
        tk.Entry(host, textvariable=name_var, width=50, bg=self._btn(), fg=self._fg(),
                 insertbackground=self._fg(), font=(self._fh(), 9)).grid(row=0, column=1, sticky="we", **pad)

        tk.Label(host, text="类型:", bg=self._bg(), fg=self._fg(),
                 font=(self._fh(), 9)).grid(row=1, column=0, sticky="e", **pad)
        kind_var = tk.StringVar(value=r.get("kind", "背景"))
        kind_cb = ttk.Combobox(host, textvariable=kind_var,
                               values=["背景", "棋子立绘", "主公立绘", "OL大图", "音效", "其他"],
                               state="readonly", width=18)
        kind_cb.grid(row=1, column=1, sticky="w", **pad)

        tk.Label(host, text="匹配串:", bg=self._bg(), fg=self._fg(),
                 font=(self._fh(), 9)).grid(row=2, column=0, sticky="ne", **pad)
        pattern_var = tk.StringVar(value=r.get("pattern", ""))
        pat_cell = tk.Frame(host, bg=self._bg())
        pat_cell.grid(row=2, column=1, sticky="we", **pad)
        pt = tk.Text(pat_cell, width=60, height=3, bg=self._btn(), fg=self._fg(),
                     insertbackground=self._fg(), font=(self._fh(), 9), wrap="word")
        pt.pack(fill="x")
        pt.insert("1.0", pattern_var.get())

        # v4.70★ 匹配串最容易写错的两件事(用户现场: "我用这俩换咋都不行?"):
        #   ① 少了本赛季的后缀 —— 夏侯惇/陆逊从第 2 赛季起 DefaultSkin 变成了
        #      xiahoudun_02 / luxun_02, 写成 xiahoudun.png 一条都命中不了(按"包含"匹配,
        #      `…/xiahoudun.png` 不是 `…/xiahoudun_02.png` 的子串);
        #   ② 三大家族搞混 —— 棋子立绘 tavernChess/piece/、武将牌面 tavernChess/card/、
        #      主公立绘 tavernChess/general/(v4.75 修正, 以前的 general/maxBig/ 已 404)。
        #   与其让用户对着日志猜, 不如: 给一个「按武将名填匹配串」(填的是【本季真正会请求
        #   的那条】), 再给一个「校验匹配串」(当场说命中不命中, 能猜出正确写法时直接给建议)。
        pbtns = tk.Frame(pat_cell, bg=self._bg())
        pbtns.pack(fill="x", pady=(3, 0))
        check_lbl = tk.Label(pat_cell, text="", bg=self._bg(), fg=self._dim(),
                             font=(self._fh(), 8), wraplength=620, justify="left", anchor="w")
        check_lbl.pack(fill="x")

        def _fill_pattern():
            """按武将名填匹配串: 只列【本赛季在用的那套】, 填的是不含域名的相对形态。"""

            def _chosen(opt):
                if not opt:
                    return
                pat = RR.relative_pattern(opt.get("pattern") or "")
                if pat:
                    pt.delete("1.0", "end")
                    pt.insert("1.0", pat)
                    if not name_var.get().strip():
                        name_var.set("%s（%s）" % (opt.get("name") or "", opt.get("skin") or ""))
                check_lbl.configure(
                    text="已填入本赛季真正会请求的那一条: %s" % pat, fg=self._blue())

            self._official_picker(top, _chosen, for_pattern=True)

        def _check_pattern():
            # 三态: True=能命中 / False=确实命中不了 / None=不在配置表里(背景/音效)判不了。
            #   判不了就不吓人 —— 显示成黄色的"无法判定", 别把用户一条好好的背景规则吓成红的。
            res = RR.check_pattern(pt.get("1.0", "end-1c").strip(), self._season())
            if res["ok"] is True:
                check_lbl.configure(text="✔ " + res["hint"], fg=self._green())
            elif res["ok"] is None:
                check_lbl.configure(text="？ " + res["hint"], fg=self._gold())
            else:
                check_lbl.configure(text="✘ " + res["hint"], fg=self._red())

        tk.Button(pbtns, text="按武将名填匹配串 …", command=_fill_pattern,
                  bg=self._act(), fg=self._gold(), font=(self._fh(), 8), padx=6,
                  cursor="hand2").pack(side="left", padx=(0, 6))
        tk.Button(pbtns, text="校验匹配串", command=_check_pattern,
                  bg=self._btn(), fg=self._blue(), font=(self._fh(), 8), padx=6,
                  cursor="hand2").pack(side="left")

        # v4.73★ 「贴官方网址」: OL 大立绘(static/dynamic)那两条家族的编号在主游戏皮肤表里,
        #   自走棋配置表里没有 → 「按武将名」枚举不了。用户在 DevTools 网络面板抓到什么 URL,
        #   粘进来归一化成匹配串(剥协议/域名/?v=), 抓到什么就能换什么。
        def _paste_official():
            try:
                raw = top.clipboard_get()
            except Exception:
                raw = ""
            res = RR.official_pattern_from_url(raw)
            if not res.get("pattern"):
                check_lbl.configure(
                    text="✘ 剪贴板里没认出官方资源网址 —— 要 web.sanguosha.com/…/pc/res/assets/… "
                         "的完整 URL(带 ?v=… 缓存参数也没关系, 会自动剥掉)", fg=self._red())
                return
            pat = res["pattern"]
            pt.delete("1.0", "end")
            pt.insert("1.0", pat)
            if res.get("kind"):
                kind_var.set(res["kind"])
                if not name_var.get().strip():
                    name_var.set(res["kind"])
            hint = "已从剪贴板填入: %s（%s）" % (pat, res.get("kind") or "未知家族")
            if "big/dynamic/" in pat:
                hint += ("。动态帧规律: dynamic/ 编号 = 静态编号 + 200000"
                         "(例: static/621401 → dynamic/821401); 每一帧(daiji.png / beijing.png …)"
                         "是单独一个文件, 换整段动画要一帧建一条规则。")
            check_lbl.configure(text=hint, fg=self._blue())

        tk.Button(pbtns, text="贴官方网址(OL大图/动态帧)", command=_paste_official,
                  bg=self._btn(), fg=self._gold(), font=(self._fh(), 8), padx=6,
                  cursor="hand2").pack(side="left", padx=(6, 0))

        # v4.110★ 批量建规则 —— 【匹配串侧 / 目标侧共用】: 选中的几帧 → 一帧一条规则,
        #   目标都用同一张图 tgt。动态皮肤本来就是"一帧一个 png", 不存在"一条规则换掉
        #   整段动画"这回事。两边唯一的区别是"往哪儿回话"(匹配串侧回 check_lbl,
        #   目标侧回 tgt_lbl), 所以用 tell 回调传进来, 免得同一段逻辑抄两份。
        def _dress_many_rules(items, tgt, tell):
            if not items or not tgt:
                return 0
            now = int(time.time() * 1000)
            new = []
            for i, it in enumerate(items):
                pat = str(it.get("pattern") or "").strip()
                if not pat:
                    continue
                new.append({"id": "drs%d_%d" % (now, i),
                            "name": "%s(%s)" % (str(it.get("name") or "OL大图").strip(),
                                                "动态帧" if it.get("dynamic") else "静态"),
                            "kind": "OL大图",
                            "pattern": RR.relative_pattern(pat),
                            "target": tgt, "enabled": True,
                            "season": self._season() or ""})
            if not new:
                return 0
            total, added, covered = RR.upsert_rules(new)
            self._load_rules()
            tell("已按选中的 %d 帧各建一条规则 → 「%s」—— 动态皮肤一帧一个文件, "
                 "换整段动画就是一帧一条; 现在共 %d 条规则, 记得点「立即同步到浏览器」"
                 % (len(new), os.path.basename(tgt), total), self._green())
            return len(new)

        # v4.75★ 皮肤表来了: OL 大图也能按武将名挑, 不用再自己去 DevTools 抓网址。
        def _pick_dress():
            def _chosen(opt):
                pat = opt.get("pattern") or ""
                if not pat:
                    return
                pt.delete("1.0", "end")
                pt.insert("1.0", pat)
                kind_var.set("OL大图")
                if not name_var.get().strip():
                    name_var.set(opt.get("name") or "")
                check_lbl.configure(
                    text="已探到并填入: %s(%s) —— 记得在下面配「你的图」或直接用官方图当目标"
                         % (pat, "动态帧" if opt.get("dynamic") else "静态大图"),
                    fg=self._blue())

            def _pick_dress_many(items):
                """选了好几帧(静态大图 + 动态帧) → 弹窗选一张图 → 【一帧一条规则】。"""
                # v4.110★ 【编辑时不许另起炉灶】: 以前不管是不是编辑, 这里都直接建 N 条新规则,
                #   于是用户在编辑窗里挑完图 → 正在编辑那条一点没动, 旁边凭空多出一条
                #   【匹配串=刚挑的图 / 目标=原来那张】的反向规则(用户原话:
                #   "我编辑自走棋背景出来一个把我现在的图换到我要换的图里面了")。
                #   编辑态只把选中的第一张填进【匹配串】框, 改不改由「保存」说了算。
                if is_edit:
                    it = (items or [None])[0]
                    if it:
                        _chosen(it)
                        check_lbl.configure(
                            text="编辑这条规则: 只把选中那几张的第一张填进了【匹配串】, 没有新建规则 —— "
                                 "想一次给多帧各建一条, 关掉这个窗口去用「OL大图/动态皮肤一键配」。",
                            fg=self._gold())
                    return
                tgt = filedialog.askopenfilename(
                    title="选择要换成的大图(选中的 %d 帧都用它)" % len(items),
                    filetypes=[("图片", "*.png *.jpg *.jpeg *.webp *.gif"), ("所有文件", "*.*")],
                    initialdir=RR.IMG_DIR if os.path.exists(RR.IMG_DIR) else os.path.expanduser("~")) or ""
                if not tgt:
                    return
                _dress_many_rules(items, tgt, lambda m, c: check_lbl.configure(text=m, fg=c))

            # 传 on_multi 才两件事: ① 列表变 multiple(点一下选中, 再点取消; 不传就是 browse
            #   单选, 点第二行会把第一行顶掉 —— 这就是"一个个点也选不上"的真凶);
            #   ② 多出「全选」「把选中的帧都配上」两个按钮。
            self._dress_picker(top, _chosen, on_multi=_pick_dress_many)

        tk.Button(pbtns, text="从皮肤表挑(OL大图) …", command=_pick_dress,
                  bg=self._btn(), fg=self._gold(), font=(self._fh(), 8), padx=6,
                  cursor="hand2").pack(side="left", padx=(6, 0))

        # v4.70★ 目标类型三种: 本地图片 / 网络地址 / 官方武将立绘(可直接挑选切换)
        tk.Label(host, text="目标类型:", bg=self._bg(), fg=self._fg(),
                 font=(self._fh(), 9)).grid(row=3, column=0, sticky="e", **pad)
        tgt_cur = str(r.get("target") or "")
        # 初值判定: 官方域名 → official; 其它 http(s) → url; 其余 → local
        if RR.is_remote_target(tgt_cur) and "sanguosha.com" in tgt_cur.lower():
            _mode0 = "official"
        elif RR.is_remote_target(tgt_cur):
            _mode0 = "url"
        else:
            _mode0 = "local"
        mode_var = tk.StringVar(value=_mode0)
        mrow = tk.Frame(host, bg=self._bg())
        mrow.grid(row=3, column=1, sticky="w", **pad)
        for _txt, _val in (("本地图片(我的图 或任意本地文件)", "local"),
                           ("网络地址(任意在线图片)", "url"),
                           ("官方武将立绘(服务器上那张, 可切换)", "official")):
            tk.Radiobutton(mrow, text=_txt, value=_val, variable=mode_var,
                           bg=self._bg(), fg=self._fg(), selectcolor="#33333f",
                           activebackground=self._bg(),
                           font=(self._fh(), 9)).pack(side="left", padx=(0, 10))

        tk.Label(host, text="目标:", bg=self._bg(), fg=self._fg(),
                 font=(self._fh(), 9)).grid(row=4, column=0, sticky="e", **pad)
        target_var = tk.StringVar(value=tgt_cur)
        te = tk.Entry(host, textvariable=target_var, width=50, bg=self._btn(), fg=self._fg(),
                      insertbackground=self._fg(), font=(self._fh(), 9))
        te.grid(row=4, column=1, sticky="we", **pad)

        def _browse():
            # v4.86★ 匹配串看着像音效(voice/*.mp3 之类)就让选音频, 否则按老样子选图
            cur = ""
            try:
                cur = str(pt.get("1.0", "end") or "").strip()
            except Exception:
                cur = str(pattern_var.get() or "")
            is_audio = RR.is_audio_pattern(cur)
            p = filedialog.askopenfilename(
                title=("选择要替换成的音频文件" if is_audio else "选择要替换成的本地图片"),
                filetypes=[(("音频", "*.mp3 *.ogg *.oga *.m4a *.wav") if is_audio
                            else ("图片", "*.png *.jpg *.jpeg *.webp *.gif")),
                           ("所有文件", "*.*")],
                initialdir=RR.IMG_DIR if os.path.exists(RR.IMG_DIR) else os.path.expanduser("~"))
            if p:
                mode_var.set("local")
                target_var.set(p)

        tk.Button(host, text="浏览 …", command=_browse,
                  bg=self._btn(), fg=self._fg(), font=(self._fh(), 9)).grid(row=4, column=2, **pad)

        def _pick_official():
            """挑选官方立绘: 选中就把它的 URL 填进「目标」(一张图都不用下载)。"""

            def _chosen(opt):
                if not opt:
                    return
                mode_var.set("official")
                target_var.set(opt.get("url") or "")
                if not name_var.get().strip():
                    name_var.set("%s → %s" % (opt.get("name") or "", opt.get("skin") or ""))
                tgt_lbl.configure(
                    text="✔ 已填入官方立绘目标: %s —— 上面「匹配串」一侧挑你要换掉的那张即可"
                         % (opt.get("url") or ""), fg=self._blue())

            self._official_picker(top, _chosen)

        # v4.75★ 目标侧补两个官方入口(用户原话: "给目标也加上可以贴官方ol、从皮肤表"):
        #   之前「贴官方网址/从皮肤表挑」只在匹配串一侧, 想换"官方图A → 官方图B"
        #   (如 威马超经典形象 1220501 → 戾战灼征 5220501)时目标还得手抄完整 URL。
        def _paste_official_target():
            """贴官方网址到「目标」: 官方资源 → 剥 ?v= 拼成干净完整 URL; 其它 http(s) 照填。"""
            try:
                raw = top.clipboard_get()
            except Exception:
                raw = ""
            raw = str(raw or "").strip()
            res = RR.official_pattern_from_url(raw)
            if res.get("pattern"):
                # 匹配串用的 relative 形态不能直接当目标 —— 目标要完整 URL
                full = RR.res_full_url(res["pattern"])
                mode_var.set("official")
                target_var.set(full)
                tgt_lbl.configure(text="✔ 已从剪贴板填入目标: %s(%s)"
                                         % (full, res.get("kind") or "官方资源"), fg=self._blue())
                return
            if RR.is_remote_target(raw):
                mode_var.set("url")
                target_var.set(raw)
                tgt_lbl.configure(text="已把剪贴板里的网址填为「网络地址」目标: %s" % raw,
                                  fg=self._blue())
                return
            tgt_lbl.configure(
                text="✘ 剪贴板里没认出网址 —— 要 web.sanguosha.com/…/pc/res/assets/… 的完整 URL"
                     "(带 ?v=… 没关系, 会剥掉), 或任意 http(s) 图片地址", fg=self._red())

        def _pick_dress_target():
            """从皮肤表挑官方图当「目标」: 探测到的静态大图/动态帧 → 完整 URL。"""

            def _chosen(opt):
                pat = opt.get("pattern") or ""
                if not pat:
                    return
                full = RR.res_full_url(pat)
                mode_var.set("official")
                target_var.set(full)
                if not name_var.get().strip():
                    name_var.set(opt.get("name") or "")
                tgt_lbl.configure(
                    text="✔ 已把官方图填为目标: %s(%s) —— 匹配串一侧挑你要换掉的那张即可"
                         % (full, "动态帧" if opt.get("dynamic") else "静态大图"), fg=self._blue())

            def _pick_dress_target_many(items):
                """v4.110★ 目标侧的批量 —— 用户原话"我说的是目标侧"。

                目标就用【当前框里已经填好的那张】(本地图 / 网络地址 / 官方图都行),
                没填才弹窗让选一张; 然后给选中的每一帧各建一条规则。
                和匹配串侧那个按钮的区别只在"图从哪儿来": 那边是选完帧再弹窗选图,
                这边是先有图再挑帧 ——                 结果完全一样(一帧一条规则)。

                v4.110★ 【编辑时不许另起炉灶】: 这条按钮挂在「目标」那一栏, 而 _dress_many_rules
                  是把【选中的帧当匹配串、框里的图当目标】去建新规则 —— 在编辑窗里点它就等于
                  "把刚挑的图当成要被换掉的那张", 出来的规则方向正好是反的(用户原话:
                  "把我现在的图换到我要换的图里面了; 这样不行")。
                  编辑态改成: 只把选中的第一张填进【目标】框, 一条规则也不新建。
                """
                if is_edit:
                    it = (items or [None])[0]
                    if it:
                        _chosen(it)
                        tgt_lbl.configure(
                            text="编辑这条规则: 只把选中那几张的第一张填进了【目标】, 没有新建规则 "
                                 "(目标只能有一张) —— 想给多帧各建一条, 关掉这个窗口用批量入口。",
                            fg=self._gold())
                    return
                tgt = str(target_var.get() or "").strip()
                if not tgt:
                    tgt = filedialog.askopenfilename(
                        title="选择要换成的大图(选中的 %d 帧都用它)" % len(items),
                        filetypes=[("图片", "*.png *.jpg *.jpeg *.webp *.gif"), ("所有文件", "*.*")],
                        initialdir=RR.IMG_DIR if os.path.exists(RR.IMG_DIR) else os.path.expanduser("~")) or ""
                    if not tgt:
                        return
                    mode_var.set("local")
                    target_var.set(tgt)
                _dress_many_rules(items, tgt, lambda m, c: tgt_lbl.configure(text=m, fg=c))

            self._dress_picker(top, _chosen, on_multi=_pick_dress_target_many,
                               multi_label="给选中的帧各建一条(帧 = 被换掉的那张) …")

        # 三个目标辅助按钮并成一行(原来只有「挑选官方立绘」孤零零一个)
        tgt_btns = tk.Frame(host, bg=self._bg())
        tgt_btns.grid(row=5, column=1, sticky="w", **pad)
        tk.Button(tgt_btns, text="挑选官方立绘 …", command=_pick_official,
                  bg=self._act(), fg=self._gold(), font=(self._fh(), 9, "bold"),
                  cursor="hand2").pack(side="left")
        tk.Button(tgt_btns, text="贴官方网址 …", command=_paste_official_target,
                  bg=self._btn(), fg=self._gold(), font=(self._fh(), 8), padx=6,
                  cursor="hand2").pack(side="left", padx=(6, 0))
        tk.Button(tgt_btns, text="从皮肤表挑(OL大图) …", command=_pick_dress_target,
                  bg=self._btn(), fg=self._gold(), font=(self._fh(), 8), padx=6,
                  cursor="hand2").pack(side="left", padx=(6, 0))

        # 目标侧的操作回话(和匹配串侧的 check_lbl 对称, 粘贴/挑选成没成功当场说清楚)
        tgt_lbl = tk.Label(host, text="", bg=self._bg(), fg=self._dim(),
                           font=(self._fh(), 8), wraplength=620, justify="left")
        tgt_lbl.grid(row=6, column=1, sticky="w", **pad)

        tk.Label(host, text="提示: 三选一 —— ① 本地图片(我的图/ 或任意文件, 走助手主端口的 /img/); "
                            "② 任意网络地址(直接填 URL, 或点「贴官方网址」); "
                            "③ 官方武将立绘: 「挑选官方立绘」按武将名挑(本赛季配置表), "
                            "「从皮肤表挑」按武将名挑主游戏 OL 全部皮肤(威马超两套就是这么换的)",
                 bg=self._bg(), fg=self._dim(), font=(self._fh(), 8),
                 wraplength=620, justify="left").grid(row=7, column=1, sticky="w", **pad)

        enabled_var = tk.BooleanVar(value=bool(r.get("enabled", True)))
        tk.Checkbutton(host, text="启用", variable=enabled_var,
                       bg=self._bg(), fg=self._fg(), selectcolor="#33333f",
                       activebackground=self._bg(), font=(self._fh(), 9)).grid(row=8, column=1, sticky="w", **pad)

        # 按钮行挂在 bar_host 上(pack side=bottom), 不跟内容一起滚动 —— 这是"保存按钮
        #   永远看得见"的关键; 旧版把它当第 9 行 grid 进内容里, 内容超高就被裁掉了。
        btn_row = tk.Frame(bar_host, bg=self._bg())
        btn_row.pack(pady=10)

        def _ok():
            new_rule = {
                "id": r.get("id"),
                "name": name_var.get().strip(),
                "kind": kind_var.get().strip() or "背景",
                "pattern": pt.get("1.0", "end-1c").strip(),
                "target": target_var.get().strip(),
                "enabled": enabled_var.get(),
                "season": self._season() or "",
            }
            ok, msg = RR.validate_rule(new_rule)
            if not ok:
                messagebox.showwarning("规则校验", msg, parent=top)
                return
            # v4.70★ 保存前自检: 匹配串【本赛季命中不了】时当场说清楚, 能猜出正确写法就直接问
            #   一句"要不要改成这条"。这条路径挡的正是"配了没反应"—— 用户看不出是匹配串写错了。
            chk = RR.check_pattern(new_rule["pattern"], self._season())
            if chk["ok"] is False:
                sug = chk.get("suggest") or ""
                if sug:
                    if messagebox.askyesno(
                            "这条匹配串本赛季不会命中",
                            "%s\n\n要改成建议的这条吗?\n\n    %s\n\n"
                            "[是] 用建议的这条(推荐)\n[否] 照原样保存(确定自己没写错)"
                            % (chk["hint"], sug), parent=top):
                        new_rule["pattern"] = sug
                        pt.delete("1.0", "end")
                        pt.insert("1.0", sug)
                elif not messagebox.askyesno(
                        "这条匹配串本赛季不会命中",
                        "%s\n\n确定要照原样保存吗?" % chk["hint"], parent=top):
                    return
            rules = RR.load_rules()
            if is_edit:
                rules = [x if x.get("id") != new_rule["id"] else new_rule for x in rules]
            else:
                # 避免重复 pattern
                rules = [x for x in rules if x.get("pattern") != new_rule["pattern"]]
                rules.append(new_rule)
            RR.save_rules(rules)
            top.destroy()
            self._load_rules()

        tk.Button(btn_row, text="保存", command=_ok,
                  bg=self._green(), fg=self._bg(), font=(self._fh(), 10, "bold"),
                  padx=12, cursor="hand2").pack(side="left", padx=6)
        tk.Button(btn_row, text="取消", command=top.destroy,
                  bg=self._btn(), fg=self._fg(), font=(self._fh(), 9),
                  padx=12, cursor="hand2").pack(side="left", padx=6)

        host.columnconfigure(1, weight=1)
        # 高度按内容算, 不再写死(见本函数开头那段说明):
        #    want_h = 内容需求高 + 按钮行高 + 边距; 上限 = 屏幕的 85%(小屏笔记本也不会伸出去)。
        #    update_idletasks 必须先调一次, 否则 winfo_reqheight 拿到的还是 1。
        top.update_idletasks()
        want_h = host.winfo_reqheight() + btn_row.winfo_reqheight() + 34
        max_h = max(320, int(top.winfo_screenheight() * 0.85))
        top.geometry("840x%d" % max(340, min(want_h, max_h)))
        top.minsize(660, 260)
        top.update_idletasks()
        x = (top.winfo_screenwidth() - top.winfo_width()) // 2
        y = (top.winfo_screenheight() - top.winfo_height()) // 2
        top.geometry("+%d+%d" % (x, y))

    # ---------- 官方立绘挑选框 ----------
    def _official_skins(self):
        """官方立绘清单(懒加载 + 缓存)。

        581 条, 每次打开挑选框都去读 4MB 的赛季配置表会明显卡一下, 所以缓存起来;
        换赛季后助手本来就要重启, 不需要在这里做失效判断。
        """
        if self._skins is None:
            try:
                self._skins = RR.official_skin_options(self._season())
            except Exception as e:
                self._skins = []
                self._status("读取官方立绘清单失败: %s" % e)
        return self._skins

    def _official_picker(self, parent, on_pick, for_pattern=False):
        """按武将名挑官方立绘 —— 选中后回调拿到 {kind,name,skin,pattern,url}。

        为什么不用下拉框: 581 条塞进 Combobox 里翻不动。这里给"搜索 + 类别过滤 + 列表",
        输入武将名(或拼音资源名)就能定位; 同一武将的两套立绘会挨着显示, 一眼能切换。

        for_pattern=True 是另一种用途(填【匹配串】而不是填目标): 这时只列【本赛季在用的
          那套】—— 匹配串必须是游戏真的会请求的那条, 拿往季的旧名字去写匹配串, 现象就是
          "配了没反应"(夏侯惇/陆逊这类有 xiahoudun / xiahoudun_02 两套的武将最容易踩)。
        """
        opts = self._official_skins()
        if for_pattern:
            opts = [o for o in opts if o.get("current")]
        if not opts:
            messagebox.showinfo("官方立绘", "没读到官方立绘清单 —— 赛季配置表(data/season/…)在吗?",
                                parent=parent)
            return

        top = tk.Toplevel(parent)
        top.title("按武将名填匹配串(列的是本赛季在用的那套)" if for_pattern
                  else "挑选官方立绘(服务器上那张, 不用下载)")
        top.configure(bg=self._bg())
        top.transient(parent)
        top.grab_set()

        pad = {"padx": 8, "pady": 6}
        bar = tk.Frame(top, bg=self._bg())
        bar.pack(fill="x", **pad)
        tk.Label(bar, text="搜索:", bg=self._bg(), fg=self._fg(),
                 font=(self._fh(), 9)).pack(side="left")
        kw_var = tk.StringVar(value="")
        ent = tk.Entry(bar, textvariable=kw_var, width=26, bg=self._btn(), fg=self._fg(),
                       insertbackground=self._fg(), font=(self._fh(), 9))
        ent.pack(side="left", padx=(2, 8))
        tk.Label(bar, text="类别:", bg=self._bg(), fg=self._fg(),
                 font=(self._fh(), 9)).pack(side="left")
        kd_var = tk.StringVar(value="全部")
        kd = ttk.Combobox(bar, textvariable=kd_var, state="readonly", width=12,
                          values=["全部", "棋子立绘", "武将牌面", "主公立绘"])
        kd.pack(side="left", padx=(2, 8))

        wrap = tk.Frame(top, bg=self._bg())
        wrap.pack(fill="both", expand=True, padx=8)
        sb = tk.Scrollbar(wrap, orient="vertical")
        sb.pack(side="right", fill="y")
        lb = tk.Listbox(wrap, bg=self._btn(), fg=self._fg(), font=(self._fh(), 9), height=18,
                        selectmode="browse", activestyle="none", exportselection=False,
                        yscrollcommand=sb.set)
        sb.configure(command=lb.yview)
        lb.pack(side="left", fill="both", expand=True)

        shown = []

        def _fill(*_a):
            kw = kw_var.get().strip().lower()
            kind = kd_var.get()
            lb.delete(0, "end")
            del shown[:]
            for o in opts:
                if kind != "全部" and o.get("kind") != kind:
                    continue
                hay = ("%s %s %s" % (o.get("name"), o.get("skin"), o.get("kind"))).lower()
                if kw and kw not in hay:
                    continue
                mark = "【本赛季在用】" if o.get("current") else ("【第%s赛季的旧立绘】" % o.get("season")
                                                                if o.get("season") else "")
                lb.insert("end", "%s | %s（%s）%s" % (
                    o.get("kind"), o.get("name"), o.get("skin"), mark))
                shown.append(o)
            # 列表内容变了, 选中态要清掉, 否则"选中的那条"和显示的对不上
            lb.selection_clear(0, "end")

        kw_var.trace_add("write", _fill)
        kd.bind("<<ComboboxSelected>>", _fill)
        _fill()

        info = tk.Label(top, text=(
            "选中后点「用这条」—— 匹配串会填成【本赛季游戏真正会请求的那一段】"
            "(不含域名, x/pc 和 10/pc 两条线路都能命中)。"
            "这里只剩本赛季在用的那套: 往季的旧名字(xiahoudun、luxun 这种)游戏已经不再请求了。"
            if for_pattern else
            "选中后点「用这张」—— 目标会填成它的官方 URL, 一张图都不用下载"),
            bg=self._bg(), fg=self._dim(), font=(self._fh(), 8), wraplength=640,
            justify="left")
        info.pack(fill="x", padx=8)

        def _ok(_e=None):
            sel = lb.curselection()
            if not sel:
                return
            on_pick(shown[sel[0]])
            top.destroy()

        lb.bind("<Double-Button-1>", _ok)
        ent.bind("<Return>", _ok)

        btns = tk.Frame(top, bg=self._bg())
        btns.pack(pady=8)
        tk.Button(btns, text=("用这条" if for_pattern else "用这张"), command=_ok,
                  bg=self._green(), fg=self._bg(),
                  font=(self._fh(), 10, "bold"), padx=12, cursor="hand2").pack(side="left", padx=6)
        tk.Button(btns, text="取消", command=top.destroy, bg=self._btn(), fg=self._fg(),
                  font=(self._fh(), 9), padx=12, cursor="hand2").pack(side="left", padx=6)

        # v4.86★ 【末尾这句写死 760x470 才是"没有按钮"的真凶】:
        #   ① 它把开头给的 980x640 整个覆盖掉(宽度也缩回 760, 长匹配串又被截半);
        #   ② 高度写死 470, 而这个窗口的内容实测要 ~451px(离屏量过: dev/_measure_dress_ui.py),
        #      系统字号/缩放稍大一点就超 —— 超出那一截(最底下的按钮行)被推到窗口外面:
        #      看不见、点不到, 于是"取消也不行, 只能点右上角 × 关窗"。
        #   改成: 按内容撑 → 最多占屏高 85% → 再居中; 真放不下就让列表少显示几行,
        #      优先保住按钮行(宁可列表短, 也不能没按钮)。
        top.update_idletasks()
        req_w = max(980, top.winfo_reqwidth())
        req_h = top.winfo_reqheight()
        max_h = max(420, int(top.winfo_screenheight() * 0.85))
        if req_h > max_h:
            over = req_h - max_h
            lb.configure(height=max(6, int(lb.cget("height")) - int(over / 18) - 1))
            top.update_idletasks()
            req_h = top.winfo_reqheight()
        w = min(req_w, max(760, top.winfo_screenwidth() - 60))
        h = min(req_h, max_h)
        # 居中到【父窗口所在那块屏】上: 以前固定按主屏算, 双屏(副屏在左/上, 坐标是负的)时
        #   窗口会弹到用户根本没看的那块屏幕上 —— 表现就是"好像弹出来了又看不见"。
        try:
            px, py = parent.winfo_rootx(), parent.winfo_rooty()
            x = px + max(0, (parent.winfo_width() - w) // 2)
            y = py + max(0, (parent.winfo_height() - h) // 2)
        except Exception:
            x = (top.winfo_screenwidth() - w) // 2
            y = (top.winfo_screenheight() - h) // 2
        # 无论如何别跑到屏幕外(否则标题栏都摸不到)
        x = max(0, min(x, max(0, top.winfo_screenwidth() - w)))
        y = max(0, min(y, max(0, top.winfo_screenheight() - h)))
        top.geometry("%dx%d+%d+%d" % (w, h, x, y))

    def _dress_picker(self, parent, on_pick, on_multi=None, multi_label=None):
        """v4.75★ OL 大图按武将名挑(皮肤表) —— 选中探测结果后回调 {pattern,name,dynamic}。

        数据源 core/dress_table.py(data/dress_table.json)。两步走:
          ① 搜索: SkinConf 5300+ 套按 武将名/皮肤名/编号 过滤;
          ② 探测: 对选中的一套发 ~7 个请求问官方服务器哪几张真的存在(静态大图 + 动态帧),
             只有探到的才允许选 —— 不猜编号, 猜出来的就是 404(和「按编号探测」同一口径)。

        v4.86★ on_multi: 传了就多给一个「把选中的几帧都配上」按钮(列表变多选),
          回调拿到【选中的那一批】而不是一条 —— 动态皮肤是【一帧一个文件】, 换整段动画
          本来就得一帧一条规则, 以前只能一条条配, 用户"不会用"就卡在这里。

        v4.110★ multi_label: 批量按钮的文案。目标侧也接了批量(先有图、再挑帧), 语义和
          匹配串侧(先挑帧、再选图)是反的, 两边共用一个"把选中的帧都配上"会看岔 ——
          调用方可以改这句, 提示语(探测完那行蓝字)也跟着变, 免得说一套做一套。
        """
        _multi_label = multi_label or "把选中的帧都配上 …"
        from core import dress_table as DT
        t = DT.load()
        top = tk.Toplevel(parent)
        top.title("从皮肤表挑 OL 大图(主游戏 5300+ 套皮肤)")
        top.configure(bg=self._bg())
        top.transient(parent)
        top.grab_set()
        # v4.86★ 给个够宽的初始尺寸: 以前窗口按内容撑, 探测结果那一列(编号+名字+匹配串)
        #   经常被截掉一半 —— 用户原话"那个编辑框的 ui 不是很友好, 没有显示完整"。
        top.geometry("980x640")
        pad = {"padx": 8, "pady": 6}

        if not t["loaded"]:
            tk.Label(top, text="本地还没有皮肤表(data/dress_table.json)。\n"
                               "去图库页(/gallery)点「OL 皮肤表」→ 联网抓取一次, 或者跑 "
                               "python core/dress_table.py",
                     bg=self._bg(), fg=self._gold(), font=(self._fh(), 9),
                     justify="left").pack(**pad)
            tk.Button(top, text="关闭", command=top.destroy, bg=self._btn(), fg=self._fg(),
                      padx=12).pack(pady=8)
            return

        bar = tk.Frame(top, bg=self._bg())
        bar.pack(fill="x", **pad)
        tk.Label(bar, text="搜索(武将名/皮肤名/编号):", bg=self._bg(), fg=self._fg(),
                 font=(self._fh(), 9)).pack(side="left")
        kw_var = tk.StringVar(value="")
        ent = tk.Entry(bar, textvariable=kw_var, width=22, bg=self._btn(), fg=self._fg(),
                       insertbackground=self._fg(), font=(self._fh(), 9))
        ent.pack(side="left", padx=(2, 8))

        status = tk.Label(top, text="共 %d 套 · 更新于 %s" % (
            t["count"], time.strftime("%Y-%m-%d", time.localtime(t["updated"]))),
            bg=self._bg(), fg=self._dim(), font=(self._fh(), 8),
            justify="left", wraplength=940, anchor="w")   # v4.86★ 提示语经常一整句被挤成一行省略
        status.pack(fill="x", padx=8)

        wrap = tk.Frame(top, bg=self._bg())
        wrap.pack(fill="both", expand=True, padx=8)
        sb = tk.Scrollbar(wrap, orient="vertical")
        sb.pack(side="right", fill="y")
        sbh = tk.Scrollbar(wrap, orient="horizontal")     # v4.86★ 匹配串长, 没有横滚就永远看不全
        sbh.pack(side="bottom", fill="x")
        # 按钮行(先建出来: 「已选 N 条」那个计数标签要挂在它上面)
        btns_row = tk.Frame(top, bg=self._bg())
        btns_row.pack(pady=8)

        # v4.87★ 多选用 "multiple" 而不是 "extended" —— 用户原话: "没有办法选好几个啊"。
        #   extended 必须按住 Ctrl/Shift 才能多选: 直接点第二行会把第一行顶掉, 手感就是
        #   "只能选一个"; 而且这句规矩只写在状态栏里, 没人看得见。
        #   multiple: 【点一下选中、再点一下取消】, 想选几条点几条, 不用记快捷键。
        #   不给 on_multi 的老调用方仍是 browse(单选), 行为完全不变。
        lb = tk.Listbox(wrap, bg=self._btn(), fg=self._fg(), font=(self._fh(), 9), height=18,
                        selectmode=("multiple" if on_multi else "browse"),
                        activestyle="none", exportselection=False,
                        yscrollcommand=sb.set, xscrollcommand=sbh.set)
        sb.configure(command=lb.yview)
        sbh.configure(command=lb.xview)
        lb.pack(side="left", fill="both", expand=True)

        # v4.87★ "我到底选上没选上" 要看得见: 按钮行左边常驻一个「已选 N 条」。
        #   (以前全靠状态栏一句话, 选没选上全凭猜 —— 这是"多选没用"的另一半原因。)
        sel_lab = tk.Label(btns_row, text="", bg=self._bg(), fg=self._gold(),
                           font=(self._fh(), 9, "bold"))
        sel_lab.pack(side="left", padx=(0, 10))

        def _on_sel(*_a):
            if not on_multi:
                return
            n = len(lb.curselection())
            sel_lab.configure(text=("已选 %d 条" % n) if n else "")

        lb.bind("<<ListboxSelect>>", _on_sel)

        shown = []
        probed = {}                                     # 探测结果行 → {"pattern","name","dynamic"}

        def _fill_search(*_a):
            del shown[:]
            probed.clear()
            lb.delete(0, "end")
            for it in DT.search(kw_var.get().strip(), limit=200):
                lb.insert("end", "%s | %s·%s" % (it["id"], it["title"], it["name"]))
                shown.append(it)
            lb.selection_clear(0, "end")

        def _fill_probe(res):
            del shown[:]
            probed.clear()
            lb.delete(0, "end")
            nm = (res.get("title") or "") + "·" + (res.get("name") or "")
            for f in (res.get("found") or []):
                tag = ("动态帧 " + f["frame"]) if f.get("dynamic") else "静态大图"
                lb.insert("end", "%s %sx%s | %s" % (
                    tag, f.get("w") or "?", f.get("h") or "?", f["pattern"]))
                # v4.86★ 把官方地址一起带上: 「让背景动起来」要拿这些地址当轮播的帧
                shown.append({"pattern": f["pattern"], "name": nm, "url": f.get("url") or "",
                              "dynamic": bool(f.get("dynamic"))})
            lb.selection_clear(0, "end")
            if not shown:
                lb.insert("end", "(这套编号在官方服务器上一张都没探到 —— 换一套试试)")

        def _probe():
            sel = lb.curselection()
            if not sel or not shown:
                status.configure(text="先在列表里选一套皮肤再点探测", fg=self._gold())
                return
            it = shown[sel[0]]
            status.configure(text="探测中(静态大图 + 6 个动态帧, 等几秒)…", fg=self._gold())
            top.update_idletasks()
            try:
                res = DT.probe_skin(it["id"])
            except Exception as e:
                status.configure(text="探测失败: %s" % e, fg=self._red())
                return
            _fill_probe({"found": res, "title": it["title"], "name": it["name"]})
            # v4.87★ 怎么选要写在脸上: 以前写"按住 Ctrl/Shift", 用户照着点也选不上(其实是
            #   模式不对), 现在改成"点一下选中、再点一下取消", 并在按钮行左边显示已选几条。
            n_dyn = len([x for x in (res or []) if x.get("dynamic")])
            tip = ("探到 %d 张(其中动态帧 %d 张) —— 要哪几张就【在列表里点一下选中、再点一下取消】"
                   "(不用按 Ctrl), 选完点「%s」; 只换一张就双击那一行。"
                   % (len(res), n_dyn, _multi_label))
            if not n_dyn:
                tip += " 这套只有静态图(没有动态帧), 要动态效果得换一套带「动态帧」字样的。"
            status.configure(text=tip, fg=self._blue())

        def _back():
            _fill_search()
            status.configure(text="搜索模式", fg=self._dim())

        def _ok(_e=None):
            sel = lb.curselection()
            if not sel:
                return
            # v4.86★ 多选模式下若选了不止一条, 双击也走批量 —— 以前双击只配第一条就关窗,
            #   看着就像"多选没用"。
            if on_multi and len(sel) > 1:
                on_multi([shown[i] for i in sel if i < len(shown)])
                top.destroy()
                return
            it = shown[sel[0]]
            if "pattern" in it:
                on_pick(it)
                top.destroy()
            else:
                # v4.110★ 双击的是【皮肤】(还没探测) → 以前什么都不发生, 静默最像"坏了"
                status.configure(text="这一行是【一套皮肤】还不是图 —— 先只选它 → 点「探测大图」, "
                                      "再从探出来的那几张里挑。", fg=self._gold())

        def _multi():
            """v4.86★ 选中的几帧一起配 —— 动态皮肤一帧一个文件, 换整段动画就是一帧一条规则。"""
            sel = lb.curselection()
            if not sel or not shown:
                status.configure(text="先选一套皮肤 → 点「探测大图」, 再从结果里挑要换的那几帧",
                                 fg=self._gold())
                return
            items = [shown[i] for i in sel if i < len(shown)]
            # v4.87★ 还在【搜索结果】(皮肤列表)这一步就点批量: 那些行没有 pattern,
            #   配出来是空规则 —— 以前静悄悄什么都不发生, 用户就以为"多选坏了/没反应"。
            if not any(str(x.get("pattern") or "").strip() for x in items):
                status.configure(text="现在选中的是【皮肤】(还没有帧)。先只选一套 → 点「探测大图」, "
                                      "再从探到的帧里挑要换的几张。", fg=self._gold())
                return
            try:
                on_multi(items)
                top.destroy()
            except Exception as e:
                status.configure(text="配规则失败: %s" % e, fg=self._red())

        kw_var.trace_add("write", _fill_search)
        _fill_search()

        btns = btns_row                      # 「已选 N 条」标签已经挂在上面了
        tk.Button(btns, text="探测大图", command=_probe, bg=self._act(), fg=self._gold(),
                  font=(self._fh(), 9, "bold"), padx=10, cursor="hand2").pack(side="left", padx=6)
        if on_multi:
            # v4.110★ 「全选」: 探测出来就是 1 张静态大图 + 6 个动态帧, 要整套就得点七下,
            #   以前只能一张张点 —— 而且列表要是 browse(单选)模式, 点第二张还会把第一张顶掉。
            def _select_all():
                lb.selection_set(0, "end")      # 一次选中列表里全部行
                _on_sel()                       # 顺手把「已选 N 条」那个计数刷出来

            tk.Button(btns, text="全选", command=_select_all,
                      bg=self._btn(), fg=self._fg(), font=(self._fh(), 9),
                      padx=8, cursor="hand2").pack(side="left", padx=6)
            tk.Button(btns, text=_multi_label, command=_multi,
                      bg=self._act(), fg=self._green(), font=(self._fh(), 9, "bold"),
                      padx=10, cursor="hand2").pack(side="left", padx=6)
        tk.Button(btns, text="返回搜索", command=_back, bg=self._btn(), fg=self._fg(),
                  font=(self._fh(), 9), padx=10, cursor="hand2").pack(side="left", padx=6)
        tk.Button(btns, text="用这条", command=_ok, bg=self._green(), fg=self._bg(),
                  font=(self._fh(), 10, "bold"), padx=12, cursor="hand2").pack(side="left", padx=6)
        tk.Button(btns, text="取消", command=top.destroy, bg=self._btn(), fg=self._fg(),
                  font=(self._fh(), 9), padx=12, cursor="hand2").pack(side="left", padx=6)
        lb.bind("<Double-Button-1>", _ok)

        # v4.86★ 【末尾这句写死 760x470 才是"没有按钮"的真凶】:
        #   ① 它把开头给的 980x640 整个覆盖掉(宽度也缩回 760, 长匹配串又被截半);
        #   ② 高度写死 470, 而这个窗口的内容实测要 ~451px(离屏量过: dev/_measure_dress_ui.py),
        #      系统字号/缩放稍大一点就超 —— 超出那一截(最底下的按钮行)被推到窗口外面:
        #      看不见、点不到, 于是"取消也不行, 只能点右上角 × 关窗"。
        #   改成: 按内容撑 → 最多占屏高 85% → 再居中; 真放不下就让列表少显示几行,
        #      优先保住按钮行(宁可列表短, 也不能没按钮)。
        top.update_idletasks()
        req_w = max(980, top.winfo_reqwidth())
        req_h = top.winfo_reqheight()
        max_h = max(420, int(top.winfo_screenheight() * 0.85))
        if req_h > max_h:
            over = req_h - max_h
            lb.configure(height=max(6, int(lb.cget("height")) - int(over / 18) - 1))
            top.update_idletasks()
            req_h = top.winfo_reqheight()
        w = min(req_w, max(760, top.winfo_screenwidth() - 60))
        h = min(req_h, max_h)
        # 居中到【父窗口所在那块屏】上: 以前固定按主屏算, 双屏(副屏在左/上, 坐标是负的)时
        #   窗口会弹到用户根本没看的那块屏幕上 —— 表现就是"好像弹出来了又看不见"。
        try:
            px, py = parent.winfo_rootx(), parent.winfo_rooty()
            x = px + max(0, (parent.winfo_width() - w) // 2)
            y = py + max(0, (parent.winfo_height() - h) // 2)
        except Exception:
            x = (top.winfo_screenwidth() - w) // 2
            y = (top.winfo_screenheight() - h) // 2
        # 无论如何别跑到屏幕外(否则标题栏都摸不到)
        x = max(0, min(x, max(0, top.winfo_screenwidth() - w)))
        y = max(0, min(y, max(0, top.winfo_screenheight() - h)))
        top.geometry("%dx%d+%d+%d" % (w, h, x, y))

    def _add_rule(self):
        self._rule_dialog()

    def _edit_selected(self):
        r, is_custom = self._selected_rule()
        if r is None:
            messagebox.showinfo("编辑", "请先在上面选一条规则。")
            return
        if not is_custom:
            # 默认规则直接改没有意义(下次读配置表就被覆盖), 所以引导到"转成自定义"
            if messagebox.askyesno(
                    "这是默认规则",
                    "这是从赛季配置表算出来的【默认规则】, 直接改会在下次刷新时被覆盖。\n\n"
                    "要我把它复制成一条【自定义规则】(匹配串原样带过来), 然后接着编辑吗?"):
                r = RR.rule_from_default(r)
                self._rule_dialog(r)
            return
        self._rule_dialog(r)

    def _clone_to_custom(self):
        """v4.69★ 默认规则 → 可编辑的自定义规则(用户问的"不能编辑啊, 默认规则")。"""
        r, is_custom = self._selected_rule()
        if r is None:
            messagebox.showinfo("转成自定义", "请先在上面选一条规则。")
            return
        if is_custom:
            messagebox.showinfo("转成自定义", "这条已经是自定义规则了, 直接点「编辑」即可。")
            return
        new_r = RR.rule_from_default(r)
        rules = RR.load_rules()
        rules = [x for x in rules if x.get("pattern") != new_r.get("pattern")]
        rules.append(new_r)
        RR.save_rules(rules)
        self._status("已把默认规则复制成自定义规则(匹配串 %s), 现在可以随意改目标了" % new_r.get("pattern"))
        self._load_rules()
        self._rule_dialog(new_r)

    def _set_target_for_selected(self):
        """选中一行 → 直接换图(自动转成自定义), 省掉"抄匹配串"这一步。

        这是最常用的一条路: 用户看到「棋子立绘 luzhi」想换成自己的图, 不需要知道
        匹配串长什么样。默认规则会自动复制成自定义规则(同名覆盖)。
        """
        r, is_custom = self._selected_rule()
        if r is None:
            messagebox.showinfo("换图", "请先在上面选一条规则。")
            return
        if not is_custom:
            r = RR.rule_from_default(r)
        p = filedialog.askopenfilename(
            title="选择要替换成的本地图片",
            filetypes=[("图片", "*.png *.jpg *.jpeg *.webp *.gif"), ("所有文件", "*.*")],
            initialdir=RR.IMG_DIR if os.path.exists(RR.IMG_DIR) else os.path.expanduser("~"))
        if not p:
            return
        r["target"] = p
        r["enabled"] = True
        rules = RR.load_rules()
        rules = [x for x in rules if x.get("pattern") != r.get("pattern")]
        rules.append(r)
        RR.save_rules(rules)
        self._status("已设置替换: %s → %s" % (r.get("pattern"), os.path.basename(p)))
        self._load_rules()

    def _check_custom_rules(self):
        """自检【自定义规则】: 哪些匹配串本赛季命中不了, 能猜出正确写法的一键修正。

        为什么值得一个按钮(用户原话: "我用这俩换咋都不行? 为什么你写的那几个生效了?"):
          匹配串写错【不会报错、不写日志、界面那一列还全显示"已就绪"】, 唯一的现象就是
          "游戏里没变化" —— 用户只能一条条猜。这里一次把所有坏规则摊开, 并给出建议写法。
        最常见的就是"少了本赛季的皮肤后缀": 夏侯惇/陆逊从第 2 赛季起 DefaultSkin 变成
          xiahoudun_02 / luxun_02, 写 xiahoudun.png 一条都命中不了(包含匹配不等于前缀匹配)。
        """
        custom = RR.load_rules()
        if not custom:
            messagebox.showinfo("自检", "还没有自定义规则。\n(默认规则是从赛季配置表现算的, 不用自检)")
            return
        season = self._season()
        bad, good, unknown = [], [], []
        for r in custom:
            res = RR.check_pattern(r.get("pattern"), season)
            if res["ok"] is True:
                good.append((r, res))
            elif res["ok"] is None:
                unknown.append((r, res))     # 背景/音效这类不在配置表里, 判不了, 不算错
            else:
                bad.append((r, res))
        if not bad:
            messagebox.showinfo(
                "自检自定义规则",
                "自定义规则 %d 条, 没发现命中不了的情况。\n"
                "(其中 %d 条能在本赛季命中, %d 条是背景/音效这类不在配置表里的, 自检判不了)"
                % (len(custom), len(good), len(unknown)))
            return
        lines = ["本赛季命中不了的自定义规则 %d 条(自定义共 %d 条):\n" % (len(bad), len(custom))]
        for r, res in bad[:20]:
            lines.append("· %s\n    匹配串: %s\n    %s\n"
                         % (r.get("name") or r.get("id"),
                            r.get("pattern"),
                            ("建议改成: " + res["suggest"]) if res.get("suggest") else res["hint"]))
        if len(bad) > 20:
            lines.append("…(还有 %d 条未列出)\n" % (len(bad) - 20))
        fixable = [(r, res) for r, res in bad if res.get("suggest")]
        if fixable:
            lines.append("\n其中 %d 条能自动改成建议的匹配串。" % len(fixable))
            if messagebox.askyesno("自检自定义规则", "\n".join(lines) + "\n\n要现在自动修正吗?"):
                changed = 0
                for r, res in fixable:
                    self.log("自检修正: %s → %s" % (r.get("pattern"), res["suggest"]))
                    r["pattern"] = res["suggest"]
                    changed += 1
                RR.save_rules(custom)
                self._status("已自动修正 %d 条匹配串(其余请照上面提示手动改)" % changed)
                self._load_rules()
        else:
            messagebox.showinfo("自检自定义规则", "\n".join(lines))

    def _delete_selected(self):
        r, is_custom = self._selected_rule()
        if r is None or not is_custom:
            messagebox.showinfo("删除", "只有【自定义】规则能删。默认规则请用「切换启用」或换个目标。")
            return
        if not messagebox.askyesno("删除规则", "删除这条自定义规则?\n%s" % r.get("pattern", "")):
            return
        rules = [x for x in RR.load_rules() if x.get("id") != r.get("id")]
        RR.save_rules(rules)
        self._load_rules()

    def _toggle_selected(self):
        r, is_custom = self._selected_rule()
        if r is None:
            messagebox.showinfo("切换启用", "请先在上面选一条规则。")
            return
        if not is_custom:
            # 默认规则没有存储, 想停用只能"复制成自定义规则 + 停用"(同名覆盖)
            r = RR.rule_from_default(r)
            r["enabled"] = False
        else:
            r["enabled"] = not r.get("enabled", True)
        rules = [x for x in RR.load_rules() if x.get("id") != r.get("id")]
        rules.append(r)
        RR.save_rules(rules)
        # v4.71★ 停用/启用后把话说清楚。"停用默认规则"这条以前是【假的】(build_rules 里
        #   被停用的规则没能压住同名默认规则, 见 core/resource_rules.py 的说明), 修好之后
        #   这里明确告诉用户"会存成一条同名自定义规则压住它", 否则他看到多出来一行会以为
        #   是重复添加。
        if r.get("enabled", True):
            self._status("已启用: %s" % r.get("pattern"))
        else:
            # v4.74★ 话术跟着修: 停用的行现在仍留在表格里(带【自定义】标记, 启用列=否),
            #   直接指给用户"选它再点一次"就行, 不用再解释"同名压住"那套了。
            self._status("已停用: %s —— 这一行还在列表里(『启用』列=否), 选中它再点一次"
                         "『切换启用』即可恢复" % r.get("pattern"))
        self._load_rules()

    # ---------- 预览 / 核对 / 同步 (v4.71★) ----------
    def _preview_selected(self):
        """看选中这一行: 你现在要换成的那张图(优先), 没有就用系统看图打开/浏览器开官方那张。

        用户原话: "有预览吗?" —— 预览必须【不下载、不等待】: 官方立绘直接开它的 URL,
        本地图直接扔给系统看图程序。要一次看全部请用「预览全部(网页图墙)」。
        """
        r, _is_custom = self._selected_rule()
        if r is None:
            messagebox.showinfo("预览", "请先在列表里选一条规则(双击一行也是预览)。")
            return
        base = self._base_url()
        pair = RR.preview_pair(base, r, sizes=self._sizes)
        url = pair["current"] or pair["official"]
        if not url:
            messagebox.showinfo(
                "预览",
                "这条还没有图可看。\n\n匹配串: %s\n%s\n\n"
                "想换的话: 点「给这条换图 …」选一张本地图, 或用「挑选官方立绘 …」"
                "直接用服务器上那张。\n(官方原图地址: %s)"
                % (r.get("pattern") or "", pair["note"] or "",
                   RR.official_target_url(r.get("pattern")) or "(这类没有官方图)"))
            return
        try:
            if RR.is_remote_target(url):
                import webbrowser
                webbrowser.open(url)
            else:
                os.startfile(url)          # Windows: 交给系统默认看图程序
        except Exception as e:
            self._status("预览打不开(%s): %s —— 地址是 %s" % (type(e).__name__, e, url))
            return
        # v4.72★ 顺手把"尺寸/会不会变形"讲一句 —— 用户原话:"那个替换前的尺寸大小, 分辨率,
        #   像素多少? 要不换进去, 好乱都变形了"。没量过的就在后台量一下(不挡预览),
        #   量完状态栏补上结果; 已经量过且【会变形/格式不对】的, 当场弹一次警告(同一条只弹一次,
        #   否则连着双击十行会被弹十次)。
        info = self._sizes.get(str(r.get("pattern") or ""))
        if info is None:
            self._status("预览: %s → %s (%s); 正在量尺寸…"
                         % (r.get("pattern") or "", url, pair["note"]))
            self._measure_rules([r], quiet=True)
            return
        self._status("预览: %s → %s (%s) ｜ 原图 %s, 你的 %s ｜ %s"
                     % (r.get("pattern") or "", url, pair["note"],
                        RR.size_text(info.get("official")) or "?",
                        RR.size_text(info.get("mine")) or "?",
                        info.get("advice") or ""))
        pat = str(r.get("pattern") or "")
        if (info.get("ok") is False or info.get("fmt_ok") is False) and pat not in self._size_warned:
            self._size_warned.add(pat)
            messagebox.showwarning(
                "尺寸提醒",
                "%s\n\n%s\n\n(改完再点一次「量尺寸」就能确认)"
                % (info.get("advice") or "", info.get("fmt_note") or ""))

    def _preview_all(self):
        """把当前这张表【全部】生成一个网页图墙(官方那张 ↔ 你要换的那张 并排)。

        用户原话: "然后把资源替换; 全部显示出来能够替换的" + "有预览吗"。
        360+ 条在 tkinter 里画不出来(700 张图会卡死), 所以写成 HTML 丢给浏览器,
        图片用 loading=lazy, 打开只在滚到的位置才去取 —— 不会一下子打 700 个请求。
        """
        rules = list(self._all_rules or [])
        if not rules:
            messagebox.showinfo("预览全部", "列表还是空的, 先点「读取默认规则」。")
            return
        season = self._season() or ""

        def _work():
            try:
                base = self._base_url()
                html = RR.preview_html(rules, base_url=base, season=season,
                                       probe=self._probe, sizes=self._sizes)
                os.makedirs(RR.REF_DIR, exist_ok=True)
                p = os.path.join(RR.REF_DIR, "%s_资源预览.html" % (season or "current"))
                import io as _io
                with _io.open(p, "w", encoding="utf-8") as f:
                    f.write(html)
                self.root.after(0, self._preview_all_done, p, len(rules))
            except Exception as e:
                self.root.after(0, self._status, "生成预览页失败: %s" % e)
        threading.Thread(target=_work, daemon=True).start()
        self._status("正在生成预览页(共 %d 条)…" % len(rules))

    def _preview_all_done(self, path, n):
        try:
            import webbrowser
            webbrowser.open("file:///" + path.replace("\\", "/"))
        except Exception as e:
            self._status("预览页已生成但打不开浏览器: %s —— 文件在 %s" % (e, path))
            return
        self._status("预览页已生成并打开(%d 条): %s —— 双击表格里的行是个体预览" % (n, path))

    # ---------- 尺寸: 原图多大 / 换了会不会变形 (v4.72★) ----------
    def _row_size_info(self, r):
        """这一行「尺寸」列要显示的尺寸信息 —— 就地补齐那些不用联网、不用点按钮的部分。

        两件事在这里做掉:
          ① 还没量过的: "你这张"是本地文件, 读文件头就知道(不联网), 直接填上 ——
             所以一打开表, 自己放好的图有多大就已经显示了; 要发请求的是官方那一半。
          ② 量过之后又把图换了的: 结果里"你的图"那一半已经作废(换图是用户最常做的事),
             就地重算这一半 + 重新判比例; 官方那一半留着(它跟目标无关, 不用再请求一次)。
        """
        pat = str(r.get("pattern") or "")
        tgt = str(r.get("target") or "").strip()
        info = self._sizes.get(pat)
        if info is not None and str(info.get("mine_url") or "") == tgt:
            return info
        _tsz = (0, 0, "")
        if tgt and not RR.is_remote_target(tgt) and os.path.isfile(tgt):
            _tsz = RR.local_image_size(tgt)[0]
        _fok, _fnote = RR.check_target_format(tgt)
        if info is None:
            return {"official": (0, 0, ""), "mine": _tsz, "mine_url": tgt,
                    "ok": None, "advice": "", "fmt_ok": _fok, "fmt_note": _fnote}
        info = dict(info)
        info.update({"mine": _tsz, "mine_url": tgt, "fmt_ok": _fok, "fmt_note": _fnote})
        info["ok"], info["advice"] = RR.size_advice(info.get("official") or (0, 0, ""), _tsz)
        self._sizes[pat] = info      # 存回去, 免得每画一行就重算一遍
        return info

    def _measure_sizes(self):
        """量尺寸 —— 选中一行只量这一行; 没选中就量【当前表里显示的】那些。

        用户原话: "还有那个替换前的尺寸大小, 分辨率, 像素多少? 要不换进去, 好乱都变形了"。
        为什么做成按钮而不是自动: 官方图有几百张, 每张要发一个请求 —— 一打开页面就自动打
          几百个请求既慢又不礼貌。做成按钮后: 想量哪条量哪条, 量过的写进
          data/image_sizes.json 缓存, 以后再点几乎是瞬时的。
        为什么"没选中"是量【表里显示的】而不是量全部: 表上有搜索框 + 类型筛选, 用户筛到
          "棋子立绘"再点量尺寸, 意思显然就是量这 300 条(而不是连主公大图一起量)。
        """
        r, _is_custom = self._selected_rule()
        if r is not None:
            self._measure_rules([r])
            return
        shown_pats = {str(self.tree.item(i, "values")[2]) for i in self.tree.get_children()}
        rules = [x for x in (self._all_rules or []) if str(x.get("pattern")) in shown_pats]
        if not rules:
            messagebox.showinfo("量尺寸", "列表还是空的, 先点「读取默认规则」。")
            return
        if not messagebox.askyesno(
                "量尺寸",
                "没选中行 → 量【当前表里显示的 %d 条】。\n\n"
                "① 官方原图: 每条只向官方发一个小请求, 只取前 4KB 就够(不下载整张图);\n"
                "② 你这张: 本地文件读一下文件头就行, 不联网。\n\n"
                "第一次可能要等一会儿(几百条), 量过的会缓存, 以后再点就很快。\n"
                "只量一条更省时间: 先在表里选中那一行, 再点这个按钮。" % len(rules)):
            return
        self._measure_rules(rules)

    def _measure_rules(self, rules, quiet=False):
        """后台量这些规则的尺寸, 量完刷新表格。quiet=True 时不刷状态栏(预览里顺手量的)。"""
        rules = list(rules or [])
        if not rules:
            self._status("没有要量的规则。")
            return
        if not quiet:
            self._status("正在量尺寸 %d 条(官方图只取前 4KB, 不会下载整张)…" % len(rules))

        def _work():
            got = bad = 0
            for i, r in enumerate(rules):
                try:
                    info = RR.rule_size_info(r, timeout=6.0)
                except Exception as e:
                    # 单条量不出来不能拖垮整批: 记下原因接着量下一条
                    info = {"pattern": str(r.get("pattern") or ""), "official": (0, 0, ""),
                            "official_note": "%s: %s" % (type(e).__name__, e),
                            "mine": (0, 0, ""), "ok": None, "advice": "",
                            "fmt_ok": None, "fmt_note": ""}
                self._sizes[str(r.get("pattern") or "")] = info
                if RR.size_text(info.get("official")) or RR.size_text(info.get("mine")):
                    got += 1
                if info.get("ok") is False or info.get("fmt_ok") is False:
                    bad += 1
                if not quiet and i and i % 25 == 0:
                    self.root.after(0, self._status,
                                    "正在量尺寸 %d/%d…(官方图只取前 4KB)" % (i, len(rules)))
            self.root.after(0, self._measure_done, got, bad, len(rules))

        threading.Thread(target=_work, daemon=True).start()

    def _measure_done(self, got, bad, total):
        self._fill_tree(self._all_rules, self._custom)
        msg = "尺寸量完: %d/%d 条量到" % (got, total)
        if bad:
            msg += ("; ⚠ %d 条有问题(比例和原图不一致会被拉伸变形, 或格式游戏显示不了)"
                    " —— 看「尺寸」那一列的 ⚠" % bad)
        else:
            msg += "; 没发现有会变形的"
        self._status(msg)
        self.log(msg)

    def _probe_official(self):
        """问服务器"这张图到底在不在"(HEAD, 不下载图)。

        用户原话: "根据这个能查到皮肤的 png 或者 jpg 吗" —— 光把名字拼成 URL 不算查到。
        服务器上没有那个文件时, 规则一配就是 404, 现象是"棋子变透明"。
        选中一行只核这一行; 没选就问你一句要不要全核(几百个请求会比较慢)。
        """
        r, _is_custom = self._selected_rule()
        targets = []
        if r is not None:
            targets = [r]
        else:
            cands = [x for x in self._all_rules
                     if x.get("kind") in RR.OFFICIAL_KINDS
                     and RR.official_target_url(x.get("pattern"))]
            if not cands:
                messagebox.showinfo("核对官方图", "列表是空的, 先点「读取默认规则」。")
                return
            if not messagebox.askyesno(
                    "核对官方图",
                    "没有选中行。要核对【全部 %d 条】官方资源吗?\n\n"
                    "做法: 对每一条的官方图片地址发一个 HEAD 请求(不下载图), 大约要 %d 秒。\n"
                    "结论会写进表格『这是什么』那一列(查不到的会红字标出)。"
                    % (len(cands), max(5, int(len(cands) * 0.35)))):
                return
            targets = cands

        def _work():
            bad = {}
            done = 0
            for rule in targets:
                url = RR.official_target_url(rule.get("pattern"))
                if not url:
                    continue
                alive, detail = RR.official_alive(url, timeout=5.0)
                done += 1
                if not alive:
                    bad[str(rule.get("pattern") or "")] = detail
                if done % 20 == 0:
                    self.root.after(0, self._status,
                                    "核对官方资源 %d/%d(查不到 %d 条)…" % (done, len(targets), len(bad)))
            self.root.after(0, self._probe_done, bad, done)
        threading.Thread(target=_work, daemon=True).start()
        self._status("正在核对官方资源 %d 条(HEAD 请求, 不下载图)…" % len(targets))

    def _probe_done(self, bad, done):
        self._probe.update(bad or {})
        if bad:
            self._status("核对完成: %d 条里有 %d 条服务器上查不到(已在列表里标出; "
                         "这类名字配了也不会命中, 建议改用「挑选官方立绘」列出的名字)" % (done, len(bad)))
        else:
            self._status("核对完成: %d 条官方地址都能取到(服务器上确实有这些图)" % done)
        self._fill_tree(self._all_rules, self._custom)

    def _sync_browser(self):
        """点一下就同步: 让游戏页面【立刻】重拉 /rules(不等脚本那 15 秒轮询)。

        用户原话: "怎么换在 python 端能操作更新的, 就是我换完, 点 python 端就能同步更新?"
        脚本侧本来就每 15 秒拉一次(sgs_bridge.user.js 的 SWAP_POLL_MS), 所以"换完等一会儿"
        也会生效; 但等 15 秒没法确认成没成, 于是这里直接给页面发一条 js:
          __sgsResSwap.pull()    → 立刻重拉规则
          再回读 __sgsResSwap.state → 报"浏览器现在生效几条 / 换肤开关开着吗"
        结果是通过桥接服务(助手主端口)下发的, 所以必须【游戏页开着 + 脚本在跑】才有回音。
        """
        try:
            from core import bridge as _BR
            st = getattr(_BR, "STATE", None)
        except Exception as e:
            messagebox.showinfo("同步", "读不到桥接模块(core/bridge.py): %s" % e)
            return
        if st is None:
            messagebox.showinfo("同步", "助手没在跑桥接服务, 无法下发同步指令。")
            return
        code = ("(function(){try{if(!window.__sgsResSwap||!window.__sgsResSwap.pull)"
                "return 'NO_SCRIPT';window.__sgsResSwap.pull();return 'PULL_OK';}"
                "catch(e){return 'ERR:'+e.message;}})()")
        read_code = ("(function(){try{var s=window.__sgsResSwap.state;"
                     "return JSON.stringify({on:!!s.enabled,n:(s.rules||[]).length});}"
                     "catch(e){return 'ERR:'+e.message;}})()")
        try:
            cid = st.send_cmd("js", code=code)
        except Exception as e:
            self._status("下发同步指令失败: %s" % e)
            return
        self._status("已通知浏览器立刻重拉规则, 等页面回话…")
        base = self._base_url()

        def _work():
            res = self._wait_cmd(st, cid, 6.0)
            if res is None:
                self.root.after(0, self._sync_report, "NO_ANSWER", "", base)
                return
            if str(res).startswith("NO_SCRIPT"):
                self.root.after(0, self._sync_report, "NO_SCRIPT", "", base)
                return
            if str(res).startswith("ERR:"):
                self.root.after(0, self._sync_report, "ERR", str(res), base)
                return
            # 拉取是异步的, 睡一下再回读"现在生效几条"
            import time as _t
            _t.sleep(1.2)
            cid2 = st.send_cmd("js", code=read_code)
            self.root.after(0, self._sync_report, "OK", self._wait_cmd(st, cid2, 6.0) or "", base)
        threading.Thread(target=_work, daemon=True).start()

    @staticmethod
    def _wait_cmd(st, cid, timeout):
        """等脚本回传执行结果(轮询 take_result) —— 超时返回 None。"""
        import time as _t
        t0 = _t.time()
        while _t.time() - t0 < timeout:
            try:
                r = st.take_result(cid)
            except Exception:
                return None
            if r is not None:
                return r
            _t.sleep(0.15)
        return None

    def _sync_report(self, status, payload, base):
        if status == "NO_SCRIPT":
            self._status("浏览器那边没有换肤模块(window.__sgsResSwap 不存在) —— "
                         "点「换肤脚本安装」把 bridge/sgs_bridge.user.js 装上/更新一下再点我。")
            messagebox.showinfo(
                "同步到浏览器",
                "游戏页面里没有换肤模块。\n\n"
                "原因一般是: ① 脚本还没装/没更新(点上面的「换肤脚本安装」); "
                "② 游戏页没开着(同步是发给正在运行的页面, 关着就没人接)。\n\n"
                "规则本身已经生成好了, 装好脚本后会自动拉取。")
        elif status == "NO_ANSWER":
            self._status("浏览器没回话(游戏页可能没开着, 或脚本是旧版) —— 规则文件已生成, "
                         "页面开着的话脚本 15 秒内会自己拉到。")
        elif status == "ERR":
            self._status("页面执行出错: %s" % payload)
        else:
            # v4.110★ 浏览器真回话"拉到了" → 给所有规则盖上 synced_at, 并立刻刷新表格状态列。
            #   只有这一处能盖: 没回话(NO_ANSWER/NO_SCRIPT/ERR)就不能算生效, 否则状态是假的。
            try:
                n = RR.mark_synced()
                self._load_rules()
            except Exception:
                n = 0
            self._status("同步完成: 已给 %d 条规则盖上『已生效』。浏览器当前状态 %s(换肤角标在"
                         "游戏页右下角, 单击=暂停/恢复)。规则地址 %s/rules"
                         % (n, payload or "(没回读到)", base or ""))

    def _use_candidate(self):
        """把候选那一行配成"可替换资源" —— 选一张本地图, 生成一条自定义规则。

        v4.74★ 两处修正:
          ① 匹配串改用候选里的【相对串】(pattern), 不再把整条 URL 去掉 http:// 拿去当匹配串 ——
             带域名的匹配串只有那一条线路能命中(棋子/大图走 x/pc, 主公走 10/pc), 是"配了不生效"
             的经典来源(见 RR.relative_pattern 的说明);
          ② 具体的建规则动作抽到 _add_rule_for_pattern, 让官方大图清单那一块共用同一套逻辑。
        """
        c = self._selected_candidate()
        if not c:
            messagebox.showinfo("用作背景", "请先在下方候选 URL 里双击或选中一行。")
            return
        pat = str(c.get("pattern") or c.get("url") or "")
        self._add_rule_for_pattern(pat, kind=str(c.get("kind") or "背景"),
                                   name_hint=os.path.basename(pat).split("?")[0])

    def _add_rule_for_pattern(self, pattern, kind="背景", name_hint="", target=None):
        """[匹配串 → 本地图片] 落成一条自定义规则(没给 target 就弹框让用户选一张图)。

        pattern 一律收口成【不含域名的相对串】: 同一条规则在 x/pc 与 10/pc 两条线路上都能命中。
        """
        pat = RR.relative_pattern(str(pattern or "").strip())
        if not pat:
            messagebox.showinfo("配置替换", "这一行拿不到可用的匹配串, 换一行试试。")
            return
        tgt = str(target or "").strip()
        if not tgt:
            # v4.86★ 换的是音效(mp3/ogg/m4a)就让你选音频, 不然一路只给图片后缀 ——
            #   语音/音效那条路以前就是卡在这一步上(扫得到、建得了, 却选不进文件)。
            audio = RR.is_audio_pattern(pat)
            tgt = filedialog.askopenfilename(
                title=("选择要替换成的音频(要换掉的音效: %s)" % pat) if audio
                      else ("选择要替换成的图片(要换的资源: %s)" % pat),
                filetypes=[(("音频", "*.mp3 *.ogg *.oga *.m4a *.wav") if audio
                            else ("图片", "*.png *.jpg *.jpeg *.webp *.gif")),
                           ("所有文件", "*.*")],
                initialdir=RR.IMG_DIR if os.path.exists(RR.IMG_DIR) else os.path.expanduser("~")) or ""
        if not tgt:
            return
        if RR.is_audio_pattern(pat) and not kind:
            kind = "音效"
        rule = {
            "id": "r%d" % int(time.time() * 1000),
            "name": str(name_hint or "").strip() or os.path.basename(pat),
            "kind": str(kind or "背景"),
            "pattern": pat,
            "target": tgt,
            "enabled": True,
            "season": self._season() or "",
        }
        rules = RR.load_rules()
        old = [x for x in rules if str(x.get("pattern")) == pat]
        rules = [x for x in rules if str(x.get("pattern")) != pat]
        rules.append(rule)
        RR.save_rules(rules)
        self._load_rules()
        self._status("已配好替换 %s: %s → %s%s"
                     % ("(覆盖了原来的同名规则)" if old else "", pat,
                        os.path.basename(tgt), "  ← 记得点「立即同步到浏览器」" ))

    # ---------- v4.74★ 官方大图 / 皮肤资源清单 ----------
    def _load_official_res(self):
        """后台读 data/official_resources.json → 填表(启动时、扫描/探测/删除后都刷新)。"""
        def _work():
            try:
                items = RR.load_official_resources()
                self.root.after(0, self._fill_official_res, items)
            except Exception as e:
                self.root.after(0, self._status, "读官方资源清单失败: %s" % e)
        threading.Thread(target=_work, daemon=True).start()

    def _fill_official_res(self, items):
        self._official_items = list(items or [])
        self.big_tree.delete(*self.big_tree.get_children())
        for i, it in enumerate(self._official_items):
            size = ""
            if it.get("w") and it.get("h"):
                size = "%d×%d" % (int(it["w"]), int(it["h"]))
            if it.get("size"):
                size = (size + " " if size else "") + "%.0f KB" % (int(it["size"]) / 1024.0)
            src = {"scan": "扫描", "probe": "编号探测", "manual": "手动",
                   "preset": "内置样例"}.get(str(it.get("src") or ""), str(it.get("src") or ""))
            if int(it.get("seen") or 0) > 1:
                src += "(见过%d次)" % int(it.get("seen"))
            self.big_tree.insert("", "end", iid="b%d" % i,
                                 values=(it.get("kind") or "", it.get("pattern") or "", size, src))

    def _selected_official(self):
        """选中的官方资源 → dict(含 pattern/url/kind/尺寸)。"""
        sel = self.big_tree.selection()
        if not sel:
            return None
        try:
            i = int(str(sel[0])[1:])
        except Exception:
            return None
        if 0 <= i < len(self._official_items):
            return self._official_items[i]
        return None

    def _scan_official_res(self):
        """让浏览器把"真的加载过哪些资源"交回来, 并进清单。

        走的是脚本里的 window.__sgsResSwap.dumpRes()(v4.74 新增)。为什么这才是"获取"的正路:
          编号不是连号(500103 = 刘备皮肤 / 621401 = 神马超 / 821401 是它的动态版), 盲枚举要发
          成千上万个请求; 而这里是浏览器【已经替你请求过】的地址 —— 零猜测。
        用法: 在游戏里把皮肤/武将详情点开逛一圈(那些大图才会被加载), 再回来点这个按钮。
        """
        try:
            from core import bridge as _BR
            st = getattr(_BR, "STATE", None)
        except Exception as e:
            messagebox.showinfo("扫描游戏资源", "读不到桥接模块(core/bridge.py): %s" % e)
            return
        if st is None:
            messagebox.showinfo("扫描游戏资源", "助手没在跑桥接服务 —— 先让助手跑起来, 并开着游戏页。")
            return
        code = ("(function(){try{if(!window.__sgsResSwap||!window.__sgsResSwap.dumpRes)"
                "return 'NO_SCRIPT';return JSON.stringify(window.__sgsResSwap.dumpRes());}"
                "catch(e){return 'ERR:'+e.message;}})()")
        try:
            cid = st.send_cmd("js", code=code)
        except Exception as e:
            self._status("下发扫描指令失败: %s" % e)
            return
        self._status("已让浏览器交出加载过的资源地址, 等回话…")

        def _work():
            res = self._wait_cmd(st, cid, 10.0)
            if res is None:
                self.root.after(0, self._scan_report, -1, 0)
                return
            s = str(res)
            if s.startswith("NO_SCRIPT"):
                self.root.after(0, self._scan_report, -2, 0)
                return
            if s.startswith("ERR:"):
                self.root.after(0, self._status, "页面执行出错: %s" % s)
                return
            try:
                obj = json.loads(s)
            except Exception:
                obj = []
            # 脚本 v4.86 起交回来的是 {urls:[...], diag:{...}}; 旧脚本交的是裸数组 —— 两种都认
            if isinstance(obj, dict):
                arr = obj.get("urls") or []
                diag = obj.get("diag") or {}
            else:
                arr = obj if isinstance(obj, list) else []
                diag = {}
            if not isinstance(arr, list):
                arr = []
            added = 0
            try:
                added = int(RR.merge_official_resources(arr, source="scan").get("added") or 0)
            except Exception as e:
                self.root.after(0, self._status, "存清单失败: %s" % e)
            self.root.after(0, self._scan_report, len(arr), added, diag)
        threading.Thread(target=_work, daemon=True).start()

    def _scan_report(self, n_seen, n_added, diag=None):
        """扫描结果 —— v4.86★ 带上诊断: 一条都没采到时, 说清到底是哪一路空。

        用户原话"我加载过很多次, 但是都没有加载到": 光说"没有记录"等于没说, 得告诉他
          是【指令落到了没有游戏的 frame】(hasLaya=假)、还是【performance 缓冲被丢光】
          (perfEntries 很少)、还是【真没逛过那些界面】(各路都是 0 但 Laya 在)。
        """
        d = diag or {}
        if n_seen == -1:
            self._status("浏览器没回话(游戏页没开着?) —— 扫描是发给正在运行的页面的, 关着就没人接。")
            return
        if n_seen == -2:
            self._status("页面里没有换肤模块(window.__sgsResSwap 不存在) —— 先点「换肤脚本安装」"
                         "把它更新到 v4.86, 刷新游戏页再点我。")
            return
        self._load_official_res()
        # v4.86★ 一条都没采到时, 先排除最常见的原因: 【脚本没更新】。
        #   旧脚本根本不会带诊断信息回来(那批"只收 performance 250 条"的逻辑还在),
        #   与其让用户去猜"是不是我没逛够界面", 不如直接把这一步点破。
        if n_seen == 0 and not d:
            self._status("扫描回来了, 但一条都没有, 而且没带诊断信息回来 —— 说明游戏页里的脚本还是旧的"
                         "(诊断是 v4.86 才加的): 点「换肤脚本安装」更新脚本 → 刷新游戏页 → 再点扫描。")
            return
        if n_seen == 0:
            ver = str(d.get("ver") or "")
            try:
                vt = tuple(int(x) for x in ver.split(".")) if ver else (0,)
            except Exception:
                vt = (0,)
            if ver and vt < (4, 8, 6):
                self._status("扫描回来了, 但一条都没有 —— 游戏页里的脚本是 v%s, 而采集面(performance 缓冲/"
                             "Laya 缓存/iframe)是 v4.86 才加的: 点「换肤脚本安装」更新脚本、刷新游戏页再扫。"
                             % ver)
                return
        if n_seen == 0:
            if d:
                why = ("页面里%s游戏(Laya%s) / performance 记录 %s 条 / 这一层 iframe %s 个 / "
                       "各路收到 performance %s · Laya缓存 %s · 请求hook %s"
                       % ("有" if d.get("hasLaya") else "没有", "在" if d.get("hasLaya") else "不在",
                          d.get("perfEntries"), d.get("iframes"),
                          d.get("fromPerf"), d.get("fromLaya"), d.get("fromHook")))
                if not d.get("hasLaya"):
                    why += (" —— 说明这条指令落到了【没有游戏的那个 frame】(游戏跑在 iframe 里时"
                            "常见): 把游戏窗口切到前台再扫一次, 或刷新游戏页让脚本重新接管")
                else:
                    why += " —— 在游戏里把皮肤/武将详情、棋盘对局逛一圈再来扫(那些大图才会被加载)"
            else:
                why = "在游戏里把皮肤/武将详情点开逛一圈再来扫(那些大图才会被加载)"
            self._status("扫描回来了, 但一条 res/assets 都没有: %s" % why)
            return
        tail = ""
        if d:
            tail = " (来源: performance %s / Laya缓存 %s / 请求hook %s)" % (
                d.get("fromPerf", "?"), d.get("fromLaya", "?"), d.get("fromHook", "?"))
        self._status("扫描完成: 页面加载过 %d 条资源地址, 其中 %d 条是新的(已进清单)%s"
                     % (n_seen, n_added, tail))

    def _bg_bundle(self):
        """v4.86★ 一张图铺满自走棋所有背景 —— 用户原话: "能不能改成全部?"。

        为什么必须成套(源码 TavernChessGameScene):
            InHoliday ? "tavernChessHolidayBg" : "tavernChessBg"
          只换一张 = "有时候换成了、有时候又变回官方图"(节日活动期走的是另一张, 压根没被换)。
          所以这里默认把棋盘 + 节日两张都勾上, 大厅/选将/棋盘区底看你要不要一起换。
        """
        # v4.137★ 加进新版选人页那两张(newStyleTableScene/), 以前名单里没有 → 无处可换
        presets = (list(getattr(RR, "TAVERN_BG_PRESETS", ()) or ())
                   + list(getattr(RR, "NEWSTYLE_BG_PRESETS", ()) or ()))
        ident = list(getattr(RR, "IDENT_BG_PRESETS", ()) or ())   # v4.86★ 身份场背景(battleBg/)
        if not presets and not ident:
            messagebox.showinfo("背景一键套装", "资源模块里没找到背景预设表(core/resource_rules.py)。")
            return
        top = tk.Toplevel(self.root)
        top.title("背景一键套装(自走棋 + 身份场)")
        top.configure(bg=self._bg())
        top.transient(self.root)
        tk.Label(top, text=("选图, 一次把它们铺到勾上的那些背景上 —— 两个按钮都能多选:\n"
                            "  只选 1 张 → 勾上的背景全用这张; 选多张 → 按列表顺序一张一背景(不够就循环)。\n"
                            "图可以是本地文件, 也可以点「从皮肤表挑(可多选) …」直接用官方大图(不用下载)。\n"
                            "自走棋棋盘那张原图 2134×1200(≈16:9), 身份场那张 1600×900 —— 比例差太多\n"
                            "会被拉伸变形, 配好后到「资源预览」页点「按原图裁剪」就行。\n"
                            "★ 身份场(battleBg/)是【另一张桌子】的背景, 不打自走棋也照样能换。"),
                 bg=self._bg(), fg=self._fg(), font=(self._fh(), 9),
                 justify="left").pack(anchor="w", padx=10, pady=(10, 6))
        box = tk.Frame(top, bg=self._bg())
        box.pack(fill="x", padx=14)
        picks = []

        def _group(title, items, default_on, fg):
            tk.Label(box, text=title, bg=self._bg(), fg=fg, font=(self._fh(), 9, "bold"),
                     anchor="w").pack(fill="x", anchor="w", pady=(4, 2))
            for pat, note, _size in items:
                v = tk.BooleanVar(value=default_on)
                picks.append((pat, v))
                tk.Checkbutton(box, text="%s —— %s" % (pat.rsplit("/", 1)[-1], note),
                               variable=v, bg=self._bg(), fg=self._fg(),
                               selectcolor=self._bg(), activebackground=self._bg(),
                               activeforeground=self._gold(), font=(self._fh(), 9),
                               anchor="w").pack(fill="x", anchor="w")

        if presets:
            _group("自走棋(默认全勾)", presets, True, self._gold())
        if ident:
            _group("身份场(默认不勾 —— 换的是另一张桌子)", ident, False, self._blue())

        got = {"imgs": []}          # [{"v": 本地路径 / 官方URL, "label": 显示名}]
        row = tk.Frame(top, bg=self._bg())
        row.pack(fill="x", padx=10, pady=(10, 4))
        lbl = tk.Label(row, text="(还没选图)", bg=self._bg(), fg=self._dim(),
                       font=(self._fh(), 9))
        lbl.pack(side="left")

        def _refill_lbl():
            fs = got["imgs"]
            if not fs:
                lbl.configure(text="(还没选图)", fg=self._dim())
                return
            names = [f["label"] for f in fs]
            s = "、".join(names)
            if len(s) > 44:
                s = s[:41] + "…"
            lbl.configure(text=("%d 张: %s" % (len(fs), s)) if len(fs) > 1 else s,
                          fg=self._gold())

        def _push(v, label):
            """v4.110★ 收一张图(本地文件 / 官方 URL 都行, 可混着来), 重复的跳过。"""
            for f in got["imgs"]:
                if f["v"] == v:
                    return 0
            got["imgs"].append({"v": v, "label": label})
            _refill_lbl()
            return 1

        def _clear():
            got["imgs"] = []
            _refill_lbl()

        def _pick():
            """v4.110★ 本地图也改成可多选(askopenfilenames) —— 用户原话: "这个也改成可以多选"。"""
            ps = filedialog.askopenfilenames(
                title="挑图(可一次选多张) —— 多张时按顺序分给勾上的背景",
                filetypes=[("图片", "*.png *.jpg *.jpeg *.webp *.gif"), ("所有文件", "*.*")],
                initialdir=RR.IMG_DIR if os.path.exists(RR.IMG_DIR) else os.path.expanduser("~"))
            for p in (ps or ()):
                if p:
                    _push(p, os.path.basename(p))

        def _pick_dress():
            """v4.110★ 从皮肤表挑【官方图】当背景 —— 用户原话: "要" + "这个也改成可以多选"。

            以前这个弹窗只能选一张本地文件, 而用户那套"管宁·墨韵荷香"背景是官方 URL
            (static/6195001.png) —— 想换另一张官方图只能去规则表里把 5 条的 target
            一条条改。现在挑几张就铺满勾上的那些背景, 一张图都不用下载。
            """
            def _one(it):
                _many([it])

            def _many(items):
                n = 0
                for it in (items or []):
                    pat = str(it.get("pattern") or "").strip()
                    if not pat:
                        continue
                    full = str(it.get("url") or "").strip() or RR.res_full_url(pat)
                    if not full:
                        continue
                    nm = str(it.get("name") or "").strip()
                    if it.get("frame"):                 # 动态帧: 名字后面标出是哪一帧
                        nm = "%s·%s" % (nm, it["frame"]) if nm else str(it["frame"])
                    tail = full.rsplit("/", 1)[-1]
                    n += _push(full, "%s(%s)" % (tail, nm) if nm else tail)
                if not n:
                    messagebox.showinfo(
                        "从皮肤表挑",
                        "没拿到官方地址 —— 在皮肤表里选一套后要先点「探测大图」:\n"
                        "探测 = 问官方服务器这几张到底在不在, 探出来的那些行才带地址。", parent=top)
            self._dress_picker(top, _one, on_multi=_many,
                               multi_label="把选中的 %d 张都收下")

        # side="right" 先 pack 的更靠右: 本地图放最右(最常用), 皮肤表在它左边
        tk.Button(row, text="选本地图(可多选) …", command=_pick, bg=self._btn(), fg=self._green(),
                  font=(self._fh(), 9), padx=8, cursor="hand2").pack(side="right")
        tk.Button(row, text="从皮肤表挑(可多选) …", command=_pick_dress, bg=self._btn(), fg=self._gold(),
                  font=(self._fh(), 9), padx=8, cursor="hand2").pack(side="right", padx=(0, 6))
        tk.Button(row, text="清空", command=_clear, bg=self._btn(), fg=self._dim(),
                  font=(self._fh(), 9), padx=6, cursor="hand2").pack(side="right", padx=(0, 6))

        btns = tk.Frame(top, bg=self._bg())
        btns.pack(fill="x", padx=10, pady=(6, 10))

        def _ok():
            imgs = [f["v"] for f in got["imgs"]]
            if not imgs:
                messagebox.showinfo("背景一键套装", "先选图 —— 本地文件或官方图都行。", parent=top)
                return
            chosen = [p for p, v in picks if v.get()]
            if not chosen:
                messagebox.showinfo("背景一键套装", "至少勾一张背景。", parent=top)
                return
            # v4.110★ 多张图按顺序分给勾上的背景(图不够就从头循环); 只选 1 张时全部用它
            now = int(time.time() * 1000)
            new = []
            for i, pat in enumerate(chosen):
                tgt = imgs[i % len(imgs)]
                fname = pat.rsplit("/", 1)[-1]
                is_tavern = "tavernChess" in pat
                base = "%s-%s" % ("自走棋背景" if is_tavern else "身份场背景",
                                  fname.rsplit(".", 1)[0])
                if len(imgs) > 1:        # 多张图时把"用的哪张"标进名字, 回头好认
                    base += "-%s" % os.path.basename(tgt).rsplit(".", 1)[0][:12]
                new.append({"id": "bgb%d_%d" % (now, i),
                            "name": base,
                            "kind": "自走棋背景" if is_tavern else "背景",
                            "pattern": RR.relative_pattern(pat),
                            "target": tgt, "enabled": True,
                            "season": self._season() or ""})
            total, added, covered = RR.upsert_rules(new)
            self._load_rules()
            show = "、".join([f["label"] for f in got["imgs"]])
            if len(show) > 40:
                show = show[:37] + "…"
            self._status("背景一键套装: 新建 %d 条 / 覆盖 %d 条 → 「%s」(共 %d 条规则) —— "
                         "记得点「立即同步到浏览器」" % (added, covered, show, total))
            top.destroy()

        tk.Button(btns, text="取消", command=top.destroy, bg=self._btn(), fg=self._fg(),
                  font=(self._fh(), 9), padx=10, cursor="hand2").pack(side="right")
        tk.Button(btns, text="一键配上", command=_ok, bg=self._act(), fg=self._gold(),
                  font=(self._fh(), 9, "bold"), padx=10, cursor="hand2").pack(side="right", padx=(0, 6))

    def _dress_bundle(self):
        """v4.86★ OL 大图 / 动态皮肤一键配 —— 用户原话: "动态皮肤按帧管理…但是我不会用啊"。

        手把手三步: 搜武将名 → 点「探测大图」(真问官方哪几帧存在) → 想换几帧就选几帧
          (点一下选中、再点一下取消, 可多选) → 点「把选中的帧都配上 …」。
        为什么要有这个按钮: 动态皮肤是【一帧一个文件】(beijing/daiji/daiji_2 …),
          换整段动画本来就得一帧一条规则; 以前只能一帧一帧地配, 中间还要自己抄匹配串,
          所以那条路实际上没人走通。
        """
        def _one(it):
            self._add_rule_for_pattern(it.get("pattern"), kind="OL大图",
                                       name_hint=str(it.get("name") or ""))

        def _many(items):
            if not items:
                return
            tgt = filedialog.askopenfilename(
                title="选择要换成的大图(选中的 %d 帧都用它)" % len(items),
                filetypes=[("图片", "*.png *.jpg *.jpeg *.webp *.gif"), ("所有文件", "*.*")],
                initialdir=RR.IMG_DIR if os.path.exists(RR.IMG_DIR) else os.path.expanduser("~")) or ""
            if not tgt:
                return
            now = int(time.time() * 1000)
            new = []
            for i, it in enumerate(items):
                pat = str(it.get("pattern") or "").strip()
                if not pat:
                    continue
                new.append({"id": "drs%d_%d" % (now, i),
                            "name": "%s(%s)" % (str(it.get("name") or "OL大图").strip(),
                                                "动态帧" if it.get("dynamic") else "静态"),
                            "kind": "OL大图",
                            "pattern": RR.relative_pattern(pat),
                            "target": tgt, "enabled": True,
                            "season": self._season() or ""})
            if not new:
                return
            total, added, covered = RR.upsert_rules(new)
            self._load_rules()
            self._status("已配 %d 帧(新建 %d / 覆盖 %d) → 「%s」—— 动态皮肤一帧一个文件, "
                         "换整段动画就是一帧一条规则; 现在共 %d 条规则, 记得点「立即同步到浏览器」"
                         % (len(new), added, covered, os.path.basename(tgt), total))

        self._dress_picker(self.root, _one, on_multi=_many)

    def _goto_dynskin(self):
        """切到『换肤』页的「动态皮肤」子页(动态皮整套替换在那儿)。

        v4.139★ 原来直接 select(app.tab_dynskin) —— 动态皮肤和即时换肤并成一个『换肤』
          页之后, 那个顶层 Tab 没了, 得跳到它的子页签上, 否则点过来只会停在子页的
          第一页(恰好也是动态皮肤, 但换个顺序就跳错)。
        """
        try:
            _goto = getattr(self.app, "_goto_skin_sub", None)
            if _goto and _goto("动态皮肤"):
                return
            self.app.notebook.select(getattr(self.app, "tab_skin", None)
                                     or self.app.tab_dynskin)
        except Exception as e:      # noqa: BLE001 —— 缺这个 tab 时说一句, 不弹框打断
            print("[动态皮肤] 切页失败: %s" % e, file=sys.stderr)
            try:
                self._status("切到『动态皮肤』页失败: %s" % e)
            except Exception:       # noqa: BLE001 —— 连状态栏都没有就到此为止, 别二次炸
                print("[动态皮肤] 状态栏不可用", file=sys.stderr)

    # ---------- 图集(atlas): 一整张图集里取某一块 (v4.87★) ----------
    def _atlas_tool(self):
        """v4.87★ 图集取图 / 贴图 —— 用户原话:

            "自走棋图集, 好像是一个剪辑图, 里面有很多元素, 但是放的时候放一部分,
             取一部分就是一个图; 里面有很多"

        官方一大堆界面资源是【图集】: `xxx.atlas`(清单) + `xxx.png`(一整张剪贴画) 永远
        成对出现。png 那张里面挤着几十上百个小元素(底框 / 图标 / 角标 / 进度条…), 游戏
        按清单里的矩形【取一块】画出来 —— 所以:

          · 想拿其中某个元素当素材  → 「裁这块」存成一张独立 png;
          · 想只换掉其中一个元素    → 「把我的图贴到这里」导出一张【新的整图】, 再给整图建
            规则。注意: 游戏加载的是整张 png, 从不单独请求某个元素 —— 所以"换图集里的
            一个元素"唯一的办法就是换掉整张、把你那块贴进它原来的位置。

        没有 .atlas 清单也能用: 在左边图上【按住左键拖一个框】就是区域(坐标框也可手填)。
        """
        from core import atlas as AT
        from core import resource_rules as RR
        try:
            from PIL import Image, ImageTk
        except Exception as e:                # 没 Pillow 就别开半个窗口出来
            messagebox.showerror("图集取图", "缺少 Pillow(pip install pillow), 读不了 png: %s" % e)
            return

        CV_W, CV_H = 640, 430
        top = tk.Toplevel(self.root)
        top.title("图集取图 / 贴图(一整张图集里取某一块)")
        top.configure(bg=self._bg())
        top.transient(self.root)
        top.grab_set()

        st = {"png": "", "frames": [], "shown": [], "im": None, "photo": None,
              "scale": 1.0, "pattern": "", "name": ""}
        drag = {"on": False, "x0": 0, "y0": 0}

        def _safe(s):
            return "".join(ch if ch not in '\\/:*?"<>|' else "_" for ch in str(s or ""))[:40]

        # ---- 顶栏: 图集从哪来
        head = tk.Frame(top, bg=self._bg())
        head.pack(fill="x", padx=8, pady=(8, 4))
        path_var = tk.StringVar(value="(还没选图集 —— 点右边两个按钮之一)")
        tk.Label(head, textvariable=path_var, bg=self._bg(), fg=self._gold(),
                 font=(self._fh(), 9), anchor="w").pack(side="left", fill="x", expand=True)
        tk.Button(head, text="用已发现的图集 …", command=lambda: _pick_known(),
                  bg=self._act(), fg=self._blue(), font=(self._fh(), 9, "bold"),
                  padx=8, cursor="hand2").pack(side="right")
        tk.Button(head, text="打开本地图集 png …", command=lambda: _open_local(),
                  bg=self._act(), fg=self._gold(), font=(self._fh(), 9, "bold"),
                  padx=8, cursor="hand2").pack(side="right", padx=(0, 6))

        info = tk.Label(top, bg=self._bg(), fg=self._dim(), font=(self._fh(), 8),
                        justify="left", anchor="w", wraplength=960)
        info.pack(fill="x", padx=8)
        info.configure(text="图集 = 一整张 png(里面挤着很多元素) + 一份 .atlas 清单"
                            "(每个元素在哪块矩形)。选好图集后: 右边点元素名 → 左边框出那一块。")

        # ---- 中间: 左画布 / 右元素列表
        mid = tk.Frame(top, bg=self._bg())
        mid.pack(fill="both", expand=True, padx=8, pady=6)
        cv = tk.Canvas(mid, width=CV_W, height=CV_H, bg="#1b1b1b", cursor="crosshair",
                       highlightthickness=1, highlightbackground=self._dim())
        cv.pack(side="left")
        right = tk.Frame(mid, bg=self._bg())
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))
        tk.Label(right, text="图集里的元素(点一下选中):", bg=self._bg(), fg=self._fg(),
                 font=(self._fh(), 9)).pack(anchor="w")
        kw_var = tk.StringVar(value="")
        tk.Entry(right, textvariable=kw_var, width=28, bg=self._btn(), fg=self._fg(),
                 insertbackground=self._fg(), font=(self._fh(), 9)).pack(fill="x", pady=(0, 4))
        wrap = tk.Frame(right, bg=self._bg())
        wrap.pack(fill="both", expand=True)
        sbv = tk.Scrollbar(wrap)
        sbv.pack(side="right", fill="y")
        sbh = tk.Scrollbar(wrap, orient="horizontal")
        sbh.pack(side="bottom", fill="x")
        lb = tk.Listbox(wrap, bg=self._btn(), fg=self._fg(), font=(self._fh(), 9),
                        selectmode="browse", activestyle="none", exportselection=False,
                        yscrollcommand=sbv.set, xscrollcommand=sbh.set)
        sbv.configure(command=lb.yview)
        sbh.configure(command=lb.xview)
        lb.pack(side="left", fill="both", expand=True)

        # ---- 底部: 区域坐标 + 按钮
        bot = tk.Frame(top, bg=self._bg())
        bot.pack(fill="x", padx=8)
        vxy = [tk.IntVar(value=0) for _ in range(4)]
        for name, var in zip(("x", "y", "宽", "高"), vxy):
            tk.Label(bot, text=name, bg=self._bg(), fg=self._fg(),
                     font=(self._fh(), 9)).pack(side="left")
            tk.Spinbox(bot, from_=0, to=99999, width=6, textvariable=var,
                       command=lambda: _draw(), font=(self._fh(), 9)).pack(side="left", padx=(0, 8))
        rot_var = tk.BooleanVar(value=False)
        tk.Checkbutton(bot, text="这一块在图集里是转过 90° 存的(裁出来自动摆正)",
                       variable=rot_var, bg=self._bg(), fg=self._fg(),
                       selectcolor=self._btn(), activebackground=self._bg(),
                       font=(self._fh(), 8)).pack(side="left")

        btns = tk.Frame(top, bg=self._bg())
        btns.pack(pady=8)
        tk.Button(btns, text="裁这块 → 存成 png", command=lambda: _crop_save(),
                  bg=self._act(), fg=self._gold(), font=(self._fh(), 9, "bold"),
                  padx=10, cursor="hand2").pack(side="left", padx=6)
        tk.Button(btns, text="把我的图贴到这里 → 生成新整图", command=lambda: _paste_save(),
                  bg=self._act(), fg=self._green(), font=(self._fh(), 9, "bold"),
                  padx=10, cursor="hand2").pack(side="left", padx=6)
        tk.Button(btns, text="关闭", command=top.destroy, bg=self._btn(), fg=self._fg(),
                  font=(self._fh(), 9), padx=12, cursor="hand2").pack(side="left", padx=6)

        # ---- 内部函数(都在上面那些控件之后才可能被点到, 所以引用它们是安全的)
        def _say(msg, color=None):
            info.configure(text=msg, fg=color or self._dim())

        def _cur_rect():
            try:
                r = tuple(int(v.get()) for v in vxy)
            except Exception:
                return None
            return r if (r[2] > 0 and r[3] > 0) else None

        def _set_rect(r):
            for v, val in zip(vxy, r):
                v.set(int(val))
            _draw()

        def _draw():
            cv.delete("all")
            if st["photo"] is not None:
                cv.create_image(0, 0, anchor="nw", image=st["photo"])
            r = _cur_rect()
            if r and st["im"] is not None:
                s = st["scale"] or 1.0
                cv.create_rectangle(r[0] * s, r[1] * s, (r[0] + r[2]) * s, (r[1] + r[3]) * s,
                                    outline=self._gold(), width=2)

        def _load_png(path, pattern="", name=""):
            try:
                im = Image.open(path)
                im.load()
            except Exception as e:
                _say("这张打不开(%s): %s" % (type(e).__name__, e), self._red())
                return False
            st["im"] = im.convert("RGBA")
            st["png"] = path
            st["pattern"] = pattern
            st["name"] = name or os.path.basename(path)
            sc = min(CV_W / max(1, im.size[0]), CV_H / max(1, im.size[1]), 1.0)
            st["scale"] = sc
            disp = st["im"]
            if sc < 1.0:
                disp = disp.resize((max(1, int(im.size[0] * sc)), max(1, int(im.size[1] * sc))),
                                   Image.LANCZOS)
            st["photo"] = ImageTk.PhotoImage(disp)     # 引用必须留着, 否则画布变空白
            path_var.set("%s  (%dx%d)" % (path, im.size[0], im.size[1]))
            _draw()
            return True

        def _refill(*_a):
            kw = kw_var.get().strip().lower()
            st["shown"] = [f for f in st["frames"]
                           if (not kw) or kw in str(f.get("name") or "").lower()]
            lb.delete(0, "end")
            for f in st["shown"][:1000]:
                lb.insert("end", AT.frame_label(f))

        def _load_atlas(path):
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    parsed = AT.parse_atlas(f.read())
            except Exception as e:
                _say("清单读不出来(%s): %s —— 没关系, 直接在左边图上拖个框也能取。"
                     % (type(e).__name__, e), self._gold())
                return
            st["frames"] = parsed.get("frames") or []
            _refill()
            _say("清单里有 %d 个元素 —— 右边点一个, 左边就框出那一块; 没有名字可搜就搜关键字。"
                 % len(st["frames"]), self._blue())

        def _on_pick(*_a):
            sel = lb.curselection()
            if not sel or not st["shown"]:
                return
            f = st["shown"][sel[0]]
            r = AT.rect_of(f)
            if not r:
                _say("这个元素清单里没写 xy/size —— 自己在左边图上拖个框。", self._gold())
                return
            _set_rect(r)
            rot_var.set(str(f.get("rotate") or "").strip().lower() == "true")
            _say("选中「%s」  %dx%d @%d,%d" % (f.get("name"), r[2], r[3], r[0], r[1]),
                 self._blue())

        def _rect_from_drag(e):
            s = st["scale"] or 1.0
            x0, x1 = sorted((drag["x0"], e.x))
            y0, y1 = sorted((drag["y0"], e.y))
            return (int(x0 / s), int(y0 / s),
                    max(1, int((x1 - x0) / s)), max(1, int((y1 - y0) / s)))

        def _down(e):
            if st["im"] is None:
                return
            drag.update({"on": True, "x0": e.x, "y0": e.y})

        def _move(e):
            if not drag["on"]:
                return
            r = _rect_from_drag(e)
            for v, val in zip(vxy, r):
                v.set(val)
            _draw()

        def _up(e):
            if not drag["on"]:
                return
            drag["on"] = False
            r = _rect_from_drag(e)
            _set_rect(r)
            _say("已框选 %dx%d @%d,%d —— 可以手改上面的数字微调。"
                 % (r[2], r[3], r[0], r[1]), self._blue())

        def _open_local():
            p = filedialog.askopenfilename(
                title="选择图集整图(png)", parent=top,
                filetypes=[("图片", "*.png *.jpg *.jpeg *.webp"), ("所有文件", "*.*")],
                initialdir=RR.IMG_DIR if os.path.exists(RR.IMG_DIR)
                else os.path.expanduser("~")) or ""
            if not p:
                return
            if not _load_png(p):
                return
            st["frames"] = []
            st["shown"] = []
            lb.delete(0, "end")
            side = AT.side_files(p)
            if side:
                _load_atlas(side)
            else:
                _say("这张图旁边没有同名的 .atlas 清单 —— 不影响: 在左边图上【按住左键拖一个框】"
                     "就是你要取的那一块(上面 x/y/宽/高 也能手填)。", self._gold())

        def _pick_known():
            try:
                items = AT.discover_atlases()
            except Exception as e:
                messagebox.showerror("图集取图", "读已发现的图集失败: %s" % e, parent=top)
                return
            if not items:
                messagebox.showinfo(
                    "还没有发现过图集",
                    "助手还没见过任何 .atlas 清单。两条路:\n\n"
                    "① 在游戏里逛一遍界面 —— 脚本会记下它加载过的 .atlas, 这里就能列出来;\n"
                    "② 直接用「打开本地图集 png …」, 从游戏资源目录里拿那张整图。", parent=top)
                return
            d = tk.Toplevel(top)
            d.title("选一个图集")
            d.configure(bg=self._bg())
            d.transient(top)
            d.grab_set()
            dlb = tk.Listbox(d, bg=self._btn(), fg=self._fg(), font=(self._fh(), 9),
                             width=76, height=16, selectmode="browse", activestyle="none")
            dlb.pack(fill="both", expand=True, padx=8, pady=8)
            for it in items:
                dlb.insert("end", it["png"])
            dbb = tk.Frame(d, bg=self._bg())
            dbb.pack(pady=(0, 8))
            picked = {"i": None}

            def _go():
                sel = dlb.curselection()
                if not sel:
                    return
                picked["i"] = sel[0]
                d.destroy()

            tk.Button(dbb, text="取消", command=d.destroy, bg=self._btn(), fg=self._fg(),
                      font=(self._fh(), 9), padx=10).pack(side="right", padx=6)
            tk.Button(dbb, text="下载并打开", command=_go, bg=self._act(), fg=self._gold(),
                      font=(self._fh(), 9, "bold"), padx=10).pack(side="right")
            d.wait_window()
            if picked["i"] is None:
                return
            it = items[picked["i"]]
            cache = os.path.join(RR.REF_DIR, "图集缓存")
            png_p = os.path.join(cache, _safe(os.path.basename(it["png"])) or "atlas.png")
            atl_p = os.path.splitext(png_p)[0] + ".atlas"

            def _work():
                try:
                    png_url = it["png"] if "://" in it["png"] else RR.res_full_url(it["png"])
                    atl_url = it["atlas"] if "://" in it["atlas"] else RR.res_full_url(it["atlas"])
                    AT.download(png_url, png_p)
                except Exception as e:
                    self.root.after(0, lambda e=e: _say("图集整图下载失败: %s" % e, self._red()))
                    return
                got_atlas = ""
                try:
                    AT.download(atl_url, atl_p)
                    got_atlas = atl_p
                except Exception:
                    got_atlas = ""

                def _done():
                    if not _load_png(png_p, pattern=RR.relative_pattern(it["png"]),
                                     name=os.path.basename(it["png"])):
                        return
                    st["frames"] = []
                    st["shown"] = []
                    lb.delete(0, "end")
                    if got_atlas:
                        _load_atlas(got_atlas)
                    else:
                        _say("清单(.atlas)没下到 —— 不影响: 在左边图上拖个框就能取块。"
                             "整图已存到 %s" % png_p, self._gold())
                self.root.after(0, _done)

            threading.Thread(target=_work, daemon=True).start()
            _say("正在下载图集整图 + 清单 …", self._gold())

        def _elem_name():
            sel = lb.curselection()
            if sel and st["shown"]:
                return str(st["shown"][sel[0]].get("name") or "")
            return "选区"

        def _crop_save():
            if not st["png"]:
                _say("先选一张图集整图(右上两个按钮)。", self._gold())
                return
            r = _cur_rect()
            if not r:
                _say("先选一块: 右边点元素名, 或在左边图上拖个框。", self._gold())
                return
            out = os.path.join(RR.IMG_DIR, "%s_%s.png"
                               % (_safe(os.path.splitext(st["name"])[0]), _safe(_elem_name())))
            try:
                AT.crop_out(st["png"], r, out, rotate_back=rot_var.get())
            except Exception as e:
                _say("裁不出来(%s): %s" % (type(e).__name__, e), self._red())
                return
            _say("已存: %s  (%dx%d)" % (out, r[2], r[3]), self._green())
            rule, _c = self._selected_rule()
            if rule is not None and messagebox.askyesno(
                    "裁好了",
                    "已存到:\n%s\n\n要给当前选中的那条规则(%s)换成这张吗?"
                    % (out, rule.get("pattern") or ""), parent=top):
                nr = dict(rule)
                nr["target"] = out
                total, _a, _cv = RR.upsert_rules([nr])
                self._load_rules()
                self._status("图集取图: %s 已换成裁出来的那块(共 %d 条规则) —— 记得点"
                             "「立即同步到浏览器」" % (rule.get("pattern") or "", total))

        def _paste_save():
            if not st["png"]:
                _say("先选一张图集整图(右上两个按钮)。", self._gold())
                return
            r = _cur_rect()
            if not r:
                _say("先选一块: 右边点元素名, 或在左边图上拖个框。", self._gold())
                return
            src = filedialog.askopenfilename(
                title="选一张图, 贴到图集的这一块上(%dx%d)" % (r[2], r[3]), parent=top,
                filetypes=[("图片", "*.png *.jpg *.jpeg *.webp"), ("所有文件", "*.*")],
                initialdir=RR.IMG_DIR if os.path.exists(RR.IMG_DIR)
                else os.path.expanduser("~")) or ""
            if not src:
                return
            out = os.path.join(RR.IMG_DIR, "%s_%s_改.png"
                               % (_safe(os.path.splitext(st["name"])[0]), _safe(_elem_name())))
            try:
                AT.paste_out(st["png"], r, src, out, rotate=rot_var.get())
            except Exception as e:
                _say("贴不进去(%s): %s" % (type(e).__name__, e), self._red())
                return
            pat = st["pattern"]
            if pat and messagebox.askyesno(
                    "新整图已生成",
                    "%s\n\n给这张整图建一条替换规则吗?\n匹配串: %s\n\n"
                    "(游戏加载的是整张图集 —— 换整张才生效)"
                    % (out, RR.relative_pattern(pat)), parent=top):
                kind = RR.res_kind(RR.relative_pattern(pat))[0]
                new = [{"id": "atl%d" % int(time.time() * 1000),
                        "name": "%s(图集改一块)" % _safe(os.path.splitext(st["name"])[0]),
                        "kind": kind, "pattern": RR.relative_pattern(pat),
                        "target": out, "enabled": True, "season": self._season() or ""}]
                total, added, covered = RR.upsert_rules(new)
                self._load_rules()
                self._status("图集贴图: 已建规则(新建 %d / 覆盖 %d, 共 %d 条) → %s —— 记得点"
                             "「立即同步到浏览器」" % (added, covered, total, os.path.basename(out)))
            else:
                _say("已生成新整图: %s —— 想让它生效就给这张整图建一条规则。" % out,
                     self._green())

        lb.bind("<<ListboxSelect>>", _on_pick)
        kw_var.trace_add("write", _refill)
        cv.bind("<ButtonPress-1>", _down)
        cv.bind("<B1-Motion>", _move)
        cv.bind("<ButtonRelease-1>", _up)

        # 居中 + 不超过屏幕(和上面那几个窗口同一个套路: 写死尺寸会把按钮顶出窗口外)
        top.update_idletasks()
        w = min(1060, max(900, top.winfo_screenwidth() - 80))
        h = min(700, max(520, int(top.winfo_screenheight() * 0.85)))
        x = max(0, (top.winfo_screenwidth() - w) // 2)
        y = max(0, (top.winfo_screenheight() - h) // 2)
        top.geometry("%dx%d+%d+%d" % (w, h, x, y))

    def _anim_bg(self):
        """v4.86★ 让背景动起来(多帧轮播) —— 用户原话: "看不懂; 我不会操作"。

        所以这一版按【三步】摆, 每步一句人话, 不需要知道"匹配串/纹理"是什么:
          ① 选要让哪张背景动(下拉里全是人话: 自走棋棋盘 / 节日棋盘 / 身份场…);
          ② 挑几张图当"帧"(最少 2 张, 列表从上到下 = 播放顺序);
          ③ 每张停多久(默认 500 毫秒) → 点「配上, 让它动起来」。
        为什么只能这么干(别再去找 gif / mp4 了): 背景是【画布贴图】—— gif / 动图 webp
          只会显示第一帧, mp4 根本不解码(会变一片空白)。能"动"的唯一办法就是【定时换图】:
          脚本每隔 N 毫秒把那张背景换成下一张, 循环播放 —— 也就是最老实的"翻页动画"。
        """
        cands = []
        for pat, note, size in (list(getattr(RR, "TAVERN_BG_PRESETS", ()) or ())
                                + list(getattr(RR, "NEWSTYLE_BG_PRESETS", ()) or ())
                                + list(getattr(RR, "IDENT_BG_PRESETS", ()) or ())):
            cands.append(("%s（%s）" % (note, size), pat))
        if not cands:
            messagebox.showinfo("让背景动起来", "没找到背景预设表(core/resource_rules.py)。")
            return
        top = tk.Toplevel(self.root)
        top.title("让背景动起来(多帧轮播)")
        top.configure(bg=self._bg())
        top.transient(self.root)
        top.geometry("780x720")

        def _step(n, txt):
            tk.Label(top, text="第 %d 步 · %s" % (n, txt), bg=self._bg(), fg=self._gold(),
                     font=(self._fh(), 9, "bold"), anchor="w").pack(fill="x", padx=10, pady=(10, 2))

        tk.Label(top, text=("背景是【画布贴图】: gif / 动图只会显示第一帧, mp4 根本不解码。\n"
                            "所以想让它动, 只能靠【定时换图】—— 把你挑的几张图轮流贴上去(翻页动画)。"),
                 bg=self._bg(), fg=self._dim(), font=(self._fh(), 8),
                 justify="left", wraplength=600).pack(anchor="w", padx=10, pady=(10, 0))

        _step(1, "要让哪张背景动?")
        cb = ttk.Combobox(top, values=[c[0] for c in cands], width=54, state="readonly")
        cb.current(0)
        cb.pack(anchor="w", padx=14)

        _step(2, "挑几张图当「帧」(最少 2 张; 列表从上到下 = 播放顺序)")
        wrap = tk.Frame(top, bg=self._bg())
        wrap.pack(fill="both", expand=True, padx=14)
        sb = tk.Scrollbar(wrap, orient="vertical")
        sb.pack(side="right", fill="y")
        lb = tk.Listbox(wrap, bg=self._btn(), fg=self._fg(), font=(self._fh(), 9), height=9,
                        selectmode="browse", activestyle="none", exportselection=False,
                        yscrollcommand=sb.set)
        sb.configure(command=lb.yview)
        lb.pack(side="left", fill="both", expand=True)

        # v4.86★ 帧有两条来路(用户原话: "这个不能用皮肤吗? 或者动态帧?" —— 能):
        #   ① file = 你自己的图(会拷进「我的图」目录, 和别的地方一个口径);
        #   ② url  = 官方皮肤大图 / 动态帧的【完整地址】—— 动态皮肤本来就是"一帧一个 png",
        #      直接把它们排起来轮播就是最正宗的背景动画, 连下载都不用(脚本拿地址去贴)。
        frames = []          # 每项 {"t": "file"/"url", "v": 路径或官方地址, "label": 显示名}

        def _refill():
            lb.delete(0, "end")
            for i, f in enumerate(frames):
                lb.insert("end", "%d. %s [%s]" % (i + 1, f["label"],
                                                  "我的图" if f["t"] == "file" else "官方图"))
            if not frames:
                lb.insert("end", "(还没挑图 —— 「添加帧(我的图)」挑本地图; 想直接用官方皮肤/"
                                 "动态帧, 点下面「从皮肤表挑 …」)")

        def _push(t, v, label):
            for f in frames:
                if f["v"] == v:
                    return 0
            frames.append({"t": t, "v": v, "label": label})
            return 1

        def _add():
            ps = filedialog.askopenfilenames(
                title="挑几张图当帧(可一次选多张, 选完还能上下调顺序)",
                filetypes=[("图片", "*.png *.jpg *.jpeg *.webp"), ("所有文件", "*.*")],
                initialdir=RR.IMG_DIR if os.path.exists(RR.IMG_DIR) else os.path.expanduser("~"))
            for p in (ps or ()):
                if p:
                    _push("file", p, os.path.basename(p))
            _refill()

        def _add_dress():
            """从 OL 皮肤表挑帧 —— 官方大图/动态帧【本身就是一帧一张 png】, 现成的轮播素材。"""
            def _one(it):
                _many([it])

            def _many(items):
                n = 0
                for it in (items or []):
                    u = str(it.get("url") or "").strip()
                    if not u:
                        continue
                    nm = str(it.get("name") or "")
                    tail = u.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                    n += _push("url", u, "%s%s" % ((nm + "·") if nm else "", tail))
                _refill()
                if not n:
                    messagebox.showinfo(
                        "让背景动起来",
                        "没拿到官方地址 —— 在皮肤表里选一套后要先点「探测大图」:\n"
                        "探测 = 问官方服务器这几张到底在不在, 探出来的那些行才带地址。", parent=top)
            self._dress_picker(top, _one, on_multi=_many)

        def _add_scanned():
            """从「官方大图/皮肤资源」清单(扫过/探测过的)里挑 —— 也是现成的官方地址。"""
            items = [x for x in (RR.load_official_resources() or [])
                     if str(x.get("url") or "").strip()]
            if not items:
                messagebox.showinfo(
                    "让背景动起来",
                    "清单里还没有带地址的资源 —— 先点「扫描游戏已加载资源」或「联网批量探测 …」"
                    "往清单里添几条, 再来这里挑。", parent=top)
                return
            dlg = tk.Toplevel(top)
            dlg.title("从清单里挑帧(按住 Ctrl 可多选)")
            dlg.configure(bg=self._bg())
            dlg.transient(top)
            lbx = tk.Listbox(dlg, bg=self._btn(), fg=self._fg(), font=(self._fh(), 9),
                             selectmode="extended", width=72, height=14, activestyle="none")
            lbx.pack(fill="both", expand=True, padx=8, pady=(8, 0))
            for x in items:
                lbx.insert("end", "%s | %s" % (x.get("kind") or "",
                                               x.get("pattern") or x.get("url")))

            def _done():
                n = 0
                for i in lbx.curselection():
                    it = items[i]
                    n += _push("url", str(it.get("url") or ""),
                               str(it.get("pattern") or "").rsplit("/", 1)[-1])
                _refill()
                dlg.destroy()

            bb = tk.Frame(dlg, bg=self._bg())
            bb.pack(fill="x", padx=8, pady=8)
            tk.Button(bb, text="取消", command=dlg.destroy, bg=self._btn(), fg=self._fg(),
                      font=(self._fh(), 9), padx=10).pack(side="right")
            tk.Button(bb, text="加进去", command=_done, bg=self._act(), fg=self._gold(),
                      font=(self._fh(), 9, "bold"), padx=10).pack(side="right", padx=(0, 6))

        def _del():
            sel = lb.curselection()
            if not sel:
                return
            frames.pop(sel[0])
            _refill()

        def _up():
            sel = lb.curselection()
            if not sel or sel[0] == 0:
                return
            i = sel[0]
            frames[i - 1], frames[i] = frames[i], frames[i - 1]
            _refill()
            lb.selection_set(i - 1)

        def _down():
            sel = lb.curselection()
            if not sel or sel[0] >= len(frames) - 1:
                return
            i = sel[0]
            frames[i + 1], frames[i] = frames[i], frames[i + 1]
            _refill()
            lb.selection_set(i + 1)

        r2 = tk.Frame(top, bg=self._bg())
        r2.pack(fill="x", padx=14, pady=(4, 0))
        for t, cmd in (("添加帧(我的图) …", _add), ("删掉", _del), ("上移", _up), ("下移", _down)):
            tk.Button(r2, text=t, command=cmd, bg=self._btn(), fg=self._fg(),
                      font=(self._fh(), 9), padx=6, cursor="hand2").pack(side="left", padx=(0, 4))
        r2b = tk.Frame(top, bg=self._bg())
        r2b.pack(fill="x", padx=14, pady=(4, 0))
        tk.Label(r2b, text="不想用本地图? 直接拿官方皮肤当帧:", bg=self._bg(), fg=self._dim(),
                 font=(self._fh(), 8)).pack(side="left")
        for t, cmd in (("从皮肤表挑(OL大图/动态帧) …", _add_dress),
                       ("从已扫清单挑 …", _add_scanned)):
            tk.Button(r2b, text=t, command=cmd, bg=self._btn(), fg=self._blue(),
                      font=(self._fh(), 9), padx=6, cursor="hand2").pack(side="left", padx=(6, 0))

        _step(3, "每张停多久?")
        r1 = tk.Frame(top, bg=self._bg())
        r1.pack(fill="x", padx=14)
        ms_var = tk.StringVar(value="500")
        tk.Entry(r1, textvariable=ms_var, width=8, bg=self._btn(), fg=self._fg(),
                 insertbackground=self._fg(), font=(self._fh(), 9)).pack(side="left")
        tk.Label(r1, text="毫秒(500 = 一秒翻两张; 想更顺滑填 200, 范围 80~10000)",
                 bg=self._bg(), fg=self._dim(), font=(self._fh(), 8)).pack(side="left", padx=6)
        _refill()

        btns = tk.Frame(top, bg=self._bg())
        btns.pack(fill="x", padx=10, pady=(8, 10))

        def _ok():
            i = cb.current()
            pat = RR.relative_pattern(cands[i][1] if 0 <= i < len(cands) else "")
            if not pat:
                messagebox.showinfo("让背景动起来", "先选要让哪张背景动。", parent=top)
                return
            if len(frames) < 2:
                messagebox.showinfo("让背景动起来",
                                    "至少挑 2 张图才轮得起来 —— 只有 1 张那就是静止的。", parent=top)
                return
            try:
                ms = int(str(ms_var.get() or "500").strip() or 500)
            except Exception:
                ms = 500
            ms = max(80, min(10000, ms))
            # 帧分两种: 本地图 → 拷进「我的图」目录(重名自动加序号, 不覆盖原图, 给 /img/ 用);
            #   官方地址(皮肤大图/动态帧) → 【原样用】—— 脚本那边认 http 开头的完整地址,
            #   会自己去取这张图, 不用先下载到本地(这也是"能用皮肤当帧"的关键)。
            import shutil as _sh
            if any(f["t"] == "file" for f in frames):
                try:
                    os.makedirs(RR.IMG_DIR, exist_ok=True)
                except Exception as e:
                    messagebox.showerror("让背景动起来", "建不了我的图目录: %s" % e, parent=top)
                    return
            names = []
            for f in frames:
                if f["t"] == "url":
                    names.append(f["v"])
                    continue
                p = f["v"]
                try:
                    base = os.path.basename(p)
                    nm, ext = os.path.splitext(base)
                    dst = os.path.join(RR.IMG_DIR, base)
                    k = 1
                    while os.path.exists(dst) and os.path.abspath(dst) != os.path.abspath(p):
                        dst = os.path.join(RR.IMG_DIR, "%s_%d%s" % (nm, k, ext))
                        k += 1
                    if os.path.abspath(dst) != os.path.abspath(p):
                        _sh.copy2(p, dst)
                    names.append(os.path.basename(dst))
                except Exception as e:
                    self._status("有一帧没拷进我的图目录: %s" % e)
            if len(names) < 2:
                messagebox.showinfo("让背景动起来", "帧没准备好(拷图失败)。", parent=top)
                return
            first = os.path.join(RR.IMG_DIR, names[0])
            rule = {"id": "anm%d" % int(time.time() * 1000),
                    "name": "伪动态背景(%d帧/%dms)" % (len(names), ms),
                    "kind": "自走棋背景" if "tavernChess" in pat else "背景",
                    "pattern": pat, "target": first, "enabled": True,
                    "season": self._season() or "",
                    "animate": {"urls": names, "ms": ms}}
            total, added, covered = RR.upsert_rules([rule])
            self._load_rules()
            self._status("已配上: 「%s」用 %d 张图轮播, 每 %d 毫秒换一张(共 %d 条规则)。"
                         "点「立即同步到浏览器」后就能看到它在动(重开一局也会接着动); "
                         "想停 —— 在下面的规则表里把这条停用或删掉。"
                         % (cands[i][0] if 0 <= i < len(cands) else pat, len(names), ms, total))
            top.destroy()

        tk.Button(btns, text="取消", command=top.destroy, bg=self._btn(), fg=self._fg(),
                  font=(self._fh(), 9), padx=10, cursor="hand2").pack(side="right")
        tk.Button(btns, text="配上, 让它动起来", command=_ok, bg=self._act(), fg=self._gold(),
                  font=(self._fh(), 9, "bold"), padx=10, cursor="hand2").pack(side="right", padx=(0, 6))

    def _probe_big_id(self):
        """手上有编号时直接问官方"这个编号的资源在不在", 而不是猜名字。

        用户给的样本(500103/500104 刘备皮肤 935KB、621401 神马超、821401 它的动态版 beijing/
          daiji/daiji_2)就是这么验的。一个编号探 7 个地址: 静态大图 1 个 + 动态皮肤常见帧 6 个,
        并发出去, 几秒出结果 —— 存在的自动进清单。
        """
        raw = simpledialog.askstring(
            "按编号探测",
            "输入资源编号(例: 500103 = 刘备皮肤, 621401 = 神马超)。\n"
            "动态版会自动一起探(编号 + 200000, 帧名 beijing/daiji/daiji_2 等)。\n"
            "多个编号用空格或逗号分隔:", parent=self.root)
        if not raw:
            return
        ids = re.findall(r"\d{5,9}", str(raw))[:8]
        if not ids:
            messagebox.showinfo("按编号探测", "没认出编号 —— 输入纯数字, 例如 500103。")
            return
        jobs = []
        for i in ids:
            jobs += RR.big_id_candidates(i, with_neighbors=False)
        self._status("正在探测 %d 个编号的 %d 个地址(HEAD 请求, 不下载图)…" % (len(ids), len(jobs)))

        def _work():
            from concurrent.futures import ThreadPoolExecutor, as_completed
            hits = []
            try:
                with ThreadPoolExecutor(max_workers=6) as ex:
                    futs = {ex.submit(RR.probe_res_url, u): u for u in jobs}
                    for f in as_completed(futs):
                        u = futs[f]
                        try:
                            rr = f.result()
                        except Exception:
                            rr = {"ok": False}
                        if rr.get("ok"):
                            hits.append({"url": u, "w": rr.get("w") or 0,
                                         "h": rr.get("h") or 0, "size": rr.get("size") or 0})
            except Exception as e:
                self.root.after(0, self._status, "探测出错: %s" % e)
                return
            added = 0
            try:
                added = int(RR.merge_official_resources(hits, source="probe").get("added") or 0)
            except Exception as e:
                self.root.after(0, self._status, "存清单失败: %s" % e)
            self.root.after(0, self._probe_big_report, len(jobs), len(hits), added)
        threading.Thread(target=_work, daemon=True).start()

    def _probe_big_report(self, done, hit, added):
        self._load_official_res()
        if not hit:
            self._status("探测了 %d 个地址, 服务器上都没有 —— 编号可能不是这套规则; 把你在网络面板里"
                         "看到的那条完整地址贴进「添加自定义规则」的匹配串最稳。" % done)
            return
        self._status("探测完成: %d 个地址里 %d 个真的存在(新增 %d 条进清单); "
                     "双击清单里的一行就能把它配成可替换资源" % (done, hit, added))

    def _probe_dress_batch(self):
        """v4.86★ 不用逛界面也能拿到官方资源 —— 用户原话: "扫描需要我都点一遍吗? 你不能去获取吗?"

        能, 而且不用等游戏加载: 皮肤表(data/dress_table.json, 5300+ 套)每一行都带着【大图编号】,
          按编号就能拼出官方地址(静态大图 1 个 + 动态帧 6 个), 并发 HEAD 去问服务器"在不在",
          活着的直接进「官方大图/皮肤资源」清单。
        只发 HEAD(取前 4KB 量尺寸, 不下载整张图), 所以几百个地址也就是等一会儿的事 ——
          但全表 5300 套 = 三万多个请求, 那是明确不做的(真要全量就去抓 Config.sgs 解表)。
        """
        from core import dress_table as DT
        try:
            t = DT.load()
        except Exception as e:
            messagebox.showinfo("联网批量探测", "皮肤表读不出来: %s" % e)
            return
        if not t.get("loaded"):
            messagebox.showinfo("联网批量探测",
                                "本地还没有皮肤表(data/dress_table.json) —— 先到图库页(/gallery)"
                                "点「OL 皮肤表」联网抓一次, 或者跑 python core/dress_table.py。")
            return
        raw = simpledialog.askstring(
            "联网批量探测(不用逛界面)",
            "从皮肤表里挑【前 N 套】去问官方服务器(HEAD 请求, 不下载图)。\n"
            "填 N(默认 60; 每套要问 7 个地址, 建议别超过 200):\n"
            "只想探某个武将, 写成「60 刘备」。", parent=self.root)
        if raw is None:
            return
        nums = re.findall(r"\d+", str(raw))
        n = int(nums[0]) if nums else 60
        n = max(1, min(200, n))
        kw = re.sub(r"\d+", " ", str(raw)).strip()
        try:
            rows = DT.search(kw, limit=n) if kw else [{"id": r[0]} for r in (t.get("skins") or [])[:n]]
        except Exception as e:
            messagebox.showinfo("联网批量探测", "筛皮肤失败: %s" % e)
            return
        if not rows:
            messagebox.showinfo("联网批量探测", "没筛到皮肤(关键词「%s」)。" % kw)
            return
        jobs = []
        for r in rows:
            try:
                jobs += DT.skin_candidates(r.get("id"))
            except Exception as e:
                self.log("候选生成失败(%s): %s" % (r.get("id"), e), level="WARN")
        if not jobs:
            messagebox.showinfo("联网批量探测", "这套表里没算出候选地址(表里可能没有大图编号)。")
            return
        jobs = list(dict.fromkeys(jobs))
        self._status("正在问官方服务器: %d 套皮肤 / %d 个地址(HEAD, 不下载图)…" % (len(rows), len(jobs)))

        def _work():
            from concurrent.futures import ThreadPoolExecutor, as_completed
            hits = []
            try:
                with ThreadPoolExecutor(max_workers=8) as ex:
                    futs = {}
                    for u in jobs:
                        futs[ex.submit(RR.probe_res_url, u, 6.0)] = u
                    done_n = 0
                    for f in as_completed(futs):
                        u = futs[f]
                        done_n += 1
                        try:
                            rr = f.result()
                        except Exception:
                            rr = {"ok": False}
                        if rr.get("ok"):
                            hits.append({"url": u, "w": rr.get("w") or 0,
                                         "h": rr.get("h") or 0, "size": rr.get("size") or 0})
                        if done_n % 40 == 0:
                            self.root.after(0, self._status,
                                            "探测中 %d/%d … 已探到 %d 个" % (done_n, len(jobs), len(hits)))
            except Exception as e:
                self.root.after(0, self._status, "探测出错: %s" % e)
                return
            added = 0
            try:
                added = int(RR.merge_official_resources(hits, source="probe").get("added") or 0)
            except Exception as e:
                self.root.after(0, self._status, "存清单失败: %s" % e)
            self.root.after(0, self._probe_big_report, len(jobs), len(hits), added)

        threading.Thread(target=_work, daemon=True).start()

    def _preview_official(self):
        """v4.86★ 清单里的官方资源也要能看 —— 用户原话: "下面的那俩不能预览吗?"。"""
        it = self._selected_official()
        if not it:
            messagebox.showinfo("预览", "请先在「官方大图 / 皮肤资源」清单里点一行。")
            return
        url = str(it.get("url") or RR.res_full_url(it.get("pattern") or ""))
        if not url:
            messagebox.showinfo("预览", "这一行没有地址(老数据) —— 删掉重新扫描/探测一次。")
            return
        try:
            import webbrowser
            webbrowser.open(url)
            self._status("预览(浏览器打开): %s —— 这就是游戏现在用的那张官方图; "
                         "想换成自己的, 点「替换成我的图 …」" % url)
        except Exception as e:
            self._status("预览打不开(%s): %s —— 地址是 %s" % (type(e).__name__, e, url))

    def _preview_candidate(self):
        """v4.86★ 候选资源 URL(从诊断/游戏页抓到的)也能预览 —— 同上, 双击是"用作背景"。"""
        sel = self.cand_tree.selection()
        if not sel:
            messagebox.showinfo("预览", "请先在下面的候选资源列表里点一行。")
            return
        vals = self.cand_tree.item(sel[0], "values") or ()
        url = str(vals[1] if len(vals) > 1 else (vals[0] if vals else "")).strip()
        if not url:
            messagebox.showinfo("预览", "这一行没有 URL。")
            return
        try:
            import webbrowser
            webbrowser.open(url)
            self._status("预览(浏览器打开): %s —— 这是游戏真的加载过的地址(双击那一行是拿它建规则)" % url)
        except Exception as e:
            self._status("预览打不开(%s): %s —— 地址是 %s" % (type(e).__name__, e, url))

    def _use_official_res(self):
        """清单里选中一行 → 配成"可替换资源"(选一张本地图, 见 _add_rule_for_pattern)。"""
        it = self._selected_official()
        if not it:
            messagebox.showinfo("配替换", "请先在官方资源清单里点一行(双击也行)。")
            return
        pat = str(it.get("pattern") or it.get("url") or "")
        self._add_rule_for_pattern(pat, kind=it.get("kind") or "OL大图",
                                   name_hint=os.path.basename(pat).split("?")[0])

    def _set_official_target(self):
        """把清单里选中的官方大图/皮肤【当成本次替换的目标】(皮肤切换那条路)。

        例: 上面规则表里选中一条棋子立绘规则 → 点它 → 那条规则的目标变成官方 OL 大图的 URL。
        好处是不用先把大图下载到本地(官方图是 https, 游戏页也是 https, 同协议直接能用)。
        """
        it = self._selected_official()
        if not it:
            messagebox.showinfo("当替换目标", "请先在官方资源清单里点一行。")
            return
        r, is_custom = self._selected_rule()
        if r is None:
            messagebox.showinfo("当替换目标",
                                "请先在【上面的规则表】里选一条要改的规则, 再来点它 —— "
                                "这一行官方资源会被写进那条规则的目标。")
            return
        url = str(it.get("url") or RR.res_full_url(it.get("pattern") or ""))
        if not url:
            return
        if not is_custom:
            r = RR.rule_from_default(r)
        r["target"] = url
        r["enabled"] = True
        rules = RR.load_rules()
        rules = [x for x in rules if x.get("pattern") != r.get("pattern")]
        rules.append(r)
        RR.save_rules(rules)
        self._load_rules()
        self._status("已把规则 %s 的目标设成官方资源: %s" % (r.get("pattern"), url))

    def _del_official_res(self):
        it = self._selected_official()
        if not it:
            messagebox.showinfo("删除", "请先在官方资源清单里点一行。")
            return
        pat = str(it.get("pattern") or "")
        items = [x for x in RR.load_official_resources() if str(x.get("pattern")) != pat]
        try:
            RR.save_official_resources(items)
        except Exception as e:
            messagebox.showinfo("删除", "写清单失败: %s" % e)
            return
        self._fill_official_res(items)
        self._status("已从官方资源清单里删掉: %s" % pat)

    def _load_defaults(self):
        """仅刷新显示, 不保存。默认规则始终来自配置表。"""
        self._load_rules()
        self._status("已重新读取默认规则(自定义规则优先)")

    def _generate(self):
        def _work():
            try:
                season = self._season()
                base = self._base_url()
                only = bool(self.only_existing_var.get())
                info = RR.generate_redirect_files(season, only_existing=only, base_url=base)
                self.root.after(0, self._status,
                                "已生成 %s 规则: 导出 %d 条 / 共 %d 条(还差图 %d 条, 写在 .tsv) -> %s"
                                % (info["season"], info["count"], info["total"],
                                   info["skipped"], info["tsv"]))
                self.root.after(0, self.log,
                                "规则文件: %s%s" % (info["txt"],
                                                 "  (目标用本机图片服务 %s)" % base if base else "  (目标为本地文件路径)"))
                # v4.69★ 顺手清掉同一个赛季的"别名文件"(s10 与 10 那种) —— 不告诉用户的话,
                #   他会以为自己的文件被吞了(实际是被归一化成一份了)。
                if info.get("cleaned"):
                    self.root.after(0, self.log,
                                    "已清理同赛季的旧文件(避免不知道该导入哪个): %s"
                                    % ", ".join(info["cleaned"]))
            except Exception as e:
                self.root.after(0, self._status, "生成失败: %s" % e)
        threading.Thread(target=_work, daemon=True).start()

    def _import_tsv(self):
        p = filedialog.askopenfilename(
            title="选择要导入的 tsv",
            filetypes=[("TSV", "*.tsv"), ("全部文件", "*.*")],
            initialdir=RR.REF_DIR if os.path.exists(RR.REF_DIR) else os.path.expanduser("~"))
        if not p:
            return
        imported = RR.import_from_tsv(p)
        if not imported:
            messagebox.showinfo("导入", "没有读到规则, 或文件格式不对。")
            return
        rules = RR.load_rules()
        # 以 pattern 去重: 导入覆盖现有
        existing = {x.get("pattern"): x for x in rules}
        for r in imported:
            existing[r.get("pattern")] = r
        rules = list(existing.values())
        RR.save_rules(rules)
        self._load_rules()
        self._status("从 %s 导入 %d 条规则" % (os.path.basename(p), len(imported)))

    def _open_img_dir(self):
        """一键打开「我的图」文件夹 —— v4.87★ 以前这个方法【没有按钮绑它】, 用户只能手找
        路径(用户原话: "也没有按钮啊?"), 现在界面上直接给了按钮。"""
        d = RR.IMG_DIR
        os.makedirs(d, exist_ok=True)
        try:
            os.startfile(d)
            self._status("已打开图片目录: %s —— 把 png/jpg/webp 丢进去就能在规则里选到。" % d)
        except Exception as e:
            self._status("打不开目录: %s" % e)
