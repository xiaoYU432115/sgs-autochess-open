"""挑牌诊断分析(v4.49★)。

为什么要单独有这一层
--------------------
脚本在『服务器等着挑武将/锦囊』时, 要判断候选该从哪个字段读。历史上这块是
**按优先级蒙着挑**(见 bridge/sgs_bridge.user.js 的 _scPendingPicks): 依次试
currentSelectChessOrSpellIndex → WaitSelectCards → waitSelectChess*Dic → 候选池,
谁先抠出 ID 就用谁。一次实机挑选只走一条分支, 于是『到底哪条来源是权威的』永远说不清。

v4.48 加了 _scPickDiagText(): 一次性把 9 个来源全 dump 出来。但 dump 只落在日志里,
要靠人肉翻 —— 于是『钉死来源』这件事一直没做完。v4.49 又补了最关键的一环:
**每次挑选(含成功路径)都记一行「候选源=X 池=Y 取第 n/N 个」**。

这一层就把日志里的证据抽出来直接给判决:
  · 哪条来源在真正供候选(候选源=X);
  · 有没有『没有权威来源、只能靠候选池兜底』的挑选(= 还在蒙着挑的那几次);
  · 诊断块里哪个来源有对象却抠不出 ID(说明字段名不对, 这就是『蒙』的根)。

设计: 全是纯函数(输入日志行, 输出 dict/文本), 不碰文件系统也不起界面,
所以能被 dev/_verify_pick_diag.py 用构造日志钉死。读文件的活统一交给 load_logs()。
"""

import os
import re

# 日志按大小轮转(见日志落盘那边), 旧证据在 .1 里也要一起看
LOG_CANDIDATES = ("log.txt", "log.txt.1", "log.txt.2")
DEFAULT_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

# 「候选源=... 池=... 取第 n/N 个」—— v4.49★ 的证据行
RE_SRC = re.compile(r"候选源=(?P<src>[^|\s]*)(?:\s+池=(?P<pool>[^|\s]*))?")
RE_TAKEN = re.compile(
    r"(?:取第\s*(?P<taken>[\d?]+)/(?P<total>\d+)\s*个|\((?P<t2>\d+)/(?P<n2>\d+)\))")
# 日志行首的时间戳, 有就取出来, 没有就算了
RE_TS = re.compile(r"^\s*\[?(\d{2}:\d{2}:\d{2})\]?")
# 诊断块里『某个来源』的行首标记
RE_DIAG_SECTION = re.compile(r"^[①②③④⑤⑥⑦⑧⑨]")


def load_logs(data_dir=None, limit_bytes=8 * 1024 * 1024):
    """把 data/ 下的 log.txt / log.txt.1 ... 读成一个列表 [(文件名, 行)]。

    为什么要带上轮转文件: 挑牌诊断是『偶然事件』(得正好赶上一次抠不出 ID 的挑选),
    只读当前 log.txt 很容易正好错过那一次。
    """
    d = data_dir or DEFAULT_DATA_DIR
    out = []
    for name in LOG_CANDIDATES:
        p = os.path.join(d, name)
        try:
            if not os.path.isfile(p):
                continue
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                txt = f.read()
            if len(txt) > limit_bytes:          # 超大日志只留尾部(新证据在后面)
                txt = txt[-limit_bytes:]
            for ln in txt.splitlines():
                if ln:
                    out.append((name, ln))
        except Exception:
            continue
    return out


def parse_src_lines(lines):
    """抽「候选源=... 池=... 取第 n/N 个」的证据行。

    src 为空或 '?' 表示这一次没读到权威来源 —— 那就是『蒙着挑』的那次, 要重点看。
    """
    out = []
    for fname, ln in lines:
        if "候选源=" not in ln:
            continue
        m = RE_SRC.search(ln)
        if not m:
            continue
        src = (m.group("src") or "").strip()
        pool = (m.group("pool") or "").strip()
        mt = RE_TAKEN.search(ln)
        taken, total = None, None
        if mt:
            if mt.group("taken"):
                taken, total = mt.group("taken"), int(mt.group("total"))
            else:
                taken, total = mt.group("t2"), int(mt.group("n2"))
        mts = RE_TS.match(ln)
        out.append({
            "file": fname, "ts": mts.group(1) if mts else "",
            "src": src, "pool": pool, "taken": taken, "total": total, "raw": ln.strip(),
        })
    return out


def src_name(src):
    """把「currentSelectChessOrSpellIndex(正等第0档)」压成「currentSelectChessOrSpellIndex」。

    统计时要把同一来源的不同档位归并, 否则每档一条会把统计表撑爆。
    """
    s = (src or "").strip()
    if not s:
        return ""
    return s.split("(", 1)[0].strip()


def parse_diag_blocks(lines):
    """抽挑牌诊断块。

    块的形状(见 bridge/sgs_bridge.user.js 的 _scPickDiagLog):
        ===== 挑牌诊断(候选里没有「X」) 候选来源全量 =====
          <来源①~⑨ 的现场 dump>
        ===== 诊断结束(把这几行发我即可定位候选源) =====
    所以: 含『挑牌诊断』的边框行开块, 含『诊断结束』的边框行收块;
    块没收尾(日志被截断)也认, 不能把已经拿到的现场丢掉。
    """
    blocks = []
    cur = None
    for fname, ln in lines:
        if "挑牌诊断" in ln and "=====" in ln:
            if cur is not None:
                blocks.append(cur)
            mts = RE_TS.match(ln)
            cur = {"file": fname, "ts": mts.group(1) if mts else "",
                   "text": "", "sections": [], "lines": 0}
            continue
        if cur is None:
            continue
        if "诊断结束" in ln:
            blocks.append(cur)
            cur = None
            continue
        body = ln.strip()
        if not body:
            continue
        cur["text"] += (("\n" if cur["text"] else "") + body)
        cur["lines"] += 1
        if RE_DIAG_SECTION.match(body):
            cur["sections"].append(body[:120])
    if cur is not None:
        blocks.append(cur)
    return blocks


