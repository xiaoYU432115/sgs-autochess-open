# -*- coding: utf-8 -*-
"""v4.145★ 逐局对局记录(JSONL) + 脱敏 + 复盘 —— 【持续学习闭环】的地基。

用户要的闭环:
    采集对局 → 本地校验/复盘 → 交给 AI 分析 → 审核建议 → 改策略/代码 → 验证 → 新对局反馈
本文件负责前两环(落盘 / 复盘); 后面的"交给 AI"就是把 jsonl 发出去让 AI 读。

★ 这套设计学自 codex 项目(见 codex/docs/数据采集与策略复刻总览.md), 四条原则照搬:

  ① 【五态, 不是两态】一次操作的结果必须分开记:
       not_sent  页面方法调了, 但没看到协议发出去 —— 这可能是【采集器漏了】,
                 不等于服务器拒绝(文档原话: 这是采集器假阴性)
       sent      已见请求包, 还没等到回包
       accepted  回包 errCode == 0
       rejected  回包 errCode != 0
       timeout   30 秒没等到回包
     把它们混成"成功/失败"正是我们踩过的坑 —— ReqChessLineUp 那次, 包被
     CanOperate 门禁吞了(一个字节都没发), 我们却在下面干等响应, 日志一片绿。

  ② 【配对】CReqXxx ↔ CRespXxx 按协议主体名 FIFO 配对, 不猜、不模糊匹配。

  ③ 【脱敏】落盘前按键名正则剔除身份字段 —— 因为你要把对局数据发给 AI 看,
     没这道处理迟早把昵称/账号/token 一起发出去。

  ④ 【不回填】没有的数据写 None/"unknown", 绝不用画面猜补齐; 旧日志不改写。

★ 为什么"零页面改动"就能做:
     页面脚本早就把收发协议流水推给 Python 了(bridge/sgs_bridge.user.js 的
     netBuf, 每条 {evt, ts, full}), 而 push() 每次都把它带过来。所以配对、
     判定、落盘这三件事全在 Python 端做即可 —— 不用改一行 JS。
"""
import hashlib
import json
import os
import re
import threading
import time

try:
    from core.id_map import equip_name_of as _equip_name_of
except Exception:                       # 独立运行 / 导入失败时降级成"不翻名字"
    def _equip_name_of(_eid):
        return ""


def _eqname(v):
    """装备 ID -> 中文名。已经是名字(或查不到)就原样返回 —— 绝不编。"""
    s = str(v)
    return _equip_name_of(s) or s

_HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.path.dirname(_HERE), "data", "matchlogs")

MAX_BYTES = 30 * 1024 * 1024      # 单局上限(与 codex 对齐)
TIMEOUT_SEC = 30.0                # 等到回包的最长时间
GAP_NEW_MATCH_SEC = 300.0         # 断流这么久就认为是新的一局
OP_MATCH_TTL_SEC = 10.0           # 操作意图的配对寿命(见 _take_op / _expire_ops)

# v4.149★ 开新局的信号 —— 见 feed_net 里的用法说明。
#   只认【服务器确认过】的: CResp* 是回包, CNotify* 是服务器主动通知。
#   不收 CReqLobbyChessRoomStartGame: 它只是客户端点了"开始", 实测同一局会发
#   两次(点一次 + 重试一次), 拿它切文件会把一局切成两个。
#   ⚠ 故意【不收】CNotifyChessGameResult / CRespLobbyTableLeave 这类"结束"信号:
#     它一来就切文件的话, 若这局之后你直接关了游戏, 就会留下一个只有 match_start
#     的空壳文件 —— 又变成另一种乱。用"下一局真的开始了"来切, 就不会有空壳。
_NEW_GAME_EVTS = {
    "CRespLobbyChessRoomStartGame",
    "CNotifyGameStart",
}
_NEW_GAME_DEDUPE_SEC = 30.0       # 刚开过文件这么久内的重复信号一律忽略

_REQ_RE = re.compile(r"^CReq(.+)$")
_RESP_RE = re.compile(r"^CResp(.+)$")

