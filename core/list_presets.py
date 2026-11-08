# -*- coding: utf-8 -*-
"""v4.153★ 招募 / 遣散 的『快速预设』

为什么要有这个模块
==================
用户每局要配的东西, 脑子里其实都是【一句话】:
    「太史慈吴遣散」= 除了孙尚香、太史慈, 剩下的吴都遣散;
    「找黄盖」      = 除了黄盖都遣散;
    「招募线」      = 祢衡、丁奉、陈珪。
但落到界面上, 每次都得在 3700 条的『全部武将』里点十几下、还得记得清的上次那份。
预设就是把这句话存下来: 下拉选中 → 一键应用 → 自动落盘并推油猴端。

两种预设的形态不一样, 所以要分开存:
    recruit  = {name, items}             —— 招募目标名单(名字/ID, 顿号逗号都认)
    dismiss  = {name, scope, keep}       —— 遣散: 范围限某势力(或全部) → 除保留名单外的全进去

存哪 / 为什么不进 config.json
============================
data/list_presets.json, 和 data/lists/*.json 同级。
config.json 里混着只对本机有效的东西(屏幕坐标、bridge.refresh_pos 校准值),
预设是纯内容 —— 换机器应该一起搬走, 不该被"本机配置"的导入导出带偏。

★ 范围限定到【本季】的说明见 season_target_ids(): 遣散名单里混进往季同名的 ID
  会让面板变成一屏看不懂的重复条目(同一个"黄盖"二十几个), 而它们根本刷不出来。
"""
import json
import os
import re

try:
    from core.config import DATA_DIR
except ImportError:
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from core.config import DATA_DIR

RECRUIT = "recruit"
DISMISS = "dismiss"
KINDS = (RECRUIT, DISMISS)

# ★ 用户确认过、以后不再提示的"非本季 ID"(见 season_check 的说明: 有的卡就是跨季的)
IGNORE_KEY = "ignore_not_season"

# 势力范围里的"不限"哨兵 —— 与 dual_list 的 _ALL_FACTION 同款
ALL_SCOPE = "全部势力"

_PATH = os.path.join(DATA_DIR, "list_presets.json")

# 第一次用时塞进去的例子。用户原话就是这四个:
#   「太史慈吴遣散就是除了孙尚香、太史慈都遣散、剩下的吴」「找黄盖就是除了黄盖都遣散」
#   「招募就是提前写好几个祢衡、丁奉、陈珪 / 孙策、陈珪」
# ★ 注意: 有 plain dict 便于用户照着改, 名字起得短是为了下拉里一眼认得出。
DEFAULT_PRESETS = {
    RECRUIT: [
        {"name": "祢衡/丁奉/陈珪", "items": "祢衡、丁奉、陈珪"},
        {"name": "孙策/陈珪", "items": "孙策、陈珪"},
    ],
    DISMISS: [
        {"name": "太史慈吴遣散", "scope": "吴", "keep": "孙尚香、太史慈"},
        {"name": "找黄盖", "scope": "吴", "keep": "黄盖"},
    ],
}

_SPLIT_RE = re.compile(r'[,，;；、\t\r\n]+')
_TAIL_ID_RE = re.compile(r'[（(]\d+[)）]\s*$')


