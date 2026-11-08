# -*- coding: utf-8 -*-
"""赛季配置表(TavernChessConfig.json)全表解析 → 生成一份能看的 md。

为什么要有这个工具(而不是"我手写一份文档"):
  TavernChessConfig.json 有 44 张表、4.26 MB, 而且【每个赛季都会变】(S10 是 50 个主公,
  下赛季可能是 52 个、字段还可能增减)。手写的文档一定会在换赛季那天变成假文档 ——
  到时候"参考"比没有还危险。所以: 讲解(人写的、慢变) + 字段总览(脚本生成、每次都准),
  两部分合成一份 md, 换赛季只要 `python tools/analyze_season_config.py --season s11`。

生成物: 参考_配置表/TavernChessConfig_{season}_解析.md
用法:
  python tools/analyze_season_config.py                 # 当前赛季
  python tools/analyze_season_config.py --season s10    # 指定赛季
"""
import io
import json
import os
import sys
from collections import OrderedDict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------------------------------------------------------------------------- 讲解部分
#   这一整段是【人写的】: 表的用途、谁在用、能拿它做什么、表里【没有】什么。
#   换赛季时这段基本不用动(变的是数量, 不是语义); 字段级的细节由下面的自动部分负责。
INTRO = """# 赛季配置表 TavernChessConfig.json 全解析({season})

> 自动生成: `python tools/analyze_season_config.py --season {season}`
> 数据源: `{path}`
> 大小: {size:.2f} MB, 顶层 {ntop} 张表 / {nrows} 条记录

---

## 一、这张表是什么

官方客户端用的策划表被导出成 JSON 后的合集, 由 **tabtoy**(`Tool` 字段写的就是它)从
`Config.sgs` 转出来的, 表结构就是策划表结构: 每张表一个 key, 值是记录数组, 字段名即策划字段名。

项目里它是靠 `tools/season_config.py` 抓下来并存到 `data/season/{season}/TavernChessConfig.json`,
之后所有需要"游戏规则数字"的地方都从这里读(`core/csv_loader.py: season_config()`), 不猜、不写死。
**这就是本项目的一条硬规矩**: 单价、概率、名单这类数字只能来自这张表或协议, 不许手写常量。

---

## 二、按用途分类(重点看这节)

### ① 经济与节奏(最容易被写死、也最该读这里的一类)

| 表 | 条数 | 关键字段 | 用途 |
|---|---|---|---|
| `ChessGlobalConf` | 1 | `ShopRefreshCost`(刷新费=1)、`ChessPrice`(买卡价=3)、`ChessSoldPrice`(卖价=1)、`BaseIncrement`(每回合基础收益)、`ProfitIncrement`(利息系数)、`MaxBattleTime`、`SkipBattleTime`、`MaxHandCard`、`GeneralSelectTime`、`MinionWeightCurSeason` | 全局限一张表定死: 刷新/买卖价、每回合收入、各阶段等待时长 |
| `ChessShopConf` | 6 | `ShopRank`、`UpgradeCost`(升级费)、`ShopColumn`(货架格数)、`RollingChance1..6`(各费用档出现概率)、`RollingChanceGod` | 升到几级要几金、几级几格、**每个等级刷出各费棋子的概率** |
| `ChessRankConf` | 6 | `Rank`、`StarShown`、`TotalCount`、`Resouce` | 费用档 ↔ 星级/规模口径(卡池规模的依据之一) |

> ★ 实机验证过的例子: 基础价 **买武将 3 虎符 / 刷新 1 虎符** 就是 `ChessPrice` / `ShopRefreshCost`。
> 拿了**丈八蛇矛**变成 买 2 / 刷 2, **刘焉**和部分主公技能再各减 1 —— 所以任何"按价算钱"的地方
> 都必须**优先读活体价**(协议推送的当前值), 读不到才回落到这张表的基准值。
> 项目里由 `core/refresh_advisor._cfg_costs()` + `core/csv_loader.shop_costs()` 统一提供, 三处口径一致。

### ② 棋子 / 主公 / 技能(对局决策的主体)

| 表 | 条数 | 用途 |
|---|---|---|
| `ChessConf` | {n_chess} | **棋子主表**: ID/名字/费用/阵营/羁绊/属性/技能挂点(全项目引用最多的表) |
| `ChessGeneralConf` | {n_lord} | **主公表**: `GeneralID`/`GeneralName` → `data/lords.json` 就是它生成的(修"复盘里主公ID不对"用的就是这张表) |
| `ChessSkillConf` | | 技能主表(被动/主动/触发参数) |
| `ChessGeneralSkillConf` | | 主公 ↔ 技能 的挂接 |
| `ChessSpellConf` | | **锦囊表**: 锦囊名/费用/目标类型/技能挂点 |
| `ChessSpellSkillConf` | | 锦囊 ↔ 技能 的挂接(840 个锦囊背后只有 331 条技能) |
| `ChessBuffConf` / `ChessBuffSizeChangeConf` / `ChessBuffAdditionConf` / `ChessBuffGetSkillConf` / `ChessBuffMinionShareConf` | | Buff 体系: 效果/随 Buff 量变化/附加/触发取技能/小兵共享 —— 做"伤害/收益预估"要看这几张 |
| `ChessBuffFollowUpConf` | 39 | **随征(FollowUp)**: `ChessID` + `BSCount` + `FollowUpAttrBuffID[]` —— "哪些棋子有随征、要多少 BS、给什么属性"。★项目里**还没有任何代码读它**(随征名单目前是手写常量), 见第六节 |

### ③ 装备 / 关键词 / 展示

| 表 | 用途 |
|---|---|
| `ChessWeaponConf`(335) / `ChessWeaponSkillConf`(116) / `ChessWeaponRankConf`(0) | 装备(武器)本体、武器带的技能、武器档位。★目前只有导出脚本在读, 助手没用它做"装备收益/该给谁"这类分析 |
| `ChessClientKeywordConf`(18) | 界面关键词(羁绊/词条)的中文解释 → 助手的悬浮提示可以照抄官方的措辞 |
| `ChessClientSkillConf`(171) / `ChessClientSoundEffectConf`(126) / `ChessEmoteConf`(20) / `ChessClientStoryConf`(4398) | 客户端展示用: 技能描述、音效、表情、剧情文案(与对局决策无关) |
| `ChessPropConf`(4) | 道具: 鲜花/菜叶/鸡腿这类"送礼物"道具 |

### ④ 赛季 / 段位 / 玩法 / 活动

| 表 | 用途 |
|---|---|
| `ChessModeSeasonConf`(10) | 赛季定义: `SeasonID`/`SeasonName`/起止时间/初始分 → **"这赛季是哪个赛季"的权威来源**(换赛季的判定看这里) |
| `ChessModeRankConf`(34) / `ChessRankingConf`(8) / `ChessRankRewardConf`(90) | 段位/榜单/段位奖励 |
| `ChessModeServerMaintenanceConf`(26) | 维护时间窗(判断"现在能不能打") |
| `ChessApexConf`(18) / `ChessApexTaskConf`(53) | 巅峰赛与任务 |
| `ChessActivityPassConf`(420) / `ChessActivityDuoConf`(2) / `ChessPrizewheelConf`(1) / `ChessPrizewheelPoolConf`(15) | 通行证/双人/转盘抽奖(与对局无关) |
| `ChessRookieGameCustomConf`(12) / `ChessRookieFunctionOpenConf`(12) / `ChessClientRookieFunctionDescConf`(23) | 新手引导 |
| `ChessSeasonGameplayConf`(1) | 赛季玩法总开关 |

---

## 三、这张表能做什么(可直接落地的)

1. **经济计算器**: `BaseIncrement` + `ProfitIncrement` + `UpgradeCost` → 算"这回合攒到多少、
   下回合能刷几次/升不升级更划算"。现在的决策器只看当前金币, 加上"未来两回合收入"才叫规划。
2. **发牌概率与追卡期望**: `ChessShopConf.RollingChance1..6` + 各费用档张数 → 算"几级刷最容易出这张、
   大概要刷几次"。这部分已经落地: `tools/export_pool_tables.py` → `参考_配置表/发牌概率与卡池_s10.md`。
3. **随征名单自动生成**: `ChessBuffFollowUpConf` 的 39 条就是全部随征棋子。现在助手用的是手写名单
   (漏一个就静默失效); 改成从这张表生成, 换赛季自动跟着变。
4. **羁绊/关键词提示**: `ChessClientKeywordConf` 的官方措辞 + `ChessConf` 的阵营字段 →
   悬浮显示"这个棋子吃哪些羁绊、现在场上几个人"。
5. **装备推荐**: `ChessWeaponConf` + `ChessWeaponSkillConf` → "这件装备给谁收益最高"。
6. **主公技能提示**: `ChessGeneralConf` + `ChessGeneralSkillConf` → 选主公前就知道技能是什么
   (助手的主公页已经在用这条链路)。
7. **维护/赛季判定**: `ChessModeServerMaintenanceConf` + `ChessModeSeasonConf` → 开局前提示
   "现在在维护"或"赛季已切换, 配置表要更新"。

---

## 四、怎么查(速查表)

| 我想知道 | 看哪张表 · 哪个字段 |
|---|---|
| 买一张武将多少虎符 / 刷一次多少 | `ChessGlobalConf.ChessPrice` / `.ShopRefreshCost` |
| 每回合基础给多少钱、利息怎么算 | `ChessGlobalConf.BaseIncrement` / `.ProfitIncrement` |
| 升到 5 级要几金、货架几格 | `ChessShopConf.UpgradeCost` / `.ShopColumn` |
| 5 级刷出 4 费的概率 | `ChessShopConf.RollingChance4`(按 `ShopRank=5` 那一行) |
| 某个武将几费、什么阵营、带什么技能 | `ChessConf`(按 `ChessID`) |
| 主公有哪些、ID 对应什么名字 | `ChessGeneralConf.GeneralID/GeneralName` |
| 某个锦囊几费、能不能指定目标 | `ChessSpellConf` |
| 哪些棋子有"随征" | `ChessBuffFollowUpConf.ChessID` |
| 某件装备带什么技能 | `ChessWeaponSkillConf` |
| 这个赛季怎么算分 / 什么时候结束 | `ChessModeSeasonConf` |
| 现在是不是维护时间 | `ChessModeServerMaintenanceConf` |

---

## 五、表里【没有】的东西(别在这张表上白费功夫)

* **公共卡池剩余张数**: 全项目搜过 `AllChessNum/ChessLeftNum/RemainChess/PoolNum/CardPool` 各 0 次;
  抽牌在服务器(`CMSG_CREQSHOPREFRESHCHESS` 只有一个"刷新"请求, 响应直接给货架)——
  所以"池里还剩 N 张"这句话在客户端**不成立**。能站得住的只有"全场已见 X 张"(己方+可见对手)。
* **整局最终名次**: 协议里那条 `emRus` 是**每场战斗的结果码**, 不是名次(见 `core/protocol.py`
  的源码依据)。助手现在的名次是用**淘汰顺序**推的。
* **皮肤 / 立绘 / 模型**: 本表只有 `ChessEmoteConf.ResourceName`(表情动画名)这类资源名,
  没有武将皮肤 ID 字段 —— 想换皮肤得走客户端资源替换(见 `参考_配置表/资源替换方案.md`)。
* **对手的手牌/意图**: 协议里只给"可见阵容", 不给手牌。

---

## 六、已知的"表里有、但项目还没用起来"的部分(改造清单)

| 表 | 现状 | 可以怎么用 |
|---|---|---|
| `ChessBuffFollowUpConf` | 0 处引用 | 随征名单改成从表生成(现在手写, 漏项 = 静默失效) |
| `ChessWeaponConf` / `ChessWeaponSkillConf` | 只有导出脚本引用 | 装备收益推荐 / "这装备该给谁" |
| `ChessBuffAdditionConf`(703)、`ChessBuffSizeChangeConf`(2240) | 未使用 | 伤害/成长预估(随 Buff 量的非线性变化全在这里) |
| `ChessClientSkillConf`(171) | 未使用 | 技能描述的官方文案(界面直接显示, 不用自己写) |
| `ChessRankConf.TotalCount` | 只在发牌概率导出里用过 | "这张卡全场共几张"的分母(如果口径确认为按档计数) |

"""
# ---------------------------------------------------------------------------- 自动部分

