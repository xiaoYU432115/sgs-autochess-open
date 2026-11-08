# -*- coding: utf-8 -*-
"""自动招募引擎（纯注入：协议直读商店 + 协议直发买卡/遣散/上阵，全程后台不占鼠标）。

循环逻辑（招募阶段每 interval 秒一次）:
  1. 读 LIVE.shop_snapshot()（协议真实商店: chessID/minionTypes/skills）
  2. 识别目标卡: 武将名(ID映射) 或 阵营类型(CHESSMTWu=吴国等)
  3. 命中 -> 协议直发 CReqShopBuyChess
  4. "买了就卖"名单(如庞德公: 买了触发效果后卖掉) -> 按goodsID协议遣散
  5. 未命中且金币够 -> 协议直发刷新商店
  6. auto_deploy开 -> 备战区有未上阵的卡自动协议上阵
  7. auto_sell开 -> 备战区满且无目标卡时, 遣散最不值钱的非上阵卡腾位
配置由 HUD 传入，停止条件: 被叫停 / 不在对局 / 非招募阶段。

⚠️ v4.108★ 【已废弃·不要接线】
   全项目无人 import、无人实例化(已用脚本统计确认: 引用数 0)。
   自动招募早已改由「Python 编排 + 油猴执行」的链路实现(见开发文档.md 技术债条目)。
   本文件保留仅供对照参考 —— 里面的旧排序函数名与坐标卖卡逻辑已过时, **接回去会出问题**。
"""
import threading
import time
from core import silence as _SIL

try:
    from . import id_map as IDMAP
    from .protocol import LIVE
except ImportError:   # 直接运行本文件
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from core import id_map as IDMAP
    from core.protocol import LIVE


