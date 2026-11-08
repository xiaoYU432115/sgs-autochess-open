# -*- coding: utf-8 -*-
"""v4.105★ 阵容推荐器 —— 吃上游那两张表, 回答两个"每次开局都要想一遍"的问题。

数据来源: B站 UP『耳目一曲』《【S10精忠报国】终端阵容解析》的配套表(已转成 json)
  · data/s10_lineups.json : 35 组【四势力组合】× 有毒/关键装备、无毒 两套后期转阵思路
  · data/tavern_odds.json : 各级营帐的刷新概率 / 商店张数 / 找指定卡的【期望刷新次数】

它能回答的
==========
 1. 我这局的四个势力 → 后期该转什么(有毒一套、没毒一套)?
 2. 我要找的那张卡是几星 → 在【几本】刷最省(期望次数最少)? 要准备多少金币?

之所以有用: 期望次数那张表有个反直觉的结论 —— 卡越高级不代表在本数越高的营帐越
好找(例: 陈珪在 4 本最好找, 5 本反而更难; 祢衡 6 本比 5 本容易)。以前只能凭感觉 D。
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")

# v4.110 修: 以前是 7 个, 【漏了"群"】。群是本季势力且卡最多(60 张 —— 南华老仙/贾诩/
#   蔡文姬/祢衡/左慈全在群), 每局四势力里必带群。上游 mod 那份也是 8 个:
#   refs/江海常流-一键遣散保留指定卡-v1.0.0.js:577
#       ["魏","蜀","吴","群","黄巾","汉","西凉","袁"]  —— 照它对齐。
FACTIONS = ["魏", "蜀", "吴", "群", "黄巾", "汉", "西凉", "袁"]

# v4.110★ 官方 faction 字段 → 界面显示名。这层映射以前【根本没有】, 是"势力对不上"的根因:
#   卡池用的是官方字段(群/吴/神/魏/蜀/晋/汉/袁), 界面用的是显示名(群/吴/袁/魏/蜀/黄巾/汉/西凉),
#   两边直接比就会得出"张梁是晋, 不是黄巾"这种自相矛盾的结果。
#   映射是照【各势力的高费卡】认的: 晋 = 张梁/张宝/管亥/马元义/卜巳/白绕(黄巾军);
#   袁 = 马腾/马超/马岱/庞德/李傕/郭汜/樊稠(西凉军); 神 = 袁绍/袁术/沮授/田丰/文丑(袁氏)。
FACTION_ALIAS = {"晋": "黄巾", "袁": "西凉", "神": "袁"}


def faction_display(f):
    """官方 faction 字段 → 界面显示名(晋→黄巾、袁→西凉、神→袁, 其余原样)。"""
    s = str(f or "").strip()
    return FACTION_ALIAS.get(s) or s
STAR_OF = {           # 关键卡 → 星级(表里的示例卡, 用来把"卡"换成"几星")
    "陈武": 1, "丁奉": 2, "陈珪": 3, "淳于琼": 4, "祢衡": 5,
}


def _current_season():
    """助手现在认的赛季号(与 csv_loader 同一份 data/season/season.json)。"""
    try:
        with open(os.path.join(DATA_DIR, "season", "season.json"), "r", encoding="utf-8") as f:
            s = str((json.load(f) or {}).get("season") or "").strip().lstrip("sS")
        return s or ""
    except Exception as e:
        print("[阵容推荐] 读赛季号失败(按 S10 处理): %s" % e, file=sys.stderr)
        return ""


def _save_json(path, obj):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        return True
    except Exception as e:
        print("[阵容推荐] 写 %s 失败: %s" % (path, e), file=sys.stderr)
        return False


def _load_json(name, data_dir=DATA_DIR):
    path = os.path.join(data_dir, name)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print("[阵容推荐] 读 %s 失败: %s" % (path, e), file=sys.stderr)
        return {}


class LineupAdvisor:
    """纯读表, 不联网、不改任何游戏数据。表读不到时所有方法返回空结果而不是抛异常。"""

    def __init__(self, data_dir=DATA_DIR):
        self.data_dir = data_dir
        self.season = _current_season()
        # v4.106★ 阵容表【按赛季】找: 换季后新表叫 lineups_s11.json / s11_lineups.json,
        #   找不到才回退 S10 那张 —— 但会标 fallback=True, 界面上必须写清"这是上赛季的表",
        #   免得拿着旧赛季的思路打新赛季(势力/卡池一变, 结论可能整个反过来)。
        self.lineups_file = self._pick_lineups_file()
        self.fallback = bool(self.season) and not (
            self.lineups_file and ("s%s" % self.season) in os.path.basename(self.lineups_file))
        self.lineups = (_load_json(os.path.basename(self.lineups_file), data_dir).get("lineups") or []) \
            if self.lineups_file else []
        self.odds = _load_json("tavern_odds.json", data_dir)
        self._pool = None      # 卡池(懒加载: 只在真要查卡时才读那 300 多 KB)
        self.pool_source = ""          # 卡池实际来自哪张表(界面/日志要显示)
        self.pool_fallback = False     # True = 用的不是本季表, 结论只能当参考

    # ---------------- 卡池(任意卡 → 星级/攻防, 站位也要用) ----------------
    def card_pool(self, force=False):
        """本季卡池 {卡ID: {name, atk, def, rank, ...}}。

        ★ v4.140 修(实测缺陷): 换到 S11 后 data/tavern_chess_s11.json 一直不存在,
          这里【静默回退】到 tavern_chess_s10.json —— 于是 econ_planner.tier_values()
          拿 S10 的身材算"该不该刷/升", 刷新顾问和阵容推荐跟着一起用错赛季的数据,
          而界面上一句提示都没有。现在三步:
            ① 有本季表 → 直接用;
            ② 没有 → 用 tools/build_tavern_pool.py 从官方赛季表【现生成一张】;
            ③ 生成也失败 → 才回退上赛季, 并且必须标 pool_fallback=True 让上层能提示。
        """
        if self._pool is None or force:
            pool = {}
            self.pool_fallback = False
            self.pool_source = ""
            want = "tavern_chess_s%s.json" % self.season if self.season else ""

            for nm in ([want] if want else []) + ["tavern_chess_s10.json"]:
                d = _load_json(nm, self.data_dir)
                if isinstance(d, dict) and d:
                    pool = d
                    self.pool_source = nm
                    break

            # 本季表不在(拿到的名字是 s10 兜底) → 试着从官方赛季表现生成一张
            if want and self.pool_source != want and self.season.isdigit():
                try:
                    import tools.build_tavern_pool as BP
                    dst = BP.build(self.season, verbose=False)
                    d = _load_json(os.path.basename(dst), self.data_dir)
                    if isinstance(d, dict) and d:
                        pool = d
                        self.pool_source = os.path.basename(dst)
                        print("[阵容推荐] 本季卡池表缺失 → 已从官方赛季表生成 %s(%d 条)"
                              % (self.pool_source, len(d)), file=sys.stderr)
                except Exception as e:
                    print("[阵容推荐] 本季卡池表生成失败(退回 S10 表, 结论仅供参考): %s: %s"
                          % (type(e).__name__, e), file=sys.stderr)

            if want and self.pool_source != want:
                self.pool_fallback = True

            self._pool = pool
        return self._pool

    def card_info(self, name):
        """卡名 → 卡池里那一行(完全匹配优先, 其次包含)。找不到返回 None。"""
        key = str(name or "").strip()
        if not key:
            return None
        pool = self.card_pool() or {}
        for _cid, v in pool.items():
            if not isinstance(v, dict):
                continue
            if str(v.get("name") or v.get("n") or "").strip() == key:
                return v
        for _cid, v in pool.items():
            if not isinstance(v, dict):
                continue
            if key and key in str(v.get("name") or v.get("n") or ""):
                return v
        return None

    def _pick_lineups_file(self):
        cands = []
        if self.season:
            cands += [os.path.join(self.data_dir, "lineups_s%s.json" % self.season),
                      os.path.join(self.data_dir, "s%s_lineups.json" % self.season)]
        cands.append(os.path.join(self.data_dir, "s10_lineups.json"))
        for p in cands:
            if os.path.isfile(p):
                return p
        return None

    # ---------------- 1. 势力组合 → 转阵思路 ----------------
    def match(self, factions):
        """完全匹配: 传入 4 个势力(顺序随意) → 那一行的两套方案。找不到返回 None。"""
        want = set(str(x).strip() for x in (factions or []) if str(x).strip())
        if not want:
            return None
        for row in self.lineups:
            if set(row.get("factions") or []) == want:
                return row
        # v4.110★ 群降级: 原表(B站那份)把"群"当固定席位【没写进组合名】—— 实测原表 41 行,
        #   含"群"的行 0 行, 35 组全是 魏/蜀/吴/汉/黄巾/西凉/袁 这七个里选四个。可本季群每局
        #   必有(南华老仙就是群), 于是玩家勾到含群的组合【必然精确匹配失败】 → 界面弹"这组
        #   势力没写出可识别的核心卡", 表现就是"点了没反应 —— 一个都出不来"。
        #   这里把群去掉, 拿剩下那几个去表里找【包含它们】的组合, 取差得最少的一条。
        #   打上 _qun_fallback: 上层要照实告诉用户"这是推的, 群没算进去", 不能装作原表写的。
        if "群" in want:
            rest = want - {"群"}
            if rest:
                cands = self.partial(sorted(rest))
                if cands:
                    row = dict(cands[0])
                    row["_qun_fallback"] = True
                    return row
        return None

    def partial(self, factions):
        """还没选满 4 个势力时: 列出【包含已选这些势力】的组合。"""
        want = set(str(x).strip() for x in (factions or []) if str(x).strip())
        if not want:
            return []
        out = []
        for row in self.lineups:
            fs = set(row.get("factions") or [])
            if want <= fs:
                out.append((len(fs - want), row))     # 差得越少排越前
        out.sort(key=lambda x: (x[0], x[1].get("key", "")))
        return [r for _, r in out]

    def recommend(self, factions):
        """给 UI 用: 返回 {exact, poison, clean, candidates}。candidates 为凑不满时的候选。"""
        ex = self.match(factions)
        # v4.110★ 候选也要先去群: 表里一行含"群"的都没有, 拿"群/魏/蜀/吴"去做子集匹配
        #   一条都出不来(群不在任何一行的 factions 里)。去掉群再找, 才能列出相关的那几组。
        want = set(str(x).strip() for x in (factions or []) if str(x).strip())
        rest = sorted(want - {"群"}) if "群" in want else sorted(want)
        cands = self.partial(rest)[:8] if rest else []
        res = {
            "exact": ex,
            "poison": (ex or {}).get("poison", ""),
            "clean": (ex or {}).get("clean", ""),
            # 降级来的(ex 带 _qun_fallback)照样把候选列出来 —— 用户要能看见"相关的都有哪几组"
            "candidates": ([] if (ex and not ex.get("_qun_fallback")) else cands),
        }
        return res

    # ---------------- 2. 找卡: 几本最省? 要多少钱? ----------------
    def expected_refreshes(self, level, star):
        """某级营帐刷出【指定某张 N 星卡】平均要刷几次。表里没有 → None(那本刷不出这个星)。"""
        try:
            row = (self.odds.get("expected_refreshes") or {}).get(str(int(level))) or {}
            v = row.get(str(int(star)))
            return float(v) if v is not None else None
        except Exception as e:
            print("[阵容推荐] 期望次数取值异常: %s" % e, file=sys.stderr)
            return None

    def best_level_for(self, star):
        """找这张星级的卡, 在哪本刷最省 → [(level, 期望次数), ...](次数升序)。"""
        out = []
        for lv in range(1, 7):
            n = self.expected_refreshes(lv, star)
            if n is not None:
                out.append((lv, n))
        out.sort(key=lambda x: x[1])
        return out

    def star_of(self, name):
        """卡名 → 星级。

        v4.107★ 以前只认表里的 5 张示例卡(陈武/丁奉/陈珪/淳于琼/祢衡) —— 输"张星彩"就回
        "认不出", 等于这功能只对 5 张卡有用。现在【先查卡池】: 372 张每行都带 rank,
        任意卡都能算; 查不到才回退那 5 个示例名(老表口径保留作兜底)。
        """
        info = self.card_info(name)
        if info is not None:
            try:
                return int(info.get("rank") or 0) or None
            except Exception as e:
                print("[阵容推荐] 星级取值异常(卡名=%s): %s" % (name, e), file=sys.stderr)
        for k, v in STAR_OF.items():
            if k in str(name or ""):
                return v
        return None

    def find_plan(self, name, refresh_cost=2, buy_cost=3, gold=None):
        """给一张卡名算"在哪本 D、大概刷几次、要准备多少钱"。

        refresh_cost: 刷一次几金币(默认 2, 丈八/刘焉等会变, 界面上可改)
        buy_cost    : 买到手那张卡几金币(默认 3)
        返回 dict; 认不出星级时 star=None, 只回表里的提示文案。
        """
        star = self.star_of(name)
        plan = {"name": name, "star": star, "levels": [], "best": None,
                "refresh_cost": refresh_cost, "buy_cost": buy_cost,
                "gold_need": None, "affordable": None, "tips": self.odds.get("tips") or []}
        if star is None:
            return plan
        plan["levels"] = self.best_level_for(star)
        if not plan["levels"]:
            return plan
        lv, times = plan["levels"][0]
        plan["best"] = {"level": lv, "times": times}
        cost = times * refresh_cost + buy_cost
        plan["gold_need"] = round(cost, 1)
        if gold is not None:
            try:
                plan["affordable"] = float(gold) >= cost
            except Exception:
                plan["affordable"] = None
        return plan

    # ---------------- 3. 给 UI 的一段话 ----------------
    def find_text(self, name, refresh_cost=2, buy_cost=3, gold=None):
        p = self.find_plan(name, refresh_cost, buy_cost, gold)
        if p["star"] is None or not p["levels"]:
            return ("表里有示例卡的星级: " + "、".join("%s(%d星)" % (k, v) for k, v in STAR_OF.items())
                    + "。别的卡先按它的星级对照上表。")
        lv, times = p["levels"][0]
        lines = ["「%s」%d星 —— 最省在 %d 本刷: 平均 %.1f 次(≈%.0f 金币: 刷新 %d×%d + 买 %d)"
                 % (name, p["star"], lv, times, p["gold_need"], times, refresh_cost, buy_cost)]
        rest = " / ".join("%d本 %.1f 次" % (l, t) for l, t in p["levels"][1:])
        if rest:
            lines.append("其它本: " + rest)
        if gold is not None and p["affordable"] is False:
            lines.append("⚠ 现在 %s 金币不够, 先攒或降本" % gold)
        return "\n".join(lines)

    # ---------------- 4. 站位(v4.107★) ----------------
    def position_plan(self, names, key=""):
        """一组核心卡 → 7 格站位 {"slots":[7个], "saved":bool, "note":str}

        先查存过的方案(data/lineup_positions.json); 没存过就按【卡池攻防】给个默认盘:
        防御最高的 3 张站前排(1~3 格, 先挨打), 其余按攻击降序排后面。
        话说在前头: 站位要看当时局势(对手阵容/装备/有没有毒), 所以这是【默认建议】不是
        结论 —— 在阵容页改完点保存, 下次这组阵容直接调出来。
        """
        saved = ((_load_json("lineup_positions.json", self.data_dir) or {}).get("plans") or {})
        names = [str(n).strip() for n in (names or []) if str(n).strip()]
        if key and key in saved:
            return {"slots": list((saved[key] or {}).get("slots") or [])[:7], "saved": True,
                    "note": "用的是你存过的方案"}
        cards = []
        for n in names:
            info = self.card_info(n) or {}
            try:
                atk = int(info.get("atk") or 0)
            except Exception:
                atk = 0
            try:
                dfn = int(info.get("def") or 0)
            except Exception:
                dfn = 0
            try:
                rank = int(info.get("rank") or 0)
            except Exception:
                rank = 0
            cards.append({"name": n, "atk": atk, "def": dfn, "rank": rank})
        front = sorted(cards, key=lambda c: (-c["def"], -c["rank"]))[:3]
        fn = set(c["name"] for c in front)
        back = sorted([c for c in cards if c["name"] not in fn],
                      key=lambda c: (-c["atk"], -c["rank"]))
        slots = ([c["name"] for c in front] + [c["name"] for c in back] + [""] * 7)[:7]
        return {"slots": slots, "saved": False,
                "note": "默认盘: 防御最高的 3 张站前排, 其余按攻击排后面(可在下面改)"}

    def save_position(self, key, slots):
        """把改好的 7 格存起来(按阵容 key)。"""
        path = os.path.join(self.data_dir, "lineup_positions.json")
        d = _load_json("lineup_positions.json", self.data_dir) or {"v": 1, "plans": {}}
        plans = d.get("plans") or {}
        plans[str(key or "").strip()] = {"slots": list(slots or [])[:7]}
        d["plans"] = plans
        return _save_json(path, d)

    # ---------------- 5. 克制(v4.107★) ----------------
    def counters(self, text=""):
        """克制关系: 传一段推荐文案就只回【跟它有关】的几条, 不传就全给。"""
        d = _load_json("lineup_counters.json", self.data_dir) or {}
        items = d.get("items") or []
        t = str(text or "").strip()
        if not t:
            return items
        hit = [x for x in items
               if (x.get("a") and str(x["a"]) in t) or (x.get("b") and str(x["b"]) in t)]
        return hit or items

    @staticmethod
    def counter_text(x):
        a, b, w = str(x.get("a") or "?"), str(x.get("b") or "?"), str(x.get("win") or "a")
        rel = ("克制" if w == "a" else ("被克制" if w == "b" else "五五开"))
        return ("%s %s %s" % (a, rel, b)) + (("  (%s)" % x["note"]) if x.get("note") else "")

    def add_counter(self, a, b, win="a", note=""):
        path = os.path.join(self.data_dir, "lineup_counters.json")
        d = _load_json("lineup_counters.json", self.data_dir) or {"v": 1, "items": []}
        items = d.get("items") or []
        items.append({"a": str(a or "").strip(), "b": str(b or "").strip(),
                      "win": ("b" if str(win) == "b" else ("even" if str(win) == "even" else "a")),
                      "note": str(note or "自己记的").strip()})
        d["items"] = items
        return _save_json(path, d)
