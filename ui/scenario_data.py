# -*- coding: utf-8 -*-
"""剧本页用到的【常量】与【模块级小工具】 —— v4.109★ 从 ui/app.py 整体搬来。

app.py 原本一万多行, 剧本页占其中两大块:
  ① 本文件: App 类之前的常量与模块级函数(SCENARIO_OPS / SCENARIO_STEP_SCHEMA /
     SCENARIO_PRESETS 等 40+ 常量, 以及 _scenario_on_cn / _scenario_safe_int 等
     30+ 个下划线开头的小工具)
  ② ui/scenario_tab.py: App 类里那 60 个 _scenario_* 方法

app.py 顶层用 from ui.scenario_data import * 把名字照旧拿回去。
⚠ 因为下划线开头的名字不会被 import * 带出去, 所以本文件显式写了 __all__。

⚠ TIMING_ROWS / TIMING_ROWS_BY_KEY 也夹在这段里, 但它们其实是【节奏页】在用的,
   只是为了整段搬移省事才一起过来 —— app.py 靠 import * 仍能拿到, 行为不变。
"""
import os
import sys
import json
import re
import time
import threading
from collections import OrderedDict

# v4.109★ 从 app.py 搬过来时补上的: 这段里的 IDMAP 原先是靠 app.py 顶层那句
#   `from core import id_map as IDMAP` 拿到的, 单独成文件后必须自己 import ——
#   否则 _scenario_ids_for_names 会 NameError(dev/_verify_scenario.py 实测抓到的)。
from core import id_map as IDMAP
from core.config import DATA_DIR   # 全季锦囊表 data/tavern_spells.json 就在这个目录下

__all__ = [
    "SCENARIO_OPS",
    "SCENARIO_CHECK_PAIRS",
    "SCENARIO_CHECK_NUMERIC",
    "SCENARIO_CHECK_PAIRS_OPTIONAL",
    "SCENARIO_CHECK_CN",
    "SCENARIO_CMP_PAIRS",
    "SCENARIO_NORULE_PAIRS",
    "SCENARIO_VARAS_NOT_HAS",
    "SCENARIO_MODE_PAIRS",
    "_scenario_on_cn",
    "_scenario_on_index",
    "_scenario_post_pick_cn",
    "_scenario_plan_pick_cn",
    "_scenario_pick_kind_cn",
    "SCENARIO_CARD_KIND_CN",
    "SCENARIO_CAMP_IDS",
    "SCENARIO_CAMP_LABELS",
    "SCENARIO_CAMP_MEMBERS",
    "SCENARIO_CAMP_PAIRS",
    "_camp_cache",
    "_scenario_camp_members",
    "SCENARIO_WHERE_PAIRS",
    "SCENARIO_WHERE_CN",
    "SCENARIO_STAGE_PAIRS",
    "SCENARIO_TRIGGER_MODE_PAIRS",
    "_scenario_pair_to_value",
    "_scenario_value_to_pair",
    "_scenario_safe_int",
    "_scenario_timeout_cn",
    "_scenario_safe_float",
    "SCENARIO_SCOPE_PAIRS",
    "SCENARIO_PICK_KIND_PAIRS",
    "SCENARIO_PICK_PRESETS",
    "SCENARIO_PICK_HINT",
    "SCENARIO_ON_PAIRS",
    "SCENARIO_ON_HINT",
    "SCENARIO_PRIORITY_HINT",
    "TIMING_ROWS",
    "TIMING_ROWS_BY_KEY",
    "_scenario_cond_fields",
    "SCENARIO_VERSION_PAIRS",
    "SCENARIO_VERSION_HINT",
    "SCENARIO_STEP_SCHEMA",
    "SCENARIO_RETRY_FIELDS",
    "SCENARIO_NO_RETRY_OPS",
    "SCENARIO_NEWOP_HINT",
    "SCENARIO_FIELD_ALIASES",
    "_scenario_split_names",
    "_scenario_split_spell_plan",
    "_scenario_to_bool",
    # v4.151★ 「按卡分别定」(shopFind.cardPlan) 那张表的读写
    "SCENARIO_CARDPLAN_AFTER_PAIRS",
    "SCENARIO_CARDPLAN_AFTER_LABELS",
    "SCENARIO_CARDPLAN_AFTER_GOLD",
    "SCENARIO_CARDPLAN_NUM_LABELS",
    "_scenario_cardplan_after_norm",
    "_scenario_cardplan_after_label",
    "_scenario_cardplan_after_cn",
    "_scenario_split_cardplan",
    "_scenario_cardplan_text",
    "_scenario_cardplan_brief",
    "_scenario_val_set",
    "_scenario_show_when_ok",
    "_scenario_cond_visible",
    "_scenario_cond_brief",
    "SCENARIO_OP_LABELS",
    "SCENARIO_LABEL_OPS",
    "SCENARIO_POS_HINT",
    "_scenario_ids_for_names",
    "_expand_list_ids",
    "_scenario_spell_ids_for_names",
    "_is_slot_spec",
    "_scenario_pick_is_spec",
    "_scenario_cond_prefix",
    "_scenario_shopfind_target_names",
    "_scenario_shopfind_target_groups",
    "_scenario_fill_target_ids",
    "_scenario_spell_names",
    "_scenario_spell_base",
    "_scenario_spell_plan_text",
    "_SPELL_PLAN_EXAMPLE",
    "_SPELL_PLAN_ALL",
    "_SPELL_PLAN_N",
    "SCENARIO_PRESETS",
    "_scenario_parse_condgroup",
    "_scenario_condgroup_ok",
]

# v4.27★ 「剧本」tab 的步骤类型 (界面文字 → 下发给页面的 op)。
#   顺序 = 下拉框顺序; 括号式的循环/条件刻意做成"开始/结束"两个平铺步骤,
#   这样整张表就是可上下移动的扁平行, 不用做树形编辑器 (页面端按配对跳转)。
SCENARIO_OPS = [
    # ---- 武将 / 手牌 ----
    ("deploy",     "上阵(手牌里找武将)"),
    # v4.169★ 按【手牌序号】上阵(不看名字) —— 上游 EasyDeploy.js 的 lineupAt(index) 那条需求。
    #   换季初期 / 杂牌名字解析不出来时, 按名字那一步根本写不出来, 这一步绕开名字直接按位置。
    ("deployIndex", "上阵手牌第N张(不看名字)"),
    ("pick",       "选人(上阵效果选目标)"),
    ("pickOne",    "挑目标(武将/锦囊)"),
    ("recycle",    "遣散(手牌/阵上)"),
    # v4.81★ 遣散锦囊 —— 用户原话"遣散锦囊不可用, 用不了": 「遣散」是照【武将】找目标的,
    #   锦囊用的是另一套 spellID, 填锦囊名常常落空; 而且它不限"只找锦囊", 手牌里武将也在
    #   同一份列表里, 误命中就是主力被卖。这一步只认手牌里带 spellID 的卡, 目标留空=全清。
    ("recycleSpell", "遣散锦囊(手牌里的锦囊, 留空=全清)"),
    # v4.164★ 选装备 —— 把"避开某大类(武器/宝具/坐骑)/ 优先某件"做成【可独立配置的步骤】,
    #   每处装备三选一各配各的, 复用高; 全局版见剧本头部的「本局走五六剑」勾选 / equipPick。
    ("selectEquip", "选装备(避开大类/优先某件)"),
    # v4.68★ 合成(合金) —— 用户问"孙策张数大于等于3不会合金吗? 那你怎么算呢?"。
    #   两个要点: ① 游戏自己会三连合成, 但本脚本的 Tab 补丁会【阻止自动合成】, 所以要能主动发一次;
    #   ② 张数必须【普通/金分开算】—— 两者同名不同 ID(尾数 1 / 2), "2 普通 + 1 金"合不了。
    ("combine",    "合成(合金, 按同名同版本满3张)"),
    # v4.184★ 指定三连: 绕过全局三连开关, 直接对【指定卡】合金(斗转999 这类提效明显)
    ("combinespecific", "指定三连(绕过开关, 对指定卡合金)"),
    # v4.56★ 记数值快照 —— 给"技能自动作用(不弹选人框)"的场景用:
    #   用技能【之前】记一遍阵上每张卡的攻/血, 用完之后用条件「数值变化」看哪张变大了,
    #   就能反推这个技能作用到了谁(用户实测: 数值加完卡面攻/血会变)。
    ("snapStat",   "记数值快照(自动作用型技能用)"),
    # v4.64★ 随征 —— 把随征卡(薛灵芸/黄盖/马元义…)拖到指定的友方身上。
    #   为什么要单独做成一步: 用户的用法是"随征给阵上的魏国, 而且要【摊开】, 不能一直给同一个";
    #   以前只有 Shift+3 那个快捷键, 而它写死给【位置0】—— "均分"这件事它做不到。
    ("followUp",   "随征(把随征卡拖给指定友方)"),
    # v4.173★ 自动随征 —— 与「随征」一样把随征卡拖给友方, 但【不配目标也能跑】:
    #   留空就自动挑阵上第一个有棋子的位置(对应 mod 的"一键自动随征"), "给谁"可按名字/阵位号指定。
    #   识别层与「随征」共用 collectHandFollowUps, 所以认哪张随征卡完全一致。受「自动随征」总开关控制。
    ("autoFollowUp", "自动随征(一键: 自动识别手牌随征卡, 给谁可设)"),
    # v4.185★ 自动随征【后台引擎】的「开启 / 关闭」步骤: 剧本里可以单独开 / 关这个后台循环
    #   (不受引擎自身限制 —— 它们是引擎开关本身)。用法例: 招募阶段才开、进战斗前关掉;
    #   或某一段不许后台自动随征、那一段放开。状态看「快捷键」页勾选框。
    ("autoFollowUpOn",  "开启自动随征(后台)"),
    ("autoFollowUpOff", "关闭自动随征(后台)"),
    # ---- 商店 ----
    ("shopFind",   "牌库找卡(可刷新)"),
    ("buyCard",    "买指定卡(只看货架不刷新)"),
    ("refreshShop", "刷新商店(纯刷/烧虎符)"),
    ("levelUp",    "升级商店到N级"),
    ("lockShop",   "锁/解锁商店"),
    ("moneyPlan",  "按决策器执行(该刷就刷)"),
    # ---- 锦囊 ----
    ("useSpell",   "使用锦囊"),
    ("useAllSpells", "用尽锦囊(手里全部用掉)"),
    # v4.68★ 用尽锦囊·逐条 —— 每张锦囊【单独写用法】, 还能单独停用某几张。
    #   为什么另做一个而不改上面那个(用户原话: "这个用尽锦囊【不改】"):
    #     「用尽锦囊」是"一张张自动试目标", 它不知道每张该给谁 —— 而实际每张锦囊的目标都不同
    #     (瞒天过海·六 给阵上第1个 / 破釜沉舟·六 给手牌里的孙策 …), 自动试的结果就是"随机给一个"
    #     或者"所有目标都不行"。这个新步骤把控制权交回给人, 同时保留"一张张自动用"的效率。
    ("spellPlan",  "用尽锦囊·逐条(每张写用法, 可停用)"),
    # v4.80★ 用尽锦囊·按用法 —— 用户原话: "可以先看手牌有什么再去执行对应的"。
    #   与上面两步的区别讲清楚(三个并存, 老的两个一个字没动):
    #     「用尽锦囊」= 不认识任何一张, 每张都把目标挨个试过去(慢, 还可能给错目标);
    #     「逐条用锦囊」= 每张都由你写, 但手牌里【不在计划里】的锦囊一张不碰(用不完);
    #     「按用法」= 先扫手牌, 有写法的照写法一发命中, 没写的按你选的策略(自动试 / 留着不动)。
    ("spellSmart", "用尽锦囊·按用法(先扫手牌, 有用法的一发命中)"),
    # v4.186★ 自动锦囊【后台引擎】的「开启/关闭」步骤: 剧本里单独开/关后台自动用锦囊循环
    #   (不受引擎自身限制 —— 它们是引擎开关本身)。状态看「剧本」页勾选框 / 快捷键。
    ("autoSpellOn",   "开启自动锦囊(后台)"),
    ("autoSpellOff",  "关闭自动锦囊(后台)"),
    # v4.187★ 自动遣散【后台引擎】的「开启/关闭」步骤: 剧本里单独开/关后台自动遣散循环
    #   (不受引擎自身限制 —— 它们是引擎开关本身)。状态看「剧本」页勾选框 / 快捷键。
    ("autoDismissOn",   "开启自动遣散(后台)"),
    # ---- v4.188★ 选目标真空家族(上游 sgscodex v2.0.4 的 Alt+7 原理) ----
    #   原理: 选人包(ReqChessChooseSkillTarget)一成功服务端就结算了效果; 游戏随后会【自动】
    #   补发一条空上阵包(LineUpArr 恒 undefined)。把那条换成我方阵容 + 同刻本地取消选择窗,
    #   就得到"效果落地、消耗没落地"的虚空。
    #   「陈珪虚空」= 手牌陈珪 + 上阵区最右丁奉 的固定套路(键盘 Alt+7 同款);
    #   「选目标真空」= 通用版: 任意"当先选目标"的卡 + 任意目标(司马徽 21108111 这类走它);
    #   「解锁选择窗」= 游戏卡在选人/挑牌窗口时一键退出(评论区说的"回收后卡子卡住"就是这类);
    #   「结算探测」= 记账/对比: 做动作前后各按一次, 看金币/手牌/阵容到底谁变了。
    ("chenguiVoid", "陈珪虚空(Alt+7: 陈珪留场 + 丁奉回场 + 3虎符)"),
    ("skillVoid",   "选目标真空(通用: 任意当先选目标卡, 如司马徽)"),
    ("cancelSelect", "解锁选择窗(卡在选择框时用)"),
    ("probe",       "结算探测(记账/对比: 金币/手牌/阵容谁变了)"),
    ("autoDismissOff",  "关闭自动遣散(后台)"),
    # ---- 流程控制: 如果 / 否则 (满足做什么, 不满足做什么) ----
    ("ifStart",    "如果(条件成立就做这段)"),
    ("else",       "否则(条件不成立做这段)"),
    ("ifEnd",      "结束如果"),
    # ---- 流程控制: 循环 ----
    ("loopStart",  "循环开始(重复N次)"),
    ("break",      "跳出循环"),
    ("continue",   "跳过本次循环"),
    ("loopEnd",    "循环结束"),
    # v4.??★ 锚点 mark(L4) —— 给「失败跳转 onFailJump」定位用: 某步失败时不用数步号,
    #   直接跳到 named 锚点继续(见 L4)。引擎把它记成"当前下标", onFailJump 按名字找回。
    ("mark",       "锚点(失败跳转定位点)"),
    # ---- 等待 / 变量 / 时间 ----
    ("waitUntil",  "等到条件成立(带超时)"),
    # v4.66★ 等到某个阶段 —— 用户要的"等招募开始再干活"。
    #   以前只有 waitUntil + 把「判断」选成「阶段」, 三步的活; 现在一步到位、还能超时兜底。
    ("waitStage",  "等到阶段(招募/战斗)"),
    # v4.147★ 结束招募 —— 提前收工进战斗。
    #   能力其实早就有了: 页内 skipBattle() 按阶段分派(phase 7 → scene.onEndRecruitJump()),
    #   但助手端的 skip_battle 走的是【类名点击】(只认 JumpBtn/SkipBtn, 那是战斗阶段的按钮),
    #   两条路不一样 —— 所以"跳过战斗"按钮一直【管不到结束招募】。这里把它接成剧本步骤。
    #   ⚠ 别当成"跳过战斗"用: 它只在招募阶段生效, 且会【提前关掉买卡窗口】——
    #     剧本还没买完就放这一步, 后面该买的卡就买不到了。
    ("endRecruit", "结束招募(提前进战斗)"),
    # v4.176★ 跳过三连合成动画 —— 三连合成时游戏会播一段动画, 那期间 CanOperate=false /
    #   IsPlayCompositeAni=true, 剧本每一步和挑牌都要等它放完(一次三连白等好几秒)。
    #   这一步就是"立刻跳掉它"。
    #   ⚠ 与「禁用三连」不是一回事: 那个管【合不合】(囤卡), 这个管"已经合了, 那段动画等不等"。
    #   ★ 想【一直】生效, 别在剧本里每轮插一条 —— 用助手端「节奏」页那个勾选框, 下发一次就一直管。
    ("skipAni", "跳过三连动画"),
    # v4.176★ 禁用三连(囤卡) —— 与「跳过三连动画」不是一回事: 这个管【合不合】
    #   (陈珪那类: 买了先别自动合, 囤着等更好的); 那个管"已经合了, 那段动画等不等"。
    #   ★ 想【一直】生效, 用助手端「节奏」页那个相同开关即可(下发 window.blockTripleCombine);
    #     剧本里这一步更适合"临时切一下"(比如某轮前囤、某轮后放开)。
    #   op 键必须叫 holdNoCombine —— 桥接 _scOpNorm 只做小写化, 分派分支认的是 'holdnocombine'。
    ("holdNoCombine", "禁用三连(囤卡)"),
    ("wait",       "等待(毫秒)"),
    ("setVar",     "设置变量"),
    ("addVar",     "变量加减"),
    # v4.??★ 统计计数(L3) —— 把当前手牌/阵上每张武将按名字计数, 金/普通分开存进变量
    #   (cnt:孙策:normal / cnt:孙策:gold), 之后能在「如果」里按「变量 项数/数值」判抽齐没有。
    ("tallyGen",   "统计计数(每武将/金普通分开)"),
    # ---- 复用 / 收尾 / 提示 ----
    ("callScenario", "调用另一个剧本"),
    # v4.66★ 挑空并进入下一轮 —— 用户说的"999 那套收尾 4~5 步手拼"。
    #   一轮干完常见收尾 = 反复答掉待挑窗口 + 跳进下一轮, 以前要 pickOne + 循环判断 拼出来。
    ("drainNext",  "挑空并进入下一轮(收尾)"),
    ("abort",      "中止剧本"),
    ("toast",      "页内提示"),
    ("log",        "写日志"),
    # v4.101★ 移动已上阵武将的位置 —— 用户原话"现在只能上阵时选落点, 不能事后挪位置"。
    #   原理: 上阵协议 CReqChessLineUp 发的就是【整条 7 格阵容数组】(positionChess), 游戏拿它
    #   跟现状比(完全相同就静默返回), 所以"挪位置"= 构造一个【新顺序】的数组重发一次, 不用新接口。
    ("moveCard",   "移动已上阵武将(换位置)"),
    # v4.101★ 批量上阵 —— 手牌里的武将一次性全上(可按名单挑/排除, 也上限张数)。
    ("deployAll",  "批量上阵(手牌里全上/按名单)"),
    # v4.101★ 记录武将数据 —— 把某张卡此刻的攻/血读出来存进变量(之后能在条件里判)。
    ("recordStat", "记录武将数据(攻/血→变量)"),
    # v4.101★ 主动用主公技能 —— 底层早就有了(脚本里 lordSkillUse 发的就是 cmsg.CReqChessUseSkill,
    #   与游戏自身同源), 但一直【没接成剧本步骤】, 只能按快捷键。这里补上。
    ("lordSkill",  "用主公技能"),
    # v4.101★ 按数值重排站位 —— 底层早就有了(上阵包发的就是整条 7 格阵容数组),
    #   "把攻最高的挪到第 1 格" 就能一步做完, 不用自己算顺序再一步步挪。
    # v4.107★ 加【按名单摆位】: 直接写"1号位放谁、2号位放谁"(阵容页那 7 格站位就是这顺序)。
    ("arrange",    "摆位(按数值 / 按名单)"),
    # ---- v4.107★ 七个新步骤(用户点名要的) ----
    #   卖到只剩 N 张: 清手牌腾位。比「只保留」粗(不管名字只管张数), 但不用列名单 ——
    #   新赛季杂牌你不认识时, 列名单这活根本没法做。
    ("sellBelow",  "卖到只剩N张(清手牌)"),
    #   刷买直到金币 < N: 「等金币」的下半段 —— 攒够钱一口气刷买, 到线收手。
    ("buyUntilGold", "刷买直到金币<N"),
    #   对调阵上两格(原 benchSwap): 用户澄清 —— 阵上的卡【不能换下来】(要下场只能靠陈珪
    #   收回或遣散), 所以这一步只调【顺序】, 而且写格号就行(不用知道那格是谁)。
    ("swapSlots",  "对调阵上两格(只换顺序)"),
    ("pauseForUser", "暂停等你确认"),
    ("beep",       "响一声(提醒)"),
    ("useSpellIf", "条件用锦囊(够条件才用)"),
    #   记本局快照: 打个点(回合/等级/血量/金币/阵容进日志)。真正的战绩是对局结束时
    #   协议自动记的那条(带名次), 这一步不重复记 —— 界面 hint 里写明了。
    ("recordResult", "记本局快照(打点)"),
]

# ============================================================================
# v4.55★ 剧本步骤的【字段定义表】(schema) —— "每类步骤有自己的格式"
#
#   为什么要有这张表: 旧版编辑框是"固定几个通用输入框(目标/落点/次数毫秒/附加)",
#   每个步骤"哪个框填什么"全靠按钮旁边的注释和用户记忆 —— 用户原话是
#   "这个步骤有自己的格式, 现在都是这样有点乱, 也看不懂"。
#
#   现在每类步骤自己声明要用哪些字段, 编辑框照着声明动态生成:
#     · 用不上的字段【根本不显示】(不是藏起来), 所以不会出现"填了却不生效";
#     · 每个字段有自己的标签 + 一句话说明;
#     · 存出来的步骤 JSON 就是这些字段本身, 不再有 "scope=hand, keepGold=10"
#       这种塞在一个隐藏字符串里的写法(旧版所有值都挤进 extra, 所以看不出填到哪去了)。
#
#   type 决定控件:
#     text=文本框 · int=数字 · names=逗号分隔的名字(存成数组) · combo=下拉
#     bool=勾选框 · scenario=下拉选已有剧本 · stage=阶段下拉
#   free=True: combo 允许手动输入下拉里没有的值(如自定义落点 after:孙尚香)
#   showWhen=(依赖字段, 允许取值元组): 当前值不在元组里就【不显示】这一行
#   req=True: 保存时校验非空 —— 空值存下去只会变成一条永远不成立的步骤
# ============================================================================

# ---- 条件判定: 「判断什么」的可选项 (顺序 = 下拉顺序) ----
SCENARIO_CHECK_PAIRS = [
    ("手牌/场上有卡", "card"),
    ("金币", "gold"),
    ("回合", "turn"),
    ("商店等级", "level"),
    ("血量", "hp"),
    ("阶段(招募/战斗)", "stage"),
    ("变量", "var"),
    ("已运行秒数", "elapsed"),
    # v4.56★ 数值变化: 和「记数值快照」那一步比, 某张卡的攻/血变了多少。
    #   专门解决"技能自动作用、不弹选人框"时【没法指定目标】的问题 —— 只能事后反推作用到了谁。
    ("数值变化(卡面攻/血)", "stat"),
    # v4.67★ 局面维度: 以前的条件只能看【自己】(手牌/金币/回合/等级/血量), 想把
    #   "只剩 3 家就别再乱刷了, 把钱留着" 写成剧本根本写不出来 —— 只能在脑子里替脚本判。
    #   数据来自协议里的 otherPlayer(服务器下发的血量), 不是现读游戏内存猜字段名。
    #   读不到就判【不成立】并在日志里出声 —— "还剩几家"这种事绝不编一个数出来。
    ("存活家数", "alive"),
    # ★值必须【全小写】: 说明文字那几处会先把 check 做 .lower() 再查 SCENARIO_CHECK_CN /
    #   SCENARIO_CHECK_NUMERIC, 写成 foeHp 的话两边都对不上(实测过: 列表里会显示成 "foehp <= 20")
    ("对手最高血量", "foehp"),
    # v4.164★ 装备: 判"我身上装了什么 / 这一窗待选里有什么" —— 玉玺/吴六剑/七星灯这类规则的地基。
    #   大类(武器/宝具/坐骑)取自赛季配置 WeaponType, 装备名/ID 走离线装备表(data/equip_table.json)。
    ("身上装备(已装/待选)", "equip"),
]
# 哪些 check 是"数值比较"形态(需要 比较符 + 数值), 哪些是别的形态
#   v4.56★ stat 也算数值形态: 它同样要"比较符 + 数值"(比的是变化量, 0 = 只要变大了),
#   而且同样支持「条件取反」—— 加进这里, 那几个字段就会自动对它显示, 不用在界面上重复声明。
SCENARIO_CHECK_NUMERIC = ("gold", "turn", "level", "hp", "elapsed", "stat", "alive", "foehp")
# v4.66★ 循环的「提前跳出条件」用这一份: 第一项是"不提前跳出"。
#   为什么不用一个独立开关(勾选框): 开关控制不了下面那几行的显示(showWhen 只认一个依赖字段),
#   条件是"按 check 分形态"显示的 —— 让 check 自己带一个空选项, 就天然实现了"不填=不判",
#   而且老剧本(没有 check 这个键)照样按老行为走(见 _scenario_show_when_ok)。
SCENARIO_CHECK_PAIRS_OPTIONAL = [("不提前跳出(只按次数)", "")] + SCENARIO_CHECK_PAIRS
# 条件名 → 中文(步骤列表/预览里显示用)
SCENARIO_CHECK_CN = {"card": "手牌/场上有卡", "gold": "金币", "turn": "回合",
                     "level": "商店等级", "hp": "血量", "stage": "阶段",
                     "var": "变量", "elapsed": "已运行秒数", "stat": "数值变化",
                     "alive": "存活家数", "foehp": "对手最高血量",
                     "equip": "身上装备"}
SCENARIO_CMP_PAIRS = [("≥ 大于等于", ">="), ("≤ 小于等于", "<="), ("= 等于", "=="),
                      ("> 大于", ">"), ("< 小于", "<"), ("≠ 不等于", "!=")]
# v4.80★ 「用尽锦囊·按用法」里"手牌有这张、但用法表里没写"的锦囊怎么处理。
#   默认【自动轮目标】(和老「用尽锦囊」一个样, 保证手牌一定被用尽); 想"只按我写的用"就选留着不动。
SCENARIO_NORULE_PAIRS = [("自动轮目标试(和用尽锦囊一样)", "auto"), ("留着不动(只用我写了的)", "skip")]
# v4.68★ 「变量」的三种问法里"不是「里面有没有某个词」"的那几种 —— 「比较」「数值」两行只在
#   这些形态下显示(见条件字段的 showWhen)。
#   ⚠ 必须把"空 / 键缺失"也算进来: showWhen 是拿 str(cur) 比字面量的, 而老剧本【根本没有
#     varAs 这个键】(str(None) = "None")。漏掉它们 = 老剧本里「金币 ≥ 100」的「比较」那一行
#     会被吃掉 —— 又是一次"新增收窄把老场景改坏"的回归。
SCENARIO_VARAS_NOT_HAS = ("num", "count", "", "None", "none")
SCENARIO_MODE_PAIRS = [("全部都有", "all"), ("任意一个有", "any"),
                       ("全都没有", "none")]


# v4.65★ 「用锦囊 → 作用到」的存储值 → 人话(步骤列表 / 预览里显示用)。
#   存储值有两个来源: 下拉给的索引值(shop / shop-last / field-last / 1~6 …) 和用户手输的自由
#   文本(阵上3格 / field3 / 手牌2)。脚本端两套都认(见 sgs_bridge 的 _scSpellTarget),
#   这里只负责别让界面上显示成 "field-last第一张" 这种东西。
def _scenario_on_cn(v):
    s = str(v if v is not None else "").strip()
    low = s.lower()
    tail = "最后一张" if ("last" in low or "最后" in s) else "第一张"
    n = _scenario_on_index(s)
    if low in ("none", "no", "direct") or s in ("不选目标(直接使用)", "不选目标"):
        return "不选目标(直接使用)"
    if low.startswith("hand") or "手牌" in s:
        return ("手牌第%d张" % n) if n else ("手牌" + tail)
    if low.startswith("field") or "阵上" in s or "场上" in s:
        return ("阵上第%d格" % n) if n else ("阵上" + tail)
    return ("牌库第%d格" % n) if n else ("牌库" + tail)


def _scenario_on_index(s):
    """从『牌库3格 / 阵上第3格 / field3 / 3』里取出格号(1-based); 没有就返回 0。"""
    m = re.findall(r"[1-7]", str(s if s is not None else ""))
    return int(m[-1]) if m else 0

# v4.64★ 阵营名单 —— 现在【从配置表自动生成】, 不用再人工维护。
#
#   ⚠ 先纠正 v4.56 在这里写下的一个错误结论。当时写的是"游戏配置表里没有势力字段,
#     MinionType 是类型/职业不是势力(步练师 MinionType=2、陆逊=3, 可两人都是吴国人)",
#     于是名单一直靠手填 —— 而手填必然漏人: 实测吴只列了 21 个(表里 40 个)、
#     魏列了 22 个(表里 35 个), 按阵营清场/给随征都会漏掉一大半。
#
#     真正去数一遍 ChessConf.csv 的 MinionType 列就会发现它【就是势力】, 9 种取值和已知
#     势力一个反例都没有:
#         1=魏   2=蜀   3=吴   4=群   5=黄巾   6=汉   7=西凉   8=袁   11=神
#     最硬的证据是 MinionType=2 那一组: 魏延/黄忠/张飞/关羽/诸葛亮/刘禅/姜维/法正…
#     清一色蜀将, 混杂率为 0。另外用户要的「薛灵芸随征给魏国」—— 薛灵芸正好 MinionType=1。
#     (当年那句"步练师 MinionType=2"是看错了列: 步练师在 MinionType 列的值是 3。)
#
#   所以现在名单统一由 _scenario_camp_members() 现读配置表(赛季更新后自动跟着变),
#   下面这张手填表【只在读不到 CSV 时兜底】, 每边留几个代表名字 —— 免得没导入赛季数据时
#   筛选直接退化成"谁都匹配不上"。名单不硬编码进脚本: 下发时展开成 campNames 给页面端
#   (见 _scenario_fill_target_ids)。
SCENARIO_CAMP_IDS = {"魏": "1", "蜀": "2", "吴": "3", "群": "4", "黄巾": "5",
                     "汉": "6", "西凉": "7", "袁": "8", "神": "11"}
SCENARIO_CAMP_LABELS = {"魏": "魏国", "蜀": "蜀国", "吴": "吴国", "群": "群雄",
                        "黄巾": "黄巾", "汉": "汉室", "西凉": "西凉",
                        "袁": "袁氏", "神": "神将"}
SCENARIO_CAMP_MEMBERS = {
    "魏": ["司马懿", "张辽", "徐晃", "曹仁", "许褚", "典韦", "郭嘉", "荀彧", "甄姬",
           "薛灵芸", "钟会", "邓艾", "夏侯惇", "夏侯渊", "张郃", "程昱"],
    "蜀": ["刘备", "关羽", "张飞", "赵云", "诸葛亮", "黄忠", "魏延", "姜维", "庞统"],
    "吴": ["孙策", "周瑜", "孙尚香", "丁奉", "陆逊", "吕蒙", "甘宁", "太史慈", "黄盖",
           "大乔", "小乔", "周泰", "鲁肃"],
    "群": ["吕布", "貂蝉", "华佗", "贾诩", "陈珪", "庞德公", "蔡文姬", "于吉"],
    "黄巾": ["张宝", "张梁", "黄巾兵", "马元义", "张燕", "管亥"],
    "汉": ["何进", "皇甫嵩", "朱儁", "王允", "董承"],
    "西凉": ["马超", "马腾", "华雄", "董卓军", "李傕", "郭汜"],
    "袁": ["袁绍", "袁术", "颜良", "文丑", "纪灵"],
    "神": ["神张飞", "神马超", "神司马懿", "神陆逊"],
}
# 阵营下拉: 「不限」+ 9 个势力
SCENARIO_CAMP_PAIRS = [("不限(按名字点名)", "")] + [
    ("%s(%s势力全部)" % (SCENARIO_CAMP_LABELS[c], c), c) for c in SCENARIO_CAMP_IDS]

_camp_cache = {}


