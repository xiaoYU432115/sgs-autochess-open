# -*- coding: utf-8 -*-
"""三国杀自走棋助手 - 专用控件集

HeroPickWidget: 武将筛选/搜索/双列表(白名单+黑名单)
"""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import json
import os
from collections import OrderedDict
from core import silence as _SIL

# v4.00★ 导入 ID 映射库用于技能描述查找
try:
    from core import id_map as IDMAP
except ImportError:
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from core import id_map as IDMAP

# v3.99★ UI 样式常量 (同步自 app.py)
BG = "#141419"; FG = "#8ef58e"; DIM = "#666"; BTN = "#23232c"
WARN = "#ffb347"; GOLD = "#ffd700"; BLUE = "#7fd4ff"; RED = "#ff6b6b"
FH = "Microsoft YaHei UI"


def open_lord_skill_picker(parent, on_pick, season=None):
    """『从技能库选主公技能』挑选框: 查本季 ChessGeneralSkillConf(lordskill), 双击/确定把技能ID填回去。

    数据走 tools.season_config.view_skills(优先读赛季快照, 没快照退回 data/lord_skills.json);
    首次读十几 MB 快照放子线程, 不卡 UI。on_pick(skill_id:str) 在选择后被调用,
    由调用方决定填到哪个输入框(剧本步骤 / 主公技能面板共用)。
    """
    import threading as _th
    try:
        from tools import season_config as SC
    except Exception:
        SC = None

    d2 = tk.Toplevel(parent)
    d2.title("从技能库选主公技能")
    d2.configure(bg=BG)
    try:
        d2.transient(parent)
    except Exception:
        pass
    tk.Label(d2, text="搜索(技能ID / 名字 / 说明) · 双击或点『确定』填ID",
             bg=BG, fg=DIM, font=(FH, 8)).pack(anchor="w", padx=10, pady=(8, 0))
    sv = tk.StringVar()
    ent = tk.Entry(d2, textvariable=sv, bg=BTN, fg=FG, insertbackground=FG, font=(FH, 9))
    ent.pack(fill="x", padx=10, pady=(2, 0))
    ent.focus_set()
    top = tk.Frame(d2, bg=BG)
    top.pack(fill="x", padx=10, pady=(4, 0))
    cnt_var = tk.StringVar(value="读取中 ...")
    tk.Label(top, textvariable=cnt_var, bg=BG, fg=DIM, font=(FH, 8)).pack(side="left")
    wrap = tk.Frame(d2, bg=BG)
    wrap.pack(fill="both", expand=True, padx=10, pady=6)
    sb = tk.Scrollbar(wrap)
    sb.pack(side="right", fill="y")
    lb = tk.Listbox(wrap, bg=BTN, fg=FG, font=(FH, 9), height=16,
                    activestyle="none", exportselection=False, yscrollcommand=sb.set)
    sb.configure(command=lb.yview)
    lb.pack(side="left", fill="both", expand=True)

    rows = []
    shown_ids = []

    def _populate(data):
        rows[:] = data or []
        _fill()

    def _fill(*_a):
        kw = sv.get().strip()
        lb.delete(0, "end")
        del shown_ids[:]
        for r in rows:
            name = str(r.get("name") or "")
            sid = str(r.get("id") or "")
            desc = str(r.get("desc") or "")
            if kw and kw not in name and kw not in sid and kw not in desc:
                continue
            lb.insert("end", "%s  %s  [%s]" % (sid, name, r.get("type", "")))
            shown_ids.append(sid)
        cnt_var.set("共 %d 条(本季主公技能)" % len(rows))

    def _take(*_a):
        sel = list(lb.curselection())
        if not sel:
            messagebox.showinfo("提示", "先在上面选一个(双击也能直接选中)", parent=d2)
            return
        sid = shown_ids[sel[0]]
        d2.destroy()
        try:
            on_pick(sid)
        except Exception as e:
            _SIL.note("ui/widgets.open_lord_skill_picker", "on_pick失败: %s" % e)

    def _load():
        if SC is None:
            d2.after(0, lambda: cnt_var.set("读不到 season_config 模块"))
            return
        try:
            data = SC.view_skills(season or SC.current_season(), "lordskill")
        except Exception as e:
            d2.after(0, lambda: cnt_var.set("读不到: %s" % e))
            return
        d2.after(0, _populate, data)

    sv.trace_add("write", _fill)
    lb.bind("<Double-Button-1>", _take)
    ent.bind("<Return>", lambda _e: _take())
    bar = tk.Frame(d2, bg=BG)
    bar.pack(fill="x", padx=10, pady=(0, 10))
    tk.Button(bar, text="确定", command=_take, bg=BTN, fg=FG, activebackground=BTN,
              activeforeground=FG, font=(FH, 9), relief="flat", padx=10,
              cursor="hand2").pack(side="right")
    tk.Button(bar, text="取消", command=d2.destroy, bg=BTN, fg=DIM, activebackground=BTN,
              activeforeground=FG, font=(FH, 9), relief="flat", padx=10,
              cursor="hand2").pack(side="right", padx=(0, 6))
    _th.Thread(target=_load, daemon=True).start()
    _fill()
    return d2


class ThemeMixin(object):
    """v4.109★ 各 tab 页「主题色 getter」的公共实现 —— 给 *TabBuilder 类继承用。

    以前 inject / resource / mechanism / stats / lineup / season 六个 tab 类
    各写一份 _bg/_fg/_dim/_gold/_btn/_act/_blue/_green/_red/_warn/_fh,
    内容全是 getattr(self.app, "XX", "兜底色") —— 重复六遍不说, 兜底色值还写死在
    各自文件里, 而且【已经对不上了】:
        inject/resource/mechanism/stats 用 BG=#2a2a2a FG=#eeeeee BTN=#3a3a3a GREEN=#5cb85c
        lineup/season                   用 BG=#1e1e1e FG=#dddddd BTN=#2c2c2c GREEN=#7fd48f
    而 app.py:315-325 的真实值就是第一派那套 —— 后两个文件的兜底从来就没对上过。
    实践中 app 一定带这些属性, 兜底从不触发, 所以一直没人发现。

    现在统一到 app.py 的真实默认值: 各 tab 类继承本 mixin 即可, 不再各写各的。

    ⚠️ 上面那组 BG/FG/... 模块常量是本文件(widgets)内部控件自己用的另一套配色,
       与这里的 tab 主题是两回事, 别混着用。
    """

    def _theme(self, key, fallback):
        """从 app 取主题值; app 没有这个属性时才用 fallback(与 app.py 默认值一致)。"""
        return getattr(self.app, key, fallback)

    def _bg(self):    return self._theme("BG",    "#2a2a2a")
    def _fg(self):    return self._theme("FG",    "#eeeeee")
    def _dim(self):   return self._theme("DIM",   "#999999")
    def _gold(self):  return self._theme("GOLD",  "#e0b060")
    def _btn(self):   return self._theme("BTN",   "#3a3a3a")
    def _act(self):   return self._theme("ACT",   "#4a4a4a")
    def _blue(self):  return self._theme("BLUE",  "#7fd4ff")
    def _green(self): return self._theme("GREEN", "#5cb85c")
    def _red(self):   return self._theme("RED",   "#ff5555")
    def _warn(self):  return self._theme("WARN",  "#ffcc00")
    def _fh(self):    return self._theme("FH",    "Microsoft YaHei UI")