# 脱敏: 命中这些键名就整对丢掉(codex 的 _scrub 同思路)
_SENSITIVE_RE = re.compile(
    r"password|passwd|cookie|token|session|nickname|nick|user.?id|userid|"
    r"account|acct|uid|openid|roleid|rolename|playername", re.I)

# 回包只留这些业务字段(别的可能是身份信息或超大数组, 一律不落盘)
_KEEP_KEYS = {
    "errCode", "err_code", "restCoin", "coin", "goodsID", "goodsId",
    "spellGoodsID", "spellID", "equipmentID", "chessGoodsId", "chessId",
    "positionChess", "isPreselection", "tableID", "hp", "round",
    # v4.151★ 装备三选一的候选列表(CNotifyEquipmentChooseList.equipments)。
    #   以前没在白名单里 → 那条包落盘后 payload 恒为 {}、keys 恒为 [],
    #   表现就是"包抓到了, 但三个候选一个都看不见"。
    "equipments",
}


def _scrub(obj, depth=0):
    """落盘前的脱敏 + 限深限长(codex server.py:35-53 同思路)。

    递归限深 10、每项限 500、字符串截断 2000 —— 顺带防止某条协议里塞了个
    巨大的数组把 jsonl 撑爆。
    """
    if depth > 10:
        return "<deep>"
    if isinstance(obj, dict):
        out = {}
        n = 0
        for k, v in obj.items():
            if n >= 500:
                break
            if _SENSITIVE_RE.search(str(k)):
                continue                      # 身份类字段: 整对丢掉
            out[str(k)[:100]] = _scrub(v, depth + 1)
            n += 1
        return out
    if isinstance(obj, (list, tuple)):
        return [_scrub(x, depth + 1) for x in list(obj)[:500]]
    if isinstance(obj, str):
        return obj[:2000]
    if isinstance(obj, (int, float, bool)) or obj is None:
        return obj
    return str(obj)[:200]


def _pick(d):
    """从协议内容里只挑业务字段(其余一律丢) —— 落盘体积小、也不会误存隐私。"""
    if not isinstance(d, dict):
        return {}
    out = {}
    for k, v in d.items():
        if k in _KEEP_KEYS:
            # v4.151★ 数组/字典走 _scrub(限深限长), 【不能】str(v)[:200]。
            #   以前非标量一律 str() 截断, 于是 equipments(三选一候选列表)这类字段
            #   落盘后变成被砍断的 Python repr 字符串 —— 白名单里加了也没用,
            #   表现正是"包抓到了, 三个候选一个都看不全"。
            if isinstance(v, (int, float, str, bool)) or v is None:
                out[k] = v
            else:
                out[k] = _scrub(v)
    return out


