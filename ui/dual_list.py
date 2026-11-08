# -*- coding: utf-8 -*-
"""v4.13★ 可复用双列表组件 DualListPanel

布局:
  ┌──────────────┬──────────────┐
  │ 全部武将      │  已配置武将   │
  │ [搜索框]      │              │
  │ ┌──────────┐ │  ┌──────────┐│
  │ │ 吕蒙(123)│ │  │ 陆逊(456)││
  │ │ ...      │ │  │ ...      ││
  │ └──────────┘ │  └──────────┘│
  │  [添加 →]    [← 移除]        │
  │      [保存配置(推送油猟端)]   │
  └──────────────┴──────────────┘

用法:
  panel = DualListPanel(parent, title="自动招募武将",
                         all_generals_data=generals,
                         list_name="auto_buy",
                         ops=ops, ops_push_method=ops.update_auto_buy_config,
                         log_func=self.log, height=10)
  panel.pack(fill="both", expand=True)
"""
import os
import re
import sys
import json
import threading
from core import silence as _SIL

try:
    from core.config import DATA_DIR
except ImportError:
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from core.config import DATA_DIR

# v3.54★ 批量导入纯数字 ID 时用它反查名字(往季武将/异格版本不在当前赛季池里,
#   光看 ID 用户根本认不出是谁)。失败不影响主流程。
try:
    from core import id_map as IDMAP
except Exception:
    IDMAP = None

try:
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
except ImportError:
    tk = None

# 颜色常量 (与 app.py 一致)
BG = "#2a2a2a"
FG = "#eeeeee"
DIM = "#999999"
GOLD = "#e0b060"
BTN = "#3a3a3a"
GREEN = "#5cb85c"
RED = "#ff5555"
BLUE = "#7fd4ff"
WARN = "#ffcc00"
SEL_BG = "#2a6db5"
SEL_FG = "#ffffff"
FH = "Microsoft YaHei"

# v3.54★ 批量文本解析用: 分隔符(逗号/顿号/分号/制表/换行, 中英文都认)
_BULK_SPLIT_RE = re.compile(r'[,，;；、\t\r\n]+')
# 一个 token 的三种形态: ① 名 (ID)  ② 纯 ID  ③ 纯名字
# 顺序有意为之 —— 先让 "曹操 (21001011)" 整体命中①, 避免被空格切成两半;
# ③ 的字符集排除了引号/括号/冒号等, 防止 JSON 解析失败时把语法符号当成武将名。
_BULK_TOKEN_RE = re.compile(
    r'([^\s,，;；、()（）\[\]{}"\'’‘“”:：]+?)\s*[（(](\d{4,})[)）]'
    r'|(\d{4,})'
    r'|([^\s,，;；、()（）\[\]{}"\'’‘“”:：]+)'
)
# "名 (ID)" 格式, 用于把整段拆开后判断某个 chunk 是不是本身就是完整条目
_BULK_PAIR_RE = re.compile(r'^(.+?)\s*[（(](\d{4,})[)）]$')

# v3.55★ 势力/费用筛选的"全部"哨兵值
_ALL_FACTION = "全部势力"
_ALL_RANK = "全部费用"

# lists 目录
_LISTS_DIR = os.path.join(DATA_DIR, "lists")
os.makedirs(_LISTS_DIR, exist_ok=True)


def _list_path(list_name):
    return os.path.join(_LISTS_DIR, f"{list_name}.json")


def _load_list_json(list_name):
    """从 json 加载已配置的武将列表, 返回 {ids:[], names:[]}

    v4.21★ 兼容两种历史格式:
      A. 新版(本文件 save_config 写的): {"ids": ["21001011", ...], "names": ["曹休", ...]}
      B. 旧版/导入格式: [{"chessID": "21001011", "name": "曹休", "display": "曹休 (21001011)"}, ...]
         —— data/lists/whitelist.json 实际就是这个格式(373 条)。
    旧代码只认 A 里的 "ids", 且遇到 list 直接把整个 list 当 ids 用 → ids 变成 [dict, dict, ...],
    于是 _load_config 里 str(item) 得到的是 "{'chessID': '21001011', ...}" 这种字符串,
    和 all_generals_data 里的 gen["id"](如 "21001011") 永不相等 → 右侧列表恒为空。
    后果就是: F6「协议买目标」拿到的白名单永远是 [] , 只会返回全 0。
    """
    path = _list_path(list_name)
    if not os.path.exists(path):
        return {"ids": [], "names": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return {"ids": data.get("ids", []), "names": data.get("names", [])}
            if isinstance(data, list):
                ids, names = [], []
                for it in data:
                    if isinstance(it, dict):
                        gid = it.get("id") or it.get("chessID") or it.get("goodsID") or ""
                        if not gid:
                            continue
                        ids.append(str(gid))
                        names.append(it.get("name") or it.get("display") or "")
                    elif it:
                        ids.append(str(it))
                        names.append("")
                return {"ids": ids, "names": names}
    except Exception:
        _SIL.note("ui/dual_list.py:125", "with open(path, 'r', encoding='utf-8') as f: d")
    return {"ids": [], "names": []}


def _save_list_json(list_name, ids, names=None):
    """保存已配置武将列表到 json"""
    path = _list_path(list_name)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"ids": ids, "names": names or []}, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


