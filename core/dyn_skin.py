"""动态皮肤(整套 Spine 皮肤) —— 单独一个核心模块 (v4.111★)。

为什么单开一个模块
==================
动态皮是【渲染资源】(skel 骨架 + atlas 切片表 + png 贴图), 跟普通"一张 png 换一张 png"
不是一回事:
  · png 替换: 换的是【像素】, 换完还是静态图 —— 资源替换页那一套;
  · 动态皮:   换的是【一整套动画资源】—— 游戏拿 atlas 里写死的坐标去切 png, 再按 skel
               播动画。少一个文件、或者三件套不同源, 出来就是错位碎块 / 直接空白。
两套东西塞在一个页面里配, 最常见的下场就是"以为换了动态皮、实际只换了一张贴图"。
→ 所以动态皮单独成模块、单独成页(『动态皮肤』), 跟资源替换页彻底分开。

能落到哪些位置(用户原话: "是换自走棋背景、和主公棋子")
======================================================
  A. 自走棋背景 / 身份场背景(tavernChess/*Bg.png、battleBg/*.jpg)
  B. 主公棋子、棋子立绘(tavernChess/general|piece/{skin}.png)
   这两处【本身是静态 png】, 所以落法是: 拿动态皮里某一帧的【贴图】去顶替那张 png ——
   得到的是动态皮的高清图(2048×2048 那一档), 但【不是动画】(动画要 Spine 渲染,
   URL 替换改不了渲染方式)。
  C. 整套映射(动态皮编号 → 另一套动态皮编号): 这个才是真换动画 —— skel+atlas+png
   一起指到同一个目标编号, 三件套同源, 游戏播出来就是另一套完整动画。

帧名/后缀为什么是探测出来的
==========================
官方皮肤表(GeneralSkinInfoConfig 的 SkinConf)里【没有】动态编号、也没有帧名单 ——
实测字段只有 SkinID / ItemName / Resource / Painter / Searchkey / IsBanner / BigResource
这几样。动态目录编号要拿 Resource 去试偏移(+20万 / +200万), 帧名更是【每套都不一样】
(见 resource_rules.DAIJI_VARIANTS) —— 所以这两个只能问服务器, 不能猜。
"""

import os
import sys
import urllib.request

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import resource_rules as RR
from core import silence as _SIL


# ---------------------------------------------------------------- 落点(换到哪)

def slot_options(kinds=None, season=None, include_past=True):
    """可换的落点清单 → [{kind, name, pattern, url, note, current}]。

    kinds: 只要这几类(None = 全要) —— "背景" / "主公立绘" / "棋子立绘";
    include_past: 主公/棋子连往季的名字一起列(换季前后名字带不带 _02 就是这么来的)。
    """
    out = []
    want = set(kinds) if kinds else None

    if not want or "背景" in want:
        for path, note, size in (RR.TAVERN_BG_PRESETS
                                 + getattr(RR, "NEWSTYLE_BG_PRESETS", ())
                                 + RR.IDENT_BG_PRESETS):
            # v4.137★ 这批背景【不带 runtime/】—— 实测:
            #     tavernChess/tavernChessBg.png              200 (1854KB)
            #     runtime/tavernChess/tavernChessBg.png      404
            #     newStyleTableScene/newStyleTableBg.png     200
            #     runtime/newStyleTableScene/…               404
            #   以前无脑补 runtime/ → 预览图/量尺寸/换图全都打在 404 上,
            #   表现就是"配好了, 游戏里一点反应都没有"。
            full = path
            out.append({
                "kind": "背景",
                "name": note,
                "pattern": RR.relative_pattern(path) or path,
                "url": RR.res_full_url(full),
                "note": size,
                "current": True,
            })

    if not want or ("主公立绘" in want or "棋子立绘" in want):
        try:
            opts = RR.official_skin_options(season) or []
        except Exception:
            opts = []
        for it in opts:
            k = it.get("kind")
            if k not in ("主公立绘", "棋子立绘"):
                continue
            if want and k not in want:
                continue
            if not include_past and not it.get("current"):
                continue
            out.append({
                "kind": k,
                "name": "%s(%s)" % (it.get("name") or it.get("skin"), it.get("skin")),
                "pattern": RR.relative_pattern(it.get("pattern") or "") or it.get("pattern"),
                "url": it.get("url") or "",
                "note": ("本赛季在用" if it.get("current") else "往季名(本季可能不请求)"),
                "current": bool(it.get("current")),
            })
    return out


