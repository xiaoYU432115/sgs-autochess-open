# -*- coding: utf-8 -*-
"""三选一奖励优先级配置（文档 §4.1 / §4.2 / §8 的落地数据层）

设计口径（对齐 自走棋助手-重构版/docs/CARDS_SPELLS_EQUIP_SELECT3.md）：
  · 默认名单是【可修改配置】，不是自动学习策略 —— 不做"自动学习/自动加名单"。
  · 权威存储: data/reward_priority.json（随 core.config.DATA_DIR 多开隔离）。
  · 「保存」时同时写本文件 + 经 set_triple_reward_priority 下发到游戏页
    localStorage['sgs_triple_reward_priority']（剧本 pickOne 未指定 pick 时走它，
    见 bridge/sgs_bridge.user.js 的 _scOpPickOne → _scDefaultRewardPick）。
  · 换季出现不认识的锦囊：桥端只把候选的真实 ID+名字记进 window.__sgsUnknownSpells
    （可诊断、可拉取），由用户在本配置里显式维护 —— 不把旧编号机械套用。
  · 特殊锦囊（用完后触发"武将三选一"强制默认武将名单）也可配置：
    默认 311019 借尸还魂·六 / 311002 草船借箭·五；311061 天降神兵·五 是
    装备三选一（走装备算法 sgs_shenbing_targets_v1），不吃武将名单，仅登记识别。
"""
import json
import os
import tempfile

from core.config import DATA_DIR, BASE_DIR

CONFIG_PATH = os.path.join(DATA_DIR, "reward_priority.json")

# §8 默认武将优先级（高→低）。改这里只影响"出厂默认"，用户文件一旦存在以文件为准。
DEFAULT_CHESS = [
    "陈珪", "左慈", "贾诩", "许劭", "蔡文姬", "程昱", "祢衡", "孙尚香", "庞德公", "高升",
    "黄承彦", "诸葛亮", "徐庶", "张鲁", "皇甫嵩", "周仓", "典韦", "廖化", "陆逊", "丁奉",
    "雷薄", "邓艾", "邹氏",
]

# §8 默认锦囊优先级（高→低）。三选一按【精确 ID】匹配（同名多阶，按名字会随机命中低阶）。
DEFAULT_SPELLS = [
    311034,  # 美人计·五
    311063,  # 偷梁换柱·六
    311052,  # 随机应变·六
    311047,  # 三十六计·五
    311081,  # 鸩毒·六
    311066,  # 万箭齐发·六
    311001,  # 兵贵神速·六
    311032,  # 联军盛宴·六
    311062,  # 偷梁换柱·四
    311015,  # 火攻·六
    311014,  # 火攻·四
    311070,  # 无中生有·五
    311069,  # 无中生有·三
    311013,  # 浑水摸鱼·六
    311037,  # 抛砖引玉·四
    311018,  # 借刀杀人·六
    311076,  # 以逸待劳·五
    311036,  # 南蛮入侵·四
    311035,  # 南蛮入侵·二
]

# 需要识别的三个特殊锦囊（S11 局内 ID）。
#   311019 / 311002: 用完后会再开一个【武将】三选一 → 强制 §8 默认武将名单
#                    （bridge _scSetSourceTag，名单已做成可配置 specialSpellIds）。
#   311061:          用完后开的是【装备】三选一 → 走装备算法，不进武将名单。
DEFAULT_SPECIAL_SPELLS = [311019, 311002]
EQUIP_SPELL_IDS = [311061]   # 仅登记识别用（桥端装备窗流程另有一套，勿混）

# 内置 ID→名字 目录（兜底；正常情况从 data/tavern_spells.json 现读本季全表）
DEFAULT_SPELL_CATALOG = {
    311034: "美人计·五",   311063: "偷梁换柱·六", 311052: "随机应变·六",
    311047: "三十六计·五", 311081: "鸩毒·六",     311066: "万箭齐发·六",
    311001: "兵贵神速·六", 311032: "联军盛宴·六", 311062: "偷梁换柱·四",
    311015: "火攻·六",     311014: "火攻·四",     311070: "无中生有·五",
    311069: "无中生有·三", 311013: "浑水摸鱼·六", 311037: "抛砖引玉·四",
    311018: "借刀杀人·六", 311076: "以逸待劳·五", 311036: "南蛮入侵·四",
    311035: "南蛮入侵·二",
    311019: "借尸还魂·六", 311002: "草船借箭·五", 311061: "天降神兵·五",
}

_CATALOG_CACHE = None


def load_spell_catalog(refresh=False):
    """锦囊 ID→名字 目录：优先 data/tavern_spells.json（本季全量），兜底内置 22 条。"""
    global _CATALOG_CACHE
    if _CATALOG_CACHE is not None and not refresh:
        return dict(_CATALOG_CACHE)
    catalog = dict(DEFAULT_SPELL_CATALOG)
    for base in (DATA_DIR, os.path.join(BASE_DIR, "data")):
        p = os.path.join(base, "tavern_spells.json")
        try:
            if not os.path.exists(p):
                continue
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            for k, v in (d.items() if isinstance(d, dict) else []):
                try:
                    sid = int(k)
                except (TypeError, ValueError):
                    continue
                nm = str((v or {}).get("n") or "").strip() if isinstance(v, dict) else ""
                if sid > 0 and nm:
                    catalog[sid] = nm
            break   # DATA_DIR 命中就不再看根 data/
        except Exception:
            continue
    _CATALOG_CACHE = dict(catalog)
    return catalog