def _scenario_camp_members(camp):
    """某个阵营的全部棋子名(去重)。

    首选 ChessConf.csv 的 MinionType 列(理由见上面那段: 实测它就是势力), 读不到 CSV 才退回
    SCENARIO_CAMP_MEMBERS 那份兜底名单。

    为什么【不】顺手按 IsShop / 当前赛季过滤: 场上的"东吴水兵/刀斧手/近卫"这类衍生物也带本
    势力, 按阵营清场、给随征时它们同样该算进来 —— 过滤掉反而会漏(999-B「清多余吴将」清的就是
    游戏当先补出来的那些卡, 里面既有真武将也有兵种)。
    """
    camp = str(camp or "").strip()
    if not camp:
        return []
    if camp in _camp_cache:
        return _camp_cache[camp]
    want = SCENARIO_CAMP_IDS.get(camp)
    out = []
    if want:
        try:
            from core import csv_loader
            seen = set()
            for c in csv_loader.load_from_csv():
                if str((c or {}).get("minionType") or "").strip() != want:
                    continue
                nm = str((c or {}).get("name") or "").strip()
                if nm and nm not in seen:
                    seen.add(nm)
                    out.append(nm)
        except Exception:
            out = []
    if not out:
        out = list(SCENARIO_CAMP_MEMBERS.get(camp) or [])
    _camp_cache[camp] = out
    return out
SCENARIO_WHERE_PAIRS = [("手牌", "hand"), ("阵上", "field"), ("手牌或阵上", "both"),
                        ("全场已见(我+所有对手)", "all")]
# v4.67★ 位置词的中文只留这一处 —— 说明文字/表格列/条件短语都从这儿取,
#   免得加了「全场已见」之后有的地方认得它、有的地方把它当成手牌。
SCENARIO_WHERE_CN = {"hand": "手牌", "field": "阵上", "both": "手牌或阵上", "all": "全场已见"}
SCENARIO_STAGE_PAIRS = [("招募阶段", "招募"), ("战斗阶段", "战斗")]
# v4.55★ 触发方式: 同一个按键, 三种用法
#   按下=按一下就完整跑一遍(适合"一键完成操作")
#   松开=抬起来才跑(适合"按住预览、松手执行")
#   切换=按一下开始、再按一下停(适合"循环刷牌"这类需要手动叫停的剧本)
SCENARIO_TRIGGER_MODE_PAIRS = [("按下就跑一次", "press"),
                              ("松开才跑", "release"),
                              ("切换(按一下开始/再按停)", "toggle")]


def _scenario_pair_to_value(pairs, text, default=""):
    """下拉里显示的文本 → 存储值。也接受直接就是存储值(兼容老配置/手写)。"""
    t = str(text or "").strip()
    for lab, val in pairs:
        if lab == t:
            return val
    for lab, val in pairs:
        if val == t:
            return val
    return default or t


def _scenario_value_to_pair(pairs, value, default_label=""):
    """存储值 → 下拉显示文本(找不到就返回默认标签)。"""
    v = str(value or "").strip()
    for lab, val in pairs:
        if val == v:
            return lab
    return default_label


def _scenario_safe_int(v, default=0, lo=None, hi=None):
    """输入框里的数 → int。填了乱七八糟的东西就退回默认值(不报错、不留脏值)。"""
    try:
        n = int(float(str(v).strip()))
    except Exception:
        return default
    if lo is not None and n < lo:
        n = lo
    if hi is not None and n > hi:
        n = hi
    return n


def _scenario_timeout_cn(v, default_ms, brief=False):
    """「超时」那一栏 → 说明文字里的人话(brief=True 是表格列里那种短的)。

    v4.66★ 为什么要有这个函数: 以前这里写成 `s.get("timeoutMs") or 10000`, 而 0 在脚本端
      是【一直等】(_scOpWaitUntil: timeout > 0 才计时退出; waitStage 同样) —— 于是用户按
      「超时」栏的说明填 0, 说明栏却写"最多等 10000 毫秒", 与实际行为正好相反。
      这一栏是用户判断"会不会卡死"的唯一依据, 不能糊。
    """
    n = _scenario_safe_int(v, default_ms)
    if not n:
        return "一直等(0 = 无限等)" if brief else "一直等(填 0 = 无限等, 等不到就不会往下走)"
    return "最多等%dms" % n if brief else "最多等 %d 毫秒" % n


def _scenario_safe_float(v, default=1.0, lo=None, hi=None):
    try:
        f = float(str(v).strip())
    except Exception:
        return default
    if lo is not None and f < lo:
        f = lo
    if hi is not None and f > hi:
        f = hi
    return f

SCENARIO_SCOPE_PAIRS = [("手牌+阵上", "both"), ("只手牌", "hand"), ("只阵上", "field")]
SCENARIO_PICK_KIND_PAIRS = [("自动(按服务器在等的类型)", ""), ("挑武将", "chess"), ("挑锦囊", "spell")]

# v4.??★ 锦囊「后续选择」的类别 —— 用完锦囊弹出的窗可能是【选武将/选锦囊/选武器】三种,
#   所以比 pickOne 那一份多一项「武器/装备」(脚本端认 equip, 见 _scPendingPicks 的装备待选)。
#   ★ 注意别和上面那份混: 那份是"挑目标"步骤自己的类型, 这份是"用完之后再挑"的类型。
SCENARIO_POST_PICK_KIND_PAIRS = [
    ("自动(按服务器弹的那类)", ""),
    ("选武将", "chess"),
    ("选锦囊", "spell"),
    ("选武器/装备", "equip"),
]
# 「后续选择」两个框的说明 —— 四个锦囊步骤共用同一份文案, 免得四处漂移。
SCENARIO_POST_PICK_HINT = (
    "★ 有些锦囊【用完还要再选一次】—— 草船借箭要挑一个武将、珠联璧合要挑一张等级N的武将、 "
    "天降神兵要挑一件武器。这一步会自动把那个选择窗答掉(不然会一直挂着禁操作); "
    "这两个框就是指定【挑哪一个】, 不写 = 自动挑第一个。\n"
    "★ 挑哪张可以写: 名字(玉玺 / 孙策)或序号(1=第一个候选, 2=第二个…)。名字是包含匹配, "
    "写 瞒天过海 能匹配到任意品质; 想精确就写完整名字(瞒天过海·六)。\n"
    "★ 也可以写【优先级】 玉玺 > 七星灯: 前者不在候选里就用后者。\n"
    "★ 挑哪类一定要跟着窗口的实际类型填: 它要你选武器而你填了「选武将」, 就会按该类的第 1 个挑走。"
)


def _scenario_post_pick_fields(extra=""):
    """四个锦囊步骤共用的「后续选择」两行。

    做成函数而不是复制四份: 这四处的取值和说明必须完全一致, 复制过去必然漂移
    (项目里 "同一份东西两处写、后来一处改了" 踩过不止一次)。
    extra= 给「逐条/按用法」补一句"每行还能单独写「挑:」覆盖。
    """
    return [
        {"key": "postPickKind", "label": "用完还要选→挑哪类", "type": "combo",
         "values": SCENARIO_POST_PICK_KIND_PAIRS, "default": "",
         "hint": SCENARIO_POST_PICK_HINT + extra},
        {"key": "postPick", "label": "用完还要选→挑哪张", "type": "text",
         "hint": "留空 = 自动挑这个窗口里的第一个; 写名字(玉玺 / 孙策)、序号(1/2/3=第几个候选)、"
                 "优先级(玉玺 > 七星灯)都行 —— 写法的详细说明见上面「挑哪类」那一栏"},
    ]


# v4.??★ 「逐条用锦囊 / 按用法」里【每一行】还能单独指定后续选择 ——
#   因为一步计划里往往只有一两张锦囊是"用完还要选"的(草船借箭挑武将、天降神兵挑武器),
#   它们的挑法各不相同, 用步骤级那两个框(对所有卡片生效)就串台了。
#   写法: 作用在后面加一个「挑:」—— 例
#       草船借箭=牌库1 挑:武将:2      用完给牌库1, 然后挑候选里第 2 个武将
#       草船借箭 挑:武将:2            作用留空(自动轮目标), 只指定后续挑第 2 个武将
#       天降神兵 挑:玉玺              用完挑武器「玉玺」
#       草船借箭 挑:2                 类别不写 = 按服务器弹的那类, 挑第 2 个
#   类别可填 武将 / 锦囊 / 武器(不填=自动); 冒号后面是"挑哪张"(名字或序号, 同上面那个框)。
#   ★ 行里写了「挑:」就以行为准, 没写的行才用步骤级那两个框。
SCENARIO_PLAN_PICK_HINT = (
    "\n★ 用完还要再选一次的那张(草船借箭/天降神兵), 可以在【这一行末尾】单独写:"
    " 草船借箭=牌库1 挑:武将:2 (挑第 2 个武将)、天降神兵 挑:玉玺 (挑武器玉玺)。"
    "写成 挑:2 就是「按服务器弹的那类挑第 2 个」。这一行的覆盖下面两栏的设定。"
)
SCENARIO_PLAN_PICK_EXTRA = (
    "\n★【这一行级别】的写法见上面「每张锦囊的用法」里的说明: 在行末加 挑:武将:2 / 挑:玉玺。"
    "没写「挑:」的行才用这里这两个框。"
)

# v4.41★ 「挑目标 / 选人」那两个框是【可编辑下拉】: 内置下面这些写法, 用户点「+存为预设」
#   就能把自己常用的加进来(存进配置文件的 pick_presets, 下次开对话框还在)。
#   ★ 位置有讲究: 下面 SCENARIO_STEP_SCHEMA 会引用这两个常量, 所以必须定义在它【前面】,
#     定义在后面会让整个模块一 import 就 NameError(界面直接起不来)。
SCENARIO_PICK_PRESETS = ["auto", "1", "2", "3", "4", "5", "6", "7"]
SCENARIO_PICK_HINT = "auto=第一个 · 1..7=第几格 · 也可直接写武将名/锦囊名 (下拉可自建预设)"

# ============================================================================
# v4.69★ 「作用到」的候选 —— 从 useSpell 的 schema 里提出来, 让「挑选」按钮与下拉共用一份。
#
#   用户原话: "剧本里面使用锦囊, 没有那个挑里面那个可以选择? 上阵、遣散、使用, 也加上可以
#   挑选这个功能"。以前这个框虽然写着"可手输", 但阵上第 2~7 格、【某个武将】都要自己敲 ——
#   敲错了要等剧本跑到那一步才报"锦囊找不到作用目标"。
#   ★ 阵上1格~阵上7格 = 屏幕格号(与"阵上第一个"不同: 前者认第几格, 后者认第一个有卡的)。
# ============================================================================
SCENARIO_ON_PAIRS = [
    ("牌库第一张", "shop"), ("牌库最后一张", "shop-last"),
    ("牌库1格", "1"), ("牌库2格", "2"), ("牌库3格", "3"),
    ("牌库4格", "4"), ("牌库5格", "5"), ("牌库6格", "6"),
    ("手牌第一张", "hand"), ("手牌最后一张", "hand-last"),
    ("阵上第一个", "field"), ("阵上最后一个", "field-last"),
    ("阵上1格", "field1"), ("阵上2格", "field2"), ("阵上3格", "field3"),
    ("阵上4格", "field4"), ("阵上5格", "field5"), ("阵上6格", "field6"),
    ("阵上7格", "field7"),
    ("不选目标(直接使用)", "none"),
]
SCENARIO_ON_HINT = ("也能手输: 阵上3格 = 阵上第3格, 牌库4格, 手牌2 —— 都是屏幕格号。"
                    "「最后一张」指有卡的那一头(牌库/手牌/阵上通用)")

# v4.69★ 「优先级」写法 —— 挑目标/挑锦囊/用哪张锦囊都支持 A > B > C(见脚本 _scSplitAlts)。
#   用户原话: "挑目标和锦囊能不能有优先级, 如果有瞒天过海就选, 没有就往下选, 比如选联军盛宴"。
SCENARIO_PRIORITY_HINT = ("★ 优先级: 用 > 分隔, 从左到右依次尝试 —— 例: "
                          "瞒天过海·六 > 联军盛宴·五(有前者就用前者, 没有才用后者)")

# ==================================================================
# v4.85★ 「节奏」页 —— 脚本里那些【写死的延迟】逐项可改。
#   用户原话: "这个新开一个 python 端的 tab 页都可改? 且加上说明? 尤其是遣散武将"。
#
#   为什么要有这一页: 以前想让脚本跑快一点只有【剧本倍速】这一条路(剧本页顶部的「速度」),
#   而倍速是【无差别压缩】—— 它连"等服务器结算"的等待一起压掉, 于是日志里常见
#   「包发了但没生效」「发了但还在手牌」。真正的诉求是"哪一步慢就单独调哪一步"。
#
#   key 必须与脚本 SGS_TIMING_DEFAULT 的键一一对应(dev/_verify_scenario.py 会核对);
#   default 只用于【界面占位 + 恢复默认】, 真正生效的是下发的值 ——
#   【留空 = 不下发 = 脚本继续用自己的默认】(默认值与 v4.84 逐毫秒一致, 不动它就是老样子)。
# ==================================================================
TIMING_ROWS = [
    # ── 遣散(用户点名的那一项) ──
    ("recycleGap", "遣散武将每张之间", 200,
     "★ 就是你说的那一项。剧本「遣散」一次命中好几张时, 每遣散完一张等这么久再遣散下一张。"
     "以前写死 160 毫秒、界面上根本改不了 —— 现在【两处都能改】: 这里是全局默认, "
     "剧本「遣散」那一步上还有个「每张之间等多久」(步上填了就以步上的为准, 只影响那一步)。"
     "嫌一批遣散慢就调 80~100; 调到 0 = 连发, 但服务器来不及结算时日志会出现"
     "「包发了但那张还在」, 那一批就漏了。"),
    ("recycleSpellGap", "遣散锦囊每张之间", 120,
     "「遣散锦囊」那一步每张之间。它每张都会再扫一次手牌确认真的没了, 所以这里调太小"
     "容易报「包发了但它还在手牌」。"),
    # ── 轮询片 ──
    ("pollFast", "快轮询片", 100,
     "「等挑牌窗弹出来」「等那张卡真的离开手牌」这类每隔几毫秒看一眼的间隔。调小 = 判定更及时"
     "(也更费 CPU); 调大 = 省性能但可能错过窗口。"),
    ("pollMid", "中轮询片", 150,
     "「等到条件成立」「挑牌判空/等开窗」每一轮之间。剧本里 waitUntil 的判定频率就是它 —— "
     "调 80 会让条件成立得更及时。"),
    # ── 商店 ──
    ("shopVerifyMs", "刷新回包最多等", 30000,
     "「刷新商店」「牌库找卡」每次发请求后都必须等到 CRespShopRefreshChess 回包，才会继续下一次。"
     "超时会停止并报告，不会拿旧货架继续盲刷；极速和内部零等待也不能跳过这项网络确认。"
     "默认 30 秒以覆盖慢回包；填得太短可能提前停止，填 0 按默认值处理。"),
    ("shopPollMs", "刷新回包检查间隔", 25,
     "等待服务器刷新回包时检查的间隔；调小会略快发现回包，也会增加少量页面定时器。"
     "这是本地检查频率，不会额外向服务器发送请求。"),
    ("shopRefreshGapMs", "连续刷新之间最小间隔", 100,
     "牌库找卡或「刷新商店」连续发刷新请求时, 两次请求之间至少间隔这么久(真实时间, 不受剧本倍速影响)。"
     "默认 100 毫秒(参考丁奉循环的刷新节拍), 用来给游戏页面和服务端留出处理时间; 填 0 = 不限速。"
     "BURST 烧虎符模式不受此项影响。"),
    ("pollSlow", "慢退避片", 200,
     "服务器还没推进时的退避等待(挑牌指纹没变化、游戏在播动画)。调小会【更频繁地重发包】, "
     "反而更容易被服务器无视, 一般别动。"),
    # ── 手牌区刷新 ──
    ("handRefreshA", "发完包后第一次补刷手牌", 150,
     "任何发包(买/卖/遣散/随征)之后补刷一次手牌区的时机。调大 = 少重建一次列表(省性能), "
     "但界面上手牌更新得慢一点。"),
    ("handRefreshB", "发完包后第二次补刷手牌", 400,
     "紧跟着的第二次补刷(第一次常常太早, 数据还没回来)。填 0 等于只要第一次。"),
    ("handSchedA", "快捷键补刷手牌·第一拍", 200,
     "Shift+1/2/3/4 这些【快捷键】路径补刷手牌的三拍。"),
    ("handSchedB", "快捷键补刷手牌·第二拍", 600, "同上, 第二拍。"),
    ("handSchedC", "快捷键补刷手牌·第三拍", 1200, "同上, 第三拍(填 0 就只刷前两拍)。"),
    ("handFlushMs", "跑剧本时手牌多久补刷一次", 3000,
     "★ 跑剧本时【每一步】都重建手牌区是老行为 —— 20 步的剧本 = 60 次重建"
     "(refreshHandCardsOnly = 恢复隐藏节点 + 重建整个手牌列表 + 校正布局), 这才是"
     "「跑剧本时游戏页面卡」的主要来源, 比每张之间那几十毫秒大一个量级。"
     "非 0 = 剧本跑的过程中先不刷, 只在【收尾 / 按停】时补一次, 另外每隔这么久兜底刷一次"
     "(防剧本异常退出后手牌画面一直是旧的)。"
     "★ 剧本的判断读的是服务器数据不是界面节点, 所以画面滞后【不影响任何条件判定】。"
     "填 0 = 关掉这个优化, 回到每步都刷。"),
    ("sentinelMs", "手牌哨兵轮询间隔", 1500,
     "常驻的『手牌数量变了没』轮询。默认只【记录不刷 UI】(要自动刷得在控制台开 "
     "window.__sgsHandSentinel.autoRefresh=true), 所以调大基本没副作用。"),
    # ── 快捷键 / 停止 ──
    ("hkFollowGapMs", "Shift+3 连发间隔", 300,
     "勾了「随征: 一次全给同一个人」时, 每两张随征卡之间。以前写死下限 120ms, 现在填 0 = 连发。"
     "剧本里的「随征」不走这里(它用步骤上自己的『两张之间等多久』)。"),
    ("stopSliceMs", "停止检查粒度", 100,
     "点「停止」后脚本多快停下来: 所有等待被切成这么大的片, 每片检查一次停止标志。"
     "调 20 = 几乎立刻停; 调大只会让停止更迟钝, 不建议超过 100。"),
    # ── 步与步之间(用户点名的那一项) ──
    ("stepGap", "步与步之间", 0,
     "★ 就是你说的那一项。剧本每走完一步, 等这么久再走下一步。以前想让它慢一点只能往剧本里"
     "插「等待」步骤 —— 各剧本写了 300/400/500 都不一样, 也没有一个能统一改的地方, "
     "所以才显得『现在是个范围』(那其实是不同剧本各自写死的数, 不是这里能调的范围)。\n"
     "现在这一项全局说了算: 默认 80 毫秒(在你说的 50~100 之间); 填 0 = 一步接一步不停。"
     "判条件那几步(如果/否则/循环结束)不算在内, 不会白等。\n"
     "⚠ 调太小的症状: 服务器来不及结算, 日志出现「包发了但那张还在」。真要快, 先调这一项, "
     "再看剧本里有没有多余的「等待」步骤(那一步是按它自己写的毫秒等, 不受这一项管)。"),
    ("shopVerifyCapMs", "极速·刷新回包等待上限", 30000,
     "极速档下刷新回包的等待上限；仍必须等到服务器回包，超时就停止而不会继续盲刷。"),
]
TIMING_ROWS_BY_KEY = {r[0]: r for r in TIMING_ROWS}

# ==================================================================
# v4.134★ 【节奏档位】—— 一键把上面那十几项整组改掉。
#   为什么要有这个: 节奏页【逐项可改】是对的, 但"想快一点"的人根本不知道该动哪几项,
#   于是永远停留在默认值上(默认值是照"稳"调的)。档位是把"哪几项一起动、动到多少"
#   这个经验固化下来 —— 点一下就到位, 想再微调还能继续单独改某一项。
#
# ★ 极速档治的是什么(用户原话"到游戏页面有卡的地方" / "速度快一点"):
#     最大的单项不是"等服务器", 而是【手牌区被连续重建三遍】(handSchedA/B/C = 200/600/1200)。
#     refreshHandCardsOnly 是"恢复隐藏节点 + 重建整个手牌列表 + 校正布局", 一次就很贵,
#     每次快捷键操作后连做三次 —— 这就是既慢又卡的那一下。极速档只留第一拍(后两拍填 0),
#     一次操作省掉 1.8 秒的重建。手牌没刷出来时按 Alt+H 手动补一次即可。
#   ⚠ 极速档不是"压掉等服务器": pollSlow / recycleGap 只压到还没到"服务器来不及结算"的量级,
#     真出现「包发了但那张还在」就退回标准档 —— 快 0.3 秒不值得漏一张卡。
# ==================================================================
TIMING_PRESETS = {
    "turbo": {
        "recycleGap": 80, "recycleSpellGap": 80,
        "pollFast": 50, "pollMid": 80, "pollSlow": 140,
        "handRefreshA": 80, "handRefreshB": 0,
        "handSchedA": 100, "handSchedB": 0, "handSchedC": 0,
        # 极速档: 跑剧本时手牌 6 秒才兜底刷一次(收尾一定刷) —— 60 次重建 → 个位数
        "handFlushMs": 6000,
        "sentinelMs": 3000, "hkFollowGapMs": 150, "stopSliceMs": 40,
        "shopVerifyMs": 30000, "shopPollMs": 25, "shopVerifyCapMs": 30000,
        "shopRefreshGapMs": 100,
        "stepGap": 0,
    },
    # 标准 = 脚本默认值(逐毫秒一致), 点它是"回到没调过的样子"
    "standard": {r[0]: r[2] for r in TIMING_ROWS},
    "safe": {
        "recycleGap": 220, "recycleSpellGap": 160,
        "pollFast": 120, "pollMid": 200, "pollSlow": 300,
        "handRefreshA": 200, "handRefreshB": 500,
        "handSchedA": 300, "handSchedB": 800, "handSchedC": 1500,
        # 保守档: 手牌 1.5 秒兜底刷一次 —— 画面跟得紧, 但还是比"每步都刷"省得多
        "handFlushMs": 1500,
        "sentinelMs": 1500, "hkFollowGapMs": 350, "stopSliceMs": 100,
        "shopVerifyMs": 30000, "shopPollMs": 25, "shopVerifyCapMs": 30000,
        "shopRefreshGapMs": 100,
        "stepGap": 120,
    },
}


def _scenario_cond_fields(label_prefix="", values=None, check_default="card"):
    """条件判定的字段组 —— ifStart / waitUntil / loopStart(跳出条件) 共用同一套。

    「变量」形态要两个值(变量名 + 比较值), 所以比别的多一行 varName:
        check=var → 左边取 S.vars[varName], 右边取 target
    「阶段」比的是 招募/战斗, 没有"大于等于"这回事, 所以不写 cmp。
    values= 可以换掉「判断」那一行的选项 —— loopStart 用它加一项「不提前跳出」。
    check_default= 判断那一行的默认值。ifStart/waitUntil 必须是 "card"(老剧本没写 check
      = 判手牌, 界面上得显示成同一件事); loopStart 必须是 ""(空 = 不提前跳出) ——
      要是那里也默认 card, 一个普普通通的「循环3次」存一次就会变成
      "手牌/场上有卡就跳出循环", 等于凭空给老剧本加了个跳出条件。
    """
    return [
        {"key": "check", "label": label_prefix + "判断", "type": "combo",
         "values": values or SCENARIO_CHECK_PAIRS, "default": check_default,
         "hint": "选一个, 下面的输入框会自动换成对应的"},
        # --- 形态①: 手牌/场上有卡 ---
        {"key": "has", "label": "哪些卡", "type": "names", "req": True,
         "reqUnless": "count",        # v4.66★ 填了「数量」就可以留空(= 数总张数)
         "showWhen": ("check", ("card", "stat")),
         "hint": "卡名, 逗号分隔。可带前缀: 手牌:孙策 (只看手牌) · 阵上:孙尚香 (只看阵上) · "
                 "全场:孙策 (连对手阵容一起数) · "
                 "!丁奉 / -丁奉 (要求没有它) · 可叠加 阵上:!丁奉。混合写=同时满足。"
                 "填了「数量」时可以留空 —— 那就是数手牌/阵上的总张数(如 手牌≥8张)。"
                 "★ v4.101 站位: 写 格3:孙策 / 第3格:孙策 = 判【第 3 格】是不是孙策"
                 "(别名再同名、站错格也不算; 那一格是空的也不算)"},
        {"key": "mode", "label": "满足方式", "type": "combo", "values": SCENARIO_MODE_PAIRS,
         "showWhen": ("check", ("card",)),
         "hint": "带 ! 的那些项一律要求「不在」, 不受这里影响"},
        {"key": "where", "label": "在哪儿找", "type": "combo", "values": SCENARIO_WHERE_PAIRS,
         "showWhen": ("check", ("card",)),
         "hint": "没写 手牌:/阵上: 前缀的卡按这里找。"
                 "★「全场已见」= 我的手牌+阵上 再 + 所有对手阵容里出现过的。"
                 "卡池是公共的(你遣散回池、别人买走, 两头都看不见), 所以池里【还剩几张】"
                 "谁也算不出来 —— 这里给的是「至少见过几张」这个下界, 用来判「还追不追得动」"},
        # v4.56★ 判「金卡」: 金色不是"3星", 而是【另一个 chessID】(普通尾数1/金尾数2)
        {"key": "gold", "label": "卡的版本", "type": "combo", "default": "",
         "values": [("不限", ""), ("只要普通", "normal"), ("只要金色", "gold")],
         "showWhen": ("check", ("card",)),
         "hint": "用来判「阵上这张是不是金孙策」——金色牌是另一个ID(普通尾数1/金尾数2)"},
        # v4.??★ 按【类别】筛 —— 用户原话"还有如果判断, 没有锦囊?"。
        #   以前只有"按名字点名"这一条路, 而"手牌里有没有(任意一张)锦囊"写不出来 ——
        #   只能一个个锦囊名写否定(手牌:!顺手牵羊), 漏一张没抽到的就判错。
        #   现在: 「哪些卡」【留空】 + 这里选类别 = 判"有/没有这一类";
        #          「哪些卡」填了名字 + 这里选类别 = 点名那条再叠加一层类别限定。
        {"key": "kind", "label": "卡的类别", "type": "combo", "default": "",
         "values": [("不限", ""), ("只要锦囊", "spell"), ("只要武将", "chess"), ("只要武器/装备", "equip")],
         "showWhen": ("check", ("card",)),
         "hint": "★ 判断「没有锦囊」: 卡的类别选【只要锦囊】+ 满足方式选【全无】(「哪些卡」留空)。"
                 "  判「手牌还有锦囊」就选【任一有】。同理可判有没有武将、有没有武器。\n"
                 "★ 判据: 带 spellID = 锦囊; 带 equipmentID = 武器/装备; 其余(阵上/手牌的武将、随征卡) = 武将。\n"
                 "★ 配合「数量」还能数这一类有几张: 例 类别=只要锦囊 + 数量3 + ≥ = 手牌至少 3 张锦囊。\n"
                 "★ 点名下也能叠加: 哪些卡=孙策 + 类别=只要武将 = 只认武将位的孙策"},
        # v4.56★ 「数值变化」看的是攻还是血。比的是【变化量】而不是绝对值 ——
        #   因为技能加了多少事先不知道, 只能拿"用之前的快照"和"用之后的值"做差。
        {"key": "stat", "label": "看哪个数值", "type": "combo", "default": "atk",
         "values": [("攻击力", "atk"), ("血量", "hp")],
         "showWhen": ("check", ("stat",)),
         "hint": "拿现在的值 减 「记数值快照」那一步记的值: 下面的「数值」填 0 = 只要变大了就成立"},
        # --- 形态④: 装备(身上已装 / 这一窗待选) —— v4.164★ 用户规则的地基:
        #   · 已装含玉玺 → 不用天降神兵(它挑新装备替换现有的)→ 遣散锦囊
        #   · 已装吴六剑 → 装备三选一避开整个武器大类、优先玉玺/七星灯
        #   大类取自赛季配置 WeaponType(1武器 2宝具 3坐骑, 实锤), 装备名/ID 走离线装备表。
        {"key": "eqScope", "label": "看哪里", "type": "combo", "default": "own",
         "values": [("身上已装", "own"), ("当前待选", "cand"), ("两者都看", "both")],
         "showWhen": ("check", ("equip",)),
         "hint": "已装=身上装了什么(eq_own); 待选=这一窗三选一里有什么(eq_cand)。"
                 "两条规则都靠它: 已装含玉玺→不用天降神兵; 待选含玉玺→这窗就能直接挑它"},
        {"key": "eqNames", "label": "装备名(逗号分隔)", "type": "text",
         "showWhen": ("check", ("equip",)),
         "hint": "按名字命中(如 玉玺 或 吴六剑,七星灯)。名字查不到就用装备ID。留空 + 选了大类 = 只按大类判"},
        {"key": "eqType", "label": "或按大类", "type": "combo", "default": "",
         "values": [("不限", ""), ("武器", 1), ("宝具", 2), ("坐骑", 3)],
         "showWhen": ("check", ("equip",)),
         "hint": "不想写名字就按大类判: 1武器 2宝具 3坐骑(大类取自赛季配置 WeaponType, 实锤)"},
        # --- 形态②: 变量 ---
        {"key": "varName", "label": "变量名", "type": "text", "req": True,
         "showWhen": ("check", ("var",)), "hint": "用「设置变量」步骤建过的名字(如 v1)"},
        # v4.68★ 变量能存的不只是数了(见「设置变量」): 还能问"它有几项 / 里面有没有某个词"
        {"key": "varAs", "label": "问它的什么", "type": "combo", "default": "num",
         "omitIfDefault": True,
         "values": [("数值(比大小)", "num"),
                    ("有几项(列表 / 逗号文本)", "count"),
                    ("里面有没有某个词", "has")],
         "showWhen": ("check", ("var",)),
         "hint": "「设置变量」的值写 孙策,周瑜,孙尚香 这种就是【列表】——"
                 "选「有几项」判够不够3个(下面的「比较」+「数值」当张数用); "
                 "选「里面有没有某个词」判抽到孙策没(想判「没有」就勾「条件取反」)。"
                 "老剧本没这个字段 = 比数值, 一个字都不用改"},
        {"key": "varText", "label": "要找的词", "type": "text", "req": True,
         "showWhen": {"all": [("check", ("var",)), ("varAs", ("has",))]},
         "hint": "判「这个列表/这段文本里有没有它」——只要出现就算命中, 不用写全名; "
                 "想判【没有】就顺手勾上下面的「条件取反」"},
        # v4.66★ 数量阈值 —— 用户要的"攒够3张孙策 / 手牌≥8张"。
        #   填了它, 这个条件就从"有没有"变成"数张数再比大小":
        #     哪些卡=孙策 + 数量3 + ≥  → 攒够3张孙策
        #     哪些卡留空 + 数量8 + ≥ + 在哪儿找=只手牌 → 手牌≥8张
        #   留空/0 = 老行为(只看有没有), 老剧本完全不受影响。
        {"key": "count", "label": "数量(留空=只看有没有)", "type": "int", "default": 0,
         "omitIfDefault": True,       # 0/空 = 老行为, 不用写进 JSON
         "showWhen": ("check", ("card",)),
         "hint": "填了就改成【数张数】: 例 哪些卡=孙策 + 数量3 + ≥ = 攒够3张孙策; "
                 "哪些卡留空 + 数量8 + ≥ + 只手牌 = 手牌≥8张。"
                 "★有「手牌:/阵上:」前缀的卡按前缀找, 其余按「在哪儿找」。"
                 "★张数按【名字】数, 普通和金算在一起(2普通+1金 也会报 3 张) —— 但它们 ID 不同, "
                 "那 3 张【合不了金】。所以这条条件只能告诉你「够不够数」, 不能用来判「能不能合」; "
                 "真要合请用「合成」步骤, 它的日志按版本分开报"},
        # 「比较」要出现在【数值形态】和【数量形态】两种情况下, 而这两种形态由不同字段触发
        # → 用列表写法(=或), 加 "__set__"(填了就显示)。见 _scenario_show_when_ok
        # v4.68★ 再套一层「与」: 变量形态里选了「里面有没有某个词」时, 「比较」「数值」都要收起来
        #   —— has 是"命中即成立", 没有任何东西可比; 留着不隐藏, 用户会以为还得填个数值,
        #   而填了也不生效(本项目最忌讳的"看着生效其实没生效")。
        {"key": "cmp", "label": "比较", "type": "combo", "values": SCENARIO_CMP_PAIRS,
         "showWhen": {"all": [
             [("check", SCENARIO_CHECK_NUMERIC + ("var",)), ("count", ("__set__",))],
             ("varAs", SCENARIO_VARAS_NOT_HAS),
         ]}},
        # --- 数值: 三种形态共用一行「数值」(变量形态填要比较的数) ---
        {"key": "target", "label": "数值", "type": "text", "req": True,
         "showWhen": {"all": [("check", SCENARIO_CHECK_NUMERIC + ("var",)),
                              ("varAs", SCENARIO_VARAS_NOT_HAS)]},
         "hint": "已运行秒数=剧本开跑到现在过了几秒, 可用它做「等 N 秒后再说」。"
                 "存活家数=还活着的人头数(含自己), 对手最高血量=别人里血最多的那个 —— "
                 "想写「只剩3家就别乱刷了, 把钱留着保血」用这两个"},
        # --- 形态③: 阶段 ---
        {"key": "target", "label": "阶段", "type": "combo", "values": SCENARIO_STAGE_PAIRS,
         "req": True, "showWhen": ("check", ("stage",))},
        # --- v4.56★ 取反: 脚本端 _scCondNum 里早有 neg 判定, 但界面一直没有这个开关 →
        #     想写「金币不小于100」只能反着写 if/else 两段。现在勾一下就行。
        {"key": "negate", "label": "条件取反(反过来判)", "type": "bool", "default": False,
         # v4.68★ 把 varAs=has 也加进来: 「里面有没有某个词」想判【没有】只能靠这个开关,
         #   而它原来是靠 check/count 触发的 → has 形态下根本看不到取反, 那个需求就写不出来。
         "showWhen": [("check", SCENARIO_CHECK_NUMERIC + ("stage",)), ("count", ("__set__",)),
                      ("varAs", ("has",))],
         "hint": "勾上 = 结果反过来。如 金币<100 + 取反 = 金币不小于100; "
                 "数量形态下「不到N张」也能直接勾它; "
                 "变量形态选「里面有没有某个词」时, 勾它就变成「没有这个词」"},
    ]


