# -*- coding: utf-8 -*-
"""v4.11★ 从 ChessConf.csv / ChessSpellConf.csv 加载武将和锦囊数据

白名单 = 当前赛季(SeasonID='10')且 IsShop='1' 的武将 (商店可购买)
黑名单 = 全部赛季的武将 (历史数据)

CSV 实际是 Tab 分隔 (TSV)。加载后保存到 data/lists/whitelist.json / blacklist.json,
用户可在 UI 中手动编辑, 修改后保存, 下次启动自动加载.
"""
import csv
import os
import json
import re
import sys
import threading
from core import silence as _SIL

try:
    from .config import DATA_DIR
except ImportError:
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from core.config import DATA_DIR

# ChessConf.csv 路径 (项目根目录 / source 目录)
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CHESS_CONF_PATHS = [
    os.path.join(_BASE, "ChessConf.csv"),                           # 项目根目录
    os.path.join(_BASE, "三国杀脚本", "sgs_forward_looking-main", "source", "ChessConf.csv"),
    os.path.join(_BASE, "..", "三国杀脚本", "sgs_forward_looking-main", "source", "ChessConf.csv"),
]

def _read_current_season():
    """v4.32★ 当前赛季: 优先读 data/season/season.json (由 tools/season_config.py 抓取后写入),
    读不到才回退 '10'。赛季更新时只要跑一次「赛季数据获取」, 这里就自动跟着变 ——
    不用再手改代码里的常量(以前是写死的 '10', 赛季一变白名单/名字映射全错)。
    """
    try:
        p = os.path.join(DATA_DIR, "season", "season.json")
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                s = str((json.load(f) or {}).get("season") or "").strip()
            if s:
                return s
    except Exception as e:
        print(f"[csv_loader] 读 data/season/season.json 失败(按 S10 处理): {e}")
    return "10"


CURRENT_SEASON = _read_current_season()   # 当前赛季 (默认 S10)
_lock = threading.RLock()
_cache = {}


def _parse_skill_ids(raw):
    """ChessSkill 列 → 技能ID列表。
    实机数据形如 "[330411]" / "[2011061,2011062]" / "330411" (也可能带空格)。
    丁奉(S10, 21003051) 的 ChessSkill = [330411] —— 这正是页面端"解析不出技能"时
    需要兜底的那份数据。"""
    if raw is None:
        return []
    out = []
    for m in re.findall(r"\d+", str(raw)):
        v = int(m)
        if v and v not in out:
            out.append(v)
    return out


def _find_chess_conf():
    """查找 ChessConf.csv 文件路径"""
    for p in _CHESS_CONF_PATHS:
        if os.path.exists(p):
            return p
    return None


def _parse_chess_conf(path):
    """解析 ChessConf.csv (Tab 分隔), 返回 [{chessID, name, rank, season, isShop, ...}]"""
    if not path or not os.path.exists(path):
        return []
    result = []
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, delimiter="\t")
            for row in reader:
                chess_id = (row.get("ChessID") or "").strip()
                name = (row.get("ChessName") or "").strip()
                if not chess_id or not name:
                    continue
                result.append({
                    "chessID": chess_id,
                    "name": name,
                    "rank": (row.get("ChessRank") or "").strip(),
                    "season": (row.get("SeasonID") or "").strip(),
                    "isShop": (row.get("IsShop") or "").strip(),
                    "isInRandomPool": (row.get("IsInRandomPool") or "").strip(),
                    "skillDesc": (row.get("ChessSkillDesc") or "").strip(),
                    "minionType": (row.get("MinionType") or "").strip(),
                    # v4.32★ 技能ID: 页面端判定"这张卡要不要选人/该发哪个技能ID"要用
                    "skills": _parse_skill_ids(row.get("ChessSkill")),
                    "display": f"{name} ({chess_id})",
                })
    except Exception as e:
        print(f"[csv_loader] 解析 ChessConf.csv 失败: {e}")
    return result


def load_from_csv(force_reload=False):
    """从 CSV 加载全部武将数据, 带缓存"""
    with _lock:
        if not force_reload and "all_chess" in _cache:
            return _cache["all_chess"]
        path = _find_chess_conf()
        data = _parse_chess_conf(path)
        _cache["all_chess"] = data
        return data