class DualListPanel(tk.Frame):
    """双列表组件: 左=全部武将(可搜索), 右=已配置武将

    参数:
        parent          父容器
        title           标题
        all_generals_data  全部武将数据 [{id, name, display}]
        list_name       配置名 (用于 json 文件名)
        ops             BridgeOps 实例
        ops_push_method 推送方法 (可选, 保存时调用)
        log_func        日志函数 (可选)
        height          listbox 高度
        show_manual     是否显示手动输入框
        left_title      左侧标题
        right_title     右侧标题
    """

    def __init__(self, parent, title="", all_generals_data=None, list_name="",
                 ops=None, ops_push_method=None, log_func=None, height=10,
                 show_manual=True, left_title="全部武将", right_title="已配置武将",
                 bg=BG, **kwargs):
        super().__init__(parent, bg=bg, **kwargs)
        self.all_generals_data = all_generals_data or []
        self.list_name = list_name
        self.ops = ops
        self.ops_push_method = ops_push_method
        self.log_func = log_func or print
        self._height = height
        self._show_manual = show_manual
        self._left_title = left_title
        self._right_title = right_title

        # 右侧已配置的 display 列表 (顺序保持)
        self._right_items = []  # [{id, name, display}]

        self._build_ui()
        self._load_config()

    def _log(self, msg, level="INFO"):
        try:
            self.log_func(msg, level=level)
        except (TypeError, AttributeError):
            try:
                self.log_func(msg)
            except Exception:
                print(msg)

    def _build_ui(self):
        # 标题
        if hasattr(self, 'master') and self._title_text():
            tk.Label(self, text=self._title_text(), bg=BG, fg=GOLD,
                     font=(FH, 10, "bold")).pack(anchor="w", pady=(0, 5))

        # 搜索框
        search_frame = tk.Frame(self, bg=BG)
        search_frame.pack(fill="x", pady=(0, 3))
        tk.Label(search_frame, text="搜索:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left", padx=(0, 2))
        self._search_var = tk.StringVar()
        self._search_var.trace("w", self._on_search)
        # v3.55★ 势力/费用 筛选: 配合「全选添加」可一键把整个势力/整个费用档全部录入。
        #   必须在这里定义好, 因为 _refresh_left / _add_all_left / _refresh_filters 都要读。
        self._faction_var = tk.StringVar(value=_ALL_FACTION)
        self._rank_var = tk.StringVar(value=_ALL_RANK)
        # 先 pack 右侧两个下拉, 再让 Entry 用 expand 吃掉中间剩余宽度
        self._rank_cb = ttk.Combobox(search_frame, textvariable=self._rank_var,
                                     values=[_ALL_RANK], width=4, state="readonly",
                                     font=(FH, 8))
        self._rank_cb.pack(side="right")
        self._rank_cb.bind("<<ComboboxSelected>>", lambda e: self._refresh_left())
        self._faction_cb = ttk.Combobox(search_frame, textvariable=self._faction_var,
                                        values=[_ALL_FACTION], width=6, state="readonly",
                                        font=(FH, 8))
        self._faction_cb.pack(side="right", padx=(0, 3))
        self._faction_cb.bind("<<ComboboxSelected>>", lambda e: self._refresh_left())
        tk.Entry(search_frame, textvariable=self._search_var, bg=BTN, fg=FG,
                 insertbackground=FG, font=(FH, 9)).pack(side="left", fill="x", expand=True)

        # 双列表区
        lists_frame = tk.Frame(self, bg=BG)
        lists_frame.pack(fill="both", expand=True, pady=(0, 5))

        # 左侧: 全部武将
        left_frame = tk.LabelFrame(lists_frame, text=self._left_title, bg=BG, fg=FG,
                                   bd=1, relief="solid", font=(FH, 9, "bold"))
        left_frame.pack(side="left", fill="both", expand=True, padx=(0, 3))
        left_list_frame = tk.Frame(left_frame, bg=BG)
        left_list_frame.pack(fill="both", expand=True, padx=2, pady=2)
        self._left_listbox = tk.Listbox(left_list_frame, selectmode=tk.MULTIPLE,
                                         bg=BTN, fg=FG, selectbackground=SEL_BG,
                                         selectforeground=SEL_FG, font=(FH, 9),
                                         height=self._height, exportselection=False)
        self._left_listbox.pack(side="left", fill="both", expand=True)
        left_sb = tk.Scrollbar(left_list_frame, orient="vertical",
                               command=self._left_listbox.yview)
        left_sb.pack(side="right", fill="y")
        self._left_listbox.config(yscrollcommand=left_sb.set)

        # 中间按钮
        btn_frame = tk.Frame(lists_frame, bg=BG)
        btn_frame.pack(side="left", padx=3)
        tk.Button(btn_frame, text="添加 →", command=self._add_selected, bg=BTN, fg=FG,
                  font=(FH, 9, "bold"), padx=8, cursor="hand2").pack(pady=(0, 3))
        tk.Button(btn_frame, text="← 移除", command=self._remove_selected, bg=BTN, fg=FG,
                  font=(FH, 9, "bold"), padx=8, cursor="hand2").pack(pady=3)
        tk.Button(btn_frame, text="清空右侧", command=self._clear_right, bg=BTN, fg=RED,
                  font=(FH, 8), padx=4, cursor="hand2").pack(pady=(3, 0))
        # v3.54★ 全选添加: 配合搜索框用 —— 搜"蜀"→全选添加, 比一条条点快得多
        tk.Button(btn_frame, text="全选添加 →", command=self._add_all_left, bg=BTN, fg=BLUE,
                  font=(FH, 8), padx=4, cursor="hand2").pack(pady=(3, 0))

        # 右侧: 已配置武将
        right_frame = tk.LabelFrame(lists_frame, text=self._right_title, bg=BG, fg=FG,
                                     bd=1, relief="solid", font=(FH, 9, "bold"))
        right_frame.pack(side="left", fill="both", expand=True, padx=(3, 0))
        right_list_frame = tk.Frame(right_frame, bg=BG)
        right_list_frame.pack(fill="both", expand=True, padx=2, pady=2)
        self._right_listbox = tk.Listbox(right_list_frame, selectmode=tk.MULTIPLE,
                                          bg=BTN, fg=FG, selectbackground=SEL_BG,
                                          selectforeground=SEL_FG, font=(FH, 9),
                                          height=self._height, exportselection=False)
        self._right_listbox.pack(side="left", fill="both", expand=True)
        right_sb = tk.Scrollbar(right_list_frame, orient="vertical",
                                command=self._right_listbox.yview)
        right_sb.pack(side="right", fill="y")
        self._right_listbox.config(yscrollcommand=right_sb.set)

        # 手动输入框 (可选)
        if self._show_manual:
            manual_frame = tk.Frame(self, bg=BG)
            manual_frame.pack(fill="x", pady=(0, 3))
            tk.Label(manual_frame, text="手动添加(逗号分隔):", bg=BG, fg=FG,
                     font=(FH, 9)).pack(side="left", padx=(0, 3))
            self._manual_var = tk.StringVar()
            tk.Entry(manual_frame, textvariable=self._manual_var, bg=BTN, fg=FG,
                     insertbackground=FG, font=(FH, 9)).pack(side="left", fill="x", expand=True)
            tk.Button(manual_frame, text="添加", command=self._add_manual, bg=BTN, fg=FG,
                      font=(FH, 9), padx=4, cursor="hand2").pack(side="left", padx=(3, 0))

        # 底部保存按钮
        bottom_frame = tk.Frame(self, bg=BG)
        bottom_frame.pack(fill="x", pady=(3, 0))
        self._count_label = tk.Label(bottom_frame, text="已配置: 0 个", bg=BG, fg=DIM,
                                     font=(FH, 9))
        self._count_label.pack(side="left")
        tk.Button(bottom_frame, text="保存配置 (推送油猟端)", command=self.save_config,
                  bg=GREEN, fg=FG, font=(FH, 9, "bold"), padx=8, cursor="hand2").pack(side="right")
        # v3.54★ 批量三件套: 粘贴 / 文件导入 / 导出 (几百条名单靠手点不现实)
        tk.Button(bottom_frame, text="导出", command=self._export_to_file, bg=BTN, fg=BLUE,
                  font=(FH, 9), padx=6, cursor="hand2").pack(side="right", padx=(0, 3))
        tk.Button(bottom_frame, text="导入文件", command=self._import_from_file, bg=BTN, fg=BLUE,
                  font=(FH, 9), padx=6, cursor="hand2").pack(side="right", padx=(0, 3))
        tk.Button(bottom_frame, text="批量粘贴", command=self._open_bulk_paste_dialog, bg=BTN, fg=GOLD,
                  font=(FH, 9, "bold"), padx=6, cursor="hand2").pack(side="right", padx=(0, 3))

        # 填充左侧列表 (必须先建筛选选项, 否则下拉框是空的)
        self._refresh_filters()
        self._refresh_left()

    def _title_text(self):
        """子类可覆盖"""
        return ""

    # ---------- 左侧列表 ----------
    def _refresh_filters(self):
        """按当前武将池重建「势力 / 费用」下拉选项。

        v3.55★ 选项从数据里动态收集而非硬编码 —— 赛季更新、武将表变化后
        下拉框自动跟着变, 不会出现"选了某势力结果永远是空列表"。
        """
        if not hasattr(self, '_faction_cb'):
            return
        facs, rnks = set(), set()
        for gen in self.all_generals_data:
            f = str(gen.get("faction") or "").strip()
            if f:
                facs.add(f)
            r = gen.get("rank")
            if r not in (None, ""):
                rs = str(r).strip()
                if rs.isdigit():
                    rnks.add(int(rs))
        f_vals = [_ALL_FACTION] + sorted(facs)
        r_vals = [_ALL_RANK] + [str(r) for r in sorted(rnks)]
        self._faction_cb["values"] = f_vals
        self._rank_cb["values"] = r_vals
        # 旧选项在新数据里已不存在时必须复位, 否则列表会一直是空的
        if self._faction_var.get() not in f_vals:
            self._faction_var.set(_ALL_FACTION)
        if self._rank_var.get() not in r_vals:
            self._rank_var.set(_ALL_RANK)

    def _refresh_left(self):
        """刷新左侧列表: 排除已在右侧的, 并应用 搜索 / 势力 / 费用 三重筛选"""
        self._left_listbox.delete(0, tk.END)
        right_ids = set(item["id"] for item in self._right_items)
        search = self._search_var.get().lower() if hasattr(self, '_search_var') else ""
        fac = self._faction_var.get() if hasattr(self, '_faction_var') else _ALL_FACTION
        rnk = self._rank_var.get() if hasattr(self, '_rank_var') else _ALL_RANK
        # v4.101★ 只画前 400 条: 武将表上千条, 全量 insert 就是上千次 Tk 行操作 —— 而
        #   「重新获取数据」要连着重建【7 个】这样的面板(上万人次), 界面僵 1~3 秒; 人眼一次
        #   也就看几十条。多出来的给一行提示, 想找谁用搜索框(比滚长列表快得多)。
        shown = 0
        for gen in self.all_generals_data:
            gid = str(gen.get("id", ""))
            if gid in right_ids:
                continue
            if fac != _ALL_FACTION and str(gen.get("faction") or "") != fac:
                continue
            if rnk != _ALL_RANK and str(gen.get("rank") or "") != rnk:
                continue
            display = gen.get("display", gen.get("name", ""))
            if search and search not in display.lower():
                continue
            if shown >= 400:
                break
            self._left_listbox.insert(tk.END, display)
            shown += 1
        if shown >= 400:
            self._left_listbox.insert(tk.END, "… 还有更多未显示, 用上面的搜索框缩小范围")

    def _on_search(self, *args):
        # v4.101★ 防抖: 搜索框绑的是 trace("w") —— 【每敲一个字符】就重建一次整个列表
        #   (上千条), 打字会明显一顿一顿。改成停手 250ms 才真的重建一次。
        try:
            if getattr(self, "_search_job", None):
                self._left_listbox.after_cancel(self._search_job)
        except Exception as e:
            sys.stderr.write("[列表] 取消上一次搜索刷新失败: %s\n" % e)
        try:
            self._search_job = self._left_listbox.after(250, self._refresh_left)
        except Exception as e:
            sys.stderr.write("[列表] 排搜索刷新失败: %s\n" % e)
            self._refresh_left()

    # ---------- 右侧列表 ----------
    def _refresh_right(self):
        self._right_listbox.delete(0, tk.END)
        for item in self._right_items:
            self._right_listbox.insert(tk.END, item["display"])
        self._count_label.config(text=f"已配置: {len(self._right_items)} 个")

    def _add_selected(self):
        """将左侧选中的武将添加到右侧"""
        selected = self._left_listbox.curselection()
        if not selected:
            return
        for idx in selected:
            display = self._left_listbox.get(idx)
            # 从 all_generals_data 找到对应数据
            gen = self._find_gen_by_display(display)
            if gen:
                gid = str(gen.get("id", ""))
                # 避免重复
                if not any(r["id"] == gid for r in self._right_items):
                    self._right_items.append({
                        "id": gid,
                        "name": gen.get("name", ""),
                        "display": gen.get("display", gen.get("name", "")),
                    })
        self._refresh_left()
        self._refresh_right()

    def _remove_selected(self):
        """将右侧选中的武将移除"""
        selected = list(self._right_listbox.curselection())
        if not selected:
            return
        # 从后往前删, 避免 index 错位
        for idx in sorted(selected, reverse=True):
            if 0 <= idx < len(self._right_items):
                del self._right_items[idx]
        self._refresh_left()
        self._refresh_right()

    def _clear_right(self):
        """清空右侧"""
        self._right_items = []
        self._refresh_left()
        self._refresh_right()

    def _add_manual(self):
        """手动添加: 输入武将名, 匹配 all_generals_data"""
        text = self._manual_var.get().strip()
        if not text:
            return
        added = 0
        for name in text.split(","):
            name = name.strip()
            if not name:
                continue
            gen = self._find_gen_by_name_or_id(name)
            if gen:
                gid = str(gen.get("id", ""))
                if not any(r["id"] == gid for r in self._right_items):
                    self._right_items.append({
                        "id": gid,
                        "name": gen.get("name", ""),
                        "display": gen.get("display", gen.get("name", "")),
                    })
                    added += 1
                else:
                    self._log(f"[{self.list_name}] {name} 已在列表中", level="WARNING")
            elif name.isdigit():
                # v4.42★ 允许直接填一个"全部武将"里没有的纯数字 ID。
                #   为什么必须允许: 各面板右侧列表用的 ID 空间并不都是"棋子ID"。
                #   随征(followup)用的是【技能ID】(ChessSkillConf.SkillID), 例如
                #   942311 = 黄巾兵的随征技能 —— 它在 ChessConf.csv / 白名单 /
                #   tavern_chess.json 里【全都没有】, 因为它是技能不是棋子。
                #   以前这里一律回"未找到武将", 于是"随征ID里的黄巾兵"根本加不进去。
                #   现在: 反查不到名字就退化成 ID 显示, 但【能加进去、能保存、能推送】——
                #   与 _load_config(v4.21) 那句"面板里看到的 = 真正传给页面端的"保持一致。
                if not any(r["id"] == name for r in self._right_items):
                    self._right_items.append({"id": name, "name": name, "display": f"ID {name}"})
                    added += 1
                    self._log(f"[{self.list_name}] {name} 不在武将表里, 已按原始ID加入"
                              f"(随征/技能类 ID 属于正常情况)")
                else:
                    self._log(f"[{self.list_name}] {name} 已在列表中", level="WARNING")
            else:
                self._log(f"[{self.list_name}] 未找到武将: {name}", level="WARNING")
        if added > 0:
            self._manual_var.set("")
            self._refresh_left()
            self._refresh_right()
            self._log(f"[{self.list_name}] 手动添加了 {added} 个武将")

    def _find_gen_by_display(self, display):
        """通过 display 字符串找武将数据"""
        for gen in self.all_generals_data:
            if gen.get("display", gen.get("name", "")) == display:
                return gen
        return None

    def _find_gen_by_name_or_id(self, text):
        """通过名字或ID找武将数据, 支持 "名 (ID)" 格式"""
        text = text.strip()
        # 先尝试从 "名 (ID)" 格式提取ID
        m = re.search(r'\((\d+)\)\s*$', text)
        if m:
            gid = m.group(1)
            for gen in self.all_generals_data:
                if str(gen.get("id", "")) == gid:
                    return gen
        # 再尝试用 ops.get_general_id_by_name
        if self.ops and hasattr(self.ops, 'get_general_id_by_name'):
            gid = self.ops.get_general_id_by_name(text)
            if gid:
                gid_str = str(gid)
                for gen in self.all_generals_data:
                    if str(gen.get("id", "")) == gid_str:
                        return gen
        # 最后尝试名字匹配
        text_lower = text.lower()
        for gen in self.all_generals_data:
            if gen.get("name", "").lower() == text_lower:
                return gen
        return None

    # ---------- 保存/加载 ----------
    def save_config(self):
        """保存配置: 1.保存到本地json 2.推送到油猟端"""
        ids = [item["id"] for item in self._right_items]
        names = [item["name"] for item in self._right_items]

        # 1. 保存到本地 json
        ok = _save_list_json(self.list_name, ids, names)
        if ok:
            self._log(f"[{self.list_name}] 配置已保存: {len(ids)} 个武将 → {self.list_name}.json")
        else:
            self._log(f"[{self.list_name}] 保存失败", level="ERROR")

        # 2. 推送到油猟端
        if self.ops_push_method:
            try:
                self.ops_push_method(ids)
            except Exception as e:
                self._log(f"[{self.list_name}] 推送油猟端失败: {e}", level="ERROR")

        return ids

    def _load_config(self):
        """从本地 json 加载已配置的武将列表"""
        data = _load_list_json(self.list_name)
        saved_ids = data.get("ids", [])
        saved_names = data.get("names", [])
        if not saved_ids:
            return
        # v4.21★ 关键修复: 旧实现是「拿 all_generals_data(当前赛季武将池)去反查 id_set」,
        #   凡是池子里没有的 ID 就被直接丢弃。实测白名单里 158 个 ID 只有 78 个能命中池子,
        #   剩下 80 个(多为往季/异格版本 chessID)静默消失 —— 面板看不到, F6 也拿不到,
        #   表现就是"我明明配了它却不买"。
        #   现在改为【按保存顺序遍历, 匹配不到也保留】(名字退化成 ID),
        #   保证「面板里看到的 = 真正传给 F6 的」。
        gen_map = {}
        for gen in self.all_generals_data:
            gen_map[str(gen.get("id", ""))] = gen
        seen = set()
        for idx, raw_id in enumerate(saved_ids):
            gid = str(raw_id or "").strip()
            if not gid or gid in seen:
                continue
            seen.add(gid)
            gen = gen_map.get(gid)
            if gen:
                self._right_items.append({
                    "id": gid,
                    "name": gen.get("name", ""),
                    "display": gen.get("display", gen.get("name", "")),
                })
            else:
                nm = saved_names[idx] if idx < len(saved_names) else ""
                self._right_items.append({
                    "id": gid,
                    "name": nm or gid,
                    "display": f"{nm or gid} ({gid})",
                })
        self._refresh_left()
        self._refresh_right()
        self._log(f"[{self.list_name}] 已加载 {len(self._right_items)} 个保存的配置")

     # ---------- 批量操作 (v3.54★) ----------
    #   背景: 白名单/黑名单动辄几百条, 靠左侧列表一条条点"添加→"不现实。
    #   这里提供三个入口, 全部走同一套解析 _parse_bulk():
    #     · 批量粘贴   —— 弹窗里直接 Ctrl+V 一大段名单
    #     · 导入文件   —— 读 txt / json / csv (含本项目 data/lists/whitelist.json 那种格式)
    #     · 全选添加   —— 左侧(配合搜索框)一次全加
    def _entry_from_text(self, token, by_id=None, by_name=None):
        """把一段文本解析成 {id,name,display}; 无法识别返回 None。

        接受三种形态: 纯名字 / 纯数字ID / "名 (ID)"。
        传了 by_id/by_name 索引就走索引 —— 批量导入几千条时是 O(1),
        否则每个 token 都要遍历一遍武将池, 会明显卡顿。
        """
        token = (token or "").strip().strip('"\'')
        if not token:
            return None

        # ① "名 (ID)" 组合格式: 名字和 ID 都拿到了, 直接用
        m = _BULK_PAIR_RE.match(token)
        if m:
            name_part, id_part = m.group(1).strip(), m.group(2)
            gen = by_id.get(id_part) if by_id else None
            if gen:
                return {"id": id_part, "name": gen.get("name", ""),
                        "display": gen.get("display", gen.get("name", ""))}
            return {"id": id_part, "name": name_part, "display": f"{name_part} ({id_part})"}

        # ② 纯数字 ID
        if re.fullmatch(r'\d{4,}', token):
            gen = by_id.get(token) if by_id else None
            if gen:
                return {"id": token, "name": gen.get("name", ""),
                        "display": gen.get("display", gen.get("name", ""))}
            # 当前赛季池里找不到: 多半是往季武将 / 异格版本 chessID。
            # 这类 ID 依然有效(商店里刷出来就能匹配), 所以保留,
            # 但用 id_map 反查个名字, 免得列表里全是光秃秃的数字。
            nm = ""
            if IDMAP is not None:
                try:
                    nm = IDMAP.name_of(int(token), chess_hint=True) or ""
                except Exception:
                    nm = ""
            return {"id": token, "name": nm or token,
                    "display": f"{nm} ({token})" if nm else f"{token} ({token})"}

        # ③ 纯名字
        if by_name is not None:
            gen = by_name.get(token) or by_name.get(token.lower())
            if not gen:
                return None
            return {"id": str(gen.get("id", "")), "name": gen.get("name", ""),
                    "display": gen.get("display", gen.get("name", ""))}
        gen = self._find_gen_by_name_or_id(token)
        if gen:
            return {"id": str(gen.get("id", "")), "name": gen.get("name", ""),
                    "display": gen.get("display", gen.get("name", ""))}
        return None

    def _parse_bulk(self, text):
        """把一大段文本解析成条目列表, 返回 (entries, unknown)。

        支持:
          1) JSON 数组 —— ["曹操","21001011"] 或 [{"chessID":..,"name":..}]
             (本项目 data/lists/whitelist.json 就是后者, 可直接导入)
          2) 自由文本 —— 名字 / ID / "名 (ID)",
             分隔符: 换行 逗号 顿号 分号 制表符 空格 都认;
             行内 # 之后视为注释。
        """
        text = (text or "").strip()
        if not text:
            return [], []

        tokens = []
        # JSON 优先(只在看起来像 JSON 时尝试, 免得把普通文本误判)
        if text[:1] in "[{":
            try:
                data = json.loads(text)
            except Exception:
                data = None
            if isinstance(data, list):
                for it in data:
                    if isinstance(it, dict):
                        gid = it.get("id") or it.get("chessID") or it.get("goodsID")
                        nm = it.get("name") or it.get("display") or ""
                        if gid:
                            tokens.append(str(gid))
                        elif nm:
                            tokens.append(str(nm))
                    elif it not in (None, ""):
                        tokens.append(str(it))
            elif isinstance(data, dict):
                gid = data.get("id") or data.get("chessID") or data.get("goodsID")
                if gid:
                    tokens.append(str(gid))

        if not tokens:
            for line in text.splitlines():
                line = line.split("#", 1)[0]
                tokens.extend(p.strip() for p in _BULK_SPLIT_RE.split(line) if p.strip())

        # 建索引, 避免每个 token 都去遍历武将池
        by_id, by_name = {}, {}
        for gen in self.all_generals_data:
            gid = str(gen.get("id", ""))
            if gid:
                by_id.setdefault(gid, gen)
            nm = str(gen.get("name", "")).strip()
            if nm:
                by_name.setdefault(nm, gen)
                by_name.setdefault(nm.lower(), gen)

        entries, unknown, seen = [], [], set()
        for chunk in tokens:
            # 一个 chunk 里可能还夹着空格(如 "曹操 (21001011) 刘备"),
            # 所以用 findall 逐段提取, 而不是简单 split —— 否则 "曹操 (21001011)"
            # 会被空格切成 "曹操" 和 "(21001011)" 两个残片。
            hits = _BULK_TOKEN_RE.findall(chunk) or [("", "", "", chunk)]
            for g1, g2, g3, g4 in hits:
                if g1 and g2:
                    tok = f"{g1} ({g2})"
                elif g3:
                    tok = g3
                else:
                    tok = g4
                tok = (tok or "").strip()
                if not tok:
                    continue
                ent = self._entry_from_text(tok, by_id, by_name)
                if not ent:
                    unknown.append(tok)
                    continue
                if ent["id"] in seen:
                    continue
                seen.add(ent["id"])
                entries.append(ent)
        return entries, unknown

    def _bulk_add(self, entries):
        """把一批 {id,name,display} 合并进右侧列表, 返回 (新增数, 重复数)。"""
        existing = set(item["id"] for item in self._right_items)
        added = dup = 0
        for ent in entries:
            gid = ent.get("id")
            if not gid or gid in existing:
                dup += 1
                continue
            existing.add(gid)
            self._right_items.append(ent)
            added += 1
        if added:
            # 只在最后刷一次 UI —— 几百条逐个刷新会卡死
            self._refresh_left()
            self._refresh_right()
        return added, dup

    def _add_all_left(self):
        """把左侧当前显示(受搜索框过滤)的武将全部加入右侧。

        典型用法: 搜索框打"蜀" → 全选添加 → 整族一次进白名单。
        """
        items = list(self._left_listbox.get(0, tk.END))
        if not items:
            messagebox.showinfo("提示", "左侧没有可添加的武将。")
            return
        cond = []
        if self._faction_var.get() != _ALL_FACTION:
            cond.append(f"势力={self._faction_var.get()}")
        if self._rank_var.get() != _ALL_RANK:
            cond.append(f"费用={self._rank_var.get()}")
        if self._search_var.get().strip():
            cond.append(f"搜索={self._search_var.get().strip()}")
        tip = ("筛选条件: " + ", ".join(cond)) if cond else "未设任何筛选, 即全部武将"
        if len(items) > 80 and not messagebox.askyesno(
                "确认全选添加",
                f"将左侧 {len(items)} 个武将全部加入「{self._right_title}」吗?\n{tip}"):
            return
        entries = []
        for display in items:
            gen = self._find_gen_by_display(display)
            if gen:
                entries.append({
                    "id": str(gen.get("id", "")),
                    "name": gen.get("name", ""),
                    "display": gen.get("display", gen.get("name", "")),
                })
        added, dup = self._bulk_add(entries)
        self._log(f"[{self.list_name}] 全选添加 {added} 个 (重复 {dup})")

    def _fill_text_from_file(self, txt_widget, status_label):
        """把文件内容读进弹窗文本框(不直接导入, 让用户先过目/删改)。"""
        path = filedialog.askopenfilename(
            title="选择名单文件",
            filetypes=[("文本 / JSON / CSV", "*.txt *.json *.csv"), ("所有文件", "*.*")])
        if not path:
            return
        raw = None
        for enc in ("utf-8-sig", "gbk", "utf-8"):
            try:
                with open(path, "r", encoding=enc) as f:
                    raw = f.read()
                break
            except UnicodeDecodeError:
                continue
            except Exception as e:
                status_label.config(text=f"读取失败: {e}", fg=RED)
                return
        if raw is None:
            status_label.config(text="读取失败: 编码无法识别(试过 utf-8/gbk)", fg=RED)
            return
        txt_widget.delete("1.0", tk.END)
        txt_widget.insert("1.0", raw)
        status_label.config(
            text=f"已读入 {os.path.basename(path)} ({len(raw)} 字符), 确认后点『确定添加』", fg=BLUE)

    def _open_bulk_paste_dialog(self):
        """批量粘贴对话框: 一次性录入大量武将。"""
        top = tk.Toplevel(self.winfo_toplevel())
        top.title(f"批量添加 — {self._right_title}")
        top.configure(bg=BG)
        top.geometry("560x460")
        top.transient(self.winfo_toplevel())

        tk.Label(top, text="把名单粘进来 (Ctrl+V), 一行一个, 或用 逗号/空格 分隔:",
                 bg=BG, fg=FG, font=(FH, 9)).pack(anchor="w", padx=10, pady=(10, 1))
        tk.Label(top, text="支持  曹操    21001011    曹操 (21001011)    以及整个 JSON 数组",
                 bg=BG, fg=DIM, font=(FH, 8)).pack(anchor="w", padx=10)

        text_frame = tk.Frame(top, bg=BG)
        text_frame.pack(fill="both", expand=True, padx=10, pady=(6, 4))
        txt = tk.Text(text_frame, bg="#1e1e1e", fg=FG, insertbackground=FG,
                      font=(FH, 9), wrap="none", undo=True)
        txt.pack(side="left", fill="both", expand=True)
        sb = tk.Scrollbar(text_frame, orient="vertical", command=txt.yview)
        sb.pack(side="right", fill="y")
        txt.configure(yscrollcommand=sb.set)
        txt.focus_set()

        status = tk.Label(top, text="", bg=BG, fg=DIM, font=(FH, 9), justify="left",
                          wraplength=530, anchor="w")
        status.pack(fill="x", padx=10)

        def _do_add():
            raw = txt.get("1.0", tk.END)
            entries, unknown = self._parse_bulk(raw)
            if not entries:
                status.config(text=f"没解析出可识别的武将。未识别 {len(unknown)} 项: "
                                   f"{', '.join(unknown[:8])}", fg=RED)
                return
            added, dup = self._bulk_add(entries)
            msg = f"新增 {added} 个, 重复跳过 {dup} 个"
            if unknown:
                tail = ", ".join(unknown[:6]) + ("..." if len(unknown) > 6 else "")
                msg += f"\n未识别 {len(unknown)} 项: {tail}"
            status.config(text=msg, fg=WARN if unknown else GREEN)
            self._log(f"[{self.list_name}] 批量添加 {added} 个 (重复 {dup}, 未识别 {len(unknown)})")
            if added:
                txt.delete("1.0", tk.END)   # 成功的清掉, 只留没识别上的让用户改

        btn_bar = tk.Frame(top, bg=BG)
        btn_bar.pack(fill="x", padx=10, pady=(2, 10))
        tk.Button(btn_bar, text="确定添加", command=_do_add, bg=GREEN, fg=FG,
                  font=(FH, 9, "bold"), padx=10, cursor="hand2").pack(side="right")
        tk.Button(btn_bar, text="关闭", command=top.destroy, bg=BTN, fg=FG,
                  font=(FH, 9), padx=10, cursor="hand2").pack(side="right", padx=(0, 4))
        tk.Button(btn_bar, text="从文件读取…",
                  command=lambda: self._fill_text_from_file(txt, status),
                  bg=BTN, fg=BLUE, font=(FH, 9), padx=8, cursor="hand2").pack(side="left")
        tk.Label(btn_bar, text="(记得最后点『保存配置』才写入生效)", bg=BG, fg=DIM,
                 font=(FH, 8)).pack(side="left", padx=(8, 0))

    def _import_from_file(self):
        """从 txt/json/csv 批量导入(默认合并, 可选清空后导入)。"""
        path = filedialog.askopenfilename(
            title=f"导入到 {self._right_title}",
            filetypes=[("文本 / JSON / CSV", "*.txt *.json *.csv"), ("所有文件", "*.*")])
        if not path:
            return
        raw = None
        for enc in ("utf-8-sig", "gbk", "utf-8"):
            try:
                with open(path, "r", encoding=enc) as f:
                    raw = f.read()
                break
            except UnicodeDecodeError:
                continue
            except Exception as e:
                messagebox.showerror("导入失败", f"读取文件失败: {e}")
                return
        if raw is None:
            messagebox.showerror("导入失败", "编码无法识别(试过 utf-8/gbk)")
            return

        entries, unknown = self._parse_bulk(raw)
        if not entries:
            messagebox.showwarning(
                "导入", f"没解析出可识别的武将。\n未识别 {len(unknown)} 项: "
                       f"{', '.join(unknown[:8])}")
            return

        if self._right_items:
            keep = messagebox.askyesno(
                "导入方式",
                f"从 {os.path.basename(path)} 解析到 {len(entries)} 个武将。\n\n"
                f"【是】合并到现有 {len(self._right_items)} 个\n"
                f"【否】先清空右侧再导入")
            if not keep:
                self._right_items = []
        added, dup = self._bulk_add(entries)
        self._log(f"[{self.list_name}] 从 {os.path.basename(path)} 导入 {added} 个 "
                  f"(重复 {dup}, 未识别 {len(unknown)})")
        messagebox.showinfo(
            "导入完成",
            f"新增 {added} 个, 重复跳过 {dup} 个"
            + (f"\n未识别 {len(unknown)} 项" if unknown else "")
            + "\n\n记得点『保存配置 (推送油猟端)』才正式生效。")

    def _export_to_file(self):
        """导出右侧列表。JSON 可原样再导入; TXT 便于人看; CSV 便于 Excel 编辑。"""
        if not self._right_items:
            messagebox.showinfo("导出", "当前列表为空, 没有可导出的内容。")
            return
        path = filedialog.asksaveasfilename(
            title=f"导出 {self._right_title}",
            defaultextension=".json",
            initialfile=f"{self.list_name}.json",
            filetypes=[("JSON (可再次导入)", "*.json"),
                       ("纯文本 (每行一个)", "*.txt"),
                       ("CSV (Excel)", "*.csv")])
        if not path:
            return
        try:
            low = path.lower()
            if low.endswith(".txt"):
                with open(path, "w", encoding="utf-8") as f:
                    f.write("\n".join(item["display"] for item in self._right_items))
            elif low.endswith(".csv"):
                import csv
                with open(path, "w", encoding="utf-8-sig", newline="") as f:
                    w = csv.writer(f)
                    w.writerow(["chessID", "name", "display"])
                    for item in self._right_items:
                        w.writerow([item["id"], item["name"], item["display"]])
            else:
                with open(path, "w", encoding="utf-8") as f:
                    # 与 data/lists/*.json 既有格式保持一致, 保证能被 _load_list_json / 再次导入识别
                    json.dump([{"chessID": it["id"], "name": it["name"], "display": it["display"]}
                               for it in self._right_items],
                              f, ensure_ascii=False, indent=2)
        except Exception as e:
            messagebox.showerror("导出失败", str(e))
            return
        self._log(f"[{self.list_name}] 已导出 {len(self._right_items)} 个 → {os.path.basename(path)}")
        messagebox.showinfo("导出完成", f"{len(self._right_items)} 个武将已写入:\n{path}")

    # ---------- 外部接口 ----------
    def get_selected_ids(self):
        """获取右侧已配置的武将ID列表"""
        return [item["id"] for item in self._right_items]

    def get_selected_entries(self):
        """v4.55★ 获取右侧已配置的完整条目 [{id,name,display}, ...]。

        为什么需要它: 有些调用方除了 ID 还要【名字】—— 典型是「多选购买」,
        它得按名字把该武将的全部赛季 ID 展开(见 app._expand_list_ids),
        否则只拿到一个 ID, 商店刷出同武将的另一个 ID 就匹配不上(漏买)。
        """
        return [dict(item) for item in self._right_items]

    def get_selected_displays(self):
        """获取右侧已配置的武将display列表"""
        return [item["display"] for item in self._right_items]

    def add_entries(self, entries):
        """外部往右侧列表灌一批 {id,name,display}(不自动保存/推送), 返回 (新增数, 重复数)。

        v4.43★ 给『技能库』tab 用: 随征卡牌ID 填的其实是 ChessSkillConf.SkillID(如 942311=黄巾兵),
        而左侧『全部武将』只有棋子, 搜不到技能 —— 以前只能打开『手动添加』把 ID 一个个敲进去。
        现在在技能库里选中/多选直接灌进来。要落盘+推油猴, 调用方接着调 save_config()。
        """
        return self._bulk_add(entries or [])

    def refresh_data(self, all_generals_data):
        """刷新全部武将数据 (数据管理tab刷新后调用)"""
        self.all_generals_data = all_generals_data or []
        self._refresh_filters()
        self._refresh_left()

    def reload_from_disk(self):
        """v4.47★ 预设导入后用: 丢掉内存里那份右列表, 重新从 data/lists/*.json 读一遍。

        为什么需要: refresh_data() 只换左侧『全部武将』的池子, 右侧『已配置』是启动时
        _load_config() 读进内存的 —— 导入别人的预设后如果不重读, 面板还显示旧内容,
        用户会以为导入没生效(甚至再点一次保存, 把刚导入的又覆盖回去)。
        """
        self._right_items = []
        self._load_config()          # 有内容时它自己会 _refresh_left/_refresh_right
        if not self._right_items:    # 导入的这份列表本来就是空的 → 得手工清一次界面
            self._refresh_left()
            self._refresh_right()

    def replace_entries(self, entries):
        """替换右侧全部内容 (v4.153★ 快速预设用): 清空 + 灌入 + 刷新, 返回 (新增数, 重复数)。

        为什么单独给一个方法而不是让外界调 _clear_right + add_entries: 那是两步,
        中间会各自刷一次 UI —— 预设动辄几十上百条, 刷到一半卡一下倒还好, 一旦中间
        抛异常就停在"清了旧的、没加新的", 用户手里的名单直接没了。这里是一次成型。
        """
        self._right_items = []
        added, dup = self._bulk_add(entries or [])
        if not added:
            self._refresh_left()
            self._refresh_right()
        return added, dup

    def parse_text(self, text):
        """把一段文本解析成条目 (v4.153★ 预设名单走的就是同一套解析, 见 _parse_bulk)。"""
        return self._parse_bulk(text)

    def clear_right(self):
        """清空右侧列表(外部入口)"""
        self._clear_right()

    def set_ops(self, ops):
        """设置 ops 实例 (延迟绑定)"""
        self.ops = ops

    def set_push_method(self, method):
        """设置推送方法"""
        self.ops_push_method = method
