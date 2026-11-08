# -*- coding: utf-8 -*-
"""终端对局类型识别 —— 看对手阵容, 判出"这一局是 8 种终端对局里的哪一种"。

数据来源: B站 BV1Kmte6jETq《【S10精忠报国】终端阵容解析》(118:55, 2026-09-04) 的【分P目录】,
  见 学习_耳目一曲_sgscodex.md 5.1 节。这是全套资料里最硬的一份。

⚠ 必须说清的能力边界(别把它当成"完整攻略"):
  · 我们只拿到【分P目录 = 8 个类型的名字】。作者配套的详细配阵/站位在 PPT(百度网盘)
    和 xlsx(金山文档)里, **没有拿到**。
  · 所以这里能做的是【识别】: 从对手阵容的卡名/势力, 判出它属于哪类对局。
  · 对策只给【定性思路】(从分P标题和评论里能推出来的那部分), 不编造具体站位数字。
    想要完整版: 去拿那份 xlsx(链接在 sgscodex 5.1 节配套资料里), 我把表接进来。

识别怎么判的: 每种类型给一组"信号卡"(哪几张卡同时出现, 基本就是这一类)。命中越多分越高,
  按分排序。信号卡是我按类型名反推的关键卡(比如"多田" = 多个田尚衣), 卡名都对着本季卡池
  核过 —— 卡池里没有的(如"菜刀""高栏"是玩法称呼不是卡名)就没当信号用。

⚠ 第二个坑: 资料里的势力名和本季卡池【对不上】。资料说"西凉护甲""黄巾内战", 但本季卡池
  的 faction 只有 【群 吴 神 魏 蜀 晋 汉 袁】, 没有西凉也没有黄巾 —— 实测张梁/管亥的势力
  是"晋"、皇甫嵩是"汉"、马伶俐是"袁"。推测"西凉/黄巾"是旧赛季称呼或玩法标签。
  所以【识别只认卡名, 不认势力名】; 势力分布照卡池真值显示, 但不拿它去匹配类型。

只读: 不发包、不改游戏状态。
"""
import sys

from core import id_map as IDMAP

# ==================== 8 种终端对局 ====================
# 每项: key / name(类型名, 照抄分P目录) / signals(信号卡名, 命中越多越像) /
#        factions(涉及势力, 辅助判断) / hint(定性对策 —— 只写能站得住的部分)
TYPES = [
    {
        "key": "duotian_du",
        "name": "多田 v.s. 多毒庞统",
        "scope": "魏蜀吴西凉",
        "signals": ["田尚衣", "庞统"],
        "counts": {"田尚衣": 2, "庞统": 2},   # "多田/多毒"要真的是多张才算
        "hint": "两边都在堆同名核心(多个田尚衣 / 多个庞统)。这类对局拼的是【谁的同名卡先成型】, "
                "所以看到对手已经在堆了, 你要么跟堆, 要么走清场路线打断它成型。",
    },
    {
        "key": "shu_vs_xiliang",
        "name": "纯蜀传功 v.s. 纯西凉护甲",
        "scope": "蜀 vs 西凉",
        "signals": ["庞统", "董承", "何进", "马伶俐"],
        "factions": ["蜀", "西凉"],
        "hint": "传功(蜀)打护甲(西凉)是经典对位: 传功靠叠数值, 护甲靠减伤扛。你站哪边决定你要"
                "优先补什么 —— 对面是护甲就别硬拼数值, 走穿甲/真实伤害类。",
    },
    {
        "key": "xiliang_start",
        "name": "西凉护甲 + 皇甫嵩 / 马伶俐, 开战多启动",
        "scope": "西凉",
        "signals": ["皇甫嵩", "马伶俐"],
        "factions": ["西凉"],
        "hint": "靠开战瞬间的多次启动打爆发。对策是【别让它开出来】—— 先手控制/沉默优先于硬堆身材。",
    },
    {
        "key": "tian_spray",
        "name": "田尚衣喷射(可达 8 位数伤害)",
        "scope": "喷射体系",
        "signals": ["田尚衣", "郭图", "白绕"],
        "factions": [],
        "hint": "喷射的上限极高(作者说能到 8 位数), 但怕【中途被打断】。评论里提到的袁势力 bug 会让"
                "喷射棋子喷到一半停转 —— 那是游戏侧问题, 我们只能看不能修。",
    },
    {
        "key": "short_game",
        "name": "魏蜀吴汉局: 短局看谁准备得多",
        "scope": "魏蜀吴汉",
        "signals": ["庞统", "于吉", "曹丕", "程昱", "田尚衣"],
        "factions": ["魏", "蜀", "吴", "汉"],
        "hint": "短局 = 回合少, 成型慢的阵容直接没机会。作者列的几条路线: 菜刀 / 庞统于吉 / "
                "田尚衣曹丕 / 毒程昱。这类局【前几回合的准备量】决定胜负。",
    },
    {
        "key": "huangjin_mirror",
        "name": "黄巾内战(无七星灯) —— 关键是伪复活",
        "scope": "黄巾",
        "signals": ["张梁", "管亥", "张让", "赵忠"],
        "factions": ["黄巾"],
        "hint": "双方都黄巾时没有七星灯, 胜负手是【伪复活】(死了还能再起来一次的机制)。"
                "别把资源浪费在一次性爆发上。",
    },
    {
        "key": "dongcheng_hejin",
        "name": "魏蜀汉西凉 / 蜀吴汉西凉 —— 最优董承 / 何进",
        "scope": "魏蜀汉西凉 / 蜀吴汉西凉",
        "signals": ["董承", "何进"],
        "factions": ["魏", "蜀", "汉", "西凉", "吴"],
        "hint": "作者说这几个势力组合下, 董承 / 何进是最优解。看到这个势力分布就往这两个核心靠。",
    },
    {
        "key": "shu_dongwan",
        "name": "蜀董绾: 面对大身材 vs 清场喷射, 配阵与站位不同",
        "scope": "蜀",
        "signals": ["董绾"],
        "factions": ["蜀"],
        "hint": "同一套董绾, 对面是【大身材】还是【清场喷射】要换配阵和站位 —— 这部分是站位细节, "
                "在作者 PPT 里, 我们没拿到, 只能提示你: 先分清对面是哪一种再摆。",
    },
]

