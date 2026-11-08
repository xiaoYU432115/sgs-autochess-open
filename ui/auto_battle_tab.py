# -*- coding: utf-8 -*-
"""「自动对局」页 —— S11 自走棋自动对战 + 离线学习。

放在一个独立模块里, 由 app.py 的 _build_tabs 懒加载调用: build(app, parent)。
本页只负责【界面 + 把 UI 配置喂给 core.auto_battle.AutoBattler】, 决策/执行全在引擎里。
"""
import tkinter as tk
from tkinter import ttk

from core import silence as SILENCE   # 兜底不静默: 吞掉的异常记一笔(见 core/silence.py)

# 颜色/字体(与 app.py 保持一致)
BG = "#2a2a2a"
FG = "#e8e8e8"
DIM = "#9a9a9a"
GOLD = "#ffcc66"
FH = "Microsoft YaHei"

# 8 大势力(对齐 tavern_kb.FACTION_CODE)
ALL_FACTIONS = ["魏", "蜀", "吴", "群", "黄巾", "汉", "西凉", "袁"]
# 用户点名 + 常见机制
ALL_TAGS = ["经济", "盾", "护甲", "毒", "回手", "成长", "召唤", "群体", "当先", "亡语"]


def build(app, parent):
    """构建自动对局页内容。app: App 实例(有 ops / log / cfg)。"""
    from core.auto_battle import AutoBattler

    # 懒创建引擎(只在第一次进本页时)
    if not hasattr(app, "auto_battler") or app.auto_battler is None:
        app.auto_battler = AutoBattler(app.ops, log_cb=app.log)

    frame = app._make_scrollable_tab(parent)

    tk.Label(frame, text="S11 自走棋自动对局(自动选主公 / 经济 / 阵容 / 针对 / 离线学习)。\n"
                         "进对局后点【启动】, 引擎自动接管; 一局打完会自动记录, 并在下一局继续。",
             bg=BG, fg=DIM, font=(FH, 9)).pack(anchor="w", pady=(0, 6))

    # ---------------- 被禁势力 ----------------
    banned_vars = {}
    fb = tk.LabelFrame(frame, text=" 本局禁用的势力(被禁的不选主公 / 不主走) ",
                       bg=BG, fg=GOLD, bd=1, relief="groove", font=(FH, 10, "bold"))
    fb.pack(fill="x", pady=(4, 6))
    row = tk.Frame(fb, bg=BG)
    row.pack(fill="x", padx=8, pady=6)
    for i, f in enumerate(ALL_FACTIONS):
        v = tk.BooleanVar()
        banned_vars[f] = v
        tk.Checkbutton(row, text=f, variable=v, bg=BG, fg=FG,
                       selectcolor="#1e1e1e", font=(FH, 9)).grid(row=0, column=i, padx=4)

    # ---------------- 推荐主公 ----------------
    # 为什么要有这块: 以前选主公只在【日志】里打一行, 而且必须真的进了对局才触发。
    #   用户想"我禁了魏, 该选谁"时看不到任何东西, 只能自己猜 —— 等于功能不存在。
    #   这里把推荐摆到界面上, 随手改勾选就能立刻看到结果变。
    rp = tk.LabelFrame(frame, text=" 推荐主公(按上面禁用的势力算) ",
                       bg=BG, fg=GOLD, bd=1, relief="groove", font=(FH, 10, "bold"))
    rp.pack(fill="x", pady=(4, 6))
    rp_top = tk.Frame(rp, bg=BG)
    rp_top.pack(fill="x", padx=8, pady=(6, 2))
    ban_src_var = tk.StringVar(value="禁用来源: —")
    tk.Label(rp_top, textvariable=ban_src_var, bg=BG, fg=DIM, font=(FH, 9)).pack(side="left")
    rp_text = tk.Text(rp, height=7, bg="#1e1e1e", fg=FG, font=(FH, 9), state="disabled",
                      wrap="word")
    rp_text.pack(fill="x", padx=8, pady=(2, 6))

    def _current_banned_with_src():
        """本局禁用势力 → (列表, 来源)。协议读到优先, 读不到用界面勾选。"""
        live = getattr(app, "live", None)
        got = getattr(live, "banned_factions", None) if live else None
        if got:
            return sorted(got), "协议(从游戏包里读到的)"
        return [f for f, v in banned_vars.items() if v.get()], "界面勾选(没从协议读到)"

    def _refresh_recs():
        try:
            import core.s11_enhance as S11
            banned, src = _current_banned_with_src()
            ban_src_var.set("禁用于: %s —— 来源: %s"
                            % ("/".join(banned) or "无(没禁任何势力)", src))
            tags = [t for t, v in tag_vars.items() if v.get()]
            recs = S11.lord_pick_advice("s11", banned_factions=banned,
                                        prefer_tags=tags, top=5)
            if not recs:
                body = "没推荐出来(可能候选全被禁掉了) —— 放宽上面的禁用再试。"
            else:
                body = []
                for i, r in enumerate(recs, 1):
                    body.append("%d. %s   %.1f分   血%s" %
                                (i, r.get("name"), r.get("score", 0), r.get("hp")))
                    body.append("     %s" % (r.get("why") or "常规"))
                    if r.get("tip"):
                        body.append("     图鉴参考: %s" % r["tip"])
                body = "\n".join(body)
            rp_text.configure(state="normal")
            rp_text.delete("1.0", "end")
            rp_text.insert("end", body)
            rp_text.configure(state="disabled")
        except Exception as e:
            SILENCE.note("auto_battle_tab._refresh_recs", "推荐主公算不出来: %s" % e)
            ban_src_var.set("推荐算不出来: %s" % e)

    tk.Button(rp_top, text="重算推荐", command=_refresh_recs, bg="#3a3a3a", fg=FG,
              font=(FH, 9)).pack(side="right")

    # ---------------- 想要的机制 ----------------
    tag_vars = {}
    tb = tk.LabelFrame(frame, text=" 想主打的机制(决定主公倾向 / 阵容加权) ",
                       bg=BG, fg=GOLD, bd=1, relief="groove", font=(FH, 10, "bold"))
    tb.pack(fill="x", pady=(4, 6))
    trow = tk.Frame(tb, bg=BG)
    trow.pack(fill="x", padx=8, pady=6)
    for i, t in enumerate(ALL_TAGS):
        v = tk.BooleanVar()
        tag_vars[t] = v
        tk.Checkbutton(trow, text=t, variable=v, bg=BG, fg=FG,
                       selectcolor="#1e1e1e", font=(FH, 9)).grid(row=0, column=i, padx=4)

    # 勾任何一个禁势力/机制都立刻重算推荐(不用记得点按钮); 进页面先算一次。
    # ★ 必须放在 tag_vars 建好之后 —— 上面那个循环依赖它。
    for _v in list(banned_vars.values()) + list(tag_vars.values()):
        _v.trace_add("write", lambda *_a: _refresh_recs())
    _refresh_recs()

    # ---------------- 自动选项 ----------------
    opt = tk.LabelFrame(frame, text=" 自动操作开关 / 经济 ",
                        bg=BG, fg=GOLD, bd=1, relief="groove", font=(FH, 10, "bold"))
    opt.pack(fill="x", pady=(4, 6))
    orow = tk.Frame(opt, bg=BG)
    orow.pack(fill="x", padx=8, pady=6)
    auto_choose = tk.BooleanVar(value=True)
    auto_level = tk.BooleanVar(value=True)
    auto_refresh = tk.BooleanVar(value=True)
    use_spells = tk.BooleanVar(value=True)
    # 自动变阵: 会真的遣散掉"方向外"的卡, 所以在标签上写明, 不藏着
    auto_pivot = tk.BooleanVar(value=True)
    for i, (lab, var) in enumerate([("自动选主公", auto_choose), ("自动升级", auto_level),
                                    ("自动刷新", auto_refresh), ("自动用锦囊", use_spells),
                                    ("自动变阵(会遣散)", auto_pivot)]):
        tk.Checkbutton(orow, text=lab, variable=var, bg=BG, fg=FG,
                       selectcolor="#1e1e1e", font=(FH, 9)).grid(row=0, column=i, padx=4)
    rrow = tk.Frame(opt, bg=BG)
    rrow.pack(fill="x", padx=8, pady=(2, 6))
    tk.Label(rrow, text="保留金币:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left")
    reserve_var = tk.StringVar(value="0")
    tk.Entry(rrow, textvariable=reserve_var, bg="#1e1e1e", fg=FG, insertbackground=FG,
             width=8, font=(FH, 9)).pack(side="left", padx=6)
    tk.Label(rrow, text="(经济紧张时预留, 0=不预留)", bg=BG, fg=DIM, font=(FH, 8)).pack(side="left")

    # ---------------- 启动 / 停止 ----------------
    ctl = tk.Frame(frame, bg=BG)
    ctl.pack(fill="x", pady=(6, 8))
    status_var = tk.StringVar(value="未启动")
    start_btn = tk.Button(ctl, text="▶ 启动自动对局", bg="#2e7d32", fg="white",
                          font=(FH, 11, "bold"), width=18,
                          command=lambda: _start(app, banned_vars, tag_vars, auto_choose,
                                                 auto_level, auto_refresh, use_spells,
                                                 auto_pivot, reserve_var, status_var,
                                                 start_btn, stop_btn))
    start_btn.pack(side="left", padx=(0, 10))
    stop_btn = tk.Button(ctl, text="■ 停止", bg="#b71c1c", fg="white",
                         font=(FH, 11, "bold"), width=12, state="disabled",
                         command=lambda: _stop(app, status_var, start_btn, stop_btn))
    stop_btn.pack(side="left")

    tk.Label(ctl, textvariable=status_var, bg=BG, fg=GOLD, font=(FH, 10)).pack(side="left", padx=12)

    # ---------------- 实时状态 ----------------
    st = tk.LabelFrame(frame, text=" 实时状态 ", bg=BG, fg=GOLD, bd=1, relief="groove",
                       font=(FH, 10, "bold"))
    st.pack(fill="x", pady=(4, 6))
    st_text = tk.Text(st, height=7, bg="#1e1e1e", fg=FG, font=(FH, 9), state="disabled",
                      wrap="word")
    st_text.pack(fill="x", padx=8, pady=6)

    # ---------------- 局势判断(v4.140★ 大脑算出来的, 不是拍脑袋) ----------------
    br = tk.LabelFrame(frame, text=" 局势判断(身材/对拼/针对/经济) ", bg=BG, fg=GOLD, bd=1,
                       relief="groove", font=(FH, 10, "bold"))
    br.pack(fill="x", pady=(4, 6))
    br_text = tk.Text(br, height=6, bg="#1e1e1e", fg=FG, font=(FH, 9), state="disabled",
                      wrap="word")
    br_text.pack(fill="x", padx=8, pady=6)

    learn = tk.LabelFrame(frame, text=" 学习统计(已打过的局) ", bg=BG, fg=GOLD, bd=1,
                          relief="groove", font=(FH, 10, "bold"))
    learn.pack(fill="x", pady=(4, 6))
    learn_text = tk.Text(learn, height=8, bg="#1e1e1e", fg=FG, font=(FH, 9), state="disabled",
                         wrap="word")
    learn_text.pack(fill="x", padx=8, pady=6)

    def _refresh():
        try:
            if app.auto_battler:
                s = app.auto_battler.get_status()
                lines = [
                    "运行: %s   阶段: %s" % ("是" if s.get("running") else "否", s.get("phase")),
                    "主公: %s    主阵营: %s" % (s.get("lord"), s.get("faction")),
                    "策略: %s" % s.get("strategy"),
                    "最近动作: %s" % s.get("last_action"),
                    "已学局数: %d" % s.get("games", 0),
                ]
                st_text.configure(state="normal")
                st_text.delete("1.0", "end")
                st_text.insert("end", "\n".join(lines))
                st_text.configure(state="disabled")

                # 局势判断: 身材/最难打的对手/该补什么/钱该怎么花
                b = s.get("brain") or {}
                bl = ["身材: %s" % (b.get("body") or "还没读到自己的阵容")]
                bl.append("最难打: %s" % (b.get("worst") or "-"))
                bl.append("建议补: %s" % (b.get("need") or "-"))
                bl.append("经济: %s" % (b.get("econ") or "-"))
                # v4.142★ 变阵: 现在在玩什么 / 该不该转。
                #   这两行必须显示 —— 变阵是"助手替你做了个大决定", 不给理由的话
                #   用户看到卡被卖掉只会觉得助手在抽风。
                bl.append("当前体系: %s" % (s.get("system") or "-"))
                bl.append("变阵: %s" % (s.get("pivot") or "-"))
                br_text.configure(state="normal")
                br_text.delete("1.0", "end")
                br_text.insert("end", "\n".join(bl))
                br_text.configure(state="disabled")
                ls = s.get("learn") or {}
                lr = ls.get("win_rate", 0)
                lg = ls.get("games", 0)
                fw = ls.get("faction_wins") or {}
                fac_lines = ["胜负: %d 局, 胜率 %.0f%%" % (lg, 100 * float(lr))]
                for k, v in sorted(fw.items(), key=lambda kv: -(v.get("w", 0) / max(1, v.get("n", 1)))):
                    fac_lines.append("  %s: %d胜/%d局" % (k, v.get("w", 0), v.get("n", 0)))
                learn_text.configure(state="normal")
                learn_text.delete("1.0", "end")
                learn_text.insert("end", "\n".join(fac_lines))
                learn_text.configure(state="disabled")
        except Exception:
            # 学习面板每秒刷一次, 这里抛异常不该把整个定时刷新打断;
            # 但一直静默的话"学习数据从来没显示出来"就查不到。
            SILENCE.note("auto_battle_tab._refresh", "学习面板刷新失败")
        parent.after(1000, _refresh)

    parent.after(1000, _refresh)