RACE_OPTIONS = [
    ('all', '全部'), ('wei', '魏'), ('shu', '蜀'), ('wu', '吴'), ('qun', '群'),
    ('han', '汉'), ('wei_wu', '魏晋(或自定义)')
]
JOB_OPTIONS = [
    ('all', '全部'), ('fangzhu', '方主'), ('zhanjian', '戰艦'), ('xiaoji', '驍騎'),
    ('sheshou', '射手'), ('cike', '刺客'), ('junshi', '軍師'), ('qi', '騎'),
    ('bu', '步'), ('gong', '弓'), ('gui', '鬼'), ('shen', '神')
]
GENDER_OPTIONS = [
    ('all', '全部'), ('male', '男'), ('female', '女'), ('unknown', '未知')
]
COST_OPTIONS = [
    ('all', '全部'), ('1', '1费'), ('2', '2费'), ('3', '3费'), ('4', '4费'), ('5', '5费')
]
STAR_OPTIONS = [
    ('all', '全部'), ('1', '★'), ('2', '★★'), ('3', '★★★')
]
TYPE_OPTIONS = [
    ('all', '全部'), ('hero', '武将'), ('spell', '锦囊/道具')
]

_DEFAULT_FILTERS = {
    'race': 'all', 'job': 'all', 'cost': 'all', 'star': 'all',
    'gender': 'all', 'type': 'all', 'keyword': '',
    'show_tags': True, 'show_names': True, 'show_whitelist': True,
    'show_blacklist': True,
}

