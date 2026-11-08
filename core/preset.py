# -*- coding: utf-8 -*-
"""v4.47★ 配置预设 导入 / 导出 —— 把"我调好的这一套"整包带走。

为什么要有这个模块
==================
助手现在的配置散在三处, 而且【没有一处能整体搬走】:
  · config.json              —— 剧本 / 热键 / 招募参数 / 遣散策略 / 速度 / Flag / 概率表 / 窗口校准
  · data/lists/*.json        —— 7 个武将列表(随征 / 白名单 / 黑名单 / 自动招募 / 多选 / 永久保留 / 遣散)
  · data/scenario_presets 等 —— 也在 config.json 里, 但属于独立功能
换台机器 / 重装 / 给朋友一份自己的配置时, 只能一个个文件拷, 还容易把 game_region(屏幕坐标)
这种【只对本机有效】的东西一起带过去, 到新机器上直接校准全错 —— 所以预设包是【分块】的:
用户自己勾"要带哪几块", 默认不勾窗口校准。

包格式(单个 json, 人能看懂):
    {"kind": "sgs_preset", "version": 1, "created_at": "...",
     "parts": ["lists", "scenarios"],
     "data": {"lists": {名字: {"ids": [...], "names": [...]}},
              "scenarios": {"scenarios": [...], ...}}}

导入前一律先整份备份(见 backup_before_import) —— 覆盖了别人的预设之后能一键回到原样。
"""
import json
import os
import shutil
import time

from core.config import CONFIG_PATH, DATA_DIR, load_config, save_config, DEFAULT_CONFIG
from core import list_config as LC

PRESET_KIND = "sgs_preset"
PRESET_VERSION = 1

# 每一块 = (键, 说明)。说明直接显示在界面上, 用户不用看代码就知道勾了会带走什么。
PARTS = [
    ("lists", "武将列表 —— 随征卡牌ID / 白名单 / 黑名单 / 自动招募 / 多选购买 / 永久保留 / 遣散目标"),
    ("scenarios", "剧本 + 剧本预设 + 目标预设(『剧本』页那整套)"),
    ("behavior", "对局行为 —— 找卡间隔/最大刷新/自动购买/遣散策略/速度档 & Flag/概率表"),
    ("bridge", "桥接与招募参数 —— 端口/找将方式/招募保留数"),
    ("window", "窗口校准 —— 游戏区域/子区域/截图尺寸 (只对本机有效, 换机器别勾)"),
]
PART_LABEL = dict(PARTS)

SCENARIO_KEYS = ("scenarios", "scenario_presets", "pick_presets")
BEHAVIOR_KEYS = ("find_interval", "max_refresh", "auto_buy", "dismiss_policy", "fps", "flags",
                 "pool_totals", "star_odds", "script_map_season_only", "script_map_season_no")
BRIDGE_KEYS = ("bridge",)
WINDOW_KEYS = ("game_region", "sub_regions", "capture_size", "quick_points", "quick_grids", "quick")

_KEYS_OF = {"scenarios": SCENARIO_KEYS, "behavior": BEHAVIOR_KEYS,
            "bridge": BRIDGE_KEYS, "window": WINDOW_KEYS}


def norm_parts(parts=None):
    """把界面传来的块名归一化: 认不出/为空 → 全部块(导出时的兜底)。"""
    ok = [k for k, _d in PARTS]
    if not parts:
        return list(ok)
    out = [str(p) for p in parts if str(p) in ok]
    return out or list(ok)


def collect(parts=None):
    """按块把当前配置收集成一个预设 dict(不写文件)。"""
    parts = norm_parts(parts)
    cfg = load_config()
    data = {}
    if "lists" in parts:
        data["lists"] = {name: LC.load_list(name) for name in LC.LIST_FILES}
    for key in parts:
        keys = _KEYS_OF.get(key)
        if keys:
            data[key] = {k: cfg.get(k, DEFAULT_CONFIG.get(k)) for k in keys}
    return {"kind": PRESET_KIND, "version": PRESET_VERSION,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "app": "三国杀自走棋助手", "parts": parts, "data": data}


