# -*- coding: utf-8 -*-
"""全能扩展的【主脚本下发源】—— 扩展不再内置脚本，改从助手这里拉最新版。

为什么要有这个模块
==================
以前主脚本是"抄"进扩展目录的（dev/_pack_ext.py 生成 bridge/sgs_ext/page/bridge.main.js，
manifest 里当 content script 内置）。那份复制品有两个躲不掉的问题：

  · 改了 bridge/sgs_bridge.user.js 之后，扩展里那份【还是旧的】，而且不报错 ——
    现象是"助手里改好了，游戏里还是老行为"，只能靠重跑打包 + 在浏览器里点"重新加载扩展"；
  · 用户想更新脚本，得回来重跑构建再手动重载扩展 —— 也就是本次需求里说的
    "不要内置脚本了，且可以更新脚本"。

现在：**唯一来源就是 bridge/sgs_bridge.user.js**，扩展每次从助手
`GET /ext/bridge.main.js` 取正文（先拿 `/ext/bridge.meta` 比 rev，变了才拉）。
本模块就是这条链的单一实现：渲染（去掉 TM 头 + 加存活标记）只写一份，
构建脚本（dev/_pack_ext.py）和运行时端点（core/bridge.py）都调它 ——
两边各写一份 = 迟早漂移，正是本项目最忌讳的那类"不报错的错"。

rev 是怎么变的
==============
rev = 内容指纹 + 发布时间戳。改了源脚本 → 指纹变；在界面点「更新全能扩展脚本」→
时间戳变（内容没变也能强制扩展重拉一次）。扩展只比字符串，不等就重拉。
"""
import hashlib
import os
import re
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRIDGE_DIR = os.path.join(ROOT, "bridge")
SRC_MAIN = os.path.join(BRIDGE_DIR, "sgs_bridge.user.js")

TM_HEADER_END = "// ==/UserScript=="

# 发布时间戳：初始取源文件 mtime（助手重启不会白重拉一次），
# 点「更新全能扩展脚本」时改成当前时间（内容没变也能强制扩展重拉）。
_published_ts = None


def strip_userscript_header(text):
    """去掉 Tampermonkey 元数据头（含 ==UserScript== 那一行本身）。

    返回 (正文, 头部里的 @version)。没有头就原样返回，version 取 ''。
    纯函数：同样的输入必得同样的输出 —— 生成和校验都走这一个实现。
    """
    lines = text.split("\n")
    end = None
    ver = ""
    for i, ln in enumerate(lines):
        m = re.match(r"^//\s*@version\s+(\S+)", ln.strip("\r"))
        if m and not ver:
            ver = m.group(1)
        if ln.strip("\r").strip() == TM_HEADER_END:
            end = i
            break
    if end is None:
        return text, ver
    body = "\n".join(lines[end + 1:])
    return body.lstrip("\n"), ver


def body_script_ver(body):
    """从正文里取 const SCRIPT_VER = 'x.y.z'; —— 与头部的 @version 互相印证。"""
    m = re.search(r"const\s+SCRIPT_VER\s*=\s*'([^']+)'", body)
    return m.group(1) if m else ""


def render_bridge_main(src_text):
    """sgs_bridge.user.js 的文本 → 给全能扩展用的正文（纯函数）。

    和 Tampermonkey 以 @grant none 注入【同一件事】：都在页面主世界、document-start
    跑同一份代码。区别只有两点：去掉 ==UserScript== 头（浏览器不读它，留着反而让人
    以为还要装 Tampermonkey），以及加上扩展管理页要用的存活标记。
    """
    body, ver = strip_userscript_header(src_text)
    sver = body_script_ver(body)
    note = """// ============================================================================
// 【自动生成，不要手改】全能扩展用的主脚本正文
//   来源：bridge/sgs_bridge.user.js（油猴脚本本体，@version %s）
//   下发：助手 http://127.0.0.1:<端口>/ext/bridge.main.js （改了源脚本在助手里点
//         「资源替换 → 更新全能扩展脚本」，扩展 ≤20 秒自动取走新的一版）
//
// 为什么不再内置进扩展：扩展里放一份复制品，改完源脚本那份【还是旧的且不报错】。
//   现在唯一来源就是源脚本 —— 扩展起的时候从助手拉，离线用上次拉到的那份。
//   和 Tampermonkey 的 @grant none 是同一件事（页面主世界 + document-start），
//   所以装了这个扩展就不用再装 Tampermonkey。
//   ⚠ 换肤部分（installResSwap）会自动让位：本扩展在网络层重定向更彻底，
//     页面层那套 hook 见到 <html data-sgs-res-ext> 标记就停用（见源脚本 refreshResExt）。
// ============================================================================
try {
    // 页面主世界里的存活标记 —— 管理页「检查游戏页」靠它确认脚本真的注入进去了。
    //   刻意不在这里写版本号（版本从 window.SCRIPT_VER 读），免得和源脚本对不上。
    window.__sgsExtPage = { ext: 'sgs_ext', at: Date.now(), ok: true };
} catch (e) { }

""" % (ver or sver or "?")
    if sver and ver and sver != ver:
        raise ValueError(
            "sgs_bridge.user.js 头部 @version(%s) 与正文 SCRIPT_VER(%s) 不一致 —— 先把版本改齐再下发" % (ver, sver))
    return note + body.rstrip("\n") + "\n"


def read_source():
    """读源脚本（UTF-8）。返回 (文本, mtime秒)。读不到抛异常 —— 让调用方如实报错。"""
    with open(SRC_MAIN, "r", encoding="utf-8") as f:
        text = f.read()
    try:
        mt = int(os.path.getmtime(SRC_MAIN))
    except Exception:
        mt = 0
    return text, mt


def _rev_of(code, ts):
    return "%s-%s" % (hashlib.sha1(code.encode("utf-8")).hexdigest()[:12], int(ts or 0))


def payload():
    """当前这一版的完整载荷 {ok, code, ver, rev, size, mtime, publishedAt}。"""
    try:
        src, mt = read_source()
    except Exception as e:
        return {"ok": False, "error": "读不到主脚本(%s): %s" % (SRC_MAIN, e),
                "ver": "", "rev": "", "size": 0}
    code = render_bridge_main(src)
    if _published_ts is None:
        ts = mt
    else:
        ts = _published_ts
    return {
        "ok": True,
        "code": code,
        "ver": body_script_ver(code) or "",
        "rev": _rev_of(code, ts),
        "size": len(code.encode("utf-8")),
        "mtime": mt,
        "publishedAt": int(ts or 0),
        "source": os.path.basename(SRC_MAIN),
    }


def meta():
    """给扩展比对用的元信息（不含代码 —— 没变就不用拉那 1MB）。"""
    p = payload()
    p.pop("code", None)
    return p


def publish():
    """在界面点了「更新全能扩展脚本」：把发布时间戳推到当前 —— rev 变 → 扩展重拉。

    即使脚本内容一个字没改也照样生效（用户要的就是"我点了，你去更新"）；
    返回的 meta 里带 publishedAt，界面上直接显示这次发布的时间。
    """
    global _published_ts
    _published_ts = int(time.time())
    return meta()
