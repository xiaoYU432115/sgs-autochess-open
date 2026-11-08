# -*- coding: utf-8 -*-
"""
宏录制/回放 (v3.43★ 新增)

录制来源(事件会被推到 recorder.on_event):
  1) Python UI动作: a_skip / a_buyall / a_deploy / 等等 (App 侧在动作入口处调 record())
  2) 游戏内小抄按钮回传: hudBtn (App._drain_hud_btns 里调 record())
  3) 协议直发: BridgeOps.psend (实锤成功才录, 避免学空包)
  4) 全局键鼠录制(v3.44★PIXEL模式): core.hotkeys.HM 触发录制回调时, 调
     record_keyboard_event/record_mouse_event; 回放用 ctypes SendInput 合成.

事件结构 Event = {
    "ts":   float (seconds, 录制时单调递增, 回放时按差值算等待)
    "kind": "ui" / "hudbtn" / "psend" / "wait" / "loop" / "deploy_all"
            / "key" / "mouse_press" / "mouse_scroll"
    "data": dict (与kind对应的参数)
}

录制模式 (v3.44★):
  "semantic" = 只录 1/2/3 (语义动作, 零鼠标零坐标, 后台友好, 默认)
  "pixel"    = 语义 + 全局键鼠 (按真实像素录制, 回放要保证游戏窗口位置基本一致)


回放加速(宏回放速度 scale):
    speed=1.0 → 按录制间隔真实重放;
    speed=2.0 → 所有间隔/2 (加速2倍);
    speed=0   → 事件之间不加等待(最大速度, 仍保留协议直发verify耗时)

存储: JSON格式, 目录 data/macros/*.json  (用户可自行编辑/导入/分享)
"""
import json
import os
import time
import threading
from .config import DATA_DIR, load_config, save_config
from core import silence as _SIL

MACRO_DIR = os.path.join(DATA_DIR, "macros")
os.makedirs(MACRO_DIR, exist_ok=True)

# ---------- 事件原子类型 ----------
# 每个 event.kind -> 回放时调用的 fn(app, data)
KIND_ORDER = ["hudbtn", "ui", "psend", "wait"]   # 仅展示用

# ---------- 录制模式常量 ----------
REC_MODES = {"SEMANTIC": "semantic", "PIXEL": "pixel"}

# ---------- 录制热键过滤黑名单 (功能控制键永远不录进宏,避免回放/录制自己的开关) ----------
# F5=扫 F6=买 F7=遣散 F8=回放 F9=停自动 F10=录开 F11=录停 F12=保留
# Pause=中断宏  0x13=VK_PAUSE
_HOTKEY_VK_FILTER = {
    0x74, 0x75, 0x76, 0x77, 0x78, 0x79, 0x7A, 0x7B,   # F5-F12
    0x13,                                                    # VK_PAUSE
}
_HOTKEY_NAME_FILTER = {"F5","F6","F7","F8","F9","F10","F11","F12","PAUSE"}

# ---------- ctypes 输入合成(PIXEL 回放用) ----------
try:
    import ctypes as _ctypes
    from ctypes import wintypes as _wt
    # ===== v3.50★ DPI 感知: 高DPI屏 GetCursorPos/SetCursorPos 不被系统缩放, 坐标=真实物理像素 =====
    #   避免: 150%缩放下 GetCursorPos 返回(1000,600)但实际物理像素是(1500,900)→回放时点歪.
    try:
        _ctx = _ctypes.windll.user32.GetWindowDpiAwarenessContext \
            if hasattr(_ctypes.windll.user32, 'GetWindowDpiAwarenessContext') else None
        if hasattr(_ctypes.windll.user32, 'SetProcessDpiAwarenessContext'):
            # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4 (最高质量,Per-Monitor V2)
            _ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)
        elif hasattr(_ctypes.windll.shcore, 'SetProcessDpiAwareness'):
            # PROCESS_PER_MONITOR_DPI_AWARE = 2
            try: _ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except Exception: _SIL.note("core/recorder.py:72", "_ctypes.windll.shcore.SetProcessDpiAwareness(2")
        else:
            try: _ctypes.windll.user32.SetProcessDPIAware()
            except Exception: _SIL.note("core/recorder.py:75", "_ctypes.windll.user32.SetProcessDPIAware()")
    except Exception: _SIL.note("core/recorder.py:76", "_ctx = _ctypes.windll.user32.GetWindowDpiAware")
    # v3.49★ wintypes兼容性补丁: 某些Python发行版 ctypes.wintypes 不暴露 Structure/Union/基础整型.
    #   3色框PIXEL监听初始化报错『module 'ctypes.wintypes' has no attribute 'Structure』就来自这里缺字段.
    #   做法: ctypes 顶层有 Structure/Union, 这里同步写回_wt; 基础C类型(WORD/DWORD/LONG等)也从 ctypes 标准c_*型取别名, 保证后面 _wt.Structure/_wt.LONG 都可用.
    if not hasattr(_wt, 'Structure') and hasattr(_ctypes, 'Structure'):
        _wt.Structure = _ctypes.Structure
    if not hasattr(_wt, 'Union') and hasattr(_ctypes, 'Union'):
        _wt.Union = _ctypes.Union
    _WT_FALLBACKS = {
        'WORD': _ctypes.c_ushort,
        'DWORD': _ctypes.c_uint32,
        'UINT': _ctypes.c_uint,
        'USHORT': _ctypes.c_ushort,
        'SHORT': _ctypes.c_short,
        'LONG': _ctypes.c_int32,
        'ULONG': _ctypes.c_uint32,
        'INT': _ctypes.c_int,
        'BOOL': _ctypes.c_int,
        'BYTE': _ctypes.c_ubyte,
        'CHAR': _ctypes.c_char,
        'WCHAR': _ctypes.c_wchar,
        'LPARAM': _ctypes.c_ssize_t,
        'WPARAM': _ctypes.c_size_t,
        'LRESULT': _ctypes.c_ssize_t,
        'ATOM': _ctypes.c_ushort,
        'HMODULE': _ctypes.c_void_p,
        'HINSTANCE': _ctypes.c_void_p,
        'HWND': _ctypes.c_void_p,
        'HHOOK': _ctypes.c_void_p,
        'HANDLE': _ctypes.c_void_p,
        'HDC': _ctypes.c_void_p,
        'HGDIOBJ': _ctypes.c_void_p,
        'HBITMAP': _ctypes.c_void_p,
        'HKEY': _ctypes.c_void_p,
    }
    for _name, _fall in _WT_FALLBACKS.items():
        if not hasattr(_wt, _name):
            try: setattr(_wt, _name, _fall)
            except Exception: _SIL.note("core/recorder.py:114", "setattr(_wt, _name, _fall)")
    _user32 = _ctypes.windll.user32
    _INPUT_MOUSE = 0
    _INPUT_KEYBOARD = 1
    _MOUSEEVENTF_LEFTDOWN = 0x0002
    _MOUSEEVENTF_LEFTUP = 0x0004
    _MOUSEEVENTF_RIGHTDOWN = 0x0008
    _MOUSEEVENTF_RIGHTUP = 0x0010
    _MOUSEEVENTF_WHEEL = 0x0800
    _MOUSEEVENTF_ABSOLUTE = 0x8000
    _KEYEVENTF_KEYUP = 0x0002
    _KEYEVENTF_SCANCODE = 0x0008
    _SM_CXSCREEN = 0
    _SM_CYSCREEN = 1
    _SendInput = _user32.SendInput
    _SendInput.argtypes = [_wt.UINT, _ctypes.c_void_p, _ctypes.c_int]
    _SendInput.restype = _wt.UINT
    _GetSystemMetrics = _user32.GetSystemMetrics
    _SetCursorPos = _user32.SetCursorPos
    _MapVirtualKeyW = _user32.MapVirtualKeyW
except Exception:
    _ctypes = None
    _user32 = None


