# -*- coding: utf-8 -*-
"""游戏协议解析器（实锤数据源：1.txt 对局记录捕获的 cmsg.* 协议）。

已验证协议字段:
  cmsg.CNotifyChessEnterTeam / CNotifyChessDataUpdate:
    owner|chessBaseInfo: {hp, coin, shopLevel, generalID(主公), shopLevelUpCost,
                          shopRefreshCost, shopBuyCost, nickname, userID, hpLimit}
    otherPlayer: 8人 [{hp, coin, shopLevel, generalID, nickname, userID}]  ← 对手金币实锤可见
    teamInfo: {turn, ...}
    shopGoods: [{goodsID, chessID, hp, attack, totalHp, totalAttack, skills, minionTypes}]
  cmsg.CRespShopRefreshChess: {shopGoods, restCoin, isAuto}
  cmsg.CRespShopBuyChess: {goodsID, buyChess{...}}
  cmsg.CRespChessLineUp: {positionChess:[goodsID...]}   布阵
  cmsg.CNotifyChessBattleStart / CNotifyChessBattleEnd(battleResult: CBGRTWin/CBGRTDraw/...)
  cmsg.CNtfChessGameOverMsg: {GameOverInfo: [{emRus, UserID, iLostHp}]}
    ⚠ v4.68★ 更正: emRus 不是名次, 是【这一场战斗的结果码】。反编译源码里的用法是
      `battleEndProtoData = { battleResult: e.emRus + 1, ...lostHp: iLostHp }`
      (2==emRus 时取 iLostHp 掉血、3==emRus 时 i=0), 而这条包每场战斗都发。
      被当成名次记了几百条 → 复盘统计全错(209 条只有 1/2/3 三档 = 胜/平/负)。
    真实名次改由【淘汰顺序】推算(见 _track_eliminations / _guess_rank)。
"""
import sys
import threading
import time
from core import silence as _SIL

try:
    from .records import add_record
    from . import id_map as IDMAP
except ImportError:
    from core.records import add_record
    from core import id_map as IDMAP

MY_USER_KEY = "my_user_id"

# ============================================================ 本局禁用势力(v4.141★)
# 现状: 桥接脚本 WATCH 里已经在采样 CNotifyChessGeneralList / CNotifyAllChessGeneral /
#   CNotifyHasChessPlayerChooseGeneral 这几条选主公相关的包, 但 Python 侧【从来没解析过】,
#   也没有任何 ban 字段 —— 也就是说"本局禁了哪些势力"以前只能靠用户在界面上手勾。
#
# ★ 这里的原则: 【没有真机样本就不许猜字段名】。猜错的 ban 比没有 ban 更糟 ——
#   它会把本来能用的主公排除掉, 而且用户完全看不出是助手读错了。
#   所以扫描器只在【字段名像 + 取值也像】时才认, 认不出就返回空(决策层退回 UI 勾选)。
#   每次遇到选主公的包都会把原始结构存一份样本到 data/_ban_samples.jsonl,
#   拿到真机样本后照着补规则即可。

# 字段名里带这些词 = "禁用"语义
_BAN_WORDS = ("ban", "forbid", "disable", "exclude", "prohibit", "filter")
# 字段名里带这些词 = "势力"语义(去掉下划线/大小写后比对)
_FACTION_WORDS = ("miniontype", "faction", "camp", "group", "race", "country")

# 势力代码 1~8(见 tavern_kb.FACTION_CODE), 超出这个范围的一律不当势力
_FACTION_CODES = {"1": "魏", "2": "蜀", "3": "吴", "4": "群",
                  "5": "黄巾", "6": "汉", "7": "西凉", "8": "袁"}


def _key_looks_banned(k):
    kk = str(k or "").lower().replace("_", "").replace("-", "")
    if not kk:
        return False
    has_ban = any(w in kk for w in _BAN_WORDS)
    has_fac = any(w in kk for w in _FACTION_WORDS)
    # 要么"禁用"和"势力"两个语义都有(如 banMinionTypes), 要么单独一个"ban"+短字段名
    return (has_ban and has_fac) or (has_ban and len(kk) <= 12)


def _val_to_factions(v):
    """把字段值翻成势力中文名集合; 不像势力列表就返回 None(注意: 空集合也是有效结果,
    表示"这个字段在, 但一个都没禁" —— 所以这里用 None 而不是 set() 表示"认不出")。"""
    if isinstance(v, (list, tuple, set)):
        out = set()
        for x in v:
            s = str(x).strip()
            if s in _FACTION_CODES:                       # 数字代码
                out.add(_FACTION_CODES[s])
            elif s in set(_FACTION_CODES.values()):       # 中文名("魏")
                out.add(s)
        return out if out else None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        s = str(int(v))
        return {_FACTION_CODES[s]} if s in _FACTION_CODES else None
    if isinstance(v, str):
        # 逗号分隔的 "1,3,5" 或中文 "魏,吴"
        parts = [p.strip() for p in v.replace("，", ",").split(",") if p.strip()]
        out = set()
        for p in parts:
            if p in _FACTION_CODES:
                out.add(_FACTION_CODES[p])
            elif p in set(_FACTION_CODES.values()):
                out.add(p)
        return out if out else None
    return None


def scan_banned_factions(node, depth=0, _found=None):
    """在协议包的字典里找"本局禁用势力"。返回 (中文名集合, 命中的字段名)。

    只在【字段名像禁用势力】且【取值能翻成 1~8 势力】时才认。认不出 → (set(), "")。
    """
    if _found is None:
        _found = {}
    if depth > 6 or not isinstance(node, dict):
        return set(), ""
    for k, v in node.items():
        if isinstance(v, dict):
            scan_banned_factions(v, depth + 1, _found)
            continue
        if not _key_looks_banned(k):
            continue
        got = _val_to_factions(v)
        if got is None:
            continue
        # ★ 字段名只有"禁用"语义、没有"势力"语义时(如 banUserIDs / banList),
        #   值必须是【中文势力名】才敢认。纯数字 1~8 太容易是别的东西
        #   (用户ID / 序号 / 等级 / 营帐等级…), 认错一次就会误排除掉整个势力,
        #   而这种错界面上完全看不出来 —— 宁可漏(退回界面勾选), 不许猜。
        kk = str(k or "").lower().replace("_", "")
        if not any(w in kk for w in _FACTION_WORDS):
            # ★ 看【原始值】里有没有中文势力名, 不能看翻译后的结果:
            #   翻译后 [1,2] 也会变成 {魏,蜀}, 那就等于没检查。
            raw = (",".join(str(x) for x in v)
                   if isinstance(v, (list, tuple, set)) else str(v))
            if not any(f in raw for f in _FACTION_CODES.values()):
                continue
        _found[str(k)] = got
    if depth != 0 or not _found:
        return set(), ""
    # 多个字段都像时取并集(不同包写法不一样, 并集比任选一个稳)
    out = set()
    for s in _found.values():
        out |= s
    return out, "|".join(sorted(_found))


def _save_ban_sample(evt, data, got, field):
    """把选主公相关的包存一份样本 —— 拿到真机数据后照着补识别规则。

    ★ 只存【键名结构】不存整包: 整包里有账号信息, 没必要落盘; 而我们要的只是
      "这个包有哪些字段", 够用来确认禁用势力写在哪个字段里。
    """
    try:
        import json
        import os
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        p = os.path.join(base, "data", "_ban_samples.jsonl")
        keys = list(data.keys())[:40] if isinstance(data, dict) else []
        rec = {"ts": time.time(), "evt": evt, "keys": keys,
               "got": sorted(got), "field": field}
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        # 存样本失败不该影响对局解析, 但也不能静默 —— 不然永远不知道没存上
        _SIL.note("protocol._save_ban_sample", "禁用势力样本没存上: %s" % e)


