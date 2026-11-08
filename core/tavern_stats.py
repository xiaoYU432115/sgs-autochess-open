# -*- coding: utf-8 -*-
"""对局统计(v4.88★) —— 助手端这一半。

分工:
  游戏页(bridge/sgs_bridge.user.js 的 SgsTavernStats) 负责【记】: 包装 manager 上的
    ReqShopRefreshChess / ReqShopBuyChess / ReqChessUseSpell / updateShopGoods /
    onRespShopRefreshChess, 攒成一份账本。
  这边负责【问、存、算】: 下发开始/停止/拉取指令, 把账本落盘成 json, 再算出
    『统计』页要显示的摘要 / 每回合 / 排行。

★ 数据口径与上游那份 TavernChessStats 一致, 所以这里导出的 json 可以直接喂给
  visualization.py(它认 totals.refresh.details / totals.buy.details /
  shopAppearances / spells / rounds / events 这几个键)。

为什么不直接装上游那个脚本: 它是另一份独立油猴, 自己找 manager、自己存日志,
  助手一个数都拿不到, 想看还得"导出文件再手动导入"。搬进桥接脚本之后是同一套
  manager、同一条回传通道, 『统计』页点一下就有数。
"""

import json
import os
import subprocess
import sys
import time
from collections import Counter

from core.config import BASE_DIR, DATA_DIR

# 日志落盘目录: 每局一个 json, 名字带时间, 不会被下一局覆盖
STATS_DIR = os.path.join(DATA_DIR, "tavern_stats")


def _int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return default


def ensure_dir():
    try:
        os.makedirs(STATS_DIR, exist_ok=True)
    except Exception as e:
        print("[统计] 建目录失败 %s: %s" % (STATS_DIR, e), file=sys.stderr)
    return STATS_DIR


class TavernStats(object):
    """对局统计的助手端入口。

    ops: core.remote_ops.BridgeOps(已经在 app 里建好, 传 self.ops 即可)
    """

    def __init__(self, ops, log=None):
        self.ops = ops
        self.log = log or (lambda m, level="INFO": None)

    # ---------- 下发指令 ----------
    def _cmd(self, action, **kw):
        """发一条指令并等结果。返回 dict(失败返回 {'ok': False, 'reason': ...})。"""
        try:
            res = self.ops.send_recv(action, kw, timeout=6.0)
        except Exception as e:
            return {"ok": False, "reason": "下发指令异常: %s" % e}
        if not res:
            return {"ok": False, "reason": "游戏页没有回应(没进对局 / 脚本没更新到 v4.88?)"}
        try:
            data = json.loads(res)
        except Exception:
            return {"ok": False, "reason": "返回不是JSON: %s" % str(res)[:120]}
        if not isinstance(data, dict):
            return {"ok": False, "reason": "返回格式不对: %s" % str(data)[:120]}
        return data

    def start(self, reset=True):
        return self._cmd("stats_start", reset=bool(reset))

    def stop(self):
        return self._cmd("stats_stop")

    def reset(self):
        return self._cmd("stats_reset")

    def dump(self):
        return self._cmd("stats_dump")

    # ---------- 落盘 / 读取 ----------
    def save(self, data, name=None):
        """把一份统计账本存成 json, 返回路径。"""
        ensure_dir()
        stats = (data or {}).get("stats") or {}
        gid = str(stats.get("gameId") or "game")
        ts = time.strftime("%Y%m%d-%H%M%S")
        safe = "".join(ch for ch in gid if (ch.isalnum() or ch in "-_"))[:40]
        fn = name or ("stats-%s-%s.json" % (ts, safe))
        if not fn.endswith(".json"):
            fn += ".json"
        path = os.path.join(STATS_DIR, fn)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(stats, f, ensure_ascii=False, indent=1)
        return path

    def list_saved(self, limit=50):
        ensure_dir()
        try:
            files = [f for f in os.listdir(STATS_DIR) if f.endswith(".json")]
        except Exception:
            return []
        files.sort(key=lambda f: os.path.getmtime(os.path.join(STATS_DIR, f)), reverse=True)
        return [os.path.join(STATS_DIR, f) for f in files[:limit]]

    @staticmethod
    def load(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)


