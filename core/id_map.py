# -*- coding: utf-8 -*-
"""ID->名称映射库(三个命名空间分库存盘，防ID段串表):
  武将 data/id_map.json   generalID -> {n:中文名, h:头像, src}
  卡牌 data/cards.json    cardID/skillID -> {n:名称}   (jnd:spell-catalog，杀/闪/锦囊/技能)
  皮肤 data/skins.json    itemID/skinID -> {n:中文名}  ("经典形象*糜竺"提取)

来源: 油猴探针(id_map指令) v2.9.0+。实时战况用 name_of 查武将名。
"""
import difflib
import json
import os
import threading
import time
from core import silence as _SIL

try:
    from .config import DATA_DIR
except ImportError:   # 直接运行本文件
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from core.config import DATA_DIR

_PATHS = {
    "gen": os.path.join(DATA_DIR, "id_map.json"),        # 自走棋棋子 + 身份武将(通用)
    "cards": os.path.join(DATA_DIR, "cards.json"),        # 卡牌/技能/锦囊
    "skins": os.path.join(DATA_DIR, "skins.json"),        # 皮肤
    "lords": os.path.join(DATA_DIR, "lords.json"),        # 自走棋主公(独立命名空间,防与GeneralCards重叠)
    "chess": os.path.join(DATA_DIR, "chess.json"),        # 自走棋棋子详情(6位ChessID)
    "lord_skills": os.path.join(DATA_DIR, "lord_skills.json"),  # 主公技能 SkillID->{n, desc}
    "learned": os.path.join(DATA_DIR, "discovered_ids.json"),  # v3.85★ 累积学习库
    "tavern_chess": os.path.join(DATA_DIR, "tavern_chess.json"), # v3.85★ 自走棋专属棋子
    "tavern_lords": os.path.join(DATA_DIR, "tavern_lords.json"), # v3.85★ 自走棋专属主公
    "tavern_spells": os.path.join(DATA_DIR, "tavern_spells.json"), # v3.85★ 自走棋专属锦囊
    "skills": os.path.join(DATA_DIR, "skills.json"),             # v4.00★ 独立技能库
}
_lock = threading.RLock()   # 可重入: learn_faces持锁期间会调_load
_caches = {}

# v3.55★ 武将元数据(费用/势力): data/chess_meta.json
#   结构 {"by_id": {"21001011": {"name","rank","faction"}}, "by_name": {...}}
#   整表 372 个 ID / 186 个武将(每个武将两个 ID, ...1 和 ...2)。
#   用途: 给 UI 的武将池补上 rank(费用)/faction(势力), 使 DualListPanel 能按
#   「势力 / 费用」筛选, 从而支持"整个魏国一次加进白名单"这类批量操作。
#   文件缺失时一切照常, 只是筛选下拉框里没有可选项。
_meta_cache = None

# v4.148★ 自走棋【装备】ID -> 名字: data/season/s11/TavernChessConfig.json 里的 ChessWeaponConf。
#   结构: [{"WeaponID": 4111008, "WeaponName": "乌云踏雪", "WeaponType": 3,
#           "WeaponSelectRound": 3, ...}, ...]
#   WeaponSelectRound = 第几回合给(实测 3/7/11), 与《探查记录_自走棋.md》"第 3/7/11 回合
#   各一件"完全对得上 —— 所以这份表可信。
#   ★ 关键区别: 装备 ID 是【配置表固定号】, 不像 goodsID 是每局重排的运行时实例号,
#     所以它能【离线回查】—— 旧日志也能补名字, 不用重打一局去验证。
_weapon_cache = None


def equip_name_of(eid):
    """装备(武器)ID -> 中文名。查不到 / 传进来的已经是名字时返回 ''。"""
    global _weapon_cache
    try:
        eid = int(eid)
    except Exception:
        return ""          # 已经是中文名了, 让调用方保留原值
    if not eid:
        return ""
    with _lock:
        if _weapon_cache is None:
            m = {}
            for sub in ("s11", "s10"):
                fp = os.path.join(DATA_DIR, "season", sub, "TavernChessConfig.json")
                if not os.path.exists(fp):
                    continue
                try:
                    cfg = json.load(open(fp, encoding="utf-8", errors="replace"))
                except Exception:
                    continue
                for it in (cfg.get("ChessWeaponConf") or []):
                    if not isinstance(it, dict):
                        continue
                    wid, nm = it.get("WeaponID"), str(it.get("WeaponName") or "").strip()
                    if wid and nm:
                        try:
                            m.setdefault(int(wid), nm)   # s11 优先, s10 只补空缺
                        except Exception:
                            pass
            _weapon_cache = m
        return _weapon_cache.get(eid, "")


# v4.150★ 自走棋【技能】SkillID -> 名字: 同一份配置表里的 ChessSkillConf(2301 条)。
#   结构: [{"SkillID": 10161, "SkillName": "白起", "SkillDesc": "...", ...}, ...]
_skill_cache = None


def skill_name_of(sid):
    """技能 ID -> 中文名。查不到 / 传进来的已经是名字时返回 ''。"""
    global _skill_cache
    try:
        sid = int(sid)
    except Exception:
        return ""
    if not sid:
        return ""
    with _lock:
        if _skill_cache is None:
            m = {}
            for sub in ("s11", "s10"):
                fp = os.path.join(DATA_DIR, "season", sub, "TavernChessConfig.json")
                if not os.path.exists(fp):
                    continue
                try:
                    cfg = json.load(open(fp, encoding="utf-8", errors="replace"))
                except Exception:
                    continue
                for it in (cfg.get("ChessSkillConf") or []):
                    if not isinstance(it, dict):
                        continue
                    k, nm = it.get("SkillID"), str(it.get("SkillName") or "").strip()
                    if k and nm:
                        try:
                            m.setdefault(int(k), nm)
                        except Exception:
                            pass
            _skill_cache = m
        return _skill_cache.get(sid, "")


def weapon_name_map():
    """v4.150★ 装备 WeaponID -> 名称 全表, 推给页面端 window.__sgsWeaponNameMap。

    为什么必须推: 3选1 的【装备档】里, 页面端 SgsSelect3._one() 只认 chessID / spellID,
    装备候选一律判成 type='?' 且名字落空 —— 界面上只剩裸 ID, 按名字挑也无从下手。
    装备 ID 是配置表固定号(见 equip_name_of), 离线就能查全, 推过去即可。
    """
    with _lock:
        if _weapon_cache is None:
            equip_name_of(1)      # 触发建表(带锁 + 幂等); 传 0 会提前 return
        return {str(k): v for k, v in (_weapon_cache or {}).items()}


def equip_table():
    """v4.164★ 装备 ID -> {名字, 大类} 表, 推给页面端 window.__sgsEquipTable。

    为什么必须有: 剧本要"按装备判定"(有玉玺/有吴六剑)和"选装备按类型筛"(避开武器/优先玉玺七星灯),
    都得先知道每个 equipmentID 是哪个大类(1武器 2宝具 3坐骑)。这张表由
    tools/build_equip_table.py 离线从赛季配置生成(data/equip_table.json), 不依赖真机。
    """
    fp = os.path.join(DATA_DIR, "equip_table.json")
    if not os.path.exists(fp):
        return {"season": 0, "types": {"1": "武器", "2": "宝具", "3": "坐骑"}, "items": {}}
    try:
        return json.load(open(fp, encoding="utf-8", errors="replace"))
    except Exception:
        return {"season": 0, "types": {"1": "武器", "2": "宝具", "3": "坐骑"}, "items": {}}


def skill_name_map():
    """v4.150★ 技能 SkillID -> 名称 全表, 推给页面端 window.__sgsSkillNameMap。

    同上: 3选1 / 日志里出现技能 ID 时, 页面端没有名字表就只能显示裸号。
    """
    with _lock:
        if _skill_cache is None:
            skill_name_of(1)
        return {str(k): v for k, v in (_skill_cache or {}).items()}


# v4.148★ 自走棋【锦囊】SpellID -> 名字: 同一份配置表里的 ChessSpellConf(931 条)。
#   结构: [{"SpellID": 311053, "SpellName": "桃", "SpellTextRank": "二", ...}, ...]
#   ★ 和装备一样是【配置表固定号】, 能离线回查 —— 所以操作参数里那些 311xxx 的
#     锦囊号(挑锦囊/用锦囊)不必等下一局, 现在就能补出名字。
_spell_cache = None


def spell_name_of(sid):
    """锦囊 SpellID -> 中文名。查不到 / 传进来的已经是名字时返回 ''。"""
    global _spell_cache
    try:
        sid = int(sid)
    except Exception:
        return ""
    if not sid:
        return ""
    with _lock:
        if _spell_cache is None:
            m = {}
            for sub in ("s11", "s10"):
                fp = os.path.join(DATA_DIR, "season", sub, "TavernChessConfig.json")
                if not os.path.exists(fp):
                    continue
                try:
                    cfg = json.load(open(fp, encoding="utf-8", errors="replace"))
                except Exception:
                    continue
                for it in (cfg.get("ChessSpellConf") or []):
                    if not isinstance(it, dict):
                        continue
                    s, nm = it.get("SpellID"), str(it.get("SpellName") or "").strip()
                    if s and nm:
                        try:
                            m.setdefault(int(s), nm)
                        except Exception:
                            pass
            _spell_cache = m
        return _spell_cache.get(sid, "")