GROUPS = OrderedDict((
    ("经济与节奏", ("ChessGlobalConf", "ChessShopConf", "ChessRankConf")),
    ("棋子/主公/技能", ("ChessConf", "ChessGeneralConf", "ChessSkillConf",
                   "ChessGeneralSkillConf", "ChessSpellConf", "ChessSpellSkillConf",
                   "ChessBuffConf", "ChessBuffFollowUpConf", "ChessBuffSizeChangeConf",
                   "ChessBuffAdditionConf", "ChessBuffGetSkillConf", "ChessBuffMinionShareConf",
                   "ChessClientSkillConf")),
    ("装备/关键词/展示", ("ChessWeaponConf", "ChessWeaponSkillConf", "ChessWeaponRankConf",
                    "ChessClientKeywordConf", "ChessClientSoundEffectConf", "ChessEmoteConf",
                    "ChessClientStoryConf", "ChessPropConf", "ChessClientExpansionConf",
                    "ChessClientEnterTipsConf")),
    ("赛季/段位/玩法/活动", ("ChessModeSeasonConf", "ChessModeRankConf", "ChessRankingConf",
                     "ChessRankRewardConf", "ChessModeServerMaintenanceConf", "ChessApexConf",
                     "ChessApexTaskConf", "ChessActivityPassConf", "ChessActivityDuoConf",
                     "ChessPrizewheelConf", "ChessPrizewheelPoolConf", "ChessSeasonGameplayConf",
                     "ChessRookieGameCustomConf", "ChessRookieFunctionOpenConf",
                     "ChessClientRookieFunctionDescConf")),
))
MAX_FIELDS = 30
SAMPLE_LEN = 200


