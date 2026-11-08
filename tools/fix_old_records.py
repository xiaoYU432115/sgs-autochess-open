# -*- coding: utf-8 -*-
"""一次性清理: 把老战绩里"被误当名次"的 emRus 值摘出来(不让界面再拿它算假统计)。

背景(实锤, 见 core/protocol.py 里 _track_eliminations / _guess_rank 的注释):
  老版助手写战绩时 `placement = int(mine.get("emRus"))`, 而反编译源码把这条包用得很明确:
      battleEndProtoData = { battleResult: e.emRus + 1, lostHp: 0, rank: 1, gameOver: !1 }
      2 == e.emRus && (i = e.iLostHp)      // 掉的血写进 lostHp
      3 == e.emRus && (i = 0)
  → emRus 是【每场战斗的结果码】(胜/平/负, +1 映射 TavernChessBattleGameResultType),
    不是名次; 而且这条包【每场战斗都发】。
  证据: 209 条里只有 1/2/3 三档(100/97/12) —— 正好是胜/平/负, 不可能是名次分布。
  后果: 复盘页按它算, 于是报出"吃鸡率 47%、前四率 100%"这种假数
        (用户原话: "对局复盘 tab 看不到对局实际情况, 统计啥的也不对")。

这个脚本做什么(只对【没有 place_src 标记】的老记录):
  · 把 placement 原值搬到 battle_code(留档, 信息不丢);
  · placement 改成 0 = 名次未知 → 界面从此不再拿它算前四率/平均名次;
  · result 里的"胜利/前四/失败"(那是由脏值推出来的) 改成"未知", 原值写进备注;
  · 备注追加一句清理说明, 以后复核能对得上。
幂等: 带 place_src 或 placement 已是 0 的记录不动, 重复跑不会重复改。

用法:
  python tools/fix_old_records.py            # 预览(默认, 不写文件)
  python tools/fix_old_records.py --apply    # 真改(先备份到 data/_backup/)
"""
import io
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import records as REC      # noqa: E402

DIRTY = ("胜利", "前四", "失败")      # 老代码由脏 placement 推出来的三种结果


def plan_one(r):
    """返回 (新记录, 改动说明) 或 (None, "") 表示不需要改。"""
    if not isinstance(r, dict):
        return None, ""
    if str(r.get("place_src") or "").strip():
        return None, ""                                  # 新记录(有名次来源)一律不动
    p = int(r.get("placement") or 0)
    if p <= 0:
        return None, ""                                  # 本来就是"未知", 不动
    n = dict(r)
    n["battle_code"] = p                                 # 原值留档: 其实是末场战斗结果码
    n["placement"] = 0
    old_res = str(n.get("result") or "").strip()
    if old_res in DIRTY:
        n["result"] = "未知"
    note = str(n.get("note") or "")
    add = ("【v4.68清理】archive: 原档位=%d(实为末场战斗结果码 emRus, 非名次); 原结果=%s"
           % (p, old_res or "-"))
    n["note"] = (note + " " + add).strip() if add not in note else note
    return n, "placement %d→0 (%s), result %s→%s" % (p, "emRus", old_res or "-", n.get("result"))


def main():
    apply = "--apply" in sys.argv
    path = REC.RECORDS_PATH
    if not os.path.exists(path):
        print("没有战绩文件: " + path)
        return 1
    rs = REC.load_records()
    out, changed = [], 0
    for r in rs:
        n, why = plan_one(r)
        if n is None:
            out.append(r)
        else:
            changed += 1
            out.append(n)
            if changed <= 8:
                print("  · 第 %d 条: %s" % (len(out), why))
    print("-" * 60)
    print("战绩共 %d 条, 需要清理 %d 条" % (len(rs), changed))
    if not changed:
        print("没什么要改的(可能已经清理过)。")
        return 0
    if not apply:
        print("这是【预览】, 没有改文件。要真改请加 --apply")
        return 0
    bak_dir = os.path.join(os.path.dirname(path), "_backup")
    os.makedirs(bak_dir, exist_ok=True)
    bak = os.path.join(bak_dir, "records.json.bak-" + time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(path, bak)
    REC.save_records(out)
    print("已改并写好: " + path)
    print("备份: " + bak)
    print("提示: 老记录的『名次』一律变成未知(它们本来就不是名次); "
          "以后的新记录会用【淘汰顺序】推真实名次。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
