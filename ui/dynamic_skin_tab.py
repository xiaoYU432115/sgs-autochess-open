"""『动态皮肤』页 (v4.111★) —— UI 放在 ui/dynamic_skin_tab.py, 道理同资源替换页:
app.py 不被这一页撑爆。构建模式与 SeasonUpdateTabBuilder / ResourceTabBuilder 同一套
(app 传进来, 不反向依赖)。

为什么单独一页
==============
用户原话: "我感觉渲染和 png 替换不一样; 这俩放一起会乱"。
  是的两码事:
    · 资源替换页那套 = 【一张 png 换一张 png】, 换的是像素, 换完还是静态图;
    · 动态皮 = 【一整套渲染资源】(skel 骨架 + atlas 切片表 + png 贴图), 少一个文件
      或三件套不同源 → 错位碎块 / 空白。
  混在一页里配, 最常见的下场就是"以为换了动态皮, 实际只换了一张贴图"。所以单独成页。

本页三件事
==========
  ① 挑一套动态皮 → 探测这套里【真实存在】的文件(帧名/后缀都是探出来的, 不猜);
  ② 拿某一帧的【贴图】换【自走棋背景 / 主公棋子】(这两处本身是静态 png) ——
     得到的是动态皮的高清图(2048×2048 那一档), 但【不是动画】;
  ③ 整套映射(动态皮编号 → 另一套动态皮编号) —— 这个才是真换动画。
"""

import os
import re
import json
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from ui.widgets import ThemeMixin   # v4.109★ 主题色 getter 统一到 widgets.ThemeMixin
from core import silence as _SIL


# v4.139★ 默认插到【自走棋】那一层 —— 实测 tavernChessBg.png 就画在 TavernChessGameScene
#   上(SceneLayer 下唯一的子节点)。以前默认是牌桌那两条(ChessTableScene / newStyleTableDeckBg),
#   在自走棋里一个都匹配不上, 表现就是"开关都开了, 就是不出人"。
DEFAULT_PAT = "TavernChessGameScene"

# v4.139★ 扫游戏页: 把所有【贴了静态图的节点】列出来当落点 —— 用户原话: "其他位置也要一个个
#   找吗? 慢慢换, 看合不合适吗? 例如资源替换那些从网页找出来的"。
#   与 SCAN_HOST_JS 的分工: 那个找"容器/层"(插动态皮用), 这个找"贴了图的节点"(换静态图用)。
#   只收【真的有一张图片地址】的节点(skin / texture / graphics 画的那张), 按面积从大到小排。
SCAN_SLOT_JS = r"""
(function () {
    try {
        var S = window.Laya && Laya.stage;
        if (!S) return '[]';
        var out = [];
        function texOf(o) {
            var us = [];
            try {
                var t = o.texture || o._texture;
                if (t) { var x = t.url || t._url || (t.bitmap && t.bitmap.url) || ''; if (x) us.push(String(x)); }
                var sk = o._skin || o.skin;
                if (sk && typeof sk === 'string') us.push(sk);
                var g = o.graphics || o._graphics, cm = g && (g._cmds || g.cmds || g._commands);
                if (cm) for (var i = 0; i < cm.length; i++) {
                    var c = cm[i];
                    if (c && typeof c === 'object') for (var k in c) {
                        var v = c[k];
                        if (v && typeof v === 'object' && (v.url || v._url)) us.push(String(v.url || v._url));
                    }
                }
            } catch (e) { }
            return us;
        }
        (function w(o, d) {
            if (!o || d > 16 || out.length >= 400) return;
            var us = texOf(o), cn = '', nm = '';
            try { cn = (o.constructor && o.constructor.name) || ''; } catch (e) { }
            try { nm = String(o.name || ''); } catch (e) { }
            if (us.length && /\.(png|jpg|jpeg|webp)$/i.test(String(us[0]))) {
                out.push({ cls: cn, name: nm, tex: String(us[0]),
                           w: Math.round(o.width || 0), h: Math.round(o.height || 0) });
            }
            var ch = o._children || o.children || [];
            for (var i = 0; i < ch.length; i++) w(ch[i], d + 1);
        })(S, 0);
        var seen = {}, r = [];
        for (var i = 0; i < out.length; i++) {
            var k = out[i].tex;
            if (!seen[k]) { seen[k] = 1; r.push(out[i]); }
        }
        r.sort(function (a, b) { return (b.w * b.h) - (a.w * a.h); });
        return JSON.stringify(r.slice(0, 150));
    } catch (e) { return '[]'; }
})()
"""

# v4.139★ 扫游戏页: 把"可以插动态皮的那些层"列出来 —— 免得用户手填匹配串、一个个试。
#   返回 JSON: [{cls, w, h, tex}] —— cls 是节点类名(最稳的匹配串), tex 是它画的贴图名。
#   只收"像容器/像背景"的节点: 面积占舞台 1/4 以上, 或类名/贴图名里带 Scene/Layer/Bg 这类字。
#   放在这里(而不是油猴脚本里)的道理同 core/pool_probe.py: 改一次探测逻辑不用让用户重注入脚本。
SCAN_HOST_JS = r"""
(function () {
    try {
        var S = window.Laya && Laya.stage;
        if (!S) return '[]';
        var out = [], SW = S.width || 1600, SH = S.height || 900;
        function urls(o) {
            var us = [];
            try {
                var t = o.texture || o._texture;
                if (t) { var x = t.url || t._url || (t.bitmap && t.bitmap.url) || ''; if (x) us.push(String(x)); }
                var sk = o._skin || o.skin;
                if (sk && typeof sk === 'string') us.push(sk);
                var g = o.graphics || o._graphics, cm = g && (g._cmds || g.cmds || g._commands);
                if (cm) for (var i = 0; i < cm.length; i++) {
                    var c = cm[i];
                    if (c && typeof c === 'object') for (var k in c) {
                        var v = c[k];
                        if (v && typeof v === 'object' && (v.url || v._url)) us.push(String(v.url || v._url));
                    }
                }
            } catch (e) { }
            return us;
        }
        (function w(o, d) {
            if (!o || d > 14 || out.length >= 200) return;
            var cn = '';
            try { cn = (o.constructor && o.constructor.name) || ''; } catch (e) { }
            var us = urls(o), w2 = o.width || 0, h2 = o.height || 0;
            var big = (w2 * h2) >= (SW * SH * 0.25);
            var looks = /Scene|Layer|Bg|Back|Root|Main|View|Panel|Table/i.test(cn)
                        || /bg|background|scene/i.test(us.join(' '));
            if (cn && (big || looks)) {
                out.push({ cls: cn, w: Math.round(w2), h: Math.round(h2),
                           tex: String(us[0] || '').replace(/^.*\/assets\//, '').slice(0, 60) });
            }
            var ch = o._children || o.children || [];
            for (var i = 0; i < ch.length; i++) w(ch[i], d + 1);
        })(S, 0);
        var seen = {}, r = [];
        for (var i = 0; i < out.length; i++) {
            var k = out[i].cls + '|' + out[i].tex;
            if (!seen[k]) { seen[k] = 1; r.push(out[i]); }
        }
        return JSON.stringify(r.slice(0, 80));
    } catch (e) { return '[]'; }
})()
"""


