# -*- coding: utf-8 -*-

import os
import re
import time
import threading
import json
from datetime import datetime
import traceback

import tkinter as tk
from tkinter import ttk, Toplevel, messagebox # v3.50★ 窗口选择器新类需要 Toplevel

from core.bridge import BridgeServer, STATE
from core.protocol import LIVE
from core.remote_ops import BridgeOps
from core.page_errs import format_errors as _fmt_page_errs
from core.recorder import MACRO, REC_MODES
from core.hotkeys import HM
from core.config import BASE_DIR, DATA_DIR, load_config, save_config
# v4.109★ 只留 _log_err: 原来这里还 import 了 get_resource_path / resource_path /
#   is_frozen / _name_of, 但全项目【没有任何一处调用】(已逐个搜过: 打包后的资源路径
#   是 core/csv_loader.py 用 __file__ 反推的, 不走 utils —— 见 dev/_build_exe.py:38-41)。
#   留着只会让人以为打包路径是这么取的, 误导后来人。
from core.utils import _log_err
try:
    from core.ocr_ops import OcrOps      # v4.108★ 可选依赖(OCR/截图/鼠标拖拽); 见 core/ocr_ops.py 顶部软导入
except Exception:
    OcrOps = None                        # 缺包时助手照样能开, 只是 OCR/拖拽不可用
from core import id_map as IDMAP # v4.28★ 剧本目标按中文名反查 chessID 用
from core import pool_probe as POOLPROBE # v4.67★ 公共卡池探测(见 core/pool_probe.py 顶部说明)
from ui import resource_tab as RESTAB # v4.68★ 资源替换 Tab
from ui.widgets import open_lord_skill_picker  # v4.156★ 主公技能ID选取器
# v4.23★ 清理: 此处原有两行注释掉的 `from core.hero_picker import HeroPicker` /
#   `from core.img_hash import ImgHasher` —— core.hero_picker 与 core.img_hash 这两个模块
#   在项目里**根本不存在**, HeroPicker 也从未被实例化(见下方 self.hero_picker 的清理)。
#   留成注释会让人误以为"有现成组件、只是没启用", 故删除。

import sys
import subprocess
import webbrowser
import platform

# --------------- Constants ---------------
# v4.22★ 与游戏页油猴脚本(bridge/sgs_bridge.user.js 的 @version 与 SCRIPT_VER)统一编号,
#   避免界面同时出现两个不一致的版本数字(标题栏/关于框 vs 底部脚本状态)。
#   历史: Python 端曾独立编号至 v3.55; 自 v4.22 起跟随脚本版本号。
# v4.70★ 与脚本同号。本版是【资源替换】这一条链的收口(用户连问了好几条):
#   ① 换肤/换背景【内置进桥接脚本】—— 不用 Redirector / GoRes, 也不用第二个油猴脚本;
#   ② 资源服务不再单开端口: /rules 与 /img/xxx.png 挂到助手主端口(见 core/bridge.py),
#      免得"忘了点启动图片服务 → 规则指向空端口 → 图配好了却没生效";
#   ③ 「目标类型」多了「官方武将立绘」: 按武将名挑服务器上那张(棋子立绘/武将牌面/主公立绘
#      共 581 条, 其中 19 位武将有两套, 如 陆逊 luxun / luxun_02), 自定义照旧。
#   → 所以 MIN_SCRIPT_VER 抬到 4.70: ①② 都是"新说明配旧脚本会静默什么都不发生"。
# v4.64★ 与脚本同号(sgs_bridge.user.js 的 @version / SCRIPT_VER)。
#   本版新加的两个能力 —— 「随征」步骤、「用尽锦囊」的步骤级排除名单 —— 都只被
#   【新导入的预设】用到(招募流 / 999-F), 老剧本在 4.63 脚本上照样跑。
#   所以 MIN_SCRIPT_VER 保持不动(仍 4.61), 不因为这两个新预设平白逼所有人升级;
#   新预设的说明里已经写明"需要 v4.64+"。与 v4.43/v4.62 是同一种取舍。
# v4.66★ 与脚本同号。本版(含上一版 v4.65 的挑目标改动)大部分是【脚本侧】行为:
#   v4.65: 选目标"先精确再模糊"(填"张飞"不再把"神张飞"一起遣散/挑走) + 「用锦囊作用到」
#          补上 阵上2~7格/手牌/最后一张;
#   v4.66: 条件「数量」(攒够3张/手牌≥8张) + 循环「提前跳出条件」 + 步骤「失败重试N次」
#          + 新步骤「等到阶段」「挑空并进入下一轮」 + 买卡那条路的商店匹配同样先精确再模糊。
#   → 所以 MIN_SCRIPT_VER 必须抬到 4.66: 旧脚本会把新步骤当"未知步骤"跳过、把数量条件
#     退化成"有没有那张卡", 而这两种现象都长得像"剧本写错了"(见下面对 MIN 的说明)。
# v4.67★ 与脚本同号。本版加的五件事(①②在桥接端, ③④在两端, ⑤只动助手端)：
#   ① 剧本条件「存活家数 / 对手最高血量」—— 局面快照来自服务器下发的玩家名单;
#   ② 「哪些卡」新增【全场已见】口径 —— 卡池是公共的, 只数自己那份会把概率算乐观
#      (用户原话: "这个卡池剩余张数不对, 因为是公共卡池");
#   ③ 「先跑前 N 步看看」(试运行) + 列表里正在跑的那一行点亮;
#   ④ 剧本级「最长 N 秒」总时长封套 —— 跑满走「中止收尾」, 不是暴力掐断。
#   ⑤ 「公共卡池探测」(快捷键页新按钮) —— 由"能不能探到公共卡池"这个问题而来。
#      先离线取证(反编译源码 1.4MB + 赛季配置 + 抓包结构, 5 条依据, 见 core/pool_probe.py),
#      再做成真机可跑的探针; 产出是把这件事一次性钉死 + 顺手确认了能用的替代数据
#      (配置表 ChessShopConf 的发牌权重: 等级→各费用概率)。
#      ⑤ 只动助手端, 【不需要更新脚本】: 探针走 eval 通道下发, 不写进油猴脚本 ——
#      探测要反复改, 而每改一次脚本用户就得重新注入 + 刷新游戏页, 成本全压在用户身上。
#   → MIN_SCRIPT_VER 必须抬到 4.67: 这四件旧脚本【会静默做错事】——
#     新条件会退化成"变量值看不懂 → 按 0 算"; 全场已见会只数自己那几张;
#     试运行参数会被忽略(用户以为只跑 3 步, 实际整份剧本真跑完, 该花的钱都花了);
#     封套参数会被忽略(设了"最长 60 秒"却一直跑)。都没有"无害降级"一说。
# v4.68★ 与脚本同号。本版四件事:
#   ④ 「设置变量」把用法全部写进界面(用户问"这个怎么用?"), 并把值从"只能是数"放开成
#      "也能是文本/列表"——这就是"数组怎么用"的答案: 值写 孙策,周瑜,孙尚香, 条件里选
#      「变量」+「有几项」判够不够3个, 或 +「里面有没有某个词」判抽到孙策没。
#      老代码把不是数字的值【按 0 存下去】只留一条很快被刷掉的 ⚠ —— 用户明明设过,
#      后面条件却全判不成立, 只会以为脚本坏了。这是又一个"不报错但做错事"。
#      (为此 showWhen 新加了 {"all": [...]} 的「与」写法: 「比较」「数值」两行必须在
#       check=var 且 不是「里面有没有某个词」时才显示, 否则用户会以为还得填个数)
#   ① 新步骤「逐条用锦囊」—— 由用户"再写一个用尽锦囊、老的别改"而来。粒度从"整步排除名单"
#      细到"每张各自给谁": 一步里可以写 瞒天过海·六=阵上1, 破釜沉舟·六=手牌:孙策, ...
#      哪一条不想用就在前面加 #。原「用尽锦囊」一个字没动, 内置预设也只是【新增】。
#   ② 「作用到」支持直接带名字(手牌:孙策 / 阵上:周瑜) —— ①要的是"一条文本里同时写清
#      用哪张锦囊和给谁", 而这一步没有第二个输入框能填"指向某个武将"。
#   ③ 顺手修掉两个静默错(见下, 都是"不报错但做错事"那一类):
#      · 「使用锦囊」失败时返回的那个对象【没带 code】, 于是"这张锦囊还没抽到"(常态)
#        会被下游掉进 else 分支当成【真失败】——逐条版首跑时日志会红一片;
#      · 后端 dev/_verify_static.py 遇到语法错误时把 SyntaxError 对象当语法树往下传,
#        每个调用方的 `if tree is None: continue` 全不成立, 整套自检崩在
#        AttributeError: 'SyntaxError' object has no attribute '_fields'。
#        现象是"改坏一个引号 → 自检挂掉且看不出是哪一行"。现在单列一条 S0 并指名文件:行号。
#   → MIN_SCRIPT_VER 抬到 4.68: 旧脚本会把「逐条用锦囊」当"未知步骤类型"【静默跳过】——
#     用户以为手上锦囊已经用完了, 实际一张没动(而这一步存在的唯一意义就是把它们用掉);
#     另外 ②那个写法在旧脚本上会退化成"手牌第一张": 锦囊用出去了、目标却是别人,
#     不报错不提示。没有"无害降级"一说。
# v4.73★ 通信层加固版: 修复 psend 批量忙等冻结页面 1.2s / push 失败丢命令结果 /
#   push与pollCmd HOST切换互相打架 / 诊断上报与xcPush硬编码127.0.0.1 / get_learned_ids
#   把对象当pid 五处实锤断连·卡顿类 bug(详见 bridge/sgs_bridge.user.js 头部 v4.73 更新日志;
#   配套 core/id_map.py 补上缺失的 merge(), 诊断自动抓ID入库自 v3.x 起从未生效)。
#   无功能与协议变化, 本可不抬 MIN —— 但 v4.71 起"三处同号"已成口径(dev/_verify_scenario
#   的自检也按同号断言), 脱钩反而多一个特例, 所以跟着抬到同号。
# v4.76★ 赛季更新页 + 脚本侧「防重复注入守卫」; v4.77★ 「立即停止」(见下面 MIN_SCRIPT_VER 前的说明)。
# v4.83★ 默认值改回"从最右边开始用" + 优先名单有了界面入口 —— 用户原话"Shift+2/Shift+3 按识别
#   出来的最右边开始使用" + "没看到这个啊? 还有怎么改? 怎么使用? python端有吗? 需要新加tab吗?"。
#   ① v4.82 把默认改成"取识别到的第一张(手牌最左)"是行为变更, 不是用户要的 → 默认改回【最右】
#      (脚本 Shift+2/Shift+3 与剧本「随征」的 order 默认值), 想取最左要显式写;
#   ② 「快捷键」页新增【锦囊/随征 优先名单】输入框(锦囊名单 / 随征名单 / 随征给谁 + 两个开关),
#      点"保存并推送"即写进 config.json 并下发游戏页, 连接建立时也会自动带过去 —— 不用新加 tab。
# v4.82★ 锦囊/随征改成"先识别, 再使用" —— 用户原话"不是像 shift+2、shift+3 那样只识别最右边;
#   改成识别到随征卡; 和锦囊卡, 使用方法一样"。脚本侧新增统一识别层(全扫手牌, 带位置/张数,
#   支持名字顺位), 剧本「随征」和 Shift+2/Shift+3 共用一套; 顺手修掉"随征名单里填技能ID时
#   剧本不认"的静默失效; 快捷键入口 __sgsHotkey 支持 {names/right/useAll/target} 传参。
# v4.81★ 新步骤「遣散锦囊」(recycleSpell) —— 用户原话: "遣散锦囊不可用, 用不了"。
#   以前想卖掉手里的锦囊只能拿【遣散】那一步去写, 而它是照【武将】找目标的: 锦囊用的是
#   另一套 ID(spellID), 助手端按武将表反查不出 ID → 页面端只剩"按名字比对"这一条路,
#   名字一没解析出来就退化成裸 ID → 落空, 现象就是"手牌里明明有这张, 却报没找到要遣散的"。
#   新步骤只认【手牌里带 spellID 的卡】, 目标留空 = 全清, 发完再复查那张是否真离手。
#   → MIN_SCRIPT_VER 抬到同号: 旧脚本会把它当"未知步骤类型"【静默跳过】—— 用户以为
#     锦囊已经清掉了, 实际一张没动(而这一步存在的唯一意义就是清掉它们), 没有无害降级。
# v4.80★ 新步骤「用尽锦囊·按用法」(spellSmart) —— 用户原话"可以先看手牌有什么再去执行对应的":
#   开工先扫手牌, 一张张看: 用法表里写了的按写法一发命中(不试目标), 没写的按策略
#   (自动轮目标 / 留着不动)。与「用尽锦囊」「逐条用锦囊」并存, 那两个一个字没动。
# v4.85★ 新增「节奏」页 —— 把脚本里那些【写死的延迟】逐项搬到界面上(用户原话:
#   "这个新开一个 python 端的 tab 页都可改? 且加上说明? 尤其是遣散武将")。
#   以前那些毫秒数只能靠【剧本倍速】整体压缩, 而倍速连"等服务器结算"一起压掉 →
#   日志里常见"包发了但没生效"; 现在每一项单独填(遣散武将每张之间、轮询片、手牌补刷、
#   快捷键补刷、手牌哨兵、Shift+3 连发间隔、停止检查粒度), 每一项都带说明。
# v4.84★ 「随征」的等待能调低了(用户原话: "这个延迟可以改低吗?"):
#   ① 新增【两张之间等多久 gapMs】—— 以前两张之间也用 waitMs, 想发快就得把"最后一张发完后的
#      结算等待"一起压掉, 于是日志常见"发了但没生效"; 现在只调 gapMs 就能连发。
#   ② 两个等待都【允许填 0 = 真不等】—— 旧脚本写的是 `parseInt(v) || 500`, 填 0 会被当成
#      "没填"退回 500ms, 越改越慢(而且界面上改了旧脚本也不认 gapMs, 静默按老一套跑)。
# v4.90★ 3选1【自动选】按实机取证改对(与脚本同号, MIN_SCRIPT_VER 抬到 4.90):
#   取证 15.txt 里, 助手自己发的挑选包是 {index:1, selectTyp:1, goodsID:0, spellID:0}
#   → 服务器 errCode=3 BE_InvalidParams; 而手动点那条成功包是
#   {index:0, selectTyp:2, goodsID:0, spellID:310014} → errCode=0 且带回 spell 实体。
#   结论: 必须带这一档候选的【真实 ID】, 而且这台客户端锦囊 selectTyp=2(武将=1)。
#   旧脚本会继续发全 0 的包(服务器直接退), 所以必须抬 MIN —— 不抬的话点了"自动选"
#   只会看到"已发送", 实际一张都没挑到。
# v4.89★ 3选1 收进常驻脚本(与脚本同号, MIN_SCRIPT_VER 抬到 4.89):
#   挑武将/挑锦囊/挑对手武将那个窗, 以前只能靠控制台粘贴型脚本 refs/EasySelect3.js ——
#   它的 get3State 等四个全局函数是最后一行挂上去的, 不整段粘进控制台跑一次就没有
#   (直接敲必然 ReferenceError)。用户原话: "我确实不会去安装 EasySelect3.js, 装太多
#   脚本很乱"。现在同样能力收进桥接脚本(SgsSelect3), 控制台能直接敲, 助手也能下发。
#   新指令 select3_state / select3_auto / select3_force —— 旧脚本没有, 不抬 MIN 的话
#   点按钮只会得到一句"游戏页没有回应", 看不出是脚本旧了。
# v4.88★ 两个新页 + 脚本侧两个新模块(与脚本同号, MIN_SCRIPT_VER 抬到 4.88):
#   ① 『统计』页: 把上游那份独立油猴 TavernChessStats 的统计口径搬进桥接脚本 ——
#      包装 manager 的 ReqShopRefreshChess / ReqShopBuyChess / ReqChessUseSpell /
#      updateShopGoods / onRespShopRefreshChess, 记 刷新(次数/虎符/是否自动)、购买
#      (次数/虎符/买了什么)、商店快照(每张卡出现几次)、锦囊(用了几次哪张)。
#      新指令 stats_start/stats_stop/stats_reset/stats_dump —— 旧脚本没有, 所以必须抬 MIN:
#      不抬的话点「开始记录」只会得到一句"游戏页没有回应", 用户看不出是脚本旧了。
#   ② 『机制』页(陆逊小抄): 循环「用手牌第 N 张锦囊 → 同步手牌 → 刷新手牌区」。
#      不转发上游 AutoUseSpell.js: 那份的 startLuXun/stopLuXun/toggleLuXun 【只有调用、
#      没有定义】(点了就是 ReferenceError), 而且它"同步手牌"的兜底是 ReqShopRefreshChess
#      —— 那是刷商店, 会真扣虎符。所以重新写进脚本(SgsLuXun), 新指令 luxun_*。
VERSION = "4.195"   # ★ 必须与 bridge/sgs_bridge.user.js 的 @version / SCRIPT_VER / __SGS_VER
                    #   四处同号(静态自检会比对)。改脚本版本时这里要一起抬 —— 否则
                    #   助手版本落后于脚本, 界面上显示的还是旧号, 排查时极易误判。
# 游戏页脚本最低可用版本: 低于此版本时 HUD 按钮 / 列表推送等特性不可用(工具栏会提示更新)
#   v4.44★ 提到 4.44: "多档挑选一次挑空 + 用尽锦囊的二段挑牌窗"只有脚本 v4.44 才修好。
#     v4.41/4.42 的判据是错的(游戏开窗时会把那一档从 waitSelectChessSpellDic 里删掉, 于是
#     "窗口正开着等人答"时它反而读成 0), 结果诸葛亮「招募时挑选N张当前营帐等级的锦囊」
#     只挑下来一个(手牌只多一种锦囊), 屏幕上留一堆没人答的卡 → 游戏提示「有未选择的卡牌」
#     并且整个棋盘禁止操作; 珠联璧合/草船借箭 的二段挑牌窗同样白弹。
#     停在 4.43 及以下就是"能挑但挑不干净 / 用锦囊后卡住操作不了", 所以这里主动提示更新。
#   v4.43★ 【停在 4.42, 没有跟着 VERSION 涨】: 本版加的是纯界面功能『技能库』tab
#     (查 ChessSkillConf.SkillID / 主公ID / 主公技能ID, 并能把 ID 灌进『随征卡牌ID』),
#     不依赖脚本端任何新能力 —— 脚本 v4.42 的界面照样能用。跟着涨只会平白逼老脚本用户升级。
#     配套的非界面改动: 推送/替换时补写 data/lords.json / lord_skills.json(此前恒为空), 也是纯 Python 侧。
#   v4.42★ 提到 4.42: "用完锦囊后自动答掉二段挑牌窗"这一环只有脚本 v4.42 才有。
#     珠联璧合(挑选1张等级N的武将牌) / 草船借箭(挑选你上个对手的1个武将) 这类锦囊,
#     用出去之后服务器会再弹一个挑牌窗; 4.41 会把它留在屏幕上, 导致游戏一直提示
#     「有未选择的卡牌」并把玩家操作禁掉。停在 4.41 就是"其实已经选完了但操作不了"。
#   v4.41★ 提到 4.41: 「用尽锦囊」这一步和"挑完自动续挑挑空"都只有脚本 v4.41 才有 ——
#     旧版不仅没有 useallspells, 还会在挑完锦囊后打出假警报
#     (『挑完之后服务器还在等挑选』: 它拿候选池当待挑队列判的), 并让挑选窗口留在屏幕上。
#   v4.40★ 提到 4.40: "选人型当先"(丁奉 330411「移除1个友方」)真正生效必须有脚本 v4.40 ——
#     旧版把上阵包交给【游戏自己代发】(ReqChessLineUp(GameContext.LineUpArr, true)), 而那个
#     LineUpArr 只有"玩家拖牌"时才会被预置, 我们走协议直发从来没预置过 → 它恒为 undefined,
#     发出去的就是一个没有 positionChess 的空换阵包(实机抓包: {"tableID":8747,"changeSeat":true}),
#     服务器收到等于没收到: 卡没上场、当先自然不生效。v4.40 改成选人响应到后由脚本自己补发,
#     并且用 t=true 绕过 CanOperate 门禁。脚本停在 4.39 及以下时这一环修不好,
#     所以这里主动提示更新, 而不是让用户对着"卡没上阵"继续猜。
#   v4.47★ 提到 4.47: 「挑锦囊」该不该发包的判据(windowOpen)只有脚本 v4.47 才有 ——
#     4.44~4.46 看的是"服务器还有几档待处理", 那不等于"这一档的窗口现在就开着等人答",
#     抢答未开窗的档会被服务器无视 → 症状就是用户报的「执行时一直跳还有未选择卡牌、棋盘点不动」。
#     停在 4.46 及以下时这一环修不好, 所以这里一并提示更新。
#   v4.49★ 提到 4.49: 「挑目标/挑锦囊/用尽锦囊」该挑【哪张】的依据在 v4.49 才收口 ——
#     4.48 及以下先拿被服务器刷新覆盖的候选池按档号重推候选, 推不出来才看窗口快照, 于是
#     池子一刷新就会挑到跟屏幕上不一样的卡 → 实机症状正是用户报的「挑了没反应 + 还有未选择的卡牌」。
#     权威来源钉死为游戏自己建的窗口快照 WaitSelectCards(依据见 core/pick_diag.py 的判决与
#     dev/_verify_pick.js 的 T10)只有脚本 v4.49 才有。停在 4.48 及以下时这一环修不好,
#     所以这里一并提示更新。
#   v4.56★ 提到 4.56: 「记数值快照」这一步 + 「数值变化」这个条件是脚本 v4.56 才有的。
#     停在 4.55 及以下时, 这类剧本跑起来只会打一条"未知步骤类型: snapStat"然后静默跳过该步,
#     条件那边还会退化成卡牌判定 → 用户看到的是"条件不成立"而不是"脚本太旧", 完全找不着北。
#   v4.61★ 提到 4.61: 4.56~4.60 的「数值变化」【恒判不成立】—— 它读的是卡的基础 attack/hp
#     (那个数从头到尾不变), 而真正会涨的是 totalAttack/totalHp(实机抓包 data/net_samples.json
#     同一张卡: attack 恒为 2, totalAttack 11→16→21)。症状是日志写「陈武第1格 攻击 2→2(+0)」,
#     看着像"技能没生效"或"剧本写错了", 其实是脚本读错了字段 —— 所以 4.56 那句"够用"已失真,
#     这里提到 4.61。数值条件在 4.60 及以下同样不可信, 一样提示更新。
#   v4.62★ 【没有】跟着提 MIN(仍留在 4.61): 本版修的是"商店空格子(买走卡后留下的未初始化
#     对象)被当成一件商品" —— getInfo 收进三无条目 / actBuyCard 缺 goodsID 守卫 / actBuyAll
#     未先挡空货。它防的是"万一发出一个空货购买包"(只有白名单关闭或目标为空时才走得到),
#     现有剧本与「数值变化」判定的正确性都不受影响 → 停在 4.61 的用户不会因此跑错,
#     没必要逼着升级(同 v4.43 的先例: 不为纯加固改动抬门槛)。
#   v4.66★ 提到 4.66(从 4.61 一次抬到位): v4.65~v4.66 加的东西全是脚本侧行为 ——
#     · 新步骤「等到阶段」「挑空并进入下一轮」: 旧脚本会打一条"未知步骤类型"然后【静默跳过】;
#     · 条件「数量」(攒够3张/手牌≥8张): 旧脚本不认识 count, 会退化成"手牌里有没有这张卡"
#       —— 判成"有"就跳出/继续, 用户看到的是"条件写错了", 而不是"脚本太旧";
#     · 循环的「提前跳出条件」、步骤的「失败重试」: 旧脚本一律不认(条件不生效、失败直接中断);
#     · 遣散/挑牌/用锦囊/买卡的选目标改成"先精确再模糊": 这一条是【修错】—— 旧脚本填"张飞"
#       会把"神张飞"一起遣散/买走(v4.65 之前就是这样), 所以停在旧版本不升级是"继续踩坑"。
#     这五件事都没有"无害降级"一说, 所以这次主动抬门槛(与 v4.56 同一种取舍)。
#   v4.67★ 提到 4.67: 本版四件事同样没有降级路径, 而且"静默做错"的那一条更贵 ——
#     · 「试运行前 N 步」: 旧脚本不认这个参数 → 用户以为只跑 3 步, 实际整份剧本真跑完,
#       该买的卡买了、该花的金币花了。这是【实打实的经济损失】, 不是显示问题;
#     · 「最长 N 秒」封套: 旧脚本不认 sc.maxDuration → 兜底形同没有, 循环/等条件卡住就一直卡着;
#     · 局面条件(存活家数/对手最高血量) + 「全场已见」: 旧脚本按 0 算 / 只数自己那几张,
#       结论会偏乐观(见上面对 MIN 的说明)。
#   v4.68★ 提到 4.68: 本版新加的「逐条用锦囊」是【新步骤】, 旧脚本打一条"未知步骤类型"
#     就静默跳过 —— 而这一步的用途正是"把手上的锦囊挨张用出去", 静默跳过 = 用户以为
#     用完了、实际一张没动。另外「作用到」里写 手牌:孙策 这种名字, 旧脚本会当成普通的
#     「手牌」分支 → 静默指到【手牌第一张】(锦囊确实用掉了, 只是给了别人)。
#     这两条都不是显示问题, 所以跟着 VERSION 一起抬。
#   v4.69★ 提到 4.69: 本版三件事都是"新写法在旧脚本上会静默做错事", 没有无害降级 ——
#     · 【优先级】写法(A > B > 3): 旧脚本把 "瞒天过海·六 > 联军盛宴·五" 当成【一个字面的
#       名字】→ 永远匹配不上 → 落到"候选里找不到就默认选第 1 个" —— 挑牌挑到谁完全看运气;
#     · 「逐条用锦囊」等号右边【留空】= 自动轮目标: 旧脚本里留空其实只发"牌库第一张",
#       于是库里那些没写右值的行会静默指错目标(或不生效), 界面上完全看不出为什么;
#     · 内置预设升级成【整季锦囊全列出来、右值留空】—— 它依赖上面那一条才成立, 所以
#       旧脚本配合新预设会一条条往牌库第一格上招呼。三件事都必须跟着抬门槛。
#   v4.70★ 提到 4.70: 【换肤/换背景已内置进桥接脚本】(用户原话 "我不想再加一个扩展插件: gores了")。
#     为什么这也得抬门槛: 资源替换页这一版会告诉用户"改完规则最多 15 秒自动生效、不用再导入
#     GoRes" —— 但 4.69 及更早的脚本里【根本没有这段请求改写代码】。用户照着页面说明配好规则、
#     刷新游戏, 屏幕上什么都没变, 而页面又没有任何报错 → 只能怀疑"是不是我图放错了/规则没生成",
#     然后一遍遍重配。这是最典型的那种"不报错但做错事", 所以跟着 VERSION 一起抬。
# v4.71 也跟着抬到同号: 本版脚本逻辑虽零改动(只是"脚本本体进了全能扩展"), 但门槛与
#     VERSION 脱钩会让"三处同号"的排查口径永久多一个特例 —— 接受"多拦一次旧脚本"换口径统一。
# v4.74★ 资源替换这一版把用户现场报的四件事一起收了(用户原话: "确实没有显示完整啊; 并且没有
#   保存按钮; 资源替换下面的背景打开也没有替换啊; 左上角的赛季也没有效果啊" + "这些(刘备皮肤
#   500103 / 神马超 621401 / 动态神马超 821401)能直接也加到可以替换的资源里面吗"):
#   ① 「添加自定义规则…」弹窗看不到「保存」: 高度写死 820x430, 而 9 行内容实测需要约 470px,
#      最后一行(保存/取消)被裁到可视区外 → 改成"按钮行钉在窗口底部 + 内容超高可滚 + 高度按内容
#      自适应(上限屏幕 85%)";
#   ② 「用作背景」配了不生效: 候选 URL 是从诊断包 atlasFrames 的【帧名】拼出来的 —— 游戏加载的
#      是整张图集(会单独请求 …/xxx.atlas 与 …/xxx.png), 从来不单独请求帧名, 所以那批地址是死地址
#      (而且键是脚本 slice(-70) 的尾巴, 域名都是残的)。现在只从"真实 URL"取: 脚本新报的
#      resUrls(页面性能条目) + netEvts + 图集 .atlas 对应的同名 .png + 官方资源清单;
#   ③ 「用作背景」选了别处的图就 404: /img/<文件名> 以前只在 我的图/ 里找, 而规则目标可以是任意
#      本地文件(界面上「浏览 …」选的就是任意图) → 现在按 我的图/ → 参考_配置表/资源替换/ →
#      规则里的同名目标 三处找(core/res_server.find_image_file);
#   ④ 「赛季」填了没效果: csv_loader 对已经带 s 的赛季名又拼了一个 s(`s10` → `ss10`), 主路径永远
#      不存在、只能靠"扫到哪个赛季算哪个"兜底(所以填什么都一样); 界面上那个框还一直空着、不绑任何
#      事件 → 改成下拉(data/season 下真实存在的赛季, ★ = 有配置表)+ 选中即重读 + 状态栏写出
#      "实际读的是哪张表"。
#   新增: 「官方大图 / 皮肤资源」清单(data/official_resources.json), 两条获取路 —— 扫描游戏已加载
#      资源(脚本 dumpRes, 零猜测) / 按编号探测(HEAD 直接问官方在不在), 并内置用户提供的 6 条样本
#      (500103、500104、621401、821401 的 beijing/daiji/daiji_2, 都带真实宽高与大小)。
#   → MIN_SCRIPT_VER 抬到 4.74: 「扫描游戏已加载资源」发的就是脚本 v4.74 才有的
#     window.__sgsResSwap.dumpRes(), 旧脚本上点这个按钮只会提示"页面里没有换肤模块"。
#   v4.75★ 脚本本体零改动(纯助手/图库页侧: OL 皮肤表/裁剪/模拟/试听/模态框), 本可不抬 MIN ——
#     但 v4.71 起"三处同号"已成口径(_verify_offline 的封套/锦囊两项自检按同号断言),
#     脱钩反而多一个特例, 所以跟着抬到同号(与 v4.73 那次同一理由)。
#   v4.76★ 提到 4.76: 脚本侧加了「防重复注入守卫」—— 油猴脚本与全能扩展(bridge/sgs_ext)是
#     同一份代码, 两个都装(或一前一后一旧一新)会跑两遍, 现象是"快捷键按一次触发两次 / 更新了
#     不生效"。守卫是新脚本才有的行为, 停在旧脚本的用户仍会双跑 → 所以跟着抬门槛
#     (v4.75 那次是纯 Python 侧、留着没抬; 这次不能再留 —— 免得"什么时候该抬"变成看心情)。
#   v4.77★ 提到 4.77: 「立即停止」这一版把用户报的"点了停止还在跑"修在了脚本侧 ——
#     用户原话: "那个剧本停止执行, 我感觉很慢, 不能立马实现"。
#     旧脚本的停止是【协作式】的(只在两步之间看一眼标志), 于是点了停止之后当前这一步会照旧跑完:
#     「等待 30000 毫秒」等满 30 秒、「刷新商店 x10」把剩下的刷完(真花钱)、「失败重试 9 次」
#     一次不落地重试完。新脚本改成【打断式】: 等待分片、命中停止就把整条 await 链一次退到底,
#     内部循环也逐个补了检查, 而且停止不再显示成红字失败。
#     助手端这一版会把按钮与日志写成"立刻中断" —— 配旧脚本就是"界面承诺了、脚本做不到",
#     所以跟着 VERSION 一起抬(与 v4.56 / v4.67 同一种取舍: 没有无害降级路径)。
#   v4.78★ 提到 4.78: 修用户报的"挑诸葛亮剧本 → 选不完, 窗口消失, 游戏一直提示
#     「有未选择的卡牌」但已经没有窗口"(实机日志 log.txt.1 11:35~11:37 卡了 84 秒):
#     那种状态下客户端 WaitSelectCards 空 + curIdx<0 + 待挑字典空, 只剩 chessList 里
#     3 张"裸卡档"(直接带 goodsID 的项)没人答 —— 旧脚本只认 selectChess/chessList/list
#     嵌套数组形态, 把它们当"没有候选"跳过, 永远判"没在等"。旧脚本配新剧本 = 同一坑。
#     另: 「用尽锦囊」开工先扫手牌报总账 + 从右往左用、「逐条用锦囊」手牌里没有的名字
#     直接跳过不挨个试、「随征」新增 allToOne(全给一个人一次发完) —— 三者旧脚本都会
#     【静默按老一套跑】, 界面承诺的省时/总账全做不到, 所以 MIN 跟着抬到同号。
#   v4.80★ 提到 4.80: 新步骤「用尽锦囊·按用法」只有脚本 v4.80 才认得(_scOpSpellSmart),
#     旧脚本会把它当"未知步骤类型"【整步静默跳过】—— 现象就是"这一步明明执行了, 锦囊却
#     一张没用"。它存在的唯一意义就是把锦囊按用法用掉, 没有"无害降级", 所以抬到同号。
#   v4.83★ 提到 4.83: 优先名单界面依赖脚本 v4.82 的 updateSpellPriority / updateFollowupPriority,
#     而"默认取最右"这个默认值是 v4.83 才改回来的 —— 旧脚本上界面写着"从最右开始"、实际却取
#     最左, 说明与行为不一致, 排查必跑偏。没有无害降级。
#   v4.85★ 提到 4.85: 「节奏」页下发的每一项延迟都只有脚本 v4.85 才认得 —— 旧脚本没有
#     window.SGS_TIMING / applyTiming, 界面上改了【静默按写死的老值跑】, 用户会以为"没生效"。
#     这类"界面承诺与实际行为不一致"正是排查时最容易跑偏的, 没有无害降级。
#   v4.84★ 提到 4.84: 「随征」新增【两张之间等多久 gapMs】并把两个等待都允许填 0 ——
#     旧脚本不认 gapMs, 界面上改了也【静默按 waitMs 跑】(而且旧脚本把 0 当成没填、退回 500ms,
#     越改越慢), 用户会以为"调了没用/反而更慢"。没有无害降级。
#   v4.107★ 提到 4.107: 七个新步骤(卖到只剩N张 / 刷买直到金币<N / 对调阵上两格 / 暂停等
#     你确认 / 响一声 / 条件用锦囊 / 记本局快照)只有脚本 v4.107 才认得, 旧脚本一律当
#     "未知步骤类型"【整步静默跳过】—— 现象是"剧本跑完了, 这步像没写一样";
#     另外「摆位」新增【按名单】和 levelUp 补的 count/keepGold, 旧脚本会【按老行为跑】
#     (名单被忽略、保留金币被忽略)。都没有无害降级, 所以抬到同号。
#   v4.171★ 提到 4.171: 锦囊三个缺口一起做的三项能力(用户 2026-10-27 口径), 旧脚本【都会
#     静默做错事】而不是"不支持而已", 所以必须抬到同号:
#       ① 四个锦囊步骤的「后续选择」(postPickKind/postPick, 以及逐条/按用法每行末尾的
#          「挑:武将:2」): 旧脚本认都不认这两个键, 照样【挑候选第一个】—— 用户以为挑的是
#          "第 2 个武将", 实际拿的是第 1 个; 「挑:」那一段还会当成作用目标的一部分去找目标
#          (目标找不到 → 那张锦囊等于没用)。更易 nudge 的是: 普通「使用锦囊」那条路径以前
#          压根【不答二段挑牌窗】(只有"目标留空=自动轮"那支会答), 旧脚本会把草船借箭的挑选窗
#          挂在屏幕上禁操作 —— 这属于旧脚本的行为 bug, 升级是最干净的修法。
#       ② 遣散(武将)的「最多遣散几张」: 旧脚本忽略它 → 【要遣 1 个、实际全遣】, 这是"按翻倍
#          的相反方向做错", 且无法撤销(武将遣散是收不回来的)。没有无害降级。
#       ③ 条件「卡」形态的类别(kind): 旧脚本忽略它 → 「手牌没有锦囊」这条会被判成【恒真】
#          (等于什么都没判), 整个 if 分支就此跑空 —— 又是"看着生效其实没生效"。
#     共同点: 三项都是"界面上有框、填了没反应/反应是错的", 排查成本极高, 所以抬版本。
MIN_SCRIPT_VER = (4, 196, 0)
FH = "Microsoft YaHei UI" if platform.system() == "Windows" else "Arial"

# Colors
BG = "#2a2a2a" # Background
FG = "#eeeeee" # Foreground
DIM = "#999999" # Dimmed text
WARN = "#ffcc00" # Warning text
RED = "#ff5555" # Error/danger
ORANGE = "#ffa500" # Orange
GREEN = "#5cb85c" # Green
GOLD = "#e0b060" # Gold/highlight
BLUE = "#7fd4ff" # Blue
BTN = "#3a3a3a" # Button background
ACT = "#4a4a4a" # Active button
# 注意: Tk 只认 #RGB / #RRGGBB / 颜色名 —— 写成 #RRGGBBAA(带 alpha) 会直接抛
#   TclError: invalid color name。此处原来误写成 "#0055aaff", 因为全项目没人用它
#   (各列表各自硬编码 "#2a6db5") 所以一直没暴露; v4.27 「剧本」列表是第一个使用者,
#   一启动就崩在 _build_scenario_tab()。dev/_verify_static.py 已加校验防复发。
SEL_BG = "#0055aa" # Selection background (e.g., listbox)
SEL_FG = "#ffffff" # Selection foreground

# v4.25★ "遣散前先上阵"的落点选项 (界面文字 → 下发给油猴端的表达式)。
#   顺序有讲究: 第0项必须是"不上阵" —— 它是默认值, 也代表"与旧版行为完全一致"。
#   与 bridge/sgs_bridge.user.js 的 _resolveDeploySlot() 里的取值一一对应。
# v4.26★ "倒数第二格"的含义 = 【上完后的】倒数第二格(已上阵N人 → 第N格; 已上阵6人 → 第6格);
#   落点被占时那张卡及其右侧整体右移一格, 新卡真的占住该格(旧版是退到第一个空位, 阵满时形同虚设)。
DEPLOY_POS_OPTIONS = ["不上阵", "倒数第二格", "第一个空位"] + [f"第{i}格" for i in range(1, 8)]

# v4.109★ 剧本页的常量与模块级工具已抽到 ui/scenario_data.py
#   那边显式写了 __all__, 所以下划线开头的函数也会一起过来, 这里照旧用。
from ui.scenario_data import *
from core import silence as _SIL


def _season_errmsg(e):
    """赛季任务报错 → 翻成人话 + 给一条能直接照做的命令。

    v4.35★ 专治 `wasmtime is required. Install with: python -m pip install wasmtime`:
    原文是一句英文包名提示, 用户拿到它不知道 ① 这依赖是干什么的 ② 该往【哪个】python 里装 ——
    机器上常有好几个解释器(本项目实测至少两个 venv), 装错地方再点还是同样的报错, 容易卡死。
    这里直接把 sys.executable 填进命令里, 复制就能用。
    """
    m = str(e) or e.__class__.__name__
    low = m.lower()
    if "wasmtime" in low:
        return ("缺少解密依赖 wasmtime —— 官方 Config.sgs 是 wasm 加密的, 没它解不出来。\n"
                "  请在【助手正在用的这个 python】里安装(复制整行到终端执行):\n"
                '    "%s" -m pip install wasmtime\n'
                "  装完直接再点一次本按钮即可(不用重启助手)。" % sys.executable)
    if "no module named" in low:
        return ("缺少 Python 依赖: %s\n"
                "  请在【助手正在用的这个 python】里补依赖(复制整行到终端执行):\n"
                '    "%s" -m pip install -r requirements.txt' % (m, sys.executable))
    return m


# --------------- App Main Class ---------------
class App:
    def __init__(self, master=None):
        print("App.__init__() started")
        self.master = master
        self.root = master if master else tk.Tk()
        self.root.title(f"三国杀自走棋助手 v{VERSION}")

        # v4.??★ 克隆/替换函数注册表(偷梁换柱式): 启动时构建, 供剧本编辑器"已注册函数"列表使用。
        #   用 try/except 包住, 任何失败都只记日志, 不影响主程序启动。
        try:
            from core.fn_registry import get_registry
            self.fn_registry = get_registry()
        except Exception as _e:
            self.fn_registry = None
            _log_err("构建函数注册表失败(不影响主程序): %s" % _e)
        # v4.162★ 默认窗口按"最大化时的舒展布局"呈现(内容不挤), 但不强制最大化:
        #   取【可用桌面区(排除任务栏)】的 ~94%/90% 作为默认窗体并居中 —— 各页签/工具栏
        #   靠 pack/fill 自适应, 窗口一大布局和最大化时一致, 又保留了可拖拽缩小的能力。
        #   字号/控件尺寸一律不动(用户要求"不会动布局"), 纯靠放大窗体让布局舒展。
        #   ⚠ 必须用工作区而不是整屏: 整屏高 1080 时 0.92 算出来的窗体会顶到底、被任务栏盖住。
        _aw = self.root.winfo_screenwidth()
        _ah = self.root.winfo_screenheight()
        _ax = _ay = 0
        try:
            import ctypes
            import ctypes.wintypes as _wt
            _wr = _wt.RECT()
            # SPI_GETWORKAREA = 0x0030 —— 返回不含任务栏的可用桌面矩形
            if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(_wr), 0):
                _aw, _ah = _wr.right - _wr.left, _wr.bottom - _wr.top
                _ax, _ay = _wr.left, _wr.top
        except Exception:
            pass
        #   比例按用户调过一轮: 宽再收 1/5(0.94→0.75)、高再收 1/8(0.90→0.79)。
        _w = max(1200, int(_aw * 0.75))
        _h = max(760, int(_ah * 0.79))
        self.root.geometry("%dx%d+%d+%d" % (
            _w, _h, _ax + (_aw - _w) // 2, _ay + (_ah - _h) // 2))
        self.root.minsize(1200, 760)
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

        # Log to file
        self._log_file_path = os.path.join(DATA_DIR, "log.txt")
        self._log_queue = []
        self._log_max_lines = 500
        # v4.101★ 显示缓冲【与写盘队列分开】。以前日志框和 _log_writer 共用 _log_queue,
        #   而写盘线程是 pop(0) 【消费】它的 —— 于是框里显示的是"还没写盘的那一段",
        #   写线程一追上, 整框内容就被清掉重画(现象: 日志框会突然变少/跳变, 想回看刚
        #   才那几行根本看不到)。现在显示自己留一份(带单调序号, 供增量渲染用)。
        self._log_view = []
        self._log_seq = 0
        self._log_drawn_seq = 0
        # v4.101★ 日志是【后台线程也在写】的(剧本/统计/各种 _run_bg 回调都在工作线程里 log),
        #   所以缓冲要用锁保护; _main_tid 用来判断"现在是不是主线程"(见 log() 里的说明:
        #   后台线程绝对不能碰 Tk 的 after)。
        self._log_lock = threading.Lock()
        self._main_tid = threading.get_ident()
        self._log_level = "DEBUG"
        # v4.51★ 日志重画改为"标脏 + 合并刷新": 后端刷屏时不再每条都重建一次 Text,
        #   而且只在『日志』这一页可见时才真的重画(其它页只记脏, 切回来再刷)。
        self._log_dirty = False
        self._log_display_scheduled = False
        self._log_thread = threading.Thread(target=self._log_writer, daemon=True)
        self._log_thread.start()

        # v4.47★ 【协议泵】—— "新功能页读不到对局"的根因就是这个, 补回来。
        #   油猴每 ~1s 把协议流水推给 /push, core.bridge 收进 STATE.net_sink;
        #   但重写 UI 之后【没有任何地方再把它取出来喂 LIVE.feed】(dev/ 下的抓包脚本一直这么喂),
        #   于是所有"协议直读"的功能(对局决策页、卡池已买统计、主公技能挑目标、自动上阵读阵容…)
        #   只剩 DOM 扫描那一点点数据 —— 现象就是"进对局了也读不到"。
        #   单起一个搬运线程: 抽干队列 → LIVE.feed(解析在本线程), 异常吞掉继续
        #   (整局断流比报一条错严重得多)。
        self._live_pump_stop = False
        self._live_stat = {"msgs": 0, "last": 0.0, "err": 0}
        threading.Thread(target=self._live_pump, daemon=True).start()

        # Config
        self.cfg = load_config()

        # Bridge Server
        # v4.160★ 端口必须来自【本实例的配置】(可被 main.py --port 覆盖)。
        #   以前这里是无参 BridgeServer() → 恒等于默认 8765 → 多开时第二个实例
        #   绑不上端口却不报错(只在控制台打一行警告), 浏览器照旧把数据推给 8765
        #   上那个实例 —— 现象是"第二个窗口一直连不上 / 读到的是别人的对局"。
        #   这正是 archive/_verify_static.py 记的那条"改端口后悄悄退回 8765"。
        self.server = BridgeServer(int(self.cfg.get("bridge", {}).get("port") or 8765))
        self.server.start()
        if getattr(self.server, "bind_error", None):
            # 别静默: 绑不上 = 本实例压根没有桥接服务, 后面所有功能都是空的
            try:
                self.log("[桥接] " + self.server.bind_error, level="ERROR")
            except Exception:
                pass
            try:
                messagebox.showerror(
                    "桥接端口被占用",
                    self.server.bind_error +
                    "\n\n本实例没有桥接服务 —— 浏览器推来的数据会全部落到占用者那里。\n"
                    "请换一个端口重启本实例(多开请给每个实例不同的 --port)。")
            except Exception:
                pass

        # v4.160★ 多开: 窗口标题带上实例名 + 实际端口, 5 个窗口一眼分清谁是谁
        try:
            from core.config import INSTANCE_NAME, is_multi_instance
            _tag = ""
            if is_multi_instance():
                _tag = " [%s]" % (INSTANCE_NAME or "多开")
            self.root.title(f"三国杀自走棋助手 v{VERSION}{_tag} · 端口 {self.server.port}")
        except Exception:
            pass

        # v4.160★ 重启请求轮询(/restart、/reconfigure 改端口都靠它落地)。
        #   以前 bridge.py 写了标记文件却【没有任何地方读它】—— 于是"改端口"
        #   点完什么都不会发生。每 2 秒看一次本实例自己的标记(不是助手根那个)。
        self.root.after(3000, self._poll_restart)

        # v4.33★ 【⚡一键注入】的脚本服务状态由 serve_userscript 模块自己管(SU.ensure / SU.shutdown),
        #   这里不再存 httpd/端口副本 —— 两处各存一份正是"第二次点按钮打开错地址"那类 bug 的温床。



        # Core components
        self.ops = BridgeOps(self.log)
        # v4.108★ OcrOps 可能是 None(缺可选依赖), None(...) 会 TypeError —— 启动时直接炸
        self.ocr_scanner = OcrOps(self.log) if OcrOps else None
        # v4.23★ 清理: 此处原有 `# self.hero_picker = HeroPicker(...)` / `# self.img_hasher = ...`
        #   两行注释。它们**从未生效** → self.hero_picker 属性根本不存在 →
        #   下游所有 hasattr(self, "hero_picker") 恒为 False(界面曾因此白名单恒为空,
        #   使 protocol_buy_targets 直接返回全 0, 见 a_protocol_buy 里的 v4.21 注释)。
        #   真正的白/黑名单是「数据管理」页签的 whitelist_panel / blacklist_panel(DualListPanel)。
        MACRO.get_app = lambda: self # recorder 模块里需要 app 的引用来打日志
        MACRO.on_event = self._macro_on_record_event # 录制时事件回调

        # UI variables
        self.status_bar_var = tk.StringVar(value="准备就绪")
        self._last_fb = "" # Feedback message
        self._hud_log = "" # Last message pushed to HUD
        self._hud_bar_timer = None
        self._root_after_ids = {} # Store root.after IDs for cancellation
        # v4.23★ 清理: 原有 `self._autofill_timer = None` —— 除这一行赋值外全项目无任何引用,
        #   它当年服务于已被删除的 _autofill_hero_picker, 属死属性。

        # Variables for ID configuration lists
        self.all_generals_data = self.ops.get_all_generals_for_ui() # Get all generals (name and ID)
        # v4.153★ 招募 / 遣散 tab 的『快速预设』界面状态(key = "recruit" / "dismiss")
        self._preset_ui = {}
        self.followup_search_var = tk.StringVar()
        self.permanent_keep_search_var = tk.StringVar()
        self.whitelist_search_var = tk.StringVar()
        self.blacklist_search_var = tk.StringVar()
        self.recruit_search_var = tk.StringVar()
        self.discard_target_search_var = tk.StringVar()

        self.followup_listbox = None
        self.permanent_keep_listbox = None
        self.whitelist_listbox = None
        self.blacklist_listbox = None
        self.recruit_generals_listbox = None
        self.discard_target_listbox = None

        # Build UI
        # v4.161★ 多开时最上面先来一条实例色条(实例名 + 端口 + 热键开关)。
        #   5 个窗口并排时全都长一个样, 只能靠标题栏那行小字分辨 —— 余光就能分清
        #   才是真的分清。单实例模式不放(老界面一个像素都不变)。
        self._build_instance_bar()
        self._build_menu()
        self._build_toolbar()
        self._build_tabs()
        self._build_status_bar()

        # Hotkeys
        # v4.161★ 多开: 默认只有"主实例"接管全局热键(其余实例启动时带 --no-hotkeys)。
        #   不改的话: 按一次 F6/Ctrl+Alt+B, 每个带界面的实例都会执行一遍 ——
        #   各自操作自己的号, 看着像"助手疯了"。
        self.hotkeys_on = os.environ.get("SGS_NO_HOTKEYS", "").strip() not in ("1", "true", "yes")
        self.hotkeys_manager = None
        self._hotkeys()

        # 把本窗口句柄登记进实例注册表(供多开面板"切到这个窗口")
        self.root.after(800, self._register_hwnd)
        # 若是"改端口后自动重启", 起来后把浏览器指到新端口的装脚本页(端口同步)
        self.root.after(1000, self._maybe_open_install_after_restart)

        # Initial checks
        self._log_heartbeat()     # v4.101★ 日志刷新心跳(后台线程写的日志靠它刷到界面上)
        self._check_version_loop()
        self._check_bridge_connection_loop()
        print("App.__init__() completed")

    def log(self, message, level="INFO"):
        ts = datetime.now().strftime("%H:%M:%S")
        log_entry = f"[{ts}] [{level.upper()}] {message}"
        # v4.101★ 后台线程也会调 log(剧本线程/统计线程/每个 _run_bg 回调都会) —— 这里的
        #   写入必须用锁: 不然两条线程同时 append 时会把 _log_seq 加重(增量渲染靠这个序号
        #   判断"哪些是新的", 序号重复 = 界面上【漏掉一行日志】)。
        with self._log_lock:
            self._log_queue.append(log_entry)
            if len(self._log_queue) > self._log_max_lines * 2:  # Keep some buffer
                self._log_queue = self._log_queue[-self._log_max_lines:]
            # 显示缓冲: 带单调序号(增量渲染靠它判断"哪些是新增的"), 并按上限裁掉最老的
            try:
                self._log_seq += 1
                self._log_view.append((self._log_seq, log_entry))
                over = len(self._log_view) - int(self._log_max_lines or 500)
                if over > 0:
                    del self._log_view[:over]
            except Exception as e:
                sys.stderr.write(f"[日志] 显示缓冲写入失败: {e}\n")
        # v4.51★ 不再"每条都 after(0) 重画一次": 后端刷屏时那是 UI 卡顿的主因。
        #   改成标脏 + 合并成最多每 400ms 一次, 并且只在『日志』页可见时才真的重画。
        self._log_dirty = True
        # v4.101★ 【root.after 只能在主线程调】。以前没做这个判断 —— 后台线程一 log 就在
        #   非主线程上调 root.after: Tk 不是线程安全的, 轻则这个定时器石沉大海(于是
        #   "日志不刷新", 用户看到的就是"点了按钮一点反应都没有"), 重则整个界面抽风。
        #   后台线程只标脏, 由主线程那条 _log_heartbeat 链(500ms)负责刷出来。
        if getattr(self, "_main_tid", None) != threading.get_ident():
            return
        if not self._log_display_scheduled:
            self._log_display_scheduled = True
            try:
                self.root.after(400, self._flush_log_display)
            except Exception as e:
                self._log_display_scheduled = False
                sys.stderr.write(f"[日志] 排刷新失败: {e}\n")

    # v4.22★ 日志轮转参数: 原实现是纯 append 且【永不轮转】, data/log.txt 只涨不跌
    #   (实测已积累 1.4MB, 长时间挂机跑自动招募会持续变大, 最终拖慢每次 flush 并占满磁盘)。
    #   现在单文件超过 LOG_MAX_BYTES 就滚动成 log.txt.1(覆盖更老的备份), 再开新文件,
    #   即最多占 2 * LOG_MAX_BYTES。
    LOG_MAX_BYTES = 2 * 1024 * 1024
    LOG_BACKUP_SUFFIX = ".1"

    def _rotate_log_file(self):
        """把 log.txt 滚动为 log.txt.1(已存在则覆盖)。任何失败都不抛, 不能让写日志线程挂掉。"""
        try:
            if not os.path.exists(self._log_file_path):
                return
            backup = self._log_file_path + self.LOG_BACKUP_SUFFIX
            try:
                os.replace(self._log_file_path, backup)  # 原子操作, Windows 上可直接覆盖
            except OSError:
                # 极端情况(文件被别的进程占用): 先删掉旧备份再改名
                if os.path.exists(backup):
                    os.remove(backup)
                os.replace(self._log_file_path, backup)
        except Exception:
            _SIL.note("ui/app.py:537", "if not os.path.exists(self._log_file_path): re")

    def _log_writer(self):
        # 启动时若历史日志已超限, 先滚一次, 避免单文件从第一秒起就无限膨胀
        try:
            if os.path.exists(self._log_file_path) and os.path.getsize(self._log_file_path) > self.LOG_MAX_BYTES:
                self._rotate_log_file()
        except Exception:
            _SIL.note("ui/app.py:545", "if os.path.exists(self._log_file_path) and os.")

        f = open(self._log_file_path, "a", encoding="utf-8")
        try:
            written = f.tell()  # append 模式下 tell() 即当前文件大小
        except Exception:
            written = 0

        while True:
            with self._log_lock:   # 与 log() 的 append 同一把锁, 免得两条线程同时动这个 list
                entry = self._log_queue.pop(0) if self._log_queue else None
            if entry is not None:
                line = entry + "\n"
                try:
                    f.write(line)
                    f.flush()
                    written += len(line.encode("utf-8", errors="ignore"))
                except Exception:
                    _SIL.note("ui/app.py:563", "f.write(line)")
                if written >= self.LOG_MAX_BYTES:
                    try:
                        f.close()
                    except Exception:
                        _SIL.note("ui/app.py:568", "f.close()")
                    # v4.22★ 这里再包一层: 轮转内部虽然有兜底, 但万一有意外异常冒出,
                    #   整个写日志线程会【永久死掉】(实测复现), 之后 log.txt 不再更新。
                    #   宁可这次不轮转, 也要保住线程。
                    try:
                        self._rotate_log_file()
                    except Exception:
                        _SIL.note("ui/app.py:575", "self._rotate_log_file()")
                    try:
                        f = open(self._log_file_path, "a", encoding="utf-8")
                        written = 0
                    except Exception:
                        time.sleep(1)  # 打不开就歇 1 秒再试, 避免忙等刷爆 CPU
            else:
                time.sleep(0.1)

    def _log_heartbeat(self):
        """v4.101★ 日志刷新心跳(主线程, 每 500ms 一次)。

        为什么需要它: 后台线程(剧本/统计/各种 _run_bg 回调)也会调 log(), 而【root.after 只能
        在主线程调】 —— 后台线程那边只能标脏。这条链负责把脏的内容刷出来。
        开销: 绝大多数时候 _log_dirty 是 False, 进来立刻返回。
        """
        try:
            if getattr(self, "_log_dirty", False):
                self._update_log_display()
        except Exception as e:
            sys.stderr.write(f"[日志] 心跳刷新失败: {e}\n")
        try:
            self.root.after(500, self._log_heartbeat)
        except Exception as e:
            sys.stderr.write(f"[日志] 续日志心跳失败: {e}\n")

    def _flush_log_display(self):
        self._log_display_scheduled = False
        self._update_log_display()

    def _log_tab_visible(self):
        try:
            return self.notebook.tab(self.notebook.select(), "text") == "日志"
        except Exception:
            return True

    def _update_log_display(self, force=False):
        # Update log tab (if it exists)
        if not hasattr(self, "log_text"):
            return
        if not force:
            # v4.51★ 别的页可见时只记脏不重建 Text —— 这是"后端一刷屏 UI 就卡"的主因。
            #   切回『日志』页时 _on_tab_change 会带 force=True 强制刷一次, 不会漏看。
            if not self._log_dirty or not self._log_tab_visible():
                return
        self._log_dirty = False
        # v4.101★ 改成【增量追加】: 以前是 delete('1.0', END) + insert 整篇 500 行 ——
        #   后端刷屏时(剧本日志/多选购买进度/协议告警都是每条一行)就是每 400ms 一次
        #   【全屏重排 + 重绘 + 滚动到底】, 这是"开着日志页看剧本跑"掉帧的直接来源。
        #   现在只把新来的那几行追加到末尾; 只有首次 / 序号断档(缓冲被裁过) / force 才整表重画。
        try:
            with self._log_lock:
                items = list(self._log_view)   # 后台线程也在写这个 list, 先拷一份再画
            drawn_seq = int(getattr(self, "_log_drawn_seq", 0) or 0)
            gap = bool(items) and (items[0][0] > drawn_seq + 1)
            if force or not items or gap:
                self.log_text.config(state="normal")
                self.log_text.delete("1.0", tk.END)
                self.log_text.insert(tk.END, "\n".join(e[1] for e in items))
            else:
                new_lines = [e[1] for e in items if e[0] > drawn_seq]
                if new_lines:
                    self.log_text.config(state="normal")
                    self.log_text.insert(tk.END, "\n".join(new_lines) + "\n")
                    over = len(items) - int(self._log_max_lines or 500)
                    if over > 0:
                        self.log_text.delete("1.0", "%d.0" % (over + 1))
            if items:
                self._log_drawn_seq = items[-1][0]
            self.log_text.see(tk.END)
            self.log_text.config(state="disabled")
        except Exception as e:
            sys.stderr.write(f"[日志] 日志框刷新失败: {e}\n")

    def _push_hud_bar(self):
        s = self._last_fb
        if self._hud_log == s:
            return
        self._hud_log = s
        STATE.send_cmd("hud_bar", text=s, ms=2000)
        # v4.101★ 同一句也写进【助手底部状态栏】。以前结果只送到游戏内的 HUD 条 —— 而 HUD
        #   要游戏页连上、且人正盯着游戏画面才看得见; 否则点了 F6 / F7 / 上阵 就是"完全没反应"。
        #   注意本函数经常被【后台线程】调用(a_deploy / 卖闲 / 各种 _run_bg 回调), 而 Tk 只能
        #   主线程碰 —— 非主线程一律 after(0) 回主线程再做(又一个"后台线程直接动 Tk"的点)。
        try:
            if getattr(self, "_main_tid", None) == threading.get_ident():
                self.status_bar_var.set(str(s))
            else:
                self.root.after(0, lambda t=str(s): self._status(t))
        except Exception as e:
            self.log(f"[HUD] 状态栏回显失败: {e}", level="WARN")
        # Clear after 2 seconds(同样只能在主线程排定时器)
        try:
            if getattr(self, "_main_tid", None) == threading.get_ident():
                self._hud_clear_later()
            else:
                self.root.after(0, self._hud_clear_later)
        except Exception as e:
            self.log(f"[HUD] 排清除定时器失败: {e}", level="WARN")

    def _hud_clear_later(self):
        """2 秒后把游戏内那条 HUD 抹掉(必须跑在主线程)。"""
        try:
            if self._hud_bar_timer:
                self.root.after_cancel(self._hud_bar_timer)
            self._hud_bar_timer = self.root.after(
                2000,
                lambda: (STATE.send_cmd("hud_bar", text="", ms=0), setattr(self, "_hud_bar_timer", None)))
        except Exception as e:
            self.log(f"[HUD] 清除定时器失败: {e}", level="WARN")


    def _on_closing(self):
        self.log("程序退出...")
        # Stop all threads and close server
        self._live_pump_stop = True      # 协议泵(daemon, 只是让它别再空转)
        self.server.stop()
        # v4.33★ 顺手关掉【⚡一键注入】起的脚本服务: 线程虽是 daemon、进程退出也会带走它, 但显式关
        #   能立刻释放端口 —— 否则再开一次助手会 probe 到一个"已经死掉"的旧服务, 或被判为端口被占。
        try:
            import serve_userscript as SU
            SU.shutdown()
        except Exception as _e:
            self.log(f"[注入] 关闭脚本服务失败: {_e}", level="ERROR")
        if self.hotkeys_manager:
            self.hotkeys_manager.stop()
        MACRO.ensure_pixel_listeners(False)
        # v4.24★ 退出前把协议落盘缓冲刷出去(net_samples/net_structures 现在是缓冲合并写的,
        #   不刷的话最后几秒的样本会留在内存里丢掉); 顺带把落盘失败次数报到日志 ——
        #   存不上必须看得见, 不能再像以前那样被 `except: pass` 吞掉。
        try:
            STATE.flush_net()
            _h = STATE.get_net_health()
            if _h.get("errors"):
                self.log(f"[落盘] 本次运行有 {_h['errors']} 次失败, 最近: {_h.get('last_error')}",
                         level="ERROR")
        except Exception as _e:
            self.log(f"[落盘] 退出前刷盘失败: {_e}", level="ERROR")
        self.root.destroy()
        sys.exit(0) # Ensure all threads are terminated

    def _build_menu(self):
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="文件", menu=file_menu)
        file_menu.add_command(label="打开数据目录", command=self._open_data_dir)
        file_menu.add_command(label="重新加载配置", command=self._reload_config)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self._on_closing)

        # v4.23★ 新增「工具」菜单: 协议 pid 学习表审计
        #   背景: 油猴的 pid 学习表(__sgsPidMap)优先级**高于**游戏自带的权威表(ProtoBufId),
        #   一旦被跨协议错配写坏就会一直遮蔽正确值 → 表现为某协议"怎么重新学习都修不好",
        #   而且完全没有报错(实机: F6 买卡全部失败, 日志还误报"卡已sold/金币不足")。
        #   审计用与油猴 findPid() 完全相同的逻辑逐条对账, 找出被权威表证伪的记录。
        tools_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="工具", menu=tools_menu)
        tools_menu.add_command(label="协议 pid 审计(只检查, 不改动)", command=self.audit_proto_pids)
        tools_menu.add_command(label="协议 pid 审计 + 自动修复",
                               command=lambda: self.audit_proto_pids(auto_fix=True))
        tools_menu.add_separator()
        # v4.24★ 把 pid_forget 接到菜单上。
        #   此前它只有 Python API(forget_proto_pid), GUI 用户根本够不着 ——
        #   而审计日志里却写着"手动 forget_proto_pid() 清它", 等于给了一条死路。
        tools_menu.add_command(label="清空 pid 学习表并重来…", command=self._reset_proto_pids)

        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="帮助", menu=help_menu)
        # v4.22★ 方案A: 移除 GitHub 占位符外链与「检查更新」
        #   (本项目未建立仓库, 原实现必然请求失败并弹错误框)
        help_menu.add_command(label="关于 / 本地版本信息", command=self._show_about)
        help_menu.add_command(label="打开使用手册（本地）", command=self._open_manual)
        help_menu.add_separator()
        help_menu.add_command(label="打开数据目录", command=self._open_data_dir)

    def audit_proto_pids(self, auto_fix=False):
        """v4.23★ 审计 pid 学习表(放后台线程, 避免网络等待卡住 UI)。

        结果直接打到日志页: 每条可疑记录都会给出 学习值 vs 权威值。
        auto_fix=True 时只清除**被权威表证伪(WRONG)**的记录 —— 其余类型只报告,
        因为它们可能本来就是对的, 误删会把本来能用的协议搞坏。
        """
        def _task():
            try:
                if not self.ops.connected():
                    self.log("[协议审计] 桥接未连接 —— 请先在游戏页确认脚本已注入(需 v4.23)")
                    return
                self.log("[协议审计] 正在比对学习表与游戏权威表...")
                self.ops.audit_proto_pids(auto_fix=auto_fix)
            except Exception as e:
                self.log(f"[协议审计] 失败: {e}", level="ERROR")
                _log_err()
        threading.Thread(target=_task, daemon=True).start()

    def _reset_proto_pids(self):
        """v4.24★ 清空 pid 学习表(浏览器 localStorage + 本地 learned_pids.json)。

        为什么需要"整表清空"而不是逐条挑:
          实机审计显示学习表里的错配是**成组**出现的 —— 一组 4~6 个互不相干的协议共用同一个
          pid(v4.24 修掉的写入漏洞造成的)。组内哪一条才是该 pid 的真正主人, 无法用权威表判定
          (本环境下权威表对这些协议一律查不到), 所以逐条挑必然误删。
        整表清空是安全的:
          ① 清掉后 findPid 回退游戏自带权威表;
          ② 油猴的 pid 学习是**被动**的 —— 正常玩一局(刷新/买卡/上阵…)就会重新学到;
          ③ v4.24 已修掉"一条发送被反复复用"的漏洞, 重新学到的值不会再成批串台。
        """
        if not messagebox.askyesno(
                "清空 pid 学习表",
                "将清除全部已学习的协议 pid(浏览器 localStorage + 本地 learned_pids.json)。\n\n"
                "· 已学到的协议会暂时失效, 直到重新学到为止;\n"
                "· 之后正常玩一局(点刷新 / 买卡 / 上阵)即会自动重新学习;\n"
                "· 需要游戏页脚本为 v4.24 或更高(已修掉「一条发送被反复复用」的写入漏洞,\n"
                "  重新学到的值不会再成批串台)。\n\n"
                "确定清空吗?") is False:
            self.log("[协议修复] 已取消")
            return

        def _task():
            try:
                if not self.ops.connected():
                    self.log("[协议修复] 桥接未连接 —— 请先在游戏页确认脚本已注入(需 v4.24)")
                    return
                self.ops.forget_proto_pid(all_names=True)
                self.log("[协议修复] 已清空学习表 → 请正常玩一局(刷新/买卡/上阵), pid 会自动重新学习")
            except Exception as e:
                self.log(f"[协议修复] 清空失败: {e}", level="ERROR")
                _log_err()
        threading.Thread(target=_task, daemon=True).start()

    def _open_data_dir(self):
        try:
            path = DATA_DIR
            if platform.system() == "Windows":
                os.startfile(path)
            elif platform.system() == "Darwin": # macOS
                subprocess.Popen(["open", path])
            else: # Linux
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            self.log(f"打开数据目录失败: {e}", level="ERROR")
            messagebox.showerror("错误", f"无法打开数据目录: {e}")

    def _reload_config(self):
        self.cfg = load_config()
        self.log("配置已重新加载.")
        messagebox.showinfo("提示", "配置已重新加载.")

    def _show_about(self):
        """v4.22★ 方案A: 「关于 / 本地版本信息」—— 只读本地状态, 不发任何网络请求。

        背景: 原实现显示 `作者: YourName` / `项目主页: https://github.com/your-repo`
        两个占位符, 且「检查更新」会去请求
        `api.github.com/repos/your-repo/releases/latest` —— 本项目并未建立 GitHub
        仓库, 该请求必然失败并弹「更新检查失败」错误框。现改为展示真实有用的本地信息。
        """
        lines = ["三国杀自走棋助手 (本地版)", ""]
        lines.append(f"助手版本 (Python):   v{VERSION}")

        # 游戏页脚本版本: 取自油猴每次 push 携带的 v 字段
        try:
            ver_info = self.ops.get_version() if getattr(self, "ops", None) else None
            sv = (ver_info or {}).get("version")
        except Exception:
            sv = None
        lines.append(f"游戏页脚本 (油猴):   {'v' + str(sv) if sv else '未连接'}")

        # v4.24★ 显示【实际监听端口】(self.server.port), 而不是 config 里的期望值 ——
        #   两者曾经长期不一致(端口配置从未生效), 只报配置值会掩盖真正的故障。
        try:
            port = getattr(getattr(self, "server", None), "port", None) \
                or load_config().get("bridge", {}).get("port", "?")
        except Exception:
            port = "?"
        lines.append(f"桥接端口:            {port}")
        lines.append("")
        lines.append(f"数据目录: {DATA_DIR}")
        lines.append("")
        lines.append("数据文件状态:")
        for fn in ("hero_picker.json", "chess.json", "chess_meta.json",
                   "discovered_ids.json", "tavern_summary.json"):
            p = os.path.join(DATA_DIR, fn)
            if os.path.exists(p):
                lines.append(f"  [OK] {fn:24s} {os.path.getsize(p) / 1024:9.1f} KB")
            else:
                lines.append(f"  [--] {fn:24s} 缺失")
        lines.append("")
        lines.append("本程序为本地工具, 不联网检查更新。")
        messagebox.showinfo("关于 / 本地版本信息", "\n".join(lines))

    def _open_manual(self):
        """v4.22★ 用系统默认程序打开本地使用手册 (纯本地, 不联网)。"""
        # v4.160★ 以前用 dirname(DATA_DIR) 当助手根 —— 多开时 DATA_DIR 挪到实例目录下,
        #   dirname 就变成 instances/, 手册永远找不到。代码/文档这类【只读资源】
        #   一律用 BASE_DIR(不随实例变)。
        path = os.path.join(BASE_DIR, "使用手册与功能示例.md")
        if not os.path.exists(path):
            messagebox.showwarning("提示", f"未找到使用手册:\n{path}")
            return
        try:
            webbrowser.open_new("file:///" + path.replace("\\", "/"))
        except Exception as e:
            self.log(f"打开使用手册失败: {e}", level="ERROR")
            messagebox.showerror("错误", f"无法打开使用手册: {e}")

    # ---------------- v4.33★ 一键注入页面脚本 ----------------
    def a_inject_userscript(self):
        """起本地脚本服务(serve_userscript 把 bridge/ 挂在 127.0.0.1 的某个端口) + 打开浏览器 →
        Tampermonkey 弹出安装/更新页, 点『安装』即把 v{VERSION} 装/更新到游戏页。

        注意(实测): 必须由【浏览器打开导航】到 .user.js 才会触发 TM 的安装页 ——
        之前 serve_userscript 的响应头带了 Content-Disposition: attachment, 浏览器当成下载,
        TM 拦不到, 于是"一键注入"变成了下载一个文件。已修掉, 见 serve_userscript.py。
        """
        import webbrowser
        import serve_userscript as SU
        try:
            # v4.160★ 多开: 候选端口按实例序号平移, 免得复用别的实例的服务
            from core.config import slot_ports
            port, notes = SU.ensure(ports=slot_ports(SU.PORT))
            url = SU.url(port) if port else ""
        except Exception as e:
            # 不在这里猜一个写死的地址: 端口只有 serve_userscript 说了算(项目踩过"写死端口"的坑)
            notes, port, url = [f"脚本服务异常({e})"], None, ""
        if not port:
            self.log("[注入] " + " | ".join(notes))
            messagebox.showerror("一键注入", "本地脚本服务没起来:\n" + "\n".join(notes)
                                 + "\n\n可先手动运行: python serve_userscript.py")
            return
        opened = False
        try:
            opened = bool(webbrowser.open_new(url))
            notes.append("已打开主脚本" if opened else "浏览器没接受打开请求")
        except Exception as e:
            notes.append(f"打不开浏览器({e})")
        # v4.153★ PNA 绕过依赖配套传输层脚本: 一并打开安装页, 两个都要装
        try:
            opened_xhr = bool(webbrowser.open(SU.companion_url(port)))
            notes.append("已打开配套传输层脚本" if opened_xhr else "浏览器没接受打开配套请求")
        except Exception as e:
            notes.append(f"打不开配套({e})")
        self.log("[注入] " + " | ".join(notes))
        self.log(f"[注入] 主脚本地址: {url}")
        self.log(f"[注入] 配套传输层地址: {SU.companion_url(port)}")
        self.log(f"[注入] 浏览器里 Tampermonkey 会先后弹两个『安装/更新』页(主脚本 + 传输层配套), "
                 f"两个都点安装/更新; 装完回游戏页按 F5。本次脚本版本 v{VERSION}, "
                 f"装完底部『脚本状态』应显示 ✓ 最新。")
        if not opened:
            messagebox.showinfo("一键注入",
                                 f"请手动在浏览器打开下面两个地址(各点一次安装):\n{url}\n"
                                 f"{SU.companion_url(port)}\n\n"
                                 f"Tampermonkey 会弹安装/更新页, 点安装后回游戏页 F5。"
                                 f"\n\n(没装 Tampermonkey? 把地址末尾的 .user.js 删掉再打开, 有图解说明)")

    # ---------------- v4.161★ 多开: 实例色条 / 热键开关 / 窗口句柄 ----------------
    # 每个实例一个固定颜色(按实例槽位取, 不随机 —— 重启后还是同一个颜色)
    INSTANCE_COLORS = ["#e0564b", "#4b8fe0", "#4bb96b", "#d9a13b", "#9b6bd6",
                       "#3fb6c4", "#d2689b", "#7a8fa6"]

    def _instance_color(self):
        try:
            from core.config import INSTANCE_NAME, INSTANCE_SLOT
            if INSTANCE_SLOT:
                return self.INSTANCE_COLORS[INSTANCE_SLOT % len(self.INSTANCE_COLORS)]
            import zlib
            n = zlib.crc32((INSTANCE_NAME or "?").encode("utf-8"))
            return self.INSTANCE_COLORS[n % len(self.INSTANCE_COLORS)]
        except Exception:
            return self.INSTANCE_COLORS[0]

    def _build_instance_bar(self):
        """顶部实例条: 颜色 + 实例名 + 端口 + 热键开关。仅多开时显示。"""
        try:
            from core.config import INSTANCE_NAME, is_multi_instance
            if not is_multi_instance():
                return
        except Exception:
            return
        try:
            c = self._instance_color()
            bar = tk.Frame(self.root, bg=c, height=28)
            bar.pack(side=tk.TOP, fill=tk.X)
            bar.pack_propagate(False)
            tk.Label(bar, text="  %s  " % (INSTANCE_NAME or "实例"), bg=c, fg="white",
                     font=("Microsoft YaHei", 12, "bold")).pack(side="left")
            tk.Label(bar, text="端口 %s" % getattr(self.server, "port", "?"),
                     bg=c, fg="#f0f0f0", font=("Microsoft YaHei", 9)).pack(side="left", padx=4)
            self._hk_var = tk.StringVar(value="")
            tk.Button(bar, textvariable=self._hk_var, command=self.a_toggle_hotkeys,
                      bg=c, fg="white", bd=0, relief="flat", padx=6,
                      font=("Microsoft YaHei", 9, "bold"), cursor="hand2"
                      ).pack(side="right", padx=6)
            self._refresh_hk_btn()
        except Exception as e:
            try:
                self.log(f"[多开] 实例条构建失败: {e}")
            except Exception:
                pass

    def _refresh_hk_btn(self):
        try:
            on = getattr(self, "hotkeys_on", True)
            self._hk_var.set("热键: 接管中" if on else "热键: 已关闭")
        except Exception:
            pass

    def a_toggle_hotkeys(self):
        """切换本实例是否接管全局热键。

        多开时只让一个实例接管: 否则一次按键, 所有带界面的实例一起执行,
        各自操作自己的号。改完立即生效(不用重启), 状态写在实例条按钮上。
        """
        self.hotkeys_on = not getattr(self, "hotkeys_on", True)
        try:
            HM = getattr(self, "hotkeys_manager", None)
            if self.hotkeys_on:
                if HM is not None:
                    HM.start()
                else:
                    self._hotkeys()
            else:
                if HM is not None:
                    HM.stop()
                try:
                    import keyboard as _kb
                    _kb.unhook_all()      # 兜底那套也一起卸掉, 不然关了还会触发
                except Exception:
                    pass
            os.environ["SGS_NO_HOTKEYS"] = "" if self.hotkeys_on else "1"
        except Exception as e:
            try:
                self.log(f"[热键] 切换失败: {e}")
            except Exception:
                pass
        self._refresh_hk_btn()
        try:
            self.log("[热键] 本实例%s全局热键(F5/F6/F8/Ctrl+Alt+B…)"
                     % ("接管" if self.hotkeys_on else "不再接管"))
        except Exception:
            pass

    def _register_hwnd(self):
        """把本窗口句柄写进实例注册表 —— 面板里"切到这个窗口"要用。"""
        try:
            from core import instances as _I
            hwnd = 0
            try:
                hwnd = int(self.root.frame(), 16)     # Tk 顶层窗口的 Win32 句柄
            except Exception:
                hwnd = 0
            if hwnd:
                _I.set_hwnd(getattr(self.server, "port", 0), hwnd)
        except Exception:
            pass

    # ---------------- v4.160★ 在界面里改桥接端口 ----------------
    def a_change_port(self):
        """改本实例的端口 → 写进本实例 config.json → 自动重启本实例。

        为什么必须重启: 端口是 BridgeServer 起监听那一刻 bind 死的, 改配置不会让
        已经 bind 的 socket 换端口。重启走 os.execv(同一套 argv), 数据目录不变。
        """
        import socket as _sk
        import tkinter.simpledialog as _sd
        from core.config import set_bridge_port

        cur = int(getattr(self.server, "port", 0)
                  or self.cfg.get("bridge", {}).get("port") or 8765)
        v = _sd.askinteger(
            "桥接端口",
            "当前端口: %d\n\n"
            "改完会发生:\n"
            "  ① 助手自动重启, 用新端口监听(数据/名单都不变)\n"
            "  ② 浏览器那边也要改成同一个端口:\n"
            "       · 全能扩展 → 扩展 popup / 选项页里填\n"
            "       · 油猴脚本 → 打开 http://127.0.0.1:<新端口>/install.html\n\n"
            "新端口(1024-65535):" % cur,
            initialvalue=cur, minvalue=1024, maxvalue=65535, parent=self.root)
        if not v or int(v) == cur:
            return
        # 先探一下: 端口已经有人占的话, 助手起来也是个空壳(绑不上)
        s = _sk.socket()
        s.settimeout(0.5)
        busy = (s.connect_ex(("127.0.0.1", int(v))) == 0)
        try:
            s.close()
        except Exception:
            pass
        if busy:
            messagebox.showwarning("端口被占用",
                                   "端口 %d 上已经有服务在跑了(多半是另一个助手实例)。\n"
                                   "换一个, 或者先关掉占用它的那个。" % int(v))
            return
        try:
            set_bridge_port(int(v))
        except Exception as e:
            messagebox.showerror("改端口失败", str(e))
            return
        self.log(f"[端口] 已改为 {int(v)}, 正在重启本实例…")
        try:
            import core.bridge as _BR
            _BR.request_restart("change_port", open_install=True)
        except Exception as e:
            self.log(f"[端口] 重启请求失败(请手动重启助手): {e}", level="ERROR")

    def _maybe_open_install_after_restart(self):
        """v4.161★ 若本次是因为"改端口"而重启的, 自动把浏览器指到新端口的装脚本页。

        改端口只改了助手这一端, 浏览器脚本/皮肤还记着旧端口 → 不处理就是"连不上"。
        install.html 会把 localStorage['sgs_bridge_port'] 改写成新端口, 皮肤(与主脚本
        共用同一把钥匙)也就跟着生效了。标记写在实例目录, 只影响这一个实例。
        """
        try:
            from core.config import runtime_path
            _m = runtime_path(".open_install_after_restart")
            if not os.path.exists(_m):
                return
            try:
                os.remove(_m)
            except Exception:
                pass
            import webbrowser as _wb
            _p = getattr(getattr(self, "server", None), "port", None) or \
                self.cfg.get("bridge", {}).get("port", 8765)
            _wb.open("http://127.0.0.1:%d/install.html" % int(_p))
            self.log("[端口] 已自动打开新端口的装脚本页, 浏览器脚本/皮肤端口同步完成")
        except Exception:
            pass

    def _build_toolbar(self):
        # v4.162★ 工具栏改【固定两行】(原来全挤一行, 缩到 1440 宽时 pack(side=RIGHT) 的
        #   『端口 / ⚡一键注入』会被挤成 1px 直接看不见 —— 用户报"必须最大化才舒服")。
        #   做法: 外层 outer 里叠两个行 Frame, 第一行=操作按钮, 第二行=速度/状态/端口/注入。
        #   注意这里【不改任何一行创建按钮的代码】—— 靠下面的 `toolbar = 第二行` 重新绑定,
        #   让后半段的控件自然以第二行为父。跨 Frame 用 pack(in_=..) 重排是不行的(Tk 要求 -in
        #   必须是控件的父控件), 所以必须是"创建时就落在正确的行"。
        outer = tk.Frame(self.root, bd=1, relief=tk.RAISED, bg=BTN)
        outer.pack(side=tk.TOP, fill=tk.X)
        toolbar = tk.Frame(outer, bg=BTN)      # 第一行: 操作按钮
        toolbar.pack(side=tk.TOP, fill=tk.X)

        tk.Button(toolbar, text="一键买全(F6)", command=self.a_protocol_buy, bg=BTN, fg=FG,
                  font=(FH, 9, "bold"), padx=6, cursor="hand2").pack(side=tk.LEFT, padx=2, pady=2)
        tk.Button(toolbar, text="一键上阵(Ctrl+Alt+D)", command=self.a_deploy, bg=BTN, fg=FG,
                  font=(FH, 9, "bold"), padx=6, cursor="hand2").pack(side=tk.LEFT, padx=2, pady=2)
        tk.Button(toolbar, text="一键遣散(F7)", command=self.a_protocol_sell, bg=BTN, fg=FG,
                  font=(FH, 9, "bold"), padx=6, cursor="hand2").pack(side=tk.LEFT, padx=2, pady=2)
        tk.Button(toolbar, text="全遣散手牌", command=self._trigger_discard_all_hand, bg=RED, fg=FG,
                  font=(FH, 9, "bold"), padx=6, cursor="hand2").pack(side=tk.LEFT, padx=2, pady=2)
        tk.Button(toolbar, text="刷新商店(Ctrl+R)", command=self.a_refresh, bg=BTN, fg=FG,
                  font=(FH, 9, "bold"), padx=6, cursor="hand2").pack(side=tk.LEFT, padx=2, pady=2)
        tk.Button(toolbar, text="跳过战斗(Space)", command=self.a_skip, bg=BTN, fg=FG,
                  font=(FH, 9, "bold"), padx=6, cursor="hand2").pack(side=tk.LEFT, padx=2, pady=2)
        # v4.16x★ 『结束招募』勾选: 勾上时, "跳过战斗(Space)" 那一下走【全模式】
        #   (页内 skipBattle() 按阶段分派: 战斗→跳过、招募→提前收工), 与 EasySkipBattle.js
        #   的 endRecruit 默认开一致。取消勾选则只跳过战斗、不动招募阶段。
        self.end_recruit_var = tk.BooleanVar(value=True)
        tk.Checkbutton(toolbar, text="结束招募", variable=self.end_recruit_var, bg=BTN, fg=FG,
                       selectcolor="#33333f", activebackground=BTN, activeforeground=FG,
                       font=(FH, 9), cursor="hand2").pack(side=tk.LEFT, padx=4, pady=2)
        # v4.143★ 手牌哨兵【点一下刷一次】: 常驻轮询默认关了(常驻 250ms 空转对多数人
        #   是白开销), 手牌区没跟上时就点这一下 / 游戏里按 Alt+H。
        tk.Button(toolbar, text="手牌哨兵(Alt+H)", command=self.a_hand_sentinel, bg=BTN, fg=FG,
                  font=(FH, 9, "bold"), padx=6, cursor="hand2").pack(side=tk.LEFT, padx=2, pady=2)
        # v4.144★ 【页面错误】查看窗: 脚本一直在 window.__sgsTopErrs 里收集同步 throw 与未处理
        #   的 Promise, 但一直没有查看入口; F12 控制台又常被网络请求日志刷屏(那类日志不是本脚本
        #   打的 —— 全项目 grep "已完成加载" 0 命中), 真错误一闪而过。这里直接从页面读回数组,
        #   按"同一条出现几次"聚合, 反复炸的那句排最上面。
        tk.Button(toolbar, text="页面错误", command=self.a_page_errors, bg=BTN, fg=FG,
                  font=(FH, 9, "bold"), padx=6, cursor="hand2").pack(side=tk.LEFT, padx=2, pady=2)
        tk.Button(toolbar, text="逻辑探测", command=self.a_logic_scan, bg=BTN, fg=FG,
                  font=(FH, 9, "bold"), padx=6, cursor="hand2").pack(side=tk.LEFT, padx=2, pady=2)

        # v4.11★ 速度/帧率调整移到主工具栏
        # v4.162★ 第二行从这里开始: 把 toolbar 重绑定到第二个行 Frame ——
        #   之后的 速度组 / 脚本状态 / 端口 / ⚡一键注入 全部落在第二行, 原代码一行不用改。
        toolbar = tk.Frame(outer, bg=BTN)
        toolbar.pack(side=tk.TOP, fill=tk.X)
        speed_frm = tk.Frame(toolbar, bg=BTN)
        speed_frm.pack(side=tk.LEFT, padx=(10, 2))
        tk.Label(speed_frm, text="游戏加速:", bg=BTN, fg=FG, font=(FH, 9)).pack(side=tk.LEFT)
        self.toolbar_speed_var = tk.StringVar(value=str(self.cfg.get("fps", {}).get("speed", "1.0")))
        # v4.56★ 原来只有 ["1.0","2.0","3.0","5.0"] 四档 —— 用户报"你说上限 10, 但我只能选到 5"。
        #   现在开成 1~20 全程可选, 与脚本端 __applyFps 的上限保持一致(那里也从 10 提到 20)。
        #   注意这里是【游戏整体加速】(改 Laya.timer.scale), 和剧本的「速度(倍)」不是一回事:
        #     剧本倍速只压缩剧本里 wait 的等待, 不动游戏动画; 这个会真的把游戏的时间轴拉快。
        # v4.166★ 上限 20 → 100(用户要求更快): 脚本端 __applyFps 的夹取已同步放宽到 100,
        #   这里不开上去就会出现"选了高倍速但被页面夹回 20"的老毛病。
        #   1~20 保持 1 一档(常用区), 再往上 5 一档 —— 1~100 逐档会撑出 100 项下拉, 选不动。
        speed_options = (["%.1f" % i for i in range(1, 21)]
                         + ["%.1f" % i for i in range(25, 101, 5)])
        ttk.OptionMenu(speed_frm, self.toolbar_speed_var, self.toolbar_speed_var.get(), *speed_options,
                       command=self._on_toolbar_speed_change).pack(side=tk.LEFT, padx=2)

        # 加速 Flag 快捷勾选
        self.toolbar_tween_var = tk.BooleanVar(value=self.cfg.get("flags", {}).get("tweenScale", False))
        tk.Checkbutton(speed_frm, text="动画加速", variable=self.toolbar_tween_var,
                       bg=BTN, fg=FG, selectcolor="#33333f", activebackground=BTN,
                       font=(FH, 8), bd=0, relief="flat",
                       command=self._on_toolbar_flags_change).pack(side=tk.LEFT, padx=4)
        self.toolbar_batch_var = tk.BooleanVar(value=self.cfg.get("flags", {}).get("batchProtocol", False))
        tk.Checkbutton(speed_frm, text="批量协议", variable=self.toolbar_batch_var,
                       bg=BTN, fg=FG, selectcolor="#33333f", activebackground=BTN,
                       font=(FH, 8), bd=0, relief="flat",
                       command=self._on_toolbar_flags_change).pack(side=tk.LEFT, padx=4)

        # v4.185★ 全局操作倍率(不是游戏加速, 也不是剧本倍率): 压缩脚本自己的等待,
        #   手动/快捷键/自动招募/剧本 全部生效(与剧本倍率相乘)。详见 bridge _sleepJs。
        gspeed_frm = tk.Frame(toolbar, bg=BTN)
        gspeed_frm.pack(side=tk.LEFT, padx=(8, 2))
        tk.Label(gspeed_frm, text="操作倍率:", bg=BTN, fg=FG, font=(FH, 9)).pack(side=tk.LEFT)
        self.toolbar_gspeed_var = tk.StringVar(value=str(self.cfg.get("global_speed", "1.0")))
        gspeed_options = ["0.5", "1.0", "2.0", "3.0", "5.0", "10.0", "20.0"]
        ttk.OptionMenu(gspeed_frm, self.toolbar_gspeed_var, self.toolbar_gspeed_var.get(), *gspeed_options,
                       command=self._on_toolbar_gspeed_change).pack(side=tk.LEFT, padx=2)

        self.ver_lab = tk.Label(toolbar, text="脚本状态: 未连接", bg=BTN, fg=DIM, font=(FH, 9))
        self.ver_lab.pack(side=tk.RIGHT, padx=10)

        # v4.33★ 【⚡一键注入】按钮 —— 之前只有『点【⚡一键注入】更新』这句提示文字,
        #   但界面上根本没有这个按钮(用户反馈"没有这个一键注入啊")。
        #   现在真的做出来了: 起本地脚本服务(首选 8766, 被占则自动换端口) + 打开浏览器 →
        #   Tampermonkey 弹安装/更新页, 点一下就把 bridge/sgs_bridge.user.js 装/更新到游戏页。
        #   端口由 serve_userscript.ensure() 决定并记住, 这里不写死。
        # v4.160★ 端口入口: 点一下改本实例端口(改完自动重启本实例)。
        #   按钮上直接写当前端口 —— 多开时一眼能对上"这个窗口是哪个端口"。
        _p = getattr(getattr(self, "server", None), "port", None) or \
            self.cfg.get("bridge", {}).get("port", "?")
        tk.Button(toolbar, text="端口 %s" % _p, command=self.a_change_port, bg=BTN, fg=GOLD,
                  font=(FH, 9, "bold"), padx=6, cursor="hand2").pack(side=tk.RIGHT, padx=4, pady=2)
        tk.Button(toolbar, text="⚡一键注入", command=self.a_inject_userscript, bg=ACT, fg=GOLD,
                  font=(FH, 9, "bold"), padx=8, cursor="hand2").pack(side=tk.RIGHT, padx=4, pady=2)

    def _build_tabs(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(expand=True, fill="both", padx=5, pady=5)

        # Tab 1: 招募 (含自动招募选武将)
        self.tab_recruit = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(self.tab_recruit, text="招募")
        self._build_recruit_tab()

        # Tab 2: 多选购买武将
        self.tab_multi_buy = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(self.tab_multi_buy, text="多选购买")
        self._build_multi_buy_tab()

        # Tab 3: 遣散管理 (v4.11★ 新增)
        self.tab_discard = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(self.tab_discard, text="遣散管理")
        self._build_discard_management_tab()

        # Tab 4: 数据管理
        self.tab_data_management = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(self.tab_data_management, text="数据管理")
        self._build_data_management_tab()

        # Tab 5: 宏录制
        self.tab_macro = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(self.tab_macro, text="宏录制")
        self._build_macro_tab()

        # v4.71★ 删掉『对局复盘』页。用户原话:
        #   "然后复盘删了吧, 你只记录了, 我选的主公, 里面的操作没有记录, 我感觉也浪费
        #    输出传输、和占内存"。
        #   复盘页靠的是 data/records.json(每局只存 名次/主公/时间) + data/net_samples.json
        #   (协议流水)。用户真正想看的是"我这一局做了哪些操作", 而记录里【没有操作】——
        #   只有名次那几列, 于是这一页看完等于没看。留着它还容易让人以为"助手在偷偷录东西"。
        #   一并去掉的: 页构建函数 _build_replay_tab / notebook 注册 / 切页刷新 / 两个自检
        #   (dev/_verify_replay.py、dev/_smoke_replay_tab.py)、dev/_verify_offline.py 的 Part J。
        #   注意: data/records.json 仍在写(每局几百字节, 由 core/protocol.py 收官时落盘),
        #     这是"战绩/统计"用的原始数据, 与复盘页是两回事 —— 要一起停的话说一声。

        # Tab 7: 剧本 (v4.27★ 有先后顺序的步骤流: 上阵/遣散/牌库找卡/用锦囊 + 循环/条件 + 快捷键)
        self.tab_scenario = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(self.tab_scenario, text="剧本")
        self._build_scenario_tab()

        # Tab 7b: 快速找牌 (v4.184★ 纯连发刷新找牌 + 自动买)
        self.tab_quickfind = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(self.tab_quickfind, text="快速找牌")
        self._build_quickfind_tab()

        # Tab 8: 赛季数据 (v4.33★ 赛季换表: 一键下载解析 → 查看 → 推送/替换 → 导入导出)
        self.tab_season = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(self.tab_season, text="赛季数据")
        self._build_season_tab()

        # Tab 8b: 技能库 (v4.43★ 查 ChessSkillConf.SkillID / 主公ID / 主公技能ID 并能直接用出去)
        #   为什么要单开一页: 『查看数据』只认棋子/锦囊两张表, 而『随征卡牌ID』用的是【技能ID】
        #   (942311 = 黄巾兵的「随征」), 棋子表里永远没有它 —— 以前用户拿着 ID 无处可查。
        self.tab_skill = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(self.tab_skill, text="技能库")
        self._build_skill_tab()

        # Tab 8c: 主公技能 (v4.46★ 在对局中【主动使用】主公技能, 推送到当前游戏页)
        #   为什么要单开一页: 主公技能的选择发生在【对局内】(选将时选中一个 + 主公自带一个,
        #   1053「面涅」这类还能在对局里再挑一个), 助手离线表看不到"本局拿到了哪几个" ——
        #   必须问游戏活体(manager.SelfInfo.generalSkillList), 而『技能库』只管"查到 ID"。
        self.tab_lord = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(self.tab_lord, text="主公技能")
        self._build_lord_skill_tab()

        # Tab 8d: 主公 (v4.152★ 列 40 个可选主公, 点哪个直接发 ReqChessChooseGeneral(ID))
        #   与『主公技能』的区别: 那一页管【对局内】用技能(问游戏活体拿本局技能),
        #   这一页管【开局前】选主公(候选不从服务器下发, 直接列配置表, 见 ui/general_tab.py 顶部说明)。
        self.tab_general = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(self.tab_general, text="主公")
        self._build_general_tab()

        # Tab 8d: 快捷键 (v4.54★ 把键盘快捷键搬到按钮上 + 直接往游戏页控制台发指令)
        self.tab_hotkey = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(self.tab_hotkey, text="快捷键")
        self._build_hotkey_tab()

        # Tab 8d2: 节奏 (v4.85★ 脚本里那些【写死的延迟】逐项可改 —— 见 TIMING_ROWS 的说明。
        #   单开一页而不是塞进「快捷键」页: 那页已经是按钮面板了, 再塞十几个数字框会挤成一团;
        #   而且这些延迟作用于【剧本 + 快捷键 + 自动招募】全部路径, 挂在任一页下面都会让人
        #   以为"只影响这一页的功能"。)
        self.tab_timing = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(self.tab_timing, text="节奏")
        self._build_timing_tab()

        # Tab 8e: 资源替换 (v4.68★ 背景/立绘/头像等本地替换规则管理)
        self.tab_resource = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(self.tab_resource, text="资源替换")
        self._build_resource_tab()

        # Tab 8e-2 + 8e-3: 换肤 (v4.139★ 把原来平级的「即时换肤」和「动态皮肤」两页
        #   并成一页, 里面用子页签分开 —— 配一套皮要在这两处来回切, 拆成两个顶层 Tab
        #   反而要多点一次; 而且动态皮肤那页原来【没挂滚动容器】, 内容比窗口高就被截掉
        #   (用户原话"现在的动态皮肤都看不全"), 现在两页各自可滚。
        #   两套机制仍然分开讲, 不混着配:
        #     · 即时换肤 = 当场换屏幕上已显示的纹理(还能挂点击播语音);
        #     · 动态皮肤 = skel+atlas+png 三件套, 少一个文件就是错位碎块。
        self.tab_skin = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(self.tab_skin, text="换肤")
        self._build_skin_tab()
        # v4.139★ 兼容旧引用: 资源替换页有按钮会 notebook.select(app.tab_dynskin) 跳过来,
        #   合并后那两个对象没了 —— 指向合并后的页, 跳转照旧能用(只是落在"换肤"页)。
        self.tab_dynskin = self.tab_skin
        self.tab_inject = self.tab_skin

        # Tab 8e-4: 性能 (v4.182★ 渲染闸门: 取消渲染 / 暂停角标·手牌区·换肤·统计录制重绘)
        self.tab_perf = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(self.tab_perf, text="性能")
        self._build_perf_tab()

        # Tab 8f: 赛季更新 (v4.76★ 换季一键流: 检查官方→抓配置→推送现行数据 + 只读体检 + OL皮肤表)
        self.tab_season_upd = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(self.tab_season_upd, text="赛季更新")
        self._build_season_update_tab()

        # Tab 8g: 统计 (v4.88★ 一局里刷了几次/买了什么/商店出现过哪些卡 —— 数据来自游戏页
        #   桥接脚本新加的统计钩子, 口径与上游 TavernChessStats 一致)
        #   为什么要单开一页: 它要的是"回看一整局"的视角(摘要/每回合/排行), 跟『日志』那种
        #   一行行流水不是一回事; 而且钩子默认关, 得有个明确的开关页面, 不能藏在哪页角落里。
        self.tab_stats = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(self.tab_stats, text="统计")
        self._build_stats_tab()

        # Tab 8h: 机制 (v4.89★ 利用游戏机制的玩法做成按钮, 目前两条: 陆逊小抄 / 3选1)
        #   为什么要单开一页: 它不是"按一下干一件事"(那是『快捷键』页), 而是一个【常驻循环】
        #   —— 有运行中/已停止的状态、有轮次计数、要能随时停, 塞按钮面板里说不清楚。
        self.tab_mech = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(self.tab_mech, text="机制")
        self._build_mechanism_tab()

        # Tab 8i: 阵容 (v4.105★ 上游两张表做成能点的: 四势力 → 后期转阵思路; 卡名 → 几本最省)
        #   为什么要单开一页: 它跟任何一页都不沾边 —— 不是操作(『快捷键』), 不是复盘(『统计』),
        #   也不是换表(『赛季数据』); 而且它是【开局前/中都要反复查】的参考资料, 得有个固定位置。
        self.tab_lineup = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(self.tab_lineup, text="阵容")
        self._build_lineup_tab()

        # Tab 8k: 经济决策 (v4.110★ L0 —— 每回合回答"该存钱 / 刷新 / 升级 / 买哪张")
        #   为什么不塞进「阵容」页: 阵容是【开局前/中查的参考资料】(往哪个阵容转), 这一页是
        #   【对局中每回合的实时账本】(往前看两回合的期望收益), 而且要能自动刷 —— 用途和
        #   刷新频率都不一样, 放一起反而互相干扰。
        self.tab_econ = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(self.tab_econ, text="经济决策")
        self._build_econ_tab()

        # Tab 8k-2: 对局决策 (v4.47★ 写的, v4.71 只删了注册没删函数 → 变成死代码,
        #   v4.141 重新接回来)
        #   为什么恢复而不是删掉: 「经济决策」页用蒙特卡洛算"该存/刷/升/买", 比这一页强,
        #   但这一页有它答不了的两块 ——
        #     ① 本局速览: 金币/血量/阶段/刷新费/升级差/备战区/**商店 5 张明细**/池内剩余。
        #        经济页只显示"金币 x · N 级 · 第 N 回合"三个数;
        #     ② 排查向导: 全项目唯一消费 self._live_stat 的地方, 也是唯一能区分
        #        「游戏页没在推数据」和「推了但解析不出来」的线索。
        #   这两块正是跑自动对局时第一个要用到的 —— 助手读到什么局面、为什么读不到。
        self.tab_decision = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(self.tab_decision, text="对局决策")
        self._build_decision_tab()

        # Tab 8m: 终端对局 (v4.110★ B站 5.1: 看对手阵容 → 判是 8 种终端对局里的哪一种)
        #   单开一页, 理由同阵容页: 它是【对局中看对手】的实时判断(要随时重读),
        #   跟阵容页那种【开局前查资料】的用法不一样, 塞一起反而互相干扰。
        self.tab_endgame = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(self.tab_endgame, text="终端对局")
        self._build_endgame_tab()

        # Tab 8n: 锦囊试验台 (v4.145★ 不进游戏也能直接构造锦囊包发出去 —— 见 spell_lab_tab.py
        #   的说明: 取证"某个锦囊到底要什么参数"以前只能进游戏攒卡手动点, 太慢)
        self.tab_spell_lab = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(self.tab_spell_lab, text="锦囊试验台")
        self._build_spell_lab_tab()

        # Tab 8n-2: 对局复盘 (v4.145★ 协议流水 / 操作成败五态 + 导出报告 + 打开日志目录)
        self.tab_review = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(self.tab_review, text="对局复盘")
        self._build_review_tab()

        # Tab 9: 日志
        self.tab_log = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(self.tab_log, text="日志")
        self._build_log_tab()

        # Tab: 自动对局 (S11 自动对战 + 离线学习)
        self.tab_auto = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(self.tab_auto, text="自动对局")
        self._build_auto_battle_tab()

        # Tab: Debug 诊断 (v4.200★ Debug/决策审计 页)
        self.tab_debug = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(self.tab_debug, text="Debug诊断")
        self._build_debug_tab()

        # Tab: 奖励配置 (v4.200★ §4.1/§2.6 奖励方案有序列表编辑)
        self.tab_reward_cfg = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(self.tab_reward_cfg, text="奖励配置")
        self._build_reward_cfg_tab()

        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_change)

    def _build_auto_battle_tab(self):
        """S11 自动对局页(自动选主公 / 经济 / 阵容 / 针对 / 离线学习)。"""
        from .auto_battle_tab import build as _build_auto
        _build_auto(self, self.tab_auto)

    def _build_debug_tab(self):
        """v4.200★ Debug 诊断页(实时快照/决策审计/装备目标/开关)。"""
        from ui import debug_tab as DBGT
        try:
            self._debug_builder = DBGT.DebugTabBuilder(self)
            self._debug_builder._build()
        except Exception as _e:
            self.log("Debug 诊断 tab 构建失败: %s" % _e, "WARN")

    def _build_reward_cfg_tab(self):
        """v4.200★ 奖励配置页(§4.1/§2.6 奖励方案有序列表编辑 + 隐藏池 + 方案切换)。"""
        from ui import reward_config_tab as RCT
        try:
            self._reward_cfg_builder = RCT.RewardConfigTabBuilder(self)
            self._reward_cfg_builder._build()
        except Exception as _e:
            self.log("奖励配置 tab 构建失败: %s" % _e, "WARN")

    def _build_multi_buy_tab(self):
        """v4.13★ 多选购买 tab — DualListPanel 双列表"""
        from ui.dual_list import DualListPanel
        frame = self._make_scrollable_tab(self.tab_multi_buy)

        # 顶部状态/控制区
        top_frame = tk.Frame(frame, bg=BG)
        top_frame.pack(fill="x", pady=(0, 5))
        tk.Label(top_frame, text="多选购买:", bg=BG, fg=FG, font=(FH, 11, "bold")).pack(side="left")
        self.multi_buy_status_var = tk.StringVar(value="等待启动...")
        tk.Label(top_frame, textvariable=self.multi_buy_status_var, bg=BG, fg=DIM, font=(FH, 10)).pack(side="left", padx=5)
        tk.Button(top_frame, text="▶ 开始监测购买", command=self._start_multi_buy_ui, bg=BTN, fg=BLUE,
                  font=(FH, 10, "bold"), padx=8, cursor="hand2").pack(side="right", padx=2)
        tk.Button(top_frame, text="■ 停止", command=self._stop_multi_buy_ui, bg=BTN, fg=RED,
                  font=(FH, 10, "bold"), padx=8, cursor="hand2").pack(side="right", padx=2)

        # 双列表组件
        self.multi_buy_panel = DualListPanel(
            frame, title="",
            all_generals_data=self.all_generals_data,
            list_name="multi_buy",
            ops=self.ops,
            ops_push_method=self._push_multi_buy_targets,
            log_func=self.log, height=10,
            left_title="全部武将", right_title="购买目标武将"
        )
        self.multi_buy_panel.pack(fill="both", expand=True)

    def _build_discard_management_tab(self):
        """v4.13★ 遣散管理 tab — 双列表(遣散目标+永久保留) + 执行按钮 + 策略参数"""
        from ui.dual_list import DualListPanel
        frame = self._make_scrollable_tab(self.tab_discard)

        # 顶部执行按钮区
        exec_frame = tk.Frame(frame, bg=BG)
        exec_frame.pack(fill="x", pady=(0, 5))
        tk.Button(exec_frame, text="执行策略遣散 (F7)", command=self._trigger_discard_target_chess_ids_ui, bg=RED, fg=FG,
                  font=(FH, 10, "bold"), padx=8, cursor="hand2").pack(side="left", padx=2)
        tk.Button(exec_frame, text="全遣散手牌", command=self._trigger_discard_all_hand, bg=RED, fg=FG,
                  font=(FH, 10, "bold"), padx=8, cursor="hand2").pack(side="left", padx=2)

        # v4.153★ 快速预设: 「太史慈吴遣散」「找黄盖」这一类一句话规则存下来, 一键套用
        self._build_preset_bar(frame, "dismiss")

        # 遣散目标双列表
        self.discard_target_panel = DualListPanel(
            frame, title="",
            all_generals_data=self.all_generals_data,
            list_name="discard_target",
            ops=self.ops,
            ops_push_method=self.ops.update_target_discard_ids,
            log_func=self.log, height=6,
            left_title="全部武将", right_title="遣散目标武将"
        )
        self.discard_target_panel.pack(fill="both", expand=True, pady=(0, 5))

        # 永久保留双列表
        self.permanent_keep_panel = DualListPanel(
            frame, title="",
            all_generals_data=self.all_generals_data,
            list_name="permanent_keep",
            ops=self.ops,
            ops_push_method=self.ops.update_permanent_keep_ids,
            log_func=self.log, height=6,
            left_title="全部武将", right_title="永久保留武将 (不被遣散)"
        )
        self.permanent_keep_panel.pack(fill="both", expand=True, pady=(0, 5))

        # 策略参数
        policy_frame = tk.LabelFrame(frame, text="遣散策略参数", bg=BG, fg=GOLD, bd=1, relief="solid", font=(FH, 10, "bold"))
        policy_frame.pack(fill="x", pady=5)
        tk.Label(policy_frame, text="保留星级≤:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left", padx=5, pady=5)
        self.dismiss_max_star_var = tk.StringVar(value=str(self.cfg.get("dismiss_policy", {}).get("max_star", 1)))
        ttk.OptionMenu(policy_frame, self.dismiss_max_star_var, self.dismiss_max_star_var.get(), "1", "2", "3").pack(side="left", padx=5)
        tk.Label(policy_frame, text="保留价值≥:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left", padx=15)
        self.dismiss_min_cost_var = tk.StringVar(value=str(self.cfg.get("dismiss_policy", {}).get("min_cost", 1)))
        ttk.OptionMenu(policy_frame, self.dismiss_min_cost_var, self.dismiss_min_cost_var.get(), "1", "2", "3", "4", "5").pack(side="left", padx=5)

        # 说明
        tk.Label(frame, text="说明: F7=按右侧「遣散目标」列表遣散; 全遣散=手牌全部遣散;\n"
                            "两者都跳过「永久保留」列表中的武将。保存配置即自动推送油猴端。\n"
                            "v4.89★ 「永久保留」会自动带上同名卡的【其它品质】: 普通和金卡是同名不同 ID\n"
                            "(尾数 1=普通 / 2=金), 只填普通的那条, 金卡一样会被遣散 —— 现在会自动一起保留。",
                 bg=BG, fg=DIM, font=(FH, 9), justify="left").pack(anchor="w", pady=5)

    def _make_scrollable_tab(self, parent, padx=(10, 0), pady=10, hscroll=False):
        """v4.68★ 把 Tab 内容放进 Canvas+Scrollbar，避免内容过多被窗口截断。

        返回一个 Frame，后续控件直接 pack 到这个 Frame 里即可。
        鼠标滚轮只在当前 Tab 可见时才滚动，避免多个可滚动 Tab 同时响应。
        v4.74★ hscroll=True 时再加一条横向滚动条(内容比窗口宽时左右滑, Shift+滚轮也行)。
          用户原话: "给你这个页面加个左右也可以滑动吧, 像竖向那种"。资源替换页的表格/说明
          一行很长, 窗口窄时右边被裁掉又没法拖 → 横向滚动补齐。默认 False, 其它页不受影响。
        """
        if hscroll:
            # 外面套一层 holder: 画布占主体, 竖条在右、横条在底
            holder = tk.Frame(parent, bg=BG)
            holder.pack(side="left", fill="both", expand=True, padx=padx, pady=pady)
            canvas = tk.Canvas(holder, bg=BG, highlightthickness=0)
            scrollbar = tk.Scrollbar(holder, orient="vertical", command=canvas.yview)
            hbar = tk.Scrollbar(holder, orient="horizontal", command=canvas.xview)
            canvas.configure(yscrollcommand=scrollbar.set, xscrollcommand=hbar.set)
            scrollbar.pack(side="right", fill="y")
            hbar.pack(side="bottom", fill="x")
            canvas.pack(side="left", fill="both", expand=True)
        else:
            canvas = tk.Canvas(parent, bg=BG, highlightthickness=0)
            scrollbar = tk.Scrollbar(parent, orient="vertical", command=canvas.yview)
            canvas.pack(side="left", fill="both", expand=True, padx=padx, pady=pady)
            scrollbar.pack(side="right", fill="y")
        frame = tk.Frame(canvas, bg=BG)

        _win = canvas.create_window((0, 0), window=frame, anchor="nw")

        # 让内容宽度跟随画布宽度(fill="x" 的块才不会缩成一条)
        # v4.70★ 关键修复: 当 Tab 内容(如对局复盘的 PanedWindow)使用 fill="both"+expand=True
        #   时, Canvas 里的 frame 高度必须【至少等于画布高度】, 否则 expand 控件展不开,
        #   页面就会缩成顶部一行、下面大片空白。内容更高时, 又让 frame 按自身需求扩展。
        def _ensure_frame_height():
            # v4.73★ 兼容修复: 原来恢复自然高度用 height=""(旧 Tk 认, 新 Tk 报
            #   TclError: bad screen distance "") —— 画布 window item 的 -height 是
            #   屏幕距离, 新版 Tk 不再接受空串。按 Tk 文档, <=0 即"用窗口自身请求高度",
            #   语义与原来的空串完全等价且各版本通吃。页签销毁后 configure 回调可能
            #   仍被触发, 先查 winfo_exists 再动手(不吞异常, 静默except预算不放松)。
            if not canvas.winfo_exists():
                return
            ch = canvas.winfo_height() or 1
            rh = frame.winfo_reqheight()
            if rh < ch:
                canvas.itemconfigure(_win, height=ch)
            else:
                canvas.itemconfigure(_win, height=0)

        def _on_canvas_cfg(e):
            if hscroll:
                # 内容比画布宽时不强行压到画布宽 —— 保持自然宽度, 横条才有得滚
                rw = frame.winfo_reqwidth()
                canvas.itemconfigure(_win, width=max(e.width, rw))
            else:
                canvas.itemconfigure(_win, width=e.width)
            _ensure_frame_height()

        def _on_frame_cfg(e):
            _ensure_frame_height()
            canvas.configure(scrollregion=canvas.bbox("all"))

        canvas.bind("<Configure>", _on_canvas_cfg)
        frame.bind("<Configure>", _on_frame_cfg)

        def _on_mousewheel(event):
            if canvas.winfo_viewable():
                if hscroll and (event.state & 0x0004):   # Shift 按着 → 左右滑
                    canvas.xview_scroll(int(-1 * (event.delta / 120)), "units")
                else:
                    canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)
        return frame

    def _build_data_management_tab(self):
        # v4.12★ 加滚动条 (Canvas+Scrollbar)
        frame = self._make_scrollable_tab(self.tab_data_management)

        tk.Label(frame, text="数据文件说明:", bg=BG, fg=FG, font=(FH, 12, "bold")).pack(anchor="w", pady=(0, 10))

        info_text = """
        核心数据文件及其来源和功能:
        - generals.json: 武将百科数据，由TavernChessConfig.json提取，包含武将详细属性。
        - tavern_chess.json: 自走棋武将数据，由TavernChessConfig.json提取，用于游戏内匹配。
        - tavern_spells.json: 自走棋锦囊数据，由TavernChessConfig.json提取。
        - skills.json: 独立技能库，由TavernChessConfig.json提取，包含技能描述。
        - lords.json: 主公数据，由TavernChessConfig.json提取。
        - lord_skills.json: 主公技能数据，由TavernChessConfig.json提取。
        - cards.json: 通用卡牌数据，由TavernChessConfig.json提取。
        - chess.json: 棋子详细属性，由TavernChessConfig.json提取。
        - discovered_ids.json: 助手累积学习到的ID映射，包含各种ID到名称的映射。

        数据来源: 主要从游戏内存中的 TavernChessConfig.json 配置文件解析。
        点击下方按钮可从指定网页重新获取最新数据并更新本地文件。
        """
        tk.Label(frame, text=info_text, bg=BG, fg=FG, justify="left", wraplength=700, font=(FH, 10)).pack(anchor="w", pady=(0, 15))

        fetch_button_frame = tk.Frame(frame, bg=BG)
        fetch_button_frame.pack(fill="x", pady=(0, 10))

        tk.Button(fetch_button_frame, text="从网页重新获取数据", command=self._refetch_data_from_web, bg=BLUE, fg=FG,
                  font=(FH, 10, "bold"), padx=6, cursor="hand2").pack(side="left", padx=(0, 5))
        # v4.42★ 手动刷新入口。以前"全部武将"只在助手启动时取一次, 而唯一能重新取数的
        #   _refresh_generals_for_ui 被拴在『推送/替换』的"顺带重建武将精选"勾选框后面 ——
        #   ChessConf.csv / 白名单 / tavern_chess.json 变了以后, 招募/多选/遣散/随征/白名单/
        #   黑名单六个面板不会跟着变, 只能重启。这里给一个随时能点的按钮。
        tk.Button(fetch_button_frame, text="刷新武将表", command=self._refresh_generals_for_ui,
                  bg=BLUE, fg=FG, font=(FH, 10, "bold"), padx=6,
                  cursor="hand2").pack(side="left", padx=(0, 5))

        self.data_refetch_status_var = tk.StringVar(value="等待操作")
        tk.Label(fetch_button_frame, textvariable=self.data_refetch_status_var, bg=BG, fg=DIM, font=(FH, 9)).pack(side="left", padx=(5, 0))

        # v4.71★ 协议流水【落盘开关】补到这里 —— 它原来挂在被删掉的『对局复盘』页上。
        #   为什么必须补一个入口(而不是"默认关就够了"): core/config.py 的 net_record 默认
        #   False, 而"要不要记录"的主动权在用户 —— 删页面时把开关一起带走, 以后再想离线
        #   查某一局/做抓包诊断就只能手改 config.json(没人知道有 net_record 这个键)。
        self._build_net_record_section(frame)

        self._build_preset_section(frame)

        self._build_id_configuration_section(frame)

        # Buttons for other complex operations
        complex_ops_button_frame = tk.Frame(frame, bg=BG)
        complex_ops_button_frame.pack(fill="x", pady=(5, 0))

        # v4.82★ 文案改掉: 以前写"最右侧锦囊", 现在 Shift+2 是【先识别手牌再挑一张用】,
        #   写"最右"会让人以为还是老行为(排查时必跑偏)。
        tk.Button(complex_ops_button_frame, text="用锦囊(按识别结果, Shift+2)", command=self._trigger_use_rightmost_spell_ui, bg=ORANGE, fg=FG,
                  font=(FH, 10, "bold"), padx=6, cursor="hand2").pack(side="left", fill="x", expand=True, padx=(0, 5))
        tk.Button(complex_ops_button_frame, text="触发上阵手牌最右侧卡牌 (Alt+0)", command=self._trigger_deploy_rightmost_card_ui, bg=GREEN, fg=FG,
                  font=(FH, 10, "bold"), padx=6, cursor="hand2").pack(side="left", fill="x", expand=True, padx=(5, 0))

    def _build_net_record_section(self, parent_frame):
        """协议流水落盘开关(默认关) —— data/net_samples.json / net_structures.json。

        为什么默认关: 这两份文件是"内存常驻镜像 + 定期全量重写"(实测约 2MB), 用户原话
        「占内存、占存储、占发数据时间」。关的只是【写盘】—— 实时对局/决策页与所有读协议的
        功能都走 STATE.net_sink(内存队列), 一律不受影响。
        什么时候要打开: 离线抓包诊断, 或想把某一局的事后流水翻出来看。查完记得关回去。
        """
        box = tk.LabelFrame(parent_frame, text=" 协议流水记录 (诊断用, 默认关) ",
                            bg=BG, fg=GOLD, font=(FH, 11, "bold"), bd=1, relief="groove")
        box.pack(fill="x", pady=(12, 6))
        self.net_record_var = tk.BooleanVar(value=bool(self.cfg.get("net_record")))
        tk.Checkbutton(box, text="把游戏页推上来的协议流水写到 data/ (net_samples.json / net_structures.json)",
                       variable=self.net_record_var, command=self._toggle_net_record,
                       bg=BG, fg=FG, selectcolor=BG, activebackground=BG, activeforeground=FG,
                       font=(FH, 10), anchor="w", cursor="hand2").pack(fill="x", padx=8, pady=(8, 2))
        tk.Label(box, text=(
            "关着不影响任何功能: 实时对局/决策页读的是内存队列, 不读这两个文件。\n"
            "只有「事后把某一局翻出来看」和「离线抓包诊断」需要它。打开后两个文件各留最近 500 条(约 2MB), "
            "排查完请关回去; 关掉不会删已写下的文件(想清理就到菜单『文件 → 打开数据目录』手动删)。"),
            bg=BG, fg=DIM, font=(FH, 9), justify="left", anchor="w",
            wraplength=900).pack(fill="x", padx=8, pady=(0, 8))

    def _toggle_net_record(self):
        on = bool(self.net_record_var.get())
        self.cfg["net_record"] = on
        try:
            save_config(self.cfg)
        except Exception as e:
            self.log(f"[记录] 开关没存上: {e}", level="ERROR")
            messagebox.showerror("协议流水记录", f"开关没存上: {e}")
            return
        if on:
            self.log("[记录] 协议流水落盘 = 开 —— 现在起会写 data/net_samples.json 与 "
                     "data/net_structures.json(各留最近 500 条)。这是诊断用的, 排查完请回来关掉。")
        else:
            self.log("[记录] 协议流水落盘 = 关(默认) —— 不再写盘; 已写下的文件保留, "
                     "要清理就到菜单『文件 → 打开数据目录』删那两个 json。")

    # v4.101★ 这三个按钮走的是 ops.hotkey() —— 超时【8 秒】。以前是在 Tk 主线程里直连:
    #   游戏页没开 / 脚本没注入时, 点一下界面就僵 8 秒、窗口拖不动, 看着像"卡死", 其实只是在
    #   等回包。现在丢后台线程, 主线程只负责先回一句"已发送"。
    def _bg_log_done(self, tag, ok_msg=None):
        """通用收尾: 后台那一步跑完只往日志/状态栏回一句(成功不啰嗦, 失败一定报错)。"""
        def _cb(r, err):
            if err:
                self.log(f"[{tag}] 失败: {err}", level="WARN")
                self._status(f"{tag} 失败: {err}")
            elif ok_msg:
                self.log(f"[{tag}] {ok_msg}")
                self._status(ok_msg)
        return _cb

    def _trigger_done(self, label):
        def _cb(r, err):
            # v4.101★ 结果一定要【看得见】: 以前这类动作只在日志里留一行, 而状态栏一直停在
            #   "已发送…" —— 用户点了没看到任何变化, 就是"这个按钮不管用"。现在状态栏也回一句。
            if err:
                self.log(f"[UI] {label} 失败: {err}", level="WARN")
                self._status(f"{label} 失败: {err}")
            else:
                txt = "(无回包: 游戏页没连上/超时)" if r is None else str(r)
                self.log(f"[UI] {label}: {txt}")
                self._status(f"{label} → {txt}")
        return _cb

    def _status(self, text):
        """往底部状态栏写一句(顺带记日志): 让用户【不用翻日志】就知道刚才那一步的结果。"""
        try:
            self.status_bar_var.set(str(text))
        except Exception as e:
            self.log(f"状态栏更新失败: {e}", level="WARN")

    def _trigger_use_rightmost_spell_ui(self):
        self.log("[UI] 用锦囊(先识别手牌里的锦囊, 再挑一张用)")
        self._run_bg("use_spell", (lambda: self.ops.trigger_use_rightmost_spell()),
                     self._trigger_done("用锦囊"), wait="已发送: 用锦囊(等游戏页回包)…")

    def _trigger_deploy_rightmost_card_ui(self):
        self.log("[UI] 触发上阵手牌最右侧卡牌")
        self._run_bg("deploy_card", (lambda: self.ops.trigger_deploy_rightmost_card()),
                     self._trigger_done("上阵最右侧卡牌"), wait="已发送: 上阵(等游戏页回包)…")

    def _trigger_discard_target_chess_ids_ui(self):
        self.log("[UI] 触发一键遣散指定卡牌")
        self._run_bg("discard_target", (lambda: self.ops.trigger_discard_target_chess_ids()),
                     self._trigger_done("一键遣散"), wait="已发送: 一键遣散(等游戏页回包)…")

    # ---------- 配置预设 导入/导出 (v4.47★) ----------
    def _build_preset_section(self, parent_frame):
        """配置预设: 把"我调好的这一套"整包导出/导入(换机器、给别人、留档)。

        为什么要分块而不是"一键全导": 助手配置里混着【只对本机有效】的东西 —— game_region
        等屏幕坐标、bridge.refresh_pos 这种校准值。整包搬过去到新机器上校准全错, 而校准错了
        的现象(点不到刷新按钮)和"配置没导进来"完全分不清。所以默认只勾内容(列表/剧本/行为),
        坐标类默认不勾。
        """
        from core import preset as P
        box = tk.LabelFrame(parent_frame, text=" 配置预设 (整包导出/导入: 换机器 / 给别人 / 留档) ",
                            bg=BG, fg=GOLD, font=(FH, 11, "bold"), bd=1, relief="groove")
        box.pack(fill="x", pady=(12, 6))

        self._preset_vars = {}
        default_on = {"lists", "scenarios", "behavior"}
        rows = tk.Frame(box, bg=BG)
        rows.pack(fill="x", padx=8, pady=(8, 2))
        for key, desc in P.PARTS:
            var = tk.BooleanVar(value=(key in default_on))
            self._preset_vars[key] = var
            sub = tk.Frame(rows, bg=BG)
            sub.pack(anchor="w")
            tk.Checkbutton(sub, text=desc, variable=var, bg=BG, fg=FG, selectcolor="#33333f",
                           activebackground=BG, activeforeground=FG, font=(FH, 9)).pack(side="left")
            if key in ("window", "bridge"):
                tk.Label(sub, text="◀ 只对本机有效, 换机器别勾", bg=BG, fg=ORANGE,
                         font=(FH, 8)).pack(side="left", padx=6)

        btns = tk.Frame(box, bg=BG)
        btns.pack(fill="x", padx=8, pady=(6, 2))
        tk.Button(btns, text="导出预设 …", command=self.a_preset_export, bg=GREEN, fg=FG,
                  font=(FH, 9, "bold"), padx=8, cursor="hand2").pack(side="left", padx=(0, 6))
        tk.Button(btns, text="预览预设 …", command=self.a_preset_preview, bg=BTN, fg=BLUE,
                  font=(FH, 9, "bold"), padx=8, cursor="hand2").pack(side="left", padx=(0, 6))
        tk.Button(btns, text="导入预设 …", command=self.a_preset_import, bg=ORANGE, fg=FG,
                  font=(FH, 9, "bold"), padx=8, cursor="hand2").pack(side="left", padx=(0, 10))
        self.preset_status_var = tk.StringVar(value="等待操作")
        tk.Label(btns, textvariable=self.preset_status_var, bg=BG, fg=DIM,
                 font=(FH, 9)).pack(side="left")
        tk.Label(box, text=("导入 = 覆盖本机这几块配置, 导入前会自动整份备份 config.json + data/lists/*.json "
                            "到 data/_backup/preset_<时间>/, 日志里会打印路径, 想退回按路径还原即可。\n"
                            "勾『列表』导入时可选【覆盖】或【并集】—— 只想把别人白名单里多出来的卡并进来时用并集。"),
                 bg=BG, fg=DIM, font=(FH, 8), wraplength=800, justify="left").pack(anchor="w", padx=8, pady=(0, 8))

    def _preset_parts(self):
        """当前勾选了哪几块(按 PARTS 顺序)。"""
        from core import preset as P
        return [k for k, _d in P.PARTS if getattr(self, "_preset_vars", {}).get(k)
                and self._preset_vars[k].get()]

    def _preset_pick_parts(self, action):
        """导出/导入前把勾选情况确认一次(空勾选直接拦下)。"""
        from core import preset as P
        parts = self._preset_parts()
        if not parts:
            messagebox.showinfo("配置预设", "一块都没勾 —— 请先勾上要%s的内容。" % action)
            return None
        return parts

    def a_preset_export(self):
        """导出预设: 写一个 json(单文件, 好传给别人)。"""
        from tkinter import filedialog
        from core import preset as P
        parts = self._preset_pick_parts("导出")
        if parts is None:
            return
        init = "自走棋助手_预设_%s.json" % datetime.now().strftime("%Y%m%d")
        path = filedialog.asksaveasfilename(title="导出配置预设", defaultextension=".json",
                                           initialfile=init, filetypes=[("预设包", "*.json")])
        if not path:
            return
        try:
            info = P.export_preset(path, parts)
        except Exception as e:
            self.log(f"[预设] 导出失败: {e}", level="ERROR")
            messagebox.showerror("配置预设", "导出失败: %s" % e)
            return
        msg = ("已导出 %d 块 (%s); 列表 %d 个共 %d 条 ID, 剧本 %d 个"
               % (len(info["parts"]), "/".join(info["parts"]),
                  info["n_lists"], info["n_ids"], info["n_scenarios"]))
        self.preset_status_var.set("已导出 → " + os.path.basename(path))
        self.log("[预设] %s → %s" % (msg, path))
        for p in parts:
            self.log("[预设]   含: " + P.PART_LABEL.get(p, p))

    def a_preset_preview(self):
        """预览预设: 导入前先看清楚这个包里有什么、有几条, 别直接把别人的配置盖上来。"""
        from tkinter import filedialog
        from core import preset as P
        path = filedialog.askopenfilename(title="选择要预览的预设包",
                                          filetypes=[("预设包", "*.json"), ("全部文件", "*.*")])
        if not path:
            return
        try:
            meta, data = P.read_preset(path, with_data=True)
            info = P.summarize({"parts": meta["parts"], "data": data}, path=path)
        except Exception as e:
            self.log(f"[预设] 读预设失败: {e}", level="ERROR")
            messagebox.showerror("配置预设", "读不出来: %s" % e)
            return
        txt = ("包: %s\n导出时间: %s   版本: %s\n\n包含 %d 块:\n%s\n\n"
               "列表 %d 个, 共 %d 条 ID; 剧本 %d 个"
               % (os.path.basename(path), meta.get("created_at") or "?", meta.get("version"),
                  len(info["parts"]),
                  "\n".join("  · " + P.PART_LABEL.get(p, p) for p in info["parts"]),
                  info["n_lists"], info["n_ids"], info["n_scenarios"]))
        self.preset_status_var.set("已预览: " + os.path.basename(path))
        self.log("[预设] 预览 %s: 块=%s, 列表 %d 个/%d 条 ID, 剧本 %d 个"
                 % (path, info["parts"], info["n_lists"], info["n_ids"], info["n_scenarios"]))
        messagebox.showinfo("预设包内容", txt)

    def a_preset_import(self):
        """导入预设: 覆盖 / 并集 两条路, 覆盖前自动整份备份。"""
        from tkinter import filedialog
        from core import preset as P
        parts = self._preset_pick_parts("导入")
        if parts is None:
            return
        path = filedialog.askopenfilename(title="选择要导入的预设包",
                                          filetypes=[("预设包", "*.json"), ("全部文件", "*.*")])
        if not path:
            return
        try:
            meta, _d = P.read_preset(path)
        except Exception as e:
            self.log(f"[预设] 读预设失败: {e}", level="ERROR")
            messagebox.showerror("配置预设", "读不出来: %s" % e)
            return
        use = [p for p in meta["parts"] if p in parts]
        if not use:
            messagebox.showinfo("配置预设",
                                "这个包里没有你勾选的内容。\n\n包里只有:\n" +
                                "\n".join("  · " + P.PART_LABEL.get(p, p) for p in meta["parts"]))
            return
        choice = messagebox.askyesnocancel(
            "导入配置预设",
            "包: %s\n导出时间: %s\n将导入 %d 块:\n%s\n\n"
            "是 = 覆盖(预设整份替换本机这几块)\n"
            "否 = 并集(只对武将列表生效: 把包里多出来的 ID 并进来, 本机原来的不动)\n"
            "取消 = 不导了"
            % (os.path.basename(path), meta.get("created_at") or "?",
               len(use), "\n".join("  · " + P.PART_LABEL.get(p, p) for p in use)))
        if choice is None:
            return
        mode = "replace" if choice else "merge"
        try:
            r = P.import_preset(path, parts=use, mode=mode)
        except Exception as e:
            self.log(f"[预设] 导入失败: {e}", level="ERROR")
            messagebox.showerror("配置预设", "导入失败: %s" % e)
            return
        # 列表已经落盘 —— 把内存/界面同步过来, 否则面板还显示旧内容, 用户会以为没生效。
        self.cfg = load_config()
        try:
            self._preset_sync_ui()
        except Exception as e:
            self.log(f"[预设] 界面同步出错(数据已写入, 重启即可): {e}", level="WARN")
        self.preset_status_var.set("已导入(%s) ← %s" % ("覆盖" if choice else "并集",
                                                       os.path.basename(path)))
        for line in P.format_report(r).splitlines():
            self.log("[预设] " + line)

    def _preset_sync_ui(self):
        """导入后把界面同步到新配置: 列表面板重读 + 能刷的几个勾选框跟着变。"""
        self._refresh_generals_for_ui()
        for attr in ("recruit_panel", "multi_buy_panel", "discard_target_panel",
                     "permanent_keep_panel", "followup_panel", "whitelist_panel", "blacklist_panel"):
            panel = getattr(self, attr, None)
            if panel is None or not hasattr(panel, "reload_from_disk"):
                continue
            try:
                panel.reload_from_disk()
            except Exception as e:
                self.log(f"[预设] 面板 {attr} 重读失败: {e}", level="WARN")
        # 这几个开关是"保存即生效"的, 不等重启
        var = getattr(self, "script_map_season_var", None)
        if var is not None:
            var.set(bool((self.cfg or {}).get("script_map_season_only")))
        self.log("[预设] 列表已即时重读; 剧本/热键/Flag 等界面元素重启助手后完全生效。")

    def _build_id_configuration_section(self, parent_frame):
        """v4.13★ 用 DualListPanel 替换旧的单 listbox"""
        from ui.dual_list import DualListPanel
        tk.Label(parent_frame, text="武将ID配置 (双列表, 保存即推送油猟端):", bg=BG, fg=FG, font=(FH, 12, "bold")).pack(anchor="w", pady=(10, 5))

        # 随征卡牌ID
        self.followup_panel = DualListPanel(
            parent_frame, title="",
            all_generals_data=self.all_generals_data,
            list_name="followup",
            ops=self.ops,
            ops_push_method=self.ops.update_followup_ids,
            log_func=self.log, height=6,
            left_title="全部武将", right_title="随征卡牌ID"
        )
        self.followup_panel.pack(fill="both", expand=True, pady=(0, 5))

        # 白名单 (当前赛季)
        self.whitelist_panel = DualListPanel(
            parent_frame, title="",
            all_generals_data=self.all_generals_data,
            list_name="whitelist",
            ops=self.ops,
            ops_push_method=None,
            log_func=self.log, height=6,
            left_title="全部武将", right_title="白名单 (当前赛季)"
        )
        self.whitelist_panel.pack(fill="both", expand=True, pady=(0, 5))

        # 黑名单 —— v4.101★ 标题改掉: 以前写"黑名单 (全部赛季)", 于是它被当成
        #   "全赛季武将统计表"往里灌了 3346 条全量武将 → F6 一张都买不到。
        #   黑名单的本义是【你不想买/不想留的武将】, 空着才是对的; 要看全部武将用下面那块。
        self.blacklist_panel = DualListPanel(
            parent_frame, title="",
            all_generals_data=self.all_generals_data,
            list_name="blacklist",
            ops=self.ops,
            ops_push_method=None,
            log_func=self.log, height=6,
            left_title="全部武将", right_title="黑名单 (不想买/不想留的, 可以为空)"
        )
        self.blacklist_panel.pack(fill="both", expand=True, pady=(0, 5))

        # v4.101★ 全量武将【统计 / 查看】(只读)
        self._build_all_generals_stats(parent_frame)

    # ---------- v4.101★ 全量武将统计(只读) ----------
    def _build_all_generals_stats(self, parent):
        """全部赛季武将的统计与查看 —— 这就是以前"拿黑名单顶着"的那个用途。

        为什么单独做一块: 黑名单被当成统计表灌进 3346 条之后, 它就不只是"看着乱"了 ——
        F6 买目标每张卡都先命中黑名单(白名单 184 条被 100% 覆盖), 一张都买不到; F7 的
        兜底分支拿它按名字遣散, 等于把备战区清空。这里明确【只读】: 不参与买/遣散的任何判断。
        """
        box = tk.LabelFrame(parent, text=" 全量武将 · 统计与查看 (只读, 不参与买卡/遣散) ",
                            bg=BG, fg=GOLD, font=(FH, 10, "bold"), bd=1, relief="groove")
        box.pack(fill="both", expand=True, pady=(6, 10))
        self._allgen_stat_var = tk.StringVar(value="统计中…")
        tk.Label(box, textvariable=self._allgen_stat_var, bg=BG, fg=FG, font=(FH, 9),
                 justify="left", wraplength=860).pack(anchor="w", padx=8, pady=(6, 2))
        bar = tk.Frame(box, bg=BG)
        bar.pack(fill="x", padx=8, pady=(4, 2))
        tk.Label(bar, text="搜索:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left")
        self._allgen_search_var = tk.StringVar()
        ent = tk.Entry(bar, textvariable=self._allgen_search_var, bg=BTN, fg=FG,
                       insertbackground=FG, font=(FH, 9), width=22)
        ent.pack(side="left", padx=4)
        ent.bind("<Return>", lambda _e: self._allgen_fill())
        tk.Button(bar, text="列出", command=self._allgen_fill, bg=BTN, fg=FG,
                  font=(FH, 8), padx=6, cursor="hand2").pack(side="left", padx=(2, 6))
        tk.Label(bar, text="(名字或ID, 回车查看; 最多列 300 条)", bg=BG, fg=DIM,
                 font=(FH, 8)).pack(side="left")
        lb = tk.Listbox(box, height=8, bg="#1e1e1e", fg=FG, font=(FH, 9),
                        selectbackground="#3a3a4a")
        lb.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self._allgen_listbox = lb
        self._allgen_rows = None
        # 统计延后一点点算: 3346 条要遍历一遍, 别拖慢启动
        self.root.after(400, self._allgen_stat)

    def _allgen_rows_cached(self):
        if self._allgen_rows is not None:
            return self._allgen_rows
        rows = []
        try:
            from core import csv_loader
            rows = list(csv_loader.get_all_generals_from_csv() or [])
        except Exception as e:
            self.log(f"[全量武将] 读取失败(回退界面缓存): {e}", level="WARN")
        if not rows:
            rows = list(self.all_generals_data or [])
        self._allgen_rows = rows
        return rows

    def _allgen_stat(self):
        try:
            rows = self._allgen_rows_cached()
            total = len(rows)
            by_season, by_rank = {}, {}
            for r in rows:
                if not isinstance(r, dict):
                    continue
                s = str(r.get("season") or "?")
                k = str(r.get("rank") or "?")
                by_season[s] = by_season.get(s, 0) + 1
                by_rank[k] = by_rank.get(k, 0) + 1
            txt = f"共 {total} 名武将 · 覆盖 {len(by_season)} 个赛季"
            if by_season:
                last = sorted(by_season.items(), key=lambda kv: kv[0])[-6:]
                txt += " (最近: " + ", ".join(f"S{s}·{n}名" for s, n in last) + ")"
            if by_rank:
                txt += " · 星级 " + ", ".join(f"{k}星 {v}名" for k, v in sorted(by_rank.items())[:6])
            txt += "\n这块只是【统计和查看】, 不参与买卡/遣散 —— 以前拿黑名单顶这个用途, 结果 F6 一张都买不到。"
        except Exception as e:
            txt = f"统计失败: {e}"
        try:
            self._allgen_stat_var.set(txt)
        except Exception as e:
            self.log(f"[全量武将] 统计显示失败: {e}", level="WARN")

    def _allgen_fill(self):
        lb = getattr(self, "_allgen_listbox", None)
        if lb is None:
            return
        lb.delete(0, tk.END)
        kw = (self._allgen_search_var.get() or "").strip().lower()
        shown = 0
        for r in self._allgen_rows_cached():
            if not isinstance(r, dict):
                continue
            cid = str(r.get("chessID") or r.get("id") or "")
            nm = str(r.get("name") or "")
            if kw and kw not in nm.lower() and kw not in cid:
                continue
            lb.insert(tk.END, f"{nm} ({cid}) · S{r.get('season') or '?'} · {r.get('rank') or '?'}星")
            shown += 1
            if shown >= 300:
                lb.insert(tk.END, "… 只列前 300 条, 把搜索词写细一点")
                break
        if shown == 0:
            lb.insert(tk.END, "(没有匹配的武将)")

    def _create_id_list_frame(self, parent_frame, title, listbox_attr, search_var_attr, save_command):
        frame = tk.LabelFrame(parent_frame, text=title, bg=BG, fg=FG, font=(FH, 10, "bold"), bd=2, relief="groove")
        frame.pack(side="left", fill="both", expand=True, padx=5, pady=5)

        # Search
        search_sub_frame = tk.Frame(frame, bg=BG)
        search_sub_frame.pack(fill="x", pady=(5, 0))
        tk.Label(search_sub_frame, text="搜索:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left", padx=(0, 5))
        search_var = getattr(self, search_var_attr)
        search_var.trace("w", lambda *args: self._filter_id_list(search_var, listbox_attr, *args))
        tk.Entry(search_sub_frame, textvariable=search_var, bg=BTN, fg=FG, insertbackground=FG, font=(FH, 9)).pack(side="left", fill="x", expand=True)

        # Listbox
        listbox_sub_frame = tk.Frame(frame, bg=BG)
        listbox_sub_frame.pack(fill="both", expand=True, pady=(5, 5))

        listbox = tk.Listbox(listbox_sub_frame, selectmode=tk.MULTIPLE, bg=BTN, fg=FG, selectbackground="#2a6db5", selectforeground="#ffffff", font=(FH, 9), height=8)
        listbox.pack(side="left", fill="both", expand=True)
        setattr(self, listbox_attr, listbox) # Store listbox reference in instance

        scrollbar = tk.Scrollbar(listbox_sub_frame, orient="vertical", command=listbox.yview)
        scrollbar.pack(side="right", fill="y")
        listbox.config(yscrollcommand=scrollbar.set)

        # Populate listbox with all generals initially
        for gen in self.all_generals_data:
            listbox.insert(tk.END, gen.get("display", gen["name"]))

        # Save Button
        tk.Button(frame, text="保存配置", command=save_command, bg=BLUE, fg=FG,
                  font=(FH, 9, "bold"), padx=6, cursor="hand2").pack(fill="x", pady=(0, 5))

    def _filter_id_list(self, search_var, listbox_attr, *args):
        search_term = search_var.get().lower()
        listbox = getattr(self, listbox_attr)
        listbox.delete(0, tk.END)
        for gen in self.all_generals_data:
            display = gen.get("display", gen["name"])
            if search_term in display.lower():
                listbox.insert(tk.END, display)

    def _filter_recruit_generals(self, *args):
        """过滤招募tab的武将列表"""
        if not hasattr(self, 'recruit_generals_listbox'):
            return
        search_term = self.recruit_search_var.get().lower()
        self.recruit_generals_listbox.delete(0, tk.END)
        for gen in self.all_generals_data:
            display = gen.get("display", gen["name"])
            if search_term in display.lower():
                self.recruit_generals_listbox.insert(tk.END, display)

    def _filter_discard_target_list(self, *args):
        """过滤遣散管理tab的武将列表"""
        if not hasattr(self, 'discard_target_listbox'):
            return
        search_term = self.discard_target_search_var.get().lower()
        self.discard_target_listbox.delete(0, tk.END)
        for gen in self.all_generals_data:
            display = gen.get("display", gen["name"])
            if search_term in display.lower():
                self.discard_target_listbox.insert(tk.END, display)

    def _filter_permanent_keep_list(self, *args):
        """v4.12★ 过滤永久保留武将列表"""
        if not hasattr(self, 'permanent_keep_listbox'):
            return
        search_term = self.permanent_keep_search_var.get().lower()
        self.permanent_keep_listbox.delete(0, tk.END)
        for gen in self.all_generals_data:
            display = gen.get("display", gen["name"])
            if search_term in display.lower():
                self.permanent_keep_listbox.insert(tk.END, display)

    def _save_list_config(self, list_name, listbox_attr, ops_method=None):
        """v4.11★ 通用列表配置保存: 从 listbox 获取选中项 → 提取ID → 保存json → 推送到油猟端"""
        listbox = getattr(self, listbox_attr, None)
        if not listbox:
            self.log(f"[{list_name}] listbox 不存在", level="ERROR")
            return
        selected_indices = listbox.curselection()
        selected_displays = [listbox.get(i) for i in selected_indices]
        target_ids = []
        target_names = []
        for display in selected_displays:
            gen_id = self.ops.get_general_id_by_name(display)
            if gen_id:
                target_ids.append(str(gen_id))
                # 提取纯名字部分
                name_part = display.rsplit(" (", 1)[0] if " (" in display else display
                target_names.append(name_part)
            else:
                self.log(f"[{list_name}] 未找到 {display} 对应的ID", level="WARNING")
        # 保存到 json
        try:
            from core.list_config import save_list
            save_list(list_name, target_ids, target_names)
            self.log(f"[{list_name}] 配置已保存: {len(target_ids)} 个ID")
        except Exception as e:
            self.log(f"[{list_name}] 保存失败: {e}", level="ERROR")
        # 推送到油猟端
        if ops_method:
            try:
                ops_method(target_ids)
            except Exception as e:
                self.log(f"[{list_name}] 推送油猟端失败: {e}", level="ERROR")

    def _load_saved_list_config(self, list_name, listbox_attr):
        """v4.11★ 从 json 加载保存的配置, 恢复 listbox 选中状态"""
        listbox = getattr(self, listbox_attr, None)
        if not listbox:
            return
        try:
            from core.list_config import load_list
            data = load_list(list_name)
            saved_ids = set(data.get("ids", []))
            if not saved_ids:
                return
            # 遍历 listbox 项, 匹配 ID 选中
            for i in range(listbox.size()):
                item_text = listbox.get(i)
                # 从 "名 (ID)" 格式提取 ID
                import re
                m = re.search(r'\((\d+)\)\s*$', item_text.strip())
                if m and m.group(1) in saved_ids:
                    listbox.selection_set(i)
            self.log(f"[{list_name}] 已加载 {len(saved_ids)} 个保存的配置")
        except Exception as e:
            self.log(f"[{list_name}] 加载配置失败: {e}", level="ERROR")

    def _save_whitelist_config(self):
        """保存白名单配置"""
        self._save_list_config("whitelist", "whitelist_listbox")

    def _save_blacklist_config(self):
        """保存黑名单配置"""
        self._save_list_config("blacklist", "blacklist_listbox")

    # v4.11★ 工具栏速度/Flag 快捷回调
    def _on_toolbar_speed_change(self, value):
        try:
            spd = float(value)
        except (TypeError, ValueError):
            return
        self.log(f"[工具栏] 速度切换 → {spd}x")
        # 持久化(本地文件, 快)留在主线程; 下发挪后台 —— set_game_speed 超时 3 秒,
        #   以前在主线程直连: 工具栏下拉框改一次, 界面就僵 3 秒。
        fps_cfg = self.cfg.setdefault("fps", {})
        fps_cfg["speed"] = str(spd)
        save_config(self.cfg)
        self._run_bg("game_speed", (lambda: self.ops.set_game_speed(spd)),
                     self._bg_log_done("工具栏", "速度已下发"), wait=f"已发送: 速度 {spd}x")

    # v4.185★ 工具栏「操作倍率」回调(全局操作倍率, 见 ops.update_global_speed)
    def _on_toolbar_gspeed_change(self, value):
        try:
            spd = float(value)
        except (TypeError, ValueError):
            return
        self.log(f"[工具栏] 操作倍率 → {spd}x")
        self.cfg["global_speed"] = str(spd)
        save_config(self.cfg)
        self._run_bg("global_speed", (lambda: self.ops.update_global_speed(spd)),
                     self._bg_log_done("工具栏", "操作倍率已下发"), wait=f"已发送: 操作倍率 {spd}x")

    def _on_toolbar_flags_change(self):
        flags = {
            "tweenScale": self.toolbar_tween_var.get(),
            "batchProtocol": self.toolbar_batch_var.get(),
        }
        # 保留 recruitFastTween 原值
        flags["recruitFastTween"] = self.cfg.get("flags", {}).get("recruitFastTween", False)
        self.cfg["flags"] = flags
        save_config(self.cfg)
        self.log(f"[工具栏] Flag 切换: {flags}")
        # v4.101★ flags_set 超时 4 秒 —— 参数在主线程读好传进去, 下发丢后台线程。
        self._run_bg("flags_set", (lambda: self.ops.flags_set(flags)),
                     self._bg_log_done("工具栏", "Flag 已下发"), wait="已发送: Flag")

    def _trigger_discard_all_hand(self):
        """v4.11★ 全遣散手牌按钮"""
        self.log("[UI] 触发全遣散手牌")
        # v4.101★ discard_all_hand 超时 5 秒, 以前在主线程: 点一下界面僵 5 秒。
        self._run_bg("discard_all", (lambda: self.ops.discard_all_hand()),
                     self._bg_log_done("UI", "全遣散已送达"), wait="已发送: 全遣散手牌(等回包)…")

    # v4.23★ 清理: 此处原有 _filter_generals_list() 方法, 已整体删除。核实过程(三步都做过):
    #   · 搜 `_filter_generals_list` → 全项目只有这一处定义, **零调用点**(连 keyrelease
    #     绑定都没有), 是 v4.13「多选购买」页签改用 DualListPanel 之前的遗留方法;
    #   · 搜 `\bgenerals_listbox` / `\bmulti_buy_search_var`(词边界, 不会误命中
    #     recruit_generals_listbox / discard_target_listbox) → 仅出现在上面这个方法体内,
    #     且 app.py:113-118 的 None 初始化列表里也没登记 → 这两个属性恒不存在;
    #   · 现页签 _build_multi_buy_tab() 已是纯 DualListPanel(自身带候选/已选双列表),
    #     页签内根本没有搜索框, 过滤职责已移交组件。
    #   三个前提同时成立 ⇒ 没有任何入口能走到它, 删除是纯减法, 不影响运行。

    def _start_multi_buy_ui(self):
        """v4.13★ 从右侧已配置列表读取目标ID

        v4.55★ 下发前先把目标【按武将名展开成全部赛季 ID】(见 _expand_list_ids)。
        在此之前只下发用户勾的单个 ID, 于是"商店刷出同一武将的另一个 ID"就永远匹配不上,
        实机现象就是"有的能拿下来, 有的不能"。
        """
        try:
            entries = self.multi_buy_panel.get_selected_entries()
        except Exception as e:
            self.log(f"[多选购买] 读取已选列表失败, 退回只发 ID: {e}", level="WARNING")
            entries = []
        target_ids = _expand_list_ids(entries) if entries else self.multi_buy_panel.get_selected_ids()
        if not target_ids:
            self.log("[多选购买] 右侧列表为空, 请先添加武将", level="WARNING")
            self._update_multi_buy_status("请先添加武将")
            return
        if len(target_ids) > len(entries):
            self.log(f"[多选购买] {len(entries)} 个武将 → 展开为 {len(target_ids)} 个ID"
                     f"(同一武将各赛季ID都算命中, 这是修『有的能买有的不能』的关键)")
        else:
            self.log(f"[多选购买] 启动监测，目标武将ID: {target_ids}")
        self.ops.start_multi_buy_monitor(target_ids, self._update_multi_buy_status)

    def _stop_multi_buy_ui(self):
        self.ops.stop_multi_buy_monitor()

    def _push_multi_buy_targets(self, ids):
        """v4.12★ 推送多选购买目标到油猟端"""
        try:
            ids_str = json.dumps([str(i) for i in ids])
            self.ops.send_recv("eval", js=f"window.AutoBuyAndSell.updateMultiBuyTargets({ids_str});", timeout=2.0)
            self.log(f"[多选购买] 已推送 {len(ids)} 个目标到油猟端")
        except Exception as e:
            self.log(f"[多选购买] 推送失败: {e}", level="ERROR")




    def _update_multi_buy_status(self, message):
        # v4.101★ 这个回调是【多选购买的工作线程】调进来的(core/remote_ops.py 的
        #   _multi_buy_worker)。Tk 变量只能在主线程写 —— 跨线程写 Tk 轻则界面抽风, 重则
        #   整个进程随机崩(而且崩在 Tcl 里, Python 这边看不到任何 traceback)。
        try:
            self.root.after(0, lambda m=message: self.multi_buy_status_var.set(m))
        except Exception as e:
            sys.stderr.write(f"[多选购买] 状态回界面失败: {e}\n")
        self.log(f"[多选购买状态] {message}")

    def _refetch_data_from_web(self):
        self.log("[数据管理] 开始从网页重新获取数据...")
        self.data_refetch_status_var.set("获取中...")
        
        def _task():
            try:
                # This function needs to be implemented in remote_ops.py
                success = self.ops.refetch_and_process_config()
                if success:
                    self.root.after(0, lambda: self.data_refetch_status_var.set("数据获取成功！"))
                    self.log("[数据管理] 数据获取成功并已更新本地文件。")
                    # Refresh generals list in multi-buy tab after data refetch
                    self.root.after(0, self._refresh_generals_for_ui)
                else:
                    self.root.after(0, lambda: self.data_refetch_status_var.set("数据获取失败。"))
                    self.log("[数据管理] 数据获取失败，请检查网络或游戏状态。", level="ERROR")
            except Exception as e:
                self.root.after(0, lambda: self.data_refetch_status_var.set(f"错误: {e}"))
                self.log(f"[数据管理] 获取数据时发生错误: {e}", level="ERROR")
            
        threading.Thread(target=_task, daemon=True).start()

    def _refresh_generals_for_ui(self):
        """v4.13★ 数据更新后刷新所有 DualListPanel 的武将数据"""
        if hasattr(self, 'ops'):
            self.all_generals_data = self.ops.get_all_generals_for_ui()
            for panel_attr in ['recruit_panel', 'multi_buy_panel', 'discard_target_panel',
                               'permanent_keep_panel', 'followup_panel', 'whitelist_panel', 'blacklist_panel']:
                panel = getattr(self, panel_attr, None)
                if panel:
                    panel.refresh_data(self.all_generals_data)
            # v4.153★ 预设的『范围』下拉也跟着换(势力是跟着赛季变的)
            try:
                self._preset_refresh_scopes("dismiss")
            except Exception as e:
                self.log(f"[数据管理] 预设势力下拉刷新失败: {e}", level="WARN")
            self.log("[数据管理] 武将列表已刷新。")

    def _live_pump(self):
        """把油猴推上来的协议流水喂给 LIVE —— 所有"协议直读"功能唯一的数据源。

        只做"搬运+解析", 不碰任何 Tk 控件(UI 侧照旧自己 after() 去读 LIVE 缓存)。
        """
        from core.bridge import STATE
        from core.protocol import LIVE
        while not getattr(self, "_live_pump_stop", False):
            try:
                msgs = STATE.drain_net()
                if msgs:
                    LIVE.feed(msgs)
                    self._live_stat["msgs"] += len(msgs)
                    self._live_stat["last"] = time.time()
            except Exception as e:
                # ⚠️ 一条脏包不能让整局断流(那又变回"读不到对局"): 计数 + 前 3 次提示, 循环继续。
                self._live_stat["err"] += 1
                if self._live_stat["err"] <= 3:
                    print("[live_pump] 协议解析失败: %s" % e)
            time.sleep(0.2)

    def _on_tab_change(self, event):
        selected_tab = self.notebook.tab(self.notebook.select(), "text")
        if selected_tab == "宏录制":
            self.log("[UI] 切换到宏录制页")
            # 切换到宏录制页时自动刷新窗口列表
            self._macro_refresh_window_list()
        elif selected_tab == "赛季数据":
            # 切到赛季数据页时刷新概览 + 表格(取数都在子线程, 不会卡界面; 表行数还有缓存)
            self._season_refresh_overview()
            _tree = getattr(self, "season_tree", None)
            if _tree is not None and not _tree.get_children():
                self._season_show_table()
        elif selected_tab == "换肤":
            # v4.139★ 原来这里是「即时换肤」页 —— 现在两页并成『换肤』, 子页签切换由
            #   _build_skin_tab 里那个 <<NotebookTabChanged>> 负责; 这里只管"刚切到本页"。
            #   规则可能在『资源替换』页被改过, 进来重读一次再显示。
            _ib = getattr(self, "_inject_builder", None)
            if _ib is not None:
                try:
                    _ib.refresh()
                except Exception as e:
                    self.log("[UI] 即时换肤页刷新失败: %s" % e)
        elif selected_tab == "技能库":
            # v4.43★ 首次切进来才加载(不在启动时读那十几 MB 快照), 之后 user 自己点查询/换类别
            _stree = getattr(self, "skill_tree", None)
            if _stree is not None and not _stree.get_children():
                self._skill_show_table()
        elif selected_tab == "日志":
            # v4.51★ 日志只在"这一页可见"时重画(见 log()); 切进来强制刷一次, 保证看到最新
            self._update_log_display(force=True)
        elif selected_tab == "主公技能":
            # v4.46★ 首次切进来: 只读本地那份离线技能表补名字(不等游戏),
            #   "读本局"要用户自己点 —— 否则没开游戏时会白等一次 4 秒超时。
            _ltree = getattr(self, "lord_tree", None)
            if _ltree is not None and not _ltree.get_children():
                self._lord_show_hint()
        elif selected_tab == "资源替换":
            # v4.68★ 切进来时刷新一次规则与候选 URL(配置表/候选包可能已更新)
            if getattr(self, "_resource_builder", None) is not None:
                self._resource_builder._load_rules()
                self._resource_builder._load_candidates()
        # v4.51★ 独立判断(原来挂在 else 上): 现在『日志』等页也有自己的分支, 不能因为
        #   多一个 elif 就漏掉"离开宏录制页时关掉窗口选择器"。
        if selected_tab != "宏录制" and hasattr(self, 'window_picker_dialog') \
                and self.window_picker_dialog is not None and self.window_picker_dialog.winfo_exists():
            self.window_picker_dialog.destroy()
            self.window_picker_dialog = None
            self.log("[UI] 宏录制页切换，窗口选择器已关闭。")

    def _build_status_bar(self):
        """底部状态栏。
        v4.63★ 可折叠(用户原话"连接状态, 可以最小化"): 这行常驻窗口最底部, 而它 99% 的时间
        只有"已连接到游戏脚本"这一句 —— 白白占一行。现在收成一个 ▾ 小按钮, 点一下折起来只剩
        按钮本身, 再点展开。折叠状态写进 cfg["status_collapsed"], 重启助手后保持。
        """
        wrap = tk.Frame(self.root, bg=BG)
        wrap.pack(side=tk.BOTTOM, fill=tk.X)
        self._status_collapsed = bool((getattr(self, "cfg", None) or {}).get("status_collapsed"))
        self.status_toggle_btn = tk.Button(
            wrap, text=("▸ 状态" if self._status_collapsed else "▾ 状态"),
            command=self._toggle_status_bar, bg=BTN, fg=DIM, font=(FH, 8),
            bd=0, padx=6, cursor="hand2")
        self.status_toggle_btn.pack(side=tk.LEFT)
        self.status_bar_label = tk.Label(wrap, textvariable=self.status_bar_var, bd=1,
                                         relief=tk.SUNKEN, anchor=tk.W, bg=BG, fg=FG, font=(FH, 9))
        if not self._status_collapsed:
            self.status_bar_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

    def _toggle_status_bar(self):
        """折叠 / 展开底部状态栏(状态存盘, 下次启动照旧)。"""
        self._status_collapsed = not bool(getattr(self, "_status_collapsed", False))
        try:
            if self._status_collapsed:
                self.status_bar_label.pack_forget()
            else:
                # expand=True 才铺满整行 —— 否则只占文字宽度, 右边会露出一截窗口底色
                self.status_bar_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
            self.status_toggle_btn.config(text=("▸ 状态" if self._status_collapsed else "▾ 状态"))
        except Exception:
            _SIL.note("ui/app.py:2082", "if self._status_collapsed: self.status_bar_lab")
        try:
            self.cfg["status_collapsed"] = bool(self._status_collapsed)
            save_config(self.cfg)
        except Exception:
            _SIL.note("ui/app.py:2087", "self.cfg['status_collapsed'] = bool(self._stat")

    def _check_bridge_connection_loop(self):
        # v4.22★ 修复「状态栏恒显示已连接」:
        #   原判据是 `STATE.connected_ts is not None`, 但 BridgeState.connected_ts 的初始值是 0,
        #   而 Python 里 `0 is not None` 恒为 True → 状态栏永远显示"已连接"。
        #   连带更严重的后果: _lists_pushed_on_connect 在启动瞬间就被置 True,
        #   else 分支永远走不到 → 真正连上游戏后【也不会】自动推送列表。
        #   现改用统一的 STATE.get_connection_info()(与 remote_ops.connected() 同源),
        #   它自带 PID 假绿判定 + 5s/10s 超时判定, 能区分"等待浏览器数据/连接不稳定/断连"。
        try:
            info = STATE.get_connection_info()
        except Exception as e:  # 兜底: 取状态失败不能中断这个每秒循环
            info = {"connected": False, "reason": "error", "msg": f"连接状态获取失败: {e}"}

        clock_txt = ""
        try:
            clock_txt = self._real_clock_tick()
        except Exception:
            clock_txt = ""   # 倒计时只是附属信息, 绝不能让它把这个每秒循环弄挂

        if info.get("connected", False):
            self.status_bar_var.set("已连接到游戏脚本" + ("   |   " + clock_txt if clock_txt else ""))
            # v4.13★ 首次连接时自动推送所有列表到油猴端
            if not getattr(self, "_lists_pushed_on_connect", False):
                self._lists_pushed_on_connect = True
                # v4.101★ 以前这里写的是 after(500, ...) —— 也就是跑在【主线程】。而这个函数
                #   里是一条接一条的十几条同步请求(最坏合计约 26 秒), 于是"刚连上那一下界面
                #   死十几秒"。同一个函数在 4075 行本来就是当线程起的, 这里补成一致。
                threading.Thread(target=self._push_all_lists_to_script,
                                 name="push-lists", daemon=True).start()
        else:
            msg = info.get("msg") or ""
            if not msg or info.get("reason") == "waiting_first_push":
                msg = "未连接到游戏脚本 (请打开游戏页面)"
            self.status_bar_var.set(msg)
            self._lists_pushed_on_connect = False  # 断开后重置, 重连时再推
        self.root.after(1000, self._check_bridge_connection_loop)

    # ---------- v4.50★ 真实时间倒计时(不受游戏加速影响) ----------
    def _real_clock_tick(self):
        """每秒刷一次"真实时间" —— 顺便在【开打那一刻】往日志里留一条实测。

        用户原话: "加一个可以记录真实时间的倒计时…让用户知道什么时候开打", 且强调
        "不受游戏加速影响"。为什么不能直接看游戏里的倒计时: 游戏页面把 Laya.timer/
        Tween 整体加速(加速xN) —— 屏幕上"还剩几秒"跑得比真实秒快 N 倍, 用户盯着它算不出
        "真实还有多久开打"。所以数字全部来自 GameLive 的【墙上时钟】(见 protocol.real_clock()),
        本函数只负责把它摆到界面上 + 把"开打"这个时刻连同"距上一场打完的真实秒"写进日志。

        返回: 可直接拼进状态栏的文字; 取不到时返回空串(调用方原样跳过)。
        """
        clk = LIVE.real_clock()
        ph = clk.get("phase") or ""
        prev = getattr(self, "_rt_phase_seen", None)
        if ph != prev:
            self._rt_phase_seen = ph
            # 只在"真的开打"那条边沿记日志: 招募 → 战斗
            if prev == "recruit" and ph == "battle":
                d = clk.get("last_recruit_real")
                self.log("[实时] 开打 —— 距上一场打完 "
                         + (f"{d:.1f}s(墙上时钟, 不含游戏加速)" if d else "?(这一轮没测到)")
                         + f"; 历史样本中位数 {clk.get('sample') or '?'}s")
        return clk.get("text") or ""

    # ---------- 连接推送: 「映射只推本季」开关 (v4.46★) ----------
    def _script_map_season(self):
        """连接时推映射用哪个赛季、要不要过滤往季。返回 (season(str), only(bool))。

        用户原话: "我现在都不太了解那个赛季数据推送, 能不能只推送根据我查找到本赛季、
        或者我选的赛季进行推送。"

        这里的 season 取『赛季数据』页那个『赛季号』输入框(即"你查找到/选中的那个赛季"),
        留空则跟随 season_config.current_season()。开关本身存在 cfg["script_map_season_only"]。
        实际过滤发生在 csv_loader.chess_names_all(season) / chess_skill_map(season, True) ——
        只作用于【ChessConf.csv 出来】的那两份表; 武将表(tavern_chess.json)与锦囊表(cards.json)
        不带可靠赛季字段(条目里的 src 有 "uploaded_script" 这种来历不明项), 硬按赛季过滤会把
        名字整片删掉, 所以那两张表不受这个开关影响 —— 这点也在日志里说清楚。
        """
        only = bool((self.cfg or {}).get("script_map_season_only"))
        sea = str((self.cfg or {}).get("script_map_season_no") or "").strip()
        if not sea:
            # 输入框优先(用户此刻正看着的那个赛季), 其次配置, 最后 CURRENT_SEASON
            if hasattr(self, "season_no_var"):
                sea = str(self.season_no_var.get() or "").strip()
            if not sea:
                try:
                    from tools import season_config as SC
                    sea = str(SC.current_season())
                except Exception as e:
                    self.log(f"[连接] 取当前赛季失败, 映射按全量推: {e}", level="WARN")
                    return "", False
        return sea, only

    def _on_toggle_script_map_season(self):
        """『只推本季』勾选变化 → 存盘 + 立刻重推一次(不必断开重连才生效)。"""
        only = bool(self.script_map_season_var.get())
        sea = ""
        if hasattr(self, "season_no_var"):
            sea = str(self.season_no_var.get() or "").strip()
        self.cfg["script_map_season_only"] = only
        self.cfg["script_map_season_no"] = sea
        try:
            save_config(self.cfg)
        except Exception as e:
            self.log(f"[赛季] 保存『只推本季』设置失败(本次仍生效, 重启后会丢): {e}", level="WARN")
        self.log("[赛季] 连接映射推送范围 = "
                 + (f"只含赛季 S{sea or '当前赛季'}" if only else "全量(含往季)"))
        # 立刻生效: "要断开重连才生效"的开关最容易让人以为"没生效"
        if self.ops and self.ops.connected():
            threading.Thread(target=self._push_all_lists_to_script, daemon=True).start()

    def _push_all_lists_to_script(self):
        """v4.13★ 连接建立后自动推送所有列表到油猟端 (跟随征/永久保留/遣散目标/多选购买/自动招募)"""
        if not self.ops or not self.ops.connected():
            return
        try:
            self.log("[连接] 检测到油猟端连接, 自动推送所有列表...")
            pushed = 0
            # 0. v4.83★ 锦囊/随征【优先名单】: 连上就自动带过去, 不用用户再点一次"保存并推送"
            try:
                self._push_priority_lists(log=True)
            except Exception as e:
                self.log(f"[连接] 推送优先名单失败: {e}")
            # 0b. v4.85★ 节奏/延迟表: 连上就自动带过去, 不用再点一次「保存并推送」
            try:
                self._push_timing()
            except Exception as e:
                self.log(f"[连接] 推送节奏设置失败: {e}")
            # 0c. v4.145★ 【极速】开关: 默认就是"别让我等", 连上直接带过去
            try:
                self._push_fast_mode()
            except Exception as e:
                self.log(f"[连接] 推送极速开关失败: {e}")
            # 0d. v4.163★ 【内部零等待】开关: 同上, 连上就带过去(不然用户勾了、刷新页面又变回等)
            try:
                self._push_zero_wait()
            except Exception as e:
                self.log(f"[连接] 推送零等待开关失败: {e}")
            # 0e. v4.176★ 【跳过三连动画】: 同理, 连上就带过去(否则勾了、刷新游戏页又变回干等)
            try:
                self._push_skip_triple_ani(log=False)
            except Exception as e:
                self.log(f"[连接] 推送跳过三连动画开关失败: {e}")
            # 0e. v4.185★ 【自动随征】后台引擎: 连上就带过去(关了的话刷新页面也应保持关)
            try:
                self._push_auto_follow_up()
            except Exception as e:
                self.log(f"[连接] 推送自动随征开关失败: {e}")
            # 0g. v4.185★ 【全局操作倍率】: 连上就带过去(不然设了、刷新游戏页又变回 1x)
            try:
                _gs = float(self.toolbar_gspeed_var.get()) if hasattr(self, "toolbar_gspeed_var") else 1.0
                self.ops.update_global_speed(_gs)
            except Exception as e:
                self.log(f"[连接] 推送操作倍率失败: {e}")
            # 0f. v4.182★ 【性能】渲染闸门: 连上就带过去(关了某项渲染, 刷新页面也应保持关)
            try:
                self._push_render()
            except Exception as e:
                self.log(f"[连接] 推送渲染闸门失败: {e}")
            # 1. 随征卡ID
            if hasattr(self, "followup_panel"):
                ids = self.followup_panel.get_selected_ids()
                if ids:
                    self.ops.update_followup_ids(ids)
                    pushed += 1
            # 2. 永久保留武将
            #    v4.18★ 空列表也要推: 以前是 if ids: 才推 → 清空保留列表后油猴端仍留着旧的
            #    PERMANENT_KEEP_IDS_SET, 那些卡永远遣散不掉。
            if hasattr(self, "permanent_keep_panel"):
                try:
                    ids = self.permanent_keep_panel.get_selected_ids() or []
                    self.ops.update_permanent_keep_ids(ids)
                    pushed += 1
                except Exception as e:
                    self.log(f"[连接] 推送永久保留失败: {e}")
            # 3. 遣散目标武将
            #    v4.18★ 同上: 空列表也要推 → 否则用户删光目标后油猴端还按旧目标继续遣散。
            if hasattr(self, "discard_target_panel"):
                try:
                    ids = self.discard_target_panel.get_selected_ids() or []
                    self.ops.update_target_discard_ids(ids)
                    pushed += 1
                except Exception as e:
                    self.log(f"[连接] 推送遣散目标失败: {e}")
            # 4.5 自动遣散策略(星级/价值) —— 让后台自动遣散引擎按这套门槛(不改引擎开关)
            try:
                pol = self.cfg.get("dismiss_policy", {})
                if pol:
                    self.ops.update_auto_dismiss_policy(pol)
            except Exception as e:
                self.log(f"[连接] 推送自动遣散策略失败: {e}")
            # 4. 多选购买目标
            if hasattr(self, "multi_buy_panel"):
                ids = self.multi_buy_panel.get_selected_ids()
                if ids:
                    self._push_multi_buy_targets(ids)
                    pushed += 1
            # 5. 自动招募目标
            if hasattr(self, "recruit_panel"):
                ids = self.recruit_panel.get_selected_ids()
                if ids:
                    self.ops.update_auto_buy_config(ids)
                    pushed += 1
            # 6. 推送武将名字映射 (供 normalizeGoods 使用)
            # v4.33★ 并入 ChessConf.csv 的【全量 chessID → 名字】: 原来只推"自走棋可用武将"
            #   (tavern_chess.json, 300+ 条), 场上一旦出现表外的变体卡(如 20503211 董袭),
            #   页面名字解析就退化成裸 ID → 日志"20503211:1366@2", 按名字找卡也永远匹配不上。
            try:
                from core.id_map import get_all_tavern_generals_for_ui
                from core import csv_loader as _cl
                gens = get_all_tavern_generals_for_ui()
                id_map = {str(g.get("id", "")): g.get("name", "") for g in gens}
                sea, only = self._script_map_season()
                full = _cl.chess_names_all(sea if only else None)
                n_new = 0
                for k, v in full.items():
                    if v and id_map.get(k) != v:
                        if k not in id_map:
                            n_new += 1
                        id_map[k] = v
                self.ops.load_chess_name_map(id_map)
                pushed += 1
                if n_new:
                    self.log(f"[连接] 名称映射: 武将对局表 {len(gens)} 条 + CSV 补 {n_new} 条 = {len(id_map)} 条")
                if only:
                    self.log(f"[连接] 名称映射按你的设置【只含赛季 S{sea}】(『赛季数据』页勾了『只推本季』) —— "
                             f"往季变体卡(含被赛季更新挤掉的旧卡)在页面上的名字会退化成裸 ID; "
                             f"想要全量就去取消那个勾选")
            except Exception as e:
                self.log(f"[连接] 推送武将映射失败: {e}")
            # 6b. v4.31★ 推送锦囊名字映射 —— 原来【只推武将、不推锦囊】:
            #   remote_ops.load_spell_name_map 定义了却从没被调用过 → 油猴端 _spellNameCache 恒为空,
            #   手牌锦囊的名字解析不出来, 退化成裸 ID("现有锦囊: 310004(spellID=310004,...)"),
            #   按名字找锦囊/看日志都无从下手。
            try:
                from core.id_map import spell_name_map
                sp = spell_name_map()
                if sp:
                    self.ops.load_spell_name_map(sp)
                    pushed += 1
            except Exception as e:
                self.log(f"[连接] 推送锦囊映射失败: {e}")
            # 6b2. v4.150★ 补推【装备】+【技能】名字映射 —— v4.13 那批只做了武将 + 锦囊两张,
            #   装备一直没推。后果就是 3选1 的【装备档】里候选全是无名裸 ID:
            #   页面端 SgsSelect3._one() 只认 chessID / spellID, 认不出装备, 名字也无从解析。
            #   装备/技能 ID 都是配置表固定号(data/season/s<季>/TavernChessConfig.json 的
            #   ChessWeaponConf 371 条 / ChessSkillConf 2301 条), 离线就能查全, 直接推即可。
            for _label, _fn, _push in (
                    ("装备", "weapon_name_map", "load_weapon_name_map"),
                    ("技能", "skill_name_map", "load_skill_name_map"),
                    ("装备表", "equip_table", "load_equip_table")):
                try:
                    from core import id_map as _idm
                    _m = getattr(_idm, _fn)()
                    if _m:
                        getattr(self.ops, _push)(_m)
                        pushed += 1
                    else:
                        self.log(f"[连接] ⚠ {_label}名字表为空 —— 3选1 的{_label}档会显示裸 ID"
                                 f"(需要 data/season/s<赛季>/TavernChessConfig.json)")
                except Exception as e:
                    self.log(f"[连接] 推送{_label}映射失败: {e}")
            # 6c. v4.32★ 推送【棋子 → 技能ID】映射 (ChessConf.csv 的 ChessSkill 列)。
            #   实机问题: 丁奉的技能在表里写得清清楚楚(=[330411]), 但页面端三个来源全落空,
            #   日志只剩『卡上没解析到任何技能ID』→ 当先被当成"不用选人"→ 效果被服务器吞。
            #   这份映射就是第四个来源, 也是"赛季更新后重推即生效"的那一环。
            #   v4.39★ 同一份 payload 里再带上 picks = 需要手动选目标的技能ID —— 页面端够不到
            #   游戏运行时的 TavernChessConfiger, 只能靠这份离线表回答"这张卡要不要选人"。
            try:
                from core import csv_loader
                # v4.46★ 勾了『只推本季』时 ids/names2 也只留本季; 没勾【不能】顺手把 season 传进去 ——
                #   chess_skill_map(season=...) 会把 names 也按赛季过滤掉, 那就把 v4.32 的"全量兜底"
                #   给改坏了(默认 None 才是原来的行为)。
                _sea2, _only2 = self._script_map_season()
                sm = csv_loader.chess_skill_map(_sea2, True) if _only2 else csv_loader.chess_skill_map()
                if sm.get("n"):
                    self.ops.load_chess_skill_map(sm)
                    pushed += 1
                else:
                    self.log("[连接] 技能映射为空 —— ChessConf.csv 的 ChessSkill 列没解析出内容")
                if not sm.get("picks"):
                    # 选人判定会退回"一律不需要选人"(= 丁奉这类卡的当先被吞), 必须让用户看见
                    self.log("[连接] ⚠ 选人技能表(picks)为空 —— 丁奉这类『当先要选人』的卡会被判成"
                             "不需要选人。请到「赛季数据」重新抓一次配置(读的是 "
                             "data/season/s<赛季>/TavernChessConfig.json)")
            except Exception as e:
                self.log(f"[连接] 推送技能映射失败: {e}")
            # 7. v4.13★ 推送速度设置 (默认1.0x) + 加速Flag
            try:
                spd_str = self.toolbar_speed_var.get() if hasattr(self, "toolbar_speed_var") else "1.0"
                self.ops.set_game_speed(float(spd_str))
                # 推送 Flag
                flags_to_push = {}
                if hasattr(self, "toolbar_tween_var"):
                    flags_to_push["tweenScale"] = self.toolbar_tween_var.get()
                if hasattr(self, "toolbar_batch_var"):
                    flags_to_push["batchProtocol"] = self.toolbar_batch_var.get()
                if flags_to_push:
                    js_flags = json.dumps(flags_to_push)
                    self.ops.send_recv("eval", js=f"window.__sgsHookFlags=Object.assign(window.__sgsHookFlags||{{}}, {js_flags});", timeout=2.0)
                pushed += 1
            except Exception as e:
                self.log(f"[连接] 推送速度设置失败: {e}")
            # 8. v4.27★ 剧本 (有先后顺序的步骤流)
            try:
                # v4.109★ 剧本页搬到 ui/scenario_tab.py 后, scenario_scripts 存在
                #   builder 上了 —— 所以这里改成看 builder 在不在。剧本页从没打开过
                #   就没有 builder, 也就不推(和原来的行为一致)。
                if getattr(self, "_scenario_builder", None) is not None:
                    self.ops.push_scenarios(
                        self._scenario_collect(include_transport_fields=True), quiet=True)
                    pushed += 1
            except Exception as e:
                self.log(f"[连接] 推送剧本失败: {e}")
            self.log(f"[连接] 已推送 {pushed} 个列表到油猟端")
        except Exception as e:
            self.log(f"[连接] 自动推送列表异常: {e}", level="ERROR")

    def _check_version_loop(self):
        # v4.22★ 修复: 原实现把「取脚本版本」与「排空 HUD 按钮队列」塞在同一个 try 里,
        #   而 self.ops.get_version() 此前并不存在(BridgeOps 从未实现过它) → 每秒抛
        #   AttributeError, 于是:
        #     ① 工具栏 ver_lab 恒显示红色 "连接失败(...)" (即使脚本其实已经连上);
        #     ② 同一个 try 块里的 STATE.drain_hud_btns() 永远执行不到 →
        #        游戏内 HUD 按钮点了没反应。
        #   现在两者解耦: 版本获取单独兜底, HUD 排空无论上面成败都照常执行。
        def _check():
            try:
                ver_info = self.ops.get_version()
                v = (ver_info or {}).get("version")
                if v:
                    self.root.after(0, lambda: self._update_version_label(v))
                else:
                    self.root.after(0, lambda: self.ver_lab.config(text="脚本状态: 未连接", fg=RED))
            except Exception as e:
                self.root.after(0, lambda e_=e: self.ver_lab.config(text=f"脚本状态: 连接失败({e_})", fg=RED))

            # HUD 按钮排空 —— 与版本获取解耦, 即使上面抛错也必须照常处理
            try:
                for btn_id in STATE.drain_hud_btns():
                    self.root.after(0, lambda b=btn_id: self._on_hud_btn({"id": b}))
            except Exception as e:
                self.log(f"[HUD] 按钮队列处理异常: {e}", level="ERROR")

            # v4.54★ 游戏页快捷键事件(在游戏里按 Tab 切三连) → 写进『快捷键』页自己的日志。
            #   这里只 drain 队列(事件发生才有一条), 不是轮询上报, 所以不会刷屏。
            try:
                for hk in STATE.drain_hotkey_logs():
                    k, tx = "?", ""
                    try:
                        k = str((hk or {}).get("key", "?"))
                        tx = str((hk or {}).get("text", ""))
                    except Exception:
                        tx = str(hk)
                    self.root.after(0, lambda k_=k, t_=tx: self._hk_log(f"⌨ 游戏页 [{k_}] {t_}"))
            except Exception:
                _SIL.note("ui/app.py:2384", "for hk in STATE.drain_hotkey_logs(): k, tx = '")

            self.root.after(1000, _check)
        self.root.after(1000, _check)

    def _update_version_label(self, v):
        # v3.44+ 判定 "最新"
        def ver_num(s):
            try:
                parts = [int(x) for x in s.split(".") if x.isdigit()]
                while len(parts) < 3: parts.append(0)
                return tuple(parts[:3])
            except Exception:
                return (0,0,0)
        # v4.57★ 顺手把版本记在 self 上 —— 下发剧本时要拿它核对(见 _scenario_warn_if_script_old)。
        #   为什么不能只靠底部那个标签: "脚本旧"造成的现象全都长得像"剧本写错了"
        #   (新步骤被静默跳过 / 新条件退化成卡牌判定), 用户根本不会想到去看版本号。
        self.script_ver = str(v or "")
        self.script_ver_num = ver_num(self.script_ver)
        if self.script_ver_num >= MIN_SCRIPT_VER:
            self.ver_lab.config(text=f"游戏页脚本: v{v} ✓ 最新", fg=FG)
        else:
            need = "v" + ".".join(str(x) for x in MIN_SCRIPT_VER)
            self.ver_lab.config(text=f"游戏页脚本: v{v} → 旧版(需≥{need}), 点【⚡一键注入】更新", fg=WARN)

    # ---------------- 热键 (v3.44★ Windows 低级键盘钩子, 全局可屏蔽) ----------------
    def _hotkeys(self):
        # v4.161★ 多开: 本实例被指定为"不接管"时直接跳过(只装 UI 按钮用)。
        #   不跳过会怎样: 每个带界面的实例都装同一个全局钩子 → 按一次 F6,
        #   5 个实例同时对自己的浏览器执行"协议买目标"。
        if os.environ.get("SGS_NO_HOTKEYS", "").strip() in ("1", "true", "yes"):
            try:
                self.log("[热键] 本实例不接管全局热键(多开模式: 只让主实例接管, "
                         "免得一次按键所有号一起动)。需要时点顶部『热键: 已关闭』打开。")
            except Exception:
                pass
            return
        # 优先 hotkeys.py (WH_KEYBOARD_LL + 吞键), 回退 keyboard 库
        try:
            from core.hotkeys import HM
            self.hotkeys_manager = HM   # v3.44★ PIXEL宏录制需获取HM注册前置回调
            # 把 HM 跟 Tk root 绑定: 以便内部回调用 root.after 回到 UI 线程
            try:
                HM.root = self.root
                HM.log = self.log
            except Exception:
                _SIL.note("ui/app.py:2420", "HM.root = self.root")
            # 快捷包装: 输入框焦点守卫 + UI线程投递
            def bind(combo, method):
                def _cb():
                    w = None
                    try: w = self.root.focus_get()
                    except Exception: _SIL.note("ui/app.py:2426", "w = self.root.focus_get()")
                    if isinstance(w, (tk.Entry, tk.Text, ttk.Combobox)):
                        return
                    try: method()
                    except Exception as e:
                        self.log(f"[热键] {combo} 回调异常: {e}")
                HM.bind(combo, _cb)

            bind("f5", self.ocr_scan)
            bind("f6", self.a_protocol_buy)
            bind("f7", self.a_protocol_sell)
            bind("ctrl+r", self.a_refresh)
            bind("f10", self._macro_start)
            bind("f11", self._macro_stop)
            bind("f8",  self._macro_play_dispatch)   # v3.50★ _macro_play 先走递归保护(3层锁)再调 _macro_play_dispatch,避免重名覆盖
            bind("pause", self._macro_abort)
            bind("f9", self.ocr_stop)
            bind("space", self.a_skip)
            bind("ctrl+alt+b", self.a_buyall)
            bind("ctrl+alt+d", self.a_deploy)
            bind("ctrl+alt+x", self.a_sell_idle)
            # v4.13★ 全遣散手牌 (Shift+5) — 跳过永久保留列表
            bind("shift+5", self._trigger_discard_all_hand)
            HM.start()
            # HM.start() 内部会输出最终状态(成功 / 失败 RegisterHotKey兜底 / 非Windows).
            # 这里仍注册 keyboard 库作为"最后兜底": 即使 WH_KEYBOARD_LL + RegisterHotKey 都失败,
            # 只要 Python 进程有输入焦点/权限，依然能响应 Fn 键.
            # 注: 两套(钩子+keyboard)如果同时生效, 焦点守卫会挡住重复触发(输入框场景不动作两次)
        except Exception as e:
            self.log(f"[热键] hotkeys.py 不可用, 自动回退keyboard库: {e}")
        # ---- 回退: keyboard库 ----
        try:
            import keyboard
            keyboard.add_hotkey("space", lambda: self.root.after(0, self._guard(self.a_skip)))
            keyboard.add_hotkey("ctrl+alt+b", lambda: self.root.after(0, self._guard(self.a_buyall)))
            keyboard.add_hotkey("ctrl+alt+d", lambda: self.root.after(0, self._guard(self.a_deploy)))
            keyboard.add_hotkey("ctrl+alt+x", lambda: self.root.after(0, self._guard(self.a_sell_idle)))
            keyboard.add_hotkey("ctrl+r", lambda: self.root.after(0, self._guard(self.a_refresh)))
            keyboard.add_hotkey("f5", lambda: self.root.after(0, self._guard(self.ocr_scan)))
            keyboard.add_hotkey("f6", lambda: self.root.after(0, self._guard(self.a_protocol_buy)))
            keyboard.add_hotkey("f7", lambda: self.root.after(0, self._guard(self.a_protocol_sell)))
            keyboard.add_hotkey("f9", lambda: self.root.after(0, self._guard(self.ocr_stop)))
            keyboard.add_hotkey("f10", lambda: self.root.after(0, self._guard(self._macro_start)))
            keyboard.add_hotkey("f11", lambda: self.root.after(0, self._guard(self._macro_stop)))
            keyboard.add_hotkey("f8",  lambda: self.root.after(0, self._guard(self._macro_play_dispatch)))
            try:
                keyboard.add_hotkey("pause", lambda: self.root.after(0, self._guard(self._macro_abort)))
            except Exception:
                _SIL.note("ui/app.py:2474", "keyboard.add_hotkey('pause', lambda: self.root")
            # v4.13★ 全遣散手牌 (Shift+5)
            keyboard.add_hotkey("shift+5", lambda: self.root.after(0, self._guard(self._trigger_discard_all_hand)))
        except Exception as e:
            self.log(f"[热键] keyboard库也不可用: {e}")

    # ---------- v3.44★ 新动作入口 (协议优先) ----------
    def a_protocol_buy(self):
        self._rec_ui("protocol_buy")
        self._last_fb = "F6: 协议买目标…"
        self._push_hud_bar()
        def _task():
            try:
                # 1) 先 DOM/协议 权威同步 (rescan_areas)
                self.ops.rescan_areas(mode="dom_then_proto", timeout=3.5)
                # 2) 拿白/黑名单
                # v4.21★ 修复: 原来读的是 self.hero_picker, 但那个东西从来没被创建过
                #   (本文件里创建它的两行是注释状态) → hasattr 恒为 False → 白名单恒为 [] →
                #   protocol_buy_targets 直接返回全 0(就是日志里那行全 0)。
                #   真正的白/黑名单在「数据管理」tab 的 DualListPanel 上。
                wl, bl = [], []
                if hasattr(self, "whitelist_panel") and self.whitelist_panel:
                    try: wl = self.whitelist_panel.get_selected_ids() or []
                    except Exception as e: self.log(f"[F6/买目标] 读取白名单失败: {e}")
                if hasattr(self, "blacklist_panel") and self.blacklist_panel:
                    try: bl = self.blacklist_panel.get_selected_ids() or []
                    except Exception as e: self.log(f"[F6/买目标] 读取黑名单失败: {e}")
                if not wl:
                    self.root.after(0, lambda: self.log(
                        "[F6/买目标] 白名单为空 —— 请到『数据管理 → 白名单(当前赛季)』添加要买的武将并保存"))
                summary = self.ops.protocol_buy_targets(whitelist=wl, blacklist=bl, budget_floor=2, limit=8)
                self._last_fb = f"买: ok={summary.get('bought',0)}  fail={summary.get('failed',0)}  save={summary.get('gold_saved',0)}"
                self.root.after(0, lambda: self.log(f"[F6/买目标] {summary}"))
            except Exception as e:
                self.root.after(0, lambda e=e: self.log(f"[F6/买目标] 失败: {e}"))
                self._last_fb = f"买失败: {e}"
            self._push_hud_bar()
        threading.Thread(target=_task, daemon=True).start()

    def a_protocol_sell(self):
        self._rec_ui("protocol_sell")
        self._last_fb = "F7: 协议遣散…"
        self._push_hud_bar()
        def _task():
            try:
                self.ops.rescan_areas(mode="dom_then_proto", timeout=3.5)
                # v4.17★ F7 = 「执行策略遣散」, 语义必须和 UI 按钮 / Shift+4 一致:
                #   按【遣散目标武将列表】(data/lists/discard_target.json → 油猴端 TARGET_DISCARD_CHESS_IDS_SET) 遣散手牌。
                #   旧实现让热键直接走 dismiss_areas_policy(备战区 + 名字黑名单), 根本不读遣散目标列表,
                #   于是"配了遣散目标按 F7 毫无反应"; 而且黑名单为空时它等于什么都没做。
                did = False
                try:
                    if self.ops and self.ops.connected():
                        did = bool(self.ops.trigger_discard_target_chess_ids())
                except Exception as e:
                    self.log(f"[F7/遣散] 遣散目标链路异常, 回退备战区策略: {e}")
                if did:
                    self._last_fb = "遣散: 已按遣散目标执行"
                    self.root.after(0, lambda: self.log("[F7/遣散] 已按【遣散目标列表】执行(与 Shift+4 同链路)"))
                else:
                    # 回退: 备战区 + 黑名单名字匹配(没配遣散目标时这条路仍有意义)
                    bl = []
                    # v4.23★ 清理: 这里原来先试 `self.hero_picker.get_blacklist()`, 但该属性
                    #   从未被创建(创建它的那行一直是注释) → hasattr 恒为 False, 分支永远进不去。
                    #   黑名单的唯一真源就是「数据管理」页签的 blacklist_panel。
                    # 从数据管理 tab 的 blacklist_panel 读取
                    if hasattr(self, "blacklist_panel"):
                        try:
                            bl_ids = self.blacklist_panel.get_selected_ids()
                            if bl_ids:
                                from core import id_map as _idmap_mod
                                # blacklist_panel 存的是 chessID, 转成名字匹配
                                for cid in bl_ids:
                                    try:
                                        nm = _idmap_mod.name_of(int(cid), chess_hint=True)
                                        if nm: bl.append(nm)
                                    except Exception: _SIL.note("ui/app.py:2550", "nm = _idmap_mod.name_of(int(cid), chess_hint=T")
                        except Exception: _SIL.note("ui/app.py:2551", "bl_ids = self.blacklist_panel.get_selected_ids")
                    # v4.101★ 兜底保护: 现在的黑名单有 3346 条(=全量武将)。拿这么一份名单去
                    #   "按名字遣散备战区", 等于把备战区【整排清空】 —— 几乎不可能是用户想要的。
                    #   名单过大时本次不使用它, 只警告(遣散请用专门的『遣散目标』列表, 33 个)。
                    if len(bl) > 500:
                        self.log(f"[F7/遣散] 黑名单有 {len(bl)} 个名字 —— 用它按名字遣散会把备战区清空, "
                                 f"本次【不使用】; 请到『数据管理 → 黑名单』精简, 策略遣散请用『遣散目标』列表。",
                                 level="WARN")
                        bl = []
                    summary = self.ops.dismiss_areas_policy(
                        areas=["bench"],
                        blacklist_names=bl,
                    )
                    self._last_fb = f"遣散: ok={len(summary.get('dismissed',[]))}  skip={len(summary.get('skipped',[]))}"
                    self.root.after(0, lambda s=summary: self.log(f"[F7/遣散] {s}"))
                # 遣散会让手牌【减少】, 而手牌哨兵只盯"变多" → 这里必须主动补刷一次
                try:
                    self.ops.refresh_hand_view()
                except Exception as e:
                    self.log(f"[F7/遣散] 刷新手牌区失败: {e}")
            except Exception as e:
                self.root.after(0, lambda e=e: self.log(f"[F7/遣散] 失败: {e}"))
                self._last_fb = f"遣散失败: {e}"
            self._push_hud_bar()
        threading.Thread(target=_task, daemon=True).start()

    # ---------- v3.44★ 全局热键对应的宏入口 (F10/F11/F8/Pause) ----------
    def _ensure_macro_mode(self):
        """保证 macro_mode_var 存在 (用户可能还没切到【宏录制】页)"""
        if not getattr(self, "macro_mode_var", None):
            # v3.49★默认PIXEL模式(键鼠物理录制) · UI可切回SEMANTIC
            self.macro_mode_var = tk.StringVar(value="PIXEL")
        return self.macro_mode_var.get()

    def _ensure_pixel_listeners(self):
        """PIXEL 模式: 启动 HM 前置回调 + mouse/key 轮询监听线程(只启动一次)"""
        try:
            from core.recorder import MACRO, MacroRecorder
            hm_ref = getattr(self, "hotkeys_manager", None)
            get_hm = None
            if hm_ref is not None:
                get_hm = lambda: hm_ref
            MacroRecorder.ensure_pixel_listeners(
                get_hotkeys_manager=get_hm,
                get_app=lambda: self,
                log=lambda m: self.log(m))
        except Exception as e:
            import traceback as _tb
            self.log(f"[宏] PIXEL监听初始化失败(不影响SEMANTIC模式): {e}\n{_tb.format_exc()[-1000:]}")

    def _macro_start(self):
        """F10: 开始录制 (按UI上的 macro_name + macro_mode)"""
        try:
            from core.recorder import MACRO
            mode = self._ensure_macro_mode().upper()
            if mode == "PIXEL":
                self._ensure_pixel_listeners()
            
            # v3.50★ 同步宏录制捕获范围和目标窗口
            self._sync_scope_to_macro()

            name = (self.macro_name.get() or "").strip()
            if not name:
                name = MACRO._default_name()
                self.macro_name.set(name)
            
            # 检查是否有同名文件, 提醒用户是否覆盖
            if os.path.exists(MACRO.path_of(name)):
                if not messagebox.askyesno("确认", f"宏文件 '{name}.json' 已存在, 是否覆盖?"):
                    self.log("[宏] 用户取消录制, 宏文件未覆盖.")
                    return

            res = MACRO.start(name=name, mode=mode)
            self.macro_record_status.config(text=f"录制中: {res} ({mode})", fg=GOLD)
            self._last_fb = f"录制中 {name} ({mode})"
            self._push_hud_bar()
            self.log(f"[宏] 开始录制: {res}  模式={mode}")
        except Exception as e:
            self.log(f"[宏] 开始录制失败: {e}")
            self.macro_record_status.config(text=f"录制失败: {e}", fg=RED)

    def _macro_stop(self):
        """F11: 停止录制 (并保存到宏文件)"""
        try:
            from core.recorder import MACRO
            res = MACRO.stop(save=True)
            nm = res.get("name", "")
            cnt = res.get("count", 0)
            p = res.get("path", "")
            self.macro_record_status.config(text=f"已停止: {nm} 事件{cnt}条", fg=FG)
            self._last_fb = f"停止录制 {nm} 事件{cnt}"
            self._push_hud_bar()
            # 尝试选中刚录制的宏
            if p and not p.startswith("SAVE_FAIL:"):
                try:
                    self.macro_combo_files.set(os.path.basename(p))
                except Exception as e:
                    self.log(f"[宏] 停止录制: {nm} 事件={cnt} 保存={p} (选中文本失败: {e})")
            else:
                self.log(f"[宏] 停止录制: {nm} 事件={cnt} 保存={p}")
            self._macro_refresh_events() # 刷新事件列表
        except Exception as e:
            self.log(f"[宏] 停止录制失败: {e}")
            self.macro_record_status.config(text=f"停止失败: {e}", fg=RED)

    def _macro_abort(self):
        """Pause: 停止回放 (兼容 recorder 里 stop_playback + 老 abort API)"""
        try:
            from core.recorder import MACRO
            # 都调一次:
            try: MACRO.stop_playback()
            except Exception: _SIL.note("ui/app.py:2662", "MACRO.stop_playback()")
            try: MACRO.abort()
            except Exception: _SIL.note("ui/app.py:2664", "MACRO.abort()")
            self._last_fb = "宏已中断"
            self._push_hud_bar()
            self.log("[宏] 已请求中断回放")
        except Exception as e:
            self.log(f"[宏] 中断失败: {e}")

    def _macro_on_record_event(self, event):
        # v4.101★ 【录制最卡】的第一元凶就在这里: 以前【每来一条事件】就 root.after(0) 一次,
        #   而那一次是【全表重建】(delete 掉全部行 + insert 全部 N 行)。鼠标移动 30ms 一条
        #   → 约 33 条/秒 = 每秒 33 次全表重建; 录到 2000 条时就是【每秒 6.6 万次】Tk 行操作,
        #   全在主线程 —— 界面直接卡死, 而且【录得越久越卡】(O(N) 随事件数线性恶化)。
        #   现在照抄同文件 log() 已经做对的做法: 只标脏, 400ms 合并成一次刷新。
        self._macro_events_dirty = True
        try:
            if getattr(self, "_macro_refresh_job", None):
                return          # 已经排了一次, 等它到点即可
            self._macro_refresh_job = self.root.after(400, self._macro_flush_events)
        except Exception as e:
            # 排不上(界面已关)就丢一帧, 但要留个痕 —— 静默吞掉的话现象是"列表不动了"
            sys.stderr.write(f"[宏] 排事件刷新任务失败: {e}\n")

    def _macro_tab_visible(self):
        """现在是不是停在「宏录制」这一页(看不到就不必重画列表)。"""
        try:
            nb = getattr(self, "notebook", None) or getattr(self, "nb", None)
            if nb is None:
                return True
            return nb.tab(nb.select(), "text") == "宏录制"
        except Exception as e:
            self.log(f"[宏] 判断当前页失败(按可见处理): {e}", level="WARN")
            return True

    def _macro_flush_events(self):
        self._macro_refresh_job = None
        if not getattr(self, "_macro_events_dirty", False):
            return
        # v4.182★ 【性能】页勾了「暂停助手端统计重绘」就整段跳过(脏标记留着不消费), 但链必须
        #   续上 —— 否则取消勾选后没人再排定时器, 事件表会一直停在旧画面上(静默"不刷新了")。
        if self._render_paused("stats"):
            try:
                self._macro_refresh_job = self.root.after(5000, self._macro_flush_events)
            except Exception as e:
                self.log(f"[宏] 暂停重绘期间续刷新任务失败: {e}", level="WARN")
            return
        # v4.101★ 看不到这一页就【不重画】(但把"脏"留着, 切回来时会补上)。以前切到别的页
        #   继续录, 重建照跑 —— 用户的感觉是"我一录, 整个程序都卡"(同文件日志页、机制页
        #   都做了这个判断, 唯独这里漏了)。
        if not self._macro_tab_visible():
            # 让这条链活着(5 秒看一眼), 这样切回宏录制页最多 5 秒就会自动补一次刷新 ——
            #   否则"停止录制后切过去"可能停在旧画面上。
            try:
                self._macro_refresh_job = self.root.after(5000, self._macro_flush_events)
            except Exception as e:
                self.log(f"[宏] 续刷新任务失败: {e}", level="WARN")
            return
        self._macro_events_dirty = False
        try:
            self._macro_refresh_events()
        except Exception as e:
            self.log(f"[宏] 刷新事件列表失败: {e}")

    def _macro_refresh_events(self):
        # 刷新事件列表显示(由 _macro_flush_events 每 400ms 调一次, 不再是每来一条事件一次)
        if not hasattr(self, "macro_event_tree"): return
        # v4.182★ 性能页「暂停统计/录制重绘」: 勾了就不重画事件表。
        #   取数走 snapshot_since(只拷上次画过之后新增的那段), 所以暂停期间攒下的行
        #   取消勾选后会一次性补齐, 不会丢事件。
        if self._render_paused("stats"): return
        # v4.101★ 增量 + 只拷增量:
        #   · 取数走 recorder.snapshot_since —— 只拷"上次画过之后新增的那段"。以前是整份
        #     list(MACRO._events), O(N), 录到几万条时一次拷贝几万个对象, 还占着录制线程要用的
        #     同一把锁(两边互相拖慢, 这就是"录得越久越卡"的残留根因)。
        #   · 已经画在树上的行不动, 只追加新的; 只有重录/清空(总数变少)才整表重画。
        drawn = int(getattr(self, "_macro_drawn", 0) or 0)
        try:
            events, total_events, _kinds = MACRO.snapshot_since(drawn)
        except Exception as e:
            self.log(f"[宏] 取事件快照失败: {e}", level="WARN")
            return
        if total_events < drawn:
            self.macro_event_tree.delete(*self.macro_event_tree.get_children())
            drawn = 0
            try:
                events, total_events, _kinds = MACRO.snapshot_tail(1200)
            except Exception as e:
                self.log(f"[宏] 取事件快照失败: {e}", level="WARN")
                return
            drawn = total_events - len(events)
        
        # v4.101★ 计数改成【跨刷新累加】: 现在只遍历"新增的那一段", 用局部计数会把已经画过的
        #   那部分漏掉; 整表重画时归零重算。
        if drawn == 0 or getattr(self, "_macro_counts", None) is None:
            self._macro_counts = {"km": 0, "move": 0, "click": 0, "key": 0, "sem": 0}
        cnt = self._macro_counts

        for i, ev in enumerate(events):
            kind = ev.get("kind", "")
            data = ev.get("data", {})
            icon = ""
            desc = ""

            if kind == "key":
                vk_name = data.get("vk_name", f"VK{data.get('vk')}")
                icon = "⌨"
                desc = f"键 {vk_name} {'按下' if data.get('down') else '抬起'}"
                cnt["km"] += 1
                cnt["key"] += 1
            elif kind == "mouse_press":
                btn = data.get("button", "left")
                icon = "🖱"
                desc = f"鼠标 {btn} {'按下' if data.get('down') else '抬起'} @ ({data.get('x')},{data.get('y')})"
                cnt["km"] += 1
                cnt["click"] += 1
            elif kind == "mouse_move":
                icon = "🖱"
                desc = f"鼠标移动 @ ({data.get('x')},{data.get('y')})"
                cnt["km"] += 1
                cnt["move"] += 1
            elif kind == "mouse_scroll":
                icon = "🖱"
                desc = f"滚轮 {data.get('delta')} @ ({data.get('x')},{data.get('y')})"
                cnt["km"] += 1          # 滚轮也算"键鼠"这一类
            elif kind == "ui":
                icon = "⚙"
                desc = f"UI操作: {data.get('action')}"
                cnt["sem"] += 1
            elif kind == "psend":
                icon = "⚡"
                desc = f"协议: {data.get('name')} {data.get('args')}"
                cnt["sem"] += 1
            elif kind == "hudbtn":
                icon = "👆"
                desc = f"小抄按钮: {data.get('btn_id')}"
                cnt["sem"] += 1
            elif kind == "wait":
                icon = "⏳"
                desc = f"等待 {data.get('seconds', 0):.2f} 秒"
                cnt["sem"] += 1
            elif kind == "loop":
                icon = "🔁"
                desc = f"循环 {data.get('count', 0)} 次"
                cnt["sem"] += 1
            else:
                icon = "❓"
                desc = f"未知事件: {kind}"
            
            self.macro_event_tree.insert("", "end", values=(drawn + i + 1, icon, desc))
        self._macro_drawn = total_events
        # 下面那段诊断头还在用这几个名字, 从累加器里倒回来
        pixel_key_mouse_count = cnt["km"]
        pixel_move_count = cnt["move"]
        pixel_click_count = cnt["click"]
        pixel_key_count = cnt["key"]
        semantic_count = cnt["sem"]

        # Update diagnostic header
        diag_text = f"[诊断] 共{total_events}条 | PIXEL键鼠={pixel_key_mouse_count}"
        if pixel_move_count > 0: diag_text += f"(移动{pixel_move_count}"
        if pixel_click_count > 0: diag_text += f"/点击{pixel_click_count}"
        if pixel_key_count > 0: diag_text += f"/按键{pixel_key_count}"
        if pixel_key_mouse_count > 0: diag_text += ")"
        if semantic_count > 0: diag_text += f" | 语义={semantic_count}"

        self.macro_diag_label.config(text=diag_text)

        if total_events == 0 and MACRO.mode == "pixel":
            self.macro_event_tree.insert("", "end", values=(1, "💡", "PIXEL模式下, 请先点击[开始录制] (F10), 再回到游戏里移动鼠标/点击/按键。"))
            self.macro_event_tree.insert("", "end", values=(2, "💡", "F11停止录制, F8回放。"))

    def _macro_refresh_window_list(self):
        # 这个方法已经废弃, 切换到新的 WindowPickerDialog
        # 兼容旧代码, 不做任何事情
        pass

    def _macro_use_foreground_window(self):
        # 这个方法已经废弃, 逻辑转移到 WindowPickerDialog
        pass

    def _macro_pick_window_cursor(self):
        # 这个方法已经废弃, 逻辑转移到 WindowPickerDialog
        pass
        
    def _sync_scope_to_macro(self):
        """每次F10/开始录制按钮 都同步一次scope/hwnd到MACRO对象(避免切了选择没生效)."""
        try:
            from core.recorder import MACRO
            scope = self.macro_capture_scope.get() if getattr(self, "macro_capture_scope", None) else "GLOBAL"
            sel = getattr(self, "macro_capture_hwnd_sel", None)
            if scope == "WINDOW" and sel is None:
                self.log("[宏] G·仅窗口模式: 还没选目标窗口→自动降级为A·全局捕获(否则会录0条,避免你以为在录其实没录).")
                scope = "GLOBAL"
            hwnd = sel[0] if sel and scope == "WINDOW" else None
            win_title = sel[1] if sel else ""
            save_hwnd = bool(self.macro_capture_savehwnd_var.get()) if getattr(self, "macro_capture_savehwnd_var", None) else True
            MACRO.set_capture_scope(scope, hwnd=hwnd, win_title=win_title, save_hwnd=save_hwnd)
        except Exception as e:
            self.log(f"[宏] 同步scope到宏失败: {e}")

    def _macro_delete_selected(self):
        from core.recorder import MACRO
        nm = (self.macro_combo_files.get() or "").strip()
        if not nm:
            self.log("[宏] 先选一个要删的宏")
            return
        if MACRO.delete(nm):
            self.log(f"[宏] 已删除 {nm}")
            self._macro_reload_list()
            try:
                self.macro_combo_files.set("")
            except Exception:
                _SIL.note("ui/app.py:2863", "self.macro_combo_files.set('')")

    def _macro_play_full_impl(self):
        """宏Tab页 ▶回放按钮 完整实现(worker线程/进度/错误处理).
        v3.50★ 方法重命名: 之前叫_macro_play 和全局F8入口重名了,Python class 后者覆盖前者=完全没反应!"""
        from core.recorder import MACRO
        if MACRO.is_playing():
            self.log("[宏] 已有回放正在跑——先点■停止")
            return
        nm = (self.macro_combo_files.get() or "").strip()
        if not nm:
            with MACRO._lock:
                evs = list(MACRO._events)
            if not evs:
                self.log("[宏] 请先选要回放的宏(下拉里选 或 先录/插入事件)")
                return
            obj = {"name": "__MEMORY__", "events": evs}
        else:
            try:
                obj = MACRO.load(nm)
            except FileNotFoundError:
                self.log(f"[宏] {nm} 文件不存在")
                return
            except Exception as e:
                self.log(f"[宏] {nm} 读取失败: {e}")
                return
        speed = float(self.macro_speed.get() or 1.0)
        loop = max(1, int(self.macro_loop.get() or 1))
        # v3.50★ GLOBAL模式也能自动切前台: 当前下拉选中的窗口hwnd传过去
        sel = getattr(self, "macro_capture_hwnd_sel", None)
        extra_hwnd = int(sel[0]) if sel and sel[0] else None
        self.btn_macro_play.config(state="disabled")
        self.macro_play_status.config(text=f"回放中: {obj.get('name', nm)}  速度x{speed}  循环{loop}次 …", fg=GOLD)
        self.log(f"[宏] 回放启动 {nm or '内存宏'} x{speed} 循环{loop}次  事件{len(obj['events'])}条"
                 + (f"  目标窗口hwnd={extra_hwnd}" if extra_hwnd else ""))
        def worker():
            try:
                played, ok, fail = MACRO.playback(obj, speed=speed, loop=loop,
                                                  extra_foreground_hwnd=extra_hwnd)
                self.root.after(0, lambda: (
                    self.btn_macro_play.config(state="normal"),
                    self.macro_play_status.config(
                        text=f"回放完成: 共{played}事件  成功{ok}  失败{fail}",
                        fg=(GOLD if fail == 0 else RED)),
                    self.log(f"[宏] 回放完成: {played}事件 成功{ok} 失败{fail}")))
            except Exception as e:
                self.root.after(0, lambda: (
                    self.btn_macro_play.config(state="normal"),
                    self.macro_play_status.config(text=f"回放异常: {e}", fg=RED),
                    self.log(f"[宏] 回放异常 {e.__class__.__name__}:{e}")))
        threading.Thread(target=worker, daemon=True).start()
    
    def _macro_play_dispatch(self):
        """统一回放分发: 按钮▶、F8热键都走这里 → 优先Tab页完整实现(含UI状态/错误处理).
        解决方法名冲突大Bug: 之前两个def _macro_play() 后者覆盖前者,F8/按钮全走了简化版."""
        self._macro_play_full_impl()

    def _macro_stop_play(self):
        from core.recorder import MACRO
        MACRO.stop_playback()
        self.log("[宏] 已请求停止回放(等待最后1步完成后退出)")

    def _macro_on_progress(self, idx, total, label_or_speed=None, speed=None):
        """双版本兼容:
        - playback 回调: playback(self, obj, speed=..., loop=..., on_progress=on_progress(idx, total))
        - 旧版 on_play_progress 默认签名(idx, total, speed)
        """
        def _u():
            # macro_play_status (Tab 版)
            try:
                s = self.macro_play_status
                if isinstance(label_or_speed, str):
                    s.config(text=f"回放: {idx}/{total}  {label_or_speed}", fg=GOLD)
                elif speed is not None:
                    s.config(text=f"回放: {idx}/{total}  速度x{speed}", fg=GOLD)
                else:
                    s.config(text=f"回放: {idx}/{total}", fg=GOLD)
            except Exception:
                _SIL.note("ui/app.py:2941", "s = self.macro_play_status")
        self.root.after(0, _u)

    def _guard(self, fn):
        def _guarded_fn(*args, **kwargs):
            try:
                fn(*args, **kwargs)
            except Exception as e:
                self.log(f"[Guard] 函数 {fn.__name__} 异常: {e}", level="ERROR")
        return _guarded_fn

    # ---------- 快速预设 (v4.153★ 招募 / 遣散 两个 tab 共用) ----------
    #   来源(用户原话): 「太史慈吴遣散就是除了孙尚香、太史慈都遣散、剩下的吴」
    #                    「找黄盖就是除了黄盖都遣散」
    #                    「招募就是提前写好几个祢衡、丁奉、陈珪 / 孙策、陈珪」
    #   这些话在脑子里是一句, 落到界面上是每局重填十几下 —— 预设把它存下来,
    #   还要能【自己写/改/删】(存当前 / 编辑 / 删除 三个按钮就是干这个的)。
    def _build_preset_bar(self, parent, kind):
        """建一条『快速预设』工具条。

        kind: "recruit"=招募目标名单 | "dismiss"=按势力+保留名单算出来的遣散名单
        """
        from core import list_presets as LP
        is_rec = (kind == LP.RECRUIT)
        box = tk.LabelFrame(parent, text=" 快速预设 ", bg=BG, fg=GOLD, bd=1,
                            relief="groove", font=(FH, 10, "bold"))
        box.pack(fill="x", pady=(0, 6))

        ui = {"kind": kind,
              "panel_attr": "recruit_panel" if is_rec else "discard_target_panel"}
        # 先挂上去再往里填控件: 下面建『范围』下拉时就要调 _preset_refresh_scopes,
        # 它按 kind 从这里取登记表 —— 挂晚了那次刷新会落空, 下拉永远只剩"全部势力"。
        self._preset_ui[kind] = ui
        row = tk.Frame(box, bg=BG)
        row.pack(fill="x", padx=6, pady=(5, 3))
        tk.Label(row, text="预设:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left")
        var = tk.StringVar()
        combo = ttk.Combobox(row, textvariable=var, values=[], width=20,
                             state="readonly", font=(FH, 9))
        combo.pack(side="left", padx=(3, 6))
        combo.bind("<<ComboboxSelected>>", lambda e, k=kind: self._preset_update_summary(k))
        ui["var"], ui["combo"] = var, combo
        # 每个按钮都过 self._guard: Tk 回调里抛异常只会往 stderr 打一串,
        #   界面上完全静默 —— 用户以为"点了没反应"。包一层至少落到日志区。
        tk.Button(row, text="▶ 应用", command=self._guard(lambda k=kind: self._preset_apply(k)),
                  bg=GREEN, fg=FG, font=(FH, 9, "bold"), padx=8, cursor="hand2").pack(side="left", padx=(0, 3))
        tk.Button(row, text="存当前", command=self._guard(lambda k=kind: self._preset_save_current(k)),
                  bg=BTN, fg=BLUE, font=(FH, 9), padx=6, cursor="hand2").pack(side="left", padx=(0, 3))
        tk.Button(row, text="编辑", command=self._guard(lambda k=kind: self._preset_edit(k)),
                  bg=BTN, fg=FG, font=(FH, 9), padx=6, cursor="hand2").pack(side="left", padx=(0, 3))
        tk.Button(row, text="删除", command=self._guard(lambda k=kind: self._preset_delete(k)),
                  bg=BTN, fg=RED, font=(FH, 9), padx=6, cursor="hand2").pack(side="left")
        tk.Label(row, text="(应用 = 替换右侧整份名单并自动保存推送)",
                 bg=BG, fg=DIM, font=(FH, 8)).pack(side="right")

        summary = tk.Label(box, text="", bg=BG, fg=DIM, font=(FH, 8), anchor="w", justify="left")
        summary.pack(fill="x", padx=6)
        ui["summary"] = summary

        # 编辑区 —— 平时收起, 点『存当前 / 编辑』才展开, 不占地方
        ed = tk.Frame(box, bg=BG)
        ui["editor"] = ed
        ed1 = tk.Frame(ed, bg=BG)
        ed1.pack(fill="x", padx=6, pady=(3, 0))
        tk.Label(ed1, text="名称:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left")
        name_var = tk.StringVar()
        tk.Entry(ed1, textvariable=name_var, bg=BTN, fg=FG, insertbackground=FG,
                 font=(FH, 9), width=16).pack(side="left", padx=(3, 8))
        ui["name_var"] = name_var
        items_var, scope_var, keep_var = (tk.StringVar(), tk.StringVar(value=LP.ALL_SCOPE),
                                          tk.StringVar())
        if is_rec:
            tk.Label(ed1, text="招募目标:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left")
            tk.Entry(ed1, textvariable=items_var, bg=BTN, fg=FG, insertbackground=FG,
                     font=(FH, 9)).pack(side="left", fill="x", expand=True)
            ui["items_var"] = items_var
        else:
            tk.Label(ed1, text="范围:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left")
            ui["scope_cb"] = ttk.Combobox(ed1, textvariable=scope_var, values=[LP.ALL_SCOPE],
                                          width=8, state="readonly", font=(FH, 9))
            ui["scope_cb"].pack(side="left", padx=(3, 8))
            tk.Label(ed1, text="保留不遣散:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left")
            tk.Entry(ed1, textvariable=keep_var, bg=BTN, fg=FG, insertbackground=FG,
                     font=(FH, 9)).pack(side="left", fill="x", expand=True, padx=(3, 0))
            ui["scope_var"], ui["keep_var"] = scope_var, keep_var
            # 选项要按【真实武将池】填, 所以得等 vars 都登记完再刷
            self._preset_refresh_scopes(kind)
        ed2 = tk.Frame(ed, bg=BG)
        ed2.pack(fill="x", padx=6, pady=(4, 6))
        tk.Button(ed2, text="保存预设", command=self._guard(lambda k=kind: self._preset_do_save(k)),
                  bg=GREEN, fg=FG, font=(FH, 9, "bold"), padx=8, cursor="hand2").pack(side="left")
        tk.Button(ed2, text="取消", command=self._guard(lambda k=kind: self._preset_close_editor(k)),
                  bg=BTN, fg=FG, font=(FH, 9), padx=6, cursor="hand2").pack(side="left", padx=(5, 0))
        tk.Label(ed2, text=("名单用顿号/逗号分隔, 名字或 ID 都认" if is_rec else
                            "范围内的本季武将全进遣散目标, 只有『保留』里的不进"),
                 bg=BG, fg=DIM, font=(FH, 8)).pack(side="left", padx=(8, 0))

        self._preset_refresh(kind)

    def _preset_ui_of(self, kind):
        return (getattr(self, "_preset_ui", None) or {}).get(kind) or {}

    def _preset_panel(self, kind):
        attr = self._preset_ui_of(kind).get("panel_attr")
        return getattr(self, attr, None) if attr else None

    def _preset_refresh_scopes(self, kind):
        """『范围』下拉的选项 = 当前武将池里实有的势力(本季), 随赛季/数据表变。"""
        from core import list_presets as LP
        ui = self._preset_ui_of(kind)
        cb = ui.get("scope_cb")
        if not cb:
            return
        vals = [LP.ALL_SCOPE] + LP.factions_in(getattr(self, "all_generals_data", None) or [])
        cb["values"] = vals
        if ui["scope_var"].get() not in vals:
            ui["scope_var"].set(LP.ALL_SCOPE)

    def _preset_refresh(self, kind, select=None):
        from core import list_presets as LP
        ui = self._preset_ui_of(kind)
        if not ui or not ui.get("combo"):
            return
        names = [str(p.get("name") or "").strip() for p in LP.load(kind)]
        names = [n for n in names if n]
        ui["combo"]["values"] = names
        cur = select or ui["var"].get()
        if cur not in names:
            cur = names[0] if names else ""
        ui["var"].set(cur)
        self._preset_update_summary(kind)

    def _preset_update_summary(self, kind):
        from core import list_presets as LP
        ui = self._preset_ui_of(kind)
        if not ui or not ui.get("summary"):
            return
        p = LP.get(kind, ui["var"].get())
        ui["summary"].config(text=("  " + LP.summarize(p, kind)) if p else "")

    def _preset_open_editor(self, kind):
        ui = self._preset_ui_of(kind)
        if ui.get("editor") and ui["editor"].winfo_ismapped():
            return
        ui["editor"].pack(fill="x", pady=(0, 4))

    def _preset_close_editor(self, kind):
        ui = self._preset_ui_of(kind)
        if ui.get("editor"):
            ui["editor"].pack_forget()

    def _preset_edit(self, kind):
        from core import list_presets as LP
        ui = self._preset_ui_of(kind)
        p = LP.get(kind, ui["var"].get())
        if not p:
            messagebox.showinfo("快速预设", "先在下拉里选一条预设, 再点编辑。")
            return
        ui["name_var"].set(p.get("name") or "")
        if kind == LP.RECRUIT:
            ui["items_var"].set(p.get("items") or "")
        else:
            ui["scope_var"].set(p.get("scope") or LP.ALL_SCOPE)
            ui["keep_var"].set(p.get("keep") or "")
        self._preset_open_editor(kind)

    def _preset_save_current(self, kind):
        """把面板上【现有的那份名单】存成预设。

        招募: 直接拿右侧目标的名字。
        遣散: 反推规则(见 list_presets.rule_from_entries) —— 手工挑出来的那份
              遣散名单, 反过来就是"在这个势力里、除了我留的那几个"。
        """
        from core import list_presets as LP
        ui = self._preset_ui_of(kind)
        panel = self._preset_panel(kind)
        if not panel:
            return
        entries = panel.get_selected_entries() or []
        if not entries:
            messagebox.showinfo("存为预设", "右侧列表是空的 —— 先把这份名单调出来再存。")
            return
        if kind == LP.RECRUIT:
            names = [str(e.get("name") or e.get("id") or "") for e in entries]
            ui["items_var"].set("、".join(n for n in names if n))
            ui["name_var"].set("、".join(n for n in names[:3] if n))
        else:
            rule = LP.rule_from_entries(getattr(self, "all_generals_data", None) or [],
                                        [e.get("id") for e in entries])
            ui["scope_var"].set(rule.get("scope") or LP.ALL_SCOPE)
            ui["keep_var"].set(rule.get("keep") or "")
            ui["name_var"].set("%s遣散" % (rule.get("scope") or "全部"))
        self._preset_open_editor(kind)

    def _preset_do_save(self, kind):
        from core import list_presets as LP
        ui = self._preset_ui_of(kind)
        name = (ui["name_var"].get() or "").strip()
        if not name:
            messagebox.showwarning("保存预设", "先给这条预设起个名字。")
            return
        data = {"name": name}
        if kind == LP.RECRUIT:
            data["items"] = (ui["items_var"].get() or "").strip()
            if not data["items"]:
                messagebox.showwarning("保存预设", "招募目标名单不能为空。")
                return
        else:
            data["scope"] = ui["scope_var"].get() or LP.ALL_SCOPE
            data["keep"] = (ui["keep_var"].get() or "").strip()
        LP.upsert(kind, data)
        self._preset_refresh(kind, select=name)
        self._preset_close_editor(kind)
        self._status("已保存预设『%s』 — %s" % (name, LP.summarize(data, kind)))

    def _preset_delete(self, kind):
        from core import list_presets as LP
        ui = self._preset_ui_of(kind)
        name = (ui["var"].get() or "").strip()
        if not name:
            messagebox.showinfo("删除预设", "下拉里没有可删的预设。")
            return
        if not messagebox.askyesno("删除预设", "删掉『%s』?\n只删这条预设, 界面上的名单不动。" % name):
            return
        LP.delete(kind, name)
        self._preset_refresh(kind)
        self._preset_close_editor(kind)
        self._status("已删除预设『%s』" % name)

    def _preset_ask_not_season(self, bad):
        """v4.153★ 非本季 ID 点名确认窗 —— 返回 "yes" / "yes_silent" / "cancel"。

        为什么不用 messagebox: 它的按钮文案是固定的, 塞不下"以后别再提示"这个勾 ——
        而【合法的跨季 ID】是真实存在的(黑名单里那条 S9 黄巾兵 20904231、异格版本
        chessID), 每次都弹一遍等于把提示作废。所以给勾, 勾了就写进忽略名单。

        bad = [(name, gid, "dup"|"gone", alt_id)] 见 list_presets.season_check。
        """
        dlg = Toplevel(self.root)
        dlg.title("这几条不是本季 ID")
        dlg.configure(bg=BG)
        dlg.transient(self.root)
        res = {"v": "cancel"}
        head = ("以下 %d 条解析到的不是【本季】的棋子 ID。\n"
                "换季、或这张卡被移出本季商店时会出现 —— 名字照样查得到、界面不报错,\n"
                "但商店大概率刷不出, 这条就悄悄废了。\n\n"
                "★ 也可能是你故意留的跨季卡(如往季特殊棋子), 那就勾上'以后不再提示'。" % len(bad))
        tk.Label(dlg, text=head, bg=BG, fg=FG, font=(FH, 9), justify="left",
                 wraplength=520).pack(fill="x", padx=10, pady=(10, 6))
        box = tk.Text(dlg, height=min(10, max(4, len(bad))), width=68, bg=BTN, fg=FG,
                      font=(FH, 9), relief="flat")
        box.pack(fill="both", expand=True, padx=10)
        for nm, gid, k2, alt in bad:
            if k2 == "dup":
                box.insert("end", "  %s (%s) —— 本季池里有同名卡, 该用 %s\n" % (nm, gid, alt))
            else:
                box.insert("end", "  %s (%s) —— 本季池里没这个名字, 商店大概率刷不出\n" % (nm, gid))
        box.config(state="disabled")
        silent_var = tk.BooleanVar(value=False)
        tk.Checkbutton(dlg, text="这几条以后不再提示(记进 data/list_presets.json)",
                       variable=silent_var, bg=BG, fg=FG, selectcolor="#33333f",
                       activebackground=BG, font=(FH, 9), bd=0, relief="flat",
                       anchor="w").pack(fill="x", padx=10, pady=(6, 0))
        bf = tk.Frame(dlg, bg=BG)
        bf.pack(fill="x", padx=10, pady=10)

        def _ok():
            res["v"] = "yes_silent" if silent_var.get() else "yes"
            dlg.destroy()

        tk.Button(bf, text="仍然应用", command=_ok, bg=GREEN, fg=FG,
                  font=(FH, 9, "bold"), padx=8, cursor="hand2").pack(side="right")
        tk.Button(bf, text="取消", command=dlg.destroy, bg=BTN, fg=FG,
                  font=(FH, 9), padx=8, cursor="hand2").pack(side="right", padx=(0, 6))
        try:
            dlg.grab_set()
            dlg.wait_window()
        except Exception as e:
            # 不能 pass: 这里一旦静默, 用户点了应用却什么都没发生, 查不到为什么。
            self.log(f"[快速预设] 非本季确认窗异常(按取消处理): {e}", level="WARN")
        return res["v"]

    def _preset_apply(self, kind):
        """把选中的预设灌进右侧列表 → 自动保存(落盘) + 推送油猴端。

        替换而非合并: 「除了黄盖都遣散」这种规则是【整份名单】的概念, 合并会残留
        上一条预设留下的卡, 用户根本看不出哪几张是旧的。所以替换前问一下。
        """
        from core import list_presets as LP
        ui = self._preset_ui_of(kind)
        panel = self._preset_panel(kind)
        if not panel:
            messagebox.showerror("快速预设", "列表面板还没建好, 稍等再试。")
            return
        p = LP.get(kind, ui["var"].get())
        if not p:
            messagebox.showinfo("快速预设", "先在下拉里选一条预设。(想自己写 → 点『存当前』或『编辑』)")
            return
        title = str(p.get("name") or "")
        gens = getattr(self, "all_generals_data", None) or []
        unknown, extra = [], ""
        notes = []          # 应用后要告诉用户的"这里可能不对"的点
        if kind == LP.RECRUIT:
            entries, unknown = panel.parse_text(p.get("items") or "")
            tip = "招募目标"
        else:
            entries, unknown, stats = LP.build_dismiss_entries(
                gens, p.get("scope") or LP.ALL_SCOPE, p.get("keep") or "")
            extra = "no_faction=%(no_faction)d skipped=%(kept)d" % stats
            tip = "遣散目标"
            # 保留名单里写了【本季池已经没有】的名字: 名字查得到(往季有)、不报错,
            # 但本季范围内根本没这张卡可留 → 这条保留等于没写, 那张卡照样被遣散。
            gone = LP.season_check_names(LP.split_names(p.get("keep") or ""), gens)
            if gone:
                notes.append("保留名单里 %d 个名字【本季池里没有】(等于没保留, 仍会被遣散): %s"
                             % (len(gone), "、".join(n for n, _k in gone[:8])))
        if not entries:
            messagebox.showwarning(
                "快速预设",
                "预设『%s』没算出任何武将。\n\n%s\n%s" % (
                    title, LP.summarize(p, kind), extra))
            return
        # v4.153★ 招募是按【名字】解析 ID 的, 而武将池是历赛季合表 —— 解析到往季 ID 时
        #   名字照样查得到、界面一声不吭, 但商店永远刷不出, 这条预设就悄悄废了。
        #   这里点名出来问一次(可勾"以后不再提示"放行那些故意留的跨季卡)。
        if kind == LP.RECRUIT:
            bad = LP.season_check(entries, gens)
            if bad:
                ans = self._preset_ask_not_season(bad)
                if ans == "cancel":
                    self._status("已取消应用『%s』(有 %d 条不是本季 ID)" % (title, len(bad)))
                    return
                if ans == "yes_silent":
                    LP.add_ignore([b[1] for b in bad])
                    self.log("[快速预设] 已放行 %d 个非本季 ID(以后不再提示): %s"
                             % (len(bad), "、".join(b[1] for b in bad[:8])))
                else:
                    notes.append("%d 条不是本季 ID(商店大概率刷不出): %s"
                                 % (len(bad), "、".join("%s(%s)" % (b[0], b[1]) for b in bad[:6])))
        old = len(panel.get_selected_ids())
        # 保留名单里有【查不到的名字】时必须拦一下再问: 写错一个字的后果是这张卡不在保留
        # 名单里 → 照样被遣散(实测: 错字时遣散数反而变多), 而界面上完全看不出来。
        warn = ""
        if unknown:
            warn = ("\n\n⚠ 里面有 %d 个名字在武将表里查不到: %s" % (len(unknown), "、".join(unknown[:8]))
                    + ("\n   写错字 = 这张卡不在保留名单里 → 照样会被遣散。" if kind != LP.RECRUIT else ""))
        if notes:
            warn += "\n\n⚠ " + "\n⚠ ".join(notes)
        ask = old or unknown or bool(notes)
        if ask and not messagebox.askyesno(
                "应用预设",
                (("把右侧现有的 %d 个%s替换成『%s』算出的 %d 个吗?" % (old, tip, title, len(entries)))
                 if old else
                 ("『%s』算出 %d 个%s, 现在套用吗?" % (title, len(entries), tip)))
                + "\n\n是 = 替换并保存推送\n否 = 不动" + warn):
            return
        added, _dup = panel.replace_entries(entries)
        saved = panel.save_config()
        msg = "[快速预设] 已应用『%s』→ %s %d 个(替换前 %d 个); %s\n已保存并推送油猴端(%d 个ID)" % (
            title, tip, added, old, LP.summarize(p, kind), len(saved or []))
        if unknown:
            msg += "\n⚠ 名单里有 %d 个名字在武将表里查不到: %s" % (len(unknown), "、".join(unknown[:8]))
        for n in notes:
            msg += "\n⚠ " + n
        self.log(msg, level="WARNING" if (unknown or notes) else "INFO")
        self._status("已套用『%s』: %s %d 个(已保存推送)" % (title, tip, added))

    # ---------- Tab: 招募 ----------
    def _build_recruit_tab(self):
        from ui.dual_list import DualListPanel
        # Tab 内容
        tab_content = self._make_scrollable_tab(self.tab_recruit)

        # 1. 顶部状态/控制区
        top_frame = tk.Frame(tab_content, bg=BG)
        top_frame.pack(fill="x", pady=5)

        tk.Label(top_frame, text="自动招募:", bg=BG, fg=FG, font=(FH, 11, "bold")).pack(side="left")

        self.recruit_status_var = tk.StringVar(value="空闲")
        tk.Label(top_frame, textvariable=self.recruit_status_var, bg=BG, fg=DIM, font=(FH, 10)).pack(side="left", padx=5)

        # v4.13★ 招募选项区: 是否遣散 + 最大刷新次数
        self.recruit_discard_var = tk.BooleanVar(value=False)
        tk.Checkbutton(top_frame, text="买后自动遣散", variable=self.recruit_discard_var,
                       bg=BG, fg=FG, selectcolor="#33333f", activebackground=BG,
                       font=(FH, 9), bd=0, relief="flat").pack(side="left", padx=(15, 0))
        tk.Label(top_frame, text="刷新次数:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left", padx=(10, 2))
        self.recruit_max_refresh_var = tk.StringVar(value="50")
        tk.Entry(top_frame, textvariable=self.recruit_max_refresh_var, width=4, bg=BTN, fg=FG, insertbackground=FG, font=(FH, 9)).pack(side="left")

        self.recruit_btn = tk.Button(top_frame, text="▶ 启动自动招募", command=self._guard(self._toggle_recruit),
                                     bg=BTN, fg=GOLD, font=(FH, 10, "bold"), padx=10, cursor="hand2")
        self.recruit_btn.pack(side="right")

        # v4.24★ 招募增强项 (第二行) —— 四项全部默认关闭/为0, 不勾选时行为与旧版逐行一致
        opt_frame = tk.Frame(tab_content, bg=BG)
        opt_frame.pack(fill="x", pady=(0, 2))

        tk.Label(opt_frame, text="保留金币:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left")
        self.recruit_reserve_var = tk.StringVar(value="0")
        tk.Entry(opt_frame, textvariable=self.recruit_reserve_var, width=4, bg=BTN, fg=FG,
                 insertbackground=FG, font=(FH, 9)).pack(side="left", padx=(2, 0))
        tk.Label(opt_frame, text="(剩余金币≤该值就停止D牌, 0=不限)", bg=BG, fg=DIM, font=(FH, 8)).pack(side="left", padx=(3, 12))

        self.recruit_combine_var = tk.BooleanVar(value=False)
        tk.Checkbutton(opt_frame, text="每轮自动合星", variable=self.recruit_combine_var,
                       bg=BG, fg=FG, selectcolor="#33333f", activebackground=BG,
                       font=(FH, 9), bd=0, relief="flat").pack(side="left", padx=(0, 12))

        self.recruit_bench_sell_var = tk.BooleanVar(value=False)
        tk.Checkbutton(opt_frame, text="备战满自动卖闲", variable=self.recruit_bench_sell_var,
                       bg=BG, fg=FG, selectcolor="#33333f", activebackground=BG,
                       font=(FH, 9), bd=0, relief="flat").pack(side="left")
        tk.Label(opt_frame, text="阈值:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left", padx=(6, 2))
        self.recruit_bench_thr_var = tk.StringVar(value="8")
        tk.Entry(opt_frame, textvariable=self.recruit_bench_thr_var, width=3, bg=BTN, fg=FG,
                 insertbackground=FG, font=(FH, 9)).pack(side="left")
        tk.Label(opt_frame, text="(只卖1星非目标非阵上非永久保留的卡)", bg=BG, fg=DIM, font=(FH, 8)).pack(side="left", padx=(3, 0))

        # v4.24★ 第三行: 逐卡"买到就遣散" (等价于目标串加 '~' 前缀; 与上面的整批"买后自动遣散"独立)
        msell_frame = tk.Frame(tab_content, bg=BG)
        msell_frame.pack(fill="x", pady=(0, 4))
        tk.Label(msell_frame, text="买到就遣散:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left")
        self.recruit_sell_after_var = tk.StringVar(value="")
        tk.Entry(msell_frame, textvariable=self.recruit_sell_after_var, bg=BTN, fg=FG,
                 insertbackground=FG, font=(FH, 9)).pack(side="left", fill="x", expand=True, padx=(3, 6))
        tk.Label(msell_frame, text="(名字或ID, 逗号分隔; 可写 名字@3 单独指定上阵格)",
                 bg=BG, fg=DIM, font=(FH, 8)).pack(side="left")

        # v4.25★ 第四行: "买到就遣散"的【遣散前先上阵】落点
        #   默认"不上阵" → 不选就和旧版逐行一致; 选了才走 买到→上阵→等当先→遣散。
        #   逐卡还能在第三行里写 "名字@3" 单独覆盖这里的默认落点。
        dep_frame = tk.Frame(tab_content, bg=BG)
        dep_frame.pack(fill="x", pady=(0, 4))
        tk.Label(dep_frame, text="遣散前先上阵:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left")
        self.recruit_deploy_pos_var = tk.StringVar(value=DEPLOY_POS_OPTIONS[0])
        ttk.OptionMenu(dep_frame, self.recruit_deploy_pos_var, DEPLOY_POS_OPTIONS[0],
                       *DEPLOY_POS_OPTIONS).pack(side="left", padx=(3, 6))
        tk.Label(dep_frame, text="(顺序: 买到→上阵到该格→等当先效果→再遣散; 该格被占则它和右侧的卡整体右移一格; 上阵失败则直接遣散)",
                 bg=BG, fg=DIM, font=(FH, 8)).pack(side="left")

        # v4.153★ 快速预设: 常用招募目标列表(如 祢衡/丁奉/陈珪、孙策/陈珪)存一条, 一键套用
        self._build_preset_bar(tab_content, "recruit")

        # v4.13★ 双列表组件
        self.recruit_panel = DualListPanel(
            tab_content, title="",
            all_generals_data=self.all_generals_data,
            list_name="auto_buy",
            ops=self.ops,
            ops_push_method=self.ops.update_auto_buy_config if self.ops else None,
            log_func=self.log, height=10,
            left_title="全部武将", right_title="招募目标武将"
        )
        self.recruit_panel.pack(fill="both", expand=True, pady=5)

    def _toggle_recruit(self):
        if hasattr(self, "_recruit_thread") and self._recruit_thread.is_alive():
            self._stop_recruit_loop = True
            self.recruit_btn.config(text="▶ 启动自动招募", fg=GOLD)
            self.log("[自动招募] 停止中...")
        else:
            # v4.22★ 修复 Tkinter 跨线程访问:
            #   原来 target_ids / mode / max_refreshes 是在 _recruit_loop(工作线程)里读的,
            #   它们分别来自 Tk 组件(DualListPanel 的 Listbox)与 Tk 变量(StringVar/BooleanVar)。
            #   Tk 不是线程安全的, 在子线程里调 widget 方法或 var.get() 会随机抛
            #   "main thread is not in main loop" 甚至直接崩进程, 且极难复现。
            #   现在全部在主线程(此函数由按钮 command 触发, 必在主线程)读好后作为参数传下去。
            target_ids = self.recruit_panel.get_selected_ids()
            mode = 'buy_and_sell' if self.recruit_discard_var.get() else 'buy'
            try:
                max_refreshes = int(self.recruit_max_refresh_var.get() or "50")
            except (TypeError, ValueError):
                max_refreshes = 50

            # v4.24★ 招募增强项 (同样在主线程读; 默认 0/关闭 → 不设置时与旧版完全一致)
            try:
                reserve_gold = int(self.recruit_reserve_var.get() or "0")
            except (TypeError, ValueError):
                reserve_gold = 0
            try:
                bench_threshold = int(self.recruit_bench_thr_var.get() or "8")
            except (TypeError, ValueError):
                bench_threshold = 8
            # v4.25★ 遣散前先上阵的默认落点: 界面文字 → 油猴端表达式
            #   (none=不上阵 | default=上完后的倒数第二格 | auto=第一个空位 | '1'..'7'=固定格)
            _dp_label = (self.recruit_deploy_pos_var.get() or "").strip()
            if _dp_label == "倒数第二格":
                _deploy_pos = "default"
            elif _dp_label == "第一个空位":
                _deploy_pos = "auto"
            elif _dp_label.startswith("第") and _dp_label.endswith("格"):
                _deploy_pos = _dp_label[1:-1] or "none"   # "第3格" → "3"
            else:
                _deploy_pos = "none"
            opts = {
                "reserve_gold": max(0, reserve_gold),
                "auto_combine": bool(self.recruit_combine_var.get()),
                "auto_sell_bench": bool(self.recruit_bench_sell_var.get()),
                "bench_threshold": max(1, bench_threshold),
                "sell_after_deploy_pos": _deploy_pos,
            }
            # v4.24★ 逐卡"买到就遣散": 统一加 '~' 前缀, 且放在目标列表【最前面】——
            #   同一个卡同时出现在两边时, 让"买到就卖"优先命中(否则会被普通项先匹配走)。
            #   顺手把中文逗号/顿号/分号都当分隔符(中文输入法下很容易打出全角逗号)。
            _raw = self.recruit_sell_after_var.get()
            for _ch in ("，", "、", "；", ";"):
                _raw = _raw.replace(_ch, ",")
            sell_after = [t.strip().lstrip("~") for t in _raw.split(",")]
            sell_after = [t for t in sell_after if t]
            if sell_after:
                target_ids = ["~" + t for t in sell_after] + list(target_ids)

            # 空列表直接在主线程拦掉, 连线程都不必启动 (只填"买到就遣散"也算有目标)
            if not target_ids:
                self.log("[自动招募] 右侧列表为空, 请先添加武将", level="WARNING")
                self.recruit_status_var.set("请先添加武将")
                return

            self._stop_recruit_loop = False
            self.recruit_btn.config(text="■ 停止自动招募", fg=RED)
            self.log("[自动招募] 启动...")
            self._recruit_thread = threading.Thread(
                target=self._recruit_loop,
                args=(target_ids, max_refreshes, mode, opts),
                daemon=True,
            )
            self._recruit_thread.start()

    def _recruit_loop(self, target_ids, max_refreshes, mode, opts=None):
        """v4.13★ 自动招募: 从右侧已配置列表读取目标ID → 调用 autoFindAndBuy

        v4.22★ 参数由主线程读好后传入(见 _toggle_recruit 注释)。
        v4.24★ opts: 招募增强项 {reserve_gold, auto_combine, auto_sell_bench, bench_threshold};
               默认全关, 不设置时与旧版行为一致。
        v4.25★ opts 新增 sell_after_deploy_pos: "买到就遣散"的【遣散前先上阵】默认落点
               (none=不上阵 | default=上完后的倒数第二格 | auto=第一个空位 | '1'..'7'=固定格)。
               同样默认 none, 不选时与旧版逐行一致。
        v4.26★ 落点语义修正: default 改按"上完后"算(已上阵6人 → 第6格, 旧版给的是第5格);
               目标格被占时改为【插入右移】(该格及其右侧整体右移一格, 新卡真的占住落点),
               不再退到第一个空位 —— 旧规则在阵满时让"指定落点"形同虚设。
        v4.27★ 新增「剧本」tab: 一串有先后顺序的步骤(上阵/遣散/牌库找卡/用锦囊/循环/条件),
               每个剧本可各绑一个快捷键在游戏页面里执行一次。
               页面 API: push_scenarios() / list_scenarios() / start_scenario(id) /
                         stop_scenario() / poll_scenario_log(seq)
               上阵落点新增相对写法: after:名字 / before:名字 / middle:名字(两张同名中间)。
        本函数运行在工作线程, 除 self.log()(内部走 root.after) 外,
        任何 UI 操作都必须经 self.root.after 回主线程。
        """
        self.log(f"[自动招募] 目标ID: {target_ids}, 模式: {mode}, 最大刷新: {max_refreshes}, 增强: {opts or '无'}")
        self.root.after(0, lambda: self.recruit_status_var.set(f"招募中... 目标{len(target_ids)}个"))

        def progress_cb(msg):
            self.root.after(0, lambda: self.recruit_status_var.set(str(msg)))

        self.ops.start_auto_buy(target_ids, max_refreshes, progress_cb, mode, opts)

        try:
            while not self._stop_recruit_loop and self.ops._auto_task_running:
                time.sleep(0.5)
        except Exception as e:
            self.log(f"[自动招募] 发生错误: {e}", level="ERROR")
        finally:
            self.ops.stop_auto_buy()
            self.root.after(0, lambda: self.recruit_status_var.set("空闲"))
            self.root.after(0, lambda: self.recruit_btn.config(text="▶ 启动自动招募", fg=GOLD))
            self.log("[自动招募] 已停止.")

    # ---------- Tab: 宏录制 ----------
    def _build_macro_tab(self):
        tab_content = self._make_scrollable_tab(self.tab_macro)

        # 顶部控制区
        top_frame = tk.Frame(tab_content, bg=BG)
        top_frame.pack(fill="x")

        # 宏名称
        name_frm = tk.LabelFrame(top_frame, text="宏名称", bg=BG, fg=GOLD, bd=1, relief="solid", font=(FH, 9, "bold"), padx=4, pady=1)
        name_frm.pack(side="left", padx=5, pady=2)
        self.macro_name = tk.StringVar(value="macro_new")
        tk.Entry(name_frm, textvariable=self.macro_name, width=20, bg=BTN, fg=FG, insertbackground=FG, relief="flat", font=(FH, 9)).pack(side="left")

        # 录制模式
        mode_frm = tk.LabelFrame(top_frame, text="录制模式", bg=BG, fg=GOLD, bd=1, relief="solid", font=(FH, 9, "bold"), padx=4, pady=1)
        mode_frm.pack(side="left", padx=5, pady=2)
        self.macro_mode_var = tk.StringVar(value="PIXEL") # v3.49★默认PIXEL模式(键鼠物理录制)
        tk.Radiobutton(mode_frm, text="PIXEL (键鼠)", variable=self.macro_mode_var, value="PIXEL",
                       bg=BG, fg=FG, selectcolor="#33333f", activebackground=BG,
                       font=(FH, 9), bd=0, relief="flat").pack(side="left", padx=2)
        tk.Radiobutton(mode_frm, text="SEMANTIC (语义)", variable=self.macro_mode_var, value="SEMANTIC",
                       bg=BG, fg=FG, selectcolor="#33333f", activebackground=BG,
                       font=(FH, 9), bd=0, relief="flat", command=self._macro_mode_on_change).pack(side="left", padx=2)
        self.macro_mode_var.trace_add("write", self._macro_mode_on_change)

        # 录制状态
        self.macro_record_status = tk.Label(top_frame, text="空闲", bg=BG, fg=DIM, font=(FH, 10, "bold"))
        self.macro_record_status.pack(side="left", padx=10)

        # 录制按钮
        record_btn_frm = tk.Frame(top_frame, bg=BG)
        record_btn_frm.pack(side="right", padx=5)
        tk.Button(record_btn_frm, text="开始录制 (F10)", command=self._macro_start,
                  bg=GOLD, fg=BG, relief="flat", bd=0,
                  font=(FH, 10, "bold"), padx=10, pady=2, cursor="hand2").pack(side="left", padx=2)
        tk.Button(record_btn_frm, text="停止录制 (F11)", command=self._macro_stop,
                  bg=RED, fg=BG, relief="flat", bd=0,
                  font=(FH, 10, "bold"), padx=10, pady=2, cursor="hand2").pack(side="left", padx=2)

        # 窗口选择区 (新的入口按钮)
        cap_frm = tk.LabelFrame(tab_content, text="捕获范围 (PIXEL模式)", bg=BG, fg=GOLD, bd=1, relief="solid", font=(FH, 9, "bold"), padx=4, pady=1)
        cap_frm.pack(fill="x", pady=5)
        
        row_c1 = tk.Frame(cap_frm, bg=BG); row_c1.pack(fill="x", pady=1)
        tk.Label(row_c1, text="模式:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left", padx=2)
        self.macro_capture_scope = tk.StringVar(value="GLOBAL") # GLOBAL / WINDOW
        tk.Radiobutton(row_c1, text="全局 (F键)", variable=self.macro_capture_scope, value="GLOBAL",
                           bg=BG, fg=FG, selectcolor="#33333f", activebackground=BG,
                           font=(FH, 9), bd=0, relief="flat", command=self._macro_scope_on_change).pack(side="left", padx=2)
        tk.Radiobutton(row_c1, text="窗口 (G键)", variable=self.macro_capture_scope, value="WINDOW",
                           bg=BG, fg=FG, selectcolor="#33333f", activebackground=BG,
                           font=(FH, 9), bd=0, relief="flat", command=self._macro_scope_on_change).pack(side="left", padx=2)
        
        # 新的窗口选择入口按钮
        tk.Button(row_c1, text="🔍 选择游戏窗口", command=self._show_window_picker_dialog,
                  bg=BLUE, fg=BG, relief="flat", bd=0,
                  font=(FH, 9, "bold"), padx=8, cursor="hand2").pack(side="left", padx=10)

        # 显示当前选中的窗口信息
        self.macro_capture_hwnd_sel = None # (hwnd, title, w, h, classname)
        self.macro_current_window_label = tk.Label(row_c1, text="未选择窗口", bg=BG, fg=DIM, font=(FH, 9))
        self.macro_current_window_label.pack(side="left", padx=5)

        self.macro_capture_savehwnd_var = tk.BooleanVar(value=True)
        tk.Checkbutton(row_c1, text="保存hwnd进宏(回放时自动切游戏窗到前景)", variable=self.macro_capture_savehwnd_var,
                       bg=BG, fg=GOLD, selectcolor="#33333f", activebackground=BG,
                       font=(FH, 8), bd=0, relief="flat").pack(side="right", padx=4)

        # 宏事件列表
        event_list_frm = tk.LabelFrame(tab_content, text="宏事件列表", bg=BG, fg=GOLD, bd=1, relief="solid", font=(FH, 9, "bold"), padx=4, pady=1)
        event_list_frm.pack(fill="both", expand=True, pady=5)

        self.macro_diag_label = tk.Label(event_list_frm, text="[诊断] 共0条", bg=BG, fg=DIM, font=(FH, 9))
        self.macro_diag_label.pack(fill="x", anchor="w")

        self.macro_event_tree = ttk.Treeview(event_list_frm, columns=("idx", "icon", "desc"), show="headings", height=10)
        self.macro_event_tree.heading("idx", text="#")
        self.macro_event_tree.heading("icon", text="")
        self.macro_event_tree.heading("desc", text="事件描述")
        self.macro_event_tree.column("idx", width=30, anchor="center")
        self.macro_event_tree.column("icon", width=30, anchor="center")
        self.macro_event_tree.column("desc", width=400, anchor="w")
        self.macro_event_tree.pack(fill="both", expand=True)

        # 底部回放控制区
        bottom_frame = tk.Frame(tab_content, bg=BG)
        bottom_frame.pack(fill="x", side="bottom", pady=5)

        r4 = tk.Frame(bottom_frame, bg=BG); r4.pack(fill="x", pady=1)
        tk.Label(r4, text="选择宏文件:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left", padx=2)
        self.macro_combo_files = ttk.Combobox(r4, textvariable=tk.StringVar(), values=[], width=30, state="readonly")
        self.macro_combo_files.pack(side="left", padx=2)
        self.macro_combo_files.bind("<<ComboboxSelected>>", lambda e: self._macro_load_selected_file())

        tk.Button(r4, text="删除", command=self._macro_delete_selected,
                  bg=RED, fg=BG, relief="flat", bd=0,
                  font=(FH, 9, "bold"), padx=8, cursor="hand2").pack(side="left", padx=2)

        tk.Label(r4, text="速度x:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left", padx=10)
        self.macro_speed = tk.DoubleVar(value=1.0)
        tk.Entry(r4, textvariable=self.macro_speed, width=5, bg="#222", fg=FG, insertbackground=FG, relief="flat", font=(FH, 9)).pack(side="left")

        tk.Label(r4, text="循环:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left", padx=10)
        self.macro_loop = tk.IntVar(value=1)
        loop_en = tk.Entry(r4, textvariable=self.macro_loop, width=5,
                           bg="#222", fg=FG, insertbackground=FG, relief="flat",
                           font=(FH, 10))
        loop_en.pack(side="left", padx=2)
        tk.Label(r4, text="次", bg=BG, fg=FG, font=(FH, 9)).pack(side="left")

        self.btn_macro_play = tk.Button(r4, text="▶回放", command=self._macro_play_dispatch,
                                        bg="#1a6cff", fg="#fff", relief="flat", bd=0,
                                        font=(FH, 10, "bold"), padx=14, pady=2, cursor="hand2")
        self.btn_macro_play.pack(side="right", padx=4)
        tk.Button(r4, text="■停止", command=self._macro_stop_play,
                  bg=RED, fg="#fff", relief="flat", bd=0,
                  font=(FH, 10, "bold"), padx=14, pady=2, cursor="hand2").pack(side="right", padx=4)

        self.macro_play_status = tk.Label(bottom_frame, text="回放: 空闲", bg=BG, fg=DIM,
                                          font=(FH, 9, "bold"), justify="left")
        self.macro_play_status.pack(fill="x", anchor="w")

        self._macro_reload_list() # Initial load of macro files
        self._macro_refresh_events() # Initial display of current events

    def _macro_mode_on_change(self, *args):
        self.log(f"[宏] 录制模式切换为: {self.macro_mode_var.get()}")
        MACRO.set_mode(self.macro_mode_var.get())

    def _macro_scope_on_change(self, *args):
        self.log(f"[宏] 捕获范围切换为: {self.macro_capture_scope.get()}")
        # 如果从 WINDOW 切换到 GLOBAL, 清空已选窗口
        if self.macro_capture_scope.get() == "GLOBAL":
            self.macro_capture_hwnd_sel = None
            self.macro_current_window_label.config(text="未选择窗口", fg=DIM)
        # MACRO.set_capture_scope 会在录制开始前调用 _sync_scope_to_macro 进行统一设置

    def _show_window_picker_dialog(self):
        # 弹出窗口选择对话框
        initial_keyword = ""
        if self.macro_capture_hwnd_sel:
            initial_keyword = self.macro_capture_hwnd_sel[1] # Use current window title as initial search

        self.window_picker_dialog = WindowPickerDialog(self.root, self.log, initial_search_keyword=initial_keyword)
        if self.window_picker_dialog.selected_window_info:
            hwnd, title, w, h, classname = self.window_picker_dialog.selected_window_info
            self.macro_capture_hwnd_sel = self.window_picker_dialog.selected_window_info
            self.macro_capture_scope.set("WINDOW") # 自动切换为窗口模式
            self.macro_current_window_label.config(text=f"已选: {title} (hwnd={hwnd})", fg=FG)
            self.log(f"[宏] 窗口选择器选中: {title} (hwnd={hwnd})")
        else:
            self.log("[宏] 窗口选择器取消或未选择。")
            
    def _macro_load_selected_file(self):
        nm = (self.macro_combo_files.get() or "").strip()
        if not nm:
            self.log("[宏] 未选择宏文件")
            return
        try:
            obj = MACRO.load(nm)
            # v4.22★ 记住本次加载的宏文件, 供 _macro_reload_list() 下次启动时恢复选中。
            #   此前该属性只被读取(hasattr 判断)、从未被赋值 → "恢复上次编辑的宏"一直失效。
            self._last_edited_macro_file = nm
            self.log(f"[宏] 已加载宏文件: {nm} 事件{len(obj.get('events', []))}条")
            # 加载后自动刷新事件列表
            MACRO._events = obj.get("events", []) # 将加载的事件放到内存中
            # v4.101★ 换了一份事件 → 增量基线必须作废: 否则列表会把新宏的这几条当成
            #   "上一个宏后面新增的", 界面上留着旧宏的行(而且计数会继续往上累加)。
            self._macro_drawn = 0
            self._macro_counts = None
            MACRO.set_mode(obj.get("metadata", {}).get("mode", "PIXEL"))
            self.macro_mode_var.set(MACRO.mode.upper())

            meta = obj.get("metadata", {})
            if meta.get("capture_scope") == "WINDOW" and meta.get("capture_hwnd"):
                self.macro_capture_scope.set("WINDOW")
                self.macro_capture_hwnd_sel = (meta["capture_hwnd"], meta["capture_win_title"], 0, 0, "") # 尺寸类名未知,暂时填0
                self.macro_current_window_label.config(text=f"已选: {meta['capture_win_title']} (hwnd={meta['capture_hwnd']})", fg=FG)
            else:
                self.macro_capture_scope.set("GLOBAL")
                self.macro_capture_hwnd_sel = None
                self.macro_current_window_label.config(text="未选择窗口", fg=DIM)

            self.macro_capture_savehwnd_var.set(meta.get("capture_save_hwnd", True))
            self._macro_refresh_events()
        except Exception as e:
            self.log(f"[宏] 加载宏文件 {nm} 失败: {e}", level="ERROR")
            messagebox.showerror("错误", f"加载宏文件 {nm} 失败: {e}")

    def _macro_reload_list(self):
        files = MACRO.list_macros()
        self.macro_combo_files.config(values=files)
        if files:
            # 首次加载时, 尝试选中上一次编辑的宏 (如果存在)
            if hasattr(self, "_last_edited_macro_file") and self._last_edited_macro_file in files:
                self.macro_combo_files.set(self._last_edited_macro_file)
                self._macro_load_selected_file()
            else:
                self.macro_combo_files.set(files[0]) # 默认选中第一个
                self._macro_load_selected_file()
        else:
            self.macro_combo_files.set("")
            MACRO._events = [] # 清空内存中的事件
            self._macro_refresh_events()
            self.log("[宏] 未找到任何宏文件。")


    # ---------- Tab: 剧本 (v4.27★) ----------

    # v4.109★ 剧本页本体已搬到 ui/scenario_tab.py (ScenarioTabBuilder), 这里只留薄壳。
    def _build_scenario_tab(self):
        from ui import scenario_tab as SCTAB
        self._scenario_builder = SCTAB.ScenarioTabBuilder(self)
        self._scenario_builder._build_scenario_tab()

    # v4.184★ 快速找牌页本体在 ui/quickfind_tab.py (QuickFindTabBuilder), 这里只留薄壳。
    def _build_quickfind_tab(self):
        from ui import quickfind_tab as QFT
        self._quickfind_builder = QFT.QuickFindTabBuilder(self)
        self._quickfind_builder._build()

    def _scenario_collect(self, include_transport_fields=False):
        """薄壳: 收集逻辑已随剧本页搬到 ui/scenario_tab.py, 这里转发过去。

        app.py 的 _push_all_lists_to_script 会调它 —— 那个方法不搬, 所以壳得留着。
        剧本页还没构建过(用户没点到那一页)时返回 [], 与原来的行为一致。
        """
        b = getattr(self, "_scenario_builder", None)
        if b is None:
            return []
        return b._scenario_collect(include_transport_fields=include_transport_fields)

    def _run_bg(self, tag, fn, on_done=None, wait=None):
        """把一次会【阻塞等游戏页回话】的调用丢进工作线程(同名动作单飞: 上轮没回来不发新的)。

        为什么必须有它: core/remote_ops.py 的 send_recv 是 `while + time.sleep` 的阻塞轮询
        (默认 5 秒, hotkey() 更是 8 秒)。在 Tk 主线程里直接调用 = 界面整个冻住 —— 点一下
        「用锦囊」界面僵 8 秒、窗口拖不动, 看着像"卡死", 其实只是在等回包。项目里原来只有
        _hk_btn / _scenario_run_thread 等少数几处做对了, 其余按钮都是直连网络。
        """
        b = getattr(self, "_bg_busy", None)
        if b is None:
            b = self._bg_busy = {}
        if b.get(tag):
            return False
        b[tag] = True
        if wait:
            try:
                self.status_bar_var.set(str(wait))
            except Exception as e:
                self.log(f"[{tag}] 状态栏更新失败: {e}", level="WARN")

        def _work():
            r, err = None, ""
            try:
                r = fn()
            except Exception as e:
                err = str(e)

            def _done():
                b[tag] = False
                try:
                    if on_done:
                        on_done(r, err)
                except Exception as e:
                    self.log(f"[{tag}] 回界面更新失败: {e}", level="WARN")
            try:
                self.root.after(0, _done)
            except Exception as e:
                b[tag] = False
                self.log(f"[{tag}] 回主线程失败(界面可能已关): {e}", level="WARN")

        try:
            threading.Thread(target=_work, name="bg-" + str(tag), daemon=True).start()
            return True
        except Exception as e:
            b[tag] = False
            self.log(f"[{tag}] 起线程失败: {e}", level="WARN")
            return False

    def _build_season_tab(self):
        """赛季换表工具: ① 一键下载解析 → ② 查看数据 → ③ 切换向导 → ④ 推送/替换 → 导入/导出。

        为什么要有这个 tab: 官方每个赛季都会改自走棋配置(改名/加卡/换技能ID), 而助手"这张卡是
        什么、什么技能、要不要选人、按名字找卡"全靠本地那几张表(ChessConf.csv /
        ChessSpellConf.csv / data/*.json)。赛季一更新这些表就是错的, 现象是找卡失败、技能解析不出。
        以前只能用命令行跑 tools/season_config.py 再对着文档自己搬文件, 现在一个页面全做完:
          · 一键下载解析 = 抓 Config.sgs → 解密 → 解析(只写赛季目录, 不动现行数据)
          · 查看数据     = 直接看解析出来的棋子和锦囊(可按本季/关键词筛)
          · 切换向导     = 写之前先对比『本季快照 vs 现行数据』: 哪些卡新增/退场/改名, 哪些
                          列表配置(随征ID/白名单/…)还引用着会失效的 ID —— 只读, 不动数据。
          · 推送/替换    = 按【所有消费方需要的格式】一次写好现行数据(推送=保留旧赛季的键;
                          替换=完全按这份快照重建)。写之前自动整份备份到 data/_backup/。
          · 导入/导出    = 导出整包(所有格式, 给别人/留档); 导入认 json/sgs/CSV/名字映射/导出包。
        """
        from tools import season_config as SC
        SC.set_logger(self._season_log)      # 工具里的 log() 全部转到本 tab 的日志框

        self._season_running = False
        # v4.68★ 赛季数据页内容很多, 用 Canvas+Scrollbar 避免下方工具链被窗口截断。
        # v4.70★ 复用统一的滚动容器(已修复 expand 控件展不开的问题)。
        wrap = self._make_scrollable_tab(self.tab_season, padx=(10, 0), pady=8)

        # ------------------------------ ① 一键下载解析 ------------------------------
        box1 = tk.LabelFrame(wrap, text=" ① 一键下载解析 (抓官方配置 → 解密 → 解析; 只写赛季目录, 不动现行数据) ",
                             bg=BG, fg=GOLD, font=(FH, 10, "bold"), bd=1, relief="groove")
        box1.pack(fill="x", pady=(0, 6))
        r1 = tk.Frame(box1, bg=BG)
        r1.pack(fill="x", padx=6, pady=6)

        tk.Label(r1, text="赛季号:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left")
        self.season_no_var = tk.StringVar(value=SC.current_season())
        tk.Entry(r1, textvariable=self.season_no_var, width=4, bg=BTN, fg=FG,
                 insertbackground=FG, font=(FH, 9)).pack(side="left", padx=(2, 10))
        tk.Label(r1, text="资源路径段:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left")
        self.season_ver_var = tk.StringVar(value="")
        tk.Entry(r1, textvariable=self.season_ver_var, width=5, bg=BTN, fg=FG,
                 insertbackground=FG, font=(FH, 9)).pack(side="left", padx=(2, 4))
        tk.Label(r1, text="(不填=同赛季号; 下赛季一般是 11)", bg=BG, fg=DIM, font=(FH, 8)).pack(side="left")

        self.season_fetch_btn = tk.Button(r1, text="一键下载解析", command=self.a_season_fetch,
                                          bg=ACT, fg=GOLD, font=(FH, 10, "bold"), padx=12,
                                          cursor="hand2")
        self.season_fetch_btn.pack(side="right", padx=(8, 0))
        # v4.71★ 「检查官方更新」—— 用户原话: "赛季更新这个能更新到吗? 加上可以更新"。
        #   更新本来就能(下载/解密/导出/推送整条链都在这个页面), 缺的是【先看一眼值不值得下】:
        #   8MB 的 Config.sgs 每点一次就重下一遍太浪费, 所以这里只发 HEAD(不下载),
        #   比大小/Last-Modified 就知道官方改过没有, 顺便探一下下个路径段上线没有
        #   (下赛季发布时它才会从占位变成真文件 —— 这样能提前知道要换赛季)。
        self.season_check_btn = tk.Button(r1, text="检查官方更新", command=self.a_season_check_update,
                                          bg=BTN, fg=BLUE, font=(FH, 9, "bold"), padx=8,
                                          cursor="hand2")
        self.season_check_btn.pack(side="right", padx=(8, 0))
        tk.Label(r1, text="联网失败/已下过 → 用下面『导入』选本地 json 或 .sgs", bg=BG, fg=DIM,
                 font=(FH, 8)).pack(side="right")

        r2 = tk.Frame(box1, bg=BG)
        r2.pack(fill="x", padx=6, pady=(0, 6))
        self.season_import_btn = tk.Button(r2, text="导入本地数据 …", command=self.a_season_import,
                                           bg=BTN, fg=BLUE, font=(FH, 9, "bold"), padx=8, cursor="hand2")
        self.season_import_btn.pack(side="left", padx=(0, 6))
        self.season_export_btn = tk.Button(r2, text="导出整包 …", command=self.a_season_export,
                                           bg=BTN, fg=BLUE, font=(FH, 9, "bold"), padx=8, cursor="hand2")
        self.season_export_btn.pack(side="left", padx=(0, 12))
        tk.Label(r2, text="导入认: TavernChessConfig.json / .sgs / 导出包目录 / ChessConf.csv / 名字映射 json",
                 bg=BG, fg=DIM, font=(FH, 8)).pack(side="left")

        self.season_status_var = tk.StringVar(value="准备就绪")
        tk.Label(box1, textvariable=self.season_status_var, bg=BG, fg=BLUE, font=(FH, 9)).pack(
            anchor="w", padx=8, pady=(0, 6))

        # ------------------------------ ② 查看数据 ------------------------------
        box2 = tk.LabelFrame(wrap, text=" ② 查看数据 (当前解析出的赛季配置) ",
                             bg=BG, fg=GOLD, font=(FH, 10, "bold"), bd=1, relief="groove")
        box2.pack(fill="both", expand=True, pady=(0, 6))
        r3 = tk.Frame(box2, bg=BG)
        r3.pack(fill="x", padx=6, pady=(6, 2))

        self.season_kind_var = tk.StringVar(value="chess")
        for txt, val in (("棋子", "chess"), ("锦囊", "spell")):
            tk.Radiobutton(r3, text=txt, variable=self.season_kind_var, value=val,
                           command=self._season_show_table, bg=BG, fg=FG, selectcolor="#33333f",
                           activebackground=BG, activeforeground=FG, font=(FH, 9)).pack(side="left")
        tk.Label(r3, text="关键词:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left", padx=(12, 2))
        self.season_kw_var = tk.StringVar(value="")
        kw_entry = tk.Entry(r3, textvariable=self.season_kw_var, width=14, bg=BTN, fg=FG,
                            insertbackground=FG, font=(FH, 9))
        kw_entry.pack(side="left")
        kw_entry.bind("<Return>", lambda _e: self._season_show_table())   # 回车查, 不逐键刷(表很大)
        # v4.42★ 这里以前是个「只看本季」复选框 —— 于是"看别的赛季"在界面上根本做不到。
        #   要区分两个概念(以前被混成了一个, 所以怎么点都只能看到本赛季):
        #     · 上面那行「赛季号」= 读哪份快照  (data/season/s{号}/TavernChessConfig.json)
        #     · 这里「只看赛季」  = 表格里筛出 SeasonID 等于哪个赛季的行
        #   一份快照里【本来就带着往季的行】(SeasonID=9/8/…), 以前只是没有入口把它们筛出来。
        #   所以"看 S9 的条目"有两条路, 取决于你手上有没有 s9 的快照:
        #     有 s9 快照    → 上面赛季号填 9
        #     只有 s10 快照 → 上面填 10, 这里填 9 (在 s10 的表里筛出 SeasonID=9 的行)
        tk.Label(r3, text="只看赛季:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left", padx=(12, 2))
        self.season_only_var = tk.StringVar(value="本季")
        only_entry = tk.Entry(r3, textvariable=self.season_only_var, width=6, bg=BTN, fg=FG,
                              insertbackground=FG, font=(FH, 9))
        only_entry.pack(side="left")
        only_entry.bind("<Return>", lambda _e: self._season_show_table())
        tk.Label(r3, text="空=不过滤 / 本季 / 9", bg=BG, fg=DIM,
                 font=(FH, 8)).pack(side="left", padx=(3, 0))
        self.season_table_var = tk.StringVar(value="")
        tk.Label(r3, textvariable=self.season_table_var, bg=BG, fg=DIM, font=(FH, 9)).pack(side="right", padx=6)
        tk.Button(r3, text="刷新表格", command=self._season_show_table, bg=BTN, fg=FG,
                  font=(FH, 9, "bold"), padx=8, cursor="hand2").pack(side="right")

        tf = tk.Frame(box2, bg=BG)
        tf.pack(fill="both", expand=True, padx=6, pady=(2, 6))
        cols = ("id", "name", "rank", "season", "shop", "desc")
        self.season_tree = ttk.Treeview(tf, columns=cols, show="headings", height=9)
        for c, t, w in (("id", "ID", 90), ("name", "名字", 130), ("rank", "品阶", 50),
                        ("season", "赛季", 50), ("shop", "本季可用", 70), ("desc", "技能/说明", 560)):
            self.season_tree.heading(c, text=t)
            self.season_tree.column(c, width=w, anchor="w")
        vs = tk.Scrollbar(tf, orient="vertical", command=self.season_tree.yview)
        self.season_tree.configure(yscrollcommand=vs.set)
        self.season_tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")

        # ------------------------------ ③ 赛季切换向导 (v4.47★) ------------------------------
        # 以前只有「写」没有「先看会变成什么」—— 按下推送表的瞬间就已经换掉了, 换错只能翻
        # data/_backup 手工还原。而换表真正的坑是【换完之后哪些卡没了 / 哪些 ID 还被人引用】:
        # 『随征卡牌ID』存的是技能ID(942311 之类)跟着赛季走, 退场后插件会【静默】不生效;
        # 改名比退场更隐蔽 —— ID 还在, 但按名字保存的白名单/剧本目标全部失配。
        # 所以把"对齐检查"放在写之前: 先对比 → 看清退场/改名/受影响列表 → 再决定写不写。
        boxw = tk.LabelFrame(wrap, text=" ③ 赛季切换向导 (换表前先对比『本季快照』和『现行数据』, 只读不动数据) ",
                             bg=BG, fg=GOLD, font=(FH, 10, "bold"), bd=1, relief="groove")
        boxw.pack(fill="x", pady=(0, 6))
        rw = tk.Frame(boxw, bg=BG)
        rw.pack(fill="x", padx=6, pady=6)
        self.season_diff_btn = tk.Button(rw, text="对比差异 (本季 vs 现行)", command=self.a_season_diff,
                                         bg=BTN, fg=BLUE, font=(FH, 9, "bold"), padx=8, cursor="hand2")
        self.season_diff_btn.pack(side="left", padx=(0, 8))
        self.season_wizard_btn = tk.Button(
            rw, text="一键切换 (备份 → 推送本季 → 刷新武将表/映射)",
            command=self.a_season_wizard_go, bg=ACT, fg=GOLD,
            font=(FH, 9, "bold"), padx=10, cursor="hand2")
        self.season_wizard_btn.pack(side="left", padx=(0, 10))
        self.season_diff_var = tk.StringVar(value="还没对比 —— 先点『对比差异』, 结果接在下面日志里")
        tk.Label(rw, textvariable=self.season_diff_var, bg=BG, fg=DIM, font=(FH, 9),
                 wraplength=720, justify="left").pack(side="left")
        tk.Label(boxw, text=(
            "看什么: 【新增/退场/改名】三类会直接决定换表后要不要改配置 —— 退场的卡会让"
            "以卡名保存的白名单/剧本目标找不到卡; 改名的卡 ID 还在但名字对不上, 更隐蔽。\n"
            "『随征卡牌ID』存的其实是技能ID(如 942311 黄巾兵), 它也跟着赛季走 —— 向导会点名"
            "『还引用着会失效 ID』的列表, 换完照着改一遍即可。"),
            bg=BG, fg=DIM, font=(FH, 8), wraplength=800, justify="left").pack(anchor="w", padx=8, pady=(0, 6))

        # ------------------------------ ④ 写入现行数据 ------------------------------
        box3 = tk.LabelFrame(wrap, text=" ④ 写入现行数据 (写前自动整份备份到 data/_backup) ",
                             bg=BG, fg=GOLD, font=(FH, 10, "bold"), bd=1, relief="groove")
        box3.pack(fill="x")
        r4 = tk.Frame(box3, bg=BG)
        r4.pack(fill="x", padx=6, pady=6)

        self.season_push_btn = tk.Button(r4, text="推送到现行数据 (合并: 保留旧赛季, 推荐)",
                                         command=self.a_season_push, bg=GREEN, fg=FG,
                                         font=(FH, 10, "bold"), padx=10, cursor="hand2")
        self.season_push_btn.pack(side="left", padx=(0, 8))
        self.season_replace_btn = tk.Button(r4, text="替换现行数据 (整份重建)",
                                            command=self.a_season_replace, bg=RED, fg=FG,
                                            font=(FH, 10, "bold"), padx=10, cursor="hand2")
        self.season_replace_btn.pack(side="left", padx=(0, 12))
        self.season_rebuild_picker_var = tk.BooleanVar(value=True)
        tk.Checkbutton(r4, text="顺带重建武将精选(hero_picker.json)", variable=self.season_rebuild_picker_var,
                       bg=BG, fg=FG, selectcolor="#33333f", activebackground=BG,
                       font=(FH, 9)).pack(side="left")

        # v4.46★ 「只推本季」: 用户问"能不能只推送根据我查找到本赛季、或者我选的赛季进行推送"。
        #   上一行的『推送』/『替换』管的是【写进现行数据(csv/json)】, 这个勾管的是
        #   【连接时推给油猴脚本的映射(名字表/技能表)】—— 后者以前永远全量, 没有开关。
        r5 = tk.Frame(box3, bg=BG)
        r5.pack(fill="x", padx=8, pady=(0, 6))
        self.script_map_season_var = tk.BooleanVar(
            value=bool((getattr(self, "cfg", None) or {}).get("script_map_season_only")))
        tk.Checkbutton(r5, text="连接时推给脚本的映射只含上面那个『赛季号』",
                       variable=self.script_map_season_var,
                       command=self._on_toggle_script_map_season,
                       bg=BG, fg=FG, selectcolor="#33333f", activebackground=BG,
                       font=(FH, 9)).pack(side="left")
        tk.Label(r5, text="(默认不勾 = 全量含往季。勾上: 表更小/不串季, 代价是往季变体卡与已被"
                          "赛季更新挤掉的旧卡, 名字会退化成裸 ID —— 实机上 20503211「董袭」正是"
                          "靠全量名表才解析出名字)",
                 bg=BG, fg=DIM, font=(FH, 8)).pack(side="left", padx=8)

        bottom = tk.Frame(box3, bg=BG)
        bottom.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        left = tk.Frame(bottom, bg=BG)
        left.pack(side="left", fill="both", expand=True, padx=(0, 4))
        tk.Label(left, text="现行数据现状:", bg=BG, fg=FG, font=(FH, 9, "bold")).pack(anchor="w")
        self.season_info_text = tk.Text(left, height=9, wrap="none", bg="#1e1e1e", fg=FG,
                                        font=(FH, 8), state="disabled")
        self.season_info_text.pack(fill="both", expand=True)
        right = tk.Frame(bottom, bg=BG)
        right.pack(side="left", fill="both", expand=True, padx=(4, 0))
        tk.Label(right, text="本页日志 (同步进『日志』tab):", bg=BG, fg=FG, font=(FH, 9, "bold")).pack(anchor="w")
        self.season_log_text = tk.Text(right, height=9, wrap="word", bg="#1e1e1e", fg=FG,
                                       font=(FH, 8), state="disabled")
        self.season_log_text.pack(fill="both", expand=True)

        # ------------------------------ ⑤ 赛季工具链 (v4.68★) ------------------------------
        # 把命令行四件套搬到界面上, 换季时不用切出助手记命令。
        box4 = tk.LabelFrame(wrap, text=" ⑤ 赛季工具链 (体检 / 资源规则 / 配置表解析 / 老战绩清理) ",
                             bg=BG, fg=GOLD, font=(FH, 10, "bold"), bd=1, relief="groove")
        box4.pack(fill="x", pady=(6, 0))
        rt = tk.Frame(box4, bg=BG)
        rt.pack(fill="x", padx=6, pady=(6, 0))

        self._season_tool_status = {}
        tool_btns = [
            ("check", "赛季体检", self.a_season_check, GREEN, FG),
            ("gen_res", "生成资源规则", self.a_season_gen_res, BTN, BLUE),
            ("analyze", "解析配置表", self.a_season_analyze, BTN, BLUE),
            ("fix_records", "清理老战绩", self.a_season_fix_records, RED, FG),
            ("open_res", "打开资源目录", self.a_season_open_res_dir, BTN, FG),
        ]
        for key, txt, cmd, bgc, fgc in tool_btns:
            col = tk.Frame(rt, bg=BG)
            col.pack(side="left", padx=(0, 8), anchor="n")
            tk.Button(col, text=txt, command=cmd,
                      bg=bgc, fg=fgc, font=(FH, 9, "bold"), padx=8, cursor="hand2"
                      ).pack(fill="x")
            st = tk.Label(col, text="从未", bg=BG, fg=DIM, font=(FH, 7))
            st.pack(fill="x")
            self._season_tool_status[key] = st
        tk.Label(rt, text="结果写进本页左侧日志。体检/清理默认安全, 生成/解析只读本地配置表。\n"
                           "按钮下方显示上次运行时间, 关闭后仍保留。",
                 bg=BG, fg=DIM, font=(FH, 8)).pack(side="left", padx=(6, 0), anchor="n")

        # 启动时刷新一次"上次运行"标记
        self.root.after(100, self._season_refresh_tool_status)

    def _season_refresh_tool_status(self):
        """从 data/tool_state.json 读出各工具上次运行状态, 更新按钮下方标签。"""
        try:
            from core import tool_state as TS
            labels = {
                "check": "赛季体检",
                "gen_res": "生成资源规则",
                "analyze": "解析配置表",
                "fix_records": "清理老战绩",
            }
            for key, base in labels.items():
                st = self._season_tool_status.get(key)
                if st is None:
                    continue
                s = TS.get(key)
                if not s:
                    st.config(text="从未")
                    continue
                txt = "%s: %s" % (base, TS.fmt(s.get("ts")))
                if s.get("exit") != 0:
                    txt += " (exit=%d)" % s.get("exit")
                st.config(text=txt, fg=("#ff6b6b" if s.get("exit") else DIM))
        except Exception as e:
            self._season_log("[tool状态] 刷新失败: %s" % e)

        # 启动时把概览和表格填上(延后一点, 别拖慢开界面)
        self.root.after(200, self._season_refresh_overview)
        self.root.after(400, self._season_show_table)

    # ---------- 赛季数据 tab: 日志 / 状态 / 概览 ----------
    def _season_log(self, msg):
        """season_config.set_logger 的回调 —— 下载/解密跑在子线程里, 一律转回主线程再动界面,
        同时镜像进主日志(『日志』tab 和 log.txt), 出问题好回溯。"""
        s = str(msg)
        try:
            self.root.after(0, self._season_log_append, s)
        except Exception:
            print(s)

    def _season_log_append(self, msg):
        self.log("[赛季] " + msg)
        tw = getattr(self, "season_log_text", None)
        if tw is None:
            return
        try:
            tw.configure(state="normal")
            tw.insert("end", msg + "\n")
            lines = int(tw.index("end-1c").split(".")[0])
            if lines > 900:              # 长时间挂着别把控件撑爆
                tw.delete("1.0", "%d.0" % (lines - 900))
            tw.see("end")
            tw.configure(state="disabled")
        except Exception as e:
            sys.stderr.write("[赛季] 日志框写入失败: %s\n" % e)

    def _season_busy(self, on):
        self._season_running = bool(on)
        for attr in ("season_fetch_btn", "season_push_btn", "season_replace_btn",
                     "season_import_btn", "season_export_btn"):
            b = getattr(self, attr, None)
            if b is None:
                continue
            try:
                b.configure(state=("disabled" if on else "normal"))
            except Exception as e:
                sys.stderr.write("[赛季] 按钮状态切换失败: %s\n" % e)

    def _season_run(self, title, fn):
        """统一入口: 子线程跑 fn()(下载/解密/写表可能几十秒, 不能卡界面), 结束刷新概览。"""
        if getattr(self, "_season_running", False):
            self.log("[赛季] 上一个任务还没跑完, 请稍候", level="WARN")
            messagebox.showinfo("赛季数据", "上一个任务还在跑, 等它结束再点。")
            return
        self._season_busy(True)
        self.season_status_var.set(title + " ...")
        self._season_log("──────── %s ────────" % title)

        def _task():
            try:
                fn()
            except BaseException as e:      # 含 season_config 里的 SystemExit(业务性报错)
                m = _season_errmsg(e)
                self._season_log("失败: " + m)
                # 状态栏只有一行, 取错误信息的首行(多行指引全在日志里)
                self.root.after(0, lambda mm=m: self.season_status_var.set("失败: " + mm.splitlines()[0][:60]))
            else:
                self.root.after(0, lambda: self.season_status_var.set(title + " 完成"))
            finally:
                self.root.after(0, lambda: self._season_busy(False))
                self.root.after(120, self._season_refresh_overview)

        self.log("[赛季] " + title)
        threading.Thread(target=_task, daemon=True).start()

    def _season_refresh_overview(self):
        """左下角『现行数据现状』: 快照表行数 + 现行数据文件清单。

        读快照(十几 MB json)放子线程 —— 否则每次切到本 tab 都会在主线程卡一下(界面僵住)。
        """
        tw = getattr(self, "season_info_text", None)
        if tw is None:
            return
        season = (self.season_no_var.get() or "").strip() or "10"

        def _work():
            from tools import season_config as SC
            try:
                info = SC.summary(season)
            except BaseException as e:
                self.root.after(0, self._season_fill_overview, None,
                                str(e) or e.__class__.__name__)
                return
            self.root.after(0, self._season_fill_overview, info, "")

        threading.Thread(target=_work, daemon=True).start()

    def _season_fill_overview(self, info, err):
        """主线程填『现行数据现状』(io/解析都在子线程做完了)。"""
        tw = getattr(self, "season_info_text", None)
        if tw is None:
            return
        season = (self.season_no_var.get() or "").strip() or "10"
        if err or info is None:
            lines = ["读取失败: %s" % err]
        else:
            lines = ["赛季目录: %s" % info["dir"],
                     "解密快照: %s" % (("已有 %s (%.1f MB)  [%s]"
                                        % (os.path.basename(info["json"]),
                                           (info.get("json_size") or 0) / 1048576.0, season))
                                       if info["has_json"] else "还没有 → 点『一键下载解析』"),
                     "助手当前赛季标记: %s" % (("S%s" % info["current_season"])
                                               if info.get("current_season")
                                               else "(未知: %s)" % info.get("current_season_error", ""))]
            if info.get("error"):
                lines.append("快照读不了: %s" % info["error"])
            for k, v in (info.get("tables") or {}).items():
                lines.append("   %-22s %d" % (k, v))
            lines.append("现行数据文件(%d 个):" % len(info["files"]))
            for label, path, ex, size in info["files"]:
                lines.append("  %s %-26s %s" % ("[有]" if ex else "[无]", label,
                                                ("%.1f KB" % (size / 1024.0)) if ex else ""))
        tw.configure(state="normal")
        tw.delete("1.0", "end")
        tw.insert("end", "\n".join(lines))
        tw.configure(state="disabled")

    def _season_show_table(self):
        """『查看数据』表格: 从解析出的 json 里取棋子/锦囊。

        取数(首次要读十几 MB 的 json)放子线程, 只把结果回主线程填表 —— 点"刷新表格"不卡界面。
        """
        tree = getattr(self, "season_tree", None)
        if tree is None:
            return
        season = (self.season_no_var.get() or "").strip() or "10"
        kind = self.season_kind_var.get()
        kw = self.season_kw_var.get()
        sel = self._season_only_value()
        self._season_last_filter = sel
        self.season_table_var.set("读取中 ...")

        def _work():
            from tools import season_config as SC
            try:
                rows = SC.view_rows(season, kind, kw,
                                    only_current=(sel == "本季"),
                                    season_filter=(sel if sel not in ("", "本季") else None))
            except BaseException as e:
                m = str(e) or e.__class__.__name__
                self.root.after(0, lambda: self.season_table_var.set("读不到: " + m[:60]))
                return
            self.root.after(0, self._season_fill_table, kind, rows)

        threading.Thread(target=_work, daemon=True).start()

    def _season_fill_table(self, kind, rows):
        """主线程填表。读的过程中用户切了"棋子/锦囊"就丢掉这次结果, 免得填成另一种表。"""
        tree = getattr(self, "season_tree", None)
        if tree is None or kind != self.season_kind_var.get():
            return
        tree.delete(*tree.get_children())
        for r in rows:
            tree.insert("", "end", values=(r["id"], r["name"], r["rank"], r["season"], r["shop"],
                                           (r["desc"] or "").replace("\n", " ")[:90]))
        f = getattr(self, "_season_last_filter", "本季")
        self.season_table_var.set("共 %d 行%s" % (
            len(rows),
            ("  [只看赛季 %s]" % f) if f and f != "本季"
            else ("  [只看本季]" if f == "本季" else "  [全部赛季]")))

    def _season_only_value(self):
        """把「只看赛季」输入框翻译成 view_rows 的过滤参数。

        v4.42★ 返回 "" = 不过滤; "本季" = 跟上面「赛季号」一致; "9" = 只看 SeasonID=9。
        以前这里是个「只看本季」复选框, 只能表达"本季/全部", 于是明知道快照里带着往季的
        条目也筛不出来 —— 用户要的就是"能看别的赛季"。
        """
        raw = (self.season_only_var.get() or "").strip()
        if not raw or raw in ("全部", "所有", "全", "all", "*", "0", "-"):
            return ""
        if raw in ("本季", "本赛季", "当前", "cur", "current"):
            return "本季"
        return raw if raw.isdigit() else ""

    def _season_log_report(self, rpt):
        """推送/替换的写入明细(worker 线程调, 逐行走线程安全日志)。"""
        self._season_log("— 写入明细 —")
        for label, desc in (rpt or {}).get("items", []):
            self._season_log("  %-28s %s" % (label, desc))
        if (rpt or {}).get("backup"):
            self._season_log("  备份目录: %s" % rpt["backup"])
        self._season_log("  已顺带刷新助手内存里的表/白名单; 界面武将列表可在『数据管理』里刷新, "
                         "保险起见可重启助手。")

    # ---------- 赛季数据 tab: 按钮动作 ----------
    def a_season_fetch(self):
        """一键下载解析: 联网抓 Config.sgs → 解密 → 解析(只写赛季目录)。"""
        season = (self.season_no_var.get() or "").strip() or "10"
        ver = (self.season_ver_var.get() or "").strip() or None

        def _do():
            from tools import season_config as SC
            SC.fetch_and_parse(season, ver)
            self.root.after(0, self._season_show_table)

        self._season_run("一键下载解析 (赛季 S%s)" % season, _do)

    def a_season_check_update(self):
        """检查官方更新: 只发 HEAD(不下载 8MB), 说清"官方改没改 / 下赛季上线没"。

        用户原话: "赛季更新这个能更新到吗? 加上可以更新" —— 能更新, 但每次点『一键下载解析』
        都会重下 8MB; 有了这一步, 平时点它看一眼就行, 真变了再下载。
        结论摆在对话框里(界面上还有日志), 且【问一句要不要现在下】—— 少一步"看完还得自己找按钮"。
        """
        season = (self.season_no_var.get() or "").strip() or "10"
        ver = (self.season_ver_var.get() or "").strip() or None

        def _do():
            from tools import season_config as SC
            return SC.check_update(season, ver)

        def _done(info):
            if not info:
                return
            self.season_status_var.set(
                "官方更新检查: %s" % ("有更新" if info.get("changed") else
                                     ("没变" if info.get("changed") is False else "判断不了")))
            lines = ["赛季 S%s(路径段 %s)" % (info.get("season"), info.get("version"))]
            rm = info.get("remote") or {}
            if rm.get("status") == 200:
                lines.append("官方 Config.sgs: %d 字节%s"
                             % (rm.get("size") or 0,
                                ("  更新于 " + rm["mtime"]) if rm.get("mtime") else ""))
            else:
                lines.append("取官方 Config.sgs 失败: %s %s" % (rm.get("status"), rm.get("err")))
            for n in (info.get("notes") or []):
                lines.append("· " + n)
            if info.get("next_live"):
                lines.append("· 路径段 %s 上已经是真实配置 → 下赛季可能已发布"
                             % info.get("next_version"))
            if info.get("changed") is False:
                messagebox.showinfo("检查官方更新", "\n".join(lines) + "\n\n不用重下, 现在这份就是最新的。")
                return
            if messagebox.askyesno(
                    "检查官方更新",
                    "\n".join(lines) + "\n\n要现在『一键下载解析』把它抓下来吗?\n"
                    "(只写赛季目录, 不动现行数据; 之后还要再点『推送到现行数据』才生效)"):
                self.a_season_fetch()

        # 复用统一的"子线程 + 概览刷新"通道, 但结果要落到对话框上, 所以这里自己起线程
        def _work():
            try:
                info = _do()
            except Exception as e:
                self.root.after(0, self._season_log, "检查更新失败: %s: %s" % (type(e).__name__, e))
                return
            self.root.after(0, _done, info)
        threading.Thread(target=_work, daemon=True).start()
        self.season_status_var.set("正在检查官方更新(只发 HEAD, 不下载)…")

    def a_season_import(self):
        """导入: 认 导出包目录 / TavernChessConfig.json / .sgs / ChessConf.csv / 名字映射 json。"""
        from tkinter import filedialog
        season = (self.season_no_var.get() or "").strip() or "10"
        choice = messagebox.askyesnocancel(
            "导入本地数据",
            "要导入什么?\n\n  是 = 整个导出包目录(含各格式文件)\n"
            "  否 = 单个文件(TavernChessConfig.json / .sgs / CSV / 名字映射 json)\n"
            "  取消 = 不导了")
        if choice is None:
            return
        if choice:
            src = filedialog.askdirectory(title="选择赛季数据目录(导出包)")
        else:
            src = filedialog.askopenfilename(
                title="选择要导入的文件",
                filetypes=[("赛季数据", "*.json *.sgs *.csv *.tsv"), ("全部文件", "*.*")])
        if not src:
            return

        def _do():
            from tools import season_config as SC
            msg, _sd = SC.import_any(src, season)
            self._season_log(msg)
            self.root.after(0, self._season_show_table)

        self._season_run("导入 %s" % os.path.basename(src.rstrip("\\/") or src), _do)

    def a_season_export(self):
        """导出整包: 把这一份赛季数据按【所有格式】写到选定目录(可直接拿回来导入/留档)。"""
        from tkinter import filedialog
        season = (self.season_no_var.get() or "").strip() or "10"
        # 只查文件在不在 —— 别在主线程 load 十几 MB 的 json(导出时 export_all 自己会读)
        jp = os.path.join(DATA_DIR, "season", "s%s" % season, "TavernChessConfig.json")
        if not os.path.exists(jp):
            messagebox.showwarning("导出", "还没有解密快照, 先点『一键下载解析』或『导入』。\n\n%s" % jp)
            return
        dest = filedialog.askdirectory(title="选择导出目录(会新建 season_s%s 子目录)" % season)
        if not dest:
            return
        out = os.path.join(dest, "season_s%s" % season)

        def _do():
            from tools import season_config as SC
            written = SC.export_all(season, out)
            self._season_log("导出完成(%d 个文件) → %s" % (len(written), out))
            self._season_log("  内容: " + ", ".join(written))

        self._season_run("导出赛季 S%s" % season, _do)

    # ---------- 赛季切换向导 ----------
    def a_season_diff(self):
        """③ 区: 对比『本季快照(要写入的)』和『现行数据(现在用的)』, 只读。

        跑在子线程里(要 load 十几 MB 的快照 json); 明细全部经 season_config.log 落到下面
        那个日志框, 界面上只留一行摘要 —— 明细很长, 挤成一行反而看不清。
        """
        from tools import season_config as SC
        season = (self.season_no_var.get() or "").strip() or SC.current_season()

        def _do():
            d = SC.diff_season(season)
            self.root.after(0, self._season_fill_diff, d)

        self._season_run("赛季切换向导 · 对比 S%s (本季 vs 现行)" % season, _do)

    def _season_fill_diff(self, d):
        """把对比结果压成一行摘要(明细在日志里), 并把它存下来给『一键切换』做二次确认用。"""
        d = d if isinstance(d, dict) else {}
        self._season_last_diff = d
        var = getattr(self, "season_diff_var", None)
        if var is None:
            return
        if not d.get("ok"):
            var.set("对比不了: " + str(d.get("error") or "未知原因").splitlines()[0][:110])
            return
        c, s = d.get("chess") or {}, d.get("spell") or {}
        refs = d.get("refs") or []
        var.set("S%s(要写入: 棋%d/锦%d) vs S%s(现行: 棋%d/锦%d)   "
                "棋子 新增%d 退场%d 改名%d; 锦囊 新增%d 退场%d 改名%d;   "
                "有 %d 个列表引用了会失效的ID"
                % (d.get("season"), c.get("n_new", 0), s.get("n_new", 0),
                   d.get("cur_season") or "?", c.get("n_cur", 0), s.get("n_cur", 0),
                   len(c.get("added") or []), len(c.get("removed") or []), len(c.get("renamed") or []),
                   len(s.get("added") or []), len(s.get("removed") or []), len(s.get("renamed") or []),
                   len(refs)))
        if refs:
            self.log("[赛季] 换表后要跟着改的列表: " + "; ".join(
                "%s(%d个)" % (r.get("list"), r.get("count")) for r in refs), level="WARN")

    def a_season_wizard_go(self):
        """③ 区『一键切换』= 备份 + 按本季快照推送(合并) + 重建精选/刷新武将表。

        故意【不】直接等价于『推送』按钮: 那条路要先点『对比差异』(或至少确认一次),
        因为这是本页唯一会改现行数据的入口, 而改错了现象是"卡名突然变裸ID/列表静默失效",
        很难和别的问题区分开。
        """
        from tools import season_config as SC
        season = (self.season_no_var.get() or "").strip() or SC.current_season()
        d = getattr(self, "_season_last_diff", None) or {}
        if d.get("season") != season or not d.get("ok"):
            if not messagebox.askyesno(
                    "赛季切换向导",
                    "还没有对 S%s 做过差异对比。\n\n"
                    "建议先点『对比差异』看清退场/改名的卡和受影响的列表 —— 换错了要翻 "
                    "data/_backup 手工还原。\n\n要直接开始换表吗?" % season):
                self.a_season_diff()
                self.log("[赛季] 已先跑『对比差异』—— 看完下面的日志再点『一键切换』。", level="WARN")
                return
        else:
            c, s = d.get("chess") or {}, d.get("spell") or {}
            refs = d.get("refs") or []
            warn = ("  ⚠ 有 %d 个列表引用了会失效的 ID: %s\n"
                    % (len(refs), ", ".join(r.get("list") or "" for r in refs))) if refs else ""
            if not messagebox.askyesno(
                    "一键切换 = 备份 + 推送本季",
                    "按『S%s』的快照写进现行数据(合并模式, 保留旧赛季的键):\n\n"
                    "  棋子: 本季 %d 个 (新增 %d / 退场 %d / 改名 %d)\n"
                    "  锦囊: 本季 %d 个 (新增 %d / 退场 %d / 改名 %d)\n"
                    "%s\n"
                    "会自动先整份备份到 data/_backup/, 并顺带重建武将精选 + 刷新界面武将表。\n"
                    "要继续吗?"
                    % (season, c.get("n_new", 0), len(c.get("added") or []),
                       len(c.get("removed") or []), len(c.get("renamed") or []),
                       s.get("n_new", 0), len(s.get("added") or []),
                       len(s.get("removed") or []), len(s.get("renamed") or []), warn)):
                return
        self.log("[赛季] 一键切换开始: S%s → 现行数据(合并)" % season)
        self._season_do_write(season, "merge")

    def a_season_push(self):
        """推送: 名字映射按 ID 合并(旧赛季/旧键都留着), CSV 列序沿用旧表 —— 最稳的换表方式。"""
        season = (self.season_no_var.get() or "").strip() or "10"
        if not messagebox.askyesno(
                "推送到现行数据",
                "把赛季 S%s 的解析结果写进现行数据:\n\n"
                "  · ChessConf.csv / ChessSpellConf.csv  (列序沿用旧表)\n"
                "  · data/chess.json / cards.json        (按 ID 合并, 保留旧赛季)\n"
                "  · data/tavern_chess.json              (本季商店武将, 整份换)\n"
                "  · data/tavern_spells.json             (本季可用锦囊)\n"
                "  · data/skills.json / 白名单 / 赛季标记\n\n"
                "写之前会自动整份备份到 data/_backup/。要继续吗?" % season):
            return
        self._season_do_write(season, "merge")

    def a_season_replace(self):
        """替换: 完全按这份快照重建(表外条目一律删掉) —— 想彻底对齐官方配置时用。"""
        season = (self.season_no_var.get() or "").strip() or "10"
        if not messagebox.askyesno(
                "替换现行数据",
                "⚠ 替换 = 完全按赛季 S%s 的快照重建现行数据:\n"
                "  · 棋/锦囊/技能映射里这份快照没有的条目【会被删掉】(旧赛季的变体卡名也没了)\n"
                "  · CSV 列序按本次快照重排(不是沿用旧表)\n\n"
                "只在你确定要彻底对齐官方配置时用; 一般换表用『推送』就够了。\n"
                "写之前会自动整份备份到 data/_backup/。要继续吗?" % season):
            return
        self._season_do_write(season, "replace")

    def _season_do_write(self, season, mode):
        """推送/替换公共部分 —— hook_after 里按勾选顺带重建武将精选。"""
        rebuild = bool(self.season_rebuild_picker_var.get())

        def _hook():
            r = None
            if rebuild:
                r = self.ops.build_hero_picker()
            # v4.42★ 刷新武将表【不能】跟"顺带重建武将精选"共用一个 if —— 这正是用户问的
            #   "那个全部武将数据怎么更新呢？就是招募tab中、多选tab中、遣散tab中"。
            #   原来 _refresh_generals_for_ui 只在勾了『顺带重建武将精选』时才执行(而且它是
            #   全项目【唯一】的调用点), 所以: 没勾那个框 → 推送/替换之后 ChessConf.csv 已经
            #   换新, 可六个面板的『全部武将』还是助手启动时那份旧表, 只能重启助手才更新。
            #   "刷新武将表"和"重建 hero_picker.json"本来是两件事, 解耦。
            self.root.after(0, self._refresh_generals_for_ui)
            return r or ("已重建武将精选" if rebuild else "已刷新武将表")

        def _do():
            from tools import season_config as SC
            rpt = SC.push_to_current(season, mode, do_skills=True, hook_after=_hook)
            self._season_log_report(rpt)
            self.root.after(0, self._season_show_table)

        self._season_run(("替换" if mode == "replace" else "推送") + "赛季 S%s → 现行数据" % season, _do)

    # ---------- 赛季工具链按钮 (v4.68★) ----------
    def _season_run_tool(self, label, args, timeout=300):
        """用子进程跑一个 tools 目录下的脚本, 输出写进赛季 tab 的日志。"""
        def _do():
            try:
                self._season_log("--- 开始: %s ---" % label)
                cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                cmd = [sys.executable, "-X", "utf8"] + args
                p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=timeout)
                out = (p.stdout or "") + (p.stderr or "")
                for line in out.splitlines()[:600]:  # 防巨长输出刷屏
                    self._season_log(line)
                self._season_log("--- 结束: %s (exit=%d) ---" % (label, p.returncode))
                # 体检/生成/解析都是只读或半只读, 不需要刷新 UI 表格
                if "season_check" in args or "gen_res_redirect" in args:
                    self.root.after(0, self._season_refresh_overview)
                # 记录运行状态, 方便按钮下方显示"上次是哪天"
                try:
                    from core import tool_state as TS
                    key = None
                    for k, path in [
                        ("check", "tools/season_check.py"),
                        ("gen_res", "tools/gen_res_redirect.py"),
                        ("analyze", "tools/analyze_season_config.py"),
                        ("fix_records", "tools/fix_old_records.py"),
                    ]:
                        if path in args:
                            key = k
                            break
                    if key:
                        TS.mark(key, exit_code=p.returncode, summary=label)
                        self.root.after(0, self._season_refresh_tool_status)
                except Exception as e:
                    self._season_log("[工具状态] 记录失败: %s" % e)
            except Exception as e:
                self._season_log("[错误] %s: %s" % (label, e))
        threading.Thread(target=_do, daemon=True).start()

    def a_season_check(self):
        """赛季体检: 对比配置表与现行数据, 生成 Markdown 报告。"""
        self._season_run_tool("赛季体检", ["tools/season_check.py", "--md"])

    def a_season_gen_res(self):
        """生成当前赛季的资源替换规则(棋子/主公 + 用户自定义规则)。"""
        self._season_run_tool("生成资源规则", ["tools/gen_res_redirect.py"])

    def a_season_analyze(self):
        """解析 TavernChessConfig.json 生成人可读的配置表总览。"""
        self._season_run_tool("解析配置表", ["tools/analyze_season_config.py"])

    def a_season_fix_records(self):
        """一次性修复老 records.json 里被错用的 placement。"""
        if not messagebox.askyesno("清理老战绩",
            "会把 records.json 里 "
            "老记录的旧 placement 移到 battle_code, "
            "名次改成「未知」(先备份到 data/_backup/)。\n\n继续?"):
            return
        self._season_run_tool("清理老战绩", ["tools/fix_old_records.py", "--apply"])

    def a_season_open_res_dir(self):
        """打开资源替换规则所在的参考目录。"""
        d = os.path.join(BASE_DIR, "参考_配置表", "资源替换")
        os.makedirs(d, exist_ok=True)
        try:
            os.startfile(d)
        except Exception as e:
            self._season_log("打开目录失败: %s" % e)

    # ---------- Tab: 技能库 (v4.43★) ----------
    def _build_skill_tab(self):
        """技能库: 查 ChessSkillConf.SkillID / 主公ID / 主公技能ID, 并且能直接把 ID 用出去。

        用户原话: "942311 是它的随征技能ID(ChessSkillConf.SkillID), 三张表里都不存在, 所以只能靠
        『纯数字 ID 直接加入』来加。那没有查询这个 ChessSkillConf.SkillID 的吗? 然后你能查看主公ID
        和主公技能ID吗? 然后开一个tab页把技能放进去可以使用?"

        确实没有得查 —— 『赛季数据』tab 的『查看数据』只有棋子/锦囊两个单选, 而
        chess.json / cards.json / tavern_chess.json 全是【棋子】表, 技能和主公压根不在里面。
        于是在这里补上三张表(共 2200+2200+50+63 条)的统一查询入口, 并解决"查到了怎么用":

          ① 查    : 武将技能 / 主公 / 主公技能 三类, 关键词按 ID·名字·说明 匹配
          ② 看清  : 说明列会补全词条(942311 光看 SkillDesc 只有"随征"两个字),
                     选一行 → 下面文本框给完整说明(表格列会被截断)
          ③ 用出去: 选中(可多选) → 复制ID / 一键加入『随征卡牌ID』(= 随征真正吃的那张表)

        数据源与『赛季数据』一致: 优先读那份解密快照, 没有快照就退回 data/*.json(推送流程写的)。
        """
        from tools import season_config as SC
        wrap = self._make_scrollable_tab(self.tab_skill, pady=8)

        # ------------------------------ ① 查询 ------------------------------
        box1 = tk.LabelFrame(wrap, text=" ① 查询 (读哪份数据 = 下面『赛季号』那份解密快照; 没快照就退回 data/*.json) ",
                             bg=BG, fg=GOLD, font=(FH, 10, "bold"), bd=1, relief="groove")
        box1.pack(fill="x", pady=(0, 6))
        r1 = tk.Frame(box1, bg=BG)
        r1.pack(fill="x", padx=6, pady=6)

        self.skill_kind_var = tk.StringVar(value="skill")
        for txt, val in (("武将技能", "skill"), ("主公", "lord"), ("主公技能", "lordskill")):
            tk.Radiobutton(r1, text=txt, variable=self.skill_kind_var, value=val,
                           command=self._skill_show_table, bg=BG, fg=FG, selectcolor="#33333f",
                           activebackground=BG, activeforeground=FG, font=(FH, 9)).pack(side="left")

        tk.Label(r1, text="赛季号:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left", padx=(12, 2))
        self.skill_season_var = tk.StringVar(value=SC.current_season())
        e1 = tk.Entry(r1, textvariable=self.skill_season_var, width=4, bg=BTN, fg=FG,
                      insertbackground=FG, font=(FH, 9))
        e1.pack(side="left")
        e1.bind("<Return>", lambda _e: self._skill_show_table())

        tk.Label(r1, text="ID/名字/说明:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left", padx=(12, 2))
        self.skill_kw_var = tk.StringVar(value="")
        e2 = tk.Entry(r1, textvariable=self.skill_kw_var, width=16, bg=BTN, fg=FG,
                      insertbackground=FG, font=(FH, 9))
        e2.pack(side="left")
        e2.bind("<Return>", lambda _e: self._skill_show_table())    # 回车查; 表很大, 不逐键刷
        tk.Button(r1, text="查询", command=self._skill_show_table, bg=BTN, fg=FG,
                  font=(FH, 9, "bold"), padx=8, cursor="hand2").pack(side="left", padx=(6, 0))
        self.skill_count_var = tk.StringVar(value="点『查询』或切到本页自动加载")
        tk.Label(r1, textvariable=self.skill_count_var, bg=BG, fg=DIM, font=(FH, 9)).pack(side="right", padx=6)

        # ------------------------------ ② 结果 ------------------------------
        box2 = tk.LabelFrame(wrap, text=" ② 结果 (双击一行 = 复制它的ID; 说明列过长会被截断, 看下面那个框) ",
                             bg=BG, fg=GOLD, font=(FH, 10, "bold"), bd=1, relief="groove")
        box2.pack(fill="both", expand=True, pady=(0, 6))
        tf = tk.Frame(box2, bg=BG)
        tf.pack(fill="both", expand=True, padx=6, pady=(6, 2))
        cols = ("id", "name", "type", "tag", "desc")
        self.skill_tree = ttk.Treeview(tf, columns=cols, show="headings", height=12, selectmode="extended")
        for c, t, w in (("id", "ID", 95), ("name", "名字", 130), ("type", "类型", 75),
                        ("tag", "标记", 190), ("desc", "说明", 600)):
            self.skill_tree.heading(c, text=t)
            self.skill_tree.column(c, width=w, anchor="w")
        vs = tk.Scrollbar(tf, orient="vertical", command=self.skill_tree.yview)
        self.skill_tree.configure(yscrollcommand=vs.set)
        self.skill_tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        self.skill_tree.bind("<<TreeviewSelect>>", self._skill_on_select)
        self.skill_tree.bind("<Double-1>", self._skill_copy_id)

        tk.Label(box2, text="选中行完整说明:", bg=BG, fg=FG, font=(FH, 9, "bold")).pack(anchor="w", padx=8)
        self.skill_detail_text = tk.Text(box2, height=4, wrap="word", bg="#1e1e1e", fg=FG,
                                        font=(FH, 9), state="disabled")
        self.skill_detail_text.pack(fill="x", padx=6, pady=(0, 6))

        # ------------------------------ ③ 拿去用 ------------------------------
        box3 = tk.LabelFrame(wrap, text=" ③ 拿去用 (可 Ctrl/Shift 多选) ",
                             bg=BG, fg=GOLD, font=(FH, 10, "bold"), bd=1, relief="groove")
        box3.pack(fill="x")
        r3 = tk.Frame(box3, bg=BG)
        r3.pack(fill="x", padx=6, pady=6)
        tk.Button(r3, text="复制选中ID", command=self._skill_copy_id, bg=BTN, fg=BLUE,
                  font=(FH, 9, "bold"), padx=8, cursor="hand2").pack(side="left", padx=(0, 8))
        tk.Button(r3, text="加入『随征卡牌ID』(保存并推油猴端)", command=self._skill_add_followup,
                  bg=GREEN, fg=FG, font=(FH, 9, "bold"), padx=8, cursor="hand2").pack(side="left")
        tk.Label(box3,
                 text="随征(ChessSkillConf 里 SkillDesc=随征 的那些)吃的就是这个 ID —— 以前只能靠『手动添加』敲数字; "
                      "棋子名、技能名同名的用『标记』列区分(如 942311 黄巾兵 = 随征, 需选目标)。",
                 bg=BG, fg=DIM, font=(FH, 8), wraplength=760, justify="left").pack(anchor="w", padx=8, pady=(0, 6))

        self._skill_rows = []

    def _skill_show_table(self):
        """取数(首次要读十几 MB 快照)放子线程, 只把结果回主线程填表 —— 切/点都不卡界面。"""
        tree = getattr(self, "skill_tree", None)
        if tree is None:
            return
        season = (self.skill_season_var.get() or "").strip() or "10"
        kind = self.skill_kind_var.get()
        kw = self.skill_kw_var.get()
        self.skill_count_var.set("读取中 ...")

        def _work():
            from tools import season_config as SC
            try:
                rows = SC.view_skills(season, kind, kw)
            except BaseException as e:
                m = str(e) or e.__class__.__name__
                self.root.after(0, lambda: self.skill_count_var.set("读不到: " + m[:60]))
                return
            self.root.after(0, self._skill_fill_table, kind, rows, season)

        threading.Thread(target=_work, daemon=True).start()

    def _skill_fill_table(self, kind, rows, season):
        """主线程填表。读的过程中用户切了类别就丢掉这次结果, 免得填成另一张表。"""
        tree = getattr(self, "skill_tree", None)
        if tree is None or kind != self.skill_kind_var.get():
            return
        tree.delete(*tree.get_children())
        self._skill_rows = list(rows)
        for r in rows:
            tree.insert("", "end", values=(r["id"], r["name"], r["type"], r["tag"],
                                           (r["desc"] or "").replace("\n", " ")))
        label = {"skill": "武将技能", "lord": "主公", "lordskill": "主公技能"}.get(kind, kind)
        self.skill_count_var.set("赛季 S%s · %s 共 %d 行" % (season, label, len(rows)))
        self._skill_set_detail("在上表选一行 → 这里显示完整说明。\n"
                               "（942311 这种 SkillDesc 只有一个词的, 说明里会把 ChessClientKeywordConf "
                               "的词条全文补上, 免得查出来等于没查。）")

    def _skill_set_detail(self, txt):
        tw = getattr(self, "skill_detail_text", None)
        if tw is None:
            return
        try:
            tw.configure(state="normal")
            tw.delete("1.0", "end")
            tw.insert("end", txt)
            tw.configure(state="disabled")
        except Exception as e:
            sys.stderr.write("[技能库] 说明框写入失败: %s\n" % e)

    def _skill_on_select(self, _evt=None):
        """把选中行的完整信息(表格列截断的部分)显示到下面那个文本框。"""
        tree = getattr(self, "skill_tree", None)
        if tree is None:
            return
        sel = tree.selection()
        if not sel:
            return
        vals = list(tree.item(sel[0], "values") or [])
        vals += ["", "", "", "", ""]
        gid, name, kind, tag, desc = vals[:5]
        self._skill_set_detail("ID: %s\n名字: %s      类型: %s      %s\n说明: %s"
                               % (gid, name, kind, tag, desc))

    def _skill_selected_rows(self):
        """当前选中的行 → [{"id","name"}]。双击/复制/加入都走它, 保证三处行为一致。"""
        tree = getattr(self, "skill_tree", None)
        if tree is None:
            return []
        out = []
        for iid in tree.selection():
            vals = list(tree.item(iid, "values") or [])
            if vals and str(vals[0]).strip():
                out.append({"id": str(vals[0]).strip(), "name": str(vals[1]).strip() if len(vals) > 1 else ""})
        return out

    def _skill_copy_id(self, _evt=None):
        """双击/按钮: 把选中行的 ID 复制到剪贴板(多行按行分开)。"""
        rows = self._skill_selected_rows()
        if not rows:
            self.log("[技能库] 没选中任何行", level="WARN")
            return
        txt = "\n".join(r["id"] for r in rows)
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(txt)
        except Exception as e:
            self.log(f"[技能库] 写剪贴板失败: {e}", level="ERROR")
            return
        self.log("[技能库] 已复制 %d 个ID: %s" % (len(rows), txt.replace("\n", ", ")))

    def _skill_add_followup(self):
        """选中行 → 直接灌进『随征卡牌ID』并保存/推送。

        这就是用户问的"把技能放进去可以使用": 随征吃的正是 ChessSkillConf.SkillID,
        而在左侧『全部武将』里永远搜不到技能名(那是棋子表), 所以必须给一条从技能库直达的路。
        """
        rows = self._skill_selected_rows()
        if not rows:
            self.log("[技能库] 没选中任何行", level="WARN")
            return
        panel = getattr(self, "followup_panel", None)
        if panel is None:
            self.log("[技能库] 找不到『随征卡牌ID』面板", level="ERROR")
            return
        entries = [{"id": r["id"], "name": r["name"],
                    "display": f"{r['name']} ({r['id']})" if r["name"] else r["id"]}
                   for r in rows]
        added, dup = panel.add_entries(entries)
        ids = panel.save_config()      # 落盘 + 推油猴端(和面板自己的『保存配置』同一条路)
        self.log("[技能库] 加入『随征卡牌ID』: 新增 %d 个, 重复 %d 个 → 列表现有 %d 个%s"
                 % (added, dup, len(ids), ("; " + ", ".join(r["id"] for r in rows)) if added else ""))
        if not added:
            messagebox.showinfo("技能库", "选中的 ID 都已经在『随征卡牌ID』里了, 没有新增。")

    # ---------- Tab: 主公技能 (v4.46★) ----------
    def _build_lord_skill_tab(self):
        """主公技能: 在对局中【主动使用】主公技能, 直接推送到当前游戏页。

        用户原话: "再开一个 tab 页里面能够使用主公技能推送到现在的网页里面? 就是我想在对局中
        使用别的主公技能, 你像 1053, 这个主公技能, 就可以挑选一个主公技能使用, 你能基于这个
        原理进行实现主公技能使用吗? 还有这些被动的能使用吗? 直接算被动满足条件?"

        原理(完整推导见 core/remote_ops.lord_skill_use 的注释): 游戏客户端施放主公技能最终
        就是发一条 cmsg.CReqChessUseSkill ——
            TavernChessZhuGongBaseSkill.Use()
              → TavernChessGameContext.UseSkill(ZHUGONG, SkillID)
              → manager.ReqChessUseSkill(SkillID, chessMinionTyp, goodsID[], chessID[], buy)
        脚本侧调的就是最后那个 manager.ReqChessUseSkill(...), 与游戏自身完全同源。

        为什么必须单开一页: 主公技能是【对局内】决定的(选将选中一个 + 主公自带一个, 1053「面涅」
        这类还能再挑一个), 助手离线表里没有"你本局拿到了哪几个", 只能问
        manager.SelfInfo.generalSkillList。『技能库』页只管"按 ID 查得到", 管不了"能不能用"。

        被动技能(SkillType=2): 客户端没有施放入口, 发包也会被服务器拒 —— 本页把它们的可用性
        直接标成"不可用"并写出原因, 而不是让你点了才发现没反应(那是 v4.41 挑牌窗踩过的坑)。
        """
        from tools import season_config as SC
        wrap = self._make_scrollable_tab(self.tab_lord, pady=8)

        # ------------------------------ ① 读取本局状态 ------------------------------
        box1 = tk.LabelFrame(
            wrap,
            text=" ① 本局状态 (问游戏活体 manager.SelfInfo.generalSkillList —— 离线表看不到你本局拿了哪几个) ",
            bg=BG, fg=GOLD, font=(FH, 10, "bold"), bd=1, relief="groove")
        box1.pack(fill="x", pady=(0, 6))
        r1 = tk.Frame(box1, bg=BG)
        r1.pack(fill="x", padx=6, pady=6)
        tk.Button(r1, text="读取本局主公技能", command=self._lord_read_state, bg=GREEN, fg=FG,
                  font=(FH, 9, "bold"), padx=8, cursor="hand2").pack(side="left")
        self.lord_state_var = tk.StringVar(
            value="还没读取 —— 点左边按钮 (需要: 游戏页已连上助手 + 正在对局中)")
        tk.Label(r1, textvariable=self.lord_state_var, bg=BG, fg=DIM, font=(FH, 9),
                 wraplength=620, justify="left").pack(side="left", padx=8)

        # ------------------------------ ② 本局技能 ------------------------------
        box2 = tk.LabelFrame(wrap, text=" ② 本局技能 (双击一行 = 把它填进下面『技能ID』; 被动技能不能主动用) ",
                             bg=BG, fg=GOLD, font=(FH, 10, "bold"), bd=1, relief="groove")
        box2.pack(fill="both", expand=True, pady=(0, 6))
        tf = tk.Frame(box2, bg=BG)
        tf.pack(fill="both", expand=True, padx=6, pady=(6, 2))
        cols = ("id", "name", "type", "state", "cost", "desc")
        self.lord_tree = ttk.Treeview(tf, columns=cols, show="headings", height=8)
        for c, t, w in (("id", "技能ID", 80), ("name", "技能名", 110), ("type", "类型", 60),
                        ("state", "本回合", 80), ("cost", "消耗", 55),
                        ("desc", "说明 / 不能用的原因", 560)):
            self.lord_tree.heading(c, text=t)
            self.lord_tree.column(c, width=w, anchor="w")
        vs = tk.Scrollbar(tf, orient="vertical", command=self.lord_tree.yview)
        self.lord_tree.configure(yscrollcommand=vs.set)
        self.lord_tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        self.lord_tree.bind("<Double-1>", self._lord_pick_to_use)
        self.lord_tree.bind("<<TreeviewSelect>>", self._lord_on_select)

        tk.Label(box2, text="选中行完整说明:", bg=BG, fg=FG, font=(FH, 9, "bold")).pack(anchor="w", padx=8)
        self.lord_detail_text = tk.Text(box2, height=4, wrap="word", bg="#1e1e1e", fg=FG,
                                        font=(FH, 9), state="disabled")
        self.lord_detail_text.pack(fill="x", padx=6, pady=(0, 6))

        # ------------------------------ ③ 使用 ------------------------------
        box3 = tk.LabelFrame(wrap, text=" ③ 使用 (发包 cmsg.CReqChessUseSkill —— 与游戏自身同一条路) ",
                             bg=BG, fg=GOLD, font=(FH, 10, "bold"), bd=1, relief="groove")
        box3.pack(fill="x")
        r3 = tk.Frame(box3, bg=BG)
        r3.pack(fill="x", padx=6, pady=6)
        tk.Label(r3, text="技能ID:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left")
        self.lord_use_id_var = tk.StringVar(value="")
        tk.Entry(r3, textvariable=self.lord_use_id_var, width=8, bg=BTN, fg=FG,
                 insertbackground=FG, font=(FH, 9)).pack(side="left", padx=(2, 2))
        tk.Button(r3, text="从技能库选", command=lambda: open_lord_skill_picker(
            self.root, lambda sid: self.lord_use_id_var.set(sid)),
            bg=BTN, fg=BLUE, activebackground=BTN, activeforeground=FG,
            font=(FH, 8, "bold"), relief="flat", padx=6, cursor="hand2").pack(side="left", padx=(0, 10))
        tk.Label(r3, text="目标goodsID:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left")
        self.lord_use_goods_var = tk.StringVar(value="")
        tk.Entry(r3, textvariable=self.lord_use_goods_var, width=14, bg=BTN, fg=FG,
                 insertbackground=FG, font=(FH, 9)).pack(side="left", padx=(2, 10))
        tk.Label(r3, text="目标chessID:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left")
        self.lord_use_chess_var = tk.StringVar(value="")
        tk.Entry(r3, textvariable=self.lord_use_chess_var, width=12, bg=BTN, fg=FG,
                 insertbackground=FG, font=(FH, 9)).pack(side="left", padx=(2, 10))
        tk.Label(r3, text="势力/兵种:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left")
        self.lord_use_minion_var = tk.StringVar(value="")
        tk.Entry(r3, textvariable=self.lord_use_minion_var, width=5, bg=BTN, fg=FG,
                 insertbackground=FG, font=(FH, 9)).pack(side="left", padx=(2, 10))
        self.lord_use_buy_var = tk.BooleanVar(value=False)
        tk.Checkbutton(r3, text="buy", variable=self.lord_use_buy_var, bg=BG, fg=FG,
                       selectcolor="#33333f", activebackground=BG,
                       font=(FH, 9)).pack(side="left")
        tk.Button(r3, text="使用这个主公技能", command=self._lord_use, bg=RED, fg=FG,
                  font=(FH, 9, "bold"), padx=10, cursor="hand2").pack(side="right")

        r4 = tk.Frame(box3, bg=BG)
        r4.pack(fill="x", padx=8, pady=(0, 6))
        tk.Label(r4, text=(
            "· 不需要选目标的技能 → 三个目标框【全部留空】, 直接点『使用这个主公技能』。\n"
            "· 需要选目标的(点兵/戎弁/密诏这类「选择1个友方」) → 填目标的 goodsID 或 chessID "
            "(逗号分隔可多个; 两个都填也安全 —— 游戏自己的窗口也是两个都带)。\n"
            "· 被动技能(『类型』列写被动)【不能主动使用】: 游戏客户端没有施放入口, 发包也会被服务器拒, "
            "所以本页直接标出可用性, 不让你点了没反应。被动是否触发由服务器按条件判定 —— 想「帮它满足"
            "条件」请用『剧本』/招募设置去做(例如自动留够某类卡)。\n"
            "· 1053「面涅」这类「挑选1个主公技能」的技能: 先直接使用它, 拿到的新技能会出现在 "
            "SelfInfo.generalSkillList 里 → 再点『读取本局主公技能』, 新技能就在表里, 双击后单独使用它。\n"
            "· 本季全部主公技能的名字/主动被动来自离线表(赛季 S%s); 游戏配置表拿得到时会以游戏为准。"
            % SC.current_season()),
            bg=BG, fg=DIM, font=(FH, 8), wraplength=800, justify="left").pack(anchor="w")

        # v4.156★ 岳飞·精忠 选势力(诊断): 协议还没在真机确认, 先给"抓窗口"和"手动回答"两个按钮。
        #   用法: 选岳飞 → 等"选势力"窗口弹出 → 先『抓窗口(诊断)』把协议 JSON 发回来 → 我们据实写自动回答;
        #   急着用的话, 填一个势力点『手动回答(势力)』试一次(协议确认前 best-effort)。
        box_lf = tk.LabelFrame(wrap, text=" 岳飞·精忠 选势力 (诊断: 抓窗口协议 / 手动回答) ",
                               bg=BG, fg=GOLD, font=(FH, 10, "bold"), bd=1, relief="groove")
        box_lf.pack(fill="x", pady=(6, 0))
        rlf = tk.Frame(box_lf, bg=BG)
        rlf.pack(fill="x", padx=6, pady=6)
        self.lord_faction_var = tk.StringVar(value="")
        tk.Entry(rlf, textvariable=self.lord_faction_var, width=10, bg=BTN, fg=FG,
                 insertbackground=FG, font=(FH, 9)).pack(side="left", padx=(2, 4))
        tk.Button(rlf, text="手动回答(势力)", command=self._lord_faction_answer, bg=GREEN, fg=FG,
                  font=(FH, 9, "bold"), padx=8, cursor="hand2").pack(side="left", padx=(0, 8))
        tk.Button(rlf, text="抓窗口(诊断)", command=self._lord_faction_probe, bg=BLUE, fg=FG,
                  font=(FH, 9, "bold"), padx=8, cursor="hand2").pack(side="left")
        tk.Label(rlf, text="(选岳飞→窗口弹出后: 先『抓窗口』把协议发我; 再填势力『手动回答』试一次)",
                 bg=BG, fg=DIM, font=(FH, 8)).pack(side="left", padx=(6, 0))
        self.lord_faction_text = tk.Text(box_lf, height=4, wrap="word", bg="#1e1e1e", fg=FG,
                                         font=(FH, 9), state="disabled")
        self.lord_faction_text.pack(fill="x", padx=6, pady=(0, 6))

        # ------------------------------ ④ 自动挑目标 (v4.47★) ------------------------------
        # 用户最常问的两句: "目标要填哪个?" / "填了没生效"。
        # 需要选目标的技能(点兵/戎弁/密诏这类"选择1个友方")要填的是【本局实例 goodsID】——
        # 游戏商店界面上看不到这个号, 只能从协议(LIVE)里读。所以让助手自己挑并填好,
        # 同时把"为什么挑它"写进日志; 用户不放心可以改完再点使用。
        box4 = tk.LabelFrame(wrap, text=" ④ 自动挑目标 (从 LIVE 的备战区/上阵里按策略挑一张填进上面的目标框) ",
                             bg=BG, fg=GOLD, font=(FH, 10, "bold"), bd=1, relief="groove")
        box4.pack(fill="x", pady=(6, 0))
        r5 = tk.Frame(box4, bg=BG)
        r5.pack(fill="x", padx=6, pady=6)
        tk.Label(r5, text="策略:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left")
        self.lord_target_mode = tk.StringVar(value="星级最高")
        ttk.Combobox(r5, textvariable=self.lord_target_mode, width=10, state="readonly",
                     values=("星级最高", "费用最高", "金色优先", "备战区最前")).pack(side="left", padx=(2, 10))
        tk.Button(r5, text="自动挑目标并填入", command=self._lord_auto_target, bg=BTN, fg=FG,
                  font=(FH, 9, "bold"), padx=8, cursor="hand2").pack(side="left")
        self.lord_target_var = tk.StringVar(value="还没挑 —— 不需要选目标的技能用不上这一步")
        tk.Label(r5, textvariable=self.lord_target_var, bg=BG, fg=DIM, font=(FH, 9),
                 wraplength=560, justify="left").pack(side="left", padx=8)

        # ------------------------------ ⑤ 被动技能监控 (v4.47★) ------------------------------
        # 被动技能不能主动发(客户端没有施放入口), 但"它到底触发了没 / 还差什么条件"是能看的 ——
        # 这才是对被动唯一有效的辅助方式。判据: 本回合已用名单(游戏给得到才有) + 技能说明里的条件。
        box5 = tk.LabelFrame(wrap, text=" ⑤ 被动技能监控 (被动不能主动发; 这里只告诉你「触发了没 / 条件是什么」) ",
                             bg=BG, fg=GOLD, font=(FH, 10, "bold"), bd=1, relief="groove")
        box5.pack(fill="x", pady=(6, 0))
        r6 = tk.Frame(box5, bg=BG)
        r6.pack(fill="x", padx=6, pady=(6, 0))
        self.lord_auto_var = tk.BooleanVar(value=False)
        tk.Checkbutton(r6, text="每 5 秒自动读一次本局状态(用来做「该用还没用」提醒)",
                       variable=self.lord_auto_var, command=self._lord_auto_toggle,
                       bg=BG, fg=FG, selectcolor="#33333f", activebackground=BG,
                       font=(FH, 9)).pack(side="left")
        self.lord_passive_var = tk.StringVar(value="")
        tk.Label(r6, textvariable=self.lord_passive_var, bg=BG, fg=DIM,
                 font=(FH, 9)).pack(side="left", padx=8)
        self.lord_passive_text = tk.Text(box5, height=5, wrap="word", bg="#1e1e1e", fg=FG,
                                         font=(FH, 9), state="disabled")
        self.lord_passive_text.pack(fill="x", padx=6, pady=6)

    def _lord_offline_map(self):
        """本季主公技能的离线表 {skillID(int): row} —— 游戏配置表问不到时用来补名字/类型/说明。"""
        cache = getattr(self, "_lord_offline_cache", None)
        if cache is not None:
            return cache
        out = {}
        try:
            from tools import season_config as SC
            for r in SC.view_skills(SC.current_season(), "lordskill"):
                try:
                    out[int(r["id"])] = r
                except (TypeError, ValueError, KeyError):
                    continue
        except Exception as e:
            self.log(f"[主公技能] 读离线主公技能表失败(不影响使用, 只是名字可能缺): {e}", level="WARN")
        self._lord_offline_cache = out
        return out

    def _lord_name_of(self, gid):
        """主公ID → 主公名(离线表)。查不到就返回空串。"""
        cache = getattr(self, "_lord_hero_cache", None)
        if cache is None:
            cache = {}
            try:
                from tools import season_config as SC
                for r in SC.view_skills(SC.current_season(), "lord"):
                    cache[str(r["id"])] = str(r["name"])
            except Exception as e:
                self.log(f"[主公技能] 读主公名表失败: {e}", level="WARN")
            self._lord_hero_cache = cache
        return cache.get(str(gid), "")

    def _lord_set_detail(self, txt):
        tw = getattr(self, "lord_detail_text", None)
        if tw is None:
            return
        try:
            tw.configure(state="normal")
            tw.delete("1.0", "end")
            tw.insert("end", txt)
            tw.configure(state="disabled")
        except Exception as e:
            sys.stderr.write("[主公技能] 说明框写入失败: %s\n" % e)

    def _lord_show_hint(self):
        """切到本页时的提示(不联网)。已读过状态就不覆盖。"""
        tree = getattr(self, "lord_tree", None)
        if tree is None or tree.get_children():
            return
        off = self._lord_offline_map()
        n_active = sum(1 for r in off.values() if str(r.get("tag") or "") == "主动")
        self._lord_set_detail(
            "点上面的『读取本局主公技能』把本局（对局内）拿到的技能读出来。\n"
            "本季离线表里有 %d 个主公技能（主动 %d / 被动 %d）—— 离线表只是名字来源，"
            "「你这一局到底有哪几个」只有游戏自己知道。\n"
            "被动技能不能主动使用（服务器按条件自动触发），这一点本页会直接标出来。"
            % (len(off), n_active, len(off) - n_active))

    def _lord_read_state(self):
        """读本局主公技能状态。远程调用放子线程, 只把结果回主线程填表。"""
        if getattr(self, "_lord_busy", False):
            self.log("[主公技能] 上一次读取还没回来, 稍等", level="WARN")
            return
        ops = getattr(self, "ops", None)
        if ops is None:
            self.log("[主公技能] 助手还没准备好", level="WARN")
            return
        self._lord_busy = True
        self.lord_state_var.set("读取中 ...")

        def _task():
            try:
                data = ops.lord_skill_state()
            except BaseException as e:
                data = {"ok": False, "reason": f"异常: {e}", "skills": []}
            self.root.after(0, self._lord_fill_state, data)

        threading.Thread(target=_task, daemon=True).start()

    def _lord_fill_state(self, data):
        """主线程填表。数据源两路: 游戏配置表(权威) + 离线表(补名字/说明)。"""
        self._lord_busy = False
        tree = getattr(self, "lord_tree", None)
        if tree is None:
            return
        data = data if isinstance(data, dict) else {}
        tree.delete(*tree.get_children())
        if not data.get("ok"):
            reason = str(data.get("reason") or "未知原因")
            self.lord_state_var.set("读不到: " + reason[:80])
            self.log(f"[主公技能] 读取失败: {reason}", level="WARN")
            self._lord_fill_passive({})          # ⑤ 区也回到"没读到"的实话, 不留上一局的旧内容
            self._lord_set_detail(
                "读不到本局主公技能。\n\n常见原因:\n"
                "  · 游戏页还没连上助手(去『日志』页确认已连接)\n"
                "  · 还没进入对局(在大厅里 SelfInfo 不存在)\n"
                "  · 游戏页脚本版本 < 4.46(本页用的指令是新加的, 重新注入一次即可)")
            return
        off = self._lord_offline_map()
        gid = int(data.get("generalID") or 0)
        gname = self._lord_name_of(gid) if gid else ""
        src = data.get("src") or {}
        can_op = data.get("canOperate")
        self.lord_state_var.set(
            "主公: %s%s   可操作: %s   阶段: %s   技能 %d 个   [数据源 配置表:%s 技能管理器:%s 字段:%s]"
            % (("【%s】" % gname) if gname else "", gid or "?",
               "是" if can_op else ("否" if can_op is False else "未知"),
               data.get("phase"), len(data.get("skills") or []),
               "有" if src.get("cfg") else "无", "有" if src.get("zgsm") else "无",
               src.get("skillList") or "?"))
        for s in (data.get("skills") or []):
            sid = int(s.get("skillID") or 0)
            row = off.get(sid) or {}
            name = str(s.get("name") or "").strip() or str(row.get("name") or "")
            desc = str(s.get("desc") or "").strip() or str(row.get("desc") or "")
            styp = int(s.get("skillType") or 0)
            if not styp:
                styp = 2 if str(row.get("tag") or "") == "被动" else (1 if str(row.get("tag") or "") == "主动" else 0)
            tname = {1: "主动", 2: "被动"}.get(styp, "未知")
            is_used = s.get("isUsed")
            state = "已用过" if is_used is True else ("可用" if is_used is False else "未知")
            cost = s.get("costNum")
            note = str(s.get("note") or "").strip()
            show = (note + " | " + desc) if note and desc else (note or desc)
            tree.insert("", "end", values=(sid, name, tname, state,
                                           ("" if cost in (None, 0) else cost), show))
        self.log("[主公技能] 已读取本局主公技能 %d 个(数据源 配置表:%s / 技能管理器:%s)"
                 % (len(data.get("skills") or []),
                    "有" if src.get("cfg") else "无", "有" if src.get("zgsm") else "无"))
        if not (data.get("skills") or []):
            self._lord_set_detail(
                "本局技能列表为空。\n"
                "  字段来源: SelfInfo.%s\n"
                "  可能原因: 还没选将 / 主公技能在别的字段名下(那需要按实机日志换字段名)。"
                % (src.get("skillList") or "generalSkillList"))
        else:
            self._lord_set_detail("双击上表任意一行 → 技能ID 会填进下面输入框 → 点『使用这个主公技能』。\n"
                                  "被动技能双击只填 ID, 【不要】点使用(服务器会拒)。")
        # ⑤ 区(被动监控) + "该用还没用"提醒 —— 每次都跟着这次回读刷新, 不再需要用户自己点。
        self._lord_fill_passive(data)
        self._lord_tip_if_unused(data)

    def _lord_on_select(self, _evt=None):
        tree = getattr(self, "lord_tree", None)
        if tree is None:
            return
        sel = tree.selection()
        if not sel:
            return
        vals = list(tree.item(sel[0], "values") or [])
        vals += ["", "", "", "", "", ""]
        self._lord_set_detail("技能ID: %s\n技能名: %s      类型: %s      本回合: %s      消耗: %s\n说明: %s"
                              % tuple(vals[:6]))

    def _lord_pick_to_use(self, _evt=None):
        """双击一行 → 把技能ID填进『使用』区(被动技能也照填, 但会提示别点使用)。"""
        tree = getattr(self, "lord_tree", None)
        if tree is None:
            return
        sel = tree.selection()
        if not sel:
            return
        vals = list(tree.item(sel[0], "values") or [])
        vals += ["", "", "", "", "", ""]
        sid, name, tname, state = str(vals[0]), str(vals[1]), str(vals[2]), str(vals[3])
        var = getattr(self, "lord_use_id_var", None)
        if var is not None:
            var.set(sid)
        if tname == "被动":
            self.log(f"[主公技能] {name}({sid}) 是【被动】技能 —— 不能主动使用, 只把 ID 填好了; "
                     f"被动由服务器按条件自动触发。", level="WARN")
        elif state == "已用过":
            self.log(f"[主公技能] {name}({sid}) 本回合已经用过了, 再用会被服务器拒。", level="WARN")
        else:
            self.log(f"[主公技能] 已选中 {name}({sid}), 填好目标(需要的话)后点『使用这个主公技能』。")

    @staticmethod
    def _lord_ints(txt):
        """把用户随手粘的一串目标ID切成 int 列表。

        v4.46★ 用【取所有数字串】而不是按分隔符 split 再 int():
          按分隔符切的话, 只要用户粘进来的分隔符比我列举的多一种(全角「；」、全角空格、换行、
          「11/22」…), 那些数字就会被 int() 失败【静默丢掉】—— 冒烟里实测 "11, 22 33；44、55"
          会变成 [11, 22, 55], 33/44 直接消失, 而且界面上什么提示都没有。
          目标ID丢一个的后果是"技能只对我随便选的那个目标生效", 属于最难查的一类问题,
          所以这里宁可宽松: 只要是数字串就要, 分隔符长什么样都无所谓。
        """
        return [int(x) for x in re.findall(r"\d+", str(txt or ""))]

    def _lord_use(self):
        """使用主公技能。主动技能才走这条路; 被动会在脚本侧被服务器拒, 这里先提示一次。"""
        ops = getattr(self, "ops", None)
        if ops is None:
            self.log("[主公技能] 助手还没准备好", level="WARN")
            return
        try:
            sid = int((self.lord_use_id_var.get() or "").strip())
        except ValueError:
            messagebox.showinfo("主公技能", "请先在『技能ID』里填一个数字(可双击上表某行自动填)。")
            return
        # 被动技能拦一道: 让"点了没反应"变成"点了明确告诉你为什么"。
        #   判据同时看两处 —— 本局读回来的类型(游戏配置表, 权威) 和 离线表 tag,
        #   任一说被动就拦(实测两者偶尔只有一边有数据)。
        off = self._lord_offline_map()
        row = off.get(sid) or {}
        passive = (str(row.get("tag") or "") == "被动")
        tree = getattr(self, "lord_tree", None)
        if not passive and tree is not None:
            for it in tree.get_children():
                v = list(tree.item(it, "values") or [])
                if v and str(v[0]) == str(sid) and len(v) > 2 and str(v[2]) == "被动":
                    passive = True
                    break
        force = False
        if passive:
            if not messagebox.askyesno(
                    "这是被动技能",
                    "技能 %s「%s」是【被动】技能。\n\n"
                    "被动技能由服务器按条件自动触发, 客户端【没有施放入口】—— 主动发包只会被服务器拒。\n"
                    "它要不要触发取决于条件是否满足, 想「帮它满足条件」应该用『剧本』/招募设置\n"
                    "(例如自动留够某类卡), 而不是在这里点使用。\n\n"
                    "既然你想试, 那就硬发一次(仅这一次, 会有明确回执)。要继续吗?"
                    % (sid, row.get("name") or "?")):
                return
            force = True
            self.log(f"[主公技能] {sid} 是被动技能, 你确认硬试一次 —— 被服务器拒是预期结果", level="WARN")
        goods = self._lord_ints(self.lord_use_goods_var.get())
        chess = self._lord_ints(self.lord_use_chess_var.get())
        minion = self._lord_ints(self.lord_use_minion_var.get())
        minion = minion[0] if minion else 0
        buy = bool(self.lord_use_buy_var.get())
        self.log("[主公技能] 使用 %s%s (目标 goodsID=%s chessID=%s minion=%s buy=%s force=%s)"
                 % (row.get("name") or "", sid, goods or "空", chess or "空", minion, buy, force))

        def _task():
            try:
                r = ops.lord_skill_use(sid, goods_ids=goods, chess_ids=chess,
                                       chess_minion_typ=minion, buy=buy, force=force)
            except BaseException as e:
                r = {"ok": False, "reason": f"异常: {e}"}
            self.root.after(0, self._lord_report, sid, r)

        threading.Thread(target=_task, daemon=True).start()

    # v4.156★ 岳飞·精忠 选势力: 协议还没在真机确认, 先给"抓窗口(诊断)"和"手动回答"两个按钮
    def _lord_faction_probe(self):
        """抓岳飞选势力窗口的协议(类名/候选势力/onOk 源码), 结果写进下面的文本框 + 日志。"""
        ops = getattr(self, "ops", None)
        if ops is None:
            self.log("[主公技能] 助手还没准备好", level="WARN")
            return

        def _task():
            try:
                res = ops.lord_faction_probe()
            except BaseException as e:
                res = {"ok": False, "reason": f"异常: {e}"}
            self.root.after(0, self._lord_faction_show, res)

        threading.Thread(target=_task, daemon=True).start()

    def _lord_faction_answer(self):
        """手动回答岳飞选势力窗口(best-effort, 协议确认前先用一次看对不对)。"""
        ops = getattr(self, "ops", None)
        if ops is None:
            self.log("[主公技能] 助手还没准备好", level="WARN")
            return
        fac = (self.lord_faction_var.get() or "").strip()
        if not fac:
            messagebox.showinfo("岳飞选势力", "先在左边填势力(魏/蜀/吴/群/黄巾/汉/西凉/袁 或数字 1~8)")
            return

        def _task():
            try:
                res = ops.lord_faction_answer(fac)
            except BaseException as e:
                res = {"ok": False, "reason": f"异常: {e}"}
            self.root.after(0, self._lord_faction_show, res)

        threading.Thread(target=_task, daemon=True).start()

    def _lord_faction_show(self, res):
        tw = getattr(self, "lord_faction_text", None)
        if tw is not None:
            try:
                tw.configure(state="normal")
                tw.delete("1.0", "end")
                tw.insert("end", str(res))
                tw.configure(state="disabled")
            except Exception as e:
                sys.stderr.write("[主公技能] 岳飞文本写入失败: %s\n" % e)
        self.log("[主公技能] 岳飞选势力: %s" % res)

    def _lord_report(self, sid, r):
        """把使用结果写到日志 + 用回读状态刷新表格(区分"包发出去了"和"服务器真的接受了")。"""
        r = r if isinstance(r, dict) else {}
        if not r.get("ok"):
            self.log(f"[主公技能] {sid} 使用失败: {r.get('reason') or '未知原因'}", level="ERROR")
            return
        sent = r.get("sent") or {}
        self.log("[主公技能] %s 已发包: skillID=%s chessMinionTyp=%s goodsID=%s chessID=%s buy=%s"
                 % (sid, sent.get("skillID"), sent.get("chessMinionTyp"),
                    sent.get("goodsID"), sent.get("chessID"), sent.get("buy")))
        after = r.get("after")
        if isinstance(after, dict) and after:
            self._lord_fill_state(after)
            hit = [s for s in (after.get("skills") or []) if int(s.get("skillID") or 0) == int(sid)]
            if hit and hit[0].get("isUsed") is True:
                self.log(f"[主公技能] {sid} 服务器已接受(本回合状态 = 已用过)。")
            elif hit:
                self.log(f"[主公技能] {sid} 包已发出, 但回读仍是『{hit[0].get('note') or '可用'}』—— "
                         f"可能被服务器拒了(条件不满足/没有合法目标), 也可能只是回读太早。", level="WARN")
            else:
                self.log(f"[主公技能] {sid} 包已发出, 但回读列表里没有这个技能了 —— "
                         f"它可能是一次性的(用掉就没了), 也可能是字段变了。", level="WARN")
        else:
            self.log("[主公技能] 包已发出(未回读状态)。", level="WARN")

    # ---------- 主公技能: 自动挑目标 / 被动监控 (v4.47★) ----------
    def _lord_auto_toggle(self):
        """⑤ 区那个勾选: 开着就每 5 秒读一次状态(只读, 永不自动发包)。"""
        if self.lord_auto_var.get():
            # v4.101★ 起链前先 cancel 旧链: 以前反复勾选会【叠出好几条】1 秒心跳,
            #   每条都在读状态, 越点越卡(取消勾选时又只停掉最后那条)。
            if getattr(self, "_lord_auto_job", None):
                try:
                    self.root.after_cancel(self._lord_auto_job)
                except Exception as e:
                    self.log(f"[主公技能] 取消上一次自动读取失败(忽略): {e}", level="WARN")
                self._lord_auto_job = None
            self._lord_auto_last = 0.0
            self.log("[主公技能] 已开启自动读取: 每 5 秒读一次本局状态(只读, 不会自动用技能)")
            self._lord_auto_tick()
        else:
            self.log("[主公技能] 已关闭自动读取")

    def _lord_auto_tick(self):
        """自动读取心跳。只读状态 —— 发包永远只发生在你按下『使用』的时候。"""
        if not self.lord_auto_var.get():
            return
        now = time.time()
        if (now - getattr(self, "_lord_auto_last", 0.0)) >= 5.0 and not getattr(self, "_lord_busy", False):
            self._lord_auto_last = now
            self._lord_read_state()
        self._lord_auto_job = self.root.after(1000, self._lord_auto_tick)

    def _lord_fill_passive(self, data):
        """把本局【被动】技能写进 ⑤ 区: 触发了没 + 条件/说明。

        v4.47★ 被动技能不能主动使用(客户端没有施放入口, 发包会被服务器拒), 所以对被动
        唯一有用的辅助是【监控】。IsUsed 拿不到时如实写"未知" —— 不能写成"没触发",
        那会让人以为被动坏了。
        """
        tw = getattr(self, "lord_passive_text", None)
        if tw is None:
            return
        skills = (data or {}).get("skills") or []
        rows = []
        n_hit = 0
        for s in skills:
            sid = int(s.get("skillID") or 0)
            row = self._lord_offline_map().get(sid) or {}
            styp = int(s.get("skillType") or 0)
            if not styp:
                styp = 2 if str(row.get("tag") or "") == "被动" else (
                    1 if str(row.get("tag") or "") == "主动" else 0)
            if styp != 2:
                continue
            name = str(s.get("name") or row.get("name") or "?")
            desc = str(s.get("desc") or row.get("desc") or "")
            is_used = s.get("isUsed")
            if is_used is True:
                head = "已触发 / 本回合已用"
                n_hit += 1
            elif is_used is False:
                head = "本回合还没触发"
            else:
                head = "触发状态未知(游戏没给 IsUsed)"
            rows.append("· %s(%d) —— %s\n    条件/说明: %s"
                        % (name, sid, head, desc or "(离线表里也没有说明)"))
        if rows:
            txt = ("本局被动技能 %d 个。被动由【服务器按条件自动触发】, 助手只能看不能发。\n%s"
                   % (len(rows), "\n".join(rows)))
        else:
            txt = ("本局没有读到被动技能(或者还没读过状态)。\n"
                   "被动是服务器按条件触发的 —— 想「帮它满足条件」要用『剧本』/招募设置"
                   "(例如自动留够某类卡), 不是在这一页点使用。")
        try:
            tw.configure(state="normal")
            tw.delete("1.0", "end")
            tw.insert("end", txt)
            tw.configure(state="disabled")
        except Exception as e:
            sys.stderr.write("[主公技能] 被动区写入失败: %s\n" % e)
        var = getattr(self, "lord_passive_var", None)
        if var is not None:
            var.set(("被动 %d 个 / 已触发 %d 个" % (len(rows), n_hit)) if rows else "")

    def _lord_tip_if_unused(self, data):
        """「该用还没用」提醒: 现在能操作 + 有主动技能没用过 → 日志提一句(同一个只提一次)。"""
        if not (data or {}).get("ok") or data.get("canOperate") is not True:
            return
        for s in (data.get("skills") or []):
            sid = int(s.get("skillID") or 0)
            row = self._lord_offline_map().get(sid) or {}
            styp = int(s.get("skillType") or 0)
            if not styp:
                styp = 2 if str(row.get("tag") or "") == "被动" else 1
            if styp != 1 or s.get("isUsed") is True:
                continue
            key = "%s|%s" % (sid, data.get("turn") or data.get("phase"))
            if getattr(self, "_lord_tip_key", "") == key:
                return
            self._lord_tip_key = key
            self.log("[主公技能] 提醒: %s(%d) 现在可用且还没用 —— 需要用就去『主公技能』页点一下。"
                     % (str(s.get("name") or row.get("name") or "?"), sid))
            return

    def _lord_auto_target(self):
        """④ 区: 从 LIVE(备战区 + 上阵)按策略挑一张目标卡, 把 goodsID/chessID 填进 ③ 区。

        v4.47★ 需要选目标的技能要填的是【本局实例 goodsID】—— 那个号在游戏界面上看不到,
        只能从协议(LIVE)里读; 填错的后果是"技能白用"。所以这里把"为什么挑它"一并写进日志,
        用户不放心可以改完再点使用。
        """
        mode = self.lord_target_mode.get()
        try:
            from core.protocol import LIVE
            from core import id_map as IDMAP
            with LIVE.lock:
                board = [c for c in (LIVE.me.get("_board") or []) if isinstance(c, dict)]
                # ⚠️ v4.47★ _lineUp 存的是【goodsID 整数】(protocol 与 remote_ops 都按 int 用),
                #   原来这里也按 dict 过滤 → 上阵那 7 格永远筛不出候选, 只能挑到备战区的卡。
                line = []
                for _gid in (LIVE.me.get("_lineUp") or []):
                    try:
                        _n = int(_gid)
                    except (TypeError, ValueError):
                        continue
                    if _n <= 0:
                        continue
                    line.append({"goodsID": _n,
                                 "chessID": (LIVE.goods_map or {}).get(_n)})
        except Exception as e:
            self.log(f"[主公技能] 读 LIVE 失败, 挑不了目标: {e}", level="WARN")
            return
        cands = ([("备战区", i, c) for i, c in enumerate(board)]
                 + [("上阵", i, c) for i, c in enumerate(line)])
        cands = [x for x in cands
                 if str(x[2].get("chessID") or "").strip() and not x[2].get("_sold")]
        if not cands:
            self.log("[主公技能] LIVE 里没有可当目标的卡(还没进对局? 或备战区/上阵都是空的)", level="WARN")
            return

        def _star(c):
            for k in ("chessStar", "star", "_star", "Star"):
                v = c.get(k)
                if v not in (None, "", 0):
                    try:
                        return int(v)
                    except (TypeError, ValueError):
                        break
            return 0

        def _cost(c):
            try:
                return int(IDMAP.chess_cost_of(str(c.get("chessID") or "")) or 0)
            except Exception:
                return 0

        def _nm(c):
            cid = str(c.get("chessID") or "")
            try:
                return IDMAP.name_of(cid, chess_hint=True) or cid
            except Exception:
                return cid

        if mode == "费用最高":
            keyf = lambda x: (_cost(x[2]), _star(x[2]))          # noqa: E731
        elif mode == "金色优先":
            keyf = lambda x: (1 if _star(x[2]) >= 3 else 0, _star(x[2]), _cost(x[2]))   # noqa: E731
        elif mode == "备战区最前":
            keyf = lambda x: (0 if x[0] == "备战区" else 1, -x[1])                        # noqa: E731
        else:
            keyf = lambda x: (_star(x[2]), _cost(x[2]))          # noqa: E731
        best = None
        for x in cands:
            if best is None or keyf(x) > keyf(best):
                best = x
        where, pos, card = best
        gid = str(card.get("goodsID") or "").strip()
        cid = str(card.get("chessID") or "").strip()
        if gid:
            self.lord_use_goods_var.set(gid)
        self.lord_use_chess_var.set(cid)
        why = "%s 第%d 张 %s(chessID=%s, %d星, %d费)" % (
            where, pos + 1, _nm(card), cid, _star(card), _cost(card))
        self.lord_target_var.set("已挑: " + why)
        self.log("[主公技能] 自动挑目标: %s → goodsID=%s chessID=%s (策略: %s)"
                 % (why, gid or "空", cid, mode))
        if not gid:
            self.log("[主公技能] 没读到 goodsID, 只填了 chessID —— 若技能要求指定实例, 请手动补 goodsID。",
                     level="WARN")

    # v4.71★ 【已删除】对局复盘页(_build_replay_tab 及全部 _replay_*/_rp_* 共 433 行)。
    #   用户原话: 「然后复盘删了吧, 你只记录了, 我选的主公, 里面的操作没有记录, 我感觉也浪费
    #    输出传输、和占内存」。
    #   事实核对: 战绩(data/records.json)每局只落 名次/主公/时间 这几个字段, 【没有操作记录】
    #   —— 用户想看的「我这局做了啥」根本不在里面; 协议流水才可能有, 而它默认是关的
    #   (net_record=false), 所以这一页看完等于没看, 留着还容易让人以为助手在偷偷录东西。
    #   删掉的是【这一页】; core/records.py 与每局收官那一次落盘(几百字节)保留 —— 战绩/统计
    #   还用得上, 和复盘页是两回事。自检那边同步去掉了 dev/_verify_replay.py、
    #   dev/_smoke_replay_tab.py, 以及 dev/_verify_offline.py 的 Part J。
    #   ⚠ v4.71 连带修复: 那一页上原来挂着『协议流水落盘(net_record)』开关, 删页面时一并
    #     没了 —— 现已在『数据管理』页补回(_build_net_record_section), 否则用户以后想诊断
    #     就只能手改 config.json。
    #   将来真要做复盘, 先把「操作」记下来(协议里那份可读的操作事件), 否则做出来还是这一页。

    # ---------- Tab: 对局决策 (v4.47★) ----------
    def _build_decision_tab(self):
        """对局决策: ① 刷新决策器 ② 本局速览 —— 两个都只读 LIVE, 不发送任何包。

        用户的要求是"刷新决策器": 『剧本』里的 shopFind 是【先设好刷多少次然后无脑刷】,
        『招募』是【看到就买】, 都对不了"手上这点金币该刷还是该攒着升级"。这里把判断依据
        (金币/等级/刷新费/升级费/目标卡池剩余/各档出现率)摆出来并给一条结论 + 理由。

        页面里【没有任何发放入口】是刻意的: 判断题和"照着步骤执行"混在一起, 人会以为
        点了就会自己刷。真要执行, 去『剧本』用 shopFind, 或手动在游戏里点刷新。
        """
        from core import refresh_advisor as RA
        wrap = self._make_scrollable_tab(self.tab_decision, pady=8)

        # ------------------------------ ① 刷新决策器 ------------------------------
        box1 = tk.LabelFrame(
            wrap, text=" ① 刷新决策器 (只算不发 —— 结论只是建议, 执行请用『剧本』shopFind 或手动点刷新) ",
            bg=BG, fg=GOLD, font=(FH, 10, "bold"), bd=1, relief="groove")
        box1.pack(fill="x", pady=(0, 6))
        r1 = tk.Frame(box1, bg=BG)
        r1.pack(fill="x", padx=6, pady=6)
        tk.Label(r1, text="目标卡(名字/ID, 逗号分隔可多个):", bg=BG, fg=FG, font=(FH, 9)).pack(side="left")
        self.dec_target_var = tk.StringVar(value="")
        tk.Entry(r1, textvariable=self.dec_target_var, width=22, bg=BTN, fg=FG,
                 insertbackground=FG, font=(FH, 9)).pack(side="left", padx=(2, 10))
        tk.Label(r1, text="保留金币:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left")
        self.dec_keep_var = tk.StringVar(value="0")
        tk.Entry(r1, textvariable=self.dec_keep_var, width=5, bg=BTN, fg=FG,
                 insertbackground=FG, font=(FH, 9)).pack(side="left", padx=(2, 10))
        tk.Label(r1, text="计划升到Lv:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left")
        self.dec_level_var = tk.StringVar(value="")
        tk.Entry(r1, textvariable=self.dec_level_var, width=4, bg=BTN, fg=FG,
                 insertbackground=FG, font=(FH, 9)).pack(side="left", padx=(2, 10))
        tk.Button(r1, text="分析一下", command=self.a_dec_advise, bg=ACT, fg=GOLD,
                  font=(FH, 9, "bold"), padx=10, cursor="hand2").pack(side="left")
        self.dec_verdict_var = tk.StringVar(value="还没分析 —— 填好目标卡(可留空)后点『分析一下』")
        tk.Label(box1, textvariable=self.dec_verdict_var, bg=BG, fg=BLUE,
                 font=(FH, 10, "bold"), wraplength=820, justify="left").pack(anchor="w", padx=8, pady=(0, 4))
        self.dec_text = tk.Text(box1, height=9, wrap="word", bg="#1e1e1e", fg=FG,
                                font=(FH, 9), state="disabled")
        self.dec_text.pack(fill="x", padx=6, pady=(0, 6))

        # ------------------------------ ② 本局速览 ------------------------------
        box2 = tk.LabelFrame(wrap, text=" ② 本局速览 (直接从协议读, 不走 OCR; 只读) ",
                             bg=BG, fg=GOLD, font=(FH, 10, "bold"), bd=1, relief="groove")
        box2.pack(fill="both", expand=True)
        r2 = tk.Frame(box2, bg=BG)
        r2.pack(fill="x", padx=6, pady=6)
        tk.Button(r2, text="刷新速览", command=self.a_dec_snapshot, bg=BTN, fg=FG,
                  font=(FH, 9, "bold"), padx=8, cursor="hand2").pack(side="left")
        self.dec_auto_var = tk.BooleanVar(value=False)
        tk.Checkbutton(r2, text="每 3 秒自动刷(只读)", variable=self.dec_auto_var,
                       command=self._dec_auto_toggle, bg=BG, fg=FG, selectcolor="#33333f",
                       activebackground=BG, font=(FH, 9)).pack(side="left", padx=8)
        self.dec_snap_var = tk.StringVar(value="")
        tk.Label(r2, textvariable=self.dec_snap_var, bg=BG, fg=DIM, font=(FH, 9)).pack(side="left")
        tk.Label(box2, text=("说明: 数据来自油猴端推上来的协议状态(LIVE) —— 金币/商店等级/刷新费/升级费/"
                             "血量/回合/阶段/商店 5 张/备战区/上阵/本局各卡被买走张数。\n"
                             "读数不准时先看『日志』页确认已连接; 若商店是空的, 通常是还没进招募阶段或"
                             "游戏页刚刷新(等一两秒再点)。"),
                 bg=BG, fg=DIM, font=(FH, 8), wraplength=820, justify="left").pack(anchor="w", padx=8, pady=(0, 4))
        self.dec_snap_text = tk.Text(box2, height=10, wrap="none", bg="#1e1e1e", fg=FG,
                                     font=(FH, 9), state="disabled")
        self.dec_snap_text.pack(fill="both", expand=True, padx=6, pady=(0, 6))

    def _dec_set_text(self, widget, txt):
        """只读 Text 写入的公共部分(异常只写 stderr, 不让界面炸)。"""
        if widget is None:
            return
        try:
            widget.configure(state="normal")
            widget.delete("1.0", "end")
            widget.insert("end", txt)
            widget.configure(state="disabled")
        except Exception as e:
            sys.stderr.write("[对局决策] 文本框写入失败: %s\n" % e)

    def a_dec_advise(self):
        """刷新决策器: 读 LIVE + 概率表 → 结论 + 理由(纯本地计算, 不联网不发包)。"""
        from core import refresh_advisor as RA
        try:
            keep = int(float((self.dec_keep_var.get() or "0").strip() or 0))
        except ValueError:
            keep = 0
        try:
            want_lv = int(float((self.dec_level_var.get() or "0").strip() or 0))
        except ValueError:
            want_lv = 0
        try:
            d = RA.advise(self.dec_target_var.get(), keep_gold=keep, want_level=want_lv,
                          cfg=getattr(self, "cfg", None))
        except Exception as e:
            self.log(f"[对局决策] 分析失败: {e}", level="ERROR")
            return
        self.dec_verdict_var.set("结论: " + str(d.get("verdict") or "?"))
        self._dec_set_text(self.dec_text, RA.format_report(d))
        self.log("[对局决策] 刷新建议: %s (目标=%s 保留金币=%d 计划等级=%s)"
                 % (d.get("verdict"), self.dec_target_var.get().strip() or "未指定",
                    keep, want_lv or "未设"))
        for line in (d.get("reasons") or [])[:3]:
            self.log("[对局决策]   " + line)

    def _dec_auto_toggle(self):
        if self.dec_auto_var.get():
            self._dec_auto_last = 0.0
            self.log("[对局决策] 已开启速览自动刷新: 每 3 秒(只读, 不发包)")
            self._dec_auto_tick()
        else:
            self.log("[对局决策] 已关闭速览自动刷新")

    def _dec_visible(self):
        """「本局速览」这一块现在有没有真的显示在屏幕上。

        用 winfo_ismapped() 而不是比对页签名: 这个速览区是嵌在别的页里的(并没有一个叫
        "对局决策"的独立页签), ttk.Notebook 切走时会把整块 unmap —— 于是"看不见"这件事
        直接问控件本身最准。
        """
        try:
            w = getattr(self, "dec_text", None)
            if w is None:
                return True
            return bool(w.winfo_ismapped())
        except Exception as e:
            self.log(f"[对局决策] 判断本页是否可见失败(按可见处理): {e}", level="WARN")
            return True

    def _dec_auto_tick(self):
        if not self.dec_auto_var.get():
            return
        now = time.time()
        # v4.101★ 看不见就不刷新: 以前切到别的页它照样每 3 秒做一次快照, 还把速览那个 Text
        #   【整篇重写】一遍(全在主线程), 用户的感觉就是"我干别的它也卡"。
        if (now - getattr(self, "_dec_auto_last", 0.0)) >= 3.0 and self._dec_visible():
            self._dec_auto_last = now
            self.a_dec_snapshot(quiet=True)
        self._dec_auto_job = self.root.after(1000, self._dec_auto_tick)

    # ==================== 经济决策页 (v4.110★ L0) ====================
    def _build_econ_tab(self):
        """把"这回合该干什么"的账算出来给人看。只读, 不发包。"""
        frame = self._make_scrollable_tab(self.tab_econ)

        top = tk.Frame(frame, bg=BG)
        top.pack(fill="x", padx=8, pady=6)
        tk.Label(top, text="目标卡(逗号分隔, 留空=通用刷牌):", bg=BG).pack(side="left")
        self.econ_target_var = tk.StringVar(value="")
        tk.Entry(top, textvariable=self.econ_target_var, width=24).pack(side="left", padx=4)
        tk.Label(top, text="保留金币:", bg=BG).pack(side="left", padx=(10, 0))
        self.econ_keep_var = tk.StringVar(value="0")
        tk.Entry(top, textvariable=self.econ_keep_var, width=6).pack(side="left", padx=4)
        tk.Button(top, text="算一次", command=self.a_econ_advise).pack(side="left", padx=8)
        self.econ_auto_var = tk.BooleanVar(value=False)
        tk.Checkbutton(top, text="自动(每 3 秒, 只看这页时)", variable=self.econ_auto_var,
                       bg=BG, command=self._econ_auto_toggle).pack(side="left")

        self.econ_verdict_var = tk.StringVar(value="点『算一次』看看这回合该怎么打")
        tk.Label(frame, textvariable=self.econ_verdict_var, bg=BG,
                 justify="left").pack(anchor="w", padx=8)

        self.econ_text = tk.Text(frame, height=18, wrap="word")
        self.econ_text.pack(fill="both", expand=True, padx=8, pady=6)
        self.econ_text.configure(state="disabled")

    def a_econ_advise(self, quiet=False):
        from core import econ_planner as EP
        try:
            keep = int((self.econ_keep_var.get() or "0").strip() or 0)
        except ValueError:
            keep = 0
        try:
            d = EP.plan(targets=self.econ_target_var.get(), keep_gold=keep)
        except Exception as e:
            self.log(f"[经济决策] 计算失败: {e}", level="ERROR")
            return
        if d.get("ok"):
            self.econ_verdict_var.set("建议: %s" % ((d.get("best") or {}).get("name")))
        else:
            self.econ_verdict_var.set("现在给不了建议: %s" % d.get("reason", "?"))
        self._econ_set_text(EP.format_report(d))
        if not quiet:
            self.log("[经济决策] 已算一次(只读, 不发包)")

    def _econ_set_text(self, txt):
        w = getattr(self, "econ_text", None)
        if w is None:
            return
        try:
            w.configure(state="normal")
            w.delete("1.0", "end")
            w.insert("1.0", txt)
            w.configure(state="disabled")
        except Exception as e:
            self.log(f"[经济决策] 写结果失败: {e}", level="WARN")

    def _econ_visible(self):
        """跟速览同一个道理: 切走时整块会 unmap, 直接问控件最准 —— 看不见就别刷。"""
        try:
            w = getattr(self, "econ_text", None)
            if w is None:
                return True
            return bool(w.winfo_ismapped())
        except Exception:
            return True

    def _econ_auto_toggle(self):
        if self.econ_auto_var.get():
            self.log("[经济决策] 已开启自动刷新: 每 3 秒(只读, 不发包)")
            self._econ_auto_tick()
        else:
            self.log("[经济决策] 已关闭自动刷新")

    def _econ_auto_tick(self):
        if not self.econ_auto_var.get():
            return
        now = time.time()
        if (now - getattr(self, "_econ_auto_last", 0.0)) >= 3.0 and self._econ_visible():
            self._econ_auto_last = now
            self.a_econ_advise(quiet=True)
        self._econ_auto_job = self.root.after(1000, self._econ_auto_tick)

    # ==================== 终端对局页 (v4.110★ B站 5.1: 8 种终端对局识别) ====================
    def _build_endgame_tab(self):
        """看对手阵容 → 判这是 8 种终端对局里的哪一种。只读, 不发包。"""
        frame = self._make_scrollable_tab(self.tab_endgame)
        top = tk.Frame(frame, bg=BG)
        top.pack(fill="x", padx=8, pady=6)
        tk.Button(top, text="读对手阵容并识别", command=self.a_endgame_scan).pack(side="left")
        tk.Label(top, text="(对手上过场 / 你点过他头像, 才有数据)", bg=BG).pack(side="left", padx=6)

        self.eg_text = tk.Text(frame, height=22, wrap="word")
        self.eg_text.pack(fill="both", expand=True, padx=8, pady=6)
        self.eg_text.configure(state="disabled")

    def a_endgame_scan(self):
        from core import endgame_matcher as EM
        try:
            rows = EM.from_live(only_alive=True)
        except Exception as e:
            self.log(f"[终端对局] 读取失败: {e}", level="ERROR")
            return
        if not rows:
            self._eg_set_text(
                "现在还没有对手阵容数据。\n\n"
                "对手要【上过场】, 或者你【点过他的头像】(服务器会推 lastLineUp), "
                "助手才嗅得到他的阵容 —— 开局头几回合一般还没有, 这是正常的。\n\n"
                "另外: 只有在对局中(连着游戏页)才有; 离线看不了。")
            return
        parts = []
        for nick, d in rows:
            parts.append("———— %s ————" % nick)
            parts.append(EM.format_report(d))
            parts.append("")
        self._eg_set_text("\n".join(parts))
        self.log(f"[终端对局] 识别了 {len(rows)} 个对手(只读, 不发包)")

    def _eg_set_text(self, txt):
        w = getattr(self, "eg_text", None)
        if w is None:
            return
        try:
            w.configure(state="normal")
            w.delete("1.0", "end")
            w.insert("1.0", txt)
            w.configure(state="disabled")
        except Exception as e:
            self.log(f"[终端对局] 写结果失败: {e}", level="WARN")

    def a_dec_snapshot(self, quiet=False):
        """本局速览: 把 LIVE 里跟决策有关的状态抄出来给人看(只读)。"""
        from core import refresh_advisor as RA
        from core import id_map as IDMAP
        try:
            s = RA.snapshot()
        except Exception as e:
            self.log(f"[对局决策] 读状态失败: {e}", level="ERROR")
            return
        if not s.get("in_game"):
            # v4.47★ 别只说"读不到" —— 把"助手这侧到底收到了多少协议流水"一并显示出来,
            #   这是区分"游戏页没连上/没在推"和"推上来了但解析不出"的唯一线索。
            st = getattr(self, "_live_stat", None) or {}
            n = int(st.get("msgs") or 0)
            last = float(st.get("last") or 0.0)
            tail = ""
            if last:
                tail = "，最后一条 %s" % datetime.fromtimestamp(last).strftime("%H:%M:%S")
            if st.get("err"):
                tail += "，解析报错 %d 次" % int(st.get("err") or 0)
            self.dec_snap_var.set("还没读到对局 —— 按下面 3 条排查")
            self._dec_set_text(self.dec_snap_text,
                               "LIVE 里还没有对局数据。\n"
                               "\n"
                               "助手这侧: 已收到协议流水 %d 条%s\n"
                               "\n"
                               "排查(按顺序):\n"
                               "  1) 游戏页要连上助手 —— 脚本页右上角角标是绿色;\n"
                               "     不是绿的: 点『一键注入』, 然后刷新游戏页。\n"
                               "  2) 要真的在一局自走棋里 —— 大厅/结算界面本来就没有对局数据。\n"
                               "  3) 上面流水若是 0 条: 说明游戏页根本没在推;\n"
                               "     点游戏页的『诊断上报』, 再看助手日志(那里面有收到什么)。\n"
                               "\n"
                               "背景: 数据来自游戏页每秒上报的协议流水, 由助手这侧解析。"
                               % (n, tail))
            return
        lines = ["我方: %s   金币 %d   商店Lv%d   血量 %s/%s   第%d回合   阶段 %s"
                 % (s.get("nickname") or "?", s.get("coin"), s.get("level"),
                    s.get("hp"), s.get("hp_limit"), s.get("turn"), s.get("phase") or "-"),
                 "费用: 刷新 %d / 升级还差 %d / 买卡 %s   备战区 %d 张, 上阵 %d 位, 对手 %d 家"
                 % (s.get("refresh_cost"), max(0, s.get("level_up_cost") - s.get("coin")),
                    s.get("buy_cost") or "?", s.get("board_n"), s.get("line_n"), s.get("others_n")),
                 ""]
        if s.get("shop"):
            lines.append("商店 5 张:")
            for g in s["shop"]:
                cid = str(g.get("chessID") or "")
                lines.append("  #%-6s %-14s %s/%s  %s"
                             % (g.get("goodsID"), IDMAP.name_of(cid, chess_hint=True) or cid,
                                g.get("attack"), g.get("hp"),
                                "/".join(str(x) for x in (g.get("minionTypes") or []))))
        else:
            lines.append("商店: (空 —— 不在招募阶段, 或刚进对局还没同步)")
        taken = s.get("taken") or {}
        hot = sorted(taken.items(), key=lambda kv: -int(kv[1] or 0))[:8]
        if hot:
            lines.append("")
            lines.append("本局已被买走最多(卡池剩余越少越难刷到):")
            for cid, n in hot:
                base = IDMAP.chess_pool_size(cid) or 0
                lines.append("  %-14s 已被买 %s 张, 池内粗估还剩 %s/%s"
                             % (IDMAP.name_of(cid, chess_hint=True) or cid, n,
                                max(0, base - int(n or 0)), base))
        self._dec_set_text(self.dec_snap_text, "\n".join(lines))
        self.dec_snap_var.set("已更新 %s%s" % (datetime.now().strftime("%H:%M:%S"),
                                              "(自动)" if quiet else ""))

    # ---------- Tab: 快捷键 (v4.54★) ----------
    # ================= v4.85★ 「节奏」页 =================
    # 脚本里那些【写死的延迟】逐项可改 —— 用户原话: "这个新开一个 python 端的 tab 页都可改?
    # 且加上说明? 尤其是遣散武将"。逐项说明见 TIMING_ROWS(界面上的灰字就是它)。
    def _build_timing_tab(self):
        frame = self._make_scrollable_tab(self.tab_timing)
        tk.Label(frame, text="这里改的是【脚本里写死的那些等待】—— 以前只能靠剧本页顶部「速度(倍)」"
                             "整体压缩, 而倍速连『等服务器结算』一起压掉, 于是日志常见"
                             "「包发了但没生效」。现在哪一项慢就单独调哪一项:"
                             "框里就是当前值(160 这类只是初始值, 改成 80 就是 80), "
                             "填 0 = 这一步完全不等, 清空 = 回到默认。"
                             "改完点【保存并推送】才生效(也存进 config.json, 下次连上游戏页自动带过去)。",
                 bg=BG, fg=DIM, font=(FH, 9), wraplength=980, justify="left").pack(anchor="w", pady=(0, 6))

        _cfgt = self.cfg.get("timing") or {}
        self.timing_vars = {}
        groups = [
            ("遣散 (你点名的那一项)", ["recycleGap", "recycleSpellGap"]),
            ("轮询片: 等服务器 / 等窗口的每一小步", ["pollFast", "pollMid", "pollSlow"]),
            ("手牌区刷新 (重建列表很费性能, 调大能省 CPU)",
             ["handRefreshA", "handRefreshB", "handSchedA", "handSchedB", "handSchedC", "sentinelMs"]),
            ("商店刷新 (确认服务器响应与刷新间隔)",
             ["shopVerifyMs", "shopPollMs", "shopVerifyCapMs", "shopRefreshGapMs"]),
            ("快捷键 / 停止", ["hkFollowGapMs", "stopSliceMs"]),
        ]
        for title, keys in groups:
            box = tk.LabelFrame(frame, text=" " + title + " ", bg=BG, fg=GOLD, bd=1,
                                relief="groove", font=(FH, 10, "bold"))
            box.pack(fill="x", pady=(8, 0))
            for key in keys:
                row = TIMING_ROWS_BY_KEY.get(key)
                if not row:
                    continue
                _, label, dft, hint = row
                r = tk.Frame(box, bg=BG)
                r.pack(fill="x", padx=8, pady=(6, 0))
                tk.Label(r, text=label + ":", bg=BG, fg=FG, font=(FH, 9), width=24,
                         anchor="w").pack(side="left")
                # v4.85★ 框里直接填【当前生效值】, 不是留空 —— 空框看着就像"写死了、改不了";
                #   160/150/… 都只是初始值, 改成 80 就是 80(保存并推送后立刻生效)。
                #   清空那一项才是"回到脚本默认"。
                _cur = _cfgt.get(key, None)
                var = tk.StringVar(value=str(dft if _cur in (None, "") else _cur))
                tk.Entry(r, textvariable=var, bg="#1e1e1e", fg=FG, insertbackground=FG,
                         font=(FH, 9), width=8).pack(side="left")
                tk.Label(r, text="毫秒 (默认 %d —— 直接改就行; 清空 = 回到默认)" % dft, bg=BG,
                         fg=DIM, font=(FH, 8)).pack(side="left", padx=(6, 0))
                self.timing_vars[key] = var
                tk.Label(box, text=hint, bg=BG, fg=DIM, font=(FH, 8), wraplength=960,
                         justify="left").pack(anchor="w", padx=26, pady=(1, 2))

        # v4.163★ 【内部零等待】总闸 —— 用户原话:
        #   "每个步骤里面不要加、在外面加我们写的那个等待多少毫秒这样进行延迟"
        #   开了它: 上阵/遣散/刷新/挑牌/用锦囊这些步骤【内部一个毫秒都不等】, 发完请求立刻返回;
        #   节奏全靠剧本里自己插的「等待 N 毫秒」步骤。关掉 = 各步骤回到自己的等待(老行为)。
        #   ★ 与上面那些毫秒数的区别: 那些是"等多久", 这个是"还要不要等" —— 所以单独一个开关。
        zw_row = tk.Frame(frame, bg=BG)
        zw_row.pack(fill="x", pady=(12, 0))
        self.zero_wait_var = tk.BooleanVar(value=bool(self.cfg.get("zero_wait", False)))
        tk.Checkbutton(zw_row, text="内部零等待(延迟全部由剧本里的「等待」步骤给)",
                       variable=self.zero_wait_var, bg=BG, fg=FG, selectcolor="#1e1e1e",
                       activebackground=BG, activeforeground=FG, font=(FH, 9, "bold"),
                       command=self._zero_wait_toggle).pack(side="left")
        tk.Label(zw_row, text="勾选后: 步骤内部不再自己等(连那些「填 0 也不生效」的硬下限一起归零), "
                              "发完请求立刻走下一步 —— 要多快就在步骤之间插「等待 N 毫秒」。\n"
                              "⚠ 代价: 服务器没落地就往下走, 日志会出现「没生效 / 还在手牌」—— "
                              "那是【没等】不是失败, 把「等待」加大即可。挑牌窗也是: 前面不等就不会有窗。",
                 bg=BG, fg=DIM, font=(FH, 8), wraplength=980,
                 justify="left").pack(anchor="w", pady=(1, 2))

        # v4.170★ 【手牌区不重建(跑剧本时)】—— 用户原话"手牌区可以不重建? 在大页面加上手牌区不重建"。
        #   勾上后, 跑剧本过程中【完全不重建手牌区】(连 handFlushMs 的兜底周期都不要), 只在剧本收尾/
        #   按停时才刷一次 —— 比「极速档」更彻底的去卡方式(极速档只是把周期拉长到 6 秒)。
        #   不影响任何判定: 剧本读的是 manager 里的数据不是界面节点。代价: 跑的过程中手牌画面是旧的,
        #   想看实时手牌按 Alt+H 手动补一次。脚本侧由 scheduleHandRefresh 读 sgsT('handNoRebuild') 实现。
        hnb_row = tk.Frame(frame, bg=BG)
        hnb_row.pack(fill="x", pady=(10, 0))
        self.hand_no_rebuild_var = tk.BooleanVar(value=bool(self.cfg.get("hand_no_rebuild", False)))
        tk.Checkbutton(hnb_row, text="手牌区不重建(跑剧本时只在收尾/按停时刷一次)",
                       variable=self.hand_no_rebuild_var, bg=BG, fg=FG, selectcolor="#1e1e1e",
                       activebackground=BG, activeforeground=FG, font=(FH, 9, "bold"),
                       command=self._hand_no_rebuild_toggle).pack(side="left")
        tk.Label(hnb_row, text="勾选后: 跑剧本时手牌区【完全不重建】(比极速档更彻底), 只在剧本结束/按停时刷一次。"
                              "跑的过程中手牌画面是旧的, 想看实时按 Alt+H。不影响任何条件判定。",
                 bg=BG, fg=DIM, font=(FH, 8), wraplength=980,
                 justify="left").pack(anchor="w", pady=(1, 2))

        # v4.176★ 【跳过三连合成动画】—— 用户原话"这个跳过三连在 python 端页面上加上开关按钮"
        #   (来源: sgscodex 里的 cy-跳过三连动画及3选1-dev.js)。
        #   三连合成时游戏会播一段合成动画, 期间 CanOperate=false / IsPlayCompositeAni=true,
        #   剧本每一步和挑牌都要等它放完 —— 一次三连白等好几秒。
        #   ★ 与「禁用三连」不是一回事: 那个管"合不合", 这个管"已经合了, 那段动画等不等"。
        sta_row = tk.Frame(frame, bg=BG)
        sta_row.pack(fill="x", pady=(8, 0))
        self.skip_triple_ani_var = tk.BooleanVar(value=bool(self.cfg.get("skip_triple_ani", False)))
        tk.Checkbutton(sta_row, text="跳过三连合成动画(一播就跳, 不干等)",
                       variable=self.skip_triple_ani_var, bg=BG, fg=FG, selectcolor="#1e1e1e",
                       activebackground=BG, activeforeground=FG, font=(FH, 9, "bold"),
                       command=self._skip_triple_ani_toggle).pack(side="left")
        tk.Label(sta_row, text="勾选后: 三连一开始合成, 立刻跳掉那段动画 —— 先调它自己的完成回调, "
                              "叫不动才撕节点, 最后强制复位「动画中」两个标志(不复位会让后面所有步骤永久卡死)。"
                              "与「禁用三连」是两件事: 那个是【别合】, 这个是【合了别等】。",
                 bg=BG, fg=DIM, font=(FH, 8), wraplength=980,
                 justify="left").pack(anchor="w", pady=(1, 2))

        btns = tk.Frame(frame, bg=BG)
        btns.pack(fill="x", pady=(10, 4))
        # v4.134★ 档位: 一键把上面十几项整组改掉(填表 + 保存 + 推送, 一步到位)。
        #   逐项可改是对的, 但"想快一点"的人不知道该动哪几项, 于是永远停在默认值上。
        tk.Label(btns, text="档位:", bg=BG, fg=FG, font=(FH, 9, "bold")).pack(side="left")
        for _nm, _tx, _cb in (("turbo", "⚡ 极速", WARN), ("standard", "标准", BTN),
                              ("safe", "保守(稳)", BTN)):
            tk.Button(btns, text=_tx, command=lambda n=_nm: self._timing_preset(n),
                      bg=_cb, fg=FG, font=(FH, 9, "bold"), padx=8,
                      cursor="hand2").pack(side="left", padx=(6, 0))
        tk.Label(btns, text="│", bg=BG, fg=DIM, font=(FH, 9)).pack(side="left", padx=(10, 2))
        tk.Button(btns, text="保存并推送到游戏页", command=self._timing_save,
                  bg=GREEN, fg=FG, font=(FH, 10, "bold"), padx=10, cursor="hand2").pack(side="left")
        tk.Button(btns, text="恢复默认(清空全部)", command=self._timing_reset,
                  bg=BTN, fg=FG, font=(FH, 9), padx=8, cursor="hand2").pack(side="left", padx=(10, 0))
        tk.Label(frame, text="★ 这些值全是【等服务器 / 等界面】的时间, 不是限速 —— 压太狠的典型症状"
                             "就是日志出现「包发了但没生效」「发了但还在手牌」。"
                             "只想让整个剧本跑快, 先去剧本页顶部调「速度(倍)」。",
                 bg=BG, fg=WARN, font=(FH, 8), wraplength=980,
                 justify="left").pack(anchor="w", pady=(4, 0))

    def _timing_cfg(self):
        """从界面读: 只收【填了且是数字】的项 —— 留空/乱填的那一项保持脚本默认。"""
        out = {}
        for key, var in (getattr(self, "timing_vars", None) or {}).items():
            s = str(var.get()).strip()
            if not s:
                continue
            try:
                n = int(float(s))
            except (TypeError, ValueError):
                continue
            out[key] = max(0, min(60000, n))
        return out

    def _push_timing(self, cfg=None):
        """下发给游戏页。空表 = 没什么要改(脚本本来就是默认值), 不必发。"""
        cfg = dict(cfg if cfg is not None else (self.cfg.get("timing") or {}))
        # v4.170★ 手牌区不重建: 随节奏表一起下发, 让脚本侧 sgsT('handNoRebuild') 读得到
        #   (applyTiming 只认数字, 所以发 1/0 而不是 bool)。
        cfg["handNoRebuild"] = 1 if self.cfg.get("hand_no_rebuild") else 0
        if not cfg:
            return 0
        if not (self.ops and self.ops.connected()):
            return 0
        try:
            return int(self.ops.update_timing(cfg) or 0)
        except Exception as e:
            self.log(f"[节奏] 推送失败: {e}", level="WARN")
            return 0

    def _timing_save(self):
        cfg = self._timing_cfg()
        self.cfg["timing"] = cfg
        try:
            save_config(self.cfg)
        except Exception as e:
            self.log(f"[节奏] 保存 config.json 失败: {e}", level="WARN")
        # v4.101★ 推送挪后台(update_timing 超时 2 秒, 以前在主线程点一下僵 2 秒)
        def _timing_done(r, err):
            if err:
                self.log(f"[节奏] 推送失败: {err}", level="WARN")
            else:
                self.log("[节奏] 已保存 %d 项(留空的项 = 用脚本默认) —— %s"
                         % (len(cfg), "已推送到游戏页"
                            if r else "游戏页没连上, 下次连上时会自动带过去"))

        self._run_bg("timing_push", (lambda: self._push_timing(cfg)),
                     _timing_done, wait="已保存, 正在推送到游戏页…")

    def _push_fast_mode(self):
        """v4.145★ 把【极速】开关推给页面 —— 单独一个方法, 不塞进 _push_timing。

        为什么不塞进去: _push_timing 开头就有 `if not cfg: return 0`(节奏表空着就省一次
        通信), 而开关跟节奏表没关系 —— 塞进去的话, 从没调过节奏的用户(表是空的)
        就永远收不到这个开关, 表现是"我明明开了却没变快"。
        """
        if not (self.ops and self.ops.connected()):
            return False
        try:
            return bool(self.ops.update_fast_mode(bool(self.cfg.get("fast_mode", True))))
        except Exception as e:
            self.log(f"[极速] 推送失败: {e}", level="WARN")
            return False

    def _push_zero_wait(self):
        """v4.163★ 把【内部零等待】总闸推给页面 —— 与 _push_fast_mode 同理, 不塞进 _push_timing

        (节奏表空着就省一次通信, 而开关跟表没关系 —— 塞进去 = 从没调过节奏的用户永远收不到)。
        """
        if not (self.ops and self.ops.connected()):
            return False
        try:
            return bool(self.ops.update_zero_wait(bool(self.cfg.get("zero_wait", False))))
        except Exception as e:
            self.log(f"[零等待] 推送失败: {e}", level="WARN")
            return False

    def _push_auto_follow_up(self):
        """v4.185★ 把【自动随征】后台引擎的开关推给页面 —— 与 _push_zero_wait 同理, 不塞进 _push_timing
        (节奏表空着就省一次通信, 而开关跟表没关系)。
        """
        if not (self.ops and self.ops.connected()):
            return False
        try:
            return bool(self.ops.update_auto_follow_up(bool(self.cfg.get("auto_follow_up", True))))
        except Exception as e:
            self.log(f"[自动随征] 推送失败: {e}", level="WARN")
            return False

    # ---------- v4.182★ 【性能】渲染闸门 ----------
    RENDER_KEYS = ("off", "badge", "hand", "skin", "stats")

    def _render_cfg(self):
        """读 config 的 render 段并补齐缺的键。

        为什么要补: 老用户的 config.json 里根本没有这一段(DEFAULT_CONFIG 是后加的),
        不全就传 —— 界面上五个勾会长得出来但读到的全是 None, 而 update_render 只发
        "存在的键", 少发一位就等于"那一项保持页面上的旧状态", 看着像没生效。
        """
        cur = self.cfg.get("render") or {}
        if not isinstance(cur, dict):
            cur = {}
        return dict((k, bool(cur.get(k, False))) for k in self.RENDER_KEYS)

    def _zero_wait_toggle(self):
        """v4.163★ 勾上/取消【内部零等待】: 存 config + 立刻推给游戏页(后台线程)。"""
        on = bool(self.zero_wait_var.get())
        self.cfg["zero_wait"] = on
        try:
            save_config(self.cfg)
        except Exception as e:
            self.log(f"[零等待] 保存 config.json 失败: {e}", level="WARN")

        def _zw_done(r, err):
            if err:
                self.log(f"[零等待] 推送失败: {err}", level="WARN")
            elif r:
                self.log("[零等待] 已%s —— %s" % (
                    "开启" if on else "关闭",
                    "步骤内部不再自己等, 延迟请写在剧本的「等待 N 毫秒」步骤上"
                    if on else "已恢复各步骤自己的等待"))
            else:
                self.log("[零等待] 已保存(游戏页没连上, 下次连上时会自动带过去)")

        self._run_bg("zero_wait_push", (lambda: self._push_zero_wait()),
                     _zw_done, wait="正在推送零等待开关…")

    def _auto_follow_up_toggle(self):
        """v4.185★ 勾上/取消【自动随征后台引擎】: 存 config + 立刻推给游戏页(后台线程)。"""
        on = bool(self.auto_follow_up_var.get())
        self.cfg["auto_follow_up"] = on
        try:
            save_config(self.cfg)
        except Exception as e:
            self.log(f"[自动随征] 保存 config.json 失败: {e}", level="WARN")

        def _afu_done(r, err):
            if err:
                self.log(f"[自动随征] 推送失败: {err}", level="WARN")
            elif r:
                self.log("[自动随征] 后台引擎已%s —— %s" % (
                    "开启" if on else "关闭",
                    "手牌出现随征卡会自动随征给「随征目标」指定的人"
                    if on else "已停掉后台自动随征(手动 Shift+3 / 剧本随征仍可)"))
            else:
                self.log("[自动随征] 已保存(游戏页没连上, 下次连上时会自动带过去)")

        self._run_bg("auto_follow_up_push", (lambda: self._push_auto_follow_up()),
                     _afu_done, wait="正在推送自动随征开关…")

    def _refresh_auto_follow_up(self):
        """v4.185★ 读游戏页当前的 SGS_AUTO_FOLLOW_UP_ON(后台引擎是否在跑)实际值, 同步勾选框。

        剧本里的「开启/关闭自动随征」步骤会 start/stop 后台循环、改页面标志, 但 config 里的勾选框
        不会自动跟着变 —— 点这个按钮把页面真实状态拉回来, 免得"我明明在剧本里关了, 这里还显示勾着"。
        """
        if not (self.ops and self.ops.connected()):
            self.log("[自动随征] 游戏页没连上, 无法读取实际状态", level="WARN")
            return
        try:
            r = self.ops.send_recv("eval", js="window.SGS_AUTO_FOLLOW_UP_ON !== false", timeout=2.0)
            on = (r is True) or (str(r).strip().lower() in ("true", "1"))
            self.auto_follow_up_var.set(on)
            self.log("[自动随征] 游戏页实际状态: %s" % ("开启" if on else "关闭"))
        except Exception as e:
            self.log(f"[自动随征] 读取实际状态失败: {e}", level="WARN")

    def _hand_no_rebuild_toggle(self):
        """v4.170★ 勾上/取消【手牌区不重建】: 存 config + 立刻推给游戏页(后台线程)。"""
        on = bool(self.hand_no_rebuild_var.get())
        self.cfg["hand_no_rebuild"] = on
        try:
            save_config(self.cfg)
        except Exception as e:
            self.log(f"[手牌不重建] 保存 config.json 失败: {e}", level="WARN")

        def _hnb_done(r, err):
            if err:
                self.log(f"[手牌不重建] 推送失败: {err}", level="WARN")
            elif r:
                self.log("[手牌不重建] 已%s —— 跑剧本时手牌区不再重建, 只在收尾/按停时刷一次"
                         % ("开启" if on else "关闭"))
            else:
                self.log("[手牌不重建] 已保存(游戏页没连上, 下次连上时会自动带过去)")

        self._run_bg("hnb_push", (lambda: self._push_timing()),
                     _hnb_done, wait="正在推送手牌不重建开关…")

    def _skip_triple_ani_toggle(self):
        """v4.176★ 勾上/取消【跳过三连动画】: 存 config + 立刻推给游戏页(后台线程)。"""
        on = bool(self.skip_triple_ani_var.get())
        self.cfg["skip_triple_ani"] = on
        try:
            save_config(self.cfg)
        except Exception as e:
            self.log(f"[跳过三连动画] 保存 config.json 失败: {e}", level="WARN")

        def _sta_done(r, err):
            if err:
                self.log(f"[跳过三连动画] 推送失败: {err}", level="WARN")
            elif r:
                self.log("[跳过三连动画] 已%s —— %s"
                         % ("开启" if on else "关闭",
                            "三连合成就跳动画" if on else "恢复等动画放完"))
            else:
                self.log("[跳过三连动画] 已保存(游戏页没连上, 或脚本低于 v4.176; "
                         "下次连上会自动带过去)")

        self._run_bg("sta_push", (lambda: self._push_skip_triple_ani()), _sta_done,
                     wait="正在推送跳过三连动画开关…")

    def _push_skip_triple_ani(self, log=True):
        """v4.176★ 把 config 里的 skip_triple_ani 下发到游戏页(连上时自动带过去)。

        返回 bool: 脚本确认收到了就是 True; 没连上/脚本太旧 = False(调用方按需记日志)。
        """
        on = bool(self.cfg.get("skip_triple_ani", False))
        try:
            ok = bool(self.ops.set_skip_triple_ani(on))
        except Exception as e:
            if log:
                self.log(f"[跳过三连动画] 下发异常: {e}", level="WARN")
            return False
        if log:
            self.log("[跳过三连动画] 已%s" % ("开启" if on else "关闭"))
        return ok

    # ==================================================================
    # v4.182★ 「性能」页 —— 渲染闸门(取消渲染 / 分项暂停)
    #   为什么单开一页而不塞进「节奏」页: 节奏那页管的是【等不等、等多久】(毫秒, 改了会影响
    #   操作是否落地); 这一页管的是【画不画】(是否重绘, 只省 CPU、不影响任何发包与判定)。
    #   混在一页, 用户很容易把"加速"和"省画面"当成同一件事来调。
    # ==================================================================
    def _build_perf_tab(self):
        frame = self._make_scrollable_tab(self.tab_perf)
        tk.Label(frame, text="这里管的是【画不画】—— 脚本在游戏页上的哪些画面还要不要刷新。"
                             "勾哪一项, 那一类重绘立刻停掉; 关掉就恢复(这就是「取消渲染」之后把渲染打开)。"
                             "★ 只影响画面: 所有判定读的都是 manager 里的数据, 发包/下单/挑牌一步不少。\n"
                             "想让整脚本下来的重绘全停, 勾最上面那个主开关就行, 不用一个个勾。",
                 bg=BG, fg=DIM, font=(FH, 9), wraplength=980, justify="left").pack(anchor="w", pady=(0, 6))

        _cfgp = self.cfg.get("render") or {}
        self.perf_vars = {}
        rows = [
            ("off", "取消渲染(主开关)",
             "开 = 下面这几类重绘【全部暂停】: 角标/提示条、手牌区重建、换肤动画与注入、统计与录制重绘。\n"
             "恢复时把这一项关掉即可 —— 脚本一直在干活, 只是不刷新画面。"),
            ("badge", "暂停角标 / 提示条",
             "桥角标(那个「桥:…」的小条)不再更新; 连接状态与报错只在日志里看得到。"),
            ("hand", "暂停手牌区重建",
             "跑剧本时不再重建手牌列表(与「节奏」页的手牌区不重建同一效果, 这页也能管)。"),
            ("skin", "暂停换肤动画与注入",
             "动态皮肤轮播与资源注入轮询停住 —— 这两个是常驻定时器里最勤的一对。"),
            ("stats", "暂停统计 / 录制重绘",
             "助手端的『统计』面板与『宏录制』事件表不再跟着刷(数据照记, 只是不画)。"),
        ]
        for key, label, hint in rows:
            row = tk.Frame(frame, bg=BG)
            row.pack(fill="x", pady=(8, 0))
            _master = (key == "off")
            var = tk.BooleanVar(value=bool(_cfgp.get(key, False)))
            tk.Checkbutton(row, text=label, variable=var, bg=BG,
                           fg=GOLD if _master else FG, selectcolor="#1e1e1e",
                           activebackground=BG, activeforeground=FG,
                           font=(FH, 10 if _master else 9, "bold"),
                           command=self._perf_toggle).pack(side="left")
            self.perf_vars[key] = var
            tk.Label(frame, text=hint, bg=BG, fg=DIM, font=(FH, 8), wraplength=960,
                     justify="left").pack(anchor="w", padx=26, pady=(1, 2))

        btns = tk.Frame(frame, bg=BG)
        btns.pack(fill="x", pady=(12, 4))
        tk.Button(btns, text="全部暂停(取消渲染)", command=lambda: self._perf_all(True),
                  bg=WARN, fg=FG, font=(FH, 10, "bold"), padx=10, cursor="hand2").pack(side="left")
        tk.Button(btns, text="全部恢复(打开渲染)", command=lambda: self._perf_all(False),
                  bg=GREEN, fg=FG, font=(FH, 10, "bold"), padx=10, cursor="hand2").pack(side="left", padx=(10, 0))
        tk.Label(btns, text="│", bg=BG, fg=DIM, font=(FH, 9)).pack(side="left", padx=(10, 2))
        tk.Button(btns, text="保存并推送到游戏页", command=self._perf_save,
                  bg=BTN, fg=FG, font=(FH, 9), padx=8, cursor="hand2").pack(side="left")
        tk.Label(frame, text="★ 勾了会【立刻生效】(顺手存进 config.json, 下次连上游戏页自动带过去)。"
                             "排查「为什么画面不更新」时记得回来这一页看一眼 —— 大概率是这里勾上了。",
                 bg=BG, fg=WARN, font=(FH, 8), wraplength=980,
                 justify="left").pack(anchor="w", pady=(4, 0))

    def _perf_cfg(self):
        """从界面读当前勾选(全部键都给bool —— 桥只认 off/badge/hand/skin, stats 助手端自己用)。"""
        out = {}
        for key, var in (getattr(self, "perf_vars", None) or {}).items():
            out[key] = bool(var.get())
        return out

    @staticmethod
    def _perf_desc(cfg):
        on = [k for k, v in (cfg or {}).items() if v]
        if not on:
            return "全部恢复(照常重绘)"
        if "off" in on:
            return "取消渲染(全部重绘暂停)"
        return "暂停: " + ", ".join(on)

    def _push_render(self, cfg=None):
        """v4.182★ 把渲染闸门推给游戏页 —— 与 _push_fast_mode 同理, 不并入 _push_timing
        (节奏表空着就省一次通信, 而这个开关跟节奏表没关系 —— 塞进去会让没调过节奏的人收不到)。

        不传 cfg = 按 config.json 那份推, 且用 _render_cfg() 【补齐五个键】: 老用户的
        config.json 里可能压根没有 render 段, 直接发 `{}` 虽然语义上等于"全部恢复",
        但页面若已经处在"某项暂停"的状态(例如刚刷新过的页面被别的窗口推过), 缺键就
        覆盖不掉那一项。发全五项才叫"按存档对齐"。
        """
        cfg = dict(cfg if cfg is not None else self._render_cfg())
        if not (self.ops and self.ops.connected()):
            return False
        try:
            return bool(self.ops.update_render(cfg))
        except Exception as e:
            self.log(f"[性能] 推送失败: {e}", level="WARN")
            return False

    def _render_paused(self, kind):
        """v4.182★ 助手端自己的重绘闸门: 主开关 off 或对应分项为真 → 暂停这一类重绘。

        与 _push_render 的分工: 那个把开关推给【游戏页】(桥),
        这个是给【助手端】自己画的统计面板 / 宏录制事件表用的。
        """
        try:
            r = self.cfg.get("render") or {}
            return bool(r.get("off")) or bool(r.get(kind))
        except Exception:
            return False

    def _perf_toggle(self):
        """勾上/取消任一开关: 存 config + 立刻推给游戏页(后台线程)。"""
        cfg = self._perf_cfg()
        self.cfg["render"] = cfg
        try:
            save_config(self.cfg)
        except Exception as e:
            self.log(f"[性能] 保存 config.json 失败: {e}", level="WARN")

        desc = self._perf_desc(cfg)

        def _perf_done(r, err):
            if err:
                self.log(f"[性能] 推送失败: {err}", level="WARN")
            elif r:
                self.log("[性能] 已应用到游戏页 —— %s" % desc)
            else:
                self.log("[性能] 已保存(游戏页没连上, 下次连上时会自动带过去)")

        self._run_bg("perf_push", (lambda: self._push_render(cfg)),
                     _perf_done, wait="正在推送渲染闸门…")

    def _perf_all(self, on):
        """一键全部暂停 / 全部恢复: 勾选全改 + 保存 + 推送(省得一个个勾)。"""
        for _key, var in (getattr(self, "perf_vars", None) or {}).items():
            try:
                var.set(bool(on))
            except Exception:
                pass
        self._perf_toggle()

    def _perf_save(self):
        # 与 _perf_toggle 同一条路径(读界面 → 存 → 推); 单独留一个名字只是为了让按钮语义清楚。
        self._perf_toggle()

    def _timing_preset(self, name):
        """v4.134★ 一键套用一档节奏(极速 / 标准 / 保守): 填表 + 保存 + 推送, 一步到位。

        为什么单独开一个方法而不只是填表: 只填表的话用户还得再点一次「保存并推送」,
        而"点档位"这个动作本身就表达了"我要它现在生效" —— 少一步就少一个"点了没反应"的误会。
        """
        try:
            preset = (TIMING_PRESETS or {}).get(name) or {}
        except Exception:
            preset = {}
        if not preset:
            self.log(f"[节奏] 没有这一档: {name}")
            return
        # 只改【界面上真实存在】的项: 预设里多出来的 key(脚本没这一项)不能凭空造一个框
        hit = 0
        for key, var in (getattr(self, "timing_vars", None) or {}).items():
            if key in preset:
                var.set(str(preset[key]))
                hit += 1
        label = {"turbo": "极速", "standard": "标准", "safe": "保守"}.get(name, name)
        self.log(f"[节奏] 已套用「{label}」档({hit} 项) —— 手牌没刷出来时按 Alt+H 手动补一次")
        # v4.145★ 只有【极速】档把硬开关一起打开; 选标准/保守表达的正是"我要稳", 那就自动关掉,
        #   不然"点了保守却还是不等服务器"就是界面承诺与实际行为不一致(排查时最容易跑偏)。
        self.cfg["fast_mode"] = (name == "turbo")
        # v4.163★ 【内部零等待】也跟着档位走: 极速档 = "一步都不等"(延迟自己用「等待」步骤加),
        #   标准/保守 = 各步骤照自己的等待来。界面上那个勾要同步刷新 —— 否则"选了档位、勾还是旧的",
        #   用户看一眼以为没生效, 又去点一遍。
        self.cfg["zero_wait"] = (name == "turbo")
        if hasattr(self, "zero_wait_var"):
            try:
                self.zero_wait_var.set(bool(self.cfg["zero_wait"]))
            except Exception:
                pass
        self._timing_save()
        # 两条推送各走各的(节奏表 / 零等待开关), 与 _push_fast_mode 的推送同理
        self._run_bg("zero_wait_push", (lambda: self._push_zero_wait()), None)

    def _timing_reset(self):
        # v4.85★ 恢复默认 = 把框填回默认值(不是清空) —— 空框会让人以为"这项没生效/改不了"
        for key, var in (getattr(self, "timing_vars", None) or {}).items():
            row = TIMING_ROWS_BY_KEY.get(key)
            var.set(str(row[2]) if row else "")
        self.cfg["timing"] = {}
        # v4.170★ 手牌区不重建 也一起回到默认(关)
        self.cfg["hand_no_rebuild"] = False
        if hasattr(self, "hand_no_rebuild_var"):
            try:
                self.hand_no_rebuild_var.set(False)
            except Exception:
                pass
        try:
            save_config(self.cfg)
        except Exception as e:
            self.log(f"[节奏] 保存 config.json 失败: {e}", level="WARN")
        if self.ops and self.ops.connected():
            # v4.101★ 空表 = 脚本端 resetTiming()(超时 2 秒) —— 挪后台, 别僵住界面
            self._run_bg("timing_reset", (lambda: self.ops.update_timing({})),
                         self._bg_log_done("节奏", "已恢复脚本默认延迟(游戏页已同步)"),
                         wait="正在恢复默认…")
        else:
            self.log("[节奏] 已清空 —— 游戏页没连上, 下次连上时就是默认")

    def _build_hotkey_tab(self):
        """把键盘上的那些快捷键搬到按钮上, 外加一个能直接往游戏页控制台发指令的输入框。

        用户原话: "那些快捷键操作, 加到新的一个 tab 页中可以直接点击 python 端按钮触发, 然后有
        简单的日志信息; 这个日志不需要一直发送, 只有点击 python 端的快捷键才显示, 还有那个 tab
        快捷键才显示; 还有可以直接发送消息到 f12 后的那个控制台内容, 直接发指令, 例如 setCoin(100)"。

        这页只干三件事:
          ① 按钮 → 调游戏页 window.__sgsHotkey.<名字>(...) —— 与键盘走的是**同一个函数**
             (油猴 v4.54 才暴露; 在那之前 App 调的 window.useRightmostSpell() 之类在 window 上
             根本不存在, 所以"点按钮代替按键"必然失败);
          ② 输入框 → 把整段 JS 原样丢进游戏页 eval, 等价于在游戏页 F12 控制台里敲一行;
          ③ 本页自己的日志: **只**记"点了本页按钮"和"游戏页按了 Tab 键"这两类事件 ——
             既不订阅全局日志也不做轮询, 所以它不会像主日志那样一直刷屏。
        """
        frame = self._make_scrollable_tab(self.tab_hotkey)

        tk.Label(frame, text="点按钮 = 在游戏页按下对应快捷键(两边调用同一个函数); "
                             "所有结果只写在本页下方日志里, 不进『日志』页。",
                 bg=BG, fg=DIM, font=(FH, 9)).pack(anchor="w")

        # ---- v4.83★ 锦囊 / 随征【优先名单】 ----
        #   用户问"怎么用优先名单 / python 端有吗 / 需要新加一个 tab 吗"。不用新 tab:
        #   Shift+2 / Shift+3 的按钮就在本页下面, 名单填在这里 → 点【保存并推送】立刻下发到
        #   游戏页(window.SPELL_PRIORITY / window.FOLLOWUP_PRIORITY), 再点按钮(或按键)就按这份
        #   名单识别 + 使用。识别本身仍是"全扫手牌"(见脚本 collectHandSpells / collectHandFollowUps),
        #   名单只决定【先用哪张】—— 不填就用"随征卡牌ID"那份表和手牌里认出来的锦囊。
        pr = tk.LabelFrame(frame, text=" 锦囊 / 随征 优先名单 (Shift+2 / Shift+3 先用哪张) ",
                           bg=BG, fg=GOLD, bd=1, relief="groove", font=(FH, 10, "bold"))
        pr.pack(fill="x", pady=(8, 0))
        _cfgp = (self.cfg.get("priority_lists") or {})

        def _pr_row(parent, label, var, hint):
            r = tk.Frame(parent, bg=BG)
            r.pack(fill="x", padx=8, pady=(6, 0))
            tk.Label(r, text=label, bg=BG, fg=FG, font=(FH, 9), width=14, anchor="w").pack(side="left")
            tk.Entry(r, textvariable=var, bg="#1e1e1e", fg=FG, insertbackground=FG,
                     font=(FH, 9)).pack(side="left", fill="x", expand=True, padx=6)
            tk.Label(parent, text=hint, bg=BG, fg=DIM, font=(FH, 8),
                     wraplength=0).pack(anchor="w", padx=22, pady=(1, 0))

        self.hk_spell_prio_var = tk.StringVar(value=_cfgp.get("spell", ""))
        self.hk_follow_prio_var = tk.StringVar(value=_cfgp.get("followup", ""))
        self.hk_follow_target_var = tk.StringVar(value=_cfgp.get("followup_target", ""))
        # v4.185★ 自动随征【后台引擎】开关: 勾上=页面后台循环盯手牌、有随征卡自动随征给「随征目标」的人
        self.auto_follow_up_var = tk.BooleanVar(value=bool(self.cfg.get("auto_follow_up", True)))
        _pr_row(pr, "锦囊优先名单:", self.hk_spell_prio_var,
                "Shift+2 用。逗号分隔, 越靠前越优先, 例: 瞒天过海·六,联军盛宴·五 —— "
                "手牌里有哪张先用哪张(同名的多张从最右边那张开始)。留空 = 手牌里认出来的锦囊, 从最右开始用。")
        _pr_row(pr, "随征优先名单:", self.hk_follow_prio_var,
                "Shift+3 用。例: 黄巾兵,薛灵芸。留空 = 认助手『随征卡牌ID』那份表(那份表不用动, 保持原样)。")
        _pr_row(pr, "随征给谁:", self.hk_follow_target_var,
                "留空 = 老行为(阵上位置0)。填武将名(例: 张梁)就按名字在阵上找那个人给。")
        # v4.105★ 锦囊黑名单: 抄上游 sgscodex 1.0.3 的 Alt+3 —— 那些"自动用出去反而坏事 /
        #   根本用不了"的锦囊, 识别归识别, 但自动使用的三条路都跳过它; 「遣散锦囊」照卖。
        self.hk_spell_block_var = tk.StringVar(
            value=_cfgp.get("spell_blacklist", "斗转,破釜,增兵,釜底抽薪"))
        _pr_row(pr, "锦囊黑名单:", self.hk_spell_block_var,
                "v4.105★ 名单里的锦囊【不自动使用】(Shift+2 / 剧本「用锦囊」/ 陆逊小抄 都遵守), "
                "但照常识别、『遣散锦囊』照卖。作者点名: 斗转会坏事、釜底抽薪根本用不了。留空 = 一张都不拦。")
        # v4.176★ 【用锦囊后需补挑的锦囊名单】—— 与「锦囊优先名单」(决定先用哪张)不是一回事:
        #   这个决定"哪些锦囊用出去之后还要手动再挑一次, 用完脚本自动补挑"(草船/三顾最常见)。
        #   默认 草船,三顾; 留空 = 脚本默认(草船,三顾)。
        self.hk_spell_need_pick_var = tk.StringVar(
            value=self.cfg.get("spell_need_pick", "草船,三顾"))
        _pr_row(pr, "用锦囊后需补挑:", self.hk_spell_need_pick_var,
                "v4.176★ 用出去还要再手动挑一次的锦囊, 用完自动补挑(草船/三顾最常见)。"
                "逗号分隔, 留空 = 脚本默认(草船,三顾)。与「锦囊优先名单」不是一回事: "
                "那是决定先用哪张, 这个是决定哪些用完后要补挑。")

        sw = tk.Frame(pr, bg=BG)
        sw.pack(fill="x", padx=8, pady=(6, 2))
        self.hk_spell_all_var = tk.BooleanVar(value=bool(_cfgp.get("spell_use_all")))
        self.hk_follow_all_var = tk.BooleanVar(value=bool(_cfgp.get("followup_use_all")))
        tk.Checkbutton(sw, text="锦囊: 识别到的全用掉(从最右开始)", variable=self.hk_spell_all_var,
                       bg=BG, fg=FG, selectcolor="#33333f", activebackground=BG,
                       font=(FH, 9), bd=0, relief="flat").pack(side="left")
        tk.Checkbutton(sw, text="随征: 一次全给同一个人", variable=self.hk_follow_all_var,
                       bg=BG, fg=FG, selectcolor="#33333f", activebackground=BG,
                       font=(FH, 9), bd=0, relief="flat").pack(side="left", padx=(12, 0))

        # v4.185★ 自动随征【后台引擎】—— 放在随征优先名单这里最顺手(和 Shift+3 直接相关)。
        #   勾上 = 页面后台循环一直盯着【手牌区】, 一出现可随征的卡, 自动随征给「随征目标」指定的人;
        #   取消 = 停掉后台循环(手动 Shift+3 / 剧本「随征」「自动随征」仍可正常调, 不受它限制)。
        af = tk.Frame(pr, bg=BG)
        af.pack(fill="x", padx=8, pady=(2, 4))
        tk.Checkbutton(af, text="自动随征(后台): 勾上=手牌出现随征卡自动随征给『随征目标』的人",
                       variable=self.auto_follow_up_var, bg=BG, fg=GOLD,
                       selectcolor="#33333f", activebackground=BG, font=(FH, 9, "bold"),
                       bd=0, relief="flat", command=self._auto_follow_up_toggle).pack(side="left")
        tk.Button(af, text="↻ 读游戏页实际状态", command=self._refresh_auto_follow_up,
                  bg=BTN, fg=FG, font=(FH, 8), padx=6, cursor="hand2").pack(side="left", padx=(10, 0))

        btns = tk.Frame(pr, bg=BG)
        btns.pack(fill="x", padx=8, pady=(6, 8))
        tk.Button(btns, text="保存并推送到游戏页", command=self._hk_save_priority,
                  bg=GREEN, fg=FG, font=(FH, 10, "bold"), padx=10, cursor="hand2").pack(side="left")
        tk.Button(btns, text="按名单用锦囊(Shift+2)",
                  command=lambda: self._hk_btn_args("useRightmostSpell", "按名单用锦囊"),
                  bg=ORANGE, fg=FG, font=(FH, 9, "bold"), padx=8, cursor="hand2").pack(side="left", padx=(10, 0))
        tk.Button(btns, text="按名单随征(Shift+3)",
                  command=lambda: self._hk_btn_args("autoFollowUp", "按名单随征"),
                  bg=ORANGE, fg=FG, font=(FH, 9, "bold"), padx=8, cursor="hand2").pack(side="left", padx=(6, 0))
        tk.Label(pr, text="填法与剧本里一致: 写「A > B」= 先 A 没有才 B; 逗号分隔 = 谁在就用谁。"
                          "改完点【保存并推送】才下发(也存进 config.json, 下次开助手自动带上)。",
                 bg=BG, fg=DIM, font=(FH, 8)).pack(anchor="w", padx=10, pady=(0, 6))

        # ---- 分组按钮 ----
        groups = [
            ("手牌 / 上阵", [
                ("Shift+1  遣散最右", "discardRightmostHand", None),
                ("Shift+2  用锦囊", "useRightmostSpell", None),
                ("Shift+3  随征", "autoFollowUp", None),
                ("Shift+4  遣散指定卡", "discardTargetChessIds", None),
                ("Shift+5  全遣散手牌", "discardAllHand", None),
                ("Alt+0  上阵最右", "deployRightmostCard", None),
                # v4.188★ Alt+7 陈珪虚空触发丁奉(上游 v2.0.4 的原理版: 服务端结算"陈珪上阵+
                #   目标丁奉"后, 把游戏自动补发的空上阵包换掉并本地取消 → 陈珪留场, 再重发丁奉)
                # v4.188★ 解锁选择窗(Alt+6) / 结算探测(Alt+5) —— 与键盘同一个函数
                ("Alt+6  解锁选择窗", "cancelSelectWindow", None),
                ("Alt+5  结算探测(记账/对比)", "settleProbe", None),
                ("Alt+7  陈珪虚空", "deployChenGuiVoid", None),
                # v4.170★ Alt+8 陈珪三段链(陈珪上阵→目标上阵区最右→按B分流→遣散陈珪), 与键盘同一个函数
                ("Alt+8  陈珪链", "deployChenGuiChain", None),
                ("Alt+9  遣散上阵最右", "discardRightmostBattle", None),
                ("Shift+R  强制刷新UI", "forceRefreshUI", None),
            ]),
            ("商店 / 战斗 / 三连", [
                ("Space  跳过战斗", "skipBattle", None),
                ("R  刷新营帐", "refreshShop", None),
                ("F  锁定营帐", "lockShop", None),
                ("Tab  三连抑制(切换)", "toggleTripleCombine", None),
            ]),
            ("买营帐卡 (1-6 号位)", [
                ("买第 1 格", "buyChess", 0),
                ("买第 2 格", "buyChess", 1),
                ("买第 3 格", "buyChess", 2),
                ("买第 4 格", "buyChess", 3),
                ("买第 5 格", "buyChess", 4),
                ("买第 6 格", "buyChess", 5),
            ]),
        ]
        for title, items in groups:
            box = tk.LabelFrame(frame, text=" " + title + " ", bg=BG, fg=GOLD, bd=1, relief="groove",
                                font=(FH, 10, "bold"))
            box.pack(fill="x", pady=(8, 0))
            grid = tk.Frame(box, bg=BG)
            grid.pack(fill="x", padx=8, pady=6)
            cols = 4
            for idx, (label, name, arg) in enumerate(items):
                tk.Button(grid, text=label, bg=BTN, fg=BLUE, font=(FH, 9, "bold"),
                          padx=6, pady=3, cursor="hand2",
                          command=lambda n=name, l=label, a=arg: self._hk_btn(n, l, a)
                          ).grid(row=idx // cols, column=idx % cols, sticky="ew", padx=3, pady=2)
            for c in range(cols):
                grid.columnconfigure(c, weight=1)

        # ---- 直接发控制台指令 ----
        con = tk.LabelFrame(frame, text=" 直接向游戏页控制台发指令 (等价在游戏页 F12 里敲一行) ",
                            bg=BG, fg=GOLD, bd=1, relief="groove", font=(FH, 10, "bold"))
        con.pack(fill="x", pady=(8, 0))
        row = tk.Frame(con, bg=BG)
        row.pack(fill="x", padx=8, pady=(6, 2))
        tk.Label(row, text="指令:", bg=BG, fg=FG, font=(FH, 10)).pack(side="left")
        self.hk_cmd_var = tk.StringVar()
        ent = tk.Entry(row, textvariable=self.hk_cmd_var, bg="#1e1e1e", fg=FG,
                       insertbackground=FG, font=(FH, 10))
        ent.pack(side="left", fill="x", expand=True, padx=6)
        ent.bind("<Return>", lambda _e: self._hk_run_console())
        tk.Button(row, text="▶ 执行 (Enter)", command=self._hk_run_console, bg=GREEN, fg=FG,
                  font=(FH, 10, "bold"), padx=10, cursor="hand2").pack(side="left")
        tk.Label(con, text="写【表达式】即可, 例:  setCoin(100)   addCoin(-30)   showCoin()   window.SCRIPT_VER   "
                           "(写成 return x 会被 JS 拒绝 —— 脚本端执行的是 eval)",
                 bg=BG, fg=DIM, font=(FH, 8)).pack(anchor="w", padx=10, pady=(0, 6))

        # ---- 公共卡池探测 (v4.67★) ----
        # 为什么放在这一页: 这一页本来就是"对游戏页下命令 + 看回包"的地方, 而"能不能读到
        #   公共卡池"此刻还只是一次【实验】(所以探测 JS 写在 core/pool_probe.py, 走
        #   run_console_js 的 eval 通道, 而不是加进油猴脚本)——
        #   加进脚本的话, 每改一次探测逻辑用户就得重新注入 + 刷新游戏页, 迭代成本全压在用户身上。
        # 它回答什么: 卡池是公共的, 而助手端算概率用的 pool_taken 只统计【自己买走的】,
        #   于是"别刷了, 概率太低"这类结论其实不可靠。要修, 前提是先确认客户端到底有没有
        #   "某个棋子还剩几张"这份数据 —— 有就直接读, 没有就只能给下界(见 core/pool_probe.py)。
        pool = tk.LabelFrame(frame, text=" 公共卡池探测 (只读, 不改游戏任何状态) ",
                             bg=BG, fg=GOLD, bd=1, relief="groove", font=(FH, 10, "bold"))
        pool.pack(fill="x", pady=(8, 0))
        prow = tk.Frame(pool, bg=BG)
        prow.pack(fill="x", padx=8, pady=(6, 2))
        tk.Button(prow, text="▶ 探测卡池", command=self._hk_probe_pool, bg=BLUE, fg=FG,
                  font=(FH, 10, "bold"), padx=10, pady=2, cursor="hand2").pack(side="left")
        self.hk_pool_var = tk.StringVar(value="未探测")
        tk.Label(prow, textvariable=self.hk_pool_var, bg=BG, fg=BLUE,
                 font=(FH, 9, "bold")).pack(side="left", padx=8)
        tk.Label(pool,
                 text="扫 4 处: ① manager 属性里有没有『{棋子ID: 剩余张数}』形状的表(最有说服力);"
                      "② 商店发牌数据里有没有藏剩余字段;\n"
                      "③ 配置表的发牌权重(等级→各费用概率); ④ 已收到的协议样本里有没有池语义字段名。\n"
                      "结论写进下面日志; 原始报告存 window.__sgsPoolProbe"
                      "(游戏页控制台敲 JSON.stringify(window.__sgsPoolProbe) 看全量)。",
                 bg=BG, fg=DIM, font=(FH, 8), justify="left").pack(anchor="w", padx=10, pady=(0, 6))

        # ---- 本页日志 ----
        logbox = tk.LabelFrame(frame, text=" 快捷键日志 (只记录: 本页按钮 / 游戏页 Tab 键) ",
                               bg=BG, fg=GOLD, bd=1, relief="groove", font=(FH, 10, "bold"))
        logbox.pack(fill="both", expand=True, pady=(8, 0))
        bar = tk.Frame(logbox, bg=BG)
        bar.pack(fill="x", padx=8, pady=(4, 2))
        tk.Button(bar, text="清空", command=self._hk_log_clear, bg=BTN, fg=FG,
                  font=(FH, 9), padx=8, cursor="hand2").pack(side="left")
        tk.Label(bar, text="(不订阅主日志、不轮询: 只有上面两类事件会让它增长)",
                 bg=BG, fg=DIM, font=(FH, 8)).pack(side="left", padx=6)

        self.hk_log_text = tk.Text(logbox, height=12, wrap="word", bg="#1e1e1e", fg=FG,
                                   font=(FH, 10), state="disabled")
        self.hk_log_text.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self._hk_log("快捷键页就绪: 点上面按钮, 或在游戏页按 Tab 切三连, 都会记在这里。")

    # ---------- Tab: 资源替换 (v4.68★) ----------
    def _build_resource_tab(self):
        """背景/立绘/头像/音效等本地替换规则管理。

        直接调用 ui/resource_tab.py 里的 ResourceTabBuilder, 让 app.py 不被这一大页 UI 撑爆。
        """
        self._resource_builder = RESTAB.ResourceTabBuilder(self)
        self._resource_builder.build(self.tab_resource)

    # ---------- Tab: 换肤 (v4.139★ 动态皮肤 + 即时换肤 并成一页, 里面子页签分开) ----------
    def _build_skin_tab(self):
        """一个顶层 Tab, 里面用子页签分开『动态皮肤』和『即时换肤』两套机制。

        为什么要合并: 配一套动态皮要在这两处来回切(先探出这套有哪些文件, 再决定走注入
        还是整套映射), 拆成两个顶层 Tab 只是多点一次;
        为什么子页签不并成一大页: 两套机制不是一回事(见下面两个方法的说明), 混着配
        最常见的下场就是"以为换了动态皮, 实际只换了一张贴图";
        两页各自挂一份滚动容器 —— 动态皮肤那页原来没挂, 内容比窗口高就被截掉
        (用户原话"现在的动态皮肤都看不全")。
        """
        sub = ttk.Notebook(self.tab_skin)
        sub.pack(fill="both", expand=True)
        self._skin_sub = sub

        f_dyn = tk.Frame(sub, bg=BG)
        f_inj = tk.Frame(sub, bg=BG)
        sub.add(f_dyn, text=" 动态皮肤 ")
        sub.add(f_inj, text=" 即时换肤 ")

        self._build_dynamic_skin_tab(f_dyn)
        self._build_inject_tab(f_inj)

        # 子页签之间切换时也要刷新: 规则可能在『资源替换』页被改过
        def _on_sub_change(_e=None):
            try:
                _t = str(sub.tab(sub.select(), "text") or "").strip()
            except Exception:
                return
            if _t == "即时换肤":
                _ib = getattr(self, "_inject_builder", None)
                if _ib is not None:
                    try:
                        _ib.refresh()
                    except Exception as e:
                        self.log("[UI] 即时换肤页刷新失败: %s" % e)

        sub.bind("<<NotebookTabChanged>>", _on_sub_change)

    def _goto_skin_sub(self, text):
        """跳到『换肤』页并选中指定的子页签(给别的页的跳转按钮用)。

        返回 True/False —— 调用方拿不到子页时不至于抛异常。
        """
        try:
            self.notebook.select(self.tab_skin)
            for i in self._skin_sub.tabs():
                if str(self._skin_sub.tab(i, "text") or "").strip() == text:
                    self._skin_sub.select(i)
                    return True
        except Exception:
            return False
        return False

    # ---------- Tab: 即时换肤 (v4.87★) ----------
    def _build_inject_tab(self, parent=None):
        """资源替换的第二套机制: 即时注入(换纹理) + 点击播语音。

        与『资源替换』页分工: 那页管所有规则(默认走 URL 替换), 这页只管"走注入"的
          那部分 —— 总开关、按类别选模式、每条的语音地址。
        """
        from ui import inject_tab as INJTAB
        self._inject_builder = INJTAB.InjectTabBuilder(self)
        self._inject_builder.build(parent if parent is not None else self.tab_skin)

    # ---------- Tab: 动态皮肤 (v4.111★) ----------
    def _build_dynamic_skin_tab(self, parent=None):
        """动态皮(skel+atlas+png)整套替换 —— 与『资源替换』页分开, 两套机制不混着配。

        这页三件事: ① 挑一套动态皮并探出真实文件; ② 拿它的贴图换自走棋背景/主公棋子;
        ③ 整套映射(编号→编号)才是真换动画。UI 在 ui/dynamic_skin_tab.py。
        """
        from ui import dynamic_skin_tab as DSTAB
        self._dynskin_builder = DSTAB.DynamicSkinTabBuilder(self)
        self._dynskin_builder.build(parent if parent is not None else self.tab_skin)

    # ---------- Tab: 赛季更新 (v4.76★) ----------
    def _build_season_update_tab(self):
        """换季一键流(检查官方→抓配置→推送现行数据) + 只读体检 + OL 皮肤表更新。

        与『赛季数据』页分工: 那页是换表工作台(一步步来, 有对比向导); 这页是把
        tools/season_check.py / tools/season_config.py / core/dress_table.py 那几条
        换季命令做成按钮 —— 用户原话: "这些新做一个tab页做成功能、可以点击的按钮, 进行更新数据"。
        UI 放在 ui/season_update_tab.py, 道理同资源替换页: app.py 不被这一页撑爆。
        """
        from ui import season_update_tab as SUPTAB
        self._season_upd_builder = SUPTAB.SeasonUpdateTabBuilder(self)
        self._season_upd_builder.build(self.tab_season_upd)

    # ---------- Tab: 统计 (v4.88★) ----------
    def _build_stats_tab(self):
        """一局的对账: 刷新/购买/商店出现/锦囊, 摘要 + 每回合 + 三个排行。

        数据由游戏页桥接脚本的统计钩子采集(默认关, 这页点「开始记录」才挂), 与上游那份
        TavernChessStats 口径一致 —— 存下来的 json 能直接喂给 visualization.py 出图。
        UI 放在 ui/stats_tab.py, 道理同资源替换页: app.py 不被这一页撑爆。
        """
        from ui import stats_tab as STATTAB
        self._stats_builder = STATTAB.StatsTabBuilder(self)
        self._stats_builder.build(self.tab_stats)

    # ---------- Tab: 机制 (v4.88★) ----------
    def _build_mechanism_tab(self):
        """利用游戏机制的常驻玩法, 目前一条: 陆逊小抄。

        循环「用手牌第 N 张锦囊 → 同步手牌 → 刷新手牌区」。实现写在桥接脚本的 SgsLuXun 里
        (不转发上游 AutoUseSpell.js —— 那份的三个启动函数只有调用没有定义)。
        UI 放在 ui/mechanism_tab.py。
        """
        from ui import mechanism_tab as MECTAB
        self._mech_builder = MECTAB.MechanismTabBuilder(self)
        self._mech_builder.build(self.tab_mech)

    # ---------- Tab: 锦囊试验台 (v4.145★) ----------
    def _build_spell_lab_tab(self):
        """在助手上直接发 CReqChessUseSpell —— UI 与说明都在 ui/spell_lab_tab.py。"""
        from ui import spell_lab_tab as SLT
        self._spell_lab_builder = SLT.SpellLabTabBuilder(self)
        self._spell_lab_builder.build(self.tab_spell_lab)

    # ---------- Tab: 主公 / 选将 (v4.152★) ----------
    def _build_general_tab(self):
        """列 40 个可选主公, 点哪个发 ReqChessChooseGeneral(ID) —— UI 与说明都在 ui/general_tab.py。"""
        from ui import general_tab as GT
        self._general_builder = GT.GeneralTabBuilder(self)
        self._general_builder.build(self.tab_general)

    # ---------- Tab: 对局复盘 (v4.145★) ----------
    def _build_review_tab(self):
        """看 data\\matchlogs 里的协议流水并导出 —— UI 与说明都在 ui/match_review_tab.py。"""
        from ui import match_review_tab as MRT
        self._review_builder = MRT.MatchReviewTabBuilder(self)
        self._review_builder.build(self.tab_review)

    # ---------- Tab: 阵容 (v4.105★) ----------
    def _build_lineup_tab(self):
        """后期转阵思路 + 找卡成本, 数据来自上游那两张表(已转成 data/*.json)。

        与『赛季数据』页分工: 那页管【卡池/配置表】的换表, 这页管【打法】——
        35 组四势力组合各自该转什么、以及"这张卡在几本刷最省、要准备多少钱"。
        UI 放在 ui/lineup_tab.py, 道理同其它页: app.py 不被这一页撑爆。
        """
        from ui import lineup_tab as LUPTAB
        self._lineup_builder = LUPTAB.LineupTabBuilder(self)
        self._lineup_builder.build(self.tab_lineup)


    def _hk_log(self, text):
        """往『快捷键』页自己的日志追加一行。

        刻意【不】走 self.log(): 主日志会被后端各种推送刷屏, 而用户要的是"只有我点按钮 /
        游戏页按 Tab 时才出现"的那几条 —— 混在一起就找不到了。
        """
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {text}"
        try:
            if not isinstance(getattr(self, "_hk_log_lines", None), list):
                self._hk_log_lines = []
            self._hk_log_lines.append(line)
            if len(self._hk_log_lines) > 500:
                del self._hk_log_lines[:-500]
        except Exception:
            self._hk_log_lines = [line]
        t = getattr(self, "hk_log_text", None)
        if t is None:
            return
        try:
            t.config(state="normal")
            t.insert(tk.END, line + "\n")
            # 行数超上限就整体重绘一次 —— 事件本来就低频(点按钮/按 Tab), 这点代价可忽略,
            #   换来的是 Text 控件不会无限膨胀。
            try:
                if int(t.index("end-1c").split(".")[0]) > 600:
                    t.delete("1.0", tk.END)
                    t.insert(tk.END, "\n".join(self._hk_log_lines) + "\n")
            except Exception:
                _SIL.note("ui/app.py:5649", "if int(t.index('end-1c').split('.')[0]) > 600:")
            t.see(tk.END)
            t.config(state="disabled")
        except Exception:
            _SIL.note("ui/app.py:5653", "t.config(state='normal')")

    def _hk_log_clear(self):
        self._hk_log_lines = []
        t = getattr(self, "hk_log_text", None)
        if t is None:
            return
        try:
            t.config(state="normal")
            t.delete("1.0", tk.END)
            t.config(state="disabled")
        except Exception:
            _SIL.note("ui/app.py:5665", "t.config(state='normal')")

    # ---------- v4.83★ 优先名单: 界面 ⇄ config.json ⇄ 游戏页 ----------
    def _hk_names_arg(self, s):
        """「瞒天过海·六,联军盛宴·五」→ ["瞒天过海·六","联军盛宴·五"](中文逗号也算)"""
        return [x.strip() for x in str(s or "").replace("，", ",").split(",") if x.strip()]

    def _hk_priority_cfg(self):
        """读界面上那几项优先名单配置(保存 / 推送 / 执行三处共用同一份口径)"""
        def _v(attr):
            v = getattr(self, attr, None)
            try:
                return (v.get() or "").strip() if v is not None else ""
            except Exception:
                return ""

        def _bv(attr):
            v = getattr(self, attr, None)
            try:
                return bool(v.get()) if v is not None else False
            except Exception:
                return False

        return {
            "spell": _v("hk_spell_prio_var"),
            "followup": _v("hk_follow_prio_var"),
            "followup_target": _v("hk_follow_target_var"),
            "spell_blacklist": _v("hk_spell_block_var"),
            "spell_use_all": _bv("hk_spell_all_var"),
            "followup_use_all": _bv("hk_follow_all_var"),
            "spell_need_pick": _v("hk_spell_need_pick_var"),
        }

    def _push_priority_lists(self, p=None, log=True):
        """把优先名单下发到游戏页。脚本 v4.82+ 才有 updateSpellPriority / updateFollowupPriority,
        老脚本上这两个调用会静默失败(日志里能看到), 不影响其它功能。"""
        if not self.ops or not self.ops.connected():
            return 0
        try:
            p = p or (self.cfg.get("priority_lists") or {})
            sp = self._hk_names_arg(p.get("spell"))
            fu = self._hk_names_arg(p.get("followup"))
            tgt = str(p.get("followup_target") or "").strip()
            n = 0
            if sp:
                self.ops.update_spell_priority(sp)
                n += 1
            if fu:
                self.ops.update_followup_priority(fu)
                n += 1
            if tgt:
                self.ops.update_followup_target(tgt)
                n += 1
            # v4.105★ 黑名单: 键存在就推(留空也是一种明确意图 = 全部解禁), 没这个键 = 老配置, 不动
            if "spell_blacklist" in p:
                self.ops.update_spell_blacklist(self._hk_names_arg(p.get("spell_blacklist")))
                n += 1
            # v4.176★ 用锦囊后需补挑的锦囊名单: 连接时也会走到这里(下面 p 缺这个键时退回 cfg 默认)
            _np = p.get("spell_need_pick")
            if _np is None:
                _np = self.cfg.get("spell_need_pick", "")
            if _np is not None:
                self.ops.set_spell_need_pick(str(_np))
                n += 1
            if log and n:
                self.log(f"[优先名单] 已推送到游戏页: 锦囊 {len(sp)} 个 / 随征 {len(fu)} 个"
                         + (f" / 随征给「{tgt}」" if tgt else "")
                         + f" / 黑名单 {len(self._hk_names_arg(p.get('spell_blacklist')))} 个")
            return n
        except Exception as e:
            if log:
                self.log(f"[优先名单] 推送失败: {e}", level="WARN")
            return 0

    def _hk_save_priority(self):
        """保存 + 立刻推送(不等重启、也不等断连重连 —— 那样最容易让人以为没生效)"""
        p = self._hk_priority_cfg()
        self.cfg["priority_lists"] = p
        self.cfg["spell_need_pick"] = p.get("spell_need_pick", "")
        try:
            save_config(self.cfg)
        except Exception as e:
            self.log(f"[优先名单] 保存 config.json 失败: {e}", level="WARN")
        # v4.101★ 一次点击要发【三条】请求(锦囊/随征/随征目标, 各 2 秒超时), 以前在
        #   主线程一条接一条: 最坏 6 秒界面不动。挪后台, 结果回来再回一句。
        def _prio_done(r, err):
            if err:
                self._hk_log(f"优先名单推送失败: {err}")
            else:
                self._hk_log("优先名单已保存并推送: 锦囊[%s] 随征[%s] 给[%s] 用后补挑[%s]"
                             % (p["spell"] or "(留空=认出来的锦囊, 从最右开始)",
                                p["followup"] or "(留空=认『随征卡牌ID』那份表)",
                                p["followup_target"] or "(留空=位置0)",
                                p.get("spell_need_pick") or "(默认草船,三顾)"))

        self._run_bg("priority_push", (lambda: self._push_priority_lists(p, log=True)),
                     _prio_done, wait="已保存, 正在推送优先名单…")
        # 连没连上在【主线程】当场判(不需要等回包), 没连上就明说一声
        if not (self.ops and self.ops.connected()):
            messagebox.showinfo("优先名单",
                                "已保存到 config.json。\n\n游戏页还没连上, 等连上后会自动再推一次;"
                                "也可以现在连上助手再点一次【保存并推送】。")

    def _hk_btn_args(self, name, label):
        """带参数点快捷键: 参数就取自本页那几项(不用每次去控制台敲 window.__sgsHotkey…)"""
        p = self._hk_priority_cfg()
        if name == "useRightmostSpell":
            arg = {"names": self._hk_names_arg(p["spell"]),
                   "useAll": bool(p["spell_use_all"]), "right": True}
        else:
            arg = {"names": self._hk_names_arg(p["followup"]),
                   "useAll": bool(p["followup_use_all"]), "right": True}
            if p["followup_target"]:
                arg["target"] = p["followup_target"]
        self._hk_btn(name, label, arg)

    def _hk_btn(self, name, label, arg=None):
        """快捷键按钮: 发指令到游戏页 → 结果写进本页日志(后台线程等回包, 不卡界面)。"""
        self._hk_log(f"▶ {label}  →  __sgsHotkey.{name}()")
        def _task():
            try:
                res = self.ops.hotkey(name, arg)
            except Exception as e:
                res = f"ERR:{e}"
            txt = "(无回包: 游戏页未连接/超时)" if res is None else str(res)
            try:
                self.root.after(0, lambda: self._hk_log(f"   ← {label}: {txt}"))
            except Exception:
                _SIL.note("ui/app.py:5783", "self.root.after(0, lambda: self._hk_log(f' ← {")
        threading.Thread(target=_task, daemon=True).start()

    def _hk_run_console(self, event=None):
        """把输入框里的 JS 原样发到游戏页执行(等价在 F12 控制台敲一行), 返回值写进本页日志。"""
        try:
            code = (self.hk_cmd_var.get() or "").strip()
        except Exception:
            code = ""
        if not code:
            return
        self._hk_log(f"❯ {code}")
        try:
            self.hk_cmd_var.set("")
        except Exception:
            _SIL.note("ui/app.py:5798", "self.hk_cmd_var.set('')")
        def _task():
            try:
                res = self.ops.run_console_js(code)
            except Exception as e:
                res = f"ERR:{e}"
            txt = "(无回包: 游戏页未连接/超时)" if res is None else str(res)
            try:
                self.root.after(0, lambda: self._hk_log("   ← " + txt))
            except Exception:
                _SIL.note("ui/app.py:5808", "self.root.after(0, lambda: self._hk_log(' ← ' ")
        threading.Thread(target=_task, daemon=True).start()

    def _hk_probe_pool(self):
        """探测游戏页里到底有没有『公共卡池剩余张数』—— 只读, 不动游戏任何状态。

        为什么要探: 助手端算概率用的 pool_taken 只统计【自己买走的】
        (core/protocol.py 只在 CRespShopBuyChess / CRespShopRecycleChess 这两个【响应包】上
        写入, 而响应包只回给本人), 可卡池是公共的 —— 别人买走、你遣散回池, 都会改变池内数量。
        于是"别刷了, 概率太低"这类结论其实建立在残缺数据上, 它只反映【你自己买了多少】。

        要修这个偏差, 前提是先确认客户端有没有这份数据:
          有 → 直接读, 问题解决; 没有 → 只能给下界, 而且措辞上必须说清"这是下界"。

        取证结论写在 core/pool_probe.py 顶部(反编译源码 1.4MB + 赛季配置 + 抓包结构, 逐条可复核);
        这个按钮是在【真机上再确认一遍】—— 理由很实在: 反编译版本未必等于线上版本。

        为什么走 eval 通道而不是加进油猴脚本: 探测本身是实验, 要反复改; 而每改一次脚本,
        用户就得重新注入 + 刷新游戏页。走通道的话改一次跑一次, 迭代成本几乎为零。
        """
        self._hk_log("▶ 探测公共卡池: 只读扫描 manager 属性 / 商店发牌 / 配置表 / 协议样本")
        # v4.67★ 这里刻意用 getattr 而不是 try/except: pass —— 项目的静态自检
        #   (dev/_verify_static.py) 对"静默 except: pass"是【只许减不许增】的预算制,
        #   而这一页的标签一定已由 _build_hotkey_tab 建好, 缺了就是"页都没建", 不是要吞掉的异常。
        _sv = getattr(self, "hk_pool_var", None)
        if _sv is not None:
            _sv.set("探测中…(最多 20 秒)")

        def _task():
            raw = None
            try:
                raw = self.ops.run_console_js(POOLPROBE.PROBE_JS, timeout=20.0)
            except Exception as e:
                raw = f"ERR:{e}"
            rep = POOLPROBE.parse(raw)
            lines = POOLPROBE.format_report(rep)
            brief = POOLPROBE.summarize(rep)
            self._hk_pool_report = rep     # 留在内存里, 方便后续复制粘贴给开发者

            def _paint():
                for ln in lines:
                    self._hk_log(("   " + ln) if ln else "")
                v2 = getattr(self, "hk_pool_var", None)
                if v2 is not None:
                    v2.set(brief)

            try:
                self.root.after(0, _paint)
            except Exception as e:
                # 极端情况(探测期间窗口被关掉)。不写成 pass: 静默吞掉的表现是"点了没反应",
                # 而这种"看起来像功能没做"的故障最难排查 —— 宁可多一行日志。
                self._hk_log(f"   (界面更新失败: {e})")

        threading.Thread(target=_task, daemon=True).start()

    # ---------- Tab: 日志 ----------
    def _build_log_tab(self):
        log_frame = tk.Frame(self.tab_log, bg=BG)
        log_frame.pack(fill="both", expand=True, padx=5, pady=5)

        # ---- 工具条 ----
        bar = tk.Frame(log_frame, bg=BG)
        bar.pack(fill="x", pady=(0, 4))
        tk.Button(bar, text="分析挑牌诊断", command=self._pick_diag_analyze, bg=BTN, fg=FG,
                  font=(FH, 9), padx=8, cursor="hand2").pack(side="left", padx=(0, 6))
        tk.Label(bar, text="(读 data/log.txt 及轮转文件, 判决『挑选候选该从哪个字段读』; "
                           "v4.49 起成功挑选也记来源, 打一局再点)", bg=BG, fg=DIM,
                 font=(FH, 8)).pack(side="left")

        self.log_text = tk.Text(log_frame, wrap="word", bg="#1e1e1e", fg=FG, font=(FH, 10), state="disabled")
        self.log_text.pack(fill="both", expand=True)

        # 初始加载日志
        self._update_log_display()

    def _pick_diag_analyze(self):
        """把日志里的挑牌证据翻成结论(v4.49★) —— 『钉死候选源』那件事的收口。

        为什么要有个按钮、而且放在日志页: 这个结论不是给用户看的"战绩数字", 而是
        『脚本该改哪一行』的判据, 它读的就是这一页正在展示的那份日志 —— 放一起最不容易错位。
        以前要人肉翻日志找"哪条来源供了候选", 现在一次点击出结论。
        """
        try:
            from core import pick_diag as PD
            res, txt = PD.report()
        except Exception as e:
            self.log("[挑牌诊断] 分析失败: %s: %s" % (type(e).__name__, e), level="ERROR")
            return
        det = [txt]
        if res["by_src"]:
            det.append("")
            det.append("各来源命中次数:")
            for k, v in sorted(res["by_src"].items(), key=lambda kv: (-kv[1], kv[0])):
                det.append("  %-46s x%d" % (k, v))
        if res["srcs"]:
            det.append("")
            det.append("最近 8 条证据:")
            for it in res["srcs"][-8:]:
                det.append("  " + it["raw"][:150])
        if res["diags"]:
            det.append("")
            det.append("最后一个诊断块(前 20 行):")
            for ln in res["diags"][-1]["text"].splitlines()[:20]:
                det.append("  " + ln[:150])
        self.log("[挑牌诊断] 详细证据:\n" + "\n".join(det))
        try:
            messagebox.showinfo("挑牌诊断判决", txt + "\n\n(详细证据已写进本页日志, 可直接复制)")
        except Exception as e:
            self.log("[挑牌诊断] 弹窗失败(结论已写进日志): %s" % e, level="WARN")

    # ---------- OCR / 扫描 ----------
    def ocr_scan(self):
        self._rec_ui("ocr_scan")
        self._last_fb = "F5: OCR扫描中..."
        self._push_hud_bar()
        self._stop_ocr_scan = False
        def _scan_loop():
            try:
                while not self._stop_ocr_scan:
                    # v4.17★ OcrOps.scan() 返回 {"shop":[...], "bench":[...]} (新增方法, 之前不存在)
                    res = self.ocr_scanner.scan()
                    shop_n = len(res.get("shop", [])) if isinstance(res, dict) else 0
                    bench_n = len(res.get("bench", [])) if isinstance(res, dict) else 0
                    self.root.after(0, lambda s=shop_n, b=bench_n: self.log(f"[OCR] 扫描完成: 商店{s}项 / 备战区{b}项"))
                    time.sleep(1) # 每秒扫描一次
            except Exception as e:
                self.root.after(0, lambda e=e: self.log(f"[OCR] 扫描错误: {e}", level="ERROR"))
            finally:
                self.root.after(0, lambda: self.log("[OCR] 扫描停止."))
                self.root.after(0, lambda: setattr(self, '_last_fb', "F5: OCR扫描停止"))
                self.root.after(0, self._push_hud_bar)

        threading.Thread(target=_scan_loop, daemon=True).start()

    def ocr_stop(self):
        self._rec_ui("ocr_stop")
        self._stop_ocr_scan = True
        self.log("[OCR] 请求停止扫描。")

    # ---------- 加速设置 ----------
    def _flags_apply(self):
        # 从 UI 配置中读取加速Flag并推送到脚本
        flags_to_push = {}
        # v4.54★ 按要求: 『阻止三连合成』不再由助手推送(UI 上那一项也已注释掉) ——
        #   原来每次推 flags 都会顺手把 blockTripleCombine 写回默认值, 等于把用户在游戏里
        #   按 Tab 切出来的状态又冲回去。现在它只由游戏页 Tab 键 / 『快捷键』页按钮控制。
        #   要恢复: 把 _build_flags_ui 里 flags 字典那一行放开, 并放开下面这几行。
        # triple_combine_state = True # Default value
        for key, var in getattr(self, "flags_vars", {}).items():
            # if key == "blockTripleCombine":
            #     triple_combine_state = var.get()
            # else:
            flags_to_push[key] = var.get()
        if flags_to_push:
            STATE.send_cmd("flags_set", flags=flags_to_push)
            self.log(f"[加速Flag] 推送脚本: {flags_to_push}")

        # # Explicitly call set_triple_combine for the new flag
        # if hasattr(self.ops, 'set_triple_combine'):
        #     self.ops.set_triple_combine(triple_combine_state)

    # ---------- 帧率设置 (新版由 BridgeOps.set_game_speed 处理) ----------
    def _fps_apply(self):
        """应用帧率设置 → 推 fps_set 指令给油猴脚本.
        v3.43★双保险: 立即发1次 + 3秒后再发1次 (Laya.stage重建/战斗结束切回招募, frameRate/timer.scale会被重置回默认).
        v3.98★同步 auto_skip 状态到脚本 localStorage."""
        mode = self.fps_mode.get()
        fps = int(self.fps_val.get()) if mode == "custom" else 0
        speed = float(self.fps_speed.get()) if mode == "custom" else 1.0
        # v1.0.0 ★ 新增：通过 BridgeOps 调用 Tampermonkey 的 setGameSpeed 函数
        # v4.101★ set_game_speed 超时 3 秒 —— 以前在【起线程之前】于主线程直连, 点「应用」
        #   先僵 3 秒才开始后面的事。挪进后台。
        self._run_bg("fps_speed", (lambda: self.ops.set_game_speed(speed)),
                     self._bg_log_done("帧率", "游戏速度已下发"), wait="已发送: 游戏速度")
        # v3.98★ 提取自动跳过状态
        auto_skip = bool(self.auto_skip_var.get())

        # 顺带推加速Flag(保证Flag/FPS/时间加速始终同步到脚本, 启动后不漏)
        # v4.44★ 这里以前是静默 pass: 推送失败时"加速Flag"不会生效, 而用户只能看到
        #   "改了没反应", 查不到任何原因。留一条日志(棘轮: except: pass 只减不增)。
        try:
            self._flags_apply()
        except Exception as _e:
            self.log(f"[帧率] 加速Flag 推送失败(脚本侧可能不同步): {_e}", level="WARN")
        def _apply_once(retry=False):
            cid = STATE.send_cmd("fps_set", mode=mode, fps=fps, speed=speed, auto_skip=auto_skip) # 传递 auto_skip
            tag = " (重试)" if retry else ""
            self.log(f"[帧率] 应用模式={mode} fps={fps if mode=='custom' else '-'} 时间x{speed}{tag}")
            # 异步等结果
            end = time.time() + 4.0
            last = None
            while time.time() < end:
                r = STATE.take_result(cid)
                if r is not None:
                    if r.get("ok"):
                        self.log("[帧率] 应用成功: 脚本已回传")
                        return True
                    else:
                        self.log(f"[帧率] 应用失败: {r.get('msg','脚本未回传(需油猴v3.43+且游戏页面打开)')}", level="ERROR")
                        return False
                time.sleep(0.05)
            self.log("[帧率] 应用失败: 脚本未回传(需油猴v3.43+且游戏页面打开)", level="ERROR")
            return False

        # v4.24★ 修 UI 卡死: 原来 `_apply_once()` 直接被主线程调用, 而它内部"等结果"最长阻塞 4 秒
        #   (while + time.sleep(0.05)), 且 3 秒后的 root.after 回调也在主线程跑同一段
        #   → 点一次"应用"界面要僵 ~8 秒(窗口拖不动、像卡死)。注释写着"异步等结果"其实完全没异步。
        #   现在整段丢到后台线程; 日志仍走 self.log(队列 + root.after, 见 log() 的实现)。
        def _worker():
            _apply_once()
            # 3秒后再次应用 (防止Laya.stage重建/战斗结束切回招募, frameRate/timer.scale被重置)
            time.sleep(3.0)
            _apply_once(retry=True)

        threading.Thread(target=_worker, daemon=True).start()


    def _build_flags_ui(self, parent):
        flags_frm = tk.LabelFrame(parent, text="加速配置", bg=BG, fg=GOLD, bd=1, relief="solid", font=(FH, 9, "bold"), padx=4, pady=1)
        flags_frm.pack(fill="x", pady=5)

        self.flags_vars = {} # Store BooleanVar for each flag
        
        # 定义所有加速 Flag
        flags = {
            "tweenScale": "动画加速 (tweenScale)",
            "recruitFastTween": "招募动画加速 (recruitFastTween)",
            "batchProtocol": "批量协议 (batchProtocol)",
            "auto_skip": "自动跳过战斗 (auto_skip)", # v3.98★ 新增
            # v4.54★ 按要求【注释掉】"阻止三连合成"这个复选框:
            #   ① 界面上不再显示它;
            #   ② 也不再由助手推送(见 _flags_apply 里被注释掉的那几行) —— 否则每次点其它
            #      flag 都会把 blockTripleCombine 重置回默认值, 把用户在游戏里按 Tab 切的状态冲掉;
            #   ③ 现在三连抑制完全由游戏页 Tab 键(或『快捷键』页那个按钮)控制。
            # "blockTripleCombine": "阻止三连合成" # 新增
        }

        for key, desc in flags.items():
            var = tk.BooleanVar(value=self.cfg.get("flags", {}).get(key, True)) # Default True
            chk = tk.Checkbutton(flags_frm, text=desc, variable=var,
                                 bg=BG, fg=FG, selectcolor="#33333f", activebackground=BG,
                                 font=(FH, 9), bd=0, relief="flat",
                                 command=self._flags_on_change)
            chk.pack(side="left", padx=5, pady=2)
            self.flags_vars[key] = var
        
        # 自动跳过战斗 独立处理, 因为它也影响帧率的auto_skip参数
        if "auto_skip" in self.flags_vars:
            self.auto_skip_var = self.flags_vars["auto_skip"] # 引用同一个变量

        self._load_flags_config()

    def _flags_on_change(self):
        flags_cfg = {}
        for key, var in self.flags_vars.items():
            flags_cfg[key] = var.get()
        self.cfg["flags"] = flags_cfg
        save_config(self.cfg)
        self.log(f"[加速Flag] 配置已保存: {flags_cfg}")
        self._flags_apply() # 立即应用到脚本

    def _load_flags_config(self):
        flags_cfg = self.cfg.get("flags", {})
        for key, var in self.flags_vars.items():
            if key in flags_cfg:
                var.set(flags_cfg[key])

    # ---------- FPS 设置 (旧版, 新版统一由 _fps_apply 处理) ----------
    def _build_fps_ui(self, parent):
        fps_frm = tk.LabelFrame(parent, text="帧率/时间加速", bg=BG, fg=GOLD, bd=1, relief="solid", font=(FH, 9, "bold"), padx=4, pady=1)
        fps_frm.pack(fill="x", pady=5)

        tk.Label(fps_frm, text="模式:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left", padx=5)
        self.fps_mode = tk.StringVar(value="default") # default / custom
        tk.Radiobutton(fps_frm, text="默认", variable=self.fps_mode, value="default",
                       bg=BG, fg=FG, selectcolor="#33333f", activebackground=BG,
                       font=(FH, 9), bd=0, relief="flat", command=self._fps_mode_on_change).pack(side="left", padx=2)
        tk.Radiobutton(fps_frm, text="自定义", variable=self.fps_mode, value="custom",
                       bg=BG, fg=FG, selectcolor="#33333f", activebackground=BG,
                       font=(FH, 9), bd=0, relief="flat", command=self._fps_mode_on_change).pack(side="left", padx=2)

        tk.Label(fps_frm, text="FPS:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left", padx=10)
        self.fps_val = tk.StringVar(value="30") # 10, 20, 30, 60
        ttk.OptionMenu(fps_frm, self.fps_val, "30", "10", "20", "30", "60").pack(side="left", padx=2)

        tk.Label(fps_frm, text="时间x:", bg=BG, fg=FG, font=(FH, 9)).pack(side="left", padx=10)
        self.fps_speed = tk.StringVar(value="1.0") # 1.0, 2.0, 3.0, 5.0
        ttk.OptionMenu(fps_frm, self.fps_speed, "1.0", "1.0", "2.0", "3.0", "5.0").pack(side="left", padx=2)

        tk.Button(fps_frm, text="应用", command=self._fps_apply,
                  bg=BTN, fg=BLUE, relief="flat", bd=0,
                  font=(FH, 9, "bold"), padx=8, cursor="hand2").pack(side="left", padx=10)
        
        self._load_fps_config()
        self._fps_mode_on_change() # Initial state update

    def _fps_mode_on_change(self, *args):
        is_custom = (self.fps_mode.get() == "custom")
        # 禁用/启用 FPS 和时间加速的下拉框
        for child in self.winfo_children(): # 获取所有子控件
            if isinstance(child, (tk.Label, ttk.OptionMenu)) and child.cget("text") in ("FPS:", "时间x:"):
                for grand_child in child.winfo_children():
                    if isinstance(grand_child, ttk.OptionMenu):
                        grand_child.config(state="normal" if is_custom else "disabled")
        # 直接控制 OptionMenu 的 state
        # 这种方式可能需要更精细地定位 OptionMenu 实例
        # 暂时先用上面的方法,或者在 build_fps_ui 时把 OptionMenu 存起来
        pass # UI state update handled by _fps_apply indirectly

    def _load_fps_config(self):
        fps_cfg = self.cfg.get("fps", {})
        if fps_cfg:
            self.fps_mode.set(fps_cfg.get("mode", "default"))
            self.fps_val.set(str(fps_cfg.get("fps", "30")))
            self.fps_speed.set(str(fps_cfg.get("speed", "1.0")))

    # ---------- 动作 (顶栏按钮, 小抄按钮, 热键) ----------
    def a_logic_scan(self):
        self._rec_ui("logic_scan")
        # v3.50★ 探测前检查连接，如果未连接则尝试强制同步
        def _t():
            # v4.101★ 这两行以前在【起线程之前】: STATE.force_reset() 后主线程硬睡 0.5 秒,
            #   点一下「逻辑探测」界面先僵半秒。挪进线程里, 主线程一点不卡。
            if not STATE.get_state():
                self.log("[逻辑探测] 正在尝试激活脚本连接...")
                STATE.force_reset()
                time.sleep(0.5)   # 等连接建立(在线程里睡, 不影响界面)
            self.log("[逻辑探测] 启动增强扫描...")
            try:
                # v4.102★ 扫描前先把活体手牌同步进 _board: 否则逻辑探测拿到的
                #   备战区地址指向空数组, DOM 扫描恒报"备战区0"。
                try:
                    self.ops.sync_board_from_hand(force=True)
                except Exception:
                    _SIL.note("ui/app.py:6142", "self.ops.sync_board_from_hand(force=True)")
                res = self.ops.discover_logical_paths()
                if res:
                    self.log(f"[逻辑探测] 成功! 发现地址: {res}")
                    self.ops.rescan_areas() # 顺便扫一次数据
                else:
                    self.log("[逻辑探测] 失败: 未找到有效地址，请检查游戏是否已进入对局。")
            except Exception as e:
                self.log(f"[逻辑探测] 异常: {e}")
        threading.Thread(target=_t, daemon=True).start()

    def a_skip(self):
        self._rec_ui("skip")
        if self.end_recruit_var.get():
            self._bg(self.ops.skip_battle_full, "跳过战斗+结束招募")
        else:
            self._bg(self.ops.skip_battle, "跳过战斗")

    def a_refresh(self):
        self._rec_ui("refresh")
        self._bg(self.ops.refresh_shop, "刷新商店")

    def a_hand_sentinel(self):
        """手牌哨兵【点一下刷一次】(v4.143★)。

        用户原话: "把手牌哨兵加一个开关？一直关着；然后点一下触发一次？"
        所以哨兵的常驻轮询默认关掉(脚本侧 installHandSentinel 的 enabled:false),
        这里就是那个"点一下" —— 手动跑一次并把手牌区 UI 刷出来, 同时把手牌张数
        记进哨兵状态, 下次能看出"变没变"。
        游戏里也能直接按 Alt+H(不必切回助手窗口)。
        """
        self._bg(self.ops.refresh_hand_once, "手牌哨兵")

    def a_page_errors(self):
        """【页面错误】查看窗(v4.144★)。

        用户原话: "那个一直加的错误看不到, 一会一直加"。
        脚本端从 document-start 就在 window.__sgsTopErrs 里收集所有同步 throw 和未处理的
        Promise(环形缓冲 160 条, 见 00-bridge.js:2693-2716), 但【没有查看入口】; F12 控制台
        又常被网络请求日志刷屏 → 真错误一闪而过。这里直接从页面把数组读回来, 并按
        "同一条错误出现过几次"聚合 —— 反复炸的那一句排最上面(那就是"一直在加"的那个)。
        """
        def _done(arr):
            win = tk.Toplevel(self.root)
            win.title("页面错误 —— 来自 window.__sgsTopErrs")
            win.geometry("900x520")
            win.configure(bg="#1e1e1e")
            txt = tk.Text(win, wrap=tk.NONE, bg="#1e1e1e", fg="#d4d4d4",
                          font=("Consolas", 10))
            sb = tk.Scrollbar(win, command=txt.yview)
            txt.configure(yscrollcommand=sb.set)
            sb.pack(side=tk.RIGHT, fill=tk.Y)
            txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            # ★ 聚合与显示统一走 core/page_errs.py —— 和命令行 dev/_page_errors_once.py
            #   共用一份。以前两处各写一遍, 收集端加了 tgt 字段后只改一边就会漏
            #   (见 core/page_errs.py 顶部说明)。
            txt.insert(tk.END, _fmt_page_errs(arr))
            txt.configure(state=tk.DISABLED)

        def _work():
            try:
                arr = self.ops.page_errors()
            except Exception as e:
                self.log(f"[页面错误] {e}")
                arr = None
            # tk 只能主线程碰 —— 后台跑完塞回主线程再建窗口
            self.root.after(0, lambda: _done(arr))

        self._bg(_work, "页面错误")

    @staticmethod
    def _live_buy_price(g=None, me=None):
        """一张卡的【实价】(虎符)。用户原话: "正常情况下买武将要花费3虎符啊; 拿了丈八蛇矛
        会变成买武将2; 刘焉/主公技能还会再减1" —— 所以这数必须按活体取, 不能写死。

        取值优先级: 商品自带 price(服务器算好的) > 活体 me.shopBuyCost > 配置表基准(ChessPrice=3) > 3。
        为什么要有这个函数: a_buyall 原来写 `coin -= 1`(注释还写着"假设每张1金币"),
        买3金的卡时金币估算是错的 → "买全"会把钱花过保留线, 而且日志看不出。
        """
        try:
            if isinstance(g, dict):
                for k in ("price", "Price", "buyCost", "BuyCost", "ShopBuyCost"):
                    v = g.get(k)
                    try:
                        v = int(v)
                    except (TypeError, ValueError):
                        continue      # 这个键不是数字(或没有这个键) → 看下一个, 不是"吞异常"
                    if v > 0:
                        return v
        except (TypeError, AttributeError) as e:
            sys.stderr.write("[买全] 商品价读不到(按下一级兜底): %s: %s\n" % (type(e).__name__, e))
        try:
            v = int((me or {}).get("shopBuyCost") or 0)
        except (TypeError, ValueError):
            v = 0
        if v > 0:
            return v
        try:
            from core import csv_loader
            v = int((csv_loader.shop_costs() or {}).get("ShopBuyCost") or 0)
        except Exception as e:
            v = 0
            sys.stderr.write("[买全] 配置表买卡价读不到(按 3 金算): %s: %s\n"
                             % (type(e).__name__, e))
        return v or 3

    def a_buyall(self):
        self._rec_ui("buyall")

        def run():
            from core.recorder import MACRO
            shop = LIVE.shop_snapshot()
            # v4.13★ 如果 LIVE.shop 为空, 尝试从油猟端 getShopGoods() 拉取
            if not shop and self.ops and self.ops.connected():
                try:
                    self.log("[买全] LIVE.shop为空, 尝试从油猟端拉取商店...")
                    js_code = "(function(){try{var s=window.AutoBuyAndSell.getShopGoods();return JSON.stringify(s);}catch(e){return '[]';}})()"
                    res = self.ops.send_recv("eval", js=js_code, timeout=3.0)
                    if res and res.strip() not in ("", "[]"):
                        import json as _json
                        shop = _json.loads(res)
                        self.log(f"[买全] 油猟端返回 {len(shop)} 个商品")
                except Exception as e:
                    self.log(f"[买全] 油猟端拉取失败: {e}")
            if not shop:
                self.log("[买全] 商店为空——先刷新一次商店让协议推数据")
                return
            coin = LIVE.me.get("coin") or 0
            n = 0
            items = []   # 用于批量协议的 [{name,data}, ...]
            # 遍历 snapshot 实际有的槽位(不是固定 1-7), 跳过已sold的
            for g in shop:
                if g.get("_sold"):
                    continue
                gid = g.get("goodsID")
                if not gid:
                    continue
                # v4.68★ 用这一张的【实价】扣(普通武将 3 虎符; 丈八蛇矛→2; 刘焉/主公技能再减)。
                #   原来写死"每张 1 金", 买 3 金的卡时估算偏小 → 会把钱买到保留线以下。
                _price = self._live_buy_price(g, LIVE.me)
                if coin < _price:
                    self.log(f"[买全] 金币{coin}不够买下一张(要{_price}金) —— 停")
                    break
                items.append({"name": "cmsg.CReqShopBuyChess", "data": {"goodsID": int(gid)},
                              "card": g})
                coin -= _price   # 以实价预扣, 最终仍以协议响应为准(下一轮回读 LIVE.me)
            if not items:
                self.log("[买全] 没有可买的卡")
                return
            # v3.43★ 批量协议开关: 调用方传 Flag 或从 __sgsHookFlags 读(默认全开)
            batch_on = True
            try:
                # 通过UI面板勾选 persist 到 self.cfg, 这里读配置
                flags_cfg = (self.cfg.get("flags") or {}) if hasattr(self, "cfg") else {}
                if isinstance(flags_cfg, dict) and flags_cfg.get("batchProtocol") is False:
                    batch_on = False
            except Exception:
                _SIL.note("ui/app.py:6248", "flags_cfg = (self.cfg.get('flags') or {}) if h")
            # 如果 batch 开 → psend_batch 一次打包发完, 再逐条注入手卡
            # 单张 mode 开 → 每条单独psend(带verify + 注入)
            if batch_on:
                payloads = [{"name": it["name"], "data": it["data"]} for it in items]
                r = self.ops.psend_batch(payloads, limit=len(payloads))
                if r and r.get("count"):
                    perItem = r.get("perItem") or []
                    for i, it in enumerate(items):
                        if i < len(perItem) and perItem[i].get("sent") is True:
                            try:
                                self.ops._bench_inject(it["card"])
                            except Exception:
                                _SIL.note("ui/app.py:6261", "self.ops._bench_inject(it['card'])")
                            n += 1
                            self._last_fb = f"买全(批量) {n}/{len(items)}"
            if n == 0 and items:
                # batch不可用或全部失败 → 回退单张协议
                self.log("[买全] 批量未命中, 回退单张协议")
                coin = LIVE.me.get("coin") or 0
                for g in shop:
                    if g.get("_sold"):
                        continue
                    gid = g.get("goodsID")
                    if not gid:
                        continue
                    _price = self._live_buy_price(g, LIVE.me)
                    if coin < _price:
                        self.log(f"[买全] 金币{coin}不够买下一张(要{_price}金) —— 停")
                        break
                    if self.ops.buy_goods(gid, delay=0.25):
                        n += 1
                        coin -= _price
                        self._last_fb = f"买全中 {n}/{len([x for x in shop if not x.get('_sold')])}"
                        continue
            self._last_fb = f"买全完成 买入{n}张(批量模式={batch_on and n>0})"
            self.log(f"[买全] 完成 买入{n}张 批量={'开' if (batch_on and n>0) else '未使用'}")
        self._bg(run, "买全商店")

    def a_deploy(self):
        self._rec_ui("deploy")
        # bench_index=0 / "all" = 把备战区所有未上阵卡挨个上阵(直到战斗区7格满)
        # 单张模式 deploy_bench(1) 太慢, 且用户反馈"上阵当先不触发"=一次1张服务器被动推送也1张1张,
        # 现在全量上阵→每张协议后sleep 0.45s, 让被动【当先】推送齐
        def run():
            self.log("[上阵] 启动全量上阵...")
            try:
                # v4.13★ 先尝试油猟端 directCall 上阵 (更可靠), 失败再回退协议
                if self.ops and self.ops.connected():
                    try:
                        # v4.17★ 修复: 原片段是 "(function(){try{ ... })()" —— 外层 try 没有
                        #   catch/finally, 油猴端 eval 直接抛
                        #   "Missing catch or finally after try"(用户日志里的原始报错)。
                        #   顺带把递归里的 var 改成 let: var 是函数作用域, 递归会互相覆盖 ch/i,
                        #   导致场景遍历错乱甚至死循环。
                        #   eval() 返回最后一个表达式的值, 所以这里必须是 IIFE 表达式(不能写 return 开头)。
                        js_code = ("(function(){"
                                   "try{"
                                   "var m=(function search(o,v){"
                                   "if(!o||v.has(o))return null;v.add(o);"
                                   "if(o.constructor&&o.constructor.name==='TavernChessGameScene')return o;"
                                   "var ch=o._children||o.children||o.childList||[];"
                                   "for(let i=0;i<ch.length;i++){let r=search(ch[i],v);if(r)return r;}"
                                   "return null;})(Laya.stage,new WeakSet());"
                                   "if(!m||!m.manager)return 'ERR:no manager';"
                                   "var mgr=m.manager;"
                                   "if(typeof mgr.ReqChessLineUp!=='function')return 'ERR:no ReqChessLineUp';"
                                   # v4.102 手牌取值与桥接 getHandArray() 统一（原来只读 mgr.HandChess,
                                   #   该字段为空时上阵静默不动; 真正的卡常在 selfInfo.newLyHandChess 上）
                                   "var ben=(function(){var cs=[mgr.HandChess,mgr.handChess,"
                                   "mgr.selfInfo&&mgr.selfInfo.newLyHandChess,"
                                   "mgr.selfInfo&&mgr.selfInfo.NewLyHandChess,"
                                   "mgr.selfInfo&&mgr.selfInfo.HandChess,"
                                   "mgr.selfInfo&&mgr.selfInfo.handChess,mgr.benches];"
                                   "for(let k=0;k<cs.length;k++){if(Array.isArray(cs[k])&&cs[k].length)return cs[k];}"
                                   "return [];})();"
                                   "var n=0;"
                                   "for(let i=0;i<ben.length;i++){"
                                   "var c=ben[i];if(!c)continue;"
                                   "var gid=c.goodsID||c.GoodsID||0;"
                                   "if(gid){try{mgr.ReqChessLineUp(gid,0);n++;}catch(_){}}"
                                   "}"
                                   "return String(n);"
                                   "}catch(e){return 'ERR:'+e.message;}"
                                   "})()")
                        res = self.ops.send_recv("eval", js=js_code, timeout=5.0)
                        self.log(f"[上阵] 油猟端上阵返回: {res}")
                        if res and res.strip().isdigit() and int(res) > 0:
                            self._last_fb = f"上阵: 油猟端 {res}张"
                            self._push_hud_bar()
                            return
                    except Exception as e:
                        self.log(f"[上阵] 油猟端上阵失败, 回退协议: {e}")
                # 回退: Python 协议上阵
                self.ops.rescan_areas() # 确保数据最新
                # v4.108★ 不再写死 0.45: 间隔统一走 remote_ops.DEPLOY_GAP_S(0.10), 一处可调
                summary = self.ops.deploy_all_bench_chess()
                self._last_fb = f"上阵: {summary.get('deployed',0)}张"
                self.log(f"[上阵] 完成: {summary}")
            except Exception as e:
                self.log(f"[上阵] 错误: {e}", level="ERROR")
                self._last_fb = f"上阵失败: {e}"
            self._push_hud_bar()
        self._bg(run, "全量上阵")

    def a_sell_idle(self):
        self._rec_ui("sell_idle")
        # 卖掉所有闲置卡牌
        def run():
            self.log("[卖闲] 启动卖闲置卡牌...")
            try:
                self.ops.rescan_areas() # 确保数据最新
                summary = self.ops.dismiss_idle_bench_chess()
                self._last_fb = f"卖闲: {summary.get('sold',0)}张, 退金{summary.get('gold_refund',0)}"
                self.log(f"[卖闲] 完成: {summary}")
            except Exception as e:
                self.log(f"[卖闲] 错误: {e}", level="ERROR")
                self._last_fb = f"卖闲失败: {e}"
            self._push_hud_bar()
        self._bg(run, "卖闲置卡牌")


    # ---------- 后台任务包装 ----------
    def _bg(self, task_func, description="任务"):
        def wrapper():
            self.log(f"[后台] {description} 启动...")
            try:
                task_func()
            except Exception as e:
                self.log(f"[后台] {description} 异常: {e}", level="ERROR")
                _log_err()
            finally:
                self.log(f"[后台] {description} 完成.")
        threading.Thread(target=wrapper, daemon=True).start()

    # ---------- 游戏内小抄按钮事件 ----------
    def _on_hud_btn(self, data):
        btn_id = data.get("id")
        self.log(f"[小抄按钮] 点击: {btn_id}")
        # 根据按钮ID分发事件
        if btn_id == "ocr_scan":
            self.ocr_scan()
        # v4.23★ 清理: 原有 `elif btn_id == "autofill": self._autofill_hero_picker()` 分支及
        #   _autofill_hero_picker() 方法一并删除。理由:
        #     · 它要填充的 "英雄选择器"(self.hero_picker) 从未被创建 → 该分支走到最后只会打印
        #       "英雄选择器未启用, 无法自动填充" 的警告, 属纯粹的误导信息;
        #     · 自 v4.22 移除游戏内注入 UI 后(buildXiaochao 首行 return), 游戏页不再推送
        #       任何 hudBtn → 整个 _on_hud_btn 本就不可达;
        #     · 且 "把全部棋子ID自动填充进白名单" 在语义上也不再成立(DualListPanel 只有读取
        #       接口 get_selected_ids/get_selected_displays, 没有批量写入接口)。
        #   真正的白/黑名单配置入口是「数据管理」页签的 whitelist_panel / blacklist_panel。
        elif btn_id == "recruit_start":
            self._toggle_recruit()
        elif btn_id == "recruit_stop":
            self._toggle_recruit() # 停止也是同一个函数
        else:
            self.log(f"[小抄按钮] 未知按钮ID: {btn_id}", level="WARN")

    # ---------------- v4.160★ 重启(供 /restart 与改端口用) ----------------
    def _poll_restart(self):
        """每 2 秒看一次本实例的 .restart_requested, 有就重启本进程。

        为什么必须自己实现: core/bridge.py 只负责【写】这个标记, 全项目原本
        【没有任何地方读它】—— 于是 /restart 和 /reconfigure(改端口) 点完
        什么都不会发生。多开后标记文件按实例分开, 谁的请求只重启谁。
        """
        try:
            from core import bridge as _BR
            if _BR.restart_requested():
                _BR.consume_restart_flag()
                self._do_restart()
                return
        except Exception as _e:
            try:
                self.log(f"[重启] 轮询异常: {_e}")
            except Exception:
                pass
        try:
            self.root.after(2000, self._poll_restart)
        except Exception:
            pass

    def _do_restart(self):
        """重启本实例(保留全部命令行参数, 含 --port / --profile)。"""
        import os as _os
        import sys as _sys
        try:
            self.log(f"[重启] 正在重启本实例(端口 {getattr(self.server, 'port', '?')})…")
        except Exception:
            pass
        for _fn in (lambda: self.server.stop(), lambda: STATE.flush_net()):
            try:
                _fn()
            except Exception:
                pass
        try:
            import serve_userscript as _SU
            _SU.shutdown()
        except Exception:
            pass
        try:
            _os.execv(_sys.executable, [_sys.executable] + _sys.argv)
        except Exception as _e:
            try:
                self.log(f"[重启] 重启失败: {_e}", level="ERROR")
            except Exception:
                pass

    def _rec_ui(self, action_name):
        # Placeholder for UI recording.
        # This function should interact with the MACRO recorder if it's active.
        try:
            from core.recorder import MACRO
            if MACRO.is_recording():
                MACRO.record_ui(action_name)
        except Exception as e:
            self.log(f"[宏] 录制UI动作 {action_name} 失败: {e}", level="ERROR")


def main():
    print("Entering main()")
    app = App()
    print("App mainloop started")
    app.root.mainloop()

if __name__ == '__main__':
    main()

# --- v3.50★ 窗口选择器模态对话框 ---
class WindowPickerDialog(Toplevel):
    def __init__(self, parent, log_func, initial_search_keyword=""):
        super().__init__(parent)
        self.parent = parent
        self.log = log_func
        self.transient(parent)  # Set to be a transient window of the parent
        self.grab_set()  # Make it modal
        self.title("选择游戏窗口")
        self.geometry("800x600")

        self.selected_window_info = None # (hwnd, title, w, h, classname)
        # v4.13★ 修复: search_var 必须在 create_widgets 之前初始化
        self.search_var = tk.StringVar(value=initial_search_keyword or "")

        # UI elements
        self.create_widgets(initial_search_keyword)
        self.update_window_list()

        self.protocol("WM_DELETE_WINDOW", self._on_closing)
        self.wait_window() # Wait until this window is closed

    def create_widgets(self, initial_search_keyword):
        # Search bar
        search_frame = tk.Frame(self, padx=5, pady=5)
        search_frame.pack(fill="x")
        tk.Label(search_frame, text="搜索:", font=("Arial", 10, "bold")).pack(side="left")
        self.search_entry = tk.Entry(search_frame, textvariable=self.search_var, width=40, font=("Arial", 10))
        self.search_entry.pack(side="left", padx=5)
        self.search_entry.bind("<KeyRelease>", self._on_search_key_release)
        tk.Button(search_frame, text="刷新列表", command=self.update_window_list).pack(side="left", padx=5)

        # Treeview for window list
        self.tree = ttk.Treeview(self, columns=("hwnd", "title", "class", "size"), show="headings")
        self.tree.heading("hwnd", text="HWND")
        self.tree.heading("title", text="窗口标题")
        self.tree.heading("class", text="类名")
        self.tree.heading("size", text="尺寸")
        self.tree.column("hwnd", width=80, anchor="center")
        self.tree.column("title", width=300)
        self.tree.column("class", width=150)
        self.tree.column("size", width=100, anchor="center")
        self.tree.pack(fill="both", expand=True, padx=5, pady=5)
        self.tree.bind("<Double-1>", self._on_tree_double_click)

        # Buttons
        button_frame = tk.Frame(self, padx=5, pady=5)
        button_frame.pack(fill="x", side="bottom")
        tk.Button(button_frame, text="📌抓当前窗口", command=self._grab_foreground_window).pack(side="left", padx=5)
        tk.Button(button_frame, text="✚光标选择(3秒)", command=self._pick_window_by_cursor).pack(side="left", padx=5)
        tk.Button(button_frame, text="确认选中", command=self._on_select_confirm).pack(side="right", padx=5)
        tk.Button(button_frame, text="取消", command=self._on_closing).pack(side="right", padx=5)

    def update_window_list(self):
        from core.recorder import MACRO
        self.tree.delete(*self.tree.get_children()) # Clear existing items
        
        keyword = self.search_var.get().strip()
        windows = MACRO.find_windows_by_keyword(keyword) if keyword else MACRO.enum_windows(include_blank=True)

        # Sort: priority first (三国杀/LayaAir/Chrome game tabs)
        def _sort_key(win_info):
            hwnd, title, w, h, classname = win_info
            title_lower = (title or "").lower()
            class_lower = (classname or "").lower()
            if "三国杀" in title_lower or "三国杀" in class_lower: return 0
            if "layaair" in title_lower or "layaair" in class_lower: return 1
            if "chrome" in title_lower and ("tab" in class_lower or "widget" in class_lower): return 2 # Chrome game tabs
            return 99 # Others

        windows.sort(key=_sort_key)

        for hwnd, title, w, h, classname in windows:
            self.tree.insert("", "end", values=(hwnd, title, classname, f"{w}x{h}"), iid=str(hwnd))
            
    def _on_search_key_release(self, event):
        self.update_window_list()

    def _on_tree_double_click(self, event):
        self._on_select_confirm()

    def _grab_foreground_window(self):
        from core.recorder import MACRO
        info = MACRO.foreground_window()
        if info:
            hwnd, title, w, h, classname = info
            self.selected_window_info = info
            self.log(f"[窗口选择器] 抓取当前前台窗口: {title} (hwnd={hwnd})")
            self.destroy()
        else:
            messagebox.showinfo("提示", "未能抓取到当前前台窗口。")

    def _pick_window_by_cursor(self):
        from core.recorder import MACRO
        messagebox.showinfo("提示", "鼠标将变为十字光标, 请在3秒内点击目标窗口。")
        self.withdraw() # Hide the dialog temporarily

        def _done(info, msg):
            # v4.22★ 跨线程修复: 本回调由 MACRO.pick_window_by_cursor 的工作线程直接调用,
            #   而 deiconify() / destroy() / messagebox 全都是 Tk 调用 → 必须整体丢回主线程。
            def _apply():
                self.deiconify() # Show the dialog again
                if info:
                    hwnd, title, w, h, classname = info
                    self.selected_window_info = info
                    self.log(f"[窗口选择器] 光标选择窗口: {title} (hwnd={hwnd})")
                    self.destroy()
                else:
                    messagebox.showinfo("提示", f"光标选择窗口失败: {msg}")
            try:
                self.root.after(0, _apply)
            except Exception:
                _SIL.note("ui/app.py:6538", "self.root.after(0, _apply)")

        threading.Thread(target=lambda: MACRO.pick_window_by_cursor(timeout_sec=3, on_done=_done), daemon=True).start()


    def _on_select_confirm(self):
        selected_item = self.tree.focus()
        if not selected_item:
            messagebox.showwarning("警告", "请先选择一个窗口。")
            return

        hwnd = int(self.tree.item(selected_item, "values")[0])
        # Find the full info from the original list (or re-enumerate)
        from core.recorder import MACRO
        for win_info in MACRO.enum_windows(include_blank=True):
            if win_info[0] == hwnd:
                self.selected_window_info = win_info
                break
        self.destroy()

    def _on_closing(self):
        self.selected_window_info = None # User cancelled
        self.destroy()
