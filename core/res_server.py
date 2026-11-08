# -*- coding: utf-8 -*-
"""v4.69★ 本地图片服务 —— 把「我的图/」挂到 http://127.0.0.1:<port>/ 上。

为什么非要有它(用户原话: "然后这个图片是另起一个服务端口吗? 在那个端口整图片? 还是就是本地的?")
================================================================================
替换规则的目标(target)有两种写法, 各有各的毛病:
  · 本地文件  file:///D:/…/我的图/luxun.png
      —— 游戏页是 https 的, 从 https 跳到 file:// 大多数浏览器【直接拒绝】(跨协议/本地文件访问),
         表现是"规则配了、图也放了, 但就是不生效", 而且浏览器控制台不一定报得清楚;
  · 网络地址  https://…(官方皮肤)
      —— 这个能用, 但只能指向别人服务器上已有的图。
所以第三种、也是最稳的一种: **本机起一个小 http 服务**, 规则目标写成
  http://127.0.0.1:<这里的端口>/img/luxun.png     ← 端口由 ensure() 定(默认见下面的 PORT 常量)
同源策略/协议跳转的问题全没了, 图还是 我的图/ 里那一张(不复制第二份)。
(★ 文档里刻意不写死端口数字: 端口是可以换的, 写死会让"改端口"这件事看起来做不到 ——
 静态自检 S5 也是因为这个把它当问题报出来。)

同一个服务顺带提供三样东西(都是为了"别再手工折腾")
  /                一个说明页, 告诉你当前端口、规则条数、下一步点哪
  /rules          规则 JSON —— 给【皮肤替换脚本】自动读取(见 bridge/sgs_skin_swap.user.js)。
                   有了它, 以后改规则【不用再去 Redirector/GoRes 重新导入】。
  /sgs_skin_swap.user.js   同一个脚本的安装入口(和 serve_userscript 一个套路)。

★ v4.70 起这里的三样东西**已经挂到助手主端口上了**(见 core/bridge.py 的 do_GET):
  http://127.0.0.1:<桥接端口>/rules · /img/xxx.png · /sgs_bridge.user.js
  原因: 上一版要在界面上手点「启动图片服务」才会起这个 8767, 忘了点 → 规则目标指向一个
  没在监听的端口 → 现象是"图配好了却没生效", 而且看不出是谁的锅。
  桥接服务是助手必开的, 所以把路由挂到它身上之后:
    · 端口只有一个, 不用记、不用点;
    · 规则目标里的地址 = 桥接地址, 一定活着;
    · 本模块的独立服务保留(命令行 `python core/res_server.py` / 只想单跑图片服务时用),
      两边共用下面那几个**纯函数**(rules_json / read_image / read_script), 不会各写一份。

端口约定与 serve_userscript.py 保持一致的那几条(踩过的坑也一起搬过来):
  · 端口必须记住: 8767 被占就退到 8768/8769/…, 调用方一律用 ensure() 返回的 port;
  · 重复点击/重复起服务由本模块挡住, 不会越开越多;
  · 响应头【不能】带 Content-Disposition: attachment(那会变成下载, Tampermonkey 拦不到)。
"""
import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from core import resource_rules as RR
from core import silence as _SIL

PORT = 8767
BRIDGE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bridge")
SWAP_SCRIPT = "sgs_skin_swap.user.js"
EXT_DIR_NAME = "res_ext"          # 轻量版扩展(只做网络层重定向)所在子目录
EXT_ALL_DIR_NAME = "sgs_ext"      # 全能版扩展(油猴脚本本体 + 重定向 + 管理页)所在子目录
EXT_ENTRY = "manifest.json"       # 有它才算一个能加载的扩展

_CTYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
    ".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".wav": "audio/wav",
    ".mp4": "video/mp4", ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8", ".txt": "text/plain; charset=utf-8",
}

# 图片服务自己记住"当前用哪个端口填进规则里"(用户可以在界面上改这个数字)
_active_port = None

# v4.87★ 【进程启动时刻】 —— 用来判断"你现在看到的页面是不是旧代码生成的":
#   助手是 Python 程序, 改完 .py 必须【重启助手】才生效; 而界面上「独立图片服务」是在
#   助手【进程内】起线程(RS.ensure()), 不是另开进程 —— 所以不重启的话, 打开的页面永远是
#   旧代码生成的(表现为"页面上没有新按钮 / 按钮点了没反应")。更隐蔽的一种: 8767 被
#   【昨天跑的旧进程】占着, ensure() 以为"服务已在跑"就直接复用它 → 你连上了旧进程。
#   把这两个时间摆在页面上, 一眼就能看出该不该重启。
_BOOT_TS = time.time()
_BOOT_STR = time.strftime("%m-%d %H:%M:%S", time.localtime(_BOOT_TS))


def base_url(port=None):
    p = port or _active_port or PORT
    return "http://127.0.0.1:%d" % int(p)


def rules_json(base=None):
    """给脚本读的规则: 只含【准备好了的】规则(跟给扩展用的 txt 同一口径)。

    base = 本机服务的地址前缀(http://127.0.0.1:<端口>)。**由调用方传进来更准**:
      bridge.py 把它挂在自己那个端口上, 它传自己; 本模块的独立服务传自己那个。
      不传就用默认端口 —— 那只是给命令行/自测兜底, 不是给主链路用的。
    """
    from core import csv_loader
    b = str(base or "").rstrip("/") or base_url()
    season = RR.norm_season(csv_loader.CURRENT_SEASON) or "current"
    # v4.87★ 即时注入: 总开关 + 类别默认模式(没开 = 全部按 url 下发, 和以前完全一致)
    try:
        from core import config as _cfg
        _ri = (_cfg.load_config() or {}).get("res_inject") or {}
        _inject_on = bool(_ri.get("on"))
        _modes = _ri.get("modes") or {}
    except Exception:
        _inject_on, _modes = False, {}
    out = []
    stamp = []
    for r in RR.build_rules(season):
        pat = str(r.get("pattern") or "").strip()
        tgt = str(r.get("target") or "").strip()
        if not pat or not tgt:
            continue
        if RR.is_remote_target(tgt):
            url = tgt
        else:
            if not RR.target_exists(tgt):
                continue
            name = os.path.basename(tgt)
            try:
                st = os.stat(tgt)
                stamp.append("%s|%d|%d" % (name, int(st.st_size), int(st.st_mtime)))
            except Exception:
                stamp.append(name)
            url = "%s/img/%s" % (b, name)
        item = {"pattern": pat, "url": url, "kind": r.get("kind") or "", "name": r.get("name") or ""}
        # v4.87★ 即时注入: 脚本靠 mode 分流(url 走改地址 / inject 走换纹理), voice 是点击播的语音
        _md = RR.rule_mode(r, _modes, _inject_on)
        if _md != RR.MODE_URL:
            item["mode"] = _md
        _vc = str(r.get("voice") or "").strip()
        if _vc:
            if RR.is_remote_target(_vc):
                item["voice"] = _vc
            else:
                item["voice"] = "%s/img/%s" % (b, os.path.basename(_vc))
        stamp.append("%s@%s/%s" % (pat, _md, _vc))
        # v4.86★ 伪动态背景(多帧轮播): 帧给的是【文件名】(跟规则目标一个口径), 脚本那边
        #   拼上 base + '/img/' 再去取; 只认 ≥2 帧的(1 帧谈不上动画)。
        an = r.get("animate")
        if isinstance(an, dict) and len(an.get("urls") or []) >= 2:
            try:
                ms = int(an.get("ms") or 500)
            except Exception:
                ms = 500
            item["animate"] = {"urls": [str(x) for x in (an.get("urls") or [])][:24],
                               "ms": max(80, min(10000, ms))}
            stamp.append("%s=>anim%d/%d" % (pat, len(item["animate"]["urls"]), ms))
        out.append(item)
    import hashlib
    # v4.74★ rev 不再只看图片文件的时间戳: 把【每条规则的 pattern→url 映射】也算进去。
    #   为什么要改 —— "立马更新"的破缓存靠 rev, 但旧算法只对 我的图/ 的文件变化敏感:
    #   用户把某条规则的目标从图 A 改成图 B(或改成官方立绘), 文件名集合没变,
    #   rev 不动 → 脚本/扩展那边拿 ?r=旧rev 当缓存键, 游戏里还是旧图。
    for r in out:
        stamp.append("%s=>%s" % (r["pattern"], r["url"]))
    rev = hashlib.md5((";".join(sorted(stamp)) + "|" + str(len(out))).encode("utf-8")).hexdigest()[:8]
    return {"season": season, "count": len(out), "base": b, "rev": rev, "rules": out}


# 旧名保留: 这两个名字在别的模块/文档里出现过, 改名的收益不如留个别名
_rules_payload = rules_json


def skins_json():
    """给【全能扩展管理页「官方立绘」】用的清单 —— 与界面「挑选官方立绘」同一个来源。

    为什么值得一个独立端点:
      扩展管理页要的是"整张候选表"(581 套, 带 current 标记), 而 /rules 里只有
      "已经配了替换的"那几十条 —— 拿规则表当候选表, 用户能挑的只剩自己配过的。
    返回 {"season", "count", "kinds", "skins":[{kind,name,skin,pattern,url,season,current}]}
    """
    from core import csv_loader
    season = RR.norm_season(csv_loader.CURRENT_SEASON) or "current"
    opts = RR.official_skin_options()
    kinds = []
    for o in opts:
        k = str(o.get("kind") or "")
        if k and k not in kinds:
            kinds.append(k)
    return {"season": season, "count": len(opts), "kinds": kinds, "skins": opts}


def dynbg_json(base=None):
    """给脚本读的【动态背景】配置(v4.113★)。

    三件套的地址一律指回本机(/dyn/<no>/<file>) —— 游戏页是 https, 脚本在里面直接取官方
    域名的 skel/atlas/png 会被 CORS 挡掉(官方静态资源不带 CORS 头); 走本机同源同协议就没
    这个问题(见 read_asset)。

    没开 / 没填编号 / 三件套没下载 → 一律返回 {"on": False, "err": ...}: 让脚本那边直接
    歇着, 别去猜一个半吊子配置然后播出一堆碎块。
    """
    try:
        from core import config as _cfg
        d = dict((_cfg.load_config() or {}).get("dyn_bg") or {})
    except Exception as e:
        return {"on": False, "err": "读配置失败: %s" % e}
    if not d.get("on"):
        return {"on": False, "err": "动态背景开关没开"}
    no = re.sub(r"\D", "", str(d.get("no") or ""))
    if not no:
        return {"on": False, "err": "没填动态皮编号"}
    frame = str(d.get("frame") or "beijing").strip() or "beijing"
    try:
        scale = float(d.get("scale"))
    except Exception:
        scale = None
    b = str(base or "").rstrip("/") or base_url()
    out = {
        "on": True, "no": no, "frame": frame,
        "anim": str(d.get("anim") or "").strip(),
        "pattern": str(d.get("pattern") or "").strip(),
        # v4.138★ "人物背景"开关: 勾上才连骨架自带的 beijing 底图一起播
        #   (它是不透明的, 会把游戏原背景整块盖住); 不勾 = 只播人物, 背景用游戏原图。
        "useBg": bool(d.get("useBg", False)),
        # v4.139★ 助手端那几个开关一并下发(见脚本 onCfg 的说明):
        #   hideLayers 隐藏骨架图层(上衣/袖子这类), stripBg 摘掉游戏原背景(等于"背景设透明"),
        #   bgZoom 底图在自动铺满之上再放大几倍(盖住动画晃出来的边)。
        #   空值一律不下发 —— 脚本那边的规矩是"助手给了才改", 发空值会把本地手搓值冲掉。
        "hideLayers": [str(x).strip() for x in (d.get("hideLayers") or []) if str(x).strip()],
        "stripBg": bool(d.get("stripBg", False)) if d.get("stripBg") not in (None, "") else "",
        "bgZoom": (float(d.get("bgZoom")) if str(d.get("bgZoom") or "").strip() else ""),
        # v4.163★ scale/x/y "助手给了才改": 等于出厂默认(1.0/0/0)就发空串, 免得把脚本
        #   本地手搓值 / 代码默认(人物 0.9)冲掉; 非默认才下发, 桥接据此当默认(见 start/onCfg)。
        "scale": ("" if scale is None or abs(scale - 1.0) < 1e-9 else scale),
        "x": ("" if not d.get("x") else d.get("x")),
        "y": ("" if not d.get("y") else d.get("y")),
        "player": "%s/lib/spine-player.min.js" % b,
        "css": "%s/lib/spine-player.min.css" % b,
    }
    # 三件套有没有真的下载到本地 —— 缺哪个列哪个, 别让脚本白等三个 404
    # v4.134★ 骨架有【两种格式】, 官方两种并存:
    #   .sk   = Laya 二进制(老皮) —— 游戏自己的加载器认, 实测能一路走到 _parseComplete;
    #   .skel = Spine 二进制(新皮) —— 游戏那套解析器读不了(getUint16 out of bounds)。
    # 所以这里两种都探, 探到哪个用哪个, 并把 fmt 告诉脚本 —— 脚本据此分流:
    #   fmt=sk   → 【游戏内】SgsSkeletonEffect.playEffect(不插 DOM 层, 不挡 UI)
    #   fmt=skel → 原来的 DOM spine-player(画布下层)
    sk_name = ""
    for ext in (".sk", ".skel"):
        if os.path.isfile(os.path.join(RR.DATA_DIR, "dyn_skins", no, frame + ext)):
            sk_name = frame + ext
            break
    out["fmt"] = os.path.splitext(sk_name)[1].lstrip(".") if sk_name else ""
    out["sk"] = ("%s/dyn/%s/%s" % (b, no, sk_name)) if sk_name else ""
    # v4.136★ 底图层: 动态皮自带的 beijing 帧(底图/场景)。
    #   只播人物帧(daiji*)的话, 人物是"悬在空背景上"的 —— 实测观感很差, 而且用户每次
    #   刷新都得靠控制台手搓一层背景。所以这里连底图一起给, 脚本同时播两层:
    #   beijing 铺满在下(z=0), 人物帧居中在上(z=1)。
    #   当前选的就是 beijing、或底图没下载到 → 不给(只播一层, 行为同以前)。
    out["bgSk"] = ""
    out["bgFmt"] = ""
    if frame != "beijing":
        for ext in (".sk", ".skel"):
            if os.path.isfile(os.path.join(RR.DATA_DIR, "dyn_skins", no, "beijing" + ext)):
                out["bgSk"] = "%s/dyn/%s/beijing%s" % (b, no, ext)
                out["bgFmt"] = ext.lstrip(".")
                break
    miss = []
    for k, ext in (("skel", ".skel"), ("atlas", ".atlas"), ("png", ".png")):
        out[k] = "%s/dyn/%s/%s%s" % (b, no, frame, ext)
        if not os.path.isfile(os.path.join(RR.DATA_DIR, "dyn_skins", no, frame + ext)):
            miss.append(frame + ext)
    if sk_name.endswith(".sk"):
        # .sk 那套本来就没有 skel —— 别把它算进 missing, 否则脚本以为缺件、干脆不播;
        # skel 字段兜底指向实际骨架, 免得旧逻辑拿到一个死地址。
        miss = [m for m in miss if not m.endswith(".skel")]
        out["skel"] = out["sk"]
    out["missing"] = miss
    return out


def _probe_image(n):
    """单个候选文件名在三个落点里找一遍 → 真实路径或空。find_image_file 的内部件。"""
    for d in (RR.IMG_DIR, RR.REF_DIR):
        p = os.path.join(d, n)
        if os.path.isfile(p):
            return p
    try:
        from core import csv_loader
        season = RR.norm_season(csv_loader.CURRENT_SEASON)
        for r in RR.build_rules(season):
            t = str(r.get("target") or "").strip()
            if not t or RR.is_remote_target(t):
                continue
            if os.path.basename(t) == n and os.path.isfile(t):
                return t
    except Exception as e:
        sys.stderr.write("[res_server] 按规则里的目标找图失败: %s\n" % e)
    return ""


# v4.74★ 省扩展名的自动补全名单(按常见度排序; 只在名字本身没有扩展名时才试)
_IMG_EXT_CANDIDATES = (".png", ".webp", ".jpg", ".jpeg", ".gif", ".bmp")


