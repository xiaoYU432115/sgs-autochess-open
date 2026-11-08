# -*- coding: utf-8 -*-
"""S11 自走棋自动对局引擎(自动学习版)。

一句话: 连上正在进行的自走棋对局, 替你打完一整局 —— 选主公 → 招募 → 上阵 → 应对对手 →
      结算, 并把每局结果记下来, 越打越会挑阵容 / 主公 / 经济。

控制面(只读这里, 不自己造轮子):
  · 实时状态: core.protocol.LIVE   (in_game / phase / me / others / shop_snapshot())
  · 发指令:   core.remote_ops.BridgeOps
              (buy_goods / refresh_shop / sell_card / deploy_bench / use_spells / psend / send_recv)
  · 数据大脑: core.tavern_kb        (S11 棋子/主公/锦囊/机制标签/身材, 全从官方表算, 不手写)

★ 为什么这层单独存在:
  原 auto_recruit 只管"买指定的卡", 决策是拍脑袋; 现在要的是"自己想清楚这局怎么打",
  所以把【决策】从【执行】里拆出来 —— 决策全在 tavern_kb + 本文件的策略里,
  执行一律走 BridgeOps(协议直发, 和人工点一样, 不会把游戏搞坏)。

★ 关于"学很多局":
  每局结束把 (主公, 策略, 名次/胜负) 写进 data/auto_battle_learning.json,
  下次开局前读出来, 给"上次赢过的组合"加权。这是离线学习, 不依赖模拟器。

★ 关于身份局(身份匹配模式):
  本引擎的 mode="tavern"(自走棋) 已完整实现; mode="identity" 暂未建模 ——
  身份局的协议/状态目前 LIVE 没有接管, 强行跑会直接报错提示。自走棋是本季重点, 先把它做扎实。
"""
import os
import time
import json
import threading
import random

from core import tavern_kb as KB
from core import battle_brain as BB      # v4.140★ 局势大脑(身材/对拼/针对/变阵/经济)
from core import silence                 # 兜底不静默: 吞掉异常也要记一笔(见 core/silence.py)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEARN_PATH = os.path.join(BASE_DIR, "data", "auto_battle_learning.json")

# 阵营代码 → 中文(跟 tavern_kb.FACTION_CODE 对齐: 1魏 2蜀 3吴 4群 5黄巾 6汉 7西凉 8袁)
FACTION_NAME = {"1": "魏", "2": "蜀", "3": "吴", "4": "群", "5": "黄巾", "6": "汉", "7": "西凉", "8": "袁"}
NAME_FACTION = {v: k for k, v in FACTION_NAME.items()}


# ============================================================ 学习账本
class LearningStore:
    """每局结果 + 组合胜率, 落盘到 JSON。"""

    def __init__(self, path=LEARN_PATH):
        self.path = path
        self.data = {"games": [], "faction_wins": {}, "lord_wins": {}, "strategy_wins": {}}
        self._load()

    def _load(self):
        try:
            if os.path.isfile(self.path):
                with open(self.path, encoding="utf-8") as f:
                    d = json.load(f)
                if isinstance(d, dict):
                    self.data = d
                    for k in ("games", "faction_wins", "lord_wins", "strategy_wins"):
                        self.data.setdefault(k, {} if "wins" in k else [])
        except Exception:
            # 学习档案读不出来(文件坏了/半截写的) → 当没学过, 从零开始。
            # 不能 pass: 静默的话"我明明打过很多局, 怎么推荐还是乱的"就查不出原因。
            silence.note("auto_battle.Learn._load", "学习档案读不出来, 本次按空档案跑")

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=1)
        except Exception:
            # 写不进去 → 这一局白学。必须留痕: 不然"打了几十局助手一点没长进"
            # 根本查不到是落盘失败。
            silence.note("auto_battle.Learn.save", "学习档案写盘失败, 本局不计入学习")

    def record(self, lord_id, lord_name, faction, strategy, won, note=""):
        g = {
            "ts": time.time(), "lord_id": lord_id, "lord_name": lord_name,
            "faction": faction, "strategy": strategy, "won": bool(won), "note": note,
        }
        self.data["games"].append(g)
        if len(self.data["games"]) > 2000:
            self.data["games"] = self.data["games"][-2000:]
        self._bump("faction_wins", faction, won)
        self._bump("lord_wins", str(lord_id), won)
        self._bump("strategy_wins", strategy, won)
        self.save()

    def _bump(self, bucket, key, won):
        d = self.data.get(bucket) or {}
        rec = d.get(key) or {"n": 0, "w": 0}
        rec["n"] = rec.get("n", 0) + 1
        rec["w"] = rec.get("w", 0) + (1 if won else 0)
        d[key] = rec
        self.data[bucket] = d

    def faction_weight(self, faction):
        """该阵营的历史胜率加成(0~1 区间映射到 -0.3~+0.3)。"""
        rec = (self.data.get("faction_wins") or {}).get(faction)
        if not rec or rec.get("n", 0) < 2:
            return 0.0
        return round((rec["w"] / float(rec["n"]) - 0.5) * 0.6, 3)

    def stats(self):
        games = self.data.get("games") or []
        n = len(games)
        wins = sum(1 for g in games if g.get("won"))
        return {
            "games": n, "wins": wins,
            "win_rate": round(wins / float(n), 3) if n else 0.0,
            "faction_wins": self.data.get("faction_wins") or {},
            "lord_wins": self.data.get("lord_wins") or {},
            "strategy_wins": self.data.get("strategy_wins") or {},
        }


