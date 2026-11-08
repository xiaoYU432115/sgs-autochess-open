# -*- coding: utf-8 -*-
"""奖励配置 tab —— v4.194★ 三选一奖励优先级（有序列表编辑）

对齐文档 §4.1 / §2.6 / §8（自走棋助手-重构版/docs/CARDS_SPELLS_EQUIP_SELECT3.md）：
  · 武将 / 锦囊各一条【有序列表】：添加 / 上移 / 下移 / 删除，靠前优先；
  · 锦囊用下拉选（带名字），落库只存局内 ID（S11=311xxx，同名多阶必须按 ID）；
  · 特殊锦囊（借尸还魂/草船借箭用完 → 武将三选一强制默认名单）可配置；
  · 名单外锦囊：桥端记进 __sgsUnknownSpells（可诊断），本页可拉取查看，
    用户显式加进名单 —— 不自动学习、不把旧编号机械套用。

数据层在 core/reward_priority.py：
  · 本地权威: data/reward_priority.json（多开隔离）；
  · 下发: set_triple_reward_priority → 游戏页 localStorage['sgs_triple_reward_priority']，
    剧本 pickOne 未指定 pick 时按它选（bridge _scOpPickOne → _scDefaultRewardPick）。

配套指令(bridge/sgs_bridge.user.js)：
  get_triple_reward_priority / set_triple_reward_priority
  get_unknown_spells / clear_unknown_spells
"""
import json
import tkinter as tk
from tkinter import ttk, messagebox

from ui.widgets import ThemeMixin
from ui.app import (BG, FG, DIM, RED, GOLD, BLUE, BTN, ACT,
                    SEL_BG, SEL_FG, FH)
from core import reward_priority as R


class _OrderList(tk.Frame):
    """一列有序条目: Listbox + 上移/下移/删除/清空。条目 = (展示文本, 值)。"""

    def __init__(self, master, height=7):
        super().__init__(master, bg=BG)
        self._vals = []          # 与 Listbox 行一一对应的值(名字或 ID)
        self.lb = tk.Listbox(self, bg=BTN, fg=FG, font=(FH, 9), height=height,
                             selectbackground=SEL_BG, selectforeground=SEL_FG,
                             exportselection=False)
        self.lb.pack(side="left", fill="both", expand=True)
        sb = tk.Frame(self, bg=BG)
        sb.pack(side="left", padx=(4, 0))
        for text, cmd in (("↑ 上移", lambda: self.move(-1)),
                          ("↓ 下移", lambda: self.move(1)),
                          ("删除", self.delete_sel),
                          ("清空", lambda: self.set_items([]))):
            tk.Button(sb, text=text, command=cmd, bg=BTN, fg=FG, activebackground=BTN,
                      activeforeground=FG, font=(FH, 9), relief="flat", padx=8,
                      cursor="hand2").pack(anchor="w", pady=1)

    def items(self):
        return list(self._vals)

    def set_items(self, vals, labels=None):
        self._vals = list(vals or [])
        labels = labels or []
        self.lb.delete(0, "end")
        for i, v in enumerate(self._vals):
            self.lb.insert("end", labels[i] if i < len(labels) else str(v))

    def add(self, val, label=None):
        val = str(val).strip()
        if not val or val in self._vals:
            return False
        self._vals.append(val)
        self.lb.insert("end", label if label else val)
        return True

    def _redraw(self):
        self.set_items(self._vals)

    def move(self, d):
        sel = self.lb.curselection()
        if not sel:
            return
        i = sel[0]
        j = i + d
        if j < 0 or j >= len(self._vals):
            return
        self._vals[i], self._vals[j] = self._vals[j], self._vals[i]
        self._redraw()
        self.lb.selection_set(j)

    def delete_sel(self):
        sel = self.lb.curselection()
        if not sel:
            return
        del self._vals[sel[0]]
        self._redraw()