# ============================================================================
# v4.68★ 卡牌版本(普通 / 金色)的下拉选项 —— 「遣散 / 上阵」共用。
#
# 为什么需要这个字段(用户问的): "遣散和上阵 不能选普通和金色吗?"
#   普通卡和金卡是【两个不同的 chessID】, 但【共用同一个名字】:
#        孙策  普通 21003211 /  金 21003212     ← 尾数 1=普通, 2=金
#   所以只按名字匹配一定会把两张一起命中:
#     · 遣散 → 想把"普通的都清掉、金的留着"根本写不出来, 一次全做掉;
#     · 上阵 → 手牌里两张同名, 拿到哪张完全取决于扫描顺序。
#   以前只有【条件】(如果/循环里填 gold=金/普通)认得出版本, 这两个步骤不认 —— 这一版补上。
#
# 留空 = 不限(老剧本一个字都不用改, 行为与之前完全一致)。
# ⚠ 尾数不是 1/2 的卡(部分锦囊、特殊卡)判不出金/普 —— 脚本端遇到这种卡会【判为不命中】,
#   并在报错里把"实际有哪些版本"列出来, 免得用户对着"没找到金孙策"猜半天。
# ============================================================================
SCENARIO_VERSION_PAIRS = [
    ("", "不限(普通的和金的一起算)"),
    ("normal", "只要普通(金的不要)"),
    ("gold", "只要金色"),
]
SCENARIO_VERSION_HINT = (
    "普通和金是【同名不同 ID】(尾数 1=普通、2=金), 只按名字找会把两张都命中。"
    "例: 想「把普通的孙策都遣散、留着金孙策」→ 这里选「只要普通」。"
    "⚠ 尾数不是 1/2 的卡判不出金/普, 会被当'不是你要的版本'跳过(报错里会列出实际有哪些版本)"
)