# ---------------------------------------------------------------- 贴图(拿哪张图去换)

def frame_choices(suite):
    """整套探测结果 → 可以当"图"用的那些【贴图】→ [{file,url,size,hint}]。

    只有 .png 能拿去顶替静态 png(skel/atlas 塞进去就是坏文件)。
    hint 是给界面看的人话: beijing 是底图(适合当背景), daiji* 是待机帧(人物立绘)。
    """
    out = []
    for d in (suite or []):
        if str(d.get("ext") or "").lower() != "png":
            continue
        fr = str(d.get("frame") or "")
        if fr == "beijing":
            hint = "底图 → 适合换背景"
        elif fr.startswith("daiji"):
            hint = "待机帧(人物) → 适合换主公/棋子"
        else:
            hint = "贴图"
        if d.get("role") and "第" in str(d.get("role")):
            hint += " · %s" % d["role"]
        out.append({"file": d.get("file"), "url": d.get("url"),
                    "size": int(d.get("size") or 0), "hint": hint})
    return out


def rule_for_slot(slot, frame, skin_no=""):
    """一个落点 + 一张贴图 → 一条规则 dict(还没落盘)。

    名字写清楚来源(哪套皮的哪一帧), 不然后面在规则列表里分不清是谁配的。
    """
    nm = str(slot.get("name") or slot.get("pattern") or "")
    tag = ("动态皮%s/%s" % (skin_no, frame.get("file"))) if skin_no else str(frame.get("file"))
    return {
        "kind": slot.get("kind") or "",
        "name": "%s ← %s" % (nm, tag),
        "pattern": slot.get("pattern") or "",
        "target": frame.get("url") or "",
        "enabled": True,
    }


def apply_slot_rules(rules):
    """批量落盘(同 pattern 覆盖) → RR.upsert_rules 的 (总数, 新建, 覆盖)。"""
    rules = [r for r in (rules or []) if r.get("pattern") and r.get("target")]
    if not rules:
        return (0, 0, 0)
    return RR.upsert_rules(rules)


# ---------------------------------------------------------------- 整套映射(真换动画)

def suite_rules(src, dst, files):
    """整套映射的规则列表: 源每个文件 → 目标同名文件。

    files: 源里真存在的那些文件名(探测过的) —— 只映射这些, 不猜。
    """
    s = "".join(ch for ch in str(src or "") if ch.isdigit())
    d = "".join(ch for ch in str(dst or "") if ch.isdigit())
    if not s or not d or not files:
        return []
    out = []
    for f in files:
        f = str(f)
        out.append({
            "kind": "OL大图",
            "name": "动态皮 %s→%s %s" % (s, d, f),
            "pattern": "runtime/general/big/dynamic/%s/%s" % (s, f),
            "target": "%s/x/pc/res/assets/runtime/general/big/dynamic/%s/%s" % (RR.OFFICIAL_HOST, d, f),
            "enabled": True,
        })
    return out


def apply_suite(src, dst, files):
    """整套映射落盘 → (总数, 新建, 覆盖)。"""
    return apply_slot_rules(suite_rules(src, dst, files))


# ---------------------------------------------------------------- 探测(转发, 免得界面引两个模块)

def probe_suite(num, timeout=8.0, files=None):
    """这套动态皮里真存在的文件 → [{file,frame,ext,role,url,size}]。"""
    return RR.probe_dyn_suite(num, timeout=timeout, files=files)


