# -*- coding: utf-8 -*-
"""P3 性能热点 profile —— 离线可测部分量化 + 静态结论。

不依赖 pytest / 不连游戏。用法(在 e:/三国杀 下):  python 自走棋助手/tests/profile_p3.py

测什么(离线可测):
  ① CSV 加载缓存命中: load_from_csv(force_reload) vs 缓存第二次  —— csv_loader 有缓存?
  ② map 重建成本: chess_skill_map() / chess_names_all() 每次重跑 3718 行(这两个函数【没缓存】)
  ③ 赛季总表: season_config() 读 4MB JSON —— 有 mtime 缓存?
  ④ OCR 单次成本: pytesseract 每次 spawn tesseract.exe 子进程(头号延迟嫌疑), 实测单次毫秒数
静态结论(需连游戏, 这里给事实+建议):
  · auto_recruit._loop 每 0.3s 调一次 rescan_areas()(eval 扫页面 DOM) —— 每轮都扫, 大概率过度
  · OCR 只在"协议/颜色/模板都拿不到数字"时才兜底, 但若被放进热轮询就是 ~50-200ms/次 的子进程开销
"""
import os
import sys
import time
import traceback

ROOT = r"e:/三国杀/自走棋助手"
sys.path.insert(0, ROOT)
os.chdir(ROOT)


def _fmt(ms):
    return "%.2f ms" % ms if ms < 1000 else "%.3f s" % (ms / 1000.0)


def profile():
    rows = []  # (name, ms_or_None, note)

    from core import csv_loader as CL

    # ---------- ① CSV 加载缓存命中 ----------
    t0 = time.perf_counter()
    d1 = CL.load_from_csv(force_reload=True)
    t1 = time.perf_counter()
    d2 = CL.load_from_csv()  # 命中缓存
    t2 = time.perf_counter()
    rows.append(("CSV 加载(uncached, %d 行)" % len(d1), (t1 - t0) * 1000.0, "force_reload 全量解析"))
    rows.append(("CSV 加载(cached 第2次)", (t2 - t1) * 1000.0,
                 "缓存命中 → 应接近 0(若不是说明没缓存)"))

    # ---------- ② map 重建成本 + 缓存命中(P3-B) ----------
    # 首次调用: 构建并写入缓存(未缓存基准)
    t0 = time.perf_counter()
    sm = CL.chess_skill_map()
    t1 = time.perf_counter()
    # 第二次(同一底层数据): 应命中 P3-B 缓存
    sm2 = CL.chess_skill_map()
    t1b = time.perf_counter()
    t2b = time.perf_counter()
    na = CL.chess_names_all()
    t3 = time.perf_counter()
    na2 = CL.chess_names_all()
    t3b = time.perf_counter()
    rows.append(("chess_skill_map() 首次构建(未缓存)", (t1 - t0) * 1000.0,
                 "ids=%d names=%d" % (len(sm.get("ids", {})), len(na))))
    rows.append(("chess_skill_map() 命中缓存(P3-B)", (t1b - t1) * 1000.0,
                 "应≈0" if sm2 == sm else "⚠ 缓存与重算不一致!"))
    rows.append(("chess_names_all() 首次构建(未缓存)", (t3 - t2b) * 1000.0,
                 "n=%d" % len(na)))
    rows.append(("chess_names_all() 命中缓存(P3-B)", (t3b - t3) * 1000.0,
                 "应≈0" if na2 == na else "⚠ 缓存与重算不一致!"))

    # ---------- ③ 赛季总表(4MB JSON) mtime 缓存 ----------
    try:
        t0 = time.perf_counter()
        cfg1 = CL.season_config()
        t1 = time.perf_counter()
        cfg2 = CL.season_config()  # 命中缓存
        t2 = time.perf_counter()
        rows.append(("season_config() 读 4MB 总表(uncached)", (t1 - t0) * 1000.0,
                     "ChessConf 条数≈%d" % len(cfg1.get("ChessConf") or [])))
        rows.append(("season_config() 读 总表(cached)", (t2 - t1) * 1000.0, "mtime 缓存命中 → 应≈0"))
    except Exception as e:
        rows.append(("season_config()", None, "异常: %r" % e))

    # ---------- ④ OCR 单次成本 ----------
    try:
        import pytesseract
        from PIL import Image, ImageDraw, ImageFont
        # 造一张含数字的小图(模拟金币/价格)
        img = Image.new("RGB", (120, 40), (255, 255, 255))
        d = ImageDraw.Draw(img)
        try:
            fnt = ImageFont.load_default()
        except Exception:
            fnt = None
        d.text((10, 10), "12345", fill=(0, 0, 0), font=fnt)
        # 暖机(首调含子进程冷启动/模型装载)
        try:
            pytesseract.image_to_string(img, config="--psm 7 -c tessedit_char_whitelist=0123456789")
        except Exception:
            pass
        N = 5
        t0 = time.perf_counter()
        for _ in range(N):
            pytesseract.image_to_string(img, config="--psm 7 -c tessedit_char_whitelist=0123456789")
        t1 = time.perf_counter()
        rows.append(("OCR 单次(pytesseract, 平均 %d 次)" % N, (t1 - t0) * 1000.0 / N,
                     "每次 spawn tesseract.exe 子进程 —— 若在热轮询里调用就是 ~此值/次 的硬开销"))
    except Exception as e:
        rows.append(("OCR 单次", None,
                     "无法测量(缺 tesseract/pytesseract/PIL 或引擎报错: %r) —— 架构上每次 OCR 都 spawn 子进程, 是头号延迟嫌疑" % e))

    return rows


def main():
    print("=== P3 性能 profile(离线可测部分) ===")
    try:
        rows = profile()
    except Exception as e:
        print("profile 异常: %r" % e)
        traceback.print_exc()
        os._exit(1)

    print("")
    print("  %-42s %12s  说明" % ("项目", "耗时"))
    print("  " + "-" * 90)
    for name, ms, note in rows:
        ms_s = _fmt(ms) if ms is not None else "N/A"
        print("  %-42s %12s  %s" % (name, ms_s, note))

    print("")
    print("=== 静态结论(需连游戏, 这里给事实+建议) ===")
    print("  · auto_recruit._loop: 每 sleep(0.3) 一轮, 且【每轮】调 rescan_areas()(eval 让页面 scan_areas")
    print("    扫 DOM 逻辑地址并写回 LIVE)。DOM 地址对局内基本不变 → 0.3s 扫一次是过度开销。")
    print("    建议: 改为每 N 秒(如 5~10s)或仅在检测到地址失效时重扫, 省掉 ~3 次/秒 的 eval 往返。")
    print("  · OCR(ocr_number) 用 pytesseract, 每次 spawn tesseract.exe 子进程; 只在协议/颜色/模板")
    print("    都拿不到数字时才兜底 —— 若它被放进热轮询, 单次成本即硬性阻塞。建议: 调用点加缓存/降频。")
    print("  · 数据加载侧: load_from_csv + season_config 均已缓存, 二次命中≈0ms; chess_skill_map/")
    print("    chess_names_all 本身无缓存但只在连接/切换时调用, 非热路径 → 暂无需动作。")

    os._exit(0)


if __name__ == "__main__":
    main()