SCENARIO_STEP_SCHEMA = {
    # ================= 武将 / 手牌 =================
    "deploy": {
        "fields": [
            {"key": "target", "label": "武将名", "type": "names", "req": True,
             "pickFrom": "hero",
             "hint": "在手牌里找这张卡上阵; 多个用逗号(取第一个能上阵的)。点右边「挑选」从武将表里选"},
            # v4.68★ 版本(普通/金色) —— 手牌里同时有普通和金时, 不指定就"先扫到哪张算哪张"。
            #   上阵哪一张会直接影响合金结果, 所以这不是小事(见 SCENARIO_VERSION_PAIRS 的说明)。
            {"key": "version", "label": "卡的版本", "type": "combo",
             "values": SCENARIO_VERSION_PAIRS, "default": "",
             "hint": "手牌里普通和金都有时, 指定上哪一张。" + SCENARIO_VERSION_HINT},
            {"key": "pos", "label": "落点", "type": "combo", "free": True,
             "values": [("开位(最左)", "a"), ("第一个空位", "first"), ("倒数第二格", "default")]
                       + [("第%d格" % _i, str(_i)) for _i in range(1, 8)],
             "hint": "也可手写 after:孙尚香(右) / before:孙尚香(左) / middle:孙尚香(两张中间)"},
            {"key": "pick", "label": "上阵后选人", "type": "combo", "free": True,
             "pickFrom": "pick", "values": SCENARIO_PICK_PRESETS,
             "hint": SCENARIO_PICK_HINT + "。" + SCENARIO_PRIORITY_HINT},
            {"key": "skillId", "label": "指定技能ID", "type": "int",
             "hint": "留空=自动判断; 填了(如 330411)就强制按选人流程上阵"},
            # v4.64★ 补 optional —— 脚本端一直认这个字段, 但 schema 里漏了, 于是界面上看不到、
            #   编辑一次还会被抹掉。自检把 deploy/pickOne/useAllSpells 三处一起扫出来了。
            # v4.65★ 默认值 True → False —— 只补字段只解决了一半: 默认 True 时"勾着"就等于
            #   "没填", 存出来的 JSON 里压根没有 optional, 而脚本端把【键不存在】当"失败即中断"
            #   (见 _scRunOne 的 !s.optional), 于是界面上勾着「没这张卡就算了」实际却会停剧本;
            #   更坑的是预设里显式写好的 optional:true, 一编辑就被抹掉。默认 False 后:
            #   勾上=写出去=真的不中断, 不勾=失败即中断。与 shopFind 的既有写法(默认 False)统一。
            {"key": "optional", "label": "没这张卡就算了(不中断)", "type": "bool", "default": False},
            # v4.110★ 上阵的三段等待 —— 脚本端 v4.29 起就认 respMs/quietMs/maxQuietMs,
            #   但 schema 里一直没声明 → ① 界面上没得填; ② 在界面上编辑一次就被抹掉
            #   (和上面 optional、shopFind 的 buyAfterFull 踩的是同一个坑)。现在补上。
            {"key": "respMs", "label": "等上阵响应(毫秒)", "type": "int", "default": 900,
             "hint": "最多等服务器回「上阵成功」多久。★这是【上限不是固定等待】: 收到了立刻往下走, "
                     "平时只花响应到达的那点时间。调太小(<600)会误判成没收到 → 这张卡上不去; "
                     "只有上阵失败(战斗区满/被服务器拒)时才会真的等满这个数。没把握别动。"},
            {"key": "quietMs", "label": "上阵后安静多久(毫秒)", "type": "int", "default": 150,
             "hint": "上阵响应之后, 再等这么久没有新消息才走下一步 —— 当先/产出包就在这段到达。"
                     "★这是唯一真占时间的那段(原默认350, 现150)。\n"
                     "⚠ 调太小的代价: 邓茂这类「上阵有产出」的卡, 可能产出还没到就被下一步遣散了, "
                     "刷兵会变少。发现黄巾兵变少就调回 350。"},
            {"key": "maxQuietMs", "label": "安静最多等(毫秒)", "type": "int", "default": 1200,
             "hint": "兜底上限: 消息一直不停也不会卡死, 到点就走(正常很少触顶)。"},
        ],
    },
    # v4.169★ 按【手牌序号】上阵 —— 上游 EasyDeploy.js 的 lineupAt(index) 那条需求:
    #   不看名字, "手牌第 N 张, 上去"。以前只有【按名字找卡】的上阵, 而换季初期 / 杂牌
    #   名字解析不出来时(日志里名字退化成裸 ID), 按名字那一步根本写不出来 —— 这一步绕开名字。
    "deployIndex": {
        "fields": [
            {"key": "index", "label": "手牌第几张", "type": "int", "req": True, "default": 1,
             "hint": "1 = 手牌最左边第 1 张, 2 = 第 2 张 …; 填 -1 = 最后一张(最右), -2 = 倒数第二张。"
                     "★ 顺序就是游戏手牌区【从左到右】的顺序, 不用知道它叫什么名字"},
            {"key": "count", "label": "怎么数", "type": "combo",
             "values": [("只数武将(跳过锦囊)", "chess"), ("手牌全算(含锦囊)", "all")],
             "default": "chess",
             "hint": "默认【只数武将】: 锦囊混在手牌里而且不能上阵, 按原始下标数到一张锦囊上去只会白跑。"
                     "想严格按手牌下标数(和上游脚本一样)就选「全算」"},
            {"key": "pos", "label": "落点", "type": "combo", "free": True,
             "values": [("开位(最左)", "a"), ("第一个空位", "first"), ("倒数第二格", "default")]
                       + [("第%d格" % _i, str(_i)) for _i in range(1, 8)],
             "hint": "也可手写 after:孙尚香(右) / before:孙尚香(左) / middle:孙尚香(两张中间)"},
            {"key": "pick", "label": "上阵后选人", "type": "combo", "free": True,
             "pickFrom": "pick", "values": SCENARIO_PICK_PRESETS,
             "hint": SCENARIO_PICK_HINT + "。" + SCENARIO_PRIORITY_HINT},
            {"key": "skillId", "label": "指定技能ID", "type": "int",
             "hint": "留空=自动判断; 填了(如 330411)就强制按选人流程上阵"},
            {"key": "optional", "label": "这张上不了就算了(不中断)", "type": "bool", "default": False},
            # 这三段等待与「上阵」那一步完全同款(引擎照样认), 不声明就会变成"界面上看不到 +
            # 编辑一次被抹掉"的死字段 —— 见 v4.110 那条教训。
            {"key": "respMs", "label": "等上阵响应(毫秒)", "type": "int", "default": 900,
             "hint": "最多等服务器回「上阵成功」多久(上限, 不是固定等待)"},
            {"key": "quietMs", "label": "上阵后安静多久(毫秒)", "type": "int", "default": 150,
             "hint": "上阵响应后再等这么久没新消息才走下一步 —— 当先/产出包就在这段到达"},
            {"key": "maxQuietMs", "label": "安静最多等(毫秒)", "type": "int", "default": 1200,
             "hint": "兜底上限: 消息一直不停也不会卡死"},
        ],
    },
    "pick": {
        "fields": [
            {"key": "pick", "label": "选谁", "type": "combo", "free": True,
             "pickFrom": "pick", "values": SCENARIO_PICK_PRESETS,
             "hint": SCENARIO_PICK_HINT + "。" + SCENARIO_PRIORITY_HINT},
            {"key": "waitMs", "label": "等多久(毫秒)", "type": "int", "default": 1500,
             "hint": "等服务器弹选人窗的最长时间"},
        ],
    },
    "pickOne": {
        "fields": [
            {"key": "kind", "label": "挑哪类", "type": "combo", "values": SCENARIO_PICK_KIND_PAIRS,
             "hint": "留空=自动(按服务器此刻在等的类型判断)"},
            {"key": "pick", "label": "挑哪张", "type": "combo", "free": True,
             "pickFrom": "pickkind",
             "values": SCENARIO_PICK_PRESETS,
             "hint": "留空=自动挑第一张; 也可写武将名/锦囊名, 或 1..7 表示第几个候选。"
                     "锦囊带品质(如瞒天过海·六)时, 写瞒天过海会匹配任意品质; 想精确就写完整名字。"
                     "点右边「挑选」按上面「挑哪类」从对应的表里选(不挑类就武将锦囊都能挑)。"
                     + SCENARIO_PRIORITY_HINT},
            {"key": "afterMs", "label": "等多久(毫秒)", "type": "int", "default": 250,
             "hint": "等挑牌窗弹出来的时间"},
            # v4.64★ 补 optional(同上; 挑不到窗时它本来也只记 warn 不中断)
            # v4.65★ 默认 True → False, 理由同 deploy: 默认值不写出去 = 勾了也白勾
            {"key": "optional", "label": "没等到挑牌窗就算了(不中断)", "type": "bool", "default": False},
        ],
    },
    "recycle": {
        "fields": [
            {"key": "targets", "label": "遣散谁", "type": "names", "pickFrom": "hero",
             "hint": "卡名(多个用逗号), 或阵上序号: 3 / 第3格。点右边「挑选」从武将表里选。"
                     "⚠ 用下面「按阵营」时这里可以留空 —— 两者二选一, 填了阵营就只按阵营找"},
            # v4.64★ 补上「按阵营 / 排除名单」—— 这两项脚本端从 v4.56 起就支持(999-B / 999-E
            #   两个内置预设一直在用), 但 schema 里【一直没声明】, 于是有两个后果:
            #     ① 界面上看不到这两项, 用户不知道有这个功能;
            #     ② 更坑的是【编辑一次就把它抹掉】—— 编辑框只回写 schema 里声明过的字段,
            #        预设里写好的 camp/exclude 一保存就没了, 那条遣散步骤从此变成"什么都不遣"。
            #        与 v4.56 给 shopFind 补 optional 是同一类坑, 修法也一样。
            {"key": "camp", "label": "按阵营(整个阵营一起遣散)", "type": "combo",
             "values": SCENARIO_CAMP_PAIRS, "default": "",
             "hint": "选阵营就不用一个个点名 —— 名单是从【赛季配置表的势力列】现读的"
                     "(赛季更新后自动跟着变, 不用手工维护)。"
                     "阵营与上面的「遣散谁」二选一: 填了阵营就只按阵营找"},
            # v4.68★ 卡的版本(普通/金色) —— 用户原话"遣散和上阵 不能选普通和金色吗?"
            #   以前不能: 普通和金【同名不同 ID】, 按名字找会把两张一起遣散, 想"清普通的、留金的"
            #   写不出来。这个字段就是干这个的(留空 = 不限, 老剧本不受影响)。
            {"key": "version", "label": "卡的版本", "type": "combo",
             "values": SCENARIO_VERSION_PAIRS, "default": "",
             "hint": "例: 把普通的孙策都清掉、留金孙策 → 选「只要普通」。" + SCENARIO_VERSION_HINT},
            {"key": "exclude", "label": "但这几个不要碰", "type": "names", "pickFrom": "hero",
             "hint": "排除名单, 名字逗号分隔。无论按阵营还是点名, 命中这里的一律跳过。"
                     "例: 按阵营清吴将时, 把 孙策,周瑜,孙尚香 填这里 = 只清杂鱼、留主力"},
            {"key": "scope", "label": "从哪儿", "type": "combo", "values": SCENARIO_SCOPE_PAIRS,
             "default": "both"},
            # v4.??★ 数量上限 —— 用户原话: "能不能只遣散一个武将? 不全遣散?"。
            #   以前命中几张就全遣(和遣散锦囊那步不一致: 它早就有「最多遣散几张」)。
            #   ★【故意不写 default】: 写了默认值(哪怕 0), 编辑一次老步骤就会多出一行 maxCount:0,
            #     剧本往返自检会报警 —— 老剧本一个字没动却被改写了(这个坑和 gapMs 那条一样)。
            #     留空 = 不限 = 老行为。
            {"key": "maxCount", "label": "最多遣散几张", "type": "int",
             "hint": "留空或 0 = 命中的全都遣散(老行为); 填 1 = 只遣散一个。"
                     "★ 顺序是【先手牌、后阵上】: 命中多个时留下来的是靠后的那几个。"
                     "(和「遣散锦囊」那一步一个口径 —— 那边叫「最多遣散几张」)"},
            # v4.85★ 「每张之间等多久」—— 用户原话: "不要写死 160 啊要可改"。
            #   以前这一项【根本没有界面入口】: 一次命中好几张时, 每遣散完一张固定等 160 毫秒,
            #   想快只能去开剧本倍速(而倍速会把"等服务器结算"一起压掉 → 日志常见"包发了但没生效")。
            #   现在这一【步】自己就能定: 填了就用这个, 不填才用「节奏」页那个全局值(默认 160)。
            #   ⚠ 这里【故意不写 default】: 写了默认值, 编辑一次老步骤就会多出一行 gapMs:160,
            #     剧本往返自检立刻报警(老剧本一个字没动却被改写了)。留空 = 用「节奏」页的全局值,
            #     全局值默认就是 160 —— 与以前写死的值一样, 所以老剧本行为完全不变。
            {"key": "gapMs", "label": "每张之间等多久(毫秒)", "type": "int",
             "hint": "一次命中好几张时, 每遣散完一张等这么久再遣散下一张。"
                     "留空 = 用「节奏」页那个全局值(默认 160); 想让这一步更快就填 80~100, "
                     "填 0 = 连发不等(最快, 但服务器来不及结算时日志会出现「包发了但那张还在」, "
                     "那一批就会漏)。"
                     "★ 只影响【这一步】; 想让所有遣散都变快就去「节奏」页改「遣散武将每张之间」"
                     "—— 步上填了就以步上的为准。"},
            # v4.65★ 默认 True → False, 理由同 deploy: 默认值不写出去 = 勾了也白勾。
            #   注意这里和 shopFind 的默认 False 现在已经一致了(以前一个 True 一个 False,
            #   界面上一模一样的标签, 行为却相反)。
            {"key": "optional", "label": "找不到就算了(不中断)", "type": "bool", "default": False},
        ],
    },
    # ================= v4.81★ 遣散锦囊 =================
    #   用户原话: "遣散锦囊不可用, 用不了"。为什么非要另起一步(而不是把「遣散」改一改):
    #     ① 「遣散」是照【武将】找目标的 —— 锦囊用的是另一套 ID(spellID), 助手端按武将表
    #        反查不出 ID, 页面端只剩"按名字比对"一条路, 名字没解析出来就退化成裸 ID → 落空;
    #     ② 它不限"只找锦囊": 锦囊和武将同在【备战区同一份列表】里, 误命中就是主力被卖;
    #     ③ 它的目标不能留空(留空直接报"遣散步骤没写目标"), 而"手上的锦囊全清"正是最常见的需求。
    #   改旧步骤会让老剧本行为变化, 所以做成新步骤, 老的一步一个字没动。
    "recycleSpell": {
        "fields": [
            {"key": "targets", "label": "遣散哪几张锦囊", "type": "names", "pickFrom": "spell",
             "hint": "锦囊名(多个用逗号); 【留空 = 手牌里的锦囊全清】(最常见的用法)。"
                     "点右边「挑选」从本季锦囊表里选; 名字带不带间隔号都行"
                     "(顺手牵羊三 / 顺手牵羊·三)。"
                     "★ 只找【手牌】里的锦囊 —— 已经摆到阵上的锦囊不在手牌里, 这一步管不着"},
            {"key": "exclude", "label": "但这几张留着", "type": "names", "pickFrom": "spell",
             "hint": "想清掉大部分、只留几张时填这里(例: 留着 瞒天过海·六)。命中这里的一律不碰"},
            {"key": "maxCount", "label": "最多遣散几张", "type": "int", "default": 0,
             "hint": "留空或 0 = 有多少遣散多少; 填 2 = 只清两张(从手牌左边那张开始)"},
            {"key": "waitMs", "label": "每张等生效(毫秒)", "type": "int", "default": 700,
             "hint": "发完会再扫一次手牌确认那张真的没了; 到点还在手牌就记一笔"
                     "「包发了但它还在手牌」—— 那就是服务器不接受卖这张"},
            {"key": "optional", "label": "没得遣散就算了(不中断)", "type": "bool", "default": False,
             "hint": "勾上 = 手上没有锦囊 / 一张都没卖掉时只记一笔警告继续往下走"
                     "(默认不勾 = 报出来停下, 免得后面几步建立在「锦囊已清」这个假前提上)"},
        ],
    },
    # ================= v4.164★ 选装备 =================
    #   把"装备三选一避开某大类 / 优先某件"做成【可独立配置的步骤】(不是全局开关), 复用高:
    #   每处三选一窗口前放一步, 各自配"避开武器"或"优先玉玺/七星灯", 互不串台。
    #   这一步本身不弹窗、不等待 —— 它只设一个临时过滤器(主挑牌循环在装备窗弹出时用掉即清)。
    "selectEquip": {
        "fields": [
            {"key": "rejectType", "label": "避开哪个大类", "type": "combo", "default": "",
             "values": [("不限", ""), ("武器", 1), ("宝具", 2), ("坐骑", 3)],
             "hint": "装备三选一出现时, 这个大类一律不选(走五六剑就选「武器」)。"
                     "大类取自赛季配置 WeaponType: 1武器 2宝具 3坐骑(实锤)"},
            {"key": "preferNames", "label": "优先挑这些(名字逗号分隔)", "type": "text",
             "hint": "优先选这些装备(如 玉玺,七星灯)。不填 = 在非避开类里随便挑第一个。"
                     "名字/ID 走赛季装备表; 可空"},
        ],
    },
    # ================= v4.68★ 合成(合金) =================
    #   用户问: "孙策张数大于等于3不会合金吗? 那你怎么算呢?"
    #   答(也是这一步存在的理由):
    #     ① 游戏自己在攒满 3 张【同名同版本】时会自动合成, 但本脚本的 Tab 补丁会【阻止自动合成】
    #        (window.blockTripleCombine) —— 所以"有 3 张就自动合"不成立, 要主动发一次;
    #     ② 张数必须【普通 / 金 分开算】: 两者同名但不同 ID, 所以
    #          · 3 张普通 → 合 1 张金;
    #          · "2 普通 + 1 金" 合不了(金是另一档, 不参与这 3 张)。
    #        这一步的日志就是按这个口径报的(先报"哪几张能合几组", 后报合完少了几张)。
    #     ③ 走的是游戏自己的合成接口(独立引擎里已在用的 actCombine), 不手搓拖拽 ——
    #        星级和结果由官方接口处理, 比在剧本里模拟更安全。
    "combine": {
        "fields": [
            {"key": "waitMs", "label": "合成后等多久(毫秒)", "type": "int", "default": 800,
             "hint": "等服务器把合成结果同步回手牌; 太短会在日志里看到「合成后手牌没变」"},
            {"key": "optional", "label": "没得合就算了(不中断)", "type": "bool", "default": False,
             "hint": "勾上 = 凑不满 3 张时只记一笔警告、继续往下走(默认不勾 = 报出来停下)。"
                     "★这一步是【能合的都合】(游戏自己的合成接口), 不能只合其中一张 —— "
                     "想只合孙策, 就先「遣散」掉不想合的同类卡(凑不满 3 张自然合不了)"},
        ],
    },
    # v4.184★ 指定三连: 绕过 blockTripleCombine 全局开关, 直接对指定卡发合金。
    #   ids 直接给 goodsID 数组最稳; names 给卡名(可重复)由脚本在游戏内按手牌/商店/场上解析。
    "combinespecific": {
        "fields": [
            {"key": "ids", "label": "卡 goodsID 列表(逗号/数组)", "type": "text", "default": "",
             "hint": "直接写要合的卡的 goodsID, 如 111,222,333; 与 names 二选一, 写了 ids 优先"},
            {"key": "names", "label": "卡名(可重复, 逗号分隔)", "type": "text", "default": "",
             "hint": "如 孙策,孙策,左慈 —— 脚本在游戏内按名字找 goodsID 再合金; 不依赖全局三连开关"},
            {"key": "optional", "label": "找不到就算了(不中断)", "type": "bool", "default": False,
             "hint": "勾上 = 没解析到足够可合卡时只记警告继续; 默认不勾 = 报出来停下"},
        ],
    },
    # v4.56★ 记数值快照: 没有字段可填 —— 点一下就把当前阵上每张卡的攻/血记下来。
    #   必须放在【用技能之前】; 用完之后用条件「数值变化」看哪张变大了, 就知道技能作用到了谁。
    #   为什么要做成独立一步(而不是塞进"用锦囊"里): 触发数值变化的不一定是锦囊 ——
    #   上阵某张卡、遣散某张卡都可能间接触发, 独立一步才能覆盖所有情况。
    # v4.188★ 陈珪虚空（Alt+7 同款）—— 不用填卡：自动挑【手牌最右的陈珪】+【上阵区最右的丁奉】。
    "chenguiVoid": {
        "fields": [
            {"key": "waitMs", "label": "等丁奉回手(毫秒)", "type": "int", "default": 1800,
             "hint": "陈珪的当先把丁奉收回手牌要一会儿；超时没回手就判失败（不会瞎重发）"},
            {"key": "cancel", "label": "本地取消选择窗(虚空的关键)", "type": "bool", "default": True,
             "hint": "★这就是「虚空」的来源：服务端已经结算、客户端不确认。取消才叫陈珪留场；"
                     "取消掉勾选 = 走普通确认流程，陈珪会被移除（那就退化成 Alt+8 的前半段了）"},
            {"key": "optional", "label": "没这条件就算了(不中断)", "type": "bool", "default": False,
             "hint": "手牌没陈珪 / 上阵区最右不是丁奉 时：勾上=只记警告继续；不勾=报出来停下"},
        ],
    },
    # v4.188★ 选目标真空（通用）—— 陈珪虚空的一般化：任意"当先选目标"的卡都能试。
    #   典型: 司马徽(本季 21108111) 当先=把1个友方变化为等级+1的武将 → 目标留空、不勾重发即可。
    "skillVoid": {
        "fields": [
            {"key": "src", "label": "要上阵的卡", "type": "names", "req": True, "pickFrom": "hero",
             "hint": "手牌里这张卡必须是「当先选目标」型的（陈珪 / 丁奉 / 司马徽…）。"
                     "司马徽本季 21108111，当先=把1个友方变化为等级+1的武将；"
                     "丁奉 21103051，当先=移除1个友方获得3虎符"},
            {"key": "target", "label": "技能目标", "type": "text", "default": "",
             "hint": "留空 = 自动挑一张【非同名友方】；也可写 第3格 / gid:135000123 / 卡名"},
            {"key": "redeploy", "label": "目标被收走后重新上阵", "type": "bool", "default": False,
             "hint": "勾上 = 等目标回到手牌再把它重新上阵（丁奉这类「移除/回手」技能用）。"
                     "司马徽这种「变化等级」目标不离场，不要勾"},
            {"key": "waitMs", "label": "等目标回手(毫秒)", "type": "int", "default": 1800,
             "omitIfDefault": True},
            {"key": "cancel", "label": "本地取消选择窗(虚空的关键)", "type": "bool", "default": True,
             "hint": "不勾 = 只接管上阵包、照常确认（用来对照「虚空」到底差多少）"},
            {"key": "optional", "label": "没这张卡就算了(不中断)", "type": "bool", "default": False},
        ],
    },
    # v4.188★ 解锁选择窗：没有字段。卡在选人/挑牌窗口时插一步即可（找不到窗口不算失败）。
    "cancelSelect": {"fields": []},
    # v4.188★ 结算探测：按一次记账，再按一次出差值。
    "probe": {
        "fields": [
            {"key": "mode", "label": "模式", "type": "combo",
             "values": [("自动(有上次记账就出对比)", ""), ("只记账(不出对比)", "mark")],
             "default": ""},
        ],
    },
    "snapStat": {"fields": []},
    # ================= 商店 =================
    "shopFind": {
        "fields": [
            {"key": "target", "label": "找哪张卡", "type": "names", "req": True, "pickFrom": "hero",
             # v4.103★ 每个目标可以自己带张数: "孙策*3,陈珪*2" (也认 孙策:3 / 孙策x3)
             "hint": "在商店货架里找; 找不到会按下面「最多刷新」次数刷新。点右边「挑选」从武将表里选。\n"
                     "★每个将可以各写各的张数: 孙策*3,陈珪*2 —— 凑够 3 张孙策后不会停, "
                     "会继续刷继续买, 直到陈珪也凑够 2 张(所以「找齐」要勾上)。普通/金色同名按一个目标计数。\n"
                     "不写 *N 就用下面「每种最多买几张」那个统一的数"},
            {"key": "refreshCount", "label": "最多刷新(次)", "type": "int", "default": 3},
            {"key": "keepGold", "label": "保留金币", "type": "int", "default": 0,
             "hint": "手上低于这个数就不刷了"},
            {"key": "buy", "label": "找到就买下来", "type": "bool", "default": True},
            # v4.101★ 「每种最多买几张」+「找齐才算」—— 用户原话"孙策买3张写不出来"。
            #   以前: 货架上命中的【全部】买掉、买到任一张立刻返回 —— 于是
            #     ① 想"孙策买3张"只能外面套循环; ② 想"孙策+陈珪都买到才往下走"根本写不出来
            #       (买到孙策就返回了, 陈珪这轮没买到也照样算成功)。
            #   ⚠ 计数【按每个目标分别算】, 不是总数: 填 3 = 孙策最多3张、陈珪也最多3张。
            #   ⚠ 普通和金算不算同一个目标: 算。孙策普通/金是两个 chessID, 但都归到"孙策"这个目标下
            #     计数(脚本端收的是 Python 下发的【分组表】, 见 _scenario_ids_for_names 那一段)。
            {"key": "buyCount", "label": "每种最多买几张", "type": "int", "default": 0,
             "omitIfDefault": True,   # 0 = 老行为(不限), 不写进 JSON → 老剧本编辑后不会多出 buyCount:0
             "hint": "0 或留空 = 不限(货架上有几张买几张); 填 3 = 孙策最多买 3 张、陈珪也最多买 3 张。"
                     "只想买一张就填 1(不填的话货架上两张同名会一起买走)"},
            {"key": "requireAll", "label": "找齐所有目标才算成功", "type": "bool", "default": False,
             "hint": "勾上 = 每个目标都得买到(上面填几张就要几张)才往下走, 没齐就继续刷新;"
                     "不勾 = 买到任一个就走(老行为)。例: 孙策+陈珪都写、这里勾上 = 两个都买到才继续"},
            # v4.102★ 「张数从哪儿算起」—— 用户问"孙策已经3张、陈珪1张, 刷到孙策还会买吗?"
            #   旧实现只数"这一步买了几张", 于是已经有 3 张孙策时还会再买 3 张(买到第 6 张)。
            #   ✅默认(勾选)= 凑够口径: 手里+阵上已有的算进张数, 孙策已有3张就不再买孙策。
            #   ☐取消  = 补货口径: 不管手里有几张, 这一步就是要【再买】N 张
            #            (真要囤同名卡/刷三连以外的用途时才用)。
            #   ⚠ 只有填了「每种最多买几张」时才有意义; 没填(不限)时两种口径行为一样。
            {"key": "countFromOwned", "label": "张数从已有算起(凑够就行)", "type": "bool",
             "default": True,
             "showWhen": ("buyCount", ("__set__",)),
             "hint": "勾选(默认) = 手里/阵上【已有】的张数先算上: 孙策已经有 3 张、这里填 3 "
                     "→ 刷到孙策也不买(已经够了)。\n"
                     "取消 = 只数这一步【新买】了几张: 手里已有 3 张孙策, 填 3 就再买 3 张。\n"
                     "★「找齐」勾上时同理: 勾选=每种凑够就行; 取消=每种都要新买到这么多张。"},
            # v4.110★ 「凑够之后还买不买」—— 脚本端 v4.104 早就有 buyAfterFull, 但 schema 里没声明:
            #   ① 界面上没得填; ② 编辑一次就抹掉(和下面 optional 踩的是同一个坑)。
            #   默认(留空)= 按金币: 凑够 N 张后商店再刷出来、买完仍留得住保留金币且还能再刷一次 → 照样买走。
            #   ⚠ 副作用就是「只想买一张邓茂, 货架上同时刷出两张邓茂 → 两张都买走, 多花金币」。
            {"key": "buyAfterFull", "label": "凑够后还买吗", "type": "text", "default": "",
             "hint": "留空 = 按金币(默认): 凑够之后商店再刷出来、钱还宽裕就买走(会多花金币, "
                     "凑数量的孙策/陈珪这样正好)。\n"
                     "填 off = 凑够就绝不再买 ★「一次只要一张」的(邓茂/张鲁这类刷完就遣散的)填这个。\n"
                     "填数字(如 12) = 金币 ≥ 该值才买。"},
            # v4.151★ 改成【一张卡一行的小表】(以前是单行输入框, 得手敲 "张宝 3 off",
            #   而脚本端只按换行分行 —— 用逗号连写的旧串会被当成同一张卡的后续列, 整条失效)。
            #   表里每一行三格: 卡名(可挑)/要几张(下拉)/凑够后(下拉, 也可手敲金币门槛)。
            #   存出来的仍是【一行一张、换行分隔】的文本, 旧剧本不用转换、脚本端不用改。
            {"key": "cardPlan", "label": "★按卡分别定(一张一行)", "type": "cardplan", "default": "",
             "hint": "一张卡一行, 每格都能【选】, 不用手打:\n"
                     "  卡名 = 从武将表里挑(下面「＋挑一张」, 也能按常用/势力整组挑); "
                     "「找哪张卡」里写过的会自动补成行。\n"
                     "  要几张 = 不限(有就买) / 选 1~9 / 也可以直接敲更大的数(比如 12); "
                     "凑够的口径看上面「张数从已有算起」。\n"
                     "  凑够后 = 不再买 / 看金币 / 选最后一项时在【右边框里填金币门槛】(如 20), "
                     "不填就沿用上面「凑够后还买吗」。\n"
                     "★选「不限」时第三列会置灰 —— 连「要凑够几张」都没有, 就谈不上「凑够之后」, "
                     "而且脚本端是按列读的, 写上去会读歪。★表里没写的卡按上面那两个统一的数走。"},
            #   (命中但买不起 / 刷新失败 / 刷完没找到 三条路都有 optional 分支), 但 schema 里没声明 →
            #   ① 界面上没得勾; ② 更坑的是【编辑一次就把它抹掉】—— 编辑框只回写 schema 里声明的字段,
            #   预设里写好的 optional:True 一存就没了。后果: 拿"牌库找卡"当可选步骤时,
            #   货架上恰好没这张卡 = 整个剧本中断, 后面的步骤全不跑。
            #   默认 False = 旧行为(找不到就报错中断), 想宽松得自己勾上。
            {"key": "optional", "label": "找不到就算了(不中断)", "type": "bool", "default": False},
        ],
    },
    # v4.101★ 移动已上阵武将的位置(换位置)
    "moveCard": {
        "fields": [
            {"key": "target", "label": "挪谁", "type": "names", "req": True, "pickFrom": "hero",
             "hint": "卡名(如 孙策), 或阵上格号: 3 / 第3格。"
                     "⚠ 这张卡必须【已经在阵上】—— 手牌里的卡请用「上阵」, 这一步只挪已经摆上去的"},
            {"key": "pos", "label": "挪到哪儿", "type": "combo", "free": True,
             "values": [("第%d格" % _i, str(_i)) for _i in range(1, 8)]
                       + [("倒数第二格", "default")],
             "hint": "也可手写 after:孙尚香(挪到它右边) / before:孙尚香(它左边)。"
                     "★ 目标格上已经有卡时【两张互换位置】; 是空位就直接搬过去(原来那格空出来); "
                     "已经在那格就不动"},
            {"key": "optional", "label": "挪不动就算了(不中断)", "type": "bool", "default": False},
        ],
    },
    # v4.101★ 批量上阵(手牌里的武将一次全上)
    "deployAll": {
        "fields": [
            {"key": "target", "label": "只上这几张(留空=全上)", "type": "names", "pickFrom": "hero",
             "hint": "留空 = 手牌里的武将【全部】上阵; 填了就只上名单里的这几张"},
            {"key": "exclude", "label": "但这几个别上", "type": "names", "pickFrom": "hero",
             "hint": "排除名单, 逗号分隔"},
            {"key": "pos", "label": "落点", "type": "combo", "free": True,
             "values": [("开位(最左)", "a"), ("第一个空位", "first"), ("倒数第二格", "default")]
                       + [("第%d格" % _i, str(_i)) for _i in range(1, 8)],
             "hint": "每一张都往这个落点上(留空=游戏自己安排); 也可手写 after:孙尚香"},
            {"key": "maxCount", "label": "最多上几张", "type": "int", "default": 0,
             "hint": "0 或留空 = 手牌里有多少上多少"},
            {"key": "gapMs", "label": "每张之间等多久(毫秒)", "type": "int", "default": 320,
             "hint": "上太快服务器来不及结算(日志会出现『包发了但那张还在』), 想快可以填 150~200"},
            {"key": "optional", "label": "没上齐就算了(不中断)", "type": "bool", "default": False},
        ],
    },
    # v4.101★ 记录武将数据(攻/血 → 变量)
    "recordStat": {
        "fields": [
            {"key": "target", "label": "记谁", "type": "names", "req": True, "pickFrom": "hero",
             "hint": "卡名(多个用逗号 = 都记一遍)"},
            {"key": "scope", "label": "从哪儿找", "type": "combo",
             "values": SCENARIO_SCOPE_PAIRS, "default": "field",
             "hint": "阵上 / 手牌"},
            {"key": "varName", "label": "变量名前缀(留空=用卡名)", "type": "text",
             "hint": "存出来的变量叫「前缀_攻」和「前缀_血」(例: 填 sunce → sunce_攻 / sunce_血); "
                     "留空就用卡名(孙策_攻 / 孙策_血)。之后在条件里选「变量」就能拿它判断, "
                     "配一步「写日志」就能把数值留下来"},
            {"key": "optional", "label": "没找到就算了(不中断)", "type": "bool", "default": False},
        ],
    },
    # v4.101★ 用主公技能(底层 lordSkillUse 早就有了, 以前只能按快捷键)
    "lordSkill": {
        "fields": [
            {"key": "skillId", "label": "技能ID(留空=自动挑一个能用的)", "type": "int",
             "skillPick": True,
             "hint": "留空 = 用【本回合还没用过】的第一个主公技能; 想指定就填 ID。"
                     "不知道本局有哪些: 看助手的「主公技能」面板, 或跑一次看剧本日志(会打印本局技能与是否已用)"},
            {"key": "pick", "label": "技能目标(需要选人的技能才填)", "type": "combo", "free": True,
             "pickFrom": "pick", "values": SCENARIO_PICK_PRESETS,
             "hint": "点兵/戎弁/密诏这类要点一个友方的技能, 在这里写那张卡的名字 —— "
                     "脚本把目标一起发过去, 不用去点游戏那个窗口"},
            {"key": "force", "label": "已用过也再试一次", "type": "bool", "default": False,
             "hint": "一般别勾: 主公技能一回合通常只能用一次, 硬发多半被服务器拒绝"},
            {"key": "optional", "label": "用不了就算了(不中断)", "type": "bool", "default": False},
        ],
    },
    # v4.101★ 按数值重排站位
    "arrange": {
        "fields": [
            # v4.107★ 「怎么排」—— 老行为(按攻/血)保留为默认, 新增【按名单】:
            #   直接写"1号位放谁、2号位放谁", 与阵容页存的 7 格站位是同一个顺序。
            {"key": "mode", "label": "怎么排", "type": "combo",
             "values": [("按数值(攻/血)", "stat"), ("按名单(谁站第几格)", "list")], "default": "stat",
             "hint": "按名单 = 照你写的顺序摆(第 1 个放第 1 格 = 最前排); 按数值 = 攻/血高的站前面"},
            {"key": "slots", "label": "站位名单(第1格起)", "type": "names", "pickFrom": "hero",
             "showWhen": ("mode", ("list",)),
             "hint": "按想站的顺序填, 逗号隔开: 第 1 个放第 1 格(最前排)。没写进名单的卡排在后面。"
                     "阵容页『站位』那 7 格可以直接照抄过来"},
            {"key": "by", "label": "按什么排", "type": "combo",
             "values": [("攻击", "atk"), ("血量", "hp")], "default": "atk",
             "showWhen": ("mode", ("stat",)),
             "hint": "读的是【卡面有效值】(和「记数值快照」同一把尺子)"},
            {"key": "order", "label": "顺序", "type": "combo",
             "values": [("由高到低(最强的站最左)", "desc"), ("由低到高(最弱的站最左)", "asc")],
             "default": "desc", "showWhen": ("mode", ("stat",)),
             "hint": "排完是【整条阵容重发一次】(与「移动」同一条底层路), 读不到数值的卡会被排到最后"},
            {"key": "optional", "label": "排不了就算了(不中断)", "type": "bool", "default": False},
        ],
    },
    "sellBelow": {
        "fields": [
            {"key": "keep", "label": "留几张", "type": "int", "req": True, "default": 3,
             "hint": "多出来的全卖(从最后往前卖 —— 后面的多是刚买的杂牌)"},
            {"key": "protect", "label": "这几张不卖", "type": "names", "pickFrom": "hero",
             "hint": "留空 = 谁都能卖。想留的主力写这里(先精确再模糊匹配, 不会误卖神卡)"},
            {"key": "scope", "label": "卖哪里的", "type": "combo",
             "values": [("只卖手牌", "hand"), ("只卖阵上", "field"), ("都算", "both")], "default": "hand",
             "hint": "默认只卖手牌 —— 勾上阵上要小心: 卖掉就是下场了"},
            {"key": "optional", "label": "卖不动就算了(不中断)", "type": "bool", "default": False},
        ],
    },
    "buyUntilGold": {
        "fields": [
            {"key": "target", "label": "想买什么", "type": "names", "pickFrom": "hero",
             "hint": "留空 = 只刷不买(那就等于一条带金币线的刷新)"},
            {"key": "keepGold", "label": "金币低于多少就停", "type": "int", "req": True, "default": 5,
             "hint": "刷到手里金币 < 这个数就收手 —— 配前面的「等金币」就是 攒钱 → 爆买"},
            {"key": "maxRefresh", "label": "最多刷几次", "type": "int", "default": 20,
             "hint": "兜底: 刷这么多次还没到线就停(免得金币线写错时一直刷)"},
            {"key": "buyAll", "label": "货架上同名的都买", "type": "bool", "default": False},
            {"key": "optional", "label": "没到线就算了(不中断)", "type": "bool", "default": False},
        ],
    },
    "swapSlots": {
        "fields": [
            {"key": "a", "label": "第几格", "type": "int", "req": True, "default": 1,
             "hint": "1~7(第 1 格 = 最前排, 先挨打)"},
            {"key": "b", "label": "和第几格对调", "type": "int", "req": True, "default": 7,
             "hint": "⚠ 只调【顺序】。阵上的卡【不能直接换下来】—— 想让它下场只能靠陈珪的当先"
                     "收回手牌, 或遣散。与「移动」的区别: 这条写格号就行, 不用知道那格是谁"},
            {"key": "optional", "label": "调不了就算了(不中断)", "type": "bool", "default": False},
        ],
    },
    "pauseForUser": {
        "fields": [
            {"key": "text", "label": "提示写什么", "type": "text", "default": "",
             "hint": "页面正中会出一条提示 + 一个「继续」按钮, 你点了剧本才往下走"},
            {"key": "timeoutMs", "label": "最多等多久(毫秒)", "type": "int", "default": 0,
             "hint": "0 = 一直等你点(默认); 填数字 = 超时按下面那条处理"},
            {"key": "optional", "label": "超时就算了(不中断)", "type": "bool", "default": False},
        ],
    },
    "beep": {
        "fields": [
            {"key": "text", "label": "顺便记一句", "type": "text", "default": "",
             "hint": "写不写都行 —— 会进剧本日志(比如写「下饭阵容成型了」)"},
            {"key": "times", "label": "响几声", "type": "int", "default": 2,
             "hint": "1~5。浏览器不让出声时只弹条, 不会中断剧本"},
        ],
    },
    "useSpellIf": {
        "fields": [
            {"key": "target", "label": "用哪张锦囊", "type": "names", "req": True,
             "pickFrom": "spell",
             "hint": "与「使用锦囊」同一套(也支持 A > B 的优先级写法)" + SCENARIO_PRIORITY_HINT},
            {"key": "on", "label": "作用到", "type": "text", "default": "",
             "hint": "同「使用锦囊」的『作用到』: 手牌 / 阵上 / 阵上3格 / 牌库1 … 也能写「手牌:孙策」。"
                     "留空 = 不用选目标"},
            {"key": "minField", "label": "阵上至少几张才用", "type": "int", "default": 0,
             "hint": "0 或留空 = 不设这个条件"},
            {"key": "minGold", "label": "金币至少多少才用", "type": "int", "default": 0,
             "hint": "0 或留空 = 不设这个条件"},
            {"key": "has", "label": "要有这张卡才用", "type": "names", "pickFrom": "hero",
             "hint": "手牌或阵上有它才用(留空 = 不设)。条件不满足 = 整步跳过(不用锦囊), 不中断剧本"},
        ],
    },
    "recordResult": {
        "fields": [
            {"key": "text", "label": "标记", "type": "text", "default": "",
             "hint": "打个点: 把此刻的回合/等级/血量/金币/阵容写进剧本日志(比如「阵容成型」)。"
                     "⚠ 真正的战绩是对局结束时由协议自动记的那条(带名次), 这一步不会重复记"},
        ],
    },
    "buyCard": {
        "fields": [
            {"key": "target", "label": "买哪张卡", "type": "names", "req": True, "pickFrom": "hero",
             "hint": "只看当前货架, 不刷新。点右边「挑选」从武将表里选"},
            # v4.68★ 版本(普通/金色): 与上阵/遣散同一件事 —— 货架上同名的普通和金都在时,
            #   不指定就是"先扫到哪张买哪张"。脚本端 _scOpBuyCard 认这个字段(买之前还会
            #   按这个版本过滤 + 用实价问一次"买得起吗")。
            {"key": "version", "label": "卡的版本", "type": "combo",
             "values": SCENARIO_VERSION_PAIRS, "default": "",
             "hint": "货架上普通和金都有时, 指定买哪一张。" + SCENARIO_VERSION_HINT},
            {"key": "buyCount", "label": "最多买几张", "type": "int", "default": 0,
             "hint": "留空或 0 = 只买第一张(老行为); 填 N = 这一步最多买 N 张"
                     "(货架上同名有几张买几张, 最多 N)"},
            # v4.65★ 补 optional —— 脚本端(v4.2x 起)一直认它(货架没这张卡时不中断),
            #   但 schema 里没声明 → 界面上看不到, 而且【编辑一次就被抹掉】。
            #   与前面 deploy/recycle/shopFind 是同一个坑, 一并补齐。
            {"key": "optional", "label": "货架上没有就算了(不中断)", "type": "bool", "default": False},
            # v4.163b★ 不点名 any —— 脚本端认(买货架上第一张; any='spell' 买第一张锦囊)。
            #   验证/通用剧本不知道这局货架有什么, 点名就必然落空, 所以必须能不点名。
            #   同样必须在 schema 声明, 否则编辑框「确定」会把 any 抹掉。
            {"key": "any", "label": "不点名(留空=点名买)", "type": "text", "default": "",
             "hint": "验证剧本用: 留空=按上面名字买; 填 spell=买货架上第一张锦囊; 填其它=买第一张武将"},
        ],
    },
    "refreshShop": {
        "fields": [
            {"key": "refreshCount", "label": "刷新几次", "type": "int", "req": True, "default": 1},
            {"key": "keepGold", "label": "保留金币", "type": "int", "default": 0,
             "hint": "手上低于这个数就不刷了"},
            # v4.65★ 补 optional —— 脚本端认得(刷不满次数时: 金币不够2金 / 商店没变化 → 不中断),
            #   但 schema 里没声明 → 界面看不到 + 编辑一次就抹掉。
            #   这条对"循环刷牌"的剧本很关键: 金币刷干是正常的, 不勾就会把整个剧本中断在最后一下。
            {"key": "optional", "label": "刷不满就算了(不中断)", "type": "bool", "default": False},
            # v4.164★ 极限连发 burst —— 脚本端 v4.164 起认(只调发包函数、不读虎符/不验证)。
            #   ★ 必须声明在 schema 里, 否则编辑框「确定」时会把 burst 抹掉(就是之前"剧本里 burst
            #     不管用"的坑: 控制台直接调 _scOpRefreshShop({burst:true}) 生效, 走剧本却丢字段)。
            #   勾上 = 脚本侧 1 秒能甩 2000+ 个包, 但游戏客户端处理刷新有速率, 真生效的没几个 ——
            #   纯测"发包最快"用, 想让每次都真刷成别勾这个(用虎符判据 + 外部等待控制节奏)。
            {"key": "burst", "label": "极限连发(只发包不验证)", "type": "bool", "default": False,
             "hint": "v4.164★ 最快但绝大多数被游戏吞: 只连着调发包函数、不读虎符/不建快照/不等。"
                     "想真刷成别勾(用虎符判据+节奏页等待)"},
            # v4.166★ 烧虎符到归零 —— 脚本端 v4.166 起认(untilEmpty): 不按固定次数, 刷到虎符
            #   (CoinNum)归零才停。配合 burst 最快。必须声明在 schema, 否则编辑框「确定」会被抹掉。
            {"key": "untilEmpty", "label": "烧虎符到归零(刷到虎符没了才停)", "type": "bool", "default": False,
             "hint": "v4.166★ 一键清空虎符: 循环刷新直到虎符余额归零(不再按上面『刷新几次』, "
                     "那个数只当安全上限, 默认 200 万)。配合【极限连发】最快, 但多数会被游戏吞(纯测发包)"},
            # v4.166★ 方案B 底层直发(对应 window.__scBurstDeep): 勾上则绕过 ReqShopRefreshChess() 高层封装,
            #   直接走 net.QASendProto 发 CReqShopRefreshChess 协议(跳过 ~14ms 客户端同步/UI 开销)。
            #   代价: 服务器可能限流/去重(连发太快只认前几个), 且某版本底层改名会【静默失效】。
            {"key": "burstDeep", "label": "底层直发(方案B, 更快但可能静默失效)", "type": "bool", "default": False,
             "hint": "v4.166★ 极限中的极限: 直接发刷新协议包, 跳过客户端 ~14ms 同步开销, 微秒级发包。"
                     "需配合『极限连发』(勾它就自动进 burst)。风险: 连发太快服务器可能只认前几个; "
                     "底层改名会静默失效(发包成功但虎符不掉, 无报错) —— 以虎符实际是否下降为准。"},
        ],
    },
    "levelUp": {
        "fields": [
            {"key": "level", "label": "升到几级", "type": "int", "req": True, "default": 6,
             "hint": "★ 这就是【升到 N 级】(不是升一级): 一直升到这个等级才收手; "
                     "想说「升两次」就填下面那栏"},
            # v4.107★ 这两栏脚本端从 v4.47 起就认(count=升几次 / keepGold=保留金币), 但 schema 里
            #   一直没声明 —— 后果与其它 op 是同一个坑: 界面上看不到, 而且【编辑一次就被抹掉】
            #   (编辑框只回写 schema 声明过的字段, 预设里写好的 count 一保存就没了)。
            {"key": "count", "label": "改成就升几次", "type": "int", "default": 0,
             "omitIfDefault": True,
             "hint": "填了就按【次数】升(不管升到几级), 与上面那栏二选一"},
            {"key": "keepGold", "label": "保留金币", "type": "int", "default": 0,
             "omitIfDefault": True,
             "hint": "手上低于这个数就不升了"},
            {"key": "optional", "label": "升不到就算了(不中断)", "type": "bool", "default": False},
        ],
    },
    "lockShop": {
        "fields": [
            {"key": "target", "label": "锁还是解锁", "type": "combo",
             "values": [("锁定商店", "锁"), ("解锁商店", "解锁")], "default": "锁"},
        ],
    },
    "moneyPlan": {
        "fields": [
            {"key": "target", "label": "想找的卡", "type": "names", "pickFrom": "hero",
             "hint": "可留空 —— 只做金币/等级节奏。点右边「挑选」从武将表里选"},
            {"key": "refreshCount", "label": "最多刷新(次)", "type": "int", "default": 3},
            {"key": "level", "label": "目标等级", "type": "int", "default": 0,
             "hint": "留空 = 按回合自动(≤2回合4级 / ≤5回合5级 / 之后6级)"},
            {"key": "keepGold", "label": "保留金币", "type": "int", "default": 0},
            {"key": "buy", "label": "找到就买下来", "type": "bool", "default": True},
        ],
    },
    # ================= 锦囊 =================
    "useSpell": {
        "fields": [
            {"key": "target", "label": "用哪张锦囊", "type": "names", "req": True,
             "pickFrom": "spell",
             # v4.69★ 优先级: 见 SCENARIO_PRIORITY_HINT。为什么锦囊特别需要它 ——
             #   抽到哪张锦囊是随机的(贾诩/诸葛亮给的六本里不一定有你写的那张),
             #   以前只能写一张, 没抽到那一步就白等; 现在写 "瞒天过海·六 > 联军盛宴·五"
             #   就是"优先用它, 没有就退而用后者"。
             "hint": "名字带不带间隔号都行(顺手牵羊三 / 顺手牵羊·三)。点右边「挑选」从本季锦囊表里选。"
                     + SCENARIO_PRIORITY_HINT},
            # v4.65★ 「作用到」补全 —— 用户原话"阵上的目标不完整啊、只有第一个; 还有手牌?
            #   不能选最后一个?"。老下拉只有 牌库1~6格 / 手牌第一张 / 阵上第一个, 阵上第2~7格
            #   和两边的「最后一个」都写不出来。现在补上三个"最后一张", 并把这行改成【可手输】:
            #   想指第几格就照样式写「阵上3格」「牌库5格」「手牌2」(数字=屏幕格号, 1-based)。
            # v4.69★ 阵上1~7格也进下拉 + 加「挑选」按钮(用户要的"使用也能挑")。
            {"key": "on", "label": "作用到", "type": "combo", "default": "shop", "free": True,
             "pickFrom": "target",
             "values": SCENARIO_ON_PAIRS, "hint": SCENARIO_ON_HINT},
            {"key": "onName", "label": "指向某个武将", "type": "text", "pickFrom": "hero",
             "hint": "留空=按上面「作用到」; 填武将名就优先指它(名字完全相等优先, 不会把神将一起算上)。"
                     "点右边「挑选」从武将表里选。★ 写多个也能排先后(孙策 > 周瑜): 前者不在场上才用后者"},
            # v4.65★ 补两个脚本端一直在认、schema 里却没声明的字段(和 useAllSpells/recycle
            #   是同一个坑): 界面上看不到, 而且【编辑一次就被回写抹掉】。
            {"key": "waitMs", "label": "用完等多久(毫秒)", "type": "int", "default": 1200,
             "hint": "等这张锦囊真的离开手牌再走下一步; 到点还在手牌就记一笔失败"},
            {"key": "optional", "label": "用不出去就算了(不中断)", "type": "bool", "default": False},
            # v4.163b★ 不点名 any —— 脚本端认(用手牌里第一张锦囊)。验证剧本不知道这局抽到哪张,
            #   点名就必然落空, 所以必须能不点名。同样必须声明, 否则编辑框「确定」会抹掉。
            {"key": "any", "label": "不点名(用手牌第一张锦囊)", "type": "bool", "default": False,
             "hint": "验证剧本用: 勾上就不指定哪张、用手牌里第一张锦囊"},
            # v4.??★ 用完还要选的那一下(见 SCENARIO_POST_PICK_HINT)
            *_scenario_post_pick_fields(),
        ],
    },
    "useAllSpells": {
        "fields": [
            {"key": "rounds", "label": "最多用几张", "type": "int", "default": 30,
             "hint": "防死循环的上限"},
            {"key": "waitMs", "label": "每个目标等多久(毫秒)", "type": "int", "default": 700,
             "hint": "目标多的时候调小能明显加快(500 一般也够)。"
                     # v4.68★ 把"大概要多久"算出来写在界面上 —— 用户问的就是这句:
                     #   "那个用尽锦囊大概要多久时间呢?"
                     #   公式 = 每张锦囊 最多试的候选目标数 × 这里的等待。候选 = 牌库6格 + 阵上7格
                     #   (勾了「试着用在手牌上」再加手牌张数)。所以默认(30张/700ms)最坏 ≈ 30×13×0.7s
                     #   ≈ 4.6 分钟; 实际绝大多数锦囊第 1~2 个目标就命中(几十毫秒到几秒一张)。
                     "★耗时(最坏): 每张最多要试 牌库6格 + 阵上7格 = 13 个目标, "
                     "所以「最多用几张 × 13 × 这个等待」就是上限 —— 默认(30张/700ms)约 4~5 分钟; "
                     "多数锦囊前 1~2 个目标就中, 实际通常几秒到几十秒。"
                     "要更快: 等待调 500 / 关掉不需要的「试着用在…」(勾了手牌候选会更多)"},
            # v4.56★ 目标候选池与方向。
            #   原来写死"牌库逐格 → 阵上【最后一个】", 扫不到手牌、也扫不到阵上中间:
            #   锦囊要指向"手牌里的某张"或"阵上中间那张"时, 从头发到尾都试不到。
            {"key": "scanShop", "label": "试着用在牌库上", "type": "bool", "default": True},
            {"key": "scanField", "label": "试着用在阵上", "type": "bool", "default": True},
            {"key": "scanHand", "label": "试着用在手牌上", "type": "bool", "default": False,
             "hint": "锦囊要点手牌里的武将时勾上(候选变多会慢些)"},
            {"key": "dirRtl", "label": "从右往左扫", "type": "bool", "default": False,
             "hint": "斗转星移这类「移除一个、数值加给最左边」的必须从右往左, 否则会把左边先送掉"},
            {"key": "noTargetFirst", "label": "先甩掉不需要目标的", "type": "bool", "default": True},
            # v4.64★ 步骤级排除名单 —— 用户原话"用所有锦囊, 但是不能用破釜沉舟·六, 斗转星移四,
            #   闪电五", 并特意强调"那个用尽锦囊【不要修改预设】…因为只有这次需要排除这几个
            #   锦囊"。所以做成步骤自己的字段: 同一个「用尽锦囊」步骤, 在招募剧本里排除这几张,
            #   在别的剧本里照样全用 —— 内置预设一个字都没动。
            {"key": "exclude", "label": "但这几张留着不用", "type": "names",
             "hint": "名字逗号分隔, 带不带间隔号都行(破釜沉舟·六 / 破釜沉舟六)。"
                     "这几张会被当'不在手里'跳过, 日志末尾会写「按排除名单留下 N 张」"},
            # v4.64★ 顺手补上 optional —— 脚本端从 v4.41 起就一直认这个字段(没有可用锦囊时
            #   不中断), 但 schema 里没声明: 界面上看不到, 编辑一次还会被回写抹掉。
            #   与 recycle 补 camp/exclude 是同一个坑, 自检把它们一起扫出来了。
            # v4.65★ 默认 True → False, 理由同 deploy: 默认值不写出去 = 勾了也白勾。
            {"key": "optional", "label": "一张都用不出去就算了(不中断)", "type": "bool", "default": False},
            # v4.??★ 每张用完还弹选择窗时统一按这两个框挑(见 SCENARIO_POST_PICK_HINT)。
            #   这一步是"一次用一堆", 所以这里的设定对【所有】卡片生效 ——
            #   要让每张挑得不一样, 请用「逐条用锦囊」(它每行都能单独写 挑:)。
            *_scenario_post_pick_fields(),
        ],
    },
    # ================= v4.68★ 用尽锦囊·逐条 =================
    #   用户原话: "是不是可以再写一个用尽锦囊、这个用尽锦囊【不改】; 新的用尽锦囊, 就是
    #   每一个锦囊, 单独用使用锦囊去写他们的用法呢? 然后还可以选择不用哪些呢?
    #   但是要把他们的用法写出来, 可以启用"。
    #   → 所以「用尽锦囊」一个字没动, 这是并存的新步骤: 一行一张锦囊, 自己写用法, 可单独停用。
    #
    #   写法(填在「每张锦囊的用法」里, 用逗号分隔多条):
    #       瞒天过海·六=阵上1, 破釜沉舟·六=手牌:孙策, 斗转星移·四
    #   等号右边是"作用到", 与「使用锦囊」的写法完全一致(阵上2 / 牌库1 / 手牌:孙策 / 直接使用);
    #   留空 = 交给脚本自动轮目标(牌库1→N → 阵上)。
    #   这一条【不用】就在前面加 # :  #草船借箭·六=牌库1
    #
    #   为什么用"名字=目标"这种一条一条的写法, 而不是做一个可拖拽的小表格:
    #     · 少一次点击 —— 复制粘贴、批量改、跨剧本搬都能直接当文本处理;
    #     · 进 JSON 就是一个干净的字符串("瞒天过海·六=阵上1, 破釜沉舟·六=手牌:孙策"),
    #       复制给别人的时候不用交代格式, 粘进框里就能跑;
    #     · 不需要为它新增控件类型(那种控件在这个 55 万行的界面里维护成本很高)。
    #   ⚠ 字段类型必须是 text 而不是 names —— names 走 _scenario_split_names,
    #     它会【按空格也切一刀】, 于是"瞒天过海·六 = 阵上1"(等号两边带空格, 谁都这么写)
    #     会被切成 ["瞒天过海·六","=","阵上1"] 三条, 静默变成三个不存在的锦囊。
    #     脚本端 _scParseSpellPlan 认的是"整个字符串再按 , ; 换行 切", 两边口径要对齐。
    "spellPlan": {
        "fields": [
            # v4.69★ 从单行 Entry 换成多行文本框 —— 内置预设现在是【整季锦囊全列出来】
            #   (130+ 行), 单行框里根本没法改(左右拖着一格一格看)。解析口径一个字没动,
            #   仍然按 逗号/分号/换行 切, 逗号那一版老剧本照旧能读。
            {"key": "plan", "label": "每张锦囊的用法", "type": "multiline", "req": True,
             "pickFrom": "spellplan",
             "hint": "一条一张: 锦囊名=作用到(用逗号或换行分隔)。"
                     "例: 瞒天过海·六=阵上1, 破釜沉舟·六=手牌:孙策, 斗转星移·四。"
                     "等号右边写法与「使用锦囊」一致(阵上2 / 牌库1 / 手牌:孙策 / 直接使用); "
                     "★ 留空 = 自动轮目标(直接使用 → 牌库1~6格 → 阵上1~7格, 谁能让这张离手就算谁)。"
                     "★ 右边「每张锦囊的用法」右边那两个按钮: 「补全本季锦囊」= 一键列全本赛季所有锦囊; "
                     "「＋加一张」= 从锦囊表里挑一张追加(自动补上 名字= )。"
                     "★ 这一条不想用就在最前面加 #(如 #草船借箭·五=牌库1), 其余照跑。"
                     # v4.68★ 用户问:"贾诩选锦囊, 如果随机的六本锦囊没有瞒天过海·六呢? 我选了要它"
                     "★ 写了名字但【这一局没抽到】(例: 挑牌给的六本里没有 瞒天过海·六): "
                     "那一行直接跳过, 日志末尾单独列出来(不算失败、不会中断剧本); "
                     "想连提都不提就按上面那样加 #。"
                     "★ 耗时: 每行最多 ≈ 每张等生效 + 两张之间等 ≈ 1.4 秒(默认 1200+150), "
                     "10 行也就十几秒 —— 比「用尽锦囊」快得多, 因为它不挨个试目标"
                     + SCENARIO_PLAN_PICK_HINT},
            {"key": "gapMs", "label": "两张之间等多久(毫秒)", "type": "int", "default": 150,
             "hint": "太赶会在日志里看到「发了但没生效」—— 服务器还没处理完上一张"},
            {"key": "waitMs", "label": "每张等生效(毫秒)", "type": "int", "default": 1200,
             "hint": "每张最多等这么久; 这张离开手牌就立刻往下走(所以正常不会真的等到上限)"},
            {"key": "optional", "label": "一张都用不出去就算了(不中断)", "type": "bool", "default": False},
            # v4.??★ 每张还能【单独】指定后续选择(下面两栏是"都没写时的兜底")
            *_scenario_post_pick_fields(SCENARIO_PLAN_PICK_EXTRA),
        ],
    },
    # ================= v4.80★ 用尽锦囊·按用法 =================
    # 为什么还要再写一步(用户原话: "可以先看手牌有什么再去执行对应的"):
    #   「用尽锦囊」= 每张都把目标挨个试过去 —— 它不认识任何一张, 每张该给谁全靠猜(慢);
    #   「逐条用锦囊」= 每张都由你写, 但【计划里没写的】锦囊一张不碰 —— 手上突然多一张没写过的
    #     (比如挑牌窗奖励的、上一张锦囊给的)就用不掉, 得回头补计划。
    #   这一步: 开工先扫手牌报总账, 然后一张张看 —— 用法表里写了的按写法【一发命中】,
    #   没写的按下面那一行选的策略处理。所以"写几张常用的"就能既快又用得完。
    "spellSmart": {
        "fields": [
            {"key": "rules", "label": "每张锦囊的用法", "type": "multiline", "req": True,
             "pickFrom": "spellplan",
             "hint": "一条一张: 锦囊名=作用到(用逗号或换行分隔)。例: 瞒天过海·六=阵上1, "
                     "破釜沉舟·六=手牌:孙策, 乐不思蜀·四。"
                     "等号右边写法与「使用锦囊」一致(阵上2 / 牌库1 / 手牌:孙策 / 直接使用); "
                     "★ 多认一个词「同上」= 沿用上一张锦囊生效的那个目标(「随机应变」脚本已经内置成同上, "
                     "不用你自己写)。"
                     "★ 这一张不想用就在最前面加 #(如 #草船借箭·五=牌库1), 其余照跑。"
                     "★ 【没写到这里】的锦囊按下面那一行处理 —— 所以这张表只填你真正在意的那几张就行, "
                     "不用像「逐条用锦囊」那样列全季。"
                     "★ 右边那两个按钮: 「补全本季锦囊」= 一键列全本赛季所有锦囊(再删掉不想管的); "
                     "「＋加一张」= 从锦囊表里挑一张追加(自动补上 名字= )。"
                     "★ 名字是【包含匹配】, 写「瞒天过海」也能命中「瞒天过海·六」。"
                     + SCENARIO_PLAN_PICK_HINT},
            {"key": "noRule", "label": "没写用法的锦囊怎么办", "type": "combo",
             "values": SCENARIO_NORULE_PAIRS, "default": "auto",
             "hint": "★ 自动轮目标(默认) = 和老「用尽锦囊」一样: 直接使用 → 牌库1~6格 → 阵上1~7格, "
                     "谁能让这张离手就算谁 —— 好处是手牌一定用得完, 代价是那张该给谁由脚本猜(慢一点)。"
                     "★ 留着不动 = 只用你在上面写了用法的那几张, 其余原样留在手牌(日志里会列出来) —— "
                     "怕脚本把某张锦囊给错人时选这个。"
                     "⚠ 注意这里是『试中即消耗』: 一旦服务器收下就收不回来了, 所以写了用法的那几张"
                     "不会再试别的目标(用法不生效就记一笔失败, 不会乱给)。"},
            {"key": "rounds", "label": "最多用几张", "type": "int", "default": 30,
             "hint": "防死循环的上限; 手牌里的锦囊用完就提前结束, 不会真的跑满"},
            {"key": "gapMs", "label": "两张之间等多久(毫秒)", "type": "int", "default": 150,
             "hint": "太赶会在日志里看到「发了但没生效」—— 服务器还没处理完上一张"},
            {"key": "waitMs", "label": "每张等生效(毫秒)", "type": "int", "default": 1200,
             "hint": "每张最多等这么久; 这张离开手牌就立刻往下走(所以正常不会真的等到上限)"},
            {"key": "optional", "label": "一张都用不出去就算了(不中断)", "type": "bool", "default": False},
            # v4.??★ 每张还能【单独】指定后续选择(下面两栏是"这一行没写 挑: 时的兜底")
            *_scenario_post_pick_fields(SCENARIO_PLAN_PICK_EXTRA),
        ],
    },
    # ================= v4.64★ 随征 =================
    #   把随征卡(薛灵芸/黄盖/马元义/张勋/黄巾兵 —— 认的是助手『随征卡牌ID』里那份表)
    #   拖到【阵上】指定的友方身上。用户原话"薛灵芸随征给阵上的魏国, 尽可能分均薛灵芸、
    #   不要一直给一个武将", 所以「轮着来」默认是开的 —— 关掉就每次都固定给第一个。
    "followUp": {
        "fields": [
            # v4.65★ 「可以填多个吗」——可以。这里和下面 targets/exclude 都是【名单】字段,
            #   逗号分隔, 命中的任意一张都算(this._scHit(c, cardKeys)), 取手牌里最靠右的那张。
            #   所以"黄盖、薛灵芸、黄巾兵"直接写成「黄盖, 薛灵芸, 黄巾兵」= 谁在手上就用谁。
            # v4.82★ 「先识别, 再使用」: 全扫手牌认出命中的随征卡(第几位/叫什么/共几张,
            #   日志会报总账), 再由下面「取哪张」决定拿哪一张 —— 不再一律"取最靠右那张"。
            {"key": "cards", "label": "用哪张随征卡", "type": "names", "pickFrom": "hero",
             "hint": "可填多张(逗号分隔): 例「黄盖, 薛灵芸, 黄巾兵」—— 手牌里这几张谁在就用谁; "
                     "写成「黄盖 > 薛灵芸」则按顺位(先黄盖, 没有才薛灵芸)。"
                     "留空 = 认助手『随征卡牌ID』那份表(或页面 window.FOLLOWUP_PRIORITY 优先名单)。"
                     # v4.68★ 用户问:"随征是根据随征的id查的最右边, 还是算手牌最右边?
                     #   如果当时手牌的随征卡牌, 右边有武将或者锦囊呢?"
                     "★ 识别口径: 先在【手牌】里挑出命中的随征卡(只看它是不是随征卡, 不管它左右"
                     "挨着武将还是锦囊), 再按下面「取哪张」挑一张 —— 日志会写"
                     "「识别随征卡 N 张: 第x张 名字 → 本次用…」, 用错那张一眼看得出来。"
                     "★ 名单里填【棋子ID】或【技能ID】都认(如 20904231 或技能 942311) —— "
                     "v4.82 起剧本也认技能ID(以前只有 Shift+3 认, 剧本会静默不命中)"},
            {"key": "order", "label": "取哪张", "type": "combo",
             "values": [("最靠右那张(默认)", "right"), ("识别到的第一张(手牌最左)", "left")],
             "default": "right",
             "hint": "命中的随征卡有好几张时用哪一张。v4.83★ 默认「最靠右那张」—— 与 Shift+2/Shift+3 "
                     "一致: 按识别出来的最右边开始用; 想先用手牌最左边那张就选「识别到的第一张」"},
            # v4.65★ 「不限」在哪 —— 就是这个下拉的第一项。选它 = 不按势力筛,
            #   只按下面的「额外还允许给谁」点名给(即纯"按名字给")。
            {"key": "camp", "label": "给哪一方的", "type": "combo",
             "values": SCENARIO_CAMP_PAIRS, "default": "",
             "hint": "第一项「不限」= 不按势力筛, 此时就只看下面「额外还允许给谁」点名的卡"
                     "(纯按名字给)。选了势力则名单从赛季配置表现读(1魏/2蜀/3吴/4群/5黄巾/"
                     "6汉/7西凉/8袁/11神), 赛季更新后自动跟着变, 不用手工维护"},
            {"key": "targets", "label": "额外还允许给谁", "type": "names", "pickFrom": "hero",
             "hint": "可填多个(逗号分隔), 与上面选的阵营名单【并集】。两种用法: "
                     "① 阵营选「不限」+ 这里点名 = 只给这几张; "
                     "② 补一些【不在阵营名单里、但能蹭到势力】的卡 —— 例: 鲁肃(相邻共享势力)、"
                     "袁术(4个不同势力)。别把这类卡塞进阵营名单, 那会影响所有按该阵营筛的地方"},
            {"key": "exclude", "label": "但不给谁", "type": "names", "pickFrom": "hero",
             "hint": "排除名单, 可填多个(逗号分隔)。例: 钟会。命中这里的一律不选(优先级高于上面两项)"},
            {"key": "rotate", "label": "轮着来(尽量摊均)", "type": "bool", "default": True,
             "hint": "开(默认)= 每次都挑还没给过的那张, 一圈给完再从头; "
                     "关掉 = 永远固定给阵上第一个符合条件的(和 Shift+3 一样老是同一个人)"},
            # v4.78★ 用户原话"随征也是先识别手牌随征卡, 在哪, 有几张, 然后全给一个人一次;
            #   比如黄巾兵都给张梁, 一次就给完了"。
            {"key": "allToOne", "label": "全给同一个人(一次发完)", "type": "bool", "default": False,
             "hint": "开 = 先扫手牌数出命中的随征卡有几张(日志报「黄巾兵×3、…」总账), "
                     "然后逐张发给同一个人, 每张之间等一拍「给完等多久」让服务器结算 —— "
                     "黄巾兵×5 给张梁就一步给完, 不用把随征步骤写 5 遍。"
                     "关(默认)= 每次只发一张(哪一张见上面「取哪张」)"},
            # v4.84★ 用户原话: "这个延迟可以改低吗?" —— 以前【两张之间】也用 waitMs,
            #   想发快就得把"最后一张发完后的结算等待"一起压掉 → 日志常见"发了但没生效";
            #   拆出 gapMs 之后: 只调这一项就能连发, 收尾那拍照旧留给服务器。
            {"key": "gapMs", "label": "两张之间等多久(毫秒)", "type": "int", "default": 200,
             "hint": "只影响「全给同一个人」时【每两张随征卡之间】的间隔 —— 想快就调这里:"
                     "200→120 一般还稳, 填 0 = 连发不等(最快, 但服务器可能来不及结算, "
                     "日志会出现「发了但没生效」, 届时分给不同人的结果会错乱)。"},
            {"key": "waitMs", "label": "发完后等多久(毫秒)", "type": "int", "default": 500,
             "hint": "最后一张发完后等服务器结算(让下一步读到的是已随征的状态)。"
                     "填 0 = 发完立刻走下一步(最快); 嫌整步慢就先调上面「两张之间」那一项"},
            # v4.65★ 默认 True → False, 理由同 deploy: 默认值不写出去 = 勾了也白勾。
            {"key": "optional", "label": "没卡/没目标就算了(不中断)", "type": "bool", "default": False},
        ],
    },
    # ================= v4.173★ 自动随征 =================
    #   与「随征」(followUp) 的区别: 它必须填"给哪一方的 / 点名"; 本步骤【不填也能跑】——
    #   留空就自动挑阵上第一个有棋子的位置(对应 mod 的"自动随征"一键版)。"给谁"三种写法:
    #     ① targets(名字) ② position(阵位号 N, 0 起, 优先于名字) ③ 都不填=自动选第一个有棋子的位置。
    #   识别层 collectHandFollowUps / 候选池 / 命中判定与「随征」共用, 受「自动随征」总开关控制。
    "autoFollowUp": {
        "fields": [
            # 留空 = 认助手『随征卡牌ID』那份表 + 随征优先名单(全要); 填名字/ID 只认指定的。
            {"key": "cards", "label": "用哪张随征卡", "type": "names", "pickFrom": "hero",
             "hint": "留空=认助手『随征卡牌ID』那份表 + 随征优先名单(全部随征掉); 填名字/ID 只认指定的"
                     "(可多张逗号分隔, 或「>」顺位, 例: 黄巾兵, 薛灵芸)"},
            {"key": "order", "label": "取哪张", "type": "combo",
             "values": [("最靠右那张(默认)", "right"), ("识别到的第一张(手牌最左)", "left")],
             "default": "right",
             "hint": "命中好几张时用哪一张(默认最靠右, 与 Shift+3 一致)"},
            # 三种"给谁"入口: 名字 / 阵位号 / 都不填自动。
            {"key": "targets", "label": "随征给谁(名字)", "type": "names", "pickFrom": "hero",
             "hint": "按名字在阵上找这个人给。留空 = 自动挑阵上第一个有棋子的位置(一键随征)"},
            {"key": "position", "label": "或按阵位号给第几格", "type": "int", "default": "",
             "hint": "留空 = 自动挑阵上第一个有棋子的位置(一键随征)。填数字 N = 给阵上第 N 格(0 起), "
                     "填了就优先用它, 忽略上面的名字。越界/空位会退回自动选"},
            {"key": "exclude", "label": "但不给谁", "type": "names", "pickFrom": "hero",
             "hint": "排除名单, 可多个逗号分隔。命中这里的一律不选"},
            # v4.173★ 默认开: 用户原话"有随征卡就随征" —— 手牌里认到的随征卡一次全给同一个人。
            {"key": "allToOne", "label": "手上的随征卡一次全给(默认开)", "type": "bool", "default": True,
             "hint": "开(默认)= 手牌里认到的随征卡都给同一个人(一步随征完); 关 = 只发一张(取上面的「取哪张」)"},
            # v4.173★ 两张之间 / 发完等多久(同「随征」拆开两个字段, 都可填 0 = 连发)。
            {"key": "gapMs", "label": "两张之间等多久(毫秒)", "type": "int", "default": 200,
             "hint": "「一次全给」时每两张随征卡之间。想快调小, 填 0 = 连发不等(最快, 但服务器可能来不及结算)"},
            {"key": "waitMs", "label": "发完后等多久(毫秒)", "type": "int", "default": 500,
             "hint": "最后一张发完后等服务器结算(让下一步读到的是已随征状态)。填 0 = 发完立刻走下一步"},
            {"key": "optional", "label": "没卡/没目标就算了(不中断)", "type": "bool", "default": False},
        ],
    },
    # ================= v4.185★ 自动随征后台引擎: 开 / 关 =================
    #   这两步真正 start/stop 页面里的自动随征循环(见 _sgsSetAutoFollowUp / 桥内 setInterval):
    #   开 = 后台一直盯手牌区, 有随征卡就自动随征给「随征目标」指定的人; 关 = 停掉循环。
    #   它们本身不受引擎限制——是引擎开关本体。手动 Shift+3 / 剧本随征步骤不受影响。
    "autoFollowUpOn": {"fields": []},
    "autoFollowUpOff": {"fields": []},
    # ================= v4.186★ 自动锦囊后台引擎: 开 / 关 =================
    #   这两步真正 start/stop 页面里的自动锦囊循环(见 _sgsSetAutoSpell / 桥内 setInterval):
    #   开 = 后台一直盯手牌区, 有可自动用的锦囊就自动用掉; 关 = 停掉循环。
    #   它们本身不受引擎限制——是引擎开关本体。手动 锦囊步骤 / 快捷键不受影响。
    "autoSpellOn": {"fields": []},
    "autoSpellOff": {"fields": []},
    # v4.187★ 自动遣散后台引擎: 开 / 关
    #   这两步真正 start/stop 页面里的自动遣散循环(见 _sgsSetAutoDismiss / 桥内 setInterval):
    #   开 = 后台盯手牌, 白名单永不遣散、黑名单直接遣散、其余按星级/价值策略; 关 = 停掉循环。
    #   它们本身不受引擎限制——是引擎开关本体。手动 遣散步骤 / 快捷键不受影响。
    "autoDismissOn": {"fields": []},
    "autoDismissOff": {"fields": []},
    # ================= 流程控制: 如果 / 否则 =================
    "ifStart": {
        "fields": _scenario_cond_fields()
          + [{"key": "conds", "label": "条件组(且/或, 留空=用上面单条件)", "type": "condgroup",
              "hint": "写一组【且/或】组合条件; 留空就只用上面那条单条件。引擎优先看条件组。"}],
    },
    "else": {"fields": []},
    "ifEnd": {"fields": []},
    # ================= 流程控制: 循环 =================
    "loopStart": {
        # v4.66★ 循环里加「提前跳出条件」 —— 用户原话"只能按次数, 每轮都得手写 如果+跳出"。
        #   条件就复用 ifStart 那一套(判断方式/数值/比较/哪些卡…), 只是「判断」下拉多了
        #   第一项「不提前跳出」: 选它(默认) = 和以前完全一样; 选了别的 = 每轮跑完判一次,
        #   成立就结束循环。所以老剧本 JSON 里没有 check 键 → 走老行为, 不受影响。
        # v4.??★ mode=while(L1): 条件循环 —— 每次开始前判一次条件, 不成立就结束(0 次都跑不到)。
        #   与「按次数 + 跳出条件」的区别: while 是"不成立即停", 且开始时就不成立则一轮都不跑;
        #   次数模式是"先跑够 N 次, 中途条件成立提前停"。两者复用同一组条件字段, 由 mode 区分。
        "fields": [
            {"key": "mode", "label": "循环方式", "type": "combo",
             "values": [("按次数(可加跳出条件)", "count"), ("条件循环 while", "while")],
             "default": "count",
             "hint": "按次数=重复 N 次(下面可加「跳出条件」提前停); 条件循环=每次开始前判条件, "
                     "不成立就结束(开始就不成立则一轮都不跑)"},
            {"key": "times", "label": "循环几次", "type": "int", "req": True, "default": 3,
             "showWhen": ("mode", ("count",)),
             "hint": "从「循环开始」到「循环结束」之间的步骤算一轮; "
                     "下面填了跳出条件时, 它只是【上限】, 不一定真跑这么多次"},
        ] + _scenario_cond_fields("跳出/条件: ", values=SCENARIO_CHECK_PAIRS_OPTIONAL, check_default="")
          + [{"key": "conds", "label": "条件组(且/或, 留空=用上面单条件)", "type": "condgroup",
              "hint": "写一组【且/或】组合条件; 留空就只用上面那条单条件。引擎优先看条件组。"}],
    },
    "break": {"fields": [
        {"key": "text", "label": "备注", "type": "text", "hint": "只写进日志, 可留空"},
    ]},
    "continue": {"fields": [
        {"key": "text", "label": "备注", "type": "text", "hint": "只写进日志, 可留空"},
    ]},
    "loopEnd": {"fields": []},
    # ================= 等待 / 变量 / 时间 =================
    "waitUntil": {
        "fields": _scenario_cond_fields("等到") + [
            {"key": "conds", "label": "条件组(且/或, 留空=用上面单条件)", "type": "condgroup",
             "hint": "写一组【且/或】组合条件; 留空就只用上面那条单条件。引擎优先看条件组。"},
            {"key": "timeoutMs", "label": "超时(毫秒)", "type": "int", "default": 10000,
             "hint": "一直等不到就按超时处理(0=无限等, 危险)"},
            {"key": "optional", "label": "超时也不中断", "type": "bool", "default": False},
        ],
    },
    # v4.66★ 等到某个阶段 —— "等招募开始再干活"(以前得 waitUntil + 判断=阶段, 三步的活)
    "waitStage": {
        "fields": [
            {"key": "stage", "label": "等到哪个阶段", "type": "combo", "req": True,
             "values": SCENARIO_STAGE_PAIRS, "default": "招募",
             "hint": "已经在目标阶段就立刻过; 否则等到它出现为止"},
            {"key": "timeoutMs", "label": "最多等多久(毫秒)", "type": "int", "default": 30000,
             "hint": "默认半分钟。0 = 无限等(战斗阶段很长时才会用到)"},
            {"key": "optional", "label": "等不到就算了(不中断)", "type": "bool", "default": False},
        ],
    },
    # v4.147★ 结束招募 —— 招募阶段提前收工, 直接进战斗。
    #   原理: 直接调 scene.onEndRecruitJump()(与页内 skipBattle() 在 phase 7 时走的是同一个),
    #   不是去点按钮 —— 点按钮那条路只认战斗阶段的 JumpBtn/SkipBtn, 管不到这里。
    #   ⚠ 这是"提前关掉买卡窗口": 放太早会漏买。默认勾着「不在招募阶段就算了」,
    #     于是什么时候跑到这里都安全(不是招募阶段就安安静静过去, 不中断剧本)。
    "endRecruit": {
        "fields": [
            {"key": "optional", "label": "不在招募阶段就算了(不中断)", "type": "bool",
             "default": True,
             "hint": "强烈建议勾着。不勾的话, 万一跑到这里时已经进战斗了, 剧本会【中断】—— "
                     "而「什么时候跑到这里」取决于服务器结算快慢, 不是你能完全控制的"},
            {"key": "text", "label": "备注", "type": "text", "hint": "只写进日志, 可留空"},
        ],
    },
    # v4.176★ 跳过三连合成动画。
    #   桥接侧做法(见 sgs_bridge.user.js 的 sgsSkipCompositeAni): 先调节点自己的完成回调
    #   (close/complete/…), 叫不动才 destroy(), 最后【强制复位】IsPlayCompositeAni / CanOperate。
    #   ⚠ 最后那步复位不能省: 只撕节点不复位的话, 剧本侧 _scBusy() 会永远判"正忙",
    #     后面每一步都卡在等动画上 —— 比不跳过更糟。
    #   ⚠ 这一步【不等待】: 跳完立刻往下走, 要不要再等由后面的「等待」自己写
    #     (项目规矩: 步骤里不塞内部等待)。
    "skipAni": {
        "fields": [
            {"key": "when", "label": "什么时候动手", "type": "combo",
             "values": [("always", "总是(没在播也顺手复位一次)"),
                        ("playing", "只在确实在播动画时(没播就跳过本步)")],
             "default": "always",
             "hint": "选「只在确实在播」= 没播动画时这一步什么都不做, 直接过去, 不摸舞台"},
            {"key": "optional", "label": "不在对局里就算了(不中断)", "type": "bool",
             "default": True,
             "hint": "勾着: 拿不到 manager 时只记一句、不中断剧本。不勾: 算失败并中断"},
            {"key": "text", "label": "备注", "type": "text", "hint": "只写进日志, 可留空"},
        ],
    },
    # v4.176★ 禁用三连(囤卡)。桥接侧 _scOpHoldNoCombine(同 Tab 那套 window.blockTripleCombine):
    #   不写 enable 默认 = 禁用(囤卡); 写 enable=false = 放开(恢复正常合成)。
    #   放开时会顺手 scheduleHandRefresh(1) 刷一次手牌, 否则刚放开的那次三连要等下次刷新才被识别
    #   (与 Tab 一致)。本步不等待。
    "holdNoCombine": {
        "fields": [
            {"key": "enable", "label": "禁用三连(囤卡)", "type": "bool", "default": True,
             "hint": "勾着 = 禁用三连(买了先不自动合, 囤着); 不勾 = 允许三连(恢复正常合成)。"
                     "想【一直】生效请用「节奏」页那个相同开关, 剧本这一步适合临时切"},
            {"key": "text", "label": "备注", "type": "text", "hint": "只写进日志, 可留空"},
        ],
    },
    # v4.66★ 挑空并进入下一轮 —— 一轮干完的标准收尾(999 那套以前要手拼 4~5 步)
    "drainNext": {
        "fields": [
            {"key": "maxRounds", "label": "最多答几个窗口", "type": "int", "default": 8,
             "hint": "挑牌窗可能连着弹(合金/招募); 答满这么多个就收手, 防止死循环"},
            {"key": "next", "label": "挑空后跳到下一轮", "type": "bool", "default": True,
             "hint": "默认勾着 = 挑完直接进下一轮循环(等于以前手写的「跳过本次循环」); "
                     "不勾 = 只挑空, 继续往下走"},
            {"key": "text", "label": "备注", "type": "text", "hint": "只写进日志, 可留空"},
        ],
    },
    "wait": {
        "fields": [
            {"key": "ms", "label": "等多少毫秒", "type": "int", "req": True, "default": 500,
             "hint": "1000 毫秒 = 1 秒"},
        ],
    },
    # ================= v4.68★ 「设置变量」怎么用 =================
    # 用户原话: "然后就是「设置变量」怎么用? 脚本端传过来的【数组】怎么用?"
    #   以前的提示只有一句"填数字, 或写 gold/turn/...", 讲了值的语法, 却没讲
    #   【在哪儿读它】和【什么时候会没】—— 而这两件才是"用不明白"的真正原因。
    #   所以这一版把三件事全写进控件提示:
    #     ① 值能写什么(数字 / 现成的量 / 别的变量 / 一串名字);
    #     ② 在哪儿读它(条件选「变量」; 任何要填数字的地方也能直接写变量名);
    #     ③ 什么时候会没(只在这一次开跑内有效, 重新开跑从 0 开始)。
    #   顺带把值从"只能是数"放开成"也能是文本/列表" —— 这就是"数组怎么用"的答案:
    #     值写 孙策,周瑜,孙尚香 → 存成列表; 条件里选「变量」+「有几项」(判够不够3个)
    #     或 +「里面有没有某个词」(判抽到孙策没)。
    #   ⚠ 不放开的话老代码会把它【按 0 存】, 只留一条很快被刷掉的 ⚠ 日志。
    "setVar": {
        "fields": [
            {"key": "name", "label": "变量名", "type": "text", "req": True,
             "hint": "自己起个名字(如 v1 / 连败次数 / 要留的卡)。"
                     "剧本里到处都能读它: 条件里选「变量」, 或任何要填数字的地方直接写这个名字"},
            {"key": "value", "label": "设成什么", "type": "text", "req": True,
             "hint": "四种写法都行: "
                     "① 数字: 0 / 3 / -1; "
                     "② 现成的量: gold(金币) turn(回合) level(等级) hp(血量) "
                     "elapsed(剧本开跑到现在过了几秒) now(当前时间戳); "
                     "③ 别的变量名: v2(把它的值拷过来); "
                     "④ 一串名字/一段文本: 孙策,周瑜,孙尚香 → 存成【列表】, "
                     "之后条件选「变量」+「有几项」= 判够不够3个, 或 +「里面有没有某个词」= 判抽到孙策没。"
                     "⚠ 变量只在【这一次开跑】里有效: 停止或重新开跑就从 0 开始, 不会跨局留着"},
        ],
    },
    "addVar": {
        "fields": [
            {"key": "name", "label": "变量名", "type": "text", "req": True,
             "hint": "要加减的那个变量(先用「设置变量」建它, 或这里当场按 0 起算)"},
            {"key": "value", "label": "加减多少", "type": "text", "req": True,
             "hint": "可以是负数(如 -1); 也可以写 gold/别的变量名。"
                     "变量不存在时按 0 算。"
                     "⚠ 变量如果是【列表/文本】(设置变量写的那些名字), 加减对它没意义, "
                     "会按 0 起算并在日志里说明"},
        ],
    },
    # v4.??★ 统计计数(L3) —— 把范围内每张武将按【名字】计数, 金/普通分两桶存进变量
    #   (cnt:孙策:normal / cnt:孙策:gold), 之后「如果」里选「变量」+「数值/项数」就能判
    #   "抽齐 3 张金孙策了吗"。存法对齐沙盒 core/scenario_sandbox.py 的 tallyGen 分支。
    "tallyGen": {
        "fields": [
            {"key": "scope", "label": "统计范围", "type": "combo",
             "values": [("手牌", "hand"), ("阵上", "field"), ("手牌+阵上", "both")],
             "default": "both",
             "hint": "数哪里的卡"},
            {"key": "names", "label": "只数这些名(留空=全部)", "type": "names",
             "hint": "卡名逗号分隔; 留空=统计范围内所有卡"},
            {"key": "out", "label": "变量前缀", "type": "text", "default": "cnt",
             "hint": "计数存进 cnt:孙策:normal 这样的变量(前缀可改, 如 my)"},
            {"key": "goldSeparate", "label": "金/普通分开计数", "type": "bool", "default": True,
             "hint": "勾=金和普通分别记(cnt:孙策:gold / cnt:孙策:normal); 不勾=只记总数"},
        ],
    },
    # v4.??★ 锚点 mark(L4) —— 给「失败跳转 onFailJump」定位: 某步失败时不用数步号,
    #   填这个锚点名就跳回来继续。引擎把它记成"当前下标", onFailJump 按名字找回。
    "mark": {
        "fields": [
            {"key": "name", "label": "锚点名", "type": "text", "req": True,
             "hint": "给这个锚点起个名(如 retryPoint), 别的步骤的「失败跳转」填这个名字就跳回来"},
            {"key": "text", "label": "备注", "type": "text",
             "hint": "只写进日志, 可留空"},
        ],
    },
    # ================= 复用 / 收尾 / 提示 =================
    "callScenario": {
        "fields": [
            {"key": "target", "label": "调用哪个剧本", "type": "scenario", "req": True,
             "hint": "它会就地跑完再回到本剧本继续; 那个剧本里的「中止」会把本剧本一起带走; 最多套5层"},
            {"key": "optional", "label": "找不到也继续", "type": "bool", "default": False},
        ],
    },
    "abort": {
        "fields": [
            {"key": "target", "label": "中止原因", "type": "text",
             "hint": "只写进日志(可留空); 后面的步骤都不跑, 直接收尾"},
        ],
    },
    "toast": {
        "fields": [
            {"key": "text", "label": "显示什么", "type": "text", "req": True,
             "hint": "弹在游戏页面上的提示"},
        ],
    },
    "log": {
        "fields": [
            {"key": "text", "label": "写什么", "type": "text", "req": True,
             "hint": "只写进日志, 不弹提示"},
        ],
    },
}

