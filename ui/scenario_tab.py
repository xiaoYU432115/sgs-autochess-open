# -*- coding: utf-8 -*-
"""剧本页 —— v4.109★ 从 ui/app.py 搬来的那 60 个 _scenario_* 方法。

app.py 原本一万多行, 剧本页独占一大截(构建 + 步骤编辑 + 预设 + 导入导出 +
执行, 三千多行)。现在整体搬到本文件的 ScenarioTabBuilder, app.py 只留两个薄壳
(_build_scenario_tab 构建 / _scenario_collect 转发, 因为后者被 app.py 里
_push_all_lists_to_script 调用)。

搬法: 方法体【逐字照搬】, 只做了一处机械替换 —— 原先指向 App 自身的
self.xxx(self.log / self.cfg / self.ops / self.root / self._run_bg ...) 改成
self.app.xxx; 剧本页自己的 self.scenario_* / self._scenario_* 一律不动,
现在它们是 builder 的属性。

⚠ _run_bg 是【通用】方法(UI页/剧本/节奏/快捷键/FPS 共 12 处在用), 没搬, 留在 app.py。
⚠ 剧本页用到的常量与模块级小工具在 ui/scenario_data.py(也是从 app.py 搬出来的)。
"""
import os
import re
import sys
import json
import time
import threading
import tkinter as tk
# v4.109★ Toplevel 必须单独 import: 剧本页原来写在 app.py 里时用的是【裸名】
#   (app.py 顶层 from tkinter import Toplevel), 搬过来只写 import tkinter as tk 的话,
#   加步骤 / 编辑步骤 / 捕获快捷键 一弹窗就 NameError。
from tkinter import ttk, messagebox, simpledialog, filedialog, Toplevel

from core import id_map as IDMAP
from core import tavern_kb as _KB
from core import csv_loader as _CL
from core.spell_lib import TGT_ZH
from core.config import save_config
from ui.widgets import ThemeMixin, open_lord_skill_picker
from ui.scenario_data import *
from ui import scenario_struct as SSTRUCT   # v4.??★ 想法A: 缩进/配对/校验/修复(结构层)

# 主题色/字体/版本常量: 直接跟 app.py 同一个来源, 免得两处各写一份然后漂移。
#   app.py 是在【函数内】import 本模块的, 所以这里反向 import 它不会循环。
from ui.app import (BG, FG, DIM, RED, GOLD, BLUE, BTN, ACT,
                    SEL_BG, SEL_FG, FH, MIN_SCRIPT_VER, DATA_DIR)
from core import silence as _SIL

# v4.170★ 武将挑选框的【技能类型】索引(当先/亡语/召唤…) —— 来自本季棋子档案的 mech_tags。
#   只算一次并缓存(在同一进程内多次打开挑选框不必重算)。所有类型里只挑有代表性的几类放进下拉,
#   避免把几十个细碎标签全铺出来。要新增类型就往 _HERO_TAG_CURATED 加。
_HERO_SKILL_INDEX = None
_HERO_TAG_CURATED = ["当先", "亡语", "召唤", "群体", "护甲", "盾", "毒", "回手", "成长", "经济"]


def _hero_skill_index():
    """(name->set(tags), tag->set(names)) for current season chess."""
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
        _SIL.note("ui/scenario_tab.py:_hero_skill_index", str(_e))
    _HERO_SKILL_INDEX = (name_to_tags, tag_to_names)
    return _HERO_SKILL_INDEX


def _hero_tag_choices():
    """挑选框「技能类型」下拉的选项: {类型: [名字...]}, 只保留本季确实存在的代表性类型。"""
    _, tag_to_names = _hero_skill_index()
    out = {}
    for _t in _HERO_TAG_CURATED:
        _ns = tag_to_names.get(_t)
        if _ns:
            out[_t] = sorted(_ns)
    return out


