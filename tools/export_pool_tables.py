"""导出自走棋「发牌概率 + 卡池规模」成人能看的表 / 程序能读的 json。

为什么单独导一份:
  原始 data/season/s{季}/TavernChessConfig.json 有 42 张表、ChessConf 3346 条, 想看一眼
  "我 6 级刷到 4 费的概率是多少" 得现写脚本。而这些数字是【既定规则】, 不随对局变化 ——
  导出来一次就够, 之后直接查表, 不用每次去翻 json。

  ⚠ 这个文件同时是【给用户看的说明书】: 表后面那几节解释概率怎么算、能拿来干什么。
  改这个工具时别只盯着数字, 那几节说明是用户真正要的东西。

⚠ 两个必须记住的坑(踩过):
  ① 【必须按 SeasonID 过滤】ChessConf 3346 条是 10 个赛季累积的。不过滤会把前 9 季的
     棋子全算进这一季的池子 —— 算出来 1600 种 / 18362 张, 数字大得离谱, 但看着又挺像真的,
     不做交叉验证根本发现不了。过滤后 s10 是 184 种 / 2078 张。
  ② 【RollingChance 是权重, 不是百分比】要除以 ShowPercentage 才是概率:
         概率% = 100 × RollingChanceN / ShowPercentage
     这个公式不是猜的, 是游戏自己算的(TavernChessShopRateUI.UpdateUI)。
     1~5 级 ShowPercentage=100(权重和正好 100), 6 级是 1000(权重和正好 1000) ——
     直接当百分比读, 6 级的 4 费会算出 "300%"。
  ③ 【同名不同源】"本季"这类字段在 ChessGlobalConf 和 ChessModeSeasonConf 里各有一份,
     而 ChessGlobalConf 那份是【旧赛季残留】: s10 的势力权重, global 只有 5 个势力,
     本赛季行里是 7 个。取错了表也照样画得出一张完整的表, 只是静默少两个势力 ——
     所以本工具取 season 行, 并把取源写进产物、由 validate() 盯着回归。

用法:
    python tools/export_pool_tables.py            # 默认赛季 s10, 输出到 参考_配置表/
    python tools/export_pool_tables.py --season 11
    python tools/export_pool_tables.py --check    # 只跑自洽性校验, 不写文件
"""
import argparse
import io
import json
import os
import sys
from collections import Counter
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    # 不包 try/except: pass —— 项目的静态自检对"静默 except"是只许减不许增的预算制,
    # 而 hasattr 已经挡住了"这个方法不存在"这一种情况。真出错就该看见, 不该吞掉。
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "参考_配置表")

# 同一份 json 里其他值得知道的表 —— 用户问"还能拿到什么关于自走棋的", 这份清单就是答案。
# ⚠ 用途是【人工判断】写死的, 不是从文件里读的: 里面带 ★ 的那几条是我实际翻过内容确认过的,
#   改配置表结构时记得回来看一眼。条数才是动态读的(换赛季自动更新)。
OTHER_TABLES = [
    ("ChessConf", "棋子本体: 名字、费用、攻击/血量、技能、势力、种族、属于哪个赛季",
     "★ 助手已在用（棋子库、中文名反查 ID）"),
    ("ChessClientSkillConf", "技能【自动触发的硬条件】: 场上要几个兵、商店要有几张牌、金币够不够、神势力是否禁用",
     "★ 最被低估的一张: 判断\"这个技能现在能不能放\"的直接依据"),
    ("ChessSkillConf", "技能全文: 描述、触发类型、优先级、可触发次数、激活概率",
     "★ 剧本\"自动放技能\"要的条件判断"),
    ("ChessGeneralConf", "主公: 血量、默认皮肤、要求的势力、是否可用",
     "★ 已经在用（选主公）"),
    ("ChessGeneralSkillConf", "主公技能: 类型、效果、花费类型、描述",
     "★ 已经在用（主公技能按钮）"),
    ("ChessWeaponConf", "装备: 类型、品阶、第几回合能选、技能",
     "装备推荐 / 第几回合该留钱"),
    ("ChessWeaponSkillConf", "装备技能的效果与触发时机", "同上"),
    ("ChessSpellConf", "锦囊: 名字、品阶、技能、关键字、赛季",
     "已经在用（锦囊库）"),
    ("ChessBuffConf", "Buff 定义: 效果类型、是否叠加、叠加方式、生效参数",
     "查\"这个状态到底加多少\""),
    ("ChessBuffSizeChangeConf", "Buff 带来的体型/数值变化", "同上"),
    ("ChessModeRankConf", "段位分数区间、Elo 的 K 值、会不会掉段",
     "局面评估 / 对手水平参考"),
    # 这里不写"赛季名长什么样"的例子: 写死的例子过两个赛季就是错的。
    #   实际赛季名由 to_markdown() 从快照里读出来填, 换赛季自动跟着变。
    ("ChessModeSeasonConf", "赛季名、起止时间、初始分、各势力权重",
     "★ 换赛季时确认当前是哪一季"),
    ("ChessClientKeywordConf", "技能关键字（18 个）的颜色和说明", "技能描述的解析"),
    ("ChessClientStoryConf", "每张牌的背景故事（4398 条）", "百科/UI 展示"),
    ("ChessPrizewheelConf", "转盘: 可选棋子费用、随机次数、开启周期", "转盘/选秀玩法"),
    ("ChessPrizewheelPoolConf", "转盘的棋子组合（每行 7 个位置）", "同上"),
    ("ChessRookieGameCustomConf", "新手局的发牌控制参数（比例、可选数量）", "新手局特殊规则"),
    ("ChessModeServerMaintenanceConf", "服务器维护时间段（26 条）", "解释\"今天为什么进不去\""),
]


