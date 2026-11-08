# -*- coding: utf-8 -*-
"""配置管理：加载/保存 config.json，提供默认配置"""
import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ==================================================================
# v4.160★ 多开(多实例)隔离
#
#   为什么需要: 以前 DATA_DIR / CONFIG_PATH 都由 BASE_DIR(助手根)推出来 ——
#     也就是【所有实例共用一份 config.json 和同一个 data/】。开两个窗口时:
#       · 两边保存配置互相覆盖(最后写者赢);
#       · data/ 下那一堆 json(resource_rules / learned_pids / id_map 系列 /
#         records / 白黑名单 …)全部是直接覆盖写, 没有进程锁 → 互相吞数据;
#       · data/log.txt 被多进程追加, 日志交织成一团;
#       · .restart_requested 只有一个 → A 实例改端口会把 B/C/D/E 全带着重启。
#   现在: 实例目录(默认 = BASE_DIR, 也就是老行为)由环境变量指定,
#     config.json 与 data/ 都落在它下面, 各开各的。
#
#   注意 BASE_DIR 【不变】: 代码、bridge/ 下的脚本、参考_配置表 这些是
#     【只读资源】, 全员共享一份才对(各存一份既浪费又会让"改脚本"只对某
#     一个实例生效 —— 那正是最难查的一类 bug)。
#
#   三个环境变量(由 main.py 的命令行参数在 import 任何 core 模块之前设置):
#     SGS_INSTANCE_DIR  : 实例目录绝对路径
#     SGS_INSTANCE_NAME : 实例名(只用于界面显示/日志, 默认空 = 单实例)
#     SGS_INSTANCE_SLOT : 实例序号(0 起), 用来错开"脚本服务/图片服务"这些
#                         附属端口, 免得第二个实例去复用第一个实例的服务。
#     SGS_BRIDGE_PORT   : 覆盖本次进程的桥接端口(不落盘, 只本次生效)
# ==================================================================
INSTANCE_NAME = (os.environ.get("SGS_INSTANCE_NAME") or "").strip()
_ENV_DIR = (os.environ.get("SGS_INSTANCE_DIR") or "").strip()
INSTANCE_DIR = os.path.abspath(_ENV_DIR) if _ENV_DIR else BASE_DIR
try:
    INSTANCE_SLOT = max(0, int(os.environ.get("SGS_INSTANCE_SLOT") or 0))
except (TypeError, ValueError):
    INSTANCE_SLOT = 0

DATA_DIR = os.path.join(INSTANCE_DIR, "data")
CONFIG_PATH = os.path.join(INSTANCE_DIR, "config.json")


def is_multi_instance():
    """True = 当前跑在某个实例目录下(多开模式); False = 老的单实例布局。"""
    return os.path.abspath(INSTANCE_DIR) != os.path.abspath(BASE_DIR)


def ensure_instance_dir():
    """建实例目录, 并在首次启动时从助手根那份 config.json 复制一份当初始配置。

    为什么不直接共用根 config.json: 共用就失去隔离意义(改端口/开关会串)。
    为什么不从 DEFAULT_CONFIG 生成: 用户现有的节奏/名单/白黑名单都在根那份里,
    复制过去 = "跟我现在用的习惯一样, 只是从此各管各的", 符合直觉。
    复制失败不致命(退回默认值即可), 但要打印, 不然用户不知道自己配置去哪了。
    """
    if not is_multi_instance():
        return
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
    except Exception as e:
        print("[多开] 建实例目录失败 %s: %s" % (DATA_DIR, e))
    if os.path.exists(CONFIG_PATH):
        return
    src = os.path.join(BASE_DIR, "config.json")
    if not os.path.exists(src):
        return
    try:
        import shutil
        shutil.copyfile(src, CONFIG_PATH)
        print("[多开] 实例配置已初始化: %s (复制自助手根 config.json)" % CONFIG_PATH)
    except Exception as e:
        print("[多开] 复制初始配置失败 %s -> %s: %s" % (src, CONFIG_PATH, e))

