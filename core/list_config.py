"""v4.10★ 武将列表配置管理
6 个武将列表分开存 json，config.json 引用，自动加载保留修改。
列表文件存放在 data/lists/ 目录下：
  - auto_buy.json       自动招募武将
  - multi_buy.json      多选购买武将
  - permanent_keep.json 永久保留武将
  - discard_target.json 遣散目标武将
  - followup.json       随征ID
  - whitelist.json      白名单
  - blacklist.json      黑名单
每个文件格式: {"ids": ["2001", "2002"], "names": ["吕蒙", "陆逊"]}
"""
import os
import json

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
LISTS_DIR = os.path.join(DATA_DIR, "lists")

LIST_FILES = {
    "auto_buy": "auto_buy.json",
    "multi_buy": "multi_buy.json",
    "permanent_keep": "permanent_keep.json",
    "discard_target": "discard_target.json",
    "followup": "followup.json",
    "whitelist": "whitelist.json",
    "blacklist": "blacklist.json",
}


def _ensure_dir():
    os.makedirs(LISTS_DIR, exist_ok=True)


def load_list(list_name):
    """加载某个列表配置，返回 {"ids": [...], "names": [...]}"""
    _ensure_dir()
    fname = LIST_FILES.get(list_name)
    if not fname:
        return {"ids": [], "names": []}
    path = os.path.join(LISTS_DIR, fname)
    if not os.path.exists(path):
        return {"ids": [], "names": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                return {"ids": [], "names": []}
            data.setdefault("ids", [])
            data.setdefault("names", [])
            return data
    except Exception:
        return {"ids": [], "names": []}


def save_list(list_name, ids, names=None):
    """保存某个列表配置"""
    _ensure_dir()
    fname = LIST_FILES.get(list_name)
    if not fname:
        return False
    path = os.path.join(LISTS_DIR, fname)
    data = {"ids": [str(i) for i in (ids or [])], "names": list(names or [])}
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def load_all_lists():
    """加载所有 6 个列表，返回 {list_name: {"ids":..., "names":...}}"""
    return {name: load_list(name) for name in LIST_FILES}


def save_all_lists(configs):
    """批量保存 {list_name: {"ids":..., "names":...}}"""
    for name, cfg in configs.items():
        save_list(name, cfg.get("ids", []), cfg.get("names", []))