# ============================================================================
# v4.66★ 「失败重试」—— 每个真正干活的步骤都自动带上这两个字段。
#
#   为什么要有: 买卡/上阵/用锦囊失败太常见了(点太快、上一张卡还在结算、回合切换中),
#   而以前只有两种选择 —— 「跳过(optional)」或者「整剧本中断」, 想重试只能把同一个步骤
#   复制好几遍。现在一个字段搞定。
#
#   为什么用"全局追加"而不是逐个步骤去写: schema 有 30 多个步骤, 逐个加一定会漏
#   (optional 那个坑就是漏出来的: 脚本端认、界面没声明 → 编辑一次就被抹掉)。
#   这里统一补, 新增步骤类型也自动带上。
#
#   注意: 只加给"会真去操作游戏"的步骤 —— 如果/循环/跳出这类纯流程步骤执行时必然成功
#   (它们不进 _scRunOne), 给它们加重试字段只会让人以为"重试有用"。
# ============================================================================
SCENARIO_RETRY_FIELDS = [
    {"key": "retry", "label": "失败重试几次", "type": "int", "default": 0, "omitIfDefault": True,
     "hint": "默认 0 = 不重试(和以前一样)。填 2 = 失败后再试 2 回, 还是失败才按"
             "「中断/跳过」处理。重试前会先把手上的挑牌窗答掉 —— 多数失败就是被窗口卡住的"},
    {"key": "retryMs", "label": "重试前等多久(毫秒)", "type": "int", "default": 500,
     "omitIfDefault": True, "showWhen": ("retry", ("__set__",)),
     "hint": "给游戏一点时间把上一次操作结算完; 填了重试次数才会显示这一行"},
]
# 纯流程步骤: 它们不执行游戏操作, 不加重试
SCENARIO_NO_RETRY_OPS = ("ifStart", "else", "ifEnd", "loopStart", "break", "continue",
                         "loopEnd", "drainNext")
for _op_k, _sch_v in SCENARIO_STEP_SCHEMA.items():
    if _op_k in SCENARIO_NO_RETRY_OPS:
        continue
    _flds = _sch_v.setdefault("fields", [])
    if not any(f.get("key") == "retry" for f in _flds):
        _flds.extend([dict(f) for f in SCENARIO_RETRY_FIELDS])

# 旧版遗留: 早期"新步骤用法提示"字典。现在每类步骤的提示都写在 SCENARIO_STEP_SCHEMA 的
#   字段 hint 里(贴着输入框显示, 比堆在对话框底部好读), 这里保留一个空表只为兼容旧引用。
SCENARIO_NEWOP_HINT = {}

# 个别字段在历史剧本里用的是另一个名字 —— 读进来时兜一下(写回去一律用标准名)。
#   ("op", "标准字段名") -> "旧字段名"
SCENARIO_FIELD_ALIASES = {
    ("recycle", "targets"): "target",     # 早期把遣散目标写进 target
    ("abort", "target"): "text",          # 早期用 text 存中止原因
    ("toast", "text"): "target",          # 早期用 target 存提示文字
}


def _scenario_split_names(txt):
    """'孙策, 周瑜' → ['孙策','周瑜']; 已有 list 原样清洗; 空 → []。

    分隔符收得比较宽(中英文逗号/分号/斜杠/空白) —— 用户从别处粘贴过来的名单什么样式都有。
    注意: 不能按数字切, 因为"顺手牵羊三"这种名字自带数字。

    v4.69★ 「优先级」写法(A > B)先做一次归一: 把 > 两边的空格吃掉再切 ——
      否则空格会被当分隔符, "瞒天过海·六 > 联军盛宴·五" 会被切成
      ['瞒天过海·六', '>', '联军盛宴·五'] 三条, 存进 JSON 里多出一个孤零零的 ">"
      (功能上侥幸还能用, 但表格里那一列会显示成"瞒天过海·六, >, 联军盛宴·五",
       看着像用户自己写错了)。
    """
    import re as _re
    if isinstance(txt, (list, tuple)):
        return [str(x).strip() for x in txt if str(x).strip()]
    s = _re.sub(r"\s*[>＞]\s*", ">", str(txt or "").strip())
    return [x for x in _re.split(r"[,，;；/\s]+", s) if x]


