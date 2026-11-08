# -*- coding: utf-8 -*-
"""P2 细验 K3: 改 bridge/sgs_bridge.user.js 后, 主脚本是否真生效(无 stale 副本覆盖)。

不依赖 pytest。用法(在 e:/三国杀 下):  python 自走棋助手/tests/smoke_k3_sync.py

K3 背景(来自 feedback_bridge_sync): 只改 sgs_bridge.user.js 不生效, 构建会被 modules 旧副本
覆盖。调研后发现架构已分叉成两条路, 本测试针对两条路分别核验"源改了, 线上/内置副本跟没跟上":

  路A 全能扩展(bridge/sgs_ext, MV3): 主脚本【运行时】从助手 /ext/bridge.main.js 拉,
    由 core/ext_script.render_bridge_main(user.js) 渲染 —— 本就【没有】静态内置副本, 故线上
    路径天然无 stale。唯一的静态副本是离线兜底 bridge/sgs_ext/lib/bridge.bundle.js, 它必须
    由同一份 render 生成, 否则【离线模式】会喂旧代码(这是 v4.81 之后 K3 的真正残留点)。

  路B 模块容器扩展(extension/): tools/build_extension.py 步骤①把 user.js 原样复制成
    extension/dist/modules/00-bridge.js; 步骤③【跳过】00-bridge(不再被 extension/modules/ 那份
    易过期的并行副本覆盖), 且 extension/modules/00-bridge.js 这个冗余源副本已删除(P4-阶段1)。
    所以 dist/modules/00-bridge.js 永远 == user.js, K3 根因(改了 user.js 被旧副本覆盖)已堵死。
    本测试现核验"构建产物 dist/00-bridge == 原始 user.js" + "冗余源副本已不再存在"。

核验手段: 用"新鲜渲染的 user.js" / "原始 user.js 字节" 对各静态副本做一致性比对。
"""
import os
import sys
import traceback

ROOT = r"e:/三国杀/自走棋助手"
sys.path.insert(0, ROOT)
os.chdir(ROOT)

USERJS = os.path.join(ROOT, "bridge", "sgs_bridge.user.js")
BUNDLE = os.path.join(ROOT, "bridge", "sgs_ext", "lib", "bridge.bundle.js")
MOD_SRC = os.path.join(ROOT, "extension", "modules", "00-bridge.js")  # 已删除(冗余源副本, P4-阶段1)
DIST00 = os.path.join(ROOT, "extension", "dist", "modules", "00-bridge.js")
SGEXT_MANIFEST = os.path.join(ROOT, "bridge", "sgs_ext", "manifest.json")


def _case(cases, name, ok, detail=""):
    cases.append((name, "PASS" if ok else "FAIL", detail))


def _ver_of(text):
    import re
    m = re.search(r"const\s+SCRIPT_VER\s*=\s*'([^']+)'", text or "")
    return m.group(1) if m else "(无)"


