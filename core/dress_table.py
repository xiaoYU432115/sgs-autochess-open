# -*- coding: utf-8 -*-
"""OL 皮肤 dress 表 —— "OL 大图按武将名挑"的数据源(v4.75★)。

v4.73 做贴网址时写死过一句结论: "OL 大图枚举不了, 编号在主游戏的 dress 皮肤表里"。
v4.75 这句话被推翻了一半 —— 表找到了, 而且【能抓到】:

  · 游戏客户端把全部配置打在一个 Config.sgs 里(明文 ZIP, 直链可下:
      https://web.sanguosha.com/10/pc/res/config/Config.sgs );
  · 里面的 GeneralSkinInfoConfig.sgs 成员还是加密的(.sgs OFB), 用 resc wasm 解
      (core/sgs_decrypt_util.py 那一套, 参考_sgs_forward_looking-main/resc 有钥匙);
  · 解出来的 GeneralSkinInfoConfig.json → SkinConf 有 5300+ 条:
      {"SkinID": 500103, "ItemName": "励战年兽*刘备", "BigResource": 500103, …}
    ItemName 是 "皮肤名*武将名", BigResource 是大图编号(空就用 SkinID 试 ——
    实测经典形象 SkinID 自己就是大图编号: static/100101.png 存在)。

大图地址模板(和 resource_rules.py 的 BIG_STATIC_URL/BIG_DYNAMIC_URL 同一份规律):
  静态: runtime/general/big/static/{编号}.png
  动态: runtime/general/big/dynamic/{动态编号}/{帧}.png —— 动态编号【不固定】:
    神马超 621401→821401 是 +20万, 威马超 5220501→7220501 是 +200万 —— 写死 +200000
    会让后者一张动态帧都探不到(表现就是"全选也只有静态那 1 张")。
    所以探测两种偏移都试, 并优先用表里指向同一 big 的兄弟编号(见 skin_candidates);
    帧有哪些同样要探测, 不猜(同一套皮肤通常只有 6 帧里的 3~4 帧真的存在)。

本模块只做四件事:
  1) 把 SkinConf 瘦身成本地表 data/dress_table.json(行 = [id, big, 皮肤名, 武将名]);
  2) update_online(): 下载 Config.sgs → 解成员 → 建表(需要 resc + wasmtime);
     update_from_file(): 用户自己喂一个 Config.sgs / 解好的 json 也行;
  3) load()/search(): 给界面按武将名/皮肤名搜;
  4) probe_skin(): 对选中的一套皮肤探测静态+动态帧哪几张真的存在(按需发 7 个请求,
     不给全表 5300 套挨个探测 —— 那是上万个请求)。

为什么瘦身而不是存原始 JSON: 原表 10MB+ 有画师/描述/活动跳转一堆用不上的字段;
本地表只留画地址必需的四样, 300KB 出头, 打进 exe 也不肉疼。
"""

import gzip
import io
import json
import os
import re
import sys
import time
import urllib.request
import zipfile

# 直接 `python core/dress_table.py` 跑自测时把项目根补进 sys.path(被 import 时不影响)
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import resource_rules as RR

TABLE_PATH = os.path.join(RR.DATA_DIR, "dress_table.json")
TABLE_V = 1

# v4.112★ 【哪些皮肤有动态】单独一份(不塞进瘦身表, 免得动到那四列的老结构)。
# 官方 SkinConf 里没有 IsDynamic, 但【有】DynamicStandbySkel —— 实测 5379 条里 327 条有值
#   (值恒为 1), 而且这一条的 Resource 就是【动态目录编号】(抽查 6/6 命中:
#   飞龙在天*关羽 Resource=700201 → dynamic/700201/daiji.png 真存在 3.99MB;
#   没标记的 100101 经典形象*刘备 → 那个目录压根没有)。
# → 有了这份清单, 挑一套皮【不用联网猜】它有没有动态版、动态编号是几, 直接查表。
DYN_FLAG_PATH = os.path.join(RR.DATA_DIR, "dyn_flag.json")

