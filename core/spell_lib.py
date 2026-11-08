# -*- coding: utf-8 -*-
"""49 锦囊 -> 可复用函数(步骤块)库。

基于《锦囊基线分类表.md》「二、逐锦囊详析」与「三、Phase 2 落地建议」翻译为
step-op 步块。每个锦囊 = 一个可直接 callScenario 调用的命名函数。

统一标记 validate="需实机校验":
  · 效果触发依赖引擎 useSpell 对具体锦囊名的实现;
  · target 选择子(如 highestCostFriendly)与 gating 条件 type(如 enemyHighestAttack)
    的准确性, 需在真机跑一遍确认 —— 本文件给出的是「结构正确、参数待校准」的版本。

gating 设计(对应文档 Phase 2 建议):
  · 立即类: ifStart(spellStock) -> optional(handCount<20) -> useSpell / 否则 recycleSpell
  · 局势类: ifStart(spellStock) -> optional(<盘面条件>) -> useSpell / 否则 recycleSpell
  · 动态类(天降神兵): ifStart(spellStock) -> optional(equipNonPreserved) -> useSpell / 否则 recycleSpell
  · 全局守卫(handCap=20 / 鸩毒 cnt>7) 已并入对应函数。

注意: 偷梁换柱(文档#10)已由 core/scenario_fns.build_default_registry 预置为基础函数+变体,
此处不再重复注册, 避免覆盖。其余 48 个锦囊在此生成(共 49 个锦囊全覆盖)。
"""
from __future__ import annotations