def get_whitelist_from_csv():
    """白名单: 当前赛季且 IsShop=1 的武将 (用于其他列表的选择范围)"""
    all_data = load_from_csv()
    return [c for c in all_data if c["season"] == CURRENT_SEASON and c["isShop"] == "1"]


def get_all_generals_from_csv():
    """全量武将(全部赛季) —— 只用于【统计 / 查看】, 不参与买卡与遣散判断。

    v4.101★ 这份数据以前被当成"黑名单"存(见下面 get_blacklist_from_csv 的说明),
      用户其实是拿黑名单当"全赛季武将统计表"看的。现在独立成一个文件
      data/lists/all_generals.json, 界面上有专门的统计入口, 两边各归各位。
    """
    return load_from_csv()


def save_all_generals(generals):
    """保存全量武将到 json(供统计/查看用)"""
    path = _list_path("all_generals")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(generals, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"[csv_loader] 保存全量武将失败: {e}")
        return False


def get_blacklist_from_csv():
    """黑名单【不再】从 CSV 生成 —— 返回空列表。

    v4.101★ 以前这里写的是 `return load_from_csv()`, 也就是【全部赛季的武将】。
      于是每次赛季更新(tools/season_config.py 里 save_blacklist(get_blacklist_from_csv()))
      都会把 3346 条全量武将灌进 data/lists/blacklist.json, 黑名单从此等于"禁止一切":
        · F6 买目标: 每张卡都先命中黑名单 → 一张都买不到(用户报的"F6 不管用", 实测
          白名单 184 条被黑名单 100% 覆盖);
        · F7 遣散: 回退分支按名字遣散 → 等于把备战区整排清空。
      黑名单的本义是"用户自己填的、不想买/不想留的武将", 可以为空 —— 空着才是对的。
      想看全部武将请用 get_all_generals_from_csv() / 界面上的「全量武将·统计」。
    """
    return []


def _lists_dir():
    d = os.path.join(DATA_DIR, "lists")
    os.makedirs(d, exist_ok=True)
    return d


def _list_path(name):
    return os.path.join(_lists_dir(), f"{name}.json")


def load_whitelist():
    """加载白名单 json (如果用户编辑过则用 json, 否则从 CSV 生成)
    返回 [{chessID, name, display, ...}]"""
    path = _list_path("whitelist")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list) and data:
                    return data
                if isinstance(data, dict) and "generals" in data:
                    return data["generals"]
        except Exception:
            _SIL.note("core/csv_loader.py:186", "with open(path, 'r', encoding='utf-8') as f: d")
    # 首次加载: 从 CSV 生成
    return get_whitelist_from_csv()


def load_blacklist():
    """加载黑名单 json (全赛季)"""
    path = _list_path("blacklist")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list) and data:
                    return data
                if isinstance(data, dict) and "generals" in data:
                    return data["generals"]
        except Exception:
            _SIL.note("core/csv_loader.py:203", "with open(path, 'r', encoding='utf-8') as f: d")
    return get_blacklist_from_csv()


def save_whitelist(generals):
    """保存白名单到 json (用户编辑后保存)"""
    path = _list_path("whitelist")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(generals, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"[csv_loader] 保存白名单失败: {e}")
        return False


def save_blacklist(generals):
    """保存黑名单到 json"""
    path = _list_path("blacklist")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(generals, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"[csv_loader] 保存黑名单失败: {e}")
        return False


# ================================================================ 选人判定表(v4.39★)
# 「必须由玩家手动选目标」的技能。游戏自己的判据是:
#     ChessSkill.TargetTyp1 == TavernChessTargetType.CHESSHTTShouDongXuanZe
# 枚举定义在游戏的打包闭包里 —— 页面端既读不到它、也没法在运行时问它, 所以只能把【数值】
# 固化在助手这边, 再用技能描述做一次自校验(见 pick_skill_ids 末尾)。
PICK_TARGET_TYP = 21

_season_json_cache = {}