def dyn_id_of_ref(ref, timeout=6.0):
    """皮肤表里挑出来的一条 → 这套的动态目录编号(探不到返回 "")。"""
    return RR.dyn_id_of_ref(ref, timeout=timeout)


# ---------------------------------------------------------------- 动态清单(不用猜有没有动态版)

def dyn_list():
    """【有动态的】皮肤清单 → [{id, no, big, name}] 按名字排(来源: data/dyn_flag.json)。

    这份表是建皮肤表时顺手存的(见 dress_table.dyn_flags_from_skinconf) —— 官方皮肤表
    5379 条里只有 327 条带 DynamicStandbySkel, 也就是说【五百多套里只有三百多套有动态版】。
    以前挑一套皮得联网猜它有没有动态版, 猜错就是白等几秒 + 一句"没探到"; 现在直接查表。
    """
    try:
        from core import dress_table as DT
    except Exception:      # noqa: BLE001
        return []
    try:
        m = DT.load_dyn_flags() or {}
    except Exception:      # noqa: BLE001
        return []
    out = []
    for sid, d in m.items():
        if not isinstance(d, dict):
            continue
        out.append({"id": str(sid), "no": int(d.get("no") or 0),
                    "big": int(d.get("big") or 0), "name": str(d.get("name") or "")})
    out = [x for x in out if x["no"] > 0]
    out.sort(key=lambda x: (x["name"], x["no"]))
    return out


def _norm_name(s):
    """名字归一: 只留字母数字汉字 —— '飞龙在天*关羽' 和 '飞龙在天(关羽)' 要能配上。"""
    return "".join(ch for ch in str(s or "") if ch.isalnum())


def dyn_no_by_name(name):
    """皮肤名(皮肤表挑出来的那条) → 这套的动态编号; 查不到返回 0。

    皮肤表挑出来的名字跟官方 ItemName 长得不完全一样(有的带括号/空格), 所以两边都
    归一后按【互为子串】比 —— 全等太脆, 差一个标点就配不上。
    """
    want = _norm_name(name)
    if not want:
        return 0
    for it in dyn_list():
        got = _norm_name(it.get("name"))
        if got and (got == want or (len(got) > 3 and (got in want or want in got))):
            return int(it.get("no") or 0)
    return 0


# ---------------------------------------------------------------- 下载整套(给本地 SpineViewer 看)

def download_suite(no, files, outdir="", timeout=60.0, on_each=None):
    """把这套的文件【下载到本地】 → (目录, 成功[], 失败[(file, 原因)])。

    为什么要有这个: 官方的 skel+atlas+png 三件套, 用本地的 SpineViewer 打开就能看动画
    (同目录拖进去即可) —— 比在浏览器里 F12 一条条捞省事。下载完的目录也能直接拿去
    当替换目标(本地文件那条路), 不依赖官方服务器在不在。

    files: 探测出来的文件名列表(只下真实存在的那些, 不猜)。
    """
    s = "".join(ch for ch in str(no or "") if ch.isdigit())
    if not s or not files:
        return ("", [], [])
    base = outdir or os.path.join(RR.DATA_DIR, "dyn_skins", s)
    try:
        os.makedirs(base, exist_ok=True)
    except Exception as e:      # noqa: BLE001
        return ("", [], [(str(f), "建目录失败: %s" % e) for f in files])
    ok, fail = [], []
    for f in files:
        f = str(f)
        try:
            url = RR._dyn_url(s, f)
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            data = urllib.request.urlopen(req, timeout=timeout).read()
            if not data:
                raise RuntimeError("空的")
            with open(os.path.join(base, f), "wb") as fp:
                fp.write(data)
            ok.append(f)
        except Exception as e:      # noqa: BLE001
            fail.append((f, str(e)))
        if on_each:
            try:
                on_each(f, len(ok), len(fail), len(files))
            except Exception: _SIL.note("core/dyn_skin.py", "on_each(进度回调) 抛异常 —— 不影响替换本身")
    return (base, ok, fail)