def find_image_file(name):
    """文件名 → 本机真实路径(找不到返回 "")。

    v4.74★ 为什么不能只认「我的图/」(用户原话: "资源替换下面的背景打开也没有替换啊"):
      规则的目标可以是【任意本地文件】—— 界面上「浏览 …」选的就是任意一张图, 选完直接写进
      规则。而 /img/<文件名> 以前只到 我的图/ 里找, 于是"图在别处"的规则一律 404:
      脚本拿不到替换图 → 游戏里那张背景原样不动, 而且不报错, 用户只能看到"配了没效果"。
      现在按这个顺序找, 三个落点都能命中:
        ① 我的图/                      老习惯(手放图片的地方), 最优先;
        ② 参考_配置表/资源替换/         生成规则时图片的默认落点;
        ③ 当前规则里【同名的本地目标】   用户自己挑的那个路径就在这儿。

    v4.74★ 两处宽松匹配(用户原话: "有加载不出来？还是我写的有问题？要写扩展名吗？"):
      ① 省扩展名 —— /img/OIP-C (5) 找不到时自动试 OIP-C (5).png/.webp/.jpg/…。
         手写地址的人不会记得每张图是 webp 还是 png; 而规则目标里的文件名是程序生成的、
         一定带扩展名, 先精确后宽松不影响正常路径。
      ② URL 解码在 read_image 里做(浏览器把空格发成 %20, 不解码连带扩展名都救不回来)。
    """
    n = os.path.basename(str(name or ""))
    if not n:
        return ""
    p = _probe_image(n)
    if p:
        return p
    root, ext = os.path.splitext(n)
    if not ext:
        for e in _IMG_EXT_CANDIDATES:
            p = _probe_image(n + e)
            if p:
                return p
    return ""


_ASSET_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def read_asset(rest, top="dyn"):
    """取 data/dyn_skins/ 或 data/lib/ 下的静态资源 → (code, body, ctype)。

    v4.113★ 为什么非得由本机供出去: 游戏页是 https, 脚本在里面直接 fetch 官方域名的
      skel/atlas/png 会被 CORS 挡掉(官方静态资源不带 CORS 头) —— 表现就是"动画加载不出来、
      控制台只有一条 CORS 报错"。所以动态背景走的是【download_suite 先拉到
      data/dyn_skins/<no>/】→【本机 /dyn/<no>/<file> 取】这条路: 同源同协议, 没有跨域,
      而且断网也能用(文件已经在本地了)。spine-player.js 同理放 data/lib/, 不依赖 CDN。

    安全边界: 每段只认 [A-Za-z0-9._-], 出现 ".." 或别的分隔符一律 404 ——
      与 read_image 同口径: 这里能读到的是用户自己下的皮肤资源, 不给它读任意路径的能力。
    """
    rel = str(rest or "").split("?")[0].split("#")[0]
    parts = [x for x in rel.split("/") if x not in ("", ".")]
    if not parts or any((x == ".." or not _ASSET_NAME_RE.match(x)) for x in parts):
        return (404, b"bad name", "text/plain; charset=utf-8")
    if top == "dyn":
        if len(parts) != 2:
            return (404, b"need /dyn/<no>/<file>", "text/plain; charset=utf-8")
        p = os.path.join(RR.DATA_DIR, "dyn_skins", parts[0], parts[1])
    elif top == "lib":
        if len(parts) != 1:
            return (404, b"need /lib/<file>", "text/plain; charset=utf-8")
        p = os.path.join(RR.DATA_DIR, "lib", parts[0])
    else:
        return (404, b"unknown top", "text/plain; charset=utf-8")
    if not os.path.isfile(p):
        return (404, b"not found", "text/plain; charset=utf-8")
    ext = os.path.splitext(p)[1].lower()
    if ext == ".atlas":
        ctype = "text/plain; charset=utf-8"
    elif ext in (".skel", ".sk"):
        ctype = "application/octet-stream"
    else:
        ctype = _CTYPES.get(ext) or "application/octet-stream"
    try:
        with open(p, "rb") as f:
            return (200, f.read(), ctype)
    except Exception as e:
        return (500, str(e).encode("utf-8", "replace"), "text/plain; charset=utf-8")


def read_image(rest):
    """取一张图 → (code, body, ctype)。接 URL 里 /img/ 之后那一段。

    安全边界: 只取 basename, 所以 /img/../../config.json 这种穿越写法取不到东西;
    这里服务的本来就是本机用户自己放的图, 不给它任何"能读到任意路径"的能力。
      (v4.74 起的 find_image_file 仍然只按【文件名】在三个已知落点/规则目标里比对,
       穿越写法一样取不到东西。)

    返回体是 bytes(或提示文本的 str), 调用方(本模块的服务 / bridge.py 的主端口)自己发包。

    v4.74★ 必须先做 URL 解码(unquote): 浏览器把地址里的空格/中文/括号发成 %20%28 这类转义,
    旧版拿转义串直接当文件名找 → 文件明明在也 404, 而且报错信息里显示的还是转义串,
    用户根本对不上号("OIP-C%20(5)" vs "OIP-C (5).webp")。解码放在这里而不是两个入口,
    是因为主端口(bridge.py)和本模块独立服务共用这个纯函数, 改一处两边都好。

    v4.75★ 还要剥掉尾巴上的破缓存参数: 正确写法是 /img/x.png?r=rev, 但旧版图库页/
    旧版打包 exe 会盲拼成 /img/x.png&r=rev(没 '?' 却用了 '&'), 这整段被当文件名 → 404。
    服务端在这里兜一层: 结尾的 ?r=… / &r=…(十六进制 rev)直接剥掉再找文件 ——
    旧客户端/缓存的旧页面发来的坏地址也能出图, 不再红。
    """
    from urllib.parse import unquote
    rest = re.sub(r"[?&]r=[0-9a-fA-F]{4,32}$", "", str(rest or ""))
    name = os.path.basename(unquote(rest))
    if not name:
        return 404, "not found", "text/plain; charset=utf-8"
    p = find_image_file(name)
    if not p:
        # 缺图 = 脚本会 404。这里把话说清楚(排查时最容易怀疑的是"规则没生效")
        return (404, "本机没有这张图: %s\n把它放进 %s(或者在规则里指向它所在的那个文件)"
                     % (name, RR.IMG_DIR),
                "text/plain; charset=utf-8")
    # Content-Type 按【真实命中的文件】算 —— 省扩展名的请求(name 没有后缀)也能给出正确类型
    ctype = _CTYPES.get(os.path.splitext(p)[1].lower(), "application/octet-stream")
    try:
        with open(p, "rb") as f:
            return 200, f.read(), ctype
    except Exception as e:
        return 500, "读取失败: %s" % e, "text/plain; charset=utf-8"


def scripts_json():
    """给【全能扩展管理页「获取新脚本」】用的清单 —— bridge/ 下有哪些可装的 .user.js。

    为什么值得一个独立端点: 扩展管理页要的是"名字+描述+哪个已内置"这种元信息,
    而 /res 是给人看的 HTML, 扩展解析它等于把展示层当接口用(改排版就崩)。
    builtin=True 的条目(主脚本)在扩展里只显示"不用装"不给安装按钮 —— v4.81 起主脚本
    【不再内置】, 由 /ext/bridge.main.js 下发(改完源脚本在助手「资源替换」页点
    『更新全能扩展脚本』, 扩展 ≤20 秒自动取走)。再装一份就是页面里跑两份同一脚本
    (会双重 hook, 幸好脚本自己会让位, 但没必要)。
    返回 {"count", "scripts":[{name,file,description,size,builtin}]}
    """
    items = []
    # (文件名, 是否已内置进扩展, 一句话说明) —— 顺序就是展示顺序
    known = [
        ("sgs_bridge.user.js", True, "主脚本：收发协议/剧本/看牌/换肤页面 hook —— 全能扩展由助手下发（不内置），无需安装"),
        ("sgs_skin_swap.user.js", False, "独立换肤脚本：只做换肤(检测到主脚本在场会自动让位)"),
    ]
    for fname, builtin, desc in known:
        p = os.path.join(BRIDGE_DIR, fname)
        if not os.path.isfile(p):
            continue
        try:
            size = os.path.getsize(p)
        except OSError:
            size = 0
        items.append({
            "name": fname.replace(".user.js", ""),
            "file": fname,
            "description": desc,
            "size": size,
            "builtin": builtin,
        })
    return {"count": len(items), "scripts": items}


# ---------------------------------------------------------------- 图库管理页(/gallery)的 API
# v4.74★ 用户原话: "里面可以添加；编辑；保存、导入、导出、右边的图片…可以直接选自定义图片,
#   进行保存；然后立马更新；可以直接就在三国杀那边就能看到效果的"。
#   设计口径: 浏览器图库页是【第二个操作入口】, 与助手 tkinter 界面共用同一份
#   data/resource_rules.json(自定义规则) —— 两个入口改的是同一个东西, 不做第二份存储。

_IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")


def _norm_pattern_input(raw):
    """匹配串输入归一化: 粘完整官方网址也能用(剥协议/?v=缓存参数)。"""
    p = str(raw or "").strip()
    m = re.match(r"^https?://", p, re.I)
    if m:
        p = p[m.end():]
    q = p.find("?")
    if q >= 0:
        p = p[:q]
    h = p.find("#")
    if h >= 0:
        p = p[:h]
    return p.strip().strip("/")


def _upsert_custom(pattern, target="", kind="", name="", enabled=True):
    """按 pattern 在自定义规则里新增/更新一条 → (规则dict, 是否新建)。"""
    rules = RR.load_rules()
    hit = None
    for r in rules:
        if str(r.get("pattern") or "").strip() == pattern:
            hit = r
            break
    if hit is None:
        hit = {"id": "r%d" % int(time.time() * 1000), "pattern": pattern,
               "target": "", "kind": kind or "背景", "name": name or "",
               "enabled": True, "season": ""}
        rules.append(hit)
        created = True
    else:
        created = False
    if target:
        hit["target"] = target
    if kind:
        hit["kind"] = kind
    if name:
        hit["name"] = name
    hit["enabled"] = bool(enabled)
    RR.save_rules(rules)
    return hit, created


def gallery_state_json(base=None):
    """图库页的整包状态: 所有规则的卡片数据(官方图 / 目标图 / 就绪态)。

    【不能】直接用 build_rules() 的输出当卡片清单 —— 它会把【停用的】规则跳过
      (停用条目只负责占位压住默认规则, 不进下发列表), 那样停用的卡片就从页面上
      消失了, 用户再也找不到地方重新启用。所以这里自己合并:
      自定义规则(启停都算) + 没被表过态的默认规则。
    """
    from core import csv_loader
    b = str(base or "").rstrip("/") or base_url()
    season = RR.norm_season(csv_loader.CURRENT_SEASON) or "current"
    customs = RR.load_rules()
    custom_pats = set()
    for r in customs:
        pat = str(r.get("pattern") or "").strip()
        if pat:
            custom_pats.add(pat)
    rows = list(customs)
    for d in RR.collect_default_rules(season):
        if str(d.get("pattern") or "").strip() not in custom_pats:
            rows.append(d)
    cards = []
    for r in rows:
        pat = str(r.get("pattern") or "").strip()
        if not pat:
            continue
        p = RR.preview_pair(b, r)
        cards.append({
            "pattern": pat,
            "kind": str(r.get("kind") or ""),
            "name": str(r.get("name") or ""),
            "target": str(r.get("target") or ""),
            "enabled": r.get("enabled", True) is not False,
            "source": "custom" if pat in custom_pats else "default",
            "official": p["official"],
            "current": p["current"],
            "note": p["note"],
            "ready": bool(p["ready"]),
        })
    cards.sort(key=lambda c: (c["kind"], c["name"], c["pattern"]))
    info = rules_json(base=b)
    return {"season": season, "base": b, "rev": info["rev"], "count": len(cards),
            "ready": sum(1 for c in cards if c["ready"]),
            "kinds": sorted({c["kind"] for c in cards if c["kind"]}),
            "cards": cards}


def api_upload(name, data, base=None, pattern="", kind="", rule_name=""):
    """上传一张图 → 存进 我的图/, 给了 pattern 就顺手把(自定义)规则配好。

    这就是"点卡片右边那个框 → 选图 → 保存 → 立马更新"的那一步:
    文件一落盘, /rules 的 rev 就变(mtime 变 + 规则映射变了), 脚本/扩展 ≤20 秒内自动重拉。
    返回 (code, payload_dict)。
    """
    fname = os.path.basename(str(name or "").replace("\\", "/")).strip()
    if not fname or "/" in fname or ".." in fname:
        return 400, {"ok": False, "error": "文件名不能用(只要文件名本身, 如 luxun_02.png)"}
    stem, ext = os.path.splitext(fname)
    if ext.lower() not in _IMG_EXTS:
        return 400, {"ok": False,
                     "error": "只收图片(%s), 收到的是 %s" % ("/".join(_IMG_EXTS), ext or "(无后缀)")}
    if not data:
        return 400, {"ok": False, "error": "没收到文件内容"}
    img_dir = RR.ensure_img_dir()
    p = os.path.join(img_dir, fname)
    with open(p, "wb") as f:
        f.write(data)
    out = {"ok": True, "name": fname, "url": "%s/img/%s" % (base, fname), "path": p}
    pat = _norm_pattern_input(pattern)
    if pat:
        if len(pat) < 4:
            return 400, {"ok": False, "error": "匹配串太短(至少 4 个字符)"}
        target = os.path.join(img_dir, fname)
        hit, created = _upsert_custom(pat, target=target, kind=kind or "背景",
                                      name=rule_name or stem, enabled=True)
        out["rule"] = {"pattern": pat, "target": hit["target"], "created": created}
    return 200, out


def api_post(body, base=None):
    """图库页的 JSON 动作入口 → (code, payload_dict)。

    action: saveRule / toggleRule / delRule / importRules
    全部只动【自定义规则】(data/resource_rules.json); 默认规则是配置表算出来的,
    "删"不了 —— 想恢复默认就删掉同 pattern 的自定义条目, 想不换就用 toggleRule 停用。
    """
    b = str(base or "").rstrip("/") or base_url()
    body = body if isinstance(body, dict) else {}
    action = str(body.get("action") or "")

    if action == "saveRule":
        pat = _norm_pattern_input(body.get("pattern"))
        if len(pat) < 4:
            return 400, {"ok": False, "error": "匹配串太短(至少 4 个字符) —— 太短会误伤无关资源"}
        tgt = str(body.get("target") or "").strip()
        enabled = body.get("enabled") is not False
        if not tgt and enabled:
            return 400, {"ok": False, "error": "目标不能为空 —— 填网址, 或用卡片上的「选图」走本地图"}
        hit, created = _upsert_custom(pat, target=tgt, kind=str(body.get("kind") or ""),
                                      name=str(body.get("name") or ""), enabled=enabled)
        return 200, {"ok": True, "created": created, "rule": hit}

    if action == "toggleRule":
        pat = _norm_pattern_input(body.get("pattern"))
        if not pat:
            return 400, {"ok": False, "error": "缺 pattern"}
        want_on = body.get("enabled") is not False
        rules = RR.load_rules()
        hit = next((r for r in rules if str(r.get("pattern") or "").strip() == pat), None)
        if hit is not None:
            hit["enabled"] = want_on
            RR.save_rules(rules)
            return 200, {"ok": True, "enabled": want_on}
        # 没有自定义条目 → 说明现在生效的是默认规则: 复制一份出来再改启停
        #   (build_rules 的占位语义: 自定义条目【无论启停】都压住同名默认规则)
        src = next((r for r in RR.build_rules()
                    if str(r.get("pattern") or "").strip() == pat), None)
        if src is None:
            return 400, {"ok": False, "error": "配置里没有这条规则: %s" % pat}
        _upsert_custom(pat, target=str(src.get("target") or ""), kind=str(src.get("kind") or ""),
                       name=str(src.get("name") or ""), enabled=want_on)
        return 200, {"ok": True, "enabled": want_on}

    if action == "delRule":
        pat = _norm_pattern_input(body.get("pattern"))
        if not pat:
            return 400, {"ok": False, "error": "缺 pattern"}
        rules = RR.load_rules()
        keep = [r for r in rules if str(r.get("pattern") or "").strip() != pat]
        if len(keep) == len(rules):
            return 400, {"ok": False,
                         "error": "这不是自定义规则(是配置表默认带出来的) —— 默认规则删不掉, "
                                  "想不换它用「停用」"}
        RR.save_rules(keep)
        return 200, {"ok": True}

    if action == "importRules":
        items = body.get("rules")
        if not isinstance(items, list) or not items:
            return 400, {"ok": False, "error": "没有可导入的规则(要 [{pattern, target}, …] 数组)"}
        img_dir = RR.ensure_img_dir()
        ok_n, bad = 0, []
        for it in items[:2000]:                      # 上限保护: 导入不是越多越好
            if not isinstance(it, dict):
                continue
            pat = _norm_pattern_input(it.get("pattern"))
            tgt = str(it.get("target") or "").strip()
            if len(pat) < 4 or not tgt:
                bad.append(str(it.get("pattern") or "(空)"))
                continue
            if not RR.is_remote_target(tgt) and not os.path.isabs(tgt):
                # 只有文件名的 → 当作 我的图/ 里的名字(导入别人分享的规则包时最常见的形态)
                tgt = os.path.join(img_dir, os.path.basename(tgt.replace("\\", "/")))
            _upsert_custom(pat, target=tgt, kind=str(it.get("kind") or ""),
                           name=str(it.get("name") or ""),
                           enabled=it.get("enabled") is not False)
            ok_n += 1
        return 200, {"ok": True, "imported": ok_n, "skipped": len(bad),
                     "bad": bad[:5]}

    if action == "dressUpdate":
        # v4.75★ 皮肤表更新: 不给 file = 从官方直链抓(需 resc+wasmtime); 给了 = 喂本地文件。
        #   下载 8MB + 解密要好几秒, 页面按钮上要写明"等一下"。
        from core import dress_table as _DT
        f = str(body.get("file") or "").strip()
        res = _DT.update_from_file(f) if f else _DT.update_online()
        return (200 if res.get("ok") else 400), res

    return 400, {"ok": False, "error": "未知 action: %s" % (action or "(空)")}