# P3-B memoization: 绑定到底层 load_from_csv() 返回的列表对象身份 —— force_reload 重读 CSV 时
# 会生成新列表对象, 旧缓存因 `is data` 不成立自动失效, 行为与未缓存时完全等价(只省重算, 不改语义).
_chess_skill_map_cache = {}
_chess_names_all_cache = {}


def season_dir_name(season=None):
    """赛季名 → data/season 下的目录名(统一带 s 前缀)。

    v4.74★ 为什么单独抽一个函数: 调用方传进来的写法有两种 ——
      · csv_loader 内部: CURRENT_SEASON 是 "10"(读自 season.json, 不带 s);
      · 界面/规则内核: core.resource_rules.norm_season 归一化成 "s10";
    而目录实际叫 `s10`。旧代码一律按 "s" + sea 拼, 于是界面上填的 "s10" 被拼成
    `ss10` —— 主路径永远不存在, 只能靠"扫 data/season 取最大赛季号"兜底。
    现象就是这个赛季形同虚设: 填 s9/s11 都一样, 读到的永远是那个唯一的 s10。
    """
    s = str(season if season is not None else CURRENT_SEASON).strip()
    if not s:
        return ""
    if s[0] in "sS" and s[1:].isdigit():
        return "s" + s[1:]
    return "s" + s if s.isdigit() else s


def _season_config_paths(season=None):
    """候选赛季配置表路径: 指定赛季优先, 之后按赛季号从大到小(赛季更新后旧目录还在时兜底)。"""
    out = []
    want = season_dir_name(season)
    if want:
        out.append(os.path.join(DATA_DIR, "season", want, "TavernChessConfig.json"))
    try:
        base = os.path.join(DATA_DIR, "season")
        others = []
        for name in os.listdir(base):
            # v4.74★ 要排掉的是【同一个赛季的目录】(规范化后相等), 不是字面比较 ——
            #   多一个 ss10 不存在, 但"s10" 与 want 字面不等时会把 s10 当"别的赛季"再排一次。
            if not name.startswith("s") or season_dir_name(name) == want:
                continue
            p = os.path.join(base, name, "TavernChessConfig.json")
            # 用 isdigit 判而不是 try/int: 目录里可能有非赛季名的 s 开头项
            if name[1:].isdigit() and os.path.exists(p):
                others.append((int(name[1:]), p))
        others.sort(reverse=True)
        out += [p for _, p in others]
    except Exception as e:
        # 扫描失败不影响主路径(指定赛季那条), 但要说出来, 不静默
        print(f"[csv_loader] 扫描 data/season 目录失败: {e}", file=sys.stderr)
    return out


def season_dir_choices():
    """data/season 下有哪些赛季目录 → ["s10 ★", "s9"(无 ★ = 没配置表)] —— 界面下拉框用。

    带 ★ 的才是"能读的赛季"。没有配置表的那些也留在列表里, 是为了让用户一眼看出
    "我填的这个赛季确实没数据", 而不是怀疑助手坏了(旧版的表现就是静默回退到别的赛季)。
    """
    out = []
    try:
        base = os.path.join(DATA_DIR, "season")
        for name in sorted(os.listdir(base), reverse=True):
            d = os.path.join(base, name)
            if not os.path.isdir(d) or not name.startswith("s") or not name[1:].isdigit():
                continue
            has = os.path.exists(os.path.join(d, "TavernChessConfig.json"))
            out.append(name + (" ★" if has else ""))
    except Exception as e:
        print(f"[csv_loader] 列 data/season 目录失败: {e}", file=sys.stderr)
    return out


def season_config_path(season=None):
    """这个赛季【实际会读】的那张表; 表不存在时返回"首选那条路径"(供界面提示"没有")。"""
    ps = _season_config_paths(season)
    for p in ps:
        if os.path.exists(p):
            return p
    return ps[0] if ps else ""


