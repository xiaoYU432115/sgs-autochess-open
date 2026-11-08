# -*- coding: utf-8 -*-
"""赛季更新 Tab(v4.76★) —— 把"换季要跑的那几条命令"做成按钮。

为什么单独一页
==============
『赛季数据』页是【换表工作台】(下载解析→查看→对比向导→推送/替换, 一步一步来);
这一页是【一键换季】: 官方更新了 → 检查 → 抓下来 → 写进现行数据, 全程点按钮,
外加两件换季时容易忘的事:
  · 只读体检(tools/season_check.py) —— 对照新配置表列出"该做没做"的事, 不改任何数据;
  · OL 皮肤表(core/dress_table.py) —— 资源替换页「从皮肤表挑(OL大图)」用的那张表,
    换季后新皮肤的编号全在里面, 联网抓一次 Config.sgs 就能跟上。

日志口径: 本页日志同时镜像到『赛季数据』页日志框和主日志(app._season_log 本来就
镜像主日志), 两边看哪边都行。season_config 的全局 logger 在这里接管 —— 它输出的
每一行两边都有, 不会丢。
"""
import contextlib
import io
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox

from tools import season_config as SC


from ui.widgets import ThemeMixin   # v4.109★ 主题色 getter 统一到 widgets.ThemeMixin