class MacroRecorder:
    """单例: 全局录制/宏管理"""

    def __init__(self, get_app=None):
        self.get_app = get_app
        self._recording = False
        self._rec_name = None
        self._events = []
        self._rec_ts0 = None
        self._lock = threading.RLock()
        # v4.101★ 事件分类累计数: 界面那行「PIXEL键鼠=x(移动y/点击z)」以前是【每 400ms 遍历
        #   全表】算出来的 —— 录到几万条时这一遍就是几万次。改成录制时顺便累加, 取数是 O(1)。
        self._kind_counts = {}
        # v4.101★ 上限: 见 record() 里的说明(只 append 不裁剪会让"录得越久越卡")。
        self._max_events = 20000
        # 回放状态
        self._playing = False
        self._stop_flag = False
        # v3.49★合成输入抑制标记: SendInput回放键鼠事件期间, 全局LL钩子/keyboard兜底会收到"自己合成的"同一个键,
        #   导致F8回放时再次触发_macro_play→死循环(maximum recursion depth exceeded).
        #   解决: _play_key/_play_mouse_press/_play_mouse_scroll SendInput前后 置位=True,
        #         回放键鼠事件的回调/全局低阶钩子 检测到本标记=True时直接return不处理.
        self._in_synth_input = False
        # 回放统计回调(给UI显示)
        self.on_play_progress = None   # fn(idx, total, speed)
        self.on_event = None           # fn(event) — 便于调试
        # ---- v3.49★录制模式(默认PIXEL·键鼠物理录制;UI可切换回SEMANTIC协议录制) ----
        self.mode = REC_MODES["PIXEL"]
        # --- v3.49★录制来源与窗口过滤 (G=指定窗口, F=全局抓) ---
        self.capture_scope = "GLOBAL"   # "GLOBAL" / "WINDOW"
        self.capture_hwnd = None        # int (Win32 hwnd) 仅当 capture_scope="WINDOW" 生效
        self.capture_win_title = ""
        self.capture_win_rect = None    # (left, top, right, bottom)
        self.capture_save_hwnd = True   # 是否把窗口hwnd保存进宏文件metadata

    def set_mode(self, mode):
        m = REC_MODES.get(str(mode).upper(), REC_MODES.get(mode, REC_MODES["PIXEL"]))
        # fallback兼容字符串直接等于semantic/pixel
        if mode in ("semantic", "pixel"):
            m = mode
        self.mode = m

    # ================================================================
    # v3.49★ 录制来源控制: F=全局抓 / G=指定窗口(hwnd) —— 像 OBS 的 "窗口捕获 / 全局捕获"
    # ================================================================
    def set_capture_scope(self, scope: str, hwnd=None, win_title: str = "", save_hwnd: bool = True):
        """scope in ('GLOBAL', 'F', 'WINDOW', 'G'). F/GLOBAL=全局; G/WINDOW=仅指定窗口(hwnd)."""
        up = str(scope or "").upper()
        if up in ("GLOBAL", "F"): self.capture_scope = "GLOBAL"
        elif up in ("WINDOW", "G"): self.capture_scope = "WINDOW"
        else: self.capture_scope = "GLOBAL"
        try: self.capture_hwnd = int(hwnd) if hwnd else None
        except (TypeError, ValueError): self.capture_hwnd = None
        self.capture_win_title = str(win_title or "")
        self.capture_win_rect = None
        self.capture_save_hwnd = bool(save_hwnd)
        # 预先取一次窗口矩形(若窗口存在)
        if self.capture_scope == "WINDOW" and self.capture_hwnd and _user32 is not None and _ctypes is not None:
            try:
                class _R(_wt.Structure):
                    _fields_ = [("left", _wt.LONG),("top",_wt.LONG),("right",_wt.LONG),("bottom",_wt.LONG)]
                r = _R()
                if _user32.GetWindowRect(int(self.capture_hwnd), _ctypes.byref(r)):
                    self.capture_win_rect = (int(r.left), int(r.top), int(r.right), int(r.bottom))
            except Exception:
                _SIL.note("core/recorder.py:204", "class _R(_wt.Structure): _fields_ = [('left', ")
        return (self.capture_scope, self.capture_hwnd, self.capture_win_title, self.capture_win_rect)

    @classmethod
    def enum_windows(cls, include_blank=True, prioritize_browsers=True, min_size=(15, 10)):
        """枚举所有顶层窗口 + 兜底搜索可见子窗口(解决WeGame/内嵌浏览器包一层父窗口导致找不到游戏的问题).
        返回 [(hwnd:int, title:str, w:int, h:int, class_name:str, is_child:bool)] 列表.
        用于UI: 宏录制Tab 窗口下拉 / 刷新按钮.  类似OBS窗口选择器.
        v3.50★: include_blank默认True(以前=False→漏了很多游戏窗口); min_size放宽到15x10; 新增EnumChildWindows兜底."""
        if _user32 is None or _ctypes is None: return []
        results = []
        seen = set()
        try:
            class _R(_wt.Structure):
                _fields_ = [("left",_wt.LONG),("top",_wt.LONG),("right",_wt.LONG),("bottom",_wt.LONG)]
            def _try_add(hwnd, is_child=False):
                try:
                    if int(hwnd) in seen: return
                    if not bool(_user32.IsWindowVisible(hwnd)): return
                    # 标题
                    length = _user32.GetWindowTextLengthW(hwnd)
                    buf = _ctypes.create_unicode_buffer(max(length+1, 4))
                    _user32.GetWindowTextW(hwnd, buf, length+1)
                    title = str(buf.value or "").strip()
                    if (not title) and not include_blank and not is_child: return
                    # 尺寸(按窗口客户区或WinRect取较大的)
                    r = _R(); w = h = 0
                    if bool(_user32.GetWindowRect(int(hwnd), _ctypes.byref(r))):
                        w = int(r.right - r.left); h = int(r.bottom - r.top)
                    try:
                        cr = _R()
                        if bool(_user32.GetClientRect(int(hwnd), _ctypes.byref(cr))):
                            cw = int(cr.right - cr.left); ch = int(cr.bottom - cr.top)
                            if cw * ch > w * h: w, h = cw, ch
                    except Exception: _SIL.note("core/recorder.py:238", "cr = _R()")
                    min_w, min_h = min_size or (15, 10)
                    # v3.50★: 关键字命中的不按尺寸杀(避免三国杀/LayaAir极小启动窗被误杀)
                    kw_hit = False
                    if (title or "").lower():
                        tl = (title or "").lower()
                        if any(k in tl for k in ("layaair","自走棋","三国杀","tavernchess","sgs","棋战","酒馆","chrome","edge","firefox","浏览器","wegame","微端","游戏")):
                            kw_hit = True
                    if not kw_hit and (w < max(15, min_w) or h < max(10, min_h)) and not is_child:
                        return
                    # 类名
                    cls_buf = _ctypes.create_unicode_buffer(128)
                    _user32.GetClassNameW(int(hwnd), cls_buf, 127)
                    class_name = str(cls_buf.value or "")
                    cnl = class_name.lower()
                    if not kw_hit and any(k in cnl for k in ("chrome_widgetwin","microsoftedge","firefox","msedge","360se","qqbrowser","laya","sdl","opengl","cocos","unity")):
                        kw_hit = True
                    if not kw_hit and (w < max(15, min_w) or h < max(10, min_h)): return
                    seen.add(int(hwnd))
                    results.append((int(hwnd), title, w, h, class_name, bool(is_child)))
                except Exception:
                    _SIL.note("core/recorder.py:259", "if int(hwnd) in seen: return")
            # 1) 顶层窗口
            @_ctypes.WINFUNCTYPE(_wt.BOOL, _wt.HWND, _wt.LPARAM)
            def _cb_top(hwnd, _lp):
                _try_add(hwnd, False); return True
            _user32.EnumWindows(_cb_top, 0)
            # 2) 兜底: 对部分"壳窗口"(Chrome_WidgetWin_0/Laya空标题类等) 再递归挖一层子窗口(找有实际标题/大尺寸的内嵌渲染窗)
            shell_parents = []
            for it in list(results):
                hw, tt, ww, hh, cn, ic = it
                cnl = (cn or "").lower()
                ttl = (tt or "").lower()
                if (not ttl) or any(k in cnl for k in ("chrome_widgetwin_0","shell_embedding","atl","mdiclient","tabwindow")):
                    shell_parents.append(hw)
            @_ctypes.WINFUNCTYPE(_wt.BOOL, _wt.HWND, _wt.LPARAM)
            def _cb_child(hwnd, _lp):
                _try_add(hwnd, True); return True
            for phw in shell_parents[:40]:
                try: _user32.EnumChildWindows(int(phw), _cb_child, 0)
                except Exception: _SIL.note("core/recorder.py:278", "_user32.EnumChildWindows(int(phw), _cb_child, ")
        except Exception:
            _SIL.note("core/recorder.py:280", "class _R(_wt.Structure): _fields_ = [('left',_")
        if not results: return []
        # 去重(按hwnd)并还原成5元组 (兼容旧代码: 去掉is_child字段)
        dedup = []
        _s2 = set()
        for it in results:
            if it[0] in _s2: continue
            _s2.add(it[0]); dedup.append(it)
        results5 = [(a, b, c, d, e) for (a, b, c, d, e, _ic) in dedup]
        if prioritize_browsers and results5:
            # 置顶排序: 1) 标题/LayaAir/自走棋/三国杀/tavernchess最高 2) Chrome/Edge/Firefox浏览器 3) 其余字典序
            def _score(item):
                _hwnd, title, _w, _h, cn = item
                tl = (title or "").lower()
                cnl = (cn or "").lower()
                s = 0
                kw_high = ["layaair", "自走棋", "三国杀", "tavernchess", "sgs", "棋战", "酒馆", "wegame", "游戏", "微端"]
                kw_browser_class = ["chrome_widgetwin", "microsoftedge", "firefox", "msedge", "360se", "qqbrowser"]
                if any(k in tl for k in kw_high): s += 5000
                if any(k in cnl for k in kw_browser_class): s += 3000
                if any(k in tl for k in ("chrome", "edge", "firefox", "浏览器")): s += 1000
                # 窗口越大越靠前
                s += min(((_w or 0) * (_h or 0)) // 40000, 500)
                return (-s, tl.lower())
            results5.sort(key=_score)
        return results5

    @classmethod
    def find_windows_by_keyword(cls, keyword):
        """按关键字(标题或类名)搜索窗口, 返回同enum_windows格式. 用于下拉找不到时点搜索框兜底."""
        if not keyword: return cls.enum_windows()
        kl = str(keyword or "").lower()
        all_ = cls.enum_windows(include_blank=True, prioritize_browsers=False)
        hit = []
        for it in all_:
            hw, tt, w, h, cn = it
            if (kl in (tt or "").lower()) or (kl in (cn or "").lower()): hit.append(it)
        return hit or all_

    @classmethod
    def foreground_window(cls):
        """返回当前最前台窗口 (hwnd,title,w,h,class_name) 或 None. 用于自动取当前窗口兜底."""
        if _user32 is None or _ctypes is None: return None
        try:
            hwnd = int(_user32.GetForegroundWindow() or 0)
            if not hwnd: return None
            length = _user32.GetWindowTextLengthW(hwnd) + 1
            buf = _ctypes.create_unicode_buffer(max(length, 4))
            _user32.GetWindowTextW(hwnd, buf, length)
            title = str(buf.value or "").strip()
            class _R(_wt.Structure):
                _fields_ = [("left",_wt.LONG),("top",_wt.LONG),("right",_wt.LONG),("bottom",_wt.LONG)]
            r = _R(); w = h = 0
            if bool(_user32.GetWindowRect(hwnd, _ctypes.byref(r))):
                w = int(r.right - r.left); h = int(r.bottom - r.top)
            cls_buf = _ctypes.create_unicode_buffer(128)
            _user32.GetClassNameW(hwnd, cls_buf, 127)
            class_name = str(cls_buf.value or "")
            return (hwnd, title, w, h, class_name)
        except Exception:
            return None

    @classmethod
    def pick_window_by_cursor(cls, timeout_sec=3, on_progress=None, on_done=None):
        """光标点击选择窗口(像OBS一样): 调用后3秒内, 把目标窗口带到前台并点一下左键, 自动选中当前最前台窗口hwnd.
        返回 (hwnd, title, w, h, class_name) 或 None.  期间可调用 on_progress(sec_left) 用于UI更新倒计时."""
        if _user32 is None or _ctypes is None: return None
        import time as _t
        try:
            # 先把当前前景窗口记下来 (避免算成Python助手)
            orig_fg = _user32.GetForegroundWindow()
        except Exception:
            orig_fg = None
        deadline = _t.time() + max(1, int(timeout_sec))
        last_fg = orig_fg
        while _t.time() < deadline:
            sec_left = int(deadline - _t.time()) + 1
            try:
                if callable(on_progress): on_progress(sec_left)
            except Exception: _SIL.note("core/recorder.py:359", "if callable(on_progress): on_progress(sec_left")
            try:
                fg = _user32.GetForegroundWindow()
                if fg and fg != last_fg and fg != orig_fg and bool(_user32.IsWindowVisible(fg)):
                    hwnd = int(fg)
                    break
            except Exception:
                _SIL.note("core/recorder.py:366", "fg = _user32.GetForegroundWindow()")
            _t.sleep(0.1)
        else:
            hwnd = None
            try:
                fg = _user32.GetForegroundWindow()
                if fg and fg != orig_fg: hwnd = int(fg)
            except Exception: _SIL.note("core/recorder.py:373", "fg = _user32.GetForegroundWindow()")
        if not hwnd:
            if callable(on_done): on_done(None, "超时未检测到前台窗口变化")
            return None
        # 取对应信息
        try:
            length = _user32.GetWindowTextLengthW(hwnd) + 1
            buf = _ctypes.create_unicode_buffer(max(length, 4))
            _user32.GetWindowTextW(hwnd, buf, length)
            title = str(buf.value or "").strip()
            class _R(_wt.Structure):
                _fields_ = [("left",_wt.LONG),("top",_wt.LONG),("right",_wt.LONG),("bottom",_wt.LONG)]
            r = _R(); w = h = 0
            if bool(_user32.GetWindowRect(hwnd, _ctypes.byref(r))):
                w = int(r.right - r.left); h = int(r.bottom - r.top)
            cls_buf = _ctypes.create_unicode_buffer(128)
            _user32.GetClassNameW(hwnd, cls_buf, 127)
            class_name = str(cls_buf.value or "")
            info = (hwnd, title, w, h, class_name)
        except Exception:
            info = (hwnd, "", 0, 0, "")
        try:
            if callable(on_done): on_done(info, "OK")
        except Exception: _SIL.note("core/recorder.py:396", "if callable(on_done): on_done(info, 'OK')")
        return info

    _pixels_tid = None
    _pixels_stop = False
    _last_mouse = None            # (x, y, left_state, right_state)
    _pixel_key_callback_ref = None  # 防止被 GC
    _pixel_hm_bound = False

    @classmethod
    def ensure_pixel_listeners(cls, get_hotkeys_manager=None, get_app=None, log=None):
        """启动 PIXEL 模式监听器(只启动一次)。
        - Keyboard: 调 HM 提供的『on_before_block』回调; 如无 HM, 则用 user32.GetAsyncKeyState 5ms 轮询(全覆盖 VK)
        - Mouse   : user32.GetCursorPos + GetAsyncKeyState(VK_LBUTTON/RBUTTON/MBUTTON/XBUTTON + 滚轮)
        - v3.49★ 新增 log 参数: 失败时不再静默 return False, 而调用 log(str) 给UI写日志(便于诊断『PIXEL 0事件』原因).
        """
        if cls._pixels_tid is not None: return True
        # ---- 基础环境检查(需要ctypes/user32, 非Windows就失败) ----
        if _ctypes is None or _user32 is None:
            try:
                if callable(log): log("[宏] PIXEL监听失败: 非Windows环境/ctypes.user32加载失败")
            except Exception: _SIL.note("core/recorder.py:417", "if callable(log): log('[宏] PIXEL监听失败: 非Windows")
            return False
        try:
            # VK 代码表 (只轮询常用的, 避免 256 个轮询占 CPU)
            KEY_VK = {
                "BACKSPACE":0x08,"TAB":0x09,"RETURN":0x0D,"SHIFT":0x10,"CONTROL":0x11,"MENU":0x12,
                "PAUSE":0x13,"CAPITAL":0x14,"ESCAPE":0x1B,"SPACE":0x20,"PRIOR":0x21,"NEXT":0x22,
                "END":0x23,"HOME":0x24,"LEFT":0x25,"UP":0x26,"RIGHT":0x27,"DOWN":0x28,
                "SELECT":0x29,"PRINT":0x2A,"EXECUTE":0x2B,"SNAPSHOT":0x2C,"INSERT":0x2D,"DELETE":0x2E,
                "HELP":0x2F,
                "0":0x30,"1":0x31,"2":0x32,"3":0x33,"4":0x34,"5":0x35,"6":0x36,"7":0x37,"8":0x38,"9":0x39,
                "A":0x41,"B":0x42,"C":0x43,"D":0x44,"E":0x45,"F":0x46,"G":0x47,"H":0x48,"I":0x49,"J":0x4A,
                "K":0x4B,"L":0x4C,"M":0x4D,"N":0x4E,"O":0x4F,"P":0x50,"Q":0x51,"R":0x52,"S":0x53,"T":0x54,
                "U":0x55,"V":0x56,"W":0x57,"X":0x58,"Y":0x59,"Z":0x5A,
                "LWIN":0x5B,"RWIN":0x5C,
                "NUMPAD0":0x60,"NUMPAD1":0x61,"NUMPAD2":0x62,"NUMPAD3":0x63,"NUMPAD4":0x64,
                "NUMPAD5":0x65,"NUMPAD6":0x66,"NUMPAD7":0x67,"NUMPAD8":0x68,"NUMPAD9":0x69,
                "MULTIPLY":0x6A,"ADD":0x6B,"SEPARATOR":0x6C,"SUBTRACT":0x6D,"DECIMAL":0x6E,"DIVIDE":0x6F,
                "F1":0x70,"F2":0x71,"F3":0x72,"F4":0x73,"F5":0x74,"F6":0x75,"F7":0x76,"F8":0x77,
                "F9":0x78,"F10":0x79,"F11":0x7A,"F12":0x7B,
                "NUMLOCK":0x90,"SCROLL":0x91,
                "LSHIFT":0xA0,"RSHIFT":0xA1,"LCONTROL":0xA2,"RCONTROL":0xA3,"LMENU":0xA4,"RMENU":0xA5,
                "OEM_1":0xBA,"OEM_PLUS":0xBB,"OEM_COMMA":0xBC,"OEM_MINUS":0xBD,"OEM_PERIOD":0xBE,"OEM_2":0xBF,
                "OEM_3":0xC0,"OEM_4":0xDB,"OEM_5":0xDC,"OEM_6":0xDD,"OEM_7":0xDE,"OEM_8":0xDF,
            }
            VK_REV = {}
            for k,v in KEY_VK.items(): VK_REV[v] = k
            # 补充鼠标按钮虚拟码
            VK_LBUTTON = 0x01; VK_RBUTTON = 0x02; VK_MBUTTON = 0x04
            VK_XBUTTON1 = 0x05; VK_XBUTTON2 = 0x06
            VK_MOUSEWHEEL_VK_HACK = 0xFF  # 占位, 滚轮不通过这个
        except Exception as _e0:
            try:
                import traceback as _tb
                if callable(log): log(f"[宏] PIXEL监听(VK定义初始化)失败: {_e0}\n{_tb.format_exc()[-900:]}")
            except Exception: _SIL.note("core/recorder.py:452", "import traceback as _tb")
            return False

        # 尝试 HM 接管 key 事件 (如果调用方能提供 HM 引用)
        try:
            if callable(get_hotkeys_manager):
                hm = get_hotkeys_manager()
                if hm is not None:
                    # 安装 HM 前置回调: 在 HM 真正吞/放行键之前, 先通知 recorder 录下来
                    rec = MACRO
                    def _on_key_hook(vk, is_down, scan, vk_name=""):
                        try:
                            if MACRO.mode != "pixel": return
                            if not MACRO._recording: return
                            MACRO.record_keyboard_event(int(vk), bool(is_down), int(scan or 0), str(vk_name or ""))
                        except Exception:
                            _SIL.note("core/recorder.py:468", "if MACRO.mode != 'pixel': return")
                    cls._pixel_key_callback_ref = _on_key_hook
                    try:
                        # 若 HM 提供该 API
                        if hasattr(hm, "add_pre_callback"):
                            hm.add_pre_callback(_on_key_hook)
                            cls._pixel_hm_bound = True
                        elif hasattr(hm, "set_on_before_block"):
                            hm.set_on_before_block(_on_key_hook)
                            cls._pixel_hm_bound = True
                    except Exception:
                        cls._pixel_hm_bound = False
        except Exception:
            cls._pixel_hm_bound = False

        # 后台线程: keyboard(GetAsyncKeyState 兜底) + mouse (必跑)
        # v4.101★ 5ms → 10ms。5ms 意味着这个线程每秒醒来 200 次, 每次都要抢一次 GIL 去做
        #   一堆跨进程 syscall —— Tk 主线程就是这样被【饿死】的(表现: 一开始录制, 整个助手
        #   点哪都慢半拍)。10ms = 100Hz 对鼠标完全够用(而且下面还有"位移>=15px 才记"的判据)。
        POLL_MS = 0.010
        import time as _t
        _GAS = _user32.GetAsyncKeyState
        _GAS.restype = _wt.SHORT
        _GetCursorPos = _user32.GetCursorPos
        class POINT(_wt.Structure):
            _fields_ = [("x", _wt.LONG), ("y", _wt.LONG)]
        prev_keys = {}
        _prev_wheel_ts = 0.0
        cls._pixels_stop = False
        cls._pixel_err_count = 0
        cls._last_move_ts = 0.0            # 上次记录move事件时间戳(用于记录MOVE节流,避免几百万条)
        cls._last_move_xy = None          # 上次记录move事件坐标

        # v4.101★ 这个 ctypes 结构体类【只在启动时建一次】。以前它被写在 _inside_target_window
        #   的里面 —— 而那个函数每 5ms 调一次, 于是每秒 200 次 "建一个类"(metaclass + 字段布局
        #   + 类型对象分配), 是 Python 里最贵的操作之一。窗口矩形同样缓存: 它一局都不会变,
        #   却每 5ms 跨进程问一次 GetWindowRect。
        class _R2(_wt.Structure):
            _fields_ = [("left", _wt.LONG), ("top", _wt.LONG), ("right", _wt.LONG), ("bottom", _wt.LONG)]

        _rect_cache = {"hwnd": 0, "ts": 0.0, "r": None}

        def _inside_target_window(x, y):
            """仅WINDOW模式: 判定(x,y)是否在目标窗口矩形内,不在就不录该鼠标事件."""
            if MACRO.capture_scope != "WINDOW": return True
            hwnd = MACRO.capture_hwnd
            if not hwnd: return True
            try:
                now0 = _t.time()
                r = _rect_cache["r"]
                # 同一个窗口的矩形 0.5 秒问一次就够(窗口被拖动最多延迟半秒生效)
                if (r is None) or (_rect_cache["hwnd"] != hwnd) or ((now0 - _rect_cache["ts"]) > 0.5):
                    r = _R2()
                    if not _user32.GetWindowRect(int(hwnd), _ctypes.byref(r)): return True
                    _rect_cache["hwnd"] = hwnd
                    _rect_cache["ts"] = now0
                    _rect_cache["r"] = r
                return (int(r.left) <= int(x) <= int(r.right)) and (int(r.top) <= int(y) <= int(r.bottom))
            except Exception:
                return True

        def loop():
            wheel_last_delta = 0
            wheel_last_ts = 0.0
            logged_start = False
            _last_kb_poll = 0.0      # v4.101★ 键盘兜底轮询的时间戳(下面按 20ms 降频)
            while not cls._pixels_stop:
                now = _t.time()
                try:
                    if MACRO.mode == "pixel" and MACRO._recording and not logged_start:
                        logged_start = True
                        try:
                            if callable(log):
                                scope_txt = "F全局捕获"
                                if MACRO.capture_scope == "WINDOW":
                                    scope_txt = (f"G窗口捕获(hwnd={MACRO.capture_hwnd} "
                                                f"{MACRO.capture_win_rect or ''} "
                                                f"{(MACRO.capture_win_title or '')[:30]})")
                                log(f"[宏] PIXEL轮询启动: {scope_txt} | HM绑定={'是' if cls._pixel_hm_bound else '否(兜底轮询VK:'+str(len(KEY_VK))+')'} POLL={int(POLL_MS*1000)}ms")
                        except Exception: _SIL.note("core/recorder.py:547", "if callable(log): scope_txt = 'F全局捕获' if MACRO")
                    if (not MACRO._recording) or MACRO.mode != "pixel":
                        logged_start = False
                    # 1) mouse pos + buttons
                    pt = POINT()
                    got = _GetCursorPos(_ctypes.byref(pt))
                    if got:
                        lbtn = bool(_GAS(VK_LBUTTON) & 0x8000)
                        rbtn = bool(_GAS(VK_RBUTTON) & 0x8000)
                        mbtn = bool(_GAS(VK_MBUTTON) & 0x8000)
                        xb1 = bool(_GAS(VK_XBUTTON1) & 0x8000)
                        xb2 = bool(_GAS(VK_XBUTTON2) & 0x8000)
                        x = int(pt.x); y = int(pt.y)
                        inside = _inside_target_window(x, y)
                        if cls._last_mouse is None:
                            cls._last_mouse = (x, y, lbtn, rbtn, mbtn, xb1, xb2)
                        else:
                            lx, ly, ll, lr, lm, lx1, lx2 = cls._last_mouse
                            # ---- v3.50★ 记录鼠标移动事件(之前缺失是"0事件"真因: 只动鼠标不点不会录)
                            #  节流: 每 >=30ms 或 位移 >=15px 才录一次(选锦囊拖卡15px也能命中,避免之前40px太严)
                            if inside and (lx != x or ly != y):
                                dx = abs(x - lx); dy = abs(y - ly)
                                dt = now - (cls._last_move_ts or 0.0)
                                if (dx + dy >= 15) or (dt >= 0.030):
                                    MACRO.record_mouse_event("move", x, y, "", 0, False)
                                    cls._last_move_ts = now
                                    cls._last_move_xy = (x, y)
                            # 按键/滚轮事件只在目标窗口内才录 (窗口过滤)
                            if inside:
                                if lbtn != ll:
                                    MACRO.record_mouse_event("press", x, y, "left", 0, bool(lbtn))
                                if rbtn != lr:
                                    MACRO.record_mouse_event("press", x, y, "right", 0, bool(rbtn))
                                if mbtn != lm:
                                    MACRO.record_mouse_event("press", x, y, "middle", 0, bool(mbtn))
                                if xb1 != lx1:
                                    MACRO.record_mouse_event("press", x, y, "x1", 0, bool(xb1))
                                if xb2 != lx2:
                                    MACRO.record_mouse_event("press", x, y, "x2", 0, bool(xb2))
                            cls._last_mouse = (x, y, lbtn, rbtn, mbtn, xb1, xb2)
                    # 2) keyboard (HM 没接管 或 接管失败时, 这里兜底) —— 窗口过滤模式下,键盘不做过滤(跨窗口也录,跟OBS窗口捕获的键盘行为一致)
                    if not cls._pixel_hm_bound:
                        # v4.101★ 键盘兜底【降频到 20ms(50Hz)】。以前是每 5ms 把 KEY_VK
                        #   (约 90 个键)全扫一遍 = 90 × 200 = 【每秒 1.8 万次】GetAsyncKeyState
                        #   跨进程调用, 全挤在一个 Python 线程里, 每次都要抢 GIL —— 这才是
                        #   "一录就像整个电脑都变慢"的根因(助手界面和它抢同一个 GIL)。
                        #   20ms 完全够: 人敲键的最快间隔也在 30ms 以上, 50Hz 不会漏键。
                        if (now - _last_kb_poll) >= 0.020:
                            _last_kb_poll = now
                            if MACRO.mode == "pixel" and MACRO._recording:
                                for name, vk in KEY_VK.items():
                                    st = _GAS(vk)
                                    pressed_now = bool(st & 0x8000)
                                    before = bool(prev_keys.get(vk, False))
                                    if pressed_now != before:
                                        MACRO.record_keyboard_event(vk, pressed_now, 0, name)
                                        prev_keys[vk] = pressed_now
                except Exception as _e1:
                    try:
                        cls._pixel_err_count += 1
                        if cls._pixel_err_count < 3 and callable(log):
                            import traceback as _tb
                            log(f"[宏] PIXEL轮询异常#{cls._pixel_err_count}: {_e1}\n{_tb.format_exc()[-700:]}")
                    except Exception: _SIL.note("core/recorder.py:610", "cls._pixel_err_count += 1")
                # 3) 轮询间隔: 录制中 5ms, 待机 30ms
                if MACRO.mode == "pixel" and MACRO._recording:
                    _t.sleep(POLL_MS)
                else:
                    _t.sleep(0.030)

        t = threading.Thread(target=loop, name="MacroPixels", daemon=True)
        try:
            t.start()
        except Exception as _e:
            import traceback as _tb
            if callable(log):
                log(f"[宏] PIXEL线程启动失败: {_e}\n{_tb.format_exc()[-900:]}")
            return False
        cls._pixels_tid = t
        if callable(log):
            try:
                hm_txt = "是" if cls._pixel_hm_bound else ("否(兜底轮询VK:" + str(len(KEY_VK)) + ")")
                log(f"[宏] ✓ PIXEL监听已就绪(线程id:{getattr(t,'native_id',t.ident)} HM绑定={hm_txt}")
            except Exception: _SIL.note("core/recorder.py:630", "hm_txt = '是' if cls._pixel_hm_bound else ('否(兜")
        return True

    # ---------- 录制 ----------
    def start(self, *args, **kwargs):
        """
        推荐关键字调用: start(name=None, mode="semantic|pixel")
        —— 兼容旧版 只传 mode 字符串的写法 (第一个参数如果不是合法文件名字符串则自动识别为 mode)
        """
        name = kwargs.get("name", None)
        mode = kwargs.get("mode", None)
        # 位置参数: 允许 start("SEMANTIC") 或 start(mode="PIXEL")
        if args and name is None and mode is None:
            a0 = args[0]
            if isinstance(a0, str) and a0.upper() in REC_MODES:
                mode = a0
                name = kwargs.get("name")
            else:
                name = a0
                if len(args) >= 2: mode = args[1]
        # 兜底
        if mode is not None:
            self.set_mode(mode)
        with self._lock:
            # v4.101★ 【二次 start 会静默清空已录内容】—— 以前这里上来就是 self._events = [],
            #   于是再按一次 F10(或点两次「开始录制」)前面录好的就全没了, 界面上还看不出来。
            #   现在: 已经在录就什么都不做, 把当前名字原样交回去(不清空、不重置计时)。
            if self._recording:
                return self._rec_name
            if name is None: name = self._default_name()
            name = (str(name)).strip() or self._default_name()
            self._rec_name = name
            self._events = []
            self._kind_counts = {}
            self._rec_ts0 = time.time()
            self._recording = True
            self._saved_len = -1          # 新的一轮, 清掉上一轮的"已存盘"标记
            self._saved_name = ""
            self._saved_path = None
        return name

    def stop(self, save=False):
        with self._lock:
            self._recording = False
            evs = list(self._events)
            name = self._rec_name
            cnt = len(evs)
        path = None
        if save:
            # v4.101★ 别重复写盘: 以前 stop() 之后 _events 还留着, 再按一次 F11 会把
            #   【同一批事件再存一遍同名文件】(path_of 是覆盖写), 回放时看到两份一样的。
            #   这里只在"内容或名字变了"时才真的写一次。
            if (getattr(self, "_saved_len", -1) == cnt
                    and getattr(self, "_saved_name", "") == name
                    and getattr(self, "_saved_path", None)):
                path = self._saved_path
            else:
                try: path = self.save(name)
                except Exception as e: path = "SAVE_FAIL:" + str(e)
                self._saved_len = cnt
                self._saved_name = name
                self._saved_path = path
        return {"name": name, "count": cnt, "path": path, "events": evs if not save else None}

    def is_recording(self):
        with self._lock:
            return self._recording

    def rec_name(self):
        with self._lock:
            return self._rec_name

    def events_count(self):
        with self._lock:
            return len(self._events)

    # ---- 便捷辅助 (UI 直接调) ----
    def has_script(self, path=None):
        """检查回放是否有脚本: 如果给了 path 就看文件存在 + 有 events; 否则看内存里 _events 非空."""
        if path:
            try:
                import os as _o
                if _o.path.isfile(path):
                    with open(path, "r", encoding="utf-8") as f:
                        obj = json.load(f)
                    return isinstance(obj, dict) and len(list(obj.get("events") or [])) > 0
            except Exception: return False
        return self.events_count() > 0

    def play(self, path=None, ops=None, app_ref=None, speed=None, loop=None):
        """更直观的 API: play(path=xxx, app_ref=self) —— 失败抛异常"""
        if self._playing: raise RuntimeError("已有宏在回放中")
        if path and not self.has_script(path):
            raise FileNotFoundError("宏脚本为空或不存在: " + str(path))
        obj = self.load(path) if path else {"events": self._events, "name": self.rec_name(), "event_count": self.events_count()}
        from .config import load_config as _lc
        cfg = _lc() or {}
        sp = float(speed if speed is not None else (cfg.get("macro", {}).get("speed", 1.0)))
        lp = int(loop if loop is not None else (cfg.get("macro", {}).get("loop", 1)))
        prog = None
        if app_ref is not None and hasattr(app_ref, "_macro_on_progress"):
            prog = app_ref._macro_on_progress
        if MACRO.get_app is None and app_ref is not None:
            MACRO.get_app = lambda: app_ref
        self.playback(obj, speed=sp, loop=lp, on_progress=prog)
        return True

    def _default_name(self):
        return "macro_" + time.strftime("%Y%m%d_%H%M%S")

    # ---------- 插入事件(对外 API, App/remote_ops 入口处调) ----------
    def record(self, kind, data):
        if not self._recording:
            return
        # semantic 模式: 过滤掉 key/mouse_* 等键鼠事件(仅录语义)
        if self.mode == REC_MODES["SEMANTIC"] and kind in ("key", "mouse_press", "mouse_move", "mouse_scroll"):
            return
        ev = {"ts": time.time(), "kind": str(kind), "data": dict(data or {})}
        try:
            if callable(self.on_event):
                self.on_event(ev)
        except Exception:
            _SIL.note("core/recorder.py:752", "if callable(self.on_event): self.on_event(ev)")
        with self._lock:
            if self._recording:
                self._events.append(ev)
                self._kind_counts[str(kind)] = self._kind_counts.get(str(kind), 0) + 1
                # v4.101★ 上限。以前这个 list 【只 append 不裁剪】: 鼠标移动 30ms 一条 →
                #   录 10 分钟约 2 万条、1 小时 12 万条; 而界面每 400ms 要整份拷贝一遍(还要抢
                #   这同一把锁) —— 录得越久, 界面刷新和录制互相拖慢, 这就是"录制越久越卡"的
                #   残留根因。超上限就砍掉【最老的】(回看要看的是末尾那一段)。
                over = len(self._events) - int(self._max_events or 0)
                if over > 0:
                    del self._events[:over]

    # ---------- v4.101★ 给界面刷新用的两个"只拷增量"的入口 ----------
    #   以前界面是 `list(MACRO._events)` —— 整份拷贝, O(N), 录到几万条时一次拷贝几万个
    #   对象, 还占着录制线程要用的那把锁。现在: 增量刷新只拷"上次画过之后新增的那段"。
    def snapshot_since(self, n=0):
        """返回 (新增事件列表, 总条数, 分类计数)。n = 界面已经画到第几条。"""
        with self._lock:
            total = len(self._events)
            n = int(n or 0)
            if n < 0 or n > total:
                n = 0                      # 重录/清空过 → 让界面整表重画
            return list(self._events[n:]), total, dict(self._kind_counts)

    def snapshot_tail(self, n=1200):
        """返回 (末尾 n 条, 总条数, 分类计数) —— 整表重画时用, 别把几万条都塞进 Treeview。"""
        with self._lock:
            total = len(self._events)
            n = max(1, int(n or 1200))
            return list(self._events[-n:]), total, dict(self._kind_counts)

    # ---------- 键鼠录制包装 (core.hotkeys.HM/UI 调) ----------
    def record_keyboard_event(self, vk_code: int, down: bool, scan_code: int = 0, vk_name: str = ""):
        """PIXEL 模式才录; semantic 自动过滤;
        v3.61★: 强化过滤逻辑，确保功能热键(F5-F12, Pause, F10/F11)不被录入宏文件."""
        try:
            vk = int(vk_code)
            # 1. 按 VK 过滤 (0x70-0x7B 是 F1-F12)
            if 0x70 <= vk <= 0x7B: 
                return
            if vk == 0x13: # VK_PAUSE
                return
            if vk in _HOTKEY_VK_FILTER: 
                return
                
            # 2. 按名称过滤
            nm = (str(vk_name or "")).strip().upper()
            # v4.101★ 补上 F1-F4: 以前内层只认 F5-F12, 于是 F1~F4 【会被录进宏并回放】——
            #   而这几个键是助手自己的功能键(F1 帮助 / F2 改名 …)或游戏的热键, 回放时按下去
            #   等于隔空触发别的功能。外层那个 startswith("F") 因此也成了废判断, 一并收口。
            if nm in _HOTKEY_NAME_FILTER or (len(nm) <= 3 and nm.startswith("F")
                                             and nm[1:].isdigit()):
                return
        except Exception:
            _SIL.note("core/recorder.py:807", "vk = int(vk_code)")
            
        self.record("key", {"vk": int(vk_code), "down": bool(down),
                            "scan": int(scan_code), "vk_name": str(vk_name)})

    def record_mouse_event(self, kind: str, x: int, y: int, button="left", delta=0, down=True):
        """kind in ['press','scroll','move']
        v3.50★: move 不再静默丢弃, 存成 mouse_move; 之前的节流(PIXEL监听线程50ms/40px)已经避免爆炸."""
        if kind == "press":
            self.record("mouse_press", {"x": int(x), "y": int(y),
                                        "button": str(button or "left"), "down": bool(down)})
        elif kind == "scroll":
            self.record("mouse_scroll", {"x": int(x), "y": int(y), "delta": int(delta)})
        elif kind == "move":
            # 节流已在 PIXEL 监听线程(loop)里做(>=50ms 或 位移>=40px 才调一次本方法)
            self.record("mouse_move", {"x": int(x), "y": int(y)})

    # ---------- 事件便捷包装(常用动作封装成1条) ----------
    def add_wait(self, seconds):
        """在录制/编辑模式下手动插入一条等待"""
        with self._lock:
            if self._recording:
                self._events.append({"ts": time.time(), "kind": "wait",
                                     "data": {"seconds": float(seconds)}})
                return True
        return False

    def add_loop_marker(self, count):
        """在录制模式下插入"循环标记": 回放时把从上一个loop_start到当前之间重复count次.
        (简化实现)"""
        with self._lock:
            if self._recording:
                self._events.append({"ts": time.time(), "kind": "loop",
                                     "data": {"count": int(count)}})

    # ---------- 宏文件管理 ----------
    @staticmethod
    def list_macros():
        try:
            files = [f for f in os.listdir(MACRO_DIR) if f.lower().endswith(".json")]
        except FileNotFoundError:
            return []
        return sorted(files)

    @staticmethod
    def path_of(name):
        if not name.lower().endswith(".json"):
            name = name + ".json"
        return os.path.join(MACRO_DIR, name)

    def save(self, name=None):
        """把当前录制中的事件写入 data/macros/<name>.json"""
        with self._lock:
            evs = list(self._events)
            nm = name or self._rec_name or self._default_name()
        path = self.path_of(nm)
        # v3.49★ metadata: 记录录制模式/来源/窗口hwnd/尺寸 → 回放时能切前景窗口
        metadata = {
            "mode": self.mode,
            "capture_scope": self.capture_scope,
            "capture_hwnd": (int(self.capture_hwnd) if self.capture_save_hwnd and self.capture_hwnd else None),
            "capture_win_title": (str(self.capture_win_title) if self.capture_save_hwnd else ""),
            "capture_win_rect": (list(self.capture_win_rect) if self.capture_save_hwnd and self.capture_win_rect else None),
            "capture_save_hwnd": bool(self.capture_save_hwnd),
        }
        obj = {
            "name": os.path.splitext(os.path.basename(path))[0],
            "version": "v3.50",
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "events": evs,
            "event_count": len(evs),
            "metadata": metadata,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        return path

    def load(self, name):
        path = self.path_of(name)
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        return obj

    def delete(self, name):
        path = self.path_of(name)
        if os.path.exists(path):
            os.remove(path)
            return True
        return False

    def rename(self, old_name, new_name):
        """v3.49★宏改名: old/new都可以带不带.json后缀.
        重命名文件 data/macros/<old>.json → <new>.json, 并同步obj的name字段.
        返回(new_path, old_path); 目标已存在=抛FileExistsError; 源不存在=抛FileNotFoundError."""
        import re as _re
        def clean(s):
            s = os.path.splitext(str(s or "").strip())[0]
            # 去除非法路径字符: \/:*?"<>|
            return _re.sub(r'[\\/:*?"<>|\s]+', '_', s).strip('_') or ""
        old_c = clean(old_name); new_c = clean(new_name)
        if not old_c or not new_c:
            raise ValueError("宏名不能为空")
        if old_c == new_c:
            return (self.path_of(old_c), self.path_of(old_c))
        op = self.path_of(old_c)
        np = self.path_of(new_c)
        if not os.path.exists(op):
            raise FileNotFoundError(op)
        if os.path.exists(np):
            raise FileExistsError(np)
        # 先修改obj里的name字段,再移动文件(保证内容一致性)
        try:
            obj = self.load(old_c)
            obj["name"] = new_c
            obj["renamed_from"] = old_c
            obj["renamed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            with open(op, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False, indent=2)
        except Exception:
            _SIL.note("core/recorder.py:928", "obj = self.load(old_c)")
        os.rename(op, np)
        return (np, op)

    def list_scripts(self):
        """返回data/macros目录下所有.json宏文件名(不含后缀),按修改时间倒序"""
        try:
            if not os.path.isdir(MACRO_DIR):
                return []
            arr = []
            for fn in os.listdir(MACRO_DIR):
                if not fn.lower().endswith(".json"): continue
                p = os.path.join(MACRO_DIR, fn)
                try:
                    mt = os.path.getmtime(p)
                except Exception:
                    mt = 0
                arr.append((os.path.splitext(fn)[0], mt))
            arr.sort(key=lambda x: x[1], reverse=True)
            return [n for (n, _) in arr]
        except Exception:
            return []

    # ---------- 回放 ----------
    def is_playing(self):
        return self._playing

    def stop_playback(self):
        self._stop_flag = True

    def abort(self):
        """兼容UI侧『_macro_abort』调用"""
        self.stop_playback()

    @classmethod
    def bring_window_to_foreground(cls, hwnd=None, win_title="", timeout_sec=1.5, log=None):
        """把目标窗口带到前台(回放前必做一步,否则鼠标点到桌面其他东西).
        优先用 hwnd(整数), 失败再按标题模糊搜索. 返回 True=成功激活."""
        if _user32 is None or _ctypes is None: return False
        target = None
        try:
            if hwnd:
                try:
                    hw = int(hwnd)
                    if hw and bool(_user32.IsWindow(hw)) and bool(_user32.IsWindowVisible(hw)):
                        target = hw
                except Exception: _SIL.note("core/recorder.py:974", "hw = int(hwnd)")
            if target is None and win_title:
                # 按标题模糊枚举找
                import re as _re
                ttl = str(win_title or "").lower()
                if ttl:
                    wins = cls.enum_windows(include_blank=True, prioritize_browsers=False)
                    for (hw, tt, _w, _h, _cn) in wins:
                        if ttl in (tt or "").lower():
                            target = int(hw); break
            if not target: return False
            # 先最小化还原(解决图标化的窗口无法直接SetForeground)
            SW_RESTORE = 9
            try:
                if bool(_user32.IsIconic(target)):
                    _user32.ShowWindow(target, SW_RESTORE)
            except Exception: _SIL.note("core/recorder.py:990", "if bool(_user32.IsIconic(target)): _user32.Sho")
            try:
                _user32.BringWindowToTop(target)
            except Exception: _SIL.note("core/recorder.py:993", "_user32.BringWindowToTop(target)")
            try:
                _user32.SetForegroundWindow(target)
            except Exception: _SIL.note("core/recorder.py:996", "_user32.SetForegroundWindow(target)")
            try:
                # SetForegroundWindow有时失败(当前进程不是foreground lock owner), 附加给前台线程输入再试
                cur_fg = int(_user32.GetForegroundWindow() or 0)
                if cur_fg and cur_fg != target:
                    cur_tid = _user32.GetWindowThreadProcessId(cur_fg, None)
                    tar_tid = _user32.GetWindowThreadProcessId(target, None)
                    if cur_tid and tar_tid and cur_tid != tar_tid:
                        _user32.AttachThreadInput(cur_tid, tar_tid, True)
                        try:
                            _user32.SetForegroundWindow(target)
                            _user32.BringWindowToTop(target)
                        finally:
                            try: _user32.AttachThreadInput(cur_tid, tar_tid, False)
                            except Exception: _SIL.note("core/recorder.py:1010", "_user32.AttachThreadInput(cur_tid, tar_tid, Fa")
            except Exception: _SIL.note("core/recorder.py:1011", "cur_fg = int(_user32.GetForegroundWindow() or ")
            # 验证: 等最多timeout_sec看是否真的到了前台
            import time as _t
            end = _t.time() + max(0.1, float(timeout_sec or 0))
            while _t.time() < end:
                try:
                    fg = int(_user32.GetForegroundWindow() or 0)
                    if fg == target: return True
                except Exception: _SIL.note("core/recorder.py:1019", "fg = int(_user32.GetForegroundWindow() or 0)")
                _t.sleep(0.05)
            try:
                fg = int(_user32.GetForegroundWindow() or 0)
                return fg == target
            except Exception:
                return False
        except Exception as e:
            try:
                if callable(log): log(f"[宏] 激活目标窗口失败: {e}")
            except Exception: _SIL.note("core/recorder.py:1029", "if callable(log): log(f'[宏] 激活目标窗口失败: {e}')")
            return False

    def playback(self, macro_obj, speed=1.0, loop=1, on_progress=None, extra_foreground_hwnd=None):
        """重放 macro_obj (dict, 来自 load()).
        返回 (played_events, success_count, fail_count)
        v3.50★: 回放前自动根据 metadata 激活目标窗口到前台(避免鼠标点到桌面上别的东西)
        extra_foreground_hwnd: UI上用户当前选中的窗口(即使宏是GLOBAL模式,也切它到前台=方便,避免G模式录的才切)
        """
        if self._playing:
            raise RuntimeError("已有宏正在回放, 请先停止")
        if not isinstance(macro_obj, dict) or "events" not in macro_obj:
            raise ValueError("非法宏对象(缺少events字段)")
        try:
            self._playing = True
            self._stop_flag = False
            app = self.get_app() if callable(self.get_app) else None
            if app is None:
                raise RuntimeError("App未就绪(需要在主窗口初始化后回放)")
            ops = app.ops
            log = getattr(app, "log", (lambda *a, **k: None))
            # ----- v3.50★ 回放前自动激活目标窗口 -----
            meta = (macro_obj.get("metadata") if isinstance(macro_obj.get("metadata"), dict) else None) or {}
            hw_from_meta = meta.get("capture_hwnd")
            ttl_from_meta = meta.get("capture_win_title") or ""
            need_activate_by_meta = (meta.get("capture_scope") == "WINDOW"
                                     or hw_from_meta or ttl_from_meta)
            if need_activate_by_meta:
                hw = hw_from_meta; ttl = ttl_from_meta
                ok = self.bring_window_to_foreground(hwnd=hw, win_title=ttl, timeout_sec=1.8, log=log)
                try:
                    if ok: log(f"[宏] ✓ 回放前已激活目标窗口 (hwnd={hw or '按标题'} {str(ttl)[:40]})")
                    else: log(f"[宏] ⚠ 回放前未成功激活目标窗口(hwnd={hw} title='{ttl}')——请手动把游戏带到前台,避免回放点歪")
                except Exception: _SIL.note("core/recorder.py:1062", "if ok: log(f'[宏] ✓ 回放前已激活目标窗口 (hwnd={hw or '按标")
            else:
                # GLOBAL模式: ①若用户传了 extra_foreground_hwnd(UI下拉选了窗口)→主动激活 ②否则如果助手当前在前台→提醒
                brought = False
                if extra_foreground_hwnd:
                    try:
                        brought = self.bring_window_to_foreground(hwnd=int(extra_foreground_hwnd),
                                                                  win_title="", timeout_sec=1.8, log=log)
                        if brought: log(f"[宏] ✓ GLOBAL模式·按当前下拉选中的窗口(hwnd={extra_foreground_hwnd})已激活到前台")
                    except Exception: brought = False
                if not brought:
                    try:
                        cur = self.foreground_window()
                        if cur:
                            tl = (cur[1] or "").lower()
                            assistant_title_lower = "三国杀自走棋助手"
                            if assistant_title_lower in tl:
                                log("[宏] ⚠ 当前最前=助手窗口(非游戏). PIXEL回放会真实移动鼠标,请先把游戏带到前台(或者先在下拉选好游戏窗口,回放会自动切)")
                    except Exception: _SIL.note("core/recorder.py:1080", "cur = self.foreground_window()")
            evs = list(macro_obj["events"])
            total = len(evs)
            ok = 0; fail = 0; played = 0
            def cb(idx):
                try:
                    if on_progress: on_progress(idx, total)
                    elif self.on_play_progress: self.on_play_progress(idx, total, speed)
                except Exception:
                    _SIL.note("core/recorder.py:1089", "if on_progress: on_progress(idx, total) elif s")
            prev_ts = None
            # 支持 loop 倍循环回放
            for loop_i in range(max(1, int(loop))):
                if self._stop_flag: break
                # 循环块(loop event count) 的处理简化:
                #   如果遇到 kind=="loop" 事件, 就回放到上一次 loop_start 记录的index再重放data.count次.
                #   简单实现: 不做嵌套循环.
                pending_loop = None   # (count, start_index_in_evs)
                for i, ev in enumerate(evs):
                    if self._stop_flag: break
                    cb(played)
                    kind = ev.get("kind"); data = ev.get("data") or {}
                    # ---- 间隔等待 ----
                    try:
                        tnow = float(ev.get("ts"))
                    except (TypeError, ValueError):
                        tnow = None
                    if prev_ts is not None and tnow is not None and speed is not None:
                        dt = max(0.0, tnow - prev_ts)
                        if speed and float(speed) > 0:
                            wait_s = dt / float(speed)
                            # wait 事件自带固定时间(不参与按录制间隔加速), 但也再叠加一点
                        else:
                            wait_s = 0.0
                        # 分片sleep, 每0.1s检查一次_stop_flag
                        end = time.time() + wait_s
                        while time.time() < end and not self._stop_flag:
                            time.sleep(min(0.1, end - time.time()))
                    prev_ts = tnow
                    # ---- 按 kind 执行 ----
                    success = self._play_one(app, ops, kind, data, speed)
                    played += 1
                    if success:
                        ok += 1
                    else:
                        fail += 1
            cb(played)
            return played, ok, fail
        finally:
            self._playing = False
            self._stop_flag = False

    # ---------- 回放单步(核心映射) ----------
    def _play_one(self, app, ops, kind, data, speed):
        log = getattr(app, "log", (lambda *a, **k: None))
        try:
            if kind == "hudbtn":
                name = data.get("name")
                if not name:
                    return False
                # 模拟小抄按钮触发 → 直接调 _drain_hud_btns 里的映射表
                fn_map = {"skip": app.a_skip, "buyall": app.a_buyall,
                          "sellidle": app.a_sell_idle, "deploy": app.a_deploy,
                          "ready": app.a_ready,
                          "recruitStart": None, "recruitStop": None}
                if name == "recruitStart":
                    if not app.recruiter.alive(): app.toggle_recruit(); return True
                    return False
                if name == "recruitStop":
                    if app.recruiter.alive(): app.toggle_recruit(); return True
                    return False
                fn = fn_map.get(name)
                if callable(fn):
                    fn(); return True
                log(f"[宏回放] 未知hudBtn: {name}")
                return False
            if kind == "ui":
                act = data.get("action")
                fn_map = {"skip": app.a_skip, "buyall": app.a_buyall,
                          "deploy": app.a_deploy, "sellidle": app.a_sell_idle,
                          "ready": app.a_ready, "spell": app.a_spell,
                          "refresh": app.a_refresh, "sell_idle": app.a_sell_idle}
                fn = fn_map.get(act or data.get("name"))
                if callable(fn):
                    fn(); return True
                log(f"[宏回放] 未知ui action: {act}")
                return False
            if kind == "psend":
                if not ops:
                    return False
                name = data.get("name"); payload = data.get("payload") or {}
                if not name:
                    return False
                # 回放不做pid学习; 已学过的psend直接发
                return bool(ops.psend(name, payload))
            if kind == "wait":
                secs = float(data.get("seconds") or 0.2)
                # speed>0 时等比缩短
                if speed and float(speed) > 0: secs = secs / float(speed)
                end = time.time() + secs
                while time.time() < end and not self._stop_flag:
                    time.sleep(min(0.1, end - time.time()))
                return True
            if kind == "deploy_all":
                app.a_deploy()
                return True
            # ============ v3.44★ PIXEL 键鼠回放 ============
            if kind == "key":
                return self._play_key(data)
            if kind == "mouse_press":
                return self._play_mouse_press(data)
            if kind == "mouse_scroll":
                return self._play_mouse_scroll(data)
            if kind == "mouse_move":
                return self._play_mouse_move(data)
            # 未知 kind
            log(f"[宏回放] 忽略未知 kind={kind}")
            return False
        except Exception as e:
            log(f"[宏回放] 异常 kind={kind} data={data}: {e.__class__.__name__}:{e}")
            return False

    # ============ v3.44★ PIXEL 输入合成实现 ============
    @staticmethod
    def _play_key(data):
        if not _user32 or _ctypes is None:
            return False
        vk = int(data.get("vk") or 0)
        if not vk:
            return False
        scan = int(data.get("scan") or 0)
        down = bool(data.get("down", True))
        # v3.49★合成输入抑制: SendInput前后置 MACRO._in_synth_input=True, 全局钩子收到"自己合成的F8等键"
        # 时立即return, 避免『回放F8→合成F8→LL钩子又收到F8→_macro_play→死循环』递归
        MACRO._in_synth_input = True
        try:
            MacroRecorder._send_key(vk=vk, down=down, scan=scan)
            # 给LL钩子留出一点处理窗口(>典型键盘输入延迟≈10ms)后再清
            time.sleep(0.02)
            return True
        except Exception:
            return False
        finally:
            try: MACRO._in_synth_input = False
            except Exception: _SIL.note("core/recorder.py:1224", "MACRO._in_synth_input = False")

    @staticmethod
    def _play_mouse_move(data):
        """v3.50★ 回放 mouse_move 事件: 纯 SetCursorPos 移动到(x,y),不做点击(拖拽的移动靠这个插值)."""
        if not _user32 or _ctypes is None:
            return False
        x = int(data.get("x") or 0)
        y = int(data.get("y") or 0)
        MACRO._in_synth_input = True
        try:
            _SetCursorPos(x, y)
            # move 不加额外sleep(靠事件之间的录制间隔控制节奏)
            return True
        except Exception:
            return False
        finally:
            try: MACRO._in_synth_input = False
            except Exception: _SIL.note("core/recorder.py:1242", "MACRO._in_synth_input = False")

    @staticmethod
    def _play_mouse_press(data):
        if not _user32 or _ctypes is None:
            return False
        x = int(data.get("x") or 0)
        y = int(data.get("y") or 0)
        button = str(data.get("button") or "left").lower()
        down = bool(data.get("down", True))
        MACRO._in_synth_input = True
        try:
            _SetCursorPos(x, y)
            flags = 0
            if button == "left":
                flags = _MOUSEEVENTF_LEFTDOWN if down else _MOUSEEVENTF_LEFTUP
            elif button == "right":
                flags = _MOUSEEVENTF_RIGHTDOWN if down else _MOUSEEVENTF_RIGHTUP
            else:
                flags = _MOUSEEVENTF_LEFTDOWN if down else _MOUSEEVENTF_LEFTUP
            MacroRecorder._send_mouse_dw(flags, 0, 0)
            time.sleep(0.02)
            return True
        except Exception:
            return False
        finally:
            try: MACRO._in_synth_input = False
            except Exception: _SIL.note("core/recorder.py:1269", "MACRO._in_synth_input = False")

    @staticmethod
    def _play_mouse_scroll(data):
        if not _user32 or _ctypes is None:
            return False
        x = int(data.get("x") or 0)
        y = int(data.get("y") or 0)
        delta = int(data.get("delta") or 120)
        MACRO._in_synth_input = True
        try:
            _SetCursorPos(x, y)
            MacroRecorder._send_mouse_dw(_MOUSEEVENTF_WHEEL, delta, 0)
            time.sleep(0.02)
            return True
        except Exception:
            return False
        finally:
            try: MACRO._in_synth_input = False
            except Exception: _SIL.note("core/recorder.py:1288", "MACRO._in_synth_input = False")

    @staticmethod
    def _send_key(vk, down=True, scan=0):
        """SendInput 单个按键"""
        import struct
        KEYBD_SIZE = 28   # sizeof(INPUT) w/ keyboard union on 64-bit: 4(type) + 24(union) = 28; align 8 -> ?
        # 精确对齐: 使用 ctypes 结构更稳, 直接拼 struct 避免平台差异
        class KEYBDINPUT(_ctypes.Structure):
            _fields_ = [("wVk", _wt.WORD), ("wScan", _wt.WORD),
                        ("dwFlags", _wt.DWORD), ("time", _wt.DWORD),
                        ("dwExtraInfo", _ctypes.c_ulonglong)]
        class MOUSEINPUT(_ctypes.Structure):
            _fields_ = [("dx", _ctypes.c_long), ("dy", _ctypes.c_long),
                        ("mouseData", _wt.DWORD), ("dwFlags", _wt.DWORD),
                        ("time", _wt.DWORD),
                        ("dwExtraInfo", _ctypes.c_ulonglong)]
        class DUMMYUNION(_ctypes.Union):
            _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]
        class INPUT(_ctypes.Structure):
            _anonymous_ = ("u",)
            _fields_ = [("type", _wt.DWORD), ("u", DUMMYUNION)]
        inp = INPUT()
        inp.type = _INPUT_KEYBOARD
        inp.ki.wVk = int(vk) & 0xFFFF
        inp.ki.wScan = int(scan) & 0xFFFF
        inp.ki.dwFlags = 0
        if scan:
            inp.ki.dwFlags |= _KEYEVENTF_SCANCODE
        if not down:
            inp.ki.dwFlags |= _KEYEVENTF_KEYUP
        _SendInput(1, _ctypes.byref(inp), _ctypes.sizeof(INPUT))

    @staticmethod
    def _send_mouse_dw(flags, data, time_val=0):
        class KEYBDINPUT(_ctypes.Structure):
            _fields_ = [("wVk", _wt.WORD), ("wScan", _wt.WORD),
                        ("dwFlags", _wt.DWORD), ("time", _wt.DWORD),
                        ("dwExtraInfo", _ctypes.c_ulonglong)]
        class MOUSEINPUT(_ctypes.Structure):
            _fields_ = [("dx", _ctypes.c_long), ("dy", _ctypes.c_long),
                        ("mouseData", _wt.DWORD), ("dwFlags", _wt.DWORD),
                        ("time", _wt.DWORD),
                        ("dwExtraInfo", _ctypes.c_ulonglong)]
        class DUMMYUNION(_ctypes.Union):
            _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]
        class INPUT(_ctypes.Structure):
            _anonymous_ = ("u",)
            _fields_ = [("type", _wt.DWORD), ("u", DUMMYUNION)]
        inp = INPUT()
        inp.type = _INPUT_MOUSE
        inp.mi.dwFlags = int(flags)
        inp.mi.mouseData = int(data)
        inp.mi.time = int(time_val or 0)
        _SendInput(1, _ctypes.byref(inp), _ctypes.sizeof(INPUT))


# 全局单例 (App 在 __init__ 里调 MACRO.get_app = lambda: self 绑定)
MACRO = MacroRecorder()
