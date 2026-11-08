# -*- coding: utf-8 -*-
"""OCR + 鼠标拖拽操作模块（混合策略版）
策略:
  1. 协议数据(/live) → 知道有什么卡(名字/ID/顺序, 100%可靠)
  2. 颜色分析 → 找到卡牌在屏幕上的物理位置(卡牌有彩色边框/背景)
  3. OCR → 【只】认数字(金币/价格/人口/血量)和按钮文字 —— 见 ocr_number() 的说明:
     实测商店区 chi_sim 出来 64 条全是碎片(CR/有/所/1), 一个完整将名都没有,
     将名是描边艺术字+竖排+压复杂背景, Tesseract 中文模型在这类小字上基本瞎。
  4. 模板/SSIM(v4.105★) → 认"这是不是那张卡 / 那个按钮 / 现在哪个阶段", 不认字
  5. 鼠标拖拽 → 买将(拖到备战区) / 遣散(拖到牌库上半)

买将: 长按武将卡 → 滑动到备战区
遣散: 长按武将卡 → 滑动到牌库上半部分
"""
import os, sys
# 路径自举: 直接运行本文件也能 import core.xxx
if __name__ == "__main__" and __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import time, re, threading, json, urllib.request
import ctypes, ctypes.wintypes
from core import silence as _SIL

# v4.108★ 可选依赖【软导入】: OCR / 截图 / 模拟输入这几包只有本模块真正用到时才需要,
#   主程序其余部分(协议流 / 剧本 / UI)一个都不依赖。
#   以前写在模块顶部硬 import —— 缺任何一个包, 整个助手直接起不来(ImportError)。
#   现在缺包只是「OCR 与鼠标拖拽不可用」, 助手照样能开、能跑剧本、能上阵遣散。
try:
    import mss
except Exception:
    mss = None
try:
    import pytesseract
except Exception:
    pytesseract = None
try:
    import numpy as np
except Exception:
    np = None
try:
    import pyautogui
except Exception:
    pyautogui = None
try:
    from PIL import Image
except Exception:
    Image = None


def _need_dep(name):
    """v4.108★ 用到时才检查可选依赖 —— 缺包给一句人话, 而不是满屏 ImportError。"""
    raise RuntimeError(
        "缺少可选依赖 '%s'(仅 OCR/截图/鼠标拖拽用)。装它: pip install %s ; "
        "不装也能正常用助手, 只是这几个功能不可用。" % (name, name))

# v3.99★ Tesseract 路径智能发现 (避免硬编码导致崩溃)
def _find_tesseract():
    paths = [
        r'C:\Program Files\Tesseract-OCR\tesseract.exe',
        r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
        os.environ.get('TESSERACT_PATH', ''),
        'tesseract.exe' # 尝试在系统 PATH 中找
    ]
    for p in paths:
        if p and os.path.exists(p):
            return p
    return 'tesseract.exe' # 兜底，由系统 PATH 决定

if pytesseract is not None:
    pytesseract.pytesseract.tesseract_cmd = _find_tesseract()
if pyautogui is not None:
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.05
user32 = ctypes.windll.user32

# 桥接地址
# v4.24★ 修: 原来是硬编码 8765, 而端口是可配置的(config.json → bridge.port, 游戏页 install 页可改)。
#   用户改过端口后, OCR/截图这条链路会一直去连旧端口 → 表现为"取不到数据"且没有任何报错。
#   现在每次调用都读一遍配置(改端口后无需重启助手即可生效)。
BRIDGE_PORT_FALLBACK = 8765


def bridge_base():
    """当前桥接基地址 http://127.0.0.1:<port>(端口以 config.json 的 bridge.port 为准)"""
    port = BRIDGE_PORT_FALLBACK
    try:
        try:
            from .config import CONFIG as _CFG          # 正常: 作为 core 包的一员被导入
        except ImportError:
            from core.config import CONFIG as _CFG      # 直接运行本文件(__main__)时
        port = int((_CFG.get("bridge") or {}).get("port") or BRIDGE_PORT_FALLBACK)
    except Exception:
        port = BRIDGE_PORT_FALLBACK
    return f"http://127.0.0.1:{port}"