class RewardConfigTabBuilder(ThemeMixin):
    def __init__(self, app):
        self.app = app
        self.root = app.root

    # ---------------------------------------------------------------- 工具
    @staticmethod
    def _split_names(s):
        out, seen = [], set()
        for part in (s or "").replace("，", ",").replace(",", "\n").split("\n"):
            v = part.strip()
            if v and v not in seen:
                seen.add(v)
                out.append(v)
        return out

    @staticmethod
    def _split_ids(s):
        out, seen = [], set()
        for part in (s or "").replace("，", ",").replace("\n", ",").split(","):
            p = part.strip()
            if not p:
                continue
            try:
                n = int(p)
            except Exception:
                continue
            if n > 0 and n not in seen:
                seen.add(n)
                out.append(n)
        return out

    @staticmethod
    def _parse_spell_token(tok):
        """'311034 美人计·五' / '311034' → int；解析不了返回 0。"""
        tok = (tok or "").strip()
        if not tok:
            return 0
        try:
            return int(tok.split()[0])
        except Exception:
            return 0

    def _connected(self):
        try:
            return bool(self.app.ops and self.app.ops.connected())
        except Exception:
            return False

    def _spell_choices(self):
        """下拉选项: 默认名单顺序在前, 其余本季锦囊按名排后; 不认识的 ID 也可手输。"""
        cat = R.load_spell_catalog()
        choices, seen = [], set()
        for sid in R.DEFAULT_SPELLS:
            choices.append(R.spell_label(sid))
            seen.add(sid)
        rest = sorted((k for k in cat if k not in seen), key=lambda k: cat[k])
        choices.extend("%d %s" % (k, cat[k]) for k in rest)
        return choices

    def _labels_for_spells(self, ids):
        cat = R.load_spell_catalog()
        return ["%d %s" % (sid, cat.get(sid, "（未识别·可维护）")) for sid in ids]

    # ---------------------------------------------------------------- 构建
    def _build(self):
        frame = self.app._make_scrollable_tab(self.app.tab_reward_cfg, padx=(10, 0), pady=8)
        tk.Label(frame, text="三选一奖励优先级（文档 §4.1/§4.2/§8）", bg=BG, fg=GOLD,
                 font=(FH, 14, "bold")).pack(anchor="w")
        tk.Label(frame, text=("武将按名称匹配、锦囊按局内 ID 精确匹配（S11=311xxx），靠前优先；"
                              "剧本 pickOne 未指定挑谁时也按这套名单。保存=写 data/reward_priority.json，"
                              "下发=写入游戏页。名单外锦囊会记进桥端可诊断区，拉取后显式维护，不自动学习。"),
                 bg=BG, fg=DIM, font=(FH, 9), wraplength=980, justify="left").pack(anchor="w", pady=(2, 6))

        body = tk.Frame(frame, bg=BG)
        body.pack(fill="x")

        # ===== 武将有序列表 =====
        gw = tk.LabelFrame(body, text="武将优先级（名字，靠前优先）", bg=BG, fg=GOLD,
                           bd=1, relief="solid", font=(FH, 9))
        gw.pack(side="left", fill="both", expand=True, padx=(0, 6))
        self.chess_list = _OrderList(gw)
        self.chess_list.pack(fill="both", expand=True, padx=8, pady=4)
        rw = tk.Frame(gw, bg=BG)
        rw.pack(fill="x", padx=8, pady=(0, 4))
        tk.Label(rw, text="武将名:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left")
        self.chess_add_var = tk.StringVar()
        tk.Entry(rw, textvariable=self.chess_add_var, bg=BTN, fg=FG, insertbackground=FG,
                 font=(FH, 9), width=14).pack(side="left", padx=(4, 4))
        tk.Button(rw, text="添加", command=self._chess_add, bg=ACT, fg=GOLD,
                  activebackground=ACT, activeforeground=FG, font=(FH, 9), relief="flat",
                  padx=10, cursor="hand2").pack(side="left")

        # ===== 锦囊有序列表 =====
        gs = tk.LabelFrame(body, text="锦囊优先级（局内 ID，靠前优先；同名多阶按 ID 区分）",
                           bg=BG, fg=GOLD, bd=1, relief="solid", font=(FH, 9))
        gs.pack(side="left", fill="both", expand=True)
        self.spell_list = _OrderList(gs)
        self.spell_list.pack(fill="both", expand=True, padx=8, pady=4)
        rs = tk.Frame(gs, bg=BG)
        rs.pack(fill="x", padx=8, pady=(0, 4))
        tk.Label(rs, text="添加:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left")
        self.spell_add_var = tk.StringVar()
        self.spell_add_cb = ttk.Combobox(rs, textvariable=self.spell_add_var,
                                         values=self._spell_choices(), font=(FH, 9), width=22)
        self.spell_add_cb.pack(side="left", padx=(4, 4))
        tk.Button(rs, text="添加", command=self._spell_add, bg=ACT, fg=GOLD,
                  activebackground=ACT, activeforeground=FG, font=(FH, 9), relief="flat",
                  padx=10, cursor="hand2").pack(side="left")

        # ===== 特殊锦囊 / 隐藏池 =====
        gx = tk.LabelFrame(frame, text="特殊锦囊与隐藏池", bg=BG, fg=GOLD, bd=1, relief="solid", font=(FH, 9))
        gx.pack(fill="x", pady=(8, 8))
        rx1 = tk.Frame(gx, bg=BG)
        rx1.pack(fill="x", padx=8, pady=4)
        tk.Label(rx1, text="用完触发【武将】三选一强制默认名单的锦囊 ID(逗号分隔, 默认 311019 借尸还魂/311002 草船借箭):",
                 bg=BG, fg=FG, font=(FH, 9)).pack(side="left")
        self.special_var = tk.StringVar()
        tk.Entry(rx1, textvariable=self.special_var, bg=BTN, fg=FG, insertbackground=FG,
                 font=(FH, 9), width=20).pack(side="left", padx=(6, 0))
        tk.Label(rx1, text="（311061 天降神兵·五=装备三选一, 走装备算法, 无需加这里）",
                 bg=BG, fg=DIM, font=(FH, 9)).pack(side="left", padx=(6, 0))
        rx2 = tk.Frame(gx, bg=BG)
        rx2.pack(fill="x", padx=8, pady=4)
        tk.Label(rx2, text="隐藏武将名:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left")
        self.hchess_var = tk.StringVar()
        tk.Entry(rx2, textvariable=self.hchess_var, bg=BTN, fg=FG, insertbackground=FG,
                 font=(FH, 9), width=24).pack(side="left", padx=(6, 0))
        tk.Label(rx2, text="隐藏锦囊ID:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left", padx=(12, 0))
        self.hspell_var = tk.StringVar()
        tk.Entry(rx2, textvariable=self.hspell_var, bg=BTN, fg=FG, insertbackground=FG,
                 font=(FH, 9), width=24).pack(side="left", padx=(6, 0))
        tk.Label(rx2, text="（隐藏=暂不自动选, 仍可服务器首项兜底）", bg=BG, fg=DIM, font=(FH, 9)).pack(side="left", padx=(6, 0))

        # ===== 方案 groups(可选) =====
        gg = tk.LabelFrame(frame, text="方案 groups（可选；每行一个方案，格式 名称|武将,武将|锦囊ID,锦囊ID）",
                           bg=BG, fg=GOLD, bd=1, relief="solid", font=(FH, 9))
        gg.pack(fill="x", pady=(0, 8))
        self.groups_text = tk.Text(gg, bg=BTN, fg=FG, font=(FH, 9), height=3, wrap="word")
        self.groups_text.pack(fill="x", padx=8, pady=4)
        rg = tk.Frame(gg, bg=BG)
        rg.pack(fill="x", padx=8, pady=(0, 4))
        tk.Label(rg, text="生效方案(留空=默认方案):", bg=BG, fg=FG, font=(FH, 9)).pack(side="left")
        self.active_var = tk.StringVar()
        tk.Entry(rg, textvariable=self.active_var, bg=BTN, fg=FG, insertbackground=FG,
                 font=(FH, 9), width=24).pack(side="left", padx=(6, 0))

        # ===== 未识别锦囊（可诊断区） =====
        gu = tk.LabelFrame(frame, text="未识别锦囊（名单外候选的真实 ID+名字, 拉取后显式维护进上面名单）",
                           bg=BG, fg=GOLD, bd=1, relief="solid", font=(FH, 9))
        gu.pack(fill="x", pady=(0, 8))
        self.unknown_text = tk.Text(gu, bg=BTN, fg=FG, font=(FH, 9), height=4, wrap="word")
        self.unknown_text.pack(fill="x", padx=8, pady=4)

        # ===== 操作 =====
        ctrl = tk.Frame(frame, bg=BG)
        ctrl.pack(fill="x", pady=(0, 8))
        for text, cmd in (("从页面读取", self._load_page),
                          ("从配置文件载入", self._load_file),
                          ("保存到配置文件", self._save_file),
                          ("下发到游戏页", self._push_page),
                          ("拉取未识别锦囊", self._pull_unknown),
                          ("清除未识别记录", self._clear_unknown)):
            tk.Button(ctrl, text=text, command=cmd, bg=BTN, fg=FG, activebackground=BTN,
                      activeforeground=FG, font=(FH, 9), relief="flat", padx=10,
                      cursor="hand2").pack(side="left", padx=(0, 6))
        tk.Button(ctrl, text="保存并下发", command=self._save, bg=ACT, fg=GOLD,
                  activebackground=ACT, activeforeground=FG, font=(FH, 9), relief="flat",
                  padx=12, cursor="hand2").pack(side="left")
        self.status_var = tk.StringVar(value="未读取")
        tk.Label(ctrl, textvariable=self.status_var, bg=BG, fg=BLUE, font=(FH, 9)).pack(side="left", padx=(10, 0))

        self._load_file()

    # ---------------------------------------------------------------- 列表操作
    def _chess_add(self):
        name = self.chess_add_var.get().strip()
        if name and self.chess_list.add(name):
            self.chess_add_var.set("")

    def _spell_add(self):
        sid = self._parse_spell_token(self.spell_add_var.get())
        if sid:
            if self.spell_list.add(str(sid), R.spell_label(sid)):
                self.spell_add_var.set("")
        else:
            self.status_var.set("锦囊 ID 解析失败（要 311xxx 这样的数字）")

    # ---------------------------------------------------------------- 读取
    def _apply(self, chess, spells, special, hchess, hspell, groups, active):
        self.chess_list.set_items(chess)
        self.spell_list.set_items([str(x) for x in spells], self._labels_for_spells(spells))
        self.special_var.set(", ".join(str(x) for x in (special or [])))
        self.hchess_var.set(", ".join(hchess or []))
        self.hspell_var.set(", ".join(str(x) for x in (hspell or [])))
        lines = ["%s|%s|%s" % (g.get("name", ""), ",".join(g.get("chessNames") or []),
                               ",".join(str(x) for x in (g.get("spellIds") or [])))
                 for g in (groups or [])]
        self.groups_text.delete("1.0", "end")
        self.groups_text.insert("1.0", "\n".join(lines))
        self.active_var.set("" if active in (None, "") else str(active))

    def _load_file(self):
        cfg = R.load()
        dp = cfg.get("defaultPriority") or {}
        self._apply(dp.get("chessNames") or [], dp.get("spellIds") or [],
                    cfg.get("specialSpellIds") or [], cfg.get("hiddenChessNames") or [],
                    cfg.get("hiddenSpellIds") or [], cfg.get("groups") or [],
                    cfg.get("activeGroupId"))
        self.status_var.set("已从配置文件载入（%s）" % R.CONFIG_PATH)

    def _load_page(self):
        if not self._connected():
            self.status_var.set("未连接游戏页（可先从配置文件载入）")
            return
        try:
            res = self.app.ops.send_recv("get_triple_reward_priority", timeout=3.0)
        except Exception as e:
            self.status_var.set("读取失败: %s" % str(e)[:60])
            return
        try:
            d = json.loads(res) if isinstance(res, str) else res
            cfg = (d or {}).get("config") or {}
        except Exception:
            self.status_var.set("返回非 JSON")
            return
        self._apply(cfg.get("chess") or [], cfg.get("spell") or [],
                    cfg.get("specialSpellIds") or [], cfg.get("hiddenChess") or [],
                    cfg.get("hiddenSpell") or [], cfg.get("groups") or [],
                    cfg.get("activeGroupId"))
        self.status_var.set("已从游戏页读取（生效方案: %s）" % (cfg.get("activeGroupId") or "默认"))

    # ---------------------------------------------------------------- 收集
    def _collect(self):
        """从界面收集 → 归一化后的配置 dict（含 spellCatalog）。"""
        chess = [str(x) for x in self.chess_list.items()]
        spells = R._norm_ids(self.spell_list.items())
        groups = []
        for line in self.groups_text.get("1.0", "end").split("\n"):
            line = line.strip()
            if not line:
                continue
            parts = line.split("|")
            name = parts[0].strip()
            if not name:
                continue
            groups.append({"id": name, "name": name,
                           "chessNames": self._split_names(parts[1]) if len(parts) > 1 else [],
                           "spellIds": self._split_ids(parts[2]) if len(parts) > 2 else []})
        cfg = R.load()                     # 保留文件里的 spellCatalog 等未编辑字段
        cfg["defaultPriority"] = {"chessNames": chess, "spellIds": spells}
        cfg["specialSpellIds"] = self._split_ids(self.special_var.get())
        cfg["hiddenChessNames"] = self._split_names(self.hchess_var.get())
        cfg["hiddenSpellIds"] = self._split_ids(self.hspell_var.get())
        cfg["groups"] = groups
        cfg["activeGroupId"] = self.active_var.get().strip() or None
        return R.normalize(cfg)

    def _save_file(self):
        try:
            cfg = self._collect()
            R.save(cfg)
            self.status_var.set("已保存到配置文件（武将 %d / 锦囊 %d）"
                                % (len(cfg["defaultPriority"]["chessNames"]),
                                   len(cfg["defaultPriority"]["spellIds"])))
        except Exception as e:
            messagebox.showerror("保存失败", str(e))

    def _push_page(self):
        if not self._connected():
            messagebox.showinfo("提示", "先连接游戏页（配置文件本身已可保存）")
            return
        try:
            cfg = self._collect()
            self.app.ops.send("set_triple_reward_priority", cfg=R.to_bridge_cfg(cfg))
            self.status_var.set("已下发到游戏页（武将 %d / 锦囊 %d）"
                                % (len(cfg["defaultPriority"]["chessNames"]),
                                   len(cfg["defaultPriority"]["spellIds"])))
        except Exception as e:
            messagebox.showerror("下发失败", str(e))

    def _save(self):
        self._save_file()
        if self._connected():
            self._push_page()

    # ---------------------------------------------------------------- 未识别锦囊
    def _pull_unknown(self):
        if not self._connected():
            self.status_var.set("未连接游戏页")
            return
        try:
            res = self.app.ops.send_recv("get_unknown_spells", timeout=3.0)
        except Exception as e:
            self.status_var.set("拉取失败: %s" % str(e)[:60])
            return
        try:
            d = json.loads(res) if isinstance(res, str) else res
            unknown = (d or {}).get("unknown") or {}
        except Exception:
            self.status_var.set("返回非 JSON")
            return
        self.unknown_text.delete("1.0", "end")
        if not unknown:
            self.unknown_text.insert("1.0", "（暂无名单外锦囊记录）")
        else:
            cat = R.load_spell_catalog()
            lines = ["%s  %s%s" % (k, cat.get(int(k), ""), "（表外·需维护）" if int(k) not in cat else "")
                     for k in sorted(unknown, key=lambda x: int(x))]
            self.unknown_text.insert("1.0", "\n".join(lines))
        self.status_var.set("未识别锦囊 %d 条" % len(unknown))

    def _clear_unknown(self):
        if not self._connected():
            self.status_var.set("未连接游戏页")
            return
        try:
            self.app.ops.send_recv("clear_unknown_spells", timeout=3.0)
            self.unknown_text.delete("1.0", "end")
            self.unknown_text.insert("1.0", "（已清除）")
            self.status_var.set("已清除未识别记录")
        except Exception as e:
            self.status_var.set("清除失败: %s" % str(e)[:60])