class GameLive:
    """实时对局状态（协议直读，无OCR误差）"""
    def __init__(self):
        # ★用 RLock 而不是 Lock: 同一个线程可以嵌套多次 with self.lock(不会死锁)
        # 之前 CRespChessGetLastBattlePlayerData 解析里 _attach_board 内部持锁
        # + 外层又 with self.lock = 永久死锁——RLock 自动解决
        self.lock = threading.RLock()
        self.my_id = ""
        self.me = {}          # hp/coin/shopLevel/generalID/...
        self.others = []      # 对手列表
        self.shop = []        # 当前商店
        # v4.141★ 本局禁用的势力(中文名集合, 如 {"魏","吴"}) —— 选主公/选阵容都要用它。
        #   ban_src 记"从哪读到的": 自动读到 = "协议", 没读到 = ""(此时决策层退回 UI 手勾)。
        #   为什么要分开记: 界面上必须能区分"确实没禁"和"我没读到", 否则用户会以为没禁。
        self.banned_factions = set()
        self.ban_src = ""
        self.ban_field = ""   # 认出来时, 记下是哪个字段(真机样本回来后用来核对)
        self.turn = 0
        self.in_game = False
        self.battles = {"win": 0, "draw": 0, "lose": 0}
        self.last_event = ""
        self.last_ts = 0.0
        self.game_start_ts = 0.0
        self.auto_records = []   # 自动战绩 [(placement, result, ts)]
        self.game_over_recorded = False   # 同一局的GameOverMsg会重发，只记一次
        # ============ v4.68★ 真实名次: 用【淘汰顺序】推, 不再拿 emRus 当名次 ============
        #   用户原话: "对局复盘 tab 页中的主公ID不对, 并且看不到对局实际情况, 统计啥的也不对"。
        #   主公ID那条已修(见 id_map 的映射优先级), 这里修的是"统计不对"的根:
        #     旧代码 `placement = int(mine.get("emRus"))` 把 emRus 当"名次"记进战绩。
        #     反编译源码(数据包/外仓 all_tavernchess_classes.txt)里同一条包是这么用的:
        #         battleEndProtoData = { battleResult: e.emRus + 1, lostHp:0, rank:1, gameOver:!1 }
        #         2 == e.emRus && (i = e.iLostHp)      // 掉的血写进 lostHp
        #         3 == e.emRus && (i = 0)
        #     → emRus 是【这一场战斗的结果枚举】(胜/平/负, +1 后映射 TavernChessBattleGameResultType),
        #       而这条包【每场战斗都发】(见下面 _check_pending_game_over 的注释)。
        #     证据也摆在战绩里: 209 条只有 1/2/3 三档(100/97/12) —— 这不是名次分布, 是胜/平/负;
        #       而且"3 分钟只打了 1 场"和"24 分钟打了 12 场"记的是同一个档位 2。
        #   能算出真名次的只有一样东西: 8 人局的【淘汰顺序】(第 1 个死的=第 8 名, 活到最后=第 1 名)。
        #   数据来源: 每回合 DataUpdate / EnterTeam 里的 hp(自己 me + otherPlayer 8 人)。
        self.elim_order = []      # 淘汰顺序 [(uid, 当时血量, 时间戳), ...] 按发生先后
        self._dead_seen = set()   # 已经计过淘汰的 uid
        self._hp_seen = {}        # uid -> 最近一次血量(给"名次依据"当证据)
        self._players_seen = set()  # 这一局见过的玩家集合(估计总人数, 含自己)
        self.rank_est = 0         # 最近一次推算出的名次(0=推不出)
        self.rank_src = ""        # 名次的依据说明(写给战绩备注, 可复核)
        # 对局阶段: recruit(招募)/battle(战斗)/choose(选武将) —— 一律走下面的 phase 属性,
        #   因为"阶段切换"这个事实要在一个地方被打上真实时间戳(见 v4.50★ 真实时间时钟)。
        self._phase = ""
        # ============ v4.50★ 真实时间时钟(倒计时不受游戏加速影响) ============
        # 用户原话: "加一个可以记录真实时间的倒计时…让用户知道什么时候开打", 而且明说
        #   "不受游戏加速影响"。为什么必须自己记: 游戏页面把 Laya.timer/Tween 整体加速
        #   (加速xN), 屏幕上那个"还剩几秒"跑得比真实秒快 N 倍 —— 用户盯着它根本算不出
        #   "真实还有多久开打"。所以这里只认【墙上时钟】(time.time()), 用协议事件打点:
        #     打完(CNotifyChessBattleEnd) → recruit(结算期, 也就是下一轮的起点)
        #     进回合(CNotifyChessEnterTeam) → 仍是 recruit(操作期开始, 起点不推后)
        #     开打(CNotifyChessBattleStart) → battle ← 在这里收下"起点→开打"的真实秒当样本
        #   油猴端(bridge/sgs_bridge.user.js 的 rtNote)用的是【同一条定义】, 两处数字同源。
        self._rt_phase_since = 0.0    # 当前阶段是什么真实时间开始的
        self._rt_recruit_at = 0.0     # 本轮起点 = "上一场打完"的真实时间
        self._rt_battle_at = 0.0      # 最近一次"开打"的真实时间
        self._rt_hist = []            # 最近几轮"打完→开打"的实测真实秒(取中位数当估计)
        self._rt_last_recruit_real = None   # 刚刚这一轮的真实历时(开打那一刻写死)
        self._rt_marks = []           # 最近几条打点 [(真实时间, 新阶段), ...] —— 排查用
        # ---- ★goodsID → chessID 映射(positionChess里存的是goodsID不是chessID!) ----
        self.goods_map = {}      # {goodsID(int): chessID(int)} —— 从shopGoods/buyChess里建
        # ---- 卡池统计(整局): chessID -> 本局被买走次数(CRespShopBuyChess实锤) ----
        self.pool_taken = {}
        # ---- v3.20.3 战斗胜负状态机 ----
        self._hp_before_battle = None   # 开战时血量基准
        self._await_hp_judge = False    # BattleEnd后挂起, 等下次hp刷新时判定
        # ---- 打牌计数(李广【飞将】等"每使用N张"型技能进度) ----
        self.used_cards_this_turn = 0   # 本回合已打武将牌数
        self.used_cards_total = 0       # 整局累计
        # ---- 主公技能状态(CNotifyUseSkill skillID 4位=主公技能) ----
        self.lord_skill_used_turn = 0   # 本回合已用的主公技能ID(0=未用)
        self.lord_skill_used_total = 0  # 整局主公技能触发总次数
        # ---- 整局结束延迟确认(实锤: CNtfChessGameOverMsg每场战斗都发!) ----
        self._pending_game_over = None  # {ts, infos} 30秒无新回合才确认整局结束
        # ============ v3.44★ 三区域/白名单/当先 buff ============
        # 协议过滤命中的特殊卡(主公技能/锦囊产物等)缓存池(不再进战斗区乱污染)
        self._pending_buffs = []
        # CHESS_ADD_BUFF 事件: {chessID|goodsID|targetID: [{ts, proto}...]}
        self._buff_events = {}
        # chessID -> threading.Event (wait_buff_for 用)
        self._buff_events_signal = {}
        # 最近一次 DOM 快照的时间戳
        self._dom_snapshot_ts = 0.0
        # 修正函数保留不动(兜底用): 若项目里有就兼容调用; 目前占位逻辑在_apply里会自动走

    # ================== v4.50★ 真实时间时钟 ==================
    @property
    def phase(self):
        return self._phase

    @phase.setter
    def phase(self, ph):
        """任何地方给 phase 赋值都会经过这里 —— 于是"阶段切换"只在一个地方被打点。

        为什么不挨个去改那 8 处赋值: 它们分散在协议分支里(回合开始/买卡/刷新/开打/选将…),
        以后再加分支就会漏掉打点; 属性 setter 是唯一的必经之路, 漏不掉。
        同一个值重复赋值(招募期买卡/刷新都会重设 recruit)不算切换, 不重置计时。
        """
        new = str(ph or "")
        old = getattr(self, "_phase", "") or ""
        self._phase = new
        if not new or new == old:
            return
        # 这里【不用】try/except包着: _rt_on_phase 是纯算术(时间戳/列表), 不会失败;
        # 而吞掉异常等于"时钟悄悄不走了还没人知道"(dev/_verify_static.py 也在盯 except:pass 的增长)。
        self._rt_on_phase(old, new)

    def _rt_on_phase(self, old, new, now=None):
        """阶段切换 → 墙上时钟打点(见 __init__ 里"真实时间时钟"的说明)。

        "一轮"的定义必须与油猴端 rtNote 完全一致, 否则同一时刻两个界面会显示两个数:
          一轮 = 「上一场打完(结算开始)」→「下一场开打」之间的真实秒。
        这里不用特意写"打完"分支: 协议里打完那一刻走的正是 BattleEnd → phase='recruit'
        (见 CNotifyChessBattleEnd 分支), 它就是本轮 recruit 的起点; 随后的
        CNotifyChessEnterTeam 到达时 phase 已经是 recruit(同值不重打点), 起点不会被推后。
        """
        now = time.time() if now is None else now
        self._rt_phase_since = now
        if new == "recruit":
            self._rt_recruit_at = now
        elif new == "battle":
            self._rt_battle_at = now
            if self._rt_recruit_at:
                d = now - self._rt_recruit_at
                # 只收"像样"的一轮: 重连/收尾时可能连着来几条转移, 几十毫秒的那种不是样本
                if 1.0 <= d <= 600.0:
                    self._rt_last_recruit_real = round(d, 1)
                    self._rt_hist.append(round(d, 2))
                    del self._rt_hist[:-6]
        self._rt_marks.append((round(now, 2), new))
        del self._rt_marks[:-12]

    def real_clock(self):
        """真实时间倒计时(不受游戏加速影响) → dict, 给助手 UI 显示 / 日志留证。

        返回: {phase, phase_cn, elapsed, sample, last_recruit_real, eta_battle, text}
          · elapsed          当前阶段已经过去的【真实秒】(墙上时钟量的)
          · sample           最近几轮"上轮打完→开打"实测真实秒的中位数(= 估计依据)
          · eta_battle       本轮距开打还剩多少真实秒; 没样本时是 None —— 不编数字
          · text             一行可直接摆到界面上的文字(与油猴角标同一套措辞, 两处数字同源)
        """
        now = time.time()
        ph = self._phase or ""
        cn = {"recruit": "招募", "battle": "战斗中", "choose": "选主公"}.get(ph, ph or "未进局")
        el = (now - self._rt_phase_since) if self._rt_phase_since else 0.0
        el = max(0.0, el)
        sample = None
        hist = sorted(self._rt_hist or [])
        if hist:
            m = len(hist) // 2
            sample = round(hist[m] if len(hist) % 2 else (hist[m - 1] + hist[m]) / 2.0, 1)
        eta = None
        if ph == "recruit" and sample:
            eta = round(max(0.0, sample - el), 1)
        if ph == "recruit":
            # 招募期(含打完后的结算): "上轮打完 Ns" = 距上一场打完过了多少【真实】秒
            tail = " | 上轮打完 %.0fs" % el
            if eta is not None:
                text = "⏱真实 距开打 %s≈%.0fs(实测%.0fs)%s" % (
                    "⚠" if eta <= 10 else "", eta, sample, tail)
            else:
                text = "⏱真实 距开打 ?(打完一轮就有数)" + tail
        elif ph in ("battle", "choose") and self._rt_phase_since:
            text = "⏱真实 %s %.0fs" % (cn, el)
        else:
            text = "⏱真实 %s" % cn
        return {"phase": ph, "phase_cn": cn, "elapsed": round(el, 1), "sample": sample,
                "last_recruit_real": self._rt_last_recruit_real, "eta_battle": eta,
                "recruit_at": round(self._rt_recruit_at, 2),
                "battle_at": round(self._rt_battle_at, 2), "text": text}

    # ---------- 主入口 ----------
    def feed(self, msgs):
        """吃桥接 drain_net 的协议流水 [{evt, full|brief|sample}]"""
        import json as _json
        self._check_pending_game_over()   # 整局结束延迟确认
        for m in msgs:
            evt = str(m.get("evt", ""))
            data = m.get("full") or m.get("sample")
            if data is None:
                b = m.get("brief")
                if b:
                    try:
                        data = _json.loads(b)
                    except Exception:
                        continue
            if data is None:
                continue
            self._apply(evt, data)
            # ---- v4.68★ 每吃一条包都看看"谁死了" → 攒出淘汰顺序(名次只能从这里来) ----
            try:
                self._track_eliminations()
            except Exception as e:
                # 不算致命(名次推不出而已), 但要说出来: 否则用户只会看到"名次未知"而不知道原因
                sys.stderr.write("[名次] 淘汰顺序统计失败(这一步不影响其它功能): %s: %s\n"
                                 % (type(e).__name__, e))
            # ---- v3.20: 全网嗅探对手阵容(otherPlayer没有棋子字段, 独立协议推) + 未知棋子结构记录 ----
            try:
                self._sniff_any_chess(evt, data)
            except Exception:
                _SIL.note("core/protocol.py:235", "self._sniff_any_chess(evt, data)")

    # ================== v4.68★ 淘汰顺序 → 真实名次 ==================
    def _reset_rank_track(self):
        """新一局开始: 淘汰榜清零(必须在"新一局"判定里调用, 否则上局死的人会被算进来)。"""
        self.elim_order = []
        self._dead_seen = set()
        self._hp_seen = {}
        self._players_seen = set()
        self.rank_est = 0
        self.rank_src = ""

    def _track_eliminations(self):
        """把"谁的血量归零了"按发生顺序记下来 —— 这是唯一能推出真实名次的东西。

        口径(8 人自走棋): 第 1 个被淘汰的 = 第 8 名, 最后活着的 = 第 1 名。
        所以只要知道自己排在第几个被淘汰, 名次就是 总人数 - 名次序号 + 1。

        为什么不能省这一步: 旧代码拿协议里的 emRus 当名次(见 __init__ 里的源码证据),
        而 emRus 是【每场战斗的结果枚举】—— 于是 209 条战绩里"第 1 名 100 次"其实是
        "最后一场战斗赢了", 用户看到的就是"统计啥的也不对"。

        保守原则: 血量读不到就不记(宁可不写名次, 也不写一个假的)。
        """
        with self.lock:
            me = dict(self.me or {})
            rows = [("", me)]
            for o in (self.others or []):
                if isinstance(o, dict):
                    rows.append((str(o.get("userID") or ""), o))
            my = str(self.my_id or "")
            for uid, p in rows:
                hp = p.get("hp")
                if not isinstance(hp, (int, float)):
                    continue
                key = uid or (my if p is me else "")
                if not key:
                    continue
                self._players_seen.add(key)
                self._hp_seen[key] = hp
                if hp <= 0 and key not in self._dead_seen:
                    self._dead_seen.add(key)
                    self.elim_order.append((key, hp, time.time()))
            if len(self._players_seen) < len(self.others or []) + 1:
                # 自己 or 部分对手还没见过血量: 把 others 里的 uid 也算进人数(总人数口径更准)
                for o in (self.others or []):
                    if isinstance(o, dict) and str(o.get("userID") or ""):
                        self._players_seen.add(str(o["userID"]))

    def _guess_rank(self):
        """按淘汰顺序推最终名次 → (名次, 依据说明)。推不出就 (0, 原因), 绝不硬编一个数。

        三条分支, 都写清了依据(写进战绩备注, 事后能复核):
          ① 自己被淘汰过 → 名次 = 总人数 - (自己是第几个被淘汰的) + 1
          ② 自己没被淘汰, 且其余人都已淘汰 → 第 1 名(吃鸡)
          ③ 自己没被淘汰, 但同桌还有人活着 → 【不猜】: 这多半是提前退出/断线/我们漏收包,
             报"第 1 名"就又是假数据(旧代码就是这么把 100 局记成"胜利"的)。
        """
        my = str(self.my_id or "")
        n = len(self._players_seen) or (len(self.others or []) + 1)
        order = [u for u, _hp, _ts in self.elim_order]
        if n < 2:
            return 0, "只见到 %d 个玩家的血量, 推不出名次" % n
        if my and my in order:
            i = order.index(my)          # 0-based = 第 i+1 个被淘汰
            return max(1, n - i), "淘汰顺序: 你是第 %d 个被淘汰(共 %d 人) → 第 %d 名" % (i + 1, n, max(1, n - i))
        if len(order) >= n - 1:
            return 1, "淘汰顺序: 其余 %d 人都已淘汰, 你活到最后 → 第 1 名" % len(order)
        return 0, ("自己没被淘汰, 但只看到 %d/%d 人被淘汰(提前退出/断线/漏包都可能) → 名次不猜"
                   % (len(order), max(0, n - 1)))

    # ================== 整局结束延迟确认 ==================
    def _check_pending_game_over(self):
        """CNtfChessGameOverMsg 每场战斗都发(实锤)。
        挂起30秒: 若来了新回合(EnterTeam/DataUpdate/EndRecruit)=单场结束, 取消;
        30秒无新回合=真整局结束, 才记战绩。"""
        p = self._pending_game_over
        if p and time.time() - p["ts"] > 30:
            self._pending_game_over = None
            try:
                self._finalize_game_over(p["infos"])
            except Exception:
                _SIL.note("core/protocol.py:317", "self._finalize_game_over(p['infos'])")

    # ================== 全网嗅探 ==================
    def _sniff_any_chess(self, evt, data):
        """递归扫描任何协议里的 {UserID, X=[{chessID/goodsID,attack,hp},...]} 结构，挂到对手阵容上。
        ★用 RLock + with self.lock 包裹整个 walk: 避免 walk 里多次调 _attach_board 反复持锁/释放,
        也避免和 _apply 持锁冲突"""
        if data is None:
            return
        seen_ids = set()
        # ★用 RLock 包裹整个 walk 过程, 避免多次持锁/释放争抢
        with self.lock:
            def walk(node, depth=0, path=""):
                if depth > 8 or id(node) in seen_ids:
                    return
                if isinstance(node, dict):
                    seen_ids.add(id(node))
                    uid = str(node.get("userID") or node.get("UserID") or
                              node.get("UserId") or node.get("user_id") or "")
                    if uid:
                        pc = node.get("positionChess") or node.get("PositionChess")
                        if isinstance(pc, list) and pc and all(isinstance(x, int) for x in pc[:3]):
                            if self._is_me(uid):   # v4.24★ 修: 原来拿 me["userID"] 比对(该键不存在→恒假)
                                self.me["_lineUp"] = list(pc)
                    if uid:
                        for k, v in node.items():
                            if k in ("userID", "UserID", "UserId", "user_id"):
                                continue
                            if isinstance(v, list) and 1 <= len(v) <= 16:
                                sample_count = sum(1 for x in v[:3] if isinstance(x, dict)
                                                   and any(ck in x for ck in
                                                   ("chessID", "chessId", "goodsID", "attack", "hp")))
                                if sample_count >= 1:
                                    # ★已持 RLock, 直接调 _attach_board_locked
                                    self._attach_board_locked(uid, k, v, evt, path)
                    for k, v in node.items():
                        walk(v, depth + 1, path + "." + str(k))
                elif isinstance(node, list) and 1 <= len(node) <= 128:
                    seen_ids.add(id(node))
                    for i, x in enumerate(node):
                        walk(x, depth + 1, f"{path}[{i}]")
            walk(data)

    def _attach_board(self, uid, field_name, board_list, evt, path):
        """把嗅探到的{userID -> 棋子列表} 挂到self.others对应条目下的_board上"""
        if not uid or not isinstance(board_list, list):
            return
        with self.lock:
            self._attach_board_locked(uid, field_name, board_list, evt, path)

    def _attach_board_with_info(self, uid, player_dict, evt):
        """一次性持锁: 挂对手阵容 + 更新对手基本信息(hp/coin/generalID/nickname)"""
        player = player_dict or {}
        board_list = player.get("lastLineUp") or []
        with self.lock:
            self._attach_board_locked(uid, "lastLineUp", board_list, evt, ".player.lastLineUp")
            # 顺便更新对手基本信息(合并进同一次持锁, 避免嵌套锁死锁)
            for o in self.others:
                if str(o.get("userID") or "") == uid:
                    if player.get("hp") is not None:
                        o["hp"] = player.get("hp")
                    if player.get("coin") is not None:
                        o["coin"] = player.get("coin")
                    if player.get("generalID") is not None:
                        o["generalID"] = player.get("generalID")
                    if player.get("nickname"):
                        o["nickname"] = player.get("nickname")
                    o["_board_ts"] = time.time()
                    break

    def _attach_board_locked(self, uid, field_name, board_list, evt, path):
        """内部方法: 假设已持 self.lock, 只做_board挂接"""
        # v3.97★ 增强过滤: 排除明确标记为对手的单位 (side=2)
        filtered_list = []
        for c in board_list:
            if isinstance(c, dict):
                side = c.get("side") or c.get("Side")
                if side in (2, "2"): continue
            filtered_list.append(c)
        board_list = filtered_list

        if self._is_me(uid):
            if not self.me.get("_board"):
                self.me["_board_src"] = f"{evt}::{field_name}{path}"
                self.me["_board"] = list(board_list)
            else:
                existing_gids = {str(c.get("goodsID")) for c in (self.me.get("_board") or [])
                                 if isinstance(c, dict) and c.get("goodsID")}
                merged = list(self.me["_board"])
                for c in board_list:
                    if isinstance(c, dict) and str(c.get("goodsID")) not in existing_gids:
                        merged.append(c)
                self.me["_board"] = merged
            return
        for o in self.others:
            if str(o.get("userID") or "") == uid:
                o["_board_src"] = f"{evt}::{field_name}{path}"
                o["_board"] = list(board_list)
                o["_board_ts"] = time.time()
                return
        # 对手不在当前 others 列表里, 追加
        self.others.append({"userID": uid, "_board": list(board_list),
                             "_board_src": f"{evt}::{field_name}{path}",
                             "_board_ts": time.time()})

    def _find_shop_goods(self, goodsID):
        """从当前shop快照里按goodsID找完整卡信息"""
        gid = str(goodsID)
        for g in self.shop:
            if str(g.get("goodsID")) == gid:
                return g
        return None

    def _add_to_board(self, card_dict):
        """往 me._board 里加一张卡(买卡成功时调用)"""
        if not isinstance(card_dict, dict):
            return
        gid = card_dict.get("goodsID")
        cid = card_dict.get("chessID")
        sid = card_dict.get("spellID") or card_dict.get("itemID")
        if not gid and not cid and not sid:
            return
        with self.lock:
            # v3.78★ 同步更新 goods_map
            if gid and cid:
                try: self.goods_map[int(gid)] = int(cid)
                except: _SIL.note("core/protocol.py:443", "self.goods_map[int(gid)] = int(cid)")
            
            board = list(self.me.get("_board") or [])
            # 按 goodsID 去重(防止重复加)
            if gid:
                for c in board:
                    if isinstance(c, dict) and str(c.get("goodsID")) == str(gid):
                        # v3.88★ 已存在也更新时间戳，防止被 300s 逻辑清理
                        c["_last_seen"] = time.time()
                        return   # 已存在
            
            card_to_add = dict(card_dict)
            card_to_add["_last_seen"] = time.time()
            board.append(card_to_add)
            self.me["_board"] = board

    def _remove_from_board(self, goodsID=None, chessID=None):
        """从 me._board 里删掉一张(遣散/上阵时调用)"""
        with self.lock:
            board = list(self.me.get("_board") or [])
            removed = False
            new_board = []
            for c in board:
                if not isinstance(c, dict):
                    new_board.append(c); continue
                if not removed:
                    match_gid = goodsID and str(c.get("goodsID")) == str(goodsID)
                    match_cid = chessID and str(c.get("chessID")) == str(chessID)
                    if match_gid or match_cid:
                        removed = True; continue
                new_board.append(c)
            self.me["_board"] = new_board

    # ==================== v3.44★ 白名单过滤 / DOM 快照 / 当先 buff ====================
    def _is_battle_compatible(self, card):
        """协议推送战斗区时的白名单校验.
        返回 True=可以进战斗区(真实武将卡); False=特殊卡(锦囊/主公技能/buff)→ 进 _pending_buffs."""
        if not isinstance(card, dict):
            return False
        
        # v3.94★ 强制过滤对手单位: 如果明确标记为对方(side=2)
        side = card.get("side") or card.get("Side")
        if side in (2, "2"):
            return False

        # v3.86★ 强制白名单: 8位 ID 且以 20/21 开头的一定是武将 (S10赛季特征)
        cid = card.get("chessID") or card.get("chessId")
        if cid:
            s_cid = str(cid)
            if len(s_cid) == 8 and (s_cid.startswith("21") or s_cid.startswith("20")):
                return True

        # 1) 黑名单字段任一命中 → 非战斗卡
        for f in ("isSpell", "isJinnang", "fromSpell", "spellID", "buffID", "isItem",
                  "skillID", "fromSkill", "isProp", "isConsumable", "isTool"):
            v = card.get(f)
            if v not in (None, False, 0, "", "0", "false", "False"):
                return False
        # 2) goodsType 黑名单
        for k in ("goodsType", "type", "ChessType", "chessType", "GoodsType"):
            v = card.get(k)
            try:
                vi = int(v)
            except (TypeError, ValueError):
                vi = None
            if vi is not None and vi in (2, 3, 5, 6, 8, 10, 11, 13, 50, 51, 99):
                return False
        # 3) chessID 锦囊前缀
        if cid is not None:
            s = str(cid)
            # 6 位 ID 且在锦囊段，高度怀疑是锦囊
            if len(s) == 6 and s[:2] in GameLive.SPELL_CID_PREFIX:
                # 除非有明确的攻血数据且不是 0
                atk = card.get("attack") or card.get("atk") or card.get("Attack")
                hp = card.get("hp") or card.get("Hp") or card.get("totalHp")
                try:
                    atk_i = int(atk)
                    hp_i = int(hp)
                except (TypeError, ValueError):
                    atk_i = 0; hp_i = 0
                if not (atk_i >= 1 and hp_i >= 1):
                    return False
            # 锦囊段的其他长度 ID (通常也是锦囊)
            elif len(s) >= 4 and s[:2] in GameLive.SPELL_CID_PREFIX:
                return False
        return True

    def apply_dom_snapshot(self, snap):
        """三区域 DOM 权威扫描结果写入(视觉真实态强制覆盖).
        snap = {battle:[7..], bench:[..], shop:[..], ts:float}
        ...
        """
        import copy as _cp
        import json as _json
        import os
        if not isinstance(snap, dict):
            return {"error": "snap 不是 dict"}
        
        # v3.86★ 导出调试 JSON
        try:
            from .config import DATA_DIR
            debug_path = os.path.join(DATA_DIR, "debug_dom_raw.json")
            with open(debug_path, "w", encoding="utf-8") as f:
                _json.dump(snap, f, ensure_ascii=False, indent=2)
        except Exception:
            _SIL.note("core/protocol.py:548", "from .config import DATA_DIR")

        stats = {"battle_written": 0, "bench_written": 0, "shop_written": 0, "skipped_special": 0}
        with self.lock:
            self._dom_snapshot_ts = float(snap.get("ts") or time.time())
            # 战斗区: 7 格按 slot_idx 对齐 → goodsID 数组
            battle_raw = snap.get("battle") or []
            line = [0, 0, 0, 0, 0, 0, 0]   # 固定 7 格
            # v3.71★ 诊断字段: 记录探测到的战斗格数
            self.me["_battle_raw_cnt"] = len(battle_raw)
            
            # v3.72★ 将战斗区武将也存入一个临时缓存，方便 UI 查找名字和属性
            battle_info_pool = {}
            # v3.86★ 增强过滤: 战斗区仅允许真实武将进入，过滤掉误识别的技能/锦囊
            for item in battle_raw:
                if not isinstance(item, dict): continue
                
                # v3.94★ 增强过滤: 过滤掉非本方单位 (side=2)
                side = item.get("side") or item.get("Side")
                if side in (2, "2"):
                    continue

                # v3.86★ 战斗区白名单过滤
                if not self._is_battle_compatible(item):
                    stats["skipped_special"] += 1
                    continue
                
                try:
                    idx = int(item.get("slot_idx") or 1) - 1
                except (TypeError, ValueError):
                    idx = 0
                idx = max(0, min(6, idx))
                gid = item.get("goodsID")
                if gid:
                    try:
                        gid_i = int(gid)
                        line[idx] = gid_i
                        battle_info_pool[str(gid_i)] = item
                    except (TypeError, ValueError):
                        line[idx] = 0
                
                # goods_map 同步写入
                cid = item.get("chessID")
                if gid and cid:
                    try:
                        self.goods_map[int(gid)] = int(cid)
                    except (TypeError, ValueError):
                        _SIL.note("core/protocol.py:595", "self.goods_map[int(gid)] = int(cid)")
                stats["battle_written"] += 1
            
            if battle_info_pool:
                if "_battle_info" not in self.me: self.me["_battle_info"] = {}
                self.me["_battle_info"].update(battle_info_pool)
            
            # v3.86★ 备战区深度合并: 从 _all_detected 提取所有可能的路径并去重
            all_detected = snap.get("_all_detected") or {}
            bench_all = list(snap.get("bench") or [])
            
            # 提取所有探测到的备战区数据
            detected_benches = all_detected.get("bench") or []
            for entry in detected_benches:
                data = entry.get("data")
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, (dict, int, str)) and item not in bench_all:
                            bench_all.append(item)
            
            # v3.87★ 额外检查 shop 是否也包含了一些应该在备战区的东西 (针对某些赛季 bug)
            # 有时商店探测到的东西其实是备战区的
            shop_raw = snap.get("shop") or []
            
            bench_raw = bench_all
            if stats["battle_written"] > 0 or any(line) or bench_raw or shop_raw:
                self.in_game = True

            # 如果 DOM 扫描到 0 格, 说明当时界面不在招募/战斗区, 这种情况不要覆盖(避免清空阵容)
            if stats["battle_written"] > 0 or any(line):
                self.me["_lineUp"] = line
            
            # 备战区(权威写入, 直接替换掉协议推送的延迟版本)
            new_bench = []
            if bench_raw:
                for c in bench_raw:
                    # v3.68★ 兼容原始 ID 数组
                    if not isinstance(c, dict):
                        if isinstance(c, (int, str)) and str(c).isdigit():
                            new_bench.append({"chessID": int(c), "_from_dom": True})
                        continue
                    
                    d = _cp.deepcopy(c)
                    gid = d.get("goodsID"); cid = d.get("chessID")
                    sid = d.get("spellID"); iid = d.get("itemID")
                    if not gid and not cid and not sid and not iid: continue
                    
                    # 同步 goods_map
                    if gid and cid:
                        try: self.goods_map[int(gid)] = int(cid)
                        except: _SIL.note("core/protocol.py:645", "self.goods_map[int(gid)] = int(cid)")
                    
                    d["_from_dom"] = True
                    d["_last_seen"] = time.time()
                    new_bench.append(d)
            
            # v3.88★ 更加激进的合并策略: 只要是最近 5 分钟内出现的都保留，除非显式冲突
            existing = list(self.me.get("_board") or [])
            dom_keys = set()
            for d in new_bench:
                k = str(d.get("goodsID") or d.get("spellID") or d.get("itemID") or ("cid@" + str(d.get("chessID"))))
                dom_keys.add(k)
            
            merged = list(new_bench)
            now = time.time()
            for old in existing:
                if not isinstance(old, dict): continue
                okey = str(old.get("goodsID") or old.get("spellID") or old.get("itemID") or ("cid@" + str(old.get("chessID"))))
                if okey not in dom_keys:
                    # 只要旧数据有名字或 ID，且是在 5 分钟内看到的，就保留
                    last_seen = old.get("_last_seen") or old.get("_board_ts") or 0
                    if now - last_seen < 300: # 5分钟
                        merged.append(old)
            
            self.me["_board"] = merged
            stats["bench_written"] = len(new_bench)

            # v3.88★ 导出处理后的调试 JSON
            try:
                debug_merged_path = os.path.join(DATA_DIR, "debug_live_state.json")
                with open(debug_merged_path, "w", encoding="utf-8") as f:
                    _json.dump({"me": self.me, "shop": self.shop, "ts": time.time()}, f, ensure_ascii=False, indent=2)
            except Exception:
                _SIL.note("core/protocol.py:678", "debug_merged_path = os.path.join(DATA_DIR, 'de")
            
            # 商店
            if shop_raw:
                new_shop = [_cp.deepcopy(x) for x in shop_raw if isinstance(x, dict)]
                if new_shop:
                    self.shop = new_shop
                    stats["shop_written"] = len(new_shop)
                    for g in new_shop:
                        try:
                            gid = int(g.get("goodsID") or 0)
                            cid = int(g.get("chessID") or 0)
                            if gid and cid: self.goods_map[gid] = cid
                        except: _SIL.note("core/protocol.py:691", "gid = int(g.get('goodsID') or 0)")
            
            # v3.85★ 累积学习
            try:
                learned_dict = {}
                for c in (bench_raw + shop_raw + battle_raw):
                    if not isinstance(c, dict): continue
                    gid = c.get("goodsID") or c.get("spellID") or c.get("itemID")
                    cid = c.get("chessID")
                    use_id = str(gid or cid)
                    if use_id and use_id != "0":
                        # 锦囊归一化
                        if use_id.isdigit() and len(use_id) == 6 and (30 <= int(use_id[:2]) <= 69 or use_id[:2] in ("88", "98", "99")):
                            use_id = "spell_" + use_id
                        
                        name = c.get("name") or c.get("n") or c.get("nameTxt") or c.get("heroName") or c.get("hero_name") or ""
                        if not name:
                            name = IDMAP.name_of(use_id)
                            if name.startswith("?"): name = ""

                        # v3.97★ 极致净化名字后再学习
                        if name:
                            name = IDMAP._clean_name(name)

                        learned_dict[use_id] = {
                            "n": name,
                            "src": "dom:scan",
                            "atk": c.get("attack") or c.get("atk") or c.get("attackTxt"),
                            "hp": c.get("hp") or c.get("totalHp") or c.get("hpTxt")
                        }
                if learned_dict:
                    IDMAP.learn_ids(learned_dict)
            except: _SIL.note("core/protocol.py:723", "learned_dict = {}")

        return stats

    def wait_buff_for(self, chessID, timeout_ms=3000, since_ts=None):
        """等待某个 chessID 的 CHESS_ADD_BUFF 推送(修复当先).
        若 timeout_ms 毫秒内收到返回 (True, events_list); 否则返回 (False, []).

        v4.108★ since_ts = 「本次上阵发起的时刻」(基线)。传了它就只认 ts >= since_ts 的新事件。
          为什么必须有这个(刷邓茂/陈珪"同一张卡反复上阵"时必踩):
            (1) 上一轮上阵留下的 buff 事件还在 30s 窗口内 → 一进来就命中【旧事件】立即返回 True;
            (2) 更隐蔽: _buff_events_signal 里的 Event 被上一轮 set 过就一直保持 True,
                连 ev.wait() 都会立刻返回 —— 于是脚本拿到的是【上一轮的回执】,
                而本次当先其实还没结算(黄巾兵还没出来), 下一步就跑在过期状态上, combo 断链。
          传基线后: 只看本次上阵之后新增的事件; 等待前先 clear 掉上一轮遗留的 set 标志;
          等完再复核一次, 确实没拿到新事件就老老实实返回 False(不假装成功)。
        """
        import threading as _th
        ev = None
        key = str(chessID)
        with self.lock:
            pre = self._buff_events.get(key) or []
            if since_ts is None:
                # 旧行为(保持向后兼容): 最近 30s 内有过就算命中
                now = time.time()
                fresh = [x for x in pre if now - x.get("ts", 0) < 30.0]
                if fresh:
                    return True, fresh
            else:
                # 只认本次基线之后的新事件(psend 后可能已经先到了)
                new = [x for x in pre if x.get("ts", 0) >= since_ts]
                if new:
                    return True, new
            ev = self._buff_events_signal.get(key)
            if ev is None:
                ev = _th.Event()
                self._buff_events_signal[key] = ev
            if since_ts is not None:
                # 关键: 清掉上一轮遗留的 set 标志, 否则 wait() 会立刻返回"假成功"
                try:
                    ev.clear()
                except Exception:
                    _SIL.note("core/protocol.py:765", "ev.clear()")
        ev.wait(timeout=float(timeout_ms) / 1000.0)
        with self.lock:
            res = [x for x in (self._buff_events.get(key) or [])]
            if since_ts is not None:
                res = [x for x in res if x.get("ts", 0) >= since_ts]
        return bool(res), res

    def _record_buff_event(self, target_key, proto_dict):
        """CHESS_ADD_BUFF 推送到时记录"""
        if not target_key:
            return
        key = str(target_key)
        item = {"ts": time.time(), "proto": dict(proto_dict or {})}
        with self.lock:
            self._buff_events.setdefault(key, []).append(item)
            # 长度截断, 避免内存膨胀
            lst = self._buff_events[key]
            if len(lst) > 100:
                self._buff_events[key] = lst[-100:]
            sig = self._buff_events_signal.get(key)
            if sig is not None:
                try:
                    sig.set()
                except Exception:
                    _SIL.note("core/protocol.py:790", "sig.set()")


    # ---------------- v3.43 锦囊统一判定(全字段) ----------------
    # 真实协议里锦囊卡片(=需要点"用锦囊"消耗后获得效果或获得武将的那些)
    #   不一定带 isSpell 字段, 所以我们要做一个多重规则综合判定.
    # 字段线索: type=2/3/5 / ChessType=501~599 / isSpell=1 / goodsType / spellID /
    #          chessID 前缀 (31-50, 62, 63, 68, 69, 88, 98, 99 三国杀里常见的锦囊/活动棋子段) /
    #          名字长度极短(≤2)但带goodsID(一般武将3字)的特殊道具
    # v3.96★ 锦囊识别增强: 涵盖 S10 更多锦囊/道具 ID 段 (30-69)
    SPELL_CID_PREFIX = tuple(str(i) for i in range(30, 70)) + ("88", "98", "99")
    # goodsType: 2=锦囊 3=道具 5=主公令/锦囊卡  (不同版本字段值会变, 保持合集)
    SPELL_GOODS_TYPES = {2, 3, 5, 6, 8, 10, 11, 13, 50, 51, 99}
    SPELL_BOOL_FIELDS = ("isSpell", "isJinnang", "isItem", "fromSpell", "isProp",
                         "isConsumable", "isTool", "isCard", "isGiveCard", "isSpellCard")
    SPELL_TAG_FIELDS = ("spellID", "skillID", "itemID", "jinNangID", "jinnangID", "jnID", "SpellID")

    def is_spell_card(self, c):
        if not isinstance(c, dict):
            return False
        
        # v3.96★ 增强型 ID 提取: 从所有可能的锦囊/道具字段寻找 ID
        cid = str(c.get("spellID") or c.get("itemID") or c.get("cardID") or 
                  c.get("jinNangID") or c.get("jinnangID") or c.get("jnID") or 
                  c.get("chessID") or c.get("goodsID") or c.get("ID") or "")
        
        # v3.96★ 强制锦囊判定: 如果 ID 以 spell_ 开头
        if cid.startswith("spell_"): return True
        
        # v3.96★ 针对 S10 锦囊 ID 范围增强 (30-69 段)
        if cid.isdigit():
            # 6-7位 ID (如 310072)
            if 6 <= len(cid) <= 7:
                prefix = int(cid[:2])
                # 30-69 段通常是赛季专属锦囊/道具
                if 30 <= prefix <= 69: return True
                if prefix in (88, 98, 99): return True
            # 短 ID 判定 (如 31)
            elif 2 <= len(cid) <= 3:
                prefix = int(cid)
                if 30 <= prefix <= 69: return True

        # (1) 显式flag字段
        for f in self.SPELL_BOOL_FIELDS:
            v = c.get(f)
            if v not in (None, False, 0, "", "0", "false", "False"):
                return True
        
        # (2) 有锦囊/技能/道具ID字段 (排除 10xx 段的主公 ID)
        for f in self.SPELL_TAG_FIELDS:
            v = c.get(f)
            if v not in (None, 0, "", "0"):
                try:
                    vn = int(v)
                    if 1000 <= vn <= 2000: continue # 主公 ID
                except: _SIL.note("core/protocol.py:845", "vn = int(v)")
                return True
        
        # (3) goodsType/type/ChessType
        for k in ("goodsType", "type", "ChessType", "chessType", "GoodsType"):
            v = c.get(k)
            try:
                vn = int(v)
            except (TypeError, ValueError):
                continue
            if vn in self.SPELL_GOODS_TYPES:
                return True
            if 500 <= vn <= 699: # 游戏内部约定锦囊段
                return True
        
        # (4) ID前缀特征 (道具锦囊段)
        if len(cid) >= 4 and cid[:2] in self.SPELL_CID_PREFIX:
            # 排除 8 位复合 ID (武将)
            if len(cid) == 8 and (cid.startswith("20") or cid.startswith("21")):
                return False
            return True
        
        # (5) 名字特征
        name = str(c.get("name") or c.get("chessName") or c.get("n") or "")
        if len(name) >= 1 and any(k in name for k in ("锦囊", "令", "策", "之策", "之计", "卷轴",
                                                        "礼物", "道具", "信件", "书", "图",
                                                        "赏赐", "宝箱", "杂耍")):
            return True
        return False

    def list_spell_cards(self):
        """返回当前备战区里识别到的所有锦囊列表 (list[dict]).
        同时加一层 _spell 标记, 保证渲染/日志里能看见."""
        res = []
        board = list(self.me.get("_board") or [])
        for c in board:
            if not isinstance(c, dict):
                continue
            if self.is_spell_card(c):
                cc = dict(c)
                cc["_spell"] = True
                res.append(cc)
        return res

    def _read_banned(self, pd, evt):
        """扫描包里的"本局禁用势力"。

        认得出 → 更新 banned_factions 并记 ban_src="协议";
        认不出 → 【保持原样, 不动】(空的就还是空), 让决策层退回界面上勾的那份。
        ★ 认不出时绝对不猜: 猜错的 ban 会把本来能用的主公排除掉, 而用户完全看不出
          是助手读错了 —— 比"没读到"危险得多。
        每次都存一份键名样本, 真机跑一局后照着 data/_ban_samples.jsonl 补规则。
        """
        got, field = scan_banned_factions(pd if isinstance(pd, dict) else {})
        if got:
            self.banned_factions = set(got)
            self.ban_src = "协议"
            self.ban_field = field
        _save_ban_sample(evt, pd, got, field)

    def _apply(self, evt, data):
        with self.lock:
            # 打牌计数: 数据是list形态[["1388"],...], _dig会转成dict丢掉 → 在dig之前截获
            if evt in ("TEAM_CHESS_DELETE_HAND_CHESS", "cmsg.TEAM_CHESS_DELETE_HAND_CHESS"):
                self._count_played_cards(data)
                return
            self.last_event, self.last_ts = evt, time.time()
            pd = self._dig(data)
            if not isinstance(pd, dict):
                return
            if evt in ("cmsg.CNotifyChessEnterTeam",):
                # 新回合开始 = 上一个GameOverMsg只是单场战斗结束, 取消整局确认
                self._pending_game_over = None
                old_boards = {str(o.get("userID")): (o.get("_board"), o.get("_board_src"))
                              for o in self.others if isinstance(o, dict)}
                self._take_owner(pd.get("owner") or {})
                self.others = list(pd.get("otherPlayer") or [])
                # 保留嗅探到的阵容(实锤bug: EnterTeam每回合替换others, 嗅探的_board全被清掉)
                for o in self.others:
                    k = str(o.get("userID"))
                    if k in old_boards:
                        o["_board"], o["_board_src"] = old_boards[k]
                ti = pd.get("teamInfo") or {}
                self.turn = int(ti.get("turn") or self.turn or 0)
                if not self.in_game:   # 新一局开始: 重置整局卡池统计
                    self.pool_taken = {}
                    self._reset_rank_track()      # v4.68★ 淘汰榜也跟着清零(否则上局死的人会算进来)
                self.in_game = True
                self.game_over_recorded = False   # 新一局开始，允许记录战绩
                self.used_cards_this_turn = 0     # 新回合: 已打武将牌计数清零
                self.lord_skill_used_turn = 0     # 新回合: 主公技能(限1次型)重置
                if not self.game_start_ts:
                    self.game_start_ts = time.time()
                sg = pd.get("shopGoods") or self._deep(data, "shopGoods")
                if sg:
                    self.shop = sg
                self.phase = "recruit"
            elif evt in ("cmsg.CNotifyEndRecruit", "cmsg.CNotifyChessDataUpdate"):
                # 招募结束(带chessPlayer.hp) / 数据更新(带chessBaseInfo.hp) → 可能触发胜负判定
                if evt == "cmsg.CNotifyEndRecruit":
                    self._pending_game_over = None   # 新回合活动=非整局结束
                    self._take_owner(pd.get("chessPlayer") or {})
                else:
                    base = pd.get("chessBaseInfo") or {}
                    if base:
                        self._take_owner(base, keep_name=True)
                    self._pending_game_over = None
                # otherPlayer在DataUpdate里也会刷新(金币/血量/商店等级变动)
                new_others = pd.get("otherPlayer") or self._deep(data, "otherPlayer") or []
                if isinstance(new_others, list) and len(new_others) >= 5:
                    # 拷贝已嗅探到的_board到新的other列表(按userID)
                    old_boards = {str(o.get("userID")): (o.get("_board"), o.get("_board_src"))
                                  for o in self.others if isinstance(o, dict)}
                    for o in new_others:
                        k = str(o.get("userID"))
                        if k in old_boards:
                            o["_board"], o["_board_src"] = old_boards[k]
                    self.others = new_others
                # v3.24: DataUpdate也带shopGoods(商店变动推送)——快照保持实时, 买卡goodsID才不会过期
                sg = pd.get("shopGoods") or self._deep(data, "shopGoods")
                if sg:
                    self.shop = sg
                if self.phase not in ("battle", "choose"):
                    self.phase = "recruit"
            # ======== ★游戏内自定义事件(非cmsg. 但直接带卡结构) v3.41 ========
            # DELETE_CHESS: 棋子从棋盘/备战区移除 (数组形态 [[{goodsID, chessID, pos, ...}]])
            # 每次遣散/上阵/棋子死亡都会推, 可以用来实时更新 _board 和 _lineUp
            elif evt in ("DELETE_CHESS",):
                # data 可能是 [[{卡字典}]] 或 [{卡字典}] 或 直接 {卡字典}
                removed_gids = []
                cands = []
                pd_inner = pd   # _dig 之后的 dict
                if pd_inner:
                    cands.append(pd_inner)
                # 再试原始 data (嵌套数组可能 _dig 剥不完全)
                if isinstance(data, list):
                    def _collect_deep(node, depth=0):
                        if depth > 4: return
                        if isinstance(node, dict):
                            cands.append(node); return
                        if isinstance(node, list):
                            for el in node: _collect_deep(el, depth + 1)
                    _collect_deep(data)
                for card in cands:
                    if not isinstance(card, dict): continue
                    gid = card.get("goodsID")
                    cid = card.get("chessID")
                    if gid:
                        removed_gids.append(int(gid))
                    # 如果 pos 指明位置, 从 _lineUp 也移除
                    pos = card.get("pos")
                    if pos and 1 <= pos <= 7:
                        line = self.me.get("_lineUp") or []
                        line = [0 if (int(x) == int(gid) or int(x) == int(cid or -1)) else x
                                for x in line]
                        self.me["_lineUp"] = line
                if removed_gids:
                    # 从 _board 移除所有被删除的卡
                    new_board = []
                    for c in (self.me.get("_board") or []):
                        if isinstance(c, dict) and int(c.get("goodsID") or 0) in removed_gids:
                            continue
                        new_board.append(c)
                    self.me["_board"] = new_board
            # CHESS_INFO_UPDATE: 棋子属性变化(HP/攻击力/buff, 不是加/移卡)
            # 结构: {goodsID, hpValue, attValue, buffParams, attDiffValue}
            elif evt in ("CHESS_INFO_UPDATE",):
                gid = pd.get("goodsID") if pd else None
                att = pd.get("attValue") if pd else None
                hp = pd.get("hpValue") if pd else None
                if gid and (att is not None or hp is not None):
                    gid = int(gid)
                    with self.lock:
                        # 更新 shop 快照里对应卡的属性
                        for g in self.shop:
                            if int(g.get("goodsID") or 0) == gid:
                                if att is not None: g["attack"] = att
                                if hp is not None: g["hp"] = hp
                        # 更新 _board 里对应卡的属性
                        for c in (self.me.get("_board") or []):
                            if isinstance(c, dict) and int(c.get("goodsID") or 0) == gid:
                                if att is not None: c["attack"] = att
                                if hp is not None: c["hp"] = hp
            # CHESS_USE_SPELL: 游戏内锦囊消耗事件 [ChessID, UniqueID, SpellID, [targets...], flag, 0]
            # 这里的 SpellID 能用来判断是什么类型的锦囊
            elif evt in ("CHESS_ADD_BUFF",):
                # v3.44★ buff 推送 — 把所有可能的 target 键(chessID / goodsID / target /
                # targetID / TargetID / UniqueID)都记录一遍, 保证 wait_buff_for(chessID)
                # 一定命中(丁奉/葛玄当先时, 服务器端一般推 chessID 作为 target).
                def hunt_tgt(n, depth=0):
                    if depth > 6 or n is None:
                        return []
                    if isinstance(n, dict):
                        keys = []
                        for t in ("chessID", "ChessID", "goodsID", "GoodsID",
                                  "target", "targetID", "TargetId", "TargetID",
                                  "uniqueID", "UniqueID", "tarId", "owner"):
                            v = n.get(t)
                            if v not in (None, "", 0):
                                try:
                                    keys.append(int(v))
                                except (TypeError, ValueError):
                                    _SIL.note("core/protocol.py:1031", "keys.append(int(v))")
                        for vv in n.values():
                            keys.extend(hunt_tgt(vv, depth + 1))
                        return keys
                    if isinstance(n, list):
                        r = []
                        for e in n:
                            r.extend(hunt_tgt(e, depth + 1))
                        return r
                    return []
                targets = hunt_tgt(data)
                if not targets and isinstance(data, list) and data:
                    # CHESS_ADD_BUFF 格式: [target, source, buffID, buffParams, ...]
                    for idx in (0, 1):
                        try:
                            t = int(data[idx])
                            if t: targets.append(t)
                        except (TypeError, ValueError):
                            _SIL.note("core/protocol.py:1049", "t = int(data[idx])")
                for t in set(targets):
                    if not t: continue
                    self._record_buff_event(t, data if isinstance(data, dict) else {"raw": str(data)[:200]})
            # CHESS_REMOVE_BUFF: 仅计数, 不用信号
            elif evt in ("CHESS_REMOVE_BUFF",):
                pass
            elif evt in ("cmsg.CNtfChessUpdateInfoMsg", "CNtfChessUpdateInfoMsg"):
                chess_list = pd.get("Chess") or []
                if isinstance(chess_list, list) and chess_list:
                    for c in chess_list:
                        if not isinstance(c, dict): continue
                        
                        # v3.96★ 强制过滤对手单位 (side=2)
                        side = c.get("side") or c.get("Side")
                        if side in (2, "2"):
                            continue
                            
                        gid = c.get("goodsID")
                        cid = c.get("chessID")
                        pos = c.get("pos")
                        try:
                            pos_n = int(pos) if pos is not None and pos != "" else None
                        except (TypeError, ValueError):
                            pos_n = None
                        if gid and cid:
                            self.goods_map[int(gid)] = int(cid)
                        extras = {}
                        for tkey in ("type", "tag", "kind", "source", "spellID", "skillID", "itemID",
                                     "isSpell", "isItem", "isGeneral", "isJinnang", "fromSpell",
                                     "fromSkill", "spellName", "skillName", "GeneralSkillID",
                                     "ChessType", "chessType", "GoodsType", "goodsType"):
                            if tkey in c and c[tkey] not in (None, ""):
                                extras[tkey] = c[tkey]
                        if extras:
                            c = dict(c)
                            c.update(extras)
                        is_battle = (pos_n is not None and 1 <= pos_n <= 7)
                        if is_battle:
                            # v3.44★白名单过滤: 特殊卡不进战斗区
                            if not self._is_battle_compatible(c):
                                self._pending_buffs.append(dict(c))
                                if len(self._pending_buffs) > 200:
                                    self._pending_buffs = self._pending_buffs[-200:]
                                continue
                            line = self.me.get("_lineUp") or []
                            # 按 pos_n 写入指定格(更稳定)
                            line_len = len(line)
                            if line_len < 7:
                                line = list(line) + [0] * (7 - line_len)
                            try:
                                slot = int(pos_n) - 1
                                if 0 <= slot <= 6:
                                    line = list(line)
                                    line[slot] = int(gid) if gid else 0
                                    self.me["_lineUp"] = line
                                    continue
                            except (TypeError, ValueError):
                                _SIL.note("core/protocol.py:1107", "slot = int(pos_n) - 1")
                            # 兜底: 没 goodsID 或 pos 不合法, 填空格
                            if gid and int(gid) not in [int(x) for x in line if x != 0]:
                                for i in range(len(line)):
                                    if line[i] == 0:
                                        new_line = list(line)
                                        new_line[i] = int(gid)
                                        self.me["_lineUp"] = new_line
                                        break
                        else:
                            self._add_to_board(c)
            elif evt in ("cmsg.CNotifyChessBattleStart", "cmsg.CNotifyChessFightStart",
                         "CNotifyChessFightStart", "CNotifyChessBattleStart"):
                self.phase = "battle"
                # 实锤: CNotifyChessBattleEnd的protoData是空的(无battleResult字段)!
                # 且BattleEnd时刻hp还没掉(结算在下一次DataUpdate/EnterTeam).
                # → 只记基准+挂起, 判定挪到 _take_owner(hp刷新时)
                self._hp_before_battle = self.me.get("hp")
                self._await_hp_judge = True
            elif evt == "cmsg.CRespChessChooseGeneral":
                self.phase = "choose"
                self._read_banned(pd, evt)
            elif evt in ("cmsg.CNotifyChessGeneralList", "CNotifyChessGeneralList",
                         "cmsg.CNotifyAllChessGeneral", "CNotifyAllChessGeneral",
                         "cmsg.CNotifyHasChessPlayerChooseNum",
                         "CNotifyHasChessPlayerChooseNum"):
                # v4.141★ 这几条是"选主公"相关的包, 桥接一直在采样但 Python 从来没解析。
                #   本局禁了哪些势力应该就在这些包里 —— 扫一遍, 认得出就记下来。
                self.phase = "choose"
                self._read_banned(pd, evt)
            elif evt == "cmsg.CRespShopRefreshChess":
                # v3.23: 快照实时更新(刷新响应自带新商店), _deep兜底应对非常规响应结构
                sg = pd.get("shopGoods") or self._deep(data, "shopGoods")
                if sg:
                    self.shop = sg
                    # ★只要 shopGoods 非空就说明在对局里(EnterTeam 可能错过了)
                    self.in_game = True
                    if not self.game_start_ts:
                        self.game_start_ts = time.time()
                    # ★建 goodsID → chessID 映射(positionChess里存的是goodsID!)
                    for g in sg:
                        if isinstance(g, dict):
                            _gid = g.get("goodsID")
                            _cid = g.get("chessID")
                            if _gid and _cid:
                                self.goods_map[int(_gid)] = int(_cid)
                coin = pd.get("restCoin")
                if coin is None:
                    coin = self._deep(data, "restCoin")
                if coin is not None:
                    # ★ 顺手记一笔真实花费: 响应里的 restCoin 是服务器算完剩下的钱,
                    #   和"发包前我们看到的钱"之差就是这次操作真正花掉的。
                    #   这比按配置表的费用估算准 —— 打折/活动/返利都会体现在这里。
                    try:
                        _old = int(self.me.get("coin") or 0)
                        _d = _old - int(coin)
                        if _d > 0:
                            self.me["_spent"] = int(self.me.get("_spent") or 0) + _d
                            self.me["_last_spend"] = _d
                    except Exception:
                        pass
                    self.me["coin"] = coin
                self.phase = "recruit"
            elif evt == "cmsg.CRespShopBuyChess":
                self.phase = "recruit"
                # ★任何买卡响应都设 in_game(EnterTeam 可能错过了)
                self.in_game = True
                if not self.game_start_ts:
                    self.game_start_ts = time.time()
                bc = pd.get("buyChess") or {}
                gid = bc.get("goodsID") or pd.get("goodsID") or self._deep(data, "goodsID")
                if gid:
                    for g in self.shop:
                        if g.get("goodsID") == gid:
                            g["_sold"] = True
                            break
                # 卡池统计: 被买走的棋子离开公共卡池
                taken_cid = bc.get("chessID") or self._deep(data, "chessID")
                if taken_cid:
                    self.pool_taken[str(taken_cid)] = self.pool_taken.get(str(taken_cid), 0) + 1
                # ★关键: 从自己的买卡响应累积 me._board(备战区/手卡)
                # 游戏协议不推"你有哪些备战卡", 只能靠自己的操作记录
                if bc or (taken_cid and gid):
                    self._add_to_board(bc or {**(self._find_shop_goods(gid) or {}),
                                              "goodsID": gid, "chessID": taken_cid})
                # 金币更新(买卡花了钱)
                coin = pd.get("restCoin") or self._deep(data, "restCoin")
                if coin is not None:
                    try:
                        _old = int(self.me.get("coin") or 0)
                        _d = _old - int(coin)
                        if _d > 0:
                            self.me["_spent"] = int(self.me.get("_spent") or 0) + _d
                            self.me["_last_spend"] = _d
                    except Exception:
                        pass
                    self.me["coin"] = coin
            elif evt in ("cmsg.CRespShopRecycleChess", "CRespShopRecycleChess"):
                # 遣散(卖)响应: 棋子回到公共卡池 → pool_taken减1 + 从_board里删掉
                bc = pd.get("recycleChess") or pd.get("buyChess") or {}
                cid = bc.get("chessID") or self._deep(data, "chessID")
                gid = bc.get("goodsID") or pd.get("goodsID") or self._deep(data, "goodsID")
                if not cid and gid:
                    # 从_board里反查chessID
                    for c in (self.me.get("_board") or []):
                        if isinstance(c, dict) and str(c.get("goodsID")) == str(gid):
                            cid = c.get("chessID"); break
                if cid:
                    key = str(cid)
                    cur = self.pool_taken.get(key, 0)
                    if cur > 0:
                        self.pool_taken[key] = cur - 1
                    # 从_board里删掉这张
                    self._remove_from_board(goodsID=gid, chessID=cid)
                # 遣散加了金币
                coin = pd.get("restCoin") or self._deep(data, "restCoin")
                if coin is not None:
                    self.me["coin"] = coin
            # ======== ★锦囊获得武将/使用武将/选武将 ========
            elif evt in ("cmsg.CRespChessSelectSpellChess", "CRespChessSelectSpellChess"):
                # 用锦囊获得武将(曹轶那种) → 加进_board
                # v3.43★同时收集候选列表(selectChess / chessList / candidates / selectList 等),
                #   存进 me._spell_candidates, 后续 use_spells._spell_pick_candidate 里选星最高的确认.
                chess = pd.get("chess") or self._deep(data, "chess")
                if chess and isinstance(chess, dict):
                    self._add_to_board(chess)
                    _gid = chess.get("goodsID")
                    _cid = chess.get("chessID")
                    if _gid and _cid:
                        self.goods_map[int(_gid)] = int(_cid)
                # 收集候选: 所有 list 类型的集合字段(可能叫 chessList / selectChess /
                #   candidates / selectList / chooseChess / chooseList / 甚至就是直接 List[chess])
                cand_list = None
                for ck in ("selectChess", "SelectChess", "chessList", "ChessList",
                           "candidates", "Candidates", "selectList", "SelectList",
                           "chooseChess", "ChooseChess", "chooseList", "ChooseList",
                           "List", "list", "items", "Items"):
                    v = pd.get(ck)
                    if isinstance(v, list) and len(v) and isinstance(v[0], dict):
                        # 元素含 chessID/goodsID → 确认是候选武将列表
                        if any(k in v[0] for k in ("chessID", "goodsID", "GoodsID", "ChessID", "name")):
                            cand_list = [x for x in v if isinstance(x, dict)]
                            break
                # 兜底: 遍历整个data找list of dict里第一条有chessID的(除了已处理"chess"字段)
                if not cand_list:
                    def hunt(node, depth=0):
                        if depth > 8:
                            return None
                        if isinstance(node, list) and len(node) and isinstance(node[0], dict):
                            if any(k in node[0] for k in ("chessID", "goodsID", "GoodsID", "ChessID", "name")):
                                return [x for x in node if isinstance(x, dict)]
                        if isinstance(node, dict):
                            for vv in node.values():
                                r = hunt(vv, depth + 1)
                                if r:
                                    return r
                        if isinstance(node, list):
                            for el in node:
                                r = hunt(el, depth + 1)
                                if r:
                                    return r
                        return None
                    cand_list = hunt(data)
                if cand_list:
                    # 过滤掉已经是单个 "chess" 字段的那个结果(避免重复)
                    if chess and cand_list and cand_list[0] is chess:
                        pass   # 如果是同对象就不重
                    try:
                        self.me["_spell_candidates"] = cand_list
                        self.log(f"[协议] 锦囊给了 {len(cand_list)} 个候选武将, 等待助手选一个")
                    except Exception:
                        _SIL.note("core/protocol.py:1250", "self.me['_spell_candidates'] = cand_list")
            elif evt in ("cmsg.CRespChessUseSpell", "CRespChessUseSpell"):
                # 用锦囊 → 锦囊本身消耗(从_board/战斗区移除)
                spell_gid = pd.get("spellGoodsID") or self._deep(data, "spellGoodsID")
                if spell_gid:
                    self._remove_from_board(goodsID=spell_gid)
                    line = self.me.get("_lineUp") or []
                    if spell_gid in line:
                        self.me["_lineUp"] = [0 if x == spell_gid else x for x in line]
            elif evt in ("cmsg.CRespChessSkillSelectSpellOrChess", "CRespChessSkillSelectSpellOrChess"):
                # 升级选锦囊/选武将 → 如果选了武将(CBSETChess), 从shopGoods里找到加进_board
                if pd.get("selectTyp") == "CBSETChess":
                    gid = pd.get("goodsID")
                    if gid:
                        sg = self.shop or []
                        for g in sg:
                            if isinstance(g, dict) and str(g.get("goodsID")) == str(gid):
                                self._add_to_board(g); break
            elif evt in ("cmsg.CRespChessLineUp", "CRespChessLineUp",
                         "cmsg.CReqChessLineUp", "CReqChessLineUp"):
                # 布阵响应/请求(协议都会走): 更新 _lineUp + 从_board移除已上阵的
                pc = pd.get("positionChess") or pd.get("PositionChess")
                if isinstance(pc, list) and pc:
                    self.me["_lineUp"] = [int(x) if x not in (None, "", "0") else 0 for x in pc]
                    # ★positionChess 存的是 goodsID(短ID), 不是 chessID! 用 goodsID 对比!
                    in_line_gids = {int(x) for x in pc if x not in (None, "", "0")}
                    new_board = []
                    for c in (self.me.get("_board") or []):
                        if isinstance(c, dict) and int(c.get("goodsID") or 0) in in_line_gids:
                            continue   # 这张已经上阵了, 从备战区移除
                        new_board.append(c)
                    self.me["_board"] = new_board
            elif evt in ("cmsg.CRespChessGetLastBattlePlayerData", "CRespChessGetLastBattlePlayerData",
                         "cmsg.CNtfChessGetLastBattlePlayerData", "CNtfChessGetLastBattlePlayerData"):
                # ★关键: 对手上回合阵容(点击对手头像时服务器返回)
                # protoData.player = {hp, coin, shopLevel, generalID, nickname, userID, lastLineUp:[...]}
                player = pd.get("player") or self._deep(data, "player")
                uid = str(player.get("userID") or "") if isinstance(player, dict) else ""
                if uid and player:
                    # ★锁在_attach_board内部, 这里不再独立with self.lock(嵌套会永久死锁!)
                    self._attach_board_with_info(uid, player, evt)
            elif evt == "RESP_SELECT_GENERAL":
                self.phase = "choose"
            elif evt in ("cmsg.CNotifyChessBattleEnd", "cmsg.CNotifyChessFightEnd",
                         "CNotifyChessBattleEnd", "CNotifyChessFightEnd"):
                # 实锤: protoData为空, battleResult字段不存在.
                # 胜负判定已挪到 _take_owner(下一次hp刷新时), 这里只保持挂起标记.
                # 兼容兜底: 万一服务器某天带上battleResult仍可识别
                r_raw = pd.get("battleResult")
                if r_raw not in (None, ""):
                    r_s = str(r_raw).lower()
                    if "win" in r_s:
                        self.battles["win"] += 1
                        self._await_hp_judge = False
                        self._hp_before_battle = None
                    elif "draw" in r_s:
                        self.battles["draw"] += 1
                        self._await_hp_judge = False
                        self._hp_before_battle = None
                    elif any(x in r_s for x in ("lose", "fail")):
                        self.battles["lose"] += 1
                        self._await_hp_judge = False
                        self._hp_before_battle = None
                self.phase = "recruit"
            elif evt in ("cmsg.CNotifyUseSkill", "CNotifyUseSkill"):
                # 技能使用通知: skillID 4位(≤2000)=主公技能(1013孔融等实锤), 6位=棋子技能(940311)
                sid = pd.get("skillID")
                try:
                    sid_n = int(sid)
                except (TypeError, ValueError):
                    sid_n = None
                if sid_n is not None and 1000 <= sid_n <= 2000:
                    self.lord_skill_used_turn = sid_n   # 本回合主公技能已用(限1次型)
                    self.lord_skill_used_total += 1
            elif evt == "cmsg.CNtfChessGameOverMsg":
                # 实锤: 此协议每场战斗都发(protoData空的/GameInfo带当前排名),
                # 不是整局结束! 挂起30秒, 没有新回合才真结束 → _check_pending_game_over
                infos = pd.get("GameOverInfo") or []
                if self.in_game and not self.game_over_recorded:
                    self._pending_game_over = {"ts": time.time(), "infos": infos}

    def shop_snapshot(self):
        """当前商店快照 [{goodsID, chessID, hp, attack, skills, minionTypes...}](深拷贝防并发)"""
        import copy
        with self.lock:
            return copy.deepcopy(self.shop)

    def _dig(self, data):
        """样本形态1: [ {msg:{protoVO:{protoData:{...}}}}, false ]；形态2: {protoVO:{...}}
        v3.24: 实锤(topKeys=['0','1']且msg=none)——数组形态时不能只看[0], 遍历所有元素找事件形态;
        v3.41: 嵌套数组 [ [{goodsID:...}] ] (DELETE_CHESS) — 递归剥数组直到找到dict"""
        def _unwrap(node, depth=0):
            """递归剥数组: 找第一个含数据的dict或非空数组"""
            if depth > 4:
                return node
            if isinstance(node, list):
                # 遍历所有元素找有意义的dict
                for el in node:
                    if isinstance(el, dict) and el:
                        return el
                # 没有dict → 剥最内层
                for el in node:
                    if isinstance(el, list):
                        return _unwrap(el, depth + 1)
                # 全部是基本类型数组 → 原样返回(调用方自己处理)
                return node
            return node

        node = _unwrap(data)
        if isinstance(node, dict) and "msg" in node:
            node = node["msg"]
        if isinstance(node, dict) and "protoVO" in node:
            node = node["protoVO"].get("protoData", node)
        # ★兜底: 直接是 {'protoData': {...}} 也挖进去(油猴脚本可能简化了结构)
        if isinstance(node, dict) and "protoData" in node and len(node) <= 3:
            node = node["protoData"]
        return node if isinstance(node, dict) else {}

    def _deep(self, data, key):
        """v3.23 深搜兜底: 实锤(CRespShopRefreshChess topKeys=['0','1'])响应可能是非常规数组结构,
        _dig提取失败时按key深搜整个data, 找到第一个含该key的dict即返回其值."""
        if isinstance(data, dict):
            if key in data:
                return data[key]
            for v in data.values():
                r = self._deep(v, key)
                if r is not None:
                    return r
        elif isinstance(data, (list, tuple)):
            for v in data:
                r = self._deep(v, key)
                if r is not None:
                    return r
        return None

    def _count_played_cards(self, data):
        """实锤(4.txt): TEAM_CHESS_DELETE_HAND_CHESS: [["1388"]] 每打出武将牌发一次.
        数据是list形态, 不能走_dig(dict-only), 自己剥壳."""
        node = data
        if isinstance(node, list) and node and isinstance(node[0], dict):
            node = node[0]
        if isinstance(node, dict) and "msg" in node:
            node = node["msg"]
        if isinstance(node, dict) and "protoVO" in node:
            node = node["protoVO"].get("protoData", node)
        n = 0
        if isinstance(node, list):
            for item in node:
                if isinstance(item, (list, tuple)):
                    n += len(item)
                elif item is not None:
                    n += 1
        if n:
            self.used_cards_this_turn += n
            self.used_cards_total += n

    def _is_me(self, uid):
        """uid 是否就是本人。

        v4.24★ 修(实锤 bug): 原来两处判断写的是 `str(self.me.get("userID") or "") == uid`,
          但 me 是 _take_owner() 按【白名单】写入的, 白名单里没有 userID →
          这个比较恒为假, 于是两段代码从来没生效过:
            ① protocol.py 的 positionChess 分支 → self.me["_lineUp"] 永远不写
            ② _attach_board_locked 的开头          → self.me["_board"] 永远不写
          (离线样本喂完 500 条真实协议后 me 里既无 _lineUp 也无 _board, 就是这么来的)
          现在同时认 my_id(_take_owner 一直在维护) 和 me["userID"](已补进白名单)。
        """
        if not uid:
            return False
        uid = str(uid)
        return uid == str(self.me.get("userID") or "") or uid == str(self.my_id or "")

    def _take_owner(self, o, keep_name=False):
        if not o:
            return
        uid = str(o.get("userID") or "")
        if uid and not self.my_id:
            self.my_id = uid
        old_hp = self.me.get("hp")
        self.me.update({k: o[k] for k in
                        ("hp", "coin", "shopLevel", "generalID", "shopLevelUpCost",
                         "shopRefreshCost", "shopBuyCost", "hpLimit",
                         "userID") if k in o})   # v4.24★ userID 补进白名单(见 _is_me)
        if not keep_name and o.get("nickname"):
            self.me["nickname"] = o["nickname"]
        # ★★★ 关键修复: 解析 owner.Chess 和 owner.lineUp
        # EnterTeam 里有完整的手卡/备战区/战斗区数据, 之前完全没解析!
        chess_dict = o.get("Chess") or {}
        if chess_dict:
            # Chess 是 {goodsID: {goodsID, chessID, hp, attack, pos, source, totalHp, totalAttack}}
            # v3.43★pos判定规则同步CNtfChessUpdateInfoMsg:
            #   pos=1..7 → 战斗区; pos<1 / pos>7 / pos缺失 / 非数字 → 全部算备战区
            #   (之前硬编码 pos==0 / pos>=8 → 会漏掉 pos=-1 / pos=None 等非标准形态)
            board = []
            line_ids = []
            for k, v in chess_dict.items():
                if not isinstance(v, dict): continue
                
                # v3.96★ 强制过滤对手单位 (side=2)
                side = v.get("side") or v.get("Side")
                if side in (2, "2"):
                    continue
                    
                gid = v.get("goodsID") or int(k)
                cid = v.get("chessID")
                # ★建 goods_map
                if gid and cid:
                    self.goods_map[int(gid)] = int(cid)
                raw_pos = v.get("pos")
                try:
                    pos_n = int(raw_pos) if raw_pos is not None and raw_pos != "" else None
                except (TypeError, ValueError):
                    pos_n = None
                card = dict(v)
                card["goodsID"] = gid
                card["chessID"] = cid
                if pos_n is not None and 1 <= pos_n <= 7:
                    # 战斗区的卡 → 加入 lineUp(用goodsID数字, 后续与协议positionChess对齐)
                    line_ids.append(str(gid))
                else:
                    # ★pos<1 / pos>7 / 缺失 → 全部备战区
                    board.append(card)
            # 补全 7 格 lineUp (空位填 "0")
            # ★lineUp 顺序由协议 owner.lineUp 决定(上面的 line_ids 是遍历顺序, 不准)
            # 但如果协议有 owner.lineUp 直接用那个!
            proto_lineup = o.get("lineUp") or []
            if proto_lineup:
                self.me["_lineUp"] = [int(x) if x not in (None, "", "0") else 0 for x in proto_lineup]
            elif line_ids:
                while len(line_ids) < 7:
                    line_ids.append(0)
                self.me["_lineUp"] = line_ids[:7]
            # board = 备战区
            # v3.86★ 备战区深度合并: 实现非破坏性持久化，防止刷新包导致数据“闪现”消失
            existing_board = list(self.me.get("_board") or [])
            if board:
                # 协议推了备战区 -> 合并
                proto_keys = set()
                for b_card in board:
                    pk = str(b_card.get("goodsID") or b_card.get("spellID") or b_card.get("itemID") or ("cid@" + str(b_card.get("chessID"))))
                    proto_keys.add(pk)
                
                merged = list(board)
                now = time.time()
                # 保留那些不在协议里但最近看到的项目
                for old in existing_board:
                    if not isinstance(old, dict): continue
                    okey = str(old.get("goodsID") or old.get("spellID") or old.get("itemID") or ("cid@" + str(old.get("chessID"))))
                    if okey not in proto_keys:
                        # v3.88★ 更加激进的保留策略: 只要是最近 5 分钟内看到的都保留
                        last_seen = old.get("_last_seen") or old.get("_board_ts") or 0
                        if now - last_seen < 300:
                            merged.append(old)
                self.me["_board"] = merged
            else:
                # v3.88★ 协议完全没推备战区(空包) -> 强制保留现有 DOM 数据
                # 特别是在招募阶段刷新商店时，服务器有时不推 Chess 字段
                if existing_board:
                    now = time.time()
                    self.me["_board"] = [c for c in existing_board if now - (c.get("_last_seen") or c.get("_board_ts") or 0) < 300]
        # 没 Chess 但有 lineUp → 至少更新战斗区
        elif o.get("lineUp"):
            self.me["_lineUp"] = [int(x) if x not in (None, "", "0") else 0 for x in o["lineUp"]]
        # ---- v3.20.3 战斗胜负判定点(实锤时序: BattleEnd时hp还没掉,
        #      hp更新在下一次 DataUpdate/EnterTeam/EndRecruit 的 chessBaseInfo/chessPlayer 里) ----
        if self._await_hp_judge:
            self._await_hp_judge = False
            hp_new = self.me.get("hp")
            if self._hp_before_battle is not None and isinstance(hp_new, (int, float)):
                if hp_new < self._hp_before_battle:
                    self.battles["lose"] += 1
                elif hp_new > self._hp_before_battle:
                    self.battles["win"] += 1   # 血量不减反增(医疗等)=活下来了, 归胜
                else:
                    self.battles["win"] += 1   # 不掉血=赢(平局罕见, 暂并入胜)
            self._hp_before_battle = None

    # ---------- 整局结束(经30秒延迟确认后调用) ----------
    def _finalize_game_over(self, infos):
        if not self.in_game or self.game_over_recorded:
            return
        self.in_game = False
        self.game_over_recorded = True
        mine = None
        for it in infos:
            if str(it.get("UserID")) == self.my_id:
                mine = it
                break
        if mine is None:
            return
        # ============ v4.68★ 名次: 用淘汰顺序推, 不再拿 emRus 当名次 ============
        #   老代码: placement = int(mine.get("emRus") or 0) —— 那是【最后一场战斗的结果枚举】,
        #   不是名次(源码依据见 __init__ 与 _track_eliminations 的注释)。它导致 209 条里
        #   只有 1/2/3 三档、"胜利 99 条"其实是"最后一战赢了", 复盘整页统计全是错的。
        #   emRus 本身没浪费: 作为"末场战斗结果码"原样写进备注, 想对比还能对。
        _last_code = mine.get("emRus")
        rank, src = self._guess_rank()
        self.rank_est, self.rank_src = rank, src
        placement = int(rank or 0)
        result = ("胜利" if placement == 1 else
                  ("前四" if 1 < placement <= 4 else
                   ("失败" if placement > 4 else "未知")))
        lord = f"主公:{self._nm(self.me.get('generalID', '?'))}"
        dur = int((time.time() - self.game_start_ts) / 60) if self.game_start_ts else 0
        # v4.107★ 武将列表: 以前这里传的是空字符串, 于是"按武将统计"永远是一片空白。
        #   取最后一次布阵(self.me["_lineUp"], 存的是 goodsID) → goods_map 换 chessID → 名字;
        #   拿不到就留空(不瞎编, 界面会注明"这局没记下武将")。
        gens = []
        try:
            for gid in (self.me.get("_lineUp") or self.me.get("_board") or []):
                s = str(gid).strip()
                if not s.isdigit():          # 布阵里可能有 0/空(没放卡的格子)
                    continue
                gid_i = int(s)
                if not gid_i:
                    continue
                cid = int(self.goods_map.get(gid_i, gid_i))
                nm = self._nm(cid)
                if "(" in nm and nm.endswith(")"):        # "21002021(张星彩)" → "张星彩"
                    nm = nm.split("(", 1)[1][:-1]
                if nm and nm not in gens:
                    gens.append(nm)
        except Exception as e:
            print("[战绩] 取本局武将失败(这条战绩的武将留空): %s" % e)
        try:
            add_record("自走棋", lord, gens, result, placement, dur,
                       note=(f"战绩自动记录(协议) 战斗W{self.battles['win']}/D{self.battles['draw']}/L{self.battles['lose']}"
                             + (f" 名次依据: {src}" if src else "")
                             + (f" 末场结果码(emRus, 不是名次): {_last_code}"
                                if _last_code is not None else "")),
                       place_src=("elim" if rank else ""))
            self.auto_records.append((placement, result, time.time()))
        except Exception:
            _SIL.note("core/protocol.py:1583", "add_record('自走棋', lord, gens, result, placemen")
        # v4.110★ 对手成型阵容归档: 每局把【前 3 名用的阵容】存一份, 攒起来看"现在流行什么"
        #   + 查克制表给反制思路。数据本来就在 LIVE 里(others[i]['_board'] 是嗅探到的棋子,
        #   hp 每回合都在刷, elim_order 是淘汰顺序), 这里只是借结算这一刻顺手落盘 ——
        #   不新开采集、不额外发包, 所以放在战绩写完之后, 失败了也不影响战绩本身。
        try:
            from core import opponent_archive as OA

            def _nm_board(g):
                """别人棋盘里存的是【goodsID】, 得先过 goods_map 换成 chessID 再取名字
                   —— 和上面取自己武将是同一套换算。"""
                try:
                    gi = int(str(g).strip())
                    s = self._nm(int(self.goods_map.get(gi, gi)))
                    if "(" in s and s.endswith(")"):
                        s = s.split("(", 1)[1][:-1]
                    return s
                except Exception:
                    return str(g)

            OA.archive_game(self.others, self.my_id, elim_order=self.elim_order,
                            mode="自走棋", lord=lord, my_rank=placement, nm=_nm_board)
        except Exception as e:
            print("[战绩] 对手阵容归档失败(不影响战绩本身): %s" % e)
        # 重置战斗计数与淘汰榜，为下一局准备
        self.battles = {"win": 0, "draw": 0, "lose": 0}
        self.game_start_ts = 0.0
        self._reset_rank_track()

    # ---------- 展示 ----------
    @staticmethod
    def _nm(gid, *, lord=False, chess=False):
        """ID -> '21002021(张星彩)'；无映射时原样显示ID"""
        if gid in (None, "", "-"):
            return str(gid if gid is not None else "?")
        try:
            # v3.95★ 统一调用 IDMAP.name_of，内部已包含 1019 修正和 S10 剥离逻辑
            n = IDMAP.name_of(gid, lord_hint=lord, chess_hint=chess)
            return f"{gid}({n})" if n else str(gid)
        except Exception:
            return str(gid)

    @staticmethod
    def _lord_line(gid):
        """主公ID -> '1021(庞山民)【飞将】被动'，带技能"""
        if gid in (None, "", "-"):
            return str(gid if gid is not None else "?")
        base = GameLive._nm(gid, lord=True)
        try:
            info = IDMAP.lord_info_of(gid)
            if info and info.get("skill_id"):
                sk = IDMAP.lord_skill_of(info["skill_id"])
                if sk and sk.get("name"):
                    return f"{base}【{sk['name']}】{sk.get('desc','')[:30]}"
        except Exception:
            _SIL.note("core/protocol.py:1638", "info = IDMAP.lord_info_of(gid)")
        return base

    def summary(self):
        with self.lock:
            if not self.me and not self.others:
                return "尚未捕获对局数据（进入一局自走棋后自动显示）"
            lines = []
            me = dict(self.me)
            phase_cn = {"recruit": "招募", "battle": "战斗中",
                        "choose": "选主公"}.get(self.phase, self.phase or "?")
            lines.append(f"我方: {me.get('nickname','?')}  金币:{me.get('coin','?')}  "
                         f"血量:{me.get('hp','?')}/{me.get('hpLimit','?')}  "
                         f"商店Lv:{me.get('shopLevel','?')}(升级{me.get('shopLevelUpCost','?')}"
                         f"/刷新{me.get('shopRefreshCost','?')}/买卡{me.get('shopBuyCost','?')})  "
                         f"主公:{self._lord_line(me.get('generalID','?'))}")
            # 技能状态行: 打牌进度(飞将型) + 限1次技能已用状态
            try:
                gid = me.get("generalID")
                info = IDMAP.lord_info_of(gid) or {}
                sid_cfg = info.get("skill_id")
                used_tag = f"  本回合已用(skillID={self.lord_skill_used_turn})" if self.lord_skill_used_turn else "  本回合未用"
                n6 = self.used_cards_this_turn % 6
                lines.append(f"技能: 本回合已打武将牌{self.used_cards_this_turn}张(局累计{self.used_cards_total})"
                             f"  距6张触发:{6 - n6 if n6 else 0}张"
                             f"  主公技能局内触发{self.lord_skill_used_total}次{used_tag}"
                             f"  [skillID={sid_cfg}]")
            except Exception:
                _SIL.note("core/protocol.py:1666", "gid = me.get('generalID')")
            lines.append(f"回合:{self.turn}({phase_cn})  "
                         f"战斗 胜{self.battles['win']}平{self.battles['draw']}负{self.battles['lose']}"
                         f"  阶段:{self.phase or '-'}")

            # v3.96★ 备战区与锦囊显示 (DOM 扫描后能直接看到)
            try:
                board = self.me.get("_board") or []
                heros = [c for c in board if not self.is_spell_card(c)]
                spells = [c for c in board if self.is_spell_card(c)]
                
                if heros:
                    h_list = "  ".join(f"• {self._nm(c.get('chessID') or c.get('goodsID'), chess=True)} ({c.get('attack','?')}/{c.get('hp','?')})" for c in heros[:10])
                    lines.append(f"▶ 武将卡(共{len(heros)}张): {h_list}")
                
                if spells:
                    s_list = "  ".join(f"* {self._nm(c.get('spellID') or c.get('goodsID') or c.get('chessID'), chess=True)}" for c in spells[:10])
                    lines.append(f"▶ 锦囊/道具(共{len(spells)}张): {s_list}")
            except Exception: _SIL.note("core/protocol.py:1684", "board = self.me.get('_board') or []")

            # v3.96★ 战斗区 (7格状态)
            try:
                line = self.me.get("_lineUp") or []
                if line and any(x for x in line):
                    che_parts = []
                    for i, gid in enumerate(line):
                        if gid and int(gid) > 0:
                            # 找对应的 chessID
                            cid = self.goods_map.get(int(gid)) or gid
                            # 从 _board 或 shop 里找实时攻血
                            atk, hp = "?", "?"
                            for c in (self.me.get("_board") or []) + (self.shop or []):
                                if str(c.get("goodsID")) == str(gid):
                                    atk, hp = c.get("attack", "?"), c.get("hp", "?")
                                    break
                            che_parts.append(f"[{i+1}]{self._nm(cid, chess=True)}({atk}/{hp})")
                        else:
                            che_parts.append(f"[{i+1}](空)")
                    lines.append("▶ 战斗区(7格): " + "  ".join(che_parts))
            except Exception: _SIL.note("core/protocol.py:1705", "line = self.me.get('_lineUp') or []")

            if self.others:
                lines.append("对手(昵称/金币/血量/商店Lv/主公/阵容):")
                for o in self.others:
                    opp_uid = str(o.get("userID") or "")
                    nick = o.get("nickname", "?")
                    # ---------- 找对手阵容字段: 先查常见名字(chessList/board/lineUp/positionChess) ----------
                    # 再查全网嗅探到的 _board (来自独立协议, 如阵容/布阵/战斗数据推送)
                    board_str = ""
                    board = None
                    for k in ("_board", "chessList", "board", "lineUp", "positionChess",
                              "myChess", "chessListInfo", "chesses", "positionChessList"):
                        if isinstance(o.get(k), list) and len(o[k]):
                            board = o[k]
                            break
                    src_tag = ""
                    if board is not None and board is o.get("_board"):
                        src_tag = f"[来源:{o.get('_board_src','sniff')}]"
                    if board:
                        # 每条应该是 chessID+attack+hp，按协议形态：dict 或 str/goodsID
                        che_parts = []
                        for c in board:
                            if isinstance(c, dict):
                                cid = c.get("chessID") or c.get("chessId") or c.get("id") or "?"
                                atk = c.get("attack") or c.get("atk") or "?"
                                hp_ = c.get("hp") or c.get("HP") or "?"
                                star = c.get("star") or c.get("level") or ""
                                star_s = f"★{star}" if str(star).isdigit() and int(star) > 1 else ""
                                che_parts.append(f"{self._nm(cid, chess=True)}({atk}/{hp_}{star_s})")
                            else:
                                che_parts.append(str(c)[:16])
                        board_str = "  阵容:" + " ".join(che_parts[:8]) + src_tag
                    # ---------- 调试: 把对手里未知的list字段打出来, 找真正的阵容字段名 ----------
                    unknown_lists = []
                    exclude = {"_board", "_board_src", "_board_ts",
                               "chessList", "board", "lineUp", "positionChess",
                               "myChess", "chessListInfo", "chesses", "positionChessList",
                               "userID", "UserId", "user_id", "nickname"}
                    for k, v in (o or {}).items():
                        if k in exclude:
                            continue
                        if isinstance(v, list) and 1 <= len(v) <= 200:
                            # 看第一个元素是不是有棋子特征
                            first = v[0] if v else None
                            feat = ""
                            if isinstance(first, dict):
                                if any(ck in first for ck in ("chessID", "chessId", "attack", "hp", "goodsID")):
                                    feat = "[疑似棋子!]"
                                else:
                                    feat = f"[keys:{','.join(list(first.keys())[:6])}]"
                            elif isinstance(first, (int, float)):
                                feat = f"[nums,首={first}]"
                            elif isinstance(first, str):
                                feat = f"[str,首10={first[:10]}]"
                            unknown_lists.append(f"{k}×{len(v)}{feat}")
                    debug = ""
                    if unknown_lists:
                        debug = "  【未知字段】" + " ".join(unknown_lists[:4])
                    lines.append(f"  {nick}  金币:{o.get('coin','-')}  "
                                 f"血量:{o.get('hp','-')}  Lv:{o.get('shopLevel','-')}  "
                                 f"主公:{self._lord_line(o.get('generalID','-'))}"
                                 f"{board_str}{debug}")
            if self.shop:
                goods = "  ".join(
                    f"#{g.get('goodsID')} {self._nm(g.get('chessID'), chess=True)}({g.get('attack')}/{g.get('hp')}"
                    f" {','.join(g.get('minionTypes') or [])})" for g in self.shop[:7])
                lines.append(f"商店: {goods}")
            try:
                cs = IDMAP.counts()
                lines.append(f"[库:主公{cs.get('lords',0)} 棋子{cs.get('chess',0)}+学{cs.get('gen',0)} 锦囊{cs.get('cards',0)} 主技能{cs.get('lord_skills',0)}]")
            except Exception:
                _SIL.note("core/protocol.py:1777", "cs = IDMAP.counts()")
            return "\n".join(lines)


LIVE = GameLive()
