# -*- coding: utf-8 -*-
"""组装 Chrome 扩展目录(extension/dist) —— 单一来源, 不手抄脚本。

为什么要有这一步
================
扩展要跑的是 bridge/sgs_bridge.user.js 那份代码, 但扩展里不能直接放 .user.js
(带油猴头也能跑, 但容易误会)。所以构建时把它原样复制成 modules/00-bridge.js:

  bridge/sgs_bridge.user.js  ──复制──▶  extension/dist/modules/00-bridge.js
  extension/src/loader.js    ──复制──▶  extension/dist/src/loader.js
  extension/modules/*.js     ──复制──▶  extension/dist/modules/*.js
  extension/manifest.json    ──复制──▶  extension/dist/manifest.json

不改一个字 —— 00-bridge 就是现在油猴里跑的那份, 行为完全一致, 只是被装进了
【模块容器】里: 它崩了有 try/catch 兜着, 别的模块不受影响, 角标会标红。

用法:
  python tools/build_extension.py            # 构建
  python tools/build_extension.py --open     # 构建完打开目录(方便拖进 chrome://extensions)
"""

import os
import sys
import shutil
import argparse

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(BASE, "extension")
DIST = os.path.join(SRC, "dist")
BRIDGE = os.path.join(BASE, "bridge", "sgs_bridge.user.js")


def _copy(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    return os.path.getsize(dst)


def build():
    if not os.path.isfile(BRIDGE):
        print("找不到主脚本:", BRIDGE)
        return False

    # dist 整个重建, 免得留着上次的旧模块
    if os.path.isdir(DIST):
        shutil.rmtree(DIST, ignore_errors=True)
    os.makedirs(DIST, exist_ok=True)

    total = 0
    # ① 主脚本 → 00-bridge.js
    n = _copy(BRIDGE, os.path.join(DIST, "modules", "00-bridge.js"))
    total += n
    print("  modules/00-bridge.js          %8.0f KB  (来自 bridge/sgs_bridge.user.js)" % (n / 1024.0))

    # ② 加载器
    n = _copy(os.path.join(SRC, "src", "loader.js"), os.path.join(DIST, "src", "loader.js"))
    total += n
    print("  src/loader.js                 %8.0f KB" % (n / 1024.0))

    # ③ 其它模块(按文件名排序, 90-health 之类)
    #    ⚠ P4-阶段1: 跳过 00-bridge.js —— 它已由步骤①从权威源 bridge/sgs_bridge.user.js
    #    生成; 若此处再拷 extension/modules/00-bridge.js(可能过期) 会覆盖步骤①的成果,
    #    导致"改了 user.js 不生效"(K3 根因)。故 00-bridge 永远只来自权威源。
    mods = os.path.join(SRC, "modules")
    if os.path.isdir(mods):
        for f in sorted(os.listdir(mods)):
            if not f.endswith(".js"):
                continue
            if f == "00-bridge.js":
                continue  # P4-阶段1: 防 stale 副本覆盖权威源(见上方注释)
            n = _copy(os.path.join(mods, f), os.path.join(DIST, "modules", f))
            total += n
            print("  modules/%-20s %8.0f KB" % (f, n / 1024.0))

    # ④ manifest
    n = _copy(os.path.join(SRC, "manifest.json"), os.path.join(DIST, "manifest.json"))
    total += n
    print("  manifest.json                 %8.0f KB" % (n / 1024.0))

    print("")
    print("构建完成: %s (共 %.0f KB)" % (DIST, total / 1024.0))
    print("加载方式: chrome://extensions → 打开『开发者模式』→『加载已解压的扩展程序』→ 选这个 dist 目录")
    return True


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--open", action="store_true", help="构建完打开目录")
    a = ap.parse_args()
    ok = build()
    if ok and a.open:
        try:
            os.startfile(DIST)
        except Exception as e:
            print("打开目录失败:", e)