class AutoRecruiter:
    def __init__(self, ops, log_cb=None):
        self.ops = ops
        self.log = log_cb or (lambda m: None)
        self.running = False
        self._th = None
        self.cfg = {}
        self.stats = {"bought": 0, "refreshed": 0, "sold": 0, "deployed": 0}
        # v3.44★ 武将精选白/黑名单支持 (goodsID 或 chessID 字符串集合)
        self.whitelist = []
        self.blacklist = []

    # ---------- 启停 ----------
    def start(self, cfg):
        if self.running:
            self.stop()
        self.cfg = dict(cfg or {})
        self.stats = {"bought": 0, "refreshed": 0, "sold": 0, "deployed": 0}
        self.running = True
        self._th = threading.Thread(target=self._loop, daemon=True)
        self._th.start()
        strict = bool(self.cfg.get("strict_whitelist", False))
        self.log(f"[自动招募] 启动: 目标={self.cfg.get('targets') or '-'} "
                 f"阵营={self.cfg.get('minion_types') or '-'} "
                 f"自动刷新={'开' if self.cfg.get('auto_refresh') else '关'} "
                 f"自动上阵={'开' if self.cfg.get('auto_deploy') else '关'} "
                 f"自动遣散={'开' if self.cfg.get('auto_sell') else '关'} "
                 f"保留金币={self.cfg.get('reserve', 0)} "
                 f"买了就卖={self._sell_names() or '-'} "
                 f"严格按白名单买={'开(忽略旧targets输入)' if strict else '关(targets+白名单并集, 黑名单优先剔除)'} "
                 f"白名单={len(self.whitelist)}  黑名单={len(self.blacklist)}")

    def stop(self, reason=""):
        if not self.running:
            return
        self.running = False
        s = self.stats
        self.log(f"[自动招募] 停止{(':' + reason) if reason else ''} "
                 f"买{s['bought']} 刷{s['refreshed']} 卖{s['sold']} 上阵{s['deployed']}")

    def alive(self):
        return self.running and self._th and self._th.is_alive()

    # ---------- 规则解析 ----------
    def _raw_targets(self):
        return [t.strip() for t in str(self.cfg.get("targets") or "")
                .replace("，", ",").split(",") if t.strip()]

    def _targets(self):
        """正常招募目标(去掉!前缀)"""
        return [t.lstrip("！!") for t in self._raw_targets()]

    def _sell_names(self):
        """买了就卖目标: targets里以!开头的名字(如 !庞德公 = 买了触发效果后立即遣散)"""
        return [t.lstrip("！!") for t in self._raw_targets() if t.startswith(("!", "！"))]

    def _minion_types(self):
        return [t.strip() for t in str(self.cfg.get("minion_types") or "").replace("，", ",").split(",") if t.strip()]

    # ---------- v3.44★ 武将精选 白/黑名单 判定 ----------
    def _ids_of(self, goods_or_card):
        """返回 (goodsID_str, chessID_str) 或者 ("","")"""
        if isinstance(goods_or_card, dict):
            return (str(goods_or_card.get("goodsID") or ""),
                    str(goods_or_card.get("chessID") or goods_or_card.get("id") or ""))
        else:
            s = str(goods_or_card or "")
            return (s, s)

    def _in_list(self, goods_or_card, id_list):
        g, c = self._ids_of(goods_or_card)
        if not g and not c: return False
        setL = {str(x) for x in id_list or []}
        return (g in setL) or (c in setL)

    def _in_blacklist(self, goods_or_card):
        return self._in_list(goods_or_card, self.blacklist or [])

    def _in_whitelist(self, goods_or_card):
        return self._in_list(goods_or_card, self.whitelist or [])

    def _should_buy(self, g):
        """严格白名单: 只买白名单命中的;
        否则: targets/类型/白名单 任一命中 + 不在黑名单."""
        if g is None: return False, ""
        if self._in_blacklist(g):
            return False, "命中黑名单"
        strict = bool(self.cfg.get("strict_whitelist", False))
        if strict:
            if self._in_whitelist(g):
                return True, "严格白名单"
            return False, "严格模式未在白名单"
        # 非严格: 白名单 优先生效 -> 直接买
        if self._in_whitelist(g):
            return True, "白名单命中"
        return None, ""  # 留待旧规则判定

    # ---------- 主循环 ----------
    def _wait_shop_update(self, old_shop, timeout=2.0):
        """刷新/买卡后, 轮询LIVE.shop_snapshot直到数据变化(最多等timeout秒)"""
        old_sig = tuple(sorted(str(g.get("goodsID")) for g in (old_shop or []) if g.get("goodsID")))
        end = time.time() + timeout
        while time.time() < end:
            cur = LIVE.shop_snapshot()
            new_sig = tuple(sorted(str(g.get("goodsID")) for g in (cur or []) if g.get("goodsID") and not g.get("_sold")))
            if new_sig and new_sig != old_sig:
                return cur
            time.sleep(0.1)
        return None

    @staticmethod
    def _rescan_due(now, interval, last_rescan, force):
        """P3-A: 是否该重扫 DOM 逻辑地址(纯函数, 便于测试)。
        interval<=0 → 每轮扫(旧行为/逃生钩子); force(重连/进局翻转)→必扫;
        否则每 interval 秒扫一次。"""
        if interval <= 0:
            return True
        if force:
            return True
        return (now - last_rescan) >= interval

    def _loop(self):
        # P3-A 节流: DOM 逻辑地址对局内基本不变, 不必每 0.3s 扫一次。
        #   rescan_interval 秒数(默认 5, cfg 可配); 设 <=0 则回退"每轮都扫"(旧行为/逃生钩子)。
        #   翻转强制: 连接重连 / 刚进对局 时强制重扫, 避免地址 stale。
        _rescan_interval = float((self.cfg or {}).get("rescan_interval", 5.0))
        _last_rescan = 0.0
        _was_connected = False
        _was_in_game = False
        while True:
            time.sleep(0.3)
            try:
                conn = self.ops.connected()
                ingame = LIVE.in_game
                # 重连 / 刚进对局 → 强制重扫, 避免地址 stale
                force_rescan = (conn and not _was_connected) or (ingame and not _was_in_game)
                _was_connected = conn
                _was_in_game = ingame
                if not conn:
                    continue
                if not ingame:
                    continue
                cur = LIVE.phase or "recruit"
                if cur != "recruit":
                    continue

                # v3.54★ 同步 DOM 逻辑地址(P3-A 节流: 默认每 5s 一次, 翻转时强制; <=0 每轮扫)
                now = time.time()
                if hasattr(self.ops, "rescan_areas") and self._rescan_due(
                        now, _rescan_interval, _last_rescan, force_rescan):
                    self.ops.rescan_areas()
                    _last_rescan = now

                # ★轮换请求对手上回合阵容(协议直发CReqChessGetLastBattlePlayerData)
                # 限流: 每10秒最多请求1次(避免频繁psend阻塞auto_recruit线程)
                now = time.time()
                if not hasattr(self, "_opp_last") or (now - self._opp_last) >= 10:
                    self._opp_last = now
                    if not hasattr(self, "_opp_idx"):
                        self._opp_idx = 0
                    with LIVE.lock:
                        opps = [o for o in (LIVE.others or []) if isinstance(o, dict) and o.get("userID")]
                    if opps:
                        o = opps[self._opp_idx % len(opps)]
                        self._opp_idx += 1
                        try:
                            self.ops.fetch_opponent_board(o["userID"])
                        except Exception:
                            _SIL.note("core/auto_recruit.py:174", "self.ops.fetch_opponent_board(o['userID'])")
                # 下面是原有自动招募逻辑
                coin = LIVE.me.get("coin")
                refresh_cost = int(LIVE.me.get("shopRefreshCost") or 0)
                reserve = int(self.cfg.get("reserve") or 0)
                shop = LIVE.shop_snapshot()
                if not shop:
                    # 第一次没商店数据: 协议还没推过来, 主动刷新一次拉数据
                    try:
                        self.ops.refresh_shop(delay=0.3)
                        self.stats["refreshed"] += 1
                        time.sleep(0.8)
                    except Exception:
                        _SIL.note("core/auto_recruit.py:187", "self.ops.refresh_shop(delay=0.3)")
                    continue
                # 每轮限买一张防连点(买完等协议回金币更新)
                pick, how = self._pick(shop)
                if pick:
                    nm = IDMAP.name_of(pick.get("chessID")) or f"chessID:{pick.get('chessID')}"
                    gid = pick.get("goodsID")
                    if self.ops.buy_goods(gid):
                        self.stats["bought"] += 1
                        self.log(f"[招募] 买入 {nm} ({how})")
                        self._wait_shop_update(shop, 1.5)
                        # 买了就卖: 按goodsID协议遣散(如庞德公: 买了触发效果就卖)
                        if any(s in nm for s in self._sell_names()):
                            if self.ops.sell_card(gid):
                                self.stats["sold"] += 1
                                self.log(f"[招募] 已遣散 {nm}(买了就卖)")
                        # 自动上阵
                        elif self.cfg.get("auto_deploy"):
                            time.sleep(0.3)
                            if self.ops.deploy_bench(1):
                                self.stats["deployed"] += 1
                    continue
                # 自动遣散腾位: 备战区满(≥8张)且没有目标卡时, 卖掉非上阵的闲卡
                if self.cfg.get("auto_sell"):
                    if self._sell_idle():
                        continue
                # 自动上阵(没买卡时也尝试上阵积压的备战卡)
                if self.cfg.get("auto_deploy"):
                    if self.ops.deploy_bench(1):
                        self.stats["deployed"] += 1
                        continue
                if self.cfg.get("use_spells"):
                    if self.ops.use_spells():
                        continue
                if self.cfg.get("auto_refresh") and coin is not None:
                    # v3.51★ 自动刷新金币保护: 
                    # 1. 严格保留金币 (coin - refresh_cost >= reserve)
                    # 2. 如果刷新成本大于1且金币不多(如<6), 降低刷新频率
                    if coin - refresh_cost >= reserve and refresh_cost > 0:
                        # 额外保护: 如果刷新成本突然变高(如活动期)或者金币处于低位, 给用户留一点反应时间
                        if coin < reserve + refresh_cost * 2:
                             time.sleep(0.5) 
                        try:
                            self.ops.refresh_shop(delay=0.3)
                            self.stats["refreshed"] += 1
                            # 等商店数据真的更新了再继续(关键提速: 不等的话旧快照就废了)
                            if not self._wait_shop_update(shop, 2.0):
                                self.log("[招募] 刷新了但商店快照没变——再等一回合")
                        except Exception as e:
                            self.log(f"[招募] 刷新失败: {e}")
            except Exception as e:
                self.log(f"[自动招募] 循环异常: {e}")
                time.sleep(1)

    def _sell_idle(self):
        """v3.56★ 备战区(手卡)接近上限时遣散闲卡。适配用户反馈: 最大长度20, 含锦囊和武将"""
        try:
            me = LIVE.me or {}
            board = me.get("_board") or []
            line = {str(x) for x in (me.get("_lineUp") or [])}
            
            # 识别闲卡: 不在战斗区, 且不是白名单里的核心卡
            idle = []
            for c in board:
                if not isinstance(c, dict) or not c.get("goodsID"): continue
                cid = str(c.get("chessID") or c.get("spellID") or "")
                if cid in line: continue
                # 如果是白名单里的卡，尽量不卖
                if self._in_whitelist(c): continue
                idle.append(c)
            
            # 设定触发阈值: 如果最大20, 达到18张时开始清理
            max_len = int(self.cfg.get("max_bench_size") or 20)
            threshold = max_len - 2
            
            if len(board) < threshold or not idle:
                return False
                
            # 优先卖最便宜的(锦囊牌优先保留, 除非费用极高)
            idle.sort(key=lambda c: (IDMAP.chess_cost_of(c.get("chessID") or c.get("spellID")) or 9))
            cand = idle[0]
            cid = cand.get("chessID") or cand.get("spellID")
            nm = IDMAP.name_of(cid) or f"ID:{cid}"
            
            if self.ops.sell_card(cand["goodsID"]):
                self.stats["sold"] += 1
                self.log(f"[招募] 备战区接近上限({len(board)}/{max_len}), 遣散 {nm} 腾位")
                return True
        except Exception as e:
            self.log(f"[招募] 自动遣散异常: {e}")
        return False

    def _pick(self, shop):
        """按规则挑一张目标卡。返回 (goodsDict, 命中方式) 或 (None, '')"""
        # v3.44★ 第一步: 黑名单过滤 + 严格白名单(最高优先级, 覆盖后面所有规则)
        strict_hits = []
        for g in shop:
            if g.get("_sold"): continue
            ok, reason = self._should_buy(g)
            # 明确拒绝(黑名单/严格模式未命中) -> 跳过
            if ok is False: continue
            # 明确购买(严格白名单 / 非严格白名单命中): 先收集, 最后挑第一张
            if ok is True: strict_hits.append((g, reason))
        if strict_hits:
            return strict_hits[0]
        # ---- 第二步: 旧规则 targets 名字匹配 + 类型匹配 (只有非严格模式才跑到这里) ----
        targets = self._targets()
        mts = self._minion_types()
        for g in shop:
            if g.get("_sold"):
                continue
            gid_name = IDMAP.name_of(g.get("chessID"))
            if targets and any(t in gid_name for t in targets if gid_name):
                return g, f"名字~{gid_name}"
            types = g.get("minionTypes") or []
            if mts and any(mt in types for mt in mts):
                return g, "阵营:" + ",".join(types)
        # 武将名没映射时提示一次
        if targets and not IDMAP.count():
            self._hint_once()
        return None, ""

    _hinted = False

    def _hint_once(self):
        if AutoRecruiter._hinted:
            return
        AutoRecruiter._hinted = True
        self.log("[招募] 武将名映射为空——按名字匹配暂不可用。"
                 "点HUD面板【抓配置表(名字映射)】学会全部棋子名(一次性); "
                 "或改用阵营匹配(CHESSMTWu等)立刻可用")
