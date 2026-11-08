# -*- coding: utf-8 -*-
"""页面错误(window.__sgsTopErrs)的聚合与显示 —— 只写一份, 命令行和界面共用。

为什么要有这个模块
==================
"把数组读回来 + 按同一条错误聚合计数"这套逻辑, 原本抄了两份:

  · dev/_page_errors_once.py  (命令行一次性查看)
  · ui/app.py 的 a_page_errors (界面【页面错误】按钮)

抄两份必然漂移 —— 2026-10-08 就撞上了: 收集端(bridge/sgs_bridge.user.js 的 error
监听)新补了 tgt 字段, 于是两边各自的显示都得跟着改一遍; 改漏的那边就表现为
"命令行看得出是哪个资源炸了, 按钮里仍然是一条空白错误"。这类【不报错的错】正是
本项目最忌讳的(同样的教训见 core/ext_script.py:17-18: "两边各写一份 = 迟早漂移")。

故收成一份。以后再给错误加字段(比如堆栈), 只改这里, 两边同时生效。

tgt 是什么
==========
资源(img/script/link)加载失败也会触发 window 的 error 事件(靠 capture=true 才收得到),
而这类事件 message / filename / lineno 【全是空】—— 只按 msg+file 显示就是一条啥都没有
的"空白错误"。唯一能指认凶手的是 e.target.src, 记在 tgt 里。所以 where_of() 在
file:ln 都拿不到时, 回退到 tgt。
"""


def group_errors(arr):
    """__sgsTopErrs 数组 → [(msg, g), ...], 出现次数多的排最上面(次数相同按最近时间)。

    g 的字段: n 次数 / last 最近时间戳 / file / ln / kind / tgt。
    """
    groups = {}
    for it in arr or []:
        if not isinstance(it, dict):
            continue
        key = str(it.get("msg") or "")[:300]
        g = groups.setdefault(key, {"n": 0, "last": 0, "file": "",
                                    "ln": 0, "kind": "", "tgt": ""})
        g["n"] += 1
        g["last"] = max(g["last"], int(it.get("ts") or 0))
        g["file"] = str(it.get("file") or "")
        g["ln"] = int(it.get("ln") or 0)
        g["kind"] = str(it.get("kind") or "")
        # 同一条错误可能被收了很多次, 只有某几次带 tgt —— 取到过就一直留着
        g["tgt"] = g["tgt"] or str(it.get("tgt") or "")
    return sorted(groups.items(), key=lambda kv: (-kv[1]["n"], -kv[1]["last"]))


def where_of(g):
    """这条错误的"在哪": 优先 file:ln, 都没有就用 tgt(资源加载失败时的唯一线索)。"""
    src = g["file"].split("/")[-1] if g["file"] else ""
    where = "%s:%d" % (src, g["ln"]) if src and g["ln"] else (src or "")
    if not where and g["tgt"]:
        where = g["tgt"] if len(g["tgt"]) <= 96 else g["tgt"][:93] + "..."
    return where


def format_errors(arr):
    """整段文本 —— 命令行直接 print, 界面直接 insert。arr 为 None 表示没读到。"""
    if arr is None:
        return ("没读到 —— 要么页面没连上, 要么脚本太旧还没有 __sgsTopErrs。\n"
                "(重新注入一次桥接再点这个按钮)")
    if not arr:
        return "一条都没收到 —— 页面端目前没有捕获到错误。\n"
    ordered = group_errors(arr)
    out = ["共 %d 条 / 去重后 %d 类 —— 按出现次数降序, 排最上面的就是一直在加的那个\n\n"
           % (len(arr), len(ordered))]
    for key, g in ordered:
        out.append("×%d  [%s] %s\n    %s\n\n"
                   % (g["n"], g["kind"], where_of(g), key))
    return "".join(out)