def get_chess_meta():
    """加载 chess_meta.json 的 by_id 索引 (id -> {name, rank, faction}); 失败返回 {}"""
    global _meta_cache
    if _meta_cache is None:
        try:
            with open(os.path.join(DATA_DIR, "chess_meta.json"), "r", encoding="utf-8") as f:
                _meta_cache = (json.load(f) or {}).get("by_id") or {}
        except Exception:
            _meta_cache = {}
    return _meta_cache


_SEASON_POOL_META = None


def season_pool_meta():
    """本季官方表 → {chessID: {"rank": int, "faction": 势力名}}

    v4.153★ 为什么要这一层(2026-10-24 实测): UI 武将池的 rank/faction 原先【只】来自
      data/chess_meta.json, 而那份是 S10 的静态快照 —— by_id 372 条全是 210… 开头,
      本季 211… 一条都没有。实测 get_all_tavern_generals_for_ui() 返的 3718 条里,
      有势力的恰好只有那 372 条(全是上季卡), 本季 372 条势力【全空】。

      后果不止"势力下拉里没有本季": DualListPanel 按势力/费用筛选、以及新加的
      「某势力除X外全遣散」预设, 对本季卡一律筛出空列表 —— 界面上就是"点了没反应",
      没有任何报错。这里用本季官方表补齐, 数据源与 _rank_from_season_pool 同源。

      ★ 表的结构坑(与 _rank_from_season_pool 同一条): 金卡/进阶变体【不在池里做独立键】,
        只作为某 base 的 upgrade_id(实测 21101011.upgrade_id == '21101012', 池里只有前者)。
        所以这里把 upgrade_id 也建成键, 指向同份 rank/faction —— 同棋子同费用同势力。

      换季后由 reset_season_prefix() 清缓存, 不要手动改这个全局。
    """
    global _SEASON_POOL_META
    if _SEASON_POOL_META is None:
        out = {}
        ups = []
        try:
            from core import tavern_kb as _KB
            _no = 0
            try:
                from core import csv_loader as _CL
                _no = int(str(getattr(_CL, "CURRENT_SEASON", "") or "0").strip())
            except (TypeError, ValueError):
                _no = 0
            pool = _KB.load_chess("s%d" % _no) if 1 <= _no <= 99 else (_KB.load_chess() or {})
            for cid, v in (pool or {}).items():
                if not isinstance(v, dict):
                    continue
                rec = {"rank": _int_or(v.get("rank"), 0),
                       "faction": str(v.get("faction_name") or "").strip()}
                cid = str(cid)
                out[cid] = rec
                uid = str(v.get("upgrade_id") or "").strip()
                if uid:
                    ups.append((uid, rec))
            for uid, rec in ups:
                out.setdefault(uid, rec)
            # v4.160★ 补洞: load_chess(shop_only=True) 会漏掉【展示/占位卡】(如董袭,
            #   IsShop=0 且技能为空), 这些卡的 chess_cost_of 会返回 None → 费用筛选/排序
            #   对它们静默失效。本季卡池表 tavern_chess_s<季>.json 是全量 372 条, 用它
            #   回填缺口(同 ID 已存在的不覆盖, 只补缺失项)。
            try:
                if 1 <= _no <= 99:
                    import json as _json
                    _pp = os.path.join(DATA_DIR, "tavern_chess_s%d.json" % _no)
                    if os.path.exists(_pp):
                        with open(_pp, "r", encoding="utf-8") as _f:
                            _full = _json.load(_f) or {}
                        for _c, _v in (_full.items() if isinstance(_full, dict) else []):
                            if not isinstance(_v, dict) or str(_c) in out:
                                continue
                            out[str(_c)] = {
                                "rank": _int_or(_v.get("rank"), 0),
                                "faction": str(_v.get("faction") or "").strip(),
                            }
            except Exception as e:
                print(f"[id_map] 本季卡池全量回填失败(忽略): {e}")
        except Exception as e:
            print(f"[id_map] 本季表(费用/势力)读取失败: {e}")
        _SEASON_POOL_META = out
    return _SEASON_POOL_META