def dress_payload(q=""):
    """图库页「OL 皮肤表」弹窗的状态 + 搜索结果(一个端点全带上, 页面少发一半请求)。

    v4.75★ OL 大图按武将名挑的数据源在 core/dress_table.py —— 这里只是它的 HTTP 门面。
    """
    from core import dress_table as _DT
    t = _DT.load()
    why = _DT.decrypt_capability()
    return {"loaded": t["loaded"], "count": t["count"], "updated": t["updated"],
            "src": t["src"], "canUpdate": not why, "why": why,
            "items": _DT.search(q)}


def dress_probe_payload(skin, timeout=8.0):
    """探测一套皮肤哪些大图/动态帧真的存在(按需发 ~7 个请求, 全表探测是明确的"不做")。"""
    from core import dress_table as _DT
    try:
        sid = int(str(skin or "").strip())
    except ValueError:
        return {"ok": False, "error": "皮肤编号要是数字, 收到的是 %r" % (skin or "")}
    found = _DT.probe_skin(sid, timeout=timeout)
    hit = _DT.search(str(sid), limit=1)
    return {"ok": True, "skin": sid,
            "title": (hit[0].get("title") if hit else ""),
            "name": (hit[0].get("name") if hit else ""),
            "found": found}


def gallery_html(base=None):
    """图库管理页(/gallery) —— 预览页的【可操作版】: 选图/编辑/启停/导入导出全在浏览器里做。

    与 preview_html(纯静态, 存成文件给人看)的关系: 那张继续留着(界面「预览全部」),
    这张是活的 —— 数据现拉 /resapi/state, 保存走 /resapi, 图片带 ?r=rev 自动破缓存。
    """
    b = str(base or "").rstrip("/") or base_url()
    return _GALLERY_TMPL.replace("__BASE__", b)


_GALLERY_TMPL = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>资源图库 · 添加/编辑/导入/导出</title>
<style>
body{background:#1e1e22;color:#e8e8e8;font:13px/1.5 "Microsoft YaHei UI",sans-serif;margin:0;padding:14px}
h1{font-size:16px;margin:0 0 6px}
.bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:8px 0}
button{background:#2f2f38;color:#e8e8e8;border:1px solid #4a4a56;border-radius:5px;
  padding:5px 12px;cursor:pointer;font-size:12px}