# ============================================================ 阵容规划
class CompositionPlanner:
    """给定(被禁势力 / 想要的机制 / 已选主公), 算出这局该往哪个阵营/机制走, 以及想买的卡清单。

    全部基于 tavern_kb 的官方数据算, 不手写任何数字。
    """

    def __init__(self, season="s11"):
        self.season = season
        self.chess = KB.load_chess(season)        # chessID -> info
        self.lords = KB.load_lords(season)        # generalID -> info

    def _faction_power(self, banned, prefer_tags, learn):
        """每个阵营的平均强度(加权学习胜率), 返回 {code: score}。"""
        acc = {}
        for c in self.chess.values():
            fac = str(c.get("faction") or "").strip()
            if fac not in FACTION_NAME:
                continue
            if fac in (banned or set()):
                continue
            s = KB.power_score(c)
            # 想要的机制命中 → 加分
            if prefer_tags:
                hit = len(set(c.get("tags") or []) & set(prefer_tags))
                s += hit * 2.0
            # 速查图鉴标的"体系核心卡" → 加分。这里算的是阵营【平均】强度,
            #   所以意思是: 一个阵营里核心卡越多, 说明它越能成体系(不是只有一堆散卡)
            if c.get("is_core"):
                s += KB.CORE_BONUS
            a = acc.setdefault(fac, [0.0, 0])
            a[0] += s
            a[1] += 1
        # S11『志继』联动加权：某势力"获得/触发志继"的棋子越多, 阵亡联动越强 → 鼓励走该势力
        try:
            import core.s11_enhance as S11
            zb = S11.faction_zhiji_bonus(self.season, banned=(banned or set()))
        except Exception:
            zb = {}
        out = {}
        for fac, (tot, n) in acc.items():
            if n == 0:
                continue
            avg = tot / float(n)
            avg += (learn.faction_weight(fac) if learn else 0.0)
            fname = KB.FACTION_CODE.get(str(fac))
            if fname and fname in zb:
                avg += zb[fname]
            out[fac] = round(avg, 2)
        return out

    def plan(self, banned=None, prefer_tags=None, lord_id=None, learn=None,
             force_faction=None):
        """开局/变阵都走这里。

        force_faction: v4.142★ 变阵时【指定方向】(中文名"吴"或代码"3"都认)。
          不传时行为跟以前完全一样(自己挑平均最强的方向), 开局就走这条路。
        """
        banned = set(str(b) for b in (banned or []))
        # 主公依赖的势力 → 优先当主阵营
        target = None
        if force_faction:
            s = str(force_faction).strip()
            code = NAME_FACTION.get(s) or (s if s in FACTION_NAME else None)
            if code and code not in banned:
                target = code
        lord = self.lords.get(str(lord_id)) if lord_id else None
        dep = KB.lord_factions(lord) if lord else set()
        dep = dep - banned
        if not target and dep:
            target = next(iter(dep))  # 取第一个依赖阵营

        fp = self._faction_power(banned, prefer_tags, learn)
        if not target and fp:
            # 选平均最强的非禁阵营(已含学习加成)
            target = max(fp, key=lambda k: fp[k])

        if not target:
            # 兜底: 没数据就随大流选吴(样本通常多)
            target = "3"

        # want 清单: 目标阵营里 power_score 最高的若干张
        want = []
        for c in self.chess.values():
            if str(c.get("faction") or "") != target:
                continue
            want.append(c)
        want.sort(key=KB.power_score, reverse=True)
        want_ids = [c["id"] for c in want[:14]]
        want_names = [c["name"] for c in want[:14]]

        # flex 清单: 跨阵营的"机制核"(经济/盾/甲/回手), 不管阵营都能补强度
        flex = []
        for c in self.chess.values():
            tags = set(c.get("tags") or [])
            if tags & {"经济", "盾", "护甲", "回手"} and c["id"] not in want_ids:
                flex.append(c)
        flex.sort(key=lambda c: KB.mech_score(c), reverse=True)
        flex_ids = [c["id"] for c in flex[:6]]

        strategy = "faction_%s" % FACTION_NAME.get(target, target)
        if prefer_tags:
            strategy += "+" + "/".join(prefer_tags)

        notes = ["主阵营=%s(平均强度 %.2f)" % (FACTION_NAME.get(target, target), fp.get(target, 0))]
        if force_faction:
            notes.append("(变阵转过来的方向, 不是开局选的)")
        if lord:
            notes.append("主公=%s(依赖%s)" % (lord.get("name"),
                       "/".join(FACTION_NAME.get(x, x) for x in dep) or "无"))
        if learn and learn.faction_weight(target):
            notes.append("该阵营历史胜率加成 %.2f" % learn.faction_weight(target))
        return {
            "faction": target,
            "faction_name": FACTION_NAME.get(target, target),
            "want_ids": set(want_ids),
            "want_names": want_names,
            "flex_ids": set(flex_ids),
            "strategy": strategy,
            "notes": notes,
        }