class MatchLog(object):
    """一局一个 jsonl 文件, append-only。"""

    def __init__(self, data_dir=None):
        self.dir = data_dir or DATA_DIR
        self.lock = threading.Lock()
        self.match_id = None
        self.path = None
        self.fp = None
        self.bytes = 0
        self._foe_tag = {}         # v4.148 对手稳定编号: uid -> 序号
        self.pending = {}          # body -> [(ts, evt, seq)] 等待回包的请求
        self.opq = {}              # 操作意图: "ReqXxx" -> [{aid, src, ts}] 等协议包来配对
        self.seq = 0
        self.last_ts = 0.0
        self.counts = {}
        self.enabled = True

    # ---------- 局的生命周期 ----------
    def _open(self, match_id=None):
        self._close()
        ts = time.time()
        mid = match_id or ("m-%d-%s" % (int(ts), os.getpid()))
        mid = re.sub(r"[^A-Za-z0-9_.\-]", "_", mid)      # 防路径穿越
        try:
            os.makedirs(self.dir, exist_ok=True)
        except Exception:
            pass
        self.match_id = mid
        self.path = os.path.join(self.dir, mid + ".jsonl")
        # v4.149★ 文件名撞车: mid 用的是【秒级】时间戳, 同一秒内开两次就会算出同一个
        #   名字 —— 于是 _open() 变成"往老文件里继续追加", 新局照样混进旧局(自检
        #   _verify_newmatch.py 用例2 就是这个, 没加这段时第二个文件根本没被创建)。
        #   真机里连开两局也可能撞上, 所以撞了就加后缀躲开。
        _n = 1
        while os.path.exists(self.path):
            _n += 1
            self.path = os.path.join(self.dir, "%s_%d.jsonl" % (mid, _n))
        self.fp = open(self.path, "a", encoding="utf-8")
        self.bytes = 0
        self.pending = {}
        self.opq = {}
        self.seq = 0
        self.counts = {}
        self._opened_at = ts          # v4.149 开新局信号的去重基准(见 _NEW_GAME_EVTS)
        self._foe_fp = None      # v4.147 对手阵容指纹: 换局必须清掉, 否则新局第一次会被判"没变"
        self._foe_tag = {}       # v4.148 对手【稳定编号】: uid -> 首次出现顺序(换局清空)
        self._write("match_start", {"ts": ts, "match_id": mid})
        return mid

    def _close(self):
        try:
            if self.fp:
                self._write("match_end", {"ts": time.time(), "counts": dict(self.counts)})
                self.fp.close()
        except Exception:
            pass
        self.fp = None

    def new_match(self, match_id=None):
        with self.lock:
            return self._open(match_id)

    # ---------- 写入 ----------
    def _write(self, etype, data):
        if not self.fp:
            return
        try:
            line = json.dumps(
                {"schema": 1, "match_id": self.match_id, "ts": time.time(),
                 "type": str(etype)[:40], "data": _scrub(data)},
                ensure_ascii=False)
            self.fp.write(line + "\n")
            self.bytes += len(line)
        except Exception:
            return
        self.counts[etype] = self.counts.get(etype, 0) + 1
        if self.bytes > MAX_BYTES:
            self._close()          # 到上限就停, 不静默截断

    def append(self, etype, data):
        if not self.enabled:
            return
        with self.lock:
            if not self.fp:
                self._open()
            self._write(etype, data)

    # ---------- 对手阵容层(v4.147) ----------
    def feed_foes(self, snap):
        """v4.147★ 对手阵容快照 —— 让对局日志终于能回答"对面是什么"。

        snap = push 里 foes 字段: {myUid, list:[{uid, n, cards:[名字…]}]}

        ⚠ 这是【快照】不是【事件】: 页面每次 push(几百毫秒一次)都会带一份全场名单。
          直接全量落盘会把 jsonl 撑爆, 所以先算指纹, 只有阵容真的变了才写一条 ——
          于是"对面什么时候换的阵容"是有据可查的, 又不会把日志淹掉。
        ⚠ 数据没到位时 snap 是空的(None / list 为空) —— 空的就什么都不写。
          没有就是没有, 绝不编一个阵容出来(与"存活家数"那条的口径一致)。
        """
        if not self.enabled or not snap:
            return 0
        if not isinstance(snap, dict):
            return 0
        my_uid = str(snap.get("myUid") or "")
        rows = []
        for r in (snap.get("list") or []):
            if not isinstance(r, dict):
                continue
            uid = str(r.get("uid") or "")
            if my_uid and uid == my_uid:
                continue                       # 自己那家不记(自己的阵容另有记录)
            cards = [str(x) for x in (r.get("cards") or []) if x not in (None, "")]
            if not cards:
                continue
            # ⚠ 别把 uid 写进去: _scrub 的脱敏正则命中 uid(它是身份字段, 整对丢掉),
            #   写了也是被丢。改用【第几家】的序号 —— 既保住"这一堆牌是同一家的",
            #   又不把别人的 ID 存进日志(与脱敏口径一致: 日志是要发给 AI 看的)。
            #
            # ★v4.148 这个序号必须是【稳定编号】, 不能是"本份快照里的第几个"。
            #   实测: __sgsChessByUser(对手数据的来源)每场战斗会重置成【当前正在打的
            #   那一两家】, 于是 n_foes 会从开局的 8 掉到 1, 位置序号整体前移 ——
            #   "对手1"每个回合都在换人, 复盘根本没法追踪某一家。现在按 uid 首次出现
            #   的顺序发号(只在内存里映射, 不落盘), 同一家整局都是同一个号。
            tag = self._foe_tag.get(uid)
            if not tag:
                tag = len(self._foe_tag) + 1
                self._foe_tag[uid] = tag
            rows.append({"i": tag, "n": r.get("n") or len(cards),
                         "cards": cards})
        if not rows:
            return 0
        try:
            fp = hashlib.md5(json.dumps(rows, sort_keys=True,
                                        ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
        except Exception:
            return 0
        with self.lock:
            if not self.fp:
                self._open()
            if fp == getattr(self, "_foe_fp", None):
                return 0                       # 阵容没变, 不重复记
            self._foe_fp = fp
            self._write("foe_lineup", {"n_foes": len(rows), "foes": rows})
        return 1

    # ---------- 我方阵容层(v4.148) ----------
    def feed_me(self, snap):
        """v4.148★ 我方快照 —— 对手那份有 feed_foes, 自己这边一直没记, 于是复盘只能看到
        "调了 N 次上阵 / N 次用锦囊", 答不了"到底上了谁、手里还剩什么锦囊、带的什么装备"。

        snap = push 里 me 字段: {lineup:[名字…], hand:[名字…], eq_cand:[…], eq_own:[…]}

        与 feed_foes 同一套口径: 指纹去重(变了才写) + 空的什么都不写(绝不编)。
        """
        if not self.enabled or not snap:
            return 0
        if not isinstance(snap, dict):
            return 0
        lineup = [str(x) for x in (snap.get("lineup") or []) if x not in (None, "")]
        hand = [str(x) for x in (snap.get("hand") or []) if x not in (None, "")]
        if not lineup and not hand:
            return 0                               # 数据没到位就是没有, 不编
        row = {"lineup": lineup, "hand": hand}
        # 装备: bridge 报上来的是 equipmentID(配置表固定号), 这里离线翻成中文名
        eq_cand = [_eqname(x) for x in (snap.get("eq_cand") or []) if x not in (None, "")]
        eq_own = [_eqname(x) for x in (snap.get("eq_own") or []) if x not in (None, "")]
        if eq_cand:
            row["eq_cand"] = eq_cand               # 当前待选(刚弹出来的那一批)
        if eq_own:
            row["eq_own"] = eq_own                 # 身上已装的
        # v4.151★ 锦囊/武将三选一的候选 —— 装备那类(eq_cand)早就落了, 这一类一直没落,
        #   于是战报里只有"挑了南蛮入侵", 看不出"当时弹出的是哪三个"。bridge 已经
        #   就地摊平成 {kind,id,name}, 这里原样收下; 窗口关了之后那一批靠
        #   pick_cand_last 补上(否则快照晚一步就永远抓不到)。
        def _flat(lst):
            out = []
            for x in (lst or []):
                if not isinstance(x, dict):
                    continue
                out.append({"kind": str(x.get("kind") or ""),
                            "id": x.get("id"),
                            "name": str(x.get("name") or "")})
            return out

        pc = _flat(snap.get("pick_cand"))
        if pc:
            row["pick_cand"] = pc                  # 此刻正开着的三选一
        else:
            pcl = _flat((snap.get("pick_cand_last") or {}).get("list"))
            if pcl:
                row["pick_cand_last"] = pcl        # 最近一次开过的(窗口已关)
        try:
            fp = hashlib.md5(json.dumps(row, sort_keys=True,
                                        ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
        except Exception:
            return 0
        with self.lock:
            if not self.fp:
                self._open()
            if fp == getattr(self, "_me_fp", None):
                return 0                           # 没变, 不重复记
            self._me_fp = fp
            self._write("my_state", row)
        return 1

    # ---------- 操作意图层(我们自己脚本这一层, codex 没有对等的) ----------
    def feed_ops(self, rows):
        """rows = push 里 ops 字段的 [{aid, op, src, ts, args}, ...]

        op = 游戏 manager 上的方法名(如 ReqShopBuyChess), src = 'script'|'page'。
        记下来之后, 下面的 feed_net 遇到同名协议包(CReqShopBuyChess)就会把它俩
        配成一条 —— 于是"我想干什么 / 包发出去了吗 / 服务器收没收"三层串成一条链。

        v4.148★ args = 这次调用的【参数】(bridge 的 __sgsOpArgs 取的, 已截到 300 字)。
          没有它, 复盘只能报"用了 66 次锦囊", 答不了【用的哪张 / 作用到谁 / 随征给谁】。
        """
        if not self.enabled or not rows:
            return
        now = time.time()
        with self.lock:
            if not self.fp:
                self._open()
            for r in rows:
                if not isinstance(r, dict):
                    continue
                op = str(r.get("op") or "")
                if not op:
                    continue
                args = r.get("args")
                item = {"aid": r.get("aid"), "src": r.get("src") or "unknown",
                        "ts": now}
                if args is not None:
                    item["args"] = args
                self.opq.setdefault(op, []).append(item)
                rec = {"op_id": item["aid"], "op": op, "source": item["src"]}
                if args is not None:
                    rec["args"] = args
                self._write("op", rec)

    # ---------- 核心: 吃页面推来的协议流水 ----------
    def feed_net(self, rows):
        """rows = push 里 net 字段的 [{evt, ts, full|brief}, ...]

        只做三件事: 认出请求/响应 → FIFO 配对 → 按 errCode 判五态。
        页面脚本不用改: netBuf 早就带过来了。
        """
        if not self.enabled or not rows:
            return
        now = time.time()
        with self.lock:
            if not self.fp:
                self._open()
            elif self.last_ts and (now - self.last_ts) > GAP_NEW_MATCH_SEC:
                self._open()      # 断流太久 = 新的一局
            self.last_ts = now

            for r in rows:
                if not isinstance(r, dict):
                    continue
                evt = str(r.get("evt") or "")
                # ★ 前缀归一化 —— 这一行修的就是"jsonl 里永远只有 match_start"的毛病。
                #   页面脚本推上来的事件名是【带命名空间】的: cmsg.CRespShopBuyChess /
                #   smsg.CNotifyChessDataUpdate, 而下面的配对(_REQ_RE/_RESP_RE)和归档
                #   都按【裸协议名】来做(CRespShopBuyChess)。没剥前缀时两个正则一条都
                #   匹配不上 —— 于是文件是建出来了(rows 非空), 写下开局那行之后就再没有
                #   第二条。实机三份 jsonl 全是 167 字节的空壳, 根因就在这一处。
                #   ⚠ dev/_match_review.py 的自检喂的是手工拼的裸名, 所以它一直是绿的 ——
                #     自检跟真机格式不一致, 这种绿没有说服力。已补带前缀的回归用例。
                if "." in evt:
                    evt = evt.rsplit(".", 1)[-1]
                # v4.149★ 开新局 → 立刻切一个新 jsonl。
                #   以前只有"断流 5 分钟"这一条判定, 打完马上开下一局时两局共用一个文件
                #   —— 实测 m-1791775833 一个文件里塞了 3 局(CNotifyChessGameResult 3 条),
                #   于是"买了丁奉之后是继续刷还是上阵"这种问题根本没法按局问。
                #   去重很关键: 同一局 CResp 和 CNotify 可能各来一次, 30 秒内的重复不理,
                #   否则一局会被切成好几个文件。
                if evt in _NEW_GAME_EVTS:
                    if (now - getattr(self, "_opened_at", 0.0)) > _NEW_GAME_DEDUPE_SEC:
                        self._open()
                        self._write("game_start",
                                    {"ts": time.time(), "signal": evt})
                        now = time.time()
                        self.last_ts = now
                    continue
                ts = float(r.get("ts") or now) / 1000.0 if (r.get("ts") or 0) > 1e12 else (r.get("ts") or now)
                full = r.get("full") if isinstance(r.get("full"), dict) else None

                mreq = _REQ_RE.match(evt)
                mresp = _RESP_RE.match(evt)
                if mreq:
                    self.seq += 1
                    aid = "%s-%d" % (self.match_id, self.seq)
                    self.pending.setdefault(mreq.group(1), []).append(
                        {"aid": aid, "ts": ts, "evt": evt})
                    # 与"操作意图"配对: op 记的是 ReqXxx, 这里的 evt 是 CReqXxx,
                    #   去掉开头的 C 就是同一个名字 —— 于是知道这包是【谁发起】的。
                    #
                    # ★v4.146 队头阻塞修复: 原来这里直接 pop(0), 而 opq 既没有过期也没有
                    #   上限 —— 只要队头那条永远等不来自己的包(出方向采集中断 / 页面方法
                    #   静默 return), 它就会一直堵着, 之后【每一个】真实发出的包都被误认到
                    #   这个几十分钟前的老意图名下, 真正的发起者反倒永远配不上。
                    #   实机后果见 data/matchlogs/m-1791514957: 该局前 35 分钟出方向一条都没
                    #   采到(那段 1371 条回包全是 orphan), 存量 op 压在队头, 后面所有 action
                    #   的 op_id 全部错配 —— 用它算"出网率"得到的是纯噪声, 还会反过来误导
                    #   "是哪个来源发起的操作"这类判断。现在先按 TTL 判死老意图再取队头。
                    _op = self._take_op(self.opq.get(evt[1:]) or [], now, evt[1:])
                    self._write("action", {
                        "action_id": aid, "proto": evt, "request": _pick(full or {}),
                        "state": "sent", "turn": (full or {}).get("round"),
                        "op_id": (_op or {}).get("aid"),
                        "source": (_op or {}).get("src", "unknown"),
                    })
                elif mresp:
                    body = mresp.group(1)
                    q = self.pending.get(body) or []
                    p = q.pop(0) if q else None
                    err = None
                    if isinstance(full, dict):
                        err = full.get("errCode", full.get("err_code"))
                    if p is None:
                        # 有回包但没找到对应的请求: 可能是我们漏采了请求包。
                        # ★ 以前这里只记 err_code, payload 直接丢了 —— 但请求那一半采不
                        #   到的时候(比如页面脚本还没更新到带出方向流的版本), 响应就是
                        #   【唯一】能看到的证据: 那一局用了哪些锦囊、买了哪张卡, 全在里面。
                        #   丢掉等于整局的协议内容白白蒸发, 只剩一堆协议名。现在带上 payload。
                        self._write("server_response", {
                            "action_id": None, "proto": evt, "orphan": True,
                            "err_code": err, "state": "unknown",
                            "response": _pick(full or {})})
                        continue
                    try:
                        err_i = int(err) if err is not None else 0
                    except Exception:
                        err_i = 0 if err in (None, "", "0") else -1
                    st = "accepted" if err_i == 0 else "rejected"
                    self._write("server_response", {
                        "action_id": p["aid"], "proto": p["evt"],
                        "err_code": err if err is not None else 0,
                        "state": st, "latency_ms": int((ts - p["ts"]) * 1000)
                        if ts >= p["ts"] else 0,
                        "response": _pick(full or {})})
                else:
                    # 通知类(战斗结算/排名等): 单独记, 不参与配对
                    if evt.startswith("CNotify"):
                        # ★ 多记一个 keys: 这个包【顶层有哪些字段】。为什么要它 ——
                        #   _pick 是白名单过滤, 大部分字段会被筛掉; 实机上出现过整批改盒
                        #   payload 全是 {} 的情况(外界根本看不出"包里到底有什么")。
                        #   留下键名清单, 事后至少能知道该去哪个字段里翻证据。
                        self._write("notify", {"proto": evt, "payload": _pick(full or {}),
                                               "keys": sorted(full.keys())[:40]
                                               if isinstance(full, dict) else []})

            self._expire(now)
            self._expire_ops(now)
            try:
                self.fp.flush()      # 一批写一次, 别每条都刷盘(战斗中每秒几十条)
            except Exception:
                pass

    def _take_op(self, q, now, name):
        """取一个【还没过期】的操作意图; 过期的先判死为 not_sent。

        opq 原先是"纯 FIFO + 无过期 + 无上限"。这带来两个必须分开看的后果:
          ① 队头阻塞 —— 队头永远等不到包时, 后面所有包都会错位认到它名下;
          ② 第五态缺失 —— 文档 ① 里写的 not_sent(页面方法调了但没看到包发出去)
             全项目一处都没有落地, 于是"调了却没发"这种情况在日志里根本不出现,
             看上去和从没调用过一模一样。
        TTL 一并把这两条都补上: 过期即落一条 not_sent, 不可能再堵队头。

        ⚠ not_sent ≠ 服务器拒绝。它可能只是【采集器漏了】(出方向采集中断时满屏都是
        not_sent, 而操作其实正常)。要区分, 只能靠"目标状态是否真的变了"。
        """
        while q and (now - q[0]["ts"]) > OP_MATCH_TTL_SEC:
            old = q.pop(0)
            self._write("op_result", {
                "op_id": old.get("aid"), "op": name,
                "source": old.get("src", "unknown"),
                "state": "not_sent",
                "waited_ms": int((now - old["ts"]) * 1000),
                "reason": "intent_expired"})
        return q.pop(0) if q else None

    def _expire_ops(self, now):
        """把超期仍未配上包的操作意图判死 —— 无条件跑, 不依赖有没有请求包。

        为什么必须独立地扫一遍: 出方向采集整个断掉的时候, feed_net 里压根不会有
        mreq 分支走到 _take_op, 指望在配对处顺手清理是清不到的; 而"整局都没采到
        出方向包"恰恰是最需要暴露出来的情况(="采集中断"与"操作全被吞"表现一样)。
        """
        for name, q in list(self.opq.items()):
            while q and (now - q[0]["ts"]) > OP_MATCH_TTL_SEC:
                old = q.pop(0)
                self._write("op_result", {
                    "op_id": old.get("aid"), "op": name,
                    "source": old.get("src", "unknown"),
                    "state": "not_sent",
                    "waited_ms": int((now - old["ts"]) * 1000),
                    "reason": "intent_expired"})
            if not q:
                self.opq.pop(name, None)

    def _expire(self, now):
        """超过 TIMEOUT_SEC 还没等到回包的请求 → 记 timeout。"""
        for body, q in list(self.pending.items()):
            keep = []
            for p in q:
                if now - p["ts"] > TIMEOUT_SEC:
                    self._write("server_response", {
                        "action_id": p["aid"], "proto": p["evt"],
                        "state": "timeout", "err_code": None,
                        "latency_ms": int(TIMEOUT_SEC * 1000)})
                else:
                    keep.append(p)
            if keep:
                self.pending[body] = keep
            else:
                self.pending.pop(body, None)


_LOG = None
_LOG_LOCK = threading.Lock()


def get():
    global _LOG
    with _LOG_LOCK:
        if _LOG is None:
            _LOG = MatchLog()
        return _LOG


def _diag(note, extra=None):
    """写一行诊断到 data/_matchlog_diag.txt（每次调用覆盖同一行）。

    为什么要它：我们能看到的唯一现象是"jsonl 里只有 match_start 一条"，而这条路中间
    隔着 bridge.py 的 try/except:pass —— 页面没上报、上报了但字段是空的、我们处理时
    抛了异常，这三种情况的表现【完全一样】，只能靠猜。有了这行记录，打完一局看一眼
    就能直接定位卡在哪一环。
    """
    try:
        import json as _json
        import os as _os
        p = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                          "data", "_matchlog_diag.txt")
        rec = {"ts": time.time(), "note": note}
        if extra:
            rec.update(extra)
        with open(p, "w", encoding="utf-8") as f:
            f.write(_json.dumps(rec, ensure_ascii=False))
    except Exception:
        pass


def feed_push(data):
    """给 bridge.py 的 /push 用: 把这一次推上来的协议流水喂进来。"""
    lg = get()
    d = data or {}
    rows = d.get("net") or []
    ops = d.get("ops") or []
    _diag("push", {"keys": sorted(d.keys())[:40], "n_net": len(rows),
                   "n_ops": len(ops), "enabled": bool(lg.enabled),
                   "bytes": lg.bytes,
                   # ★ 页面端埋点状态: 没这个字段 = 页面脚本还是旧的(没更新/没刷新页面);
                   #   out=false = 埋点装了但一次都没抓到发包(GameScene 还没进/被别处接管)。
                   "hook": d.get("hook")})
    try:
        if ops:
            lg.feed_ops(ops)      # ★ 先吃操作意图, 好让紧接着的协议包能配上对
    except Exception as e:
        _diag("feed_ops 异常", {"err": str(e), "n_ops": len(ops)})
    try:
        if rows:
            lg.feed_net(rows)
    except Exception as e:
        _diag("feed_net 异常", {"err": str(e), "n_net": len(rows),
                               "首条": str(rows[0])[:200] if rows else ""})
    try:
        if d.get("foes"):
            lg.feed_foes(d.get("foes"))
    except Exception as e:
        _diag("feed_foes 异常", {"err": str(e)})
    try:
        if d.get("me"):
            lg.feed_me(d.get("me"))
    except Exception as e:
        _diag("feed_me 异常", {"err": str(e)})


# ---------------- 复盘 ----------------
def summarize(path):
    """读一局 jsonl, 出统计 —— 对标 codex 的 stats.py(它统计的那些项)。"""
    st = {"events": 0, "actions": 0, "by_state": {}, "by_proto": {},
          "timeouts": 0, "orphans": 0, "notifies": {}, "not_sent": 0,
          "foe_snaps": 0, "foe_cards": {},
          "my_snaps": 0, "my_last": None}
    timeline = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                st["events"] += 1
                t = ev.get("type")
                d = ev.get("data") or {}
                if t == "action":
                    st["actions"] += 1
                    st["by_proto"][d.get("proto") or "?"] = \
                        st["by_proto"].get(d.get("proto") or "?", 0) + 1
                    timeline.append({"t": ev.get("ts"), "id": d.get("action_id"),
                                     "proto": d.get("proto"), "state": d.get("state"),
                                     "source": d.get("source"),
                                     "op_id": d.get("op_id")})
                elif t == "server_response":
                    s = d.get("state") or "unknown"
                    st["by_state"][s] = st["by_state"].get(s, 0) + 1
                    if s == "timeout":
                        st["timemouts"] = st.get("timemouts", 0) + 1
                    if d.get("orphan"):
                        st["orphans"] += 1
                    for it in timeline:
                        if it.get("id") and it["id"] == d.get("action_id"):
                            it["state"] = s
                            it["err_code"] = d.get("err_code")
                            it["latency_ms"] = d.get("latency_ms")
                elif t == "notify":
                    p = d.get("proto") or "?"
                    st["notifies"][p] = st["notifies"].get(p, 0) + 1
                elif t == "op_result":
                    # v4.146 第五态: 页面方法调了但没看到包发出去。
                    # 大量出现通常意味着【出方向采集中断】, 不是操作失败。
                    if d.get("state") == "not_sent":
                        st["not_sent"] += 1
                elif t == "foe_lineup":
                    # v4.147 对手阵容快照(只在阵容真的变了时记一次)
                    st["foe_snaps"] += 1
                    for f in (d.get("foes") or []):
                        for cname in (f.get("cards") or []):
                            st["foe_cards"][str(cname)] = \
                                st["foe_cards"].get(str(cname), 0) + 1
                elif t == "my_state":
                    # v4.148 我方快照(阵容/手牌/装备, 变了才记)
                    st["my_snaps"] += 1
                    st["my_last"] = d
    except Exception as e:
        st["error"] = str(e)
    st["timeline"] = timeline
    return st


def list_matches(data_dir=None):
    d = data_dir or DATA_DIR
    try:
        fs = [os.path.join(d, x) for x in os.listdir(d) if x.endswith(".jsonl")]
    except Exception:
        return []
    fs.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return fs