def _scenario_split_spell_plan(v):
    """「用尽锦囊·逐条」的一串用法 → [{'name','on','enabled','no'}, ...]。

    ★ 这份必须与脚本端 sgs_bridge.user.js 的 _scParseSpellPlan【口径逐字对齐】——
      真正执行的是它, 这份只用于摘要/预览/体检。两边一旦漂移, 现象是
      "摘要里看着对、实际指到别处去了" —— 最坏的一类错(比直接报错难查得多)。
    口径(v4.68★):
      · 输入既可能是字符串("A=阵上1, B=牌库1"), 也可能是早期版本存下来的数组;
      · 分隔符: , ， ; ； 和换行(界面那个框是单行的, 用户只能按逗号分隔);
      · 开头 # ! / ／ 或 "停用/不用/禁用:" = 这一条不执行(其余照跑);
      · 等号支持 = → -> | 四种写法, 右边留空 = 交给脚本自动轮目标;
      · ⚠ 作用目标里别再写逗号(如「手牌:孙策,周瑜」会被切成两条) —— 这条写进 hint 了;
      · v4.??★ 作用目标后面还能带一个「挑:类别:第几个/名字」= 这张【用完还要再选】时挑哪一个
        (草船借箭=牌库1 挑:武将:2)。这一段在这里被切成 pickKind/pickSpec, 不留在 'on' 里。
    """
    import re as _re
    if isinstance(v, (list, tuple)):
        items = [str(x) for x in v]
    else:
        items = _re.split(r"[\r\n,，;；]+", str(v or ""))
    out = []
    for i, raw in enumerate(items):
        s = str(raw or "").strip()
        if not s:
            continue
        on = True
        if _re.match(r"^[#!／/]", s) or _re.match(r"^(停用|不用|禁用)\s*[:：]?", s):
            on = False
            s = _re.sub(r"^[#!／/]+", "", s)
            s = _re.sub(r"^(停用|不用|禁用)\s*[:：]?", "", s).strip()
        if not s:
            continue
        # v4.??★ 先切掉行末的「挑:…」, 【再】拆 名字=作用目标 —— 与脚本端 _scParseSpellPlan
        #   逐字对齐(分割符同样允许全角/半角冒号)。⚠ 顺序不能反:
        #   「天降神兵 挑:玉玺」这种【没写等号】的行, 先拆目标会把整串当成【名字】,
        #   名字里带着"挑:玉玺"、目标为空 —— 摘要和实际执行就对不上了。
        _pk, _ps = "", ""
        _pt = _re.split(r"\s*挑[:：]\s*", s)
        if len(_pt) > 1:
            s = _pt[0].strip()
            _p = "挑".join(_pt[1:]).strip()
            _pp = _re.split(r"\s*[:：]\s*", _p)
            _pk = str(_pp[0] or "").strip()
            _ps = ":".join(_pp[1:]).strip() or _p
        parts = _re.split(r"\s*(?:=|→|->|\|)\s*", s)
        nm = str(parts[0] or "").strip()
        if not nm:
            continue
        out.append({
            "name": nm,
            "on": " ".join(parts[1:]).strip(),
            "pickKind": _pk,
            "pickSpec": _ps,
            "enabled": on,
            "no": i + 1,
        })
    return out


# v4.??★ 条件「卡的类别」的值 ↔ 中文 —— 下拉选项与摘要/说明列共用同一份。
#   写死在摘要那边的话, 改了下拉忘了改摘要, 表格里就会冒出一个没人认得的值
#   (这类"两处漂移"在这个模块里已经踩过不止一次)。
SCENARIO_CARD_KIND_CN = {"spell": "锦囊", "chess": "武将", "equip": "武器/装备"}


def _scenario_pick_kind_cn(k):
    """把"挑哪类"的取值统一成一个中文词 —— 界面下拉存的是英文码(chess/spell/equip),
    而【计划行里手写的是中文】(挑:武将:2), 两种都得认(这点也是在自测里发现的:
    只认英文码的话, 逐条摘要会把"挑:武将:2"显示成"挑自动:2")。
    认不出来的一律归"自动"(按服务器弹的那类挑), 与实际行为一致。
    """
    k = str(k or "").strip().lower()
    for code, cn in (("chess", "武将"), ("spell", "锦囊"), ("equip", "武器"), ("equipment", "武器")):
        if k == code or (cn and cn in k):
            return cn
    return "自动"


def _scenario_post_pick_cn(s, prefix=" 后续选:"):
    """参数列里那段「用完还要选 → 挑哪类/挑哪张」的短写法。

    四个锦囊步骤 + 摘要列都要用同一句话 —— 做成一份, 免得四处复制后互相漂移
    (模块里好几处注释都点明: 两处口径漂移是"看着对、实际跑偏", 最难查)。
    两个字段都没写时返回空串 = 老剧本摘要一个字不变。
    """
    d = s or {}
    k = str(d.get("postPickKind") or "").strip().lower()
    v = str(d.get("postPick") or "").strip()
    if not k and not v:
        return ""
    return "%s%s%s" % (prefix, _scenario_pick_kind_cn(k), (":" + v) if v else "")


def _scenario_plan_pick_cn(p):
    """「逐条/按用法」每一行行末那个「挑:武将:2」的短写法(没有就返回空串)。"""
    d = p or {}
    k = str(d.get("pickKind") or "").strip().lower()
    v = str(d.get("pickSpec") or "").strip()
    if not k and not v:
        return ""
    return "(选%s%s)" % (_scenario_pick_kind_cn(k), ("/" + v) if v else "")


# ===========================================================================
# v4.151★ 「按卡分别定」(shopFind.cardPlan) —— 一张卡一行的【张数 + 凑够后】计划表
# ===========================================================================
#   由来: 用户要在【同一步】里给每张卡各配一套规则 —— 原话:
#     "张宝、张梁、找3张就不买了、张鲁、丁奉、陈珪、有就买; 现在不太友好、自己写不识别、
#      且不好写、可以选择那样; 按卡定可以写是有框可以选择的"
#   以前这个字段是【一个单行输入框】, 得手敲 "张宝 3 off" 这种看不出格式的字符串。
#   两个真问题: ① 单行框里打不了回车, 用逗号连写("张宝 3 off,张鲁")时脚本端【只认换行】,
#      于是整行被当成同一张卡的第三、四列 → 后面的卡全部不生效("自己写不识别"的根因);
#      ② 卡名还得跟上面「找哪张卡」重复敲一遍, 写错一个字就是静默失效。
#   现在界面给一张小表: 卡名下拉可挑(也能从武将表里点)、张数下拉、"凑够后"下拉;
#   存出来的【仍是同一份多行文本】, 所以旧剧本不用转换、脚本端也不用改。
#
#   ⚠ 与 sgs_bridge.user.js 的 _scOpShopFind(cardPlan 那段)必须口径一致: 那边按 \n 分行,
#     所以这份【序列化只能输出换行分隔】; 解析才放宽(逗号/顿号/分号也当分行), 好把用户
#     以前手写的旧串读回来。
SCENARIO_CARDPLAN_AFTER_PAIRS = [
    ("沿用上面的『凑够后还买吗』", ""),
    ("凑够就绝不再买", "off"),
    ("凑够后看金币(钱够才买)", "gold"),
]
# 第四项: 选中它 = 用【右边的输入框】填金币门槛(不再让用户往下拉里敲裸数字 —— 打错格式这一整列
#   就失效, 而且脚本端按列读, 错了会把它当成张数那一列读歪)。它不进 PAIRS: 存储值是变的。
SCENARIO_CARDPLAN_AFTER_GOLD = "凑够后金币 ≥ 右边填的数"
SCENARIO_CARDPLAN_AFTER_LABELS = [lab for lab, _v in SCENARIO_CARDPLAN_AFTER_PAIRS] \
    + [SCENARIO_CARDPLAN_AFTER_GOLD]
# 「要几张」下拉: 不限 = 有就买(不设上限, 也就谈不上"凑够")
SCENARIO_CARDPLAN_NUM_LABELS = ["不限"] + [str(i) for i in range(1, 10)]


def _scenario_cardplan_after_norm(v):
    """第三列「凑够后」归一成脚本端认的值: '' / 'off' / 'gold' / 数字串。

    先认【下拉里的那句中文】(用户在界面上选到的就是它, 不认的话选了等于没选),
    再认手写的各种简写, 最后才是数字 = 金币门槛。
    """
    s = str(v if v is not None else "").strip()
    for lab, val in SCENARIO_CARDPLAN_AFTER_PAIRS:
        if lab == s:
            return val
    low = s.lower()
    if s in ("off", "0", "false", "不买", "不再买", "不买了", "no", "绝不买"):
        return "off"
    if s in ("gold", "按金币", "看金币", "钱够才买", "看情况"):
        return "gold"
    if not s or low in ("none", "-") or s in ("沿用", "沿用上面", "沿用上面的规则"):
        return ""
    n = _scenario_safe_int(s, -1)
    return str(n) if n >= 0 else ""


def _scenario_cardplan_after_label(v):
    """存储值 → 下拉里显示的中文。手输的数字(金币门槛)原样显示。"""
    s = _scenario_cardplan_after_norm(v)
    for lab, val in SCENARIO_CARDPLAN_AFTER_PAIRS:
        if val == s:
            return lab
    return s


def _scenario_cardplan_after_cn(v):
    """存储值 → 一句话(步骤说明里用)。"""
    s = _scenario_cardplan_after_norm(v)
    if s == "off":
        return "凑够不再买"
    if s == "gold":
        return "凑够后看金币"
    if s:
        return "凑够后金币≥%s" % s
    return "沿用上面的规则"


def _scenario_split_cardplan(v):
    """cardPlan 的存储形态(多行文本 / 字符串数组) → [{'name','num','after'}]

    解析口径放宽(写). num=0 表示【不限 / 有就买】; after='' 表示沿用整步的 buyAfterFull。
    ⚠ num<=0 时强制 after='' —— 没有"要几张"就没有"凑够", 而且脚本端是按列位置读的,
      写成 "张鲁 gold" 会被它当成【张数那一列】(parseInt 失败)而不是第三列。
    """
    txt = "\n".join(str(x) for x in v) if isinstance(v, (list, tuple)) else v
    rows, seen = [], []
    for raw in re.split(r"[\n;；]+", str(txt or "")):
        line = str(raw or "").strip()
        if not line:
            continue
        # 一行里用逗号连写多张("张宝 3 off,张鲁")拆分 —— 用户手写的旧串常见这种写法
        for seg in re.split(r"[,，、]+", line):
            seg = str(seg or "").strip()
            if not seg:
                continue
            ps = seg.split()
            nm = str(ps[0] or "").strip()
            # 名字自带张数的老写法: 张宝*3 / 张宝:3 / 张宝x3
            m = re.match(r"^(.*?)\s*[*xX：:]\s*(\d+)\s*$", nm)
            num_txt = ""
            if m and m.group(1).strip():
                nm, num_txt = m.group(1).strip(), m.group(2)
            toks = ([num_txt] if num_txt else []) + [str(x) for x in ps[1:]]
            num, after = 0, ""
            if toks:
                n = _scenario_safe_int(toks[0], -1)
                if n > 0:
                    num = n
                    if len(toks) > 1:
                        after = toks[1]
                else:
                    after = toks[0]          # 没写张数直接写 off/gold 的情况
            if num <= 0:
                after = ""
            after = _scenario_cardplan_after_norm(after)
            if nm and nm not in seen:
                seen.append(nm)
                rows.append({"name": nm, "num": num, "after": after})
    return rows


def _scenario_cardplan_text(rows):
    """[{name,num,after}] → 脚本端认的多行文本(一行一张: 卡名 [张数] [凑够后])。"""
    out = []
    for r in (rows or []):
        nm = str((r or {}).get("name") or "").strip()
        if not nm:
            continue
        cols = [nm]
        num = _scenario_safe_int((r or {}).get("num"), 0)
        if num > 0:
            cols.append(str(num))
            af = _scenario_cardplan_after_norm((r or {}).get("after"))
            if af:
                cols.append(af)
        out.append(" ".join(cols))
    return "\n".join(out)


def _scenario_cardplan_brief(s, targets=None):
    """步骤说明里那一句「各要几张」—— 没配过就返回空串(不占地方)。

    v4.151★ targets = 这步要找的卡。没写进 cardPlan 的卡【照样会被找、会被买】
    (走的是整步统一的「每种最多买几张」), 所以也要在说明里念到, 否则用户只能看到
    一半规则 —— 表里有张宝、张梁, 就以为张鲁/丁奉/陈珪不买了。
    """
    rows = _scenario_split_cardplan((s or {}).get("cardPlan"))
    have = [str(r.get("name")) for r in rows]
    for nm in (targets or []):
        nm = str(nm or "").strip()
        if not nm:
            continue
        num = 0
        m = re.match(r"^(.*?)\s*[*xX：:]\s*(\d+)\s*$", nm)
        if m and m.group(1).strip():
            nm, num = m.group(1).strip(), _scenario_safe_int(m.group(2), 0)
        if nm in have:
            continue
        have.append(nm)
        rows.append({"name": nm, "num": num, "after": ""})
    if not rows:
        return ""
    parts = []
    for r in rows:
        n = _scenario_safe_int(r.get("num"), 0)
        if n > 0:
            parts.append("%s %d张(%s)" % (r.get("name"), n,
                                          _scenario_cardplan_after_cn(r.get("after"))))
        else:
            parts.append("%s(有就买)" % r.get("name"))
    return "、".join(parts)


def _scenario_to_bool(v, default=False):
    """控件值 → bool。兼容手输的 '0/false/no/否/不' 这几种写法。"""
    if isinstance(v, bool):
        return v
    if v is None or str(v).strip() == "":
        return default
    return str(v).strip().lower() not in ("0", "false", "no", "off", "否", "不", "不买", "取消")


def _scenario_val_set(v):
    """showWhen 里「填了没」的判据: 空 / 0 / false / 否 都算没填。"""
    if v is None:
        return False
    if isinstance(v, bool):
        return v
    return str(v).strip() not in ("", "0", "false", "False", "off", "no", "否", "不")


def _scenario_show_when_ok(sw, vals):
    """showWhen 判定 —— 「这个字段现在该不该显示」只留这一份实现。

    编辑框生成控件、预览、组装存盘三处都要问同一个问题, 以前在
    `_scenario_cond_visible` 和 `_scenario_visible_fields` 里各写了一遍(迟早跑偏), 现在共用。

    sw 支持四种写法:
      · (依赖字段, 允许取值元组)                    —— 老写法: 当前值在里面才显示
      · [(字段A, 取值元组), (字段B, 取值元组)]      —— 列表 = 【或】, 任一条成立就显示
      · 取值元组里写 "__set__"                      —— "只要填了(非空、非0)就显示"
      · {"all": [上面任意写法, ...]}                —— v4.68★ 【与】: 每一条都成立才显示
    为什么需要后两种: 「比较」这个字段在【数值形态】和【数量形态】下都要出现, 而这两种形态
    是由不同字段触发的(一个看 check, 一个看 count 填没填) —— 老写法只能依赖一个字段,
    于是"数量"那个条件判不了大小, 等于白加。
    为什么还要"与"(v4.68★): 「变量」形态又分了三小类(比数值 / 数它有几项 / 里面有没有某个词),
      这一层收窄是【跨字段】的 —— check=var 且 varAs≠has 才显示「比较」/「数值」。
      老写法表达不了"与", 只能把不该显示的那一行也显示出来; 而界面上多出来的一行
      一定会被当成"这里要填", 直接把用户带偏(本项目里最忌讳的那类问题)。
    """
    if not sw:
        return True
    if isinstance(sw, dict):
        for _sub in (sw.get("all") or []):
            if not _scenario_show_when_ok(_sub, vals):
                return False
        return True
    pairs = sw if (isinstance(sw, (list, tuple)) and sw and isinstance(sw[0], (list, tuple))) else [sw]
    for dep, allowed in pairs:
        if not dep:
            continue
        cur = vals.get(dep)
        if dep == "check" and dep not in vals:
            cur = "card"          # 老剧本【压根没有】check 这个键 = 判手牌
        # ★注意区分"键缺失"和"键是空串": 循环的「跳出条件」就是用空串表示"不提前跳出",
        #   这里要是把空串也当成 card, 循环开始那几行条件会全部冒出来(见 SCENARIO_CHECK_PAIRS_OPTIONAL)。
        for a in (allowed or ()):
            if str(a) == "__set__":
                if _scenario_val_set(cur):
                    return True
                continue
            if str(cur) == str(a):
                return True
    return False


def _scenario_cond_visible(step, op=None):
    """当前步骤里"条件形态"下【哪些字段是有效的】—— 供预览/描述/脚本对齐用。

    返回集合, 例如 check=card → {"check","has","mode","where"}; check=gold → {"check","cmp","target"}
    规则与编辑框完全一致(同一份 showWhen), 所以"编辑框里看到的"就是"真正生效的"。
    """
    op = str(op or step.get("op") or "")
    schema = SCENARIO_STEP_SCHEMA.get(op) or {}
    vals = {}
    for f in schema.get("fields") or []:
        k = f["key"]
        v = step.get(k)
        if v is None:
            v = f.get("default")
        vals[k] = v
    out = set()
    for f in schema.get("fields") or []:
        if _scenario_show_when_ok(f.get("showWhen"), vals):
            out.add(f["key"])
    return out


def _scenario_cond_brief(s, prefix=""):
    """一句话说清一个条件 —— 表格列 / 说明列共用(以前两处各写一份, 迟早跑偏)。

    例: 手牌 有[孙策] (全部) · 金币 >= 100 · 阶段 = 招募 · 孙策 张数 >= 3 · 非(手牌 有[丁奉])
    """
    chk = str(s.get("check") or "card").lower()
    tgt = s.get("target")
    if isinstance(tgt, (list, tuple)):
        tgt = ", ".join(str(x) for x in tgt)
    tgt = str(tgt or "")

    def _names(v):
        if isinstance(v, (list, tuple)):
            return ", ".join(str(x) for x in v)
        return str(v or "")

    if chk == "card":
        has = _names(s.get("has"))
        # v4.67★ 位置词只留这一处 —— 以前在 brief 里硬编码一份, 新加了「全场已见」,
        #   散着写就会变成"有的地方认得它、有的地方当成手牌"
        wname = SCENARIO_WHERE_CN.get(s.get("where") or "hand", "手牌")
        # v4.??★ 类别: 「哪些卡」留空时它才是【唯一】的判据, 必须说成人话
        #   (脚本端口径: 有点名时 = 点名那一层再叠加类别; 没点名时 = 判这一类有没有)
        kd = SCENARIO_CARD_KIND_CN.get(str(s.get("kind") or "").strip().lower(), "")
        m = "全部" if (s.get("mode") or "all") == "all" else "任意一个"
        # v4.66★ 数量形态: 「哪些卡」留空 = 数总张数(手牌≥8张), 别显示成空名单
        if _scenario_val_set(s.get("count")):
            # v4.67★ 位置必须带上: 「全场已见 ≥ 3 张」和「手牌 ≥ 3 张」完全是两件事
            tag = "" if wname == "手牌" else f"[{wname}]"
            if has:
                what = f"{has} 张数"
            elif kd:
                what = f"{wname}{kd}张数"
            else:
                what = f"{wname}总张数"
            return f"{prefix}{what}{tag} {s.get('cmp') or '>='} {s.get('count')} 张"
        if not has and kd:
            none_mode = (s.get("mode") or "all") == "none"
            return f"{prefix}{wname}{'没有' if none_mode else '有'}{kd}"
        return f"{prefix}{wname} 有[{has}{('且限' + kd) if kd else ''}] ({m})"
    if chk == "stage":
        return f"{prefix}阶段 = {tgt}"
    if chk == "var":
        # v4.68★ 变量有三小类问法(值可能是数, 也可能是列表/文本) —— 摘要都要看得出是哪一类,
        #   否则表格里「变量 v1 >= 3」和「变量 v1 项数 >= 3」长得一样, 核对时根本分不出。
        _vn = s.get("varName") or "?"
        _as = str(s.get("varAs") or "num").strip().lower()
        if _as == "count":
            return f"{prefix}变量 {_vn} 项数 {s.get('cmp') or '>='} {tgt}"
        if _as == "has":
            _w = str(s.get("varText") or "").strip()
            return f"{prefix}变量 {_vn} {'不含' if s.get('negate') else '包含'}「{_w}」"
        return f"{prefix}变量 {_vn} {s.get('cmp') or '>='} {tgt}"
    return f"{prefix}{SCENARIO_CHECK_CN.get(chk, chk)} {s.get('cmp') or '>='} {tgt}"


# v4.??★ 条件组文本解析(L2): 把用户填的 JSON 文本 / 已是结构的值 解析成引擎可吃的结构。
#   合法返回 dict/list, 不合法返回 None(交给 UI 标红、引擎按"不成立"判)。
#   结构形态(与 core/cond_engine.py / sgs_bridge.user.js 同口径):
#     · {"conds":[A,{join:'and',B},{join:'or',C}]}  链
#     · {"all":[A,B]} / {"any":[A,B]}               且组 / 或组
#     · {"op":"and"|"or","items":[A,B]}             显式组(可嵌套)
#     · 单条件 {check:...,has:...}                  (误传整体也兜住)
def _scenario_condgroup_ok(obj):
    if isinstance(obj, list):
        return all(_scenario_condgroup_ok(x) for x in obj)
    if not isinstance(obj, dict):
        return False
    if "conds" in obj:
        return _scenario_condgroup_ok(obj["conds"])
    if "all" in obj or "any" in obj:
        return _scenario_condgroup_ok(obj.get("all") if "all" in obj else obj.get("any"))
    if "op" in obj and "items" in obj:
        return _scenario_condgroup_ok(obj["items"])
    # 单条件: 至少带个 check 才认(否则引擎也只会判"不成立", 这里宽松通过让他能存)
    return True


def _scenario_parse_condgroup(raw):
    """raw: 用户文本 / 已是结构。返回 dict/list 或 None。"""
    if isinstance(raw, (dict, list)):
        return raw if _scenario_condgroup_ok(raw) else None
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    try:
        obj = json.loads(s)
    except Exception:
        return None
    if isinstance(obj, (dict, list)) and _scenario_condgroup_ok(obj):
        return obj
    return None


SCENARIO_OP_LABELS = {k: v for k, v in SCENARIO_OPS}
SCENARIO_LABEL_OPS = {v: k for k, v in SCENARIO_OPS}
# 落点写法说明 (给编辑框当提示)
SCENARIO_POS_HINT = ("开位=a | 固定格=1..7 | 上完后倒数第二格=default | "
                    "某武将右侧=after:孙尚香 | 左侧=before:孙尚香 | 两张同名中间=middle:孙尚香")

# v4.41★ 「挑目标 / 选人」的两个下拉预设: 定义已挪到 SCENARIO_STEP_SCHEMA 之前(它在 schema 里被引用,
#   放在后面会让整个模块导入就报 NameError —— 这是真炸, 不是"顺序无所谓")。
#   顺带记一下当年的坑: 原来「挑目标」那行和上面「上阵后选人」那行【共用了同一个 tk.StringVar】,
#   两个 Entry 绑一个变量会互相顶, 在「挑目标」里打字光标乱跳/刚输入就被覆盖
#   (用户原话"挑目标的目标输入不了")。现在各自一个变量, 谁也不抢谁。


def _scenario_ids_for_names(names):
    """v4.28★ 把剧本目标里的中文名反查成 chessID 列表, 随步骤一起下发给页面。

    为什么必须带 ID: 页面端按【中文名】匹配的前提是它能解析出卡的中文名, 而名字来自
    Python 推过去的武将映射(load_chess_name_map 只在【建立连接】时推一次)。页面若是在
    助手连上之后才注入/刷新, 那份映射就是空的 —— 卡的名字会退化成 "21003241" 这种 ID 串,
    于是用户填的中文名一个都匹配不上, 现象就是"手牌里明明有『董袭』却报『没找到要遣散的』"。

    chessID 是 ChessConf 的主键(如 董袭 = 21003241), 手牌/阵上/牌库里的卡都带这个字段,
    用它匹配完全不依赖名字解析。一个名字可能对应多个 ID(各赛季/星级), 全部带上, 匹配更宽。
    """
    out = []
    for nm in (names or []):
        s = str(nm or "").strip()
        if not s:
            continue
        if s.isdigit():                      # 用户直接填了 ID, 原样带上
            if s not in out:
                out.append(s)
            continue
        for cid in IDMAP.find_ids_by_name(s):
            cid = str(cid)
            if cid and cid not in out:
                out.append(cid)
    return out


def _expand_list_ids(entries):
    """v4.55★ 把「面板里勾了某个武将」展开成「这个武将的全部赛季 ID」。

    为什么必须展开(实测根因, 用户报"有的能拿下来, 有的不能"):
      同一张武将在不同赛季/形态下有【多个 chessID】。实测 data/chess.json:
        『张星彩』20 个(20201021 … 21002021), 『陈武』12 个(20503021 … 21003022),
        『左慈』/『张宁』各 20 个。
      而「多选购买」右侧列表只存用户勾的【那一个】(如 21002021), 页面端
      findTargetsInShop 是按 chessID【严格相等】匹配的 —— 商店刷出
      20902011 / 21002022 这些"同一个张星彩的别的 ID"时永远匹配不上。
      实机现象就是"有的能拿下来, 有的不能": 拿得下来的那次, 纯属勾中的 ID 刚好
      等于商店刷出的 ID。页面端那句名字兜底也救不了 —— 目标串是纯数字 "21002021",
      卡名是中文 "张星彩", nm.indexOf(key) 恒不成立。

    做法: 只对"名字能反查出列表里已有 id"的条目做展开 ——
      · 面板里正常加的武将, name 来自 chess 库, find_ids_by_name(name) 必含它的 id → 展开;
      · 手动填的裸 ID(如随征技能 942311, 其 name 是反查来的『黄巾兵』), 反查结果是
        8 位棋子 ID、不含 942311 → 判定为"另一套 ID 空间", 只保留原 id, 不污染目标表。
    返回去重后的 ID 列表, 【原 ID 永远排在最前】—— 即使展开整个失效, 也不会比以前更差。
    """
    ids, names = [], []
    for e in (entries or []):
        if not isinstance(e, dict):
            continue
        _id = str(e.get("id") or "").strip()
        if _id and _id not in ids:
            ids.append(_id)
        _nm = str(e.get("name") or "").strip()
        if _nm and _nm not in names:
            names.append(_nm)
    if not ids:
        return []
    extra = []
    for _nm in names:
        if _nm.isdigit():                       # 名字本身就是 ID, 没有可展开的东西
            continue
        try:
            found = [str(x) for x in IDMAP.find_ids_by_name(_nm)]
        except Exception:
            found = []
        # 名字必须能反查出"列表里已有的某个 id", 才认定这是同一张卡的多赛季 ID。
        #   这道闸门是为了不把 942311(随征技能) 这类异构 ID 一起带进来。
        if not any(f in ids for f in found):
            continue
        for f in found:
            if f and f not in ids and f not in extra:
                extra.append(f)
    return ids + extra


def _scenario_spell_ids_for_names(names):
    """v4.30★ 把锦囊名反查成 SpellID 列表(见 id_map.find_spell_ids_by_name)。

    与 _scenario_ids_for_names 的区别: 那个查的是武将 chessID(8位, ChessConf 主键),
    锦囊要用 SpellID —— 两套 ID 完全不通, 混用只会给出根本匹配不上的值。

    v4.69★ 支持【优先级】写法: "瞒天过海·六 > 联军盛宴·五" 要拆成两段各自反查 ——
      否则整串拿去查必然查不到, 下发一个空的 targetIds, 页面就只能靠名字匹配(更容易落空)。
      ⚠ 顺序保留: 页面端会按先后顺序找手牌里的锦囊(见脚本 _scOpUseSpell 的优先级分支)。
    """
    out = []
    for nm in (names or []):
        s = str(nm or "").strip()
        if not s:
            continue
        for _alt in [x.strip() for x in re.split(r"[>＞]", s) if x.strip()]:
            if _alt.isdigit():               # 用户直接填了 ID, 原样带上
                if _alt not in out:
                    out.append(_alt)
                continue
            for sid in IDMAP.find_spell_ids_by_name(_alt):
                sid = str(sid)
                if sid and sid not in out:
                    out.append(sid)
    return out


def _is_slot_spec(t):
    """v4.38★ 「3 / 第3格 / slot:3」这种【阵上序号】写法(既不是武将名也不是 ID)。

    序号只对【阵上】(1..7 格)有意义 —— 手牌没有格号。页面端 v4.38 起按同一套规则解析
    (见 _scSlotKeys): 遣散/条件下的"要作用到阵上第几格"。
    v4.29 起只有 pick(选人目标)认这套写法, 遣散的目标填 3 会被当成 ID 原样下发,
    页面按 ID 比必然落空(『没找到要遣散的: 3』) —— 所以两个字段共用这一个判据。
    """
    t = str(t or "").strip()
    d = "".join(ch for ch in t if ch.isdigit())
    other = "".join(ch for ch in t if not ch.isdigit() and ch not in " slotSlot=:第格")
    return bool(d) and not other and len(d) == 1 and d in "1234567"


def _scenario_pick_is_spec(pk):
    """v4.29★ 「选人目标」填的是不是【位置开关】而不是武将名。

    auto/first/any/自动 = 场上第一张非同名友方; 3 / 第3格 / slot:3 = 指定格子。
    这两种都不该拿去反查 chessID, 也不该在"名字查不到"里误报。
    """
    t = str(pk or "").strip()
    if not t:
        return True
    if t.lower() in ("auto", "first", "any", "自动"):
        return True
    return _is_slot_spec(t)


def _scenario_cond_prefix(name):
    """条件里的一项是否带「位置前缀 / 否定前缀」(写法见 bridge 的 _scCondKeys)。

    为什么助手端也要认一遍: 反查 chessID 时, 带前缀的字符串必然查空 —— 库里没有
    「手牌:孙策」这种卡名。而更糟的是【剥掉前缀之后的 ID 也不能混进 targetIds】:
    那份 ID 是"没有位置信息"的, 页面会拿它按条件里的 where 去判 —— 于是
    「阵上:孙尚香」会被【手牌里】的孙尚香 ID 满足, 条件直接判错, 比查不到还危险。
    所以: 带前缀的项只靠页面端的名字匹配, 助手端只把【不带前缀】的名字转成 ID。
    """
    import re as _re
    t = str(name or "").strip()
    if not t:
        return False
    if t[0] in "!！-":
        return True
    if t.startswith(("没有", "不带", "不在", "排除")):
        return True
    return bool(_re.match(r"^(手牌|手里|手上|hand|阵上|场上|field|both|任意|any)\s*[:：]", t, _re.I))


def _scenario_shopfind_target_names(step):
    """牌库找卡的逻辑目标名: 去掉每张卡自己的张数后缀, 并包含按卡规则里的目标。"""
    out = []
    raw = step.get("target") or []
    for name in _scenario_split_names(raw):
        match = re.match(r"^(.*?)\s*[*xX：:]\s*(\d+)\s*$", name)
        name = match.group(1).strip() if match and match.group(1).strip() else name
        if name and name not in out:
            out.append(name)
    for row in _scenario_split_cardplan(step.get("cardPlan")):
        name = str(row.get("name") or "").strip()
        if name and name not in out:
            out.append(name)
    return out


def _scenario_shopfind_target_groups(step):
    """生成仅供游戏页执行用的目标分组, 把同名的不同赛季/品质 ID 归到一组。"""
    groups = []
    for name in _scenario_shopfind_target_names(step):
        aliases = [name]
        for cid in _scenario_ids_for_names([name]):
            cid = str(cid)
            if cid and cid not in aliases:
                aliases.append(cid)
        groups.append({"key": name, "aliases": aliases})
    return groups


