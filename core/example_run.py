# -*- coding: utf-8 -*-
# 演示: 偷梁换柱式克隆模块「接进去」后实际长什么样
# 在项目根目录运行:  python core/example_run.py
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.scenario_fns import build_default_registry

reg = build_default_registry()
reg.register_scenario_dir(os.path.join(ROOT, "剧本库"))   # 把 19 个阵容剧本全部接入

# 一个「调用方」剧本: 它只写名字, 完全不知道后面是哪个实现
CALLER = [
    {"op": "log", "text": "—— 调用方开始(只写 target=偷梁换柱) ——"},
    {"op": "callScenario", "target": "偷梁换柱"},
    {"op": "log", "text": "—— 调用方结束 ——"},
]


def show(label, bindings):
    print("\n========== %s ==========" % label)
    print("调用方原文 callScenario.target =", CALLER[1]["target"], "  (一字未改)")
    # 经 getter 实际解析到哪个函数体
    body = reg.make_getter(bindings)(CALLER[1]["target"])
    print("经 getter 实际解析到 %d 步:" % len(body))
    for s in body:
        print("   ", {k: s[k] for k in s if k != "note"})
    # 真正跑一遍(接进去)
    res = reg.run(CALLER, bindings=bindings)
    print("沙盒执行摘要:", res["summary"])


# 情况A: 调用方无感知地用了「基础 偷梁换柱」(复制≤2级, 无 rankCap 字段)
show("情况A: 绑定到 偷梁换柱(基础)", {"偷梁换柱": "偷梁换柱"})

# 情况B: 调用方文本一字未改, 但背后悄悄换成了「六本 变体(rankCap=4)」
show("情况B: 绑定到 偷梁换柱__六本(rankCap=4)", {"偷梁换柱": "偷梁换柱__六本"})

print("\n>>> 关键: 情况A/B 的调用方剧本完全相同, 只改了 bindings 这一行映射,")
print(">>> 就实现了「换实现、调用方无感知」—— 这就是偷梁换柱式替换的意义。")