class DataViewWidget(ttk.Frame):
    """
    通用数据查看/编辑/比对控件
    用于“百科全书”、“全部原始数据”、“自走棋库”的展示与同步
    """
    def __init__(self, master, data_path=None, title="数据查看", push_callback=None, save_callback=None, encyclopedia=None, **kw):
        super().__init__(master, **kw)
        self.data_path = data_path
        self.title = title
        self.push_callback = push_callback
        self.save_callback = save_callback
        self.encyclopedia = encyclopedia or {} # name -> info
        self.db = [] # 扁平化的数据列表 [{"name":..., "cost":...}]
        self._filters = dict(_DEFAULT_FILTERS)
        self._hidden_ids = set()
        self._build_ui()
        self.load_data()

    def _build_ui(self):
        # 顶部区域 (向上阵编排看齐: 使用 LabelFrame 分组)
        top = ttk.Frame(self)
        top.pack(fill='x', padx=4, pady=2)

        # 1. 筛选与搜索组 (放在一行，更紧凑)
        filter_group = ttk.LabelFrame(top, text=f" 🔍 {self.title} - 筛选与搜索 ")
        filter_group.pack(fill='x', padx=2, pady=1)
        
        filter_inner = ttk.Frame(filter_group)
        filter_inner.pack(fill='x', padx=4, pady=2)

        # 势力/费用 筛选
        self._combo_vars = {}
        for key, title, opts in [
                ('race', '势力', RACE_OPTIONS),
                ('cost', '费用', COST_OPTIONS),
                ('type', '类型', TYPE_OPTIONS)]:
            tk.Label(filter_inner, text=title, font=(FH, 9)).pack(side='left', padx=(6, 1))
            v = tk.StringVar(value='all')
            self._combo_vars[key] = v
            cb = ttk.Combobox(filter_inner, textvariable=v, values=[x[1] for x in opts],
                              state='readonly', width=9)
            cb.pack(side='left', padx=1)
            cb.bind('<<ComboboxSelected>>', lambda e, k=key: self._apply_filter(k))
            if not hasattr(self, '_opt_map'): self._opt_map = {}
            self._opt_map[key] = {label: vid for vid, label in opts}

        # 搜索框
        tk.Label(filter_inner, text="🔍:", font=(FH, 9)).pack(side='left', padx=(12, 1))
        self._kw_var = tk.StringVar()
        e = ttk.Entry(filter_inner, textvariable=self._kw_var, width=15)
        e.pack(side='left', padx=1, fill='x', expand=True)
        e.bind('<KeyRelease>', lambda e: self.refresh())
        ttk.Button(filter_inner, text='清空', width=4, command=lambda: (self._kw_var.set(''), self.refresh())).pack(side='left', padx=3)

        # 2. 功能按钮组 (向上阵编排看齐: 按钮更小，横向排布)
        action_group = ttk.LabelFrame(top, text=" 🛠 功能操作 ")
        action_group.pack(fill='x', padx=2, pady=1)
        
        action_inner = ttk.Frame(action_group)
        action_inner.pack(fill='x', padx=4, pady=2)
        
        btn_config = [
            ('📤 推送', self._on_push),
            ('✖ 隐藏', self._on_hide),
            ('↺ 恢复', self._restore_hidden_dialog),
            ('🗑 删除', self._on_delete),
            ('📥 导入', self._on_import),
            ('📤 导出', self._on_export),
            ('💾 保存', self.save_data),
            ('↻ 刷新', self.load_data),
            ('❓ 帮助', self._show_help)
        ]
        
        for i, (txt, cmd) in enumerate(btn_config):
            if txt == '📤 推送' and not self.push_callback: continue
            btn = ttk.Button(action_inner, text=txt, command=cmd, width=7)
            btn.pack(side='left', padx=1)

        # 3. 列表区域
        list_group = ttk.LabelFrame(self, text=" 📋 数据列表 ")
        list_group.pack(fill='both', expand=True, padx=4, pady=2)
        
        mid = ttk.Frame(list_group)
        mid.pack(fill='both', expand=True, padx=2, pady=2)
        
        cols = ('name', 'cost', 'faction', 'type', 'effect', 'tags', 'mode', 'atk', 'hp', 'src_id')
        self.tv = ttk.Treeview(mid, columns=cols, show='headings', selectmode='extended')
        
        vsb = ttk.Scrollbar(mid, orient='vertical', command=self.tv.yview)
        hsb = ttk.Scrollbar(mid, orient='horizontal', command=self.tv.xview)
        self.tv.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        
        for c, w, head in [
            ('name', 90, '姓名'), ('cost', 35, '费'),
            ('faction', 60, '势力'), ('type', 60, '类型'), 
            ('effect', 250, '技能效果'), ('tags', 100, '标签'), 
            ('mode', 50, '名单'), ('atk', 35, '攻'), 
            ('hp', 35, '血'), ('src_id', 80, '原始ID')]:
            self.tv.heading(c, text=head)
            self.tv.column(c, width=w, minwidth=30, anchor='w', stretch=(c in ('effect', 'name', 'tags')))
            
        self.tv.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')
        
        mid.grid_columnconfigure(0, weight=1)
        mid.grid_rowconfigure(0, weight=1)
        
        self.tv.tag_configure('diff', background='#3e2723', foreground='#ffd700')
        self.tv.bind('<Double-1>', lambda e: self._on_double_click())

    def _show_help(self):
        msg = """【颜色说明】
● 金字深褐底：数据与“百科全书”不一致（仅校验势力/费用/描述，不校验动态变化的攻防）
● 浅紫色：锦囊/道具卡牌
● 红色：汉势力 | 蓝色：魏势力 | 绿色：蜀势力
● 橙色：吴势力 | 灰色：群势力 | 黄色：袁势力

【操作指南】
1. 双击行：弹出详细编辑窗口，修改后需点“保存修改”。
2. 推送：将选中项同步到“百科全书”，需在百科全书页点“保存”。
3. 隐藏：临时从列表移除，可通过“恢复”找回。
4. 删除：永久从当前数据库移除，需点“保存修改”写入文件。
5. 原始ID：从自走棋库推送时会保留原始 chessID/goodsID。"""
        messagebox.showinfo("使用说明", msg)

    def load_data(self):
        """从文件或传入的路径加载数据"""
        if not self.data_path or not os.path.exists(self.data_path):
            self.db = []
            self.refresh()
            return

        try:
            with open(self.data_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            
            # 统一转为列表格式
            processed = []
            if isinstance(raw, list):
                processed = raw
            elif isinstance(raw, dict):
                # 如果是 tavern_*.json 这种字典格式
                for k, v in raw.items():
                    item = dict(v) if isinstance(v, dict) else {"n": str(v)}
                    item['src_id'] = k
                    # 字段映射对齐 generals.json
                    item.setdefault('name', item.get('n') or item.get('ChessName') or item.get('name'))
                    item.setdefault('effect', item.get('desc') or item.get('ChessSkillDesc') or item.get('effect'))
                    item.setdefault('faction', item.get('faction') or item.get('race_or_camp'))
                    item.setdefault('cost', item.get('rank') or item.get('cost'))
                    processed.append(item)
            
            self.db = processed
            self.refresh()
        except Exception as e:
            messagebox.showerror("加载失败", f"无法加载 {self.data_path}: {e}")

    def save_data(self):
        """保存当前修改回文件"""
        if not self.data_path: return
        try:
            # 根据原始格式写回
            if not os.path.exists(self.data_path):
                # 如果文件不存在，直接按列表保存 db
                out = self.db
            else:
                with open(self.data_path, "r", encoding="utf-8") as f:
                    orig = json.load(f)
                
                if isinstance(orig, list):
                    out = self.db
                elif isinstance(orig, dict):
                    # 字典模式: 我们需要把 db 中的项合并回原始字典，避免丢失未显示的字段
                    # 建立 db 索引
                    db_map = {str(item.get('src_id')): item for item in self.db if item.get('src_id')}
                    
                    # 1. 更新或保留原始项
                    new_dict = {}
                    for sid, old_val in orig.items():
                        sid_s = str(sid)
                        if sid_s in db_map:
                            item = db_map[sid_s]
                            # 移除辅助字段后合并
                            clean_item = {k: v for k, v in item.items() if k != 'src_id'}
                            if isinstance(old_val, dict):
                                updated = old_val.copy()
                                updated.update(clean_item)
                                new_dict[sid] = updated
                            else:
                                new_dict[sid] = clean_item
                            # 从 db_map 中移除已处理的
                            del db_map[sid_s]
                        else:
                            # 如果项在 db 中不存在，说明被删除了，这里我们先保留还是删除？
                            # 根据 _on_delete 的逻辑，它是从 self.db 中删的。
                            # 所以这里我们不把 sid 加入 new_dict 即代表删除了。
                            pass
                    
                    # 2. 加入 db 中新增的项
                    for sid_s, item in db_map.items():
                        clean_item = {k: v for k, v in item.items() if k != 'src_id'}
                        new_dict[sid_s] = clean_item
                    
                    out = new_dict
                else:
                    out = self.db
            
            with open(self.data_path, "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False, indent=2)
            
            if self.save_callback:
                self.save_callback()
                
            messagebox.showinfo("成功", f"数据已保存至 {os.path.basename(self.data_path)}")
        except Exception as e:
            messagebox.showerror("保存失败", str(e))

    def refresh(self):
        for i in self.tv.get_children(): self.tv.delete(i)
        kw = self._kw_var.get().lower().strip()
        
        for item in self.db:
            name = str(item.get('name') or '')
            effect = str(item.get('effect') or item.get('desc') or '')
            
            # v4.00★ 如果没有直接描述，尝试通过 skill ID 列表去技能库查找
            if not effect and item.get('skill'):
                effect = IDMAP.get_chess_skills_desc(item.get('skill'))
            
            tags = str(item.get('tags') or '')
            sid = str(item.get('src_id') or '')
            
            # 隐藏过滤
            if sid in self._hidden_ids:
                continue

            # 搜索过滤
            if kw and kw not in name.lower() and kw not in effect.lower() and kw not in tags.lower() and kw not in sid.lower():
                continue
            
            # 条件筛选
            if not self._filters_match(item):
                continue
            
            # v3.98★ 比对逻辑
            diff_tags = []
            if self.encyclopedia and name in self.encyclopedia:
                wiki = self.encyclopedia[name]
                if (str(item.get('effect') or '') != str(wiki.get('effect') or wiki.get('desc') or '') or
                    str(item.get('faction') or '') != str(wiki.get('faction') or '') or
                    str(item.get('cost') or '') != str(wiki.get('cost') or '')):
                    diff_tags.append('diff')
            elif self.encyclopedia:
                diff_tags.append('diff')
                
            self.tv.insert('', 'end', values=(
                name,
                item.get('cost', ''),
                item.get('faction', ''),
                item.get('type') or ('锦囊/道具' if item.get('is_spell_or_prop') else '武将'),
                effect[:200],
                tags,
                item.get('mode', '无'),
                item.get('atk', ''),
                item.get('hp', ''),
                sid
            ), tags=tuple(diff_tags))

    def _apply_filter(self, key):
        v = self._combo_vars[key].get()
        self._filters[key] = self._opt_map[key].get(v, 'all')
        self.refresh()

    def _filters_match(self, info):
        # 势力筛选
        if self._filters['race'] != 'all':
            faction = str(info.get('faction') or info.get('race_or_camp') or '').lower()
            if faction != self._filters['race'] and not (self._filters['race'] == 'han' and faction == '汉'):
                # 特殊处理汉系，因为 RACE_OPTIONS 里是 'han'，数据里可能是 '汉'
                if not (self._filters['race'] == 'han' and faction == '汉'):
                    # 这里逻辑稍微优化下
                    pass
            
            # 统一转换匹配
            target = self._filters['race']
            actual = faction
            # 映射表
            m = {'han': '汉', 'wei': '魏', 'shu': '蜀', 'wu': '吴', 'qun': '群', 'wei_wu': '魏晋'}
            if target in m:
                if actual != m[target] and actual != target:
                    return False
            elif actual != target:
                return False

        # 费用筛选
        if self._filters['cost'] != 'all':
            if str(info.get('cost') or '') != self._filters['cost']:
                return False

        # 类型筛选
        if self._filters['type'] != 'all':
            is_spell = bool(info.get('is_spell_or_prop') or info.get('card_type') == '锦囊' or 'spell' in str(info.get('src_id') or '').lower())
            if self._filters['type'] == 'hero' and is_spell:
                return False
            if self._filters['type'] == 'spell' and not is_spell:
                return False
        
        return True

    def _on_hide(self):
        sel = self.tv.selection()
        if not sel:
            messagebox.showwarning("提示", "请先选择要隐藏的项")
            return
        for s in sel:
            vals = self.tv.item(s, 'values')
            sid = vals[8]
            if sid: self._hidden_ids.add(str(sid))
        self.refresh()

    def _restore_hidden_dialog(self):
        if not self._hidden_ids:
            messagebox.showinfo("提示", "没有已隐藏的项")
            return
        
        top = tk.Toplevel(self)
        top.title(f"恢复隐藏 - {self.title}")
        top.geometry("400x400")
        
        tk.Label(top, text="已隐藏的 ID:").pack(pady=5)
        
        lb = tk.Listbox(top, selectmode='extended')
        lb.pack(fill='both', expand=True, padx=10, pady=5)
        
        for sid in sorted(self._hidden_ids):
            lb.insert('end', sid)
            
        def restore():
            sel = lb.curselection()
            if not sel: return
            for i in reversed(sel):
                sid = lb.get(i)
                self._hidden_ids.remove(sid)
            self.refresh()
            top.destroy()
            
        def restore_all():
            self._hidden_ids.clear()
            self.refresh()
            top.destroy()
            
        f = ttk.Frame(top)
        f.pack(pady=10)
        ttk.Button(f, text="恢复选中", command=restore).pack(side='left', padx=5)
        ttk.Button(f, text="全部恢复", command=restore_all).pack(side='left', padx=5)

    def _on_delete(self):
        sel = self.tv.selection()
        if not sel:
            messagebox.showwarning("提示", "请先选择要删除的行")
            return
        
        if not messagebox.askyesno("确认删除", f"确定要从 {self.title} 中永久删除选中的 {len(sel)} 项吗？\n删除后需点击“保存修改”才会写入文件。"):
            return
            
        # 记录删除项以更新 db
        to_delete = []
        for s in sel:
            vals = self.tv.item(s, 'values')
            name_val = vals[0]
            sid_val = vals[8]
            to_delete.append((name_val, sid_val))
            
        for name_val, sid_val in to_delete:
            for i, item in enumerate(self.db):
                if str(item.get('src_id', '')) == str(sid_val) and str(item.get('name', '')) == str(name_val):
                    self.db.pop(i)
                    break
        
        self.refresh()

    def _on_import(self):
        path = filedialog.askopenfilename(title="导入 JSON 数据", filetypes=[("JSON Files", "*.json")])
        if not path: return
        try:
            with open(path, "r", encoding="utf-8") as f:
                new_data = json.load(f)
            
            if not isinstance(new_data, (list, dict)):
                raise ValueError("不支持的 JSON 格式 (须为列表或字典)")
            
            if messagebox.askyesno("导入方式", "点击“是”：清空当前数据并导入\n点击“否”：合并到当前数据"):
                self.db = []

            # 统一处理逻辑同 load_data
            processed = []
            if isinstance(new_data, list):
                processed = new_data
            elif isinstance(new_data, dict):
                for k, v in new_data.items():
                    item = dict(v) if isinstance(v, dict) else {"n": str(v)}
                    item['src_id'] = k
                    processed.append(item)
            
            # 合并去重 (按 name + src_id)
            existing = {(str(x.get('name')), str(x.get('src_id', ''))) for x in self.db}
            for p in processed:
                key = (str(p.get('name')), str(p.get('src_id', '')))
                if key not in existing:
                    self.db.append(p)
                    existing.add(key)
            
            self.refresh()
            messagebox.showinfo("成功", f"已导入 {len(processed)} 条数据。")
        except Exception as e:
            messagebox.showerror("导入失败", str(e))

    def _on_export(self):
        path = filedialog.asksaveasfilename(title="导出 JSON 数据", 
                                            defaultextension=".json",
                                            filetypes=[("JSON Files", "*.json")],
                                            initialfile=f"export_{os.path.basename(self.data_path or 'data.json')}")
        if not path: return
        try:
            # 始终以列表格式导出，方便阅读和再导入
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.db, f, ensure_ascii=False, indent=2)
            messagebox.showinfo("成功", f"数据已导出至 {path}")
        except Exception as e:
            messagebox.showerror("导出失败", str(e))

    def _on_double_click(self):
        sel = self.tv.selection()
        if not sel: return
        idx = self.tv.index(sel[0])
        # 这里需要从 tv 映射回 db 列表，考虑到过滤，需要更复杂的逻辑
        # 简单处理：根据 src_id 或 name 找
        vals = self.tv.item(sel[0], 'values')
        name_val = vals[0]
        sid_val = vals[9]
        
        target = None
        for item in self.db:
            if str(item.get('src_id')) == sid_val and str(item.get('name')) == name_val:
                target = item
                break
        
        if not target: return
        
        # 弹出编辑框 (复用 HeroPickWidget 的编辑逻辑)
        self._edit_dialog(target)

    def _edit_dialog(self, item):
        top = tk.Toplevel(self)
        top.title(f"编辑: {item.get('name')}")
        top.geometry("450x600")
        top.transient(self)
        top.grab_set()

        # 增加滚动支持
        canvas = tk.Canvas(top, highlightthickness=0)
        vsb = ttk.Scrollbar(top, orient="vertical", command=canvas.yview)
        scroll_frame = ttk.Frame(canvas)
        
        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll_frame, anchor="nw", width=430)
        canvas.configure(yscrollcommand=vsb.set)
        
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        
        fields = [
            ('name', '姓名'),
            ('cost', '费用'),
            ('faction', '势力'),
            ('type', '类型 (武将/锦囊/道具)'),
            ('mode', '名单模式 (白名单/黑名单/无)'),
            ('effect', '效果/描述'),
            ('tags', '标签(逗号分隔)'),
            ('atk', '攻击'),
            ('hp', '生命')
        ]
        
        entries = {}
        for i, (key, label) in enumerate(fields):
            tk.Label(scroll_frame, text=label).pack(pady=(5, 0), anchor='w', padx=10)
            if key == 'effect':
                t = tk.Text(scroll_frame, height=8, width=40)
                t.insert('1.0', str(item.get(key) or ''))
                t.pack(padx=10, fill='x')
                entries[key] = t
            elif key == 'mode':
                v = tk.StringVar(value=str(item.get(key) or '无'))
                cb = ttk.Combobox(scroll_frame, textvariable=v, values=['白名单', '黑名单', '无'], state='readonly', width=38)
                cb.pack(padx=10, fill='x')
                entries[key] = v
            else:
                v = tk.StringVar(value=str(item.get(key) or ''))
                e = ttk.Entry(scroll_frame, textvariable=v, width=40)
                e.pack(padx=10, fill='x')
                entries[key] = v

        def save():
            for key, entry in entries.items():
                val = entry.get('1.0', 'end-1c') if isinstance(entry, tk.Text) else entry.get()
                if key in ('cost', 'atk', 'hp') and (val.isdigit() or (val.startswith('-') and val[1:].isdigit())):
                    item[key] = int(val)
                else:
                    item[key] = val
            
            # 自动维护 is_spell_or_prop
            if "锦囊" in str(item.get("type", "")) or "道具" in str(item.get("type", "")):
                item["is_spell_or_prop"] = True
            
            self.refresh()
            top.destroy()
            messagebox.showinfo("成功", "修改已应用。请在主界面点击“保存修改”以持久化到文件。")

        # 底部按钮
        btn_f = ttk.Frame(top)
        btn_f.pack(fill='x', side='bottom', pady=10)
        ttk.Button(btn_f, text="💾 确定并应用", command=save, width=20).pack()

    def _on_push(self):
        sel = self.tv.selection()
        items_to_push = []
        
        if not sel:
            if not messagebox.askyesno("推送全部", "未选中任何行，是否要将当前列表中所有【可见】项推送到百科全书？"):
                return
            for item_id in self.tv.get_children():
                vals = self.tv.item(item_id, 'values')
                items_to_push.append(self._vals_to_dict(vals))
        else:
            for s in sel:
                vals = self.tv.item(s, 'values')
                items_to_push.append(self._vals_to_dict(vals))
        
        if self.push_callback:
            self.push_callback(items_to_push)
            messagebox.showinfo("推送成功", f"已推送 {len(items_to_push)} 条数据到百科全书。")

    def _vals_to_dict(self, vals):
        """将 Treeview 的 values (tuple) 转回字典格式，对应 cols 定义"""
        # cols = ('name', 'cost', 'faction', 'type', 'effect', 'tags', 'mode', 'atk', 'hp', 'src_id')
        return {
            'name': vals[0],
            'cost': vals[1],
            'faction': vals[2],
            'type': vals[3],
            'effect': vals[4],
            'tags': vals[5],
            'mode': vals[6],
            'atk': vals[7],
            'hp': vals[8],
            'src_id': vals[9]
        }