def _scenario_fill_target_ids(s):
    """给一个步骤里的中文目标补上 chessID(就地修改并返回)。编辑对话框和预设导入都走这里。

    页面端优先按 ID 匹配, 所以只要目标里有中文名就顺手带上对应 chessID。
    useSpell 的目标是【锦囊】: 走锦囊库反查 SpellID —— 不能拿武将 chessID 顶替,
    两套 ID 完全不通, 混用只会给出根本匹配不上的值(见 _scenario_spell_ids_for_names)。
    """
    # v4.56★ 阵营批量遣散: 把"吴国"这种阵营名【展开成具体名单】再下发 —— 脚本端不内置武将表
    #   (赛季新增武将时只改助手端这一处, 不用回头改脚本), 所以展开放在这里。
    #   注意只在 campNames 为空时填充: 用户手填过的名单优先, 不能被内置的覆盖掉。
    if str(s.get("op") or "") in ("recycle", "followUp"):
        # v4.64★ 阵营展开 + 排除名单反查 —— 原来只在 recycle 上做, 现在「随征」也要:
        #   随征用的是同一套「阵营名单 ∪ 额外点名 − 排除名单」, 不展开的话跟 999-B 那条
        #   「按阵营清场」一样只剩个空目标。两处共用这一段, 免得以后改一处忘一处。
        _camp = str(s.get("camp") or "").strip()
        if _camp and not str(s.get("campNames") or "").strip():
            # v4.64★ 现读配置表拿该势力的全部棋子(见 _scenario_camp_members 上面那段说明)
            _mem = _scenario_camp_members(_camp)
            if _mem:
                s["campNames"] = ",".join(_mem)
        # 排除名单也反查一份 ID: 页面端优先按 ID 匹配, 命中更稳(名字靠归一化比对, ID 不会错认)
        _ex = s.get("exclude") or []
        if isinstance(_ex, str):
            _ex = [_ex]
        _eids = _scenario_ids_for_names(_ex)
        if _eids:
            s["excludeIds"] = _eids
    if str(s.get("op") or "") == "followUp":
        # v4.64★ 随征还有第三个"名字"字段:「用哪张随征卡」—— 同样反查一份 ID 下发
        _cn = s.get("cards") or s.get("card") or []
        if isinstance(_cn, str):
            _cn = [_cn]
        _cids = _scenario_ids_for_names([x for x in _cn if str(x).strip()])
        if _cids:
            s["cardIds"] = _cids
    if str(s.get("op") or "") == "sellBelow":
        # v4.190★「卖到只剩」的【保护名单】也要带 ID。
        #   以前只下发文字: 页面端 _scOpSellBelow 用 _scKeys(protect, protectIds) 匹配,
        #   名字表没提到页面(或名字带间隔号/别名)时保护名单会【静默失效】——
        #   后果不是"少卖几张", 而是把要留的主力(丁奉/凌统)当杂牌卖掉。
        #   这里补一份 protectIds, 页面优先按 ID 命中。
        _pn = s.get("protect") or []
        if isinstance(_pn, str):
            _pn = [x for x in _pn.split(",")]
        _pids = _scenario_ids_for_names([str(x).strip() for x in _pn if str(x).strip()])
        if _pids:
            s["protectIds"] = _pids
    if str(s.get("op") or "") == "recycleSpell":
        # v4.81★ 遣散锦囊: 两个名字字段都是【锦囊名】—— 必须走锦囊库反查 spellID,
        #   绝不能落到下面那段【武将】反查里去(两套 ID 完全不通, 混用只会给出匹配不上的值,
        #   现象就是"填了名字却一张都没遣散")。
        for _key, _val in (("targetIds", s.get("targets") or s.get("target")),
                           ("excludeIds", s.get("exclude"))):
            if isinstance(_val, str):
                _val = [_val]
            _ids = _scenario_spell_ids_for_names([x for x in (_val or []) if str(x).strip()])
            if _ids:
                s[_key] = _ids
        return s
    if s.get("op") in ("useSpell", "useSpellIf"):
        # v4.30★ 锦囊也要带 ID(原来这里是直接 return):
        #   库里/配置表里写的是『顺手牵羊·三』(带间隔号), 用户照游戏画面填『顺手牵羊三』,
        #   页面只按名字匹配就会落空 → 报"手牌中无锦囊: 顺手牵羊三"。
        #   这里把该名字对应的所有 SpellID 随步骤下发, 页面拿 spellID 精确比, 绕开写法差异。
        snames = s.get("target")
        if isinstance(snames, str):
            snames = [snames]
        sids = _scenario_spell_ids_for_names(snames)
        if sids:
            s["targetIds"] = sids
        return s
    # v4.47★ 这些步骤的「目标」不是卡名: 剧本名 / 锁·解锁 / 中止原因 / 条件里的数字与阶段名。
    #   拿它们去武将表反查不但白费, 还可能把"跟卡名同名的剧本"错配成一份 chessID 下发。
    if str(s.get("op") or "") in ("callScenario", "lockShop", "abort",
                                   # v4.107★ 这几个的「文字」不是卡名(日志/提示/打点标记)
                                   "recordResult", "pauseForUser", "beep"):
        return s
    # v4.55★ waitUntil 和 ifStart 是同一套条件写法(共用 _scenario_cond_fields) → 判据也必须一样,
    #   否则「等到 招募阶段」会被当成卡名去反查 chessID, 平白下发一份没用的 targetIds。
    if str(s.get("op") or "") in ("ifStart", "waitUntil") and str(s.get("check") or "card").lower() != "card":
        return s
    # v4.190★ 目标字段有三种写法: targets 列表 / has 列表 / target（字符串【或列表】）。
    #   以前 target 是列表时会被套成 [[...]]，_scenario_ids_for_names 拿到的是个 list 元素
    #   → 反查必然落空 → deploy 一条 targetIds 都补不出来。而内置预设里 target 全是列表写法
    #   （{"op":"deploy","target":["孙策"]}），等于「预设导入的上阵步骤一直只靠页面裸比名字」。
    #   这里统一展平一层（字符串/列表都接受）。
    if str(s.get("op") or "") == "shopFind":
        # *N / :N 是张数语法, 不属于卡名; cardPlan 中单独指定的卡也必须带 ID,
        # 否则页面端的名字映射未就绪时会出现"这张卡在货架上, 但找不到"。
        names = _scenario_shopfind_target_names(s)
    else:
        _raw_names = s.get("targets") or s.get("has") or s.get("target") or []
        if isinstance(_raw_names, str):
            names = [_raw_names]
        elif isinstance(_raw_names, (list, tuple)):
            names = list(_raw_names)
        else:
            names = [_raw_names]
    # v4.38★ 目标是【阵上序号】(3 / 第3格 / slot:3)时不要拿去反查 chessID:
    #   序号由页面端按"阵上第几格"处理(遣散从 v4.38 起支持), 塞进 targetIds 只是噪声。
    names = [n for n in names if not _is_slot_spec(n)]
    ids = _scenario_ids_for_names(names)
    if ids:
        s["targetIds"] = ids
    # v4.101★ 注: 「每种买几张」的分组【由页面端按名字自己分】(见脚本 _scOpShopFind 里的说明)。
    #   这里不下发任何分组字段 —— 分组表一旦写进步骤 JSON, 编辑框往返就会把它当成"用户填的"
    #   一项内容(自检 I1 直接失败), 而且老剧本一保存就多出一堆看不懂的键。
    if s.get("onName"):
        oids = _scenario_ids_for_names([s.get("onName")])
        if oids:
            s["onIds"] = oids
    # v4.29★ 「选人目标」(pick) 也可能是武将名/ID —— 同样反查一份, 页面按 ID 匹配更稳
    pk = str(s.get("pick") or "").strip()
    if pk and not pk.isdigit() and not _scenario_pick_is_spec(pk):
        pids = _scenario_ids_for_names([pk])
        if pids:
            s["pickIds"] = pids
    return s


# ============================================================================
# v4.69★ 锦囊名字表 —— 「挑选」按钮与「逐条用锦囊」内置预设共用这一份。
#
# 为什么单独做一份(用户原话: "然后你内置的预设、里面逐条用锦囊, 里面不全啊"):
#   · 内置预设原来手写了 5 条示例, 而本赛季配置表里有 130+ 张锦囊(含品质) ——
#     用户要的是"全都列出来, 我用哪张就留哪张", 不是"照着示例自己补"。
#   · 界面上的「挑选」按钮也需要同一份名字表: 让用户从库里点, 而不是凭记忆手打
#     (手打错一个字, 剧本跑到那一步才发现"手牌中无锦囊")。
#
# 数据来源是 data/tavern_spells.json(助手从 ChessSpellConf.csv 解析出来的赛季锦囊表)。
#   ⚠ 特意【不用】id_map.spell_name_map(): 那一份是从 cards.json 拼的卡名表, 里面混着
#     武将(丁奉/万年公主 都在里面), 拿来当"锦囊清单"会把用户往错的方向引。
# 赛季更新后这张表跟着变 —— 所以预设与挑选框都是【现读】的, 不用手工维护。
# ============================================================================
def _scenario_spell_names():
    """本季全部锦囊名(带品质, 如 瞒天过海·六); 读不到数据就返回空列表。"""
    try:
        p = os.path.join(DATA_DIR, "tavern_spells.json")
        if not os.path.exists(p):
            return []
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return []
    out = []
    for v in (d.values() if isinstance(d, dict) else []):
        n = str((v or {}).get("n") or "").strip() if isinstance(v, dict) else ""
        if n and n not in out:
            out.append(n)
    # 按"基础名 + 品质"排: 同名(珠联璧合·二/三/四)挨在一起, 找起来是"一簇"而不是散在各处
    return sorted(out, key=lambda x: (_scenario_spell_base(x), str(x)))


def _scenario_spell_base(name):
    """瞒天过海·六 → 瞒天过海(没有间隔号的写法原样返回)。"""
    return str(name or "").split("·")[0].strip()


def _scenario_spell_plan_text(sep=", "):
    """整季锦囊的「逐条用锦囊」计划文本 —— 每行一张, 等号右边【留空】。

    ★ 留空不是偷懒: 脚本 v4.69 起, 右边留空 = 按「用尽锦囊」那套顺序自己试目标
      (直接使用 → 牌库1~6格 → 阵上1~7格), 谁能让这张锦囊离手就算谁。
      所以不用替每一张猜"该给谁" —— 想精确控制就在那一条后面补 "=阵上1"。
    """
    return sep.join(_scenario_spell_names())


# 预设里用的兜底示例: 万一 data/tavern_spells.json 缺失(比如刚解压、还没解析过配置表),
#   预设也不能是一条空计划 —— 否则用户导进来看到的是"这一步没写用法"。
_SPELL_PLAN_EXAMPLE = "瞒天过海·六=阵上1, 破釜沉舟·六=手牌:孙策, 斗转星移·四=阵上最后, 珠联璧合·五, 草船借箭·五"
_SPELL_PLAN_ALL = _scenario_spell_plan_text() or _SPELL_PLAN_EXAMPLE
_SPELL_PLAN_N = len([x for x in _SPELL_PLAN_ALL.split(",") if x.strip()])