# 兼容旧引用: 模块导入那一刻的地址。新代码请直接调 bridge_base()(端口改了才跟得上)
BRIDGE = bridge_base()


def _enum_windows():
    """枚举所有可见窗口"""
    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    wins = []
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            if buf.value:
                rect = ctypes.wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                wins.append((hwnd, buf.value, (rect.left, rect.top, rect.right, rect.bottom)))
        return True
    user32.EnumWindows(proc(cb), 0)
    return wins


class OcrOps:
    """OCR识别 + 鼜标拖拽操作（混合策略）"""

    # UI区域定义(相对游戏窗口的百分比,自动适配任何窗口大小)
    # 商店卡位区: 上半屏中部
    SHOP_PCT = (0.15, 0.25, 0.85, 0.42)
    # 备战区: 中下
    BENCH_PCT = (0.15, 0.52, 0.85, 0.68)
    # 牌库上半(遣散目标): 商店区上方
    SELL_PCT = (0.50, 0.15)

    def __init__(self, log=print):
        self.log = log
        # v4.108★ 懒创建: 以前这里直接 mss.MSS(), 而 OcrOps 是【启动时】就被实例化的
        #   (ui/app.py: self.ocr_scanner = OcrOps(self.log)) —— 缺 mss 包 = 助手直接起不来,
        #   软导入做得再好也被这一行绕过。改成真正要截图时才建, 缺包只影响 OCR。
        self._sct = None
        self._lock = threading.Lock()
        # 鼠标拖拽参数
        self.drag_hold = 0.3     # 长按时间
        self.drag_dur = 0.4     # 拖拽时长
        # 缓存游戏窗口区域
        self._game_rect = None
        self._game_rect_ts = 0

    def _ensure_sct(self):
        """v4.108★ 真正要截图时才创建 mss 实例 —— 缺包在此处报错, 而不是启动即崩。"""
        if self._sct is None:
            if mss is None:
                _need_dep('mss')
            self._sct = mss.MSS()
        return self._sct

    # ---------- 游戏窗口检测 ----------
    def find_game_window(self, force=False):
        """找游戏窗口, 返回 (left,top,right,bottom) 或 None
        匹配策略(按优先级):
          1. 浏览器类窗口(Edge/Chrome) + 标题含三国杀
          2. 独立客户端(标题精确="三国杀" 或 含"三国杀"且尺寸>800x600)
        """
        if not force and self._game_rect and (time.time() - self._game_rect_ts < 5):
            return self._game_rect
        candidates = []
        for hwnd, title, rect in _enum_windows():
            w, h = rect[2] - rect[0], rect[3] - rect[1]
            if w < 600 or h < 400:
                continue   # 忽略小窗口(任务栏预览/弹窗等)
            score = 0
            # 浏览器+三国杀
            if any(b in title for b in ("Edge", "Chrome", "浏览器")) and "三国杀" in title:
                score += 100
            # 独立客户端: 标题含三国杀
            if title.strip() == "三国杀":
                score += 90
            elif "三国杀" in title and w > 800 and h > 600:
                score += 80
            # 标题含自走棋
            if "自走棋" in title:
                score += 50
            if score > 0:
                candidates.append((score, rect, title))
        if candidates:
            candidates.sort(reverse=True)
            sc, rect, title = candidates[0]
            self._game_rect = rect
            self._game_rect_ts = time.time()
            self.log(f"  [窗口] 匹配: '{title[:30]}' score={sc} rect={rect}")
            return rect
        return None

    def activate_game(self):
        """激活游戏窗口(前置)"""
        for hwnd, title, rect in _enum_windows():
            w, h = rect[2] - rect[0], rect[3] - rect[1]
            if w < 600 or h < 400:
                continue
            match = (("Edge" in title or "Chrome" in title) and "三国杀" in title) \
                or (title.strip() == "三国杀") \
                or ("三国杀" in title and w > 800)
            if match:
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                time.sleep(0.2)
                user32.SetForegroundWindow(hwnd)
                time.sleep(0.8)
                self._game_rect = rect
                self._game_rect_ts = time.time()
                self.log(f"  [激活] 窗口: '{title[:30]}' rect={rect}")
                return rect
        self.log("  [激活] 未找到可前置的游戏窗口")
        return None

    def game_area(self):
        """返回游戏区域 (left,top,right,bottom) 物理坐标"""
        rect = self.find_game_window()
        if not rect:
            # 全屏兜底
            sct = self._ensure_sct()
            return (0, 0, sct.monitors[0]["width"], sct.monitors[0]["height"])
        return rect

    def _pct_to_px(self, pct):
        """百分比→物理像素坐标"""
        l, t, r, b = self.game_area()
        gl, gt = l, t
        gw, gh = r - l, b - t
        if len(pct) == 4:
            return (int(gl + gw * pct[0]), int(gt + gh * pct[1]),
                    int(gl + gw * pct[2]), int(gt + gh * pct[3]))
        elif len(pct) == 2:
            return (int(gl + gw * pct[0]), int(gt + gh * pct[1]))
        return pct

    def shop_region(self):
        """商店区物理坐标"""
        return self._pct_to_px(self.SHOP_PCT)

    def bench_region(self):
        """备战区物理坐标"""
        return self._pct_to_px(self.BENCH_PCT)

    def sell_target(self):
        """遣散目标点物理坐标"""
        return self._pct_to_px(self.SELL_PCT)

    # ---------- 截图+OCR ----------
    def grab(self, region=None):
        """截图 region=(left,top,right,bottom) 或 None=全屏"""
        if Image is None:
            _need_dep('pillow')
        sct = self._ensure_sct()
        if region:
            l, t, r, b = region
            m = sct.grab({"left": l, "top": t, "width": max(1, r - l), "height": max(1, b - t)})
            return Image.frombytes("RGB", m.size, m.bgra, "raw", "BGRX")
        else:
            m = sct.grab(sct.monitors[0])
            return Image.frombytes("RGB", m.size, m.bgra, "raw", "BGRX")

    def ocr(self, region=None, psm=6, scale=2, preprocess=False):
        """OCR识别 → 返回 [(text, cx, cy, w, h), ...] 中心坐标(物理)"""
        if pytesseract is None:
            _need_dep('pytesseract')
        if np is None:
            _need_dep('numpy')
        img = self.grab(region)
        offset_x = region[0] if region else 0
        offset_y = region[1] if region else 0
        # 预处理: 灰度+放大+阈值
        if preprocess:
            img = img.convert('L')
            arr = np.array(img)
            mean = arr.mean()
            arr = np.where(arr > mean * 0.85, 255, 0).astype(np.uint8)
            img = Image.fromarray(arr)
        if scale != 1:
            img = img.resize((img.width * scale, img.height * scale), Image.LANCZOS)
        data = pytesseract.image_to_data(
            img, lang="chi_sim+eng", config=f"--psm {psm}",
            output_type=pytesseract.Output.DICT)
        res = []
        for i, txt in enumerate(data["text"]):
            t = (txt or "").strip()
            if not t or len(t) < 1:
                continue
            if len(t) == 1 and not t.isdigit() and not re.match(r'[\u4e00-\u9fff]', t):
                continue
            x = data["left"][i] // scale + offset_x
            y = data["top"][i] // scale + offset_y
            w = data["width"][i] // scale
            h = data["height"][i] // scale
            cx, cy = x + w // 2, y + h // 2
            res.append((t, cx, cy, w, h))
        return res

    # ---------- v4.105★ OCR 该干什么、不该干什么 ----------
    #   上游那个键鼠脚本(caoyang-sufe/tarvenchess_master, tools.py)的做法值得抄:
    #   它 import 了 pytesseract, 但【只用它认数字】—— recognize_digit_tesseract() 里
    #   `--psm 8 -c tessedit_char_whitelist=0123456789`; 卡牌和阶段一律走
    #   calculate_image_similarity()(SSIM 结构相似度)跟模板图比对。
    #   我们 dev/_ocr_debug 那份实测报告是同一个结论(商店区 64 条全是碎片, 零个完整将名),
    #   所以这里补两个对口的工具: 数字专用 OCR + SSIM 模板比对。
    def ocr_number(self, region=None, scale=3, whitelist="0123456789"):
        """只认数字的 OCR —— 金币 / 价格 / 人口 / 血量用这个, 别用通用 ocr()。

        通用 ocr() 走 chi_sim+eng 全字库, 在数字上反而会把"3"认成"B"、把"58"认成"S8";
        这里放大 + 二值 + 字符白名单, 只留 0-9。认不出来返回 None(不猜)。
        """
        try:
            img = self.grab(region).convert("L")
            if scale != 1:
                img = img.resize((img.width * scale, img.height * scale), Image.LANCZOS)
            arr = np.array(img)
            arr = np.where(arr > arr.mean(), 255, 0).astype(np.uint8)   # 二值: 比自适应阈值稳
            cfg = "--oem 3 --psm 7 -c tessedit_char_whitelist=" + whitelist
            txt = (pytesseract.image_to_string(Image.fromarray(arr), lang="eng", config=cfg) or "").strip()
            txt = re.sub(r"[^0-9]", "", txt)
            return int(txt) if txt else None
        except Exception as e:
            self.log(f"  [OCR数字] 失败: {e}")
            return None

    def ssim(self, img_a, img_b):
        """两张图的结构相似度 0~1(1 = 一模一样)。

        用来代替"认字": 拿截图跟模板图(一张卡、一个按钮、某个阶段的标志性区域)比对。
        skimage 没装时退回灰度归一化互相关, 精度差一些但不需要额外装东西。
        """
        try:
            from skimage.metrics import structural_similarity as _ssim
            a = np.asarray(img_a.convert("L"), dtype=np.float64)
            b = np.asarray(img_b.convert("L"), dtype=np.float64)
            if a.shape != b.shape:
                b = np.asarray(Image.fromarray(b.astype(np.uint8)).resize((a.shape[1], a.shape[0])),
                               dtype=np.float64)
            return float(_ssim(a, b, data_range=255))
        except Exception:
            try:
                a = np.asarray(img_a.convert("L").resize((64, 64)), dtype=np.float64)
                b = np.asarray(img_b.convert("L").resize((64, 64)), dtype=np.float64)
                a = a - a.mean()
                b = b - b.mean()
                den = float(np.sqrt((a * a).sum()) * np.sqrt((b * b).sum()) or 1.0)
                return float((a * b).sum() / den)
            except Exception as e:
                self.log(f"  [SSIM] 失败: {e}")
                return 0.0

    def match_template(self, region, template_path, threshold=0.85):
        """这块区域里有没有这张模板图 → (相似度, 中心坐标) 或 None。

        模板可以从自己屏幕上截(dev/_ocr_debug/ 里那些 png 就能当模板用)。
        """
        try:
            tpl = Image.open(template_path).convert("RGB")
        except Exception as e:
            self.log(f"  [模板] 读不了 {template_path}: {e}")
            return None
        img = self.grab(region)
        s = self.ssim(img, tpl)
        if s < threshold:
            return None
        l, t = (region[0], region[1]) if region else (0, 0)
        return (s, (l + img.width // 2, t + img.height // 2))

    def find_text(self, keyword, region=None, fuzzy=True, scale=2):
        """找文字 → 返回 (cx, cy) 或 None"""
        items = self.ocr(region, psm=6, scale=scale)
        for t, cx, cy, w, h in items:
            if keyword in t or (fuzzy and t in keyword):
                return (cx, cy)
        if fuzzy and len(keyword) >= 2:
            for t, cx, cy, w, h in items:
                if len(t) >= 2 and any(c in t for c in keyword[:2]):
                    return (cx, cy)
        return None

    def find_all(self, keyword, region=None):
        """找所有匹配 → [(cx, cy, text), ...]"""
        items = self.ocr(region)
        return [(cx, cy, t) for t, cx, cy, w, h in items if keyword in t]

    # ---------- 颜色分析找卡牌 ----------
    def find_card_row(self):
        """找商店卡牌位置(5张卡等间距排列)
        策略: 先用颜色分析找卡牌段, 失败则用布局计算(5等分)
        返回 [(card_cx, card_cy), ...] 或 None
        """
        region = self.shop_region()
        l, t, r, b = region
        W = r - l
        H = b - t

        # 策略1: 颜色分析(找卡牌边界)
        try:
            img = self.grab(region)
            arr = np.array(img)
            r2, g2, b2 = arr[:,:,0], arr[:,:,1], arr[:,:,2]
            brightness = r2.astype(int) + g2.astype(int) + b2.astype(int)
            # 卡牌通常比背景亮(有彩色肖像/边框)
            card_mask = (brightness > 200) & (brightness < 700)
            col_sum = card_mask.sum(axis=0)
            threshold = H * 0.15

            segments = []
            in_seg = False
            seg_start = 0
            for x in range(W):
                if col_sum[x] > threshold:
                    if not in_seg:
                        seg_start = x
                        in_seg = True
                else:
                    if in_seg:
                        seg_end = x - 1
                        if seg_end - seg_start > W * 0.08:
                            segments.append((seg_start, seg_end))
                        in_seg = False
            if in_seg:
                if W - 1 - seg_start > W * 0.08:
                    segments.append((seg_start, W - 1))

            if len(segments) >= 3:
                cards = [((s + e) // 2 + l, (t + b) // 2) for s, e in segments[:5]]
                self.log(f"  [卡牌行] 颜色分析找到{len(cards)}张卡: {cards}")
                return cards
        except Exception as e:
            self.log(f"  [卡牌行] 颜色分析异常: {e}")

        # 策略2: 布局计算(5等分,最可靠)
        self.log("  [卡牌行] 颜色分析失败,用布局计算(5等分)")
        card_w = W / 5
        cy = (t + b) // 2
        cards = [(int(l + card_w * (i + 0.5)), cy) for i in range(5)]
        self.log(f"  [卡牌行] 计算位置: {cards}")
        return cards

    def find_bench_cards(self):
        """找备战区卡牌位置(颜色分析+布局兜底)"""
        region = self.bench_region()
        l, t, r, b = region
        W = r - l
        H = b - t

        # 颜色分析
        try:
            img = self.grab(region)
            arr = np.array(img)
            r2, g2, b2 = arr[:,:,0], arr[:,:,1], arr[:,:,2]
            brightness = r2.astype(int) + g2.astype(int) + b2.astype(int)
            card_mask = (brightness > 200) & (brightness < 700)
            col_sum = card_mask.sum(axis=0)
            threshold = H * 0.15

            segments = []
            in_seg = False
            seg_start = 0
            for x in range(W):
                if col_sum[x] > threshold:
                    if not in_seg:
                        seg_start = x
                        in_seg = True
                else:
                    if in_seg:
                        seg_end = x - 1
                        if seg_end - seg_start > W * 0.04:
                            segments.append((seg_start, seg_end))
                        in_seg = False
            if in_seg:
                if W - 1 - seg_start > W * 0.04:
                    segments.append((seg_start, W - 1))

            if segments:
                cards = [((s + e) // 2 + l, (t + b) // 2) for s, e in segments]
                return cards
        except Exception:
            _SIL.note("core/ocr_ops.py:472", "img = self.grab(region)")

        # 布局兜底: 备战区最多9张(3x3), 返回5等分位置
        card_w = W / 5
        cy = (t + b) // 2
        cards = [(int(l + card_w * (i + 0.5)), cy) for i in range(5)]
        return cards

    # ---------- 协议数据(/live) ----------
    def get_live(self):
        """从桥接获取LIVE协议数据(商店/备战区等)"""
        try:
            req = urllib.request.Request(f"{bridge_base()}/live", data=b"", method="POST")
            with urllib.request.urlopen(req, timeout=2) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            self.log(f"  [/live] 错误: {e}")
            return None

    # ---------- 鼠标操作 ----------
    def click(self, x, y, delay=0.05):
        """点击坐标"""
        if pyautogui is None:
            _need_dep('pyautogui')
        pyautogui.click(x, y)
        time.sleep(delay)

    def drag(self, x1, y1, x2, y2, hold=None, dur=None):
        """拖拽: 移动到起点→长按→移动到终点→释放"""
        if pyautogui is None:
            _need_dep('pyautogui')
        hold = hold if hold is not None else self.drag_hold
        dur = dur if dur is not None else self.drag_dur
        self.log(f"  拖拽 ({x1},{y1})→({x2},{y2}) hold={hold}s dur={dur}s")
        pyautogui.moveTo(x1, y1, duration=0.15)
        pyautogui.mouseDown(x1, y1, button="left")
        time.sleep(hold)
        steps = 10
        for i in range(1, steps + 1):
            ix = int(x1 + (x2 - x1) * i / steps)
            iy = int(y1 + (y2 - y1) * i / steps)
            pyautogui.moveTo(ix, iy, duration=dur / steps)
        time.sleep(0.1)
        pyautogui.mouseUp(x2, y2, button="left")
        time.sleep(0.2)

    # ---------- 业务操作(混合策略) ----------
    def buy_card_by_index(self, index):
        """按位置买将(第index张卡,0开始) → 拖拽到备战区"""
        self.log(f"[买将] 第{index+1}张卡...")
        cards = self.find_card_row()
        if not cards or index >= len(cards):
            self.log(f"  未找到第{index+1}张卡(共{len(cards) if cards else 0}张)")
            return False
        cx, cy = cards[index]
        # 备战区中心
        bench = self.bench_region()
        tx = (bench[0] + bench[2]) // 2
        ty = (bench[1] + bench[3]) // 2
        self.log(f"  卡{index+1} @ ({cx},{cy}) → 备战区 ({tx},{ty})")
        self.drag(cx, cy, tx, ty)
        self.log("  买将完成")
        return True

    def buy_card_by_name(self, name):
        """买将(按名字): 用/live数据找卡的位置 → 颜色分析找物理位置 → 拖拽"""
        self.log(f"[买将] 查找 '{name}' ...")
        live = self.get_live()
        if not live or not live.get("shop"):
            self.log("  /live无商店数据——不在对局或未刷新商店")
            # 兜底: 用OCR找
            pos = self.find_text(name, region=self.shop_region())
            if not pos:
                self.log(f"  OCR也未找到 '{name}'")
                return False
            cx, cy = pos
            bench = self.bench_region()
            tx = (bench[0] + bench[2]) // 2
            ty = (bench[1] + bench[3]) // 2
            self.drag(cx, cy + 40, tx, ty)
            return True

        # 从/live商店数据找匹配的卡
        shop = live["shop"]
        from core import id_map as IDMAP
        for i, g in enumerate(shop):
            if g.get("_sold"):
                continue
            cid = str(g.get("chessID") or "")
            gname = IDMAP.name_of(cid, chess_hint=True) if cid else ""
            if name in gname or gname in name or name in str(gname):
                self.log(f"  /live命中: 第{i+1}张 chessID={cid} 名='{gname}'")
                return self.buy_card_by_index(i)
        self.log(f"  商店中无 '{name}' (共{len(shop)}张)")
        return False

    def buy_first_available(self):
        """买第一张可买的卡"""
        live = self.get_live()
        if live and live.get("shop"):
            for i, g in enumerate(live["shop"]):
                if not g.get("_sold") and g.get("goodsID"):
                    return self.buy_card_by_index(i)
        # 兜底: 买第1张
        return self.buy_card_by_index(0)

    def sell_card_by_index(self, index):
        """按位置遣散(备战区第index张卡) → 拖拽到牌库上半"""
        self.log(f"[遣散] 第{index+1}张卡...")
        cards = self.find_bench_cards()
        if not cards or index >= len(cards):
            self.log(f"  未找到第{index+1}张卡(共{len(cards) if cards else 0}张)")
            return False
        cx, cy = cards[index]
        tx, ty = self.sell_target()
        self.log(f"  卡{index+1} @ ({cx},{cy}) → 牌库上半 ({tx},{ty})")
        self.drag(cx, cy, tx, ty)
        self.log("  遣散完成")
        return True

    def sell_card_by_name(self, name):
        """遣散(按名字)"""
        self.log(f"[遣散] 查找 '{name}' ...")
        pos = self.find_text(name, region=self.bench_region())
        if not pos:
            self.log(f"  未找到 '{name}'")
            return False
        cx, cy = pos
        tx, ty = self.sell_target()
        self.log(f"  找到 '{name}' @ ({cx},{cy}) → 牌库上半 ({tx},{ty})")
        self.drag(cx, cy + 40, tx, ty)
        self.log("  遣散完成")
        return True

    def click_button(self, name, region=None):
        """点击按钮: 找按钮文字 → 点击"""
        self.log(f"[点击] 查找 '{name}' ...")
        pos = self.find_text(name, region=region, scale=1)  # 按钮用原分辨率OCR
        if not pos:
            self.log(f"  未找到 '{name}'")
            return False
        cx, cy = pos
        self.log(f"  点击 '{name}' @ ({cx},{cy})")
        self.click(cx, cy)
        return True

    # ---------- 扫描(供UI调用) ----------
    def scan_shop(self):
        """OCR扫描商店区 → [(text, x, y), ...]"""
        return [(t, cx, cy) for t, cx, cy, w, h in self.ocr(self.shop_region())]

    def scan_bench(self):
        """OCR扫描备战区"""
        return [(t, cx, cy) for t, cx, cy, w, h in self.ocr(self.bench_region())]

    def scan_full(self):
        """全屏OCR扫描"""
        return [(t, cx, cy) for t, cx, cy, w, h in self.ocr(psm=11, scale=1)]

    def scan(self):
        """v4.17★ UI「OCR扫描」入口(app.ocr_scan 每秒调用一次).
        之前 app 调 self.ocr_scanner.scan() 但本类只提供 scan_shop/scan_bench/scan_full,
        报 "'OcrOps' object has no attribute 'scan'"。这里做商店+备战区合并扫描。
        返回: {"shop": [(text,x,y)...], "bench": [(text,x,y)...]} (任一区域失败则该键为空列表)"""
        out = {"shop": [], "bench": []}
        try:
            out["shop"] = self.scan_shop()
        except Exception as e:
            self.log(f"[OCR] 商店区扫描失败: {e}")
        try:
            out["bench"] = self.scan_bench()
        except Exception as e:
            self.log(f"[OCR] 备战区扫描失败: {e}")
        return out

    def shop_status(self):
        """商店状态摘要(供UI显示)"""
        live = self.get_live()
        if not live:
            return "未连接桥接"
        if not live.get("in_game"):
            return "未在对局"
        shop = live.get("shop", [])
        if not shop:
            return "商店无数据(刷新一次?)"
        from core import id_map as IDMAP
        parts = []
        for i, g in enumerate(shop):
            if g.get("_sold"):
                parts.append(f"[{i+1}]已卖")
            else:
                cid = str(g.get("chessID") or "")
                nm = IDMAP.name_of(cid, chess_hint=True) or "?"
                atk = g.get("attack", "?")
                hp = g.get("hp", "?")
                parts.append(f"[{i+1}]{nm}({atk}/{hp})")
        me = live.get("me", {})
        return f"金{me.get('coin','?')} T{live.get('turn','?')} " + " ".join(parts)