def _fields(rows):
    """按出现顺序收集字段名(去重)。"""
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        for k in r:
            if k not in out:
                out.append(k)
    return out


def _table_block(name, rows):
    lines = ["### `%s` — %d 条" % (name, len(rows))]
    if not rows:
        lines.append("")
        lines.append("_(空表: 官方这张表本期没有数据)_")
        lines.append("")
        return lines
    fs = _fields(rows)
    show = fs[:MAX_FIELDS]
    lines.append("")
    lines.append("字段(%d): %s%s" % (len(fs), "、".join("`%s`" % f for f in show),
                                 " …" if len(fs) > len(show) else ""))
    lines.append("")
    lines.append("样例:")
    lines.append("")
    lines.append("```json")
    for r in rows[:2]:
        s = json.dumps(r, ensure_ascii=False)
        lines.append(s[:SAMPLE_LEN] + (" …" if len(s) > SAMPLE_LEN else ""))
    lines.append("```")
    lines.append("")
    return lines


def build(cfg, season, path, size):
    nrows = 0
    for v in cfg.values():
        if isinstance(v, list):
            nrows += len(v)
    txt = INTRO.format(season=season, path=path.replace("\\", "/"), size=size,
                       ntop=sum(1 for v in cfg.values() if isinstance(v, list)) + 2,
                       nrows=nrows,
                       n_chess=len(cfg.get("ChessConf") or []),
                       n_lord=len(cfg.get("ChessGeneralConf") or []))
    txt += "\n---\n\n## 七、逐表字段总览(脚本生成, 换赛季自动更新)\n\n"
    txt += ("下面每张表给出: **条数 + 出现过的字段(去重, 按首次出现顺序) + 1~2 条真实样例**。\n"
            "样例是从 4 MB 的表里截的, 长字段会截断(以 `…` 结尾)。\n\n")
    done = set()
    for gname, tables in GROUPS.items():
        txt += "### 【%s】\n\n" % gname
        for t in tables:
            if t in cfg:
                txt += "\n".join(_table_block(t, cfg[t] or [])) + "\n"
                done.add(t)
    rest = [k for k, v in cfg.items() if isinstance(v, list) and k not in done]
    if rest:
        txt += "### 【其它表】\n\n"
        for t in rest:
            txt += "\n".join(_table_block(t, cfg[t] or [])) + "\n"
    meta = [(k, v) for k, v in cfg.items() if not isinstance(v, list)]
    if meta:
        txt += "### 【元信息】\n\n"
        for k, v in meta:
            txt += "* `%s` = `%s`\n" % (k, v)
    return txt


