# -*- coding: utf-8 -*-
"""
V3.44★ 全局热键 + 可屏蔽按键 (Win32 低级键盘钩子).

为什么不继续用 `keyboard` 库?
  - 它不支持"吞掉某几个键(让游戏端完全收不到)"
  - 多个实例同时注册时会冲突
  - F11/F8 等浏览器/系统默认快捷键常被它漏拦截

本实现:
  - SetWindowsHookEx(WH_KEYBOARD_LL) — 最低层, 所有按键先经过这里
  - 用户可"屏蔽 F10/F11/F8/Pause": 返回 1 (非零) 直接吞掉, 下游任何程序都收不到
  - 热键回调全部转成 `root.after(0, fn)` 回到 Tk 主线程执行, 不会卡死钩子线程
  - Windows 规范 WH_KEYBOARD_LL 钩子线程必须跑消息循环 (GetMessage/DispatchMessage).
    本模块单独启动一个守护线程跑消息循环, App 关闭时自动 Unhook.
"""
import threading
import sys
import os
import time
import ctypes
from ctypes import wintypes
from core import silence as _SIL

# ============= Win32 类型/常量 =============
WH_KEYBOARD_LL = 13
HC_ACTION = 0
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
LLKHF_UP = 0x80
WH_KEYBOARD_LL_KBDLLHOOKSTRUCT = ctypes.c_long   # vkCode,scanCode,flags,time,dwExtraInfo

# VK 码表: 只列我们实际会用到的几个(功能键 Fn+Ctrl/Alt/Pause)
VK = {
    "F5": 0x74, "F6": 0x75, "F7": 0x76, "F8": 0x77, "F9": 0x78,
    "F10": 0x79, "F11": 0x7A, "F12": 0x7B,
    "CONTROL": 0x11, "MENU": 0x12, "SHIFT": 0x10,   # Alt = VK_MENU
    "SPACE": 0x20, "R": 0x52, "B": 0x42, "D": 0x44, "X": 0x58,
    "PAUSE": 0x13,
}

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# KBDLLHOOKSTRUCT
class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_ulonglong),
    ]

# 显式声明 LRESULT (wintypes 不保证提供). 64bit = c_longlong; 32bit = c_long
if ctypes.sizeof(ctypes.c_void_p) == 8:
    LRESULT = ctypes.c_longlong
else:
    LRESULT = ctypes.c_long

LowLevelProc = ctypes.WINFUNCTYPE(LRESULT,         # 返回值 = LRESULT
                                  ctypes.c_int,     # nCode = HC_ACTION
                                  wintypes.WPARAM,  # wParam = 消息(WM_KEYDOWN等)
                                  wintypes.LPARAM)  # lParam = &KBDLLHOOKSTRUCT

HOOKPROC = LowLevelProc
# 为 SetWindowsHookExW argtypes 提供 HINSTANCE/HHOOK:
try:
    HINSTANCE = wintypes.HINSTANCE
except Exception:
    HINSTANCE = ctypes.c_void_p
HHOOK = ctypes.c_void_p