def season_config(season=None):
    """赛季配置表 TavernChessConfig.json 的内容(带 mtime 缓存)。读不到返回 {}。

    这份 JSON 是 tools/season_config.py 抓官方 Config.sgs 解密出来的总表, 4 MB 左右,
    里面 ChessConf / ChessSkillConf / ChessSpellConf / ChessClientSkillConf 等全都有。
    只读不写。
    """
    for p in _season_config_paths(season):
        if not os.path.exists(p):
            continue
        try:
            mt = os.path.getmtime(p)
        except Exception:
            mt = 0
        hit = _season_json_cache.get(p)
        if hit and hit[0] == mt:
            return hit[1]
        try:
            with open(p, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
        except Exception as e:
            print(f"[csv_loader] 读赛季配置表失败 {p}: {e}")
            continue
        _season_json_cache[p] = (mt, data)
        return data
    return {}


def shop_costs(season=None):
    """商店【基础】花费(配置表 ChessGlobalConf): {"ShopRefreshCost":1, ...}。读不到返回 {}。

    用途: 推给页面端当兜底值。页面端优先读游戏 manager 的活体 getter —— 那个数会变
    (丈八蛇矛 买将3→2/刷新1→2、刘焉 买/刷新各 -1、主公技能同样会改), 只有在拿不到
    manager 时才用这里的数。所以这里只放"配置表写的基准值", 不参与任何实时判断。
    """
    cfg = season_config(season)
    rows = cfg.get("ChessGlobalConf") or []
    row = rows[0] if rows and isinstance(rows[0], dict) else {}
    out = {}
    # v4.68★ 买卡的键在配置表里叫 ChessPrice(=3), 不是 ShopBuyCost —— 原来只找 ShopBuyCost,
    #   于是"买卡基准价"永远是空的, 调用方只能各自瞎猜(脚本里写死过 2, UI 里按 1 扣)。
    #   用户报的就是这个: "正常情况下买武将要花费3虎符"。两个键都收, 取到就写进统一名字。
    for k in ("ShopRefreshCost", "ShopBuyCost"):
        v = row.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[k] = int(v)
    for k, alias in (("ChessPrice", "ShopBuyCost"), ("ChessSoldPrice", "ShopSoldPrice")):
        if alias in out:
            continue
        v = row.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[alias] = int(v)
    return out


def pick_skill_ids(season=None):
    """v4.39★ 【需要玩家手动选目标】的技能ID列表 —— 连接时推给页面端判"这张卡要不要选人"。

    为什么必须有(实机问题): 页面端原来的判据是游戏运行时的
        TavernChessConfiger.GetInstance().GetClientSkillVO(skillID)
    而 TavernChessConfiger 被关在游戏的打包闭包里 —— window/裸名/eval 全查不到。
    于是 GetClientSkillVO 恒拿不到 VO → 恒判"不需要选人" → 丁奉(330411
    「当先: 移除1个友方, 获得3虎符」)的当先被服务器吞掉(场上无卡被移除、也没拿虎符),
    日志还写着"技能不需要选人"。把这张表离线算好推下去, 判定就不再依赖游戏运行时。

    判据与游戏客户端一致: ChessSkillConf.TargetTyp1 == CHESSHTTShouDongXuanZe(=21)。
    交叉验证(当前赛季 86 个): 描述全部落在「友方 / 随征」上, 例如
        330411 丁奉  当先: 移除1个友方, 获得3虎符
        10411  糜竺  当先: 使一个友方获得+2/+2
        942311 黄巾兵 随征(短描述, 完整文案在 ChessClientKeywordConf)
    而 23(自己/上阵召唤)、10(相邻)、6(全体) 这些值是自动定位, 不需要玩家点。

    ⚠ 这个 21 是"客户端枚举的取值", 属于跨版本有可能变的东西 —— 所以末尾有一次描述
    自校验: 命中描述不像"选友方"时打警告, 让人能第一时间发现(而不是静默判错)。
    """
    rows = (season_config(season) or {}).get("ChessSkillConf") or []
    out = []
    desc = {}
    for r in rows:
        try:
            sid = int(r.get("SkillID") or 0)
        except Exception:
            continue
        if not sid:
            continue
        desc[sid] = str(r.get("SkillDesc") or "")
        try:
            if int(r.get("TargetTyp1")) == PICK_TARGET_TYP and sid not in out:
                out.append(sid)
        except Exception:
            continue
    out.sort()
    if out:
        # 自校验: 命中集合的描述应统一是"选一个友方"的语义(实测 86/86 都含『友方』或『随征』)。
        # 若比例明显偏低 → 很可能这个 21 在新版客户端里换成了别的枚举, 必须报出来,
        # 否则会静默把不需选人的卡塞进选人流程(那比"少选一次"更糟)。
        ok = sum(1 for s in out if re.search(r"友方|随征", desc.get(s, "")))
        if ok * 5 < len(out) * 4:          # <80%
            print(f"[csv_loader] 警告: 选人技能表自校验不通过({ok}/{len(out)} 条描述含"
                  f"『友方/随征』) —— 游戏可能改了 TargetTyp1 的取值, 请核对 "
                  f"PICK_TARGET_TYP={PICK_TARGET_TYP}", file=sys.stderr)
    elif rows:
        print(f"[csv_loader] 警告: 赛季配置表里没有 TargetTyp1={PICK_TARGET_TYP} 的技能 —— "
              f"『要不要选人』会退回旧行为(拿不到游戏配置表时一律判不需要选人)", file=sys.stderr)
    return out


def chess_skill_map(season=None, season_filter=False):
    """v4.32★ 【棋子 → 技能ID】映射, 连接时推给页面端当兜底解析源。

    返 {ids: {chessID(str): [技能ID…]}, names: {棋子名: [技能ID…]},
        picks: [需要手动选目标的技能ID…],          ← v4.39★ 见 pick_skill_ids()
        n: ids条数}

    为什么必须有: 实机出现过『丁奉 的技能不需要选人(或解析不出技能) | 卡上技能: (卡上没解析到
    任何技能ID)』, 而 ChessConf.csv 里明明写着 丁奉=[330411]。页面端解析技能的三个来源
    (卡实例字段 / 卡面 ChessSkill / 游戏配置表)在那次对局里全落空, 于是当先被当成"不用选人"
    → 效果被服务器吞。补第四来源 = 这份表驱动的映射。

    名字索引的坑: 同一个名字有【多赛季 + 每赛季两行】, 例如 丁奉 有 20 行 ——
      201106/s1 [2011061]、20203081/s2 [30811]、20303041/s3 [330411]…21003051/s10 [330411],
      以及每赛季一条 IsShop=0 的变体(s10 那条是 [433421])。
      按"赛季号最大"取会拿到错技能的变体行, 所以要按优先级挑:
        **当前赛季 + IsShop=1 (牌库/招募能出的那张) > IsShop=1 > 赛季号更大**。
      s10 丁奉 → 21003051 → [330411] ✓ (与玩家在牌库里看到的一致)
    ids 索引按主键存全赛季(chessID 本身全局唯一, 不会撞), 页面端优先用 ids。
    v4.46★ season_filter=True 时 ids/names2 也【只留本季】(用户要求"只推我选的那个赛季")。
      默认 False = 与 v4.32 起的行为完全一致(全赛季), 因为"全量"本身修过真问题:
        实机出现过场上 20503211(董袭) 名字解析成裸 ID —— 那份卡在武将表里没有, 靠的正是全量名表。
      所以这是个【取舍开关】而不是修 bug: 开了它 → 表更小/不会串季, 代价是
        往季变体卡(以及被滚动刷掉的旧卡)在页面上的名字/技能会退化成裸 ID。
      调用方(ui/app.py 的连接推送)会在开启时把这条代价写进日志。
    """
    data = load_from_csv()
    _sm_key = (season, season_filter)
    _sm_hit = _chess_skill_map_cache.get(_sm_key)
    if _sm_hit is not None and _sm_hit[0] is data:
        return _sm_hit[1]
    ids = {}
    best = {}
    cur = str(season if season is not None else CURRENT_SEASON)
    for c in data:
        sk = c.get("skills") or []
        if not sk:
            continue
        sea_c = str(c.get("season") or "").strip()
        if season_filter and season is not None and sea_c and sea_c != str(season):
            continue
        cid = str(c.get("chessID") or "").strip()
        if cid.isdigit():
            ids[cid] = sk
        nm = str(c.get("name") or "").strip()
        if not nm:
            continue
        sea = sea_c
        if season is not None and sea != str(season):
            continue
        try:
            sn = int(sea or 0)
        except Exception:
            sn = 0
        score = (2 if sea == cur else 0) + (1 if str(c.get("isShop") or "") == "1" else 0)
        old = best.get(nm)
        if old is None or score > old[0] or (score == old[0] and sn >= old[1]):
            best[nm] = (score, sn, sk)
    names = {k: v[2] for k, v in best.items()}
    # v4.33★ names2: ChessConf.csv 全量【chessID → 名字】(3346 条, 含各赛季变体卡)。
    #   为什么要在同一份推送里带上: 助手连接时推的武将表是"自走棋可用武将(tavern_chess.json,
    #   300+ 条)", 变体卡/新卡会漏。实机问题: 场上出现 20503211(董袭 —— CSV 里有, 武将表里没有),
    #   页面名字解析全落空 → 日志/阵容显示成裸 ID "20503211:1366@2"。带上这份全量表就再也不会漏。
    # v4.39★ 同一份推送里再带上 picks(需要手动选目标的技能ID)。为什么搭这趟车而不是单独推:
    #   它们服务于同一件事(判定"这张卡的技能是什么、要不要选人"), 而且页面端把这张表落盘在
    #   localStorage 里 —— 页面重注入/刷新后靠落盘恢复, 分两次推就会出现"技能ID恢复了、
    #   picks 没恢复"的半残状态(判定又退回"一律不需选人")。
    _sm_result = {"ids": ids, "names": names,
                  "names2": chess_names_all(season if season_filter else None),
                  "picks": pick_skill_ids(season), "n": len(ids)}
    _chess_skill_map_cache[_sm_key] = (data, _sm_result)
    return _sm_result


def chess_names_all(season=None):
    """v4.33★ 从 ChessConf.csv 取【chessID(str) → 棋子名】的全量映射。

    与 id_map.chess_name_map() 的区别: 那个走 data/chess.json(游戏里真出现过的卡才会被记进去),
    这个直接读 CSV 主表, 3346 条全在, 赛季更新后重读即最新。

    v4.46★ season 传了就只收该赛季的行(见 chess_skill_map 里 season_filter 的取舍说明)。
    赛季号【为空】的行照收 —— 那是 CSV 里字段缺失的行, 不按赛季算, 剔掉反而是丢数据。
    """
    data = load_from_csv()
    _cn_key = None if season is None else str(season)
    _cn_hit = _chess_names_all_cache.get(_cn_key)
    if _cn_hit is not None and _cn_hit[0] is data:
        return _cn_hit[1]
    out = {}
    want = None if season is None else str(season)
    try:
        for c in data:
            cid = str(c.get("chessID") or "").strip()
            nm = str(c.get("name") or "").strip()
            if not (cid.isdigit() and nm and not nm.isdigit()):
                continue
            if want is not None:
                sea = str(c.get("season") or "").strip()
                if sea and sea != want:
                    continue
            out[cid] = nm
    except Exception as e:
        print(f"[csv_loader] 全量棋子名映射生成失败: {e}")
    _chess_names_all_cache[_cn_key] = (data, out)
    return out


def get_whitelist_for_ui():
    """白名单供 UI 使用, 返回 [{id, name, display}] 格式"""
    data = load_whitelist()
    result = []
    for g in data:
        cid = g.get("chessID") or g.get("id") or ""
        name = g.get("name") or g.get("n") or ""
        if cid and name:
            result.append({"id": cid, "name": name, "display": f"{name} ({cid})"})
    result.sort(key=lambda x: x["name"])
    return result


def get_blacklist_for_ui():
    """黑名单供 UI 使用"""
    data = load_blacklist()
    result = []
    for g in data:
        cid = g.get("chessID") or g.get("id") or ""
        name = g.get("name") or g.get("n") or ""
        if cid and name:
            result.append({"id": cid, "name": name, "display": f"{name} ({cid})"})
    result.sort(key=lambda x: x["name"])
    return result


if __name__ == "__main__":
    wl = get_whitelist_from_csv()
    bl = get_blacklist_from_csv()
    print(f"白名单(当前赛季商店): {len(wl)} 个")
    print(f"黑名单(全部赛季): {len(bl)} 个")
    if wl:
        print(f"白名单样本: {wl[0]['display']}")