# 每条: n=锦囊名, c=类别(立即/局势/动态), t=阶, tg=useSpell 目标选择子(可空),
#       g=局势/动态 gate 条件 type(可空), gop/gv=条件比较(可空),
#       gn=gate 人话, d=效果摘要
SPELLS = [
    {"n": "一马当先", "c": "立即", "t": "四", "tg": "highestCostFriendly", "g": None, "gn": "进手即用", "d": "使1友方获冲阵,永久+3/+2"},
    {"n": "万箭齐发", "c": "立即", "t": "六", "tg": "allEnemies", "g": None, "gn": "进手即用(战斗前收益最大)", "d": "下场战斗开始对全体敌方10伤"},
    {"n": "三十六计", "c": "立即", "t": "五", "tg": "currentTavernSpell", "g": None, "gn": "进手即用", "d": "挑1张当前营帐等级锦囊牌"},
    {"n": "三顾茅庐", "c": "立即", "t": "三", "tg": "tavern5star", "g": None, "gn": "进手即用(想冲高费核心)", "d": "挑1张5级将,3轮后获得"},
    {"n": "乐不思蜀", "c": "局势", "t": "四", "tg": "highestAttackEnemy", "g": "enemyHighestAttack", "gn": "有强敌且其为最高攻时用", "d": "下场战斗使攻击最高敌方无法攻击"},
    {"n": "五谷丰登", "c": "立即", "t": "六", "tg": "allFriendly", "g": None, "gn": "进手即用(场上单位多时放大)", "d": "全体友方+4/+4"},
    {"n": "以逸待劳", "c": "立即", "t": "五", "tg": "friendlyTank", "g": None, "gn": "进手即用(别给主C)", "d": "1友方无法攻击+6/+6"},
    {"n": "借刀杀人", "c": "局势", "t": "六", "tg": "leftmostEnemy", "g": "enemyExists", "gn": "敌群存在时用", "d": "下场战斗对随机敌造成最左敌攻击力伤害"},
    {"n": "借尸还魂", "c": "局势", "t": "六", "tg": "recentEliminatedStrongest", "g": "recentEliminated", "gn": "有淘汰者时白嫖强将", "d": "挑最近淘汰玩家场上1将保留强化"},
    {"n": "先驱", "c": "立即", "t": "二", "tg": None, "g": None, "gn": "进手即用", "d": "+1虎符"},
    {"n": "兵贵神速", "c": "立即", "t": "六", "tg": None, "g": None, "gn": "进手即用(准备连刷前)", "d": "10s内刷新营帐不耗虎符"},
    {"n": "决斗", "c": "局势", "t": "三", "tg": "enemyCore", "g": "enemyExists", "gn": "战斗/集火用", "d": "友方攻击时对目标4伤(五阶2次)"},
    {"n": "南蛮入侵", "c": "立即", "t": "二", "tg": None, "g": None, "gn": "进手即用(越早越强)", "d": "本局营帐将永久+1/+1"},
    {"n": "增兵减灶", "c": "立即", "t": "四", "tg": "currentTavernGeneral", "g": None, "gn": "手牌持有即用(优先移除马)", "d": "挑当前营帐等级将,优先移除马;无马白得将"},
    {"n": "天降神兵", "c": "动态", "t": "五", "tg": "selfEquip", "g": None, "gn": "动态:有装备槽且非保留宝具时用,否则遣散", "d": "挑1新装备替换现有"},
    {"n": "屯粮", "c": "立即", "t": "二", "tg": "highRotateFriendly", "g": None, "gn": "进手即用(给高频上阵将)", "d": "1友方招募结束永久+1/+1"},
    {"n": "抛砖引玉", "c": "局势", "t": "四", "tg": "friendly", "g": "singleFactionBuild", "gn": "走单势力体系时用,否则遣散", "d": "选友方,营帐刷同势力将"},
    {"n": "援军", "c": "立即", "t": "三", "tg": "coreFactionFriendly", "g": None, "gn": "进手即用(优先核心势力)", "d": "得1张同势力随机将(五阶2张)"},
    {"n": "撒豆成兵", "c": "局势", "t": "五", "tg": None, "g": "hasEmptyField", "gn": "上阵区空位多时补场", "d": "随机将填满上阵区"},
    {"n": "擒贼擒王", "c": "局势", "t": "六", "tg": "highestHpEnemy", "g": "highHpEnemy", "gn": "有高血敌时削坦", "d": "下场战斗最高血敌血减半"},
    {"n": "斗转星移", "c": "局势", "t": "四", "tg": "weakestFriendly", "g": "weakUnitExists", "gn": "挑最弱转移给主C", "d": "选友方属性转移给最左友方后移除"},
    {"n": "无中生有", "c": "立即", "t": "三", "tg": None, "g": None, "gn": "进手即用", "d": "得2张1~2级将(五阶3~4级)"},
    {"n": "无懈可击", "c": "立即", "t": "六", "tg": "fragileCore", "g": None, "gn": "进手即用(给脆皮核心)", "d": "友方御策+4/+4"},
    {"n": "望梅止渴", "c": "立即", "t": "六", "tg": None, "g": None, "gn": "进手即用(需当下虎符)", "d": "+5虎符,下回合-3"},
    {"n": "树上开花", "c": "局势", "t": "五", "tg": None, "g": "heavyRefresh", "gn": "配合高频刷新时用(唯一)", "d": "本回合每刷新2次,下次免虎符"},
    {"n": "桃", "c": "立即", "t": "二", "tg": "lowHpCore", "g": None, "gn": "进手即用(给残血核心)", "d": "友方血+4(高阶+24)"},
    {"n": "桃园结义", "c": "立即", "t": "三", "tg": "allFriendly", "g": None, "gn": "团队残血时用", "d": "全体血+3"},
    {"n": "洞烛先机", "c": "立即", "t": "二", "tg": None, "g": None, "gn": "准备升级前用", "d": "营帐升级费-2"},
    {"n": "浑水摸鱼", "c": "立即", "t": "六", "tg": None, "g": None, "gn": "进手即用", "d": "随机得2张5级将/锦囊"},
    {"n": "火上浇油", "c": "立即", "t": "三", "tg": "attackFriendly", "g": None, "gn": "进手即用(给靠攻击将)", "d": "友方烈刃+1/+1"},
    {"n": "火攻", "c": "局势", "t": "四", "tg": "mainCarry", "g": "attackTriggerAlly", "gn": "依赖攻击触发,给主C", "d": "友方攻击时对2随机敌3伤(六阶3敌4伤)"},
    {"n": "瞒天过海", "c": "局势", "t": "六", "tg": None, "g": "recruitEndAlly", "gn": "招募结束将≥2时爆发", "d": "触发所有友方招募结束效果1次"},
    {"n": "破釜沉舟", "c": "局势", "t": "六", "tg": None, "g": "lordHighHp", "gn": "主公血厚时换buff(残血慎用)", "d": "主公-1血,友方+1/+1(主公每失2血+1)"},
    {"n": "破阵溃敌", "c": "立即", "t": "三", "tg": "frontlineFriendly", "g": None, "gn": "进手即用(给前排)", "d": "友方破军+2/+1"},
    {"n": "美人计", "c": "局势", "t": "五", "tg": "tavernMaleStrong", "g": "maleTavernValuable", "gn": "营帐有值钱男性将时用,否则遣散", "d": "选营帐男性将,得1张基础复制"},
    {"n": "联军盛宴", "c": "局势", "t": "六", "tg": None, "g": "goldAlly", "gn": "金将≥2时优先(纯金阵核心)", "d": "每金友方+2虎符"},
    {"n": "草船借箭", "c": "局势", "t": "五", "tg": "opponentStrongGeneral", "g": "opponentValuable", "gn": "对手有高价值将时用", "d": "挑上对手1将,得基础复制"},
    {"n": "调虎离山", "c": "局势", "t": "四", "tg": "boardFriendly", "g": "boardFriendlyExists", "gn": "回手工具(借道回手分支调用)", "d": "场上1友方置入手牌"},
    {"n": "过河拆桥", "c": "局势", "t": "三", "tg": "discardableUnit", "g": "discardableUnit", "gn": "有可弃废子时(别拆核心)", "d": "移除场上/营帐1将,全体+2/+1"},
    {"n": "远交近攻", "c": "立即", "t": "五", "tg": None, "g": None, "gn": "进手即用(净+5)", "d": "下回合+5虎符(其他玩家各+1)"},
    {"n": "酒", "c": "立即", "t": "二", "tg": "mainCarry", "g": None, "gn": "进手即用(给主C)", "d": "友方攻+4(高阶+24)"},
    {"n": "金蝉脱壳", "c": "局势", "t": "二", "tg": "tavernLowUnit", "g": "tavernLowUnit", "gn": "营帐有低阶废子时", "d": "营帐1将变随机+1级将+1/+1"},
    {"n": "釜底抽薪", "c": "局势", "t": "六", "tg": "factionChoice", "g": "lockPoolPlanned", "gn": "需锁池时用(唯一)", "d": "选势力,本回合不刷该势力"},
    {"n": "闪电", "c": "局势", "t": "五", "tg": "randomEnemy", "g": "endgameOrDense", "gn": "残局或敌密集时(唯一,看脸)", "d": "下场战斗50%对随机将999伤"},
    {"n": "随机应变", "c": "局势", "t": "六", "tg": None, "g": "justUsedStrongSpell", "gn": "刚用强锦囊后紧跟", "d": "用其他锦囊后,变上一张锦囊"},
    {"n": "顺手牵羊", "c": "立即", "t": "三", "tg": None, "g": None, "gn": "进手即用(白嫖)", "d": "随机得营帐1张牌"},
    {"n": "鸩毒", "c": "局势", "t": "六", "tg": "coreFriendly", "g": "spellCnt", "gop": "<=", "gv": 7, "gn": "手牌>7即遣散/使用", "d": "友方本回合鸩毒(上限7)"},
    {"n": "齐心并力", "c": "立即", "t": "三", "tg": "allFriendly", "g": None, "gn": "场上单位多时用", "d": "全体攻击+3"},
]