class HotkeyManager:
    """全局热键管理器(Win32 WH_KEYBOARD_LL)."""

    def __init__(self, root=None, log_cb=None):
        self.root = root
        self.log = log_cb or (lambda *a, **k: None)
        # 当前按下的修饰键集合 (CONTROL/MENU/SHIFT)
        self._mods = set()
        # 注册热键: [(mod_set, vk), fn]
        #   mod_set = set of "CONTROL"/"MENU"/"SHIFT" / 空 set 表示裸击
        self._binds = []
        # 要屏蔽的裸击键 (VK整数 集合): 按下/抬起都吞掉, 但仍然允许我们的回调执行
        # (只有当"屏蔽开关"开启时才生效)
        self._blocked_vk = set()
        self._block_enabled = True  # 默认开: 屏蔽 F10/F11/F8/PAUSE (用户在UI里可切换关)
        # v4.13★ 已绑定的Fn键 vk 集合 — 绑定时自动加入, _hook_proc 命中绑定并触发回调后吞键
        # (避免 F5=刷新浏览器 / F6=聚焦地址栏 / F7=caret / F9 / F10=菜单 / F11=全屏 等浏览器默认行为)
        self._bound_vk = set()

        self._hook_handle = None
        self._hook_proc_ref = None  # 防止被 GC
        self._thread = None
        self._running = False
        self._on_state_change_callbacks = []  # fn(block_enabled)
        # ---- v3.44★ 外部按键事件前置回调 (recorder PIXEL 模式等使用) ----
        # 签名: fn(vk:int, is_down:bool, scan:int, vk_name:str)
        self._pre_callbacks = []
        # ---- v3.44★ 回退: RegisterHotKey (不能吞键, 但能保证全局触发) ----
        self._rhk_thread = None
        self._rhk_running = False
        self._rhk_atoms = {}   # id: (combo, callback)
        self._rhk_next_id = 1

    # ---------------- 公共 API ----------------
    def set_block_enabled(self, enabled: bool):
        self._block_enabled = bool(enabled)
        for cb in list(self._on_state_change_callbacks):
            try:
                cb(self._block_enabled)
            except Exception as e:
                self.log(f"[热键] state_callback 异常: {e}")

    def is_block_enabled(self):
        return self._block_enabled

    def add_blocked_vk(self, vk_names):
        for name in vk_names:
            v = VK.get(name)
            if v is not None:
                self._blocked_vk.add(v)

    def add_state_callback(self, fn):
        self._on_state_change_callbacks.append(fn)

    def add_pre_callback(self, fn):
        """v3.44★ PIXEL 宏录制: 在钩子处理任何键之前, 先通知 fn(vk, is_down, scan, vk_name).
        fn 返回值被忽略."""
        try:
            if fn and callable(fn):
                self._pre_callbacks.append(fn)
                return True
        except Exception:
            _SIL.note("core/hotkeys.py:139", "if fn and callable(fn): self._pre_callbacks.ap")
        return False

    def remove_pre_callback(self, fn):
        try:
            while fn in self._pre_callbacks:
                self._pre_callbacks.remove(fn)
            return True
        except Exception:
            return False

    def vk_to_name(self, vk):
        """vk:int -> VK 字典里的键名(没有就返回空串)

        v4.101★ 加缓存: 这个函数在【全局低阶键盘钩子】里被调用 —— 系统里【每一次按键】
        都要跑一次, 而它原来是线性扫整个 VK 字典(约 150 项)。LL 钩子有系统超时, 钩子过程
        太慢 Windows 会【静默把钩子卸载掉】。反查表建一次即可(VK 表是常量)。
        """
        try:
            rev = getattr(self, "_vk_rev", None)
            if rev is None:
                rev = {}
                try:
                    for name, v in VK.items():
                        iv = int(v)
                        if iv not in rev:
                            rev[iv] = name
                except Exception:
                    _SIL.note("core/hotkeys.py:167", "for name, v in VK.items(): iv = int(v) if iv n")
                self._vk_rev = rev
            return rev.get(int(vk), "")
        except Exception:
            return ""

    def bind(self, combo: str, fn):
        """绑定一个组合键.

        combo 语法: 用 `+` 分隔, 最后一段是按键名.
        支持:
          "space", "f5"/"F5"/"f8"..., "pause",
          "ctrl+alt+b", "ctrl+alt+d", "ctrl+alt+x", "ctrl+r",
          "f10", "f11",
        """
        parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
        if not parts:
            return
        key_name = parts[-1]
        mods_set = set()
        norm = {
            "ctrl": "CONTROL", "control": "CONTROL",
            "alt": "MENU", "menu": "MENU",
            "shift": "SHIFT",
        }
        for p in parts[:-1]:
            m = norm.get(p)
            if m:
                mods_set.add(m)
        # key_name -> VK
        vk = None
        if key_name == "space":
            vk = VK["SPACE"]
        elif key_name == "pause":
            vk = VK["PAUSE"]
        else:
            vk = VK.get(key_name.upper())
        if vk is None:
            self.log(f"[热键] 未知键: {combo}")
            return
        # 调度 fn 回到主线程
        def make_safe_callback(f):
            def _cb():
                if self.root is not None:
                    try:
                        self.root.after(0, f)
                    except Exception as e:
                        self.log(f"[热键] after 调度失败: {e}")
            return _cb
        self._binds.append((frozenset(mods_set), int(vk), make_safe_callback(fn)))
        # v4.13★ 记录已绑定的 vk, 让 _hook_proc 命中后吞键 (避免 F5/F6/F7/F9 等触发浏览器默认行为)
        # 仅对功能键 (F1-F12 / PAUSE / SPACE) 自动吞键; 字母数字键不吞 (避免影响浏览器输入)
        key_upper = key_name.upper()
        if key_upper.startswith("F") and len(key_upper) <= 3 and key_upper[1:].isdigit():
            self._bound_vk.add(int(vk))
        elif key_upper in ("PAUSE",):
            self._bound_vk.add(int(vk))

    # ---------------- 启动/停止 ----------------
    def start(self):
        # ---- 平台/权限预诊断 ----
        is_win = (sys.platform.startswith("win") or (os.name == "nt"))
        if not is_win:
            self.log("[热键] 非Windows平台, 跳过Win32钩子; 请用UI按钮或keyboard库回退")
            return  # 非 Windows: RegisterHotKey 也无效, 直接 return(交给 UI 侧 keyboard 兜底)
        if self._running:
            return
        # 默认屏蔽的功能键 (v3.44 方案C)
        if not self._blocked_vk:
            self.add_blocked_vk(["F10", "F11", "F8", "PAUSE"])
        self._thread = threading.Thread(target=self._worker, name="WinKbdLLHook", daemon=True)
        self._running = True
        self._thread.start()
        # 等待钩子线程确认是否成功(最多 400ms)
        for _ in range(20):
            if self._hook_handle is not None or not self._running:
                break
            time.sleep(0.02)
        if self._hook_handle:
            self.log("[热键] Win32 LL 钩子已启动 (F10=录制/F11=停止/F8=回放/Pause=中止 已屏蔽游戏端)")
        else:
            self.log("[热键] Win32 LL 钩子安装失败, 切换 RegisterHotKey 兜底 (注: 兜底无法吞掉 Fn 键, 会被浏览器触发全屏等默认动作)")
            self._start_register_fallback()

    def stop(self):
        if self._running:
            self._running = False
            try:
                if self._hook_handle:
                    user32.UnhookWindowsHookEx(self._hook_handle)
                    self._hook_handle = None
            except Exception as e:
                self.log(f"[热键] 卸载钩子异常: {e}")
            # 发一条 WM_QUIT 唤醒钩子线程里的 PeekMessage
            try:
                if self._thread and self._thread.ident:
                    user32.PostThreadMessageW(int(self._thread.ident), 0x0012, 0, 0)  # WM_QUIT
            except Exception:
                _SIL.note("core/hotkeys.py:265", "if self._thread and self._thread.ident: user32")
            try:
                self._thread.join(timeout=1.5)
            except Exception:
                _SIL.note("core/hotkeys.py:269", "self._thread.join(timeout=1.5)")
        # RegisterHotKey 兜底
        if self._rhk_running:
            self._stop_register_fallback()
        self.log("[热键] 钩子已停止 (所有 Fn/屏蔽键已恢复)")

    # ---------------- RegisterHotKey 兜底(安装失败时) ----------------
    def _combo_to_rhk_mods_vk(self, combo: str):
        parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
        if not parts: return None
        MOD_ALT = 0x0001; MOD_CONTROL = 0x0002; MOD_SHIFT = 0x0004; MOD_NOREPEAT = 0x4000
        key = parts[-1]
        mods = MOD_NOREPEAT
        norm = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL,
                "alt": MOD_ALT, "menu": MOD_ALT,
                "shift": MOD_SHIFT}
        for p in parts[:-1]:
            m = norm.get(p)
            if m is not None: mods |= m
        if key == "space": vk = VK["SPACE"]
        elif key == "pause": vk = VK["PAUSE"]
        else: vk = VK.get(key.upper())
        if vk is None: return None
        return mods, int(vk)

    def _start_register_fallback(self):
        """RegisterHotKey 兜底: 全局触发我们注册的 binds, 但**无法吞掉**游戏端收到的 Fn 键."""
        if self._rhk_running: return
        n_ok = 0
        try:
            import atexit
            # 逐条 bind 注册
            MODS_REV = {"CONTROL": "ctrl", "MENU": "alt", "SHIFT": "shift"}
            for (mods_froz, target_vk, cb) in list(self._binds):
                vk_name = None
                for name, v in VK.items():
                    if int(v) == int(target_vk): vk_name = name; break
                if vk_name is None: continue
                mod_names = []
                for m in mods_froz:
                    if m in MODS_REV: mod_names.append(MODS_REV[m])
                mod_names.sort()
                combo = "+".join(mod_names + [vk_name.lower()])
                parsed = self._combo_to_rhk_mods_vk(combo)
                if not parsed: continue
                mods, vk = parsed
                rid = self._rhk_next_id; self._rhk_next_id += 1
                user32.RegisterHotKey.restype = wintypes.BOOL
                ok = bool(user32.RegisterHotKey(None, rid, mods, vk))
                if ok:
                    self._rhk_atoms[rid] = (combo, cb)
                    n_ok += 1
                    self.log(f"[热键] RegisterHotKey 兜底: {combo} 已注册 (无法吞键, F11会被浏览器全屏)")
        except Exception as e:
            self.log(f"[热键] RegisterHotKey 兜底注册异常: {e}")
        if not self._rhk_atoms:
            self.log("[热键] RegisterHotKey 兜底也未成功 —— 请改用界面按钮或安装 keyboard 库")
            self._rhk_running = False
            return
        self._rhk_running = True
        def pump():
            WM_HOTKEY = 0x0312
            msg = wintypes.MSG()
            pmsg = ctypes.byref(msg)
            while self._rhk_running:
                b = 0
                try:
                    b = user32.PeekMessageW(pmsg, None, WM_HOTKEY, WM_HOTKEY, 1)  # PM_REMOVE=1
                except Exception:
                    b = 0
                if b and msg.message == WM_HOTKEY:
                    rid = int(msg.wParam)
                    rec = self._rhk_atoms.get(rid)
                    if rec:
                        try: rec[1]()
                        except Exception as e: self.log(f"[热键] RHK回调异常: {e}")
                else:
                    time.sleep(0.03)
            for rid in list(self._rhk_atoms.keys()):
                try: user32.UnregisterHotKey(None, rid)
                except Exception: _SIL.note("core/hotkeys.py:349", "user32.UnregisterHotKey(None, rid)")
            self._rhk_atoms.clear()
        self._rhk_thread = threading.Thread(target=pump, name="RHK_Pump", daemon=True)
        self._rhk_thread.start()
        try:
            atexit.register(lambda: self._stop_register_fallback())
        except Exception:
            _SIL.note("core/hotkeys.py:356", "atexit.register(lambda: self._stop_register_fa")
        self.log(f"[热键] RegisterHotKey 兜底启动完成: {n_ok} 个组合键已注册 (提示: 用管理员启动助手可回到 WH_KEYBOARD_LL 模式, 能真正屏蔽 F10/F11)")

    def _stop_register_fallback(self):
        self._rhk_running = False
        for rid in list(self._rhk_atoms.keys()):
            try: user32.UnregisterHotKey(None, rid)
            except Exception: _SIL.note("core/hotkeys.py:363", "user32.UnregisterHotKey(None, rid)")
        self._rhk_atoms.clear()

    # ---------------- 辅助 ----------------
    def _is_admin(self) -> bool:
        try:
            shell32 = ctypes.WinDLL("shell32", use_last_error=False)
            if hasattr(shell32, "IsUserAnAdmin"):
                shell32.IsUserAnAdmin.restype = wintypes.BOOL
                return bool(shell32.IsUserAnAdmin())
        except Exception:
            return False
        return False

    # ---------------- 钩子线程内部 ----------------
    def _worker(self):
        try:
            self._hook_proc_ref = HOOKPROC(self._hook_proc)
            # v3.44★ 关键修复: WH_KEYBOARD_LL 忽略 hMod → 传 NULL(0)
            # 之前的 GetModuleHandleW(None) 在 PyInstaller/嵌入式 会返回 0 或非法句柄, 导致 126(ERROR_MOD_NOT_FOUND)
            kernel32.SetLastError(0)
            user32.SetWindowsHookExW.restype = HHOOK
            user32.SetWindowsHookExW.argtypes = [
                ctypes.c_int,     # idHook
                HOOKPROC,         # lpfn
                HINSTANCE,        # hMod — WH_KEYBOARD_LL → 传 NULL(0) 即可
                wintypes.DWORD,   # dwThreadId = 0 → 全局
            ]
            self._hook_handle = user32.SetWindowsHookExW(
                WH_KEYBOARD_LL,
                self._hook_proc_ref,
                None,    # ←★ hMod=NULL (WH_KEYBOARD_LL 忽略)
                0        # dwThreadId=0 → 全局
            )
            if not self._hook_handle:
                err = int(ctypes.get_last_error() or kernel32.GetLastError())
                if err == 0: err = 126
                hint = ""
                if err == 126:
                    hint = " (ERROR_MOD_NOT_FOUND: 通常是嵌入python/pyinstaller环境导致; 已自动切 RegisterHotKey 兜底)"
                elif err == 5:
                    hint = " (ERROR_ACCESS_DENIED: 反作弊/安全软件阻止, 建议『以管理员身份运行』后重试)"
                elif err == 87:
                    hint = " (ERROR_INVALID_PARAMETER: 参数/回调签名异常)"
                elif err == 1428:
                    hint = " (ERROR_HOOK_NEEDS_HMOD: 低级钩子不需要hMod; 系统版本异常?)"
                self.log(f"[热键] SetWindowsHookEx 失败: {err}{hint}")
                self._running = False
                return
        except Exception as e:
            self.log(f"[热键] 钩子初始化失败: {e}")
            self._running = False
            return

        # Windows 强制: WH_KEYBOARD_LL 钩子线程必须跑消息循环
        msg = wintypes.MSG()
        lpmsg = ctypes.byref(msg)
        QS_ALLINPUT = 0x04FF
        while self._running:
            bRet = 0
            try:
                bRet = user32.PeekMessageW(lpmsg, None, 0, 0, 1)  # PM_REMOVE=1
            except Exception:
                bRet = 0
            if bRet:
                if msg.message == 0x0012:  # WM_QUIT
                    break
                try:
                    user32.TranslateMessage(lpmsg)
                    user32.DispatchMessageW(lpmsg)
                except Exception:
                    _SIL.note("core/hotkeys.py:434", "user32.TranslateMessage(lpmsg)")
            else:
                try:
                    user32.MsgWaitForMultipleObjects(0, None, False, 50, QS_ALLINPUT)
                except Exception:
                    time.sleep(0.03)
        try:
            if self._hook_handle:
                user32.UnhookWindowsHookEx(self._hook_handle)
                self._hook_handle = None
        except Exception:
            _SIL.note("core/hotkeys.py:445", "if self._hook_handle: user32.UnhookWindowsHook")

    def _hook_proc(self, nCode, wParam, lParam):
        # v3.44★ 新签名: lParam = wintypes.LPARAM (整数) -> cast 到 KBDLLHOOKSTRUCT *
        if nCode < HC_ACTION:
            try:
                return user32.CallNextHookEx(None, nCode, wParam, lParam)
            except Exception:
                return 0
        kb = None
        try:
            kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            vk = int(kb.vkCode)
            flags = int(kb.flags)
            is_up = bool(int(wParam) in (WM_KEYUP, WM_SYSKEYUP) or (flags & LLKHF_UP))
        except Exception:
            try:
                return user32.CallNextHookEx(None, nCode, wParam, lParam)
            except Exception:
                return 0
        try:
            # 0) v3.44★ PIXEL 模式前置回调 (录制器/调试): 无论我们的绑定/屏蔽是否命中, 先原样分发
            if self._pre_callbacks:
                try:
                    scan = int(getattr(kb, "scanCode", 0) or 0)
                    vk_name = self.vk_to_name(vk)
                    for pfn in list(self._pre_callbacks):
                        try: pfn(vk, not is_up, scan, vk_name)
                        except Exception: _SIL.note("core/hotkeys.py:473", "pfn(vk, not is_up, scan, vk_name)")
                except Exception: _SIL.note("core/hotkeys.py:474", "scan = int(getattr(kb, 'scanCode', 0) or 0)")
            # 1) 修饰键维护
            if vk == VK["CONTROL"]:
                if is_up: self._mods.discard("CONTROL")
                else: self._mods.add("CONTROL")
            elif vk == VK["MENU"]:
                if is_up: self._mods.discard("MENU")
                else: self._mods.add("MENU")
            elif vk == VK["SHIFT"]:
                if is_up: self._mods.discard("SHIFT")
                else: self._mods.add("SHIFT")
            # 2) 按下时检查绑定
            swallowed = False
            if not is_up:
                mods_now = frozenset(self._mods)
                for (mods, target_vk, cb) in self._binds:
                    if mods == mods_now and target_vk == vk:
                        try:
                            cb()
                        except Exception as e:
                            self.log(f"[热键] 回调异常: {e}")
                        # v4.13★ 命中绑定的Fn键自动吞键 (避免浏览器默认行为)
                        if vk in self._bound_vk:
                            swallowed = True
                        elif self._should_block(vk, self._mods):
                            swallowed = True
            # 3) 屏蔽: 裸击键(无修饰) 屏蔽集合
            if not swallowed and self._should_block(vk, self._mods):
                swallowed = True
            if swallowed:
                return 1  # 非 0 → 吞键, 下游任何程序都收不到
        except Exception as e:
            self.log(f"[热键] 钩子内部异常: {e}")
        try:
            return user32.CallNextHookEx(None, nCode, wParam, lParam)
        except Exception:
            return 0

    def _should_block(self, vk: int, mods_now):
        if not self._block_enabled:
            return False
        # 仅屏蔽"裸击"(非组合键): 有修饰符按下的(如 Ctrl+F10)不吞(留给其他系统/浏览器行为用)
        if mods_now:
            return False
        return vk in self._blocked_vk


# 全局单例 (App 启动时 HM.start(); 关闭时 HM.stop())
HM = HotkeyManager()