def load(season):
    jp = os.path.join(ROOT, "data", "season", "s%d" % season, "TavernChessConfig.json")
    if not os.path.exists(jp):
        raise SystemExit("找不到赛季快照: %s\n"
                         "(用 tools/season_config.py fetch 拉一份, 或先切到已有的赛季号)" % jp)
    with open(jp, encoding="utf-8") as f:
        return json.load(f), jp


def build(cfg, season):
    """把散在 4 张表里的数字收拢成一份结构。"""
    # 先显式确认这几张表都在。用 get + 自己报错, 而不是直接拿键去索引:
    #   ① 换赛季时表名真的可能变 —— 直接 KeyError 甩一屏调用栈, 不如说清"哪张表不见了";
    #   ② 项目的静态自检不许对配置对象裸索引(dev/_verify_static.py 的 S3), 这是约定。
    for need in ("ChessShopConf", "ChessRankConf", "ChessConf", "ChessGlobalConf"):
        if need not in cfg:
            raise SystemExit("配置快照里没有 %s —— 换赛季后表名可能变了, 先确认再导。" % need)
    shop = sorted(cfg.get("ChessShopConf") or [], key=lambda r: r["ShopRank"])
    rank_conf = {r["Rank"]: r for r in (cfg.get("ChessRankConf") or [])}

    # 池内棋子 = 标记了 IsInRandomPool 且属于本季的。
    #   不用 IsShop 是因为两者几乎等价(1600 vs 1606), 而 IsInRandomPool 的名字就是
    #   "进随机池" —— 算发牌概率该用的正是这一条。
    pool = [c for c in (cfg.get("ChessConf") or [])
            if c.get("IsInRandomPool") and c.get("SeasonID") == season]
    kinds = Counter(c["ChessRank"] for c in pool)

    rows = []
    for s in shop:
        sp = s.get("ShowPercentage") or 1
        row = {
            "shopRank": s["ShopRank"],
            "upgradeCost": s.get("UpgradeCost", 0),
            "columns": s.get("ShopColumn", 0),      # 这个等级商店有几格
            "showPercentage": sp,
            # 神将(RollingChanceGod): 只有 6 级给了 4, 别的等级都是 0。
            #   ⚠ 这个字段在客户端源码里【命中 0 处】—— 游戏的商店概率界面
            #   (TavernChessShopRateUI.UpdateUI) 只循环 RollingChance1..6, 不显示神将。
            #   所以它是给服务器/别的端用的。这里照样列出来是因为它在配置里确实存在,
            #   但必须标注"客户端不显示", 免得用户拿 0.4% 去做决策却发现游戏里查不到。
            "god": 100.0 * (s.get("RollingChanceGod", 0) or 0) / sp,
            "chance": {},                            # 费用档 -> 占该格的概率%
            "kinds": {},                             # 费用档 -> 池内有多少种
            "perCell": {},                           # 费用档 -> 单格刷到【指定某一张】的概率%
            "wantAny": {},                           # 费用档 -> 整刷至少出现【任意一张】N费的概率%
            "wantOne": {},                           # 费用档 -> 整刷至少出现【指定某一张】的概率%
            "expectRolls": {},                       # 想要指定某张牌 -> 期望刷新次数
        }
        # 注意: "抽到任意某张 N 费牌"要看池内张数占比, 但同费用档【每种张数相同】
        #   (ChessRankConf 每档只给一个 TotalCount), 所以"整刷出现任意 N 费"就等于
        #   该档总概率, 而"单格指定某张" = 档概率 / 档种数 —— 池模型与均匀模型在这里等价。
        for n in range(1, 7):
            w = s.get("RollingChance%d" % n, 0) or 0
            p_any = 100.0 * w / sp
            k = kinds.get(n, 0)
            p_one_cell = (p_any / 100.0 / k) if k else 0.0     # 单格命中指定牌的概率(0~1)
            cols = row["columns"] or 1
            p_one_roll = 1.0 - (1.0 - p_one_cell) ** cols       # 一次刷新(多格)至少命中1张
            row["chance"][n] = p_any
            row["kinds"][n] = k
            row["perCell"][n] = p_one_cell * 100.0
            row["wantAny"][n] = p_any
            row["wantOne"][n] = p_one_roll * 100.0
            row["expectRolls"][n] = (1.0 / p_one_roll) if p_one_roll > 0 else None
        rows.append(row)

    total_pool = sum(kinds.get(rk, 0) * (rank_conf.get(rk, {}).get("TotalCount", 0))
                     for rk in range(1, 7))
    season_row = {}
    for s0 in cfg.get("ChessModeSeasonConf", []) or []:
        if s0.get("SeasonID") == season:
            season_row = s0
            break
    season_name = season_row.get("SeasonName", "") or ""

    # 各势力有哪些棋子。
    #   为什么要列棋子名: 配置里【没有】势力 ID → 名字的对照表, 只有一串数字(MinionType)。
    #   与其我猜"4 就是群"写进文档, 不如把该势力在本季池子里的棋子列出来让用户自己认 ——
    #   看到"曹休/夏侯惇/邓艾"谁都知道是魏。这是"摆证据"而不是"替配置表下结论"。
    minion_names = {}
    for c in pool:
        mt = c.get("MinionType")
        if mt is None:
            continue
        minion_names.setdefault(str(mt), []).append(c.get("ChessName"))

    # 势力权重【必须从 ChessModeSeasonConf 本赛季那一行取】。
    #   ChessGlobalConf 里有个同名字段, 但那是旧赛季留下的: s10 的 global 只有 5 个势力,
    #   本赛季那一行有 7 个。取错源会漏掉两个势力, 而且漏得毫无痕迹 —— 表看着是齐全的。
    #   所以下面把"取的是哪个源"也带出去, 并由 validate() 盯着, 防止以后改回去。
    minion_weights = {}
    weight_source = "ChessModeSeasonConf"
    for it in season_row.get("MinionWeightCurSeason") or []:
        minion_weights[str(it.get("Minion"))] = it.get("Weight")
    # 顺手把 global 那份也收下来: 文档里要说清"两个源差在哪", 差多少得是算出来的,
    #   不能在说明里写死一个"5" —— 那换赛季就不对了。
    global_weights = {}
    for it in (cfg.get("ChessGlobalConf") or [{}])[0].get("MinionWeightCurSeason") or []:
        global_weights[str(it.get("Minion"))] = it.get("Weight")
    if not minion_weights:
        weight_source = "ChessGlobalConf"
        minion_weights = dict(global_weights)
    return {
        "season": season,
        "seasonName": season_name,
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "shopRefreshCost": (cfg.get("ChessGlobalConf") or [{}])[0].get("ShopRefreshCost", 1),
        "kindsPerRank": {str(rk): kinds.get(rk, 0) for rk in range(1, 7)},
        "copiesPerRank": {str(rk): rank_conf.get(rk, {}).get("TotalCount", 0) for rk in range(1, 7)},
        "poolTotal": total_pool,
        "poolKinds": len(pool),
        "rows": rows,
        "global": (cfg.get("ChessGlobalConf") or [{}])[0],
        "minionNames": minion_names,
        "minionWeights": minion_weights,
        "minionWeightSource": weight_source,
        "globalMinionWeights": global_weights,
        # 赛季行里的规则(和势力权重同理: 以本赛季那一行为准)
        "seasonRules": {k: season_row.get(k) for k in ("MinionNumCurSeason", "MinionMustCurSeason")},
        # 各表条数: 让"还能拿到什么"那节能带上真实规模(换赛季自动更新, 不用手改数字)
        "tableCounts": {k: len(v) for k, v in cfg.items() if isinstance(v, list)},
    }