def _use(name, tg):
    s = {"op": "useSpell", "spell": name}
    if tg:
        s["target"] = tg
    return s


def _recycle(name):
    return {"op": "recycleSpell", "spell": name}


def _make_steps(sp):
    name = sp["n"]
    steps = [{"op": "ifStart", "cond": {"type": "spellStock", "spell": name}}]
    if sp["c"] == "动态":
        steps.append({"op": "optional",
                      "cond": {"type": "equipNonPreserved", "spell": name},
                      "body": [_use(name, sp["tg"])],
                      "else": [_recycle(name)]})
    elif sp["c"] == "局势" and sp.get("g"):
        cond = {"type": sp["g"], "spell": name}
        if sp.get("gop"):
            cond["op"] = sp["gop"]
            cond["value"] = sp["gv"]
        steps.append({"op": "optional", "cond": cond,
                      "body": [_use(name, sp["tg"])],
                      "else": [_recycle(name)]})
    else:  # 立即类: 仅手牌未满保护
        steps.append({"op": "optional",
                      "cond": {"type": "handCount", "op": "<", "value": 20},
                      "body": [_use(name, sp["tg"])],
                      "else": [_recycle(name)]})
    steps.append({"op": "ifEnd"})
    return steps


# 引擎 target 选择子 -> 中文标签(界面汉化用)
TGT_ZH = {
    "highestCostFriendly": "最高费用友方",
    "lowestLevelFriendly": "最低费用友方",
    "mainCarry": "主C/核心输出",
    "allFriendly": "全体友方",
    "fragileCore": "脆皮核心",
    "friendlyTank": "前排/坦克",
    "highestAttackEnemy": "最高攻击敌方",
    "highestHpEnemy": "最高血敌方",
    "lowestHpEnemy": "最低血敌方",
    "leftmostEnemy": "最左敌方",
    "randomEnemy": "随机敌方",
    "enemyCore": "敌方核心",
    "allEnemies": "全体敌方",
    "currentTavernSpell": "当前营帐锦囊",
    "tavern5star": "营帐5级将",
    "currentTavernGeneral": "营帐同级将",
    "coreFactionFriendly": "同势力友方",
    "highRotateFriendly": "高频上阵友方",
    "attackFriendly": "攻击型友方",
    "lowHpCore": "残血友方",
    "weakestFriendly": "最弱友方",
    "tavernMaleStrong": "营帐男性强将",
    "opponentStrongGeneral": "对手强将",
    "boardFriendly": "场上友方(回手)",
    "discardableUnit": "可弃废子",
    "tavernLowUnit": "营帐低阶废子",
    "factionChoice": "势力选择",
    "selfEquip": "自身装备",
    "recentEliminatedStrongest": "近期淘汰强将",
    "friendly": "友方(通用)",
}