# ============================================================
# 出图: 把一份统计日志交给 visualization.py
# ============================================================
# 为什么要有这一段: 『统计』页里的横条图是 tk.Canvas 画的(零依赖, 点开就能看),
#   但画不出【时间线/散点/回合趋势】那种图。项目里本来就有一份 visualization.py
#   (与上游 TavernChessStats 配套的那份), 它认的键就是我们导出的这套 —— 所以直接
#   subprocess 调它补上那 9 张 PNG, 不重复造轮子。
# 为什么不在页面里直接 import matplotlib: 它没进 requirements.txt(打包会变大一圈),
#   而且 Agg 后端画图要几秒, 同步调用会把界面卡死 —— 所以走子进程 + 线程。
VIS_SCRIPT = os.path.join(BASE_DIR, "visualization.py")
VIS_DEPS = ("matplotlib", "pandas")


def vis_missing_deps():
    """缺哪些画图依赖; 不缺返回空 list。"""
    missing = []
    for m in VIS_DEPS:
        try:
            __import__(m)
        except Exception:
            missing.append(m)
    return missing


def run_visualization(json_path, output_dir=None):
    """同步跑 visualization.py; 返回 (ok, 说明文字)。

    调用方请放线程里 —— 画 9 张图要几秒, 直接在按钮回调里跑会把界面卡住。
    """
    if not os.path.exists(VIS_SCRIPT):
        return False, "找不到 visualization.py: %s" % VIS_SCRIPT
    if not os.path.exists(json_path):
        return False, "统计日志不存在: %s(先点「保存日志」)" % json_path
    missing = vis_missing_deps()
    if missing:
        return False, ("缺画图依赖 %s —— 装一下: pip install %s"
                       % ("、".join(missing), " ".join(missing)))
    ensure_dir()
    out = output_dir or os.path.join(STATS_DIR, "charts-" + time.strftime("%Y%m%d-%H%M%S"))
    try:
        os.makedirs(out, exist_ok=True)
    except Exception as e:
        return False, "建输出目录失败 %s: %s" % (out, e)
    try:
        # encoding/errors 必须显式给: 它往 stdout 打的是中文, 而 Windows 默认按 gbk 解,
        #   解不开会在 subprocess 的读线程里抛 UnicodeDecodeError(只在控制台冒一串红字,
        #   不影响结果, 但错误信息就没了) —— 显式 utf-8 + replace 就没有这个坑。
        pr = subprocess.run(
            [sys.executable, VIS_SCRIPT, "--input", json_path, "--output-dir", out],
            capture_output=True, timeout=180, encoding="utf-8", errors="replace")
    except Exception as e:
        return False, "跑 visualization.py 失败: %s" % e
    tail = (pr.stdout or "").strip().splitlines()
    last = tail[-1] if tail else ""
    if pr.returncode != 0:
        err = (pr.stderr or "").strip().splitlines()
        return False, "画图失败(退出码 %s): %s" % (pr.returncode, (err[-1] if err else last))
    return True, "已出图: %s" % out


# ============================================================
# 纯计算: 从账本算出『统计』页要显示的东西
# ============================================================
def _details(data, section, key):
    box = ((data or {}).get("totals") or {}).get(section) or {}
    det = box.get(key) or []
    return [d for d in det if isinstance(d, dict)]


def _name_of(entry, fallback="?"):
    if not isinstance(entry, dict):
        return fallback
    sample = entry.get("sample") or {}
    n = sample.get("name") or entry.get("name")
    return str(n) if n else fallback