def validate(d):
    """自洽性校验 —— 数字对不对, 得让数据自己说, 不能靠看着顺眼。"""
    out = []
    for row in d["rows"]:
        s = sum(row["chance"].values())
        # 权重和应当正好等于 ShowPercentage(即概率和正好 100%)。容差给 0.05 而不是 0.5:
        #   0.5 的容差会把"某档权重写错了 0.4%"这种错漏过去, 而这正是要抓的东西。
        ok = abs(s - 100.0) < 0.05
        out.append(("等级%d 各费用概率之和 = %.1f%%" % (row["shopRank"], s), ok,
                    "" if ok else "应当正好 100%, 说明 ShowPercentage 口径变了"))
    # 池规模落在常识区间: 太少不够 8 家抢, 太多没稀缺性
    ok = 300 <= d["poolTotal"] <= 6000
    out.append(("池总量 %d 张 / %d 种" % (d["poolTotal"], d["poolKinds"]), ok,
                "" if ok else "量级异常 —— 大概率忘了按 SeasonID 过滤"))
    cpr = d["copiesPerRank"]
    ok = all(cpr[str(a)] >= cpr[str(b)] for a, b in ((1, 2), (2, 3), (3, 4), (4, 5), (5, 6)))
    out.append(("每种张数随费用递减 (%s)" % "/".join(str(cpr[str(i)]) for i in range(1, 7)), ok,
                "" if ok else "低费该比高费常见, 顺序反了就说明表读错了"))
    # 这条专门盯"势力权重取错源"这种静默退化: 旧赛季残留的 global 字段条数偏少,
    #   少了也照样能画出一张看着完整的表, 只有比对源才知道取错了。
    nw, nn = d.get("minionWeights") or {}, d.get("minionNames") or {}
    ok = bool(nw) and d.get("minionWeightSource") == "ChessModeSeasonConf"
    out.append(("势力权重 %d 条 / 池内势力 %d 组（源 %s）"
                % (len(nw), len(nn), d.get("minionWeightSource")), ok,
                "" if ok else "取的是 ChessGlobalConf —— 那是旧赛季残留, 会漏势力, 先确认表结构"))
    return out