# ============================================================ 引擎
class AutoBattler:
    def __init__(self, ops, log_cb=None, season="s11"):
        self.ops = ops
        self.log = log_cb or (lambda m: None)
        self.season = season
        self.planner = CompositionPlanner(season)
        self.learn = LearningStore()
        self.running = False
        self._th = None
        self._run_generation = 0
        self._choose_last_attempt_at = 0.0
        self.cfg = {
            "mode": "tavern",            # tavern(自走棋) / identity(身份局, 暂未实现)
            "auto_choose_lord": True,    # 自动选主公(发协议; 失败则提示手动)
            "auto_level": True,          # 自动升级商店
            "auto_refresh": True,        # 经济宽裕时自动刷新找目标卡
            "use_spells": True,          # 自动用锦囊
            "reserve_gold": 0,           # 最少保留金币
            "banned_factions": [],       # 被禁势力(代码或中文)
            "prefer_tags": [],           # 想打的机制, 如 ["经济"]
            "buy_per_tick": 1,           # 每个招募节拍最多买几张(防一口气清空钱包)
            "tick": 0.6,                 # 招募节拍(秒)
        }
        self.status = {
            "running": False, "phase": "-", "games": 0, "lord": "-",
            "faction": "-", "strategy": "-", "last_action": "-", "learn": self.learn.stats(),
        }
        # 单局上下文
        self._active = False
        self._game_lord = None
        self._game_faction = None
        self._game_strategy = None
        self._last_refresh_t = 0
        self._opp_idx = 0

    # ---------------- 启停 ----------------
    def start(self, cfg=None):
        if self.running:
            self.stop()
        if cfg:
            self.cfg.update(cfg)
        if self.cfg.get("mode") != "tavern":
            self.log("[自动对局] 身份局模式暂未实现(需要单独建模协议), 仅支持自走棋(tavern)。")
            return False
        self._run_generation += 1
        run_generation = self._run_generation
        self._choose_last_attempt_at = 0.0
        self.running = True
        self.status["running"] = True
        self._th = threading.Thread(target=self._loop, args=(run_generation,),
                                    daemon=True, name="AutoBattle")
        self._th.start()
        self.log("[自动对局] 启动(模式=自走棋, 已学 %d 局)。进对局后自动接管。" % self.learn.stats()["games"])
        return True

    def stop(self, reason=""):
        if not self.running:
            return
        self.running = False
        self._run_generation += 1
        self.status["running"] = False
        self.log("[自动对局] 停止%s" % ((":" + reason) if reason else ""))

    def is_alive(self):
        return self.running and self._th and self._th.is_alive()

    # ---------------- 主循环 ----------------
    def _loop(self, run_generation=None):
        from core.protocol import LIVE
        if run_generation is None:
            run_generation = self._run_generation
        while self.running and run_generation == self._run_generation:
            time.sleep(self.cfg.get("tick", 0.6))
            if not self.running or run_generation != self._run_generation:
                break
            try:
                if not self.ops.connected():
                    continue
                if not LIVE.in_game:
                    self._maybe_finalize()
                    continue
                phase = LIVE.phase
                self.status["phase"] = phase or "-"
                if phase == "choose":
                    self._handle_choose(LIVE)
                elif phase == "recruit":
                    self._handle_recruit(LIVE)
                elif phase == "battle":
                    pass  # 战斗阶段不用操作, 等打完
                self._maybe_finalize()
            except Exception as e:
                self.log("[自动对局] 循环异常: %s" % e)
                time.sleep(1)

    # ---------------- 选主公 ----------------
    def _current_banned(self, LIVE):
        """本局禁用的势力 → (集合, 来源)。

        来源: 【协议】= 从游戏包里真读到了; 【界面】= 没读到, 用的用户在界面上勾的。
        为什么要把来源带出去: 这两件事在界面上长得一模一样, 但可靠度完全不同 ——
        勾错了是用户的事, 读错了是我的事, 混在一起就没法排查。
        """
        got = getattr(LIVE, "banned_factions", None)
        if got:
            return self._norm_factions(sorted(got)), "协议"
        return self._norm_factions(self.cfg.get("banned_factions") or []), "界面"

    def _handle_choose(self, LIVE):
        me = LIVE.me or {}
        if me.get("generalID"):
            return  # 已经选好了
        now = time.monotonic()
        if self._choose_last_attempt_at > 0 and now - self._choose_last_attempt_at < 5.0:
            return
        self._choose_last_attempt_at = now
        banned, ban_src = self._current_banned(LIVE)
        if ban_src == "协议":
            self.log("[自动对局] 本局禁用势力(从协议读到): %s" % ("/".join(sorted(banned)) or "无"))
        else:
            # 协议没读到 → 用界面上勾的。要说清楚是哪来的, 否则"我勾了魏但禁的是吴"
            # 这种事根本发现不了。
            self.log("[自动对局] 没从协议读到禁用势力, 用界面勾选: %s"
                     % ("/".join(sorted(banned)) or "无"))
        try:
            import core.s11_enhance as S11
            recs = S11.lord_pick_advice(self.season, banned_factions=banned,
                                        prefer_tags=self.cfg.get("prefer_tags") or [])
        except Exception as e:
            self.log("[自动对局] S11 选主公增强不可用(%s), 退回原推荐。" % e)
            recs = KB.recommend_lords(self.season, banned=banned,
                                      prefer_tags=self.cfg.get("prefer_tags") or [])
        if not recs:
            self.log("[自动对局] 没推荐出主公, 请在游戏内手动选。")
            return
        top = recs[0]
        gid = top["id"]
        self.log("[自动对局] 推荐主公: %s(评分%.1f) —— %s" % (top["name"], top["score"], top["why"]))
        if not self.cfg.get("auto_choose_lord"):
            self.log("[自动对局] 已关闭自动选主公, 请在游戏内选择(推荐上面那位)。")
            return
        # 尝试发协议选主公
        ok = self._choose_general(gid)
        if ok:
            self.log("[自动对局] 已发送选主公协议: %s" % top["name"])
        else:
            self.log("[自动对局] 自动选主公协议未通(可能 pid 未学), 请手动选上面那位, 选完我继续。")

    def _choose_general(self, gid):
        """发选主公协议。多种途径都试(标准协议 + 游戏原生接口多种命名), 任一成功即返回 True。"""
        # 途径①: 标准协议(名字不确定, 试了报错也不影响后续)
        try:
            if self.ops.psend("cmsg.CReqChessChooseGeneral", {"generalID": int(gid)}):
                return True
        except Exception as e:
            self.log("[自动对局] 选主公协议①失败: %s" % e)
        # 途径②: 游戏原生接口(和 level-up 同理, 几种可能命名都试一遍)
        js = ("(function(){try{var m=(typeof getManager==='function')?getManager():null;"
              "if(!m)return 'no-mgr';"
              "var fns=['ReqChessChooseGeneral','ReqChooseGeneral','ChessChooseGeneral'];"
              "for(var i=0;i<fns.length;i++){var f=m[fns[i]];"
              "if(typeof f==='function'){f.call(m,%s);return 'ok:'+fns[i];}}"
              "return 'no-fn';}catch(e){return 'err:'+e;}})()" % int(gid))
        try:
            r = self.ops.send_recv("eval", js=js, timeout=2.0)
            return str(r).find("ok") >= 0
        except Exception as e:
            self.log("[自动对局] 选主公原生接口失败: %s" % e)
            return False

    # ---------------- 招募 ----------------
    def _handle_recruit(self, LIVE):
        me = LIVE.me or {}
        lord_id = me.get("generalID")
        if not self._active:
            self._begin_game(LIVE, lord_id)
        # 没选主公先不买(选将阶段可能还没进招募)
        if not lord_id:
            return

        coin = int(me.get("coin") or 0)
        level = int(me.get("shopLevel") or 1)
        buy_cost = int(me.get("shopBuyCost") or 3)
        keep = int(self.cfg.get("reserve_gold") or 0)
        shop = LIVE.shop_snapshot() or []
        board = me.get("_board") or []
        line = me.get("_lineUp") or []

        # ---- 买卡: 命中 want / flex 且买得起且不爆仓 ----
        bought = 0
        bench_free = max(0, 8 - len([c for c in board if isinstance(c, dict)]))
        # ★ 买的时候把"针对需求"也算进命中: 计划清单是开局定的, 打几轮后对手的
        #   威胁出来了(对面毒多/肉盾多), 这时候该补的卡不在原清单里 —— 只认清单
        #   就是"明明看见解药也不买"。
        brain_flex = set(getattr(self, "_brain_flex", set()) or set())
        for g in shop:
            if bought >= int(self.cfg.get("buy_per_tick") or 1):
                break
            if g.get("_sold"):
                continue
            cid = str(g.get("chessID") or "")
            if cid in self._plan["want_ids"] or cid in self._plan["flex_ids"] \
                    or cid in brain_flex:
                if coin - buy_cost < keep:
                    break
                if bench_free <= 0 and len([x for x in (line or []) if x]) >= 7:
                    break
                if self.ops.buy_goods(g.get("goodsID")):
                    coin -= buy_cost
                    bought += 1
                    bench_free -= 1
                    nm = KB.load_chess(self.season).get(cid, {}).get("name", cid)
                    self._act("买 %s" % nm)
        if bought:
            self.log("[自动对局] 本节拍买入 %d 张" % bought)

        # ---- 上阵: 备战区没上阵的尽量上 ----
        try:
            if self.ops.deploy_bench("all"):
                self._act("上阵")
        except Exception as e:
            self.log("[自动对局] 上阵异常: %s" % e)

        # ---- 经济: 抽卡顾问 + 经济顾问 替掉"钱够就刷/就升"的盲目启发式 ----
        #   顾问只读 LIVE, 不发包; 任何异常都退回原启发式, 绝不让循环崩。
        ra_verdict, ep_kind = "", ""
        if self.cfg.get("auto_level") or self.cfg.get("auto_refresh"):
            want_names = self._plan.get("want_names") or []
            try:
                import core.refresh_advisor as RA
                ra = RA.advise(target=",".join(want_names), keep_gold=keep, want_level=6)
                ra_verdict = ra.get("verdict", "")
            except Exception as e:
                self.log("[自动对局] 抽卡顾问不可用: %s" % e)
            try:
                import core.econ_planner as EP
                ep = EP.plan(targets=",".join(want_names), keep_gold=keep)
                ep_kind = (ep.get("best") or {}).get("kind", "")
            except Exception as e:
                self.log("[自动对局] 经济顾问不可用: %s" % e)
        advisors_ok = (ra_verdict != "" or ep_kind != "")

        if self.cfg.get("auto_level") and level < 6:
            up = me.get("shopLevelUpCost")
            do_level = False
            if not advisors_ok:
                do_level = up is not None and coin - int(up) >= keep + buy_cost
            elif ra_verdict.startswith("先升级再刷"):
                do_level = True
            elif ep_kind in ("level", "level_refresh"):
                do_level = True
            elif up is not None and coin - int(up) >= keep + buy_cost:
                do_level = True  # 原启发式兜底
            if do_level and self.level_up(keep_gold=keep):
                self.log("[自动对局] 升级商店 → %d 级" % (level + 1))

        if self.cfg.get("auto_refresh"):
            can_refresh = False
            if not advisors_ok:
                # ★ 顾问不可用时用"经济自由度"兜底, 别退回"钱够就刷":
                #   低于保命线还刷 = 刷完没钱买, 白白亏一金币。
                ef = BB.econ_freedom(coin - keep, level=level,
                                     buy_cost=buy_cost or 3,
                                     refresh_cost=me.get("shopRefreshCost") or 1)
                can_refresh = ef["coin"] > ef["keep_line"]
            else:
                # 经济顾问让"存钱(hold)"就别刷; 让"升级/买卡"也别刷, 把钱花在该花的地方
                can_refresh = (ra_verdict == "可以刷" and ep_kind != "hold")
                if ep_kind in ("level", "level_refresh", "buy"):
                    can_refresh = False
            if can_refresh and coin - keep >= (me.get("shopRefreshCost") or 1) + buy_cost * 2:
                now = time.time()
                if now - self._last_refresh_t > 1.2:
                    self._last_refresh_t = now
                    if self.ops.refresh_shop(delay=0.3):
                        self._act("刷新商店")
                        self.log("[自动对局] 刷新商店找目标卡(%s)" % (ra_verdict or "原启发式"))

        # ---- 锦囊 ----
        if self.cfg.get("use_spells"):
            try:
                if self.ops.use_spells():
                    self._act("用锦囊")
            except Exception:
                silence.note("auto_battle._tick.用锦囊", "用锦囊这一步抛了异常, 本拍跳过")

        # ---- 针对对手(每隔几拍看一个对手的阵容, 记录强弱) ----
        self._scout_opponents(LIVE)

        # ---- 局势大脑: 身材/对拼/针对/经济(自己节流, 不会每拍都算) ----
        self._brain_eval(LIVE)

        # ---- 变阵: 刷新出来之后该不该换方向(每回合只判一次, 自己节流) ----
        #   ★ 放在买卡之后: 这一拍刚买到的卡要算进"现在手上有什么", 否则会拿着
        #     过期的手牌信息判断, 转错方向。
        self._maybe_pivot(LIVE, board)

    # ---------------- 变阵(v4.142★) ----------------
    def _sync_lose_streak(self, LIVE):
        """连败数(最近连着输了几场)。

        LIVE.battles 只有【累计】胜负, 没有"最近连着输几场"; 而连败是变阵最重要的
        触发信号之一(一直输 = 这个体系打不动)。这里用累计值的增量推:
        败场变多就累加连败, 胜场变多(说明赢过一场)就清零。
        """
        b = getattr(LIVE, "battles", None) or {}
        lose_n = int(b.get("lose") or 0)
        win_n = int(b.get("win") or 0)
        prev_l, prev_w = getattr(self, "_seen_battles", (0, 0))
        if win_n > prev_w:
            self._lose_streak = 0
        elif lose_n > prev_l:
            self._lose_streak = getattr(self, "_lose_streak", 0) + (lose_n - prev_l)
        self._seen_battles = (lose_n, win_n)
        return getattr(self, "_lose_streak", 0)

    def _maybe_pivot(self, LIVE, board):
        """看要不要转阵容 —— 用户原话"看刷新出来考虑玩什么, 怎么变阵"的核心。

        转了之后做两件事:
          ① 改 self._plan(新方向的 want 清单) —— ★ 抽卡顾问和经济顾问的"目标"就是
             从 _plan["want_names"] 来的, 所以 plan 一改, 它们下一拍自动跟着新方向走,
             不用各改一遍(这是这条链最省事的地方, 也是以前漏掉的那一环)。
          ② 遣散方向外的卡(transition_plan 算的 drop 名单)腾位置回本。
        不转就把结论挂到 status 上, 界面能看见"现在在玩什么 / 为什么不动"。
        """
        try:
            turn = int(getattr(LIVE, "turn", 0) or 0)
            # 每回合只评估一次: 变阵是重决策, 每拍都算会在两个方向间抖
            if turn == getattr(self, "_pivot_turn", -1):
                return
            self._pivot_turn = turn

            me = LIVE.me or {}
            banned, _src = self._current_banned(LIVE)
            # 主公依赖的势力: 主公技能绑它, 是转阵时权重最高的一条
            lord = KB.load_lords(self.season).get(str(me.get("generalID") or "")) or {}
            lord_f = None
            for code in (KB.lord_factions(lord) if lord else ()):
                nm = KB.FACTION_CODE.get(str(code))
                if nm and nm not in banned:
                    lord_f = nm
                    break

            cur_f = (self._plan or {}).get("faction_name")
            verdict = BB.should_pivot(
                board, season=self.season, banned=banned, lord_faction=lord_f,
                current_faction=cur_f, turn=turn,
                hp=int(me.get("hp") or 0),
                lose_streak=self._sync_lose_streak(LIVE))

            now = verdict.get("now") or {}
            self._pivot_state = verdict
            # 界面上要能看见"现在在玩什么"和"为什么不动/为什么转"
            self.status["system"] = now.get("system") or "-"
            self.status["pivot"] = ("→ %s(%s)" % (verdict["to"], verdict["why"])) \
                if verdict.get("pivot") else ("不动: %s" % (verdict.get("why") or "-"))

            if not verdict.get("pivot"):
                return
            if not self.cfg.get("auto_pivot", True):
                self.log("[变阵] 判断该转%s, 但『自动变阵』没开, 只提示不动手" % verdict["to"])
                return

            to = verdict["to"]
            old_f = cur_f
            # ① 重算 want 清单
            self._plan = self.planner.plan(
                banned=banned, prefer_tags=self.cfg.get("prefer_tags") or [],
                lord_id=me.get("generalID"), learn=self.learn, force_faction=to)
            self._game_faction = self._plan["faction"]
            self._game_strategy = self._plan["strategy"] + "+变阵"
            self.status["faction"] = self._plan["faction_name"]
            self.status["strategy"] = self._game_strategy
            self.log("[变阵] %s → %s: %s" % (old_f or "?", to, verdict.get("why")))
            self.log("[变阵] 想拿的卡换成: " + "、".join(self._plan["want_names"][:10]))

            # ② 遣散【方向外】的卡, 从最不值钱的开始扔, 最多 drop_n 张。
            #   ★ 为什么不用 transition_plan 的 drop 名单: 那个名单是【全局价值最低】
            #     的 n 张, 里面可能全是新方向的低星卡 —— 实测转吴时它排出来一张吴卡
            #     (新方向, 不能扔), 而真正该扔的蜀卡反而没进名单, 结果一张都没卖成。
            #     变阵要扔的是"新体系用不上的卡", 不是"最弱的卡", 两回事。
            cand = []
            for c in board:
                if not isinstance(c, dict):
                    continue
                cid = str(c.get("chessID") or "")
                info = KB.load_chess(self.season).get(cid) or {}
                # 新方向的卡一律留着(它在新体系里是资产)
                if info and info.get("faction_name") == to:
                    continue
                p = BB.normalize_piece(c, self.season) or {}
                cand.append(((p.get("atk") or 0) + (p.get("def") or 0), c))
            cand.sort(key=lambda x: x[0])
            n_drop = max(0, int(verdict.get("drop_n") or 0))
            sold = 0
            for _v, c in cand[:n_drop]:
                gid = c.get("goodsID")
                if gid and self.ops.sell_card(gid):
                    sold += 1
            if sold:
                self._act("变阵遣散 %d 张" % sold)
                self.log("[变阵] 遣散方向外的 %d 张(从最不值钱的开始)" % sold)

            # ③ 接下来优先找谁(transition_plan 算的, 写进日志给个交代)
            tp = BB.transition_plan(board, want_faction=to, season=self.season)
            wn = [w.get("name") for w in (tp.get("want") or [])[:5] if w.get("name")]
            if wn:
                self.log("[变阵] 接下来优先找: " + "、".join(wn))
        except Exception as e:
            # 变阵算不出来不该影响买卡主流程, 但不能静默 —— 否则"助手从来不转阵容"
            # 根本查不到是这里一直抛异常。
            silence.note("auto_battle._maybe_pivot", "变阵这一步出错: %s" % e)

    # ---------------- 局势大脑 ----------------
    def _brain_eval(self, LIVE, force=False):
        """定期看一眼局势: 我这盘身材如何、最打不过谁、该补什么、钱该怎么花。

        结果存 self._brain(界面/日志读), 并把"该补的卡"放进 self._brain_flex
        让买卡那一节直接能用。任何异常都退化成空结果 —— 循环不能因为评估挂掉。
        """
        now = time.time()
        if not force and now - getattr(self, "_brain_t", 0) < 6.0:
            return self._brain or {}
        self._brain_t = now
        try:
            me = LIVE.me or {}
            mine = (me.get("_board") or me.get("_lineUp") or [])
            opps = []
            for o in (LIVE.others or []):
                if not isinstance(o, dict):
                    continue
                b = o.get("_board") or o.get("lastLineUp") or o.get("_lineUp")
                if b:
                    opps.append((o.get("nickname") or o.get("userID") or "?", b))

            prof = BB.effective_stats(mine, self.season)
            duels = []
            for who, b in opps:
                d = BB.predict_duel(mine, b, self.season)
                if d.get("ok"):
                    duels.append((who, d))
            duels.sort(key=lambda x: x[1]["margin"])     # 最打不过的排最前

            ca = BB.counter_advice(opps, mine=mine, season=self.season)
            need = [r["tag"] for r in (ca.get("need") or [])]

            # 该补的机制 → 具体卡(买卡那一节用)
            flex = set()
            if need:
                want = set(need)
                for c in KB.load_chess(self.season).values():
                    if set(c.get("tags") or []) & want:
                        flex.add(c["id"])
            self._brain_flex = flex

            coin = int(me.get("coin") or 0)
            ef = BB.econ_freedom(coin, level=int(me.get("shopLevel") or 1),
                                 buy_cost=int(me.get("shopBuyCost") or 3),
                                 refresh_cost=int(me.get("shopRefreshCost") or 1))
            self._brain = {
                "body": BB.body_report(mine, self.season),
                "profile": prof,
                "worst": (duels[0][0], duels[0][1]["verdict"], duels[0][1]["margin"]) if duels else None,
                "best_win": (duels[-1][0], duels[-1][1]["verdict"]) if duels else None,
                "need": need,
                "econ": ef,
                "scanned": len(opps),
            }
            self.status["brain"] = {
                "body": self._brain["body"],
                "worst": ("%s %s" % (self._brain["worst"][0], self._brain["worst"][1])) if duels else "-",
                "need": "/".join(need) or "-",
                "econ": ef["act"],
            }
            if force or getattr(self, "_brain_logged", 0) < 3:
                self._brain_logged = getattr(self, "_brain_logged", 0) + 1
                self.log("[局势] " + self._brain["body"])
                if duels:
                    who, verdict, mg = self._brain["worst"]
                    self.log("[局势] 最难打: %s → %s(差距 %.2f)" % (who, verdict, mg))
                if need:
                    self.log("[局势] 建议补: " + "、".join(need))
                self.log("[局势] 钱: %s(%s)" % (ef["act"], ef["why"]))
            return self._brain
        except Exception as e:
            self.log("[局势] 评估异常(不影响操作): %s" % e)
            return {}

    # ---------------- 升级商店 ----------------
    def level_up(self, keep_gold=0):
        me = None
        try:
            from core.protocol import LIVE
            me = LIVE.me or {}
            coin = int(me.get("coin") or 0)
            cost = me.get("shopLevelUpCost")
            lv = int(me.get("shopLevel") or 1)
            if lv >= 6 or cost is None:
                return False
            if coin - int(cost) < keep_gold:
                return False
        except Exception:
            return False
        js = ("(function(){try{var m=(typeof getManager==='function')?getManager():null;"
              "if(!m||typeof m.ReqShopLevelUp!=='function')return 'no-mgr';"
              "m.ReqShopLevelUp();return 'ok';}catch(e){return 'err:'+e;}})()")
        try:
            r = self.ops.send_recv("eval", js=js, timeout=2.0)
            return str(r).find("ok") >= 0
        except Exception as e:
            self.log("[自动对局] 升级失败: %s" % e)
            return False

    # ---------------- 针对对手 ----------------
    def _scout_opponents(self, LIVE):
        try:
            others = [o for o in (LIVE.others or []) if isinstance(o, dict) and o.get("userID")]
            if not others:
                return
            if getattr(self, "_scout_t", 0) % 4 != 0:
                self._scout_t = getattr(self, "_scout_t", 0) + 1
                return
            self._scout_t = 1
            o = others[self._opp_idx % len(others)]
            self._opp_idx += 1
            self.ops.fetch_opponent_board(o["userID"])
        except Exception:
            # 侦察不到某个对手 → 这一拍就少看一家, 不影响主流程。但要记账:
            #   要是【每一家】都取不到, 后面"针对对手"就全是空谈, 得能查出来。
            silence.note("auto_battle._scout_opponents", "取对手阵容失败, 这一拍少看一家")

    # ---------------- 单局生命周期 ----------------
    def _begin_game(self, LIVE, lord_id):
        self._active = True
        self._new_run("新的一局")
        # 新的一局: 上一局的局势判断(针对需求/对拼结论)必须清空, 否则会拿着
        # 上一局"该补毒"的结论去打这一局, 而这一局对面根本没肉盾。
        self._brain = {}
        self._brain_flex = set()
        self._brain_t = 0
        self._brain_logged = 0
        # 变阵状态: 新的一局必须清零 —— 连败数、已看过的战斗计数、上次评估的回合,
        #   不然会拿着上一局的连败去触发这一局的变阵(一进新局就乱转)。
        self._lose_streak = 0
        self._seen_battles = (0, 0)
        self._pivot_turn = -1
        self._pivot_state = None
        self.status["system"] = "-"
        self.status["pivot"] = "-"
        banned = self._norm_factions(self.cfg.get("banned_factions") or [])
        self._plan = self.planner.plan(
            banned=banned, prefer_tags=self.cfg.get("prefer_tags") or [],
            lord_id=lord_id, learn=self.learn)
        self._game_lord = lord_id
        self._game_faction = self._plan["faction"]
        self._game_strategy = self._plan["strategy"]
        self.status["lord"] = KB.load_lords(self.season).get(str(lord_id), {}).get("name", str(lord_id))
        self.status["faction"] = self._plan["faction_name"]
        self.status["strategy"] = self._plan["strategy"]
        for n in self._plan["notes"]:
            self.log("[自动对局] 策略: " + n)
        self.log("[自动对局] 想拿的卡: " + "、".join(self._plan["want_names"][:10]))

    def _maybe_finalize(self):
        from core.protocol import LIVE
        if not self._active:
            return
        me = LIVE.me or {}
        hp = me.get("hp")
        over = (not LIVE.in_game) or (hp is not None and int(hp) <= 0)
        if not over:
            return
        won = not (hp is not None and int(hp) <= 0)
        lord_name = KB.load_lords(self.season).get(str(self._game_lord or ""), {}).get("name", str(self._game_lord))
        self.learn.record(self._game_lord, lord_name, self._game_faction,
                          self._game_strategy, won, note=("hp=%s" % hp))
        self.status["games"] = self.learn.stats()["games"]
        self.status["learn"] = self.learn.stats()
        self.log("[自动对局] 本局结束: %s | 主公=%s 阵营=%s 策略=%s(已学 %d 局, 胜率 %.0f%%)"
                 % ("胜" if won else "负", lord_name, self._plan["faction_name"],
                    self._game_strategy, self.status["games"],
                    100 * self.learn.stats()["win_rate"]))
        self.log("[自动对局] 请在游戏内开始下一局, 引擎继续待命。")
        self._active = False
        self._plan = None
        self.status["lord"] = self.status["faction"] = self.status["strategy"] = "-"

    # ---------------- 工具 ----------------
    def _norm_factions(self, lst):
        out = set()
        for b in (lst or []):
            s = str(b).strip()
            if s in NAME_FACTION:
                out.add(s)
            elif s in FACTION_NAME:
                out.add(s)
            else:
                # 中文也认
                code = NAME_FACTION.get(s)
                if code:
                    out.add(code)
        return out

    def _act(self, s):
        self.status["last_action"] = s
        self.status["run_id"] = getattr(self, "_run_id", 0)

    def _new_run(self, reason=""):
        """开新一轮: 换局 / 重新开自动 / 计划被重算时调用, 返回新的轮次号。

        为什么要有这个编号: 复盘时要能按"轮"把动作切开 —— 否则上一轮发起的操作,
        它的迟到确认或统计会算到新一轮头上, 看起来像"这一轮多买了一张"。

        ⚠ 说清楚: codex 靠 run_id 是因为它的回包是异步配对的, 上轮回包可能污染
        本轮计数; 我们的买卡/刷新是【同步等确认】(等到目标 goodsID 离开商店或超时),
        本来不存在跨轮混淆。所以这里主要是给日志和统计做归因, 不是去修一个
        我们已经有的 bug —— 别把它当万能药。
        """
        self._run_id = int(getattr(self, "_run_id", 0)) + 1
        self.status["run_id"] = self._run_id
        self.status["_run_reason"] = reason or ""
        try:
            self.log("[自动对局] 新一轮 #%d %s" % (self._run_id, reason or ""))
        except Exception:
            pass
        return self._run_id

    def get_status(self):
        self.status["learn"] = self.learn.stats()
        return dict(self.status)


