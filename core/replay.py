# -*- coding: utf-8 -*-
"""v4.184★ 自走棋桥接会话的录制 / 回放(离线复现对局, 不用真开游戏)。

设计(已读 core/bridge.py / core/protocol.py 实锤):
  - 桥往 Python 的入站消息全部走 BridgeState.push() —— 内含 cmdResults(控制回包) /
    latest(状态快照) / net(协议流水 -> net_sink -> app.py 搬运线程 -> LIVE.feed)。
  - 所以回放的单一接缝就是 BridgeState:
      · 录制: push()/send_cmd() 的双向消息落盘 data/replays/*.jsonl;
      · 回放: 把入站 push 原样喂回 push()(LIVE 被搬运线程自动填充), 出站命令的顺序
        驱动结果游标, take_result 返回录制时该命令对应的结果(含超时的 None)。
  - 关键点: 结果按【命令顺序】配对(不是按 id 数值, 也不是纯 FIFO), 这样即使某条命令
    在录制里超时(无结果), 游标也正确前进, 不会错位。

用法:
  from core import replay as RP
  RP.start_recording()              # 挂 STATE._replay_recorder, 开始双向录制
  ... 真实对局 ...
  path = RP.stop_recording()        # 返回录制文件
  RP.start_playback(path)           # 挂 STATE._replay_playback, 启动回放泵
  ... 跑 bot 逻辑(离线) ...
  RP.stop_playback()
"""
import os
import time
import json
import threading

from core.bridge import STATE  # 同一对象: 在此设置属性, 全进程(含绑定式 import)均可见

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPLAY_DIR = os.path.join(ROOT, "data", "replays")


class ReplayRecorder:
    """录制双向桥消息到 jsonl。每条: {"ts","dir":"in"/"out","data":...}。"""

    def __init__(self, session_id=None):
        os.makedirs(REPLAY_DIR, exist_ok=True)
        self.session = session_id or ("r-%d" % int(time.time() * 1000))
        self.path = os.path.join(REPLAY_DIR, self.session + ".jsonl")
        self._f = open(self.path, "a", encoding="utf-8")
        self._lock = threading.Lock()
        self.count = 0

    def in_event(self, payload):
        self._write("in", payload)

    def out_event(self, cmd):
        self._write("out", cmd)

    def _write(self, direction, data):
        try:
            with self._lock:
                self._f.write(json.dumps(
                    {"ts": round(time.time(), 3), "dir": direction, "data": data},
                    ensure_ascii=False) + "\n")
                self._f.flush()
                self.count += 1
        except Exception:
            pass

    def close(self):
        try:
            self._f.close()
        except Exception:
            pass


class ReplayController:
    """加载录制文件, 回放时: 按时间线泵入站 push + 按出站命令顺序供给结果。"""

    def __init__(self, path):
        self.path = path
        self.in_events = []          # [(ts, payload)] 入站事件(顺序)
        self.out_order = []          # 录制里的出站命令 id(顺序)
        self.result_by_outid = {}    # 录制 cmdResults: out_id -> 结果文本(可能缺失)
        self._cur_idx = -1           # 当前已发出到第几条出站命令
        self._cur_result = None      # 当前命令的录制结果(缓存, 多次 polltake_result 同值)
        self._running = False
        self._load()

    def _load(self):
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                d = ev.get("dir")
                if d == "in":
                    self.in_events.append((ev.get("ts", 0), ev.get("data", {}) or {}))
                    cr = (ev.get("data", {}) or {}).get("cmdResults") or {}
                    for k, v in cr.items():
                        try:
                            self.result_by_outid[int(k)] = v
                        except (ValueError, TypeError):
                            self.result_by_outid[k] = v
                elif d == "out":
                    cid = (ev.get("data", {}) or {}).get("id")
                    if cid is not None:
                        try:
                            cid = int(cid)
                        except (ValueError, TypeError):
                            pass
                        self.out_order.append(cid)

    # ---- 回放时由 bridge.send_cmd 调用: 推进命令游标, 缓存该命令的录制结果 ----
    def mark_send(self, cmd):
        self._cur_idx += 1
        if 0 <= self._cur_idx < len(self.out_order):
            rid = self.out_order[self._cur_idx]
            self._cur_result = self.result_by_outid.get(rid)
        else:
            self._cur_result = None

    # ---- 回放时由 bridge.take_result 调用(同一命令多次 poll 返回同一值) ----
    def pop_result(self):
        return self._cur_result

    def start(self):
        self._running = True
        t = threading.Thread(target=self._pump, daemon=True)
        t.start()

    def _pump(self):
        if not self.in_events:
            self._running = False
            return
        prev = self.in_events[0][0]
        for ts, payload in self.in_events:
            if not self._running:
                break
            dt = ts - prev
            prev = ts
            if dt > 0:
                time.sleep(min(dt, 2.0))  # 限速: 单步最多等 2s, 防回放卡死
            try:
                STATE.push(payload)
            except Exception:
                pass
        self._running = False

    def stop(self):
        self._running = False


# ---------------- 对外 API ----------------
def start_recording(session_id=None):
    """开始录制。返回 ReplayRecorder。"""
    rec = ReplayRecorder(session_id)
    STATE._replay_recorder = rec
    return rec


def stop_recording():
    """停止录制并返回文件路径(没在录制则返回 None)。"""
    rec = STATE._replay_recorder
    if rec is not None:
        rec.close()
        STATE._replay_recorder = None
        return rec.path
    return None


def start_playback(path):
    """开始回放指定录制文件。返回 ReplayController。"""
    ctrl = ReplayController(path)
    STATE._replay_playback = ctrl
    ctrl.start()
    return ctrl


def stop_playback():
    """停止回放。"""
    ctrl = STATE._replay_playback
    if ctrl is not None:
        ctrl.stop()
        STATE._replay_playback = None
        return True
    return False


def is_recording():
    return STATE._replay_recorder is not None


def is_playing():
    return STATE._replay_playback is not None