def main():
    season = None
    if "--season" in sys.argv:
        try:
            season = sys.argv[sys.argv.index("--season") + 1]
        except IndexError:
            print("--season 后面要跟赛季名(如 s10)")
            return 2
    from core import csv_loader
    cfg = csv_loader.season_config(season)
    if not cfg:
        print("读不到赛季配置表(season=%s) —— 先跑 tools/season_config.py 抓一次" % (season or "当前"))
        return 1
    path = ""
    for p in csv_loader._season_config_paths(season):   # 同一套路径解析, 避免两处口径
        if os.path.exists(p):
            path = p
            break
    size = os.path.getsize(path) / 1048576.0 if path else 0.0
    if not season:
        # 从路径里取赛季名(data/season/s10/...) —— 生成物要写进文件名, 名字必须对得上
        try:
            season = os.path.basename(os.path.dirname(path)) or "current"
        except Exception:
            season = "current"
    txt = build(cfg, season, path, size)
    out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "参考_配置表")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "TavernChessConfig_%s_解析.md" % season)
    with io.open(out, "w", encoding="utf-8") as f:
        f.write(txt)
    print("已生成: " + out)
    print("  赛季=%s 表数=%d 记录=%d 行数=%d" %
          (season, sum(1 for v in cfg.values() if isinstance(v, list)),
           sum(len(v) for v in cfg.values() if isinstance(v, list)), txt.count("\n")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