class HeroPickWidget(ttk.Frame):
    """
    武将筛选双列表控件
    - 左侧: 候选池 (可按阵营/职业/费用/星/性别/关键字搜索)
    - 右侧: 白名单(要合的) / 黑名单(要遣散的) —— 可切换
    - 中间: →/← 按钮, ↑↓ 排序
    - 顶部: 筛选条件行 + 搜索框

    API:
        self.set_heroes(dict)            # {"goodsID": {...}, ...}
        self.get_whitelist() -> list     # 返回 goodsID 数组
        self.get_blacklist() -> list
        self.load_user_patch(path)
        self.save_user_patch(path)
    """
    def __init__(self, master, hero_db=None, encyclopedia=None, push_callback=None, **kw):
        super().__init__(master, **kw)
        self.hero_db = OrderedDict(hero_db or {})
        self.encyclopedia = encyclopedia or {} # name -> info
        self.push_callback = push_callback
        self._filters = dict(_DEFAULT_FILTERS)
        self._whitelist = []       # goodsID
        self._blacklist = []       # goodsID
        self._list_mode = tk.StringVar(value='white')    # 'white' 或 'black'
        self._hidden_ids = set()                         # v3.98★ 隐藏列表 (临时删除)
        self._on_change = None
        self._build_ui()

    # ---------- UI ----------
    def _build_ui(self):
        top = ttk.Frame(self)
        top.pack(fill='x', padx=4, pady=2)

        # 筛选行 (向上阵编排看齐: 更加整齐的 LabelFrame)
        self._combo_vars = {}
        filter_data = [
            ('race', '阵营', RACE_OPTIONS), ('job', '职业', JOB_OPTIONS),
            ('cost', '费用', COST_OPTIONS), ('star', '星级', STAR_OPTIONS),
            ('gender', '性别', GENDER_OPTIONS), ('type', '分类', TYPE_OPTIONS)
        ]
        
        for i, (key, title, opts) in enumerate(filter_data):
            frm = ttk.LabelFrame(top, text=f" {title} ")
            frm.grid(row=0, column=i, padx=2, pady=1, sticky='we')
            v = tk.StringVar(value=self._filters[key])
            self._combo_vars[key] = v
            cb = ttk.Combobox(frm, textvariable=v, values=[x[1] for x in opts],
                              state='readonly', width=8)
            self._opt_map = getattr(self, '_opt_map', {})
            self._opt_map[key] = {label: vid for vid, label in opts}
            cb.current(0)
            cb.pack(padx=4, pady=2)
            cb.bind('<<ComboboxSelected>>', lambda e, k=key: self._apply_filter(k))
            top.columnconfigure(i, weight=1)

        # 关键字搜索
        sf = ttk.LabelFrame(top, text=' 🔍 搜索(姓名/职业/标签/ID) ')
        sf.grid(row=1, column=0, columnspan=6, padx=2, pady=2, sticky='we')
        self._kw_var = tk.StringVar()
        e = ttk.Entry(sf, textvariable=self._kw_var)
        e.pack(side='left', fill='x', expand=True, padx=6, pady=4)
        e.bind('<KeyRelease>', self._on_kw)
        ttk.Button(sf, text='清空搜索', width=8, command=lambda: (self._kw_var.set(''), self.refresh_candidates())).pack(side='right', padx=6)

        # 中间内容
        mid = ttk.Frame(self)
        mid.pack(fill='both', expand=True, padx=4, pady=2)

        # 左: 候选池
        left = ttk.LabelFrame(mid, text=' 📥 候选池 ')
        left.pack(side='left', fill='both', expand=True, padx=2)
        cols_left = ('goodsID', 'name', 'type', 'cost', 'star', 'race', 'job', 'gender', 'desc', 'tags')
        self._cand_tv = ttk.Treeview(left, columns=cols_left, show='headings', selectmode='extended')
        
        vsb1 = ttk.Scrollbar(left, orient='vertical', command=self._cand_tv.yview)
        hsb1 = ttk.Scrollbar(left, orient='horizontal', command=self._cand_tv.xview)
        self._cand_tv.configure(yscrollcommand=vsb1.set, xscrollcommand=hsb1.set)
        
        for c, w, head in [
                ('goodsID', 70, 'ID'), ('name', 90, '姓名'),
                ('type', 50, '类型'), ('cost', 35, '费'), 
                ('star', 35, '星'), ('race', 65, '阵营'), 
                ('job', 70, '职业'), ('gender', 40, '性别'), 
                ('desc', 250, '技能描述'), ('tags', 100, '标签')]:
            self._cand_tv.heading(c, text=head)
            self._cand_tv.column(c, width=w, minwidth=30, anchor='w', stretch=(c in ('desc', 'tags')))
            
        self._cand_tv.grid(row=0, column=0, sticky='nsew')
        vsb1.grid(row=0, column=1, sticky='ns')
        hsb1.grid(row=1, column=0, sticky='ew')
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(0, weight=1)
        self._cand_tv.bind('<Double-1>', lambda e: self._on_cand_double_click())

        # 中: 按钮列 (使用分组操作框)
        btns_lf = ttk.LabelFrame(mid, text=' ⚙ 操作 ')
        btns_lf.pack(side='left', padx=2, pady=2, fill='y')
        
        btns = ttk.Frame(btns_lf)
        btns.pack(padx=2, pady=2)
        
        tk.Label(btns, text='名单切换', font=(FH, 9, 'bold')).grid(row=0, column=0, columnspan=2, pady=(0, 2))
        ttk.Radiobutton(btns, text='白名单', variable=self._list_mode, value='white',
                        command=self._refresh_right_heading).grid(row=1, column=0, sticky='w', padx=1)
        ttk.Radiobutton(btns, text='黑名单', variable=self._list_mode, value='black',
                        command=self._refresh_right_heading).grid(row=1, column=1, sticky='w', padx=1)
        
        ttk.Separator(btns, orient='horizontal').grid(row=2, column=0, columnspan=2, sticky='ew', pady=4)
        
        btn_list = [
            ('→ 加入', self._add_selected), ('← 移除', self._remove_selected),
            ('✖ 隐藏', self._hide_selected), ('↺ 恢复', self._restore_hidden_dialog),
            ('↑ 上移', lambda: self._move(-1)), ('↓ 下移', lambda: self._move(1)),
            ('清空', self._clear_list), ('全入', self._add_all_filtered),
            ('📤 候百科', self._push_selected_to_wiki), ('📤 名百科', self._push_list_to_wiki),
            ('💾 保存', lambda: self._emit_change(force_save=True)), ('❓ 帮助', self._show_help),
            ('📥 导入', self._load_patch_dialog), ('📤 导出', self._save_patch_dialog)
        ]
        
        for i, (txt, cmd) in enumerate(btn_list):
            r, c = divmod(i, 2)
            ttk.Button(btns, text=txt, command=cmd, width=7).grid(row=r+3, column=c, padx=1, pady=1)
        
        # 右: 白/黑名单
        self._right_frame = ttk.LabelFrame(mid, text=' 📋 白名单 ')
        right = self._right_frame
        right.pack(side='left', fill='both', expand=True, padx=2)
        cols_right = ('rank', 'goodsID', 'name', 'type', 'cost', 'star', 'desc', 'tags')
        self._right_tv = ttk.Treeview(right, columns=cols_right, show='headings', selectmode='extended')
        
        vsb2 = ttk.Scrollbar(right, orient='vertical', command=self._right_tv.yview)
        hsb2 = ttk.Scrollbar(right, orient='horizontal', command=self._right_tv.xview)
        self._right_tv.configure(yscrollcommand=vsb2.set, xscrollcommand=hsb2.set)
        
        for c, w, head in [
                ('rank', 30, '#'), ('goodsID', 70, 'ID'),
                ('name', 90, '姓名'), ('type', 50, '类型'),
                ('cost', 35, '费'), ('star', 35, '星'), 
                ('desc', 200, '描述'), ('tags', 100, '标签/备注')]:
            self._right_tv.heading(c, text=head)
            self._right_tv.column(c, width=w, minwidth=25, anchor='w', stretch=(c in ('desc', 'tags')))
            
        self._right_tv.grid(row=0, column=0, sticky='nsew')
        vsb2.grid(row=0, column=1, sticky='ns')
        hsb2.grid(row=1, column=0, sticky='ew')
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(0, weight=1)
        
        self._right_tv.bind('<Double-1>', lambda e: self._remove_selected())

        # v3.59★ 颜色标记: 锦囊牌使用浅紫色高亮
        self._cand_tv.tag_configure('spell', foreground='#b388ff')
        self._right_tv.tag_configure('spell', foreground='#b388ff')
        
        # v3.98★ 势力颜色标记
        self._cand_tv.tag_configure('faction_汉', foreground='#ff5252') # 红色
        self._cand_tv.tag_configure('faction_魏', foreground='#448aff') # 蓝色
        self._cand_tv.tag_configure('faction_蜀', foreground='#69f0ae') # 绿色
        self._cand_tv.tag_configure('faction_吴', foreground='#ffab40') # 橙色
        self._cand_tv.tag_configure('faction_群', foreground='#bdbdbd') # 灰色
        self._cand_tv.tag_configure('faction_袁', foreground='#ffd740') # 黄色
        
        self._right_tv.tag_configure('faction_汉', foreground='#ff5252')
        self._right_tv.tag_configure('faction_魏', foreground='#448aff')
        self._right_tv.tag_configure('faction_蜀', foreground='#69f0ae')
        self._right_tv.tag_configure('faction_吴', foreground='#ffab40')
        self._right_tv.tag_configure('faction_群', foreground='#bdbdbd')
        self._right_tv.tag_configure('faction_袁', foreground='#ffd740')

        # v3.98★ 比对高亮: 与百科全书不一致时显示金黄色背景
        self._cand_tv.tag_configure('diff', background='#3e2723', foreground=GOLD)
        self._right_tv.tag_configure('diff', background='#3e2723', foreground=GOLD)

        self.refresh_candidates()
        self.refresh_right()

    def _show_help(self):
        msg = """【颜色说明】
● 金字深褐底：数据与“百科全书”不一致（仅校验势力/费用/描述，不校验动态变化的攻防）
● 浅紫色：锦囊/道具牌
● 阵营高亮：红(汉)、蓝(魏)、绿(蜀)、橙(吴)、灰(群)、黄(袁)

【黑白名单操作】
1. 加入：选中左侧候选池项，点“→ 加入”，将武将加入当前模式名单。
2. 移除：选中右侧名单项，点“← 移除”或双击，武将回到候选池。
3. 隐藏：选中候选池项点“✖ 隐藏”，武将不再出现。
4. 排序：选中右侧名单项，点“↑上移”或“↓下移”调整招募优先级。
5. 模式：通过上方单选框切换“白名单”或“黑名单”。
6. 保存：编辑武将或调整名单后，点击“💾 保存”持久化到本地。"""
        messagebox.showinfo("武将管理说明", msg)

    # ---------- 筛选 / 变更 ----------
    def _on_kw(self, _evt=None):
        self._filters['keyword'] = self._kw_var.get().strip()
        self.refresh_candidates()

    def _apply_filter(self, key):
        v = self._combo_vars[key].get()
        self._filters[key] = self._opt_map[key].get(v, 'all')
        self.refresh_candidates()

    def _filters_match(self, info):
        if self._filters['race'] != 'all':
            if str(info.get('race_or_camp') or info.get('race') or '').lower() != self._filters['race']:
                return False
        if self._filters['job'] != 'all':
            if str(info.get('job') or '').lower() != self._filters['job']:
                return False
        if self._filters['cost'] != 'all':
            if str(info.get('cost') or '') != self._filters['cost']:
                return False
        if self._filters['star'] != 'all':
            if str(info.get('star') or info.get('level') or '') != self._filters['star']:
                return False
        if self._filters['gender'] != 'all':
            if str(info.get('gender') or 'unknown').lower() != self._filters['gender']:
                return False
        if self._filters['type'] != 'all':
            is_spell = bool(info.get('is_spell_or_prop'))
            if self._filters['type'] == 'hero' and is_spell:
                return False
            if self._filters['type'] == 'spell' and not is_spell:
                return False
        kw = self._filters['keyword']
        if kw:
            haystack = ' '.join([
                str(info.get('name') or ''),
                str(info.get('race_or_camp') or info.get('race') or ''),
                str(info.get('job') or ''),
                ' '.join([str(t) for t in (info.get('tags') or [])]),
                str(info.get('desc') or ''), # v3.98★ 包含描述搜索
                str(info.get('goodsID') or ''),
                str(info.get('chessID') or ''),
                str(info.get('spellID') or ''),
                str(info.get('itemID') or '')
            ]).lower()
            if kw.lower() not in haystack:
                return False
        return True

    # ---------- 列表刷新 ----------
    def refresh_candidates(self):
        tv = self._cand_tv
        for i in tv.get_children(): tv.delete(i)
        # 过滤后 + 排除已在白/黑名单里的 + 排除隐藏的
        blocked = set(self._whitelist) | set(self._blacklist) | self._hidden_ids
        for gid, info in self.hero_db.items():
            if gid in blocked: continue
            if not self._filters_match(info): continue
            
            tags = []
            if info.get('is_spell_or_prop'):
                tags.append('spell')
            
            faction = str(info.get('race_or_camp') or info.get('race') or '')
            if faction in ('汉', '魏', '蜀', '吴', '群', '袁'):
                tags.append(f'faction_{faction}')

            # v3.98★ 比对逻辑
            name = info.get('name')
            desc = str(info.get('desc') or '')
            if not desc and info.get('skill'):
                desc = IDMAP.get_chess_skills_desc(info.get('skill'))
            
            if self.encyclopedia and name in self.encyclopedia:
                wiki = self.encyclopedia[name]
                # 比对 描述/势力/费用
                if (desc != str(wiki.get('effect') or wiki.get('desc') or '') or
                    str(faction) != str(wiki.get('faction') or '') or
                    str(info.get('cost') or '') != str(wiki.get('cost') or '')):
                    tags.append('diff')
            elif self.encyclopedia:
                # 不在百科全书里，也算 diff (缺失)
                tags.append('diff')

            tv.insert('', 'end', iid=str(gid), values=(
                str(gid),
                str(info.get('name') or '')[:24],
                str(info.get('type') or ('锦囊/道具' if info.get('is_spell_or_prop') else '武将')),
                info.get('cost', ''),
                info.get('star', info.get('level', '')),
                faction,
                info.get('job', ''),
                info.get('gender', 'unknown'),
                desc[:120],
                ', '.join([str(t) for t in (info.get('tags') or [])])[:120],
            ), tags=tuple(tags))

    def _active_list(self):
        return self._whitelist if self._list_mode.get() == 'white' else self._blacklist

    def _refresh_right_heading(self):
        mode = self._list_mode.get()
        self._right_frame.config(text=' 📋 白名单 (要合成/要上场的武将) ' if mode == 'white' else ' 📋 黑名单 (优先遣散的武将) ')
        self.refresh_right()

    def refresh_right(self):
        tv = self._right_tv
        for i in tv.get_children(): tv.delete(i)
        lst = self._active_list()
        for rank, gid in enumerate(lst, 1):
            info = self.hero_db.get(gid, {})
            
            tags = []
            if info.get('is_spell_or_prop'):
                tags.append('spell')
            
            faction = str(info.get('race_or_camp') or info.get('race') or '')
            if faction in ('汉', '魏', '蜀', '吴', '群', '袁'):
                tags.append(f'faction_{faction}')

            # v3.98★ 比对逻辑
            name = info.get('name')
            desc = str(info.get('desc') or '')
            if not desc and info.get('skill'):
                desc = IDMAP.get_chess_skills_desc(info.get('skill'))
            
            if self.encyclopedia and name in self.encyclopedia:
                wiki = self.encyclopedia[name]
                if (desc != str(wiki.get('effect') or wiki.get('desc') or '') or
                    str(faction) != str(wiki.get('faction') or '') or
                    str(info.get('cost') or '') != str(wiki.get('cost') or '')):
                    tags.append('diff')
            elif self.encyclopedia:
                tags.append('diff')

            tv.insert('', 'end', iid=f'{self._list_mode.get()}_{rank}', values=(
                rank, str(gid),
                str(info.get('name') or '')[:24],
                str(info.get('type') or ('锦囊/道具' if info.get('is_spell_or_prop') else '武将')),
                info.get('cost', ''),
                info.get('star', info.get('level', '')),
                desc[:120],
                ', '.join([str(t) for t in (info.get('tags') or [])])[:180],
            ), tags=tuple(tags))

    # ---------- 增删/排序 ----------
    def _add_selected(self):
        target = self._active_list()
        for sel in self._cand_tv.selection():
            gid = str(sel)
            if gid not in target: target.append(gid)
        self.refresh_candidates()
        self.refresh_right()
        self._emit_change()

    def _add_all_filtered(self):
        target = self._active_list()
        for iid in self._cand_tv.get_children():
            gid = str(iid)
            if gid not in target: target.append(gid)
        self.refresh_candidates()
        self.refresh_right()
        self._emit_change()

    def _remove_selected(self):
        target = self._active_list()
        for sel in self._right_tv.selection():
            try:
                # v3.98★ 移除逻辑: 仅从名单移除, 回到候选池
                rank = int(sel.split('_')[-1])
                if 1 <= rank <= len(target): target.pop(rank - 1)
            except (IndexError, ValueError):
                _SIL.note("ui/widgets.py:1012", "rank = int(sel.split('_')[-1])")
        self.refresh_candidates()
        self.refresh_right()
        self._emit_change()

    def _hide_selected(self):
        """v3.98★ 隐藏逻辑: 将选中的 ID 加入隐藏集, 不再出现在候选池"""
        for sel in self._cand_tv.selection():
            self._hidden_ids.add(str(sel))
        self.refresh_candidates()

    def _restore_hidden_dialog(self):
        """v3.98★ 恢复对话框: 选择性恢复隐藏的武将"""
        if not self._hidden_ids:
            messagebox.showinfo("提示", "当前没有已隐藏的武将。")
            return

        top = tk.Toplevel(self)
        top.title("恢复隐藏武将")
        top.geometry("600x450")
        top.transient(self)
        top.grab_set()

        lbl = tk.Label(top, text="请选择要恢复的武将 (支持多选):", pady=10)
        lbl.pack()

        # 列表
        cols = ('goodsID', 'name', 'cost', 'star', 'tags')
        tv = ttk.Treeview(top, columns=cols, show='headings', selectmode='extended')
        for c, w, head in [
                ('goodsID', 80, 'ID'), ('name', 120, '姓名'),
                ('cost', 50, '费'), ('star', 50, '星'), ('tags', 200, '标签')]:
            tv.heading(c, text=head)
            tv.column(c, width=w, anchor='w')
        
        vsb = ttk.Scrollbar(top, orient='vertical', command=tv.yview)
        tv.configure(yscrollcommand=vsb.set)
        tv.pack(side='left', fill='both', expand=True, padx=(10, 0), pady=10)
        vsb.pack(side='left', fill='y', pady=10, padx=(0, 10))

        # 填充数据
        for gid in sorted(self._hidden_ids):
            info = self.hero_db.get(gid, {})
            tv.insert('', 'end', iid=str(gid), values=(
                str(gid),
                str(info.get('name') or '未知'),
                info.get('cost', '-'),
                info.get('star', '-'),
                ', '.join([str(t) for t in (info.get('tags') or [])])
            ))

        btn_frm = ttk.Frame(top)
        btn_frm.pack(fill='x', pady=10)

        def _do_restore(all_mode=False):
            if all_mode:
                self._hidden_ids.clear()
            else:
                sels = tv.selection()
                if not sels:
                    messagebox.showwarning("警告", "请先选择要恢复的项")
                    return
                for s in sels:
                    if s in self._hidden_ids:
                        self._hidden_ids.remove(s)
            
            self.refresh_candidates()
            top.destroy()
            messagebox.showinfo("成功", "已恢复选定的武将到候选池")

        ttk.Button(btn_frm, text="恢复选中项", command=lambda: _do_restore(False)).pack(side='left', padx=20, expand=True)
        ttk.Button(btn_frm, text="全部恢复", command=lambda: _do_restore(True)).pack(side='left', padx=20, expand=True)
        ttk.Button(btn_frm, text="取消", command=top.destroy).pack(side='left', padx=20, expand=True)

    def _push_selected_to_wiki(self):
        """v3.98★ 候选池推送逻辑: 支持选中项或全部可见项"""
        sel = self._cand_tv.selection()
        items = []
        if not sel:
            visible_ids = self._cand_tv.get_children()
            if not visible_ids:
                messagebox.showwarning("提示", "候选池当前为空。")
                return
            if not messagebox.askyesno("推送全部可见", f"未选中行，是否将候选池中全部 {len(visible_ids)} 条可见项推送到百科全书？"):
                return
            target_ids = visible_ids
        else:
            target_ids = sel
            
        for gid in target_ids:
            gid_s = str(gid)
            if gid_s in self.hero_db:
                item = dict(self.hero_db[gid_s])
                item['src_id'] = gid_s
                items.append(item)
        self._execute_push(items)

    def _push_list_to_wiki(self):
        """v3.98★ 右侧名单推送逻辑: 将当前白名单/黑名单中的武将推送到百科全书"""
        target_list = self._active_list()
        if not target_list:
            messagebox.showwarning("提示", "当前名单（白/黑名单）为空。")
            return
        sel = self._right_tv.selection()
        items = []
        if not sel:
            if not messagebox.askyesno("推送名单全部", f"未选中行，是否将当前整个名单（共 {len(target_list)} 条）推送到百科全书？"):
                return
            target_ids = target_list
        else:
            target_ids = []
            for s in sel:
                vals = self._right_tv.item(s, 'values')
                if len(vals) > 1: target_ids.append(str(vals[1]))
        
        for gid in target_ids:
            gid_s = str(gid)
            if gid_s in self.hero_db:
                item = dict(self.hero_db[gid_s])
                item['src_id'] = gid_s
                items.append(item)
        self._execute_push(items)

    def _execute_push(self, items):
        if not items: return
        if self.push_callback:
            self.push_callback(items)
        else:
            # 兜底寻找 App 层的推送回调
            app = self.winfo_toplevel()
            if hasattr(app, '_push_raw_to_encyclopedia'):
                app._push_raw_to_encyclopedia(items)
            else:
                messagebox.showerror("错误", "未找到推送回调接口，请检查初始化配置")

    def _unhide_all(self):
        """v3.98★ 恢复逻辑: 清空隐藏集 (保留作为快捷方式或内部调用)"""
        self._hidden_ids.clear()
        self.refresh_candidates()

    def _on_cand_double_click(self):
        """v3.98★ 双击编辑对话框 (增加类型字段 & 布局优化)"""
        sel = self._cand_tv.selection()
        if not sel: return
        gid = str(sel[0])
        info = self.hero_db.get(gid, {})
        
        top = tk.Toplevel(self)
        top.title(f"编辑武将信息 (ID: {gid})")
        top.geometry("500x650") # 增大窗口
        top.transient(self)
        top.grab_set()

        # 使用 Canvas + Scrollbar 实现滚动，确保按钮可见
        canvas = tk.Canvas(top, highlightthickness=0)
        vsb = ttk.Scrollbar(top, orient="vertical", command=canvas.yview)
        scroll_frame = ttk.Frame(canvas)
        
        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll_frame, anchor="nw", width=480)
        canvas.configure(yscrollcommand=vsb.set)
        
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        # 字段配置
        fields = [
            ("goodsID (只读)", "goodsID", True),
            ("武将姓名", "name", False),
            ("类型 (武将/锦囊/道具)", "type", False),
            ("费用", "cost", False),
            ("星级", "star", False),
            ("阵营", "race_or_camp", False),
            ("职业", "job", False),
            ("性别", "gender", False),
            ("标签 (逗号分隔)", "tags", False),
        ]
        
        entries = {}
        for i, (label, key, readonly) in enumerate(fields):
            tk.Label(scroll_frame, text=label).pack(pady=(10, 0), padx=20, anchor='w')
            val = info.get(key, "")
            if key == "type" and not val:
                val = "锦囊/道具" if info.get('is_spell_or_prop') else "武将"
            if key == "tags" and isinstance(val, list):
                val = ",".join(map(str, val))
            
            var = tk.StringVar(value=str(val))
            e = ttk.Entry(scroll_frame, textvariable=var, state='readonly' if readonly else 'normal')
            e.pack(fill='x', padx=20, pady=2)
            entries[key] = var

        tk.Label(scroll_frame, text="技能描述").pack(pady=(10, 0), padx=20, anchor='w')
        txt_desc = tk.Text(scroll_frame, height=8)
        txt_desc.pack(fill='x', padx=20, pady=2)
        
        desc = info.get("desc", "")
        if not desc and info.get('skill'):
            desc = IDMAP.get_chess_skills_desc(info.get('skill'))
        txt_desc.insert('1.0', desc)

        def _save():
            new_info = info.copy()
            for key, var in entries.items():
                if key == "goodsID": continue
                val = var.get().strip()
                if key == "tags":
                    new_info[key] = [t.strip() for t in val.split(",") if t.strip()]
                else:
                    new_info[key] = val
            new_info["desc"] = txt_desc.get('1.0', 'end').strip()
            
            # 同步 type 到 is_spell_or_prop
            if "锦囊" in new_info.get("type", "") or "道具" in new_info.get("type", ""):
                new_info["is_spell_or_prop"] = True
            else:
                new_info["is_spell_or_prop"] = False
            
            self.hero_db[gid] = new_info
            self.refresh_candidates()
            self._emit_change(force_save=True) 
            top.destroy()
            messagebox.showinfo("成功", f"武将 {gid} 信息已更新并保存")

        # 底部按钮区 (不随滚动条滚动)
        btn_f = ttk.Frame(top)
        btn_f.pack(fill='x', side='bottom', pady=10)
        ttk.Button(btn_f, text="💾 保存修改", command=_save, width=20).pack()

    def _move(self, delta):
        target = self._active_list()
        sel = self._right_tv.selection()
        if not sel: return
        ranks = sorted([int(s.split('_')[-1]) for s in sel])
        if delta > 0: ranks = reversed(ranks)
        for r in ranks:
            idx = r - 1
            ni = idx + delta
            if 0 <= ni < len(target):
                target[idx], target[ni] = target[ni], target[idx]
        self.refresh_right()
        self._emit_change()

    def _clear_list(self):
        if not messagebox.askyesno('确认清空', f'清空{"白名单" if self._list_mode.get()=="white" else "黑名单"}?'):
            return
        if self._list_mode.get() == 'white': self._whitelist[:] = []
        else: self._blacklist[:] = []
        self.refresh_candidates()
        self.refresh_right()
        self._emit_change()

    # ---------- 公开 API ----------
    def set_heroes(self, hero_db):
        """hero_db: {goodsID: {name,cost,star,race_or_camp,job,gender,tags}}"""
        # v3.98★ 非破坏性合并: 如果已有该 ID 且包含用户修改字段, 则保留
        new_db = OrderedDict(hero_db or {})
        for gid, old_info in self.hero_db.items():
            if gid in new_db:
                # 合并逻辑: 优先保留当前内存中的字段 (可能被用户编辑过)
                merged = new_db[gid].copy()
                merged.update(old_info)
                new_db[gid] = merged
            else:
                # 网页里没发现, 但内存里有的(可能是旧数据), 保留
                new_db[gid] = old_info
        
        self.hero_db = new_db
        self.refresh_candidates()
        self.refresh_right()

    def set_change_callback(self, cb):
        self._on_change = cb

    def get_whitelist(self):
        return list(self._whitelist)

    def get_blacklist(self):
        return list(self._blacklist)

    def set_lists(self, whitelist=None, blacklist=None):
        if isinstance(whitelist, list): self._whitelist = [str(x) for x in whitelist]
        if isinstance(blacklist, list): self._blacklist = [str(x) for x in blacklist]
        self.refresh_candidates()
        self.refresh_right()

    def export_patch(self):
        """v3.98★ 扁平化导出: 返回完整的 hero_picker.json 结构。"""
        # 将 heroes 转换为列表存储
        heroes_list = []
        spells_list = []
        for gid, info in self.hero_db.items():
            item = dict(info)
            if item.get('is_spell_or_prop'):
                spells_list.append(item)
            else:
                heroes_list.append(item)

        return {
            'version': '3.98-Flat',
            'updated_at': __import__('time').strftime('%Y-%m-%d %H:%M:%S'),
            'whitelist': self._whitelist,
            'blacklist': self._blacklist,
            'hidden_ids': list(self._hidden_ids),
            'heroes': heroes_list,
            'spells': spells_list
        }

    def apply_patch(self, patch):
        """v3.98★ 扁平化加载: 支持新旧格式兼容。"""
        if not isinstance(patch, dict):
            return 0
        changed = 0
        
        # 1. 基础列表恢复
        # 兼容旧格式 __patch__ 包装
        p = patch.get('__patch__') or patch
        
        if isinstance(p.get('whitelist'), list):
            self._whitelist = [str(x) for x in p['whitelist']]
            changed += 1
        if isinstance(p.get('blacklist'), list):
            self._blacklist = [str(x) for x in p['blacklist']]
            changed += 1
        if isinstance(p.get('hidden_ids'), list):
            self._hidden_ids = set(str(x) for x in p['hidden_ids'])
            changed += 1
            
        # 2. 数据模型合并
        # A. 新格式: heroes / spells 两张列表
        #    v4.42★ 修两个让"导入"看起来像"只导入了黑名单"的 bug:
        #      ① 原来判的是 `"chessID" in h`, 但 export_patch 写出去的 hero_db 条目主键是
        #         goodsID(候选池/编辑框里用的就是 goodsID) → 这个条件恒为假,
        #         于是【武将数据一条都进不来】, 只有 whitelist/blacklist/hidden_ids 生效。
        #         用户看到的正是: "导入咋只导入了黑名单里面?"
        #      ② 只读了 patch['heroes'], 根本没读 patch['spells'] → 锦囊/道具也全丢。
        #    取值顺序按"哪个键真的存在"来, 不再假设只有 chessID。
        #    合并而非整体覆盖: 手写的补丁可能只带几个字段(比如只改 tags), 覆盖会把
        #    别的字段抹掉; 合并则"文件里有的字段生效、没有的保留"。整份 round-trip
        #    (导出后原样导入)两种做法等价。
        for key in ('heroes', 'spells'):
            for h in (patch.get(key) or []):
                if not isinstance(h, dict):
                    continue
                gid = str(h.get("goodsID") or h.get("chessID") or h.get("spellID")
                          or h.get("itemID") or "").strip()
                if not gid:
                    continue
                cur = self.hero_db.get(gid)
                if isinstance(cur, dict):
                    merged = dict(cur)
                    merged.update(h)
                    self.hero_db[gid] = merged
                else:
                    self.hero_db[gid] = h
                changed += 1
        
        # B. 兼容旧格式: 从 hero_overrides 加载
        over = p.get('hero_overrides') or {}
        if isinstance(over, dict):
            for gid, fields in over.items():
                gid_s = str(gid)
                cur = self.hero_db.setdefault(gid_s, {})
                if not isinstance(cur, dict): continue
                cur.update(fields)
                changed += 1
                
        self.refresh_candidates()
        self.refresh_right()
        self._emit_change()
        return changed

    # ---------- IO ----------
    def _load_patch_dialog(self):
        path = filedialog.askopenfilename(title='打开hero_picker.json/用户补丁',
                                          filetypes=[('JSON', '*.json'), ('所有', '*.*')])
        if path: self.load_user_patch(path)

    def _save_patch_dialog(self):
        path = filedialog.asksaveasfilename(title='保存用户补丁',
                                            defaultextension='.json',
                                            initialfile='hero_picker.json',
                                            filetypes=[('JSON', '*.json')])
        if path: self.save_user_patch(path)

    def load_user_patch(self, path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                p = json.load(f)
            n = self.apply_patch(p)
            messagebox.showinfo('导入成功', f'已加载补丁 {os.path.basename(path)}\n修改项数: {n}')
        except Exception as e:
            messagebox.showerror('导入失败', str(e))

    def save_user_patch(self, path):
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(self.export_patch(), f, ensure_ascii=False, indent=2)
            messagebox.showinfo('保存成功', os.path.basename(path))
        except Exception as e:
            messagebox.showerror('保存失败', str(e))

    def _emit_change(self, force_save=False):
        try:
            if self._on_change: self._on_change({
                'whitelist': list(self._whitelist),
                'blacklist': list(self._blacklist),
                'force_save': force_save
            })
        except Exception:
            _SIL.note("ui/widgets.py:1422", "if self._on_change: self._on_change({ 'whiteli")
