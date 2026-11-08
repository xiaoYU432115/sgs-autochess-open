# -*- coding: utf-8 -*-
"""本机桥接服务器：接收油猴脚本推送的游戏状态，下发操作指令。
仅监听 127.0.0.1，标准库实现，无额外依赖。
接口:
  POST /push        游戏状态(texts/clicks)上报
  GET  /cmd?after=n 拉取待执行指令
  GET  /status       连接状态
"""
import json
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from core import silence as _SIL

# 纯UI/动画/心跳噪音协议(无信息量, 一局几百条污染对局记录和日志)
NOISE_EVTS = {
    "groupProgress", "groupComplete", "label",
    "netframe.Client_RespHeartBeat", "cmsg.CRespSyncSystemTime",
    "onComplete",
    "UI_UPDATE_HAND_CARD_NUM", "UI_UPDATE_HAND_CARD", "UI_UPDATE_CARD_DESC",
    "HAND_CARD_MOUSE_DOWN", "SHOP_CARD_MOUSE_DOWN",
    "UI_UPDATE_BATTLE_SPELL_ACT", "UI_UPDATE_BATTLE_SPELL_ACT_COMPLETE",
    "UI_UPDATE_SEND_EMOJI_LIST",
}


class BridgeState:
    def __init__(self):
        import time as _t
        self.lock = threading.Lock()
        self.latest = None          # 最近一次游戏状态(dict)
        self.cmd_queue = queue.Queue()
        self.cmd_id = 0
        self.cmd_results = {}       # 脚本执行完回传的结果 {id(str): text}
        # v4.00★ 引入 SessionID，确保 Python 重启后脚本能立即同步水位
        self.session_id = int(_t.time())
        
        # v3.45★ 历史回放水桶(按 cmd_id 索引), 供 /cmd?after=n 过滤用.
        # 防止场景: 上一个 pollCmd 请求因为断连/关闭/cancel 导致 drain_cmds 把命令弹出去了,
        # 但下一次请求又拿不到 导致命令永久丢失 (用户感觉"点按钮没反应/助手连不上").
        self._cmd_history = []      # 按 id 升序, 长度上限 CMD_HIST_CAP
        self._cmd_history_cap = 1000
        self.click_sink = queue.Queue()   # 真实点击流(始终上报)
        self.net_sink = queue.Queue()     # 协议反馈流(实时)
        self.connected_ts = 0
        # v4.22★ 油猴脚本自报的版本号 —— 取自每次 push 的 v 字段
        #   (见 bridge/sgs_bridge.user.js 的 const SCRIPT_VER)。供
        #   remote_ops.BridgeOps.get_version() 读取, 无需为拿版本号反复发 eval 命令。
        self.script_ver = None
        # v4.24★ 协议数据落盘缓冲(见 _save_net 的说明): 原来每次 push 都要把整个文件
        #   (net_structures.json 实测 1.9MB)读进来、改、再全量重写一遍。
        self._net_cache = {}        # fname -> 内存中的完整列表(磁盘内容的镜像 + 未落盘的新数据)
        self._net_dirty = set()     # 有改动但还没写盘的 fname
        self._net_last_flush = {}   # fname -> 上次写盘时间
        self._net_lock = threading.Lock()   # 独立于 self.lock: 保证永不在持锁时反向获取
        self._net_errors = 0        # 落盘失败次数(原来被 `except: pass` 完全吞掉)
        self._net_last_error = ""
        self._net_flusher = None
        # v4.184★ 回放: 录制器 / 回放控制器(默认 None = 不录制不回放)
        #   这两个字段由 core/replay.py 在 start_recording / start_playback 时挂上,
        #   bridge 自身不 import replay(避免循环依赖), 只在这里检查并回调。
        self._replay_recorder = None
        self._replay_playback = None

    def get_script_version(self):
        """v4.22★ 油猴脚本自报的版本号(最近一次 push 的 v 字段)。

        返回 str; 从未收到过任何推送时返回 None。本方法不发起网络请求。
        """
        with self.lock:
            return self.script_ver

    def get_connection_info(self):
        """v4.00★ 获取详细连接状态信息，用于精准判定“假绿”与“断连”"""
        import time as _t, os as _os
        with self.lock:
            st = self.latest
            if not st:
                return {"connected": False, "reason": "waiting_first_push", "msg": "等待浏览器数据..."}
            
            recv_ts = st.get("_recv_ts") or 0
            now = _t.time()
            # 兼容毫秒与秒 (JS传的是毫秒，Python收时可能转了)
            if recv_ts > 1e11: recv_ts /= 1000.0
            
            ago = now - recv_ts
            mypid = _os.getpid()
            recv_pid = st.get("recv_pid")
            
            # 1. PID 冲突判定 (假绿核心原因: 旧进程残留占用了推送)
            if recv_pid is not None and recv_pid != mypid:
                return {
                    "connected": False,
                    "reason": "pid_mismatch",
                    "ago": ago,
                    "recv_pid": recv_pid,
                    "mypid": mypid,
                    "msg": f"⚠假绿! 收到的是老进程(PID:{recv_pid})的数据，请清理进程"
                }
            
            # 2. 超时判定 (6秒无数据视为不稳定，10秒视为断连)
            if ago > 10.0:
                return {
                    "connected": False,
                    "reason": "timeout",
                    "ago": ago,
                    "msg": f"⚠断连! 最后推送在 {ago:.0f}秒前"
                }
            elif ago > 5.0: # Add unstable state
                return {
                    "connected": False,
                    "reason": "unstable",
                    "ago": ago,
                    "msg": f"连接不稳定! 最后推送在 {ago:.0f}秒前"
                }
            
            return {
                "connected": True,
                "reason": "active",
                "ago": ago,
                "msg": "● 已连接"
            }

    def push(self, data):
        with self.lock:
            self.latest = data
            self.connected_ts = data.get("ts", 0)
            # v4.22★ 记录脚本版本: 油猴每次 push 都带 v=SCRIPT_VER (见 sgs_bridge.user.js)
            _v = data.get("v")
            if _v:
                self.script_ver = str(_v)
            for c in data.get("clicks", []):
                self.click_sink.put(c)
            for k, v in (data.get("cmdResults") or {}).items():
                self.cmd_results[k] = v
            if not hasattr(self, "_msgs"):
                self._msgs = []
            for m in data.get("msgs", []):
                self._msgs.append(m)
            if len(self._msgs) > 200:
                del self._msgs[:-200]
            net = data.get("net") or []
            if net:
                # ---- 噪音过滤: 纯UI/动画/心跳事件无信息量, 一局几百条污染日志(用户实锤抱怨) ----
                net = [m for m in net if str(m.get("evt", "")) not in NOISE_EVTS]
            if net:
                # v4.184★ 回放模式不二次落盘(录制文件已经是数据源)
                if self._replay_playback is None:
                    self._save_net(net)
                for m in net:
                    self.net_sink.put(m)
            samples = data.get("samples") or []
            if samples:
                if self._replay_playback is None:
                    self._save_net(samples, "net_structures.json")
            hudbtn = data.get("hudBtn")
            if hudbtn:
                # 游戏内小抄悬浮窗按钮(跳过/买全/卖闲/上阵/准备) → Python执行注入动作
                if not hasattr(self, "hud_btn_sink"):
                    self.hud_btn_sink = queue.Queue()
                self.hud_btn_sink.put(str(hudbtn))
            # v4.13★ 锦囊使用日志 (从油猟端 window.__sgsBridge.push 推送过来)
            spell_use = data.get("spell_use")
            if spell_use:
                if not hasattr(self, "spell_use_sink"):
                    self.spell_use_sink = queue.Queue()
                self.spell_use_sink.put(spell_use)
            # v4.54★ 快捷键事件上报(游戏页 Tab 切三连等, 见 sgs_bridge reportHotkey):
            #   只在事件真的发生时才带这个字段, 不做轮询; 消费方是『快捷键』页自己的日志区,
            #   不进主日志(用户要求"这个日志不需要一直发送")。
            hklog = data.get("hkLog")
            if hklog:
                if not hasattr(self, "hk_log_sink"):
                    self.hk_log_sink = queue.Queue()
                self.hk_log_sink.put(hklog)
            diag = data.get("diagnostic")
            if diag:
                # 游戏页【诊断上报】按钮: 完整落盘 + 队列通知UI
                if not hasattr(self, "diag_sink"):
                    self.diag_sink = queue.Queue()
                # 诊断自动抓的ID映射直接入库(武将/皮肤名自动合并)
                imt = diag.pop("idmapTables", None)
                if imt:
                    try:
                        from . import id_map as _idmap
                        diag["idmapAdded"] = _idmap.merge(imt)
                    except Exception as _e:
                        diag["idmapAdded"] = {"err": str(_e)}
                self._save_net([{"evt": "__diagnostic", "ts": data.get("ts", 0), "full": diag}],
                               "diagnostics.json")
                self.diag_sink.put(diag)
            # v4.184★ 回放录制钩子: 入站消息落盘(仅在录制模式, 回放模式不二次录制)
            if self._replay_recorder is not None:
                try:
                    self._replay_recorder.in_event(data)
                except Exception:
                    pass

    def drain_diagnostic(self):
        """游戏页上报的诊断数据(一次一条)"""
        if not hasattr(self, "diag_sink"):
            return None
        try:
            return self.diag_sink.get_nowait()
        except queue.Empty:
            return None

    def drain_hud_btns(self):
        """游戏内小抄悬浮窗按钮点击(0条或多条)"""
        if not hasattr(self, "hud_btn_sink"):
            return []
        out = []
        try:
            while True:
                out.append(self.hud_btn_sink.get_nowait())
        except queue.Empty:
            _SIL.note("core/bridge.py:203", "while True: out.append(self.hud_btn_sink.get_n")
        return out

    def drain_hotkey_logs(self):
        """v4.54★ 游戏页上报的快捷键事件(0条或多条), 形如 {"key":"Tab","text":"三连合成 → 阻止"}。

        只给『快捷键』页的日志区用: 用户按了游戏页的 Tab 键, 助手那页要能看到这一行。
        """
        if not hasattr(self, "hk_log_sink"):
            return []
        out = []
        try:
            while True:
                out.append(self.hk_log_sink.get_nowait())
        except queue.Empty:
            _SIL.note("core/bridge.py:218", "while True: out.append(self.hk_log_sink.get_no")
        return out

    def drain_net(self):
        """实时协议反馈(对局记录器消费)"""
        out = []
        try:
            while True:
                out.append(self.net_sink.get_nowait())
        except queue.Empty:
            _SIL.note("core/bridge.py:228", "while True: out.append(self.net_sink.get_nowai")
        return out

    # v4.22★ 落盘体积预算: 原来只按“条数”截断(保留最后 500 条), 但单条完整控件树可达数百 KB,
    #   net_structures.json 实测已涨到 3MB, 且【每次推送】都要把整个文件读进来再全量重写一遍。
    #   现在再加一层字节预算, 从最老的开始按 10% 批量丢弃(避免逐条 O(n^2))。
    _NET_MAX_BYTES = 2 * 1024 * 1024
    # v4.24★ 缓冲合并写参数
    _NET_KEEP = 500            # 每个文件最多保留条数
    _NET_FLUSH_INTERVAL = 2.0  # 同一文件两次写盘的最小间隔(秒): 把短时间内的多次推送合成一次全量写
    _NET_FLUSH_TICK = 1.0      # 兜底线程检查间隔(秒): 没有新推送时也要保证最后一批数据落盘

    def _note_net_error(self, fname, err, what="落盘失败"):
        """落盘相关错误统一记账。

        v4.24★ 原来这里是 `except Exception: pass` —— 存不上永远没人知道,
          而"数据没落盘"正是排查问题时最容易踩的坑(日志里什么都看不到)。
        """
        self._net_errors += 1
        self._net_last_error = f"{what} {fname}: {err}"
        try:
            print(f"[BridgeState] {self._net_last_error}")
        except Exception:
            _SIL.note("core/bridge.py:251", "print(f'[BridgeState] {self._net_last_error}')")

    def _load_net(self, fname):
        """读回已落盘内容(每个文件只在首次写入前读一次, 之后走内存缓冲)。"""
        import os
        try:
            from .config import DATA_DIR
            path = os.path.join(DATA_DIR, fname)
            if not os.path.exists(path):
                return []
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except Exception as e:
            # 文件损坏/被截断: 按空处理, 但留下痕迹(旧实现是静默清空历史)
            self._note_net_error(fname, e, "读取历史失败(按空处理)")
            return []

    def _write_net_locked(self, fname):
        """把内存缓冲写盘。调用方必须已持有 self._net_lock。"""
        import os, time as _t
        buf = self._net_cache.get(fname)
        if buf is None:
            return
        try:
            from .config import DATA_DIR
            os.makedirs(DATA_DIR, exist_ok=True)
            path = os.path.join(DATA_DIR, fname)
            blob = json.dumps(buf, ensure_ascii=False, indent=1)
            # 体积超预算就从最老的开始丢, 但至少保留 20 条, 免得刚发一条就被清空
            while len(blob) > self._NET_MAX_BYTES and len(buf) > 20:
                del buf[:max(1, len(buf) // 10)]
                blob = json.dumps(buf, ensure_ascii=False, indent=1)
            # 先写临时文件再原子替换: 旧实现直接 "w" 打开目标文件,
            # 写到一半崩溃/被强杀就会留下半截 JSON, 下次启动读不出来 → 历史被静默清空。
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(blob)
            os.replace(tmp, path)
            self._net_last_flush[fname] = _t.time()
            self._net_dirty.discard(fname)
        except Exception as e:
            self._note_net_error(fname, e)

    def _net_flush_loop(self):
        """兜底: 定期把还没写盘的缓冲刷出去(推送停止后最多 _NET_FLUSH_TICK 秒内落盘)。"""
        import time as _t
        while True:
            _t.sleep(self._NET_FLUSH_TICK)
            try:
                with self._net_lock:
                    if self._net_dirty:
                        for fn in list(self._net_dirty):
                            self._write_net_locked(fn)
            except Exception as e:
                self._note_net_error("(flusher)", e, "后台落盘异常")

    def flush_net(self):
        """立即把缓冲写盘(退出前调用, 避免最后几秒的数据留在内存里丢掉)。"""
        try:
            with self._net_lock:
                for fn in list(self._net_cache):
                    if fn in self._net_dirty:
                        self._write_net_locked(fn)
        except Exception as e:
            self._note_net_error("(flush_net)", e, "退出前落盘异常")

    def get_net_health(self):
        """落盘健康度(供状态面板显示: 有失败就必须能看见)"""
        with self._net_lock:
            return {
                "errors": self._net_errors,
                "last_error": self._net_last_error,
                "pending": sorted(self._net_dirty),
            }

    def _save_net(self, net, fname="net_samples.json"):
        """协议数据落盘 data/（流水 net_samples / 完整结构 net_structures），供分析与记录。

        v4.24★ 改为【内存缓冲 + 合并写】:
          旧实现每次调用都是「json.load 整个文件 → extend → del → json.dumps 全量 → 重写磁盘」,
          而 net_structures.json 实测 1.9MB / net_samples.json 668KB —— 也就是【每一条推送】
          都在读写约 2MB。现在先在内存里合并, 同一文件最多每 _NET_FLUSH_INTERVAL 秒落盘一次。
        """
        import time as _t
        # v4.68★ 落盘开关(默认关, 见 core/config.py 的 net_record)。
        #   关的只是【写盘】: 上游 STATE.net_sink 照旧收, 实时页(对局决策/实时对局)一点不受影响。
        #   为什么默认关: 用户算过账 —— 这两份文件是常驻内存 + 定期全量重写, 而用处只有
        #   "事后查某一局"(复盘时间轴)和离线抓包诊断; 诊断时再打开更合适。
        #   diagnostics.json 不受开关限制(那是用户主动点『诊断上报』的结果, 必须留下)。
        if fname != "diagnostics.json":
            try:
                from .config import CONFIG
                if not CONFIG.get("net_record"):
                    return
            except Exception as e:
                # 配置读不到就按"记录"走(宁可多写也别把诊断数据弄丢), 但要说一声:
                # 否则用户明明关了开关, 文件还在长 —— 那是最难查的一类"开关没生效"。
                import sys as _sys
                _sys.stderr.write("[协议流水] 开关读取失败, 本次按『记录』处理: %s: %s\n"
                                  % (type(e).__name__, e))
        try:
            with self._net_lock:
                buf = self._net_cache.get(fname)
                if buf is None:
                    buf = self._load_net(fname)
                    self._net_cache[fname] = buf
                buf.extend(net)
                if len(buf) > self._NET_KEEP:
                    del buf[:-self._NET_KEEP]   # 只留最近 _NET_KEEP 条(与旧行为一致)
                self._net_dirty.add(fname)
                # 启动兜底线程(第一次落盘时才建, 平时零开销)
                if self._net_flusher is None:
                    self._net_flusher = threading.Thread(
                        target=self._net_flush_loop, daemon=True, name="net-flusher")
                    self._net_flusher.start()
                if (_t.time() - self._net_last_flush.get(fname, 0.0)) >= self._NET_FLUSH_INTERVAL:
                    self._write_net_locked(fname)
        except Exception as e:
            self._note_net_error(fname, e, "缓冲/落盘异常")

    def get_state(self):
        with self.lock:
            return self.latest

    def take_result(self, cid):
        if self._replay_playback is not None:
            # 回放模式: 结果从录制 FIFO 取(按录制顺序, 与 cid 无关), 不走 cmd_results
            return self._replay_playback.pop_result()
        with self.lock:
            return self.cmd_results.pop(str(cid), None)

    def send_cmd(self, action, **kw):
        with self.lock:
            self.cmd_id += 1
            cmd = {"id": self.cmd_id, "action": action}
            cmd.update(kw)
            # v3.45★ 同步存入历史桶(上限1000, 超过后先删最老的), 供 /cmd?after= 过滤回放
            self._cmd_history.append(cmd)
            if len(self._cmd_history) > self._cmd_history_cap:
                del self._cmd_history[0:len(self._cmd_history) - self._cmd_history_cap]
            # v4.184★ 录制: 落盘出站命令
            if self._replay_recorder is not None:
                try:
                    self._replay_recorder.out_event(cmd)
                except Exception:
                    pass
            # v4.184★ 回放模式: 没有真实扩展消费 cmd_queue, 不再入队(避免无限堆积);
            #   结果由 take_result 从录制游标返回。
            if self._replay_playback is None:
                self.cmd_queue.put(cmd)
            else:
                # 回放: 推进命令游标, 缓存该命令在录制时对应的结果(take_result 返回它)
                try:
                    self._replay_playback.mark_send(cmd)
                except Exception:
                    pass
            return cmd["id"]

    def force_reset(self):
        """v3.50★ 外部触发强制重置: 下发指令让脚本重载 lastCmdId"""
        with self.lock:
            # 标记为已重置状态
            self.cmd_id = 0
            self._cmd_history = []
            # 清空队列
            while not self.cmd_queue.empty():
                try: self.cmd_queue.get_nowait()
                except queue.Empty: break
            # 下发重置指令 (ID=0 是特殊指令)
            self.cmd_queue.put({"id": 0, "action": "eval", "code": "console.log('[Bridge] Manual Reset'); lastCmdId = 0;"})

    def drain_cmds(self, after_id=0, client_session=0):
        """v4.00★ 增强重连: 引入 session_id 判定。
        如果客户端传来的 session_id 与服务端不一致，说明服务端重启过，强制客户端重置 lastCmdId。"""
        try:
            after_i = int(after_id or 0)
            c_sid = int(client_session or 0)
        except Exception:
            after_i = 0
            c_sid = 0
        
        # v4.00★ SessionID 校验逻辑: 仅在客户端已有 session_id 时进行强制重置
        if c_sid != 0 and c_sid != self.session_id:
             return [{"id": 0, "action": "eval", "code": f"console.log('[Bridge] Session Change: {c_sid} -> {self.session_id}'); lastCmdId = 0; window.__sgs_sid = {self.session_id};"}]
        
        # v3.50★ 强制重置逻辑 (兜底)
        if after_i > self.cmd_id + 100:
             return [{"id": 0, "action": "eval", "code": "console.log('[Bridge] ID Overflow Reset'); lastCmdId = 0;"}]

        cmds = []
        with self.lock:
            # 1) 先把 cmd_queue 里新产生的吸进历史
            while True:
                try:
                    c = self.cmd_queue.get_nowait()
                except queue.Empty:
                    break
                # 理论上 send_cmd 已经塞过 history；这里兜底双保险
                if self._cmd_history and self._cmd_history[-1] is c:
                    continue
                self._cmd_history.append(c)
                if len(self._cmd_history) > self._cmd_history_cap:
                    del self._cmd_history[0:len(self._cmd_history) - self._cmd_history_cap]
            # 2) 返回历史中 id>after 的 (如果 after=0, 最多返回历史 100 条避免包太大)
            limit = 500 if after_i > 0 else 80
            for c in self._cmd_history:
                if c.get("id", 0) > after_i:
                    cmds.append(c)
                    if len(cmds) >= limit:
                        break
            return cmds

    def drain_clicks(self):
        out = []
        try:
            while True:
                out.append(self.click_sink.get_nowait())
        except queue.Empty:
            _SIL.note("core/bridge.py:452", "while True: out.append(self.click_sink.get_now")
        return out

    def drain_msgs(self):
        """游戏战绩/对局消息(油猴message监听回传)"""
        with self.lock:
            if not hasattr(self, "_msgs"):
                self._msgs = []
            msgs = list(self._msgs)
            self._msgs = []
            return msgs


STATE = BridgeState()
# v3.48★ 重启请求标记: HTTP请求 /restart 或 UI按钮点后, 主循环读到=True→触发execv重开
__SGS_BRIDGE_RESTART_REQUESTED = False


def _restart_flag_path():
    """v4.160★ .restart_requested 的落点 —— 跟【实例】走(core/config.runtime_path)。

    以前是按 __file__ 拼到助手根目录: 多开时 5 个实例共用这一个标记, 于是
    "任何一个实例改端口/点重启"会把其余 4 个一起带走, 或者自己的标记被别人
    consume 掉 —— 表现为"点了重启没反应"或"没点重启自己重开了"。
    """
    from .config import runtime_path
    return runtime_path(".restart_requested")


def _load_cfg_dict():
    """读【本实例】config.json 的原始 dict(不与默认值合并) —— /reconfigure 改端口用。

    为什么不能直接 from .config import CONFIG: 那是 load_config() 的合并结果,
    把它整份写回去会把【其它实例/其它地方运行时加进去的键】固化成默认值,
    还会把本次进程的 SGS_BRIDGE_PORT 覆盖值一起写死进文件。
    """
    import json as _j
    from .config import CONFIG_PATH
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as _fh:
            d = _j.load(_fh)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def request_restart(reason="", open_install=False):
    """请求重启【本实例】(供 UI 改端口等本地操作使用)。

    为什么要有它: 以前只有 HTTP 的 /restart 会置这个标记, UI 自己想重启(比如
    改完端口必须重启才能重新 bind)没有入口。标记文件写在实例目录下 —— 只重启
    这一个实例, 不会把别的实例一起带走。
    open_install=True 时额外写个标记: 重启起来后(新进程)自动打开新端口的
    install.html, 把【浏览器脚本 + 皮肤】的端口一键同步过去 —— 否则只改了助手
    端口, 浏览器还往旧端口推, 表现为"改完端口连不上了"。
    """
    global __SGS_BRIDGE_RESTART_REQUESTED
    import time as _t
    __SGS_BRIDGE_RESTART_REQUESTED = True
    try:
        with open(_restart_flag_path(), "w", encoding="utf-8") as _f:
            _f.write("%s@%d" % (reason or "restart", int(_t.time())))
    except Exception:
        pass
    if open_install:
        try:
            from core.config import runtime_path
            with open(runtime_path(".open_install_after_restart"), "w",
                      encoding="utf-8") as _f2:
                _f2.write("1")
        except Exception:
            pass


def restart_requested():
    """供App每2秒轮询: True=应立即重启助手(已持久化标记文件也会同时存在)"""
    global __SGS_BRIDGE_RESTART_REQUESTED
    if __SGS_BRIDGE_RESTART_REQUESTED:
        return True
    try:
        import os as _os
        p = _restart_flag_path()
        if _os.path.exists(p):
            return True
    except Exception:
        _SIL.note("core/bridge.py:481", "import os as _os")
    return False


def consume_restart_flag():
    """App 完成重启前清掉标记, 防止下次再进反复重启"""
    global __SGS_BRIDGE_RESTART_REQUESTED
    __SGS_BRIDGE_RESTART_REQUESTED = False
    try:
        import os as _os
        p = _restart_flag_path()
        if _os.path.exists(p):
            _os.remove(p)
    except Exception:
        _SIL.note("core/bridge.py:495", "import os as _os")


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        # v4.146★ 优先回显真实 Origin, 不要写死 "*":
        #   部分 Chrome/Edge 版本在 PNA(私有网络访问)预检里【明确拒绝】
        #   Access-Control-Allow-Origin: *, 必须回显具体来源才放行(否则即使带了
        #   Access-Control-Allow-Private-Network: true 真实请求仍被 ERR_FAILED 拦掉)。
        #   这正好解释了"自己机器(老浏览器/关了PNA)正常, 别人机器(新浏览器强制PNA)全红"。
        #   没有 Origin 头(同源/本地页)时回退到 "*", 行为不变。
        _origin = (self.headers.get("Origin") if self.headers else None) or "*"
        self.send_header("Access-Control-Allow-Origin", _origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, HEAD")
        self.send_header("Access-Control-Allow-Headers",
                         "Content-Type, Accept, X-Requested-With, Access-Control-Request-Private-Network")
        # Chrome 私有网络访问(PNA)：https公开页面 -> localhost 预检需要响应:
        #   Access-Control-Allow-Private-Network: true
        # (Chrome 103+ 在 https://game.xxx 页面里 fetch http://127.0.0.1 要求此字段, 否则 204 也不通)
        self.send_header("Access-Control-Allow-Private-Network", "true")
        # Vary: 防止 CDN/中间缓存 把不同 Origin 下的响应缓存混掉
        try:
            self.send_header("Vary", "Origin, Access-Control-Request-Method, Access-Control-Request-Headers, Access-Control-Request-Private-Network")
        except Exception:
            _SIL.note("core/bridge.py:512", "self.send_header('Vary', 'Origin, Access-Contr")

    def _json(self, obj, code=200):
        # v4.87★ 请求日志埋点(/_raw 里也有一份) —— 只挂一边就会漏掉一半请求,
        #   页面上的日志会看起来"从来没人来拉过规则"。note_request 自己全包异常, 不会拖垮响应。
        from core import res_server as _RSL
        _RSL.note_request(getattr(self, "path", ""), code,
                          getattr(self, "command", "GET"),
                          (self.headers.get("User-Agent", "") if self.headers else ""))
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        from core import transport_metrics as _TM
        try:
            request_bytes = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            request_bytes = 0
        request_started = getattr(self, "_transport_started_at", None)
        response_ms = max(0.0, (time.monotonic() - request_started) * 1000.0) if request_started else 0.0
        _TM.record(getattr(self, "command", "GET"), getattr(self, "path", ""),
                   request_bytes, len(body), code, response_ms)
        # 浏览器端经常会中途断连(Ctrl-R/页面跳转/Tampermonkey后台取消), 这里要吞掉 BrokenPipe/ConnectionAborted,
        # 不然 do_POST except 里再调用 _json 会触发 "During handling ... another exception" 然后崩掉
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            # v3.45★ PNA 头必须跟在**所有**真实响应(200/400)里, 不只是 OPTIONS. 否则 Chrome PNA 策略
            #   在真正 fetch 时再发一次预检会判失败 (浏览器扩展 Tampermonkey 尤其敏感)
            self._cors()
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
            return

    def log_message(self, *a):   # 静默访问日志
        pass

    def do_OPTIONS(self):
        try:
            self.send_response(204)
            self._cors()
            self.send_header("Access-Control-Max-Age", "600")
            self.end_headers()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
            return

    def do_HEAD(self):
        # PNA 预检前 Chrome 可能发 HEAD 探测本地端口
        try:
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self._cors()
            self.end_headers()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
            return

    def do_INSTALL(self):
        """v4.00★ install引导: 增加 SessionID 状态展示与秒连说明"""
        import os as _os
        port = getattr(self.server, "server_port", 8765)
        host = self.headers.get("Host") or f"127.0.0.1:{port}"
        base = f"http://{host}"
        js_url = f"{base}/sgs_bridge.user.js"
        EMPTY_JSON = "'" + "{}" + "'"
        html = (f"<!doctype html><meta charset='utf-8'><title>三国杀自走棋 · 深度连接优化版 v4.00</title>"
                f"<body style='font-family:Microsoft YaHei UI;max-width:760px;margin:36px auto;padding:20px;"
                f"background:#0e0e13;color:#e6e6e6'>"
                f"<h2 style='margin-top:0'>🎯 深度连接优化 (v4.00 · Session级秒连)</h2>"
                f"<ol style='line-height:2'>"
                f"<li>端口回写： localStorage['sgs_bridge_port'] = <b style='color:#ffd700'>{port}</b></li>"
                f"<li id='st2'>连通性自检： <span style='color:#ffb347'>检测中…</span></li>"
                f"<li>SessionID： <b style='color:#6be585'>{STATE.session_id}</b> (用于秒级重连)</li>"
                f"<li id='st3'>安装脚本：<a id='alink' href='{js_url}' target='_blank'>点此安装/更新 v4.00 脚本</a></li>"
                f"</ol>"
                f"<div style='background:#181822;border-radius:8px;padding:16px 20px;margin-top:20px'>"
                f"  <h4 style='color:#7fd4ff;margin-top:0'>🚀 v4.00 优化项:</h4>"
                f"  <ul style='font-size:13px;color:#aaa;line-height:1.6'>"
                f"    <li>引入 <b>SessionID</b> 机制，解决 Python 重启后浏览器端“假死”10秒的问题，实现秒连。</li>"
                f"    <li>轮询频率提升：数据推送 1.5s -> <b>1.0s</b>，指令拉取 0.6s -> <b>0.5s</b>。</li>"
                f"    <li>更智能的 HOST 切换逻辑，减少无效的 127.0.0.1/localhost 切换导致的抖动。</li>"
                f"  </ul>"
                f"</div>"
                f"<script>"
                f"  try {{ localStorage.setItem('sgs_bridge_port', '{port}');"
                f" localStorage.setItem('sgs_skin_swap_port', '{port}'); }} catch(e){{}}"
                f"  fetch('{base}/ping').then(r=>r.json()).then(d=>{{"
                f"    document.getElementById('st2').innerHTML = '连通性自检： <span style=\"color:#6be585\">✅ OK, pid='+d.pid+'</span>';"
                f"    setTimeout(()=>window.location.href='{js_url}', 1000);"
                f"  }}).catch(err=>{{"
                f"    document.getElementById('st2').innerHTML='连通性自检： <span style=\"color:#ff6b6b\">❌ 失败</span>';"
                f"  }});"
                f"</script></body>")
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._transport_started_at = time.monotonic()
        # v3.48★ 全局重启请求标记 (GET /restart & /reconfigure 也写; 放顶部避免 Python global 赋值前声明错误)
        global __SGS_BRIDGE_RESTART_REQUESTED
        from urllib.parse import urlparse
        up = urlparse(self.path)
        p = up.path
        q = up.query
        if p == "/cmd" or p.startswith("/cmd"):
            after = 0
            session = 0
            try:
                from urllib.parse import parse_qs
                qs = parse_qs(q)
                after = int(qs.get("after", ["0"])[0])
                session = int(qs.get("session", ["0"])[0])
            except Exception:
                _SIL.note("core/bridge.py:617", "from urllib.parse import parse_qs")
            
            cmds = STATE.drain_cmds(after_id=after, client_session=session)
            # v3.45★ 回传当前最大 id: 脚本即使没执行到任何 cmd, 也能把 lastCmdId 前移
            try:
                with STATE.lock:
                    max_id = STATE.cmd_id
                    hist_max = STATE._cmd_history[-1]["id"] if STATE._cmd_history else 0
                    max_id = max(max_id, hist_max)
            except Exception:
                max_id = max(c["id"] for c in cmds) if cmds else after
            
            self._json({
                "commands": cmds, 
                "server_cmd_id_max": max_id,
                "session_id": STATE.session_id # v4.00★ 返回当前 session_id
            })
        elif p == "/transport_stats":
            from urllib.parse import parse_qs
            from core import transport_metrics as _TM
            if parse_qs(q).get("start", ["0"])[0] == "1":
                _TM.start()
            self._json(_TM.snapshot())
        elif p == "/status":
            self._json({"has_state": STATE.get_state() is not None})
        elif p == "/state":
            # 调试接口: 返回当前游戏状态摘要(用于验证助手是否在收数据)
            st = STATE.get_state()
            if not st:
                self._json({"connected": False})
            else:
                import time as _t2
                texts = st.get("texts") or []
                self._json({
                    "connected": (_t2.time() - st.get("_recv_ts", 0)) < 6.0,
                    "last_recv_ago": round(_t2.time() - st.get("_recv_ts", 0), 1),
                    "recv_pid": st.get("recv_pid"),   # v3.49★ UI 线程读来判断"假绿(旧进程残留)"
                    "recv_ts":  st.get("_recv_ts"),
                    "net_count": len(st.get("net", [])),
                    "has_texts": bool(texts),
                    "texts_count": len(texts) if isinstance(texts, list) else 0,
                    "texts_head": [str(t)[:40] for t in (texts if isinstance(texts, list) else [])[:20]],
                    "ts": st.get("ts", 0),
                    "script_v": st.get("v"),
                    "hasLaya": st.get("hasLaya"),
                    "watchdog": st.get("watchdog"),
                    "recording": st.get("recording"),
                })
        elif p == "/ping":
            # v3.44★ 轻量健康检查: 油猴脚本/浏览器端 用这个判断助手是否在运行
            import os as _os
            st = STATE.get_state()
            import time as _t
            last_ago = None
            if st and st.get("_recv_ts"):
                last_ago = round(_t.time() - st["_recv_ts"], 2)
            self._json({
                "ok": True,
                "mode": "assistant",
                "port": getattr(self.server, "server_port", None),
                "service": "sgs_bridge",
                "time": int(_t.time()),
                "pid": _os.getpid(),
                "last_push_ago_s": last_ago,
                "connected": (last_ago is not None and last_ago < 8.0),
            })
        # ===== v3.48★ /restart (写标记文件→UI端下一轮sys.exit + os.execv重开main.py) =====
        elif p in ("/restart", "/api/restart"):
            try:
                import os as _os
                flag_path = _restart_flag_path()
                try:
                    with open(flag_path, "w", encoding="utf-8") as _f:
                        _f.write(str(int(__import__("time").time())))
                except Exception:
                    _SIL.note("core/bridge.py:686", "with open(flag_path, 'w', encoding='utf-8') as")
                # 全局标记已在函数顶部 global __SGS_BRIDGE_RESTART_REQUESTED 声明
                __SGS_BRIDGE_RESTART_REQUESTED = True
                self._json({"ok": True, "flag": flag_path, "restart": True, "pid": _os.getpid()})
            except Exception as e:
                self._json({"ok": False, "err": str(e)}, 500)
        elif p in ("/reconfigure", "/api/reconfigure"):
            # v3.48★ 修改端口等桥接配置(持久化到config.json + 更新进程内STATE端口供下一次重连用)
            try:
                from urllib.parse import parse_qs as _pq
                import json as _json, copy as _cp, os as _os
                payload = None
                try:
                    clen = int(self.headers.get("Content-Length") or "0")
                    if clen > 0:
                        body = self.rfile.read(clen).decode("utf-8", errors="ignore")
                        try: payload = _json.loads(body)
                        except Exception: payload = None
                except Exception:
                    payload = None
                if not isinstance(payload, dict):
                    # 允许 querystring: /reconfigure?port=18765
                    try:
                        qs = _pq(up.query)
                        payload = {}
                        if "port" in qs:
                            try: payload["port"] = int(qs["port"][0])
                            except Exception: _SIL.note("core/bridge.py:713", "payload['port'] = int(qs['port'][0])")
                    except Exception: payload = {}
                new_port = None
                if isinstance(payload.get("port"), int):
                    np = int(payload["port"])
                    if 1024 <= np <= 65535:
                        new_port = np
                if not new_port:
                    self._json({"ok": False, "err": "port必须是1024-65535的整数"}, 400)
                    return
                # v4.160★ 走 core.config 的 CONFIG_PATH + save_config():
                #   ① 路径必须按【本实例】算(以前写死助手根 config.json, 多开时
                #      会把别的实例的配置改掉 —— 那正是"我改端口, 别的窗口跟着变");
                #   ② save_config 是 tmp+replace 原子写, 这里原来是直接 "w" 覆盖,
                #      写一半崩了就留一个坏 config.json(与 config.py 的口径不一致)。
                from .config import CONFIG_PATH as _cfg_path, save_config as _save_cfg
                cfg_path = _cfg_path
                try:
                    cur_cfg = _load_cfg_dict()
                except Exception:
                    cur_cfg = {}
                if not isinstance(cur_cfg.get("bridge"), dict):
                    cur_cfg["bridge"] = {}
                old_port = cur_cfg["bridge"].get("port")
                cur_cfg["bridge"]["port"] = new_port
                try:
                    _save_cfg(cur_cfg)
                except Exception as e_save:
                    self._json({"ok": False, "err": "config写入失败:" + str(e_save)}, 500)
                    return
                # 通知下次 restart 用新端口(也可立即关server再开，但我们用重启助手最稳)
                try:
                    flag_path = _restart_flag_path()
                    with open(flag_path, "w", encoding="utf-8") as _f:
                        _f.write(str(int(__import__("time").time())) + "\nnew_port=" + str(new_port))
                except Exception:
                    _SIL.note("core/bridge.py:748", "flag_dir = _os.path.dirname(_os.path.dirname(_")
                self._json({"ok": True, "old_port": old_port, "new_port": new_port,
                            "restart_required": True,
                            "tip": f"已写config.json bridge.port={new_port}，助手将自动重启监听新端口 → 浏览器请刷新install页: http://127.0.0.1:{new_port}/install.html"})
            except Exception as e:
                self._json({"ok": False, "err": str(e)}, 500)
        elif p in ("/install", "/install.html"):
            self.do_INSTALL()
            return
        # ===== v4.70★ 资源替换(换肤/换背景)三件套挂到主端口上 =====
        #   为什么挂在【这里】而不是单开一个端口:
        #     上一版要在「资源替换」页手动点「启动图片服务」才会起 core/res_server 的 8767,
        #     忘了点 → 规则目标指向一个没在监听的端口 → "图配好了却没生效", 还看不出是谁的锅。
        #     桥接服务是助手必开的, 挂过来之后: 端口只有一个(不用记/不用点), 规则里的地址一定活着,
        #     换肤脚本也就能直接从这里读规则、装脚本。
        #   实现全在 core/res_server.py(那边还有独立服务, 两边共用同一批纯函数, 不各写一份)。
        elif p in ("/res", "/res.html"):
            # 资源替换服务页(端口/规则条数/安装入口) —— 资源替换 Tab 的「资源服务页」按钮打开它
            try:
                from core import res_server as _RS
                port = getattr(self.server, "server_port", None)
                html = _RS.index_html(base=("http://127.0.0.1:%s" % port) if port else None)
                self._raw(200, html, "text/html; charset=utf-8", cache="no-store")
            except Exception as e:
                self._raw(500, "资源服务页生成失败: %s" % e, "text/plain; charset=utf-8")
        elif p == "/gallery":
            # v4.74★ 资源图库管理页 —— 预览页的可操作版: 添加/编辑/选图/导入/导出。
            try:
                from core import res_server as _RS
                port = getattr(self.server, "server_port", None)
                html = _RS.gallery_html(base=("http://127.0.0.1:%s" % port) if port else None)
                self._raw(200, html, "text/html; charset=utf-8", cache="no-store")
            except Exception as e:
                self._raw(500, "图库页生成失败: %s" % e, "text/plain; charset=utf-8")
        elif p in ("/resapi/health", "/resapi/files", "/resapi/log", "/resapi/status",
                   "/resapi/sizeCheck", "/resapi/official", "/resapi/openFolder",
                   "/resapi/all"):
            # v4.87★ 体检 / 列图 / 日志 / 状态 / 尺寸比对 / 查原地址 / 打开文件夹
            #   —— 与主服务同一套实现(res_server), 必须两边都挂:
            #   /res 页在【助手主端口】上打开时走的是这里的路由, 只挂在 res_server 那边的话,
            #   主端口上点按钮就是 404 —— 表现为"按钮点了没反应"。
            try:
                from core import res_server as _RS
                from urllib.parse import parse_qs as _pq
                port = getattr(self.server, "server_port", None)
                base = ("http://127.0.0.1:%s" % port) if port else None
                qq = _pq(urlparse(self.path).query)
                if p == "/resapi/health":
                    self._json(_RS.health_json(base=base))
                elif p == "/resapi/files":
                    self._json(_RS.files_json(base=base))
                elif p == "/resapi/log":
                    self._json(_RS.req_log_json(limit=(qq.get("limit") or [200])[0],
                                                only=(qq.get("only") or [""])[0] or None))
                elif p == "/resapi/status":
                    self._json(_RS.status_json(base=base))
                elif p == "/resapi/sizeCheck":
                    self._json(_RS.size_check_json(base=base,
                                                   limit=(qq.get("limit") or [20])[0]))
                elif p == "/resapi/official":
                    self._json(_RS.official_json((qq.get("q") or [""])[0], base=base))
                elif p == "/resapi/all":
                    self._json(_RS.all_json((qq.get("q") or [""])[0],
                                            (qq.get("only") or [""])[0],
                                            limit=(qq.get("limit") or [800])[0]))
                else:
                    self._json(_RS.open_img_dir())
            except Exception as e:
                self._json({"error": "%s: %s" % (type(e).__name__, e)}, 500)
        elif p == "/resapi/state":
            try:
                from core import res_server as _RS
                port = getattr(self.server, "server_port", None)
                self._json(_RS.gallery_state_json(
                    base=("http://127.0.0.1:%s" % port) if port else None))
            except Exception as e:
                self._json({"error": "%s: %s" % (type(e).__name__, e)}, 500)
        elif p == "/resapi/export":
            # 导出自定义规则(JSON 文件下载 —— 这条【要】带附件头, 和 *.user.js 相反)
            try:
                from core import res_server as _RS
                import json as _json2
                body = _json2.dumps({"type": "sgs-res-rules", "rules": _RS.RR.load_rules()},
                                    ensure_ascii=False, indent=2).encode("utf-8")
                self._raw(200, body, "application/json; charset=utf-8", cache="no-store",
                          disposition="resource_rules.json")
            except Exception as e:
                self._raw(500, "导出失败: %s" % e, "text/plain; charset=utf-8")
        elif p == "/resapi/dress" or p == "/resapi/dressProbe":
            # v4.75★ OL 皮肤表: /resapi/dress?q= 状态+搜索, /resapi/dressProbe?skin= 探测一套的真身
            try:
                from urllib.parse import parse_qs as _pqs
                from core import res_server as _RS
                qs = _pqs(q)
                if p == "/resapi/dress":
                    self._json(_RS.dress_payload((qs.get("q") or [""])[0]))
                else:
                    self._json(_RS.dress_probe_payload((qs.get("skin") or [""])[0]))
            except Exception as e:
                self._json({"error": "%s: %s" % (type(e).__name__, e)}, 500)
        elif p in ("/rules", "/rules.json"):
            try:
                import os as _os
                from core import res_server as _RS
                # base 传本服务的地址: 规则里的目标是 http://127.0.0.1:<这里这个端口>/img/…
                #   (不能让它去猜 8767 —— 上一版就是这么错开的, 忘了点"启动图片服务"就指到空端口)
                port = getattr(self.server, "server_port", None)
                self._json(_RS.rules_json(base=("http://127.0.0.1:%s" % port) if port else None))
            except Exception as e:
                self._json({"error": "%s: %s" % (type(e).__name__, e)}, 500)
        elif p in ("/rules.meta", "/rules.meta.json"):
            # N4: 只回版本卡(season/rev/count)，几十字节 —— 扩展先问它，rev 没变就跳过整包 /rules。
            try:
                from core import res_server as _RS
                port = getattr(self.server, "server_port", None)
                full = _RS.rules_json(base=("http://127.0.0.1:%s" % port) if port else None)
                self._json({ "season": full.get("season", ""), "rev": full.get("rev", ""),
                             "count": full.get("count", 0) })
            except Exception as e:
                self._json({"error": "%s: %s" % (type(e).__name__, e)}, 500)
        elif p == "/ext/backups":
            # N2: 备份列表(只读文件名+大小+时间, 不解析正文 —— 备份可能带 7MB 脚本)。
            try:
                import os as _os
                from core import resource_rules as _RR
                bdir = _os.path.join(_RR.DATA_DIR, "ext_backups")
                items = []
                if _os.path.isdir(bdir):
                    for fn in _os.listdir(bdir):
                        if not (fn.startswith("sgs_ext_backup_") and fn.endswith(".json")):
                            continue
                        try:
                            stt = _os.stat(_os.path.join(bdir, fn))
                        except OSError:
                            continue
                        core = fn[len("sgs_ext_backup_"):-len(".json")]
                        seg = core.split("__")
                        items.append({"file": fn, "size": stt.st_size, "mtime": int(stt.st_mtime),
                                      "why": seg[1] if len(seg) > 1 else ""})
                items.sort(key=lambda x: x["mtime"], reverse=True)
                self._json({"ok": True, "dir": bdir, "items": items[:50]})
            except Exception as e:
                self._json({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}, 500)
        elif p.startswith("/ext/backups/"):
            # N2: 取某份备份正文(给扩展恢复用)。文件名严格校验, 挡路径穿越。
            try:
                import os as _os
                from core import resource_rules as _RR
                name = _os.path.basename(p[len("/ext/backups/"):])
                if not (name.startswith("sgs_ext_backup_") and name.endswith(".json")):
                    self._json({"ok": False, "error": "文件名不合法"}, 400); return
                fp = _os.path.join(_RR.DATA_DIR, "ext_backups", name)
                if not _os.path.isfile(fp):
                    self._json({"ok": False, "error": "备份不存在"}, 404); return
                with open(fp, "rb") as f:
                    data = f.read()
                self._raw(200, data, "application/json; charset=utf-8", cache="no-store")
            except Exception as e:
                self._json({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}, 500)
        elif p.startswith("/img/"):
            try:
                from core import res_server as _RS
                code, body, ctype = _RS.read_image(p[len("/img/"):])
                self._raw(code, body, ctype, cache="no-store, no-cache, must-revalidate")
            except Exception as e:
                self._raw(500, "读取失败: %s" % e, "text/plain; charset=utf-8")
        elif p.startswith("/dyn/"):
            # v4.113★ 动态皮三件套(skel/atlas/png) —— 页面是 https, 直接去官方域名取会被
            #   CORS 挡掉, 所以先 download_suite 拉到 data/dyn_skins/, 再由本机供出去。
            try:
                from core import res_server as _RS
                code, body, ctype = _RS.read_asset(p[len("/dyn/"):], "dyn")
                self._raw(code, body, ctype, cache="no-store, no-cache, must-revalidate")
            except Exception as e:
                self._raw(500, "读取失败: %s" % e, "text/plain; charset=utf-8")
        elif p.startswith("/lib/"):
            # v4.113★ spine-player.js —— 同 /dyn/, 且必须本机: 依赖 CDN 的话断网就整个失效。
            try:
                from core import res_server as _RS
                code, body, ctype = _RS.read_asset(p[len("/lib/"):], "lib")
                self._raw(code, body, ctype, cache="no-store, no-cache, must-revalidate")
            except Exception as e:
                self._raw(500, "读取失败: %s" % e, "text/plain; charset=utf-8")
        elif p in ("/dynbg", "/dynbg.json"):
            # v4.113★ 动态背景配置 —— base 传本服务地址, 三件套/spine-player 都从本机取
            try:
                from core import res_server as _RS
                port = getattr(self.server, "server_port", None)
                self._json(_RS.dynbg_json(base=("http://127.0.0.1:%s" % port) if port else None))
            except Exception as e:
                self._json({"error": "%s: %s" % (type(e).__name__, e)}, 500)
        elif p in ("/skins", "/skins.json"):
            # v4.71★ 官方立绘候选清单 —— 给【全能扩展管理页「官方立绘」】用的。
            #   /rules 里只有"已经配了替换的"几十条, 当不了候选表(能挑的只剩自己配过的)。
            try:
                from core import res_server as _RS
                self._json(_RS.skins_json())
            except Exception as e:
                self._json({"error": "%s: %s" % (type(e).__name__, e)}, 500)
        elif p in ("/scripts", "/scripts.json"):
            # v4.72★ 可安装脚本清单 —— 给【全能扩展管理页「获取新脚本」】用的。
            #   纯元信息(名字/描述/哪个已内置), 不含代码; 代码走原有的 *.user.js 路由。
            try:
                from core import res_server as _RS
                self._json(_RS.scripts_json())
            except Exception as e:
                self._json({"error": "%s: %s" % (type(e).__name__, e)}, 500)
        elif p in ("/ext/bridge.meta", "/ext/bridge.meta.json"):
            # v4.81★ 全能扩展的【主脚本版本卡】—— 扩展先拿这个比 rev，变了才去拉正文。
            #   为什么要分两步: 脚本正文 1MB 多, 每分钟都拉一遍是白费(扩展的 alarm 就是
            #   一分钟一次); 而这个只有几十字节, 常问不心疼。
            try:
                from core import ext_script as _ES
                self._json(_ES.meta())
            except Exception as e:
                # 打印到控制台/日志: 扩展那边只能看见 HTTP 500, 真原因(例: 源脚本
                #   @version 与 SCRIPT_VER 不一致)只有这一行能看到 —— 不打印就是一笔糊涂账。
                print("[ext/bridge.meta] 500: %s: %s" % (type(e).__name__, e))
                self._json({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}, 500)
        elif p in ("/ext/bridge.main.js", "/ext/bridge.js"):
            # v4.81★ 全能扩展的【主脚本正文】—— 扩展不再内置脚本, 每次从这里取最新版。
            #   来源只有 bridge/sgs_bridge.user.js 一个(渲染在 core/ext_script.py, 构建与
            #   运行时共用同一份), 所以"助手里改完 → 界面点『更新全能扩展脚本』→ 扩展拉走"
            #   这条链上没有任何一处是需要手工复制的。
            try:
                from core import ext_script as _ES
                pl = _ES.payload()
                if not pl.get("ok"):
                    self._json({"ok": False, "error": pl.get("error", "读不到主脚本")}, 500)
                else:
                    self._raw(200, pl["code"], "application/javascript; charset=utf-8",
                              cache="no-store, no-cache, must-revalidate")
            except Exception as e:
                print("[ext/bridge.main.js] 500: %s: %s" % (type(e).__name__, e))
                self._json({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}, 500)
        elif p in ("/ext/script-proxy", "/ext/script.proxy"):
            # B1 兜底: 代拉第三方脚本正文(扩展侧跨域/用户没授权该站时用)。
            #   助手是 Python、不受浏览器 CORS 限制; 也免去"为更个脚本给浏览器开某个站的权限"。
            #   SSRF 防护: 只放行 http/https 的【公网】地址, 挡掉环回/私网/链路本地(别把助手变内网扫描器)。
            try:
                from urllib.parse import parse_qs as _pqs
                import ipaddress as _ipmod
                import socket as _sock
                _u = (_pqs(q).get("url", [""])[0] or "").strip()
                if not _u:
                    self._json({"ok": False, "error": "缺 url 参数"}, 400); return
                _up = urlparse(_u)
                if _up.scheme not in ("http", "https") or not _up.hostname:
                    self._json({"ok": False, "error": "只支持 http/https 且要带主机名"}, 400); return
                try:
                    _infos = _sock.getaddrinfo(_up.hostname, None)
                except Exception as _e:
                    self._json({"ok": False, "error": "解析不了主机: %s" % _e}, 400); return
                for _it in _infos:
                    _ip = _it[4][0]
                    try:
                        _ipa = _ipmod.ip_address(_ip)
                    except ValueError:
                        self._json({"ok": False, "error": "主机地址异常: %s" % _ip}, 400); return
                    if _ipa.is_private or _ipa.is_loopback or _ipa.is_link_local or _ipa.is_reserved or _ipa.is_multicast:
                        self._json({"ok": False, "error": "不允许访问内网/环回地址(%s)" % _ip}, 403); return
                import requests as _rq
                _MAX = 24 * 1024 * 1024
                with _rq.get(_u, stream=True, timeout=(10, 30),
                             headers={"User-Agent": "sgs-bridge-script-proxy"}) as _resp:
                    if _resp.status_code != 200:
                        self._json({"ok": False, "error": "远端回了 HTTP %s" % _resp.status_code}, 502); return
                    _clen = _resp.headers.get("Content-Length")
                    if _clen and str(_clen).isdigit() and int(_clen) > _MAX:
                        self._json({"ok": False, "error": "脚本过大(>24MB)"}, 413); return
                    _parts = []; _tot = 0
                    for _ch in _resp.iter_content(65536):
                        if not _ch:
                            continue
                        _tot += len(_ch)
                        if _tot > _MAX:
                            self._json({"ok": False, "error": "脚本过大(>24MB)"}, 413); return
                        _parts.append(_ch)
                self._raw(200, b"".join(_parts), "application/javascript; charset=utf-8",
                          cache="no-store")
            except Exception as e:
                print("[ext/script-proxy] 500: %s: %s" % (type(e).__name__, e))
                self._json({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}, 500)
        elif p in ("/ext.zip", "/sgs_ext.zip"):
            # v4.71★ 【全能版】扩展下载入口(推荐): 油猴脚本本体 + GoRes 同款重定向 + 管理页。
            #   这里【要】带 Content-Disposition —— 用户要的就是一个 zip 文件,
            #   和上面 *.user.js 那条"绝不能带"正好相反(Tampermonkey 需要拦到安装页)。
            try:
                from core import res_server as _RS
                data = _RS.ext_zip(_RS.EXT_ALL_DIR_NAME)
                if data is None:
                    self._raw(404, "全能扩展目录不在(bridge/sgs_ext/) 或缺 manifest.json",
                              "text/plain; charset=utf-8")
                else:
                    self._raw(200, data, "application/zip", cache="no-store",
                              disposition="sgs_ext.zip")
            except Exception as e:
                self._raw(500, "打包全能扩展失败: %s" % e, "text/plain; charset=utf-8")
        elif p in ("/res_ext.zip", "/ext_light.zip"):
            # v4.71★ 【轻量版】扩展下载入口(只做重定向, 配 Tampermonkey 用)。
            try:
                from core import res_server as _RS
                data = _RS.ext_zip()
                if data is None:
                    self._raw(404, "轻量扩展目录不在(bridge/res_ext/) 或缺 manifest.json",
                              "text/plain; charset=utf-8")
                else:
                    self._raw(200, data, "application/zip", cache="no-store",
                              disposition="res_ext.zip")
            except Exception as e:
                self._raw(500, "打包轻量扩展失败: %s" % e, "text/plain; charset=utf-8")
        elif p.endswith(".user.js"):
            # 浏览器直接打开 http://127.0.0.1:8765/sgs_bridge.user.js → Tampermonkey 弹安装/更新
            # v4.70★ 不再写死 sgs_bridge.user.js: bridge/ 下的任意 *.user.js 都要能从这里装
            #   (皮肤替换脚本也要有个 http:// 安装入口 —— 双击文件 TM 不一定接管)。
            #   安全边界: 只取 basename, 且必须落在 bridge/ 目录里, 穿越写法取不到东西。
            try:
                import os as _os
                from core import res_server as _RS
                code, body, ctype = _RS.read_script(_os.path.basename(p))
                self._raw(code, body, ctype)
            except Exception as e:
                self._json({"error": str(e)}, 500)
        else:
            self._json({"error": "unknown"})

    def _raw(self, code, body, ctype, cache=None, disposition=None):
        """发一段原始字节(图片/脚本/zip 用)。CORS/PNA 头跟 _json 一致 —— 游戏页是 https,
        这些请求都算跨源, 少一个头浏览器就把响应丢掉。

        disposition: 可选的 Content-Disposition 文件名。**只给真正的下载用**(zip)。
          油猴脚本【绝对不能】带它 —— 带了就从"安装页"变成"下载文件", Tampermonkey 拦不到。
        """
        from core import res_server as _RSL
        _RSL.note_request(getattr(self, "path", ""), code,
                          getattr(self, "command", "GET"),
                          (self.headers.get("User-Agent", "") if self.headers else ""))
        if isinstance(body, str):
            body = body.encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            if cache:
                self.send_header("Cache-Control", cache)
            if disposition:
                self.send_header("Content-Disposition", 'attachment; filename="%s"' % disposition)
            self._cors()
            self.end_headers()
            if body:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
            return

    def do_POST(self):
        self._transport_started_at = time.monotonic()
        # v3.48★ 全局重启请求标记: POST /restart 或 /reconfigure 后, UI轮询poll_restart_flag读到后execv/spawn新进程
        global __SGS_BRIDGE_RESTART_REQUESTED
        from urllib.parse import urlparse, parse_qs
        if self.path.startswith("/resapi"):
            # v4.74★ 资源图库页(/gallery)的写入口 —— 与独立服务同一套实现(res_server)。
            #   放在 /push 前面: 图库页的 POST 不带 /push 前缀, 但显式放顶上省得挨个 elif 落空。
            up = urlparse(self.path)
            q = parse_qs(up.query)
            port = getattr(self.server, "server_port", None)
            base = ("http://127.0.0.1:%s" % port) if port else None
            from core import res_server as _RS
            if up.path == "/resapi/upload":
                n = int(self.headers.get("Content-Length") or 0)
                data = self.rfile.read(n) if n else b""
                try:
                    code, payload = _RS.api_upload(
                        (q.get("name") or [""])[0], data, base=base,
                        pattern=(q.get("pattern") or [""])[0],
                        kind=(q.get("kind") or [""])[0],
                        rule_name=(q.get("rname") or [""])[0])
                except Exception as e:
                    code, payload = 500, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
                self._raw(code, json.dumps(payload, ensure_ascii=False),
                          "application/json; charset=utf-8", cache="no-store")
                return
            if up.path == "/resapi":
                n = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
                except Exception as e:
                    self._json({"ok": False, "error": "不是合法 JSON: %s" % e}, 400)
                    return
                try:
                    code, payload = _RS.api_post(body, base=base)
                except Exception as e:
                    code, payload = 500, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
                self._json(payload, code)
                return
        if self.path.startswith("/ext/backup"):
            # N2: 扩展把"手动规则 + 自添脚本 + 设置"打包备份到助手本地, 轮转保留最近 20 份。
            #   治"脚本/规则凭空丢失"(v4.140 真发生过)。助手是本机服务, 扩展 POST 过来即可。
            try:
                import os as _os
                import time as _time
                import re as _re
                from core import resource_rules as _RR
                n = int(self.headers.get("Content-Length", 0) or 0)
                raw = self.rfile.read(n) if n > 0 else b""
                if len(raw) > 64 * 1024 * 1024:
                    self._json({"ok": False, "error": "备份过大(>64MB)"}, 413); return
                obj = json.loads(raw.decode("utf-8")) if raw else None
                if not isinstance(obj, dict):
                    self._json({"ok": False, "error": "需要一个 JSON 对象"}, 400); return
                bdir = _os.path.join(_RR.DATA_DIR, "ext_backups")
                _os.makedirs(bdir, exist_ok=True)
                _why = _re.sub(r"[^a-z0-9_-]", "", str(obj.get("why", "")).lower())[:24] or "na"
                fname = "sgs_ext_backup_%s__%s.json" % (_time.strftime("%Y%m%d_%H%M%S"), _why)
                with open(_os.path.join(bdir, fname), "w", encoding="utf-8") as f:
                    json.dump(obj, f, ensure_ascii=False)
                files = sorted([x for x in _os.listdir(bdir)
                                if x.startswith("sgs_ext_backup_") and x.endswith(".json")])
                removed = 0
                while len(files) > 20:
                    try:
                        _os.remove(_os.path.join(bdir, files.pop(0))); removed += 1
                    except OSError:
                        files.pop(0)
                self._json({"ok": True, "file": fname, "kept": len(files), "removed": removed})
            except Exception as e:
                self._json({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}, 500)
            return
        if self.path.startswith("/push"):
            import time
            n = int(self.headers.get("Content-Length", 0))
            try:
                import os as _os
                data = json.loads(self.rfile.read(n).decode("utf-8"))
                data["_recv_ts"] = time.time()
                # v3.49★ 记录『这一次/push是哪个Python进程收到的』——UI线程轮询读state时对比os.getpid(),
                # 发现不一致→判定"假绿: 收到push的是老进程残留,新面板没在真实收数据"→亮红报错
                data["recv_pid"] = _os.getpid()
                STATE.push(data)
                # v4.145★ 持续学习: 把这次推上来的协议流水喂给逐局记录器
                #   (配对 CReq/CResp → 判五态 → 脱敏 → 落盘 jsonl)。
                #   页面脚本一行都不用改 —— 它早就把流水带过来了。
                try:
                    from core import matchlog as _ML
                    _ML.feed_push(data)
                except Exception as _e:
                    # ★ 不再静默: 原来这里是 pass, 于是"页面没上报 / 上报了但字段是空的 /
                    #   我们处理时抛异常"这三种情况表现完全一样 —— 都是 jsonl 里孤零零一条
                    #   match_start, 排错只能靠猜。现在至少把异常留下来。
                    try:
                        from core import matchlog as _ML2
                        _ML2._diag("bridge.feed_push 异常", {"err": str(_e)})
                    except Exception:
                        pass
                self._json({"ok": True})
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 400)
        elif self.path.startswith("/send"):
            # 验证/自动化接口: POST /send {"action":"psend","name":"cmsg.CReqShopRefreshChess","data":{}}
            # → 指令入队, 油猴脚本GET /cmd拉取执行 → 结果经/push.cmdResults回传
            n = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
                action = body.pop("action", "")
                cid = STATE.send_cmd(action, **body) if action else -1
                import time as _t
                # 等待油猴脚本执行并回传结果(最多timeout秒)
                timeout = float(body.pop("timeout", 3.0))
                end = _t.time() + timeout
                res = None
                while _t.time() < end:
                    res = STATE.take_result(cid)
                    if res is not None:
                        break
                    _t.sleep(0.15)
                self._json({"cid": cid, "result": res})
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 400)
        elif self.path.startswith("/state"):
            # 调试接口: 返回当前游戏状态摘要(用于验证助手是否在收数据)
            st = STATE.get_state()
            if not st:
                self._json({"connected": False})
            else:
                import time as _t
                texts = st.get("texts") or []
                self._json({
                    "connected": (_t.time() - st.get("_recv_ts", 0)) < 6.0,
                    "last_recv_ago": round(_t.time() - st.get("_recv_ts", 0), 1),
                    "recv_pid": st.get("recv_pid"),   # v3.49★ UI线程读到=用于判断『假绿:旧进程残留』
                    "recv_ts": st.get("_recv_ts"),
                    "net_count": len(st.get("net", [])),
                    "has_texts": bool(texts),
                    "texts_count": len(texts),
                    "texts_head": [str(t)[:40] for t in texts[:20]],
                    "ts": st.get("ts", 0)
                })
        elif self.path.startswith("/live"):
            # ★OCR辅助接口: 返回LIVE协议数据(商店/备战区/对手等),让外部脚本知道有什么卡
            try:
                from core.protocol import LIVE
                with LIVE.lock:
                    me = dict(LIVE.me) if LIVE.me else {}
                    shop = LIVE.shop_snapshot()
                    others = [dict(o) for o in LIVE.others]
                # 简化输出(去掉内部字段)
                shop_simple = []
                for g in shop:
                    shop_simple.append({
                        "goodsID": g.get("goodsID"),
                        "chessID": g.get("chessID"),
                        "attack": g.get("attack"),
                        "hp": g.get("hp"),
                        "_sold": g.get("_sold", False),
                    })
                me_simple = {
                    "nickname": me.get("nickname"),
                    "coin": me.get("coin"),
                    "hp": me.get("hp"),
                    "shopLevel": me.get("shopLevel"),
                    "generalID": me.get("generalID"),
                    "_board": me.get("_board") or [],
                    "_lineUp": me.get("_lineUp") or [],
                }
                self._json({
                    "in_game": LIVE.in_game,
                    "phase": LIVE.phase,
                    "turn": LIVE.turn,
                    "me": me_simple,
                    "shop": shop_simple,
                    "others_count": len(others),
                    "pool_taken": dict(LIVE.pool_taken),
                })
            except Exception as e:
                self._json({"error": str(e)}, 500)
        # ===== v3.48★ POST版 /restart 和 /reconfigure (install页按钮用POST,避免querystring缓存/写路径) =====
        elif self.path.startswith("/restart") or self.path.startswith("/api/restart"):
            try:
                import os as _os
                flag_path = _restart_flag_path()
                try:
                    with open(flag_path, "w", encoding="utf-8") as _f:
                        _f.write("POST@" + str(int(__import__("time").time())))
                except Exception:
                    _SIL.note("core/bridge.py:1121", "with open(flag_path, 'w', encoding='utf-8') as")
                __SGS_BRIDGE_RESTART_REQUESTED = True
                self._json({"ok": True, "flag": flag_path, "restart": True,
                            "pid": _os.getpid(), "method": "POST"})
            except Exception as e:
                self._json({"ok": False, "err": str(e)}, 500)
        elif self.path.startswith("/reconfigure") or self.path.startswith("/api/reconfigure"):
            try:
                import json as _json, os as _os
                n = int(self.headers.get("Content-Length", 0))
                payload = None
                try:
                    if n > 0:
                        body = self.rfile.read(n).decode("utf-8", errors="ignore")
                        try: payload = _json.loads(body)
                        except Exception: payload = {}
                except Exception:
                    payload = {}
                if not isinstance(payload, dict): payload = {}
                new_port = None
                if isinstance(payload.get("port"), int):
                    np = int(payload["port"])
                    if 1024 <= np <= 65535: new_port = np
                if not new_port:
                    self._json({"ok": False, "err": "port必须是1024-65535整数"}, 400)
                    return
                # v4.160★ 同 GET 版: 写【本实例】的 config.json, 且走原子写
                from .config import save_config as _save_cfg
                cur_cfg = _load_cfg_dict()
                if not isinstance(cur_cfg.get("bridge"), dict): cur_cfg["bridge"] = {}
                old_port = cur_cfg["bridge"].get("port")
                cur_cfg["bridge"]["port"] = new_port
                try:
                    _save_cfg(cur_cfg)
                except Exception as e_save:
                    self._json({"ok": False, "err": "config写入失败:" + str(e_save)}, 500)
                    return
                try:
                    flag_path = _restart_flag_path()
                    with open(flag_path, "w", encoding="utf-8") as _f:
                        _f.write("reconfigure@" + str(int(__import__("time").time())) + "\nnew_port=" + str(new_port))
                except Exception:
                    _SIL.note("core/bridge.py:1168", "flag_path = _os.path.join(_os.path.dirname(_os")
                __SGS_BRIDGE_RESTART_REQUESTED = True
                self._json({"ok": True, "old_port": old_port, "new_port": new_port,
                            "restart_required": True,
                            "tip": f"config已写port={new_port}, 助手将在1.5-3s内自动重启并监听新端口 → 请刷新 http://127.0.0.1:{new_port}/install.html"})
            except Exception as e:
                self._json({"ok": False, "err": str(e)}, 500)
        else:
            self._json({"error": "unknown"}, 404)


class BridgeServer:
    def __init__(self, port=8765):
        self.port = port
        self.httpd = None
        self.thread = None

    def start(self):
        if self.httpd:
            return
        try:
            self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        except OSError as e:
            # v4.146★ 端口被占用: 最常见是【上一次没退干净的助手老进程】还占着这个端口,
            #   新助手绑不上 → 浏览器所有 /push /cmd 实际都打到了【老进程】。
            #   老进程若是旧构建(没有 PNA 头), 就会表现为"别人机器上 PNA 全红、自己机器正常"。
            pid = self._who_occupies(self.port)
            msg = (
                f"[BridgeServer][WARN] 端口 {self.port} 绑定失败: {e}\n"
                f"            本机 127.0.0.1:{self.port} 已被占用"
                + (f" (疑似老进程 PID={pid}, 结束它: 任务管理器 / "
                   f"PowerShell: Stop-Process -Id {pid} -Force)" if pid else "")
                + "\n            → 现在所有浏览器请求都落到了占用者身上, 新助手收不到任何数据!"
            )
            try:
                print(msg)
            except Exception:
                pass
            self.httpd = None
            self.bind_error = msg
            return
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        print(f"BridgeServer started and listening on http://127.0.0.1:{self.port}")

    def _who_occupies(self, port):
        """v4.146★ 跨平台查端口占用者 PID(查不到返回 None)。Windows 走 Get-NetTCPConnection,
        其它走 lsof。绑定失败时用来提示"老进程占位", 这是"别人机器连不上"的高频根因。"""
        import subprocess as _sp, sys as _sys
        try:
            if _sys.platform.startswith("win"):
                out = _sp.run(
                    ["powershell", "-NoProfile", "-Command",
                     "(Get-NetTCPConnection -LocalPort %d -ErrorAction SilentlyContinue | "
                     "Select-Object -First 1).OwningProcess" % int(port)],
                    capture_output=True, text=True, timeout=10).stdout.strip()
                return out or None
            out = _sp.run(["lsof", "-tiTCP:%d" % int(port), "-sTCP:LISTEN"],
                          capture_output=True, text=True, timeout=10).stdout.strip()
            return out.splitlines()[0] if out else None
        except Exception:
            return None

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
