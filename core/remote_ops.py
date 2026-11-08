# -*- coding: utf-8 -*-
"""桥接远程操作（纯注入版）：全部动作 = 协议直发(psend) 或 类名直调(direct_call)。
不依赖坐标/文本识别，不占真实鼠标，可后台。

保留的三条注入通道:
  ① 协议直发  psend("cmsg.CReqXxx", {...})  —— 服务器干活, 响应验证
  ② 类名直调  direct_call("Btn类名正则")    —— 执行游戏按钮注册的click回调
  ③ 协议学习  learn_proto(...)              —— 手动做1次, 永久学会pid后走①
"""
import json
import threading
import os
import time
import requests # 新增
from pathlib import Path # 新增
import core.id_map as IDMAP # 新增
from core import csv_loader # 新增(v4.68★ 推商店花费基准值)
from core import silence as _SIL


try:
    from .bridge import STATE
    from .config import load_config, DATA_DIR
    from . import id_map as IDMAP
    from . import sgs_decrypt_util # 新增
except ImportError:   # 直接运行本文件时
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from core.bridge import STATE
    from core.config import load_config, DATA_DIR
    from core import id_map as IDMAP
    from core import sgs_decrypt_util # 新增

# 新增常量
CONFIG_SGS_URL = "https://web.sanguosha.com/10/pc/res/config/Config.sgs"
RESC_WASM_URL = "https://web.sanguosha.com/10/pc/libs/min/resc"

# v4.108★ 上阵/遣散「每张之间」的最小间隔(秒) —— 集中在这里, 方便一处调全局。
#   为什么要改小: 油猴端原生上阵就是 for 循环连发 ReqChessLineUp(几乎无间隔)也能全部成功,
#   说明服务器扛得住连续请求; 而 Python 回退路径原来死等 0.45s/张, 上满 7 格要 3 秒多,
#   刷邓茂/陈珪/丁奉这种"一回合上十几次"的打法根本跑不起来。
#   为什么不完全去掉: 两次协议贴太近时, 后一个响应可能盖掉前一个的处理(买卡那里踩过),
#   0.10s 是实测能兼顾"快"和"不丢响应"的下限; 真出问题就往上调回 0.2。
DEPLOY_GAP_S = 0.10   # 上阵: 每张之间
DISMISS_GAP_S = 0.08  # 遣散: 每张之间

# v4.108★ 当先名单: 扫一次赛季棋子表, 把 effect 里含「当先」的 chessID 收齐, 之后常驻内存。
#   为什么改: 原来读 hero_picker.json 的 hero_tags.dangxian_chessIDs,
#   但那份数据里【根本没有 hero_tags 这个键】→ 名单恒为空 → 丁奉/陈珪/邓茂这些当先将
#   全部掉进"死等"分支, 当先的 buff 等待逻辑从来没真正生效过。
_DANGXIAN_ID_CACHE = None

def _dangxian_chess_ids():
    """返回 effect 含「当先」的棋子 ID 集合(跨赛季合并, 结果缓存)。"""
    global _DANGXIAN_ID_CACHE
    if _DANGXIAN_ID_CACHE is not None:
        return _DANGXIAN_ID_CACHE
    ids = set()
    try:
        from .config import DATA_DIR
        import glob as _glob
        seen = set()
        # 只认主数据目录, 跳过 _backup 里的历史快照(避免旧赛季的同名卡混进来)
        for pat in (os.path.join(DATA_DIR, "tavern_chess*.json"),
                    os.path.join(DATA_DIR, "**", "tavern_chess*.json")):
            for p in _glob.glob(pat, recursive=True):
                if "_backup" in p or p in seen:
                    continue
                seen.add(p)
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception:
                    continue
                if not isinstance(data, dict):
                    continue
                for k, v in data.items():
                    if not isinstance(v, dict):
                        continue
                    if "当先" in str(v.get("effect") or ""):
                        try:
                            ids.add(int(k))
                        except (TypeError, ValueError):
                            _SIL.note("core/remote_ops.py:84", "ids.add(int(k))")
    except Exception:
        _SIL.note("core/remote_ops.py:86", "from .config import DATA_DIR")
    _DANGXIAN_ID_CACHE = ids
    return ids

def _download_file(url: str, destination_path: Path, log_cb=None):
    if log_cb is None:
        log_cb = (lambda m: None)
    log_cb(f"正在下载: {url} 到 {destination_path}")
    try:
        # v4.22★ 加超时 + 用 with 保证 response 释放:
        #   原来 requests.get(url, stream=True) 既没有 timeout、也没有关闭 response,
        #   网络"半死"(连上了但一直不吐数据)时这里会永久挂起, 并且一直占着 socket 不放,
        #   调用方(资源下载)会卡在界面上没有任何反馈。
        #   timeout=(连接10s, 读取60s): 流式下载时指"两次数据之间"的间隔上限。
        with requests.get(url, stream=True, timeout=(10, 60)) as response:
            response.raise_for_status() # 检查HTTP请求是否成功
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            with open(destination_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
        log_cb(f"下载成功: {destination_path}")
        return True
    except requests.exceptions.RequestException as e:
        log_cb(f"下载失败 {url}: {e}")
        return False

def _field_ci(row, name):
    lname = name.lower()
    for fk, fv in row.items():
        if str(fk).lower() == lname:
            return fv
    return None


# ---------- v4.152★ 主公表(给选将那四道闸当判据用) ----------
_GENERALS_CACHE = None


def _load_generals():
    """取 data/generals.json(40 条, 含 reqMinionTyp)。

    脚本读不到配置文件, 但游戏的 CheckGeneralAlive 要用 RequiredMinionTyp 跟本局
    ChessMinionTypList 比对, 所以由这里查出来、随 lord_choose_general 指令带过去。
    任何异常都退化成空表 —— 查不到就让闸3 放行, 不挡路。
    """
    global _GENERALS_CACHE
    if _GENERALS_CACHE is not None:
        return _GENERALS_CACHE
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "data", "generals.json"), encoding="utf-8") as f:
            _GENERALS_CACHE = json.load(f)
    except Exception:
        _GENERALS_CACHE = []
    return _GENERALS_CACHE