class ScenarioTabBuilder(ThemeMixin):
    """剧本页。app.py 那边只留薄壳, 见 ui/app.py 的 _build_scenario_tab。"""

    def __init__(self, app):
        self.app = app
        self.root = app.root

    def _build_scenario_tab(self):
        """v4.27★ 剧本 tab —— 一串【有先后顺序】的步骤, 手动/快捷键执行一次。

        步骤表是平铺的, 循环/条件用"开始/结束"两行配对(页面端按配对跳转), 例如:
            上阵 孙尚香(after 空位) → 上阵 孙策(middle:孙尚香) → 遣散 指定武将 → 循环结束
        上阵落点支持相对位置: after:孙尚香 / before:孙尚香 / middle:孙尚香(两张同名中间)。
        每个剧本各绑一个快捷键(如 ctrl+alt+1), 在游戏页面按下即执行一次。
        """
        # ---- 状态 ----
        self.scenario_scripts = list(self.app.cfg.get("scenarios") or [])
        # v4.x★ 启动自愈: 历史数据里可能有"缺 id / id 重复"的剧本(早先"导入不补 id"的 bug 造成的),
        #   而页面端 startScriptById 是按 id 找剧本的 —— 缺 id = 永远找不到 = 「执行一次」没反应。
        #   这里加载时顺手补上; list(...) 是浅拷贝、dict 是共享的, 补了会同时进 cfg,
        #   下次连接/「执行一次」的 push 就把带 id 的版本带到页面, 不用删不用重导。
        _seen_ids = set()
        for _i, _s in enumerate(self.scenario_scripts):
            if not isinstance(_s, dict):
                continue
            _id = str(_s.get("id") or "")
            if (not _id) or (_id in _seen_ids):
                _base = f"s{_i + 1}_{int(time.time())}"
                _new = _base
                _k = 1
                while _new in _seen_ids:
                    _new = f"{_base}_{_k}"
                    _k += 1
                _s["id"] = _new
                _id = _new
            _seen_ids.add(_id)
        self.scenario_cur_idx = 0
        self.scenario_clipboard = []      # v4.41★ 步骤复制/剪切的暂存区(不落盘, 关了助手就没了)
        self.scenario_running = False
        self.scenario_stop_flag = False
        self.scenario_log_seq = 0

        content = self.app._make_scrollable_tab(self.app.tab_scenario, padx=(8, 0), pady=6)

        # ============ 左: 剧本列表 ============
        left = tk.LabelFrame(content, text="剧本", bg=BG, fg=GOLD, bd=1, relief="solid",
                             font=(FH, 9, "bold"), padx=4, pady=3)
        left.pack(side="left", fill="y")
        self.scenario_listbox = tk.Listbox(left, width=24, height=15, bg=BTN, fg=FG,
                                           selectbackground=SEL_BG, selectforeground=SEL_FG,
                                           font=(FH, 9), exportselection=False)
        self.scenario_listbox.pack(fill="y", expand=True, padx=3, pady=3)
        self.scenario_listbox.bind("<<ListboxSelect>>", lambda e: self._scenario_on_select())
        lbtns = tk.Frame(left, bg=BG)
        lbtns.pack(fill="x")
        for txt, cb in (("新增", self._scenario_new), ("复制", self._scenario_dup),
                        ("删除", self._scenario_del)):
            tk.Button(lbtns, text=txt, bg=BTN, fg=FG, font=(FH, 8), width=6,
                      command=cb).pack(side="left", padx=1, pady=2)
        # v4.41★ 剧本导入/导出 —— 分享给别人、换机器、备份都靠它。
        #   导出格式就是下发页面的那个结构本身(先落盘再读回来, 没经过任何转换),
        #   所以导出的文件是"文本可读 + 原样能导回来"的。
        lb2 = tk.Frame(left, bg=BG)
        lb2.pack(fill="x")
        tk.Button(lb2, text="导出剧本", bg=BTN, fg=FG, font=(FH, 8), command=self._scenario_export,
                  ).pack(side="left", padx=1, pady=2, fill="x", expand=True)
        tk.Button(lb2, text="导入剧本", bg=BTN, fg=FG, font=(FH, 8), command=self._scenario_import,
                  ).pack(side="left", padx=1, pady=2, fill="x", expand=True)
        # v4.48★ 剪贴板分享: 聊天窗口里直接甩一段 JSON 就行, 不用来回传文件
        lb3 = tk.Frame(left, bg=BG)
        lb3.pack(fill="x")
        tk.Button(lb3, text="复制JSON", bg=BTN, fg=FG, font=(FH, 8), command=self._scenario_copy_share,
                  ).pack(side="left", padx=1, pady=2, fill="x", expand=True)
        tk.Button(lb3, text="粘贴导入", bg=BTN, fg=FG, font=(FH, 8),
                  command=self._scenario_import_clipboard,
                  ).pack(side="left", padx=1, pady=2, fill="x", expand=True)

        # ============ 左中: 已注册函数(克隆/替换库) ============
        fnframe = tk.LabelFrame(content, text="已注册函数(克隆/替换库)", bg=BG, fg=GOLD, bd=1, relief="solid",
                                font=(FH, 9, "bold"), padx=4, pady=3)
        fnframe.pack(side="left", fill="y")
        self.fn_listbox = tk.Listbox(fnframe, width=22, height=13, bg=BTN, fg=FG,
                                     selectbackground=SEL_BG, selectforeground=SEL_FG,
                                     font=(FH, 9), exportselection=False)
        self.fn_listbox.pack(fill="y", expand=True, padx=3, pady=3)
        # 参数区: 选中函数后按它的「可填参数」动态生成中文下拉(不再用裸 JSON)
        self.fn_param_frame = tk.Frame(fnframe, bg=BG)
        self.fn_param_frame.pack(fill="x", padx=3, pady=(0, 2))
        self.fn_listbox.bind("<<ListboxSelect>>", lambda e: self._fn_build_param_fields())
        fbtns = tk.Frame(fnframe, bg=BG)
        fbtns.pack(fill="x")
        tk.Button(fbtns, text="插入调用", bg=ACT, fg=GOLD, font=(FH, 8), width=7,
                  command=self._fn_insert_call).pack(side="left", padx=1, pady=2)
        tk.Button(fbtns, text="查看", bg=BTN, fg=FG, font=(FH, 8), width=5,
                  command=self._fn_view).pack(side="left", padx=1, pady=2)
        tk.Button(fbtns, text="刷新", bg=BTN, fg=FG, font=(FH, 8), width=5,
                  command=self._fn_populate).pack(side="left", padx=1, pady=2)
        self._fn_populate()
        self._fn_build_param_fields()  # 初始: 显示「选择函数后显示参数」

        # ============ 右: 步骤表 ============
        right = tk.Frame(content, bg=BG)
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))

        # ---- v4.55★ 剧本级设置: 触发按键 + 触发方式 + 次数/速度 + 运行时/结束时触发 ----
        head = tk.Frame(right, bg=BG)
        head.pack(fill="x")
        tk.Label(head, text="剧本名:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left")
        self.scenario_name_var = tk.StringVar()
        _nm_ent = tk.Entry(head, textvariable=self.scenario_name_var, bg=BTN, fg=FG,
                           insertbackground=FG, font=(FH, 9), width=14)
        _nm_ent.pack(side="left", padx=(2, 8))
        _nm_ent.bind("<FocusOut>", self._scenario_head_changed)
        _nm_ent.bind("<Return>", self._scenario_head_changed)

        tk.Label(head, text="触发按键:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left")
        self.scenario_hotkey_var = tk.StringVar()
        _hk_ent = tk.Entry(head, textvariable=self.scenario_hotkey_var, bg=BTN, fg=FG,
                           insertbackground=FG, font=(FH, 9), width=13)
        _hk_ent.pack(side="left", padx=(2, 3))
        _hk_ent.bind("<FocusOut>", self._scenario_head_changed)
        _hk_ent.bind("<Return>", self._scenario_head_changed)
        tk.Button(head, text="捕获按键", command=self._scenario_capture_hotkey, bg=BTN, fg=BLUE,
                  activebackground=BTN, activeforeground=FG, font=(FH, 8), padx=6,
                  cursor="hand2").pack(side="left", padx=(0, 8))
        self.scenario_enabled_var = tk.BooleanVar(value=True)
        tk.Checkbutton(head, text="启用", variable=self.scenario_enabled_var, bg=BG, fg=FG,
                       selectcolor=BTN, activebackground=BG, font=(FH, 9),
                       command=self._scenario_head_changed).pack(side="left")
        # v4.164★ 本局走五六剑(手动开关): 勾上 = 装备三选一避开整个武器大类、优先玉玺/七星灯。
        #   落到剧本 JSON 的 eqLiuJian; 页面端(updateScenarios)读它自动生成 equipPick 预设(也可在
        #   剧本 JSON 里直接写 equipPick 自定义 拒绝大类/优先名单, 每局自选, 不必勾这个)。
        self.scenario_eqliujian_var = tk.BooleanVar(value=False)
        tk.Checkbutton(head, text="本局走五六剑", variable=self.scenario_eqliujian_var, bg=BG, fg=FG,
                       selectcolor=BTN, activebackground=BG, font=(FH, 9),
                       command=self._scenario_head_changed).pack(side="left", padx=(0, 8))
        # v4.186★ 后台自动 随征 / 锦囊 的全局勾选框: 勾上 = 开启对应后台引擎(页面 setInterval 自动干活),
        #   取消 = 关掉引擎。即"快捷键勾选就启动; 不勾选就关闭"。引擎默认: 随征开 / 锦囊关。
        #   它是全局开关(不是某条剧本的属性), 直接推页面, 不进剧本 JSON。
        self.auto_follow_bg_var = tk.BooleanVar(value=True)
        tk.Checkbutton(head, text="后台自动随征", variable=self.auto_follow_bg_var, bg=BG, fg=FG,
                       selectcolor=BTN, activebackground=BG, font=(FH, 9),
                       command=lambda: self._scenario_auto_bg("follow")).pack(side="left", padx=(0, 8))
        self.auto_spell_bg_var = tk.BooleanVar(value=False)
        tk.Checkbutton(head, text="后台自动锦囊", variable=self.auto_spell_bg_var, bg=BG, fg=FG,
                       selectcolor=BTN, activebackground=BG, font=(FH, 9),
                       command=lambda: self._scenario_auto_bg("spell")).pack(side="left", padx=(0, 8))
        self.auto_dismiss_bg_var = tk.BooleanVar(value=False)
        tk.Checkbutton(head, text="后台自动遣散", variable=self.auto_dismiss_bg_var, bg=BG, fg=FG,
                       selectcolor=BTN, activebackground=BG, font=(FH, 9),
                       command=lambda: self._scenario_auto_bg("dismiss")).pack(side="left", padx=(0, 8))

        head2 = tk.Frame(right, bg=BG)
        head2.pack(fill="x", pady=(4, 0))
        tk.Label(head2, text="触发方式:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left")
        self.scenario_trigger_var = tk.StringVar(value=SCENARIO_TRIGGER_MODE_PAIRS[0][0])
        _tg_cb = ttk.Combobox(head2, textvariable=self.scenario_trigger_var, state="readonly",
                              width=24, font=(FH, 9),
                              values=[p[0] for p in SCENARIO_TRIGGER_MODE_PAIRS])
        _tg_cb.pack(side="left", padx=(2, 8))
        _tg_cb.bind("<<ComboboxSelected>>", self._scenario_head_changed)
        tk.Label(head2, text="运行次数:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left")
        self.scenario_runcount_var = tk.StringVar(value="1")
        _rc_ent = tk.Entry(head2, textvariable=self.scenario_runcount_var, bg=BTN, fg=FG,
                           insertbackground=FG, font=(FH, 9), width=4)
        _rc_ent.pack(side="left", padx=(2, 2))
        _rc_ent.bind("<FocusOut>", self._scenario_head_changed)
        _rc_ent.bind("<Return>", self._scenario_head_changed)
        tk.Label(head2, text="(0=一直跑)", bg=BG, fg=DIM, font=(FH, 8)).pack(side="left", padx=(0, 8))
        tk.Label(head2, text="速度:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left")
        self.scenario_speed_var = tk.StringVar(value="1")
        _sp_ent = tk.Entry(head2, textvariable=self.scenario_speed_var, bg=BTN, fg=FG,
                           insertbackground=FG, font=(FH, 9), width=4)
        _sp_ent.pack(side="left", padx=(2, 2))
        _sp_ent.bind("<FocusOut>", self._scenario_head_changed)
        _sp_ent.bind("<Return>", self._scenario_head_changed)
        tk.Label(head2, text="倍 (0.1~100, 填几就是几倍速, 越大越快; 太大→等待被压没, 易『发了没生效』)", bg=BG, fg=DIM,
                 font=(FH, 8)).pack(side="left")
        # v4.67★ 总时长封套: 「这个剧本整段最多花多久」—— 到点走【中止收尾】(后面的步骤跳过,
        #   收尾挑空照做), 不是把页面端整个掐断。为什么要有它: 剧本里一旦有 waitUntil / 循环
        #   加等待, 参数写猛一点就是"一直跑", 而现场(排位/挂机)没人盯着 —— 一个兜底比一堆
        #   精确的等待条件更实在。0 = 不限(默认), 与「运行次数」的 0 是同一种"不限"。
        #   ⚠ 它判在【步骤与步骤之间】: 单个步骤自己在等多久, 由那一步的「超时」说了算 ——
        #   一个 waitUntil 正卡着 30 秒的时候封套拦不住它, 所以别指望"填 60 就一定 60 内停"。
        tk.Label(head2, text="最长运行:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left", padx=(8, 0))
        self.scenario_maxdur_var = tk.StringVar(value="0")
        _md_ent = tk.Entry(head2, textvariable=self.scenario_maxdur_var, bg=BTN, fg=FG,
                           insertbackground=FG, font=(FH, 9), width=5)
        _md_ent.pack(side="left", padx=(2, 2))
        _md_ent.bind("<FocusOut>", self._scenario_head_changed)
        _md_ent.bind("<Return>", self._scenario_head_changed)
        tk.Label(head2, text="秒 (0=不限, 跑满就收尾)", bg=BG, fg=DIM,
                 font=(FH, 8)).pack(side="left")

        head3 = tk.Frame(right, bg=BG)
        head3.pack(fill="x", pady=(4, 0))
        # v4.55★ 原叫「运行时触发」—— 用户看不懂("是开始吗? 还是每步都触发?")。
        #   它的真实语义就是【按下触发键、本剧本刚开始跑的那一下】: 先把选的剧本整个跑完,
        #   再回来跑本剧本。不写清楚就会被理解成"运行期间反复触发"。
        tk.Label(head3, text="开跑前先执行:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left")
        self.scenario_onstart_var = tk.StringVar()
        self.scenario_onstart_cb = ttk.Combobox(head3, textvariable=self.scenario_onstart_var,
                                                width=16, font=(FH, 9))
        self.scenario_onstart_cb.pack(side="left", padx=(2, 8))
        self.scenario_onstart_cb.bind("<<ComboboxSelected>>", self._scenario_head_changed)
        tk.Label(head3, text="跑完后接着执行:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left")
        self.scenario_onend_var = tk.StringVar()
        self.scenario_onend_cb = ttk.Combobox(head3, textvariable=self.scenario_onend_var,
                                              width=16, font=(FH, 9))
        self.scenario_onend_cb.pack(side="left", padx=(2, 8))
        self.scenario_onend_cb.bind("<<ComboboxSelected>>", self._scenario_head_changed)
        tk.Label(head3, text="(都留空=不触发; 选中的剧本会就地整个跑完, 再回来继续)",
                 bg=BG, fg=DIM, font=(FH, 8)).pack(side="left")

        cols = ("no", "op", "param", "desc")
        # v4.41★ selectmode="extended": 步骤要能多选 —— 复制/剪切/删除都是按"选中的那些行"来的。
        tree_wrap = tk.Frame(right, bg=BG)
        tree_wrap.pack(fill="both", expand=True, pady=(4, 2))
        self.scenario_tree = ttk.Treeview(tree_wrap, columns=cols, show="headings", height=13,
                                          selectmode="extended")
        for c, t, w in (("no", "#", 34), ("op", "步骤", 140), ("param", "参数", 240), ("desc", "说明", 330)):
            self.scenario_tree.heading(c, text=t)
            self.scenario_tree.column(c, width=w, anchor="w")
        tree_ysb = tk.Scrollbar(tree_wrap, orient="vertical", command=self.scenario_tree.yview)
        self.scenario_tree.configure(yscrollcommand=tree_ysb.set)
        tree_ysb.pack(side="right", fill="y")
        self.scenario_tree.pack(side="left", fill="both", expand=True)
        # v4.67★ 正在执行的那一行: 金色字 + 选中底色(一眼能看见)。
        #   为什么不直接用系统选中态: 那个会被用户随手一点抢走, 而且表格一刷新就没了 ——
        #   "脚本现在跑到哪儿"是【持续显示】的信息, 得有自己的标签。
        try:
            # 整块底色 —— 选中 if/循环 时, 把整段(开始→结束)染一层淡底, 用"整块成团"代替一堆彩色字
            self.scenario_tree.tag_configure("block", background="#1d2733")
            # 配对端点高亮(选中块的 开始↔结束 两行)
            self.scenario_tree.tag_configure("pair", background=SEL_BG)
            # 运行时高亮 —— 金色字 + 选中底色, 持续显示跑到哪步
            self.scenario_tree.tag_configure("run", background=SEL_BG, foreground=GOLD)
        except Exception as e:
            sys.stderr.write(f"[剧本] 高亮配色设置失败(界面可能已关闭): {e}\n")
        self.scenario_tree.bind("<Double-1>", lambda e: self._scenario_edit_step())
        # v4.??★ 想法A: 选中某步 → 高亮其配对的 如果↔结束如果 / 循环开始↔循环结束
        self.scenario_tree.bind("<<TreeviewSelect>>", lambda e: self._scenario_pair_refresh())
        # v4.41★ 键盘也能复制/剪切/粘贴, 单行和小键盘两套都绑上
        self.scenario_tree.bind("<Control-c>", lambda e: self._scenario_copy_steps())
        self.scenario_tree.bind("<Control-C>", lambda e: self._scenario_copy_steps())
        self.scenario_tree.bind("<Control-x>", lambda e: self._scenario_cut_steps())
        self.scenario_tree.bind("<Control-X>", lambda e: self._scenario_cut_steps())
        self.scenario_tree.bind("<Control-v>", lambda e: self._scenario_paste_steps())
        self.scenario_tree.bind("<Control-V>", lambda e: self._scenario_paste_steps())
        # v4.66★ 批量操作配套快捷键: 全选 / 就地复制 / 删除所选。
        #   selectmode="extended" 一直开着(Shift 连选、Ctrl 点选本来就能用), 缺的是
        #   "选中之后能一把全删", 以及不用去摸工具栏。一律 return "break" 吃掉事件,
        #   否则 Treeview 自己还会处理一遍(Delete 会乱跳、Ctrl+A 无效)。
        self.scenario_tree.bind("<Control-a>", lambda e: (self._scenario_select_all(), "break")[1])
        self.scenario_tree.bind("<Control-A>", lambda e: (self._scenario_select_all(), "break")[1])
        self.scenario_tree.bind("<Delete>", lambda e: (self._scenario_del_step(), "break")[1])
        self.scenario_tree.bind("<Control-d>", lambda e: (self._scenario_dup_steps(), "break")[1])
        self.scenario_tree.bind("<Control-D>", lambda e: (self._scenario_dup_steps(), "break")[1])

        sbtns = tk.Frame(right, bg=BG)
        sbtns.pack(fill="x")
        # v4.??★ 想法A「剧本体检」: 校验括号/嵌套合法性, 一键修复悬空括号
        #   (引擎缺 ifEnd 时会提示"用助手的「剧本体检」修一下括号" —— 这里正是那个入口)
        tk.Button(sbtns, text="剧本体检", bg=BTN, fg=RED, font=(FH, 8), width=8,
                  command=self._scenario_health).pack(side="left", padx=1, pady=2)
        tk.Button(sbtns, text="体检全部", bg=BTN, fg=RED, font=(FH, 8), width=8,
                  command=self._scenario_health_all).pack(side="left", padx=1, pady=2)
        # v4.28★ 「+ 预设」: 常用剧本一键导入, 免去从零拼步骤(用户反馈"自己剧本也有点难")
        # v4.41★ 改成【按钮 + 现建菜单】: 原来的 Menubutton 菜单是建好就不动了, 用户新存的
        #   自定义预设要重启助手才看得见; 现在每次点开都重建, 存完立刻能用。
        pm = tk.Button(sbtns, text="存预设 ▾", bg=BTN, fg=GOLD, font=(FH, 9, "bold"),
                       relief="raised", activebackground=BG, activeforeground=GOLD)
        pmenu = tk.Menu(pm, tearoff=0, bg=BTN, fg=FG, activebackground=SEL_BG,
                        activeforeground=SEL_FG, font=(FH, 9))

        def _rebuild_preset_menu():
            pmenu.delete(0, "end")
            n_builtin = len(SCENARIO_PRESETS)
            # v4.63★ 预设按来源分成两个"文件夹"(用户原话"你写的预设可以在剧本那加一个文件夹
            #   似的, 预设放进那下面, 我可以修改"): 以前是十几个内置 + 自建的平铺成一整列,
            #   想找自己存的要一直翻到底。现在内置归一个子菜单, 自建归一个子菜单。
            sub_bi = tk.Menu(pmenu, tearoff=0, bg=BTN, fg=FG, activebackground=SEL_BG,
                             activeforeground=SEL_FG, font=(FH, 9))
            for _pi, (_pn, _pd, _ps) in enumerate(SCENARIO_PRESETS):
                sub_bi.add_command(label=f"{_pn} ({len(_ps)}步)",
                                   command=lambda i=_pi: self._scenario_apply_preset(i))
            if SCENARIO_PRESETS:
                sub_bi.add_separator()
                # ★「我写的预设你也能改」: 内置预设是代码里的常量(直接改会被下次更新覆盖掉),
                #   所以这里给的是"复制一份成你自己的预设" —— 复制完就和自建预设完全一样,
                #   能改名 / 删除 / 用当前剧本的步骤覆盖它。
                sub_fork = tk.Menu(sub_bi, tearoff=0, bg=BTN, fg=FG, activebackground=SEL_BG,
                                   activeforeground=SEL_FG, font=(FH, 9))
                for _pi, (_pn, _pd, _ps) in enumerate(SCENARIO_PRESETS):
                    sub_fork.add_command(label=_pn,
                                         command=lambda i=_pi: self._scenario_fork_builtin(i))
                sub_bi.add_cascade(label="复制成我的预设(之后可改)…", menu=sub_fork)
            pmenu.add_cascade(label="内置预设(我写的) ▸", menu=sub_bi)

            up = self._scenario_user_presets()
            sub_my = tk.Menu(pmenu, tearoff=0, bg=BTN, fg=FG, activebackground=SEL_BG,
                             activeforeground=SEL_FG, font=(FH, 9))
            # v4.55★ 这里原来写的是 `for _ui, (_un, _us) in enumerate(up)`。
            #   up 里是 dict({"name":..,"steps":[..]}), 而 enumerate 给的是 (下标, dict),
            #   于是 (_un, _us) 是在解包【dict 的 key】—— 恰好两个 key(name/steps) 所以
            #   【不报错】, 但 _un 恒等于字符串 "name", _us 恒等于字符串 "steps"(len=5)
            #   → 菜单里每个自建预设都显示成 "★name (5步)", 用户报的现象就是"存预设里的
            #   名字都是 name"。下面直接用 _up.get(...) 才是对的。
            for _ui, _up in enumerate(up):
                _un = str(_up.get("name") or "未命名")
                _us = _up.get("steps") or []
                sub_my.add_command(label=f"{_un} ({len(_us)}步)",
                                   command=lambda i=n_builtin + _ui: self._scenario_apply_preset(i))
            if not up:
                sub_my.add_command(label="(还没有 — 用下面『把当前剧本存为预设』)", state="disabled")
            pmenu.add_cascade(label="我的预设(★自己存的) ▸", menu=sub_my)
            pmenu.add_separator()
            pmenu.add_command(label="把当前剧本存为预设…", command=self._scenario_save_as_preset)
            if up:
                sub = tk.Menu(pmenu, tearoff=0, bg=BTN, fg=FG, activebackground=SEL_BG,
                              activeforeground=SEL_FG, font=(FH, 9))
                # v4.56★ 「预设也能改」: 原来只能存新的 / 删旧的, 想改个名字或把步骤更新一下
                #   必须"删掉重存"。现在每个预设一项: 改名 + 可选"用当前剧本的步骤覆盖它"。
                for _ui, _up in enumerate(up):
                    sub.add_command(label=f"修改「{_up.get('name') or '未命名'}」",
                                    command=lambda i=_ui: self._scenario_rename_preset(i))
                # v4.63★ 标签写清它到底干几件事: 用户看到"修改某个预设"点进去, 第一个框却是改名,
                #   于是问"修改预设怎么成改名了? 那个是重命名?" —— 这里直接把两件事标出来。
                pmenu.add_cascade(label="修改我的预设(改名 / 覆盖步骤)…", menu=sub)
                sub2 = tk.Menu(pmenu, tearoff=0, bg=BTN, fg=FG, activebackground=SEL_BG,
                               activeforeground=SEL_FG, font=(FH, 9))
                for _ui, _up in enumerate(up):
                    sub2.add_command(label=f"删除「{_up.get('name') or '未命名'}」",
                                     command=lambda i=_ui: self._scenario_del_preset(i))
                pmenu.add_cascade(label="删除某个预设…", menu=sub2)

        def _pop_presets():
            _rebuild_preset_menu()
            try:
                pmenu.tk_popup(pm.winfo_rootx(), pm.winfo_rooty() + pm.winfo_height())
            finally:
                try:
                    pmenu.grab_release()
                except Exception:
                    _SIL.note("ui/scenario_tab.py:303", "pmenu.grab_release()")

        pm.configure(command=_pop_presets)
        pm.pack(side="left", padx=(2, 8))
        for txt, cb in (("新增步骤", self._scenario_add_step), ("编辑", self._scenario_edit_step),
                        ("删除所选", self._scenario_del_step), ("上移", lambda: self._scenario_move_step(-1)),
                        ("下移", lambda: self._scenario_move_step(1)), ("清空", self._scenario_clear_steps)):
            tk.Button(sbtns, text=txt, bg=BTN, fg=FG, font=(FH, 9), command=cb).pack(side="left", padx=2)
        # v4.??★ L1 否则如果 + B 沙盒试运行: 快捷入口
        tk.Button(sbtns, text="否则如果", bg=BTN, fg=FG, font=(FH, 9),
                  command=self._scenario_add_elseif).pack(side="left", padx=2)
        tk.Button(sbtns, text="沙盒试运行", bg=BTN, fg=GOLD, font=(FH, 9),
                  command=self._scenario_sandbox_run).pack(side="left", padx=2)

        # v4.41★ 步骤复制/剪切/粘贴(可多选)
        # v4.66★ 补上「复制一份」(就地复制, 不用再粘贴一次) 和「全选」——
        #   删除/上移/下移现在对整个选中段生效, 所以入口都放这儿, 一行说明写清快捷键。
        sbtns2 = tk.Frame(right, bg=BG)
        sbtns2.pack(fill="x", pady=(4, 0))
        for txt, cb in (("复制步骤", self._scenario_copy_steps), ("剪切步骤", self._scenario_cut_steps),
                        ("粘贴步骤", self._scenario_paste_steps), ("复制一份", self._scenario_dup_steps),
                        ("全选", self._scenario_select_all)):
            tk.Button(sbtns2, text=txt, bg=BTN, fg=FG, font=(FH, 9), command=cb).pack(side="left", padx=2)
        tk.Label(sbtns2, text="(可多选: Shift/Ctrl 点选; 删除·上移·下移 对整段生效; "
                              "Ctrl+A 全选 · Ctrl+C/X/V 复制剪切粘贴 · Ctrl+D 复制一份 · Delete 删除所选)",
                 bg=BG, fg=DIM, font=(FH, 8)).pack(side="left", padx=(8, 0))

        rbtns = tk.Frame(right, bg=BG)
        rbtns.pack(fill="x", pady=(6, 0))
        tk.Button(rbtns, text="保存并推送", bg=BTN, fg=GOLD, font=(FH, 9, "bold"),
                  command=lambda: self._scenario_save(push=True, notify=True)).pack(side="left", padx=2)
        tk.Button(rbtns, text="执行一次", bg=BTN, fg=FG, font=(FH, 9, "bold"),
                  command=self._scenario_run_once).pack(side="left", padx=2)
        tk.Button(rbtns, text="停止", bg=BTN, fg=RED, font=(FH, 9),
                  command=self._scenario_stop).pack(side="left", padx=2)
        # v4.67★ 「试跑前 N 步」—— 写剧本时最需要的一件事。
        #   以前只能整段跑: 第 3 步写错了, 前两步已经落进局里(卡买了、金币花了), 要么认了要么重开。
        #   有了它就能一段一段往前推, 每一步都先看日志确认再往下。
        tk.Label(rbtns, text="试跑", bg=BG, fg=DIM, font=(FH, 9)).pack(side="left", padx=(12, 2))
        self.scenario_trial_var = tk.StringVar(value="3")
        tk.Entry(rbtns, textvariable=self.scenario_trial_var, width=4, bg=BTN, fg=FG,
                 font=(FH, 9), insertbackground=FG, relief="flat").pack(side="left")
        tk.Button(rbtns, text="只跑这几步", bg=BTN, fg=FG, font=(FH, 9),
                  command=self._scenario_run_trial).pack(side="left", padx=(3, 2))
        tk.Label(rbtns, text="(从第1步开始跑几步就停, 不算失败)", bg=BG, fg=DIM,
                 font=(FH, 8)).pack(side="left", padx=(2, 0))
        self.scenario_status_var = tk.StringVar(value="未执行")
        tk.Label(rbtns, textvariable=self.scenario_status_var, bg=BG, fg=DIM,
                 font=(FH, 9)).pack(side="left", padx=10)

        tk.Label(right,
                 text="提示: 遣散=手牌/阵上找到就遣散(名字可多个); 牌库找卡=商店里找, 找不到就刷新(次数填下面); "
                      "条件/循环用「开始...结束」两行配对; 每步可勾「失败也继续」。"
                      " ■ 停止 = 立刻中断: 正在跑的那一步也一起停下(等待/刷新/重试都不再继续), "
                      "之后只把屏幕上的挑牌窗收干净(不然游戏会禁止你手动操作)。",
                 bg=BG, fg=DIM, font=(FH, 8), justify="left", wraplength=760).pack(anchor="w", pady=(4, 0))

        self._scenario_refresh_list()

    # ---------- 剧本: 列表/表格 ----------
    def _scenario_spell_presets(self):
        """把 data/tavern_spells.json 里本季可用锦囊名读到下拉预设里, 省得手打错字/品质。"""
        try:
            p = os.path.join(DATA_DIR, "tavern_spells.json")
            if not os.path.exists(p):
                return []
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            names = []
            for v in d.values():
                n = (v or {}).get("n")
                if n and n not in names:
                    names.append(n)
            return sorted(names, key=lambda x: (len(x), x))
        except Exception:
            return []

    def _scenario_cur(self):
        try:
            if not self.scenario_scripts:
                return None
            i = self.scenario_cur_idx
            if i < 0 or i >= len(self.scenario_scripts):
                i = 0
                self.scenario_cur_idx = 0
            return self.scenario_scripts[i]
        except Exception:
            return None

    def _scenario_warn_if_script_old(self):
        """v4.57★ 下发剧本前核对游戏页脚本版本 —— 版本不够时必须写进日志。

        为什么非做不可: "脚本旧"造成的故障, 现象全都长得像"剧本写错了" ——
          · 新步骤(如「记数值快照」)只打一句"未知步骤类型", 然后被静默跳过;
          · 新条件(如「阶段=招募」)会退化成"手牌里找这张卡", 日志报"条件不成立[招募无]"。
        用户为此连着两轮在剧本里翻找, 而底部那个版本标签根本不显眼 —— 所以让它跟着剧本一起出现。
        只警告一次: 脚本每秒上报一次版本, 不拉住会刷屏; 脚本更新过之后再降级则允许重新提醒。
        """
        cur = getattr(self, "script_ver_num", None)
        if not cur:
            return                              # 还没拿到脚本版本(没连上), 不猜
        if cur >= MIN_SCRIPT_VER:
            self._scenario_ver_warned = False   # 已经是最新 → 解除静音
            return
        if getattr(self, "_scenario_ver_warned", False):
            return
        self._scenario_ver_warned = True
        need = "v" + ".".join(str(x) for x in MIN_SCRIPT_VER)
        self.app.log(f"[剧本] ⚠ 游戏页脚本是 v{getattr(self, 'script_ver', '?')}, 助手需要 ≥{need}: "
                 f"剧本里 {need} 才有的步骤会被跳过、条件(阶段/数值变化)会被当成『手牌里找这张卡』"
                 f"而判错 —— 点工具栏【⚡一键注入】更新脚本, 再回游戏页按 F5。", level="WARN")

    def _scenario_collect(self, include_transport_fields=False):
        """导出成下发给页面的结构

        include_transport_fields=True 仅供推送游戏页时附加执行期派生数据; 配置和剧本导出保持干净。

        v4.31★ 每次下发前都重跑一遍 _scenario_fill_target_ids:
          老剧本(v4.30 之前建的 / 从配置文件读回来的)steps 里没有 targetIds, 页面就只能拿
          中文名裸匹配 —— 实机现象就是"用锦囊 顺手牵羊 报『手牌里没有这张锦囊』", 而手牌里
          明明有『顺手牵羊·三』(库里名字带间隔号, 名字对不上); 牌库找卡也一样只能靠名字。
          反查是幂等的, 每次重算最省心, 还顺带修好"后来才补进 cards.json 的名字"。
        """
        # v4.57★ 下发前先核对脚本版本: "剧本对、脚本旧"的锅必须当场说清楚, 不能让它烂在日志外
        self._scenario_warn_if_script_old()
        out = []
        for s in (self.scenario_scripts or []):
            steps = []
            stored_steps = []
            for st in (s.get("steps") or []):
                if not isinstance(st, dict):
                    steps.append(st)
                    stored_steps.append(st)
                    continue
                try:
                    enriched = _scenario_fill_target_ids(dict(st))
                except Exception:
                    enriched = dict(st)
                stored_steps.append(dict(enriched))
                if include_transport_fields and str(enriched.get("op") or "") == "shopFind":
                    enriched["targetGroups"] = _scenario_shopfind_target_groups(enriched)
                steps.append(enriched)
            s["steps"] = stored_steps   # 保存反查 ID, 但不把执行期派生字段写回配置
            out.append({
                "id": s.get("id") or "",
                "name": s.get("name") or "",
                "hotkey": (s.get("hotkey") or "").strip(),
                # v4.55★ 剧本级运行设置: 页面端拿这些决定"怎么被触发、跑几遍、多快、跑完触发谁"
                "triggerMode": str(s.get("triggerMode") or "press"),
                "runCount": _scenario_safe_int(s.get("runCount"), 1, 0),
                # v4.166★ 上限 20 → 100: 与页面端 updateScenarios / 开跑处两处夹取同步放宽,
                #   否则填 100 会被页面夹成 20(界面显示 100、实际跑 20, 完全看不出来)。
                "speed": _scenario_safe_float(s.get("speed"), 1.0, 0.1, 100.0),
                # v4.67★ 总时长上限(秒; 0 = 不限) —— 页面端 _scRunSteps 的封套靠它
                "maxDuration": _scenario_safe_int(s.get("maxDuration"), 0, 0, 3600),
                "onStart": str(s.get("onStart") or ""),
                "onEnd": str(s.get("onEnd") or ""),
                "enabled": (s.get("enabled", True) is not False),
                # v4.19x★ 「本局走五六剑」必须随剧本下发: 桥端 updateScenarios 收下后会
                #   同步到 window.sgsScenario 顶层, 装备三选一过滤器读那里(15117 行那段)。
                #   以前这里不带它 → 推送/存盘(cfg["scenarios"] 就是这份输出)两头都丢:
                #   页面上勾了等于没勾, 重启助手勾选状态也直接没了。equipPick(剧本 JSON
                #   里手写的自定义装备规则)同一条链路, 一起带上。
                "eqLiuJian": (s.get("eqLiuJian") is True),
                "steps": steps,
            })
            if s.get("equipPick"):
                out[-1]["equipPick"] = s["equipPick"]
        return out

    def _scenario_refresh_list(self):
        try:
            self.scenario_listbox.delete(0, tk.END)
            for s in self.scenario_scripts:
                hk = s.get("hotkey") or ""
                self.scenario_listbox.insert(
                    tk.END, f"{s.get('name')}" + (f" [{hk}]" if hk else "") + f" ({len(s.get('steps') or [])}步)")
            if self.scenario_scripts:
                idx = max(0, min(self.scenario_cur_idx, len(self.scenario_scripts) - 1))
                self.scenario_cur_idx = idx
                self.scenario_listbox.selection_clear(0, tk.END)
                self.scenario_listbox.selection_set(idx)
                cur = self._scenario_cur() or {}
                self.scenario_name_var.set(cur.get("name") or "")
                self.scenario_hotkey_var.set(cur.get("hotkey") or "")
                self._scenario_fill_head(cur)
            else:
                self.scenario_name_var.set("")
                self.scenario_hotkey_var.set("")
                self._scenario_fill_head({})
            self._scenario_refresh_steps()
        except Exception as e:
            self.app.log(f"[剧本] 刷新列表失败: {e}", level="ERROR")

    def _scenario_on_select(self):
        sel = self.scenario_listbox.curselection()
        if not sel:
            return
        self._scenario_apply_head()          # 先把界面上改过的设置写回"旧的那个"
        self.scenario_cur_idx = int(sel[0])
        cur = self._scenario_cur() or {}
        self._scenario_fill_head(cur)        # v4.55★ 换剧本必须回填, 否则屏幕上是上一个剧本的设置
        self._scenario_refresh_steps()

    # ---------- v4.55★ 剧本级设置(触发按键/触发方式/次数/速度/运行时·结束时触发) ----------
    def _scenario_other_names(self, cur=None):
        """除自己以外的其它剧本名 —— 给「运行时触发 / 结束时触发」两个下拉当候选。"""
        me = str((cur if isinstance(cur, dict) else self._scenario_cur() or {}).get("name") or "")
        out = []
        for x in (self.scenario_scripts or []):
            nm = str((x or {}).get("name") or "").strip()
            if nm and nm != me and nm not in out:
                out.append(nm)
        return out

    def _scenario_fill_head(self, cur):
        """把当前剧本的剧本级设置回填到头部控件。

        为什么必须回填: 这些控件是【一份共用的】, 不是每个剧本一套 —— 不回填的话,
        切换剧本时屏幕上留着上一个剧本的触发方式/次数/速度, 用户会以为"这个剧本也是这么设的",
        然后按下去发现"不是我想要的行为"(却看不出哪里设错了)。

        回填失败【不静默吞掉】: 必须留一行日志。否则用户看到的是"我设了触发方式, 切个剧本
        回来它自己变了/没生效", 而哪儿都查不到原因 —— 这正是这一轮要消灭的那类现象。
        """
        cur = cur if isinstance(cur, dict) else {}
        _names = [""] + self._scenario_other_names(cur)
        try:
            # v4.166★ 上限 20 → 100(回填: 存了 100 就要显示 100, 不然界面会把它夹回 20 显示)
            _sp = _scenario_safe_float(cur.get("speed"), 1.0, 0.1, 100.0)
            for _var, _v in (
                    (getattr(self, "scenario_name_var", None), str(cur.get("name") or "")),
                    (getattr(self, "scenario_hotkey_var", None), str(cur.get("hotkey") or "")),
                    (getattr(self, "scenario_trigger_var", None), _scenario_value_to_pair(
                        SCENARIO_TRIGGER_MODE_PAIRS, cur.get("triggerMode"),
                        SCENARIO_TRIGGER_MODE_PAIRS[0][0])),
                    (getattr(self, "scenario_runcount_var", None),
                     str(_scenario_safe_int(cur.get("runCount"), 1, 0))),
                    (getattr(self, "scenario_speed_var", None),
                     str(int(_sp)) if abs(_sp - int(_sp)) < 1e-9 else str(_sp)),
                    # v4.67★ 总时长上限(秒) —— 上下限与页面端 updateScenarios 的夹取保持一致
                    #   (0 = 不限, 最多 3600), 否则界面会显示一个页面上根本不生效的数。
                    (getattr(self, "scenario_maxdur_var", None),
                     str(_scenario_safe_int(cur.get("maxDuration"), 0, 0, 3600))),
                    (getattr(self, "scenario_enabled_var", None),
                     cur.get("enabled", True) is not False),
                    (getattr(self, "scenario_eqliujian_var", None),
                     bool(cur.get("eqLiuJian") is True)),
                    (getattr(self, "scenario_onstart_var", None), str(cur.get("onStart") or "")),
                    (getattr(self, "scenario_onend_var", None), str(cur.get("onEnd") or ""))):
                if _var is not None:
                    _var.set(_v)
            # 两个"触发哪个剧本"的下拉: 候选取【别的剧本名】, 第一行留空 = 不触发。
            #   (当前值不在候选里时把它并进候选 —— 否则"触发一个已被改名/删掉的剧本"
            #    会让下拉自动跳成空, 用户以为自己没设过。)
            for _cb, _key in ((getattr(self, "scenario_onstart_cb", None), "onStart"),
                              (getattr(self, "scenario_onend_cb", None), "onEnd")):
                if _cb is None:
                    continue
                _cur_v = str(cur.get(_key) or "")
                _cb.configure(values=_names if _cur_v in _names else ([_cur_v] + _names))
        except Exception as e:
            self.app.log(f"[剧本] 回填剧本级设置失败(头部显示的可能不是当前剧本的值): {e}", level="WARN")

    def _scenario_head_changed(self, event=None):
        """头部任意一项改动 → 立刻写回当前剧本并落盘(编辑即保存, 不用再点『保存并推送』)。"""
        if getattr(self, "_scenario_head_busy", False):
            return
        self._scenario_head_busy = True
        try:
            self._scenario_apply_head()
            cur = self._scenario_cur()
            if cur:
                self._scenario_save(push=True)
        except Exception as e:
            self.app.log(f"[剧本] 剧本级设置保存失败: {e}", level="ERROR")
        finally:
            self._scenario_head_busy = False

    def _scenario_auto_bg(self, which):
        """v4.186★ 「后台自动随征 / 后台自动锦囊」勾选框 → 直接启停对应后台引擎。
        勾上 = 开启(页面 setInterval 自动干活); 取消 = 关掉。即"快捷键勾选就启动; 不勾选就关闭"。
        """
        # v4.19x★ 修: 原来只认 follow / spell 两路 —— 勾「后台自动遣散」时 `on` 落到
        #   else 分支取的是【自动锦囊】复选框的值、日志也把"遣散"错标成"锦囊":
        #   勾遣散时实际推的是锦囊的开关状态, 推送目标(update_auto_dismiss)倒是遣散的,
        #   于是"遣散开了没"完全取决于锦囊那个框勾没勾 —— 看不出规律的功能失灵。
        _var = {"follow": "auto_follow_bg_var", "spell": "auto_spell_bg_var",
                "dismiss": "auto_dismiss_bg_var"}.get(str(which))
        label = {"follow": "随征", "spell": "锦囊", "dismiss": "遣散"}.get(str(which), "?")
        if not _var or not hasattr(self, _var):
            return
        on = getattr(self, _var).get()
        if not (self.app and self.app.ops and self.app.ops.connected()):
            self.app.log("[后台自动%s] 桥未连接 —— 仅记录勾选状态, 连上游戏后再勾一次才会真正生效" % label)
            return
        try:
            if which == "follow":
                self.app.ops.update_auto_follow_up(on)
            elif which == "spell":
                self.app.ops.update_auto_spell(on)
            else:
                self.app.ops.update_auto_dismiss(on, self.app.cfg.get("dismiss_policy", {}))
        except Exception as e:
            self.app.log("[后台自动%s] 推送失败: %s" % (label, e), level="ERROR")

    def _scenario_keysym_to_hotkey(self, ev):
        """Tk 按键事件 → 页面端认得的写法(如 ctrl+alt+1)。不能当触发键就返回 None。

        规则(不是随便收):
          · 必须带 Ctrl 或 Alt —— 光一个字母/数字在游戏里会抢普通操作(1-6 买卡 / R 刷新 / F 锁定);
          · Shift 只能当附加项(Shift+1 在游戏里已经是"遣散最右手牌")。
        这样"捕获"出来的写法, 一定和脚本端 _scHotkeyMatch 的比对方式一致,
        不会出现"手打 ctrl+alt+1 少个空格就永远没反应"。
        """
        ks = str(getattr(ev, "keysym", "") or "")
        low = ks.lower()
        if low in ("control_l", "control_r", "alt_l", "alt_r", "shift_l", "shift_r",
                   "super_l", "super_r", "caps_lock", "escape", ""):
            return None
        st = int(getattr(ev, "state", 0) or 0)
        mods = []
        if st & 0x4:
            mods.append("ctrl")
        if st & 0x8:
            mods.append("alt")
        if st & 0x1:
            mods.append("shift")
        if len(ks) == 1 and ks.isalnum():
            key = low
        elif low.startswith("f") and low[1:].isdigit() and 1 <= int(low[1:]) <= 12:
            key = low
        else:
            return None
        if "ctrl" not in mods and "alt" not in mods:
            return None
        return "+".join([m for m in ("ctrl", "alt", "shift") if m in mods] + [key])

    def _scenario_capture_hotkey(self):
        """弹个小窗, 让用户【按一下】想要的组合键 —— 比手打可靠(大小写/空格/全角都不会错)。"""
        cur = self._scenario_cur()
        if not cur:
            self.app.log("[剧本] 请先在左边选中一个剧本", level="WARN")
            return
        dlg = Toplevel(self.app.root)
        dlg.title("捕获触发按键")
        dlg.configure(bg=BG)
        dlg.transient(self.app.root)
        dlg.resizable(False, False)
        tk.Label(dlg, text="直接按下你要用的组合键", bg=BG, fg=FG,
                 font=(FH, 11, "bold")).pack(padx=26, pady=(16, 2))
        tk.Label(dlg, text="必须带 Ctrl 或 Alt(如 Ctrl+Alt+1); Esc 取消",
                 bg=BG, fg=DIM, font=(FH, 9)).pack(padx=26)
        show = tk.Label(dlg, text="…", bg=BG, fg=GOLD, font=(FH, 14, "bold"))
        show.pack(pady=14)

        def _finish(spec):
            # 收窗口分两步、各自兜底, 但都【不静默】: grab_release 在"没抓住"时会抛, 那也不能挡住
            #   destroy —— 否则用户会看到一个关不掉的窗口, 比少一条日志严重得多。
            try:
                dlg.grab_release()
            except Exception as e:
                self.app.log(f"[剧本] 释放按键捕获窗口的焦点失败(不影响使用): {e}", level="DEBUG")
            try:
                dlg.destroy()
            except Exception as e:
                self.app.log(f"[剧本] 关闭按键捕获窗口失败: {e}", level="WARN")
            if not spec:
                self.app.log("[剧本] 已取消捕获按键")
                return
            _var = getattr(self, "scenario_hotkey_var", None)
            if _var is None:
                self.app.log("[剧本] 找不到『触发按键』输入框, 捕获到的按键没法填进去", level="ERROR")
                return
            _var.set(spec)
            self._scenario_apply_head()
            self._scenario_save(push=True)
            self.app.log(f"[剧本] 「{cur.get('name') or ''}」触发按键已设为 {spec}")

        def _on_key(ev):
            if str(getattr(ev, "keysym", "")) == "Escape":
                _finish("")
                return "break"
            spec = self._scenario_keysym_to_hotkey(ev)
            if spec is None:
                show.configure(text="这个键不能单独当触发键\n(要 Ctrl / Alt + 字母或数字)", fg="#ff8866")
                return "break"
            show.configure(text=spec, fg=GOLD)
            _finish(spec)
            return "break"

        dlg.bind("<KeyPress>", _on_key)
        try:
            dlg.grab_set()
        except Exception:
            _SIL.note("ui/scenario_tab.py:645", "dlg.grab_set()")
        dlg.focus_force()
        dlg.update_idletasks()
        try:
            x = self.app.root.winfo_rootx() + max(0, (self.app.root.winfo_width() - dlg.winfo_width()) // 2)
            y = self.app.root.winfo_rooty() + max(0, (self.app.root.winfo_height() - dlg.winfo_height()) // 3)
            dlg.geometry(f"+{x}+{y}")
        except Exception:
            _SIL.note("ui/scenario_tab.py:653", "x = self.app.root.winfo_rootx() + max(0, (self")

    def _scenario_apply_head(self):
        """把头部控件写回当前剧本(v4.55★ 除名字/按键外, 还有触发方式/次数/速度/触发链)。"""
        cur = self._scenario_cur()
        if not cur:
            return
        old = str(cur.get("name") or "").strip()
        try:
            nm = (self.scenario_name_var.get() or "").strip()
            cur["hotkey"] = (self.scenario_hotkey_var.get() or "").strip()
        except Exception:
            nm = old
        if nm:
            cur["name"] = nm
        # v4.55★ 触发方式 / 运行次数 / 速度 / 运行时触发 / 结束时触发 / 启用;
        #   v4.67★ 再加一个「最长运行」(秒; 0 = 不限) —— 总时长封套, 见页面端 _scRunSteps。
        #   控件没建起来(tab 未构建 / 无头调用)时跳过, 免得整个保存直接炸掉。
        try:
            cur["triggerMode"] = _scenario_pair_to_value(
                SCENARIO_TRIGGER_MODE_PAIRS, self.scenario_trigger_var.get(), "press")
            cur["runCount"] = _scenario_safe_int(self.scenario_runcount_var.get(), 1, 0)
            # v4.166★ 上限 20 → 100(应用: 界面填 100 要真存 100, 与页面端两处夹取一致)
            cur["speed"] = _scenario_safe_float(self.scenario_speed_var.get(), 1.0, 0.1, 100.0)
            # v4.67★ 总时长上限(秒; 0 = 不限)。夹到 0~3600 —— 与页面端 updateScenarios 同一套
            #   上下限: 两端不一致的话, 界面上写 7200、页面上只有 3600, 用户会以为"没生效"。
            cur["maxDuration"] = _scenario_safe_int(self.scenario_maxdur_var.get(), 0, 0, 3600)
            cur["enabled"] = bool(self.scenario_enabled_var.get())
            cur["eqLiuJian"] = bool(self.scenario_eqliujian_var.get())
            cur["onStart"] = (self.scenario_onstart_var.get() or "").strip()
            cur["onEnd"] = (self.scenario_onend_var.get() or "").strip()
        except Exception:
            _SIL.note("ui/scenario_tab.py:683", "cur['triggerMode'] = _scenario_pair_to_value( ")
        # v4.48★ 改名要顺手把别处【调用这个剧本】的步骤一起改掉。
        #   页面端是按名字找剧本的, 改了名却没改引用 → 运行时只报一句"找不到剧本"就跳过,
        #   而这种断链非常难看出来(得一条条步骤翻)。这里自动同步 + 日志说清楚改了几处。
        if nm and old and nm != old:
            n = self._scenario_rename_refs(old, nm)
            if n:
                self.app.log(f"[剧本] 「{old}」改名为「{nm}」, 已同步 {n} 处调用它的步骤")

    def _scenario_rename_refs(self, old, new):
        """把其它剧本里 callScenario 的目标从 old 改写成 new。返回改了几处。"""
        n = 0
        for s in (self.scenario_scripts or []):
            for st in (s.get("steps") or []):
                if not isinstance(st, dict):
                    continue
                if str(st.get("op") or "") == "callScenario" \
                        and str(st.get("target") or "").strip() == old:
                    st["target"] = new
                    n += 1
        return n

    def _scenario_ref_users(self, name):
        """谁在调用这个剧本? 返回 ['剧本A 第3步', ...] —— 删除/改名时用来提醒。"""
        out = []
        name = str(name or "").strip()
        if not name:
            return out
        for s in (self.scenario_scripts or []):
            snm = str((s or {}).get("name") or "")
            for i, st in enumerate((s.get("steps") or [])):
                if isinstance(st, dict) and str(st.get("op") or "") == "callScenario" \
                        and str(st.get("target") or "").strip() == name:
                    out.append(f"{snm} 第{i + 1}步")
        return out

    def _scenario_refresh_steps(self):
        try:
            self.scenario_tree.delete(*self.scenario_tree.get_children())
            cur = self._scenario_cur() or {}
            steps = cur.get("steps") or []
            # v4.??★ 想法A: 用结构层算出每步的缩进/配对, 让"谁套谁"一眼可见(不再靠括号脑补)
            outline = SSTRUCT.scenario_outline_rows(steps)
            for r in outline:
                i = r["idx"]
                s = steps[i]
                op = r["op"]
                op_label = {
                    "ifstart": "如果",
                    "else": "否则",
                    "ifend": "结束如果",
                    "loopstart": "循环开始",
                    "loopend": "循环结束",
                }.get(op, SCENARIO_OP_LABELS.get(s.get("op"), s.get("op") or "?"))
                tags = []
                disp_op = op_label
                vals = (i + 1, disp_op, self._scenario_param_text(s), self._scenario_step_desc(s))
                self.scenario_tree.insert("", "end", iid=str(i), values=vals, tags=tuple(tags))
            # v4.67★ 重建表格会把标签一起清掉 —— 正跑着的时候改脚本/切剧本, 高亮会凭空消失;
            #   刷新完照着记下的步号重新点亮一次。
            if getattr(self, "scenario_hl_idx", -1) >= 0 and getattr(self, "scenario_running", False):
                self._scenario_hl_paint(self.scenario_hl_idx, True)
            # v4.??★ 想法A: 重建后重画配对高亮(若当前有选中)
            self._scenario_pair_refresh()
        except Exception as e:
            self.app.log(f"[剧本] 刷新步骤失败: {e}", level="ERROR")

    def _scenario_pair_refresh(self):
        """v4.??★ 想法A: 选中某步 → 高亮其配对的 如果↔结束如果 / 循环开始↔循环结束 / 否则→如果。
        纯视觉辅助, 任何异常都不影响主流程。"""
        try:
            # 先去掉旧的 pair / block 标签
            for child in self.scenario_tree.get_children():
                old = list(self.scenario_tree.item(child, "tags"))
                if "pair" in old or "block" in old:
                    old = [t for t in old if t not in ("pair", "block")]
                    self.scenario_tree.item(child, tags=tuple(old))
            sel = self.scenario_tree.selection()
            if not sel:
                return
            idx = int(sel[0])
            cur = self._scenario_cur() or {}
            steps = cur.get("steps") or []
            if idx < 0 or idx >= len(steps):
                return
            d, po, co, il, an = SSTRUCT.scenario_step_depths(steps)
            op = SSTRUCT._norm_op_of(steps[idx])
            mate = -1
            if op in SSTRUCT.SCENARIO_OPEN:
                mate = co[idx]
            elif op in SSTRUCT.SCENARIO_CLOSE:
                mate = po[idx]
            elif op == "else":
                mate = po[idx]            # else 配对它所属的 如果(再由其 close 看出整块)
            if mate < 0:
                return
            # 整块高亮: 把选中的 if/循环 整段(开始→结束, 含内部所有步骤)都染 block 底
            a = b = mate
            if op in SSTRUCT.SCENARIO_OPEN:
                a, b = idx, co[idx]
            elif op in SSTRUCT.SCENARIO_CLOSE:
                a, b = po[idx], idx
            elif op == "else":
                oi = po[idx]
                a, b = oi, co[oi]
            if a >= 0 and b >= 0:
                for rid in range(min(a, b), max(a, b) + 1):
                    if 0 <= rid < len(steps):
                        tg = list(self.scenario_tree.item(str(rid), "tags"))
                        if "block" not in tg:
                            tg.append("block")
                        self.scenario_tree.item(str(rid), tags=tuple(tg))
            # 配对端点(开始↔结束)额外加 pair, 比整块底更亮
            mates = [idx, mate]
            if op == "else" and co[mate] >= 0 and co[mate] not in mates:
                mates.append(co[mate])   # 顺带高亮 如果 与 结束如果 两端
            for rid in mates:
                if 0 <= rid < len(steps):
                    tags = list(self.scenario_tree.item(str(rid), "tags"))
                    if "block" not in tags:
                        tags.append("block")
                    if "pair" not in tags:
                        tags.append("pair")
                    self.scenario_tree.item(str(rid), tags=tuple(tags))
        except Exception as e:
            sys.stderr.write(f"[剧本] 配对高亮失败(可忽略): {e}\n")

    def _scenario_health(self):
        """v4.??★ 想法A「剧本体检」: 校验结构合法性(未闭合/作用域错位/过深), 可选一键修复。
        引擎在缺 ifEnd 时会提示"用助手的「剧本体检」修一下括号" —— 这里正是那个入口。"""
        cur = self._scenario_cur()
        if not cur:
            return
        steps = cur.get("steps") or []
        issues = SSTRUCT.scenario_validate(steps)
        if not issues:
            self.app.log("[剧本体检] 结构良好, 无括号/嵌套问题 ✓")
            return
        errs = [it for it in issues if it["level"] == "error"]
        msg = "剧本体检发现 %d 处问题(其中 %d 处致命):\n" % (len(issues), len(errs))
        for it in issues[:20]:
            msg += "· 第%d步 [%s] %s\n" % (it["at"] + 1, it["level"], it["msg"])
        if len(issues) > 20:
            msg += "· …(其余 %d 处略)\n" % (len(issues) - 20)
        if messagebox.askyesno(
                "剧本体检",
                msg + "\n\n是否按报告修复括号？仅补齐未闭合括号或忽略无对应开头的结束括号，不改其他步骤。"):
            repaired, warns = SSTRUCT.scenario_repair(steps)
            cur["steps"] = repaired
            self._scenario_save()        # 落盘 + 推送游戏页
            self._scenario_refresh_list()
            left = SSTRUCT.scenario_validate(repaired)
            if left:
                self.app.log("[剧本体检] 已修复(%d 处调整); 仍有 %d 处提示级问题(不影响运行)" % (len(warns), len(left)))
            else:
                self.app.log("[剧本体检] 已修复, 现 0 处问题 ✓" + (("；" + "; ".join(warns)) if warns else ""))
        else:
            self.app.log("[剧本体检] 未修复, 共 %d 处问题" % len(issues), level="WARN")

    def _scenario_health_all(self):
        """批量体检: 扫描当前全部剧本, 列出有结构问题的, 可一键全部修复。"""
        broken = []
        for idx, s in enumerate(self.scenario_scripts):
            steps = (s or {}).get("steps") or []
            iss = SSTRUCT.scenario_validate(steps)
            if iss:
                broken.append((idx, (s or {}).get("name", "?"), len(iss)))
        if not broken:
            self.app.log("[剧本体检] 全部 %d 个剧本结构良好 ✓" % len(self.scenario_scripts))
            return
        msg = "批量体检: 共 %d 个剧本, 其中 %d 个有结构问题:\n" % (len(self.scenario_scripts), len(broken))
        for idx, name, n in broken[:40]:
            msg += "· #%d 「%s」 —— %d 处问题\n" % (idx + 1, name, n)
        if len(broken) > 40:
            msg += "· …(其余 %d 个略)\n" % (len(broken) - 40)
        if messagebox.askyesno("剧本体检·全部", msg + "\n\n是否一键修复全部(补/删括号, 不改有效步骤)?"):
            total = 0
            for idx, name, n in broken:
                steps = self.scenario_scripts[idx].get("steps") or []
                rep, warns = SSTRUCT.scenario_repair(steps)
                self.scenario_scripts[idx]["steps"] = rep
                total += len(warns)
            self._scenario_save()        # 落盘 + 推送游戏页
            self._scenario_refresh_list()
            self.app.log("[剧本体检] 已修复 %d 个剧本(共 %d 处调整), 已保存并推送" % (len(broken), total))
        else:
            self.app.log("[剧本体检] 未修复, 共 %d 个剧本有问题" % len(broken), level="WARN")

    def _scenario_param_text(self, s):
        """表格里「参数」那一列 —— 只列这一步的关键字面值(说明留给最后一列)。"""
        op = s.get("op")
        tgt = s.get("target")
        if isinstance(tgt, (list, tuple)):
            tgt = ", ".join(str(x) for x in tgt)
        tag = " (可忽略)" if s.get("optional") else ""

        def _names(v):
            if isinstance(v, (list, tuple)):
                return ", ".join(str(x) for x in v)
            return str(v or "")

        if op == "deploy":
            return (f"{tgt or ''} → {s.get('pos') or 'auto'}"
                    + (f"  选人:{s.get('pick')}" if s.get("pick") else "")
                    + (f"  技能ID:{s.get('skillId')}" if s.get("skillId") else "") + tag)
        if op == "pick":
            return f"选 {s.get('pick') or 'auto(第一张非同名友方)'}" + tag
        if op == "pickOne":
            _k = {"chess": "武将", "spell": "锦囊"}.get(str(s.get("kind") or "").lower(), "自动")
            return f"挑{_k}: {s.get('pick') or 'auto(候选第一个)'}" + tag
        if op == "recycle":
            return (_names(s.get("targets") or tgt) + f" ({s.get('scope') or 'both'})"
                    # v4.??★ 数量上限必须出现在这列 —— 不写出来用户不知道"遣 1 个"到底生效没有
                    + (f" 最多{_scenario_safe_int(s.get('maxCount'), 0, 0)}张"
                       if _scenario_safe_int(s.get("maxCount"), 0, 0) else "")
                    + tag)
        if op == "recycleSpell":
            _n = _scenario_safe_int(s.get("maxCount"), 0, 0)
            _ex = _names(s.get("exclude"))
            return ((_names(s.get("targets") or tgt) or "手牌里所有锦囊")
                    + (f" 最多{_n}张" if _n else "")
                    + (f" 留着:{_ex}" if _ex else "") + tag)
        if op == "shopFind":
            return (f"{tgt or ''} 刷新{s.get('refreshCount') or 0}次 保留金币{s.get('keepGold') or 0}"
                    + ("" if s.get("buy", True) else " 只看不买") + tag)
        if op == "buyCard":
            _n = int(s.get("buyCount") or 0)
            return (f"{tgt or ''} 最多{_n}张" if _n else f"{tgt or ''} 有多少买多少") + tag
        if op == "refreshShop":
            _burn = " ∞烧虎符" if s.get("untilEmpty") else f"刷{s.get('refreshCount') or 1}次"
            return (_burn + (f" 保留{s.get('keepGold')}金" if (s.get("keepGold") and not s.get("untilEmpty")) else "")
                    + (" [极限连发]" if s.get("burst") else "")
                    + (" [底层直发]" if s.get("burstDeep") else "") + tag)
        if op == "levelUp":
            return f"升到 {s.get('level') or '?'} 级"
        if op == "lockShop":
            return str(s.get("target") or "锁")
        if op == "moneyPlan":
            return (f"{tgt or '只做节奏'} 刷{s.get('refreshCount') or 3}次"
                    + (f" 目标{s.get('level')}级" if s.get("level") else "")
                    + (f" 保留{s.get('keepGold')}金" if s.get("keepGold") else "")
                    + ("" if s.get("buy", True) else " 只看不买"))
        if op == "useSpell":
            return (f"{tgt or ''} → {_scenario_on_cn(s.get('on'))}"
                    + (f":{s.get('onName')}" if s.get("onName") else "")
                    + _scenario_post_pick_cn(s) + tag)
        if op == "useAllSpells":
            return (f"上限{s.get('rounds') or 30}张/每个等{s.get('waitMs') or 700}ms"
                    + ("" if s.get("noTargetFirst", True) is not False else " 先不试直接使用")
                    + _scenario_post_pick_cn(s) + tag)
        if op == "spellPlan":
            # v4.68★ 摘要里必须把【每张的用法】原样带出来 —— 这一步的坑全在"哪张给了谁"上,
            #   只写"逐条用锦囊 4 条"等于没写(几条一眼能数, 用法才是要核对的)。
            # v4.69★ 但内置预设现在是【整季锦囊全列出来】(130+ 条): 全塞进表格里会把参数列
            #   撑成一面墙, 别的步骤全被挤下去 —— 所以超过 8 条就只列前 6 条 + 总数,
            #   要看全表点「编辑」(那个框本来就是多行文本框)。
            _plan = [p for p in _scenario_split_spell_plan(s.get("plan")) if p["enabled"]]
            _cap = 6
            # v4.??★ 行末的「挑:…」要带出来: 它是"哪张要在第几个窗口上挑第几个", 属于要核对的内容
            _head = "、".join((f"{p['name']}→{p['on']}" if p["on"] else f"{p['name']}(自动轮目标)")
                              + _scenario_plan_pick_cn(p)
                              for p in _plan[:_cap])
            if len(_plan) > _cap:
                return (f"{_head} … 共 {len(_plan)} 张(点「编辑」看全表)"
                        + _scenario_post_pick_cn(s) + tag)
            return ((_head or "?") + _scenario_post_pick_cn(s) + tag)
        if op == "spellSmart":
            # v4.80★ 同 spellPlan: 摘要里必须带出【每张的用法】; 但它那张表通常只写几张,
            #   所以【没写的怎么办】这一行必须写出来(那才是这一步和另两步的分水岭)。
            _plan = [p for p in _scenario_split_spell_plan(s.get("rules")) if p["enabled"]]
            _off = [p for p in _scenario_split_spell_plan(s.get("rules")) if not p["enabled"]]
            _head = "、".join(f"{p['name']}→{p['on']}" + _scenario_plan_pick_cn(p)
                              for p in _plan[:6])
            if len(_plan) > 6:
                _head += " … 共 %d 张" % len(_plan)
            return ((_head or "未写用法")
                    + f"; 没写的{'留着不动' if s.get('noRule') == 'skip' else '自动轮目标'}"
                    + (f"; 不用{len(_off)}张" if _off else "")
                    + _scenario_post_pick_cn(s) + tag)
        if op == "combine":
            return ("凑满3张同名同版本就合" + tag)
        if op == "combineSpecific":
            return ("指定三连: 绕过开关, 对指定卡合金" + tag)
        if op == "autoFollowUp":     # v4.173★ 自动随征
            _tg = s.get("targets") or s.get("target") or []
            if isinstance(_tg, str):
                _tg = [_tg]
            _tg = [str(x) for x in _tg if str(x).strip()]
            _pos = s.get("position")
            if _pos not in (None, "", 0):
                _who = "阵上第 %s 格" % _pos
            elif _tg:
                _who = "点名: " + ", ".join(_tg)
            else:
                _who = "自动(第一个有棋子的位置)"
            _cards = s.get("cards") or s.get("card")
            return ((_who + (f" 用「{_cards}」" if _cards else " 自动识别随征卡"))
                    + tag)
        if op == "autoFollowUpOn":
            return "开启【后台自动随征】(手牌出现随征卡自动随征给随征目标的人)" + tag
        if op == "autoFollowUpOff":
            return "关闭【后台自动随征】(停掉后台循环; 手动随征仍可)" + tag
        if op == "autoSpellOn":
            return "开启【后台自动锦囊】(手牌出现可自动用的锦囊自动用掉)" + tag
        if op == "autoSpellOff":
            return "关闭【后台自动锦囊】(停掉后台循环; 手动锦囊步骤仍可)" + tag
        if op == "autoDismissOn":
            return "开启【后台自动遣散】(手牌: 白名单不遣散/黑名单直接遣散/其余按星级价值策略)" + tag
        if op == "autoDismissOff":
            return "关闭【后台自动遣散】(停掉后台循环; 手动遣散仍可)" + tag
        if op in ("ifStart", "waitUntil"):
            _chk = str(s.get("check") or "card").lower()
            if _chk == "card":
                # v4.??★ 类别要出现在这列 —— 它常常是这条条件【唯一】的判据
                #   (「哪些卡」留空 + 卡的类别=只要锦囊 + 全无 = 手牌没有锦囊),
                #   不显示的话表格里看上去就是一条空条件。
                _kdcn = SCENARIO_CARD_KIND_CN.get(
                    str(s.get("kind") or "").strip().lower(), "")
                _has = _names(s.get("has") or tgt)
                body = (_has + (f"[{_kdcn}]" if _kdcn else "")
                        + f" ({s.get('mode') or 'all'}/{(s.get('where') or 'hand')})")
                # v4.66★ 数量形态: 「哪些卡」留空 = 数总张数(手牌≥8张), 别显示成空名单
                if _scenario_val_set(s.get("count")):
                    # 选了类别时就数这一类(手牌锦囊张数), 别再显示成"总张数"
                    _what = _has or ("总" + _kdcn if _kdcn else "总")
                    body = (_what + " 张数 "
                            + f"{s.get('cmp') or '>='} {s.get('count')}")
            elif _chk == "stage":
                body = f"阶段 = {tgt}"
            elif _chk == "var":
                # v4.68★ 走共用的 brief: 变量现在有三种问法(数值/项数/包含),
                #   参数列和说明列必须由【同一份】话术生成, 否则两列说的事对不上。
                body = _scenario_cond_brief(s)
            else:
                body = f"{SCENARIO_CHECK_CN.get(_chk, _chk)} {s.get('cmp') or '>='} {tgt}"
            if op == "waitUntil":
                body += "  " + _scenario_timeout_cn(s.get("timeoutMs"), 10000, brief=True)
            return body + tag
        if op == "waitStage":
            # v4.66★ 这里原来写 `if s.get("timeoutMs")` —— 填 0(无限等)时这一小段会整个消失,
            #   看上去跟"没填超时"一样; 现在统一走 _scenario_timeout_cn, 缺省值也照实写出来。
            return (f"等到 {s.get('stage') or '招募'}"
                    + "  " + _scenario_timeout_cn(s.get("timeoutMs"), 30000, brief=True) + tag)
        if op == "drainNext":
            return ("挑空最多%d个窗口" % (s.get("maxRounds") or 8)) + \
                   ("" if s.get("next", True) else " 不跳轮次") + tag
        if op == "loopStart":
            body = f"x{s.get('times') or 1}"
            if str(s.get("check") or "").strip():
                body += "  " + _scenario_cond_brief(s, "跳出: ")
            return body + tag
        if op in ("break", "continue"):
            return str(s.get("text") or "")
        if op == "setVar":
            return f"{s.get('name') or '?'} = {s.get('value') or ''}"
        if op == "addVar":
            return f"{s.get('name') or '?'} += {s.get('value') or ''}"
        if op == "wait":
            return f"{s.get('ms') or 300} ms"
        if op == "callScenario":
            return str(tgt or "") + tag
        if op == "abort":
            return str(tgt or "")
        if op in ("toast", "log"):
            return str(s.get("text") or "")
        if op == "skipAni":
            # v4.176★ 参数列只写"什么时候动手"; 复位哪两个标志属于实现细节, 放说明列
            _when = ("只在播动画时" if str(s.get("when") or "always").strip() == "playing"
                     else "总是")
            return _when + tag
        if op == "holdNoCombine":
            # v4.176★ 参数列只写"禁用/允许"; 刷手牌、与 Tab 同开关属于实现细节, 放说明列
            _on = str(s.get("enable", True)).strip().lower() not in ("false", "0", "")
            return ("禁用三连(囤卡)" if _on else "允许三连(正常合成)") + tag
        return ""

    def _scenario_step_desc(self, s):
        """给人看的一句话说明(含"追加型字段"的尾巴, 见 _scenario_step_tail)"""
        return self._scenario_step_desc_body(s) + self._scenario_step_tail(s)

    def _scenario_step_tail(self, s):
        """追加到「一句话说明」末尾的东西: 目前只有「失败重试」。

        v4.66★ 为什么单独拎出来: retry 是【全局追加】给每个步骤的字段(见 SCENARIO_RETRY_FIELDS),
          不归任何一类步骤自己的说明文字管。不在这里补一句, 列表里"设了重试"和"没设"的步骤
          长得一模一样 —— 用户只能点开编辑框才知道自己设过没有(而"我明明设了2次"是最容易忘的事)。
        """
        n = _scenario_safe_int(s.get("retry"), 0, 0, 9)
        if n <= 0:
            return ""
        ms = _scenario_safe_int(s.get("retryMs"), 500, 0)
        return " · 失败自动重试 %d 次(%s)" % (n, "立刻重试" if ms == 0 else "每次间隔 %d 毫秒" % ms)

    def _scenario_step_desc_body(self, s):
        """给人看的一句话说明(只有"这一步自己"的内容)"""
        op = s.get("op")
        raw_tgt = s.get("target") or ""
        tgt = raw_tgt
        # v4.151★ 存盘形态里 target 常常是【列表】(['张宝','张鲁']), 不归一的话说明里会直接
        #   打印出 "牌库找 ['张宝', '张鲁']" 这种 Python 语法 —— 看着像坏了, 还会让下面那句
        #   "在武将表里查不到" 的体检对着整串列表去查, 报一条假警告。
        #   ⚠ t(逐个名字的列表)必须取【归一之前】的 raw_tgt —— 取合并后的字符串会把五张卡
        #     当成一个名字, 「按卡分别定」的摘要就只剩一条。
        if isinstance(tgt, (list, tuple)):
            tgt = ", ".join(str(x) for x in tgt)
        t = s.get("targets") or raw_tgt or []
        if isinstance(t, str):
            t = [t]
        pos = s.get("pos") or "auto"
        where = {"hand": "手牌", "field": "阵上", "both": "手牌+阵上", "shop": "牌库",
                 "1": "牌库第1格"}.get(str(s.get("where") or s.get("scope") or ""), str(s.get("scope") or "手牌+阵上"))
        if op == "deploy":
            p = pos
            if pos.startswith("after:") or pos.startswith("next:"):
                p = f"「{pos.split(':', 1)[1]}」右侧(两张同名会被挤成夹心)"
            elif pos.startswith("middle:") or pos.startswith("between:"):
                p = f"「{pos.split(':', 1)[1]}」两张中间"
            elif pos.startswith("before:") or pos.startswith("prev:"):
                p = f"「{pos.split(':', 1)[1]}」左侧"
            elif pos in ("", "d", "default"):
                p = "上完后的倒数第二格"
            elif pos in ("a", "auto", "0"):
                p = "第一个空位"
            if s.get("pick"):        # v4.29★ 需要选人的卡: 先选人, 游戏才会真正上阵
                return (f"手牌里找 {tgt} → 上阵到{p}; 这张卡要求选目标 → 替你选「{s.get('pick')}」"
                        f"(不选人服务器会吞掉这个当先)")
            return f"手牌里找 {tgt} → 上阵到{p}; 等到当先效果结束"
        if op == "pick":
            # v4.39★ 补一句导向: 选人的【正确位置】是上阵步骤的「上阵后选人」。
            #   实机踩过: 用户在上阵步骤填了选人(当时判"不需要选人"被忽略), 又补了一个独立选人步骤
            #   —— 上阵里那个修好之后, 这个独立步骤就变成"重复选人"(发出去只会被服务器拒绝),
            #   所以必须让用户知道它现在的定位是"补点", 且重复时会自动跳过。
            return (f"替你点掉游戏弹出的『选择目标』窗口: 作用到「{s.get('pick') or '场上第一张非同名友方'}」; "
                    f"触发卡 = 上一步上阵的那张。建议把选人直接写在【上阵】步骤的「上阵后选人」里"
                    f"(服务器只认紧跟在上阵之后的选人); 本步用于补点, "
                    f"上一步已选过时会自动跳过, 不会中断剧本")
        if op == "pickOne":     # v4.32★ 挑目标: 有的技能要挑武将, 有的要挑锦囊(两套协议)
            _k = str(s.get("kind") or "").lower()
            if _k == "spell":
                return ("替你点掉『挑一个锦囊』窗口: 走挑锦囊协议 ReqChessSelectSpellID "
                        f"(选「{s.get('pick') or '候选第一个'}」)")
            if _k == "chess":
                return ("替你点掉『挑一个武将』窗口: 走挑武将协议 ReqSelectOtherChess "
                        f"(选「{s.get('pick') or '候选第一个'}」)")
            return ("替你点掉『挑一个武将 / 挑一个锦囊』窗口(类型留空=自动按服务器在等的判断): "
                    f"选「{s.get('pick') or '候选第一个'}」, 写 1/2/3 就是候选里的第几个")
        if op == "recycle":
            # v4.64★ 补上「阵营 / 排除」的说明 —— 这两项以前在界面上既看不到也改不了
            #   (schema 里没声明, 编辑一次还会被回写抹掉), 预设里写好的名单等于隐形。
            _cm = str(s.get("camp") or "").strip()
            _ex = s.get("exclude") or []
            if isinstance(_ex, str):
                _ex = [_ex]
            _ex = [str(x) for x in _ex if str(x).strip()]
            if _cm:
                # v4.64★ 从配置表现读, 这样摘要里报的"共N人"才是真实数量(手填名单会少报)
                _mem = _scenario_camp_members(_cm)
                _lab = SCENARIO_CAMP_LABELS.get(_cm, _cm)
                _base = (f"遣散所有【{_lab}】将" + (f" (共{len(_mem)}人)" if _mem else ""))
            else:
                _base = f"遣散 {', '.join(str(x) for x in t)}"
            return (_base + (f"; 但这几个一个都不碰: {', '.join(_ex)}" if _ex else "")
                    + f"; 在{where}里找")
        if op == "recycleSpell":    # v4.81★ 遣散锦囊
            # 说明里必须讲清三件事: ①只管手牌里的锦囊(阵上的管不着) ②武将不会被误卖
            #   ③"发了却还在手牌"会如实报(不会假装成功) —— 前两条决定"该不该用这一步",
            #   第三条决定"失败时该往哪儿查", 都是这一步独有的坑。
            _ex = s.get("exclude") or []
            if isinstance(_ex, str):
                _ex = [_ex]
            _ex = [str(x) for x in _ex if str(x).strip()]
            _nm = [str(x) for x in t if str(x).strip()] if not isinstance(t, str) else ([t] if t.strip() else [])
            _n = _scenario_safe_int(s.get("maxCount"), 0, 0)
            return ("把【手牌里的锦囊】卖掉(走的和遣散武将同一个接口): "
                    + ("、".join(_nm) if _nm else "手牌里的锦囊全清")
                    + (f", 最多 {_n} 张" if _n else "")
                    + (f"; 但留着: {'、'.join(_ex)}" if _ex else "")
                    + "。★ 只认手牌里带 spellID 的卡 —— 武将/随征卡没有 spellID, 不会被误卖; "
                      "已经摆到阵上的锦囊不在手牌里, 这一步管不着。"
                      "★ 发完会再看一眼那张还在不在手上: 还在就如实报「包发了但它还在手牌」"
                      "(服务器不接受卖锦囊时就是这个现象), 不会假装成功")
        if op == "snapStat":        # v4.56★ 记数值快照
            # 说明文字里必须带上【顺序】这一条: 这一步本身没有字段可填, 界面上一行空白的话
            #   用户只会看到"多了个没用的空步骤"; 而它真正的坑是把顺序放反 —— 放反之后
            #   「数值变化」恒判不成立, 现象长得像"技能没生效", 从剧本里根本看不出来。
            return ("记数值快照: 把此刻阵上每张卡的攻/血抄一份下来, 给后面的条件「数值变化」当比较基线 "
                    "—— 之后谁的值变大, 就说明技能作用到了谁。"
                    " ⚠ 必须放在【触发之前】(上阵/遣散/用锦囊都算触发): 顺序放反, 基线就成了"
                    "「已经涨过」, 「数值变化」永远判不成立, 后面那一整段等于白跑")
        if op == "useAllSpells":    # v4.41★ 用尽锦囊
            _ex = s.get("exclude") or []
            if isinstance(_ex, str):
                _ex = [_ex]
            _ex = [str(x) for x in _ex if str(x).strip()]
            return ("把手牌里每一张锦囊都想办法用出去: 不用选目标的直接使用; 需要选目标的按 "
                    "牌库第1格→…→最后一格→阵上最后一个 轮着试, 哪个目标能让锦囊离手就算成功; "
                    "全试完不行就记一笔失败、换下一张(不卡死)"
                    + (f"。⚠ 这几张【留着不用】: {', '.join(_ex)}" if _ex else ""))
        if op == "spellPlan":       # v4.68★ 用尽锦囊·逐条
            _plan = _scenario_split_spell_plan(s.get("plan"))
            _onList = [p for p in _plan if p["enabled"]]
            _offList = [p for p in _plan if not p["enabled"]]
            _body = "、".join((f"{p['name']}→{p['on']}" if p["on"] else f"{p['name']}→脚本自动轮目标")
                              for p in _onList[:6]) or "?"
            if len(_onList) > 6:                       # 同上: 整季锦囊全列时不能全铺在这
                _body += " … 共 %d 张" % len(_onList)
            _offShow = "、".join(p['name'] for p in _offList[:6])
            if len(_offList) > 6:
                _offShow += " … 共 %d 张" % len(_offList)
            return (f"按你写的顺序一张张用锦囊(中间等 {s.get('gapMs') or 150} 毫秒, "
                    f"每张最多等 {s.get('waitMs') or 1200} 毫秒生效): {_body}"
                    + (f"。⚠ 按计划【不用】: {_offShow}" if _offList else "")
                    + "。与「用尽锦囊」的区别: 那个是自动挨个试目标(每张给谁由脚本猜), "
                      "这一步是【每张都由你指定给谁】—— 手上的锦囊各有各的目标时用这个; "
                      "手牌里还没抽到的会跳过并单独列出来(不算失败)")
        if op == "spellSmart":      # v4.80★ 用尽锦囊·按用法
            _plan = _scenario_split_spell_plan(s.get("rules"))
            _onList = [p for p in _plan if p["enabled"]]
            _offList = [p for p in _plan if not p["enabled"]]
            _body = "、".join(f"{p['name']}→{p['on']}" for p in _onList[:6]) or "一张都没写"
            if len(_onList) > 6:
                _body += " … 共 %d 张" % len(_onList)
            _noRule = ("留着不动(只用上面写了用法的那几张, 其余留在手牌)"
                       if s.get("noRule") == "skip" else
                       "自动轮目标(和「用尽锦囊」一样: 直接使用 → 牌库1~6格 → 阵上1~7格, "
                       "谁能让这张离手就算谁)")
            return ("先扫一遍手牌看看到底有哪些锦囊, 再一张张用: "
                    f"上面写了用法的按写法【一发命中】(不试别的目标 —— 这里试中即消耗, "
                    f"试错就收不回来): {_body}。"
                    f"★ 手上有、但没写用法的锦囊: {_noRule}"
                    + (f"。★ 按计划不用: {'、'.join(p['name'] for p in _offList[:6])}"
                       if _offList else "")
                    + "。与另两步的区别: 「用尽锦囊」不认识任何一张(每张都把目标挨个试过去, 慢); "
                      "「逐条用锦囊」只跑你写的那几张(计划里没有的锦囊它一张不碰, 用不完); "
                      "这一步是【先看手牌, 有用法的一发命中, 没用法按你选的策略】—— "
                      "「随机应变」脚本已经内置成「同上」(沿用上一张的目标), 不用自己写")
        if op == "combine":         # v4.68★ 合成(合金)
            # 说明里必须把【普通/金分开算】写清楚 —— 用户问的就是"孙策≥3张不会合金吗? 那你怎么算呢?"。
            #   两个版本的卡同名不同 ID(孙策 普通 21003211 / 金 21003212),
            #   所以"2 普通 + 1 金"在游戏眼里根本凑不满 3 张, 不写明白会被当成脚本算错。
            return ("主动合成(合金): 攒满 3 张【同名 + 同版本】就发一次合成请求, 一次合掉 2 张换 1 张高一档的。"
                    "⚠ 张数是【普通/金分开算】的 —— 同名卡有两个版本、两个不同 ID"
                    "(孙策 普通 21003211 / 金 21003212), 所以「2 张普通 + 1 张金」合不了; "
                    "日志里会先报「哪几张能合几组」再报合完少了几张。")
        if op == "combineSpecific":      # v4.184★ 指定三连
            # 与「合成」本质不同: 不靠游戏自动检测(那会被 blockTripleCombine 拦掉),
            # 直接调原始合金入口对【指定卡】发请求 —— 所以全局三连开关是关的也能合。
            return ("指定三连(绕过全局开关): 直接对 ids/names 指定的卡发合金请求;"
                    "斗转999 这类「整体禁三连、但特定组合要合」的场景用。"
                    "⚠ 张数是【普通/金分开算】的 —— 同名卡有两个版本、两个不同 ID"
                    "(孙策 普通 21003211 / 金 21003212), 所以「2 张普通 + 1 张金」合不了; "
                    "日志里会先报「哪几张能合几组」再报合完少了几张。"
                    "要不要这一步: 游戏自己在攒满 3 张时会自动合, 但脚本的 Tab 补丁会【阻止自动合成】"
                    "(防止它在你要挑锦囊的时候把牌合掉), 所以想合就得主动发一次。"
                    f"合完等 {s.get('waitMs') or 800} 毫秒让服务器把结果同步回手牌")
        if op == "followUp":        # v4.64★ 随征
            _cm = str(s.get("camp") or "").strip()
            _pk = s.get("targets") or s.get("target") or []
            if isinstance(_pk, str):
                _pk = [_pk]
            _pk = [str(x) for x in _pk if str(x).strip()]
            _ex = s.get("exclude") or []
            if isinstance(_ex, str):
                _ex = [_ex]
            _ex = [str(x) for x in _ex if str(x).strip()]
            _who = (f"阵上的【{_cm}】将" if _cm else "阵上点名的卡")
            if _pk:
                _who += " + " + ", ".join(_pk)
            _rot = str(s.get("rotate") or "").strip().lower() not in ("0", "false", "no")
            return (f"随征: 把「{s.get('cards') or s.get('card') or '手牌里的随征卡'}」拖给 {_who}"
                    + (f" (但跳过: {', '.join(_ex)})" if _ex else "")
                    + ("; 轮着来, 尽量摊给每个人" if _rot else "; 固定给第一个")
                    + f"; 等 {s.get('waitMs') or 500} 毫秒")
        if op == "autoFollowUp":     # v4.173★ 自动随征
            _cards = s.get('cards') or s.get('card') or '手牌里的随征卡(自动识别)'
            _tg = s.get("targets") or s.get("target") or []
            if isinstance(_tg, str):
                _tg = [_tg]
            _tg = [str(x) for x in _tg if str(x).strip()]
            _pos = s.get("position")
            if _pos not in (None, "", 0):
                _who = "阵上第 %s 格" % _pos
            elif _tg:
                _who = "阵上点名的卡(" + ", ".join(_tg) + ")"
            else:
                _who = "阵上第一个有棋子的位置(自动)"
            _ex = s.get("exclude") or []
            if isinstance(_ex, str):
                _ex = [_ex]
            _ex = [str(x) for x in _ex if str(x).strip()]
            _all = str(s.get("allToOne", True)).strip().lower() not in ("0", "false", "no")
            return (f"自动随征: 把「{_cards}」随征给 {_who}"
                    + (f" (但跳过: {', '.join(_ex)})" if _ex else "")
                    + ("; 手上的随征卡一次全给" if _all else "; 只给一张")
                    + f"; 等 {s.get('waitMs') or 500} 毫秒")
        if op == "autoFollowUpOn":
            return "开启【后台自动随征】: 页面后台循环开始盯手牌, 一有随征卡就自动随征给「随征目标」指定的人(手动 Shift+3 也可)"
        if op == "autoFollowUpOff":
            return "关闭【后台自动随征】: 停掉后台循环; 手动 Shift+3 / 剧本随征步骤仍正常可用"
        if op == "autoSpellOn":
            return "开启【后台自动锦囊】: 页面后台循环开始盯手牌, 一有可自动用的锦囊就自动用掉(手动锦囊步骤也可)"
        if op == "autoSpellOff":
            return "关闭【后台自动锦囊】: 停掉后台循环; 手动锦囊步骤 / 快捷键仍正常可用"
        if op == "autoDismissOn":
            return "开启【后台自动遣散】: 页面后台循环开始盯手牌, 白名单(永久保留)永不遣散、黑名单(遣散目标)直接遣散、其余按星级/价值策略(手动遣散也可)"
        if op == "autoDismissOff":
            return "关闭【后台自动遣散】: 停掉后台循环; 手动遣散 / F7 仍正常可用"
        if op == "shopFind":
            # v4.151★ 配了「按卡分别定」就把每张卡各自的规则念出来 —— 以前那段内容只躺在
            #   存盘的文本里, 步骤列表和预览上都看不出来, 用户没法一眼确认
            #   "张宝是凑够3张不买了、张鲁是有就买"。
            _pp = _scenario_cardplan_brief(s, t)
            return (f"牌库找 {tgt}, 没有就刷新(最多{s.get('refreshCount') or 0}次"
                    + (f", 低于{s.get('keepGold')}金不刷" if s.get("keepGold") else "")
                    + (")" if s.get("buy", True) else "), 只看不买")
                    + (f"; 各要: {_pp}" if _pp else ""))
        # ---------- v4.47★ 操作类: 刷 / 买 / 升 / 锁 / 托管 ----------
        if op == "buyCard":
            _n = int(s.get("buyCount") or 0)
            return (f"看【当前货架】找 {tgt} 买下来"
                    + (f"(最多{_n}张)" if _n else "(有多少买多少)")
                    + "; 不花钱刷新 —— 货架上没有就报出来, 并附上现在卖的是哪些")
        if op == "refreshShop":
            # v4.55★ 字段名统一成 refreshCount(schema 与脚本端都是它); times 是更早剧本的写法, 兜一下 ——
            #   否则编辑器里明明填了 5 次, 说明栏却一直写"1 次"(用户会以为自己没填上)。
            if s.get("untilEmpty"):
                return ("刷新商店 · 烧虎符到归零(循环刷新直到虎符余额归零, 『刷新几次』只当安全上限)"
                        + ("; 极限连发(只发包不验证)" if s.get("burst") else "; 每次核对虎符是否真的减了")
                        + ("; 方案B底层直发(更快但可能静默失效)" if s.get("burstDeep") else ""))
            return (f"刷新商店 {s.get('refreshCount') or s.get('times') or 1} 次"
                    + (f", 低于{s.get('keepGold')}金就停" if s.get("keepGold") else "")
                    + ("; 极限连发(只发包不验证)" if s.get("burst") else "")
                    + ("; 方案B底层直发" if s.get("burstDeep") else "")
                    + "; 每次都会核对商店是不是真的换了(没换会单独记一笔)")
        if op == "levelUp":
            return (f"把商店升到 {s.get('level') or '?'} 级"
                    + "(已达标就跳过; 优先走游戏原生升级, 没有就直发升级协议; "
                      "发出去但不涨级会明确报『升级没生效』, 不算成功)")
        if op == "lockShop":
            _w = str(s.get("target") or "锁")
            _un = _w.strip().lower() in ("0", "解锁", "unlock", "off", "false", "no")
            return (f"{'解锁' if _un else '锁住'}商店(已经是这个状态就不切, 避免来回切把自己切反)")
        if op == "moneyPlan":
            _lvl = s.get("level")
            return ("按对局决策器执行: " + (f"想找 {tgt}" if tgt else "只做钱/等级节奏")
                    + (f", 目标{_lvl}级" if _lvl else ", 等级按回合自动给(≤2回合4级 / ≤5回合5级 / 之后6级)")
                    + (f", 手上留{s.get('keepGold')}金" if s.get("keepGold") else "")
                    + f"; 最多刷{s.get('refreshCount') or s.get('times') or 3}次。"
                      "读不到金币就直接说『这一步先不做』, 不会瞎刷")
        if op == "callScenario":
            return (f"调用剧本「{tgt}」: 就地把它跑完再回来继续本剧本"
                    "(最多套 5 层, 防止两个剧本互相调用绕不出来)")
        if op == "abort":
            return ("中止本剧本: 后面的步骤都不跑, 直接收尾"
                    + (f"(原因: {tgt})" if tgt else ""))
        if op == "useSpell":
            on = str(s.get("on") or "shop")
            # v4.65★ 把存储值翻成人话: 老写法(shop/hand/field/1~6)和新写法(shop-last/hand-last/
            #   field-last/阵上3格/field3)都要认, 否则步骤列表里会直接显示 "field-last第一张"。
            on_txt = _scenario_on_cn(on)
            # v4.30★ 反查到 SpellID 时标一下: 用户一眼能看出"这个名字页面认得", 不用等跑剧本
            _ids = s.get("targetIds") or []
            _tag = f" [已按ID匹配x{len(_ids)}]" if _ids else ""
            _who = (":" + str(s.get("onName"))) if s.get("onName") else ""
            return f"用锦囊 {tgt} 作用到 {on_txt}{_who}{_tag}"
        if op in ("ifStart", "waitUntil"):
            # 条件的三种形态: 卡 / 数值·变量·已运行秒数 / 阶段
            _chk = str(s.get("check") or "card").lower()
            _head = "等到 " if op == "waitUntil" else "如果 "
            _tail = (f" ({_scenario_timeout_cn(s.get('timeoutMs'), 10000)}, 等不到就算不成立)"
                     if op == "waitUntil" else " → 走下面; 否则跳到『否则/结束如果』")
            if _chk in SCENARIO_CHECK_NUMERIC:
                _nm = SCENARIO_CHECK_CN.get(_chk, _chk)
                return (f"{_head}{_nm} {s.get('cmp') or '>='} {tgt}{_tail}"
                        f"(读不到活局状态时判为不成立, 并在日志里说明)")
            if _chk == "var":
                # v4.68★ 走共用 brief, 并把"这一档到底在问什么"用一句人话补上 ——
                #   光看「变量 keep 项数 >= 3」还得想一下"项数"是啥, 一句解释省掉这次思考。
                _as = str(s.get("varAs") or "num").strip().lower()
                _extra = {"count": "(数这个列表里有几项)",
                          "has": "(看这个列表/文本里有没有这个词; 勾了「条件取反」就是「没有」)"
                          }.get(_as, "")
                return f"{_head}{_scenario_cond_brief(s)}{_extra}{_tail}"
            if _chk == "stage":
                _p = {"招募": "招募(准备)阶段", "准备": "招募(准备)阶段", "recruit": "招募(准备)阶段",
                      "战斗": "战斗阶段", "battle": "战斗阶段"}.get(str(tgt).strip(), tgt)
                return f"{_head}现在是 {_p}{_tail}(读不到活局状态时判为不成立)"
            # v4.66★ 数量形态(攒够3张/手牌≥8张) —— 与表格列共用 _scenario_cond_brief
            if _scenario_val_set(s.get("count")):
                return (f"{_head}{_scenario_cond_brief(s)}{_tail}"
                        f"(「哪些卡」留空 = 数手牌/阵上的总张数; 0 张也照数, 不等同于「没有」)")
            h = s.get("has") or tgt or []
            if isinstance(h, str):
                h = [h]
            m = "全部" if (s.get("mode") or "all") == "all" else "任意一个"
            w = SCENARIO_WHERE_CN.get(s.get("where") or "hand", "手牌")
            return f"{_head}{w} 里 有['{', '.join(str(x) for x in h)}'] ({m}){_tail}"
        if op == "else":
            return "否则分支: 条件不成立时从这里开始, 执行完跳到『结束如果』"
        if op == "ifEnd":
            return "结束如果 (配对『如果』)"
        if op == "waitStage":
            return (f"等到阶段变成【{s.get('stage') or '招募'}】才继续 —— 已经在目标阶段就立刻过; "
                    + _scenario_timeout_cn(s.get("timeoutMs"), 30000)
                    + (", 等不到就跳过这一步" if s.get("optional") else ", 等不到就中断剧本"))
        if op == "drainNext":
            _nx = s.get("next", True) is not False
            return ("挑空: 反复答掉待挑窗口(合金/招募效果都会弹), 最多 %d 个 —— "
                    % (s.get("maxRounds") or 8)
                    + ("然后直接进下一轮(等于「跳过本次循环」)"
                       if _nx else "然后继续往下走(不跳轮次)"))
        if op == "loopStart":
            return ("循环开始: 下面到『循环结束』之间的步骤重复 "
                    f"{s.get('times') or 1} 次"
                    + (f"; 每轮跑完若【{_scenario_cond_brief(s)}】成立就提前结束循环"
                       if str(s.get("check") or "").strip() else ""))
        if op == "break":
            return ("跳出循环: 立刻结束本层循环, 跳到『循环结束』后面"
                    + (f" ({s.get('text')})" if s.get("text") else ""))
        if op == "continue":
            return ("跳过本次循环: 本轮剩下的步骤不跑了, 直接开始下一轮"
                    + (f" ({s.get('text')})" if s.get("text") else ""))
        if op == "loopEnd":
            return "循环结束 (配对『循环开始』)"
        if op == "setVar":           # v4.68★ 用户问"这个怎么用" → 说明列把三件事全写出来
            return (f"设置变量: {s.get('name') or '?'} = {s.get('value') or ''}。"
                    "值能写: ① 数字(0 / 3 / -1); "
                    "② 现成的量 gold(金币)·turn(回合)·level(等级)·hp(血量)·"
                    "elapsed(剧本开跑到现在过了几秒)·now(当前时间戳); "
                    "③ 别的变量名(把它的值拷过来); "
                    "④ 一串名字(孙策,周瑜,孙尚香 → 存成列表)。"
                    "★ 在哪儿读它: 条件里选「变量」; 任何要填数字的地方也能直接写这个名字。"
                    "列表还能问「有几项」(判够不够3个) 和「里面有没有某个词」(判抽到孙策没)。"
                    "★ 只在【这一次开跑】里有效: 停止或重新开跑就从 0 开始, 不会跨局留着")
        if op == "addVar":
            return (f"变量加减: {s.get('name') or '?'} += {s.get('value') or ''} (可以是负数, "
                    "也可以写 gold/别的变量名)。"
                    "★ 变量不存在时按 0 起算; 变量如果是列表/文本(设置变量写的那串名字), "
                    "加减对它没意义, 会按 0 起算并在日志里说明")
        if op == "wait":
            return f"等 {s.get('ms') or 300} 毫秒"
        if op == "callScenario":
            return (f"调用剧本「{s.get('target') or '?'}」: 就地跑完再回来继续"
                    + (" (找不到也继续)" if s.get("optional") else " (找不到就中断)"))
        if op == "log":
            return f"写日志: {s.get('text') or ''}"
        if op == "toast":
            return f"弹提示: {s.get('text') or ''}"
        if op == "skipAni":      # v4.176★ 跳过三连合成动画
            _when = str(s.get("when") or "always").strip()
            _head = ("只在确实在播动画时才动手(没播就跳过本步)" if _when == "playing"
                     else "不管在不在播都动手(顺手复位一次)")
            return ("跳过三连合成动画(三连时游戏播的那段): " + _head
                    + "; 先调节点自己的完成回调, 叫不动才撕节点, "
                      "最后强制复位「动画中」两个标志(不复位会让后面所有步骤卡死等动画)。"
                      "★ 本步【不等待】: 跳完立刻往下走, 要等请紧跟一条「等待」。"
                      "★ 与「禁用三连」是两件事: 那个是别合, 这个是合了别等")
        if op == "holdNoCombine":      # v4.176★ 禁用三连(囤卡)
            _on = str(s.get("enable", True)).strip().lower() not in ("false", "0", "")
            return (("禁用三连合成(囤卡: 买了先不自动合, 等更好的再合)" if _on
                     else "允许三连合成(恢复正常自动合成)")
                    + "。与「跳过三连动画」是两件事: 这个是【合不合】, 那个是【合了别等动画】。"
                      "放开时会顺手刷一次手牌, 否则刚放开的那次三连要等下次商店刷新才被识别。"
                      "★ 本步不等待。想【一直】生效请用「节奏」页那个相同开关, 剧本这一步适合临时切")
        return ""

    # ---------- 剧本: 步骤编辑 ----------
    # ---------- v4.41★ 预设库: 内置 + 用户自建 ----------
    def _scenario_user_presets(self):
        """用户自己存的剧本预设(落在配置里, 关掉助手也还在)。返回 [{"name":..,"steps":[..]}, ..]"""
        v = self.app.cfg.get("scenario_presets")
        if not isinstance(v, list):
            v = []
            self.app.cfg["scenario_presets"] = v
        return [x for x in v if isinstance(x, dict) and isinstance(x.get("steps"), list)]

    def _scenario_fork_builtin(self, i):
        """把内置预设复制一份成「我的预设」。
        v4.63★ 用户原话"我要可以你写的我能修改" —— 内置预设(SCENARIO_PRESETS)是代码里的常量,
        界面上根本没有入口能碰它。直接放开"改内置"是不行的: 下次更新会被覆盖, 而且改坏了没法
        还原。所以走"复制一份"的路子 —— 复制完它就是一个普通的自建预设, 改名 / 删除 / 用当前
        剧本的步骤覆盖它, 全都跟是自己存的一样。
        """
        if not (0 <= i < len(SCENARIO_PRESETS)):
            return
        name, desc, steps = SCENARIO_PRESETS[i]
        # 依次试 "原名", "原名(2)", "原名(3)"… 直到不跟已有的撞名(撞名会静默覆盖掉别人的预设)
        lst = self.app.cfg.setdefault("scenario_presets", [])
        used = {str(x.get("name")) for x in lst if isinstance(x, dict)}
        new_name = str(name)
        k = 2
        while new_name in used:
            new_name = f"{name}({k})"
            k += 1
        item = {"name": new_name, "steps": json.loads(json.dumps(steps, ensure_ascii=False))}
        lst.append(item)
        save_config(self.app.cfg)
        self.app.log(f"[剧本] 已把内置预设「{name}」复制成我的预设「{new_name}」({len(steps)} 步)"
                 f" —— 现在可以随便改了")
        messagebox.showinfo(
            "已复制成你的预设",
            f"已复制成「{new_name}」。\n\n"
            f"它现在是你自己的预设了, 想怎么改都行:\n"
            f"  · 改名 / 用当前剧本的步骤覆盖它 → 「+ 预设 → 修改我的预设…」\n"
            f"  · 删除 → 「+ 预设 → 删除某个预设…」\n"
            f"  · 直接跑它 → 「+ 预设 → 我的预设 ▸」里点它\n\n"
            f"改步骤的常规做法: 先把它导进当前剧本, 改好, 再「把当前剧本存为预设」覆盖它。")

    def _scenario_save_as_preset(self):
        """把当前剧本原样存成预设 —— 「这个预设也可以添加」"""
        from tkinter import simpledialog
        cur = self._scenario_cur()
        if not cur:
            self.app.log("[剧本] 请先「新增」一个剧本, 再存预设", level="WARN")
            return
        steps = cur.get("steps") or []
        if not steps:
            self.app.log("[剧本] 这个剧本还是空的, 没东西可存", level="WARN")
            return
        name = simpledialog.askstring("存为预设", "预设名字(会出现在「+ 预设」菜单里):",
                                      initialvalue=str(cur.get("name") or "我的预设"), parent=self.app.root)
        if name is None:
            return
        name = name.strip() or "我的预设"
        item = {"name": name, "steps": json.loads(json.dumps(steps, ensure_ascii=False))}
        lst = self.app.cfg.setdefault("scenario_presets", [])
        for k, it in enumerate(lst):
            if isinstance(it, dict) and it.get("name") == name:
                if not messagebox.askyesno("已存在", f"已经有一个叫「{name}」的预设, 覆盖它?"):
                    return
                lst[k] = item
                break
        else:
            lst.append(item)
        save_config(self.app.cfg)
        self.app.log(f"[剧本] 已把「{cur.get('name')}」存为预设「{name}」({len(steps)} 步) —— 点「+ 预设」即可导入")
        messagebox.showinfo("已保存", f"预设「{name}」已保存。\n点「+ 预设」菜单就能看到它(带 ★ 标记)。")

    def _scenario_del_preset(self, j):
        up = self._scenario_user_presets()
        if not (0 <= j < len(up)):
            return
        nm = up[j].get("name") or "?"
        if not messagebox.askyesno("确认", f"删除自定义预设「{nm}」?"):
            return
        lst = self.app.cfg.get("scenario_presets") or []
        try:
            lst.remove(up[j])
        except ValueError:
            return
        save_config(self.app.cfg)
        self.app.log(f"[剧本] 已删除自定义预设「{nm}」")

    def _scenario_rename_preset(self, j):
        """v4.56★ 修改一个自建预设: 改名 + (可选)用当前剧本的步骤覆盖它。
        v4.63★ 措辞修正: 用户反馈"修改预设, 怎么成改名? 那个是重命名?" —— 因为这里本来就是
        【两件事分两步】: 第 1 步改名(下面这个输入框), 第 2 步问要不要用当前剧本的步骤覆盖它。
        以前标题只写"修改预设", 点进来只看到一个改名框, 自然就以为"修改"= "重命名"。
        现在标题写明"第 1/2 步", 并直接告诉用户"不想改名就原样确定"。

        它存在的理由(用户原话"把你存的预设改为可修改"): 原来是"只能新增 / 只能删除" ——
        改了一个剧本想把对应预设也更新一下, 只能删掉重存(还得重想一遍名字)。
        """
        up = self._scenario_user_presets()
        if not (0 <= j < len(up)):
            return
        it = up[j]
        old = str(it.get("name") or "未命名")
        from tkinter import simpledialog
        new = simpledialog.askstring(
            "修改预设（第 1/2 步）：改名",
            f"把「{old}」改名成 ——\n"
            f"· 不想改名就原样点「确定」(名字不动);\n"
            f"· 下一步会问要不要用【当前剧本的步骤】覆盖它。",
            initialvalue=old, parent=self.app.root)
        if new is None:
            return
        new = new.strip() or old
        if new != old:
            for k, other in enumerate(up):
                if k != j and str(other.get("name") or "") == new:
                    if not messagebox.askyesno("名字重复", f"已经有一个叫「{new}」的预设, 继续?"):
                        return
                    break
            it["name"] = new
        cur = self._scenario_cur()
        steps = (cur or {}).get("steps") or []
        if steps:
            if messagebox.askyesno(
                    "步骤也要换吗?",
                    f"用当前剧本「{(cur or {}).get('name') or '未命名'}」的 {len(steps)} 步"
                    f"覆盖「{new}」原来的步骤吗?\n\n选「否」= 只改名, 步骤保持原样。",
                    parent=self.app.root):
                it["steps"] = json.loads(json.dumps(steps, ensure_ascii=False))
                self.app.log(f"[剧本] 预设「{new}」的步骤已被当前剧本的 {len(steps)} 步覆盖")
            else:
                self.app.log(f"[剧本] 预设「{old}」已改名为「{new}」(步骤没动)")
        else:
            self.app.log(f"[剧本] 预设「{old}」已改名为「{new}」(当前剧本是空的, 没有步骤可覆盖)")
        save_config(self.app.cfg)
        messagebox.showinfo("已保存", f"预设「{new}」已更新。\n点「+ 预设」菜单即可看到。")

    def _scenario_apply_preset(self, i):
        """v4.28★ 把第 i 个预设的步骤【追加】到当前剧本末尾(不覆盖已有步骤)。
        v4.41★ i 落在内置预设范围内就是内置的, 再往后依次是用户自建预设。

        预设里的武将名只是示例, 导入后点「编辑」改成自己的即可;
        改名保存时 _scenario_fill_target_ids 会重新反查 chessID, 不用手动管 ID。
        """
        cur = self._scenario_cur()
        if not cur:
            self.app.log("[剧本] 请先「新增」一个剧本, 再导入预设", level="WARN")
            return
        n_builtin = len(SCENARIO_PRESETS)
        if 0 <= i < n_builtin:
            pname, pdesc, psteps = SCENARIO_PRESETS[i]
        else:
            up = self._scenario_user_presets()
            k = i - n_builtin
            if not (0 <= k < len(up)):
                return
            pname = up[k].get("name") or "我的预设"
            pdesc = "你自己存下来的预设"
            psteps = up[k].get("steps") or []
        steps = cur.setdefault("steps", [])
        for raw in psteps:
            s = dict(raw)
            for k2 in ("targets", "has"):                # 深拷一份, 免得改剧本改到预设常量
                if k2 in s:
                    s[k2] = list(s[k2])
            steps.append(_scenario_fill_target_ids(s))
        self._scenario_refresh_list()
        self.app.log(f"[剧本] 已导入预设「{pname}」({len(psteps)} 步, 追加到末尾): {pdesc}")

    # ---------- v4.41★ 剧本导入 / 导出 ----------
    def _scenario_export(self):
        """导出剧本到 JSON(内容就是下发页面那份结构, 文本可读, 也能原样导回来)"""
        from tkinter import filedialog
        scripts = self._scenario_collect()
        if not scripts:
            self.app.log("[剧本] 没有可导出的剧本", level="WARN")
            return
        cur = self._scenario_cur() or {}
        default = str(cur.get("name") or "剧本").replace("/", "_").replace("\\", "_") + ".json"
        path = filedialog.asksaveasfilename(title="导出剧本", defaultextension=".json",
                                            initialfile=default,
                                            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")])
        if not path:
            return
        payload = {"type": "sgs_tavern_scenario", "version": 1,
                   "exportedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "scenarios": scripts}
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception as e:
            messagebox.showerror("导出失败", str(e))
            return
        self.app.log(f"[剧本] 已导出 {len(scripts)} 个剧本 → {path}")
        messagebox.showinfo("导出成功",
                            f"已导出 {len(scripts)} 个剧本:\n{path}\n\n"
                            f"里面就是下发给页面的那份结构, 文本可读, 也支持原样导回来。\n"
                            f"想直接发给别人可以点「复制JSON」, 不用存文件。")

    def _scenario_export_payload(self):
        """导出/分享用的那份数据 —— 就是下发给页面结构的本身, 中间没做任何转换"""
        scripts = self._scenario_collect()
        return {"type": "sgs_tavern_scenario", "version": 1,
                "exportedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
                "scenarios": scripts}

    @staticmethod
    def _scenario_payload_arr(data):
        """从任意 JSON(文件/剪贴板)里取出剧本数组; 取不到返回 None。
        认三种写法: {"scenarios":[...]} / 单个剧本对象 / 剧本数组。"""
        if isinstance(data, dict):
            arr = data.get("scenarios")
            if arr is None and isinstance(data.get("steps"), list):
                return [data]          # 直接贴了单个剧本对象也认
        else:
            arr = data
        if isinstance(arr, dict):
            arr = [arr]
        if not isinstance(arr, list):
            return None
        return [x for x in arr if isinstance(x, dict) and isinstance(x.get("steps"), list)]

    def _scenario_copy_share(self):
        """v4.48★ 把剧本 JSON 放进系统剪贴板 —— 不用存文件也能发给别人(QQ/微信/论坛)"""
        scripts = self._scenario_collect()
        if not scripts:
            self.app.log("[剧本] 没有可复制的剧本", level="WARN")
            return
        txt = json.dumps(self._scenario_export_payload(), ensure_ascii=False, indent=2)
        try:
            self.app.root.clipboard_clear()
            self.app.root.clipboard_append(txt)
            self.app.root.update_idletasks()
        except Exception as e:
            messagebox.showerror("复制失败", f"写系统剪贴板失败:\n{e}")
            return
        self.app.log(f"[剧本] 已把 {len(scripts)} 个剧本的 JSON 复制到剪贴板({len(txt)} 字) —— 直接粘贴给别人即可")
        messagebox.showinfo("已复制",
                            f"已把 {len(scripts)} 个剧本复制到剪贴板({len(txt)} 字)。\n\n"
                            "对方在助手剧本页点「粘贴导入」就能拿到, 不用来回传文件。")

    def _scenario_import_clipboard(self):
        """v4.48★ 从剪贴板导入 —— 别人发来的一整段 JSON 直接贴进来就行"""
        try:
            txt = str(self.app.root.clipboard_get() or "").strip()
        except Exception as e:
            messagebox.showerror("粘贴导入", f"读不了剪贴板(里面可能不是文本):\n{e}")
            return
        if not txt:
            messagebox.showerror("粘贴导入", "剪贴板是空的")
            return
        try:
            data = json.loads(txt)
        except Exception as e:
            messagebox.showerror("粘贴导入",
                                 "剪贴板里的内容不是剧本 JSON。\n\n"
                                 "正确做法: 对方点「复制JSON」, 你在这里点「粘贴导入」。\n"
                                 f"(解析失败: {e})")
            return
        self._scenario_import_payload(data, "剪贴板")

    def _scenario_import(self):
        """从 JSON 导入剧本 —— 追加到列表末尾, 不覆盖现有剧本"""
        from tkinter import filedialog
        path = filedialog.askopenfilename(title="导入剧本",
                                          filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            messagebox.showerror("导入失败", f"读不了这个文件:\n{e}")
            return
        self._scenario_import_payload(data, str(path).replace("\\", "/").rsplit("/", 1)[-1] or "文件")

    def _scenario_import_payload(self, data, src="文件"):
        """导入的核心: 追加剧本 + 修好"重名改名"带来的调用断链 + 快捷键/ID 去重"""
        arr = self._scenario_payload_arr(data)
        if not arr:
            messagebox.showerror("导入失败",
                                 '里面没找到剧本。\n需要 {"scenarios": [...]} 或直接是一个剧本数组。')
            return
        exist = {str(x.get("name")) for x in self.scenario_scripts}
        used_hk = {str(x.get("hotkey") or "") for x in self.scenario_scripts}
        used_id = {str(x.get("id") or "") for x in self.scenario_scripts}
        rename = {}          # 旧名 → 实际用的新名(只有重名才会不一样)
        hk_dropped = 0
        added = 0
        for it in arr:
            name = str(it.get("name") or f"导入剧本{added + 1}")
            base, n = name, 2
            while name in exist:
                name = f"{base}({n})"
                n += 1
            if name != base:
                rename[base] = name       # 记住改名, 好把"调用它"的步骤一起改过来
            exist.add(name)
            hk = str(it.get("hotkey") or "")
            if hk and hk in used_hk:          # 快捷键撞车就丢掉, 否则按下去会两边都跑
                hk = ""
                hk_dropped += 1
            if hk:
                used_hk.add(hk)
            # v4.48★ ID 也去重: 页面端是按 id 找剧本的, 两份都叫 s1 会永远命中第一个
            iid = str(it.get("id") or "")
            if iid and iid in used_id:
                iid = ""
            # v4.x★ 文件没带 id(或 id 重复被清空)时【必须补一个】: 页面端 startScriptById 是按 id
            #   找剧本的, 缺 id 的剧本导进去 = 永远找不到 = 「执行一次」毫无反应(无 id 文件全变废条)。
            #   「新增」剧本一直有 id, 只有「导入」这条以前是漏洞, 这里补上。
            if not iid:
                _base = f"s{len(self.scenario_scripts) + added + 1}_{int(time.time())}"
                iid = _base
                _k = 1
                while iid in used_id:
                    iid = f"{_base}_{_k}"
                    _k += 1
            used_id.add(iid)
            steps = [_scenario_fill_target_ids(dict(s)) for s in it["steps"] if isinstance(s, dict)]
            # v4.19x★ 剧本级设置必须跟着导入: 导出的 JSON 里带着 触发方式/运行次数/速度/
            #   最长运行/开跑前后触发/启用/五六剑, 以前这里只收 name/hotkey/steps/id ——
            #   用户分享的「0=一直跑」循环刷牌剧本导回来变成只跑一次(速度/触发方式全回默认),
            #   界面上还看不出来。字段夹取口径与 _scenario_collect 一致。
            sc = {
                "name": name, "hotkey": hk, "steps": steps, "id": iid,
                "triggerMode": _scenario_pair_to_value(
                    SCENARIO_TRIGGER_MODE_PAIRS, str(it.get("triggerMode") or "press"), "press"),
                "runCount": _scenario_safe_int(it.get("runCount"), 1, 0),
                "speed": _scenario_safe_float(it.get("speed"), 1.0, 0.1, 100.0),
                "maxDuration": _scenario_safe_int(it.get("maxDuration"), 0, 0, 3600),
                "onStart": str(it.get("onStart") or ""),
                "onEnd": str(it.get("onEnd") or ""),
                "enabled": (it.get("enabled", True) is not False),
                "eqLiuJian": (it.get("eqLiuJian") is True),
            }
            if it.get("equipPick"):
                sc["equipPick"] = it["equipPick"]
            self.scenario_scripts.append(sc)
            added += 1
        if not added:
            messagebox.showwarning("导入失败", "里面没有能识别的剧本步骤")
            return
        # v4.48★ 引用修复: 导入的剧本里写的是"原来的名字", 若因为重名被改成了 名字(2),
        #   那句「调用剧本」就指到了不存在的名字 → 运行时只报一句"找不到剧本"就跳过,
        #   用户根本看不出是导入导致的。这里统一改写掉。
        fixed = 0
        if rename:
            for s in self.scenario_scripts:
                for st in (s.get("steps") or []):
                    if not isinstance(st, dict) or str(st.get("op") or "") != "callScenario":
                        continue
                    t = str(st.get("target") or "").strip()
                    if t in rename:
                        st["target"] = rename[t]
                        fixed += 1
        self.scenario_cur_idx = len(self.scenario_scripts) - 1
        # v4.19x★ 先把控件回填成【导入剧本自己的设置】再保存 —— _scenario_save 里的
        #   _scenario_apply_head 会把头部控件当前值反向写回当前剧本, 不先回填的话:
        #   屏幕上还留着上一个剧本的 触发方式/次数/速度/启用, 导入的设置立刻被这层
        #   【旧值覆盖】(实测: 导入 toggle/0次/2.5倍速 → 存下来全变默认 press/1/1.0)。
        self._scenario_fill_head(self._scenario_cur() or {})
        self._scenario_save(push=True)
        self._scenario_refresh_list()
        tip = f"已从{src}导入 {added} 个剧本并已保存推送。"
        if fixed:
            tip += f"\n\n已自动修复 {fixed} 处『调用剧本』的名字(重名改名造成的断链)。"
        if hk_dropped:
            tip += f"\n\n⚠ {hk_dropped} 个快捷键重复被清空(冲突会两边都触发), 请自己改一个。"
        # 顺手体检: 有没有指向"不存在的剧本"的调用步骤(导入的包里没带子剧本时会这样)
        _names = {str((x or {}).get("name") or "") for x in self.scenario_scripts}
        dangling = []
        for s in self.scenario_scripts:
            for i, st in enumerate(s.get("steps") or []):
                if isinstance(st, dict) and str(st.get("op") or "") == "callScenario" \
                        and str(st.get("target") or "").strip() not in _names:
                    dangling.append(f"{s.get('name')} 第{i + 1}步 → {st.get('target')}")
        if dangling:
            tip += ("\n\n⚠ 这些步骤调用的剧本不存在, 运行时会被跳过:\n· "
                    + "\n· ".join(dangling[:6])
                    + (f"\n· …共 {len(dangling)} 处" if len(dangling) > 6 else ""))
        self.app.log(f"[剧本] 已从{src}导入 {added} 个剧本(追加到末尾)"
                 + (f", 修复 {fixed} 处调用引用" if fixed else "")
                 + (f", {hk_dropped} 个快捷键冲突被清空" if hk_dropped else ""))
        messagebox.showinfo("导入成功", tip)

    # ---------- v4.41★ 步骤复制 / 剪切 / 粘贴 ----------
    def _scenario_sel_rows(self):
        """选中的行号(升序)。一个都没选时退回"光标所在那一行", 免得用户白按一下。
        返回 (rows, 步骤总数)"""
        cur = self._scenario_cur()
        steps = (cur or {}).get("steps") or []
        rows = []
        try:
            for it in self.scenario_tree.selection():
                rows.append(int(it))
        except Exception:
            rows = []
        if not rows:
            try:
                f = self.scenario_tree.focus()
                if f not in ("", None):
                    rows = [int(f)]
            except Exception:
                rows = []
        rows = sorted(set(r for r in rows if isinstance(r, int) and r >= 0))
        return [r for r in rows if r < len(steps)], len(steps)

    def _scenario_copy_steps(self, cut=False):
        cur = self._scenario_cur()
        if not cur:
            return
        rows, _n = self._scenario_sel_rows()
        if not rows:
            self.app.log("[剧本] 先选一行要复制/剪切的步骤", level="WARN")
            return
        steps = cur.get("steps") or []
        self.scenario_clipboard = [json.loads(json.dumps(steps[r], ensure_ascii=False)) for r in rows]
        names = [str(self._scenario_step_desc(steps[r]))[:26] for r in rows]
        act = "剪切" if cut else "复制"
        if cut:
            for r in sorted(rows, reverse=True):
                del steps[r]
            self._scenario_refresh_list()
        self.app.log(f"[剧本] 已{act} {len(rows)} 步: " + " | ".join(names)
                 + ("" if cut else "   (Ctrl+V 或点「粘贴步骤」放下去)"))

    def _scenario_cut_steps(self):
        self._scenario_copy_steps(cut=True)

    def _scenario_paste_steps(self):
        cur = self._scenario_cur()
        if not cur:
            return
        clip = getattr(self, "scenario_clipboard", None) or []
        if not clip:
            self.app.log("[剧本] 剪贴板是空的 —— 先「复制步骤」或「剪切步骤」", level="WARN")
            return
        rows, n = self._scenario_sel_rows()
        steps = cur.setdefault("steps", [])
        at = (max(rows) + 1) if rows else len(steps)      # 粘到选中行【后面】; 没选就追加到末尾
        at = max(0, min(at, len(steps)))
        for s in clip:
            steps.insert(at, json.loads(json.dumps(s, ensure_ascii=False)))
            at += 1
        self._scenario_refresh_list()
        self.app.log(f"[剧本] 已粘贴 {len(clip)} 步到第 {at - len(clip) + 1} 行之后")
        _ = n

    # ---- v4.??★ 克隆/替换函数库: 在剧本里插入 callScenario 调用 ----
    def _fn_registry_safe(self):
        """稳妥取注册表: 优先用 App 启动时构建好的(已挂 self.app.fn_registry),
        拿不到再临时取单例。都不行返回 None。"""
        reg = getattr(self.app, "fn_registry", None)
        if reg is not None:
            return reg
        try:
            from core.fn_registry import get_registry
            return get_registry()
        except Exception:
            return None

    def _fn_populate(self):
        """把注册表里的名字填进左侧「已注册函数」列表框。
        剧本库导入的整局剧本标前缀「剧本:」, 与可复用函数(如 偷梁换柱)区分开。"""
        try:
            self.fn_listbox.delete(0, tk.END)
        except Exception:
            return
        reg = self._fn_registry_safe()
        if not reg:
            self.fn_listbox.insert(tk.END, "(注册表不可用)")
            return
        meta = getattr(reg, "_meta", {}) or {}
        for nm in reg.names():
            is_scenario = bool((meta.get(nm) or {}).get("source"))
            self.fn_listbox.insert(tk.END, ("剧本:" + nm) if is_scenario else nm)

    def _fn_selected(self):
        sel = self.fn_listbox.curselection()
        if not sel:
            return None
        val = str(self.fn_listbox.get(sel[0]))
        if val.startswith("("):
            return None
        return val.replace("剧本:", "", 1)   # 去掉显示前缀, 取真实函数名

    def _fn_insert_call(self):
        """在当前剧本的步骤里插入 `callScenario target=<函数名>`(带界面填的参数)。
        插入到选中行【之后】; 没选中则追加到末尾。插入逻辑与 _scenario_paste_steps 一致。"""
        name = self._fn_selected()
        if not name:
            self.app.log("[函数库] 先在左侧「已注册函数」里选一个", level="WARN")
            return
        cur = self._scenario_cur()
        if not cur:
            self.app.log("[函数库] 请先「新增」一个剧本", level="WARN")
            return
        step = {"op": "callScenario", "target": name}
        # 参数: 从动态下拉里收集(中文标签 -> 引擎 token); 与默认值不同才写入
        params = {}
        for key, var, is_opt, opts, opts_zh, default in getattr(self, "fn_param_vars", []):
            val = (var.get() or "").strip()
            if not val:
                continue
            if is_opt:
                token = opts[opts_zh.index(val)] if val in opts_zh else val
                if token == default:
                    continue
                params[key] = token
            else:
                if val == str(default):
                    continue
                params[key] = val
        if params:
            step["params"] = params
        steps = cur.setdefault("steps", [])
        rows, _ = self._scenario_sel_rows()
        at = (max(rows) + 1) if rows else len(steps)      # 插到选中行后面; 没选就追加
        at = max(0, min(at, len(steps)))
        steps.insert(at, step)
        self._scenario_refresh_steps()
        self._scenario_refresh_list()
        self.app.log(f"[函数库] 已插入调用: {name}{' +参数' if 'params' in step else ''} (第 {at + 1} 步)")

    def _fn_build_param_fields(self):
        """按选中函数的「可填参数」动态生成中文下拉; 无参数则提示。"""
        frame = getattr(self, "fn_param_frame", None)
        if frame is None:
            return
        for w in frame.winfo_children():
            w.destroy()
        self.fn_param_vars = []
        name = self._fn_selected()
        reg = self._fn_registry_safe()
        meta = (getattr(reg, "_meta", {}) or {}).get(name) if (reg and name) else None
        params = (meta or {}).get("params") or []
        if not params:
            tk.Label(frame, text="（该函数无需参数）", bg=BG, fg=DIM, font=(FH, 8)).pack(anchor="w")
            return
        for p in params:
            key = p.get("key", "")
            label = p.get("label", key)
            default = p.get("default")
            row = tk.Frame(frame, bg=BG)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=label + "：", bg=BG, fg=DIM, font=(FH, 8)).pack(side="left")
            opts_zh = p.get("options_zh") or []
            if opts_zh:
                default_zh = TGT_ZH.get(default, default) if key == "target" else default
                var = tk.StringVar(value=default_zh)
                om = tk.OptionMenu(row, var, *opts_zh)
                om.config(bg=BTN, fg=FG, font=(FH, 8), highlightthickness=0)
                om.pack(side="left", fill="x", expand=True)
                self.fn_param_vars.append((key, var, True, p.get("options") or [], opts_zh, default))
            else:
                var = tk.StringVar(value=str(default if default is not None else ""))
                tk.Entry(row, textvariable=var, bg=BTN, fg=FG, font=(FH, 8),
                         insertbackground=FG).pack(side="left", fill="x", expand=True)
                self.fn_param_vars.append((key, var, False, None, None, default))

    def _zh_cond(self, c):
        t = (c or {}).get("type") or ""
        MAP = {
            "spellStock": "手牌有该锦囊",
            "handCount": "手牌数 %s %s" % ((c or {}).get("op", "<"), (c or {}).get("value", 20)),
            "enemyHighestAttack": "存在最高攻敌方",
            "enemyExists": "存在敌方单位",
            "recentEliminated": "有玩家被淘汰",
            "singleFactionBuild": "正走单势力体系",
            "hasEmptyField": "上阵区有空位",
            "highHpEnemy": "存在高血敌方",
            "weakUnitExists": "存在可牺牲弱子",
            "heavyRefresh": "计划高频刷新",
            "attackTriggerAlly": "依赖攻击触发",
            "recruitEndAlly": "招募结束将≥2",
            "lordHighHp": "主公血厚",
            "maleTavernValuable": "营帐有值钱男性将",
            "goldAlly": "金将≥2",
            "opponentValuable": "对手有高价值将",
            "boardFriendlyExists": "有可回手友方",
            "discardableUnit": "有可弃废子",
            "tavernLowUnit": "营帐有低阶废子",
            "lockPoolPlanned": "计划锁池",
            "endgameOrDense": "残局/敌密集",
            "justUsedStrongSpell": "刚用过强锦囊",
            "equipNonPreserved": "有装备槽且非保留宝具",
            "spellCnt": "该锦囊存量 %s %s" % ((c or {}).get("op", "<"), (c or {}).get("value", 7)),
        }
        return MAP.get(t, t)

    def _zh_steps(self, steps, depth):
        out = []
        pad = "    " * depth
        for st in (steps or []):
            if not isinstance(st, dict):
                continue
            op = st.get("op")
            if op == "ifStart":
                c = st.get("cond") or {}
                sp = c.get("spell") or c.get("name") or ""
                out.append("%s◆ 守卫：手牌持有「%s」才执行下面这段" % (pad, sp))
            elif op == "ifEnd":
                out.append("%s◆ 守卫结束" % pad)
            elif op == "optional":
                out.append("%s◇ 分支：若 %s" % (pad, self._zh_cond(st.get("cond"))))
                out.extend(self._zh_steps(st.get("body"), depth + 1))
                if st.get("else"):
                    out.append("%s◇ 否则：" % pad)
                    out.extend(self._zh_steps(st.get("else"), depth + 1))
            elif op == "useSpell":
                sp = st.get("spell") or ""
                tg = st.get("target") or ""
                tgzh = (TGT_ZH.get(tg, tg) if tg else "（引擎默认）")
                out.append("%s→ 施放锦囊「%s」→ 目标：%s" % (pad, sp, tgzh))
            elif op == "recycleSpell":
                out.append("%s→ 遣散锦囊「%s」" % (pad, st.get("spell") or ""))
            else:
                out.append("%s· %s：%s" % (pad, op, json.dumps(st, ensure_ascii=False)))
        return out

    def _fn_view(self):
        """查看选中函数：用中文逻辑展示(而非裸 JSON), 新手也能看懂。"""
        name = self._fn_selected()
        if not name:
            self.app.log("[函数库] 先在左侧「已注册函数」里选一个", level="WARN")
            return
        reg = self._fn_registry_safe()
        if not reg:
            self.app.log("[函数库] 注册表不可用", level="WARN")
            return
        steps = reg.get(name)
        if steps is None:
            self.app.log(f"[函数库] 找不到函数: {name}", level="WARN")
            return
        meta = (getattr(reg, "_meta", {}) or {}).get(name) or {}
        lines = ["【函数】%s" % name]
        cat = meta.get("zh_cat") or meta.get("cat") or ""
        tier = meta.get("zh_tier") or meta.get("tier") or ""
        if cat or tier:
            lines.append("类别：%s%s" % (cat, (" · " + tier + "阶") if tier else ""))
        if meta.get("zh_effect"):
            lines.append("效果：%s" % meta["zh_effect"])
        if meta.get("zh_gate"):
            lines.append("出手条件：%s" % meta["zh_gate"])
        if meta.get("validate"):
            lines.append("校验：%s" % meta["validate"])
        params = meta.get("params") or []
        if params:
            lines.append("可填参数：")
            for p in params:
                lines.append("  · %s（默认 %s）" % (p.get("label", p.get("key")), p.get("default")))
        lines.append("")
        lines.append("步骤（中文逻辑）：")
        lines.extend(self._zh_steps(steps, 0))
        win = tk.Toplevel(self.root)
        win.title("函数内容：%s（%d 步）" % (name, len(steps)))
        win.geometry("560x520")
        try:
            win.configure(bg=BG)
        except Exception:
            pass
        t = tk.Text(win, bg=BTN, fg=FG, font=(FH, 9), wrap="char")
        t.pack(fill="both", expand=True, padx=6, pady=6)
        t.insert("1.0", "\n".join(lines))
        t.config(state="disabled")
        tk.Button(win, text="关闭", bg=BTN, fg=FG, font=(FH, 9), command=win.destroy).pack(pady=(0, 6))

    def _scenario_add_step(self):
        if not self._scenario_cur():
            self.app.log("[剧本] 请先「新增」一个剧本", level="WARN")
            return
        self._scenario_step_editor(None)

    def _scenario_edit_step(self):
        rows, _n = self._scenario_sel_rows()
        if not rows:
            self.app.log("[剧本] 先在表里选一行", level="WARN")
            return
        # v4.66★ 多选时只开第一行 —— 说清楚, 别让人以为"改了会一起改"(步骤各不相同, 没法批量改字段)
        if len(rows) > 1:
            self.app.log(f"[剧本] 选中了 {len(rows)} 行, 只编辑第 {rows[0] + 1} 行"
                     f"(想批量改只能一行一行来)", level="WARN")
        self._scenario_step_editor(rows[0])

    def _scenario_select_all(self):
        cur = self._scenario_cur()
        steps = (cur or {}).get("steps") or []
        if not steps:
            return
        try:
            self.scenario_tree.selection_set([str(i) for i in range(len(steps))])
            self.scenario_tree.focus(str(len(steps) - 1))
        except Exception as e:
            self.app.log(f"[剧本] 全选失败: {e}", level="WARN")
            return
        self.app.log(f"[剧本] 已全选 {len(steps)} 步")

    def _scenario_add_elseif(self):
        """v4.??★ L1 否则如果: 在选中「如果」块【后面】插入 else + ifStart 组合,
        相当于"否则, 如果满足另一组条件就做这段"。引擎不认 elseif, 用 this 组合模拟。"""
        cur = self._scenario_cur()
        if not cur:
            self.app.log("[剧本] 请先「新增」一个剧本", level="WARN")
            return
        rows, _n = self._scenario_sel_rows()
        if not rows:
            self.app.log("[剧本] 先选中一个「如果」步骤(或它那一块)", level="WARN")
            return
        steps = cur.setdefault("steps", [])
        idx = rows[0]
        try:
            sp = SSTRUCT.scenario_block_span(steps, idx)
            end = sp[1] if sp else idx
        except Exception:
            end = idx
        ins = end + 1
        steps.insert(ins, {"op": "else"})
        steps.insert(ins + 1, {"op": "ifStart", "check": "card", "has": "", "where": "hand"})
        self._scenario_refresh_list()
        try:
            self.scenario_tree.selection_set(str(ins + 1))
            self.scenario_tree.focus(str(ins + 1))
        except Exception:
            pass
        self.app.log("[剧本] 已插入「否则如果」(else + 如果) → 第 %d 行, 选中它直接改条件" % (ins + 2))

    def _scenario_sandbox_run(self):
        """v4.??★ B 沙盒试运行: 用离线沙盒跑一遍当前剧本结构(合成状态), 看每步 trace / 断言结果。
        注意: 仅验证【控制流 + 条件结构】是否跑得通, 不含真机效果(游戏动作标 effect_pending)。"""
        cur = self._scenario_cur()
        if not cur:
            self.app.log("[剧本] 请先「新增」一个剧本", level="WARN")
            return
        steps = [json.loads(json.dumps(s, ensure_ascii=False)) for s in (cur.get("steps") or [])]
        if not steps:
            self.app.log("[剧本] 剧本是空的, 没啥可跑", level="WARN")
            return
        state = {"vars": {}, "hand": [], "field": [], "allSeen": [], "gold": 100,
                 "turn": 1, "stage": "选将", "alive": 7, "enemyHp": 100}
        try:
            import core.scenario_sandbox as SB
            res = SB.run_scenario(steps, state, {"loopCap": 2000})
        except Exception as e:
            messagebox.showerror("沙盒运行失败", str(e))
            return
        self._scenario_sandbox_show(res)

    def _scenario_sandbox_show(self, res):
        win = tk.Toplevel(self.root)
        win.title("沙盒试运行结果")
        win.geometry("760x520")
        try:
            win.configure(bg=BG)
        except Exception:
            pass
        txt = tk.Text(win, bg=BTN, fg=FG, font=(FH, 9), wrap="none")
        txt.pack(fill="both", expand=True, padx=6, pady=6)
        lines = []
        lines.append("ok=%s  errors=%d  steps=%d" % (res.get("ok"), len(res.get("errors", [])), len(res.get("trace", []))))
        for e in res.get("errors", []):
            lines.append("[ERROR] " + str(e))
        for t in res.get("trace", []):
            op = t.get("op") or t.get("label") or ""
            st = t.get("status", "")
            note = (t.get("note") or "")
            if note and len(note) > 80:
                note = note[:80] + "…"
            lines.append("%3d  %-12s %-9s %s" % (t.get("idx", 0) + 1, op, st, note))
        txt.insert("1.0", "\n".join(lines))
        txt.config(state="disabled")
        tk.Button(win, text="关闭", bg=BTN, fg=FG, font=(FH, 9), command=win.destroy).pack(pady=(0, 6))

    def _scenario_sel_block_rows(self, rows, steps):
        """v4.??★ 想法A: 把选中的行扩展成"结构安全"的行集 —— 若某行是 如果/循环 的
        开始或结束标记, 则连同整块(含体/否则/结束)一起算, 避免删/移一半留下悬空括号。"""
        expanded = set()
        for r in rows:
            if 0 <= r < len(steps):
                op = SSTRUCT._norm_op_of(steps[r])
                if op in SSTRUCT.SCENARIO_OPEN or op in SSTRUCT.SCENARIO_CLOSE:
                    lo, hi = SSTRUCT.scenario_block_span(steps, r)
                    for k in range(lo, hi + 1):
                        expanded.add(k)
                else:
                    expanded.add(r)
        return sorted(expanded)

    def _scenario_del_step(self):
        """v4.66★ 支持多选: 选了一整段就一次删掉(以前只删第一行, 得按十几次)。
        v4.??★ 想法A: 选到 如果/循环 的开始或结束标记时, 整块一起删, 不留悬空括号。"""
        cur = self._scenario_cur()
        if not cur:
            return
        steps = cur.get("steps") or []
        rows, n = self._scenario_sel_rows()
        if not rows:
            return
        rows = self._scenario_sel_block_rows(rows, steps)
        if len(rows) > 1:
            if not messagebox.askyesno(
                    "确认删除",
                    f"删除选中的 {len(rows)} 个步骤?\n\n"
                    + "\n".join(f"· 第{r + 1}行 {self._scenario_step_desc(steps[r])[:40]}"
                                for r in rows[:8])
                    + ("\n· …" if len(rows) > 8 else "")):
                return
        for r in sorted(rows, reverse=True):      # 从后往前删, 行号不会错位
            del steps[r]
        self._scenario_refresh_list()
        self.app.log(f"[剧本] 已删除 {len(rows)} 步(原第 "
                 + ", ".join(str(r + 1) for r in rows[:10]) + ("…" if len(rows) > 10 else "")
                 + f" 行), 现在共 {len(steps)} 步")
        _ = n

    def _scenario_dup_steps(self):
        """v4.66★ 就地复制选中的步骤(插到这一段后面) —— 以前只能 复制→粘贴 两下。
        v4.??★ 想法A: 选中 如果/循环 标记时, 整块一起复制, 不留悬空括号。"""
        cur = self._scenario_cur()
        if not cur:
            return
        steps = cur.setdefault("steps", [])
        rows, _n = self._scenario_sel_rows()
        if not rows:
            self.app.log("[剧本] 先选要复制的步骤", level="WARN")
            return
        rows = self._scenario_sel_block_rows(rows, steps)
        clip = [json.loads(json.dumps(steps[r], ensure_ascii=False)) for r in rows]
        at = max(rows) + 1
        for s in clip:
            steps.insert(at, s)
            at += 1
        self._scenario_refresh_list()
        self.app.log(f"[剧本] 已就地复制 {len(clip)} 步 → 插在第 {max(rows) + 2} 行起")

    def _scenario_move_step(self, d):
        """v4.66★ 上移/下移改成【整段】搬: 选中连续几行时当一块挪, 而且搬完保持选中,
        这样"往上挪三格"可以连点三下, 不用每次都重新选(以前只动第一行)。
        v4.??★ 想法A: 选到 如果/循环 标记时整块搬, 避免搬一半留下悬空括号。"""
        cur = self._scenario_cur()
        rows, n = self._scenario_sel_rows()
        if not cur or not rows:
            return
        steps = cur.get("steps") or []
        rows = self._scenario_sel_block_rows(rows, steps)
        lo, hi = rows[0], rows[-1]
        if d < 0:
            if lo <= 0:
                self.app.log("[剧本] 已经在最上面了", level="WARN")
                return
            block = steps[lo - 1:hi + 1]              # 连上面那一行一起取出来
            steps[lo - 1:hi + 1] = block[1:] + block[:1]   # 把块往上滚一格
            new_rows = [r - 1 for r in rows]
        else:
            if hi >= n - 1:
                self.app.log("[剧本] 已经在最下面了", level="WARN")
                return
            block = steps[lo:hi + 2]                  # 连下面那一行一起取出来
            steps[lo:hi + 2] = block[-1:] + block[:-1]     # 把块往下滚一格
            new_rows = [r + 1 for r in rows]
        self._scenario_refresh_list()
        try:
            self.scenario_tree.selection_set([str(r) for r in new_rows])
            self.scenario_tree.focus(str(new_rows[-1] if d > 0 else new_rows[0]))
            self.scenario_tree.see(str(new_rows[0]))
        except Exception as e:
            # 只是"把选中/光标还原"这件小事失败: 步骤顺序已经改好了, 不影响正确性。
            # v4.41★ 但不许静默 —— 报出来才知道是行号越界还是表被重建了。
            self.app.log(f"[剧本] 上移/下移后选中状态没能还原({new_rows}): {e}", level="WARN")

    def _scenario_clear_steps(self):
        cur = self._scenario_cur()
        if not cur:
            return
        if messagebox.askyesno("确认", "清空该剧本的所有步骤?"):
            cur["steps"] = []
            self._scenario_refresh_list()

    # ========================================================================
    # v4.55★ 步骤 ↔ 编辑框字段值 (schema 驱动) —— 替代旧的"通用4框 + extra 字符串"
    #
    #   旧写法把"刷新几次/保留金币/阶段/比较符"等等全塞进一个 extra 字符串里, 用户看不出
    #   自己填的东西存到哪去了, 想加个字段也只能靠手写 "key=value"。用户原话:
    #   "这个步骤有自己的格式, 现在都是这样有点乱, 也看不懂"。
    #
    #   现在两边都直接对着 SCENARIO_STEP_SCHEMA 走:
    #     _scenario_step_values(step)           → {字段: 值}   (打开编辑框时回填)
    #     _scenario_step_from_values(op, vals)  → step dict    (点确定时组装)
    #   只有当前形态真正用得到的字段才会显示/被写入, 所以存出来的 JSON 一眼能看懂。
    # ========================================================================
    def _scenario_op_schema(self, op):
        """取某个步骤类型的字段声明(没登记就返回空)。"""
        return SCENARIO_STEP_SCHEMA.get(str(op or "")) or {}

    # v4.??★ 通用尾部字段: 任何步骤类型都能挂(L4 失败跳转 / L5 步后效果断言)。
    #   onFailJump = 本步失败时跳到哪个「锚点」继续; verify = 步执行后立刻复检的条件组。
    #   放在尾部而非写进每个 schema, 一是省得给 40+ 个 op 各加一遍, 二是它对所有步骤都成立。
    def _scenario_tail_fields(self):
        return [
            {"key": "onFailJump", "label": "失败跳转到锚点", "type": "text",
             "hint": "本步失败(含效果断言未达成)时, 跳到同剧本里某个『锚点』步骤继续, 而非中断。填锚点名"},
            {"key": "verify", "label": "步后效果断言(条件组JSON)", "type": "condgroup",
             "hint": "步执行后立刻复检, 没达成当作本步失败(可配合『失败跳转』自救)。结构同条件组, 例如 "
                     '{"all":[{"check":"gold","cmp":">=","target":"30"}]}'},
        ]

    def _scenario_effective_fields(self, op, vals=None):
        """schema 字段 + 通用尾部字段 —— 生成控件/校验/组装/收集 共用这一份。"""
        base = (self._scenario_op_schema(op).get("fields") or [])
        if vals is None:
            return list(base) + self._scenario_tail_fields()
        out = [f for f in base if _scenario_show_when_ok(f.get("showWhen"), vals)]
        out += self._scenario_tail_fields()
        return out

    def _scenario_visible_fields(self, op, vals):
        """按 showWhen 算出"当前该显示/该写哪些字段" —— 生成控件、校验、组装共用这一份判定。

        条件步骤就靠它实现"选金币只显示 数值+比较, 选有卡只显示 哪些卡+满足方式+在哪找"。
        """
        return self._scenario_effective_fields(op, vals)

    def _scenario_step_values(self, s):
        """step dict → {字段名: 控件值}。names 拼成逗号串, bool 保持 bool, 其余一律 str。"""
        op = str(s.get("op") or "")
        vals = {}
        for f in self._scenario_effective_fields(op):
            k = f["key"]
            t = f.get("type", "text")
            v = s.get(k)
            if v is None:                      # 老剧本用了别的字段名 → 兜一下
                alias = SCENARIO_FIELD_ALIASES.get((op, k))
                if alias:
                    v = s.get(alias)
            if v is None:
                v = f.get("default")
            if t == "names":
                vals[k] = ", ".join(str(x) for x in v) if isinstance(v, (list, tuple)) \
                    else str(v if v is not None else "")
            elif t == "bool":
                vals[k] = bool(v) if v is not None else bool(f.get("default"))
            elif t == "cardplan" and isinstance(v, (list, tuple)):
                # v4.151★ 脚本端也认数组写法, 回填时统一成【换行分隔】的文本给表里3列拆
                vals[k] = "\n".join(str(x) for x in v)
            elif t == "condgroup":
                # L2 条件组: dict/list → 紧凑 JSON 串; 其余原样转 str(交给解析器判定)
                if isinstance(v, (dict, list)):
                    try:
                        vals[k] = json.dumps(v, ensure_ascii=False, separators=(",", ":"))
                    except Exception:
                        vals[k] = "" if v is None else str(v)
                else:
                    vals[k] = "" if v is None else str(v)
            else:
                vals[k] = "" if v is None else str(v)
        # check 不写 = 判手牌(老剧本都这样), 回填时明确显示成"手牌/场上有卡", 免得下拉空着
        if op in ("ifStart", "waitUntil") and not str(vals.get("check") or "").strip():
            vals["check"] = "card"
        return vals

    def _scenario_step_from_values(self, op, vals, optional=False):
        """{字段名: 控件值} → step dict。

        写入规则(目标: JSON 干净到能一眼看懂这一步要干什么):
          · 只写【当前形态可见】的字段 —— 隐藏的框压根没显示, 自然不会把旧值带进来;
          · 空的文本/名单/下拉不写(用不上就别占地方);
          · bool 只有与默认相反时才写 —— 所以 schema 里的 default 必须【等于脚本端"键不存在"时
            的实际行为】, 否则那个勾就成了摆设: 勾着 = 等于默认值 = 不写出去 = 脚本端看不到 =
            行为跑偏, 而且预设里显式写好的 true 一经编辑就被抹掉。optional 这类字段统一
            default=False(不勾 = 失败即中断, 与脚本端 _scRunOne 的 !s.optional 一致),
            想宽松得自己勾上 —— 见 v4.65★ 的说明;
          · int 填了就写(不猜脚本端默认值, 显式最不容易出岔子);
          · 凡是声明了 omitIfDefault 的字段(int / 下拉 / 文本都一样, v4.68★), 值等于默认值
            就不写 —— 前提是那个默认值【等于脚本端"键不存在"时的实际行为】。否则每次编辑
            都会给老剧本补上 `varAs:"num"`、`retry:0` 这类"写在里面但其实等于没写"的键。
        """
        op = str(op or "")
        vals = dict(vals or {})
        s = {"op": op}
        for f in self._scenario_visible_fields(op, vals):
            k = f["key"]
            t = f.get("type", "text")
            raw = vals.get(k)
            if t == "names":
                arr = _scenario_split_names(raw)
                if arr:
                    s[k] = arr
                continue
            if t == "bool":
                b = _scenario_to_bool(raw, bool(f.get("default")))
                if b != bool(f.get("default")):
                    s[k] = b
                continue
            if t == "int":
                txt = str(raw if raw is not None else "").strip()
                if txt == "":
                    continue
                try:
                    n = int(float(txt))
                except Exception:
                    continue
                # v4.66★ omitIfDefault: 等于默认值就不写。
                #   给"全局追加/大多数步骤都用不上"的字段用(retry/retryMs/count 这类):
                #   否则每存一次步骤都多两个键 —— 老剧本会越编辑越胖, 而且 `retry:0`、
                #   `count:0` 这种"写在里面但其实等于没写"的键最容易被误读成"这里配了东西"。
                if f.get("omitIfDefault") and n == _scenario_safe_int(f.get("default"), 0):
                    continue
                s[k] = n
                continue
            if t == "condgroup":
                raw = vals.get(k)
                if raw is None or (isinstance(raw, str) and raw.strip() == ""):
                    continue
                parsed = _scenario_parse_condgroup(raw)
                if parsed is None:
                    s[k] = raw if isinstance(raw, str) else raw   # 原样保留, 让校验/日志发现
                else:
                    s[k] = parsed
                continue
            txt = str(raw if raw is not None else "").strip()
            if txt == "":
                continue
            # v4.68★ omitIfDefault 也给下拉/文本用(原来只有 int 那一条分支认它)。
            #   现实用例: 「变量」新增的「问它的什么」默认 = 比数值; 老剧本没有这个键,
            #   一起编辑一次就被补写成 varAs:"num" —— 于是自检里的"打开再确定, 一个字不变"
            #   立刻失败, 老剧本也会被一行行养胖。凡是"默认值 = 脚本端没这个键时的行为"
            #   的字段, 都应该能声明 omitIfDefault。
            if f.get("omitIfDefault") and txt == str(f.get("default") or "").strip():
                continue
            s[k] = txt
        if op in ("ifStart", "waitUntil"):
            s.setdefault("check", "card")      # 条件必须有 check, 脚本端按它分流
        if optional:
            s["optional"] = True
        # v4.28★ 目标里的中文名 → 顺带存一份 chessID 一起下发(见 _scenario_fill_target_ids)
        return _scenario_fill_target_ids(s)

    # v4.55★ 说明: 旧的 _scenario_step_from_fields(op, target, pos, num, extra) 已经
    #   整个删掉 —— 它是"4 个通用框 + extra 隐藏字符串"那套的解析入口, 正是用户说
    #   "有点乱也看不懂"的根源。现在统一走 _scenario_step_values / _scenario_step_from_values。


    def _scenario_missing_names(self, s):
        """v4.28★ 目标里那些【反查不到 chessID】的中文名 —— 对话框上直接提示, 不用等执行失败。

        查不到 ID 就只能靠页面按名字匹配, 而页面名字映射可能为空(见 _scenario_ids_for_names),
        那种情况下这一条必然匹配不到任何卡。早提示一句, 比跑完才报"没找到"强。
        """
        # v4.69★ 一个锦囊写法里可能带【优先级】(瞒天过海·六 > 联军盛宴·五) 或名单 ——
        #   体检要一段一段查。整串拿去查必然查不到, 于是界面上会挂一条假的
        #   "「瞒天过海·六 > 联军盛宴·五」在锦囊表里查不到", 把用户往"名字写错了"上带。
        def _spell_alts(v):
            items = v if isinstance(v, (list, tuple)) else [v]
            out = []
            for _it in items:
                for _a in re.split(r"[>＞,，;；/\s]+", str(_it or "")):
                    _a = _a.strip()
                    if _a and _a not in out:
                        out.append(_a)
            return out

        def _spell_miss(v):
            return [n for n in _spell_alts(v)
                    if n and (not n.isdigit()) and not IDMAP.find_spell_ids_by_name(n)]

        if s.get("op") in ("useSpell", "useSpellIf"):
            # v4.30★ 锦囊也体检一次: 查锦囊库(cards/tavern_spells)而不是武将库。
            #   原来直接 return [] → 用户填了个库里根本没有的锦囊名也不提示,
            #   要等剧本跑到那一步才报"手牌中无锦囊"。
            return _spell_miss(s.get("target"))
        if s.get("op") in ("spellPlan", "spellSmart"):
            # v4.80★ 「按用法」的用法表和「逐条」是同一套写法, 体检也走同一套(查同一份锦囊库)。
            #   (v4.68 原文: )不查的话, 一条写错要等剧本真跑到
            #   那一步才看到"手牌中无锦囊: xxx" —— 而这一步通常是"一次把手上锦囊用完",
            #   写错的代价是那一整条计划白等(用户以为锦囊没抽到)。
            # v4.69★ 内置预设现在是整季锦囊全列出来(130+ 行), 逐条查一遍也就毫秒级;
            #   但名字里的 > 要按段查(同 useSpell)。
            _bad = []
            # v4.80★ 两个步骤存的字段名不同: 逐条存 plan, 按用法存 rules(内容格式完全一样)。
            for _p in _scenario_split_spell_plan(s.get("plan") or s.get("rules")):
                for _n in _spell_miss(_p.get("name")):
                    if _n not in _bad:
                        _bad.append(_n)
            return _bad
        # v4.47★ 有些步骤的「目标/内容」根本不是卡名, 拿它去武将表里查必然查不到,
        #   于是预览会挂一条"⚠ 在武将表里查不到"的假警告, 用户会以为自己填错了。
        _op = str(s.get("op") or "")
        if _op == "shopFind":
            names = _scenario_shopfind_target_names(s)
        elif _op in ("callScenario",):      # 这里填的是剧本名
            return []
        elif _op == "lockShop":             # 这里填的是 锁/解锁
            return []
        elif _op == "abort":                # 这里填的是原因文字
            return []
        elif _op in ("ifStart", "waitUntil"):
            # 只有"判手牌/阵上有卡"那种形态才是名字; 判数值/阶段时目标框里是数字或 招募/战斗
            if str(s.get("check") or "card").lower() != "card":
                return []
        # v4.152★ 存盘形态里 target 常常是【列表】—— 原先写成 [target] 会把它整个包一层,
        #   于是循环里拿到的是 list 本身, str() 出来就是 "['张宝', '张梁']" → 武将表里当然查不到,
        #   预览天天挂着一条指错方向的假警告(用户会以为自己把卡名写错了)。这里展开并按需再切一刀。
        if _op != "shopFind":
            _tgv = s.get("target")
            if isinstance(_tgv, (list, tuple)):
                _tgv = [str(x).strip() for x in _tgv if str(x).strip()]
            else:
                _tgv = _scenario_split_names(_tgv)
            names = s.get("targets") or s.get("has") or _tgv
            if isinstance(names, (list, tuple)):
                names = list(names)
        if s.get("pick"):                     # v4.29★ 上阵后选谁 / 选人步骤选谁, 也一起体检
            names = list(names) if isinstance(names, (list, tuple)) else [names]
            names = [x for x in names if x] + [s.get("pick")]
        if isinstance(names, str):
            names = [names]
        miss = []
        for nm in names:
            t = str(nm or "").strip()
            if not t or t.isdigit():          # 空 / 直接填 ID 的不用查
                continue
            if _scenario_pick_is_spec(t):     # v4.29★ 选人的 auto/第3格 不是名字
                continue
            if not IDMAP.find_ids_by_name(t):
                miss.append(t)
        return miss

    # ========================================================================
    # v4.55★ 步骤编辑器(重写) —— 照着 SCENARIO_STEP_SCHEMA 现场生成控件
    #
    #   旧版(见下面的 _scenario_step_dialog_legacy)是"目标/落点/次数/附加"四个通用框,
    #   外加十几行"按类型显示哪一行"的判断, 和一个把选项拼成字符串的 _sync_extra。
    #   后果就是用户说的: "这个步骤有自己的格式, 现在都是这样有点乱, 也看不懂"。
    #
    #   新版规则很短: 每类步骤声明自己有哪些字段 → 这里就只生成那几个字段;
    #   换「步骤类型」、换条件里的「判断什么」→ 整块字段区重建。所以屏幕上永远只出现
    #   这一步真正会用到的输入, 每个输入旁边紧挨着它自己的说明。
    # ========================================================================
    def _scenario_step_editor(self, idx):
        """新增/编辑一个步骤 (idx=None 为新增)。"""
        cur = self._scenario_cur()
        if not cur:
            return
        steps = cur.setdefault("steps", [])
        step = dict(steps[idx]) if (idx is not None and 0 <= idx < len(steps)) else {"op": "deploy"}
        if str(step.get("op") or "") not in SCENARIO_STEP_SCHEMA:
            step["op"] = "deploy"

        dlg = Toplevel(self.app.root)
        dlg.title("编辑步骤" if idx is not None else "新增步骤")
        dlg.configure(bg=BG)
        dlg.transient(self.app.root)
        body = tk.Frame(dlg, bg=BG)
        body.pack(fill="both", expand=True, padx=12, pady=10)

        op_labels = [lab for _, lab in SCENARIO_OPS]
        op_var = tk.StringVar(value=SCENARIO_OP_LABELS.get(step.get("op"), op_labels[0]))

        head = tk.Frame(body, bg=BG)
        head.pack(fill="x")
        tk.Label(head, text="步骤类型", bg=BG, fg=FG, font=(FH, 10, "bold"), width=8,
                 anchor="w").pack(side="left")
        # v4.110★ 字段区改成【可滚动容器】—— 「随征」这类字段多的步骤, 生成的窗口会比屏幕还高,
        #   底部「确定/取消」被顶到屏幕外 → 【看不全 + 点不到保存】(用户原话: "太长了ui,
        #   我都看不全了, 且保存不了了, 是我看不到了")。
        #   现在: 字段区上下 / 左右都能滚, 窗口高度封顶, 按钮固定在窗口底部, 永远看得到。
        _scroll_wrap = tk.Frame(body, bg=BG)
        _canvas = tk.Canvas(_scroll_wrap, bg=BG, highlightthickness=0, bd=0)
        _vsb = tk.Scrollbar(_scroll_wrap, orient="vertical", command=_canvas.yview)
        _hsb = tk.Scrollbar(_scroll_wrap, orient="horizontal", command=_canvas.xview)
        _canvas.configure(yscrollcommand=_vsb.set, xscrollcommand=_hsb.set)
        fields_box = tk.Frame(_canvas, bg=BG)
        _canvas_win = _canvas.create_window((0, 0), window=fields_box, anchor="nw")
        fields_box.bind("<Configure>",
                        lambda _e: _canvas.configure(scrollregion=_canvas.bbox("all")))
        def _on_canvas_cfg(e):
            """内容宽度: 窄了就撑满(好看), 宽了就保持自然宽(这样横向才滚得动)。

            v4.110★ 以前这里写死 width=画布宽度 —— 内容被压得和可视区一样宽, bbox("all")
              永远不超出画布 → 横向滚动条成了摆设, 于是"说明文字看不全又划不过去"。
              取 max(内容需要的宽, 画布宽) 两头都顾上。
            """
            try:
                need = fields_box.winfo_reqwidth()
            except Exception:
                need = 0
            _canvas.itemconfig(_canvas_win, width=max(int(need or 0), int(e.width or 0)))

        _canvas.bind("<Configure>", _on_canvas_cfg)
        _vsb.pack(side="right", fill="y")
        _hsb.pack(side="bottom", fill="x")
        _canvas.pack(side="left", fill="both", expand=True)

        def _wheel(e):
            # Windows/Mac 给的是 delta, Linux 给的是 Button-4/5 —— 两种都接, 换系统也不会失灵
            if getattr(e, "delta", 0):
                _canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
            elif getattr(e, "num", 0) == 4:
                _canvas.yview_scroll(-1, "units")
            elif getattr(e, "num", 0) == 5:
                _canvas.yview_scroll(1, "units")
            return "break"

        def _bind_wheel(w):
            """字段区每次重建都是新控件 → 建完得重新挂滚轮(见 _build 末尾那句)。"""
            try:
                w.bind("<MouseWheel>", _wheel)
                w.bind("<Button-4>", _wheel)
                w.bind("<Button-5>", _wheel)
            except Exception:
                _SIL.note("ui/scenario_tab.py:bind<MouseWheel>", "w.bind")
            for c in w.winfo_children():
                _bind_wheel(c)

        prev = tk.Label(body, text="", bg=BG, fg=GOLD, font=(FH, 9), justify="left", wraplength=680)

        st = {"op": None, "vars": {}, "wids": [], "busy": False}

        # ---------- 目标预设(用户自建, 存配置里) ----------
        def _user_pick_presets():
            v = self.app.cfg.get("pick_presets")
            if not isinstance(v, list):
                v = []
                self.app.cfg["pick_presets"] = v
            return v

        def _pick_values_all():
            out = list(SCENARIO_PICK_PRESETS)
            for x in _user_pick_presets():
                x = str(x).strip()
                if x and x not in out:
                    out.append(x)
            # v4.68★ 把本季可用锦囊追加进来, 写剧本时直接点选不用猜品质。
            spells = self._scenario_spell_presets()
            if spells:
                out.append("---- 本季锦囊 ----")
                out.extend(spells)
            return out

        def _call_values():
            """「调用哪个剧本」下拉 —— 排除自己(自调用必然死循环)。"""
            me = ""
            try:
                me = str((self._scenario_cur() or {}).get("name") or "").strip()
            except Exception:
                me = ""
            out = []
            for x in (self.scenario_scripts or []):
                nm = str((x or {}).get("name") or "").strip()
                if nm and nm != me and nm not in out:
                    out.append(nm)
            return out

        # ---------- 候选值 ↔ 存储值 互转 ----------
        def _pair_list(f):
            """字段候选值统一成 [(显示文本, 存储值)]: 写 ("≥ 大于等于",">=") 或直接写 "a"。"""
            out = []
            for it in (f.get("values") or []):
                if isinstance(it, (list, tuple)) and len(it) >= 2:
                    out.append((str(it[0]), str(it[1])))
                else:
                    out.append((str(it), str(it)))
            return out

        def _choices(f):
            """这个字段实际给用户看哪些候选。"""
            if f.get("type") == "scenario":
                return [(x, x) for x in _call_values()]
            if f.get("key") == "pick":
                return [(x, x) for x in _pick_values_all()]
            return _pair_list(f)

        def _is_choice(f):
            return f.get("type") in ("combo", "stage", "scenario") and not f.get("free")

        def _v2l(f, val):
            s = "" if val is None else str(val)
            for lab, v in _choices(f):
                if v == s:
                    return lab
            return s

        def _l2v(f, txt):
            s = "" if txt is None else str(txt)
            for lab, v in _choices(f):
                if lab == s:
                    return v
            return s            # free 字段手输的值原样保留

        def _seed_vars(op, src):
            """按 schema 建一整套变量; src 里的值是【存储形态】, 显示前转成下拉文本。"""
            out = {}
            base = self._scenario_step_values(src)
            for f in self._scenario_effective_fields(op):
                k = f["key"]
                t = f.get("type", "text")
                v = base.get(k)
                if v is None or v == "":
                    v = f.get("default")
                if t == "bool":
                    out[k] = tk.BooleanVar(value=_scenario_to_bool(v, True))
                elif t in ("combo", "stage", "scenario"):
                    out[k] = tk.StringVar(value=_v2l(f, v))
                else:
                    out[k] = tk.StringVar(value="" if v is None else str(v))
            return out

        def _collect():
            """当前控件 → {字段: 存储值} (只收这个 op 声明过的字段)。"""
            op = st["op"] or "deploy"
            fmap = {f["key"]: f for f in self._scenario_op_schema(op).get("fields") or []}
            vals = {}
            for k, var in st["vars"].items():
                f = fmap.get(k)
                if f is None:
                    continue
                try:
                    v = var.get()
                except Exception:
                    continue
                if _is_choice(f):
                    v = _l2v(f, v)
                vals[k] = v
            return vals

        def _refresh(*_a):
            try:
                s = self._scenario_step_from_values(st["op"] or "deploy", _collect())
                txt = "→ " + self._scenario_step_desc(s)
                miss = self._scenario_missing_names(s)
                if miss:
                    # v4.68★ spellPlan 走的也是锦囊库 —— 提示文案必须跟着走锦囊那一条,
                    #   否则用户会看到"「瞒天过海·六」在武将表里查不到"这种指错方向的话。
                    if s.get("op") in ("useSpell", "spellPlan", "spellSmart"):
                        txt += ("    ⚠ 「" + "」「".join(miss) + "」在锦囊表里查不到"
                                "(名字带不带间隔号都行, 比如 顺手牵羊三 / 顺手牵羊·三)")
                    else:
                        txt += "    ⚠ 「" + "」「".join(miss) + "」在武将表里查不到, 建议换成表里的写法"
                prev.config(text=txt)
            except Exception as e:
                prev.config(text="(预览不可用: %s)" % e)

        def _later(fn):
            """把"重建控件"推到这一轮事件处理完之后 —— 在控件的回调里直接销毁它自己会崩。"""
            def _w(*_a):
                if st["busy"]:
                    return
                st["busy"] = True

                def _do():
                    st["busy"] = False
                    try:
                        fn()
                    except Exception as e:
                        self.app.log("[剧本] 编辑器刷新失败: %s" % e, level="WARN")
                try:
                    dlg.after_idle(_do)
                except Exception:
                    st["busy"] = False
            return _w

        def _rebuild():
            _build(reset=False)
            _refresh()

        # ---------- v4.69★ 「挑选」按钮: 从名字表里点, 不用凭记忆手打 ----------
        #   用户原话: "剧本里面使用锦囊, 没有那个挑里面那个可以选择? 上阵、遣散、使用, 也加上
        #   可以挑选这个功能"。在此之前只有「上阵后选人 / 挑哪张」有下拉, 而"用哪张锦囊"
        #   "遣散谁"都得手打 —— 打错一个字(顺手牵羊三 vs 顺手牵羊·三)要等剧本真跑到那一步
        #   才报"手牌中无锦囊", 而那时人已经离开屏幕了。
        def _picker_items(lib):
            """挑选框的候选 [(显示文本, 存进框里的值)]。名字表全是【现读】的, 赛季更新自动跟着变。"""
            if lib == "hero":
                try:
                    # v4.170★ 只列【本季】武将(按 chessID 前缀过滤), 不再混 S10/S11 等旧季名字
                    return [(n, n) for n in IDMAP.current_season_hero_names()]
                except Exception:
                    return []
            if lib == "spell":
                names = _scenario_spell_names()
                base, items = [], []
                for n in names:                      # 先【任意品质】(写"瞒天过海"能匹配到任意档)
                    b = _scenario_spell_base(n)
                    if b and b not in base:
                        base.append(b)
                for b in base:
                    items.append(("（任意品质）" + b, b))
                for n in names:                      # 再【精确到品质】
                    items.append(("（精确）" + n, n))
                return items
            if lib == "pick":
                return [(x, x) for x in _pick_values_all()]
            if lib == "target":
                return [(lab + "  =  " + v, v) for lab, v in SCENARIO_ON_PAIRS]
            if lib == "pickkind":
                # 「挑哪张」按上面选的「挑哪类」决定给哪张表; 选"自动"就两类都给
                k = str(st["vars"].get("kind").get() if st["vars"].get("kind") else "").strip()
                if k == "spell":
                    return _picker_items("spell")
                if k == "chess":
                    return _picker_items("hero")
                return _picker_items("hero") + _picker_items("spell")
            return []

        def _hero_used_names(limit=30):
            """v4.152★ 「常用」= 剧本里【已经用过】的卡名(按出现次数排序)。

            用户原话: "没有常用?" —— 反复配同一套阵容时, 每次都要从两百个武将里翻出那几张
            主力。而"哪些算常用"问用户最准的答案就藏在剧本自己身上: 一个名字在步骤里出现的
            次数越多, 它就越可能是常用的那个。
            ⚠ 只收【武将表里查得到的】名字 —— "手牌:!孙策"「for rotaq」这类条件写法会被自然
              过滤掉, 不会混进候选里。
            """
            try:
                _all = set(v for _lab, v in _picker_items("hero"))
            except Exception:
                return []
            if not _all:
                return []
            cnt = {}
            for _sc in (getattr(self, "scenario_scripts", None) or []):
                for _stp in (_sc.get("steps") or []):
                    for _kk in ("target", "targets", "has", "cards", "card", "exclude"):
                        for _nm in _scenario_split_names(_stp.get(_kk)):
                            nm = str(_nm or "").strip()
                            # 条件写法里夹带目标名: "手牌:!孙策" / "field:孙策"
                            if ("：" in nm or ":" in nm) and nm not in _all:
                                nm = re.split(r"[:：]", nm)[-1].strip().lstrip("!").strip()
                            nm = re.sub(r"\s*[*xX]\s*\d+\s*$", "", nm).strip()   # 去掉 *3 后缀
                            if nm in _all:
                                cnt[nm] = cnt.get(nm, 0) + 1
            return [n for n, _c in sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))][:limit]

        def _hero_groups():
            """挑选框顶部的快捷组: 常用 / 各势力 —— 点一下列表只剩这一组(并全选)。

            用户原话: "不能根据势力吗?" —— 以前候选只有【全部武将平铺的一张长表】, 想加整个
            魏势力得一张张点。势力名单是从赛季配置表现读的(_scenario_camp_members), 换季自动跟着变。
            """
            out = []
            used = _hero_used_names()
            if used:
                out.append(("常用", used))
            try:
                for _c in SCENARIO_CAMP_IDS:
                    ms = [str(x).strip() for x in _scenario_camp_members(_c) if str(x).strip()]
                    if ms:
                        out.append(("%s势力" % _c, ms))
            except Exception:
                _SIL.note("ui/scenario_tab.py:hero_groups", "_scenario_camp_members()")
            return out

        def _open_name_picker(items, title, var=None, multi=False, on_pick=None, groups=None,
                              enable_tag_filter=False):
            """通用名字挑选框: 搜索 + 列表 + 双击选中。multi=True 时多选并【追加】到框里。

            v4.152★ groups=[(组名, [名字...])]: 顶上一排快捷组按钮 —— 点一下把列表换成那一组
              并【全选】, 再点「确定」就整组追加(以前往步骤里加"整个魏势力"要点十几次)。
            v4.170★ enable_tag_filter: 仅武将列表开的【技能类型】下拉(当先/亡语/召唤…),
              选了就只列带该类技能的武将。
            """
            if not items:
                messagebox.showinfo("提示",
                                    "没读到可挑的名字 —— 先确认助手里有赛季配置(data/*.json)")
                return
            d2 = Toplevel(dlg)
            d2.title(title)
            d2.configure(bg=BG)
            d2.transient(dlg)
            cur_items = [list(items)]                 # 切组时换它(闭包不能改外层 items)
            _all = list(items)                        # 原表备份: 按技能类型筛选时从这里重新过滤
            if groups:
                gbar = tk.Frame(d2, bg=BG)
                gbar.pack(fill="x", padx=10, pady=(8, 0))
                tk.Label(gbar, text="快捷:", bg=BG, fg=DIM, font=(FH, 8)).pack(side="left")
                for _lab, _vals in groups:
                    tk.Button(gbar, text=_lab, command=lambda v=_vals: _set_group(v),
                              bg=BTN, fg=DIM, activebackground=BTN, activeforeground=FG,
                              font=(FH, 8), relief="flat", padx=6,
                              cursor="hand2").pack(side="left", padx=(4, 0))
            # v4.170★ 武将挑选框: 按【技能类型】筛选(当先/亡语/召唤…)
            if enable_tag_filter:
                _tg = _hero_tag_choices()
                if _tg:
                    _tframe = tk.Frame(d2, bg=BG)
                    _tframe.pack(fill="x", padx=10, pady=(6, 0))
                    tk.Label(_tframe, text="技能类型:", bg=BG, fg=DIM, font=(FH, 8)).pack(side="left")
                    _tgvar = tk.StringVar(value="全部")
                    _tgcb = ttk.Combobox(_tframe, textvariable=_tgvar, state="readonly",
                                         values=["全部"] + sorted(_tg.keys()), font=(FH, 8), width=12)
                    _tgcb.pack(side="left", padx=(4, 0))

                    def _apply_tag(*_a):
                        _t = _tgvar.get()
                        if _t == "全部" or _t not in _tg:
                            cur_items[0] = list(_all)
                        else:
                            _names = set(_tg[_t])
                            cur_items[0] = [(lab, val) for lab, val in _all if val in _names]
                        _fill()

                    _tgcb.bind("<<ComboboxSelected>>", _apply_tag)
            tk.Label(d2, text=("搜索(可只打一两个字; 多选后点「确定」会追加到框里)" if multi
                               else "搜索 / 双击直接选中"), bg=BG, fg=DIM,
                     font=(FH, 8)).pack(anchor="w", padx=10, pady=(8, 0))
            sv = tk.StringVar()
            ent = tk.Entry(d2, textvariable=sv, bg=BTN, fg=FG, insertbackground=FG, font=(FH, 9))
            ent.pack(fill="x", padx=10, pady=(2, 0))
            ent.focus_set()
            wrap = tk.Frame(d2, bg=BG)
            wrap.pack(fill="both", expand=True, padx=10, pady=6)
            sb = tk.Scrollbar(wrap)
            sb.pack(side="right", fill="y")
            lb = tk.Listbox(wrap, bg=BTN, fg=FG, font=(FH, 9), height=15,
                            selectmode=("extended" if multi else "browse"),
                            activestyle="none", exportselection=False,
                            yscrollcommand=sb.set)
            sb.configure(command=lb.yview)
            lb.pack(side="left", fill="both", expand=True)
            shown = []

            def _fill(*_a):
                kw = sv.get().strip()
                lb.delete(0, "end")
                lb.selection_clear(0, "end")
                del shown[:]
                for lab, val in cur_items[0]:
                    if kw and kw not in lab and kw not in str(val):
                        continue
                    lb.insert("end", lab)
                    shown.append(val)

            def _set_group(vals):
                """点快捷组: 列表只剩这一组, 并【全选】—— 用户点完直接按「确定」就整组进去了。"""
                cur_items[0] = [(str(x), str(x)) for x in vals]
                try:
                    sv.set("")
                except Exception:
                    _SIL.note("ui/scenario_tab.py:picker", "sv.set")
                _fill()
                try:
                    lb.selection_clear(0, "end")
                    lb.selection_set(0, "end")
                except Exception:
                    _SIL.note("ui/scenario_tab.py:picker", "lb.selection_set")

            def _take(*_a):
                sel = list(lb.curselection())
                if not sel:
                    messagebox.showinfo("提示", "先在上面选一个(双击也能直接选中)", parent=d2)
                    return
                vals = [shown[i] for i in sel if i < len(shown)]
                d2.destroy()
                if on_pick:
                    on_pick(vals)                # 由调用方自己决定怎么用(如追加一行计划)
                    return
                if var is None:
                    return
                if multi:
                    cur = _scenario_split_names(var.get())
                    for v in vals:
                        if v and v not in cur:
                            cur.append(v)
                    var.set(", ".join(cur))
                else:
                    var.set(vals[0])
                _refresh()

            # trace_add 而不是老式 trace("w"): 后者在新版 Tcl(9.x) 上会报 DeprecationWarning,
            #   而这个框每次打开都要装一次(用户看不见警告, 但日志会被刷)。
            sv.trace_add("write", _fill)
            lb.bind("<Double-Button-1>", _take)

            def _on_return(_e=None):
                # v4.152★ 搜索框里敲回车原先是【清空已选 + 强行选中第一条】—— 用户按 Ctrl 多选了
                #   五张、回车一下就只剩第一张, 看着就像"覆盖了"。现在: 已经选了就直接带走,
                #   一个都没选才替他选第一条。
                if not list(lb.curselection()) and shown:
                    lb.selection_set(0)
                _take()

            ent.bind("<Return>", _on_return)
            bar = tk.Frame(d2, bg=BG)
            bar.pack(fill="x", padx=10, pady=(0, 10))
            if multi:
                tk.Button(bar, text="✓ 全选当前列表",
                          command=lambda: lb.selection_set(0, "end"),
                          bg=BTN, fg=DIM, activebackground=BTN, activeforeground=FG,
                          font=(FH, 8), relief="flat", padx=8,
                          cursor="hand2").pack(side="left")
            tk.Button(bar, text="确定", command=_take, bg=BTN, fg=FG, activebackground=BTN,
                      activeforeground=FG, font=(FH, 9), relief="flat", padx=10,
                      cursor="hand2").pack(side="right")
            tk.Button(bar, text="取消", command=d2.destroy, bg=BTN, fg=DIM, activebackground=BTN,
                      activeforeground=FG, font=(FH, 9), relief="flat", padx=10,
                      cursor="hand2").pack(side="right", padx=(0, 6))
            _fill()

        def _btn(row, text, cmd, pad=6):
            return tk.Button(row, text=text, command=cmd, bg=BTN, fg=DIM, activebackground=BTN,
                             activeforeground=FG, font=(FH, 8), relief="flat", padx=pad,
                             cursor="hand2")

        def _fill_all_spells(var):
            """「补全本季锦囊」= 把本赛季全部锦囊写进来(每行一张, 右值留空 = 自动轮目标)。

            为什么右值可以留空: 脚本 v4.69 起留空 = 按「用尽锦囊」那套顺序自己试目标
            (直接使用 → 牌库1~6格 → 阵上1~7格), 谁能让这张锦囊离手就算谁。
            """
            txt = _scenario_spell_plan_text()
            if not txt:
                messagebox.showinfo("提示", "没读到本赛季锦囊表(data/tavern_spells.json)")
                return
            var.set(txt)
            _build(reset=False)
            self.app.log("[剧本] 「每张锦囊的用法」已补全为本赛季全部锦囊")

        def _append_spell_line(var):
            """「＋加一张」= 从锦囊表里挑一张, 追加成 "名字=作用到" 一行(右值留空也能用)。"""
            def _on_pick(vals):
                nm = str(vals[0] if vals else "").strip()
                if not nm:
                    return
                txt = str(var.get() or "").strip()
                # 单行/多行都能追加: 老内容按逗号或换行结尾, 都能拼出合法的一串
                sep = "" if (not txt or txt.endswith((",", "，", "\n"))) else ", "
                var.set(txt + sep + nm + "=")
                _build(reset=False)
            _open_name_picker(_picker_items("spell"), "挑一张锦囊加到计划里", on_pick=_on_pick)

        def _build(reset=False):
            """重建字段区。reset=True 表示换了步骤类型(整组变量重开), 否则只按 showWhen 增减。"""
            op = SCENARIO_LABEL_OPS.get(op_var.get(), "deploy")
            if reset or op != st["op"] or not st["vars"]:
                # v4.151★ 换了步骤类型就把「按卡分别定」那张表的行缓存丢掉 —— 否则上一次编辑
                #   的行会串到这一步来(卡名跟着上一步的目标)。
                st["plan"], st["planHeroes"] = {}, {}
                st["planTraced"] = False
                st["vars"] = _seed_vars(op, {"op": op} if reset else step)
                st["op"] = op
            for w in st["wids"]:
                try:
                    w.destroy()
                except Exception:
                    _SIL.note("ui/scenario_tab.py:2248", "w.destroy()")
            st["wids"] = []
            vals = _collect()
            for f in self._scenario_visible_fields(op, vals):
                k = f["key"]
                var = st["vars"].get(k)
                if var is None:
                    continue
                row = tk.Frame(fields_box, bg=BG)
                row.pack(fill="x", pady=2)
                st["wids"].append(row)
                t = f.get("type", "text")
                if t == "multiline":
                    # v4.69★ 「逐条用锦囊」的计划框 —— 内置预设现在是【整季锦囊全列出来】,
                    #   单行 Entry 里没法改(要左右拖着看), 所以换成带滚动条的多行框。
                    tk.Label(row, text=f.get("label") or k, bg=BG, fg=FG, font=(FH, 9),
                             anchor="w").pack(side="top", anchor="w")
                    bx = tk.Frame(row, bg=BG)
                    bx.pack(fill="x")
                    _sb = tk.Scrollbar(bx)
                    _sb.pack(side="right", fill="y")
                    tx = tk.Text(bx, height=9, width=72, bg=BTN, fg=FG, insertbackground=FG,
                                 font=(FH, 9), wrap="char", relief="flat",
                                 yscrollcommand=_sb.set)
                    _sb.configure(command=tx.yview)
                    tx.pack(side="left", fill="x", expand=True)
                    tx.insert("1.0", str(var.get() or ""))

                    def _sync_tx(_e=None, _v=var, _t=tx):
                        # 每敲一下就同步回 StringVar —— 不这么做, 后面点「确定」收到的是【打开时的
                        #   旧文本】(编辑框只认 st["vars"]), 用户会以为"我改了啊怎么没生效"。
                        _v.set(_t.get("1.0", "end-1c"))
                        _refresh()
                    tx.bind("<KeyRelease>", _sync_tx)
                    tx.bind("<FocusOut>", _sync_tx)
                    if f.get("pickFrom") == "spellplan":
                        bf = tk.Frame(row, bg=BG)
                        bf.pack(fill="x", pady=(3, 0))
                        _btn(bf, "补全本季锦囊",
                             lambda v=var: _fill_all_spells(v)).pack(side="left")
                        _btn(bf, "＋加一张", lambda v=var: _append_spell_line(v)).pack(
                            side="left", padx=(6, 0))
                elif t == "cardplan":
                    # v4.151★ 「按卡分别定」—— 一张卡一行的小表。
                    #   以前这里是一个【单行输入框】, 得手敲 "张宝 3 off" 这种记住了才会写的串;
                    #   而脚本端按【换行】分行 —— 单行框里打不了回车, 用逗号连写就被当成同一张
                    #   卡的第三、四列, 后面的卡全都不生效(用户说的"自己写不识别")。
                    #   现在三格全用下拉: 卡名(可挑)/要几张/凑够后, 存出来仍是同一份多行文本。
                    _plan = st.setdefault("plan", {})
                    _heroes = st.setdefault("planHeroes", {})
                    # ⚠ 陷阱: var 是【外层循环】的变量(_build 里每渲染一个字段就重新赋值一次),
                    #   而 _plan_flush 是【之后才被调用】的闭包 —— 照老写法它会看到循环结束时
                    #   最后那个字段的变量(实测把表内容写进了 optional 的 BooleanVar: 既写不进去,
                    #   勾选项还被写成字符串)。这里先把它钉到一个本分支自己的名字上。
                    _pvar = var
                    tk.Label(row, text=f.get("label") or k, bg=BG, fg=FG, font=(FH, 9),
                             anchor="w").pack(side="top", anchor="w")
                    pbox = tk.Frame(row, bg=BG)
                    pbox.pack(fill="x")
                    if k not in _plan:
                        _plan[k] = _scenario_split_cardplan(var.get())
                    # v4.152★ 版本号: 「把表画出来」这件事可能被排到 after_idle 里晚一步执行,
                    #   而期间字段区可能已经重建过(_build 又跑一遍, 旧表已被替换)。旧任务再跑一次
                    #   就会往已经被弃用的 pbox 上再画一套 —— 屏幕上出现【两套同名行】, 其中一套是
                    #   死控件(改它不动数据, 改数据它也不跟着变)。实测就这样: 点金币门槛项时启用的是
                    #   死的那套, 用户看到的还是置灰。版本号不一样就让旧任务直接退出。
                    _tok = "%s#%s" % (time.time(), id(var))
                    st["planToken"] = _tok
                    # ⚠ Tk 变量陷阱: 每次重绘都新建 StringVar, 旧行的控件销毁后它的 Python 对象会被
                    #   回收、【Tk 变量名被释放】, 下一个新变量分配到同一个名字 —— 而旧名字上挂的
                    #   write trace 未必跟着消失, 于是改 A 行会触发 B 行的回调(实测点张鲁的"凑够后",
                    #   被启用的是最后一行的金币框, 屏幕上那行却纹丝不动)。把变量钉住就不会被复用。
                    _keep = st.setdefault("planVars", [])

                    rows = _plan[k]

                    def _plan_heroes(_h=_heroes, _k=k):
                        """全部武将名(现读一次就缓存 —— 每次重建都查表的话改一行卡名要等)。"""
                        if _k not in _h:
                            try:
                                _h[_k] = sorted(str(x) for x in IDMAP.all_names())
                            except Exception:
                                _SIL.note("ui/scenario_tab.py:plan", "IDMAP.all_names()")
                                _h[_k] = []
                        return _h[_k]

                    def _plan_base(nm):
                        """去掉 '张宝*3' 这类老后缀, 只留名字。"""
                        m = re.match(r"^(.*?)\s*[*xX：:]\s*(\d+)\s*$", str(nm or "").strip())
                        return (m.group(1).strip() if m and m.group(1).strip()
                                else str(nm or "").strip())

                    def _plan_target_var():
                        return st["vars"].get("target")

                    def _plan_names():
                        """卡名候选 = 「找哪张卡」里写过的(放前面) + 全部武将名。"""
                        out = []
                        tv = _plan_target_var()
                        for n in _scenario_split_names(tv.get() if tv is not None else ""):
                            n = _plan_base(n)
                            if n and n not in out:
                                out.append(n)
                        for n in _plan_heroes():
                            if n not in out:
                                out.append(n)
                        return out

                    def _plan_add_to_target(nm):
                        """表里新增/改名的卡若不在这步的『找哪张卡』里就补进去 ——
                        脚本端是拿 target 去货架上匹配的, 只写进这张表等于没生效。"""
                        tv = _plan_target_var()
                        nm = str(nm or "").strip()
                        if tv is None or not nm:
                            return
                        cur = _scenario_split_names(tv.get())
                        if nm in cur or nm in [_plan_base(x) for x in cur]:
                            return
                        cur.append(nm)
                        tv.set(", ".join(cur))
                        _plan_flush()

                    def _plan_flush():
                        _pvar.set(_scenario_cardplan_text(rows))
                        _refresh()

                    def _plan_draw_later():
                        """重绘放到下一个空闲周期 —— 在 FocusOut 回调里销毁正在编辑的那个控件
                        (卡名失焦时会补写上面的「找哪张卡」, 然后整表重建)容易踩 Tk 的事件尾巴。"""
                        def _run():
                            try:
                                _plan_draw()
                            except Exception:
                                _SIL.note("ui/scenario_tab.py:plan", "_plan_draw()")

                        try:
                            dlg.after_idle(_run)
                        except Exception:
                            _SIL.note("ui/scenario_tab.py:plan", "dlg.after_idle")

                    def _plan_fill_from_target():
                        """把『找哪张卡』里有、但表里还没排到的卡补成行(默认不限 = 有就买)。"""
                        tv = _plan_target_var()
                        names = _scenario_split_names(tv.get() if tv is not None else "")
                        n0 = len(rows)
                        for raw in names:
                            nm = str(raw).strip()
                            num = 0
                            m = re.match(r"^(.*?)\s*[*xX：:]\s*(\d+)\s*$", nm)
                            if m and m.group(1).strip():
                                nm, num = m.group(1).strip(), _scenario_safe_int(m.group(2), 0)
                            if nm and all(str(r.get("name")) != nm for r in rows):
                                rows.append({"name": nm, "num": num, "after": ""})
                        return len(rows) != n0

                    def _plan_add(names=None):
                        for nm in (names or [""]):
                            nm = str(nm or "").strip()
                            if nm and all(str(r.get("name")) != nm for r in rows):
                                rows.append({"name": nm, "num": 0, "after": ""})
                                _plan_add_to_target(nm)
                        _plan_flush()
                        _plan_draw()

                    def _plan_draw():
                        if st.get("planToken") != _tok:
                            return        # 这张表已经被后一次重建取代了 —— 别再往旧框上画
                        for wd in list(pbox.winfo_children()):
                            try:
                                wd.destroy()
                            except Exception:
                                _SIL.note("ui/scenario_tab.py:plan", "wd.destroy()")
                        hd = tk.Frame(pbox, bg=BG)
                        hd.pack(fill="x")
                        for _lab, _w in (("卡名", 13), ("要几张(可直接敲数)", 8),
                                         ("凑够之后还买吗", 20), ("金币门槛", 6)):
                            tk.Label(hd, text=_lab, bg=BG, fg=DIM, font=(FH, 8),
                                     width=_w, anchor="w").pack(side="left", padx=(0, 4))
                        names = _plan_names()
                        for _it in rows:
                            rr = tk.Frame(pbox, bg=BG)
                            rr.pack(fill="x", pady=1)
                            nv = tk.StringVar(value=str(_it.get("name") or ""))
                            _keep.append(nv)
                            cb = ttk.Combobox(rr, textvariable=nv, values=names,
                                              font=(FH, 9), width=12)
                            cb.pack(side="left")

                            # v4.152★ 「要几张」改成【可手敲】的下拉(原来是 readonly, 选项只到 9
                            #   —— 用户原话"多余9咋办?")。想要 12、15 张直接在里面敲数字就行。
                            _num0 = _scenario_safe_int(_it.get("num"), 0)
                            _nlab = str(_num0) if _num0 > 0 else "不限"
                            mv = tk.StringVar(value=_nlab)
                            _keep.append(mv)
                            cb2 = ttk.Combobox(rr, textvariable=mv,
                                               values=SCENARIO_CARDPLAN_NUM_LABELS,
                                               font=(FH, 9), width=5)
                            cb2.pack(side="left", padx=(4, 0))

                            # v4.152★ 「凑够后」的金币门槛不再让用户往下拉里敲裸数字(以前得自己在
                            #   那一行手打 20, 打错格式整列失效), 改成: 选最后一项 → 旁边出现输入框。
                            _aft = _scenario_cardplan_after_norm(_it.get("after"))
                            if _num0 <= 0:
                                # 不限张数就没有"凑够", 第三列既没意义也会让脚本端读错位(它按列读), 归整掉
                                _aft = ""
                                _it["after"] = ""
                            # goldPending: 用户【选了】"金币≥"这一项但还没把数字填进来 —— 那时 after
                            #   先兜成 gold(语义最接近), 但界面必须停在第四项并留着可输入的框,
                            #   否则一重绘就跳回"看金币", 用户会以为自己刚才白选了。
                            _is_gold = _aft not in ("", "off", "gold") or bool(_it.get("goldPending"))
                            av = tk.StringVar(value=(SCENARIO_CARDPLAN_AFTER_GOLD if _is_gold
                                                     else _scenario_cardplan_after_label(_aft)))
                            _keep.append(av)
                            # ⚠ 不能设 readonly: ttk::combobox 在 readonly 状态下 set() 与部分
                            #   下拉选中【不会写回 textvariable】, 选了等于没选。行为-normal 由
                            #   _sync_after 兜着(认不出来的写法按"沿用"算), 不会写出脏值。
                            cb3 = ttk.Combobox(rr, textvariable=av,
                                               values=SCENARIO_CARDPLAN_AFTER_LABELS,
                                               font=(FH, 9), width=19)
                            cb3.pack(side="left", padx=(4, 0))
                            gv = tk.StringVar(value=_aft if _is_gold else "")
                            _keep.append(gv)
                            # name="gold": Windows 下 ttk::combobox 内部也有一个 Entry 子窗口,
                            #   按类型找的话会先摸到那个(改的是卡名), 给自己的框留个标记好认。
                            ge = tk.Entry(rr, textvariable=gv, bg=BTN, fg=FG, insertbackground=FG,
                                          font=(FH, 9), width=5, name="gold")
                            ge.pack(side="left", padx=(2, 0))
                            if _num0 <= 0:
                                cb3.configure(state="disabled")     # 与下面置灰规则保持一致
                            if not _is_gold:
                                ge.configure(state="disabled", disabledbackground=BG)

                            # ⚠⚠ 又一个闭包陷阱(与上面 var 那个同源, 而且更隐蔽): 这些函数里用到的
                            #   av/gv/cb3/ge 全是【for 循环里的变量】, 每一行迭代都会被重新赋值。
                            #   不钉进默认参数的话, 回调真正跑起来时它们指向的是【最后一行】——
                            #   现象: 点第 3 行的"凑够后", 被启用的是最后一行的金币框(实测如此)。
                            #   所以下面每个函数都把本行那几个控件以默认参数带进去。
                            def _plan_row_gray(_on, _av=av, _gv=gv, _cb3=cb3, _ge=ge):
                                """不限张数 → 第三列没有意义(脚本端按列读会读歪), 整列置灰。"""
                                try:
                                    if _on:
                                        _av.set("")
                                        _gv.set("")
                                        _cb3.configure(state="disabled")
                                        _ge.configure(state="disabled", disabledbackground=BG)
                                    else:
                                        _cb3.configure(state="normal")
                                        if _av.get() == SCENARIO_CARDPLAN_AFTER_GOLD:
                                            _ge.configure(state="normal", disabledbackground=BTN)
                                except Exception:
                                    _SIL.note("ui/scenario_tab.py:plan", "gray")

                            def _sync_name(_a=None, _b=None, _c=None, _it2=_it, _v=nv):
                                _it2["name"] = str(_v.get() or "").strip()

                            def _sync_num(_a=None, _b=None, _c=None, _it2=_it, _v=mv):
                                raw = str(_v.get() or "").strip()
                                n = _scenario_safe_int(raw, 0) if raw.isdigit() else 0
                                _it2["num"] = n if n > 0 else 0
                                if _it2["num"] <= 0:
                                    _it2["after"] = ""
                                    _plan_row_gray(True)
                                else:
                                    _plan_row_gray(False)
                                _plan_flush()

                            def _sync_after(_a=None, _b=None, _c=None, _it2=_it, _v=av,
                                            _gv=gv, _ge=ge):
                                lab = str(_v.get() or "").strip()
                                if lab == SCENARIO_CARDPLAN_AFTER_GOLD:
                                    # ⚠ 还没填数时【绝对不能兜成 0】: 0 在脚本端等于 off(凑够就绝不再买),
                                    #   跟用户"只是还没填数字"的意图正好相反。没数就按"看金币"算 ——
                                    #   它与"沿用"最接近, 也不会把这张卡直接判死。
                                    _n = _scenario_safe_int(_gv.get(), -1)
                                    _it2["after"] = str(_n) if _n > 0 else "gold"
                                    _it2["goldPending"] = True
                                    try:
                                        _ge.configure(state="normal")
                                        _ge.configure(disabledbackground=BTN)
                                        _ge.focus_set()
                                    except Exception:
                                        _SIL.note("ui/scenario_tab.py:plan", "ge.focus_set")
                                else:
                                    _it2["after"] = _scenario_cardplan_after_norm(lab)
                                    _it2["goldPending"] = False
                                    try:
                                        _gv.set("")
                                        _ge.configure(state="disabled", disabledbackground=BG)
                                    except Exception:
                                        _SIL.note("ui/scenario_tab.py:plan", "ge.configure")
                                _plan_flush()

                            def _sync_gold(_a=None, _b=None, _c=None, _it2=_it, _v=gv, _av=av):
                                if _av.get() != SCENARIO_CARDPLAN_AFTER_GOLD:
                                    return
                                _it2["after"] = str(_scenario_safe_int(_v.get(), 0))
                                _plan_flush()

                            def _lose_focus(_e=None, _it2=_it):
                                _sync_name()
                                _plan_add_to_target(_it2.get("name"))
                                _plan_flush()
                                _plan_draw_later()

                            # ⚠ 别只靠 trace_add: 某些 Tk 版本里下拉选中/set() 不会触发
                            #   textvariable 的 write trace(Win + Tk8.6 实测就这样), 现象是
                            #   "选了但值没进去、说明也不跟着变"。所以每个下拉再挂一份事件。
                            for _w, _fn in ((cb, _lose_focus), (cb2, _sync_num), (cb3, _sync_after)):
                                _w.bind("<<ComboboxSelected>>", lambda _e, _fn2=_fn: _fn2())
                            # 「要几张」是【可手敲】的(要 12、15 张时下拉里没有), 所以除了下拉选中
                            #   事件还靠 trace 一笔一笔跟; 敲成非数字就按"不限"算, 不留脏值。
                            nv.trace_add("write", _sync_name)
                            mv.trace_add("write", _sync_num)
                            av.trace_add("write", _sync_after)
                            gv.trace_add("write", _sync_gold)
                            cb2.bind("<Return>", lambda _e: _sync_num())
                            ge.bind("<FocusOut>", lambda _e: _sync_gold())
                            # 只有【卡名】失焦才重绘: 那时可能要把这张卡补进上面的『找哪张卡』。
                            #   「凑够后」失焦不重绘 —— 每 Tab 一下就整表重建会丢焦点/闪屏。
                            cb.bind("<FocusOut>", _lose_focus)

                            def _drop(_it2=_it):
                                try:
                                    rows.remove(_it2)
                                except ValueError:
                                    pass
                                _plan_flush()
                                _plan_draw()

                            tk.Button(rr, text="✕", command=_drop, bg=BTN, fg=DIM,
                                      activebackground=BTN, activeforeground=RED,
                                      font=(FH, 8), relief="flat", padx=4,
                                      cursor="hand2").pack(side="left", padx=(4, 0))
                        def _do_fill():
                            if _plan_fill_from_target():
                                _plan_flush()
                            _plan_draw()

                        # 「常用 / 各势力」两组在上面 fields 区渲染时已经算过(算机器人), 没有就现算
                        if "heroGroups" not in st:
                            st["heroGroups"] = _hero_groups()
                        _grp2 = st["heroGroups"]
                        _picked = _picker_items("hero")     # ⚠ 别在 lambda 里调用: 那时字段区已重建完
                        bar = tk.Frame(pbox, bg=BG)
                        bar.pack(fill="x", pady=(3, 0))
                        _btn(bar, "＋ 挑一张加到表里",
                             lambda it=_picked, g=_grp2: _open_name_picker(
                                 it, "挑要分别定规则的卡", on_pick=_plan_add, multi=True,
                                 groups=g)).pack(side="left")
                        _btn(bar, "把『找哪张卡』里没排到的补成行", _do_fill).pack(side="left", padx=(6, 0))

                    # 打开就先把这张表填满: 老剧本里 cardPlan 是空的, 但「找哪张卡」里写了卡 ——
                    #   不自动补的话用户看到一张空表, 还得手动一张张加。补完之后顺手把填表的
                    #   结果写回去 —— 顺便把用户以前手敲的脏写法(逗号连写 / *3 后缀)规范成标准文本。
                    if _plan_fill_from_target() or \
                            _scenario_cardplan_text(rows) != str(_pvar.get() or "").strip():
                        _plan_flush()

                    # v4.152★ 【反向联动】在「找哪张卡」里挑好的卡自动排到这张表里。
                    #   没有这一步的话用户得把同一批名字写两遍(用户原话"都要手写?")—— 挑完回头一看,
                    #   表里还是空的, 而【不写进表也一样会买】(走统一的张数), 最容易当成没配上。
                    #   ⚠ 只往【卡不在表里】的方向加: 表→target 那条(__plan_add_to_target)反过来
                    #     又会写 target, 两边都只在缺东西时动手, 所以不会互相触发到死循环。
                    _tv = _plan_target_var()
                    # 只挂一次: _build 会反复重建字段区, 每重建一次就多绑一条的话,
                    # 用户改一行会让同一件事做十几遍(看着莫名其妙地卡)。
                    if _tv is not None and not st.get("planTraced"):
                        st["planTraced"] = True

                        def _on_target_changed(_a=None, _b=None, _c=None):
                            if _plan_fill_from_target():
                                _plan_flush()
                                _plan_draw_later()
                        _tv.trace_add("write", _on_target_changed)
                    _plan_draw()
                elif t == "condgroup":
                    tk.Label(row, text=f.get("label") or k, bg=BG, fg=FG, font=(FH, 9),
                             width=18, anchor="w").pack(side="left")
                    tk.Entry(row, textvariable=var, bg=BTN, fg=FG, insertbackground=FG,
                             font=(FH, 8), width=52).pack(side="left")
                    _vlab = tk.Label(row, text="", bg=BG, fg=DIM, font=(FH, 8))
                    _vlab.pack(side="left", padx=(4, 0))

                    def _cg_check(_e=None, _v=var, _lab=_vlab):
                        raw = str(_v.get() or "").strip()
                        if not raw:
                            _lab.config(text="", fg=DIM)
                            return
                        if _scenario_parse_condgroup(raw) is not None:
                            _lab.config(text="✓ 条件组合法", fg=FG)
                        else:
                            _lab.config(text="✗ JSON/结构不合法", fg=RED)

                    var.trace_add("write", _cg_check)
                    _cg_check()
                elif t == "bool":
                    tk.Checkbutton(row, text=f.get("label") or "启用", variable=var, bg=BG, fg=FG,
                                   selectcolor=BTN, activebackground=BG, font=(FH, 9),
                                   command=_refresh).pack(side="left")
                else:
                    tk.Label(row, text=f.get("label") or k, bg=BG, fg=FG, font=(FH, 9),
                             width=12, anchor="w").pack(side="left")
                    if t in ("combo", "stage", "scenario"):
                        cb = ttk.Combobox(row, textvariable=var,
                                          values=[c[0] for c in _choices(f)],
                                          font=(FH, 9), width=(26 if t == "scenario" else 18))
                        if not f.get("free"):
                            cb.configure(state="readonly")
                        cb.pack(side="left")
                        cb.bind("<<ComboboxSelected>>",
                                _later(_rebuild if k == "check" else _refresh))
                        if k == "check":
                            # 换「判断什么」= 换一整组字段, 所以这里必须重建而不是只刷新预览
                            pass
                    elif t == "int":
                        tk.Entry(row, textvariable=var, bg=BTN, fg=FG, insertbackground=FG,
                                 font=(FH, 9), width=7).pack(side="left")
                        if f.get("skillPick"):
                            # v4.156★ 主公技能ID不用手敲: 点一下从本季技能库(lordskill)直接选
                            _btn(row, "从技能库选",
                                 lambda v=var: open_lord_skill_picker(
                                     dlg, lambda sid: (v.set(sid), _refresh())),
                                 pad=6).pack(side="left", padx=(4, 0))
                    else:
                        tk.Entry(row, textvariable=var, bg=BTN, fg=FG, insertbackground=FG,
                                 font=(FH, 9), width=(30 if t == "names" else 34)).pack(side="left")
                    # v4.69★ 「挑选」按钮 —— 字段自己声明 pickFrom(hero/spell/target/pick/…),
                    #   这里就生成一个能搜索、能多选的挑选框。names 字段多选后【追加】。
                    # v4.152★ 选武将时额外带上【常用 / 各势力】两组快捷入口(见 _hero_groups)。
                    if f.get("pickFrom"):
                        _grp = None
                        if f.get("pickFrom") == "hero":
                            # 每次重建字段都扫一遍全部剧本太贵 —— 整个对话框算一次就够
                            if "heroGroups" not in st:
                                st["heroGroups"] = _hero_groups()
                            _grp = st["heroGroups"]
                        # ⚠⚠ 与上面 cardplan 那个 var 是同一个坑, 而且这个是【用户直接撞见的那一个】:
                        #   t 和 var 都是 _build 循环里的变量, 点「挑选」时循环早跑完了 ——
                        #   multi=(t=="names") 那时取到的是【最后一个字段】的类型(bool), 于是
                        #   挑选框永远走【单选】分支 → var.set(vals[0]) 把整框内容覆盖掉。
                        #   用户原话: "不能多选? 且挑选完, 就覆盖了?" —— 根因就在这儿。
                        #   修法: 在【这一轮循环里】把值先取出来放进默认参数。
                        _multi = (t == "names")
                        _items = _picker_items(f.get("pickFrom"))
                        _label = f.get("label") or ""
                        _is_hero = (f.get("pickFrom") == "hero")
                        _btn(row, "挑选",
                             lambda v=var, g=_grp, m=_multi, it=_items, lb2=_label, h=_is_hero:
                             _open_name_picker(it, "挑选" + lb2, var=v, multi=m, groups=g,
                                              enable_tag_filter=h),
                             pad=6).pack(side="left", padx=(4, 0))
                    if k == "pick":
                        _btn(row, "＋存为预设",
                             lambda v=var: _save_preset(v)).pack(side="left", padx=(4, 0))
                hint = f.get("hint") or ""
                if hint:
                    # 多行框的说明放到【框下面】(放右边会被挤成一条竖线), 其余字段照旧放右边
                    _ml = (t in ("multiline", "cardplan"))
                    tk.Label(row, text=hint, bg=BG, fg=DIM, font=(FH, 8),
                             wraplength=(680 if _ml else 400),
                             justify="left").pack(side=("top" if _ml else "left"),
                                                  anchor="w",
                                                  padx=((0, 0) if _ml else (8, 0)),
                                                  pady=((3, 0) if _ml else (0, 0)))
            _bind_wheel(fields_box)   # v4.110★ 重建出来的新控件要重新挂上滚轮
            try:
                dlg.after_idle(_fit)
            except Exception:
                _SIL.note("ui/scenario_tab.py:2340", "dlg.after_idle(_fit)")

        def _fit():
            """让窗口贴合当前字段数量, 但【高度封顶】—— 顶到屏幕就交给字段区内部滚动,
            这样「确定/取消」永远留在窗口底部(以前字段一多它们就被挤到屏幕外)。

            只改宽高、不动位置 —— 直接 geometry("") 会把窗口甩到屏幕角上。
            """
            try:
                dlg.update_idletasks()
                # v4.110★ 用【屏幕比例】封顶, 不是减固定像素: 系统缩放一变(125%/150%),
                #   "屏高-260" 算出来的可用高度会偏大, 窗口照样顶出屏幕(用户原话
                #   "用尽锦囊按用法 这个本身一打开很大")。按比例留余量, 缩放多少都够。
                mw = max(560, int(dlg.winfo_screenwidth() * 0.85))
                mh = max(320, int(dlg.winfo_screenheight() * 0.80))
                # 字段区: 内容高就封顶(内部滚), 内容少就贴合 —— 别留一大块空白
                need = fields_box.winfo_reqheight()
                room = max(180, mh - 260)
                _canvas.configure(height=min(need, room))
                dlg.update_idletasks()
                dlg.geometry("%dx%d" % (min(dlg.winfo_reqwidth(), mw),
                                        min(dlg.winfo_reqheight(), mh)))
            except Exception:
                _SIL.note("ui/scenario_tab.py:2348", "dlg.update_idletasks()")

        def _save_preset(var):
            """把当前填的写法存成目标预设 —— 下次开这个框就在下拉里。"""
            val = str(var.get() or "").strip()
            if not val:
                messagebox.showinfo("提示", "先把要存的写法填进「选谁 / 上阵后选人」框, 再点 ＋存为预设")
                return
            if val in SCENARIO_PICK_PRESETS or val in _user_pick_presets():
                messagebox.showinfo("提示", "「%s」已经在预设里了" % val)
                return
            _user_pick_presets().append(val)
            try:
                save_config(self.app.cfg)
            except Exception as e:
                # 存盘失败不致命: 这次能用, 但重启会丢 —— 得说出来
                self.app.log("[剧本] 目标预设写盘失败(这次能用, 重启后会丢): %s" % e, level="WARN")
            self.app.log("[剧本] 已存为目标预设: %s" % val)
            messagebox.showinfo("已保存", "「%s」已加入目标预设(下次打开就在下拉里)" % val)
            _build(reset=False)

        def _ok():
            op = st["op"] or "deploy"
            vals = _collect()
            for f in self._scenario_visible_fields(op, vals):
                if not f.get("req"):
                    continue
                # v4.66★ reqUnless: 另一个字段填了, 这个就不必填 ——
                #   条件的「哪些卡」在【数量形态】(填了数量)下可以留空(留空 = 数总张数,
                #   比如"手牌≥8张"), 两个都填才是"限定这几张卡、看够不够N张"。
                if f.get("reqUnless") and _scenario_val_set(vals.get(f["reqUnless"])):
                    continue
                v = vals.get(f["key"])
                if f.get("type") == "names":
                    empty = not _scenario_split_names(v)
                else:
                    empty = str(v if v is not None else "").strip() == ""
                if empty:
                    messagebox.showwarning("提示", "「%s」不能空 —— %s"
                                           % (f.get("label") or f["key"], f.get("hint") or ""))
                    return
            s = self._scenario_step_from_values(op, vals)
            if op == "callScenario":
                # v4.48★ 两个必然踩的坑在保存前就挡掉: 调用自己(死循环) / 名字写错(运行时才报)
                me = str((self._scenario_cur() or {}).get("name") or "").strip()
                tg = str(s.get("target") or "").strip()
                if me and tg == me:
                    messagebox.showwarning("提示", "不能调用剧本自己 —— 会无限递归, 油猴端也会拦下它。\n"
                                                  "请从下拉里换一个剧本。")
                    return
                known = set()
                for x in (self.scenario_scripts or []):
                    known.add(str((x or {}).get("name") or "").strip())
                    known.add(str((x or {}).get("id") or ""))
                if tg and tg not in known:
                    if not messagebox.askyesno(
                            "没有这个剧本",
                            "当前剧本列表里没有「%s」。\n\n"
                            "运行时会在日志里报『找不到剧本』并跳过这一步。\n\n还是要保存吗?" % tg):
                        return
            if idx is None:
                steps.append(s)
            else:
                steps[idx] = s
            # v4.55★ 编辑完立刻落盘 + 推送到游戏页。
            #   旧版这里只刷新列表, 要用户再去点一次「保存并推送」——忘了点就等于白改,
            #   这正是用户说的"条件不能保存"。
            self._scenario_save(push=True)
            dlg.destroy()

        ttk.OptionMenu(head, op_var, op_var.get(), *op_labels,
                       command=_later(lambda: (_build(reset=True), _refresh()))).pack(side="left")
        tk.Label(head, text="(换类型会换成它自己的字段, 用不到的不会显示)", bg=BG, fg=DIM,
                 font=(FH, 8)).pack(side="left", padx=(6, 0))
        # v4.110★ 排布顺序有讲究: 按钮行 / 预览用 side="bottom" 【先】占住底部,
        #   字段区再去 fill+expand 吃掉剩下的空间 —— 于是窗口怎么缩放(用户把窗口拖小、
        #   系统缩放比例变大), 按钮都被钉在底部看得到。
        #   (以前它们排在滚动区后面, 滚动区一 expand 就把按钮顶出可视区 —— 用户原话
        #    "我一缩小比例、那个按钮就看不到了")
        btns = tk.Frame(body, bg=BG)
        btns.pack(fill="x", side="bottom", pady=(8, 0))
        tk.Button(btns, text="确定", bg=BTN, fg=GOLD, font=(FH, 9, "bold"), width=8,
                  command=_ok).pack(side="right", padx=3)
        tk.Button(btns, text="取消", bg=BTN, fg=FG, font=(FH, 9), width=8,
                  command=dlg.destroy).pack(side="right", padx=3)
        prev.pack(fill="x", side="bottom", pady=(8, 2))
        _scroll_wrap.pack(fill="both", expand=True, pady=(6, 0))

        _build(reset=False)
        _refresh()
        try:
            dlg.update_idletasks()
            dlg.geometry("+%d+%d" % (self.app.root.winfo_rootx() + 120, self.app.root.winfo_rooty() + 80))
        except Exception as e:
            self.app.log("[剧本] 对话框定位失败(用默认位置): %s" % e, level="WARN")
        dlg.grab_set()

    def _scenario_step_dialog_legacy(self, idx):
        """【v4.55 起废弃】旧的"通用4框 + extra 字符串"编辑器 —— 保留仅为对照阅读。

        它依赖的 _scenario_step_fields / _scenario_step_from_fields 已经删除, 所以
        【绝对不要调用它】(跑到这里就会 NameError)。真正在用的是 _scenario_step_editor。
        """
        cur = self._scenario_cur()
        if not cur:
            return
        steps = cur.setdefault("steps", [])
        step = dict(steps[idx]) if (idx is not None and 0 <= idx < len(steps)) else {"op": "deploy"}

        dlg = Toplevel(self.app.root)
        dlg.title("编辑步骤" if idx is not None else "新增步骤")
        dlg.configure(bg=BG)
        dlg.transient(self.app.root)
        dlg.resizable(False, False)
        body = tk.Frame(dlg, bg=BG)
        body.pack(fill="both", expand=True, padx=12, pady=10)
        tk.Label(body, text="提示: 条件步骤选『条件开始』后, 在「判断什么」里选金币/回合/阶段/血量等, "
                 "「目标/内容」框填要比较的数字(阶段填 招募 / 战斗); 武将/锦囊条件直接填名字。",
                 bg=BG, fg=DIM, font=(FH, 8), wraplength=520, justify="left").pack(anchor="w", pady=(0, 6))

        op_labels = [lab for _, lab in SCENARIO_OPS]
        op_var = tk.StringVar(value=SCENARIO_OP_LABELS.get(step.get("op"), op_labels[0]))
        tgt, pos, num, extra = self._scenario_step_fields(step)
        tgt_var = tk.StringVar(value=tgt)
        pos_var = tk.StringVar(value=pos)
        num_var = tk.StringVar(value=num)
        extra_var = tk.StringVar(value=extra)
        init_opt = bool(step.get("optional")) if idx is not None else (step.get("op") == "recycle")
        opt_var = tk.BooleanVar(value=init_opt)

        def _row(label):
            f = tk.Frame(body, bg=BG)
            f.pack(fill="x", pady=2)
            tk.Label(f, text=label, bg=BG, fg=FG, font=(FH, 9), width=9, anchor="w").pack(side="left")
            return f

        f1 = _row("步骤类型")
        ttk.OptionMenu(f1, op_var, op_var.get(), *op_labels).pack(side="left")
        f2 = _row("目标/内容")
        tk.Entry(f2, textvariable=tgt_var, bg=BTN, fg=FG, insertbackground=FG,
                 font=(FH, 9), width=38).pack(side="left")
        tk.Label(_row(""), text="武将名/锦囊名(可逗号分隔); 遣散=名字或【阵上序号:3/第3格】; 条件=要判断的名字; 提示=显示的文字; 选人=要作用到哪个友方",
                 bg=BG, fg=DIM, font=(FH, 8)).pack(side="left")
        f3 = _row("落点")
        tk.Entry(f3, textvariable=pos_var, bg=BTN, fg=FG, insertbackground=FG,
                 font=(FH, 9), width=38).pack(side="left")
        tk.Label(_row(""), text=SCENARIO_POS_HINT, bg=BG, fg=DIM, font=(FH, 8)).pack(side="left")
        f4 = _row("次数/毫秒")
        tk.Entry(f4, textvariable=num_var, bg=BTN, fg=FG, insertbackground=FG,
                 font=(FH, 9), width=12).pack(side="left")
        tk.Label(f4, text="(循环次数 / 牌库刷新次数 / 等待毫秒)", bg=BG, fg=DIM, font=(FH, 8)).pack(side="left")
        # ---------- v4.28★ 附加选项: 按步骤类型显示对应的下拉框/勾选 ----------
        # 旧版是一个要手写 "scope=hand, keepGold=10" 的文本框, 用户反馈"有点复杂没看懂"。
        # 现在改成下拉/勾选, 只显示这一步真正用得上的选项; 而数据源仍然是 extra 字符串
        # (解析入口 _scenario_step_from_fields 没动), 只是改由控件来拼 —— 单一真相来源不变。
        ORIGIN_PAIRS = [("手牌+阵上", "both"), ("只手牌", "hand"), ("只阵上", "field")]
        MODE_PAIRS = [("全部都有", "all"), ("任意一个有", "any")]
        WHERE_PAIRS = [("手牌", "hand"), ("阵上", "field"), ("手牌或阵上", "both")]
        ON_PAIRS = [("牌库(第一张)", "shop"), ("直接使用(不选目标)", "none"),
                    ("牌库1格", "1"), ("牌库2格", "2"), ("牌库3格", "3"),
                    ("牌库4格", "4"), ("牌库5格", "5"), ("牌库6格", "6"),
                    ("手牌(第一张)", "hand"), ("阵上(第一个)", "field")]
        # v4.47★ 条件不再只有"手牌/阵上有没有这张卡": 判金币/回合/等级/血量/阶段时,
        #   「目标/内容」框填数字(阶段填 招募/战斗), 比较符由这里选。
        CHECK_PAIRS = [("手牌/阵上有卡", "card"), ("金币", "gold"), ("回合", "turn"),
                       ("商店等级", "level"), ("血量", "hp"), ("阶段(招募/战斗)", "stage")]
        CMP_PAIRS = [("≥", ">="), ("≤", "<="), ("=", "=="), (">", ">"), ("<", "<")]

        def _pair_label(pairs, val, default):
            for lab, v in pairs:
                if v == str(val):
                    return lab
            return default

        scope_var = tk.StringVar(value=_pair_label(ORIGIN_PAIRS, step.get("scope"), "手牌+阵上"))
        mode_var = tk.StringVar(value=_pair_label(MODE_PAIRS, step.get("mode"), "全部都有"))
        where_var = tk.StringVar(value=_pair_label(WHERE_PAIRS, step.get("where"), "手牌"))
        on_var = tk.StringVar(value=_pair_label(ON_PAIRS, step.get("on"), "牌库(第一张)"))
        onname_var = tk.StringVar(value=str(step.get("onName") or ""))
        keepgold_var = tk.StringVar(value=str(step.get("keepGold") or 0))
        # v4.29★「上阵后选人」: 需要选目标的卡(陈珪这类当先)填这里, 空=不选
        pick_var = tk.StringVar(value=str(step.get("pick") or ""))
        buy_var = tk.BooleanVar(value=(step.get("buy") is not False))
        # v4.47★ 数值/阶段条件用: check=判断什么(金币/回合/…), cmp=比较符。
        #   这两个变量必须在建 row_numcond 之前就存在, 否则下面 OptionMenu 会 NameError。
        check_var = tk.StringVar(value=_pair_label(CHECK_PAIRS, step.get("check") or "card", "手牌/阵上有卡"))
        cmp_var = tk.StringVar(value=_pair_label(CMP_PAIRS, step.get("cmp") or ">=", "≥"))
        level_var = tk.StringVar(value=str(step.get("level") or ""))   # v4.47★ 托管的目标等级

        opt_box = tk.Frame(body, bg=BG)
        opt_box.pack(fill="x")

        def _opt_row(label, var, pairs):
            f = tk.Frame(opt_box, bg=BG)
            tk.Label(f, text=label, bg=BG, fg=FG, font=(FH, 9), width=9, anchor="w").pack(side="left")
            ttk.OptionMenu(f, var, var.get(), *[p[0] for p in pairs]).pack(side="left")
            return f

        def _inline(f, text):
            tk.Label(f, text=text, bg=BG, fg=FG, font=(FH, 9)).pack(side="left")

        row_scope = _opt_row("遣散范围", scope_var, ORIGIN_PAIRS)
        row_cond = _opt_row("条件判定", mode_var, MODE_PAIRS)
        _inline(row_cond, " 查哪儿")
        ttk.OptionMenu(row_cond, where_var, where_var.get(), *[p[0] for p in WHERE_PAIRS]).pack(side="left")
        # v4.47★ 数值/阶段条件那一行(和上面 row_cond 并存: 选"手牌/阵上有卡"看上面, 选了金币/回合看这里)
        row_numcond = tk.Frame(opt_box, bg=BG)
        tk.Label(row_numcond, text="判断什么", bg=BG, fg=FG, font=(FH, 9), width=9,
                 anchor="w").pack(side="left")
        ttk.OptionMenu(row_numcond, check_var, check_var.get(),
                       *[p[0] for p in CHECK_PAIRS]).pack(side="left")
        _inline(row_numcond, " 比较")
        ttk.OptionMenu(row_numcond, cmp_var, cmp_var.get(),
                       *[p[0] for p in CMP_PAIRS]).pack(side="left")
        tk.Label(row_numcond, text="数字填在「目标/内容」框(阶段填 招募 或 战斗)", bg=BG, fg=DIM,
                 font=(FH, 8)).pack(side="left", padx=(4, 0))
        row_spell = _opt_row("作用到", on_var, ON_PAIRS)
        _inline(row_spell, " 目标武将")
        tk.Entry(row_spell, textvariable=onname_var, bg=BTN, fg=FG, insertbackground=FG,
                 font=(FH, 9), width=12).pack(side="left")
        # ---------- v4.41★ 「目标/选人」这两个框改成可编辑下拉, 顺带把目标预设库接上 ----------
        def _user_pick_presets():
            """用户自建的目标预设(存在配置里, 关掉助手也还在)"""
            v = self.app.cfg.get("pick_presets")
            if not isinstance(v, list):
                v = []
                self.app.cfg["pick_presets"] = v
            return v

        def _pick_values():
            out = list(SCENARIO_PICK_PRESETS)
            for x in _user_pick_presets():
                x = str(x).strip()
                if x and x not in out:
                    out.append(x)
            spells = self._scenario_spell_presets()
            if spells:
                out.append("---- 本季锦囊 ----")
                out.extend(spells)
            return out

        def _save_pick_preset():
            """把当前填的写法存成预设 —— 「这个预设也可以添加」"""
            v = (tgt_var.get() or "").strip() or (pick_var.get() or "").strip()
            if not v:
                messagebox.showinfo("提示", "先把要存的写法填进「目标」框(或「上阵后选人」框), 再点 +存为预设")
                return
            if v in SCENARIO_PICK_PRESETS or v in _user_pick_presets():
                messagebox.showinfo("提示", f"「{v}」已经在预设里了")
                return
            _user_pick_presets().append(v)
            try:
                save_config(self.app.cfg)
            except Exception as e:
                # 存盘失败不算致命: 本次对话框里已经能用了, 但重启会丢 —— 必须说出来
                self.app.log(f"[剧本] 目标预设写盘失败(这次能用, 重启后会丢): {e}", level="WARN")
            try:
                self.app.log(f"[剧本] 已存为目标预设: {v}")
            except Exception as e:
                # App 上的日志入口叫 log, 不是 _log(v4.43★ 顺手修: 以前写成 self._log →
                # AttributeError 被这里吞掉, 界面看着"存好了"但日志里一条都没有)
                print(f"[剧本] 写日志失败: {e}", file=sys.stderr)
            messagebox.showinfo("已保存", f"「{v}」已加入目标预设(下次开这个对话框就在下拉里)")

        # v4.29★ 上阵后选人: 只对"需要玩家选目标"的卡(陈珪这类当先)有意义
        row_pick = tk.Frame(opt_box, bg=BG)
        tk.Label(row_pick, text="上阵后选人", bg=BG, fg=FG, font=(FH, 9), width=9, anchor="w").pack(side="left")
        ttk.Combobox(row_pick, textvariable=pick_var, values=_pick_values(),
                     font=(FH, 9), width=14).pack(side="left")
        tk.Label(row_pick, text="留空=不选 · 3=第3格 · 武将名=选它 · auto=第一张非同名友方",
                 bg=BG, fg=DIM, font=(FH, 8)).pack(side="left")
        # v4.32★ 挑目标(武将/锦囊): 服务器要求二选一的技能, 两套协议完全不同, 所以要能指定类型。
        #   留空=自动(按服务器此刻在等的类型判断)。
        # v4.41★ 这里原来又建了一个 textvariable=pick_var 的输入框, 两个问题, 实机反馈就是
        #   「挑目标的目标输入不了」:
        #     ① 和上面「上阵后选人」那个框【共用同一个 tk.StringVar】—— 上面那个虽然被
        #        pack_forget() 藏起来了, 但控件还活着、还绑着这个变量, 两边互相顶, 打字光标乱跳;
        #     ② 更关键: 就算真输进去了也【没有任何人读它】—— pickOne 真正生效的值走的是
        #        「目标/内容」那个框(tgt_var, 见 _scenario_step_from_fields 里 s["pick"] = target),
        #        在这个框里填的等于白填。
        #   现在直接绑到真正生效的 tgt_var 上(和上面「目标/内容」是同一个值, 改哪边都算),
        #   下拉里给常用写法, 还能把自己要用的存成预设。
        KIND_PAIRS = [("自动(按服务器在等的类型)", ""), ("挑武将", "chess"), ("挑锦囊", "spell")]
        kind_var = tk.StringVar(value=_pair_label(KIND_PAIRS, step.get("kind"), "自动(按服务器在等的类型)"))
        row_kind = _opt_row("挑哪类", kind_var, KIND_PAIRS)
        _inline(row_kind, " 目标")
        ttk.Combobox(row_kind, textvariable=tgt_var, values=_pick_values(),
                     font=(FH, 9), width=14).pack(side="left")
        tk.Button(row_kind, text="+存为预设", command=_save_pick_preset, bg=BTN, fg=FG,
                  activebackground=BTN, activeforeground=FG, font=(FH, 8),
                  relief="flat", padx=5, cursor="hand2").pack(side="left", padx=(4, 0))
        tk.Label(row_kind, text=SCENARIO_PICK_HINT, bg=BG, fg=DIM, font=(FH, 8)).pack(side="left")
        # v4.48★ 「调用另一个剧本」: 目标框改成下拉, 直接选已有剧本名。
        #   手打错一个字运行时只会报"找不到剧本" —— 而那句话在日志里, 用户很容易漏看,
        #   现象就是"我这个剧本跑一半没反应了"。下拉里直接排除当前剧本, 从源头上挡掉自调用。
        def _call_values():
            me = ""
            try:
                me = str((self._scenario_cur() or {}).get("name") or "").strip()
            except Exception:
                me = ""
            out = []
            for x in (self.scenario_scripts or []):
                nm = str((x or {}).get("name") or "").strip()
                if nm and nm != me and nm not in out:
                    out.append(nm)
            return out

        row_call = tk.Frame(opt_box, bg=BG)
        tk.Label(row_call, text="调用哪个", bg=BG, fg=FG, font=(FH, 9), width=9,
                 anchor="w").pack(side="left")
        ttk.Combobox(row_call, textvariable=tgt_var, values=_call_values(),
                     font=(FH, 9), width=20).pack(side="left")
        tk.Label(row_call, text="(下拉=已有剧本; 它会就地跑完再回到本剧本)", bg=BG, fg=DIM,
                 font=(FH, 8)).pack(side="left")
        row_shop = tk.Frame(opt_box, bg=BG)
        tk.Label(row_shop, text="保留金币", bg=BG, fg=FG, font=(FH, 9), width=9, anchor="w").pack(side="left")
        tk.Entry(row_shop, textvariable=keepgold_var, bg=BTN, fg=FG, insertbackground=FG,
                 font=(FH, 9), width=6).pack(side="left")
        tk.Label(row_shop, text="(低于这个数就不刷新)", bg=BG, fg=DIM, font=(FH, 8)).pack(side="left")
        # 这个勾选只对"会买东西"的步骤(牌库找卡/按决策器执行)有意义 —— 纯刷新时下面会把它收起来
        buy_chk = tk.Checkbutton(row_shop, text="找到就买下来", variable=buy_var, bg=BG, fg=FG,
                                 selectcolor=BTN, activebackground=BG, font=(FH, 9))
        buy_chk.pack(side="left", padx=(8, 0))
        # v4.47★ 「按决策器执行」专属: 想升到几级(留空 = 按回合自动给赛季常规节奏)
        row_level = tk.Frame(opt_box, bg=BG)
        tk.Label(row_level, text="目标等级", bg=BG, fg=FG, font=(FH, 9), width=9, anchor="w").pack(side="left")
        tk.Entry(row_level, textvariable=level_var, bg=BTN, fg=FG, insertbackground=FG,
                 font=(FH, 9), width=6).pack(side="left")
        tk.Label(row_level, text="(留空 = 按回合自动: ≤2回合4级 / ≤5回合5级 / 之后6级)",
                 bg=BG, fg=DIM, font=(FH, 8)).pack(side="left")
        opt_hint = tk.Label(body, text="", bg=BG, fg=DIM, font=(FH, 8))
        opt_hint.pack(anchor="w")

        def _sync_extra(*_a):
            """控件 → extra 字符串(仍走 _scenario_step_from_fields 那唯一一个解析入口)"""
            op0 = SCENARIO_LABEL_OPS.get(op_var.get(), "deploy")
            parts = []
            if op0 == "recycle":
                parts.append("scope=" + dict(ORIGIN_PAIRS)[scope_var.get()])
            elif op0 == "ifStart":
                # v4.47★ 两种形态互斥: 判"有没有卡"→ mode/where; 判数值/阶段→ check/cmp。
                chk = dict(CHECK_PAIRS)[check_var.get()]
                if chk == "card":
                    parts.append("check=card")
                    parts.append("mode=" + dict(MODE_PAIRS)[mode_var.get()])
                    parts.append("where=" + dict(WHERE_PAIRS)[where_var.get()])
                else:
                    parts.append("check=" + chk)
                    if chk != "stage":      # 阶段比的是字符串, 没有比较符
                        parts.append("cmp=" + dict(CMP_PAIRS)[cmp_var.get()])
            elif op0 == "useSpell":
                parts.append("on=" + dict(ON_PAIRS)[on_var.get()])
                if onname_var.get().strip():
                    parts.append("onName=" + onname_var.get().strip())
            elif op0 == "shopFind":
                kg = str(keepgold_var.get()).strip()
                if kg not in ("", "0"):
                    parts.append("keepGold=" + kg)
                if not buy_var.get():
                    parts.append("buy=0")
            elif op0 in ("refreshShop", "moneyPlan"):   # v4.47★ 都要"保留金币"; 托管还认"目标等级/找到就买"
                kg = str(keepgold_var.get()).strip()
                if kg not in ("", "0"):
                    parts.append("keepGold=" + kg)
                if op0 == "moneyPlan":
                    lv = str(level_var.get()).strip()
                    if lv:
                        parts.append("level=" + lv)
                    if not buy_var.get():
                        parts.append("buy=0")
            elif op0 == "deploy":                      # v4.29★
                if pick_var.get().strip():
                    parts.append("pick=" + pick_var.get().strip())
            elif op0 == "pickOne":                     # v4.32★ 挑武将/挑锦囊
                parts.append("kind=" + dict(KIND_PAIRS)[kind_var.get()])
            extra_var.set(", ".join(parts))

        def _show_op_options(*_a):
            op0 = SCENARIO_LABEL_OPS.get(op_var.get(), "deploy")
            # v4.48★ 「调用剧本」的那行下拉(row_call)必须在这里登记 —— all_rows 里没登记的行会被
            #   pack_forget() 掉, 于是"点了「调用另一个剧本」却看不到下拉"(只剩一个要手打的目标框,
            #   名字错一个字就只有运行时日志里一句"找不到剧本")。这类"控件建了但没接上"的坑
            #   正是 dev/_verify_scenario.py 要照的那一类。
            shown = {"recycle": [row_scope], "useSpell": [row_spell],
                     "shopFind": [row_shop], "refreshShop": [row_shop],
                     "moneyPlan": [row_shop, row_level],
                     "deploy": [row_pick], "pickOne": [row_kind],
                     "callScenario": [row_call]}.get(op0) or []
            if op0 == "ifStart":
                # v4.47★ 条件分两种: 判卡(条件判定行) 还是 判数值/阶段(判断什么那一行)。
                #   一次只显示用得上的那行, 免得用户两个都填, 不知道哪个生效。
                k = dict(CHECK_PAIRS)[check_var.get()]
                shown = [row_cond] if k == "card" else [row_numcond]
            all_rows = (row_scope, row_cond, row_numcond, row_spell, row_shop, row_level,
                        row_pick, row_kind, row_call)
            for f in all_rows:
                if f in shown:
                    f.pack(fill="x", pady=2)
                else:
                    f.pack_forget()
            # 纯刷新的步骤没有"买"这回事, 把那个勾选收起来, 免得用户以为勾了会顺便买卡
            if op0 in ("shopFind", "moneyPlan"):
                buy_chk.pack(side="left", padx=(8, 0))
            else:
                buy_chk.pack_forget()
            # v4.47★ 新步骤的用法提示: 这个字典一直是"写了但没人读"的状态(用户只能猜框里填什么),
            #   在这里真正接到界面上。
            hint = SCENARIO_NEWOP_HINT.get(op0, "")
            opt_hint.config(text=hint or ("" if shown else "这一步没有附加选项"))

        tk.Checkbutton(_row(""), text="这一步失败也继续 (可忽略)", variable=opt_var, bg=BG, fg=FG,
                       selectcolor=BTN, activebackground=BG, font=(FH, 9)).pack(side="left")

        prev = tk.Label(body, text="", bg=BG, fg=GOLD, font=(FH, 9), justify="left", wraplength=620)
        prev.pack(anchor="w", pady=(8, 2))

        def _mk():
            op = SCENARIO_LABEL_OPS.get(op_var.get(), "deploy")
            s = self._scenario_step_from_fields(op, tgt_var.get(), pos_var.get(),
                                                num_var.get(), extra_var.get(), optional=opt_var.get())
            try:
                txt = "→ " + self._scenario_step_desc(s)
                _miss = self._scenario_missing_names(s)
                if _miss:
                    # v4.30★ 用锦囊查的是锦囊表(cards/tavern_spells), 不是武将表 —— 提示要分开,
                    #   并且告诉用户"名字带不带间隔号都行", 免得对着表里的『顺手牵羊·三』不敢填。
                    if op == "useSpell":
                        txt += ("    ⚠ 「" + "」「".join(_miss) + "」在锦囊表里查不到"
                                + "(名字带不带间隔号都行, 比如 顺手牵羊三 / 顺手牵羊·三)")
                    else:
                        txt += "    ⚠ 「" + "」「".join(_miss) + "」在武将表里查不到, 建议换成表里的写法"
                prev.config(text=txt)
            except Exception as e:
                prev.config(text=f"(预览不可用: {e})")
            return s

        for v in (op_var, tgt_var, pos_var, num_var, extra_var, opt_var):
            try:
                v.trace_add("write", lambda *a: _mk())
            except tk.TclError:
                v.trace("write", lambda *a: _mk())   # 老版 Tk 没有 trace_add, 退回 trace

        # v4.28★ 切换步骤类型 → 换一组附加选项, 并把选中的值同步进 extra
        def _on_op_change(*_a):
            _show_op_options()
            _sync_extra()

        try:
            op_var.trace_add("write", lambda *a: _on_op_change())
        except tk.TclError:
            op_var.trace("write", lambda *a: _on_op_change())
        # 选项控件变化 → 同步 extra(extra 已连到 _mk, 预览会自动跟着更新)
        for v in (scope_var, mode_var, where_var, check_var, cmp_var, on_var, onname_var,
                  keepgold_var, level_var, pick_var, buy_var, kind_var):
            try:
                v.trace_add("write", lambda *a: _sync_extra())
            except tk.TclError:
                v.trace("write", lambda *a: _sync_extra())
        # 判断什么 变了 → 除了同步 extra, 还要换掉显示哪一行(判卡 / 判数值)
        try:
            check_var.trace_add("write", lambda *a: _show_op_options())
        except tk.TclError:
            check_var.trace("write", lambda *a: _show_op_options())
        _show_op_options()
        _mk()

        btns = tk.Frame(body, bg=BG)
        btns.pack(fill="x", pady=(8, 0))

        def _ok():
            s = _mk()
            if s.get("op") in ("deploy", "shopFind", "useSpell", "buyCard", "callScenario") \
                    and not s.get("target"):
                messagebox.showwarning("提示", "这一步要填「目标/内容」")
                return
            if s.get("op") == "callScenario":
                # v4.48★ 保存前就把两种必然踩的坑挡掉: 调用自己(死循环) / 名字写错(运行时才报, 看不出来)
                _me = str((self._scenario_cur() or {}).get("name") or "").strip()
                _tg = str(s.get("target") or "").strip()
                if _me and _tg == _me:
                    messagebox.showwarning("提示", "不能调用剧本自己 —— 会无限递归, 油猴端也会拦下它。\n"
                                                  "请从下拉里换一个剧本。")
                    return
                _known = set()
                for _x in (self.scenario_scripts or []):
                    _known.add(str((_x or {}).get("name") or "").strip())
                    _known.add(str((_x or {}).get("id") or ""))
                if _tg and _tg not in _known:
                    if not messagebox.askyesno(
                            "没有这个剧本",
                            f"当前剧本列表里没有「{_tg}」。\n\n"
                            "运行时会在日志里报『找不到剧本』并跳过这一步。\n"
                            "可能是还没建、或者名字打错了。\n\n还是要保存吗?"):
                        return
            if s.get("op") == "ifStart" and not s.get("target"):
                messagebox.showwarning("提示", "条件这一步要填「目标/内容」：\n"
                                              "· 判断手牌/阵上 → 填卡名\n"
                                              "· 判断金币/回合/等级/血量 → 填数字\n"
                                              "· 判断阶段 → 填 招募 或 战斗")
                return
            if idx is None:
                steps.append(s)
            else:
                steps[idx] = s
            self._scenario_refresh_list()
            dlg.destroy()

        tk.Button(btns, text="确定", bg=BTN, fg=GOLD, font=(FH, 9, "bold"), width=8,
                  command=_ok).pack(side="right", padx=3)
        tk.Button(btns, text="取消", bg=BTN, fg=FG, font=(FH, 9), width=8,
                  command=dlg.destroy).pack(side="right", padx=3)
        try:
            dlg.update_idletasks()
            x = self.app.root.winfo_rootx() + 120
            y = self.app.root.winfo_rooty() + 80
            dlg.geometry(f"+{x}+{y}")
        except Exception as e:
            self.app.log(f"[剧本] 对话框定位失败(用默认位置): {e}", level="WARN")
        dlg.grab_set()

    # ---------- 剧本: 剧本级操作 ----------
    def _scenario_new(self):
        n = len(self.scenario_scripts) + 1
        hk = f"ctrl+alt+{n}" if n <= 9 else ""
        self.scenario_scripts.append({
            "id": f"s{n}_{int(time.time())}", "name": f"剧本{n}", "hotkey": hk,
            # v4.55★ 剧本级默认值: 按下就跑一次、正常速度、启用、不触发别的剧本
            "triggerMode": "press", "runCount": 1, "speed": 1.0, "maxDuration": 0,
            "onStart": "", "onEnd": "", "enabled": True,
            "steps": [],
        })
        self.scenario_cur_idx = len(self.scenario_scripts) - 1
        self._scenario_refresh_list()
        self._scenario_save(push=True)      # v4.55★ 新增即落盘, 不用再手点保存
        self.app.log(f"[剧本] 已新增「剧本{n}」触发按键 {hk or '(未设)'} (点「捕获按键」可以直接录一个)")

    def _scenario_dup(self):
        cur = self._scenario_cur()
        if not cur:
            return
        import copy as _copy
        new = _copy.deepcopy(cur)
        new["id"] = f"{cur.get('id')}_c{int(time.time())}"
        new["name"] = str(cur.get("name") or "") + " 副本"
        self.scenario_scripts.insert(self.scenario_cur_idx + 1, new)
        self.scenario_cur_idx += 1
        self._scenario_refresh_list()
        # v4.19x★ 复制完立刻落盘+推送 —— 以前只刷列表不保存(新增/删除都有 save, 唯独复制没有):
        #   复制完不做别的操作就关助手, 副本直接丢; 页面端也没拿到它(按快捷键永远找不到)。
        self._scenario_save(push=True)

    def _scenario_del(self):
        cur = self._scenario_cur()
        if not cur:
            return
        _nm = str(cur.get("name") or "")
        _refs = self._scenario_ref_users(_nm)         # v4.48★ 有别人在"调用"它要说一声, 否则删完就断链
        _msg = f"删除剧本「{_nm}」?"
        if _refs:
            _msg += (f"\n\n注意: 有 {len(_refs)} 处步骤在调用它 —— 删掉后那些步骤运行时会报"
                     "『找不到剧本』并跳过:\n· " + "\n· ".join(_refs[:6]))
            if len(_refs) > 6:
                _msg += f"\n· …等共 {len(_refs)} 处"
        if not messagebox.askyesno("确认", _msg):
            return
        self.scenario_scripts.pop(self.scenario_cur_idx)
        self.scenario_cur_idx = max(0, self.scenario_cur_idx - 1)
        self._scenario_refresh_list()
        self._scenario_save(push=True)

    def _scenario_save(self, push=True, notify=False):
        self._scenario_apply_head()
        self.app.cfg["scenarios"] = self._scenario_collect()
        try:
            save_config(self.app.cfg)
        except Exception as e:
            self.app.log(f"[剧本] 本地保存失败: {e}", level="ERROR")
        pushed = False
        if push and self.app.ops and self.app.ops.connected():
            # v4.101★ push_scenarios 超时 3 秒 —— 以前在主线程直连: 点「保存」界面僵 3 秒。
            #   参数在主线程收集好传进去, 推送丢后台, 结果回来单独回一句。
            _scen = self._scenario_collect(include_transport_fields=True)

            def _push_done(r, err):
                if err:
                    self.app.log(f"[剧本] 推送失败: {err}", level="ERROR")
                else:
                    self.app.log("[剧本] 已推送到游戏页")

            self.app._run_bg("scenario_push", (lambda: self.app.ops.push_scenarios(_scen)),
                         _push_done, wait="剧本已存本地, 正在推送到游戏页…")
            pushed = True
        self._scenario_refresh_list()
        if notify:
            self.app.log("[剧本] 已保存(推送在后台, 结果下面单独回一句)" if pushed
                     else "[剧本] 已保存(未连接游戏页, 仅存本地)")

    # ---------- 剧本: 执行 ----------
    def _scenario_run_once(self, limit=0):
        """执行一次。v4.67★ limit>0 = 试运行, 只跑前 N 步就收工(入口是 _scenario_run_trial)。"""
        cur = self._scenario_cur()
        if not cur:
            self.app.log("[剧本] 请先「新增」一个剧本", level="WARN")
            return
        if not (cur.get("steps") or []):
            self.app.log("[剧本] 该剧本还没有步骤", level="WARN")
            return
        if self.scenario_running:
            self.app.log("[剧本] 已有剧本在执行中, 忽略", level="WARN")
            return
        if not self.app.ops or not self.app.ops.connected():
            self.app.log("[剧本] 未连接游戏页, 无法执行", level="WARN")
            return
        self._scenario_save(push=True)      # 先保证页面拿到最新步骤
        _lim = max(0, int(limit or 0))
        _nstep = len(cur.get("steps") or [])
        if _lim > _nstep:
            _lim = _nstep
        self.scenario_running = True
        self.scenario_stop_flag = False
        self.scenario_log_seq = 0
        self.scenario_hl_idx = -1           # v4.67★ 别把上一轮的高亮带到这一轮
        self.scenario_status_var.set(
            f"试跑前 {_lim} 步: {cur.get('name')}" if _lim else f"执行中: {cur.get('name')}")
        threading.Thread(target=self._scenario_run_thread, args=(dict(cur), _lim),
                         daemon=True).start()

    def _scenario_run_trial(self):
        """v4.67★ 「先跑几步看看对不对」—— 写剧本最缺的就是这一件事。

        以前只有『执行一次』: 第 3 步写错了, 前两步已经【真的】落进局里(卡买了、金币花了),
        要么认了要么重开。这里从第 1 步开始只走到第 N 步。
        停在 N 步算【正常收工】不算失败 —— 页面端也是这么标的(见 _scRunSteps 的 trial 分支),
        否则日志里一个红字"失败"会让人以为是剧本写坏了, 反而去查半天。
        """
        try:
            n = int(str(self.scenario_trial_var.get() or "").strip())
        except (TypeError, ValueError):
            n = 0
        if n <= 0:
            self.app.log("[剧本] 「试跑」要填一个大于 0 的整数(填 3 = 只跑前 3 步)", level="WARN")
            return
        self.app.log(f"[剧本] 试运行: 从第 1 步起只跑前 {n} 步")
        self._scenario_run_once(limit=n)

    # ---------- v4.101★ 通用: 把会阻塞的 ops 调用挪出 Tk 主线程 ----------
    def _scenario_stop(self):
        # v4.101★ 【点了停止却停不下来】根因就是以前那句门卫 `if not self.scenario_running:
        #   return` —— 本地没记"在执行"就【一个停止包都不发】。而本地状态跟页面对不上有三种情况,
        #   偏偏都是用户最想停的时候:
        #     ① 剧本是【游戏页快捷键】触发的(比如 alt+4) —— 助手端压根没参与启动, 自然没有记录;
        #     ② 助手端跟踪超时/收工后放弃了等待(日志里那句"页面可能仍在跑") —— 页面其实还在
        #        把剩下的循环跑完, 而「硬刷孙策」是 100 轮 × 每轮最多 10 次刷新, 能跑很久;
        #     ③ 助手端这边线程异常退出(连接抖动等)。
        #   现象就是用户看到的"点停止, 页面照跑"。停止指令本身是【幂等】的(页面没在跑时把
        #   stopFlag 置一下毫无副作用, 下次开跑还会清零), 所以改成【无条件下发】, 发完再回读
        #   一次页面状态, 明确告诉用户"真停了"还是"页面那边没收到"。
        self._scenario_stop_local = bool(self.scenario_running)
        self.scenario_stop_flag = True
        if not self._scenario_stop_local:
            self.app.log("[剧本] 本地没有执行中的记录 —— 这一趟可能是【游戏页快捷键】起的, 或者助手端"
                     "已经放弃跟踪(页面其实还在跑)。停止指令照发(幂等, 页面没在跑也无害)。",
                     level="WARN")
        self.app.log("[剧本] 已请求停止 —— 立刻中断(正在跑的那一步也停下, 剩余步骤都不执行);"
                 " 只有「快速挑空」会把屏幕上的挑牌窗收干净, 免得游戏禁止你手动操作")
        # v4.101★ 发停止 + 等 0.8 秒 + 回读页面状态, 这三步以前全在【主线程】:
        #   stop_scenario(3s) + sleep(0.8) + poll_scenario_log(3s) = 最坏 6.8 秒界面完全无响应,
        #   而用户点"停止"时恰恰最需要界面立刻有反馈。现在整段挪进工作线程。
        self.app._run_bg("scenario_stop", self._scenario_stop_task, self._scenario_stop_done)

    def _scenario_stop_task(self):
        """工作线程: 发停止指令 → 等 0.8 秒 → 回读页面到底停没停。"""
        if not (self.app.ops and self.app.ops.connected()):
            return {"sent": False, "why": "未连接游戏页 —— 停止指令发不出去(只能停掉本地跟踪)"}
        try:
            self.app.ops.stop_scenario()
        except Exception as e:
            return {"sent": False, "why": "发送停止请求失败: %s" % e}
        time.sleep(0.8)          # 停止是打断式的, 0.8 秒足够退出当前那一步
        try:
            d = self.app.ops.poll_scenario_log(int(self.scenario_log_seq or 0)) or {}
            return {"sent": True, "running": bool(d.get("running"))}
        except Exception as e:
            return {"sent": True, "running": None, "why": "停止后回读状态失败: %s" % e}

    def _scenario_stop_done(self, r, err):
        if err:
            self.app.log(f"[剧本] 停止异常: {err}", level="WARN")
            return
        r = r or {}
        if not r.get("sent"):
            self.app.log("[剧本] %s" % (r.get("why") or "停止指令没发出去"), level="WARN")
            return
        if r.get("why"):
            self.app.log("[剧本] %s" % r["why"], level="WARN")
            return
        if r.get("running"):
            self.app.log("[剧本] ⚠ 页面端回读仍在执行 —— 停止信号已送到, 它会停在当前那一步;"
                     " 要是几秒后还在动, 再点一次停止; 还不行就刷新游戏页(那一定能停)。",
                     level="WARN")
        elif self._scenario_stop_local:
            self.app.log("[剧本] ✔ 已停止(页面端回读 running=false)")
        else:
            self.app.log("[剧本] · 页面端回读也没有在执行(本来就是停着的)")

    # ---------- 剧本: 正在跑的那一步高亮 (v4.67★) ----------
    def _scenario_hl_apply(self, idx, running):
        """把"正在跑的那一行"点亮。这里是工作线程 → 一律 root.after 回 UI 线程再动控件。

        为什么要有它: 剧本跑起来以前只有一片滚动日志, 列表里看不出跑到哪儿了 ——
        十几步的剧本一旦某步卡住, 只能对着日志文字自己数"现在是第几步", 数错一步就会去改错行。
        """
        try:
            self.app.root.after(0, lambda i=idx, rr=running: self._scenario_hl_paint(i, rr))
        except Exception as e:
            sys.stderr.write(f"[剧本] 高亮调度失败(界面可能已关闭): {e}\n")

    def _scenario_hl_paint(self, idx, running):
        """(UI 线程) 打标签/清标签。idx<0 或没在跑 = 清掉。"""
        tree = getattr(self, "scenario_tree", None)
        if tree is None:
            return
        for iid in list(getattr(self, "_scenario_hl_items", None) or ()):
            try:
                tree.item(iid, tags=())
            except Exception as e:
                # 清不掉就说明这一行已经没了(表格刚被刷新过) —— 不影响后面重新打标签,
                # 但【不能静默吞掉】: 真出别的问题时, 没有这行就只剩"高亮有时候会残留"这种
                # 没法复现的现象(见 dev/_verify_static.py 对 except: pass 的预算)。
                sys.stderr.write(f"[剧本] 清高亮标签失败: {e}\n")
        self._scenario_hl_items = []
        if not running:
            self.scenario_hl_idx = -1
            return
        try:
            n = int(idx)
        except (TypeError, ValueError):
            n = -1
        self.scenario_hl_idx = n
        if n < 0:
            return
        iid = str(n)        # iid 就是步骤下标(建表时 insert(..., iid=str(i)))
        try:
            if tree.exists(iid):
                tree.item(iid, tags=("run",))
                self._scenario_hl_items = [iid]
                tree.see(iid)   # 跟着脚本滚, 别让高亮停在看不见的地方
        except Exception as e:
            sys.stderr.write(f"[剧本] 高亮打标签失败: {e}\n")

    def _scenario_run_thread(self, sc, limit=0):
        """后台跑: 启动剧本 + 轮询页面日志, 直到 running=False 或超时

        v4.67★ limit>0 = 试运行前 N 步(截断在页面端做, 见 _scRunSteps 的 trial 分支)。
        """
        name = sc.get("name") or ""
        try:
            r = self.app.ops.start_scenario(sc.get("id"), limit)
            if not r or not r.get("ok"):
                reason = (r or {}).get("reason") or "未知"
                hint = ""
                if "startScriptById" in str(reason):
                    need = "v" + ".".join(str(x) for x in MIN_SCRIPT_VER)
                    hint = f" —— 请点助手【⚡一键注入】更新到 ≥{need}，再刷新游戏页(F5)"
                self.app.log(f"[剧本] 启动失败: {reason}{hint}", level="ERROR")
                return
            self.app.log(f"[剧本] 开始「{name}」共 {r.get('steps')} 步 (页面日志如下)")
            t0 = time.time()
            idle = 0
            stop_sent = False
            # v4.67★ 跟踪的"放弃等待"上限 = 剧本自己的总时长封套 + 60 秒余量。
            #   为什么必须让它跟着封套走: 这里是【助手端的跟随】, 页面端才是真正在跑的那个 ——
            #   用户把「最长运行」填成 300 秒, 助手端却照旧的 600 秒硬顶去等, 就会出现
            #   "页面上已经在收尾了, 助手端还在空转"或者反过来("助手端已经放弃, 其实还在跑")。
            _md = int(sc.get("maxDuration") or 0)
            _track_cap = max(600.0, float(_md) + 60.0)
            _stat_last = 0.0
            while True:
                # 停止只重发一次: 轮询间隔 0.35 秒, 每轮重发会把日志刷爆
                if self.scenario_stop_flag and not stop_sent:
                    stop_sent = True
                    try:
                        self.app.ops.stop_scenario()
                    except Exception as e:
                        self.app.log(f"[剧本] 重发停止请求失败: {e}", level="WARN")
                data = self.app.ops.poll_scenario_log(self.scenario_log_seq) or {}
                if data:
                    # v4.67★ 把"正在跑的那一行"点亮(这里是工作线程 → 一律走 root.after 回 UI 线程)。
                    #   高亮与日志取自【同一次轮询】, 所以两者讲的永远是同一步 ——
                    #   另接一个接口去问"现在第几步", 迟早出现"日志已经走到第7步、高亮还停在第5行"。
                    self._scenario_hl_apply(data.get("curStep", -1), bool(data.get("running")))
                    # v4.67★ 总时长封套的进度写进状态栏(「已用 42s / 上限 60s」)。
                    #   为什么值得显示: 封套是在【步骤之间】判的 —— 屏幕上"卡住不动"的那十几秒
                    #   到底是"快收尾了"还是"死在这儿了", 光看日志分不出来; 有了这个数就有依据。
                    #   _stat_last 限流 1 秒一次: 轮询本身 0.35 秒一轮, 每轮都 set 会让状态栏闪。
                    _now = time.time()
                    if data.get("running") and _now - _stat_last >= 1.0:
                        _stat_last = _now
                        _md2 = int(data.get("maxDuration") or 0)
                        _used = int(data.get("usedSec") or 0)
                        _txt = (f"执行中: {name}" if limit == 0 else f"试跑前 {limit} 步: {name}")
                        if _md2 > 0:
                            _txt += f" · 已用{_used}秒/上限{_md2}秒"
                        else:
                            _txt += f" · 已用{_used}秒"
                        try:
                            self.app.root.after(0, lambda t=_txt: self.scenario_status_var.set(t))
                        except Exception as e:
                            # 界面已经关了 / after 队列不可用 —— 不影响剧本本身, 但要说一声:
                            # 静默吞掉的话, 现象是"状态栏一直停在旧字上", 而日志里什么都没有。
                            sys.stderr.write(f"[剧本] 状态栏更新失败: {e}\n")
                    for ln in (data.get("lines") or []):
                        try:
                            seq_now = int(ln.get("seq") or 0)
                        except (TypeError, ValueError):
                            seq_now = 0
                        self.scenario_log_seq = max(self.scenario_log_seq, seq_now)
                        lv = str(ln.get("lv") or "info").upper()
                        self.app.log("[剧本] " + str(ln.get("s") or ""),
                                 level={"ERROR": "ERROR", "WARN": "WARN"}.get(lv, "INFO"))
                    if not data.get("running"):
                        lr = data.get("lastResult")
                        if lr:
                            ok = bool(lr.get("ok"))
                            # v4.77★ 「玩家按停」既不是"完成"也不是"失败": 页面端按停时给的是
                            #   ok=false + stopped=true。不单独认它的话, 用户自己按的停止会在日志里
                            #   变成一行红字「失败: 玩家停止」—— 看着像剧本坏了, 又要白查一轮。
                            stopped = bool(lr.get("stopped"))
                            _tail = ("完成" if ok else ("失败: " + str(lr.get("reason") or "")))
                            if stopped:
                                _tail = "玩家停止 —— " + str(lr.get("reason") or "已中断(剩余步骤未执行)")
                            # v4.67★ 中止收尾【不是失败】—— 页面端 ok=true + aborted=true
                            #   (见 _scRunSteps: 剩余步骤跳过, 收尾挑空照做)。以前这里会把它
                            #   显示成"完成", 用户根本看不出"它是被中止/被时长封套拦下来的"。
                            elif ok and lr.get("aborted"):
                                _tail = "中止收尾 —— " + str(lr.get("reason") or "已中止")
                            elif ok and lr.get("trial"):
                                _tail = "试运行结束 —— " + str(lr.get("reason") or "")
                            self.app.log(f"[剧本] 「{lr.get('name')}」" + _tail +
                                     f" 耗时{lr.get('ms')}ms",
                                     level="INFO" if (ok or stopped) else "ERROR")
                            break
                        idle += 1
                        if idle >= 6:      # 页面没在执行也没有结果 → 收工
                            break
                if time.time() - t0 > _track_cap:
                    self.app.log(f"[剧本] 跟踪超时({int(_track_cap)}秒), 放弃等待 (页面可能仍在跑)",
                             level="WARN")
                    break
                time.sleep(0.35)
        except Exception as e:
            self.app.log(f"[剧本] 执行异常: {e}", level="ERROR")
        finally:
            self.scenario_running = False
            self.scenario_stop_flag = False
            # v4.19x★ 跟踪超时/异常退出时也要清掉"正在跑"高亮 —— 正常收尾那次
            #   poll(running=false)会清, 但超时 break / 异常这条路上没人清:
            #   那行金色高亮会一直挂在表上, 状态栏也停在"执行中", 看着像还在跑。
            self._scenario_hl_apply(-1, False)
            try:
                self.app.root.after(0, lambda: self.scenario_status_var.set("未执行"))
            except Exception as e:
                sys.stderr.write(f"[剧本] 重置状态显示失败(界面可能已关闭): {e}\n")

    # ---------- Tab: 赛季数据 (v4.33★) ----------