def _derive_params(sp):
    """从 spell 自动推导可填参数(中文标签 + 默认值 + 候选)。

    · 有目标 -> 「目标」参数(下拉: 默认目标 + 几个常用候选), 覆盖 useSpell.target;
    · 局势/动态 -> 「出手条件」参数: 满足条件才用 / 强制使用 / 暂留·遣散。
    默认即当前步块里的取值, 引擎按默认值也能跑; 用户可在界面改, 错了随时纠正。"""
    params = []
    if sp.get("tg"):
        common = ["allFriendly", "mainCarry", "friendly", "highestCostFriendly"]
        opts = [sp["tg"]] + [t for t in common if t != sp["tg"]]
        params.append({
            "key": "target", "label": "目标", "default": sp["tg"],
            "options": opts, "options_zh": [TGT_ZH.get(t, t) for t in opts],
        })
    if sp["c"] in ("局势", "动态"):
        params.append({
            "key": "出手", "label": "出手条件", "default": "满足条件才用",
            "options": ["满足条件才用", "强制使用", "暂留/遣散"],
            "options_zh": ["满足条件才用", "强制使用", "暂留/遣散"],
        })
    return params


def build_spell_functions(reg):
    """把 48 个锦囊注册为可调用函数(偷梁换柱已由默认注册表预置, 不再重复)。

    返回实际成功注册的名字列表。任何单条失败都不影响其它。
    """
    added = []
    for sp in SPELLS:
        name = sp["n"]
        try:
            steps = _make_steps(sp)
            params = _derive_params(sp)
            meta = {
                "source": "spell_lib",
                "cat": sp["c"], "tier": sp["t"],
                "zh_cat": sp["c"], "zh_tier": sp["t"],
                "zh_effect": sp["d"], "zh_gate": sp.get("gn") or "",
                "desc": sp["d"], "gate": sp.get("gn") or "",
                "params": params,
                "validate": "需实机校验",
            }
            reg.register(name, steps, meta=meta)
            added.append(name)
        except Exception:
            pass
    reg._fn_loaded_spells = added
    return added


if __name__ == "__main__":
    from .scenario_fns import build_default_registry
    reg = build_default_registry()
    names = build_spell_functions(reg)
    print("锦囊函数已注册 %d 个:" % len(names))
    for i, n in enumerate(names):
        print("  %2d %s" % (i + 1, n))