# Config.sgs 的直链(README 口径; 实测 2026-09 明文 ZIP 可直接下载)
CONFIG_URL = RR.OFFICIAL_HOST + "/10/pc/res/config/Config.sgs"
CONFIG_MEMBER = "GeneralSkinInfoConfig.sgs"

# 动态帧候选名单 —— 与 resource_rules.BIG_DYNAMIC_FRAMES 同一份, 引过来免得写两处
_DYNAMIC_FRAMES = RR.BIG_DYNAMIC_FRAMES
_DYNAMIC_OFFSET = RR.DYNAMIC_ID_OFFSET


# ---------------------------------------------------------------- resc(解密钥匙)定位

def resc_candidates():
    """resc wasm 可能出现的几个位置, 按优先级排。找不到就返回 []。

    打包版里 参考_sgs_forward_looking-main/ 多半没跟着走 —— 那种情况在线更新做不了
    (缺钥匙), 但【本地表还在】(打包时带的 data/dress_table.json), 按名挑照常能用。
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = [
        os.path.join(root, "参考_sgs_forward_looking-main", "resc"),
        os.path.join(root, "resc"),
        os.path.join(RR.DATA_DIR, "resc"),
    ]
    env = os.environ.get("SGS_RESC_WASM", "")
    if env:
        out.insert(0, env)
    return out


def resc_path():
    for p in resc_candidates():
        if os.path.isfile(p):
            return p
    return ""


def decrypt_capability():
    """解密能力检查 → ""(能解) 或 一句人话(为什么不能)。"""
    if not resc_path():
        return "找不到 resc 解密钥匙(参考_sgs_forward_looking-main/resc) —— 在线更新用不了, 本地表还能用"
    try:
        import wasmtime  # noqa: F401
    except ImportError:
        return "缺 wasmtime 库(pip install wasmtime) —— 在线更新用不了, 本地表还能用"
    return ""


# ---------------------------------------------------------------- .sgs 解密(bytes 级)

def _decrypt_payload(raw):
    """一段 .sgs 载荷 → (明文 bytes, 经历了几层加密)。

    复用 sgs_decrypt_util 的 wasm OFB 流程(与参考仓库 sgs_decrypt.py 同一套口径):
    明文/ZIP/GZIP 直接过; 否则解一层, 还不像就再解一层(上游脚本就是这么干的)。
    """
    from core import sgs_decrypt_util as SG
    if SG._looks_like_zip(raw) or SG._looks_like_gzip(raw) or SG._looks_like_json(raw):
        return raw, 0
    why = decrypt_capability()
    if why:
        raise RuntimeError(why)
    ctx = SG._load_wasm(resc_path())
    d1 = SG._ofb_decrypt(raw, ctx)
    if SG._looks_like_zip(d1) or SG._looks_like_gzip(d1) or SG._looks_like_json(d1):
        return d1, 1
    d2 = SG._ofb_decrypt(d1, ctx)
    if SG._looks_like_zip(d2) or SG._looks_like_gzip(d2) or SG._looks_like_json(d2):
        return d2, 2
    raise RuntimeError("解了两层都不是认识的格式(zip/gzip/json) —— 这份文件不是标准 .sgs?")


def _to_text(data):
    """明文 bytes → 文本(是 gzip 就先解; 都不是就当 utf-8 文本)。"""
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return data.decode("utf-8", "replace")


# ---------------------------------------------------------------- Config.sgs → 皮肤表

def slim_from_skinconf(d):
    """GeneralSkinInfoConfig dict → 瘦身行列表 [[id, big, 皮肤名, 武将名], …]。

    · ItemName 是 "皮肤名*武将名"(可能有多个 * —— 以最后一个切, 前面的都算皮肤名);
    · big = BigResource, 没有就 0(探测时用 SkinID 兜底 —— 经典形象的 SkinID 就是大图编号);
    · 排序: 武将名 → id, 界面上同一个武将的几套皮肤挨在一起。
    """
    rows = []
    for it in (d.get("SkinConf") or []):
        if not isinstance(it, dict):
            continue
        try:
            sid = int(it.get("SkinID") or 0)
        except Exception:
            continue
        if sid <= 0:
            continue
        try:
            big = int(it.get("BigResource") or 0)
        except Exception:
            big = 0
        nm = str(it.get("ItemName") or "").strip()
        if "*" in nm:
            title, gen = nm.rsplit("*", 1)
        else:
            title, gen = "", nm
        rows.append([sid, big, title.strip(), gen.strip()])
    rows.sort(key=lambda x: (x[3], x[0]))
    return rows


def _parse_skinconf_text(text):
    d = json.loads(text)
    if isinstance(d, dict) and isinstance(d.get("SkinConf"), list):
        return d
    if isinstance(d, list) and d and isinstance(d[0], dict) and "SkinID" in d[0]:
        return {"SkinConf": d}          # 有人直接把 SkinConf 数组喂进来也认
    raise RuntimeError("这份 JSON 里没有 SkinConf —— 要 GeneralSkinInfoConfig 的内容")


def dyn_flags_from_skinconf(d):
    """GeneralSkinInfoConfig dict → {skinId: {no, big, name}} —— 只收【有动态】的那批。

    · 判据就是 DynamicStandbySkel 有值(官方【没有】IsDynamic 这个字段, 别去找);
    · no = Resource —— 实测它就是 dynamic/ 目录编号(抽查 6/6 命中), 所以拿到直接用,
      不用再 "+20万 / +200万" 两个偏移各试一遍(试错的一半请求省下来了);
    · big = BigResource —— 静态大图编号(拿贴图换背景那一步要用)。
    """
    out = {}
    for it in (d.get("SkinConf") or []):
        if not isinstance(it, dict) or not it.get("DynamicStandbySkel"):
            continue
        try:
            sid = int(it.get("SkinID") or 0)
            no = int(it.get("Resource") or 0)
        except Exception:
            continue
        if sid <= 0 or no <= 0:
            continue
        try:
            big = int(it.get("BigResource") or 0)
        except Exception:
            big = 0
        out[str(sid)] = {"no": no, "big": big, "name": str(it.get("ItemName") or "").strip()}
    return out


def _save_dyn_flags(flags, source=""):
    """写 data/dyn_flag.json。写失败不算建表失败 —— 只是没这份索引, 退回老办法猜。"""
    if not flags:
        return 0
    try:
        json.dump({"v": 1, "source": source, "count": len(flags), "map": flags},
                  open(DYN_FLAG_PATH, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        return 0
    return len(flags)


def load_dyn_flags():
    """→ {skinId: {no, big, name}}; 没这份文件或坏了 → {}(调用方退回探测)。"""
    try:
        d = json.load(open(DYN_FLAG_PATH, encoding="utf-8"))
    except Exception:
        return {}
    return (d or {}).get("map") or {}


def build_from_any(raw, source=""):
    """任意形态的输入 → 瘦身表(写盘) → 摘要 dict。

    认四种输入(自动判断, 不用用户选):
      ① Config.sgs 整包(明文 ZIP, 成员还是加密的);
      ② 单个加密 .sgs 成员(GeneralSkinInfoConfig.sgs);
      ③ 解好的 gzip/明文 JSON;
      ④ 已经是瘦身表的 json(仅 load 场景, 不走这里)。
    """
    raw = bytes(raw or b"")
    if not raw:
        return {"ok": False, "error": "没收到内容"}
    # ① 整包 ZIP: 挑出成员再走 ②
    if raw[:4] == b"PK\x03\x04":
        try:
            zf = zipfile.ZipFile(io.BytesIO(raw))
        except Exception as e:
            return {"ok": False, "error": "ZIP 打不开: %s" % e}
        names = zf.namelist()
        hit = CONFIG_MEMBER if CONFIG_MEMBER in names else next(
            (n for n in names if "GeneralSkinInfoConfig" in n), "")
        if not hit:
            return {"ok": False, "error": "包里没有 %s —— 这不像 Config.sgs(有 %d 个文件: %s…)"
                                             % (CONFIG_MEMBER, len(names), ", ".join(names[:3]))}
        raw = zf.read(hit)
        source = source or ("Config.sgs 内的 " + hit)
    try:
        plain, layers = _decrypt_payload(raw)
        text = _to_text(plain)
        parsed = _parse_skinconf_text(text)
        slim = slim_from_skinconf(parsed)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    if not slim:
        return {"ok": False, "error": "表是空的(SkinConf 一条都没有) —— 这份文件对吗?"}
    _save_table(slim, source=source)
    # v4.112★ 顺手把【哪些皮肤有动态】也记一份(见下面 dyn_flags_from_skinconf)。
    #   建表只留四列(编号/大图/皮肤名/武将名), 动态标记在这一步就丢没了 ——
    #   丢了的后果是: 挑一套皮还得联网去猜它有没有动态版(猜错就是白等 + 404)。
    flags = dyn_flags_from_skinconf(parsed)
    _save_dyn_flags(flags, source=source)
    return {"ok": True, "count": len(slim), "source": source, "layers": layers,
            "dyn": len(flags)}


def update_online(timeout=120):
    """从官方直链抓 Config.sgs 建表 → 摘要。失败给一句人话。"""
    why = decrypt_capability()
    if why:
        return {"ok": False, "error": why}
    try:
        req = urllib.request.Request(CONFIG_URL, headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=timeout).read()
    except Exception as e:
        return {"ok": False, "error": "下载 %s 失败: %s" % (CONFIG_URL, e)}
    return build_from_any(raw, source=CONFIG_URL)


def update_from_file(path):
    p = str(path or "").strip()
    if not os.path.isfile(p):
        return {"ok": False, "error": "文件不存在: %s" % p}
    try:
        with open(p, "rb") as f:
            raw = f.read()
    except OSError as e:
        return {"ok": False, "error": "读不了: %s" % e}
    return build_from_any(raw, source=p)


# ---------------------------------------------------------------- 本地表读写/查询

def _save_table(rows, source=""):
    os.makedirs(os.path.dirname(TABLE_PATH), exist_ok=True)
    _save_json(TABLE_PATH, {"v": TABLE_V, "updated": int(time.time()),
                            "src": source, "count": len(rows), "skins": rows})


def _load_json(path, default):
    # v4.109★ 改走 core.utils.load_json_file(见 core/atlas.py 同名函数处的说明)
    from core.utils import load_json_file
    return load_json_file(path, default)


def _save_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def load():
    """读本地表 → {"loaded","updated","src","count","skins"}。没表/坏了 → loaded=False。"""
    d = _load_json(TABLE_PATH, {})
    rows = d.get("skins") if isinstance(d, dict) else None
    if not isinstance(rows, list) or not rows:
        return {"loaded": False, "updated": 0, "src": "", "count": 0, "skins": []}
    return {"loaded": True, "updated": int(d.get("updated") or 0),
            "src": str(d.get("src") or ""), "count": len(rows), "skins": rows}


def search(q, limit=80):
    """按 武将名/皮肤名/编号 搜 → [{"id","big","title","name"}]。空 q 返回前 limit 条。"""
    t = load()
    kw = str(q or "").strip().lower()
    out = []
    for row in t["skins"]:
        try:
            sid, big, title, gen = int(row[0]), int(row[1] or 0), str(row[2]), str(row[3])
        except Exception:
            continue
        if kw and kw not in (gen + " " + title + " " + str(sid)).lower():
            continue
        out.append({"id": sid, "big": big, "title": title, "name": gen})
        if len(out) >= int(limit or 80):
            break
    return out


# ---------------------------------------------------------------- 探测一套皮肤的真身

def skin_candidates(skin_id):
    """一套皮肤的编号 → 可能存在的官方地址(静态 + 动态帧), 不连号探测。

    编号口径: BigResource 优先, 没有就 SkinID(经典形象的实测规律, 见文件头注释)。

    v4.110★ 【动态编号不是固定 +200000】—— 这是"全选也只看到一个"的真凶:
      老样本 神马超 621401 → 动态 821401(+200000) 照着写死就翻车了:
      威马超 静态 5220501, 它的动态帧实际在 dynamic/7220501/ 下(+2000000!),
      按 +200000 算出来是 5420501 —— 官方没这个文件, 探测只回 1 张静态图,
      于是列表里永远只有 1 行, 用户"全选"也就是选中 1 行 → 只建出 1 条规则。
    好在这张表里【动态皮肤本来就是单独一条】(row[0]=7220501, row[1]=5220501
      指回静态大图), 所以"指向同一 big 的兄弟编号"就是现成的动态编号候选,
      不必猜偏移; 两种偏移也一起试, 兜住表里没单独列条目的情况。
    """
    t = load()
    big = 0
    try:
        sid = int(skin_id)
    except Exception:
        sid = -1
    for row in t["skins"]:
        try:
            if int(row[0]) == sid:
                big = int(row[1] or 0)
                break
        except Exception:
            continue
    no = big or (sid if sid > 0 else 0)
    if not no:
        return []
    # 兄弟编号: 表里指向同一个大图编号的其它条目(动态版 / 同一套的其它形态)
    sibs = []
    for row in t["skins"]:
        try:
            v = int(row[0])
            b2 = int(row[1] or 0)
        except Exception:
            continue
        if v == no:
            continue
        if b2 == no or (big and b2 == big) or v == sid:
            if v not in sibs:
                sibs.append(v)
    dyn_ids = []
    for d in [no + _DYNAMIC_OFFSET, no + 2000000] + sorted(sibs):
        d = int(d)
        if d > 0 and d not in dyn_ids:
            dyn_ids.append(d)
    out = [RR.BIG_STATIC_URL.format(id=str(no))]
    for did in dyn_ids[:4]:                 # 最多试 4 个动态编号, 再多探测就太慢
        for fr in _DYNAMIC_FRAMES:
            out.append(RR.BIG_DYNAMIC_URL.format(id=did, frame=fr))
    seen = set()
    res = []
    for u in out:                           # 去重但保持顺序(静态永远排第一)
        if u not in seen:
            seen.add(u)
            res.append(u)
    return res


def probe_skin(skin_id, timeout=8.0):
    """把一套皮肤的候选地址挨个探一遍 → 只留活着的。

    每条复用 RR.probe_res_url(HEAD 问存在 + 前 4KB 量尺寸) —— 结论和界面"量尺寸"
      那两列同一套口径。

    v4.110★ 候选从 7 个涨到最多 25 个(静态 1 + 4 个动态编号 × 6 帧), 串行要等半分钟,
      改成并发(8 线程) —— 结果仍按候选顺序返回, 界面上"静态大图排最前"不变。
      全表探测(5300×25 个请求)依然明确不做。
    """
    urls = skin_candidates(skin_id)
    if not urls:
        return []

    def _one(u):
        try:
            return u, RR.probe_res_url(u, timeout=timeout)
        except Exception as e:               # 单个地址炸了不影响其余候选
            return u, {"ok": False, "note": str(e)}

    infos = {}
    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(8, max(1, len(urls)))) as ex:
            for u, info in ex.map(_one, urls):
                infos[u] = info
    except Exception:                        # 没有线程池也别让功能挂掉
        for u in urls:
            infos[u] = _one(u)[1]

    out = []
    for u in urls:
        info = infos.get(u) or {}
        if not info.get("ok"):
            continue
        rel = (RR.official_pattern_from_url(u) or {}).get("pattern") or RR.relative_pattern(u)
        frame = ""
        m = re.search(r"/dynamic/\d+/([a-z0-9_]+)\.png$", u, re.I)
        if m:
            frame = m.group(1)
        out.append({"url": u, "pattern": rel, "frame": frame,
                    "dynamic": bool(frame),
                    "w": info.get("w"), "h": info.get("h"), "fmt": info.get("fmt")})
    return out


if __name__ == "__main__":
    # 手动跑: python core/dress_table.py [某个 Config.sgs 路径 | 省略 = 在线抓]
    if len(sys.argv) > 1:
        info = update_from_file(sys.argv[1])
    else:
        info = update_online()
    print(json.dumps(info, ensure_ascii=False))
    if info.get("ok"):
        for it in search("刘备")[:6]:
            print(it)