class BridgeOps:
    def __init__(self, log_cb=None):
        self.log = log_cb or (lambda m: None)
        # v3.45★ 本地批缓冲: batchProtocol 开启时, 连续 psend(买/卖/刷新/准备/上阵) 先入队,
        # 跨 30ms 无新 psend 再合并 flush 成一次 psend_batch → 省掉 cmd poll/result 往返.
        # 设计: 单 psend 调用时 (1) 写入队列 (2) 启动 30ms 延迟线程 (若未启动)
        #       (3) 等 flush 线程填 perItem 结果到 self._batch_results[cid]
        #       (4) 返回 True/False 等价于原 psend verified 判定. 全程 MACRO 兼容.
        import threading as _th
        self._batch_lock = _th.Lock()
        self._batch_cv = _th.Condition(self._batch_lock)
        self._batch_queue = []      # [(name, data, cid)]
        self._batch_results = {}    # cid -> perItem dict
        self._batch_flusher_idle = True
        self._batch_thread = None

        self._auto_task_running = False
        self._auto_task_stop_flag = False
        self._auto_task_thread = None

        self._multi_buy_task_running = False
        self._multi_buy_task_stop_flag = False
        self._multi_buy_task_thread = None

        # Configured ID lists for Tampermonkey script
        self._followup_ids = []
        self._target_discard_ids = []
        self._permanent_keep_ids = []
        # v4.68★ 本季配置表里的随征棋子ID 缓存(见 _season_followup_ids): None=还没读过
        self._season_fu_cache = None

    def _batch_flag_on(self):

        """batchProtocol 是否开启.  v3.48★按用户要求=全局默认false并忽略push端开关(强制不使用批缓冲, 全部走单发)."""
        return False  # <-- 恢复时删这行, 使用下面的原始逻辑.
        try:
            st = STATE.get_state() or {}
            push_flags = (st.get("texts") or {}).get("flags") if isinstance(st.get("texts"), dict) else None
            if isinstance(push_flags, dict) and isinstance(push_flags.get("batchProtocol"), bool):
                return push_flags["batchProtocol"]
        except Exception:
            _SIL.note("core/remote_ops.py:160", "st = STATE.get_state() or {}")
        try:
            cfg = load_config() or {}
            f = (cfg.get("flags") or {}).get("batchProtocol")
            if isinstance(f, bool):
                return f
        except Exception:
            _SIL.note("core/remote_ops.py:167", "cfg = load_config() or {}")
        return False   # v3.48 默认 False (与脚本 __sgsHookFlags 一致)

    def _batch_flush_worker(self):
        """后台线程: 收集 >=30ms 内没新增的队列, 一次性 psend_batch, 结果按cid填回, 通知cv"""
        import time as _t
        while True:
            with self._batch_cv:
                if not self._batch_queue:
                    self._batch_flusher_idle = True
                    self._batch_thread = None
                    return
                # 等 30ms: 如果 30ms 内没新增就 flush, 否则继续 loop (吸收密集 psend)
                wait_ok = self._batch_cv.wait(timeout=0.030)
                if wait_ok and self._batch_queue:
                    continue   # 有新元素入队 → 再等一轮 30ms
                snapshot = list(self._batch_queue)
                self._batch_queue = []
            try:
                items_send = [{"name": n, "data": d} for (n, d, _) in snapshot]
                d = self.psend_batch(items_send, limit=max(len(items_send), 20)) or {}
                per = d.get("perItem") or []
            except Exception as e:
                d = {"count": 0, "failed": len(snapshot), "perItem": []}
                per = [{"ok": False, "err": "batch_ex:" + str(e)}] * len(snapshot)
            with self._batch_cv:
                for i, (_, _, cid) in enumerate(snapshot):
                    self._batch_results[cid] = per[i] if i < len(per) else {"ok": False, "err": "batch_missing"}
                self._batch_cv.notify_all()

    def _batch_enqueue(self, name, data):
        """入队 + 唤醒/启动flush线程, 阻塞等到结果, 返回 (perItem_dict|None)"""
        import time as _t
        import threading as _th
        cid_obj = object()   # 用对象作唯一 cid (不等同HTTP cid)
        with self._batch_cv:
            self._batch_queue.append((name, data, cid_obj))
            if self._batch_flusher_idle:
                self._batch_flusher_idle = False
                t = _th.Thread(target=self._batch_flush_worker, daemon=True,
                               name="BridgeOpsBatchFlush")
                self._batch_thread = t
                t.start()
            else:
                self._batch_cv.notify_all()   # 有新元素入队, 刷新 30ms 等待
            end = _t.time() + 7.0
            while cid_obj not in self._batch_results:
                rem = end - _t.time()
                if rem <= 0:
                    break
                self._batch_cv.wait(timeout=max(0.05, min(0.5, rem)))
            return self._batch_results.pop(cid_obj, None)

    # ---------- 基础 ----------
    def connected(self):
        """v4.00★ 统一使用 BridgeState 的连接信息判定"""
        info = STATE.get_connection_info()
        return info.get("connected", False)

    def get_conn_info(self):
        return STATE.get_connection_info()

    def get_version(self):
        """v4.22★ 获取游戏页油猴脚本的版本号。

        背景(已核查的 bug): ui/app.py 的 _check_version_loop() 每秒调用本方法,
        但 BridgeOps 里从未实现过它 → 每秒抛 AttributeError, 有两个后果:
          ① 工具栏 ver_lab 恒显示红色
             "连接失败('BridgeOps' object has no attribute 'get_version')";
          ② 与它同处一个 try 块的 STATE.drain_hud_btns() 永远执行不到,
             游戏内 HUD 按钮点了没反应。
        实现: 直接读 STATE 缓存的、油猴每次 push 都携带的 v 字段(SCRIPT_VER),
              不发起额外网络请求。
        返回: {"version": "4.22", "connected": True/False};
              从未收到过推送(脚本未连上)时返回 None。
        """
        try:
            v = STATE.get_script_version()
            conn = self.connected()
        except Exception:
            return None
        if not v or not conn:
            return None
        return {"version": v, "connected": True}

    def send(self, action, **kw):
        """下发一条指令(不等待结果)。v4.21★ 见下方 js=/code= 归一化说明。"""
        # v4.21★ 修复「send("eval", js=...) 静默失效」:
        #   油猴端 exec 的 eval/js 分支只读 c.code:
        #       case 'eval': case 'js': { const r = eval(c.code); ... }
        #   而本方法以前把 kw 原样透传给 STATE.send_cmd (内部就是 cmd.update(kw)),
        #   于是 send("eval", js="...") 发出的帧里只有 "js" 字段, 没有 "code" →
        #   油猴端 c.code === undefined → eval(undefined) 求值就是 undefined →
        #   回包文本变成字符串 "undefined" → Python 侧 json.loads 抛 JSONDecodeError,
        #   于是日志出现: [获取完整状态] 失败: 脚本返回非JSON数据: undefined
        #   这解释了为什么 F6 拿不到商店快照。受影响的一共 15 处调用:
        #   get_full_state / get_shop_goods / get_hand_chess / get_lineup / buy_chess_js /
        #   sell_chess_js / refresh_shop_js / refresh_battle_view / show_toast /
        #   updateChessData / blockTripleCombine / refreshHandCards / dumpShopNodeData 等,
        #   全都是静默失效。send_recv() 里有 js→code 的转换, send() 里漏了 —— 这里补齐。
        #
        #   另外油猴端是裸 eval, 顶层 return 属于 SyntaxError, 所以还要包一层函数体:
        #     · 以 return 开头的脚本 → 整段塞进函数体, 保留其返回值
        #     · 其它表达式/语句       → 用 return (...) 包住, 同样保住返回值
        #       (如 refreshShop() 返回 true, 调用点靠 res=="true" 判断成败;
        #        window.blockTripleCombine = true 也靠这个值回传)
        if action in ("eval", "js") and "js" in kw and "code" not in kw:
            code = str(kw.pop("js") or "").strip()
            if not code:
                kw["code"] = "undefined"
            elif code.startswith("return"):
                kw["code"] = "(function(){ " + code + " })()"
            else:
                # v4.22★ 修复「以分号结尾的脚本生成非法 JS」:
                #   原先直接拼 "return (" + code + ")"。若调用点写的是
                #       self.send_recv("eval", js="window.AutoBuyAndSell.stopRecruitOnly();")
                #   就会生成
                #       (function(){ return (window.AutoBuyAndSell.stopRecruitOnly();); })()
                #   —— 圆括号里出现语句终止符 ';' 是 JS 语法错误, 油猴端 eval 直接抛
                #   SyntaxError, 该指令静默失效(没有日志、没有报错, 只是不生效)。
                #   经 Node 沙箱实测, 受影响共 12 处: update_followup_ids /
                #   update_target_discard_ids / update_permanent_keep_ids /
                #   load_chess_name_map / load_spell_name_map / clear_spell_use_log /
                #   stop_auto_buy / stop_multi_buy_monitor / _auto_buy_task /
                #   _multi_buy_worker 等。
                #   修法: 先剥掉结尾分号; 若剥完仍含 ';'(多语句片段), 就不能再包
                #   return (...), 改为交给 eval 取完成值(既合法又保住返回值)。
                body = code.rstrip()
                while body.endswith(";"):
                    body = body[:-1].rstrip()
                if not body:
                    kw["code"] = "undefined"
                elif ";" in body:
                    kw["code"] = "(function(){ return eval(" + json.dumps(body) + "); })()"
                else:
                    kw["code"] = "(function(){ return (" + body + "); })()"
        return STATE.send_cmd(action, **kw)

    def wait_result(self, cid, timeout=3.0):
        """等待脚本执行某指令后的结果文本"""
        end = time.time() + timeout
        while time.time() < end:
            r = STATE.take_result(cid)
            if r is not None:
                return r
            time.sleep(0.15)
        return None

    def send_recv(self, action, payload=None, *, timeout=5.0, js=None, code=None):
        """发送指令(带payload字典)并等待结果。等价send+wait_result的简写.
        v4.13★ 兼容 js=/code= 关键字: 油猟端 eval 读 c.code, 这里自动把 js 转 code.
        """
        kw = dict(payload or {})
        # v4.13★ js= 自动转 code= (油猟端 eval case 读 c.code)
        if js is not None and code is None:
            code = js
        if code is not None:
            kw["code"] = code
        cid = self.send(action, **kw)
        return self.wait_result(cid, timeout)

    # ---------- 通道②: 类名直调 ----------
    def direct_call(self, keyword, label="", *, cls=None, name=None, path=None, xpath=None, return_data=False):
        """原理直调：找到按钮节点上注册的click回调并逐个执行(带合成事件).
        v3.43★支持 4 个节点定位参数(油猴侧 btn_list 回传节点的字段):
          - keyword: 类名正则(原始兼容参数, 传None时用cls/name/path三选一)
          - cls:     精确类名(脚本侧填 '^' + cls + '$' 用作匹配)
          - name:    节点 .name 字段(有时是唯一标识)
          - path/xpath: 节点唯一遍历路径
        传 cls/name/path 任一时, 脚本按该条件精确定位, 不再用宽泛类名正则.
        v4.102★ return_data=True 时回传脚本原始 JSON(dict)而不是布尔值 —— 调用方
          需要 vis(节点是否可见) 这类细节时用它(见 skip_battle 的可见性前置判断)。"""
        # 组装发给脚本的参数: 以前 keyword 单独传, 现在多加 path/name 字段
        send_kw = {}
        if keyword is not None and keyword != "":
            send_kw["keyword"] = keyword
        if cls:
            send_kw["cls"] = str(cls)
        if name:
            send_kw["name"] = str(name)
        if path:
            send_kw["path"] = str(path)
        if xpath and not send_kw.get("path"):
            send_kw["path"] = str(xpath)
        cid = self.send("direct_call", **send_kw)
        res = self.wait_result(cid, 4.5)
        if not res or res.startswith(("ERR", "notfound")):
            return False
        try:
            data = json.loads(res)
        except Exception:
            return False
        # CALL_ALL_METHODS 模式: 看 totalCalled>0 算成功
        if isinstance(data, dict) and data.get("mode") == "CALL_ALL_METHODS":
            tot = int(data.get("totalCalled") or 0)
            fails = int(data.get("fails") or 0)
            if label and (tot > 0 or fails > 0):
                self.log(f"[{label or '直调'}·全树回调] 调用{tot}次 异常{fails}")
            return tot > 0
        called = data.get("called") or []
        self.log(f"[{label or '直调'}·原理直调] 节点[{data.get('field')}] vis={data.get('vis')} 回调执行: {called}")
        ok = any(str(c).endswith(":ok") for c in called)
        return data if return_data else ok

    def _find_click(self, keyword, regex=False, timeout=2.0):
        cid = self.send("find_click", keyword=keyword, re=bool(regex))
        res = self.wait_result(cid, timeout)
        return res if (res and res.startswith("clicked")) else None

    # ---------- 通道①: 协议直发 ----------
    def psend_batch(self, items, limit=20):
        """批量协议直发(一次把多条打包发出去, 不再等每一条verify回包).
        返回: {count, failed, names, perItem}"""
        if not items:
            return None
        items_c = list(items)[:limit]
        cid = self.send("psend_batch", items=items_c, limit=int(limit))
        res = self.wait_result(cid, timeout=5.0)
        if res is None:
            self.log(f"[批量协议] 超时: 提交{len(items_c)}条 未收到脚本回包")
            return None
        try:
            d = json.loads(res)
        except Exception:
            self.log(f"[批量协议] 回包非JSON: {str(res)[:120]}")
            return None
        self.log(f"[批量协议] 提交{d.get('count')}/{len(items_c)}条  失败{d.get('failed',0)}  "
                 f"names={d.get('names')[:15]}")
        return d

    def psend(self, name, data=None, fn=None):
        """协议直发: 调网络单例发送函数发请求(如 cmsg.CReqShopRefreshChess)
        油猴端发出后等对应CResp响应——verified=True才是服务器真接受
        v3.43★ verified=True 时追加 MACRO 记录(宏回放可重放精确协议)
        v3.45★ batchProtocol=True → 本地入队 30ms 窗口 → 合并 psend_batch (省 N-1 次HTTP往返)"""
        # ---- 路径1: 本地聚合批 (batchProtocol ON + 无强制单发) ----
        if self._batch_flag_on() and not (data and isinstance(data, dict) and data.pop("__force_single", None)):
            per = self._batch_enqueue(name, data)
            self.log(f"[协议直发·批] {name} -> {str(per)[:160]}")
            ok = False
            pid = None
            if isinstance(per, dict):
                # 批模式 perItem.verified = 服务器真响应, 与原单条 psend.verified 语义一致
                v_ok = per.get("verified") is True and (per.get("errCode") is None or per.get("errCode") == 0)
                ok = bool(per.get("sent") or per.get("ok")) and v_ok
                try: pid = per.get("pid") if isinstance(per.get("pid"), int) else None
                except Exception: pid = None
            if ok:
                self.log(f"  ✓ 批·服务器已响应 pid={pid}")
                try:
                    from .recorder import MACRO
                    if MACRO.is_recording():
                        MACRO.record("psend", {"name": name, "payload": data or {}, "pid": pid})
                except Exception:
                    _SIL.note("core/remote_ops.py:421", "from .recorder import MACRO")
                return True
            # 批模式下未 verified (可能是没学到pid/本地自收) 不回退到单发: 节省时间
            return False
        # ---- 路径2: 原有单发 (batchProtocol=False 或 __force_single=True) ----
        cid = self.send("psend", name=name, data=data, fn=fn)
        res = self.wait_result(cid, 8.0)   # 含3秒响应验证窗口
        self.log(f"[协议直发] {name} -> {res}")
        if res and res.startswith("{"):
            try:
                d = json.loads(res)
                if d.get("verified"):
                    self.log(f"  ✓ 服务器已响应 pid={d.get('pid')} 耗时{d.get('elapsed', '?')}ms")
                    try:
                        from .recorder import MACRO
                        if MACRO.is_recording():
                            MACRO.record("psend", {"name": name, "payload": data or {},
                                                   "pid": d.get("pid")})
                    except Exception:
                        _SIL.note("core/remote_ops.py:440", "from .recorder import MACRO")
                    return True
                if d.get("via"):
                    self.log("  ⚠ 本地发出但服务器未响应——pid可能不对")
                    return False
            except Exception:
                _SIL.note("core/remote_ops.py:446", "d = json.loads(res)")
        return False

    # ---------- 通道③: 协议学习 ----------
    def learn_proto(self, name, hint, timeout=12000, force=False):
        """主动学习协议pid: 提示用户手动操作一次→油猴抓新发送→精确学习(永久存localStorage + 本地learned_pids.json)

        v4.22★ force=True: 先清掉该协议已有的 pid, 再等一个**新学到**的值。

        背景(已修的 bug): 油猴的 learn_wait 只要 __sgsPidMap 里**已存在**该协议就立刻返回
        {ok:true, pid:<既有值>} —— 旧实现会把这个**可能已被跨协议错配写坏的 pid** 当成本次
        学习成果持久化进 learned_pids.json, 并在下次启动时经 pid_sync 重新推回脚本。结果:
        错误值被"焊死", 用户怎么重新学习都修不好。现在只有真正新学到的(fresh=true)才落盘。
        """
        self.log(f"[协议学习] {name}" + ("(强制重新学习)" if force else ""))
        self.log(f"  → 请在 {timeout // 1000} 秒内 {hint}")
        r = self.send_recv("learn_wait", {"name": name, "timeout": timeout, "force": bool(force)},
                           timeout=timeout / 1000 + 3)
        try:
            d = json.loads(r)
        except Exception:
            d = {}
        if d.get("ok"):
            pid = d.get('pid')
            if d.get("fresh"):
                self.log(f"[协议学习] 成功 pid={pid} (永久保存, 下次自动直发)")
                # v3.54★ 持久化到本地 learned_pids.json
                self._save_learned_pid(name, pid)
            else:
                # 复用既有记录: 不落盘, 避免把可能已损坏的 pid 焊死
                self.log(f"[协议学习] 复用既有记录 pid={pid} (本轮没有新学习, 不落盘)")
            return True
        self.log(f"[协议学习] 失败: {d.get('err', r)}")
        return False

    def _save_learned_pid(self, name, pid):
        """v3.54★ 将学习到的 PID 持久化到本地文件"""
        try:
            from .config import DATA_DIR
            import os
            p = os.path.join(DATA_DIR, "learned_pids.json")
            data = {}
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
            data[name] = pid
            with open(p, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.log(f"[协议学习] 本地持久化失败: {e}")

    def _load_learned_pids(self):
        """v3.54★ 从本地加载已学习的 PID 并推送到脚本侧"""
        try:
            from .config import DATA_DIR
            import os
            p = os.path.join(DATA_DIR, "learned_pids.json")
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data:
                    self.send("pid_sync", pids=data)
                    self.log(f"[协议学习] 已从本地加载并同步 {len(data)} 个协议地址")
        except Exception:
            _SIL.note("core/remote_ops.py:510", "from .config import DATA_DIR")

    def _pid_learned(self, name):
        try:
            pm = (self.send_recv("pid_query", {"name": name}) or "")
            return str(pm).strip('"').lower() not in ("", "null", "{}", "false")
        except Exception:
            return True   # 查不到就保守认为已学过, 不再弹学习引导

    def proto_pid_info(self, name):
        """v4.22★ 查询某协议当前学习到的 pid 记录 → {"pid": int, "sample": dict}, 无则 None。

        ★注意: 这是**学习表**(localStorage.__sgsPidMap)里的值, 可能是被跨协议错配写坏的;
          游戏自带的权威表是 ProtoBufId / proto 字典 —— findPid() 只在学习表缺失时才回退到它,
          所以一个被写坏的记录会**一直遮蔽**正确值, 导致该协议永久失效(实机买卡就是这样)。
        """
        try:
            r = self.send_recv("pid_query", {"name": name}) or ""
            d = json.loads(r) if isinstance(r, str) else r
        except Exception:
            return None
        return d if (isinstance(d, dict) and d.get("pid") is not None) else None

    def audit_proto_pids(self, auto_fix=False):
        """v4.23★ 审计整张 pid 学习表, 找出被跨协议错配写坏的条目。

        为什么需要: 学习表(__sgsPidMap)的优先级**高于**游戏自带权威表(ProtoBufId/proto字典),
          一旦被写坏就永久遮蔽正确值 —— 该协议"怎么重新学习都修不好", 且不会有任何报错。

        判定(由油猴端 pid_audit 用与 findPid 完全相同的逻辑给出):
          OK        与权威表一致
          WRONG     与权威表不一致            → 应清除
          UNKNOWN   权威表里也查不到          → 无法自动判定; 若该协议正好失效即可疑
          DUPLICATE 多个协议共用同一个 pid    → 至少有一个是错的

        auto_fix=True: **只**自动清除 WRONG(有权威表作证, 确实写坏了), 清掉后 findPid 回退权威表。
          刻意**不**自动清 UNKNOWN / DUPLICATE —— 它们可能本来就是对的(只是权威表查不到),
          误删会把本来能用的协议搞坏。这两类只报告, 由你按"该协议是否正好失效"来手动决定。
        """
        try:
            r = self.send_recv("pid_audit", {}, timeout=6.0)
            d = json.loads(r) if isinstance(r, str) else r
        except Exception as e:
            self.log(f"[协议审计] 查询失败(桥接未连?): {e}")
            return {"ok": False, "err": str(e)}
        if not isinstance(d, dict) or not d.get("ok"):
            self.log(f"[协议审计] 油猴未返回有效结果: {str(r)[:200]}")
            return {"ok": False, "err": "invalid-response"}
        total = d.get("total", 0)
        bad = d.get("bad", []) or []
        stats = d.get("stats", {}) or {}
        if not total:
            # "所有协议都走游戏自带权威表"是**错的**: 本客户端根本没有可读的 CReq pid 权威表
            #   (ProtoBufId 不存在 / net.protoObjDict 是 protobufjs 类型字典, 值里没有 protoID),
            #   所以学习表为空 = 这些协议现在**发不出去**(findPid 返回 null → "pid未找到"),
            #   而不是"自动回退权威表"。被动学习只在自己操作时触发(点刷新/买卡/上阵…)才会进表。
            self.log("[协议审计] 学习表是空的 —— 现在没有任何协议握有 pid(发它会报『pid未找到』); "
                     "在游戏里操作一次(点刷新/买卡/上阵…)就会被被动学到")
            return d
        self.log("[协议审计] 学习表共 {} 条 → 可对账: 一致 OK={} / 被证伪 WRONG={} | "
                 "不可对账: 模糊命中 FUZZY={}(仅参考) / 查不到 UNKNOWN={} / 冲突 DUPLICATE={}".format(
                     total, stats.get("OK", 0), stats.get("WRONG", 0),
                     stats.get("FUZZY", 0), stats.get("UNKNOWN", 0), stats.get("DUPLICATE", 0)))
        # v4.24★ 每次都打「权威表探针」+ 逐条列表。
        #   为什么必需: OK=0 / UNKNOWN=100% 有**两种完全不同**的成因 ——
        #     ① 这些协议真的不在权威表里 → 本次审计只能查 pid 冲突, 查不出"单条写错但没撞车"的;
        #     ② 权威表根本读不到, 或**键名约定猜错** → 判据本身失效, 永远查不到。
        #   旧输出两者长得一模一样(只报一句"没有冲突"), 完全看不出该往哪查, 所以把证据全打出来。
        probe = d.get("auth_probe") or {}
        # 探针缺字段会让"查不到"和"根本没查"长得一模一样 —— 实机上刚踩过:
        #   Python 重启了、油猴脚本还是旧版, 于是新加的权威对账行一条都不打, 看着像"游戏里没有数据"。
        #   所以先判定探针是不是新版, 缺就直说, 省掉一轮"你看到的日志是旧的"的往返。
        if not any(k in probe for k in ("dict_kind", "resp_pairs_live", "pid_to_name", "has_proto_vo")):
            self.log("[协议审计] ⚠ 探针字段不完整(无 dict_kind/resp_pairs/pid_to_name) —— 浏览器里跑的"
                     "多半还是**旧版油猴脚本**(需重新注入并刷新游戏页), 本次无法尝试权威对账")
        if probe:
            if probe.get("has_proto_buf_id"):
                self.log("[协议审计] 权威表 ProtoBufId: 可用, {} 个键; 样本={}".format(
                    probe.get("pbi_keys"), probe.get("sample_keys")))
                if probe.get("probe_tried"):
                    self.log("[协议审计]   试查 {} → 用的键 {} 命中 {}".format(
                        probe.get("probe_name"), probe.get("probe_tried"), probe.get("probe_hit")))
            else:
                # 这条**每次都会打**: 本客户端没有可读的 CReq pid 权威表(依据见本函数末尾注释),
                #   所以"能不能对账"的答案永远是"不能" —— 别再把它读成"这次恰好没读到"。
                self.log("[协议审计] ⚠ 权威表 ProtoBufId 取不到(本客户端没有可读的协议 pid 表) —— "
                         "本次只能查 pid 冲突/重号, 查不出「单条写错但没和别人撞车」的记录")
            # 字典只说"79 个键"等于没说: 键是协议名还是数字 pid、值里有没有 protoID/名字,
            #   决定了它能不能当权威表用 —— 所以形态一并打出来。
            if probe.get("has_net_dict"):
                self.log("[协议审计]   协议字典: {} 个键 kind={} from={}".format(
                    probe.get("dict_keys"), probe.get("dict_kind"), probe.get("dict_from")))
                self.log("[协议审计]     样本键={} 值类型={}".format(
                    probe.get("dict_keys_sample"), probe.get("dict_val_type")))
                if probe.get("dict_val_fields"):
                    self.log("[协议审计]     值字段={} 值取样={}".format(
                        probe.get("dict_val_fields"),
                        probe.get("dict_val_seen") or probe.get("dict_val_sample")))
            else:
                self.log("[协议审计]   协议字典: 未取到")
            # ★ 唯一能直接证明"学习表里这些 pid 对不对"的东西:
            #   收到的报文自带 msg.protoName + msg.protoVO.protoID, 响应侧代码一直在采集它们。
            #   学习写入用的是**配对到的那次发送的 pid**(800ms 窗口猜的), 窗口挤了多条就配错 ——
            #   这正是 shared=false 的来源, 是配对噪声, 不等于值错。权威对才是判据。
            lp = probe.get("resp_pairs_live") or []
            dp = probe.get("resp_pairs_disk") or []
            self.log("[协议审计]   权威 Req↔Resp 对(报文自带 protoName+protoVO.protoID): "
                     "本次 {} 条 / 盘上 {} 条".format(len(lp), len(dp)))
            for x in (lp or dp)[-8:]:
                self.log("[协议审计]     {}(pid={})".format(x.get("name"), x.get("pid")))
            if probe.get("has_proto_vo"):
                self.log("[协议审计]   ProtoVO pid→名字反查: {}".format(probe.get("pid_to_name") or {}))
            else:
                self.log("[协议审计]   ProtoVO pid→名字反查: 不可用(globalThis.ProtoVO 取不到)")
        rows_all = d.get("all") or []
        if rows_all:
            self.log("[协议审计] ---- 学习表逐条({} 条{}) ----".format(
                len(rows_all), ", 仅列前 30" if len(rows_all) > 30 else ""))
            for it in rows_all[:30]:
                # 「配对是否吻合」只是 800ms 时间窗口猜出来的**副产物**: 窗口里挤了多条发送就会不吻合。
                #   实机证据: 买卡学习值 4018460094 与代码注释里的历史正确值一致, 一键买全也已实测可用 ——
                #   值是对的, 只是配对没吻合。所以这里不再打 ✗ : 判据自己哭狼会把下一次排查带偏。
                sh = it.get("shared")
                ev = ("配对吻合✓" if sh is True else
                      ("配对未吻合(窗口推断, 不参与判定)" if sh is False else "无配对证据"))
                tail = "" if it.get("respPid") is None else "  响应pid={}".format(it.get("respPid"))
                self.log("  [{}] {}  学习值={} 权威值={}({})  {}{}".format(
                    it.get("verdict"), it.get("name"), it.get("learned_pid"),
                    it.get("auth_pid"), it.get("auth_src"), ev, tail))
        if not bad:
            if stats.get("OK", 0):
                self.log("[协议审计] ✓ 没有被权威表证伪的记录, 也没有与其他 message 冲突的 pid")
            else:
                # 结论(2026-09 实机查实): **本客户端没有可读的 CReq pid 权威表**, 所以 OK 恒为 0 ——
                #   不是"这次没读到", 而是这张表根本不存在。四条查法全试过:
                #     ① globalThis.ProtoBufId                  → 不存在;
                #     ② net.protoObjDict(唯一读得到的协议表)    → protobufjs **类型字典**
                #        (值字段 options/name/fields/parent/…), 里面没有 protoID → 反查不出 pid
                #        (脚本 2506 行的注释当年已实锤同一条);
                #     ③ ProtoVO 反查(protoID→ProtoName)        → globalThis.ProtoVO 不存在;
                #     ④ "pid = 协议名的 32 位哈希"              → 用 86 组实机样本(名字→protoID, 全部 <2^32)
                #        拟合 crc32/adler32/murmur3/fnv1/fnv1a/djb2/sdbm/java31/md5/sha1 前 4 字节 ×
                #        11 种名字写法, **0/84 命中** → 不是哈希。
                #   但学习值的 **pid 本身是权威的**: 它取自 hook 游戏自己发送调用时读到的实参
                #   (__sgsSentQ 的 s0.pid), 不是猜的; 唯一可能错的是"这条 pid 挂在哪个协议名下" ——
                #   那只能靠功能实测判定(买卡: 4018460094 = 历史正确值, 且一键买全实测可用)。
                self.log("[协议审计] ⚠ OK=0 是**结构性的**: 本客户端没有可读的 CReq pid 权威表"
                         "(ProtoBufId 不存在 / protoObjDict 里没有 protoID / pid 也不是协议名哈希), "
                         "所以这里只能查重号与冲突")
                self.log("[协议审计]   值的正确性判据 = 功能实测(如一键买全); 『配对未吻合』不是值错的证据"
                         " —— 实测 Req 与 Resp 的 pid 本来就不相等(6/6)")
            if stats.get("FUZZY", 0):
                self.log(f"[协议审计] 注: 另有 {stats['FUZZY']} 条权威表只能模糊命中(键名互为子串), "
                         "这类值可能张冠李戴, 已刻意不参与判定")
            return d
        self.log(f"[协议审计] ⚠ 需要处理的记录 {len(bad)} 条:")
        for it in bad:
            sw = it.get("shared_with") or []
            extra = f"  与 {','.join(sw)} 冲突" if sw else ""
            # v4.24★ 补印 shared 证据: True = 写入当时"发送的 pid == 本响应自带的 pid",
            #   即这条响应基本可以确定就是该请求的应答(协议 id 唯一) → 自洽;
            #   False = 那条 pid 其实来自别的发送(本响应自己的 pid 是 respPid) → 靠时间窗口猜的,
            #   典型串台特征。排查/取舍时这是最关键的字段。
            sh = it.get("shared")
            if sh is None:
                ev = ""
            elif sh:
                ev = f" [自洽✓ 响应pid={it.get('respPid')}]"
            else:
                ev = f" [不自洽✗ 本响应pid={it.get('respPid')}]"
            self.log(f"  [{it.get('verdict')}] {it.get('name')}: "
                     f"学习值={it.get('learned_pid')} 权威值={it.get('auth_pid')}{extra}{ev}")
        wrong = [it for it in bad if it.get("verdict") == "WRONG"]
        dups = [it for it in bad if it.get("verdict") == "DUPLICATE"]
        if auto_fix and wrong:
            n = 0
            for it in wrong:
                nm = it.get("name")
                if nm and self.forget_proto_pid(nm):
                    n += 1
            self.log(f"[协议审计] 已清除 {n} 条被权威表证伪的记录 → findPid 将回退游戏自带权威表")
            d["fixed"] = n
        elif not auto_fix and wrong:
            self.log("[协议审计] 提示: audit_proto_pids(auto_fix=True) 可自动清除上述 WRONG 记录")
        if dups:
            shown = ", ".join(str(it.get("name")) for it in dups[:8])
            more = " ..." if len(dups) > 8 else ""
            self.log(f"[协议审计] 另有 {len(dups)} 条 DUPLICATE(与别的 message 共用 pid, 但权威表查不到 "
                     f"→ 无法证明谁对): {shown}{more}")
            self.log("[协议审计] 这类**已刻意不自动清除** —— 组内哪一条才是该 pid 的真正主人, "
                     "权威表查不到就无法判定, 逐条挑必然误删。")
            self.log("[协议审计] 处理办法:「工具 → 清空 pid 学习表并重来…」整表清空(见上一条命令), "
                     "然后正常玩一局让 pid 自动重新学习 ——")
            self.log("[协议审计]   ※ 前提是游戏页脚本已升到 v4.24: 它修掉了「一条发送被反复复用 → "
                     "同一个 pid 被成批盖到一堆协议上」的写入漏洞; 否则重学后还会再次串台。")
        if stats.get("FUZZY", 0):
            self.log(f"[协议审计] 注: 另有 {stats['FUZZY']} 条权威表只能模糊命中, 已刻意不参与判定")
        return d

    def forget_proto_pid(self, name=None, all_names=False):
        """v4.22★ 清除学习表里的协议 pid —— 用于修复被错配写坏的值。

        为什么需要它: 一旦 pid 被写坏, 下面是死循环 ——
          ① findPid 优先读学习表 → 错误值遮蔽游戏权威表 → psend 永远失败;
          ② learn_wait 一见"表里已有"就返回 ok → "重新学习"只是把坏值再确认一遍;
          ③ 旧 learn_proto 还把它持久化进 learned_pids.json → 重启后 pid_sync 再推回去 → 焊死。
        清掉之后: findPid 回退到游戏权威表(可能立即恢复), 或等用户手动操作重新学习。
        同时删除本地 learned_pids.json 的对应项, 否则下次启动又会被推回脚本。
        """
        try:
            if all_names:
                self.send_recv("pid_forget", {"all": True}, timeout=3.0)
            elif name:
                self.send_recv("pid_forget", {"name": name}, timeout=3.0)
            else:
                return False
            try:
                import os
                from .config import DATA_DIR
                p = os.path.join(DATA_DIR, "learned_pids.json")
                if os.path.exists(p):
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if all_names:
                        data = {}
                    else:
                        data.pop(name, None)
                    with open(p, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception as e:
                self.log(f"[协议修复] 本地 learned_pids.json 清理失败(不影响本次修复): {e}")
            self.log(f"[协议修复] 已清除 {'全部' if all_names else name} 的学习记录")
            return True
        except Exception as e:
            self.log(f"[协议修复] 清除失败: {e}")
            return False

    def relearn_proto(self, name, hint, timeout=12000):
        """v4.22★ 强制重新学习协议 pid: 先清掉旧记录, 再等你手动操作一次学新的。

        与 learn_proto(name, hint) 的区别: 后者在"表里已有"时会直接复用旧值(可能已被写坏),
        本方法是真的覆盖 —— 修复"该协议永久失效"就用它。
        """
        self.forget_proto_pid(name)
        return self.learn_proto(name, hint, timeout=timeout, force=True)

    # ---------- ID映射配置抓取(自动招募按名字匹配的前提) ----------
    def fetch_cfg(self):
        """从default.res.json找配置文件URL, 主动fetch并提取chessID->名字映射.
        大数据存localStorage(__fetch_xxx分块), 响应只传摘要, 避免JSON截断."""
        IDMAP.reset_namespace("lords", "chess", "cards", "skins", "lord_skills")
        self.log("[配置抓取] 已重置 lords/chess/cards/lord_skills/skins (保留手动学习的卡面映射)")
        cid = self.send("fetch_cfg")
        res = self.wait_result(cid, 15.0)
        if not res:
            self.log("[配置抓取] 无响应")
            return None
        try:
            d = json.loads(res)
        except Exception as e:
            self.log(f"[配置抓取] JSON解析失败: {type(e).__name__}: {e}")
            return None
        for s in (d.get("steps") or []):
            self.log(f"[配置抓取] {s}")
        storage_keys = d.get("storageKeys") or []
        tables = []
        for sk in storage_keys:
            raw = self.send_recv("storage_get", {"key": sk}, timeout=5.0)
            if not raw:
                continue
            try:
                mp = json.loads(raw)
            except Exception:
                continue
            tag = sk.replace("__fetch_", "").replace("__tmp", "")
            self.log(f"[配置抓取] 读到storage {tag}: {len(mp)}条")
            tables.append({"src": "fetch:" + tag, "map": mp})
        for tag, info in (d.get("maps") or {}).items():
            if isinstance(info, dict) and "map" in info and not any(
                    k in ("n", "hp", "atk", "skill_id", "desc", "chessID", "GeneralID") for k in info):
                mp = info.get("map") or {}
            elif isinstance(info, dict):
                mp = info
            else:
                continue
            if mp and isinstance(mp, dict):
                self.log(f"[配置抓取] ✓响应内 {tag}({len(mp)}条)")
                tables.append({"src": "fetch:" + tag, "map": mp})
        if not tables:
            self.log("[配置抓取] 无表可合并")
            return d
        mr = IDMAP.merge(tables)
        for src_key, msg in (mr.get("per_src") or {}).items():
            self.log(f"[配置抓取] {src_key}: {msg}")
        cs = IDMAP.counts()
        self.log(f"[配置抓取] 现库存: 主公{cs.get('lords',0)} 棋子{cs.get('chess',0)} "
                 f"武将{cs.get('gen',0)} 卡牌{cs.get('cards',0)}")
        # ===== v3.49★ 用户明确: chess.json/cards.json/lords.json 身份局混进了自走棋→武将精选全空/出错.
        # 从抓回来的tables里,按你给的5份TavernChessConfig.sgs.{xxx}Conf 表名+字段(SeasonID/IsAvailable/IsInRandomPool)单独
        # 分拣保存 data/tavern_{Chess,ChessSkill,ChessSpell,ChessWeaponSkill,ChessClientKeyword}.json,
        # build_hero_picker 后续先读这 5 份,这样自走棋和身份局彻底隔离.=====
        try:
            import os as _os
            from .config import DATA_DIR
            T_CATEGORIES = [
                # (key关键字, 输出文件名, 判定字段)
                ("ChessClientKeywordConf", "tavern_keywords.json",  None),  # 规则词条
                ("ChessWeaponSkillConf",   "tavern_weapons.json",   None),  # 坐骑/装备技能
                ("ChessSpellConf",         "tavern_spells.json",    None),  # 锦囊
                ("ChessSkillConf",         "tavern_lords.json",     None),  # 主公/技能
                ("ChessConf",              "tavern_chess.json",     None),  # 武将
            ]
            # v4.00★ 同时备份到用户指定的 skills.json 和 items.json
            EXTRA_MAP = {
                "tavern_lords.json": "skills.json",
                "tavern_spells.json": "items.json"
            }
            saved_stats = {}
            all_tavern = {}
            for cat_key, out_file, _ in T_CATEGORIES:
                # 1) 遍历所有tables,找 table.src 或者 table.map 最外层 key 里 包含 cat_key 的那一张源表
                src_rows = None
                src_tag = ""
                for t in tables:
                    mp = t.get("map") or {}
                    # 优先: map 里直接存在形如 "TavernChessConfig.sgs.ChessConf" 这种 top key
                    direct_hit = None
                    for mk in mp.keys():
                        if isinstance(mk, str) and (cat_key in mk):
                            direct_hit = mp.get(mk); src_tag = t.get("src","") + "/" + str(mk)
                            if isinstance(direct_hit, dict):
                                src_rows = direct_hit; break
                            elif isinstance(direct_hit, list):
                                src_rows = {str(i): x for i, x in enumerate(direct_hit)}; break
                    if src_rows: break
                    # 次优先: src 名含 cat_key → 整张 map 当作该表
                    if cat_key in str(t.get("src","")):
                        src_rows = mp; src_tag = t.get("src",""); break
                if not src_rows: continue
                # 2) 过滤: 非字典row跳过; 有IsAvailable!=1 & IsInRandomPool!=1 排除;没有SeasonID=身份局排除
                # ★字段名大小写不敏感(项目约定: 配置表字段名不区分大小写)。探针回传的是归一化行:
                #   棋子行季节字段是小写 season; 锦囊/技能归一化行无季节字段——但这些表全部抓自
                #   TavernChessConfig.sgs(本身就是自走棋配置), 用 src_tag 兜底放行, 不再全表误杀。

                from_tavern_cfg = "TavernChessConfig" in str(src_tag)
                cleaned_rows = {}
                total_in = len(src_rows); drop_unavail = 0; drop_not_pool = 0; drop_no_season = 0; drop_nondict = 0
                for rk, rv in src_rows.items():
                    if not isinstance(rv, dict): drop_nondict += 1; continue
                    # 存在SeasonID/season字段=自走棋; 或整表来自TavernChessConfig.sgs(本就是自走棋配置)
                    has_season = (_field_ci(rv, "SeasonID") is not None
                                  or _field_ci(rv, "season") is not None
                                  or from_tavern_cfg)
                    if not has_season: drop_no_season += 1; continue
                    # IsAvailable存在且=0的跳过
                    ia = _field_ci(rv, "IsAvailable")
                    if ia is not None and int(ia or 0) != 1:
                        drop_unavail += 1; continue
                    # IsInRandomPool存在且=0的跳过(但保留规则/坐骑/锦囊配置表,这些不需要卡池入池)
                    irp = _field_ci(rv, "IsInRandomPool")
                    if cat_key in ("ChessConf", "ChessSpellConf") and irp is not None:
                        if int(irp or 0) != 1:
                            drop_not_pool += 1; continue
                    
                    # v3.55★ 自动补全 desc 字段到本地 tavern_*.json 文件
                    # 这样用户打开 JSON 文件也能看到描述
                    if not rv.get("desc"):
                        d_val = (_field_ci(rv, "ChessSkillDesc") or _field_ci(rv, "SkillDesc") or 
                                 _field_ci(rv, "SpellSkillDesc") or _field_ci(rv, "SpellDesc") or 
                                 _field_ci(rv, "WeaponSkillDesc") or _field_ci(rv, "ChessStrategy") or 
                                 _field_ci(rv, "describe") or _field_ci(rv, "text") or "")
                        if d_val:
                            rv["desc"] = str(d_val)[:500]
                    
                    cleaned_rows[str(rk)] = rv
                # 3) 保存
                out_path = _os.path.join(DATA_DIR, out_file)
                with open(out_path, "w", encoding="utf-8") as f:
                    json.dump(cleaned_rows, f, ensure_ascii=False, indent=2)

                # v4.00★ 同时备份到用户指定的 skills.json 和 items.json
                if out_file in EXTRA_MAP:
                    extra_path = _os.path.join(DATA_DIR, EXTRA_MAP[out_file])
                    try:
                        with open(extra_path, "w", encoding="utf-8") as f:
                            json.dump(cleaned_rows, f, ensure_ascii=False, indent=2)
                    except Exception: _SIL.note("core/remote_ops.py:896", "with open(extra_path, 'w', encoding='utf-8') a")

                saved_stats[cat_key] = f"{len(cleaned_rows)}/{total_in}条 (剔除Season无={drop_no_season} 不可用={drop_unavail} 未入池={drop_not_pool} 非dict={drop_nondict})"
                all_tavern[cat_key] = {"path": out_path, "source": src_tag, "count": len(cleaned_rows),
                                       "drop": {"no_season": drop_no_season, "unavail": drop_unavail,
                                                "not_pool": drop_not_pool, "non_dict": drop_nondict}}
            if saved_stats:
                identity_filtered = 0  # 估算: tables里所有没进saved的非Season条目当身份局
                # 保存汇总
                try:
                    with open(_os.path.join(DATA_DIR, "tavern_summary.json"), "w", encoding="utf-8") as f:
                        json.dump({"saved": saved_stats, "detail": all_tavern,
                                   "ts": time.strftime("%Y-%m-%d %H:%M:%S")}, f, ensure_ascii=False, indent=2)
                except Exception:
                    _SIL.note("core/remote_ops.py:910", "with open(_os.path.join(DATA_DIR, 'tavern_summ")
                self.log("[配置抓取] ✓ 已分拣自走棋5表(彻底隔离身份局) → " + "; ".join(
                    f"{k.rsplit('Conf',1)[0]}={v}" for k, v in saved_stats.items()))
                d["__tavern_stats__"] = saved_stats
        except Exception as _e:
            self.log(f"[配置抓取] 分拣自走棋5表失败(继续原流程): {type(_e).__name__}: {_e}")
        return d

    # ---------- ID映射配置抓取(解密版) ----------
    def fetch_and_decrypt_cfg(self):
        self.log("[配置抓取·解密版] 正在启动...")
        temp_dir = Path(DATA_DIR) / "temp_sgs_decrypt"
        temp_dir.mkdir(parents=True, exist_ok=True)

        config_sgs_path = temp_dir / "Config.sgs"
        resc_wasm_path = temp_dir / "resc"
        
        # 1. 下载 Config.sgs 和 resc
        if not _download_file(CONFIG_SGS_URL, config_sgs_path, self.log):
            self.log("[配置抓取·解密版] Config.sgs 下载失败。")
            return None
        if not _download_file(RESC_WASM_URL, resc_wasm_path, self.log):
            self.log("[配置抓取·解密版] resc WASM 文件下载失败。")
            return None

        # 2. 解密 Config.sgs 并提取
        self.log("[配置抓取·解密版] 正在解密 Config.sgs 并提取内容...")
        extracted_config_path = None
        try:
            extracted_config_path = sgs_decrypt_util.decrypt_sgs_file(
                input_path=config_sgs_path,
                wasm_path=resc_wasm_path,
                extract_zip=True
            )
            if not extracted_config_path.is_dir():
                self.log(f"[配置抓取·解密版] Config.sgs 解密失败或未解压到目录: {extracted_config_path}")
                return None
            self.log(f"[配置抓取·解密版] Config.sgs 解密并提取成功到: {extracted_config_path}")
        except Exception as e:
            self.log(f"[配置抓取·解密版] 解密 Config.sgs 异常: {e}")
            # 清理临时文件
            import shutil
            try:
                shutil.rmtree(temp_dir)
            except Exception: _SIL.note("core/remote_ops.py:954", "shutil.rmtree(temp_dir)")
            return None

        # 3. 查找并解密 TavernChessConfig.sgs
        tavern_chess_sgs_path = None
        for root, _, files in os.walk(extracted_config_path):
            if "TavernChessConfig.sgs" in files:
                tavern_chess_sgs_path = Path(root) / "TavernChessConfig.sgs"
                break
        
        if not tavern_chess_sgs_path:
            self.log("[配置抓取·解密版] 在解密的 Config.sgs 中未找到 TavernChessConfig.sgs。")
            # 清理临时文件
            import shutil
            try:
                shutil.rmtree(temp_dir)
            except Exception: _SIL.note("core/remote_ops.py:970", "shutil.rmtree(temp_dir)")
            return None

        self.log("[配置抓取·解密版] 正在解密 TavernChessConfig.sgs...")
        tavern_chess_json_path = temp_dir / "TavernChessConfig.json"
        try:
            sgs_decrypt_util.decrypt_sgs_file(
                input_path=tavern_chess_sgs_path,
                wasm_path=resc_wasm_path,
                output_path=tavern_chess_json_path
            )
            self.log(f"[配置抓取·解密版] TavernChessConfig.sgs 解密成功到: {tavern_chess_json_path}")
        except Exception as e:
            self.log(f"[配置抓取·解密版] 解密 TavernChessConfig.sgs 异常: {e}")
            # 清理临时文件
            import shutil
            try:
                shutil.rmtree(temp_dir)
            except Exception: _SIL.note("core/remote_ops.py:988", "shutil.rmtree(temp_dir)")
            return None

        # 4. 解析 TavernChessConfig.json 并分拣保存
        full_config = {}
        # Start a new try block here for the entire processing and pushing logic
        try:
            with open(tavern_chess_json_path, "r", encoding="utf-8") as f:
                full_config = json.load(f)

            self.log("[配置抓取·解密版] 正在使用新逻辑处理 TavernChessConfig.json...")
            IDMAP.process_tavern_chess_config(full_config)

            # 获取处理后的棋子数据
            chess_data_map = IDMAP.get_tavern_chess_data()
            # 将字典转换为列表，适配JS端期望的格式 (假设JS端需要一个数组)
            # 并且将字典中的id也添加进去
            chess_data_list = []
            for id_str, item_data in chess_data_map.items():
                # 复制一份，避免修改原始缓存数据
                new_item_data = item_data.copy()
                new_item_data["id"] = int(id_str) # 确保id是数字类型
                chess_data_list.append(new_item_data)

            # TODO: 从用户配置或默认设置中获取永久白名单ID
            # 在这里，IDMAP.process_tavern_chess_config 已经更新了 IDMAP 中的数据。
            # 此时，只需要调用 _update_tampermonkey_config 将当前所有配置推送到油猴脚本。
            self._update_tampermonkey_config()

        except Exception as e:
            self.log(f"[配置抓取·解密版] 解析 TavernChessConfig.json 或推送数据失败: {e}")
            return None # Return None on error

        finally:
            # 清理临时文件
            import shutil
            try:
                shutil.rmtree(temp_dir)
                self.log(f"[配置抓取·解密版] 已清理临时目录: {temp_dir}")
            except Exception as e:
                self.log(f"[配置抓取·解密版] 清理临时目录失败: {e}")

        IDMAP.reset_namespace("lords", "chess", "cards", "skins", "lord_skills", "spells", "keywords", "weapons")
        self.log("[配置抓取·解密版] 已重置 IDMAP 命名空间。")

        self.log("[配置抓取·解密版] 完成。")
        return None # Return None as saved_stats is no longer generated here.

    def refetch_and_process_config(self):
        """从网页重新获取TavernChessConfig.json数据，并重新处理生成本地ID映射文件"""
        self.log("[数据管理] 开始重新获取并处理配置数据...")
        try:
            full_config = self.fetch_and_decrypt_cfg()
            if full_config:
                import id_map
                id_map.process_tavern_chess_config(full_config)
                self.log("[数据管理] 配置数据重新获取并处理成功。")
                return True
            else:
                self.log("[数据管理] 获取 TavernChessConfig.json 失败。", level="ERROR")
                return False
        except Exception as e:
            self.log(f"[数据管理] 重新获取或处理配置数据时发生异常: {e}", level="ERROR")
            return False

    def sync_id_map(self):
        """从油猴脚本同步ID映射库，主要是协议学习到的 ID。
        调用脚本侧的指令来获取。
        """
        self.log("[ID同步] 正在从油猴脚本获取已学习 ID...")
        try:
            # 调用脚本侧的指令来获取已学习的 ID
            res = self.send_recv("get_learned_ids", timeout=10.0)
            if not res:
                self.log("[ID同步] 失败: 脚本无响应或返回空。请检查油猴插件是否正常工作。")
                return None
            
            learned_ids_from_script = json.loads(res)
            if not isinstance(learned_ids_from_script, dict):
                self.log(f"[ID同步] 失败: 脚本返回数据格式不正确: {res[:100]}")
                return None
            
            added_count = IDMAP.learn_ids(learned_ids_from_script)
            self.log(f"[ID同步] 成功: 累积学习了 {added_count} 个新 ID 到 discovered_ids.json。")
            return {"status": "ok", "added_count": added_count}

        except Exception as e:
            self.log(f"[ID同步] 异常: {e.__class__.__name__}: {e}")
            return {"status": "error", "message": str(e)}

    def set_game_speed(self, speed):
        """设置游戏速度 — v4.11★ 统一走 fps_set 指令(油猟端无 window.setGameSpeed)"""
        try:
            spd = float(speed)
        except (TypeError, ValueError):
            self.log(f"[游戏速度] 速度值无效: {speed}")
            return False
        cid = self.send("fps_set", mode="custom", fps=0, speed=spd, auto_skip=False)
        res = self.wait_result(cid, 3.0)
        ok = bool(res) and not str(res).startswith("ERR")
        if ok:
            self.log(f"[游戏速度] 已设置为 {spd}x")
        else:
            self.log(f"[游戏速度] 设置失败: {res}")
        return ok

    def _update_tampermonkey_config(self):
        """内部方法：准备所有配置数据并推送到油猴脚本。

        v4.68★ 修了一个"从加上那天起就没成功过"的调用。
        原来这里拼的是 `window.updateChessData(...)` —— 那个全局名在 sgs_bridge.user.js 里
        【0 次出现】(真正的宿主对象叫 window.AutoBuyAndSell), 所以 eval 必然抛
        "window.updateChessData is not a function"。失败消息只是"[配置更新] 推送失败: ERR:...",
        看起来像偶发的连接问题, 于是没人追 —— 但它是每次都失败: 棋子库那份数据从来没进过页面。
        (随征/遣散/保留三个列表没受影响, 它们走的是另外三个 updateXxx, 名字是对的。)
        现在: 调 AutoBuyAndSell.updateChessData(规范位置), 并把配置表里的商店花费基准值
        一起带上(页面端拿不到 manager 活体 getter 时用它兜底)。
        """
        self.log("[配置更新] 正在准备并推送配置数据到油猴脚本...")
        try:
            chess_data_map = IDMAP.get_tavern_chess_data()
            chess_data_list = []
            for id_str, item_data in chess_data_map.items():
                new_item_data = item_data.copy()
                new_item_data["id"] = int(id_str)
                chess_data_list.append(new_item_data)

            # 商店花费基准值(ChessGlobalConf)。页面端只把它当兜底 —— 实时值优先读游戏 manager,
            #   因为那个值会被丈八蛇矛/刘焉/主公技能改。
            try:
                costs = csv_loader.shop_costs()
            except Exception as e:
                costs = {}
                self.log(f"[配置更新] 商店花费基准值读取失败(不影响推送): {e}")

            # v4.68★ 随征名单推【并集】(用户名单 ∪ 本季配置表 ChessBuffFollowUpConf):
            #   手工名单漏一个, 那张随征卡就会"静默不随征", 而用户根本查不到原因。
            _fu_all, _fu_extra = self._followup_ids_all()
            js_code = ("window.AutoBuyAndSell.updateChessData("
                       f"{json.dumps(chess_data_list, ensure_ascii=False)}, "
                       f"{json.dumps(_fu_all, ensure_ascii=False)}, "
                       f"{json.dumps(self._target_discard_ids, ensure_ascii=False)}, "
                       f"{json.dumps(self._permanent_keep_ids, ensure_ascii=False)}, "
                       f"{json.dumps(costs, ensure_ascii=False)})")
            cid = self.send("eval", js=js_code)
            res = self.wait_result(cid, 10.0)

            if res and res.lower() == "true":
                self.log(f"[配置更新] 配置数据成功推送到油猴脚本（棋子 {len(chess_data_list)} 条"
                         f"，随征 {len(_fu_all)}{('(含配置表补 %d)' % len(_fu_extra)) if _fu_extra else ''}"
                         f"，遣散 {len(self._target_discard_ids)}，"
                         f"保留 {len(self._permanent_keep_ids)}，花费基准 {costs or '无'}）。")
                return True
            else:
                self.log(f"[配置更新] 配置数据推送到油猴脚本失败: {res}")
                return False
        except Exception as e:
            self.log(f"[配置更新] 推送配置数据时发生异常: {e}")
            return False

    def _season_followup_ids(self):
        """本季配置表里的随征棋子ID(`ChessBuffFollowUpConf.ChessID`)。

        v4.68★ 为什么要有这个: 随征名单一直是【纯手工】维护的『随征卡牌ID』列表
        (`data`里的配置项, 界面上那张列表), 而配置表里其实有权威名单。
        手工名单的坑是"漏一个 = 那张随征卡被当成普通卡, 静默不随征" —— 用户只知道
        "随征有时候不灵", 完全查不到原因。这里把它做成【并集】: 用户名单(可自定义) ∪ 本季表,
        换赛季自动跟着新表走。读不到就只用用户名单, 不报错、不阻断。
        实测(S10): ChessBuffFollowUpConf 39 条, 项目里此前【零处引用】。
        """
        if self._season_fu_cache is not None:
            return self._season_fu_cache
        out = []
        try:
            cfg = csv_loader.season_config(None) or {}
            for r in (cfg.get("ChessBuffFollowUpConf") or []):
                # 同一张随征卡在表里会出现多条(每档 BS 数量一条), 要去重 —— 否则推送日志里
                # 的"补齐 N 个"会报成实际数量的好几倍, 让人以为名单被塞了一堆东西。
                sid = str((r or {}).get("ChessID") or "") if isinstance(r, dict) else ""
                if sid and sid not in out:
                    out.append(sid)
        except Exception as e:
            self.log(f"[随征配置] 本季随征名单读不到(只用你手动配的那份): {e}")
        self._season_fu_cache = out
        return out

    def _followup_ids_all(self):
        """用户名单 + 本季配置表补齐 → (全部ID, 补出来的那几个)。"""
        base = [str(i) for i in (self._followup_ids or [])]
        extra = [i for i in self._season_followup_ids() if i not in base]
        return base + extra, extra

    def update_followup_ids(self, ids):
        """v4.13★ 更新随征ID列表 — 调油猟端 AutoBuyAndSell.updateFollowupIds"""
        try:
            base = [str(i) for i in (ids or [])]
            self._followup_ids = base          # 记下来, 推送时(update_chess_data)也用同一份口径
            all_ids, extra = self._followup_ids_all()
            ids_str = json.dumps(all_ids)
            self.send_recv("eval", js=f"window.AutoBuyAndSell.updateFollowupIds({ids_str});", timeout=2.0)
            # 日志必须说清"多出来的是配置表补的": 否则用户会以为自己的名单被助手改了
            self.log("[随征配置] 已推送 %d 个ID%s"
                     % (len(all_ids),
                        ("(其中 %d 个由本季配置表补齐: %s%s)"
                         % (len(extra), ",".join(extra[:6]), "..." if len(extra) > 6 else ""))
                        if extra else ""))
        except Exception as e:
            self.log(f"[随征配置] 推送失败: {e}")

    def update_followup_target(self, name):
        """v4.83★ 随征"给谁"(按名字在阵上找) — 写页面 window.FOLLOWUP_TARGET。

        Shift+3 以前写死给【阵上位置0】; 填了名字就按名字找那个人。留空 = 老行为。
        """
        try:
            s = str(name or "").strip()
            self.send_recv("eval", js=f"window.FOLLOWUP_TARGET={json.dumps(s)};", timeout=2.0)
            self.log("[随征目标] 已推送: " + (s or "(留空 = 阵上位置0, 老行为)"))
        except Exception as e:
            self.log(f"[随征目标] 推送失败: {e}")

    def set_net_probe(self, on):
        """v4.145★ 协议探测器开关 —— 给「没抓过发包结构的锦囊」取证用(如釜底抽薪)。

        开了之后页面端把【所有】协议包都存完整结构(平时只存 150 字摘要, 且同一个协议
        名只记首次样本)。为什么要它: 像釜底抽薪这种"协议名 CReqChessUseSpell 早就见过、
        但这张锦囊可能多带一个势力字段"的情况, 多出来的字段按老逻辑永远抓不到。

        取证流程: 开 → 到游戏里【手动】点那张锦囊、手动选目标 → 关 → 看
        data\\net_samples.json 或从 dev\\_match_review.py 的流水里翻当时的包。
        ⚠ 只开几分钟, 别一直开着(包多、占内存)。
        """
        try:
            flag = "true" if bool(on) else "false"
            self.send_recv("eval", js="window.__sgsProbeAll = %s;" % flag, timeout=2.0)
            self.log("[协议探测] 已%s —— %s" % (
                "开启" if on else "关闭",
                "现在去游戏里手动点那张锦囊, 完事记得关掉"
                if on else "恢复常态(只存摘要 + 每协议首次样本)"))
            return True
        except Exception as e:
            self.log("[协议探测] 推送失败: %s" % e)
            return False

    def check_sent(self, name_kw, since_ms, timeout=1.5, poll=0.08):
        """v4.145★ 问页面端: since_ms 之后【真的】发出过名字含 name_kw 的包吗。

        为什么要这个方法: 游戏那批 Req* 会静默 return ——
            ReqShopRefreshChess(e=!1){ if(!e && !this.CanOperate) return; ... }
        被门禁吞掉时方法照样是"调用成功", 但我们等的回包永远不会来, 只能干等超时。
        有了它就多了个一句话的判断: 调完倩 Socket 之后问一句"出去没有"。

        用法:
            t0 = int(time.time() * 1000)
            ... 正常调用 ...
            if not ops.check_sent('ShopRefreshChess', t0):
                log('已被门禁吞掉, 别再等回包了')
        """
        # ★ 先确认探针装了没有。页面端跑的还是旧脚本时拿不到 __sgsOutLog,
        #   这时必须当作"无法判定"(返回 True 维持原行为) —— 否则用户刚更新完助手、
        #   页面脚本还没刷新, 每一步都会被误判成操作失败。
        try:
            _r = self.send_recv("eval", js="String(!!window.__sgsOutLogReady)", timeout=1.0)
            if str(_r).strip().lower() not in ("true", "1"):
                return True
        except Exception:
            return True

        deadline = time.time() + timeout
        while time.time() < deadline:
            js = ("(function(){try{var L=window.__sgsOutLog||[];var out=[];"
                  "for(var i=0;i<L.length;i++){var it=L[i];"
                  "if(it.ts<=%d)continue;"
                  "if(String(it.p).indexOf('%s')>=0)out.push(it.p);}"
                  "return JSON.stringify(out);}catch(e){return '[]';}})()"
                  % (int(since_ms), name_kw))
            try:
                res = self.send_recv("eval", js=js, timeout=1.0)
            except Exception:
                res = None
            try:
                if res and json.loads(str(res)):
                    return True
            except Exception:
                if res and str(res).strip() not in ("[]", '""', "null", ""):
                    return True
            time.sleep(poll)
        return False

    def update_timing(self, obj):
        """v4.85★ 节奏/延迟表 — 调页面端 AutoBuyAndSell.applyTiming。

        这些毫秒数以前【写死在脚本里】(遣散武将每张 160、轮询片 100/150/200、手牌补刷
        150/400…), 想快只能整体开剧本倍速, 而倍速会把"等服务器结算"一起压掉 → 日志常见
        "包发了但没生效"。现在逐项下发, 只覆盖传了的键, 留空的那项脚本继续用默认值。

        obj 为空 = 恢复脚本默认(调 resetTiming)。
        """
        try:
            if not obj:
                self.send_recv("eval", js="window.AutoBuyAndSell.resetTiming();", timeout=2.0)
                self.log("[节奏] 已恢复脚本默认延迟(全部项)")
                return 0
            payload = {}
            for k, v in obj.items():
                try:
                    payload[str(k)] = max(0, min(60000, int(v)))
                except (TypeError, ValueError):
                    continue
            if not payload:
                return 0
            self.send_recv("eval",
                           js="window.AutoBuyAndSell.applyTiming(%s);" % json.dumps(payload),
                           timeout=2.0)
            self.log("[节奏] 已推送 %d 项: %s" % (len(payload),
                     "、".join("%s=%dms" % (k, v) for k, v in payload.items())))
            return len(payload)
        except Exception as e:
            self.log(f"[节奏] 推送失败: {e}")
            return 0

    def update_fast_mode(self, on):
        """v4.145★ 【极速】硬开关 — 设页面端 window.SGS_FAST_MODE(见脚本 sgsFast())。

        和「节奏」页那些毫秒数的区别 —— 别混为一谈:
          节奏 = 把等待【调小】(随征两张之间 300 → 150 之类), 该等的照样等;
          极速 = 该等的【干脆不等】: 刷新后不核对商店(每轮那 1.5 秒全省)、批量上阵不给
                间隔, 而且不再因为 CanOperate=false / 不在招募阶段就【拒绝发请求】。

        为什么要有第二档: 用户原话是"不要延迟, 直接给就行, 不需要考虑服务器是否响应
        什么的" —— 光把毫秒数调小满足不了, 因为"发完要不要等回应"这件事本身没得调。

        ⚠ 代价必须说清: 请求连发、不等落地, 服务器忙的时候会【漏掉个把操作】(发了没生效)。
        所以它是开关, 不是常量: 想稳就关(助手「节奏」页点「标准」/「保守」会自动关掉)。
        """
        try:
            flag = "true" if bool(on) else "false"
            self.send_recv("eval", js="window.SGS_FAST_MODE = %s;" % flag, timeout=2.0)
            self.log("[极速] 已%s —— %s" % (
                "开启" if on else "关闭",
                "请求连发不等回应, 服务器忙时可能漏个把操作"
                if on else "恢复逐条核对(慢一些, 但每一步都会确认落地)"))
            return True
        except Exception as e:
            self.log(f"[极速] 推送失败: {e}")
            return False

    def update_zero_wait(self, on):
        """v4.163★ 【内部零等待】总闸 — 设页面端 window.SGS_ZERO_WAIT(见脚本 sgsZW/sgsZeroWait)。

        用户原话: "每个步骤里面不要加、在外面加我们写的那个等待多少毫秒这样进行延迟"。
        所以这一档的语义是: 步骤【内部】所有 sleep 与"最多等多久"上限一律归 0, 发完请求立刻
        返回; 节奏完全由剧本里自己插的「等待 N 毫秒」(op=wait) 步骤来给。

        与 update_fast_mode(极速) 的区别 —— 别混:
          · 零等待 = 我们【自己加的】等待归 0, 门禁照旧、请求照发;
          · 极速   = 不等服务器【还硬过】CanOperate / 阶段门禁, 漏操作的风险来自那里。
        两者可单独开。脚本端还保证: 用户显式写的等待步骤(等待 / 等到条件成立 / 等到阶段 /
        等金币 / 暂停等我确认)【不受零等待影响】—— 否则"外面加的延迟"也会被一起抹掉。
        """
        try:
            flag = "true" if bool(on) else "false"
            self.send_recv("eval", js="window.SGS_ZERO_WAIT = %s;" % flag, timeout=2.0)
            self.log("[零等待] 已%s —— %s" % (
                "开启" if on else "关闭",
                "步骤内部一律不等, 延迟请写在剧本的「等待 N 毫秒」步骤上"
                if on else "步骤恢复各自的等待(上阵/遣散/刷新/挑牌/用锦囊各等各的)"))
            return True
        except Exception as e:
            self.log(f"[零等待] 推送失败: {e}")
            return False

    def update_global_speed(self, speed):
        """v4.185★ 【全局操作倍率】— 设页面端 window.__sgsGlobalSpeed(见脚本 _sleepJs)。

        与「剧本倍率」「游戏加速(Laya timer)」都不同:
          · 剧本倍率只在跑剧本时压缩等待;
          · 游戏加速改的是游戏动画时间轴, 不动脚本自己的等待;
          · 全局操作倍率 = 无论手动/快捷键/自动招募/剧本, 脚本内部所有等待都 ÷ 倍率
            (与剧本倍率相乘, 合计夹到 0.1~100)。
        代价同剧本倍率: 压太狠会「包发了但没生效」—— 默认 1(不压)。
        同时写 localStorage, 没连助手刷新页面也保持。
        """
        try:
            spd = float(speed)
        except (TypeError, ValueError):
            self.log(f"[操作倍率] 值无效: {speed}")
            return False
        spd = max(0.1, min(100.0, spd))
        js = ("window.__sgsGlobalSpeed = %s; try{localStorage.setItem('__sgs_global_speed','%s');}catch(e){}"
              % (spd, spd))
        self.send_recv("eval", js=js, timeout=2.0)
        self.log(f"[操作倍率] 已设为 {spd}x (手动/快捷键/自动招募/剧本的等待都会 ÷{spd}; 与剧本倍率相乘)")
        return True

    def update_auto_follow_up(self, on):
        """v4.185★ 【自动随征】后台引擎 — 真的 start/stop 页面里的自动随征循环(见脚本 _sgsSetAutoFollowUp)。

        开 = 页面 setInterval 轮询手牌, 一出现可随征的卡就自动随征给「随征目标」指定的人;
        关 = 停掉循环。手动入口(Shift+3 / 剧本「随征」「自动随征」)不受影响, 永远能调。
        """
        try:
            flag = "true" if bool(on) else "false"
            self.send_recv("eval", js="window.__sgsSetAutoFollowUp(%s);" % flag, timeout=2.0)
            self.log("[自动随征] 后台引擎已%s —— %s" % (
                "开启" if on else "关闭",
                "手牌出现随征卡会自动随征给「随征目标」指定的人"
                if on else "已停掉后台自动随征循环(手动随征仍可)"))
            return True
        except Exception as e:
            self.log(f"[自动随征] 推送失败: {e}")
            return False

    def update_auto_spell(self, on):
        """v4.186★ 【自动锦囊】后台引擎 — 真的 start/stop 页面里的自动锦囊循环(见脚本 _sgsSetAutoSpell)。

        开 = 页面 setInterval 轮询手牌, 一出现可自动用的锦囊就自动用掉(复用「用尽锦囊」逻辑);
        关 = 停掉循环。手动入口(锦囊步骤 / 快捷键)不受影响, 永远能调。
        """
        try:
            flag = "true" if bool(on) else "false"
            self.send_recv("eval", js="window.__sgsSetAutoSpell(%s);" % flag, timeout=2.0)
            self.log("[自动锦囊] 后台引擎已%s —— %s" % (
                "开启" if on else "关闭",
                "手牌出现可自动用的锦囊会自动用掉(避开默认黑名单里的 12 张)"
                if on else "已停掉后台自动锦囊循环(手动用锦囊仍可)"))
            return True
        except Exception as e:
            self.log(f"[自动锦囊] 推送失败: {e}")
            return False

    def update_auto_dismiss(self, on, policy=None):
        """v4.187★ 【自动遣散】后台引擎 — 真的 start/stop 页面里的自动遣散循环(见脚本 _sgsSetAutoDismiss)。

        开 = 页面 setInterval 轮询手牌, 按 白名单/黑名单/星级价值策略 自动遣散; 关 = 停掉循环。
        手动 遣散(全遣散/F7) 不受影响, 永远能调。policy 可选, 形如 {"max_star":1,"min_cost":1}。
        """
        try:
            flag = "true" if bool(on) else "false"
            self.send_recv("eval", js="window.__sgsSetAutoDismiss(%s);" % flag, timeout=2.0)
            if policy is not None:
                pol = {"maxStar": int((policy or {}).get("max_star", 1)),
                       "minCost": int((policy or {}).get("min_cost", 1))}
                self.send_recv("eval",
                               js="window.__SGS_AUTO_DISMISS_POLICY = %s;" % json.dumps(pol),
                               timeout=2.0)
                self.log("[自动遣散] 策略: 星≤%d 且 价值≥%d" % (pol["maxStar"], pol["minCost"]))
            self.log("[自动遣散] 后台引擎已%s" % ("开启" if on else "关闭"))
            return True
        except Exception as e:
            self.log(f"[自动遣散] 推送失败: {e}")
            return False

    def update_auto_dismiss_policy(self, policy):
        """v4.187★ 只更新自动遣散的星级/价值策略(不开关引擎)。配置改了 max_star/min_cost 时推一次。"""
        try:
            pol = {"maxStar": int((policy or {}).get("max_star", 1)),
                   "minCost": int((policy or {}).get("min_cost", 1))}
            self.send_recv("eval", js="window.__SGS_AUTO_DISMISS_POLICY = %s;" % json.dumps(pol), timeout=2.0)
            self.log("[自动遣散] 策略已更新: 星≤%d 且 价值≥%d" % (pol["maxStar"], pol["minCost"]))
            return True
        except Exception as e:
            self.log(f"[自动遣散] 策略推送失败: {e}")
            return False

    def update_render(self, cfg):
        """v4.182★ 【性能】页 — 调页面端 window.SGS_ApplyRender({off,badge,hand,skin})。

        桥接脚本在游戏页上的 DOM 重绘闸门(见脚本 sgsRenderGate):
          off   = 主开关「取消渲染」—— 角标/手牌区/换肤全部暂停, 想恢复把 off 关掉即可;
          badge = 暂停角标/提示条刷新;
          hand  = 暂停手牌区重建(并入原 handNoRebuild 逻辑);
          skin  = 暂停换肤动画与资源注入轮询。
        stats 键桥不认(统计面板/录制事件表是助手端 tkinter 自己画的, 由助手端本地开关管),
        这里一并传过去无副作用。cfg 为空/None = 全部恢复默认(off/badge/hand/skin 全 false)。
        """
        try:
            payload = {}
            if cfg:
                for k in ("off", "badge", "hand", "skin", "stats"):
                    if k in cfg:
                        payload[k] = bool(cfg[k])
        except Exception as e:
            self.log(f"[性能] 配置解析失败: {e}")
            return False

        # v4.182b★ 先探一下游戏页是不是【新版脚本】再发: 装的是旧脚本时 window.SGS_ApplyRender
        #   根本不存在, eval 会抛 "... is not a function" —— 只靠 try/except 兜的话, 用户看到的
        #   就是"勾了没反应", 这正是本项目抬 MIN_SCRIPT_VER 要防的"看着生效其实没生效"。
        #   所以这里主动查一次, 查到旧脚本就明确告诉用户去更新。
        if not self.connected():
            self.log("[性能] 游戏页没连上, 已保存到 config.json(下次连上自动带过去)")
            return False
        try:
            probe = self.send_recv(
                "eval",
                js="(typeof window.SGS_ApplyRender === 'function') ? 'new' : 'old'",
                timeout=2.0)
        except Exception as e:
            self.log(f"[性能] 探测游戏页脚本版本失败: {e}")
            return False
        if str(probe).strip().strip('"\'').lower() != "new":
            self.log("[性能] ⚠ 游戏页脚本过旧(没有 SGS_ApplyRender), 渲染闸门【不会生效】"
                     " —— 请到『资源替换』页点「更新全能扩展脚本」(或重装 bridge/sgs_bridge.user.js)"
                     ", 等 ≤20 秒脚本取走新的一版再勾。", level="WARN")
            return False

        try:
            self.send_recv("eval",
                           js="window.SGS_ApplyRender(%s);" % json.dumps(payload),
                           timeout=2.0)
            # 回读一次确认真的写进去了 —— 免得"发了但页面那边没接住"又被当成生效。
            back = self.send_recv("eval",
                                  js="JSON.stringify(window.SGS_RENDER||{})",
                                  timeout=2.0)
        except Exception as e:
            self.log(f"[性能] 推送失败: {e}")
            return False

        parts = [] if not payload else ["%s=%s" % (k, payload[k]) for k in ("off", "badge", "hand", "skin", "stats") if k in payload]
        self.log("[性能] 已应用渲染闸门到游戏页: " + (", ".join(parts) if parts else "全部恢复默认")
                 + " (页面回读: %s)" % (str(back)[:160] if back else "(空)"))
        return True

    def update_spell_priority(self, names):
        """v4.82★ 锦囊优先名单(顺序即优先级) — 调页面端 updateSpellPriority。

        不推送时页面按"识别顺序"取(手牌里最左那张锦囊); 推送后 Shift+2 与
        __sgsHotkey.useRightmostSpell() 都按这份名单挑锦囊 —— 不再是"最右边那张"。
        """
        try:
            arr = [str(x).strip() for x in (names or []) if str(x).strip()]
            self.send_recv("eval",
                           js=f"window.AutoBuyAndSell.updateSpellPriority({json.dumps(arr)});",
                           timeout=2.0)
            self.log("[锦囊优先名单] 已推送 %d 个: %s%s"
                     % (len(arr), "、".join(arr[:6]), "..." if len(arr) > 6 else ""))
        except Exception as e:
            self.log(f"[锦囊优先名单] 推送失败: {e}")

    def update_spell_blacklist(self, names):
        """v4.105★ 锦囊黑名单 — 调页面端 updateSpellBlacklist。

        名单里的锦囊【不自动使用】(Shift+2 / 剧本「用锦囊」/ 陆逊小抄 三条路都遵守),
        但照常识别(面板上看得见它在手上第几位), 「遣散锦囊」那一步照卖 ——
        作者原话:"记得扔掉斗转"、"釜底抽薪目前无法使用, 记得扔"。
        传空列表 = 一张都不拦(全部解禁)。
        """
        try:
            arr = [str(x).strip() for x in (names or []) if str(x).strip()]
            self.send_recv("eval",
                           js=f"window.AutoBuyAndSell.updateSpellBlacklist({json.dumps(arr)});",
                           timeout=2.0)
            self.log("[锦囊黑名单] 已推送 %d 个: %s%s"
                     % (len(arr), "、".join(arr[:8]), "..." if len(arr) > 8 else ""))
        except Exception as e:
            self.log(f"[锦囊黑名单] 推送失败: {e}")

    def update_lineup_plan(self, plan):
        """v4.106★ 把阵容表推给页面端 → window.SGS_LINEUP_PLAN(供剧本「按推荐转阵」步骤用)。

        脚本端【不内置】任何阵容数据 —— 35 组四势力 → 后期思路那张表在助手这边(data/*.json),
        换季换表也只改助手, 不用更新油猴脚本。没推过就跑 lineupPlan 会明确提示"先去助手点一下"。
        """
        try:
            if not plan:
                self.log("[阵容表] 没东西可推(先勾四个势力)")
                return
            js = "window.SGS_LINEUP_PLAN=" + json.dumps(plan, ensure_ascii=False) \
                 + ";console.info('[阵容表] 已收到助手下发的阵容数据');"
            self.send_recv("eval", js=js, timeout=2.0)
            self.log("[阵容表] 已推送到游戏页: %s（有毒 %s / 无毒 %s）"
                     % (plan.get("name", "?"),
                        "/".join((plan.get("poison") or {}).get("cores") or []) or "-",
                        "/".join((plan.get("clean") or {}).get("cores") or []) or "-"))
        except Exception as e:
            self.log(f"[阵容表] 推送失败: {e}")

    def update_followup_priority(self, names):
        """v4.82★ 随征优先名单(顺序即优先级) — 调页面端 updateFollowupPriority。

        与「锦囊优先名单」同一套: 不推送就按识别顺序取, 推送后 Shift+3 与剧本「随征」
        (步骤里没写"用哪张随征卡"时)都按这份名单挑随征卡。
        """
        try:
            arr = [str(x).strip() for x in (names or []) if str(x).strip()]
            self.send_recv("eval",
                           js=f"window.AutoBuyAndSell.updateFollowupPriority({json.dumps(arr)});",
                           timeout=2.0)
            self.log("[随征优先名单] 已推送 %d 个: %s%s"
                     % (len(arr), "、".join(arr[:6]), "..." if len(arr) > 6 else ""))
        except Exception as e:
            self.log(f"[随征优先名单] 推送失败: {e}")

    def update_target_discard_ids(self, ids):
        """v4.11★ 更新遣散目标列表 — 调油猟端 updateDiscardTargets"""
        try:
            ids_str = json.dumps([str(i) for i in (ids or [])])
            self.send_recv("eval", js=f"window.AutoBuyAndSell.updateDiscardTargets({ids_str});", timeout=2.0)
            self.log(f"[遣散配置] 已推送 {len(ids or [])} 个ID")
        except Exception as e:
            self.log(f"[遣散配置] 推送失败: {e}")

    def get_shop_goods(self) -> list:
        """从油猴脚本获取商店物品列表。

        v4.22★ 注意: 本方法当前【没有任何调用点】(同理见下方 get_hand_chess /
        get_lineup)。现行取数入口是 get_full_state(), 由 protocol_buy_targets()
        (即 F6) 调用。此处保留备用。
        与已删除的旧版重复实现相比, 本版优点: 用 send_recv 一次完成 +
        JSON.stringify 确保回传字符串 + except Exception 全兜底(不会抛给调用方)。
        """
        self.log("[商店] 正在从脚本获取商店物品...")
        try:
            res = self.send_recv("eval", js="JSON.stringify(window.AutoBuyAndSell.getShopGoods())", timeout=2.0)
            if res:
                return json.loads(res)
            return []
        except Exception as e:
            self.log(f"[商店] 获取商店物品失败: {e}")
            return []

    def find_targets_in_shop(self, target_id: int) -> list:
        """从油猴脚本查找商店中是否存在目标卡牌"""
        self.log(f"[商店] 正在从脚本查找目标卡牌 {target_id}...")
        try:
            js_code = f"JSON.stringify(window.AutoBuyAndSell.findTargetsInShop({target_id}))"
            res = self.send_recv("eval", js=js_code, timeout=2.0)
            if res:
                return json.loads(res)
            return []
        except Exception as e:
            self.log(f"[商店] 查找目标卡牌失败: {e}")
            return []

    def refresh_shop_from_js(self):
        """从油猴脚本触发商店刷新"""
        self.log("[商店] 正在从脚本刷新商店...")
        try:
            res = self.send_recv("eval", js="window.AutoBuyAndSell.refreshShop()", timeout=2.0)
            if res and res.lower() == "true":
                self.log("[商店] 商店刷新成功。")
                return True
            else:
                self.log(f"[商店] 商店刷新失败: {res}")
                return False
        except Exception as e:
            self.log(f"[商店] 商店刷新异常: {e}")
            return False

    def trigger_discard_target_chess_ids(self):
        """触发油猴脚本中一键遣散指定卡牌的功能 (Shift+4 / F7).

        v4.21★ 改成可观测: 以前只判脚本返回的 true/false, 所以 F7"没反应"时完全分不清是
                 「没匹配到目标」还是「手牌读成空数组」还是「阶段不对」——日志里只有一句
                 "触发成功", 但游戏里什么都没发生。
                 现在脚本回传 JSON {ok, phase, handSrc, handN, matched, sent, reason},
                 这里直接把它打成一行中文诊断。
        """
        self.log("[遣散] 正在触发油猴脚本中的一键遣散功能...")
        try:
            res = self.send_recv("eval",
                                 js="JSON.stringify(window.AutoBuyAndSell.discardTargets())",
                                 timeout=5.0)
            detail = None
            try:
                detail = json.loads(res) if res else None
            except Exception:
                detail = None
            if isinstance(detail, dict):
                if detail.get("reason"):
                    self.log(f"[遣散] 未执行: {detail['reason']} "
                             f"(阶段={detail.get('phase')} 手牌源={detail.get('handSrc')} "
                             f"手牌数={detail.get('handN')} 匹配={detail.get('matched')})")
                    return False
                if detail.get("ok"):
                    self.log(f"[遣散] 已发出 {detail.get('sent')} 个遣散请求 "
                             f"(手牌源={detail.get('handSrc')} 手牌数={detail.get('handN')} "
                             f"匹配={detail.get('matched')})")
                    return True
            # 脚本还是旧版(只回 true/false)或返回解析失败时, 退回原判断
            if res and str(res).lower() == "true":
                self.log("[遣散] 一键遣散功能触发成功。")
                return True
            self.log(f"[遣散] 一键遣散功能触发失败: {res}")
            return False
        except Exception as e:
            self.log(f"[遣散] 触发一键遣散功能异常: {e}")
            return False

    def discard_all_hand(self):
        """v4.10★ 全遣散手牌 (跳过永久保留列表)"""
        self.log("[全遣散] 正在触发全遣散手牌...")
        try:
            res = self.send_recv("eval", js="window.AutoBuyAndSell.discardAllHand()", timeout=5.0)
            if res and res.lower() == "true":
                self.log("[全遣散] 全遣散手牌触发成功。")
                return True
            else:
                self.log(f"[全遣散] 全遣散手牌触发失败: {res}")
                return False
        except Exception as e:
            self.log(f"[全遣散] 全遣散手牌异常: {e}")
            return False

    def update_permanent_keep_ids(self, ids, expand_quality=True):
        """v4.10★ 推送永久保留列表到油猴端

        v4.89★ expand_quality: 把每个 ID 展开成【同名的所有品质】(普通+金)。
          不展开的后果: 普通和金是同名不同 ID(尾数 1/2), 名单里有『孙策(普通)』时
          金孙策不在名单里 → 一键遣散照样把金孙策卖了。上游 sgscodex 那份
          『一键遣散保留指定卡』就是专门修这条("自动保留同名将的所有品质(如金卡)")。
        """
        try:
            raw = [str(i) for i in (ids or [])]
            if expand_quality:
                try:
                    raw = IDMAP.expand_qualities(raw)
                except Exception as _qe:
                    # 展开失败就按原样推, 不能因为这一步让保留名单整个推不出去。
                    # 但必须留痕(静态自检禁静默 except: pass)——不记下来, 用户只会看到
                    # "金卡还是被遣散了"却查不到原因。
                    self.log(f"[永久保留] 同名品质展开失败(按原样推送): {_qe}")
            self._permanent_keep_ids = list(raw)
            ids_str = json.dumps(raw)
            self.send_recv("eval", js=f"window.AutoBuyAndSell.updatePermanentKeep({ids_str});", timeout=2.0)
            self.log(f"[永久保留] 已推送 {len(raw)} 个ID到油猴端"
                     + (f"(原 {len(list(ids or []))} 个, 已自动带上同名卡的其它品质)"
                        if expand_quality and len(raw) != len(list(ids or [])) else ""))
        except Exception as e:
            self.log(f"[永久保留] 推送失败: {e}")

    # ---------------- v4.54★ 快捷键统一入口(『快捷键』页的按钮走这里) ----------------
    def hotkey(self, name, arg=None, timeout=8.0):
        """调用游戏页【快捷键中心】window.__sgsHotkey.<name>(arg), 返回脚本回传文本。

        为什么要多这一层: 以前 App 用按钮代替键盘, 走的是 window.useRightmostSpell() /
        window.discardRightmostHand() 这类调用 —— 可这些函数一直只是油猴 IIFE 里的局部函数,
        window 上从来没有过, 所以"Python 端点按钮触发快捷键"必然失败(日志只会写"触发失败")。
        油猴 v4.54 起统一暴露到 window.__sgsHotkey(名字与快捷键一一对应)。
        返回: 脚本回传的字符串(成功多为 'true'/'ok'); 未连接/超时返回 None。
        """
        fn = str(name or "").strip()
        if not fn.isidentifier():
            return "ERR:非法的快捷键名 %r" % (name,)
        arg_js = ""
        if arg is not None:
            if isinstance(arg, (int, float, bool, dict, list)):
                arg_js = json.dumps(arg)
            else:
                arg_js = "'" + str(arg).replace("\\", "\\\\").replace("'", "\\'") + "'"
        js = ("(function(){try{var h=window.__sgsHotkey;"
              "if(!h)return 'ERR:游戏页脚本<v4.54(未注入__sgsHotkey, 请点一键注入更新)';"
              "if(typeof h.%s!=='function')return 'ERR:没有这个快捷键 %s';"
              "var r=h.%s(%s);return (r===undefined)?'ok':String(r);"
              "}catch(e){return 'ERR:'+e.message;}})()") % (fn, fn, fn, arg_js)
        try:
            return self.send_recv("eval", js=js, timeout=timeout)
        except Exception as e:
            self.log(f"[快捷键] {fn} 调用异常: {e}")
            return None

    def run_console_js(self, code, timeout=8.0):
        """把一段 JS 原样丢进游戏页执行 —— 等价于用户在游戏页 F12 控制台里直接敲。

        用户原话: "可以直接发送消息到 F12 后的那个控制台内容, 直接发指令, 例如 setCoin(100)"。
        所以这里【不做任何包装/白名单】: 用户写什么就 eval 什么, 脚本的返回值原样带回。
        注意: 脚本端执行的是 `eval(c.code)`, 所以适合写【表达式】(如 setCoin(100)),
              写成 `return 1` 会被 JS 判为 "Illegal return statement"。
        """
        src = str(code or "").strip()
        if not src:
            return "ERR:空指令"
        try:
            return self.send_recv("eval", js=src, timeout=timeout)
        except Exception as e:
            self.log(f"[控制台] 指令执行异常: {e}")
            return None

    def trigger_use_rightmost_spell(self):
        """触发游戏页"使用最右侧锦囊" (Shift+2)

        v4.54★ 改走 hotkey(): 原来的 window.useRightmostSpell() 在 window 上根本不存在,
                 所以这个按钮从加上那天起就没成功过(详见 hotkey 的说明)。
        """
        self.log("[锦囊] 正在触发游戏页中使用最右侧锦囊功能...")
        try:
            res = self.hotkey("useRightmostSpell")
            if res and str(res).strip().lower() == "true":
                self.log("[锦囊] 使用最右侧锦囊功能触发成功。")
                return True
            else:
                self.log(f"[锦囊] 使用最右侧锦囊功能触发失败: {res}")
                return False
        except Exception as e:
            self.log(f"[锦囊] 触发使用最右侧锦囊功能异常: {e}")
            return False

    def trigger_deploy_rightmost_card(self):
        """触发游戏页"上阵手牌最右侧卡牌" (Alt+0)

        v4.54★ 同 turn: 原来调的 window.deployRightmostCard() 在 window 上也不存在, 一并改走 hotkey()。
        """
        self.log("[上阵] 正在触发游戏页中上阵手牌最右侧卡牌功能...")
        try:
            res = self.hotkey("deployRightmostCard")
            if res and str(res).strip().lower() == "true":
                self.log("[上阵] 上阵手牌最右侧卡牌功能触发成功。")
                return True
            else:
                self.log(f"[上阵] 上阵手牌最右侧卡牌功能触发失败: {res}")
                return False
        except Exception as e:
            self.log(f"[上阵] 触发上阵手牌最右侧卡牌功能异常: {e}")
            return False

    def req_chess_follow_up(self, target_goods_id: int, follow_up_goods_id: int):
        """请求随征：将目标棋子随征给另一个棋子。"""
        self.log(f"[随征] 正在请求随征 {target_goods_id} 到 {follow_up_goods_id}...")
        res = self.send_recv("req_chess_follow_up", {
            "targetGoodsID": target_goods_id,
            "followUpGoodsID": follow_up_goods_id
        }, timeout=5.0)
        if res == "ok":
            self.log("[随征] 请求成功。")
            return True
        else:
            self.log(f"[随征] 请求失败: {res}")
            return False

    def req_chess_use_spell(self, spell_goods_id: int, target_goods_ids: list[int] = None):
        """请求使用锦囊：使用指定 goodsID 的锦囊，可指定目标。"""
        targets_str = f" 目标: {target_goods_ids}" if target_goods_ids else ""
        self.log(f"[使用锦囊] 正在请求使用锦囊 {spell_goods_id}{targets_str}...")
        payload = {"spellGoodsID": spell_goods_id}
        if target_goods_ids:
            payload["targetGoodsIDs"] = target_goods_ids
        
        res = self.send_recv("req_chess_use_spell", payload, timeout=5.0)
        if res == "ok":
            self.log("[使用锦囊] 请求成功。")
            return True
        else:
            self.log(f"[使用锦囊] 请求失败: {res}")
            return False

    def req_shop_recycle_chess(self, goods_id: int):
        """请求遣散棋子：遣散指定 goodsID 的棋子。"""
        self.log(f"[遣散棋子] 正在请求遣散棋子 {goods_id}...")
        res = self.send_recv("req_shop_recycle_chess", {
            "goodsID": goods_id
        }, timeout=5.0)
        if res == "ok":
            self.log("[遣散棋子] 请求成功。")
            return True
        else:
            self.log(f"[遣散棋子] 请求失败: {res}")
            return False

    def req_chess_line_up(self, lineup: list[int]):
        """请求上阵编排：按照指定的 goodsID 列表进行上阵编排（7个位置，空位填0）。"""
        self.log(f"[上阵编排] 正在请求上阵编排: {lineup}...")
        res = self.send_recv("req_chess_line_up", {
            "lineup": lineup
        }, timeout=5.0)
        if res == "ok":
            self.log("[上阵编排] 请求成功。")
            return True
        else:
            self.log(f"[上阵编排] 请求失败: {res}")
            return False

    # ---------- 主公技能 (v4.46★) ----------
    # 用户需求: "再开一个 tab 页能够使用主公技能推送到现在的网页里 —— 我想在对局中使用
    #            别的主公技能, 像 1053 这种可以挑选一个主公技能使用。被动技能也能用吗?"
    #
    # 原理(反编译源码 参考_sgs_forward_looking-main): 主公技能在客户端只有一条施放路径
    #     TavernChessZhuGongBaseSkill.Use()
    #       → TavernChessGameContext.UseSkill(TavernChessClientSkillEnum.ZHUGONG, SkillID)
    #       → manager.ReqChessUseSkill(SkillID)
    #       → SendProto(CMSG_CREQCHESSUSESKILL,
    #                   {tableID, skillID, chessMinionTyp, goodsID[], chessID[], buy})
    # 所以"主动使用主公技能"= 发一条 cmsg.CReqChessUseSkill。脚本侧走 manager.ReqChessUseSkill,
    # 与游戏自身完全同源(不依赖被关在闭包里的 TavernChessGameContext)。
    #
    # 「我这一局有哪些主公技能」只能问活体: manager.SelfInfo.generalSkillList
    # (选将时 = [选中的技能, 主公自带技能], 1053「面涅」这类还能再拿一个 → 可能 >1 个)。
    # 助手离线表里没有"本局拿到哪几个"这个信息, 所以必须有 lord_skill_state 这个方法。
    #
    # 被动技能(GeneralSkillType=2): 客户端没有施放入口, 发包也会被服务器拒 —— 脚本会把
    # usable 标 False 并给出原因, UI 直接禁掉按钮(而不是让你点了发现没反应)。

    def lord_skill_state(self):
        """读【本局我方主公技能】当前状态。

        返回脚本 lordSkillState() 的原样 dict::

            {"ok": True, "reason": "", "inGame": True, "generalID": 1021,
             "canOperate": True, "phase": 6,
             "skills": [{"skillID": 1021, "name": "戎弁", "desc": "...",
                         "skillType": 1, "effectType": 12, "costType": 2,
                         "costNum": 0, "isUsed": False, "usable": True, "note": ""}],
             "src": {"cfg": True, "zgsm": False, "skillList": "generalSkillList"}}

        读不到时 {"ok": False, "reason": "...", "skills": []}。

        src 说明(降级信息, UI 会显示):
            cfg   = 拿到 TavernChessConfiger → 技能名/主动被动/描述来自游戏自己
            zgsm  = 拿到 TavernChessZhuGongSkillManager → 才知道 isUsed("本回合是否已用")
                    拿不到时 isUsed 为 None(未知), 此时 UI 仍允许手动尝试
        """
        try:
            res = self.send_recv("lord_skill_state", timeout=4.0)
        except Exception as e:
            return {"ok": False, "reason": f"读取主公技能状态异常: {e}", "skills": []}
        if not res:
            return {"ok": False, "reason": "脚本没有回包(页面未连接, 或脚本版本 < 4.46)", "skills": []}
        if res == "ERR:undefined":
            return {"ok": False, "reason": "脚本没有 lord_skill_state 这个指令(版本过旧?)", "skills": []}
        try:
            data = json.loads(res)
        except Exception as e:
            return {"ok": False, "reason": f"回包不是 JSON: {res!r} ({e})", "skills": []}
        if not isinstance(data, dict):
            return {"ok": False, "reason": f"回包结构异常: {res!r}", "skills": []}
        return data

    def lord_general_state(self):
        """v4.152★ 读选将阶段状态(现在在不在选将 / 本局我选了哪个主公)。

        返回脚本 lordGeneralState() 的原样 dict::

            {"ok": True, "reason": "", "phase": 0, "inChoose": True, "myGeneralID": 1019}

        读不到时 {"ok": False, "reason": "...", "inChoose": False, "myGeneralID": 0}。
        """
        try:
            res = self.send_recv("lord_general_state", timeout=4.0)
        except Exception as e:
            return {"ok": False, "reason": f"读取选将状态异常: {e}",
                    "inChoose": False, "myGeneralID": 0}
        if not res:
            return {"ok": False, "reason": "脚本没有回包(页面未连接)",
                    "inChoose": False, "myGeneralID": 0}
        if res == "ERR:undefined":
            return {"ok": False, "reason": "脚本没有 lord_general_state 这个指令(版本过旧?)",
                    "inChoose": False, "myGeneralID": 0}
        try:
            data = json.loads(res)
        except Exception as e:
            return {"ok": False, "reason": f"回包不是 JSON: {res!r} ({e})",
                    "inChoose": False, "myGeneralID": 0}
        if not isinstance(data, dict):
            return {"ok": False, "reason": f"回包结构异常: {res!r}",
                    "inChoose": False, "myGeneralID": 0}
        return data

    def lord_general_auto(self, cfg=None, reset_fired=False):
        """v4.152★ 自动选将: 下发配置 / 回读状态。

        :param cfg: None 表示只读; 否则下发。结构::

            {"enabled": True, "mode": "confirm", "gap": 120,
             "rules": [{"require": [3], "exclude": [], "generalID": 1019, "req": 0,
                        "label": "有吴 -> 李广"},
                       {"require": [6], "exclude": [3], "generalID": 1048, "req": 0,
                        "label": "有汉 没吴 -> 岳飞"},
                       {"require": [], "exclude": [3, 6], "generalID": 1044, "req": 0,
                        "label": "没吴 没汉 -> 狄青"}]}

        require / exclude 里的数字是本局势力表 manager.ChessMinionTypList 的编码
        (见 data/minion_types.json: 1魏 2蜀 3吴 4群 5黄巾 6汉 7西凉 8袁绍 11神)。
        规则是一条【链】: 按数组顺序取第一条满足的 ——
          require 要求【全部包含】, exclude 要求【全部不包含】;
          两个都为空 = 兜底(一定命中), 习惯上放最后。
        v4.163★ 新增 exclude(即"没有某势力"这类条件), 条数不限;
        某条命中的主公自带势力要求(req)而本局没有时, 不再整条卡住, 跳去试下一条。
        """
        params = {}
        if cfg is not None:
            params["cfg"] = cfg
            params["resetFired"] = bool(reset_fired)
        try:
            res = self.send_recv("lord_general_auto", params, timeout=5.0)
        except Exception as e:
            return {"ok": False, "reason": f"异常: {e}"}
        if not res:
            return {"ok": False, "reason": "脚本没有回包(页面未连接)"}
        if res == "ERR:undefined":
            return {"ok": False, "reason": "脚本没有 lord_general_auto 这个指令(版本过旧?)"}
        try:
            data = json.loads(res)
        except Exception as e:
            return {"ok": False, "reason": f"回包不是 JSON: {res[:200]!r} ({e})"}
        return data if isinstance(data, dict) else {"ok": False, "reason": f"回包结构异常: {res[:200]!r}"}

    def lord_general_probe(self, timeout=8.0):
        """v4.152★ 选将探针: 一次性摸清所有可能的发包入口。

        实机现象"phase=none / 窗口开着 / 可操作 / 有主公, 但发不出去" —— 光靠猜不行,
        这个指令把 getScene().manager / win.Manager / TavernChessGameContext.Manager
        三个候选入口挨个报: 拿不拿得到、类名、ReqChessChooseGeneral 是不是函数、
        所有含 General 的方法名、phase 到底叫什么。

        返回 dict(原样), 失败时 {"ok": False, "reason": ...}。
        """
        try:
            res = self.send_recv("lord_general_probe", timeout=timeout)
        except Exception as e:
            return {"ok": False, "reason": f"探针异常: {e}"}
        if not res:
            return {"ok": False, "reason": "脚本没有回包(页面未连接 / 脚本没刷新)"}
        if res == "ERR:undefined":
            return {"ok": False, "reason": "脚本没有 lord_general_probe 这个指令(版本过旧?)"}
        try:
            data = json.loads(res)
        except Exception as e:
            return {"ok": False, "reason": f"回包不是 JSON: {res[:200]!r} ({e})"}
        return data if isinstance(data, dict) else {"ok": False, "reason": f"回包结构异常: {res[:200]!r}"}

    def lord_choose_general(self, general_id, is_preselection=True, timeout=5.0,
                            both=False, gap=120):
        """v4.152★ 选将: 直接发 ReqChessChooseGeneral, 把指定主公推到当前游戏页。

        :param general_id: 主公ID(如 1019)。从 data/generals.json 那 40 个里取。
        :param is_preselection: True=预选(游戏自己的默认: 先在界面上"预定"某个主公);
                                False=确认。实机两种都发过, 且 err_code=0。

        返回 {ok, reason, sent}。ok=True 只代表【包发出去了】, 服务器接不接受看
        游戏界面有没有真的变成那个主公。
        """
        try:
            gid = int(general_id)
        except Exception:
            return {"ok": False, "reason": f"主公ID不是数字: {general_id!r}"}
        # 把配置表里这个主公的"兵种要求"带给脚本: 脚本读不到 data/generals.json,
        # 但游戏的 CheckGeneralAlive 要用它跟本局 ChessMinionTypList 比对(闸3)。
        req_typ = 0
        try:
            for _g in _load_generals():
                if int(_g.get("id") or 0) == gid:
                    req_typ = int(_g.get("reqMinionTyp") or 0)
                    break
        except Exception:
            pass
        params = {"generalID": gid, "isPreselection": bool(is_preselection),
                  "reqMinionTyp": req_typ}

        # v4.152★ both=True 时脚本内部一次做完"预选+确认"(间隔 gap 毫秒),
        #   省掉一次跨进程往返 —— 实测一轮 2~3 秒, 两次就是 5 秒起步, 用户嫌慢。
        if both:
            params["both"] = True
            params["gap"] = int(gap or 120)
        try:
            res = self.send_recv("lord_choose_general", params, timeout=timeout)
        except Exception as e:
            return {"ok": False, "reason": f"选将异常: {e}"}
        if not res:
            return {"ok": False, "reason": "脚本没有回包(页面未连接)"}
        if res == "ERR:undefined":
            return {"ok": False, "reason": "脚本没有 lord_choose_general 这个指令(版本过旧?)"}
        try:
            data = json.loads(res)
        except Exception as e:
            return {"ok": False, "reason": f"回包不是 JSON: {res!r} ({e})"}
        return data if isinstance(data, dict) else {"ok": False, "reason": f"回包结构异常: {res!r}"}

    def lord_faction_probe(self):
        """诊断: 抓岳飞『精忠选势力』窗口的协议(类名 / 候选势力 / onOk 源码)。

        返回 {"ok": True, "raw": "<脚本返回>"} —— raw 可能是 ok:JSON(可直接发给开发者确认协议),
        也可能是 no-window(此刻没弹窗) / err:…。确认出协议后再据实写自动回答。
        """
        if not self.connected():
            return {"ok": False, "reason": "助手未连接游戏页"}
        try:
            res = self.send_recv("eval",
                                 js="(window.__sgsLordFactionProbe||function(){return 'err:脚本无此函数(版本过旧?)';})()",
                                 timeout=3.0)
        except Exception as e:
            return {"ok": False, "reason": f"调用失败: {e}"}
        return {"ok": True, "raw": str(res)}

    def lord_faction_answer(self, faction):
        """手动回答岳飞选势力窗口(best-effort: 协议确认前先手动用, 真机试了才知道对不对)。

        :param faction: '魏'/'蜀'/'吴'/'群'/'黄巾'/'汉'/'西凉'/'袁' 或数字 1~8。
        """
        if not self.connected():
            return {"ok": False, "reason": "助手未连接游戏页"}
        try:
            js = "(window.__sgsLordFactionAnswer||function(){return 'err:脚本无此函数(版本过旧?)';})(" \
                 + json.dumps(str(faction)) + ")"
            res = self.send_recv("eval", js=js, timeout=3.0)
        except Exception as e:
            return {"ok": False, "reason": f"调用失败: {e}"}
        return {"ok": True, "raw": str(res)}

    def lord_skill_use(self, skill_id, goods_ids=None, chess_ids=None,
                       chess_minion_typ=0, buy=False, force=False, wait=1.2,
                       wait_operable=2.4):
        """主动使用一个主公技能。返回 {ok, reason, sent, before, after?}。

        :param skill_id: 主公技能ID(如 1021)。
        :param goods_ids: 需要选目标时, 目标卡的 **goodsID** 列表(手牌/商店/场上的实例ID)。
        :param chess_ids: 需要选目标时, 目标卡的 chessID 列表(卡的型号ID)。
                          两个都传是安全的: 游戏自己的窗口也是同时带 goodsID 和 chessID
                          (TavernChessZhuGongSkillWindow: ReqChessUseSkill(SkillID, minion, [g], [c], buy))。
        :param chess_minion_typ: 需要"选势力/兵种"的技能用(对应协议 chessMinionTyp)。
        :param buy: 极少数技能需要(对应协议 buy), 默认 False。
        :param force: 脚本侧默认会拒【被动技能】(客户端没有施放入口, 服务器也会拒);
                      传 True 才能硬发一次(UI 在用户确认后才会传)。
        :param wait_operable: 发【之前】先等"轮到我方能操作"的窗口, 默认 2.4s(v4.47★)。
            以前直接发, 实机日志里就出现
              "[ERROR] 1058 使用失败: 现在不能操作(CanOperate=false) —— 等游戏这一手落定再试"
            —— 玩家点了『使用』却被顶回来, 体验等于"点了没反应"。
            现在每 0.4s 读一次状态, 等 CanOperate 变 True 再发; 实际等了多久写在返回值 "waited" 里。
            传 0 = 不等待(脚本内部循环自己控节奏时用); force 时也不等待(要的就是立刻试一次)。
        :param wait: 发完等多久再回读一次状态, 默认 1.2s; 0 = 不回读。
                     回读结果放在返回值的 "after" 里 —— 用来区分"包发出去了"和"服务器真的接受了"
                     (接受后一般能看到 isUsed 变 True / generalSkillList 变化 / 留牌变了)。

        ⚠ 这是【主动技能】的入口。被动技能会被脚本拦下(除非 force) —— 因为客户端根本没有
          施放入口, 发包只会被服务器拒。被动是否触发由服务器按条件判定。
        """
        try:
            sid = int(skill_id)
        except Exception:
            return {"ok": False, "reason": f"技能ID不是数字: {skill_id!r}"}
        # v4.47★ 发之前先等"能操作"的窗口 —— 实机日志里最扎眼的一条就是
        #   "[ERROR] 1058 使用失败: 现在不能操作(CanOperate=false) —— 等游戏这一手落定再试"
        # 玩家点了『使用』却被"现在不能操作"顶回去, 体验上等于"点了没反应"。
        # 现在改成: 轮询读状态(每 0.4s)直到 CanOperate 变 True 再发, 最多等 wait_operable 秒;
        # 等到了就把实际等了多久写进返回值(UI 显示"等了 0.8s 才轮到"), 等不到仍然明确报错。
        # force(用户确认硬发被动)时不等待 —— 那时候要的是"立刻试一次看服务器怎么说"。
        waited_op = 0.0
        try:
            _wop = float(wait_operable or 0)
        except (TypeError, ValueError):
            _wop = 0.0
        if _wop > 0 and not force:
            _t0 = time.time()
            while True:
                _st = self.lord_skill_state()
                if not _st.get("ok"):
                    break                       # 读不到状态就不拦, 交给脚本/UI 自己判
                if _st.get("canOperate") is not False:
                    break
                waited_op = time.time() - _t0
                if waited_op >= _wop:
                    return {"ok": False, "waited": round(waited_op, 2),
                            "reason": "现在不能操作(CanOperate=false): 等了 %.1fs 还没轮到我方 —— "
                                      "等这一手落定(动画结束/轮到自己/回到招募阶段)再点一次" % waited_op}
                time.sleep(0.4)
            waited_op = round(time.time() - _t0, 2)
        params = {
            "skillID": sid,
            "chessMinionTyp": int(chess_minion_typ or 0),
            "buy": bool(buy),
        }
        if force:
            params["force"] = True
        if goods_ids:
            params["goodsIDs"] = [int(g) for g in goods_ids]
        if chess_ids:
            params["chessIDs"] = [int(g) for g in chess_ids]
        try:
            res = self.send_recv("lord_skill_use", params, timeout=5.0)
        except Exception as e:
            return {"ok": False, "reason": f"使用主公技能异常: {e}"}
        if not res:
            return {"ok": False, "reason": "脚本没有回包(页面未连接, 或脚本版本 < 4.46)"}
        try:
            data = json.loads(res)
        except Exception as e:
            return {"ok": False, "reason": f"回包不是 JSON: {res!r} ({e})"}
        if not isinstance(data, dict):
            return {"ok": False, "reason": f"回包结构异常: {res!r}"}
        if wait and data.get("ok"):
            # 回读前的等待: 把 wait 洗成数字而不是 try/except:pass ——
            #   项目有"静默 except: pass 不许增长"的静态预算(见 dev/_verify_static.py),
            #   而且这里本来也没有"必须咽掉的异常", 洗一下参数就够。
            try:
                _w = float(wait)
            except (TypeError, ValueError):
                _w = 0.0
            if _w > 0:
                time.sleep(_w)
            data["after"] = self.lord_skill_state()
        return data




    # ---------- 通道④: 逻辑地址深度探测 (v3.61★ 稳定版) ----------
    def discover_logical_paths(self):
        """深度扫描浏览器 JS 对象树, 自动寻找符合自走棋特征的真实地址.
        v3.61: 增加 5s 时间限制与递归保护, 防止浏览器卡死."""
        self.log("[逻辑探测] 启动增强型扫描 (适配 20格备战/6格商店)...")
        
        # 探测脚本: 优化了递归效率，增加了 5s 超时保护与 Set 递归去重
        scanner_js = """
        (function() {
            let results = { _diag: { start_ts: Date.now() }, _all_detected: { battle: [], bench: [], shop: [] } };
            let visited = new Set();
            let startTime = performance.now();
            let timeLimit = 5000; // 5秒强制退出机制
            
            // 基础诊断
            results._diag.url = location.href;
            results._diag.is_top = (window === window.top);
            
            function doScan(win, isIframe = false) {
                if (!win) return;
                let prefix = isIframe ? "[IFRAME] " : "";
                
                // 诊断信息记录
                results._diag.has_LIVE = results._diag.has_LIVE || !!win.LIVE;
                results._diag.has_game = results._diag.has_game || !!win.game;
                results._diag.has_Laya = results._diag.has_Laya || !!win.Laya;

                // 1. 极速路径 (LIVE 缓存优先)
                try {
                    if (win.LIVE && win.LIVE.me) {
                        if (Array.isArray(win.LIVE.me._lineUp) && !results.battle) results.battle = prefix + "LIVE.me._lineUp";
                        if (Array.isArray(win.LIVE.me._board) && !results.bench) results.bench = prefix + "LIVE.me._board";
                    }
                    if (win.LIVE && Array.isArray(win.LIVE.shop) && !results.shop) results.shop = prefix + "LIVE.shop";
                } catch(e) {}

                // 2. 深度探测逻辑
                function scan(obj, path, depth) {
                    if (depth > 12 || !obj || typeof obj !== 'object') return;
                    if (performance.now() - startTime > timeLimit) return;
                    try { if (visited.has(obj)) return; visited.add(obj); } catch(e) { return; }
                    
                    try {
                        let low = path.toLowerCase();
                        let len = Array.isArray(obj) ? obj.length : 0;
                        
                        // v3.91★ 增强型特征匹配 (S10 适配)
                        if (len >= 1) {
                            // A. 战斗区特征 (7-14格)
                            if (len <= 14 && (low.includes('lineupchess') || low.includes('positionchess') || (low.includes('.chess') && !low.includes('hand') && !low.includes('shop')))) {
                                if (!results.battle || low.includes('selfinfo')) results.battle = prefix + path;
                                results._all_detected.battle.push({path: prefix + path, len: len});
                            }
                            
                            // B. 备战区/手卡特征 (1-25格)
                            if (len <= 25 && (low.includes('hand') || low.includes('bench') || low.includes('board'))) {
                                if (!results.bench || low.includes('newly') || low.includes('spellids')) results.bench = prefix + path;
                                results._all_detected.bench.push({path: prefix + path, len: len});
                            }
                            
                            // C. 购卡区/商店特征 (3-10格)
                            if (len >= 3 && len <= 10 && (low.includes('shop') || low.includes('goods') || (low.includes('chess') && !low.includes('selfinfo') && !low.includes('hand')))) {
                                if (!results.shop || low.includes('shopgoods')) results.shop = prefix + path;
                                results._all_detected.shop.push({path: prefix + path, len: len});
                            }
                        }
                        
                        // v3.86★ 备战区特征: 长度 1-25, 包含 handChess (最准确) 或 handSpell 或 handSpellIDs 或 board
                        if (len >= 1 && len <= 25) {
                            let isBench = false;
                            let lowerPath = path.toLowerCase();
                            // 极高优先级关键字: S10 新增 newlyhandchess, handspellids
                            if (lowerPath.includes('handchess') || lowerPath.includes('newlyhandchess') || lowerPath.includes('handspellids')) {
                                if (!results.bench || lowerPath.includes('newly') || lowerPath.includes('spellids')) {
                                    results.bench = prefix + path;
                                }
                                results._all_detected.bench.push({path: prefix + path, len: len, type: 'chess/spell', data: obj});
                                isBench = true;
                            } else if ((lowerPath.includes('handspell') || lowerPath.includes('handids')) && !lowerPath.includes('shop')) {
                                if (!results.bench || (!lowerPath.includes('spell') && !lowerPath.includes('handchess'))) {
                                    results.bench = prefix + path;
                                }
                                results._all_detected.bench.push({path: prefix + path, len: len, type: 'spell/id', data: obj});
                                isBench = true;
                            } else if ((lowerPath.includes('board') || lowerPath.includes('bench')) && !lowerPath.includes('shop') && !lowerPath.includes('lineup')) {
                                if (!results.bench) results.bench = prefix + path;
                                results._all_detected.bench.push({path: prefix + path, len: len, type: 'other', data: obj});
                                isBench = true;
                            }
                        }

                        // v3.83★ 购卡区特征: 包含 shopGoods (最准确) 或 shop
                        if (len >= 3 && len <= 10) {
                            if (low.includes('shopgoods')) {
                                results.shop = prefix + path;
                            } else if (low.includes('shop') && !low.includes('hand') && !low.includes('lineup') && 
                                       !low.includes('bench') && !low.includes('chess') && !low.includes('selfinfo')) {
                                if (!results.shop) results.shop = prefix + path;
                            }
                        }

                        let keys = Object.keys(obj);
                        // 优先扫描数组和 _children 属性
                        keys.sort((a, b) => (b === '_children' || b === 'items' || b === 'manager' || b === 'selfInfo' || b === 'shopGoods') - (a === '_children' || a === 'items' || a === 'manager' || a === 'selfInfo' || a === 'shopGoods'));
                        
                        for (let i = 0; i < Math.min(keys.length, 100); i++) {
                            let k = keys[i];
                            if (k === 'window' || k === 'parent' || k === 'top' || k === 'document' || k.startsWith('__')) continue;
                            let child = obj[k];
                            if (child && typeof child === 'object') {
                                scan(child, path ? (path + (Array.isArray(obj) ? '['+k+']' : '.'+k)) : k, depth + 1);
                            }
                            // 如果三个都找到了，且都是高质量路径（包含关键字），可以提前退出
                            if (results.battle && results.bench && results.shop) {
                                let btl = results.battle.toLowerCase();
                                let bnc = results.bench.toLowerCase();
                                let shp = results.shop.toLowerCase();
                                if (btl.includes('lineup') && bnc.includes('hand') && shp.includes('shop')) return;
                            }
                        }
                    } catch(e) {}
                }

                let roots = ['LIVE', 'game', 'me', 'TavernChess', 'AutoChess', 'App', 'Core', 'GameMain', 'SGS', 'G', 'Logic', 'Data'];
                for (let r of roots) {
                    if (win[r]) scan(win[r], r, 0);
                    if (results.battle && results.shop && results.bench) break;
                }

                // v3.63★ 特别处理 Laya 引擎
                if (win.Laya && (!results.battle || !results.shop || !results.bench)) {
                    try {
                        if (win.Laya.stage) scan(win.Laya.stage, "Laya.stage", 0);
                        if (win.Laya.Scene && win.Laya.Scene.root) scan(win.Laya.Scene.root, "Laya.Scene.root", 0);
                        if (win.Laya.SceneMgr) scan(win.Laya.SceneMgr, "Laya.SceneMgr", 0);
                        if (win.Laya.Pool) scan(win.Laya.Pool, "Laya.Pool", 1);
                    } catch(e) {}
                }
            }

            // 执行主窗体扫描
            doScan(window, false);

            // 如果没找到，尝试扫描所有 iframe
            if (!results.battle || !results.shop || !results.bench) {
                try {
                    for (let i = 0; i < window.frames.length; i++) {
                        try {
                            doScan(window.frames[i], true);
                        } catch(e) {}
                        if (results.battle && results.shop && results.bench) break;
                    }
                } catch(e) {}
            }

            // 全局兜底: 扫描 window 下的首字母大写单例
            if (!results.battle || !results.shop || !results.bench) {
                try {
                    let winKeys = Object.keys(window).filter(k => k.length < 25 && !['LIVE', 'game', 'Laya'].includes(k));
                    winKeys.sort((a, b) => (b[0] === b[0].toUpperCase()) - (a[0] === a[0].toUpperCase()));
                    for (let i = 0; i < Math.min(winKeys.length, 150); i++) {
                        let r = winKeys[i];
                        try { scan(window[r], r, 0); } catch(e) {}
                        if (results.battle && results.shop && results.bench) break;
                    }
                } catch(e) {}
            }
            
            return JSON.stringify(results);
        })()
        """
        
        cid = self.send("js", code=scanner_js)
        res = self.wait_result(cid, 10.0)
        
        if not res or res.strip() == "" or res == "null" or res == "undefined":
            self.log("[逻辑探测] 失败: 脚本执行无响应或返回空。请检查油猴插件是否正常工作。")
            return None
            
        try:
            import json
            found = json.loads(res)
            diag = found.get("_diag", {})
            self.log(f"[逻辑探测] 诊断: LIVE={diag.get('has_LIVE')}, game={diag.get('has_game')}, Laya={diag.get('has_Laya')}, TopFrame={diag.get('is_top')}")
            
            if not found.get("battle") and not found.get("shop"):
                self.log("[逻辑探测] 失败: 未能在当前及子框架中搜寻到符合条件的战斗/商店数据。")
                self.log("[提示] 请确保已进入对局且商店已开启。如果是跨域框架，请尝试在油猴设置中将脚本注入级别设为 'Always'。")
                return None

            self.log(f"[逻辑探测] 探测完毕! 发现地址: { {k:v for k,v in found.items() if k!='_diag'} }")
            
            # v3.69★ ID 导出逻辑: 将探测到的 ID 存入 discovered_ids.json
            self._export_discovered_ids(found)

            # 持久化更新
            from .config import load_config, save_config
            CONFIG = load_config()
            changed = False
            
            # 确保 sub_regions 存在
            if "sub_regions" not in CONFIG: CONFIG["sub_regions"] = {}
            # 确保 sub_regions.logical_paths 存在
            if "logical_paths" not in CONFIG["sub_regions"]: 
                CONFIG["sub_regions"]["logical_paths"] = {}
            
            target = CONFIG["sub_regions"]["logical_paths"]
            for k, label in {"battle":"战斗区", "shop":"购卡区", "bench":"备战区"}.items():
                if found.get(k):
                    if target.get(label) != found[k]:
                        target[label] = found[k]
                        changed = True
            if changed:
                save_config(CONFIG)
                self.log("[逻辑探测] config.json 已更新。")
            return found
        except Exception as e:
            self.log(f"[逻辑探测] 解析出错: {e}")
            return None

    def _export_discovered_ids(self, snap):
        """v3.91★ 将探测到的 ID 导出为多种格式 (JSON/TXT/PICKER)"""
        try:
            from .config import DATA_DIR
            import os
            
            ids = {}
            all_data = snap.get("_all_detected") or snap
            dom_log = [
                "// ============================================================",
                "// 助手 DOM 深度探测报告 (分层明细)",
                f"// 探测时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
                "// ============================================================\n"
            ]

            for area_key in ["battle", "shop", "bench"]:
                items = all_data.get(area_key) or []
                if not isinstance(items, list): continue
                
                dom_log.append(f"// --- {area_key.upper()} 区 ---")
                for i, item in enumerate(items):
                    cid = None
                    props = {}
                    if isinstance(item, dict):
                        cid = item.get("chessID") or item.get("goodsID") or item.get("spellID") or item.get("itemID")
                        # 收集更多属性用于分层显示
                        props = {k: v for k, v in item.items() if not str(k).startswith("_") and k != "path"}
                    elif isinstance(item, (int, str)):
                        cid = item
                    
                    if cid:
                        cid_s = str(cid)
                        # 锦囊归一化
                        from .protocol import GameLive
                        if cid_s.isdigit() and len(cid_s) == 6 and cid_s[:2] in GameLive.SPELL_CID_PREFIX:
                            if not cid_s.startswith("spell_"): cid_s = "spell_" + cid_s
                            
                        name = IDMAP.name_of(cid_s, chess_hint=(area_key != "bench"))
                        ids[cid_s] = {
                            "n": name if not name.startswith("?") else "",
                            "area": area_key,
                            "src": f"dom:{area_key}"
                        }
                        
                        # 格式化导出到 TXT
                        item_info = f"  [{i}] ID:{cid_s.ljust(10)} 匹配名:{name}"
                        if props: item_info += f"  属性:{json.dumps(props, ensure_ascii=False)}"
                        dom_log.append(item_info)

            # 1. 累积学习到 discovered_ids.json
            if ids:
                added = IDMAP.learn_ids(ids)
                if added > 0:
                    self.log(f"[逻辑探测] 累积学习了 {added} 个新 ID")

            # 2. 导出详细探测报告 dom_probe.txt
            with open(os.path.join(DATA_DIR, "dom_probe.txt"), "w", encoding="utf-8") as f:
                f.write("\n".join(dom_log))
            
            # 3. 导出符合 hero_picker.json 格式的候选文件
            picker_data = {"white_list": [], "black_list": []}
            for cid, info in ids.items():
                if info["n"] and not cid.startswith("spell_"):
                    # 只放武将
                    picker_data["white_list"].append(cid)
            
            with open(os.path.join(DATA_DIR, "discovered_ids_picker.json"), "w", encoding="utf-8") as f:
                json.dump(picker_data, f, ensure_ascii=False, indent=2)
            
            # v4.22★ 修复 hero_picker.json「双份文件 + 写错字段」:
            #   1) 路径错误: 这里写的是 os.path.dirname(DATA_DIR)/hero_picker.json(项目【根目录】),
            #      而 build_hero_picker() / id_map.py / deploy_position() / 当先判定 全都用
            #      DATA_DIR/hero_picker.json(data/)。一写一读两个文件 → 用户改了这里不生效;
            #      打包时 _build_exe.py 又把根目录那份【又旧又损坏】的带进发行包。
            #      现统一到 DATA_DIR, 全项目只剩这一个真源。
            #   2) 字段错误: 这里往 "white_list" 里塞, 但全项目读的都是 "whitelist"
            #      (见 id_map.py 的合并、build_hero_picker 的导出、widgets.load_user_patch),
            #      所以这条“自动同步”其实从来没被任何代码读到过。现改用 "whitelist"。
            hero_picker_path = os.path.join(DATA_DIR, "hero_picker.json")
            if os.path.exists(hero_picker_path):
                try:
                    with open(hero_picker_path, "r", encoding="utf-8") as f:
                        current_picker = json.load(f)

                    if not isinstance(current_picker, dict):
                        current_picker = {"whitelist": [], "blacklist": []}

                    added_count = 0
                    current_white = set(str(x) for x in (current_picker.get("whitelist") or []))
                    for cid in picker_data["white_list"]:
                        if str(cid) not in current_white:
                            current_picker.setdefault("whitelist", []).append(cid)
                            current_white.add(str(cid))
                            added_count += 1

                    if added_count > 0:
                        with open(hero_picker_path, "w", encoding="utf-8") as f:
                            json.dump(current_picker, f, ensure_ascii=False, indent=2)
                        self.log(f"[逻辑探测] 已自动同步 {added_count} 个新武将到 data/hero_picker.json")
                except Exception as pe:
                    self.log(f"[逻辑探测] 自动同步 hero_picker.json 失败: {pe}")
            
            self.log("[逻辑探测] 已生成 dom_probe.txt 和 discovered_ids_picker.json")
            
        except Exception as e:
            self.log(f"[逻辑探测] 导出 ID 失败: {e}")

    # ---------- v3.98★ 环境诊断报告导出 (父容器/同类全抓取) ----------
    def export_bridge_context_report(self):
        """导出桥端实时环境报告: 抓取 Laya 舞台节点树，并包含父节点与兄弟节点上下文。"""
        self.log("[环境报告] 正在抓取桥端 DOM 上下文...")
        # 抓取带 parent/children 信息的全树快照 (限制深度和节点数防止溢出)
        code = """
        (function() {
            function getContext(node, depth) {
                if (!node || depth > 5) return null;
                let info = {
                    name: node.name || "unnamed",
                    cls: node.__proto__?.constructor?.name || "Object",
                    x: node.x, y: node.y, vis: node.visible,
                    props: {}
                };
                // 抓取关键业务属性
                ["goodsID", "chessID", "generalID", "spellID", "star", "level", "cost", "hp", "atk", "side"].forEach(p => {
                    if (node[p] !== undefined) info.props[p] = node[p];
                });
                if (node.numChildren > 0) {
                    info.children = [];
                    for(let i=0; i<Math.min(node.numChildren, 20); i++) {
                        let c = getContext(node.getChildAt(i), depth + 1);
                        if(c) info.children.push(c);
                    }
                }
                return info;
            }
            let roots = {};
            if (window.Laya && window.Laya.stage) {
                roots["Laya.stage"] = getContext(window.Laya.stage, 0);
            }
            if (window.LIVE) {
                roots["LIVE_Global"] = { name: "LIVE", props: window.LIVE };
            }
            return JSON.stringify(roots);
        })()
        """
        cid = self.send("js", code=code)
        res = self.wait_result(cid, 8.0)
        if not res:
            self.log("[环境报告] 桥端无响应")
            return
        
        try:
            from .config import DATA_DIR
            import os as _os
            path = _os.path.join(DATA_DIR, "s10_container_report.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"=== 桥端环境上下文诊断报告 ({time.strftime('%Y-%m-%d %H:%M:%S')}) ===\n\n")
                f.write(res) # 直接写入 JSON 结构，方便用户查看层级
            self.log(f"[环境报告] 已导出至: {path}")
        except Exception as e:
            self.log(f"[环境报告] 导出失败: {e}")

    def export_config_context_report(self):
        """导出配置表环境报告: 抓取本地 JSON 库及其父容器/同类项明细。"""
        self.log("[环境报告] 正在生成配置表上下文报告...")
        try:
            from .config import DATA_DIR
            import os as _os
            report_path = _os.path.join(DATA_DIR, "s10_ids_report.txt")
            
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(f"=== 配置表环境上下文报告 ({time.strftime('%Y-%m-%d %H:%M:%S')}) ===\n")
                f.write("说明: 展示本地 JSON 库的父级容器(文件名)及同类项(Siblings)分布\n\n")
                
                files = ["tavern_chess.json", "tavern_spells.json", "hero_picker.json", "id_map.json"]
                for fn in files:
                    p = _os.path.join(DATA_DIR, fn)
                    if not _os.path.exists(p):
                        # 尝试根目录
                        p = _os.path.join(_os.path.dirname(DATA_DIR), fn)
                    
                    if _os.path.exists(p):
                        f.write(f"\n📂 父容器: {fn}\n")
                        f.write("-" * 40 + "\n")
                        with open(p, "r", encoding="utf-8") as jf:
                            data = json.load(jf)
                            if isinstance(data, dict):
                                f.write(f"同类项总数: {len(data)}\n")
                                # 抽取前 10 个作为同类样本展示
                                sample_keys = list(data.keys())[:15]
                                for k in sample_keys:
                                    f.write(f"  [Sibling] Key: {k.ljust(10)} Data: {json.dumps(data[k], ensure_ascii=False)}\n")
                                if len(data) > 15:
                                    f.write(f"  ... (余下 {len(data)-15} 项已省略)\n")
                            elif isinstance(data, list):
                                f.write(f"容器类型: 数组, 总长度: {len(data)}\n")
                                for i, item in enumerate(data[:10]):
                                    f.write(f"  [Item {i}] {json.dumps(item, ensure_ascii=False)}\n")
                    else:
                        f.write(f"\n❌ 未找到父容器: {fn}\n")
            
            self.log(f"[环境报告] 已导出至: {report_path}")
        except Exception as e:
            self.log(f"[环境报告] 导出失败: {e}")

    def dump_shop_nodes(self):
        """v3.98★ 专项探测 TavernChessSaleMaskTopUI 节点数据并保存"""
        self.log("[探测] 正在请求商店节点 TavernChessSaleMaskTopUI 的内存镜像...")
        # 调用脚本中新加的 dumpShopNodeData 函数
        cid = self.send("eval", js="dumpShopNodeData()")
        res = self.wait_result(cid, 5.0)
        if not res:
            self.log("[探测] 失败: 脚本未响应")
            return
        
        try:
            data = json.loads(res)
            from .config import DATA_DIR
            out_path = os.path.join(DATA_DIR, "snapshot_shop.json")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self.log(f"[探测] 成功! 抓取到 {len(data)} 个商店节点数据，已保存至: {out_path}")
        except Exception as e:
            self.log(f"[探测] 异常: {e}")

    def grab_card_faces(self):
        """扫卡面名字(nameTxt/atkTxt/hpTxt)——配合IDMAP.learn_faces与协议商店配对学ID映射"""
        cid = self.send("card_faces")
        res = self.wait_result(cid, 8.0)
        if not res or res.startswith(("ERR", "notfound")):
            self.log(f"[卡面名字] {res or '无响应'}")
            return None
        try:
            faces = json.loads(res)
        except Exception:
            return None
        return faces if isinstance(faces, list) else []

    # ================== 游戏动作(全部注入) ==================
    def skip_battle(self):
        """跳过战斗=原理直调：直接执行跳过/小抄按钮注册的click回调
        ★关键: 类名用精确前缀匹配(^锚定类名开头), 避免误触发 CloseBtn/JumpExit 等包含关键字的按钮

        v4.102★ 两处修复(实锤来自 2026-09-24 日志):
          ①重复触发: 手动 Space 与后台自动跳过会在同一秒各起一次 → 日志里
            "[后台] 跳过战斗 启动..." 连着出现两遍, 同一个按钮被点两次。
            这里加 1.5s 节流, 窗口内的重复请求直接忽略(按成功返回, 不改变语义)。
          ②vis=False 仍点击: 节点不可见说明不在战斗结算界面, 回调虽然"执行成功"
            也是空点。这里先取 vis, 不可见就记录原因并返回 False(交给上层判断)。
        """
        import time as _t
        now = _t.time()
        last = getattr(self, "_last_skip_ts", 0)
        if now - last < 1.5:
            self.log(f"[跳过] {1.5 - (now - last):.1f}s 内已请求过跳过, 忽略重复触发")
            return True
        self._last_skip_ts = now
        # 黑名单: 包含这些词的类名跳过(取消/关闭/退出/解散/返回)
        kw = r"(?=.*(?:JumpBtn|SkipBtn|TavernChessJump|TavernChessSkip))(?!.*(?:Cancel|Close|Exit|Dismiss|Quit|Back|Return|Unready|UnReady))"
        data = self.direct_call(kw, label="跳过", return_data=True)
        if isinstance(data, dict):
            if data.get("vis") is False:
                self.log("[跳过] 跳过按钮当前不可见(不在战斗结算界面) —— 不派发点击")
                return False
            if any(str(c).endswith(":ok") for c in (data.get("called") or [])):
                return True
        r = self._find_click(kw, regex=True)
        if r:
            self.log(f"跳过战斗: 事件直呼 {r}")
            return True
        self.log("[跳过] 类名未命中——战斗中再试或把诊断日志发我补类名")
        return False

    def skip_battle_full(self):
        """跳过战斗(全)= 调页内 skipBattle(), 按阶段分派:
           phase 9(战斗) → scene.onJumpBtnClick() 跳过战斗;
           phase 7(招募结束) → scene.onEndRecruitJump() 结束招募。
        配合 UI 工具栏『结束招募』勾选, 让"空格那一下"在招募阶段也能收工进战斗;
        在战斗阶段它只跳过、不会误触发结束招募(与 EasySkipBattle.js 同款分派)。
        返回 True/False。"""
        try:
            res = self.send_recv("eval", js="skipBattle()", timeout=4.0)
        except Exception as e:
            self.log(f"[跳过] 全模式调用失败: {e}")
            return False
        if res is True or str(res).strip().lower() == "true":
            self.log("[跳过] skipBattle() 已按阶段分派(跳过战斗 / 结束招募)")
            return True
        self.log(f"[跳过] skipBattle() 未生效(当前阶段不处理): {res}")
        return False

    def end_recruit(self):
        """结束招募(招募阶段提前收工)= 直接调页内 endRecruit()(与 skipBattle 在 phase7 同款)。
        不在招募阶段时页内返回 {ok:false, reason:'不在招募阶段...'}, 属正常, 不计为失败。
        返回 True(已结束招募) / False。"""
        try:
            res = self.send_recv("eval", js="endRecruit()", timeout=4.0)
        except Exception as e:
            self.log(f"[结束招募] 调用失败: {e}")
            return False
        try:
            d = json.loads(res) if isinstance(res, str) else res
        except Exception:
            d = None
        if isinstance(d, dict) and d.get("ok"):
            self.log(f"[结束招募] 已调用 onEndRecruitJump() → 提前进战斗 (phase={d.get('phase')})")
            return True
        why = (d.get("reason") if isinstance(d, dict) else None) or (res if isinstance(res, str) else "") or "未知"
        self.log(f"[结束招募] {why}")
        return False

    def refresh_shop(self, delay=0.6):
        """刷新商店: v4.13★ 改用 R键同款逻辑 (mgr.ReqShopRefreshChess 同步直调), 不再走 psend 协议包
        优先级: 油猟端 mgr.ReqShopRefreshChess() -> 类名直调(兜底)
        """
        # v4.13★ 优先: 调油猟端 mgr.ReqShopRefreshChess() (和 R键完全相同的逻辑)
        try:
            # v4.19★ 修复: 原片段 "(function(){try{ ... })()" 的【外层 try 没有 catch/finally】,
            #   油猴端 eval 直接抛 "Missing catch or finally after try" —— 也就是说这条
            #   "和 R 键同款"的刷新链路从来没真正生效过, 每次都静默掉到下面的 direct_call 兜底。
            #   顺带把递归里的 var 改 let: var 是函数作用域, 递归会互相覆盖 ch/i。
            js_code = ("(function(){"
                       "try{"
                       "var m=(function search(o,v){"
                       "if(!o||v.has(o))return null;v.add(o);"
                       "if(o.constructor&&o.constructor.name==='TavernChessGameScene')return o;"
                       "var ch=o._children||o.children||o.childList||[];"
                       "for(let i=0;i<ch.length;i++){let r=search(ch[i],v);if(r)return r;}"
                       "return null;})(Laya.stage,new WeakSet());"
                       "if(!m||!m.manager)return 'ERR:no manager';"
                       "if(typeof m.manager.ReqShopRefreshChess!=='function')return 'ERR:no ReqShopRefreshChess';"
                       "m.manager.ReqShopRefreshChess();"
                       "return 'true';"
                       "}catch(e){return 'ERR:'+e.message;}"
                       "})()")
            _t0 = int(time.time() * 1000)
            res = self.send_recv("eval", js=js_code, timeout=4.0)
            if res and res.strip().lower() == "true":
                time.sleep(delay)
                # ★ 方法调用成功 ≠ 包发出去了。官方源码里这道门禁会让它一个字节都不发:
                #       ReqShopRefreshChess(e=!1){ if(!e && !this.CanOperate) return; }
                #   以前这里直接 return True, 上层就傻等一个永远不来的回包。
                if not self.check_sent("ShopRefreshChess", _t0, timeout=1.0):
                    self.log("[刷新] ⚠ ReqShopRefreshChess 调了, 但没见到包发出去 —— "
                             "多半是当前不允许操作(CanOperate=false), 别再等回包了")
                    return False
                self.log("[刷新] mgr.ReqShopRefreshChess() 成功 (R键同款逻辑, 已确认发包)")
                return True
            else:
                self.log(f"[刷新] mgr.ReqShopRefreshChess 返回: {res}")
        except Exception as e:
            self.log(f"[刷新] mgr.ReqShopRefreshChess 失败: {e}")
        # 兜底: 类名直调
        kw = r"(?=.*(?:Refresh|Recruit|BtnRecruit|Shuaxin|Flush))(?!.*(?:Cancel|Close|Exit|Dismiss|Quit|Back|Return|Unready|UnReady))"
        if self.direct_call(kw, label="刷新"):
            time.sleep(delay)
            return True
        raise RuntimeError("刷新商店未命中——请确认游戏界面已打开且油猟端已连接")

    def _bench_inject(self, card):
        """买卡成功后本地回放CNtfChessUpdateInfoMsg——游戏内存立即加卡, 备战区立刻显示
        (协议直发跳过了游戏本地加卡步骤, 回放=补上)"""
        try:
            from .protocol import LIVE
            uid = str(getattr(LIVE, "my_id", "") or (LIVE.me or {}).get("userID") or "")
            if not uid:
                self.log("  (备战区回放: 拿不到自己userID——进对局后DataUpdate会建立)")
                return
            payload = {"userID": uid}
            for k in ("goodsID", "chessID", "hp", "attack"):
                if card.get(k) is not None:
                    payload[k] = card[k]
            r = self.send_recv("bench_inject", payload)
            d = json.loads(r)
            if d.get("injected"):
                cd = d.get("card") or {}
                self.log(f"  ✓ 原生加卡 手卡数{d.get('beforeN')}→{d.get('afterN')} "
                         f"卡值{cd.get('hp','?')}/{cd.get('attack','?')}")
            elif d.get("err"):
                self.log(f"  (原生加卡失败: {d.get('err')})")
            elif d.get("hasList") is False:
                self.log("  (本地暂无棋子快照——打1回合或上阵一次后就有)")
        except Exception as e:
            self.log(f"  备战区回放异常: {e.__class__.__name__} {e}")

    def buy_slot(self, index):
        """按槽位索引(0-6)买卡: 协议快照取goodsID -> 协议直发购买 -> 本地回放加卡"""
        i = int(index)
        if not 0 <= i <= 6:
            return False
        try:
            from .protocol import LIVE
            shop = LIVE.shop_snapshot()
            g = shop[i] if i < len(shop) else None
            if g and g.get("_sold"):
                self.log(f"槽位{i + 1}的卡已被买走(空槽)——刷新商店后再买")
                return False
            if g and g.get("goodsID"):
                gid = int(g["goodsID"])
                nm = IDMAP.name_of(g.get("chessID")) or str(g.get("chessID") or "")
                if self.psend("cmsg.CReqShopBuyChess", {"goodsID": gid}):
                    self.log(f"协议购买 槽位{i + 1} {nm} goodsID={gid}")
                    self._bench_inject(g)
                    return True
                self.log(f"[买卡] goodsID={gid} 被服务器拒绝——商店可能刚刷新过, 再点一次按新快照买")
                return False
            self.log(f"[买卡] 快照没有槽位{i + 1}的数据——先刷新一次商店(让协议推一次商店数据)")
        except Exception:
            _SIL.note("core/remote_ops.py:2322", "from .protocol import LIVE")
        self.log("[买卡] 协议通道异常——重启助手后进对局再试")
        return False

    def buy_goods(self, goodsID, delay=0.5):
        """按 goodsID 买卡: 协议直发(主) -> 未学pid才引导学习(避免每次买都弹提示).
        ★pid学习持久化到 localStorage.__sgsPidMap, 脚本v3.20+会自动读回来, 学会一次管永久.
        ★额外加 delay 参数(调用方传), 买完 sleep(delay) 给协议响应留时间, 避免连续协议快于LIVE.feed."""
        import time as _t
        gid = int(goodsID)
        _t0 = int(time.time() * 1000)
        if self.psend("cmsg.CReqShopBuyChess", {"goodsID": gid}):
            # ★ psend 返回 True 只说明"发送动作执行了", 不代表包真的出去了 —— 问一句。
            #   (这里只告警不阻断: 下面本来就有一段"看这张卡有没有从商店消失"的轮询,
            #    那才是买成功的真正判据; 这一句只是让问题更早出现在日志里, 别等半天才发现)
            if not self.check_sent("ShopBuyChess", _t0, timeout=1.0):
                self.log("[买卡] ⚠ CReqShopBuyChess 没有真正发出 —— 后面大概率买不到")
            try:
                from .protocol import LIVE
                card = next((g for g in LIVE.shop_snapshot() if str(g.get("goodsID")) == str(gid)), None)
                if card:
                    self._bench_inject(card)
            except Exception:
                _SIL.note("core/remote_ops.py:2339", "from .protocol import LIVE")
            if delay > 0:
                # v4.56★ 原来是固定 sleep(delay) 死等 —— 每张卡都白等满 0.4~0.5 秒。
                #   实机测过协议往返只要 ~100ms, 固定等待等于每张卡浪费 300ms(连着买 6 张就 1.8 秒,
                #   用户说的"辅助操作延迟有点高"主要就是这里)。
                #   改成【轮询确认】: 每 50ms 看一眼商店快照里这张还在不在, 一买走就立刻继续。
                #   这也正好答了上面那句注释的顾虑 —— 我们等的本来就是 LIVE.feed 把这张标成已买走;
                #   拿不到快照 / 一直不消失时退回死等, 所以最坏情况与原来完全一致, 只会变快不会变差。
                #   末尾再保 80ms 最小间隔: 两次购买协议贴得太近时, 后一个响应可能盖掉前一个的处理。
                t0 = _t.time()
                try:
                    from .protocol import LIVE
                    t_end = t0 + float(delay)
                    while _t.time() < t_end:
                        gone = not any(str(g.get("goodsID")) == str(gid)
                                       for g in LIVE.shop_snapshot())
                        if gone:
                            break
                        _t.sleep(0.05)
                except Exception:
                    _t.sleep(delay)
                left = 0.08 - (_t.time() - t0)
                if left > 0:
                    _t.sleep(left)
            return True
        # 只在真的"pid未学"时引导: 先查 pid_query, 返回空/null 才提示学习
        if not self._pid_learned("cmsg.CReqShopBuyChess"):
            self.log("[买卡] CReqShopBuyChess pid未学——请手动点1次商店里的任意卡购买(学习后永久)")
            if self.learn_proto("cmsg.CReqShopBuyChess",
                                f"手动点游戏商店里任意1张卡买下(学会后助手协议买 goodsID={gid})"):
                if self.psend("cmsg.CReqShopBuyChess", {"goodsID": gid}):
                    if delay > 0: _t.sleep(delay)
                    return True
        else:
            # pid已学但 psend 仍失败。
            # v4.22★ 这里原来是"一口咬定快照过期", 会严重误导排错:
            #   服务器真要拒绝(卡已卖/金币不足)时会回一条 CResp(带 errCode);
            #   而"1.2 秒内完全没有响应"几乎只能是 **pid 不对**(服务器根本不认识这个包)。
            #   实机踩过: pid 被跨协议错配写坏后买卡永久失败, 日志却一直显示"快照过期"。
            self._buy_fail_streak = getattr(self, "_buy_fail_streak", 0) + 1
            info = self.proto_pid_info("cmsg.CReqShopBuyChess") or {}
            self.log(f"[买卡] goodsID={gid} 未买到——服务器无响应/拒绝 "
                     f"(当前学习的 pid={info.get('pid')}, 连续失败 {self._buy_fail_streak} 次)")
            if self._buy_fail_streak >= 3:
                self._buy_fail_streak = 0
                self.log("[买卡] ⚠ 连续 3 次失败 —— 疑似 pid 记录被跨协议错配写坏, 尝试自愈...")
                # 先清掉可疑记录: findPid 会回退到游戏自带的权威 pid 表(ProtoBufId/proto字典),
                # 若权威表里有该协议, 清掉后无需用户操作即可立即恢复。
                self.forget_proto_pid("cmsg.CReqShopBuyChess")
                if self.psend("cmsg.CReqShopBuyChess", {"goodsID": gid}):
                    self.log("[买卡] ✓ 清除被写坏的 pid 后立刻成功 —— 已回退到游戏自带权威 pid 表。"
                             "(该协议暂未固化到本地; 需要持久化可手动买1张卡重新学习)")
                    if delay > 0:
                        _t.sleep(delay)
                    return True
                self.log("[买卡] 清除后仍失败 —— 请手动点商店里任意 1 张卡买下, 正在等待重新学习...")
                self.learn_proto("cmsg.CReqShopBuyChess",
                                 "手动点商店里任意1张卡买下(修复被写坏的 pid)", force=True)
            return False
        self.log(f"[买卡] goodsID={gid} 未买到——商店可能刚刷新(卡已不在)")
        return False

    def sell_card(self, goodsID):
        """遣散(卖)手卡: 协议直发CReqShopRecycleChess → 成功后立即更新LIVE本地状态.
        ★修复: pid已学时learn_proto不再误触发(之前_learn_proto学习路径在pid已学后仍被调用→日志喷'手动卖1张').
        ★逻辑: psend成功直接返回; 失败时 pid未学 才弹学习; pid已学但失败=快照过期/服务器拒绝."""
        import time as _t
        gid = int(goodsID)
        from .protocol import LIVE
        card_info = None
        for c in (LIVE.me.get("_board") or []):
            if isinstance(c, dict) and str(c.get("goodsID")) == str(gid):
                card_info = c; break
        if self.psend("cmsg.CReqShopRecycleChess", {"goodsID": gid}):
            # ★协议真成功 → 立即从 _board 里删掉(不等油猴推响应, 防断连)
            try:
                LIVE._remove_from_board(goodsID=gid)
                cid = (card_info or {}).get("chessID")
                if cid:
                    key = str(cid)
                    cur = LIVE.pool_taken.get(key, 0)
                    if cur > 0:
                        LIVE.pool_taken[key] = cur - 1
            except Exception:
                _SIL.note("core/remote_ops.py:2423", "LIVE._remove_from_board(goodsID=gid)")
            _t.sleep(DISMISS_GAP_S)   # v4.108★ 0.3 → 0.08(见文件头常量)
            return True
        # ★关键: pid未学 才走学习路径
        if not self._pid_learned("cmsg.CReqShopRecycleChess"):
            self.log("[遣散] CReqShopRecycleChess pid未学——请在游戏里手动卖1张(学习后永久)")
            if self.learn_proto("cmsg.CReqShopRecycleChess",
                                f"手动在游戏里卖掉(遣散)1张武将卡(学会后助手协议遣散 goodsID={gid})"):
                if self.psend("cmsg.CReqShopRecycleChess", {"goodsID": gid}):
                    try:
                        LIVE._remove_from_board(goodsID=gid)
                    except Exception:
                        _SIL.note("core/remote_ops.py:2435", "LIVE._remove_from_board(goodsID=gid)")
                    _t.sleep(0.3)
                    return True
            self.log("[遣散] 协议通道未通——按上面提示手动卖1张完成学习")
            return False
        # pid已学, 但服务器拒绝: goodsID不在备战区
        self.log(f"[遣散] goodsID={gid} 失败 pid已学但服务器拒绝——卡可能已经不在备战区(goodsID过期)")
        return False

    def deploy_bench(self, bench_index=1):
        """上阵: 把备战区卡加进战斗区阵容(支持一次上阵多张, 默认第1张未上阵的).
        协议实锤: CReqChessLineUp {positionChess:[goodsID...]} —— 固定7格, 空位填0
        ★关键: positionChess 必须是 [int, int, int, ...] 数字数组, 字符串服务器直接丢弃!
        v3.43扩展: 当传 bench_index="all" 或 0 → 把备战区所有未上阵卡挨个上阵(直到战斗区满)
                → 上阵后 sleep 0.4s 等 CHESS_ADD_BUFF 推送, 让【当先】等被动/主公技能触发效果.
        ★【当先】(凌统/张济等主公技能: 上阵棋子时对敌方造成N伤害)=服务器端被动,
                只要 positionChess 协议发出且服务器验证verified=true, 效果就会触发,
                本地不需要额外调用. 之前"不触发"根因=pid未正确发送/positionChess不是数字数组."""
        try:
            import time as _t
            from .protocol import LIVE
            # 战斗阶段不能上阵
            if LIVE.phase == "battle":
                self.log("[上阵] 战斗阶段不能上阵——等招募阶段")
                return False
            # "all" 或 0 → 上阵多张模式
            deploy_all = (bench_index in (0, "all", "ALL"))
            me = LIVE.me or {}
            bench = list(me.get("_board") or [])
            line = list(me.get("_lineUp") or [])
            # line 是固定7格, 先转int再过滤
            line_ints = [int(x) if x not in (None, "", "0") else 0 for x in line]
            non_zero = [x for x in line_ints if x != 0]
            def _deploy_one(card):
                """单张上阵: 返回 (成功?, 新line_ints)"""
                nonlocal line_ints, non_zero
                cid = card.get("chessID")
                gid = card.get("goodsID")
                use_id = int(gid or cid)
                if not cid and not gid:
                    return False, line_ints
                if use_id in non_zero:
                    return False, line_ints   # 已上阵
                if len(non_zero) >= 7:
                    return False, line_ints
                # 构造新7格
                new_line = list(line_ints)
                placed = False
                for i in range(len(new_line)):
                    if new_line[i] == 0:
                        new_line[i] = use_id; placed = True; break
                if not placed:
                    if len(new_line) < 7:
                        new_line.append(use_id)
                        while len(new_line) < 7: new_line.append(0)
                        placed = True
                    else:
                        return False, line_ints
                if not placed:
                    return False, line_ints
                # 协议直发
                # v4.108★ 先记基线: 只认本次上阵【之后】才产生的 buff
                #   (刷邓茂这种同一张卡反复上阵的打法, 不记基线就会命中上一轮的旧事件)
                _t0 = _t.time()
                nm = IDMAP.name_of(cid) or ""
                if self.psend("cmsg.CReqChessLineUp", {"positionChess": new_line, "changeSeat": False}):
                    self.log(f"[上阵] {nm}(goodsID={gid}) → 战斗区{len([x for x in new_line if x != 0])}个")
                    # 本地同步
                    try:
                        LIVE.me["_lineUp"] = new_line
                        LIVE.me["_board"] = [c for c in (LIVE.me.get("_board") or [])
                                             if not (str(c.get("chessID")) == str(cid)
                                                     and str(c.get("goodsID")) == str(gid))]
                    except Exception:
                        _SIL.note("core/remote_ops.py:2509", "LIVE.me['_lineUp'] = new_line")
                    # v4.108★ 上阵后等待【当先】等被动效果
                    #   这段原先缩进掉出了 _deploy_one(12空格), 引用了外层看不见的 new_line →
                    #   NameError 被最外层 except 吞掉, 所以"当先等待"从来没真正生效过。
                    #   现在放回 psend 成功分支内(20空格)并接上基线 _t0。
                    line_ints = new_line
                    non_zero = [x for x in line_ints if x != 0]
                    if int(cid or 0) in _dangxian_chess_ids():
                        ok_buff, _ = LIVE.wait_buff_for(int(cid), timeout_ms=1200, since_ts=_t0)
                        if not ok_buff:
                            self.log(f"[当先] {nm}(chessID={cid}) 本次 buff 未及时收到(继续)")
                    else:
                        _t.sleep(DEPLOY_GAP_S)
                    return True, line_ints
                # psend 未确认(pid 不对 / tableID 过期) → 明确失败, 别假装成功
                return False, line_ints
            # 无备战卡
            if not bench:
                self.log("[上阵] 备战区没卡——先买一张卡或等协议推送")
                return False
            if not non_zero:
                # 首阵: 先拿第一张布阵
                card = bench[0]
                ok, _ = _deploy_one(card)
                if ok and deploy_all:
                    _t.sleep(0.4)
                else:
                    return ok
            # 候选: 未上阵过的(不匹配goodsID且不匹配chessID双重防重复)
            cands = [c for c in bench
                     if isinstance(c, dict)
                     and int(c.get("goodsID") or 0) not in non_zero
                     and int(c.get("chessID") or 0) not in non_zero]
            if not cands and not deploy_all:
                # 有line,但bench没有候选 → 报"都已上阵"
                # (首阵模式上面已处理)
                if non_zero:
                    self.log("[上阵] 备战区的卡都已上阵")
                    return False
            deployed_cnt = 0
            if deploy_all:
                # 上阵所有候选(每次上阵一张)
                # 动态刷新 bench 与 cands
                while True:
                    if len(non_zero) >= 7:
                        break
                    me_curr = LIVE.me or {}
                    bench_curr = list(me_curr.get("_board") or [])
                    cands_curr = [c for c in bench_curr
                                  if isinstance(c, dict)
                                  and int(c.get("goodsID") or 0) not in non_zero
                                  and int(c.get("chessID") or 0) not in non_zero]
                    if not cands_curr:
                        break
                    next_card = cands_curr[0]
                    ok, line_ints = _deploy_one(next_card)
                    if ok:
                        deployed_cnt += 1
                        _t.sleep(0.45)   # 等被动buff推送/服务器广播
                    else:
                        break
                if deployed_cnt > 0:
                    self.log(f"[上阵-all] 完成 共上阵{deployed_cnt}张")
                    return True
                if non_zero:
                    self.log("[上阵] 备战区的卡都已上阵")
                return False
            # 单张模式: 取 bench_index-1 号候选
            if not cands:
                # 之前没首阵成功, 说明都上阵
                self.log("[上阵] 备战区的卡都已上阵")
                return False
            if len(non_zero) >= 7:
                self.log("[上阵] 战斗区已满7人——需先遣散1张")
                return False
            card = cands[max(0, min(int(bench_index or 1) - 1, len(cands)) - 1)] \
                if int(bench_index or 1) > 1 else cands[0]
            # 用_deploy_one内部, 但上面cands已通过non_zero过滤重复, 直接改协议版
            cid = card.get("chessID"); gid = card.get("goodsID")
            use_id = int(gid or cid)
            nm = IDMAP.name_of(cid) or ""
            new_line = list(line_ints)
            for i in range(len(new_line)):
                if new_line[i] == 0:
                    new_line[i] = use_id; break
            while len(new_line) < 7:
                new_line.append(0)
            if self.psend("cmsg.CReqChessLineUp", {"positionChess": new_line, "changeSeat": False}):
                self.log(f"[上阵] {nm}(goodsID={gid}) → 战斗区{len([x for x in new_line if x != 0])}个")
                try:
                    LIVE.me["_lineUp"] = new_line
                    LIVE.me["_board"] = [c for c in (LIVE.me.get("_board") or [])
                                         if not (str(c.get("chessID")) == str(cid)
                                                 and str(c.get("goodsID")) == str(gid))]
                except Exception:
                    _SIL.note("core/remote_ops.py:2604", "LIVE.me['_lineUp'] = new_line")
                _t.sleep(0.4)   # 等被动【当先】/【破军】等触发效果
                return True
            self.log("[上阵] 服务器未确认——pid可能不对或tableID过期, 手动上阵一次学习")
            return False
        except Exception as e:
            self.log(f"[上阵] 异常: {e.__class__.__name__} {e}")
            return False

    def use_spells(self):
        """一键用锦囊(所有类型). v3.43★完整重写:
        识别 = LIVE.list_spell_cards() → 6重规则覆盖所有手卡中的锦囊/道具/主公令/合成产物.
        使用 = 4 层优先级逐一尝试:
         (A) 协议直发 CReqChessUseSpell (零UI, 最稳): 逐个 goodsID 消耗;
         (B) 打开锦囊面板 → 逐张卡面节点点击(面板里卡项的 onUseSpell 事件);
         (C) 面板出来后 选目标/选将 2 步交互: 先点候选武将卡(TavernChessSpellChess), 再点确认按钮;
         (D) 如果还是没反应: direct_call 锦囊面板内部节点的 _onUseSpell / onClickUse / handleUseSpell 方法.
        每张锦囊使用后 sleep(0.55s) 等待协议推送, 避免漏响应.
        注意: 【当先/合金获得】是部署棋子时被动推送的效果, 不在 use_spells 范畴;
              use_spells 只管点击"用锦囊"按钮 / 用协议消耗道具类锦囊."""
        import time as _t
        from .protocol import LIVE
        # ==================== (1) 先枚举全部锦囊 ====================
        with LIVE.lock:
            spells = LIVE.list_spell_cards()
        # 如果 LIVE 识别不到, 再做一层UI扫描兜底(枚举道具卡):
        if not spells:
            self.log("[锦囊] LIVE里暂无锦囊标记 → 尝试UI扫描(可能是刚买/推送还在路上)")
            scan_cls_re = (r"(?=.*(?:TavernChessSpellCard|SpellCardUI|ChessSpellItemUI"
                           r"|TavernChessActiveUseSpellUI|TavernChessSpellChess|ChessUseSpellCard"
                           r"|JinnangItem|JnItem|JinNangCard|TavernChessItem|ItemCardUI))"
                           r"(?!.*(?:Cancel|Close|Exit|Dismiss|Quit|Back|Return))")
            try:
                items_raw = self.wait_result(self.send("btn_list", cls=scan_cls_re,
                                                       all_nodes=True, depth=18), 4.0) or "[]"
                nodes = json.loads(items_raw) if isinstance(items_raw, str) else items_raw
                if isinstance(nodes, list) and nodes:
                    self.log(f"[锦囊] UI扫描发现 {len(nodes)} 个锦囊节点")
                    # 直接点UI节点(逐个点, 不要点confirm, 点了卡节点内部会调onUseSpell)
                    ok_nodes = 0
                    for nd in nodes[:20]:
                        cls = str(nd.get("cls") or "").strip() or None
                        path = nd.get("xpath") or nd.get("path") or None
                        name = nd.get("name") or None
                        if self.direct_call("^" + cls + "$" if cls else None,
                                            name=name, path=path):
                            ok_nodes += 1
                            _t.sleep(0.55)
                    if ok_nodes:
                        self.log(f"[锦囊] UI逐节点点击成功 x{ok_nodes}")
                        # 点完后面板一般会弹"选目标", 做确认
                        self._spell_panel_confirm("UI扫描消耗后")
                        return True
            except Exception as e:
                self.log(f"[锦囊] UI扫描兜底异常: {e.__class__.__name__}:{e}")
            self.log("[锦囊] 没有识别到任何可使用的锦囊(手卡/道具为空 or 未进入招募阶段)")
            return False
        # ==================== (2) 打所有锦囊 ====================
        self.log(f"[锦囊] 检测到 {len(spells)} 张: " +
                 ", ".join(self._spell_describe(s) for s in spells))
        used_count = 0
        for sp in spells:
            gid_raw = sp.get("goodsID")
            gid = None
            try:
                gid = int(gid_raw) if gid_raw is not None else None
            except (TypeError, ValueError):
                gid = None
            cid = sp.get("chessID")
            # ---- (A) 协议直发 CReqChessUseSpell ----
            ok = False
            if gid:
                # CReqChessUseSpell: 大多数"道具消耗类锦囊"直接这么用就够了
                # 字段名可能是 spellGoodsID / goodsID / spellChessGoodsID, 全发一遍
                for sample in ({"spellGoodsID": gid}, {"goodsID": gid},
                               {"spellChessGoodsID": gid, "spellGoodsID": gid}):
                    try:
                        if self.psend("cmsg.CReqChessUseSpell", sample):
                            ok = True; break
                    except Exception:
                        _SIL.note("core/remote_ops.py:2684", "if self.psend('cmsg.CReqChessUseSpell', sample")
                if ok:
                    used_count += 1
                    self.log(f"[锦囊] (A)协议直发 {self._spell_describe(sp)} ✓")
                    _t.sleep(0.55)
                    # 若这是【选将/选目标】锦囊, 服务器会推 CRespChessSelectSpellChess,
                    # LIVE 已收到并把候选武将放进 _candidates. 我们要选一个(默认选质量最高的)
                    self._spell_pick_candidate(sp)
                    # 面板可能弹出来了 → 再点一次确认(有些锦囊弹窗强制用户点确认才走)
                    self._spell_panel_confirm("协议直发后补确认")
                    continue
            # ---- (B) 面板打开(如果协议没命中) ----
            opened = self._spell_open_panel()
            if opened:
                _t.sleep(0.4)
                # (B1) 面板里点这张"锦囊卡项节点"
                clicked_card = self._spell_click_card_in_panel(sp)
                _t.sleep(0.35)
                if clicked_card:
                    used_count += 1
                    # 选目标
                    self._spell_pick_candidate(sp)
                    # 确认
                    self._spell_panel_confirm("面板里点卡后补确认")
                    _t.sleep(0.3)
                    continue
                # (B2) 没点到具体卡, 尝试直接点"确认按钮"(有些锦囊面板打开就默认选好第一张)
                if self._spell_panel_confirm("面板打开后直接确认"):
                    used_count += 1
                    _t.sleep(0.3)
                    continue
            # ---- (C) 直调节点内部 _onUseSpell / use 事件回调 ----
            if gid or cid:
                self.log(f"[锦囊] (C)直调内部回调 {self._spell_describe(sp)} …")
                ok = self._spell_invoke_use_callback(sp)
                if ok:
                    used_count += 1
                    self._spell_panel_confirm("内部回调后补确认")
                    _t.sleep(0.55)
                    continue
            self.log(f"[锦囊] 失败: 全部4层用完仍未消耗 {self._spell_describe(sp)}")
        if used_count:
            self.log(f"[锦囊] 完成, 共消耗 {used_count}/{len(spells)} 张")
            return True
        self.log("[锦囊] 完成, 但 0 张成功——可能需要先进一局/协议pid未学/类型误判")
        return False

    # ---------- 工具函数: 锦囊描述 ----------
    def _spell_describe(self, sp):
        if not isinstance(sp, dict):
            return "?"
        name = sp.get("name") or sp.get("chessName") or ""
        tag = str(name)[:6] if name else ""
        gid = sp.get("goodsID"); cid = sp.get("chessID")
        parts = []
        if tag: parts.append(tag)
        if gid: parts.append(f"gid={gid}")
        if cid: parts.append(f"cid={cid}")
        for f in ("goodsType", "type", "ChessType", "spellID", "isSpell", "fromSpell",
                  "isJinnang", "isItem", "pos"):
            if sp.get(f) not in (None, "", 0, "0"):
                parts.append(f"{f}={sp[f]}")
        return "[" + " ".join(parts) + "]"

    # ---------- (B层) 打开锦囊面板 ----------
    def _spell_open_panel(self):
        # 面板/按钮类名(全量收集)
        open_kw = (r"(?=.*(?:TavernChessActiveUseSpellUI|TavernChessSpellChessUI|TavernChessUseSpellUI"
                   r"|BtnSpellUse|SpellUseBtn|UseSpellBtn|SpellBtn|JinnangUse|BtnSpellChess"
                   r"|SpellChessBtn|ChessSpellBtn|ChessJinnangBtn|BtnChessSpell|UseJnBtn|"
                   r"|SpellOpenBtn|OpenSpellBtn|ActiveSpellBtn|ItemSpellUse|UseJinNang))"
                   r"(?!.*(?:Cancel|Close|Exit|Dismiss|Quit|Back|Return|Unready|UnReady|Disband))")
        if self.direct_call(open_kw, label="锦囊-打开1"):
            return True
        # 宽松按钮(包含 Spell/Jinnang/道具 的按钮)
        open_kw2 = r"(?=.*(?:Spell|Jinnang|Jn|JinNang|Prop|Item))(?=.*Btn|Button)(?!.*(?:Cancel|Close|Exit|Dismiss|Quit|Back|Return|Unready|UnReady|Disband))"
        if self.direct_call(open_kw2, label="锦囊-打开2"):
            return True
        return False

    # ---------- (B1层) 面板里点击具体锦囊卡 ----------
    def _spell_click_card_in_panel(self, sp):
        gid = sp.get("goodsID"); cid = sp.get("chessID")
        name = str(sp.get("name") or sp.get("chessName") or "")
        # 先按类名找面板里锦囊卡项(找所有可见项)
        card_cls_re = (r"(?=.*(?:TavernChessSpellCard|SpellCardUI|ChessSpellItemUI"
                       r"|ChessSpell|SpellChess|JinnangCard|JinNangItem|ItemCardUI))"
                       r"(?!.*(?:Cancel|Close|Exit|Dismiss|Quit|Back|Return))")
        try:
            raw = self.wait_result(self.send("btn_list", cls=card_cls_re,
                                             all_nodes=True, depth=18,
                                             # 想通过goodsID过滤, 但脚本侧没有过滤能力, 只能全抓回来过滤
                                             ), 4.0) or "[]"
            nodes = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(nodes, list):
                return False
            target = None
            for nd in nodes:
                # 通过 goodsID / name 挑目标
                props = nd.get("props") or {}
                if isinstance(props, dict):
                    ng = props.get("goodsID") or props.get("GoodsID") or props.get("gid")
                    nc = props.get("chessID") or props.get("ChessID") or props.get("cid")
                    nn = props.get("name") or props.get("chessName") or nd.get("text") or ""
                    if gid and ng and str(ng) == str(gid):
                        target = nd; break
                    if cid and nc and str(nc) == str(cid):
                        target = nd; break
                    if name and nn and str(nn).startswith(name):
                        target = nd; break
            if target is None and nodes:
                target = nodes[0]   # 没匹配上, 点面板里第一张
            if target:
                cls = str(target.get("cls") or "").strip() or None
                nm = target.get("name") or None
                path = target.get("xpath") or target.get("path") or None
                ok = self.direct_call("^" + cls + "$" if cls else None, name=nm, path=path)
                if ok:
                    self.log(f"[锦囊] 面板里点卡 {cls or nm or path} ✓")
                    return True
        except Exception as e:
            self.log(f"[锦囊] (B1) 异常: {e.__class__.__name__}:{e}")
        return False

    # ---------- (选将阶段) 服务器给的候选武将里选最佳1张 ----------
    def _spell_pick_candidate(self, sp):
        """当用曹轶锦囊/升级锦囊, 服务器会推 CRespChessSelectSpellChess,
        LIVE 在 protocol.py 对应处把候选列表存进 LIVE._spell_candidates.
        这里选 star最高/攻击最高的一张, 用协议直发 CRespChessSelectSpellChess 确认."""
        try:
            from .protocol import LIVE
            with LIVE.lock:
                cands = list(LIVE.me.get("_spell_candidates") or [])
            if not cands:
                return
            # 选最佳: 先看星级, 再看攻击, 再看血量
            def score(x):
                if not isinstance(x, dict):
                    return (-9999, -9999, -9999)
                s = int(x.get("star") or 0)
                a = int(x.get("attack") or x.get("atk") or 0)
                h = int(x.get("hp") or x.get("Hp") or x.get("HP") or 0)
                return (s, a, h)
            cands_sorted = sorted(cands, key=score, reverse=True)
            best = cands_sorted[0]
            gid = best.get("goodsID") or best.get("GoodsID") or best.get("gid")
            cid = best.get("chessID") or best.get("ChessID") or best.get("cid")
            self.log(f"[锦囊] 候选{cands_sorted.__len__()}张 → 选 {self._spell_describe(best)}")
            # 直发选将确认
            ok = False
            for sample in ({"goodsID": int(gid)} if gid else {},
                           {"chessID": int(cid)} if cid else {},
                           {"goodsID": int(gid), "chessID": int(cid)} if gid and cid else {},
                           {"selectGoodsID": int(gid)} if gid else {}):
                if not sample:
                    continue
                try:
                    if self.psend("cmsg.CRespChessSelectSpellChess", sample):
                        ok = True; break
                except Exception:
                    _SIL.note("core/remote_ops.py:2844", "if self.psend('cmsg.CRespChessSelectSpellChess")
            if ok:
                with LIVE.lock:
                    LIVE.me["_spell_candidates"] = []   # 清掉, 避免重复选
                self.log("[锦囊] 选将成功 ✓")
                # 选完再补一次面板确认(有些要双确认)
                self._spell_panel_confirm("选将完成再确认")
                return
            # 协议直发失败 → 直调UI节点
            self._spell_panel_confirm("选将(直调UI)")
        except Exception as e:
            self.log(f"[锦囊] (选将)异常: {e.__class__.__name__}:{e}")

    # ---------- 面板确认: 点击所有"确定/选完/使用"按钮 ----------
    def _spell_panel_confirm(self, tag=""):
        import time as _t
        confirm_kw = (r"(?=.*(?:ConfirmSpellBtn|SureSpellBtn|SelectSpellBtn|SpellSureBtn"
                      r"|UseSpellConfirm|UseSpellOk|SpellConfirmBtn|OkBtn|SureBtn|ConfirmBtn|SelectBtn"
                      r"|MakeSure|MakeSureBtn|JnConfirm|JinNangOk|SpellOk|UseJinnangOk|"
                      r"|TavernChessSpellConfirm|ChessSpellUseBtn|BtnConfirmUse|BtnUseJinnang))"
                      r"(?!.*(?:Cancel|Close|Exit|Dismiss|Quit|Back|Return|Unready|UnReady|Disband))")
        if self.direct_call(confirm_kw, label=f"锦囊-确认[{tag}]"):
            return True
        _t.sleep(0.35)
        # 再试(首次可能动画未到)
        if self.direct_call(confirm_kw, label=f"锦囊-确认2[{tag}]"):
            return True
        # 兜底: 所有"确认/选择/OK/Sure"文案型按钮(排除Cancel类)
        try:
            items_raw = self.wait_result(self.send("btn_list",
                cls=r"Confirm|Ok|OkBtn|Sure|Select|MakeSure|Spell|Jinnang|Use"), 3.5) or "[]"
            items = json.loads(items_raw) if isinstance(items_raw, str) else []
            if isinstance(items, list):
                for it in items[:8]:
                    vis = it.get("vis") if isinstance(it, dict) else False
                    if vis is False:
                        continue
                    cls = str((it or {}).get("cls") or "").strip() or None
                    nm = (it or {}).get("name") or None
                    path = (it or {}).get("xpath") or (it or {}).get("path") or None
                    if self.direct_call("^" + cls + "$" if cls else None, name=nm, path=path):
                        self.log(f"[锦囊] 兜底确认点了 {cls or nm or path} ✓")
                        return True
        except Exception:
            _SIL.note("core/remote_ops.py:2888", "items_raw = self.wait_result(self.send('btn_li")
        return False

    # ---------- (C层) 直调节点内部 _onUseSpell / onClick 等事件回调 ----------
    def _spell_invoke_use_callback(self, sp):
        """对带 goodsID/chessID 的卡: 通过 direct_call 特殊语法直接执行其内部 onUseSpell/onClickUse
        函数(一般 Laya 卡面组件都会挂载 onClickUseSpell / onUse 之类).
        这里我们没有更细的 node→goodsID 映射, 所以退化成 broad 调用:
        让脚本侧遍历显示树内所有带 onUseSpell/useSpell/handleUseSpell 回调的节点都触发一次."""
        # direct_call 的 cls_re 传入一个特殊前缀: "__CALL_ALL_METHODS__:onUseSpell,useSpell,onClickUseSpell,handleUseSpell"
        magic = "__CALL_ALL_METHODS__:onUseSpell,useSpell,onClickUseSpell,handleUseSpell,doUse,onBtnUse,triggerUse"
        return self.direct_call(magic, label=f"锦囊-直调内部回调 {self._spell_describe(sp)}")

    def ready(self):
        """准备: 协议直发(实锤协议名CReqTeamUserReady, 非CReqChessReady!
        onBtnClick源码: this.manager.ReqTeamUserReady(!IsReady) → 切换准备状态)
        sample={tableID, ready:true} 脚本会动态注入tableID
        ★★direct_call 只用精确 TavernChessReadyBtn(自走棋准备按钮专属类名),
        绝不再用 "Ready|Prepare" 这种宽泛词——大厅里有 DismissReadyBtn / UnReadyBtn /
        RoomReadyCloseBtn 等解散/退出按钮也含"Ready"子串, 匹配到就把房间解散了!
        """
        if self.psend("cmsg.CReqTeamUserReady", {"ready": True}):
            return True
        # 空data兜底: 脚本自动用学习到的sample(含ready:true+tableID)
        if self.psend("cmsg.CReqTeamUserReady", {}):
            return True
        # ★仅用精确类名+黑名单. 中文"准备"去掉(页面里很多普通文本节点也能被正则匹配到)
        kw = r"(?=.*(?:TavernChessReadyBtn|BtnTeamReady|AutoChessReadyBtn|ChessTeamReady))" \
             r"(?!.*(?:Cancel|Close|Exit|Dismiss|Quit|Back|Return|Unready|UnReady|Disband|Kick|Leave))"
        if self.direct_call(kw, label="准备"):
            return True
        self.log("[准备] 未命中——进一局后完成一次协议学习引导(协议已能直发, 无需类名兜底)")
        return False

    def fetch_opponent_board(self, userID):
        """请求某对手的上回合阵容(点击对手头像时服务器返回 CRespChessGetLastBattlePlayerData)
        需要协议学习 CReqChessGetLastBattlePlayerData 的 pid——没学过返回 None"""
        if not self._pid_learned("cmsg.CReqChessGetLastBattlePlayerData"):
            return None   # 没学过, 静默跳过(用户点对手头像时自动学)
        return self.psend("cmsg.CReqChessGetLastBattlePlayerData", {"userID": str(userID)})

    # =============== v3.44★ 三区域扫描 / 加速Flag / 性能分析 / 武将库 / 遣散策略 ===============

    # ---------------- 三区域 DOM 权威扫描 ----------------
    def rescan_areas(self, silent=False, mode=None, timeout=None, **kwargs):
        """触发脚本 scan_areas -> 结果经 STATE 返回 JSON: {battle:[7], bench:[], shop:[], ts:...}.
        v3.56★ 优先使用 discover_logical_paths 探测到的真实逻辑地址。
        成功后自动写进 LIVE.apply_dom_snapshot 并返回写入统计。
        v4.17★ 兼容 UI 侧 rescan_areas(mode="dom_then_proto", timeout=3.5) 的调用形式。
               之前签名只有 silent, 传 mode/timeout 会直接 TypeError → F6/F7 一开局就崩
               ("got an unexpected keyword argument 'mode'")。
               mode 目前仅作兼容占位(统一走 DOM 权威扫描), timeout 控制等待秒数。"""
        from .config import load_config
        CONFIG = load_config()
        # v3.64★ 修正路径获取: 从 sub_regions.logical_paths 获取探测到的地址
        lp = CONFIG.get("sub_regions", {}).get("logical_paths") or {}
        # v4.102★ 扫描前补齐备战区: 逻辑地址指向的数组常常是空的(服务器不推 benchCards),
        #   而卡在 selfInfo.newLyHandChess 上。先把活体手牌写进 _board, 再扫,
        #   否则 DOM 快照恒为 bench:[]。force=False —— _board 已有内容就不打扰。
        try:
            self.sync_board_from_hand(force=False)
        except Exception:
            _SIL.note("core/remote_ops.py:2950", "self.sync_board_from_hand(force=False)")
        
        cid = self.send("scan_areas", logical_paths=lp)
        res = self.wait_result(cid, float(timeout) if timeout else 5.0)
        if not res:
            if not silent: self.log("[DOM扫描] 超时(需油猴v3.44+且游戏页面打开)")
            return None
        if res.startswith(("ERR", "no_stage")):
            if not silent: self.log(f"[DOM扫描] 失败: {res}")
            return None
        try:
            snap = json.loads(res)
            # v3.67★ 调试日志: 打印原始探测数据，帮用户查 ID 格式
            import json as _json
            if not silent: self.log(f"[DEBUG] DOM 原始数据快照: {_json.dumps(snap, ensure_ascii=False)[:1000]}")
        except Exception as e:
            if not silent: self.log(f"[DOM扫描] 结果解析失败: {e}")
            return None
        from .protocol import LIVE
        stats = LIVE.apply_dom_snapshot(snap)
        if not silent:
            self.log(f"[DOM扫描] 完成: 战斗区{stats.get('battle_written',0)}格 "
                     f"备战区{stats.get('bench_written',0)} 商店{stats.get('shop_written',0)}")
        return stats

    # ---------------- 合成鼠标事件(PIXEL 回放 + 脚本侧派发 Laya 事件) ----------------
    def mouse_click(self, x, y, button="left", down=True):
        """发送合成鼠标点击到脚本侧(派发到Laya舞台). 失败静默返回False."""
        cid = self.send("mouse_click", x=int(x), y=int(y),
                        button=str(button or "left"), down=bool(down))
        res = self.wait_result(cid, 2.0)
        return bool(res) and res.startswith("ok")

    # ---------------- Tween Hook / 批量协议 Flag 同步推送 ----------------
    def flags_set(self, flags_dict):
        """把 flags_dict(tweenScale/recruitFastTween/batchProtocol) 推到脚本侧.
        (App._flags_apply 会 persist 到 config.json, 这里只负责脚本同步.)"""
        if not isinstance(flags_dict, dict):
            return False
        cid = self.send("flags_set", flags=dict(flags_dict))
        res = self.wait_result(cid, 4.0)
        ok = bool(res) and not str(res).startswith("ERR")
        self.log(f"[加速Flag] 脚本侧: {res if ok else '未响应' }")
        return ok

    def set_triple_combine(self, enable: bool):
        """设置三连合成控制. 调用油猴脚本的 window.blockTripleCombine 变量. """
        if not isinstance(enable, bool):
            self.log(f"[设置三连合成] 失败: enable 必须是布尔值, 收到 {enable}")
            return False
        
        # blockTripleCombine = true 表示阻止合成，false 表示允许合成
        # 所以这里 enable = true 对应 blockTripleCombine = true
        # enable = false 对应 blockTripleCombine = false
        js_code = f"window.blockTripleCombine = {str(enable).lower()}"
        cid = self.send("eval", js=js_code)
        res = self.wait_result(cid, 2.0)
        
        ok = bool(res) and str(res).lower() == "true"
        if ok:
            self.log(f"[设置三连合成] 成功: 已设置为 {'阻止' if enable else '允许'} 三连合成")
        else:
            self.log(f"[设置三连合成] 失败: 脚本未响应或返回错误: {res}")
        return ok

    def combine_specific(self, ids, timeout=5.0):
        """v4.184★ 指定三连: 直接调游戏原始合金入口(绕过 blockTripleCombine 全局开关)。

        不需要把全局三连开关打开 —— 即使脚本运行时禁用三连(囤卡), 也能对【指定卡】强制合金。
        例: combine_specific([gid1, gid2, gid3]) 把这三张直接合金(斗转999 这类提效明显)。
        依赖桥 JS v4.184+ 暴露的 window.sgsCombineSpecific(ids)。
        """
        ids = [int(x) for x in ids]
        if not ids:
            self.log("[指定三连] 未提供任何卡 id")
            return False
        js = "window.sgsCombineSpecific(%s)" % json.dumps(ids)
        cid = self.send("eval", js=js)
        res = self.wait_result(cid, timeout)
        # 桥端 sgsCombineSpecific 回的是 JSON.stringify({ok:true,...}); 解析判定更稳, 不依赖空格/大小写
        ok = False
        if res:
            try:
                d = json.loads(res)
                ok = bool(d.get("ok")) and "err" not in d
            except Exception:
                ok = ('"ok":true' in res.replace(" ", "")) and '"err"' not in res
        self.log("[指定三连] ids=%s → %s" % (ids, (res if res is not None else "<超时>")))
        return ok

    def combine_named(self, board, names):
        """v4.184★ 给定棋盘(含 name/goodsID 的卡列表)与目标名字(可重复), 解析 ids 并指定三连。

        board 形如 [{"name":"孙策","goodsID":111}, ...]; 用于离线/预解析, 不读游戏实时状态。
        名字按「去·与空格 + 小写」归一, 与桥端 _scFindChessIds 同口径。
        """
        norm = lambda s: "".join(ch for ch in str(s) if ch not in "· ").lower()
        by_name = {}
        for c in (board or []):
            nm = norm(c.get("name"))
            gid = int(c.get("goodsID") or c.get("chessID") or 0)
            if nm and gid:
                by_name.setdefault(nm, []).append(gid)
        picked = []
        for nm in names:
            lst = by_name.get(norm(nm))
            if lst:
                picked.append(lst.pop(0))
            else:
                self.log("[指定三连] 棋盘里找不到「%s」" % nm)
        if len(picked) < 2:
            self.log("[指定三连] 解析出的可合卡不足(需要>=2): %s" % picked)
            return False
        return self.combine_specific(picked)

    def set_skip_triple_ani(self, enable: bool):
        """v4.176★ 设置【跳过三连合成动画】—— 下发脚本的 window.SGS_SKIP_TRIPLE_ANI.

        与上面 set_triple_combine 名字像, 但干的不是一件事, 别混:
          · set_triple_combine   = 三连【要不要发生】(阻止合成, 囤卡用, blockTripleCombine);
          · set_skip_triple_ani  = 已经发生的那次三连, 那【段动画要不要等】。
        为什么会出现"要跳过动画"这种需求: 动画播放期间游戏把 CanOperate 置 false、
        IsPlayCompositeAni 置 true, 剧本每一步和挑牌都要等它放完 —— 一次三连能白等好几秒。
        走的是脚本自己的指令(不是 eval): 脚本会把当前值一起带回来, 免得"发了但没生效"
        在界面上看不出来。
        """
        if not isinstance(enable, bool):
            self.log(f"[跳过三连动画] 失败: enable 必须是布尔值, 收到 {enable}")
            return False
        try:
            res = self.send_recv("set_skip_triple_ani", {"enable": enable}, timeout=2.0)
        except Exception as e:
            self.log(f"[跳过三连动画] 下发失败: {e}")
            return False
        txt = "" if res is None else str(res)
        if txt.startswith("ERR") or txt.startswith("ERR:"):
            self.log(f"[跳过三连动画] 脚本侧报错: {txt}")
            return False
        try:
            got = json.loads(txt) if txt.strip().startswith("{") else {}
        except Exception:
            got = {}
        if got.get("ok"):
            now = bool(got.get("skipTripleAni"))
            self.log("[跳过三连动画] 已%s%s" % (
                "开启" if now else "关闭",
                "" if now == enable else "(⚠ 脚本回报的值与下发不一致)"))
            return now == enable
        if txt.strip() in ("", "null", "undefined"):
            # 老脚本不认识这条指令时会走到这里 —— 说清怎么办, 别让人以为"开关点了没用"
            self.log("[跳过三连动画] 脚本没响应: 多半是脚本版本低于 v4.176, "
                     "点助手工具栏【⚡一键注入】更新后回游戏页 F5")
            return False
        self.log(f"[跳过三连动画] 未预期的返回: {txt[:120]}")
        return False

    def set_spell_need_pick(self, names):
        """v4.176★ 设置【用锦囊后需补挑的锦囊名单】(下发 window.SPELL_NEED_PICK)。

        与 set_skip_triple_ani 一样走脚本自己的指令(不是 eval): 脚本把当前值带回来,
        避免"发了但没生效"看不出来。名单决定哪些锦囊在用出去之后还需要手动再挑一次
        (草船/三顾最常见) —— 脚本用完会自动补挑(见 sgs_bridge 的 _scAutoPickAfterSpell)。

        names: 逗号分隔的字符串(如 "草船,三顾") 或 list。空/None = 恢复脚本默认(草船/三顾)。
        """
        if names is None:
            payload = {"names": []}
        elif isinstance(names, (list, tuple)):
            payload = {"names": [str(x) for x in names]}
        else:
            payload = {"names": str(names)}
        try:
            res = self.send_recv("set_spell_need_pick", payload, timeout=2.0)
        except Exception as e:
            self.log(f"[锦囊需补挑名单] 下发失败: {e}")
            return False
        txt = "" if res is None else str(res)
        if txt.startswith("ERR") or txt.startswith("ERR:"):
            self.log(f"[锦囊需补挑名单] 脚本侧报错: {txt}")
            return False
        try:
            got = json.loads(txt) if txt.strip().startswith("{") else {}
        except Exception:
            got = {}
        if got.get("ok"):
            now = got.get("names")
            if now is None:
                self.log("[锦囊需补挑名单] 已恢复脚本默认(草船,三顾)")
            else:
                self.log("[锦囊需补挑名单] 已设置为: " + ", ".join(str(x) for x in now))
            return True
        if txt.strip() in ("", "null", "undefined"):
            self.log("[锦囊需补挑名单] 脚本没响应: 多半是脚本版本低于 v4.176, "
                     "点助手工具栏【⚡一键注入】更新后回游戏页 F5")
            return False
        self.log(f"[锦囊需补挑名单] 未预期的返回: {txt[:120]}")
        return False

    # ---------------- 性能分析(网页实测) ----------------
    def refresh_battle_view(self):
        """调用油猴脚本的 refreshBattleView 函数刷新战斗区UI.
        """
        js_code = "refreshBattleView('python_cmd')"
        cid = self.send("eval", js=js_code)
        res = self.wait_result(cid, 2.0)
        ok = bool(res) and str(res).lower() == "true"
        if ok:
            self.log("[刷新战斗区] 成功")
        else:
            self.log(f"[刷新战斗区] 失败: 脚本未响应或返回错误: {res}")
        return ok

    def refresh_hand_view(self, goods_id=None):
        """刷新手牌区 UI (重建手牌列表 + 校正布局).

        v4.21★ 修复: 原来 eval 的是 refreshHandView(...), 但它只是油猴 IIFE 内部的局部函数,
                 在 eval 的全局作用域里根本不存在 → ReferenceError → 结果 undefined,
                 所以日志永远停在「[刷新手牌区] 失败: 脚本未响应或返回错误: undefined」。
                 正确入口是 window.AutoBuyAndSell.refreshHandCards()(v4.17 新增, 内部走
                 refreshHandCardsOnly: 先恢复被脚本隐藏的节点, 再重建列表+校正布局)。
        goods_id 参数保留兼容(当前实现是整体轻量刷新, 不针对单张卡)。
        """
        js_code = ("return (window.AutoBuyAndSell && typeof window.AutoBuyAndSell.refreshHandCards === 'function')"
                   " ? window.AutoBuyAndSell.refreshHandCards() : false")
        cid = self.send("eval", js=js_code)
        res = self.wait_result(cid, 2.0)
        ok = bool(res) and str(res).lower() == "true"
        if ok:
            self.log("[刷新手牌区] 成功")
        else:
            self.log(f"[刷新手牌区] 失败: 脚本未响应或返回错误: {res}")
        return ok

    def refresh_hand_once(self):
        """手牌哨兵【手动触发一次】(v4.143★)。

        跟 refresh_hand_view 的区别: 那个是"无条件重建一遍手牌列表"; 这个是走哨兵 ——
        顺带把手牌张数记进哨兵状态(下次常驻轮询或再点一次时能看出"变没变"), 并且
        日志里会打出当前手牌张数, 排查"买了卡但界面没显示"时能直接看到数据层有没有。

        ★ 手牌哨兵的【常驻轮询】v4.143 起默认是关的(用户原话"一直关着, 点一下触发一次"),
          所以这个按钮就是那个"点一下"。想恢复常驻: 控制台 window.__sgsHandSentinel.enabled = true。
        """
        # ★ 必须走 window 上的入口: 哨兵的局部函数在 eval 的全局作用域里不存在
        #   (refresh_hand_view 踩过同一个坑, 详见它的注释)。
        js_code = ("return (typeof window.sgsHandSentinelOnce === 'function')"
                   " ? window.sgsHandSentinelOnce() : false")
        cid = self.send("eval", js=js_code)
        res = self.wait_result(cid, 2.0)
        ok = bool(res) and str(res).lower() == "true"
        if ok:
            self.log("[手牌哨兵] 已手动触发一次(手牌区已刷)")
        else:
            self.log(f"[手牌哨兵] 没刷成: 脚本未响应或返回错误: {res}"
                     f"{'' if res else ' (多半是脚本版本太旧, 没有 sgsHandSentinelOnce —— 重新注入一次桥接)'}")
        return ok

    def page_errors(self):
        """取页面端收集到的错误列表(v4.144★)。

        脚本从 document-start 就挂了 window.addEventListener('error') 与
        'unhandledrejection', 所有同步 throw / 未处理的 Promise 都进
        window.__sgsTopErrs(环形缓冲, 上限 160 条, 见 00-bridge.js:2693-2716)。

        ★ 这个数组一直都在, 但【没有查看入口】—— 用户原话"那个一直加的错误看不到"。
          而 F12 控制台常被网络请求日志刷屏(那类日志不是本脚本打的: 全项目 grep
          "已完成加载" 0 命中), 真错误一闪就过去了。这里直接把数组从页面读回来,
          不依赖控制台、也不受刷屏影响。
        """
        # ★ 这里【必须】写 "return ...": js= 参数会被本文件 277-278 行包装成
        #   (function(){ ... })() 再发给页面(页面端是顶层 eval, 见 sgs_bridge.user.js:18929,
        #   裸 return 在顶层是 SyntaxError)。去掉 return 反而会让整个 IIFE 返回 undefined。
        #   ⚠ 只有【直接走 HTTP /send】的调用(如 dev/_page_errors_once.py)没有这层包装,
        #     那边写 return 才会回 "ERR:Illegal return statement" —— 两处写法不同, 别混。
        js_code = "return JSON.stringify((window.__sgsTopErrs || []).slice(-80))"
        try:
            r = self.send_recv("eval", js=js_code, timeout=5.0)
        except Exception as e:
            self.log(f"[页面错误] 读取失败: {e}")
            return None
        if not isinstance(r, str):
            r = json.dumps(r, ensure_ascii=False)
        try:
            arr = json.loads(r)
        except Exception:
            self.log(f"[页面错误] 回包不是 JSON: {str(r)[:200]}")
            return None
        return arr if isinstance(arr, list) else None

    # ------------------------------------------------------------------
    # 「数据采集 → AI 分析 → 修改与验证 → 对局反馈」闭环的【助手侧入口】
    #   页侧三个 case 早就写好了(bridge/sgs_bridge.user.js:21685/21712/21750), 但 core/ 下
    #   grep "ai_" 是 0 命中 —— 从来没人从助手这边调过, 所以闭环一直断在这一截。
    #   注意: 它们是【exec 的 action】, 不是 eval —— 直接 send(action), 不用拼 js 代码,
    #   也就【不牵扯 return 那套包装规则】。
    # ------------------------------------------------------------------
    def _ai_json(self, action, timeout=3.0):
        """调页侧 ai_* case 并解析它 JSON.stringify 回来的字符串(内部共用)。

        解析不了就把原文塞进 {"_raw": ...} 返回 —— 宁可让调用方看到原文, 也别静默丢掉
        (项目里"存不上/取不到没人知道"这类坑踩过好几次, 见开发文档.md 第 8 条)。
        """
        try:
            cid = self.send(action)
            res = self.wait_result(cid, timeout)
        except Exception as e:
            self.log(f"[{action}] 调用失败: {e}")
            return None
        if isinstance(res, dict):
            return res
        if isinstance(res, str):
            try:
                return json.loads(res)
            except Exception:
                return {"_raw": res}
        return None

    def ai_selftest(self, timeout=3.0):
        """页侧自检快照(bridge:21685): 脚本版本 / 是否真进对局 / 网络单例在不在 /
        商店-手牌-阵容有没有数据。排查"点了没反应"时第一手就该看它。"""
        return self._ai_json("ai_selftest", timeout)

    def ai_gather(self, timeout=3.0):
        """【数据采集】抓一帧完整局面(bridge:21712-21749)。

        含: live(金币/等级/阶段)、self(主公ID/血量hp/金币/座位)、shop(每张带实价)、
        hand、lineup、tableID。
        ★ hp 是闭环里唯一现成的"打得怎么样"的信号; tableID 用来把帧归到同一局。
        """
        return self._ai_json("ai_gather", timeout)

    def ai_advice(self, timeout=3.0):
        """【AI 分析】页侧给的操作建议(bridge:21750-21779): buys(已有同将→升星 /
        低费拿)、refresh(无适买且等级够→刷新)、levelUp(金币充裕→升商店)、notes。"""
        return self._ai_json("ai_advice", timeout)

    def show_toast(self, message: str, duration_ms: int = 2000):
        """调用油猴脚本的 showToast 函数显示消息.
        """
        if not isinstance(message, str):
            self.log(f"[显示Toast] 失败: message 必须是字符串, 收到 {message}")
            return False
        if not isinstance(duration_ms, int) or duration_ms <= 0:
            self.log(f"[显示Toast] 失败: duration_ms 必须是正整数, 收到 {duration_ms}")
            return False

        js_code = f"showToast('{message.replace("'", "\\'")}', {duration_ms})"
        cid = self.send("eval", js=js_code)
        res = self.wait_result(cid, 2.0)

        ok = bool(res) and str(res).lower() == "true"
        if ok:
            self.log(f"[显示Toast] 成功: {message}")
        else:
            self.log(f"[显示Toast] 失败: 脚本未响应或返回错误: {res}")
        return ok

    # ----------------------------------------------------------------
    # v4.22★ 早期「从油猴取数」方法族 —— 已核查: 以下方法均【无调用点】
    #   真正在用的取数入口是 get_full_state(), 由 protocol_buy_targets()(F6) 调用;
    #   get_shop_goods / get_hand_chess / get_lineup 仅保留备用。
    #   已知隐患: 它们只捕获 json.JSONDecodeError, 若 send()/wait_result() 抛出
    #   其它异常(TypeError / 超时等) 会直接向上冒泡。将来若要启用, 建议统一改为
    #   except Exception 兜底并返回 []。
    # ----------------------------------------------------------------
    def get_hand_chess(self):
        """获取手牌列表 (当前无调用点, 备用). 调用油猴 window.AutoBuyAndSell.getHandChess()."""
        js_code = "return window.AutoBuyAndSell.getHandChess()"
        cid = self.send("eval", js=js_code)
        res = self.wait_result(cid, 2.0)
        try:
            return json.loads(res) if res else []
        except json.JSONDecodeError:
            self.log(f"[获取手牌列表] 失败: 脚本返回非JSON数据: {res}")
            return []

    def get_lineup(self):
        """v4.13★ 获取上阵区列表. 调用油猴脚本的 window.AutoBuyAndSell.getLineup() 方法."""
        js_code = "return window.AutoBuyAndSell.getLineup()"
        cid = self.send("eval", js=js_code)
        res = self.wait_result(cid, 2.0)
        try:
            return json.loads(res) if res else []
        except json.JSONDecodeError:
            self.log(f"[获取上阵区] 失败: 脚本返回非JSON数据: {res}")
            return []

    def get_full_state(self):
        """v4.13★ 获取完整状态 (shop + benches + lineup + gold + phase)."""
        js_code = "return JSON.stringify(window.AutoBuyAndSell.getFullState())"
        cid = self.send("eval", js=js_code)
        res = self.wait_result(cid, 2.0)
        try:
            return json.loads(res) if res else {"shop": [], "benches": [], "lineup": [], "players": [], "gold": 0, "phase": "未知"}
        except json.JSONDecodeError:
            self.log(f"[获取完整状态] 失败: 脚本返回非JSON数据: {res}")
            return {"shop": [], "benches": [], "lineup": [], "players": [], "gold": 0, "phase": "未知"}

    # ---------------- v4.21★ F6「协议买目标」 ----------------
    def protocol_buy_targets(self, whitelist=None, blacklist=None, budget_floor=2, limit=8):
        """按白名单从当前商店挑出目标武将, 逐张协议购买.

        v4.21★ 新增实现。ui/app.py 的 a_protocol_buy() 一直在调用本方法, 但 BridgeOps
        里从来没有实现过它 → 按 F6 必然报
            'BridgeOps' object has no attribute 'protocol_buy_targets'
        而且这条错误会被写进 _last_fb, 于是右上角 HUD 也一起显示同一句话。

        whitelist / blacklist: 武将 ID 列表(字符串或数字), 按商店卡的 chessID 匹配;
            为兼容旧注释里"存 goodsID"的说法, 卡上的 goodsID 也一起比一次。
        budget_floor: 金币不足该值时停手(留点钱刷新), 默认 2; 拿不到金币就不做此限制。
        limit: 单次最多买几张, 默认 8。

        返回 {'bought', 'failed', 'gold_saved', 'bought_list', 'failed_list', 'skipped'}
        """
        wl = {str(x).strip() for x in (whitelist or []) if str(x or '').strip()}
        bl = {str(x).strip() for x in (blacklist or []) if str(x or '').strip()}
        out = {"bought": 0, "failed": 0, "gold_saved": 0,
               "bought_list": [], "failed_list": [], "skipped": 0}
        if not wl:
            self.log("[F6/买目标] 白名单为空 —— 先在『武将自选』里配置目标")
            return out
        try:
            st = self.get_full_state()
        except Exception as e:
            self.log(f"[F6/买目标] 读取商店失败: {e}")
            return out
        shop = st.get("shop") or []
        gold = st.get("gold") or 0
        if not shop:
            self.log("[F6/买目标] 商店为空(先刷新一次商店, 让脚本拿到实时快照)")
            return out
        # 1) 先挑出商店里的目标卡
        picks = []
        for it in shop:
            if not isinstance(it, dict):
                continue
            cid = str(it.get("id") or it.get("chessID") or "")
            gid = str(it.get("goodsID") or "")
            if not gid or gid == "0":
                continue
            # v4.101★ 【白名单优先】(F6 一直"不管用"的真因就在这):
            #   以前先判黑名单 —— 而实测黑名单有 3346 条(=全量武将: 对局表184 + CSV补3162),
            #   把白名单 184 条【100% 覆盖】。于是每一张卡都先被黑名单吃掉, F6 永远回
            #   "商店里没有目标卡(黑名单跳过N个)" —— 明明配了目标, 一张也买不到。
            #   语义上: 白名单是"我明确要买的", 比黑名单("宽泛的不买")更具体, 所以命中
            #   白名单就直接买; 黑名单只用来统计"被挡掉的", 不再能否决白名单。
            in_wl = (cid and cid in wl) or (gid and gid in wl)
            if not in_wl:
                if cid and cid in bl:
                    out["skipped"] += 1
                continue
            if it.get("owned"):
                out["skipped"] += 1
                continue
            picks.append(it)
        if not picks:
            # v4.101★ 别只给一个数字: 把商店每张卡"为什么没被选中"逐张打出来, 否则用户
            #   看着"黑名单跳过3个"根本判断不了是自己没配、还是被黑名单挡了。
            detail = []
            for it in shop:
                if not isinstance(it, dict):
                    continue
                c2 = str(it.get("id") or it.get("chessID") or "")
                g2 = str(it.get("goodsID") or "")
                nm = it.get("name") or c2 or g2
                if c2 and c2 in bl:
                    why = "在黑名单里(已是白名单优先, 这张是白名单之外的)" if c2 not in wl else "命中黑名单"
                elif not c2:
                    why = "没读到ID"
                else:
                    why = "不在白名单"
                detail.append(f"{nm}({c2 or g2})={why}")
            self.log(f"[F6/买目标] 商店里没有目标卡 (白名单{len(wl)}个, 黑名单{len(bl)}个, 跳过{out['skipped']}个)")
            if detail:
                self.log("[F6/买目标] 商店明细: " + "; ".join(detail[:8]))
            cover = len(wl & bl)
            if cover:
                self.log(f"[F6/买目标] ⚠ 黑名单里有 {cover} 个和白名单重复(白名单共 {len(wl)} 个) —— "
                         f"本次按【白名单优先】照买; 但黑名单这么大(多半是误把全量武将加进去了), "
                         f"建议到『数据管理 → 黑名单』清理。")
            return out
        # 2) v4.134★ 【一次下发、页内批量】: 把整串交给页面自己排队去买。
        #   以前这里是【逐张循环】: 每张都要一次助手↔页面的 HTTP 往返, 而且每张买完之后
        #   手牌区被连续重建三拍(handSchedA/B/C = 200/600/1200ms) —— 10 张卡光"等"就是好几秒,
        #   这就是用户说的"视频里很快、我们像卡住"。
        #   页内批量把【往返 N 次 → 1 次】、【手牌重建 N×3 拍 → 1 次】, 每张之间只剩间隔。
        #   页面要是旧脚本(没有 batchBuyTargets)就自动退回下面的逐张购买,
        #   不会退化成"点了没反应"。
        ids = []
        for it in picks[: max(1, int(limit))]:
            cid_s = str(it.get("id") or it.get("chessID") or "")
            gid_s = str(it.get("goodsID") or "")
            if cid_s:
                ids.append(cid_s)
            elif gid_s:
                ids.append(gid_s)
        if ids:
            try:
                payload = {"blacklist": sorted(bl), "limit": int(limit),
                           "budgetFloor": int(budget_floor)}
                js = ("window.AutoBuyAndSell && window.AutoBuyAndSell.batchBuyTargets"
                      " ? JSON.stringify(window.AutoBuyAndSell.batchBuyTargets(%s, %s)) : ''"
                      % (json.dumps(ids, ensure_ascii=False),
                         json.dumps(payload, ensure_ascii=False)))
                r = self.send_recv("eval", js=js, timeout=4.0)
                r = r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)
                if r and r.strip()[:1] in ("{", "["):
                    d = json.loads(r)
                    if d.get("ok"):
                        plan = d.get("planned") or []
                        out["bought"] = len(plan)
                        out["bought_list"] = ["%s(%s)" % (p.get("name") or p.get("cid")
                                                          or p.get("idx"),
                                                          p.get("cid") or p.get("idx"))
                                              for p in plan]
                        self.log("[F6/买目标] 已交给页面批量买 %d 张(页内间隔 %sms): %s%s"
                                 % (len(plan), d.get("gap"),
                                    ", ".join(out["bought_list"]) or "无",
                                    (" | " + str(d["stopped"])) if d.get("stopped") else ""))
                        return out
                    if d.get("reason"):
                        self.log(f"[F6/买目标] 页内批量没跑起来({d.get('reason')}) —— 退回逐张购买")
            except Exception as e:
                self.log(f"[F6/买目标] 页内批量异常({e}) —— 退回逐张购买")

        # 3) 退回路径: 逐张协议购买(旧页面 / 页内批量不可用)
        for it in picks:
            if out["bought"] >= int(limit):
                self.log(f"[F6/买目标] 已达单次上限 {limit} 张, 停止")
                break
            price = int(it.get("price") or 2)
            if gold > 0 and gold < max(int(budget_floor), price):
                out["gold_saved"] += price
                self.log(f"[F6/买目标] 金币{gold}不足(需{price}, 保留{budget_floor}) —— 停止购买")
                break
            try:
                gid = int(it.get("goodsID"))
            except Exception:
                continue
            nm = it.get("name") or it.get("id") or gid
            if self.buy_goods(gid, delay=0.4):
                out["bought"] += 1
                out["bought_list"].append(f"{nm}({gid})")
                if gold > 0:
                    gold = max(0, gold - price)
            else:
                out["failed"] += 1
                out["failed_list"].append(f"{nm}({gid})")
        self.log(f"[F6/买目标] 买到 {out['bought']} 张: {', '.join(out['bought_list']) or '无'}"
                 + (f" | 失败 {out['failed']}: {', '.join(out['failed_list'])}" if out["failed"] else ""))
        return out

    def load_chess_name_map(self, id_map):
        """v4.13★ 推送武将名字映射到油猟端 (用于 normalizeGoods 名字解析)"""
        try:
            js = f"window.__SGS_Standalone.loadChessNameMap({json.dumps(id_map)});"
            self.send_recv("eval", js=js, timeout=2.0)
        except Exception as e:
            self.log(f"[推送武将映射] 失败: {e}")

    def load_spell_name_map(self, spell_map):
        """v4.13★ 推送锦囊名字映射到油猟端"""
        try:
            js = f"window.__SGS_Standalone.loadSpellNameMap({json.dumps(spell_map)});"
            self.send_recv("eval", js=js, timeout=2.0)
        except Exception as e:
            self.log(f"[推送锦囊映射] 失败: {e}")

    def load_weapon_name_map(self, weapon_map):
        """v4.150★ 推送【装备】名字映射到页面端。

        为什么必须有它: 3选1 的装备档, 页面端 SgsSelect3._one() 只认 chessID / spellID,
        装备候选名字一律落空(界面只剩裸 ID)。装备 ID 是配置表固定号, 离线查全推过去即可。
        v4.13 那批只做了武将 + 锦囊两张, 装备一直没推 —— 这就是"3选1 里装备没名字"的根因。
        """
        try:
            js = f"window.__SGS_Standalone.loadWeaponNameMap({json.dumps(weapon_map)});"
            self.send_recv("eval", js=js, timeout=2.0)
        except Exception as e:
            self.log(f"[推送装备映射] 失败: {e}")

    def load_equip_table(self, equip_table):
        """v4.164★ 推送【装备表】(ID->名字/大类)到页面端, 供剧本按装备判定 + 选装备按类型筛。

        数据由 tools/build_equip_table.py 离线生成(data/equip_table.json)。
        """
        try:
            js = f"window.__SGS_Standalone.loadEquipTable({json.dumps(equip_table)});"
            self.send_recv("eval", js=js, timeout=2.0)
        except Exception as e:
            self.log(f"[推送装备表] 失败: {e}")

    def load_skill_name_map(self, skill_map):
        """v4.150★ 推送【技能】名字映射到页面端(SkillID -> 技能名), 同上。"""
        try:
            js = f"window.__SGS_Standalone.loadSkillNameMap({json.dumps(skill_map)});"
            self.send_recv("eval", js=js, timeout=2.0)
        except Exception as e:
            self.log(f"[推送技能映射] 失败: {e}")

    def load_chess_skill_map(self, skill_map):
        """v4.32★ 推送【棋子 → 技能ID】映射到页面端 (ChessConf.csv 的 ChessSkill 列)。

        用途: 页面端判定"这张卡的技能是什么、要不要选人"时, 游戏配置表/卡实例都拿不到技能ID,
        就用这份表兜底(实机案例: 丁奉 = [330411])。赛季更新后重新抓表并重推即生效, 无需改代码。

        v4.39★ 同一份 payload 里的 picks = 需要手动选目标的技能ID(见 csv_loader.pick_skill_ids)。
        没有它页面端就只能退回游戏运行时的 TavernChessConfiger —— 而那个类在打包闭包里、
        页面端根本拿不到, 结果是把"要选人"的卡一律判成"不用选人", 当先被服务器吞掉。
        """
        try:
            m = skill_map or {}
            n = m.get("n", 0)
            npick = len(m.get("picks") or [])
            js = f"window.__SGS_Standalone.loadChessSkillMap({json.dumps(m)});"
            self.send_recv("eval", js=js, timeout=3.0)
            self.log(f"[推送技能映射] 已推送 {n} 条棋子技能(ChessConf.ChessSkill) / "
                     f"{npick} 条需手动选目标的技能(ChessSkillConf.TargetTyp1={21})")
        except Exception as e:
            self.log(f"[推送技能映射] 失败: {e}")

    def get_spell_use_log(self):
        """v4.13★ 获取锦囊使用日志 (从油猟端 window.__sgsSpellUseLog 读取)"""
        js_code = "return JSON.stringify(window.__sgsSpellUseLog || [])"
        cid = self.send("eval", js=js_code)
        res = self.wait_result(cid, 2.0)
        try:
            return json.loads(res) if res else []
        except json.JSONDecodeError:
            return []

    def clear_spell_use_log(self):
        """v4.13★ 清空锦囊使用日志"""
        try:
            self.send_recv("eval", js="window.__sgsSpellUseLog = [];", timeout=2.0)
        except Exception as e:
            self.log(f"[清空锦囊日志] 失败: {e}")

    def refresh_shop_js(self):
        """刷新商店. 调用油猴脚本的 window.AutoBuyAndSell.refreshShop() 方法."""
        js_code = "return window.AutoBuyAndSell.refreshShop()"
        cid = self.send("eval", js=js_code)
        res = self.wait_result(cid, 2.0)
        ok = bool(res) and str(res).lower() == "true"
        if not ok:
            self.log(f"[JS刷新商店] 失败: 脚本未响应或返回错误: {res}")
        return ok

    def buy_chess_js(self, goods_id):
        """购买卡牌. 调用油猴脚本的 window.AutoBuyAndSell.buyChess(goodsID) 方法."""
        js_code = f"return window.AutoBuyAndSell.buyChess({goods_id})"
        cid = self.send("eval", js=js_code)
        res = self.wait_result(cid, 2.0)
        ok = bool(res) and str(res).lower() == "true"
        if not ok:
            self.log(f"[JS购买卡牌] 失败: 脚本未响应或返回错误: {res}")
        return ok

    def sell_chess_js(self, goods_id):
        """遣散卡牌. 调用油猴脚本的 window.AutoBuyAndSell.sellChess(goodsID) 方法."""
        js_code = f"return window.AutoBuyAndSell.sellChess({goods_id})"
        cid = self.send("eval", js=js_code)
        res = self.wait_result(cid, 2.0)
        ok = bool(res) and str(res).lower() == "true"
        if not ok:
            self.log(f"[JS遣散卡牌] 失败: 脚本未响应或返回错误: {res}")
        return ok

    def find_targets_in_shop_js(self, target_id):
        """查找商店中是否存在目标卡牌. 调用油猴脚本的 window.AutoBuyAndSell.findTargetsInShop(targetId) 方法."""
        js_code = f"return JSON.stringify(window.AutoBuyAndSell.findTargetsInShop('{target_id}'))"
        cid = self.send("eval", js=js_code)
        res = self.wait_result(cid, 2.0)
        try:
            return json.loads(res) if res else []
        except json.JSONDecodeError:
            self.log(f"[JS查找商店卡牌] 失败: 脚本返回非JSON数据: {res}")
            return []

    def _auto_buy_extras_js(self, opts):
        """v4.24★ 把招募增强项(保留金币/自动合星/备战满卖闲)转成 JS 片段.

        opts 为 None 或某键缺失时【不输出该键】: 油猴端 updateAutoBuyConfig 只在显式传入时
        才更新, 所以旧的调用方(例: DualListPanel 只推 target_ids)不会把用户已勾的开关重置掉.
        """
        if not opts:
            return ""
        try:
            parts = []
            if "reserve_gold" in opts:
                parts.append("reserveGold:" + str(max(0, int(opts.get("reserve_gold") or 0))))
            if "auto_combine" in opts:
                parts.append("autoCombine:" + ("true" if opts.get("auto_combine") else "false"))
            if "auto_sell_bench" in opts:
                parts.append("autoSellBench:" + ("true" if opts.get("auto_sell_bench") else "false"))
            if "bench_threshold" in opts:
                parts.append("benchFullThreshold:" + str(max(1, int(opts.get("bench_threshold") or 8))))
            # v4.26★ 遣散前先上阵的落点: none=不上阵 | default=上完后的倒数第二格(已上阵N人→第N格, 占位则右侧整体右移) |
            #        auto=第一个空位 | 1..7=固定格
            if "sell_after_deploy_pos" in opts:
                _dp = str(opts.get("sell_after_deploy_pos") or "none")
                _dp = "".join(ch for ch in _dp if ch.isalnum() or ch in "-_") or "none"
                parts.append("sellAfterDeployPos:'" + _dp + "'")
            return ("," + ",".join(parts)) if parts else ""
        except (TypeError, ValueError) as e:
            self.log(f"[自动招募] 增强项参数非法, 已忽略: {e}")
            return ""

    def _auto_buy_extras_desc(self, opts):
        """v4.24★ 增强项的可读描述 (只用于日志, 参数非法时静默忽略)."""
        desc = []
        if not opts:
            return desc
        try:
            if int(opts.get("reserve_gold") or 0) > 0:
                desc.append(f"保留金币{int(opts.get('reserve_gold'))}")
            if opts.get("auto_combine"):
                desc.append("自动合星")
            if opts.get("auto_sell_bench"):
                desc.append(f"备战满{int(opts.get('bench_threshold') or 8)}自动卖闲")
            # v4.25★ 遣散前先上阵 (只在这一项真的开启时才打)
            _dp = str(opts.get("sell_after_deploy_pos") or "none")
            if _dp and _dp != "none":
                _dp_desc = {"default": "倒数第二格", "d": "倒数第二格", "auto": "第一个空位",
                            "a": "第一个空位"}.get(_dp, f"第{_dp}格")
                desc.append(f"遣散前先上阵到{_dp_desc}")
        except (TypeError, ValueError):
            return []
        return desc

    def _auto_buy_task(self, target_id, max_refreshes, progress_callback, mode='buy', opts=None):
        """自动购买的后台任务逻辑.
        v4.12★ mode='buy'只买, mode='buy_and_sell'买+遣散.
        v4.24★ opts: 招募增强项 {reserve_gold, auto_combine, auto_sell_bench, bench_threshold},
               缺省 None = 全部按油猴端默认(关闭), 行为与旧版一致.
        支持多目标: target_id 可以是逗号分隔字符串或列表."""
        # 统一成数组 (支持 "吕蒙,陆逊" 或 ["吕蒙","陆逊"] 两种入参)
        if isinstance(target_id, (list, tuple)):
            targets = list(target_id)
        else:
            targets = [t.strip() for t in str(target_id).split(",") if t.strip()]
        _extras_desc = self._auto_buy_extras_desc(opts)
        self.log(f"[自动购买] 任务启动: 目标={targets}, 最大刷新次数={max_refreshes}" +
                 (f", 增强={_extras_desc}" if _extras_desc else ""))

        # 1) 推送配置 (多目标 + maxRefreshes + 增强项) 到油猴端
        try:
            cfg_js = ("window.AutoBuyAndSell.updateAutoBuyConfig("
                      "{enabled:true, targetChessIDs:" + json.dumps(targets) +
                      ", maxRefreshes:" + str(int(max_refreshes)) + ", mode:'" + mode + "'" +
                      self._auto_buy_extras_js(opts) + "});")
            self.send_recv("eval", js=cfg_js, timeout=2.0)
        except Exception as e:
            self.log(f"[自动购买] 推配置失败: {e}")

        # 2) 一次 eval 启动油猴端 autoFindAndBuy (内部已实现多目标+psend批量+200ms循环)
        start_js = ("window.AutoBuyAndSell.autoFindAndBuy(" +
                    json.dumps(targets) + "," + str(int(max_refreshes)) + ");")
        try:
            self.send_recv("eval", js=start_js, timeout=2.0)
        except Exception as e:
            self.log(f"[自动购买] 启动失败: {e}")
            self._auto_task_running = False
            self._auto_task_stop_flag = False
            if progress_callback:
                progress_callback(f"启动失败: {e}")
            return

        # 3) 轮询 /push 上报的 autoBuyProgress, 把进度同步回 Python UI
        # v4.13★ 等待第一个 push 周期 (1.2s) 再开始轮询, 避免读到上一次任务的残留 isRunning=false
        time.sleep(1.2)
        last_msg = ""
        last_refresh = -1
        poll_count = 0
        deadline = time.time() + (max_refreshes * 1.0) + 30  # 兜底超时: 每轮预算 1s + 30s 缓冲
        while time.time() < deadline:
            if self._auto_task_stop_flag:
                # 用户按停止 → 只停招募(不影响多选购买)
                try:
                    self.send_recv("eval", js="window.AutoBuyAndSell.stopRecruitOnly();", timeout=2.0)
                except Exception:
                    _SIL.note("core/remote_ops.py:3419", "self.send_recv('eval', js='window.AutoBuyAndSe")
                break
            poll_count += 1
            st = STATE.get_state() or {}
            # v4.15★ 招募独立进度 (优先 recruitProgress, 兼容旧的 autoBuyProgress)
            prog = st.get("recruitProgress") or st.get("autoBuyProgress") or {}
            is_running = bool(prog.get("isRunning", False))
            cur_refresh = int(prog.get("currentRefreshCount", 0) or 0)
            found = int(prog.get("foundCount", 0) or 0)
            bought = int(prog.get("totalBought", 0) or 0)
            sold = int(prog.get("totalSold", 0) or 0)
            msg = str(prog.get("lastMsg", "") or "")
            if cur_refresh != last_refresh or msg != last_msg:
                last_refresh = cur_refresh
                last_msg = msg
                self.log(f"[自动购买] 进度 刷新{cur_refresh}/{max_refreshes} 命中{found} 买{bought}" +
                         (f" 卖{sold}" if sold else "") + f" | {msg}")
                if progress_callback:
                    progress_callback(f"第{cur_refresh}/{max_refreshes}轮 | 买{bought}" +
                                      (f" 卖{sold}" if sold else "") + f" | {msg}")
            # v4.13★ 前5次轮询 (约1.5s) 不因 isRunning=false 退出, 避免读到旧push残留
            if not is_running and cur_refresh > 0 and poll_count > 5:
                # 油猴端任务结束 (达到 max 或停止)
                break
            time.sleep(0.3)

        # 最终汇总
        _st_final = STATE.get_state() or {}
        final_prog = _st_final.get("recruitProgress") or _st_final.get("autoBuyProgress") or {}
        final_bought = int(final_prog.get("totalBought", 0) or 0)
        final_refresh = int(final_prog.get("currentRefreshCount", 0) or 0)
        final_failed = int(final_prog.get("failedCount", 0) or 0)
        # v4.24★ 卖闲腾位的张数 (与"买后即卖"的 totalSold 分开统计)
        final_idle = int(final_prog.get("totalIdleSold", 0) or 0)
        self.log(f"[自动购买] 任务结束。购买数量: {final_bought}, 刷新次数: {final_refresh}" +
                 (f", 购买失败: {final_failed}" if final_failed else "") +
                 (f", 卖闲腾位: {final_idle}" if final_idle else ""))
        # v4.15★ 任务退出前必下发油猴端独立停止, 防止"Python 以为停了, 油猴还在跑"
        try:
            self.send_recv("eval", js="window.AutoBuyAndSell.stopRecruitOnly();", timeout=1.5)
        except Exception:
            _SIL.note("core/remote_ops.py:3460", "self.send_recv('eval', js='window.AutoBuyAndSe")
        self._auto_task_running = False
        self._auto_task_stop_flag = False
        if progress_callback:
            progress_callback(f"任务完成。已购买 {final_bought} 个, 刷新 {final_refresh} 次。")


    def update_auto_buy_config(self, target_ids, max_refreshes=50, mode='buy', opts=None):
        """v4.12★ 推送自动招募配置到油猟端 (不启动, 只更新配置)

        v4.24★ opts 缺省 None → 不推增强项字段, 保持油猴端现状(不会被重置)."""
        try:
            ids_json = json.dumps([str(i) for i in (target_ids or [])])
            cfg_js = ("window.AutoBuyAndSell.updateAutoBuyConfig("
                      "{enabled:true, targetChessIDs:" + ids_json +
                      ", maxRefreshes:" + str(int(max_refreshes)) + ", mode:'" + mode + "'" +
                      self._auto_buy_extras_js(opts) + "});")
            self.send_recv("eval", js=cfg_js, timeout=2.0)
            self.log(f"[自动招募] 配置已推送: {len(target_ids or [])} 个目标")
        except Exception as e:
            self.log(f"[自动招募] 推送配置失败: {e}")

    # ================================================================
    # v4.27★ 剧本 (Scenario) —— 页面端按先后顺序执行的步骤流
    #   剧本结构: [{"id": "s1", "name": "孙策当先", "hotkey": "ctrl+alt+1",
    #               "steps": [{"op": "deploy", "target": "孙尚香", "pos": "auto"}, ...]}]
    #   步骤 op: deploy 上阵 | recycle 遣散 | shopFind 牌库找卡 | useSpell 用锦囊
    #            loopStart/loopEnd 循环 | ifStart/else/ifEnd 条件 | wait 等待 | toast 提示
    #   执行一次: start_scenario() 发起 → poll_scenario_log() 收页面日志/状态
    # ================================================================
    def push_scenarios(self, scripts, enabled=True, quiet=False):
        """把剧本列表下发给油猴端(常驻, 不启动执行)。保存配置 / 连接成功时调用。"""
        try:
            payload = json.dumps({"scripts": scripts or [], "enabled": bool(enabled)})
            js = "window.AutoBuyAndSell.updateScenarios(" + payload + ");"
            self.send_recv("eval", js=js, timeout=3.0)
            if not quiet:
                self.log(f"[剧本] 已下发 {len(scripts or [])} 个剧本到油猴端")
        except Exception as e:
            self.log(f"[剧本] 下发失败: {e}")

    def list_scenarios(self):
        """读回页面里当前的剧本列表(确认下发是否生效)"""
        try:
            res = self.send_recv("eval",
                                 js="JSON.stringify(window.AutoBuyAndSell.listScenarios())",
                                 timeout=3.0)
            return json.loads(res) if res else {}
        except Exception as e:
            self.log(f"[剧本] 读取剧本列表失败: {e}")
            return {}

    def start_scenario(self, script_id, limit=0):
        """请求页面执行一次指定剧本(立即返回; 进度用 poll_scenario_log 收)

        v4.67★ limit: 试运行步数(0 = 跑完整)。页面端跑到第 limit 步就收工, 且算【正常结束】,
          不算失败 —— 用户的意图就是"先看这几步对不对"(写剧本时一处写错, 整段跑是要真花钱的)。
        """
        try:
            js = (
                "JSON.stringify((function(){"
                "var api=window.AutoBuyAndSell;"
                "if(!api||typeof api.startScriptById!=='function')"
                "return {ok:false,reason:'游戏页脚本过旧或未加载，缺少 startScriptById'};"
                "return api.startScriptById("
                + json.dumps(str(script_id)) + ", " + str(max(0, int(limit or 0)))
                + ");})())"
            )
            res = self.send_recv("eval", js=js, timeout=4.0)
            return json.loads(res) if res else {}
        except Exception as e:
            self.log(f"[剧本] 启动失败: {e}")
            return {"ok": False, "reason": str(e)}

    def stop_scenario(self):
        """请求页面中断正在执行的剧本"""
        try:
            res = self.send_recv("eval",
                                 js="JSON.stringify(window.AutoBuyAndSell.stopScript())",
                                 timeout=3.0)
            return json.loads(res) if res else {}
        except Exception as e:
            return {"ok": False, "reason": str(e)}

    def poll_scenario_log(self, from_seq=0):
        """拉取 from_seq 之后的页面剧本日志 + 运行状态"""
        try:
            js = ("JSON.stringify(window.AutoBuyAndSell.getScenarioLog("
                  + str(int(from_seq or 0)) + "))")
            res = self.send_recv("eval", js=js, timeout=3.0)
            return json.loads(res) if res else {}
        except Exception:
            return {}

    def start_auto_buy(self, target_id, max_refreshes=10, progress_callback=None, mode='buy', opts=None):
        """v4.12★ 启动自动购买任务. mode='buy'只买, mode='buy_and_sell'买+遣散.

        v4.24★ opts: 招募增强项 {reserve_gold, auto_combine, auto_sell_bench, bench_threshold},
               缺省 None = 全部按油猴端默认(关闭), 与旧版行为一致."""
        if self._auto_task_running:
            self.log("[自动购买] 任务已在运行中。")
            if progress_callback:
                progress_callback("任务已在运行中。")
            return

        self._auto_task_stop_flag = False
        self._auto_task_thread = threading.Thread(
            target=self._auto_buy_task,
            args=(target_id, max_refreshes, progress_callback, mode, opts),
            daemon=True
        )
        self._auto_task_thread.start()
        self._auto_task_running = True
        if progress_callback:
            progress_callback("自动购买任务已启动。")

    def stop_auto_buy(self):
        """停止自动购买任务."""
        if self._auto_task_running:
            self.log("[自动购买] 请求停止任务。")
            self._auto_task_stop_flag = True
        else:
            self.log("[自动购买] 没有正在运行的自动购买任务。")
        # v4.15★ 无论 Python 侧是否还在跑, 都下发一次油猴端"只停招募"停止(防残留循环)
        try:
            self.send_recv("eval", js="window.AutoBuyAndSell.stopRecruitOnly();", timeout=1.5)
        except Exception:
            _SIL.note("core/remote_ops.py:3580", "self.send_recv('eval', js='window.AutoBuyAndSe")

    def perf_start(self, label="default"):
        """开始性能分析: 脚本侧开始收集 XHR/fetch RTT / 帧耗时 / 阶段事件 / canvas 截图."""
        cid = self.send("perf_start", label=str(label or "run1"))
        res = self.wait_result(cid, 4.0)
        if res and str(res).startswith("ok"):
            self.log(f"[性能分析] ✓ 已启动(label={label})")
            return True
        self.log(f"[性能分析] 启动失败: {res or '超时'}")
        return False

    def perf_snapshot(self, label=""):
        """在当前时间点打一个快照(带canvas缩略图). 会被写入报告的时间线."""
        cid = self.send("perf_snapshot", label=str(label or ""))
        res = self.wait_result(cid, 4.0)
        return bool(res) and str(res).startswith("ok")

    def perf_stop(self, report_name=None):
        """停止性能分析, 把所有原始数据拉回Python→生成HTML报告. 返回报告HTML路径."""
        cid = self.send("perf_stop")
        res = self.wait_result(cid, 15.0)
        if not res or str(res).startswith(("ERR", "no_stage")):
            self.log(f"[性能分析] 停止失败: {res or '超时'}")
            return None
        try:
            raw = json.loads(res)
        except Exception as e:
            self.log(f"[性能分析] 结果解析失败: {e}")
            return None
        # Python 侧合成 HTML 报告 (数据太大时做截断)
        try:
            from .config import DATA_DIR
            import os as _os
            import datetime as _dt
            reports_dir = _os.path.join(DATA_DIR, "reports")
            _os.makedirs(reports_dir, exist_ok=True)
            name = report_name or ("perf_" + time.strftime("%Y%m%d_%H%M%S"))
            if not name.lower().endswith(".html"):
                name = name + ".html"
            path = _os.path.join(reports_dir, name)
            # 生成报告 HTML
            html = self._perf_build_html(raw)
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)
            self.log(f"[性能分析] ✓ 报告生成: {path}")
            return path
        except Exception as e:
            self.log(f"[性能分析] 报告生成失败: {e.__class__.__name__}:{e}")
            return None

    def _perf_build_html(self, raw):
        """把脚本侧返回的性能数据(raw)写成可交互 HTML 报告."""
        import html as _html
        xhrs = raw.get("xhrs") or []
        frames = raw.get("frames") or []
        events = raw.get("events") or []
        tw_phases = raw.get("phases") or []
        snapshots = raw.get("snapshots") or []
        cfg = raw.get("config") or {}
        # 总览指标
        duration_s = 0.0
        if events:
            t0 = float(events[0].get("ts") or time.time())
            t1 = float(events[-1].get("ts") or t0)
            duration_s = max(0.0, t1 - t0)
        avg_fps = 0.0
        if frames:
            avg_fps = 1.0 / max(0.0001, sum(float(f.get("dt") or 0.0) for f in frames) / max(1, len(frames)))
        avg_rtt = 0.0
        if xhrs:
            vals = [float(x.get("rtt") or 0.0) for x in xhrs
                    if isinstance(x, dict) and float(x.get("rtt") or 0) > 0]
            if vals: avg_rtt = sum(vals) / len(vals)
        def _fmt_dt(ms):
            ms = float(ms)
            if ms < 1000: return f"{ms:.0f}ms"
            return f"{ms/1000:.2f}s"
        def _color_ms(ms):
            ms = float(ms)
            if ms >= 2000: return "#ff4d4d"
            if ms >= 1000: return "#ffb347"
            return "#8ef58e"
        # 诊断建议
        advice = []
        # 1) Tween 慢
        tween_slow_count = sum(1 for e in events if e.get("tag") == "tween_slow")
        if tween_slow_count >= 3:
            advice.append(("红", f"检测到 {tween_slow_count} 次 Tween 单动画 > 500ms. 建议勾选【招募阶段Tween立即完成】"))
        # 2) 买全 verify 慢
        verify_slow = sum(1 for e in events if e.get("tag") == "psend_verify" and
                          float(e.get("elapsed_ms", 0)) > 1000)
        if verify_slow >= 2:
            advice.append(("红", f"{verify_slow} 次协议 verify > 1s (一般是逐张 buy 等 verify). 建议勾【批量协议并发】."))
        # 3) XHR RTT 慢
        if avg_rtt >= 500:
            advice.append(("黄", f"平均 XHR RTT {avg_rtt:.0f}ms >= 500ms, 建议检查网络(切换节点/加速器)"))
        # 4) FPS 过低
        if avg_fps and avg_fps < 30:
            advice.append(("黄", f"实测平均 FPS {avg_fps:.0f} < 30. 建议降低自定义 FPS 到 60, 或关闭浏览器内其他标签页"))
        # 5) 画布等待
        wait_canvas = sum(1 for e in events if e.get("tag") == "canvas_wait")
        if wait_canvas > 0:
            advice.append(("黄", f"等待画布事件 {wait_canvas} 次(一般是 Laya 还没就绪或战斗→招募切阶段). 若严重→F5 刷新游戏页"))
        if not advice:
            advice.append(("绿", "当前设置下的 网络/渲染/Tween 指标 都在合理范围, 无需进一步优化."))
        # Top slow XHR
        xhrs_sorted = sorted([x for x in xhrs if isinstance(x, dict)],
                             key=lambda x: -float(x.get("rtt") or 0))[:10]
        # Top slow frames
        frames_sorted = sorted([x for x in frames if isinstance(x, dict)],
                               key=lambda x: -float(x.get("dt") or 0))[:10]
        # 阶段耗时
        def row_phase(p):
            try:
                return (f"<tr><td>{int(p.get('idx') or 0)}</td>"
                        f"<td>{_html.escape(str(p.get('name') or '?'))}</td>"
                        f"<td>{_fmt_dt(float(p.get('dt_ms') or 0))}</td>"
                        f"<td style=\"color:{_color_ms(p.get('dt_ms') or 0)}\">●</td></tr>")
            except Exception:
                return ""
        phase_rows = "".join(row_phase(p) for p in (tw_phases or []))
        # 时间线简易
        def ev_desc(e):
            tag = str(e.get("tag") or "event")
            lbl = _html.escape(str(e.get("label") or ""))
            extra = _html.escape(str(e.get("extra") or ""))
            ms = _fmt_dt(float(e.get("elapsed_ms") or 0))
            col = _color_ms(e.get("elapsed_ms") or 0)
            return f"<li style=\"color:{col}\">[{ms}] <b>{tag}</b> {lbl} {extra}</li>"
        events_html = "".join(ev_desc(e) for e in events[-500:])
        thumbnails = ""
        for s in (snapshots or [])[:12]:
            data_url = s.get("data_url") or ""
            label = _html.escape(str(s.get("label") or "截图"))
            ts = f"{s.get('ts')}"
            if data_url and data_url.startswith("data:"):
                thumbnails += (f"<div style=\"display:inline-block;margin:4px;\">"
                               f"<div style=\"font-size:11px;color:#666\">{label} ts={ts[:19]}</div>"
                               f"<img src=\"{data_url}\" style=\"max-width:360px;max-height:240px;border:1px solid #444;\"></div>")
        # 最终报告
        cfg_str = _html.escape(json.dumps(cfg, ensure_ascii=False, indent=2)[:5000])
        return f"""<!doctype html>
<html><head><meta charset="utf-8">
<title>三国杀自走棋·性能报告</title>
<style>
body {{ font-family: "Microsoft YaHei UI", Consolas, monospace; background:#0e0e10; color:#d9e2d0; margin:18px; }}
h1 {{ color:#ffd700; }}
h2 {{ color:#8ef58e; border-left:4px solid #ffd700; padding-left:8px; }}
.card {{ background:#16161c; border:1px solid #2b2b34; border-radius:8px; padding:14px; margin:10px 0; }}
.row {{ display:flex; gap:18px; flex-wrap:wrap; }}
.box {{ flex:1 1 220px; padding:10px; background:#22222a; border-radius:6px; }}
.box b {{ color:#ffd700; font-size:22px; }}
.box span {{ display:block; color:#7a7a82; font-size:12px; margin-top:4px; }}
table {{ border-collapse: collapse; width:100%; }}
th, td {{ border:1px solid #333; padding:6px 8px; font-size:12px; }}
th {{ background:#1f1f28; color:#8ef58e; }}
tr:nth-child(2n) td {{ background:#15151a; }}
.adv-red {{ color:#ff6b6b; }} .adv-yellow {{ color:#ffb347; }} .adv-green {{ color:#8ef58e; }}
pre {{ background:#111117; border:1px solid #333; padding:10px; max-height:280px; overflow:auto; }}
ul.timeline {{ max-height:340px; overflow:auto; background:#111117; border:1px solid #333; padding:8px 8px 8px 24px; }}
</style></head>
<body>
<h1>三国杀自走棋·性能分析报告(v3.44)</h1>
<div class="row">
  <div class="box"><b>{duration_s:.1f}s</b><span>全程时长</span></div>
  <div class="box"><b>{len(tw_phases)}</b><span>阶段数(招募/战斗/选将)</span></div>
  <div class="box"><b>{avg_fps:.0f}</b><span>实测平均 FPS(按帧dt)</span></div>
  <div class="box"><b>{avg_rtt:.0f}ms</b><span>平均 XHR RTT</span></div>
  <div class="box"><b>{len(xhrs)}</b><span>XHR/fetch 请求数</span></div>
  <div class="box"><b>{len(frames)}</b><span>采样 Laya 帧数</span></div>
</div>
<div class="card">
  <h2>💡 诊断建议(自动按数据生成)</h2>
  <ul>
    {"".join(f'<li class="adv-{{c.lower() if c in ("红","黄","绿") else "yellow"}}">{{m}}</li>'
            .replace("{{c}}", c).replace("{{m}}", m) for (c, m) in advice)}
  </ul>
</div>
<div class="card">
  <h2>📜 时间线(最近500条, 毫秒耗时:绿<1s 黄1-2s 红≥2s)</h2>
  <ul class="timeline">{events_html if events_html else '<li>(暂无事件——是否先点了开始分析?)</li>'}</ul>
</div>
<div class="card">
  <h2>🏁 阶段耗时明细</h2>
  <table><thead><tr><th>#</th><th>阶段名</th><th>耗时</th><th>颜色</th></tr></thead>
  <tbody>{phase_rows}</tbody></table>
</div>
<div class="row"><div class="card" style="flex:1 1 48%">
  <h2>🛰 Top-10 慢网络请求(XHR/fetch RTT)</h2>
  <table><thead><tr><th>URL(前120字符)</th><th>RTT</th><th>Size(B)</th><th>状态码</th></tr></thead><tbody>
  {"".join(f'<tr><td>{_html.escape(str(x.get("url") or ""))[:160]}</td>'
          f'<td style="color:{_color_ms(x.get("rtt") or 0)}">{_fmt_dt(x.get("rtt") or 0)}</td>'
          f'<td>{x.get("size") or "?"}</td>'
          f'<td>{x.get("status") or "?"}</td></tr>' for x in xhrs_sorted)}
  </tbody></table>
</div>
<div class="card" style="flex:1 1 48%">
  <h2>🎬 Top-10 慢渲染帧(Laya dt)</h2>
  <table><thead><tr><th>时间点(s)</th><th>单帧耗时 dt</th><th>对应 FPS = 1/dt</th><th>颜色</th></tr></thead><tbody>
  {"".join(f'<tr><td>{float(f.get("t_rel") or 0):.2f}</td>'
          f'<td style="color:{_color_ms(float(f.get("dt") or 0) * 1000)}">{float(f.get("dt") or 0)*1000:.0f}ms</td>'
          f'<td>{1/max(0.0001, float(f.get("dt") or 0)):.0f}</td>'
          f'<td style="color:{_color_ms(float(f.get("dt") or 0)*1000)}">●</td></tr>'
          for f in frames_sorted)}
  </tbody></table>
</div></div>
<div class="card">
  <h2>🖼 慢帧截图(最多最近12张; 只对应 帧 dt>40ms 的时刻)</h2>
  <div>{thumbnails if thumbnails else '<span style="color:#666">(无慢帧——性能很好, 没有大于40ms的帧)</span>'}</div>
</div>
<div class="card">
  <h2>⚙ 当前 FPS/加速/加速 Flag(报告生成时的实际状态)</h2>
  <pre>{cfg_str}</pre>
</div>
</body></html>
"""

    # ---------------- hero_picker.json 武将选择库导出(问题六) ----------------
    def build_hero_picker(self):
        """v3.98★ 扁平化数据模型: 一键合并 bridge + generals.json + hero_picker.json。
        不再使用复杂的“套娃”覆盖，建立唯一的权威数据库。
        返回写入路径(成功) / None(失败)."""
        import os as _os
        import json
        from .config import DATA_DIR
        from . import id_map as _idm
        
        output_path = _os.path.join(DATA_DIR, "hero_picker.json")
        
        # 1. 加载现有的 hero_picker.json (保留用户之前的选择和修改)
        current_data = {"whitelist": [], "blacklist": [], "hidden_ids": [], "heroes_map": {}}
        if _os.path.exists(output_path):
            try:
                with open(output_path, "r", encoding="utf-8") as f:
                    old_obj = json.load(f)
                    if isinstance(old_obj, dict):
                        # 兼容旧格式的 __patch__
                        patch = old_obj.get("__patch__") or {}
                        current_data["whitelist"] = patch.get("whitelist") or old_obj.get("whitelist") or []
                        current_data["blacklist"] = patch.get("blacklist") or old_obj.get("blacklist") or []
                        current_data["hidden_ids"] = patch.get("hidden_ids") or old_obj.get("hidden_ids") or []
                        
                        # 如果是旧格式，从 heroes 列表里恢复数据，或者从 overrides 恢复
                        heroes_list = old_obj.get("heroes") or []
                        for h in heroes_list:
                            if isinstance(h, dict) and "chessID" in h:
                                current_data["heroes_map"][str(h["chessID"])] = h
                        
                        overrides = patch.get("hero_overrides") or {}
                        for gid, fields in overrides.items():
                            gid_s = str(gid)
                            if gid_s not in current_data["heroes_map"]:
                                current_data["heroes_map"][gid_s] = {}
                            current_data["heroes_map"][gid_s].update(fields)
            except Exception as e:
                self.log(f"[扁平化] 加载旧配置失败: {e}")

        # 2. 加载百科全书 (generals.json)
        encyclopedia = {}
        try:
            gen_path = _os.path.join(DATA_DIR, "generals.json")
            if _os.path.exists(gen_path):
                with open(gen_path, "r", encoding="utf-8") as f:
                    gen_list = json.load(f)
                    if isinstance(gen_list, list):
                        for g in gen_list:
                            name = g.get("name")
                            if name: encyclopedia[str(name)] = g
        except Exception: _SIL.note("core/remote_ops.py:3849", "gen_path = _os.path.join(DATA_DIR, 'generals.j")

        # 3. 读自走棋专属配置 (tavern_*.json)
        TAVERN_FILES = {"chess": "tavern_chess.json", "lords": "tavern_lords.json", "spells": "tavern_spells.json"}
        tavern_data = {"chess": {}, "lords": {}, "spells": {}}
        for k, fn in TAVERN_FILES.items():
            p = _os.path.join(DATA_DIR, fn)
            if _os.path.isfile(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        obj = json.load(f)
                        if isinstance(obj, dict): tavern_data[k] = obj
                except Exception: _SIL.note("core/remote_ops.py:3861", "with open(p, 'r', encoding='utf-8') as f: obj ")

        # 如果没有 tavern 表，强制抓一次
        if not tavern_data["chess"]:
            self.log("[扁平化] 未发现自走棋配置表，正在抓取...")
            self.fetch_cfg()
            # 重新加载
            for k, fn in TAVERN_FILES.items():
                p = _os.path.join(DATA_DIR, fn)
                if _os.path.isfile(p):
                    try:
                        with open(p, "r", encoding="utf-8") as f:
                            obj = json.load(f)
                            if isinstance(obj, dict): tavern_data[k] = obj
                    except Exception: _SIL.note("core/remote_ops.py:3875", "with open(p, 'r', encoding='utf-8') as f: obj ")

        # 4. 执行大合并逻辑
        new_heroes = {}
        new_spells = {}

        def _process_item(gid, raw_info, src, is_spell=False):
            gid_s = str(gid)
            name = (raw_info.get("n") or raw_info.get("name") or raw_info.get("ChessName") or 
                    raw_info.get("SkillName") or raw_info.get("SpellName") or f"ID{gid_s}")
            
            clean_name = _idm._clean_name(name)
            wiki = encyclopedia.get(clean_name) or {}
            
            item = {
                "chessID": int(gid) if gid_s.isdigit() else gid_s,
                "name": name,
                "cost": raw_info.get("cost") or raw_info.get("ChessRank") or wiki.get("cost"),
                "race_or_camp": raw_info.get("race_or_camp") or raw_info.get("faction") or wiki.get("faction"),
                "desc": wiki.get("effect") or raw_info.get("desc") or raw_info.get("ChessSkillDesc") or raw_info.get("SpellSkillDesc") or "",
                "tags": wiki.get("tags") or raw_info.get("tags") or [],
                "atk": raw_info.get("atk") or raw_info.get("DefaultATK") or wiki.get("atk"),
                "hp": raw_info.get("hp") or raw_info.get("DefaultDEF") or wiki.get("hp"),
                "_src": src
            }
            if isinstance(item["tags"], str):
                item["tags"] = [t.strip() for t in item["tags"].split(",") if t.strip()]

            # 合并用户之前的手动修改 (优先级最高)
            if gid_s in current_data["heroes_map"]:
                # 只合并非空字段，防止被 null 覆盖
                for fk, fv in current_data["heroes_map"][gid_s].items():
                    if fv not in (None, ""):
                        item[fk] = fv
            
            if is_spell: new_spells[gid_s] = item
            else: new_heroes[gid_s] = item

        for k, v in tavern_data["chess"].items(): _process_item(k, v, "tavern_chess")
        for k, v in tavern_data["lords"].items(): _process_item(k, v, "tavern_lords")
        for k, v in tavern_data["spells"].items(): _process_item(k, v, "tavern_spells", True)

        # 5. 保存扁平化后的 hero_picker.json
        final_output = {
            "version": "3.98-Flat",
            "updated_at": __import__('time').strftime('%Y-%m-%d %H:%M:%S'),
            "whitelist": current_data["whitelist"],
            "blacklist": current_data["blacklist"],
            "hidden_ids": current_data["hidden_ids"],
            "heroes": list(new_heroes.values()),
            "spells": list(new_spells.values())
        }
        
        try:
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(final_output, f, ensure_ascii=False, indent=2)
            self.log(f"[扁平化] 已生成唯一权威库: {len(new_heroes)}武将, {len(new_spells)}锦囊")
            return output_path
        except Exception as e:
            self.log(f"[扁平化] 保存失败: {e}")
            return None

    def hero_picker_save_user_patch(self, patch_obj):
        """写 hero_picker_user_patch.json. 独立文件, 与 hero_picker.json 解耦."""
        from .config import DATA_DIR
        import os as _os
        patch_path = _os.path.join(DATA_DIR, "hero_picker_user_patch.json")
        cur = {}
        if _os.path.exists(patch_path):
            try:
                with open(patch_path, "r", encoding="utf-8") as f:
                    cur = json.load(f) or {}
            except Exception:
                cur = {}
        # 合并 heroes 字段(chessID -> 字段覆盖)
        new_patch = {
            "version": "v3.44",
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "heroes": {**(cur.get("heroes") or {}), **(patch_obj.get("heroes") or {})}
        }
        with open(patch_path, "w", encoding="utf-8") as f:
            json.dump(new_patch, f, ensure_ascii=False, indent=2)
        self.log(f"[武将库补丁] ✓ 已写入 {patch_path} ({len(new_patch['heroes'])}条武将自定义)")
        return patch_path

    # ---------------- 遣散策略(问题5B: 分类+分区域+黑名单+二次确认) ----------------
    def dismiss_areas_policy(self, areas, hero_chess_ids=None, hero_names=None, blacklist_names=None,
                            dismiss_all_bench=False):
        """按策略批量遣散. 调用者(UI)传:
           areas=["bench"]或["bench","battle","shop"] 多选区域;
           hero_chess_ids=[...] 或 hero_names=[...] 按筛选分类遣散;
           blacklist_names=["潘濬","吕旷","吕翔"] 按名字黑名单遣散;
           dismiss_all_bench=True = 全遣散(调用方必须做二次确认!).
        返回: {dismissed:[{goodsID,name,area}], skipped:[...]}"""
        from .protocol import LIVE
        result = {"dismissed": [], "skipped": []}
        # 1) build 候选卡池(按 areas 选)
        cards = []   # [{area, goodsID, chessID, name, card_dict}]
        me = LIVE.me or {}
        def _add_card(area, c, gid_source):
            if not isinstance(c, dict): return
            gid = c.get(gid_source) or c.get("goodsID")
            cid = c.get("chessID")
            if not gid and not cid: return
            try:
                gi = int(gid) if gid else 0
                ci = int(cid) if cid else 0
            except (TypeError, ValueError):
                return
            nm = IDMAP.name_of(ci or gi, chess_hint=True) or str(ci or gi)
            cards.append({"area": area, "goodsID": gi, "chessID": ci, "name": nm, "card": c})
        if "bench" in areas:
            for c in (me.get("_board") or []):
                _add_card("bench", c, "goodsID")
        if "battle" in areas:
            for gi in (me.get("_lineUp") or []):
                if not gi: continue
                try: gi = int(gi)
                except (TypeError, ValueError): continue
                # 从 board 里找对应卡(不一定存在, 退化成合成假卡)
                card = None
                for c in (me.get("_board") or []):
                    if str(c.get("goodsID")) == str(gi): card = c; break
                if card is None:
                    card = {"goodsID": gi}
                _add_card("battle", card, "goodsID")
        if "shop" in areas:
            for c in (LIVE.shop or []):
                _add_card("shop", c, "goodsID")
        # 2) blacklist: 名字包含匹配(允许部分)
        names_black_set = {str(n).strip() for n in (blacklist_names or []) if n}
        chess_ids_white_set = set()
        for x in (hero_chess_ids or []):
            try: chess_ids_white_set.add(int(x))
            except (TypeError, ValueError): _SIL.note("core/remote_ops.py:4009", "chess_ids_white_set.add(int(x))")
        names_white_set = {str(n).strip() for n in (hero_names or []) if n}
        for item in cards:
            name = str(item.get("name") or "")
            cid = int(item.get("chessID") or 0)
            gi = int(item.get("goodsID") or 0)
            match = False
            # 优先黑名单 → 只要命中就遣散
            if names_black_set and name in names_black_set:
                match = True
            # 分类筛选(白名单 名字+chessID 任一命中 → 遣散)
            if not match and chess_ids_white_set and cid and cid in chess_ids_white_set:
                match = True
            if not match and names_white_set and name in names_white_set:
                match = True
            # dismiss_all_bench + bench 区域 → 全部命中
            if not match and dismiss_all_bench and item.get("area") == "bench":
                match = True
            if not match:
                result["skipped"].append({"name": name, "reason": "未命中筛选/黑名单",
                                          "area": item.get("area")})
                continue
            # shop 区域不能遣散(商店没这个概念), 跳到 skipped
            if item.get("area") == "shop":
                result["skipped"].append({"name": name, "reason": "商店区域只用于识别(遣散在商店侧不被服务器支持)",
                                          "area": "shop"})
                continue
            if not gi:
                result["skipped"].append({"name": name, "reason": "缺少goodsID(无法协议遣散)"})
                continue
            ok = self.sell_card(gi)
            if ok:
                result["dismissed"].append({"name": name, "goodsID": gi, "area": item.get("area")})
            else:
                result["skipped"].append({"name": name, "goodsID": gi, "area": item.get("area"),
                                          "reason": "服务器未确认(卡已不在/过期)"})
        self.log(f"[遣散策略] 完成: 遣散成功{len(result['dismissed'])}张, "
                 f"跳过{len(result['skipped'])}张")
        return result

    # ---------------- 上阵编排(指定格子) ----------------
    def deploy_position(self, assignments, wait_dangxian_ms=3000):
        """按 assignments=[{position:1-7, goodsID:int, chessID:int}] 顺序上阵.
        若 chessID 落在 hero_picker dangxian_chessIDs → 上阵后 wait_buff_for, 保证当先触发.
        返回: {ok:[position], failed:[position]}"""
        import time as _t
        from .protocol import LIVE
        dangxian_set = set()
        # 读 hero_picker.json(问题六导出的) — 如果还没导出就走 LIVE 标签兜底
        try:
            from .config import DATA_DIR
            import os as _os
            p = _os.path.join(DATA_DIR, "hero_picker.json")
            if _os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    hp = json.load(f)
                dangxian_set = set(int(x) for x in (hp.get("hero_tags") or {}).get("dangxian_chessIDs") or [])
        except Exception:
            dangxian_set = set()
        result = {"ok": [], "failed": []}
        for item in assignments:
            if not isinstance(item, dict): continue
            pos = int(item.get("position") or 0)
            if not 1 <= pos <= 7:
                continue
            gid = int(item.get("goodsID") or 0)
            cid = int(item.get("chessID") or 0)
            if not gid:
                # goodsID 未知: 退化成按 position 放 chessID 进来, 但会失败
                result["failed"].append(pos); continue
            # 构造 7 格: 取当前 _lineUp, 仅改目标格
            line = [int(x) if x not in (None, "", "0") else 0 for x in (LIVE.me.get("_lineUp") or [])]
            while len(line) < 7: line.append(0)
            line = list(line[:7])
            line[pos - 1] = gid
            nm = IDMAP.name_of(cid or gid, chess_hint=True) or str(gid)
            if self.psend("cmsg.CReqChessLineUp", {"positionChess": line, "changeSeat": False}):
                # 同步本地状态
                try:
                    LIVE.me["_lineUp"] = list(line)
                    LIVE.me["_board"] = [c for c in (LIVE.me.get("_board") or [])
                                         if not (str(c.get("chessID")) == str(cid) and
                                                 str(c.get("goodsID")) == str(gid))]
                except Exception:
                    _SIL.note("core/remote_ops.py:4093", "LIVE.me['_lineUp'] = list(line)")
                self.log(f"[上阵-指定格] 格{pos} ← {nm}(goodsID={gid}) ✓")
                result["ok"].append(pos)
                # 当先等待: chessID 在 dangxian_set → wait_buff_for
                if cid and cid in dangxian_set:
                    self.log(f"[当先] {nm}(chessID={cid}) 开始等待 CHESS_ADD_BUFF(≤{wait_dangxian_ms}ms)...")
                    ok_buff, events_buff = LIVE.wait_buff_for(cid, timeout_ms=wait_dangxian_ms)
                    if ok_buff:
                        self.log(f"[当先] {nm} buff 已收到 ✓(共{len(events_buff)}条)")
                    else:
                        self.log(f"[当先] {nm} buff 未收到(超时{wait_dangxian_ms}ms, 继续下一张)")
                _t.sleep(0.15)
            else:
                self.log(f"[上阵-指定格] 格{pos} {nm} 服务器未确认(跳过)")
                result["failed"].append(pos)
        return result

    # ---------------- v4.102★ 备战区(_board)统一取值 ----------------
    def sync_board_from_hand(self, force=False):
        """把油猴侧的活体手牌数组同步进 LIVE.me._board。

        背景(2026-09-24 实锤): 招募阶段服务器并不推 benchCards 包, 协议侧
        LIVE.me._board 恒为 []; 但卡其实已经在 mgr.selfInfo.newLyHandChess /
        mgr.HandChess 里 —— 桥接脚本 getHandArray() 一直在用这份数据。
        结果是: DOM 扫描恒报"备战区0"、全量上阵恒报"备战区为空"。

        取值链: LIVE.me._board(非空且非 force) → AutoBuyAndSell.getFullState()
                的 benches(桥接侧 getInfo() 已按 getHandArray 的顺序回退)。
        返回: 最终 _board 的张数。
        """
        from .protocol import LIVE
        import time as _t
        me = LIVE.me if isinstance(LIVE.me, dict) else None
        if me is None:
            return 0
        cur = [c for c in (me.get("_board") or []) if isinstance(c, dict)]
        if cur and not force:
            return len(cur)
        try:
            st = self.get_full_state() or {}
        except Exception as e:
            self.log(f"[备战区同步] 读取完整状态失败: {e}")
            return len(cur)
        benches = st.get("benches") or []
        if not isinstance(benches, list) or not benches:
            # 活体也读不到 → 保留原 _board, 不把它清成空
            return len(cur)
        now = _t.time()
        merged, seen = [], set()
        for it in benches:
            if not isinstance(it, dict):
                continue
            raw_gid = it.get("goodsID") or it.get("cfgId")
            raw_cid = it.get("id") or it.get("chessID")
            try:
                gid = int(raw_gid) if raw_gid else 0
            except (TypeError, ValueError):
                gid = 0
            try:
                cid = int(raw_cid) if raw_cid else 0
            except (TypeError, ValueError):
                cid = 0
            if not gid and not cid:
                continue
            key = str(gid or cid)
            if key in seen:
                continue
            seen.add(key)
            merged.append({
                "goodsID": gid,
                "chessID": cid,
                "name": it.get("name") or "",
                "star": it.get("star") or 1,
                "pos": it.get("pos"),
                "_last_seen": now,
                "_board_ts": now,
            })
        if merged:
            me["_board"] = merged
            src = st.get("_handSrc")
            self.log(f"[备战区同步] _board ← 活体手牌 {len(merged)} 张" + (f" (源: {src})" if src else ""))
        return len(merged)

    # ---------------- 全量上阵(备战区 → 战斗区) ----------------
    def deploy_all_bench_chess(self, delay_per_item=DEPLOY_GAP_S):
        """v4.17★ 把备战区(_board)里所有未上阵的卡依次上阵, 直到战斗区满 7 格。
        调用者: ui/app.py a_deploy() 的"协议回退"分支。
        背景: 之前 app 调用的就是这个方法名, 但本类只实现了 deploy_bench/deploy_position,
              于是报 "'BridgeOps' object has no attribute 'deploy_all_bench_chess'"。
        协议: cmsg.CReqChessLineUp {"positionChess": [7个goodsID], "changeSeat": False}
              (与 deploy_position 同一套协议, 每次把整条 7 格阵容发一次)。
        返回: {deployed:int, skipped:int, failed:int, seats:[占据的格号]}
        """
        import time as _t
        from .protocol import LIVE
        result = {"deployed": 0, "skipped": 0, "failed": 0, "seats": []}
        me = LIVE.me or {}
        # 当前阵容(7格, 0=空位)
        line = []
        for x in (me.get("_lineUp") or []):
            try:
                line.append(int(x) if x not in (None, "", "0") else 0)
            except (TypeError, ValueError):
                line.append(0)
        while len(line) < 7:
            line.append(0)
        line = list(line[:7])
        used = {str(g) for g in line if g}
        # v4.102★ 与 F6「原生加卡」走同一份手牌数组(活体), 不再只信协议侧 _board:
        #   实锤过"刚买的是 A/B/C, 上阵的却是 X/Y/Z"—— 那是上一局残留在 _board 里的过期卡。
        #   force=True 每次都从活体重取; 活体读不到时才退回原 _board(见 sync_board_from_hand)。
        self.sync_board_from_hand(force=True)
        board = [c for c in (me.get("_board") or []) if isinstance(c, dict)]
        if not board:
            self.log("[全量上阵] 备战区为空(活体手牌与 _board 都没有卡)")
            return result
        for card in board:
            raw_gid = card.get("goodsID") or card.get("GoodsID")
            raw_cid = card.get("chessID") or card.get("ChessID")
            try:
                gid = int(raw_gid) if raw_gid else 0
                cid = int(raw_cid) if raw_cid else 0
            except (TypeError, ValueError):
                result["skipped"] += 1
                continue
            if not gid or str(gid) in used:
                result["skipped"] += 1
                continue
            free = next((i for i, v in enumerate(line) if not v), None)
            if free is None:
                self.log("[全量上阵] 战斗区已满7格, 停止")
                break
            line[free] = gid
            nm = IDMAP.name_of(cid or gid, chess_hint=True) or str(gid)
            if self.psend("cmsg.CReqChessLineUp", {"positionChess": list(line), "changeSeat": False}):
                used.add(str(gid))
                result["deployed"] += 1
                result["seats"].append(free + 1)
                self.log(f"[全量上阵] 格{free+1} ← {nm}(goodsID={gid}) ✓")
                _t.sleep(max(0.0, float(delay_per_item or 0)))
            else:
                line[free] = 0
                result["failed"] += 1
                self.log(f"[全量上阵] 格{free+1} {nm} 服务器未确认(跳过)")
        # 同步本地状态, 避免下一张又把已上阵的重复发一次
        try:
            LIVE.me["_lineUp"] = list(line)
            deployed_ids = {str(g) for g in line if g}
            LIVE.me["_board"] = [c for c in (LIVE.me.get("_board") or [])
                                 if str(c.get("goodsID")) not in deployed_ids]
        except Exception:
            _SIL.note("core/remote_ops.py:4244", "LIVE.me['_lineUp'] = list(line)")
        self.log(f"[全量上阵] 完成: 上阵{result['deployed']}张 "
                 f"跳过{result['skipped']} 失败{result['failed']}")
        return result

    def get_all_generals_for_ui(self):
        """从 id_map 获取所有自走棋武将的名称和ID列表，供UI选择使用"""
        try:
            return IDMAP.get_all_tavern_generals_for_ui()
        except Exception as e:
            self.log(f"[获取武将列表] 失败: {e}")
            return []

    def get_general_id_by_name(self, general_name: str):
        """从 id_map 根据武将名称查找对应的ID"""
        try:
            return IDMAP.get_general_id_by_name(general_name)
        except Exception as e:
            self.log(f"[名称转ID] 失败 ({general_name}): {e}")
            return None

    def _multi_buy_worker(self, target_general_ids: list, progress_callback):
        """多选购买武将的后台监测任务逻辑.
        v4.10★ 重构: 改成一次 eval 调用 window.AutoBuyAndSell.autoBuyNoRefresh 在油猴端跑,
        只监测不刷新, 200ms 一轮, 找到目标就买, 买完继续监测, 用户手动停止.
        Python 这里只负责轮询 STATE.get_state()["autoBuyProgress"] 实时回传进度."""
        self.log(f"[多选购买] 任务启动: 目标ID列表={target_general_ids}")

        # 一次 eval 启动油猴端 autoBuyNoRefresh (只监测不刷新)
        start_js = ("window.AutoBuyAndSell.autoBuyNoRefresh(" +
                    json.dumps(target_general_ids) + ");")
        try:
            self.send_recv("eval", js=start_js, timeout=2.0)
        except Exception as e:
            self.log(f"[多选购买] 启动失败: {e}")
            self._multi_buy_task_running = False
            self._multi_buy_task_stop_flag = False
            if progress_callback:
                progress_callback(f"启动失败: {e}")
            return

        # v4.13★ 关键修复: 等待第一个 push 周期 (1.2s) 再开始轮询, 避免读到上一次任务的残留 isRunning=false 立刻退出
        time.sleep(1.2)
        # 二次确认: 主动 eval 读 isRunning
        try:
            check_js = "(window.autoBuyRunningState&&window.autoBuyRunningState.isRunning)?'true':'false'"
            check_res = self.send_recv("eval", js=check_js, timeout=2.0)
            if check_res and check_res.strip().lower() == 'true':
                self.log("[多选购买] 油猟端确认任务已启动 (isRunning=true)")
            else:
                self.log(f"[多选购买] 警告: 油猟端 isRunning={check_res}, 可能未启动成功")
        except Exception:
            _SIL.note("core/remote_ops.py:4296", "check_js = '(window.autoBuyRunningState&&windo")

        # 轮询 /push 上报的 autoBuyProgress
        last_msg = ""
        last_bought = -1
        # v4.13★ 首次轮询宽容期: 前5次不因 isRunning=false 退出 (等 push 同步)
        poll_count = 0
        while True:
            if self._multi_buy_task_stop_flag:
                try:
                    self.send_recv("eval", js="window.AutoBuyAndSell.stopMultiBuyOnly();", timeout=2.0)
                except Exception:
                    _SIL.note("core/remote_ops.py:4308", "self.send_recv('eval', js='window.AutoBuyAndSe")
                break
            poll_count += 1
            st = STATE.get_state() or {}
            # v4.15★ 多选购买独立进度 (优先 multiBuyProgress, 兼容旧的 autoBuyProgress)
            prog = st.get("multiBuyProgress") or st.get("autoBuyProgress") or {}
            is_running = bool(prog.get("isRunning", False))
            bought = int(prog.get("totalBought", 0) or 0)
            msg = str(prog.get("lastMsg", "") or "")
            if bought != last_bought or msg != last_msg:
                last_bought = bought
                last_msg = msg
                self.log(f"[多选购买] 进度 已买{bought} | {msg}")
                if progress_callback:
                    progress_callback(f"已买{bought} | {msg}")
            # v4.13★ 前5次轮询 (约1.5s) 不因 isRunning=false 退出, 避免读到旧push残留
            if not is_running and poll_count > 5:
                break
            time.sleep(0.3)

        _st_final = STATE.get_state() or {}
        final_prog = _st_final.get("multiBuyProgress") or _st_final.get("autoBuyProgress") or {}
        final_bought = int(final_prog.get("totalBought", 0) or 0)
        final_failed = int(final_prog.get("failedCount", 0) or 0)
        self.log(f"[多选购买] 任务结束。总购买数量: {final_bought}" +
                 (f", 购买失败: {final_failed}" if final_failed else ""))
        # v4.15★ 任务退出前必下发油猴端独立停止, 修复"点停止没反应/停止后油猴还在跑"
        try:
            self.send_recv("eval", js="window.AutoBuyAndSell.stopMultiBuyOnly();", timeout=1.5)
        except Exception:
            _SIL.note("core/remote_ops.py:4338", "self.send_recv('eval', js='window.AutoBuyAndSe")
        self._multi_buy_task_running = False
        self._multi_buy_task_stop_flag = False
        if progress_callback:
            progress_callback(f"任务完成。总购买 {final_bought} 个。")


    def start_multi_buy_monitor(self, target_general_ids: list, progress_callback=None):
        """启动多选购买武将监测任务."""
        if self._multi_buy_task_running:
            self.log("[多选购买] 任务已在运行中。")
            if progress_callback:
                progress_callback("任务已在运行中。")
            return

        self._multi_buy_task_stop_flag = False
        self._multi_buy_task_thread = threading.Thread(
            target=self._multi_buy_worker,
            args=(target_general_ids, progress_callback),
            daemon=True
        )
        self._multi_buy_task_thread.start()
        self._multi_buy_task_running = True
        if progress_callback:
            progress_callback("多选购买监测任务已启动。")

    def stop_multi_buy_monitor(self):
        """停止多选购买武将监测任务."""
        if self._multi_buy_task_running:
            self.log("[多选购买] 请求停止任务。")
            self._multi_buy_task_stop_flag = True
        else:
            self.log("[多选购买] 没有正在运行的多选购买监测任务。")
        # v4.15★ 无论 Python 侧是否还在跑, 都下发一次油猴端"只停多选购买"停止(修复点停止没反应)
        try:
            self.send_recv("eval", js="window.AutoBuyAndSell.stopMultiBuyOnly();", timeout=1.5)
        except Exception:
            _SIL.note("core/remote_ops.py:4375", "self.send_recv('eval', js='window.AutoBuyAndSe")