def analyze(data):
    """把账本算成 『摘要 / 每回合 / 三个排行』。全部容错: 缺字段就当 0/空。"""
    data = data or {}
    out = {
        "summary": [], "rounds": [], "top_cards": [], "top_spells": [],
        "bought": [], "refresh_events": [], "buy_events": [], "spell_events": [],
        "shop_snapshots": 0, "round_count": 0,
    }
    try:
        totals = data.get("totals") or {}
        rounds = data.get("rounds") or {}

        refresh = _details(data, "refresh", "details")
        buys = _details(data, "buy", "details")
        out["refresh_events"] = refresh
        out["buy_events"] = buys

        # 锦囊事件在 events 里(type='spell'), payload 里带名字
        for ev in (data.get("events") or []):
            if not isinstance(ev, dict) or ev.get("type") != "spell":
                continue
            pl = ev.get("payload") or {}
            sp = pl.get("spell") or {}
            out["spell_events"].append({
                "at": ev.get("at"), "round": _int(pl.get("round", ev.get("round")), 0),
                "name": sp.get("name") or "未知锦囊", "spellID": _int(sp.get("spellID"), 0),
            })

        r_total = totals.get("refresh") or {}
        b_total = totals.get("buy") or {}
        s_total = totals.get("shop") or {}
        out["shop_snapshots"] = _int(s_total.get("snapshots"), 0)

        hufu_r = sum(_int(d.get("cost"), 0) for d in refresh)
        hufu_b = sum(_int(d.get("cost"), 0) for d in buys)
        auto_r = sum(1 for d in refresh if d.get("isAuto") or d.get("is_auto"))

        # 卡牌出现总次数(排除锦囊, 与 visualization.py 口径一致)
        appearances = data.get("shopAppearances") or {}
        chess_total = 0
        for k, v in appearances.items():
            if k == "unknown" or str(k).startswith("spell:"):
                continue
            chess_total += _int((v or {}).get("count"), 0)
        spells = data.get("spells") or {}
        spell_total = sum(_int((v or {}).get("count"), 0) for v in spells.values())

        out["round_count"] = len(rounds)
        out["summary"] = [
            ("总回合数", len(rounds)),
            ("刷新次数", _int(r_total.get("count"), len(refresh))),
            ("  其中自动刷新", auto_r),
            ("刷新消耗虎符", _int(r_total.get("hufu"), hufu_r)),
            ("购买次数", _int(b_total.get("count"), len(buys))),
            ("购买消耗虎符", _int(b_total.get("hufu"), hufu_b)),
            ("总虎符消耗", hufu_r + hufu_b),
            ("商店快照数", out["shop_snapshots"]),
            ("卡牌出现总次数", chess_total),
            ("锦囊使用总次数", spell_total),
        ]

        # ---------- 每回合 ----------
        keys = set()
        for d in refresh + buys:
            keys.add(_int(d.get("round"), 0))
        for k in rounds.keys():
            keys.add(_int(k, 0))
        if not keys:
            keys = {1}
        for rn in sorted(k for k in keys if k > 0):
            rr = [d for d in refresh if _int(d.get("round"), 0) == rn]
            bb = [d for d in buys if _int(d.get("round"), 0) == rn]
            box = rounds.get(str(rn)) or {}
            shop_box = box.get("shop") or {}
            out["rounds"].append({
                "round": rn,
                "refresh": len(rr),
                "buy": len(bb),
                "cost": sum(_int(d.get("cost"), 0) for d in rr + bb),
                "snapshots": _int(shop_box.get("snapshots"), 0),
            })

        # ---------- 排行 ----------
        cards = []
        for k, v in appearances.items():
            if k == "unknown" or str(k).startswith("spell:"):
                continue
            c = _int((v or {}).get("count"), 0)
            if c > 0:
                cards.append((_name_of(v, str(k)), c))
        cards.sort(key=lambda x: x[1], reverse=True)
        out["top_cards"] = cards[:30]

        sp = []
        for k, v in spells.items():
            c = _int((v or {}).get("count"), 0)
            if c > 0:
                sp.append((_name_of(v, str(k)), c))
        sp.sort(key=lambda x: x[1], reverse=True)
        out["top_spells"] = sp[:30]

        bought = Counter()
        for d in buys:
            g = d.get("goods")
            n = None
            if isinstance(g, dict):
                n = g.get("name")
            n = n or d.get("goods_name") or d.get("goodsName")
            if n and str(n) not in ("", "unknown"):
                bought[str(n)] += 1
        out["bought"] = bought.most_common(30)
    except Exception as e:
        out["summary"].append(("统计解析异常", str(e)[:80]))
    return out