DEFAULT_CONFIG = {
    # 游戏窗口区域（绝对屏幕坐标 [x, y, w, h]），None 表示未校准
    "game_region": None,
    # 子区域：相对游戏窗口的比例坐标 [rx, ry, rw, rh]（0~1），适配任意分辨率
    "sub_regions": {
        "shop":        [0.26, 0.72, 0.50, 0.22],   # 商店5张卡区域
        "gold":        [0.86, 0.42, 0.09, 0.07],   # 金币数字
        "refresh_btn": [0.855, 0.17, 0.09, 0.06],  # 刷新按钮
        "level":       [0.05, 0.55, 0.10, 0.08],   # 等级区域
        "players":     [0.30, 0.02, 0.40, 0.09],   # 顶部8名玩家条
        "lord_area":   [0.01, 0.01, 0.10, 0.16],   # 我的主公头像区
        # v3.54★ 真实逻辑地址定义 (非坐标模式)
        "logical_paths": {
            "战斗区": "LIVE.me['_lineUp']",
            "备战区": "LIVE.me['_board']",
            "购卡区": "LIVE.shop"
        }
    },
    "capture_size": [150, 200],   # 样图入库默认裁剪尺寸(宽,高)
    "hotkeys": {"start_find": "<f8>", "stop": "<f9>"},
    # v4.83★ 锦囊 / 随征【优先名单】(在「快捷键」页填, 保存即落盘并推送到游戏页):
    #   spell / followup = 逗号分隔的名字或ID(越靠前越优先, 留空 = 认随征ID表 / 手牌里的锦囊)
    #   followup_target  = 随征给谁(按名字在阵上找; 留空 = 老行为"阵上位置0")
    #   spell_use_all    = 识别到的锦囊从最右开始依次用完;  followup_use_all = 一次全给同一个人
    "priority_lists": {"spell": "", "followup": "", "followup_target": "",
                       "spell_use_all": False, "followup_use_all": False},
    # v4.85★ 「节奏」页: 脚本里那些【写死的延迟】(毫秒), 逐项可改 —— 用户原话
    #   "这个新开一个 python 端的 tab 页都可改? 且加上说明? 尤其是遣散武将"。
    #   键与脚本 SGS_TIMING_DEFAULT 一一对应; 【留空/不填 = 不下发 = 脚本用自己的默认】
    #   (默认值与 v4.84 逐毫秒一致, 所以不动它就是老样子)。
    "timing": {},
    # v4.145★ 【极速】硬开关 —— True = 那些"等服务器回应"的等待全省(见 remote_ops.update_fast_mode
    #   的说明: 刷新后不核对商店 / 批量上阵无间隔 / 不再因 CanOperate=false 拒绝发请求)。
    #   用户要的就是"不要延迟, 直接给", 所以【默认开】; 怀疑漏操作时把它改成 false,
    #   或在助手「节奏」页点「标准」「保守」档 —— 那两档会自动把它关掉。
    "fast_mode": True,
    # v4.173★ 【自动随征】总开关 —— True(默认)= 随征类入口(剧本「随征」「自动随征」步骤 + Shift+3)正常发包;
    #   False = 这些入口一律不发包(只在日志写明原因), 玩家手动拖卡不受影响。想"全局关掉自动随征"就关它。
    "auto_follow_up": True,
    # v4.182★ 【性能】页: 助手端「性能」页下发的渲染闸门(见 remote_ops.update_render)。
    #   全部 false = 脚本默认行为(该刷的都刷)。任一为 true 即对应重绘被暂停:
    #   off=取消渲染主开关(角标/手牌区/换肤全停); badge=暂停角标; hand=暂停手牌区重建;
    #   skin=暂停换肤动画与注入; stats=暂停助手端统计面板与录制事件表重绘。
    #   ★ 默认全 false(不影响任何老用户); 连接游戏页时会自动带过去。
    "render": {"off": False, "badge": False, "hand": False, "skin": False, "stats": False},
    "find_interval": 2.5,     # 每次刷新间隔（秒）
    "max_refresh": 30,        # 最大刷新次数
    "auto_buy": True,         # 识别到目标后是否自动购买
    # v4.110★ 这两张表原先是拍脑袋的占位值(pool_totals 的注释原文就写着
    #   "按当前版本自行修正")。刷新决策器拿它们当分母, 于是等级 2/3/4 的"该不该刷新、
    #   大概还要刷几次"一直是按错模型算的(如 2 级写的 70/30, 实际是 50/50)。
    #   现在换成【赛季配置表导出的真值】: python tools/export_pool_tables.py
    #   → 参考_配置表/发牌概率与卡池_s10.md
    #     · 每种几张 = ChessRankConf.TotalCount: 1费18 / 2费15 / 3费13 / 4费11 / 5费9 / 6费7
    #       (8 家共享 2078 张 / 184 种)
    #     · 各等级各费用概率 = ChessShopConf 的档位概率(每行是 1费~6费)
    #   ⚠ 换赛季要重跑导出脚本更新, 别照抄别季的数。
    "pool_totals": {"1": 18, "2": 15, "3": 13, "4": 11, "5": 9, "6": 7},
    "star_odds": {
        "1": [100, 0, 0, 0, 0, 0],
        "2": [50, 50, 0, 0, 0, 0],
        "3": [25, 35, 40, 0, 0, 0],
        "4": [10, 20, 35, 35, 0, 0],
        "5": [7, 15, 28, 30, 20, 0],
        "6": [0, 10, 25, 30, 20, 15],   # 6 级另有神将 0.4%(客户端不读, 不计入)
        # s10 最高就 6 级。7~9 是更早赛季/早期估算留下的, 本季取不到 —— 刷新决策器会按
        # "数据不全"降级返回 None(不瞎给建议), 先留着不删。
        "7": [0, 5, 15, 40, 40],
        "8": [0, 0, 10, 35, 55],
        "9": [0, 0, 5, 30, 65]
    },
    "video": {"diff_threshold": 0.12, "sample_fps": 2},
    "ocr_interval": 3.0,      # 持续识别间隔（秒）
    # ==================================================================
    # 【已停用】坐标点击配置 —— 保留骨架, 当前全项目没有任何代码读取它
    #
    # 用途(历史): 通过屏幕绝对坐标模拟鼠标点击, 支撑「跳过战斗 / 卖出 /
    #   锦囊三选一 / 卡牌拖拽上阵」这几项操作。
    #     · quick_points = 单点坐标, 需在界面上先校准(None 表示未校准);
    #     · quick_grids  = 网格描述, 用「首格中心(x, y) + 横向间距 dx +
    #       纵向间距 dy + 列数 cols」把「第 N 格」换算成屏幕坐标。
    #
    # 现状(已核查): 实现它的 core/quick_ops.py 已不存在, 对应的页签
    #   (codex_tab / quick_tab / records_tab) 也已移除 —— 本配置成了孤儿。
    #   现行「跳过战斗 / 遣散 / 买卡」已全部改走协议通道(油猴 psend),
    #   不再依赖坐标点击。
    #
    # 处理建议: 将来若要重做坐标模式可直接复用本骨架; 确认不需要可整段删除。
    # ==================================================================
    # 快捷操作：绝对屏幕坐标点位 [x, y]，None=未校准
    "quick_points": {
        "skip_btn": None,       # 跳过战斗按钮
        "sell_btn": None,       # 卖出按钮
        "confirm_btn": None,    # 卖出确认按钮（无确认弹窗则留空）
        "picks": [None, None, None]  # 锦囊三选一 3个选项位置
    },
    # 网格：首格中心 + 横向间距 + 纵向间距 + 列数
    "quick_grids": {
        "board": {"x": 0, "y": 0, "dx": 0, "dy": 0, "cols": 7},
        "bench": {"x": 0, "y": 0, "dx": 0, "dy": 0, "cols": 8}
    },
    "quick": {
        "sell_with_confirm": False,   # 卖出后是否需要点确认
        "auto_skip": False,           # 自动跳过战斗（需先截取跳过按钮样图）
        "auto_skip_interval": 4
    },
    "bridge": {
        "port": 8765,             # 油猴桥接端口
        "auto_start": True,       # 助手启动时自动开桥接服务
        "finder_use_bridge": False,  # 自动找将走桥接(不占鼠标)
        "refresh_pos": None       # 商店刷新按钮舞台坐标[x,y]（扫描可点控件后填入）
    },
    # ==================================================================
    # v4.46★ 连接时推给油猴脚本的【棋子名/技能】映射, 是否只含【一个赛季】
    #   默认 False = 全赛季(3346 条, 与 v4.32 起的行为一致)。
    #   为什么要做成开关而不是直接过滤: "全量"当年是【修 bug 修出来的】——
    #     实机上出现过场上是 20503211(董袭), 武将表(300+ 条)里没有它 → 页面名字解析
    #     退化成裸 ID, 日志写成 "20503211:1366@2", 按名字找卡永远匹配不上。
    #     所以过滤的代价是: 往季变体卡的名字/技能会退化成裸 ID。
    #   开了之后推的表更小、不会串季; 具体推到哪一季 = 「赛季数据」页那个『赛季号』。
    #   在『赛季数据』页勾选切换, 会写回这里(见 a_season_toggle_script_map_season)。
    # ==================================================================
    "script_map_season_only": False,
    "script_map_season_no": "",   # 留空 = 跟随当前赛季(SC.current_season())
    # v3.48★ 临时关闭网络事件聚合发送 和 Tween加速 Hook (按用户要求排查"连不上"):
    #   三个 Flag 默认全部关闭. 想恢复勾选请在助手UI面板勾选后重启游戏页.
    #   tweenScale       : 压缩 Tween duration/delay (视觉更短, 按 Timer.scale 再砍半)
    #   recruitFastTween : 招募阶段 Tween/Handler/Timer 延迟→秒完成 (最狠, 卡飞动画/倒计时)
    #   batchProtocol    : Python<->油猴 协议直发 自动 16ms 窗口合并 (5条买卡刷准备 并发 1.2s 集中verify)
    "flags": {
        "tweenScale": False,
        "recruitFastTween": False,
        "batchProtocol": False,
    },
    # v4.176★ 【跳过三连合成动画】
    #   三连合成时游戏会播一段动画, 那期间 CanOperate=false / IsPlayCompositeAni=true,
    #   剧本和挑牌全都卡着等它放完。开了就每 200ms 看一眼, 一开始播就立刻跳掉。
    #   False = 老行为(不下发时脚本里也是 false)。界面在「节奏」页末尾那个勾选框。
    #   ⚠ 与 blockTripleCombine(要不要合) 不是一回事: 这个只管"已经合了, 那段动画等不等"。
    "skip_triple_ani": False,
    # v4.176★ 【用锦囊后需补挑的锦囊名单】—— 对应脚本 window.SPELL_NEED_PICK。
    #   默认 草船 / 三顾(这类锦囊用出去之后还要手动再挑一次, 脚本自动补挑)。
    #   留空("") = 恢复脚本默认(草船/三顾)。界面在「快捷键」页优先名单那一块。
    "spell_need_pick": "草船,三顾",
    # ==================================================================
    # v4.24★ 补进默认值
    #   这两个键原来【只存在于用户的 config.json 里】: 由于 load_config() 是
    #   "深拷贝默认值 + 用户覆盖", 默认值里没有的键就补不回来 ——
    #   config.json 一旦被删/损坏, 界面上的遣散策略和速度档位会静默退回硬编码兜底。
    #   代码确实在读写它们: ui/app.py 读 dismiss_policy.max_star/min_cost、
    #   fps.speed/mode/fps, 并在切换时 save_config() 回写。
    #   注: 历史上还有 hud / bridge.recruit / bridge.slot_positions 三个键属于同类
    #   "只在用户配置里"的键, 但已核查【全项目没有任何代码读取它们】(遗留数据),
    #   所以不往默认值里加 —— 检查项见 dev/_verify_static.py 的"配置漂移"。
    # ==================================================================
    "dismiss_policy": {
        "area": "bench_idle",   # 遣散区域: 目前只支持备战区空闲卡
        "max_star": 1,          # 保留「星级 ≤」的卡(1 = 只遣散 1 星)
        "min_cost": 1           # 保留「价值 ≥」的卡
    },
    "fps": {
        "speed": "1.0",         # 顶栏速度档位(1.0/2.0/3.0/5.0)
        "mode": "default",      # fps 面板: 档位名
        "fps": "30"             # fps 面板: 目标帧率
    },
    # ==================================================================
    # v4.27★ 剧本(有先后顺序的步骤流)
    #   结构: [{"id","name","hotkey","steps":[{"op","target","pos",...}]}]
    #   ui/app.py「剧本」tab 在保存时写 self.cfg["scenarios"] 并 save_config()。
    #   必须进默认值: 否则 config.json 被删/损坏后剧本会静默丢光, 而且
    #   config.json 里出现的这个键会被「配置漂移」检查判成新增漂移。
    # ==================================================================
    "scenarios": [],
    # v4.41★ 用户自建的「剧本预设」: [{"name":"我的预设","steps":[...]}, ...]
    #   点「+ 预设 → 把当前剧本存为预设…」就会往这里写一条; 菜单里带 ★ 的是它们。
    "scenario_presets": [],
    # v4.41★ 用户自建的「目标预设」: ["auto", "1", "陈武", ...]
    #   剧本编辑对话框里「目标」那个下拉框点「+存为预设」就存这儿。
    "pick_presets": [],
    # v4.66★ 底部状态栏是否折叠(v4.63 加的"▾ 状态"那个按钮)。
    #   为什么要在这里补一行: 这个键以前只在 app.py 里被写(_toggle_status_bar)和读,
    #   而 DEFAULT_CONFIG 里没有它 —— 于是它每次保存都会变成 config.json 里的"新增漂移"
    #   (dev/_verify_static.py 的漂移检查一直报"裸索引写入未定义配置键")。
    #   默认 False = 展开(和 v4.63 刚上线时一样)。
    "status_collapsed": False,
    # v4.68★ 协议流水【是否落盘】(data/net_samples.json / net_structures.json)。
    #   用户原话: "这个功能占内存, 占存储, 占发数据时间, 没有什么用, 耽误别的功能使用, 可以不要吧?"
    #   —— 拆开看这三样成本: 最大的是【写盘】(每条推送都要把 0.7MB/1.9MB 的文件在内存里合并
    #   再定期重写), 其次是内存里那两份常驻镜像。而它的收益(事后查某一局 + 离线抓包诊断)只有
    #   "真要查某一局"时才用得上 → 默认关, 需要时在『数据管理』页勾一下打开。
    #   ⚠ 开关原来挂在『对局复盘』页上, v4.71 那一页按用户要求删掉了 —— 所以开关搬到了
    #     『数据管理』页(_build_net_record_section)。删页面时把开关一起带走的话, 用户以后
    #     想诊断就只能去手改 config.json(没人知道有 net_record 这个键)。
    #   ⚠ 关的只是【存盘】: 实时那几页走 STATE.net_sink(内存队列), 不受影响。
    "net_record": False,
    # v4.87★ 资源替换第二套机制「即时注入」: 两套可以同时开着, 但同一条资源只能走一套。
    #   url = 加载前改地址(现有机制); inject = 直接换已显示对象身上的纹理(即时)。
    #   默认全 url —— 不开这个开关, 现有规则行为一点不变。
    #     on    : 注入总开关(关掉 = inject 规则自动降级成 url)
    #     modes : 类别→模式, 如 {"自走棋背景":"inject"}; 每条规则自己的 mode 字段优先级最高
    "res_inject": {
        "on": False,
        "modes": {},
    },
    # v4.113★ 动态背景: 拿官方 Spine 三件套(skel 骨架 + atlas 切片表 + png 贴图)在【战场背景】
    #   那个位置播【真动画】。为什么必须三件套一起: 只换 png 得到的是 atlas 图集原图 ——
    #   一堆切片素材表, 不是底图也不会动(用户原话: "是静态的且是他的动态帧的那个插画图,
    #   动态是在它上面取一部分, 都不是底图")。
    #     on      : 总开关
    #     no      : 动态皮编号(dyn_flag.json 的 no 字段, 如 7133001 = 众乐和鸣*乐蔡文姬)
    #     frame   : 用哪一套帧 —— beijing(背景帧) / daiji(待机帧)
    #     anim    : 播哪个动画名; 空 = 自动取第一套
    #     pattern : 要被顶掉的那张背景图(匹配串, 与资源替换页同口径)
    #     scale/x/y: 缩放与位置微调(盖层对不齐时调这三个)
    "dyn_bg": {
        "on": False,
        "no": "",
        "frame": "beijing",
        "anim": "",
        "pattern": "tavernChessBg",
        "scale": 1.0,
        "x": 0,
        "y": 0,
    },
    # v4.184★ 回放: 离线复现对局 / 调算法, 不用真开游戏。
    #   mode      : "off" 正常 | "record" 启动即录制双向桥消息 | "play" 启动即回放指定文件
    #   file      : play 模式要回放的 replay 文件(相对 data/replays/ 或绝对路径)
    #   autorecord: 即便 mode="off", 每次进对局也悄悄录制一份(事后可回放)
    "replay": {
        "mode": "off",
        "file": "",
        "autorecord": False,
    },
}