def spell_label(sid):
    """下拉框/列表展示用: "311034 美人计·五"；不认识的 ID 保留可诊断形态。"""
    cat = load_spell_catalog()
    return "%d %s" % (sid, cat.get(sid, "（未识别·可维护）"))


def default_config():
    """出厂默认配置（写盘的初始内容）。"""
    return {
        "version": 2,
        "_说明": "三选一奖励优先级：靠前优先。武将按名字精确匹配，锦囊按局内 ID 精确匹配"
                 "（S11=311xxx，换季不自动套用旧编号）。名单外的锦囊会记进桥端"
                 " __sgsUnknownSpells（奖励配置页可拉取），需在此显式维护。",
        "defaultPriority": {
            "chessNames": list(DEFAULT_CHESS),
            "spellIds": list(DEFAULT_SPELLS),
        },
        "spellCatalog": {str(k): v for k, v in sorted(DEFAULT_SPELL_CATALOG.items())},
        "specialSpellIds": list(DEFAULT_SPECIAL_SPELLS),
        "equipSpellIds": list(EQUIP_SPELL_IDS),
        "hiddenChessNames": [],
        "hiddenSpellIds": [],
        "groups": [],
        "activeGroupId": None,
    }


def load():
    """读配置；不存在/损坏时用默认生成。缺的键用默认补齐（向前兼容）。"""
    cfg = default_config()
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            u = json.load(f)
        if isinstance(u, dict):
            for k in ("defaultPriority", "spellCatalog", "specialSpellIds",
                      "equipSpellIds", "hiddenChessNames", "hiddenSpellIds",
                      "groups", "activeGroupId", "version"):
                if k in u:
                    cfg[k] = u[k]
    except FileNotFoundError:
        save(cfg)
    except Exception:
        save(cfg)
    return cfg


def save(cfg):
    """原子写（先临时文件再 replace，与 core/config.save_config 同款）。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=DATA_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return CONFIG_PATH


# ---------------------------------------------------------------- 归一化
def _norm_names(arr):
    out, seen = [], set()
    for x in (arr or []):
        s = str(x or "").strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _norm_ids(arr):
    out, seen = [], set()
    for x in (arr or []):
        try:
            n = int(x)
        except (TypeError, ValueError):
            continue
        if n > 0 and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def normalize(cfg):
    """非法值丢弃、去重、保序 —— 与桥端 _scRewardConfig 同一口径，脏数据不进决策链。"""
    cfg = dict(cfg or {})
    dp = cfg.get("defaultPriority") or {}
    cfg["defaultPriority"] = {
        "chessNames": _norm_names(dp.get("chessNames")),
        "spellIds": _norm_ids(dp.get("spellIds")),
    }
    cat = cfg.get("spellCatalog")
    if not isinstance(cat, dict):
        cat = {}
    clean_cat = {}
    for k, v in cat.items():
        try:
            sid = int(k)
        except (TypeError, ValueError):
            continue
        nm = str(v or "").strip()
        if sid > 0 and nm:
            clean_cat[str(sid)] = nm
    cfg["spellCatalog"] = clean_cat
    cfg["specialSpellIds"] = _norm_ids(cfg.get("specialSpellIds"))
    cfg["equipSpellIds"] = _norm_ids(cfg.get("equipSpellIds"))
    cfg["hiddenChessNames"] = _norm_names(cfg.get("hiddenChessNames"))
    cfg["hiddenSpellIds"] = _norm_ids(cfg.get("hiddenSpellIds"))
    groups, gseen = [], set()
    for g in (cfg.get("groups") or []):
        if not isinstance(g, dict):
            continue
        gid = str(g.get("id") or g.get("name") or "").strip()
        if not gid or gid in gseen:
            continue
        gseen.add(gid)
        groups.append({
            "id": gid,
            "name": str(g.get("name") or gid),
            "chessNames": _norm_names(g.get("chessNames")),
            "spellIds": _norm_ids(g.get("spellIds")),
        })
    cfg["groups"] = groups
    ag = cfg.get("activeGroupId")
    cfg["activeGroupId"] = str(ag) if ag else None
    if cfg["activeGroupId"] and cfg["activeGroupId"] not in gseen:
        cfg["activeGroupId"] = None   # 指向不存在的组 → 回落默认方案
    return cfg


def to_bridge_cfg(cfg):
    """转成 set_triple_reward_priority 需要的 v2 载荷（localStorage 落盘形状）。"""
    cfg = normalize(cfg)
    return {
        "version": 2,
        "defaultPriority": dict(cfg["defaultPriority"]),
        "groups": [dict(g) for g in cfg["groups"]],
        "activeGroupId": cfg["activeGroupId"],
        "specialSpellIds": list(cfg["specialSpellIds"]),
        "hiddenChessNames": list(cfg["hiddenChessNames"]),
        "hiddenSpellIds": list(cfg["hiddenSpellIds"]),
    }