# 势力统计要用的: 卡池里每张卡属于哪个势力
def _faction_of(name):
    try:
        from core.lineup_advisor import LineupAdvisor
        row = LineupAdvisor().card_info(name)
        return (row or {}).get("faction") or ""
    except Exception as e:
        sys.stderr.write("[终端识别] 查「%s」势力失败: %s\n" % (name, e))
        return ""


def names_from_board(board):
    """对手阵容(协议里的 _board / lastLineUp) → 卡名列表。

    协议给的是 {chessID, attack, hp, ...}, 没有名字 —— 这里用 id_map 翻成名字。
    """
    out = []
    for c in (board or []):
        if not isinstance(c, dict):
            continue
        nm = c.get("name") or c.get("n")
        if not nm:
            cid = c.get("chessID") or c.get("goodsID") or c.get("id")
            if cid:
                try:
                    nm = IDMAP.name_of(cid)
                except Exception as e:
                    sys.stderr.write("[终端识别] chessID=%s 翻不出名字: %s\n" % (cid, e))
        if nm:
            out.append(str(nm))
    return out


def analyze(board):
    """输入对手阵容(协议 _board) → 判定结果。

    返回 {ok, names, factions, types:[{name, score, hit, hint}], top}
    """
    names = names_from_board(board)
    if not names:
        return {"ok": False, "reason": "没读到对手阵容(要等对手上过场 / 点过他头像才有)",
                "names": [], "factions": {}, "types": [], "top": None}

    fa = {}
    for n in names:
        f = _faction_of(n)
        if f:
            fa[f] = fa.get(f, 0) + 1

    scored = []
    for t in TYPES:
        hit = [s for s in t["signals"] if s in names]
        if not hit:
            continue
        # 命中信号数 = 分。"多田/多毒"这类要看同名卡是不是真的堆了好几张。
        score = float(len(hit))
        for nm, need in (t.get("counts") or {}).items():
            if names.count(nm) >= need:
                score += 1.0
        scored.append({"key": t["key"], "name": t["name"], "scope": t.get("scope", ""),
                       "score": round(score, 1), "hit": hit, "hint": t["hint"]})
    scored.sort(key=lambda x: -x["score"])

    return {"ok": True, "names": names, "factions": fa, "types": scored,
            "top": scored[0] if scored else None}


def format_report(d, limit=3):
    """把判定结果写成给人看的文本。"""
    if not d.get("ok"):
        return "读不出对手阵容: %s" % d.get("reason", "?")

    lines = []
    lines.append("对手阵容: " + " ".join(d["names"][:12])
                 + (" ..." if len(d["names"]) > 12 else ""))
    if d["factions"]:
        fa = sorted(d["factions"].items(), key=lambda kv: -kv[1])
        lines.append("势力分布: " + "  ".join("%s×%d" % (k, v) for k, v in fa))
    lines.append("")

    if not d["types"]:
        lines.append("没对上 8 种终端对局里的任何一种(信号卡都没出现) —— 可能还在过渡期, "
                     "或者是一套我没见过的阵容。")
        return "\n".join(lines)

    lines.append("像这一类(按像的程度排):")
    for t in d["types"][:limit]:
        lines.append("  【%s】%s" % (t["name"], t["scope"]))
        lines.append("    命中: " + "、".join(t["hit"]))
        lines.append("    " + t["hint"])
    lines.append("")
    lines.append("· 对策只到定性为止 —— 详细配阵/站位在作者那份 xlsx 里(链接见 sgscodex 5.1), "
                 "我们没拿到, 不编。")
    return "\n".join(lines)


def from_live(only_alive=True):
    """直接从 LIVE 读当前所有对手, 逐个判定。返回 [(昵称, 判定结果)]。"""
    try:
        from core.protocol import LIVE
    except Exception as e:
        sys.stderr.write("[终端识别] 读 LIVE 失败: %s\n" % e)
        return []
    out = []
    for o in (getattr(LIVE, "others", None) or []):
        if not isinstance(o, dict):
            continue
        if only_alive and (o.get("hp") is not None) and int(o.get("hp") or 0) <= 0:
            continue
        board = o.get("_board")
        if not board:
            continue
        out.append((o.get("nickname") or o.get("userID") or "?", analyze(board)))
    return out