def k3_smoke(cases):
    # ---------- 源脚本存在 + 能渲染 ----------
    try:
        from core import ext_script as ES
    except Exception as e:
        _case(cases, "import ext_script", False, repr(e))
        return
    try:
        with open(USERJS, "r", encoding="utf-8") as f:
            src = f.read()
        _case(cases, "源脚本 sgs_bridge.user.js 可读", bool(src), "%d 字节" % len(src))
        rendered = ES.render_bridge_main(src)
        _case(cases, "ext_script.render_bridge_main 可渲染", bool(rendered), "%d 字节" % len(rendered))
        _case(cases, "渲染结果已剥油猴头(无 ==/UserScript==)",
              "// ==/UserScript==" not in rendered, "")
        _case(cases, "渲染结果带页面存活标记(__sgsExtPage)",
              "window.__sgsExtPage" in rendered, "")
        # 纯函数性: 源动 → 渲染动(证明线上路径没有"写死的旧副本"参与)
        rendered2 = ES.render_bridge_main(src + "\n/* smoke-probe */\n")
        _case(cases, "render 是纯函数(源变→渲染变, 无 stale 参与)",
              rendered2 != rendered, "")
    except Exception as e:
        _case(cases, "ext_script 渲染源脚本", False, repr(e))
        return

    # ---------- 路A: 离线兜底 bridge.bundle.js 是否跟源同步 ----------
    try:
        if not os.path.isfile(BUNDLE):
            _case(cases, "路A 离线兜底 bundle 存在", False, "找不到 %s" % BUNDLE)
        else:
            with open(BUNDLE, "r", encoding="utf-8", errors="replace") as f:
                bundle = f.read()
            # README 合同: bundle 应 == render_bridge_main(user.js)。
            # _rebuild_ext_bundle.py 会在末尾追加一段"构建标记"诊断注释(console.log, 离线时用来确认
            # 页面加载的是不是这份), 这段不算"内容差异"。剥掉 build-stamp 后缀 + 两端空白再比, 只比对
            # 【真正的脚本内容】是否跟源同步 —— 这才是 K3 要抓的 stale(内容变了但 bundle 没跟上)。
            bundle_core = bundle.split("/* ---- sgs build stamp ---- */")[0].rstrip("\r\n")
            rendered_core = rendered.rstrip("\r\n")
            _case(cases, "路A 离线兜底 bundle 内容 == 新鲜渲染(user.js, 忽略构建标记后缀)",
                  bundle_core == rendered_core,
                  ("不等(忽略构建标记后仍不一致): bundle_core=%d字符/SCRIPT_VER%s, 渲染=%d字符/SCRIPT_VER%s"
                   % (len(bundle_core), _ver_of(bundle_core), len(rendered_core), _ver_of(rendered_core)))
                  if bundle_core != rendered_core else "")
    except Exception as e:
        _case(cases, "路A bundle 比对", False, repr(e))

    # ---------- 路B: 模块容器 - 冗余源副本已删 + 构建产物 == user.js ----------
    #   P4-阶段1: 构建步骤③已跳过 00-bridge(不再被易过期副本覆盖), 源副本已删除 → 堵死 K3。
    try:
        _case(cases, "路B 冗余源副本 modules/00-bridge.js 已删除(防 stale 复活)",
              not os.path.isfile(MOD_SRC),
              ("仍存在: %s (应删除以源头消除 stale)" % MOD_SRC) if os.path.isfile(MOD_SRC) else "已删除")
        if not os.path.isfile(DIST00):
            _case(cases, "路B 构建产物 dist/modules/00-bridge.js 存在",
                  False, "找不到 %s(先跑 build_extension.py)" % DIST00)
        else:
            with open(DIST00, "r", encoding="utf-8", errors="replace") as f:
                dist = f.read()
            # build_extension.py 步骤①: 00-bridge 是 user.js 的原样副本(含油猴头)
            _case(cases, "路B 构建产物 dist/00-bridge.js == 原始 user.js(字节一致)",
                  dist == src,
                  "不等: dist=%d字节/SCRIPT_VER%s, user.js=%d字节/SCRIPT_VER%s"
                  % (len(dist), _ver_of(dist), len(src), _ver_of(src)))
    except Exception as e:
        _case(cases, "路B dist/00-bridge 比对", False, repr(e))

    # ---------- 路A manifest: 主脚本走运行时下发, 不内置静态 main ----------
    try:
        import json
        with open(SGEXT_MANIFEST, "r", encoding="utf-8") as f:
            mf = json.load(f)
        cjs = [j for c in (mf.get("content_scripts") or []) for j in (c.get("js") or [])]
        bad = [j for j in cjs if "bridge.main.js" in j or "00-bridge" in j]
        _case(cases, "路A manifest 不把主脚本当静态内置 content_script",
              not bad, "内置了: %s" % bad if bad else "content_scripts=%s" % cjs)
        _case(cases, "路A manifest 含 userScripts 权限(运行时下发前提)",
              "userScripts" in (mf.get("permissions") or []), "")
    except Exception as e:
        _case(cases, "路A manifest 检查", False, repr(e))


def main():
    cases = []
    print("=== P2 细验 K3: 桥接脚本改了是否真生效(无 stale 副本) ===")
    k3_smoke(cases)
    npass = nfail = 0
    for name, st, det in cases:
        print("  [%s] %s  %s" % ("PASS" if st == "PASS" else "FAIL", name,
                                 ("-> " + str(det)) if det else ""))
        if st == "PASS":
            npass += 1
        else:
            nfail += 1
    print("\n  K3: %d 通过 / %d 失败" % (npass, nfail))
    os._exit(0)


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