def export_preset(path, parts=None):
    """把预设写到 path(单个 json)。返回 {"path", "parts", "lists", "keys"}。"""
    pack = collect(parts)
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(pack, f, ensure_ascii=False, indent=2)
    return summarize(pack, path=path)


def read_preset(path, with_data=False):
    """读预设文件。返回 (meta, data) —— meta 里带 parts/created_at, 供导入前确认。

    with_data=False 时不返回 data(只看清单就够, 避免把一大堆列表读进内存)。
    """
    with open(path, "r", encoding="utf-8") as f:
        pack = json.load(f)
    if not isinstance(pack, dict):
        raise ValueError("这个文件不是预设包(内容不是一个 json 对象)")
    kind = str(pack.get("kind") or "")
    if kind != PRESET_KIND:
        raise ValueError("这个文件不是助手导出的预设包(kind=%r, 期望 %r)" % (kind, PRESET_KIND))
    parts = norm_parts(pack.get("parts"))
    data = pack.get("data") or {}
    if not isinstance(data, dict):
        raise ValueError("预设包里的 data 不是对象, 文件可能损坏了")
    meta = {"path": path, "parts": parts, "created_at": pack.get("created_at"),
            "version": pack.get("version"), "app": pack.get("app")}
    return meta, (data if with_data else None)


def summarize(pack, path=""):
    """给界面一行话: 这个包里有几块、各含什么。"""
    parts = norm_parts((pack or {}).get("parts"))
    data = (pack or {}).get("data") or {}
    lists = (data.get("lists") or {})
    n_ids = 0
    for v in lists.values():
        if isinstance(v, dict):
            n_ids += len(v.get("ids") or [])
    n_sc = len(((data.get("scenarios") or {}).get("scenarios")) or [])
    return {"path": path, "parts": parts, "n_ids": n_ids, "n_lists": len(lists),
            "n_scenarios": n_sc}


def backup_before_import():
    """导入前把 config.json + data/lists/*.json 整份备份到 data/_backup/preset_<时间>/。

    为什么必须做: 导入是【覆盖】动作 —— 别人的预设盖上来, 你自己的配置就没了。
    有这份备份, 说一句"回到导入前"就能手工还原(界面日志里会打印路径)。

    返回 (备份目录, 成功文件数, 失败说明列表) —— 失败【不吞】, 交给调用方警告用户。
    """
    stamp = time.strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(DATA_DIR, "_backup", "preset_%s" % stamp)
    os.makedirs(dest, exist_ok=True)
    n = 0
    failed = []
    if os.path.exists(CONFIG_PATH):
        try:
            shutil.copyfile(CONFIG_PATH, os.path.join(dest, "config.json"))
            n += 1
        except Exception as e:
            # 备份失败【必须报出来】: 这次导入就没有退路了, 用户得先知道再决定要不要继续。
            failed.append("config.json(%s: %s)" % (type(e).__name__, e))
    for name in LC.LIST_FILES.values():
        src = os.path.join(LC.LISTS_DIR, name)
        if os.path.exists(src):
            try:
                shutil.copyfile(src, os.path.join(dest, name))
                n += 1
            except Exception as e:
                failed.append("%s(%s: %s)" % (name, type(e).__name__, e))
    return dest, n, failed


def _merge_lists(dst, src):
    """并集: 按 ID 去重, 名字顺序跟着 ID 走(导入别人的白名单常用这个)。"""
    ids = [str(x) for x in (dst.get("ids") or [])]
    names = list(dst.get("names") or [])
    seen = set(ids)
    sids = [str(x) for x in (src.get("ids") or [])]
    snames = list(src.get("names") or [])
    added = 0
    for i, gid in enumerate(sids):
        if gid in seen:
            continue
        seen.add(gid)
        ids.append(gid)
        names.append(snames[i] if i < len(snames) else "")
        added += 1
    return {"ids": ids, "names": names}, added