button:hover{border-color:#e0b060}
button.pri{background:#4a3b1e;border-color:#e0b060;color:#e0b060;font-weight:bold}
input,select{background:#17171b;color:#e8e8e8;border:1px solid #4a4a56;border-radius:5px;padding:5px 8px;font-size:12px}
.hint{color:#9a9a9a;font-size:12px;margin:4px 0 10px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:10px}
.card{background:#26262c;border:1px solid #3a3a44;border-radius:6px;padding:8px}
.card.off{opacity:.45}
.hd{font-size:12px;color:#e0b060;margin-bottom:6px;display:flex;justify-content:space-between;gap:4px}
.tag{font-size:10px;padding:1px 5px;border-radius:3px;background:#3a3a44;color:#bbb}
.tag.custom{background:#4a3b1e;color:#e0b060}
.imgs{display:flex;gap:6px}
figure{margin:0;flex:1;text-align:center}
figcaption{font-size:11px;color:#9a9a9a;margin-bottom:2px}
.imgbox{width:100%;height:110px;object-fit:contain;background:#17171b;border-radius:4px;display:block}
.pick{height:110px;border:2px dashed #5a5a66;border-radius:4px;display:flex;align-items:center;
  justify-content:center;color:#8a8a96;font-size:11px;cursor:pointer;text-align:center;padding:4px}
.pick:hover{border-color:#e0b060;color:#e0b060}
.pat{font-size:10px;color:#7fd4ff;word-break:break-all;margin-top:6px}
.st{font-size:11px;margin-top:4px}
.ok{color:#9fd4a0}.warn{color:#c8a24a}.bad{color:#ff8080}
.ops{display:flex;gap:6px;margin-top:6px;flex-wrap:wrap}
.ops button{padding:2px 8px;font-size:11px}
#toast{position:fixed;right:16px;bottom:16px;background:#4a3b1e;color:#ffd98a;border:1px solid #e0b060;
  border-radius:6px;padding:8px 14px;font-size:12px;max-width:420px;z-index:9}
.hidden{display:none}
/* v4.75★ 模态框(替代浏览器原生 prompt/confirm —— 那两个不能排版也不能给默认按钮)
   ⚠ v4.75 补丁: 以前在 #mask 主规则里直接写 display:flex、再靠 .hidden 收起 ——
   ID 选择器(1,0,0)永远赢过 .hidden(0,1,0), 结果遮罩【永远显示】: 中间悬着一个空
   模态框灰框、整层挡掉页面所有点击, 点关闭也没用(用户现场: "他不消失啊, 我操作不了啊")。
   现在默认 display:none, 打开时加 .open —— 两条都是 #mask 前缀, 优先级同族能分出胜负。 */
#mask{position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:10;display:none;
  align-items:center;justify-content:center}
#mask.open{display:flex}
#modal{background:#26262c;border:1px solid #4a4a56;border-radius:8px;padding:16px 18px;
  min-width:420px;max-width:640px;max-height:82vh;overflow:auto;box-shadow:0 6px 24px rgba(0,0,0,.5)}
#modal h3{margin:0 0 8px;font-size:14px;color:#e0b060}
#modal .fld{margin:8px 0}
#modal .fld label{display:block;font-size:11px;color:#9a9a9a;margin-bottom:3px}
#modal .fld input,#modal .fld textarea{width:100%;box-sizing:border-box}
#modal .fld textarea{height:52px;resize:vertical;font-family:inherit}
#modal .mbtns{display:flex;gap:8px;justify-content:flex-end;margin-top:12px}
#modal .row{display:flex;gap:6px;align-items:center;padding:3px 2px;border-bottom:1px solid #33333c;font-size:12px}
#modal .row button{padding:1px 7px;font-size:11px}
#modal .dim{color:#9a9a9a;font-size:11px}
.simbox{background:#101014;border:1px solid #3a3a44;border-radius:4px;overflow:hidden}
</style></head><body>
<h1>资源图库 <span id="meta" style="font-size:12px;color:#9a9a9a"></span></h1>
<p class="hint">点右边「你要换成的」那个框(或「换图」)直接选本地图 → 保存后 <b>游戏端 15~20 秒内自动生效</b>
(换肤脚本每 15 秒 / 扩展每 20 秒重拉规则, 图片带版本号自动破缓存, 不用重启助手)。
已加载进对局的贴图要等游戏重新加载那个资源(重开对局或回大厅再进)才会换新脸。</p>
<div class="bar">
  <button class="pri" id="bAdd">＋ 添加规则</button>
  <button id="bDress">OL 皮肤表(大图按武将名挑)</button>
  <button id="bImp">导入</button>
  <button id="bExp">导出</button>
  <button id="bRef">刷新</button>
  <input id="q" placeholder="搜索 名称/匹配串…">
  <select id="fKind"><option value="">全部类别</option></select>
  <label style="font-size:12px;color:#9a9a9a"><input type="checkbox" id="fReady"> 只看已就绪</label>
</div>
<div class="grid" id="grid">加载中…</div>
<div id="toast" class="hidden"></div>
<div id="mask" class="hidden"><div id="modal"></div></div>
<input type="file" id="fpick" class="hidden" accept=".png,.jpg,.jpeg,.webp,.gif">
<input type="file" id="fimp" class="hidden" accept=".json">
<script>
var BASE = '__BASE__';
var ST = null;                       // /resapi/state 的整包
var pending = null;                  // {pattern, kind, name} = 等着选图的规则
var $ = function(id){ return document.getElementById(id); };

function toast(msg, ms){
  var t = $('toast'); t.textContent = msg; t.className = '';
  clearTimeout(t._h); t._h = setTimeout(function(){ t.className = 'hidden'; }, ms || 3200);
}
function post(action, obj){
  obj = obj || {}; obj.action = action;
  return fetch(BASE + '/resapi', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify(obj)}).then(function(r){ return r.json(); });
}
function esc(s){ return String(s == null ? '' : s).replace(/[&<>"]/g, function(c){
  return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }

// ---- v4.75★ 模态框: 替代浏览器原生 prompt()/confirm()(不能排版、不能给默认值按钮) ----
function openModal(html){
  $('modal').innerHTML = html;
  $('mask').className = 'open';
  return $('modal');
}
function closeModal(){ $('mask').className = ''; $('modal').innerHTML = ''; }
// 点遮罩 = 取消; 点框内不冒泡
$('mask').addEventListener('mousedown', function(ev){
  if (ev.target === $('mask')) closeModal();
});
// fields: [{key,label,value,placeholder,multi}] → Promise<values|null>(确定/取消)
function askModal(title, fields, okText){
  return new Promise(function(resolve){
    var html = '<h3>' + esc(title) + '</h3>';
    (fields || []).forEach(function(f){
      html += '<div class="fld"><label>' + esc(f.label || f.key) + '</label>' +
        (f.multi
          ? '<textarea id="mf_' + f.key + '" placeholder="' + esc(f.placeholder || '') + '">' + esc(f.value || '') + '</textarea>'
          : '<input id="mf_' + f.key + '" value="' + esc(f.value || '') + '" placeholder="' + esc(f.placeholder || '') + '">') +
        '</div>';
    });
    html += '<div class="mbtns"><button id="mCancel">取消</button>' +
            '<button class="pri" id="mOk">' + esc(okText || '确定') + '</button></div>';
    openModal(html);
    var first = $('modal').querySelector('input,textarea');
    if (first) first.focus();
    $('mOk').onclick = function(){
      var out = {};
      (fields || []).forEach(function(f){
        out[f.key] = ($('mf_' + f.key).value || '').trim();
      });
      closeModal(); resolve(out);
    };
    $('mCancel').onclick = function(){ closeModal(); resolve(null); };
    _escBind(resolve);
  });
}
function confirmModal(title, text){ return askModal(title, [], '确定').then(function(v){ return v !== null; }); }
// 带正文段落的确认框(confirmModal 只收字段, 放不下一段说明文字)
function confirmBox(title, text){
  return new Promise(function(resolve){
    openModal('<h3>' + esc(title) + '</h3><p style="font-size:12px;line-height:1.7">' + esc(text) + '</p>' +
      '<div class="mbtns"><button id="mCancel">取消</button><button class="pri" id="mOk">确定</button></div>');
    $('mOk').onclick = function(){ closeModal(); resolve(true); };
    $('mCancel').onclick = function(){ closeModal(); resolve(false); };
  });
}
// ESC 监听不能每次 askModal 都往 #modal 上挂一个(#modal 节点常驻, 会越积越多) —— 先摘旧的
var _ESC_H = null;
function _escBind(resolve){
  if (_ESC_H) $('modal').removeEventListener('keydown', _ESC_H);
  _ESC_H = function(ev){
    if (ev.key === 'Escape'){ closeModal(); resolve(null); }
  };
  $('modal').addEventListener('keydown', _ESC_H);
}

// ---- 通用小件 ----
function loadImg(url){
  return new Promise(function(res, rej){
    var i = new Image();
    i.onload = function(){ res(i); };
    i.onerror = function(){ rej('图加载失败: ' + url); };
    i.src = url;
  });
}
function uploadBytes(buf, fname, info){
  return fetch(BASE + '/resapi/upload?name=' + encodeURIComponent(fname) +
        '&pattern=' + encodeURIComponent(info.pattern) +
        '&kind=' + encodeURIComponent(info.kind || '') +
        '&rname=' + encodeURIComponent(info.name || ''),
        {method:'POST', body: buf})
    .then(function(r){ return r.json(); });
}
var CUR_AUDIO = null;
function playUrl(url){
  try { if (CUR_AUDIO) { CUR_AUDIO.pause(); } } catch (e) {}
  CUR_AUDIO = new Audio(url);
  CUR_AUDIO.play().catch(function(e){ toast('✗ 播放失败(浏览器可能拦了自动播放, 再点一次): ' + e); });
}

function load(){
  return fetch(BASE + '/resapi/state').then(function(r){ return r.json(); }).then(function(s){
    ST = s; render();
  });
}

// v4.75★ 破缓存参数要"有 ? 用 &、没 ? 用 ?" —— 以前一律拼 '&r=rev', 本地图(/img/x.png)
//   和剥过 ?v= 的官方图都没有 '?', 拼出来是 /img/x.png&r=abc → /img 处理把
//   "x.png&r=abc" 整段当文件名 → 404 → 右边那张图【全部】显示不出来
//   (用户现场: "不管写什么, 右边都没有")。
function rv(u){ return u + (u.indexOf('?') < 0 ? '?r=' : '&r=') + ST.rev; }

function render(){
  var q = $('q').value.trim().toLowerCase();
  var fk = $('fKind').value, fr = $('fReady').checked;
  var kc = {}; (ST.kinds || []).forEach(function(k){ kc[k] = 1; });
  var sel = $('fKind');
  if (!sel.options.length) {
    sel.innerHTML = '<option value="">全部类别</option>';
    (ST.kinds || []).forEach(function(k){
      var o = document.createElement('option'); o.value = k; o.textContent = k; sel.appendChild(o);
    });
  }
  var cards = (ST.cards || []).filter(function(c){
    if (fk && c.kind !== fk) return false;
    if (fr && !c.ready) return false;
    if (q && (c.name + ' ' + c.pattern).toLowerCase().indexOf(q) < 0) return false;
    return true;
  });
  $('meta').textContent = '· 赛季 ' + ST.season + ' · 共 ' + ST.count + ' 条, 已就绪 ' + ST.ready +
    ' · rev ' + ST.rev + ' (本视图 ' + cards.length + ' 条)';
  var html = cards.map(function(c){
    var off = c.official ? '<img class="imgbox" loading="lazy" src="' + esc(c.official) + '">'
                         : '<div class="imgbox" style="display:flex;align-items:center;justify-content:center;color:#777;font-size:11px">这类没有官方图</div>';
    var cur;
    if (c.current) cur = '<img class="imgbox" loading="lazy" src="' + esc(rv(c.current)) + '">';
    else cur = '<div class="pick" data-pat="' + esc(c.pattern) + '" data-kind="' + esc(c.kind) +
               '" data-name="' + esc(c.name) + '">点击选图<br>(存进 我的图/ 并配好这条规则)</div>';
    return '<div class="card' + (c.enabled ? '' : ' off') + '">' +
      '<div class="hd"><b>' + esc(c.kind) + '</b> · ' + esc(c.name) +
      '<span class="tag' + (c.source === 'custom' ? ' custom' : '') + '">' +
      (c.source === 'custom' ? '自定义' : '默认') + '</span></div>' +
      '<div class="imgs"><figure><figcaption>游戏现在加载的</figcaption>' + off + '</figure>' +
      '<figure><figcaption>你要换成的 (点击换图)</figcaption>' + cur + '</figure></div>' +
      '<div class="pat">' + esc(c.pattern) + '</div>' +
      '<div class="st">' + (c.ready ? '<span class="ok">已就绪</span>' : '<span class="warn">待放图</span>') +
      (c.enabled ? '' : ' · <span class="bad">已停用</span>') +
      (c.note ? ' · <span style="color:#9a9a9a">' + esc(c.note) + '</span>' : '') + '</div>' +
      '<div class="ops">' +
      '<button data-act="pick" data-pat="' + esc(c.pattern) + '" data-kind="' + esc(c.kind) +
        '" data-name="' + esc(c.name) + '">换图</button>' +
      '<button data-act="edit" data-pat="' + esc(c.pattern) + '">编辑</button>' +
      '<button data-act="toggle" data-pat="' + esc(c.pattern) + '">' + (c.enabled ? '停用' : '启用') + '</button>' +
      (c.source === 'custom' ? '<button data-act="del" data-pat="' + esc(c.pattern) + '">删除</button>' : '') +
      // v4.75★ 比例不一致会变形 → 一键按官方原图比例中心裁剪(浏览器 Canvas 现场裁, 不用开画图工具)
      (c.official && c.current
        ? '<button data-act="crop" data-pat="' + esc(c.pattern) + '">按原图裁剪</button>' +
          '<button data-act="sim" data-pat="' + esc(c.pattern) + '">模拟效果</button>'
        : '') +
      // v4.75★ 音效试听: 原声 = 官方那份, 我的 = 你换上去的(/img/ 那份)
      ((c.kind === '音效' || /voice\\//i.test(c.pattern))
        ? (c.official ? '<button data-act="playO" data-pat="' + esc(c.pattern) + '">▶ 原声</button>' : '') +
          (c.current ? '<button data-act="playM" data-pat="' + esc(c.pattern) + '">▶ 我的</button>' : '')
        : '') +
      '</div></div>';
  });
  $('grid').innerHTML = html.join('') || '<p class="hint">没有匹配的规则。</p>';
}

function pickFor(info){ pending = info; $('fpick').value = ''; $('fpick').click(); }

$('fpick').addEventListener('change', function(){
  var f = this.files[0]; if (!f || !pending) return;
  var info = pending; pending = null;
  var rd = new FileReader();
  rd.onload = function(){
    fetch(BASE + '/resapi/upload?name=' + encodeURIComponent(f.name) +
          '&pattern=' + encodeURIComponent(info.pattern) +
          '&kind=' + encodeURIComponent(info.kind || '') +
          '&rname=' + encodeURIComponent(info.name || ''),
          {method:'POST', body: rd.result})
      .then(function(r){ return r.json(); })
      .then(function(res){
        if (!res.ok) { toast('✗ ' + (res.error || '上传失败')); return; }
        toast('✓ 已保存 ' + res.name + ' —— 游戏端 15~20 秒内自动生效');
        load();
      })
      .catch(function(e){ toast('✗ 上传失败: ' + e); });
  };
  rd.readAsArrayBuffer(f);
});

$('grid').addEventListener('click', function(ev){
  var t = ev.target;
  if (t.className && String(t.className).indexOf('pick') >= 0 && t.dataset.pat) {
    pickFor({pattern: t.dataset.pat, kind: t.dataset.kind, name: t.dataset.name});
    return;
  }
  var act = t.dataset && t.dataset.act;
  if (!act) return;
  var pat = t.dataset.pat;
  if (act === 'pick') {
    var c = (ST.cards || []).filter(function(x){ return x.pattern === pat; })[0] || {};
    pickFor({pattern: pat, kind: c.kind, name: c.name});
  } else if (act === 'toggle') {
    var cur = (ST.cards || []).filter(function(x){ return x.pattern === pat; })[0] || {};
    post('toggleRule', {pattern: pat, enabled: !cur.enabled}).then(function(r){
      if (!r.ok) { toast('✗ ' + r.error); return; }
      toast(cur.enabled ? '已停用(游戏里恢复官方原图)' : '已启用'); load();
    });
  } else if (act === 'del') {
    confirmBox('删除规则', '删除这条自定义规则? 删后恢复为配置表默认。').then(function(yes){
      if (!yes) return;
      post('delRule', {pattern: pat}).then(function(r){
        if (!r.ok) { toast('✗ ' + r.error); return; }
        toast('已删除'); load();
      });
    });
  } else if (act === 'crop') {
    doCrop(pat);
  } else if (act === 'sim') {
    doSim(pat);
  } else if (act === 'playO') {
    var ca = (ST.cards || []).filter(function(x){ return x.pattern === pat; })[0] || {};
    if (ca.official) playUrl(ca.official);
  } else if (act === 'playM') {
    var cm = (ST.cards || []).filter(function(x){ return x.pattern === pat; })[0] || {};
    if (cm.current) playUrl(cm.current + (cm.current.indexOf('?') >= 0 ? '&' : '?') + 'r=' + ST.rev);
  } else if (act === 'edit') {
    var c2 = (ST.cards || []).filter(function(x){ return x.pattern === pat; })[0] || {};
    askModal('编辑规则', [
      {key: 'name', label: '规则名称(显示用)', value: c2.name || ''},
      {key: 'tgt', label: '目标地址(网址; 要换本地图请点卡片上的「换图」)', value: c2.target || '',
       placeholder: '留空 = 保持不变'},
      {key: 'kind', label: '类别', value: c2.kind || '背景'}
    ]).then(function(v){
      if (v === null) return;
      post('saveRule', {pattern: pat, target: v.tgt, name: v.name, kind: v.kind,
                        enabled: c2.enabled !== false && (v.tgt ? true : c2.enabled !== false)})
        .then(function(r){ if (!r.ok) { toast('✗ ' + r.error); return; } toast('已保存'); load(); });
    });
  }
});

$('bAdd').addEventListener('click', function(){
  askModal('添加规则', [
    {key: 'pat', label: '匹配串 = "URL 里的一截"。可以粘完整官方网址(自动剥 ?v=), 例如:',
     placeholder: 'runtime/tavernChess/piece/luxun_02.png\\nruntime/general/big/static/500103.png'},
    {key: 'tgt', label: '目标网址(官方立绘/任意在线图; 要用本地图就留空, 确定后直接选文件)',
     placeholder: '留空 = 用本地图'}
  ], '下一步').then(function(v){
    if (v === null) return;
    var pat = v.pat;
    if (pat.length < 4) { toast('✗ 匹配串太短'); return; }
    if (v.tgt) {
      post('saveRule', {pattern: pat, target: v.tgt}).then(function(r){
        if (!r.ok) { toast('✗ ' + r.error); return; } toast('已保存'); load();
      });
    } else {
      pickFor({pattern: pat, kind: '背景', name: ''});
    }
  });
});

// ---- v4.75★ 按原图比例中心裁剪(Canvas 现场裁, 免开画图工具) ----
function doCrop(pat){
  var c = (ST.cards || []).filter(function(x){ return x.pattern === pat; })[0] || {};
  if (!c.official || !c.current) { toast('这条要先有官方图和你换上的图, 才有得裁'); return; }
  if (c.current.indexOf('/img/') < 0) { toast('裁剪只支持本地图(「换图」换成 My 图里的图再试); 网址图浏览器不让画布动'); return; }
  toast('正在量官方原图尺寸…');
  loadImg(c.official).then(function(off){
    var tw = off.naturalWidth, th = off.naturalHeight;
    if (!tw || !th) throw '官方图尺寸量不到(没加载出来?)';
    return loadImg(c.current + (c.current.indexOf('?') >= 0 ? '&' : '?') + 'r=' + ST.rev)
      .then(function(mine){
        var sw = mine.naturalWidth, sh = mine.naturalHeight;
        if (/\\.gif($|\\?)/i.test(c.current)) toast('注意: 动图 gif 裁剪后只剩第一帧');
        // 中心裁到官方比例: 短边贴齐、长边居中截断
        var tr = tw / th, sr = sw / sh, cw, ch;
        if (sr > tr) { ch = sh; cw = Math.round(sh * tr); }
        else { cw = sw; ch = Math.round(sw / tr); }
        var sx = Math.round((sw - cw) / 2), sy = Math.round((sh - ch) / 2);
        var cv = document.createElement('canvas'); cv.width = tw; cv.height = th;
        cv.getContext('2d').drawImage(mine, sx, sy, cw, ch, 0, 0, tw, th);
        toast('裁剪 ' + sw + '×' + sh + ' → ' + tw + '×' + th + ', 保存中…');
        cv.toBlob(function(blob){
          if (!blob) { toast('✗ 浏览器裁剪失败(图可能太大或格式不认)'); return; }
          var rd = new FileReader();
          rd.onload = function(){
            var stem = decodeURIComponent((c.current.split('/img/')[1] || 'crop').split('?')[0])
                        .replace(/\\.[a-z0-9]+$/i, '');
            uploadBytes(rd.result, stem + '_裁剪.png', c).then(function(res){
              if (!res.ok) { toast('✗ ' + (res.error || '上传失败')); return; }
              toast('✓ 已保存 ' + res.name + ' —— 游戏端 15~20 秒内自动生效');
              load();
            });
          };
          rd.readAsArrayBuffer(blob);
        }, 'image/png');
      });
  }).catch(function(e){ toast('✗ ' + e); });
}

// ---- v4.75★ 模拟效果: 你的图按官方贴图矩形等比摆放, 所见即所得 ----
function doSim(pat){
  var c = (ST.cards || []).filter(function(x){ return x.pattern === pat; })[0] || {};
  if (!c.official) { toast('这条没有官方图可对照, 模拟不了'); return; }
  loadImg(c.official).then(function(off){
    var tw = off.naturalWidth, th = off.naturalHeight;
    if (!tw || !th) throw '官方图尺寸量不到(没加载出来?)';
    var w = Math.min(300, tw), h = Math.max(1, Math.round(w * th / tw));
    var inner = c.current
      ? '<img style="width:100%;height:100%;object-fit:contain" src="' + esc(rv(c.current)) + '">'
      : '<div style="color:#777;font-size:11px;display:flex;height:100%;align-items:center;justify-content:center">还没换图</div>';
    openModal('<h3>模拟效果 — ' + esc(c.kind) + ' · ' + esc(c.name) + '</h3>' +
      '<p class="hint">游戏贴图矩形 ' + tw + '×' + th + '。左边是游戏现在加载的官方图, 右边是你的图按引擎的等比缩放摆进去的样子(所见即所得; 比例不一致时引擎留边, 不会硬拉变形)。</p>' +
      '<div style="display:flex;gap:10px;flex-wrap:wrap">' +
      '<div><div class="dim" style="margin-bottom:3px">官方原图 ' + tw + '×' + th + '</div>' +
      '<div class="simbox" style="width:' + w + 'px;height:' + h + 'px">' +
      '<img style="width:100%;height:100%;object-fit:contain" src="' + esc(c.official) + '"></div></div>' +
      '<div><div class="dim" style="margin-bottom:3px">你的图(模拟)</div>' +
      '<div class="simbox" style="width:' + w + 'px;height:' + h + 'px">' + inner + '</div></div></div>' +
      '<div class="mbtns"><button id="mCancel">关闭</button></div>');
    $('mCancel').onclick = closeModal;
  }).catch(function(e){ toast('✗ ' + e); });
}

$('bExp').addEventListener('click', function(){ window.open(BASE + '/resapi/export'); });

// ---- v4.75★ OL 皮肤表: 大图/动态帧按武将名挑(数据源 core/dress_table.py) ----
$('bDress').addEventListener('click', dressModal);

function dressModal(){
  fetch(BASE + '/resapi/dress?q=').then(function(r){ return r.json(); }).then(function(st){
    var status = st.loaded
      ? '共 <b>' + st.count + '</b> 套 · 更新于 ' + new Date(st.updated * 1000).toLocaleString()
      : '<span class="warn">本地还没有皮肤表</span>';
    openModal('<h3>OL 大图 · 按武将名挑(主游戏皮肤表)</h3>' +
      '<p class="hint">选一套皮肤 → 「探测大图」(发 7 个请求问官方服务器哪几张真的存在) → ' +
      '点一张「换成我的图」就走换图流程, 或「官方图当目标」把它填成某条规则的目标。</p>' +
      '<div class="bar">' +
      '<input id="dq" placeholder="武将名 / 皮肤名 / 编号…" style="width:220px">' +
      '<button id="dUpd">' + (st.loaded ? '联网更新皮肤表' : '联网抓取皮肤表(约 8MB, 等几秒)') + '</button>' +
      '<span id="dinfo" class="dim">' + status + '</span></div>' +
      '<div id="dlist" style="max-height:250px;overflow:auto"></div>' +
      '<div id="dres" style="max-height:250px;overflow:auto;margin-top:8px"></div>' +
      '<div class="mbtns"><button id="mCancel">关闭</button></div>');
    if (!st.loaded && !st.canUpdate) {
      $('dinfo').innerHTML += '<br>' + esc(st.why || '这台机器上没法在线更新');
    }
    $('mCancel').onclick = closeModal;
    $('dUpd').onclick = function(){
      if (!st.canUpdate) { toast('✗ ' + (st.why || '这台机器上没法在线更新')); return; }
      $('dUpd').disabled = true;
      toast('正在下载皮肤表(约 8MB + 解密), 请等一下…');
      post('dressUpdate', {}).then(function(r){
        if (!r.ok) { toast('✗ ' + (r.error || '更新失败')); $('dUpd').disabled = false; return; }
        toast('✓ 皮肤表已更新: ' + r.count + ' 套');
        closeModal(); dressModal();
      });
    };
    var tId = null;
    $('dq').addEventListener('input', function(){
      clearTimeout(tId);
      tId = setTimeout(function(){ dressSearch($('dq').value); }, 300);
    });
    dressSearch('');
    if (st.loaded) $('dq').focus();
  });
}

function dressSearch(q){
  fetch(BASE + '/resapi/dress?q=' + encodeURIComponent(q))
    .then(function(r){ return r.json(); }).then(function(st){
      var el = $('dlist'); if (!el) return;
      if (!st.loaded) {
        el.innerHTML = '<p class="hint">还没有皮肤表 —— 点上面「联网抓取」按钮建一份' +
          (st.why ? '(' + esc(st.why) + ')' : '') + '</p>';
        return;
      }
      el.innerHTML = (st.items || []).map(function(it){
        return '<div class="row"><span class="dim" style="width:64px">' + it.id + '</span>' +
          '<b>' + esc(it.name) + '</b><span class="dim">· ' + esc(it.title || '(无皮肤名)') + '</span>' +
          '<span style="flex:1"></span>' +
          '<button data-dprobe="' + it.id + '">探测大图</button></div>';
      }).join('') || '<p class="hint">没搜到。试试只输武将名, 比如: 刘备 / 关羽 / 500103</p>';
    });
}

$('modal').addEventListener('click', function(ev){
  var t = ev.target;
  if (!t.dataset) return;
  if (t.dataset.dprobe) {
    var el = $('dres');
    if (el) el.innerHTML = '<p class="hint">探测中(静态大图 + 6 个动态帧, 等几秒)…</p>';
    fetch(BASE + '/resapi/dressProbe?skin=' + encodeURIComponent(t.dataset.dprobe))
      .then(function(r){ return r.json(); }).then(function(res){
        if (!res.ok) { toast('✗ ' + res.error); if (el) el.innerHTML = ''; return; }
        dressShowProbe(res);
      });
    return;
  }
  var act = t.dataset.dact;
  if (!act) return;
  var url = t.dataset.durl || '', pat = t.dataset.dpat || '';
  if (act === 'pick') {
    closeModal();
    pickFor({pattern: pat, kind: 'OL大图', name: t.dataset.dname || ''});
  } else if (act === 'target') {
    askModal('把这张官方图用作哪条规则的目标?', [
      {key: 'pat', label: '被替换资源的匹配串(例: runtime/tavernChess/piece/luxun_02.png)'}
    ], '保存').then(function(v){
      if (v === null || !v.pat) return;
      post('saveRule', {pattern: v.pat, target: url, kind: 'OL大图',
                        name: t.dataset.dname || ''}).then(function(r){
        if (!r.ok) { toast('✗ ' + r.error); return; }
        toast('✓ 已保存: ' + v.pat + ' → 官方大图'); load();
      });
    });
  } else if (act === 'copy') {
    (navigator.clipboard ? navigator.clipboard.writeText(url) : Promise.reject())
      .then(function(){ toast('✓ 网址已复制'); })
      .catch(function(){
        var ta = document.createElement('textarea'); ta.value = url;
        document.body.appendChild(ta); ta.select();
        try { document.execCommand('copy'); toast('✓ 网址已复制'); }
        catch (e) { toast('✗ 复制失败, 网址在控制台: ' + url); }
        document.body.removeChild(ta);
      });
  }
});

function dressShowProbe(res){
  var el = $('dres'); if (!el) return;
  var rows = (res.found || []).map(function(f){
    var nm = (res.title ? res.title + '·' : '') + (res.name || '');
    return '<div class="row"><span class="dim" style="width:70px">' +
      (f.dynamic ? ('动态帧 ' + f.frame) : '静态大图') + '</span>' +
      '<span class="dim" style="width:88px">' + (f.w ? f.w + '×' + f.h : '') + '</span>' +
      '<span class="dim" style="flex:1;word-break:break-all">' + esc(f.pattern) + '</span>' +
      '<button data-dact="pick" data-pat="' + esc(f.pattern) + '" data-dname="' + esc(nm) + '">换成我的图</button>' +
      '<button data-dact="target" data-durl="' + esc(f.url) + '" data-dname="' + esc(nm) + '">官方图当目标</button>' +
      '<button data-dact="copy" data-durl="' + esc(f.url) + '">复制网址</button></div>';
  });
  el.innerHTML = (rows.join('') ||
    '<p class="hint">这套编号(' + res.skin + ')在官方服务器上一张都没探到 —— 换一套试试。</p>');
}
$('bImp').addEventListener('click', function(){ $('fimp').value = ''; $('fimp').click(); });
$('fimp').addEventListener('change', function(){
  var f = this.files[0]; if (!f) return;
  var rd = new FileReader();
  rd.onload = function(){
    var obj;
    try { obj = JSON.parse(rd.result); } catch (e) { toast('✗ 不是合法 JSON'); return; }
    var list = Array.isArray(obj) ? obj : (obj.rules || []);
    post('importRules', {rules: list}).then(function(r){
      if (!r.ok) { toast('✗ ' + r.error); return; }
      toast('✓ 导入 ' + r.imported + ' 条' + (r.skipped ? '(跳过 ' + r.skipped + ' 条)' : '')); load();
    });
  };
  rd.readAsText(f);
});
$('bRef').addEventListener('click', function(){ load().then(function(){ toast('已刷新'); }); });
$('q').addEventListener('input', render);
$('fKind').addEventListener('change', render);
$('fReady').addEventListener('change', render);

load();
</script></body></html>"""


def read_script(name):
    """取 bridge/ 目录下的一个 .user.js → (code, body, ctype)。

    为什么必须从 http:// 地址打开而不是双击文件: 双击时 Tampermonkey 不一定接管,
    从 http 地址打开才会被它拦成安装页(和 serve_userscript 一个套路)。
    响应头【不能】带 Content-Disposition: attachment —— 那会变成下载, TM 拦不到。
    """
    name = os.path.basename(str(name or ""))
    if not name.endswith(".user.js"):
        return 404, "not found", "text/plain; charset=utf-8"
    p = os.path.join(BRIDGE_DIR, name)
    if not os.path.isfile(p):
        return 404, "not found", "text/plain; charset=utf-8"
    try:
        with open(p, "rb") as f:
            return 200, f.read(), "text/javascript; charset=utf-8"
    except Exception as e:
        return 500, "读取失败: %s" % e, "text/plain; charset=utf-8"


def ext_root(dir_name):
    """扩展根目录: bridge/<dir_name>/(正常) 或 bridge/<dir_name>/<dir_name>/(嵌套)。

    为什么要认两种: zip 里的顶层目录就叫 <dir_name>, 在 bridge/<dir_name>/ 里解压就会
    多套一层 —— 于是"扩展根目录"变成嵌套的那一层, 而 isfile(src/manifest.json) 判不到
    → /ext.zip 直接 404(界面上点"浏览器扩展(zip)"没反应, 还查不出是谁的锅)。
    认两种就够: 哪种都能打出正确的包, zip 条目一律是 <dir_name>/<相对路径>。
    """
    src = os.path.join(BRIDGE_DIR, dir_name)
    if not os.path.isfile(os.path.join(src, EXT_ENTRY)):
        nested = os.path.join(src, dir_name)
        if os.path.isfile(os.path.join(nested, EXT_ENTRY)):
            src = nested
    return src


def ext_zip(dir_name=None):
    """把 bridge/<dir_name>/(MV3 浏览器扩展)打进一个 zip → bytes。

    为什么要打包: 浏览器要"加载已解压的扩展程序", 而打包版助手的目录在用户手里
    可能就是个临时位置; 给个 zip 让用户解到一个固定目录, 引用关系才稳。
    不落盘 —— 内存里现打, 拿到的就是此刻目录里的内容。

    dir_name: 缺省 = 轻量版(res_ext)。全能版传 EXT_ALL_DIR_NAME(sgs_ext)。
      两个扩展【都是可选的】, 但全能版是推荐项(装一个 = 脚本 + 重定向 + 管理页都有了)。

    返回 None = 目录不存在或没有 manifest.json(说明这份代码没带扩展), 调用方回 404。
    """
    import io
    import zipfile
    name = dir_name or EXT_DIR_NAME
    src = ext_root(name)                   # 认嵌套那一层(见 ext_root 的说明)
    if not os.path.isfile(os.path.join(src, EXT_ENTRY)):
        return None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for dirpath, dirnames, filenames in os.walk(src):
            dirnames.sort()                       # 顺序稳定 → 同样内容产出同一个 zip(便于比对)
            for fn in sorted(filenames):
                p = os.path.join(dirpath, fn)
                rel = os.path.relpath(p, src).replace("\\", "/")
                # 统一的顶层目录名: 解压出来不散一地, 引导文案也只需要说"选中解压出的文件夹"
                z.write(p, "%s/%s" % (name, rel))
    return buf.getvalue()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype, extra=None):
        # v4.87★ 所有响应都过这里 —— 埋点放在这一个出口上, 不用在每条路由里各写一遍
        note_request(getattr(self, "path", ""), code,
                     getattr(self, "command", "GET"),
                     (self.headers.get("User-Agent", "") if self.headers else ""))
        if isinstance(body, str):
            body = body.encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            # 游戏页是 https, 这些是跨源请求 —— 不带 CORS 头浏览器会把响应丢掉
            self.send_header("Access-Control-Allow-Origin", "*")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if body:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
            return

    def do_HEAD(self):
        self.do_GET(head=True)

    def do_OPTIONS(self):
        self._send(204, b"", "text/plain; charset=utf-8",
                   {"Access-Control-Allow-Methods": "GET, HEAD, POST, OPTIONS",
                    "Access-Control-Allow-Headers": "*"})

    def do_POST(self):
        """图库页的写入口: /resapi(JSON 动作) 与 /resapi/upload(图片字节)。"""
        from urllib.parse import urlparse, parse_qs
        u = urlparse(self.path)
        q = parse_qs(u.query)
        base = base_url()
        if u.path == "/resapi/upload":
            n = int(self.headers.get("Content-Length") or 0)
            data = self.rfile.read(n) if n else b""
            try:
                code, payload = api_upload(
                    (q.get("name") or [""])[0], data, base=base,
                    pattern=(q.get("pattern") or [""])[0],
                    kind=(q.get("kind") or [""])[0],
                    rule_name=(q.get("rname") or [""])[0])
            except Exception as e:
                code, payload = 500, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
            self._send(code, json.dumps(payload, ensure_ascii=False),
                       "application/json; charset=utf-8")
            return
        if u.path == "/resapi":
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
            except Exception as e:
                self._send(400, json.dumps({"ok": False, "error": "不是合法 JSON: %s" % e},
                                           ensure_ascii=False), "application/json; charset=utf-8")
                return
            try:
                code, payload = api_post(body, base=base)
            except Exception as e:
                code, payload = 500, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
            self._send(code, json.dumps(payload, ensure_ascii=False),
                       "application/json; charset=utf-8")
            return
        self._send(404, "not found", "text/plain; charset=utf-8")

    def do_GET(self, head=False):
        path = self.path.split("?")[0]
        try:                                   # ?limit= / ?q= 这些查询参数(日志条数、查原地址)
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
        except Exception:
            q = {}
        if path in ("/", "/index.html", "/res"):
            # v4.87★ /res 以前【没挂路由】—— 而界面上「独立图片服务」按钮打开的正是 /res
            #   (_srv_started 里拼的就是 <base>/res), 于是点了按钮 = 404 Not Found。
            #   用户看到的就是"这个服务里面什么都没有"。三种写法都指向这一页。
            self._send(200, self._index_html(), "text/html; charset=utf-8")
            return
        if path == "/gallery":
            # v4.75★ base 用【真实监听端口】(self.server.server_port), 不走 base_url() 的
            #   默认值 —— 独立起服务时(core/res_server.py 8799)以前会把页面里的 BASE 写成
            #   默认 8767, 页面上所有 fetch(图库数据/皮肤表)全部打到不存在的端口上静默失败。
            self._send(200, gallery_html(base_url(self.server.server_port)),
                       "text/html; charset=utf-8")
            return
        if path == "/resapi/health":
            try:
                payload = health_json(base=base_url(self.server.server_port))
            except Exception as e:
                payload = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
            self._send(200, json.dumps(payload, ensure_ascii=False),
                       "application/json; charset=utf-8")
            return
        if path == "/resapi/files":
            try:
                payload = files_json(base=base_url(self.server.server_port))
            except Exception as e:
                payload = {"error": "%s: %s" % (type(e).__name__, e)}
            self._send(200, json.dumps(payload, ensure_ascii=False),
                       "application/json; charset=utf-8")
            return
        if path == "/resapi/log":
            payload = req_log_json(limit=(q.get("limit") or [200])[0],
                                   only=(q.get("only") or [""])[0] or None)
            self._send(200, json.dumps(payload, ensure_ascii=False),
                       "application/json; charset=utf-8")
            return
        if path == "/resapi/status":
            try:
                payload = status_json(base=base_url(self.server.server_port))
            except Exception as e:
                payload = {"error": "%s: %s" % (type(e).__name__, e)}
            self._send(200, json.dumps(payload, ensure_ascii=False),
                       "application/json; charset=utf-8")
            return
        if path == "/resapi/sizeCheck":
            try:
                payload = size_check_json(base=base_url(self.server.server_port),
                                          limit=(q.get("limit") or [20])[0])
            except Exception as e:
                payload = {"error": "%s: %s" % (type(e).__name__, e)}
            self._send(200, json.dumps(payload, ensure_ascii=False),
                       "application/json; charset=utf-8")
            return
        if path == "/resapi/official":
            try:
                payload = official_json((q.get("q") or [""])[0],
                                        base=base_url(self.server.server_port))
            except Exception as e:
                payload = {"error": "%s: %s" % (type(e).__name__, e)}
            self._send(200, json.dumps(payload, ensure_ascii=False),
                       "application/json; charset=utf-8")
            return
        if path == "/resapi/all":
            try:
                payload = all_json((q.get("q") or [""])[0], (q.get("only") or [""])[0],
                                   limit=(q.get("limit") or [800])[0])
            except Exception as e:
                payload = {"error": "%s: %s" % (type(e).__name__, e)}
            self._send(200, json.dumps(payload, ensure_ascii=False),
                       "application/json; charset=utf-8")
            return
        if path == "/resapi/openFolder":
            payload = open_img_dir()
            self._send(200, json.dumps(payload, ensure_ascii=False),
                       "application/json; charset=utf-8")
            return
        if path == "/resapi/state":
            try:
                payload = gallery_state_json()
            except Exception as e:
                self._send(500, json.dumps({"error": "%s: %s" % (type(e).__name__, e)},
                                           ensure_ascii=False), "application/json; charset=utf-8")
                return
            self._send(200, json.dumps(payload, ensure_ascii=False), "application/json; charset=utf-8")
            return
        if path in ("/resapi/dress", "/resapi/dressProbe"):
            from urllib.parse import parse_qs
            qs = parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
            try:
                if path == "/resapi/dress":
                    payload = dress_payload((qs.get("q") or [""])[0])
                else:
                    payload = dress_probe_payload((qs.get("skin") or [""])[0])
            except Exception as e:
                self._send(500, json.dumps({"error": "%s: %s" % (type(e).__name__, e)},
                                           ensure_ascii=False), "application/json; charset=utf-8")
                return
            self._send(200, json.dumps(payload, ensure_ascii=False), "application/json; charset=utf-8")
            return
        if path == "/resapi/export":
            body = json.dumps({"type": "sgs-res-rules", "rules": RR.load_rules()},
                              ensure_ascii=False, indent=2)
            self._send(200, body, "application/json; charset=utf-8",
                       {"Content-Disposition": "attachment; filename=resource_rules.json"})
            return
        if path in ("/rules", "/rules.json"):
            try:
                payload = rules_json()
            except Exception as e:
                self._send(500, json.dumps({"error": "%s: %s" % (type(e).__name__, e)}, ensure_ascii=False),
                           "application/json; charset=utf-8")
                return
            body = json.dumps(payload, ensure_ascii=False, indent=2)
            if head:
                self._send(200, b"", "application/json; charset=utf-8")
                return
            self._send(200, body, "application/json; charset=utf-8")
            return
        if path.startswith("/img/"):
            code, body, ctype = read_image(path[len("/img/"):])
            self._send(code, b"" if head else body, ctype,
                       {"Cache-Control": "no-store, no-cache, must-revalidate"})
            return
        # v4.113★ 动态背景要的三件套 + spine 运行时(见 read_asset 的说明)
        if path.startswith("/dyn/"):
            code, body, ctype = read_asset(path[len("/dyn/"):], "dyn")
            self._send(code, b"" if head else body, ctype,
                       {"Cache-Control": "no-store, no-cache, must-revalidate"})
            return
        if path.startswith("/lib/"):
            code, body, ctype = read_asset(path[len("/lib/"):], "lib")
            self._send(code, b"" if head else body, ctype,
                       {"Cache-Control": "no-store, no-cache, must-revalidate"})
            return
        if path in ("/dynbg", "/dynbg.json"):
            # v4.113★ 动态背景配置(脚本每 15s 拉一次, 跟随开关/编号变化)
            try:
                payload = dynbg_json()
            except Exception as e:
                self._send(500, json.dumps({"error": "%s: %s" % (type(e).__name__, e)}, ensure_ascii=False),
                           "application/json; charset=utf-8")
                return
            self._send(200, json.dumps(payload, ensure_ascii=False), "application/json; charset=utf-8")
            return
        if path in ("/skins", "/skins.json"):
            try:
                payload = skins_json()
            except Exception as e:
                self._send(500, json.dumps({"error": "%s: %s" % (type(e).__name__, e)}, ensure_ascii=False),
                           "application/json; charset=utf-8")
                return
            self._send(200, json.dumps(payload, ensure_ascii=False), "application/json; charset=utf-8")
            return
        if path in ("/scripts", "/scripts.json"):
            # 全能扩展管理页「获取新脚本」用的清单(纯元信息, 无代码)
            self._send(200, json.dumps(scripts_json(), ensure_ascii=False),
                       "application/json; charset=utf-8")
            return
        # 皮肤替换脚本的安装入口(和 serve_userscript 一样: 打开就弹 TM 安装页)
        code, body, ctype = read_script(path)
        if code == 200 or path.endswith(".user.js"):
            self._send(code, b"" if head else body, ctype)
            return
        self._send(404, "not found", "text/plain; charset=utf-8")

    def _index_html(self):
        return index_html()


# ----------------------------------------------------------- v4.87★ 请求日志 / 服务状态 ----
# 用户原话: "怎么检测呢?" —— 规则配错【不报错】, 唯一靠得住的证据是"到底有没有人来拉
# /rules、游戏到底请求了哪张图、哪张 404"。以前这些信息只在浏览器控制台里一闪而过,
# 这里存成列表, 页面上能翻。
_REQ_LOG = []
_REQ_LOG_MAX = 400
_REQ_LOCK = threading.Lock()
_SIZE_CACHE = {}          # 官方原图尺寸缓存(url → (w,h)): 比对要联网, 别每次点都重下


def code_mtime_str():
    """core/res_server.py 最后改动时间 —— 和进程启动时间一起看, 就知道页面上跑的是不是最新代码。"""
    try:
        return time.strftime("%m-%d %H:%M:%S", time.localtime(
            os.path.getmtime(os.path.abspath(__file__))))
    except Exception:
        return "?"


def code_is_stale():
    """代码比【当前进程】新 = 你看到的页面是旧代码生成的, 要重启助手。"""
    try:
        return os.path.getmtime(os.path.abspath(__file__)) > _BOOT_TS
    except Exception:
        return False


def note_request(path, code, method="GET", ua="", note=""):
    """记一条请求。**两个** Handler(独立服务 / 助手主端口)都要在发响应前调一次 ——
    只挂一边就会漏掉一半请求, 页面上的日志会看起来"没人来拉过"。
    """
    try:
        with _REQ_LOCK:
            _REQ_LOG.append({"t": time.time(), "ts": time.strftime("%H:%M:%S"),
                             "m": str(method or "GET")[:8], "p": str(path or "")[:200],
                             "code": int(code), "ua": str(ua or "")[:100],
                             "note": str(note or "")[:80]})
            if len(_REQ_LOG) > _REQ_LOG_MAX:
                del _REQ_LOG[: len(_REQ_LOG) - _REQ_LOG_MAX]
    except Exception:
        return          # 记日志这件事自己失败, 绝不能影响正常响应


def req_log_json(limit=200, only=None):
    """最近的请求(新的在后)。only: rules(只看谁来拉过规则) / img / bad(只看 404 等)。"""
    try:
        n = max(1, min(int(limit or 200), _REQ_LOG_MAX))
    except Exception:
        n = 200
    with _REQ_LOCK:
        items = list(_REQ_LOG)[-n:]
        total = len(_REQ_LOG)
    if only == "rules":
        items = [x for x in items if x["p"].split("?")[0] == "/rules"]
    elif only == "img":
        items = [x for x in items if x["p"].split("?")[0].startswith("/img/")]
    elif only == "bad":
        items = [x for x in items if x["code"] >= 400]
    return {"count": len(items), "total": total, "only": only or "", "items": items}


def status_json(base=None):
    """服务状态 —— 回答"我改了, 到底生效没有"。

      rev            规则指纹: 它变了才说明"改的东西已经进到脚本要的那份数据里"
      lastPull       脚本/扩展最后一次来拉 /rules 的时间 —— 空 = 【从来没人来拉过】
                     (这就是"配好了却没生效"最常见的原因: 脚本压根没在工作)
      img404         游戏请求过的图里 404 的张数 —— 有 404 就是规则里的文件名拼错了
    """
    from core import csv_loader
    b = str(base or "").rstrip("/") or base_url()
    season = RR.norm_season(csv_loader.CURRENT_SEASON) or "current"
    err = ""
    try:
        info = rules_json(base=b)
        rev, n_rules = info.get("rev", ""), len(info.get("rules") or [])
    except Exception as e:
        rev, n_rules, err = "", 0, "规则读取失败: %s" % e
    try:
        n_custom = len(RR.load_rules())
    except Exception:
        n_custom = 0
    n_img = 0
    if os.path.isdir(RR.IMG_DIR):
        n_img = len([f for f in os.listdir(RR.IMG_DIR)
                     if os.path.splitext(f)[1].lower() in _IMG_EXT_CANDIDATES])
    with _REQ_LOCK:
        pulls = [x for x in _REQ_LOG if x["p"].split("?")[0] == "/rules"]
        imgs = [x for x in _REQ_LOG if x["p"].split("?")[0].startswith("/img/")]
    last = pulls[-1] if pulls else None
    bad = [x for x in imgs if x["code"] >= 400]
    h = health_json(base=b)
    return {"base": b, "season": season, "rev": rev, "rules": n_rules,
            "custom": n_custom, "imgs": n_img,
            "lastPull": (last or {}).get("ts", ""),
            "lastPullAgo": int(time.time() - last["t"]) if last else None,
            "pulls": len(pulls), "imgHits": len(imgs), "img404": len(bad),
            "badList": [x["p"].split("?")[0] for x in bad[-10:]],
            "issues": h.get("count", 0), "imgDir": RR.IMG_DIR, "err": err,
            "pid": os.getpid(), "boot": _BOOT_STR, "code": code_mtime_str(),
            "stale": code_is_stale()}


def _head_size(b):
    """从图片【文件头】解宽高(PNG/JPEG/GIF/WEBP) —— 不解码整张图, 只够比尺寸用。"""
    import struct
    if len(b) >= 24 and b[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", b[16:24])
    if len(b) > 4 and b[:2] == b"\xff\xd8":                       # JPEG: 找 SOFn
        i = 2
        while i < len(b) - 9:
            if b[i] != 0xFF:
                i += 1
                continue
            m = b[i + 1]
            if m in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                hh, ww = struct.unpack(">HH", b[i + 5:i + 9])
                return int(ww), int(hh)
            if m in (0xD8, 0xD9) or 0xD0 <= m <= 0xD7:
                i += 2
                continue
            if i + 4 > len(b):
                break
            i += 2 + struct.unpack(">H", b[i + 2:i + 4])[0]
        return 0, 0
    if len(b) >= 10 and b[:3] == b"GIF":
        return struct.unpack("<HH", b[6:10])
    if len(b) >= 30 and b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        tag = b[12:16]
        if tag == b"VP8X":                                        # 有 alpha/ICC 的扩展型
            return (1 + int.from_bytes(b[24:27], "little"),
                    1 + int.from_bytes(b[27:30], "little"))
        if tag == b"VP8L":                                        # 无损
            bits = int.from_bytes(b[21:25], "little")
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        if tag == b"VP8 ":                                        # 有损: 找同步码
            i = b.find(b"\x9d\x01\x2a", 20)
            if i > 0 and i + 8 < len(b):
                return (int.from_bytes(b[i + 4:i + 6], "little") & 0x3FFF,
                        int.from_bytes(b[i + 6:i + 8], "little") & 0x3FFF)
    return 0, 0


def remote_image_size(url, timeout=6.0):
    """官方原图尺寸 (w, h): 只取文件头解宽高 —— 不整张下载, 所以很便宜。

    v4.109★ 优先走 core.resource_rules 那份 —— 以前本文件有一份【自己的】探测
      (自带内存缓存 _SIZE_CACHE), 与 resource_rules 里那份各探各的: 同一个地址
      会被两套缓存各请求一次。那边功能更全(带图片格式 fmt / 失败原因说明 /
      磁盘持久缓存, 重启后不用重探), 所以统一先问它。

      它没量到(或那份不可用)时退回本文件这份老实现 —— 返回类型 (w, h) 没变,
      调用点(size_check_json)零改动。
    """
    u = str(url or "").strip()
    if not u:
        return 0, 0
    try:
        (w, h, _fmt), _why = RR.remote_image_size(u, timeout=timeout)
        if w and h:
            _SIZE_CACHE[u] = (int(w), int(h))
            return int(w), int(h)
    except Exception:
        _SIL.note("core/res_server.py:1568", "(w, h, _fmt), _why = RR.remote_image_size(u, t")
    if u in _SIZE_CACHE:
        return _SIZE_CACHE[u]
    w = h = 0
    try:
        from urllib.request import Request, urlopen
        req = Request(u, headers={"Range": "bytes=0-65535",
                                  "User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=timeout) as r:
            w, h = _head_size(r.read(65536))
    except Exception:
        _SIZE_CACHE[u] = (0, 0)          # 量不到也缓存住: 免得每次点都重等一个超时
        return 0, 0
    _SIZE_CACHE[u] = (int(w), int(h))
    return int(w), int(h)


def size_check_json(base=None, limit=20):
    """尺寸比对: 你放的图 vs 游戏原来那张。差太多 = 会拉伸/变形。

    只比【已经生效的本地图】且只比【你自己配的】(默认清单几百条全比一遍要发几百个请求)。
    官方尺寸量不到也【点名】—— 那通常说明匹配串拼出来的地址是错的(官方那张根本不存在)。
    """
    try:
        from core.atlas import image_size as _isz
    except Exception:
        _isz = None
    from core import csv_loader
    b = str(base or "").rstrip("/") or base_url()
    season = RR.norm_season(csv_loader.CURRENT_SEASON) or "current"
    custom_pats = set()
    try:
        for r in RR.load_rules():
            p = str(r.get("pattern") or "").strip()
            if p:
                custom_pats.add(p)
    except Exception:
        custom_pats = set()
    todo = []
    try:
        for r in RR.build_rules(season):
            pat = str(r.get("pattern") or "").strip()
            tgt = str(r.get("target") or "").strip()
            if not pat or pat not in custom_pats or not tgt or RR.is_remote_target(tgt):
                continue
            if not RR.target_exists(tgt):
                continue
            todo.append({"pattern": pat, "name": str(r.get("name") or ""), "target": tgt,
                         "official": RR.official_target_url(pat)})
            if len(todo) >= max(1, min(int(limit or 20), 60)):
                break
    except Exception as e:
        return {"error": "读规则失败: %s" % e, "items": [], "count": 0}
    urls = [t["official"] for t in todo]
    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=6) as ex:
            rs = list(ex.map(lambda u: remote_image_size(u), urls))
    except Exception:
        rs = [remote_image_size(u) for u in urls]
    items = []
    for t, (ow, oh) in zip(todo, rs):
        lw = lh = 0
        if _isz is not None:
            try:
                lw, lh = _isz(t["target"])
            except Exception:
                lw = lh = 0
        it = {"pattern": t["pattern"], "name": t["name"], "official": t["official"],
              "ow": int(ow or 0), "oh": int(oh or 0), "lw": int(lw or 0), "lh": int(lh or 0),
              "level": "ok", "msg": ""}
        if not ow or not oh:
            it["level"] = "warn"
            it["msg"] = "官方那张量不到(地址可能不对 / 被墙) —— 也顺手说明: 这个匹配串拼出来的原图地址有问题"
        elif not lw or not lh:
            it["level"] = "warn"
            it["msg"] = "本地这张读不出尺寸(文件坏了?)"
        else:
            ra = (lw / float(lh)) if lh else 0
            rb = (ow / float(oh)) if oh else 0
            d = abs(ra - rb) / rb if rb else 0
            cross = (ra > 1.2 and rb < 0.85) or (ra < 0.85 and rb > 1.2)
            if cross:
                it["level"] = "bad"
                it["msg"] = "横竖反了: 官方 %dx%d(%s), 你放的是 %dx%d(%s) —— 会明显变形"
                it["msg"] = it["msg"] % (ow, oh, "横" if rb > 1 else "竖", lw, lh,
                                         "横" if ra > 1 else "竖")
            elif d > 0.2:
                it["level"] = "warn"
                it["msg"] = "比例差 %.0f%%: 官方 %dx%d, 你放的是 %dx%d —— 会被拉伸"
                it["msg"] = it["msg"] % (d * 100, ow, oh, lw, lh)
            else:
                it["msg"] = "比例对得上(官方 %dx%d / 你的 %dx%d)"
                it["msg"] = it["msg"] % (ow, oh, lw, lh)
        items.append(it)
    return {"base": b, "count": len(items), "items": items}


def official_json(q="", base=None):
    """查【换之前的地址】—— 用户原话: "换之前的地址呢? 怎么查找?"

    输入资源名(陆逊)或匹配串(tavernChess/piece/luxun.png), 返回:
      official  游戏【现在】加载的那张的完整 URL(换之前/没换时看到的就是它)
      candidates 名字里含关键词的规则(帮你从"我记得叫陆什么"找到确切的匹配串)
      rule      这条现在配了什么(已替换 / 还没配)
    """
    from core import csv_loader
    b = str(base or "").rstrip("/") or base_url()
    season = RR.norm_season(csv_loader.CURRENT_SEASON) or "current"
    key = str(q or "").strip()
    pat = key
    try:
        pat = _norm_pattern_input(key) if key else ""
    except Exception:
        pat = key
    # 只有"长得像匹配串"的输入才去拼官方地址 —— 输「陆逊」这种中文名时不能拿它当路径拼,
    #   否则会拼出 https://…/assets/陆逊 这种鬼东西(实测踩到过), 正确做法是走下面的候选搜索。
    low = key.lower()
    like_path = ("/" in key) or ("." in key and low.endswith((".png", ".jpg", ".jpeg", ".webp",
                                                              ".gif", ".mp3", ".ogg", ".atlas")))
    official = RR.official_target_url(pat) if (pat and like_path) else ""
    cands, cur = [], None
    try:
        for r in RR.build_rules(season):
            p = str(r.get("pattern") or "")
            nm = str(r.get("name") or "")
            if key and (key.lower() in p.lower() or key in nm or (pat and pat in p)):
                if len(cands) < 12:
                    cands.append({"pattern": p, "name": nm,
                                  "official": RR.official_target_url(p),
                                  "target": str(r.get("target") or "")})
            if cur is None and key and (p == pat or p.endswith(key) or key in p):
                cur = {"pattern": p, "name": nm, "official": RR.official_target_url(p),
                       "target": str(r.get("target") or "")}
    except Exception as e:
        return {"error": "读规则失败: %s" % e, "input": key}
    # 规则里有这条, 就用【规则里那条匹配串】拼出来的地址 —— 兜底模板会漏掉 runtime/ 这一段,
    #   实测输 "tavernChess/piece/luxun.png" 拼出来少了 runtime, 而真身是带 runtime 的那个。
    if cur:
        official = cur["official"]
        pat = cur["pattern"]
    return {"base": b, "input": key, "pattern": pat, "official": official,
            "current": cur, "candidates": cands, "count": len(cands)}


def all_json(q="", only="", limit=800):
    """【全部资源】清单 —— 用户原话: "还有不全" / "能不能再找全部"。

    要回答"缺哪些", 得先有"总共有哪些": 把赛季自带清单(棋子+主公, 几百条)和你自己配的
    合在一起, 每条给出 —— 名字 / 匹配串 / **换之前的地址** / 现在配了什么 / 有没有放图。
    筛选 only: mine(只我配的) · ready(已生效) · nopic(还没放图) · bad(配了但图不在)。
    """
    from core import csv_loader
    season = RR.norm_season(csv_loader.CURRENT_SEASON) or "current"
    key = str(q or "").strip().lower()
    try:
        n = max(1, min(int(limit or 800), 3000))
    except Exception:
        n = 800
    custom_pats = set()
    try:
        for r in RR.load_rules():
            p = str(r.get("pattern") or "").strip()
            if p:
                custom_pats.add(p)
    except Exception:
        custom_pats = set()
    items = []
    try:
        for r in RR.build_rules(season):
            pat = str(r.get("pattern") or "").strip()
            nm = str(r.get("name") or "")
            tgt = str(r.get("target") or "").strip()
            mine = pat in custom_pats
            # v4.87★ 状态分四类。关键是: 自带清单那几百条的 target 本来就是【官方 URL】
            #   (没换过原图), 那是正常状态, 不能算"没放图/图不在" —— 上一版就因为它把
            #   352 条全标成"图不在", 反而看不出真正的乱子。
            if not tgt:
                state = "没配"
            elif RR.is_remote_target(tgt):
                state = "用官方原图(没换)"
            elif RR.target_exists(tgt):
                state = "已替换"
            elif mine:
                state = "图不在(配了但文件没了)"
            else:
                # 自带清单那几百条本来就没放图(rules_json 会自动跳过它们, 不下发),
                #   【不能】算问题 —— 算了就把真正该看的少数几条淹没了。
                state = "没放图(正常)"
            # 匹配串拼坏了的: 实测有一条 pattern 直接以 .sanguosha.com/… 开头, 拼出来的
            #   官方地址里带着 "/." —— 这种就是用户说的"改的有点乱", 必须点名。
            off = RR.official_target_url(pat)
            dubious = ("/." in off) or (pat.count("http") > 1) or (".." in pat)
            if key and (key not in pat.lower() and key not in nm.lower()):
                continue
            if only == "mine" and not mine:
                continue
            if only == "ready" and state != "已替换":
                continue
            if only == "nopic" and state == "已替换":
                continue
            if only == "bad" and state != "图不在(配了但文件没了)":
                continue
            if only == "dubious" and not dubious:
                continue
            items.append({"name": nm, "pattern": pat, "mine": mine, "official": off,
                          "target": tgt, "state": state, "dubious": dubious,
                          "local": bool(tgt) and not RR.is_remote_target(tgt)})
            if len(items) >= n:
                break
    except Exception as e:
        return {"error": "读规则失败: %s" % e, "items": []}
    return {"season": season, "count": len(items), "limit": n, "only": only or "",
            "q": q or "", "items": items}


def open_img_dir():
    """一键打开「我的图」文件夹 —— 放图不用手找路径(本机操作, 服务在本机才有意义)。"""
    d = RR.ensure_img_dir()
    try:
        os.startfile(d)
        return {"ok": True, "dir": d}
    except Exception as e:
        return {"ok": False, "dir": d, "error": "%s: %s" % (type(e).__name__, e)}


def health_json(base=None):
    """v4.87★ 体检 —— 把"配了但其实不生效"的规则逐条点名。

    为什么需要它(用户原话: "我现在改的有点乱; 还有不全"):
      规则配错【不报错、也不提示】, 游戏里就是"原样不动", 人对着表格根本猜不出是哪条:
        · 目标文件本机找不到 → 那条规则直接不下发(静默跳过);
        · 同一个匹配串配了两条 → 只有后一条生效, 前一条白配;
        · 目标没填 → 同样不下发。
      这里按 /rules 的同一口径(build_rules)过一遍, 把这几类点出来。
    """
    from core import csv_loader
    b = str(base or "").rstrip("/") or base_url()
    season = RR.norm_season(csv_loader.CURRENT_SEASON) or "current"
    try:
        rules = RR.build_rules(season)
    except Exception as e:
        return {"ok": False, "base": b, "season": season, "total": 0, "ready": 0,
                "remote": 0, "issues": [{"level": "bad", "pattern": "", "name": "",
                                         "msg": "读规则失败: %s" % e}], "count": 1}
    # 只把【用户自己配的】规则当成要点名的对象: 默认清单里 360+ 条本来就都没放图,
    #   那不是"配错了", 只是"还没用上"(不放图 = 不替换, 本来就这样)。全列出来会淹掉真问题。
    custom_pats = set()
    try:
        for r in RR.load_rules():
            p = str(r.get("pattern") or "").strip()
            if p:
                custom_pats.add(p)
    except Exception:
        custom_pats = set()
    issues, seen = [], {}
    n_ready = n_remote = n_idle = 0
    for r in rules:
        pat = str(r.get("pattern") or "").strip()
        tgt = str(r.get("target") or "").strip()
        name = str(r.get("name") or "")
        if not pat:
            continue
        mine = pat in custom_pats
        if mine:
            if pat in seen:
                seen[pat] += 1
                issues.append({"level": "warn", "pattern": pat, "name": name,
                               "msg": "同一个匹配串配了 %d 条 —— 只有最后一条生效" % seen[pat]})
            else:
                seen[pat] = 1
        if not tgt:
            if mine:
                issues.append({"level": "bad", "pattern": pat, "name": name,
                               "msg": "目标没填 —— 这条不会下发"})
            else:
                n_idle += 1
            continue
        if RR.is_remote_target(tgt):
            n_remote += 1          # 网络地址不走本机图片服务, 存不存在得问对方服务器(不在这里探测)
            continue
        if not RR.target_exists(tgt):
            if mine:
                issues.append({"level": "bad", "pattern": pat, "name": name,
                               "msg": "缺图: 本机找不到 %s —— 会被静默跳过, 游戏里原样不动"
                                      % os.path.basename(tgt)})
            else:
                n_idle += 1
            continue
        n_ready += 1
    return {"ok": not any(i.get("level") == "bad" for i in issues), "base": b,
            "season": season, "total": len(rules), "ready": n_ready,
            "remote": n_remote, "idle": n_idle, "mine": len(custom_pats),
            "issues": issues, "count": len(issues)}


def files_json(base=None):
    """v4.87★ 列出本机图片落点里的图(我的图/ + 参考_配置表/资源替换/)。

    为什么需要它: /img/<文件名> 只能按名字取图, 而【名字得先知道】—— 以前没有地方能看
    "我现在到底有哪些图", 配规则时只能去文件夹里翻。这里把图连缩略图地址一起列出来,
    点一下就能复制成规则目标。
    """
    b = str(base or "").rstrip("/") or base_url()
    out, dirs = [], []
    try:
        from core.atlas import image_size as _isz
    except Exception:
        _isz = None
    for d in (getattr(RR, "IMG_DIR", None), getattr(RR, "REF_DIR", None)):
        if not d or not os.path.isdir(d):
            continue
        dirs.append(d)
        try:
            names = sorted(os.listdir(d))
        except Exception as e:
            out.append({"name": "(读不了这个目录: %s)" % e, "url": "", "size": 0, "w": 0, "h": 0})
            continue
        for fn in names:
            if os.path.splitext(fn)[1].lower() not in _IMG_EXT_CANDIDATES:
                continue
            p = os.path.join(d, fn)
            if not os.path.isfile(p):
                continue
            try:
                st = os.stat(p)
                sz = int(st.st_size)
                mt = int(st.st_mtime)
            except Exception:
                sz, mt = 0, 0
            w = h = 0
            if _isz is not None:
                try:
                    w, h = _isz(p)
                except Exception:
                    w = h = 0
            out.append({"name": fn, "dir": d, "url": "%s/img/%s" % (b, fn),
                        "size": sz, "w": int(w or 0), "h": int(h or 0), "mtime": mt})
    return {"base": b, "dirs": dirs, "files": out, "count": len(out)}


def _stale_note():
    """页面底部那行"我是哪份代码生成的" —— 用户原话: "进去了但是没效果? 那个页面有东西,
    但是都不能点?" 大多数时候就是【看的页面是旧代码生成的】, 这行让它不用猜。

    刻意写成【静态 HTML】(不是 JS 渲染): JS 万一崩了, 按钮全失效, 这行照样看得见。
    """
    if code_is_stale():
        return ('<b style="color:#ff8080">⚠ 这个页面是【旧代码】生成的</b> —— 代码文件改动于 '
                + code_mtime_str() + ', 而这个进程 ' + _BOOT_STR + ' 就启动了(比代码早)。'
                '<br>→ 改完 .py 必须<b>关掉助手再重新打开</b>才生效: 界面上的按钮是在助手'
                '<b>进程内</b>起的服务, 不重启就一直用老代码(所以新按钮看不到 / 点了没反应)。'
                '<br>→ 如果重启后还是这样: 端口可能被<b>更早的旧进程</b>占着, 旧进程抢先监听了, '
                '关掉所有 python 进程再开助手。')
    return ('这个页面由进程 %d 生成(启动于 %s) · 代码文件最后改动 %s —— '
            '<span class="ok">就是最新这份代码</span>。'
            % (os.getpid(), _BOOT_STR, code_mtime_str()))


def index_html(base=None):
    """资源服务页(/ 或 /res) —— 当前地址、规则条数、脚本安装入口都在这一页上。

    v4.70★ 由"本模块的独立服务"和"助手主端口的 /res"共用, 所以 base 由调用方给
      (主端口那边传自己的地址; 不传就用本模块默认端口)。
    """
    try:
        info = rules_json(base=base)
        n = info["count"]
        base = info["base"]
        season = info["season"]
    except Exception as e:
        n, base, season = 0, (base or base_url()), "?"
        sys.stderr.write("[res_server] 统计规则失败: %s\n" % e)
    return """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>自走棋助手 · 资源替换服务</title>
<style>
.bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:12px 0}
button{background:#2f2f38;color:#eee;border:1px solid #4a4a56;border-radius:5px;padding:5px 12px;
  cursor:pointer;font-size:13px}
button:hover{border-color:#e0b060}
button.pri{background:#4a3b1e;border-color:#e0b060;color:#e0b060;font-weight:bold}
.hint{color:#9a9a9a;font-size:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:10px;margin-top:10px}
.card{background:#33333b;border:1px solid #4a4a56;border-radius:6px;padding:8px}
.hd{font-size:12px;color:#e0b060;margin-bottom:6px;display:flex;justify-content:space-between;gap:6px;
  word-break:break-all}
.tag{font-size:10px;padding:1px 5px;border-radius:3px;background:#4a4a56;color:#ccc;white-space:nowrap}
.imgbox{width:100%%;height:120px;object-fit:contain;background:#22222a;border-radius:4px;display:block}
.ops{margin-top:6px}.ops button{padding:2px 8px;font-size:11px}
.ok{color:#9fd4a0}.warn{color:#c8a24a}.bad{color:#ff8080}
</style>
<body style="background:#2a2a2a;color:#eee;font:14px/1.8 'Microsoft YaHei UI',sans-serif;padding:28px;max-width:820px">
<h2 style="color:#e0b060">资源替换服务(赛季 %s)</h2>
<p>★ <a style="color:#ffd98a;font-weight:bold" href="/gallery">打开「资源图库」管理页 —— 添加/编辑/选图/导入/导出, 点卡片直接换图, 保存后游戏端自动生效</a></p>
<ol>
  <li>当前地址: <b style="color:#7fd4ff">%s</b> —— 规则里的目标填成 <code>%s/img/文件名.png</code></li>
  <li>本机已就绪的规则: <b>%d</b> 条(没放图的会被自动跳过, 不会让棋子变透明)</li>
  <li>规则清单(换肤脚本自动读这个): <a style="color:#7fd4ff" href="/rules">/rules</a>
      · 官方立绘候选(扩展管理页用): <a style="color:#7fd4ff" href="/skins">/skins</a></li>
  <li>主脚本(换肤功能也在里面; 全能扩展从这里取最新版 —— 不内置, 所以改完源脚本
      <b>不用重新打包</b>):
      <a style="color:#7fd4ff" href="/sgs_bridge.user.js">安装 / 更新 sgs_bridge.user.js</a>
      · 扩展取的就是这一份: <a style="color:#7fd4ff" href="/ext/bridge.main.js">/ext/bridge.main.js</a></li>
  <li>独立换肤脚本(不装助手也能用):
      <a style="color:#7fd4ff" href="/%s">安装 / 更新 %s</a></li>
  <li>★ 浏览器扩展【全能版】(推荐 —— 油猴脚本本体 + GoRes 同款重定向 + 管理页, 一份顶两份,
      装了它就不用再装 Tampermonkey):
      <a style="color:#7fd4ff" href="/ext.zip">下载 sgs_ext.zip</a></li>
  <li>浏览器扩展【轻量版】(只做重定向, 要配 Tampermonkey 才完整):
      <a style="color:#7fd4ff" href="/res_ext.zip">下载 res_ext.zip</a>
      —— 两个都解压后到扩展管理页(Chrome: chrome://extensions / Edge: edge://extensions)
      → 开「开发者模式」→ 加载已解压的扩展程序</li>
</ol>
<div class="bar">
  <button class="pri" id="bAll">全部资源清单(找全/查缺哪些)</button>
  <button class="pri" id="bHealth">体检(哪些"配了但没生效")</button>
  <button id="bStatus">服务状态</button>
  <button id="bLog">请求日志</button>
  <button id="bSize">尺寸比对</button>
  <button id="bFiles">我的图</button>
  <button id="bFolder">打开「我的图」文件夹</button>
</div>
<div class="bar">
  <input id="qOff" size="34" placeholder="查「换之前的地址」: 输资源名(如 陆逊)或匹配串">
  <button id="bOff">查原地址</button>
  <span id="sum" class="hint"></span>
</div>
<div id="out"></div>
<p style="color:#999">这个地址只监听 127.0.0.1(只有本机能访问)。挂在助手主端口上时,
关掉助手就一起停 —— 这也是"不用再点启动、不会指向空端口"的原因。</p>
<script>
var out = document.getElementById('out'), sum = document.getElementById('sum'), timer = null;
function esc(s){ return String(s==null?'':s).replace(/[&<>"]/g,function(c){
  return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
function copyUrl(u){ navigator.clipboard && navigator.clipboard.writeText(u); }
// v4.87★ 复制按钮一律走【data-u + 事件委托】, 不在 JS 字符串里嵌引号:
//   以前写 onclick="copyUrl(\'' + url + '\')" —— 但这段 HTML 是 Python 三引号字符串,
//   \' 会被 Python 当转义吃掉, 页面拿到的 JS 变成 copyUrl(''+url+'') → 整段脚本语法错误
//   → 所有按钮的 onclick 都绑不上(页面看着正常, 点了全没反应)。用属性传值就没这个坑。
document.addEventListener('click', function(e){
  var b = e.target;
  if (!b || b.tagName !== 'BUTTON') return;
  var u = b.getAttribute('data-u');
  if (u === null) return;
  copyUrl(u);
  var old = b.textContent;
  b.textContent = '已复制';
  setTimeout(function(){ b.textContent = old; }, 900);
});
function stopTimer(){ if (timer) { clearInterval(timer); timer = null; } }
function tbl(head, rows){
  var h = '<table style="border-collapse:collapse;font-size:12px"><tr style="color:#9a9a9a">';
  head.forEach(function(x){ h += '<th align="left" style="padding:3px 8px 3px 0">' + x + '</th>'; });
  h += '</tr>' + rows + '</table>';
  return h;
}
function row(cells, color){
  var h = '<tr style="border-top:1px solid #3a3a44">';
  cells.forEach(function(c, i){
    h += '<td style="padding:3px 8px 3px 0;' + (i === 0 && color ? 'color:' + color : '')
      + '">' + c + '</td>';
  });
  return h + '</tr>';
}
document.getElementById('bAll').onclick = function(){
  stopTimer();
  out.innerHTML = '<p class="hint">正在列全部资源…</p>'
    + '<div class="bar"><input id="qAll" size="24" placeholder="缩小范围: 名字或匹配串">'
    + '<button id="alAll">全部</button><button id="alMine">只看我配的</button>'
    + '<button id="alReady">只看已替换的</button><button id="alNopic">只看还没换的</button>'
    + '<button id="alBad">只看有问题的</button>'
    + '<button id="alDub">只看匹配串拼坏了的</button></div><div id="alBox"></div>';
  var box = document.getElementById('alBox');
  var show = function(only){
    var q = document.getElementById('qAll').value.trim();
    fetch('/resapi/all?q=' + encodeURIComponent(q) + '&only=' + (only || '') + '&limit=800')
      .then(function(r){ return r.json(); }).then(function(d){
      if (d.error) { box.innerHTML = '<p class="bad">读清单失败: ' + esc(d.error) + '</p>'; return; }
      var rows = '';
      d.items.forEach(function(i){
        var c = i.state === '已替换' ? '#9fd4a0'
          : (i.state === '图不在(配了但文件没了)' ? '#ff8080' : '#9a9a9a');
        var st = '<span style="color:' + c + '">' + esc(i.state) + '</span>';
        rows += row([(i.dubious ? '<span style="color:#ff8080">⚠ </span>' : '')
          + '<span style="color:#e0b060">' + esc(i.name || '-') + '</span>',
          '<span class="hint" style="font-size:11px">' + esc(i.pattern) + '</span>',
          '<span style="color:#7fd4ff;font-size:11px">' + esc(i.official) + '</span>',
          '<button style="padding:1px 6px;font-size:10px" data-u="' + esc(i.official)
            + '">复制原地址</button>',
          i.mine ? '<span class="ok">我配的</span>' : '<span class="hint">自带</span>', st]);
      });
      box.innerHTML = tbl(['资源名', '匹配串', '换之前的地址', '', '来源', '状态'], rows)
        + '<p class="hint">共 ' + d.count + ' 条 —— 这就是「总共有哪些」; 状态列能看出'
        + '哪些还没配 / 哪些配了但图不在。</p>';
      sum.textContent = d.count + ' 条 · ' + (only || '全部') + (q ? ' · 「' + q + '」' : '');
    });
  };
  document.getElementById('alAll').onclick = function(){ show(''); };
  document.getElementById('alMine').onclick = function(){ show('mine'); };
  document.getElementById('alReady').onclick = function(){ show('ready'); };
  document.getElementById('alNopic').onclick = function(){ show('nopic'); };
  document.getElementById('alBad').onclick = function(){ show('bad'); };
  document.getElementById('alDub').onclick = function(){ show('dubious'); };
  document.getElementById('qAll').onkeydown = function(e){
    if (e.key === 'Enter') show('');
  };
  show('');
};
document.getElementById('bStatus').onclick = function(){
  stopTimer(); out.innerHTML = '<p class="hint">正在读状态…</p>';
  fetch('/resapi/status').then(function(r){ return r.json(); }).then(function(d){
    if (d.error) { out.innerHTML = '<p class="bad">读状态失败: ' + esc(d.error) + '</p>'; return; }
    sum.textContent = 'rev ' + d.rev + ' · 规则 ' + d.rules + ' 条(你配的 ' + d.custom + ')';
    var pull = d.lastPull
      ? '<span class="ok">' + esc(d.lastPull) + '</span>(距今 ' + d.lastPullAgo + ' 秒, 共拉过 '
        + d.pulls + ' 次)'
      : '<span class="bad">从来没有人来拉过 /rules —— 换肤脚本或扩展没在工作, 这就是"配好了没生效"最常见的原因</span>';
    var h = tbl(['项目', '值'],
      row(['规则指纹 rev', '<code style="color:#7fd4ff">' + esc(d.rev) + '</code>'
        + '<span class="hint"> —— 改完规则它变了, 才说明改动进了脚本要的那份数据</span>'])
      + row(['规则条数', d.rules + ' 条(其中你自己配的 ' + d.custom + ' 条)'])
      + row(['脚本最后拉取', pull])
      + row(['图片请求', d.imgHits + ' 次 · <span class="' + (d.img404 ? 'bad' : 'ok') + '">404 '
        + d.img404 + ' 张</span>' + (d.img404 ? '<span class="hint"> —— '
        + esc((d.badList || []).join(', ')) + '</span>' : '')])
      + row(['本机图片', d.imgs + ' 张 · 目录 <span class="hint">' + esc(d.imgDir) + '</span>'])
      + row(['体检问题', (d.issues ? '<span class="warn">' + d.issues + ' 条</span>'
        : '<span class="ok">0 条</span>')]));
    out.innerHTML = h;
  }).catch(function(e){ out.innerHTML = '<p class="bad">读状态失败: ' + esc(e) + '</p>'; });
};
document.getElementById('bLog').onclick = function(){
  stopTimer();
  out.innerHTML = '<p class="hint">看谁能说明什么: 有 /rules 的 200 = 脚本来拉过规则; '
    + '有 /img/xxx.png 的 404 = 规则里的文件名拼错了; 【一条都没有】 = 根本没人来找过这个服务。</p>'
    + '<div class="bar"><button id="lgAll">全部</button><button id="lgRules">只看 /rules</button>'
    + '<button id="lgImg">只看 /img</button><button id="lgBad">只看 404</button></div><div id="lgBox"></div>';
  var box = document.getElementById('lgBox');
  var show = function(only){
    fetch('/resapi/log?limit=200' + (only ? '&only=' + only : '')).then(function(r){
      return r.json(); }).then(function(d){
      var rows = '';
      d.items.slice().reverse().forEach(function(x){
        var c = x.code >= 400 ? '#ff8080' : (x.code >= 300 ? '#c8a24a' : '#9fd4a0');
        var who = /Mozilla/.test(x.ua) ? '浏览器/游戏' : (x.ua ? '脚本' : '-');
        rows += row(['<span class="hint">' + esc(x.ts) + '</span>', esc(x.m),
          '<span style="color:#7fd4ff">' + esc(x.p) + '</span>',
          '<span style="color:' + c + '">' + x.code + '</span>',
          '<span class="hint">' + who + '</span>']);
      });
      box.innerHTML = tbl(['时间', '方法', '路径', '码', '谁'], rows)
        + '<p class="hint">共 ' + d.count + ' 条(缓存上限 ' + d.total + ')</p>';
      sum.textContent = '每 3 秒自动刷新 · 现在看: ' + (only || '全部');
    });
  };
  document.getElementById('lgAll').onclick = function(){ show(''); };
  document.getElementById('lgRules').onclick = function(){ show('rules'); };
  document.getElementById('lgImg').onclick = function(){ show('img'); };
  document.getElementById('lgBad').onclick = function(){ show('bad'); };
  show('');
  timer = setInterval(function(){ show(''); }, 3000);
};
document.getElementById('bSize').onclick = function(){
  stopTimer(); out.innerHTML = '<p class="hint">正在比对(要联网量官方原图, 几秒钟)…</p>';
  fetch('/resapi/sizeCheck?limit=20').then(function(r){ return r.json(); }).then(function(d){
    if (d.error) { out.innerHTML = '<p class="bad">比对失败: ' + esc(d.error) + '</p>'; return; }
    if (!d.count) { out.innerHTML = '<p class="warn">还没有"已放好本地图"的自定义规则可比。</p>'; return; }
    var rows = '', nbad = 0;
    d.items.forEach(function(i){
      var c = i.level === 'bad' ? '#ff8080' : (i.level === 'warn' ? '#c8a24a' : '#9fd4a0');
      if (i.level !== 'ok') nbad++;
      rows += row([esc(i.name || '-'), i.ow + '×' + i.oh, i.lw + '×' + i.lh,
        '<span style="color:' + c + '">' + esc(i.msg) + '</span>'], c);
    });
    sum.textContent = d.count + ' 条已比对 · 需要注意 ' + nbad + ' 条';
    out.innerHTML = tbl(['资源名', '官方原图', '你放的', '结论'], rows);
  }).catch(function(e){ out.innerHTML = '<p class="bad">比对失败: ' + esc(e) + '</p>'; });
};
document.getElementById('bFolder').onclick = function(){
  fetch('/resapi/openFolder').then(function(r){ return r.json(); }).then(function(d){
    sum.textContent = d.ok ? '已打开: ' + d.dir : ('打开失败: ' + (d.error || '?'));
  });
};
document.getElementById('bOff').onclick = function(){
  var q = document.getElementById('qOff').value.trim();
  if (!q) { out.innerHTML = '<p class="warn">先输点东西: 资源名(陆逊)或匹配串(luxun.png)。</p>'; return; }
  out.innerHTML = '<p class="hint">正在查…</p>';
  fetch('/resapi/official?q=' + encodeURIComponent(q)).then(function(r){ return r.json(); })
    .then(function(d){
    if (d.error) { out.innerHTML = '<p class="bad">查询失败: ' + esc(d.error) + '</p>'; return; }
    var h = '<div class="card"><div class="hd"><span>换之前的地址(游戏原本加载的那张)</span>'
      + '<span class="tag">官方</span></div>'
      + '<div style="word-break:break-all;color:#7fd4ff;font-size:12px">' + esc(d.official || '(拼不出来)')
      + '</div><div class="ops"><button data-u="' + esc(d.official) + '">复制</button>'
      + '<a style="color:#7fd4ff;font-size:11px;margin-left:8px" target="_blank" href="'
      + esc(d.official) + '">在浏览器里打开看看</a></div>'
      + '<p class="hint">匹配串: ' + esc(d.pattern) + (d.current
        ? ' · 现在配的目标是: ' + esc(d.current.target || '(空)') : ' · 这条还没配过') + '</p></div>';
    if (d.count) {
      var rows = '';
      d.candidates.forEach(function(c){
        rows += row(['<span style="color:#e0b060">' + esc(c.name || '-') + '</span>',
          '<span class="hint">' + esc(c.pattern) + '</span>',
          '<span style="color:#7fd4ff;font-size:11px">' + esc(c.official || '-') + '</span>',
          '<button style="padding:1px 6px;font-size:10px" data-u="' + esc(c.official)
            + '">复制</button>',
          c.target ? '<span class="ok">已替换</span>' : '<span class="hint">没配</span>']);
      });
      h += '<p class="hint" style="margin-top:10px">名字/匹配串里含「' + esc(d.input) + '」的 '
        + d.count + ' 条 —— 「换之前的地址」就是下面第三列(游戏没被替换时加载的就是它):</p>'
        + tbl(['资源名', '匹配串', '换之前的地址', '', '状态'], rows);
    }
    sum.textContent = '查到 ' + d.count + ' 条候选';
    out.innerHTML = h;
  }).catch(function(e){ out.innerHTML = '<p class="bad">查询失败: ' + esc(e) + '</p>'; });
};
document.getElementById('qOff').onkeydown = function(e){
  if (e.key === 'Enter') document.getElementById('bOff').click();
};
document.getElementById('bHealth').onclick = function(){
  out.innerHTML = '<p class="hint">正在体检…</p>';
  fetch('/resapi/health').then(function(r){ return r.json(); }).then(function(d){
    if (d.error) { out.innerHTML = '<p class="bad">体检失败: ' + esc(d.error) + '</p>'; return; }
    sum.textContent = '你配了 ' + d.mine + ' 条 · 会生效 ' + d.ready + ' 条 · 指向网络地址 '
      + d.remote + ' 条 · 问题 ' + d.count + ' 条 · 自带清单里还有 ' + d.idle
      + ' 条没放图(正常, 不放就不替换)';
    if (!d.count) { out.innerHTML = '<p class="ok">✓ 没有发现问题 —— 配了的规则都会下发。</p>'; return; }
    var h = '<table style="border-collapse:collapse;width:100%%"><tr style="color:#9a9a9a">'
      + '<th align="left">程度</th><th align="left">资源名</th><th align="left">说明</th>'
      + '<th align="left">匹配串</th></tr>';
    d.issues.forEach(function(i){
      var c = i.level === 'bad' ? '#ff8080' : '#c8a24a';
      h += '<tr style="border-top:1px solid #3a3a44"><td style="color:' + c + '">'
        + (i.level === 'bad' ? '不生效' : '疑似') + '</td><td>' + esc(i.name || '-') + '</td><td>'
        + esc(i.msg) + '</td><td style="color:#7fd4ff;font-size:11px">'
        + esc(i.pattern || '') + '</td></tr>';
    });
    out.innerHTML = h + '</table>';
  }).catch(function(e){ out.innerHTML = '<p class="bad">体检失败: ' + esc(e) + '</p>'; });
};
document.getElementById('bFiles').onclick = function(){
  stopTimer();
  out.innerHTML = '<p class="hint">正在列图…</p>';
  fetch('/resapi/files').then(function(r){ return r.json(); }).then(function(d){
    if (d.error) { out.innerHTML = '<p class="bad">列图失败: ' + esc(d.error) + '</p>'; return; }
    sum.textContent = '本机图片 ' + d.count + ' 张 · 落点: ' + (d.dirs || []).join(' | ');
    if (!d.count) { out.innerHTML = '<p class="warn">这两个目录里还没有图 —— 把 png/jpg/webp 放进'
      + '「我的图/」, 或在图库页直接上传。</p>'; return; }
    var h = '<div class="grid">';
    d.files.forEach(function(f){
      h += '<div class="card"><div class="hd"><span>' + esc(f.name) + '</span>'
        + '<span class="tag">' + (f.w && f.h ? f.w + '×' + f.h : '?')
        + (f.size ? ' · ' + Math.round(f.size/1024) + 'KB' : '') + '</span></div>'
        + '<img class="imgbox" src="' + f.url + '" loading="lazy">'
        + '<div class="ops"><button data-u="' + esc(f.url) + '">复制 /img 地址</button></div>'
        + '</div>';
    });
    out.innerHTML = h + '</div>';
  }).catch(function(e){ out.innerHTML = '<p class="bad">列图失败: ' + esc(e) + '</p>'; });
};
if (location.hash === '#health') document.getElementById('bHealth').click();
</script>
<p class="hint">%s</p>
</body></html>""" % (season, base, base, n, SWAP_SCRIPT, SWAP_SCRIPT, _stale_note())


def make_server(port=PORT):
    return ThreadingHTTPServer(("127.0.0.1", port), H)


def serve_in_thread(port=PORT):
    httpd = make_server(port)

    def _serve():
        try:
            httpd.serve_forever()
        except OSError as e:
            if getattr(e, "winerror", None) != 10038:   # 10038 = 关闭时的正常错误
                sys.stderr.write("[res_server] 服务线程退出: %s\n" % e)
        except Exception as e:
            sys.stderr.write("[res_server] 服务线程异常: %s\n" % e)

    t = threading.Thread(target=_serve, name="res-http", daemon=True)
    t.start()
    httpd._sgs_thread = t
    return httpd


def probe(port=PORT, timeout=1.0):
    """这个端口上是不是我们的图片服务(能取到 /rules 的 json 就是)。"""
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:%d/rules" % port, timeout=timeout) as r:
            head = r.read(200).decode("utf-8", "replace")
        return '"rules"' in head or '"count"' in head
    except Exception:
        return False


# ---------------------------------------------------------------- 会话内单例
_httpd = None
_port = None
_lock = threading.Lock()


def ensure(ports=None):
    """确保图片服务在跑 → (port, notes); 全起不来 → (None, notes)。"""
    global _httpd, _port, _active_port
    ports = ports or (PORT, PORT + 1, PORT + 2, PORT + 4, PORT + 24)
    notes = []
    with _lock:
        if _httpd is not None and _port:
            if probe(_port):
                _active_port = _port
                return _port, notes
            try:
                _httpd.server_close()
            except Exception as e:
                sys.stderr.write("[res_server] 关闭失效服务失败: %s\n" % e)
            notes.append("端口 %d 的服务已失效, 重新启动" % _port)
            _httpd = _port = None
        for p in ports:
            if probe(p):
                _port = p
                _active_port = p
                notes.append("图片服务已在运行(端口 %d)" % p)
                return p, notes
            try:
                _httpd = serve_in_thread(p)
            except OSError as e:
                notes.append("端口 %d 起不来(%s)" % (p, e))
                continue
            _port = p
            _active_port = p
            notes.append("已启动图片服务(端口 %d)" % p)
            return p, notes
        return None, notes


def shutdown():
    """关掉本进程起的图片服务(助手退出时调); 没起过就是空操作。"""
    global _httpd, _port, _active_port
    with _lock:
        h, _httpd, _port = _httpd, None, None
        _active_port = None
    if h is None:
        return
    t = getattr(h, "_sgs_thread", None)
    if t is not None and t.is_alive():
        try:
            h.shutdown()
        except Exception as e:
            sys.stderr.write("[res_server] shutdown 失败: %s\n" % e)
    try:
        h.server_close()
    except Exception as e:
        sys.stderr.write("[res_server] server_close 失败: %s\n" % e)


if __name__ == "__main__":
    p = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    try:
        httpd = make_server(p)
    except OSError:
        if probe(p):
            print("[提示] 端口%d 上已经有图片服务在跑: %s" % (p, base_url(p)))
            sys.exit(0)
        print("[错误] 端口%d 被别的程序占用了, 换一个: python core/res_server.py 8768" % p)
        sys.exit(1)
    _active_port = p   # v4.75★ 独立运行也要记住真实端口(否则 /gallery 页的 BASE 用默认值)
    globals()["_active_port"] = _active_port
    print("[OK] 本地图片服务: %s" % base_url(p))
    print("图片目录: %s" % RR.IMG_DIR)
    print("规则清单: %s/rules" % base_url(p))
    httpd.serve_forever()
