# -*- coding: utf-8 -*-
"""手册页 / 阵容页的【卡图】: 卡 ID → 官方图片 URL(探测) + 本地缓存 (v4.107★)

用户原话: "他那边有图片 —— 我们也能有"。做图分两步: 知道地址, 和把图弄到本地。

一、地址从哪来
  自走棋的棋子图是【拼音文件名】家族:
    https://web.sanguosha.com/10/pc/res/assets/runtime/tavernChess/card/taosheng.png
    (实测 陶生 → 200 / 214KB)
  也就是"中文名 → 拼音"就能拼出来。拼音用 pypinyin 离线生成一张表
  (data/card_pinyin.json, 由 tools/build_card_pinyin.py 生成) —— 运行时【不需要装
  pypinyin】, 只查表; 没装也能跑, 重跑一次那个脚本就行。
  为什么不用皮肤表那条路: 皮肤表给的是【武将大立绘】编号(general/big/static/),
  和自走棋的棋子图不是一个东西, 编号也对不上。

二、图从哪来(三种状态, 页面自己降级)
  ① 本地缓存 data/card_img/<卡ID>.png —— 最好: 断网也能看, 秒开;
  ② 官方直链 —— 有网就有图(探测过的才给地址, 不给"猜"的地址);
  ③ 都没有 —— 页面隐藏图片位, 不留破图。

三、探测口径(和皮肤表/资源替换同一套: 不猜, 只问)
  HEAD 一下, 200 且 >1KB 才算数; 结果记进 data/card_img_hits.json, 下次不再问。
"""
import os
import json
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
PY_PATH = os.path.join(DATA_DIR, "card_pinyin.json")     # {卡ID: [拼音候选...]}
HIT_PATH = os.path.join(DATA_DIR, "card_img_hits.json")  # {卡ID: 官方URL} 探到的
IMG_DIR = os.path.join(DATA_DIR, "card_img")             # 本地缓存的图

# v4.107★ 路径段跟着赛季走(换季不用改代码): 取不到就用 10
SEG = "10"
try:
    from tools.season_config import current_season as _cs
    SEG = str(_cs() or "10").strip() or "10"
except Exception as e:
    print("[卡图] 取当前赛季失败, 先用路径段 10: %s" % e)

BASE = "https://web.sanguosha.com/%s/pc/res/assets/" % SEG
# 候选家族(按可能性排序): 棋子图 → 主公/大立绘那一档
FAMILIES = ("runtime/tavernChess/card/%s.png",
            "runtime/tavernChess/general/%s.png")

_lock = threading.Lock()
_py = None
_hits = None


def _load_json(p):
    # v4.109★ 改走 core.utils.load_json_file。注意这里原实现是 "json.load(f) or {}"
    #   —— 文件里写 null 时也要得到 {}, 所以末尾的 or {} 必须留着, 不能只靠 default。
    from core.utils import load_json_file
    return load_json_file(p, None) or {}


def _save_json(p, obj):
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        return True
    except Exception as e:
        print("[卡图] 写 %s 失败: %s" % (p, e))
        return False


def pinyin_map():
    """卡 ID → 拼音候选(离线表)。"""
    global _py
    if _py is None:
        with _lock:
            if _py is None:
                _py = _load_json(PY_PATH)
    return _py


def hits():
    """已经探到的 {卡ID: 官方 URL}。"""
    global _hits
    if _hits is None:
        with _lock:
            if _hits is None:
                _hits = _load_json(HIT_PATH)
    return _hits


def _remember(cid, url):
    h = hits()
    h[str(cid)] = url or ""
    with _lock:
        _save_json(HIT_PATH, h)


def candidates(cid, name=""):
    """这张卡的候选图片地址(按可能性排序)。没拼音表就返回空 —— 不猜。"""
    pys = pinyin_map().get(str(cid)) or []
    if isinstance(pys, str):
        pys = [pys]
    out = []
    for py in pys:
        py = str(py or "").strip()
        if not py:
            continue
        for fam in FAMILIES:
            out.append(BASE + (fam % py))
    return out


def _head_ok(url, timeout=6):
    """HEAD 一下: 200 且不是占位空文件才算数(和皮肤表探测同一口径)。"""
    try:
        import requests
        r = requests.head(url, timeout=timeout, allow_redirects=True)
        if int(r.status_code) != 200:
            return False
        size = int(r.headers.get("Content-Length") or 0)
        return size > 1024
    except Exception:
        return False


def resolve(cid, name="", force=False):
    """卡图地址: 本地缓存 > 已探到的 > 现探(探到就记下来)。都没有返回 ""。"""
    cid = str(cid)
    if not force:
        h = hits().get(cid)
        if h:
            return h
    for u in candidates(cid, name):
        if _head_ok(u):
            _remember(cid, u)
            return u
    _remember(cid, "")            # 探过但没有 = 记住"没有", 下次不再浪费一次请求
    return ""


def cached_path(cid):
    """本地缓存文件路径(存在才返回)。"""
    p = os.path.join(IMG_DIR, "%s.png" % str(cid))
    return p if os.path.exists(p) and os.path.getsize(p) > 1024 else None


def cache_one(cid, name="", timeout=10):
    """把一张卡的图下到本地缓存。返回 (ok, 说明)。

    顺序: 先看本地有没有(有就跳过) → 要地址(没有就探测) → 下载 → 存盘。
    """
    cid = str(cid)
    if cached_path(cid):
        return True, "已有缓存"
    url = hits().get(cid) or resolve(cid, name)
    if not url:
        return False, "官方没这张图(探过, 都没有)"
    try:
        import requests
        r = requests.get(url, timeout=timeout)
        if int(r.status_code) != 200 or len(r.content) < 1024:
            return False, "下下来是空的(%s)" % r.status_code
        os.makedirs(IMG_DIR, exist_ok=True)
        with open(os.path.join(IMG_DIR, "%s.png" % cid), "wb") as f:
            f.write(r.content)
        return True, "%d KB" % (len(r.content) // 1024)
    except Exception as e:
        return False, "下载失败: %s" % e


def cache_all(cards, limit=None, on_progress=None):
    """批量缓存(给手册页「缓存卡图」按钮用)。cards = [{id,name}, ...]

    on_progress(done, total, ok_n, last_msg) —— 每下一张回调一次, 界面能显示进度。
    """
    cards = list(cards or [])
    if limit:
        cards = cards[:int(limit)]
    total = len(cards)
    ok_n = 0
    for i, c in enumerate(cards, 1):
        ok, msg = cache_one(c.get("id"), c.get("name") or "")
        if ok:
            ok_n += 1
        if on_progress:
            try:
                on_progress(i, total, ok_n, "%s: %s" % (c.get("name") or c.get("id"), msg))
            except Exception as e:
                print("[卡图] 进度回调出错: %s" % e)
    return {"total": total, "ok": ok_n, "miss": total - ok_n}


def img_field(cid, name=""):
    """给 /api/cards 用的一个字段: 本地缓存优先(离线可用), 否则官方直链, 都没有就 ""。"""
    cid = str(cid)
    if cached_path(cid):
        return "/img/%s.png" % cid
    return hits().get(cid) or ""     # 不在这里探测: 打开手册不能被 372 次 HEAD 拖慢


if __name__ == "__main__":
    print(__doc__)
    print("卡图根目录:", IMG_DIR)
    print("拼音表:", PY_PATH, "条目", len(pinyin_map()))
    print("已探到的:", len(hits()))