# v4.110★ 概率模型迁移。为什么非得有这一段: load_config 是【用户值覆盖默认值】,
#   光把 DEFAULT_CONFIG 改成真值, 老用户 config.json 里那份旧占位值照样盖在上面,
#   等于没改 —— 所以显式迁一次。
#   只在【等于旧占位值】时替换: 用户要是自己调过(比如按自己打法估的), 原样保留。
#   ⚠ 只在内存里改, 不落盘: 自检脚本也会调 load_config, 让它顺手去写用户的
#     config.json 是危险的(有过教训)。反正每次启动都会迁一次, 幂等无害;
#     用户下次保存配置时自然就把新值写进去了。
_POOL_TOTALS_LEGACY = {"1": 22, "2": 18, "3": 15, "4": 10, "5": 8}
_STAR_ODDS_LEGACY = {
    "2": [70, 30, 0, 0, 0], "3": [60, 35, 5, 0, 0], "4": [40, 40, 20, 0, 0],
    "5": [7, 15, 28, 30, 20], "6": [0, 10, 25, 35, 30], "7": [0, 5, 15, 40, 40],
    "8": [0, 0, 10, 35, 55], "9": [0, 0, 5, 30, 65],
}


def _migrate_pool_model(cfg):
    """老占位值 -> 配置表真值(真值的出处见 DEFAULT_CONFIG 里那段注释)。返回是否改过。"""
    changed = False
    if cfg.get("pool_totals") == _POOL_TOTALS_LEGACY:
        cfg["pool_totals"] = json.loads(json.dumps(DEFAULT_CONFIG["pool_totals"]))
        changed = True
    if cfg.get("star_odds") == _STAR_ODDS_LEGACY:
        cfg["star_odds"] = json.loads(json.dumps(DEFAULT_CONFIG["star_odds"]))
        changed = True
    return changed