class DynamicSkinTabBuilder(ThemeMixin):
    """『动态皮肤』页。"""

    def __init__(self, app):
        self.app = app
        self.root = app.root
        self._busy = False
        self._suite = []        # ① 探测到的【全部文件】(含 skel/atlas —— 下载整套要用)
        self._frames = []       # ② 用: 这套里能当"图"用的贴图 [{file,url,size,hint}]
        self._slots = []        # ③ 用: 当前列出来的落点(与 slot_lb 行序一致)
        self._map_rows = []     # ④ 用: 探测两边的结果
        self._dyn_items = []    # 「挑一套有动态的」窗口里当前列出来的那些

    # ---------------------------------------------------------------- 小工具
    def _status(self, msg):
        try:
            self.app._status(msg)
        except Exception as e:      # noqa: BLE001 —— 状态栏不该让整页功能挂掉
            print("[动态皮肤] 状态栏不可用: %s" % e, file=sys.stderr)

    def _set_busy(self, on):
        self._busy = bool(on)

    def _dress_picker_fn(self):
        """拿到资源替换页那个『皮肤表选择器』(按"谁带 _dress_picker"找, 改名也不炸)。"""
        try:
            from ui import resource_tab as _RT
        except Exception:       # noqa: BLE001
            return None
        for _nm in ("ResourceTabBuilder", "ResourceTab", "ResTabBuilder"):
            _cls = getattr(_RT, _nm, None)
            if _cls is not None and hasattr(_cls, "_dress_picker"):
                return _cls._dress_picker
        for _nm in dir(_RT):
            _cls = getattr(_RT, _nm, None)
            if isinstance(_cls, type) and hasattr(_cls, "_dress_picker"):
                return _cls._dress_picker
        return None

    # ---------------------------------------------------------------- build
    def build(self, parent):
        # v4.139★ 挂上滚动容器 —— 这一页有五个分区(挑皮/落点/整套映射/动态背景/…),
        #   内容比窗口高; 原来直接 pack 到 Tab 上, 超出窗口的部分就被截掉、又没法滚,
        #   表现就是"动态皮肤都看不全"。这里和『即时换肤』页同一套做法(见 app 的
        #   _make_scrollable_tab), 顺带开了横向滚动(探测出来的文件名一行很长)。
        mk = getattr(self.app, "_make_scrollable_tab", None)
        if mk:
            try:
                wrap = mk(parent, pady=8, hscroll=True)
            except TypeError:
                wrap = mk(parent, pady=8)
        else:
            wrap = tk.Frame(parent, bg=self._bg())
            wrap.pack(fill="both", expand=True, padx=10, pady=8)

        tk.Label(
            wrap,
            text="动态皮 = skel(骨架) + atlas(切片表) + png(贴图) 三件套, 跟『换一张 png』不是一回事 —— "
                 "少一个文件或三件套不同源就是错位碎块。本页四块: ① 怎么播(人物背景/透明/"
                 "图层/取景, 天天动的都在这儿); ② 挑一套并探出它真实有哪些文件; ③ 拿某一帧的"
                 "贴图画去换自走棋背景/主公棋子(换完是静态图, 不是动画); ④ 整套映射才是真换动画。",
            bg=self._bg(), fg=self._dim(), font=(self._fh(), 8),
            justify="left", wraplength=980).pack(fill="x", padx=10, pady=(8, 2))

        # v4.139★ 顺序按"用得多的放上面"重排 —— 原来最常用那块(怎么播: 人物背景/透明/图层)
        #   排在第 ⑤, 每次都要滚到底才能改; 现在放 ①, 探文件/换落点/整套映射顺延到 ②③④。
        self._build_dynbg(wrap)     # ① 怎么播(最常用: 人物背景 / 透明 / 图层 / 取景)
        self._build_pick(wrap)      # ② 挑一套 + 帧清单
        self._build_slot(wrap)      # ③ 拿帧贴图换背景/主公棋子
        self._build_map(wrap)       # ④ 整套映射

    # ---------------------------------------------------------------- ① 挑一套 + ② 帧清单
    def _build_pick(self, parent):
        box = tk.LabelFrame(parent, text=" ② 挑一套动态皮 → 探出这套里真实存在的文件 ",
                            bg=self._bg(), fg=self._gold(), font=(self._fh(), 10, "bold"),
                            bd=1, relief="groove")
        box.pack(fill="x", padx=10, pady=(0, 6))

        r = tk.Frame(box, bg=self._bg())
        r.pack(fill="x", padx=6, pady=6)
        tk.Label(r, text="动态编号:", bg=self._bg(), fg=self._fg(),
                 font=(self._fh(), 9)).pack(side="left")
        self.no_var = tk.StringVar(value="")
        tk.Entry(r, textvariable=self.no_var, width=16, bg=self._btn(), fg=self._fg(),
                 insertbackground=self._fg(), font=(self._fh(), 9)).pack(side="left", padx=(2, 6))
        tk.Button(r, text="从皮肤表挑 …", command=self._pick_dress, bg=self._btn(),
                  fg=self._gold(), font=(self._fh(), 8), padx=6,
                  cursor="hand2").pack(side="left")
        # v4.112★ 官方 5379 套皮里只有 327 套有动态版 —— 从那 327 套里挑, 不用在
        #   五千多套里碰运气(挑到没动态的只会等几秒然后一句"没探到")。
        tk.Button(r, text="挑一套有动态的 …", command=self._pick_dyn, bg=self._act(),
                  fg=self._blue(), font=(self._fh(), 8, "bold"), padx=6,
                  cursor="hand2").pack(side="left", padx=(6, 0))
        tk.Button(r, text="探测这套", command=self._probe, bg=self._act(), fg=self._gold(),
                  font=(self._fh(), 9, "bold"), padx=10, cursor="hand2").pack(side="left", padx=(6, 0))
        # v4.112★ 下载整套: 三件套落到本地同目录 → 本地 SpineViewer 打开就能看动画,
        #   也能直接拿来当替换目标(不再依赖官方服务器在不在)。
        tk.Button(r, text="下载整套(到本地)", command=self._download, bg=self._btn(),
                  fg=self._fg(), font=(self._fh(), 8), padx=6,
                  cursor="hand2").pack(side="left", padx=(6, 0))
        self.pick_var = tk.StringVar(value="")
        tk.Label(r, textvariable=self.pick_var, bg=self._bg(), fg=self._blue(),
                 font=(self._fh(), 8)).pack(side="left", padx=(8, 0))

        self.fr_lb = tk.Listbox(box, height=7, bg=self._btn(), fg=self._fg(),
                                font=(self._fh(), 9), selectbackground=self._act(),
                                exportselection=False)
        self.fr_lb.pack(fill="x", padx=6, pady=(0, 6))
        # v4.139★ 选中某一帧就预览 —— 原来只有一行文件名, 换之前根本不知道这套长什么样,
        #   点下去才知道换错了。这里把那一帧的贴图缩略图显示出来(本地有就用本地, 没有就
        #   单下这一张, 不用整套下载)。
        try:
            self.fr_lb.bind("<<ListboxSelect>>", self._on_frame_sel)
        except Exception:
            _SIL.note("ui/dynamic_skin_tab.py", "fr_lb.bind(ListboxSelect)")

        pv = tk.Frame(box, bg=self._bg())
        pv.pack(fill="x", padx=6, pady=(0, 6))
        self._prev_lab = tk.Label(pv, bg=self._bg(), fg=self._dim(),
                                  text="(探测完点上面某一帧 → 这里显示那张贴图)",
                                  font=(self._fh(), 8), justify="left", wraplength=520)
        self._prev_lab.pack(side="left", padx=(0, 10))
        self._prev_var = tk.StringVar(value="")
        tk.Label(pv, textvariable=self._prev_var, bg=self._bg(), fg=self._blue(),
                 font=(self._fh(), 8), justify="left",
                 wraplength=420).pack(side="left", anchor="n")

    def _pick_dress(self):
        picker = self._dress_picker_fn()
        if picker is None:
            messagebox.showinfo("从皮肤表挑", "没找到皮肤表选择器(资源替换页没加载)。",
                                parent=self.root)
            return
        host = getattr(self.app, "_resource_builder", None) or self

        def _got(it):
            ref = str(it.get("url") or it.get("pattern") or "")
            if not ref:
                self.pick_var.set("这套没探到地址 —— 换一套试试")
                return
            from core import dyn_skin as DS
            self.pick_var.set("正在问官方: 这套的动态版在哪个目录 …")
            self._set_busy(True)

            def _do():
                return DS.dyn_id_of_ref(ref)

            def _done(no):
                self._set_busy(False)
                if not no:
                    # v4.112★ 偏移试错没猜中 → 再查一次动态清单(按名字对)。
                    #   清单是建皮肤表时从官方配置里存下来的, 里面那 327 套的动态编号是
                    #   官方自己写的 Resource, 不用猜 —— 能配上就不用让用户白等这一轮。
                    hit = DS.dyn_no_by_name(it.get("name"))
                    if hit:
                        self.no_var.set(str(hit))
                        self.pick_var.set("%s → 动态目录 %s(清单里查到的)"
                                          % (it.get("name") or "", hit))
                        self._probe()
                        return
                    self.pick_var.set("这套没有动态版(或没探到) —— 用旁边的『挑一套有动态的』")
                    return
                self.no_var.set(no)
                self.pick_var.set("%s → 动态目录 %s" % (it.get("name") or "", no))
                self._probe()

            threading.Thread(target=lambda: self._run(_do, _done), daemon=True).start()

        try:
            picker(host, self.root, _got)
        except Exception as e:      # noqa: BLE001
            messagebox.showwarning("皮肤表", "打开失败: %s" % e, parent=self.root)

    # ---------------------------------------------------------------- ① 挑一套【有动态的】
    def _pick_dyn(self):
        """从【有动态版】的那批里挑一套 —— 数据源是 data/dyn_flag.json。

        为什么单开一个入口: 官方 5379 套里只有 327 套带 DynamicStandbySkel(实测值),
        在五千多套里碰运气挑, 十次有九次挑到没动态的 —— 表现就是"等了几秒, 没探到"。
        """
        from core import dyn_skin as DS
        try:
            items = DS.dyn_list()
        except Exception as e:      # noqa: BLE001
            messagebox.showwarning("有动态的皮肤", "读清单失败: %s" % e, parent=self.root)
            return
        if not items:
            messagebox.showinfo(
                "有动态的皮肤",
                "还没这份清单(data/dyn_flag.json)。\n\n"
                "去『换装』页做一次【在线更新皮肤表】—— 建表时会顺手把官方 5379 套里\n"
                "带 DynamicStandbySkel 的那批(327 套)存成这份清单, 之后这里就能直接挑。",
                parent=self.root)
            return

        win = tk.Toplevel(self.root)
        win.title("有动态的皮肤(共 %d 套)" % len(items))
        win.configure(bg=self._bg())
        win.geometry("560x440")
        kw = tk.StringVar(value="")
        tk.Label(win, text="搜皮肤名 / 武将名:", bg=self._bg(), fg=self._fg(),
                 font=(self._fh(), 9)).pack(anchor="w", padx=8, pady=(8, 2))
        tk.Entry(win, textvariable=kw, bg=self._btn(), fg=self._fg(),
                 insertbackground=self._fg(), font=(self._fh(), 9)).pack(fill="x", padx=8)
        lb = tk.Listbox(win, bg=self._btn(), fg=self._fg(), font=(self._fh(), 9),
                        selectbackground=self._act(), exportselection=False)
        lb.pack(fill="both", expand=True, padx=8, pady=6)

        def _fill(*_a):
            k = kw.get().strip()
            lb.delete(0, "end")
            self._dyn_items = [x for x in items if (not k or k in x["name"])]
            for x in self._dyn_items:
                lb.insert("end", "  %-28s 动态目录 %s" % (x["name"], x["no"]))
            if not self._dyn_items:
                lb.insert("end", "  (没匹配到)")
        try:
            kw.trace_add("write", _fill)
        except Exception: _SIL.note("ui/dynamic_skin_tab.py", "kw.trace_add —— 老 Tk 没有它, 退成手动刷新")
        _fill()

        def _use(_e=None):
            i = lb.curselection()
            if not i or not self._dyn_items:
                return
            it = self._dyn_items[i[0]]
            self.no_var.set(str(it["no"]))
            self.pick_var.set("%s → 动态目录 %s(清单里查到的, 不用猜)"
                              % (it["name"], it["no"]))
            win.destroy()
            self._probe()

        tk.Button(win, text="用这套(双击也行)", command=_use, bg=self._act(),
                  fg=self._gold(), font=(self._fh(), 9, "bold"),
                  padx=10).pack(pady=(0, 8))
        lb.bind("<Double-1>", _use)

    # ---------------------------------------------------------------- ① 下载整套
    def _download(self):
        """把探测出来的这整套文件下载到本地同目录。

        落地的用处有两个:
          ① 本地 SpineViewer 打开就能看动画(三件套同目录拖进去即可);
          ② 下载下来的文件可以【直接当替换目标】—— 不再依赖官方服务器那一刻在不在。
        """
        from core import dyn_skin as DS
        no = "".join(ch for ch in self.no_var.get() if ch.isdigit())
        files = [str(x.get("file")) for x in (self._suite or []) if x.get("file")]
        if not no or not files:
            messagebox.showinfo("下载整套",
                                "先填编号并点『探测这套』—— 只下载真实存在的那些文件, 不瞎猜。",
                                parent=self.root)
            return
        self.pick_var.set("正在下载 %d 个文件 …" % len(files))
        self._set_busy(True)

        def _do():
            return DS.download_suite(no, files)

        def _done(res):
            self._set_busy(False)
            if not res:
                self.pick_var.set("下载失败")
                return
            base, ok, fail = res
            if not ok:
                self.pick_var.set("一个都没下下来 —— 网络能连官方服务器吗?")
                return
            self.pick_var.set("已下载 %d 个 → %s" % (len(ok), base))
            msg = ("下载到:\n%s\n\n成功 %d 个%s\n\n"
                   "三件套在同一个目录 → 拖进本地 SpineViewer 就能看动画;\n"
                   "也可以直接拿这些本地文件当替换目标。"
                   % (base, len(ok),
                      ("\n失败 %d 个: %s" % (len(fail), "、".join(f[0] for f in fail[:4])))
                      if fail else ""))
            self._status("动态皮 %s 已下载 %d 个文件 → %s" % (no, len(ok), base))
            if messagebox.askyesno("下载整套", msg + "\n\n要打开这个文件夹吗?",
                                   parent=self.root):
                try:
                    os.startfile(base)      # Windows 资源管理器打开
                except Exception as e:      # noqa: BLE001
                    print("[动态皮肤] 打开文件夹失败: %s" % e, file=sys.stderr)

        threading.Thread(target=lambda: self._run(_do, _done), daemon=True).start()

    def _run(self, job, done):
        """后台跑 job, 回主线程调 done(结果)。"""
        try:
            res = job()
        except Exception as e:      # noqa: BLE001
            res = None
            err = str(e)
        else:
            err = ""
        if err:
            self.root.after(0, lambda: done(None) if done else None)
            return
        self.root.after(0, lambda: done(res))

    def _probe(self):
        no = "".join(ch for ch in self.no_var.get() if ch.isdigit())
        if not no:
            messagebox.showinfo("探测", "先填动态编号(dynamic/ 后面那串数字)。", parent=self.root)
            return
        from core import dyn_skin as DS
        self.pick_var.set("正在问官方: %s 这套有哪些文件 …" % no)
        self._set_busy(True)

        def _do():
            return DS.probe_suite(no)

        def _done(suite):
            self._set_busy(False)
            # v4.112★ 整套文件都留着(不只是贴图) —— 下载整套要按这份清单下 skel/atlas/png
            self._suite = list(suite or [])
            self._frames = DS.frame_choices(suite)
            self.fr_lb.delete(0, "end")
            if not suite:
                self.pick_var.set("%s: 一个文件都没探到 —— 编号对吗?" % no)
                return
            for f in self._frames:
                self.fr_lb.insert("end", "  %-18s %8s  %s"
                                  % (f["file"], self._kb(f["size"]), f["hint"]))
            # v4.139★ 探测完 → ① 里那个"帧"下拉换成这套【真实存在】的帧
            #   (原来写死 beijing/daiji 两个, 这套可能只有 beijing, 也可能有 daiji1/daiji2)。
            try:
                frs = []
                for x in (suite or []):
                    fr = str(x.get("frame") or "").strip()
                    if fr and fr not in frs:
                        frs.append(fr)
                if frs:
                    self._dbg_frame_cb["values"] = frs
                    if self._dbg_frame.get() not in frs:
                        self._dbg_frame.set("beijing" if "beijing" in frs else frs[0])
            except Exception:
                _SIL.note("ui/dynamic_skin_tab.py", "探测后更新帧下拉")
            self.pick_var.set("%s: 共 %d 个文件(其中贴图 %d 张, 贴图才能拿去换静态图)"
                              % (no, len(suite), len(self._frames)))
            self._status("动态皮 %s 探测完: %d 个文件" % (no, len(suite)))

        threading.Thread(target=lambda: self._run(_do, _done), daemon=True).start()

    @staticmethod
    def _kb(n):
        n = int(n or 0)
        if n >= 1048576:
            return "%.1fMB" % (n / 1048576.0)
        return "%dKB" % max(0, round(n / 1024.0))

    # ------------------------------------------------ v4.139★ 预览: 换之前先看一眼
    def _on_frame_sel(self, _e=None):
        """在帧清单里点某一帧 → 把那张贴图显示出来。"""
        try:
            i = self.fr_lb.curselection()
        except Exception:
            return
        if not i or not self._frames:
            return
        f = self._frames[i[0]]
        self._preview(self.no_var.get(), f.get("file"))

    def _preview(self, no, file):
        """显示这一帧的缩略图。

        取图顺序: 本地已下过整套(dyn_skins/<no>/) → 没有就【只下这一张】。
        整套下载要拉十几个文件, 预览等不起; 单下一张通常几百 KB。
        注意: 动态皮的 png 是【atlas 图集】—— 所有切片排在同一张图上, 所以预览看到的
        是素材表, 不是成品画面(成品要靠 skel+atlas 一起渲染才有)。能看出"这套是谁 /
        什么风格", 这就够挑了。
        """
        no = "".join(ch for ch in str(no or "") if ch.isdigit())
        if not no or not file:
            return
        self._prev_var.set("预览 %s …" % file)

        def _do():
            from core.config import DATA_DIR
            from core import dyn_skin as DS
            p = os.path.join(DATA_DIR, "dyn_skins", no, str(file))
            if not os.path.isfile(p):
                try:
                    DS.download_suite(no, [str(file)])
                except Exception:
                    # 下载失败 → 后面会再判一次 isfile, 走"取不到就返回 None"。
                    # 记账: 不然"这个皮肤怎么一直不显示"查不到是下载被吞了。
                    _SIL.note("dynamic_skin_tab._do(%s/%s)" % (no, file),
                              "皮肤素材下载失败")
            if not os.path.isfile(p):
                return None
            try:
                from PIL import Image
                im = Image.open(p)
                im.thumbnail((240, 240))
                return (im, p)
            except Exception:
                return None

        def _done(res):
            if not res:
                self._prev_var.set("%s —— 预览不出来(没取到这个文件 / 不是图片)" % file)
                return
            im, p = res
            try:
                from PIL import ImageTk
                self._prev_im = im          # 留住引用 —— 被 GC 掉的话图就没了
                self._prev_img = ImageTk.PhotoImage(im)
                self._prev_lab.configure(image=self._prev_img, text="")
                self._prev_var.set("%s\n(%s · 缩略图)" % (file, os.path.basename(p)))
            except Exception as e:
                self._prev_var.set("预览失败: %s" % e)

        threading.Thread(target=lambda: self._run(_do, _done), daemon=True).start()

    # ---------------------------------------------------------------- ③ 换到哪
    def _build_slot(self, parent):
        box = tk.LabelFrame(parent, text=" ③ 拿选中帧的贴图, 换自走棋背景 / 主公棋子 ",
                            bg=self._bg(), fg=self._gold(), font=(self._fh(), 10, "bold"),
                            bd=1, relief="groove")
        box.pack(fill="x", padx=10, pady=(0, 6))

        r = tk.Frame(box, bg=self._bg())
        r.pack(fill="x", padx=6, pady=(6, 0))
        self.slot_kind = tk.StringVar(value="背景")
        for k in ("背景", "主公立绘", "棋子立绘"):
            tk.Radiobutton(r, text=k, value=k, variable=self.slot_kind,
                           command=self._load_slots, bg=self._bg(), fg=self._fg(),
                           selectcolor=self._btn(), activebackground=self._bg(),
                           font=(self._fh(), 9)).pack(side="left")
        tk.Label(r, text="(可多选, 一次给好几处配同一张图)", bg=self._bg(), fg=self._dim(),
                 font=(self._fh(), 8)).pack(side="left", padx=(8, 0))
        self.slot_var = tk.StringVar(value="")
        tk.Label(r, textvariable=self.slot_var, bg=self._bg(), fg=self._blue(),
                 font=(self._fh(), 8)).pack(side="right")

        self.slot_lb = tk.Listbox(box, height=9, selectmode=tk.MULTIPLE, bg=self._btn(),
                                  fg=self._fg(), font=(self._fh(), 9),
                                  selectbackground=self._act(), exportselection=False)
        self.slot_lb.pack(fill="x", padx=6, pady=(4, 0))

        r2 = tk.Frame(box, bg=self._bg())
        r2.pack(fill="x", padx=6, pady=6)
        tk.Button(r2, text="配上(选中帧 → 选中落点)", command=self._apply_slot,
                  bg=self._act(), fg=self._gold(), font=(self._fh(), 9, "bold"), padx=10,
                  cursor="hand2").pack(side="left")
        tk.Label(r2, text="换完是【动态皮的高清图】, 不是动画 —— 这两处本身是静态 png",
                 bg=self._bg(), fg=self._dim(), font=(self._fh(), 8)).pack(side="left", padx=(8, 0))
        # v4.139★ 落点也能扫: 上面那几个类别(背景/主公立绘/棋子立绘)是【配置表里写死的】,
        #   游戏里实际还有一堆能贴静态图的位置(头像框/血条底/技能图标/…), 表里面没有。
        tk.Button(r2, text="从游戏页扫落点 …", command=self._scan_slots,
                  bg=self._btn(), fg=self._blue(), font=(self._fh(), 9),
                  padx=8, cursor="hand2").pack(side="left", padx=(12, 0))

        self._load_slots()

    def _load_slots(self):
        from core import dyn_skin as DS
        kind = self.slot_kind.get()
        try:
            self._slots = DS.slot_options(kinds=[kind])
        except Exception as e:      # noqa: BLE001
            self._slots = []
            self.slot_var.set("落点读不出来: %s" % e)
            return
        self.slot_lb.delete(0, "end")
        for s in self._slots:
            self.slot_lb.insert("end", "  %-34s %s" % (s.get("name") or "", s.get("note") or ""))
        self.slot_var.set("%d 个落点" % len(self._slots))

    def _apply_slot(self):
        fi = self.fr_lb.curselection()
        if not fi:
            messagebox.showinfo("配上", "先在上面选一张贴图(只能选 png)。", parent=self.root)
            return
        frame = self._frames[fi[0]]
        picks = [self._slots[i] for i in self.slot_lb.curselection() if 0 <= i < len(self._slots)]
        if not picks:
            messagebox.showinfo("配上", "下面选一下要换到哪(可多选)。", parent=self.root)
            return
        from core import dyn_skin as DS
        no = "".join(ch for ch in self.no_var.get() if ch.isdigit())
        rules = [DS.rule_for_slot(s, frame, no) for s in picks]
        total, added, covered = DS.apply_slot_rules(rules)
        self._status("动态皮贴图 → %d 个落点: 新建 %d / 覆盖 %d(共 %d 条规则) —— "
                     "记得点『立即同步到浏览器』, 再刷新一次游戏页"
                     % (len(picks), added, covered, total))
        messagebox.showinfo("配上", "已配 %d 条(新建 %d / 覆盖 %d)。\n\n"
                                    "换的是【%s】那张贴图 —— 静态高清图, 不是动画。\n"
                                    "生效: 资源替换页点『立即同步到浏览器』+ 刷新游戏页。"
                            % (len(picks), added, covered, frame.get("file")), parent=self.root)

    # ------------------------------------------------ v4.139★ ③ 落点也能从游戏页扫
    def _scan_slots(self):
        """扫游戏页: 把所有【贴了静态图的节点】列出来当落点。

        原来③只有三类(背景/主公立绘/棋子立绘), 来源是【配置表里写死的预设】; 游戏里实际
        还有一堆能贴静态图的地方(头像框、血条底、技能图标…), 表里没有, 以前只能一个个猜
        pattern 去试。现在跟资源替换页一个路子: 直接问游戏页现在哪些节点贴了图。
        """
        ops = getattr(self.app, "ops", None)
        if ops is None or not hasattr(ops, "run_console_js"):
            messagebox.showinfo("从游戏页扫落点",
                                "助手还没连上游戏页(取不到 ops)。\n"
                                "先在游戏里进到对局, 再回来点这个按钮。", parent=self.root)
            return
        self.slot_var.set("正在扫游戏页里贴了图的节点 …")

        def _do():
            return ops.run_console_js(SCAN_SLOT_JS, timeout=12.0)

        def _done(raw):
            try:
                items = json.loads(str(raw or "[]"))
            except Exception:
                items = []
            if not items:
                self.slot_var.set("没扫到(没连上 / 还没进对局)")
                messagebox.showinfo("从游戏页扫落点",
                                    "没扫到 —— 要在【对局里】才扫得到(大厅里没这些东西)。\n\n"
                                    "回包: %s" % str(raw)[:140], parent=self.root)
                return
            self.slot_var.set("扫到 %d 处" % len(items))
            self._slot_scan_picker(items)

        threading.Thread(target=lambda: self._run(_do, _done), daemon=True).start()

    def _slot_scan_picker(self, items):
        """扫到的落点清单(可多选 + 可搜): 类名 / 节点名 / 尺寸 / 贴图路径。"""
        win = tk.Toplevel(self.root)
        win.title("游戏里贴了图的位置(共 %d) —— 勾选要换的" % len(items))
        win.configure(bg=self._bg())
        win.geometry("900x480")
        tk.Label(win, text="勾上要换的那些(已按面积从大到小排, 大图在前面)。搜一下更快:",
                 bg=self._bg(), fg=self._fg(), font=(self._fh(), 9)).pack(anchor="w", padx=8, pady=(8, 2))
        kw = tk.StringVar(value="")
        tk.Entry(win, textvariable=kw, bg=self._btn(), fg=self._fg(),
                 insertbackground=self._fg(), font=(self._fh(), 9)).pack(fill="x", padx=8)
        lb = tk.Listbox(win, selectmode="multiple", bg=self._btn(), fg=self._fg(),
                        font=(self._fh(), 9), selectbackground=self._act(),
                        exportselection=False)
        lb.pack(fill="both", expand=True, padx=8, pady=6)
        shown = []

        def _fill(*_a):
            k = kw.get().strip().lower()
            lb.delete(0, "end")
            shown[:] = [x for x in items
                        if (not k or k in str(x.get("cls") or "").lower()
                            or k in str(x.get("name") or "").lower()
                            or k in str(x.get("tex") or "").lower())]
            for x in shown:
                lb.insert("end", "  %-28s %-12s %10s  %s"
                          % (str(x.get("cls") or "?")[:28],
                             str(x.get("name") or "")[:12],
                             "%sx%s" % (x.get("w"), x.get("h")),
                             str(x.get("tex") or "")))

        try:
            kw.trace_add("write", _fill)
        except Exception:
            _SIL.note("ui/dynamic_skin_tab.py", "落点扫描: kw.trace_add")
        _fill()

        def _ok(_e=None):
            picks = [shown[i] for i in lb.curselection() if i < len(shown)]
            if not picks:
                win.destroy()
                return
            try:
                from core import resource_rules as RR
            except Exception:
                RR = None
            for x in picks:
                tex = str(x.get("tex") or "")
                pat = ""
                if RR is not None:
                    try:
                        pat = RR.relative_pattern(tex) or ""
                    except Exception:
                        pat = ""
                nm = "[扫] %s %sx%s" % (x.get("cls") or "?", x.get("w"), x.get("h"))
                self._slots.append({
                    "kind": "扫描", "name": nm, "pattern": pat or tex,
                    "url": tex, "note": (x.get("name") or x.get("cls") or ""),
                    "current": True,
                })
                self.slot_lb.insert("end", "  %-34s %s"
                                    % (nm, str(x.get("tex") or "")[-46:]))
            win.destroy()
            self.slot_var.set("已加入 %d 个扫出来的落点(共 %d 个) —— 选好帧再点『配上』"
                              % (len(picks), len(self._slots)))

        tk.Button(win, text="用这些", command=_ok, bg=self._act(), fg=self._gold(),
                  font=(self._fh(), 9, "bold"), padx=10).pack(pady=(0, 8))
        lb.bind("<Double-1>", _ok)

    # ---------------------------------------------------------------- ④ 整套映射
    def _build_map(self, parent):
        box = tk.LabelFrame(parent, text=" ④ 整套映射(进阶, 平时用不上) ",
                            bg=self._bg(), fg=self._gold(), font=(self._fh(), 10, "bold"),
                            bd=1, relief="groove")
        box.pack(fill="x", padx=10, pady=(0, 6))
        # v4.139★ 说清楚"我到底需不需要这一块" —— 用户原话: "④整套映射没有意义啊, 我大概
        #   不需要皮肤换皮肤?"。答: 多数情况【不需要】。想播哪套, 在②填它的编号、①开开关
        #   就够了; 这一块解决的是另一件事 —— 游戏自己要去加载 A 套时, 把地址偷偷指到 B 套
        #   (比如游戏硬编码的某个武将/皮肤必须存在, 你又想让它显示成另一套)。
        tk.Label(box, text=(
            "★ 多数人不用这一块: 想播哪套就在②填它的编号、①里开开关, 一步到位。\n"
            "这一块干的是【另一件事】: 游戏自己要去加载 A 套时, 把资源地址指到 B 套 —— "
            "只有当你想改的是【游戏主动加载的那套】(换掉它长什么样)才用得上; "
            "想加一套新的动态皮, 用①+②就行。"),
                 bg=self._bg(), fg=self._dim(), font=(self._fh(), 8),
                 justify="left", wraplength=960).pack(fill="x", padx=8, pady=(6, 2))

        r = tk.Frame(box, bg=self._bg())
        r.pack(fill="x", padx=6, pady=6)
        tk.Label(r, text="现在这套(源):", bg=self._bg(), fg=self._fg(),
                 font=(self._fh(), 9)).pack(side="left")
        self.src_var = tk.StringVar(value="")
        tk.Entry(r, textvariable=self.src_var, width=16, bg=self._btn(), fg=self._fg(),
                 insertbackground=self._fg(), font=(self._fh(), 9)).pack(side="left", padx=(2, 10))
        tk.Label(r, text="要换成(目标):", bg=self._bg(), fg=self._fg(),
                 font=(self._fh(), 9)).pack(side="left")
        self.dst_var = tk.StringVar(value="")
        tk.Entry(r, textvariable=self.dst_var, width=16, bg=self._btn(), fg=self._fg(),
                 insertbackground=self._fg(), font=(self._fh(), 9)).pack(side="left", padx=(2, 10))
        tk.Button(r, text="用①里的编号当源", command=lambda: self.src_var.set(
            "".join(ch for ch in self.no_var.get() if ch.isdigit())),
            bg=self._btn(), fg=self._dim(), font=(self._fh(), 8), cursor="hand2").pack(side="left")

        r2 = tk.Frame(box, bg=self._bg())
        r2.pack(fill="x", padx=6, pady=(0, 6))
        tk.Button(r2, text="探测两边", command=self._map_probe, bg=self._btn(), fg=self._blue(),
                  font=(self._fh(), 9), padx=10, cursor="hand2").pack(side="left")
        tk.Button(r2, text="一键配上(整套)", command=self._map_apply, bg=self._act(),
                  fg=self._gold(), font=(self._fh(), 9, "bold"), padx=10,
                  cursor="hand2").pack(side="left", padx=(6, 0))
        self.map_var = tk.StringVar(value="源里每个文件 → 目标的同名文件, 三件套同源才自洽")
        tk.Label(r2, textvariable=self.map_var, bg=self._bg(), fg=self._dim(),
                 font=(self._fh(), 8)).pack(side="left", padx=(8, 0))

    def _map_probe(self):
        src = "".join(ch for ch in self.src_var.get() if ch.isdigit())
        dst = "".join(ch for ch in self.dst_var.get() if ch.isdigit())
        if not src or not dst:
            messagebox.showinfo("整套映射", "两个编号都要填。", parent=self.root)
            return
        if src == dst:
            messagebox.showinfo("整套映射", "源和目标一样, 换了等于没换。", parent=self.root)
            return
        from core import dyn_skin as DS
        self.map_var.set("正在问官方 …")
        self._set_busy(True)

        def _do():
            s = DS.probe_suite(src)
            d = DS.probe_suite(dst, files=[x["file"] for x in s])
            return (s, {x["file"]: x for x in d})

        def _done(res):
            self._set_busy(False)
            if not res:
                self.map_var.set("探测失败")
                return
            s, dmap = res
            self._map_rows = [x["file"] for x in s if x["file"] in dmap]
            miss = [x["file"] for x in s if x["file"] not in dmap]
            self.map_var.set("源 %d 个文件 / 目标有 %d 个同名 —— 会配上 %d 条%s"
                             % (len(s), len(self._map_rows), len(self._map_rows),
                                ("(目标缺 %d 个: %s)" % (len(miss), "、".join(miss[:3]))
                                 if miss else "")))

        threading.Thread(target=lambda: self._run(_do, _done), daemon=True).start()

    def _map_apply(self):
        src = "".join(ch for ch in self.src_var.get() if ch.isdigit())
        dst = "".join(ch for ch in self.dst_var.get() if ch.isdigit())
        if not self._map_rows:
            messagebox.showinfo("整套映射", "先点「探测两边」。", parent=self.root)
            return
        from core import dyn_skin as DS
        total, added, covered = DS.apply_suite(src, dst, self._map_rows)
        self._status("动态皮整套 %s → %s: 新建 %d / 覆盖 %d(共 %d 条) —— 记得同步 + 刷新游戏页"
                     % (src, dst, added, covered, total))
        messagebox.showinfo("整套映射",
                            "已配 %d 条(新建 %d / 覆盖 %d)。\n\n三件套同源, 出来就是另一套完整动画。\n"
                            "生效: 资源替换页点『立即同步到浏览器』+ 刷新游戏页。"
                            % (len(self._map_rows), added, covered), parent=self.root)

    # ------------------------------------------------- ⑤ 动态背景: 三件套播真动画
    def _build_dynbg(self, parent):
        """v4.113★ 把官方 Spine 三件套放到【战场背景】那个位置去播。

        为什么不能只换一张 png(用户原话: "是静态的且是他的动态帧的那个插画图, 动态是在它
          上面取一部分, 都不是底图"): 动态皮的 png 是【atlas 图集】—— 所有切片排在同一张
          图上, 动画 = 渲染器按 atlas 坐标抠 + 按 skel 时间轴播。只换 png 得到的是整张
          素材表: 静态, 而且根本不是底图。
        """
        # v4.139★ 提到【最上面】当 ① —— 这一块是天天要动的(人物背景要不要、透明不透明、
        #   上衣脱不脱), 原来排在第 ⑤, 改一次要滚到页尾。编号跟着新顺序重排。
        box = tk.LabelFrame(parent, text=" ① 动态皮肤怎么播: 人物背景 / 透明 / 图层 / 取景(最常用) ",
                            bg=self._bg(), fg=self._gold(), font=(self._fh(), 10, "bold"),
                            bd=1, relief="groove")
        box.pack(fill="x", padx=10, pady=(0, 6))
        tk.Label(box, text=(
            "只换一张 png = 得到 atlas 图集原图(切片素材表, 静态, 也不是底图)。真动画必须把 "
            "skel+atlas+png 三件套一起交给 Spine 渲染器 —— 落法: 隐藏游戏那张背景图 → 画布清成 "
            "透明 → 在游戏画布【下层】播官方动画; 棋盘棋子仍在 canvas 上层, 不会被遮住。"),
                 bg=self._bg(), fg=self._dim(), font=(self._fh(), 8),
                 justify="left", wraplength=960).pack(fill="x", padx=8, pady=(6, 2))

        r = tk.Frame(box, bg=self._bg())
        r.pack(fill="x", padx=6, pady=4)
        self._dbg_on = tk.BooleanVar(value=False)
        tk.Checkbutton(r, text="启用动态背景", variable=self._dbg_on, command=self._dbg_toggle,
                       bg=self._bg(), fg=self._fg(), selectcolor=self._btn(),
                       activebackground=self._bg(),
                       font=(self._fh(), 9, "bold")).pack(side="left")
        tk.Label(r, text="帧:", bg=self._bg(), fg=self._fg(),
                 font=(self._fh(), 9)).pack(side="left", padx=(12, 2))
        # v4.139★ 帧改成下拉: 探测完②之后填进这套【真实存在】的帧, 而不是写死两个选项
        #   (这套可能根本没有 daiji, 只有 beijing; 也可能有 daiji1/daiji2 好几个待机帧)。
        self._dbg_frame = tk.StringVar(value="beijing")
        self._dbg_frame_cb = ttk.Combobox(r, textvariable=self._dbg_frame, width=14,
                                          values=["beijing", "daiji"], state="normal",
                                          font=(self._fh(), 9))
        self._dbg_frame_cb.pack(side="left")
        tk.Label(r, text="(探测完②会列出这套真实有的帧)", bg=self._bg(), fg=self._dim(),
                 font=(self._fh(), 8)).pack(side="left", padx=(6, 0))

        tk.Label(box, text=(
            "帧 = 这套皮里的【一整套三件套】, 一个帧一个文件组: "
            "beijing = 底图(整张场景背景, 铺满当背景用); "
            "daiji* = 待机帧(人物站着不动那个动作, 当人物用); "
            "别的(attack/spell/…)是出招、施法这类动作帧。"),
                 bg=self._bg(), fg=self._dim(), font=(self._fh(), 8),
                 justify="left", wraplength=960).pack(fill="x", padx=8, pady=(2, 4))

        # v4.139★ 原来叫"要顶掉的背景(匹配串)" —— 名字误导: 它其实决定【人物/底图插在哪一层】,
        #   不是顶掉谁; 而且默认值是牌桌那两条(ChessTableScene/newStyleTableDeckBg), 自走棋里
        #   根本匹配不上(实测自走棋那层叫 TavernChessGameScene) —— 表现就是"明明开了却不播"。
        r1b = tk.Frame(box, bg=self._bg())
        r1b.pack(fill="x", padx=6, pady=(0, 4))
        tk.Label(r1b, text="插到这一层(类名/贴图名):", bg=self._bg(), fg=self._fg(),
                 font=(self._fh(), 9)).pack(side="left")
        self._dbg_pat = tk.StringVar(value=DEFAULT_PAT)
        tk.Entry(r1b, textvariable=self._dbg_pat, width=34, bg=self._btn(), fg=self._fg(),
                 insertbackground=self._fg(), font=(self._fh(), 9)).pack(side="left", padx=(2, 6))
        tk.Button(r1b, text="从游戏页扫 …", command=self._dbg_scan_host,
                  bg=self._btn(), fg=self._blue(), font=(self._fh(), 8),
                  padx=6, cursor="hand2").pack(side="left")
        tk.Label(r1b, text="(点『扫』= 列出游戏里现有的层让你挑, 不用手打、不用一个个试)",
                 bg=self._bg(), fg=self._dim(), font=(self._fh(), 8)).pack(side="left", padx=(8, 0))

        r2 = tk.Frame(box, bg=self._bg())
        r2.pack(fill="x", padx=6, pady=(0, 6))
        tk.Button(r2, text="用下面②的编号启用(自动补齐三件套)", command=self._dbg_apply,
                  bg=self._act(), fg=self._gold(), font=(self._fh(), 9, "bold"),
                  padx=10, cursor="hand2").pack(side="left")
        self._dbg_var = tk.StringVar(value="")
        tk.Label(r2, textvariable=self._dbg_var, bg=self._bg(), fg=self._dim(),
                 font=(self._fh(), 8)).pack(side="left", padx=(10, 0))

        # v4.138★『人物背景』开关 —— 骨架自带的 beijing 底图要不要一起播。
        #   不勾(默认): 只播人物, 背景就用游戏原图(节日背景那张), 观感最干净;
        #   勾上:      连底图一起播 —— 底图是整块不透明的, 会把游戏原背景整个盖住。
        r2b = tk.Frame(box, bg=self._bg())
        r2b.pack(fill="x", padx=6, pady=(0, 6))
        self._dbg_usebg = tk.BooleanVar(value=False)
        tk.Checkbutton(r2b, text="人物背景", variable=self._dbg_usebg,
                       command=self._dbg_usebg_save,
                       bg=self._bg(), fg=self._fg(), selectcolor=self._btn(),
                       activebackground=self._bg(), font=(self._fh(), 9),
                       cursor="hand2").pack(side="left")
        tk.Label(r2b, text="(勾上=连骨架自带的底图一起播, 会盖住游戏原背景; 不勾=只播人物)",
                 bg=self._bg(), fg=self._dim(), font=(self._fh(), 8)).pack(side="left", padx=(6, 0))

        # v4.139★ 背景设透明(摘掉游戏原背景) + 底图放大倍数
        #   透明: 原来只有控制台能开(__sgsDynSk.stripBg(true)), 助手端没有入口 ——
        #     用户要"那个位置还能换图/设透明"时就只能去 F12 手敲, 现在这里直接勾。
        #   放大: 底图是动画, bounds 每帧都在晃, 按 1 倍算会露边 —— 默认 1.25(脚本同值)。
        r2c = tk.Frame(box, bg=self._bg())
        r2c.pack(fill="x", padx=6, pady=(0, 6))
        self._dbg_strip = tk.BooleanVar(value=False)
        tk.Checkbutton(r2c, text="背景设透明", variable=self._dbg_strip,
                       command=self._dbg_strip_save,
                       bg=self._bg(), fg=self._fg(), selectcolor=self._btn(),
                       activebackground=self._bg(), font=(self._fh(), 9),
                       cursor="hand2").pack(side="left")
        tk.Label(r2c, text="(摘掉游戏原背景那张, 让动态皮直接当底 —— 想换图/留空就靠它)",
                 bg=self._bg(), fg=self._dim(), font=(self._fh(), 8)).pack(side="left", padx=(6, 14))
        tk.Label(r2c, text="底图放大:", bg=self._bg(), fg=self._fg(),
                 font=(self._fh(), 9)).pack(side="left")
        self._dbg_bgzoom = tk.StringVar(value="1.25")
        tk.Entry(r2c, textvariable=self._dbg_bgzoom, width=7, bg=self._btn(), fg=self._fg(),
                 insertbackground=self._fg(), font=(self._fh(), 9)).pack(side="left", padx=(2, 6))
        tk.Button(r2c, text="应用", command=self._dbg_bgzoom_save,
                  bg=self._btn(), fg=self._blue(), font=(self._fh(), 9),
                  padx=8, cursor="hand2").pack(side="left")

        # v4.139★ 隐藏骨架图层(脱上衣 / 袖子这类)
        #   图层名每套皮不一样(有的是 shangyi, 有的叫 shangyi1), 所以给「查询图层名」按钮 ——
        #   直接问游戏页当前这套有哪些 slot, 列出来让用户挑, 不用猜。
        r2d = tk.Frame(box, bg=self._bg())
        r2d.pack(fill="x", padx=6, pady=(0, 6))
        tk.Label(r2d, text="隐藏图层:", bg=self._bg(), fg=self._fg(),
                 font=(self._fh(), 9)).pack(side="left")
        self._dbg_layers = tk.StringVar(value="")
        tk.Entry(r2d, textvariable=self._dbg_layers, width=42, bg=self._btn(), fg=self._fg(),
                 insertbackground=self._fg(), font=(self._fh(), 9)).pack(side="left", padx=(2, 6))
        tk.Button(r2d, text="查询图层名 …", command=self._dbg_slots,
                  bg=self._btn(), fg=self._blue(), font=(self._fh(), 8),
                  padx=6, cursor="hand2").pack(side="left")
        tk.Button(r2d, text="应用", command=self._dbg_layers_save,
                  bg=self._act(), fg=self._gold(), font=(self._fh(), 9),
                  padx=8, cursor="hand2").pack(side="left", padx=(6, 0))
        tk.Label(r2d, text="(逗号分隔, 例 shangshen,piaodai = 脱上身+飘带; 名字每套都不一样, "
                           "先点『查询图层名』看这套有哪些; 留空 = 全显示)",
                 bg=self._bg(), fg=self._dim(), font=(self._fh(), 8)).pack(side="left", padx=(8, 0))

        # v4.139★ 立刻生效: 上面这些保存后要等脚本 15 秒轮询才落地, 改一个参数干等很难受
        r2e = tk.Frame(box, bg=self._bg())
        r2e.pack(fill="x", padx=6, pady=(0, 6))
        tk.Button(r2e, text="立刻生效(不等 15 秒)", command=self._dbg_push_now,
                  bg=self._act(), fg=self._gold(), font=(self._fh(), 9, "bold"),
                  padx=10, cursor="hand2").pack(side="left")
        tk.Label(r2e, text="(直接把开关下发给游戏页; 要游戏页已连上助手, 没连上会提示)",
                 bg=self._bg(), fg=self._dim(), font=(self._fh(), 8)).pack(side="left", padx=(8, 0))

        # v4.127★ 取景参数: 官方动态皮是"角色 + 背景"整幅立绘, 直接铺上去角色会盖住棋盘。
        #   放大(scale>1) + 偏移(x/y) 就能只取背景那一片, 把角色挪出可视区。
        r3 = tk.Frame(box, bg=self._bg())
        r3.pack(fill="x", padx=6, pady=(0, 6))
        tk.Label(r3, text="缩放:", bg=self._bg(), fg=self._fg(),
                 font=(self._fh(), 9)).pack(side="left")
        self._dbg_scale = tk.StringVar(value="1.0")
        tk.Entry(r3, textvariable=self._dbg_scale, width=7, bg=self._btn(), fg=self._fg(),
                 insertbackground=self._fg(), font=(self._fh(), 9)).pack(side="left", padx=(2, 10))
        tk.Label(r3, text="X偏移:", bg=self._bg(), fg=self._fg(),
                 font=(self._fh(), 9)).pack(side="left")
        self._dbg_x = tk.StringVar(value="0")
        tk.Entry(r3, textvariable=self._dbg_x, width=7, bg=self._btn(), fg=self._fg(),
                 insertbackground=self._fg(), font=(self._fh(), 9)).pack(side="left", padx=(2, 10))
        tk.Label(r3, text="Y偏移:", bg=self._bg(), fg=self._fg(),
                 font=(self._fh(), 9)).pack(side="left")
        self._dbg_y = tk.StringVar(value="0")
        tk.Entry(r3, textvariable=self._dbg_y, width=7, bg=self._btn(), fg=self._fg(),
                 insertbackground=self._fg(), font=(self._fh(), 9)).pack(side="left", padx=(2, 10))
        tk.Button(r3, text="应用取景", command=self._dbg_view,
                  bg=self._btn(), fg=self._blue(), font=(self._fh(), 9),
                  padx=8, cursor="hand2").pack(side="left")
        tk.Label(r3, text="(放大可把角色挤出画面, 只留背景)",
                 bg=self._bg(), fg=self._dim(), font=(self._fh(), 8)).pack(side="left", padx=(8, 0))

        self._dbg_load()

    def _dbg_cfg(self):
        try:
            return dict((getattr(self.app, "cfg", None) or {}).get("dyn_bg") or {})
        except Exception:
            return {}

    def _dbg_load(self):
        d = self._dbg_cfg()
        self._dbg_on.set(bool(d.get("on")))
        if d.get("frame"):
            self._dbg_frame.set(str(d["frame"]))
        if d.get("pattern"):
            self._dbg_pat.set(str(d["pattern"]))
        # v4.127★ 取景参数
        self._dbg_scale.set(str(d.get("scale") or 1.0))
        self._dbg_x.set(str(d.get("x") or 0))
        self._dbg_y.set(str(d.get("y") or 0))
        if d.get("no"):
            self._dbg_var.set("当前: %s / %s" % (d.get("no"), d.get("frame") or "beijing"))
        # v4.138★『人物背景』开关
        self._dbg_usebg.set(bool(d.get("useBg", False)))
        # v4.139★ ① 里那三个新开关
        self._dbg_strip.set(bool(d.get("stripBg", False)))
        self._dbg_bgzoom.set(str(d.get("bgZoom") or 1.25))
        self._dbg_layers.set(",".join(str(x) for x in (d.get("hideLayers") or [])))

    def _dbg_usebg_save(self):
        """v4.138★『人物背景』开关: 勾上 = 连骨架自带的 beijing 底图一起播。"""
        from core.config import save_config
        d = self._dbg_cfg()
        d["useBg"] = bool(self._dbg_usebg.get())
        self.app.cfg["dyn_bg"] = d
        save_config(self.app.cfg)
        self._dbg_var.set("人物背景已%s(脚本 15 秒内生效)"
                          % ("开 —— 自带底图会盖住游戏原背景" if d["useBg"] else "关 —— 只播人物"))

    # ------------------------------------------------ v4.139★ ① 里的三个新开关
    def _dbg_strip_save(self):
        """背景设透明 = 摘掉游戏原背景那张图(脚本 stripBg)。

        为什么归这一页管: 用户要的是"那个位置不要游戏自带的底" —— 摘掉之后, 底下露出
        的就是动态皮(或者资源替换页配的那张图), 换图/留空才有意义。
        """
        from core.config import save_config
        d = self._dbg_cfg()
        d["stripBg"] = bool(self._dbg_strip.get())
        self.app.cfg["dyn_bg"] = d
        save_config(self.app.cfg)
        self._dbg_var.set("背景设透明已%s(脚本 15 秒内生效)"
                          % ("开 —— 摘掉游戏原背景, 动态皮直接当底"
                             if d["stripBg"] else "关 —— 保留游戏原背景"))

    def _dbg_bgzoom_save(self):
        """底图放大倍数(脚本 bgZoom)。

        默认 1.25: 底图是动画, self bounds 每一帧都在晃, 正好按 1 倍算完, 动画一动
        边缘就会露出游戏原背景(实测能露 70 多像素)。放大一点就盖住了。
        """
        from core.config import save_config
        v = str(self._dbg_bgzoom.get() or "").strip()
        try:
            z = float(v)
        except Exception:
            messagebox.showinfo("底图放大", "填个数字(例 1.25 = 在自动铺满之上再放大 25%)。",
                                parent=self.root)
            return
        if not (0.5 <= z <= 3.0):
            messagebox.showinfo("底图放大", "建议在 0.5~3.0 之间 —— 太大底图会被裁得只剩中间一块。",
                                parent=self.root)
            return
        d = self._dbg_cfg()
        d["bgZoom"] = z
        self.app.cfg["dyn_bg"] = d
        save_config(self.app.cfg)
        self._dbg_var.set("底图放大 %s 已保存(脚本 15 秒内生效)" % z)

    def _dbg_layers_save(self):
        """隐藏骨架图层(脱上衣 / 袖子这类), 名字逗号分隔。"""
        from core.config import save_config
        raw = str(self._dbg_layers.get() or "")
        names = [x.strip() for x in re.split(r"[,，|\s]+", raw) if x.strip()]
        d = self._dbg_cfg()
        d["hideLayers"] = names
        self.app.cfg["dyn_bg"] = d
        save_config(self.app.cfg)
        self._dbg_var.set("隐藏图层已保存: %s(脚本 15 秒内生效)"
                          % ("、".join(names) if names else "(空 = 全显示)"))

    def _dbg_scan_host(self):
        """扫游戏页: 把"可以插动态皮的层"列出来让用户挑 —— 不用手填、不用一个个试。

        用户原话: "其他位置也要一个个找吗? 慢慢换, 看合不合适吗? 例如资源替换那些从网页
        找出来的"。对, 就该跟资源替换页『扫描游戏页资源』一个路子: 直接问游戏页现在有哪些
        层, 列出来挑, 而不是让人猜类名。
        """
        ops = getattr(self.app, "ops", None)
        if ops is None or not hasattr(ops, "run_console_js"):
            messagebox.showinfo("从游戏页扫",
                                "助手还没连上游戏页(取不到 ops)。\n"
                                "先在游戏里进到对局, 再回来点这个按钮。", parent=self.root)
            return
        self._dbg_var.set("正在扫游戏页里有哪些层 …")

        def _do():
            return ops.run_console_js(SCAN_HOST_JS, timeout=10.0)

        def _done(raw):
            try:
                items = json.loads(str(raw or "[]"))
            except Exception:
                items = []
            if not items:
                self._dbg_var.set("没扫到(没连上 / 还没进对局)")
                messagebox.showinfo("从游戏页扫",
                                    "没扫到候选层 —— 要在【对局里】才扫得到"
                                    "(大厅里没有自走棋场景)。\n\n回包: %s" % str(raw)[:140],
                                    parent=self.root)
                return
            self._dbg_var.set("扫到 %d 个候选层" % len(items))
            self._host_picker(items)

        threading.Thread(target=lambda: self._run(_do, _done), daemon=True).start()

    def _host_picker(self, items):
        """候选层清单: 类名 / 尺寸 / 它画的那张图。双击一行就用它。"""
        win = tk.Toplevel(self.root)
        win.title("游戏里现有的层(共 %d) —— 选一个插动态皮" % len(items))
        win.configure(bg=self._bg())
        win.geometry("720x420")
        tk.Label(win, text="双击一行用它。拿不准就挑【尺寸最大的那个 / 名字带 Scene 的那个】:",
                 bg=self._bg(), fg=self._fg(), font=(self._fh(), 9)).pack(anchor="w", padx=8, pady=(8, 2))
        lb = tk.Listbox(win, bg=self._btn(), fg=self._fg(), font=(self._fh(), 9),
                        selectbackground=self._act(), exportselection=False)
        lb.pack(fill="both", expand=True, padx=8, pady=6)
        for it in items:
            lb.insert("end", "  %-36s %11s  %s"
                      % (str(it.get("cls") or "?"),
                         "%sx%s" % (it.get("w"), it.get("h")),
                         str(it.get("tex") or "")))

        def _use(_e=None):
            i = lb.curselection()
            if not i:
                return
            cls = str(items[i[0]].get("cls") or "")
            self._dbg_pat.set(cls)
            win.destroy()
            # 已经在播: 直接存; 没在播: 也存, 下次开就按这个来
            if self._dbg_on.get():
                self._dbg_toggle()
            else:
                self._dbg_pat_save()

        tk.Button(win, text="用这个", command=_use, bg=self._act(), fg=self._gold(),
                  font=(self._fh(), 9, "bold"), padx=10).pack(pady=(0, 8))
        lb.bind("<Double-1>", _use)

    def _dbg_pat_save(self):
        """只保存"插到哪一层", 不动开关。"""
        from core.config import save_config
        d = self._dbg_cfg()
        d["pattern"] = self._dbg_pat.get().strip() or DEFAULT_PAT
        self.app.cfg["dyn_bg"] = d
        save_config(self.app.cfg)
        self._dbg_var.set("插的层已保存: %s(下次启用时生效)" % d["pattern"])

    def _dbg_slots(self):
        """问游戏页: 当前这套动态皮有哪些图层(slot) —— 列出来给用户勾, 不用猜名字。"""
        ops = getattr(self.app, "ops", None)
        if ops is None or not hasattr(ops, "run_console_js"):
            messagebox.showinfo("查询图层名", "助手还没连上游戏页(取不到 ops)。",
                                parent=self.root)
            return
        self._dbg_var.set("正在问游戏页: 这套有哪些图层 …")

        def _do():
            return ops.run_console_js("__sgsDynSk.slotNames()", timeout=8.0)

        def _done(raw):
            try:
                names = json.loads(str(raw or "[]"))
            except Exception:
                names = []
            if not names:
                self._dbg_var.set("没拿到图层名")
                messagebox.showinfo(
                    "查询图层名",
                    "没拿到 —— 要么游戏页没连上助手, 要么此刻没在播动态皮\n"
                    "(要在对局里、并且①的开关是开的才有图层可查)。\n\n回包: %s"
                    % str(raw)[:160], parent=self.root)
                return
            self._dbg_var.set("拿到 %d 个图层" % len(names))
            self._slot_picker(names)

        threading.Thread(target=lambda: self._run(_do, _done), daemon=True).start()

    def _slot_picker(self, names):
        """把图层名列成可多选的清单(勾上的 = 隐藏), 确定后填回输入框并保存。"""
        win = tk.Toplevel(self.root)
        win.title("图层(slot) 共 %d 个 —— 勾上的会被隐藏" % len(names))
        win.configure(bg=self._bg())
        win.geometry("520x460")
        tk.Label(win, text="勾上 = 隐藏这个图层(脱掉它)。搜一下更快:",
                 bg=self._bg(), fg=self._fg(), font=(self._fh(), 9)).pack(anchor="w", padx=8, pady=(8, 2))
        kw = tk.StringVar(value="")
        tk.Entry(win, textvariable=kw, bg=self._btn(), fg=self._fg(),
                 insertbackground=self._fg(), font=(self._fh(), 9)).pack(fill="x", padx=8)
        lb = tk.Listbox(win, selectmode="multiple", bg=self._btn(), fg=self._fg(),
                        font=(self._fh(), 9), selectbackground=self._act(),
                        exportselection=False)
        lb.pack(fill="both", expand=True, padx=8, pady=6)

        cur = [x.strip() for x in str(self._dbg_layers.get() or "").split(",") if x.strip()]
        shown = []

        def _fill(*_a):
            k = kw.get().strip().lower()
            lb.delete(0, "end")
            shown[:] = [x for x in names if (not k or k in str(x).lower())]
            for x in shown:
                lb.insert("end", "  " + str(x))
            for i, x in enumerate(shown):
                if x in cur:
                    lb.selection_set(i)

        try:
            kw.trace_add("write", _fill)
        except Exception:
            _SIL.note("ui/dynamic_skin_tab.py", "kw.trace_add —— 老 Tk 没有它, 退成手动刷新")
        _fill()

        def _ok():
            picks = [shown[i] for i in lb.curselection() if i < len(shown)]
            self._dbg_layers.set(",".join(picks))
            self._dbg_layers_save()
            win.destroy()

        tk.Button(win, text="用这些", command=_ok, bg=self._act(), fg=self._gold(),
                  font=(self._fh(), 9, "bold"), padx=10).pack(pady=(0, 8))

    def _dbg_push_now(self):
        """把①里这些开关【立刻】下发给游戏页 —— 不用等脚本下一拍 15 秒轮询。"""
        ops = getattr(self.app, "ops", None)
        if ops is None or not hasattr(ops, "run_console_js"):
            messagebox.showinfo("立刻生效", "助手还没连上游戏页(取不到 ops)。", parent=self.root)
            return
        d = self._dbg_cfg()
        try:
            z = float(d.get("bgZoom") or 1.25)
        except Exception:
            z = 1.25
        js = ("(function(){var r=[];"
              "try{r.push('人物背景:'+__sgsDynSk.useBg(%s));}catch(e){r.push('人物背景 ERR');}"
              "try{r.push('透明:'+__sgsDynSk.stripBg(%s));}catch(e){r.push('透明 ERR');}"
              "try{r.push('底图放大:'+__sgsDynSk.bgZoom(%s));}catch(e){r.push('底图放大 ERR');}"
              "try{r.push('图层:'+__sgsDynSk.layers(%s));}catch(e){r.push('图层 ERR');}"
              "return r.join(' | ');})()") % (
            "true" if bool(d.get("useBg")) else "false",
            "true" if bool(d.get("stripBg")) else "false",
            str(z),
            json.dumps(list(d.get("hideLayers") or []), ensure_ascii=False),
        )
        self._dbg_var.set("正在下发给游戏页 …")

        def _do():
            return ops.run_console_js(js, timeout=8.0)

        def _done(raw):
            txt = "(无回包: 游戏页未连接/超时)" if raw is None else str(raw)
            self._dbg_var.set("已下发 → " + txt[:160])
            self._status("动态皮肤开关已下发: %s" % txt[:120])

        threading.Thread(target=lambda: self._run(_do, _done), daemon=True).start()

    def _dbg_view(self):
        """v4.127★ 保存取景参数(缩放 / X偏移 / Y偏移), 脚本 15 秒内自动生效。"""
        from core.config import save_config

        def _num(v, default):
            try:
                return float(str(v).strip())
            except Exception:
                return default

        d = self._dbg_cfg()
        d["scale"] = _num(self._dbg_scale.get(), 1.0) or 1.0
        d["x"] = _num(self._dbg_x.get(), 0)
        d["y"] = _num(self._dbg_y.get(), 0)
        self.app.cfg["dyn_bg"] = d
        save_config(self.app.cfg)
        self._dbg_var.set("取景已保存: 缩放%s / X%s / Y%s(脚本 15 秒内生效)"
                          % (d["scale"], d["x"], d["y"]))

    def _dbg_toggle(self):
        """单独拨开关 —— 没指定编号就拦下来(开了也没东西可播, 脚本只会报 missing)。"""
        from core.config import save_config
        d = self._dbg_cfg()
        if self._dbg_on.get() and not d.get("no"):
            self._dbg_on.set(False)
            messagebox.showinfo("动态背景",
                                "还没指定动态皮编号 —— 先在②填编号, 再点『用下面②的编号启用』。",
                                parent=self.root)
            return
        d["on"] = bool(self._dbg_on.get())
        d["frame"] = self._dbg_frame.get() or "beijing"
        d["pattern"] = self._dbg_pat.get().strip() or DEFAULT_PAT
        d["useBg"] = bool(self._dbg_usebg.get())
        self.app.cfg["dyn_bg"] = d
        save_config(self.app.cfg)
        self._dbg_var.set(("已开: %s/%s(脚本 15 秒内自动生效)" % (d.get("no"), d["frame"]))
                          if d["on"] else "已关")

    def _dbg_apply(self):
        """取①里的编号 → 补齐三件套到本地 → 写配置并开开关。"""
        from core import dyn_skin as DS
        from core.config import save_config, DATA_DIR
        no = "".join(ch for ch in (self.no_var.get() or "") if ch.isdigit())
        if not no:
            messagebox.showinfo("动态背景",
                                "先在②里填动态编号(例: 7133001 = 众乐和鸣*乐蔡文姬)。",
                                parent=self.root)
            return
        frame = self._dbg_frame.get() or "beijing"
        files = [frame + e for e in (".skel", ".atlas", ".png")]
        base_dir = os.path.join(DATA_DIR, "dyn_skins", no)
        self._dbg_var.set("检查三件套 …")
        self._set_busy(True)

        def _do():
            miss = [f for f in files if not os.path.isfile(os.path.join(base_dir, f))]
            if miss:
                DS.download_suite(no, miss)
            return [f for f in files if not os.path.isfile(os.path.join(base_dir, f))]

        def _done(still):
            self._set_busy(False)
            if still is None:
                self._dbg_var.set("下载失败 —— 网络能连官方服务器吗?")
                return
            if still:
                self._dbg_var.set("还缺: " + "、".join(still))
                messagebox.showwarning("动态背景",
                                       "这套的 %s 没取到(可能官方没有这一帧)。\n换个帧名再试。"
                                       % "、".join(still), parent=self.root)
                return
            d = self._dbg_cfg()
            d.update({"on": True, "no": no, "frame": frame,
                      "pattern": self._dbg_pat.get().strip() or DEFAULT_PAT,
                      "useBg": bool(self._dbg_usebg.get())})
            self.app.cfg["dyn_bg"] = d
            save_config(self.app.cfg)
            self._dbg_on.set(True)
            self._dbg_var.set("已启用 %s/%s —— 脚本 15 秒内自动生效(游戏页不用刷新)" % (no, frame))
            self._status("动态背景已启用: %s/%s(顶掉 %s)" % (no, frame, d["pattern"]))

        threading.Thread(target=lambda: self._run(_do, _done), daemon=True).start()