def diag_section_key(line):
    """把诊断块里的一行压成『来源名』(给统计用) —— 去掉 ①~⑨ 标记再取等号/冒号前那段。"""
    s = RE_DIAG_SECTION.sub("", line or "", count=1).strip()
    return re.split(r"[=:：]", s, 1)[0].strip()[:32]


def _lines(maybe_lines):
    """允许调用方直接传『日志文本』或 load_logs() 的结果 —— 少写点胶水。"""
    if isinstance(maybe_lines, str):
        return [("(text)", l) for l in maybe_lines.splitlines() if l]
    return list(maybe_lines or [])


def analyze(maybe_lines):
    """日志证据 → 判决结果(dict)。纯函数, 不读文件。"""
    lines = _lines(maybe_lines)
    srcs = parse_src_lines(lines)
    diags = parse_diag_blocks(lines)

    by_src = {}
    for it in srcs:
        name = src_name(it["src"])
        if name and name != "?":
            by_src[name] = by_src.get(name, 0) + 1
    no_src = [it for it in srcs if src_name(it["src"]) in ("", "?")]
    with_pool = [it for it in srcs if it["pool"] and it["pool"] != "-"]
    pools = {}
    for it in with_pool:
        pools[it["pool"]] = pools.get(it["pool"], 0) + 1

    # 诊断块里哪些来源是被记录下来的现场(有对象却抠不出 ID 的那个来源就在里面)
    diag_hits = {}
    for b in diags:
        for s in b["sections"]:
            key = diag_section_key(s)
            if key:
                diag_hits[key] = diag_hits.get(key, 0) + 1

    return {
        "src_total": len(srcs),
        "by_src": by_src,
        "no_src": len(no_src),
        "pool_total": len(with_pool),
        "pools": pools,
        "diag_total": len(diags),
        "diag_hits": diag_hits,
        "dominant": _dominant(by_src),
        "srcs": srcs,
        "diags": diags,
    }


def _dominant(by_src):
    """出现最多的来源; 并列时按次数降序 + 名字升序 —— 结果稳定, 测试才好钉。"""
    if not by_src:
        return ""
    return sorted(by_src.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def verdict(res):
    """把 analyze() 的结果翻成一句人话结论 —— 这就是『钉死候选源』的答案。"""
    if not res or not res["src_total"]:
        return ("日志里还没有任何「候选源=」记录。\n"
                "下一步: 打一局(或让剧本跑一次自动挑), 再回来看 —— "
                "v4.49 之后【成功挑选也会记来源】, 不需要刻意制造失败。")
    out = []
    out.append("共 %d 次挑选记录: 有权威来源 %d 次, 只靠候选池兜底(蒙着挑) %d 次。"
               % (res["src_total"], res["src_total"] - res["no_src"], res["no_src"]))
    if res["dominant"]:
        top = sorted(res["by_src"].items(), key=lambda kv: (-kv[1], kv[0]))
        out.append("主力来源: " + "、".join("%s×%d" % (k, v) for k, v in top[:5]))
    if res["pool_total"]:
        out.append("候选池参与过 %d 次(%s) —— 池是【补充】, 不能拿它判断"
                   "『服务器还在不在等』(历史假警报就是这么来的)。"
                   % (res["pool_total"],
                      "、".join("%s×%d" % (k, v) for k, v in sorted(res["pools"].items()))))
    if res["no_src"]:
        out.append("⚠ 有 %d 次【没有权威来源】—— 这几次就是真正『蒙着挑』的地方, "
                   "要对着诊断块看是哪条来源有对象却抠不出 ID。" % res["no_src"])
    else:
        out.append("结论: 每次挑选都能指认来源, 可以据此把「%s」钉成第一优先读取源, "
                   "其余几条降为兜底。" % (res["dominant"] or "?"))
    if res["diag_total"]:
        hits = sorted(res["diag_hits"].items(), key=lambda kv: (-kv[1], kv[0]))[:6]
        out.append("诊断块 %d 个" % res["diag_total"]
                   + ("; 现场覆盖: " + "、".join("%s×%d" % (k, v) for k, v in hits) if hits else ""))
    else:
        out.append("没有诊断块(没出现过『抠不出候选 ID』的挑选; 也可能还没赶上)。")
    return "\n".join(out)


def report(maybe_lines=None, data_dir=None):
    """一站式: 读日志 → 判决 → 返回 (结果 dict, 结论文本)。界面和命令行都用这个。"""
    lines = load_logs(data_dir) if maybe_lines is None else _lines(maybe_lines)
    res = analyze(lines)
    return res, verdict(res)


if __name__ == "__main__":
    _res, _txt = report()
    print(_txt)
    if _res["srcs"]:
        print("\n最近 5 条证据:")
        for _it in _res["srcs"][-5:]:
            print("  " + _it["raw"][:150])
    if _res["diags"]:
        print("\n最后那个诊断块的前 12 行:")
        for _l in _res["diags"][-1]["text"].splitlines()[:12]:
            print("  " + _l[:150])