# ---------------------------------------------------------------- 存取
def load_all():
    """读整份预设文件; 文件不存在时返回【默认例子】(并立刻落盘, 让用户有得改)。"""
    data = None
    try:
        with open(_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        # 第一次用才走到这 —— 是"还没有这个文件", 不是读坏了, 所以不刷报错:
        # 下面会拿默认例子建档。写成 except: pass 会被静态自检拦(那是"异常也能不留痕"),
        # 这里给 data 赋值带上语义。
        data = None
    except Exception as e:
        print("[预设] 读取失败(按默认重建): %s" % e)
        data = None
    if isinstance(data, dict):
        data.setdefault(RECRUIT, [])
        data.setdefault(DISMISS, [])
        data.setdefault(IGNORE_KEY, [])
        return data
    data = {k: [dict(p) for p in v] for k, v in DEFAULT_PRESETS.items()}
    save_all(data)
    return data


def load(kind):
    if kind not in KINDS:
        return []
    return [dict(p) for p in (load_all().get(kind) or [])]


def save_all(data):
    try:
        with open(_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print("[预设] 保存失败: %s" % e)
        return False


def save(kind, presets):
    if kind not in KINDS:
        return False
    data = load_all()
    data[kind] = [dict(p) for p in (presets or [])]
    return save_all(data)


def upsert(kind, preset):
    """按 name 覆盖写一条(重名=改这一条), 返回存后的完整列表。"""
    presets = load(kind)
    name = str((preset or {}).get("name") or "").strip()
    if not name:
        return presets
    for i, p in enumerate(presets):
        if str(p.get("name") or "").strip() == name:
            presets[i] = dict(preset)
            break
    else:
        presets.append(dict(preset))
    save(kind, presets)
    return presets


def delete(kind, name):
    presets = [p for p in load(kind) if str(p.get("name") or "").strip() != str(name or "").strip()]
    save(kind, presets)
    return presets


def get(kind, name):
    for p in load(kind):
        if str(p.get("name") or "").strip() == str(name or "").strip():
            return p
    return None


def load_ignore():
    """已确认过、不再提示的非本季 ID 集合。

    为什么需要它: 有【合法的】跨季 ID —— 用户黑名单里那条 20904231(黄巾兵, S9)
    就是典型, 还有随征里混的锦囊 ID、异格版本 chessID(见 dual_list._entry_from_text)。
    这些不是"解析错了", 一律判死等于逼用户每次都看一遍同一份警告。
    """
    try:
        return set(str(x) for x in (load_all().get(IGNORE_KEY) or []) if str(x).strip())
    except Exception as e:
        print("[预设] 读忽略名单失败: %s" % e)
        return set()


def add_ignore(ids):
    data = load_all()
    cur = set(str(x) for x in (data.get(IGNORE_KEY) or []) if str(x).strip())
    cur |= set(str(i).strip() for i in (ids or []) if str(i).strip())
    data[IGNORE_KEY] = sorted(cur)
    save_all(data)
    return data[IGNORE_KEY]


# ---------------------------------------------------------------- 文本/数据工具
def split_names(text):
    """把"孙尚香、太史慈"切成 ["孙尚香","太史慈"]; 也能吃 ID 和 "名 (ID)"。"""
    out = []
    for chunk in _SPLIT_RE.split(str(text or "")):
        t = chunk.strip().strip('"\'').strip()
        if t:
            out.append(t)
    return out


def clean_name(text):
    """去掉尾巴上的 (ID) —— 预设里允许直接粘 '孙策 (21103211)' 这种整条。"""
    return _TAIL_ID_RE.sub("", str(text or "").strip()).strip()


def season_target_ids(generals=None):
    """本季棋子的 ID 集合(含金卡那条) —— 遣散/招募默认只在本季里挑。

    为什么限定本季(实测 2026-10-24): 『全部武将』是【历赛季合表】(3718 条,
    "黄盖"一个人就有 20 条)。不限定的话, "吴势力除黄盖外全遣散"会一次灌进来
    几百条往季重复条目, 面板上一眼看不出哪个才是会刷出来的那张 —— 而商店只出本季卡,
    那些往季 ID 永远匹配不上, 纯噪音。
    """
    ids = None
    try:
        from core import id_map as IDMAP
        meta = IDMAP.season_pool_meta() or {}
        ids = set(str(k) for k in meta.keys() if str(k).strip())
    except Exception as e:
        print("[预设] 本季表读取失败: %s" % e)
        ids = None
    if ids:
        return ids
    # 兜底: 本季表整个没读到时, 退回 ID 前缀(S10→"210" / S11→"211", 见 id_map.season_prefix)
    try:
        from core import id_map as IDMAP
        prefix = IDMAP.season_prefix() or ""
    except Exception:
        prefix = ""
    if not prefix:
        return set(str(g.get("id") or "") for g in (generals or []) if g.get("id"))
    return set(str(g.get("id") or "") for g in (generals or [])
               if str(g.get("id") or "").startswith(prefix))


def factions_in(generals, limit_season=True):
    """当前武将池里实际存在的势力列表(有序)。限本季, 免得把往季的势力分页也列进来。"""
    ids = limit_season and season_target_ids(generals) or set()
    out = []
    for gen in (generals or []):
        f = str(gen.get("faction") or "").strip()
        if not f or f in out:
            continue
        if ids and str(gen.get("id") or "") not in ids:
            continue
        out.append(f)
    # 固定顺序更好认: 魏蜀吴群黄巾汉西凉袁
    order = ["魏", "蜀", "吴", "群", "黄巾", "汉", "西凉", "袁"]
    out.sort(key=lambda f: order.index(f) if f in order else 99)
    return out


def summarize(preset, kind):
    """下拉旁边那行小字: 让人不用点进去就知道这条预设干了什么。"""
    p = preset or {}
    if kind == DISMISS:
        return "范围=%s | 保留=%s" % (p.get("scope") or ALL_SCOPE,
                                      "、".join(split_names(p.get("keep"))) or "(无, 全遣散)")
    return "目标=%s" % ("、".join(split_names(p.get("items"))) or "(空)")


def rule_from_entries(generals, selected_ids):
    """把一份现成的遣散名单【反推】成 {scope, keep} 规则 —— 界面上『存当前』用。

    反推方法:
      范围 = 名单里这些卡所属的势力; 跨势力时不猜, 退回 ALL_SCOPE
      保留 = 【范围内】但没在该名单里的那些名字

    ★ 只在本季范围内反推(口径同 season_target_ids): 否则"黄盖"这种往季同名的二十几个
      ID 会一起涌进保留名单, 存出来的规则了一条"(假的)全保留"。

    返回 {"scope": str, "keep": "名、名"}
    """
    sel = set(str(i or "").strip() for i in (selected_ids or []))
    sel.discard("")
    if not sel:
        return {"scope": ALL_SCOPE, "keep": ""}
    pool = season_target_ids(generals)
    facs, cands = [], []          # cands = 范围内【没被选中】的 (名字, 势力)
    for gen in (generals or []):
        gid = str(gen.get("id") or "").strip()
        nm = str(gen.get("name") or "").strip()
        if not gid or not nm or (pool and gid not in pool):
            continue
        fac = str(gen.get("faction") or "").strip()
        if gid in sel:
            if fac and fac not in facs:
                facs.append(fac)
        else:
            cands.append((nm, fac))
    scope = facs[0] if len(facs) == 1 else ALL_SCOPE
    keep, seen = [], set()
    for nm, fac in cands:
        if nm in seen:
            continue
        if scope != ALL_SCOPE and fac != scope:
            continue          # 范围收窄到单一势力时, 别的势力不该被"顺手留下"
        seen.add(nm)
        keep.append(nm)
    return {"scope": scope, "keep": "、".join(keep)}


def is_season_id(gid, generals=None):
    """这个 ID 是不是【本季】的棋子 ID。

    返回 True/False; 不是 8 位纯数字(锦囊/随征/主公 ID)返回 None = 【不参与判定】
    —— 那根本不是棋子, 拿赛季前缀去比它只会刷一堆假警告。
    """
    s = str(gid or "").strip()
    if not (s.isdigit() and len(s) == 8):
        return None
    p = ""
    try:
        from core import id_map as IDMAP
        p = IDMAP.season_prefix() or ""
    except Exception as e:
        print("[预设] 赛季前缀读取失败: %s" % e)
    if p and s.startswith(p):
        return True
    return s in (season_target_ids(generals) or set())


def season_check(entries, generals=None, ignore=None):
    """应用预设前, 把【解析结果不是本季 ID】的点名挑出来。

    为什么要有这一步(换季静默失效): 招募预设是按【名字】解析 ID 的, 而武将池是
    历赛季合表 —— 某武将被移出本季商店后, 名字照样查得到(往季那条)、界面不报任何错,
    但商店永远刷不出这张卡, 这条预设就悄悄废了(用户原话: "有的武将就是不符合赛季ID")。

    返回 [(name, gid, kind, alt_id)]
      kind="dup"   往季 ID, 但本季池里有同名卡 → 建议换成本季那条(alt_id 已给出)
      kind="gone"  往季 ID, 且本季池里没这个名 → 商店大概率刷不出

      ★ 两类都【只报告不拦】: 黑名单里那条 20904231(黄巾兵, S9)就是用户故意留的
        跨季 ID, 异格版本 chessID 也是合法的(见 dual_list._entry_from_text 注释)。
        判死 = 逼用户每次都看同一份警告, 所以提供 ignore 名单让用户自己放行。
    """
    ignore = set(str(i).strip() for i in (ignore if ignore is not None else load_ignore()) if i)
    cur_by_name = {}
    for gen in (generals or []):
        gid = str(gen.get("id") or "")
        nm = str(gen.get("name") or "").strip()
        if not gid or not nm:
            continue
        if is_season_id(gid, generals):
            cur_by_name.setdefault(nm, gid)
    out = []
    for ent in (entries or []):
        gid = str(ent.get("id") or "").strip()
        nm = str(ent.get("name") or "").strip()
        if not gid or gid in ignore:
            continue
        if is_season_id(gid, generals) is not False:      # True(本季) / None(非棋子) 都不管
            continue
        alt = cur_by_name.get(nm) or ""
        out.append((nm, gid, "dup" if alt else "gone", alt))
    return out


def season_check_names(names, generals=None):
    """保留名单里的名字, 本季池里还有没有? 返回 [(名字, "gone")]。

    同一个坑的另一面: 遣散预设的『保留』写了本季已经没有的武将 —— 不会报错,
    只是这条保留等于没写(本季范围内根本没这张卡可留)。
    """
    cur, old = set(), set()
    for gen in (generals or []):
        nm = str(gen.get("name") or "").strip()
        if not nm:
            continue
        if is_season_id(gen.get("id"), generals):
            cur.add(nm)
        else:
            old.add(nm)
    return [(n, "gone") for n in (names or []) if n and n not in cur and n in old]


# ---------------------------------------------------------------- 应用: 遣散
def build_dismiss_entries(generals, scope, keep_text, limit_season=True):
    """按「范围 + 保留名单」算出遣散条目列表。

    参数
      generals  武将池 get_all_generals_for_ui() 那种 [{id,name,display,faction}, ...]
      scope     势力名(如"吴")或 ALL_SCOPE
      keep_text 保留名单原文(名字, 顿号/逗号分隔)

    返回 (entries, unknown, stats)
      entries   [{id,name,display}, ...] —— 直接给 DualListPanel.add_entries 用
      unknown   保留名单里【池子中查不到】的名字(写错字了), 界面要报出来
      stats     {"examined": 本季候选总数, "no_faction": 其中没有势力的,
                 "kept": 因保留名单被跳过的}
    """
    keep = set(clean_name(n) for n in split_names(keep_text))
    keep.discard("")
    pool = season_target_ids(generals) if limit_season else None
    name_set = set(str(g.get("name") or "").strip() for g in (generals or []))

    unknown = [n for n in keep if n not in name_set]
    entries, seen = [], set()
    examined = no_faction = kept = 0
    for gen in (generals or []):
        gid = str(gen.get("id") or "").strip()
        nm = str(gen.get("name") or "").strip()
        if not gid or not nm:
            continue
        if pool is not None and gid not in pool:
            continue
        examined += 1
        fac = str(gen.get("faction") or "").strip()
        if not fac:
            no_faction += 1
        if scope and scope != ALL_SCOPE and fac != scope:
            continue
        if nm in keep:
            kept += 1
            continue
        if gid in seen:
            continue
        seen.add(gid)
        entries.append({"id": gid, "name": nm,
                        "display": gen.get("display") or f"{nm} ({gid})"})
    return entries, unknown, {"examined": examined, "no_faction": no_faction, "kept": kept}