def _int_or(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _enrich_generals(items):
    """给 [{id,name,display}] 就地补上 rank(费用)/faction(势力), 供 UI 筛选使用

    v4.153★ 增加本季官方表兜底: chess_meta.json 是上季快照, 换季后本季 ID 在上面全落空,
      势力/费用筛选对本季卡静默失效(详见 season_pool_meta 注释)。
      取值顺序 chess_meta → 本季官方表, 已有的结果一个字都不改。
    """
    meta = get_chess_meta() or {}
    pool = season_pool_meta() or {}
    for it in items or []:
        gid = str(it.get("id", ""))
        info = meta.get(gid) or {}
        pre = pool.get(gid) or {}
        rank = info.get("rank") or pre.get("rank")
        faction = info.get("faction") or pre.get("faction") or ""
        if rank:
            it["rank"] = rank
        if faction:
            it["faction"] = faction
    return items


def get_skill_desc(skill_id):
    """v4.00★ 通过技能ID从技能库(skills.json)中获取描述"""
    if not skill_id:
        return ""
    
    sid_s = str(skill_id)
    skills = _load("skills")
    
    # 优先查 skills.json (原 tavern_lords.json)
    if sid_s in skills:
        info = skills[sid_s]
        if isinstance(info, dict):
            return info.get("desc") or info.get("describe") or ""
        return str(info)
        
    # 兜底查 lord_skills.json
    lord_skills = _load("lord_skills")
    if sid_s in lord_skills:
        info = lord_skills[sid_s]
        if isinstance(info, dict):
            return info.get("desc") or info.get("describe") or ""
        return str(info)
        
    # 再兜底查 cards.json
    cards = _load("cards")
    if sid_s in cards:
        info = cards[sid_s]
        if isinstance(info, dict):
            return info.get("desc") or info.get("describe") or info.get("n") or ""
        return str(info)
        
    return ""

def get_chess_skills_desc(skill_ids):
    """获取武将的多个技能描述拼接串"""
    if not skill_ids:
        return ""
    if isinstance(skill_ids, (int, str)):
        skill_ids = [skill_ids]
    
    descs = []
    for sid in skill_ids:
        d = get_skill_desc(sid)
        if d:
            descs.append(d)
    
    return " | ".join(descs)

def _clean_name(name):
    if not name: return ""
    # v3.97★ 极致纯净化：移除赛季、星级、合金后缀及各种括号噪音
    import re as _re
    name = str(name)
    
    # 0. 如果名字本身就是 "ID(名字)" 格式，提取名字部分
    if "(" in name and name.endswith(")"):
        name = name.split("(")[-1].rstrip(")")
    
    # 1. 移除赛季标记 (S10, S2100, S2000等)
    name = _re.sub(r'^[sS]\d+·?', '', name)
    name = _re.sub(r'^[sS]·?', '', name)
    
    # 2. 移除星级前缀 (如 1星·, ·1星, 三星·)
    name = _re.sub(r'^\d+星·?', '', name)
    name = _re.sub(r'^[一二三四五六七八九十]星·?', '', name)
    name = _re.sub(r'·[0-9一二三四五六七八九十壹贰叁肆伍陆柒捌玖拾]+星?$', '', name)
    
    # 3. 移除合金后缀
    name = name.replace("合金", "").replace("·合金", "")
    
    # 4. 移除武将名字中的 (技) 标记
    name = name.replace("(技)", "")
    
    # 5. 移除 S10/S2100 等特有的 ID 前缀残留 (如果名字里还带着 ID)
    name = _re.sub(r'^[sS]?\d{4,8}', '', name)
    
    # 6. v3.97★ 进一步纯净化，移除任何 "S" 打头的赛季标记和 "星" 字样
    name = _re.sub(r'[sS]\d+', '', name)
    name = _re.sub(r'\d+星', '', name)
    
    # 7. 移除 "·" 及其后面的所有噪音
    if "·" in name:
        # 如果剩下的是 "张星彩·合金"，只取 "张星彩"
        parts = name.split("·")
        if len(parts) > 1:
            # 检查第一部分是否为有效名字
            if len(parts[0]) >= 2:
                name = parts[0]
            else:
                # 可能是 "S10·张星彩"，取第二部分
                name = parts[1]
        
    # 8. 移除括号内容 (如 "张星彩(自走棋)")
    name = _re.sub(r'\(.*?\)', '', name)
    
    # 9. 移除特定的后缀噪音
    for noise in ["已选", "招募", "已拥有"]:
        name = name.replace(noise, "")
    
    return name.strip()


def _load(ns):
    with _lock:
        if ns not in _caches:
            try:
                with open(_PATHS[ns], "r", encoding="utf-8") as f:
                    _caches[ns] = json.load(f)
            except Exception:
                _caches[ns] = {}
        return _caches[ns]








def learn_ids(id_dict):
    """v3.85★ 累积学习新发现的 ID，保存到 discovered_ids.json。
    id_dict = { gid: {n, ns, src, ...} }
    """
    if not id_dict:
        return 0
    
    with _lock:
        store = dict(_load("learned"))
        added = 0
        updated = 0
        
        for gid, info in id_dict.items():
            gid_s = str(gid)
            if not isinstance(info, dict):
                continue
            
            # 基础信息补全
            name = str(info.get("n") or info.get("name") or "").strip()
            if not name:
                # 尝试从 lookup 找名字，如果已经知道名字就不覆盖
                name = name_of(gid_s)
                if name.startswith("?"): name = ""
            
            entry = dict(info)
            if name: entry["n"] = name
            
            if gid_s not in store:
                store[gid_s] = entry
                added += 1
            else:
                # 增量更新字段
                old_n = store[gid_s].get("n")
                store[gid_s].update(entry)
                if old_n != store[gid_s].get("n"):
                    updated += 1
        
        if added > 0 or updated > 0:
            try:
                with open(_PATHS["learned"], "w", encoding="utf-8") as f:
                    json.dump(store, f, ensure_ascii=False, indent=1)
                _caches["learned"] = store
                # 同时触发 TXT 导出
                export_discovered_ids()
            except Exception as e:
                # v4.22★ 原来是裸 `pass`: discovered_ids.json(实测已涨到 2.2MB)写入失败时,
                #   累积学习成果静默丢失、界面无任何提示, 出问题完全没线索。
                #   这里只补提示, 不改控制流(调用方不关心磁盘异常)。
                import sys as _sys
                print(f"[ID库] 写入 {_PATHS['learned']} 失败, 本次学习结果未落盘: "
                      f"{e.__class__.__name__}: {e}", file=_sys.stderr)
    return added


def merge(tables):
    """v4.73★ 合并诊断上报自动抓到的 ID 映射表(bridge.py /push 的 diagnostic.idmapTables)。

    背景: bridge.py 里 `diag["idmapAdded"] = _idmap.merge(imt)` 从 v3.x 写好起就一直
    抛 AttributeError("module 'core.id_map' has no attribute 'merge'"), 只是外层
    try/except 把错误吞成了 {"err": ...} —— 游戏页【诊断上报】的"自动抓ID映射入库"
    实际上从来没生效过。本函数补上这个缺口。

    tables 是油猴端 probeIdMaps(compact) 抓到的表列表, 每项形如:
        {"src": "来源描述", "cnt": 条数, "map": {id: {"n": 名字, "h": 头像}}}
        或 {"src": "...", "cnt": n, "genMap": {...}}   (武将/主公表, 见 addTable isGen)
    返回 {"added": 新增条数, "tables": 收了几个表, "total": 参与合并的总条数}。
    """
    if not isinstance(tables, list) or not tables:
        return {"added": 0, "tables": 0, "total": 0}
    id_dict = {}
    used = 0
    for t in tables:
        if not isinstance(t, dict):
            continue
        m = t.get("map") if isinstance(t.get("map"), dict) else None
        if m is None:
            m = t.get("genMap") if isinstance(t.get("genMap"), dict) else None
        if not m:
            continue
        used += 1
        src = str(t.get("src") or "diag")[:60]
        for gid, info in m.items():
            if not isinstance(info, dict):
                continue
            name = str(info.get("n") or info.get("name") or "").strip()
            if not name:
                continue
            entry = {"n": name[:24], "src": "diag:" + src}
            h = info.get("h")
            if isinstance(h, str) and h:
                entry["h"] = h[-70:]
            # 同一张表内后写的优先; 不同表间先到先得(learn_ids 只增量更新字段)
            id_dict[str(gid)[:14]] = entry
    added = learn_ids(id_dict) if id_dict else 0
    return {"added": added, "tables": used, "total": len(id_dict)}


def export_discovered_ids(stores=None):
    """将所有库中的 ID 汇总导出，方便用户查看。
    v3.96★ 优化 S10 ID 解析逻辑: 过滤垃圾数据，分类清晰
    v3.98★ 引入百科全书 (generals.json) 自动富化
    """
    if stores is None:
        stores = {ns: _load(ns) for ns in _PATHS}
    
    # 加载百科全书
    encyclopedia = {}
    try:
        gen_path = os.path.join(DATA_DIR, "generals.json")
        if os.path.exists(gen_path):
            with open(gen_path, "r", encoding="utf-8") as f:
                gen_list = json.load(f)
                if isinstance(gen_list, list):
                    for g in gen_list:
                        name = g.get("name")
                        if name: encyclopedia[str(name)] = g
    except Exception: _SIL.note("core/id_map.py:307", "gen_path = os.path.join(DATA_DIR, 'generals.js")

    # 汇总所有 ID
    all_data = {}
    for ns, data in stores.items():
        if not isinstance(data, dict): continue
        for gid, info in data.items():
            if not isinstance(info, dict): continue
            gid_s = str(gid)
            
            # 过滤垃圾 ID
            if len(gid_s) < 2 and not gid_s.isdigit(): continue
            if gid_s in (".", "[", "]", "_", "?"): continue
            
            # 修正 1019
            if gid_s == "1019":
                name = "李广"
            else:
                name = _clean_name(info.get("n") or info.get("name") or "?")
            
            # 过滤名字里的垃圾
            if name in ("?", "", "undefined", "null"): continue
            
            # 记录基础信息
            all_data[gid_s] = {
                "name": name,
                "ns": ns,
                "src": info.get("src") or ns
            }
            
            # v3.98★ 从百科全书富化
            if name in encyclopedia:
                gen_info = encyclopedia[name]
                all_data[gid_s].update({
                    "cost": gen_info.get("cost"),
                    "faction": gen_info.get("faction"),
                    "effect": gen_info.get("effect"),
                    "tags": gen_info.get("tags"),
                    "atk": gen_info.get("atk"),
                    "hp": gen_info.get("hp")
                })
            
            # 尝试解析 S10 复合 ID
            if gid_s.isdigit() and len(gid_s) >= 7 and (gid_s.startswith("2100") or gid_s.startswith("2000")):
                all_data[gid_s]["season"] = gid_s[:4]
                all_data[gid_s]["star"] = gid_s[-1]
                all_data[gid_s]["base_id"] = gid_s[4:-1]
                # 标记 S10 赛季
                all_data[gid_s]["is_s10"] = True
                # 修正季节显示 (S10)
                if gid_s.startswith("2100"): all_data[gid_s]["season_display"] = "S10"

    # 1. 导出 discovered_ids.json (完整数据)
    dp_json = os.path.join(DATA_DIR, "discovered_ids.json")
    try:
        with open(dp_json, "w", encoding="utf-8") as f:
            json.dump(all_data, f, ensure_ascii=False, indent=2)
    except Exception: _SIL.note("core/id_map.py:364", "with open(dp_json, 'w', encoding='utf-8') as f")

    # 2. 导出 discovered_ids_picker.json (用户可直接参考的 hero_picker 格式)
    dp_picker = os.path.join(DATA_DIR, "discovered_ids_picker.json")
    hero_picker_path = os.path.join(DATA_DIR, "hero_picker.json")
    try:
        picker_data = {
            "version": "S10-Learning",
            "updated_at": time.strftime('%Y-%m-%d %H:%M:%S'),
            "whitelist": [],
            "hero_info": {}
        }
        
        # 排序 ID
        sorted_keys = sorted(all_data.keys(), key=lambda x: (len(x), x))
        for k in sorted_keys:
            it = all_data[k]
            # 仅导出武将/卡牌 ID (数字形式)
            if k.isdigit() or k.startswith("spell_"):
                picker_data["whitelist"].append(k)
                picker_data["hero_info"][k] = it["name"]
        
        with open(dp_picker, "w", encoding="utf-8") as f:
            json.dump(picker_data, f, ensure_ascii=False, indent=2)
            
        # v3.96★ 自动同步到 hero_picker.json
        if os.path.exists(hero_picker_path):
            try:
                with open(hero_picker_path, "r", encoding="utf-8") as f:
                    hp_data = json.load(f)
                
                # 合并 whitelist
                old_wl = set(hp_data.get("whitelist") or [])
                new_wl = set(picker_data["whitelist"])
                hp_data["whitelist"] = sorted(list(old_wl | new_wl), key=lambda x: (len(x), x))
                
                # 合并 hero_info
                old_hi = hp_data.get("hero_info") or {}
                old_hi.update(picker_data["hero_info"])
                hp_data["hero_info"] = old_hi
                
                with open(hero_picker_path, "w", encoding="utf-8") as f:
                    json.dump(hp_data, f, ensure_ascii=False, indent=2)
            except Exception: _SIL.note("core/id_map.py:407", "with open(hero_picker_path, 'r', encoding='utf")
            
    except Exception: _SIL.note("core/id_map.py:409", "picker_data = { 'version': 'S10-Learning', 'up")

    # 3. 导出 discovered_ids.txt (极致纯净格式)
    dp_txt = os.path.join(DATA_DIR, "discovered_ids.txt")
    try:
        lines = [
            "// ============================================================",
            "// 助手自动发现的 ID 列表 (S10 赛季适配版)",
            f"// 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            "// ============================================================\n",
            "// [ID]         | [名称]       | [赛季/星级]     | [库/来源]",
            "// ------------------------------------------------------------"
        ]
        
        # 分类显示
        groups = {"lords": [], "chess": [], "cards": [], "gen": [], "learned": [], "other": []}
        for k, v in all_data.items():
            ns = v.get("ns", "other")
            if ns not in groups: groups["other"].append((k, v))
            else: groups[ns].append((k, v))
            
        for ns in ["lords", "learned", "chess", "cards", "gen", "other"]:
            if not groups[ns]: continue
            lines.append(f"\n// --- {ns.upper()} 库 ---")
            items = sorted(groups[ns], key=lambda x: (len(x[0]), x[0]))
            for k, v in items:
                note = ""
                if "star" in v: 
                    season = v.get("season_display") or v.get("season", "S?")
                    note = f"{season} | {v['star']}星"
                else:
                    note = "-"
                lines.append(f"{k.ljust(12)} | {v['name'].ljust(10)} | {note.ljust(12)} | {v['ns']}")
                
        with open(dp_txt, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except Exception: _SIL.note("core/id_map.py:445", "lines = [ '// ================================")


def name_of(gid, *, lord_hint=False, chess_hint=False):
    """武将ID -> 中文名（无映射返回 "?{id}" 兜底）。
    lord_hint=True: 优先查主公库(lords)
    chess_hint=True: 优先查棋子库(chess)
    """
    if gid is None:
        return "?"
    s = str(gid)

    # v3.96★ 基础查找函数
    def _lookup(target_id, is_lord_hint=False):
        if not target_id: return ""
        tid_s = str(target_id)

        # v4.68★ 主公段(4位ID)必须【本季配置表优先】—— 修的是用户报的
        #   "对局复盘 tab 里主公ID不对": 学习库(discovered_ids.json, 2.17MB)里有一条
        #   `1037 -> 马邈`(来源 dom:scan, 是往季/误扫的), 而它在下面第 1 步是【绝对优先】,
        #   于是本季(S10)真正的 `1037 = 项羽` 永远显示不出来。同一批数据里 1019 还同时
        #   显示过"李广/孙茹/(空)"—— 旧记录把当时查到的名字烤进了文件, 换赛季后全成了错的。
        #   这里的口径: 主公段先问 lords.json(由 tools/season_config.py 从本季
        #   ChessGeneralConf 生成 = 权威), 再问 tavern_lords(旧表, 仅当本季表没有时兜底),
        #   最后才轮到学习库。其它命名空间(棋子/锦囊/技能)保持"学习库优先"不变。
        is_lord_id = bool(is_lord_hint or (tid_s.isdigit() and len(tid_s) == 4))
        if is_lord_id:
            e = _load("lords").get(tid_s)
            if e and e.get("n"): return _clean_name(e["n"])
            e = _load("tavern_lords").get(tid_s)
            if e and e.get("n"): return _clean_name(e["n"])

        # 1. v3.96★ 累积学习库 (learned): 对【非主公段】仍是绝对优先以覆盖陈旧配置
        tid_no_spell = tid_s.replace("spell_", "")
        spell_keys = [tid_s]
        if tid_s.startswith("spell_"): spell_keys.append(tid_no_spell)
        else: spell_keys.append("spell_" + tid_s)
        
        learned = _load("learned")
        for tk in spell_keys:
            e = learned.get(tk)
            if e:
                # 尝试从各种可能字段提取名字
                name = str(e.get("n") or e.get("name") or e.get("heroName") or "").strip()
                if name: return _clean_name(name)

        # 2. 查主公库 (主公库优先级高于棋子库，防止 ID 重叠)
        if is_lord_id:
            e = _load("tavern_lords").get(tid_s)
            if e and e.get("n"): return _clean_name(e["n"])
            e = _load("lords").get(tid_s)
            if e and e.get("n"): return _clean_name(e["n"])

        # 3. 查自走棋专属锦囊 (tavern_spells)
        for tk in spell_keys:
            e = _load("tavern_spells").get(tk)
            if e and e.get("n"): return _clean_name(e["n"])
            
        # 4. 优先棋子 (tavern_chess > chess)
        e = _load("tavern_chess").get(tid_s)
        if e and e.get("n"): return _clean_name(e["n"])
        e = _load("chess").get(tid_s)
        if e and e.get("n"): return _clean_name(e["n"])

        # 5. 查卡牌库 (cards)
        for tk in spell_keys:
            e = _load("cards").get(tk)
            if e and e.get("n"): return _clean_name(e["n"])
        
        # 6. 查通用武将库 (gen)
        e = _load("gen").get(tid_s)
        if e and e.get("n"): return _clean_name(e["n"])

        # 7. 历史遗留路由(降级为【最后兜底】, v4.68★):
        #   1019 在旧赛季是"李广", 但实测同一 ID 在抓到的记录里还被当成过"孙茹",
        #   而本季 ChessGeneralConf 里根本没有 1019 —— 也就是说这个号【语义不确定】。
        #   原来它被放在第 0 步"绝对优先", 于是任何来源(甚至本季配置表)都改不动它,
        #   用户看到的主公名就永远是旧赛季那一个。现在只在所有来源都查不到时才用它,
        #   并保留 ?{id} 之外的这层兜底(总比显示问号强)。
        if tid_s == "1019": return "李广"

        return ""

    # 先试原始 ID (解决 S10 8位 ID 直接在库中的情况)
    res = _lookup(s, lord_hint)
    if res: return res

    # v3.96★ S10 复合 ID 智能解析 (最后一位为星级)
    # 例如 21002021 -> 2100202 + 1 (1星)
    if len(s) >= 7 and (s.startswith("2100") or s.startswith("2000")):
        # 1. 尝试剥离最后一位星级进行查找
        s_no_star = s[:-1]
        res = _lookup(s_no_star, lord_hint)
        if res: return res
        
        # 2. 尝试提取中间的核心编号部分 (例如 21002021 -> 202)
        if len(s) == 8:
            core_id = s[4:7]  # "202"
            base_idx = s[5:7] # "02"
            # 尝试各种可能的组合
            for tid in [core_id, "1" + base_idx, "2" + base_idx, "3" + base_idx]:
                res = _lookup(tid, lord_hint)
                if res: return res

    # v3.96★ 锦囊 ID 归一化判定 (扩展范围至 30-69)
    # 判定规则: 6位ID且30-69开头，或者是88/98/99开头
    is_likely_spell = False
    if s.isdigit() and len(s) == 6:
        prefix = int(s[:2])
        if 30 <= prefix <= 69 or prefix in (88, 98, 99):
            is_likely_spell = True
            
    if s.startswith("spell_") or is_likely_spell:
        search_id = "spell_" + s if not s.startswith("spell_") else s
        res = _lookup(search_id, lord_hint)
        if res: return res

    # ★兜底: 找不到名字时返回 ?{id}
    if s and s != "0":
        return f"?{s}"
    return ""




def lord_skill_of(skill_id):
    """主公技能ID -> {name, desc}"""
    if skill_id is None:
        return None
    
    # v3.53★ 丁奉/金丁奉 技能特殊映射 (实锤: 移除目标获虎符)
    s = str(skill_id)
    # 丁奉 SkillID 映射 (常规: 103201/203201 或 丁奉ID相关的)
    if s in ("103201", "1032", "dangxian_normal"):
        return {"name": "当先", "desc": "移除一个目标获得3虎符"}
    if s in ("203201", "2032", "dangxian_gold"):
        return {"name": "金当先", "desc": "移除一个目标获得6虎符"}

    e = _load("lord_skills").get(s)
    if not e:
        # 兜底: 检查 cards 库里是否存了 (锦囊或技能有时在 cards)
        e = _load("cards").get(s)
    
    if not e:
        return None
    return {"name": e.get("n", ""), "desc": e.get("desc", "")}


def chess_info_of(chess_id_6):
    """6位棋子ID -> {n, atk, def, faction, desc} 详情（从ChessConf库）"""
    if chess_id_6 is None:
        return None
    e = _load("chess").get(str(chess_id_6))
    if not e:
        return None
    return {"name": e.get("n", ""), "atk": e.get("atk"), "def_": e.get("def"),
            "faction": e.get("faction"), "skill": e.get("skill"), "desc": e.get("desc", "")}


def lord_info_of(general_id):
    """主公GeneralID -> {name, skill_id, hp, desc} 详情"""
    if general_id is None:
        return None
    e = _load("lords").get(str(general_id))
    if not e:
        return None
    return {"name": e.get("n", ""), "skill_id": e.get("skill_id"),
            "hp": e.get("hp"), "faction_req": e.get("faction_req"), "desc": e.get("desc", "")}


def learn_faces(faces, shop, my_general_id=""):
    """卡面名字自动学习(实锤: 卡面UI有nameTxt/atkTxt/hpTxt, 协议shopGoods有attack/hp):
    UI卡面(名字,攻,血) ↔ 协议商店条目(attack,hp)按属性配对 → chessID→中文名。
    主公头像节点(GeneralHeadInfoView)直接用当前 generalID 学习。
    返回 {chessID: name} 本次学到的映射。"""
    learned = {}
    with _lock:
        store = dict(_load("gen"))
        for f in faces or []:
            if not isinstance(f, dict):
                continue
            nm = str(f.get("name") or "").strip()
            cls = str(f.get("cls") or "")
            if not nm or len(nm) > 8:
                continue
            if not any("\u4e00" <= ch <= "\u9fff" for ch in nm):
                continue   # 非中文名(数字/乱码)不学
            if "GeneralHeadInfoView" in cls and my_general_id:
                gid = str(my_general_id)
                if gid.isdigit() and (gid not in store or store[gid].get("src") == "face:auto"):
                    store[gid] = {"n": nm, "src": "face:lord"}
                    learned[gid] = nm
                continue
            atk, hp = str(f.get("atk", "")).strip(), str(f.get("hp", "")).strip()
            if not (atk.isdigit() and hp.isdigit()):
                continue
            for g in shop or []:
                if not isinstance(g, dict):
                    continue
                cid = str(g.get("chessID") or "")
                if not cid.isdigit() or cid in store:
                    continue
                if (str(g.get("attack")) == atk and str(g.get("hp")) == hp) or \
                   (str(g.get("totalAttack")) == atk and str(g.get("totalHp")) == hp):
                    store[cid] = {"n": nm, "src": "face:auto"}
                    learned[cid] = nm
                    break
        if learned:
            try:
                with open(_PATHS["gen"], "w", encoding="utf-8") as fo:
                    json.dump(store, fo, ensure_ascii=False, indent=1)
                _caches["gen"] = store
            except Exception:
                _SIL.note("core/id_map.py:658", "with open(_PATHS['gen'], 'w', encoding='utf-8'")
    return learned


def card_name_of(cid):
    return ((_load("cards").get(str(cid)) or {}).get("n", ""))


# ---------- 棋子配置查询(ChessConf: n/atk/def/faction/rank/upgrade_id) ----------
_rev_upgrade_cache = None


def _rev_upgrade_map():
    """upgrade_id反向索引: 升级后ID -> 升级前ID。
    实锤ChessConf升级链: 1星201101 --upgrade_id--> 201201(2星) --upgrade_id--> 3星"""
    global _rev_upgrade_cache
    if _rev_upgrade_cache is None:
        rev = {}
        for k, v in _load("chess").items():
            up = str((v or {}).get("upgrade_id") or "")
            if up.isdigit():
                rev.setdefault(up, k)
        _rev_upgrade_cache = rev
    return _rev_upgrade_cache


def chess_star_of(cid, attack=None):
    """棋子星级(1/2/3)。
    v3.88★ 增加对 8 位或 6 位复合 ID 的支持 (倒数第 4 位是星级)"""
    s = str(cid)
    # 针对 S10 复合 ID: [前缀n位][星级1位][基础ID 3位]
    import re as _re
    m = _re.match(r'^(\d{2,5})(\d)(\d{3})$', s)
    if m:
        try: return int(m.group(2))
        except: _SIL.note("core/id_map.py:693", "return int(m.group(2))")

    tbl = _load("chess")
    e = tbl.get(s)
    if not e:
        return 1
    try:
        a0 = float((tbl.get(chess_base_of(s)) or e).get("atk") or 0)
        if a0 > 0 and attack not in (None, "", 0):
            r = float(attack) / a0
            if r < 1.5:
                return 1
            if r < 3.5:
                return 2
            return 3
    except Exception:
        _SIL.note("core/id_map.py:709", "a0 = float((tbl.get(chess_base_of(s)) or e).ge")
    return 2 if _rev_upgrade_map().get(s) else 1


def chess_base_of(cid):
    """沿升级链回溯到1星基础ID(商店里刷的是基础ID, 招募按名字匹配用)"""
    cur = str(cid)
    rev = _rev_upgrade_map()
    for _ in range(5):
        if not rev.get(cur):
            break
        cur = rev[cur]
    return cur


_RANK_POOL_CACHE = {}


def _rank_from_season_pool(cid):
    """从【本季官方表】取费用档 —— chess_meta.json 过期时的最后一层兜底。

    为什么需要这一层(2026-10-12 实测): data/chess_meta.json 是 S10 时代的
    静态快照 —— by_id 372 条【全是 210… 开头, 211… 一条都没有】。换到 S11 后,
    本季 ID 在上面三层(chess.json / gen / chess_meta)一个都查不到,
    chess_cost_of 一律返回 None。

    后果是两处静默降级, 界面一个字都不提示:
      ① RA.rough_odds() 首行就是 `if (!r) return None` → refresh_advisor.advise()
         里 out["odds"] 永远不设置 → "刷几次能看到这张卡"这条理由永久消失,
         用户填了目标卡却跟没填一样, 静默退化成通用刷牌;
      ② board_strength() 里 cost*2 每张卡退化成 1*2 → 战力评分失真。

    本季权威 rank 在官方 ChessConf.ChessRank 里, tavern_kb.load_chess 已读好且自带
    缓存。这里只在上面全部落空时才问它, 并把结果记进 _RANK_POOL_CACHE 供热路径复用。

    v4.153★ 改为复用 season_pool_meta(): 那边同一张表、同一套 upgrade_id 反查
      (这条原先自己写的 loop 就是它的等价实现), 两处分开写会出现"一边能看到这张卡
      的势力、另一边看不到它的费用"这种半修好的状态。
    """
    s = str(cid)
    if s in _RANK_POOL_CACHE:
        return _RANK_POOL_CACHE[s]
    try:
        rank = _int_or((season_pool_meta().get(s) or {}).get("rank"), 0)
    except Exception:
        rank = 0
    _RANK_POOL_CACHE[s] = rank
    return rank


def chess_cost_of(cid):
    """棋子费用(rank字段), 未知返回None.
    v3.88★ 针对复合 ID 自动回退到基础 ID 查找费用"""
    s = str(cid)
    # 如果是复合 ID，先尝试拿 real_id
    import re as _re
    m = _re.match(r'^(\d{2,5})(\d)(\d{3})$', s)
    if m:
        real_id = m.group(3)
        # 尝试补全 4 位
        for tid in [real_id, "1" + real_id, "2" + real_id]:
            e = _load("chess").get(tid)
            if e and e.get("rank"):
                try: return int(e["rank"])
                except: _SIL.note("core/id_map.py:738", "return int(e['rank'])")
            
    e = _load("chess").get(s)
    try:
        if e and e.get("rank"):
            return int(e["rank"])
        # 尝试通用库字段
        e_gen = _load("gen").get(s)
        if e_gen:
            for f in ("cost", "fee", "price", "level", "rank"):
                if e_gen.get(f): return int(e_gen[f])
    except Exception:
        _SIL.note("core/id_map.py:750", "if e and e.get('rank'): return int(e['rank'])")
    # v4.47★ 兜底: 上面只查了 chess.json, 而 chess.json 是【页面脚本推上来的名字表】
    #   (条目只有 {"n": 名字, "src": ...}, 压根没有 rank 字段) —— 含费用的那份是
    #   chess_meta.json(赛季数据流程写的, by_id 里有 rank/faction)。
    #   于是"推过赛季数据之后"的 ID 一律查不到费用, 表现是费用筛选/按费用排序静默全落到
    #   调用方的兜底值(0 或 9), 看着像"排序坏了/这一列是空的", 很难联想到是数据源选错了。
    #   这里只把"None"变成真值, 不改任何已能查到的结果。
    try:
        _rank = (get_chess_meta().get(s) or {}).get("rank")
        if _rank:
            return int(_rank)
    except (TypeError, ValueError):
        return None
    # v4.148★ 最后一层: 本季官方表(tavern_kb.load_chess)。
    #   chess_meta 是 S10 快照, 换季后本季 ID 在上面全部落空 —— 不补这层,
    #   rough_odds 会永远返回 None, 刷新顾问的概率判断形同虚设(见 _rank_from_season_pool 注释)。
    try:
        _r = _rank_from_season_pool(s)
        if _r:
            return _r
    except Exception:
        pass
    return None


# ChessRankConf.TotalCount: 每个费用段在卡池里的初始总数
# v4.110★ 原先这里只有 1费(18)是对的, 2~6费(12/6/4/3/3)是早年估的。真值来自
#   python tools/export_pool_tables.py → 参考_配置表/发牌概率与卡池_s10.md:
#     1费18 / 2费15 / 3费13 / 4费11 / 5费9 / 6费7(8 家共享 2078 张 / 184 种)
#   这个数是 rough_odds 的分母之一 —— 错了会直接把"还差几张 / 大概还要刷几次"算歪。
#   来源样例: ChessRankConf first={"ID":1,"Rank":1,"StarShown":1,"TotalCount":18,"Resouce":1}
_RANK_POOL_SIZE = {1: 18, 2: 15, 3: 13, 4: 11, 5: 9, 6: 7}


def chess_pool_size(cid):
    """某棋子在卡池里的初始总数(按rank查ChessRankConf.TotalCount)"""
    r = chess_cost_of(cid)
    return _RANK_POOL_SIZE.get(r, 6) if r else None


def board_strength(board_list):
    """阵容强度评分(快速估算, 不是精确DPS):
    score = sum(cost*2 + hp + attack*1.5 + star_bonus) per card
    star_bonus: 1星+0, 2星+3, 3星+8"""
    if not board_list:
        return 0
    total = 0.0
    for c in board_list:
        if not isinstance(c, dict):
            continue
        cid = c.get("chessID") or c.get("chessId") or c.get("goodsID")
        cost = chess_cost_of(cid) or 1
        hp = c.get("totalHp") or c.get("hp") or 0
        atk = c.get("totalAttack") or c.get("attack") or 0
        star = chess_star_of(cid, attack=atk)
        star_bonus = {1: 0, 2: 3, 3: 8}.get(star, 0)
        total += cost * 2 + hp + atk * 1.5 + star_bonus
    return round(total, 1)


_THIS_SEASON_PREFIX = None


def season_prefix():
    """v4.113★ 本季 chessID 的前缀(8 位 ID 的头 3 位) —— 因为官方【把赛季号编进了 ID】。

    实锤(拿 S10/S11 两份配置逐条对出来的): 『曹休』S10 = 21001011、S11 = 21101011 ——
    名字一字没改, ID 整套换(第 2~3 位就是赛季号)。而 data/chess.json 是【累积全量】,
    S2~S11 的同名卡全在里面, 于是 find_ids_by_name('曹休') 一次返回 18 个 ID, 本季那两个
    还排在最后。剧本/招募/遣散/随征这些"按名字反查 ID"的入口拿走第一个 → 拿到 S2 的
    202101 → 局内压根没这张卡, 表现就是"手牌里明明有曹休, 却报找不到"。
    """
    global _THIS_SEASON_PREFIX
    if _THIS_SEASON_PREFIX is None:
        p = ""
        try:
            from core import csv_loader as _CL
            s = int(str(getattr(_CL, "CURRENT_SEASON", "") or "0").strip())
            if 1 <= s <= 99:
                p = "2%02d" % s          # S10 → "210" / S11 → "211"
        except Exception:
            _SIL.note("core/id_map.py:818", "season_prefix = '2%02d' % CURRENT_SEASON")
            p = ""
        _THIS_SEASON_PREFIX = p
    return _THIS_SEASON_PREFIX


def reset_season_prefix():
    """换季后调一下 —— 上面那份前缀缓存是按旧赛季算的, 不清就还按旧季排。

    v4.148★ 顺带清 _RANK_POOL_CACHE: 那里面存的是【旧赛季】的费用档, 换季后同样失效,
    不清的话 chess_cost_of 会一直拿旧季的 rank 当本季用。
    """
    global _THIS_SEASON_PREFIX
    _THIS_SEASON_PREFIX = None
    try:
        _RANK_POOL_CACHE.clear()
    except Exception as e:
        _SIL.note("core/id_map.py:1040", "_RANK_POOL_CACHE.clear()")
    # v4.153★ 本季的【势力/费用】同样按旧季缓存着, 不清的话 _enrich_generals 会一直
    #   给新赛季的卡贴旧赛季没有的空势力(表现: 势力下拉/按势力预设对新季卡全落空)。
    global _SEASON_POOL_META
    _SEASON_POOL_META = None


def season_first(ids):
    """把【本季】的 ID 排到最前, 其余保持原顺序(稳定排序, 不丢兜底)。"""
    p = season_prefix()
    if not p or not ids or len(ids) < 2:
        return ids
    out = list(ids)
    out.sort(key=lambda k: 0 if (str(k).isdigit() and len(str(k)) == 8
                                 and str(k).startswith(p)) else 1)
    return out


def find_chess_ids_by_name(sub):
    """按名字子串查棋子ID列表(搜索武将用)"""
    sub = (sub or "").strip()
    if not sub:
        return []
    return season_first([k for k, v in _load("chess").items() if sub in str(v.get("n", ""))])


def qualities_of(gid):
    """v4.89★ 与这张卡【同名】的所有 ID(含金卡等所有品质)。

    为什么要它(对上游 sgscodex 那份『一键遣散保留指定卡』的对齐):
      普通卡和金卡是【同名不同 ID】(尾数 1=普通 / 2=金, 见 _scGoldOk 的判据)。
      『永久保留』名单是按 ID 存的, 所以名单里填了『孙策(普通)』时, 金孙策是【另一个 ID】
      → 一键遣散会把金孙策一起卖掉。上游那份脚本专门修过这一条:
        "一键遣散现在会自动保留同名将的所有品质(如金卡)"。
      这里补齐: 保留名单按名字展开到全部品质, 用户不用手动把两个 ID 都加一遍。

    ⚠ 用【精确同名】, 不是子串: 子串会把『神张飞』当成『张飞』的品质一起保留
      (那是两张完全不同的卡, 上游那份也没做这个展开)。
    ⚠ 只在【同一赛季内】展开。为什么: 同名卡在库里横跨十几个赛季(实测『孙策』有 20 个 ID,
      从 201112 到 21003212), 全展开等于把往季的卡也塞进保留名单 —— 数量虚胖, 而且
      "保留孙策"的本来意思只是"这一季的孙策"。同季判据 = 【位数相同 + 前 3 位相同】
      (21003211 与 21003212 同季; 21003211 与 201112 不同季)。
    ⚠ 认不出(不在 chess 库里/没名字)就原样返回它自己 —— 宁可少保留一张, 也不乱加 ID。
    """
    key = str(gid or "").strip()
    if not key:
        return []
    table = _load("chess") or {}
    me = table.get(key)
    if not isinstance(me, dict):
        return [key]
    nm = str(me.get("n") or "").strip()
    if not nm:
        return [key]
    # 同季 = 位数相同 + 前 3 位相同(6 位的往季 ID 与 8 位的当季 ID 天然不会混)
    season = (len(key), key[:3]) if (key.isdigit() and len(key) >= 4) else None
    out = [key]
    for k, v in table.items():
        ks = str(k)
        if ks == key or not ks.isdigit() or not isinstance(v, dict):
            continue
        if str(v.get("n") or "").strip() != nm:
            continue
        if season and (len(ks), ks[:3] if len(ks) >= 3 else "") != season:
            continue
        out.append(ks)
    return out


def expand_qualities(ids):
    """v4.89★ 把一串 ID 展开成【含同名的所有品质】的去重列表。见 qualities_of。"""
    out = []
    for g in (ids or []):
        for k in qualities_of(g):
            ks = str(k)
            if ks and ks not in out:
                out.append(ks)
    return out


def find_ids_by_name(sub):
    """v4.28★ 按名字查棋子 chessID(8位) —— 精确优先, 字形容错兜底。返回去重后的字符串 ID 列表。

    为什么需要"字形容错": 实测『马云禄』在 ChessConf(data/chess.json) 里写作『马云騄』
    (异体字『騄』), 而游戏画面和玩家习惯都写简体『马云禄』 → 精确查空 → 剧本按名反查
    拿不到任何 ID → 用户看到的现象就是"手牌里明明有这张卡, 却报没找到要遣散的"。
    所以精确查空后再做一次"同字数、最相似"的兜底:
      『马云禄』↔『马云騄』有 2/3 字相同 → 命中;  『孙策』↔『孙坚』只有 1/2 字相同 → 不命中。
    只取相似度最高那一档, 且仅在精确查空时启用, 正常情况完全不受影响。

    注: 只查 chess 库。gen(data/id_map.json)/learned(data/discovered_ids.json) 里存的是
    6 位 ID, 与页面手牌卡上的 chessID(8位) 不是同一套, 混进来会给出根本无法匹配的 ID
    (实测『马云禄』会在 learned 里精确命中 6 位的 200414, 反而把兜底挡掉了)。
    """
    sub = (sub or "").strip()
    if not sub:
        return []
    out = []
    for k, v in (_load("chess") or {}).items():
        if isinstance(v, dict) and sub in str(v.get("n") or ""):
            ks = str(k)
            if ks.isdigit() and ks not in out:
                out.append(ks)
    if out:
        return season_first(out)
    # 字形差异兜底(仅在精确查空时启用)
    cands = []
    for k, v in (_load("chess") or {}).items():
        if not isinstance(v, dict):
            continue
        nm = str(v.get("n") or "")
        if len(nm) != len(sub) or nm == sub:
            continue
        r = difflib.SequenceMatcher(None, sub, nm).ratio()
        if r >= 0.6:
            cands.append((r, str(k)))
    if cands:
        cands.sort(reverse=True)
        top = cands[0][0]
        for r, k in cands:
            if r < top:
                break
            if k.isdigit() and k not in out:
                out.append(k)
    return season_first(out)


def _norm_spell_name(name):
    """v4.30★ 锦囊名归一化 —— 只去掉分隔符/空白, 【不】丢文字。

    为什么不能复用 _clean_name: 它第7步会把 "·" 后面整段砍掉, 对武将名(皮肤后缀)合适,
    对锦囊却是致命的 —— 库里/配置表里写的是『顺手牵羊·三』, 砍完只剩『顺手牵羊』,
    而用户照游戏画面填的是『顺手牵羊三』, 于是两边永远对不上
    (实机现象: "手牌的 就叫顺手牵羊三啊", 页面却报『手牌中无锦囊: 顺手牵羊三』)。
    """
    import re as _re
    t = str(name or "")
    t = _re.sub(r"^[sS]\d+", "", t)          # 赛季前缀 S10/S2100
    t = t.replace("合金", "")
    return _re.sub(r"""[·・•‧．.。、,，;；:：\-—－_/\\|~～()（）\[\]【】{}<>《》\s"'+]""", "", t)


def find_spell_ids_by_name(sub):
    """v4.30★ 按名字查锦囊 SpellID(精确优先, 子串兜底)。返回去重后的字符串 ID 列表。

    用途: 剧情的「用锦囊」步骤把用户填的名字反查成 SpellID 一起下发, 页面拿它跟手牌卡的
    spellID 精确比对 —— 彻底绕开"名字写法不一致"(库里『顺手牵羊·三』vs 用户『顺手牵羊三』)。
    查两个库: cards.json(通用卡牌/锦囊目录, 含各赛季) + tavern_spells.json(自走棋专属)。

    子串兜底只在精确查空时启用, 这样"顺手牵羊"(不带星级)也能把所有星级都找出来。
    """
    sub_n = _norm_spell_name(sub)
    if len(sub_n) < 2:
        return []
    exact, loose = [], []
    for ns in ("cards", "tavern_spells"):
        for k, v in (_load(ns) or {}).items():
            if not isinstance(v, dict):
                continue
            nm = _norm_spell_name(v.get("n") or "")
            if not nm:
                continue
            ks = str(k)
            if not ks.isdigit():
                continue
            if nm == sub_n:
                if ks not in exact:
                    exact.append(ks)
            elif (sub_n in nm or nm in sub_n) and ks not in loose:
                loose.append(ks)
    return exact or loose


def spell_name_map():
    """v4.31★ 锦囊/法术 SpellID -> 名称, 推给油猴端 getSpellName() 用。

    为什么必须有它: 油猴端 normalizeGoods() 解析手牌锦囊时, 名字只有三个来源 ——
      ① getSpellName(spellID)(本文返回值) ② 卡对象自带的 name 字段 ③ TavernChessConfiger。
    而 ① 以前【从来没人推过】(remote_ops.load_spell_name_map 定义了却没有调用点),
    ② 手牌对象上没有, ③ 只有当前赛季且在配置表里才查得到 →
    实测现象: 手牌里的锦囊名字全是裸 ID("现有锦囊: 310004(spellID=310004,goodsID=1369)"),
    用户按名字找锦囊必然失败, 报错也完全看不懂。

    键取 cards.json / tavern_spells.json 里的【纯数字键】(6 位 SpellID, 10 位 CardID),
    值取 n 字段; 两库同名同 ID 时后写的覆盖, 名称一致所以无所谓。
    """
    out = {}
    for ns in ("cards", "tavern_spells"):
        for k, v in (_load(ns) or {}).items():
            if not isinstance(v, dict):
                continue
            ks = str(k)
            nm = str(v.get("n") or "").strip()
            if ks.isdigit() and nm:
                out[ks] = nm
    return out


def all_names():
    """返回所有武将名集合(OCR匹配用)"""
    names = set()
    for ns in ("gen", "chess", "lords"):
        for v in _load(ns).values():
            n = v.get("n", "")
            if n and len(n) >= 2:
                names.add(n)
    return names


def current_season_hero_names():
    """v4.170★ 本季武将名集合(供剧本「挑选」框用) —— 只含【当前赛季】的棋子。

    为什么单独做: 原来的 all_names() 把 gen/chess/lords 全季汇总, 挑选框里混着一堆非本季武将
    (用户原话"剧本tab里的数据有很多不是本赛季的")。锦囊表(tavern_spells.json)早就是赛季过滤的,
    武将表却一直没过滤。这里按 chessID 前缀(season_prefix)收窄 chess.json, 主公(lords)本就按季生成。
    读不到赛季前缀 / 结果为空时退回 all_names(), 绝不让挑选框空着。
    """
    p = season_prefix()
    out = set()
    if p:
        for cid, v in _load("chess").items():
            if not str(cid).startswith(p):
                continue
            n = (v or {}).get("n")
            if n and len(n) >= 2:
                out.add(n)
    for v in _load("lords").values():
        n = (v or {}).get("n")
        if n and len(n) >= 2:
            out.add(n)
    if not out:
        return all_names()
    return sorted(out)


def count():
    return len(_load("gen"))


def counts():
    return {ns: len(v) for ns, v in _caches.items()} if _caches else {ns: len(_load(ns)) for ns in _PATHS}


def process_tavern_chess_config(full_config):
    """从 TavernChessConfig.json 提取/清洗武将、锦囊、技能、主公数据，
    结构化为 generals.json / tavern_chess.json / tavern_spells.json /
    skills.json / lords.json / lord_skills.json / cards.json / chess.json。

    v4.160★ 修(实测): 官方 tabtoy 导出的子表早已是【list】，字段是
    ChessName/DefaultATK/DefaultDEF/ChessRank/ChessSkill/RelateUpgradeChessID/
    SeasonID（不是旧的 Name/Attack/Hp/Rank/Skill/UpgradeId/Season），阵营来自
    MinionType 数字(1魏2蜀3吴4群5黄巾6汉7西凉8袁)。旧代码按 dict + 旧字段名读，
    一跑就 AttributeError 且全字段 None —— 这条「配置抓取·解密版」路径等于残废。
    现在 list/dict 两种格式、新/旧两套字段名都兼容。
    """
    if not full_config:
        print("[id_map] empty full_config, skip processing")
        return

    def _rows(conf, idkey):
        """子表可能是 dict(旧) 或 list(新)，统一成 (id_str, row) 迭代器。"""
        if isinstance(conf, dict):
            for k, v in conf.items():
                if isinstance(v, dict):
                    yield str(k), v
        elif isinstance(conf, list):
            for v in conf:
                if isinstance(v, dict) and idkey in v:
                    yield str(v[idkey]), v

    def _g(d, *names, default=None):
        """按候选字段名取第一个存在且非空的。"""
        for n in names:
            if n in d and d[n] not in (None, ""):
                return d[n]
        return default

    def _to_int(v, d=0):
        try:
            return int(v)
        except Exception:
            return d

    # 阵营: MinionType 数字 → 势力名(对齐 tavern_kb.FACTION_CODE)
    FACTION = {"1": "魏", "2": "蜀", "3": "吴", "4": "群",
               "5": "黄巾", "6": "汉", "7": "西凉", "8": "袁"}

    chess_conf = full_config.get("ChessConf", {})
    spell_conf = full_config.get("ChessSpellConf", {})
    skill_conf = full_config.get("ChessSkillConf", {})
    general_conf = full_config.get("ChessGeneralConf", {})
    gen_skill_conf = full_config.get("ChessGeneralSkillConf", {})

    # 技能描述先建好, 武将/主公的 effect 直接复用
    skill_desc = {}
    for sid, s in _rows(skill_conf, "SkillID"):
        skill_desc[sid] = _clean_name(_g(s, "SkillDesc", "Desc")) or _clean_name(_g(s, "SkillName", "Name"))
    gen_skill_desc = {}
    for sid, s in _rows(gen_skill_conf, "SkillID"):
        gen_skill_desc[sid] = _clean_name(_g(s, "SkillDesc", "Desc", "SkillName", "Name"))

    generals_data = {}        # generals.json (基准武将数据)
    tavern_chess_data = {}    # tavern_chess.json (自走棋武将)
    tavern_spells_data = {}   # tavern_spells.json (锦囊)
    skills_data = {}          # skills.json (技能库)
    lords_data = {}           # lords.json (主公)
    lord_skills_data = {}     # lord_skills.json (主公技能)
    cards_data = {}           # cards.json (通用卡牌)
    chess_detail_data = {}    # chess.json (棋子详细属性)

    # --- 武将 ChessConf ---
    for cid, item in _rows(chess_conf, "ChessID"):
        name = _clean_name(_g(item, "ChessName", "Name", ""))
        if not name:
            continue
        mt = str(_g(item, "MinionType", "Faction", "")).strip()
        faction_name = FACTION.get(mt, "")
        skill_ids = _g(item, "ChessSkill", "Skill", default=[]) or []
        inline = _clean_name(_g(item, "ChessSkillDesc", ""))
        if not inline and skill_ids:
            inline = "；".join(d for d in (skill_desc.get(str(s)) for s in skill_ids) if d)
        gen_info = {
            "id": _to_int(cid, cid),
            "name": name,
            "atk": _to_int(_g(item, "DefaultATK", "Attack"), 0),
            "def": _to_int(_g(item, "DefaultDEF", "Hp", "Def"), 0),
            "faction": faction_name,
            "rank": _to_int(_g(item, "ChessRank", "Rank"), 0),
            "skill_ids": skill_ids,
            "type": "武将",
            "season": _to_int(_g(item, "SeasonID", "Season"), 0),
            "upgrade_id": _to_int(_g(item, "RelateUpgradeChessID", "UpgradeId"), 0),
            "effect": inline,
        }
        generals_data[cid] = gen_info
        tavern_chess_data[cid] = gen_info
        chess_detail_data[cid] = {
            "n": name,
            "atk": gen_info["atk"],
            "def": gen_info["def"],
            "faction": faction_name,
            "rank": gen_info["rank"],
            "skill": skill_ids,
            "upgrade_id": gen_info["upgrade_id"],
        }
        cards_data[cid] = {"n": name, "type": "武将", "desc": inline, "faction": faction_name}

    # --- 锦囊 ChessSpellConf ---
    for sid, item in _rows(spell_conf, "SpellID"):
        name = _clean_name(_g(item, "SpellName", "Name", ""))
        if not name:
            continue
        desc = _clean_name(_g(item, "SpellSkillDesc", "Desc"))
        spell_info = {
            "id": _to_int(sid, sid),
            "name": name,
            "desc": desc,
            "rank": _to_int(_g(item, "SpellRank", "Rank"), 0),
            "type": "锦囊",
        }
        tavern_spells_data[sid] = spell_info
        cards_data[sid] = {"n": name, "type": "锦囊", "desc": desc}

    # --- 技能 ChessSkillConf ---
    for sid, item in _rows(skill_conf, "SkillID"):
        name = _clean_name(_g(item, "SkillName", "Name", ""))
        if not name:
            continue
        skill_info = {
            "id": _to_int(sid, sid),
            "n": name,
            "desc": _clean_name(_g(item, "SkillDesc", "Desc")),
            "type": "技能",
        }
        skills_data[sid] = skill_info
        lord_skills_data[sid] = skill_info

    # --- 主公 ChessGeneralConf ---
    for gid, item in _rows(general_conf, "GeneralID"):
        name = _clean_name(_g(item, "GeneralName", "Name", ""))
        if not name:
            continue
        gskill = _g(item, "GeneralSkill", "Skill")
        lord_info = {
            "id": _to_int(gid, gid),
            "n": name,
            "skill_id": gskill,
            "hp": _to_int(_g(item, "HitPoint", "Hp"), 0),
            "faction_req": FACTION.get(str(_g(item, "RequiredMinionTyp", "Faction", "")).strip(), ""),
            "type": "主公",
            "desc": _clean_name(gen_skill_desc.get(str(gskill)) if gskill else ""),
        }
        lords_data[gid] = lord_info
        cards_data[gid] = {"n": name, "type": "主公", "desc": lord_info["desc"]}


    # --- 保存到文件 ---
    def save_json(path_key, data):
        filepath = _PATHS[path_key]
        with _lock:
            try:
                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                _caches[path_key] = data # 更新缓存
            except Exception as e:
                print(f"[id_map] Failed to save {filepath}: {e}")

    save_json("generals", generals_data)
    save_json("tavern_chess", tavern_chess_data)
    save_json("tavern_spells", tavern_spells_data)
    save_json("skills", skills_data)
    save_json("lords", lords_data)
    save_json("lord_skills", lord_skills_data)
    save_json("cards", cards_data)
    save_json("chess", chess_detail_data)

    # 清除缓存，确保下次读取是最新数据
    _caches.clear()
    # 重新加载，避免数据为空
    _load("generals")
    _load("tavern_chess")
    _load("tavern_spells")
    _load("skills")
    _load("lords")
    _load("lord_skills")
    _load("cards")
    _load("chess")

    print("[id_map] Processed TavernChessConfig and updated JSON files.")

    # 更新发现的ID列表
    export_discovered_ids()


def reset_namespace(*namespaces):
    """清空指定命名空间的文件+缓存（用于路由修复后重置脏数据）。
    namespaces 为空时清空全部（gen, lords, chess, cards, skins, lord_skills）。
    不包括 learn_faces 学来的 8位chessID（gen库）。
    """
    target = set(namespaces) if namespaces else {"lords", "chess", "cards", "skins", "lord_skills"}
    with _lock:
        for ns in target:
            if ns not in _PATHS:
                continue
            try:
                with open(_PATHS[ns], "w", encoding="utf-8") as f:
                    json.dump({}, f)
            except Exception:
                _SIL.note("core/id_map.py:1188", "with open(_PATHS[ns], 'w', encoding='utf-8') a")
            _caches.pop(ns, None)



def get_tavern_chess_data():
    """从 tavern_chess.json 中加载自走棋武将数据"""
    return _load("tavern_chess")


def get_all_tavern_generals_for_ui():
    """返回所有自走棋武将的名称和ID列表，供UI选择使用

    v4.11★ 优先从白名单(CSV加载的当前赛季武将)获取, 退回 tavern_chess.json
    v4.42★ 改成【白名单 ∪ tavern_chess.json】(按 ID 去重), 而不是"白名单非空就只用白名单"。
      白名单 = 当前赛季的商店表, 里面没有的 ID 以前在『全部武将』里就彻底消失了 ——
      可它可能赛季10 还在用。tavern_chess.json 是页面脚本推上来的自走棋专属棋子库,
      会累积往季 ID, 正好补这一块。
      实机后果(用户报的): 随征卡牌ID里那个「黄巾兵」是赛季9 的 942311, 既不在全部武将里,
      手动添加也找不到它; 黑名单里若有这种跨季 ID, 从黑名单移除后【两边都不见了】——
      因为左表『全部武将』装不下它。用户原话: "黑名单不能移除到全部武将"。
    """
    items = []
    seen = set()

    def _add(gid, name, display=None):
        gid = str(gid or "").strip()
        nm = str(name or "").strip()
        if not gid or gid in seen or not nm or nm.isdigit():
            return
        seen.add(gid)
        items.append({"id": gid, "name": nm, "display": display or f"{nm} ({gid})"})

    # ① 白名单(当前赛季商店, 184 条) —— 名字最准, 排在前面(去重时优先保留)
    try:
        from .csv_loader import get_whitelist_for_ui
        for g in get_whitelist_for_ui() or []:
            _add(g.get("id"), g.get("name"), g.get("display"))
    except Exception as e:
        print(f"[id_map] 全部武将: 白名单读取失败: {e}")
    # ② ChessConf.csv 全量(3346 条, 含各赛季变体卡)。
    #    必须带上这张表: 黑名单面板里那条 3346 长的名单就是从它生成的 ——
    #    左表装不下右表里的 ID, 就会出现"从黑名单移除后两边都不见了"。
    #    实测: 20904231(黄巾兵, 赛季9) 只在这张表里, 白名单和 tavern_chess.json 都没有它。
    try:
        from .csv_loader import chess_names_all
        for gid, nm in (chess_names_all() or {}).items():
            _add(gid, nm)
    except Exception as e:
        print(f"[id_map] 全部武将: ChessConf.csv 全量读取失败: {e}")
    # ③ tavern_chess.json(页面脚本推上来的自走棋专属棋子库, 会累积往季 ID)
    try:
        for gen_id, gen_info in (_load("tavern_chess") or {}).items():
            if isinstance(gen_info, dict):
                _add(gen_id, gen_info.get("name"))
    except Exception as e:
        print(f"[id_map] 全部武将: tavern_chess.json 读取失败: {e}")
    items.sort(key=lambda x: x.get("name") or "")
    return _enrich_generals(items)


def get_general_id_by_name(general_name: str):
    """根据武将名称查找对应的ID
    v4.10★ 支持 '名 (ID)' 格式直接提取ID，也支持纯名字"""
    if not general_name:
        return None
    import re
    m = re.search(r'\((\d+)\)\s*$', general_name.strip())
    if m:
        return m.group(1)
    cleaned_name = _clean_name(general_name)
    generals_data = _load("tavern_chess")
    for gen_id, gen_info in generals_data.items():
        if _clean_name(gen_info.get("name", "")) == cleaned_name:
            return gen_id
    return None



if __name__ == "__main__":   # 手动检查: python core/id_map.py
    for ns in _PATHS:
        store = _load(ns)
        print(f"[{ns}] {len(store)} 个")
        for gid, it in list(store.items())[:10]:
            print(f"  {gid} -> {it.get('n')}  [{it.get('src', '')[:40]}]")
