# -*- coding: utf-8 -*-
"""v4.161★ 多开时的"名单 / 卡池数据"同步。

为什么需要: 多开之后每个实例都有自己的 data/, 于是
  · 招募名单 / 追牌名单 / 白黑名单 要一个号一个号地配一遍(5 个号 = 配 5 次);
  · 卡池数据(tavern_chess / cards / season 那些)每个实例各抓一次 —— 既慢又占
    空间, 还可能因为抓取时机不同导致各实例看到的卡池不一致。
同步 = 把某个实例(通常是 1 号机)的这些文件复制到其它实例, 一次配好全员生效。

为什么不做成"共享同一个目录": 名单这类文件是多进程【同时会写】的, 真共享目录
就是互相覆盖(这正是当初要把 data/ 按实例隔离的原因)。复制式同步没有并发写问题。
"""
import os
import shutil

from core.config import BASE_DIR

INST_ROOT = os.path.join(BASE_DIR, "instances")

# 名单本体(data/lists/ 整个目录)
LIST_DIRNAME = "lists"

# 各实例都要用、内容本该一致的"卡池 / 赛季 / 静态表"数据。
#   刻意【不含】: resource_rules.json(换肤规则, 各实例可能故意不同)、
#   net_*.json(协议流水, 是各自抓的包)、records.json/matchlogs(对局记录, 各是各的)、
#   tool_state.json(工具运行状态)。
POOL_FILES = [
    "tavern_chess.json", "tavern_chess_s10.json", "tavern_chess_s11.json",
    "cards.json", "chess.json", "chess_meta.json", "generals.json",
    "lords.json", "lord_skills.json", "skills.json", "items.json",
    "minion_types.json", "tavern_lords.json", "tavern_odds.json",
    "tavern_spells.json", "tavern_summary.json", "dress_table.json",
    "skins.json", "id_map.json", "discovered_ids.json",
    "discovered_ids_picker.json", "learned_pids.json", "card_pinyin.json",
    "lineup_counters.json", "lineup_positions.json", "hero_picker.json",
    "archive_lineups.json", "s10_lineups.json", "official_resources.json",
]
POOL_DIRS = ["season"]


def all_targets():
    """可同步的目标: [(显示名, data目录)] —— 助手根(单实例那套) + 每个实例目录。"""
    out = [("助手根(单实例)", os.path.join(BASE_DIR, "data"))]
    try:
        names = sorted(os.listdir(INST_ROOT))
    except Exception:
        names = []
    for n in names:
        d = os.path.join(INST_ROOT, n)
        if not os.path.isdir(d):
            continue
        # 只认"真的实例目录": instances/ 下还会混进 edge_<名字> 这种浏览器
        # profile 目录(--with-edge 建的), 把名单复制过去毫无意义。
        if not (os.path.exists(os.path.join(d, "config.json"))
                or os.path.isdir(os.path.join(d, "data"))):
            continue
        out.append((n, os.path.join(d, "data")))
    return out


def find_target(label):
    for name, d in all_targets():
        if name == label:
            return d
    return None


def _copy_file(src, dst_dir):
    try:
        os.makedirs(dst_dir, exist_ok=True)
        dst = os.path.join(dst_dir, os.path.basename(src))
        shutil.copy2(src, dst)
        return True
    except Exception:
        return False


def sync(src_dir, dst_dir, groups=("lists", "pool")):
    """把 src_dir(某个 data 目录) 的名单/卡池复制到 dst_dir。返回 (成功数, 失败数)。"""
    ok = fail = 0
    if "lists" in groups:
        s = os.path.join(src_dir, LIST_DIRNAME)
        if os.path.isdir(s):
            for fn in sorted(os.listdir(s)):
                if fn.lower().endswith(".json"):
                    if _copy_file(os.path.join(s, fn), os.path.join(dst_dir, LIST_DIRNAME)):
                        ok += 1
                    else:
                        fail += 1
    if "pool" in groups:
        for fn in POOL_FILES:
            src = os.path.join(src_dir, fn)
            if os.path.exists(src):
                if _copy_file(src, dst_dir):
                    ok += 1
                else:
                    fail += 1
        for dn in POOL_DIRS:
            s = os.path.join(src_dir, dn)
            if os.path.isdir(s):
                for fn in sorted(os.listdir(s)):
                    sp = os.path.join(s, fn)
                    if os.path.isfile(sp):
                        if _copy_file(sp, os.path.join(dst_dir, dn)):
                            ok += 1
                        else:
                            fail += 1
    return ok, fail


def sync_to_others(src_label, groups=("lists", "pool"), skip_labels=()):
    """从 src_label 同步到其它所有目标。返回 [(目标名, ok, fail)]。"""
    src = find_target(src_label)
    if not src:
        return []
    out = []
    for name, d in all_targets():
        if name == src_label or name in skip_labels:
            continue
        if not os.path.isdir(os.path.dirname(d)):
            continue
        ok, fail = sync(src, d, groups)
        out.append((name, ok, fail))
    return out