def _start(app, banned_vars, tag_vars, auto_choose, auto_level, auto_refresh,
           use_spells, auto_pivot, reserve_var, status_var, start_btn, stop_btn):
    banned = [f for f, v in banned_vars.items() if v.get()]
    tags = [t for t, v in tag_vars.items() if v.get()]
    try:
        reserve = int((reserve_var.get() or "0").strip() or 0)
    except ValueError:
        reserve = 0
    cfg = {
        "mode": "tavern",
        "auto_choose_lord": auto_choose.get(),
        "auto_level": auto_level.get(),
        "auto_refresh": auto_refresh.get(),
        "use_spells": use_spells.get(),
        "auto_pivot": auto_pivot.get(),
        "reserve_gold": reserve,
        "banned_factions": banned,
        "prefer_tags": tags,
    }
    ok = app.auto_battler.start(cfg)
    if ok:
        status_var.set("运行中… (禁:%s 机制:%s)" % ("/".join(banned) or "无", "/".join(tags) or "无"))
        start_btn.configure(state="disabled")
        stop_btn.configure(state="normal")
        app.log("[UI] 自动对局已启动(禁:%s 机制:%s)" % ("/".join(banned) or "无", "/".join(tags) or "无"))


def _stop(app, status_var, start_btn, stop_btn):
    if app.auto_battler:
        app.auto_battler.stop()
    status_var.set("已停止")
    start_btn.configure(state="normal")
    stop_btn.configure(state="disabled")
    app.log("[UI] 自动对局已停止")