# v4.28★ 内置剧本模板 —— 点「+ 预设」一键导入, 不用从零拼步骤(用户反馈"自己剧本也有点难")。
#   步骤里的武将名只是【示例】, 导入后点「编辑」改成自己的即可; 改完会自动重新反查 chessID。
SCENARIO_PRESETS = [
    # ================= v4.56★ 孙策/孙尚香联动(用户实战剧本 999) =================
    #   为什么拆成 4 个子剧本: 这就是用户要的"函数"。每一块都能单独点「执行一次」验证,
    #   主剧本只负责串流程 —— 以后改"清吴将"的规则, 不用在几十行的大表里翻半天。
    #   ⚠ 导入顺序: 先把 A/B/C/D 四个导进来(它们是"函数"), 再导入 999 主剧本。
    #     没导入的话, 主剧本里的「调用剧本」会记一条警告然后跳过 —— 不会崩, 但什么也不做。
    ("999-A 孙策上阵到中间",
     "孙策上阵到孙尚香的中间。上完之后游戏会自己把孙尚香收回手牌、并多冒出几个吴将 —— "
     "那是游戏自己的结算, 脚本不用管(下一段「清多余吴将」会收拾它们)。",
     [
         {"op": "ifStart", "check": "card", "has": ["手牌:孙策", "阵上:孙尚香"],
          "mode": "all", "where": "hand"},
         {"op": "deploy", "target": ["孙策"], "pos": "middle:孙尚香", "optional": True},
         # v4.64★ 等 600 毫秒 → 等到「孙尚香已经不在阵上」(超时 600)。
         #   为什么这个条件就等于"服务器结算完了": 上阵孙策的当先会把场上的孙尚香
         #   【收回手牌】, 所以「阵上:!孙尚香」成立 = 这次上阵已经处理完, 可以立刻往下走。
         #   等不到(例如场上不止一张孙尚香、只被收走一张)就按老样子等满 600 毫秒继续 ——
         #   只会比以前更快, 不会更慢, 也不会因为等不到而中断(optional)。
         {"op": "waitUntil", "check": "card", "has": ["阵上:!孙尚香"], "where": "field",
          "timeoutMs": 600, "optional": True},
         {"op": "ifEnd"},
         # —— v4.56★ 兜底挑将(每个子剧本末尾都要有) ——
         #   为什么必须兜: 上阵孙策之后游戏会收回孙尚香、并塞几张吴将进手牌; 那个吴将要是
         #   正好凑到第 3 张就会【自动合金】, 而合金又会触发一次珠联璧合(挑 1 张武将牌)弹框。
         #   框一旦弹出来就挂在那儿 —— 后面的步骤等于对着一个没人搭理的弹窗操作, 表现就是
         #   "卡住了 / 后面全不跑"。这里的 pickOne 留空 = 服务器在等什么就挑什么(武将/锦囊都认),
         #   而且它挑完一档【会接着挑下一档】—— 同时合金好几张、连弹几个框也是一步挑完。
         #   挑不到窗口时它只记一条 warn 跳过、【不算失败】, 所以每个子剧本都放一个不会有副作用。
         {"op": "pickOne", "afterMs": 900},
     ]),
    ("999-B 清多余吴将",
     "把场上多出来的吴将遣散掉, 只留孙策/周瑜/孙尚香。"
     "金币小于 100 时连丁奉也留着(丁奉还能用它的当先再换一轮孙尚香)。"
     "名单是【从赛季配置表的势力列现读】的(赛季更新自动跟着变), 不用再手工维护; "
     "想额外保护谁就填「但这几个不要碰」。"
     "移除孙尚香会拿到随机吴将 —— 凑够 3 张照样会自动合金弹框, 末尾那步会挑掉。",
     [
         {"op": "ifStart", "check": "gold", "cmp": "<", "target": "100"},
         {"op": "recycle", "camp": "吴", "exclude": ["孙策", "周瑜", "孙尚香", "丁奉"],
          "scope": "field", "optional": True},
         {"op": "else"},
         {"op": "recycle", "camp": "吴", "exclude": ["孙策", "周瑜", "孙尚香"],
          "scope": "field", "optional": True},
         {"op": "ifEnd"},
         # v4.56★ 移除孙尚香/吴将会给随机吴将 → 可能凑齐 3 张自动合金 → 珠联璧合弹框, 兜底挑掉
         {"op": "pickOne", "afterMs": 900},
     ]),
    ("999-C 丁奉换孙尚香",
     "先把手上所有孙尚香都上阵; 再上丁奉并让它把一张孙尚香收回手牌(丁奉自己留在场上); "
     "然后重新上阵那张孙尚香, 最后把丁奉遣散腾位。",
     [
         {"op": "loopStart", "times": 4},
         {"op": "ifStart", "check": "card", "has": ["手牌:孙尚香"], "where": "hand"},
         {"op": "deploy", "target": ["孙尚香"], "pos": "auto", "optional": True},
         {"op": "wait", "ms": 450},
         {"op": "else"},
         {"op": "break"},
         {"op": "ifEnd"},
         {"op": "loopEnd"},
         {"op": "ifStart", "check": "card", "has": ["手牌:丁奉"], "where": "hand"},
         {"op": "deploy", "target": ["丁奉"], "pos": "auto", "pick": "孙尚香", "optional": True},
         # v4.64★ 等 800 毫秒 → 等到「手牌里出现孙尚香」(超时 800): 丁奉的当先就是【移除1个
         #   友方(孙尚香回手牌)】, 手牌一出现她就说明这次当先结算完了, 下一步可以立刻上阵。
         #   上面那个循环正常是"手牌没孙尚香了才跳出", 所以不会一上来就成立; 万一循环跑满 4 轮
         #   手牌还有富余(手里孙尚香多于 4 张), 条件一开始就成立 → 直接往下走, 结果和今天一致
         #   (下一步本来就是"把手牌的孙尚香上阵")。等不到就等满 800 毫秒继续。
         {"op": "waitUntil", "check": "card", "has": ["手牌:孙尚香"], "where": "hand",
          "timeoutMs": 800, "optional": True},
         {"op": "ifEnd"},
         {"op": "deploy", "target": ["孙尚香"], "pos": "auto", "optional": True},
         {"op": "wait", "ms": 450},
         {"op": "recycle", "targets": ["丁奉"], "scope": "field", "optional": True},
         # 丁奉的当先移除孙尚香 → 游戏补一张随机吴将 → 可能凑齐 3 张自动合金 → 弹框, 兜底挑掉
         {"op": "pickOne", "afterMs": 900},
     ]),
    ("999-D 回收金孙策",
     "阵上那张孙策是【金色】时: 手牌有陈珪就用陈珪把它拿下来(孙策回手牌, 再把陈珪遣散腾位); "
     "手牌没陈珪了就直接遣散金孙策(金卡丢了还能再刷回来)。"
     "金色是按 chessID 尾数判的: 普通尾数 1、金尾数 2。",
     [
         {"op": "ifStart", "check": "card", "has": ["阵上:孙策"], "where": "field", "gold": "gold"},
         {"op": "ifStart", "check": "card", "has": ["手牌:陈珪"], "where": "hand"},
         {"op": "deploy", "target": ["陈珪"], "pos": "auto", "pick": "孙策", "optional": True},
         # v4.64★ 等 800 毫秒 → 等到「孙策已经不在阵上」(超时 800): 陈珪的当先就是把孙策
         #   【收回手牌】, 孙策一离场就说明结算完了 —— 下面才敢遣散陈珪(早了会把当先打断,
         #   孙策就白留在场上, 这一步等于白做)。外层 if 已经确认过「阵上:孙策」, 所以这个
         #   条件不会一上来就成立。等不到(当先被游戏转给了别人)就等满 800 毫秒继续, 与旧行为一致。
         {"op": "waitUntil", "check": "card", "has": ["阵上:!孙策"], "where": "field",
          "timeoutMs": 800, "optional": True},
         {"op": "recycle", "targets": ["陈珪"], "scope": "field", "optional": True},
         {"op": "else"},
         {"op": "ifStart", "check": "card", "has": ["阵上:孙尚香"], "where": "field"},
         {"op": "recycle", "targets": ["孙策"], "scope": "field", "optional": True},
         {"op": "ifEnd"},
         {"op": "ifEnd"},
         {"op": "ifEnd"},
         # 陈珪上阵/遣散前后都可能凑齐 3 张陈珪(主剧本一直在抓陈珪) → 自动合金 → 弹框, 兜底挑掉
         {"op": "pickOne", "afterMs": 900},
     ]),
    ("999-E 数值跳给周瑜就换掉它",
     "只在【招募阶段】做(战斗阶段这些操作本来就不让点)。"
     "项羽的技能是【遣散武将】时自动触发的, 而且它不弹「选一个友方」的框 —— 脚本没法指定它把"
     "数值给谁, 所以反过来做: 先「记数值快照」, 再遣散一个多余的吴将去触发, 然后看周瑜的攻/血"
     "有没有变大 —— 变大了就说明数值落到周瑜身上了。"
     " 换的时候【先确认手牌里有周瑜】再动场上那个: 手牌没有就先去货架上买一张(不刷新, 不为了"
     "这个专门烧金币), 还买不到就【什么都不做】—— 先把场上的遣散了又上不了, 等于白丢一个武将。"
     " 最后把手牌那个周瑜上阵到【所有孙尚香的左边】(落点 before:孙尚香 = 插进孙尚香那一格, "
     "把她往右推)。",
     [
         # ① 只在招募阶段做 —— 战斗阶段根本不让上阵/遣散, 跑了也是白报错
         {"op": "ifStart", "check": "stage", "target": "招募"},
         # ② 触发之前先记一遍数值 —— 这是"反推作用到谁"的唯一依据, 顺序不能反
         {"op": "snapStat"},
         # ③ 项羽技能 = 遣散武将触发 → 遣散一个多余的吴将(四个核心排除掉, 别把主力送掉)
         #    想用别的方式触发(比如上阵项羽/用某个锦囊)就把这一步换掉
         {"op": "recycle", "camp": "吴", "exclude": ["孙策", "周瑜", "孙尚香", "丁奉"],
          "scope": "field", "optional": True},
         # v4.64★ 等 900 毫秒 → 等到"周瑜的攻涨了"(超时 900): 涨了立刻走, 没涨等满
         {"op": "waitUntil", "check": "stat", "has": ["周瑜"], "stat": "atk",
          "cmp": ">", "target": "0", "timeoutMs": 900, "optional": True},
         # ④ 数值是不是加到周瑜身上了?(比的是"现在 - 快照", 0 = 只要变大了)
         {"op": "ifStart", "check": "stat", "has": ["周瑜"], "stat": "atk", "cmp": ">", "target": "0"},
         # ⑤ 手牌没有周瑜 → 先在【当前货架】上找一张(refreshCount=0 = 不刷新)
         {"op": "ifStart", "check": "card", "has": ["手牌:!周瑜"], "where": "hand"},
         {"op": "shopFind", "target": ["周瑜"], "refreshCount": 0, "keepGold": 0, "buy": True,
          "optional": True},
         {"op": "ifEnd"},
         # ⑥ 手牌真有周瑜了, 才敢动场上那个(不然遣散了上不回来)
         {"op": "ifStart", "check": "card", "has": ["手牌:周瑜"], "where": "hand"},
         {"op": "recycle", "targets": ["周瑜"], "scope": "field", "optional": True},
         {"op": "wait", "ms": 400},
         {"op": "deploy", "target": ["周瑜"], "pos": "before:孙尚香", "optional": True},
         {"op": "wait", "ms": 500},
         {"op": "ifEnd"},
         {"op": "ifEnd"},
         # ⑦ 换周瑜的过程中也可能凑齐 3 张(合金) → 弹框, 兜底挑掉
         {"op": "pickOne", "afterMs": 600},
         {"op": "ifEnd"},
     ]),
    # ================= v4.64★ 999-F: 数值必须落到孙尚香身上 =================
    #   用户原话"那个999项羽当时那个判定就是阵上孙策遣散的时候判定是不是没有给孙尚香,
    #   给其他人了, 不给孙尚香就要遣散给的, 直到给孙尚香", 以及"项羽技能都能触发;
    #   但是我只需要判定遣散孙策就行, 只有他的数值很大"。
    #   所以这一条与 999-E 的区别只有两处: ① 判定对象从周瑜换成孙尚香;
    #   ② 先专门遣散孙策(它数值最大, 转给孙尚香收益最高), 后面几轮才轮到清杂鱼吴将。
    ("999-F 数值必须给到孙尚香",
     "只在【招募阶段】做(战斗阶段不让遣散)。项羽的技能是【遣散武将】时自动触发、而且不弹"
     "「选一个友方」的框 —— 脚本没法指定它把数值给谁, 只好反过来验: 先「记数值快照」, "
     "再遣散一张(第一轮遣散孙策, 它数值最大; 之后遣散多余的吴将), 然后看孙尚香的攻有没有变大; "
     "没变大就再遣散一张, 直到她涨了才收工。"
     " 每轮只遣散一张, 而且主力(孙尚香/周瑜/丁奉)一律排除在外 —— 转歪了还得靠它们继续打, "
     "不能顺手送掉。循环次数只是上限, 场上没杂鱼了会自动跳出。",
     [
         # ① 只在招募阶段做 —— 战斗阶段不让遣散, 跑了也是白报错
         {"op": "ifStart", "check": "stage", "target": "招募"},
         {"op": "loopStart", "times": 3},
         # ⚠ 次数只留 3, 不是 8: 在主剧本里"重试"是【靠主剧本自己的下一轮循环】完成的
         #   (下一轮会重新上阵孙策), 本预设在这里多转一圈就要白等 900 毫秒 ×好几轮。
         #   单独执行本预设想多试几次, 把这个数字改大就行。
         # ② 孙策【还在场上】→ 才需要自己记快照 + 自己遣散。这两步必须包在同一个 if 里:
         #    主剧本的用法是「主剧本记快照 → 调 999-D 遣散孙策 → 调本预设判定」,
         #    如果这里无条件再 snapStat 一次, 会把主剧本【遣散前】记的那份快照覆盖成遣散后的 →
         #    比较基线变成"已经涨过了", 判定永远返回"没涨", 整个预设等于白跑。
         #    反过来单独执行本预设时孙策在场, 走这里自己记、自己遣散, 也完全自洽。
         {"op": "ifStart", "check": "card", "has": ["阵上:孙策"], "where": "field"},
         {"op": "snapStat"},
         # ③ 孙策优先: 它是数值最大的那张, 转给孙尚香收益最高。遣散掉之后孙策就没了,
         #    再跑循环时这个 if 会直接落空, 用主剧本记好的那份快照
         {"op": "recycle", "targets": ["孙策"], "scope": "field", "optional": True},
         # v4.64★ 等 900 毫秒 → 等到「孙尚香的攻涨了」(超时 900): 条件与下面第 ⑤ 步那个 if
         #   【逐字相同】—— 正好就是这一步要等的结果。涨了(数值给了孙尚香)立刻走; 没涨
         #   (数值转给别人了)就等满 900 毫秒再往下判, 与旧行为完全一致, 只是不再白等。
         {"op": "waitUntil", "check": "stat", "has": ["孙尚香"], "stat": "atk",
          "cmp": ">", "target": "0", "timeoutMs": 900, "optional": True},
         {"op": "ifEnd"},
         # ④ 孙策没了(或本来就不在场上): 遣散一张多余的吴将, 去触发项羽技能。
         #    ⚠ 主力三个排除在外 —— 见上面预设说明
         {"op": "recycle", "camp": "吴", "exclude": ["孙尚香", "周瑜", "丁奉"],
          "scope": "field", "optional": True},
         # v4.64★ 等 900 毫秒 → 等到「孙尚香的攻涨了」(超时 900): 同上, 条件与第 ⑤ 步的 if
         #   逐字相同 —— 涨了立刻去收工, 没涨就等满 900 毫秒, 交给下面那个 if 判"再遣散一张"。
         {"op": "waitUntil", "check": "stat", "has": ["孙尚香"], "stat": "atk",
          "cmp": ">", "target": "0", "timeoutMs": 900, "optional": True},
         # ⑤ 孙尚香的攻涨了吗?(比的是"现在 - 快照", 0 = 只要变大了就算落给她了)
         {"op": "ifStart", "check": "stat", "has": ["孙尚香"], "stat": "atk", "cmp": ">", "target": "0"},
         {"op": "log", "text": "数值已经给到孙尚香 → 收工"},
         {"op": "break"},
         {"op": "else"},
         {"op": "log", "text": "这次没给到孙尚香 → 再遣散一张继续试"},
         {"op": "ifEnd"},
         {"op": "loopEnd"},
         # ⑥ 循环里遣散了一堆吴将 → 很可能凑齐 3 张自动合金 → 珠联璧合弹框, 兜底挑掉
         {"op": "pickOne", "afterMs": 900},
         {"op": "ifEnd"},
     ]),
    ("999 孙策孙尚香联动(主)",
     "⚠ 先把 999-A / 999-B / 999-C / 999-D / 999-F 五个都导入好, 再导这个(它们是函数)。"
     "每轮跑: 手牌没孙策就去商店找(顺带抓陈珪) → 孙策上阵到孙尚香中间 → 清多余吴将 → "
     "记数值快照 → 回收金孙策(这一步会遣散孙策, 触发项羽技能) → "
     "验证数值有没有给到孙尚香 → 丁奉换一轮。"
     " 循环次数只是最多折腾几轮的上限, 手牌和场上都没东西了会自动跳出。",
     [
         {"op": "loopStart", "times": 6},
         # 万一上一轮留下了一个没人搭理的选将框(比如最后一步刚合金), 先挑掉再开新一轮
         {"op": "pickOne", "afterMs": 400},
         {"op": "ifStart", "check": "card", "has": ["手牌:!孙策"], "where": "hand"},
         # ⚠ 用户实际踩到的坑: 找孙策的同时顺带抓陈珪, 陈珪抓到第 3 张会【自动合金】
         #   → 合金触发珠联璧合 → 弹框。所以每次搜完商店都要挑一次(见下面那步)。
         {"op": "shopFind", "target": ["孙策", "陈珪"], "refreshCount": 4, "keepGold": 0,
          "buy": True, "optional": True},
         {"op": "ifEnd"},
         {"op": "pickOne", "afterMs": 900},
         {"op": "callScenario", "target": "999-A 孙策上阵到中间", "optional": True},
         {"op": "callScenario", "target": "999-B 清多余吴将", "optional": True},
         # v4.64★ 遣散【之前】先记一遍数值快照 —— 下一步 999-D 会遣散孙策(触发项羽技能把
         #   数值转给随机友方), 快照必须在它之前。顺序反了的话, 999-F 的比较基线就变成
         #   "已经涨过了", 判定永远返回"没涨", 那一整段等于白跑。
         {"op": "snapStat"},
         {"op": "callScenario", "target": "999-D 回收金孙策", "optional": True},
         # v4.64★ 等 900 毫秒 → 等到"孙尚香的攻涨了"(超时 900): 涨了立刻走, 没涨等满
         {"op": "waitUntil", "check": "stat", "has": ["孙尚香"], "stat": "atk",
          "cmp": ">", "target": "0", "timeoutMs": 900, "optional": True},
         # v4.64★ 判定刚才那次遣散把数值给了谁。旧版 999-E 判的是"给了周瑜就把周瑜换掉",
         #   按用户要求改成只认孙尚香 —— 原话"不用判断其他吴国遣散后的数值给谁了,
         #   只判断孙策的数据给谁了就行"。999-F 内部只在"孙策还在场上"时才自己记快照+
         #   遣散, 所以这里用的正是上面那份快照, 不会被它覆盖(单独执行 999-F 时它又自洽)。
         {"op": "callScenario", "target": "999-F 数值必须给到孙尚香", "optional": True},
         {"op": "callScenario", "target": "999-C 丁奉换孙尚香", "optional": True},
         {"op": "ifStart", "check": "card", "has": ["手牌:!孙策", "阵上:!孙尚香"],
          "mode": "all", "where": "hand"},
         {"op": "break"},
         {"op": "ifEnd"},
         {"op": "loopEnd"},
     ]),
    # ================= v4.101★ 硬刷孙策 / 凌统线(用户新套路) =================
    #   与 999 那套的区别: 999 是"孙策上阵 → 孙尚香联动"; 这套是"丁奉的当先对准【凌统】",
    #   而且【金丁奉不遣散】—— 金丁奉留在场上, 交给陈珪的当先反复拉回手牌再上阵(多刷几次当先)。
    #   版本判定用的是 chessID 尾数: 普通尾数 1、金尾数 2(见 SCENARIO_VERSION_PAIRS)。
    ("丁奉换凌统 (金丁奉留着)",
     "和『999-C 丁奉换孙尚香』同构, 只是丁奉的当先这次对准【凌统】: "
     "先把手里所有凌统上阵 → 上丁奉让它把一张凌统收回手牌 → 再上阵那张凌统 → 遣散丁奉腾位。"
     " ★ 最后一步只遣散【普通丁奉】(version=normal) —— 金丁奉留着, 它要用陈珪反复刷。"
     " 想换成别的目标就把所有「凌统」改成你要的那张卡。",
     [
         # ① 先把手里所有凌统都上阵(有几张上几张, 最多 4 张)
         {"op": "loopStart", "times": 4},
         {"op": "ifStart", "check": "card", "has": ["手牌:凌统"], "where": "hand"},
         {"op": "deploy", "target": ["凌统"], "pos": "auto", "optional": True},
         {"op": "wait", "ms": 450},
         {"op": "else"},
         {"op": "break"},
         {"op": "ifEnd"},
         {"op": "loopEnd"},
         # ② 上丁奉, 当先对准凌统(把凌统收回手牌)
         {"op": "ifStart", "check": "card", "has": ["手牌:丁奉"], "where": "hand"},
         {"op": "deploy", "target": ["丁奉"], "pos": "auto", "pick": "凌统", "optional": True},
         # 等到「手牌里出现凌统」= 这次当先结算完了(超时 800 毫秒就按老样子继续)
         {"op": "waitUntil", "check": "card", "has": ["手牌:凌统"], "where": "hand",
          "timeoutMs": 800, "optional": True},
         {"op": "ifEnd"},
         # ③ 重新上阵那张凌统(触发它的当先)
         {"op": "deploy", "target": ["凌统"], "pos": "auto", "optional": True},
         {"op": "wait", "ms": 450},
         # ④ 遣散丁奉腾位 —— 只遣散【普通】的; 金丁奉留着给陈珪刷
         {"op": "recycle", "targets": ["丁奉"], "scope": "field", "version": "normal",
          "optional": True},
         # 丁奉移除凌统后游戏会补随机吴将 → 凑够 3 张会合金弹框, 兜底挑掉
         {"op": "pickOne", "afterMs": 900},
     ]),
    ("金丁奉·陈珪刷当先",
     "金丁奉【不上遣散名单】, 改成用【陈珪】的当先把它拉回手牌、再上阵 —— 每上阵一次就触发一次丁奉的当先。"
     " 手上有几张陈珪就能刷几轮(刷完手牌里的陈珪会自动停)。"
     " ⚠ 陈珪的当先要【点一个友方】才算结算, 这里对准丁奉; 场上别留别的友方, 免得当先收错人。",
     [
         {"op": "loopStart", "times": 5},
         {"op": "ifStart", "check": "card", "has": ["手牌:陈珪"], "where": "hand"},
         # 陈珪当先: 使场上 1 个非同名友方回到手牌 —— 这里对准【丁奉】
         {"op": "deploy", "target": ["陈珪"], "pos": "auto", "pick": "丁奉", "optional": True},
         {"op": "waitUntil", "check": "card", "has": ["手牌:丁奉"], "where": "hand",
          "timeoutMs": 800, "optional": True},
         # 陈珪用完就遣散腾位(陈珪不分金普, 一律遣散 —— 它只是工具人)
         {"op": "recycle", "targets": ["陈珪"], "scope": "field", "optional": True},
         {"op": "else"},
         {"op": "break"},
         {"op": "ifEnd"},
         # 回到手牌的金丁奉再上阵 —— 触发它的当先(对准凌统)
         {"op": "deploy", "target": ["丁奉"], "pos": "auto", "pick": "凌统", "optional": True},
         {"op": "wait", "ms": 450},
         {"op": "pickOne", "afterMs": 900},
         {"op": "loopEnd"},
     ]),
    # v4.101★ 改版: 上一版写成"买孙策 → 上阵孙策"是错的 —— 孙策【本来就在阵上】(而且是金孙策),
    #   这套要刷的不是孙策, 是【陈珪】(陈珪的当先把孙策拉回手牌, 再上阵 = 又吃一次孙策的当先)。
    #   用户原话: "我这么写只是金币不够刷到陈珪, 陈珪几率有点小" —— 所以刷陈珪那一步
    #   【必须 optional】, 刷不到就下一轮再来, 绝不能中断剧本(老版就是卡在这: 刷新次数用尽 → 整条断掉)。
    ("硬刷孙策 (阵上金孙策 · 陈珪反复拉)",
     "★ 前提: 【金孙策已经在阵上】(不是手牌, 不用买也不用找)。这套只干一件事: 刷【陈珪】 —— "
     "陈珪的当先把阵上的金孙策拉回手牌 → 再把孙策上回去, 每上一次就触发一次孙策的当先。\n"
     " 手上有几张陈珪就刷几轮; 手牌里没有陈珪才去牌库刷(陈珪概率小, 刷不到是常态 —— 那一步勾了"
     "「找不到就算了」, 不会中断剧本)。\n"
     " ★ 钱不够 = 刷不动(刷新要花钱), 这里给「保留金币=2」: 刷到只剩 2 金就自动停, 不会把钱刷光。\n"
     " ★ 想多刷几轮就改第 ① 步的「最多 8 次」; 想多刷几次商店就改「刷新次数 6」。",
     [
         {"op": "loopStart", "times": 8},
         {"op": "pickOne", "afterMs": 400},
         # ① 手牌里没陈珪 → 才去牌库刷(只刷陈珪一个, 不要顺手买别的: 钱要留着刷新)
         #    ★ 刷不到是常态 → 必须 optional(否则刷新次数用尽会把整条剧本断掉)
         {"op": "ifStart", "check": "card", "has": ["手牌:!陈珪"], "where": "hand"},
         {"op": "shopFind", "target": ["陈珪"], "refreshCount": 6, "keepGold": 2,
          "buy": True, "optional": True},
         {"op": "ifEnd"},
         # ② 有陈珪才走: 陈珪上阵, 当先对准【阵上的金孙策】→ 把它拉回手牌
         {"op": "ifStart", "check": "card", "has": ["手牌:陈珪"], "where": "hand"},
         {"op": "deploy", "target": ["陈珪"], "pos": "auto", "pick": "孙策", "optional": True},
         # 等孙策真的回到手牌(超时 800ms 也照样往下走, 不中断)
         {"op": "waitUntil", "check": "card", "has": ["手牌:孙策"], "where": "hand",
          "timeoutMs": 800, "optional": True},
         # 陈珪用完就遣散腾位(它只是工具人)
         {"op": "recycle", "targets": ["陈珪"], "scope": "field", "optional": True},
         {"op": "ifEnd"},
         # ③ 孙策回到手牌了 → 立刻再上阵, 触发它的当先(这一下才是"刷"出来的数值)
         {"op": "ifStart", "check": "card", "has": ["手牌:孙策"], "where": "hand"},
         {"op": "deploy", "target": ["孙策"], "pos": "auto", "optional": True},
         {"op": "wait", "ms": 450},
         {"op": "ifEnd"},
         {"op": "pickOne", "afterMs": 900},
         {"op": "loopEnd"},
     ]),
    # ================= v4.108★ S10 经济倒转 · 陈珪刷邓茂/丁奉(黄巾兵喂张梁) =================
    #   引擎(用户原话: "经济倒转, 靠丁奉陈珪刷邓茂"):
    #     · 邓茂(2费) 当先: 获得1张黄巾兵 → 兵用 followUp 全给【张梁】
    #       (张梁 9/9: "被随征时, 也为其他非同名友方随征1个" = 随征放大器)
    #     · 陈珪(3费) 当先: 场上1个非同名友方回到手牌 → 把邓茂/丁奉捞回来重复上阵
    #     · 丁奉(2费) 当先: 移除1个友方, 获得3虎符(金6) → 移除的目标是【陈珪】
    #   ★ 倒转在哪: 陈珪 3 金买来, 丁奉吃掉它换 3 金 → 陈珪零成本, 白嫖邓茂的兵 + 张梁随征;
    #     换【金丁奉】(6金)就是 6-3 = 每轮净 +3 金, 这才是"经济越来越多"的来源。
    #   ★ 丁奉的移除【不算阵亡】—— 不会触发张宝/高升那些"友方阵亡"技能, 别指望联动。
    ("S10 经济倒转 · 陈珪刷邓茂丁奉(兵喂张梁)",
     "★ 前提: 张梁已在阵上(9/9, 被随征时也为其他非同名友方随征1个 = 放大器)。\n"
     " 每轮: 刷陈珪 → 陈珪当先捞回邓茂/丁奉 → 被捞的卡再上阵 → 丁奉吃掉陈珪换钱。\n"
     " · 拉【丁奉】= 变现(普通+3金 / 金+6金); 拉【邓茂】= 产1张黄巾兵(兵全给张梁随征)。\n"
     " · 节奏 2:1 = 2 次丁奉配 1 次邓茂; 金币 < 20 时自动只做丁奉(回血), 跳过邓茂那轮。\n"
     " · 金丁奉/金邓茂留在场上给陈珪反复拉; 普通的用完遣散腾位。\n"
     " ★ 刷陈珪那步是 optional(陈珪概率小, 刷不到就下一轮, 不会中断剧本)。",
     [
         {"op": "loopStart", "times": 4},
         {"op": "pickOne", "afterMs": 400},
         # ① 找陈珪(工具人) + 顺带把丁奉邓茂一起买了(用户: "刷的时候丁奉和邓茂一块刷吧")
         {"op": "shopFind", "target": ["陈珪", "邓茂", "丁奉"], "refreshCount": 6,
          "keepGold": 20, "buy": True, "optional": True},
         {"op": "pickOne", "afterMs": 600},
         # ② 丁奉 ×2 —— 陈珪捞丁奉 → 丁奉上阵移除陈珪 → +3/+6 金
         {"op": "loopStart", "times": 2},
         {"op": "ifStart", "check": "card", "has": ["手牌:陈珪"], "where": "hand"},
         {"op": "deploy", "target": ["陈珪"], "pos": "auto", "pick": "丁奉", "optional": True},
         {"op": "waitUntil", "check": "card", "has": ["手牌:丁奉"], "where": "hand",
          "timeoutMs": 800, "optional": True},
         # 丁奉的当先对准【陈珪】= 移除陈珪换虎符(顺带清掉工具人, 省一次遣散)
         {"op": "ifStart", "check": "card", "has": ["手牌:丁奉"], "where": "hand"},
         {"op": "deploy", "target": ["丁奉"], "pos": "auto", "pick": "陈珪", "optional": True},
         {"op": "wait", "ms": 450},
         # 普通丁奉用完就遣散腾位; 金丁奉留着(陈珪还要拉它)
         {"op": "recycle", "targets": ["丁奉"], "scope": "field", "version": "normal",
          "optional": True},
         {"op": "ifEnd"},
         {"op": "else"},
         {"op": "break"},
         {"op": "ifEnd"},
         {"op": "loopEnd"},
         # ③ 邓茂 ×1 —— 金币 >= 20 才做(用户: "金币少了拉丁奉、金币多拉邓茂")
         {"op": "ifStart", "check": "gold", "cmp": ">=", "target": "20"},
         {"op": "ifStart", "check": "card", "has": ["手牌:陈珪"], "where": "hand"},
         {"op": "deploy", "target": ["陈珪"], "pos": "auto", "pick": "邓茂", "optional": True},
         {"op": "waitUntil", "check": "card", "has": ["手牌:邓茂"], "where": "hand",
          "timeoutMs": 800, "optional": True},
         # 邓茂上阵 → 出 1 张黄巾兵
         {"op": "ifStart", "check": "card", "has": ["手牌:邓茂"], "where": "hand"},
         {"op": "deploy", "target": ["邓茂"], "pos": "auto", "optional": True},
         {"op": "wait", "ms": 450},
         {"op": "ifEnd"},
         # 黄巾兵全部随征给张梁(一次发完)
         {"op": "followUp", "cards": ["黄巾兵"], "camp": "", "targets": ["张梁"],
          "allToOne": True, "waitMs": 600, "optional": True},
         # 最后上丁奉吃掉陈珪(这一下把陈珪那 3 金成本收回来)
         {"op": "ifStart", "check": "card", "has": ["手牌:丁奉"], "where": "hand"},
         {"op": "deploy", "target": ["丁奉"], "pos": "auto", "pick": "陈珪", "optional": True},
         {"op": "wait", "ms": 450},
         {"op": "recycle", "targets": ["丁奉"], "scope": "field", "version": "normal",
          "optional": True},
         {"op": "ifEnd"},
         {"op": "ifEnd"},
         {"op": "ifEnd"},
         {"op": "pickOne", "afterMs": 900},
         {"op": "loopEnd"},
     ]),
    ("遣散手牌里指定的武将",
     "手牌和阵上一起找, 找不到也不中断 (示例名: 董袭/马云禄)",
     [
         {"op": "recycle", "targets": ["董袭", "马云禄"], "scope": "both", "optional": True},
     ]),
    ("陈珪当先: 上阵→遣散 循环",
     "陈珪是【当先·上阵】效果(上阵即触发『使场上1个非同名友方回到手牌』), 上完再把自己遣散掉, 让下一只接着上。"
     "⚠ v4.29: 这个当先要【点一个友方】才算上阵成功(游戏会弹选择框), 所以「上阵后选人」填 auto(或该友方的名字); "
     "场上没别的友方时这一步会跳过(已勾可忽略)",
     [
         {"op": "loopStart", "times": 5},
         {"op": "deploy", "target": "陈珪", "pos": "auto", "pick": "auto", "optional": True},
         {"op": "wait", "ms": 900},
         {"op": "recycle", "targets": ["陈珪"], "scope": "field", "optional": True},
         {"op": "loopEnd"},
     ]),
    ("陈珪刷当先(需求4: 上阵→收回→再上阵)",
     "核心流: 场上只留1张目标卡X(示例左慈, 换成你带的那张当先/随征卡)。每轮先重新上阵X(触发它的当先), "
     "再上阵陈珪 —— 陈珪的当先把X收回手牌(这一步游戏要求你点选目标, 脚本替你点), 然后把陈珪遣散腾位。"
     "手牌里有几张陈珪就能刷几轮。⚠ 场上别留别的友方, 否则当先可能收回别人",
     [
         {"op": "loopStart", "times": 5},
         {"op": "deploy", "target": "左慈", "pos": "auto"},
         {"op": "wait", "ms": 700},
         {"op": "deploy", "target": "陈珪", "pos": "auto", "pick": "auto", "optional": True},
         {"op": "wait", "ms": 900},
         {"op": "recycle", "targets": ["陈珪"], "scope": "field", "optional": True},
         {"op": "loopEnd"},
     ]),
    ("牌库找卡 → 买 → 上阵 → 遣散",
     "商店里没这张卡就刷新(最多3次, 金币低于10不刷); 买到后上阵, 用完再遣散掉",
     [
         {"op": "shopFind", "target": "孙策", "refreshCount": 3, "keepGold": 10},
         {"op": "deploy", "target": "孙策", "pos": "auto"},
         {"op": "wait", "ms": 500},
         {"op": "recycle", "targets": ["孙策"], "scope": "field", "optional": True},
     ]),
    ("有就执行 / 没有就跳过",
     "手牌里有这几张才走中间那段, 否则直接跳到『条件结束』",
     [
         {"op": "ifStart", "has": ["孙策", "周瑜"], "mode": "all", "where": "both"},
         {"op": "toast", "text": "条件满足"},
         {"op": "ifEnd"},
     ]),
    ("空循环骨架 (自己填中间)",
     "循环开始…循环结束之间插入你的步骤, 改「次数」决定重复几遍",
     [
         {"op": "loopStart", "times": 3},
         {"op": "wait", "ms": 500},
         {"op": "loopEnd"},
     ]),
    # ---------- v4.41★ 以下三条是实机踩坑之后补的 ----------
    ("用尽所有锦囊 (无目标直接用 / 有目标轮着试)",
     "手里每张锦囊都想方设法用出去 —— 不需要选目标的【直接使用】; 需要选目标的按 "
     "牌库第1格 → 第2格 → … → 最后一格 → 阵上最后一个 轮着试: 哪个目标能让锦囊离手就算成功。"
     "全试完都不行就记一笔失败、换下一张(不会卡死在某一张上); 跑完日志会给「成功N张 / 失败N张(卡名)」。"
     "⚠ 需要游戏页脚本 v4.41+",
     [
         {"op": "useAllSpells", "waitMs": 700, "rounds": 30, "optional": True},
     ]),
    # ================= v4.68★ 用尽锦囊·逐条 =================
    #   用户原话: "是不是可以再写一个【用尽锦囊】、这个用尽锦囊不改; 新的用尽锦囊就是,
    #   我大概知道有哪些锦囊了, 我一张张地把锦囊用出来 —— 这个可以加吧?
    #   大概知道有哪些锦囊了, 就是直接使用锦囊, 每张锦囊都是使用锦囊, 一张张用,
    #   但是可以选择不用哪些锦囊。"
    #   所以: 老步骤一个字没动, 这是【另一步】, 粒度从"整步排除名单"细到"每张各自给谁"。
    ("逐条用锦囊 (每张自己指定给谁 / 可单独停用)",
     "把手里每张锦囊【一个一个用】, 但每一张给谁由你定 —— 这是它和「用尽所有锦囊」的区别: "
     "那个是脚本挨个试目标(谁能让锦囊离手就算成功), 每张究竟给了谁它并不知道, "
     "而这里是你写的。"
     "★ 本预设已经把【本赛季全部锦囊列出来了, 共 " + str(_SPELL_PLAN_N) + " 张】"
     "(以前只给了 5 条示例, 用户问的就是这个「里面不全啊」)。"
     "写法(填在「每张锦囊的用法」里, 用逗号或换行分隔): 「锦囊名=作用到」。"
     "「作用到」与「使用锦囊」那一步完全一致: 阵上1 / 阵上最后 / 牌库1 / 手牌:孙策 / 直接使用; "
     "这里【留空】= 交给脚本自动轮目标(直接使用 → 牌库1~6格 → 阵上1~7格, 谁能让这张离手就算谁)。"
     "所以整列出来也不用替每张猜该给谁: 常用的那几张再补上 「=阵上1」 就行。"
     "★ 哪一条不想用就在它前面加 #(#草船借箭·五=牌库1) —— 同一步骤这次用这几张、"
     "下次用那几张, 不用维护两份剧本, 也不用改内置预设。"
     "顺带: 用掉珠联璧合/草船借箭这类锦囊后服务器还会再弹一个挑牌窗, 脚本会接着替你答掉"
     "(不答的话下一张锦囊会被它顶着, 后面全变成「目标找不到」)。"
     "⚠ 需要游戏页脚本 v4.69+(留空自动轮目标与优先级写法都是 v4.69 才有的)。",
     [
         {"op": "spellPlan", "gapMs": 200, "waitMs": 1200, "optional": True,
          "plan": _SPELL_PLAN_ALL},
     ]),
    # ================= v4.80★ 用尽锦囊·按用法 =================
    # 用户原话: "可以先看手牌有什么再去执行对应的"。
    #   这一步的定位: 手里锦囊不多、但你知道其中几张该给谁时最划算 —— 写那几张就够了,
    #   剩下的交给「没写用法的怎么办」(默认自动轮目标, 所以手牌照样用得完)。
    #   预设里那两行只是【示例】, 按自己这一局的牌改(「随机应变」脚本已内置成「同上」, 不用写)。
    ("用尽锦囊·按用法 (先扫手牌, 有用法的一发命中)",
     "开工【先扫一遍手牌】看看到底有哪些锦囊(日志里会报总账), 再一张张用: 上面写了用法的那几张"
     "按写法【一发命中】(不试别的目标 —— 这里试中即消耗, 试错就收不回来); "
     "手上还有、但没写用法的锦囊, 按「没写用法的怎么办」处理 —— 默认【自动轮目标】, "
     "即和老「用尽锦囊」一个样(直接使用 → 牌库1~6格 → 阵上1~7格), 手牌一定用得完; "
     "怕脚本给错人就换成【留着不动】, 那只用你写了的那几张, 其余留在手牌、日志里列出来。"
     "★ 与另两步的区别: 「用尽锦囊」不认识任何一张(每张都把目标挨个试过去, 慢); "
     "「逐条用锦囊」只跑你写的那几张(计划里没有的就一张不碰, 用不完); 这一步两头都占。"
     "★ 名字是【包含匹配】, 写「瞒天过海」也能命中「瞒天过海·六」。"
     "⚠ 需要脚本 v4.80+",
     [
         {"op": "spellSmart", "gapMs": 150, "waitMs": 1200, "rounds": 30,
          "noRule": "auto", "optional": True,
          "rules": "瞒天过海=阵上1, 破釜沉舟=手牌:孙策"},
     ]),
    ("丁奉当先: 上阵 → 移除1个友方 (选人型当先)",
     "丁奉(330411)的当先是『移除1个友方』, 上阵后游戏会弹『选目标』窗口 —— "
     "必须把这一次选人走完, 服务器才认这次上阵(不选的话卡根本不进场, 当先自然等于没触发)。"
     "本预设: 上阵丁奉(选人=自动挑场上第一张非同名友方) → 等它结算 → 再遣散丁奉腾位。"
     "⚠ 场上没有别的友方时选人会跳过(这步已勾「失败也继续」); 需要脚本 v4.40+",
     [
         {"op": "deploy", "target": "丁奉", "pos": "auto", "pick": "auto", "optional": True},
         {"op": "wait", "ms": 1200},
         {"op": "recycle", "targets": ["丁奉"], "scope": "field", "optional": True},
     ]),
    ("挑一个锦囊 (技能给你选锦囊时用)",
     "有的技能会让服务器弹『挑锦囊』窗口(走 ReqChessSelectSpellID 那套协议, 和挑武将完全不同)。"
     "本预设挑候选里的第一个; 想挑指定的那张就把「目标/内容」改成它的名字或ID, 要第几个就写 1/2/3。"
     "挑完之后如果服务器还在等(要连着挑好几个), 脚本会【自动续挑挑空】—— "
     "否则那个挑选窗口会一直挂在屏幕上, 就是你看到的『显示有未选择的卡片』。",
     [
         {"op": "pickOne", "kind": "spell", "pick": "auto", "afterMs": 300, "optional": True},
     ]),
    # ================= v4.64★ 招募流(用户要的那套) =================
    #   用户原话: "招募流剧本: 招募阶段用的, 判定手牌有没有庞德公, 有就使用, 没有就刷新牌库,
    #   找庞德公, 和需要的武将, 可能是薛凌云(薛灵芸), 或者贾诩? 遣散贾诩会挑选锦囊, 要选
    #   瞒天过海·六; 用这个可以触发一次招募效果; 然后用完庞德公, 有的也会给锦囊比如司马懿,
    #   用所有锦囊, 但是不能用破釜沉舟·六, 斗转星移四, 闪电五;
    #   薛凌云随征给阵上的魏国, 尽可能分均薛凌云、不要一直给一个武将"
    #   —— 补问之后确认: 庞德公是【武将】, 动作是【遣散】, 招募效果【自动】。
    #   ⚠ 两个新能力(脚本 v4.64 才有, 别在旧脚本上导入本条):
    #     · 「用尽锦囊」的【步骤级】排除名单 —— 只在这一步里排除那三张, 内置预设没动。
    #     · 「随征」步骤 —— 轮着把随征卡摊给阵上的魏将, 并跳过指定的人。
    ("招募流 (庞德公 / 贾诩挑锦囊 / 薛灵芸随征)",
     "只在【招募阶段】跑。一轮的顺序 —— "
     "① 手牌有庞德公就遣散它(它是武将, 招募效果由游戏自动结算); 没有就刷商店找 "
     "庞德公/薛灵芸/贾诩; "
     "② 手牌有贾诩就遣散 —— 遣散贾诩会弹「挑锦囊」, 自动选『瞒天过海·六』(这也触发一次招募); "
     "③ 把手里剩下的锦囊一次全用掉, 但【破釜沉舟·六 / 斗转星移·四 / 闪电·五 留着不动】—— "
     "这三张是特意留的, 所以排除名单写在这一步上, 【没有改「用尽所有锦囊」那个内置预设】; "
     "④ 薛灵芸随征, 摊给阵上的魏将(轮着来, 不给钟会)。"
     " 循环次数只是上限; 每轮末尾都会兜底答掉合金弹出来的挑牌窗。"
     "⚠ 需要游戏页脚本 v4.64+(旧脚本没有「随征」和排除名单, 会当未知步骤跳过)。",
     [
         # ① 只在招募阶段做 —— 战斗阶段不让遣散/上阵, 跑了也是白报错
         {"op": "ifStart", "check": "stage", "target": "招募"},
         {"op": "loopStart", "times": 8},
         # ② 庞德公在手牌 → 遣散它(遣散的招募效果游戏自己结算, 脚本不用再补操作)
         {"op": "ifStart", "check": "card", "has": ["手牌:庞德公"], "where": "hand"},
         {"op": "recycle", "targets": ["庞德公"], "scope": "hand", "optional": True},
         {"op": "wait", "ms": 800},
         {"op": "else"},
         # 手牌没庞德公 → 刷商店(顺带把后面两步要用的薛灵芸/贾诩一起抓)
         {"op": "shopFind", "target": ["庞德公", "薛灵芸", "贾诩"], "refreshCount": 4,
          "keepGold": 0, "buy": True, "optional": True},
         {"op": "ifEnd"},
         # ③ 贾诩 → 遣散会弹「挑锦囊」→ 指定挑 瞒天过海·六
         {"op": "ifStart", "check": "card", "has": ["手牌:贾诩"], "where": "hand"},
         {"op": "recycle", "targets": ["贾诩"], "scope": "both", "optional": True},
         {"op": "pickOne", "kind": "spell", "pick": "瞒天过海·六", "afterMs": 600, "optional": True},
         {"op": "wait", "ms": 700},
         {"op": "ifEnd"},
         # ④ 用尽锦囊 —— 那三张留着(排除名单只写在【这一步】上)
         {"op": "useAllSpells", "rounds": 30, "waitMs": 700,
          "exclude": ["破釜沉舟·六", "斗转星移·四", "闪电·五"], "optional": True},
         # ⑤ 薛灵芸随征 → 阵上魏将, 轮着来(尽量摊均), 不给钟会
         {"op": "followUp", "cards": ["薛灵芸"], "camp": "魏", "exclude": ["钟会"],
          "rotate": True, "waitMs": 600, "optional": True},
         # ⑥ 兜底: 这一轮里合金/挑牌窗可能还挂着, 挑掉 —— 否则下一轮是对着一个没人搭理的
         #    弹窗操作, 表现就是"卡住 / 后面全不跑"
         {"op": "pickOne", "afterMs": 600, "optional": True},
         {"op": "loopEnd"},
         {"op": "ifEnd"},
         ]),
         # ================= v4.65★ 郭胜刷功勋(用户要的招募阶段刷牌) =================
         #   用户原话: "在招募阶段: 刷新牌库、刷新的过程中买郭胜; 如果金币小于10, 就把阵上郭胜遣散,
         #   然后上阵郭胜, 再去刷新的过程中买郭胜; 招募阶段过了就停止"
         #   郭胜(s10 普通版 = 21005081, 汉室势力)的两个技能正好凑成本循环:
         #     · 招募结束时获得功勋          → 所以他要【待在阵上】吃结算
         #     · 出售时随机1名友方获得自身功勋 → 所以【遣散他】= 把攒下来的功勋转给队友
         #   于是循环是: 手里一直留一张郭胜 → 钱快见底(金币<10)就把阵上那张卖掉(功勋转手)、
         #   换新的上场接着攒 —— 买卡的钱不花光, 攒功勋这件事不断。
         #   ⚠ 「刷新牌库」必须勾着"刷不满就算了": 金币刷干时刷新必定失败, 不勾的话剧本会中断在
         #     最后一下(而不是正常收尾)。需要游戏页脚本 v4.64+。
         ("郭胜刷功勋 (招募阶段: 刷牌买郭胜 / 低金币换牌)",
         "只在【招募阶段】跑, 一轮做四件事 —— "
         "① 刷新牌库 1 次; ② 货架上有郭胜就买下来(不额外刷新); "
         "③ 金币 < 10 时: 把阵上的郭胜【遣散】(出售会把他的功勋转给随机一名友方), "
         "再把手里的新郭胜【上阵】继续吃「招募结束得功勋」; "
         "④ 兜底答掉合金/挑牌窗。 "
         "一旦进入战斗阶段(招募阶段结束) → 立刻【中止剧本】停手, 不会接着刷。 "
         "循环 40 次只是上限(够刷到阶段结束); 金币刷干时那一步会自己跳过(已勾「刷不满就算了」)。 "
         "⚠ 需要游戏页脚本 v4.64+。",
         [
         # ① 已经进战斗阶段就别跑了 —— 战斗里刷新/买卡/遣散都会被游戏静默拒绝
         {"op": "ifStart", "check": "stage", "target": "战斗"},
         {"op": "abort", "target": "现在不是招募阶段, 停手"},
         {"op": "ifEnd"},
         {"op": "loopStart", "times": 40},
         # ② 每轮再看一眼阶段 —— 招募阶段过了就停(这就是用户说的"过了就停止")
         {"op": "ifStart", "check": "stage", "target": "战斗"},
         {"op": "abort", "target": "招募阶段结束 → 停止刷牌"},
         {"op": "ifEnd"},
         # ③ 刷新牌库 1 次(金币刷干是正常收尾 → 勾上"刷不满就算了", 别在这里中断剧本)
         {"op": "refreshShop", "refreshCount": 1, "keepGold": 0, "optional": True},
         # ④ "刷新的过程中买郭胜": 只看刚刷新出来的这一波货架(refreshCount 0 = 自己不再刷新)
         {"op": "shopFind", "target": ["郭胜"], "refreshCount": 0, "keepGold": 0,
          "buy": True, "optional": True},
         # ⑤ 金币 < 10 → 卖掉阵上的郭胜(功勋转给队友) → 换一张新的上场继续攒
         {"op": "ifStart", "check": "gold", "cmp": "<", "target": "10"},
         {"op": "recycle", "targets": ["郭胜"], "scope": "field", "optional": True},
         {"op": "deploy", "target": ["郭胜"], "pos": "auto", "optional": True},
         {"op": "ifEnd"},
         # ⑥ 兜底挑牌窗(合金/招募效果都可能弹) —— 挂着不答, 下一轮就是在对空气操作
         {"op": "pickOne", "afterMs": 400, "optional": True},
         {"op": "loopEnd"},
         ]),
         # ================= v4.66★ 全自动一轮(挂机流) =================
         #   这条预设是"新加的四样东西怎么一起用"的样板, 也是用户描述的"战斗→挑牌→招募"全自动流:
         #     waitStage  = 战斗阶段什么都不做, 等回到招募才干活(以前只能"发现是战斗就中止")
         #     loopStart.check = "金币<10 就收工"(以前要在循环体里手写 ifStart+break)
         #     count 条件 = "攒够 3 张孙策"(以前只有"有没有")
         #     retry   = 买卡被拒自己重试(以前只能复制同一步骤)
         #     drainNext = 收尾: 答空待挑窗口 + 直接进下一轮(以前 4~5 步手拼)
         ("全自动一轮 (等招募 → 干活 → 挑空进下一轮)",
         "⚠ 需要脚本 v4.66+。挂机用: 战斗阶段什么都不做, 等回到【招募阶段】才开始干活; "
         "干完把待挑窗口答干净、直接进下一轮。"
         " 想真的『一直挂』就把这个剧本的「运行次数」改成 0(0 = 不限次数), 它会一轮接一轮地跑, "
         "直到你按「停止」; 建议顺手把头部「最长运行」填一个数(比如 3600 = 一小时)兜底 —— "
         "挂机时没人在旁边看, 万一某一步的等待写猛了, 至少到点会自己收尾。"
         "步骤里的卡名(孙策)只是示例, 点「编辑」改成自己要的。",
         [
          # ① 等回到招募阶段 —— 战斗阶段点了也没用。最多等 5 分钟, 等不到就跳过(挂机不能中断)
          {"op": "waitStage", "stage": "招募", "timeoutMs": 300000, "optional": True},
          # ② 循环干活: 最多 12 轮, 但"金币刷到 10 以下"就提前收工
          {"op": "loopStart", "times": 12, "check": "gold", "cmp": "<", "target": "10"},
          # ③ 找卡 + 买: 失败自己重试 2 次(被挑牌窗卡住是最常见的失败原因)
          {"op": "shopFind", "target": ["孙策"], "refreshCount": 3, "keepGold": 0,
           "buy": True, "optional": True, "retry": 2},
          # ④ 攒够 3 张就收工(数量条件) —— 前提是别把钱刷干, 所以放在上面那步之后
          {"op": "ifStart", "check": "card", "has": ["孙策"], "count": 3, "cmp": ">="},
          {"op": "break", "text": "孙策攒够了"},
          {"op": "ifEnd"},
          # ⑤ 收尾: 答空待挑窗口(合金/招募效果都会弹) + 直接进下一轮
          {"op": "drainNext", "maxRounds": 8},
          {"op": "loopEnd"},
         ]),

         ]


# ==================================================================
# v4.135★ 【16 套阵容接进预设菜单】—— dev/_gen_lineup_scenarios.py 生成到
#   【剧本库/】的那些剧本, 光躺在硬盘上等于没接进系统: 界面里【点不到】就是没有。
#   这里在启动时把它们读成附加预设, 于是「+ 预设 → 内置预设」下拉里能直接选某一套阵容。
#
# ★ 为什么是【动态读文件】而不是把步骤抄进本文件:
#     ① 阵容是攻略数据, 赛季一换就得重生成 —— 抄进代码里等于每次都要手改常量;
#     ② 抄进来会跟上面那些【人工打磨过】的内置预设混在一起, 分不清哪些可信、
#        哪些是"自动生成、需人工核对"的。所以它们在下拉里是单独一段, 说明栏也写明。
#
# ⚠ 这些都是【骨架】: 卡名与本数来自攻略原文(可靠), 但"放最前排先死""随征给后排"
#   这类操作细节自动做不出来 —— 说明里如实标注, 不假装能直接跑。
#   目录不存在 / 文件坏了 → 列表为空, 界面一切照旧(不会因此打不开)。
# ==================================================================
def _load_lineup_presets():
    import glob as _glob
    out = []
    try:
        d = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "剧本库")
        for p in sorted(_glob.glob(os.path.join(d, "*.json"))):
            try:
                with open(p, encoding="utf-8") as f:
                    sc = json.load(f)
                steps = sc.get("steps") or []
                if not steps:
                    continue
                out.append((str(sc.get("name") or os.path.basename(p)),
                            "阵容库自动生成 —— 卡名/本数取自攻略原文; "
                            "【摆位与随征目标需人工核对】",
                            steps))
            except Exception as e:
                print("[阵容预设] 跳过 %s: %s" % (p, e), file=sys.stderr)
    except Exception as e:
        print("[阵容预设] 加载失败(不影响其它预设): %s" % e, file=sys.stderr)
    return out


SCENARIO_PRESETS = SCENARIO_PRESETS + _load_lineup_presets()