def load_config():
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except json.JSONDecodeError:
            print(f"[错误] {CONFIG_PATH} 格式损坏，将使用默认配置")
        except PermissionError:
            print(f"[错误] 无法读取 {CONFIG_PATH}，权限不足")
        except Exception as e:
            print(f"[错误] 加载配置时发生未知错误: {e}")
            cfg = {}
    merged = json.loads(json.dumps(DEFAULT_CONFIG))  # 深拷贝默认
    # v4.110★ 迁移必须在 merge 【之前】, 且迁的是读进来的用户那份:
    #   merge 是深度合并, 默认表里新加的 "6" 会和用户那份旧的 1~5 费拼在一起,
    #   拼出来既不等于旧值也不等于新值(第一次就是这么写错的, 迁移整个没生效)。
    _migrate_pool_model(cfg)      # 只在内存, 不落盘 —— 见函数上的注释
    _deep_merge(merged, cfg)
    # v4.160★ 命令行端口覆盖(SGS_BRIDGE_PORT, 由 main.py --port 设置)。
    #   为什么要覆盖而不是改 config.json: 多开时【端口是启动脚本分给这个实例的】,
    #   写进 config.json 会让"同一个实例目录换端口"变成改文件, 而端口冲突是
    #   每次启动都要重新判的事 —— 落盘反而留下过期值。本次生效最干净。
    _p = os.environ.get("SGS_BRIDGE_PORT", "").strip()
    if _p:
        try:
            _pn = int(_p)
            if 1024 <= _pn <= 65535:
                merged.setdefault("bridge", {})["port"] = _pn
            else:
                print("[配置] SGS_BRIDGE_PORT=%s 不在 1024-65535, 忽略" % _p)
        except ValueError:
            print("[配置] SGS_BRIDGE_PORT=%r 不是整数, 忽略" % _p)
    return merged