def to_markdown(d):
    P = []
    A = P.append
    # 赛季名本身已经带 "S10" 前缀, 别再拼一遍变成"赛季 s10 S10 精忠报国"。
    _sn = d.get("seasonName") or ""
    if _sn.startswith("S%d" % d["season"]):
        _sn = _sn[len("S%d" % d["season"]):].strip()
    A("# 自走棋 · 发牌概率与卡池（赛季 s%d%s）"
      % (d["season"], (" " + _sn) if _sn else ""))
    A("")
    A("> 生成时间 %s　·　数据源 `data/season/s%d/TavernChessConfig.json`" % (d["generatedAt"], d["season"]))
    A("> 重新生成：`python tools/export_pool_tables.py --season %d`" % d["season"])
    A("")
    A("这份表是**既定规则**，不随对局变化（对面买了多少不影响这里的数字，见第五节）。")
    A("原始的 `TavernChessConfig.json` 有 %d 张表，这里是其中跟\"刷牌\"有关的 4 张。"
      % len(d["tableCounts"]))
    A("")

    A("## 一、商店价位表（官方口径，源码实锤）")
    A("")
    A("公式不是猜的，是游戏自己算的（`TavernChessShopRateUI.UpdateUI`）：")
    A("")
    A("```")
    A("该格出现 N 费的概率(%) = 100 × RollingChance{N} / ShowPercentage")
    A("```")
    A("")
    A("`RollingChance` 是**权重不是百分比**——1~5 级 `ShowPercentage=100`（权重和正好 100），")
    A("6 级是 `1000`（权重和正好 1000）。直接当百分比读，6 级的 4 费会算出\"300%\"。")
    A("")
    A("| 等级 | 升级费 | 商店格数 | " + " | ".join("%d费" % i for i in range(1, 7)) + " | 神将 |")
    A("|---|---|---|" + "---|" * 6 + "---|")
    for row in d["rows"]:
        cells = []
        for n in range(1, 7):
            v = row["chance"][n]
            cells.append("%.1f%%" % v if v else "—")
        god = row.get("god") or 0
        cells.append(("%.2f%%" % god) if god else "—")
        A("| %d | %d | %d | %s |" % (row["shopRank"], row["upgradeCost"], row["columns"],
                                      " | ".join(cells)))
    A("")
    A("> 神将那一列只是照抄配置（只有 6 级有值）。它在**客户端源码里从来没被读过**——")
    A("> 游戏的商店概率界面只循环 1~6 费，不显示神将。所以这个数当参考，别当游戏里能查到的依据。")
    A("")
    A("## 二、池里每种有几张（`ChessRankConf`）")
    A("")
    A("| 费用 | 每种张数 | 本季种数 | 该档池内总数 |")
    A("|---|---|---|---|")
    for n in range(1, 7):
        k = d["kindsPerRank"][str(n)]
        c = d["copiesPerRank"][str(n)]
        A("| %d费 | %d | %d | %d |" % (n, c, k, k * c))
    A("| **合计** | | **%d** | **%d** |" % (d["poolKinds"], d["poolTotal"]))
    A("")
    A("8 家玩家共享这一个池（`%d` 张 / `%d` 种）。" % (d["poolTotal"], d["poolKinds"]))
    A("")
    A("## 三、想要某张牌，大概要刷几次")
    A("")
    A("前提：同费用档的每一种牌，在池里的**张数都一样**（表二每档只给了一个数字），所以")
    A("\"从池里摸\"和\"均匀随机\"在这里恰好等价，分母直接用**种数**就行，不用去算张数占比。")
    A("")
    A("```")
    A("单格命中指定牌   = 该费用档概率 / 该档种数")
    A("整刷命中指定牌   = 1 - (1 - 单格概率)^商店格数")
    A("期望刷新次数     = 1 / 整刷概率")
    A("期望金币         = 期望刷新次数 × %d（刷新一次 %d 金币）" % (d["shopRefreshCost"], d["shopRefreshCost"]))
    A("```")
    A("")
    A("| 等级 | 目标费用 | 单格概率 | 整刷概率 | 期望刷新次数 |")
    A("|---|---|---|---|---|")
    for row in d["rows"]:
        for n in range(1, 7):
            p = row["wantOne"][n]
            if not p:
                continue
            e = row["expectRolls"][n] or 0
            A("| %d | %d费 | %.2f%% | %.2f%% | %.1f |"
              % (row["shopRank"], n, row["perCell"][n], p, e))
    A("")
    A("刷新一次 %d 金币，所以\"期望刷新次数\"就是期望金币（**不含买牌的钱**）。" % d["shopRefreshCost"])
    A("")

    # 「停在几级刷最划算」—— 这是上面数据里最反直觉的一点, 必须单独拎出来:
    #   想刷 4 费, 6 级(23.8 次)反而比 5 级(28.4 次)容易; 想刷 3 费, 4 级最好,
    #   升到 5 级反而更难。原因是升级会往池子里掺入更高费的档位, 把低费的概率摊薄了。
    #   人的直觉是"等级越高越容易刷出想要的牌" —— 对 5/6 费成立, 对 3/4 费恰好相反,
    #   而"该不该升本"恰恰是自走棋里最常做的决策之一。所以这张表不列出来就是白算。
    A("### 刷某个费用的牌，停在几级最划算")
    A("")
    A("| 目标费用 | 最划算的等级 | 期望刷新次数 | 其他等级（次） |")
    A("|---|---|---|---|")
    for n in range(1, 7):
        pairs = []
        for row in d["rows"]:
            e = row["expectRolls"][n]
            if e:
                pairs.append((e, row["shopRank"]))
        if not pairs:
            continue
        pairs.sort()
        best_e, best_lv = pairs[0]
        others = "、".join("%d级 %.1f" % (lv, e) for e, lv in pairs[1:]) or "—"
        A("| %d费 | **%d 级** | %.1f | %s |" % (n, best_lv, best_e, others))
    A("")
    A("> ⚠ 这只比\"刷出来的难易\"，**没算升级本身的金币**。所以\"6 级刷 4 费最划算\"是说")
    A("> 你已经在 6 级时；为了刷 4 费专门从 4 级升到 6 级要花几十金，那是另一笔账。")
    A(">")
    A("> 另外实战里还有两个约束这张表管不了：**买得起**（金币）和**买完占格子**（手牌上限）")
    A("> —— 见第五节。")
    A("")

    g = d["global"]
    A("## 四、同表里的其他规则（`ChessGlobalConf`，一局只读一次）")
    A("")
    A("| 字段 | 值 | 意思 |")
    A("|---|---|---|")
    for k, label in (("ShopRefreshCost", "刷新一次商店的金币"),
                     ("MaxHandCard", "手牌上限"),
                     ("MaxBattleTime", "一回合战斗最长秒数"),
                     ("SkipBattleTime", "跳过战斗的秒数"),
                     ("GeneralSelectTime", "选主公的秒数"),
                     ("GodChessNumber", "神将数量"),
                     ("GeneralDamageIncreaseRound", "从第几回合开始伤害递增")):
        if k in g:
            A("| `%s` | `%s` | %s |" % (k, g[k], label))
    # 这两条也出现在 ChessGlobalConf 里, 但真值在 ChessModeSeasonConf 本赛季那一行
    #   (见下面势力权重那段说明), 所以取那边。
    for k, label in (("MinionNumCurSeason", "本季场上势力数"),
                     ("MinionMustCurSeason", "本季必选势力数")):
        v = (d.get("seasonRules") or {}).get(k)
        if v is None:
            v = g.get(k)
        if v is not None:
            A("| `%s` | `%s` | %s（取自本赛季行） |" % (k, v, label))
    A("")
    if "MaxGeneralDamageConf" in g:
        A("存活人数 → 单回合最多掉血（`MaxGeneralDamageConf`）：")
        A("")
        for it in g["MaxGeneralDamageConf"]:
            A("- 存活 %s 人时，最多掉 %s 血" % (it.get("AliveUser"), it.get("MaxDamage")))
        A("")

    # 势力表: 配置里【只有 ID 没有名字】, 所以用"该势力有哪些棋子"代替名字。
    #   这是本文件里唯一一处"把原始数据摆出来让用户自己判断"的地方 —— 因为任何
    #   "1 就是魏"的说法都是我的推断, 而棋子名是配置里写着的。
    ids = sorted(set(list(d.get("minionNames") or {}) + list(d.get("minionWeights") or {})),
                 key=lambda x: (int(x) if x.isdigit() else 999, x))
    if ids:
        A("### 本季势力（ID → 棋子样本）")
        A("")
        A("配置里**没有**势力 ID → 名字的对照表，只有一串数字，所以这里把该势力在本季")
        A("池子里的棋子列出来当证据（看到\"曹休/夏侯惇/邓艾\"自然知道 1 是魏，不用我替")
        A("配置表下结论）：")
        A("")
        A("| 势力 ID | 本季种数 | 权重 | 该势力的棋子 |")
        A("|---|---|---|---|")
        for i in ids:
            names = (d.get("minionNames") or {}).get(i) or []
            sample = "、".join(names[:8]) + ("…" if len(names) > 8 else "")
            w = (d.get("minionWeights") or {}).get(i)
            A("| %s | %d | %s | %s |" % (i, len(names), w if w is not None else "—", sample or "—"))
        A("")
        if d.get("minionWeightSource") == "ChessModeSeasonConf":
            A("> 权重取自 `ChessModeSeasonConf` 本赛季那一行。`ChessGlobalConf` 里也有个同名"
              "字段，")
            A("> 但那份是**旧赛季残留**：同一个 s%d，global 里只有 %d 个势力，本赛季行里有 %d 个。"
              % (d["season"], len(d.get("globalMinionWeights") or {}), len(d.get("minionWeights") or {})))
        else:
            A("> ⚠ 本赛季行里没找到势力权重，回退用了 `ChessGlobalConf` —— 那份可能带着旧赛季的"
              "残留，")
            A("> 条数会偏少。拿这些数字下判断之前先确认一下表结构。")
        A(">")
        A("> 表里没给权重的势力，说明它不在本季那份权重列表里（原因未验证，别当结论用）；")
        A("> 它在池子里有牌，只说明这些牌存在，不代表本季能选到这个势力。")
        A("")

    A("## 五、这些数字能拿来干什么 / 不能干什么")
    A("")
    A("**能用：**")
    A("")
    A("- \"我这等级刷到 5 费要几刷\" —— 表三直接查，不用凭感觉。")
    A("- \"该不该升等级再刷\" —— 对比不同等级行的期望刷新次数（刷一次 %d 金，次数就等于金币；"
      "升等级还要另花 `UpgradeCost`，一起算进去）。" % d["shopRefreshCost"])
    A("- \"3 费想要的牌还差 1 张，够不够金\" —— 表三 + 你的金币。")
    A("- \"这个等级能刷出哪些 4 费牌、每种几张\" —— 表二 + `ChessConf` 的棋子名单。")
    A("")
    A("**不能用（这一条最重要）：**")
    A("")
    A("- **算不出\"池里还剩几张\"。** 卡池是 8 家共享的，别人买走多少、遣散回多少，")
    A("  客户端拿不到——发牌在服务器做，回包只给你自己那几格牌（见 `core/pool_probe.py` 的取证）。")
    A("  所以上面所有数字都是\"**在没人买过的池子里**\"的概率，是**上界**。实战中只会更低。")
    A("  人数减少后会不会回升（淘汰者的牌退回池里）—— **未验证**，各家自走棋在这点上做法不同，")
    A("  别当成已知条件用。")
    A("- 所以别说\"还有 X 张\"，只能说\"这局**已见** X 张\"。")
    A("")
    A("## 六、同一份配置里还能拿到什么")
    A("")
    A("原始的 `TavernChessConfig.json` 有 %d 张表、共 %d 条（换赛季会自动变）。"
      % (len(d["tableCounts"]), sum(d["tableCounts"].values())))
    A("跟自走棋规则有关、对做助手有价值的，按价值排：")
    A("")
    A("| 表 | 条数 | 里面是什么 | 能拿来干什么 |")
    A("|---|---|---|---|")
    for name, what, use in OTHER_TABLES:
        n = d["tableCounts"].get(name)
        if name == "ChessModeSeasonConf" and d.get("seasonName"):
            # 赛季名从快照里读, 不写死 —— 否则换赛季后文档里的例子就成了旧赛季。
            what = "%s（本季快照里是 `%s`）" % (what, d["seasonName"])
        A("| `%s` | %s | %s | %s |" % (name, n if n is not None else "—", what, use))
    A("")
    A("> 前四节讲的 `ChessShopConf`（发牌概率）、`ChessRankConf`（池内张数）、")
    A("> `ChessGlobalConf`（全局规则）就是这批表里最直接的三张。")
    A("")
    A("想把别的一起看：原件就在 `data/season/s%d/TavernChessConfig.json`，按表名搜就行。" % d["season"])
    A("换赛季后重新抓：`python tools/season_config.py fetch`（自动下载 → 解压 → 解密 → 覆盖快照）。")
    A("")
    A("## 七、自洽性校验")
    A("")
    for name, ok, why in validate(d):
        A("- %s %s%s" % ("✅" if ok else "❌", name, ("　← " + why) if why else ""))
    A("")
    return "\n".join(P)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=10)
    ap.add_argument("--check", action="store_true", help="只校验, 不写文件")
    args = ap.parse_args()

    cfg, jp = load(args.season)
    d = build(cfg, args.season)

    bad = [x for x in validate(d) if not x[1]]
    for name, ok, why in validate(d):
        print(("  OK  " if ok else "  !!  ") + name + (("   " + why) if why else ""))
    if bad:
        print("\n校验没过, 不写文件(数字先修对, 免得导出一份看着像真的错表)")
        return 1
    if args.check:
        print("\n--check: 只校验, 没写文件")
        return 0

    os.makedirs(OUT_DIR, exist_ok=True)
    md = os.path.join(OUT_DIR, "发牌概率与卡池_s%d.md" % args.season)
    js = os.path.join(OUT_DIR, "发牌概率与卡池_s%d.json" % args.season)
    with open(md, "w", encoding="utf-8") as f:
        f.write(to_markdown(d))
    with open(js, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    print("\n已写出:")
    print("  " + md)
    print("  " + js + "   (程序可读, 给后续算概率的功能查表用)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
