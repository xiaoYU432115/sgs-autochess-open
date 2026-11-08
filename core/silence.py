# -*- coding: utf-8 -*-
"""静默失败记账 —— 把历史存量里那 192 处 `except ...: pass` 变成【看得见】的。

为什么要有它: 以前全项目有 192 处"异常被吞掉且毫无提示"。绝大多数时候没事(本来就是
   兜底), 但正因为全都静默, "某个数据其实没存上"这种事就永远发现不了 —— 用户看到的
   是"跑了但好像没生效", 翻日志什么都没有。

做法: 【不改动"吞掉异常"这个行为】—— 改成抛出去会让原本能跑的流程开始报错
   (那些 except 本来就是兜底, 抛了反而坏事)。只把 pass 换成 note(): 照样吞, 但记一笔。
   开销 = 一次字典自增。

看结果:
   python -m core.silence           # 打印"哪些位置吞了多少次"
   代码里: from core import silence; silence.report()

DEBUG = True 时每条都打印(含位置与说明), 排查"到底哪儿静默失败了"时用。
"""
import time

_counts = {}      # "文件:行号" -> 累计次数
_why = {}         # "文件:行号" -> 说明(吞掉的是哪句)
_recent = []      # 最近若干条 (位置, 说明, 时间)
MAX_RECENT = 200
QUIET_AFTER = 3   # 同一位置前 3 次进 _recent/打印, 之后只在计数里攒着(免得刷屏)
DEBUG = False


def note(where, why=""):
    """在 except 里代替 pass 调一下: 照样吞掉异常, 但记一笔。

    绝不抛异常 —— 记账本身不该影响业务。注意这里用 return 而不是 pass:
    它自己不能再造一个新的静默点, 否则就白改了。
    """
    try:
        n = _counts.get(where, 0) + 1
        _counts[where] = n
        if why and where not in _why:
            _why[where] = why
        if DEBUG or n <= QUIET_AFTER:
            _recent.append((where, why, time.time()))
            if len(_recent) > MAX_RECENT:
                del _recent[0]
            if DEBUG:
                print("[静默] %s  %s" % (where, why))
    except Exception:
        return


def summary(top=30):
    """按次数倒序: [(位置, 说明, 次数)]。"""
    out = [(w, _why.get(w, ""), n) for w, n in _counts.items()]
    out.sort(key=lambda x: -x[2])
    return out[:top]


def report(top=30):
    lines = ["静默失败记账: %d 个位置, 共 %d 次" % (len(_counts), sum(_counts.values()))]
    if not _counts:
        lines.append("  (还没有 —— 一次都没吞过)")
        return "\n".join(lines)
    for w, why, n in summary(top):
        lines.append("  x%-6d %s%s" % (n, w, ("   %s" % why if why else "")))
    return "\n".join(lines)


def reset():
    _counts.clear()
    _why.clear()
    del _recent[:]


if __name__ == "__main__":
    print(report())