def import_preset(path, parts=None, mode="replace", do_backup=True):
    """把预设写进现行配置。

    parts      : 只导这几块(None = 按包里的 parts 全导); 交集语义 —— 包里有、用户也勾了才导。
    mode       : "replace" = 预设覆盖本地(列表整份换); "merge" = 列表并集(配置键仍是覆盖)。
    do_backup  : 默认先整份备份。

    返回 report dict: {"ok", "path", "parts", "backup", "lists": {名: 条数}, "keys": [...],
                       "n_scenarios", "notes": [...]}
    """
    meta, data = read_preset(path, with_data=True)
    want = set(norm_parts(parts)) if parts else set(meta["parts"])
    use = [p for p in meta["parts"] if p in want]
    if not use:
        raise ValueError("这个包里没有你勾选的内容")
    report = {"ok": True, "path": path, "parts": use, "lists": {}, "keys": [],
              "n_scenarios": 0, "notes": []}
    if do_backup:
        dest, n, failed = backup_before_import()
        report["backup"] = dest
        report["backup_files"] = n
        report["backup_failed"] = failed
        if failed:
            report["notes"].append("⚠ 备份没做全(%d 项失败: %s) —— 这次导入【没有完整退路】, "
                                   "如果结果不对请手工还原。" % (len(failed), "; ".join(failed[:3])))

    cfg = load_config()
    changed_keys = []

    if "lists" in use:
        for name, payload in (data.get("lists") or {}).items():
            if name not in LC.LIST_FILES or not isinstance(payload, dict):
                continue
            if mode == "merge":
                merged, added = _merge_lists(LC.load_list(name), payload)
                LC.save_list(name, merged["ids"], merged["names"])
                report["lists"][name] = {"count": len(merged["ids"]), "added": added}
            else:
                ids = [str(x) for x in (payload.get("ids") or [])]
                LC.save_list(name, ids, payload.get("names") or [])
                report["lists"][name] = {"count": len(ids), "added": len(ids)}

    for key in use:
        block = data.get(key) or {}
        if not isinstance(block, dict):
            continue
        for k in _KEYS_OF.get(key) or ():
            if k in block:
                cfg[k] = block[k]
                changed_keys.append(k)
        if key == "scenarios":
            report["n_scenarios"] = len(block.get("scenarios") or [])

    if changed_keys:
        save_config(cfg)
        report["keys"] = changed_keys
    if "window" in use:
        report["notes"].append("带了窗口校准: 如果这不是本机的坐标, 请回『数据管理』重新校准。")
    report["notes"].append("列表改动已即时生效; 剧本/热键等界面元素需要重启助手才完全生效。")
    return report


def format_report(r):
    """把导入结果排成给人看的多行文本。"""
    r = r if isinstance(r, dict) else {}
    lines = ["已导入: %s" % (r.get("path") or "?")]
    lines.append("  包含块: " + ", ".join(PART_LABEL.get(p, p) for p in (r.get("parts") or [])))
    if r.get("backup"):
        lines.append("  导入前备份: %s (%d 个文件)" % (r["backup"], r.get("backup_files") or 0))
    for name, info in (r.get("lists") or {}).items():
        lines.append("  列表 %s: %s 条%s"
                     % (name, info.get("count"), (" (新增 %d)" % info["added"]) if info.get("added") else ""))
    if r.get("keys"):
        lines.append("  配置键: " + ", ".join(r["keys"]))
    if r.get("n_scenarios"):
        lines.append("  剧本: %d 个" % r["n_scenarios"])
    for n in r.get("notes") or []:
        lines.append("  · " + n)
    return "\n".join(lines)