def save_config(cfg):
    """v4.109★ 原子写: 先写临时文件, 再 os.replace 整体替换。

    以前是直接 open(CONFIG_PATH, "w") 覆盖 —— 写到一半时崩溃/断电/被杀进程,
    磁盘上会留下一个【截断的坏 config.json】, 下次启动要么读不出来要么丢一片配置。
    core/tool_state.py 早就是这个写法, 这里补上(同为写本机状态文件, 口径统一)。
    """
    try:
        d = os.path.dirname(CONFIG_PATH)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())       # 落盘, 不然断电时临时文件本身也可能是空的
        os.replace(tmp, CONFIG_PATH)   # 原子替换: 要么旧文件, 要么新文件, 没有中间态
    except Exception as e:
        # 兜底: 原子写走不通(罕见, 如权限/磁盘问题)也要尽量把配置写出去,
        # 退回老的直接覆盖写法 —— 宁可有截断风险, 也不能什么都不写。
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            print("[错误] 保存配置失败: %s" % e)


def _deep_merge(base, extra):
    for k, v in extra.items():
        if isinstance(v, dict) and k in base and isinstance(base[k], dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


# v3.88★ 全局配置实例，供其他模块直接引用 (修复 cannot import name 'CONFIG' 错误)
ensure_instance_dir()      # v4.160★ 多开: 首次进实例目录时初始化它的 config.json
CONFIG = load_config()

def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def slot_ports(base, offsets=(0, 1, 2, 4, 24)):
    """按实例序号错开"附属服务"(脚本服务 8766 / 图片服务 8767)的候选端口。

    为什么必须错开: serve_userscript.ensure() 和 res_server.ensure() 都是
    "探到端口上有服务就直接复用"。单实例时这是好事(不重复起服务); 多开时是
    灾难 —— 第二个实例会静默复用第一个实例的服务, 于是"装脚本/取图片"全都
    指向别人, 而且看不出来。按 slot 整体平移一格(20 个端口)即可隔开。
    slot 0 的取值与单实例时完全相同, 老行为不变。
    """
    return [int(base) + int(o) + INSTANCE_SLOT * 20 for o in offsets]


def set_bridge_port(port):
    """只改【本实例】config.json 里的 bridge.port, 其它键一个不动(原子写)。

    为什么不直接 save_config(CONFIG): CONFIG 是 load_config() 的【合并结果】,
    整份写回去会把 DEFAULT_CONFIG 的全部分支固化进文件 —— 以后我们新增配置项
    或改默认值, 你这份文件里的旧值会一直赢, 升级等于没升级(项目里踩过)。
    """
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    data.setdefault("bridge", {})["port"] = int(port)
    save_config(data)
    return int(port)


def runtime_path(name):
    """运行期标记文件(如 .restart_requested)的落点 —— 必须跟实例走。

    以前 bridge.py 直接拼 BASE_DIR/.restart_requested: 单实例没问题, 多开时
    5 个实例共用这一个标记 → 任何一个实例点"重启/改端口", 其余 4 个会被
    带着重启(或者被别的实例把标记删掉, 自己的重启永远等不到)。
    """
    return os.path.join(INSTANCE_DIR, name)