class SeasonUpdateTabBuilder(ThemeMixin):
    """『赛季更新』页 —— 与 ResourceTabBuilder 同一套构建模式(app 传进来, 不反向依赖)。"""

    def __init__(self, app):
        self.app = app
        self.root = app.root
        self._busy = False
        self._buttons = []          # busy 时统一禁用
        self._log_text = None

    # v4.109★ 主题色 getter 已统一到 widgets.ThemeMixin(本类继承它), 不再各写一份
    #   注: 原来这里的兜底色(BG #1e1e1e / FG #dddddd / BTN #2c2c2c / GREEN #7fd48f)
    #   与 app.py:315-325 的真实值并不一致, 现已统一到 app 那套。

    # ---------------------------------------------------------------- build
    def build(self, parent):
        SC.set_logger(self._log)    # season_config 的输出两边日志框都有(见模块头说明)

        try:
            cur = SC.current_season() or "10"
        except Exception:
            cur = "10"

        # 顶部: 赛季号 / 资源路径段
        top = tk.Frame(parent, bg=self._bg())
        top.pack(fill="x", padx=10, pady=(8, 4))
        tk.Label(top, text="赛季号:", bg=self._bg(), fg=self._fg(), font=(self._fh(), 9)).pack(side="left")
        self.season_var = tk.StringVar(value=cur)
        tk.Entry(top, textvariable=self.season_var, width=4, bg=self._btn(), fg=self._fg(),
                 insertbackground=self._fg(), font=(self._fh(), 9)).pack(side="left", padx=(2, 10))
        tk.Label(top, text="资源路径段:", bg=self._bg(), fg=self._fg(), font=(self._fh(), 9)).pack(side="left")
        self.version_var = tk.StringVar(value="")
        tk.Entry(top, textvariable=self.version_var, width=5, bg=self._btn(), fg=self._fg(),
                 insertbackground=self._fg(), font=(self._fh(), 9)).pack(side="left", padx=(2, 4))
        # v4.113★ 以前写"不填=同赛季号; 下赛季一般是 11" —— 那是错的: 路径段是十周年
        #   版本目录(恒为 10), /11/ 那类路径全是占位页。赛季号只用来筛 SeasonID。
        tk.Label(top, text="(十周年=10, 一般不动; 赛季号只用来筛 SeasonID)",
                 bg=self._bg(), fg=self._dim(), font=(self._fh(), 8)).pack(side="left")
        # v4.106★ "助手现在认的是哪个赛季"要写在脸上 —— 这个号决定白名单/"本季可用"口径,
        #   而它跟上面输入框里填的"要抓哪个赛季"是两回事(抓 S11 不代表已经切到 S11)。
        self.cur_var = tk.StringVar(value="")
        tk.Label(top, textvariable=self.cur_var, bg=self._bg(), fg=self._green(),
                 font=(self._fh(), 9)).pack(side="left", padx=(10, 0))
        self.status_var = tk.StringVar(value="准备就绪")
        tk.Label(top, textvariable=self.status_var, bg=self._bg(), fg=self._blue(),
                 font=(self._fh(), 9)).pack(side="right")

        # ---------------- ① 换季一键流 ----------------
        box1 = tk.LabelFrame(
            parent,
            text=" ① 换季一键流 (检查官方 → 抓配置 → 写进现行数据; 写之前自动整份备份) ",
            bg=self._bg(), fg=self._gold(), font=(self._fh(), 10, "bold"), bd=1, relief="groove")
        box1.pack(fill="x", padx=10, pady=(0, 6))

        r0 = tk.Frame(box1, bg=self._bg())
        r0.pack(fill="x", padx=6, pady=(6, 0))
        self._btn_add(r0, "⓪ 把上面这个赛季设为『当前赛季』", self.a_set_current, fg=self._green())
        tk.Label(r0, text="只改 data/season/season.json 那个标记(决定白名单/本季口径), 不动任何表",
                 bg=self._bg(), fg=self._dim(), font=(self._fh(), 8)).pack(side="left", padx=(4, 0))

        r1 = tk.Frame(box1, bg=self._bg())
        r1.pack(fill="x", padx=6, pady=6)
        self._btn_add(r1, "① 检查官方更新", self.a_check_update, fg=self._blue())
        tk.Label(r1, text="只发 HEAD 不下载, 看官方 Config.sgs 改过没 / 下赛季上线没",
                 bg=self._bg(), fg=self._dim(), font=(self._fh(), 8)).pack(side="left", padx=(4, 0))

        r2 = tk.Frame(box1, bg=self._bg())
        r2.pack(fill="x", padx=6, pady=(0, 6))
        self._btn_add(r2, "② 一键下载解析", self.a_fetch, fg=self._gold())
        tk.Label(r2, text="抓 Config.sgs → 解密 → 解析(只写 data/season/s<N>/, 不动现行数据)",
                 bg=self._bg(), fg=self._dim(), font=(self._fh(), 8)).pack(side="left", padx=(4, 0))

        r3 = tk.Frame(box1, bg=self._bg())
        r3.pack(fill="x", padx=6, pady=(0, 6))
        self._btn_add(r3, "③ 推送到现行数据(推荐)", self.a_push, fg=self._green())
        self._btn_add(r3, "④ 替换现行数据", self.a_replace, fg=self._warn())

        # v4.113★ ⑤ 名单迁移 —— 换季之后必须做的一步(否则名单全指向局内没有的卡, 且不报错)
        r3b = tk.Frame(box1, bg=self._bg())
        r3b.pack(fill="x", padx=6, pady=(0, 6))
        self._btn_add(r3b, "⑤ 名单迁到本季(招募/随征/保留/遣散)", self.a_migrate_lists,
                      fg=self._warn())
        tk.Label(r3b, text="换季 = 【ID 整套换】(21001011→21101011, 名字一字不改); 名单里存的是 ID, "
                           "不迁就全指向局内没有的卡 —— 表现是\"名单像全失灵了\"还不报错",
                 bg=self._bg(), fg=self._dim(), font=(self._fh(), 8)).pack(side="left", padx=(4, 0))
        tk.Label(r3, text="推送=按 ID 合并(旧赛季保留); 替换=整份重建(表外条目会删)",
                 bg=self._bg(), fg=self._dim(), font=(self._fh(), 8)).pack(side="left", padx=(4, 0))

        # ---------------- ② 只读体检 ----------------
        box2 = tk.LabelFrame(
            parent, text=" ② 赛季体检 (只读: 对照配置表列出\"该做没做\"的事, 不改任何数据) ",
            bg=self._bg(), fg=self._gold(), font=(self._fh(), 10, "bold"), bd=1, relief="groove")
        box2.pack(fill="x", padx=10, pady=(0, 6))
        r4 = tk.Frame(box2, bg=self._bg())
        r4.pack(fill="x", padx=6, pady=6)
        self._btn_add(r4, "跑一次体检", self.a_health_check, fg=self._blue())
        tk.Label(r4, text="= python tools/season_check.py —— 棋子/主公/技能/名单逐项对表",
                 bg=self._bg(), fg=self._dim(), font=(self._fh(), 8)).pack(side="left", padx=(4, 0))

        # ---------------- ③ OL 皮肤表 ----------------
        box3 = tk.LabelFrame(
            parent, text=" ③ OL 皮肤表 (资源替换页「从皮肤表挑(OL大图)」的数据源) ",
            bg=self._bg(), fg=self._gold(), font=(self._fh(), 10, "bold"), bd=1, relief="groove")
        box3.pack(fill="x", padx=10, pady=(0, 6))
        r5 = tk.Frame(box3, bg=self._bg())
        r5.pack(fill="x", padx=6, pady=6)
        self._btn_add(r5, "联网更新皮肤表", self.a_dress_online, fg=self._gold())
        self._btn_add(r5, "从本地 Config.sgs 建表 …", self.a_dress_from_file, fg=self._blue())
        self.dress_var = tk.StringVar(value="")
        tk.Label(r5, textvariable=self.dress_var, bg=self._bg(), fg=self._dim(),
                 font=(self._fh(), 8)).pack(side="left", padx=(4, 0))

        # ---------------- 日志框 ----------------
        lf = tk.Frame(parent, bg=self._bg())
        lf.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self._log_text = tk.Text(lf, height=14, bg="#141414", fg="#cccccc", wrap="none",
                                 font=("Consolas", 9), state="disabled", relief="flat")
        vs = tk.Scrollbar(lf, orient="vertical", command=self._log_text.yview)
        self._log_text.configure(yscrollcommand=vs.set)
        vs.pack(side="right", fill="y")
        self._log_text.pack(fill="both", expand=True)

        self._refresh_dress_status()
        self._refresh_current_label()

    def _btn_add(self, host, text, cmd, fg=None):
        b = tk.Button(host, text=text, command=cmd, bg=self._act(),
                      fg=(fg or self._fg()), font=(self._fh(), 9, "bold"),
                      padx=8, cursor="hand2")
        b.pack(side="left", padx=(0, 6))
        self._buttons.append(b)
        return b

    # ---------------------------------------------------------------- 日志/忙碌
    def _log(self, msg):
        """worker 线程也会调: 先转回主线程, 同时镜像进『赛季数据』页日志框 + 主日志。"""
        s = str(msg)
        try:
            self.root.after(0, self._log_append, s)
        except Exception:
            print(s)
        try:
            self.app._season_log(s)         # 它自己会镜像主日志并转主线程
        except Exception as e:              # 没有赛季页日志框(旧界面)也不影响本页
            sys.stderr.write("[赛季更新] 镜像到赛季页日志失败: %s\n" % e)

    def _log_append(self, msg):
        tw = self._log_text
        if tw is None:
            return
        try:
            tw.configure(state="normal")
            tw.insert("end", msg + "\n")
            lines = int(tw.index("end-1c").split(".")[0])
            if lines > 900:
                tw.delete("1.0", "%d.0" % (lines - 900))
            tw.see("end")
            tw.configure(state="disabled")
        except Exception as e:
            sys.stderr.write("[赛季更新] 日志框写入失败: %s\n" % e)

    def _set_busy(self, on, title=""):
        self._busy = bool(on)
        for b in self._buttons:
            try:
                b.configure(state=("disabled" if on else "normal"))
            except Exception as e:          # 按钮(理论上)不会消失, 兜住不炸就行
                sys.stderr.write("[赛季更新] 按钮状态切换失败: %s\n" % e)
        if title:
            self.status_var.set(title)

    def _run(self, title, fn, done=None):
        """统一入口: 子线程跑 fn()(联网/解密可能几十秒, 不能卡界面)。"""
        if self._busy:
            messagebox.showinfo("赛季更新", "上一个任务还在跑, 等它结束再点。")
            return
        self._set_busy(True, title + " ...")
        self._log("──────── %s ────────" % title)

        def _task():
            try:
                result = fn()
                err = None
            except Exception as e:              # noqa: BLE001 —— 界面入口必须兜住一切
                result, err = None, "%s: %s" % (type(e).__name__, e)
            self.root.after(0, self._finish, title, result, err, done)

        threading.Thread(target=_task, daemon=True).start()

    def _finish(self, title, result, err, done):
        if err:
            self._log("✗ %s 失败: %s" % (title, err))
            self.status_var.set("失败: " + title)
            messagebox.showerror("赛季更新", "%s 失败:\n\n%s" % (title, err))
        else:
            self._log("✓ %s 完成" % title)
            self.status_var.set("完成: " + title)
        self._set_busy(False, self.status_var.get())
        if done:
            try:
                done(result)
            except Exception as e:              # noqa: BLE001
                sys.stderr.write("[赛季更新] done 回调失败: %s\n" % e)

    def _season_args(self):
        season = (self.season_var.get() or "").strip() or "10"
        ver = (self.version_var.get() or "").strip() or None
        return season, ver

    # ---------------------------------------------------------------- 动作
    def _refresh_current_label(self):
        try:
            cur = str(SC.current_season() or "10")
        except Exception as e:
            sys.stderr.write("[赛季更新] 读当前赛季失败: %s\n" % e)
            cur = "10"
        # v4.106★ 光报赛季号不够 —— 还得说"这个号下面有没有真表": 号切了但表没跟上,
        #   表现就是"什么都查不到"(用户原话)。有表显示绿, 没表显示红 + 一句怎么办。
        ok = False
        try:
            csvp = os.path.join(SC.season_dir(cur), "ChessConf.csv")
            ok = os.path.exists(csvp) and os.path.getsize(csvp) > 200
        except Exception as e:
            sys.stderr.write("[赛季更新] 查当前赛季数据失败: %s\n" % e)
        try:
            self.cur_var.set(("助手现在认的是 S%s ✓有数据" % cur) if ok
                             else ("助手现在认的是 S%s ⚠该季没表(会什么都查不到)" % cur))
        except Exception as e:
            sys.stderr.write("[赛季更新] 当前赛季标签刷新失败: %s\n" % e)

    def a_set_current(self):
        """⓪ 把输入框里的赛季号设成『当前赛季』。

        "能查到 S11"和"助手在用 S11"是两件事: 前者只要抓过(有 data/season/s11/ 目录),
        后者要看 data/season/season.json 那个标记 —— 决定白名单和"本季可用"口径。
        换季正路是: ②抓 S11 → ③推送(推送会自己写标记); 只想先切口径就用这一颗按钮。
        """
        season, _ver = self._season_args()

        def _do():
            sd = SC.season_dir(season)
            if not os.path.isdir(sd):
                raise RuntimeError("赛季目录不存在: %s\n—— 先对 S%s 点一次『② 一键下载解析』再切" % (sd, season))
            # v4.106★ 【目录在 ≠ 有数据】—— 用户实机踩到的坑: 看到 S11 目录就切过去,
            #   结果助手里"什么都查不到"。因为白名单/本季口径按这个号过滤, 而 S11 目录里
            #   其实是空的(只抓过 resc, 没有 ChessConf.csv) → 过滤完自然是空表。
            #   所以这里先数一眼行数: 没有真表就不让切, 并说清是"官方还没发布"还是"你没抓"。
            csvp = os.path.join(sd, "ChessConf.csv")
            n = 0
            if os.path.exists(csvp):
                try:
                    with open(csvp, "r", encoding="utf-8", errors="ignore") as f:
                        n = max(0, sum(1 for _ in f) - 1)
                except Exception as e:
                    sys.stderr.write("[赛季更新] 读 %s 失败: %s\n" % (csvp, e))
            if n <= 0:
                # v4.113★ 以前这里按【赛季号】去探官方路径段(/11/ 之类) —— 那是十周年
                #   版本目录, 除了 10 全是占位页, 于是永远报"官方还没发布"。
                #   赛季号只是配置里的 SeasonID, 抓哪一季都用 /10/ 那份配置。
                raise RuntimeError(
                    "S%s 目录里【没有真数据】(ChessConf.csv 空或不存在)。\n"
                    "现在切过去 = 你遇到的「什么都查不到」: 白名单和『本季可用』按赛季号过滤, "
                    "过滤完就是空表。\n\n"
                    "正确顺序: 『② 一键下载解析』(赛季号填 %s, 资源路径段保持 %s) → "
                    "『③ 推送到现行数据』(推送会自己写这个标记) → 再切。\n"
                    "注: 资源路径段 10 是十周年客户端版本目录, 不是赛季号; "
                    "配置里 1~%s 季的数据【全在同一份】里, 换季只换 SeasonID 那一筛。"
                    % (season, season, SC.DEFAULT_VERSION, season))
            SC.write_season_marker(season, SC.DEFAULT_VERSION)
            try:
                SC.refresh_readers()
            except Exception as e:
                self._log("  (刷新读取器失败: %s —— 重启助手即生效)" % e)
            return {"season": season, "dir": sd}

        def _done(r):
            self._log("  当前赛季 = S%s（%s）" % (r["season"], r["dir"]))
            self._log("  提示: 只改了『口径』(白名单/本季可用)。要让卡池/技能/名字也同步, "
                      "还要对 S%s 点一次『③ 推送到现行数据』。" % r["season"])
            self._refresh_current_label()

        self._run("设为当前赛季 S%s" % season, _do, done=_done)

    def a_check_update(self):
        """① 检查官方更新: 只发 HEAD(不下 8MB), 说清"官方改没改 / 下赛季上线没"。"""
        season, ver = self._season_args()

        def _do():
            return SC.check_update(season, ver)

        self._run("检查官方更新 (S%s)" % season, _do, done=self._show_update_info)

    def _show_update_info(self, info):
        if not info:
            return
        self.status_var.set("官方更新检查: %s" % ("有更新" if info.get("changed")
                                                else ("没变" if info.get("changed") is False else "判断不了")))
        lines = ["赛季 S%s(路径段 %s)" % (info.get("season"), info.get("version"))]
        rm = info.get("remote") or {}
        if rm.get("status") == 200:
            lines.append("官方 Config.sgs: %d 字节%s" % (
                rm.get("size") or 0, ("  更新于 " + rm["mtime"]) if rm.get("mtime") else ""))
        else:
            lines.append("取官方 Config.sgs 失败: %s %s" % (rm.get("status"), rm.get("err")))
        for n in (info.get("notes") or []):
            lines.append("· " + n)
        if info.get("next_live"):
            lines.append("· 路径段 %s 上已经是真实配置 → 下赛季可能已发布" % info.get("next_version"))
        if info.get("changed") is False:
            messagebox.showinfo("检查官方更新", "\n".join(lines) + "\n\n不用重下, 现在这份就是最新的。")
            return
        if messagebox.askyesno("检查官方更新",
                               "\n".join(lines) + "\n\n要现在『一键下载解析』把它抓下来吗?\n"
                               "(只写赛季目录, 不动现行数据; 之后点③推送才生效)"):
            self.a_fetch()

    def a_fetch(self):
        """② 一键下载解析: 抓 Config.sgs → 解密 → 解析(只写赛季目录)。"""
        season, ver = self._season_args()

        def _do():
            return SC.fetch_and_parse(season, ver)

        def _done(_info):
            if isinstance(_info, dict):
                self.status_var.set("已解析 S%s(棋子 %d/锦囊 %d/技能 %d) —— 点③推送到现行数据"
                                    % (_info.get("season"), _info.get("chess") or 0,
                                       _info.get("spell") or 0, _info.get("skills") or 0))
            if messagebox.askyesno(
                    "一键下载解析",
                    "S%s 已解析完成(只写了赛季目录, 现行数据未动)。\n\n现在就『推送到现行数据』让助手用上吗?\n"
                    "(推送=按 ID 合并, 旧赛季保留; 会先自动备份)" % season):
                self.a_push()

        self._run("一键下载解析 (S%s)" % season, _do, done=_done)

    def a_push(self):
        """③ 推送: 名字映射按 ID 合并(旧赛季/旧键都留着) —— 最稳的换表方式。"""
        season, _ver = self._season_args()
        if not messagebox.askyesno(
                "推送到现行数据",
                "把赛季 S%s 的解析结果写进现行数据:\n\n"
                "  · ChessConf.csv / ChessSpellConf.csv  (列序沿用旧表)\n"
                "  · 棋子/锦囊/技能映射按 ID 合并(旧赛季的键保留)\n"
                "  · 随征/白名单里引用的旧 ID 不删\n\n"
                "写之前自动整份备份到 data/_backup/。继续?" % season):
            return
        self._do_push(season, "merge")

    def a_replace(self):
        """④ 替换: 完全按快照重建(表外条目一律删掉) —— 想彻底对齐官方配置时用。"""
        season, _ver = self._season_args()
        if not messagebox.askyesno(
                "替换现行数据",
                "⚠ 替换 = 完全按赛季 S%s 的快照重建现行数据:\n"
                "  · 这份快照里没有的条目【会被删掉】(旧赛季的变体卡名也没了)\n"
                "  · 名单(随征/白名单/…)里引用被删 ID 的地方会失效\n\n"
                "已自动备份到 data/_backup/, 出问题可回滚。确定继续?" % season):
            return
        self._do_push(season, "replace")

    def _do_push(self, season, mode):
        label = "替换" if mode == "replace" else "推送"

        def _do():
            return SC.push_to_current(season, mode, None)

        def _done(rpt):
            if isinstance(rpt, dict):
                self._log("— 写入明细 —")
                for lab, desc in (rpt or {}).get("items", []):
                    self._log("  %-28s %s" % (lab, desc))
                if rpt.get("backup"):
                    self._log("  备份目录: %s" % rpt["backup"])
            self._log("已顺带刷新助手内存里的表; 保险起见可重启助手。游戏页按 F5 重连。")
            messagebox.showinfo(label + "完成",
                                "赛季 S%s 已%s现行数据(已备份)。\n\n建议: 重启助手 + 游戏页 F5。"
                                % (season, label))

        self._run("%s赛季 S%s → 现行数据" % (label, season), _do, done=_done)

    def a_migrate_lists(self):
        """⑤ 名单 ID 迁到本季。

        换季之后必须做的一步: 官方把赛季号编进 ID(曹休 S10=21001011 / S11=21101011,
        名字一字不改), 而 data/lists/ 里存的是 ID —— 不迁的话招募/随征/保留/遣散全部指向
        局内根本不存在的卡, 而且【不报错】(那是合法的历史 ID, 只是本季池里没有), 表现就是
        "换季之后名单像全失灵了"。

        先算一份预览给用户看(哪些会变、几条迁不动), 确认后才落盘 —— 落盘前整份备份。
        """
        try:
            from core import list_migrate as LM
            rep = LM.plan_migration()
        except Exception as e:
            self._log("[名单迁移] 读名单失败: %s" % e)
            return
        if not rep.get("total_changed"):
            self._log("[名单迁移] 没有要迁的 —— 名单里已经都是本季(S%s)的卡"
                      % (rep.get("season_prefix") or "?"))
            messagebox.showinfo("名单迁移", "名单里已经都是本季 ID, 不用迁。", parent=self.root)
            return
        lines = []
        for k, v in (rep.get("files") or {}).items():
            if not v.get("changed"):
                continue
            lines.append("  %s: %d 条" % (k, len(v["changed"])))
            for x in v["changed"][:4]:
                lines.append("      %s → %s  %s" % (x["old"], x["new"], x["name"]))
            if len(v["changed"]) > 4:
                lines.append("      … 还有 %d 条" % (len(v["changed"]) - 4))
        msg = ("要把 %d 条名单 ID 迁到 S%s:\n\n%s\n\n"
               "迁不动 %d 条(本季没这张卡) —— 那些【原样保留】, 不猜。\n"
               "改之前会整份备份 data/lists/。\n\n迁吗?"
               % (rep["total_changed"], rep.get("season_prefix") or "?",
                  "\n".join(lines), rep.get("total_unmapped") or 0))
        if not messagebox.askyesno("名单迁移", msg, parent=self.root):
            return

        def _do():
            return LM.apply_migration()

        def _done(res):
            if not res:
                self._log("[名单迁移] 失败")
                return
            bdir, rep2 = res
            self._log("[名单迁移] 已落盘, 备份: %s" % bdir)
            for k, v in (rep2.get("files") or {}).items():
                if not v.get("total"):
                    continue
                self._log("    %-16s 共%3d 条  本季%3d  迁不动%d"
                          % (k, v["total"], v.get("already") or 0, len(v.get("unmapped") or [])))
            self._log("[名单迁移] 完成: 迁了 %d 条; 仍迁不动 %d 条(本季没这张卡, 已原样保留)"
                      % (rep["total_changed"], rep2.get("total_unmapped") or 0))

        self._run("名单 ID 迁到本季", _do, done=_done)

    def a_health_check(self):
        """② 只读体检 = tools/season_check.py(它用 print 输出, 这里接住转进日志框)。"""
        season, _ver = self._season_args()

        def _do():
            from tools import season_check as SCH
            buf = io.StringIO()
            old_argv = sys.argv
            sys.argv = ["season_check.py", "--season", season]
            try:
                with contextlib.redirect_stdout(buf):
                    SCH.main()
            finally:
                sys.argv = old_argv
            return buf.getvalue()

        def _done(out):
            for line in str(out or "").splitlines():
                self._log(line)

        self._run("赛季体检 (S%s, 只读)" % season, _do, done=_done)

    def a_dress_online(self):
        """③ 联网更新 OL 皮肤表(资源替换页「从皮肤表挑」用的那张)。"""
        from core import dress_table as DT

        def _do():
            return DT.update_online()

        def _done(res):
            self._refresh_dress_status()
            res = res or {}
            if res.get("ok"):
                messagebox.showinfo("OL 皮肤表", "已更新: %d 套皮肤(来源 %s)。" %
                                    (res.get("count") or 0, res.get("source") or "官方"))
            else:
                messagebox.showwarning(
                    "OL 皮肤表", "联网更新失败:\n%s\n\n打包版缺解密钥匙时, 用旁边的"
                    "『从本地 Config.sgs 建表』选一份现成文件也一样。" % (res.get("error") or "未知原因"))

        self._run("联网更新 OL 皮肤表", _do, done=_done)

    def a_dress_from_file(self):
        """③ 从本地 Config.sgs / 任意官方配置建皮肤表(离线/打包版的备用入口)。"""
        p = filedialog.askopenfilename(
            title="选 Config.sgs(或含 SkinConf 的官方配置文件)",
            filetypes=[("Config.sgs / 配置", "*.sgs"), ("所有文件", "*.*")])
        if not p:
            return
        from core import dress_table as DT

        def _do():
            return DT.update_from_file(p)

        def _done(res):
            self._refresh_dress_status()
            res = res or {}
            if res.get("ok"):
                messagebox.showinfo("OL 皮肤表", "已更新: %d 套皮肤(来源 %s)。" %
                                    (res.get("count") or 0, res.get("source") or p))
            else:
                messagebox.showwarning("OL 皮肤表", "建表失败:\n%s" % (res.get("error") or "未知原因"))

        self._run("从本地文件建 OL 皮肤表", _do, done=_done)

    def _refresh_dress_status(self):
        """皮肤表现状一行字(多久前更新的 / 多少套)。"""
        try:
            from core import dress_table as DT
            d = DT.load()
            if d.get("loaded"):
                t = time.strftime("%Y-%m-%d %H:%M", time.localtime(d.get("updated") or 0))
                self.dress_var.set("当前: %d 套 · 更新于 %s" % (d.get("count") or 0, t))
            else:
                self.dress_var.set("还没有皮肤表 —— 联网抓一次或从本地 Config.sgs 建一份")
        except Exception as e:              # noqa: BLE001 —— 状态行不因缺模块变红
            self.dress_var.set("皮肤表状态读不到: %s" % e)