# ============================================================ 自测(不连游戏也能跑决策)
def _self_test():
    """用一份假 LIVE 跑一遍决策, 确保 import / 规划 / 选主公逻辑不崩。"""
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    # 假 ops
    class FakeOps:
        def connected(self): return True
        def psend(self, *a, **k): return False
        def send_recv(self, *a, **k): return "no-mgr"
        def buy_goods(self, g): print("  [fake] buy", g); return True
        def refresh_shop(self, **k): return True
        def sell_card(self, g): return True
        def deploy_bench(self, x): return True
        def use_spells(self): return False
        def fetch_opponent_board(self, u): return None

    # 假 LIVE
    class FakeMe(dict):
        pass
    fake_me = {"hp": 30, "coin": 12, "shopLevel": 3, "generalID": "",
               "shopLevelUpCost": 12, "shopRefreshCost": 2, "shopBuyCost": 3,
               "_board": [], "_lineUp": [0, 0, 0, 0, 0, 0, 0]}
    import core.protocol as P
    P.LIVE.in_game = True
    P.LIVE.phase = "recruit"
    P.LIVE.me = fake_me
    # shop 给两张目标阵营测试卡(用真实 ID 取不到也没关系, 走 flex 分支)
    P.LIVE.shop = [{"goodsID": 1, "chessID": "21001011", "_sold": False},
                   {"goodsID": 2, "chessID": "21002011", "_sold": False}]
    P.LIVE.others = [{"userID": "x1"}]
    P.LIVE.shop_snapshot = lambda: list(P.LIVE.shop)

    ab = AutoBattler(FakeOps(), log_cb=print)
    ab.cfg["auto_choose_lord"] = False
    print("推荐主公(禁魏, 想要经济):")
    for r in KB.recommend_lords("s11", banned=["魏"], prefer_tags=["经济"])[:3]:
        print("  ", r["name"], r["score"], r["why"])
    pl = ab.planner.plan(banned=["魏"], prefer_tags=["经济"], learn=ab.learn)
    print("阵容规划:", pl["faction_name"], "want=", pl["want_names"][:6])
    # 跑几拍招募逻辑(不会真发包, 因为 FakeOps)
    ab._active = True
    ab._plan = pl
    ab._handle_recruit(P.LIVE)
    print("学习账本初始统计:", ab.learn.stats()["games"])
    print("SELF_TEST_OK")


if __name__ == "__main__":
    _self_test()
